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
