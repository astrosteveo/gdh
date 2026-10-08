"""gdh test: run a project's tests headless (or on gdh's display) and report what passed and what failed.

It runs GUT (addons/gut) or gdUnit4 (addons/gdUnit4) through their own command-line runners, or, in a project with
neither, gdh's own small runner (harness/test_runner.gd). Every framework writes JUnit XML, which gdh reads. Games run
under gdh keep user:// in gdh's own directory, so tests never touch the player's saves.
"""
import json
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from gdh import scenario
from gdh.editor_bridge import parse_engine_errors, to_res
from gdh.godot import HARNESS, GdhError, alert_shims, build_csharp, godot_binary, godot_cmd, godot_env, kill_groups
from gdh.imports import ensure_imported, run_import

RUNNER = HARNESS / "test_runner.gd"
DEFAULT_DIRS = ("res://test", "res://tests")
# gdUnit4 takes its report directory relative to the project, so its reports go in the editor's cache.
GDUNIT_REPORTS = "res://.godot/gdh/gdunit"


def detect(project):
    if (project / "addons" / "gut" / "gut_cmdln.gd").exists():
        return "gut"
    if (project / "addons" / "gdUnit4" / "bin" / "GdUnitCmdTool.gd").exists():
        return "gdunit4"
    return "gdh"


def default_paths(project):
    return [d for d in DEFAULT_DIRS if (project / d.removeprefix("res://")).is_dir()]


def framework_args(framework, project, paths, junit):
    """(the arguments after Godot's own, harness arguments for GDH_ARGS)."""
    if framework == "gut":
        args = ["-s", "res://addons/gut/gut_cmdln.gd", "-gexit", f"-gjunit_xml_file={junit}"]
        if paths:
            files = [p for p in paths if p.endswith(".gd")]
            dirs = [p for p in paths if not p.endswith(".gd")]
            args += [f"-gdir={','.join(dirs)}"] if dirs else []
            args += [f"-gtest={','.join(files)}"] if files else []
            args += ["-ginclude_subdirs"]
        elif not (project / ".gutconfig.json").exists():
            args += [f"-gdir={','.join(default_paths(project))}", "-ginclude_subdirs"]
        return args, []
    if framework == "gdunit4":
        args = ["-s", "-d", "res://addons/gdUnit4/bin/GdUnitCmdTool.gd", "-c", "--ignoreHeadlessMode",
                "-rd", GDUNIT_REPORTS]
        for p in paths or default_paths(project):
            args += ["-a", p]
        return args, []
    harness = ["--junit", str(junit)]
    for p in paths:
        harness += ["--path", p]
    return ["--script", str(RUNNER)], harness


def read_junit(path):
    """{"tests", "failed", "skipped", "failures": [{"suite", "test", "message"}]} from a JUnit XML file."""
    root = ET.parse(path).getroot()
    cases = root.iter("testcase")
    result = {"tests": 0, "failed": 0, "skipped": 0, "failures": []}
    for case in cases:
        result["tests"] += 1
        bad = case.find("failure") if case.find("failure") is not None else case.find("error")
        if bad is not None:
            result["failed"] += 1
            text = (bad.text or "").strip() or bad.get("message", "")
            result["failures"].append({"suite": case.get("classname", ""), "test": case.get("name", ""),
                                       "message": "\n".join(line.strip() for line in text.splitlines() if line.strip())})
        elif case.find("skipped") is not None or case.get("status") == "skipped":
            result["skipped"] += 1
    return result


def find_junit(framework, project, junit):
    if framework != "gdunit4":
        return junit if junit.exists() else None
    reports = project / GDUNIT_REPORTS.removeprefix("res://")
    found = sorted(reports.glob("report_*/results.xml"), key=lambda p: int(p.parent.name.split("_")[-1]))
    if not found:
        return None
    shutil.copyfile(found[-1], junit)
    return junit


def cmd_test(args):
    project = Path(args.project).resolve()
    if not (project / "project.godot").exists():
        raise GdhError(f"No project.godot in {project}.")
    framework = args.framework if args.framework != "auto" else detect(project)
    paths = [to_res(project, p) for p in args.paths]
    # Scenario files among the paths, or under them, run after the framework's tests, each in a live session.
    scenarios = scenario.files_under(project, paths or default_paths(project))
    paths = [p for p in paths if not p.endswith(scenario.SUFFIX)]
    if not args.no_build:
        build_csharp(project)
    if not args.no_import:
        ensure_imported(project)
        # Test frameworks find their classes by class_name, which needs the editor's class cache.
        if not (project / ".godot" / "global_script_class_cache.cfg").exists():
            run_import(project, quiet=True)
    out = Path(args.out).resolve() if args.out else Path(tempfile.mkdtemp(prefix="gdh-test-"))
    out.mkdir(parents=True, exist_ok=True)
    junit = out / "junit.xml"
    junit.unlink(missing_ok=True)
    if paths or not args.paths:
        result = run_framework(args, project, framework, paths, out, junit)
    else:  # scenario files alone
        result = {"tests": 0, "failed": 0, "skipped": 0, "failures": [], "framework": framework, "exit": 0,
                  "junit": str(junit), "log": None, "engine_errors": []}
    code = result["exit"]
    if scenarios:
        result = with_scenarios(result, scenarios, args, out, junit)
    (out / "report.json").write_text(json.dumps(result, indent=2))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        passed = result["tests"] - result["failed"] - result["skipped"]
        print(f"gdh test ({framework}): {result['tests']} tests, {passed} passed, {result['failed']} failed, "
              f"{result['skipped']} skipped -> {out}")
        if scenarios:
            s = result["scenarios"]
            print(f"  of them {s['checks']} checks in {s['count']} scenario files, {s['failed']} failed: "
                  f"{s['results']}")
        for f in result["failures"]:
            print(f"FAILED {f['suite']} > {f['test']}")
            for line in f["message"].splitlines()[:8]:
                print(f"    {line}")
        if code == "timeout":
            print(f"note: the run took over {args.timeout} s and was stopped; the results may be partial")
        shown = result["engine_errors"][:10]
        if shown:
            print(f"{len(result['engine_errors'])} engine errors during the run (see report.json):")
            for e in shown:
                print(f"  {e['type']}: {e['message']} at {e['where']}")
    ok = result["failed"] == 0 and result["tests"] > 0 and code != "timeout"
    if result["tests"] == 0:
        print("note: no tests ran. Tests live in res://test or res://tests, or pass paths.")
    return 0 if ok else 1


def with_scenarios(result, files, args, out, junit):
    """The framework's result with the scenario files' checks added: each check a test, each scenario a suite,
    appended to junit.xml too (scenario.py). A scenario runs on gdh test's --display, or, headless, on the default."""
    doc = scenario.run_files(files, out / "scenarios", display=None if args.display == "none" else args.display,
                             start_extra={"no_build": True, "no_import": True}, quiet=args.json)
    root = ET.parse(junit).getroot() if junit.exists() else ET.Element("testsuites")
    if root.tag != "testsuites":
        whole = ET.Element("testsuites")
        whole.append(root)
        root = whole
    root.extend(ET.parse(doc["junit"]).getroot())
    ET.ElementTree(root).write(junit, encoding="utf-8", xml_declaration=True)
    merged = read_junit(junit)
    checks = [c for r in doc["scenarios"] for c in r["checks"]]
    result.update({k: merged[k] for k in ("tests", "failed", "skipped", "failures")})
    result.update({"junit": str(junit), "scenarios": {
        "count": len(files), "checks": len(checks), "failed": sum(c["passed"] is False for c in checks),
        "passed": doc["passed"], "results": doc["results"], "junit": doc["junit"]}})
    return result


def run_framework(args, project, framework, paths, out, junit):
    """Run the framework's tests on `paths`; its result, read from its JUnit XML."""
    shutil.rmtree(project / GDUNIT_REPORTS.removeprefix("res://"), ignore_errors=True)
    extra, harness = framework_args(framework, project, paths, junit)
    log_path = out / "godot.log"
    display = None
    with alert_shims() as shims:
        if args.display == "none":
            cmd = [godot_binary(project), "--headless", "--path", str(project), *extra]
            env = godot_env(shims, ":gdh-no-display", harness)
        else:
            from gdh.display import open_display
            display = open_display(args.display, args.resolution, out / "display.log")
            cmd = godot_cmd(project, args.resolution, extra)
            env = godot_env(shims, display.name, harness)
        try:
            with open(log_path, "w") as log:
                proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                        start_new_session=True)
                try:
                    code = proc.wait(timeout=args.timeout)
                except subprocess.TimeoutExpired:
                    code = "timeout"
                finally:
                    kill_groups(proc.pid)
                    proc.wait()
        finally:
            if display is not None:
                display.stop()
    log = log_path.read_text(errors="replace")
    engine_errors = [e for e in parse_engine_errors(log) if e["type"] != "warning"]
    found = find_junit(framework, project, junit)
    if found is None:
        detail = "\n".join(f"  {e['type']}: {e['message']} at {e['where']}" for e in engine_errors[:10])
        tail = "\n".join(log.splitlines()[-15:])
        raise GdhError(f"{framework} wrote no results (exit {code}). "
                       + (f"Engine errors:\n{detail}" if detail else f"Last lines of {log_path}:\n{tail}"))
    result = read_junit(found)
    result.update({"framework": framework, "exit": code, "junit": str(found), "log": str(log_path),
                   "engine_errors": engine_errors[:50]})
    if code == "timeout":
        result["timed_out"] = True
    return result


def add_parser(sub):
    p = sub.add_parser("test", help="Run the project's tests (GUT, gdUnit4, or gdh's own runner) and report failures")
    p.add_argument("--project", required=True, help="Godot project directory")
    p.add_argument("paths", nargs="*", help="Test files, scenario files (*.scenario.json) or directories (default: "
                                            "res://test and res://tests)")
    p.add_argument("--framework", choices=["auto", "gut", "gdunit4", "gdh"], default="auto",
                   help="auto: GUT if addons/gut exists, gdUnit4 if addons/gdUnit4 does, else gdh's own runner")
    p.add_argument("--out", help="Where to keep junit.xml, report.json and godot.log (default: a new temporary directory)")
    p.add_argument("--timeout", type=int, default=600, help="Seconds before the run is stopped (default 600)")
    p.add_argument("--resolution", default="1280x720", help="The window's size, with --display gpu or xvfb")
    p.add_argument("--display", choices=["none", "auto", "gpu", "xvfb"], default="none",
                   help="none: headless, nothing drawn (the default, fine for logic); gpu, xvfb or auto: run on a "
                        "display of gdh's, for tests that render or need input events")
    p.add_argument("--no-build", action="store_true", help="Don't build a C# project's assemblies first")
    p.add_argument("--no-import", action="store_true", help="Don't import the project first")
    p.add_argument("--json", action="store_true", help="Print the report as JSON")
    p.set_defaults(func=cmd_test)
