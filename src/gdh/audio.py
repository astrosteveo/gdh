"""Audio you can see: a recording's waveform and spectrogram on one time axis marked in game frames, with clipping and
silences flagged (gdh measure audio, and audio.png beside gdh movie's movie.mp4).

The samples come from a WAV file, or through ffmpeg from a video or any audio file: a movie gdh recorded (Movie Maker
records its audio frame for frame with its video, so a frame marker on the image is that frame), or a game's own
capture. A live session's audio isn't one: gdh runs games on Godot's dummy audio driver, which mixes in real time
while a step runs faster than that, so its sound doesn't line up with its frames.
"""
import json
import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from gdh.godot import GdhError

WIDTH = 1200
WAVE_H = 180
SPEC_H = 220
LEFT = 70
CLIP_LEVEL = 0.999
SILENCE_DB = -60.0
SILENCE_S = 0.5
WINDOW_S = 0.02


class AudioError(GdhError):
    pass


def read_samples(path, rate=44100):
    """A file's sound as mono float32 (-1 to 1) and its sample rate: a WAV read as it is, anything else through
    ffmpeg."""
    path = Path(path)
    if not path.is_file():
        raise AudioError(f"No such file: {path}")
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path)) as w:
                width, channels, rate = w.getsampwidth(), w.getnchannels(), w.getframerate()
                raw = w.readframes(w.getnframes())
            if width in (1, 2, 4):
                kind = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
                data = np.frombuffer(raw, dtype=kind).astype(np.float32)
                data = (data - 128) / 128 if width == 1 else data / float(2 ** (8 * width - 1))
                return data.reshape(-1, channels).mean(axis=1), rate
        except wave.Error:
            pass  # a WAV the module can't read (float samples): ffmpeg can
    from gdh.movie import tool
    proc = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar",
                           str(rate), "-f", "f32le", "-"], capture_output=True)
    if proc.returncode != 0:
        message = proc.stderr.decode(errors="replace").strip()[-400:]
        raise AudioError(f"No sound to read in {path}: {message or 'ffmpeg found no audio stream'}")
    return np.frombuffer(proc.stdout, dtype=np.float32), rate


def runs(flags):
    """The runs of true: [(first, last)] by index."""
    out, start = [], None
    for i, f in enumerate(np.append(flags, False)):
        if f and start is None:
            start = i
        elif not f and start is not None:
            out.append((start, i - 1))
            start = None
    return out


def analyse(samples, rate, fps, silence_db=SILENCE_DB, silence_s=SILENCE_S):
    """Peak, RMS, clipping (samples at or over full scale, in runs: a float mix can go over, and clips when it's
    converted) and silences (windows of WINDOW_S under silence_db for
    silence_s or more), each with its seconds and game frames."""
    if not len(samples):
        raise AudioError("The sound is empty.")
    peak = float(np.abs(samples).max())
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    clipped = runs(np.abs(samples) >= CLIP_LEVEL)
    window = max(1, int(rate * WINDOW_S))
    n = len(samples) // window
    blocks = samples[:n * window].reshape(n, window).astype(np.float64)
    level = 20 * np.log10(np.sqrt((blocks ** 2).mean(axis=1)) + 1e-12)
    quiet = [(a, b) for a, b in runs(level < silence_db) if (b - a + 1) * WINDOW_S >= silence_s]

    def at(seconds):
        return {"seconds": round(seconds, 3), "frame": int(seconds * fps)}

    return {
        "seconds": round(len(samples) / rate, 3), "sample_rate": rate, "fps": fps,
        "peak_db": round(20 * np.log10(peak + 1e-12), 2) + 0.0, "rms_db": round(20 * np.log10(rms + 1e-12), 2) + 0.0,
        "clipping": [{"from": at(a / rate), "to": at(b / rate), "samples": b - a + 1} for a, b in clipped][:200],
        "clip_runs": len(clipped), "clipped_samples": int(sum(b - a + 1 for a, b in clipped)),
        "silences": [{"from": at(a * WINDOW_S), "to": at((b + 1) * WINDOW_S)} for a, b in quiet],
    }


def spectrogram(samples, rate, columns, rows, low=40.0):
    """Magnitudes in dB, (rows, columns), on a log frequency axis from `low` to half the sample rate, the top row the
    highest."""
    size = 2048
    hop = max(1, (len(samples) - size) // max(columns - 1, 1)) if len(samples) > size else 1
    padded = np.pad(samples, (0, max(0, size + hop * columns - len(samples))))
    window = np.hanning(size).astype(np.float32)
    frames = np.stack([padded[i * hop:i * hop + size] * window for i in range(columns)])
    mags = np.abs(np.fft.rfft(frames, axis=1)) / (size / 4)
    freqs = np.fft.rfftfreq(size, 1 / rate)
    edges = np.geomspace(low, rate / 2, rows + 1)
    out = np.empty((rows, columns), dtype=np.float32)
    for r in range(rows):
        sel = (freqs >= edges[r]) & (freqs < edges[r + 1])
        band = mags[:, sel].max(axis=1) if sel.any() else mags[:, np.argmin(np.abs(freqs - edges[r]))]
        out[rows - 1 - r] = 20 * np.log10(band + 1e-9)
    return out, edges


def draw(samples, rate, facts, out, title):
    """The waveform over the spectrogram, sharing a time axis marked in seconds and game frames, clipping in red and
    silences shaded."""
    font = ImageFont.load_default(size=12)
    plot_w = WIDTH - LEFT - 10
    height = 24 + WAVE_H + 12 + SPEC_H + 40
    img = Image.new("RGB", (WIDTH, height), (22, 22, 24))
    d = ImageDraw.Draw(img)
    d.text((LEFT, 6), title, fill=(230, 230, 230), font=font)
    seconds = len(samples) / rate
    top = 24

    def x_of(t):
        return LEFT + t / max(seconds, 1e-9) * plot_w

    for s in facts["silences"]:
        d.rectangle((x_of(s["from"]["seconds"]), top, x_of(s["to"]["seconds"]), top + WAVE_H), fill=(40, 40, 52))
    # The waveform: each column's lowest and highest sample.
    edges = np.linspace(0, len(samples), plot_w + 1).astype(int)
    mid = top + WAVE_H / 2
    for c in range(plot_w):
        part = samples[edges[c]:max(edges[c + 1], edges[c] + 1)]
        lo, hi = float(part.min()), float(part.max())
        clip = hi >= CLIP_LEVEL or lo <= -CLIP_LEVEL
        lo, hi = max(lo, -1.0), min(hi, 1.0)  # over full scale (a float mix) is drawn at it
        d.line((LEFT + c, mid - hi * WAVE_H / 2, LEFT + c, mid - lo * WAVE_H / 2),
               fill=(235, 70, 60) if clip else (110, 190, 255))
    d.line((LEFT, mid, LEFT + plot_w, mid), fill=(70, 70, 80))
    for v, label in ((1, "+1"), (0, "0"), (-1, "-1")):
        d.text((LEFT - 24, mid - v * WAVE_H / 2 - 6), label, fill=(150, 150, 150), font=font)
    # The spectrogram.
    spec_top = top + WAVE_H + 12
    mags, freqs = spectrogram(samples, rate, plot_w, SPEC_H)
    t = np.clip((mags + 100) / 100, 0, 1)
    rgb = np.stack([np.clip(1.5 * t, 0, 1), np.clip(1.5 * t - 0.5, 0, 1), np.clip(3 * t - 2, 0, 1)], axis=-1)
    img.paste(Image.fromarray((rgb * 255).astype(np.uint8)), (LEFT, spec_top))
    for hz in (100, 1000, 10000):
        if freqs[0] <= hz <= freqs[-1]:
            y = spec_top + SPEC_H - np.log(hz / freqs[0]) / np.log(freqs[-1] / freqs[0]) * SPEC_H
            d.text((6, y - 6), f"{hz if hz < 1000 else str(hz // 1000) + 'k'} Hz", fill=(150, 150, 150), font=font)
            d.line((LEFT - 4, y, LEFT, y), fill=(150, 150, 150))
    # The time axis: seconds, and the game frame at each mark.
    axis = spec_top + SPEC_H + 4
    step = next(s for s in (0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300) if seconds / s <= 12)
    t_mark = 0.0
    while t_mark <= seconds + 1e-9:
        x = x_of(t_mark)
        d.line((x, top, x, top + WAVE_H), fill=(55, 55, 65))
        d.line((x, axis - 4, x, axis), fill=(150, 150, 150))
        d.text((x - 14, axis + 2), f"{t_mark:g} s", fill=(200, 200, 200), font=font)
        d.text((x - 14, axis + 16), f"f {int(round(t_mark * facts['fps']))}", fill=(150, 150, 150), font=font)
        t_mark += step
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return str(out)


def describe(facts):
    lines = [f"{facts['seconds']:g} s at {facts['sample_rate']} Hz: peak {facts['peak_db']:g} dBFS, RMS "
             f"{facts['rms_db']:g} dBFS"]
    if facts["clipping"]:
        first = facts["clipping"][0]
        lines.append(f"  clipping: {facts['clipped_samples']} samples at full scale, in {facts['clip_runs']} "
                     f"runs, the first at {first['from']['seconds']:g} s (frame {first['from']['frame']})")
    else:
        lines.append("  no clipping")
    for s in facts["silences"][:10]:
        lines.append(f"  silent from {s['from']['seconds']:g} s to {s['to']['seconds']:g} s (frames {s['from']['frame']} "
                     f"to {s['to']['frame']})")
    if len(facts["silences"]) > 10:
        lines.append(f"  ... {len(facts['silences']) - 10} more silences")
    return "\n".join(lines)


def picture(path, out, fps, title=None, silence_db=SILENCE_DB, silence_s=SILENCE_S):
    """Read a file's sound, analyse it and draw it. Returns the facts, with "image"."""
    samples, rate = read_samples(path)
    facts = analyse(samples, rate, fps, silence_db, silence_s)
    facts["image"] = draw(samples, rate, facts, out, title or f"{Path(path).name}: waveform, and spectrogram below")
    return facts


def cmd_audio(args):
    fps = args.fps
    if fps is None:
        report = Path(args.file).with_name("report.json")
        fps = json.loads(report.read_text()).get("fps", 60) if report.is_file() else 60
    out = Path(args.out) if args.out else Path(args.file).with_name(Path(args.file).stem + "-audio.png")
    facts = picture(args.file, out, fps, silence_db=args.silence_db, silence_s=args.silence)
    if args.json:
        print(json.dumps(facts, indent=1))
    else:
        print(describe(facts))
        print(f"image: {facts['image']}")
    return 1 if args.fail and facts["clipping"] else 0


def add_measure_parsers(kinds):
    """gdh measure audio. `kinds` is gdh measure's subparsers."""
    k = kinds.add_parser("audio", help="A recording's waveform and spectrogram in one image, its time axis in seconds "
                                       "and game frames, with clipping and silences flagged")
    k.add_argument("file", help="A movie (gdh movie's movie.mp4), any video or audio file, or a WAV")
    k.add_argument("--out", metavar="PNG", help="Where the image goes (default FILE-audio.png beside it)")
    k.add_argument("--fps", type=float, help="Game frames a second, for the frame marks (default: the movie's "
                                             "report.json beside it, else 60)")
    k.add_argument("--silence-db", type=float, default=SILENCE_DB, metavar="DB",
                   help=f"Quieter than this is silence (default {SILENCE_DB:g} dBFS)")
    k.add_argument("--silence", type=float, default=SILENCE_S, metavar="S",
                   help=f"Silences this long or longer are listed (default {SILENCE_S:g} s)")
    k.add_argument("--fail", action="store_true", help="Exit 1 when it clips")
    k.add_argument("--json", action="store_true", help="Print the whole result")
    k.set_defaults(func=cmd_audio)
