# Live control

`gdh live` starts a game off-screen and drives it one command at a time. The game is held between commands, so no game time passes while you, or an agent, look at a screenshot and decide what to do next.

```sh
gdh live start --project path/to/game            # main scene, held at frame 0
gdh live step 30 --hold ui_right --shot          # run 30 frames holding right, then save a frame
gdh live tree Player                             # inspect nodes
gdh live eval "get_node('Player').velocity"      # read any value
gdh live probes                                  # run the probes on the current frame
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

Screenshots go to `./captures/live/<session>/shots/`, or to `--out` if given. Every reply lists the engine errors raised since the previous reply, with repeats merged, and resources that failed to load among them get a `DEFECT:` line of their own. Add `--json` to any command for the raw reply.

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

## Scripts: `gdh live pipe`

A script that drives many steps can keep one `gdh live pipe --session NAME` open instead of starting gdh for each command. It reads requests from stdin, one JSON object a line, and answers each on stdout, one a line:

```json
{"cmd": "step", "args": {"frames": 10, "events": [{"action": "jump", "pressed": true, "at": 0}]}, "instance": 0}
{"cmd": "eval", "args": {"expr": "get_node('Player').position"}, "instance": "all"}
```

The commands and their arguments are the raw protocol's (below), and `"instance"` follows the rules above. The replies are what `--json` prints. `quit` is refused: stop the session with `gdh live stop`, which stops its companions too. If the game ends, the pipe answers with how it ended and exits with status 1.

## Processes the game spawns

A game can start other programs: a launcher that starts the game itself and exits, a server, a tool it runs. gdh marks the game with a random `GDH_MARK=...` in its environment, which everything it spawns inherits, and finds those processes by it (`src/gdh/spawned.py`). (Godot's `OS.create_process` starts each child in a session of its own, and a child whose parent exits is handed to init, so the game's process group and parent links don't find them.) They run on the game's display, with its environment, and their output goes to the game's `godot.log`.

- **`status` lists them:** `spawned: pid N: COMMAND` under each instance, and `"spawned"` in `--json` (with `"instance"` when there are several).
- **They end with the session.** `stop`, the watchdog (when a game ends by itself) and the cleanup after a crash stop them by pid, after the game and before the display. A spawned window can't outlive its display anyway: X closes its connection.
- **`start --keep-children`** keeps the session, and its display, while any game or any process they spawned runs. When a launcher hands off and exits, the session goes on: `status` says the game has exited and lists what it spawned, commands that need the game (`step`, `shot`, `eval` and the rest) fail saying so, and `stop` ends it. Once the last spawned process exits, the watchdog stops the display and the next command says the session has ended. The idle timeout still applies (below): after the game has exited, the watchdog keeps it, and stops what the game spawned, then the display, as `stop` does, once no `gdh live` command has touched the session for that long. gdh's harness runs in the game it started, not in what that spawns, so a spawned game can be watched (its log, the files it writes, `status`) but not stepped or captured: to drive the real game, start it directly with `gdh live start`, with the arguments the launcher would give it.
- A process that clears its environment, or sets `GDH_MARK` itself, isn't found.

## Programs as they are: `--binary`

`gdh live start --binary PATH` runs a program as it is, with no harness in it: an exported game (where `OS.has_feature("editor")` is false, as it is for a player), a launcher, or any X program. It gets a display of gdh's own, as a project does, and gdh sees and drives it from outside, as a person would, through the display's screen, pointer and keyboard.

```sh
gdh live start --binary build/game.x86_64 -- --level 3      # waits for its first window
gdh live wait --log 'menu ready'                             # a line of its log
gdh live shot                                                # the display's screen
gdh live input --click 640,360 --type pilot --key Return --shot
gdh live wait --exit                                         # how it ended
gdh live stop
```

- **Exports.** A program with a Godot pack, embedded or beside it as `<name>.pck` (where Godot looks for it), is an exported Godot game. gdh puts Godot's off-screen options first (`--display-driver x11 --audio-driver Dummy --resolution WxH`, and `--gpu-index` with `GDH_GPU_INDEX`), and the arguments after gdh's `--` after Godot's own `--`, where `OS.get_cmdline_user_args()` returns exactly them. Any other program gets the arguments as they are. `--raw` gives an export its arguments as they are too, with nothing added, for engine options of your own.
- **Starting.** `start` waits for the program's first window, up to `--timeout` seconds (default 60), and prints its size and title. If the program exits first, `start` fails with how it ended and the end of its log, and leaves nothing running. `--no-wait` returns at once, for a program that shows no window. `--keep-children`, the companion options, `--idle-timeout`, `--resolution`, `--display` and `--out` work as for a project. `--scene`, `--instances` and `--gpu-passes` need `--project`.
- **Its output** goes to `<out>/program.log`. The program runs under `stdbuf`, so the log is written a line at a time: a release export otherwise holds its prints until it exits. `<out>/exit.json` records how it ended.
- **Errors.** Each command prints the engine errors (`ERROR:`, `SCRIPT ERROR:`, `WARNING:`) the log gained since the command before, with repeats merged, and a `DEFECT:` line for resources that failed to load.

| Command | What it does |
|---|---|
| `shot [--label L] [--tiles]` | Saves the display's whole screen to `<out>/shots/NNNN-L.png`: what a player would see. It's read from the X server, not from the game's viewport, so the debug views (`--view`) aren't there |
| `input OPTION...` | Sends pointer and key input through the X server (XTest), in the order the options are given (below) |
| `wait --seconds S` | Waits S seconds. Fails if the program ends meanwhile |
| `wait --log REGEX` | Waits for a line of the log that matches REGEX (Python's `re.search`) and prints it. Each `--log` looks after the line the one before matched, so waiting for a line twice waits for it to come again. A line written just before the program ended is still found |
| `wait --window` | Waits for a window to show, and prints its size, place and title |
| `wait --exit` | Waits for the program to exit, and prints how it ended and the end of its log |
| `status` | Whether it runs and for how long, its windows and the processes it spawned; or how it ended (its exit code, or the signal that ended it and whether that was a crash) and the end of its log. `--json` for all of it |
| `stop` | Stops the program (SIGTERM, then SIGKILL), what it spawned, the companions and the display |

`wait --log`, `--window` and `--exit` give up after `--timeout` seconds (default 60), and exit 1 with the end of the log. The other `gdh live` commands (`step`, `tree`, `eval`, `probes`, `run`, `pause`, `pipe`, `record`, `measure`, `frames`) need the harness, and say so.

### Input

| Option | Effect |
|---|---|
| `--move X,Y` | Move the pointer to X,Y |
| `--click X,Y` | Left click at X,Y |
| `--double-click X,Y` | Double click at X,Y |
| `--right-click X,Y`, `--middle-click X,Y` | Right or middle click at X,Y |
| `--wheel DIR[:N]` | Turn the wheel N steps (default 1) where the pointer is: `up`, `down`, `left` or `right` |
| `--key KEY` | Press and let go of a key: an X keysym name (`Return`, `Escape`, `F5`, `a`, `Left`, `BackSpace`), Godot's name for it (`Enter`, `Space`, `PageUp`), or a chord (`ctrl+s`, `shift+Tab`) |
| `--hold KEY[:S]` | Hold a key or chord down for S seconds (default 0.5) |
| `--type TEXT` | Type TEXT into the window with the focus, a key a character, with Shift for a capital. A character the keyboard has no key for is put on a spare key while it's typed |
| `--pause S` | Wait S seconds before the next input |
| `--shot` | Save the screen `--settle` seconds (default 0.5) after the last input |

Positions are screenshot pixels, the screen's. Every option is checked before any input is sent, so a bad position or key name sends nothing. The displays have no window manager, so nothing gives a window the keyboard's focus, and Godot takes the focus when its window is clicked, which loses that click. So gdh gives the window under a click the focus first, and before keys the window that has it, else the one under the pointer, else the topmost, and lets the program take it in. One `--click` is one click.

### Kept off the desktop

A `--binary` session's program gets what a project's game gets: the display, the alert stand-ins, `user://` in gdh's own directory, the `GDH_MARK` that finds what it spawns, the watchdog, and the cleanup. A black box can do anything, so it's kept further off the user's session too:

- Its `XDG_RUNTIME_DIR` is an empty directory of the session's, so it finds no Wayland, D-Bus, PulseAudio or PipeWire socket of the user's, and its `DBUS_SESSION_BUS_ADDRESS` names a socket that isn't there.
- `xdg-open`, `kde-open`, `kde-open5` and `gnome-open` are stand-ins that write `gdh: xdg-open URL` to the log, so `OS.shell_open` and the like never open the user's browser.
- MangoHud and vkBasalt are turned off (`DISABLE_MANGOHUD=1`, `DISABLE_VKBASALT=1`): they draw into the program's frames, which the screen shows.
- Its core size limit is 0, so a crash leaves no core dump for a desktop's crash reporter to show the user.

### When it ends

The program runs under `src/gdh/runner.py`, its parent, which records how it ended, and holds a connection to the display while it runs (gdh's displays exit when their last client leaves). When the program exits, the watchdog stops its display, its companions and what it spawned, as for a project; with `--keep-children` once what it spawned has ended too, so a launcher's handed-off game can still be shot and clicked. The session stays until `stop`, so `status` and `wait --exit` can say how it ended; commands that need it running (`shot`, `input`, `wait --seconds`, `--log`, `--window`) fail saying how it ended, with the end of its log. There's no harness to keep the idle timeout, so the runner keeps it: after `--idle-timeout` seconds (default 1800) without a `gdh live` command on the session, it stops the program and notes so in the log, and `status` says so.

### `gdh export --smoke`

`gdh export --project DIR --preset Linux --smoke SECONDS` exports, then runs the build as a `--binary` session runs a program, on a display of gdh's (`--display`, `--resolution`), for SECONDS. It fails, exiting 1, if the build crashes or exits before then, or its log has engine errors (warnings don't count), and passes otherwise. It saves the screen at the end to `smoke.png`, beside `program.log` and `display.log`, in `--smoke-out` (default `./captures/smoke/<preset>`). Arguments after `--` go to the game. It needs a Linux preset. With `--pack`, the pack runs on gdh's Godot (`--main-pack`), which needs no export templates; that's the editor build, so `OS.has_feature("editor")` is true there, as it isn't in an export.

## Sessions

- **Several games at once:** `--session NAME` runs more than one game side by side. The default name is `default`.
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

This is how gdh talks to one instance. The commands are `status`, `step`, `shot`, `probes`, `tree`, `eval`, `frames`, `run`, `pause` and `quit`. `frames` takes `{"reset": bool, "clear": bool}` and returns `{"frames": [{"gpu": ms, "cpu": ms, "frame": n, "passes": {name: ms}, "groups": {name: ms}}, ...], "game_frames": n, "size": [w, h], "adapter": name}`. In `step`, an event with `"at": k` is injected before frame k+1 of the step. Event forms:
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
- a bad scene, the idle timeout and cleanup after a crash

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

`tests/test_binary.py` runs `testbed/binary/clicker.tscn`, a window that turns green when its button is clicked and prints what reaches it, as a build: Godot's release export template beside the clicker's pack, as `gdh export` writes a Linux build (skipped without export templates for this Godot version), and gdh's Godot running the pack. It checks:

- one click on the export changing what it draws, and its shot showing it, before and after; typed text, a key, a chord, the wheel and a right click reaching it; a bad position or key sending nothing
- `wait --log` returning the line, looking after the last match, timing out with exit 1, and finding a line written as the program quit; `wait --window`, `--seconds` and `--exit`
- an exit reported with its code and the end of the log (by `wait --exit`, `status` and a refused `shot`), a program killed by a signal, and nothing left running after `stop`
- the harness's commands refused, the program's environment (its display, no Wayland, runtime directory or D-Bus of the user's, the stand-ins first on `PATH`), and `OS.shell_open` going to the stand-in
- `--keep-children`: a launcher script that hands off to the clicker and exits, and the clicker clicked and shot after it has
- any X program (`xmessage`, when installed) answering a key with its exit code; a program that exits before its window failing `start`; the idle timeout
- `gdh export --smoke` passing a good build, and failing one that raises an engine error as it starts and one that exits early; a real export's when the templates are there

`tests/test_imports_and_size.py` checks the import cache's check and the window's size:

- a fresh copy of the testbed (no `.godot`) imported before its first capture, which then loads every texture, and not imported again on the second; a touched but unchanged texture not counted as stale, and a changed, a new or a deleted import counted
- resources that fail to load (their imported copies deleted, `--no-import`) printed as a `DEFECT:` and listed in `report.json`, and each way Godot words a failed load read as one
- `report.json`'s `window_size`, and a scene that resizes its own window failing `capture` and refused by `live start`, with no session left behind

`tests/test_display.py` checks the displays themselves:

- screenshots of a 3D scene in all six views, captured and at the same stepped frame live, the same pixel for pixel on both displays
- that the GPU display has DRI3 and runs Godot's X11 driver with V-Sync off, says so when a game turns V-Sync on, and leaves no process or runtime directory behind
- a window smaller than Xwayland's smallest screen (320x200)
- the watchdog stopping both instances' displays when one game is killed
- the fallback to Xvfb, with its note, when weston isn't installed, when weston has only a software renderer, and when it can't start at all; and `--display gpu` failing instead
- a bad `GDH_DISPLAY`, and the sweep of runtime directories left by killed runs

## The game's user data

A game run under gdh (capture or live) keeps `user://` (its saves, settings, logs and caches) in gdh's own directory, `~/.local/share/gdh/user-data` (under `$XDG_DATA_HOME` when set), never in the player's `~/.local/share/godot`. So a test run can't touch a player's saves or rotate out their logs. The directory is kept between runs, so shader caches stay warm. `GDH_USER_DATA=<dir>` picks another one, and `GDH_USER_DATA=real` uses the player's own. `gdh import` keeps the real one, where the editor's settings are.
