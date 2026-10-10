# Options side by side

Polish is full of choices of taste: how bright the bloom, how strong the shake, which of three palettes. These commands show the options next to each other in one image, so the choice takes a look instead of a run of screenshots.

| Command | What it shows |
|---|---|
| `gdh live sweep "TARGET=V1,V2,V3" [...]` | The same frame with each value of one property, or two for rows and columns ([below](#sweeps)) |

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

## Tests

`tests/test_compare.py` runs `testbed/motion/motion.tscn`, with Ball (a `Node2D` drawn by its 32 px red `Body`) held still. It checks:

- a sweep of Body's color by Ball's scale: 2 by 2 cells labeled with their values, every cell framed on the same 104 px box round Ball at its biggest, red then blue, four times the red pixels at twice the scale, and both properties put back
- one sweep of 5 rotations wrapping to 2 rows of 3, its text output and the rotation put back
- `--record` with onion-skin cells and a reset, and the note without a reset
- a sweep that isn't one, one value, a property the object doesn't have (nothing set), `--reset` without `--record`, `--as onion` without `--node`
- how values and targets are split
