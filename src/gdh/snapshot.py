"""gdh live snapshot: the UI on screen as a text outline, and comparing it with a baseline.

The game gives the nodes (harness/screen.gd, Screen.snapshot): what shows something to read or use (a text, a button,
a field, a slider, tabs, a list) and the named nodes that group them. Here they become lines, one a node, indented
under its group, a node whose name Godot made (@Button@6, which changes from run to run) by its class alone:

  - UI (CanvasLayer)
    - GoButton (Button) "Go" [focused]
    - Volume (HSlider) 80/100
    - Name (LineEdit) placeholder "Your name"
    - (AcceptDialog) "Alert!"
      - (Button) "OK" [focused]

A baseline is such a text kept in a file. Compared with it, a snapshot passes when the lines are the same, and
otherwise gives a unified diff. Boxes (`@x,y wxh`) are left out unless asked for, so a check doesn't fail on a layout
that moved a pixel; with them, they're rounded to --grid pixels.
"""
import difflib
import json


def lines(nodes, boxes=False, grid=1, depth=0):
    """The outline's lines for nodes from the game."""
    out = []
    for node in nodes:
        # A name Godot made (@Button@6) changes from run to run: the class alone stands for it.
        name = "" if node["name"].startswith("@") else f"{node['name']} "
        parts = [f"{'  ' * depth}- {name}({node['class']})"]
        if "text" in node:
            parts.append(json.dumps(node["text"], ensure_ascii=False))
        if "placeholder" in node:
            parts.append(f"placeholder {json.dumps(node['placeholder'], ensure_ascii=False)}")
        if node.get("states"):
            parts.append(f"[{', '.join(node['states'])}]")
        if "value" in node:
            parts.append(node["value"])
        if boxes and node.get("box"):
            box = [round(v / grid) * grid for v in node["box"]]
            parts.append(f"@{box[0]},{box[1]}" + (f" {box[2]}x{box[3]}" if len(box) == 4 else ""))
        out.append(" ".join(parts))
        out += lines(node.get("children", []), boxes, grid, depth + 1)
    return out


def text(nodes, boxes=False, grid=1):
    return "".join(line + "\n" for line in lines(nodes, boxes, grid))


def diff(baseline, now, baseline_name="baseline", now_name="now"):
    """The unified diff from a baseline's text to a snapshot's, or "" when they're the same."""
    return "".join(difflib.unified_diff(baseline.splitlines(True), now.splitlines(True), baseline_name, now_name))
