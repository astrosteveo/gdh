"""gdh live onion and filmstrip, and gdh measure onion, filmstrip and changes: motion in one image (motion.py) from a
live session's frames, or saved ones.

A live command steps the frames with the step's own input options, saves every Kth frame, and tracks where the node
is on each (harness/track.gd), so the box framed is the one the node moved through.
"""
import json
import math
import sys
from pathlib import Path

from PIL import Image

from gdh import motion
from gdh.godot import GdhError

# Without --every, an onion skin takes about this many frames of the run, and a filmstrip this many.
ONION_GHOSTS = 8
FILMSTRIP_CELLS = 12


class MotionCliError(GdhError):
    pass


def every_for(frames, every, wanted):
    """The frames between saved ones: --every, or about `wanted` frames spread over the run."""
    if every is not None:
        if every < 1:
            raise MotionCliError("--every takes 1 or more.")
        return every
    return max(1, math.ceil(frames / wanted))


def tracked_run(session, args, frames, every):
    """Step `frames` frames with the command's input, saving every `every`th frame and tracking args.node. Returns
    ([(label, path)], the node's box at each saved frame, the image size, the step's result)."""
    from gdh.live import LiveError, call, pick, report, step_input_events
    if frames < 1:
        raise MotionCliError("Step 1 frame or more.")
    if every > frames:
        raise MotionCliError(f"--every {every} is more than the {frames} frames stepped, so no frame would be saved.")
    chosen = pick(session, args.instance)
    if len(chosen) != 1:
        raise LiveError("Give one instance (--instance K): the image is made from one game's frames.")
    step = {"frames": frames, "events": step_input_events(args, frames), "shot_every": every, "track": [args.node]}
    reply = call(session, "step", step, instance=args.instance, timeout=max(300, frames * 5))
    result = report(reply, args.json, echo=False)
    if isinstance(result, list):
        result = result[chosen[0]]
    shots = result.get("shots", [])
    if not shots:
        raise MotionCliError("The step saved no frames.")
    at = dict((frame, box) for frame, box in result["track"][args.node]["rows"])
    boxes = [at.get(frame) for frame in result["shot_frames"]]
    with Image.open(shots[0]) as img:
        size = img.size
    return [(f"frame {frame}", path) for frame, path in zip(result["shot_frames"], shots)], boxes, size, result


def framed(args, boxes, size, frames):
    """The box an image of the node frames: round where it was on every saved frame, with --margin."""
    if args.margin < 0:
        raise MotionCliError("--margin takes pixels, 0 or more.")
    box = motion.union_box(boxes, args.margin, size)
    if box is None:
        raise MotionCliError(f"{args.node} didn't show on screen in any of the {frames} frames saved: it was hidden, "
                             f"off screen or behind the camera.")
    return box


def finish(frames, keep):
    """Delete the frames a live command saved, unless --keep."""
    if keep:
        return
    for _, path in frames:
        Path(path).unlink(missing_ok=True)


def report_image(kind, info, as_json, extra=None):
    info = {**info, **(extra or {})}
    if as_json:
        print(json.dumps(info, indent=1))
        return
    box = ",".join(str(v) for v in info["box"])
    print(f"{kind}: {info['out']} ({len(info['frames'])} frames, {info['frames'][0]} to {info['frames'][-1]}, the box "
          f"{box}, zoom {info['zoom']})")
    sys.stdout.flush()
    for note in info.get("notes", []):
        print(f"note: {note}", file=sys.stderr)


def default_out(session, kind, frames):
    first, last = frames[0][0].removeprefix("frame "), frames[-1][0].removeprefix("frame ")
    return Path(session["out"]) / "motion" / f"{kind}-{first}-{last}.png"


# --- gdh live onion ------------------------------------------------------------------------------------------------


def cmd_live_onion(args):
    from gdh.live import load_session
    session = load_session(args.session)
    every = every_for(args.frames, args.every, ONION_GHOSTS)
    frames, boxes, size, result = tracked_run(session, args, args.frames, every)
    try:
        box = framed(args, boxes, size, len(frames))
        out = Path(args.out) if args.out else default_out(session, "onion", frames)
        info = motion.onion(frames, box, out, args.zoom, args.tint, args.threshold)
    except motion.MotionError as e:
        raise MotionCliError(str(e)) from None
    finally:
        finish(frames, args.keep)
    report_image("onion", info, args.json, {"node": args.node, "every": every, "status": result["status"],
                                            **({"kept": [p for _, p in frames]} if args.keep else {})})
    return 0


# --- gdh live filmstrip --------------------------------------------------------------------------------------------


def cmd_live_filmstrip(args):
    from gdh.live import load_session
    session = load_session(args.session)
    every = every_for(args.frames, args.every, FILMSTRIP_CELLS)
    frames, boxes, size, result = tracked_run(session, args, args.frames, every)
    try:
        box = framed(args, boxes, size, len(frames))
        out = Path(args.out) if args.out else default_out(session, "filmstrip", frames)
        info = motion.filmstrip(frames, box, out, args.zoom)
    except motion.MotionError as e:
        raise MotionCliError(str(e)) from None
    finally:
        finish(frames, args.keep)
    report_image("filmstrip", info, args.json, {"node": args.node, "every": every, "status": result["status"],
                                                **({"kept": [p for _, p in frames]} if args.keep else {})})
    return 0


# --- gdh measure onion and filmstrip -------------------------------------------------------------------------------


def saved_frames(sources, every):
    """PNG frames from files and directories, every Kth of them, each labeled with its place in the run."""
    from gdh.measure import MeasureError, frame_paths
    try:
        paths = frame_paths(sources)
    except MeasureError as e:
        raise MotionCliError(str(e)) from None
    if not paths:
        raise MotionCliError("No frames: give PNG files or directories holding them.")
    if every < 1:
        raise MotionCliError("--every takes 1 or more.")
    picked = [(f"frame {i}", p) for i, p in enumerate(paths) if i % every == 0]
    with Image.open(picked[0][1]) as img:
        size = img.size
    return picked, size


def cmd_measure_onion(args):
    try:
        frames, size = saved_frames(args.frames, args.every or 1)
        info = motion.onion(frames, motion.parse_box(args.box, size), args.out, args.zoom, args.tint, args.threshold)
    except motion.MotionError as e:
        raise MotionCliError(str(e)) from None
    report_image("onion", info, args.json)
    return 0


def cmd_measure_filmstrip(args):
    try:
        frames, size = saved_frames(args.frames, args.every or 1)
        info = motion.filmstrip(frames, motion.parse_box(args.box, size), args.out, args.zoom)
    except motion.MotionError as e:
        raise MotionCliError(str(e)) from None
    report_image("filmstrip", info, args.json)
    return 0


def cmd_measure_changes(args):
    from gdh.measure import MeasureError, frame_paths
    try:
        paths = frame_paths(args.frames)
        crops = None if args.no_crops else Path(args.out).with_name(Path(args.out).stem + "-crops")
        info = motion.changes(paths, args.out, args.threshold, args.still, crops)
    except (motion.MotionError, MeasureError) as e:
        raise MotionCliError(str(e)) from None
    if args.json:
        print(json.dumps(info, indent=1))
    else:
        print(describe_changes(info))
    return 0


def describe_changes(info, prefix=""):
    """A change map's result in a few lines."""
    lines = [f"{prefix}changes over {info['frames']} frames: {info['changed_share']:.3%} of pixels ({info['changed_px']}) "
             f"changed by more than {info['threshold']:g} in at least one of the {info['steps']} steps, "
             f"{info['every_step_px']} in every one"]
    for i, r in enumerate(info["regions"][:5], 1):
        crop = f"  [{r['crop']}]" if r.get("crop") else ""
        lines.append(f"{prefix}  {i}: {r['px']} px in the box {','.join(map(str, r['box']))}, changed in up to "
                     f"{r['most_steps']} of {info['steps']} steps{crop}")
    if info["region_count"] > 5:
        lines.append(f"{prefix}  ... {info['region_count'] - 5} more regions (--json has the largest 10)")
    lines.append(f"{prefix}change map: {info['out']}")
    return "\n".join(lines)


# --- Parsers ---------------------------------------------------------------------------------------------------------


def onion_options(p):
    p.add_argument("--zoom", type=int, default=0, metavar="Z",
                   help="Scale the box up Z times (nearest neighbour); default about 640 px on its long side, at most "
                        "1280 px wide")
    p.add_argument("--tint", action="store_true", help="Tint each ghost by its age, blue (oldest) to orange")
    p.add_argument("--threshold", type=float, default=motion.ONION_THRESHOLD, metavar="T",
                   help="A pixel is part of a frame's moving part when a channel differs from the background by more "
                        "than T, of 255 (default 12)")


def add_live_parsers(commands, command):
    """gdh live onion and filmstrip. `command` is live.py's maker of a subcommand."""
    from gdh.live import add_input_options

    def tracked(name, func, help, wanted):
        p = command(name, func, help,
                    instance="Which instance's frames, and who gets the input (default 0). Every instance steps")
        p.add_argument("frames", type=int, help="How many frames to step")
        p.add_argument("--node", required=True, metavar="PATH",
                       help="The node to frame (a path from the current scene, or /root/...): the image covers where "
                            "it and what it draws went over the run")
        p.add_argument("--every", type=int, metavar="K",
                       help=f"Take every Kth frame (default: about {wanted} frames over the run)")
        p.add_argument("--margin", type=float, default=12, metavar="PX",
                       help="Grow the box by PX on each side (default 12)")
        p.add_argument("--out", metavar="FILE.png",
                       help=f"Where it goes (default <session out>/motion/{name}-FIRST-LAST.png)")
        p.add_argument("--keep", action="store_true", help="Keep the frames it saved (by default they're deleted)")
        return p

    p = tracked("onion", cmd_live_onion, "Step and lay the frames of a node's movement over each other in one image: "
                                         "an onion skin, the oldest faintest", ONION_GHOSTS)
    onion_options(p)
    add_input_options(p)
    p = tracked("filmstrip", cmd_live_filmstrip, "Step and put the same box round a node from each frame side by "
                                                 "side, labeled with its game frame", FILMSTRIP_CELLS)
    filmstrip_options(p)
    add_input_options(p)


def filmstrip_options(p):
    p.add_argument("--zoom", type=int, default=0, metavar="Z",
                   help="Scale each frame's box up Z times (nearest neighbour); default about 640 px on its long side, "
                        "less to fit at least 4 across in 1280 px")


def add_measure_parsers(kinds):
    """gdh measure onion, filmstrip and changes. `kinds` is gdh measure's subparsers."""
    k = kinds.add_parser("onion", help="Lay saved frames over each other in one image: an onion skin, the oldest "
                                       "faintest, for a movement's path, spacing and shape")
    k.add_argument("frames", nargs="+", help="PNG files, or directories of them (taken in name order)")
    k.add_argument("--out", required=True, metavar="PNG", help="Where it goes")
    k.add_argument("--box", metavar="X0,Y0,X1,Y1", help="Only this part of the frames (default all of it)")
    k.add_argument("--every", type=int, metavar="K", help="Take every Kth frame (default every one)")
    k.add_argument("--json", action="store_true", help="Print the whole result")
    onion_options(k)
    k.set_defaults(func=cmd_measure_onion)
    k = kinds.add_parser("filmstrip", help="Put the same box from each saved frame side by side, labeled, for a pose, "
                                           "a flash or a transition frame by frame")
    k.add_argument("frames", nargs="+", help="PNG files, or directories of them (taken in name order)")
    k.add_argument("--out", required=True, metavar="PNG", help="Where it goes")
    k.add_argument("--box", metavar="X0,Y0,X1,Y1", help="Only this part of the frames (default all of it)")
    k.add_argument("--every", type=int, metavar="K", help="Take every Kth frame (default every one)")
    k.add_argument("--json", action="store_true", help="Print the whole result")
    filmstrip_options(k)
    k.set_defaults(func=cmd_measure_filmstrip)
    k = kinds.add_parser("changes", help="Where a run of frames changed and how often, in one image: each pixel by how "
                                         "many of the frame-to-frame steps it changed in, with the regions boxed")
    k.add_argument("frames", nargs="+", help="PNG files, or directories of them (taken in name order)")
    k.add_argument("--out", required=True, metavar="PNG", help="Where the map goes")
    k.add_argument("--threshold", type=float, default=motion.CHANGE_THRESHOLD, metavar="T",
                   help="A pixel changed in a step when a channel differs by more than T, of 255 (default 2)")
    k.add_argument("--still", action="store_true", help="Tint the pixels that never changed green")
    k.add_argument("--no-crops", action="store_true",
                   help="Don't crop the largest regions (by default into <out>-crops/: the first frame, the last and "
                        "the map side by side)")
    k.add_argument("--json", action="store_true", help="Print the whole result")
    k.set_defaults(func=cmd_measure_changes)
