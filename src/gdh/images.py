"""Crops and tiles cut from captured frames."""
from pathlib import Path

from PIL import Image

# Crops are upscaled (nearest neighbor, at most 4x) to about this long side.
CROP_LONG_SIDE = 640


def crop_findings(findings, image_size, images, crops_dir, relative_to=None):
    """Save a zoomed crop for each finding that has a screen rect.

    Rects are in the pixel space of an image_size screenshot. images maps a
    view name to its PNG. Each finding gets a "crop" key with
    the crop's path, relative to relative_to when given.
    """
    crops_dir = Path(crops_dir)
    for i, finding in enumerate(findings, 1):
        rect = finding.get("screen_rect")
        src = images.get(finding.get("view", "normal")) or images.get("normal")
        if not rect or not image_size or not src or not Path(src).exists():
            continue
        img = Image.open(src)
        sx, sy = img.width / image_size[0], img.height / image_size[1]
        x, y, w, h = rect
        pad = max(24, 0.25 * max(w, h))
        box = (max(0, round((x - pad) * sx)), max(0, round((y - pad) * sy)),
               min(img.width, round((x + w + pad) * sx)), min(img.height, round((y + h + pad) * sy)))
        crops_dir.mkdir(parents=True, exist_ok=True)
        path = crops_dir / f"{i:02d}-{finding['probe']}.png"
        zoom(img.crop(box), CROP_LONG_SIDE).save(path)
        finding["crop"] = str(path.relative_to(relative_to) if relative_to else path)


def save_tiles(image_path, crops_dir):
    """Save the image as 2x2 tiles, each shown at 2x. Returns the tile paths."""
    img = Image.open(image_path)
    crops_dir = Path(crops_dir)
    crops_dir.mkdir(parents=True, exist_ok=True)
    half_w, half_h = img.width // 2, img.height // 2
    paths = []
    for row in range(2):
        for col in range(2):
            tile = img.crop((col * half_w, row * half_h, (col + 1) * half_w, (row + 1) * half_h))
            path = crops_dir / f"tile-{row}{col}.png"
            zoom(tile, img.width).save(path)
            paths.append(path)
    return paths


def zoom(img, long_side):
    """Scale so the long side is about long_side. Upscaling uses nearest
    neighbor so it adds no blur of its own."""
    factor = long_side / max(img.size)
    if factor > 1:
        factor = min(factor, 4.0)
        return img.resize((round(img.width * factor), round(img.height * factor)), Image.NEAREST)
    if factor < 1:
        return img.resize((round(img.width * factor), round(img.height * factor)), Image.LANCZOS)
    return img


# A diff's heatmap is shrunk by a whole factor to at most this long side, and its crop's panels zoomed to about this.
HEATMAP_LONG_SIDE = 1280
DIFF_PANEL_LONG_SIDE = 400


def heatmap(diff, base, threshold, boxes, out):
    """Save where two frames differ, over the second dimmed to grey: a pixel that differs by more than the threshold
    on a hot scale (red for the least, through yellow, to white for 255; logarithmic, so a change of a few levels
    shows as plainly as a large one), a smaller difference in dim blue, and each box (x0, y0, x1, y1) outlined in
    cyan. A frame longer than HEATMAP_LONG_SIDE is shrunk by a whole factor, each pixel showing the largest difference
    it covers, so a single changed pixel still shows. diff is each pixel's difference (0-255), base the second frame
    as float RGB. Returns out."""
    import numpy as np
    from PIL import ImageDraw
    f = -(-max(diff.shape) // HEATMAP_LONG_SIDE)
    grey = base[..., 0] * 0.2126 + base[..., 1] * 0.7152 + base[..., 2] * 0.0722
    if f > 1:
        diff, grey = shrink(diff, f, np.max), shrink(grey, f, np.mean)
    img = np.repeat((grey * 0.3)[..., None], 3, axis=2)
    t = 0.25 + 0.75 * np.log1p(diff) / np.log1p(255)
    hot = np.stack([np.clip(3 * t, 0, 1), np.clip(3 * t - 1, 0, 1), np.clip(3 * t - 2, 0, 1)], axis=-1) * 255
    over = diff > threshold
    img[over] = hot[over]
    img[(diff > 0) & ~over] = (30, 70, 170)
    picture = Image.fromarray(img.astype(np.uint8))
    draw = ImageDraw.Draw(picture)
    for x0, y0, x1, y1 in boxes:
        draw.rectangle((x0 // f - 2, y0 // f - 2, -(-x1 // f) + 1, -(-y1 // f) + 1), outline=(0, 220, 255), width=2)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    picture.save(out)
    return out


def shrink(a, f, reduce):
    """A 2D array shrunk by a whole factor f: each value is reduce() over the f x f block it covers (the edges
    repeated to fill the last blocks)."""
    import numpy as np
    h, w = a.shape
    padded = np.pad(a, ((0, -h % f), (0, -w % f)), mode="edge")
    return reduce(padded.reshape(padded.shape[0] // f, f, padded.shape[1] // f, f), axis=(1, 3))


def diff_crop(a, b, box, out, labels=("A", "B")):
    """Save the region round box (x0, y0, x1, y1) in two frames side by side, padded as crop_findings pads and each
    zoomed to about DIFF_PANEL_LONG_SIDE (nearest neighbor, at most 4x), with a third panel: their difference in each
    channel, amplified so the largest in the crop reads 255. a and b are float RGB (0-255). Returns out."""
    import numpy as np
    from PIL import ImageDraw, ImageFont
    h, w = a.shape[:2]
    x0, y0, x1, y1 = box
    pad = max(24, round(0.25 * max(x1 - x0, y1 - y0)))
    window = (slice(max(0, y0 - pad), min(h, y1 + pad)), slice(max(0, x0 - pad), min(w, x1 + pad)))
    ca, cb = a[window], b[window]
    d = np.abs(ca - cb)
    gain = 255 / max(float(d.max()), 1.0)
    panels = [(labels[0], ca), (labels[1], cb), (f"difference x{gain:.3g}", d * gain)]
    panels = [(label, zoom(Image.fromarray(np.clip(p, 0, 255).astype(np.uint8)), DIFF_PANEL_LONG_SIDE))
              for label, p in panels]
    pw, ph = panels[0][1].size
    label_h = 18
    sheet = Image.new("RGB", (3 * pw + 16, ph + label_h + 8), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=13)
    for i, (label, img) in enumerate(panels):
        x = 4 + i * (pw + 4)
        sheet.paste(img, (x, 4 + label_h))
        draw.text((x + 2, 4), label, fill=(230, 230, 230), font=font)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


# A contact sheet: up to SHEET_TILES frames spread evenly over a run, SHEET_TILE_WIDTH px wide each, four across:
# about 1220 px in all, which an image reader takes whole.
SHEET_TILES = 16
SHEET_COLUMNS = 4
SHEET_TILE_WIDTH = 300


def pick_evenly(count, wanted=SHEET_TILES):
    """Indices of `wanted` items spread evenly over `count`, first and last included."""
    if count <= wanted:
        return list(range(count))
    return sorted({round(i * (count - 1) / (wanted - 1)) for i in range(wanted)})


def contact_sheet(tiles, out, columns=SHEET_COLUMNS, width=SHEET_TILE_WIDTH):
    """Save frames side by side, each labeled, as one PNG: an overview of a run, which screen was up when.

    tiles is [(label, image path or PIL image)]. A tile is downscaled, so read it for what's on screen, never for
    pixel detail (thin lines, noise, aliasing), which scaling makes and hides. Returns out."""
    from PIL import ImageDraw, ImageFont
    images = [(label, Image.open(img) if not isinstance(img, Image.Image) else img) for label, img in tiles]
    if not images:
        raise ValueError("A contact sheet needs at least one frame.")
    first = images[0][1]
    height = round(first.height * width / first.width)
    label_h = 18
    rows = -(-len(images) // columns)
    sheet = Image.new("RGB", (columns * width + (columns + 1) * 4, rows * (height + label_h) + (rows + 1) * 4), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=13)
    for i, (label, img) in enumerate(images):
        x = 4 + (i % columns) * (width + 4)
        y = 4 + (i // columns) * (height + label_h + 4)
        sheet.paste(img.convert("RGB").resize((width, height), Image.LANCZOS), (x, y + label_h))
        draw.text((x + 2, y + 2), label, fill=(230, 230, 230), font=font)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out
