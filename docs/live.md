# Live control

`gdh live` starts a game off-screen and drives it one command at a time. The game is held between commands, so no game time passes while you, or an agent, look at a screenshot and decide what to do next.

```sh
gdh live start --project path/to/game            # main scene, held at frame 0
gdh live step 30 --hold ui_right --shot          # run 30 frames holding right, then save a frame
gdh live tree Player                             # inspect nodes
gdh live eval "get_node('Player').velocity"      # read any value
gdh live probes                                  # run the probes on the current frame
gdh live stop
```

A C# project is built with `dotnet build` first and run with `godot-mono` (README, "C# projects"). Arguments after `--` go to the game: `gdh live start --project path/to/game -- --server ws://localhost:8787` gives the game exactly `["--server", "ws://localhost:8787"]` from `OS.get_cmdline_user_args()`. The harness's own settings travel in the `GDH_ARGS` environment variable, so they never mix with the game's.

Nothing is installed into the project. `gdh` runs Godot with `--script src/gdh/harness/live.gd`, which loads the scene and attaches the control node `bridge.gd`. The project's autoloads load as usual. The control node is an internal child of the root, so game code that walks `root.get_children()` doesn't see it.

## Time

- The game starts held at game frame 0. The scene's `_ready` has run, and no `_process` or `_physics_process` has.
- `step N` runs exactly N frames, then holds again. Godot is launched with `--fixed-fps` set to the project's physics tick rate, so each frame is exactly one physics tick of game time, however long it takes to render.
- The frame number in every reply counts game frames only. Frames rendered while held don't count.
- `run` lets the game run at about real time until `pause`.
- Holding uses `SceneTree.paused`. It has these side effects:
  - Nodes whose process mode is `ALWAYS` or `WHEN_PAUSED` keep running while held. `status` lists them.
  - If the game unpauses itself while held, `gdh` pauses it again and says so in the next reply.

## Input

Input is injected at the start of a step, where real input arrives. `_input`, `is_action_just_pressed` and `is_action_pressed` see it the same way they see a player's input. An input is either an action name from the Input Map or `key:NAME` for a key (for example `key:Space`). A key goes through the Input Map like a real key press.

| Option | Effect |
|---|---|
| `--move X,Y` | Move the pointer to screenshot pixel X,Y at the start of the step, before any press. With a button held, it's a drag: each move carries how far it went and the buttons down |
| `--press INPUT` | Press at the start of the step, and keep it pressed after |
| `--release INPUT` | Release at the start of the step |
| `--hold INPUT` | Press at the start, release at the end |
| `--tap INPUT` | Press for one frame |
| `--click X,Y` | Left click at screenshot pixel X,Y |
| `--right-click X,Y` | Right click at screenshot pixel X,Y |
| `--left-hold X,Y` | Press the left button at X,Y at the start of the step, release it at the end |
| `--right-hold X,Y` | Press the right button at X,Y at the start of the step, release it at the end (a held press: a context or marking menu) |

An INPUT can also be `mouse:left`, `mouse:right` or `mouse:middle`, pressed where the pointer is: `--move 300,200 --press mouse:right`, then steps with `--move` to drag or point, then `--release mouse:right`.

Each option can be repeated. Events at the end of a step (a `--hold`'s release) reach the game before it's held again, so a node that tracks keys by their events (`_input`, `_unhandled_input`) sees them go, not only `Input.is_action_pressed`.

## Coordinates

Every position `gdh` accepts or reports is in screenshot pixels: clicks, the `screen` field in `tree`, and probe `screen_rect`s. So you can click where a screenshot or `tree` shows something, whatever the project's stretch settings are. Tests cover stretch modes `viewport` and `canvas_items`.

`tree` also reports `pos`, which is the node's global position in world units.

## Seeing the game

| Command | Output |
|---|---|
| `shot [--view V]... [--label L] [--tiles]` | PNGs of the current frame, in any capture view |
| `step N --shot-every K` | A frame every K frames during the step, to catch flicker, popping and jitter |
| `step N --shot` | A frame after the step |
| `probes` | Probe findings on the current frame, with crops |
| `tree [PATH] [--depth N]` | Nodes with class, script, world position, screen position (`[x, y]`, or `[x, y, w, h]` for a Control), text, value, velocity and animation |
| `eval EXPR` | Any Godot expression. The base is the current scene. `scene`, `tree`, `root`, each autoload by name and the engine's singletons (`OS`, `Engine`, `Input`, `Time`, `RenderingServer` and the rest) are also available. |
| `status` | Game frame, held or running, scene, nodes that run while held |
| `record N [--out DIR]` | N frames stepped and each saved as `DIR/frame-0000.png` on, for `gdh measure` |
| `measure KIND [--frames N]` | The same, measured: flicker, shimmer, jitter, black, crush or line ([measure.md](measure.md)) |
| `frames [--clear] [--reset] [--save FILE]` | Each game frame's GPU and CPU time since the record started over, and each render pass's with `start --gpu-passes`: the median, 99th percentile and worst |

Screenshots go to `./captures/live/<session>/shots/`, or to `--out` if given. Every reply lists the engine errors raised since the previous reply, with repeats merged. Add `--json` to any command for the raw reply.

## Companions

A session can start other programs beside the game, such as a game server, and stop them with it:

```sh
gdh live start --project client --session vs \
  --companion 'server=PORT={port} exec ./server --test-clock' \
  --companion-ready 'server=http://127.0.0.1:{port}/health' \
  -- --server ws://127.0.0.1:{server.port}/play
```

- **`--companion NAME=COMMAND`** runs COMMAND with `sh -c`, from the directory gdh was run in, in a process group of its own. It's repeatable, and companions start in order, each once the one before is ready, before the game.
- **Ports:** gdh picks a free port for each companion. `{port}` in its command and ready check is its own port, also in `$GDH_PORT`. `{NAME.port}` is a companion's port anywhere, the game's arguments included. `--companion-port NAME=PORT` gives it a port of your choosing instead. A placeholder naming no companion stops the start.
- **Ready:** `--companion-ready NAME=CHECK` says when it's ready. `tcp`, the default, waits for its port to take a connection. An `http://` or `https://` URL waits for a 2xx answer, and `none` doesn't wait. `--timeout` bounds the wait for each companion, and then for the game.
- **Logs:** each companion's output is in `<out>/<NAME>.log`. A companion that exits before it's ready, or isn't ready in time, stops the start with the end of its log, and nothing is left running.
- **Stopping:** `stop` quits the game first, so it can sign off, then stops every companion's process group. A watchdog also stops them if the game ends by itself (its idle timeout, a crash). While the session runs, a companion that has exited is noted, with the end of its log, on every command, and `status` shows each companion.

## Several instances

`--instances N` runs N instances of the game in one session, each under its own Xvfb, with its own output in `<out>/instance-K/`. `{instance}` in the game's arguments is each one's number, so they can be told apart (`-- --user pilot{instance}`).

- **`step`, `run` and `pause` go to every instance at once**, so the instances step together: when each frame of one waits on another (two clients of a server on a test clock that ticks once every client has asked), they keep in step. The input of a step goes to `--instance K` (default 0), or to every instance with `--instance all`.
- **`shot`, `probes`, `tree` and `eval` go to `--instance K`** (default 0), or to every instance with `--instance all`.
- With one instance, every reply is the game's own. With more, a command sent to one instance gets that instance's reply, with `"instance": K`, and a command sent to several gets `{"ok": ..., "instances": [reply, ...]}`, with the first failure as its `"error"`. The CLI prefixes each instance's lines with `[K]`.
- If any instance ends, the session has ended: the next command says which, and stops the rest.

## Scripts: `gdh live pipe`

A script that drives many steps can keep one `gdh live pipe --session NAME` open instead of starting gdh for each command. It reads requests from stdin, one JSON object a line, and answers each on stdout, one a line:

```json
{"cmd": "step", "args": {"frames": 10, "events": [{"action": "jump", "pressed": true, "at": 0}]}, "instance": 0}
{"cmd": "eval", "args": {"expr": "get_node('Player').position"}, "instance": "all"}
```

The commands and their arguments are the raw protocol's (below), and `"instance"` follows the rules above. The replies are what `--json` prints. `quit` is refused: stop the session with `gdh live stop`, which stops its companions too. If the game ends, the pipe answers with how it ended and exits with status 1.

## Sessions

- **Several games at once:** `--session NAME` runs more than one game side by side. The default name is `default`.
- **Session files:** each is kept in `$XDG_RUNTIME_DIR/gdh/` and can be read only by you. It holds the port and a random token that every request must carry. The game gets the token through its environment, which other users can't read. Keeping it off the command line keeps it out of the process list.
- **Local only:** the game listens on 127.0.0.1.
- **Idle timeout:** a game quits after 30 minutes without a request. Change this with `--idle-timeout SECONDS`, or set 0 to turn it off.
- **Stopping:** `stop` asks the game to quit, then stops Xvfb and Godot if they're still running.
- **Crashed games:** if a game dies, the next command says so, shows the end of its log, stops the session's other processes and removes the session.

## Protocol

One JSON object per line over TCP.

```json
{"id": 1, "token": "…", "cmd": "step", "args": {"frames": 30, "events": [{"action": "ui_right", "pressed": true, "at": 0}]}}
{"id": 1, "ok": true, "result": {…}, "errors": […], "frame": 30, "held": true}
```

This is how gdh talks to one instance. The commands are `status`, `step`, `shot`, `probes`, `tree`, `eval`, `frames`, `run`, `pause` and `quit`. `frames` takes `{"reset": bool, "clear": bool}` and returns `{"frames": [{"gpu": ms, "cpu": ms, "frame": n, "passes": {name: ms}, "groups": {name: ms}}, ...], "game_frames": n, "size": [w, h], "adapter": name}`. In `step`, an event with `"at": k` is injected before frame k+1 of the step. Event forms:
- `{action, pressed, strength}`
- `{key, pressed}`
- `{mouse_button, position, pressed}`
- `{mouse_motion: [x, y]}`

## Tests

`uv run pytest` runs `tests/test_live.py` against `testbed/live/arena.tscn`. The tests need Godot, a GPU with Vulkan, and Xvfb. They check:

- exact frame counts and movement at 60 and 120 ticks per second
- that taps reach `_physics_process`, `_process` and `_input` once each
- held input across steps
- button clicks at the positions `tree` reports, with no stretching and in both stretch modes
- screenshots, the tree, error reporting and the unpause note
- a bad scene, the idle timeout and cleanup after a crash

`tests/test_companions.py` runs two instances of `testbed/live/lockstep.tscn` beside `testbed/live/barrier.py`, a companion every instance waits at each frame, as clients of a server on a test clock do. It checks:

- that `{port}`, `{NAME.port}` and `{instance}` reach the companion and each instance
- that the instances step together: 60 frames with no wait given up, where stepping them one after the other would stall every frame
- input to the instance named, and a hold's release seen by a node that tracks its keys by their events
- one instance's reply alone, a bad instance number, and `pipe`
- `status`, an HTTP ready check, `stop` stopping the companion, the watchdog stopping it when the game ends by itself, a companion that fails to start, and a placeholder that names no companion
