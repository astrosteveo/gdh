# Live control

`gdh live` starts a game off-screen and drives it one command at a time. The game is held between commands, so no game time passes while you, or an agent, look at a screenshot and decide what to do next.

```sh
gdh live start --project path/to/game            # main scene, held at frame 0
gdh live step 30 --hold ui_right --shot          # run 30 frames holding right, then save a frame
gdh live tree Player                             # inspect nodes
gdh live find Play                               # nodes that show "Play", with their boxes on screen
gdh live step 5 --click-text Play                # click it
gdh live shot --node UI/Inventory --zoom 2 --out inventory.png   # just that panel, twice the size
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
| `--click-text TEXT` | Left click the node that shows TEXT, at the centre of what shows of it (below) |
| `--click-node PATH` | Left click a node, at the centre of what shows of it (below) |

An INPUT can also be `mouse:left`, `mouse:right` or `mouse:middle`, pressed where the pointer is: `--move 300,200 --press mouse:right`, then steps with `--move` to drag or point, then `--release mouse:right`.

Each option can be repeated. Events at the end of a step (a `--hold`'s release) reach the game before it's held again, so a node that tracks keys by their events (`_input`, `_unhandled_input`) sees them go, not only `Input.is_action_pressed`.

## Finding and clicking nodes

`find` lists the nodes that show on screen, one a line, with each one's path, class, text and box in screenshot pixels:

```sh
$ gdh live find go
UI/GoButton (Button) text="Go" screen=[100.0, 100.0, 120.0, 50.0]
$ gdh live find --class Button --name "*Quit*"
```

- **What it matches:** `TEXT` is part of the text a node shows, in any case: a Label's, a Button's, a LineEdit's (its placeholder while it's empty), a RichTextLabel's without its BBCode, a Label3D's, translated as the node draws it. `--name PATTERN` is the node's name, where `*` and `?` match anything. `--class CLASS` is a class or one it extends (`--class BaseButton` finds every kind of button), built in or a script's `class_name`. Give any of them together.
- **Where it looks:** the current scene and the autoloads, with the nodes Godot makes inside its own controls, so a dialog's OK button is found as `UI/@AcceptDialog@8/@HBoxContainer@4/@Button@6`. A path outside the current scene starts with `/root/`.
- **What shows:** a node shows when it and everything above it is visible (CanvasItems, Node3Ds, CanvasLayers, windows), it isn't modulated to nothing, and some of it is on screen, inside every clipping Control above it (a scroll container's). The box is that part. A Control's box is its rect; a Sprite2D's, its rect; a 3D object's, its bounds seen through the camera; another 2D or 3D node is a point, `[x, y]`. A dialog's nodes are placed through the window they're in.
- **What doesn't:** up to ten nodes that match but don't show are listed after the rest as `not showing: PATH (CLASS) hidden`, or `transparent`, `off screen`, `not on screen`. A disabled button says `disabled`. With nothing that shows, `find` exits 1.

`step --click-text TEXT` and `--click-node PATH` click a node at the centre of the part of it that shows, worked out before the step's first frame: the pointer moves there, the left button goes down and comes up a frame later, as with `--click`. A path is from the current scene, or from the root (`/root/...`). A text picks the one node showing exactly that text, in any case, else the one whose text holds it. A text no node shows, or shows in several, a path with no node, and a node that doesn't show fail the step before it runs, naming what's there: the texts on screen, the nodes that match, or why the node doesn't show. The step says what it clicked (`clicked UI/GoButton (Button) text="Go" at 160,125`; `"aimed"` in `--json`).

`tree --visible-only` leaves hidden nodes, and everything under them, out of the tree.

## Framing shots

`shot` saves the whole frame as a numbered file by default. Its options write the image wanted, so it needs no cropping or scaling after:

| Option | Effect |
|---|---|
| `--out FILE.png` | Write this file (relative to where gdh runs) instead of a numbered one in `shots/`. With several `--view`s, the view's name is added: `--out a.png` writes `a-normal.png` and `a-wireframe.png` |
| `--crop X,Y,W,H` | Keep only this part of the screen, in screenshot pixels |
| `--node PATH [--margin PX]` | Keep only the box `find` gives a node, grown by PX on each side. A node that's a point needs a margin |
| `--zoom K` | Scale up K times (1 to 16), nearest neighbour, so each pixel stays a sharp square |
| `--max-width W` | Scale down to at most W pixels wide, after any zoom, for reading the whole frame; never up |
| `--no-ui` | Leave the UI out of this shot: every CanvasLayer drawn over the game (layer 1 and up, not following the viewport) is hidden for the shot and shown again after |

A shot that's cropped or scaled says which part of the screen it shows and at what size: `normal: a.png (the screen's 90,90 140x70, saved at 280x140)`, and `"crop"` and `"size"` in `--json`. A pixel at X,Y in the file is at `crop_x + X * crop_w / size_w`, `crop_y + Y * crop_h / size_h` on screen, which is where to click it.

## gdh's camera

`camera` looks through a camera of gdh's own, so a scene can be seen from anywhere without game code, as `gdh editor --view` does in the editor:

```sh
gdh live camera --view 0,40,80:0,0,0 --fov 60    # 3D: from the first point, looking at the second
gdh live camera --view=-12,3,6:0,1,0             # a negative first number needs the =
gdh live camera --view 640,360 --zoom 2          # 2D: centred on a point, twice as close
gdh live camera --release                        # the game's own camera again
```

In 3D it adds a Camera3D with the game camera's lens (its fov, near and far planes, cull mask, environment and attributes) unless `--fov` or `--far` says otherwise, and makes it current. In 2D it adds a Camera2D, centred on X,Y in world units at `--zoom` (2 is twice as close), which moves the view while the game is held. Each is an internal child of the root, so game code walking the tree doesn't see it, and it stays through steps until `--release`, which frees it and makes the camera it replaced current again. `tree`, `find`, clicks and shots all see through it. A game that makes its own camera current again takes the view back, and one that reads the current camera (to aim, say) gets gdh's while it's there.

## Coordinates

Every position `gdh` accepts or reports is in screenshot pixels: clicks, the `screen` field in `tree` and `find`, a shot's `--crop`, and probe `screen_rect`s. So you can click where a screenshot or `tree` shows something, whatever the project's stretch settings are. Tests cover stretch modes `viewport` and `canvas_items`.

`tree` also reports `pos`, which is the node's global position in world units.

## Seeing the game

| Command | Output |
|---|---|
| `shot [--view V]... [--label L] [--tiles]` | PNGs of the current frame, in any capture view; `--out`, `--crop`, `--node`, `--zoom`, `--max-width` and `--no-ui` frame them (above) |
| `step N --shot-every K` | A frame every K frames during the step, to catch flicker, popping and jitter |
| `step N --shot` | A frame after the step |
| `probes` | Probe findings on the current frame, with crops |
| `tree [PATH] [--depth N] [--visible-only]` | Nodes with class, script, world position, screen position (`[x, y]`, or `[x, y, w, h]` for a Control), text, value, velocity and animation |
| `find [TEXT] [--name P] [--class C]` | The nodes that show on screen and match, each with its box (above) |
| `camera --view ... \| --release` | Look through gdh's own camera, or give the game its view back (above) |
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
- **`shot`, `probes`, `tree`, `find`, `camera` and `eval` go to `--instance K`** (default 0), or to every instance with `--instance all`.
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

This is how gdh talks to one instance. The commands are `status`, `step`, `shot`, `probes`, `tree`, `find`, `camera`, `eval`, `frames`, `run`, `pause` and `quit`.

- `shot` takes `{"views": [...], "label": "shot", "out": FILE, "crop": [x, y, w, h], "node": PATH, "margin": PX, "zoom": K, "max_width": W, "no_ui": bool}`, all but `views` optional; a relative `out` is under the session's output directory. It returns `{"shots": {view: path}, "image_size": [w, h]}`, with `"crop"` and `"size"` when the shot is cropped or scaled.
- `tree` takes `{"path", "depth", "visible_only": bool}`.
- `find` takes `{"text", "name", "class"}`, at least one, and returns `{"matches": [{"path", "class", "text"?, "screen", "disabled"?}], "hidden": [{..., "why"}], "more": n}`.
- `camera` takes `{"from": [x, y, z], "at": [x, y, z], "fov", "far"}` (3D), `{"at": [x, y], "zoom"}` (2D) or `{"release": true}`.

`frames` takes `{"reset": bool, "clear": bool}` and returns `{"frames": [{"gpu": ms, "cpu": ms, "frame": n, "passes": {name: ms}, "groups": {name: ms}}, ...], "game_frames": n, "size": [w, h], "adapter": name}`. In `step`, an event with `"at": k` is injected before frame k+1 of the step. Event forms:
- `{action, pressed, strength}`
- `{key, pressed}`
- `{mouse_button, position, pressed}`
- `{mouse_motion: [x, y]}`

A mouse event with `"on": {"text": TEXT}` or `"on": {"node": PATH}` instead of a position goes to the centre of what shows of that node, as `--click-text` and `--click-node` do; the step's reply lists each target in `"aimed"`, and fails before stepping when a target can't be clicked.

## Tests

The tests need Godot, a GPU with Vulkan, Xvfb, and weston and Xwayland. Every test runs on both displays, the GPU display and Xvfb (the GPU display's are skipped without weston and Xwayland). `uv run pytest` runs `tests/test_live.py` against `testbed/live/arena.tscn`. It checks:

- exact frame counts and movement at 60 and 120 ticks per second
- that taps reach `_physics_process`, `_process` and `_input` once each
- held input across steps
- button clicks at the positions `tree` reports, with no stretching and in both stretch modes
- `find` by text, name and class, its one-line output, the nodes that don't show and why, and its box matching `tree`'s in both stretch modes
- `--click-text` and `--click-node` clicking the Go button (and over `pipe`), a dialog's OK button, and failing, without stepping, on a missing text, a hidden node, a missing path and an ambiguous text, with the candidates named
- `tree --visible-only`
- `shot --out` with one view and with several, `--crop` and `--zoom`, `--node` and `--margin` (in both stretch modes, and on a 3D object), `--max-width`, the two together, and bad framings, each image compared with the full frame pixel for pixel
- `shot --no-ui` leaving the UI out of that shot and the game in, and the next shot the same as the one before
- `camera` in 2D and 3D (`testbed/smoke/smoke.tscn`): the view moved, the boxes moved with it, and after `--release` the game's camera back and the frame the same as before, pixel for pixel
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
