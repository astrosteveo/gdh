"""Line charts of values over game frames, drawn with Pillow: `gdh live step --trace-chart` and `gdh measure chart`.

A trace (bridge.gd's step result, or the CSV `--trace-out` writes) has a row per check: the game frame, then each
expression's value. Each expression gets a panel of its own with its own vertical scale, all sharing the frame axis,
so a position in hundreds of pixels and a flag that's 0 or 1 both read. A number is one line; a vector ([x, y] or
[x, y, z]) is a line for each component; true and false are 1 and 0, drawn as steps. A value of another kind (a string, a
dictionary) can't be drawn, and the panel says so. A value that failed (null) leaves a gap in the line.

With rates, each expression also gets a panel for its rate of change and one for the rate of that (velocity and
acceleration for a position), per second at the game's tick rate, from the differences between checks (not for true
and false).
"""
import csv
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH = 1200
PANEL_HEIGHT = 170
LEFT = 78
RIGHT = 16
TOP = 30
GAP = 34
BOTTOM = 42
MAX_PANELS = 12
# Checks at least this many pixels apart get a dot each.
DOT_SPACING = 12

BACKGROUND = (255, 255, 255)
INK = (34, 34, 34)
MUTED = (110, 110, 110)
GRID = (226, 226, 226)
# Distinct on white and to most color-blind readers: blue, orange, green, magenta (the first three are x, y, z).
COLORS = [(31, 104, 196), (222, 110, 20), (24, 150, 72), (190, 40, 150), (120, 80, 200), (150, 110, 40)]
COMPONENTS = "xyzw"


class ChartError(Exception):
    pass


def series_of(exprs, rows):
    """Each expression's lines: [(expr, [(name, [value or None per row])] or a reason it can't be drawn)]."""
    out = []
    for i, expr in enumerate(exprs):
        values = [row[i + 1] if i + 1 < len(row) else None for row in rows]
        out.append((expr, lines_of(values)))
    return out


def lines_of(values):
    """One expression's values as named lines, or a string saying why they can't be drawn."""
    width = None
    for v in values:
        if v is None:
            continue
        if isinstance(v, bool) or isinstance(v, (int, float)):
            n = 0
        elif isinstance(v, list) and v and all(c is None or isinstance(c, (int, float)) and not isinstance(c, bool)
                                               for c in v):
            n = len(v)
        else:
            kind = "text" if isinstance(v, str) else "a dictionary" if isinstance(v, dict) else "a list of other values"
            return f"not drawn: its values are {kind}, not numbers"
        if width is None:
            width = n
        elif width != n:
            return "not drawn: its values change shape (a number, then a vector)"
    if width is None:
        return "not drawn: every value failed"
    if width == 0:
        flags = all(v is None or isinstance(v, bool) for v in values)
        return [("true or false" if flags else "value", [None if v is None else float(v) for v in values])]
    names = list(COMPONENTS[:width]) if width <= len(COMPONENTS) else [str(k) for k in range(width)]
    return [(names[k], [None if v is None or v[k] is None else float(v[k]) for v in values]) for k in range(width)]


def rate(frames, values, ticks):
    """Change per second between checks, at the middle of each pair of checks; None across a gap."""
    out_frames, out = [], []
    for (f0, v0), (f1, v1) in zip(zip(frames, values), zip(frames[1:], values[1:])):
        out_frames.append((f0 + f1) / 2)
        out.append(None if v0 is None or v1 is None or f1 == f0 else (v1 - v0) * ticks / (f1 - f0))
    return out_frames, out


def panels_of(exprs, rows, rates=False, ticks=60):
    """The panels to draw: [{"title", "frames", "lines": [(name, values)]} or {"title", "note"}]."""
    frames = [row[0] for row in rows]
    panels = []
    for expr, lines in series_of(exprs, rows):
        if isinstance(lines, str):
            panels.append({"title": expr, "note": lines})
            continue
        flags = lines[0][0] == "true or false"
        panels.append({"title": expr, "frames": frames, "lines": lines, "steps": flags})
        if not rates or flags:
            continue
        speed = [(name, rate(frames, values, ticks)) for name, values in lines]
        mid = speed[0][1][0] if speed else []
        panels.append({"title": f"d/dt {expr} (per second)", "frames": mid,
                       "lines": [(name, v) for name, (_, v) in speed]})
        accel = [(name, rate(mid, v, ticks)) for name, (_, v) in speed]
        panels.append({"title": f"d2/dt2 {expr} (per second squared)", "frames": accel[0][1][0] if accel else [],
                       "lines": [(name, v) for name, (_, v) in accel]})
    return panels


def nice_ticks(lo, hi, count=5):
    """Round values to mark on an axis from lo to hi: about `count` of them, 1, 2 or 5 times a power of ten apart."""
    if hi == lo:
        pad = abs(lo) * 0.1 or 1.0
        lo, hi = lo - pad, hi + pad
    raw = (hi - lo) / count
    step = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if raw <= m * step:
            step *= m
            break
    first = math.ceil(lo / step) * step
    ticks = []
    v = first
    while v <= hi + step * 1e-9:
        ticks.append(0.0 if abs(v) < step * 1e-9 else v)
        v += step
    return lo, hi, ticks, step


def label(v, step):
    """A tick's value with only the decimals the step between ticks needs."""
    decimals = max(0, -math.floor(math.log10(step))) if step < 1 else 0
    text = f"{v:.{decimals}f}"
    if abs(v) >= 1e5:
        text = f"{v:.3g}"
    return text


def draw(panels, out, title=None):
    """Draw the panels one under another, sharing the frame axis, and save the PNG. Returns out."""
    if not panels:
        raise ChartError("Nothing to chart: no expressions.")
    if len(panels) > MAX_PANELS:
        raise ChartError(f"{len(panels)} panels is too many to read in one chart (at most {MAX_PANELS}): chart fewer "
                         f"expressions, or leave out the rates.")
    font = ImageFont.load_default(size=13)
    small = ImageFont.load_default(size=11)
    drawn = [p for p in panels if "lines" in p]
    all_frames = [f for p in drawn for f in p["frames"]]
    if not all_frames:
        raise ChartError("Nothing to chart: the trace has no rows.")
    f_lo, f_hi = min(all_frames), max(all_frames)
    if f_hi == f_lo:
        f_lo, f_hi = f_lo - 1, f_hi + 1
    heights = [PANEL_HEIGHT if "lines" in p else 26 for p in panels]
    top = TOP + (22 if title else 0)
    height = top + sum(heights) + GAP * (len(panels) - 1) + BOTTOM
    img = Image.new("RGB", (WIDTH, height), BACKGROUND)
    d = ImageDraw.Draw(img)
    if title:
        d.text((LEFT, 8), title, fill=INK, font=font)
    x0, x1 = LEFT, WIDTH - RIGHT

    def fx(f):
        return x0 + (f - f_lo) * (x1 - x0) / (f_hi - f_lo)

    _, _, frame_ticks, frame_step = nice_ticks(f_lo, f_hi, 10)
    frame_ticks = [t for t in frame_ticks if f_lo <= t <= f_hi]
    y = top
    for panel, h in zip(panels, heights):
        if "note" in panel:
            d.text((x0, y + 6), f"{panel['title']}: {panel['note']}", fill=MUTED, font=font)
            y += h + GAP
            continue
        values = [v for _, line in panel["lines"] for v in line if v is not None]
        lo, hi = (min(values), max(values)) if values else (0.0, 1.0)
        span = hi - lo
        lo, hi, ticks, step = nice_ticks(lo - span * 0.05, hi + span * 0.05)
        lo, hi = min(lo, ticks[0]), max(hi, ticks[-1])
        y0, y1 = y + 16, y + h

        def fy(v):
            return y1 - (v - lo) * (y1 - y0) / (hi - lo)

        for t in ticks:
            ty = fy(t)
            d.line((x0, ty, x1, ty), fill=GRID)
            text = label(t, step)
            d.text((x0 - 6 - d.textlength(text, font=small), ty - 6), text, fill=MUTED, font=small)
        for t in frame_ticks:
            d.line((fx(t), y0, fx(t), y1), fill=GRID)
        d.rectangle((x0, y0, x1, y1), outline=MUTED)
        d.text((x0, y), panel["title"], fill=INK, font=font)
        # The legend, at the right of the title line: only when there's more than the one line.
        if len(panel["lines"]) > 1:
            lx = x1
            for k, (name, _) in reversed(list(enumerate(panel["lines"]))):
                lx -= d.textlength(name, font=font) + 4
                d.text((lx, y), name, fill=INK, font=font)
                lx -= 18
                d.line((lx, y + 8, lx + 14, y + 8), fill=COLORS[k % len(COLORS)], width=3)
                lx -= 10
        frames = panel["frames"]
        # A dot at each check when they're far enough apart to tell apart, so the spacing of the checks shows.
        dots = len(frames) > 0 and (x1 - x0) / len(frames) >= DOT_SPACING and not panel.get("steps")
        for k, (_, line) in enumerate(panel["lines"]):
            color = COLORS[k % len(COLORS)]
            run = []
            for f, v in zip(frames, line):
                if v is None:
                    _stroke(d, run, color, dots)
                    run = []
                    continue
                if panel.get("steps") and run:
                    run.append((fx(f), run[-1][1]))  # true or false holds until the next check
                run.append((fx(f), fy(v)))
            _stroke(d, run, color, dots)
        y += h + GAP
    # The frame axis, under the last panel.
    axis_y = y - GAP + 4
    for t in frame_ticks:
        text = label(t, frame_step)
        d.text((fx(t) - d.textlength(text, font=small) / 2, axis_y), text, fill=MUTED, font=small)
    caption = "game frame"
    d.text(((x0 + x1) / 2 - d.textlength(caption, font=font) / 2, axis_y + 16), caption, fill=INK, font=font)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def _stroke(d, points, color, dots):
    """A run of points as a line, with a dot at each point when `dots`, or alone (a value between two gaps)."""
    if len(points) > 1:
        d.line(points, fill=color, width=2, joint="curve")
    if dots or len(points) == 1:
        for x, y in points:
            d.ellipse((x - 2, y - 2, x + 2, y + 2), fill=color)


def trace_chart(trace, out, rates=False, ticks=60, title=None):
    """A step's trace ({"exprs", "every", "rows"}) as a chart. Returns (out, notes): notes say what wasn't drawn."""
    panels = panels_of(trace["exprs"], trace["rows"], rates, ticks)
    draw(panels, out, title)
    return out, [f"{p['title']}: {p['note']}" for p in panels if "note" in p]


def read_trace_csv(path):
    """A trace from the CSV `--trace-out` writes: a frame column, then one per expression, each value a string as it
    is or JSON, an empty cell for a value that failed."""
    try:
        with open(path, newline="") as f:
            table = list(csv.reader(f))
    except OSError as e:
        raise ChartError(f"Can't read {path}: {e.strerror}.") from None
    if not table or not table[0] or table[0][0] != "frame":
        raise ChartError(f"{path} isn't a trace: its first column must be frame (gdh live step --trace-out writes one).")
    rows = []
    for number, cells in enumerate(table[1:], 2):
        if not cells:
            continue
        try:
            row = [float(cells[0])]
        except ValueError:
            raise ChartError(f"{path} line {number}: the frame {cells[0]!r} isn't a number.") from None
        for cell in cells[1:]:
            if cell == "":
                row.append(None)
                continue
            try:
                row.append(json.loads(cell))
            except ValueError:
                row.append(cell)
        rows.append(row)
    return {"exprs": table[0][1:], "rows": rows}
