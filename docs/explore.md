# Exploring: `gdh live explore`

A bot that plays for thousands of frames, unattended, and reports where the game goes wrong: where it got stuck, where nothing it pressed did anything, where it fell forever, where it left the level. Each problem is saved as a scenario that takes the game back to it, and the run ends with a heat map of where it went.

```sh
gdh live explore 6000 --node Player --session s                                   # at random, seed 1
gdh live explore 6000 --node Player --seed 4 --reset "Debug.goto('start')" --session s
gdh live explore 3000 --node Player --goal 2400,380 --session s                    # head for a point
gdh live explore 3000 --node Car --up accelerate --down brake --left steer_left --right steer_right --jump "" --session s
```

## How it plays

It plays in chunks of `--chunk` frames (15). Each chunk holds one direction, or none, and may tap the jump at a random frame in it:

- **Random** (the default): a direction among the inputs it has, or none, and the jump with a chance of `--jump-chance` (0.3), from `--seed` (1): the same seed plays the same way. The game's own random numbers are the game's: start the session with `--seed` for those.
- **Toward a goal** (`--goal X,Y`, or `X,Y,Z` in 3D): the direction that closes the larger gap to the goal, along x, and along y (2D, with `--up` and `--down`) or z (3D: up is -z, forward). When a chunk gets it no closer, it taps the jump, and now and then tries another direction. It heads straight for the goal, so a pit on the way is found, not jumped.

The inputs are `--left` (`ui_left`), `--right` (`ui_right`), `--up` and `--down` (none unless given), and `--jump` (`ui_accept`; `--jump ""` for none), each an action, `key:NAME` or `joy:NAME` as `step` takes them.

## What it finds

After every chunk it looks at where the node went (its `global_position`):

| Problem | When |
|---|---|
| stuck | Holding a direction for `--stuck` frames (90) and still within `--still` (8 px, 0.25 m in 3D) of where it was: a wall it can't get past, a ledge it can't climb, a corner it catches on. Jumping in place counts as getting nowhere. In goal mode: no closer to the goal for `--stuck` frames, whatever it tried, reported where it got closest. |
| soft lock | Within `--still` of where it was for `--softlock` frames (150), though it tried two directions or more: input that stopped working. |
| falling | Going down every frame for `--fall` frames (180): a hole with no bottom, a missing floor. Reported where the fall began. |
| out of bounds | Outside `--bounds X0,Y0,X1,Y1` (x and y; x and z in 3D). |

Each problem is saved in `--out DIR` (default `<session out>/feel/explore`) as `NN-KIND.scenario.json`: the session's input log up to that frame, so `gdh scenario run` replays the run to it frame for frame from a fresh game, with a check that the node is where the bot found it. The same problem in the same place again (within 64 px, or 2 m) is counted, not saved again. `--max-problems` (20) stops the run after that many.

After a fall or leaving the bounds, the node goes back to where it started, at rest, and the run goes on. A soft lock can't be undone that way (whatever locked the game is still set), so the run stops there, unless `--reset EXPR` is given: an expression that puts the game back (a debug autoload's `Debug.goto('start')`, or the properties to set), which then runs first, before the first chunk, and after every problem. `--reset-when-stuck` resets after getting stuck too, rather than letting the bot find its own way out.

```
explore of Explorer, random (seed 7), 3000 frames: 3 problems
  soft lock at frame 13473, at 296.666,379.967 (2 times there): captures/live/s/feel/explore/01-soft-lock.scenario.json
  falling at frame 13848, at 570,297.424 holding ui_left: captures/live/s/feel/explore/02-falling.scenario.json
  falling at frame 14688, at 619.999,381.425 (3 times there): captures/live/s/feel/explore/03-falling.scenario.json
  the game's events: fell 28, trapped 7, checkpoint 1
  reached x 296.67 to 620, y 297.42 to 643.92
heat map: captures/live/s/feel/explore/heat.png
```

**The heat map** (`heat.png`) shows where the node was, seen as the screen is (x and y) in 2D, or from above (x and z) in 3D: each cell by how many frames it spent there (on a log scale), the start ringed in green, the goal boxed in cyan and each problem crossed in red with its number. A fall's long drop is left off, so the level stays readable. **The game's events** (`GdhEvents.emit`, [live.md](live.md#the-games-events-gdhevents)) during the run are counted: a game that reports its deaths, checkpoints and pickups says what the bot did there. `--fail` exits 1 when it found a problem, for a nightly run.

## Tests

`tests/test_explore.py` runs `testbed/explore/explore.tscn`: a platformer on a floor with a checkpoint, a trap that soft-locks it, a pit with no bottom, and a wall too high to jump. It checks:

- at random (seed 7), with a reset: the soft lock in the trap and the falls beginning at the pit, the game's events counted, a scenario for each, and the heat map
- with no reset, on a fresh session: the run stops at the soft lock, and `gdh scenario run` replays its scenario to it and passes
- toward a goal past the pit: stuck at the wall, as close as it gets (x 1088); and nothing found in a short run
- a node that isn't there, goal mode with no goal, and a goal of the wrong size
