"""Measurements over rendered frames: flicker, shimmer, thin lines, points' sizes, dissolves, black from a NaN, crushed
blacks, light jitter, what one setting adds, and frame times.

Every image measure works on PNG files: frames a capture or a live session saved, or any game's. Pixel values are
luminance in Rec. 709's weights on the 0-255 sRGB values the files hold, computed in float32, so numbers taken from
the same frames are the same on every machine. Each function returns plain dictionaries, which `gdh measure` prints
and `gdh live measure` takes over frames it records.

A region limits a measure to some pixels: a box (x0, y0, x1, y1 in image pixels, the far edges excluded) and/or a
mask image (white, luminance over 127, is measured).
"""
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

WEIGHTS = (0.2126, 0.7152, 0.0722)


class MeasureError(Exception):
    pass


# --- Frames and regions --------------------------------------------------------------------------------------------


def frame_paths(sources):
    """PNG paths from files and directories (a directory's *.png in name order), in the order given."""
    paths = []
    for source in sources:
        p = Path(source)
        if p.is_dir():
            paths += sorted(p.glob("*.png"))
        elif p.is_file():
            paths.append(p)
        else:
            raise MeasureError(f"No such file or directory: {source}")
    return paths


def rgb(path):
    """An image as float32 RGB, 0-255."""
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)


def luminance(a):
    """Rec. 709 luminance of float32 RGB, in float32."""
    return WEIGHTS[0] * a[..., 0] + WEIGHTS[1] * a[..., 1] + WEIGHTS[2] * a[..., 2]


def lum(path):
    return luminance(rgb(path))


class Region:
    """Which pixels a measure reads: a box, a mask image, both, or every pixel."""

    def __init__(self, box=None, mask=None):
        self.box = [int(v) for v in box] if box else None
        self.mask_path = mask
        self._mask = None

    def __bool__(self):
        return bool(self.box or self.mask_path)

    def describe(self):
        out = {}
        if self.box:
            out["box"] = self.box
        if self.mask_path:
            out["mask"] = str(self.mask_path)
        return out or None

    def select(self, shape):
        """A boolean array of the pixels measured, for an image of this shape, or None for every pixel."""
        if not self:
            return None
        sel = np.ones(shape[:2], dtype=bool)
        if self.box:
            x0, y0, x1, y1 = self.box
            only = np.zeros_like(sel)
            only[max(y0, 0):max(y1, 0), max(x0, 0):max(x1, 0)] = True
            sel &= only
        if self.mask_path:
            if self._mask is None:
                self._mask = lum(self.mask_path) > 127
            if self._mask.shape != sel.shape:
                raise MeasureError(f"The mask {self.mask_path} is {self._mask.shape[1]}x{self._mask.shape[0]}, "
                                   f"the frames {shape[1]}x{shape[0]}.")
            sel &= self._mask
        if not sel.any():
            raise MeasureError("The region holds no pixels.")
        return sel


def pick(values, sel):
    return values if sel is None else values[sel]


def need(paths, count, what):
    if len(paths) < count:
        raise MeasureError(f"{what} needs at least {count} frames; got {len(paths)}.")


# --- Flicker and shimmer -------------------------------------------------------------------------------------------


def flicker(paths, region=None, threshold=2.0):
    """How much each pixel changes from frame to frame (a still camera should change nothing).

    mean_change: |L(t) - L(t-1)| averaged over the pixels, then over the frame pairs. range_p99: the 99th percentile
    of each pixel's range (max - min) over the run. share_changed_over_2: the share of pixels that ever change by
    more than the threshold (2) from one frame to the next.
    """
    need(paths, 3, "flicker")
    region = region or Region()
    lo = hi = changed = prev = None
    diffs = []
    for p in paths:
        L = lum(p)
        sel = region.select(L.shape)
        L = pick(L, sel)
        lo = L if lo is None else np.minimum(lo, L)
        hi = L if hi is None else np.maximum(hi, L)
        if prev is not None:
            d = np.abs(L - prev)
            diffs.append(float(d.mean()))
            c = d > threshold
            changed = c if changed is None else changed | c
        prev = L
    rng = hi - lo
    return {
        "frames": len(paths),
        "pixels": int(rng.size),
        "mean_change": round(float(np.mean(diffs)), 4),
        "range_p99": round(float(np.percentile(rng, 99)), 2),
        "range_max": round(float(rng.max()), 2),
        f"share_changed_over_{threshold:g}": round(float(changed.mean()), 5),
    }


def second_differences(paths, region=None):
    """|L(t-1) - 2 L(t) + L(t+1)| for each run of three frames, streamed: smooth motion leaves it small, while swim,
    crawl, sparkle and flicker don't."""
    region = region or Region()
    window = []
    for p in paths:
        L = lum(p)
        window = (window + [pick(L, region.select(L.shape))])[-3:]
        if len(window) == 3:
            yield np.abs(window[0] - 2 * window[1] + window[2])


def shimmer(paths, region=None, threshold=8.0):
    """The second difference over time: each triple's mean, 99th percentile and share over the threshold (8),
    averaged over the triples. It compares like shots only: moving edges count too."""
    need(paths, 3, "shimmer")
    means, p99s, over = [], [], []
    worst = 0.0
    for s in second_differences(paths, region):
        means.append(float(s.mean()))
        p99s.append(float(np.percentile(s, 99)))
        over.append(float((s > threshold).mean()))
        worst = max(worst, float(s.max()))
    return {
        "frames": len(paths),
        "second_diff_mean": round(float(np.mean(means)), 4),
        "second_diff_p99": round(float(np.mean(p99s)), 3),
        f"share_over_{threshold:g}": round(float(np.mean(over)), 5),
        "second_diff_max": round(worst, 2),
    }


# --- Lights' jitter, and what one setting adds ----------------------------------------------------------------------


def light_pixels(a, kind):
    """Which pixels count as lights: "bright" (luminance over 200), "red" (r > 150, g < 90, b < 90), or a rule such as
    "r>150,g<90,b<90" or "l>200" (channels r, g, b and luminance l; the comparisons <, >, <=, >=)."""
    if kind == "bright":
        return luminance(a) > 200
    if kind == "red":
        return (a[..., 0] > 150) & (a[..., 1] < 90) & (a[..., 2] < 90)
    sel = np.ones(a.shape[:2], dtype=bool)
    channels = {"r": a[..., 0], "g": a[..., 1], "b": a[..., 2]}
    for term in kind.split(","):
        term = term.strip()
        for op in ("<=", ">=", "<", ">"):
            if op in term:
                name, value = term.split(op, 1)
                break
        else:
            raise MeasureError(f"Can't read the light rule {term!r}: use r, g, b or l, then <, >, <= or >=, then a number.")
        name = name.strip().lower()
        if name == "l":
            values = luminance(a)
        elif name in channels:
            values = channels[name]
        else:
            raise MeasureError(f"Unknown channel {name!r} in {kind!r}: r, g, b or l.")
        v = float(value)
        sel &= {"<": values < v, ">": values > v, "<=": values <= v, ">=": values >= v}[op]
    return sel


def light_sum(a, kind):
    """The summed brightness of a frame's lights: their red channel for "red" (as the reference set took it), their
    luminance otherwise."""
    sel = light_pixels(a, kind)
    if kind == "red":
        return float(a[..., 0][sel].sum())
    return float(luminance(a)[sel].sum())


def jitter_percent(sums):
    """How far each frame's sum strays from its five-frame moving average, in percent (mean over the frames)."""
    x = np.array(sums, dtype=np.float64)
    if len(x) < 5:
        return None
    ma = np.convolve(x, np.ones(5) / 5, mode="valid")
    mid = x[2:-2]
    ok = ma > 0
    return round(float(np.mean(np.abs(mid[ok] - ma[ok]) / ma[ok]) * 100), 3) if ok.any() else None


def jitter(paths, kind="bright", region=None):
    """A light's steadiness: its summed brightness per frame against the five-frame moving average."""
    need(paths, 7, "jitter")
    region = region or Region()
    sums = []
    for p in paths:
        a = rgb(p)
        sel = region.select(a.shape)
        if sel is not None:
            a = np.where(sel[..., None], a, 0)
        sums.append(light_sum(a, kind))
    return {"frames": len(paths), "lights": kind, "sum_mean": round(float(np.mean(sums)), 1),
            "jitter_percent": jitter_percent(sums)}


class Tally:
    """A non-negative quantity's mean, 99th percentile and share over 8, over every value seen (a histogram of
    1/32 steps), so long runs at 4K fit in memory."""

    def __init__(self):
        self.hist = np.zeros(256 * 4 * 32 + 1, dtype=np.int64)
        self.sum = 0.0
        self.n = 0

    def add(self, s):
        self.hist += np.bincount(np.minimum((s * 32).astype(np.int64), len(self.hist) - 1).ravel(),
                                 minlength=len(self.hist))
        self.sum += float(s.sum())
        self.n += s.size

    def result(self):
        if self.n == 0:
            return None
        c = np.cumsum(self.hist)
        p99 = np.searchsorted(c, 0.99 * self.n) / 32
        over = 1 - c[8 * 32] / self.n
        return {"mean": round(self.sum / self.n, 4), "p99": round(float(p99), 3), "over_8": round(float(over), 5)}


class PerFrame:
    """shimmer()'s numbers, a triple at a time."""

    def __init__(self):
        self.means, self.p99s, self.over = [], [], []

    def add(self, s):
        self.means.append(float(s.mean()))
        self.p99s.append(float(np.percentile(s, 99)))
        self.over.append(float((s > 8).mean()))

    def result(self):
        return {"second_diff_mean": round(float(np.mean(self.means)), 4),
                "second_diff_p99": round(float(np.mean(self.p99s)), 3),
                "share_over_8": round(float(np.mean(self.over)), 5)}


def term(without, with_, box=None):
    """What one setting adds to a sequence, and whether it swims: two runs of the same frames, one without the
    setting and one with (each held frame shot twice, the setting the only change). The term is with - without.

    Where the term shows (its mean size over the run above 2): its own second difference (how much what the setting
    adds swims beyond smooth motion), and the whole frame's there, without and with. Over the whole frame: shimmer()'s
    numbers without and with, and the bright and red lights' jitter. Frames stream, so 4K runs fit in memory.
    """
    fa, fb = frame_paths([without]), frame_paths([with_])
    count = min(len(fa), len(fb))
    need(fa[:count], 3, "term")

    def load(p):
        a = rgb(p)
        return a[box[1]:box[3], box[0]:box[2]] if box else a

    size = None
    bright_a, bright_b, red_a, red_b = [], [], [], []
    for t in range(count):
        a, b = load(fa[t]), load(fb[t])
        la, lb = luminance(a), luminance(b)
        size = np.abs(lb - la) if size is None else size + np.abs(lb - la)
        bright_a.append(float(la[la > 200].sum()))
        bright_b.append(float(lb[lb > 200].sum()))
        red_a.append(light_sum(a, "red"))
        red_b.append(light_sum(b, "red"))
    shows = size / count > 2
    tally, where_a, where_b = Tally(), Tally(), Tally()
    whole_a, whole_b = PerFrame(), PerFrame()
    window = []
    for t in range(count):
        la, lb = luminance(load(fa[t])), luminance(load(fb[t]))
        window = (window + [(la, lb)])[-3:]
        if len(window) < 3:
            continue
        (a0, b0), (a1, b1), (a2, b2) = window
        sa = np.abs(a0 - 2 * a1 + a2)
        sb = np.abs(b0 - 2 * b1 + b2)
        st = np.abs((b0 - a0) - 2 * (b1 - a1) + (b2 - a2))
        whole_a.add(sa)
        whole_b.add(sb)
        if shows.any():
            tally.add(st[shows])
            where_a.add(sa[shows])
            where_b.add(sb[shows])
    return {
        "frames": count,
        "term_area": round(float(shows.mean()), 4),
        "term_mean": round(float((size / count)[shows].mean()), 3) if shows.any() else 0,
        "term_swim": tally.result(),
        "where_it_shows_without": where_a.result(),
        "where_it_shows_with": where_b.result(),
        "shimmer_without": whole_a.result(),
        "shimmer_with": whole_b.result(),
        "bright_jitter_without": jitter_percent(bright_a),
        "bright_jitter_with": jitter_percent(bright_b),
        "red_jitter_without": jitter_percent(red_a),
        "red_jitter_with": jitter_percent(red_b),
        "red_pixels_sum_with": round(float(np.mean(red_b)), 1),
    }


# --- Thin lines ---------------------------------------------------------------------------------------------------


def bilinear(L, x, y):
    h, w = L.shape
    x0 = np.clip(np.floor(x).astype(int), 0, w - 2)
    y0 = np.clip(np.floor(y).astype(int), 0, h - 2)
    fx = np.clip(x - x0, 0, 1)
    fy = np.clip(y - y0, 0, 1)
    return (L[y0, x0] * (1 - fx) * (1 - fy) + L[y0, x0 + 1] * fx * (1 - fy) + L[y0 + 1, x0] * (1 - fx) * fy
            + L[y0 + 1, x0 + 1] * fx * fy)


def line_profile(L, a, b, t):
    """The luminance across the line a-b at t (0 at a, 1 at b): offsets -24 to 24 px in quarter pixels."""
    v = np.array([b[0] - a[0], b[1] - a[1]], dtype=np.float64)
    v /= np.linalg.norm(v)
    perp = np.array([-v[1], v[0]])
    cx, cy = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
    o = np.arange(-24, 24.01, 0.25)
    return o, bilinear(L, cx + perp[0] * o, cy + perp[1] * o)


def line_width(L, a, b, samples=24, span=(0.2, 0.8), min_contrast=12.0, min_length=40.0, margin=20):
    """A thin line's width and brightness: across the line from a to b (image pixels; a third coordinate over 1
    means behind the camera, and the line is skipped), at `samples` points along its middle (`span`), the full
    width at half maximum above the background (the median of the profile's outer four pixels each side), in
    pixels, and the peak above the background. The medians over the points, or None when no point stood out by
    min_contrast. Lines shorter than min_length, and points within margin of the edge, are skipped."""
    if len(a) > 2 and a[2] > 1 or len(b) > 2 and b[2] > 1:
        return None
    if math.hypot(b[0] - a[0], b[1] - a[1]) < min_length:
        return None
    h, w = L.shape
    widths, peaks = [], []
    for t in np.linspace(span[0], span[1], samples):
        cx, cy = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        if not (margin < cx < w - margin and margin < cy < h - margin):
            continue
        _, prof = line_profile(L, a, b, t)
        bg = float(np.median(np.concatenate([prof[:16], prof[-16:]])))
        peak = float(prof.max())
        if peak - bg < min_contrast:
            continue
        half = bg + (peak - bg) / 2
        widths.append(float((prof >= half).sum() * 0.25))
        peaks.append(peak - bg)
    if not widths:
        return None
    return {"fwhm_px": round(float(np.median(widths)), 2), "peak_above_bg": round(float(np.median(peaks)), 1),
            "points": len(widths)}


def lines(path, segments, **kw):
    """line_width for each segment ({"a": [x, y(, z)], "b": [...], ...}) on one image; the segments' other keys
    are kept on each result."""
    L = lum(path)
    out = []
    for s in segments:
        found = line_width(L, s["a"], s["b"], **kw)
        if found:
            out.append({**{k: v for k, v in s.items() if k not in ("a", "b")}, **found})
    return out


# --- Points: stars, motes, sparks ---------------------------------------------------------------------------------


def max_filter(a, r):
    """Each pixel's largest value within r pixels each way (a square), separably."""
    out = a.copy()
    for axis in (0, 1):
        src = out.copy()
        for d in range(1, r + 1):
            for sign in (1, -1):
                shifted = np.full_like(src, -np.inf)
                if axis == 0:
                    if sign > 0:
                        shifted[d:] = src[:-d]
                    else:
                        shifted[:-d] = src[d:]
                else:
                    if sign > 0:
                        shifted[:, d:] = src[:, :-d]
                    else:
                        shifted[:, :-d] = src[:, d:]
                np.maximum(out, shifted, out=out)
    return out


def spot_size(L, x, y, r):
    """One point's size, from the window r pixels round its peak at (x, y): the background (the median of the
    window's rim), the peak above it, the full width at half maximum as the diameter of a disc of the same area
    (the pixels at or over half the peak, sampled every quarter pixel, bilinear), and the Gaussian's sigma from the
    second moment of the light above the background."""
    win = L[y - r:y + r + 1, x - r:x + r + 1]
    rim = np.concatenate([win[0], win[-1], win[1:-1, 0], win[1:-1, -1]])
    bg = float(np.median(rim))
    peak = float(L[y, x]) - bg
    o = np.arange(-r, r + 0.01, 0.25)
    gx, gy = np.meshgrid(x + o, y + o)
    up = bilinear(L, gx, gy) - bg
    area = float((up >= peak / 2).sum()) / 16
    light = np.clip(win - bg, 0, None)
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    total = float(light.sum())
    sigma = math.sqrt(float((light * (xx * xx + yy * yy)).sum()) / (2 * total)) if total > 0 else 0.0
    return {"x": int(x), "y": int(y), "fwhm_px": round(2 * math.sqrt(area / math.pi), 2), "sigma_px": round(sigma, 3),
            "peak_above_bg": round(peak, 1), "background": round(bg, 1)}


def spots(path, region=None, radius=5, min_contrast=12.0, limit=2000):
    """Isolated points of light in one image (stars, dust, sparks): each local peak that stands min_contrast above
    the median of the window `radius` pixels round it, with nothing as bright within that window (so two points
    close together, or the edge of something larger, aren't counted), and its size (spot_size). The brightest
    `limit` points, at least radius + 2 pixels from the image's edge."""
    L = lum(path)
    h, w = L.shape
    r = int(radius)
    peaks = (L >= max_filter(L, r)) & (L > 0)
    sel = region.select(L.shape) if region else None
    if sel is not None:
        peaks &= sel
    peaks[:r + 2] = False
    peaks[-(r + 2):] = False
    peaks[:, :r + 2] = False
    peaks[:, -(r + 2):] = False
    ys, xs = np.nonzero(peaks)
    order = np.argsort(-L[ys, xs], kind="stable")
    found = []
    taken = np.zeros_like(peaks)
    for i in order:
        y, x = int(ys[i]), int(xs[i])
        if taken[y, x]:
            continue  # (a flat top's other pixels)
        win = L[y - r:y + r + 1, x - r:x + r + 1]
        if (win >= L[y, x]).sum() > 4:
            continue  # a plateau or a larger shape, not a point
        s = spot_size(L, x, y, r)
        if s["peak_above_bg"] < min_contrast:
            continue
        # Isolated: the window's rim falls to near the background (another point or an edge would hold it up).
        rim = np.concatenate([win[0], win[-1], win[1:-1, 0], win[1:-1, -1]])
        if float(rim.max()) - s["background"] > s["peak_above_bg"] * 0.25:
            continue
        taken[y - r:y + r + 1, x - r:x + r + 1] = True
        found.append(s)
        if len(found) >= limit:
            break
    return found


def spots_summary(each):
    """The spots' sizes over every frame: how many, and the spread of their widths, sigmas and peaks."""
    out = {"spots": len(each)}
    if not each:
        return out
    for key in ("fwhm_px", "sigma_px", "peak_above_bg"):
        v = np.array([s[key] for s in each], dtype=np.float64)
        out[key] = {"min": round(float(v.min()), 3), "p10": round(float(np.percentile(v, 10)), 3),
                    "median": round(float(np.median(v)), 3), "p90": round(float(np.percentile(v, 90)), 3),
                    "max": round(float(v.max()), 3)}
    return out


# --- Dissolves ----------------------------------------------------------------------------------------------------


def coverage(path):
    """Where a mask frame is drawn: any channel over half."""
    return rgb(path).max(axis=2) > 127.5


def erode(m):
    """A mask shrunk by a pixel (its four neighbours), so a region's anti-aliased rim doesn't count."""
    e = m.copy()
    e[1:] &= m[:-1]
    e[:-1] &= m[1:]
    e[:, 1:] &= m[:, :-1]
    e[:, :-1] &= m[:, 1:]
    return e


def dissolve(a, b, regions):
    """A dissolve between two layers, each drawn alone as a mask (white where it draws): inside the region, every
    pixel should be drawn by exactly one. doubled_px: drawn by both; empty_px: drawn by neither; b_share: the share
    the second layer draws. The region is where both layers draw when whole (the masks given, intersected), shrunk
    by a pixel."""
    sa, sb = coverage(a), coverage(b)
    region = None
    for r in regions:
        m = coverage(r)
        region = m if region is None else region & m
    if region is None:
        raise MeasureError("dissolve needs a region: a mask of where the layers draw when whole.")
    region = erode(region)
    if not region.any():
        raise MeasureError("The dissolve's region holds no pixels.")
    return {
        "region_px": int(region.sum()),
        "doubled_px": int((sa & sb & region).sum()),
        "empty_px": int((~sa & ~sb & region).sum()),
        "b_share": round(float((sb & region).sum() / region.sum()), 4),
    }


# --- Where something draws ---------------------------------------------------------------------------------------


def silhouette(with_, without, threshold=1.0, fill=True):
    """Where something draws: two shots of one held frame, one with it and one without (hidden between the shots).
    The pixels where any channel differs by more than `threshold`, with the holes inside filled (where it happens to
    match what's behind it), so a dark crevice on a hull still counts as the hull. A boolean array."""
    a, b = rgb(with_), rgb(without)
    if a.shape != b.shape:
        raise MeasureError(f"{with_} and {without} differ in size.")
    drawn = np.abs(a - b).max(axis=2) > threshold
    if fill:
        outside, count = components(~drawn)
        if count:
            edge = np.unique(np.concatenate([outside[0], outside[-1], outside[:, 0], outside[:, -1]]))
            drawn |= ~np.isin(outside, edge[edge > 0]) & ~drawn
    return drawn


def save_mask(mask, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.where(mask, 255, 0).astype(np.uint8)).save(path)
    ys, xs = np.nonzero(mask)
    return {"mask": str(path), "pixels": int(mask.sum()),
            "box": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if mask.any() else None}


# --- Black: NaNs and crushed blacks --------------------------------------------------------------------------------


def components(mask):
    """Connected components of a boolean mask, 8-connected: (labels, count), labels 1..count and 0 off the mask.
    Runs along each row are joined with the runs they touch on the row above (union-find), so a large shape costs
    no more than its runs."""
    h, w = mask.shape
    padded = np.zeros((h, w + 2), dtype=np.int8)
    padded[:, 1:-1] = mask
    d = np.diff(padded, axis=1)
    ry, rs = np.nonzero(d == 1)
    _, re_ = np.nonzero(d == -1)
    count = len(ry)
    labels = np.zeros((h, w), dtype=np.int32)
    if count == 0:
        return labels, 0
    parent = list(range(count))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    row_start = np.searchsorted(ry, np.arange(h + 1))
    starts, ends = rs.tolist(), re_.tolist()
    for y in range(1, h):
        a0, a1 = row_start[y - 1], row_start[y]
        b0, b1 = row_start[y], row_start[y + 1]
        i, j = a0, b0
        while i < a1 and j < b1:
            # Touching, diagonals included: [s, e) and [s2, e2) with s <= e2 and s2 <= e.
            if starts[j] <= ends[i] and starts[i] <= ends[j]:
                ri, rj = root(i), root(j)
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)
            if ends[i] < ends[j]:
                i += 1
            else:
                j += 1
    roots = np.array([root(i) for i in range(count)])
    _, comp = np.unique(roots, return_inverse=True)
    comp = comp.astype(np.int32) + 1
    lengths = re_ - rs
    flat = ry * w + rs
    offsets = np.repeat(flat - (np.cumsum(lengths) - lengths), lengths) + np.arange(int(lengths.sum()))
    labels.ravel()[offsets] = np.repeat(comp, lengths)
    return labels, int(comp.max())


def ring_pixels(labels, r):
    """Each shape's ring: the pixels off every shape within r pixels of it (a square), as (shape labels, flat pixel
    indices), each pair once. A pixel between two shapes is in both rings, so a shape's ring never loses its dark
    side to a neighbour."""
    h, w = labels.shape
    off = labels == 0
    owners, pixels = [], []
    index = np.arange(h * w, dtype=np.int64).reshape(h, w)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dy == 0 and dx == 0:
                continue
            ys = slice(max(dy, 0), h + min(dy, 0))
            yd = slice(max(-dy, 0), h + min(-dy, 0))
            xs = slice(max(dx, 0), w + min(dx, 0))
            xd = slice(max(-dx, 0), w + min(-dx, 0))
            # The pixel at (yd, xd) sees the shape at (ys, xs).
            seen = labels[ys, xs]
            hit = (seen > 0) & off[yd, xd]
            owners.append(seen[hit].astype(np.int64))
            pixels.append(index[yd, xd][hit])
    owners = np.concatenate(owners)
    pixels = np.concatenate(pixels)
    pairs = np.unique(owners * (h * w) + pixels)
    return pairs // (h * w), pairs % (h * w)


def shapes(mask, L, ring, lit, limit=20):
    """The connected shapes of a mask, with how lit their surroundings are: the ring `ring` pixels wide round each
    shape, and its share of pixels over `lit`. A shape whose ring is more than half lit is a hole in something lit.
    Returns (every shape's pixels, box, ring mean and lit share, largest first, at most `limit`; the pixels in lit
    holes; the count of lit holes; the count of shapes)."""
    labels, count = components(mask)
    if count == 0:
        return [], 0, 0, 0
    owner, at = ring_pixels(labels, ring)
    values = L.ravel()[at]
    ring_n = np.bincount(owner, minlength=count + 1)
    ring_lit = np.bincount(owner, weights=(values > lit), minlength=count + 1)
    ring_sum = np.bincount(owner, weights=values, minlength=count + 1)
    sizes = np.bincount(labels.ravel(), minlength=count + 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        lit_share = np.where(ring_n > 0, ring_lit / np.maximum(ring_n, 1), 0.0)
        ring_mean = np.where(ring_n > 0, ring_sum / np.maximum(ring_n, 1), 0.0)
    holes = lit_share > 0.5
    holes[0] = False
    ys, xs = np.nonzero(labels)
    lab = labels[ys, xs]
    x0 = np.full(count + 1, 1 << 30)
    y0 = np.full(count + 1, 1 << 30)
    x1 = np.zeros(count + 1, dtype=np.int64)
    y1 = np.zeros(count + 1, dtype=np.int64)
    np.minimum.at(x0, lab, xs)
    np.minimum.at(y0, lab, ys)
    np.maximum.at(x1, lab, xs + 1)
    np.maximum.at(y1, lab, ys + 1)

    def describe(k):
        return {"px": int(sizes[k]), "box": [int(x0[k]), int(y0[k]), int(x1[k]), int(y1[k])],
                "ring_luminance": round(float(ring_mean[k]), 1), "ring_lit_share": round(float(lit_share[k]), 3),
                "lit": bool(holes[k])}

    order = np.argsort(-sizes[1:], kind="stable") + 1
    hole_order = [k for k in order if holes[k]]
    listed = [describe(k) for k in hole_order[:limit]] + [describe(k) for k in order[:limit] if not holes[k]]
    return listed, int(sizes[holes].sum()), len(hole_order), count


def black(path, region=None, ring=1, lit=24.0, floor=0):
    """Pure black from a NaN. A NaN draws black, with no error, and the glow, a blur or temporal anti-aliasing can
    spread it. Shadows go to black too, but fade into their dark surroundings; a NaN's black is a hole cut in
    something lit. So: black_px counts the pixels at or under `floor` in every channel (0: pure black), and
    nan_px the black pixels in shapes more than half of whose surrounding ring (`ring` pixels wide: 1, their
    immediate neighbours, since a shadow fades into black while a NaN cuts a hard edge) is over `lit`
    (24 of 255) in luminance. nan_shapes lists those shapes, largest first."""
    a = rgb(path)
    L = luminance(a)
    is_black = np.all(a <= floor, axis=-1)
    sel = (region or Region()).select(L.shape)
    if sel is not None:
        is_black &= sel
    listed, nan_px, nan_count, count = shapes(is_black, L, ring, lit)
    return {
        "image": str(path),
        "black_px": int(is_black.sum()),
        "black_shapes": count,
        "nan_px": nan_px,
        "nan_shape_count": nan_count,
        "nan_shapes": [{k: v for k, v in s.items() if k != "lit"} for s in listed if s["lit"]],
    }


def crush(path, region=None, floor=0, detail=6.0):
    """Crushed blacks: pixels at the tone mapper's floor (every channel at or under `floor`; 0, pure black, by
    default: ACES and its kin clip linear values under about 0.003 to it) in a region that should hold detail (the
    sky, smoke, a hull's shadowed side). crushed_px and crushed_share count them; near_floor_px those within
    `detail` levels of it (the shadows' last steps, which a darker grade crushes first); darkest the lowest
    luminance in the region; largest_shape the biggest connected patch, with its box."""
    a = rgb(path)
    L = luminance(a)
    sel = (region or Region()).select(L.shape)
    inside = np.ones(L.shape, dtype=bool) if sel is None else sel
    crushed = np.all(a <= floor, axis=-1) & inside
    near = np.all(a <= floor + detail, axis=-1) & inside
    out = {
        "image": str(path),
        "pixels": int(inside.sum()),
        "crushed_px": int(crushed.sum()),
        "crushed_share": round(float(crushed.sum() / inside.sum()), 6),
        "near_floor_px": int(near.sum()),
        "darkest": round(float(L[inside].min()), 2),
        "largest_shape": None,
    }
    if crushed.any():
        listed, _, _, count = shapes(crushed, L, 1, 255.0, limit=1)
        out["crushed_shapes"] = count
        out["largest_shape"] = {"px": listed[0]["px"], "box": listed[0]["box"]}
    return out


def over_frames(fn, paths, **kw):
    """fn over each frame, and the totals: every frame's result, and the sums of their *_px counts."""
    results = [fn(p, **kw) for p in paths]
    totals = {}
    for r in results:
        for k, v in r.items():
            if k.endswith("_px") and isinstance(v, int):
                totals[k] = totals.get(k, 0) + v
    worst = {}
    for k in totals:
        top = max(results, key=lambda r: r.get(k, 0))
        worst[k] = {"value": top[k], "image": top["image"]}
    return {"frames": len(results), "totals": totals, "worst": worst, "each": results}


# --- Frame times -----------------------------------------------------------------------------------------------------


def rank(sorted_values, q):
    """The nearest-rank quantile: the smallest value with at least q of them at or under it."""
    if not sorted_values:
        return 0.0
    return sorted_values[max(math.ceil(q * len(sorted_values)) - 1, 0)]


def spread(values):
    """The median, 99th percentile (nearest rank), worst and mean of a list of times."""
    s = sorted(values)
    return {"p50": round(rank(s, 0.5), 4), "p99": round(rank(s, 0.99), 4), "max": round(s[-1], 4) if s else 0.0,
            "mean": round(sum(s) / len(s), 4) if s else 0.0}


def times(record):
    """A frame-time record's summary: the GPU's and the CPU's render time per frame, and each render pass's GPU time,
    each as spread(). A record is gdh live frames' raw output: {"frames": [{"gpu": ms, "cpu": ms, "passes": {name:
    ms}, "groups": {name: ms}}, ...], "game_frames": n}: the frames measured, and the game frames run. A pass missing from a frame counts as 0 there, so a pass that runs on some
    frames reads low at the median and true at the top."""
    frames = record["frames"] if isinstance(record, dict) else record
    out = {"frames": len(frames)}
    if isinstance(record, dict) and "game_frames" in record:
        out["game_frames"] = record["game_frames"]
    if not frames:
        return out
    out["gpu_ms"] = spread([f["gpu"] for f in frames])
    out["cpu_ms"] = spread([f["cpu"] for f in frames])
    for key in ("passes", "groups"):
        with_key = [f for f in frames if key in f]
        names = sorted({n for f in with_key for n in f[key]})
        if names:
            table = {n: spread([f[key].get(n, 0.0) for f in with_key]) for n in names}
            out[key] = dict(sorted(table.items(), key=lambda kv: -kv[1]["p50"]))
            out[f"{key}_frames"] = len(with_key)
    return out


def load_record(path):
    return json.loads(Path(path).read_text())
