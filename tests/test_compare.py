"""Options side by side: gdh live sweep against testbed/motion/motion.tscn (Ball, a Node2D drawn by its 32 px red
Body, at 200,360 moving right 4 px a frame while `moving`), gdh capture --matrix and gdh review."""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from conftest import TESTBED, gdh, gdh_json
from gdh.compare import QUALITY, parse_qualities, parse_screens, parse_sweep, split_values
from gdh.override import Raw, render

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


# --- The display and quality matrix -----------------------------------------------------------------------------------


def test_matrix_captures_each_screen_and_quality(tmp_path):
    out = tmp_path / "matrix"
    proc = gdh("capture", "--project", TESTBED, "--scene", "res://motion/motion.tscn", "--out", out, "--modes", "normal",
               "--matrix", "16:9,phone,1000x500", "--quality", "project,low",
               "--set", "rendering/environment/defaults/default_clear_color=Color(0.1, 0.5, 0.1, 1)")
    assert f"matrix: {out / 'matrix.png'} (3 screens by 2 qualities;" in proc.stdout
    record = json.loads((out / "matrix.json").read_text())
    assert [(c["screen"], c["quality"]) for c in record["cells"]] == [
        ("16:9", "project"), ("16:9", "low"), ("phone", "project"), ("phone", "low"), ("1000x500", "project"),
        ("1000x500", "low")]
    assert all(c["ok"] for c in record["cells"]) and record["rows"] == 3 and record["columns"] == 2
    # Each cell is a capture of its own, at its screen's size, with the settings it ran with.
    for cell, size in zip(record["cells"][::2], [(1280, 720), (720, 1280), (1000, 500)]):
        with Image.open(Path(cell["dir"]) / "normal.png") as img:
            assert img.size == size
            assert img.getpixel((2, 2)) == (25, 127, 25)  # --set's clear color, through the override.cfg
        assert (Path(cell["dir"]) / "report.json").exists()
    assert record["cells"][1]["settings"]["rendering/scaling_3d/scale"] == 0.5
    with Image.open(out / "matrix.png") as img:
        assert img.width <= 1280
    assert not (TESTBED / "override.cfg").exists()


def test_matrix_refuses_what_it_cant_do(tmp_path):
    for args, said in ((["--matrix", "huge"], "Unknown screen 'huge'"),
                       (["--matrix", "16:9", "--quality", "ultra"], "Unknown quality 'ultra'"),
                       (["--quality", "low"], "--quality and --set go with --matrix"),
                       (["--matrix", "16:9", "--set", "nope"], "--set takes SECTION/KEY=VALUE"),
                       (["--matrix", "16:9", "--baseline", tmp_path], "--matrix and --baseline don't go together")):
        proc = gdh("capture", "--project", TESTBED, "--scene", "res://motion/motion.tscn", "--out", tmp_path / "x",
                   *args, check=False)
        assert proc.returncode == 1 and said in proc.stderr, (args, proc.stderr)


def test_matrix_parsing():
    assert parse_screens("16:9,deck,800x600") == [("16:9", "1280x720"), ("deck", "1280x800"), ("800x600", "800x600")]
    assert parse_screens(None)[-1] == ("phone", "720x1280")
    qualities = parse_qualities("project,high", ["rendering/x/y=2", "a/b=true", "c/d=Vector2i(1, 2)"])
    assert qualities[0] == ("project", {"rendering/x/y": 2, "a/b": True, "c/d": "Vector2i(1, 2)"})
    assert qualities[1][1]["rendering/anti_aliasing/quality/msaa_3d"] == QUALITY["high"]["rendering/anti_aliasing/quality/msaa_3d"]
    text = render({"c/d": Raw("Color(1, 0, 0, 1)"), "a/b": "text"})
    assert "d=Color(1, 0, 0, 1)" in text and 'b="text"' in text


# --- Review pages -----------------------------------------------------------------------------------------------------


def options(tmp_path, count=2):
    paths = []
    for i, color in enumerate([(200, 40, 40), (40, 40, 200), (40, 200, 40)][:count]):
        path = tmp_path / f"option-{i}.png"
        Image.new("RGB", (64, 36), color).save(path)
        paths.append(path)
    return paths


def test_review_writes_a_page_of_the_options(tmp_path):
    red, blue = options(tmp_path)
    proc = gdh("review", f"Warm={red}", f"Cool={blue}", "--settings", "Warm=tint 1.2", "--out", tmp_path / "page",
               "--title", "Tint")
    assert f"review: {tmp_path / 'page' / 'index.html'} (2 options)" in proc.stdout
    page = (tmp_path / "page" / "index.html").read_text()
    assert "<title>Tint</title>" in page and 'value="Warm"' in page and 'value="Cool"' in page
    assert "tint 1.2" in page and 'src="media/01.png"' in page
    assert (tmp_path / "page" / "media" / "02.png").read_bytes() == blue.read_bytes()
    proc = gdh("review", "--read", tmp_path / "page", check=False)
    assert proc.returncode == 1 and "No choice saved yet" in proc.stderr


def test_review_waits_for_the_choice_and_reads_it_back(tmp_path):
    red, blue = options(tmp_path)
    out = tmp_path / "page"
    waiting = subprocess.Popen([sys.executable, "-m", "gdh", "review", f"A={red}", f"B={blue}", "--out", out, "--wait",
                                "--timeout", "60", "--json"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    url = waiting.stdout.readline().split()[1]
    token = next(line for line in (out / "index.html").read_text().splitlines() if "const token" in line).split('"')[1]
    post = url.replace("index.html", "choice")

    def send(body):
        request = urllib.request.Request(post, json.dumps(body).encode(), {"Content-Type": "application/json"})
        return urllib.request.urlopen(request, timeout=10)

    with pytest.raises(urllib.error.HTTPError) as refused:
        send({"token": "wrong", "choice": "A"})
    assert refused.value.code == 403
    assert urllib.request.urlopen(url, timeout=10).status == 200  # the page itself is served
    send({"token": token, "choice": "B", "notes": "cooler reads better", "options": ["A", "B"]})
    out_text, _ = waiting.communicate(timeout=30)
    assert waiting.returncode == 0
    assert json.loads(out_text)["choice"] == "B"
    saved = json.loads((out / "choice.json").read_text())
    assert saved["choice"] == "B" and saved["notes"] == "cooler reads better" and "token" not in saved
    assert gdh("review", "--read", out).stdout == "chosen: B\nnotes: cooler reads better\n"


def test_review_takes_a_sweeps_or_a_matrixs_cells(game, tmp_path):
    reply = live("sweep", f"{BODY}=Color(1, 0, 0), Color(0, 0, 1)", "--node", "Ball", "--out", tmp_path / "s.png")
    assert json.loads((tmp_path / "s.json").read_text())["cells"] == reply["cells"]
    gdh("review", "--from", tmp_path / "s.json", "--out", tmp_path / "page")
    page = (tmp_path / "page" / "index.html").read_text()
    assert 'value="color=Color(1, 0, 0)"' in page and 'value="color=Color(0, 0, 1)"' in page
    for args, said in (([str(tmp_path / "s.png")], "A review needs 2 options or more"),
                       (["A=nope.png", "B=nope2.png"], "No such file: nope.png"),
                       ([f"A={tmp_path / 's.json'}", f"B={tmp_path / 's.png'}"], "isn't an image or a video"),
                       ([f"A={tmp_path / 's.png'}", f"B={tmp_path / 's.png'}", "--settings", "C=x"],
                        "--settings names 'C'")):
        proc = gdh("review", *args, "--out", tmp_path / "bad", check=False)
        assert proc.returncode == 1 and said in proc.stderr, (args, proc.stderr)
