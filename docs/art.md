# Animation and art checks

Some problems don't show in a still frame: an animation that never gets past its first frame, a state machine flipping between two states, a foot that slides along the ground, a mesh that pops into view. These commands look for them over a run.

| Command | What it checks |
|---|---|
| `gdh live anim N --node PATH` | An `AnimationPlayer`'s or `AnimationTree`'s state, position and blend parameters frame by frame, with restarts and flip-flops flagged ([below](#animation-timelines)) |
| `gdh live footslide N --skeleton PATH` | Feet that slide along the ground while they're planted ([below](#foot-sliding)) |

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
