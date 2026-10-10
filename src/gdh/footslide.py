"""gdh live footslide: feet that slide along the ground while they should be planted.

A foot is a Skeleton3D's bone. Over a run, each foot's height is its world position's y above the lowest it gets (or
--ground), and it's planted while that's within --height of it. A planted foot should stay put in the world; one that
moves along the ground while planted is sliding: the animation and the character's movement don't agree. Each foot's
planted frames, its slide (the ground distance it covered while planted) and its fastest frames are reported, with
crops of the worst frames.
"""
import json
import math
import re
from pathlib import Path

from PIL import Image, ImageDraw

from gdh.feel import Driver, FeelError

SIDES = {"left": ("left", "l"), "right": ("right", "r")}
# Bone names that are a foot, best first.
FOOT_WORDS = ("foot", "ankle", "toe", "ball")


class FootError(FeelError):
    pass


def words(name):
    """A bone's name as lower-case words: "mixamorig:LeftFoot" is mixamorig, left, foot; "foot.L" is foot, l."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [w for w in re.split(r"[^a-z0-9]+", spaced.lower()) if w]


def guess_feet(names):
    """The left and right foot bones among a skeleton's bone names: [names], left first."""
    picked = []
    for side, marks in SIDES.items():
        others = [m for s, ms in SIDES.items() if s != side for m in ms]
        best = None
        for name in names:
            w = words(name)
            if not any(m in w for m in marks) or any(m in w for m in others):
                continue
            for rank, word in enumerate(FOOT_WORDS):
                if any(x.startswith(word) for x in w):
                    if best is None or (rank, len(w)) < best[0]:
                        best = ((rank, len(w)), name)
                    break
        if best:
            picked.append(best[1])
    return picked


def foot_report(name, rows, ticks, ground, height, threshold):
    """One foot's run: rows are [(frame, [x, y, z], screen [x, y] or None)]."""
    ys = [p[1] for _, p, _ in rows]
    floor = min(ys) if ground is None else ground
    planted = [p[1] <= floor + height for _, p, _ in rows]
    speeds, slide, sliding = [], 0.0, []
    for i in range(1, len(rows)):
        if not (planted[i] and planted[i - 1]):
            continue
        (f0, a, _), (f1, b, screen) = rows[i - 1], rows[i]
        d = math.hypot(b[0] - a[0], b[2] - a[2])
        speed = d * ticks / max(f1 - f0, 1)
        slide += d
        speeds.append((f1, speed, screen))
        if speed > threshold:
            sliding.append((f1, speed, screen))
    worst = max(sliding, key=lambda s: s[1]) if sliding else None
    return {"bone": name, "ground": round(floor, 4), "planted_frames": sum(planted), "frames": len(rows),
            "slide": round(slide, 4), "sliding_frames": len(sliding),
            "worst": {"frame": worst[0], "speed": round(worst[1], 3)} if worst else None,
            "_sliding": sliding}


def crops(frames, worst, out, size=160, zoom=3):
    """The region round each of the worst slides' feet, from that frame's shot, zoomed, marked and labeled, side by
    side. worst is [(bone, frame, speed, screen)]."""
    from gdh.compare import grid
    cells = []
    for bone, frame, speed, screen in worst:
        path = frames.get(frame)
        if path is None or screen is None:
            continue
        with Image.open(path) as img:
            x, y = screen
            half = size // 2
            box = (int(x) - half, int(y) - half, int(x) + half, int(y) + half)
            crop = img.convert("RGB").crop(box).resize((size * zoom, size * zoom), Image.NEAREST)
        d = ImageDraw.Draw(crop)
        c = half * zoom
        d.ellipse((c - 14, c - 14, c + 14, c + 14), outline=(255, 60, 60), width=3)
        cells.append((f"{bone}, frame {frame}: {speed:.2f} m/s", crop))
    if not cells:
        return None
    return grid(cells, out, min(len(cells), 3), "planted feet sliding, worst first")["out"]


def cmd_footslide(args):
    from gdh.live import step_input_events
    game = Driver(args)
    ref = f"get_node({json.dumps(args.skeleton)})"
    if not game.eval(f"has_node({json.dumps(args.skeleton)})"):
        raise FootError(f"No node at {args.skeleton}.")
    if not game.eval(f"{ref}.is_class('Skeleton3D')"):
        raise FootError(f"{args.skeleton} isn't a Skeleton3D: footslide follows a 3D skeleton's bones.")
    count = game.eval(f"{ref}.get_bone_count()")
    names = game.eval("[" + ", ".join(f"{ref}.get_bone_name({i})" for i in range(count)) + "]") if count else []
    feet = [b.strip() for b in args.bones.split(",")] if args.bones else guess_feet(names)
    missing = [b for b in feet if b not in names]
    if missing or not feet:
        raise FootError(f"No bone {missing[0]!r} in {args.skeleton}: its bones are {', '.join(names)}." if missing else
                        f"No foot bones found among {', '.join(names) or 'no bones'}: name them with --bones L,R.")
    trace = []
    for bone in feet:
        world = f"{ref}.global_transform * {ref}.get_bone_global_pose({names.index(bone)}).origin"
        trace += [world, f"get_viewport().get_camera_3d().unproject_position({world})"]
    result = game.step(args.frames, step_input_events(args, args.frames), trace=trace,
                       shot_every=1 if args.crops else 0)
    shots = dict(zip(result.get("shot_frames", []), result.get("shots", [])))
    ticks = game.ticks
    reports = []
    for k, bone in enumerate(feet):
        rows = [(row[0], row[1 + 2 * k], row[2 + 2 * k]) for row in result["trace"]["rows"] if row[1 + 2 * k]]
        reports.append(foot_report(bone, rows, ticks, args.ground, args.height, args.threshold))
    # The worst frame of each foot first, then the next worst of any.
    every = sorted(((r["bone"], f, s, screen) for r in reports for f, s, screen in r["_sliding"]), key=lambda w: -w[2])
    firsts = [next(w for w in every if w[0] == r["bone"]) for r in reports if r["_sliding"]]
    worst = (sorted(firsts, key=lambda w: -w[2]) + [w for w in every if w not in firsts])[:args.crops]
    out = {"skeleton": args.skeleton, "feet": feet, "frames": args.frames, "ticks_per_second": ticks,
           "height": args.height, "threshold": args.threshold,
           "reports": [{k: v for k, v in r.items() if not k.startswith("_")} for r in reports]}
    if worst:
        path = Path(args.out) if args.out else game.out_dir("footslide") / "footslide.png"
        out["crops"] = crops(shots, worst, path)
    for p in shots.values():
        Path(p).unlink(missing_ok=True)
    if args.json:
        print(json.dumps(out, indent=1))
        return 0
    print(f"footslide of {args.skeleton} over {args.frames} frames, feet {', '.join(feet)}:")
    for r in out["reports"]:
        if r["sliding_frames"]:
            print(f"  {r['bone']}: planted {r['planted_frames']} of {r['frames']} frames, and slid {r['slide']:g} m while "
                  f"planted: {r['sliding_frames']} frames faster than {args.threshold:g} m/s, {r['worst']['speed']:g} m/s "
                  f"at worst (frame {r['worst']['frame']})")
        else:
            print(f"  {r['bone']}: planted {r['planted_frames']} of {r['frames']} frames, slid {r['slide']:g} m while "
                  f"planted, never faster than {args.threshold:g} m/s")
    if out.get("crops"):
        print(f"crops: {out['crops']}")
    return 0


def add_live_parsers(commands, command):
    """gdh live footslide. `command` is live.py's maker of a subcommand."""
    from gdh.live import add_input_options
    p = command("footslide", cmd_footslide, "Feet sliding while planted: a 3D skeleton's foot bones over a run, the "
                                            "frames each is on the ground and how far and fast it moved then",
                instance="Which game instance: a number (default 0). Every instance steps")
    p.add_argument("frames", type=int, help="How many frames to step")
    p.add_argument("--skeleton", required=True, metavar="PATH", help="The Skeleton3D")
    p.add_argument("--bones", metavar="L,R",
                   help="The foot bones' names (default: guessed from the names, foot before ankle before toe, by "
                        "left and right)")
    p.add_argument("--height", type=float, default=0.02, metavar="M",
                   help="A foot is planted within this height of the ground (default 0.02 m)")
    p.add_argument("--ground", type=float, metavar="Y",
                   help="The ground's height (default: each foot's lowest over the run)")
    p.add_argument("--threshold", type=float, default=0.1, metavar="M/S",
                   help="A planted foot moving faster than this along the ground is sliding (default 0.1 m/s)")
    p.add_argument("--crops", type=int, default=3, metavar="N",
                   help="Crops of the N worst sliding frames, in one image (default 3; 0: none, and no frames saved)")
    p.add_argument("--out", metavar="FILE.png", help="Where the crops go (default <session out>/feel/footslide/)")
    add_input_options(p)
