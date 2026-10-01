"""gdh editor: open scenes in the Godot editor on gdh's display and save what it shows.

Godot runs as `godot --editor --path <project> --script harness/editor.gd`: the editor starts as it does for a person
(its tool scripts, plugins and importers all run), with the harness as its main loop. The harness waits for the first
scan, opens each scene, and saves the viewport (viewport.png), the whole window (editor.png) and report.json.

The editor never touches the user's own: its settings, data and caches live in gdh's editor home
(GDH_EDITOR_HOME, default ~/.local/share/gdh/editor-home), and the project's .godot/editor (its layout, recent scenes
and each scene's camera) is put back as it was. Anything else the editor writes in the project is listed, never undone.
"""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from gdh.display import open_display
from gdh.godot import HARNESS, GdhError, alert_shims, build_csharp, godot_cmd, godot_env, kill_groups

EDITOR_SCRIPT = HARNESS / "editor.gd"
# The project's editor state (layout, open scenes, each scene's camera and folds), kept as it was.
EDITOR_STATE = Path(".godot") / "editor"
# Files up to this size are hashed too, so one the editor wrote again unchanged isn't reported.
HASHED = 256 * 1024
# Lines in the log that mean the GPU couldn't be had: most often its memory is full.
NO_DEVICE = ("Couldn't create Vulkan device", "Couldn't initialize Vulkan device")


def editor_home():
    """Where the editor gdh runs keeps its settings, data and caches: never the user's own."""
    chosen = os.environ.get("GDH_EDITOR_HOME", "")
    if chosen:
        return Path(os.path.expanduser(chosen)).resolve()
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "gdh" / "editor-home"


def editor_env(shims, display, harness_args):
    env = godot_env(shims, display, harness_args, game=False)
    home = editor_home()
    for name, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache")):
        (home / sub).mkdir(parents=True, exist_ok=True)
        env[name] = str(home / sub)
    return env


def snapshot(project):
    """Every file in the project outside .godot (and version control): its size and modification time, and a hash of
    its content if it's small."""
    files = {}
    for directory, dirs, names in os.walk(project):
        dirs[:] = [d for d in dirs if not (Path(directory) == Path(project) and d in (".godot", ".git"))]
        for name in names:
            path = Path(directory) / name
            try:
                stat = path.stat()
                digest = hashlib.sha256(path.read_bytes()).hexdigest() if stat.st_size <= HASHED else None
            except OSError:
                continue
            files[str(path.relative_to(project))] = (stat.st_size, stat.st_mtime_ns, digest)
    return files


def changed(was, now):
    """Whether a file's content changed: by its hash when both have one, else by its size and modification time."""
    if was[2] is not None and now[2] is not None:
        return was[2] != now[2]
    return was[:2] != now[:2]


def changes(before, after):
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "changed": sorted(p for p in set(before) & set(after) if changed(before[p], after[p])),
    }


class EditorState:
    """The project's .godot/editor, set aside before the editor runs and put back after."""

    def __init__(self, project):
        self.path = Path(project) / EDITOR_STATE
        self.kept = None

    def __enter__(self):
        if self.path.is_dir():
            self.kept = Path(tempfile.mkdtemp(prefix="gdh-editor-state-"))
            shutil.copytree(self.path, self.kept / "editor")
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.path, ignore_errors=True)
        if self.kept is not None:
            shutil.copytree(self.kept / "editor", self.path)
            shutil.rmtree(self.kept, ignore_errors=True)


def scene_folder(scene):
    return scene.removeprefix("res://").removesuffix(".tscn").removesuffix(".scn").replace("/", "__")


def harness_args(args, out):
    harness = ["--out", str(out), "--warmup", str(args.warmup), "--idle", str(args.idle), "--timeout", str(args.timeout)]
    for scene in args.scene:
        harness += ["--scene", scene]
    for spec in args.set:
        harness += ["--set", spec]
    if args.save:
        harness += ["--save", "1"]
    if args.rebuild:
        harness += ["--rebuild", "1"]
    for key in ("select", "focus", "orbit", "zoom", "view", "far"):
        if getattr(args, key) is not None:
            harness += [f"--{key}", str(getattr(args, key))]
    return harness


def cmd_editor(args):
    project = Path(args.project).resolve()
    if not (project / "project.godot").exists():
        raise GdhError(f"No project.godot in {project}.")
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    folders = [out] if len(args.scene) == 1 else [out / scene_folder(s) for s in args.scene]
    for folder in folders:
        for old in ("viewport.png", "editor.png", "report.json"):
            (folder / old).unlink(missing_ok=True)
    (out / "editor.json").unlink(missing_ok=True)
    if not args.no_build:
        build_csharp(project)
    before = snapshot(project)
    cmd = godot_cmd(project, args.resolution, ["--editor", "--script", str(EDITOR_SCRIPT)])
    log_path = out / "godot.log"
    code = None
    with alert_shims() as shims, EditorState(project):
        display = open_display(args.display, args.resolution, out / "display.log")
        try:
            with open(log_path, "w") as log:
                proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                        env=editor_env(shims, display.name, harness_args(args, out)),
                                        start_new_session=True)
                try:
                    code = proc.wait(timeout=args.timeout + 60 * len(args.scene))
                except subprocess.TimeoutExpired:
                    code = "timeout"
        finally:
            if "proc" in locals():
                kill_groups(proc.pid)
                proc.wait()
            display.stop()
    moved = changes(before, snapshot(project))
    log = log_path.read_text(errors="replace") if log_path.exists() else ""
    startup_path = out / "editor.json"
    startup = json.loads(startup_path.read_text()) if startup_path.exists() else {}
    if not startup:
        if any(line in log for line in NO_DEVICE):
            raise GdhError(f"The editor couldn't get the GPU (Vulkan device creation failed): its memory may be full "
                           f"(nvidia-smi shows what holds it). Log: {log_path}")
        raise GdhError(f"The editor didn't start (exit {code}). Last lines of {log_path}:\n"
                       + "\n".join(log.splitlines()[-15:]))
    if not startup.get("ready"):
        raise GdhError(f"The editor didn't finish scanning the project in {args.timeout} s. Log: {log_path}")
    startup["display"] = display.kind
    startup["project_changes"] = moved
    startup_path.write_text(json.dumps(startup, indent=2))
    print(f"editor: ready in {startup['ready_ms']} ms on {startup.get('adapter', '?')}, display={display.kind}, "
          f"exit={code}, {len(startup.get('errors', []))} errors and warnings while starting -> {out}")
    edited = [p for kind in ("added", "removed", "changed") for p in moved[kind]]
    if edited:
        print(f"  note: the editor wrote to the project: {', '.join(edited[:8])}{' ...' if len(edited) > 8 else ''} "
              f"(listed in editor.json; gdh leaves them as they are)")
    ok = code == 0
    for scene, folder in zip(args.scene, folders):
        ok &= report_scene(scene, folder)
    return 0 if ok else 1


def report_scene(scene, folder):
    path = folder / "report.json"
    if not path.exists():
        print(f"{scene}: no report (the editor ended first)")
        return False
    report = json.loads(path.read_text())
    if report.get("error"):
        print(f"{scene}: {report['error']}")
        return False
    errors = [e for e in report.get("open_errors", []) + report.get("errors", []) if e["type"] != "warning"]
    warnings = [e for e in report.get("open_errors", []) + report.get("errors", []) if e["type"] == "warning"]
    idle = report.get("idle", {})
    redraw = report.get("redraw", {})
    size = report.get("viewport_size", [0, 0])
    print(f"{scene}: opened in {report.get('open_ms', '?')} ms, errors={len(errors)} warnings={len(warnings)}, "
          f"idle redraws {idle.get('redraws_per_second', '?')}/s, viewport {size[0]}x{size[1]} "
          f"(a redraw: {redraw.get('gpu_ms_median', '?')} ms GPU, {redraw.get('cpu_ms_median', '?')} ms CPU) -> {folder}")
    for e in errors[:10]:
        count = f" (x{e['count']})" if e.get("count", 1) > 1 else ""
        print(f"  {e['type']}: {e['message']}{count} at {e['where']}")
    for note in report.get("notes", []):
        print(f"  note: {note}")
    if "rebuild" in report:
        rb = report["rebuild"]
        print(f"  rebuilt with the scene open: build exit {rb['build_exit']}, {len(rb['errors'])} errors after, "
              f"nodes under the scene {rb['nodes_before']} before, {rb['nodes_after']} after")
        for e in rb["errors"][:8]:
            print(f"  {e['type']}: {e['message']} at {e['where']}")
    if "saved" in report:
        saved = report["saved"]
        print(f"  saved through the editor: the file {'changed' if saved['changed'] else 'came back byte for byte'}"
              + (f", {len(saved['errors'])} errors while saving" if saved["errors"] else ""))
        for e in saved["errors"][:5]:
            print(f"  {e['type']}: {e['message']} at {e['where']}")
    return True


def add_parser(sub, add_display_option):
    p = sub.add_parser("editor", help="Open scenes in the Godot editor and save its viewport, its window and a report")
    p.add_argument("--project", required=True, help="Godot project directory")
    p.add_argument("--scene", required=True, action="append", help="res:// path; repeatable")
    p.add_argument("--out", required=True, help="Output directory")
    p.add_argument("--resolution", default="1600x900", help="The editor window's size (default 1600x900)")
    add_display_option(p)
    p.add_argument("--warmup", type=int, default=60, help="Frames to wait after a scene opens (default 60)")
    p.add_argument("--idle", type=float, default=2.0,
                   help="Seconds to count the editor's redraws while nothing happens (default 2)")
    p.add_argument("--set", action="append", default=[], metavar="NODE:PROPERTY=VALUE",
                   help="Set a property before capturing, in memory only (never saved); the value in Godot's syntax "
                        "(str_to_var), a res:// path as that resource, or plain text. NODE is relative to the scene's root: . is the root. Repeatable")
    p.add_argument("--select", metavar="NODE", help="Select a node, so the inspector shows it")
    p.add_argument("--focus", metavar="NODE",
                   help="Center the 3D view on a node, as the View menu's Focus Selection does (it keeps its distance); in a 2D "
                        "scene, frame it as the 2D View menu's Frame Selection does (centred and zoomed to fit)")
    p.add_argument("--orbit", metavar="DX,DY", help="Then turn the 3D view as dragging with the middle button DX,DY pixels does")
    p.add_argument("--zoom", type=int, metavar="STEPS",
                   help="Then zoom the 3D view as the mouse wheel does: STEPS out, or in when negative")
    p.add_argument("--view", metavar="X,Y,Z:X,Y,Z",
                   help="Look from the first point at the second, through a camera gdh adds (never saved) and previews")
    p.add_argument("--far", type=float, help="The --view camera's far plane, in metres (default: the editor camera's)")
    p.add_argument("--timeout", type=int, default=300, help="Seconds to wait for the editor to start (default 300)")
    p.add_argument("--no-build", action="store_true", help="Don't build a C# project's assemblies first")
    p.add_argument("--rebuild", action="store_true",
                   help="With a scene open, build a C# project's code again, give the editor the focus so it loads the new "
                        "build (as coming back to its window does), and save viewport-rebuilt.png: errors land in the report")
    p.add_argument("--save", action="store_true",
                   help="Save each scene through the editor (File > Save Scene) after capturing it, as a person would; "
                        "the report says whether the file changed")
    p.set_defaults(func=cmd_editor)
