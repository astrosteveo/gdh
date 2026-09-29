"""gdh live: start a game off-screen and drive it through harness/bridge.gd.

Each command connects to the running game, sends one request and prints the
reply. The game is held between commands, so no game time passes unless a
command steps it.
"""
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

from gdh.godot import HARNESS, godot_cmd, godot_env, project_ticks, write_alert_shims
from gdh.images import crop_findings, save_tiles

LIVE_SCRIPT = HARNESS / "live.gd"
SESSION_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or Path.home() / ".cache") / "gdh"


class LiveError(Exception):
    pass


# --- Sessions -----------------------------------------------------------------

def session_path(name):
    return SESSION_DIR / f"{name}.json"


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def log_tail(path, lines=15):
    try:
        return "\n".join(Path(path).read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return "(no log)"


def remove_session(session):
    session_path(session["name"]).unlink(missing_ok=True)
    shutil.rmtree(session.get("shims", ""), ignore_errors=True)


def load_session(name):
    path = session_path(name)
    if not path.exists():
        raise LiveError(f"No live session '{name}'. Start one with: gdh live start --project <dir>")
    session = json.loads(path.read_text())
    if not pid_alive(session["pid"]):
        remove_session(session)
        raise LiveError(f"Session '{name}' has ended. Last lines of {session['log']}:\n{log_tail(session['log'])}")
    return session


def kill_group(pid):
    """Stop the whole process group: xvfb-run, Xvfb and Godot."""
    for sig, wait in ((signal.SIGTERM, 3.0), (signal.SIGKILL, 1.0)):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and pid_alive(pid):
            time.sleep(0.1)


def request(session, cmd, args=None, timeout=300):
    try:
        sock = socket.create_connection(("127.0.0.1", session["port"]), timeout=10)
    except OSError as e:
        raise LiveError(f"Can't reach the game on port {session['port']}: {e}") from e
    with sock:
        sock.settimeout(timeout)
        message = {"id": 1, "token": session["token"], "cmd": cmd, "args": args or {}}
        sock.sendall((json.dumps(message) + "\n").encode())
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(1 << 16)
            if not chunk:
                raise LiveError("The game closed the connection.")
            data += chunk
    return json.loads(data.split(b"\n", 1)[0])


# --- Output -------------------------------------------------------------------

def report(reply, as_json, echo=True):
    """Print a reply's notes and errors, or the whole reply as JSON. Returns
    the result, or raises on failure. echo=False defers JSON printing to the caller."""
    if as_json:
        if echo:
            print(json.dumps(reply, indent=2))
    else:
        for note in reply.get("notes", []):
            print(f"note: {note}")
        for e in reply.get("errors", []):
            count = f" (x{e['count']})" if e.get("count", 1) > 1 else ""
            print(f"{e['type']}: {e['message']}{count} at {e['where']}")
    if not reply.get("ok"):
        raise LiveError(reply.get("error", "Request failed."))
    return reply.get("result", {})


def describe_status(status):
    seconds = status["frame"] / max(status.get("ticks_per_second", 60), 1)
    state = "held" if status["held"] else "running"
    lines = [f"{state} at frame {status['frame']} ({seconds:.2f} s), scene {status['scene']}"]
    for node in status.get("runs_while_held", []):
        lines.append(f"  runs while held ({node['mode']}): {node['node']}")
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


# --- Commands -----------------------------------------------------------------

def cmd_start(args):
    name = args.session
    existing = session_path(name)
    if existing.exists():
        old = json.loads(existing.read_text())
        if pid_alive(old["pid"]):
            raise LiveError(f"Session '{name}' is already running. Stop it with: gdh live stop --session {name}")
        remove_session(old)
    project = Path(args.project).resolve()
    out = Path(args.out or Path.cwd() / "captures" / "live" / name).resolve()
    out.mkdir(parents=True, exist_ok=True)
    SESSION_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    # The game can raise alerts at any time, so the stand-ins live as long as the session.
    shims = write_alert_shims(SESSION_DIR / f"{name}-shims")
    ready = SESSION_DIR / f"{name}-ready.json"
    ready.unlink(missing_ok=True)
    token = secrets.token_hex(16)
    ticks = project_ticks(project)
    user_args = ["--ready-file", str(ready), "--out", str(out),
                 "--idle-timeout", str(args.idle_timeout), "--ticks", str(ticks)]
    if args.scene:
        user_args += ["--scene", args.scene]
    # --fixed-fps matching the tick rate makes every frame exactly one physics tick.
    cmd = godot_cmd(project, args.resolution,
                    ["--fixed-fps", str(ticks), "--script", str(LIVE_SCRIPT), "--", *user_args])
    log_path = out / "godot.log"
    # The token goes in the environment, which only this user can read. The
    # command line is visible to everyone in the process list.
    env = godot_env(shims)
    env["GDH_TOKEN"] = token
    with open(log_path, "w") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=env, start_new_session=True)
    session = {"name": name, "pid": proc.pid, "port": 0, "token": token, "project": str(project),
               "out": str(out), "log": str(log_path), "shims": str(shims)}
    deadline = time.monotonic() + args.timeout
    while not ready.exists():
        if proc.poll() is not None:
            remove_session(session)
            raise LiveError(f"Godot exited with code {proc.returncode} before the game was ready. "
                            f"Last lines of {log_path}:\n{log_tail(log_path)}")
        if time.monotonic() > deadline:
            kill_group(proc.pid)
            remove_session(session)
            raise LiveError(f"The game wasn't ready after {args.timeout} s. Last lines of {log_path}:\n{log_tail(log_path)}")
        time.sleep(0.2)
    info = json.loads(ready.read_text())
    ready.unlink()
    for e in info.get("errors", []):
        print(f"{e['type']}: {e['message']} at {e['where']}")
    if not info.get("port"):
        kill_group(proc.pid)
        remove_session(session)
        raise LiveError(info.get("error") or "The game didn't open a port.")
    session["port"] = info["port"]
    fd = os.open(session_path(name), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(session, f)
    print(describe_status(info["status"]))
    print(f"session '{name}': pid {proc.pid}, output in {out}")
    return 0


def cmd_stop(args):
    path = session_path(args.session)
    if not path.exists():
        print(f"No live session '{args.session}'.")
        return 0
    session = json.loads(path.read_text())
    if pid_alive(session["pid"]):
        try:
            request(session, "quit", timeout=5)
        except (LiveError, OSError):
            pass
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and pid_alive(session["pid"]):
            time.sleep(0.2)
        if pid_alive(session["pid"]):
            kill_group(session["pid"])
    remove_session(session)
    print(f"Stopped session '{args.session}'.")
    return 0


def cmd_status(args):
    session = load_session(args.session)
    status = report(request(session, "status"), args.json)
    if not args.json:
        print(describe_status(status))
    return 0


def input_event(token, pressed, at):
    if token.startswith("key:"):
        return {"key": token[4:], "pressed": pressed, "at": at}
    return {"action": token, "pressed": pressed, "at": at}


def cmd_step(args):
    session = load_session(args.session)
    n = args.frames
    events = []
    for token in args.press:
        events.append(input_event(token, True, 0))
    for token in args.release:
        events.append(input_event(token, False, 0))
    for token in args.hold:
        events += [input_event(token, True, 0), input_event(token, False, n)]
    for token in args.tap:
        events += [input_event(token, True, 0), input_event(token, False, 1)]
    for point in args.click:
        x, y = (float(v) for v in point.split(","))
        events += [{"mouse_motion": [x, y], "at": 0},
                   {"mouse_button": 1, "position": [x, y], "pressed": True, "at": 0},
                   {"mouse_button": 1, "position": [x, y], "pressed": False, "at": 1}]
    step_args = {"frames": n, "events": events, "shot_every": args.shot_every}
    result = report(request(session, "step", step_args, timeout=max(60, n)), args.json)
    if not args.json:
        print(f"stepped {result['frames']} frames")
        for path in result.get("shots", []):
            print(f"  shot: {path}")
        print(describe_status(result["status"]))
    if args.shot:
        shot(session, ["normal"], "after-step", False, args.json)
    return 0


def shot(session, views, label, tiles, as_json):
    result = report(request(session, "shot", {"views": views, "label": label}), as_json)
    for view, path in result["shots"].items():
        if not as_json:
            print(f"{view}: {path}")
        if tiles and view == "normal":
            for tile in save_tiles(path, Path(path).with_suffix("")):
                if not as_json:
                    print(f"  tile: {tile}")
    return result


def cmd_shot(args):
    session = load_session(args.session)
    shot(session, args.view or ["normal"], args.label, args.tiles, args.json)
    return 0


def cmd_probes(args):
    session = load_session(args.session)
    reply = request(session, "probes")
    result = report(reply, args.json, echo=False)
    findings = result["findings"]
    normal = Path(result["shots"]["normal"])
    crop_findings(findings, result["image_size"], result["shots"],
                  normal.parent / normal.name.replace("-normal.png", "-crops"))
    if args.json:
        print(json.dumps(reply, indent=2))
        return 0
    print(f"{len(findings)} findings, frame shot: {normal}")
    for f in findings:
        crop = f"  [{f['crop']}]" if f.get("crop") else ""
        print(f"  {f['severity']}: {f['probe']} {f['node']}: {f['message']}{crop}")
    return 0


def cmd_tree(args):
    session = load_session(args.session)
    result = report(request(session, "tree", {"path": args.path or "", "depth": args.depth}), args.json)
    if not args.json:
        print_tree(result["tree"])
        if result.get("cut_nodes"):
            print(f"({result['cut_nodes']} nodes not shown; narrow with a path or --depth)")
    return 0


def cmd_eval(args):
    session = load_session(args.session)
    result = report(request(session, "eval", {"expr": args.expr}), args.json)
    if not args.json:
        print(json.dumps(result["value"]))
    return 0


def cmd_run(args):
    session = load_session(args.session)
    status = report(request(session, "run"), args.json)
    if not args.json:
        print(describe_status(status))
    return 0


def cmd_pause(args):
    session = load_session(args.session)
    status = report(request(session, "pause"), args.json)
    if not args.json:
        print(describe_status(status))
    return 0


def add_parsers(sub):
    live = sub.add_parser("live", help="Start a game off-screen and drive it step by step")
    commands = live.add_subparsers(dest="live_command", required=True)

    def command(name, func, help):
        p = commands.add_parser(name, help=help)
        p.add_argument("--session", default="default", help="Session name (default: default)")
        p.add_argument("--json", action="store_true", help="Print the raw reply")
        p.set_defaults(func=func)
        return p

    p = command("start", cmd_start, "Launch the game, held at frame 0")
    p.add_argument("--project", required=True, help="Godot project directory")
    p.add_argument("--scene", help="res:// path (default: the project's main scene)")
    p.add_argument("--out", help="Output directory (default: ./captures/live/<session>)")
    p.add_argument("--resolution", default="1280x720")
    p.add_argument("--idle-timeout", type=int, default=1800,
                   help="Quit after this many seconds without a request (default 1800)")
    p.add_argument("--timeout", type=int, default=60, help="Seconds to wait for the game to load")

    command("stop", cmd_stop, "Quit the game and clean up")
    command("status", cmd_status, "Show frame, hold state and scene")

    p = command("step", cmd_step, "Run the game for a number of frames, then hold")
    p.add_argument("frames", type=int, nargs="?", default=1)
    p.add_argument("--press", action="append", default=[], metavar="INPUT",
                   help="Press at the start and keep it pressed. INPUT is an action name or key:NAME")
    p.add_argument("--release", action="append", default=[], metavar="INPUT", help="Release at the start")
    p.add_argument("--hold", action="append", default=[], metavar="INPUT",
                   help="Press at the start, release at the end")
    p.add_argument("--tap", action="append", default=[], metavar="INPUT", help="Press for one frame")
    p.add_argument("--click", action="append", default=[], metavar="X,Y", help="Left click at screenshot pixel X,Y")
    p.add_argument("--shot-every", type=int, default=0, metavar="K", help="Save a frame every K frames")
    p.add_argument("--shot", action="store_true", help="Save a frame after stepping")

    p = command("shot", cmd_shot, "Save the current frame")
    p.add_argument("--view", action="append",
                   help="normal, unshaded, lighting, normals, wireframe or overdraw; repeatable")
    p.add_argument("--label", default="shot")
    p.add_argument("--tiles", action="store_true", help="Also save 2x2 tiles at 2x zoom")

    command("probes", cmd_probes, "Run the probes on the current frame")

    p = command("tree", cmd_tree, "Show the scene tree")
    p.add_argument("path", nargs="?", help="Node path relative to the current scene")
    p.add_argument("--depth", type=int, default=4)

    p = command("eval", cmd_eval, "Evaluate a Godot expression against the current scene")
    p.add_argument("expr", help="e.g. \"get_node('Player').position\" (inputs: scene, tree, root)")

    command("run", cmd_run, "Let the game run in real time")
    command("pause", cmd_pause, "Hold the game")
