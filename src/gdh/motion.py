"""Motion in one image: onion skins and filmstrips of a part of the screen over many frames.

Each takes frames as [(label, PNG path)] in time order and a box (x0, y0, x1, y1 in image pixels, the far edges
excluded) that stays the same for every frame, so motion inside it shows as motion.

An onion skin lays the frames over each other. The background is the median of the frames, each pixel's middle value
over the run, which is the scene behind whatever moved as long as the camera held still. Each frame's own pixels,
where it differs from that background, are drawn over it in turn, the oldest faintest and the newest solid, so one
image shows a whole movement: its path, its spacing (even spacing for constant speed, bunched where it slows), and
how its shape changes. A camera that moves makes the whole background differ, and the result says so.

A filmstrip puts the same box from each frame side by side, labeled, for judging a pose, a flash or a transition
frame by frame.
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# The widest image made: an image reader takes about 1280 px whole.
MAX_WIDTH = 1280
# Without a zoom given, a crop is scaled up (nearest neighbour, whole factors, at most MAX_ZOOM) to about this long side.
TARGET_LONG_SIDE = 640
MAX_ZOOM = 8
# A pixel belongs to a frame's moving part when a channel differs from the background by more than this (of 255).
ONION_THRESHOLD = 12.0
# The faintest ghost's opacity; the newest frame is solid.
FAINTEST = 0.25
# Ghosts tinted by age go from blue (oldest) to orange, this much of the way to the tint.
TINT_OLD, TINT_NEW, TINT_AMOUNT = (40, 110, 255), (255, 140, 30), 0.45
# A frame is labeled at its largest moving blob when that blob holds at least this share of its moving pixels.
MAIN_SHARE = 0.7
# The most cells a filmstrip takes, and the most ghosts an onion skin: more can't be told apart.
MAX_CELLS = 48
MAX_GHOSTS = 24
HEADER = 22
BACKGROUND = (24, 24, 24)
TEXT = (230, 230, 230)


class MotionError(Exception):
    pass


def union_box(boxes, margin, size):
    """The box round boxes given as [x, y, w, h] or points as [x, y] (None skipped), grown by margin on each side and
    clipped to an image of `size` (w, h): (x0, y0, x1, y1) in whole pixels, or None when there are none. A box of
    points alone grows by at least 32 px, as there's nothing to frame."""
    rects = [b if len(b) == 4 else [b[0], b[1], 0, 0] for b in boxes if b]
    if not rects:
        return None
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects)
    y1 = max(r[1] + r[3] for r in rects)
    if all(r[2] == 0 and r[3] == 0 for r in rects):
        margin = max(margin, 32)
    box = (max(0, int(np.floor(x0 - margin))), max(0, int(np.floor(y0 - margin))),
           min(size[0], int(np.ceil(x1 + margin))), min(size[1], int(np.ceil(y1 + margin))))
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def parse_box(text, size):
    """--box X0,Y0,X1,Y1 for an image of `size`, or the whole image when text is None."""
    if text is None:
        return (0, 0, size[0], size[1])
    try:
        box = tuple(int(v) for v in text.split(","))
    except ValueError:
        box = ()
    if len(box) != 4 or box[2] <= box[0] or box[3] <= box[1]:
        raise MotionError(f"--box is X0,Y0,X1,Y1 in image pixels, X1 past X0 and Y1 past Y0, not {text!r}.")
    clipped = (max(0, box[0]), max(0, box[1]), min(size[0], box[2]), min(size[1], box[3]))
    if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
        raise MotionError(f"--box {text} is off the {size[0]}x{size[1]} frames.")
    return clipped


def crops(frames, box):
    """Each frame's box as float32 RGB."""
    out = []
    for _, path in frames:
        with Image.open(path) as img:
            out.append(np.asarray(img.convert("RGB").crop(box), dtype=np.float32))
    return out


def pick_zoom(width, height, zoom=0, fit=MAX_WIDTH):
    """The whole factor to scale a crop up by: `zoom` when given, else about TARGET_LONG_SIDE on the long side; then
    less if the width would pass `fit`. A crop wider than `fit` at 1x is shrunk by the caller."""
    if not zoom:
        zoom = max(1, min(MAX_ZOOM, TARGET_LONG_SIDE // max(width, height)))
    while zoom > 1 and width * zoom > fit:
        zoom -= 1
    return zoom


def scaled(img, zoom, fit=MAX_WIDTH):
    """An image scaled up by a whole factor (nearest neighbour, so no blur of its own), or down to `fit` wide."""
    if zoom > 1:
        img = img.resize((img.width * zoom, img.height * zoom), Image.NEAREST)
    if img.width > fit:
        img = img.resize((fit, max(1, round(img.height * fit / img.width))), Image.LANCZOS)
    return img


def font(size=13):
    return ImageFont.load_default(size=size)


def with_header(img, text):
    """The image under a line of text on a dark strip, at least wide enough for the text."""
    f = font()
    probe = ImageDraw.Draw(img)
    width = min(MAX_WIDTH, max(img.width, int(probe.textlength(text, font=f)) + 12))
    sheet = Image.new("RGB", (width, img.height + HEADER), BACKGROUND)
    sheet.paste(img, (0, HEADER))
    ImageDraw.Draw(sheet).text((6, 4), text, fill=TEXT, font=f)
    return sheet


def dilate(mask):
    """A mask grown by a pixel each way, diagonals included."""
    out = mask.copy()
    out[1:] |= mask[:-1]
    out[:-1] |= mask[1:]
    grown = out.copy()
    grown[:, 1:] |= out[:, :-1]
    grown[:, :-1] |= out[:, 1:]
    return grown


def onion(frames, box, out, zoom=0, tint=False, threshold=ONION_THRESHOLD, title=None):
    """Lay frames ([(label, path)], oldest first) over each other in `box`, the oldest faintest, and save the PNG.
    Returns {"out", "box", "zoom", "frames": [labels], "moving_share": [each frame's share of pixels unlike the
    background], "notes"}."""
    if not frames:
        raise MotionError("An onion skin needs frames.")
    if len(frames) > MAX_GHOSTS:
        raise MotionError(f"{len(frames)} frames is too many to tell apart in one onion skin (at most {MAX_GHOSTS}): "
                          f"take every Kth frame.")
    images = crops(frames, box)
    n = len(images)
    background = np.median(np.stack(images), axis=0) if n >= 3 else images[-1]
    masks = [dilate(np.abs(img - background).max(axis=2) > threshold) for img in images]
    shares = [round(float(m.mean()), 4) for m in masks]
    notes = []
    if n >= 3 and float(np.median(shares)) > 0.5:
        notes.append("Most of the box changes from frame to frame, so the background isn't still (a moving camera, "
                     "or something filling the box): hold the camera still (gdh live camera), or frame less.")
    canvas = background.copy()
    centres = []
    for i, (img, mask) in enumerate(zip(images, masks)):
        t = i / (n - 1) if n > 1 else 1.0
        alpha = FAINTEST + (1 - FAINTEST) * t
        color = img
        if tint and i < n - 1:
            hue = np.array(TINT_OLD, np.float32) * (1 - t) + np.array(TINT_NEW, np.float32) * t
            color = img * (1 - TINT_AMOUNT) + hue * TINT_AMOUNT
        canvas[mask] = canvas[mask] * (1 - alpha) + color[mask] * alpha
        centres.append(main_part(mask))
    w, h = box[2] - box[0], box[3] - box[1]
    zoom = pick_zoom(w, h, zoom)
    picture = scaled(Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8)), zoom)
    label_frames(picture, [label for label, _ in frames], centres, picture.width / w)
    first, last = frames[0][0], frames[-1][0]
    text = title or f"onion skin: {n} frames, {first} to {last}; faintest oldest, solid newest"
    picture = with_header(picture, text)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    picture.save(out)
    return {"out": str(out), "box": list(box), "zoom": zoom, "frames": [label for label, _ in frames],
            "moving_share": shares, "notes": notes}


def main_part(mask):
    """The middle of a frame's moving part: of its largest blob, when that holds most of the frame's moving pixels
    (MAIN_SHARE); else None, as with several things moving there's no one place the label belongs."""
    from gdh.measure import components
    labels, count = components(mask)
    if count == 0:
        return None
    sizes = np.bincount(labels.ravel())[1:]
    biggest = int(np.argmax(sizes)) + 1
    if sizes[biggest - 1] < MAIN_SHARE * sizes.sum():
        return None
    ys, xs = np.nonzero(labels == biggest)
    return float(xs.mean()), float(ys.mean())


def label_frames(picture, labels, centres, scale):
    """Each frame's label at the middle of its moving part, where labels don't land on each other."""
    d = ImageDraw.Draw(picture)
    f = font(11)
    placed = []
    for label, centre in zip(labels, centres):
        if centre is None:
            continue
        text = label.removeprefix("frame ")
        x, y = centre[0] * scale, centre[1] * scale
        w = d.textlength(text, font=f)
        spot = (x - w / 2, y - 6, x + w / 2, y + 7)
        if any(not (spot[2] < p[0] or spot[0] > p[2] or spot[3] < p[1] or spot[1] > p[3]) for p in placed):
            continue
        placed.append(spot)
        d.text((spot[0], spot[1]), text, fill=(255, 255, 255), font=f, stroke_width=2, stroke_fill=(0, 0, 0))


def filmstrip(frames, box, out, zoom=0, title=None):
    """The same box from each frame ([(label, path)]) side by side, each labeled, in rows at most MAX_WIDTH wide, and
    save the PNG. Returns {"out", "box", "zoom", "frames", "columns"}."""
    if not frames:
        raise MotionError("A filmstrip needs frames.")
    if len(frames) > MAX_CELLS:
        raise MotionError(f"{len(frames)} frames is too many for one filmstrip (at most {MAX_CELLS}): take every Kth "
                          f"frame.")
    w, h = box[2] - box[0], box[3] - box[1]
    gap, label_h = 4, 16
    # Big enough to see, and at least 4 across when the frames are many.
    fit = MAX_WIDTH - 2 * gap if len(frames) == 1 else (MAX_WIDTH - gap) // min(len(frames), 4) - gap
    zoom = pick_zoom(w, h, zoom, fit)
    cells = [scaled(Image.fromarray(c.astype(np.uint8)), zoom, fit) for c in crops(frames, box)]
    cw, ch = cells[0].size
    columns = max(1, min(len(cells), (MAX_WIDTH - gap) // (cw + gap)))
    rows = -(-len(cells) // columns)
    sheet = Image.new("RGB", (columns * (cw + gap) + gap, rows * (ch + label_h + gap) + gap), BACKGROUND)
    d = ImageDraw.Draw(sheet)
    f = font(12)
    for i, ((label, _), cell) in enumerate(zip(frames, cells)):
        x = gap + (i % columns) * (cw + gap)
        y = gap + (i // columns) * (ch + label_h + gap)
        d.text((x + 2, y + 1), label, fill=TEXT, font=f)
        sheet.paste(cell, (x, y + label_h))
    text = title or f"filmstrip: {len(frames)} frames, {frames[0][0]} to {frames[-1][0]}, left to right"
    sheet = with_header(sheet, text)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return {"out": str(out), "box": list(box), "zoom": zoom, "frames": [label for label, _ in frames],
            "columns": columns}
