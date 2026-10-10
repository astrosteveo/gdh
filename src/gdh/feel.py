"""Game feel in numbers: gdh live latency (T13), feel (T14), and hitstop, shake and follow (T15).

Each command drives a live session with ordinary steps, reading values with --trace's machinery and frames with
--shot-every's, and turns them into the numbers a designer tunes: frames from an input to a response, time to top
speed, a jump's height and airtime, the coyote and buffer windows, how long time stands still on a hit, a shake's
amplitude and decay, a camera's lag. A chart (chart.py) of each run goes beside the numbers.

Frames are physics frames: gdh runs a game at one physics tick a frame. Seconds are frames over the game's tick rate.
"""
import json
import math
import statistics
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from gdh import chart
from gdh.godot import GdhError


class FeelError(GdhError):
    pass


# --- Driving the game ---------------------------------------------------------------------------------------------


class Driver:
    """Steps and reads one instance of a session, printing the game's problems as they come (unless --json)."""

    def __init__(self, args):
        from gdh.live import load_session, pick
        self.args = args
        self.session = load_session(args.session)
        chosen = pick(self.session, getattr(args, "instance", "0"))
        if len(chosen) != 1:
            raise FeelError("Give one instance (--instance K): the numbers are one game's.")
        self.index = chosen[0]
        self.ticks = None

    def step(self, frames, events=(), trace=(), every=1, shot_every=0, track=None, until=None):
        from gdh.live import call, report
        request = {"frames": max(frames, 1), "events": list(events), "shot_every": shot_every}
        if trace or until:
            request["every"] = every
        if trace:
            request["trace"] = list(trace)
        if until:
            request["until"] = until
        if track:
            request["track"] = list(track)
        reply = call(self.session, "step", request, instance=self.args.instance, timeout=max(300, frames * 5))
        result = report(reply, self.args.json, echo=False)
        if isinstance(result, list):
            result = result[self.index]
        self.ticks = result["status"].get("ticks_per_second", 60)
        return result

    def eval(self, expr):
        from gdh.live import call, report
        result = report(call(self.session, "eval", {"expr": expr}, instance=self.args.instance), self.args.json,
                        echo=False)
        if isinstance(result, list):
            result = result[self.index]
        return result["value"]

    def out_dir(self, name):
        return Path(self.session["out"]) / "feel" / name


def press(token, at=0, until=None):
    """A press of INPUT at frame `at`, and its release at `until` when given."""
    from gdh.live import input_event
    events = [input_event(token, True, at)]
    if until is not None:
        events.append(input_event(token, False, until))
    return events


def numbers(values):
    """min, median and max of a list of numbers, or None for an empty one."""
    if not values:
        return None
    return {"min": min(values), "median": statistics.median(values), "max": max(values)}


def frames_text(stats, ticks):
    if stats is None:
        return "no response"
    ms = 1000 / ticks
    if stats["min"] == stats["max"]:
        return f"{stats['min']:g} frame{'' if stats['min'] == 1 else 's'} ({stats['min'] * ms:.0f} ms)"
    return (f"{stats['median']:g} frames median ({stats['median'] * ms:.0f} ms), {stats['min']:g} to "
            f"{stats['max']:g}")


def emit(result, as_json, lines):
    if as_json:
        print(json.dumps(result, indent=1))
    else:
        print("\n".join(lines))


# --- gdh live latency -----------------------------------------------------------------------------------------------

# A pixel in the node's box has changed when a channel differs from the frame before the input by more than this, and
# the box has when at least LATENCY_PIXELS of its pixels have.
LATENCY_THRESHOLD = 8.0
LATENCY_PIXELS = 4


def changed_at(baseline, frames, box, threshold=LATENCY_THRESHOLD, pixels=LATENCY_PIXELS):
    """The first frame ([(frame number, path)]) whose box differs from the baseline image's, as (index, pixels
    changed), or (None, 0)."""
    with Image.open(baseline) as img:
        base = np.asarray(img.convert("RGB").crop(box), dtype=np.int16)
    for i, (_, path) in enumerate(frames):
        with Image.open(path) as img:
            now = np.asarray(img.convert("RGB").crop(box), dtype=np.int16)
        count = int((np.abs(now - base).max(axis=2) > threshold).sum())
        if count >= pixels:
            return i, count
    return None, 0


def cmd_latency(args):
    from gdh.motion import union_box
    if not args.watch and not args.node:
        raise FeelError("latency needs what responds: --watch EXPR (a value) and/or --node PATH (its pixels).")
    if args.repeat < 1 or args.max < 1 or args.settle < 1:
        raise FeelError("--repeat, --max and --settle take 1 or more.")
    game = Driver(args)
    trials = []
    shots = []
    for trial in range(args.repeat):
        # Settle with no input, saving the last frame and reading the value: the baseline.
        before = game.step(args.settle, shot_every=args.settle if args.node else 0,
                           trace=[args.watch] if args.watch else (), every=args.settle,
                           track=[args.node] if args.node else None)
        start = before["status"]["frame"]
        base_value = before["trace"]["rows"][-1][1] if args.watch else None
        events = press(args.press, 0, args.max) if args.hold else press(args.press, 0, 1)
        run = game.step(args.max, events, trace=[args.watch] if args.watch else (), every=1,
                        shot_every=1 if args.node else 0, track=[args.node] if args.node else None)
        entry = {"start_frame": start}
        if args.watch:
            rows = run["trace"]["rows"]
            entry["state"] = next((row[0] - start for row in rows if row[1] != base_value), None)
            entry["value_before"] = base_value
            entry["value_after"] = next((row[1] for row in rows if row[1] != base_value), None)
        if args.node:
            boxes = [b for _, b in before["track"][args.node]["rows"][-1:]] + [b for _, b in run["track"][args.node]["rows"]]
            with Image.open(before["shots"][-1]) as img:
                size = img.size
            box = union_box(boxes, args.margin, size)
            frames = list(zip(run["shot_frames"], run["shots"]))
            if box is None:
                raise FeelError(f"{args.node} didn't show on screen.")
            index, count = changed_at(before["shots"][-1], frames, box, args.threshold)
            entry["pixels"] = None if index is None else frames[index][0] - start
            entry["box"] = list(box)
            entry["changed_px"] = count
            shots += [before["shots"][-1], *run["shots"]]
        trials.append(entry)
    for path in shots:
        if not args.keep:
            Path(path).unlink(missing_ok=True)
    ticks = game.ticks
    state = numbers([t["state"] for t in trials if t.get("state") is not None]) if args.watch else None
    pixels = numbers([t["pixels"] for t in trials if t.get("pixels") is not None]) if args.node else None
    result = {"input": args.press, "trials": trials, "ticks_per_second": ticks, "max": args.max}
    if args.watch:
        result["state"] = state
        result["state_missed"] = sum(1 for t in trials if t.get("state") is None)
    if args.node:
        result["pixels"] = pixels
        result["pixels_missed"] = sum(1 for t in trials if t.get("pixels") is None)
    gaps = [t["pixels"] - t["state"] for t in trials if t.get("state") is not None and t.get("pixels") is not None]
    if gaps:
        result["gap"] = numbers(gaps)
    lines = [f"latency of {args.press} over {args.repeat} trial{'s' if args.repeat > 1 else ''}, at "
             f"{ticks:g} frames a second:"]
    if args.watch:
        missed = f"; no change within {args.max} frames in {result['state_missed']}" if result["state_missed"] else ""
        lines.append(f"  state ({args.watch}): {frames_text(state, ticks)}{missed}")
    if args.node:
        missed = f"; no change within {args.max} frames in {result['pixels_missed']}" if result["pixels_missed"] else ""
        lines.append(f"  pixels ({args.node}): {frames_text(pixels, ticks)}{missed}")
    if gaps:
        lines.append(f"  pixels after state: {frames_text(result['gap'], ticks)}")
    lines.append("  (frames counted from the input to the first frame drawn showing it; the display adds its own)")
    emit(result, args.json, lines)
    return 0


# --- Parsers ---------------------------------------------------------------------------------------------------------


def add_live_parsers(commands, command):
    """gdh live latency, feel, hitstop, shake and follow. `command` is live.py's maker of a subcommand."""
    one = "Which game instance: a number (default 0). Every instance steps"
    p = command("latency", cmd_latency, "Frames from an input to the first change in a value (--watch) and in a "
                                        "node's pixels (--node), over several trials", instance=one)
    p.add_argument("--press", required=True, metavar="INPUT",
                   help="The input: an action, key:NAME, mouse:left or joy:NAME, as step takes it; a tap, or held "
                        "with --hold")
    p.add_argument("--watch", metavar="EXPR", help="A value that responds (an expression, as eval takes)")
    p.add_argument("--node", metavar="PATH", help="A node whose pixels respond (its box, and what it draws)")
    p.add_argument("--hold", action="store_true", help="Hold the input for the whole trial, not one frame")
    p.add_argument("--repeat", type=int, default=5, metavar="N", help="Trials (default 5)")
    p.add_argument("--max", type=int, default=30, metavar="N", help="Frames to wait for a response (default 30)")
    p.add_argument("--settle", type=int, default=10, metavar="N",
                   help="Frames with no input before each trial (default 10)")
    p.add_argument("--margin", type=float, default=4, metavar="PX", help="Grow the node's box by PX (default 4)")
    p.add_argument("--threshold", type=float, default=LATENCY_THRESHOLD, metavar="T",
                   help="A pixel changed when a channel differs by more than T, of 255 (default 8)")
    p.add_argument("--keep", action="store_true", help="Keep the frames saved for --node")
