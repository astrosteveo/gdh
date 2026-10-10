"""gdh live contrast against testbed/anim/readability.tscn (on a mid-grey backdrop: Bright, a white square; Murky, a
brownish grey square of about the backdrop's brightness; Ghost, a Node2D drawn by a grey square a shade lighter than
the backdrop), and gdh measure palette on made-up images."""
import json
import os

import numpy as np
import pytest
from PIL import Image

from conftest import TESTBED, gdh, gdh_json
from gdh.readability import compare_palettes, contrast_ratio, lab, palette_of, relative_luminance

SESSION = f"test-readability-{os.getpid()}"


@pytest.fixture(scope="module")
def game(tmp_path_factory, display):
    out = tmp_path_factory.mktemp("readability")
    gdh("live", "start", "--project", TESTBED, "--scene", "res://anim/readability.tscn", "--session", SESSION,
        "--out", out)
    yield out
    gdh("live", "stop", "--session", SESSION)


def test_contrast_against_what_is_round_the_node(game, tmp_path):
    reply = gdh_json("live", "contrast", "--node", "Bright", "--node", "Murky", "--node", "Ghost", "--out", tmp_path,
                     "--session", SESSION)
    nodes = {r["node_path"]: r for r in reply["nodes"]}
    # White against mid grey (sRGB 127): (1.05) / (0.212 + 0.05), about 4:1.
    assert nodes["Bright"]["ratio"] == pytest.approx(4.0, abs=0.05) and not nodes["Bright"]["low"]
    assert nodes["Bright"]["node"]["pixels"] == 80 * 80
    # Murky's brightness is the backdrop's, though its color isn't; Ghost (its child draws it) is a shade off.
    assert nodes["Murky"]["ratio"] < 1.1 and nodes["Murky"]["low"] and nodes["Murky"]["delta_e"] > 5
    assert nodes["Ghost"]["ratio"] < 1.2 and nodes["Ghost"]["low"] and nodes["Ghost"]["delta_e"] < 5
    assert all((tmp_path / f"{n}-contrast.png").exists() for n in nodes)
    # Every node is shown again after.
    assert gdh_json("live", "eval", "[get_node('Bright').visible, get_node('Ghost').visible]",
                    "--session", SESSION)["result"]["value"] == [True, True]


def test_contrast_fails_on_request_and_says_what_it_cant_measure(game):
    proc = gdh("live", "contrast", "--node", "Murky", "--fail", "--session", SESSION, check=False)
    assert proc.returncode == 1 and "Murky: contrast 1.04:1" in proc.stdout and "hard to pick out" in proc.stdout
    gdh_json("live", "eval", "get_node('Bright').hide()", "--session", SESSION)
    try:
        proc = gdh("live", "contrast", "--node", "Bright", "--session", SESSION, check=False)
    finally:
        gdh_json("live", "eval", "get_node('Bright').show()", "--session", SESSION)
    assert proc.returncode == 1 and "Bright: The node drew no pixels" in proc.stderr
    proc = gdh("live", "contrast", "--node", "Nope", "--session", SESSION, check=False)
    assert proc.returncode == 1


def test_palette_says_how_a_shot_differs_from_the_reference(tmp_path):
    rng = np.random.default_rng(5)
    base = rng.integers(60, 200, (120, 160, 3)).astype(np.uint8)
    Image.fromarray(base).save(tmp_path / "shot.png")
    Image.fromarray((base * 0.6).astype(np.uint8)).save(tmp_path / "dark.png")
    grey = np.repeat(base.mean(axis=2, keepdims=True), 3, axis=2).astype(np.uint8)
    Image.fromarray(grey).save(tmp_path / "grey.png")
    reply = gdh_json("measure", "palette", tmp_path / "shot.png", "--reference", tmp_path / "dark.png",
                     "--out", tmp_path / "side.png")
    assert reply["differences"][0].startswith("lightness (mean L*)") and "brighter by" in reply["differences"][0]
    assert reply["histogram_distance"] > 10 and (tmp_path / "side.png").exists()
    reply = gdh_json("measure", "palette", tmp_path / "shot.png", "--reference", tmp_path / "grey.png")
    assert "more saturated by" in reply["differences"][4] and reply["reference"]["saturation"] < 0.01
    same = gdh_json("measure", "palette", tmp_path / "shot.png", "--reference", tmp_path / "shot.png")
    assert same["histogram_distance"] == 0 and all("about the same" in line for line in same["differences"])
    # A mood board: the references are taken together.
    board = gdh_json("measure", "palette", tmp_path / "shot.png", "--reference", tmp_path / "dark.png",
                     "--reference", tmp_path / "shot.png")
    assert reply["shot"]["lightness"] > board["reference"]["lightness"] > json.loads(json.dumps(
        palette_of(np.asarray(Image.open(tmp_path / "dark.png")).reshape(-1, 3))))["lightness"]


def test_color_math():
    assert contrast_ratio(1.0, 0.0) == pytest.approx(21)
    assert float(relative_luminance(np.array([255, 255, 255]))) == pytest.approx(1.0)
    white = lab(np.array([[255, 255, 255]]))[0]
    assert white[0] == pytest.approx(100, abs=0.1) and abs(white[1]) < 0.5 and abs(white[2]) < 0.5
    shot = {"lightness": 50, "contrast": 10, "black_point": 5, "white_point": 95, "saturation": 0.3,
            "histogram": [1.0] + [0.0] * 31}
    lines, emd = compare_palettes(shot, {**shot, "lightness": 40, "histogram": [0.0, 1.0] + [0.0] * 30})
    assert lines[0] == "lightness (mean L*): 50 against 40, brighter by 10" and emd == pytest.approx(100 / 32, abs=0.01)
