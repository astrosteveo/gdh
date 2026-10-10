# Seeing motion

An image reader sees one still frame at a time, and a contact sheet of 16 frames shows that something changed but not how it moved. These commands turn motion over many frames into one image:

| Command | What it shows |
|---|---|
| `gdh live step N --trace EXPR --trace-chart F.png` | Values over time as a line chart, with velocity and acceleration (`--trace-rates`) ([live.md](live.md#waiting-and-tracing)) |
| `gdh live onion N --node PATH` | A node's movement as an onion skin: its frames laid over each other, the oldest faintest ([below](#onion-skins)) |
| `gdh live filmstrip N --node PATH` | The same box round a node from each frame side by side, labeled with its game frame ([below](#filmstrips)) |
| `gdh live step N --trail PATH` | Where nodes went, drawn on the last frame with a dot every K frames, and the pixels between the dots ([below](#trails)) |

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

## Filmstrips

```sh
gdh live filmstrip 24 --node Player --hold ui_right --session s           # a walk cycle, about 12 frames of it
gdh live filmstrip 12 --node Enemy --every 1 --press attack --session s   # a hit flash, frame by frame
gdh measure filmstrip frames/door --every 2 --box 400,180,880,540 --out door.png
```

`gdh live filmstrip N --node PATH` steps N frames, saves every Kth (`--every K`; by default about 12 spread over the run), and puts the same box from each side by side in rows, left to right, each cell labeled with its game frame. The box is the one round where the node went over the whole run, as for an onion skin, and it's the same for every cell, so the node moving inside it shows as movement from cell to cell, and a node that stays put shows its pose, color or shape changing. Use it for what an onion skin can't separate: a walk or attack cycle's poses, a hit flash or a blink one frame long, a fade or a transition, or what a sprite sheet's frames look like in play.

Each cell is scaled up a whole number of times (nearest neighbour), by `--zoom Z` or, by default, to about 640 pixels on its long side, less to fit at least 4 cells across and to keep the whole image within about 1280 by 1280 pixels. A filmstrip takes at most 48 frames. The options `--margin`, `--out` (default `<session out>/motion/filmstrip-FIRST-LAST.png`), `--keep`, `--json` and the step's input options work as for `onion`. `gdh measure filmstrip FRAMES... --out FILE.png` does the same from saved PNG frames, with `--every`, `--box` and `--zoom`.

## Trails

```sh
gdh live step 60 --hold ui_right --trail Player --every 5 --session s           # the run, a dot every 5 frames
gdh live step 90 --tap ui_accept --trail Player --trail Camera --every 3 --trail-out jump.png --session s
```

`--trail PATH` (repeatable) on `step` records where each node is before the step's first frame and after every frame, then saves a shot of the last frame with each node's path drawn on it: a line through its positions, a dot every `--every K` frames (default 1) from where it started, and its first and last game frame numbered at the ends, the last with the node's name. Each node gets its own color, over a dark outline so it shows on light scenes too. The image goes to `--trail-out FILE.png`, or to the session's shots as `NNNN-trail.png`.

A node's position is a `Node2D`'s or `Node3D`'s global position, or a `Control`'s middle. The path is the one through the world, seen through the last frame's view: the node's canvas transform (with its camera or `CanvasLayer`) for a 2D node, its viewport's camera for a 3D one. So a camera that followed the node, which keeps it in one place on screen, still shows the path it took to get there, ending where the node is now. UI on a `CanvasLayer` stays where it is on screen.

The dots' spacing is the motion's timing. Dots at even frame intervals are evenly spaced for constant speed, bunch up where it slows (easing out, a landing) and spread where it speeds up. As text, `step` prints each trail's start and end and the pixels between consecutive dots:

```
trail: captures/live/s/shots/0007-trail.png
  Player: from 200,360 at frame 120 to 320,360 at frame 150; px between dots every 5 frames: 20, 20, 20, 20, 20, 20
```

With `--json` the reply gets `"trails": {"image", "every", "trails": [{"node", "dots": [[frame, x, y], ...], "spacing", "missing", "off_screen"}]}`, and the step's result holds every frame's point and box under `"track"`. A point off the frame is still drawn toward, with the line leaving the frame there; a point behind a 3D camera, or after the node was freed, has no place (`null`), and the line breaks there. Both are counted, with a note on stderr. A session of several instances draws one: give `--instance K`.

## Tests

`tests/test_motion.py` runs `testbed/motion/motion.tscn`, where Ball moves right 4 pixels a physics frame, Spinner turns 6 degrees a frame in place, Blinker changes color every 10 frames and the rest stands still; with `follow`, the camera keeps Ball at the screen's centre. In `motion3d.tscn`, Cube moves along x 0.05 a frame in front of the camera, and Ghost moves 0.25 a frame along z, from in front of the camera to behind it. It checks:

- a trace chart's panels, and their numbers: Ball's position, its rate (240 pixels a second at 60 ticks a second) and the rate of that (0); true and false drawn as steps with no rates; text left out with a note; `gdh measure chart` from the CSV `--trace-out` wrote
- an onion skin of Ball: about 8 frames, the box exactly Ball's body over the distance it moved plus the margin, the newest frame solid, the oldest faint, and the frames it saved deleted
- `onion` with step's input, `--tint` and `--keep`; a node that isn't there, one that's hidden for the whole run, and `--every` longer than the run
- `gdh measure onion` over frames `gdh live record` saved, and the note when the background moves
- a trail of Ball: a dot every 5 frames, 20 pixels apart, the first and last where Ball was, the line and dots drawn in the first trail's color; with the camera following Ball, the path through the world ending at the screen's centre; the text output, a UI node's trail, and `--trail-out` without a trail
- a filmstrip of Spinner: about 12 frames 3 apart, labeled with their game frames, within 1280 by 1280 pixels, every cell the same size and no two alike; one of Blinker 10 frames apart, its color alternating; `gdh measure filmstrip`, and too many frames
- 3D trails on `motion3d.tscn`: Cube's last point where the camera's own `unproject_position` puts it, and Ghost's points behind the camera missing, with the note
