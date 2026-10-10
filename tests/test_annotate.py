"""gdh live shot --annotate against testbed/annotate: annotate.tscn (2D) and annotate3d.tscn.

annotate.tscn: Player, a CharacterBody2D at 200,360 moving right at 120 px/s, drawn by its 32 px Body, with a box
collision shape; Coin, an Area2D at 400,300 in the group pickups, drawn by a Polygon2D diamond 24 px across, with a
circle of radius 12; Wall, a StaticBody2D whose capsule shape is disabled; Nav, a navigation region of one square
(700,450 to 1000,650, not baked); five 10 px Pebbles packed together from 300,520; a Backdrop over the whole screen;
and Score and Pause on a CanvasLayer. annotate3d.tscn: Crate (a StaticBody3D with a box mesh and box shape), Runner
(a CharacterBody3D moving at 2 m/s along x), and Floor, a navigation region of one quad.
"""
import os

import pytest
from PIL import Image

from conftest import TESTBED, gdh, gdh_json
from gdh.annotate import KIND_COLORS, overlaps

SESSION = f"test-annotate-{os.getpid()}"
SESSION_3D = f"{SESSION}-3d"


@pytest.fixture(scope="module")
def scene(tmp_path_factory, display):
    out = tmp_path_factory.mktemp("annotate")
    gdh("live", "start", "--project", TESTBED, "--scene", "res://annotate/annotate.tscn", "--session", SESSION,
        "--out", out)
    yield out
    gdh("live", "stop", "--session", SESSION)


@pytest.fixture(scope="module")
def scene3d(tmp_path_factory, display):
    out = tmp_path_factory.mktemp("annotate3d")
    gdh("live", "start", "--project", TESTBED, "--scene", "res://annotate/annotate3d.tscn", "--session", SESSION_3D,
        "--out", out)
    yield out
    gdh("live", "stop", "--session", SESSION_3D)


def shot(*args, session=SESSION):
    return gdh_json("live", "shot", *args, "--session", session)


def by_path(items):
    return {item["path"]: item for item in items}


def test_names_boxes_each_node_that_draws(scene, tmp_path):
    out = tmp_path / "shot.png"
    reply = shot("--annotate", "--out", out)
    nodes = by_path(reply["annotations"]["nodes"])
    # What draws, not what only holds or lays out others (Player, Coin, Pebbles, UI/Root); the backdrop is listed.
    assert set(nodes) == {"Player/Body", "Coin/Look", "Pebbles/P1", "Pebbles/P2", "Pebbles/P3", "Pebbles/P4",
                          "Pebbles/P5", "UI/Root/Score", "UI/Root/Pause"}
    assert [n["n"] for n in reply["annotations"]["nodes"]] == list(range(1, 10))
    assert [b["path"] for b in reply["annotations"]["backdrops"]] == ["Backdrop"]
    assert nodes["Player/Body"]["box"] == [184, 344, 32, 32] and nodes["Player/Body"]["kind"] == "2d"
    assert nodes["Coin/Look"]["box"] == [388, 288, 24, 24]  # a Polygon2D's own bounds
    assert nodes["UI/Root/Score"]["kind"] == "ui"
    annotated = reply["annotated"]["normal"]
    assert annotated == str(tmp_path / "shot-annotated.png")
    with Image.open(annotated) as img, Image.open(out) as plain:
        assert img.size == plain.size
        assert img.getpixel((184, 360)) == KIND_COLORS["2d"]  # Body's box, on its left edge
        assert img.getpixel((20, 33)) == KIND_COLORS["ui"]
    # No label covers another, nor another node's box.
    labels = reply["labels"]
    assert len(labels) == 9 and reply["labeled"] + reply["numbered"] == 9
    boxes = {n["n"]: n["box"] for n in reply["annotations"]["nodes"]}
    for a in labels:
        assert not any(overlaps(a["rect"], b["rect"]) for b in labels if b is not a)
        for n, (x, y, w, h) in boxes.items():
            if n != a["n"]:
                assert not overlaps(a["rect"], (x, y, x + w, y + h)), (a, n)


def test_layers_draw_shapes_navigation_and_velocity(scene, tmp_path):
    reply = shot("--annotate", "all", "--out", tmp_path / "all.png")
    found = reply["annotations"]
    assert found["layers"] == ["names", "collisions", "nav", "velocity"]
    shapes = by_path(found["shapes"])
    assert {p: s["kind"] for p, s in shapes.items()} == {"Player/Shape": "body", "Coin/Shape": "area",
                                                          "Wall/Shape": "disabled"}
    assert shapes["Player/Shape"]["lines"] == [{"points": [[184, 344], [216, 344], [216, 376], [184, 376]],
                                                "closed": True}]
    circle = shapes["Coin/Shape"]["lines"][0]["points"]
    assert all(abs(((x - 400) ** 2 + (y - 300) ** 2) ** 0.5 - 12) < 0.2 for x, y in circle)
    assert found["nav"] == [{"path": "Nav", "polygons": [[[700, 450], [1000, 450], [1000, 650], [700, 650]]]}]
    assert found["velocity"] == [{"path": "Player", "from": [200, 360], "to": [260, 360], "speed": 120,
                                  "unit": "px/s"}]
    with Image.open(reply["annotated"]["normal"]) as img:
        assert img.getpixel((240, 360))[0] > 200 and img.getpixel((240, 360))[1] < 120  # the magenta arrow


def test_filters_narrow_what_is_labeled(scene, tmp_path):
    reply = shot("--annotate", "--filter", "Pebbles", "--out", tmp_path / "pebbles.png")
    assert [n["path"] for n in reply["annotations"]["nodes"]] == [f"Pebbles/P{i}" for i in range(1, 6)]
    # A class or group labels those nodes, drawn or not, boxed round what they draw.
    reply = shot("--annotate", "--filter", "class:CharacterBody2D", "--out", tmp_path / "bodies.png")
    assert [(n["path"], n["box"]) for n in reply["annotations"]["nodes"]] == [("Player", [184, 344, 32, 32])]
    reply = shot("--annotate", "names,collisions", "--filter", "group:pickups", "--out", tmp_path / "pickups.png")
    assert [(n["path"], n["box"]) for n in reply["annotations"]["nodes"]] == [("Coin", [388, 288, 24, 24])]
    assert [s["path"] for s in reply["annotations"]["shapes"]] == ["Coin/Shape"]
    reply = shot("--annotate", "--no-ui", "--out", tmp_path / "no-ui.png")
    assert not [n for n in reply["annotations"]["nodes"] if n["kind"] == "ui"]


def test_annotations_follow_a_framed_shot(scene, tmp_path):
    reply = shot("--node", "Player", "--margin", "40", "--zoom", "2", "--annotate", "--out", tmp_path / "framed.png")
    # Player itself is a point (its origin), so the frame is 40 px round it.
    assert reply["crop"] == [160, 320, 80, 80] and reply["size"] == [160, 160]
    # Only what's in the frame, drawn where the zoomed shot shows it: Body's left edge at (184 - 160) * 2.
    assert [n["path"] for n in reply["annotations"]["nodes"]] == ["Player/Body"]
    with Image.open(reply["annotated"]["normal"]) as img:
        assert img.size == (160, 160)
        assert img.getpixel((48, 80)) == KIND_COLORS["2d"]


def test_annotate_refuses_what_it_cant_do(scene, tmp_path):
    proc = gdh("live", "shot", "--annotate", "bogus", "--session", SESSION, check=False)
    assert proc.returncode == 1 and "Unknown layer 'bogus' for --annotate" in proc.stderr
    proc = gdh("live", "shot", "--filter", "class:Area2D", "--session", SESSION, check=False)
    assert proc.returncode == 1 and "--filter picks what --annotate labels" in proc.stderr
    proc = gdh("live", "shot", "--annotate", "--filter", "Pebbles", "--filter", "UI", "--session", SESSION, check=False)
    assert proc.returncode == 1 and "--filter takes one path" in proc.stderr
    proc = gdh("live", "shot", "--annotate", "--filter", "Nope", "--session", SESSION, check=False)
    assert proc.returncode == 1 and "No node at Nope." in proc.stderr


def test_annotate_prints_the_numbers_and_their_nodes(scene):
    proc = gdh("live", "shot", "--annotate", "all", "--session", SESSION)
    assert "normal annotated: " in proc.stdout
    assert "  1: Player/Body (ColorRect) box=[184.0, 344.0, 32.0, 32.0]" in proc.stdout
    assert "  backdrop, not boxed: Backdrop (ColorRect)" in proc.stdout
    assert "  collision disabled: Wall/Shape" in proc.stdout and "  velocity: Player 120 px/s" in proc.stdout


def test_find_boxes_a_polygon(scene):
    reply = gdh_json("live", "find", "--name", "Look", "--session", SESSION)
    assert reply["result"]["matches"][0]["screen"] == [388, 288, 24, 24]


def test_annotate_3d(scene3d, tmp_path):
    reply = shot("--annotate", "all", "--out", tmp_path / "3d.png", session=SESSION_3D)
    found = reply["annotations"]
    assert [(n["path"], n["kind"]) for n in found["nodes"]] == [("Crate/Mesh", "3d"), ("Runner/Mesh", "3d")]
    crate = by_path(found["nodes"])["Crate/Mesh"]["box"]
    # The box shape's 12 edges, inside the mesh's box on screen (the shape and the mesh are the same unit cube).
    edges = by_path(found["shapes"])["Crate/Shape"]["lines"]
    assert len(edges) == 12
    for line in edges:
        for x, y in line["points"]:
            assert crate[0] - 0.5 <= x <= crate[0] + crate[2] + 0.5 and crate[1] - 0.5 <= y <= crate[1] + crate[3] + 0.5
    assert len(found["nav"]) == 1 and len(found["nav"][0]["polygons"][0]) == 4
    velocity = found["velocity"][0]
    assert velocity["path"] == "Runner" and velocity["speed"] == 2 and velocity["unit"] == "m/s"
    assert velocity["to"][0] > velocity["from"][0]
    reply = shot("--annotate", "--filter", "class:CharacterBody3D", "--out", tmp_path / "runner.png",
                 session=SESSION_3D)
    assert [n["path"] for n in reply["annotations"]["nodes"]] == ["Runner"]
