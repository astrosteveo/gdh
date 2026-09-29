---
name: gdh
description: See and drive a Godot 4 game on Linux. Render scenes off-screen on the real GPU, read screenshots and debug views (wireframe, normals, lighting, overdraw), get engine errors and automatic defect checks, and play the game frame by frame with scripted input. Use this whenever you build, change or debug anything in a Godot project, including scenes, levels, materials, shaders, UI, sprites, cameras, player controls and gameplay logic. Also use it when the user says something looks wrong, asks you to check or verify a scene, playtest, reproduce a bug, or confirm a change works. Godot's --headless mode draws nothing, so without this you're guessing at what the game shows.
---

# gdh: seeing and driving a Godot game

`gdh` runs Godot under Xvfb with Vulkan on the real GPU, so nothing appears on the user's desktop. It has two modes:

- `gdh capture` renders a scene once. You get six views, the engine errors and the probe findings.
- `gdh live` starts the game held at frame 0. You step it an exact number of frames with input, and you can look at it between steps.

Use it to check your own work. After you change a scene, material, shader or UI, capture it and look before telling the user it's done. After you change controls or gameplay, drive it live and measure. The user judges game feel (controls, pacing, fun). You cover whether the game renders correctly, runs without errors and behaves as specified.

## Setup

Check that `gdh --help` works. If it doesn't, the source is in the plugin root, `${CLAUDE_PLUGIN_ROOT}`. That's two directories above this skill's base directory if the variable isn't set. Either install it once, with the user's agreement, or run it in place:

```sh
uv tool install "${CLAUDE_PLUGIN_ROOT}"                 # puts gdh on PATH
uv run --project "${CLAUDE_PLUGIN_ROOT}" gdh --help     # no install
```

Requirements are Linux, Godot 4.x as `godot` on PATH (or set `GODOT=/path/to/godot`), Xvfb, and a Vulkan driver. After adding or changing assets such as textures or models, run `gdh import --project <dir>` so Godot imports them before capturing.

## Choosing a mode

| Situation | Use |
|---|---|
| A scene, level, material, lighting or UI layout was changed | `gdh capture` |
| "Does this look right?", or checking a scene for defects | `gdh capture`, then read everything it produced |
| Movement, input, animation, physics, timers, scene changes, UI interaction | `gdh live` |
| A bug that shows up after doing something | `gdh live`: reproduce it step by step |
| Flicker, popping or jitter over time | `gdh live step N --shot-every K` |

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
- **Stop when done.** Always run `gdh live stop`, because a running game keeps the GPU busy. It quits by itself after 30 idle minutes.
- **Time.** The game is held between commands, so take as long as you need. `step N` runs exactly N frames, and each frame is one physics tick. Game seconds are frames divided by ticks per second, shown in `status`. This makes measurements exact. For example, a player at 120 px/s moves exactly 60 px in 30 frames at 60 ticks per second.
- **Input.** An INPUT is an action from the Input Map, such as `ui_right` or `jump`, or a key such as `key:Space`.
  - `--press`: press and keep pressed.
  - `--release`: release.
  - `--hold`: press for the whole step.
  - `--tap`: press for one frame.
  - `--click X,Y`: left click.

  Input arrives the way a player's does, so `_input`, `is_action_just_pressed` and `is_action_pressed` all see it.
- **Coordinates** are screenshot pixels everywhere. `tree` gives each node's `screen` position (`[x, y, w, h]` for Controls), so click at the center of what `tree` reports.
- **`eval`** evaluates one Godot Expression, with the current scene as its base. `scene`, `tree`, `root`, every autoload by name and the engine's singletons (`OS`, `Engine`, `Input`, `Time`...) are available. It can't assign with `=`. Use `set("prop", value)` or call a method instead.
- **Errors.** Every reply lists the engine errors raised since the previous command. Read them after every step. They're often the real bug.
- **Nodes that run while held.** `status` lists nodes with process mode ALWAYS or WHEN_PAUSED. They keep running while the game is held, so account for them when measuring.

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
