"""A game that pauses itself, against testbed/live/pause.tscn. gdh holds a game by pausing its tree; the game's own
pause (its pause menu open) is kept apart and given back when gdh lets it go, so the game stays paused across steps
until it unpauses itself, as for a player. A replay of steps the game was paused for comes back to the same frame.

The pause scene pauses on ui_cancel and shows a menu, which runs while the game is paused, with a Resume button; its
mover moves a pixel each frame the game runs in.
"""
import json
import os

import pytest

from conftest import TESTBED, gdh, gdh_json

SESSION = f"test-pause-{os.getpid()}"
X = "get_node('Mover').position.x"


@pytest.fixture(scope="module")
def game(tmp_path_factory, display):
    out = tmp_path_factory.mktemp("pause")
    gdh("live", "start", "--project", TESTBED, "--scene", "res://live/pause.tscn", "--session", SESSION, "--out", out)
    yield out
    gdh("live", "stop", "--session", SESSION)


def live(*args):
    return gdh_json("live", *args, "--session", SESSION)


def value(expr):
    return live("eval", expr)["result"]["value"]


def test_a_game_that_pauses_itself_stays_paused_across_steps(game):
    live("step", "10")
    assert value(X) == 10 and "game_paused" not in live("status")["result"]
    live("step", "1", "--tap", "ui_cancel")
    paused_at = value(X)
    # The next steps run their frames with the game still paused: only the menu, which runs while paused, runs.
    reply = live("step", "30")
    assert value(X) == paused_at and value("get_node('Menu').visible")
    assert reply["result"]["frames"] == 0 and reply["result"]["ran"] == 30
    status = live("status")["result"]
    assert status["held"] and status["game_paused"]
    assert "stepped 5 frames (the game paused itself for 5 of them)" in gdh("live", "step", "5", "--session",
                                                                           SESSION).stdout
    assert "paused by the game itself" in gdh("live", "status", "--session", SESSION).stdout
    # Resume, clicked while the game is paused, lets it go on.
    live("step", "2", "--click-text", "Resume")
    assert "game_paused" not in live("status")["result"] and not value("get_node('Menu').visible")
    reply = live("step", "10")
    assert reply["result"]["frames"] == 10 and "ran" not in reply["result"]
    assert value(X) > paused_at + 9


def test_a_replay_of_steps_the_game_was_paused_for_comes_back_to_the_same_frame(game):
    # The log keeps the frames each step ran, and the ones the game ran in when it was paused for some.
    steps = [e for e in map(json.loads, (game / "inputs.jsonl").read_text().splitlines()[1:]) if e["cmd"] == "step"]
    assert {"ran": 30, "game_frames": 0} == {k: steps[2][k] for k in ("ran", "game_frames")}
    frame = live("status")["result"]["frame"]
    x = value(X)
    proc = gdh("live", "restart", "--session", SESSION, "--replay")
    assert f"held at frame {frame}" in proc.stdout.splitlines()[-1]
    assert live("status")["result"]["frame"] == frame and value(X) == x
