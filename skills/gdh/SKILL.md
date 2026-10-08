---
name: gdh
description: See and drive a Godot 4 game on Linux. Render scenes off-screen on the real GPU, read screenshots and debug views (wireframe, normals, lighting, overdraw), get engine errors and automatic defect checks, play the game frame by frame with scripted input, wait for conditions, find and click UI by its text, keep playthroughs as replayable scenarios, run exported builds off-screen, see what the Godot editor itself shows a scene as (tool scripts included), and measure what it draws (flicker, shimmer, line width, NaN and crushed pixels, frame time and budgets, per-pass GPU cost, leaks, audio levels). Use this whenever you build, change or debug anything in a Godot project, including scenes, levels, materials, shaders, UI, sprites, cameras, player controls and gameplay logic. Also use it when the user says something looks wrong, asks you to check or verify a scene, playtest, reproduce a bug, or confirm a change works. Godot's --headless mode draws nothing, so without this you're guessing at what the game shows.
---

# gdh: seeing and driving a Godot game

`gdh` runs Godot with Vulkan on the real GPU, on a virtual display of its own, so nothing appears on the user's desktop. `gdh capture` renders a scene once: six views, the engine errors and the probe findings. `gdh live` starts the game held at frame 0 and steps it an exact number of frames with input, so you can look between steps. `gdh movie` records a video, and `gdh editor` saves what the Godot editor shows. `gdh guide` prints a one-page cheat sheet.

Use it to check your own work. After you change a scene, material, shader or UI, capture it and look before telling the user it's done. After you change controls or gameplay, drive it live and measure. The user judges game feel (controls, pacing, fun). You cover whether the game renders correctly, runs without errors and behaves as specified.

The rest of this plugin: the `godot-editor` skill (changing files with the editor open, `gdh bridge`, UIDs), `godot-gdscript`, `godot-project`, `godot-export` (builds), and the `playtester` agent, which plays a scene against a goal, reports back and keeps what passed as a scenario.

## The fast loop

Each tool call costs you a turn, while a gdh command takes about 0.1 s. Do in one call what would take several:

```sh
gdh live start --project <dir> --session <name> [--scene res://...] [--recipe get-to-hangar.txt]
gdh live step --until "scene.name == 'Hangar'" --max 1200 --session <name>   # wait; exit 1 with the last value
gdh live step 120 --hold ui_right --trace "get_node('Player').position.x" --every 10 --session <name>
gdh live eval "[get_node('Player').position, GameState.score]" --session <name>   # several values at once
gdh live find Play --session <name>                    # visible nodes showing "Play", with their screen boxes
gdh live step 2 --click-text Play --session <name>     # or --click-node UI/Menu/Play: no coordinates
gdh live shot --node UI/Inventory --zoom 2 --out inv.png --session <name>   # that part of the frame, ready to read
gdh live batch --session <name> < steps.txt            # CLI lines, one process; --stop-on-error
gdh live reload --session <name>                       # after a GDScript edit: new code, same game state
gdh live restart --replay --session <name>             # after any other change: rebuilt, replayed to the same frame
gdh live save-scenario --session <name> test/scenarios/door.scenario.json   # keep it; gdh scenario run replays it
gdh live stop --session <name>
```

- **Read stderr.** Engine errors (with a script's backtrace), `DEFECT:` lines, notes and what the game printed (`game: ...`) go to stderr, results to stdout. Never add `2>/dev/null`. `--strict` (or `GDH_STRICT=1`) exits 1 when the game raised engine errors.
- **One session per task**, with a name of your own (another agent may be using `default`), and always `gdh live stop` it: a running game keeps the GPU busy. `gdh live list` shows every session, left-over ones included.
- **View every image you report or publish.** Saying a capture shows something you haven't opened is a guess.
- **Keep what you verified.** A saved scenario replays frame-exactly with `gdh scenario run` (and under `gdh test` in `res://test`): add `expect` checks to it. From Python, `from gdh.client import Session` drives a game over one pipe. See `${CLAUDE_PLUGIN_ROOT}/docs/scenarios.md`.
- **Give a big game a debug autoload:** a node whose methods return what you check as dictionaries (`Debug.state()`), and jump to a state (`Debug.goto("hangar")`). `eval` and `--until` call it, recipes and scenarios start from it, and it's the only way `eval` sees C# objects.

## Setup

Check that `gdh --help` works. If it doesn't, the source is the plugin root, `${CLAUDE_PLUGIN_ROOT}` (two directories above this skill): install it once, with the user's agreement (`uv tool install "${CLAUDE_PLUGIN_ROOT}"`), or run it in place (`uv run --project "${CLAUDE_PLUGIN_ROOT}" gdh ...`). It needs Linux, Godot 4.x as `godot` (or `GODOT=/path/to/godot`), a Vulkan driver, weston and Xwayland, and Xvfb; a C# project also needs `godot-mono` and the .NET SDK.

- **Builds and imports.** gdh builds a C# project when its code changed and imports a project whose import cache is missing or stale, before `capture` and `live start`, so don't run `dotnet build` or a Godot import yourself (`gdh import` does it, for instance after changing only import settings). A `DEFECT: ... failed to load` line means the game drew without those resources: report it, never treat it as log noise.
- **Window size.** `--resolution` is checked: if the game sizes its own window otherwise, `capture` exits 1 and `live start` refuses, naming the size it found.
- **Displays.** The game runs on a display the GPU presents to (weston and Xwayland), at full speed even at 4K. If that can't start, gdh falls back to Xvfb with a note: Xvfb is slow at high resolutions and GPU times read high, so say so when you report timings. Screenshots are the same on both.
- **User data.** Games under gdh keep `user://` in gdh's own directory, never the player's. `start --user-data fresh` or `--user-data-from DIR` gives a session its own.

## Choosing a mode

| Situation | Use |
|---|---|
| A scene, level, material, lighting or UI layout was changed | `gdh capture`, then read everything it produced |
| Did a change alter only what it should? | `gdh capture --baseline`, or `gdh measure diff A B` |
| Movement, input, animation, physics, timers, scene changes, UI interaction | `gdh live` |
| A bug that shows up after doing something | `gdh live`: reproduce it step by step, then save it as a scenario |
| Flicker, popping or jitter over time | `gdh live step N --shot-every K` to look; `gdh live measure flicker` or `shimmer` for a number |
| A thin effect's width, a point's size, a NaN, crushed blacks, a dissolve | `gdh measure line`, `spots`, `black`, `crush`, `dissolve` |
| Frame time against a budget, or which render pass costs what | `gdh live bench`; `gdh live start --gpu-passes`, then `gdh live frames` |
| Leaks, sound | `gdh live monitors --leak`, `gdh live audio` |
| An exported build, a launcher, anything where `OS.has_feature("editor")` matters | `gdh live start --binary PATH`; `gdh export --smoke` |
| What the editor shows: tool scripts, `@tool` previews | `gdh editor` |
| Footage: a clip, a trailer shot, a video of a bug | `gdh movie` |
| Logic with no picture to check: rules, save data, maths | `gdh test` (GUT, gdUnit4, or gdh's own runner) |
| A long play-through against a goal, kept out of the main conversation | the `playtester` agent |

## Capture

```sh
gdh capture --project <dir> --scene res://path/scene.tscn --out <dir>/captures/<name>
```

Repeat `--scene` for several scenes, each in its own subdirectory; `--modes normal,wireframe` renders only some views, which is faster. It prints each finding with a crop path, and saves `normal.png` (the frame as the player sees it), `unshaded.png`, `lighting.png`, `normals.png`, `wireframe.png` and `overdraw.png` (3D only), `report.json` (GPU, `errors` merged with counts, `findings`, render stats), `crops/` (a zoomed crop per finding; `--tiles` adds four 2× tiles) and `godot.log`.

**Baselines.** `--baseline <dir> --update-baseline` once, then `--baseline <dir>`: it exits 1 naming each changed view and its box. Unshaded and normals views make steadier baselines than the lit frame; give the lit frame a `--tolerance`. Baselines belong to one machine. For two frames you already have, `gdh measure diff A B --out <dir>` gives the share changed, its box and `crop.png`: trust its numbers over comparing by eye.

## Live control

```sh
gdh live start --project <dir> --session <name> -- --level 3       # arguments after -- go to the game
gdh live status --session <name>
gdh live step 30 --hold ui_right --session <name>                  # exactly 30 frames, then held again
gdh live shot --session <name> [--view wireframe]
gdh live tree [NodePath] [--visible-only] --session <name>
gdh live probes --session <name>
```

- **Time.** The game is held between commands, so take as long as you need. Each frame is one physics tick, and game seconds are frames divided by ticks per second (`status`): a player at 120 px/s moves exactly 60 px in 30 frames at 60 ticks. Nodes with process mode ALWAYS or WHEN_PAUSED (listed by `status`) run while held. If the game turns V-Sync on, steps run at about 60 frames a second, and a note says so.
- **Input.** An INPUT is an Input Map action (`ui_right`, `jump`), a key (`key:Space`) or shortcut (`key:ctrl+s`), a gamepad button (`joy:a`), or `mouse:left|right|middle` at the pointer. `--press` keeps it pressed, `--release` lets go, `--hold` presses for the whole step, `--tap` for one frame. Also `--click X,Y`, `--right-click`, `--left-hold`, `--right-hold`, `--move X,Y` (a drag is `--move`, `--press mouse:left`, steps with `--move`, `--release mouse:left`), `--type TEXT` (a character a frame, into the focused field), `--wheel down:3` (`--wheel-at X,Y`), `--mod ctrl,shift` (held for the step, carried by its clicks, keys and wheel), `--axis left_x=0.5` (a stick stays until moved), `--touch X,Y` and `--touch-drag X,Y:X,Y`, and `--look DX,DY` (mouse-look; `--look=-40,0` for a negative DX). Input arrives as a player's does, so `_input` and `is_action_just_pressed` see it.
- **Coordinates** are screenshot pixels everywhere; prefer `--click-text` and `--click-node`, which survive a layout change.
- **`eval`** evaluates one Godot Expression with the current scene as its base; `scene`, `tree`, `root`, every autoload and the engine's singletons are available. It can't assign with `=`: use `set("prop", value)` or call a method.
- **Starting where you need to be.** `--recipe FILE` runs batch lines once the game is ready; `--seed N` makes `randi()` repeat (restart keeps the seed); `start --replay <out>/inputs.jsonl` replays a saved session. `gdh live reload` keeps state but cancels a pending `await`, and doesn't reload scenes, resources or C#: use `restart --replay` for those.
- **Companions and instances.** `--companion 'NAME=COMMAND'` starts a program beside the game (a server) and stops it with the session: `{port}` is a free port, `{NAME.port}` passes it to the game's arguments, `--companion-ready NAME=http://127.0.0.1:{port}/health` waits for it. `--instances N` runs N games that step together; input goes to `--instance K`, and `eval`, `shot` and `tree` take `--instance K` or `all`.
- **Spawned processes and builds.** What the game spawns runs on its display and ends with the session; `--keep-children` keeps it for a launcher that hands off (watch it, but start the real game directly to step it). An exported build, a launcher or any X program runs as it is with `start --binary PATH [-- args]`: `wait --log REGEX`, `shot`, `input --click X,Y --type T --key Return --shot`, `status`, `stop`. `gdh export --preset Linux --smoke 10` is the quick release check.

## Movies

```sh
gdh movie --project <dir> --out <dir>/captures/clip --seconds 10 --resolution 3840x2160 [--scene res://...] [-- game args]
```

It writes `movie.mp4` (H.264 and AAC, every frame at a fixed rate with the audio), `sheet.png` and `report.json`. Read `sheet.png` first: it shows whether the clip shows what was wanted. A `covered` warning means a UI panel sat over the middle of the screen for most of the run. gdh refuses a project with an `override.cfg` of its own: tell the user rather than moving it. See `${CLAUDE_PLUGIN_ROOT}/docs/movie.md`.

## Measuring

Numbers settle what eyes can't. Every measure reads PNGs (files or directories, in name order); `${CLAUDE_PLUGIN_ROOT}/docs/measure.md` defines each.

```sh
gdh live record 90 --out <dir>/frames --session <name> [--hold ui_left]   # step 90 frames, save each
gdh measure flicker <dir>/frames          # still camera: anything over 0 changes on its own
gdh measure shimmer <dir>/frames          # moving camera: the second difference over time
gdh measure line shot.png --from X,Y --to X,Y   # a thin line's width at half maximum, its peak
gdh measure spots shot.png --radius 6     # each isolated point's width, sigma and peak
gdh measure black <dir>/frames --fail     # pure black cut into something lit: a NaN
gdh measure crush shot.png --mask hull.png      # pixels at the tone mapper's floor in a region
gdh live measure shimmer --frames 60 --session <name>  # record and measure in one go
gdh live bench 600 --budget-p99 8.3 --session <name>   # time 600 frames; exit 1 over budget
```

- **Compare like with like:** a shot against the same shot changed one way, never two different shots. `term WITHOUT WITH` measures what one setting adds.
- **Timing.** Time budgets in a session started without `--gpu-passes`, which inflates times (start with it, then `gdh live frames`, to see what each pass costs). Re-time if the summary names other games on the machine. A game's own pass shows when it calls `RenderingDevice.capture_timestamp("Name")`. Under Xvfb the GPU idles between frames, so say which display ran.
- **Leaks and sound.** `gdh live monitors --leak` exits 1 when nodes, orphan nodes, objects, resources or video memory grow steadily; `step N --monitors` shows a step's change. `gdh live audio` after a step gives each bus's peak and what played; mixing runs in real time, so step a few hundred frames first.
- **`black` and `crush` have blind spots:** a black object on purpose in front of something lit reads as a NaN's hole, and which regions should hold detail is yours to choose (`--box`, `--mask`).

## Looking at images

Read PNGs with the Read tool. This is where you catch what logs miss, so don't skip it.

- **Keep each image at about 1280 px wide or less** (`shot --max-width 1280`). Compare frames one at a time or as the same crop of each, never tiled into one image: scaling creates streaks and blotches that aren't in the frame.
- **Contact sheets are for what's on screen, not for pixels.** `sheet.png` (`gdh movie`), `<dir>-sheet.png` (`live record`, `live measure`) and `gdh measure sheet` show which screen was up when (a dialog left open, a loading screen, black); open single frames for anything finer.
- **Zoom in for small things.** A floating object, a blurry sprite or a misaligned icon is obvious at 2–4×: the crops in `crops/`, `--tiles`, or `shot --node PATH --zoom 3` / `--crop X,Y,W,H --zoom 3`. `--no-ui` leaves the HUD out, and `gdh live camera --view X,Y,Z:X,Y,Z` looks from anywhere in 3D (`--release` gives the game its camera back).
- **Debug views affect 3D only.** In a 2D or UI scene all six images are identical: use `find`, `tree` boxes and zoomed shots.

| View | Reveals |
|---|---|
| `unshaded` | Albedo with no lighting: a color or texture problem told apart from a lighting one |
| `lighting` | Light only: shadow acne (fine stripes), light leaks, missing shadows |
| `normals` | Surface direction as color: flipped faces, and smoothed hard edges as gradients on flat faces |
| `wireframe` | Triangles: holes, stray or stretched triangles, meshes far too dense for their size |
| `overdraw` | How often each pixel is drawn: stacked transparency, hidden geometry |

## The editor

```sh
gdh editor --project <dir> --scene res://a.tscn [--scene res://b.tscn] --out <dir>/captures/editor
```

It runs `godot --editor` with gdh's harness as its main loop, so the project's tool scripts, plugins and importers run as they do for the user, and saves `viewport.png` (the 3D or 2D viewport), `editor.png` (the whole window) and `report.json` (errors while the scene opened, how long it took, idle redraws: a tool script writing every frame keeps the editor redrawing, the cost of a redraw, the nodes each tool script made). `--orbit=DX,DY`, `--zoom STEPS`, `--focus NODE` and `--view X,Y,Z:X,Y,Z` frame the view; `--set NODE:PROPERTY=VALUE` and `--select NODE` change what's shown, in memory only. `editor.json` lists `project_changes`, files the editor wrote (often `project.godot`): gdh never undoes them, so revert what the user didn't ask for. See `${CLAUDE_PLUGIN_ROOT}/docs/editor.md`.

## Probes

The probes check scene data for likely defects: objects floating above surfaces, a rolled camera, geometry cut off by the far plane, material values outside 0–1, a texture with alpha drawn opaque, zero shadow bias, mirrored `Label3D` text, pixel art blurred by linear filtering, smeared sprite regions, raw translation keys and placeholder text, and one UI item out of line with its siblings. Treat a `warning` as a strong lead and confirm it on its crop; an `info` is often intended. An empty list doesn't mean the scene is fine, so still look. `references/probes.md` has what each measures and where it's weak.

## Checking a Godot API name

The installed Godot may be newer than your training data, and Godot 3 names (`KinematicBody2D`, `instance()`, `yield`) slip in easily. Look up what you aren't sure of in the installed version's reference: `gdh api CharacterBody2D` (its chain and members), `gdh api CharacterBody2D.move_and_slide` (one member), `gdh api --search floor`, `gdh api @GDScript`. A wrong name exits 1 with close matches.

## Reporting to the user

- **Verified and unverified:** say what you verified and how ("stepped 30 frames holding right; the player moved 60 px, which matches 120 px/s"), and what you didn't check.
- **Evidence:** the paths of the screenshots or crops that show a problem or a fix, and the scenario that replays it. If a file-sending tool is available, send the key image.
- **Game feel:** leave it to the user. Say which parts need a human playtest: feel, pacing, input latency, sound.

## References

- `references/probes.md`: each probe, its thresholds and its blind spots
- `${CLAUDE_PLUGIN_ROOT}/docs/live.md`: live control in full, batches, the protocol for scripts, restart and replays, `--binary` sessions
- `${CLAUDE_PLUGIN_ROOT}/docs/scenarios.md`: scenario files, `gdh scenario run` and the Python client
- `${CLAUDE_PLUGIN_ROOT}/docs/measure.md`: every measure, diffs and baselines, frame times, bench, monitors and audio
- `${CLAUDE_PLUGIN_ROOT}/docs/editor.md`: how `gdh editor` drives the Godot editor
