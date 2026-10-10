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
