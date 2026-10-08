"""gdh live against testbed/live/arena.tscn.

The arena moves the player right at 120 px/s while ui_right is held, counts
ui_accept in _physics_process, _process and _input, and counts Go button
clicks; its Field is a LineEdit to type into. GameState is an autoload.
"""
import json
import os
import signal
import time

import pytest

from conftest import TESTBED, gdh, gdh_json, make_testbed_variant

SESSION = f"test-{os.getpid()}"


@pytest.fixture(scope="module")
def arena(tmp_path_factory, display):
    out = tmp_path_factory.mktemp("live")
    gdh("live", "start", "--project", TESTBED, "--scene", "res://live/arena.tscn",
        "--session", SESSION, "--out", out, "--", "--level", "3", "--hard")
    yield out
    gdh("live", "stop", "--session", SESSION)


def live(*args):
    return gdh_json("live", *args, "--session", SESSION)


def value(expr):
    return live("eval", expr)["result"]["value"]


def pipe(*requests):
    """Requests over one gdh live pipe, and their replies."""
    import subprocess
    import sys
    proc = subprocess.run([sys.executable, "-m", "gdh", "live", "pipe", "--session", SESSION],
                          input="".join(json.dumps(r) + "\n" for r in requests), capture_output=True, text=True)
    return [json.loads(line) for line in proc.stdout.splitlines()]


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


def test_type_puts_text_where_the_keys_go(arena):
    live("step", "2", "--click", "450,120")  # (the field takes the focus)
    live("step", "12", "--type", "Ada Lovelace")
    assert value("get_node('UI/Field').text") == "Ada Lovelace"
    live("step", "2", "--tap", "key:BackSpace")
    assert value("get_node('UI/Field').text") == "Ada Lovelac"
    value("get_node('UI/Field').release_focus()")  # (the keys go back to the game)


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


def test_right_hold_stays_down_for_the_step(arena):
    held, released = value("[right_held_frames, right_releases]")
    live("step", "12", "--right-hold", "300,200")
    after_held, after_released = value("[right_held_frames, right_releases]")
    # Down for the whole step, and let go at its end: the game sees the release before it's held again.
    assert after_held - held >= 11
    assert after_released == released + 1
    live("step", "5")
    assert value("right_held_frames") == after_held


def test_a_drag_carries_its_motion_and_buttons(arena):
    live("step", "2", "--move", "100,100")
    drag = value("right_drag")
    releases = value("right_releases")
    live("step", "3", "--press", "mouse:right")
    live("step", "3", "--move", "140,90")
    live("step", "3", "--move", "150,90")
    live("step", "2", "--release", "mouse:right")
    live("step", "2", "--move", "300,300")  # (no button held: not a drag)
    after = value("right_drag")
    # Motion reaches the game in its window's pixels, so compare against the screenshot's scale.
    scale = value("get_viewport().get_visible_rect().size.x") / value("get_viewport().get_texture().get_size().x")
    assert after[0] - drag[0] == pytest.approx(50 * scale, abs=0.5)
    assert after[1] - drag[1] == pytest.approx(-10 * scale, abs=0.5)
    assert value("right_releases") == releases + 1


def test_the_game_keeps_its_user_data_apart_from_the_players(arena):
    # user:// under gdh's own directory, never ~/.local/share/godot (the player's saves and logs).
    from gdh.godot import user_data_home
    assert value("OS.get_user_data_dir()").startswith(user_data_home() + "/")


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


def test_find_lists_what_shows_with_its_box(arena):
    go = live("find", "go")["result"]
    assert go["matches"] == [{"path": "UI/GoButton", "class": "Button", "text": "Go", "screen": [100, 100, 120, 50]}]
    assert go["matches"][0]["screen"] == live("tree", "UI/GoButton")["result"]["tree"]["screen"]
    controls = live("find", "--class", "Control")["result"]
    assert {"UI/GoButton", "UI/Field", "Player/Body"} <= {m["path"] for m in controls["matches"]}
    assert {"path": "UI/PauseMenu", "class": "Control", "why": "hidden"} in controls["hidden"]
    assert [m["path"] for m in live("find", "--name", "*button", "--class", "BaseButton")["result"]["matches"]] == ["UI/GoButton"]
    # One line each: path, class, text and box.
    assert gdh("live", "find", "Go", "--session", SESSION).stdout.splitlines() == [
        'UI/GoButton (Button) text="Go" screen=[100.0, 100.0, 120.0, 50.0]']
    proc = gdh("live", "find", "--name", "PauseMenu", "--session", SESSION, check=False)
    assert proc.returncode == 1 and "No node that shows matches" in proc.stderr
    assert "not showing: UI/PauseMenu (Control) hidden" in proc.stdout


def test_click_text_and_click_node_press_the_button(arena):
    before = value("clicks")
    aimed = live("step", "3", "--click-text", "go")["result"]["aimed"]
    assert aimed == [{"path": "UI/GoButton", "class": "Button", "text": "Go", "at": [160, 125]}]
    live("step", "3", "--click-node", "UI/GoButton")
    assert value("clicks") == before + 2
    # The raw protocol: a mouse event aimed with "on".
    on = {"text": "Go"}
    [reply] = pipe({"cmd": "step", "args": {"frames": 3, "events": [
        {"mouse_motion": [0, 0], "on": on, "at": 0}, {"mouse_button": 1, "on": on, "pressed": True, "at": 0},
        {"mouse_button": 1, "on": on, "pressed": False, "at": 1}]}})
    assert reply["ok"]
    assert value("clicks") == before + 3


def test_click_on_what_isnt_there_fails_naming_candidates(arena):
    frame = live("status")["result"]["frame"]

    def fails(*args):
        proc = gdh("live", "step", "3", *args, "--session", SESSION, check=False)
        assert proc.returncode == 1
        return proc.stderr

    missing = fails("--click-text", "Nope")
    assert "Nothing on screen shows \"Nope\"" in missing and "UI/GoButton (Button) \"Go\"" in missing
    assert "it's hidden" in fails("--click-node", "UI/PauseMenu")
    assert "No node at UI/Missing" in fails("--click-node", "UI/Missing")
    field = value("get_node('UI/Field').text")
    live("eval", "get_node('UI/Field').set('text', 'Go')")
    try:
        ambiguous = fails("--click-text", "go")
        assert "matches 2 nodes" in ambiguous and "UI/GoButton" in ambiguous and "UI/Field" in ambiguous
        live("eval", "get_node('UI/Field').set('text', 'Gone')")
        assert live("step", "1", "--click-text", "go")["result"]["aimed"][0]["path"] == "UI/GoButton"  # exact first
    finally:
        live("eval", f"get_node('UI/Field').set('text', {json.dumps(field)})")
    assert live("status")["result"]["frame"] == frame + 1  # a failed click steps nothing


def test_click_text_reaches_a_dialog(arena):
    # A dialog is a window embedded in the game's; its OK button is a node Godot makes inside it.
    live("eval", "get_node('UI').add_child(ClassDB.instantiate('AcceptDialog'))")
    dialog = "get_node('UI').get_child(-1)"
    try:
        live("eval", f"{dialog}.popup_centered(Vector2i(300, 120))")
        live("step", "2")
        ok = live("find", "OK", "--class", "Button")["result"]["matches"]
        assert len(ok) == 1 and ok[0]["path"].startswith("UI/@AcceptDialog")
        live("step", "3", "--click-text", "OK")
        assert value(f"{dialog}.visible") is False
    finally:
        live("eval", f"{dialog}.queue_free()")
        live("step", "1")


def test_find_and_click_inside_a_subviewport(arena):
    # A SubViewport shown by a SubViewportContainer at 2x, as pixel-art games draw: its nodes are placed through it.
    container = "get_node('UI').get_child(-1)"
    button = f"{container}.get_child(0).get_child(0)"
    made = pipe(*({"cmd": "eval", "args": {"expr": expr}} for expr in (
        "get_node('UI').add_child(ClassDB.instantiate('SubViewportContainer'))",
        f"{container}.set('stretch', true)", f"{container}.set('stretch_shrink', 2)",
        f"{container}.set('position', Vector2(700, 200))", f"{container}.set('size', Vector2(200, 100))",
        f"{container}.add_child(ClassDB.instantiate('SubViewport'))",
        f"{container}.get_child(0).add_child(ClassDB.instantiate('Button'))",
        f"{button}.set('text', 'Inner')", f"{button}.set('toggle_mode', true)",
        f"{button}.set('position', Vector2(10, 10))", f"{button}.set('size', Vector2(60, 40))")))
    try:
        assert all(r["ok"] for r in made)
        live("step", "2")
        assert [m["screen"] for m in live("find", "Inner")["result"]["matches"]] == [[720, 220, 120, 80]]
        live("step", "3", "--click-text", "Inner")
        assert value(f"{button}.button_pressed") is True
    finally:
        live("eval", f"{container}.queue_free()")
        live("step", "1")


def test_tree_visible_only_leaves_out_hidden_nodes(arena):
    assert "PauseMenu" in [c["name"] for c in live("tree", "UI")["result"]["tree"]["children"]]
    shown = [c["name"] for c in live("tree", "UI", "--visible-only")["result"]["tree"]["children"]]
    assert "PauseMenu" not in shown and "GoButton" in shown


def test_shot_writes_exactly_the_image_asked_for(arena, tmp_path):
    from PIL import Image, ImageChops

    def shot(*args):
        return live("shot", *args)["result"]

    full = shot("--out", tmp_path / "full.png")
    assert full["shots"] == {"normal": str(tmp_path / "full.png")}
    frame = Image.open(tmp_path / "full.png").convert("RGB")
    assert list(frame.size) == full["image_size"]

    def same(path, expected):
        image = Image.open(path).convert("RGB")
        return image.size == expected.size and ImageChops.difference(image, expected).getbbox() is None

    two = shot("--out", tmp_path / "two.png", "--view", "normal", "--view", "wireframe")["shots"]
    assert two == {"normal": str(tmp_path / "two-normal.png"), "wireframe": str(tmp_path / "two-wireframe.png")}
    crop = shot("--crop", "100,100,120,50", "--zoom", "2", "--out", tmp_path / "crop.png")
    assert crop["crop"] == [100, 100, 120, 50] and crop["size"] == [240, 100]
    assert same(tmp_path / "crop.png", frame.crop((100, 100, 220, 150)).resize((240, 100), Image.NEAREST))
    node = shot("--node", "UI/GoButton", "--margin", "10", "--out", tmp_path / "node.png")
    assert node["crop"] == [90, 90, 140, 70]
    assert same(tmp_path / "node.png", frame.crop((90, 90, 230, 160)))
    small = shot("--max-width", "640", "--out", tmp_path / "small.png")
    assert small["size"] == [640, 360] and Image.open(tmp_path / "small.png").size == (640, 360)
    zoomed = shot("--crop", "0,0,400,100", "--zoom", "4", "--max-width", "800", "--out", tmp_path / "both.png")
    assert zoomed["size"] == [800, 200]
    # Numbered shots go on as before.
    assert "/shots/" in shot()["shots"]["normal"]
    for bad, message in ((["--crop", "2000,2000,10,10"], "off it"), (["--zoom", "0"], "zoom"),
                         (["--node", "UI/PauseMenu"], "hidden"), (["--out", tmp_path / "x.jpg"], ".png")):
        proc = gdh("live", "shot", *bad, "--session", SESSION, check=False)
        assert proc.returncode == 1 and message in proc.stderr


def test_shot_no_ui_leaves_the_ui_out_of_that_shot_only(arena, tmp_path):
    from PIL import Image, ImageChops
    paths = [tmp_path / f"{name}.png" for name in ("before", "no-ui", "after")]
    live("shot", "--out", paths[0])
    live("shot", "--no-ui", "--out", paths[1])
    live("shot", "--out", paths[2])
    before, no_ui, after = (Image.open(p).convert("RGB") for p in paths)
    go = (100, 100, 220, 150)
    assert len(no_ui.crop(go).getcolors()) == 1  # the button's gone: only the background
    body = tuple(round(v) for v in live("find", "--name", "Body")["result"]["matches"][0]["screen"])
    body = (body[0], body[1], body[0] + body[2], body[1] + body[3])
    assert ImageChops.difference(no_ui.crop(body), before.crop(body)).getbbox() is None  # the game's still drawn
    assert ImageChops.difference(before, after).getbbox() is None


def test_camera_2d_looks_elsewhere_and_gives_the_view_back(arena, tmp_path):
    from PIL import Image, ImageChops
    x, y = value("get_node('Player').global_position")
    body = live("find", "--name", "Body")["result"]["matches"][0]["screen"]
    live("shot", "--out", tmp_path / "before.png")
    looked = live("camera", "--view", f"{x},{y}", "--zoom", "2")["result"]
    assert looked["camera"] == "2d" and looked["replaced"] == ""
    # The player is in the middle now, twice the size, while the game stays held.
    assert live("find", "--name", "Body")["result"]["matches"][0]["screen"] == [600, 320, 80, 80]
    assert live("camera", "--release")["result"]["released"]
    assert live("find", "--name", "Body")["result"]["matches"][0]["screen"] == body
    live("shot", "--out", tmp_path / "after.png")
    before, after = (Image.open(tmp_path / f"{n}.png").convert("RGB") for n in ("before", "after"))
    assert ImageChops.difference(before, after).getbbox() is None


def test_self_unpause_is_reported(arena):
    live("eval", "tree.set_pause(false)")
    reply = live("status")
    assert reply["held"]
    assert any("unpaused itself" in note for note in reply.get("notes", []))


def test_held_game_draws_about_20_frames_a_second(arena):
    # The bridge paces held frames itself: Godot skips its own limiter under --fixed-fps.
    first = value("Engine.get_frames_drawn()")
    time.sleep(2)
    assert 20 <= value("Engine.get_frames_drawn()") - first <= 70  # about 45 in 2 s and the round trips


def test_run_is_about_real_time(arena):
    start = live("status")["result"]["frame"]
    live("run")
    time.sleep(2)
    frames = live("pause")["result"]["frame"] - start
    assert 90 <= frames <= 180  # about 130 at 60 ticks a second


def test_commands_while_held_dont_wait_for_the_next_frame(arena):
    # Held frames come 50 ms apart; a request wakes the bridge, so a script's many commands don't each wait.
    import subprocess
    import sys
    requests = [{"cmd": "eval", "args": {"expr": "1"}}] * 40
    started = time.monotonic()
    proc = subprocess.run([sys.executable, "-m", "gdh", "live", "pipe", "--session", SESSION],
                          input="".join(json.dumps(r) + "\n" for r in requests), capture_output=True, text=True)
    elapsed = time.monotonic() - started
    assert proc.returncode == 0 and len(proc.stdout.splitlines()) == 40
    assert elapsed < 1.5  # 40 x 25 ms on average, were each to wait for its frame, would be 1 s more than this takes
    # Waiting for requests raises no engine errors of its own, as clients come and go.
    time.sleep(0.5)
    replies = [json.loads(line) for line in proc.stdout.splitlines()] + [live("status")]
    assert [e for r in replies for e in r.get("errors", [])] == []


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
        # find gives the same box, a click on the button's text or path lands too, and a shot of it is its box.
        assert run("find", "Go")["result"]["matches"][0]["screen"] == [x, y, w, h]
        run("step", "3", "--click-text", "Go")
        run("step", "3", "--click-node", "UI/GoButton")
        assert run("eval", "clicks")["result"]["value"] == 3
        assert run("shot", "--node", "UI/GoButton", "--out", tmp_path / "go.png")["result"]["size"] == [round(w), round(h)]
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


def test_camera_3d_looks_from_a_point_and_gives_the_view_back(tmp_path):
    import math
    from PIL import Image, ImageChops
    name = f"{SESSION}-camera"
    gdh("live", "start", "--project", TESTBED, "--scene", "res://smoke/smoke.tscn", "--session", name,
        "--out", tmp_path / "out")

    def run(*args):
        return gdh_json("live", *args, "--session", name)

    camera = "get_viewport().get_camera_3d().name"
    try:
        run("shot", "--out", tmp_path / "before.png")
        box = run("find", "--name", "Box")["result"]["matches"][0]["screen"]
        looked = run("camera", "--view", "0,8,0.01:0,0,0", "--fov", "60")["result"]
        assert looked["camera"] == "3d" and looked["replaced"] == "Camera3D" and looked["fov"] == 60
        assert run("eval", camera)["result"]["value"] == "GdhCamera3D"
        # Straight down: the box and the sphere side by side, level, where the game's camera saw them otherwise.
        seen = {m["path"]: m["screen"] for m in run("find", "--class", "MeshInstance3D")["result"]["matches"]}
        assert seen["Box"] != box and seen["Box"][0] < seen["Sphere"][0]
        assert seen["Box"][1] == pytest.approx(seen["Sphere"][1], abs=1)
        x, y, w, h = seen["Box"]
        framed = run("shot", "--node", "Box", "--out", tmp_path / "box.png")["result"]
        assert framed["size"] == [math.ceil(x + w) - math.floor(x), math.ceil(y + h) - math.floor(y)]
        assert run("camera", "--release")["result"]["restored"] == ["Camera3D"]
        assert run("eval", camera)["result"]["value"] == "Camera3D"
        run("shot", "--out", tmp_path / "after.png")
        before, after = (Image.open(tmp_path / f"{n}.png").convert("RGB") for n in ("before", "after"))
        assert ImageChops.difference(before, after).getbbox() is None
    finally:
        gdh("live", "stop", "--session", name)
