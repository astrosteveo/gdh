"""The debugger gdh runs beside a game started with `gdh live start --profiler`, for `gdh live profile`.

Godot's script profiler reports to a remote debugger only: the editor, over TCP, in Godot's binary Variant format
(variant.py). So with --profiler, gdh starts this process first, on a free port, and starts the game with
--remote-debug tcp://127.0.0.1:PORT. The game connects as it starts. This process tells it to skip breakpoints and
not to break on errors, so it never stops waiting for a debugger's say-so, and keeps the connection open for as long
as the game runs, as the game can't connect later.

It answers requests on a Unix socket only this user can reach, a JSON object a line:
  {"cmd": "start", "max": 256}   turn the profiler on (at most `max` functions a frame), keeping its frames, and
                                 answer once the first has come
  {"cmd": "stop", "until": N}    turn it off once frame N's profile has come (or after a moment, with no `until`),
                                 and return {"frames": [...], "signatures": {id: "path::line::name"}}
  {"cmd": "status"}              {"connected": bool, "recording": bool}

Each frame the profiler sends ("servers:profile_frame") is kept as {"frame", "frame_time", "process_time",
"physics_time", "physics_frame_time", "script_time", "servers": {name: {function: seconds}}, "functions":
[[signature id, calls, self seconds, total seconds, internal seconds]]}: the layout of Godot's
ServersDebugger::ServersProfilerFrame::serialize().
"""
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

from gdh import variant
from gdh.godot import GdhError


class DebuggerError(GdhError):
    pass


def parse_frame(data):
    """A "servers:profile_frame" message's data as a dictionary."""
    frame = dict(zip(("frame", "frame_time", "process_time", "physics_time", "physics_frame_time", "script_time"),
                     data[:6]))
    i = 6
    servers = {}
    for _ in range(data[i] if i < len(data) else 0):
        i += 1
        name = data[i]
        i += 1
        count = data[i]
        servers[name] = {data[i + 1 + k]: data[i + 2 + k] for k in range(0, count, 2)}
        i += count
    i += 1
    count = data[i] if i < len(data) else 0
    functions = [data[i + 1 + k:i + 6 + k] for k in range(0, count, 5)]
    frame["servers"] = servers
    frame["functions"] = functions
    return frame


class Debugger:
    def __init__(self):
        self.lock = threading.Lock()
        self.game = None
        self.connected = False
        self.ended = False
        self.recording = False
        self.frames = []
        self.signatures = {}

    def send(self, name, data):
        with self.lock:
            if self.game is not None:
                self.game.sendall(variant.message(name, data))

    def serve_game(self, listener):
        listener.settimeout(300)
        try:
            game, _ = listener.accept()
        except OSError:
            self.ended = True
            return
        self.game = game
        self.connected = True
        self.send("set_skip_breakpoints", [True])
        self.send("set_ignore_error_breaks", [True])
        try:
            while True:
                size = struct.unpack("<I", read_exact(game, 4))[0]
                body = read_exact(game, size)
                try:
                    msg = variant.decode(body)
                except variant.VariantError:
                    continue
                if not isinstance(msg, list) or len(msg) < 3:
                    continue
                self.handle(msg[0], msg[2])
        except (OSError, EOFError):
            pass
        self.ended = True

    def handle(self, name, data):
        if name == "debug_enter":  # a break all the same: go on
            self.send("continue", [])
        elif name == "servers:function_signature":
            self.signatures[data[1]] = data[0]
        elif name == "servers:profile_frame" and self.recording:
            try:
                self.frames.append(parse_frame(data))
            except (IndexError, TypeError, KeyError):
                pass

    def answer(self, request):
        cmd = request.get("cmd")
        if cmd == "status":
            return {"connected": self.connected, "recording": self.recording, "ended": self.ended}
        if not self.connected:
            return {"error": "The game never connected to gdh's debugger."}
        if cmd == "start":
            self.frames = []
            self.recording = True
            self.send("profiler:servers", [True, [int(request.get("max", 256)), bool(request.get("native", False))]])
            # The game turns its profiler on when its debugger next reads messages, which can be some frames on, so
            # wait for its frames: a step after this is profiled from its first frame. (They come while it's held.)
            if not self.wait(lambda: self.frames, float(request.get("timeout", 10))):
                self.send("profiler:servers", [False, []])
                self.recording = False
                return {"error": "The game sent gdh's debugger no profiled frames."}
            return {"ok": True}
        if cmd == "stop":
            # The game sends a frame's profile after the frame, so with `until`, wait for that frame's; without,
            # give the frames still on the way a moment.
            until = request.get("until")
            if until is None:
                time.sleep(float(request.get("settle", 0.3)))
            came = until is None or self.wait(lambda: self.frames and self.frames[-1]["frame"] >= until,
                                              float(request.get("timeout", 10)))
            self.send("profiler:servers", [False, []])
            self.recording = False
            if not came:
                return {"error": f"The game sent gdh's debugger no profile of frame {until}."}
            return {"frames": self.frames, "signatures": {str(k): v for k, v in self.signatures.items()}}
        return {"error": f"Unknown command {cmd!r}."}

    def wait(self, done, timeout):
        """Whether `done()` came true within `timeout` seconds, while the game is connected."""
        deadline = time.monotonic() + timeout
        while not done():
            if self.ended or time.monotonic() > deadline:
                return False
            time.sleep(0.01)
        return True


def read_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data


def serve_control(debugger, path):
    Path(path).unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX)
    server.bind(path)
    os.chmod(path, 0o600)
    server.listen(4)
    server.settimeout(1)
    while not debugger.ended:
        try:
            conn, _ = server.accept()
        except socket.timeout:
            continue
        with conn:
            line = b""
            while not line.endswith(b"\n"):
                chunk = conn.recv(1 << 16)
                if not chunk:
                    break
                line += chunk
            try:
                reply = debugger.answer(json.loads(line or b"{}"))
            except (ValueError, OSError) as e:
                reply = {"error": str(e)}
            conn.sendall((json.dumps(reply) + "\n").encode())
    Path(path).unlink(missing_ok=True)


def main(config_path):
    config = json.loads(Path(config_path).read_text())
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    debugger = Debugger()
    threading.Thread(target=debugger.serve_game, args=(listener,), daemon=True).start()
    control = threading.Thread(target=serve_control, args=(debugger, config["socket"]), daemon=True)
    control.start()
    Path(config["ready"]).write_text(str(listener.getsockname()[1]))
    while not debugger.ended:
        time.sleep(0.2)
    time.sleep(1.2)  # the control socket's last answers
    return 0


# --- From gdh ------------------------------------------------------------------------------------------------------


def start(folder, out, timeout=10):
    """Start the debugger. Returns its record: {pid, socket, port}."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    ready = folder / "debugger-ready"
    ready.unlink(missing_ok=True)
    config = folder / "debugger.json"
    record = {"socket": str(folder / "debugger.sock"), "ready": str(ready)}
    config.write_text(json.dumps(record))
    log = open(Path(out) / "debugger.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "gdh.debugger", str(config)], stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + timeout
    while not ready.exists() or not ready.read_text():
        if proc.poll() is not None or time.monotonic() > deadline:
            proc.kill()
            raise DebuggerError(f"gdh's debugger didn't start: see {Path(out) / 'debugger.log'}.")
        time.sleep(0.05)
    return {"pid": proc.pid, "socket": record["socket"], "port": int(ready.read_text())}


def request(record, cmd, timeout=60, **args):
    """Send the debugger a request and return its answer; raises DebuggerError for an error or no answer."""
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(timeout)
            s.connect(record["socket"])
            s.sendall((json.dumps({"cmd": cmd, **args}) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(1 << 20)
                if not chunk:
                    break
                data += chunk
    except OSError as e:
        raise DebuggerError(f"Can't reach gdh's debugger: {e}") from None
    reply = json.loads(data or b"{}")
    if "error" in reply:
        raise DebuggerError(reply["error"])
    return reply


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
