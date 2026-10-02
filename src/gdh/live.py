"""gdh live: start a game off-screen and drive it through harness/bridge.gd.

Each command connects to the running game, sends one request and prints the
reply. The game is held between commands, so no game time passes unless a
command steps it.

A session can also start companion processes beside the game (a server, say:
companions.py), and run several instances of the game, which step together.

Whatever a game spawns (a launcher's game, a tool) carries the game's GDH_MARK in its environment (spawned.py):
status lists those processes, and they end with the session. With --keep-children the session, and its display,
last until they have ended too.
"""
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gdh import companions, spawned
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
    if cmd in EVERY_INSTANCE:
        sends = [(i, args if i in chosen or cmd != "step" else {**args, "events": []}) for i in range(len(games))]
    else:
        sends = [(i, args) for i in chosen]
    if len(sends) == 1:
        replies = [request(games[sends[0][0]], cmd, sends[0][1], timeout)]
    else:
        with ThreadPoolExecutor(len(sends)) as pool:
            replies = list(pool.map(lambda s: request(games[s[0]], cmd, s[1], timeout), sends))
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

def report(reply, as_json, echo=True):
    """Print a reply's notes and errors, or the whole reply as JSON. Returns
    the result (a list of results for a reply from several instances), or
    raises on failure. echo=False defers JSON printing to the caller."""
    if as_json:
        if echo:
            print(json.dumps(reply, indent=2))
    else:
        for part in reply.get("instances", [reply]):
            prefix = f"[{part['instance']}] " if "instance" in part else ""
            for note in part.get("notes", []):
                print(f"{prefix}note: {note}")
            for e in part.get("errors", []):
                count = f" (x{e['count']})" if e.get("count", 1) > 1 else ""
                print(f"{prefix}{e['type']}: {e['message']}{count} at {e['where']}")
            missing = missing_resources(part.get("errors", []))
            if missing:
                print(f"{prefix}{describe_missing(missing)}")
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
    game_args = [companions.expand(a, ports, instance=index) for a in args.game_args]
    # --fixed-fps matching the tick rate makes every frame exactly one physics tick.
    # --gpu-profile makes the renderer capture a timestamp at each pass, which `frames` reads (harness/frames.gd).
    profile = ["--gpu-profile"] if getattr(args, "gpu_passes", False) else []
    cmd = godot_cmd(project, args.resolution, [*profile, "--fixed-fps", str(ticks), "--script", str(LIVE_SCRIPT)], game_args)
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "godot.log"
    display = open_display(args.display, args.resolution, out / "display.log")
    user_args += ["--display", display.kind]
    # The token goes in the environment, which only this user can read. The
    # command line is visible to everyone in the process list.
    env = godot_env(shims, display.name, user_args)
    env["GDH_TOKEN"] = token
    env[spawned.VAR] = mark  # everything the game spawns inherits it (spawned.py)
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
    prefix = label.strip() + " " if label else ""
    for e in info.get("errors", []):
        print(f"{prefix}{e['type']}: {e['message']} at {e['where']}")
    missing = missing_resources(info.get("errors", []))
    if missing:
        print(f"{prefix}{describe_missing(missing)}")
    if not info.get("port"):
        raise LiveError(info.get("error") or "The game didn't open a port.")
    mismatch = size_mismatch(info.get("status", {}).get("window_size"), resolution)
    if mismatch:
        raise LiveError(f"Not started{label}: {mismatch}")
    record["port"] = info["port"]
    return info


def start_watchdog(watch, groups, remove, marks, keep_children):
    """A detached process that stops the session's other processes once any game ends, or with keep_children once
    every game and every process they spawned has ended (watchdog.py)."""
    proc = subprocess.Popen([sys.executable, "-m", "gdh.watchdog", "--watch", *map(str, watch),
                             "--groups", *map(str, groups), "--remove", *remove, "--marks", *marks,
                             *(["--keep-children"] if keep_children else [])],
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
    project = Path(args.project).resolve()
    parse_resolution(args.resolution)
    if not args.no_build:
        build_csharp(project)
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
        deadline = time.monotonic() + args.timeout
        for index in range(args.instances):
            instance_out = out if args.instances == 1 else out / f"instance-{index}"
            record, proc, ready = start_instance(project, args, index, args.instances, instance_out, shims, ports, ticks)
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
                              [r["mark"] for r in games], args.keep_children)
    first = games[0]
    session.update({"pid": first["pid"], "port": first["port"], "token": first["token"], "log": first["log"],
                    "out": str(out), "project": str(project), "instances": games, "watchdog": watchdog,
                    "keep_children": args.keep_children,
                    "groups": [*started_groups, watchdog]})
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
    return 0


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
    print(f"Stopped session '{args.session}'.")
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


def input_event(token, pressed, at):
    if token.startswith("key:"):
        return {"key": token[4:], "pressed": pressed, "at": at}
    if token.startswith("mouse:"):
        name = token[6:]
        if name not in MOUSE_BUTTONS:
            raise SystemExit(f"gdh: unknown mouse button {name!r} (left, right or middle)")
        # Where the pointer is: --move puts it there first.
        return {"mouse_button": MOUSE_BUTTONS[name], "pressed": pressed, "at": at}
    return {"action": token, "pressed": pressed, "at": at}


def cmd_step(args):
    session = load_session(args.session)
    n = args.frames
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
    step_args = {"frames": n, "events": events, "shot_every": args.shot_every}
    # Generous: a big window that saves a frame every step can take seconds a frame under Xvfb.
    reply = call(session, "step", step_args, instance=args.instance, timeout=max(300, 2 * n))
    result = report(reply, args.json)
    if not args.json:
        for prefix, r in each(reply, result):
            print(f"{prefix}stepped {r['frames']} frames")
            for path in r.get("shots", []):
                print(f"{prefix}  shot: {path}")
            print(describe_status(r["status"], prefix))
    if args.shot:
        shot(session, ["normal"], "after-step", False, args.json, args.instance)
    return 0


def shot(session, views, label, tiles, as_json, instance=0):
    reply = call(session, "shot", {"views": views, "label": label}, instance=instance)
    result = report(reply, as_json)
    for prefix, r in each(reply, result):
        for view, path in r["shots"].items():
            if not as_json:
                print(f"{prefix}{view}: {path}")
            if tiles and view == "normal":
                for tile in save_tiles(path, Path(path).with_suffix("")):
                    if not as_json:
                        print(f"{prefix}  tile: {tile}")
    return result


def cmd_shot(args):
    session = load_session(args.session)
    shot(session, args.view or ["normal"], args.label, args.tiles, args.json, args.instance)
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
    reply = call(session, "tree", {"path": args.path or "", "depth": args.depth}, instance=args.instance)
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
                         timeout=req.get("timeout", 300))
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


def session_alive(session):
    return all(pid_alive(g["pid"]) for g in instances(session))


def add_display_option(parser):
    parser.add_argument("--display", choices=CHOICES, default=None,
                        help="gpu: a virtual display the GPU presents to (weston and Xwayland); xvfb: Xvfb, which "
                             "copies every frame through the CPU; auto: gpu if it starts, else xvfb with a note "
                             "(default: $GDH_DISPLAY, else auto)")


def add_parsers(sub):
    live = sub.add_parser("live", help="Start a game off-screen and drive it step by step")
    commands = live.add_subparsers(dest="live_command", required=True)

    def command(name, func, help, instance=None):
        p = commands.add_parser(name, help=help)
        p.add_argument("--session", default="default", help="Session name (default: default)")
        p.add_argument("--json", action="store_true", help="Print the raw reply")
        if instance:
            p.add_argument("--instance", default="0", metavar="N", help=instance)
        p.set_defaults(func=func)
        return p

    one = "Which game instance: a number, or all (default 0)"

    p = command("start", cmd_start, "Launch the game, held at frame 0 (arguments after -- go to the game)")
    p.set_defaults(game_args=[])
    p.add_argument("--no-build", action="store_true", help="Don't build a C# project's assemblies first")
    p.add_argument("--no-import", action="store_true",
                   help="Don't import the project first when its import cache is missing or stale")
    p.add_argument("--project", required=True, help="Godot project directory")
    p.add_argument("--scene", help="res:// path (default: the project's main scene)")
    p.add_argument("--out", help="Output directory (default: ./captures/live/<session>)")
    p.add_argument("--resolution", default="1280x720")
    add_display_option(p)
    p.add_argument("--idle-timeout", type=int, default=1800,
                   help="Quit after this many seconds without a request (default 1800)")
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
                        "not just the game (a launcher that hands off to the game it starts). Without it, spawned "
                        "processes end with the session")
    p.add_argument("--gpu-passes", action="store_true",
                   help="Have the renderer time each of its passes, for `frames` (Godot's --gpu-profile; it also "
                        "prints a GPU profile to the log each second)")

    command("stop", cmd_stop, "Quit the game and its companions, and clean up")
    command("status", cmd_status, "Show frame, hold state and scene of each instance, and the companions")

    p = command("step", cmd_step, "Run every instance for a number of frames, then hold",
                instance="Which instance gets the input: a number, or all (default 0). Every instance steps")
    p.add_argument("frames", type=int, nargs="?", default=1)
    p.add_argument("--move", action="append", default=[], metavar="X,Y",
                   help="Move the pointer to screenshot pixel X,Y at the start, before any press (a drag, with a button held)")
    p.add_argument("--press", action="append", default=[], metavar="INPUT",
                   help="Press at the start and keep it pressed. INPUT is an action name, key:NAME, or mouse:left, "
                        "mouse:right or mouse:middle (at the pointer)")
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
    p.add_argument("--shot-every", type=int, default=0, metavar="K", help="Save a frame every K frames")
    p.add_argument("--shot", action="store_true", help="Save a frame after stepping")

    p = command("shot", cmd_shot, "Save the current frame", instance=one)
    p.add_argument("--view", action="append",
                   help="normal, unshaded, lighting, normals, wireframe or overdraw; repeatable")
    p.add_argument("--label", default="shot")
    p.add_argument("--tiles", action="store_true", help="Also save 2x2 tiles at 2x zoom")

    command("probes", cmd_probes, "Run the probes on the current frame", instance=one)

    p = command("tree", cmd_tree, "Show the scene tree", instance=one)
    p.add_argument("path", nargs="?", help="Node path relative to the current scene")
    p.add_argument("--depth", type=int, default=4)

    p = command("eval", cmd_eval, "Evaluate a Godot expression against the current scene", instance=one)
    p.add_argument("expr", help="e.g. \"get_node('Player').position\" (inputs: scene, tree, root)")

    command("run", cmd_run, "Let every instance run in real time")
    command("pause", cmd_pause, "Hold every instance")
    command("pipe", cmd_pipe, "Take requests as JSON lines on stdin and answer each on stdout (for scripts)")

    from gdh import measure_cli
    measure_cli.add_live_parsers(commands, command)
