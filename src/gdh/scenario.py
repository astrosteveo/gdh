"""gdh scenario: playthroughs kept as JSON files and replayed against a live session, with checks.

A scenario file holds the options its session starts with, then its steps in order: `gdh live batch` lines or
structured steps, checks (expect), and checkpoint shots, which can be compared with baselines. `gdh scenario run
FILE...` runs each in a session of its own, prints each check's verdict, writes results.json and junit.xml, stops the
session however the run ends, and exits 1 when a check failed. gdh's steps are frame-exact, so a replay reaches the
same state each time. `gdh live save-scenario` writes a session's start options and input log (restart.py) as a
scenario to add checks to. docs/scenarios.md describes the format.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from gdh import client, live, restart
from gdh.display import CHOICES
from gdh.godot import GdhError

SUFFIX = ".scenario.json"
TOP_KEYS = ("name", "description", "start", "steps", "expect", "baselines", "allow_errors")
STEP_OPTIONS = ("until", "every", "max", "trace", "shot_every", "events")
FRAMING = ("view", "crop", "node", "margin", "zoom", "max_width", "no_ui")
# What each kind of structured step takes besides its own key; every kind takes "name" and "instance".
KINDS = {
    "step": (*client.STEP_INPUTS, *STEP_OPTIONS),
    "until": (*client.STEP_INPUTS, "every", "max"),
    "expect": ("equals", "approx", "within"),
    "shot": ("baseline", "tolerance", "threshold", *FRAMING),
    "snapshot": ("baseline", "path", "boxes", "grid"),
    "eval": (),
    "request": ("args", "timeout"),
}
# How far apart an approx check lets numbers be, when it doesn't say (a vector's parts are rounded to this).
WITHIN = 0.001
NAME = re.compile(r"^[\w.-]+$")


class ScenarioError(GdhError):
    pass


# --- Reading a scenario ---------------------------------------------------------------------------------------------

def scenario_name(path):
    name = Path(path).name
    return name[:-len(SUFFIX)] if name.endswith(SUFFIX) else Path(path).stem


def load(path):
    """A scenario file, checked: every step and check is one gdh knows, before anything starts. Returns
    {"name", "file", "dir", "start", "steps": [{"kind", "spec", "label"}], "expect": [...], "baselines",
    "allow_errors"}."""
    path = Path(path).resolve()
    try:
        doc = json.loads(path.read_text())
    except OSError as e:
        raise ScenarioError(f"Can't read {path}: {e.strerror}.") from None
    except ValueError as e:
        raise ScenarioError(f"{path} isn't JSON: {e}.") from None
    if not isinstance(doc, dict):
        raise ScenarioError(f"{path}: a scenario is a JSON object with \"steps\".")
    unknown = [k for k in doc if k not in TOP_KEYS]
    if unknown:
        raise ScenarioError(f"{path}: no {', '.join(map(repr, unknown))} in a scenario; it takes {', '.join(TOP_KEYS)}.")
    start = doc.get("start", {})
    if not isinstance(start, dict):
        raise ScenarioError(f"{path}: \"start\" holds gdh live start's options as an object.")
    for key in ("session", "out"):
        if key in start:
            raise ScenarioError(f"{path}: start can't set {key}: gdh scenario run sets it (--session, --out).")
    if start.get("binary"):
        raise ScenarioError(f"{path}: a scenario drives a game through gdh's harness, and start.binary runs a program "
                            f"without one (--binary): its input isn't frame-exact, so it can't replay. Give "
                            f"start.project, or the folder holding the file.")
    if not isinstance(start.get("args", []), list):
        raise ScenarioError(f"{path}: start.args is a list of the game's arguments.")
    steps = doc.get("steps", [])
    expects = doc.get("expect", [])
    if not isinstance(steps, list) or not isinstance(expects, list):
        raise ScenarioError(f"{path}: \"steps\" and \"expect\" are lists.")
    name = doc.get("name") or scenario_name(path)
    if not NAME.match(name):
        raise ScenarioError(f"{path}: a scenario's name is letters, digits, '.', '-' and '_', not {name!r}.")
    return {
        "name": name,
        "file": str(path),
        "dir": path.parent,
        "start": start,
        "steps": [read_step(path, f"steps[{i}]", s) for i, s in enumerate(steps)],
        "expect": [read_step(path, f"expect[{i}]", {"expect": s} if isinstance(s, str) else s, only="expect")
                   for i, s in enumerate(expects)],
        "baselines": path.parent / doc.get("baselines", f"baselines/{name}"),
        "allow_errors": bool(doc.get("allow_errors", False)),
    }


def read_step(path, where, spec, only=None):
    """One step, checked: a command line, or an object with one kind of step's key."""
    if isinstance(spec, str):
        try:
            parsed = live.parse_command_line(spec, "scenario")
        except live.LiveError as e:
            raise ScenarioError(f"{path}: {where}: {e}") from None
        if parsed is None:
            return {"kind": "comment", "spec": spec, "label": spec}
        return {"kind": "line", "spec": spec, "label": spec.strip()}
    if not isinstance(spec, dict):
        raise ScenarioError(f"{path}: {where}: a step is a command line or an object, not {json.dumps(spec)}.")
    kinds = [k for k in KINDS if k in spec]
    if len(kinds) != 1 or (only and kinds != [only]):
        wanted = f'"{only}"' if only else f"one of {', '.join(KINDS)}"
        raise ScenarioError(f"{path}: {where}: a step has {wanted}: {json.dumps(spec)[:200]}")
    kind = kinds[0]
    allowed = {kind, "name", "instance", *KINDS[kind]}
    unknown = sorted(set(spec) - allowed)
    if unknown:
        what = "a step" if kind == "step" else f"a{'n' if kind[0] in 'aeiu' else ''} {kind} step"
        raise ScenarioError(f"{path}: {where}: {what} takes no {', '.join(unknown)}; it takes "
                            f"{', '.join(sorted(allowed - {kind}))}.")
    if kind == "expect":
        if "equals" in spec and "approx" in spec:
            raise ScenarioError(f"{path}: {where}: an expect takes equals or approx, not both.")
        if "within" in spec and "approx" not in spec:
            raise ScenarioError(f"{path}: {where}: within goes with approx.")
    if kind in ("shot", "snapshot") and not NAME.match(str(spec[kind])):
        raise ScenarioError(f"{path}: {where}: a {kind}'s name is letters, digits, '.', '-' and '_' (its file's name), "
                            f"not {spec[kind]!r}.")
    if kind == "shot" and "view" in spec and not isinstance(spec["view"], str):
        raise ScenarioError(f"{path}: {where}: a checkpoint shot takes one view.")
    return {"kind": kind, "spec": spec, "label": spec.get("name") or label(kind, spec)}


def label(kind, spec):
    if kind == "expect":
        if "equals" in spec:
            return f"{spec['expect']} == {json.dumps(spec['equals'])}"
        if "approx" in spec:
            return f"{spec['expect']} ~ {json.dumps(spec['approx'])}"
        return str(spec["expect"])
    if kind == "until":
        return f"until {spec['until']}"
    if kind in ("shot", "snapshot"):
        return f"{kind} {spec[kind]}"
    if kind == "request":
        return f"request {spec['request']}"
    if kind == "eval":
        return f"eval {spec['eval']}"
    return json.dumps(spec)[:120]


def project_of(scenario, override=None):
    """The project a scenario runs: --project, else start.project (from the file's folder), else the nearest folder
    holding the file with a project.godot."""
    if override:
        return Path(override).resolve()
    if scenario["start"].get("project"):
        return (scenario["dir"] / scenario["start"]["project"]).resolve()
    for folder in (scenario["dir"], *scenario["dir"].parents):
        if (folder / "project.godot").exists():
            return folder
    raise ScenarioError(f"{scenario['file']}: no project: give start.project, or keep the file in its project.")


# --- Checks ---------------------------------------------------------------------------------------------------------

def same(a, b):
    """Whether two JSON values are equal: numbers by value (1 is 1.0), a bool only to a bool."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    return a == b


def close(a, b, within):
    """Whether two numbers, or two lists of numbers (a vector), are within `within` of each other, part by part."""
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(close(x, y, within) for x, y in zip(a, b))
    numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (a, b))
    return numeric and abs(a - b) <= within


def brief(value, limit=200):
    text = json.dumps(value)
    return text if len(text) <= limit else text[:limit] + "..."


def expect(game, spec):
    """An expect's verdict: (passed, message, value)."""
    expr = spec["expect"]
    try:
        value = game.eval(expr, spec.get("instance", 0))
    except client.ClientError as e:
        return False, f"{expr} failed: {e}", None
    if "equals" in spec:
        if same(value, spec["equals"]):
            return True, f"{expr} is {brief(value)}", value
        return False, f"{expr} was {brief(value)}, expected {brief(spec['equals'])}", value
    if "approx" in spec:
        within = spec.get("within", WITHIN)
        if close(value, spec["approx"], within):
            return True, f"{expr} is {brief(value)}", value
        return False, f"{expr} was {brief(value)}, expected {brief(spec['approx'])} within {within:g}", value
    if value:
        return True, f"{expr} is {brief(value)}", value
    return False, f"{expr} was {brief(value)}", value


def checkpoint(game, spec, scenario, out, update):
    """A checkpoint shot, saved as <out>/checkpoints/NAME.png, and compared with its baseline when it has one.
    Returns (the shot's path, and the check: None without a baseline, else (passed, message, details))."""
    name = spec["shot"]
    framing = {k: spec[k] for k in FRAMING if k in spec and k != "view"}
    path = game.shot(out=out / "checkpoints" / f"{name}.png", views=[spec.get("view", "normal")], label=name,
                     instance=spec.get("instance", 0), **framing)
    baseline = spec.get("baseline")
    if not baseline:
        return path, None
    from gdh import measure
    base = scenario["baselines"] / f"{name}.png" if baseline is True else scenario["dir"] / baseline
    threshold = spec.get("threshold", measure.DIFF_THRESHOLD)
    tolerance = spec.get("tolerance", 0.0)
    d = None
    if base.exists():
        try:
            d = measure.diff(base, path, threshold, out=out / "diffs", name=f"{name}-", labels=("baseline", "now"))
        except measure.MeasureError as e:
            if not update:
                return path, (False, str(e), {"baseline": str(base)})
    details = {"baseline": str(base)}
    if d is not None:
        details.update({k: d[k] for k in ("max_diff", "changed_px", "changed_share", "box") if k in d})
        details.update({k: d[k] for k in ("heatmap", "crop") if k in d})
    if update:
        if not base.parent.exists():
            base.parent.mkdir(parents=True)
            (base.parent / ".gdignore").touch()  # so Godot doesn't import the baselines as the project's textures
        shutil.copyfile(path, base)
        changed = f"; {describe_change(d, threshold)}" if d and d["changed_px"] else ""
        return path, (True, f"wrote the baseline {base}{changed}", details)
    if d is None:
        return path, (False, f"no baseline at {base} (gdh scenario run --update-baselines writes it)", details)
    if not d["changed_px"]:
        return path, (True, "the same as the baseline", details)
    over = measure.diff_over(d, tolerance)
    within = "" if over else f", within the tolerance ({tolerance:g}%)"
    return path, (not over, f"{describe_change(d, threshold)}{within}", details)


def ui_checkpoint(game, spec, scenario, out, update):
    """A snapshot of the UI on screen, saved as <out>/checkpoints/NAME.txt, and compared with its baseline (NAME.txt in
    the baselines folder) when it has one. Returns (its path, and the check: None without a baseline, else (passed,
    message, details))."""
    from gdh import snapshot
    name = spec["snapshot"]
    now = game.snapshot(spec.get("path", ""), boxes=spec.get("boxes", False), grid=spec.get("grid", 1),
                        instance=spec.get("instance", 0))
    path = out / "checkpoints" / f"{name}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(now)
    baseline = spec.get("baseline")
    if not baseline:
        return path, None
    base = scenario["baselines"] / f"{name}.txt" if baseline is True else scenario["dir"] / baseline
    details = {"baseline": str(base)}
    kept = base.read_text() if base.is_file() else None
    if update:
        base.parent.mkdir(parents=True, exist_ok=True)
        (base.parent / ".gdignore").touch()
        base.write_text(now)
        changed = "" if kept is None or kept == now else "; it changed:\n" + snapshot.diff(kept, now, str(base))
        return path, (True, f"wrote the baseline {base}{changed}", details)
    if kept is None:
        return path, (False, f"no baseline at {base} (gdh scenario run --update-baselines writes it)", details)
    changed = snapshot.diff(kept, now, str(base), str(path))
    if not changed:
        return path, (True, "the same as the baseline", details)
    details["diff"] = changed
    return path, (False, f"the UI on screen isn't the same as the baseline:\n{changed}", details)


def describe_change(d, threshold):
    return (f"{100 * d['changed_share']:.3g}% of pixels ({d['changed_px']}) changed by more than {threshold:g} from "
            f"the baseline, max {d['max_diff']}, in the box {','.join(map(str, d['box']))}"
            + (f"; heatmap {d['heatmap']}" if d.get("heatmap") else ""))


# --- Running --------------------------------------------------------------------------------------------------------

class Run:
    """One scenario's run: its checks as they come, printed unless quiet."""

    def __init__(self, scenario, out, session, quiet):
        self.scenario = scenario
        self.quiet = quiet
        self.result = {"name": scenario["name"], "file": scenario["file"], "session": session, "out": str(out),
                       "passed": False, "checks": [], "shots": {}, "errors": [], "frame": None, "seconds": 0.0}

    def check(self, name, kind, passed, message, where=None, **extra):
        record = {"name": name, "kind": kind, "passed": passed, "message": message, **({"step": where} if where else {}),
                  **extra}
        self.result["checks"].append(record)
        if not self.quiet:
            verdict = "skip" if passed is None else "pass" if passed else "FAIL"
            print(f"  {verdict}  {name}: {message}", flush=True)
        return passed

    def skip_rest(self, steps, expects, why):
        """The checks a failed step kept from running, as skipped."""
        for item, where in steps + expects:
            if item["kind"] in ("expect", "until") or (item["kind"] in ("shot", "snapshot")
                                                         and item["spec"].get("baseline")):
                self.check(item["label"], item["kind"], None, f"not reached: {why}", where, skipped=True)


def run_scenario(scenario, out, session, display=None, project=None, start_extra=None, update=False, echo=True,
                 quiet=False):
    """Run one scenario in session `session`, its output in `out`; its result. The session is stopped however the
    run ends."""
    run = Run(scenario, out, session, quiet)
    result = run.result
    began = time.monotonic()
    for old in ("checkpoints", "diffs"):
        shutil.rmtree(out / old, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    steps = [(s, f"step {i + 1}") for i, s in enumerate(scenario["steps"])]
    expects = [(s, f"expect {i + 1}") for i, s in enumerate(scenario["expect"])]
    if not quiet:
        print(f"scenario {scenario['name']} ({scenario['file']}): session {session}", flush=True)
    game = None
    here = os.getcwd()
    try:
        # Relative paths in the scenario (start's options, its lines' files) are from its own folder.
        os.chdir(scenario["dir"])
        start = scenario["start"]
        options = {k: v for k, v in start.items() if k not in ("project", "scene", "args")}
        for key, value in (start_extra or {}).items():
            options.setdefault(key, value)
        if display:
            options["display"] = display
        try:
            game = client.Session.start(project_of(scenario, project), scene=start.get("scene"), name=session,
                                        args=start.get("args", []), out=str(out), echo=echo, **options)
        except GdhError as e:
            run.check("start", "start", False, str(e))
            run.skip_rest(steps, expects, "the session didn't start")
            return result
        failed = None
        for k, (item, where) in enumerate(steps):
            if not run_step(run, game, item, where, out, update):
                failed = k
                break
        if failed is None:
            for item, where in expects:
                run_step(run, game, item, where, out, update)
            run.check("steps", "steps", True, f"all {len(steps)} steps ran, to frame {game.frame}")
        else:
            run.skip_rest(steps[failed + 1:], expects, f"{steps[failed][1]} failed")
        result["frame"] = game.frame
        result["errors"] = game.errors[:50]
        if not scenario["allow_errors"]:
            errors = game.errors
            first = f", the first {errors[0]['type']}: {errors[0]['message']} at {errors[0]['where']}" if errors else ""
            count = sum(e.get("count", 1) for e in errors)
            run.check("no engine errors", "errors", not errors,
                      f"the game raised {count} engine error{'s' if count != 1 else ''}{first}" if errors
                      else "the game raised none")
    finally:
        os.chdir(here)
        if game is not None:
            game.stop()
        elif live.session_path(session).exists():
            subprocess.run([*client.gdh_command(), "live", "stop", "--session", session], capture_output=True,
                           timeout=120)
        result["seconds"] = round(time.monotonic() - began, 2)
        result["passed"] = bool(result["checks"]) and all(c["passed"] is not False for c in result["checks"])
    return result


def run_step(run, game, item, where, out, update):
    """Run one step of a scenario. Returns False when the scenario can't go on: a step failed, an until didn't hold,
    or the session ended."""
    kind, spec, name = item["kind"], item["spec"], item["label"]
    instance = spec.get("instance", 0) if isinstance(spec, dict) else 0
    try:
        if kind == "comment":
            return True
        if kind == "line":
            entry = game.batch([spec], stop_on_error=False)[0]
            if not entry["ok"]:
                return run.check(f"{where}: {name}", "step", False, entry.get("error") or "the command failed", where)
            return True
        if kind == "request":
            game.request(spec["request"], spec.get("args", {}), instance, spec.get("timeout"))
            return True
        if kind == "eval":
            game.eval(spec["eval"], instance)
            return True
        if kind == "step":
            options = {k: v for k, v in spec.items() if k not in ("step", "name", "instance")}
            result = game.step(spec["step"], instance=instance, **options)
            until = result.get("until") if isinstance(result, dict) else None
            if until and not until.get("met"):
                return run.check(name if spec.get("name") else f"until {until['expr']}", "until", False,
                                 f"didn't hold in {result['frames']} frames: {client.describe_unmet(until)}", where)
            return True
        if kind == "until":
            options = {k: v for k, v in spec.items() if k not in ("until", "name", "instance")}
            try:
                until = game.until(spec["until"], instance=instance, **options)
            except client.Unmet as e:
                return run.check(name, "until", False,
                                 f"didn't hold in {e.result.get('frames')} frames: {client.describe_unmet(e.until)}",
                                 where)
            run.check(name, "until", True, f"held at frame {until['frame']}: {brief(until['value'])}", where,
                      value=until["value"], frame=until["frame"])
            return True
        if kind == "expect":
            passed, message, value = expect(game, spec)
            run.check(name, "expect", passed, message, where, value=value, frame=game.frame)
            return game_alive(game)
        if kind == "snapshot":
            path, verdict = ui_checkpoint(game, spec, run.scenario, out, update)
            if verdict is not None:
                passed, message, details = verdict
                run.check(name, "snapshot", passed, message, where, snapshot=str(path), frame=game.frame, **details)
            return True
        if kind == "shot":
            path, verdict = checkpoint(game, spec, run.scenario, out, update)
            run.result["shots"][spec["shot"]] = path
            if verdict is not None:
                passed, message, details = verdict
                run.check(name, "shot", passed, message, where, shot=path, frame=game.frame, **details)
            return True
    except client.ClientError as e:
        run.check(f"{where}: {name}", kind if kind in ("shot", "snapshot", "expect") else "step", False, str(e), where)
        return kind in ("shot", "snapshot", "expect") and game_alive(game)
    raise ScenarioError(f"Unknown step kind {kind}.")


def game_alive(game):
    return game._pipe is not None and live.session_path(game.name).exists()


def write_junit(results, path):
    """The scenarios as JUnit XML: a test suite per scenario, a test case per check."""
    def counts(checks):
        return {"tests": str(len(checks)), "failures": str(sum(c["passed"] is False for c in checks)),
                "skipped": str(sum(c["passed"] is None for c in checks))}

    every = [c for r in results for c in r["checks"]]
    root = ET.Element("testsuites", name="gdh scenarios", **counts(every),
                      time=f"{sum(r['seconds'] for r in results):.2f}")
    for r in results:
        suite = ET.SubElement(root, "testsuite", name=r["name"], file=r["file"], **counts(r["checks"]),
                              time=f"{r['seconds']:.2f}")
        for c in r["checks"]:
            case = ET.SubElement(suite, "testcase", classname=r["name"], name=c["name"], file=r["file"])
            if c["passed"] is None:
                ET.SubElement(case, "skipped", message=c["message"])
            elif not c["passed"]:
                failure = ET.SubElement(case, "failure", message=c["message"])
                failure.text = c["message"]
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def run_files(files, out_root, session=None, display=None, project=None, start_extra=None, update=False, echo=True,
              quiet=False):
    """Run scenario files one after another, each in session `session` in its turn, and write results.json and
    junit.xml in out_root. Returns the results document. A file that doesn't load is a scenario whose "load" check
    failed; the others still run."""
    out_root = Path(out_root).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    project = Path(project).resolve() if project else None  # each scenario runs from its own folder
    session = session or f"scenario-{os.getpid()}"
    results = []
    used = set()
    # A stop (SIGTERM) still stops the session: the run's finally blocks run on the way out.
    try:
        previous = signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    except ValueError:  # not the main thread
        previous = None
    try:
        for file in files:
            try:
                scenario = load(file)
            except ScenarioError as e:
                name = scenario_name(file)
                if not quiet:
                    print(f"scenario {name} ({file}):\n  FAIL  load: {e}", flush=True)
                results.append({"name": name, "file": str(Path(file).resolve()), "session": None, "out": None,
                                "passed": False, "seconds": 0.0, "shots": {}, "errors": [], "frame": None,
                                "checks": [{"name": "load", "kind": "load", "passed": False, "message": str(e)}]})
                continue
            name = scenario["name"]
            k = 2
            while name in used:
                name, k = f"{scenario['name']}-{k}", k + 1
            used.add(name)
            result = run_scenario(scenario, out_root / name, session, display, project, start_extra, update, echo,
                                  quiet)
            results.append(result)
            if not quiet:
                failed = [c for c in result["checks"] if c["passed"] is False]
                verdict = "passed" if result["passed"] else f"FAILED ({', '.join(c['name'] for c in failed)})"
                print(f"{name}: {verdict}, {len(result['checks'])} checks, {result['seconds']:.1f} s -> "
                      f"{result['out']}", flush=True)
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
    doc = {"passed": all(r["passed"] for r in results) and bool(results), "scenarios": results,
           "results": str(out_root / "results.json"), "junit": str(out_root / "junit.xml")}
    (out_root / "results.json").write_text(json.dumps(doc, indent=2) + "\n")
    write_junit(results, out_root / "junit.xml")
    return doc


def files_under(project, paths):
    """The scenario files (*.scenario.json) among res:// paths, or under them when they're folders."""
    found = []
    for p in paths:
        local = Path(project) / p.removeprefix("res://")
        if local.is_file() and local.name.endswith(SUFFIX):
            found.append(local)
        elif local.is_dir():
            found += sorted(f for f in local.rglob(f"*{SUFFIX}") if not any(part.startswith(".") for part in
                                                                            f.relative_to(local).parts))
    return found


def cmd_run(args):
    out = Path(args.out or Path.cwd() / "captures" / "scenarios")
    doc = run_files(args.files, out, args.session, args.scenario_display, args.project, update=args.update_baselines,
                    quiet=args.json)
    if args.json:
        print(json.dumps(doc, indent=2))
    else:
        failed = [r for r in doc["scenarios"] if not r["passed"]]
        count = len(doc["scenarios"])
        verdict = f"{len(failed)} of {count} failed" if failed else f"{'all ' if count > 1 else ''}{count} passed"
        print(f"gdh scenario: {verdict}; results in {doc['results']}, junit in {doc['junit']}")
    return 0 if doc["passed"] else 1


def add_parser(sub):
    p = sub.add_parser("scenario", help="Run scenario files: playthroughs replayed in a live session, with checks")
    commands = p.add_subparsers(dest="scenario_command", required=True)
    r = commands.add_parser("run", help="Run scenario files, print each check's verdict, write results.json and "
                                        "junit.xml; exit 1 when a check failed")
    r.add_argument("files", nargs="+", metavar="FILE", help="Scenario files (*.scenario.json)")
    r.add_argument("--out", help="Where results.json, junit.xml and a folder per scenario go "
                                 "(default: ./captures/scenarios)")
    r.add_argument("--session", help="The session's name (default: scenario-<pid>); each scenario runs in it in turn")
    r.add_argument("--display", dest="scenario_display", choices=CHOICES,
                   help="The display, over the scenario's own start.display (default: that, else $GDH_DISPLAY, else "
                        "auto)")
    r.add_argument("--project", help="The project, over start.project (default: that, from the file's folder, else "
                                     "the nearest folder above the file with a project.godot)")
    r.add_argument("--update-baselines", action="store_true",
                   help="Write each checkpoint shot that has a baseline into it (changes are listed, never failed)")
    r.add_argument("--json", action="store_true", help="Print the results as JSON (results.json)")
    r.set_defaults(func=cmd_run)


# --- gdh live save-scenario -----------------------------------------------------------------------------------------

# The start options a saved scenario leaves out: the runner's own (out, display, the build and import), and a
# --replay log and --recipe, whose requests the input log already holds.
NOT_SAVED = ("project", "out", "display", "no_build", "no_import", "rebuild", "replay", "recipe", "game_args", "seed")


def recorded_steps(name):
    """What session `name` has been sent, from its input log (restart.logged_steps), as scenario steps: a step as
    {"step": frames, "events": [...]}, an eval as {"eval": EXPR}, a camera move as {"request": "camera", ...}."""
    steps = []
    for step in restart.logged_steps(name):
        instance = {"instance": step["instance"]} if "instance" in step else {}
        if "step" in step:  # the frames and events replay it; what a step measured (monitors, say) doesn't matter
            step = {"step": step["step"], **({"events": step["events"]} if step.get("events") else {}), **instance}
        elif step.get("request") == "eval":
            step = {"eval": step["args"]["expr"], **instance}
        steps.append(step)
    return steps


def start_options(name, folder):
    """Session `name`'s start options as a scenario's start, from its start record: those not at their defaults, its
    seed, and its project, left out when `folder` (the scenario's) is inside it. Returns (start, notes)."""
    record = restart.read_record(name)
    options = record["options"]
    defaults = vars(restart.start_args(name, {"options": {"project": options["project"]}}))
    start = {key: value for key, value in options.items()
             if key not in NOT_SAVED and key in defaults and value != defaults[key]}
    if options.get("game_args"):
        start["args"] = options["game_args"]
    if options.get("seed") is not None:
        start["seed"] = options["seed"]
    if start.get("user_data_from"):
        start["user_data_from"] = os.path.relpath(start["user_data_from"], folder)
    project = Path(options["project"])
    if folder != project and project not in folder.parents:
        start = {"project": os.path.relpath(project, folder), **start}
    notes = []
    if options.get("recipe") or options.get("replay"):
        notes.append("the requests of the session's --recipe and --replay are steps of the scenario, as its input log "
                     "holds them")
    if options.get("companion") and Path(record.get("cwd", folder)) != folder:
        notes.append(f"the companions started from {record['cwd']}, and the scenario's start from {folder}: check "
                     f"their paths")
    return start, notes


def cmd_save_scenario(args):
    live.load_session(args.session)
    path = Path(args.file).resolve()
    if path.exists() and not args.force:
        raise live.LiveError(f"{path} exists; --force writes over it.")
    start, notes = start_options(args.session, path.parent)
    steps = recorded_steps(args.session)
    doc = {"start": start, "steps": steps, "expect": []}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n")
    if args.json:
        print(json.dumps({"file": str(path), "steps": len(steps), "start": start}))
    else:
        print(f"wrote {path}: {len(steps)} steps; add checks to \"expect\" (or turn an eval step into an expect) and "
              f"run it with gdh scenario run {args.file}")
    if not path.name.endswith(SUFFIX):
        notes.append(f"gdh test runs files named *{SUFFIX}")
    sys.stdout.flush()
    for note in notes:
        print(f"note: {note}", file=sys.stderr)
    return 0


def add_live_parsers(commands, command):
    p = command("save-scenario", cmd_save_scenario,
                "Write the session's start options and input log so far as a scenario file, to add checks to")
    p.add_argument("file", metavar="FILE", help="The scenario file to write (NAME.scenario.json)")
    p.add_argument("--force", action="store_true", help="Write over FILE if it exists")
