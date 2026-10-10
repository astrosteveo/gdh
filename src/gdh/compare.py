"""Options side by side, for decisions of taste: gdh live sweep (T16), gdh capture --matrix (T17) and gdh review
(T18).

A sweep sets a property of the running game to each of a few values in turn and saves the same frame with each, into
one labeled grid: a row of cells for one property, rows by columns for two. Every value is put back afterwards.
"""
import json
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from gdh.godot import GdhError

MAX_WIDTH = 1280
GAP = 4
LABEL = 18
HEADER = 22
BACKGROUND = (24, 24, 24)
TEXT = (230, 230, 230)
MUTED = (160, 160, 160)


class CompareError(GdhError):
    pass


# --- Grids -------------------------------------------------------------------------------------------------------------


def grid(cells, out, columns, title=None, row_labels=None, column_labels=None, width=MAX_WIDTH):
    """Cells ([(label, image path or PIL image)], row by row) in a grid at most `width` wide, each fitted inside one
    cell's size (the first's aspect) at its own aspect, each labeled; with row and column labels, those go down the left and along the
    top instead. Returns {"out", "cell_size", "columns", "rows"}."""
    images = [(label, img if isinstance(img, Image.Image) else Image.open(img)) for label, img in cells]
    if not images:
        raise CompareError("A grid needs cells.")
    font = ImageFont.load_default(size=13)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    left = int(max((probe.textlength(t, font=font) for t in row_labels), default=0)) + 10 if row_labels else 0
    top = LABEL if column_labels else 0
    rows = -(-len(images) // columns)
    cell_w = (width - left - GAP * (columns + 1)) // columns
    first = images[0][1]
    cell_w = min(cell_w, first.width * 2)  # scaled up at most twice
    cell_h = max(1, round(first.height * cell_w / first.width))
    own_label = 0 if row_labels and column_labels else LABEL
    sheet_w = left + GAP + columns * (cell_w + GAP)
    sheet_h = (HEADER if title else 0) + top + GAP + rows * (cell_h + own_label + GAP)
    sheet = Image.new("RGB", (sheet_w, sheet_h), BACKGROUND)
    d = ImageDraw.Draw(sheet)
    y0 = HEADER if title else 0
    if title:
        d.text((6, 4), title, fill=TEXT, font=font)
    for c, text in enumerate(column_labels or []):
        d.text((left + GAP + c * (cell_w + GAP) + 2, y0 + 2), text, fill=TEXT, font=font)
    for i, (label, img) in enumerate(images):
        r, c = divmod(i, columns)
        x = left + GAP + c * (cell_w + GAP)
        y = y0 + top + GAP + r * (cell_h + own_label + GAP)
        if own_label:
            d.text((x + 2, y + 2), label, fill=TEXT, font=font)
        # Fitted inside the cell at its own aspect, centred: cells of other shapes aren't stretched.
        k = min(cell_w / img.width, cell_h / img.height)
        size = (max(1, round(img.width * k)), max(1, round(img.height * k)))
        resized = img.convert("RGB").resize(size, Image.LANCZOS if k < 1 else Image.NEAREST)
        sheet.paste(resized, (x + (cell_w - size[0]) // 2, y + own_label + (cell_h - size[1]) // 2))
        if row_labels and c == 0:
            d.text((6, y + own_label + cell_h // 2 - 7), row_labels[r], fill=TEXT, font=font)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return {"out": str(out), "cell_size": [cell_w, cell_h], "columns": columns, "rows": rows}


# --- gdh live sweep ------------------------------------------------------------------------------------------------


def split_values(text):
    """Comma-separated values, the commas inside brackets or quotes not splitting: "1, Color(1, 0, 0), 'a,b'"."""
    out, depth, quote, current = [], 0, None, ""
    for ch in text:
        if quote:
            current += ch
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(current.strip())
            current = ""
            continue
        current += ch
    if current.strip():
        out.append(current.strip())
    return out


def parse_sweep(text):
    """TARGET=V1,V2,... as {"object", "property", "name", "values"}. TARGET is OBJECT.PROPERTY (OBJECT an expression,
    as eval takes; a bare PROPERTY is the current scene's), and PROPERTY may go on with ":" into a part of it
    (position:x). Each value is an expression."""
    target, sep, values = text.partition("=")
    target = target.strip()
    if not sep or not target or not values.strip():
        raise CompareError(f"A sweep is TARGET=VALUE,VALUE..., such as \"get_node('Sun').light_energy=0.5,1,2\"; not "
                           f"{text!r}.")
    depth, cut = 0, -1
    for i, ch in enumerate(target):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "." and depth == 0:
            cut = i
    obj, prop = (target[:cut], target[cut + 1:]) if cut >= 0 else ("", target)
    parsed = split_values(values)
    if len(parsed) < 2:
        raise CompareError(f"A sweep needs 2 values or more: {text!r}.")
    return {"object": obj, "property": prop, "name": prop.split(":")[-1], "values": parsed}


def cmd_sweep(args):
    from gdh.live import LiveError, call, load_session, pick, report, step_input_events
    sweeps = [parse_sweep(t) for t in args.sweeps]
    if len(sweeps) > 2:
        raise CompareError("Sweep one property, or two for rows and columns.")
    session = load_session(args.session)
    if len(pick(session, args.instance)) != 1:
        raise LiveError("Give one instance (--instance K): the grid is one game's frames.")
    if args.record is None and (args.reset or args.as_ != "sheet"):
        raise CompareError("--reset and --as go with --record.")
    if args.as_ == "onion" and not args.node:
        raise CompareError("--as onion needs --node: the node whose movement it lays over itself.")
    rows = sweeps[0]["values"]
    cols = sweeps[1]["values"] if len(sweeps) == 2 else [None]
    out = Path(args.out) if args.out else Path(session["out"]) / "sweep" / "sweep.png"
    cells_dir = out.with_name(out.stem + "-cells")
    if cells_dir.exists():
        shutil.rmtree(cells_dir)
    cells_dir.mkdir(parents=True)
    framing = {}
    if args.crop:
        from gdh.live import numbers
        framing = {"crop": numbers(args.crop, 4, "--crop")}
    if args.no_ui:
        framing["no_ui"] = True

    def send(cmd, request):
        return report(call(session, cmd, request, instance=args.instance), args.json, echo=False)

    cells, made, boxes = [], [], []
    try:
        for r, row_value in enumerate(rows):
            for c, col_value in enumerate(cols):
                if args.record is not None and args.reset:
                    send("eval", {"expr": args.reset})
                set_to = [(sweeps[0], row_value)] + ([(sweeps[1], col_value)] if col_value is not None else [])
                for sweep, value in set_to:
                    send("set", {"object": sweep["object"], "property": sweep["property"], "value": value})
                label = ", ".join(f"{s['name']}={v}" for s, v in set_to)
                path = cells_dir / f"{r * len(cols) + c + 1:02d}.png"
                if args.record is None:
                    shot = send("shot", {"views": [args.view], "label": "sweep", **framing})
                    shutil.move(shot["shots"][args.view], path)
                    if args.node:  # where the node is with these values: the frame covers it in every cell
                        where = send("shot", {"views": ["normal"], "label": "sweep-box", "node": args.node,
                                              "margin": args.margin, "max_width": 1})
                        boxes.append(where["crop"])
                        Path(where["shots"]["normal"]).unlink(missing_ok=True)
                else:
                    record_cell(send, args, step_input_events, path)
                cells.append((label, path))
                made.append({"label": label, "values": [v for _, v in set_to], "image": str(path)})
    finally:
        restored = send("restore", {})
    if boxes:
        x0 = min(b[0] for b in boxes)
        y0 = min(b[1] for b in boxes)
        x1 = max(b[0] + b[2] for b in boxes)
        y1 = max(b[1] + b[3] for b in boxes)
        for _, path in cells:
            with Image.open(path) as img:
                img.crop((x0, y0, x1, y1)).save(path)
    columns = len(cols) if len(sweeps) == 2 else min(len(rows), args.columns)
    title = " x ".join(f"{s['object'] + '.' if s['object'] else ''}{s['property']}" for s in sweeps)
    if len(sweeps) == 2:
        info = grid(cells, out, columns, title, [f"{sweeps[0]['name']}={v}" for v in rows],
                    [f"{sweeps[1]['name']}={v}" for v in cols])
    else:
        info = grid(cells, out, columns, title)
    result = {**info, "cells": made, "restored": restored["restored"]}
    if args.json:
        print(json.dumps(result, indent=1))
        return 0
    print(f"sweep: {out} ({len(made)} cells, {info['rows']} by {info['columns']}, each {info['cell_size'][0]}x"
          f"{info['cell_size'][1]})")
    for cell in made:
        print(f"  {cell['label']}: {cell['image']}")
    print(f"  put back: {restored['restored']} propert{'y' if restored['restored'] == 1 else 'ies'} as they were")
    if args.record is not None and not args.reset:
        sys.stdout.flush()
        print("note: each cell's recording starts where the one before ended: give --reset EXPR to start each from "
              "the same state", file=sys.stderr)
    return 0


def record_cell(send, args, step_input_events, path):
    """One cell of a sweep with --record: step the frames, saving every Kth, and make a contact sheet of them or an
    onion skin of --node."""
    from gdh import motion
    from gdh.images import contact_sheet, pick_evenly
    frames = args.record
    request = {"frames": frames, "events": step_input_events(args, frames), "shot_every": args.every}
    if args.node:
        request["track"] = [args.node]
    result = send("step", request)
    shots = result.get("shots", [])
    if not shots:
        raise CompareError("The recording saved no frames: --every is more than --record.")
    numbered = list(zip(result["shot_frames"], shots))
    try:
        if args.as_ == "onion":
            boxes = dict((f, b) for f, b in result["track"][args.node]["rows"])
            with Image.open(shots[0]) as img:
                size = img.size
            box = motion.union_box([boxes.get(f) for f, _ in numbered], args.margin, size)
            if box is None:
                raise CompareError(f"{args.node} didn't show on screen.")
            chosen = [numbered[i] for i in pick_evenly(len(numbered), motion.MAX_GHOSTS)]
            motion.onion([(f"frame {f}", p) for f, p in chosen], box, path)
        else:
            contact_sheet([(f"frame {numbered[i][0]}", numbered[i][1]) for i in pick_evenly(len(numbered), 9)], path,
                          columns=3, width=400)
    finally:
        for p in shots:
            Path(p).unlink(missing_ok=True)


def add_live_parsers(commands, command):
    """gdh live sweep. `command` is live.py's maker of a subcommand."""
    from gdh.live import add_input_options
    p = command("sweep", cmd_sweep, "Set a property to each of a few values and save the same frame with each, in one "
                                    "labeled grid; two sweeps make rows and columns. Every value is put back after",
                instance="Which game instance: a number (default 0)")
    p.add_argument("sweeps", nargs="+", metavar="TARGET=V1,V2,...",
                   help="OBJECT.PROPERTY=VALUE,VALUE...: OBJECT an expression as eval takes (a bare PROPERTY is the "
                        "scene's), PROPERTY:part for a part of it (position:x), each VALUE an expression. One or two")
    p.add_argument("--out", metavar="FILE.png", help="Where the grid goes (default <session out>/sweep/sweep.png); "
                                                     "each cell's full image goes beside it in FILE-cells/")
    p.add_argument("--view", default="normal", help="The view each cell shows (default normal)")
    p.add_argument("--node", metavar="PATH",
                   help="Frame the cells on this node: the box round it with every value (with --margin), the same "
                        "for every cell")
    p.add_argument("--margin", type=float, default=24, metavar="PX", help="With --node: grow its box (default 24)")
    p.add_argument("--crop", metavar="X,Y,W,H", help="Frame each cell on this part of the screen")
    p.add_argument("--no-ui", action="store_true", help="Leave the UI out of each cell")
    p.add_argument("--columns", type=int, default=4, metavar="N", help="Cells across for one sweep (default 4)")
    p.add_argument("--record", type=int, metavar="N",
                   help="For a setting that shows in motion: step N frames for each cell, with the input options, "
                        "and show them as a contact sheet (or --as onion)")
    p.add_argument("--every", type=int, default=1, metavar="K", help="With --record: save every Kth frame (default 1)")
    p.add_argument("--as", dest="as_", choices=["sheet", "onion"], default="sheet",
                   help="With --record: a contact sheet of 9 frames (default), or an onion skin of --node")
    p.add_argument("--reset", metavar="EXPR",
                   help="With --record: an expression run before each cell, to start each from the same state")
    add_input_options(p)
