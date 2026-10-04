"""gdh api: the installed Godot's class reference, built once from the editor's help cache and read after."""
import os

import pytest

from conftest import gdh


@pytest.fixture(scope="module")
def reference(tmp_path_factory, display):
    """A fresh cache and editor home, so the reference is built from scratch. It needs no display: built once."""
    if display != "gpu" and os.environ.get("GDH_API_TESTS_RAN"):
        pytest.skip("gdh api runs headless: once is enough")
    os.environ["GDH_API_TESTS_RAN"] = "1"
    saved = {k: os.environ.get(k) for k in ("XDG_CACHE_HOME", "GDH_EDITOR_HOME")}
    os.environ["XDG_CACHE_HOME"] = str(tmp_path_factory.mktemp("api-cache"))
    os.environ["GDH_EDITOR_HOME"] = str(tmp_path_factory.mktemp("api-editor-home"))
    proc = gdh("api", "CharacterBody2D")
    yield proc
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_a_class_lists_its_chain_and_members(reference):
    out = reference.stdout
    assert out.startswith("CharacterBody2D < PhysicsBody2D < CollisionObject2D < Node2D")
    assert "move_and_slide() -> bool" in out
    assert "motion_mode: CharacterBody2D.MotionMode = 0" in out
    assert "MOTION_MODE_GROUNDED = 0" in out
    assert "building the class reference" in reference.stderr


def test_a_member_is_found_up_the_chain_with_its_description(reference):
    out = gdh("api", "CharacterBody2D.position").stdout
    assert out.startswith("Node2D.position: Vector2 = Vector2(0, 0)   [property]   (from Node2D)")
    assert "[member" not in out and "relative to the node's parent" in out


def test_a_wrong_name_suggests_close_ones(reference):
    proc = gdh("api", "Node.get_nodee", check=False)
    assert proc.returncode == 1 and "get_node" in proc.stderr
    proc = gdh("api", "KinematicBody2D", check=False)
    assert proc.returncode == 1 and "no class KinematicBody2D" in proc.stderr


def test_search_and_gdscript_builtins(reference):
    out = gdh("api", "--search", "is_on_floor").stdout
    assert "CharacterBody2D.is_on_floor() -> bool const   [method]" in out
    assert "preload(path: String) -> Resource" in gdh("api", "@GDScript").stdout
    assert "building" not in gdh("api", "Node").stderr
