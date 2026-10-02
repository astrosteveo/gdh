---
name: gdh
description: See and drive a Godot 4 game on Linux. Render scenes off-screen on the real GPU, read screenshots and debug views (wireframe, normals, lighting, overdraw), get engine errors and automatic defect checks, play the game frame by frame with scripted input, see what the Godot editor itself shows a scene as (tool scripts included), and measure what it draws (flicker, shimmer, line width, NaN and crushed pixels, frame time, per-pass GPU cost). Use this whenever you build, change or debug anything in a Godot project, including scenes, levels, materials, shaders, UI, sprites, cameras, player controls and gameplay logic. Also use it when the user says something looks wrong, asks you to check or verify a scene, playtest, reproduce a bug, or confirm a change works. Godot's --headless mode draws nothing, so without this you're guessing at what the game shows.
---

# gdh: seeing and driving a Godot game

`gdh` runs Godot with Vulkan on the real GPU, on a virtual display of its own, so nothing appears on the user's desktop. It has three modes:

- `gdh capture` renders a scene once. You get six views, the engine errors and the probe findings.
- `gdh live` starts the game held at frame 0. You step it an exact number of frames with input, and you can look at it between steps.
- `gdh editor` opens scenes in the Godot editor itself and saves what it shows: the 3D (or 2D) viewport, the whole editor window and a report. Use it to check what a user will see when they open a scene, above all a scene with tool scripts.

Use it to check your own work. After you change a scene, material, shader or UI, capture it and look before telling the user it's done. After you change controls or gameplay, drive it live and measure. The user judges game feel (controls, pacing, fun). You cover whether the game renders correctly, runs without errors and behaves as specified.

## Setup

Check that `gdh --help` works. If it doesn't, the source is in the plugin root, `${CLAUDE_PLUGIN_ROOT}`. That's two directories above this skill's base directory if the variable isn't set. Either install it once, with the user's agreement, or run it in place:

```sh
uv tool install "${CLAUDE_PLUGIN_ROOT}"                 # puts gdh on PATH
uv run --project "${CLAUDE_PLUGIN_ROOT}" gdh --help     # no install
```

Requirements are Linux, Godot 4.x as `godot` on PATH (or set `GODOT=/path/to/godot`), a Vulkan driver, weston and Xwayland, and Xvfb. gdh imports the project before `capture` and `live start` when its import cache is missing or stale (a fresh checkout or worktree, a new or changed asset), and says so; `gdh import --project <dir>` does it by hand, for instance after changing only an asset's import settings. A `DEFECT: ... failed to load` line means the game drew without those resources: report it, never treat it as log noise.

`--resolution` is checked: if the game sizes its own window to something else, `capture` exits 1 and `live start` refuses, naming the size it found. Ask for that size, or change what sizes the window.

**Displays.** By default gdh runs the game on a display the GPU presents to (weston and Xwayland), so it runs at the GPU's full speed, even at 4K. If that can't start (weston or Xwayland missing, no GPU to composite on), gdh falls back to Xvfb and prints a note. Xvfb copies every frame through the CPU, so it's slow at high resolutions and the GPU idles between frames. If you see the note, say so when you report timings, and suggest installing weston and Xwayland. `--display gpu|xvfb|auto` (or `GDH_DISPLAY`) picks one. Screenshots are the same on both.

A C# project (one with a `.csproj`) also needs `godot-mono` and the .NET SDK. gdh builds it with `dotnet build` before every `capture`, `live start` and `import`, and runs `godot-mono`, so there's nothing to do by hand. A failed build stops gdh with the compiler's errors. `eval` can't see plain C# objects: have the game expose a node or autoload whose methods return dictionaries and arrays, and call those.

## Choosing a mode

| Situation | Use |
|---|---|
| A scene, level, material, lighting or UI layout was changed | `gdh capture` |
| "Does this look right?", or checking a scene for defects | `gdh capture`, then read everything it produced |
| Movement, input, animation, physics, timers, scene changes, UI interaction | `gdh live` |
| A bug that shows up after doing something | `gdh live`: reproduce it step by step |
| Flicker, popping or jitter over time | `gdh live step N --shot-every K` to look; `gdh live measure flicker` or `shimmer` for a number |
| A thin effect's width, a point's size (stars, dust), a NaN, crushed blacks, a dissolve | `gdh measure line`, `spots`, `black`, `crush`, `dissolve` |
| Frame time, or which render pass costs what | `gdh live start --gpu-passes`, then `gdh live frames` |
| What the editor shows: tool scripts, `@tool` previews, scenes the user opens to edit | `gdh editor` |

## The editor

```sh
gdh editor --project <dir> --scene res://path/scene.tscn --out <dir>/captures/editor/<name>
gdh editor --project <dir> --scene res://a.tscn --scene res://b.tscn --out <dir>   # several, one editor run
```

It runs `godot --editor` with gdh's harness as the main loop, so the project's tool scripts, plugins and importers run exactly as they do for the user, and nothing is installed into the project. It waits for the editor's first scan, opens each scene, and saves `viewport.png` (the first 3D viewport, or the 2D one), `editor.png` (the whole window: the Scene dock, the inspector, the viewport) and `report.json`: the errors raised while the scene opened and after, how long it took to open, how often the editor redrew over two idle seconds (`idle`: a tool script that writes to the scene every frame keeps the editor redrawing), what one redraw of the viewport costs on the CPU and the GPU (`redraw`), the editor camera, and the scene's nodes with how many each tool script made (`made_by_scripts`, never saved). `editor.json` has the startup's errors and `project_changes`: files the editor wrote in the project (often `project.godot`, rewritten in its own format). gdh lists them and never undoes them, so revert what the user didn't ask for.

- **Framing.** The editor camera starts a few metres from the origin. `--orbit=DX,DY` drags it round with the middle button and `--zoom STEPS` turns the wheel (out; negative is in), as a person would. `--view X,Y,Z:X,Y,Z` looks from one point at another through a camera gdh adds (never saved) and previews, with the editor camera's lens or `--far M`. `--focus NODE` centers on a node (the View menu's Focus Selection; it keeps the distance).
- **Changing what's shown.** `--set NODE:PROPERTY=VALUE` (in memory, never saved; Godot syntax, a `res://` path for a resource, or plain text) and `--select NODE`, which shows it in the inspector. NODE is relative to the scene's root: `.` is the root.
- **Nothing of the user's is touched.** The editor's settings, data and caches live in gdh's editor home (`GDH_EDITOR_HOME`, default `~/.local/share/gdh/editor-home`), and the project's `.godot/editor` (its layout, open scenes, each scene's camera) is put back as it was.
- **The editor's own camera** reaches 4 km by default (View → Settings → View Z-Far), unlike most game cameras: something far off may be clipped in the editor and not in the game.
- **A C# project** is built first, as for `capture` and `live`; the editor loads the build and runs its `[Tool]` scripts.

## Capture

```sh
gdh capture --project <dir> --scene res://path/scene.tscn --out <dir>/captures/<name>
```

Repeat `--scene` to capture several scenes. Each gets its own subdirectory. The command prints each finding with a crop path. The output directory holds:

- `normal.png`: the frame as the player sees it
- `unshaded.png`, `lighting.png`, `normals.png`, `wireframe.png` and `overdraw.png` (3D only, see below)
- `report.json`: GPU, `errors` (engine errors, merged by message with a count), `findings` and render stats
- `crops/`: a zoomed crop per finding. `--tiles` adds `normal.png` as four 2× tiles.
- `godot.log`: full engine output

Use `--modes normal,wireframe` to render only some views, which is faster.

## Live control

```sh
gdh live start --project <dir> [--scene res://...] --session <name>   # held at game frame 0
gdh live start --project <dir> --session <name> -- --level 3          # arguments after -- go to the game
gdh live status --session <name>
gdh live step 30 --hold ui_right --session <name>                     # run exactly 30 frames, then hold
gdh live shot --session <name> [--view wireframe]
gdh live tree [NodePath] --session <name>
gdh live eval "get_node('Player').velocity" --session <name>
gdh live probes --session <name>
gdh live stop --session <name>
```

- **Session names.** Give each task its own `--session` name. Another agent or task may be using `default`.
- **Stop when done.** Always run `gdh live stop`, because a running game keeps the GPU busy. It stops the game's display too. The game quits by itself after 30 idle minutes.
- **Processes the game spawns** (a launcher that starts the game and exits, a server) run on the session's display and end with the session; `status` lists them. For a launcher that hands off, use `gdh live start --keep-children`: the session and display last until the spawned processes exit, and `status` shows them. Don't wrap gdh in your own `xvfb-run` for this. gdh can't step or capture the handed-off game (its harness is in the launcher), so to drive it, start the game directly with `gdh live start` and the launcher's arguments.
- **Time.** The game is held between commands, so take as long as you need. `step N` runs exactly N frames, and each frame is one physics tick. Game seconds are frames divided by ticks per second, shown in `status`. This makes measurements exact. For example, a player at 120 px/s moves exactly 60 px in 30 frames at 60 ticks per second.
- **Input.** An INPUT is an action from the Input Map, such as `ui_right` or `jump`, or a key such as `key:Space`.
  - `--press`: press and keep pressed.
  - `--release`: release.
  - `--hold`: press for the whole step.
  - `--tap`: press for one frame.
  - `--type TEXT`: type into the focused text field, a character a frame (click the field first; step at least as many frames as characters).
  - `--click X,Y`: left click. `--right-click X,Y`: right click. `--left-hold X,Y` and `--right-hold X,Y`: press there for the whole step.
  - `--move X,Y` moves the pointer; `mouse:left`, `mouse:right` and `mouse:middle` work with `--press`, `--release`, `--hold` and `--tap` at the pointer. A drag or a point-while-held is `--move` then `--press mouse:right`, steps with `--move`, then `--release mouse:right`.

  Input arrives the way a player's does, so `_input`, `is_action_just_pressed` and `is_action_pressed` all see it.
- **User data.** Games run under gdh keep `user://` in `~/.local/share/gdh/user-data`, never the player's own; `GDH_USER_DATA=<dir>` picks another, `real` the player's.
- **Coordinates** are screenshot pixels everywhere. `tree` gives each node's `screen` position (`[x, y, w, h]` for Controls), so click at the center of what `tree` reports.
- **`eval`** evaluates one Godot Expression, with the current scene as its base. `scene`, `tree`, `root`, every autoload by name and the engine's singletons (`OS`, `Engine`, `Input`, `Time`...) are available. It can't assign with `=`. Use `set("prop", value)` or call a method instead.
- **Errors.** Every reply lists the engine errors raised since the previous command. Read them after every step. They're often the real bug.
- **Companions and instances.** `--companion 'NAME=COMMAND'` starts a program beside the game (a server, say) and stops it with the session; `{port}` in its command is a free port, and `{NAME.port}` passes it to the game's arguments (`-- --server ws://127.0.0.1:{server.port}`). `--companion-ready NAME=http://127.0.0.1:{port}/health` waits for it. `--instances N` runs N games that step together (`{instance}` in the game's arguments tells them apart); `step` input goes to `--instance K`, and `eval`, `shot` and `tree` take `--instance K` or `all`. A script that steps many times keeps one `gdh live pipe` open (docs/live.md).
- **Frame pacing.** gdh starts Godot with V-Sync off: steps run as fast as the GPU goes. If the game turns V-Sync on itself, a step's notes say so, and steps then run at about 60 frames a second.
- **Nodes that run while held.** `status` lists nodes with process mode ALWAYS or WHEN_PAUSED. They keep running while the game is held, so account for them when measuring.

## Measuring

Numbers settle what eyes can't: whether something flickers, swims, stays a pixel wide, or costs too much. Every measure reads PNGs (files or directories, in name order); see `${CLAUDE_PLUGIN_ROOT}/docs/measure.md` for each definition.

```sh
gdh live record 90 --out <dir>/frames --session <name> [--hold ui_left]   # step 90 frames, save each
gdh measure flicker <dir>/frames          # still camera: anything over 0 changes on its own
gdh measure shimmer <dir>/frames          # moving camera: the second difference over time
gdh measure line shot.png --from X,Y --to X,Y   # a thin line's width at half maximum, its peak
gdh measure spots shot.png --radius 6     # each isolated point's width at half maximum, sigma and peak
gdh measure black <dir>/frames --fail     # pure black cut into something lit: a NaN
gdh measure crush shot.png --mask hull.png      # pixels at the tone mapper's floor in a region
gdh measure mask with.png without.png --out hull.png   # where a thing draws (shots with it and without)
gdh live measure shimmer --frames 60 --session <name>  # record and measure in one go
gdh live start --project <dir> --session <name> --gpu-passes   # then:
gdh live frames --clear --session <name>; gdh live step 600 --session <name>; gdh live frames --session <name>
```

- **Compare like with like.** Moving edges count in `shimmer`, so compare a shot against the same shot changed one way, never two different shots. `term WITHOUT WITH` measures what one setting adds from two runs of the same held frames.
- **Frame times** count only frames the game ran, each measured frame once; the summary says how many were measured of the frames run. A game's own pass shows when it calls `RenderingDevice.capture_timestamp("Name")`. Under Xvfb the GPU idles between frames, so times read slower than in play: say which display ran, and compare runs with each other, alone on the GPU.
- **`black` and `crush` have blind spots:** a black object on purpose in front of something lit reads as a NaN's hole, and which regions should hold detail is yours to choose (`--box`, `--mask`).

## Looking at images

Read PNGs with the Read tool. This is where you catch what logs miss, so don't skip it.

- **Keep each image at about 1280 px wide or less, and never view several frames tiled into one large image.** Downscaled contact sheets create streaks and blotches that aren't in the real frame. To compare frames, view them one at a time or crop the same region from each.
- **Zoom in for small things.** A floating object, a blurry sprite or a misaligned icon is easy to miss at full-frame size and obvious at 2–4×. Use the crops in `crops/`, `--tiles`, or crop a region yourself with nearest-neighbor scaling.
- **Debug views affect 3D only.** In a 2D or UI scene, all six images are identical. For 2D, use `tree` screen positions and zoomed crops instead.

What each 3D view shows:

| View | Reveals |
|---|---|
| `unshaded` | Albedo with no lighting. Tells a color or texture problem apart from a lighting problem. |
| `lighting` | Light only. Shows shadow acne (fine stripes), light leaks and missing shadows. |
| `normals` | Surface direction as color. Flipped faces and smoothed hard edges show as wrong or gradient colors on flat faces. |
| `wireframe` | Triangles. Shows holes, stray or stretched triangles, and meshes far too dense for their size. |
| `overdraw` | How often each pixel is drawn. Shows stacked transparency and hidden geometry. |

## Probes

The probes check scene data for likely defects:

- objects floating above surfaces
- a rolled camera
- geometry cut off by the far plane
- material values outside 0–1
- a texture with alpha drawn opaque
- zero shadow bias
- mirrored `Label3D` text
- pixel art blurred by linear filtering
- smeared sprite regions
- raw translation keys and placeholder text
- one UI item out of line with its siblings

Treat a `warning` as a strong lead and confirm it on its crop before reporting it. An `info` finding is often intended. The probes can't see everything, and they were tuned on a small set of scenes. An empty findings list doesn't mean the scene is fine, so still look at the images. See `references/probes.md` for what each probe measures and where it's weak.

## Checking a Godot API name

The installed Godot may be newer than your training data. When unsure whether a class, method, property or signal exists, check the engine itself:

```sh
godot --version
cd /tmp && godot --headless --dump-extension-api   # writes extension_api.json
jq '.classes[] | select(.name=="CharacterBody2D") | [.methods[].name, .properties[]?.name]' extension_api.json
```

## Reporting to the user

- **Verified and unverified:** say what you verified and how, such as "stepped 30 frames holding right; the player moved 60 px, which matches 120 px/s". Also say what you didn't check.
- **Evidence:** give the paths of the screenshots or crops that show a problem or a fix. If a file-sending tool is available, send the key image.
- **Game feel:** leave it to the user. Say which parts need a human playtest, such as feel, pacing, input latency or sound.

## References

- `references/probes.md`: what each probe measures, its thresholds and its known blind spots
- `${CLAUDE_PLUGIN_ROOT}/docs/live.md`: full live-control reference, including the raw JSON protocol for scripting many steps in one program
- `${CLAUDE_PLUGIN_ROOT}/docs/editor.md`: how `gdh editor` drives the Godot editor, its report, and what it leaves alone
- `${CLAUDE_PLUGIN_ROOT}/docs/measure.md`: every measure's definition, its options and its limits, and how frame times are recorded
