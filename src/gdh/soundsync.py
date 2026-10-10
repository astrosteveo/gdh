"""gdh live soundsync: how many frames a sound comes after (or before) what it belongs to: a footstep and the foot
landing, a hit and its impact, a door and its slam.

A sound starts on the frame the game calls play(): every play() makes a new playback, and the harness notes each
player whose playback isn't the one it saw the frame before. That's game time, exact even under gdh's dummy audio
driver, which mixes in real time, so that under a step a short sound may still be playing when it's played again. The visual event is an expression becoming true (--when), or the game's own
event (--event NAME, from GdhEvents.emit). Each event is paired with the nearest sound start within --window frames,
and the offset in frames is the sound's frame less the event's: positive when the sound comes late.
"""
import json

from gdh.feel import Driver, FeelError

class SyncError(FeelError):
    pass


def rising(frames, values):
    """The frames a value turned truthy."""
    return [f for i, f in enumerate(frames) if values[i] and not (values[i - 1] if i else False)]


def pair(events, sounds, window):
    """Each event with the nearest sound start within `window` frames (each start used once): [{"event", "sound",
    "player", "offset"}], and the starts left over."""
    left = sorted(sounds)
    pairs = []
    for event in events:
        near = [s for s in left if abs(s[0] - event) <= window]
        if not near:
            pairs.append({"event": event, "sound": None, "player": None, "offset": None})
            continue
        best = min(near, key=lambda s: (abs(s[0] - event), s[0]))
        left.remove(best)
        pairs.append({"event": event, "sound": best[0], "player": best[1], "offset": best[0] - event})
    return pairs, left


def cmd_soundsync(args):
    from gdh.live import step_input_events
    if bool(args.when) == bool(args.event):
        raise SyncError("soundsync pairs the sounds with --when EXPR (true when it happens) or --event NAME (the "
                        "game's own GdhEvents): give one.")
    game = Driver(args)
    wanted = []
    for path in args.player or []:
        if not game.eval(f"has_node({json.dumps(path)})"):
            raise SyncError(f"No node at {path}.")
        wanted.append(str(game.eval(f"get_node({json.dumps(path)}).get_path()")))
    result = game.step(args.frames, step_input_events(args, args.frames), trace=[args.when] if args.when else (),
                       sound_starts=True)
    sounds = [(f, p) for f, p in result["sound_starts"] if not wanted or p in wanted]
    if args.when:
        rows = result["trace"]["rows"]
        events = rising([row[0] for row in rows], [row[1] for row in rows])
    else:
        events = [e["frame"] for e in result["game_events"] if e["name"] == args.event]
    players = sorted({p for _, p in sounds} | set(wanted))
    pairs, unpaired = pair(events, sounds, args.window)
    ticks = game.ticks
    late = [p for p in pairs if p["offset"] is not None and abs(p["offset"]) > args.max_offset]
    missing = [p for p in pairs if p["offset"] is None]
    offsets = [p["offset"] for p in pairs if p["offset"] is not None]
    out = {"frames": args.frames, "ticks_per_second": ticks, "players": players,
           "event": args.event or args.when, "pairs": pairs, "unpaired_sounds": [{"frame": f, "player": p}
                                                                                  for f, p in unpaired],
           "offsets": {"min": min(offsets), "max": max(offsets)} if offsets else None,
           "out_of_sync": len(late), "no_sound": len(missing)}
    if args.json:
        print(json.dumps(out, indent=1))
        return 1 if args.fail and (late or missing) else 0
    what = f"--event {args.event}" if args.event else f"--when {args.when}"
    print(f"sound against {what} over {args.frames} frames: {len(events)} events, {len(sounds)} sound starts")
    ms = 1000 / ticks
    for p in pairs[:30]:
        if p["offset"] is None:
            print(f"  event at frame {p['event']}: no sound within {args.window} frames")
            continue
        when = "on the same frame" if p["offset"] == 0 else (
            f"{abs(p['offset'])} frame{'s' if abs(p['offset']) > 1 else ''} ({abs(p['offset']) * ms:.0f} ms) "
            f"{'late' if p['offset'] > 0 else 'early'}")
        flag = "  <- out of sync" if abs(p["offset"]) > args.max_offset else ""
        print(f"  event at frame {p['event']}: {p['player'].rsplit('/', 1)[-1]} {when}{flag}")
    if len(pairs) > 30:
        print(f"  ... {len(pairs) - 30} more (--json has them all)")
    if unpaired:
        print(f"  {len(unpaired)} sound starts with no event near them: "
              + ", ".join(f"{p.rsplit('/', 1)[-1]} at {f}" for f, p in unpaired[:8]))
    if late or missing:
        print(f"warning: {len(late)} of {len(pairs)} sounds more than {args.max_offset} frames off their event, "
              f"{len(missing)} events with no sound")
    return 1 if args.fail and (late or missing) else 0


def add_live_parsers(commands, command):
    """gdh live soundsync. `command` is live.py's maker of a subcommand."""
    from gdh.live import add_input_options
    p = command("soundsync", cmd_soundsync, "Frames between a sound starting and what it belongs to (an expression "
                                            "turning true, or a GdhEvents event), for each time it happens",
                instance="Which game instance: a number (default 0). Every instance steps")
    p.add_argument("frames", type=int, help="How many frames to step")
    p.add_argument("--when", metavar="EXPR", help="The visual event: the frames this expression turns true")
    p.add_argument("--event", metavar="NAME", help="The visual event: the game's own GdhEvents.emit(NAME)")
    p.add_argument("--player", action="append", metavar="PATH",
                   help="An audio player to listen to; repeatable (default every one, those spawned during the run "
                        "included)")
    p.add_argument("--window", type=int, default=15, metavar="F",
                   help="Pair an event with a sound starting within F frames of it (default 15)")
    p.add_argument("--max-offset", type=int, default=2, metavar="F",
                   help="Flag a sound more than F frames off its event (default 2, about 33 ms at 60 a second)")
    p.add_argument("--fail", action="store_true", help="Exit 1 when one is out of sync or has no sound")
    add_input_options(p)
