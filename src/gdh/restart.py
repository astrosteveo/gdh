"""Starting a live game where an agent needs it, and fast: gdh live restart, input logs and replays, recipes, seeds, a
session's own user data, and gdh live reload.

Every live session keeps an input log, <out>/inputs.jsonl: a header line, then a line for each command that changed
the game (log_input):

  {"gdh_input_log": 1, "session": NAME, "project": DIR, "scene": "res://...", "seed": N}
  {"cmd": "step", "args": {...the protocol's step arguments}, "instance": 0, "frame": 30, "ran": 30}
  {"cmd": "eval", "args": {"expr": "..."}, "instance": 0, "frame": 30}

"frame" is the game's frame after the command, and "ran" the frames a step ran (fewer than "frames" when --until
held). Steps, evals (which can set values), camera, run and pause are logged, from every way in: a command, batch,
pipe, a recipe and a replay. A replay sends them again in order to a game started the same way, stepping the frames
the game ran by itself (run) as plain steps, so it comes back to the same frame.

`gdh live restart` starts a session again with the options it was started with (kept in a start record, which
outlives the session); --replay replays its input log. `gdh live start --replay FILE` replays a saved log, and
--recipe FILE runs `gdh live batch` lines once the game is ready. --seed seeds the game's global random number
generator; without it gdh picks a seed, which the log and restart keep, so a replay draws the same numbers.
"""
import contextlib
import io
import json
import os
import secrets
import shutil
import sys
import time
from pathlib import Path

from gdh import live
from gdh.godot import user_data_home

LOG_NAME = "inputs.jsonl"
LOG_VERSION = 1
# The commands that change the game, which the input log keeps.
LOGGED = ("step", "eval", "camera", "run", "pause")
# A step's arguments a replay leaves out: it runs the frames the step ran, and saves no frames.
NOT_REPLAYED = ("until", "trace", "every", "shot_every", "cover_every", "views")
# The start options a start record leaves out: they belong to one command, not to the session.
NOT_RECORDED = ("func", "command", "live_command", "json", "strict", "session", "rebuild")
# How long a start record is kept after its session last started, for restart.
RECORD_DAYS = 7
# What a session's own user:// links to in the shared one, so its shader and pipeline caches stay warm.
CACHES = ("shader_cache", "vulkan")


def starts_dir():
    return live.SESSION_DIR / "starts"


# --- The input log ------------------------------------------------------------------------------------------------

def log_input(session, cmd, args, instance, replies):
    """Append a command that changed the game to the session's input log: one that every instance answered ok."""
    path = session.get("input_log")
    if not path or cmd not in LOGGED or not all(r.get("ok") for r in replies):
        return
    entry = {"cmd": cmd, "args": args, "instance": instance, "frame": replies[0].get("frame", 0)}
    if cmd == "step":
        entry["ran"] = replies[0].get("result", {}).get("frames", args.get("frames", 1))
    try:
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        print(f"note: can't add to the input log {path}: {e}", file=sys.stderr)


def read_log(path):
    """An input log's header and entries. Raises LiveError for a file that isn't one."""
    try:
        lines = Path(path).read_text().splitlines()
    except OSError as e:
        raise live.LiveError(f"Can't read the input log {path}: {e.strerror}.") from None
    try:
        header = json.loads(lines[0]) if lines else {}
        entries = [json.loads(line) for line in lines[1:] if line.strip()]
    except ValueError as e:
        raise live.LiveError(f"{path} isn't an input log: {e}.") from None
    if not isinstance(header, dict) or "gdh_input_log" not in header:
        raise live.LiveError(f"{path} isn't an input log: its first line isn't gdh's header.")
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("cmd") not in LOGGED or not isinstance(entry.get("frame"), int):
            raise live.LiveError(f"{path} isn't an input log: {json.dumps(entry)[:120]} isn't a logged command.")
    return header, entries


def replay_args(entry):
    """A logged step as the step a replay sends: the frames it ran, without --until, --trace or saved frames. When
    --until ended it early, the events it sent then go at its new end, and those it never reached are left out."""
    args = {k: v for k, v in entry["args"].items() if k not in NOT_REPLAYED}
    frames = max(int(args.get("frames", 1)), 1)
    ran = int(entry.get("ran", frames))
    if ran < frames:
        args["events"] = [{**e, "at": min(e.get("at", 0), ran)} for e in args.get("events", [])
                          if e.get("at", 0) <= ran or e.get("at", 0) >= frames]
    args["frames"] = ran
    return args


def replay_plan(entries):
    """The requests a replay sends, [(cmd, args, instance)], and the frame it ends at. The frames the game ran by
    itself between two commands (after `run`) become a step of their own."""
    plan = []
    frame = 0
    for entry in entries:
        before = entry["frame"] - entry.get("ran", 0)
        if before > frame:
            plan.append(("step", {"frames": before - frame, "events": []}, 0))
            frame = before
        if entry["cmd"] in ("run", "pause"):
            continue
        plan.append((entry["cmd"], replay_args(entry) if entry["cmd"] == "step" else entry["args"],
                     entry.get("instance", 0)))
        frame = max(frame, entry["frame"])
    return plan, frame


def log_path(name):
    """A session's input log: from its session file, or, once it has ended, its start record."""
    path = live.session_path(name)
    if path.exists():
        log = json.loads(path.read_text()).get("input_log")
        if log:
            return Path(log)
    record = read_record(name)
    return Path(record["log"])


def logged_steps(name):
    """What session `name` has been sent, as a replay would send it again: {"step": frames, "events": [...], ...the
    step's other protocol arguments, "instance"?} for a step, {"request": cmd, "args": {...}, "instance"?} for the
    rest (eval, camera)."""
    _, entries = read_log(log_path(name))
    plan, _ = replay_plan(entries)
    steps = []
    for cmd, args, instance in plan:
        extra = {} if instance in (0, "0") else {"instance": instance}
        if cmd == "step":
            steps.append({"step": args["frames"], **{k: v for k, v in args.items() if k != "frames"}, **extra})
        else:
            steps.append({"request": cmd, "args": args, **extra})
    return steps


def replay(session, entries, source):
    """Send a log's commands to a session just started, in order, and print where it ended. Raises LiveError at the
    first that fails (a node a click names that isn't there now, say): the session stays, held there."""
    plan, end = replay_plan(entries)
    began = time.monotonic()
    frame = 0
    for number, (cmd, args, instance) in enumerate(plan, 1):
        frames = args.get("frames", 0) if cmd == "step" else 0
        reply = live.call(session, cmd, args, instance=instance, timeout=max(300, 2 * frames))
        for part in reply.get("instances", [reply]):
            live.problems({"errors": part.get("errors", [])}, "replay: ")
            live.count_errors(part)
        if not reply.get("ok"):
            raise live.LiveError(f"The replay of {source} stopped at its request {number} of {len(plan)} ({cmd} at "
                                 f"frame {frame}): {reply.get('error', 'it failed')}. The session runs, held there.")
        frame = reply.get("frame", reply.get("instances", [{}])[0].get("frame", frame))
    note = "" if frame == end else f", not the frame {end} the log ended at"
    print(f"replayed {len(plan)} request{'s' if len(plan) != 1 else ''} from {source} in "
          f"{time.monotonic() - began:.1f} s: held at frame {frame}{note}")


# --- Starting -------------------------------------------------------------------------------------------------------

def prepare_start(args, name):
    """Check a start's own options before anything starts, read its --replay log and --recipe, and pick its seed:
    --seed, else the replayed log's, else a random one."""
    if getattr(args, "user_data", "shared") == "fresh" and getattr(args, "user_data_from", None):
        raise live.LiveError("--user-data fresh and --user-data-from both give the session its own user data: pick one.")
    source = getattr(args, "user_data_from", None)
    if source and not Path(source).is_dir():
        raise live.LiveError(f"--user-data-from takes a directory, the user:// to start from: {source} isn't one.")
    header = {}
    if getattr(args, "replay", None) and getattr(args, "_entries", None) is None:
        header, args._entries = read_log(args.replay)
        logged = (header.get("project"), header.get("scene"))
        if logged[0] and logged[0] != str(Path(args.project).resolve()):
            print(f"note: {args.replay} was logged from {logged[0]}, scene {logged[1]}: the replay may not fit this "
                  f"game.", file=sys.stderr)
    recipe = getattr(args, "recipe", None)
    if recipe and not getattr(args, "_skip_recipe", False):
        try:
            lines = Path(recipe).read_text().splitlines()
        except OSError as e:
            raise live.LiveError(f"Can't read the recipe {recipe}: {e.strerror}.") from None
        args._recipe = []
        for number, line in enumerate(lines, 1):
            try:
                parsed = live.parse_command_line(line, name)
            except live.LiveError as e:
                raise live.LiveError(f"{recipe} line {number}: {e}") from None
            if parsed:
                args._recipe.append((number, line.strip()))
    if getattr(args, "seed", None) is None:
        args.seed = header.get("seed") if isinstance(header.get("seed"), int) else secrets.randbelow(1 << 31)


def begin_log(args, session, infos):
    """Start the session's input log and its start record, which `restart` starts it again from. Notes a game whose
    user:// isn't where its own user data was put."""
    out = Path(session["out"])
    log = out / LOG_NAME
    header = {"gdh_input_log": LOG_VERSION, "session": session["name"], "project": session["project"],
              "scene": session.get("scene", ""), "seed": args.seed, "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    log.write_text(json.dumps(header) + "\n")
    session.update({"input_log": str(log), "seed": args.seed})
    options = {key: value for key, value in vars(args).items()
               if key not in NOT_RECORDED and not key.startswith("_") and is_json(value)}
    options.update({"project": session["project"], "out": str(out)})
    for key in ("recipe", "replay", "user_data_from"):
        if options.get(key):
            options[key] = str(Path(options[key]).resolve())
    record = {"options": options, "log": str(log), "cwd": os.getcwd()}
    starts_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
    for old in starts_dir().glob("*.json"):  # the records of sessions not started for a week go
        with contextlib.suppress(OSError):
            if time.time() - old.stat().st_mtime > RECORD_DAYS * 86400:
                old.unlink()
    fd = os.open(starts_dir() / f"{session['name']}.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(record, f)
    if own_user_data(args):
        for instance, info in zip(session["instances"], infos):
            want = project_user_dir(session["project"], Path(instance["out"]) / "user-data")
            if info.get("user_dir") and Path(info["user_dir"]) != want:
                print(f"note: the game's user:// is {info['user_dir']}, not {want}, where gdh put its own user data: "
                      f"it doesn't see it.", file=sys.stderr)


def is_json(value):
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return False
    return True


def after_start(args, session):
    """Once the game is ready: replay the --replay log, then run the recipe. Returns the start's exit code."""
    entries = getattr(args, "_entries", None)
    if entries is not None:
        replay(session, entries, getattr(args, "_source", None) or args.replay)
    for number, line in getattr(args, "_recipe", []):
        print(f"> {line}", flush=True)
        parsed = live.parse_command_line(line, session["name"])
        parsed.strict = parsed.strict or args.strict
        code, error = live.run_command(parsed)
        sys.stdout.flush()
        if code:
            raise live.LiveError(f"The recipe {args.recipe} failed at line {number} ({line}): {error or 'it failed'}. "
                                 f"The rest of it didn't run; the session runs, held there.")
    return 0


# --- A session's own user data ----------------------------------------------------------------------------------------

def own_user_data(args):
    return getattr(args, "user_data", "shared") == "fresh" or bool(getattr(args, "user_data_from", None))


def user_data_env(args, project, out):
    """The environment that gives one instance a user:// of its own, <out>/user-data, made afresh at each start:
    empty (--user-data fresh) or a copy of a directory (--user-data-from). Its shader and pipeline caches link to the
    shared user data's, so they stay warm. {} for the shared user data."""
    if not own_user_data(args):
        return {}
    home = Path(out) / "user-data"
    shutil.rmtree(home, ignore_errors=True)
    user_dir = project_user_dir(project, home)
    if getattr(args, "user_data_from", None):
        shutil.copytree(args.user_data_from, user_dir, symlinks=True)
    else:
        user_dir.mkdir(parents=True)
    shared_home = user_data_home() or os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    shared = project_user_dir(project, Path(shared_home))
    for name in CACHES:
        if not (user_dir / name).exists():
            (shared / name).mkdir(parents=True, exist_ok=True)
            (user_dir / name).symlink_to(shared / name, target_is_directory=True)
    return {"XDG_DATA_HOME": str(home)}


def project_settings(project, section):
    """One section of a project.godot as {key: value}, its strings and booleans read; other values as written."""
    values = {}
    current = None
    for line in (Path(project) / "project.godot").read_text().splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
        elif current == section and "=" in line and not line.startswith(";"):
            key, _, value = line.partition("=")
            try:
                values[key.strip()] = json.loads(value.strip())
            except ValueError:
                values[key.strip()] = value.strip()
    return values


def safe_dir_name(name, allow_paths=False):
    """Godot's OS.get_safe_dir_name: a project's name as a directory's."""
    bad = [":", "*", "?", '"', "<", ">", "|"]
    name = str(name)
    if allow_paths:
        bad.append("..")
        name = name.replace("\\", "/").replace("//", "/").strip()
    else:
        bad += ["/", "\\"]
        name = name.strip()
        name = {".": "dot", "..": "twodots"}.get(name, name)
    for ch in bad:
        name = name.replace(ch, "-")
    return name.rstrip(".")


def project_user_dir(project, data_home):
    """Where a project's user:// is when Godot's data directory ($XDG_DATA_HOME) is data_home, as Godot works it out:
    godot/app_userdata/<name>, or a custom user dir's name."""
    settings = project_settings(project, "application")
    name = safe_dir_name(settings.get("config/name", ""))
    if not name:
        return Path(data_home) / "godot" / "app_userdata" / "[unnamed project]"
    if settings.get("config/use_custom_user_dir") is True:
        return Path(data_home) / (safe_dir_name(settings.get("config/custom_user_dir_name", ""), True) or name)
    return Path(data_home) / "godot" / "app_userdata" / name


# --- Restarting -------------------------------------------------------------------------------------------------------

def read_record(name):
    path = starts_dir() / f"{name}.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        raise live.LiveError(f"Session '{name}' has no start to repeat. Start it with: gdh live start --project <dir> "
                             f"--session {name}") from None


def start_args(name, record):
    """The start command's arguments for session `name`, as its start record keeps them, with the parser's defaults
    for any option the record doesn't name (one gdh has gained since)."""
    parser = live.LineParser(prog="gdh")
    live.add_parsers(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(["live", "start", "--session", name, "--project", record["options"]["project"]])
    for key, value in record["options"].items():
        setattr(args, key, value)
    return args


def cmd_restart(args):
    """Stop the session if it runs, and start it again as it was started: the same project, scene, options,
    companions and game arguments, the C# build and the import redone if they're stale. --replay replays its input
    log, so it comes back to the same frame; without it, a --replay log and a recipe it started with run again."""
    record = read_record(args.session)
    log = Path(record["log"])
    entries = read_log(log)[1] if args.replay else None
    # The new start begins a new log: the one it had stays beside it.
    kept = log.with_name(f"{log.stem}-before-restart{log.suffix}")
    if log.is_file():
        shutil.copyfile(log, kept)
    if live.session_path(args.session).exists():
        with contextlib.redirect_stdout(io.StringIO()):
            live.cmd_stop(args)
    again = start_args(args.session, record)
    again.rebuild = args.rebuild
    again._restarted = True
    again.strict = args.strict
    if entries is not None:
        again._entries, again._source, again._skip_recipe = entries, str(kept), True
    if os.path.isdir(record.get("cwd", "")):
        os.chdir(record["cwd"])  # where its relative paths (companions', the game's arguments) start
    return live.cmd_start(again)


def cmd_reload(args):
    """Put the GDScript files that changed into the running game, keeping its state (harness/reload.gd)."""
    session = live.load_session(args.session)
    reply = live.call(session, "reload", {"paths": args.paths}, instance=args.instance)
    result = live.report(reply, args.json)
    failed = False
    for prefix, r in live.each(reply, result):
        failed = failed or bool(r["failed"])
        if args.json:
            continue
        for path in r["reloaded"]:
            print(f"{prefix}reloaded {path}")
        for path, members in r.get("filled", {}).items():
            print(f"{prefix}  {path}: new members {', '.join(members)} start at their initial values")
        for f in r["failed"]:
            print(f"{prefix}{f['path']} doesn't compile ({f['error']}, above), so the version that ran stays")
        if not r["reloaded"] and not r["failed"]:
            print(f"{prefix}no loaded script has changed ({r['unchanged']} loaded)")
    if failed:
        raise live.LiveError("A script didn't compile: fix it, and reload again.")
    return 0


def add_live_parsers(commands, command):
    """restart and reload, beside start. `command` is live.py's maker of a subcommand."""
    p = command("restart", cmd_restart, "Stop the session and start it again as it was started (--replay: back to the "
                                        "same frame)")
    p.add_argument("--replay", action="store_true",
                   help="Replay the session's input log, so it comes back to the same frame and state")
    p.add_argument("--rebuild", action="store_true", help="Build a C# project's assemblies even if nothing changed")

    p = command("reload", cmd_reload, "Put the GDScript files that changed into the running game, keeping its state",
                instance="Which game instance: a number, or all (default all)")
    p.set_defaults(instance="all")
    p.add_argument("paths", nargs="*", metavar="res://SCRIPT.gd",
                   help="Reload these scripts (default: every loaded script whose file changed)")
