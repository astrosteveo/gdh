"""gdh live shot --annotate: a shot with what's on it named. The game reports the geometry (harness/annotate.gd) in
screenshot pixels; this draws it over a copy of each view saved, mapped into the shot's framing (a crop, a zoom).

Each node that draws gets its box (blue for UI, yellow in the 2D world, orange in 3D) and a number, and a label with the number and the end of its path where one fits
without covering another; the reply lists every number with its node, so a number alone still names it. Collision
shapes are outlined green (an Area's blue, a disabled one grey), navigation polygons filled translucent cyan, and
velocities drawn as magenta arrows to where the body would be in half a second, with the speed.
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

LAYERS = ["names", "collisions", "nav", "velocity"]
# UI (Controls on a CanvasLayer), the 2D world, the 3D world.
KIND_COLORS = {"ui": (90, 170, 255), "2d": (255, 210, 60), "3d": (255, 140, 60)}
SHAPE_COLORS = {"body": (60, 230, 90), "area": (80, 200, 255), "disabled": (150, 150, 150)}
NAV_FILL = (0, 220, 220, 50)
NAV_EDGE = (0, 220, 220, 160)
VELOCITY = (255, 70, 220)
OUTLINE = (0, 0, 0)
LABEL_SIZE = 12
# A label shows the last this many parts of a node's path.
LABEL_PARTS = 2


class AnnotateError(Exception):
    pass


def parse_layers(text):
    """--annotate's value: names (the default), or layers separated by commas, or all."""
    layers = [w.strip() for w in (text or "names").split(",") if w.strip()]
    if "all" in layers:
        return list(LAYERS)
    for layer in layers:
        if layer not in LAYERS:
            raise AnnotateError(f"Unknown layer {layer!r} for --annotate: {', '.join(LAYERS)}, or all.")
    return layers


def parse_filters(texts):
    """--filter values: a node path (only what's under it), group:NAME, class:NAME. Each given must match."""
    out = {}
    for text in texts:
        key, _, value = text.partition(":")
        if key in ("group", "class") and value:
            name = key
        else:
            name, value = "path", text
        if name in out:
            raise AnnotateError(f"--filter takes one {name}, and got {out[name]!r} and {value!r}.")
        out[name] = value
    return out


class Frame:
    """From screenshot pixels to a saved image's: through its crop ([x, y, w, h]) to its size."""

    def __init__(self, image_size, crop=None, size=None):
        self.x, self.y, w, h = crop or [0, 0, *image_size]
        out = size or image_size
        self.sx, self.sy = out[0] / w, out[1] / h

    def point(self, p):
        return ((p[0] - self.x) * self.sx, (p[1] - self.y) * self.sy)

    def box(self, b):
        x0, y0 = self.point(b[:2])
        x1, y1 = self.point((b[0] + b[2], b[1] + b[3]))
        return (x0, y0, x1, y1)


def short(path):
    parts = [p for p in path.split("/") if p]
    return "/".join(parts[-LABEL_PARTS:]) or path


def overlaps(a, b):
    return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])


def place(d, text, box, taken, size, f, avoid=()):
    """Where a label goes: above the box's left corner, below it, inside it, or at its right, the first spot that's
    on the image and clear of every label placed and every rect in `avoid`. Returns its rect, or None."""
    w = d.textlength(text, font=f) + 4
    h = LABEL_SIZE + 4
    x0, y0, x1, y1 = box
    for x, y in ((x0, y0 - h - 1), (x0, y1 + 1), (x0 + 2, y0 + 2), (x1 + 2, y0), (x1 - w, y0 - h - 1), (x1 - w, y1 + 1)):
        spot = (x, y, x + w, y + h)
        if spot[0] < 0 or spot[1] < 0 or spot[2] > size[0] or spot[3] > size[1]:
            continue
        if not any(overlaps(spot, t) for t in taken) and not any(overlaps(spot, a) for a in avoid):
            return spot
    return None


def place_away(d, text, box, taken, size, f, avoid):
    """A spot for a label further from its box, in rings round it, clear of the labels placed and the rects in `avoid`;
    or None. Its caller draws a line from it to the box."""
    w = d.textlength(text, font=f) + 4
    h = LABEL_SIZE + 4
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    for ring in range(1, 7):
        reach_x = (box[2] - box[0]) / 2 + w / 2 + ring * 12
        reach_y = (box[3] - box[1]) / 2 + h / 2 + ring * 10
        for k in range(12):
            a = k * math.tau / 12
            x, y = cx + math.cos(a) * reach_x - w / 2, cy + math.sin(a) * reach_y - h / 2
            spot = (x, y, x + w, y + h)
            if spot[0] < 0 or spot[1] < 0 or spot[2] > size[0] or spot[3] > size[1]:
                continue
            if not any(overlaps(spot, t) for t in taken) and not any(overlaps(spot, r) for r in avoid):
                return spot
    return None


def nearest(box, p):
    """The point of a box nearest p."""
    return (min(max(p[0], box[0]), box[2]), min(max(p[1], box[1]), box[3]))


def draw(image, found, frame, out):
    """Draw the game's annotations (found: annotate.gd's result) over `image` mapped by `frame`, save it at out, and
    return {"labeled", "numbered", "labels"}: how many nodes got a label, how many only their number, and each one's
    {"n", "text", "rect"} in the image's pixels (x0, y0, x1, y1)."""
    with Image.open(image) as img:
        picture = img.convert("RGBA")
    size = picture.size
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for region in found.get("nav", []):
        for polygon in region["polygons"]:
            points = [frame.point(p) for p in polygon]
            if len(points) >= 3:
                od.polygon(points, fill=NAV_FILL, outline=NAV_EDGE)
    picture = Image.alpha_composite(picture, overlay).convert("RGB")
    d = ImageDraw.Draw(picture)
    for shape in found.get("shapes", []):
        color = SHAPE_COLORS.get(shape["kind"], SHAPE_COLORS["body"])
        for line in shape["lines"]:
            points = [frame.point(p) for p in line["points"]]
            if line["closed"] and len(points) > 2:
                points.append(points[0])
            if len(points) > 1:
                d.line(points, fill=color, width=2, joint="curve")
    f = ImageFont.load_default(size=LABEL_SIZE)
    for v in found.get("velocity", []):
        a, b = frame.point(v["from"]), frame.point(v["to"])
        arrow(d, a, b, VELOCITY)
        d.text((b[0] + 4, b[1] - 6), f"{v['speed']:g} {v['unit']}", fill=VELOCITY, font=f, stroke_width=2,
               stroke_fill=OUTLINE)
    nodes = found.get("nodes", [])
    boxes = [frame.box(n["box"]) for n in nodes]
    for n, box in zip(nodes, boxes):
        d.rectangle(box, outline=KIND_COLORS.get(n["kind"], KIND_COLORS["2d"]), width=2)
    # Labels last, over everything; the smallest boxes first, as they have the least room. A label that doesn't fit
    # beside its box is just the number, further out with a line to the box when it must be.
    taken = []
    labels = []
    labeled = numbered = 0
    for i in sorted(range(len(nodes)), key=lambda i: (boxes[i][2] - boxes[i][0]) * (boxes[i][3] - boxes[i][1])):
        n, box = nodes[i], boxes[i]
        color = KIND_COLORS.get(n["kind"], KIND_COLORS["2d"])
        others = [b for j, b in enumerate(boxes) if j != i]
        # The label clear of the other labels and boxes; else the number alone, clear of both, then of the labels;
        # else the number inside the box's corner.
        text = f"{n['n']} {short(n['path'])}"
        spot = place(d, text, box, taken, size, f, others)
        if spot is None:
            text = str(n["n"])
            spot = place(d, text, box, taken, size, f, others)
            if spot is None:
                spot = place_away(d, text, box, taken, size, f, others + [box])
                if spot is None:
                    spot = (box[0] + 2, box[1] + 2, box[0] + 2 + d.textlength(text, font=f) + 4,
                            box[1] + LABEL_SIZE + 6)
                else:
                    middle = ((spot[0] + spot[2]) / 2, (spot[1] + spot[3]) / 2)
                    d.line([nearest(box, middle), nearest(spot, nearest(box, middle))], fill=color, width=1)
            numbered += 1
        else:
            labeled += 1
        taken.append(spot)
        labels.append({"n": n["n"], "text": text, "rect": [round(v, 1) for v in spot]})
        d.text((spot[0] + 2, spot[1] + 1), text, fill=color, font=f, stroke_width=2, stroke_fill=OUTLINE)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    picture.save(out)
    return {"labeled": labeled, "numbered": numbered, "labels": sorted(labels, key=lambda label: label["n"])}


def arrow(d, a, b, color):
    """A line from a to b with a head at b, over a dark outline."""
    angle = math.atan2(b[1] - a[1], b[0] - a[0])
    head = [b, (b[0] - 10 * math.cos(angle - 0.45), b[1] - 10 * math.sin(angle - 0.45)),
            (b[0] - 10 * math.cos(angle + 0.45), b[1] - 10 * math.sin(angle + 0.45))]
    d.line([a, b], fill=OUTLINE, width=5)
    d.polygon(head, fill=color, outline=OUTLINE)
    d.line([a, b], fill=color, width=3)
