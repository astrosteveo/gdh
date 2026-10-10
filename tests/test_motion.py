"""The motion tools against testbed/motion/motion.tscn: trace charts and onion skins.

Ball moves right 4 px a physics frame while `moving`, Spinner turns 6 degrees a frame in place, Blinker changes color
every 10 frames, and with `follow` the camera keeps Ball at the screen's centre.
"""
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
