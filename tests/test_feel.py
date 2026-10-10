"""Game feel in numbers against testbed/feel/feel.tscn: latency, the movement profile, hit-stop, shake and follow.

Hero, a CharacterBody2D at 60 frames a second, accelerates at 1200 px/s² to 240 px/s on the ground (600 px/s² in the
air), slows at 1800 px/s², jumps at 600 px/s under a gravity of 1800 px/s², can jump up to 6 frames after walking off
the floor's right end (x 800) and jumps on landing when the jump was pressed up to 5 frames before. Each press of
ui_accept sets Hero.pressed_at at once, and UI/Lamp lights 3 frames later. hit() stops time for 6 frames and shakes the
camera for 30 frames, 8 px along x at 10 Hz, decaying linearly; the camera follows Hero with position smoothing.
"""
import os

import pytest

from conftest import TESTBED, gdh, gdh_json

SESSION = f"test-feel-{os.getpid()}"
HERO_START = "get_node('Hero').set_global_position(Vector2(200, 376))"


@pytest.fixture(scope="module")
def feel(tmp_path_factory, display):
    out = tmp_path_factory.mktemp("feel")
    gdh("live", "start", "--project", TESTBED, "--scene", "res://feel/feel.tscn", "--session", SESSION, "--out", out)
    yield out
    gdh("live", "stop", "--session", SESSION)


def live(*args):
    return gdh_json("live", *args, "--session", SESSION)


def run(*args, check=True):
    return gdh("live", *args, "--session", SESSION, check=check)


def settle():
    """Hero back at its start, at rest on the floor."""
    live("eval", f"[{HERO_START}, get_node('Hero').set_velocity(Vector2(0, 0))]")
    live("step", "20")


# --- Latency ----------------------------------------------------------------------------------------------------------


def test_latency_counts_frames_to_the_state_and_to_the_pixels(feel):
    settle()
    reply = live("latency", "--press", "ui_accept", "--watch", "get_node('Hero').pressed_at", "--node", "UI/Lamp",
                 "--repeat", "3")
    assert len(reply["trials"]) == 3 and reply["ticks_per_second"] == 60
    # The state on the first frame; the lamp 3 frames later.
    assert reply["state"] == {"min": 1, "median": 1, "max": 1}
    assert reply["pixels"] == {"min": 4, "median": 4, "max": 4}
    assert reply["gap"] == {"min": 3, "median": 3, "max": 3}
    assert all(t["state"] == 1 and t["pixels"] == 4 for t in reply["trials"])


def test_latency_prints_and_says_when_nothing_answers(feel):
    settle()
    proc = run("latency", "--press", "ui_accept", "--node", "UI/Lamp", "--repeat", "2")
    assert "pixels (UI/Lamp): 4 frames (67 ms)" in proc.stdout
    proc = run("latency", "--press", "ui_left", "--watch", "get_node('Hero').pressed_at", "--repeat", "2", "--max", "5")
    assert "state (get_node('Hero').pressed_at): no response; no change within 5 frames in 2" in proc.stdout
    proc = run("latency", "--press", "ui_accept", check=False)
    assert proc.returncode == 1 and "latency needs what responds" in proc.stderr
