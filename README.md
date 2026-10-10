# gdh

Renders Godot scenes off-screen on the real GPU and saves screenshots, debug views, engine errors and checks for likely defects. An AI agent or a script can then inspect what the game draws.

It also lets an agent change a project without fighting the Godot editor: a live link into the editor (`gdh bridge`) for UIDs, script checks and edits the user can undo, plus `gdh api` for the installed Godot's class reference, `gdh test` for a project's tests and `gdh export` for builds.

Linux only for now.

## Requirements

- Linux
- Godot 4.7 (tested with 4.7.2)
- A Vulkan driver for your GPU
- weston and Xwayland, for the display the GPU presents to (Arch: `weston xorg-xwayland`, Debian/Ubuntu: `weston xwayland`). Tested with weston 15 and Xwayland 24.1. Older releases may not have what gdh uses (rootful Xwayland, weston's `--renderer=gl` on its headless backend), and then gdh falls back to Xvfb.
- Xvfb, the fallback when that display can't start (Arch: `xorg-server-xvfb`, Debian/Ubuntu: `xvfb`)
- [uv](https://docs.astral.sh/uv/) (it provides Python 3.10+, Pillow and NumPy)
- For a C# project: Godot's .NET build as `godot-mono`, and the .NET SDK (`dotnet`)

## C# projects

A project with a `.csproj` is a C# project. Before `capture`, `live start` and `import`, gdh builds its assemblies with `dotnet build`, since Godot run from the command line loads them but never builds them. It builds only when a `.cs`, `.csproj`, `.sln` or props/targets file changed since its last build, so don't build by hand first; `--rebuild` forces a build. It runs `godot-mono` unless `GODOT` names another binary. A failed build stops gdh with the compiler's errors. `--no-build` skips the build.

C# objects are invisible to `eval` unless the game hands them over as Godot values, so give the project a node or autoload with methods that return dictionaries and arrays.

## Importing

Godot run from the command line, as gdh runs a game, never imports assets. A texture or model whose imported copy in `.godot/imported` is missing fails to load, and the game draws without it, so a fresh checkout or worktree renders with missing textures. A changed asset with a stale copy draws as it was. So before `capture` and `live start`, gdh checks the project's import cache and imports it (`godot --headless --import`) when it's missing or stale, and says why on stderr. The check reads file times only, and hashes an asset only when it's newer than its import (a checkout touches files without changing them), so it costs next to nothing when nothing changed; a Godot import costs a couple of seconds. It counts as stale when `.godot` or `.godot/uid_cache.bin` is missing, an importable asset has no `.import` file, an imported copy named in an `.import` file is missing, an asset's content changed since its import, or a script's `class_name` is missing from Godot's class cache. An asset Godot can't import is remembered and skipped, and every other asset is still checked. A change to an asset's import settings alone isn't detected: run `gdh import`. `--no-import` skips the check. `gdh editor` needs none of this: it runs the editor, which imports as it opens.

Resources that still fail to load (`Failed loading resource`, `Error loading resource`, `No loader found for resource`, a scene's `[ext_resource] referenced non-existent resource`, a missing `.ctex`) are a defect, not log noise: `capture` prints a `DEFECT:` line naming them and lists them in `report.json` under `missing_resources`, and `live` prints the same line on stderr, with the errors of the command that raised them.

## Window size

`--resolution` sets the game's window. gdh checks the window's real size once the game has started (`DisplayServer.window_get_size()`), since a game can size its window itself, from a saved setting say. If it isn't the size asked for, `capture` saves what it got and exits 1 with the size it found (also in `report.json` under `size_mismatch`), and `live start` stops the game and says so. A game that resizes its window later in a live session gets a note on the next `step`. The window is what's compared: under stretch mode `viewport` the screenshots are at the project's base size whatever the window is.

## Install

From a clone of this repo:

```sh
uv tool install .
```

This puts `gdh` on your `PATH`. After pulling changes, run `uv tool install --reinstall .`.

To run it from the repo without installing, use `uv run gdh ...`.

`gdh guide` prints a one-page cheat sheet of the commands that save an agent the most calls, for an agent that has gdh but not the plugin below.

## Claude Code plugin

The repo is also a Claude Code plugin. To try it without installing:

```sh
claude --plugin-dir /path/to/gdh
```

To install it from GitHub ([astrosteveo/gdh](https://github.com/astrosteveo/gdh), private for now, so you need access to it):

```
/plugin marketplace add astrosteveo/gdh
/plugin install gdh@gdh
```

It runs gdh from the plugin's own copy of this repo, so `uv` is the only extra install. It holds:

| Part | What it does |
|---|---|
| `gdh` skill | When and how to see and drive the game: which mode to pick, how to read the views and crops, how to drive a live session, what to report |
| `godot-editor` skill | Changing a project with the editor open: the bridge, live edits the user can undo, UIDs |
| `godot-gdscript`, `godot-project`, `godot-export` skills | Typed Godot 4.7 GDScript and its Godot 3 pitfalls; scenes, autoloads, the Input Map and saves; exporting builds |
| `playtester` agent | Plays a scene with `gdh live` against a goal and reports pass or fail with evidence, keeps each passed goal as a scenario, and leaves notes for the next playtest |
| Hooks | Before each Write or Edit in a Godot project: refuse edits to `.godot/`, to scenes with unsaved changes in the editor, to `project.godot` under a running editor, and `uid://` values the project doesn't have. After: rescan the file, reload it if it's open, fill in a new scene's UIDs and check GDScript, returning Godot's errors. `GDH_HOOKS=off` turns them off. |
| Band | A row above Claude Code's prompt in a Godot project: the editor bridge, the open scene, unsaved scenes, the running game and editor errors waiting for Claude |

## Capture a scene

```sh
gdh capture --project path/to/game --scene res://levels/level_1.tscn --out captures/level_1
```

Arguments after `--` go to the game, and `OS.get_cmdline_user_args()` returns exactly them:

```sh
gdh capture --project path/to/game --scene res://main.tscn --out captures/main -- --level 3
```

Nothing is installed into the project. For each scene, the output directory gets these files:

| File | Contents |
|---|---|
| `normal.png` | The frame as the player sees it |
| `unshaded.png` | Base colors with lighting off |
| `lighting.png` | Lighting only |
| `normals.png` | Surface directions (normal buffer) |
| `wireframe.png` | Triangle edges |
| `overdraw.png` | How many times each pixel is drawn |
| `report.json` | GPU used, display, window and image size, engine errors and warnings, resources that failed to load, render stats, probe findings |
| `crops/` | A zoomed crop for each finding that has a screen area |
| `godot.log` | Full engine output |
| `display.log` | The display's own output (weston and Xwayland, or Xvfb) |

Options:

| Option | Default | Meaning |
|---|---|---|
| `--scene` | required | Scene to capture. Repeat it to capture several scenes, each in its own subdirectory. |
| `--modes` | all six | Comma-separated list of views, e.g. `normal,wireframe` |
| `--locale CODE` | the project's | Translate the game's text to this locale, or `pseudo`: every text 40% longer with accents, so the `text_overflow` probe finds what won't fit ([docs/probes.md](docs/probes.md#text-that-wont-fit---locale)) |
| `--warmup` | `30` | Frames to render before capturing |
| `--resolution` | `1280x720` | The game window's size, checked once the game has started ([Window size](#window-size)) |
| `--display` | `auto` | `gpu`, `xvfb` or `auto` ([Displays](#displays)) |
| `--timeout` | `120` | Seconds allowed per scene |
| `--tiles` | off | Also save `normal.png` as four 2× tiles in `crops/` |
| `--baseline DIR` | off | Compare each view with the PNG of its name in DIR (a subdirectory per scene, as `--out`), report a changed view as a finding with a crop, and exit 1 when one changed past `--tolerance` ([docs/measure.md](docs/measure.md)) |
| `--update-baseline` | off | Write the views into `--baseline DIR` instead |
| `--tolerance PCT` | `0` | With `--baseline`: the percent of a view's pixels that may change |
| `--threshold T` | `2` | With `--baseline`: a pixel has changed when a channel differs by more than T (of 255) |
| `--no-import` | off | Don't import the project first when its import cache is missing or stale ([Importing](#importing)) |

Environment variables:

| Variable | Meaning |
|---|---|
| `GODOT` | Path to the Godot binary. Default: `godot-mono` for a C# project, `godot` otherwise. |
| `GDH_GPU_INDEX` | Vulkan device index to render on. Default: Godot picks one. |
| `GDH_DISPLAY` | The display when `--display` isn't given: `auto` (the default), `gpu` or `xvfb`. |

## Drive a running game

```sh
gdh live start --project path/to/game
gdh live step 30 --hold ui_right --shot
gdh live eval "get_node('Player').position"
gdh live stop
```

`gdh live` starts the game off-screen, held at frame 0, and runs it an exact number of frames per `step`. Steps can inject actions, keys and shortcuts, clicks, drags, typed text (`--type`), the wheel, a gamepad's buttons and sticks, touches and mouse-look. Between commands you can save frames in any view, run the probes, inspect the scene tree and evaluate expressions.

Most checks take one command:

```sh
gdh live step --until "scene.name == 'Hangar'" --max 1200   # run until it holds, or exit 1
gdh live step 120 --trace "get_node('Ship').position.y"       # a value, frame by frame
gdh live find Play                                             # what shows "Play", and where
gdh live step 2 --click-text Play                              # click it, no coordinates
gdh live shot --node UI/Inventory --zoom 2 --out inv.png       # that part of the frame, zoomed
gdh live shot --annotate all --filter class:Enemy               # each node boxed and named, shapes, nav, velocities
gdh live pick 640,360                                           # what's drawn at that pixel, topmost first
gdh live batch --session s < commands.txt                      # many commands, one process
```

After a code change, `gdh live reload` loads changed GDScript into the running game with its state kept, and `gdh live restart --replay` starts the session again with the same options and companions and replays its input log back to the same frame. `start --recipe FILE` runs lines written as for `batch` once the game is ready (a project's "get to the hangar"), `--seed N` makes the global random numbers repeat, and `--user-data fresh` or `--user-data-from DIR` give the session a `user://` of its own.

`start --net NAME` puts a companion behind gdh's network proxy, and `gdh live net` adds latency, jitter and packet loss, cuts and heals one instance's link, or resets its connections, to test a multiplayer game's lag, desync and reconnects. `gdh live snapshot` prints the UI on screen as a text outline, each text, button, field and slider with its state, and compares it with a baseline file, a check that doesn't depend on pixels. `start --timeline` keeps a record of the session to look through afterwards, as Playwright's trace viewer does: `<out>/timeline/index.html` lists every command with its frames, errors, notes and values, and a thumbnail of the frame after it. `--click-text` and `--click-node` fail without stepping when something else would take the click, such as a transparent panel left over a button, and say what it is.

Results go to stdout. Engine errors (with a script's backtrace), `DEFECT:` lines, notes and what the game printed go to stderr, so they survive a discarded stdout; `--strict` exits 1 when the game raised engine errors. `gdh live list` lists every session.

A session can also start companion processes beside the game, such as a server, wait until they're ready, hand their ports to the game and stop them with it, and it can run several instances of the game that step together. A script can drive it over one pipe:

```sh
gdh live start --project path/to/game --instances 2 \
  --companion 'server=exec ./server --port {port}' -- --connect 127.0.0.1:{server.port} --name player{instance}
gdh live step 60 --hold move_right --instance 1
gdh live eval "get_node('Player').position" --instance all
gdh live pipe < requests.jsonl
```

Processes the game spawns (a launcher's game, a tool) are listed by `gdh live status` and end with the session. `gdh live start --keep-children` keeps the session and its display until they have ended too, for a launcher that hands off to the game and exits, or until the idle timeout passes with no `gdh live` command on the session; the harness stays in the game gdh started, so a handed-off game can be watched but not stepped.

See [docs/live.md](docs/live.md).

## See what the editor shows

```sh
gdh editor --project path/to/game --scene res://levels/level_1.tscn --out captures/editor
gdh editor --project path/to/game --scene res://levels/level_1.tscn --out captures/editor --zoom 30 --orbit=-150,40
gdh editor --project path/to/game --scene res://levels/level_1.tscn --out captures/editor --view 0,40,80:0,0,0 --far 5000
```

`gdh editor` opens scenes in the Godot editor itself, on a display of gdh's own, and saves what the editor shows. Godot runs as `godot --editor --script <gdh's harness>`, so the editor starts as it does for a person, with the project's tool scripts, plugins and importers, and gdh's harness as its main loop: nothing is installed into the project. For each scene the output directory gets:

| File | Contents |
|---|---|
| `viewport.png` | The editor's first 3D viewport (or its 2D one), as the editor draws it: the scene, the grid and the gizmos |
| `editor.png` | The whole editor window: the docks, the inspector, the viewport and its menus |
| `report.json` | Errors and warnings while the scene opened and after, how long it took to open, how often the editor redrew over idle seconds, what one redraw of the viewport costs, the camera, and the scene's nodes with how many tool scripts made under each |

Beside them, `editor.json` holds the startup (how long the editor took to be ready, its errors) and `project_changes`: files outside `.godot` that the editor wrote in the project (Godot often rewrites `project.godot` in its own format). gdh lists them and leaves them as they are. `godot.log` and `display.log` are the engine's and the display's output.

Options:

| Option | Default | Meaning |
|---|---|---|
| `--scene` | required | Scene to open. Repeat it to open several in one editor run, each in its own subdirectory. |
| `--resolution` | `1600x900` | The editor window's size |
| `--warmup` | `60` | Frames to wait after a scene opens |
| `--idle` | `2` | Seconds to count the editor's redraws while nothing happens |
| `--orbit DX,DY` | | Turn the 3D view as dragging with the middle button does (write `--orbit=DX,DY` for a negative DX) |
| `--zoom STEPS` | | Zoom the 3D view as the mouse wheel does: out, or in when negative |
| `--focus NODE` | | Center the 3D view on a node, as the View menu's Focus Selection does; in a 2D scene, frame it (Frame Selection) |
| `--rebuild` | off | Build a C# project's code again with the scene open and give the editor the focus so it loads the new build; saves `viewport-rebuilt.png` |
| `--save` | off | Save each scene through the editor after capturing it; the report says whether the file changed |
| `--view X,Y,Z:X,Y,Z` | | Look from the first point at the second, through a camera gdh adds and previews (never saved) |
| `--far` | the editor camera's | The `--view` camera's far plane |
| `--set NODE:PROPERTY=VALUE` | | Set a property before capturing, in memory only; repeatable. The value is read as Godot's syntax; a `res://` path is that resource, loaded; anything else is plain text |
| `--select NODE` | | Select a node, so the inspector shows it |
| `--display`, `--timeout`, `--no-build` | | As for `capture` |

NODE is a path from the scene's root (`.` is the root). The editor's settings, data and caches live in `~/.local/share/gdh/editor-home` (`GDH_EDITOR_HOME` picks another), never your own, and the project's `.godot/editor` (its layout, recent scenes and each scene's camera) is put back as it was. See [docs/editor.md](docs/editor.md).

## Measure what it draws

`gdh measure` puts numbers on frames, any game's: flicker on a still camera, shimmer on a moving one (the second difference over time), a light's jitter, what one setting adds, a thin line's width and brightness, the size of each point of light (stars, dust), doubled or empty pixels in a dissolve, black from a NaN, crushed blacks, and frame times. A live session records the frames, or measures as it goes, and records each frame's GPU time and, with `--gpu-passes`, each render pass's.

```sh
gdh live record 120 --session s --out frames/still       # 120 frames, each saved
gdh measure flicker frames/still                          # every pixel's change over them
gdh measure line frame.png --from 400,120 --to 400,580    # a line's width at half its height
gdh measure spots frame.png --radius 6                     # each star's or mote's width at half its peak, and sigma
gdh live measure black --frames 60 --fail --session s     # black cut into something lit: a NaN
gdh live start --project game --session s --gpu-passes    # time each render pass too
gdh live bench 600 --budget-p99 8.3 --session s            # time 600 frames in one call; exit 1 over budget
gdh live monitors --leak --session s                       # nodes, orphans, objects, memory: exit 1 on steady growth
gdh live audio --session s                                 # each bus's peak over the last step, and what played
gdh measure sheet frames/still --out still-sheet.png      # 16 frames of a run (or a video) in one image
gdh measure diff before.png after.png --out diff          # what changed: share, box, heatmap, amplified crop
```

A recording (`live record`, `live measure`) also writes a contact sheet beside its frames' directory and flags a UI panel that covered the middle of the screen for most of it ([docs/movie.md](docs/movie.md#panels-that-cover-the-screen)).

See [docs/measure.md](docs/measure.md) for each measure's definition and limits.

## See motion

An image reader sees one still frame at a time. These turn motion over many frames into one image, from a live session (stepped with any of `step`'s input) or from saved frames:

```sh
gdh live step 90 --hold ui_accept --trace "get_node('Player').position" --trace-chart jump.png --trace-rates
gdh live onion 40 --node Player --hold ui_right --session s     # the frames laid over each other, oldest faintest
gdh live step 60 --trail Player --every 5 --session s           # its path on the last frame, a dot every 5 frames
gdh live filmstrip 24 --node Player --session s                 # the same box from each frame, side by side
gdh measure changes frames/idle --out idle-changes.png --still  # where a run changed, and how often
```

A trace chart shows values over time, with velocity and acceleration. An onion skin shows a movement's path, spacing and shape. A trail shows the path through the world even when the camera follows the node, and prints the pixels between its dots, so easing and overshoot show as numbers. A filmstrip shows a pose, a flash or a transition frame by frame. A change map shows what moved in a scene that should be still, and what stayed frozen that should move; `gdh live record` writes one beside each recording. See [docs/motion.md](docs/motion.md).

## Record a movie

```sh
gdh movie --project path/to/game --out captures/clip --seconds 20 --resolution 3840x2160
```

`gdh movie` records the game with Godot's Movie Maker at a fixed frame rate (`--fps`, 60), every frame and the game's audio, as an MJPEG AVI at its best quality, and ffmpeg encodes it to `movie.mp4`: H.264 (crf 18, yuv420p, `+faststart`) with AAC audio. The output directory also gets `sheet.png`, a contact sheet of 16 frames over the run, and `report.json`. Movie Maker ignores `--resolution` (it records at the size the project's settings give its window), so gdh writes an `override.cfg` into the project for the run and removes it once Godot has read it; it refuses to touch an `override.cfg` it didn't write. gdh checks the movie's size and frame count, and flags a UI panel left over the middle of the screen for most of the run (a `covered` warning). It needs `ffmpeg` and `ffprobe`. See [docs/movie.md](docs/movie.md).

## Probes

After capturing, `gdh` checks the scene's data for likely defects. Examples are floating objects, a tilted camera, geometry cut off by the far plane, material values out of range, blurry pixel art, raw translation keys and misaligned UI items. It prints each finding and saves a zoomed crop of it. See [docs/probes.md](docs/probes.md) for the checks, their thresholds and test results.

## Work through the editor

```sh
gdh bridge install                 # copy the addon into addons/gdh_bridge; the user enables it in Project Settings > Plugins
gdh bridge start                   # or: gdh's own headless editor, with nothing installed in the project
gdh bridge status                  # open scenes, unsaved scenes, the current scene, the running game
gdh bridge scan                    # rescan; waits until imports and new .uid files are done
gdh bridge uid res://player.gd     # path to UID, or uid:// to path
gdh bridge resave res://level.tscn # save through Godot, which fills in the scene's UIDs
gdh bridge check res://player.gd   # Godot's errors for a script (headless when no editor runs)
gdh bridge open res://level.tscn   # show it to the user
gdh bridge exec edit.gd            # run func run(editor) inside the editor, as an undoable edit
```

The bridge is a small HTTP server on 127.0.0.1 inside the Godot editor, with a random token in `.godot/gdh_bridge.json`. Every reply carries the editor's errors since the last one. `gdh bridge start` runs it in a headless editor of gdh's own (settings in gdh's editor home, the project's `.godot/editor` put back when it stops), which quits after 30 idle minutes. See [docs/bridge.md](docs/bridge.md).

## Look up the API

```sh
gdh api CharacterBody2D                 # its chain, properties, methods, signals, constants
gdh api CharacterBody2D.move_and_slide  # one member's signature and description, found up the chain
gdh api --search floor
```

The reference is the installed Godot's own: the editor's help cache, built once per Godot version (about ten seconds, in a headless editor) and kept in `~/.cache/gdh/api/`. A wrong name exits 1 with close matches.

## Run tests

```sh
gdh test --project path/to/game                       # res://test and res://tests
gdh test --project path/to/game res://test/test_player.gd --out captures/tests
```

`gdh test` runs GUT (`addons/gut`) or gdUnit4 (`addons/gdUnit4`) through their own command-line runners, or, in a project with neither, gdh's own runner: each method named `test*` in a `test*.gd` file is a test, and a test fails when it raises an engine error (a failed `assert()`, `push_error()`, a script error). It runs headless unless `--display` asks for one, and prints each failure; `report.json` and `junit.xml` hold the rest. It also runs the `*.scenario.json` files there (below). See [docs/test.md](docs/test.md).

## Scenarios

```sh
gdh live save-scenario --session s level1.scenario.json   # what a session was sent, with its seed
gdh scenario run level1.scenario.json                       # replay it, check it, exit 1 on a failing check
```

A scenario is a JSON playthrough: `start` options, steps (`gdh live batch` lines or objects), checks (`expect` an expression truthy, `equals` or `approx` a value, `until` it holds) and checkpoint shots compared with baselines. gdh's steps are frame-exact, so a scenario replays the same way each time. `gdh scenario run` writes `results.json` and `junit.xml` and always stops its session. A script can drive a game from Python instead: `from gdh.client import Session`, then `with Session.start(project, scene=...) as g: g.step(30, hold="ui_right"); g.until("..."); g.eval("...")`, all over one pipe. See [docs/scenarios.md](docs/scenarios.md).

## Export

```sh
gdh export --project path/to/game                    # list the presets
gdh export --project path/to/game --preset Linux     # release build to the preset's export path
gdh export --project path/to/game --preset Linux --pack   # the .pck alone, no templates needed
```

It checks the preset and the export templates for the exact Godot version first, and says what's missing. `--smoke SECONDS` then runs the build off-screen for that long, fails on a crash, an early exit or engine errors in its log, and saves `smoke.png`.

To drive an exported build, a launcher or any other X program as it is (where `OS.has_feature("editor")` matters), run it as a black box on gdh's display: `gdh live start --binary build/game.x86_64`, then `wait --log REGEX`, `shot`, `input --click X,Y --type TEXT --key Return`, `status` and `stop`. Shots read the display, input goes through XTest, and gdh gives the window the focus so one click is one click. Commands that need gdh's harness (`step`, `eval`, `tree`, `find`) say so. See [docs/live.md](docs/live.md).

## Displays

Each Godot run gets an X display of its own, and Godot draws to it with its X11 driver and Vulkan. There are two kinds:

- **`gpu`**: a virtual display the GPU presents to. It's weston's headless backend, compositing with OpenGL on the GPU, running a rootful Xwayland. Xwayland has DRI3, so Vulkan hands each finished frame over as a GPU buffer, and the game runs as fast as the GPU draws it.
- **`xvfb`**: Xvfb, which has no DRI3. Vulkan copies every frame through the CPU, which takes about 100 ms a frame at 3840x2160 while the GPU idles.

`--display auto`, the default, uses the GPU display, and falls back to Xvfb with a note when it can't start: weston or Xwayland isn't installed, or weston finds no GPU to composite on. `--display gpu` fails instead of falling back. `GDH_DISPLAY` sets the default for scripts. Screenshots are the same on both, pixel for pixel, since gdh reads them from the game's own viewport. [docs/displays.md](docs/displays.md) has how it works and what was measured.

gdh starts Godot with `--disable-vsync`, since it paces the frames itself: held at 20 frames a second, `run` at the tick rate and `step` as fast as the GPU goes. A game that turns V-Sync on itself waits for the display's 60 Hz refresh on the GPU display, and `step` says so.

## How it stays off your desktop

Godot's window is on its own display, never your session's. Each GPU display has a runtime directory of its own for weston's socket, so weston and Xwayland never see your Wayland session, and Godot is pointed at a Wayland display that doesn't exist, so it can't fall back to yours. Every display ends when its game does, and gdh stops and removes whatever is left (`gdh live stop`, or the session's watchdog when a game ends by itself).

A game run under gdh also keeps its `user://` (saves, settings, logs, caches) in `~/.local/share/gdh/user-data`, never your own `~/.local/share/godot`, so a test run can't touch your saves or rotate out your logs. `GDH_USER_DATA=<dir>` picks another directory, `GDH_USER_DATA=real` uses yours ([docs/live.md](docs/live.md)).

For each run, gdh also writes stand-ins for `zenity`, `kdialog`, `Xdialog` and `xmessage` to a temporary directory and puts that directory first on `PATH`. When Godot pops up an alert, the stand-in writes the message to `godot.log` and no dialog opens.

## Layout

| Path | Contents |
|---|---|
| `src/gdh/cli.py`, `guide.py` | The `gdh` command, and `gdh guide`'s cheat sheet |
| `src/gdh/capture.py`, `live.py`, `editor.py`, `movie.py` | `gdh capture`, `gdh live`, `gdh editor` and `gdh movie` |
| `src/gdh/display.py` | The displays: the GPU display (weston and Xwayland) and Xvfb |
| `src/gdh/imports.py` | `gdh import`, and the import cache's check before a run |
| `src/gdh/override.py` | The `override.cfg` written into a project for one run |
| `src/gdh/client.py`, `scenario.py` | The Python client (`gdh.client.Session`) and `gdh scenario` |
| `src/gdh/restart.py` | `gdh live restart`, replays and input logs, recipes, and `gdh live reload` |
| `src/gdh/blackbox.py`, `perf.py` | `--binary` sessions (any program, its shots and XTest input), and `live bench`, `monitors` and `audio` |
| `src/gdh/companions.py`, `spawned.py`, `watchdog.py` | A live session's companion processes, the processes its game spawns, and the watchdog that stops them when its game ends |
| `src/gdh/measure.py`, `measure_cli.py` | The measures over frames, and `gdh measure` with `gdh live record`, `measure` and `frames` |
| `src/gdh/covered.py` | Panels that covered the middle of the screen during a recording |
| `src/gdh/motion.py`, `motion_cli.py`, `chart.py` | Motion in one image: onion skins, filmstrips, trails and change maps, with `gdh live onion` and `filmstrip` and their `gdh measure` forms; and trace charts |
| `src/gdh/editor_bridge.py`, `src/gdh/addon/gdh_bridge/` | `gdh bridge`, and the bridge that runs in the editor (the addon a project installs, or gdh's headless editor) |
| `src/gdh/hooks.py`, `hooks/` | The Claude Code hooks around Write and Edit, and the band above the prompt (`hooks/register.tsx`, its state in `types/`) |
| `src/gdh/api.py`, `testing.py`, `export.py` | `gdh api`, `gdh test` and `gdh export` |
| `src/gdh/harness/capture.gd` | Runs inside Godot. Saves the views, runs the probes and writes `report.json`. |
| `src/gdh/harness/live.gd`, `bridge.gd` | Run inside Godot for `gdh live`. The bridge takes commands over a local socket. |
| `src/gdh/annotate.py`, `src/gdh/harness/annotate.gd`, `pick.gd` | `gdh live shot --annotate`: the geometry the game reports, and the drawing and labels over the shot; and `gdh live pick`, what's drawn at a pixel |
| `src/gdh/harness/screen.gd`, `camera.gd` | Where nodes show on screen, for `find`, clicks by text or node and framed shots; and gdh's camera for `gdh live camera` |
| `src/gdh/harness/frames.gd` | Runs inside Godot for `gdh live frames`: each frame's GPU and CPU time, and each pass's |
| `src/gdh/harness/movie.gd` | The main loop for `gdh movie`: runs the scene for the frames asked for under Movie Maker |
| `src/gdh/harness/track.gd` | Where nodes are frame by frame during a step, for trails, onion skins and filmstrips |
| `src/gdh/harness/covered.gd` | Lists the UI panels drawn over the screen's centre, for `covered.py` |
| `src/gdh/harness/editor.gd` | The editor's main loop for `gdh editor`: opens each scene and saves what the editor shows |
| `src/gdh/harness/probes.gd` | The probes |
| `src/gdh/harness/bridge_host.gd`, `api_dump.gd`, `test_runner.gd`, `check.gd` | The headless editor's main loop for `gdh bridge start`, the help cache's reader for `gdh api`, gdh's own test runner, and the script check with no editor, which loads scripts as the game does, autoloads and all |
| `testbed/` | Godot project with test scenes |
| `tests/` | `uv run pytest`: probe findings on the testbed, live control, companions and spawned processes, the editor, the displays, imports, movies and the measures, each on both displays; the bridge and hooks, `api`, `test` and `export`, which run headless, once. `claude plugin test .` runs the band's tests. |
| `docs/` | Live control, the editor, measuring frames, seeing motion, movies, displays, probes, and test reports |
| `skills/`, `agents/`, `.claude-plugin/` | The Claude Code skills, the playtester agent, and the plugin manifests |

## License

To be decided.
