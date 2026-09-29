"""gdh: run Godot scenes off-screen on the real GPU and collect what they render.

Godot runs under Xvfb with the Vulkan driver, so windows never reach the
user's desktop. Environment: GODOT (binary, default "godot"), GDH_GPU_INDEX
(Vulkan device index; default lets Godot choose).
"""
import argparse
import sys

from gdh import live
from gdh.capture import cmd_capture, cmd_import


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
    cap.add_argument("--timeout", type=int, default=120, help="Seconds per scene")
    cap.add_argument("--tiles", action="store_true", help="Also save normal.png as 2x2 tiles at 2x zoom")
    cap.set_defaults(func=cmd_capture)

    imp = sub.add_parser("import", help="Import project assets headlessly")
    imp.add_argument("--project", required=True)
    imp.set_defaults(func=cmd_import)

    live.add_parsers(sub)

    args = parser.parse_args()
    try:
        sys.exit(args.func(args))
    except live.LiveError as e:
        sys.stdout.flush()
        print(f"gdh: {e}", file=sys.stderr)
        sys.exit(1)
