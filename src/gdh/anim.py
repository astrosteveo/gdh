"""gdh live anim: an AnimationPlayer's or AnimationTree's state, frame by frame, as numbers and a chart.

For an AnimationPlayer: the animation playing, its position and whether it plays. For an AnimationTree: each state
machine's current state and its position, and every number among its parameters (blend positions and amounts,
time scales, conditions). A step records them with --trace's machinery, and the run is checked for two common bugs:
an animation started again every frame, so it never gets past its first frames, and a state machine that flips
between two states back and forth.
"""
import json
from pathlib import Path

from gdh import chart
from gdh.feel import Driver, FeelError

# Godot's Variant types among a parameter's: the ones charted (bool, int, float), and a state machine's playback.
NUMBERS = (1, 2, 3)
OBJECT = 24
# Godot's PropertyUsageFlags.
STORAGE, EDITOR, READ_ONLY = 2, 4, 1 << 28
# A flip: A, then B, then A again within this many frames of the first change.
FLIP_FRAMES = 6


class AnimError(FeelError):
    pass


def find_animator(game, path):
    """The node to watch, and its kind ("player" or "tree"): the node at path, or the first AnimationTree, else the
    first AnimationPlayer, under it."""
    ref = f"get_node({json.dumps(path)})"
    if not game.eval(f"has_node({json.dumps(path)})"):
        raise AnimError(f"No node at {path}.")
    kind = game.eval(f"{ref}.get_class()")
    for wanted, name in (("AnimationTree", "tree"), ("AnimationPlayer", "player")):
        if game.eval(f"{ref}.is_class('{wanted}')"):
            return ref, name, path
    for wanted, name in (("AnimationTree", "tree"), ("AnimationPlayer", "player")):
        found = game.eval(f"{ref}.find_children('*', '{wanted}', true, false)")
        if found:
            child = found[0]["node"]
            return f"get_node({json.dumps(child)})", name, child
    raise AnimError(f"{path} ({kind}) is no AnimationPlayer or AnimationTree, and has none under it.")


def watched(game, ref, kind):
    """What a run traces: [(name, expression, how)], how "state", "position", "flag" or "number"."""
    if kind == "player":
        return [("animation", f"{ref}.current_animation", "state"),
                ("position", f"{ref}.current_animation_position", "position"),
                ("playing", f"{ref}.is_playing()", "flag")]
    props = game.eval(f"{ref}.get_property_list()") or []
    out = []
    for prop in props:
        name = prop.get("name", "")
        if not name.startswith("parameters/"):
            continue
        if prop.get("type") == OBJECT and name.endswith("/playback"):
            machine = name.removesuffix("playback").removeprefix("parameters/").rstrip("/") or "root"
            out.append((f"{machine} state", f"{ref}.get('{name}').get_current_node()", "state"))
            out.append((f"{machine} position", f"{ref}.get('{name}').get_current_play_position()", "position"))
        elif prop.get("type") in NUMBERS and set_by_the_game(prop.get("usage", 0)):
            out.append((name.removeprefix("parameters/"), f"{ref}.get('{name}')", "number"))
    return out


def set_by_the_game(usage):
    """Whether a parameter is one a game sets (stored, or shown in the editor), not the tree's read-only bookkeeping
    (its nodes' current position, length and delta)."""
    return not usage & READ_ONLY and bool(usage & (STORAGE | EDITOR))


def changes(values, frames):
    """Where a state changed: [(frame, from, to)]."""
    return [(f, a, b) for f, a, b in zip(frames[1:], values, values[1:]) if a != b]


def flips(change_list):
    """A to B and back to A within FLIP_FRAMES frames: [(frame, a, b)]."""
    out = []
    for (f0, a, b), (f1, b2, a2) in zip(change_list, change_list[1:]):
        if b == b2 and a2 == a and f1 - f0 <= FLIP_FRAMES:
            out.append((f0, a, b))
    return out


def restarts(names, positions, playing, frames, ticks):
    """Frames where an animation was back at its start while playing on: the same animation as the frame before, not
    past where it was then, and within a frame and a half of its start. Returns them, and the longest run of them."""
    back = []
    for i in range(1, len(positions)):
        if names[i] and names[i] == names[i - 1] and playing[i] and positions[i] is not None and \
                positions[i - 1] is not None and positions[i] <= positions[i - 1] + 1e-9 and \
                positions[i] <= 1.5 / ticks:
            back.append(frames[i])
    longest = run = 0
    for a, b in zip(back, back[1:]):
        run = run + 1 if b - a == 1 else 0
        longest = max(longest, run)
    return back, longest + 1 if back else 0


def cmd_anim(args):
    from gdh.live import step_input_events
    game = Driver(args)
    ref, kind, path = find_animator(game, args.node)
    items = watched(game, ref, kind)
    if not items:
        raise AnimError(f"{path} has no state or parameters to watch.")
    result = game.step(args.frames, step_input_events(args, args.frames), trace=[e for _, e, _ in items])
    rows = result["trace"]["rows"]
    frames = [row[0] for row in rows]
    columns = {name: [row[i + 1] for row in rows] for i, (name, _, _) in enumerate(items)}
    ticks = game.ticks
    out = {"node": path, "kind": kind, "frames": args.frames, "ticks_per_second": ticks, "warnings": []}
    for name, _, how in items:
        if how == "state":
            found = changes(columns[name], frames)
            out.setdefault("states", {})[name] = {
                "values": sorted({v for v in columns[name] if v}),
                "changes": [{"frame": f, "from": a, "to": b} for f, a, b in found]}
            flipped = flips(found)
            if len(flipped) >= args.flips:
                out["warnings"].append(f"{name} flips back and forth: {len(flipped)} times it went to a state and "
                                       f"back within {FLIP_FRAMES} frames, first at frame {flipped[0][0]} "
                                       f"({flipped[0][1]} to {flipped[0][2]} and back)")
    if kind == "player":
        back, longest = restarts(columns["animation"], columns["position"], columns["playing"], frames, ticks)
        out["restarts"] = back
        if longest >= args.restarts:
            out["warnings"].append(f"{columns['animation'][-1] or 'the animation'} started again {len(back)} times "
                                   f"while playing, {longest} frames in a row at most: something calls play() or "
                                   f"seek() each frame, so it never gets past its start")
    else:
        for name, _, how in items:
            if how != "position":
                continue
            states = columns[name.replace(" position", " state")]
            back, longest = restarts(states, columns[name], [True] * len(states), frames, ticks)
            if longest >= args.restarts:
                machine = name.replace(" position", "")
                out["warnings"].append(f"{machine}'s state {states[frames.index(back[0])]} started again {len(back)} "
                                       f"times, {longest} frames in a row at most: something sends it back to its "
                                       f"start each frame")
    out["values"] = {name: columns[name] for name in columns}
    out["rows_frames"] = frames
    out["chart"] = str(draw(columns, items, frames, Path(args.chart) if args.chart else game.out_dir("anim") / "anim.png",
                            path))
    if args.json:
        print(json.dumps(out, indent=1))
        return 0
    print(f"{kind} {path} over {args.frames} frames:")
    for name, state in out.get("states", {}).items():
        first = columns[name][0]
        moves = state["changes"]
        print(f"  {name}: {first or '(none)'}" + (f", then {len(moves)} change{'s' if len(moves) != 1 else ''}: "
              + ", ".join(f"{c['to']} at {c['frame']}" for c in moves[:8]) + (" ..." if len(moves) > 8 else "")
              if moves else ", throughout"))
    for name, _, how in items:
        if how == "number":
            values = [v for v in columns[name] if isinstance(v, (int, float))]
            if values:
                print(f"  {name}: {values[0]:g} to {values[-1]:g} (from {min(values):g} to {max(values):g})")
    for warning in out["warnings"]:
        print(f"warning: {warning}")
    print(f"chart: {out['chart']}")
    return 0


def draw(columns, items, frames, out, path):
    """The run as a chart: each state as steps (its states numbered in the title), each position and number as a
    line."""
    panels = []
    for name, _, how in items:
        values = columns[name]
        if how == "state":
            names = []
            for v in values:
                if v not in names:
                    names.append(v)
            index = [names.index(v) for v in values]
            key = ", ".join(f"{i} {n or '(none)'}" for i, n in enumerate(names))
            panels.append({"title": f"{name}: {key}", "frames": frames, "lines": [("state", index)], "steps": True})
        elif how in ("position", "number", "flag"):
            series = [None if v is None else float(v) for v in values]
            panels.append({"title": f"{name}{' (s)' if how == 'position' else ''}", "frames": frames,
                           "lines": [(name, series)], "steps": how == "flag"})
    return chart.draw(panels[:chart.MAX_PANELS], out, f"animation of {path}")


def add_live_parsers(commands, command):
    """gdh live anim. `command` is live.py's maker of a subcommand."""
    from gdh.live import add_input_options
    p = command("anim", cmd_anim, "An AnimationPlayer's or AnimationTree's state, animation, position and blend "
                                  "parameters frame by frame, as a chart, with restarts and flip-flops flagged",
                instance="Which game instance: a number (default 0). Every instance steps")
    p.add_argument("frames", type=int, help="How many frames to step")
    p.add_argument("--node", required=True, metavar="PATH",
                   help="An AnimationTree or AnimationPlayer, or a node with one under it (the first tree, else the "
                        "first player)")
    p.add_argument("--restarts", type=int, default=3, metavar="N",
                   help="Flag an animation started again N frames in a row (default 3)")
    p.add_argument("--flips", type=int, default=3, metavar="N",
                   help=f"Flag a state machine that goes to a state and back within {FLIP_FRAMES} frames N times "
                        f"(default 3)")
    p.add_argument("--chart", metavar="FILE.png", help="Where the chart goes (default <session out>/feel/anim/anim.png)")
    add_input_options(p)
