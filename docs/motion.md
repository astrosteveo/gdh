# Seeing motion

An image reader sees one still frame at a time, and a contact sheet of 16 frames shows that something changed but not how it moved. These commands turn motion over many frames into one image:

| Command | What it shows |
|---|---|
| `gdh live step N --trace EXPR --trace-chart F.png` | Values over time as a line chart, with velocity and acceleration (`--trace-rates`) ([live.md](live.md#waiting-and-tracing)) |
| `gdh live onion N --node PATH` | A node's movement as an onion skin: its frames laid over each other, the oldest faintest ([below](#onion-skins)) |

Each live command steps the game with the same input options as `step` (`--hold`, `--press`, `--click`, `--axis` and the rest), so the movement can be one the player makes. A recording of frames saved earlier (`gdh live record`, or any game's PNGs) works with the `gdh measure` forms.

The node a command frames is a path from the current scene, or `/root/...`. Its box on each frame covers the node and its visible descendants, since a `Node2D` or `Node3D` is usually drawn by its children (a sprite, a mesh). The image covers the box the node moved through over the run, plus `--margin` pixels each side (12 by default). A node with nothing drawn under it is a point, framed with at least 32 pixels each side. Frames where the node is hidden don't count.

## Onion skins

```sh
gdh live onion 40 --node Player --hold ui_right --session s               # 40 frames of a run, about 8 of them
gdh live onion 24 --node Player/Sword --every 2 --tint --press attack --session s
gdh measure onion frames/jump --every 3 --box 300,200,700,520 --out jump-onion.png
```

`gdh live onion N --node PATH` steps N frames and saves every Kth (`--every K`; by default about 8 frames spread over the run). It frames the box the node moved through and lays those frames over each other in one image. The background is the median of the frames, each pixel's middle value over the run, which is the scene behind whatever moved while the camera held still. Each frame's pixels that differ from that background by more than `--threshold` (12, of 255) are drawn over it in turn, the oldest at a quarter opacity and each later one more solid, up to the newest at full opacity. Each frame's game frame number is printed at the middle of what moved in it, when one blob holds most of it.

So one image shows a whole movement:

- **Its path**, and whether it's the arc or the line it should be.
- **Its spacing.** Frames taken at even intervals are evenly spaced for constant speed, bunched where it slows (easing out, an overshoot settling) and spread where it speeds up.
- **Its shape over time**: a swing's arc, a squash and stretch, a turn.

`--tint` tints each ghost by its age, from blue (oldest) to orange, with the newest untinted, which helps when the ghosts overlap a lot. `--zoom Z` scales the box up Z times (nearest neighbour, so no blur of its own); by default it's about 640 pixels on its long side, and never more than 1280 pixels wide. `--out FILE.png` names the image (default `<session out>/motion/onion-FIRST-LAST.png`). The frames it saved are deleted unless `--keep`. It prints the image, the frames it took, the box and the zoom; `--json` prints them as JSON, with each frame's `moving_share` (the share of the box that differs from the background) and the session's status.

**The camera has to hold still.** A camera that follows the node keeps it in one place on screen while the world moves past it, so the onion skin shows the node standing still. A camera that moves makes the whole background differ from frame to frame; when most of the box differs in most frames, a note on stderr says so. Hold the camera still with `gdh live camera --view ...` for the run, or turn off the game's camera follow.

`gdh measure onion FRAMES... --out FILE.png` does the same from saved PNG frames (files, or directories taken in name order), every `--every K` of them (default every one), in `--box X0,Y0,X1,Y1` (default the whole frame). Its frames are numbered from 0 in the order given. An onion skin takes at most 24 frames, since more can't be told apart.

## Tests

`tests/test_motion.py` runs `testbed/motion/motion.tscn`, where Ball moves right 4 pixels a physics frame, Spinner turns 6 degrees a frame in place, Blinker changes color every 10 frames and the rest stands still; with `follow`, the camera keeps Ball at the screen's centre. It checks:

- a trace chart's panels, and their numbers: Ball's position, its rate (240 pixels a second at 60 ticks a second) and the rate of that (0); true and false drawn as steps with no rates; text left out with a note; `gdh measure chart` from the CSV `--trace-out` wrote
- an onion skin of Ball: about 8 frames, the box exactly Ball's body over the distance it moved plus the margin, the newest frame solid, the oldest faint, and the frames it saved deleted
- `onion` with step's input, `--tint` and `--keep`; a node that isn't there, one that's hidden for the whole run, and `--every` longer than the run
- `gdh measure onion` over frames `gdh live record` saved, and the note when the background moves
