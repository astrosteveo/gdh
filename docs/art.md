# Animation and art checks

Some problems don't show in a still frame: an animation that never gets past its first frame, a state machine flipping between two states, a foot that slides along the ground, a mesh that pops into view. These commands look for them over a run.

| Command | What it checks |
|---|---|
| `gdh live anim N --node PATH` | An `AnimationPlayer`'s or `AnimationTree`'s state, position and blend parameters frame by frame, with restarts and flip-flops flagged ([below](#animation-timelines)) |
| `gdh live footslide N --skeleton PATH` | Feet that slide along the ground while they're planted ([below](#foot-sliding)) |
| `gdh live clipping N --node PATH` | A character's bones and mesh points inside solid geometry, frame by frame ([below](#clipping)) |
| `gdh measure popin FRAMES` | Pop-in in a recording: what appears all at once while the rest moves smoothly ([below](#pop-in)) |
| `gdh live contrast --node PATH` | How well a node stands out from what's round it: a luminance contrast ratio and a color difference ([below](#readability)) |
| `gdh measure palette SHOT --reference IMAGE` | A shot's brightness, contrast, saturation and colors against a reference or a mood board ([below](#matching-a-look)) |

## Animation timelines

```sh
gdh live anim 120 --node Player/AnimationTree --hold ui_right --session s
gdh live anim 60 --node Player --tap attack --session s       # the first AnimationTree under Player, else its AnimationPlayer
```

`anim N --node PATH` steps N frames, with any of `step`'s input options, and records what the animation is doing after each frame. PATH is an `AnimationTree` or `AnimationPlayer`, or a node with one under it: the first tree, else the first player.

- **An `AnimationPlayer`**: the animation playing, its position in seconds, and whether it plays.
- **An `AnimationTree`**: each state machine's current state and its position, and every number among the tree's parameters that a game sets (blend positions, blend amounts, time scales, conditions). The tree's own read-only bookkeeping (each node's current position, length and delta) is left out.

It prints each state machine's state and its changes (`root state: idle, then 1 change: move at 212`), and each parameter's range, and draws a chart (`--chart FILE.png`, default `<session out>/feel/anim/anim.png`): each state as steps, its states numbered in the panel's title, and each position and parameter as a line. `--json` has every value frame by frame.

It warns about two bugs that a still frame can't show:

- **An animation started again every frame.** The same animation, playing, not past where it was the frame before and within a frame and a half of its start, `--restarts` frames in a row (3). Something calls `play()` after `stop()`, or `seek(0)`, every frame, so it never gets past its start: the character looks frozen in its first pose.
- **A state machine flipping back and forth.** A state machine that goes to a state and back within 6 frames, `--flips` times (3). Two transitions whose conditions are both true, or code that travels to one state and another in turn, so neither ever plays.

## Foot sliding

```sh
gdh live footslide 120 --skeleton Player/Armature/Skeleton3D --hold ui_up --session s
gdh live footslide 120 --skeleton Hero/Skeleton3D --bones "mixamorig:LeftFoot,mixamorig:RightFoot" --session s
```

A foot that slides while it's on the ground is the most common sign that an animation and the character's movement don't agree: a walk played too slowly for the speed, root motion ignored, a blend at the wrong point. `footslide N --skeleton PATH` steps N frames, with any of `step`'s input options, and follows the feet of a `Skeleton3D` in the world.

- **The feet** are guessed from the bones' names: a bone whose name has `foot` (else `ankle`, `toe` or `ball`) and `left` or `l` (`foot.L`, `LeftFoot`, `mixamorig:LeftFoot`, `Foot_L`), and the same for the right. `--bones L,R` names them; one bone is fine.
- **Planted** is within `--height` (0.02 m) of the ground: the lowest the foot gets over the run, or `--ground Y`.
- **Sliding** is a planted foot moving along the ground (in x and z) faster than `--threshold` (0.1 m/s) between two planted frames.

For each foot it prints the frames it was planted, how far it slid while planted, how many frames it slid faster than the threshold, and its worst speed and frame:

```
footslide of Walker/Skeleton over 80 frames, feet foot.L, foot.R:
  foot.L: planted 42 of 80 frames, and slid 0.74 m while planted: 37 frames faster than 0.1 m/s, 1.2 m/s at worst (frame 202)
  foot.R: planted 42 of 80 frames, and slid 0.76 m while planted: 38 frames faster than 0.1 m/s, 1.2 m/s at worst (frame 222)
crops: captures/live/s/feel/footslide/footslide.png
```

With a slide, it saves crops of the `--crops` (3) worst frames side by side, each 160 px round the foot on screen, zoomed 3 times and marked: each foot's worst first, then the next worst of any (`--out FILE.png`; `--crops 0` saves no frames). A slide as fast as the character walks means the foot moves with the body: the stride doesn't cover the ground.

## Clipping

```sh
gdh live clipping 240 --node Player --hold ui_up --session s          # an arm through a wall, a cape through the floor
gdh live clipping 120 --node Enemy --mask 1 --fail --session s       # only layer 1; exit 1 if anything clipped
```

`clipping N --node PATH` steps N frames, with any of `step`'s input options, and tests a character's points against the solid collision shapes around it after every frame:

- **The points** are every bone of each `Skeleton3D` under the node, and the vertices (up to 200 of each) of each `MeshInstance3D` under it that isn't skinned. A skinned mesh's vertices move with its bones on the GPU, where gdh can't read them, so its bones stand for it.
- **The shapes** are those on `--mask` (every layer by default), less the collision bodies under the node itself: the character's own capsule always holds its bones. A point is inside when the physics space's `intersect_point` says so, which finds it inside a box, sphere, capsule, convex polygon or height map. A concave trimesh has no inside, so a point inside one isn't found: give walls that matter a convex shape, or check them by eye.

The frames each point spent inside each body are grouped into runs, longest first:

```
clipping of Reacher over 120 frames (2 points tested a frame):
  hand inside Wall: frames 17 to 43 (27 frames), at 3.004,1,0
  hand inside Wall: frames 77 to 103 (27 frames), at 3.004,1,0
crops: captures/live/s/feel/clipping/clipping.png
```

With a run, it saves crops of the `--crops` (3) longest runs at their first frame, each 160 px round the point, zoomed and marked (`--out FILE.png`; `--crops 0` saves no frames). `--fail` exits 1 when a point was inside.

## Pop-in

```sh
gdh live record 300 --out captures/flythrough --session s      # a camera path through the level
gdh measure popin captures/flythrough
```

`measure popin FRAMES` reads a recording (PNG files, or directories taken in name order) from any game and looks for what appears or changes all at once: a mesh switching its LOD or appearing at the end of its visibility range, a texture streaming in, a light switching on. The recording should move smoothly, a camera on a path, as pop-in is what breaks that.

1. For each 24-pixel block (`--block`), it finds the shift of the frame before, up to 32 pixels each way, that best explains the block in this frame: what moved is explained, and what appeared isn't. The search runs at a quarter of the size, so it takes a few seconds for a hundred frames.
2. A block pops when the change that no shift explains is over `--ratio` (4) times its typical change in the 3 frames before and after, and over it by at least `--floor` (12) luminance levels, and stays changed: 2 frames on, its brightness is still at least half the jump away from the frame before. Something passing through the block leaves it as it was.
3. Blocks that pop together in a frame are one pop, listed with the frame (the recording's file index from 0), its box and its change against the change around it, the biggest first. A frame where most of the screen (30%) changes at once is a cut or a flash, and is listed as one.

```
pop-in over 120 frames: 1 pop, 0 cuts
  frame 80: the box 576,312,696,408 changed by 70.62 against 0 in the frames around it  [captures/flythrough-popin/01-pop.png]
```

Each of the 10 biggest pops gets a crop in `<frames>-popin/` (`--out DIR`; `--no-crops` for none): the frame before, the frame and their difference amplified. `--fail` exits 1 when something popped. Very fast motion, more than 32 pixels a frame (a post right beside the camera), can't be followed and can leave a few levels unexplained; the floor of 12 keeps that out. A lower `--floor` finds subtler pops, such as a texture's detail level switching, with more of those false ones.

## Readability

```sh
gdh live contrast --node Player --node Enemy --node Pickups/Coin --session s
gdh live contrast --node UI/Crosshair --min-ratio 4.5 --fail --session s
```

A player has to pick out the character, the enemies and what can be picked up at a glance, against whatever is behind them. `contrast --node PATH` (repeatable) measures it on the frame as it is: it shoots the frame, hides the node and shoots it again, and the pixels that differ are the node's (its children's included). The ring round them, `--ring` (12) pixels wide, is what it's seen against. Two numbers compare the node's median color with the ring's:

- **The contrast ratio**, WCAG's: the relative luminance of the brighter plus 0.05, over the darker's plus 0.05, from 1:1 (the same brightness) to 21:1 (white on black). It's what a player who can't tell the colors apart goes by, and what still shows in a dim room or on a washed-out screen. Under `--min-ratio` (3, the least WCAG asks of graphics that must be seen) is flagged: hard to pick out by brightness alone. `--fail` exits 1 then.
- **The color difference**, ΔE (CIE76, in L\*a\*b\*): about 2.3 is just noticeable, 10 plain, over 40 two different colors. A node with a low ratio and a high ΔE stands out by its hue only.

```
Bright: contrast 4:1 with the 12 px round it (at least 3), color difference 46.8 (node (255, 255, 255), round it (127, 127, 127))  [captures/live/s/readability/Bright-contrast.png]
Murky: contrast 1.04:1 with the 12 px round it (under 3: hard to pick out by brightness alone), color difference 9.7 (node (143, 127, 115), round it (127, 127, 127))  [captures/live/s/readability/Murky-contrast.png]
```

Each node gets a crop with its outline in white and its ring's in cyan, and the frame, in `--out DIR` (default `<session out>/readability`). `--no-ui` leaves the UI out of both shots. The node is shown again afterwards. Measure it where it matters: in the level's darkest and brightest places, in fog, behind effects.

## Matching a look

```sh
gdh measure palette captures/hub/normal.png --reference art/hub_concept.png --out captures/hub-vs-concept.png
gdh measure palette shot.png --reference board/01.jpg --reference board/02.jpg --reference board/03.jpg
```

`palette SHOT --reference IMAGE` compares a shot's look with a reference: concept art, a mood board (repeat `--reference`; its images are taken together), or an earlier shot. It gives, for each, the mean lightness (CIE L\*, 0 to 100), its spread (the contrast), the black and white points (the 2% darkest and brightest), the mean saturation, and the 6 dominant colors with their shares, and says how the shot differs in each:

```
  lightness (mean L*): 53.5 against 32.5, brighter by 21
  contrast (L* spread): 3.9 against 2.6, about the same
  black point (2% darkest, L*): 53.2 against 32.3, lifted blacks by 20.9
  saturation (mean): 0.001 against 0.001, about the same
  brightness histograms 21.96 L* apart (0: the same; under 3 hard to tell)
```

The histogram distance is the earth mover's distance between the two brightness histograms, in L\* units: how far the shot's brightness would have to move to match. `--out PNG` saves the two side by side, each with its dominant colors as a bar and its brightness histogram. Images over 2 million pixels are halved first. Compare like with like: the same view, or the reference's framing.

## Tests

`tests/test_anim.py` runs `testbed/anim/anim.tscn`: an `AnimationPlayer` looping "bob" and an `AnimationTree` whose state machine is "idle", or "move" (a blend space by `speed`) while `moving`, with a switch for each bug. It checks:

- the player's position moving on a frame (1/60 s) at a time, wrapping at its length, with no warnings
- the tree in "move" with its blend position, and only the parameters a game sets; a node above both finding the tree
- the restart bug flagged with the frames in a row, and the flip bug with the state's changes
- a node that isn't there, and one with no animation under it
- the rules for changes, flips and restarts on made-up values

On `testbed/anim/walker.tscn`, a `Skeleton3D` walking along x at 1.2 m/s with its feet `foot.L` and `foot.R` taking turns on a 40-frame stride (with `slide`, a planted foot moves with the body):

- the feet guessed, each planted about half the frames and not moving then
- with `slide`, each planted foot sliding at 1.2 m/s, 0.02 m a frame, and the crops saved; `--bones` with one foot
- a node that isn't there, one that isn't a skeleton, and a bone it doesn't have
- foot bones guessed from several naming styles

On `testbed/anim/clip.tscn`, a hand bone swinging into a static wall, and a recording of `testbed/anim/popin.tscn`, a camera gliding past posts toward a cube that appears on the 81st frame:

- clipping: two runs of 27 frames of the hand inside Wall, 60 frames apart, the character's own capsule left out, the crops; `--fail` with and without a run; a node that isn't there, and one with nothing to test
- pop-in: one pop, on file 80, round the middle of the view, and none from the posts moving; `--fail`
- pop-in on made-up frames: a texture panning 3 px a frame with a block appearing on frame 7 and a frame of black, found as one pop and two cuts

`tests/test_readability.py` runs `testbed/anim/readability.tscn`, three squares on a mid-grey backdrop: white, a brownish grey of the backdrop's brightness, and a grey a shade lighter drawn by a child. It checks:

- contrast: about 4:1 for white, under 1.1 and flagged for the other two, the brownish one with a plain color difference and the grey one without; the crops; every node shown again after
- `--fail`, and a hidden node, which draws nothing
- palette on made-up images: brighter than a darkened copy, more saturated than a grey copy, the same as itself, and a mood board taken together
- the color math: white's luminance and L\*a\*b\*, the contrast ratio's range, and the differences and histogram distance
