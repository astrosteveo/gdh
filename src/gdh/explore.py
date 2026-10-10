"""gdh live explore: a bot that plays for thousands of frames and reports where the game goes wrong.

It plays in chunks of --chunk frames. Each chunk holds a direction, or none, and may tap the jump: chosen at random
from --seed (random mode), or toward --goal, jumping when it stops getting closer and trying something else when
that doesn't help (goal mode). After every chunk it looks at where the node went:

  stuck        holding a direction for --stuck frames, and still within --still of where it was (jumping in place
               counts): a wall it can't get past, a ledge it can't climb, a corner it catches on. In goal mode: no
               closer to the goal for --stuck frames, whatever it tried, reported where it got closest.
  soft lock    within --still of where it was for --softlock frames, though it tried at least two directions: input
               that stopped working.
  falling      going down without stopping for --fall frames: a hole with no bottom, a missing floor. It's reported
               where the fall began, and the drop itself is left off the heat map.
  out of bounds  outside --bounds.

Each problem is saved as a scenario (the session's input log up to that frame, and a check of where the node is), so
`gdh scenario run` takes the game back to it. After a fall or going out of bounds, and after a soft lock with
--reset, the node goes back to its start (or --reset runs) and the run goes on. The run ends with a heat map of where
the node went, the problems marked, and the game's own events (GdhEvents) counted. The same problem in the same place
(within 64 px, or 2 m in 3D) is counted, not saved again.
"""
import json
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from gdh.feel import Driver, FeelError

DIRECTIONS = ("left", "right", "up", "down")


class ExploreError(FeelError):
    pass


class Bot:
    def __init__(self, args, three_d):
        self.args = args
        self.rng = random.Random(args.seed)
        self.three_d = three_d
        moves = {"left": args.left, "right": args.right, "up": args.up, "down": args.down}
        self.moves = {k: v for k, v in moves.items() if v}
        self.last_choice = None

    def choose(self, position, progress):
        """The next chunk's direction (or None) and whether to tap the jump."""
        if self.args.mode == "random":
            direction = self.rng.choice([*self.moves, None, None]) if self.rng.random() < 0.85 else None
            return direction, bool(self.args.jump) and self.rng.random() < self.args.jump_chance
        goal = self.args.goal
        dx = goal[0] - position[0]
        dy = (goal[-1] - position[-1]) if self.three_d else (goal[1] - position[1])
        options = []
        if abs(dx) > self.args.near:
            options.append("right" if dx > 0 else "left")
        if (self.three_d or "up" in self.moves) and abs(dy) > self.args.near:
            # 3D: up is -z (forward); 2D with an up input: up is -y.
            options.append("down" if dy > 0 else "up")
        options = [o for o in options if o in self.moves]
        if not options:
            return None, False
        direction = options[0] if abs(dx) >= abs(dy) or len(options) == 1 else options[1]
        if progress is not None and progress < self.args.still:
            # No closer last chunk: jump, and now and then try another way.
            if self.rng.random() < 0.3:
                direction = self.rng.choice(list(self.moves))
            return direction, bool(self.args.jump)
        return direction, False


def ground_distance(a, b, three_d):
    return math.hypot(b[0] - a[0], b[2] - a[2]) if three_d else math.hypot(b[0] - a[0], b[1] - a[1])


def going_down(a, b, three_d):
    return (b[1] < a[1]) if three_d else (b[1] > a[1])


def save_problem(game, args, kind, index, ref, position, out_dir):
    """The session's input log so far as a scenario, with a check of where the node is."""
    from gdh.scenario import recorded_steps, start_options
    path = out_dir / f"{index:02d}-{kind.replace(' ', '-')}.scenario.json"
    start, _ = start_options(args.session, out_dir)
    within = 1.0 if len(position) == 2 else 0.05
    doc = {"start": start, "steps": recorded_steps(args.session),
           "expect": [{"expect": f"{ref}.global_position", "approx": position, "within": within}]}
    path.write_text(json.dumps(doc, indent=2) + "\n")
    return str(path)


def cmd_explore(args):
    from gdh.live import input_event
    game = Driver(args)
    ref = f"get_node({json.dumps(args.node)})"
    if not game.eval(f"has_node({json.dumps(args.node)})"):
        raise ExploreError(f"No node at {args.node}.")
    start = game.eval(f"{ref}.global_position")
    if not isinstance(start, list):
        raise ExploreError(f"{args.node} has no global_position: explore follows a Node2D or Node3D.")
    three_d = len(start) == 3
    args.mode = args.mode or ("goal" if args.goal else "random")
    if args.goal is not None:
        args.goal = [float(v) for v in args.goal.split(",")]
        if len(args.goal) != (3 if three_d else 2):
            raise ExploreError(f"--goal takes {'X,Y,Z' if three_d else 'X,Y'} for {args.node}.")
    elif args.mode == "goal":
        raise ExploreError("--mode goal needs --goal.")
    bounds = [float(v) for v in args.bounds.split(",")] if args.bounds else None
    if args.still is None:
        args.still = 0.25 if three_d else 8.0
    has_velocity = bool(game.eval(f"'velocity' in {ref}"))
    out_dir = Path(args.out) if args.out else game.out_dir("explore")
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.scenario.json"):
        old.unlink()
    bot = Bot(args, three_d)

    def reset():
        if args.reset:
            game.eval(args.reset)
        else:
            vector = f"Vector{'3' if three_d else '2'}({', '.join(map(str, start))})"
            sets = [f"{ref}.set_global_position({vector})"]
            if has_velocity:
                sets.append(f"{ref}.set_velocity(Vector{'3' if three_d else '2'}({', '.join(['0'] * len(start))}))")
            game.eval(f"[{', '.join(sets)}]")

    if args.reset:  # a known state to start from, as after every problem
        reset()
        game.step(2)
        start = game.eval(f"{ref}.global_position")
    began = None  # the game frame the run began at: its events are the ones after it
    trail = [start]
    problems = []
    seen = {}  # (kind, cell) -> the problem found there first
    cell = 2.0 if three_d else 64.0
    anchor = start       # where it was when it last got somewhere: --still from here is getting nowhere
    best = None          # goal mode: the closest it got to the goal, where, and the frames since it got closer
    held_still = 0       # frames holding a direction since, without getting anywhere
    still_since = None   # frames since anything moved it, and the directions tried meanwhile
    tried = set()
    falling = 0
    fall_start = None
    heat = []  # where it was, less its falls
    progress = None
    frames_run = 0
    stopped = None
    position = start
    while frames_run < args.frames:
        direction, jump = bot.choose(position, progress)
        n = min(args.chunk, args.frames - frames_run)
        events_in = []
        if direction:
            token = bot.moves[direction]
            events_in += [input_event(token, True, 0), input_event(token, False, n)]
        if jump:
            at = bot.rng.randrange(0, max(1, n - 1))
            events_in += [input_event(args.jump, True, at), input_event(args.jump, False, at + 1)]
        events_in.sort(key=lambda e: e["at"])
        before = position
        result = game.step(n, events_in, trace=[f"{ref}.global_position"])
        if began is None:
            began = result["status"]["frame"] - n
        frames_run += n
        rows = [row[1] for row in result["trace"]["rows"] if row[1] is not None]
        trail += rows
        position = rows[-1] if rows else position
        if args.goal is not None:
            progress = ground_distance(before, args.goal, three_d) - ground_distance(position, args.goal, three_d)
        frame = result["status"]["frame"]
        found = None
        # Falling: every frame of the chunk lower than the one before.
        down = all(going_down(a, b, three_d) for a, b in zip([before] + rows, rows)) and rows
        if down and not falling:
            fall_start = before
        falling = falling + n if down else 0
        if not falling or falling <= args.chunk * 2:  # a fall's first frames stay on the map, its long drop doesn't
            heat += rows
        if falling >= args.fall:
            found = "falling"
        elif bounds and not (bounds[0] <= position[0] <= bounds[2] and
                             bounds[1] <= (position[2] if three_d else position[1]) <= bounds[3]):
            found = "out of bounds"
        # Stuck: a direction held, and still within --still of where it was (jumping in place counts).
        if ground_distance(anchor, position, three_d) >= args.still or down:
            anchor, held_still, still_since, tried = position, 0, None, set()
        else:
            if direction:
                held_still += n
                tried.add(direction)
            still_since = (still_since or 0) + n
        if not found and still_since and still_since >= args.softlock and len(tried) >= 2:
            found = "soft lock"
        elif not found and args.goal is None and held_still >= args.stuck:
            found = "stuck"
        if args.goal is not None and not found:
            # Goal mode: stuck when it hasn't got closer to the goal for --stuck frames, whatever it tried.
            gap = ground_distance(position, args.goal, three_d)
            if best is None or gap < best[0] - args.still:
                best = [gap, position, 0]
            elif not down:
                best[2] += n
                if best[2] >= args.stuck and gap > args.near:
                    found = "stuck"
        if found:
            where = fall_start if found == "falling" and fall_start else position  # a fall: where it began
            if found == "stuck" and best is not None:
                where = best[1]  # goal mode: the closest it got
            key = (found, round(where[0] / cell), round((where[2] if three_d else where[1]) / cell))
            if key in seen:  # the same problem in the same place again: counted, not saved again
                seen[key]["times"] += 1
            else:
                problem = {"kind": found, "frame": frame, "at": [round(v, 3) for v in where],
                           "holding": bot.moves.get(direction) if direction else None, "times": 1}
                problem["scenario"] = save_problem(game, args, found, len(problems) + 1, ref, position, out_dir)
                problems.append(problem)
                seen[key] = problem
            held_still, falling, best = 0, 0, None
            if found != "stuck":  # stuck keeps counting toward a soft lock: nothing else moving it either
                still_since, tried = None, set()
                anchor = position
            if found == "soft lock" and not args.reset:
                stopped = (f"soft-locked at frame {frame}: nothing it pressed moved it, and without --reset it can't "
                           f"get out")
                break
            if found in ("falling", "out of bounds", "soft lock") or (found == "stuck" and args.reset_when_stuck):
                reset()
                position = anchor = game.eval(f"{ref}.global_position")
                trail.append(position)
                heat.append(position)
            if len(problems) >= args.max_problems:
                stopped = f"stopped at {args.max_problems} different problems (--max-problems)"
                break
    from gdh.events import counts
    from gdh.live import call, report
    got = report(call(game.session, "events", {}, instance=args.instance), args.json, echo=False)
    if isinstance(got, list):
        got = got[game.index]
    game_events = counts([e for e in got["events"] if began is not None and e["frame"] > began])
    image = heat_map([start] + heat, problems, start, three_d, out_dir / "heat.png", args.goal)
    out = {"node": args.node, "mode": args.mode, "seed": args.seed, "frames": frames_run, "problems": problems,
           "events": game_events, "heat_map": image, "reached": extent([start] + heat, three_d), "stopped": stopped}
    if args.json:
        print(json.dumps(out, indent=1))
        return 1 if problems and args.fail else 0
    print(f"explore of {args.node}, {args.mode} (seed {args.seed}), {frames_run} frames: {len(problems)} problem"
          f"{'s' if len(problems) != 1 else ''}")
    for p in problems:
        holding = f" holding {p['holding']}" if p["holding"] else ""
        times = f" ({p['times']} times there)" if p["times"] > 1 else ""
        print(f"  {p['kind']} at frame {p['frame']}, at {','.join(f'{v:g}' for v in p['at'])}{holding}{times}: "
              f"{p['scenario']}")
    if game_events:
        print("  the game's events: " + ", ".join(f"{k} {v}" for k, v in game_events.items()))
    r = out["reached"]
    print(f"  reached x {r['x'][0]:g} to {r['x'][1]:g}, {'z' if three_d else 'y'} {r['y'][0]:g} to {r['y'][1]:g}")
    if stopped:
        print(f"  {stopped}")
    print(f"heat map: {image}")
    return 1 if problems and args.fail else 0


def extent(trail, three_d):
    xs = [p[0] for p in trail]
    ys = [p[2] if three_d else p[1] for p in trail]
    return {"x": [round(min(xs), 2), round(max(xs), 2)], "y": [round(min(ys), 2), round(max(ys), 2)]}


def heat_map(trail, problems, start, three_d, out, goal=None, size=640):
    """Where the node was, seen from above (x and z) in 3D or as the screen is (x and y) in 2D: each cell by how many
    frames it spent there (log scale), the start in green, the goal in cyan, problems in red with their numbers."""
    xs = np.array([p[0] for p in trail], dtype=float)
    ys = np.array([p[2] if three_d else p[1] for p in trail], dtype=float)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    pad = max(x1 - x0, y1 - y0, 1e-6) * 0.08 + (0.5 if three_d else 16)
    x0, x1, y0, y1 = x0 - pad, x1 + pad, y0 - pad, y1 + pad
    span = max(x1 - x0, y1 - y0)
    w = max(1, round(size * (x1 - x0) / span))
    h = max(1, round(size * (y1 - y0) / span))
    cells = 64
    grid, _, _ = np.histogram2d(ys, xs, bins=[max(1, round(cells * h / size)), max(1, round(cells * w / size))],
                                range=[[y0, y1], [x0, x1]])
    t = np.log1p(grid) / max(np.log1p(grid.max()), 1e-9)
    rgb = np.stack([np.clip(1.6 * t, 0, 1), np.clip(1.6 * t - 0.6, 0, 1), np.clip(3 * t - 2, 0, 1)], axis=-1)
    rgb[grid == 0] = (0.1, 0.1, 0.12)
    img = Image.fromarray((rgb * 255).astype(np.uint8)).resize((w, h), Image.NEAREST)
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=12)

    def at(p):
        return ((p[0] - x0) / (x1 - x0) * w, ((p[2] if three_d else p[1]) - y0) / (y1 - y0) * h)

    sx, sy = at(start)
    d.ellipse((sx - 6, sy - 6, sx + 6, sy + 6), outline=(80, 230, 120), width=3)
    if goal is not None:
        gx, gy = at(goal)
        d.rectangle((gx - 6, gy - 6, gx + 6, gy + 6), outline=(0, 220, 255), width=3)
    for i, p in enumerate(problems, 1):
        px, py = at(p["at"])
        d.line((px - 7, py - 7, px + 7, py + 7), fill=(255, 60, 60), width=3)
        d.line((px - 7, py + 7, px + 7, py - 7), fill=(255, 60, 60), width=3)
        d.text((px + 9, py - 7), f"{i} {p['kind']}", fill=(255, 90, 90), font=font, stroke_width=2,
               stroke_fill=(0, 0, 0))
    caption = (f"where it went, {'from above (x, z)' if three_d else 'as on screen (x, y)'}: x {x0:.4g} to {x1:.4g}, "
               f"{'z' if three_d else 'y'} {y0:.4g} to {y1:.4g}; green the start, red the problems")
    sheet = Image.new("RGB", (max(w, 560), h + 22), (24, 24, 24))
    sheet.paste(img, (0, 22))
    ImageDraw.Draw(sheet).text((4, 4), caption, fill=(230, 230, 230), font=font)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return str(out)


def add_live_parsers(commands, command):
    """gdh live explore. `command` is live.py's maker of a subcommand."""
    p = command("explore", cmd_explore, "A bot that plays for N frames, at random or toward a goal, and reports where "
                                        "it got stuck, soft-locked, fell forever or left the bounds, each as a "
                                        "replayable scenario, with a heat map",
                instance="Which game instance: a number (default 0). Every instance steps")
    p.add_argument("frames", type=int, help="How many frames to play")
    p.add_argument("--node", required=True, metavar="PATH", help="The character")
    p.add_argument("--mode", choices=["random", "goal"], default=None,
                   help="random (the default), or goal: toward --goal")
    p.add_argument("--goal", metavar="X,Y[,Z]", help="Where to head for (implies --mode goal)")
    p.add_argument("--seed", type=int, default=1, help="The bot's random choices (default 1)")
    p.add_argument("--chunk", type=int, default=15, metavar="N", help="Frames each choice is held (default 15)")
    p.add_argument("--left", default="ui_left", metavar="INPUT", help="The inputs (default ui_left, ui_right; "
                                                                     "--up and --down have none unless given)")
    p.add_argument("--right", default="ui_right", metavar="INPUT")
    p.add_argument("--up", metavar="INPUT")
    p.add_argument("--down", metavar="INPUT")
    p.add_argument("--jump", default="ui_accept", metavar="INPUT", help="Tapped now and then (default ui_accept; "
                                                                       "'' for none)")
    p.add_argument("--jump-chance", type=float, default=0.3, metavar="P",
                   help="Random mode: the chance a chunk taps the jump (default 0.3)")
    p.add_argument("--near", type=float, default=8.0, metavar="D", help="Goal mode: close enough (default 8)")
    p.add_argument("--still", type=float, metavar="D",
                   help="Staying within D of where it was is getting nowhere (default 8 px, or 0.25 m in 3D)")
    p.add_argument("--stuck", type=int, default=90, metavar="N",
                   help="Stuck after N frames holding a direction and getting nowhere (default 90)")
    p.add_argument("--softlock", type=int, default=150, metavar="N",
                   help="Soft-locked after N frames getting nowhere, two directions or more tried (default 150)")
    p.add_argument("--fall", type=int, default=180, metavar="N", help="Falling forever after N frames down (default 180)")
    p.add_argument("--bounds", metavar="X0,Y0,X1,Y1", help="Out of bounds outside this (x and y; x and z in 3D)")
    p.add_argument("--reset", metavar="EXPR",
                   help="An expression that puts the game back, run first and after each problem (default: the node "
                        "back at its start, at rest; a soft lock then ends the run)")
    p.add_argument("--reset-when-stuck", action="store_true", help="Reset after getting stuck too")
    p.add_argument("--max-problems", type=int, default=20, metavar="N", help="Stop after N problems (default 20)")
    p.add_argument("--out", metavar="DIR", help="Where the scenarios and the heat map go (default <session "
                                                "out>/feel/explore)")
    p.add_argument("--fail", action="store_true", help="Exit 1 when it found a problem")
