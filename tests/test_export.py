"""gdh export: lists a project's presets, says what's missing before exporting, and exports a pack headless."""
import os

import pytest

from conftest import gdh

PRESETS = ('[preset.0]\n\nname="Linux"\nplatform="Linux"\nrunnable=true\nexport_filter="all_resources"\n'
           'export_path="build/game.x86_64"\n\n[preset.0.options]\n\nbinary_format/architecture="x86_64"\n')


@pytest.fixture
def project(tmp_path, display):
    if display != "gpu" and os.environ.get("GDH_EXPORT_TESTS_RAN") == "done":
        pytest.skip("gdh export runs headless: once is enough")
    directory = tmp_path / "game"
    directory.mkdir()
    (directory / "project.godot").write_text(
        'config_version=5\n\n[application]\n\nconfig/name="game"\nrun/main_scene="res://main.tscn"\n')
    (directory / "main.tscn").write_text('[gd_scene format=3]\n\n[node name="Main" type="Node2D"]\n')
    (directory / "export_presets.cfg").write_text(PRESETS)
    return directory


def test_lists_presets_and_refuses_an_unknown_one(project):
    assert "Linux   [Linux]   build/game.x86_64" in gdh("export", "--project", project).stdout
    proc = gdh("export", "--project", project, "--preset", "Windows", check=False)
    assert proc.returncode == 1 and "presets: Linux" in proc.stderr


def test_says_when_the_export_templates_are_missing(project, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "no-templates"))
    proc = gdh("export", "--project", project, "--preset", "Linux", check=False)
    assert proc.returncode == 1 and "export templates" in proc.stderr and "--pack" in proc.stderr


def test_exports_a_pack_without_templates(project):
    proc = gdh("export", "--project", project, "--preset", "Linux", "--pack")
    assert (project / "build" / "game.pck").stat().st_size > 0
    assert "game.pck" in proc.stdout
    os.environ["GDH_EXPORT_TESTS_RAN"] = "done"


def test_a_project_without_presets_is_told_how_to_add_one(project):
    (project / "export_presets.cfg").unlink()
    proc = gdh("export", "--project", project, check=False)
    assert proc.returncode == 1 and "Project > Export" in proc.stderr
