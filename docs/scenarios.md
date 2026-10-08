# Scenarios and the Python client

A scenario is a playthrough kept as a JSON file: the options its session starts with, the inputs, frame by frame, and checks along the way. `gdh scenario run` replays it in a live session and says which checks passed. gdh's steps are frame-exact (each frame is one physics tick, however long it takes to draw), so a replay reaches the same state every time, and a check that passed once fails only when the game changed. That makes a playtest a regression test.

```sh
gdh scenario run test/first_level.scenario.json                 # exit 1 when a check failed
gdh scenario run test/*.scenario.json --out captures/scenarios   # several, one after another
gdh scenario run test/menus.scenario.json --update-baselines     # write the checkpoint shots' baselines
gdh test --project game                                          # runs res://test(s)/**/*.scenario.json too
```

`gdh.client` drives a session from Python, for a playthrough that needs logic a file can't hold ([below](#the-python-client)), and `gdh live save-scenario` writes the inputs a client sent as a scenario to add checks to.

## A scenario file

```json
{
  "description": "The player walks right, clicks Go and jumps.",
  "start": {"scene": "res://live/arena.tscn", "resolution": "640x360", "args": ["--level", "3"]},
  "steps": [
    "step 30 --hold ui_right",
    {"expect": "get_node('Player').position.x", "approx": 260, "name": "half a second right is 60 px"},
    {"step": 2, "click_text": "Go"},
    {"expect": "clicks", "equals": 1},
    {"until": "get_node('Player').position.x >= 300", "hold": "ui_right", "max": 600, "every": 5},
    {"step": 1, "tap": "ui_accept"},
    {"shot": "after-jump", "baseline": true}
  ],
  "expect": ["GameState.jumps == 1"]
}
```

`testbed/scenarios/arena.scenario.json` is this file, run by the tests. The keys:

| Key | What it holds |
|---|---|
| `start` | `gdh live start`'s options by their long names, underscores for dashes: `"resolution": "640x360"` is `--resolution 640x360`, `"no_build": true` is `--no-build`, a list repeats the option (`"companion": ["server=..."]`). `project` is the project (default: the nearest folder holding the file with a `project.godot`), `scene` the scene (default: the main scene), and `args` the game's arguments (after `--`). `session` and `out` are the runner's |
| `steps` | What happens, in order: command lines and structured steps (below) |
| `expect` | Checks made after the last step, each as an `expect` step takes it, or a string, an expression that must be truthy |
| `name` | The scenario's name, for its results and output folder (default: the file's name without `.scenario.json`) |
| `baselines` | The folder of the checkpoint shots' baselines (default: `baselines/<name>` beside the file) |
| `allow_errors` | `true` lets the game raise engine errors without failing the scenario (default `false`) |
| `description` | Words for people |

Paths in a scenario (`start.project`, a baseline, a line's `--out`, start options that name files) are from the file's own folder: the runner runs each scenario there.

### Steps

A string is a `gdh live` command line, as `gdh live batch` takes it ([live.md](live.md#batches-gdh-live-batch)): `"step 30 --hold ui_right"`, `"eval GameState.debug = true"`, `"camera --view 0,40,80:0,0,0"`, `"record 60 --out frames"`. `#` starts a comment. Every command but `start`, `batch` and `pipe` can be a line.

An object is a structured step, named by its one kind key:

| Step | What it does |
|---|---|
| `{"step": N, ...}` | Steps N frames (`null` with `until`). Its input takes `gdh live step`'s options by name, underscores for dashes, each a value or a list: `press`, `release`, `hold`, `tap` (an action, `key:NAME`, `mouse:left`, `joy:a`), `type`, `move`, `click`, `right_click`, `left_hold`, `right_hold` (`"X,Y"` or `[x, y]`), `click_text`, `click_node`, `wheel`, `wheel_at`, `mod`, `axis`, `touch`, `touch_drag`, `look`; and `until`, `every`, `max`, `trace`, `shot_every`, and `events` (the raw protocol's) |
| `{"until": EXPR, ...}` | Steps until EXPR is truthy, checked every `every` frames (default 1), at most `max` frames (default 3600), with input as `step` takes it. A check: it passes when EXPR held |
| `{"expect": EXPR, ...}` | A check of EXPR's value, below |
| `{"shot": NAME, ...}` | A checkpoint shot, below |
| `{"request": CMD, "args": {...}}` | One request of the raw protocol ([live.md](live.md#protocol)), with an optional `timeout` |

Any step also takes `name`, the name its check is reported by, and `instance`, the game instance it goes to in a session of several.

### Checks

| Check | Passes when |
|---|---|
| `{"expect": EXPR}` | EXPR's value is truthy: not `false`, `0`, `""`, `[]`, `{}` or `null` |
| `{"expect": EXPR, "equals": VALUE}` | It equals VALUE as JSON: numbers by value (`1` equals `1.0`), a bool only a bool, lists and objects item by item |
| `{"expect": EXPR, "approx": VALUE, "within": D}` | It's within D (default 0.001) of VALUE: a number, or a list of numbers part by part, as a vector comes (`[x, y]`, rounded to 0.001) |
| `{"until": EXPR}` | EXPR held within its frames |
| `{"shot": NAME, "baseline": ...}` | The shot matches its baseline (below) |

EXPR is a Godot expression with `eval`'s inputs ([live.md](live.md#seeing-the-game)): the current scene is its base, and `scene`, `tree`, `root`, the autoloads and the engine's singletons are available. A check is named by its `name`, else by its expression (`clicks == 1`, `until ...`, `shot NAME`).

Every scenario also has checks of its own. `steps` passes when every step ran. `no engine errors` passes when the game raised none during the run (warnings don't count), unless `allow_errors` is set; engine errors raised as the game loads count too.

**When something fails.** A failed `expect` is reported, and the scenario goes on, so one run shows every check that fails. A step that fails (an `until` that never held, a click on a text nothing shows, a line that exits 1) ends the scenario there, since what follows depends on it. It's a failed check of its own, named by the step (`step 4: step 1 --click-text Play`) or by its until, in place of `steps`, and the checks after it are reported as skipped, `not reached: step 4 failed`.

### Checkpoint shots

`{"shot": NAME}` saves the frame as `<out>/<scenario>/checkpoints/NAME.png`. It takes `shot`'s framing ([live.md](live.md#framing-shots)): `crop`, `node`, `margin`, `zoom`, `max_width` and `no_ui`, and `view` (one capture view; `unshaded` and `normals` make steady baselines, [measure.md](measure.md#baselines)).

With `"baseline": true` it's compared with `NAME.png` in the scenario's baselines folder, and `"baseline": "path.png"` names the file. The comparison is `gdh measure diff`'s ([measure.md](measure.md#what-changed-diffs-and-baselines)): the check fails when more than `tolerance` percent (default 0) of the pixels changed by more than `threshold` (default 2, of 255), and says how much changed and where. Changed shots get a heatmap and a crop of the largest change in `<out>/<scenario>/diffs/`, and the numbers are in the check's record. A missing baseline fails the check.

`--update-baselines` writes each checkpoint shot that has a baseline into it, replacing what was there, and passes; where there was one, the check says what changed. A baselines folder gdh makes gets a `.gdignore`, so Godot doesn't import the PNGs as the project's textures. gdh draws a scene the same, pixel for pixel, run after run on one machine; another GPU or driver may not, so keep baselines per machine.

## Running: `gdh scenario run`

`gdh scenario run FILE...` checks each file first: a key, step kind, option or command line gdh doesn't know fails that scenario's `load` check, saying where, before any game starts. Then each scenario runs in turn, in a session of its own (`--session NAME`, default `scenario-<pid>`), and prints each check as it's made:

```
scenario arena (testbed/scenarios/arena.scenario.json): session scenario-4127
  pass  half a second right is 60 px: get_node('Player').position.x is 260.0
  FAIL  Go was clicked once: clicks was 0, expected 1
  pass  until get_node('Player').position.x >= 300: held at frame 52: true
  ...
arena: FAILED (Go was clicked once), 7 checks, 2.1 s -> captures/scenarios/arena
gdh scenario: 1 of 1 failed; results in captures/scenarios/results.json, junit in captures/scenarios/junit.xml
```

What the game printed, its engine errors and notes go to stderr as `gdh live` prints them.

- **The session always stops**, however the run ends: a check or a step failing, gdh failing, Ctrl-C, or SIGTERM. A run that's killed outright (SIGKILL) leaves the game to its idle timeout.
- **Output:** `--out DIR` (default `./captures/scenarios`) holds `results.json`, `junit.xml`, and a folder per scenario with its session's output (`godot.log`, `shots/`), `checkpoints/` and `diffs/`.
- **`--display`** picks the display over the scenario's `start.display`; **`--project`** the project over `start.project`.
- **`--json`** prints `results.json` instead of the lines.
- **Exit status:** 0 when every check of every scenario passed, 1 otherwise.

`results.json` holds `passed`, and `scenarios`, one record per scenario: `name`, `file`, `session`, `out`, `passed`, `seconds`, `frame` (the game frame it ended at), `shots` (each checkpoint shot's file), `errors` (the engine errors, up to 50), and `checks`, each with `name`, `kind` (`expect`, `until`, `shot`, `step`, `steps`, `errors`, `start` or `load`), `passed` (`null` when skipped), `message`, `step` (`step 3`, `expect 1`), and what it found: an expect's `value`, a check's `frame`, a shot's `shot`, `baseline` and diff numbers. `junit.xml` has a test suite per scenario and a test case per check, failed or skipped as the check was, for CI.

### Under `gdh test`

`gdh test` runs every `*.scenario.json` under `res://test` and `res://tests` (or under the paths given, or the scenario files given) after the framework's tests ([test.md](test.md)). Each check counts as a test, its failures are listed with the rest, and `junit.xml` holds the scenarios' suites beside the framework's. The scenarios run on a display even when the tests run headless.

## Saving a session as a scenario

```sh
gdh live save-scenario --session s first_level.scenario.json
```

writes the session's start options and the inputs sent to it through `gdh.client` so far, as a scenario with an empty `expect` list to fill in. Steps keep the form they were sent in: `step()` and `until()` calls as structured steps with their input (`{"step": 30, "hold": "ui_right"}`), `batch()` lines that step the game or move gdh's camera (`step`, `camera`, `record`, `measure`) as lines, and raw `step` and `camera` requests as `request` steps. Replayed, they reach the same frame and state; add `expect` checks for what the playthrough showed, and run it.

- The start options are those the client started the session with. For a session started otherwise, they're its project, the scene it started in, its window's size, the game's arguments and the number of instances; companions aren't saved, and a note says to add them.
- Inputs sent by `gdh live step` and `gdh live batch` on their own aren't recorded, and neither is an `eval` that changes the game; a note says when no steps were recorded.
- A file inside the project leaves `start.project` out, since the runner finds it; one elsewhere names it from the file's folder. `--force` writes over an existing file.

## The Python client

`gdh.client` drives a live session from Python over one `gdh live pipe`, so each request costs a line on a pipe rather than a gdh process:

```python
from gdh.client import Session, Unmet

with Session.start("path/to/game", scene="res://level.tscn", resolution="1280x720") as game:
    game.until("scene.name == 'Hangar'", max=1200, every=10)  # raises Unmet if it never holds
    game.click(text="Launch")
    game.step(60, hold="throttle_up", trace="get_node('Ship').speed")
    if game.eval("get_node('Ship').speed") < 10:
        game.step(30, tap="boost")
    print(game.find("Docked"))
    game.shot(out="captures/docked.png", node="UI/Status", zoom=2)
# the session is stopped here, however the block ends
```

| Call | What it does |
|---|---|
| `Session.start(project, scene=None, name=None, *, args=(), cwd=None, echo=True, **options)` | Runs `gdh live start` (options by name, as a scenario's `start` takes them; `args` the game's) and opens a pipe. The session is `name`, or `client-<pid>-<n>`. A start that fails raises `ClientError` with gdh's message |
| `Session.attach(name)` | A pipe to a running session. Leaving its `with` block closes the pipe and leaves the session running |
| `step(frames=1, *, until, every, max, trace, shot_every, events, instance, **inputs)` | A step, with input as a scenario's `step` takes it; the step's result. An `until` that doesn't hold is in the result (`until.met` false), as the protocol has it |
| `until(expr, max=None, every=1, **inputs)` | Steps until `expr` holds; its until record (`expr`, `met`, `value`, `frame`, `checks`). Raises `Unmet` when it never held |
| `eval(expr)` | The expression's value |
| `click(text=, node=, at=(x, y), right=False, frames=2)` | Clicks what shows a text, a node, or a point, in a step of `frames` frames; the step's result, with `aimed` |
| `find(text, name=, class_name=)` | The matching nodes that show, each with `path`, `class`, `text` and `screen` |
| `shot(out=None, *, views, label, crop, node, margin, zoom, max_width, no_ui)` | Saves the frame; the file's path (`{view: path}` for several views) |
| `tree(path, depth, visible_only)`, `status()` | `tree`'s and `status`'s results |
| `batch(lines, stop_on_error=True)` | Runs `gdh live` command lines in this process, as `gdh live batch --json` does; each line's `{line, ok, replies, error}` |
| `request(cmd, args, instance, timeout)` | One request of the raw protocol; the whole reply |
| `stop()`, `close()` | Stops the session, or closes the pipe and leaves it running |

Every call that takes `instance` sends to that instance (default 0, or `"all"`, which returns a list). A request that fails raises `ClientError`, whose `reply` is the reply. What the game printed, its engine errors (with backtraces) and notes go to stderr as the CLI prints them; `echo=False` keeps them quiet. `errors` lists the engine errors (not warnings) seen so far, and `frame` is the game frame of the latest reply.

## Tests

`tests/test_scenarios.py` runs `testbed/live/arena.tscn`. It checks:

- the client starting the arena, stepping, waiting with `until` and raising `Unmet`, clicking by text, finding, shooting whole and framed, running batch lines, failing on a bad line and a bad expression, a second pipe by `attach`, and stopping the session; a start that fails leaving no session
- `testbed/scenarios/arena.scenario.json` passing, with its checks' lines, `results.json`, `junit.xml` and its checkpoint shots
- failing checks exiting 1 and naming them: an `equals`, an `until` that never held (the checks after it skipped), a step that failed, a file that isn't JSON; a failed expect not stopping the scenario; the session stopped each time
- checkpoint baselines written by `--update-baselines` with a `.gdignore`, passing unchanged, failing with the changed pixels and their box when the baseline was painted on, and failing when it's missing
- `save-scenario` recording `step`, `click`, `until`, `batch` and `camera` inputs, refusing to write over a file, and its file, with checks of the state added, replaying to the same frame, values and shot
- a run stopped with SIGTERM stopping its session and game
- `gdh test` running a project's scenario files beside its framework test, counting their checks and naming the failure, and scenario files given alone running without the framework
