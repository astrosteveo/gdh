# Live control

`gdh live` starts a game off-screen and drives it one command at a time. The game is held between commands, so no game time passes while you, or an agent, look at a screenshot and decide what to do next.

```sh
gdh live start --project path/to/game            # main scene, held at frame 0
gdh live step 30 --hold ui_right --shot          # run 30 frames holding right, then save a frame
gdh live tree Player                             # inspect nodes
gdh live eval "get_node('Player').velocity"      # read any value
gdh live probes                                  # run the probes on the current frame
gdh live step --until "get_node('Ship').docked"  # run until it holds, at most 3600 frames
gdh live stop
```

A C# project is built with `dotnet build` first and run with `godot-mono` (README, "C# projects"). A project whose import cache is missing or stale is imported first (README, "Importing"; `--no-import` skips it). Once the game is ready, `start` checks that its window is the `--resolution` asked for, and stops the game, saying what size it found, if the game sized it otherwise; a game that resizes its window later gets a note on the next `step` (README, "Window size"). Arguments after `--` go to the game: `gdh live start --project path/to/game -- --server ws://localhost:8787` gives the game exactly `["--server", "ws://localhost:8787"]` from `OS.get_cmdline_user_args()`. The harness's own settings travel in the `GDH_ARGS` environment variable, so they never mix with the game's.

Nothing is installed into the project. `gdh` runs Godot with `--script src/gdh/harness/live.gd`, which loads the scene and attaches the control node `bridge.gd`. The game runs on a display of its own: the GPU display by default, or Xvfb (`--display`, README's [Displays](../README.md#displays)). The project's autoloads load as usual. The control node is an internal child of the root, so game code that walks `root.get_children()` doesn't see it.

## Time

- The game starts held at game frame 0. The scene's `_ready` has run, and no `_process` or `_physics_process` has.
- `step N` runs exactly N frames, then holds again. Godot is launched with `--fixed-fps` set to the project's physics tick rate, so each frame is exactly one physics tick of game time, however long it takes to render.
- The frame number in every reply counts game frames only. Frames rendered while held don't count.
- Held, the game still draws 20 frames a second, so it barely loads the GPU, and a request wakes it at once. gdh paces these frames itself, since Godot's own limiter (`Engine.max_fps`) is off under `--fixed-fps`.
- `run` lets the game run at about real time until `pause`.
- Steps run as fast as the GPU draws, since gdh starts Godot with V-Sync off. A game that turns V-Sync on itself waits for the display's 60 Hz refresh on the GPU display, and the first `step` after it does says so in a note.
- Holding uses `SceneTree.paused`. It has these side effects:
  - Nodes whose process mode is `ALWAYS` or `WHEN_PAUSED` keep running while held. `status` lists them.
  - If the game unpauses itself while held, `gdh` pauses it again and says so in the next reply.

## Input

Input is injected at the start of a step, where real input arrives. `_input`, `is_action_just_pressed` and `is_action_pressed` see it the same way they see a player's input. An input is either an action name from the Input Map or `key:NAME` for a key (for example `key:Space`). A key goes through the Input Map like a real key press.

| Option | Effect |
|---|---|
| `--move X,Y` | Move the pointer to screenshot pixel X,Y at the start of the step, before any press. With a button held, it's a drag: each move carries how far it went and the buttons down |
| `--press INPUT` | Press at the start of the step, and keep it pressed after |
| `--release INPUT` | Release at the start of the step |
| `--hold INPUT` | Press at the start, release at the end |
| `--tap INPUT` | Press for one frame |
| `--type TEXT` | Type TEXT into whatever has the keyboard's focus (a `LineEdit`, a chat line), a character a frame from the step's start: each a key press and release carrying its character, with Shift for a capital. The step must be at least as many frames as characters |
| `--click X,Y` | Left click at screenshot pixel X,Y |
| `--right-click X,Y` | Right click at screenshot pixel X,Y |
| `--left-hold X,Y` | Press the left button at X,Y at the start of the step, release it at the end |
| `--right-hold X,Y` | Press the right button at X,Y at the start of the step, release it at the end (a held press: a context or marking menu) |

An INPUT can also be `mouse:left`, `mouse:right` or `mouse:middle`, pressed where the pointer is: `--move 300,200 --press mouse:right`, then steps with `--move` to drag or point, then `--release mouse:right`.

Each option can be repeated. Events at the end of a step (a `--hold`'s release) reach the game before it's held again, so a node that tracks keys by their events (`_input`, `_unhandled_input`) sees them go, not only `Input.is_action_pressed`.

## Coordinates

Every position `gdh` accepts or reports is in screenshot pixels: clicks, the `screen` field in `tree`, and probe `screen_rect`s. So you can click where a screenshot or `tree` shows something, whatever the project's stretch settings are. Tests cover stretch modes `viewport` and `canvas_items`.

`tree` also reports `pos`, which is the node's global position in world units.

## Seeing the game

| Command | Output |
|---|---|
| `shot [--view V]... [--label L] [--tiles]` | PNGs of the current frame, in any capture view |
| `step N --shot-every K` | A frame every K frames during the step, to catch flicker, popping and jitter |
| `step N --shot` | A frame after the step |
| `probes` | Probe findings on the current frame, with crops |
| `tree [PATH] [--depth N]` | Nodes with class, script, world position, screen position (`[x, y]`, or `[x, y, w, h]` for a Control), text, value, velocity and animation |
| `eval EXPR` | Any Godot expression. The base is the current scene. `scene`, `tree`, `root`, each autoload by name and the engine's singletons (`OS`, `Engine`, `Input`, `Time`, `RenderingServer` and the rest) are also available. |
| `status` | Game frame, held or running, scene, image and window size, nodes that run while held, processes the game spawned |
| `record N [--out DIR]` | N frames stepped and each saved as `DIR/frame-0000.png` on, for `gdh measure` |
| `measure KIND [--frames N]` | The same, measured: flicker, shimmer, jitter, black, crush or line ([measure.md](measure.md)) |
| `frames [--clear] [--reset] [--save FILE]` | Each game frame's GPU and CPU time since the record started over, and each render pass's with `start --gpu-passes`: the median, 99th percentile and worst |

Screenshots go to `./captures/live/<session>/shots/`, or to `--out` if given. Their names start with a number that goes on from the highest already in the folder, so a session started again with the same `--out` never writes over earlier shots. An array in a reply (from `eval`, say) keeps its first 100 items and ends with a string saying how many more there were: `"... 150 more (250 in all)"`. Add `--json` to any command for the raw reply.

## Waiting and tracing

A step can run until something holds, and record values as it goes, so waiting for a scene, a login or a landing takes one command instead of a loop of `step` and `eval`.

```sh
gdh live step --until "scene.name == 'Hangar'"                       # check after every frame, stop when it holds
gdh live step --until "get_node('Ship').landed" --every 10 --max 1200 --hold ui_down
gdh live step 120 --trace "get_node('Ship').position.y" --trace "GameState.fuel" --every 5 --trace-out fuel.csv
```

- **`--until EXPR`** runs until EXPR is truthy, checked after every `--every K` frames (default 1). EXPR is a Godot Expression with the same inputs as `eval`. The step stops at the first check that holds, and the reply has the frames run and the value (`"until": {"expr", "met", "value", "frame", "checks"}`). The frame count, or `--max N`, is the most it runs, 3600 frames if neither is given. If EXPR never holds, the step ends there and gdh exits 1, saying what EXPR was at the last check. A check whose evaluation fails (a node that isn't there yet, a scene changing) counts as not yet, and the reply says why (`"error"`).
- **Input** works as in any step. When `--until` ends the step early, the inputs due at the step's end (a `--hold`'s release) go to the game then, so nothing is left held that the step would have let go. Inputs due later (the rest of a `--type`) don't.
- **`--trace EXPR`** (repeatable) records each EXPR's value at every check: `"trace": {"exprs", "every", "rows": [[frame, value, ...], ...]}`, where `frame` is the game frame. As text, a row that repeats the one before is left out, and at most 40 rows are shown; `--json` and `--trace-out FILE.csv` have every row. In the CSV a string is as it is, anything else is JSON, and a value whose evaluation failed is empty (`null` in JSON, with the reason in `"failed"` and a note).
- With several instances, `--until` is refused, since the instances step together and each would stop on its own. `--trace` records every instance's values.

## Problems and the game's output

Results go to stdout. Everything about what went wrong goes to stderr, so a command whose stdout is thrown away (`>/dev/null`, or read by a script) still shows it:

- **Engine errors** raised since the previous reply, with repeats merged. An error raised from a script (a `push_error`, a call on a null instance, an engine error the script's call raised) has the script's backtrace under it, innermost call first: `  at res://player.gd:42 in _physics_process`. gdh's own frames are left out.
- **`DEFECT:` lines** for resources that failed to load among them.
- **Notes** (the game unpaused itself, turned V-Sync on, resized its window).
- **What the game printed** (`print`, `printerr`) since the previous reply, each line as `game: LINE`. A line printed again at once is merged into one with a count, `again (x50)`. Past 40 lines, the first 10 and the last 30 are kept, with a line saying how many were cut. What the game prints while it loads is shown by `start`.

With `--json` all of it is in the reply instead: `"errors"` (each with `"backtrace"` when it has one), `"notes"`, `"output"` and `"output_cut"`.

**`--strict`**, on any command, or `GDH_STRICT=1` in the environment, makes a command exit 1 when the game raised engine errors during it (warnings don't count), after printing its results as usual. In a `batch`, each command is judged on its own.

## Companions

A session can start other programs beside the game, such as a game server, and stop them with it:

```sh
gdh live start --project client --session vs \
  --companion 'server=PORT={port} exec ./server --test-clock' \
  --companion-ready 'server=http://127.0.0.1:{port}/health' \
  -- --server ws://127.0.0.1:{server.port}/play
```

- **`--companion NAME=COMMAND`** runs COMMAND with `sh -c`, from the directory gdh was run in, in a process group of its own. It's repeatable, and companions start in order, each once the one before is ready, before the game.
- **Ports:** gdh picks a free port for each companion. `{port}` in its command and ready check is its own port, also in `$GDH_PORT`. `{NAME.port}` is a companion's port anywhere, the game's arguments included. `--companion-port NAME=PORT` gives it a port of your choosing instead. A placeholder naming no companion stops the start.
- **Ready:** `--companion-ready NAME=CHECK` says when it's ready. `tcp`, the default, waits for its port to take a connection. An `http://` or `https://` URL waits for a 2xx answer, and `none` doesn't wait. `--timeout` bounds the wait for each companion, and then for the game.
- **Logs:** each companion's output is in `<out>/<NAME>.log`. A companion that exits before it's ready, or isn't ready in time, stops the start with the end of its log, and nothing is left running.
- **Stopping:** `stop` quits the game first, so it can sign off, then stops every companion's process group. A watchdog also stops them if the game ends by itself (its idle timeout, a crash). While the session runs, a companion that has exited is noted, with the end of its log, on every command, and `status` shows each companion.

## Several instances

`--instances N` runs N instances of the game in one session, each on a display of its own, with its own output in `<out>/instance-K/`. `{instance}` in the game's arguments is each one's number, so they can be told apart (`-- --user pilot{instance}`).

- **`step`, `run` and `pause` go to every instance at once**, so the instances step together: when each frame of one waits on another (two clients of a server on a test clock that ticks once every client has asked), they keep in step. The input of a step goes to `--instance K` (default 0), or to every instance with `--instance all`.
- **`shot`, `probes`, `tree` and `eval` go to `--instance K`** (default 0), or to every instance with `--instance all`.
- With one instance, every reply is the game's own. With more, a command sent to one instance gets that instance's reply, with `"instance": K`, and a command sent to several gets `{"ok": ..., "instances": [reply, ...]}`, with the first failure as its `"error"`. The CLI prefixes each instance's lines with `[K]`.
- If any instance ends, the session has ended: the next command says which, and stops the rest.

## Batches: `gdh live batch`

`gdh live batch --session NAME` runs command lines from stdin, written as on the command line, one after another in one process, and prints each one's output after a `> LINE` header:

```sh
gdh live batch --session s <<'END'
# log in, then fly
step 2 --click 640,400
step 6 --type "pilot1"
step --until "scene.name == 'Hangar'" --max 1200
shot --label hangar
eval "GameState.credits"
END
```

- A line is a `gdh live` command, with or without `gdh live` before it, split as a shell splits it. `#` comments and blank lines are skipped. Every command but `start`, `batch` and `pipe` can be on a line. Each line runs on the batch's `--session`, unless it gives its own.
- A command that fails says so on stderr, and the batch goes on; `--stop-on-error` stops it there. The batch exits 1 if any command failed, saying on which lines. If the session ends, the batch stops.
- `--json` prints one JSON line per command: `{"line", "ok", "replies": [...], "error"}`, with the replies `--json` would print.
- `--strict` applies to each command.

## Scripts: `gdh live pipe`

A script that drives many steps can keep one `gdh live pipe --session NAME` open instead of starting gdh for each command. It reads requests from stdin, one JSON object a line, and answers each on stdout, one a line:

```json
{"cmd": "step", "args": {"frames": 10, "events": [{"action": "jump", "pressed": true, "at": 0}]}, "instance": 0}
{"cmd": "eval", "args": {"expr": "get_node('Player').position"}, "instance": "all"}
```

The commands and their arguments are the raw protocol's (below), so a step takes `"until"`, `"trace"` and `"every"` there too, and `"instance"` follows the rules above. A request waits for its reply for `"timeout"` seconds if it gives one, otherwise 300, or for a `step` two seconds a frame when that's longer, as `gdh live step` does. The replies are what `--json` prints. `quit` is refused: stop the session with `gdh live stop`, which stops its companions too. If the game ends, the pipe answers with how it ended and exits with status 1.

## Processes the game spawns

A game can start other programs: a launcher that starts the game itself and exits, a server, a tool it runs. gdh marks the game with a random `GDH_MARK=...` in its environment, which everything it spawns inherits, and finds those processes by it (`src/gdh/spawned.py`). (Godot's `OS.create_process` starts each child in a session of its own, and a child whose parent exits is handed to init, so the game's process group and parent links don't find them.) They run on the game's display, with its environment, and their output goes to the game's `godot.log`.

- **`status` lists them:** `spawned: pid N: COMMAND` under each instance, and `"spawned"` in `--json` (with `"instance"` when there are several).
- **They end with the session.** `stop`, the watchdog (when a game ends by itself) and the cleanup after a crash stop them by pid, after the game and before the display. A spawned window can't outlive its display anyway: X closes its connection.
- **`start --keep-children`** keeps the session, and its display, while any game or any process they spawned runs. When a launcher hands off and exits, the session goes on: `status` says the game has exited and lists what it spawned, commands that need the game (`step`, `shot`, `eval` and the rest) fail saying so, and `stop` ends it. Once the last spawned process exits, the watchdog stops the display and the next command says the session has ended. The idle timeout still applies (below): after the game has exited, the watchdog keeps it, and stops what the game spawned, then the display, as `stop` does, once no `gdh live` command has touched the session for that long. gdh's harness runs in the game it started, not in what that spawns, so a spawned game can be watched (its log, the files it writes, `status`) but not stepped or captured: to drive the real game, start it directly with `gdh live start`, with the arguments the launcher would give it.
- A process that clears its environment, or sets `GDH_MARK` itself, isn't found.

## Sessions

- **Several games at once:** `--session NAME` runs more than one game side by side. The default name is `default`.
- **Listing them:** `gdh live list` shows every session of yours, whoever started it: running or ended, its pids and displays, the project and the scene it started in, how long its game has run and how long since a command on it (`--json` for a list). It only reads the session files, so it doesn't keep a game from its idle timeout, and a session whose game has ended stays listed until a command on it cleans it up.
- **Options in one argument:** zsh doesn't split an unquoted variable, so `S="--session x --json"; gdh live step 10 $S` passes `--session x --json` as one argument. gdh says so in one line and exits 2. Use `${=S}` or an array.
- **Session files:** each is kept in `$XDG_RUNTIME_DIR/gdh/` and can be read only by you. It holds the port and a random token that every request must carry. The game gets the token through its environment, which other users can't read. Keeping it off the command line keeps it out of the process list.
- **Local only:** the game listens on 127.0.0.1.
- **Idle timeout:** a game quits after 30 minutes without a request, which ends the session. Change this with `--idle-timeout SECONDS`, or set 0 to turn it off. With `--keep-children`, the game's quitting doesn't end the session, so once every game has exited the watchdog keeps the timeout instead: when no `gdh live` command has been run on the session for that long, counted from the later of the last command and the game's exit, it stops the processes the game spawned, then the display, and notes why at the end of the game's `godot.log`. Every `gdh live` command on the session counts, `status` included, and so do ones that fail because the game has exited; `start` and `stop` don't.
- **Stopping:** `stop` asks the game to quit, then stops Godot and its display if they're still running, and removes the display's runtime directory. The display's own output is in `<out>/display.log`.
- **Crashed games:** if a game dies, the next command says so, shows the end of its log, stops the session's other processes (and those the game spawned) and removes the session; with `--keep-children` that waits until what the game spawned has ended (above).

## Protocol

One JSON object per line over TCP.

```json
{"id": 1, "token": "…", "cmd": "step", "args": {"frames": 30, "events": [{"action": "ui_right", "pressed": true, "at": 0}]}}
{"id": 1, "ok": true, "result": {…}, "errors": […], "frame": 30, "held": true}
```

This is how gdh talks to one instance. The commands are `status`, `step`, `shot`, `probes`, `tree`, `eval`, `frames`, `run`, `pause` and `quit`. `step` takes `frames`, `events` and `shot_every`, and `until` (an expression), `trace` (a list of expressions) and `every` (see "Waiting and tracing"). Every reply has `"errors"`, each `{type, message, where, count}` with a `backtrace` for one raised from a script, and, when the game printed anything since the previous reply, `"output"` and `"output_cut"`. `frames` takes `{"reset": bool, "clear": bool}` and returns `{"frames": [{"gpu": ms, "cpu": ms, "frame": n, "passes": {name: ms}, "groups": {name: ms}}, ...], "game_frames": n, "size": [w, h], "adapter": name}`. In `step`, an event with `"at": k` is injected before frame k+1 of the step. Event forms:
- `{action, pressed, strength}`
- `{key, pressed}`
- `{mouse_button, position, pressed}`
- `{mouse_motion: [x, y]}`

## Tests

The tests need Godot, a GPU with Vulkan, Xvfb, and weston and Xwayland. Every test runs on both displays, the GPU display and Xvfb (the GPU display's are skipped without weston and Xwayland). `uv run pytest` runs `tests/test_live.py` against `testbed/live/arena.tscn`. It checks:

- exact frame counts and movement at 60 and 120 ticks per second
- that taps reach `_physics_process`, `_process` and `_input` once each
- held input across steps
- button clicks at the positions `tree` reports, with no stretching and in both stretch modes
- screenshots, the tree, error reporting and the unpause note
- an array of over 100 items in a reply saying how many were left out, shots numbered on from an earlier session's in the same out dir, and a pipe step's wait growing with its frames
- a bad scene, the idle timeout and cleanup after a crash

`tests/test_live_loop.py` runs `testbed/chatty/chatty.tscn`, which counts its frames, prints every 10 frames and on request, and raises an error two calls down. It checks:

- `--until` stopping at the first check that holds, with `--every`; giving up at `--max` with exit 1, and with an expression that fails; and letting go of a `--hold` when it ends early
- `--trace` rows and their CSV, the text leaving out repeated rows, and `--until` and `--trace` through `pipe`
- `batch`: its headers and outputs, going on after an error or stopping with `--stop-on-error`, and `--json`
- errors, `DEFECT:` lines and notes on stderr with only the result on stdout, and `--strict` and `GDH_STRICT=1`
- the game's output in replies, merged and cut, and at `start`; a script error's backtrace without gdh's frames
- `list`, and the one-line hint for options run together in one argument

`tests/test_companions.py` runs two instances of `testbed/live/lockstep.tscn` beside `testbed/live/barrier.py`, a companion every instance waits at each frame, as clients of a server on a test clock do. It checks:

- that `{port}`, `{NAME.port}` and `{instance}` reach the companion and each instance
- that the instances step together: 60 frames with no wait given up, where stepping them one after the other would stall every frame
- input to the instance named, and a hold's release seen by a node that tracks its keys by their events
- one instance's reply alone, a bad instance number, and `pipe`
- `status`, an HTTP ready check, `stop` stopping the companion, the watchdog stopping it when the game ends by itself, a companion that fails to start, and a placeholder that names no companion

`tests/test_spawned.py` runs `testbed/children/launcher.tscn`, a launcher that starts `child.gd` in a second Godot on the same display and quits after a few frames. It checks:

- the child listed by `status` (text and `--json`), and stopped by `stop`
- without `--keep-children`, the child ending once the launcher quits
- with `--keep-children`, the session and display kept after the launcher quits, the child still drawing frames, `status` saying the game exited, `shot` refused saying why, and the watchdog stopping the display and ending the session once the child exits
- with `--keep-children` and a short `--idle-timeout`, `status` keeping the session open after the launcher quits, and the watchdog stopping the child and the display once no command has come for that long

`tests/test_imports_and_size.py` checks the import cache's check and the window's size:

- a fresh copy of the testbed (no `.godot`) imported before its first capture, which then loads every texture, and not imported again on the second; a touched but unchanged texture not counted as stale, and a changed, a new or a deleted import counted, a VRAM-compressed texture's included (its record is `name.png-<hash>.md5` beside `name.png-<hash>.s3tc.ctex`)
- a stale asset found though another is remembered as one an import can't fix
- the class cache counted as stale when a script declares a class it lacks, or it has a class no script declares
- resources that fail to load (their imported copies deleted, `--no-import`) printed as a `DEFECT:` and listed in `report.json`, and each way Godot words a failed load read as one
- `report.json`'s `window_size`, and a scene that resizes its own window failing `capture` and refused by `live start`, with no session left behind

`tests/test_display.py` checks the displays themselves:

- screenshots of a 3D scene in all six views, captured and at the same stepped frame live, the same pixel for pixel on both displays
- that the GPU display has DRI3 and runs Godot's X11 driver with V-Sync off, says so when a game turns V-Sync on, and leaves no process or runtime directory behind
- a window smaller than Xwayland's smallest screen (320x200)
- the watchdog stopping both instances' displays when one game is killed
- the fallback to Xvfb, with its note, when weston isn't installed, when weston has only a software renderer, and when it can't start at all; and `--display gpu` failing instead
- a bad `GDH_DISPLAY`, and the sweep of runtime directories left by killed runs
- a display stopping as soon as its processes have exited, an exited child of gdh's reaped rather than waited on, and a capture of `arena.tscn` on the GPU display taking under 2.5 s with Godot's exit code kept

## The game's user data

A game run under gdh (capture or live) keeps `user://` (its saves, settings, logs and caches) in gdh's own directory, `~/.local/share/gdh/user-data` (under `$XDG_DATA_HOME` when set), never in the player's `~/.local/share/godot`. So a test run can't touch a player's saves or rotate out their logs. The directory is kept between runs, so shader caches stay warm. `GDH_USER_DATA=<dir>` picks another one, and `GDH_USER_DATA=real` uses the player's own. `gdh import` keeps the real one, where the editor's settings are.
