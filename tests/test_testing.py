"""gdh test: runs a project's tests headless with GUT, gdUnit4 or gdh's own runner, and reports each failure."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import gdh

# The frameworks are fetched once, at these versions, into gdh's cache; their tests skip without a network.
FRAMEWORKS = {
    "gut": ("https://github.com/bitwes/Gut.git", "v9.7.1", "addons/gut"),
    "gdunit4": ("https://github.com/MikeSchulze/gdUnit4.git", "v6.2.1", "addons/gdUnit4"),
}
TESTS = {
    "gut": 'extends GutTest\n\nfunc test_adds():\n\tassert_eq(1 + 1, 2)\n\n'
           'func test_wrong():\n\tassert_eq(1, 2, "one is two")\n',
    "gdunit4": 'extends GdUnitTestSuite\n\nfunc test_adds() -> void:\n\tassert_int(1 + 1).is_equal(2)\n\n'
               'func test_wrong() -> void:\n\tassert_int(1).is_equal(2)\n',
    "gdh": 'extends Node\n\nfunc test_adds():\n\tassert(1 + 1 == 2)\n\nfunc test_wrong():\n\tassert(1 == 2, "one is two")\n\n'
           'func test_waits_a_frame():\n\tawait get_tree().process_frame\n\tassert(is_inside_tree())\n\n'
           'func test_crashes():\n\tvar nothing = null\n\tnothing.foo()\n',
}


@pytest.fixture(scope="module", autouse=True)
def once(display):
    if display != "gpu" and os.environ.get("GDH_TESTING_TESTS_RAN"):
        pytest.skip("gdh test runs headless by default: once is enough")
    os.environ["GDH_TESTING_TESTS_RAN"] = "1"


def framework_copy(name):
    url, tag, addon = FRAMEWORKS[name]
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "gdh" / "test-frameworks" / f"{name}-{tag}"
    if not (base / addon).is_dir():
        shutil.rmtree(base, ignore_errors=True)
        proc = subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", tag, url, str(base)],
                              capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            pytest.skip(f"couldn't fetch {name} {tag}: {proc.stderr.strip()[:200]}")
    return base / addon


def make_project(directory, framework):
    (directory / "test").mkdir(parents=True)
    (directory / "project.godot").write_text(f'config_version=5\n\n[application]\n\nconfig/name="{framework}"\n')
    (directory / "test" / "test_math.gd").write_text(TESTS[framework])
    if framework in FRAMEWORKS:
        addon = framework_copy(framework)
        shutil.copytree(addon, directory / "addons" / addon.name)
    return directory


@pytest.mark.parametrize("framework", ["gut", "gdunit4"])
def test_a_framework_reports_its_failure(tmp_path, framework):
    project = make_project(tmp_path / "game", framework)
    proc = gdh("test", "--project", project, "--out", tmp_path / "out", check=False)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert f"gdh test ({framework}): 2 tests, 1 passed, 1 failed" in proc.stdout
    assert "test_wrong" in proc.stdout
    report = json.loads((tmp_path / "out" / "report.json").read_text())
    assert report["failures"][0]["test"] == "test_wrong"


def test_gdhs_own_runner_fails_asserts_errors_and_crashes(tmp_path):
    project = make_project(tmp_path / "game", "gdh")
    proc = gdh("test", "--project", project, "--out", tmp_path / "out", "--json", check=False)
    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    assert report["framework"] == "gdh"
    assert report["tests"] == 4 and report["failed"] == 2
    failed = {f["test"]: f["message"] for f in report["failures"]}
    assert "Assertion failed: one is two" in failed["test_wrong"]
    assert "Nonexistent function 'foo'" in failed["test_crashes"]


def test_passing_tests_exit_zero_and_paths_pick_tests(tmp_path):
    project = make_project(tmp_path / "game", "gdh")
    (project / "test" / "test_good.gd").write_text("extends RefCounted\n\nfunc test_fine():\n\tassert(true)\n")
    proc = gdh("test", "--project", project, "res://test/test_good.gd", "--out", tmp_path / "out")
    assert "1 tests, 1 passed, 0 failed" in proc.stdout


def test_a_test_file_that_doesnt_parse_is_a_failure(tmp_path):
    project = make_project(tmp_path / "game", "gdh")
    (project / "test" / "test_math.gd").write_text("extends RefCounted\nfunc test_x(:\n")
    proc = gdh("test", "--project", project, "--out", tmp_path / "out", check=False)
    assert proc.returncode == 1
    assert "(load)" in proc.stdout and "Parse Error" in proc.stdout
