# Game feel in numbers

Feel is mostly timing: how soon the game answers an input, how fast a character gets going and stops, how high and long a jump is, how forgiving its edges are, how long a hit freezes time and how hard the camera shakes. These commands measure each one in a live session, so feel can be tuned toward a target and checked again after a change, instead of judged by eye from a few screenshots.

| Command | What it measures |
|---|---|
| `gdh live latency --press INPUT --watch EXPR --node PATH` | Frames from an input to the first change in a value and in a node's pixels ([below](#latency)) |
| `gdh live feel --node PATH` | A character's movement: top speed and the time to it, stopping, a jump's apex and airtime, air control, coyote time and the jump buffer ([below](#movement-feel)) |
| `gdh live hitstop --do EXPR --node PATH` | Frames where time slows or stops after a trigger, and a moving node freezes ([below](#hit-stop-shake-and-follow)) |
| `gdh live shake --do EXPR` | A camera shake's amplitude, frequency, length and decay ([below](#hit-stop-shake-and-follow)) |
| `gdh live follow --target PATH` | How far and how many frames a camera lags its target, its overshoot and settling ([below](#hit-stop-shake-and-follow)) |
| `gdh live soundsync N --event NAME` | Frames between a sound starting and what it belongs to ([below](#sound-in-sync-soundsync)) |

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

## Movement: `feel`

```sh
gdh live feel --node Player --session s
gdh live feel --node Player --right move_right --jump jump --skip coyote,buffer --session s
gdh live feel --node Player --reset "Debug.goto('ledge_test')" --session s
```

`feel --node PATH` measures a character the way a designer describes one, and draws a chart of its run and its jumps (`--chart FILE.png`, default `<session out>/feel/feel/feel.png`):

```
feel of Hero at 60 frames a second:
  run (ui_right): top speed 240 px/s, reached in 12 frames (200 ms) (90% in 11)
  stop: 14 px in 8 frames (133 ms) after letting go
  jump (held): apex 105 px after 20 frames (333 ms), airtime 42 frames (700 ms)
  jump (tapped): apex 105 px after 20 frames (333 ms), airtime 42 frames (700 ms)
  air control: speeds up at 600 px/s² in the air (on the ground: 1200), 240 px/s at most (on the ground: 240), 125.5 px covered from takeoff to landing
  coyote time: a jump pressed up to 6 frames (100 ms) after leaving the floor still jumps
  jump buffer: a jump pressed up to 5 frames (83 ms) before landing, the landing frame included still jumps
```

Each part starts from rest at the same place, and `--skip` leaves parts out (`run`, `jump`, `air`, `coyote`, `buffer`, separated by commas):

- **run**: holds `--right` (`ui_right`) for `--run-frames` (90), then lets go for `--stop-frames` (60). The speed is the distance along the ground between frames, per second. Top speed is the highest; the frames to it count to the first frame within 1% of it, and to 90%. Acceleration is the slope of a straight line through the speed until 90%. Stopping counts the frames and the distance from letting go until the speed falls under 1% of the top. A note says when it was still speeding up at the end.
- **jump**: presses `--jump` (`ui_accept`) from rest, held for the whole jump, then tapped for one frame, so a jump whose height depends on the hold shows both heights. Apex is the most height above the start, with the frames to it from the press; airtime is the frames from takeoff to landing (the first frame `is_on_floor()` is true again, or back to the start's height for a node without it). `--jump-frames` (120) is how long each jump is watched.
- **air**: the held jump again, with `--right` held too: how fast it speeds up along the ground in the air and how fast it gets, beside the run's numbers, and the distance from takeoff to landing.
- **coyote**: walks right until `is_on_floor()` is false (at most `--walk-max` frames, 600), steps k more frames still walking, then taps the jump, for k = 0, 1, 2 and on: it still jumps when it moves up within 4 frames. The coyote time is how many k in a row jumped. The character needs a ledge to its right; put it near one with `--reset`.
- **buffer**: finds the frame a tapped jump lands on, then for k = 0, 1, 2 and on presses the jump again k frames before that frame (0: on the landing frame itself), and checks that it jumps again just after landing. The buffer is how many k in a row did.

Both windows are tried up to `--max-window` frames (15). They need `is_on_floor()`, which a `CharacterBody2D` or `CharacterBody3D` has; without it, they're skipped with a note.

**Resets.** Before it starts, `feel` lets the character land (when it has `is_on_floor()`) and settle (`--settle` frames, 20), and takes that position as the start. Before each measure it puts the character back there with its velocity at zero, or runs `--reset EXPR` (an expression, as `eval` takes, such as a debug autoload's `Debug.goto('test_room')`) when given. A game's own timers aren't reset (a jump still buffered, a cooldown running), so after each reset `feel` waits for the character to land and settles again. A note says when the character never landed at the start.

In 3D, up is +y and the ground is the x-z plane; in 2D, up is -y. Distances are in pixels in 2D and metres in 3D. `--json` has every number and each run's speeds and heights frame by frame.

## Hit-stop, shake and follow

```sh
gdh live hitstop --do "get_node('Player').take_hit(10)" --hold ui_right --node Player --session s
gdh live shake --press attack --session s
gdh live follow --target Player --hold ui_right --frames 90 --session s
```

Each triggers an effect, watches it frame by frame and draws a chart (`--chart FILE.png`, default under `<session out>/feel/`). The trigger is `--do EXPR`, an expression run just before the first frame (as `eval` takes it, such as a method that deals a hit), and any of `step`'s input options over the first `--frames`: `--press`, `--hold`, `--tap`, `--click` and the rest. Set the scene up first: a character running, a camera at rest.

**`hitstop`** watches `Engine.time_scale` for `--frames` (30) frames after the trigger: the first frame it reads under 1 after, for how many frames, and the lowest value. With `--node PATH`, a node that's moving when it's hit, it also finds the frames the node stood still and then moved again: a hit-stop done by pausing nodes rather than the time scale shows there. Keep the node moving through it (`--hold ui_right`), or the freeze can't be told from standing still, which a note says. The two counts can differ by a frame: a game that puts the time scale back during a frame's `_process` reads 1 after that frame, though its physics didn't move in it.

```
hit-stop over 30 frames after the trigger, at 60 frames a second:
  Engine.time_scale: under 1 from frame 1 for 5 frames (83 ms), at least 0
  Hero: stood still from frame 1 for 6 frames (100 ms), then moved again
```

**`shake`** reads the camera (`--camera PATH`, default the viewport's current one) for `--frames` (60) frames: a `Camera2D`'s `offset`, or a `Camera3D`'s `h_offset` and `v_offset`, where the game shakes it that way, else its position on screen (`get_screen_center_position()` in 2D, its global position in 3D) less its moving average over `--window` (9) frames, which takes out the camera following something. For each axis that shakes: the most it moves, its RMS, its frequency (from the frames between its swings), how long it lasts, and how fast it decays: the frames from its biggest swing until the swings are under half of it and under a tenth.

```
shake of get_viewport().get_camera_2d() over 60 frames after the trigger, from its offset:
  x: 6.697 px at most (RMS 3.378), 10 Hz, for 28 frames (467 ms) from frame 2, down to half in 14 frames (233 ms) and a tenth in 26
```

**`follow --target PATH`** steps `--frames` (60) frames with the input that moves the target, then `--after` (60) with none, and compares the camera's position (its centre on screen in 2D) with the target's, with the gap between them at the start counted as none. It gives the lag in distance, at most and on average; the lag in frames, the delay that best lines the camera's path up with the target's; the overshoot, the furthest the camera goes past where the target stopped, along the way it moved; and the frames after the input ends until the camera stays within `--settled` (1 px or 1 m) of the target.

```
camera get_viewport().get_camera_2d() following Hero over 60 frames of input and 60 after:
  lag: 43.598 px at most, 21.061 on average; the camera's path is 10 frames (167 ms) behind the target's
  overshoot: 0 px past where the target stopped, settled within 1 px 48 frames (800 ms) after the input ended
```

## Sound in sync: `soundsync`

```sh
gdh live soundsync 600 --hold ui_right --event footstep_contact --session s
gdh live soundsync 300 --when "get_node('Player').is_on_floor()" --player Player/LandSound --session s
gdh live soundsync 200 --tap attack --event hit --max-offset 1 --fail --session s
```

A sound that comes a few frames after what it belongs to (a footstep after the foot lands, a hit after the impact) feels wrong long before anyone can say why. `soundsync N` steps N frames, with any of `step`'s input options, and pairs each time the visual event happens with the nearest sound starting within `--window` frames (15):

- **A sound starts** on the frame the game calls `play()`: each `play()` makes a new playback, and the harness notes every player whose playback isn't the one it saw the frame before. That's game time, exact even under gdh's dummy audio driver, which mixes in real time (so under a step a short sound can still be playing when it's played again). The players are every `AudioStreamPlayer`, `AudioStreamPlayer2D` and `AudioStreamPlayer3D` in the game, those spawned during the run included, or `--player PATH` (repeatable).
- **The visual event** is the frames an expression turns true (`--when EXPR`), or the game's own event (`--event NAME`, from `GdhEvents.emit(NAME)`, [live.md](live.md#the-games-events-gdhevents)).

For each event it prints the sound paired with it and its offset, the sound's frame less the event's (late when positive, early when negative), in frames and milliseconds; events with no sound near them; and sound starts with no event near them. An offset over `--max-offset` frames (2, about 33 ms at 60 a second) is flagged; `--fail` exits 1 then, or when an event has no sound.

On `testbed/audio/sync.tscn` with the thud put 6 frames after the landing:

```
sound against --when landed over 130 frames: 3 events, 3 sound starts
  event at frame 160: Thud 6 frames (100 ms) late  <- out of sync
  event at frame 200: Thud 6 frames (100 ms) late  <- out of sync
  event at frame 240: Thud 6 frames (100 ms) late  <- out of sync
warning: 3 of 3 sounds more than 2 frames off their event, 0 events with no sound
```

## Tests

`tests/test_feel.py` runs `testbed/feel/feel.tscn`. Hero is a platformer character with known numbers at 60 frames a second (it speeds up at 1200 px/s² to 240 px/s, 600 px/s² in the air, slows at 1800 px/s², jumps at 600 px/s under 1800 px/s² of gravity, with 6 frames of coyote time and a 5-frame jump buffer), on a floor that ends at x 800. Each press of `ui_accept` sets `Hero.pressed_at` at once and lights `UI/Lamp` 3 frames later. `hit()` stops time for 6 frames and shakes the camera 8 px along x at 10 Hz for 30 frames, decaying linearly; the camera follows Hero with position smoothing (speed 5). It checks:

- feel: 12 frames and 1200 px/s² to 240 px/s, 8 frames and 14 px to stop, the apex 20 frames up and about 105 px high, the same for a tapped jump, 600 px/s² in the air, a coyote time of 6 frames and a buffer of 5, all as Hero's constants give them; the text output and `--skip`; a node that doesn't move and has no `is_on_floor()`
- hitstop: `hit()` reading under 1 for 5 frames and Hero standing still for 6, and the note when Hero isn't moving
- shake: about 6.7 px at 10 Hz from the offset, lasting about 28 frames, half gone in about 14, nothing along y; and the numbers of a made-up sine
- follow: position smoothing at speed 5 lagging a 240 px/s Hero by up to 48 px and about a fifth of a second, with no overshoot, and the note when the target doesn't move
- soundsync (`tests/test_audio.py`, on `testbed/audio/sync.tscn`, a ball landing every 40 frames and a thud played as it lands): three landings, each with the thud on the same frame and none flagged, the hum that was already playing not counted; with the thud 6 frames late, each flagged and `--fail` exiting 1; `--player` listening to one player only; the errors; and the pairing rules on made-up frames
- latency: the state after 1 frame and the lamp after 4 in every trial, a gap of 3; the text output; no response within `--max`; and the error without `--watch` or `--node`
