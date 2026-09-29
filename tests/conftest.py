import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TESTBED = ROOT / "testbed"

if not (shutil.which("xvfb-run") and shutil.which("godot")):
    pytest.skip("needs godot and xvfb-run on PATH", allow_module_level=True)


def gdh(*args, check=True):
    """Run the gdh CLI and return the finished process."""
    proc = subprocess.run([sys.executable, "-m", "gdh", *map(str, args)],
                          capture_output=True, text=True, timeout=600)
    if check and proc.returncode != 0:
        raise AssertionError(f"gdh {' '.join(map(str, args))} failed:\n{proc.stdout}\n{proc.stderr}")
    return proc


def gdh_json(*args):
    return json.loads(gdh(*args, "--json").stdout)


@pytest.fixture(scope="session", autouse=True)
def imported():
    gdh("import", "--project", TESTBED)


def make_testbed_variant(directory, settings):
    """Copy the testbed to directory with extra project settings, then import it.

    settings is project.godot text, e.g. "[physics]\\n\\ncommon/physics_ticks_per_second=120".
    """
    shutil.copytree(TESTBED, directory, ignore=shutil.ignore_patterns(".godot"))
    project = Path(directory) / "project.godot"
    project.write_text(project.read_text().replace("[rendering]", f"{settings}\n\n[rendering]"))
    gdh("import", "--project", directory)
    return directory
