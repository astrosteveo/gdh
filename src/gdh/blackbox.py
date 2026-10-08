"""--binary sessions: any program on a display of gdh's own, driven from outside, with no harness in it.

gdh live start --project runs a project on Godot's editor binary with gdh's harness inside. A --binary session runs a
program as it is (an exported game, a launcher, any X program) on the same kind of display, with the same alert
stand-ins, user data directory, watchdog and cleanup. gdh sees and drives it as a person would: shot reads the
display's screen, input sends the pointer and keys through XTest (x11.py), wait watches the clock, the program's log,
its windows or its exit, and status says whether it runs, how it ended and what it spawned. Each command prints the
engine errors the program's log gained since the one before. Commands that need the harness (step, tree, eval, probes
and the rest) say so.

An exported Godot game (its pack embedded, or beside it as <name>.pck) gets Godot's off-screen options first: the X11
driver, the silent audio driver and the --resolution; the arguments after -- then go after Godot's own --, where
OS.get_cmdline_user_args() returns exactly them. --raw passes them as they are, with nothing added, as for any other
program.

The program runs under runner.py, its parent, which records how it ended and keeps the idle timeout. A black box can do
anything, so it's kept further off the user's session than a project is: its XDG_RUNTIME_DIR is an empty directory of
the session's (no Wayland, D-Bus, PulseAudio or PipeWire socket to find), its D-Bus address names a socket that isn't
there, and xdg-open and its kin are stand-ins that log what would have opened. Vulkan overlays (MangoHud, vkBasalt)
are turned off, since the screen would show them.

gdh export --smoke runs a build the same way for a number of seconds, and fails on a crash, an early exit or engine
errors in its log (smoke()).
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from gdh import companions, live, spawned, x11
from gdh.display import open_display, parse_resolution
from gdh.editor_bridge import parse_engine_errors
from gdh.godot import godot_env, kill_groups, pid_alive, write_alert_shims
from gdh.images import save_tiles
from gdh.imports import describe_missing, missing_resources
from gdh.live import LiveError, session_path

# Programs that open a URL or a file in the user's browser or file manager, on their desktop.
OPENERS = ("xdg-open", "kde-open", "kde-open5", "gnome-open")
OPENER_SHIM = """#!/bin/sh
# Stands in for a program that opens a URL or file on the user's desktop: what a --binary session's program opens
# lands in its log instead.
echo "gdh: $(basename "$0") $*" >&2
exit 0
"""
# Signals that mean the program crashed, rather than was stopped.
CRASH_SIGNALS = {signal.SIGSEGV, signal.SIGABRT, signal.SIGBUS, signal.SIGILL, signal.SIGFPE, signal.SIGTRAP}
# What a --binary session takes, besides start; anything else needs the harness (PASS goes to live as it is).
COMMANDS = ("stop", "status", "shot", "input", "wait")
PASS = ("list",)


# --- The program and its environment ---------------------------------------------------------------------------------

def find_binary(name):
    """The program's absolute path: a path, or a name on PATH."""
    path = Path(name) if "/" in name else Path(shutil.which(name) or name)
    if not path.is_file():
        raise LiveError(f"No program {name!r}: --binary takes a path to an executable, or a name on PATH.")
    if not os.access(path, os.X_OK):
        raise LiveError(f"{path} isn't executable (chmod +x it).")
    return path.resolve()


def export_pack(binary):
    """The pack an exported Godot game runs from: its own file when the pack is embedded (it ends with Godot's GDPC
    magic), or <name>.pck beside it, as Godot looks for it. None for any other program."""
    binary = Path(binary)
    try:
        with open(binary, "rb") as f:
            f.seek(-4, os.SEEK_END)
            if f.read(4) == b"GDPC":
                return binary
    except OSError:
        pass
    beside = binary.with_suffix(".pck")
    return beside if beside.is_file() else None


def command(binary, game_args, resolution, pack):
    """The command line: an export gets Godot's off-screen options first, and its arguments after Godot's --."""
    if pack is None:
        return [str(binary), *game_args]
    gpu = ["--gpu-index", os.environ["GDH_GPU_INDEX"]] if "GDH_GPU_INDEX" in os.environ else []
    return [str(binary), "--display-driver", "x11", "--audio-driver", "Dummy", "--resolution", resolution, *gpu,
            *(["--", *game_args] if game_args else [])]


def write_shims(directory):
    """The alert stand-ins (godot.py), the openers' stand-ins, and an empty runtime directory, in directory."""
    shims = write_alert_shims(directory)
    for name in OPENERS:
        path = shims / name
        path.write_text(OPENER_SHIM)
        path.chmod(0o755)
    runtime = shims / "runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    return shims


def program_env(shims, display):
    """godot_env's isolation (the display, alert stand-ins, gdh's user data), and none of the user's session."""
    env = godot_env(shims, display)
    env.pop("GDH_ARGS", None)
    runtime = Path(shims) / "runtime"
    env["XDG_RUNTIME_DIR"] = str(runtime)
    env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime / 'bus'}"
    for name in ("PULSE_SERVER", "PIPEWIRE_REMOTE", "SESSION_MANAGER", "XAUTHORITY", "MANGOHUD", "ENABLE_VKBASALT"):
        env.pop(name, None)
    # Vulkan overlays draw into the program's frames, which the screen then shows: keep only the program's own drawing.
    env["DISABLE_MANGOHUD"] = env["DISABLE_VKBASALT"] = "1"
    return env


def run(cmd, env, log, exit_file, mark, idle_timeout=0, session_file=None):
    """Start the program under runner.py. Returns (the runner's Popen, the program's pid)."""
    Path(exit_file).unlink(missing_ok=True)
    runner = subprocess.Popen([sys.executable, "-m", "gdh.runner", "--log", str(log), "--exit-file", str(exit_file),
                               "--mark", mark, "--idle-timeout", str(idle_timeout),
                               *(["--session-file", str(session_file)] if session_file else []), "--", *cmd],
                              env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              start_new_session=True)
    line = runner.stdout.readline()
    runner.stdout.close()
    try:
        reply = json.loads(line)
    except ValueError:
        reply = {"error": "gdh's runner didn't start."}
    if "error" in reply:
        runner.wait()
        raise LiveError(reply["error"])
    return runner, reply["pid"]


def stop_run(pid, runner, display, mark):
    """Stop a program gdh started in this command, what it spawned, its runner and its display. gdh is the runner's
    and the display's parent here, so it waits for each to exit (they do once the program has: the display when its
    last client leaves) rather than leave them to linger, unreaped, as process groups that are still there."""
    if pid:
        kill_groups(pid)
        spawned.stop([p["pid"] for p in spawned.marked(mark)])
    for proc in (runner, display.proc if display else None):
        if proc is None:
            continue
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    if runner and runner.poll() is None:
        runner.kill()
        runner.wait()
    if display:
        display.stop()


def read_exit(exit_file, wait=0.0):
    """How the program ended ({"code", "seconds", "idle"}), or None while that's unknown."""
    deadline = time.monotonic() + wait
    while True:
        try:
            return json.loads(Path(exit_file).read_text())
        except (OSError, ValueError):
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.05)


def describe_exit(ended, log):
    """How the program ended, in a sentence: its code, or the signal that ended it."""
    if ended is None:
        return "has ended (how isn't known: gdh's runner was stopped)"
    code = ended["code"]
    after = f" after {ended['seconds']:.1f} s"
    if ended.get("idle"):
        return f"was stopped{after} without a gdh live command (--idle-timeout)"
    if code >= 0:
        return f"exited with code {code}{after}"
    try:
        sig = signal.Signals(-code)
    except ValueError:
        return f"was ended by signal {-code}{after}"
    crashed = sig in CRASH_SIGNALS or "Program crashed with signal" in tail_text(log, 200)
    return f"{'crashed' if crashed else 'was ended'} with signal {-code} ({sig.name}){after}"


def tail_text(path, lines=15):
    return live.log_tail(path, lines)


def merge(errors):
    """Engine errors with repeats merged: [{type, message, where, count}]."""
    merged = {}
    for e in errors:
        key = (e["type"], e["message"], e["where"])
        merged.setdefault(key, {**e, "count": 0})["count"] += 1
    return list(merged.values())


def describe_errors(errors, prefix=""):
    lines = []
    for e in errors:
        count = f" (x{e['count']})" if e.get("count", 1) > 1 else ""
        lines.append(f"{prefix}{e['type']}: {e['message']}{count} at {e['where']}")
    missing = missing_resources(errors)
    if missing:
        lines.append(f"{prefix}{describe_missing(missing)}")
    return "\n".join(lines)


def describe_window(w):
    title = f" {json.dumps(w['title'])}" if w["title"] else ""
    return f"window {w['width']}x{w['height']} at {w['x']},{w['y']}{title}{' (popup)' if w['popup'] else ''}"


def wait_window(display, pid, deadline, exit_file, log, mark=None):
    """The first window the program shows, or with mark (--keep-children) a process it spawned shows. Raises if they
    end first, or none shows before the deadline."""
    with x11.Display(display) as d:
        while True:
            shown = [w for w in d.windows() if not w["popup"]] or d.windows()
            if shown:
                return shown[-1]
            if not pid_alive(pid) and not (mark and spawned.marked(mark)):
                raise LiveError(f"The program {describe_exit(read_exit(exit_file, 2), log)} before it showed a window. "
                                f"Last lines of {log}:\n{tail_text(log)}")
            if time.monotonic() > deadline:
                raise LiveError(f"The program showed no window in time (--timeout; --no-wait for a program that "
                                f"shows none). Last lines of {log}:\n{tail_text(log)}")
            time.sleep(0.1)


# --- Sessions ------------------------------------------------------------------------------------------------------

def read_session(name):
    path = session_path(name)
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def save_session(session):
    fd = os.open(session_path(session["name"]), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(session, f)


def touch(session):
    """A command on the session: the idle timeout counts from this."""
    try:
        os.utime(session_path(session["name"]))
    except OSError:
        pass


def running(session):
    """Whether the program runs, or, with --keep-children, any process it spawned."""
    return live.session_running(session)


def ended_error(session):
    """The error for a command that needs the program running, once it has ended: how, and the end of its log."""
    ended = read_exit(session["exit_file"], 1)
    return LiveError(f"The program of session '{session['name']}' {describe_exit(ended, session['log'])}. "
                     f"Last lines of {session['log']}:\n{tail_text(session['log'])}")


def require_running(session):
    if not running(session):
        live.stop_all(session)
        raise ended_error(session)


def new_errors(session):
    """Engine errors the log gained since the last command, merged; moves the session's mark past them."""
    try:
        with open(session["log"], "rb") as f:
            f.seek(session.get("errors_at", 0))
            data = f.read()
    except OSError:
        return []
    # Only whole lines, and an error's "at:" line with it: stop before a last line that may still be written.
    cut = data.rfind(b"\n")
    if cut < 0:
        return []
    lines = data[:cut + 1].decode(errors="replace").splitlines(keepends=True)
    if lines and re.match(r"\s*(SCRIPT ERROR|ERROR|WARNING): ", lines[-1]):
        lines = lines[:-1]
    text = "".join(lines)
    session["errors_at"] = session.get("errors_at", 0) + len(text.encode(errors="replace"))
    return merge(parse_engine_errors(text))


def report_errors(session, as_json=False):
    errors = new_errors(session)
    save_session(session)
    if errors and not as_json:
        print(describe_errors(errors))
    return errors


def cmd_start(args):
    name = args.session
    for option, given in (("--project", args.project), ("--scene", args.scene)):
        if given:
            raise LiveError(f"{option} runs a project under gdh's harness, and --binary runs a program as it is: "
                            f"give one of them.")
    if args.instances != 1 or args.gpu_passes:
        raise LiveError(f"{'--instances' if args.instances != 1 else '--gpu-passes'} needs --project: a --binary "
                        f"session runs one program, without gdh's harness.")
    binary = find_binary(args.binary)
    existing = read_session(name)
    if existing:
        if live.session_running(existing):
            raise LiveError(f"Session '{name}' is already running. Stop it with: gdh live stop --session {name}")
        live.stop_all(existing)
        live.remove_session(existing)
    commands = companions.parse_named(args.companion, "--companion")
    checks = companions.parse_named(args.companion_ready, "--companion-ready")
    fixed = companions.parse_named(args.companion_port, "--companion-port")
    for option, named in (("--companion-ready", checks), ("--companion-port", fixed)):
        for key in named:
            if key not in commands:
                raise LiveError(f"{option} names '{key}', which isn't a --companion.")
    ports = {key: companions.port_of(fixed[key]) if key in fixed else companions.free_port() for key in commands}
    for key, text in commands.items():
        companions.expand(text, ports, own=key)
    game_args = [companions.expand(a, ports, instance=0) for a in args.game_args]
    parse_resolution(args.resolution)
    pack = None if args.raw else export_pack(binary)
    cmd = command(binary, game_args, args.resolution, pack)
    out = Path(args.out or Path.cwd() / "captures" / "live" / name).resolve()
    out.mkdir(parents=True, exist_ok=True)
    live.SESSION_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    shims = write_shims(live.SESSION_DIR / f"{name}-shims")
    log, exit_file = out / "program.log", out / "exit.json"
    session = {"name": name, "kind": "binary", "shims": str(shims)}
    started_groups, display, mark, pid, runner = [], None, os.urandom(8).hex(), None, None
    try:
        for key, text in commands.items():
            try:
                record = companions.start(key, text, ports[key], ports, out, checks.get(key, "tcp"), args.timeout)
            except companions.CompanionError as e:
                started_groups.append(e.record["pid"])
                raise LiveError(f"{e} Last lines of {e.record['log']}:\n{tail_text(e.record['log'])}") from None
            started_groups.append(record["pid"])
            session.setdefault("companions", []).append(record)
            print(f"companion '{key}': pid {record['pid']}, port {record['port']}, log {record['log']}")
        deadline = time.monotonic() + args.timeout
        display = open_display(args.display, args.resolution, out / "display.log")
        launched = time.time()
        runner, pid = run(cmd, program_env(shims, display.name), log, exit_file, mark, args.idle_timeout,
                          session_path(name))
        window = None if args.no_wait else wait_window(display.name, pid, deadline, exit_file, log,
                                                       mark if args.keep_children else None)
    except BaseException:
        stop_run(pid, runner, display, mark)
        kill_groups(*started_groups)
        live.remove_session(session)
        raise
    record = {"pid": pid, "port": 0, "token": "", "out": str(out), "log": str(log), "groups": [pid, *display.groups],
              "display": display.record(), "mark": mark}
    others = [*started_groups, runner.pid, *display.groups]
    watchdog = live.start_watchdog([pid], others, [display.runtime_dir] if display.runtime_dir else [], [mark],
                                   args.keep_children, args.idle_timeout, name, [str(log)])
    session.update({"pid": pid, "port": 0, "token": "", "log": str(log), "out": str(out), "binary": str(binary),
                    "command": cmd, "export": pack is not None, "instances": [record], "watchdog": watchdog,
                    "runner": runner.pid, "exit_file": str(exit_file), "keep_children": args.keep_children,
                    "resolution": args.resolution, "started": launched, "log_at": 0, "errors_at": 0, "shots": 0,
                    "groups": [*started_groups, pid, runner.pid, *display.groups, watchdog]})
    report_errors(session)
    kind = "an exported Godot game, given Godot's off-screen options" if pack else "run as it is"
    print(f"started {binary}: {kind}")
    if window:
        print(describe_window(window))
    print(f"session '{name}': pid {pid}, {live.describe_display(record)}, output in {out}")
    return 0


def cmd_stop(args, session):
    # The program first (SIGTERM, so it can save and sign off), then what it spawned, the companions and the display.
    kill_groups(session["pid"])
    live.stop_all(session)
    live.remove_session(session)
    print(f"Stopped session '{args.session}'.")
    return 0


def cmd_status(args, session):
    touch(session)
    alive = pid_alive(session["pid"])
    ended = None if alive else read_exit(session["exit_file"], 1)
    children = live.spawned_by(session)
    windows = []
    if alive or (session.get("keep_children") and children):
        try:
            with x11.Display(session["instances"][0]["display"]["name"]) as d:
                windows = d.windows()
        except x11.X11Error:
            pass
    else:
        live.stop_all(session)  # what the watchdog does, in case it couldn't
    errors = report_errors(session, args.json)
    seconds = time.time() - session["started"]
    if args.json:
        print(json.dumps({"ok": True, "kind": "binary", "running": alive, "pid": session["pid"],
                          "binary": session["binary"], "export": session.get("export", False),
                          "seconds": round(seconds, 2), "exit": ended, "windows": windows, "spawned": children,
                          "errors": errors, "display": session["instances"][0]["display"],
                          "companions": [{**c, "running": pid_alive(c["pid"])} for c in session.get("companions", [])]},
                         indent=2))
        return 0
    if alive:
        print(f"running, pid {session['pid']}, {seconds:.1f} s: {session['binary']}")
    else:
        print(f"the program {describe_exit(ended, session['log'])}: {session['binary']}")
        if children:
            print("the session lasts while the processes it spawned run (--keep-children)")
    print(f"  {live.describe_display(session['instances'][0])}")
    for w in windows:
        print(f"  {describe_window(w)}")
    if children:
        print(live.describe_spawned(children, "  "))
    for companion in session.get("companions", []):
        state = "running" if pid_alive(companion["pid"]) else "exited"
        print(f"companion '{companion['name']}': {state}, pid {companion['pid']}, port {companion['port']}")
    if not alive:
        print(f"Last lines of {session['log']}:\n{tail_text(session['log'])}")
    return 0


def screen(session, label, tiles, as_json):
    """Save the display's screen to <out>/shots/NNNN-<label>.png."""
    display = session["instances"][0]["display"]["name"]
    image = x11.grab(display)
    session["shots"] = session.get("shots", 0) + 1
    shots = Path(session["out"]) / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", label) or "shot"
    path = shots / f"{session['shots']:04d}-{safe}.png"
    image.save(path)
    save_session(session)
    result = {"shots": {"normal": str(path)}, "image_size": list(image.size)}
    if not as_json:
        print(f"normal: {path}")
        if tiles:
            for tile in save_tiles(path, path.with_suffix("")):
                print(f"  tile: {tile}")
    return result


def cmd_shot(args, session):
    touch(session)
    require_running(session)
    if args.view and args.view != ["normal"]:
        raise LiveError("A --binary session's shot is the display's screen: the debug views need gdh's harness, "
                        "in a session started with --project.")
    errors = report_errors(session, args.json)
    result = screen(session, args.label, args.tiles, args.json)
    if args.json:
        print(json.dumps({"ok": True, "result": result, "errors": errors}, indent=2))
    return 0


# --- Input ---------------------------------------------------------------------------------------------------------

class Ordered(argparse.Action):
    """Keeps every input option, in the order given, in args.inputs."""

    def __call__(self, parser, namespace, values, option_string=None):
        namespace.inputs = [*getattr(namespace, "inputs", []), (self.dest, values)]


def point(text, size):
    try:
        x, y = (int(round(float(v))) for v in text.split(","))
    except ValueError:
        raise LiveError(f"A position is X,Y in screenshot pixels, not {text!r}.") from None
    if not (0 <= x < size[0] and 0 <= y < size[1]):
        raise LiveError(f"{x},{y} is off the screen, which is {size[0]}x{size[1]}.")
    return x, y


def plan(inputs, size):
    """Check every input before any is sent: [(kind, value)] with positions, keys and durations parsed."""
    steps = []
    for kind, value in inputs:
        if kind in ("move", "click", "double_click", "right_click", "middle_click"):
            steps.append((kind, point(value, size)))
        elif kind == "wheel":
            direction, _, count = value.partition(":")
            if direction not in x11.WHEEL or (count and not count.isdigit()):
                raise LiveError(f"--wheel takes up, down, left or right, and :N for N steps (down:3), not {value!r}.")
            steps.append((kind, (direction, int(count or 1))))
        elif kind == "key":
            steps.append((kind, x11.parse_key(value)))
        elif kind == "hold":
            key, _, seconds = value.rpartition(":") if re.search(r":[\d.]+$", value) else (value, "", "0.5")
            try:
                steps.append((kind, (x11.parse_key(key), float(seconds))))
            except ValueError:
                raise LiveError(f"--hold takes KEY or KEY:SECONDS, not {value!r}.") from None
        elif kind == "type":
            steps.append((kind, value))
        elif kind == "pause":
            try:
                steps.append((kind, float(value)))
            except ValueError:
                raise LiveError(f"--pause takes seconds, not {value!r}.") from None
    return steps


def send(d, kind, value):
    """Send one input. Returns what it reached, for the report."""
    if kind == "move":
        d.move(*value)
    elif kind in ("click", "double_click", "right_click", "middle_click"):
        button = {"right_click": x11.BUTTONS["right"], "middle_click": x11.BUTTONS["middle"]}.get(kind, 1)
        d.click(*value, button=button, count=2 if kind == "double_click" else 1)
        target = d.window_at(*value)
        return f"{kind.replace('_', '-')} {value[0]},{value[1]}" + (f" on {describe_window(target)}" if target else
                                                                    " (no window there)")
    elif kind == "wheel":
        d.wheel(*value)
    elif kind == "key":
        d.keys(value)
    elif kind == "hold":
        d.keys(value[0], hold=value[1])
    elif kind == "type":
        for ch in value:
            d.keys([x11.keysym_of_char(ch)])
    elif kind == "pause":
        time.sleep(value)
    return None


def cmd_input(args, session):
    touch(session)
    require_running(session)
    inputs = getattr(args, "inputs", [])
    if not inputs:
        raise LiveError("Nothing to send: give --click X,Y, --key KEY, --type TEXT and the rest (gdh live input -h).")
    display = session["instances"][0]["display"]["name"]
    with x11.Display(display) as d:
        d.check_xtest()
        steps = plan(inputs, d.size())
        if not d.windows():
            print("note: the display shows no window, so the input reaches none")
        reached = [r for kind, value in steps if (r := send(d, kind, value))]
    if not args.json:
        for line in reached:
            print(line)
        print(f"sent {len(steps)} input{'s' if len(steps) != 1 else ''}")
    result = {"sent": len(steps), "reached": reached}
    if args.shot:
        time.sleep(args.settle)
        result.update(screen(session, "after-input", False, args.json))
    errors = report_errors(session, args.json)
    if args.json:
        print(json.dumps({"ok": True, "result": result, "errors": errors}, indent=2))
    return 0


# --- Waiting -------------------------------------------------------------------------------------------------------

def find_line(session, pattern):
    """The first whole line from the session's log mark on that matches, or None; moves the mark past it."""
    try:
        with open(session["log"], "rb") as f:
            f.seek(session.get("log_at", 0))
            data = f.read()
    except OSError:
        return None
    at = session.get("log_at", 0)
    for raw in data.splitlines(keepends=True):
        if not raw.endswith(b"\n"):
            break
        at += len(raw)
        line = raw.decode(errors="replace").rstrip("\n")
        if pattern.search(line):
            session["log_at"] = at
            save_session(session)
            return line
    return None


def cmd_wait(args, session):
    touch(session)
    pattern = None
    if args.log is not None:
        try:
            pattern = re.compile(args.log)
        except re.error as e:
            raise LiveError(f"--log takes a regular expression: {e}") from None
    display = session["instances"][0]["display"]["name"]
    timeout = args.seconds if args.seconds is not None else args.timeout
    deadline = time.monotonic() + timeout
    while True:
        # Whether it ran before the log was read: a line written just before it ended is still found.
        alive, still = pid_alive(session["pid"]), running(session)
        if pattern:
            line = find_line(session, pattern)
            if line is not None:
                report_errors(session)
                print(line)
                return 0
        if args.exit and not alive:
            ended = read_exit(session["exit_file"], 2)
            report_errors(session)
            print(f"the program {describe_exit(ended, session['log'])}")
            print(f"Last lines of {session['log']}:\n{tail_text(session['log'])}")
            return 0
        if not still and not args.exit:
            report_errors(session)
            live.stop_all(session)
            raise ended_error(session)
        if args.window:
            try:
                with x11.Display(display) as d:
                    shown = [w for w in d.windows() if not w["popup"]]
            except x11.X11Error:
                shown = []
            if shown:
                report_errors(session)
                print(describe_window(shown[-1]))
                return 0
        if time.monotonic() >= deadline:
            break
        time.sleep(min(0.1, max(deadline - time.monotonic(), 0)))
    report_errors(session)
    if args.seconds is not None:
        print(f"waited {args.seconds:g} s; the program runs")
        return 0
    what = (f"No line matching {args.log!r} in the log" if pattern else
            "The program still runs" if args.exit else "No window shown")
    raise LiveError(f"{what} after {timeout:g} s (--timeout). Last lines of {session['log']}:\n"
                    f"{tail_text(session['log'])}")


# --- Routing -------------------------------------------------------------------------------------------------------

def route(name, func):
    """gdh live NAME's function: --binary sessions' commands go here, the rest to live (func)."""
    def run_command(args):
        if name == "start":
            if getattr(args, "binary", None):
                return cmd_start(args)
            if not args.project:
                raise LiveError("gdh live start takes --project DIR (a project, under gdh's harness) or --binary PATH "
                                "(a program as it is: an exported game, a launcher).")
            if args.raw or args.no_wait:
                raise LiveError(f"{'--raw' if args.raw else '--no-wait'} goes with --binary.")
            return func(args)
        session = read_session(args.session)
        if session is None or session.get("kind") != "binary" or name in PASS:
            if session is None and name in ("input", "wait"):
                raise LiveError(f"No live session '{args.session}'. Start one with: gdh live start --binary <path>")
            return func(args)
        handler = {"stop": cmd_stop, "status": cmd_status, "shot": cmd_shot, "input": cmd_input,
                   "wait": cmd_wait}.get(name)
        if handler is None:
            raise LiveError(f"gdh live {name} needs gdh's harness in the game, and session '{args.session}' runs a "
                            f"program as it is (--binary). It takes {', '.join(COMMANDS)}; start the project with "
                            f"--project to step, inspect and evaluate it.")
        return handler(args, session)
    return run_command


def binary_only(name):
    def refuse(args):
        raise LiveError(f"gdh live {name} drives a --binary session from outside. Session '{args.session}' runs a "
                        f"project under gdh's harness: send its input with gdh live step, and wait with step or run.")
    return refuse


def add_start_options(p):
    p.add_argument("--binary", metavar="PATH",
                   help="Run this program as it is instead of a project (an exported game, a launcher, any X "
                        "program): no harness, so it's seen with shot and wait and driven with input. An exported "
                        "Godot game gets Godot's off-screen options, and the arguments after -- after Godot's --")
    p.add_argument("--raw", action="store_true",
                   help="With --binary: pass the arguments after -- as they are, with nothing added")
    p.add_argument("--no-wait", action="store_true",
                   help="With --binary: don't wait for the program's first window (a program that shows none)")


def add_live_parsers(commands, command):
    p = command("input", binary_only("input"),
                "Send a --binary session's program pointer and key input through the display, in the order given")
    p.set_defaults(inputs=[])
    for flag, metavar, text in (
            ("--move", "X,Y", "Move the pointer to screenshot pixel X,Y"),
            ("--click", "X,Y", "Left click at X,Y, giving the window there the focus first"),
            ("--double-click", "X,Y", "Double click at X,Y"),
            ("--right-click", "X,Y", "Right click at X,Y"),
            ("--middle-click", "X,Y", "Middle click at X,Y"),
            ("--wheel", "DIR[:N]", "Turn the wheel at the pointer: up, down, left or right, N steps (default 1)"),
            ("--key", "KEY", "Press and let go of a key: an X keysym name (Return, Escape, F5, a, Left) or a chord "
                             "(ctrl+s); Godot's names (Enter, Space, PageUp) work too"),
            ("--hold", "KEY[:S]", "Hold a key (or chord) down for S seconds (default 0.5)"),
            ("--type", "TEXT", "Type TEXT, a key a character, into the window with the focus"),
            ("--pause", "S", "Wait S seconds before the next input")):
        p.add_argument(flag, action=Ordered, metavar=metavar, help=text)
    p.add_argument("--shot", action="store_true", help="Save the screen after the input")
    p.add_argument("--settle", type=float, default=0.5, metavar="S",
                   help="Seconds to let the program draw before --shot (default 0.5)")

    p = command("wait", binary_only("wait"), "Wait on a --binary session's program: time, a log line, a window or "
                                              "its exit")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--seconds", type=float, metavar="S", help="Wait S seconds (fails if the program ends)")
    which.add_argument("--log", metavar="REGEX",
                       help="Wait for a line of its log matching REGEX, after the line the last --log matched")
    which.add_argument("--window", action="store_true", help="Wait for it to show a window")
    which.add_argument("--exit", action="store_true", help="Wait for it to exit, and say how it ended")
    p.add_argument("--timeout", type=float, default=60, help="Seconds before --log, --window or --exit gives up and "
                                                             "exits 1 (default 60)")


# --- gdh export --smoke --------------------------------------------------------------------------------------------

def smoke(cmd, seconds, out, display_choice, resolution):
    """Run a build black-box for `seconds` and check it: still running at the end, and no engine errors in its log.
    Saves the screen at the end to <out>/smoke.png. Returns (passed, report lines)."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    log, exit_file = out / "program.log", out / "exit.json"
    mark = os.urandom(8).hex()
    with tempfile.TemporaryDirectory(prefix="gdh-smoke-") as tmp:
        shims = write_shims(Path(tmp) / "shims")
        display = open_display(display_choice, resolution, out / "display.log")
        pid = runner = None
        try:
            # Hold a connection to the display for the run: an X server started with -terminate exits when its last
            # client leaves, which a program that restarts its window would otherwise be.
            with x11.Display(display.name) as d:
                runner, pid = run(cmd, program_env(shims, display.name), log, exit_file, mark)
                deadline = time.monotonic() + seconds
                windows = []
                while time.monotonic() < deadline and pid_alive(pid):
                    windows = windows or d.windows()
                    time.sleep(0.1)
                alive = pid_alive(pid)
                shot = None
                if alive:
                    shot = out / "smoke.png"
                    x11.grab(display.name).save(shot)
        finally:
            stop_run(pid, runner, display, mark)
    errors = merge([e for e in parse_engine_errors(Path(log).read_text(errors="replace")) if e["type"] != "warning"])
    lines, passed = [], alive and not errors
    if alive:
        lines.append(f"ran {seconds:g} s{'' if windows else ', and showed no window'}; screen: {shot}")
    else:
        ended = read_exit(exit_file, 2)
        lines.append(f"the build {describe_exit(ended, log)}, before {seconds:g} s. Last lines of {log}:\n"
                     f"{tail_text(log)}")
    if errors:
        lines.append(f"{len(errors)} engine error{'s' if len(errors) != 1 else ''} in its log:")
        lines.append(describe_errors(errors[:15], "  "))
    lines.append(f"log: {log}")
    return passed, lines
