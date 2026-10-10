"""gdh live: start a game off-screen and drive it through harness/bridge.gd.

Each command connects to the running game, sends one request and prints the
reply. The game is held between commands, so no game time passes unless a
command steps it.

A session can also start companion processes beside the game (a server, say:
companions.py), and run several instances of the game, which step together.

Whatever a game spawns (a launcher's game, a tool) carries the game's GDH_MARK in its environment (spawned.py):
status lists those processes, and they end with the session. With --keep-children the session, and its display,
last until they have ended too.

Results go to stdout. What went wrong (engine errors, DEFECT lines, notes) and what the game printed go to stderr,
so a caller that throws stdout away still sees them.
"""
import argparse
import contextlib
import csv
import io
import json
import os
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gdh import companions, netem, restart, spawned, timeline
from gdh.display import CHOICES, open_display, parse_resolution, stop_displays
from gdh.godot import (HARNESS, GdhError, build_csharp, godot_cmd, godot_env, kill_groups, pid_alive, project_ticks,
                       size_mismatch, write_alert_shims)
from gdh.images import crop_findings, save_tiles
from gdh.imports import describe_missing, ensure_imported, missing_resources

LIVE_SCRIPT = HARNESS / "live.gd"
SESSION_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or Path.home() / ".cache") / "gdh"

# Commands every instance runs at once: the instances step together.
EVERY_INSTANCE = ("step", "run", "pause", "quit")


class LiveError(GdhError):
    pass


# --- Sessions -----------------------------------------------------------------

def session_path(name):
    return SESSION_DIR / f"{name}.json"


def log_tail(path, lines=15):
    try:
        return "\n".join(Path(path).read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return "(no log)"


def remove_session(session):
    session_path(session["name"]).unlink(missing_ok=True)
    shutil.rmtree(session.get("shims", ""), ignore_errors=True)
    if session.get("net"):
        shutil.rmtree(Path(session["net"]["socket"]).parent, ignore_errors=True)


def instances(session):
    """The session's game instances. A session file from before instances holds just the one."""
    if session.get("instances"):
        return session["instances"]
    return [{"pid": session["pid"], "port": session["port"], "token": session["token"], "log": session["log"],
             "out": session["out"], "groups": session.get("groups", [session["pid"]])}]


def all_groups(session):
    return session.get("groups", [session["pid"]])


def displays(session):
    return [g["display"] for g in instances(session) if g.get("display")]


def spawned_by(session, index=None):
    """The live processes the session's games spawned ([{"pid", "cmd"}]): every instance's, or instance `index`'s."""
    games = instances(session)
    chosen = games if index is None else [games[index]]
    pids = {g["pid"] for g in games}
    return [p for g in chosen for p in spawned.marked(g.get("mark"), exclude=pids)]


def session_running(session):
    """Whether any of the session's games runs, or, with --keep-children, any process they spawned."""
    if any(pid_alive(g["pid"]) for g in instances(session)):
        return True
    return bool(session.get("keep_children") and spawned_by(session))


def describe_spawned(procs, prefix=""):
    return "\n".join(f"{prefix}spawned: pid {p['pid']}: {p['cmd'][:150]}" for p in procs)


def stop_all(session):
    """Stop every process the session started or its games spawned, and remove its displays' runtime directories."""
    games = [g["pid"] for g in instances(session)]
    kill_groups(*games)
    spawned.stop([p["pid"] for p in spawned_by(session)])
    kill_groups(*all_groups(session))
    stop_displays(displays(session))


def load_session(name, ended_ok=False):
    """The session, after checking its games run. A game that has ended ends the session (its processes are stopped
    and it is removed), unless --keep-children keeps it for the processes the game spawned: then it raises, or, with
    ended_ok, returns the session for commands that don't need the game (status)."""
    path = session_path(name)
    if not path.exists():
        raise LiveError(f"No live session '{name}'. Start one with: gdh live start --project <dir>")
    session = json.loads(path.read_text())
    # A command on the session: with --keep-children, the watchdog's idle timeout counts from this.
    try:
        os.utime(path)
    except OSError:
        pass
    for i, instance in enumerate(instances(session)):
        if not pid_alive(instance["pid"]):
            children = spawned_by(session) if session.get("keep_children") else []
            if children:
                if ended_ok:
                    continue
                which = f"Instance {i} of session '{name}'" if len(instances(session)) > 1 else f"The game of session '{name}'"
                raise LiveError(f"{which} has exited, so it takes no commands. --keep-children keeps the session, "
                                f"and its display, while the processes it spawned run:\n{describe_spawned(children)}\n"
                                f"Their output goes to {instance['log']}. gdh live status lists them; gdh live stop "
                                f"--session {name} stops them.")
            stop_all(session)
            remove_session(session)
            which = f"Instance {i} of session '{name}'" if len(instances(session)) > 1 else f"Session '{name}'"
            raise LiveError(f"{which} has ended. Last lines of {instance['log']}:\n{log_tail(instance['log'])}")
    for companion in session.get("companions", []):
        if not pid_alive(companion["pid"]):
            print(f"note: companion '{companion['name']}' has exited. Last lines of {companion['log']}:\n"
                  f"{log_tail(companion['log'], 8)}", file=sys.stderr)
    return session


def request(instance, cmd, args=None, timeout=300):
    try:
        sock = socket.create_connection(("127.0.0.1", instance["port"]), timeout=10)
    except OSError as e:
        raise LiveError(f"Can't reach the game on port {instance['port']}: {e}") from e
    with sock:
        sock.settimeout(timeout)
        message = {"id": 1, "token": instance["token"], "cmd": cmd, "args": args or {}}
        sock.sendall((json.dumps(message) + "\n").encode())
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(1 << 16)
            if not chunk:
                raise LiveError("The game closed the connection.")
            data += chunk
    return json.loads(data.split(b"\n", 1)[0])


def pick(session, instance):
    """The instance indices `instance` names: a number, or "all"."""
    count = len(instances(session))
    if instance == "all":
        return list(range(count))
    try:
        index = int(instance)
    except (TypeError, ValueError):
        raise LiveError(f"--instance takes a number or all, not {instance!r}.") from None
    if not 0 <= index < count:
        raise LiveError(f"No instance {index}: the session runs {count} (0 to {count - 1}).")
    return [index]


def call(session, cmd, args=None, instance=0, timeout=300):
    """Send a command, and return the reply.

    step, run, pause and quit go to every instance at once, so the instances
    step together; a step's input events go only to the instances `instance`
    names. Anything else goes to the instances `instance` names. With one
    instance the reply is the game's own. With more, a command sent to one
    instance gets its reply (with "instance"), and one sent to several gets
    {"ok", "instances": [reply, ...], "error"?}.
    """
    games = instances(session)
    args = args or {}
    chosen = pick(session, instance)
    started = time.time()
    if cmd in EVERY_INSTANCE:
        sends = [(i, args if i in chosen or cmd != "step" else {**args, "events": []}) for i in range(len(games))]
    else:
        sends = [(i, args) for i in chosen]
    if len(sends) == 1:
        replies = [request(games[sends[0][0]], cmd, sends[0][1], timeout)]
    else:
        with ThreadPoolExecutor(len(sends)) as pool:
            replies = list(pool.map(lambda s: request(games[s[0]], cmd, s[1], timeout), sends))
    restart.log_input(session, cmd, args, instance, replies)
    timeline.record(session, cmd, args, instance, replies, [i for i, _ in sends], started,
                    lambda i, c, a: request(games[i], c, a, 60))
    if len(games) == 1:
        return replies[0]
    for (i, _), reply in zip(sends, replies):
        reply["instance"] = i
    if len(sends) == 1:
        return replies[0]
    combined = {"ok": all(r.get("ok") for r in replies), "instances": replies}
    failed = [r for r in replies if not r.get("ok")]
    if failed:
        combined["error"] = f"instance {failed[0]['instance']}: {failed[0].get('error', 'Request failed.')}"
    return combined


# --- Output -------------------------------------------------------------------

# The engine errors (not warnings) that the replies to the command under way carried, for --strict.
raised = []


def problems(part, prefix=""):
    """Print on stderr what the game printed, and the notes, engine errors (with a script's backtrace) and DEFECT
    lines of one reply, or of a ready file."""
    sys.stdout.flush()  # the results so far first, when stdout and stderr go to one place
    for line in part.get("output", []):
        print(f"{prefix}game: {line}", file=sys.stderr)
    for note in part.get("notes", []):
        print(f"{prefix}note: {note}", file=sys.stderr)
    for e in part.get("errors", []):
        count = f" (x{e['count']})" if e.get("count", 1) > 1 else ""
        print(f"{prefix}{e['type']}: {e['message']}{count} at {e['where']}", file=sys.stderr)
        for frame in e.get("backtrace", []):
            print(f"{prefix}  at {frame}", file=sys.stderr)
    missing = missing_resources(part.get("errors", []))
    if missing:
        print(f"{prefix}{describe_missing(missing)}", file=sys.stderr)
    sys.stderr.flush()


def count_errors(part):
    raised.extend(e for e in part.get("errors", []) if e.get("type") != "warning")


def report(reply, as_json, echo=True):
    """Print a reply's problems on stderr (problems()), or the whole reply as JSON. Returns
    the result (a list of results for a reply from several instances), or
    raises on failure. echo=False defers JSON printing to the caller."""
    if as_json:
        if echo:
            print(json.dumps(reply, indent=2))
    else:
        for part in reply.get("instances", [reply]):
            problems(part, f"[{part['instance']}] " if "instance" in part else "")
    for part in reply.get("instances", [reply]):
        count_errors(part)
    if not reply.get("ok"):
        raise LiveError(reply.get("error", "Request failed."))
    if "instances" in reply:
        return [part.get("result", {}) for part in reply["instances"]]
    return reply.get("result", {})


def each(reply, result):
    """(prefix, result) for each instance in a reply: one, or several."""
    if "instances" in reply:
        return [(f"[{part['instance']}] ", r) for part, r in zip(reply["instances"], result)]
    return [(f"[{reply['instance']}] " if "instance" in reply else "", result)]


def describe_display(instance):
    display = instance.get("display")
    return f"display {display['kind']} {display['name']}" if display else "display xvfb"


def describe_status(status, prefix=""):
    seconds = status["frame"] / max(status.get("ticks_per_second", 60), 1)
    state = "held" if status["held"] else "running"
    lines = [f"{prefix}{state} at frame {status['frame']} ({seconds:.2f} s), scene {status['scene']}"]
    for node in status.get("runs_while_held", []):
        lines.append(f"{prefix}  runs while held ({node['mode']}): {node['node']}")
    return "\n".join(lines)


def print_tree(node, indent=0):
    extras = []
    for key in ("script", "pos", "screen", "text", "value", "velocity", "linear_velocity",
                "current_animation", "animation", "process_mode"):
        if key in node:
            value = node[key]
            extras.append(f"{key}={json.dumps(value) if isinstance(value, str) else value}")
    if node.get("hidden"):
        extras.append("hidden")
    print(f"{'  ' * indent}{node['name']} ({node['class']}){' ' + ' '.join(extras) if extras else ''}")
    for child in node.get("children", []):
        print_tree(child, indent + 1)
    if node.get("more_children"):
        print(f"{'  ' * (indent + 1)}... {node['more_children']} more")


# --- Starting -------------------------------------------------------------------

def start_instance(project, args, index, count, out, shims, ports, ticks):
    """Launch one instance of the game on a display of its own. Returns (record, process, ready file)."""
    name = args.session if count == 1 else f"{args.session}-{index}"
    ready = SESSION_DIR / f"{name}-ready.json"
    ready.unlink(missing_ok=True)
    token = secrets.token_hex(16)
    mark = secrets.token_hex(8)
    user_args = ["--ready-file", str(ready), "--out", str(out), "--resolution", args.resolution,
                 "--idle-timeout", str(args.idle_timeout), "--ticks", str(ticks)]
    if args.scene:
        user_args += ["--scene", args.scene]
    if getattr(args, "seed", None) is not None:
        user_args += ["--seed", str(args.seed)]
    if getattr(args, "locale", None):
        user_args += ["--locale", args.locale]
    game_args = [companions.expand(a, ports, instance=index) for a in args.game_args]
    # --fixed-fps matching the tick rate makes every frame exactly one physics tick.
    # --gpu-profile makes the renderer capture a timestamp at each pass, which `frames` reads (harness/frames.gd).
    profile = ["--gpu-profile"] if getattr(args, "gpu_passes", False) else []
    cmd = godot_cmd(project, args.resolution, [*profile, "--fixed-fps", str(ticks), "--script", str(LIVE_SCRIPT)], game_args)
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "godot.log"
    own_user_data = restart.user_data_env(args, project, out)  # before the display, which a failure would leave
    display = open_display(args.display, args.resolution, out / "display.log")
    user_args += ["--display", display.kind]
    # The token goes in the environment, which only this user can read. The
    # command line is visible to everyone in the process list.
    env = godot_env(shims, display.name, user_args)
    env["GDH_TOKEN"] = token
    env[spawned.VAR] = mark  # everything the game spawns inherits it (spawned.py)
    env.update(own_user_data)
    try:
        with open(log_path, "w") as log:
            proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    env=env, start_new_session=True)
    except BaseException:
        display.stop()
        raise
    # pid is Godot, the process that matters for liveness. groups are the
    # process groups of Godot and its display, which stop kills.
    record = {"pid": proc.pid, "port": 0, "token": token, "out": str(out), "log": str(log_path),
              "groups": [proc.pid, *display.groups], "display": display.record(), "mark": mark}
    return record, proc, ready


def wait_ready(record, proc, ready, deadline, label, resolution):
    while not ready.exists():
        if proc.poll() is not None:
            raise LiveError(f"Godot{label} exited with code {proc.returncode} before the game was ready. "
                            f"Last lines of {record['log']}:\n{log_tail(record['log'])}")
        if time.monotonic() > deadline:
            raise LiveError(f"The game{label} wasn't ready in time. Last lines of {record['log']}:\n{log_tail(record['log'])}")
        time.sleep(0.2)
    info = json.loads(ready.read_text())
    ready.unlink()
    problems(info, label.strip() + " " if label else "")
    count_errors(info)
    if not info.get("port"):
        raise LiveError(info.get("error") or "The game didn't open a port.")
    mismatch = size_mismatch(info.get("status", {}).get("window_size"), resolution)
    if mismatch:
        raise LiveError(f"Not started{label}: {mismatch}")
    record["port"] = info["port"]
    return info


def start_watchdog(watch, groups, remove, marks, keep_children, idle_timeout, name, logs):
    """A detached process that stops the session's other processes once any game ends, or with keep_children once
    every game and every process they spawned has ended, or every game has exited and no gdh command has touched the
    session for idle_timeout seconds (watchdog.py)."""
    keep = ["--keep-children", "--idle-timeout", str(idle_timeout), "--session-file", str(session_path(name)),
            "--logs", *logs] if keep_children else []
    proc = subprocess.Popen([sys.executable, "-m", "gdh.watchdog", "--watch", *map(str, watch),
                             "--groups", *map(str, groups), "--remove", *remove, "--marks", *marks, *keep],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    return proc.pid


def cmd_start(args):
    name = args.session
    existing = session_path(name)
    if existing.exists():
        old = json.loads(existing.read_text())
        if session_running(old):
            raise LiveError(f"Session '{name}' is already running. Stop it with: gdh live stop --session {name}")
        stop_all(old)
        remove_session(old)
    if args.instances < 1:
        raise LiveError("--instances takes 1 or more.")
    commands = companions.parse_named(args.companion, "--companion")
    checks = companions.parse_named(args.companion_ready, "--companion-ready")
    fixed = companions.parse_named(args.companion_port, "--companion-port")
    for option, named in (("--companion-ready", checks), ("--companion-port", fixed)):
        for key in named:
            if key not in commands:
                raise LiveError(f"{option} names '{key}', which isn't a --companion.")
    ports = {key: companions.port_of(fixed[key]) if key in fixed else companions.free_port() for key in commands}
    # Check every placeholder before anything starts.
    for key, command in commands.items():
        companions.expand(command, ports, own=key)
    for arg in args.game_args:
        companions.expand(arg, ports, instance=0)
    for key in args.net:
        if key not in commands:
            raise LiveError(f"--net names '{key}', which isn't a --companion.")
    shaping = {"latency": args.net_latency, "jitter": args.net_jitter, "loss": args.net_loss}
    if any(v < 0 for v in shaping.values()) or args.net_loss > 100:
        raise LiveError("--net-latency and --net-jitter take milliseconds, and --net-loss a percent from 0 to 100.")
    if not args.net and any(shaping.values()):
        raise LiveError("--net-latency, --net-jitter and --net-loss go with --net NAME: the companion to put behind "
                        "the network proxy.")
    project = Path(args.project).resolve()
    parse_resolution(args.resolution)
    restart.prepare_start(args, name)
    if not args.no_build:
        build_csharp(project, force=getattr(args, "rebuild", False))
    if not args.no_import:
        ensure_imported(project)
    out = Path(args.out or Path.cwd() / "captures" / "live" / name).resolve()
    out.mkdir(parents=True, exist_ok=True)
    SESSION_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    # The game can raise alerts at any time, so the stand-ins live as long as the session.
    shims = write_alert_shims(SESSION_DIR / f"{name}-shims")
    ticks = project_ticks(project)
    started_groups = []
    started = []
    session = {"name": name, "shims": str(shims)}
    try:
        for key, command in commands.items():
            try:
                record = companions.start(key, command, ports[key], ports, out, checks.get(key, "tcp"), args.timeout)
            except companions.CompanionError as e:
                started_groups.append(e.record["pid"])
                raise LiveError(f"{e} Last lines of {e.record['log']}:\n{log_tail(e.record['log'])}") from None
            started_groups.append(record["pid"])
            session.setdefault("companions", []).append(record)
            print(f"companion '{key}': pid {record['pid']}, port {record['port']}, log {record['log']}")
        if args.net:
            net = netem.start(netem.plan(args.net, ports, args.instances, shaping), args.seed,
                              SESSION_DIR / f"{name}-net", out)
            started_groups.append(net["pid"])
            session["net"] = net
            for route in net["routes"]:
                print(f"network: {netem.describe_route_start(route)}")
        deadline = time.monotonic() + args.timeout
        for index in range(args.instances):
            instance_out = out if args.instances == 1 else out / f"instance-{index}"
            own_ports = {**ports, **netem.ports_for(session.get("net"), index)}
            record, proc, ready = start_instance(project, args, index, args.instances, instance_out, shims, own_ports,
                                                 ticks)
            started_groups += record["groups"]
            started.append((record, proc, ready))
        infos = [wait_ready(record, proc, ready, deadline, "" if args.instances == 1 else f" (instance {i})",
                            args.resolution)
                 for i, (record, proc, ready) in enumerate(started)]
    except BaseException:
        kill_groups(*started_groups)
        stop_displays([record["display"] for record, _, _ in started])
        remove_session(session)
        raise
    games = [record for record, _, _ in started]
    extra = [g for g in started_groups if g not in [r["pid"] for r in games]]
    watchdog = start_watchdog([r["pid"] for r in games], extra,
                              [r["display"]["runtime_dir"] for r in games if r["display"].get("runtime_dir")],
                              [r["mark"] for r in games], args.keep_children, args.idle_timeout, name,
                              [r["log"] for r in games])
    first = games[0]
    session.update({"pid": first["pid"], "port": first["port"], "token": first["token"], "log": first["log"],
                    "out": str(out), "project": str(project), "instances": games, "watchdog": watchdog,
                    "keep_children": args.keep_children,
                    "groups": [*started_groups, watchdog]})
    session["scene"] = infos[0].get("status", {}).get("scene", "")  # the scene it started in, for list
    restart.begin_log(args, session, infos)
    timeline.begin(args, session, restarted=getattr(args, "_restarted", False))
    fd = os.open(session_path(name), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(session, f)
    for i, (record, info) in enumerate(zip(games, infos)):
        prefix = "" if len(games) == 1 else f"[{i}] "
        print(describe_status(info["status"], prefix))
        if len(games) > 1:
            print(f"{prefix}pid {record['pid']}, {describe_display(record)}, output in {record['out']}")
    print(f"session '{name}': pid {first['pid']}"
          f"{', ' + describe_display(first) if len(games) == 1 else ''}, output in {out}")
    return restart.after_start(args, session)


# --- Commands -----------------------------------------------------------------

def cmd_stop(args):
    path = session_path(args.session)
    if not path.exists():
        print(f"No live session '{args.session}'.")
        return 0
    session = json.loads(path.read_text())
    games = [g for g in instances(session) if pid_alive(g["pid"])]
    # The games first, so they can say goodbye to their companions; then everything else.
    for game in games:
        try:
            request(game, "quit", timeout=5)
        except (LiveError, OSError):
            pass
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and any(pid_alive(g["pid"]) for g in games):
        time.sleep(0.2)
    # The display exits with Godot, but make sure every group is gone.
    stop_all(session)
    remove_session(session)
    timeline.end(session)
    print(f"Stopped session '{args.session}'.")
    if session.get("timeline"):
        print(f"timeline: {Path(session['timeline']) / 'index.html'}")
    return 0


def cmd_status(args):
    session = load_session(args.session, ended_ok=True)
    games = instances(session)
    running = [i for i, g in enumerate(games) if pid_alive(g["pid"])]
    many = len(games) > 1
    # Each instance's status from its game, or, for a game that has exited (--keep-children), None.
    statuses = [None] * len(games)
    replies = []
    if len(running) == len(games):
        reply = call(session, "status", instance="all")
        replies.append(reply)
        result = report(reply, args.json, echo=False)
        for i, (_, status) in enumerate(each(reply, result)):
            statuses[i] = status
    else:
        for i in running:
            reply = call(session, "status", instance=i)
            replies.append(reply)
            statuses[i] = report(reply, args.json, echo=False)
    children = [spawned_by(session, i) for i in range(len(games))]
    if args.json:
        out = replies[0] if len(replies) == 1 and len(running) == len(games) else {"ok": True, "instances": replies}
        out["spawned"] = [{**p, "instance": i} if many else p for i, procs in enumerate(children) for p in procs]
        out["exited"] = [i for i in range(len(games)) if i not in running]
        print(json.dumps(out, indent=2))
        return 0
    for i, (status, instance) in enumerate(zip(statuses, games)):
        prefix = f"[{i}] " if many else ""
        if status is None:
            print(f"{prefix}the game has exited; the session lasts while the processes it spawned run "
                  f"(--keep-children). Output in {instance['log']}")
        else:
            print(describe_status(status, prefix))
        print(f"{prefix}  {describe_display(instance)}")
        if children[i]:
            print(describe_spawned(children[i], prefix + "  "))
    for companion in session.get("companions", []):
        state = "running" if pid_alive(companion["pid"]) else "exited"
        print(f"companion '{companion['name']}': {state}, pid {companion['pid']}, port {companion['port']}")
    return 0


MOUSE_BUTTONS = {"left": 1, "right": 2, "middle": 3}
# Godot's JoyButton and JoyAxis, in order, and the wheel's buttons.
JOY_BUTTONS = ["a", "b", "x", "y", "back", "guide", "start", "left_stick", "right_stick", "left_shoulder",
               "right_shoulder", "dpad_up", "dpad_down", "dpad_left", "dpad_right", "misc1", "paddle1", "paddle2",
               "paddle3", "paddle4", "touchpad"]
JOY_AXES = ["left_x", "left_y", "right_x", "right_y", "trigger_left", "trigger_right"]
WHEEL = {"up": 4, "down": 5, "left": 6, "right": 7}
MODIFIER_KEYS = {"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "meta": "Meta"}


def input_event(token, pressed, at):
    if token.startswith("joy:"):
        name = token[4:]
        if name not in JOY_BUTTONS and not name.isdigit():
            raise SystemExit(f"gdh: unknown gamepad button {name!r} ({', '.join(JOY_BUTTONS)}, or a number)")
        return {"joy_button": JOY_BUTTONS.index(name) if name in JOY_BUTTONS else int(name), "pressed": pressed,
                "at": at}
    if token.startswith("key:"):
        return {"key": token[4:], "pressed": pressed, "at": at}
    if token.startswith("mouse:"):
        name = token[6:]
        if name not in MOUSE_BUTTONS:
            raise SystemExit(f"gdh: unknown mouse button {name!r} (left, right or middle)")
        # Where the pointer is: --move puts it there first.
        return {"mouse_button": MOUSE_BUTTONS[name], "pressed": pressed, "at": at}
    return {"action": token, "pressed": pressed, "at": at}


def xy(text, option, form="X,Y"):
    try:
        x, y = (float(v) for v in text.split(","))
    except ValueError:
        raise SystemExit(f"gdh: {option} takes {form}, not {text!r}") from None
    return [x, y]


def device_input(args, n):
    """A step's wheel, touches, gamepad axes and mouse-look, as events."""
    events = []
    # The wheel: each notch a press and a release of a wheel button, a notch a frame, where the pointer is.
    if args.wheel_at and not args.wheel:
        raise SystemExit("gdh: --wheel-at needs a --wheel")
    at = xy(args.wheel_at, "--wheel-at") if args.wheel_at else None
    if at:
        events.append({"mouse_motion": at, "at": 0})
    for spec in args.wheel:
        direction, _, count = spec.partition(":")
        if direction not in WHEEL or not (count or "1").isdigit():
            raise SystemExit(f"gdh: --wheel takes up, down, left or right, and :N for N notches; not {spec!r}")
        count = int(count or 1)
        if count > n:
            raise SystemExit(f"gdh: --wheel turns a notch a frame: step at least {count} frames")
        for i in range(count):
            notch = {"mouse_button": WHEEL[direction], "at": i, **({"position": at} if at else {})}
            events += [{**notch, "pressed": True}, {**notch, "pressed": False}]
    # Touches: each a finger of its own, numbered from 0. A tap lifts after a frame; a drag moves evenly over the step
    # and lifts at its end.
    for index, point in enumerate(args.touch):
        p = xy(point, "--touch")
        events += [{"touch": p, "index": index, "pressed": True, "at": 0},
                   {"touch": p, "index": index, "pressed": False, "at": 1}]
    for index, spec in enumerate(args.touch_drag, start=len(args.touch)):
        ends = spec.split(":")
        if len(ends) != 2:
            raise SystemExit(f"gdh: --touch-drag takes X,Y:X,Y, not {spec!r}")
        a, b = (xy(p, "--touch-drag", "X,Y:X,Y") for p in ends)
        events.append({"touch": a, "index": index, "pressed": True, "at": 0})
        moves = max(n - 1, 1)
        for k in range(1, moves + 1):
            events.append({"touch_drag": [a[0] + (b[0] - a[0]) * k / moves, a[1] + (b[1] - a[1]) * k / moves],
                           "index": index, "at": k if n > 1 else 0})
        events.append({"touch": b, "index": index, "pressed": False, "at": n})
    # A stick or trigger stays where it's put, as a hand holds it, until another --axis moves it.
    for spec in args.axis:
        name, _, value = spec.partition("=")
        try:
            value = float(value)
        except ValueError:
            value = None
        if name not in JOY_AXES or value is None or not -1 <= value <= 1:
            raise SystemExit(f"gdh: --axis takes NAME=VALUE, VALUE from -1 to 1 and NAME one of {', '.join(JOY_AXES)}; "
                             f"not {spec!r}")
        events.append({"joy_axis": JOY_AXES.index(name), "value": value, "at": 0})
    for spec in args.look:
        events.append({"look": xy(spec, "--look", "DX,DY"), "at": 0})
    return events


def with_modifiers(events, mods, n):
    """Splits each key:MOD+KEY into its modifier keys and the key, and holds the step's --mod keys from its start to its
    end, around everything else. A key, button or pointer move made while modifiers are down carries them ("mods"), as
    a keyboard's state does; a modifier key's own press and release carry the ones held before it."""
    held = []
    for name in (m for spec in mods for m in spec.lower().split(",") if m):
        if name not in MODIFIER_KEYS:
            raise SystemExit(f"gdh: unknown modifier {name!r} (ctrl, shift, alt or meta)")
        if name not in held:
            held.append(name)

    def modifier(name, pressed, at, before):
        return {"key": MODIFIER_KEYS[name], "pressed": pressed, "at": at, **({"mods": before} if before else {})}

    out = [modifier(m, True, 0, held[:i]) for i, m in enumerate(held)]
    for event in events:
        combo = []
        if "+" in event.get("key", ""):
            *names, key = event["key"].split("+")
            names = [m.lower() for m in names]
            for name in names:
                if name not in MODIFIER_KEYS:
                    raise SystemExit(f"gdh: unknown modifier {name!r} in key:{event['key']} (ctrl, shift, alt or meta)")
            combo = [m for m in names if m not in held]
            event = {**event, "key": key, "mods": names}
        if held and any(k in event for k in ("key", "mouse_button", "mouse_motion", "look")):
            event = {**event, "mods": held + [m for m in event.get("mods", []) if m not in held]}
        presses = [modifier(m, True, event["at"], held + combo[:i]) for i, m in enumerate(combo)]
        releases = [modifier(m, False, event["at"], held + combo[:i]) for i, m in enumerate(combo)][::-1]
        out += presses + [event] if event.get("pressed", True) else [event] + releases
    return out + [modifier(m, False, n, held[:i]) for i, m in enumerate(held)][::-1]


def step_input_events(args, n):
    """The input events of a step of n frames from its input options (add_input_options), in time order."""
    events = []
    for point in args.move:
        x, y = (float(v) for v in point.split(","))
        events.append({"mouse_motion": [x, y], "at": 0})
    for token in args.press:
        events.append(input_event(token, True, 0))
    for token in args.release:
        events.append(input_event(token, False, 0))
    for token in args.hold:
        events += [input_event(token, True, 0), input_event(token, False, n)]
    for token in args.tap:
        events += [input_event(token, True, 0), input_event(token, False, 1)]
    # Typed text: a character a frame, each pressed and let go as a key with its character, as a keyboard types.
    if args.type is not None:
        if len(args.type) > n:
            raise SystemExit(f"gdh: --type needs a frame a character: step at least {len(args.type)} frames")
        for i, ch in enumerate(args.type):
            events += [{"text": ch, "pressed": True, "at": i}, {"text": ch, "pressed": False, "at": i}]
    # A click lets go after one frame; a hold at the end of the step, as --hold does.
    for button, points, until in ((1, args.click, 1), (2, args.right_click, 1),
                                  (1, args.left_hold, n), (2, args.right_hold, n)):
        for point in points:
            x, y = (float(v) for v in point.split(","))
            events += [{"mouse_motion": [x, y], "at": 0},
                       {"mouse_button": button, "position": [x, y], "pressed": True, "at": 0},
                       {"mouse_button": button, "position": [x, y], "pressed": False, "at": until}]
    # A click on a node, or on what shows a text: the game finds where it shows before the step's first frame.
    for key, targets in (("text", args.click_text), ("node", args.click_node)):
        for target in targets:
            on = {key: target}
            events += [{"mouse_motion": [0, 0], "on": on, "at": 0},
                       {"mouse_button": 1, "on": on, "pressed": True, "at": 0},
                       {"mouse_button": 1, "on": on, "pressed": False, "at": 1}]
    events = with_modifiers(events + device_input(args, n), args.mod, n)
    # In time order, each frame's as given: the game follows the pointer, its buttons and the fingers event by event.
    events.sort(key=lambda e: e["at"])
    return events


def cmd_step(args):
    session = load_session(args.session)
    n = step_length(args, session)
    step_args = {"frames": n, "events": step_input_events(args, n), "shot_every": args.shot_every}
    step_args.update(watch_args(args))
    if args.trail:
        step_args["track"] = args.trail
    if args.monitors:
        step_args["monitors"] = 0  # Godot's Performance monitors before the first frame and after the last (perf.py)
    # Generous: a big window that saves a frame every step can take seconds a frame under Xvfb.
    reply = call(session, "step", step_args, instance=args.instance, timeout=max(300, 2 * n))
    # With --trail, the JSON waits for the trails, which go in it.
    result = report(reply, args.json, echo=not args.trail)
    if not args.json:
        for prefix, r in each(reply, result):
            print(f"{prefix}stepped {r['frames']} frames")
            for target in r.get("aimed", []):
                took = f", which passed it to {describe_took(target['took'])}" if "took" in target else ""
                print(f"{prefix}  clicked {describe_match(target)} at {target['at'][0]:g},{target['at'][1]:g}{took}")
            for click in r.get("clicked", []):
                took = describe_took(click["took"]) if click["took"] else "no control (the game's _unhandled_input)"
                print(f"{prefix}  click at {click['at'][0]:g},{click['at'][1]:g} went to {took}")
            for path in r.get("shots", []):
                print(f"{prefix}  shot: {path}")
            print(describe_status(r["status"], prefix))
            if r.get("monitors"):
                from gdh import perf
                print(perf.describe_run(r["monitors"], prefix))
    unmet = watched(reply, result, args)
    if args.trail:
        trails(session, args, reply, result)
    if args.shot:
        shot(session, ["normal"], "after-step", False, args.json, args.instance)
    if unmet:
        raise LiveError(unmet)
    return 0


# Without --max or a frame count, the most frames a step with --until runs: a minute at 60 ticks a second.
UNTIL_MAX = 3600
# The most rows of a trace printed as text (--json and --trace-out have them all).
TRACE_ROWS = 40


def step_length(args, session):
    """The frames a step runs: its count (1 if none), or with --until the most it runs: --max, else the count, else
    UNTIL_MAX."""
    if args.every is not None and not (args.until or args.trace or args.trail):
        raise LiveError("--every sets how often --until and --trace check, and --trail's dots (--shot-every saves "
                        "frames).")
    if args.trail_out and not args.trail:
        raise LiveError("--trail-out is where --trail's image goes: give a --trail.")
    if args.trail and len(pick(session, args.instance)) > 1:
        raise LiveError("--trail draws one instance's frame: give --instance K.")
    if args.trace_out and not args.trace:
        raise LiveError("--trace-out writes the values of --trace.")
    if args.trace_chart and not args.trace:
        raise LiveError("--trace-chart draws the values of --trace.")
    if args.trace_rates and not args.trace_chart:
        raise LiveError("--trace-rates adds panels to the --trace-chart: give one.")
    if args.until is None:
        if args.max is not None:
            raise LiveError("--max bounds a step with --until.")
        return 1 if args.frames is None else args.frames
    if len(instances(session)) > 1:
        raise LiveError("--until needs a session of one instance: the instances step together, and each would stop "
                        "on its own.")
    return args.max or args.frames or UNTIL_MAX


def watch_args(args):
    """A step's --until, --trace and --every as the protocol's step arguments."""
    out = {}
    if args.until:
        out["until"] = args.until
    if args.trace:
        out["trace"] = args.trace
    if out:
        out["every"] = args.every or 1
    return out


def watched(reply, result, args):
    """Print a step's --until and --trace results (unless --json), and write --trace-out. Returns why --until didn't
    hold, or None."""
    unmet = None
    parts = each(reply, result)
    for i, (prefix, r) in enumerate(parts):
        until, trace = r.get("until"), r.get("trace")
        path = chart = None
        if trace and args.trace_out:
            path = Path(args.trace_out) if len(parts) == 1 else Path(args.trace_out).with_suffix(f".{i}.csv")
            write_trace(trace, path)
        if trace and args.trace_chart:
            chart = Path(args.trace_chart) if len(parts) == 1 else Path(args.trace_chart).with_suffix(f".{i}.png")
            chart_trace(trace, chart, args.trace_rates, r["status"].get("ticks_per_second", 60), prefix)
        if trace:
            for expr, error in trace.get("failed", {}).items():
                sys.stdout.flush()
                print(f"{prefix}note: trace {expr} failed (its value is null): {error}", file=sys.stderr)
        if until and not until["met"]:
            last = (f"its last check, at frame {until['frame']}, failed: {until['error']}" if until.get("error")
                    else f"it was {json.dumps(until['value'])} at frame {until['frame']}" if until["checks"]
                    else "it was never checked, as --every is more than that")
            unmet = f"{prefix}--until {until['expr']} didn't hold in {r['frames']} frames: {last}"
        if args.json:
            continue
        if until and until["met"]:
            print(f"{prefix}until held at frame {until['frame']}: {json.dumps(until['value'])}")
        if trace:
            print_trace(trace, prefix)
        if path:
            print(f"{prefix}trace: {path}")
        if chart:
            print(f"{prefix}chart: {chart}")
    return unmet


# The most dots' spacings printed for a trail (--json has them all).
TRAIL_SPACINGS = 24


def trails(session, args, reply, result):
    """Draw a step's --trail over a shot of its last frame, and print where it went (or put it in the JSON)."""
    from gdh import motion
    if isinstance(result, list):
        result = result[pick(session, args.instance)[0]]
    shots = call(session, "shot", {"views": ["normal"], "label": "trail"}, instance=args.instance)
    frame = report(shots, False)["shots"]["normal"]
    out = Path(args.trail_out) if args.trail_out else Path(frame).with_name(Path(frame).name.replace("-normal", ""))
    every = args.every or 1
    drawn = motion.draw_trails(frame, [{"node": node, "points": result["track"][node]["points"]} for node in args.trail],
                               every, out)
    if not args.trail_out:
        Path(frame).unlink(missing_ok=True)
    if args.json:
        reply["trails"] = {"image": str(out), "every": every, "trails": drawn}
        print(json.dumps(reply, indent=2))
        return
    print(f"trail: {out}")
    for t in drawn:
        known = [d for d in t["dots"] if d[1] is not None]
        spacing = t["spacing"]
        shown = ", ".join("-" if v is None else f"{v:g}" for v in spacing[:TRAIL_SPACINGS])
        more = f" ... {len(spacing) - TRAIL_SPACINGS} more (--json has them all)" if len(spacing) > TRAIL_SPACINGS else ""
        where = (f"from {known[0][1]:g},{known[0][2]:g} at frame {known[0][0]} to {known[-1][1]:g},{known[-1][2]:g} at "
                 f"frame {known[-1][0]}") if known else "never on screen"
        print(f"  {t['node']}: {where}; px between dots every {every} frames: {shown or 'none'}{more}")
        sys.stdout.flush()
        if t["off_screen"]:
            print(f"note: trail {t['node']}: {t['off_screen']} of its points were off screen, so its line leaves the "
                  f"frame there", file=sys.stderr)
        if t["missing"]:
            print(f"note: trail {t['node']}: {t['missing']} of its points have no place on screen (behind the camera, "
                  f"or the node gone), so its line breaks there", file=sys.stderr)


def chart_trace(trace, path, rates, ticks, prefix=""):
    """Draw a trace as a chart (chart.py), with a note on stderr for each expression it couldn't draw."""
    from gdh import chart
    try:
        _, notes = chart.trace_chart(trace, path, rates, ticks)
    except chart.ChartError as e:
        raise LiveError(str(e)) from None
    for note in notes:
        sys.stdout.flush()
        print(f"{prefix}note: chart: {note}", file=sys.stderr)


def print_trace(trace, prefix=""):
    """A trace's rows, leaving out a row that repeats the one before (the last is always shown), at most TRACE_ROWS."""
    rows = trace["rows"]
    shown = [row for i, row in enumerate(rows) if i in (0, len(rows) - 1) or row[1:] != rows[i - 1][1:]]
    left = f", {len(shown)} of {len(rows)} rows: the rest repeat the row before" if len(shown) < len(rows) else ""
    print(f"{prefix}trace every {trace['every']} frames{left}: frame: {' | '.join(trace['exprs'])}")
    cut = len(shown) - TRACE_ROWS
    if cut > 0:
        shown = shown[:TRACE_ROWS // 2] + [None] + shown[-TRACE_ROWS // 2:]
    for row in shown:
        if row is None:
            print(f"{prefix}  ... {cut} rows not shown (--json and --trace-out FILE.csv have every row)")
        else:
            print(f"{prefix}  {row[0]}: {' | '.join(json.dumps(v) for v in row[1:])}")


def write_trace(trace, path):
    """A trace as CSV: a frame column, then one per expression; a string as it is, anything else as JSON, and a value
    that failed empty."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        out = csv.writer(f)
        out.writerow(["frame", *trace["exprs"]])
        for row in trace["rows"]:
            out.writerow([row[0], *("" if v is None else v if isinstance(v, str) else json.dumps(v) for v in row[1:])])


def shot(session, views, label, tiles, as_json, instance=0, framing=None, echo=True):
    reply = call(session, "shot", {"views": views, "label": label, **(framing or {})}, instance=instance)
    result = report(reply, as_json, echo)
    for prefix, r in each(reply, result):
        for view, path in r["shots"].items():
            if not as_json:
                region = ""
                if "crop" in r:
                    x, y, w, h = r["crop"]
                    region = f" (the screen's {x},{y} {w}x{h}, saved at {r['size'][0]}x{r['size'][1]})"
                print(f"{prefix}{view}: {path}{region}")
            if tiles and view == "normal":
                for tile in save_tiles(path, Path(path).with_suffix("")):
                    if not as_json:
                        print(f"{prefix}  tile: {tile}")
    return result


def cmd_shot(args):
    session = load_session(args.session)
    framing = {}
    if args.out:
        if len(pick(session, args.instance)) > 1:
            raise LiveError("--out names one file: give it one instance (--instance K).")
        framing["out"] = str(Path(args.out).resolve())
    if args.crop:
        framing["crop"] = numbers(args.crop, 4, "--crop")
    if args.node:
        framing.update({"node": args.node, "margin": args.margin})
    elif args.margin:
        raise LiveError("--margin goes with --node.")
    if args.zoom != 1:
        framing["zoom"] = args.zoom
    if args.max_width:
        framing["max_width"] = args.max_width
    if args.no_ui:
        framing["no_ui"] = True
    if args.filter and args.annotate is None:
        raise LiveError("--filter picks what --annotate labels: give --annotate.")
    if args.annotate is None:
        shot(session, args.view or ["normal"], args.label, args.tiles, args.json, args.instance, framing)
        return 0
    from gdh import annotate
    try:
        layers, filters = annotate.parse_layers(args.annotate), annotate.parse_filters(args.filter)
    except annotate.AnnotateError as e:
        raise LiveError(str(e)) from None
    if len(pick(session, args.instance)) > 1:
        raise LiveError("--annotate labels one instance's shot: give --instance K.")
    result = shot(session, args.view or ["normal"], args.label, args.tiles, args.json, args.instance, framing,
                  echo=False)
    reply = call(session, "annotate", {"layers": layers, "filter": filters, "no_ui": args.no_ui},
                 instance=args.instance)
    found = report(reply, args.json, echo=False)
    if result.get("crop"):  # only what's in a framed shot
        x, y, w, h = result["crop"]
        found["nodes"] = [n for n in found.get("nodes", []) if n["box"][0] < x + w and n["box"][0] + n["box"][2] > x
                          and n["box"][1] < y + h and n["box"][1] + n["box"][3] > y]
    for n, node in enumerate(found.get("nodes", []), 1):
        node["n"] = n
    frame = annotate.Frame(found["image_size"], result.get("crop"), result.get("size"))
    images = {}
    for view, path in result["shots"].items():
        images[view] = str(Path(path).with_name(Path(path).stem + "-annotated.png"))
        drawn = annotate.draw(path, found, frame, images[view])
    if args.json:
        print(json.dumps({**result, "annotated": images, "annotations": found, **drawn}, indent=2))
        return 0
    print_annotations(images, found, drawn)
    return 0


def print_annotations(images, found, drawn):
    """An annotated shot's files, and each numbered node with its box, as text."""
    for view, path in images.items():
        print(f"{view} annotated: {path}")
    for node in found.get("nodes", []):
        print(f"  {node['n']}: {node['path']} ({node['class']}) box={node['box']}")
    if found.get("more"):
        print(f"  ... {found['more']} more not boxed (at most {len(found['nodes'])}): narrow it with --filter")
    for node in found.get("backdrops", []):
        print(f"  backdrop, not boxed: {node['path']} ({node['class']}) box={node['box']}")
    for shape in found.get("shapes", []):
        print(f"  collision {shape['kind']}: {shape['path']}")
    for region in found.get("nav", []):
        print(f"  navigation: {region['path']}, {len(region['polygons'])} polygons")
    for v in found.get("velocity", []):
        print(f"  velocity: {v['path']} {v['speed']:g} {v['unit']}")
    if drawn["numbered"]:
        many = drawn["numbered"] > 1
        print(f"  ({drawn['numbered']} label{'s' if many else ''} had no room: {'those nodes show their number' if many else 'that node shows its number'} only)")


def numbers(text, count, option):
    """`count` numbers from comma-separated text, or an error naming the option."""
    try:
        values = [float(v) for v in text.split(",")]
    except ValueError:
        values = []
    if len(values) != count:
        raise LiveError(f"{option} takes {count} numbers separated by commas, not {text!r}.")
    return values


def describe_took(took):
    """What took a click: path (class, mouse_filter X)."""
    filt = f", mouse_filter {took['mouse_filter']}" if "mouse_filter" in took else ""
    return f"{took['path']} ({took['class']}{filt})"


def describe_match(m):
    """One node `find` found, as a line: path (class) text="..." screen=[x, y, w, h]."""
    extras = [f"text={json.dumps(m['text'])}"] if "text" in m else []
    if "screen" in m:
        extras.append(f"screen={m['screen']}")
    if m.get("disabled"):
        extras.append("disabled")
    if "why" in m:
        extras.append(m["why"])
    return f"{m['path']} ({m['class']}){' ' + ' '.join(extras) if extras else ''}"


def cmd_find(args):
    session = load_session(args.session)
    filters = {key: value for key, value in (("text", args.text), ("name", args.name), ("class", args.class_name))
               if value}
    if not filters:
        raise LiveError("find takes a TEXT, --name or --class.")
    reply = call(session, "find", filters, instance=args.instance)
    result = report(reply, args.json)
    found = False
    for prefix, r in each(reply, result):
        found = found or bool(r["matches"])
        if args.json:
            continue
        for m in r["matches"]:
            print(f"{prefix}{describe_match(m)}")
        if r.get("more"):
            print(f"{prefix}... {r['more']} more; narrow it with TEXT, --name or --class")
        if r["hidden"]:
            print(f"{prefix}not showing: {'; '.join(describe_match(m) for m in r['hidden'])}")
    if not found:
        raise LiveError("No node that shows matches.")
    return 0


def cmd_snapshot(args):
    from gdh import snapshot
    session = load_session(args.session)
    if args.grid < 1:
        raise LiveError("--grid takes a whole number of pixels, 1 or more.")
    boxes = args.boxes or args.grid > 1
    reply = call(session, "snapshot", {"path": args.path or ""}, instance=args.instance)
    result = report(reply, args.json)
    if isinstance(result, list):
        if args.baseline or args.update_baseline:
            raise LiveError("--baseline compares one instance's snapshot: give --instance N.")
        if not args.json:
            for prefix, r in each(reply, result):
                print("".join(f"{prefix}{line}\n" for line in snapshot.lines(r["nodes"], boxes, args.grid)), end="")
        return 0
    now = snapshot.text(result["nodes"], boxes, args.grid)
    if not args.json:
        print(now or "Nothing on screen to read or use.", end="" if now else "\n")
    if args.update_baseline:
        if not args.baseline:
            raise LiveError("--update-baseline writes the --baseline FILE: give one.")
        Path(args.baseline).parent.mkdir(parents=True, exist_ok=True)
        Path(args.baseline).write_text(now)
        print(f"wrote the baseline {args.baseline}", file=sys.stderr)
        return 0
    if args.baseline:
        try:
            kept = Path(args.baseline).read_text()
        except OSError as e:
            raise LiveError(f"Can't read the baseline {args.baseline}: {e.strerror}. --update-baseline writes it.") from None
        changed = snapshot.diff(kept, now, str(args.baseline), "now")
        if changed:
            print(changed, end="", file=sys.stderr)
            raise LiveError(f"The UI on screen isn't the same as the baseline {args.baseline} (the diff is above).")
        print(f"the same as the baseline {args.baseline}", file=sys.stderr)
    return 0


def cmd_net(args):
    session = load_session(args.session)
    if not session.get("net"):
        raise LiveError("This session has no network proxy: start it with --net NAME, NAME a --companion.")
    routes = {}
    if args.companion:
        routes["name"] = args.companion
    if args.instance != "all":
        routes["instance"] = pick(session, args.instance)[0]
    values = {k: getattr(args, k) for k in ("latency", "jitter", "loss") if getattr(args, k) is not None}
    if any(v < 0 for v in values.values()) or values.get("loss", 0) > 100:
        raise LiveError("--latency and --jitter take milliseconds, and --loss a percent from 0 to 100.")
    if args.cut and args.heal:
        raise LiveError("Give --cut or --heal, not both.")
    if args.cut or args.heal:
        values["cut"] = args.cut
    try:
        if values:
            state = netem.request(session["net"], "set", routes, values)
        if args.reset:
            state = netem.request(session["net"], "reset", routes)
        if not values and not args.reset:
            state = netem.request(session["net"], "state", routes)
    except netem.NetError as e:
        raise LiveError(str(e)) from None
    if args.json:
        print(json.dumps({"routes": state}, indent=2))
    else:
        for route in state:
            print(netem.describe(route))
    return 0


def cmd_camera(args):
    session = load_session(args.session)
    if bool(args.view) == args.release:
        raise LiveError("camera takes --view or --release.")
    if args.release:
        request_args = {"release": True}
    else:
        points = args.view.split(":")
        if len(points) == 2:
            if args.zoom is not None:
                raise LiveError("--zoom is for a 2D view (--view X,Y); a 3D one takes --fov.")
            request_args = {"from": numbers(points[0], 3, "--view"), "at": numbers(points[1], 3, "--view")}
            request_args.update({k: v for k, v in (("fov", args.fov), ("far", args.far)) if v is not None})
        elif len(points) == 1:
            if args.fov is not None or args.far is not None:
                raise LiveError("--fov and --far are for a 3D view (--view X,Y,Z:X,Y,Z); a 2D one takes --zoom.")
            request_args = {"at": numbers(points[0], 2, "--view"), "zoom": 1.0 if args.zoom is None else args.zoom}
        else:
            raise LiveError(f"--view takes X,Y,Z:X,Y,Z (3D) or X,Y (2D), not {args.view!r}.")
    reply = call(session, "camera", request_args, instance=args.instance)
    result = report(reply, args.json)
    if not args.json:
        for prefix, r in each(reply, result):
            if args.release:
                said = "released gdh's camera" if r["released"] else "no gdh camera to release"
                again = f"; {', '.join(r['restored'])} is the camera again" if r["restored"] else ""
                print(f"{prefix}{said}{again}")
                continue
            instead = f", in place of {r['replaced']}" if r["replaced"] else ""
            if r["camera"] == "3d":
                print(f"{prefix}looking from {','.join(f'{v:g}' for v in r['from'])} at {','.join(f'{v:g}' for v in r['at'])}"
                      f", fov {r['fov']:g}, far {r['far']:g}{instead}")
            else:
                print(f"{prefix}centred on {','.join(f'{v:g}' for v in r['at'])} at zoom {r['zoom']:g}{instead}")
    return 0


def cmd_probes(args):
    session = load_session(args.session)
    reply = call(session, "probes", instance=args.instance)
    result = report(reply, args.json, echo=False)
    for prefix, r in each(reply, result):
        findings = r["findings"]
        normal = Path(r["shots"]["normal"])
        crop_findings(findings, r["image_size"], r["shots"], normal.parent / normal.name.replace("-normal.png", "-crops"))
        if args.json:
            continue
        print(f"{prefix}{len(findings)} findings, frame shot: {normal}")
        for f in findings:
            crop = f"  [{f['crop']}]" if f.get("crop") else ""
            print(f"{prefix}  {f['severity']}: {f['probe']} {f['node']}: {f['message']}{crop}")
    if args.json:
        print(json.dumps(reply, indent=2))
    return 0


def cmd_tree(args):
    session = load_session(args.session)
    reply = call(session, "tree", {"path": args.path or "", "depth": args.depth, "visible_only": args.visible_only},
                 instance=args.instance)
    result = report(reply, args.json)
    if not args.json:
        for prefix, r in each(reply, result):
            if prefix:
                print(prefix.strip())
            print_tree(r["tree"])
            if r.get("cut_nodes"):
                print(f"({r['cut_nodes']} nodes not shown; narrow with a path or --depth)")
    return 0


def cmd_eval(args):
    session = load_session(args.session)
    reply = call(session, "eval", {"expr": args.expr}, instance=args.instance)
    result = report(reply, args.json)
    if not args.json:
        for prefix, r in each(reply, result):
            print(f"{prefix}{json.dumps(r['value'])}")
    return 0


def cmd_run(args):
    session = load_session(args.session)
    reply = call(session, "run")
    result = report(reply, args.json)
    if not args.json:
        for prefix, r in each(reply, result):
            print(describe_status(r, prefix))
    return 0


def cmd_pause(args):
    session = load_session(args.session)
    reply = call(session, "pause")
    result = report(reply, args.json)
    if not args.json:
        for prefix, r in each(reply, result):
            print(describe_status(r, prefix))
    return 0


def cmd_pipe(args):
    """Requests on stdin, one JSON object a line; each reply on stdout, one a line.

    A request is {"cmd": ..., "args": {...}, "instance": 0 | "all"}, with the
    commands and arguments of the raw protocol (docs/live.md) and call()'s rules
    for instances. The session is checked once, as the pipe opens.
    """
    session = load_session(args.session)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            if not isinstance(req, dict) or not isinstance(req.get("cmd"), str):
                raise LiveError('A request is a JSON object with a "cmd".')
            if req["cmd"] == "quit":
                raise LiveError("Stop the session with gdh live stop, which stops its companions too.")
            reply = call(session, req["cmd"], req.get("args") or {}, instance=req.get("instance", 0),
                         timeout=req.get("timeout") or reply_timeout(req["cmd"], req.get("args") or {}))
        except (GdhError, OSError, ValueError) as e:
            reply = {"ok": False, "error": str(e)}
            if not session_alive(session):
                try:
                    load_session(args.session)
                except LiveError as ended:  # how the session ended; it's cleaned up
                    reply["error"] = str(ended)
                print(json.dumps(reply), flush=True)
                return 1
        print(json.dumps(reply), flush=True)
    return 0


def reply_timeout(cmd, args):
    """How long a request waits for its reply, in seconds: a step's grows with its frames, as `gdh live step`'s does."""
    frames = args.get("frames") if isinstance(args, dict) else None
    return max(300, 2 * int(frames)) if cmd == "step" and isinstance(frames, (int, float)) else 300


def session_alive(session):
    return all(pid_alive(g["pid"]) for g in instances(session))


# --- Batches --------------------------------------------------------------------

# Commands a list of command lines can't hold: they start a session, or read stdin themselves.
NOT_IN_BATCH = ("start", "batch", "pipe", "restart")


class LineParser(argparse.ArgumentParser):
    """The CLI's parser for a command written as a line of text: a mistake raises LiveError, saying what's wrong in
    one line, where the CLI would print its usage and exit."""

    def error(self, message):
        raise LiveError(f"{self.prog.removeprefix('gdh ')}: {message}")

    def exit(self, status=0, message=None):
        raise LiveError(message.strip() if message else f"{self.prog.removeprefix('gdh ')}: stopped")


_line_parser = None


def parse_command_line(line, session):
    """A gdh live command written as on the command line ("step 30 --hold ui_right", with or without "gdh live"
    before it), parsed as the CLI parses it, for `session` unless the line names its own. Returns the parsed
    arguments, whose func runs it (run_command), or None for a blank line or a # comment. Raises LiveError for a line
    that isn't a command a list of commands can hold."""
    global _line_parser
    try:
        words = shlex.split(line, comments=True)
    except ValueError as e:
        raise LiveError(f"Can't split the line into words: {e}") from None
    if words[:2] == ["gdh", "live"]:
        words = words[2:]
    elif words[:1] == ["live"]:
        words = words[1:]
    if not words:
        return None
    if words[0] in NOT_IN_BATCH:
        raise LiveError(f"{words[0]} can't run from a list of commands.")
    if _line_parser is None:
        _line_parser = LineParser(prog="gdh")
        add_parsers(_line_parser.add_subparsers(dest="command", required=True))
    return _line_parser.parse_args(["live", words[0], "--session", session, *words[1:]])


def run_command(args):
    """Run parsed command arguments (parse_command_line) as the CLI would. Returns (exit code, error or None)."""
    try:
        return args.func(args) or 0, None
    except (GdhError, OSError, ValueError) as e:
        return 1, str(e)
    except SystemExit as e:
        if isinstance(e.code, str):
            return 1, e.code.removeprefix("gdh: ")
        return e.code or 0, None


def json_documents(text):
    """The JSON documents a command printed, one after another; text that isn't JSON as {"text": ...}."""
    decoder = json.JSONDecoder()
    docs = []
    text = text.strip()
    i = 0
    while i < len(text):
        try:
            doc, i = decoder.raw_decode(text, i)
        except ValueError:
            docs.append({"text": text[i:]})
            break
        docs.append(doc)
        while i < len(text) and text[i].isspace():
            i += 1
    return docs


def cmd_batch(args):
    """Command lines on stdin, in the CLI's own syntax, run one after another in this one process. Each prints what
    it would print alone, after a "> LINE" header; with --json, each line's replies are one JSON line,
    {"line", "ok", "replies", "error"?}. Exit status 1 if any command failed."""
    session = load_session(args.session)
    failed = []
    ran = 0
    for number, line in enumerate(sys.stdin, 1):
        text = line.strip()
        try:
            parsed = parse_command_line(text, args.session)
            code, error = 0, None
        except LiveError as e:
            parsed, code, error = None, 1, str(e)
        if parsed is None and not error:
            continue
        ran += 1
        if args.json:
            output = io.StringIO()
            if parsed:
                parsed.json = True
                parsed.strict = parsed.strict or args.strict
                with contextlib.redirect_stdout(output):
                    code, error = run_command(parsed)
            print(json.dumps({"line": text, "ok": code == 0, "replies": json_documents(output.getvalue()),
                              **({"error": error} if error else {})}), flush=True)
        else:
            print(f"> {text}", flush=True)
            if parsed:
                parsed.strict = parsed.strict or args.strict
                code, error = run_command(parsed)
            sys.stdout.flush()
            if error:
                print(f"gdh: {error}", file=sys.stderr, flush=True)
        if not code:
            continue
        failed.append(number)
        if args.stop_on_error:
            break
        if not session_path(args.session).exists() or not session_alive(session):
            print(f"gdh: session '{args.session}' has ended, so the batch stops at line {number}.", file=sys.stderr)
            break
    raised.clear()  # each command was judged on its own (--strict)
    if failed:
        lines = ", ".join(map(str, failed))
        stopped = f"; it stopped at line {failed[-1]}" if args.stop_on_error else ""
        print(f"gdh: {len(failed)} of {ran} commands failed (line{'s' if len(failed) > 1 else ''} {lines}){stopped}.",
              file=sys.stderr)
        return 1
    return 0


# --- Listing --------------------------------------------------------------------

def process_started(pid):
    """When a process started, in seconds since the epoch, from /proc; None if it has gone."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        boot = next(int(line.split()[1]) for line in Path("/proc/stat").read_text().splitlines()
                    if line.startswith("btime "))
        return boot + int(fields[19]) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration):
        return None


def describe_age(seconds):
    if seconds is None:
        return "?"
    seconds = int(seconds)
    if seconds < 120:
        return f"{seconds} s"
    if seconds < 7200:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"


def cmd_list(args):
    """Every live session of this user, whoever started it: its state, project, scene, pids, displays, how long its
    game has run and how long since a command touched it. Reading the session files changes nothing: a session whose
    game has ended is cleaned up by the next command on it."""
    now = time.time()
    rows = []
    for path in sorted(SESSION_DIR.glob("*.json")):
        try:
            session = json.loads(path.read_text())
            games = instances(session)
            idle = now - path.stat().st_mtime
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue  # not a session file, or one going away
        alive = [pid_alive(g["pid"]) for g in games]
        started = process_started(games[0]["pid"]) if alive[0] else None
        rows.append({"name": session.get("name", path.stem),
                     "state": "running" if all(alive) else "ended" if not session_running(session) else
                              "kept for the processes its game spawned",
                     "project": session.get("project", ""), "scene": session.get("scene", ""),
                     "pids": [g["pid"] for g in games], "displays": [g.get("display") for g in games],
                     "age_s": None if started is None else round(now - started),
                     "idle_s": round(idle), "out": session.get("out", "")})
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("No live sessions.")
    for row in rows:
        pids = ", ".join(map(str, row["pids"]))
        shown = "; ".join(describe_display({"display": d}) for d in row["displays"])
        print(f"{row['name']}: {row['state']}, pid {pids}, {shown}, up {describe_age(row['age_s'])}, last command "
              f"{describe_age(row['idle_s'])} ago, scene {row['scene'] or '?'}, project {row['project'] or '?'}")
    return 0


def strictly(func):
    """A command that, with --strict (or GDH_STRICT=1), fails when the game raised engine errors during it."""
    def run(args):
        raised.clear()
        code = func(args)
        if getattr(args, "strict", False) and raised:
            first = raised[0]
            count = sum(e.get("count", 1) for e in raised)
            raise LiveError(f"--strict: the game raised {count} engine error{'s' if count > 1 else ''} during the "
                            f"command, the first {first['type']}: {first['message']} at {first['where']}")
        return code
    return run


def add_display_option(parser):
    parser.add_argument("--display", choices=CHOICES, default=None,
                        help="gpu: a virtual display the GPU presents to (weston and Xwayland); xvfb: Xvfb, which "
                             "copies every frame through the CPU; auto: gpu if it starts, else xvfb with a note "
                             "(default: $GDH_DISPLAY, else auto)")


def add_input_options(p):
    """A step's input options: step's own, and the motion commands' that step (motion_cli.py)."""
    p.add_argument("--move", action="append", default=[], metavar="X,Y",
                   help="Move the pointer to screenshot pixel X,Y at the start, before any press (a drag, with a button held)")
    p.add_argument("--press", action="append", default=[], metavar="INPUT",
                   help="Press at the start and keep it pressed. INPUT is an action name, key:NAME (key:ctrl+s with "
                        "its modifiers), mouse:left, mouse:right or mouse:middle (at the pointer), or a gamepad's "
                        "joy:NAME (joy:a, joy:start, joy:dpad_up...)")
    p.add_argument("--release", action="append", default=[], metavar="INPUT", help="Release at the start")
    p.add_argument("--hold", action="append", default=[], metavar="INPUT",
                   help="Press at the start, release at the end")
    p.add_argument("--tap", action="append", default=[], metavar="INPUT", help="Press for one frame")
    p.add_argument("--type", metavar="TEXT", help="Type TEXT into whatever has the keyboard's focus, a character a frame from the step's start")
    p.add_argument("--click", action="append", default=[], metavar="X,Y", help="Left click at screenshot pixel X,Y")
    p.add_argument("--right-click", action="append", default=[], metavar="X,Y", help="Right click at screenshot pixel X,Y")
    p.add_argument("--left-hold", action="append", default=[], metavar="X,Y",
                   help="Press the left button at screenshot pixel X,Y at the start, release it at the end")
    p.add_argument("--right-hold", action="append", default=[], metavar="X,Y",
                   help="Press the right button at screenshot pixel X,Y at the start, release it at the end")
    p.add_argument("--click-text", action="append", default=[], metavar="TEXT",
                   help="Left click the node that shows TEXT (exactly, in any case; else the one whose text holds it), "
                        "at the centre of what shows of it. None, or several, fails, naming them")
    p.add_argument("--click-node", action="append", default=[], metavar="PATH",
                   help="Left click a node (a path from the current scene, or /root/...) at the centre of what shows of it")

    p.add_argument("--wheel", action="append", default=[], metavar="DIR[:N]",
                   help="Turn the mouse wheel up, down, left or right N notches (default 1) where the pointer is, a "
                        "notch a frame from the step's start")
    p.add_argument("--wheel-at", metavar="X,Y",
                   help="Move the pointer to screenshot pixel X,Y first, and turn the wheel there")
    p.add_argument("--mod", action="append", default=[], metavar="MODS",
                   help="Hold ctrl, shift, alt or meta (comma-separated) for the step: pressed at its start, released "
                        "at its end, and carried by its keys, clicks, wheel and pointer moves")
    p.add_argument("--axis", action="append", default=[], metavar="NAME=VALUE",
                   help="Put a gamepad axis at VALUE (-1 to 1) at the start, where it stays until another --axis moves "
                        "it: left_x, left_y, right_x, right_y, trigger_left or trigger_right")
    p.add_argument("--touch", action="append", default=[], metavar="X,Y",
                   help="Tap the touchscreen at screenshot pixel X,Y for one frame. Each --touch, then each "
                        "--touch-drag, is a finger of its own, numbered from 0")
    p.add_argument("--touch-drag", action="append", default=[], metavar="X,Y:X,Y",
                   help="Put a finger down at the first point at the start, move it evenly to the second over the "
                        "step, and lift it at the end")
    p.add_argument("--look", action="append", default=[], metavar="DX,DY",
                   help="Move the mouse by DX,DY screenshot pixels at the start: relative motion, for mouse-look (a "
                        "captured mouse stays at the window's centre). A negative DX needs =: --look=-40,0")


def add_parsers(sub):
    from gdh import blackbox
    live = sub.add_parser("live", help="Start a game off-screen and drive it step by step")
    commands = live.add_subparsers(dest="live_command", required=True)
    strict = os.environ.get("GDH_STRICT", "") not in ("", "0")

    def command(name, func, help, instance=None):
        p = commands.add_parser(name, help=help)
        p.add_argument("--session", default="default", help="Session name (default: default)")
        p.add_argument("--json", action="store_true", help="Print the raw reply")
        p.add_argument("--strict", action="store_true", default=strict,
                       help="Exit 1 when the game raised engine errors during the command (default: on with "
                            "GDH_STRICT=1)")
        if instance:
            p.add_argument("--instance", default="0", metavar="N", help=instance)
        p.set_defaults(func=blackbox.route(name, strictly(func)))  # a --binary session's commands go to blackbox.py
        return p

    one = "Which game instance: a number, or all (default 0)"

    p = command("start", cmd_start, "Launch the game, held at frame 0 (arguments after -- go to the game)")
    p.set_defaults(game_args=[])
    p.add_argument("--no-build", action="store_true", help="Don't build a C# project's assemblies first")
    p.add_argument("--no-import", action="store_true",
                   help="Don't import the project first when its import cache is missing or stale")
    p.add_argument("--project", help="Godot project directory (or --binary)")
    p.add_argument("--scene", help="res:// path (default: the project's main scene)")
    p.add_argument("--out", help="Output directory (default: ./captures/live/<session>)")
    p.add_argument("--resolution", default="1280x720")
    add_display_option(p)
    p.add_argument("--idle-timeout", type=int, default=1800,
                   help="Quit after this many seconds without a request (default 1800; 0: never). With "
                        "--keep-children, once the game has exited: stop what it spawned, and the session, after "
                        "this many seconds without a gdh live command on the session")
    p.add_argument("--timeout", type=int, default=60,
                   help="Seconds to wait for each companion, and then for the game, to be ready")
    p.add_argument("--instances", type=int, default=1, metavar="N",
                   help="Run N instances of the game, which step together (default 1)")
    p.add_argument("--companion", action="append", default=[], metavar="NAME=COMMAND",
                   help="Start a shell command beside the game and stop it with the session; repeatable. "
                        "{port} is a free port picked for it ($GDH_PORT too), and {NAME.port} fills it in "
                        "elsewhere, the game's arguments included")
    p.add_argument("--companion-ready", action="append", default=[], metavar="NAME=CHECK",
                   help="When a companion is ready: tcp (its port takes a connection; the default), "
                        "none, or an http(s) URL that answers 2xx")
    p.add_argument("--companion-port", action="append", default=[], metavar="NAME=PORT",
                   help="Give a companion this port instead of a free one")
    p.add_argument("--keep-children", action="store_true",
                   help="Keep the session, and its display, until every process the game spawned has ended too, "
                        "not just the game (a launcher that hands off to the game it starts), or, once the game "
                        "has exited, until --idle-timeout seconds pass without a gdh live command on the session. "
                        "Without it, spawned processes end with the session")
    p.add_argument("--gpu-passes", action="store_true",
                   help="Have the renderer time each of its passes, for `frames` (Godot's --gpu-profile; it also "
                        "prints a GPU profile to the log each second)")
    blackbox.add_start_options(p)
    p.add_argument("--seed", type=int, metavar="N",
                   help="Seed the game's global random number generator (randi, randf...) before its autoloads and "
                        "scene load (default: a seed gdh picks, which restart keeps)")
    p.add_argument("--replay", metavar="FILE.jsonl",
                   help="Replay an input log (a session's <out>/inputs.jsonl) once the game is ready, back to the "
                        "frame it ended at; its seed too, unless --seed gives one")
    p.add_argument("--timeline", action="store_true",
                   help="Keep a timeline of every command, in <out>/timeline/index.html: what it sent, the frames it "
                        "ran, its errors, notes and the game's output, and a thumbnail of the frame after it")
    p.add_argument("--locale", metavar="CODE",
                   help="Translate the game's text to this locale (fr, de_DE), or pseudo: every text 40%% longer, "
                        "with accents, so text that won't fit shows (the text_overflow probe)")
    p.add_argument("--net", action="append", default=[], metavar="NAME",
                   help="Put companion NAME behind gdh's network proxy: each instance's {NAME.port} is a port of its "
                        "own on the proxy, whose latency, loss and cuts `gdh live net` sets; repeatable")
    p.add_argument("--net-latency", type=float, default=0, metavar="MS", help="With --net: the latency each way")
    p.add_argument("--net-jitter", type=float, default=0, metavar="MS",
                   help="With --net: up to this much more latency, drawn for each packet")
    p.add_argument("--net-loss", type=float, default=0, metavar="PCT", help="With --net: the share of UDP datagrams dropped")
    p.add_argument("--recipe", metavar="FILE",
                   help="Run these command lines, written as for gdh live batch, once the game is ready (after "
                        "--replay); a line that fails stops the start, with exit 1, and leaves the session there")
    p.add_argument("--user-data", choices=["shared", "fresh"], default="shared",
                   help="fresh: user:// of the session's own, empty at each start, in <out>/user-data (its shader "
                        "caches stay shared). Default shared: gdh's user data, kept between runs")
    p.add_argument("--user-data-from", metavar="DIR",
                   help="user:// of the session's own, a copy of DIR at each start (saves, settings: a fixture)")
    p.add_argument("--rebuild", action="store_true",
                   help="Build a C# project's assemblies even if no code or project file has changed since the last "
                        "build")

    command("stop", cmd_stop, "Quit the game and its companions, and clean up")
    command("status", cmd_status, "Show frame, hold state and scene of each instance, and the companions")

    p = command("step", cmd_step, "Run every instance for a number of frames, then hold",
                instance="Which instance gets the input: a number, or all (default 0). Every instance steps")
    p.add_argument("frames", type=int, nargs="?", default=None,
                   help="How many frames (default 1); with --until, the most")
    add_input_options(p)
    p.add_argument("--shot-every", type=int, default=0, metavar="K", help="Save a frame every K frames")
    p.add_argument("--shot", action="store_true", help="Save a frame after stepping")
    p.add_argument("--until", metavar="EXPR",
                   help="Run until EXPR (a Godot expression, as eval takes) is truthy, checked after every --every "
                        "frames; the frame count, or --max, is the most it runs (default 3600). Exit 1 if it never is")
    p.add_argument("--max", type=int, metavar="N", help="With --until: run at most N frames")
    p.add_argument("--trace", action="append", default=[], metavar="EXPR",
                   help="Record EXPR's value after every --every frames; repeatable")
    p.add_argument("--every", type=int, metavar="K", help="Check --until and --trace every K frames (default 1)")
    p.add_argument("--trace-out", metavar="FILE.csv", help="Also write the --trace values here as CSV")
    p.add_argument("--trace-chart", metavar="FILE.png",
                   help="Also draw the --trace values as a line chart: a panel for each expression, a line for each "
                        "component of a vector, against game frames")
    p.add_argument("--trace-rates", action="store_true",
                   help="With --trace-chart: add each expression's rate of change per second and the rate of that "
                        "(velocity and acceleration for a position)")

    p.add_argument("--trail", action="append", default=[], metavar="PATH",
                   help="Draw where this node went over the step on a shot of its last frame, seen through that "
                        "frame's view, with a dot every --every frames; repeatable")
    p.add_argument("--trail-out", metavar="FILE.png",
                   help="Where --trail's image goes (default: the session's shots, as NNNN-trail.png)")
    p.add_argument("--monitors", action="store_true",
                   help="Godot's Performance monitors (objects, nodes, orphan nodes, draw calls, video memory...) "
                        "before and after the step, and their change")

    p = command("shot", cmd_shot, "Save the current frame", instance=one)
    p.add_argument("--view", action="append",
                   help="normal, unshaded, lighting, normals, wireframe or overdraw; repeatable")
    p.add_argument("--label", default="shot")
    p.add_argument("--tiles", action="store_true", help="Also save 2x2 tiles at 2x zoom")
    p.add_argument("--out", metavar="FILE.png",
                   help="Save to this file (with the view's name added when there are several), not a numbered one")
    p.add_argument("--crop", metavar="X,Y,W,H", help="Keep only this part of the screen (screenshot pixels)")
    p.add_argument("--node", metavar="PATH", help="Keep only this node's box on screen (as find reports it)")
    p.add_argument("--margin", type=float, default=0, metavar="PX", help="With --node: grow its box by PX on each side")
    p.add_argument("--zoom", type=int, default=1, metavar="K", help="Scale up K times, nearest neighbour (1 to 16)")
    p.add_argument("--max-width", type=int, default=0, metavar="W",
                   help="Scale down to at most W pixels wide, for reading the image (never up)")
    p.add_argument("--no-ui", action="store_true",
                   help="Leave the UI out of this shot: hide every CanvasLayer over the game (layer 1 and up) for it")
    p.add_argument("--annotate", nargs="?", const="names", metavar="LAYERS",
                   help="Also save each view annotated (NAME-annotated.png): names (the default; each node that "
                        "draws, boxed and numbered), collisions, nav, velocity, comma-separated, or all")
    p.add_argument("--filter", action="append", default=[], metavar="WHAT",
                   help="With --annotate: only what's under node PATH, or in group:NAME, or of class:NAME (a class "
                        "name labels those nodes, drawn or not); repeat to narrow")

    command("probes", cmd_probes, "Run the probes on the current frame", instance=one)

    p = command("tree", cmd_tree, "Show the scene tree", instance=one)
    p.add_argument("path", nargs="?", help="Node path relative to the current scene")
    p.add_argument("--depth", type=int, default=4)
    p.add_argument("--visible-only", action="store_true", help="Leave out hidden nodes and everything under them")

    p = command("find", cmd_find, "Find the nodes that show on screen by their text, name or class, with their boxes",
                instance=one)
    p.add_argument("text", nargs="?", help="Text the node shows (any case; part of it will do)")
    p.add_argument("--name", metavar="PATTERN", help="The node's name; * and ? match anything (any case)")
    p.add_argument("--class", dest="class_name", metavar="CLASS",
                   help="The node's class, built in or a script's class_name, or one it extends")

    p = command("snapshot", cmd_snapshot, "The UI on screen as a text outline: what shows a text or can be used, "
                                          "with its state; compare it with a baseline file", instance=one)
    p.add_argument("path", nargs="?", help="Only what's under this node (a path from the current scene, or /root/...)")
    p.add_argument("--boxes", action="store_true", help="Add each node's box on screen (@x,y wxh)")
    p.add_argument("--grid", type=int, default=1, metavar="PX",
                   help="Round the boxes to PX pixels, so a small shift doesn't count as a change (implies --boxes)")
    p.add_argument("--baseline", metavar="FILE",
                   help="Compare with this file: print the diff and exit 1 when the outline isn't the same")
    p.add_argument("--update-baseline", action="store_true", help="Write the outline to the --baseline FILE instead")

    p = command("net", cmd_net, "Make the network to the companions behind --net worse, or better, and show what "
                                "went through it", instance="Which game instance's link: a number, or all (the default)")
    p.set_defaults(instance="all")
    p.add_argument("--companion", metavar="NAME", help="Only the link to this companion (default: each one behind --net)")
    p.add_argument("--latency", type=float, metavar="MS", help="The latency each way")
    p.add_argument("--jitter", type=float, metavar="MS", help="Up to this much more latency, drawn for each packet")
    p.add_argument("--loss", type=float, metavar="PCT", help="The share of UDP datagrams dropped, each way")
    p.add_argument("--cut", action="store_true",
                   help="Cut the link: nothing goes through (TCP data is held until --heal, UDP dropped)")
    p.add_argument("--heal", action="store_true", help="Join a cut link again")
    p.add_argument("--reset", action="store_true", help="Close the TCP connections through the link at once (a reset)")

    p = command("camera", cmd_camera, "Look through a camera of gdh's own, without game code, or give the view back",
                instance=one)
    p.add_argument("--view", metavar="X,Y,Z:X,Y,Z",
                   help="3D: look from the first point at the second. 2D: X,Y, the point to centre on. Write "
                        "--view=-1,2,3:0,0,0 for a negative first number")
    p.add_argument("--fov", type=float, help="3D: the field of view in degrees (default: the game camera's)")
    p.add_argument("--far", type=float, help="3D: the far plane in metres (default: the game camera's)")
    p.add_argument("--zoom", type=float, help="2D: the zoom (2 is twice as close; default 1)")
    p.add_argument("--release", action="store_true", help="Free gdh's camera: the game's camera is current again")

    p = command("eval", cmd_eval, "Evaluate a Godot expression against the current scene", instance=one)
    p.add_argument("expr", help="e.g. \"get_node('Player').position\" (inputs: scene, tree, root)")

    command("run", cmd_run, "Let every instance run in real time")
    command("pause", cmd_pause, "Hold every instance")
    command("pipe", cmd_pipe, "Take requests as JSON lines on stdin and answer each on stdout (for scripts)")
    p = command("batch", cmd_batch, "Run command lines from stdin, written as on the command line (\"step 30 --hold "
                                    "ui_right\"), in one process, printing each one's output")
    p.add_argument("--stop-on-error", action="store_true", help="Stop at the first command that fails")
    command("list", cmd_list, "List every live session: project, scene, pids, displays, age")

    from gdh import measure_cli, motion_cli
    measure_cli.add_live_parsers(commands, command)
    motion_cli.add_live_parsers(commands, command)
    from gdh import perf
    perf.add_live_parsers(commands, command)
    blackbox.add_live_parsers(commands, command)
    restart.add_live_parsers(commands, command)

    from gdh import scenario
    scenario.add_live_parsers(commands, command)
