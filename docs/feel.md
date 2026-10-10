# Game feel in numbers

Feel is mostly timing: how soon the game answers an input, how fast a character gets going and stops, how high and long a jump is, how forgiving its edges are, how long a hit freezes time and how hard the camera shakes. These commands measure each one in a live session, so feel can be tuned toward a target and checked again after a change, instead of judged by eye from a few screenshots.

| Command | What it measures |
|---|---|
| `gdh live latency --press INPUT --watch EXPR --node PATH` | Frames from an input to the first change in a value and in a node's pixels ([below](#latency)) |

Frames are physics frames: gdh runs a game at one physics tick a frame, so at 60 ticks a second a frame is 16.7 ms. Each command prints its numbers; `--json` has every trial and sample. They step the game, so run them where the game is in the state to measure: on the floor, in a level, past the menus.

## Latency

```sh
gdh live latency --press ui_accept --watch "get_node('Player').velocity.y" --node Player --session s
gdh live latency --press attack --node Player/Sprite --repeat 10 --session s
gdh live latency --press key:Escape --node UI/PauseMenu --session s
```

`latency --press INPUT` presses an input (an action, `key:NAME`, `mouse:left`, `joy:NAME`, as `step` takes them) and counts the frames until the game answers it, in two ways:

- **`--watch EXPR`**: the frames until the value of EXPR (an expression, as `eval` takes) first differs from what it was before the input: the game's state answering.
- **`--node PATH`**: the frames until the node's box on screen (round it and what it draws, grown by `--margin`, 4 px) first differs from the frame before the input, by more than `--threshold` (8, of 255) in at least 4 pixels: the player seeing it.

With both, it also gives the gap: the frames the pixels came after the state, such as an animation that starts late or a sprite updated a frame behind its body. Each trial settles first, `--settle` frames (10) with no input, then presses the input for one frame (`--hold`: for the whole trial) and steps up to `--max` frames (30). It runs `--repeat` trials (5) and prints the least, median and most, in frames and milliseconds:

```
latency of ui_accept over 5 trials, at 60 frames a second:
  state (get_node('Hero').pressed_at): 1 frame (17 ms)
  pixels (UI/Lamp): 4 frames (67 ms)
  pixels after state: 3 frames (50 ms)
```

A response on the first frame after the input counts as 1 frame. The count ends at the first frame drawn that shows the change, so the display adds its own delay on a real screen (a frame or more of buffering and the monitor's own), which gdh can't see. Anything else that changes the node's box (an idle animation, a blink) counts as a response too, so frame the part that answers, or hold the rest still. `--json` lists each trial: its start frame, the frames to the state and to the pixels, the value before and after, the box and the pixels changed. `--keep` keeps the frames saved for `--node`.

## Tests

`tests/test_feel.py` runs `testbed/feel/feel.tscn`. Hero is a platformer character with known numbers at 60 frames a second, on a floor that ends at x 800. Each press of `ui_accept` sets `Hero.pressed_at` at once and lights `UI/Lamp` 3 frames later. It checks:

- latency: the state after 1 frame and the lamp after 4 in every trial, a gap of 3; the text output; no response within `--max`; and the error without `--watch` or `--node`
