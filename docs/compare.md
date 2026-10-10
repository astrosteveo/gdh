# Options side by side

Polish is full of choices of taste: how bright the bloom, how strong the shake, which of three palettes. These commands show the options next to each other in one image, so the choice takes a look instead of a run of screenshots.

| Command | What it shows |
|---|---|
| `gdh live sweep "TARGET=V1,V2,V3" [...]` | The same frame with each value of one property, or two for rows and columns ([below](#sweeps)) |
| `gdh capture --matrix [SCREENS] --quality PRESETS` | A scene at several screen shapes and quality settings, each a capture with its probe findings ([below](#screens-and-quality-capture---matrix)) |
| `gdh review LABEL=PATH... --out DIR --wait` | A page with the options side by side for the user to pick from, and their choice and notes read back ([below](#asking-the-user-gdh-review)) |

## Sweeps

```sh
gdh live sweep "get_node('World').environment.glow_intensity=0.4, 0.8, 1.2, 1.6" --session s
gdh live sweep "get_node('Sun').light_energy=0.5, 1, 2" "get_node('Sun').rotation_degrees:x=-30, -60" --session s
gdh live sweep "get_node('Player/Sprite').modulate=Color(1, 1, 1), Color(1, 0.8, 0.8)" --node Player --margin 40 --session s
gdh live sweep "get_node('Player').jump_velocity=-500, -600, -700" --record 40 --every 2 --as onion --node Player \
    --press jump --reset "Debug.goto('jump_test')" --session s
```

`sweep TARGET=V1,V2...` sets a property of the running game to each value in turn, saves the frame, and lays the frames out in one labeled grid, at most 1280 pixels wide. With two sweeps, the first gives the rows and the second the columns, each cell one pair of values. Afterwards every property is put back to the value it had before the sweep, and the output says how many.

- **TARGET** is `OBJECT.PROPERTY`: OBJECT is an expression, as `eval` takes it (`get_node('Sun')`, `get_node('World').environment`, an autoload's name), and a bare `PROPERTY` is the current scene's. `PROPERTY:part` sets a part of it, as Godot's `set_indexed` does (`position:x`, `rotation_degrees:y`). An object without the property is refused before anything is set.
- **Each value** is an expression: a number, `true`, `Color(1, 0.8, 0.8)`, `Vector2(2, 2)`, a string in quotes. Values are separated by commas, and the commas inside brackets and quotes don't split them.
- **The frame** doesn't move on: the game is held, so each cell is the same moment with other values. `--view` picks the view (`normal` by default), `--crop X,Y,W,H` a part of the screen, and `--no-ui` leaves the UI out. `--node PATH` frames the cells on a node: the box round it with every value, grown by `--margin` (24 px), the same for every cell, so a size or position that changes shows as one. One sweep lays its cells `--columns` (4) across.

The grid goes to `--out FILE.png` (default `<session out>/sweep/sweep.png`), and each cell's image at full size into `FILE-cells/`, numbered in the grid's order. Cells are scaled to fit the grid, so read a cell's own image for fine detail. `--json` lists each cell's label, values and image.

**Settings that show in motion** (a camera's smoothing, a jump's height, a particle's lifetime) need more than a frame. `--record N` steps N frames for each cell, with any of `step`'s input options (`--press jump`, `--hold ui_right`), saving every `--every K` frame, and makes the cell a contact sheet of 9 of them, or with `--as onion --node PATH` an onion skin of the node's movement ([motion.md](motion.md#onion-skins)). Each cell's recording goes on from where the one before ended, unless `--reset EXPR` runs an expression before each cell that puts the game back in the same state (a debug autoload's `Debug.goto('jump_test')`, or setting a position); without it, a note says so.

## Screens and quality: `capture --matrix`

```sh
gdh capture --project game --scene res://levels/hub.tscn --out captures/hub --matrix
gdh capture --project game --scene res://ui/main_menu.tscn --out captures/menu --matrix 16:9,21:9,phone --modes normal
gdh capture --project game --scene res://levels/hub.tscn --out captures/hub --matrix 16:9 --quality low,medium,high
gdh capture --project game --scene res://levels/hub.tscn --out captures/hub --matrix deck --set rendering/scaling_3d/mode=2
```

`--matrix` captures a scene at each screen, by each `--quality`, as a capture of its own (its views, `report.json` with the probe findings, crops, logs) in `OUT/<screen>-<quality>/`, and lays their `normal` views out in `OUT/matrix.png`: a row for each screen, a column for each quality, each cell labeled with its size and its warnings, or FAILED. `OUT/matrix.json` lists every cell: its screen, size, quality, directory, whether it went through, its warnings and findings, and the settings it ran with. With several `--scene`s, each gets its own `OUT/<scene>/`. A UI that doesn't fit a phone, a HUD cut off at 21:9 or a probe that only fires at low quality shows in one image.

**Screens** are window sizes, comma-separated: `16:9` (1280x720), `16:10` (1152x720), `21:9` (1720x720), `32:9` (2560x720), `4:3` (960x720), `deck` (1280x800), `phone` (720x1280), `phone-landscape` (1280x720), `tablet` (1024x768), or any `WxH`. The default is `16:9,21:9,4:3,deck,phone`. The game's own stretch settings decide how it fills each, which is what the matrix shows. A game that sizes its own window fails the cell that differs, as `--resolution` does.

**Qualities** are sets of project settings, comma-separated: `project` (the project's own settings, the default), `low`, `medium` and `high`. Godot reads these settings only from the project, so gdh writes them into an `override.cfg` beside `project.godot` for each run and removes it once the run ends; a project that has an `override.cfg` of its own is refused, as for `gdh movie`. `--set SECTION/KEY=VALUE` (repeatable) adds a setting to every cell, on top of its quality: a number, `true` or `false`, a Godot value written as one (`Color(0.1, 0.1, 0.1, 1)`), or text.

| Setting | `low` | `medium` | `high` |
|---|---|---|---|
| `rendering/scaling_3d/scale` | 0.5 | 0.75 | 1.0 |
| `rendering/anti_aliasing/quality/msaa_3d` | 0 | 0 | 2 |
| `rendering/anti_aliasing/quality/screen_space_aa` | 0 | 1 | 0 |
| `rendering/lights_and_shadows/directional_shadow/size` | 1024 | 2048 | 4096 |
| `rendering/lights_and_shadows/directional_shadow/soft_shadow_filter_quality` | 0 | 2 | 3 |
| `rendering/lights_and_shadows/positional_shadow/soft_shadow_filter_quality` | 0 | 2 | 3 |
| `rendering/environment/ssao/quality` | 0 | 1 | 2 |
| `rendering/environment/ssil/quality` | 0 | 1 | 2 |
| `rendering/environment/glow/use_high_quality` | false | the project's | true |

The values are Godot's: `msaa_3d` 2 is 4x MSAA, `screen_space_aa` 1 is FXAA, a shadow filter quality runs from 0 (hard) to 5 (ultra), and an SSAO or SSIL quality from 0 (very low) to 4 (ultra). "The project's" leaves that setting as the project has it.

`--modes` picks the views each cell saves (all six by default; `--modes normal` is quicker), and `--warmup`, `--timeout`, `--display` and the game's arguments after `--` apply to every cell. `--baseline` doesn't go with `--matrix`.

## Asking the user: `gdh review`

```sh
gdh review Warm=captures/warm.png Cool=captures/cool.png --settings "Warm=tint 1.2, 0.9" --out captures/tint --wait --open
gdh review --from captures/sweep/sweep.json --out captures/glow-review --wait
gdh review --from captures/hud/matrix.json --out captures/hud-review --question "Which screens look wrong?"
gdh review --read captures/tint
```

A choice of taste is the user's. `review` makes the page they choose on: each option an image or a video (`LABEL=PATH`, or a `PATH` labeled by its file's name), with the settings that made it under it (`--settings LABEL=TEXT`), side by side, the question (`--question`) and title (`--title`) at the top, and a notes box. They pick by clicking or with the keys 1 to 9, and save. `--from FILE.json` takes the options from a sweep's cells (`gdh live sweep` writes `FILE.json` beside its grid) or a matrix's (`matrix.json`), each with its values or its screen, size and settings; long settings fold under their first line.

The page and copies of the files go into `--out DIR` (`index.html` and `media/`), so it opens anywhere, and a video plays in it. Getting the choice back:

- **`--wait`** serves the page on 127.0.0.1 (`--port`, default a free one), prints its address, and waits up to `--timeout` seconds (1800) for the user to save. The page posts the choice with a token only it has, gdh writes it to `DIR/choice.json` and prints it (`--json`: all of it), and stops. `--open` opens the page in the browser. With no choice in time it exits 1, leaving the page.
- **Without `--wait`**, the page is a file: saving downloads `choice.json`, which the user puts in the page's folder.

`gdh review --read DIR` prints a choice saved there: the label chosen and the notes. `choice.json` holds `choice`, `notes`, `options` (the labels shown, in order), the chosen option's `settings`, and `saved_at`.

## Tests

`tests/test_compare.py` runs `testbed/motion/motion.tscn`, with Ball (a `Node2D` drawn by its 32 px red `Body`) held still. It checks:

- a sweep of Body's color by Ball's scale: 2 by 2 cells labeled with their values, every cell framed on the same 104 px box round Ball at its biggest, red then blue, four times the red pixels at twice the scale, and both properties put back
- one sweep of 5 rotations wrapping to 2 rows of 3, its text output and the rotation put back
- `--record` with onion-skin cells and a reset, and the note without a reset
- a sweep that isn't one, one value, a property the object doesn't have (nothing set), `--reset` without `--record`, `--as onion` without `--node`
- how values and targets are split
- a matrix of `motion.tscn` at 16:9, phone and 1000x500 by the project's quality and `low`, with a clear color set by `--set`: 6 cells in order, each at its size with the clear color it was given, `low`'s settings recorded, the grid within 1280 px, and no `override.cfg` left behind
- an unknown screen or quality, `--quality` without `--matrix`, a `--set` that isn't a setting, and `--matrix` with `--baseline`
- how screens, qualities and settings are read and written
- a review page of two images with their settings and copies of the files, and `--read` before anything is saved
- `--wait`: the page served, a post with the wrong token refused, the right one saved into `choice.json` and printed, and `--read` printing it after
- a review from a sweep's JSON, and the errors: one option, a missing file, a file that isn't an image or a video, and settings for a label that isn't there
