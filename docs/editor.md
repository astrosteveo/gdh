# The editor

`gdh editor` opens scenes in the Godot editor, on a display of gdh's own, and saves what the editor shows: its viewport, its whole window and a report. It's for checking what a person will see when they open a scene to edit it, above all a scene whose tool scripts build or preview things in the editor.

```sh
gdh editor --project path/to/game --scene res://world.tscn --out captures/editor
gdh editor --project path/to/game --scene res://world.tscn --scene res://ship.tscn --out captures/editor
gdh editor --project path/to/game --scene res://world.tscn --out captures/editor --orbit=-150,40 --zoom 30
gdh editor --project path/to/game --scene res://world.tscn --out captures/editor --set .:sector='"drift"' --select .
```

## How it runs

gdh starts `godot --editor --path <project> --script src/gdh/harness/editor.gd`. With `--editor`, Godot builds its editor into the main loop's tree even when `--script` names the main loop, so the editor starts exactly as it does for a person: it scans and imports the project, loads its plugins and runs its tool scripts. The harness is the main loop, a `SceneTree`, and drives the editor through `EditorInterface`:

1. It waits until the editor's first scan of the project is done (30 frames without scanning).
2. For each scene: it opens it (`open_scene_from_path`), switches to the 3D screen (2D for a 2D scene), applies `--set`, frames the view (`--view`, else `--focus`, `--orbit` and `--zoom`), selects `--select`, and waits `--warmup` frames.
3. It counts the editor's redraws over `--idle` seconds, draws the viewport 32 times to time it (`viewport_set_measure_render_time`), then draws once more and saves the images and the report.
4. It closes the scene, and quits when every scene is done. Nothing is saved unless `--save` asks for it, and quitting skips the editor's "save changes?" prompt.

The editor draws only when something changes (low processor mode), so the harness never waits for a frame to come by itself: it forces one (`RenderingServer.force_draw`). That's also why `idle` means something: a scene whose tool scripts write to it every frame (a shader parameter, a transform) keeps the editor redrawing as fast as it can, and `idle.redraws_per_second` shows it. A still scene redraws about 0 times a second.

A C# project is built with `dotnet build` first, as for `capture` and `live`, so the editor loads the current build and runs its `[Tool]` scripts. A failed build stops gdh with the compiler's errors.

## Moving the view

The editor's camera starts a few metres from the origin, looking at it. Three ways to move it:

- `--orbit=DX,DY` and `--zoom STEPS` feed the first 3D viewport the input a person would: a drag with the middle button (DX, DY pixels, in steps of 20) and turns of the mouse wheel (out, or in when negative). How far they move it depends on the editor's navigation settings, which are Godot's defaults in gdh's editor home.
- `--focus NODE` selects a node and runs the View menu's Focus Selection, which centers the view on it and keeps its distance. In a 2D scene (a UI), it runs the 2D View menu's Frame Selection instead, which centers the node and zooms to fit it.
- `--save` saves each scene through the editor after it's captured (`EditorInterface.save_scene()`, as File → Save Scene does), and the report's `saved` says whether the file changed and what errors the save raised. Saving twice shows whether a save is stable: a tool script that leaves its editor-only view in the scene changes it every time.
- `--view X,Y,Z:X,Y,Z` adds a camera to the scene (an internal child, not owned, so a save never keeps it) at the first point looking at the second, selects it and ticks the viewport's Preview box, so the viewport draws through it. It has the editor camera's field of view and near plane, and its far plane unless `--far` says otherwise. The report's `camera.previewing` is true.

The editor's camera reaches 4000 m by default (View → Settings → View Z-Far, per scene): a game whose camera reaches farther can show things the editor clips.

## The report

`report.json`, one a scene:

| Key | Contents |
|---|---|
| `open_ms` | From asking the editor to open the scene to its first frame drawn |
| `open_errors`, `errors` | Errors and warnings raised while the scene opened, and after (the tool scripts' first frames, the warmup), merged by message with a count |
| `idle` | `redraws` and `redraws_per_second` over `seconds` while nothing happened |
| `redraw` | One redraw of the viewport: `cpu_ms_median`, `gpu_ms_median` and `gpu_ms_worst` over 30 |
| `viewport_size`, `window_size` | Pixels |
| `camera` | The camera the viewport drew through: position, forward, fov, near, far, and whether it was `--view`'s |
| `tree` | The scene's nodes as the scene owns them (with each one's script and the scene it instances), to six levels; `made_by_scripts` counts the nodes tool scripts made under a node (never owned, so never saved) |
| `notes` | Options that couldn't be applied, and why |
| `error` | Set when the scene couldn't be opened; gdh exits 1 |

`editor.json` holds the startup: `ready_ms`, the errors and warnings while the editor started (Godot 4.7's editor logs `Parameter "current_window" is null` twice as it starts on any project), the adapter, the display, and `project_changes`.

## What it leaves alone

- **Your editor.** The editor's settings, data and caches (`XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`) are gdh's editor home, `~/.local/share/gdh/editor-home`, or `GDH_EDITOR_HOME`. Your own editor settings, recent projects and script templates are never read or written.
- **The project's editor state.** `.godot/editor` (the dock layout, the scenes left open, each scene's camera and folded nodes) is copied aside before the editor starts and put back after, so the next time you open the project it's as you left it.
- **The project's files** are only reported. The editor writes to a project as it opens it: often `project.godot`, in its own format, sometimes `.import` files or `.uid` files for new scripts. gdh snapshots every file outside `.godot` before and after (hashing those up to 256 KB, so one rewritten unchanged isn't counted) and lists what was `added`, `removed` and `changed` in `editor.json`'s `project_changes`, and on the command line. It never undoes them: revert what you didn't mean to change.

When the GPU's memory is full (other games, other agents), Vulkan can't create its device and the editor can't start; gdh says so.
