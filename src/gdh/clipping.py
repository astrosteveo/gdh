"""Clipping and pop-in over a run: gdh live clipping and gdh measure popin.

Clipping is found in the game, exactly: each frame, a character's points (its bones, its meshes' vertices) are tested
against solid collision shapes (harness/clip.gd). The frames a point spent inside one are grouped into runs.

Pop-in is found in the pixels of a recording, from any game. What moves between two frames is explained by shifting the
frame before (each block by whatever offset within POP_REACH pixels matches it best); what's left is change that no
movement explains. While the camera moves smoothly that's small, and about the same from frame to frame: a block
whose unexplained change is far more in one frame than in the frames before and after it has popped (a mesh switching its LOD or appearing at
the end of its visibility range, a texture streaming in, a light switching on), as long as it stays changed after:
something passing through the block (a post whipping past the edge of the view) leaves it as it was. A frame where
most of the screen pops is a cut, and is listed as one.
"""
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from gdh.feel import Driver, FeelError

POP_BLOCK = 24
# How far a block may have moved between frames, in pixels, and still count as moving rather than popping.
POP_REACH = 32
POP_RATIO = 4.0
POP_FLOOR = 12.0
POP_WINDOW = 3
PERSIST = 2
CUT_SHARE = 0.3


class ClipError(FeelError):
    pass


# --- gdh live clipping ---------------------------------------------------------------------------------------------


def runs_of(hits):
    """The hits ([[frame, [{point, collider, at, screen?}]]]) as runs of consecutive frames for each point and collider:
    [{"point", "collider", "first", "last", "frames", "screen", "at"}], longest first."""
    open_runs, done = {}, []
    for frame, found in hits:
        for h in found:
            key = (h["point"], h["collider"])
            run = open_runs.get(key)
            if run and run["last"] == frame - 1:
                run["last"] = frame
                run["frames"] += 1
            else:
                if run:
                    done.append(run)
                open_runs[key] = {"point": h["point"], "collider": h["collider"], "first": frame, "last": frame,
                                  "frames": 1, "screen": h.get("screen"), "at": h["at"]}
    done += open_runs.values()
    return sorted(done, key=lambda r: (-r["frames"], r["first"]))


def cmd_clipping(args):
    from gdh.compare import grid
    from gdh.live import call, report, step_input_events
    game = Driver(args)
    reply = call(game.session, "step", {"frames": args.frames, "events": step_input_events(args, args.frames),
                                        "shot_every": 1 if args.crops else 0,
                                        "clip": {"node": args.node, "mask": args.mask}},
                 instance=args.instance, timeout=max(300, args.frames * 5))
    result = report(reply, args.json, echo=False)
    if isinstance(result, list):
        result = result[game.index]
    clip = result["clip"]
    shots = dict(zip(result.get("shot_frames", []), result.get("shots", [])))
    found = runs_of(clip["hits"])
    out = {"node": args.node, "frames": args.frames, "points": clip["points"],
           "frames_clipping": len(clip["hits"]), "runs": found}
    cells = []
    for run in found[:args.crops]:
        path = shots.get(run["first"])
        if path is None or not run["screen"]:
            continue
        with Image.open(path) as img:
            x, y = run["screen"]
            crop = img.convert("RGB").crop((int(x) - 80, int(y) - 80, int(x) + 80, int(y) + 80)).resize((480, 480),
                                                                                                        Image.NEAREST)
        d = ImageDraw.Draw(crop)
        d.ellipse((226, 226, 254, 254), outline=(255, 60, 60), width=3)
        cells.append((f"{run['point']} in {run['collider']}, frame {run['first']}", crop))
    if cells:
        target = Path(args.out) if args.out else game.out_dir("clipping") / "clipping.png"
        out["crops"] = grid(cells, target, min(len(cells), 3), f"{args.node}: inside solid geometry")["out"]
    for p in shots.values():
        Path(p).unlink(missing_ok=True)
    if args.json:
        print(json.dumps(out, indent=1))
        return 0 if not found or not args.fail else 1
    print(f"clipping of {args.node} over {args.frames} frames ({clip['points']} points tested a frame):")
    if not found:
        print("  no point inside solid geometry")
    for run in found[:20]:
        span = f"frame {run['first']}" if run["frames"] == 1 else f"frames {run['first']} to {run['last']}"
        print(f"  {run['point']} inside {run['collider']}: {span} ({run['frames']} frame{'s' if run['frames'] > 1 else ''}), "
              f"at {','.join(f'{v:g}' for v in run['at'])}")
    if len(found) > 20:
        print(f"  ... {len(found) - 20} more (--json has them all)")
    if out.get("crops"):
        print(f"crops: {out['crops']}")
    return 1 if found and args.fail else 0


# --- gdh measure popin ---------------------------------------------------------------------------------------------


def block_changes(paths, block=POP_BLOCK, reach=POP_REACH):
    """For each frame after the first, each block's change from the frame before that no movement explains: the mean
    difference left after shifting the frame before by whatever offset, within `reach` pixels each way, matches the
    block best (searched at a quarter of the size). Returns that, (frames - 1, rows, columns), and each block's mean
    luminance in each frame, (frames, rows, columns)."""
    from gdh.images import shrink
    from gdh.measure import lum
    q = 4  # the search runs at a quarter of the size
    b = max(1, block // q)
    r = max(1, reach // q)

    def small(path):
        a = lum(path)
        h, w = a.shape
        return shrink(a[:h - h % (b * q), :w - w % (b * q)], q, np.mean)

    def blocks(a):
        rows, cols = a.shape[0] // b, a.shape[1] // b
        return a[:rows * b, :cols * b].reshape(rows, b, cols, b).mean(axis=(1, 3))

    prev = small(paths[0])
    changes, means = [], [blocks(prev)]
    for path in paths[1:]:
        now = small(path)
        if now.shape != prev.shape:
            raise ClipError(f"{path} isn't the size of the frames before it.")
        padded = np.pad(prev, r, mode="constant", constant_values=1e6)  # off the frame matches nothing
        best = np.full(blocks(now).shape, np.inf)
        h, w = now.shape
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                shifted = padded[r + dy:r + dy + h, r + dx:r + dx + w]
                best = np.minimum(best, blocks(np.abs(now - shifted)))
        changes.append(best)
        means.append(blocks(now))
        prev = now
    return np.array(changes), np.array(means)


def popin(paths, out_dir=None, ratio=POP_RATIO, floor=POP_FLOOR, block=POP_BLOCK, window=POP_WINDOW):
    """The pops in a recording: [{"frame", "box", "blocks", "change", "around", "crop"?}] and the cuts."""
    from gdh.images import diff_crop
    from gdh.measure import components, rgb
    if len(paths) < 2 * window + 2:
        raise ClipError(f"Pop-in needs {2 * window + 2} frames or more: a frame's change is judged against the "
                        f"{window} before and after it.")
    changes, means = block_changes(paths, block)
    pops, cuts = [], []
    for t in range(len(changes)):
        around = [changes[i] for i in range(max(0, t - window), min(len(changes), t + window + 1)) if i != t]
        typical = np.median(np.stack(around), axis=0)
        spiking = changes[t] > ratio * typical + floor
        share = float(spiking.mean())
        frame = t + 1  # the change into frame t + 1 (indexed from 0)
        if share >= CUT_SHARE:  # most of the screen at once: a cut, or a flash
            cuts.append({"frame": frame, "share": round(share, 3)})
            continue
        # A pop stays: PERSIST frames on, the block is still unlike the frame before the change. Something passing
        # through (a post whipping past the edge of the view) leaves it as it was.
        later = min(t + 1 + PERSIST, len(means) - 1)
        jump = np.abs(means[t + 1] - means[t])
        spiking &= np.abs(means[later] - means[t]) >= 0.5 * jump
        labels, count = components(spiking)
        for r in range(1, count + 1):
            by, bx = np.nonzero(labels == r)
            size = max(1, block // 4) * 4
            box = [int(bx.min()) * size, int(by.min()) * size, int(bx.max() + 1) * size, int(by.max() + 1) * size]
            pops.append({"frame": frame, "box": box, "blocks": int(len(by)),
                         "change": round(float(changes[t][by, bx].mean()), 2),
                         "around": round(float(typical[by, bx].mean()), 2)})
    pops.sort(key=lambda p: -(p["change"] - p["around"]) * p["blocks"])
    if out_dir is not None:
        for i, pop in enumerate(pops[:10], 1):
            a, b = rgb(paths[pop["frame"] - 1]), rgb(paths[pop["frame"]])
            pop["crop"] = str(diff_crop(a, b, pop["box"], Path(out_dir) / f"{i:02d}-pop.png",
                                        (f"frame {pop['frame'] - 1}", f"frame {pop['frame']}")))
    return pops, cuts


def cmd_popin(args):
    from gdh.measure import MeasureError, frame_paths
    try:
        paths = frame_paths(args.frames)
    except MeasureError as e:
        raise ClipError(str(e)) from None
    pops, cuts = popin(paths, None if args.no_crops else Path(args.out or Path(paths[0]).parent.with_name(
        Path(paths[0]).parent.name + "-popin")), args.ratio, args.floor, args.block)
    out = {"frames": len(paths), "pops": pops, "cuts": cuts}
    if args.json:
        print(json.dumps(out, indent=1))
        return 1 if pops and args.fail else 0
    print(f"pop-in over {len(paths)} frames: {len(pops)} pop{'s' if len(pops) != 1 else ''}, {len(cuts)} cut"
          f"{'s' if len(cuts) != 1 else ''}")
    for p in pops[:20]:
        crop = f"  [{p['crop']}]" if p.get("crop") else ""
        print(f"  frame {p['frame']}: the box {','.join(map(str, p['box']))} changed by {p['change']:g} against "
              f"{p['around']:g} in the frames around it{crop}")
    for c in cuts:
        print(f"  frame {c['frame']}: a cut ({c['share']:.0%} of the screen changed at once)")
    return 1 if pops and args.fail else 0


# --- Parsers ---------------------------------------------------------------------------------------------------------


def add_live_parsers(commands, command):
    """gdh live clipping. `command` is live.py's maker of a subcommand."""
    from gdh.live import add_input_options
    p = command("clipping", cmd_clipping, "A character's bones and mesh points inside solid geometry, frame by frame, "
                                          "as runs of frames, with crops",
                instance="Which game instance: a number (default 0). Every instance steps")
    p.add_argument("frames", type=int, help="How many frames to step")
    p.add_argument("--node", required=True, metavar="PATH",
                   help="The character: every Skeleton3D's bones and every unskinned mesh's vertices under it are "
                        "tested, and its own collision bodies left out")
    p.add_argument("--mask", type=lambda v: int(v, 0), default=0xFFFFFFFF, metavar="BITS",
                   help="The collision layers to test against (default all)")
    p.add_argument("--crops", type=int, default=3, metavar="N",
                   help="Crops of the N longest runs, at their first frame (default 3; 0: none, and no frames saved)")
    p.add_argument("--out", metavar="FILE.png", help="Where the crops go (default <session out>/feel/clipping/)")
    p.add_argument("--fail", action="store_true", help="Exit 1 when a point was inside")
    add_input_options(p)


def add_measure_parsers(kinds):
    """gdh measure popin. `kinds` is gdh measure's subparsers."""
    k = kinds.add_parser("popin", help="Pop-in in a recording: blocks of pixels that change far more in one frame "
                                       "than in the frames around it, while the rest moves smoothly")
    k.add_argument("frames", nargs="+", help="PNG files, or directories of them (taken in name order)")
    k.add_argument("--out", metavar="DIR", help="Where the crops go (default <frames>-popin/ beside them)")
    k.add_argument("--ratio", type=float, default=POP_RATIO,
                   help=f"A block pops when its change is over RATIO times its typical change, plus --floor "
                        f"(default {POP_RATIO:g})")
    k.add_argument("--floor", type=float, default=POP_FLOOR,
                   help=f"Luminance levels a change must be over its typical change by, at least (default {POP_FLOOR:g})")
    k.add_argument("--block", type=int, default=POP_BLOCK, help=f"The block's size in pixels (default {POP_BLOCK})")
    k.add_argument("--no-crops", action="store_true", help="Don't crop the pops")
    k.add_argument("--fail", action="store_true", help="Exit 1 when something popped")
    k.add_argument("--json", action="store_true", help="Print the whole result")
    k.set_defaults(func=cmd_popin)
