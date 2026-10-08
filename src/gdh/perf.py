"""gdh live bench, monitors and audio: a frame-time budget, Godot's Performance monitors and the leaks they show, and
what the game plays. Also the warnings on a frame-time record: the renderer timing its passes, and other games on the
machine.

The game's side is harness/frames.gd (the frame record), harness/monitors.gd and harness/audio.gd; bridge.gd's step
does the warm-up and takes the monitors' samples.
"""
import json
import os
import statistics
import subprocess
import threading
from pathlib import Path

from gdh import measure as m
from gdh.godot import GdhError, pid_alive

# The names Godot's binaries run under (a process's name is its binary's, cut to 15 characters).
GODOT_NAMES = ("godot", "godot-mono")


class PerfError(GdhError):
    pass


# --- Other games on the machine --------------------------------------------------------------------------------------


def command_line(pid):
    try:
        return [a.decode(errors="replace") for a in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if a]
    except OSError:
        return []


def running(pid):
    """Alive and not a zombie."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def godot_pids():
    names = set(GODOT_NAMES)
    if os.environ.get("GODOT"):
        names.add(Path(os.environ["GODOT"]).name[:15])
    pids = set()
    for name in names:
        out = subprocess.run(["pgrep", "-x", name], capture_output=True, text=True).stdout
        pids |= {int(p) for p in out.split()}
    return pids


def session_games():
    """Every gdh live session's games on this machine, whatever their binary: {pid: "gdh session 'NAME'"}."""
    from gdh.live import SESSION_DIR, instances
    games = {}
    for path in SESSION_DIR.glob("*.json"):
        try:
            session = json.loads(path.read_text())
            many = len(instances(session)) > 1
            for i, game in enumerate(instances(session)):
                games[game["pid"]] = f"gdh session '{session['name']}'" + (f" instance {i}" if many else "")
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return games


def running_games():
    """The games running on this machine: Godot processes (pgrep -x godot, godot-mono, and $GODOT's name) and every
    gdh live session's games. A Godot run --headless draws nothing, so it isn't one. Returns [{"pid", "what"}], what
    being the session, or the binary and project. (add_warnings takes out the game measured.)"""
    sessions = session_games()
    found = []
    for pid in sorted(godot_pids() | {p for p in sessions if pid_alive(p)}):
        args = command_line(pid)
        if not running(pid) or "--headless" in args:
            continue
        found.append({"pid": pid, "what": sessions.get(pid) or describe_godot(args)})
    return found


def describe_godot(args):
    """A Godot process in a few words: its binary, whether it's the editor, and its project."""
    if not args:
        return "?"
    name = Path(args[0]).name
    if "-e" in args or "--editor" in args:
        name += " (the editor)"
    for flag in ("--path", "--main-pack"):
        if flag in args[:-1]:
            return f"{name} on {args[args.index(flag) + 1]}"
    return " ".join([name, *args[1:]])[:120]


class Watch:
    """Other games found on the machine while a `with` block runs: looked for as it starts, every half second, and
    as it ends."""

    def __init__(self):
        self.found = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _look(self):
        for game in running_games():
            self.found.setdefault(game["pid"], game)

    def _run(self):
        while not self._stop.wait(0.5):
            self._look()

    def __enter__(self):
        self._look()
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        self._look()

    def games(self):
        return list(self.found.values())


def gpu_profiled(pid):
    """Whether the game runs with --gpu-profile (gdh live start --gpu-passes)."""
    return "--gpu-profile" in command_line(pid)


def add_warnings(summary, record, game_pid, others=()):
    """Put on a frame-time summary what makes its times read high: the renderer timing each of its passes, and other
    games on the machine while the record ran (at its start, from the record, and `others`). Sets "gpu_passes",
    "other_games" and "warnings"."""
    summary["gpu_passes"] = bool("groups" in summary or (game_pid and gpu_profiled(game_pid)))
    games = {}
    for game in [*record.get("others_at_start", []), *others]:
        game = {**game, "pid": int(game["pid"])}  # (a number through Godot's JSON comes back a float)
        if game["pid"] != game_pid:
            games.setdefault(game["pid"], game)
    summary["other_games"] = list(games.values())
    warnings = []
    if summary["gpu_passes"]:
        warnings.append("the renderer timed each of its passes (--gpu-passes), which adds GPU time to every frame: "
                        "time a budget in a session started without it")
    if games:
        listed = "; ".join(f"pid {g['pid']}: {g['what']}" for g in list(games.values())[:5])
        more = f"; and {len(games) - 5} more" if len(games) > 5 else ""
        warnings.append(f"{len(games)} other game{'s' if len(games) > 1 else ''} ran on the machine during the "
                        f"record, so the GPU may have been shared: {listed}{more}")
    summary["warnings"] = warnings
    return summary


# --- gdh live bench --------------------------------------------------------------------------------------------------


def budgets(summary, args):
    """Each budget asked for, against the GPU time: [{"stat", "gpu_ms", "budget_ms", "over"}]."""
    out = []
    for stat, name, limit in (("p50", "median", args.budget_median), ("p99", "p99", args.budget_p99)):
        if limit is not None:
            value = summary.get("gpu_ms", {}).get(stat)
            out.append({"stat": name, "gpu_ms": value, "budget_ms": limit, "over": value is None or value > limit})
    return out


def chosen_parts(reply, session, instance):
    """The replies of the instances `instance` names, from a reply every instance sent (a step's)."""
    from gdh.live import pick
    if "instances" not in reply:
        return [reply]
    chosen = pick(session, instance)
    return [part for part in reply["instances"] if part["instance"] in chosen]


def cmd_bench(args):
    from gdh.live import call, instances, load_session, report
    from gdh.measure_cli import step_events, times_brief
    session = load_session(args.session)
    games = instances(session)
    if args.frames < 1:
        raise PerfError("bench takes 1 frame or more.")
    step = {"frames": args.frames, "events": step_events(args, args.frames), "warmup": args.warmup,
            "clear_record": True}
    with Watch() as watch:
        stepped = call(session, "step", step, instance=args.instance,
                       timeout=max(300, 2 * args.frames + int(args.warmup)))
        report(stepped, args.json, echo=False)
    reply = call(session, "frames", instance=args.instance)
    report(reply, args.json, echo=False)
    errors = [e for r in (stepped, reply) for part in r.get("instances", [r]) for e in part.get("errors", [])]
    outs = []
    parts = reply.get("instances", [reply])
    for part in parts:
        index = part.get("instance", 0)
        record = part["result"]
        summary = {"size": record.get("size"), "adapter": record.get("adapter"), **m.times(record)}
        add_warnings(summary, record, games[index]["pid"], watch.games())
        summary["budgets"] = budgets(summary, args)
        summary["over_budget"] = any(b["over"] for b in summary["budgets"])
        if len(games) > 1:
            summary["instance"] = index
        outs.append(summary)
        if args.save:
            path = Path(args.save) if len(parts) == 1 else Path(args.save).with_suffix(f".{index}.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({**record, "summary": summary}) + "\n")
    over = any(s["over_budget"] for s in outs)
    if args.json:
        out = outs[0] if len(outs) == 1 else {"instances": outs}
        if errors:
            out = {**out, "errors": errors}
        print(json.dumps(out, indent=1))
        return 1 if over else 0
    for s in outs:
        prefix = f"[{s['instance']}] " if "instance" in s else ""
        size = "x".join(str(int(v)) for v in s["size"]) if s.get("size") else "?"
        print(f"{prefix}{s.get('adapter', '?')} at {size}: " + times_brief(s).replace("\n", "\n" + prefix))
        for b in s["budgets"]:
            value = "not measured" if b["gpu_ms"] is None else f"{b['gpu_ms']:.3f} ms"
            print(f"{prefix}budget: GPU {b['stat']} {value}, {'OVER' if b['over'] else 'within'} {b['budget_ms']:g} ms")
    if args.save:
        print(f"saved: {args.save}")
    return 1 if over else 0


# --- gdh live monitors, and step --monitors --------------------------------------------------------------------------

# What each monitor is called in text, in order. Memory is in bytes.
LABELS = {
    "objects": "objects", "resources": "resources", "nodes": "nodes", "orphan_nodes": "orphan nodes",
    "draw_calls": "draw calls", "objects_drawn": "objects drawn", "primitives": "primitives",
    "pipelines_compiled": "pipelines compiled",
    "video_mem": "video memory", "texture_mem": "texture memory", "buffer_mem": "buffer memory",
    "static_mem": "static memory",
    "physics_2d_active": "2D bodies active", "physics_3d_active": "3D bodies active",
}
LINES = (("objects", "resources", "nodes", "orphan_nodes"),
         ("draw_calls", "objects_drawn", "primitives", "pipelines_compiled"),
         ("video_mem", "texture_mem", "buffer_mem", "static_mem"),
         ("physics_2d_active", "physics_3d_active"))

# The counters a leak shows in: what's made and never freed. (Static memory isn't one: gdh's own frame record grows it.)
LEAK_COUNTERS = ("objects", "resources", "nodes", "orphan_nodes", "video_mem", "texture_mem", "buffer_mem")


def value_text(key, value):
    if key.endswith("_mem"):
        return f"{value / 2 ** 20:.1f} MB"
    return f"{value:g}" if isinstance(value, float) else str(value)


def change_text(key, change):
    if key.endswith("_mem"):
        return f"{change / 2 ** 20:+.1f} MB"
    return f"{change:+g}"


def monitor_keys(sample):
    return [k for k in LABELS if k in sample] + sorted(k for k in sample if k.startswith("custom/"))


def describe_monitors(sample, prefix=""):
    """One reading, a line a group."""
    lines = [f"{prefix}monitors at frame {sample['frame']}:"]
    for group in LINES:
        parts = [f"{LABELS[k]} {value_text(k, sample[k])}" for k in group if k in sample]
        if parts:
            lines.append(f"{prefix}  " + ", ".join(parts))
    for key in sorted(k for k in sample if k.startswith("custom/")):
        lines.append(f"{prefix}  {key} {value_text(key, sample[key])}")
    return "\n".join(lines)


def describe_run(samples, prefix=""):
    """Readings over a step: each monitor at the start and the end, its change, and its least and most."""
    first, last = samples[0], samples[-1]
    keys = [k for k in monitor_keys(last) if all(k in s for s in samples)]
    rows = [("", "start", "end", "change", "min", "max")]
    for key in keys:
        values = [s[key] for s in samples]
        rows.append((LABELS.get(key, key), value_text(key, first[key]), value_text(key, last[key]),
                     change_text(key, last[key] - first[key]), value_text(key, min(values)),
                     value_text(key, max(values))))
    widths = [max(len(r[i]) for r in rows) for i in range(6)]
    lines = [f"{prefix}monitors over frames {first['frame']} to {last['frame']} ({len(samples)} readings):"]
    for row in rows:
        lines.append(f"{prefix}  " + "  ".join([row[0].ljust(widths[0])] + [c.rjust(w) for c, w in zip(row[1:], widths[1:])]))
    return "\n".join(lines)


def leaks(samples, warmup):
    """The counters that grew steadily after the warm-up (the readings `warmup` frames or more after the first).
    Those readings are cut in quarters: a counter grew steadily when each quarter's median is above the one before
    and the last quarter's least is above the first quarter's most. A count that churns (bullets made and freed),
    or that jumps once and stays (a level part loaded), doesn't. Counters are LEAK_COUNTERS and the game's own
    monitors. Returns [{"counter", "from", "to", "per_frame", "frames": [first, last]}]."""
    after = [s for s in samples if s["frame"] - samples[0]["frame"] >= warmup]
    if len(after) < 8:
        raise PerfError(f"{len(after)} readings after the warm-up, and a leak needs 8 or more: step more frames, "
                        f"read more often (--every) or warm up for less (--warmup).")
    cuts = [round(i * len(after) / 4) for i in range(5)]
    quarters = [after[cuts[i]:cuts[i + 1]] for i in range(4)]
    keys = [k for k in (*LEAK_COUNTERS, *sorted(k for k in after[-1] if k.startswith("custom/")))
            if all(isinstance(s.get(k), (int, float)) for s in after)]
    found = []
    for key in keys:
        medians = [statistics.median(s[key] for s in q) for q in quarters]
        if all(a < b for a, b in zip(medians, medians[1:])) and max(s[key] for s in quarters[0]) < min(s[key] for s in quarters[3]):
            span = statistics.mean(s["frame"] for s in quarters[3]) - statistics.mean(s["frame"] for s in quarters[0])
            found.append({"counter": key, "from": medians[0], "to": medians[3],
                          "per_frame": round((medians[3] - medians[0]) / span, 4) if span else None,
                          "frames": [after[0]["frame"], after[-1]["frame"]]})
    return found


def describe_leaks(found, checked, prefix=""):
    if not found:
        return f"{prefix}no leak: {', '.join(LABELS.get(k, k) for k in checked)} didn't grow steadily after the warm-up"
    lines = []
    for leak in found:
        key = leak["counter"]
        rate = f", {change_text(key, leak['per_frame'])} a frame" if leak["per_frame"] is not None else ""
        lines.append(f"{prefix}LEAK: {LABELS.get(key, key)} grew steadily over frames {leak['frames'][0]} to "
                     f"{leak['frames'][1]}: from {value_text(key, leak['from'])} to {value_text(key, leak['to'])}{rate}")
    return "\n".join(lines)


def cmd_monitors(args):
    from gdh.live import call, each, load_session, report
    from gdh.measure_cli import step_events
    session = load_session(args.session)
    frames = args.frames if args.frames is not None else (600 if args.leak else 0)
    if frames <= 0:
        if args.leak:
            raise PerfError("--leak needs a run to watch: --frames N.")
        reply = call(session, "monitors", instance=args.instance)
        result = report(reply, args.json)
        if not args.json:
            for prefix, r in each(reply, result):
                print(describe_monitors(r, prefix))
        return 0
    every = args.every or max(frames // 60, 1)
    warmup = args.warmup if args.warmup is not None else frames // 3
    step = {"frames": frames, "events": step_events(args, frames), "monitors": every}
    reply = call(session, "step", step, instance=args.instance, timeout=max(300, 2 * frames))
    report(reply, args.json, echo=False)
    outs = []
    for part in chosen_parts(reply, session, args.instance):
        samples = part["result"]["monitors"]
        out = {"samples": samples, "every": every}
        if "instance" in part:
            out["instance"] = part["instance"]
        if args.leak:
            out["warmup"] = warmup
            out["leaks"] = leaks(samples, warmup)
            out["checked"] = [k for k in (*LEAK_COUNTERS, *(k for k in samples[-1] if k.startswith("custom/")))
                              if k in samples[-1]]
        outs.append(out)
    leaked = any(o.get("leaks") for o in outs)
    if args.json:
        errors = [e for part in reply.get("instances", [reply]) for e in part.get("errors", [])]
        out = outs[0] if len(outs) == 1 else {"instances": outs}
        if errors:
            out = {**out, "errors": errors}
        print(json.dumps(out, indent=1))
        return 1 if leaked else 0
    for o in outs:
        prefix = f"[{o['instance']}] " if "instance" in o else ""
        print(describe_run(o["samples"], prefix))
        if args.leak:
            print(f"{prefix}after a warm-up of {warmup} frames:")
            print(describe_leaks(o["leaks"], o["checked"], prefix))
    return 1 if leaked else 0


# --- gdh live audio --------------------------------------------------------------------------------------------------


def describe_audio(r, prefix=""):
    lines = []
    if not r["frames"]:
        lines.append(f"{prefix}the game hasn't run yet: step it, then read what it played")
    else:
        when = "since `run`" if r["running"] else "the last step"
        lines.append(f"{prefix}{when}: {r['frames']} frames in {r['wall_ms']:.0f} ms of real time, {r['mixes']} "
                     f"block{'' if r['mixes'] == 1 else 's'} mixed ({r['driver']} audio driver, {r['mix_rate']:g} Hz)")
        if not r["mixes"]:
            block = f" (4096 samples, {4096 / r['mix_rate'] * 1000:.0f} ms)" if r["driver"] == "Dummy" else ""
            lines.append(f"{prefix}  no audio was mixed in it: Godot mixes in real time, a block{block} at a time, so "
                         f"the buses' levels need a longer step")
    lines.append(f"{prefix}buses (peak over {'it' if r['frames'] else 'no step'}):")
    for bus in r["buses"]:
        peak = "not measured" if bus["peak_db"] is None else (
            "silent" if bus["peak_db"] <= -200 else f"{bus['peak_db']:.1f} dB")
        extra = [f"volume {bus['volume_db']:.1f} dB"] if bus["volume_db"] else []
        if bus["mute"]:
            extra.append("muted")
        if bus["send"]:
            extra.append(f"to {bus['send']}")
        lines.append(f"{prefix}  {bus['bus']}: {peak}" + (f" ({', '.join(extra)})" if extra else ""))
    if r["players"]:
        lines.append(f"{prefix}players that played:")
        for p in r["players"]:
            lines.append(f"{prefix}  {describe_player(p)}")
    elif r["frames"]:
        lines.append(f"{prefix}no player played")
    if r["idle_count"]:
        names = ", ".join(p["node"] for p in r["idle"][:8])
        more = f" and {r['idle_count'] - 8} more" if r["idle_count"] > 8 else ""
        lines.append(f"{prefix}players that didn't play: {names}{more}")
    return "\n".join(lines)


def describe_player(p):
    stream = p["stream"] or "no stream"
    length = f" of {p['length']:.2f}" if p.get("length") else ""
    text = f"{p['node']} ({p['class']}): {stream} on {p['bus']}, at {p['position']:.2f}{length} s"
    if p["volume_db"]:
        text += f", volume {p['volume_db']:.1f} dB"
    if "distance" in p:
        unit = " m" if p["class"].endswith("3D") else " px"
        limit = f" (heard to {p['max_distance']:g}{unit})" if p.get("max_distance") else ""
        text += f", {p['distance']:g}{unit} from the listener{limit}"
    if p.get("gone"):
        return text + ", since freed or out of the tree"
    return text + (", playing" if p["playing"] else ", ended")


def cmd_audio(args):
    from gdh.live import call, each, load_session, report
    session = load_session(args.session)
    reply = call(session, "audio", instance=args.instance)
    result = report(reply, args.json)
    if not args.json:
        for prefix, r in each(reply, result):
            print(describe_audio(r, prefix))
    return 0


# --- The command lines -----------------------------------------------------------------------------------------------


def add_live_parsers(commands, command):
    """bench, monitors and audio on gdh live. `command` is live.py's maker of a subcommand."""
    one = "Which game instance: a number, or all (default 0)"

    def inputs(p):
        p.add_argument("--move", action="append", default=[], metavar="X,Y", help="As step: move the pointer first")
        p.add_argument("--press", action="append", default=[], metavar="INPUT", help="As step: press at the start")
        p.add_argument("--release", action="append", default=[], metavar="INPUT", help="As step: release at the start")
        p.add_argument("--hold", action="append", default=[], metavar="INPUT", help="As step: hold for the frames")

    p = command("bench", cmd_bench, "Time N frames on the GPU and check a budget: exit 1 over it",
                instance="Which instance's times, and who gets the input (default 0). Every instance steps")
    p.add_argument("frames", type=int, nargs="?", default=600, help="Frames to step and time (default 600)")
    p.add_argument("--budget-median", type=float, metavar="MS", help="Exit 1 if the GPU's median frame is over this")
    p.add_argument("--budget-p99", type=float, metavar="MS", help="Exit 1 if the GPU's 99th percentile is over this")
    p.add_argument("--warmup", type=float, default=1.0, metavar="SECONDS",
                   help="Seconds of held frames drawn back to back first, so the GPU has clocked up (default 1; "
                        "no game time passes)")
    p.add_argument("--save", metavar="FILE.json", help="Write every frame's times and the summary here")
    inputs(p)

    p = command("monitors", cmd_monitors, "Godot's Performance monitors: objects, nodes, orphan nodes, resources, "
                                          "draw calls, video memory; over a step with --frames, and --leak",
                instance="Which instance's monitors (default 0). With --frames every instance steps")
    p.add_argument("--frames", type=int, metavar="N",
                   help="Step N frames, reading the monitors as they go (default: read them once, now; with --leak 600)")
    p.add_argument("--every", type=int, metavar="K", help="Read them every K frames (default: about 60 readings)")
    p.add_argument("--leak", action="store_true",
                   help="Exit 1 if a count (objects, resources, nodes, orphan nodes, video memory, the game's own "
                        "monitors) grows steadily after the warm-up")
    p.add_argument("--warmup", type=int, metavar="FRAMES",
                   help="Frames from the start before --leak looks (default a third of them)")
    inputs(p)

    command("audio", cmd_audio, "Each audio bus's peak level over the last step, and the players that played what",
            instance=one)
