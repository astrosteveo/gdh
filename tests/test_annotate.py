"""gdh live shot --annotate against testbed/annotate: annotate.tscn (2D) and annotate3d.tscn.

annotate.tscn: Player, a CharacterBody2D at 200,360 moving right at 120 px/s, drawn by its 32 px Body, with a box
collision shape; Coin, an Area2D at 400,300 in the group pickups, drawn by a Polygon2D diamond 24 px across, with a
circle of radius 12; Wall, a StaticBody2D whose capsule shape is disabled; Nav, a navigation region of one square
(700,450 to 1000,650, not baked); five 10 px Pebbles packed together from 300,520; a Backdrop over the whole screen;
Marker, a Sprite2D at 900,200 scaled 4x whose 16 px texture is opaque on its left half only; and Score and Pause on a
CanvasLayer. annotate3d.tscn: Crate (a StaticBody3D with a box mesh and box shape), Runner (a CharacterBody3D moving
at 2 m/s along x), Floor (a navigation region of one quad), Sign (a mesh of two quads, surface 0 on the left with the
material Left, 1 on the right with Right) and Back, a wall behind them all.
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
                          "Pebbles/P5", "Marker", "UI/Root/Score", "UI/Root/Pause"}
    assert [n["n"] for n in reply["annotations"]["nodes"]] == list(range(1, 11))
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
    assert len(labels) == 10 and reply["labeled"] + reply["numbered"] == 10
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
    assert [(n["path"], n["kind"]) for n in found["nodes"]] == [("Crate/Mesh", "3d"), ("Runner/Mesh", "3d"),
                                                                ("Sign", "3d"), ("Back", "3d")]
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


# --- Pick ------------------------------------------------------------------------------------------------------------


def pick(*points, session=SESSION):
    return gdh_json("live", "pick", *points, "--session", session)["result"]["points"]


def test_pick_lists_what_is_drawn_at_each_point_top_first(scene):
    body, coin, pause, marker, clear, nothing = pick("200,360", "400,300", "1200,40", "880,200", "920,200", "1300,10")
    assert [(h["path"], h["kind"]) for h in body["hits"]] == [("Player/Body", "2d"), ("Backdrop", "2d")]
    assert body["hits"][0]["local"] == [16, 16]
    assert [h["path"] for h in coin["hits"]] == ["Coin/Look", "Backdrop"]  # inside the polygon
    # The UI is above the world, with its text; and the click there goes to the button.
    assert pause["hits"][0]["path"] == "UI/Root/Pause" and pause["hits"][0]["text"] == "Pause"
    assert pause["takes_click"]["path"] == "UI/Root/Pause"
    # A sprite counts where its texture is opaque, with the texture and the texel.
    assert marker["hits"][0]["path"] == "Marker"
    assert marker["hits"][0]["texture"] == "res://annotate/marker.png" and marker["hits"][0]["texel"] == [3, 8]
    assert [h["path"] for h in clear["hits"]] == ["Backdrop"]
    assert nothing["hits"] == []


def test_pick_follows_z_index(scene):
    gdh_json("live", "eval", "get_node('Pebbles/P1').set_z_index(-1)", "--session", SESSION)
    try:
        assert [h["path"] for h in pick("305,525")[0]["hits"]] == ["Backdrop", "Pebbles/P1"]
    finally:
        gdh_json("live", "eval", "get_node('Pebbles/P1').set_z_index(0)", "--session", SESSION)


def test_pick_prints_each_point(scene):
    proc = gdh("live", "pick", "880,200", "1300,10", "--session", SESSION)
    assert "at 880,200:\n  1. Marker (Sprite2D, 2d): texture res://annotate/marker.png at texel 3,8" in proc.stdout
    assert "at 1300,10:\n  nothing drawn there" in proc.stdout
    proc = gdh("live", "pick", "12", "--session", SESSION, check=False)
    assert proc.returncode == 1 and "pick takes X,Y" in proc.stderr


def test_pick_in_3d_by_triangles_nearest_first(scene3d):
    left, right = (gdh_json("live", "eval", f"get_viewport().get_camera_3d().unproject_position(Vector3({x}, 2.3, 0))",
                            "--session", SESSION_3D)["result"]["value"] for x in (-0.5, 0.5))
    crate = gdh_json("live", "eval", "get_viewport().get_camera_3d().unproject_position(Vector3(-1.5, 0.5, 0.5))",
                     "--session", SESSION_3D)["result"]["value"]
    hits = pick(*(f"{x},{y}" for x, y in (left, right, crate)), session=SESSION_3D)
    # The sign's two surfaces, each with its own material, and the wall behind.
    a, b = hits[0]["hits"][0], hits[1]["hits"][0]
    assert (a["path"], a["surface"], a["material"]) == ("Sign", 0, "StandardMaterial3D Left")
    assert (b["path"], b["surface"], b["material"]) == ("Sign", 1, "StandardMaterial3D Right")
    assert a["world"] == pytest.approx([-0.5, 2.3, 0], abs=0.01) and a["normal"] == pytest.approx([0, 0, 1], abs=0.01)
    assert [h["path"] for h in hits[0]["hits"]] == ["Sign", "Back"]
    # Through the crate's front face: the crate, then the wall behind, by distance.
    front = hits[2]["hits"]
    assert [h["path"] for h in front] == ["Crate/Mesh", "Back"] and front[0]["distance"] < front[1]["distance"]
    assert front[0]["world"] == pytest.approx([-1.5, 0.5, 0.5], abs=0.01) and front[0]["by"] == "triangles"
