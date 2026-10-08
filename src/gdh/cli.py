"""gdh: run Godot scenes off-screen on the real GPU and collect what they render.

Godot runs with Vulkan on a display of its own, so windows never reach the
user's desktop: a virtual display the GPU presents to (weston and Xwayland),
or Xvfb when that can't start (--display). Environment: GODOT (the binary;
default godot-mono for a C# project and godot otherwise), GDH_GPU_INDEX
(Vulkan device index; default lets Godot choose), GDH_DISPLAY (the default
for --display: auto, gpu or xvfb). A C# project is built with `dotnet build`
first.

Arguments after `--` go to the game (capture, live start and movie), where
OS.get_cmdline_user_args() returns exactly them. `gdh editor` opens scenes in
the Godot editor itself and saves what it shows.
"""
import argparse
import sys

from gdh import api, editor, editor_bridge, export, live, measure_cli, movie, testing
from gdh.capture import cmd_capture
from gdh.display import default_choice
from gdh.godot import GdhError
from gdh.imports import cmd_import


def main():
    parser = argparse.ArgumentParser(prog="gdh", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", help="Render scenes and save one PNG per debug view")
    cap.add_argument("--project", required=True, help="Godot project directory")
    cap.add_argument("--scene", required=True, action="append", help="res:// path; repeatable")
    cap.add_argument("--out", required=True, help="Output directory")
    cap.add_argument("--modes", help="Comma list: normal,unshaded,lighting,normals,wireframe,overdraw")
    cap.add_argument("--warmup", type=int, default=30, help="Frames to render before capturing")
    cap.add_argument("--resolution", default="1280x720")
    live.add_display_option(cap)
    cap.add_argument("--timeout", type=int, default=120, help="Seconds per scene")
    cap.add_argument("--tiles", action="store_true", help="Also save normal.png as 2x2 tiles at 2x zoom")
    cap.add_argument("--baseline", metavar="DIR",
                     help="Compare each view with the PNG of its name here (a subdirectory per scene, as --out); "
                          "exit 1 when one changed past --tolerance")
    cap.add_argument("--update-baseline", action="store_true",
                     help="Write the views into --baseline (changes are listed, never failed)")
    cap.add_argument("--tolerance", type=float, default=0.0, metavar="PCT",
                     help="With --baseline: the percent of a view's pixels that may change (0)")
    cap.add_argument("--threshold", type=float, default=2.0,
                     help="With --baseline: a pixel has changed when a channel differs by more than this (2, of 255)")
    cap.add_argument("--no-build", action="store_true", help="Don't build a C# project's assemblies first")
    cap.add_argument("--no-import", action="store_true",
                     help="Don't import the project first when its import cache is missing or stale")
    cap.set_defaults(func=cmd_capture, game_args=[])

    imp = sub.add_parser("import", help="Import project assets headlessly")
    imp.add_argument("--project", required=True)
    imp.add_argument("--no-build", action="store_true", help="Don't build a C# project's assemblies first")
    imp.set_defaults(func=cmd_import)

    live.add_parsers(sub)
    editor.add_parser(sub, live.add_display_option)
    editor_bridge.add_parser(sub)
    api.add_parser(sub)
    testing.add_parser(sub)
    export.add_parser(sub)
    movie.add_parser(sub, live.add_display_option)
    measure_cli.add_parsers(sub)

    argv = sys.argv[1:]
    game_args = []
    if "--" in argv:
        split = argv.index("--")
        argv, game_args = argv[:split], argv[split + 1:]
    args = parser.parse_args(argv)
    if game_args and not hasattr(args, "game_args"):
        parser.error("arguments after -- go to the game: only capture, live start and movie take them")
    if hasattr(args, "game_args"):
        args.game_args = game_args
    try:
        if getattr(args, "display", False) is None:
            args.display = default_choice()
        sys.exit(args.func(args))
    except GdhError as e:
        sys.stdout.flush()
        print(f"gdh: {e}", file=sys.stderr)
        sys.exit(1)
