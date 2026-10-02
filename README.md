# gdh

Renders Godot scenes off-screen on the real GPU and saves screenshots, debug views, engine errors and checks for likely defects. An AI agent or a script can then inspect what the game draws.

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

A project with a `.csproj` is a C# project. Before `capture`, `live start` and `import`, gdh builds its assemblies with `dotnet build`, since Godot run from the command line loads them but never builds them. It runs `godot-mono` unless `GODOT` names another binary. A failed build stops gdh with the compiler's errors. `--no-build` skips the build.

C# objects are invisible to `eval` unless the game hands them over as Godot values, so give the project a node or autoload with methods that return dictionaries and arrays.

## Importing

Godot run from the command line, as gdh runs a game, never imports assets. A texture or model whose imported copy in `.godot/imported` is missing fails to load, and the game draws without it, so a fresh checkout or worktree renders with missing textures. A changed asset with a stale copy draws as it was. So before `capture` and `live start`, gdh checks the project's import cache and imports it (`godot --headless --import`) when it's missing or stale, and says why on stderr. The check reads file times only, and hashes an asset only when it's newer than its import (a checkout touches files without changing them), so it costs next to nothing when nothing changed; a Godot import costs a couple of seconds. It counts as stale when `.godot` or `.godot/uid_cache.bin` is missing, an importable asset has no `.import` file, an imported copy named in an `.import` file is missing, or an asset's content changed since its import. A change to an asset's import settings alone isn't detected: run `gdh import`. `--no-import` skips the check. `gdh editor` needs none of this: it runs the editor, which imports as it opens.

Resources that still fail to load (`Failed loading resource`, `Error loading resource`, `No loader found for resource`, a scene's `[ext_resource] referenced non-existent resource`, a missing `.ctex`) are a defect, not log noise: `capture` prints a `DEFECT:` line naming them and lists them in `report.json` under `missing_resources`, and `live` prints the same line with the errors of the command that raised them.

## Window size

`--resolution` sets the game's window. gdh checks the window's real size once the game has started (`DisplayServer.window_get_size()`), since a game can size its window itself, from a saved setting say. If it isn't the size asked for, `capture` saves what it got and exits 1 with the size it found (also in `report.json` under `size_mismatch`), and `live start` stops the game and says so. A game that resizes its window later in a live session gets a note on the next `step`. The window is what's compared: under stretch mode `viewport` the screenshots are at the project's base size whatever the window is.

## Install

From a clone of this repo:

```sh
uv tool install .
```

This puts `gdh` on your `PATH`. After pulling changes, run `uv tool install --reinstall .`.

To run it from the repo without installing, use `uv run gdh ...`.

## Claude Code plugin

The repo is also a Claude Code plugin. Its `gdh` skill teaches Claude when and how to use the tool: which mode to pick, how to read the views and crops, how to drive a live session, and what to report. To try it without installing:

```sh
claude --plugin-dir /path/to/gdh
```

To install it from GitHub once the repo is published, where `OWNER` is the GitHub account:

```
/plugin marketplace add OWNER/gdh
/plugin install gdh@gdh
```

The skill runs gdh from the plugin's own copy of this repo, so `uv` is the only extra install.

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
| `--warmup` | `30` | Frames to render before capturing |
| `--resolution` | `1280x720` | The game window's size, checked once the game has started ([Window size](#window-size)) |
| `--display` | `auto` | `gpu`, `xvfb` or `auto` ([Displays](#displays)) |
| `--timeout` | `120` | Seconds allowed per scene |
| `--tiles` | off | Also save `normal.png` as four 2× tiles in `crops/` |
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

`gdh live` starts the game off-screen, held at frame 0, and runs it an exact number of frames per `step`. Steps can inject actions, keys, clicks, drags and typed text (`--type`). Between commands you can save frames in any view, run the probes, inspect the scene tree and evaluate expressions.

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
gdh live frames --clear --session s && gdh live step 600 --session s && gdh live frames --session s
```

See [docs/measure.md](docs/measure.md) for each measure's definition and limits.

## Probes

After capturing, `gdh` checks the scene's data for likely defects. Examples are floating objects, a tilted camera, geometry cut off by the far plane, material values out of range, blurry pixel art, raw translation keys and misaligned UI items. It prints each finding and saves a zoomed crop of it. See [docs/probes.md](docs/probes.md) for the checks, their thresholds and test results.

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
| `src/gdh/cli.py` | The `gdh` command |
| `src/gdh/capture.py`, `live.py`, `editor.py` | `gdh capture`, `gdh live` and `gdh editor` |
| `src/gdh/display.py` | The displays: the GPU display (weston and Xwayland) and Xvfb |
| `src/gdh/imports.py` | `gdh import`, and the import cache's check before a run |
| `src/gdh/companions.py`, `watchdog.py` | A live session's companion processes, and the watchdog that stops them when its game ends |
| `src/gdh/measure.py`, `measure_cli.py` | The measures over frames, and `gdh measure` with `gdh live record`, `measure` and `frames` |
| `src/gdh/harness/capture.gd` | Runs inside Godot. Saves the views, runs the probes and writes `report.json`. |
| `src/gdh/harness/live.gd`, `bridge.gd` | Run inside Godot for `gdh live`. The bridge takes commands over a local socket. |
| `src/gdh/harness/editor.gd` | The editor's main loop for `gdh editor`: opens each scene and saves what the editor shows |
| `src/gdh/harness/probes.gd` | The probes |
| `testbed/` | Godot project with test scenes |
| `tests/` | `uv run pytest`: probe findings on the testbed, live control, the editor and the displays, each on both displays |
| `docs/` | Live control, the editor, probes, and test reports |

| `src/gdh/harness/frames.gd` | Runs inside Godot for `gdh live frames`: each frame's GPU and CPU time, and each pass's |
| `src/gdh/harness/probes.gd` | The probes |
| `testbed/` | Godot project with test scenes |
| `tests/` | `uv run pytest`: probe findings on the testbed, live control and the displays, each on both displays, and the measures |
| `docs/` | Live control, measuring frames, probes, and test reports |
| `skills/gdh/`, `.claude-plugin/` | The Claude Code skill and plugin manifests |

## License

To be decided.
