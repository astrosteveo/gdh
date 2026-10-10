"""gdh live anim against testbed/anim/anim.tscn: Player (an AnimationPlayer looping "bob", 1 s) and Tree (an
AnimationTree whose state machine is "idle", or "move" while `moving`, "move" a blend space by `speed`), with two bugs
to switch on: `restart_bug` restarts "bob" every frame, `flip_bug` flips the state machine every 2 frames."""
import os

import pytest

from conftest import TESTBED, gdh, gdh_json
from gdh.anim import changes, flips, restarts

SESSION = f"test-anim-{os.getpid()}"


@pytest.fixture(scope="module")
def game(tmp_path_factory, display):
    out = tmp_path_factory.mktemp("anim")
    gdh("live", "start", "--project", TESTBED, "--scene", "res://anim/anim.tscn", "--session", SESSION, "--out", out)
    yield out
    gdh("live", "stop", "--session", SESSION)


def live(*args):
    return gdh_json("live", *args, "--session", SESSION)


def bugs(moving=False, speed=0.0, restart=False, flip=False):
    live("eval", f"[set('moving', {str(moving).lower()}), set('speed', {speed}), set('restart_bug', "
                 f"{str(restart).lower()}), set('flip_bug', {str(flip).lower()})]")


def test_anim_follows_a_player(game, tmp_path):
    bugs()
    reply = live("anim", "30", "--node", "Player", "--chart", tmp_path / "player.png")
    assert reply["kind"] == "player" and reply["warnings"] == [] and reply["restarts"] == []
    positions = reply["values"]["position"]
    # bob plays on, a frame (1/60 s) at a time, wrapping at 1 s.
    steps = [round((b - a) % 1.0, 4) for a, b in zip(positions, positions[1:])]
    assert all(s == pytest.approx(1 / 60, abs=1e-3) for s in steps)
    assert set(reply["values"]["animation"]) == {"bob"} and (tmp_path / "player.png").exists()


def test_anim_follows_a_tree_and_its_parameters(game):
    bugs()
    live("step", "5")
    bugs(moving=True, speed=0.7)
    reply = live("anim", "20", "--node", "Tree")
    assert reply["kind"] == "tree"
    # It goes to "move" on the step's first frame, before the first row is read, and stays there.
    assert reply["states"]["root state"] == {"values": ["move"], "changes": []}
    # Only the parameters a game sets: not the tree's read-only bookkeeping.
    assert set(k for k in reply["values"] if not k.startswith("root")) == {"move/blend_position"}
    assert reply["values"]["move/blend_position"][-1] == pytest.approx(0.7)
    assert reply["warnings"] == []
    # A node above them: the tree, first.
    assert live("anim", "2", "--node", ".")["kind"] == "tree"


def test_anim_flags_a_restart_every_frame(game):
    bugs(restart=True)
    try:
        proc = gdh("live", "anim", "20", "--node", "Player", "--session", SESSION)
    finally:
        bugs()
    assert "warning: bob started again 19 times while playing, 19 frames in a row at most" in proc.stdout


def test_anim_flags_a_state_machine_flipping(game):
    bugs(flip=True)
    try:
        reply = live("anim", "30", "--node", "Tree")
    finally:
        bugs()
    assert len(reply["states"]["root state"]["changes"]) >= 14
    assert any("root state flips back and forth" in w for w in reply["warnings"])


def test_anim_says_what_it_cant_watch(game):
    proc = gdh("live", "anim", "5", "--node", "Nope", "--session", SESSION, check=False)
    assert proc.returncode == 1 and "No node at Nope." in proc.stderr
    proc = gdh("live", "anim", "5", "--node", "Body", "--session", SESSION, check=False)
    assert proc.returncode == 1 and "is no AnimationPlayer or AnimationTree, and has none under it" in proc.stderr


def test_anim_rules():
    found = changes(["a", "a", "b", "a", "a", "c"], [1, 2, 3, 4, 5, 6])
    assert found == [(3, "a", "b"), (4, "b", "a"), (6, "a", "c")]
    assert flips(found) == [(3, "a", "b")]
    back, longest = restarts(["x"] * 5, [0.5, 0.0, 0.0, 0.0, 0.05], [True] * 5, [1, 2, 3, 4, 5], 60)
    assert back == [2, 3, 4] and longest == 3
