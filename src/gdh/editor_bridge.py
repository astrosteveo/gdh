"""gdh bridge: work through a running Godot editor instead of behind its back.

The bridge (addon/gdh_bridge/server.gd) is an HTTP server on 127.0.0.1 inside the editor. It runs in a person's editor
once the addon is installed and enabled (`gdh bridge install`), or in a headless editor of gdh's own
(`gdh bridge start`), which installs nothing into the project. Either way it writes its port and a random token to
.godot/gdh_bridge.json, and every command here reads that file.

Every reply carries "errors": the editor's errors and warnings since the previous reply.

This module uses only the standard library, so Claude Code's hooks can import it without gdh's dependencies.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from gdh.godot import HARNESS, GdhError, build_csharp, godot_binary, kill_groups, pid_alive, write_alert_shims

ADDON = Path(__file__).resolve().parent / "addon" / "gdh_bridge"
ADDON_FILES = ("plugin.cfg", "plugin.gd", "server.gd")
PLUGIN_CFG = "res://addons/gdh_bridge/plugin.cfg"
INFO = Path(".godot") / "gdh_bridge.json"
HOST_SCRIPT = HARNESS / "bridge_host.gd"
CHECK_SCRIPT = HARNESS / "check.gd"
SCENE_EXTENSIONS = {".tscn", ".scn"}


class NoBridge(GdhError):
    """No bridge answers for the project."""


# --- Finding the project and the bridge -----------------------------------------------------------------------------

def find_project(start):
    """The directory holding project.godot at or above start, or None."""
    d = Path(start).resolve()
    if d.is_file() or not d.exists():
        d = d.parent
    for candidate in (d, *d.parents):
        if (candidate / "project.godot").is_file():
            return candidate
    return None


def to_res(project, path):
    """A res:// path for a file system path inside the project (res:// and uid:// pass through)."""
    s = str(path)
    if s.startswith(("res://", "uid://")):
        return s
    rel = os.path.relpath(Path(s).resolve(), project)
    if rel.startswith(".."):
        raise GdhError(f"{s} is outside the project {project}")
    return "res://" + rel.replace(os.sep, "/")


def read_info(project):
    try:
        info = json.loads((Path(project) / INFO).read_text())
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) and "port" in info and "token" in info else None


def call(project, payload, timeout=330):
    """Send one command to the project's bridge and return its reply. Raises NoBridge when none answers."""
    info = read_info(project)
    if info is None:
        raise NoBridge("No editor bridge is running for this project: open the project in the Godot editor with the gdh "
                       "bridge addon enabled (gdh bridge install), or start gdh's headless editor (gdh bridge start).")
    req = urllib.request.Request(
        f"http://127.0.0.1:{info['port']}/", data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-Gdh-Token": info["token"]})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except ValueError:
            raise NoBridge(f"The bridge on port {info['port']} answered HTTP {e.code}.") from None
    except (urllib.error.URLError, OSError) as e:
        raise NoBridge(f"The bridge on port {info['port']} doesn't answer ({getattr(e, 'reason', e)}): the editor "
                       f"has closed, or is busy. .godot/gdh_bridge.json is left from it.") from None


def status(project, timeout=5):
    """The bridge's status, or None when no bridge answers."""
    try:
        reply = call(project, {"cmd": "status"}, timeout=timeout)
    except NoBridge:
        return None
    return reply if reply.get("ok") else None


# --- Installing the addon -------------------------------------------------------------------------------------------

def install(project, enable):
    dest = Path(project) / "addons" / "gdh_bridge"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ADDON_FILES:
        shutil.copyfile(ADDON / name, dest / name)
    print(f"Copied the gdh bridge addon to {dest}")
    if not enable:
        print("Enable it in the editor: Project > Project Settings > Plugins > gdh bridge.")
        return 0
    if status(project):
        raise GdhError("An editor is running the bridge on this project: enable the plugin there (Project > Project "
                       "Settings > Plugins) rather than editing project.godot under it.")
    path = Path(project) / "project.godot"
    text = path.read_text()
    if PLUGIN_CFG in text:
        print("It's already enabled in project.godot.")
        return 0
    m = re.search(r"^enabled=PackedStringArray\((.*)\)$", text, re.M)
    if m:
        inner = m.group(1).strip()
        new = f'enabled=PackedStringArray({inner + ", " if inner else ""}"{PLUGIN_CFG}")'
        text = text[:m.start()] + new + text[m.end():]
    else:
        text = text.rstrip("\n") + f'\n\n[editor_plugins]\n\nenabled=PackedStringArray("{PLUGIN_CFG}")\n'
    path.write_text(text)
    print("Enabled it in project.godot: it starts when the editor next opens the project.")
    return 0


# --- Checking scripts without an editor ---------------------------------------------------------------------------

def check_headless(project, paths, timeout=60):
    """Load each script in a headless Godot running harness/check.gd, off the user's desktop. Returns {path: [errors]}.

    check.gd runs as the game's SceneTree, so a script that names an autoload compiles as it does in the game, which
    `godot --check-only` can't do. The autoloads are created but never enter the tree (check.gd). The project is
    imported first when its import cache is stale, as before a game runs: global class names come from its class
    cache, and a preloaded asset from its imported copy."""
    from gdh.godot import alert_shims, godot_env
    from gdh.imports import ensure_imported
    ensure_imported(project)
    with alert_shims() as shims, tempfile.TemporaryDirectory(prefix="gdh-check-") as tmp:
        result = Path(tmp) / "checked.json"
        env = godot_env(shims, ":gdh-no-display", ["--out", result, *paths])
        cmd = [godot_binary(project), "--headless", "--path", str(project), "--script", str(CHECK_SCRIPT)]
        try:
            proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout,
                                  stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return {p: [{"type": "error", "message": f"Godot took over {timeout} s to check it", "where": p}]
                    for p in paths}
        try:
            return json.loads(result.read_text())
        except (OSError, ValueError):
            errors = parse_engine_errors(proc.stdout + proc.stderr)
            errors.append({"type": "error", "message": f"Godot exited {proc.returncode} before checking it",
                           "where": str(CHECK_SCRIPT)})
            return {p: errors for p in paths}


def parse_engine_errors(text):
    """Errors from Godot's printed log: each `SCRIPT ERROR:`/`ERROR:` line with the `at:` line after it."""
    errors = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"\s*(SCRIPT ERROR|ERROR|WARNING): (.*)", line)
        if not m:
            continue
        where = ""
        if i + 1 < len(lines):
            at = re.match(r"\s*at: (?:\S+ )?\((.*)\)", lines[i + 1])
            where = at.group(1) if at else lines[i + 1].strip().removeprefix("at: ")
        kind = {"SCRIPT ERROR": "script", "ERROR": "error", "WARNING": "warning"}[m.group(1)]
        errors.append({"type": kind, "message": m.group(2), "where": where})
    # "Failed to load script ... Parse error" only repeats the parse errors above it.
    if any(e["type"] == "script" for e in errors):
        errors = [e for e in errors if not e["message"].startswith("Failed to load script")]
    return errors


# --- gdh's own headless editor --------------------------------------------------------------------------------------

def session_dir(project):
    from gdh.live import SESSION_DIR
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", str(Path(project).resolve()).strip("/"))
    return SESSION_DIR / "bridge" / name


def start(project, idle_timeout, timeout, build=True):
    running = status(project)
    if running:
        who = "gdh's headless editor" if running.get("headless") else "an editor (a person's)"
        print(f"The bridge is already running for this project, in {who}.")
        return 0
    if build:
        build_csharp(project)
    from gdh.editor import editor_env
    sdir = session_dir(project)
    shutil.rmtree(sdir, ignore_errors=True)
    sdir.mkdir(parents=True)
    shims = write_alert_shims(sdir / "shims")
    harness = ["--server", str(ADDON / "server.gd"), "--idle-timeout", str(idle_timeout), "--timeout", str(timeout)]
    # Headless needs no display; the bogus one keeps any child off the desktop.
    env = editor_env(shims, ":gdh-no-display", harness)
    cmd = [godot_binary(project), "--headless", "--editor", "--path", str(project), "--script", str(HOST_SCRIPT)]
    (Path(project) / INFO).unlink(missing_ok=True)
    log = sdir / "godot.log"
    # A supervisor runs the editor and, whenever it ends, puts the project's editor state (.godot/editor) back.
    sup = subprocess.Popen([sys.executable, "-m", "gdh.editor_bridge", "--supervise", str(project), str(log), *cmd],
                           env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           start_new_session=True, cwd=str(Path(__file__).resolve().parent.parent))
    (sdir / "supervisor.pid").write_text(str(sup.pid))
    deadline = time.monotonic() + timeout + 30
    while time.monotonic() < deadline:
        if sup.poll() is not None:
            tail = "\n".join(log.read_text(errors="replace").splitlines()[-15:]) if log.exists() else ""
            raise GdhError(f"gdh's headless editor exited before its bridge started. Last lines of {log}:\n{tail}")
        running = status(project, timeout=2)
        if running:
            print(f"gdh's headless editor is running the bridge for {project} (Godot {running['godot']}). It quits "
                  f"after {int(idle_timeout)} s without a request, or on gdh bridge stop. Log: {log}")
            return 0
        time.sleep(0.25)
    kill_groups(sup.pid)
    raise GdhError(f"gdh's headless editor didn't start its bridge in {timeout + 30} s. Log: {log}")


def supervise(project, log, cmd):
    """Run the editor, wait for it, and put .godot/editor back as it was. Runs detached, from start()."""
    from gdh.editor import EditorState
    with EditorState(project), open(log, "w") as out:
        proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True)
        try:
            proc.wait()
        finally:
            kill_groups(proc.pid)
    info = read_info(project)
    if info and info.get("pid") == proc.pid:
        (Path(project) / INFO).unlink(missing_ok=True)


def stop(project):
    running = status(project)
    if running is None:
        print("No bridge is running for this project.")
        return 0
    if not running.get("headless"):
        raise GdhError("The bridge running for this project is in a person's editor; gdh only stops its own headless "
                       "editor.")
    info = read_info(project)
    call(project, {"cmd": "quit"})
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and pid_alive(info["pid"]):
        time.sleep(0.2)
    sup = session_dir(project) / "supervisor.pid"
    if sup.exists():
        pid = int(sup.read_text())
        while time.monotonic() < deadline and pid_alive(pid):
            time.sleep(0.2)
        if pid_alive(pid):
            kill_groups(pid)
    print("Stopped gdh's headless editor.")
    return 0


# --- The CLI ----------------------------------------------------------------------------------------------------------

def print_reply(reply, as_json):
    if as_json:
        print(json.dumps(reply, indent=2))
        return
    errors = reply.pop("errors", [])
    for key, value in reply.items():
        if key == "ok":
            continue
        print(f"{key}: {json.dumps(value) if not isinstance(value, str) else value}")
    for e in errors:
        count = f" (x{e['count']})" if e.get("count", 1) > 1 else ""
        print(f"editor {e['type']}: {e['message']}{count} at {e['where']}")


def cmd_bridge(args):
    project = find_project(args.project or os.getcwd())
    if project is None:
        raise GdhError(f"No project.godot at or above {args.project or os.getcwd()}: pass --project.")
    action = args.action
    if action == "install":
        return install(project, args.enable)
    if action == "start":
        return start(project, args.idle_timeout, args.timeout, build=not args.no_build)
    if action == "stop":
        return stop(project)
    paths = [to_res(project, p) for p in getattr(args, "paths", []) or []]
    if action in ("status", "errors"):
        payload = {"cmd": action}
    elif action in ("scan", "save", "resave", "check"):
        if action in ("resave", "check") and not paths:
            raise GdhError(f"{action} needs at least one path")
        payload = {"cmd": action, "paths": paths}
    elif action == "reload":
        payload = {"cmd": action, "paths": paths, "force": args.force}
    elif action == "uid":
        payload = {"cmd": action, "items": paths}
    elif action == "open":
        payload = {"cmd": action, "path": to_res(project, args.path)}
    elif action == "play":
        if args.stop:
            payload = {"cmd": "stop"}
        else:
            target = args.scene or ""
            payload = {"cmd": "play", "path": target if target in ("", "current") else to_res(project, target)}
    elif action == "exec":
        try:
            code = sys.stdin.read() if args.file == "-" else Path(args.file).read_text()
        except OSError as e:
            raise GdhError(str(e)) from None
        payload = {"cmd": action, "code": code}
    else:
        raise GdhError(f"unknown bridge action {action}")
    try:
        reply = call(project, payload)
    except NoBridge as e:
        if action != "check":
            print(f"gdh: {e}", file=sys.stderr)
            return 3
        # No editor: load the scripts in a headless Godot instead.
        checked = check_headless(project, paths)
        reply = {"ok": not any(e["type"] != "warning" for errs in checked.values() for e in errs),
                 "checked": checked, "note": "no editor bridge: loaded in a headless Godot instead"}
    ok = bool(reply.get("ok"))
    print_reply(reply, args.json)
    return 0 if ok else 1


def add_parser(sub):
    p = sub.add_parser("bridge", help="Work through a running Godot editor: rescan, UIDs, open, save, reload, check, exec")
    acts = p.add_subparsers(dest="action", required=True)

    def action(name, help_text):
        a = acts.add_parser(name, help=help_text)
        a.add_argument("--project", help="The Godot project (default: the one holding the current directory)")
        a.add_argument("--json", action="store_true", help="Print the bridge's reply as JSON")
        return a

    a = action("install", "Copy the bridge addon into the project's addons/")
    a.add_argument("--enable", action="store_true", help="Also enable it in project.godot (only with no editor open)")
    a = action("start", "Start gdh's own headless editor with the bridge (installs nothing in the project)")
    a.add_argument("--idle-timeout", type=float, default=1800, help="Quit after this many seconds without a request (default 1800)")
    a.add_argument("--timeout", type=float, default=300, help="Seconds to wait for the editor to start (default 300)")
    a.add_argument("--no-build", action="store_true", help="Don't build a C# project's assemblies first")
    action("stop", "Stop gdh's headless editor (never a person's)")
    action("status", "Open scenes, unsaved scenes, the current scene, play state")
    action("errors", "The editor's errors and warnings since the last command")
    a = action("scan", "Rescan the project (or the files given) and wait until imports and UIDs are done")
    a.add_argument("paths", nargs="*")
    a = action("uid", "A path's UID, or a uid:// path's file")
    a.add_argument("paths", nargs="+", metavar="PATH_OR_UID")
    a = action("open", "Open a scene, script or resource in the editor")
    a.add_argument("path")
    a = action("save", "Save the given open scenes, or every open scene")
    a.add_argument("paths", nargs="*")
    a = action("reload", "Reload open scenes from disk; skips scenes with unsaved changes unless --force")
    a.add_argument("paths", nargs="+")
    a.add_argument("--force", action="store_true", help="Reload even over unsaved changes (they're lost)")
    a = action("resave", "Save scenes or resources through Godot, which fills in their UIDs and node unique_ids")
    a.add_argument("paths", nargs="+")
    a = action("check", "Load scripts again from disk and report the errors each raises (no editor: a headless parse)")
    a.add_argument("paths", nargs="+")
    a = action("play", "Run the main scene, the current scene (current) or a scene in the editor; --stop stops it")
    a.add_argument("scene", nargs="?")
    a.add_argument("--stop", action="store_true")
    a = action("exec", "Run a GDScript's func run(editor) inside the editor (FILE, or - for stdin)")
    a.add_argument("file")
    p.set_defaults(func=cmd_bridge)


if __name__ == "__main__" and len(sys.argv) > 3 and sys.argv[1] == "--supervise":
    supervise(Path(sys.argv[2]), sys.argv[3], sys.argv[4:])
