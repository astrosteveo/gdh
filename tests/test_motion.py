"""The motion tools against testbed/motion/motion.tscn: trace charts.

Ball moves right 4 px a physics frame while `moving`, Spinner turns 6 degrees a frame in place, Blinker changes color
every 10 frames, and with `follow` the camera keeps Ball at the screen's centre.
"""
import os

import pytest
from PIL import Image

from conftest import TESTBED, gdh, gdh_json
from gdh import chart

SESSION = f"test-motion-{os.getpid()}"
BALL = "get_node('Ball').position"


@pytest.fixture(scope="module")
def motion(tmp_path_factory, display):
    out = tmp_path_factory.mktemp("motion")
    gdh("live", "start", "--project", TESTBED, "--scene", "res://motion/motion.tscn", "--session", SESSION,
        "--out", out)
    yield out
    gdh("live", "stop", "--session", SESSION)


def live(*args):
    return gdh_json("live", *args, "--session", SESSION)


def run(*args, check=True):
    return gdh("live", *args, "--session", SESSION, check=check)


# --- Trace charts ---------------------------------------------------------------------------------------------------


def test_trace_chart_draws_a_panel_per_expression_with_rates(motion, tmp_path):
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


def test_trace_chart_needs_a_trace(motion, tmp_path):
    proc = run("step", "5", "--trace-chart", tmp_path / "x.png", check=False)
    assert proc.returncode == 1 and "--trace-chart draws the values of --trace" in proc.stderr
    proc = run("step", "5", "--trace", BALL, "--trace-rates", check=False)
    assert proc.returncode == 1 and "--trace-rates adds panels to the --trace-chart" in proc.stderr


def test_measure_chart_draws_a_saved_trace(motion, tmp_path):
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
