"""Readability and look: gdh live contrast (a node against what's around it) and gdh measure palette (a shot's colors
against a reference image or a mood board).

Contrast is measured on the node's own pixels: the frame is shot twice, as it is and with the node hidden, and the
pixels that differ are the node's. The ring round them, --ring pixels wide, is what the node is seen against. Two
numbers: the luminance contrast ratio between them (WCAG's, from 1 to 21, which is what a player who can't tell the
colors apart goes by: 3 is the least WCAG asks of graphics that must be seen), and the color difference ΔE (CIE76 in
L*a*b*: about 2.3 is just noticeable, over 40 plainly different).

A palette is a shot's brightness, contrast and saturation, its black and white points, and its few dominant colors.
Compared with a reference's, each gets a line saying how the shot differs.
"""
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from gdh.godot import GdhError

MIN_RATIO = 3.0
RING = 12
MASK_THRESHOLD = 3
PALETTE_COLORS = 6
HIST_BINS = 32


class ReadabilityError(GdhError):
    pass


# --- Color ---------------------------------------------------------------------------------------------------------


def linear(rgb):
    """sRGB (0-255) to linear light (0-1)."""
    c = np.asarray(rgb, dtype=np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def relative_luminance(rgb):
    """WCAG's relative luminance (0-1) of sRGB pixels."""
    lin = linear(rgb)
    return lin[..., 0] * 0.2126 + lin[..., 1] * 0.7152 + lin[..., 2] * 0.0722


def contrast_ratio(y1, y2):
    hi, lo = max(y1, y2), min(y1, y2)
    return (hi + 0.05) / (lo + 0.05)


def lab(rgb):
    """sRGB (0-255) to CIE L*a*b* under D65."""
    lin = linear(rgb)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = lin @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def saturation(rgb):
    """HSV saturation (0-1) of sRGB pixels."""
    c = np.asarray(rgb, dtype=np.float64)
    hi, lo = c.max(axis=-1), c.min(axis=-1)
    return np.where(hi > 0, (hi - lo) / np.maximum(hi, 1e-9), 0.0)


# --- gdh live contrast ---------------------------------------------------------------------------------------------


def node_mask(shown, hidden, threshold=MASK_THRESHOLD):
    """The node's pixels: where the frame with it differs from the frame without it."""
    return np.abs(shown.astype(np.int16) - hidden.astype(np.int16)).max(axis=2) > threshold


def grow(mask, r):
    """A mask grown by r pixels (a square neighbourhood)."""
    out = mask.copy()
    for _ in range(r):
        g = out.copy()
        g[1:] |= out[:-1]
        g[:-1] |= out[1:]
        g[:, 1:] |= g[:, :-1].copy()
        g[:, :-1] |= g[:, 1:].copy()
        out = g
    return out


def contrast_of(shown, mask, ring_px=RING):
    """The node's pixels against the ring round them: {"ratio", "delta_e", "node", "ring"}."""
    ring = grow(mask, ring_px) & ~mask
    if not mask.any():
        raise ReadabilityError("The node drew no pixels: hiding it changed nothing on screen.")
    if not ring.any():
        raise ReadabilityError("The node covers the whole screen: there's nothing round it to compare with.")
    node_px, ring_px_values = shown[mask], shown[ring]
    y_node = float(np.median(relative_luminance(node_px)))
    y_ring = float(np.median(relative_luminance(ring_px_values)))
    lab_node = np.median(lab(node_px), axis=0)
    lab_ring = np.median(lab(ring_px_values), axis=0)
    return {"ratio": round(contrast_ratio(y_node, y_ring), 2),
            "delta_e": round(float(np.linalg.norm(lab_node - lab_ring)), 1),
            "node": {"luminance": round(y_node, 4), "lab": [round(float(v), 1) for v in lab_node],
                     "color": [int(v) for v in np.median(node_px, axis=0)], "pixels": int(mask.sum())},
            "ring": {"luminance": round(y_ring, 4), "lab": [round(float(v), 1) for v in lab_ring],
                     "color": [int(v) for v in np.median(ring_px_values, axis=0)], "pixels": int(ring.sum())}}


def contrast_crop(shown, mask, ring_px, out):
    """The node and its ring, outlined (the node white, the ring cyan), cropped and zoomed."""
    from gdh.images import zoom
    ring = grow(mask, ring_px) & ~mask
    ys, xs = np.nonzero(mask | ring)
    pad = 16
    box = (max(0, xs.min() - pad), max(0, ys.min() - pad), min(shown.shape[1], xs.max() + pad + 1),
           min(shown.shape[0], ys.max() + pad + 1))
    img = shown.copy()
    edge = mask & grow(~mask, 1)  # the node's own pixels beside something else
    img[edge] = (255, 255, 255)
    ring_edge = ring & grow(~(mask | ring), 1)
    img[ring_edge] = (0, 220, 255)
    picture = zoom(Image.fromarray(img).crop(box), 480)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    picture.save(out)
    return str(out)


def cmd_contrast(args):
    from gdh.live import LiveError, call, load_session, pick, report
    session = load_session(args.session)
    if len(pick(session, args.instance)) != 1:
        raise LiveError("Give one instance (--instance K).")
    out_dir = Path(args.out) if args.out else Path(session["out"]) / "readability"
    out_dir.mkdir(parents=True, exist_ok=True)

    def send(cmd, request):
        return report(call(session, cmd, request, instance=args.instance), args.json, echo=False)

    results = []
    for node in args.node:
        shown = send("shot", {"views": ["normal"], "label": "contrast", **({"no_ui": True} if args.no_ui else {})})
        try:
            send("set", {"object": f"get_node({json.dumps(node)})", "property": "visible", "value": "false"})
            hidden = send("shot", {"views": ["normal"], "label": "contrast-hidden",
                                   **({"no_ui": True} if args.no_ui else {})})
        finally:
            send("restore", {})
        with Image.open(shown["shots"]["normal"]) as a, Image.open(hidden["shots"]["normal"]) as b:
            pa, pb = np.asarray(a.convert("RGB")), np.asarray(b.convert("RGB"))
        Path(hidden["shots"]["normal"]).unlink(missing_ok=True)
        mask = node_mask(pa, pb)
        try:
            found = contrast_of(pa, mask, args.ring)
        except ReadabilityError as e:
            raise ReadabilityError(f"{node}: {e}") from None
        found["node_path"] = node
        found["crop"] = contrast_crop(pa, mask, args.ring, out_dir / f"{node.replace('/', '_')}-contrast.png")
        shutil.move(shown["shots"]["normal"], out_dir / f"{node.replace('/', '_')}-frame.png")
        found["low"] = found["ratio"] < args.min_ratio
        results.append(found)
    out = {"min_ratio": args.min_ratio, "ring": args.ring, "nodes": results}
    if args.json:
        print(json.dumps(out, indent=1))
    else:
        for r in results:
            verdict = (f"under {args.min_ratio:g}: hard to pick out by brightness alone" if r["low"]
                       else f"at least {args.min_ratio:g}")
            print(f"{r['node_path']}: contrast {r['ratio']:g}:1 with the {args.ring} px round it ({verdict}), color "
                  f"difference {r['delta_e']:g} (node {tuple(r['node']['color'])}, round it {tuple(r['ring']['color'])})"
                  f"  [{r['crop']}]")
    return 1 if args.fail and any(r["low"] for r in results) else 0


# --- gdh measure palette -------------------------------------------------------------------------------------------


def pixels_of(paths):
    """Every pixel of the images, as one (n, 3) array; large images are halved until under 2 million pixels."""
    out = []
    for path in paths:
        with Image.open(path) as img:
            img = img.convert("RGB")
            while img.width * img.height > 2_000_000:
                img = img.resize((img.width // 2, img.height // 2), Image.BOX)
            out.append(np.asarray(img).reshape(-1, 3))
    return np.concatenate(out)


def palette_of(px):
    """A set of pixels' look: brightness (L*), its spread, black and white points, saturation, and dominant colors."""
    L = lab(px)[:, 0]
    sat = saturation(px)
    sample = px[:: max(1, len(px) // 200_000)]
    quantized = Image.fromarray(sample.reshape(1, -1, 3).astype(np.uint8)).quantize(colors=PALETTE_COLORS, method=0)
    counts = sorted(quantized.getcolors(), reverse=True)
    pal = quantized.getpalette()
    colors = [{"rgb": pal[i * 3:i * 3 + 3], "share": round(n / len(sample), 3)} for n, i in counts]
    hist = np.histogram(L, bins=HIST_BINS, range=(0, 100))[0] / len(L)
    return {"lightness": round(float(L.mean()), 1), "contrast": round(float(L.std()), 1),
            "black_point": round(float(np.percentile(L, 2)), 1), "white_point": round(float(np.percentile(L, 98)), 1),
            "saturation": round(float(sat.mean()), 3), "colors": colors, "histogram": [round(float(v), 4) for v in hist]}


def compare_palettes(shot, ref):
    """How the shot's look differs from the reference's, a line each, and the distance between their brightness
    histograms (the earth mover's distance, in L* units)."""
    lines = []

    def say(name, a, b, unit, more, less, close):
        d = a - b
        if abs(d) < close:
            lines.append(f"{name}: {a:g} against {b:g}, about the same")
        else:
            lines.append(f"{name}: {a:g} against {b:g}, {more if d > 0 else less} by {abs(d):g}{unit}")

    say("lightness (mean L*)", shot["lightness"], ref["lightness"], "", "brighter", "darker", 3)
    say("contrast (L* spread)", shot["contrast"], ref["contrast"], "", "more contrast", "flatter", 2)
    say("black point (2% darkest, L*)", shot["black_point"], ref["black_point"], "", "lifted blacks", "deeper blacks", 3)
    say("white point (2% brightest, L*)", shot["white_point"], ref["white_point"], "", "brighter whites",
        "duller whites", 3)
    say("saturation (mean)", shot["saturation"], ref["saturation"], "", "more saturated", "less saturated", 0.03)
    cdf = np.cumsum(shot["histogram"]) - np.cumsum(ref["histogram"])
    emd = float(np.abs(cdf).sum() * (100 / HIST_BINS))
    return lines, round(emd, 2)


def palette_image(shot_path, refs, shot, ref, out):
    """The shot and the reference side by side, each with its dominant colors and its brightness histogram."""
    font = ImageFont.load_default(size=13)
    panel_w = 600
    cells = []
    for title, paths, look in (("shot", [shot_path], shot), ("reference", refs, ref)):
        with Image.open(paths[0]) as img:
            img = img.convert("RGB")
            img = img.resize((panel_w, max(1, round(img.height * panel_w / img.width))), Image.LANCZOS)
        h = img.height + 140
        cell = Image.new("RGB", (panel_w, h), (24, 24, 24))
        cell.paste(img, (0, 0))
        d = ImageDraw.Draw(cell)
        x = 0
        for c in look["colors"]:
            w = max(1, round(c["share"] * panel_w))
            d.rectangle((x, img.height + 4, x + w, img.height + 40), fill=tuple(c["rgb"]))
            x += w
        top = img.height + 50
        peak = max(look["histogram"]) or 1
        bw = panel_w / HIST_BINS
        for i, v in enumerate(look["histogram"]):
            bh = round(70 * v / peak)
            d.rectangle((i * bw, top + 70 - bh, (i + 1) * bw - 1, top + 70), fill=(200, 200, 200))
        d.text((4, top + 74), f"{title}: L* {look['lightness']:g}, spread {look['contrast']:g}, saturation "
                              f"{look['saturation']:g}" + (f" ({len(paths)} images)" if len(paths) > 1 else ""),
               fill=(230, 230, 230), font=font)
        cells.append(cell)
    sheet = Image.new("RGB", (panel_w * 2 + 12, max(c.height for c in cells) + 8), (24, 24, 24))
    for i, c in enumerate(cells):
        sheet.paste(c, (4 + i * (panel_w + 4), 4))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return str(out)


def cmd_palette(args):
    for p in [args.shot, *args.reference]:
        if not Path(p).is_file():
            raise ReadabilityError(f"No such file: {p}")
    shot = palette_of(pixels_of([args.shot]))
    ref = palette_of(pixels_of(args.reference))
    lines, emd = compare_palettes(shot, ref)
    out = {"shot": shot, "reference": ref, "differences": lines, "histogram_distance": emd}
    if args.out:
        out["image"] = palette_image(args.shot, args.reference, shot, ref, args.out)
    if args.json:
        print(json.dumps(out, indent=1))
        return 0
    print(f"{args.shot} against {', '.join(args.reference)}:")
    for line in lines:
        print(f"  {line}")
    print(f"  brightness histograms {emd:g} L* apart (0: the same; under 3 hard to tell)")
    print("  dominant colors, shot: " + ", ".join(f"#{''.join(f'{v:02x}' for v in c['rgb'])} {c['share']:.0%}"
                                                    for c in shot["colors"]))
    print("  dominant colors, reference: " + ", ".join(f"#{''.join(f'{v:02x}' for v in c['rgb'])} {c['share']:.0%}"
                                                        for c in ref["colors"]))
    if out.get("image"):
        print(f"image: {out['image']}")
    return 0


# --- Parsers ---------------------------------------------------------------------------------------------------------


def add_live_parsers(commands, command):
    """gdh live contrast. `command` is live.py's maker of a subcommand."""
    p = command("contrast", cmd_contrast, "How well nodes stand out: each one's pixels against the ring round them, as "
                                          "a luminance contrast ratio and a color difference",
                instance="Which game instance: a number (default 0)")
    p.add_argument("--node", action="append", required=True, metavar="PATH", help="A node to measure; repeatable")
    p.add_argument("--ring", type=int, default=RING, metavar="PX",
                   help=f"How wide the ring round the node is (default {RING})")
    p.add_argument("--min-ratio", type=float, default=MIN_RATIO, metavar="R",
                   help=f"Flag a contrast ratio under R:1 (default {MIN_RATIO:g}, WCAG's least for graphics)")
    p.add_argument("--no-ui", action="store_true", help="Leave the UI out of the frames")
    p.add_argument("--out", metavar="DIR", help="Where the frames and crops go (default <session out>/readability)")
    p.add_argument("--fail", action="store_true", help="Exit 1 when a node is under --min-ratio")


def add_measure_parsers(kinds):
    """gdh measure palette. `kinds` is gdh measure's subparsers."""
    k = kinds.add_parser("palette", help="A shot's brightness, contrast, black and white points, saturation and "
                                         "dominant colors against a reference image or a mood board")
    k.add_argument("shot", help="The shot (a PNG or JPEG)")
    k.add_argument("--reference", action="append", required=True, metavar="IMAGE",
                   help="The look to match; repeat for a mood board, whose images are taken together")
    k.add_argument("--out", metavar="PNG", help="Also save the two side by side, with their colors and histograms")
    k.add_argument("--json", action="store_true", help="Print the whole result")
    k.set_defaults(func=cmd_palette)
