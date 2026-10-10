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

    def step(self, frames, events=(), trace=(), every=1, shot_every=0, track=None, until=None, **extra):
        from gdh.live import call, report
        request = {"frames": max(frames, 1), "events": list(events), "shot_every": shot_every, **extra}
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
        parts = reply.get("instances", [reply])
        result["game_events"] = parts[min(self.index, len(parts) - 1)].get("events", [])  # GdhEvents during the step
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


# --- Effects: hitstop, shake, follow -----------------------------------------------------------------------------------


def triggered(game, args, frames, trace, every=1):
    """Run the trigger (--do, then the step's input over the first `frames`) and step, tracing `trace`. Returns the
    step's result, with the frame before the trigger's values first in its rows when `before` holds them."""
    from gdh.live import step_input_events
    before = game.step(1, trace=trace)
    if args.do:
        game.eval(args.do)
    result = game.step(frames, step_input_events(args, frames), trace=trace, every=every)
    result["trace"]["rows"] = before["trace"]["rows"] + result["trace"]["rows"]
    return result


def runs(flags):
    """The runs of true in a list: [(first index, length)]."""
    out, start = [], None
    for i, flag in enumerate(flags + [False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            out.append((start, i - start))
            start = None
    return out


def node_ref(path):
    return f"get_node({json.dumps(path)})"


def camera_ref(args):
    if args.camera:
        return node_ref(args.camera)
    return None


def cmd_hitstop(args):
    game = Driver(args)
    trace = ["Engine.time_scale"] + ([f"{node_ref(args.node)}.global_position"] if args.node else [])
    result = triggered(game, args, args.frames, trace)
    rows = result["trace"]["rows"]
    start = rows[0][0]
    out = {"frames": args.frames, "ticks_per_second": game.ticks}
    scales = [row[1] for row in rows[1:]]
    slow = [s is not None and s < 1 for s in scales]
    found = runs(slow)
    if found:
        first, length = found[0]
        out["time_scale"] = {"first_frame": first + 1, "frames": length, "lowest": min(s for s in scales if s is not None),
                             "slow_frames": sum(slow)}
    if args.node:
        points = [row[2] for row in rows]
        moves = [None if a is None or b is None else math.dist(a, b) for a, b in zip(points, points[1:])]
        still = [m is not None and m < 1e-3 for m in moves]
        out["moving"] = [round(m, 3) if m is not None else None for m in moves]
        if not any(m and m >= 1e-3 for m in moves):
            out["note"] = f"{args.node} didn't move over the {args.frames} frames, so a freeze can't be told from " \
                          f"standing still: keep it moving (--hold, or --do)."
        else:
            freezes = [(f, n) for f, n in runs(still) if f + n < len(still) or f == 0]  # it moves again after
            if freezes:
                first, length = freezes[0]
                out["freeze"] = {"first_frame": first + 1, "frames": length}
    panels = [{"title": "Engine.time_scale", "frames": list(range(0, len(rows))), "lines": [("time_scale", [r[1] for r in rows])]}]
    if args.node:
        speed = [None] + [None if m is None else m * game.ticks for m in moves]
        panels.append({"title": f"{args.node}: speed (per second)", "frames": list(range(0, len(rows))),
                       "lines": [("speed", speed)]})
    out["chart"] = str(chart.draw(panels, Path(args.chart) if args.chart else game.out_dir("hitstop") / "hitstop.png",
                                  "hit-stop", "frames after the trigger"))
    lines = [f"hit-stop over {args.frames} frames after the trigger, at {game.ticks:g} frames a second:"]
    ts = out.get("time_scale")
    lines.append(f"  Engine.time_scale: under 1 from frame {ts['first_frame']} for {seconds(ts['frames'], game.ticks)}, "
                 f"at least {ts['lowest']:g}" if ts else "  Engine.time_scale: 1 throughout")
    if args.node:
        fr = out.get("freeze")
        if fr:
            lines.append(f"  {args.node}: stood still from frame {fr['first_frame']} for {seconds(fr['frames'], game.ticks)}, "
                         f"then moved again")
        elif "note" not in out:
            lines.append(f"  {args.node}: never stood still")
        if out.get("note"):
            lines.append(f"  note: {out['note']}")
    lines.append(f"chart: {out['chart']}")
    emit(out, args.json, lines)
    return 0


def shake_signal(rows, window):
    """The shake in a series of positions: each axis less its centred moving average over `window` frames."""
    points = np.array([row for row in rows], dtype=float)
    half = window // 2
    padded = np.pad(points, ((half, half), (0, 0)), mode="edge")
    kernel = np.ones(window) / window
    trend = np.stack([np.convolve(padded[:, k], kernel, mode="valid") for k in range(points.shape[1])], axis=1)
    return points - trend


def describe_shake(values, ticks):
    """Amplitude, RMS, frequency and decay of one axis's shake."""
    v = np.asarray(values, dtype=float)
    peak = float(np.abs(v).max()) if len(v) else 0.0
    if peak < 1e-3:
        return None
    active = np.nonzero(np.abs(v) > 0.05 * peak)[0]
    first, last = int(active[0]), int(active[-1])
    part = v[first:last + 1]
    # The frequency from the frames between sign changes (each half a cycle), ignoring the samples near zero.
    kept = np.nonzero(np.abs(part) > 0.05 * peak)[0]
    signs = np.sign(part[kept])
    flips = np.nonzero(np.diff(signs) != 0)[0]
    middles = (kept[flips] + kept[flips + 1]) / 2
    frequency = ticks / (2 * float(np.diff(middles).mean())) if len(middles) > 1 else 0.0
    period = max(int(round(ticks / frequency)) if frequency > 0 else 1, 1)
    envelope = np.array([np.abs(v[i:i + period]).max() for i in range(len(v))])
    at_peak = int(np.argmax(np.abs(v)))
    half = next((i - at_peak for i in range(at_peak, len(v)) if envelope[i] < 0.5 * peak), None)
    tenth = next((i - at_peak for i in range(at_peak, len(v)) if envelope[i] < 0.1 * peak), None)
    return {"amplitude": round(peak, 3), "rms": round(float(np.sqrt((part ** 2).mean())), 3),
            "frequency_hz": round(frequency, 2), "first_frame": first, "frames": last - first + 1,
            "peak_frame": at_peak, "frames_to_half": half, "frames_to_tenth": tenth}


def camera_exprs(game, args):
    """What a shake or follow reads of the camera: [(name, expression)], and whether it's 2D."""
    ref = camera_ref(args)
    if ref is None:
        two = game.eval("get_viewport().get_camera_2d() != null")
        ref = "get_viewport().get_camera_2d()" if two else "get_viewport().get_camera_3d()"
        if not two and not game.eval("get_viewport().get_camera_3d() != null"):
            raise FeelError("The viewport has no current camera: give --camera PATH.")
    else:
        two = bool(game.eval(f"{ref}.is_class('Camera2D')"))
    return ref, two


def cmd_shake(args):
    game = Driver(args)
    ref, two = camera_exprs(game, args)
    if two:
        trace = [f"{ref}.get_screen_center_position()", f"{ref}.offset"]
    else:
        trace = [f"{ref}.global_position", f"Vector2({ref}.h_offset, {ref}.v_offset)"]
    result = triggered(game, args, args.frames, trace)
    rows = result["trace"]["rows"]
    offsets = [row[2] for row in rows]
    unit = "px" if two else "m"
    if any(o and any(abs(c) > 1e-6 for c in o) for o in offsets):
        source, signal = ("offset" if two else "h_offset, v_offset"), np.array(offsets, dtype=float)
    else:
        source = "screen centre, less its moving average" if two else "position, less its moving average"
        signal = shake_signal([row[1] for row in rows], args.window)
    axes = "xy" if signal.shape[1] == 2 else "xyz"
    found = {axis: describe_shake(signal[:, k], game.ticks) for k, axis in enumerate(axes)}
    out = {"camera": ref, "source": source, "frames": args.frames, "ticks_per_second": game.ticks,
           "axes": {k: v for k, v in found.items() if v}, "signal": signal.round(3).tolist()}
    out["chart"] = str(chart.draw([{"title": f"camera shake ({unit}), from its {source}",
                                    "frames": list(range(len(rows))),
                                    "lines": [(axis, signal[:, k].tolist()) for k, axis in enumerate(axes)]}],
                                  Path(args.chart) if args.chart else game.out_dir("shake") / "shake.png",
                                  "screen shake", "frames after the trigger"))
    lines = [f"shake of {ref} over {args.frames} frames after the trigger, from its {source}:"]
    if not out["axes"]:
        lines.append("  no shake")
    for axis, d in out["axes"].items():
        decay = ""
        if d["frames_to_half"] is not None:
            decay = f", down to half in {seconds(d['frames_to_half'], game.ticks)}"
            if d["frames_to_tenth"] is not None:
                decay += f" and a tenth in {d['frames_to_tenth']}"
        lines.append(f"  {axis}: {d['amplitude']:g} {unit} at most (RMS {d['rms']:g}), {d['frequency_hz']:g} Hz, "
                     f"for {seconds(d['frames'], game.ticks)} from frame {d['first_frame']}{decay}")
    lines.append(f"chart: {out['chart']}")
    emit(out, args.json, lines)
    return 0


def cmd_follow(args):
    game = Driver(args)
    ref, two = camera_exprs(game, args)
    camera = f"{ref}.get_screen_center_position()" if two else f"{ref}.global_position"
    target = f"{node_ref(args.target)}.global_position"
    result = triggered(game, args, args.frames, [camera, target])
    after = game.step(args.after, trace=[camera, target])
    rows = result["trace"]["rows"] + after["trace"]["rows"]
    cam = np.array([r[1] for r in rows], dtype=float)
    tgt = np.array([r[2] for r in rows], dtype=float)
    cam = cam - (cam[0] - tgt[0])  # where the camera sits from the target at rest counts as no lag
    gap = np.linalg.norm(cam - tgt, axis=1)
    unit = "px" if two else "m"
    out = {"camera": ref, "target": args.target, "ticks_per_second": game.ticks, "frames": args.frames,
           "after": args.after, "max_lag": round(float(gap.max()), 3), "mean_lag": round(float(gap.mean()), 3)}
    # Lag in frames: the delay that best lines the camera's path up with the target's.
    best, best_err = 0, float("inf")
    for shift in range(0, min(60, len(rows) // 2)):
        err = float(((cam[shift:] - tgt[:len(tgt) - shift]) ** 2).sum(axis=1).mean())
        if err < best_err - 1e-9:
            best, best_err = shift, err
    out["lag_frames"] = best
    stop = args.frames  # the target's input ends here: index args.frames is the frame it ended on
    moved = tgt[stop] - tgt[0]
    if np.linalg.norm(moved) > 1e-6:
        direction = moved / np.linalg.norm(moved)
        beyond = (cam[stop:] - tgt[-1]) @ direction
        out["overshoot"] = round(max(float(beyond.max()), 0.0), 3)
        settled = next((i for i in range(stop, len(gap)) if all(gap[j] < args.settled for j in range(i, len(gap)))), None)
        out["frames_to_settle"] = None if settled is None else settled - stop
    axes = "xy" if two else "xyz"
    panels = [{"title": f"{axis} ({unit}): camera and target", "frames": list(range(len(rows))),
               "lines": [("target", tgt[:, k].tolist()), ("camera", cam[:, k].tolist())]}
              for k, axis in enumerate(axes) if np.ptp(tgt[:, k]) > 1e-6 or np.ptp(cam[:, k]) > 1e-6]
    panels.append({"title": f"lag ({unit})", "frames": list(range(len(rows))), "lines": [("lag", gap.tolist())]})
    out["chart"] = str(chart.draw(panels, Path(args.chart) if args.chart else game.out_dir("follow") / "follow.png",
                                  f"camera following {args.target}", "frames from the start"))
    lines = [f"camera {ref} following {args.target} over {args.frames} frames of input and {args.after} after:",
             f"  lag: {out['max_lag']:g} {unit} at most, {out['mean_lag']:g} on average; the camera's path is "
             f"{seconds(best, game.ticks)} behind the target's"]
    if "overshoot" in out:
        settle = (f", settled within {args.settled:g} {unit} {seconds(out['frames_to_settle'], game.ticks)} after the "
                  f"input ended" if out["frames_to_settle"] is not None else
                  f", not settled within {args.settled:g} {unit} {args.after} frames after the input ended")
        lines.append(f"  overshoot: {out['overshoot']:g} {unit} past where the target stopped{settle}")
    else:
        lines.append(f"  note: {args.target} didn't move, so there's no lag to see: move it (--hold ...)")
    lines.append(f"chart: {out['chart']}")
    emit(out, args.json, lines)
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

    from gdh.live import add_input_options

    def effect(name, func, help, frames):
        p = command(name, func, help, instance=one)
        p.add_argument("--do", metavar="EXPR", help="An expression that triggers it (as eval takes), run just before")
        p.add_argument("--frames", type=int, default=frames, metavar="N",
                       help=f"Frames to watch after the trigger, with the input (default {frames})")
        p.add_argument("--chart", metavar="FILE.png", help=f"Where the chart goes (default <session out>/feel/{name}/)")
        add_input_options(p)
        return p

    p = effect("hitstop", cmd_hitstop, "Frames where time slows or stops after a trigger (Engine.time_scale), and a "
                                       "moving node standing still", 30)
    p.add_argument("--node", metavar="PATH", help="A node that's moving when it's triggered, to see it freeze")
    p = effect("shake", cmd_shake, "A camera's shake after a trigger: amplitude, frequency, how long, how fast it "
                                   "decays", 60)
    p.add_argument("--camera", metavar="PATH", help="The camera (default the viewport's current one)")
    p.add_argument("--window", type=int, default=9, metavar="N",
                   help="With no offset to read, the shake is the camera's position less its moving average over N "
                        "frames (default 9)")
    p = effect("follow", cmd_follow, "How a camera follows a node: its lag in distance and frames, its overshoot "
                                     "and how long it takes to settle", 60)
    p.add_argument("--target", required=True, metavar="PATH", help="The node the camera follows")
    p.add_argument("--camera", metavar="PATH", help="The camera (default the viewport's current one)")
    p.add_argument("--after", type=int, default=60, metavar="N",
                   help="Frames to watch after the input ends, for the overshoot and settling (default 60)")
    p.add_argument("--settled", type=float, default=1.0, metavar="D",
                   help="Settled once within D of the target, px or m (default 1)")
