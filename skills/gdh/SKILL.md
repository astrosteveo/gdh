---
name: gdh
description: See and drive a Godot 4 game on Linux with gdh. Render scenes off-screen on the real GPU and read screenshots, debug views (wireframe, normals, lighting, overdraw), engine errors and automatic defect checks; play the game frame by frame with scripted input, click UI by its text and keep playthroughs as replayable scenarios; see what the Godot editor shows a scene as; see motion in one image; and measure game feel (input latency, jumps, hit-stop), frame times, GPU cost, flicker, leaks and audio. Use this whenever you build, change or debug anything in a Godot project (a folder with project.godot), including scenes, levels, materials, shaders, UI, sprites, cameras, player controls and gameplay logic. Also use it when the user says something looks or feels wrong, asks you to check, verify or playtest, reproduce a bug, or confirm a change works. Godot's --headless mode draws nothing, so without this you're guessing at what the game shows.
---

# gdh: seeing and driving a Godot game

`gdh` runs Godot with Vulkan on the real GPU, on a virtual display of its own, so nothing appears on the user's desktop. `gdh capture` renders a scene once: six views, the engine errors and the probe findings. `gdh live` starts the game held at frame 0 and steps it an exact number of frames with input, so you can look between steps. `gdh movie` records a video, and `gdh editor` saves what the Godot editor shows. `gdh guide` prints a one-page cheat sheet.

Use it to check your own work before you tell the user it's done. The user judges whether the game feels good (controls, pacing, fun). You cover whether it loads without errors, renders correctly and behaves as specified, and you measure what feel is made of: latency, speeds, jumps, hit-stop, shake. For each change:

1. **It loads.** The hooks hand back Godot's errors for each `.gd` you write: fix them first. A `.tscn` or `.tres` isn't always checked, and Godot drops a misspelt property in one without a word; the `godot-editor` skill says how to check them.
2. **It draws right.** `gdh capture` each scene you made or changed. Read its errors, `DEFECT:` lines and findings, then open `normal.png`, and in 3D the debug views ([Looking at images](#looking-at-images)).
3. **It behaves right.** After a change to controls, gameplay or UI flow, drive it with `gdh live` and measure: a position before and after, a frame count, a value read with `eval`.
4. **It shows in the editor.** The user works in the Godot editor, so run `gdh editor --scene` for each scene you made or changed and look at `viewport.png` ([below](#the-editor)). A scene whose script builds everything at run time is empty there, and the user can't inspect or change it; the `godot-project` skill says how to build scenes that show.
5. **Keep it, and say what you checked.** If you drove it, save that as a scenario with checks, so it replays as a regression test. Report what you verified and how, and what you didn't ([Reporting](#reporting-to-the-user)).

Load this plugin's other skills before the work each covers, not only when something goes wrong: `godot-project` before making or restructuring a scene, a screen or a system; `godot-gdscript` before writing GDScript; `godot-editor` before changing a `.tscn`, `.tres`, script, shader or project setting, and whenever the editor may be open (`gdh bridge`, UIDs); `godot-export` for builds. The `playtester` agent plays a scene against a goal, reports back and keeps what passed as a scenario.

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
- **Let the game tell you what happened:** `gdh events install --project <dir>` adds `GdhEvents`; a call to `GdhEvents.emit("died", data)` where something happens comes back with its frame on stderr during any step, and `gdh live events` lists and counts them. It does nothing outside gdh, so the calls can stay in.
- **Give a big game a debug autoload:** a node whose methods return what you check as dictionaries (`Debug.state()`), and jump to a state (`Debug.goto("hangar")`). `eval` and `--until` call it, recipes and scenarios start from it, and it's the only way `eval` sees C# objects.

## Setup

Check that `gdh guide` works. If it doesn't, or a command or option this skill names is missing, the `gdh` on PATH is missing or older than this plugin. The plugin's own copy is at `${CLAUDE_PLUGIN_ROOT}` (two directories above this skill): run it in place (`uv run --project "${CLAUDE_PLUGIN_ROOT}" gdh ...`), or, with the user's agreement, install it (`uv tool install --reinstall "${CLAUDE_PLUGIN_ROOT}"`). It needs Linux, Godot 4.x as `godot` (or `GODOT=/path/to/godot`), a Vulkan driver, weston and Xwayland, and Xvfb; a C# project also needs `godot-mono` and the .NET SDK.

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
| How something moves: its path, timing, easing, overshoot; a pose, flash or transition frame by frame | `gdh live onion`, `step --trail`, `gdh live filmstrip`, `step --trace-chart` ([below](#seeing-motion)) |
| Whether it holds up on other screens and settings: ultrawide, a phone, a Steam Deck, low quality | `gdh capture --matrix [16:9,21:9,phone] --quality low,high`: one grid, each cell a capture with its probe findings |
| Animation bugs: frozen in its first pose, states flipping, feet sliding, limbs through walls | `gdh live anim`, `footslide`, `clipping` (`${CLAUDE_PLUGIN_ROOT}/docs/art.md`) |
| Pop-in along a camera path; a character lost against the background; a shot drifting from the concept art | `gdh measure popin` on a recording; `gdh live contrast --node PATH`; `gdh measure palette SHOT --reference ART` |
| A choice of taste: which value of a setting looks best | `gdh live sweep "OBJ.PROP=V1,V2,V3"`: one grid of the options; then `gdh review --from SWEEP.json --out DIR --wait` for the user to pick, and their choice comes back (`${CLAUDE_PLUGIN_ROOT}/docs/compare.md`) |
| Responsiveness and movement: input latency, speeds, jumps, coyote time, hit-stop, shake, camera lag, sounds in sync | `gdh live latency`, `feel`, `hitstop`, `shake`, `follow`, `soundsync` (`${CLAUDE_PLUGIN_ROOT}/docs/feel.md`) |
| What moves in a scene that should be still, or what's frozen that should move | `gdh live record`, then its `<dir>-changes.png`, or `gdh measure changes --still` |
| Flicker, popping or jitter over time | `gdh live step N --shot-every K` to look; `gdh live measure flicker` or `shimmer` for a number |
| A thin effect's width, a point's size, a NaN, crushed blacks, a dissolve | `gdh measure line`, `spots`, `black`, `crush`, `dissolve` |
| Frame time against a budget, or which render pass costs what | `gdh live bench`; `gdh live start --gpu-passes`, then `gdh live frames` |
| Which script functions a slow frame spent its time in | `gdh live start --profiler`, then `gdh live profile N`: the game's functions by self and total time, overall and on the spike frames |
| Leaks, sound | `gdh live monitors --leak`, `gdh live audio` |
| An exported build, a launcher, anything where `OS.has_feature("editor")` matters | `gdh live start --binary PATH`; `gdh export --smoke` |
| What the editor shows the user: each scene you made or changed, tool scripts, `@tool` previews | `gdh editor` |
| Footage: a clip, a trailer shot, a video of a bug | `gdh movie` (its `audio.png` shows the sound: waveform, spectrogram, clipping, frame by frame) |
| Logic with no picture to check: rules, save data, maths | `gdh test` (GUT, gdUnit4, or gdh's own runner; the `godot-gdscript` skill has a test file's shape) |
| A multiplayer game: a server beside it, several players, lag, drops and reconnects | `gdh live start --companion`, `--instances N` and `--net NAME`, then `gdh live net` (`${CLAUDE_PLUGIN_ROOT}/docs/live.md`: Companions, Several instances, A worse network) |
| Where the game breaks over a long run: stuck spots, soft locks, holes, leaving the level | `gdh live explore N --node PATH` (at random, or `--goal X,Y`): each problem a replayable scenario, and a heat map (`${CLAUDE_PLUGIN_ROOT}/docs/explore.md`) |
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

- **Time.** The game is held between commands, so take as long as you need. Each frame is one physics tick, and game seconds are frames divided by ticks per second (`status`): a player at 120 px/s moves exactly 60 px in 30 frames at 60 ticks. Nodes with process mode ALWAYS or WHEN_PAUSED (listed by `status`) run while held. A game that pauses itself during a step (a pause menu) stays paused across steps until it unpauses, as for a player: `status` says `paused by the game itself`, and `get_tree().paused` reads true while held either way. A pause made while held (by `eval` or `set`, or in the game's `_ready`) isn't kept: the next step unpauses the game, so open a menu with a step's input. If the game turns V-Sync on, steps run at about 60 frames a second, and a note says so.
- **Input.** An INPUT is an Input Map action (`ui_right`, `jump`), a key (`key:Space`) or shortcut (`key:ctrl+s`), a gamepad button (`joy:a`), or `mouse:left|right|middle` at the pointer. `--press` keeps it pressed, `--release` lets go, `--hold` presses for the whole step, `--tap` for one frame. Also `--click X,Y`, `--right-click`, `--left-hold`, `--right-hold`, `--move X,Y` (a drag is `--move`, `--press mouse:left`, steps with `--move`, `--release mouse:left`), `--type TEXT` (a character a frame, into the focused field), `--wheel down:3` (`--wheel-at X,Y`), `--mod ctrl,shift` (held for the step, carried by its clicks, keys and wheel), `--axis left_x=0.5` (a stick stays until moved), `--touch X,Y` and `--touch-drag X,Y:X,Y`, and `--look DX,DY` (mouse-look; `--look=-40,0` for a negative DX). Input arrives as a player's does, so `_input` and `is_action_just_pressed` see it.
- **Coordinates** are screenshot pixels everywhere; prefer `--click-text` and `--click-node`, which survive a layout change.
- **A click that can't land.** `--click-text` and `--click-node` fail without stepping when another control would take the click (`can't be clicked at X,Y: UI/Fade (ColorRect) is over it and takes the click (mouse_filter Stop)`). That's a bug to report: a transparent panel, a fade or a menu left up eats players' clicks too. `--click X,Y` clicks anyway and says where the click went (`went to ...`).
- **`eval`** evaluates one Godot Expression with the current scene as its base; `scene`, `tree`, `root`, every autoload and the engine's singletons are available. It can't assign with `=`: use `set("prop", value)` or call a method.
- **Reading the UI without a screenshot.** `gdh live snapshot [PATH]` prints what's on screen to read or use as an outline (`GoButton (Button) "Go" [disabled]`, a slider's `80/100`, the focus), cheaper than a shot and exact. `--baseline FILE` checks it hasn't changed (`--update-baseline` writes it); a scenario's `{"snapshot": NAME, "baseline": true}` does the same.
- **A record of the session.** `start --timeline` keeps every command, with its frames, errors, notes, values and a thumbnail of the frame after it, in `<out>/timeline/index.html` (a page that opens from the disk). Use it for a playtest someone will check, or one you'll want to look back through; point the user at it in your report.
- **Starting where you need to be.** `--recipe FILE` runs batch lines once the game is ready; `--seed N` makes `randi()` repeat (restart keeps the seed); `start --replay <out>/inputs.jsonl` replays a saved session. `gdh live reload` keeps state but cancels a pending `await`, and doesn't reload scenes, resources or C#: use `restart --replay` for those.
- **Multiplayer.** A server beside the game (`--companion`), several games stepping together (`--instances N`, then `--instance K` on each command) and a worse network between them (`--net`, `gdh live net`) each have a section in `${CLAUDE_PLUGIN_ROOT}/docs/live.md`. Network delays are in real time, so test them with `gdh live run`, wait, `gdh live pause`.
- **Other languages.** `--locale pseudo` on `capture` or `live start` makes every text 40% longer with accents (Godot's pseudolocalization), and the `text_overflow` probe lists what's cut off or spills out; `--locale fr` uses a real translation.
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

## Seeing motion

You see one still frame at a time, so a movement read off a few shots is a guess. Turn it into one image, and numbers:

```sh
gdh live step 90 --hold ui_accept --trace "get_node('Player').position" --trace-chart jump.png --trace-rates --session <name>
gdh live onion 40 --node Player --hold ui_right --session <name>        # the frames over each other, oldest faintest
gdh live step 60 --hold ui_right --trail Player --every 5 --session <name>   # its path; px between dots printed
gdh live filmstrip 24 --node Player/Sprite --press attack --session <name>  # the same box from each frame
gdh measure changes <dir>/frames --out changes.png --still              # where a run changed, and how often
```

- **Spacing is timing.** In an onion skin or a trail, frames at even intervals sit evenly apart for constant speed, bunch where it slows (easing out, a landing) and spread where it speeds up. A trail prints the pixels between its dots, and `--trace-rates` charts velocity and acceleration: report those numbers, not an impression.
- **Hold the camera still for an onion skin.** A following camera keeps the node in place on screen; an onion skin then shows it standing still, and a note says when the background moves. A trail doesn't need it: it shows the path through the world, seen through the last frame's view.
- **A filmstrip for what overlaps:** an attack's poses, a one-frame hit flash, a fade. Its cells are scaled up by whole factors, so read it as it is.
- **A change map for a scene that should be still:** each region is something moving (an animation left running, a flickering light); with `--still`, green on something that should animate means it's frozen.

See `${CLAUDE_PLUGIN_ROOT}/docs/motion.md`.

## Looking at images

Read PNGs with the Read tool. This is where you catch what logs miss, so don't skip it.

- **Keep each image at about 1280 px wide or less** (`shot --max-width 1280`). Compare frames one at a time or as the same crop of each, never tiled into one image yourself: scaling creates streaks and blotches that aren't in the frame. gdh's filmstrips and onion skins scale only up, by whole factors, so they don't.
- **Contact sheets are for what's on screen, not for pixels.** `sheet.png` (`gdh movie`), `<dir>-sheet.png` (`live record`, `live measure`) and `gdh measure sheet` show which screen was up when (a dialog left open, a loading screen, black); open single frames for anything finer.
- **Name what you see.** `shot --annotate` saves the frame again with each node that draws boxed and numbered, and lists each number's node path: a stray sprite, a misplaced panel or an overlap leads straight to the node to change. `--annotate collisions,nav,velocity` (or `all`) adds collision shapes (to compare with what's drawn), navigation and velocity arrows; `--filter class:Enemy`, `group:NAME` or a node path keeps a busy scene readable. `gdh live pick X,Y` answers "what's this pixel?": every node drawn there, topmost first, with a sprite's texture and texel or a mesh's surface and material.
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

The installed Godot may be newer than your training data, and Godot 3 names (`KinematicBody2D`, `instance()`, `yield`) slip in easily. Look up what you aren't sure of in the installed version's reference: `gdh api CharacterBody2D` (its chain and members), `gdh api CharacterBody2D.move_and_slide` (one member), `gdh api --search floor`, `gdh api @GDScript`, and an annotation with its `@` (`gdh api @GDScript.@export_range`). A wrong name exits 1 with close matches. The properties it lists are the names a `.tscn` or `.tres` uses, and its theme properties are how a scene overrides a control's look (the `godot-editor` skill: Writing scenes and resources).

## Reporting to the user

- **Verified and unverified:** say what you verified and how ("stepped 30 frames holding right; the player moved 60 px, which matches 120 px/s"), and what you didn't check.
- **Evidence:** the paths of the screenshots or crops that show a problem or a fix, and the scenario that replays it. If a file-sending tool is available, send the key image.
- **Game feel:** give the numbers you can measure (`gdh live latency`, `feel`, `hitstop`, `shake`, `follow`: frames, speeds, heights, windows) against the target the user gave, and leave the judgment of whether it feels good to them. Say which parts need a human playtest: pacing, fun, sound.

## References

Read the one you need when this skill isn't enough. `docs/live.md` is long (about 700 lines): list its sections with `grep -n '^##' ${CLAUDE_PLUGIN_ROOT}/docs/live.md` and read the one you need.

- `references/probes.md`: each probe, its thresholds and its blind spots
- `${CLAUDE_PLUGIN_ROOT}/docs/live.md`: live control in full: input, finding and clicking nodes, snapshots, framing and annotated shots, the debug autoload, events, the timeline, companions, instances and the network, batches, the protocol for scripts, restart and replays, `--binary` sessions
- `${CLAUDE_PLUGIN_ROOT}/docs/scenarios.md`: scenario files, `gdh scenario run` and the Python client
- `${CLAUDE_PLUGIN_ROOT}/docs/test.md`: `gdh test`, its frameworks, gdh's own runner and its report
- `${CLAUDE_PLUGIN_ROOT}/docs/measure.md`: every measure, diffs and baselines, frame times, bench, monitors and audio
- `${CLAUDE_PLUGIN_ROOT}/docs/feel.md`, `motion.md`, `art.md`: game feel, motion in one image, animation and art checks
- `${CLAUDE_PLUGIN_ROOT}/docs/compare.md`, `explore.md`, `movie.md`: sweeps, matrices and the user's pick; the exploring bot; movies
- `${CLAUDE_PLUGIN_ROOT}/docs/editor.md`, `bridge.md`: how `gdh editor` drives the Godot editor, and the bridge
