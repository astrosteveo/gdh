"""The motion tools against testbed/motion/motion.tscn: trace charts, onion skins, trails, filmstrips and change maps;
3D trails against motion3d.tscn.

Ball moves right 4 px a physics frame while `moving`, Spinner turns 6 degrees a frame in place, Blinker changes color
every 10 frames, and with `follow` the camera keeps Ball at the screen's centre. In motion3d.tscn Cube moves along x
0.05 a frame in front of the camera, and Ghost moves 0.25 a frame along z, from in front of the camera to behind it.
"""
import json
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from conftest import TESTBED, gdh, gdh_json
from gdh import chart, motion

SESSION = f"test-motion-{os.getpid()}"
BALL = "get_node('Ball').position"


@pytest.fixture(scope="module")
def game(tmp_path_factory, display):
    out = tmp_path_factory.mktemp("motion")
    gdh("live", "start", "--project", TESTBED, "--scene", "res://motion/motion.tscn", "--session", SESSION,
        "--out", out)
    yield out
    gdh("live", "stop", "--session", SESSION)


def value(expr):
    return live("eval", expr)["result"]["value"]


def reset():
    """Ball back at its start, moving, and the camera still at the screen's centre."""
    live("eval", "[get_node('Ball').set_position(Vector2(200, 360)), set('moving', true), set('follow', false), "
                 "get_node('Camera').set_position(Vector2(640, 360))]")


def live(*args):
    return gdh_json("live", *args, "--session", SESSION)


def run(*args, check=True):
    return gdh("live", *args, "--session", SESSION, check=check)


# --- Trace charts ---------------------------------------------------------------------------------------------------


def test_trace_chart_draws_a_panel_per_expression_with_rates(game, tmp_path):
    png, csv_path = tmp_path / "trace.png", tmp_path / "trace.csv"
    proc = run("step", "30", "--trace", BALL, "--trace", "moving", "--trace", "get_tree().current_scene.name",
               "--every", "2", "--trace-chart", png, "--trace-rates", "--trace-out", csv_path)
    assert f"chart: {png}" in proc.stdout
    # The scene's name is text: the chart says it left it out.
    assert "note: chart: get_tree().current_scene.name: not drawn: its values are text" in proc.stderr
    with Image.open(png) as img:
        assert img.width <= 1280 and img.height > 400
    # The numbers drawn: the position, its rate (4 px a frame at 60 ticks a second) and the rate of that.
    trace = chart.read_trace_csv(csv_path)
    panels = chart.panels_of(trace["exprs"], trace["rows"], rates=True, ticks=60)
    titles = [p["title"] for p in panels]
    assert titles == [BALL, f"d/dt {BALL} (per second)", f"d2/dt2 {BALL} (per second squared)", "moving",
                      "get_tree().current_scene.name"]
    speed = dict(panels[1]["lines"])
    assert all(v == pytest.approx(240) for v in speed["x"]) and all(v == 0 for v in speed["y"])
    assert all(v == pytest.approx(0) for v in dict(panels[2]["lines"])["x"])
    # true and false are drawn as steps, with no rate panels.
    assert panels[3]["steps"] and panels[3]["lines"][0][0] == "true or false"


def test_trace_chart_needs_a_trace(game, tmp_path):
    proc = run("step", "5", "--trace-chart", tmp_path / "x.png", check=False)
    assert proc.returncode == 1 and "--trace-chart draws the values of --trace" in proc.stderr
    proc = run("step", "5", "--trace", BALL, "--trace-rates", check=False)
    assert proc.returncode == 1 and "--trace-rates adds panels to the --trace-chart" in proc.stderr


def test_measure_chart_draws_a_saved_trace(game, tmp_path):
    csv_path = tmp_path / "trace.csv"
    run("step", "20", "--trace", BALL, "--trace", "ticks", "--trace-out", csv_path)
    out = tmp_path / "chart.png"
    reply = gdh_json("measure", "chart", csv_path, "--out", out, "--only", "ticks", "--title", "ticks")
    assert reply == {"chart": str(out), "rows": 20, "notes": []}
    assert out.exists()
    proc = gdh("measure", "chart", csv_path, "--out", out, "--only", "nope", check=False)
    assert proc.returncode == 1 and "has no column 'nope'" in proc.stderr


def test_chart_reads_vectors_flags_gaps_and_other_values():
    lines = chart.lines_of([[1, 2, 3], None, [4, None, 6]])
    assert [name for name, _ in lines] == ["x", "y", "z"]
    assert dict(lines)["y"] == [2.0, None, None]
    assert chart.lines_of([True, False, None]) == [("true or false", [1.0, 0.0, None])]
    assert chart.lines_of(["a", "b"]).startswith("not drawn: its values are text")
    assert chart.lines_of([1, [1, 2]]).startswith("not drawn: its values change shape")
    assert chart.lines_of([None, None]) == "not drawn: every value failed"
    # A rate across a gap is a gap.
    frames, values = chart.rate([0, 1, 2, 3], [0.0, 1.0, None, 3.0], 60)
    assert frames == [0.5, 1.5, 2.5] and values == [60.0, None, None]


# --- Onion skins ----------------------------------------------------------------------------------------------------


RED = np.array([230, 77, 51])  # Ball's color, Color(0.9, 0.3, 0.2), in 0-255


def test_onion_frames_the_nodes_path_and_fades_the_oldest(game, tmp_path):
    reset()
    shots = Path(game) / "shots"
    before = set(shots.glob("*.png")) if shots.exists() else set()
    out = tmp_path / "onion.png"
    reply = live("onion", "40", "--node", "Ball", "--out", out)
    # About 8 frames over the run: every 5th.
    assert reply["every"] == 5 and len(reply["frames"]) == 8
    first, last = int(reply["frames"][0].split()[1]), int(reply["frames"][-1].split()[1])
    assert last - first == 35
    # The box: Ball's 32 px body over the 4 px a frame it moved between the first frame saved and the last, and 12 px
    # of margin each side.
    x0, y0, x1, y1 = reply["box"]
    assert (x1 - x0, y1 - y0) == (32 + 4 * 35 + 24, 32 + 24)
    zoom = reply["zoom"]
    with Image.open(out) as img:
        assert img.width <= 1280
        a = np.asarray(img.convert("RGB"), dtype=np.int32)[motion.HEADER:]
    row = a[(12 + 4) * zoom]  # near the body's top, clear of the frame labels at its middle
    # The newest frame is solid: its body is Ball's own red. The oldest ghost's left edge, which no later frame
    # covers, is faint: between the background and red.
    newest = row[(x1 - x0 - 12 - 16) * zoom]
    oldest = row[(12 + 2) * zoom]
    background = row[2 * zoom]
    assert np.abs(newest - RED).max() <= 2
    assert np.abs(oldest - RED).max() > 40 and np.abs(oldest - background).max() > 20
    # The frames it saved are deleted.
    assert (set(shots.glob("*.png")) if shots.exists() else set()) == before


def test_onion_takes_step_input_and_keeps_frames_on_request(game, tmp_path):
    reply = live("onion", "6", "--node", "Spinner", "--every", "2", "--tint", "--tap", "ui_accept", "--keep",
                 "--out", tmp_path / "spin.png")
    assert len(reply["frames"]) == 3 and all(Path(p).exists() for p in reply["kept"])
    assert reply["status"]["frame"] == value("ticks")


def test_onion_says_why_it_cant_frame_a_node(game, tmp_path):
    proc = run("onion", "4", "--node", "Nope", check=False)
    assert proc.returncode == 1 and "No node at Nope." in proc.stderr
    live("eval", "get_node('Ball').hide()")
    try:
        proc = run("onion", "4", "--node", "Ball", check=False)
    finally:
        live("eval", "get_node('Ball').show()")
    assert proc.returncode == 1 and "Ball didn't show on screen in any of the" in proc.stderr
    proc = run("onion", "4", "--node", "Ball", "--every", "5", check=False)
    assert proc.returncode == 1 and "--every 5 is more than the 4 frames stepped" in proc.stderr


def test_measure_onion_on_recorded_frames(game, tmp_path):
    reset()
    frames = tmp_path / "frames"
    run("record", "12", "--out", frames, "--no-sheet")
    out = tmp_path / "onion.png"
    reply = gdh_json("measure", "onion", frames, "--every", "4", "--box", "150,300,400,420", "--out", out)
    assert reply["frames"] == ["frame 0", "frame 4", "frame 8"] and reply["box"] == [150, 300, 400, 420]
    assert reply["notes"] == [] and out.exists()


def test_onion_says_when_the_background_moves(tmp_path):
    rng = np.random.default_rng(1)
    frames = []
    for i in range(4):
        path = tmp_path / f"noise-{i}.png"
        Image.fromarray(rng.integers(0, 255, (40, 60, 3), dtype=np.uint8)).save(path)
        frames.append((f"frame {i}", path))
    info = motion.onion(frames, (0, 0, 60, 40), tmp_path / "out.png")
    assert "the background isn't still" in info["notes"][0]


def test_union_box_grows_points_and_clips_to_the_image():
    assert motion.union_box([[10, 10, 20, 20], None, [40, 5, 10, 10]], 2, (100, 100)) == (8, 3, 52, 32)
    assert motion.union_box([[50, 50]], 4, (100, 100)) == (18, 18, 82, 82)
    assert motion.union_box([[90, 90, 30, 30]], 0, (100, 100)) == (90, 90, 100, 100)
    assert motion.union_box([None], 4, (100, 100)) is None


# --- Trails -----------------------------------------------------------------------------------------------------------


YELLOW = np.array([255, 220, 40])  # the first trail's color


def test_trail_draws_the_path_with_a_dot_every_k_frames(game, tmp_path):
    reset()
    out = tmp_path / "trail.png"
    reply = live("step", "30", "--trail", "Ball", "--every", "5", "--trail-out", out)
    trails = reply["trails"]
    assert trails["image"] == str(out) and trails["every"] == 5
    ball = trails["trails"][0]
    start = reply["result"]["status"]["frame"] - 30
    # A dot every 5 frames from where it started, 20 px apart (4 px a frame), and the points in the reply's track.
    assert [d[0] - start for d in ball["dots"]] == [0, 5, 10, 15, 20, 25, 30]
    assert ball["dots"][0][1:] == [200, 360] and ball["dots"][-1][1:] == [320, 360]
    assert ball["spacing"] == [20.0] * 6 and ball["missing"] == 0 and ball["off_screen"] == 0
    points = reply["result"]["track"]["Ball"]["points"]
    assert len(points) == 31 and points[1][1:] == [204, 360]
    with Image.open(out) as img:
        a = np.asarray(img.convert("RGB"), dtype=np.int32)
        assert img.size == (1280, 720)
    assert np.abs(a[360, 250] - YELLOW).max() <= 10  # the line, between the dots at 240 and 260
    assert np.abs(a[360, 260] - YELLOW).max() <= 10  # a dot


def test_trail_shows_the_path_through_the_world_when_the_camera_follows(game, tmp_path):
    reset()
    live("eval", "set('follow', true)")
    try:
        reply = live("step", "30", "--trail", "Ball", "--every", "10", "--trail-out", tmp_path / "trail.png")
    finally:
        reset()
    ball = reply["trails"]["trails"][0]
    # Ball ends at the screen's centre, the camera on it; seen through that last view, it came from 120 px left.
    assert ball["dots"][-1][1:] == [640, 360] and ball["dots"][0][1:] == [520, 360]
    assert ball["spacing"] == [40.0] * 3


def test_trail_prints_the_spacing_and_is_refused_without_a_trail(game, tmp_path):
    reset()
    proc = run("step", "10", "--trail", "Ball", "--trail", "UI/Score", "--every", "5")
    assert "trail: " in proc.stdout
    assert "  Ball: from 200,360 at frame" in proc.stdout and "px between dots every 5 frames: 20, 20" in proc.stdout
    assert "  UI/Score: from 120,35" in proc.stdout
    image = proc.stdout.split("trail: ")[1].split()[0]
    assert Path(image).name.endswith("-trail.png") and Path(image).exists()
    proc = run("step", "5", "--trail-out", tmp_path / "x.png", check=False)
    assert proc.returncode == 1 and "--trail-out is where --trail's image goes" in proc.stderr
    proc = run("step", "5", "--trail", "Nope", check=False)
    assert proc.returncode == 1 and "No node at Nope." in proc.stderr


SESSION_3D = f"{SESSION}-3d"


@pytest.fixture(scope="module")
def game3d(tmp_path_factory, display):
    out = tmp_path_factory.mktemp("motion3d")
    gdh("live", "start", "--project", TESTBED, "--scene", "res://motion/motion3d.tscn", "--session", SESSION_3D,
        "--out", out)
    yield out
    gdh("live", "stop", "--session", SESSION_3D)


def test_trail_in_3d_projects_through_the_camera(game3d, tmp_path):
    proc = gdh("live", "step", "30", "--trail", "Cube", "--trail", "Ghost", "--every", "5", "--trail-out",
               tmp_path / "trail.png", "--session", SESSION_3D, "--json")
    reply = json.loads(proc.stdout)
    cube, ghost = reply["trails"]["trails"]
    expected = gdh_json("live", "eval", "get_viewport().get_camera_3d().unproject_position("
                                        "get_node('Cube').global_position)", "--session", SESSION_3D)
    assert cube["dots"][-1][1:] == pytest.approx(expected["result"]["value"], abs=0.1)
    assert cube["missing"] == 0 and len(set(cube["spacing"])) <= 2
    # Ghost goes behind the camera: those points have no place, and the rest leave the frame on the way.
    assert 0 < ghost["missing"] < 31 and ghost["off_screen"] > 0
    proc = gdh("live", "step", "30", "--trail", "Ghost", "--session", SESSION_3D)
    assert "points have no place on screen (behind the camera" in proc.stderr


# --- Filmstrips -------------------------------------------------------------------------------------------------------


def cells(path, reply, count):
    """Each filmstrip cell's pixels, from where motion.filmstrip puts them."""
    x0, y0, x1, y1 = reply["box"]
    zoom, columns = reply["zoom"], reply["columns"]
    cw, ch = (x1 - x0) * zoom, (y1 - y0) * zoom
    with Image.open(path) as img:
        a = np.asarray(img.convert("RGB"), dtype=np.int32)
    out = []
    for i in range(count):
        x = 4 + (i % columns) * (cw + 4)
        y = motion.HEADER + 4 + (i // columns) * (ch + 16 + 4) + 16
        out.append(a[y:y + ch, x:x + cw])
    return out


def test_filmstrip_labels_each_frame_and_keeps_the_box_fixed(game, tmp_path):
    out = tmp_path / "strip.png"
    reply = live("filmstrip", "36", "--node", "Spinner", "--out", out)
    # About 12 frames over the run, each labeled with its game frame, 3 apart.
    assert reply["every"] == 3 and len(reply["frames"]) == 12
    numbers = [int(f.split()[1]) for f in reply["frames"]]
    assert [b - a for a, b in zip(numbers, numbers[1:])] == [3] * 11
    assert numbers[-1] == reply["status"]["frame"]
    with Image.open(out) as img:
        assert img.width <= 1280 and img.height <= 1280
    # The box is the same for every cell, so the arm turning inside it shows: no two cells alike.
    strip = cells(out, reply, 12)
    assert all(c.shape == strip[0].shape for c in strip)
    assert all(np.abs(a - b).max() > 100 for a, b in zip(strip, strip[1:]))


def test_filmstrip_shows_a_flash_frame_by_frame(game, tmp_path):
    out = tmp_path / "blink.png"
    # Blinker changes color every 10 frames: cells 10 frames apart alternate.
    live("step", "--until", "ticks % 10 == 5")
    reply = live("filmstrip", "40", "--node", "Blinker", "--every", "10", "--margin", "0", "--out", out)
    middles = [c[c.shape[0] // 2, c.shape[1] // 2] for c in cells(out, reply, 4)]
    yellow = [bool(m[0] > 200 and m[2] < 100) for m in middles]
    assert yellow in ([True, False, True, False], [False, True, False, True])


def test_measure_filmstrip_and_its_limits(game, tmp_path):
    frames = tmp_path / "frames"
    run("record", "12", "--out", frames, "--no-sheet")
    reply = gdh_json("measure", "filmstrip", frames, "--every", "3", "--box", "560,120,720,280", "--out",
                     tmp_path / "strip.png")
    assert reply["frames"] == ["frame 0", "frame 3", "frame 6", "frame 9"] and reply["columns"] == 4
    proc = run("filmstrip", "60", "--node", "Spinner", "--every", "1", check=False)
    assert proc.returncode == 1 and "60 frames is too many for one filmstrip (at most 48)" in proc.stderr


# --- Change maps ------------------------------------------------------------------------------------------------------


def test_record_writes_a_change_map_that_counts_each_pixels_changes(game, tmp_path):
    reset()
    live("eval", "set('moving', false)")
    # Blinker flips every 10 frames: lined up so 40 frames hold 4 flips.
    live("step", "--until", "ticks % 10 == 5")
    frames = tmp_path / "rec"
    try:
        reply = live("record", "40", "--out", frames)
    finally:
        reset()
    changes = reply["changes"]
    assert changes["out"] == str(tmp_path / "rec-changes.png") and Path(changes["out"]).exists()
    assert changes["frames"] == 40 and changes["steps"] == 39
    # Two things moved: Spinner and Blinker. Ball stood still.
    boxes = [r["box"] for r in changes["regions"]]
    assert [1100, 80, 1160, 140] in boxes and len(boxes) == 2
    blinker = changes["regions"][boxes.index([1100, 80, 1160, 140])]
    assert blinker["px"] == 60 * 60 and blinker["most_steps"] == 4
    reply = gdh_json("measure", "changes", frames, "--out", tmp_path / "map.png", "--still")
    assert len(reply["crops"]) == 2 and all(Path(c).exists() for c in reply["crops"])
    with Image.open(tmp_path / "map.png") as img:
        a = np.asarray(img.convert("RGB"), dtype=np.int32)[motion.HEADER:]
    # --still: what never changed is tinted green; Blinker's pixels are on the hot scale (red first).
    assert a[600, 640][1] > a[600, 640][0] + 30
    assert a[110, 1130][0] > 200 and a[110, 1130][2] < 60
    proc = run("record", "4", "--out", tmp_path / "quiet", "--no-sheet")
    assert "change map" not in proc.stdout and not (tmp_path / "quiet-changes.png").exists()


def test_change_counts_are_exact(tmp_path):
    paths = []
    for i in range(5):
        a = np.zeros((20, 30, 3), np.uint8)
        a[2:6, 2:6] = 255 if i % 2 else 0  # flips every step
        if i >= 3:
            a[10:18, 20:28] = 200          # changes once, between frames 2 and 3
        a[0, 29] = 1                       # under the threshold: never counted
        path = tmp_path / f"f{i}.png"
        Image.fromarray(a).save(path)
        paths.append(path)
    info = motion.changes(paths, tmp_path / "map.png", crops_dir=tmp_path / "crops")
    assert info["steps"] == 4 and info["changed_px"] == 16 + 64 and info["every_step_px"] == 16
    assert [(r["px"], r["box"], r["most_steps"]) for r in info["regions"]] == [(64, [20, 10, 28, 18], 1),
                                                                               (16, [2, 2, 6, 6], 4)]
    assert len(info["crops"]) == 2
    proc = gdh("measure", "changes", paths[0], "--out", tmp_path / "x.png", check=False)
    assert proc.returncode == 1 and "A change map needs 2 frames or more." in proc.stderr
