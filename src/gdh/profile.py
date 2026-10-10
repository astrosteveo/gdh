"""gdh live profile: which script functions a run's frames spent their time in, overall and on its slowest frames.

The game must run under gdh's debugger (`gdh live start --profiler`, debugger.py), which reads Godot's script
profiler: for each frame, each script function called in it, with its calls, its self time (in its own body) and its
total time (with what it called), and the frame's time in processing, physics and the servers. `profile N` turns the
profiler on, steps N frames with the step's input, turns it off, and sums the frames the step ran.
"""
import json
import statistics
import sys

from gdh.godot import HARNESS, GdhError

# gdh's own callbacks in the game: their total time is gdh's, taken out of each frame's (its frame pacing, saving
# frames, the bridge).
GDH_ROOTS = ("_process", "_physics_process", "_input", "_unhandled_input", "_notification")
SPIKE_TIMES = 3.0
SPIKE_MIN_MS = 1.0


class ProfileError(GdhError):
    pass


def where(signature):
    """A profiler signature, "res://path.gd::LINE::name", as (path, line, name)."""
    parts = signature.split("::")
    if len(parts) >= 3:
        try:
            return parts[0], int(parts[1]), "::".join(parts[2:])
        except ValueError:
            pass
    return signature, 0, signature


def is_gdh(signature):
    """Whether a function is gdh's own (its harness in the game), not the game's."""
    return where(signature)[0].startswith(str(HARNESS))


def gdh_ms(frame, signatures):
    """gdh's own time in a frame: the total time of its harness's callbacks."""
    total = 0.0
    for sig_id, _calls, _self, total_s, *_ in frame["functions"]:
        signature = signatures.get(str(sig_id), "")
        if is_gdh(signature) and where(signature)[2] in GDH_ROOTS:
            total += total_s * 1000
    return total


def summarize(frames, signatures, ticks, budget_ms=None, top=15):
    """The profile of a run's frames: the game's time in each (processing and physics, less gdh's own) and the whole
    frame's, the top functions overall and on the spike frames (the game's, not gdh's), and the servers' time. Times
    are in milliseconds."""
    if not frames:
        raise ProfileError("The profiler sent no frames for the step.")
    overhead = [gdh_ms(f, signatures) for f in frames]
    # The game's own time in each frame: processing and physics, less gdh's. Rendering and presenting the frame (all
    # of a frame's time on Xvfb) are gdh live bench's and frames' to measure; spikes are judged on this.
    times = [max((f["process_time"] + f["physics_time"]) * 1000 - g, 0.0) for f, g in zip(frames, overhead)]
    median = statistics.median(times)
    # A spike: over the budget, or else well over the typical frame: 3 times the median, and 1 ms more at least.
    threshold = budget_ms if budget_ms is not None else max(SPIKE_TIMES * median, median + SPIKE_MIN_MS)
    spikes = [(f, t) for f, t in zip(frames, times) if t > threshold]
    out = {
        "frames": len(frames),
        "frame_ms": stats([f["frame_time"] * 1000 for f in frames]),
        "game_ms": stats(times),
        "gdh_ms": stats(overhead),
        "process_ms": stats([max(f["process_time"] * 1000 - g, 0.0) for f, g in zip(frames, overhead)]),
        "physics_ms": stats([f["physics_time"] * 1000 for f in frames]),
        "servers_ms": servers(frames),
        "top": functions(frames, signatures, top),
        "spike_threshold_ms": round(threshold, 3),
        "spike_rule": f"over the budget, {budget_ms:g} ms" if budget_ms is not None else
                      f"over {threshold:.3g} ms: {SPIKE_TIMES:g} times the median, and {SPIKE_MIN_MS:g} ms over it at "
                      f"least",
        "spikes": [{"frame": f["frame"], "game_ms": round(t, 3), "frame_ms": round(f["frame_time"] * 1000, 3),
                    "physics_ms": round(f["physics_time"] * 1000, 3)} for f, t in spikes],
    }
    if spikes:
        out["spike_top"] = functions([f for f, _ in spikes], signatures, top)
    return out


def stats(values):
    ordered = sorted(values)
    return {"median": round(statistics.median(ordered), 3), "p99": round(ordered[max(0, round(0.99 * (len(ordered) - 1)))], 3),
            "max": round(ordered[-1], 3), "total": round(sum(ordered), 3)}


def servers(frames):
    """Each server's time over the frames (its functions' times added up), in ms: {name: {"total", "max"}}."""
    out = {}
    for f in frames:
        for name, parts in f["servers"].items():
            t = sum(v for v in parts.values() if isinstance(v, (int, float))) * 1000
            entry = out.setdefault(name, {"total": 0.0, "max": 0.0})
            entry["total"] += t
            entry["max"] = max(entry["max"], t)
    return {k: {"total": round(v["total"], 3), "max": round(v["max"], 3)} for k, v in out.items()}


def functions(frames, signatures, top):
    """Each function's calls, self time and total time over the frames, by self time and by total time."""
    sums = {}
    for f in frames:
        for sig_id, calls, self_s, total_s, *_ in f["functions"]:
            if is_gdh(signatures.get(str(sig_id), "")):
                continue
            entry = sums.setdefault(sig_id, [0, 0.0, 0.0, 0.0])
            entry[0] += calls
            entry[1] += self_s * 1000
            entry[2] += total_s * 1000
            entry[3] = max(entry[3], total_s * 1000)
    rows = []
    for sig_id, (calls, self_ms, total_ms, worst) in sums.items():
        signature = signatures.get(str(sig_id), f"function {sig_id}")
        path, line, name = where(signature)
        rows.append({"function": name, "script": path, "line": line, "calls": calls, "self_ms": round(self_ms, 3),
                     "total_ms": round(total_ms, 3), "worst_frame_ms": round(worst, 3)})
    by_self = sorted(rows, key=lambda r: -r["self_ms"])[:top]
    by_total = sorted(rows, key=lambda r: -r["total_ms"])[:top]
    return {"by_self": by_self, "by_total": by_total, "functions": len(rows)}


def describe(row):
    line = f":{row['line']}" if row["line"] else ""
    return (f"{row['function']} ({row['script']}{line}): self {row['self_ms']:g} ms, total {row['total_ms']:g} ms, "
            f"{row['calls']} calls")


def cmd_profile(args):
    from gdh import debugger
    from gdh.live import LiveError, call, load_session, report, step_input_events
    session = load_session(args.session)
    record = session.get("debugger")
    if not record:
        raise LiveError("profile reads Godot's script profiler, which needs gdh's debugger: start the session with "
                        "gdh live start --profiler.")
    try:
        debugger.request(record, "start", max=args.max_functions)
        before = report(call(session, "eval", {"expr": "Engine.get_process_frames()"}), args.json, echo=False)["value"]
        result = report(call(session, "step", {"frames": args.frames, "events": step_input_events(args, args.frames)},
                             timeout=max(300, args.frames * 5)), args.json, echo=False)
        after = report(call(session, "eval", {"expr": "Engine.get_process_frames()"}), args.json, echo=False)["value"]
        got = debugger.request(record, "stop")
    except debugger.DebuggerError as e:
        raise LiveError(str(e)) from None
    frames = [f for f in got["frames"] if before < f["frame"] <= after]
    ticks = result["status"].get("ticks_per_second", 60)
    out = summarize(frames, got["signatures"], ticks, args.budget, args.top)
    out["stepped"] = args.frames
    if args.json:
        print(json.dumps(out, indent=1))
        return 0
    print(f"profile of {out['frames']} frames ({args.frames} game frames stepped): the game's processing and physics "
          f"{out['game_ms']['median']:g} ms median, {out['game_ms']['p99']:g} p99, {out['game_ms']['max']:g} worst "
          f"(whole frames, rendering included: {out['frame_ms']['median']:g} median)")
    print(f"  process {out['process_ms']['median']:g} ms median ({out['process_ms']['max']:g} worst), physics "
          f"{out['physics_ms']['median']:g} ({out['physics_ms']['max']:g}); gdh's own {out['gdh_ms']['median']:g} "
          f"({out['gdh_ms']['max']:g}), left out of the frames' times")
    for name, s in sorted(out["servers_ms"].items(), key=lambda kv: -kv[1]["total"]):
        if s["total"] > 0:
            print(f"  {name}: {s['total']:g} ms over the run, {s['max']:g} at most in a frame")
    print(f"  the game's top functions by self time ({out['top']['functions']} called):")
    for row in out["top"]["by_self"][:args.top]:
        print(f"    {describe(row)}")
    if out["spikes"]:
        print(f"  {len(out['spikes'])} spike frame{'s' if len(out['spikes']) > 1 else ''} ({out['spike_rule']}): "
              + ", ".join(f"{s['frame']} ({s['game_ms']:g} ms)" for s in out["spikes"][:10]))
        print("  on them, by total time:")
        for row in out["spike_top"]["by_total"][:args.top]:
            print(f"    {describe(row)}")
    else:
        print(f"  no spike frames ({out['spike_rule']})")
    return 0


def add_live_parsers(commands, command):
    """gdh live profile. `command` is live.py's maker of a subcommand."""
    from gdh.live import add_input_options
    p = command("profile", cmd_profile, "Step N frames with Godot's script profiler on, and rank the script functions "
                                        "by their time, overall and on the spike frames (needs start --profiler)")
    p.add_argument("frames", type=int, help="How many frames to step")
    p.add_argument("--budget", type=float, metavar="MS",
                   help="Spike frames are those over this many ms (default: over 3 times the median frame, and "
                        "1 ms over it at least)")
    p.add_argument("--top", type=int, default=10, metavar="N", help="Functions listed (default 10)")
    p.add_argument("--max-functions", type=int, default=256, metavar="N",
                   help="The most functions the profiler reports a frame (default 256)")
    add_input_options(p)
