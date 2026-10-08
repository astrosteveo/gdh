"""gdh export: build a project with one of its export presets, headless, off the user's desktop.

Godot exports with the presets in export_presets.cfg (made in the editor's Project > Export dialog) and the export
templates for its exact version, in ~/.local/share/godot/export_templates/<version>. gdh checks both first and says
what's missing, imports the project if its import cache is stale, runs the export, and reports the errors it raised
and the files it wrote. --pack writes only the game's data (.pck), which needs no templates.

--smoke SECONDS then runs the build as a --binary session runs a program (blackbox.py), on a display of gdh's, for that
long, and fails on a crash, an early exit or engine errors in its log; it saves the screen at the end. A pack runs on
gdh's Godot (--main-pack), which is the editor build, so OS.has_feature("editor") is true there as it isn't in an export.
"""
import configparser
import os
import re
import shutil
import subprocess
from pathlib import Path

from gdh import blackbox
from gdh.api import godot_version
from gdh.editor_bridge import parse_engine_errors
from gdh.godot import GdhError, alert_shims, build_csharp, csharp_project, godot_binary, godot_env
from gdh.imports import ensure_imported


def read_presets(project):
    """[{"name", "platform", "export_path"}] from export_presets.cfg, in order."""
    path = Path(project) / "export_presets.cfg"
    if not path.exists():
        return []
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string(path.read_text())
    presets = []
    for section in parser.sections():
        if re.fullmatch(r"preset\.\d+", section):
            p = parser[section]
            presets.append({k: p.get(k, "").strip('"') for k in ("name", "platform", "export_path")})
    return presets


def templates_dir(version, csharp):
    """Where Godot looks for this build's export templates: <data>/godot/export_templates/<x.y.z.status[.mono]>. The
    .NET build (its version says mono) looks in the .mono directory for any project, C# or not."""
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    parts = version.split(".")
    name = ".".join(parts[:4]) if len(parts) >= 4 and not parts[3].isdigit() else ".".join(parts[:3])
    if csharp or "mono" in parts[3:5]:
        name += ".mono"
    return Path(base) / "godot" / "export_templates" / name


def cmd_export(args):
    project = Path(args.project).resolve()
    if not (project / "project.godot").exists():
        raise GdhError(f"No project.godot in {project}.")
    presets = read_presets(project)
    if args.list or not args.preset:
        if not presets:
            raise GdhError("The project has no export presets (export_presets.cfg). Add one in the editor's Project > "
                           "Export dialog: it needs choices only a person can make (platform, icon, signing).")
        for p in presets:
            print(f"{p['name']}   [{p['platform']}]   {p['export_path'] or '(no export path)'}")
        return 0
    chosen = next((p for p in presets if p["name"] == args.preset), None)
    if chosen is None:
        names = ", ".join(p["name"] for p in presets) or "none"
        raise GdhError(f"No export preset named {args.preset!r}. The project's presets: {names}.")
    out = args.out or chosen["export_path"]
    if not out:
        raise GdhError(f"The preset {args.preset!r} has no export path: pass --out.")
    out_path = (project / out).resolve() if not os.path.isabs(out) else Path(out)
    if args.pack and out_path.suffix not in (".pck", ".zip"):
        out_path = out_path.with_suffix(".pck")
    if args.smoke is not None and not args.pack and chosen["platform"] not in ("Linux", "Linux/X11"):
        raise GdhError(f"--smoke runs a Linux build, and the preset {args.preset!r} is for {chosen['platform']}.")
    binary = godot_binary(project)
    csharp = csharp_project(project) is not None
    if not args.pack:
        templates = templates_dir(godot_version(binary), csharp)
        if not templates.is_dir():
            raise GdhError(f"Godot's export templates for this version aren't installed ({templates} is missing). "
                           f"Install them from the editor (Editor > Manage Export Templates), or download the "
                           f"matching .tpz from godotengine.org. --pack exports only the game's data, without them.")
    if not args.no_build:
        build_csharp(project)
    ensure_imported(project)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    flag = "--export-pack" if args.pack else ("--export-debug" if args.debug else "--export-release")
    cmd = [binary, "--headless", "--path", str(project), flag, args.preset, str(out_path)]
    before = out_path.stat().st_mtime_ns if out_path.exists() else None
    with alert_shims() as shims:
        # The real data directory: that's where the export templates are.
        env = godot_env(shims, ":gdh-no-display", game=False)
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=args.timeout,
                              stdin=subprocess.DEVNULL)
    log = proc.stdout + proc.stderr
    errors = [e for e in parse_engine_errors(log) if e["type"] != "warning"]
    wrote = out_path.exists() and out_path.stat().st_mtime_ns != before
    if proc.returncode != 0 or not wrote:
        detail = "\n".join(f"  {e['type']}: {e['message']} at {e['where']}" for e in errors[:15])
        tail = "\n".join(log.splitlines()[-15:])
        raise GdhError(f"The export failed (exit {proc.returncode}). " + (detail or tail))
    files = sorted(p for p in out_path.parent.iterdir() if p.is_file() and p.stat().st_mtime_ns > (before or 0))
    print(f"gdh export: {args.preset} ({'pack' if args.pack else 'debug' if args.debug else 'release'}) -> {out_path}")
    for f in files:
        print(f"  {f.name}  {f.stat().st_size / 1e6:.1f} MB")
    if errors:
        print(f"{len(errors)} errors while exporting:")
        for e in errors[:15]:
            print(f"  {e['type']}: {e['message']} at {e['where']}")
    if args.smoke is not None:
        return smoke(args, binary, out_path)
    return 0


def smoke(args, binary, out_path):
    """Run the build black-box for --smoke seconds: a pack on gdh's Godot, an export as it is."""
    if args.pack:
        cmd = blackbox.command(shutil.which(binary) or binary, args.game_args, args.resolution, out_path)
        cmd[1:1] = ["--main-pack", str(out_path)]
    elif blackbox.export_pack(out_path):
        cmd = blackbox.command(out_path, args.game_args, args.resolution, out_path)
    else:
        raise GdhError(f"--smoke found no pack in or beside {out_path}, so it isn't a Godot build it can run.")
    out = Path(args.smoke_out or Path.cwd() / "captures" / "smoke" / re.sub(r"[^A-Za-z0-9_.-]", "_", args.preset))
    passed, lines = blackbox.smoke(cmd, args.smoke, out.resolve(), args.display, args.resolution)
    print(f"smoke: {'passed' if passed else 'FAILED'}: " + "\n".join(lines))
    if not passed:
        raise GdhError(f"The smoke run of {out_path.name} failed (above).")
    return 0


def add_parser(sub, add_display_option):
    p = sub.add_parser("export", help="Export the project with one of its presets, headless")
    p.add_argument("--project", required=True, help="Godot project directory")
    p.add_argument("--preset", help="The export preset's name (without it, the presets are listed)")
    p.add_argument("--out", help="Where to write (default: the preset's export path, relative to the project)")
    p.add_argument("--debug", action="store_true", help="A debug build instead of a release one")
    p.add_argument("--pack", action="store_true", help="Only the game's data (.pck): needs no export templates")
    p.add_argument("--list", action="store_true", help="List the project's presets")
    p.add_argument("--timeout", type=int, default=1800, help="Seconds before the export is stopped (default 1800)")
    p.add_argument("--no-build", action="store_true", help="Don't build a C# project's assemblies first")
    p.add_argument("--smoke", type=float, metavar="SECONDS",
                   help="Then run the build off-screen for SECONDS, and fail on a crash, an early exit or engine errors "
                        "in its log; saves the screen at the end (arguments after -- go to the game)")
    p.add_argument("--smoke-out", metavar="DIR", help="The smoke run's log and screen (default: ./captures/smoke/<preset>)")
    p.add_argument("--resolution", default="1280x720", help="The smoke run's window (default 1280x720)")
    add_display_option(p)
    p.set_defaults(func=cmd_export, game_args=[])
