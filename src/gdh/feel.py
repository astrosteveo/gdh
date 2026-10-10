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


# --- gdh live feel ---------------------------------------------------------------------------------------------------

# A speed within this share of the top counts as the top; under this share of it, stopped.
TOP_SHARE = 0.99
STOPPED_SHARE = 0.01
# Moving up by more than this many units in the frames after a press counts as a jump.
JUMPED = 0.5


class Body:
    """The node `feel` measures: where it is, how to put it back, and which way is up."""

    def __init__(self, game, args):
        self.game = game
        self.path = args.node
        self.ref = f"get_node({json.dumps(args.node)})"
        if not isinstance(game.eval(f"{self.ref}.global_position"), list):
            raise FeelError(f"{args.node} has no global_position: feel measures a Node2D or Node3D.")
        self.has_velocity = bool(game.eval(f"'velocity' in {self.ref}"))
        self.has_floor = bool(game.eval(f"{self.ref}.has_method('is_on_floor')"))
        # At rest before the start is read, as it's where every reset goes back to: on the floor when it has one.
        if args.reset:
            game.eval(args.reset)
        if self.has_floor:
            game.step(600, until=f"{self.ref}.is_on_floor()")
        game.step(args.settle)
        self.start = game.eval(f"{self.ref}.global_position")
        self.three_d = len(self.start) == 3
        self.reset_expr = args.reset
        self.settle = args.settle
        self.notes = []
        if self.has_floor and not game.eval(f"{self.ref}.is_on_floor()"):
            self.notes.append(f"{args.node} didn't land in 600 frames, and each measure starts from where it is: "
                              f"give it a floor, or --reset.")

    def position(self):
        return f"{self.ref}.global_position"

    def reset(self):
        """Back to where it started, at rest (or --reset), then settle. A game's own timers (a jump still buffered,
        a cooldown) aren't reset, so with a floor it also waits for the character to land, and settles again."""
        if self.reset_expr:
            self.game.eval(self.reset_expr)
        else:
            vector = f"Vector3({', '.join(map(str, self.start))})" if self.three_d else \
                f"Vector2({', '.join(map(str, self.start))})"
            zero = "Vector3(0, 0, 0)" if self.three_d else "Vector2(0, 0)"
            sets = [f"{self.ref}.set_global_position({vector})"]
            if self.has_velocity:
                sets.append(f"{self.ref}.set_velocity({zero})")
            self.game.eval(f"[{', '.join(sets)}]")
        self.game.step(self.settle)
        if self.has_floor and not self.game.eval(f"{self.ref}.is_on_floor()"):
            self.game.step(600, until=f"{self.ref}.is_on_floor()")
            self.game.step(self.settle)
            if not self.reset_expr:  # landed somewhere after a leftover jump: back to the start again
                self.game.eval(f"[{', '.join(sets)}]")
                self.game.step(self.settle)

    def up(self, a, b):
        """How far b is above a."""
        return (b[1] - a[1]) if self.three_d else (a[1] - b[1])

    def across(self, a, b):
        """How far b is from a along the ground."""
        if self.three_d:
            return math.hypot(b[0] - a[0], b[2] - a[2])
        return abs(b[0] - a[0])


def positions(result):
    """[(frame, position)] from a step's trace of the position."""
    return [(row[0], row[1]) for row in result["trace"]["rows"] if row[1] is not None]


def speeds(body, points, ticks):
    """[(frame, speed along the ground)] between consecutive points, per second."""
    return [(f1, body.across(p0, p1) * ticks / max(f1 - f0, 1)) for (f0, p0), (f1, p1) in zip(points, points[1:])]


def acceleration(series, ticks):
    """How fast speed grows while speeding up: the slope of a straight line fit through [(frame, speed)] up to the
    first reaching 90% of the most, per second. None with fewer than two points."""
    if not series:
        return None
    top = max(s for _, s in series)
    end = next(i for i, (_, s) in enumerate(series) if s >= 0.9 * top)
    part = series[:end + 1]
    if len(part) < 2:
        return None
    t = np.array([f for f, _ in part], dtype=float) / ticks
    v = np.array([s for _, s in part], dtype=float)
    return round(float(np.polyfit(t, v, 1)[0]), 1)


def run_profile(game, body, args):
    """Hold the run input, then let go: top speed, time to it, stopping distance and time."""
    body.reset()
    go = game.step(args.run_frames, press(args.right, 0, args.run_frames), trace=[body.position()])
    start = go["status"]["frame"] - args.run_frames
    stop = game.step(args.stop_frames, trace=[body.position()])
    ticks = game.ticks
    points = [(start, None)] + positions(go) + positions(stop)
    points[0] = (start, body.start if not args.reset else points[1][1])
    v = speeds(body, points, ticks)
    run_v = [(f - start, s) for f, s in v if f - start <= args.run_frames]
    stop_v = [(f - start - args.run_frames, s) for f, s in v if f - start > args.run_frames]
    top = max((s for _, s in run_v), default=0.0)
    out = {"frames_held": args.run_frames, "speed": [[f, round(s, 2)] for f, s in run_v + [(args.run_frames + f, s) for f, s in stop_v]]}
    if top < 1e-6:
        out["note"] = f"It didn't move holding {args.right} for {args.run_frames} frames."
        return out
    out["top_speed"] = round(top, 2)
    out["frames_to_top"] = next(f for f, s in run_v if s >= TOP_SHARE * top)
    out["frames_to_90"] = next(f for f, s in run_v if s >= 0.9 * top)
    out["acceleration"] = acceleration(run_v, ticks)
    end_speed = run_v[-1][1]
    if end_speed < TOP_SHARE * top:
        out["note"] = f"Still speeding up after {args.run_frames} frames: give more --run-frames."
    let_go = points[args.run_frames][1] if len(points) > args.run_frames else None
    stopped = next((f for f, s in stop_v if s < STOPPED_SHARE * top), None)
    out["frames_to_stop"] = stopped
    if stopped is not None and let_go is not None:
        at = dict((f - start - args.run_frames, p) for f, p in points if f - start > args.run_frames)
        out["stopping_distance"] = round(body.across(let_go, at[stopped]), 2)
    return out


def jump_profile(game, body, args, hold, across=False):
    """A jump from rest: held for `hold` frames (0: the whole jump), with the run input held too when `across`.
    Apex height and the frames to it, airtime, and how far and fast it went along the ground in the air."""
    body.reset()
    frames = args.jump_frames
    events = press(args.jump, 0, hold or frames)
    if across:
        events += press(args.right, 0, frames)
    trace = [body.position()] + ([f"{body.ref}.is_on_floor()"] if body.has_floor else [])
    result = game.step(frames, events, trace=trace)
    start = result["status"]["frame"] - frames
    rows = result["trace"]["rows"]
    origin = body.start if not args.reset else rows[0][1]
    heights = [(row[0] - start, body.up(origin, row[1])) for row in rows if row[1] is not None]
    out = {"held_frames": hold or frames, "height": [[f, round(h, 2)] for f, h in heights]}
    if not heights or max(h for _, h in heights) <= JUMPED:
        out["note"] = f"It didn't leave the ground pressing {args.jump}."
        return out
    apex_frame, apex = max(heights, key=lambda fh: fh[1])
    out["apex_height"] = round(apex, 2)
    out["frames_to_apex"] = apex_frame
    took_off = next(f for f, h in heights if h > JUMPED)
    if body.has_floor:
        floors = [(row[0] - start, row[2]) for row in rows]
        landed = next((f for f, on in floors if on and f > took_off), None)
    else:
        landed = next((f for f, h in heights if f > apex_frame and h <= JUMPED), None)
    out["frames_to_takeoff"] = took_off
    out["airtime_frames"] = None if landed is None else landed - took_off + 1
    if landed is None:
        out["note"] = f"It hadn't landed after {frames} frames: give more --jump-frames."
    if across:
        points = [(row[0], row[1]) for row in rows if row[1] is not None]
        air = [(f, s) for f, s in speeds(body, [(start, origin)] + points, game.ticks)
               if took_off <= f - start and (landed is None or f - start <= landed)]
        out["air_top_speed"] = round(max((s for _, s in air), default=0.0), 2)
        out["air_acceleration"] = acceleration(air, game.ticks)
        end = landed if landed is not None else frames
        land_at = dict((row[0] - start, row[1]) for row in rows)
        out["air_distance"] = round(body.across(land_at.get(took_off, origin), land_at.get(end, points[-1][1])), 2)
    return out


def jumped_after(game, body, args, frames, hold_right):
    """Tap the jump now and step `frames`: whether it went up."""
    events = press(args.jump, 0, 1) + (press(args.right, 0, frames) if hold_right else [])
    result = game.step(frames, events, trace=[body.position()])
    points = [p for _, p in positions(result)]
    return any(body.up(a, b) > JUMPED for a, b in zip(points, points[1:]))


def coyote_window(game, body, args):
    """Walk off the ledge, then tap the jump k frames after leaving the floor, for k = 0, 1...: how many frames
    after leaving it still jumps."""
    if not body.has_floor:
        return {"note": f"{body.path} has no is_on_floor(), so the coyote window can't be found."}
    floor = f"{body.ref}.is_on_floor()"
    worked = 0
    for k in range(args.max_window + 1):
        body.reset()
        walk = game.step(args.walk_max, press(args.right, 0, args.walk_max), until=f"not {floor}")
        if not walk["until"]["met"]:
            return {"note": f"It never left the floor walking {args.right} for {args.walk_max} frames: put it near "
                            f"a ledge with --reset, or give more --walk-max."}
        if k:
            game.step(k, press(args.right, 0, k))
        if not jumped_after(game, body, args, 4, True):
            break
        worked = k + 1
    out = {"frames": worked}
    if worked > args.max_window:
        out["note"] = f"It still jumped {args.max_window} frames after leaving the floor: give more --max-window."
    return out


def buffer_window(game, body, args):
    """Jump, then tap the jump again k frames before the frame it lands, for k = 0, 1...: how early a press still
    jumps on landing."""
    if not body.has_floor:
        return {"note": f"{body.path} has no is_on_floor(), so the jump buffer can't be found."}
    first = jump_profile(game, body, args, 1)
    if first.get("airtime_frames") is None:
        return {"note": "It didn't land after a tapped jump, so the jump buffer can't be found."}
    landing = first["frames_to_takeoff"] + first["airtime_frames"] - 1  # the frame it lands, from the first press
    worked = 0
    for k in range(args.max_window + 1):
        if k >= landing - 1:
            break
        body.reset()
        game.step(landing - 1 - k, press(args.jump, 0, 1))
        # The press goes in on frame landing - k; the landing is k frames on, and the jump on the frame after.
        if not jumped_after(game, body, args, k + 4, False):
            break
        worked = k + 1
    out = {"frames": worked}
    if worked > args.max_window:
        out["note"] = f"A press {args.max_window} frames before landing still jumped: give more --max-window."
    return out


PARTS = ("run", "jump", "air", "coyote", "buffer")


def cmd_feel(args):
    skip = {w.strip() for w in (args.skip or "").split(",") if w.strip()}
    if skip - set(PARTS):
        raise FeelError(f"--skip takes {', '.join(PARTS)}, separated by commas.")
    game = Driver(args)
    body = Body(game, args)
    result = {"node": args.node, "notes": body.notes}
    if "run" not in skip:
        result["run"] = run_profile(game, body, args)
    if "jump" not in skip:
        result["jump"] = jump_profile(game, body, args, 0)
        result["short_jump"] = jump_profile(game, body, args, 1)
    if "air" not in skip:
        result["air"] = jump_profile(game, body, args, 0, across=True)
    if "coyote" not in skip:
        result["coyote"] = coyote_window(game, body, args)
    if "buffer" not in skip:
        result["buffer"] = buffer_window(game, body, args)
    body.reset()
    ticks = game.ticks
    result["ticks_per_second"] = ticks
    out = Path(args.chart) if args.chart else game.out_dir("feel") / "feel.png"
    panels = feel_panels(result, body)
    if panels:
        chart.draw(panels, out, f"feel of {args.node}", "frames after the press")
        result["chart"] = str(out)
    emit(result, args.json, feel_lines(result, args, body, ticks))
    return 0


def feel_panels(result, body):
    unit = "m" if body.three_d else "px"
    panels = []
    run = result.get("run")
    if run and run.get("speed"):
        panels.append({"title": f"run: speed ({unit}/s), held {run['frames_held']} frames then let go",
                       "frames": [f for f, _ in run["speed"]], "lines": [("speed", [s for _, s in run["speed"]])]})
    lines = []
    for key, name in (("jump", "held"), ("short_jump", "tapped"), ("air", "held, moving")):
        part = result.get(key)
        if part and part.get("height"):
            lines.append((name, part))
    if lines:
        frames = sorted({f for _, part in lines for f, _ in part["height"]})
        series = []
        for name, part in lines:
            at = dict((f, h) for f, h in part["height"])
            series.append((name, [at.get(f) for f in frames]))
        panels.append({"title": f"jump: height ({unit}) after the press", "frames": frames, "lines": series})
    return panels


def seconds(frames, ticks):
    return f"{frames} frame{'s' if frames != 1 else ''} ({frames * 1000 / ticks:.0f} ms)"


def feel_lines(result, args, body, ticks):
    unit = "m" if body.three_d else "px"
    lines = [f"feel of {args.node} at {ticks:g} frames a second:"] + [f"  note: {n}" for n in result["notes"]]
    run = result.get("run")
    if run:
        if "top_speed" in run:
            lines.append(f"  run ({args.right}): top speed {run['top_speed']:g} {unit}/s, reached in "
                         f"{seconds(run['frames_to_top'], ticks)} (90% in {run['frames_to_90']})")
            if run.get("frames_to_stop") is not None:
                lines.append(f"  stop: {run['stopping_distance']:g} {unit} in {seconds(run['frames_to_stop'], ticks)} "
                             f"after letting go")
            else:
                lines.append(f"  stop: still moving {args.stop_frames} frames after letting go")
        if run.get("note"):
            lines.append(f"  run: {run['note']}")
    for key, name in (("jump", "jump (held)"), ("short_jump", "jump (tapped)")):
        part = result.get(key)
        if not part:
            continue
        if "apex_height" in part:
            air = f", airtime {seconds(part['airtime_frames'], ticks)}" if part.get("airtime_frames") else ""
            lines.append(f"  {name}: apex {part['apex_height']:g} {unit} after {seconds(part['frames_to_apex'], ticks)}"
                         f"{air}")
        if part.get("note"):
            lines.append(f"  {name}: {part['note']}")
    air = result.get("air")
    if air and "air_top_speed" in air:
        ground = run and run.get("acceleration") is not None
        speed = f" (on the ground: {run['top_speed']:g})" if ground else ""
        accel = (f"speeds up at {air['air_acceleration']:g} {unit}/s² in the air"
                 + (f" (on the ground: {run['acceleration']:g})" if ground else "") + ", "
                 if air.get("air_acceleration") is not None else "")
        lines.append(f"  air control: {accel}{air['air_top_speed']:g} {unit}/s at most{speed}, "
                     f"{air['air_distance']:g} {unit} covered from takeoff to landing")
    for key, name, what in (("coyote", "coyote time", "after leaving the floor"),
                            ("buffer", "jump buffer", "before landing, the landing frame included")):
        part = result.get(key)
        if not part:
            continue
        if "frames" in part:
            lines.append(f"  {name}: a jump pressed up to {seconds(part['frames'], ticks)} {what} still jumps"
                         if part["frames"] else f"  {name}: none")
        if part.get("note"):
            lines.append(f"  {name}: {part['note']}")
    if result.get("chart"):
        lines.append(f"chart: {result['chart']}")
    return lines


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

    p = command("feel", cmd_feel, "A character's movement in numbers: top speed and the time to it, stopping, a jump's "
                                  "apex and airtime, air control, coyote time and the jump buffer", instance=one)
    p.add_argument("--node", required=True, metavar="PATH", help="The character (a Node2D or Node3D)")
    p.add_argument("--right", default="ui_right", metavar="INPUT", help="The input that runs (default ui_right)")
    p.add_argument("--jump", default="ui_accept", metavar="INPUT", help="The input that jumps (default ui_accept)")
    p.add_argument("--skip", metavar="PARTS", help=f"Leave out some of {', '.join(PARTS)}, separated by commas")
    p.add_argument("--reset", metavar="EXPR",
                   help="An expression that puts the character back before each measure (default: its position "
                        "and velocity as they were at the start)")
    p.add_argument("--settle", type=int, default=20, metavar="N", help="Frames after each reset (default 20)")
    p.add_argument("--run-frames", type=int, default=90, metavar="N", help="Frames holding --right (default 90)")
    p.add_argument("--stop-frames", type=int, default=60, metavar="N", help="Frames after letting go (default 60)")
    p.add_argument("--jump-frames", type=int, default=120, metavar="N", help="Frames for a jump (default 120)")
    p.add_argument("--walk-max", type=int, default=600, metavar="N",
                   help="For the coyote time: at most this many frames walking to the ledge (default 600)")
    p.add_argument("--max-window", type=int, default=15, metavar="N",
                   help="The most frames tried for the coyote time and the jump buffer (default 15)")
    p.add_argument("--chart", metavar="FILE.png", help="Where the chart goes (default <session out>/feel/feel/feel.png)")
