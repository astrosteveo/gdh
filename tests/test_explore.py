"""The game's events and exploration against testbed/explore/explore.tscn: Explorer (a platformer at x 420 on a floor
from 0 to 600) reports "checkpoint" the first time it passes x 500, "trapped" when it walks into the Trap (x 260 to
300, left of it), after which it ignores input, and "fell" when it drops below y 1200: the pit from 600 to 700 has no
bottom. Further right, past the pit, a Wall at x 1100 is too high to jump."""
import json
import os
import shutil

import pytest

from conftest import TESTBED, gdh, gdh_json
from gdh.events import install

SESSION = f"test-explore-{os.getpid()}"
START = "[get_node('Explorer').set_global_position(Vector2(420, 380)), get_node('Explorer').set_velocity(Vector2(0, 0)), " \
        "get_node('Explorer').set('trapped', false), get_node('Explorer').set('checkpoint', false), " \
        "get_node('Explorer').set('fell', false)]"


@pytest.fixture(scope="module")
def level(tmp_path_factory, display):
    out = tmp_path_factory.mktemp("explore")
    gdh("live", "start", "--project", TESTBED, "--scene", "res://explore/explore.tscn", "--session", SESSION,
        "--out", out, "--timeline")
    yield out
    gdh("live", "stop", "--session", SESSION)


def live(*args):
    return gdh_json("live", *args, "--session", SESSION)


def reset():
    live("eval", START)
    live("step", "10")
    live("events", "--clear", "--no-chart")


# --- The game's events --------------------------------------------------------------------------------------------


def test_events_come_with_their_frames(level):
    reset()
    proc = gdh("live", "step", "30", "--hold", "ui_right", "--session", SESSION)
    # 200 px/s from x 420: past 500 on the 24th frame.
    assert "event: checkpoint at frame" in proc.stderr and ': {"x": ' in proc.stderr
    reply = live("step", "90", "--hold", "ui_left")
    assert [e["name"] for e in reply["events"]] == ["trapped"]
    reply = live("events")
    assert [e["name"] for e in reply["events"]] == ["checkpoint", "trapped"]
    assert reply["counts"] == {"checkpoint": 1, "trapped": 1} and reply["chart"].endswith("events.png")
    first = reply["events"][0]["frame"]
    assert reply["events"][1]["frame"] > first and reply["events"][0]["data"]["x"] >= 500
    assert [e["name"] for e in live("events", "--name", "trapped")["events"]] == ["trapped"]
    proc = gdh("live", "events", "--clear", "--session", SESSION)
    assert "2 events: checkpoint 1, trapped 1" in proc.stdout and "cleared" in proc.stdout
    assert live("events")["events"] == []


def test_events_go_into_the_timeline(level):
    reset()
    live("step", "30", "--hold", "ui_right")
    lines = (level / "timeline" / "timeline.js").read_text().splitlines()
    steps = [json.loads(line[2:-2]) for line in lines if '"cmd": "step"' in line]
    names = [e["name"] for s in steps for r in s["replies"] for e in r.get("events", [])]
    assert "checkpoint" in names
    assert "event: ${x.name}" in (level / "timeline" / "index.html").read_text()


def test_install_puts_the_autoload_in_a_project(tmp_path):
    project = tmp_path / "game"
    project.mkdir()
    (project / "project.godot").write_text('config_version=5\n\n[application]\n\nconfig/name="game"\n')
    said = install(project)
    assert said == [f"wrote {project / 'addons' / 'gdh_events' / 'gdh_events.gd'}",
                    f"added the autoload GdhEvents to {project / 'project.godot'}"]
    text = (project / "project.godot").read_text()
    assert '[autoload]\n\nGdhEvents="*res://addons/gdh_events/gdh_events.gd"\n' in text
    # Again: nothing to do.
    assert install(project) == [f"GdhEvents is already an autoload in {project / 'project.godot'}"]
    assert (project / "project.godot").read_text() == text
    # A project with autoloads already: it goes in their section.
    other = tmp_path / "other"
    other.mkdir()
    (other / "project.godot").write_text('config_version=5\n\n[autoload]\n\nState="*res://state.gd"\n\n[rendering]\n')
    install(other)
    assert (other / "project.godot").read_text().count("[autoload]") == 1
    assert 'GdhEvents="*res://addons/gdh_events/gdh_events.gd"\nState=' in (other / "project.godot").read_text()
    proc = gdh("events", "install", "--project", tmp_path / "nothing", check=False)
    assert proc.returncode == 1 and "No project.godot in" in proc.stderr


# --- The explorer bot -----------------------------------------------------------------------------------------------

RESET = "[get_node('Explorer').set_global_position(Vector2({x}, 380)), get_node('Explorer').set_velocity(Vector2(0, 0)), " \
        "get_node('Explorer').set('trapped', false), get_node('Explorer').set('fell', false)]"


def test_explore_at_random_finds_the_trap_and_the_pit(level, tmp_path):
    reply = live("explore", "3000", "--node", "Explorer", "--seed", "7", "--reset", RESET.format(x=420),
                 "--out", tmp_path)
    kinds = {p["kind"] for p in reply["problems"]}
    assert {"soft lock", "falling"} <= kinds and reply["mode"] == "random" and reply["frames"] == 3000
    trap = next(p for p in reply["problems"] if p["kind"] == "soft lock")
    assert 260 <= trap["at"][0] <= 320  # in the trap
    pit = [p for p in reply["problems"] if p["kind"] == "falling"]
    assert all(560 <= p["at"][0] <= 700 for p in pit)  # each fall began at the pit
    assert reply["events"]["trapped"] >= 1 and reply["events"]["fell"] >= 1
    for p in reply["problems"]:
        doc = json.loads(open(p["scenario"]).read())
        assert doc["steps"] and doc["expect"][0]["expect"] == "get_node(\"Explorer\").global_position"
    assert (tmp_path / "heat.png").exists() and reply["reached"]["x"][0] < 320


def test_explore_stops_at_a_soft_lock_it_cant_undo_and_the_scenario_replays_it(tmp_path):
    session = f"{SESSION}-fresh"
    gdh("live", "start", "--project", TESTBED, "--scene", "res://explore/explore.tscn", "--session", session,
        "--out", tmp_path / "game")
    try:
        reply = gdh_json("live", "explore", "1200", "--node", "Explorer", "--seed", "3", "--out", tmp_path / "run",
                         "--session", session)
    finally:
        gdh("live", "stop", "--session", session)
    assert [p["kind"] for p in reply["problems"]] == ["soft lock"]
    assert "soft-locked at frame" in reply["stopped"] and reply["frames"] < 1200
    proc = gdh("scenario", "run", reply["problems"][0]["scenario"], "--out", tmp_path / "replay")
    assert "1 passed" in proc.stdout


def test_explore_toward_a_goal_finds_the_wall(level, tmp_path):
    reply = live("explore", "600", "--node", "Explorer", "--goal", "1300,380", "--reset", RESET.format(x=800),
                 "--out", tmp_path)
    assert reply["mode"] == "goal"
    stuck = [p for p in reply["problems"] if p["kind"] == "stuck"]
    # The wall's face is at x 1100, the explorer 24 px wide: as close as it gets, 1088.
    assert stuck and stuck[0]["at"][0] == pytest.approx(1088, abs=2)
    proc = gdh("live", "explore", "60", "--node", "Explorer", "--goal", "1300,380", "--reset", RESET.format(x=420),
               "--fail", "--session", SESSION, check=False)
    assert proc.returncode == 0  # nothing found in 60 frames


def test_explore_says_what_it_cant_do(level):
    for args, said in ((["--node", "Nope"], "No node at Nope."),
                       (["--node", "Explorer", "--mode", "goal"], "--mode goal needs --goal"),
                       (["--node", "Explorer", "--goal", "1,2,3"], "--goal takes X,Y for Explorer")):
        proc = gdh("live", "explore", "10", *args, "--session", SESSION, check=False)
        assert proc.returncode == 1 and said in proc.stderr, (args, proc.stderr)
