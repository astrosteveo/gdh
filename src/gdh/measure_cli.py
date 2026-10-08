"""gdh measure (over saved frames) and gdh live record, measure and frames (over a live session).

The measures themselves are in measure.py; this reads the command lines, records frames from a live session and
prints the results.
"""
import json
import shutil
from pathlib import Path

from PIL import Image

from gdh import measure as m
from gdh.covered import covered_findings
from gdh.godot import GdhError
from gdh.images import contact_sheet, crop_findings, pick_evenly

# The image measures, with their help.
KINDS = {
    "flicker": "How much each pixel changes frame to frame (a still camera)",
    "shimmer": "The second difference over time: swim and sparkle beyond smooth motion (a moving camera)",
    "jitter": "A light's summed brightness per frame against its five-frame average, in percent",
    "black": "Pure black, and black holes cut in something lit: a NaN's signature",
    "crush": "Pixels at the tone mapper's floor in a region that should hold detail",
    "line": "A thin line's width (full width at half maximum) and brightness across its profile",
    "spots": "Isolated points of light (stars, dust, sparks): how many, and each one's width, sigma and peak",
}


class MeasureCliError(GdhError):
    pass


def point(text):
    try:
        values = [float(v) for v in text.split(",")]
    except ValueError:
        raise MeasureCliError(f"A point is X,Y in image pixels, not {text!r}.") from None
    if len(values) not in (2, 3):
        raise MeasureCliError(f"A point is X,Y in image pixels, not {text!r}.")
    return values


def region_of(args):
    box = None
    if getattr(args, "box", None):
        try:
            box = [int(v) for v in args.box.split(",")]
        except ValueError:
            box = []
        if len(box) != 4:
            raise MeasureCliError(f"--box is X0,Y0,X1,Y1 in image pixels, not {args.box!r}.")
    return m.Region(box, getattr(args, "mask", None))


def run_kind(kind, paths, args):
    """One image measure over frames, as a dictionary."""
    region = region_of(args)
    if kind == "flicker":
        out = m.flicker(paths, region, args.threshold if args.threshold is not None else 2.0)
    elif kind == "shimmer":
        out = m.shimmer(paths, region, args.threshold if args.threshold is not None else 8.0)
    elif kind == "jitter":
        out = m.jitter(paths, args.lights, region)
    elif kind == "black":
        out = m.over_frames(m.black, paths, region=region, ring=args.ring, lit=args.lit, floor=args.floor)
        out["nan_free"] = out["totals"].get("nan_px", 0) == 0
    elif kind == "crush":
        out = m.over_frames(m.crush, paths, region=region, floor=args.floor, detail=args.detail)
        out["crush_free"] = out["totals"].get("crushed_px", 0) == 0
    elif kind == "line":
        out = run_lines(paths, args)
    elif kind == "spots":
        each = []
        for p in paths:
            each += [{"image": str(p), **s} for s in m.spots(p, region, args.radius, args.contrast, args.limit)]
        out = {"frames": len(paths), **m.spots_summary(each), "each": each}
    else:
        raise MeasureCliError(f"Unknown measure {kind}.")
    if region:
        out["region"] = region.describe()
    return out


def run_lines(paths, args):
    segments = []
    per_frame = None
    if args.lines:
        data = json.loads(Path(args.lines).read_text())
        if isinstance(data, dict) and "frames" in data:
            # A list of lines for each frame, in order: lines that move with the picture.
            per_frame = data["frames"]
            if len(per_frame) < len(paths):
                raise MeasureCliError(f"{args.lines} has lines for {len(per_frame)} frames, and there are {len(paths)}.")
        else:
            segments = data if isinstance(data, list) else data.get("lines", [])
    for a, b in zip(args.start or [], args.end or []):
        segments.append({"a": point(a), "b": point(b)})
    if len(args.start or []) != len(args.end or []):
        raise MeasureCliError("Each --from needs its --to.")
    if not segments and per_frame is None:
        raise MeasureCliError("line needs a line: --from X,Y --to X,Y, or --lines FILE.json.")
    kw = {"samples": args.samples, "min_contrast": args.contrast, "min_length": args.min_length}
    each = []
    for i, p in enumerate(paths):
        for found in m.lines(p, segments + (per_frame[i] if per_frame else []), **kw):
            each.append({"image": str(p), **found})
    out = {"frames": len(paths), "lines_measured": len(each), "each": each}
    if each:
        widths = sorted(e["fwhm_px"] for e in each)
        out["fwhm_px_median"] = round(float(m.np.median(widths)), 2)
        out["fwhm_px_min"] = round(widths[0], 2)
        out["fwhm_px_max"] = round(widths[-1], 2)
        out["peak_above_bg_median"] = round(float(m.np.median([e["peak_above_bg"] for e in each])), 1)
    return out


# --- Printing --------------------------------------------------------------------------------------------------------


def brief(kind, out):
    """A line or a few for a person; --json prints everything."""
    if kind == "flicker":
        return (f"flicker over {out['frames']} frames: mean change {out['mean_change']}, range p99 {out['range_p99']} "
                f"(worst {out['range_max']}), {next(v for k, v in out.items() if k.startswith('share_changed')):.3%} "
                f"of pixels ever changed by more than {args_threshold(out, 'share_changed_over_')}")
    if kind == "shimmer":
        share = next(v for k, v in out.items() if k.startswith("share_over"))
        return (f"shimmer over {out['frames']} frames: second difference mean {out['second_diff_mean']}, "
                f"p99 {out['second_diff_p99']}, {share:.3%} of pixels over {args_threshold(out, 'share_over_')} "
                f"(worst {out['second_diff_max']})")
    if kind == "jitter":
        return f"{out['lights']} lights over {out['frames']} frames: jitter {out['jitter_percent']}%, mean sum {out['sum_mean']}"
    if kind == "black":
        lines = [f"black over {out['frames']} frames: {out['totals'].get('black_px', 0)} pure black pixels, "
                 f"{out['totals'].get('nan_px', 0)} in holes cut in lit areas (a NaN's signature)"]
        for r in out["each"]:
            for s in r["nan_shapes"][:5]:
                lines.append(f"  {Path(r['image']).name}: {s['px']} px at {s['box']}, ring luminance {s['ring_luminance']}")
        return "\n".join(lines)
    if kind == "crush":
        lines = [f"crush over {out['frames']} frames: {out['totals'].get('crushed_px', 0)} pixels at the floor, "
                 f"{out['totals'].get('near_floor_px', 0)} within its last steps"]
        for r in out["each"]:
            if r["crushed_px"]:
                lines.append(f"  {Path(r['image']).name}: {r['crushed_px']} px ({r['crushed_share']:.4%} of "
                             f"{r['pixels']}), largest {r['largest_shape']}")
        return "\n".join(lines)
    if kind == "spots":
        if not out["spots"]:
            return f"spots: none found over {out['frames']} frames (none stood out from the background)"
        f, s, pk = out["fwhm_px"], out["sigma_px"], out["peak_above_bg"]
        return (f"spots over {out['frames']} frames: {out['spots']} points, width {f['median']} px median "
                f"({f['p10']} to {f['p90']} for 80%, {f['min']} to {f['max']} in all), sigma {s['median']} px median "
                f"(min {s['min']}), peak {pk['median']} above the background")
    if kind == "line":
        if not out["lines_measured"]:
            return f"line: nothing measured over {out['frames']} frames (no point stood out from the background)"
        return (f"line over {out['frames']} frames: {out['lines_measured']} measured, width {out['fwhm_px_median']} px "
                f"(from {out['fwhm_px_min']} to {out['fwhm_px_max']}), peak {out['peak_above_bg_median']} above the background")
    return json.dumps(out, indent=2)


def args_threshold(out, prefix):
    return next(k for k in out if k.startswith(prefix))[len(prefix):]


def emit(kind, out, as_json, save=None):
    if save:
        Path(save).parent.mkdir(parents=True, exist_ok=True)
        Path(save).write_text(json.dumps(out, indent=1) + "\n")
    if as_json:
        print(json.dumps(out, indent=1))
    else:
        print(brief(kind, out))
        if save:
            print(f"saved: {save}")


def times_brief(out):
    run = f" of {out['game_frames']} run" if "game_frames" in out else ""
    lines = [f"{out['frames']} frames measured{run}"]
    for key in ("gpu_ms", "cpu_ms"):
        if key in out:
            s = out[key]
            lines.append(f"  {key[:3].upper()}: median {s['p50']:.3f} ms, p99 {s['p99']:.3f} ms, worst {s['max']:.3f} ms")
    for key, title in (("groups", "groups"), ("passes", "passes")):
        if key in out:
            lines.append(f"  {title} (GPU ms over {out[key + '_frames']} frames; median, p99, worst):")
            for name, s in list(out[key].items())[:30]:
                lines.append(f"    {name}: {s['p50']:.3f}, {s['p99']:.3f}, {s['max']:.3f}")
    for warning in out.get("warnings", []):
        lines.append(f"  warning: {warning}")
    return "\n".join(lines)


# --- gdh measure -----------------------------------------------------------------------------------------------------


def cmd_measure(args):
    try:
        if args.kind == "term":
            box = [int(v) for v in args.box.split(",")] if args.box else None
            emit("term", m.term(args.without, args.with_, box), True, args.save)
            return 0
        if args.kind == "dissolve":
            emit("dissolve", m.dissolve(args.a, args.b, args.region), True, args.save)
            return 0
        if args.kind == "mask":
            mask = m.silhouette(args.with_, args.without, args.threshold, not args.no_fill)
            print(json.dumps(m.save_mask(mask, args.out), indent=1))
            return 0
        if args.kind == "sheet":
            print(json.dumps({"sheet": str(make_sheet(args.frames, args.out))}, indent=1))
            return 0
        if args.kind == "times":
            from gdh.perf import add_warnings
            record = m.load_record(args.record)
            out = add_warnings(m.times(record), record, None, record.get("summary", {}).get("other_games", []))
            if args.json:
                print(json.dumps(out, indent=1))
            else:
                print(times_brief(out))
            return 0
        paths = m.frame_paths(args.frames)
        if not paths:
            raise MeasureCliError("No frames: give PNG files or directories holding them.")
        out = run_kind(args.kind, paths, args)
        emit(args.kind, out, args.json, args.save)
        return fail_code(args, out)
    except m.MeasureError as e:
        raise MeasureCliError(str(e)) from None


VIDEO_SUFFIXES = {".mp4", ".avi", ".mkv", ".mov", ".webm", ".ogv"}


def make_sheet(sources, out):
    """A contact sheet from PNG frames, or from a video (through ffmpeg)."""
    if len(sources) == 1 and Path(sources[0]).suffix.lower() in VIDEO_SUFFIXES:
        import tempfile
        from gdh.movie import fps_of, sheet_from_video, video_facts
        video = Path(sources[0])
        if not video.is_file():
            raise MeasureCliError(f"No such file: {video}")
        with tempfile.TemporaryDirectory(prefix="gdh-sheet-") as work:
            return sheet_from_video(video, video_facts(video)["frames"], fps_of(video), out, work)
    paths = m.frame_paths(sources)
    if not paths:
        raise MeasureCliError("No frames: give PNG files or directories holding them, or a video.")
    return sheet_of_frames(paths, out)


def fail_code(args, out):
    """--fail: exit 1 when a black or crush measure found what it looks for."""
    if not getattr(args, "fail", False):
        return 0
    if "nan_free" in out and not out["nan_free"]:
        return 1
    if "crush_free" in out and not out["crush_free"]:
        return 1
    return 0


def add_image_options(p, kind):
    p.add_argument("--box", metavar="X0,Y0,X1,Y1", help="Measure only this box (image pixels, far edges excluded)")
    p.add_argument("--mask", metavar="PNG", help="Measure only where this image is white")
    p.add_argument("--save", metavar="FILE.json", help="Also write the result here")
    if kind in ("flicker", "shimmer"):
        p.add_argument("--threshold", type=float, default=None,
                       help="The change counted as a pixel changing: 2 for flicker, 8 for shimmer (luminance 0-255)")
    if kind == "jitter":
        p.add_argument("--lights", default="bright",
                       help='Which pixels are lights: bright (luminance over 200), red, or a rule like "r>150,g<90,b<90"')
    if kind == "black":
        p.add_argument("--ring", type=int, default=1, help="The ring round a black shape that's read, in pixels (1: its neighbours)")
        p.add_argument("--lit", type=float, default=24.0,
                       help="Luminance over which a ring pixel is lit (24 of 255); a shape whose ring is mostly lit is a hole")
        p.add_argument("--floor", type=int, default=0, help="The value every channel must be at or under to count as black (0)")
        p.add_argument("--fail", action="store_true", help="Exit 1 when a frame has black in a lit hole")
    if kind == "crush":
        p.add_argument("--floor", type=int, default=0, help="The tone mapper's floor, per channel (0: pure black)")
        p.add_argument("--detail", type=float, default=6.0, help="How close to the floor counts as its last steps (6)")
        p.add_argument("--fail", action="store_true", help="Exit 1 when the region has a pixel at the floor")
    if kind == "line":
        p.add_argument("--from", dest="start", action="append", metavar="X,Y", help="A line's start; repeatable")
        p.add_argument("--to", dest="end", action="append", metavar="X,Y", help="Its end")
        p.add_argument("--lines", metavar="FILE.json",
                       help='Lines as JSON: [{"a": [x, y], "b": [x, y], ...}] (or {"lines": [...]}), or {"frames": [[lines], ...]} for each frame in turn; other keys are kept')
        p.add_argument("--samples", type=int, default=24, help="Points measured along the line's middle 60%% (24)")
        p.add_argument("--contrast", type=float, default=12.0, help="How far a point's peak must stand above its background (12)")
        p.add_argument("--min-length", type=float, default=40.0, help="Shorter lines are skipped (40 px)")
    if kind == "spots":
        p.add_argument("--radius", type=int, default=5, help="The window round each point, in pixels each way: nothing as bright in it (5)")
        p.add_argument("--contrast", type=float, default=12.0, help="How far a point's peak must stand above its background (12)")
        p.add_argument("--limit", type=int, default=2000, help="The brightest this many points in each frame (2000)")


def add_parsers(sub):
    p = sub.add_parser("measure", help="Measure saved frames: flicker, shimmer, lines, points, dissolves, NaNs, crushed blacks, frame times")
    kinds = p.add_subparsers(dest="kind", required=True)
    for kind, text in KINDS.items():
        k = kinds.add_parser(kind, help=text, description=text)
        k.add_argument("frames", nargs="+", help="PNG files, or directories of them (taken in name order)")
        k.add_argument("--json", action="store_true", help="Print the whole result")
        add_image_options(k, kind)
        k.set_defaults(func=cmd_measure)
    k = kinds.add_parser("term", help="What one setting adds to a sequence, and whether it swims (two runs: without, with)")
    k.add_argument("without")
    k.add_argument("with_", metavar="with")
    k.add_argument("--box", metavar="X0,Y0,X1,Y1")
    k.add_argument("--save", metavar="FILE.json")
    k.add_argument("--json", action="store_true", help="It always prints JSON; taken for scripts' sake")
    k.set_defaults(func=cmd_measure)
    k = kinds.add_parser("dissolve", help="Doubled and empty pixels between two layers drawn alone as masks")
    k.add_argument("a", help="The first layer alone (white where it draws)")
    k.add_argument("b", help="The second layer alone")
    k.add_argument("--region", action="append", required=True, metavar="PNG",
                   help="Where the layers draw when whole (white); repeat to intersect several")
    k.add_argument("--save", metavar="FILE.json")
    k.add_argument("--json", action="store_true", help="It always prints JSON; taken for scripts' sake")
    k.set_defaults(func=cmd_measure)
    k = kinds.add_parser("mask", help="Where something draws: two shots of one frame, with it and without, as a mask "
                                      "for --mask (holes filled)")
    k.add_argument("with_", metavar="with", help="The frame with it drawn")
    k.add_argument("without", help="The same frame without it")
    k.add_argument("--out", required=True, metavar="PNG", help="Where the mask goes (white where it draws)")
    k.add_argument("--threshold", type=float, default=1.0, help="A difference over this in any channel counts (1)")
    k.add_argument("--no-fill", action="store_true", help="Don't fill the holes inside it")
    k.add_argument("--json", action="store_true", help="It always prints JSON; taken for scripts' sake")
    k.set_defaults(func=cmd_measure)
    k = kinds.add_parser("sheet", help="A contact sheet: 16 frames spread evenly over a run, labeled, in one image, "
                                       "to see at a glance which screen was up when")
    k.add_argument("frames", nargs="+", help="PNG files or directories of them (in name order), or one video file")
    k.add_argument("--out", required=True, metavar="PNG", help="Where the sheet goes")
    k.add_argument("--json", action="store_true", help="It always prints JSON; taken for scripts' sake")
    k.set_defaults(func=cmd_measure)
    k = kinds.add_parser("times", help="Summarize a frame-time record (gdh live frames --save)")
    k.add_argument("record")
    k.add_argument("--json", action="store_true")
    k.set_defaults(func=cmd_measure)


# --- gdh live record, measure and frames -----------------------------------------------------------------------------


def step_events(args, frames):
    from gdh.live import input_event
    events = []
    for point_text in args.move:
        x, y = (float(v) for v in point_text.split(","))
        events.append({"mouse_motion": [x, y], "at": 0})
    for token in args.press:
        events.append(input_event(token, True, 0))
    for token in args.release:
        events.append(input_event(token, False, 0))
    for token in args.hold:
        events += [input_event(token, True, 0), input_event(token, False, frames)]
    return events


def record(session, args, frames, out_dir, as_json=False):
    """Step `frames` frames, saving every one, and move the frames into out_dir as frame-0000.png on. Returns the paths
    and the engine errors raised meanwhile. Printed as they come, or with as_json left for the caller to put in its
    JSON, so stdout stays one JSON document."""
    from gdh.live import call, report
    out_dir = Path(out_dir)
    if out_dir.exists():
        for old in out_dir.glob("frame-*.png"):
            old.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)
    step = {"frames": frames, "events": step_events(args, frames), "shot_every": args.every,
            "cover_every": max(frames // COVER_SAMPLES, 1)}
    reply = call(session, "step", step, instance=args.instance, timeout=max(120, frames * 5))
    result = report(reply, as_json, echo=False)
    errors = [e for part in reply.get("instances", [reply]) for e in part.get("errors", [])]
    if isinstance(result, list):  # several instances: the one asked for
        result = result[int(args.instance) if args.instance != "all" else 0]
    paths = []
    for i, shot in enumerate(result.get("shots", [])):
        dst = out_dir / f"frame-{i:04d}.png"
        shutil.move(shot, dst)
        paths.append(dst)
    return paths, errors, result.get("cover_samples", [])


# About this many samples of what covers the screen over a recording.
COVER_SAMPLES = 48


def sheet_path(out_dir):
    """A recording's contact sheet goes beside its directory, so the directory holds only its frames."""
    out_dir = Path(out_dir).resolve()
    return out_dir.parent / f"{out_dir.name}-sheet.png"


def sheet_of_frames(paths, out, every=1):
    """A contact sheet of PNG frames spread evenly over a run, each labeled with its index in the run."""
    return contact_sheet([(f"frame {i * every + every - 1}", paths[i]) for i in pick_evenly(len(paths))], out)


def after_recording(paths, samples, out_dir, args, as_json):
    """What a recording leaves beside its frames: the contact sheet, and the panels that covered the screen. Returns
    {"sheet", "findings"} for JSON; prints them otherwise."""
    extra = {"findings": covered_findings(samples)}
    if paths and not getattr(args, "no_sheet", False):
        extra["sheet"] = str(sheet_of_frames(paths, sheet_path(out_dir), args.every))
    if extra["findings"] and paths:
        with Image.open(paths[len(paths) // 2]) as middle:
            size = list(middle.size)
        crops = Path(out_dir).resolve().parent / f"{Path(out_dir).resolve().name}-crops"
        crop_findings(extra["findings"], size, {"normal": paths[len(paths) // 2]}, crops)
    if not as_json:
        if extra.get("sheet"):
            print(f"contact sheet: {extra['sheet']}")
        for f in extra["findings"]:
            crop = f"  [{f['crop']}]" if f.get("crop") else ""
            print(f"{f['severity']}: {f['probe']} {f['node']}: {f['message']}{crop}")
    return extra


def default_dir(session, name):
    return Path(session["out"]) / "measure" / name


def cmd_record(args):
    from gdh.live import load_session
    session = load_session(args.session)
    out = Path(args.out) if args.out else default_dir(session, args.label)
    paths, _, samples = record(session, args, args.frames, out, args.json)
    extra = after_recording(paths, samples, out, args, args.json)
    if args.json:
        print(json.dumps({"frames": [str(p) for p in paths], "frames_dir": str(out), **extra}, indent=1))
    else:
        print(f"recorded {len(paths)} frames into {out}")
    return 0


def cmd_live_measure(args):
    from gdh.live import load_session
    session = load_session(args.session)
    out = Path(args.out) if args.out else default_dir(session, args.kind)
    paths, errors, samples = record(session, args, args.frames, out, args.json)
    if not paths:
        raise MeasureCliError("The step saved no frames.")
    try:
        result = run_kind(args.kind, paths, args)
    except m.MeasureError as e:
        raise MeasureCliError(str(e)) from None
    result["frames_dir"] = str(out)
    if args.json and errors:
        result["errors"] = errors
    result.update(after_recording(paths, samples, out, args, True))
    emit(args.kind, result, args.json, args.save)
    if not args.json:
        for f in result["findings"]:
            print(f"{f['severity']}: {f['probe']} {f['node']}: {f['message']}")
        if result.get("sheet"):
            print(f"contact sheet: {result['sheet']}")
    if not args.keep:
        for p in paths:
            p.unlink()
    return fail_code(args, result)


def cmd_frames(args):
    from gdh.live import call, instances, load_session, report
    from gdh.perf import add_warnings, running_games
    session = load_session(args.session)
    # The other games running now: as the record starts over, the game keeps them with it; read, they're warned of.
    others = running_games()
    reply = call(session, "frames", {"reset": args.reset, "clear": args.clear, "others": others},
                 instance=args.instance)
    # With --json, stdout is one JSON document: the engine's errors go into it, not before it.
    result = report(reply, args.json, echo=False)
    errors = [e for part in reply.get("instances", [reply]) for e in part.get("errors", [])]
    results = result if isinstance(result, list) else [result]
    summaries = []
    games = instances(session)
    for part, r in zip(reply.get("instances", [reply]), results):
        if args.clear:
            continue
        summary = {"size": r.get("size"), "adapter": r.get("adapter"), **m.times(r)}
        add_warnings(summary, r, games[part.get("instance", 0)]["pid"], others)
        summaries.append(summary)
        if args.save:
            path = Path(args.save) if len(results) == 1 else Path(args.save).with_suffix(f".{len(summaries) - 1}.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({**r, "summary": summary}) + "\n")
    if args.clear:
        print("frame record cleared")
        return 0
    if args.json:
        out = summaries[0] if len(summaries) == 1 else summaries
        if errors:
            out = {**out, "errors": errors} if isinstance(out, dict) else {"instances": out, "errors": errors}
        print(json.dumps(out, indent=1))
        return 0
    for i, s in enumerate(summaries):
        prefix = f"[{i}] " if len(summaries) > 1 else ""
        size = "x".join(str(int(v)) for v in s["size"]) if s.get("size") else "?"
        print(f"{prefix}{s.get('adapter', '?')} at {size}: " + times_brief(s))
        if s["frames"] and "groups" not in s:
            print(f"{prefix}  (the renderer's own passes: start the session with --gpu-passes)")
    if args.save:
        print(f"saved: {args.save}")
    return 0


def add_live_parsers(commands, command):
    """The live session's measuring commands, beside step and shot. `command` is live.py's maker of a subcommand."""
    one = "Which game instance: a number, or all (default 0)"

    def inputs(p):
        p.add_argument("--every", type=int, default=1, metavar="K", help="Save every Kth frame (default every one)")
        p.add_argument("--out", help="Where the frames go (default: <session out>/measure/<name>)")
        p.add_argument("--move", action="append", default=[], metavar="X,Y", help="As step: move the pointer first")
        p.add_argument("--press", action="append", default=[], metavar="INPUT", help="As step: press at the start")
        p.add_argument("--release", action="append", default=[], metavar="INPUT", help="As step: release at the start")
        p.add_argument("--hold", action="append", default=[], metavar="INPUT", help="As step: hold for the frames")
        p.add_argument("--no-sheet", action="store_true",
                       help="Don't make the contact sheet (<out>-sheet.png, beside the frames' directory)")

    p = command("record", cmd_record, "Step and save every frame into a directory, for gdh measure",
                instance="Which instance's frames, and who gets the input (default 0). Every instance steps")
    p.add_argument("frames", type=int)
    p.add_argument("--label", default="record", help="The directory's name under <session out>/measure (default record)")
    inputs(p)

    p = command("measure", cmd_live_measure, "Step, saving every frame, and measure them",
                instance="Which instance's frames, and who gets the input (default 0). Every instance steps")
    p.add_argument("kind", choices=list(KINDS))
    p.add_argument("--frames", type=int, default=120, help="How many frames to step (default 120)")
    p.add_argument("--keep", action="store_true", help="Keep the frames (by default they're deleted once measured)")
    inputs(p)
    add_shared_measure_options(p)

    p = command("frames", cmd_frames, "Each game frame's GPU and CPU time, and each render pass's, since the last reset",
                instance=one)
    p.add_argument("--reset", action="store_true", help="Start the record over after reading it")
    p.add_argument("--clear", action="store_true", help="Start the record over now (before a run)")
    p.add_argument("--save", metavar="FILE.json", help="Write every frame's times and the summary here")


def add_shared_measure_options(p):
    """Every image measure's options on one parser (gdh live measure takes any kind)."""
    p.add_argument("--box", metavar="X0,Y0,X1,Y1", help="Measure only this box (image pixels)")
    p.add_argument("--mask", metavar="PNG", help="Measure only where this image is white")
    p.add_argument("--save", metavar="FILE.json", help="Also write the result here")
    p.add_argument("--threshold", type=float, default=None, help="flicker 2, shimmer 8 (luminance 0-255)")
    p.add_argument("--lights", default="bright", help="jitter: bright, red, or a rule like r>150,g<90,b<90")
    p.add_argument("--ring", type=int, default=1, help="black: the ring read round a shape (1 px)")
    p.add_argument("--lit", type=float, default=24.0, help="black: a lit ring pixel's luminance (24)")
    p.add_argument("--floor", type=int, default=0, help="black, crush: the floor per channel (0)")
    p.add_argument("--detail", type=float, default=6.0, help="crush: the floor's last steps (6)")
    p.add_argument("--fail", action="store_true", help="black, crush: exit 1 when found")
    p.add_argument("--from", dest="start", action="append", metavar="X,Y", help="line: its start")
    p.add_argument("--to", dest="end", action="append", metavar="X,Y", help="line: its end")
    p.add_argument("--lines", metavar="FILE.json", help="line: lines as JSON")
    p.add_argument("--samples", type=int, default=24)
    p.add_argument("--contrast", type=float, default=12.0)
    p.add_argument("--min-length", type=float, default=40.0)
    p.add_argument("--radius", type=int, default=5, help="spots: the window round each point (5 px)")
    p.add_argument("--limit", type=int, default=2000, help="spots: the brightest this many in each frame (2000)")
