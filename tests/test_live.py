"""gdh live against testbed/live/arena.tscn.

The arena moves the player right at 120 px/s while ui_right is held, counts
ui_accept in _physics_process, _process and _input, and counts Go button
clicks. GameState is an autoload.
"""
import json
import os
import signal
import time

import pytest

from conftest import TESTBED, gdh, gdh_json, make_testbed_variant

SESSION = f"test-{os.getpid()}"


@pytest.fixture(scope="module")
def arena(tmp_path_factory):
    out = tmp_path_factory.mktemp("live")
    gdh("live", "start", "--project", TESTBED, "--scene", "res://live/arena.tscn",
        "--session", SESSION, "--out", out, "--", "--level", "3", "--hard")
    yield out
    gdh("live", "stop", "--session", SESSION)


def live(*args):
    return gdh_json("live", *args, "--session", SESSION)


def value(expr):
    return live("eval", expr)["result"]["value"]


def test_eval_reaches_engine_singletons(arena):
    assert value("Engine.get_physics_ticks_per_second()") == 60


def test_game_gets_only_its_own_arguments(arena):
    assert value("OS.get_cmdline_user_args()") == ["--level", "3", "--hard"]


def test_starts_held_at_frame_zero(arena):
    first = live("status")
    time.sleep(1)
    second = live("status")
    assert first["result"]["frame"] == second["result"]["frame"] == 0
    assert second["held"]


def test_reports_nodes_that_run_while_held(arena):
    nodes = live("status")["result"]["runs_while_held"]
    assert {"node": "UI/PauseMenu", "mode": "when_paused"} in nodes


def test_step_runs_exact_frames(arena):
    x = value("get_node('Player').position.x")
    reply = live("step", "30", "--hold", "ui_right")
    assert reply["result"]["frames"] == 30
    # 120 px/s for 30 frames at 60 ticks per second.
    assert value("get_node('Player').position.x") == pytest.approx(x + 60)


def test_tap_reaches_every_callback_once(arena):
    before = value("[just_physics, just_process, input_events, GameState.jumps]")
    live("step", "5", "--tap", "ui_accept")
    live("step", "5", "--tap", "key:Space")
    after = value("[just_physics, just_process, input_events, GameState.jumps]")
    assert [a - b for a, b in zip(after, before)] == [2, 2, 2, 2]


def test_press_persists_across_steps(arena):
    x = value("get_node('Player').position.x")
    live("step", "10", "--press", "ui_right")
    live("step", "10")
    live("step", "1", "--release", "ui_right")
    assert value("get_node('Player').position.x") == pytest.approx(x + 40)


def test_click_presses_button(arena):
    before = value("clicks")
    live("step", "4", "--click", "160,125")
    assert value("clicks") == before + 1


def test_shots_and_step_recording(arena):
    shots = live("shot", "--view", "normal", "--view", "wireframe")["result"]["shots"]
    assert set(shots) == {"normal", "wireframe"}
    recorded = live("step", "20", "--shot-every", "10")["result"]["shots"]
    assert len(recorded) == 2
    for path in [*shots.values(), *recorded]:
        assert os.path.getsize(path) > 0


def test_tree_and_errors(arena):
    tree = live("tree")["result"]["tree"]
    assert tree["name"] == "Arena"
    assert "Player" in [c["name"] for c in tree["children"]]
    reply = live("eval", "get_node('Missing')")
    assert any("Missing" in e["message"] for e in reply["errors"])


def test_self_unpause_is_reported(arena):
    live("eval", "tree.set_pause(false)")
    reply = live("status")
    assert reply["held"]
    assert any("unpaused itself" in note for note in reply.get("notes", []))


def test_bad_scene_fails_cleanly():
    proc = gdh("live", "start", "--project", TESTBED, "--scene", "res://missing.tscn",
               "--session", f"{SESSION}-bad", check=False)
    assert proc.returncode == 1
    assert "Can't load scene" in proc.stderr


def test_idle_timeout_and_dead_session_cleanup(tmp_path):
    name = f"{SESSION}-idle"
    gdh("live", "start", "--project", TESTBED, "--scene", "res://live/arena.tscn",
        "--session", name, "--out", tmp_path, "--idle-timeout", "2")
    time.sleep(6)
    proc = gdh("live", "status", "--session", name, check=False)
    assert proc.returncode == 1
    assert "has ended" in proc.stderr


def test_killed_session_is_cleaned_up(tmp_path):
    from gdh.live import session_path
    name = f"{SESSION}-kill"
    gdh("live", "start", "--project", TESTBED, "--scene", "res://live/arena.tscn",
        "--session", name, "--out", tmp_path)
    os.kill(json.loads(session_path(name).read_text())["pid"], signal.SIGKILL)
    time.sleep(1)
    proc = gdh("live", "status", "--session", name, check=False)
    assert "has ended" in proc.stderr
    assert not session_path(name).exists()


def start_variant(tmp_path, name, settings, scene="res://live/arena.tscn"):
    project = make_testbed_variant(tmp_path / "project", settings)
    gdh("live", "start", "--project", project, "--scene", scene, "--session", name, "--out", tmp_path / "out")
    return lambda *args: gdh_json("live", *args, "--session", name)


@pytest.mark.parametrize("mode", ["viewport", "canvas_items"])
def test_click_lands_where_tree_says_under_stretch(tmp_path, mode):
    name = f"{SESSION}-{mode}"
    run = start_variant(tmp_path, name, "[display]\n\nwindow/size/viewport_width=640\n"
                        f"window/size/viewport_height=360\nwindow/stretch/mode=\"{mode}\"")
    try:
        x, y, w, h = run("tree", "UI/GoButton")["result"]["tree"]["screen"]
        run("step", "3", "--click", f"{x + w / 2},{y + h / 2}")
        run("step", "3", "--click", "5,5")
        assert run("eval", "clicks")["result"]["value"] == 1
    finally:
        gdh("live", "stop", "--session", name)


def test_step_follows_project_tick_rate(tmp_path):
    name = f"{SESSION}-ticks"
    run = start_variant(tmp_path, name, "[physics]\n\ncommon/physics_ticks_per_second=120")
    try:
        assert run("status")["result"]["ticks_per_second"] == 120
        x = run("eval", "get_node('Player').position.x")["result"]["value"]
        assert run("step", "30", "--hold", "ui_right")["result"]["frames"] == 30
        # 120 px/s for 30 ticks at 120 ticks per second.
        assert run("eval", "get_node('Player').position.x")["result"]["value"] == pytest.approx(x + 30)
    finally:
        gdh("live", "stop", "--session", name)
