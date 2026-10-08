"""gdh live step's wheel, modifiers, gamepad, touches and mouse-look, against testbed/input/devices.tscn.

The scene records each wheel notch, key, touch and drag its _input sees, counts mouse motions, sums relative motion
while the mouse is captured, and zooms its Map on Ctrl and the wheel. "save" is Ctrl+S, added to the Input Map by the
scene. The gamepad goes through Godot's built-in actions: ui_select is its Y button, ui_left and ui_right its D-pad
and left stick.
"""
import os

import pytest

from conftest import TESTBED, gdh, gdh_json

SESSION = f"test-input-{os.getpid()}"
LIST = "get_node('List').scroll_vertical"


@pytest.fixture(scope="module")
def devices(tmp_path_factory, display):
    out = tmp_path_factory.mktemp("input")
    gdh("live", "start", "--project", TESTBED, "--scene", "res://input/devices.tscn",
        "--session", SESSION, "--out", out)
    yield out
    gdh("live", "stop", "--session", SESSION)


def live(*args):
    return gdh_json("live", *args, "--session", SESSION)


def value(expr):
    return live("eval", expr)["result"]["value"]


def test_the_wheel_scrolls_what_the_pointer_is_over(devices):
    live("step", "1", "--move", "190,230")
    seen = value("wheel.size()")
    live("step", "1", "--wheel", "down")
    one = value(LIST)
    assert one > 0
    live("step", "2", "--wheel", "down:2")
    assert value(LIST) == pytest.approx(3 * one, abs=2)  # a notch a frame, each seen
    assert value(f"wheel.slice({seen})") == [[5, False, False]] * 3
    live("step", "3", "--wheel", "up:3")
    assert value(LIST) == 0


def test_ctrl_and_the_wheel_zoom_the_map(devices):
    zoom = value("zoom")
    live("step", "1", "--wheel", "up", "--wheel-at", "900,230")
    assert value("zoom") == zoom  # (the wheel alone doesn't zoom)
    live("step", "2", "--wheel", "up:2", "--wheel-at", "900,230", "--mod", "ctrl")
    assert value("zoom") == pytest.approx(zoom * 1.25 ** 2)
    # Each notch carries Ctrl, and the Ctrl key is down while it turns; let go at the step's end.
    assert value("wheel.slice(-2)") == [[4, True, True]] * 2
    assert value("Input.is_key_pressed(OS.find_keycode_from_string('Ctrl'))") is False
    assert value(LIST) == 0


def test_a_key_with_its_modifiers_reaches_the_input_map(devices):
    saves = value("saves")
    seen = value("keys.size()")
    live("step", "3", "--tap", "key:ctrl+s")
    assert value("saves") == saves + 1
    assert value(f"keys.slice({seen})") == [["Ctrl", True, False, False], ["S", True, True, False],
                                            ["S", False, True, False], ["Ctrl", False, False, False]]
    live("step", "3", "--tap", "key:s")
    assert value("saves") == saves + 1  # (S alone isn't save)
    seen = value("keys.size()")
    live("step", "3", "--tap", "key:a", "--mod", "shift")
    assert value(f"keys.slice({seen})") == [["Shift", True, False, False], ["A", True, False, True],
                                            ["A", False, False, True], ["Shift", False, False, False]]


def test_gamepad_buttons_reach_the_input_map(devices):
    frames, just = value("[select_frames, select_just]")
    live("step", "5", "--hold", "joy:y")
    assert value("[select_frames, select_just]") == [frames + 5, just + 1]
    live("step", "2", "--press", "joy:dpad_left")
    assert value("[Input.is_action_pressed('ui_left'), Input.is_joy_button_pressed(0, 13)]") == [True, True]
    live("step", "1", "--release", "joy:dpad_left")
    assert value("Input.is_action_pressed('ui_left')") is False


def test_a_stick_reaches_the_input_map_and_stays_put(devices):
    live("step", "2", "--axis", "left_x=0.75")
    deadzone = value("InputMap.action_get_deadzone('ui_right')")
    assert value("right_strength") == pytest.approx((0.75 - deadzone) / (1 - deadzone))
    assert value("[Input.get_action_raw_strength('ui_right'), Input.get_joy_axis(0, 0)]") == [0.75, 0.75]
    live("step", "2")
    assert value("Input.is_action_pressed('ui_right')") is True
    live("step", "1", "--axis", "left_x=-1")
    assert value("[Input.is_action_pressed('ui_right'), Input.get_action_strength('ui_left')]") == [False, 1.0]
    live("step", "1", "--axis", "left_x=0")
    assert value("Input.get_vector('ui_left', 'ui_right', 'ui_up', 'ui_down')") == [0, 0]


def test_a_touch_taps_and_stands_in_for_the_mouse(devices):
    seen, taps = value("[touches.size(), taps]")
    live("step", "2", "--touch", "560,525")  # on the Tap button
    assert value(f"touches.slice({seen})") == [[0, True, 560, 525], [0, False, 560, 525]]
    assert value("taps") == taps + 1
    assert value("[get_viewport().get_mouse_position(), DisplayServer.mouse_get_position()]") == [[560, 525]] * 2


def test_fingers_drag_together(devices):
    touches, drags = value("[touches.size(), drags.size()]")
    live("step", "5", "--touch-drag", "300,600:400,600", "--touch-drag", "900,600:800,650")
    assert value(f"touches.slice({touches})") == [[0, True, 300, 600], [1, True, 900, 600],
                                                  [0, False, 400, 600], [1, False, 800, 650]]
    moved = value(f"drags.slice({drags})")
    assert [d[0] for d in moved] == [0, 1] * 4  # each finger, every frame after the first
    for finger, end, total in ((0, [400, 600], [100, 0]), (1, [800, 650], [-100, 50])):
        mine = [d for d in moved if d[0] == finger]
        assert mine[-1][1:3] == end
        assert [sum(d[3] for d in mine), sum(d[4] for d in mine)] == total


def test_moves_put_the_display_pointer_there(devices):
    motions = value("motions")
    live("step", "1", "--move", "321,187")
    live("step", "3")
    # One motion: X's own for the pointer's warp never reaches the game.
    assert value("motions") == motions + 1
    assert value("[get_viewport().get_mouse_position(), DisplayServer.mouse_get_position()]") == [[321, 187]] * 2
    live("step", "1", "--look", "15,-7")
    assert value("[get_global_mouse_position(), DisplayServer.mouse_get_position()]") == [[336, 180]] * 2
    assert value("motions") == motions + 2


def test_look_turns_a_captured_mouse(devices):
    value("Input.set_mouse_mode(2)")  # captured
    try:
        look = value("look")
        live("step", "1", "--look", "40,-12")
        live("step", "1", "--look=-10,2")
        assert value("look") == [look[0] + 30, look[1] - 10]
        width, height = value("DisplayServer.window_get_size()")
        assert value("look_at") == [width // 2, height // 2]
    finally:
        value("Input.set_mouse_mode(0)")


@pytest.mark.parametrize("args, message", [
    (["--wheel", "sideways"], "--wheel takes"),
    (["--wheel", "down:3"], "step at least 3 frames"),
    (["--wheel-at", "10,10"], "--wheel-at needs a --wheel"),
    (["--axis", "left_x=2"], "--axis takes"),
    (["--tap", "joy:z"], "unknown gamepad button"),
    (["--tap", "key:hyper+s"], "unknown modifier"),
    (["--mod", "ctrl,super"], "unknown modifier"),
    (["--touch-drag", "1,2"], "--touch-drag takes X,Y:X,Y"),
])
def test_bad_input_is_refused(devices, args, message):
    proc = gdh("live", "step", *args, "--session", SESSION, check=False)
    assert proc.returncode != 0
    assert message in proc.stderr
