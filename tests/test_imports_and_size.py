"""gdh imports a project whose import cache is missing or stale before running it, reports resources that failed to
load as a defect, and checks that the game's window is the size asked for."""
import json
import os
import re
import shutil
import time

from conftest import TESTBED, gdh
from gdh.imports import missing_resources, stale_reason

SESSION = f"test-size-{os.getpid()}"
SCENE = "res://blind/scene_01.tscn"


def fresh_copy(directory):
    """The testbed as a fresh checkout has it: no .godot."""
    shutil.copytree(TESTBED, directory, ignore=shutil.ignore_patterns(".godot"))
    return directory


def test_staleness_is_read_from_file_times_and_hashes(tmp_path):
    project = fresh_copy(tmp_path / "project")
    # A VRAM-compressed texture: its copy is name.png-<hash>.s3tc.ctex (or .bptc, .etc2, .astc), its record
    # name.png-<hash>.md5.
    vram = project / "blind/assets/textures/vram.png"
    vram.write_bytes((project / "blind/assets/textures/bark.png").read_bytes())
    vram.with_name("vram.png.import").write_text('[remap]\n\nimporter="texture"\ntype="CompressedTexture2D"\n\n'
                                                 '[params]\n\ncompress/mode=2\n')
    assert "no .godot" in stale_reason(project)
    gdh("import", "--project", project)
    copies = [p.name for p in project.glob(".godot/imported/vram.png-*.ctex")]
    assert copies and all(re.fullmatch(r"vram\.png-[0-9a-f]{32}\.(s3tc|bptc|etc2|astc)\.ctex", c) for c in copies)
    assert stale_reason(project) is None
    texture = project / "blind/assets/textures/asphalt.png"
    # Touched, as a checkout does, but the same bytes: no import.
    later = time.time() + 5
    for touched in (texture, vram):
        os.utime(touched, (later, later))
    assert stale_reason(project) is None
    vram.write_bytes(vram.read_bytes() + b"\0")
    assert "vram.png changed" in stale_reason(project)
    texture.write_bytes(texture.read_bytes() + b"\0")
    assert "asphalt.png changed" in stale_reason(project)
    (project / "blind/assets/textures/new.png").write_bytes((project / "blind/assets/textures/bark.png").read_bytes())
    gdh("import", "--project", project)
    assert stale_reason(project) is None
    next(project.glob(".godot/imported/bark.png-*.ctex")).unlink()
    assert "bark.png's imported copy is missing" in stale_reason(project)


def test_an_asset_remembered_as_unfixable_hides_no_other(tmp_path, monkeypatch):
    from gdh import imports
    project = fresh_copy(tmp_path / "project")
    gdh("import", "--project", project)
    textures = project / "blind/assets/textures"
    for name in ("asphalt.png", "bark.png"):
        (textures / name).write_bytes((textures / name).read_bytes() + b"\0")
    reasons = list(imports.stale_reasons(project))
    assert len(reasons) == 2 and "asphalt.png changed" in reasons[0] and "bark.png changed" in reasons[1]
    imported = []
    monkeypatch.setattr(imports, "run_import", lambda project, quiet=False: imported.append(project))
    (project / imports.UNFIXABLE).write_text(reasons[0] + "\n")
    imports.ensure_imported(project)
    assert imported == [project]  # bark.png's change isn't hidden behind asphalt.png's
    assert (project / imports.UNFIXABLE).read_text().splitlines() == reasons  # (the stand-in imported nothing)
    imported.clear()
    imports.ensure_imported(project)
    assert imported == []  # both remembered now


def test_the_class_cache_is_stale_when_a_script_declares_a_class_it_lacks(tmp_path):
    project = fresh_copy(tmp_path / "project")
    gdh("import", "--project", project)
    assert stale_reason(project) is None
    (project / "enemy.gd").write_text("@tool\nclass_name Enemy extends Node2D\n")
    assert stale_reason(project) == "enemy.gd declares class Enemy, which the class cache lacks"
    gdh("import", "--project", project)
    assert stale_reason(project) is None
    (project / "enemy.gd").write_text("extends Node2D\n")
    assert stale_reason(project) == "the class cache has class Enemy, which enemy.gd no longer declares"


def test_each_way_godot_says_a_resource_failed_to_load_is_read():
    errors = [{"message": "Error loading resource: 'res://fonts/Oxanium.ttf'."},
              {"message": "Failed loading resource: res://blind/assets/textures/bark.png."},
              {"message": "Unable to open file: res://.godot/imported/bark.png-0123.ctex."},
              {"message": "No loader found for resource: res://fonts/Other.ttf (expected type: FontFile)"},
              {"message": "res://main.tscn:4 - Parse Error: [ext_resource] referenced non-existent resource at: "
                          "res://fonts/Third.otf."}]
    assert missing_resources(errors) == ["res://fonts/Oxanium.ttf", "res://blind/assets/textures/bark.png",
                                         "res://fonts/Other.ttf", "res://fonts/Third.otf"]


def test_a_fresh_checkout_is_imported_before_capture_and_only_once(tmp_path):
    project = fresh_copy(tmp_path / "project")
    first = gdh("capture", "--project", project, "--scene", SCENE, "--modes", "normal", "--out", tmp_path / "a")
    assert "importing the project first" in first.stderr
    report = json.loads((tmp_path / "a/report.json").read_text())
    assert not [e for e in report["errors"] if "Failed loading" in e["message"]]
    assert "missing_resources" not in report
    second = gdh("capture", "--project", project, "--scene", SCENE, "--modes", "normal", "--out", tmp_path / "b")
    assert "importing" not in second.stderr


def test_resources_that_fail_to_load_are_a_defect(tmp_path):
    project = fresh_copy(tmp_path / "project")
    gdh("import", "--project", project)
    for ctex in project.glob(".godot/imported/*.ctex"):
        ctex.unlink()
    proc = gdh("capture", "--project", project, "--scene", SCENE, "--modes", "normal", "--out", tmp_path / "out",
               "--no-import", check=False)
    assert "DEFECT:" in proc.stdout and "failed to load" in proc.stdout
    missing = json.loads((tmp_path / "out/report.json").read_text())["missing_resources"]
    assert "res://blind/assets/textures/wood_planks.png" in missing
    assert not [m for m in missing if m.startswith("res://.godot/")]


def test_capture_reports_the_window_size_and_fails_when_the_game_changes_it(tmp_path):
    gdh("capture", "--project", TESTBED, "--scene", "res://smoke/smoke.tscn", "--modes", "normal",
        "--resolution", "1600x900", "--out", tmp_path / "ok")
    assert json.loads((tmp_path / "ok/report.json").read_text())["window_size"] == [1600, 900]
    proc = gdh("capture", "--project", TESTBED, "--scene", "res://size/resizes_window.tscn", "--modes", "normal",
               "--resolution", "1280x720", "--out", tmp_path / "resized", check=False)
    assert proc.returncode == 1
    assert "640x360, not the 1280x720 asked for" in proc.stderr
    assert "640x360" in json.loads((tmp_path / "resized/report.json").read_text())["size_mismatch"]


def test_live_start_refuses_a_window_the_game_resized(tmp_path):
    from gdh.live import session_path
    proc = gdh("live", "start", "--project", TESTBED, "--scene", "res://size/resizes_window.tscn",
               "--session", SESSION, "--out", tmp_path, check=False)
    assert proc.returncode == 1
    assert "640x360, not the 1280x720 asked for" in proc.stderr
    assert not session_path(SESSION).exists()
