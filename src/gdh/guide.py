"""gdh guide: a one-page cheat sheet for an agent that has gdh but not its Claude Code skill."""

GUIDE = """\
gdh: see and drive a Godot 4 game off-screen, on the real GPU. Nothing reaches the desktop.
Never run godot yourself: a bare godot can open windows or dialogs on the user's screen.

LOOK
  gdh capture --project P --scene res://s.tscn --out D     6 views, errors, probe findings, crops/
  gdh capture ... --baseline B [--update-baseline]           exit 1 naming each view that changed
  gdh measure diff a.png b.png --out D                       share changed, its box, crop.png

PLAY (one session per task, a name of your own; always stop it)
  gdh live start --project P --session S [--scene res://...] [--recipe F] [--seed N] [--timeline] [-- game args]
  gdh live step 30 --hold ui_right --session S               exactly 30 frames, then held
  gdh live step --until "EXPR" --max 1200 --session S        wait for it; exit 1 with the last value
  gdh live step 120 --trace "EXPR" --every 10 --session S    a value, frame by frame
    ... --trace-chart F.png [--trace-rates]                  as a line chart (with velocity, acceleration)
  gdh live eval "[EXPR, EXPR]" --session S                   several values in one call
  gdh live find TEXT --session S                             visible nodes showing TEXT, with boxes
  gdh live snapshot [PATH] --session S                       the UI on screen as text, with states
  gdh live step 2 --click-text TEXT --session S              or --click-node PATH; fails if covered
  gdh live shot --session S [--node PATH --zoom 2] [--out F.png] [--max-width 1280]
  gdh live onion 40 --node PATH --hold ui_right --session S  a movement in one image, oldest faintest
  gdh live step 60 --trail PATH --every 5 --session S         its path on the last frame; px between dots
  gdh live batch --session S < lines.txt                     many CLI lines, one process
  gdh live reload --session S                                GDScript edited: new code, same state
  gdh live restart --replay --session S                      anything else: rebuilt, back to the frame
  gdh live net --latency 150 --cut --instance 1 --session S  with start --net NAME: a worse network to a companion
  gdh live list                                              every session, left-over ones included
  gdh live stop --session S

INPUT (step options)
  --press/--release/--hold/--tap INPUT   INPUT: action, key:Space, key:ctrl+s, joy:a, mouse:left
  --click X,Y  --right-click X,Y  --move X,Y  --type TEXT  --wheel down:3  --mod ctrl
  --axis left_x=0.5  --touch X,Y  --touch-drag X,Y:X,Y  --look DX,DY
  Positions are screenshot pixels. A frame is one physics tick.

KEEP IT
  gdh live save-scenario --session S test/x.scenario.json    then add "expect" checks
  gdh scenario run test/x.scenario.json                      replays frame-exactly; gdh test runs it too
  Python: from gdh.client import Session

MEASURE
  gdh live bench 600 --budget-p99 8.3 --session S            frame times; exit 1 over budget
  gdh live monitors --leak --session S                       nodes, orphans, memory growing
  gdh live audio --session S                                 bus peaks, what played
  gdh live record 90 --out D --session S; gdh measure flicker|shimmer|black D

BUILDS AND THE EDITOR
  gdh export --project P --preset Linux --smoke 10           build, run it off-screen, fail on errors
  gdh live start --binary build/game.x86_64 --session S      any program as it is: wait, shot, input
  gdh editor --project P --scene res://s.tscn --out D        what the editor shows, tool scripts included
  gdh bridge ...   gdh api CLASS[.MEMBER]   gdh test --project P

RULES
  Results go to stdout; engine errors, DEFECT lines and the game's prints go to stderr. Never 2>/dev/null.
  --strict exits 1 when the game raised engine errors.
  Read every image you report: open it with your image reader, at about 1280 px wide or less.
  A DEFECT line means resources failed to load: report it.
  gdh builds C# and imports assets itself when they changed.
  Docs: README.md and docs/ in gdh's source; gdh <command> --help.
"""


def cmd_guide(args):
    print(GUIDE, end="")
    return 0


def add_parser(sub):
    p = sub.add_parser("guide", help="A one-page cheat sheet for agents: the commands that save the most calls")
    p.set_defaults(func=cmd_guide)
