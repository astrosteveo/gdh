"""Options side by side: gdh live sweep against testbed/motion/motion.tscn (Ball, a Node2D drawn by its 32 px red
Body, at 200,360 moving right 4 px a frame while `moving`)."""
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from conftest import TESTBED, gdh, gdh_json
from gdh.compare import parse_sweep, split_values

SESSION = f"test-compare-{os.getpid()}"
BODY = "get_node('Ball/Body').color"


@pytest.fixture(scope="module")
def game(tmp_path_factory, display):
    out = tmp_path_factory.mktemp("compare")
    gdh("live", "start", "--project", TESTBED, "--scene", "res://motion/motion.tscn", "--session", SESSION,
        "--out", out)
    gdh_json("live", "eval", "[set('moving', false), get_node('Ball').set_position(Vector2(200, 360))]",
             "--session", SESSION)
    yield out
    gdh("live", "stop", "--session", SESSION)


def live(*args):
    return gdh_json("live", *args, "--session", SESSION)


def run(*args, check=True):
    return gdh("live", *args, "--session", SESSION, check=check)


def share(path, color):
    """The share of an image's pixels within 10 of a color in each channel."""
    with Image.open(path) as img:
        a = np.asarray(img.convert("RGB"), dtype=np.int16)
    return float((np.abs(a - np.array(color)).max(axis=2) <= 10).mean())


def test_sweep_two_properties_into_rows_and_columns(game, tmp_path):
    out = tmp_path / "grid.png"
    reply = live("sweep", f"{BODY}=Color(1, 0, 0), Color(0, 0, 1)", "get_node('Ball').scale=Vector2(1, 1), Vector2(2, 2)",
                 "--node", "Ball", "--margin", "20", "--out", out)
    assert reply["rows"] == 2 and reply["columns"] == 2 and reply["restored"] == 2
    assert [c["label"] for c in reply["cells"]] == [
        "color=Color(1, 0, 0), scale=Vector2(1, 1)", "color=Color(1, 0, 0), scale=Vector2(2, 2)",
        "color=Color(0, 0, 1), scale=Vector2(1, 1)", "color=Color(0, 0, 1), scale=Vector2(2, 2)"]
    cells = [c["image"] for c in reply["cells"]]
    # One frame for every cell: the box round Ball at its biggest, 64 px and 20 more each side.
    sizes = {Image.open(p).size for p in cells}
    assert sizes == {(104, 104)}
    # Each cell shows its values: red, then blue; twice the scale, four times the pixels.
    red_small, red_big, blue_small = share(cells[0], (255, 0, 0)), share(cells[1], (255, 0, 0)), share(cells[2], (0, 0, 255))
    assert red_small > 0.05 and red_big == pytest.approx(4 * red_small, rel=0.05) and blue_small == pytest.approx(red_small)
    with Image.open(out) as img:
        assert img.width <= 1280
    # Every value is put back.
    assert live("eval", f"[{BODY}, get_node('Ball').scale]")["result"]["value"] == ["Color(0.9, 0.3, 0.2, 1)", [1, 1]]


def test_sweep_one_property_wraps_and_prints(game, tmp_path):
    proc = run("sweep", "get_node('Ball').rotation_degrees=0, 15, 30, 45, 60", "--columns", "3", "--node", "Ball",
               "--out", tmp_path / "turn.png")
    assert "(5 cells, 2 by 3," in proc.stdout and "rotation_degrees=45: " in proc.stdout
    assert "put back: 1 property as they were" in proc.stdout
    assert live("eval", "get_node('Ball').rotation_degrees")["result"]["value"] == 0


def test_sweep_records_each_cell_from_a_reset(game, tmp_path):
    reset = "[set('moving', true), get_node('Ball').set_position(Vector2(200, 360))]"
    reply = live("sweep", "get_node('Ball').scale=Vector2(1, 1), Vector2(2, 2)", "--record", "12", "--every", "4",
                 "--as", "onion", "--node", "Ball", "--reset", reset, "--out", tmp_path / "rec.png")
    assert len(reply["cells"]) == 2 and all(Path(c["image"]).exists() for c in reply["cells"])
    proc = run("sweep", "get_node('Ball').scale=Vector2(1, 1), Vector2(2, 2)", "--record", "4",
               "--out", tmp_path / "rec2.png")
    assert "each cell's recording starts where the one before ended" in proc.stderr
    live("eval", "[set('moving', false), get_node('Ball').set_position(Vector2(200, 360))]")


def test_sweep_refuses_what_it_cant_do(game):
    for args, said in ((["nothing"], "A sweep is TARGET=VALUE,VALUE..."),
                       (["get_node('Ball').scale=Vector2(2, 2)"], "A sweep needs 2 values or more"),
                       (["get_node('Ball').nope=1, 2"], "has no property nope"),
                       (["get_node('Ball').scale=1, 2", "--reset", "x"], "--reset and --as go with --record"),
                       (["get_node('Ball').scale=1, 2", "--record", "4", "--as", "onion"], "--as onion needs --node")):
        proc = run("sweep", *args, check=False)
        assert proc.returncode == 1 and said in proc.stderr, (args, proc.stderr)
    assert live("eval", "get_node('Ball').scale")["result"]["value"] == [1, 1]


def test_sweep_parsing():
    assert split_values("1, Color(1, 0, 0), 'a,b', [1, 2]") == ["1", "Color(1, 0, 0)", "'a,b'", "[1, 2]"]
    assert parse_sweep("get_node('A.b').light_energy=0.5,1") == {
        "object": "get_node('A.b')", "property": "light_energy", "name": "light_energy", "values": ["0.5", "1"]}
    assert parse_sweep("speed=1,2")["object"] == ""
    assert parse_sweep("get_node('P').position:x=1,2")["name"] == "x"
