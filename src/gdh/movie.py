"""gdh movie: record a video of a game with Godot's Movie Maker, off-screen, at the size asked for.

Godot runs with --write-movie into an MJPEG AVI at --quality, 1.0 by default (Movie Maker's PNG output encodes each frame on the
main thread and was about 5x slower at 3840x2160 for about the same picture), at a fixed frame rate, so the movie
holds every frame whatever the machine's speed, with the game's audio. ffmpeg then encodes it to H.264 in an MP4 with
AAC audio. Movie Maker takes its size from the project's settings, never --resolution, so gdh gives it the size
through an override.cfg written for the run (override.py). The AVI is kept in a temporary directory in the output
directory, never in the project, and removed unless --keep-avi.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from gdh.covered import covered_findings
from gdh.display import open_display, parse_resolution
from gdh.godot import HARNESS, GdhError, alert_shims, build_csharp, godot_cmd, godot_env, kill_groups, size_mismatch
from gdh.images import contact_sheet, crop_findings, pick_evenly
from gdh.imports import describe_missing, ensure_imported, missing_resources
from gdh.override import project_override

MOVIE_SCRIPT = HARNESS / "movie.gd"
# Movie Maker's MJPEG quality, 0 to 1 (Godot's default 0.75). Godot silently ignores a setting it doesn't know in
# override.cfg, so the harness reports the value Godot read, and gdh checks it.
QUALITY_KEY = "editor/movie_writer/video_quality"
SILENT_DB = -90.0


class MovieError(GdhError):
    pass


def tool(name):
    if not shutil.which(name):
        raise MovieError(f"gdh movie needs {name} (part of ffmpeg) on PATH.")
    return name


def probe(path, stream, entries, count=False):
    """ffprobe's view of one stream of a file, as a dict ({} when there's no such stream)."""
    cmd = [tool("ffprobe"), "-v", "error", *(["-count_frames"] if count else []), "-select_streams", stream,
           "-show_entries", f"stream={entries}", "-of", "json", str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True)
    streams = json.loads(out.stdout or "{}").get("streams", [])
    return streams[0] if streams else {}


def video_facts(path):
    v = probe(path, "v:0", "width,height,nb_read_frames,codec_name", count=True)
    return {"width": int(v.get("width", 0)), "height": int(v.get("height", 0)),
            "frames": int(v.get("nb_read_frames", 0) or 0), "codec": v.get("codec_name")}


def fps_of(path):
    """A video's frame rate."""
    rate = probe(path, "v:0", "r_frame_rate").get("r_frame_rate", "0/1")
    num, _, den = rate.partition("/")
    return float(num) / float(den or 1) if float(den or 1) else 0.0


def audio_facts(path):
    """The audio stream, and its loudest sample in dB (SILENT_DB or under is silence), or None without one."""
    a = probe(path, "a:0", "codec_name,sample_rate,channels")
    if not a:
        return None
    out = subprocess.run([tool("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0",
                          "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
    peak = next((float(line.split("max_volume:")[1].split()[0]) for line in out.splitlines() if "max_volume:" in line),
                -float("inf"))
    return {"codec": a.get("codec_name"), "sample_rate": int(a.get("sample_rate", 0)),
            "channels": int(a.get("channels", 0)), "max_db": peak, "silent": peak <= SILENT_DB}


def encode(avi, mp4, crf, has_audio):
    """The AVI as H.264 (yuv420p, so every player takes it) with AAC audio, its index at the front (+faststart)."""
    cmd = [tool("ffmpeg"), "-y", "-hide_banner", "-v", "error", "-i", str(avi), "-map", "0:v:0",
           *(["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k"] if has_audio else []),
           "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p", "-movflags", "+faststart",
           str(mp4)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise MovieError(f"ffmpeg couldn't encode the movie: {proc.stderr.strip()[-1500:]}")


def sheet_from_video(video, frames, fps, out, work):
    """A contact sheet of frames spread evenly over a video, each labeled with its frame and time."""
    picks = pick_evenly(frames)
    pattern = Path(work) / "sheet-%03d.png"
    select = "+".join(f"eq(n\\,{n})" for n in picks)
    proc = subprocess.run([tool("ffmpeg"), "-y", "-hide_banner", "-v", "error", "-i", str(video),
                           "-vf", f"select='{select}'", "-fps_mode", "vfr", str(pattern)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise MovieError(f"ffmpeg couldn't take frames for the contact sheet: {proc.stderr.strip()[-800:]}")
    tiles = sorted(Path(work).glob("sheet-*.png"))
    return contact_sheet([(f"frame {n}  {n / fps:.2f} s" if fps else f"frame {n}", p) for n, p in zip(picks, tiles)], out)


def wait_started(proc, started, override, deadline):
    """Wait for the harness to say Godot has read its settings, then remove the override.cfg written for it."""
    while not started.exists() and proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    override.remove()
    return json.loads(started.read_text()) if started.exists() else {}


def cmd_movie(args):
    project = Path(args.project).resolve()
    width, height = parse_resolution(args.resolution)
    if width % 2 or height % 2:
        raise MovieError(f"--resolution {args.resolution}: H.264 in yuv420p, which every player takes, needs an even "
                         f"width and height.")
    if args.fps < 1:
        raise MovieError("--fps takes 1 or more.")
    if not 0 < args.quality <= 1:
        raise MovieError("--quality takes a number over 0, up to 1.")
    frames = args.frames if args.frames else round(args.seconds * args.fps)
    if frames < 1:
        raise MovieError("Give --seconds or --frames: how long to record.")
    tool("ffmpeg")
    tool("ffprobe")
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    for old in ("movie.mp4", "movie.avi", "sheet.png", "report.json"):
        (out / old).unlink(missing_ok=True)
    shutil.rmtree(out / "crops", ignore_errors=True)
    if not args.no_build:
        build_csharp(project)
    if not args.no_import:
        ensure_imported(project)
    timeout = args.timeout or 120 + frames
    settings = {"display/window/size/window_width_override": width,
                "display/window/size/window_height_override": height,
                QUALITY_KEY: args.quality}
    # The AVI is large (MJPEG at quality 1.0: about 1 MB a frame at 1920x1080), so it goes beside the output, on its
    # disk, not into a RAM-backed /tmp. .gdignore keeps Godot out of it should the output be inside the project.
    work = Path(tempfile.mkdtemp(prefix=".gdh-movie-", dir=out))
    (work / ".gdignore").touch()
    avi = work / "movie.avi"
    started, result_file = work / "started.json", work / "result.json"
    user_args = ["--frames", str(frames), "--started-file", str(started), "--result-file", str(result_file)]
    if args.scene:
        user_args += ["--scene", args.scene]
    cmd = godot_cmd(project, args.resolution, ["--write-movie", str(avi), "--fixed-fps", str(args.fps),
                                                "--script", str(MOVIE_SCRIPT)], args.game_args)
    try:
        t0 = time.monotonic()
        code = None
        with project_override(project, settings) as override, alert_shims() as shims:
            display = open_display(args.display, args.resolution, out / "display.log")
            try:
                with open(out / "godot.log", "w") as log:
                    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                            env=godot_env(shims, display.name, user_args), start_new_session=True)
                    deadline = time.monotonic() + timeout
                    applied = wait_started(proc, started, override, deadline).get("settings", {})
                    try:
                        code = proc.wait(timeout=max(deadline - time.monotonic(), 1))
                    except subprocess.TimeoutExpired:
                        code = "timeout"
            finally:
                if "proc" in locals():
                    kill_groups(proc.pid)
                    proc.wait()
                display.stop()
        recorded_s = time.monotonic() - t0
        result = json.loads(result_file.read_text()) if result_file.exists() else {}
        return finish(args, out, work, avi, frames, code, result, applied, display.kind, recorded_s)
    finally:
        if args.keep_avi and avi.exists():
            shutil.move(avi, out / "movie.avi")
        shutil.rmtree(work, ignore_errors=True)


def finish(args, out, work, avi, frames, code, result, applied, display_kind, recorded_s):
    """Check what Godot recorded, encode it, and report. Returns the exit code."""
    log = out / "godot.log"
    if code == "timeout":
        raise MovieError(f"The recording took longer than --timeout ({args.timeout or 120 + frames} s). Log: {log}")
    if result.get("error"):
        raise MovieError(f"{result['error']} (log: {log})")
    if not avi.exists() or not result:
        tail = "\n".join(log.read_text(errors="replace").splitlines()[-12:]) if log.exists() else "(no log)"
        raise MovieError(f"Godot recorded no movie (exit {code}). Last lines of {log}:\n{tail}")
    if applied and abs(float(applied.get("video_quality", -1)) - args.quality) > 1e-6:
        raise MovieError(f"Movie Maker's quality didn't take ({QUALITY_KEY} is {applied.get('video_quality')}, not "
                         f"{args.quality}): this Godot may not know the setting.")
    want = f"{args.resolution}"
    raw = video_facts(avi)
    problems = []
    if f"{raw['width']}x{raw['height']}" != want:
        problems.append(f"Movie Maker recorded {raw['width']}x{raw['height']}, not the {want} asked for, though gdh "
                        f"set the project's window size override to it (the project or game sets the size otherwise)")
    mismatch = size_mismatch(result.get("window_size"), args.resolution)
    if mismatch:
        problems.append(mismatch)
    if result.get("quit_early"):
        problems.append(f"the game quit by itself after {result.get('frames')} of the {frames} frames asked for")
    if raw["frames"] != frames:
        problems.append(f"Movie Maker recorded {raw['frames']} frames, not {frames}")
    audio = audio_facts(avi)
    t1 = time.monotonic()
    mp4 = out / "movie.mp4"
    encode(avi, mp4, args.crf, audio is not None)
    encoded_s = time.monotonic() - t1
    final = video_facts(mp4)
    if (final["width"], final["height"], final["frames"]) != (raw["width"], raw["height"], raw["frames"]):
        problems.append(f"the MP4 holds {final['frames']} frames at {final['width']}x{final['height']}, not the AVI's "
                        f"{raw['frames']} at {raw['width']}x{raw['height']}")
    sheet = None
    if not args.no_sheet and final["frames"]:
        sheet = sheet_from_video(avi, raw["frames"], args.fps, out / "sheet.png", work)
    findings = covered_findings(result.get("cover_samples", []))
    if findings and sheet:
        # The crop shows the panel on the middle frame of the run.
        middle = work / "middle.png"
        subprocess.run([tool("ffmpeg"), "-y", "-v", "error", "-i", str(avi), "-vf", f"select='eq(n\\,{raw['frames'] // 2})'",
                        "-frames:v", "1", str(middle)], capture_output=True)
        if middle.exists():
            crop_findings(findings, result.get("image_size"), {"normal": middle}, out / "crops", out)
    missing = missing_resources(result.get("errors", []))
    report = {
        "movie": str(mp4), "sheet": str(sheet) if sheet else None, "scene": result.get("scene"),
        "frames": final["frames"], "fps": args.fps, "seconds": round(final["frames"] / args.fps, 3),
        "size": [final["width"], final["height"]], "window_size": result.get("window_size"),
        "audio": audio, "quality": args.quality, "crf": args.crf, "display": display_kind,
        "adapter": result.get("adapter"),
        "recorded_s": round(recorded_s, 2), "encoded_s": round(encoded_s, 2),
        "errors": result.get("errors", []), "findings": findings, "problems": problems,
        **({"missing_resources": missing} if missing else {}),
    }
    (out / "report.json").write_text(json.dumps(report, indent=2))
    errors = sum(e.get("count", 1) for e in report["errors"])
    if audio is None:
        sound = "no audio"
    else:
        sound = "silent audio" if audio["silent"] else f"audio {audio['sample_rate']} Hz, peak {audio['max_db']:.1f} dB"
    print(f"{report['scene']}: {final['frames']} frames at {args.fps} fps ({report['seconds']:.2f} s), "
          f"{final['width']}x{final['height']}, {sound}, display={display_kind} errors={errors} -> {mp4}")
    print(f"  recorded in {recorded_s:.1f} s (Movie Maker, MJPEG), encoded in {encoded_s:.1f} s (H.264, crf {args.crf})")
    if sheet:
        print(f"  contact sheet: {sheet}")
    for f in findings:
        crop = f"  [{f['crop']}]" if f.get("crop") else ""
        print(f"  {f['severity']}: {f['probe']} {f['node']}: {f['message']}{crop}")
    if missing:
        print(f"  {describe_missing(missing)}")
    for problem in problems:
        print(f"gdh: {problem}", file=sys.stderr)
    return 1 if problems else 0


def add_parser(sub, add_display_option):
    p = sub.add_parser("movie", help="Record a video (MP4, with audio) of a game with Godot's Movie Maker, off-screen "
                                     "(arguments after -- go to the game)")
    p.add_argument("--project", required=True, help="Godot project directory")
    p.add_argument("--scene", help="res:// path (default: the project's main scene)")
    p.add_argument("--out", required=True, help="Output directory: movie.mp4, sheet.png, report.json and the logs")
    length = p.add_mutually_exclusive_group(required=True)
    length.add_argument("--seconds", type=float, help="How long to record, in game seconds")
    length.add_argument("--frames", type=int, help="How many frames to record")
    p.add_argument("--fps", type=int, default=60, help="Frames a second, fixed: game time per frame is 1/fps (60)")
    p.add_argument("--resolution", default="1920x1080", help="The movie's size, and the window's (1920x1080)")
    add_display_option(p)
    p.add_argument("--quality", type=float, default=1.0,
                   help="Movie Maker's MJPEG quality, over 0 up to 1, before the H.264 encode (1.0; Godot's default "
                        "is 0.75)")
    p.add_argument("--crf", type=int, default=18,
                   help="The H.264 encode's quality: lower is better and larger (18)")
    p.add_argument("--keep-avi", action="store_true", help="Keep Movie Maker's MJPEG AVI as movie.avi")
    p.add_argument("--no-sheet", action="store_true", help="Don't make the contact sheet")
    p.add_argument("--timeout", type=int, help="Seconds allowed for the recording (default 120 plus a second a frame)")
    p.add_argument("--no-build", action="store_true", help="Don't build a C# project's assemblies first")
    p.add_argument("--no-import", action="store_true",
                   help="Don't import the project first when its import cache is missing or stale")
    p.set_defaults(func=cmd_movie, game_args=[])
