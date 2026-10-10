"""gdh live anim against testbed/anim/anim.tscn: Player (an AnimationPlayer looping "bob", 1 s) and Tree (an
AnimationTree whose state machine is "idle", or "move" while `moving`, "move" a blend space by `speed`), with two bugs
to switch on: `restart_bug` restarts "bob" every frame, `flip_bug` flips the state machine every 2 frames.

gdh live footslide against testbed/anim/walker.tscn: a Skeleton3D walking along x at 1.2 m/s, its feet foot.L and
foot.R taking turns on a 40-frame stride, each planted half of it; with `slide`, a planted foot moves with the body."""
import os

import pytest

from conftest import TESTBED, gdh, gdh_json
from gdh.anim import changes, flips, restarts
from gdh.footslide import guess_feet

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


# --- Foot sliding ---------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def walker(tmp_path_factory, display):
    out = tmp_path_factory.mktemp("walker")
    session = f"{SESSION}-walk"
    gdh("live", "start", "--project", TESTBED, "--scene", "res://anim/walker.tscn", "--session", session, "--out", out)
    yield session
    gdh("live", "stop", "--session", session)


def test_footslide_finds_planted_feet_that_stay_put(walker):
    reply = gdh_json("live", "footslide", "80", "--skeleton", "Walker/Skeleton", "--session", walker)
    assert reply["feet"] == ["foot.L", "foot.R"]
    for r in reply["reports"]:
        # Each foot is down about half the time, and doesn't move then.
        assert 38 <= r["planted_frames"] <= 44 and r["slide"] == pytest.approx(0, abs=1e-3)
        assert r["sliding_frames"] == 0 and r["worst"] is None
    assert "crops" not in reply


def test_footslide_finds_planted_feet_sliding(walker, tmp_path):
    gdh_json("live", "eval", "set('slide', true)", "--session", walker)
    try:
        reply = gdh_json("live", "footslide", "80", "--skeleton", "Walker/Skeleton", "--out", tmp_path / "slide.png",
                         "--session", walker)
    finally:
        gdh_json("live", "eval", "set('slide', false)", "--session", walker)
    for r in reply["reports"]:
        # A planted foot moving with the body: 1.2 m/s, 0.02 m a frame.
        assert r["worst"]["speed"] == pytest.approx(1.2, abs=0.01)
        assert r["sliding_frames"] >= 35 and r["slide"] == pytest.approx(0.02 * r["sliding_frames"], abs=0.03)
    assert reply["crops"] == str(tmp_path / "slide.png") and (tmp_path / "slide.png").exists()
    proc = gdh("live", "footslide", "40", "--skeleton", "Walker/Skeleton", "--bones", "foot.L", "--crops", "0",
               "--session", walker)
    assert "foot.L: planted" in proc.stdout and "foot.R" not in proc.stdout.split("feet")[1].split("\n", 1)[1]


def test_footslide_says_what_it_cant_follow(walker):
    for args, said in ((["--skeleton", "Nope"], "No node at Nope."),
                       (["--skeleton", "Walker"], "isn't a Skeleton3D"),
                       (["--skeleton", "Walker/Skeleton", "--bones", "toe.X"], "No bone 'toe.X' in Walker/Skeleton")):
        proc = gdh("live", "footslide", "5", *args, "--session", walker, check=False)
        assert proc.returncode == 1 and said in proc.stderr, (args, proc.stderr)


def test_foot_bones_are_guessed_by_name():
    assert guess_feet(["Hips", "foot.L", "foot.R", "toe.L"]) == ["foot.L", "foot.R"]
    assert guess_feet(["mixamorig:LeftFoot", "mixamorig:RightFoot", "mixamorig:LeftToeBase"]) == [
        "mixamorig:LeftFoot", "mixamorig:RightFoot"]
    assert guess_feet(["Ankle_L", "Ankle_R", "Hand_L"]) == ["Ankle_L", "Ankle_R"]
    assert guess_feet(["Spine", "Head"]) == []
