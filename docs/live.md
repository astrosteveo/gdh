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
gdh live step --until "get_node('Ship').docked"  # run until it holds, at most 3600 frames
gdh live reload                                  # put the GDScript you changed into the running game
gdh live restart --replay                        # start again, back at this frame
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

Input is injected at the start of a step, where real input arrives. `_input`, `is_action_just_pressed` and `is_action_pressed` see it the same way they see a player's input. An input is an action name from the Input Map, `key:NAME` for a key (for example `key:Space`, or `key:ctrl+s` with its modifiers), or `joy:NAME` for a gamepad button (for example `joy:a`). Keys and gamepad buttons go through the Input Map like a player's.

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

| `--wheel DIR[:N]` | Turn the mouse wheel `up`, `down`, `left` or `right` N notches (default 1) where the pointer is, a notch a frame from the step's start. Each notch is a press and a release of the wheel's button, as a mouse sends. The step must be at least N frames |
| `--wheel-at X,Y` | Move the pointer to X,Y first, and turn the wheel there |
| `--mod MODS` | Hold `ctrl`, `shift`, `alt` or `meta` (comma-separated) for the whole step: their keys go down at its start, before the rest of its input, and up at its end, and every key, click, wheel notch and pointer move of the step carries them (`event.ctrl_pressed`) |
| `--axis NAME=VALUE` | Put a gamepad axis at VALUE, from -1 to 1, at the start of the step: `left_x`, `left_y`, `right_x`, `right_y`, `trigger_left` or `trigger_right`. It stays there, as a held stick does, until another `--axis` moves it (`--axis left_x=0` lets go) |
| `--touch X,Y` | Tap the touchscreen at X,Y: a finger down at the start of the step, up after one frame |
| `--touch-drag X,Y:X,Y` | Put a finger down at the first point at the start of the step, move it evenly to the second over the step (a drag event a frame), and lift it at the end |
| `--look DX,DY` | Move the mouse by DX,DY at the start of the step: relative motion, for mouse-look. A negative DX needs `=`: `--look=-40,0` |

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
- **What shows:** a node shows when it and everything above it is visible (CanvasItems, Node3Ds, CanvasLayers, windows), it isn't modulated to nothing, and some of it is on screen, inside every clipping Control above it (a scroll container's). The box is that part. A Control's box is its rect; a Sprite2D's, its rect; a 3D object's, its bounds seen through the camera; another 2D or 3D node is a point, `[x, y]`. A dialog's nodes are placed through the window they're in, and a SubViewport's (a game drawn small and scaled up, a 3D view in a menu) through the SubViewportContainer that shows it; a SubViewport shown some other way (a ViewportTexture) is `not on screen`.
- **What doesn't:** up to ten nodes that match but don't show are listed after the rest as `not showing: PATH (CLASS) hidden`, or `transparent`, `off screen`, `not on screen`. A disabled button says `disabled`. With nothing that shows, `find` exits 1.

`step --click-text TEXT` and `--click-node PATH` click a node at the centre of the part of it that shows, worked out before the step's first frame: the pointer moves there, the left button goes down and comes up a frame later, as with `--click`. A path is from the current scene, or from the root (`/root/...`). A text picks the one node showing exactly that text, in any case, else the one whose text holds it. A text no node shows, or shows in several, a path with no node, and a node that doesn't show fail the step before it runs, naming what's there: the texts on screen, the nodes that match, or why the node doesn't show. The step says what it clicked (`clicked UI/GoButton (Button) text="Go" at 160,125`; `"aimed"` in `--json`).

Before the first frame, the step also checks that the click would reach the node, as a browser test checks a button isn't covered. gdh sends the pointer there with the game held and asks Godot which control it's over (`gui_get_hovered_control()`, into a dialog and through a SubViewportContainer), the control a click there goes to. The click fails, without stepping, when something else would take it:

- a control drawn over the node that doesn't let the pointer through (a transparent panel or a menu left up, with mouse_filter Stop or Pass): `UI/GoButton (Button) can't be clicked at 160,125: UI/Fade (ColorRect) is over it and takes the click (mouse_filter Stop)`
- a control inside the node with mouse_filter Stop (an icon in a button), which keeps the click from it: `UI/GoButton/Icon (TextureRect) inside it takes the click first`
- a dialog or other window over it, or an exclusive window anywhere

A control inside the node with mouse_filter Pass passes the click up to it, and a node that lets clicks through (a Label, mouse_filter Ignore) is clicked through to whatever is under it. Both are fine, and the step names what took the click (`which passed it to ...`; `"took"` in the aimed entry). A node that isn't a Control (a Sprite2D, a 3D object) clicks into the world: a control with mouse_filter Stop where it shows is a note, not a failure, since the game's `_input` still gets the click and only `_unhandled_input` doesn't. `--click X,Y` checks nothing and says where the click went (`click at 160,125 went to UI/Fade (ColorRect, mouse_filter Stop)`; `"clicked": [{"at", "took"}]` in `--json`, with `took` null when no control takes it), so it clicks a covered node anyway. Sending the pointer there moves the controls' hover while the game is held, as the click would; after a failed click gdh moves it back.

`tree --visible-only` leaves hidden nodes, and everything under them, out of the tree.

## UI snapshots: `gdh live snapshot`

`gdh live snapshot` prints the UI on screen as an outline, as a browser test's accessibility snapshot does: what shows something to read or use, with its state, under the named nodes that group it.

```
$ gdh live snapshot
- Arena (Node2D)
  - UI (CanvasLayer)
    - GoButton (Button) "Go" [focused]
    - Stats (Label) "x 200.0  jumps 0  clicks 1"
    - Field (LineEdit) placeholder "Name"
    - (AcceptDialog) "Alert!"
      - (Label) "Saved."
      - (Button) "OK"
```

- **What's in it:** each node that shows (as `find` counts it) and has a text (a Label's, a Button's, a RichTextLabel's, a Label3D's), or can be used whatever its text (a button, a LineEdit or TextEdit, a slider, spin box or progress bar, a TabContainer, an ItemList). A window (a dialog) is a group of its own, with its title. A node that groups more than one of them is a line of its own, with what's under it indented; one that holds a single one is left out, and so are hidden nodes and everything under them, the parts Godot makes inside a field or a slider, and scroll bars. A name Godot made (`@Button@6`, a dialog's button) changes from run to run, so the line gives the class alone.
- **State:** `[disabled]`, `[pressed]` (a toggle button), `[checked]` (a CheckBox or CheckButton), `[read-only]`, `[focused]`; a range's `value/max`, a TabContainer's `tab "Name"`, an ItemList's count and selected items; a field's text, or its `placeholder` while it's empty.
- **Boxes:** `--boxes` adds each node's box on screen (`@x,y wxh`), and `--grid PX` rounds them to PX pixels (and adds them), so a shift of a pixel or two isn't a change.
- `snapshot PATH` keeps it to what's under one node; `--json` gives the nodes as the game sent them, with paths and boxes.

`--baseline FILE` compares the outline with a file and exits 1 with a unified diff on stderr when a line differs; `--update-baseline` writes the file. It checks what the UI says and its state without depending on pixels, so a baseline holds across GPUs. A scenario takes it as a step, `{"snapshot": NAME, "baseline": true}` ([scenarios.md](scenarios.md#ui-snapshots)).

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

- **Modifiers.** `--tap key:ctrl+s` presses Ctrl, then S carrying Ctrl, and lets them go in the opposite order, as a keyboard does, so an action bound to Ctrl+S fires and `Input.is_key_pressed(KEY_CTRL)` is true meanwhile. `--mod ctrl` holds Ctrl for the whole step instead: `--wheel up --mod ctrl` is Ctrl and the wheel, a map's zoom.
- **Gamepad.** `joy:NAME` is a button of gamepad 0, by Godot's name: `a`, `b`, `x`, `y`, `back`, `guide`, `start`, `left_stick`, `right_stick`, `left_shoulder`, `right_shoulder`, `dpad_up`, `dpad_down`, `dpad_left`, `dpad_right`, `misc1`, `paddle1` to `paddle4` or `touchpad`, or a number. Buttons and axes reach the Input Map as a connected pad's do, so `is_action_pressed` and `get_action_strength` (past the action's deadzone) see them. Godot's built-in `ui_*` actions take the D-pad and the left stick. No pad is connected, though: `Input.get_connected_joypads()` is empty.
- **Touch.** Each `--touch`, then each `--touch-drag`, of a step is a finger of its own, numbered from 0, so two `--touch-drag`s are a pinch or a two-finger swipe. They reach `_input` as `InputEventScreenTouch` and `InputEventScreenDrag`. With Godot's default `emulate_mouse_from_touch`, finger 0 also stands in for the mouse, so a tap presses a `Button`, and it moves the pointer.
- **The pointer.** gdh moves the display's own pointer wherever a step's input leaves it (`--move`, clicks, `--wheel-at`, `--look`, finger 0), so `get_mouse_position()`, `get_global_mouse_position()` and `DisplayServer.mouse_get_position()` say where it is. X answers each move with a motion event of its own; gdh takes it and drops it before the step's input goes in, so the game sees only gdh's. A step's input starts from where the pointer is, so if the game moves it itself (`Input.warp_mouse`), the next step starts from there.
- **Mouse-look.** `--look DX,DY` is a motion event carrying DX,DY as its `relative` and `screen_relative`, in screenshot pixels (the game's own units unless it stretches). With the mouse captured (`Input.MOUSE_MODE_CAPTURED`), the pointer stays at the window's centre, as X keeps it, and only the relative motion moves; otherwise the pointer moves by DX,DY.

## Coordinates

Every position `gdh` accepts or reports is in screenshot pixels: clicks, the `screen` field in `tree` and `find`, a shot's `--crop`, and probe `screen_rect`s. So you can click where a screenshot or `tree` shows something, whatever the project's stretch settings are. Tests cover stretch modes `viewport` and `canvas_items`.

`tree` also reports `pos`, which is the node's global position in world units.

## Seeing the game

| Command | Output |
|---|---|
| `shot [--view V]... [--label L] [--tiles]` | PNGs of the current frame, in any capture view; `--out`, `--crop`, `--node`, `--zoom`, `--max-width` and `--no-ui` frame them (above) |
| `step N --shot-every K` | A frame every K frames during the step, to catch flicker, popping and jitter |
| `step N --shot` | A frame after the step |
| `step N --trail PATH` | Where a node went over the step, drawn on its last frame with a dot every `--every K` frames, and the pixels between the dots ([motion.md](motion.md#trails)) |
| `onion N --node PATH` | A node's movement over N frames as an onion skin: the frames laid over each other, the oldest faintest ([motion.md](motion.md#onion-skins)) |
| `probes` | Probe findings on the current frame, with crops |
| `tree [PATH] [--depth N] [--visible-only]` | Nodes with class, script, world position, screen position (`[x, y]`, or `[x, y, w, h]` for a Control), text, value, velocity and animation |
| `find [TEXT] [--name P] [--class C]` | The nodes that show on screen and match, each with its box (above) |
| `camera --view ... \| --release` | Look through gdh's own camera, or give the game its view back (above) |
| `eval EXPR` | Any Godot expression. The base is the current scene. `scene`, `tree`, `root`, each autoload by name and the engine's singletons (`OS`, `Engine`, `Input`, `Time`, `RenderingServer` and the rest) are also available. |
| `status` | Game frame, held or running, scene, image and window size, nodes that run while held, processes the game spawned |
| `record N [--out DIR]` | N frames stepped and each saved as `DIR/frame-0000.png` on, for `gdh measure` |
| `measure KIND [--frames N]` | The same, measured: flicker, shimmer, jitter, black, crush or line ([measure.md](measure.md)) |
| `frames [--clear] [--reset] [--save FILE]` | Each game frame's GPU and CPU time since the record started over, and each render pass's with `start --gpu-passes`: the median, 99th percentile and worst |
| `bench N [--budget-median MS] [--budget-p99 MS]` | N frames stepped and timed in one call, after a second of held frames drawn back to back; exits 1 over a budget ([measure.md](measure.md#a-budget-gdh-live-bench)) |
| `monitors [--frames N] [--every K] [--leak]` | Godot's Performance monitors: objects, resources, nodes, orphan nodes, draw calls, video memory. `--leak` flags a count that grows steadily ([below](#performance-monitors)) |
| `step N --monitors` | The monitors before and after the step, and their change |
| `audio` | Each audio bus's peak level over the last step, and the players that played what ([below](#audio)) |

Screenshots go to `./captures/live/<session>/shots/`, or to `--out` if given. Their names start with a number that goes on from the highest already in the folder, so a session started again with the same `--out` never writes over earlier shots. An array in a reply (from `eval`, say) keeps its first 100 items and ends with a string saying how many more there were: `"... 150 more (250 in all)"`. Add `--json` to any command for the raw reply.

## Waiting and tracing

A step can run until something holds, and record values as it goes, so waiting for a scene, a login or a landing takes one command instead of a loop of `step` and `eval`.

```sh
gdh live step --until "scene.name == 'Hangar'"                       # check after every frame, stop when it holds
gdh live step --until "get_node('Ship').landed" --every 10 --max 1200 --hold ui_down
gdh live step 120 --trace "get_node('Ship').position.y" --trace "GameState.fuel" --every 5 --trace-out fuel.csv
gdh live step 90 --hold ui_accept --trace "get_node('Player').position" --trace-chart jump.png --trace-rates
```

- **`--until EXPR`** runs until EXPR is truthy, checked after every `--every K` frames (default 1). EXPR is a Godot Expression with the same inputs as `eval`. The step stops at the first check that holds, and the reply has the frames run and the value (`"until": {"expr", "met", "value", "frame", "checks"}`). The frame count, or `--max N`, is the most it runs, 3600 frames if neither is given. If EXPR never holds, the step ends there and gdh exits 1, saying what EXPR was at the last check. A check whose evaluation fails (a node that isn't there yet, a scene changing) counts as not yet, and the reply says why (`"error"`).
- **Input** works as in any step. When `--until` ends the step early, the inputs due at the step's end (a `--hold`'s release) go to the game then, so nothing is left held that the step would have let go. Inputs due later (the rest of a `--type`) don't.
- **`--trace EXPR`** (repeatable) records each EXPR's value at every check: `"trace": {"exprs", "every", "rows": [[frame, value, ...], ...]}`, where `frame` is the game frame. As text, a row that repeats the one before is left out, and at most 40 rows are shown; `--json` and `--trace-out FILE.csv` have every row. In the CSV a string is as it is, anything else is JSON, and a value whose evaluation failed is empty (`null` in JSON, with the reason in `"failed"` and a note).
- **`--trace-chart FILE.png`** draws the trace as a line chart, about 1200 px wide: a panel for each expression with its own vertical scale, all sharing the game-frame axis. A number is one line, a vector a line for each component (x, y, z), and true and false are 1 and 0, drawn as steps. A value that failed leaves a gap. A value of another kind (text, a dictionary) can't be drawn: its panel says so, and so does a note on stderr. Each check gets a dot when the checks are far enough apart to tell apart, so the dots' spacing shows easing: even for constant speed, closer together where it slows. **`--trace-rates`** adds two panels under each expression's: its rate of change per second (velocity, for a position) and the rate of that (acceleration), from the differences between checks at the game's tick rate. A chart is easier to read than rows of numbers for smoothness, overshoot, a camera lagging its target or a value that jumps. `gdh measure chart FILE.csv --out FILE.png` draws a CSV that `--trace-out` wrote, with `--only EXPR` (repeatable) to chart some of its columns, `--rates`, `--ticks N` (the tick rate for the rates, 60 by default) and `--title`.
- With several instances, `--until` is refused, since the instances step together and each would stop on its own. `--trace` records every instance's values, and `--trace-out` and `--trace-chart` write a file for each, `.0.csv`, `.1.csv` and so on.

## Problems and the game's output

Results go to stdout. Everything about what went wrong goes to stderr, so a command whose stdout is thrown away (`>/dev/null`, or read by a script) still shows it:

- **Engine errors** raised since the previous reply, with repeats merged. An error raised from a script (a `push_error`, a call on a null instance, an engine error the script's call raised) has the script's backtrace under it, innermost call first: `  at res://player.gd:42 in _physics_process`. gdh's own frames are left out.
- **`DEFECT:` lines** for resources that failed to load among them.
- **Notes** (the game unpaused itself, turned V-Sync on, resized its window).
- **What the game printed** (`print`, `printerr`) since the previous reply, each line as `game: LINE`. A line printed again at once is merged into one with a count, `again (x50)`. Past 40 lines, the first 10 and the last 30 are kept, with a line saying how many were cut. What the game prints while it loads is shown by `start`.

With `--json` all of it is in the reply instead: `"errors"` (each with `"backtrace"` when it has one), `"notes"`, `"output"` and `"output_cut"`.

**`--strict`**, on any command, or `GDH_STRICT=1` in the environment, makes a command exit 1 when the game raised engine errors during it (warnings don't count), after printing its results as usual. In a `batch`, each command is judged on its own.

## Performance monitors

`monitors` reads Godot's Performance monitors: the counts of objects, resources, nodes and orphan nodes (nodes out of the tree that nothing has freed), what the last frame drew (draw calls, objects, primitives), the render pipelines compiled so far, video memory (textures and buffers), static memory, and the active 2D and 3D physics bodies. A game's own monitors (`Performance.add_custom_monitor`) come too, as `custom/<id>`. Memory is in bytes in `--json`. Godot's process times aren't among them: it updates them once a second, with the slowest frame's.

- **`monitors`** reads them once, now.
- **`monitors --frames N`** steps N frames, every instance as `step` does, and reads them before the first frame and every K frames (`--every K`; about 60 readings by default). It prints each monitor at the start and the end, its change, and its least and most. `--hold`, `--press`, `--release` and `--move` go in as `step`'s do.
- **`monitors --leak`** (600 frames unless `--frames`) also looks for a leak after a warm-up, a third of the frames by default (`--warmup FRAMES`). The readings after it are cut into quarters, and a count grew steadily when each quarter's median is above the one before and the last quarter's least is above the first quarter's most. It looks at objects, resources, nodes, orphan nodes, video, texture and buffer memory, and the game's own monitors, says which grew and by how much a frame, and exits 1 if any did. A count that churns (bullets made and freed) doesn't grow steadily, and neither does one that jumps once and stays (a level part loaded). Static memory isn't judged, since gdh's own frame record grows it.
- **`step N --monitors`** reads them before and after the step, and prints the change.

## Audio

gdh runs Godot with the Dummy audio driver, which mixes the game's audio as a sound card's driver does, on a thread of its own in real time, and sends it nowhere: the buses' levels are real, and nothing reaches the speakers. `audio` reports, for the last step (or since `run`):

- **Each bus:** its peak level over the step (the loudest of its channels), its volume, whether it's muted and the bus it sends to. `not measured` means no audio was mixed during the step, and `silent` that the bus was.
- **The players that played:** every `AudioStreamPlayer`, `AudioStreamPlayer2D` and `AudioStreamPlayer3D` that played in the step, with its stream (the resource's path), its bus, its volume, where it is in the stream and its length, whether it was still playing at the end, and, for a 2D or 3D player, how far it is from the listener (the viewport's listener, else its camera, else for 2D the screen's centre) and how far it can be heard. A player freed during the step is listed as it was.
- **The players that didn't play**, by name.

The driver mixes a block of 4096 samples at a time (93 ms at 44.1 kHz) by the clock on the wall, not game time. A step that runs faster than real time mixes less sound than the game time it covers: 120 frames, two seconds of game time, can run in 80 ms and mix one block, so a player's position reads about a tenth of a second in. A step shorter than a block may mix none, and then the levels aren't measured; step longer to read them. Held, the game's players pause with it, so the levels are taken only from blocks mixed while the game ran.

## A debug autoload

`eval` reaches whatever the game exposes, so a game that is more than a scene or two is quicker to check with an autoload made for it: a node whose methods return the state you check, as dictionaries and arrays, and take the game straight to a state. `eval` and `--until` call it, a `--recipe` starts from it, and a scenario's checks read it. It's also the only way `eval` sees C# objects.

```gdscript
# debug.gd, the autoload "Debug"
extends Node

func state() -> Dictionary:
	var player := get_tree().get_first_node_in_group("player")
	return {"scene": get_tree().current_scene.name, "position": player.global_position, "health": player.health}

func goto(where: String) -> void:
	get_tree().change_scene_to_file({"hangar": "res://levels/hangar.tscn"}[where])
```

```sh
gdh live eval "Debug.state()" --session s
gdh live eval "Debug.goto('hangar')" --session s
gdh live step --until "Debug.state().scene == 'Hangar'" --max 600 --session s
```

Keep it to reading state and jumping to it; what it skips (a login, a tutorial) still needs a playthrough of its own now and then.

## Locales

`start --locale CODE` translates the game's text to that locale, and `--locale pseudo` turns on Godot's pseudolocalization (every text 40% longer, with accents), so `probes` and `snapshot` show the text that won't fit ([probes.md](probes.md#text-that-wont-fit---locale)).

## The timeline: `--timeline`

`gdh live start --timeline` keeps a record of the session in `<out>/timeline/`, as Playwright's trace viewer does for a browser test: `index.html` lists every command sent to the game, in order, with what it sent, the frames before and after it, how long it took, the engine errors (with their backtraces), notes and game output that came back, what a click went to, an eval's value, links to the shots it saved, and a thumbnail of the frame after each `step`, `run`, `pause`, `camera` and `reload` (320 pixels wide; click it for the full thumbnail). A checkbox shows only the commands that failed or raised errors or notes. It keeps the commands from every way in: commands, `batch`, `pipe`, a recipe and a replay; `status` is left out.

The page opens straight from the disk, with no server. It reads `timeline.js` beside it, one line a command (`T({...});`), written as the session goes, so it's current at any time, and survives a session that crashed. `start` and `stop` print its path. `restart` keeps the option and adds to the same timeline: the old game's stop, then a mark where the game started again; a new `start` begins it afresh. Each thumbnail is one more small shot after the command, a few milliseconds; a session without `--timeline` does nothing more. A `--binary` session has no timeline.

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

## A worse network: `--net`

`--net NAME` puts companion NAME behind a network proxy of gdh's, so a test can give the game latency, lost packets and a link that goes down, as browser tests throttle and cut the network:

```sh
gdh live start --project game --instances 2 --net server --net-latency 80 \
  --companion 'server=exec ./server --port {port}' -- --connect 127.0.0.1:{server.port}
gdh live net --cut --instance 1          # instance 1's link goes down
gdh live run; sleep 5; gdh live pause    # the game notices, in real time
gdh live net --heal --instance 1
gdh live net --reset --instance 0        # close instance 0's TCP connections now: does it reconnect?
gdh live net                             # each link's settings, and what went through it
```

- **The links:** the companion keeps its port. Each game instance gets a port of its own on the proxy, which `{NAME.port}` names in its arguments, and the proxy passes what comes in, TCP and UDP alike (ENet, WebSockets, a game's own protocol), on to the companion and back. Only the instances' links go through it: the companion's own command still gets its real `{port}`.
- **What it does:** `--latency MS` delays everything, each way; `--jitter MS` adds up to that much more to each packet, drawn at random, so UDP datagrams can arrive out of order; `--loss PCT` drops that share of UDP datagrams each way. TCP loses nothing (it resends: a game sees only delay), and its data stays in order. `--cut` is a link that's gone: UDP is dropped, and TCP data is held, without closing anything, until `--heal`, as a network that goes down and comes back does (a game that times out first closes the connection itself). `--reset` closes the link's TCP connections at once, with a reset, as a server that drops its clients does.
- **Which link:** `gdh live net` changes every link, or one instance's with `--instance K`, or one companion's with `--companion NAME`. `--net-latency`, `--net-jitter` and `--net-loss` on `start` set every link from the start. With no change, `net` prints each link and what went through it: TCP connections and bytes each way, UDP datagrams each way and those dropped (`--json` for all of it).
- **Time:** the delays are in real time, as a network's are. A held game doesn't read its sockets, and `step` runs frames as fast as the GPU goes, so let the game run in real time (`gdh live run`, then `pause`) or wait on what it measured (`step --until`, in a session of one instance). The random draws repeat with the session's seed: the same datagrams are dropped, in a game that sends the same ones.
- The proxy is a process of the session's (its output is `<out>/net.log`), and stops with it.

## Several instances

`--instances N` runs N instances of the game in one session, each on a display of its own, with its own output in `<out>/instance-K/`. `{instance}` in the game's arguments is each one's number, so they can be told apart (`-- --user pilot{instance}`).

- **`step`, `run` and `pause` go to every instance at once**, so the instances step together: when each frame of one waits on another (two clients of a server on a test clock that ticks once every client has asked), they keep in step. The input of a step goes to `--instance K` (default 0), or to every instance with `--instance all`.
- **`shot`, `probes`, `tree`, `find`, `camera` and `eval` go to `--instance K`** (default 0), or to every instance with `--instance all`.
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

- A line is a `gdh live` command, with or without `gdh live` before it, split as a shell splits it. `#` comments and blank lines are skipped. Every command but `start`, `restart`, `batch` and `pipe` can be on a line. Each line runs on the batch's `--session`, unless it gives its own.
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

From Python, `gdh.client` keeps one pipe open and wraps it: `Session.start(...)` or `Session.attach(name)`, then `step`, `until`, `eval`, `click`, `find`, `shot` and `stop`, with input given as `step`'s options by name ([scenarios.md](scenarios.md#the-python-client)).

## Scenarios: `gdh live save-scenario`

`gdh live save-scenario --session NAME FILE` writes the session's start options (with its seed) and its input log so far (below) as a scenario file. Add checks to it, and `gdh scenario run FILE` replays it in a session of its own, frame for frame, and says which checks passed ([scenarios.md](scenarios.md)). `--force` writes over FILE.

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
- **Listing them:** `gdh live list` shows every session of yours, whoever started it: running or ended, its pids and displays, the project and the scene it started in, how long its game has run and how long since a command on it (`--json` for a list). It only reads the session files, so it doesn't keep a game from its idle timeout, and a session whose game has ended stays listed until a command on it cleans it up.
- **Options in one argument:** zsh doesn't split an unquoted variable, so `S="--session x --json"; gdh live step 10 $S` passes `--session x --json` as one argument. gdh says so in one line and exits 2. Use `${=S}` or an array.
- **Session files:** each is kept in `$XDG_RUNTIME_DIR/gdh/` and can be read only by you. It holds the port and a random token that every request must carry. The game gets the token through its environment, which other users can't read. Keeping it off the command line keeps it out of the process list.
- **Local only:** the game listens on 127.0.0.1.
- **Idle timeout:** a game quits after 30 minutes without a request, which ends the session. Change this with `--idle-timeout SECONDS`, or set 0 to turn it off. With `--keep-children`, the game's quitting doesn't end the session, so once every game has exited the watchdog keeps the timeout instead: when no `gdh live` command has been run on the session for that long, counted from the later of the last command and the game's exit, it stops the processes the game spawned, then the display, and notes why at the end of the game's `godot.log`. Every `gdh live` command on the session counts, `status` included, and so do ones that fail because the game has exited; `start` and `stop` don't.
- **Stopping:** `stop` asks the game to quit, then stops Godot and its display if they're still running, and removes the display's runtime directory. The display's own output is in `<out>/display.log`.
- **Crashed games:** if a game dies, the next command says so, shows the end of its log, stops the session's other processes (and those the game spawned) and removes the session; with `--keep-children` that waits until what the game spawned has ended (above).

## Starting again: restart, replays and recipes

Starting a game takes seconds, and getting it back to where it was (logging in, flying to the hangar) takes more. A session can start again where it was, or where a recipe puts it.

```sh
gdh live start --project game --seed 42 --recipe recipes/hangar.txt   # held at frame 0, then the recipe's lines run
gdh live restart                     # after a code change: started again as it was started, the recipe run again
gdh live restart --replay            # started again, and the session's input replayed: the same frame and state
gdh live start --project game --replay captures/live/default/inputs.jsonl   # a saved log, in a new session
```

- **The input log.** Every session writes `<out>/inputs.jsonl`: a header line, `{"gdh_input_log": 1, "session", "project", "scene", "seed", "started"}`, then a line for each command that changed the game, `{"cmd", "args", "instance", "frame", "ran"}`. `args` are the protocol's (below), `frame` is the game's frame after the command, and `ran` the frames a step ran. Steps, `eval` (which can set values), `camera`, `run` and `pause` are logged, however they came: a command, `batch`, `pipe`, a recipe or a replay. A command that failed isn't.
- **`restart`** stops the session if it runs, and starts it again with what it was started with: the project, scene, resolution, display, instances, companions (on new ports, filled into the game's arguments again), the game's arguments, its seed, `--user-data`, `--replay` and `--recipe`, from the directory it was started in. It rebuilds a C# project and imports the project again only when they're stale (below). It works on a session that has ended too: gdh keeps what a session was started with in `$XDG_RUNTIME_DIR/gdh/starts/`, for a week after its last start. The new start writes a new log, and the old one stays beside it as `inputs-before-restart.jsonl`.
- **`restart --replay`** replays the session's input log once the game is ready, and doesn't run its `--replay` log or recipe again, since the log holds what they sent. `start --replay FILE` replays a saved log, and takes its seed unless `--seed` gives one. A replay sends each command again in order: each step for the frames it ran, without its `--until`, `--trace` and saved frames, and the frames the game ran by itself after `run` as a step of their own, so the game comes back to the same frame. It prints `replayed N requests from LOG in S s: held at frame F`. A command that fails now (a `--click-text` whose button the new code moved off screen) stops the replay, and the start exits 1 saying which; the session stays, held there.
- **The same frame is the same state** when the game does the same with the same input: physics at a fixed tick does, and the global random number generator does with the same seed. What a game reads from the clock, the network, or a `RandomNumberGenerator` it seeds itself, and anything that ran while the game was held, can differ: a node whose process mode ignores the pause, or code that awaits the tree's `process_frame` or `physics_frame` signals, which fire while it's held.
- **`--seed N`** seeds the game's global random number generator (`randi`, `randf`, `randf_range`, `shuffle` and the rest) before the autoloads' `_ready` and the scene's, so its random choices repeat from one start to the next. Without it gdh picks a seed, so the game is as random as ever, and the log and `restart` keep it. A game that calls `randomize()`, or makes its own `RandomNumberGenerator`, isn't covered.
- **`--recipe FILE`** runs command lines once the game is ready, after `--replay`, as `gdh live batch` runs them: one `gdh live` command a line, `#` comments, each printed after a `> LINE` header. Keep a project's "get to the hangar" in one. Each line is checked before the game starts, so a misspelt command fails at once. A line that fails stops the start with exit 1, naming the line and why; the lines after it don't run, and the session stays, held where it stopped. `restart` runs the recipe again, read afresh.

### The session's own user data

By default every session shares gdh's user data (below), so a game's saves and settings carry from one session to the next. A session can have `user://` of its own instead, made afresh at each start and restart, so every run begins the same:

- **`--user-data fresh`**: an empty `user://`.
- **`--user-data-from DIR`**: a copy of DIR as `user://`, such as a folder holding a save and a settings file. DIR itself is never written to.

It is `<out>/user-data` (one for each instance, under `<out>/instance-K/`), which is Godot's data directory, so `user://` is `<out>/user-data/godot/app_userdata/<project name>/`, or the project's custom user directory under it. Its `shader_cache` and `vulkan` directories link to the shared user data's, so shaders compiled once stay compiled. gdh deletes `<out>/user-data` at each start of such a session.

### C# builds

Before it starts a C# project, gdh builds it with `dotnet build` only when the build is stale: when a `.cs`, `.csproj`, `.sln`, `.props` or `.targets` file has changed (its size or modification time) since gdh last built it, or the build's output (`.godot/mono/temp/bin`) is gone. It looks in the project's folder and in the folder of every project its `.csproj` references (`<ProjectReference>`, followed through their own references, so code shared with a server counts), at the files the `.csproj` files import or compile by a path outside those folders, and at the `Directory.Build.props`, `global.json` and the like in the folders above each, leaving out `bin`, `obj` and hidden folders. A reference it can't follow (a project that isn't there, a path made of MSBuild properties) makes it build every time. The stamp of what it last built from is `.godot/mono/temp/gdh-build-stamp`, written after each build that succeeds. So there's no need to run `dotnet build` first, and an unchanged project starts without it. `start --rebuild` and `restart --rebuild` build anyway; `--no-build` never builds. Every gdh command that builds (`capture`, `import`, `movie`, `test`, `editor`, `export`) uses the same stamp.

## Reloading scripts: `gdh live reload`

`gdh live reload` puts the GDScript files that changed on disk into the running game, which keeps its state: the player stays where it is, and every member variable keeps its value.

```sh
gdh live reload                           # every loaded script whose file changed
gdh live reload res://player/player.gd    # just these
```

Each changed script is compiled again in place, as the editor's Debug > "Synchronize Script Changes" does for a game it runs (`Script.reload(true)`), and the loaded scripts that extend it after it, so they see its new members. It goes to every instance (`--instance` picks one). It prints `reloaded res://...` for each, and exits 1 if one doesn't compile. A script is loaded when a node in the tree or an autoload has it, it's one they extend or preload as a constant, or it's a global class (`class_name`) that has been loaded; a script only loaded some other way can be named.

- Member variables keep their values, and so do static variables. Functions, and methods and lambdas connected to signals, run the new code from the next frame.
- A member variable a change adds starts at its initial value in the nodes in the tree, taken from a fresh instance of the script (unless its `_init` needs arguments); `@onready` ones, and those of objects outside the tree, start `null`. The reply says which were added.
- A function waiting at an `await` in a reloaded script is cancelled, and Godot warns: `Canceling suspended execution of "..." due to a script reload`. A coroutine that loops forever (a state machine's) stops there.
- A script that doesn't compile is put back to the version that ran, so the game never runs without it, and its parse error is on stderr. Fix it and reload again.
- Scenes, resources and C# code aren't reloaded, and what `_ready` and the initializers have already done stays done: `restart --replay` starts the game again with them, back at the same frame.

The spike behind it ran each of these in a game held between steps: a function changed, a member added (with an initializer), a static variable, a `class_name` script, a base script under a derived one, a lambda connected to a signal, a timer's method, a pending `await`, and a parse error.

## Protocol

One JSON object per line over TCP.

```json
{"id": 1, "token": "…", "cmd": "step", "args": {"frames": 30, "events": [{"action": "ui_right", "pressed": true, "at": 0}]}}
{"id": 1, "ok": true, "result": {…}, "errors": […], "frame": 30, "held": true}
```

This is how gdh talks to one instance. The commands are `status`, `step`, `shot`, `probes`, `tree`, `find`, `camera`, `eval`, `frames`, `monitors`, `audio`, `reload`, `run`, `pause` and `quit`. `step` takes `frames`, `events` and `shot_every`, and `until` (an expression), `trace` (a list of expressions) and `every` (see "Waiting and tracing"). Every reply has `"errors"`, each `{type, message, where, count}` with a `backtrace` for one raised from a script, and, when the game printed anything since the previous reply, `"output"` and `"output_cut"`.

- `shot` takes `{"views": [...], "label": "shot", "out": FILE, "crop": [x, y, w, h], "node": PATH, "margin": PX, "zoom": K, "max_width": W, "no_ui": bool}`, all but `views` optional; a relative `out` is under the session's output directory. It returns `{"shots": {view: path}, "image_size": [w, h]}`, with `"crop"` and `"size"` when the shot is cropped or scaled.
- `tree` takes `{"path", "depth", "visible_only": bool}`.
- `find` takes `{"text", "name", "class"}`, at least one, and returns `{"matches": [{"path", "class", "text"?, "screen", "disabled"?}], "hidden": [{..., "why"}], "more": n}`.
- `snapshot` takes `{"path"}` (optional) and returns `{"nodes": [{"name", "class", "path", "children", "text"?, "placeholder"?, "states"?, "value"?, "box"?}]}`, the outline `gdh live snapshot` prints.
- `camera` takes `{"from": [x, y, z], "at": [x, y, z], "fov", "far"}` (3D), `{"at": [x, y], "zoom"}` (2D) or `{"release": true}`.
- `reload` takes `{"paths": [...]}` (empty: every loaded script whose file changed) and returns `{"reloaded": [path], "failed": [{"path", "error"}], "unchanged": n, "filled": {path: [member]}}`.

`frames` takes `{"reset": bool, "clear": bool, "others": [...]}` and returns `{"frames": [{"gpu": ms, "cpu": ms, "frame": n, "passes": {name: ms}, "groups": {name: ms}}, ...], "game_frames": n, "size": [w, h], "adapter": name, "others_at_start": [...]}`; `others`, the other games gdh found as the record starts over, is kept with the record. `monitors` returns one reading, `{"frame": n, "objects": n, ...}`, and `audio` what `gdh live audio --json` prints. In `step`, an event with `"at": k` is injected before frame k+1 of the step. `step` also takes `"warmup": seconds` (held frames drawn back to back first), `"clear_record": true` (the frame record started over just before the first frame) and `"monitors": K` (readings before the first frame, every K frames, and after the last, in the result's `"monitors"`; 0 for just the first and last). Event forms:
- `{action, pressed, strength}`
- `{key, pressed, mods}`: `key` is a key's name, or one with its modifiers (`"ctrl+s"`)
- `{mouse_button, position, pressed, mods}`: buttons 1 to 3 are left, right and middle, and 4 to 7 the wheel up, down, left and right (a notch is a press and a release)
- `{mouse_motion: [x, y], mods}`
- `{look: [dx, dy], mods}`: relative motion
- `{joy_button, pressed, device}` and `{joy_axis, value, device}`: Godot's `JoyButton` and `JoyAxis` numbers, on gamepad `device` (default 0)
- `{touch: [x, y], index, pressed}` and `{touch_drag: [x, y], index}`: finger `index` down, up, or moved

`mods` lists the modifiers held (`ctrl`, `shift`, `alt`, `meta`) and sets the event's flags. It doesn't press their keys, which are key events of their own (`gdh live step` sends them around the rest). Send events in time order: the bridge follows the pointer, its buttons and the fingers event by event.

A mouse event with `"on": {"text": TEXT}` or `"on": {"node": PATH}` instead of a position goes to the centre of what shows of that node, as `--click-text` and `--click-node` do; the step's reply lists each target in `"aimed"` (with `"took"`, `{path, class, mouse_filter}`, when a control other than the target takes the click), and fails before stepping when a target can't be clicked or another control would take the click ([Finding and clicking nodes](#finding-and-clicking-nodes)). A step whose first frame starts with a press at a position reports what takes each one in `"clicked"`.

## Tests

The tests need Godot, a GPU with Vulkan, Xvfb, and weston and Xwayland. Every test runs on both displays, the GPU display and Xvfb (the GPU display's are skipped without weston and Xwayland). `uv run pytest` runs `tests/test_live.py` against `testbed/live/arena.tscn`. It checks:

- exact frame counts and movement at 60 and 120 ticks per second
- that taps reach `_physics_process`, `_process` and `_input` once each
- held input across steps
- button clicks at the positions `tree` reports, with no stretching and in both stretch modes
- `find` by text, name and class, its one-line output, the nodes that don't show and why, and its box matching `tree`'s in both stretch modes
- `--net`: two instances of `testbed/live/net.tscn` reaching an echo server (`testbed/live/echo.py`) over UDP and TCP through ports of their own on the proxy; latency on one instance's link doubling into its round trips and not the other's; a cut link passing nothing until healed while the other talks on; a reset making the game connect again; the same seed dropping the same datagrams (`tests/test_net.py`)
- `snapshot`: the arena's UI outline with a clicked button focused, a hidden menu left out, boxes on a 10-pixel grid, a dialog as a group with its title and the names Godot made left out; a baseline written, the same, then failing with the diff when the button was disabled; and a scenario's snapshot baseline passing, then failing with its diff (`tests/test_scenarios.py`)
- `start --timeline`: a step, a click, an eval, a failed eval and a `batch`'s step and shot in `timeline/timeline.js` with their frames, values, error and thumbnails, `status` left out, the shot linked, a restart added to it and a new start beginning it again (`tests/test_timeline.py`)
- a click on a button under a transparent panel failing without stepping and naming the panel, `--click` reporting the panel took it, and a panel with mouse_filter Ignore letting the click through; a control inside a button passing the click up with mouse_filter Pass and failing it with Stop; a click on a Node2D noting the control that stops it
- `--click-text` and `--click-node` clicking the Go button (and over `pipe`), a dialog's OK button, a button in a SubViewport shown at 2x, and failing, without stepping, on a missing text, a hidden node, a missing path and an ambiguous text, with the candidates named
- `tree --visible-only`
- `shot --out` with one view and with several, `--crop` and `--zoom`, `--node` and `--margin` (in both stretch modes, and on a 3D object), `--max-width`, the two together, and bad framings, each image compared with the full frame pixel for pixel
- `shot --no-ui` leaving the UI out of that shot and the game in, and the next shot the same as the one before
- `camera` in 2D and 3D (`testbed/smoke/smoke.tscn`): the view moved, the boxes moved with it, and after `--release` the game's camera back and the frame the same as before, pixel for pixel
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

`tests/test_restart.py` runs `testbed/restart/walker.tscn`, which draws random numbers as it starts and moves its player by input and a random step every frame. It checks:

- `restart` with the same resolution, seed and game arguments, its companion started again on a new port, from another directory, and after `stop`
- `restart --replay` back at the same frame with the same positions, clicks and values, after steps with `--hold`, `--click-text` and `--until`, an `eval` that set a value, `run` and `pause`, `pipe` and `batch`; the log's lines; the steps as a scenario holds them; and `start --replay` of the saved log in a new session
- `--seed` repeating `randi()` and the positions across starts, another seed drawing others, and the seed gdh picks kept by `restart`
- `--recipe` running after start and again on `restart`, stopping with exit 1 at a failing line with the session held there, and a line that isn't a command failing before the game starts
- `--user-data fresh` and `--user-data-from` giving the session its own `user://`, made afresh by `restart`, with the shader caches linked and the fixture and the shared user data untouched; and where Godot puts `user://`
- `reload` putting a changed script in with the state kept and a new member at its initial value, and a script that doesn't compile leaving the one that ran

`tests/test_csharp.py` also checks that a start of an unchanged C# project doesn't run `dotnet build`, a changed `.cs` builds and runs the new code, and `--rebuild` builds anyway; that a change in a project the `.csproj` references from outside the Godot folder builds again; and that the stamp follows references (written with `\` or `/`, one reached twice), leaves out `obj`, counts the build files above a referenced folder, and gives up on a property in a path or a missing project.

`tests/test_input.py` runs `testbed/input/devices.tscn`, which records each wheel notch, key, touch and drag its `_input` sees. It checks:

- the wheel scrolling a `ScrollContainer` a notch a frame, and Ctrl and the wheel zooming a map that the wheel alone doesn't
- `key:ctrl+s` reaching an action bound to Ctrl+S, which S alone doesn't, and `--mod` keys pressed around the rest
- gamepad buttons and a stick reaching Godot's `ui_*` actions, with the stick's strength past its deadzone, until it's moved back
- a tap pressing a `Button` through touch's mouse emulation, and two fingers dragging at once
- the display's pointer where `--move`, a tap and `--look` leave it, with X's own motion event for each move never reaching the game, and `--look` turning a captured mouse
- bad input refused, saying why

`tests/test_perf.py` runs `testbed/perf/perf.tscn`, whose modes (`-- --mode NAME`) churn nodes but keep their count flat, leak a node into the tree each frame, leak an orphan node each frame, or play a beep over and over on three kinds of player and two buses. It checks:

- `bench`'s summary and its exit status under a budget and over one, with no game time passing in its warm-up
- the warnings: a session started with `--gpu-passes`, another game running during `bench`, and one running when the record started though it has ended since
- the monitors read once, over a step and with `step --monitors`; `--leak` flagging the leaking modes and passing the churning one, and the leak rule on made-up readings
- `audio`: the three players playing the beep, the one that didn't, and a non-silent peak on both buses

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

A game run under gdh (capture or live) keeps `user://` (its saves, settings, logs and caches) in gdh's own directory, `~/.local/share/gdh/user-data` (under `$XDG_DATA_HOME` when set), never in the player's `~/.local/share/godot`. So a test run can't touch a player's saves or rotate out their logs. The directory is kept between runs, so shader caches stay warm. `GDH_USER_DATA=<dir>` picks another one, and `GDH_USER_DATA=real` uses the player's own. `gdh import` keeps the real one, where the editor's settings are. `live start --user-data fresh` and `--user-data-from DIR` give one session `user://` of its own ("The session's own user data", above).
