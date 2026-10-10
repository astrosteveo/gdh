"""The game's own events: gdh events install (the GdhEvents autoload, into a project) and gdh live events.

A game reports what happens in it (a death, a checkpoint, a pickup, a level loaded) with GdhEvents.emit(name, data).
Under gdh's harness each event is kept with the game frame it happened on, and goes back with the reply to the
command it happened during: it's printed on stderr beside the game's output, and kept in a session's timeline.
`gdh live events` lists them, counts them and charts them over the run.
"""
import json
import re
import shutil
from pathlib import Path

from gdh import chart
from gdh.godot import GdhError

ADDON = Path(__file__).parent / "addon" / "gdh_events" / "gdh_events.gd"
RES_PATH = "res://addons/gdh_events/gdh_events.gd"
AUTOLOAD = "GdhEvents"


class EventsError(GdhError):
    pass


def install(project):
    """Put gdh_events.gd into the project's addons/gdh_events/ and add the autoload GdhEvents to project.godot, unless
    it's there. Returns what it did, as lines."""
    project = Path(project)
    settings = project / "project.godot"
    if not settings.is_file():
        raise EventsError(f"No project.godot in {project}.")
    target = project / "addons" / "gdh_events" / "gdh_events.gd"
    done = []
    if not target.exists() or target.read_text() != ADDON.read_text():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ADDON, target)
        done.append(f"wrote {target}")
    text = settings.read_text()
    line = f'{AUTOLOAD}="*{RES_PATH}"'
    if re.search(rf"^{AUTOLOAD}=", text, re.M):
        done.append(f"{AUTOLOAD} is already an autoload in {settings}")
    else:
        if re.search(r"^\[autoload\]\s*$", text, re.M):
            text = re.sub(r"^(\[autoload\]\s*\n(?:\s*\n)?)", lambda m: m.group(1) + line + "\n", text, count=1, flags=re.M)
        else:
            text = text.rstrip("\n") + f"\n\n[autoload]\n\n{line}\n"
        settings.write_text(text)
        done.append(f"added the autoload {AUTOLOAD} to {settings}")
    return done


def cmd_install(args):
    for line in install(args.project):
        print(line)
    print(f"Call {AUTOLOAD}.emit(\"name\", data) where something happens; gdh live events lists them. With the "
          f"editor open, let it reload project.godot (or add the autoload in Project Settings instead).")
    return 0


def describe(event):
    data = event.get("data")
    return f"{event['name']} at frame {event['frame']}" + (f": {json.dumps(data)}" if data is not None else "")


def counts(events):
    out = {}
    for e in events:
        out[e["name"]] = out.get(e["name"], 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def chart_events(events, out):
    """Each event's count over the run, as steps: a line each, by game frame."""
    names = list(counts(events))[:chart.MAX_PANELS]
    frames = sorted({e["frame"] for e in events})
    lines = []
    for name in names:
        total, series = 0, []
        at = {}
        for e in events:
            if e["name"] == name:
                at[e["frame"]] = at.get(e["frame"], 0) + 1
        for f in frames:
            total += at.get(f, 0)
            series.append(float(total))
        lines.append((name, series))
    return chart.draw([{"title": "events so far, by name", "frames": frames, "lines": lines, "steps": True}], out,
                      "the game's events")


def cmd_events(args):
    from gdh.live import call, load_session, report
    session = load_session(args.session)
    result = report(call(session, "events", {"name": args.name or "", "clear": args.clear}, instance=args.instance),
                    args.json, echo=False)
    if isinstance(result, list):
        result = result[0]
    events = result["events"]
    out = {"events": events, "counts": counts(events)}
    if events and not args.no_chart:
        path = Path(args.chart) if args.chart else Path(session["out"]) / "events" / "events.png"
        out["chart"] = str(chart_events(events, path))
    if args.json:
        print(json.dumps(out, indent=1))
        return 0
    if not events:
        print("No events" + (f" named {args.name}" if args.name else "") + ". The game reports them with "
              "GdhEvents.emit(name, data): gdh events install adds it to a project.")
        return 0
    print(f"{len(events)} events: " + ", ".join(f"{name} {n}" for name, n in out["counts"].items()))
    shown = events[-args.last:] if args.last else events
    if len(shown) < len(events):
        print(f"  ... {len(events) - len(shown)} earlier (--last 0 or --json for all)")
    for e in shown:
        print(f"  {describe(e)}")
    if out.get("chart"):
        print(f"chart: {out['chart']}")
    if args.clear:
        print("cleared")
    return 0


def add_parser(sub):
    p = sub.add_parser("events", help="The game's own events (GdhEvents.emit): install the autoload into a project")
    kinds = p.add_subparsers(dest="events_command", required=True)
    k = kinds.add_parser("install", help="Put the GdhEvents autoload into a project, for GdhEvents.emit(name, data)")
    k.add_argument("--project", default=".", help="The project's directory (default the current one)")
    k.set_defaults(func=cmd_install)


def add_live_parsers(commands, command):
    """gdh live events. `command` is live.py's maker of a subcommand."""
    p = command("events", cmd_events, "The game's own events (GdhEvents.emit) so far, with their frames, counts and a "
                                      "chart", instance="Which game instance: a number (default 0)")
    p.add_argument("--name", metavar="NAME", help="Only events of this name")
    p.add_argument("--last", type=int, default=40, metavar="N", help="List the last N (default 40; 0 for all)")
    p.add_argument("--clear", action="store_true", help="Forget them after listing them")
    p.add_argument("--chart", metavar="FILE.png", help="Where the chart goes (default <session out>/events/events.png)")
    p.add_argument("--no-chart", action="store_true", help="Don't draw the chart")
