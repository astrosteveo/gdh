"""gdh editor: the Godot editor opens a scene on gdh's display, and gdh saves its 3D viewport, its whole window and a
report, leaving the user's editor settings and the project's editor state as they were."""
import json
import shutil

import pytest
from PIL import Image, ImageStat

from conftest import TESTBED, gdh


@pytest.fixture(scope="module")
def project(tmp_path_factory, display):
    """A copy of the testbed, imported: the editor writes to a project it opens (project.godot, .godot)."""
    directory = tmp_path_factory.mktemp("editor") / "testbed"
    shutil.copytree(TESTBED, directory, ignore=shutil.ignore_patterns(".godot"))
    gdh("import", "--project", directory)
    return directory


@pytest.fixture(autouse=True)
def editor_home(tmp_path, monkeypatch):
    """The editor's settings, data and caches in a home of the test's own."""
    monkeypatch.setenv("GDH_EDITOR_HOME", str(tmp_path / "editor-home"))


def test_editor_saves_the_viewport_the_window_and_a_report(project, tmp_path):
    out = tmp_path / "out"
    proc = gdh("editor", "--project", project, "--scene", "res://smoke/smoke.tscn", "--out", out)
    assert "res://smoke/smoke.tscn: opened in" in proc.stdout
    window = Image.open(out / "editor.png")
    viewport = Image.open(out / "viewport.png")
    assert window.size == (1600, 900)
    report = json.loads((out / "report.json").read_text())
    assert list(viewport.size) == report["viewport_size"]
    assert viewport.size[0] < window.size[0] and viewport.size[1] < window.size[1]
    # The scene is drawn: the floor, the box and the sphere, not one flat colour.
    assert max(ImageStat.Stat(viewport.convert("L")).stddev) > 5
    assert report["tree"]["name"] == "Smoke"
    assert {c["name"] for c in report["tree"]["children"]} >= {"Camera3D", "Sun", "Floor", "Box", "Sphere"}
    assert report["open_ms"] >= 0 and report["idle"]["seconds"] > 0 and report["redraw"]["frames"] == 30
    assert not [e for e in report["open_errors"] + report["errors"] if e["type"] != "warning"]
    startup = json.loads((out / "editor.json").read_text())
    assert startup["ready"] and set(startup["project_changes"]) == {"added", "removed", "changed"}


def test_editor_leaves_its_state_and_the_users_settings_alone(project, tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("GDH_EDITOR_HOME", str(home))
    state = project / ".godot" / "editor"
    state.mkdir(parents=True, exist_ok=True)
    (state / "kept.cfg").write_text("[kept]\nby = \"the test\"\n")
    before = sorted(p.relative_to(state) for p in state.rglob("*"))
    gdh("editor", "--project", project, "--scene", "res://smoke/smoke.tscn", "--out", tmp_path / "out")
    assert sorted(p.relative_to(state) for p in state.rglob("*")) == before
    assert (state / "kept.cfg").read_text() == "[kept]\nby = \"the test\"\n"
    # Its settings went to the editor home it was given.
    assert list((home / "config").rglob("editor_settings-*.tres"))


def test_editor_sets_selects_and_views(project, tmp_path):
    out = tmp_path / "out"
    proc = gdh("editor", "--project", project, "--scene", "res://smoke/smoke.tscn", "--out", out,
               "--set", "Box:visible=false", "--select", "Sphere", "--view", "0,6,8:0,0,0", "--far", "100")
    report = json.loads((out / "report.json").read_text())
    assert report["notes"] == [], proc.stdout
    camera = report["camera"]
    assert camera["previewing"] and camera["far"] == pytest.approx(100)
    assert camera["position"] == pytest.approx([0, 6, 8], abs=0.01)
    assert camera["forward"] == pytest.approx([0, -0.6, -0.8], abs=0.01)


def test_editor_turns_and_zooms_its_own_camera(project, tmp_path):
    out = tmp_path / "out"
    gdh("editor", "--project", project, "--scene", "res://smoke/smoke.tscn", "--out", out, "--orbit=200,0", "--zoom", "10")
    camera = json.loads((out / "report.json").read_text())["camera"]
    assert not camera["previewing"]
    # The editor camera starts about 4 m from the origin; ten steps of the wheel take it farther, and the drag turns it.
    distance = sum(v * v for v in camera["position"]) ** 0.5
    assert distance > 6


def test_editor_reports_a_missing_scene(project, tmp_path):
    proc = gdh("editor", "--project", project, "--scene", "res://smoke/nope.tscn", "--out", tmp_path, check=False)
    assert proc.returncode == 1
    assert "No scene at res://smoke/nope.tscn" in proc.stdout
