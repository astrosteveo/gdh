"""gdh.client, gdh scenario run, gdh live save-scenario and scenario files under gdh test, against
testbed/live/arena.tscn (testbed/scenarios/arena.scenario.json) and a small project of the test's own.

The arena moves the player right at 120 px/s while ui_right is held, counts Go button clicks, counts ui_accept in
GameState.jumps (an autoload), and has a LineEdit, Field.
"""
import json
import os
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import pytest
from PIL import Image, ImageDraw

from conftest import TESTBED, gdh
from gdh import live
from gdh.client import ClientError, Session, Unmet

ARENA = "res://live/arena.tscn"
EXAMPLE = TESTBED / "scenarios" / "arena.scenario.json"
PID = os.getpid()


def scenario_file(directory, name, doc):
    """A scenario file for the testbed's arena, written into directory."""
    path = directory / f"{name}.scenario.json"
    start = {"project": os.path.relpath(TESTBED, directory), "scene": ARENA, "resolution": "640x360"}
    path.write_text(json.dumps({"start": start, **doc}))
    return path


def run_scenarios(*files, out, session, extra=()):
    """gdh scenario run FILES, unchecked."""
    return gdh("scenario", "run", *files, "--out", out, "--session", session, *extra, check=False)


def gone(session):
    return not live.session_path(session).exists()


def test_the_client_drives_the_arena(tmp_path):
    name = f"s10-client-{PID}"
    with Session.start(TESTBED, scene=ARENA, name=name, out=tmp_path / "live", resolution="640x360",
                       args=["--level", "3"], echo=False) as game:
        assert game.status()["frame"] == 0
        result = game.step(30, hold="ui_right")
        assert result["frames"] == 30 and game.frame == 30
        assert game.eval("get_node('Player').position.x") == pytest.approx(260, abs=0.01)
        until = game.until("get_node('Player').position.x >= 300", max=600, every=5, hold="ui_right")
        assert until["met"] and until["frame"] == 50 and game.frame == 50
        with pytest.raises(Unmet) as unmet:
            game.until("clicks > 0", max=4)
        assert unmet.value.until["checks"] == 4 and "didn't hold in 4 frames" in str(unmet.value)
        clicked = game.click(text="Go")
        assert clicked["aimed"][0]["path"] == "UI/GoButton"
        assert game.eval("clicks") == 1
        assert [m["path"] for m in game.find("go")] == ["UI/GoButton"]
        shot = game.shot(out=tmp_path / "frame.png")
        assert Image.open(shot).size == (640, 360)
        button = game.shot(out=tmp_path / "go.png", node="UI/GoButton", zoom=2)
        assert Image.open(button).size == (240, 100)
        lines = game.batch("# a comment\nstep 2 --tap ui_accept\neval GameState.jumps")
        assert [entry["line"] for entry in lines] == ["step 2 --tap ui_accept", "eval GameState.jumps"]
        assert lines[1]["replies"][0]["result"]["value"] == 1
        with pytest.raises(ClientError, match="step 1 --click-text Nowhere"):
            game.batch(["step 1 --click-text Nowhere"])
        with pytest.raises(ClientError):
            game.eval("1 +")
        # Another pipe to the same session; closing it leaves the session running.
        with Session.attach(name, echo=False) as other:
            assert other.eval("clicks") == 2  # ui_accept pressed the Go button too: it had the focus
        assert game.eval("OS.get_cmdline_user_args()") == ["--level", "3"]
        assert game.errors == [] and game.frame == 58
    assert gone(name)
    with pytest.raises(ClientError, match="--resolution"):
        Session.start(TESTBED, scene=ARENA, name=f"s10-bad-{PID}", resolution="wide", echo=False)
    assert gone(f"s10-bad-{PID}")


def test_a_scenario_that_passes_writes_results_and_junit(tmp_path):
    session = f"s10-pass-{PID}"
    proc = run_scenarios(EXAMPLE, out=tmp_path, session=session)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "  pass  half a second right is 60 px: get_node('Player').position.x is 260" in proc.stdout
    assert "  pass  until get_node('Player').position.x >= 300: held at frame 52: true" in proc.stdout
    assert "gdh scenario: 1 passed" in proc.stdout
    results = json.loads((tmp_path / "results.json").read_text())
    arena = results["scenarios"][0]
    assert results["passed"] and arena["passed"] and arena["frame"] == 53
    assert [c["name"] for c in arena["checks"]] == [
        "half a second right is 60 px", "Go was clicked once", "until get_node('Player').position.x >= 300",
        "GameState.jumps == 1", "OS.get_cmdline_user_args() == [\"--level\", \"3\"]", "steps", "no engine errors"]
    assert Image.open(arena["shots"]["after-jump"]).size == (640, 360)
    assert Image.open(arena["shots"]["go-button"]).size == (240, 100)
    suite = ET.parse(tmp_path / "junit.xml").getroot().find("testsuite")
    assert suite.get("name") == "arena" and suite.get("tests") == "7" and suite.get("failures") == "0"
    assert gone(session)


def test_failing_checks_exit_1_naming_them_and_the_session_stops(tmp_path):
    session = f"s10-fail-{PID}"
    wrong = scenario_file(tmp_path, "wrong", {"steps": [
        "step 10 --hold ui_right",
        {"expect": "get_node('Player').position.x", "equals": 999, "name": "the player is far right"},
        {"expect": "get_node('Player').position.x > 200", "name": "the player moved"},
        {"until": "clicks > 0", "max": 5},
        {"expect": "clicks", "equals": 1, "name": "after the click"},
    ], "expect": ["true"]})
    broken_step = scenario_file(tmp_path, "broken", {"steps": [
        "step 3", "step 1 --click-text Nowhere", {"expect": "true", "name": "never reached"}]})
    not_json = tmp_path / "garbled.scenario.json"
    not_json.write_text("{steps: [")
    binary = tmp_path / "binary.scenario.json"
    binary.write_text(json.dumps({"start": {"binary": "/bin/true"}, "steps": ["step 1"]}))
    proc = run_scenarios(wrong, broken_step, not_json, binary, out=tmp_path / "out", session=session)
    assert proc.returncode == 1
    out = proc.stdout
    assert "  FAIL  the player is far right: get_node('Player').position.x was 220.0, expected 999" in out
    assert "  pass  the player moved" in out  # a failed expect doesn't stop the scenario
    assert "  FAIL  until clicks > 0: didn't hold in 5 frames: it was false at frame 15" in out
    assert "  skip  after the click: not reached: step 4 failed" in out
    assert "  skip  true: not reached" in out
    assert "  FAIL  step 2: step 1 --click-text Nowhere:" in out
    assert "  skip  never reached: not reached: step 2 failed" in out
    assert "  FAIL  load:" in out and "isn't JSON" in out
    assert "start.binary runs a program without one (--binary)" in out
    assert "wrong: FAILED (the player is far right, until clicks > 0)" in out
    assert "gdh scenario: 4 of 4 failed" in out
    results = json.loads((tmp_path / "out" / "results.json").read_text())
    assert [r["passed"] for r in results["scenarios"]] == [False, False, False, False]
    cases = {c.get("name"): c for c in ET.parse(tmp_path / "out" / "junit.xml").getroot().iter("testcase")}
    assert cases["the player is far right"].find("failure") is not None
    assert cases["the player moved"].find("failure") is None
    assert cases["after the click"].find("skipped") is not None
    assert gone(session)


def test_checkpoint_baselines_pass_unchanged_and_fail_changed(tmp_path):
    session = f"s10-base-{PID}"
    path = scenario_file(tmp_path, "look", {"steps": [
        "step 20 --hold ui_right", {"shot": "frame", "baseline": True}, {"shot": "go", "node": "UI/GoButton"}]})
    proc = run_scenarios(path, out=tmp_path / "first", session=session, extra=["--update-baselines"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    baseline = tmp_path / "baselines" / "look" / "frame.png"
    assert baseline.exists() and (baseline.parent / ".gdignore").exists()
    assert "  pass  shot frame: wrote the baseline" in proc.stdout
    proc = run_scenarios(path, out=tmp_path / "second", session=session)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "  pass  shot frame: the same as the baseline" in proc.stdout
    image = Image.open(baseline).convert("RGB")
    ImageDraw.Draw(image).rectangle([400, 200, 449, 239], fill=(0, 255, 0))
    image.save(baseline)
    proc = run_scenarios(path, out=tmp_path / "third", session=session)
    assert proc.returncode == 1
    assert "  FAIL  shot frame: " in proc.stdout and "(2000) changed by more than 2 from the baseline" in proc.stdout
    check = json.loads((tmp_path / "third" / "results.json").read_text())["scenarios"][0]["checks"][0]
    assert check["box"] == [400, 200, 450, 240] and os.path.exists(check["heatmap"])
    (tmp_path / "baselines" / "look" / "frame.png").unlink()
    proc = run_scenarios(path, out=tmp_path / "fourth", session=session)
    assert proc.returncode == 1 and "  FAIL  shot frame: no baseline at" in proc.stdout
    assert gone(session)


def test_save_scenario_replays_to_the_same_state(tmp_path):
    """A session driven every way in (the client, gdh live step, a batch line), saved from its input log."""
    name = f"s10-record-{PID}"
    with Session.start(TESTBED, scene=ARENA, name=name, out=tmp_path / "live", resolution="640x360",
                       echo=False) as game:
        seed = json.loads(live.session_path(name).read_text())["seed"]
        game.step(30, hold="ui_right")
        game.click(text="Go", frames=2)
        gdh("live", "step", "3", "--tap", "ui_accept", "--session", name)
        game.click(node="UI/Field", frames=2)
        game.step(5, type="pilot")
        game.batch("step 7 --hold ui_right\neval clicks")
        game.until("get_node('Player').position.x >= 330", max=600, every=3, hold="ui_right")
        game.request("camera", {"at": [320, 180], "zoom": 2.0})
        state = {expr: game.eval(expr) for expr in (
            "get_node('Player').position.x", "clicks", "GameState.jumps", "get_node('UI/Field').text")}
        frame = game.status()["frame"]
        path = tmp_path / "replay.scenario.json"
        saved = gdh("live", "save-scenario", "--session", name, path)
        assert "wrote" in saved.stdout and "13 steps" in saved.stdout
        refused = gdh("live", "save-scenario", "--session", name, path, check=False)
        assert refused.returncode == 1 and "--force" in refused.stderr
        game.shot(out=tmp_path / "live-end.png")
    assert gone(name)
    assert state["clicks"] == 2 and state["GameState.jumps"] == 1 and state["get_node('UI/Field').text"] == "pilot"
    doc = json.loads(path.read_text())
    assert doc["start"] == {"project": os.path.relpath(TESTBED, tmp_path), "scene": ARENA, "resolution": "640x360",
                            "seed": seed}
    assert doc["steps"][0] == {"step": 30, "events": [{"action": "ui_right", "pressed": True, "at": 0},
                                                      {"action": "ui_right", "pressed": False, "at": 30}]}
    assert doc["steps"][2]["step"] == 3  # gdh live step's
    assert doc["steps"][6] == {"eval": "clicks"}  # the batch's eval
    assert "until" not in doc["steps"][7]  # the frames the until ran
    assert doc["steps"][8] == {"request": "camera", "args": {"at": [320, 180], "zoom": 2.0}}
    doc["expect"] = [{"expect": expr, "equals": value} for expr, value in state.items()]
    doc["steps"].append({"shot": "end", "baseline": str(tmp_path / "live-end.png")})
    path.write_text(json.dumps(doc))
    proc = run_scenarios(path, out=tmp_path / "out", session=f"s10-replay-{PID}")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    replay = json.loads((tmp_path / "out" / "results.json").read_text())["scenarios"][0]
    assert replay["frame"] == frame
    assert "  pass  shot end: the same as the baseline" in proc.stdout


def test_a_stopped_run_still_stops_its_session(tmp_path):
    session = f"s10-term-{PID}"
    path = scenario_file(tmp_path, "long", {"steps": [{"until": "false", "max": 1000000}]})
    proc = subprocess.Popen([sys.executable, "-m", "gdh", "scenario", "run", path, "--out", tmp_path / "out",
                             "--session", session], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 120
    while not live.session_path(session).exists():
        assert proc.poll() is None and time.monotonic() < deadline, proc.communicate()
        time.sleep(0.2)
    game = json.loads(live.session_path(session).read_text())["pid"]
    time.sleep(1)
    proc.send_signal(signal.SIGTERM)
    proc.communicate(timeout=60)
    assert proc.returncode == 128 + signal.SIGTERM
    assert gone(session)
    deadline = time.monotonic() + 10
    while live.pid_alive(game) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not live.pid_alive(game)


@pytest.fixture
def small_project(tmp_path):
    """A project with a scene that counts its frames, a test for gdh's own runner and two scenario files."""
    project = tmp_path / "game"
    (project / "test").mkdir(parents=True)
    (project / "project.godot").write_text('config_version=5\n\n[application]\n\nconfig/name="scenarios"\n'
                                           'run/main_scene="res://main.tscn"\n')
    (project / "main.gd").write_text("extends Node2D\n\nvar ticks := 0\n\n\nfunc _physics_process(_delta: float) -> "
                                     "void:\n\tticks += 1\n")
    (project / "main.tscn").write_text('[gd_scene load_steps=2 format=3]\n\n[ext_resource type="Script" '
                                       'path="res://main.gd" id="1"]\n\n[node name="Main" type="Node2D"]\n'
                                       'script = ExtResource("1")\n')
    (project / "test" / "test_logic.gd").write_text("extends RefCounted\n\nfunc test_adds():\n\tassert(1 + 1 == 2)\n")
    (project / "test" / "count.scenario.json").write_text(json.dumps(
        {"steps": ["step 10"], "expect": [{"expect": "ticks", "equals": 10, "name": "ten ticks"}]}))
    (project / "test" / "wrong.scenario.json").write_text(json.dumps(
        {"steps": ["step 5"], "expect": [{"expect": "ticks", "equals": 99, "name": "ninety-nine ticks"}]}))
    return project


def test_gdh_test_runs_scenario_files_beside_other_tests(small_project, tmp_path, display):
    if display != "gpu" and os.environ.get("GDH_SCENARIO_TEST_RAN"):
        pytest.skip("once is enough")
    os.environ["GDH_SCENARIO_TEST_RAN"] = "1"
    proc = gdh("test", "--project", small_project, "--out", tmp_path / "all", check=False)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    # test_adds, and each scenario's checks: its expect, its steps and no engine errors.
    assert "gdh test (gdh): 7 tests, 6 passed, 1 failed" in proc.stdout
    assert "FAILED wrong > ninety-nine ticks" in proc.stdout
    report = json.loads((tmp_path / "all" / "report.json").read_text())
    assert report["scenarios"]["count"] == 2 and report["scenarios"]["failed"] == 1
    names = [c.get("name") for c in ET.parse(tmp_path / "all" / "junit.xml").getroot().iter("testcase")]
    assert "test_adds" in names and "ten ticks" in names
    proc = gdh("test", "--project", small_project, "res://test/count.scenario.json", "--out", tmp_path / "one")
    assert "gdh test (gdh): 3 tests, 3 passed, 0 failed" in proc.stdout
    assert not (tmp_path / "one" / "godot.log").exists()  # scenario files alone: no framework run
