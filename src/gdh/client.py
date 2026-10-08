"""gdh.client: drive a gdh live session from Python, over one `gdh live pipe`.

    from gdh.client import Session

    with Session.start("path/to/game", scene="res://level.tscn", resolution="640x360") as game:
        game.step(30, hold="ui_right")                      # 30 frames holding right
        x = game.eval("get_node('Player').position.x")     # any value
        game.until("get_node('Ship').docked", max=1200, every=10)
        game.click(text="Play")                             # what shows "Play"
        game.shot(out="docked.png")
    # the session is stopped here, however the block ends

Session.start runs `gdh live start` with its keyword arguments as options (resolution="640x360" is --resolution
640x360, no_build=True is --no-build, a list repeats the option), then opens one `gdh live pipe` that every request
goes through. Session.attach(name) drives a session started some other way, and leaves it running when closed.

Requests that fail raise ClientError. What the game printed, its engine errors and notes go to stderr as the CLI
prints them (echo=False keeps them quiet), and the engine errors (not warnings) collect in Session.errors.

Like every way into a session, what a Session sends goes in the session's input log, so `gdh live save-scenario`
can write it as a scenario (docs/scenarios.md).
"""
import contextlib
import io
import itertools
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from gdh import live
from gdh.godot import GdhError

# The options of a step, by their CLI names with underscores (move is --move, click_text is --click-text).
STEP_INPUTS = ("move", "press", "release", "hold", "tap", "type", "click", "right_click", "left_hold", "right_hold",
               "click_text", "click_node", "wheel", "wheel_at", "mod", "axis", "touch", "touch_drag", "look")
_names = itertools.count()


class ClientError(GdhError):
    """A request that failed. .reply is the reply, when there was one."""

    def __init__(self, message, reply=None):
        super().__init__(message)
        self.reply = reply


class Unmet(ClientError):
    """An until that didn't hold within its frames. .until is the step's until record, .result its result."""

    def __init__(self, message, result):
        super().__init__(message)
        self.result = result
        self.until = result.get("until", {})


def gdh_command():
    """gdh, run by this Python."""
    return [sys.executable, "-m", "gdh"]


def option_args(options):
    """Keyword options as command-line options: resolution="640x360" is --resolution=640x360, True a flag alone,
    False and None left out, and a list or tuple the option once for each item."""
    out = []
    for key, value in options.items():
        flag = "--" + key.replace("_", "-")
        if value is None or value is False:
            continue
        if value is True:
            out.append(flag)
            continue
        # --flag=value, so a value that starts with "-" (--look=-40,0) is still the option's.
        for item in value if isinstance(value, (list, tuple)) else [value]:
            out.append(f"{flag}={item}")
    return out


# Start's stderr lines for engine errors, as live.problems prints them: "error: MESSAGE (xN) at WHERE".
ERROR_LINE = re.compile(r"^(?:\(instance \d+\) )?(error|script|shader): (.*?)(?: \(x(\d+)\))? at (.*)$")


class Session:
    """A gdh live session, driven over one `gdh live pipe`. Use Session.start or Session.attach."""

    def __init__(self, name, owned=False, echo=True, command=None):
        self.name = name
        self.owned = owned
        self.echo = echo
        self.errors = []  # the engine errors (not warnings) the game raised in replies so far
        self.frame = None  # the game frame of the latest reply
        self.start_output = ""
        self._command = list(command or gdh_command())
        self._pipe = None
        path = live.session_path(name)
        if path.exists() and json.loads(path.read_text()).get("kind") == "binary":
            raise ClientError(f"Session '{name}' runs a program as it is (--binary), with no harness in it to step or "
                              f"evaluate: drive it with gdh live input, wait and shot.")
        live.load_session(name)
        self._open()

    # --- Starting and stopping ---------------------------------------------------------------------------------

    @classmethod
    def start(cls, project, scene=None, name=None, *, args=(), cwd=None, echo=True, command=None, **options):
        """Start a session (gdh live start) and open a pipe to it. `options` are start's options by name (out,
        resolution, display, instances, companion...), `args` the game's arguments (after --), and `cwd` where gdh
        runs, which relative paths are taken from. The session is named `name`, or client-<pid>-<n>."""
        name = name or f"client-{os.getpid()}-{next(_names)}"
        command = list(command or gdh_command())
        argv = ["live", "start", "--project", str(project), "--session", name]
        if scene:
            argv += ["--scene", scene]
        argv += option_args(options)
        if args:
            argv += ["--", *map(str, args)]
        proc = subprocess.run([*command, *argv], capture_output=True, text=True, cwd=cwd)
        if echo and proc.stderr:
            sys.stderr.write(proc.stderr)
            sys.stderr.flush()
        if proc.returncode != 0:
            said = proc.stderr.strip() or proc.stdout.strip()
            raise ClientError(f"gdh live start failed (exit {proc.returncode}): {said.removeprefix('gdh: ')}")
        try:
            session = cls(name, owned=True, echo=echo, command=command)
        except BaseException:
            subprocess.run([*command, "live", "stop", "--session", name], capture_output=True, timeout=120)
            raise
        session.start_output = proc.stdout
        session.errors += load_errors(proc.stderr)
        return session

    @classmethod
    def attach(cls, name, echo=True, command=None):
        """Open a pipe to a running session. Closing it leaves the session running; stop() stops it."""
        return cls(name, owned=False, echo=echo, command=command)

    def _open(self):
        self._pipe = subprocess.Popen([*self._command, "live", "pipe", "--session", self.name], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, text=True, bufsize=1)

    def close(self, wait=10):
        """Close the pipe. The session goes on running."""
        pipe, self._pipe = self._pipe, None
        if pipe is None:
            return
        with contextlib.suppress(OSError):
            pipe.stdin.close()
        try:
            pipe.wait(timeout=wait)
        except subprocess.TimeoutExpired:  # a request still under way
            pipe.kill()
            pipe.wait()

    def stop(self):
        """Close the pipe and stop the session (gdh live stop), with its companions."""
        self.close(wait=2)
        subprocess.run([*self._command, "live", "stop", "--session", self.name], capture_output=True, timeout=120)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.owned:
            self.stop()
        else:
            self.close()

    # --- Requests ----------------------------------------------------------------------------------------------

    def request(self, cmd, args=None, instance=0, timeout=None):
        """Send one request of the raw protocol (docs/live.md) and return the whole reply. Raises ClientError when
        it fails."""
        return self._send(cmd, args, instance, timeout)

    def _send(self, cmd, args=None, instance=0, timeout=None):
        if self._pipe is None:
            raise ClientError(f"The pipe to session '{self.name}' is closed.")
        message = {"cmd": cmd, "args": args or {}, "instance": instance}
        if timeout:
            message["timeout"] = timeout
        try:
            self._pipe.stdin.write(json.dumps(message) + "\n")
            self._pipe.stdin.flush()
            line = self._pipe.stdout.readline()
        except OSError:
            line = ""
        if not line:
            code = self._pipe.wait()
            self._pipe = None
            raise ClientError(f"gdh live pipe for session '{self.name}' ended (exit {code}).")
        reply = json.loads(line)
        self._problems(reply)
        if not reply.get("ok"):
            raise ClientError(reply.get("error", "Request failed."), reply)
        return reply

    def _problems(self, reply):
        """Print a reply's problems on stderr (unless echo is off), and keep its engine errors and frame."""
        if not isinstance(reply, dict):
            return
        for part in reply.get("instances", [reply]):
            if self.echo:
                live.problems(part, f"[{part['instance']}] " if "instance" in part else "")
            self.errors += [e for e in part.get("errors", []) if e.get("type") != "warning"]
        if isinstance(reply.get("frame"), int):
            self.frame = reply["frame"]

    @staticmethod
    def _result(reply):
        if "instances" in reply:
            return [part.get("result", {}) for part in reply["instances"]]
        return reply.get("result", {})

    # --- Commands ----------------------------------------------------------------------------------------------

    def status(self, instance=0):
        """Frame, held or running, scene, sizes: status's result."""
        return self._result(self._send("status", instance=instance))

    def eval(self, expr, instance=0):
        """A Godot expression's value, as eval gives it (a list of values with instance="all")."""
        result = self._result(self._send("eval", {"expr": expr}, instance))
        return [r["value"] for r in result] if isinstance(result, list) else result["value"]

    def step(self, frames=None, *, until=None, every=None, max=None, trace=(), shot_every=0, events=(), instance=0,
             **inputs):
        """Run `frames` frames (default 1) with input, then hold; the step's result. `inputs` are gdh live step's
        input options by name, a value or a list of them: press, release, hold, tap (an INPUT: an action, key:NAME,
        mouse:left, joy:a), type (text), move, click, right_click, left_hold, right_hold, wheel_at, touch ("X,Y" or
        [x, y]), click_text, click_node, wheel ("down:3"), mod ("ctrl"), axis ("left_x=1"), touch_drag
        ("X,Y:X,Y"), look ("DX,DY"). `events` adds events of the raw protocol. With `until`, the step stops at the
        first check (every `every` frames) where it holds, and runs at most `max` frames (else `frames`, else 3600);
        an until that doesn't hold is in the result (until.met false), as the protocol has it: until() raises."""
        args = self._step_args(frames, until, every, max, trace, shot_every, events, inputs)
        reply = self._send("step", args, instance, timeout=live.reply_timeout("step", args))
        return self._result(reply)

    def until(self, expr, max=None, every=1, *, instance=0, **inputs):
        """Step until `expr` holds, checked every `every` frames, at most `max` frames (default 3600), with input as
        step() takes it. Returns the until record ({"expr", "met", "value", "frame", "checks"}); raises Unmet when
        it never held."""
        args = self._step_args(None, expr, every, max, (), 0, (), inputs)
        reply = self._send("step", args, instance, timeout=live.reply_timeout("step", args))
        result = self._result(reply)
        if isinstance(result, list):
            result = result[0]
        until = result.get("until", {})
        if not until.get("met"):
            raise Unmet(f"until {expr} didn't hold in {result.get('frames')} frames: {describe_unmet(until)}", result)
        return until

    def click(self, text=None, node=None, at=None, *, right=False, frames=2, instance=0):
        """Click what shows `text`, the node at `node` (a path), or the point `at` (screenshot pixels), in a step of
        `frames` frames (the button goes down at its start and up a frame later); the step's result, whose "aimed"
        says what a text or node click hit."""
        given = [k for k, v in (("text", text), ("node", node), ("at", at)) if v is not None]
        if len(given) != 1:
            raise ClientError("click takes one of text, node or at.")
        if right and at is None:
            raise ClientError("A right click takes at=(x, y).")
        key = {"text": "click_text", "node": "click_node", "at": "right_click" if right else "click"}[given[0]]
        return self.step(frames, instance=instance, **{key: text if text is not None else node if node is not None
                                                      else at})

    def find(self, text=None, name=None, class_name=None, instance=0):
        """The nodes that show on screen and match, each {"path", "class", "text"?, "screen"} (find's matches)."""
        filters = {k: v for k, v in (("text", text), ("name", name), ("class", class_name)) if v}
        if not filters:
            raise ClientError("find takes text, name or class_name.")
        result = self._result(self._send("find", filters, instance))
        return [r["matches"] for r in result] if isinstance(result, list) else result["matches"]

    def shot(self, out=None, *, views=("normal",), label="shot", crop=None, node=None, margin=0, zoom=1, max_width=0,
             no_ui=False, instance=0):
        """Save the current frame; the file's path (with several views, {view: path}). `out` names the file
        (relative to the working directory); the framing options are shot's (docs/live.md)."""
        views = [views] if isinstance(views, str) else list(views)
        args = {"views": views, "label": label}
        if out is not None:
            args["out"] = str(Path(out).resolve())
        if crop is not None:
            args["crop"] = numbers(crop)
        if node:
            args.update({"node": node, "margin": margin})
        if zoom != 1:
            args["zoom"] = zoom
        if max_width:
            args["max_width"] = max_width
        if no_ui:
            args["no_ui"] = True
        result = self._result(self._send("shot", args, instance))
        if isinstance(result, list):
            return [r["shots"] for r in result]
        return result["shots"][views[0]] if len(views) == 1 else result["shots"]

    def tree(self, path="", depth=4, visible_only=False, instance=0):
        """The scene tree from `path` (tree's result["tree"])."""
        result = self._result(self._send("tree", {"path": path, "depth": depth, "visible_only": visible_only},
                                         instance))
        return [r["tree"] for r in result] if isinstance(result, list) else result["tree"]

    def batch(self, lines, stop_on_error=True):
        """Run gdh live command lines ("step 30 --hold ui_right", as `gdh live batch` takes them) in this process, in
        order; each line's {"line", "ok", "replies", "error"?}, as `gdh live batch --json` prints it. A line that
        fails raises ClientError (with its record as .reply), unless stop_on_error is off."""
        if isinstance(lines, str):
            lines = lines.splitlines()
        done = []
        for text in lines:
            text = text.strip()
            try:
                parsed = live.parse_command_line(text, self.name)
            except live.LiveError as e:
                raise ClientError(f"{text}: {e}") from None
            if parsed is None:
                continue
            parsed.json = True
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code, error = live.run_command(parsed)
            replies = live.json_documents(output.getvalue())
            for reply in replies:
                self._problems(reply)
            entry = {"line": text, "ok": code == 0, "replies": replies, **({"error": error} if error else {})}
            done.append(entry)
            if code and stop_on_error:
                live.raised.clear()
                raise ClientError(f"{text}: {error or f'exit {code}'}", entry)
        live.raised.clear()
        return done

    # --- Steps as the protocol takes them ----------------------------------------------------------------------

    def _step_args(self, frames, until, every, most, trace, shot_every, events, inputs):
        unknown = sorted(set(inputs) - set(STEP_INPUTS))
        if unknown:
            raise ClientError(f"A step takes no {', '.join(unknown)}; its inputs are {', '.join(STEP_INPUTS)}.")
        if until is None and most is not None:
            raise ClientError("max bounds a step with until.")
        n = (most or frames or live.UNTIL_MAX) if until is not None else (1 if frames is None else frames)
        try:
            built = step_events(inputs, n)
        except SystemExit as e:
            raise ClientError(str(e.code).removeprefix("gdh: ")) from None
        built += [dict(e) for e in events]
        built.sort(key=lambda e: e.get("at", 0))
        args = {"frames": n, "events": built, "shot_every": shot_every}
        trace = [trace] if isinstance(trace, str) else list(trace)
        if until is not None:
            args["until"] = until
        if trace:
            args["trace"] = trace
        if until is not None or trace:
            args["every"] = every or 1
        return args



def as_list(value):
    if value is None:
        return []
    return [value] if isinstance(value, (str, int, float)) or is_point(value) else list(value)


def is_point(value):
    return isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(v, (int, float)) for v in value)


def point_text(p):
    """A point as the CLI writes it: "X,Y" as it is, or [x, y] as "x,y"."""
    if isinstance(p, str):
        return p
    return ",".join(f"{float(v):g}" for v in p)


def numbers(value):
    if isinstance(value, str):
        return [float(v) for v in value.split(",")]
    return [float(v) for v in value]


def step_events(inputs, n):
    """A step's input options (by name, as step() takes them) as protocol events, built as `gdh live step` builds
    them."""
    def get(key):
        return as_list(inputs.get(key))

    events = []
    for p in get("move"):
        events.append({"mouse_motion": live.xy(point_text(p), "move"), "at": 0})
    for token in get("press"):
        events.append(live.input_event(token, True, 0))
    for token in get("release"):
        events.append(live.input_event(token, False, 0))
    for token in get("hold"):
        events += [live.input_event(token, True, 0), live.input_event(token, False, n)]
    for token in get("tap"):
        events += [live.input_event(token, True, 0), live.input_event(token, False, 1)]
    text = inputs.get("type")
    if text is not None:
        if len(text) > n:
            raise SystemExit(f"gdh: type needs a frame a character: step at least {len(text)} frames")
        for i, ch in enumerate(text):
            events += [{"text": ch, "pressed": True, "at": i}, {"text": ch, "pressed": False, "at": i}]
    for button, key, until in ((1, "click", 1), (2, "right_click", 1), (1, "left_hold", n), (2, "right_hold", n)):
        for p in get(key):
            x, y = live.xy(point_text(p), key)
            events += [{"mouse_motion": [x, y], "at": 0},
                       {"mouse_button": button, "position": [x, y], "pressed": True, "at": 0},
                       {"mouse_button": button, "position": [x, y], "pressed": False, "at": until}]
    for key, option in (("text", "click_text"), ("node", "click_node")):
        for target in get(option):
            on = {key: target}
            events += [{"mouse_motion": [0, 0], "on": on, "at": 0},
                       {"mouse_button": 1, "on": on, "pressed": True, "at": 0},
                       {"mouse_button": 1, "on": on, "pressed": False, "at": 1}]
    # A drag is "X,Y:X,Y" or [[x, y], [x, y]]; several are a list of them.
    drag = inputs.get("touch_drag")
    if drag is None:
        drag = []
    elif isinstance(drag, str) or (len(drag) == 2 and all(is_point(p) for p in drag)):
        drag = [drag]
    drags = [d if isinstance(d, str) else ":".join(point_text(p) for p in d) for d in drag]
    device = SimpleNamespace(wheel=list(map(str, get("wheel"))),
                             wheel_at=point_text(inputs["wheel_at"]) if inputs.get("wheel_at") is not None else None,
                             touch=[point_text(p) for p in get("touch")], touch_drag=drags,
                             axis=list(map(str, get("axis"))), look=[point_text(p) for p in get("look")])
    events = live.with_modifiers(events + live.device_input(device, n), list(map(str, get("mod"))), n)
    events.sort(key=lambda e: e["at"])
    return events


def describe_unmet(until):
    if until.get("error"):
        return f"its last check, at frame {until.get('frame')}, failed: {until['error']}"
    if until.get("checks"):
        return f"it was {json.dumps(until.get('value'))} at frame {until.get('frame')}"
    return "it was never checked, as every is more than that"


def load_errors(stderr):
    """The engine errors (not warnings) `gdh live start` printed as the game loaded."""
    errors = []
    for line in stderr.splitlines():
        m = ERROR_LINE.match(line)
        if m:
            errors.append({"type": m.group(1), "message": m.group(2), "where": m.group(4),
                           "count": int(m.group(3) or 1)})
    return errors
