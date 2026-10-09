"""The network between a live session's games and its companions, made worse on purpose: gdh live start --net NAME.

gdh puts a proxy of its own between each game instance and companion NAME (a server). The companion keeps its port;
each instance gets a port of its own on the proxy, which `{NAME.port}` in its arguments names, and the proxy passes
what goes through it on, TCP and UDP alike, after a delay (latency, plus up to jitter more), dropping a share of
UDP datagrams (loss), or nothing at all while the link is cut. `gdh live net` changes all of it while the session
runs, for every instance or one, and shows what went through.

    TCP: each chunk read is written on after its delay, in order. Loss doesn't apply (TCP resends what's lost: the
         game would see delay, which latency gives). Cut holds everything, as a network that's gone does, and passes
         it on when healed, unless the game gave up first. Reset closes the instance's connections at once.
    UDP: each datagram goes on after its own delay, so jitter can reorder them, or is dropped (loss, cut).

The delays are in real time, as a network's are. A held game doesn't read its socket, and `step` runs frames faster
than real time, so a test of latency runs the game in real time (`gdh live run`) or waits on what the game measured
(`step --until`). The proxy's random draws (jitter, loss) repeat with the session's seed.

The proxy is a process of its own (python -m gdh.netem CONFIG.json), in the session's process groups, so it stops
with the session. It answers requests on a Unix socket only this user can reach: a JSON object a line,
{"cmd": "state"}, {"cmd": "set", "routes": {"name"?, "instance"?}, "set": {"latency", "jitter", "loss", "cut"}} or
{"cmd": "reset", "routes": {...}}, each answered with every route's state.
"""
import asyncio
import json
import os
import random
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

from gdh.godot import GdhError

SETTINGS = ("latency", "jitter", "loss", "cut")


class NetError(GdhError):
    pass


# --- The proxy --------------------------------------------------------------------------------------------------------

class Route:
    """One instance's way to one companion: a TCP and a UDP port on 127.0.0.1, passing on to the companion's port."""

    def __init__(self, spec, rng):
        self.name, self.instance = spec["name"], spec["instance"]
        self.listen, self.target = spec["listen"], spec["target"]
        self.latency, self.jitter = float(spec.get("latency", 0)), float(spec.get("jitter", 0))
        self.loss, self.cut = float(spec.get("loss", 0)), bool(spec.get("cut", False))
        self.rng = rng
        self.stats = {"tcp_connections": 0, "tcp_bytes_up": 0, "tcp_bytes_down": 0, "udp_up": 0, "udp_down": 0,
                      "udp_dropped": 0}
        self.connections = set()  # (reader task pair, writers) of each open TCP connection
        self.healed = asyncio.Event()
        self.healed.set()
        self.udp_out = None  # the transport answering the game
        self.udp_peers = {}  # game address -> transport to the companion

    def state(self):
        return {"name": self.name, "instance": self.instance, "port": self.listen, "target": self.target,
                "latency": self.latency, "jitter": self.jitter, "loss": self.loss, "cut": self.cut,
                "tcp_open": len(self.connections), **self.stats}

    def delay(self):
        return (self.latency + (self.rng.uniform(0, self.jitter) if self.jitter else 0)) / 1000

    def set(self, values):
        for key, value in values.items():
            setattr(self, key, bool(value) if key == "cut" else float(value))
        if self.cut:
            self.healed.clear()
        else:
            self.healed.set()

    def reset(self):
        for writers in list(self.connections):
            for w in writers:
                transport = w.transport
                sock = transport.get_extra_info("socket")
                if sock is not None:  # a reset, not a goodbye: SO_LINGER 0
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                transport.abort()
        self.connections.clear()

    # TCP

    async def serve_tcp(self):
        return await asyncio.start_server(self.accept, "127.0.0.1", self.listen)

    async def accept(self, game_reader, game_writer):
        try:
            up_reader, up_writer = await asyncio.open_connection("127.0.0.1", self.target)
        except OSError:
            game_writer.close()
            return
        self.stats["tcp_connections"] += 1
        writers = (game_writer, up_writer)
        self.connections.add(writers)
        try:
            await asyncio.gather(self.pump(game_reader, up_writer, "tcp_bytes_up"),
                                 self.pump(up_reader, game_writer, "tcp_bytes_down"))
        finally:
            self.connections.discard(writers)
            for w in writers:
                w.close()

    async def pump(self, reader, writer, counter):
        """Read chunks and write each on after its delay, in order: a queue and a writer that waits for each."""
        queue = asyncio.Queue()
        last = [0.0]

        async def deliver():
            while True:
                due, data = await queue.get()
                if data is None:
                    break
                wait = due - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                await self.healed.wait()
                writer.write(data)
                await writer.drain()
                self.stats[counter] += len(data)
            if writer.can_write_eof():
                writer.write_eof()

        sender = asyncio.ensure_future(deliver())
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                last[0] = max(last[0], time.monotonic() + self.delay())
                queue.put_nowait((last[0], data))
        except (ConnectionError, OSError):
            pass
        queue.put_nowait((0, None))
        try:
            await sender
        except (ConnectionError, OSError):
            pass

    # UDP

    async def serve_udp(self):
        loop = asyncio.get_running_loop()
        route = self

        class FromGame(asyncio.DatagramProtocol):
            def connection_made(self, transport):
                route.udp_out = transport

            def datagram_received(self, data, addr):
                asyncio.ensure_future(route.up(data, addr))

        transport, _ = await loop.create_datagram_endpoint(FromGame, local_addr=("127.0.0.1", self.listen))
        return transport

    def dropped(self):
        if self.cut or (self.loss and self.rng.uniform(0, 100) < self.loss):
            self.stats["udp_dropped"] += 1
            return True
        return False

    async def up(self, data, addr):
        if self.dropped():
            return
        peer = self.udp_peers.get(addr)
        if peer is None:
            loop = asyncio.get_running_loop()
            route = self

            class FromCompanion(asyncio.DatagramProtocol):
                def datagram_received(self, reply, _):
                    if not route.dropped():
                        loop.call_later(route.delay(), route.down, reply, addr)

            peer, _ = await loop.create_datagram_endpoint(FromCompanion, remote_addr=("127.0.0.1", self.target))
            self.udp_peers[addr] = peer
        asyncio.get_running_loop().call_later(self.delay(), self.send_up, peer, data)

    def send_up(self, peer, data):
        if not peer.is_closing():
            peer.sendto(data)
            self.stats["udp_up"] += 1

    def down(self, data, addr):
        if self.udp_out is not None and not self.udp_out.is_closing():
            self.udp_out.sendto(data, addr)
            self.stats["udp_down"] += 1


async def serve(config):
    rng = random.Random(config.get("seed"))
    routes = [Route(spec, random.Random(rng.random())) for spec in config["routes"]]
    for route in routes:
        await route.serve_tcp()
        await route.serve_udp()

    def chosen(spec):
        spec = spec or {}
        return [r for r in routes if ("name" not in spec or r.name == spec["name"])
                and ("instance" not in spec or r.instance == int(spec["instance"]))]

    async def control(reader, writer):
        async for line in reader:
            try:
                request = json.loads(line)
                picked = chosen(request.get("routes"))
                if not picked:
                    raise ValueError("no route matches")
                if request.get("cmd") == "set":
                    for route in picked:
                        route.set({k: v for k, v in request.get("set", {}).items() if k in SETTINGS})
                elif request.get("cmd") == "reset":
                    for route in picked:
                        route.reset()
                elif request.get("cmd") != "state":
                    raise ValueError(f"unknown command {request.get('cmd')!r}")
                reply = {"ok": True, "routes": [r.state() for r in routes]}
            except (ValueError, TypeError, KeyError) as e:
                reply = {"ok": False, "error": str(e)}
            writer.write((json.dumps(reply) + "\n").encode())
            await writer.drain()
        writer.close()

    path = config["socket"]
    if os.path.exists(path):
        os.unlink(path)
    old = os.umask(0o077)
    try:
        server = await asyncio.start_unix_server(control, path)
    finally:
        os.umask(old)
    Path(config["ready"]).write_text("ready")
    async with server:
        await server.serve_forever()


def main():
    config = json.loads(Path(sys.argv[1]).read_text())
    try:
        asyncio.run(serve(config))
    except KeyboardInterrupt:
        pass


# --- From gdh live ----------------------------------------------------------------------------------------------------

def free_port():
    """A port on 127.0.0.1 free for both TCP and UDP now."""
    for _ in range(50):
        with socket.socket() as tcp:
            tcp.bind(("127.0.0.1", 0))
            port = tcp.getsockname()[1]
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                try:
                    udp.bind(("127.0.0.1", port))
                except OSError:
                    continue
                return port
    raise NetError("Can't find a port free for both TCP and UDP for the network proxy.")


def plan(names, ports, count, settings):
    """The routes for --net NAME...: one per companion and instance, each with a port of its own."""
    return [{"name": name, "instance": i, "listen": free_port(), "target": ports[name], **settings}
            for name in names for i in range(count)]


def start(routes, seed, folder, out, timeout=10):
    """Start the proxy for these routes. Returns its record: {pid, socket, routes}."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = folder / "net.json"
    ready = folder / "net-ready"
    ready.unlink(missing_ok=True)
    record = {"socket": str(folder / "net.sock"), "ready": str(ready), "seed": seed, "routes": routes}
    config.write_text(json.dumps(record))
    log = open(Path(out) / "net.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "gdh.netem", str(config)], stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + timeout
    while not ready.exists():
        if proc.poll() is not None or time.monotonic() > deadline:
            proc.kill()
            raise NetError(f"The network proxy didn't start: see {Path(out) / 'net.log'}.")
        time.sleep(0.05)
    return {"pid": proc.pid, "socket": record["socket"], "routes": routes}


def ports_for(record, instance):
    """{NAME: port} for one instance: the proxy's port in place of each companion behind it."""
    if not record:
        return {}
    return {r["name"]: r["listen"] for r in record["routes"] if r["instance"] == instance}


def request(record, cmd, routes=None, values=None):
    """Ask the session's proxy something; its reply's routes."""
    message = {"cmd": cmd, "routes": routes or {}}
    if values:
        message["set"] = values
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(5)
            s.connect(record["socket"])
            s.sendall((json.dumps(message) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
    except OSError as e:
        raise NetError(f"Can't reach the session's network proxy: {e}.") from None
    reply = json.loads(data)
    if not reply.get("ok"):
        raise NetError(f"The network proxy refused it: {reply.get('error')}.")
    return reply["routes"]


def describe_route_start(route):
    worse = [f"{k} {route[k]:g}" for k in ("latency", "jitter", "loss") if route.get(k)]
    return (f"instance {route['instance']} reaches '{route['name']}' (port {route['target']}) through port "
            f"{route['listen']}{', ' + ', '.join(worse) if worse else ''}")


def describe(route):
    """One route as a line: what it does now, and what went through it."""
    worse = []
    if route["cut"]:
        worse.append("CUT")
    if route["latency"] or route["jitter"]:
        worse.append(f"latency {route['latency']:g} ms" + (f" +0-{route['jitter']:g}" if route["jitter"] else ""))
    if route["loss"]:
        worse.append(f"loss {route['loss']:g}%")
    return (f"{route['name']} instance {route['instance']}: port {route['port']} -> {route['target']}, "
            f"{', '.join(worse) or 'clear'}; TCP {route['tcp_open']} open ({route['tcp_connections']} in all), "
            f"{route['tcp_bytes_up']} B up, {route['tcp_bytes_down']} B down; UDP {route['udp_up']} up, "
            f"{route['udp_down']} down, {route['udp_dropped']} dropped")


if __name__ == "__main__":
    main()
