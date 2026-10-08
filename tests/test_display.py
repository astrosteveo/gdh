"""The displays gdh runs Godot on (display.py): the GPU display (weston and
Xwayland) and Xvfb. These name the display themselves, so they run once."""
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from conftest import GPU_DISPLAY, TESTBED, gdh, gdh_json
from gdh.godot import pid_alive

SESSION = f"test-display-{os.getpid()}"
needs_gpu_display = pytest.mark.skipif(not GPU_DISPLAY, reason="the GPU display needs weston and Xwayland")


@pytest.fixture(scope="session")
def display():
    """Overrides conftest's: each test here picks its displays itself."""
    return None


def run(*args, env=None):
    return subprocess.run([sys.executable, "-m", "gdh", *map(str, args)], capture_output=True, text=True,
                          timeout=600, env=env)


def session_file(name):
    from gdh.live import session_path
    return json.loads(session_path(name).read_text())


def gone(record):
    """Whether a display's processes have all exited and its runtime directory is removed."""
    return (not any(pid_alive(g) for g in record["groups"])
            and not (record.get("runtime_dir") and os.path.exists(record["runtime_dir"])))


def wait_until(test, seconds=15):
    deadline = time.monotonic() + seconds
    while not test() and time.monotonic() < deadline:
        time.sleep(0.2)
    return test()


def path_without(directory, *programs):
    """A PATH with every program on the current one except these: as if they weren't installed."""
    directory.mkdir(parents=True)
    for entry in os.environ["PATH"].split(os.pathsep):
        if not os.path.isdir(entry):
            continue
        for name in os.listdir(entry):
            link = directory / name
            if name not in programs and not link.exists() and os.access(os.path.join(entry, name), os.X_OK):
                link.symlink_to(os.path.join(entry, name))
    return str(directory)


def runtime_dirs():
    """The GPU displays' runtime directories that no running weston holds: left behind. Another gdh run on the
    machine (another agent's tests) has directories of its own, which its weston holds, so they don't count."""
    import fcntl
    from gdh.display import SOCKET, displays_root
    left = set()
    for directory in displays_root().glob("*") if displays_root().exists() else []:
        try:
            with open(directory / f"{SOCKET}.lock") as f:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)  # (taken: nothing holds it)
        except BlockingIOError:
            continue
        except OSError:
            pass  # no lock file: weston never started there, or it's gone
        left.add(directory)
    return left


@needs_gpu_display
def test_screenshots_are_the_same_on_both_displays(tmp_path):
    scene = "res://blind/scene_03.tscn"  # 3D: sun, shadows, alpha-tested cards
    for display in ("gpu", "xvfb"):
        proc = gdh("capture", "--project", TESTBED, "--scene", scene, "--out", tmp_path / display, "--display", display)
        assert f"display={display}" in proc.stdout
    views = sorted(p.name for p in (tmp_path / "gpu").glob("*.png"))
    assert len(views) == 6
    for view in views:
        gpu, xvfb = (Image.open(tmp_path / d / view).convert("RGBA") for d in ("gpu", "xvfb"))
        assert gpu.size == xvfb.size == (1280, 720)
        assert ImageChops.difference(gpu, xvfb).getbbox() is None, f"{view} differs between the displays"


@needs_gpu_display
def test_live_shots_are_the_same_on_both_displays(tmp_path):
    views = ["normal", "unshaded", "lighting", "normals", "wireframe", "overdraw"]
    shots = {}
    for display in ("gpu", "xvfb"):
        name = f"{SESSION}-same-{display}"
        gdh("live", "start", "--project", TESTBED, "--scene", "res://blind/scene_03.tscn", "--session", name,
            "--out", tmp_path / display, "--display", display)
        try:
            gdh("live", "step", "45", "--session", name)
            shots[display] = gdh_json("live", "shot", *[a for v in views for a in ("--view", v)],
                                      "--session", name)["result"]["shots"]
        finally:
            gdh("live", "stop", "--session", name)
    for view in views:
        gpu, xvfb = (Image.open(shots[d][view]).convert("RGBA") for d in ("gpu", "xvfb"))
        assert gpu.size == xvfb.size
        assert ImageChops.difference(gpu, xvfb).getbbox() is None, f"{view} differs between the displays"


@needs_gpu_display
def test_gpu_display_runs_godot_on_x11_with_dri3_and_cleans_up(tmp_path):
    gdh("live", "start", "--project", TESTBED, "--scene", "res://live/arena.tscn", "--session", SESSION,
        "--out", tmp_path, "--display", "gpu")
    record = session_file(SESSION)["instances"][0]["display"]
    try:
        assert record["kind"] == "gpu" and record["device"].startswith("/dev/dri/")
        assert os.path.isdir(record["runtime_dir"])
        # Godot's own X11 driver, with V-Sync off: gdh paces the frames.
        reply = gdh_json("live", "eval", "[DisplayServer.get_name(), DisplayServer.window_get_vsync_mode()]",
                         "--session", SESSION)
        assert reply["result"]["value"] == ["X11", 0]
        if shutil.which("xdpyinfo"):  # DRI3 is what lets Vulkan hand frames over on the GPU
            extensions = subprocess.run(["xdpyinfo", "-display", record["name"], "-queryExtensions"],
                                        capture_output=True, text=True).stdout
            assert "DRI3" in extensions and "Present" in extensions
        assert "display gpu" in gdh("live", "status", "--session", SESSION).stdout
        # A game that turns V-Sync on hears about it on its next step.
        gdh("live", "eval", "DisplayServer.window_set_vsync_mode(DisplayServer.VSYNC_ENABLED)", "--session", SESSION)
        notes = gdh_json("live", "step", "2", "--session", SESSION).get("notes", [])
        assert any("V-Sync" in n for n in notes)
    finally:
        gdh("live", "stop", "--session", SESSION)
    assert gone(record)


@needs_gpu_display
def test_window_smaller_than_xwaylands_smallest_screen(tmp_path):
    # Xwayland's screen is never under 320x200; the window, and so the screenshot, keeps its own size.
    proc = gdh("capture", "--project", TESTBED, "--scene", "res://live/arena.tscn", "--out", tmp_path,
               "--display", "gpu", "--resolution", "300x180", "--modes", "normal")
    assert "exit=0" in proc.stdout and "display=gpu" in proc.stdout
    assert Image.open(tmp_path / "normal.png").size == (300, 180)


@needs_gpu_display
def test_watchdog_stops_the_display_when_the_game_dies(tmp_path):
    name = f"{SESSION}-kill"
    gdh("live", "start", "--project", TESTBED, "--scene", "res://live/arena.tscn", "--session", name,
        "--out", tmp_path, "--display", "gpu", "--instances", "2", "--resolution", "640x360")
    session = session_file(name)
    records = [i["display"] for i in session["instances"]]
    assert len({r["name"] for r in records}) == 2  # a display each
    os.kill(session["instances"][1]["pid"], signal.SIGKILL)
    assert wait_until(lambda: all(gone(r) for r in records))
    assert "has ended" in gdh("live", "status", "--session", name, check=False).stderr


@needs_gpu_display
def test_auto_falls_back_to_xvfb_without_weston(tmp_path):
    env = {**os.environ, "PATH": path_without(tmp_path / "bin", "weston"), "GDH_DISPLAY": "auto"}
    proc = run("capture", "--project", TESTBED, "--scene", "res://smoke/smoke.tscn", "--out", tmp_path,
               "--modes", "normal", env=env)
    assert proc.returncode == 0, proc.stderr
    assert "display=xvfb" in proc.stdout
    assert "running on Xvfb" in proc.stderr and "weston is not installed" in proc.stderr
    assert json.loads((tmp_path / "report.json").read_text())["display"] == "xvfb"

    proc = run("capture", "--project", TESTBED, "--scene", "res://smoke/smoke.tscn", "--out", tmp_path,
               "--modes", "normal", "--display", "gpu", env=env)
    assert proc.returncode == 1
    assert "The GPU display can't start: weston is not installed" in proc.stderr


@needs_gpu_display
@pytest.mark.parametrize("egl, reason", [
    # Mesa's software renderer: weston starts, but on no GPU.
    ({"__EGL_VENDOR_LIBRARY_FILENAMES": "/usr/share/glvnd/egl_vendor.d/50_mesa.json", "LIBGL_ALWAYS_SOFTWARE": "1"},
     "weston found no GPU to composite on"),
    # No EGL at all: weston can't start.
    ({"__EGL_VENDOR_LIBRARY_FILENAMES": "/nonexistent.json"}, "weston and Xwayland didn't start"),
])
def test_auto_falls_back_to_xvfb_without_a_gpu_to_composite_on(tmp_path, egl, reason):
    if "LIBGL_ALWAYS_SOFTWARE" in egl and not os.path.exists(egl["__EGL_VENDOR_LIBRARY_FILENAMES"]):
        pytest.skip("needs Mesa's EGL")
    # Godot renders with Vulkan, which these leave alone.
    env = {**os.environ, **egl, "GDH_DISPLAY": "auto"}
    before = runtime_dirs()
    proc = run("live", "start", "--project", TESTBED, "--scene", "res://live/arena.tscn", "--session",
               f"{SESSION}-nogpu", "--out", tmp_path, env=env)
    try:
        assert proc.returncode == 0, proc.stderr
        assert reason in proc.stderr and "running on Xvfb" in proc.stderr
        assert session_file(f"{SESSION}-nogpu")["instances"][0]["display"]["kind"] == "xvfb"
    finally:
        gdh("live", "stop", "--session", f"{SESSION}-nogpu")
    # Nothing of the GPU display that didn't start is left: its runtime directory is gone.
    assert not runtime_dirs() - before


def test_stopping_a_group_reaps_our_own_children_without_waiting():
    """A child of ours that has exited stays in its process group until it's reaped: kill_groups reaps it, rather
    than waiting out its SIGTERM and SIGKILL grace (3 s and 1 s) on it."""
    from gdh.godot import kill_groups
    exited = subprocess.Popen(["sh", "-c", "exit 7"], start_new_session=True)
    assert exited.wait() == 7
    running = subprocess.Popen(["sleep", "30"], start_new_session=True)
    unreaped = subprocess.Popen(["true"], start_new_session=True)
    time.sleep(0.2)
    started = time.monotonic()
    kill_groups(exited.pid, running.pid, unreaped.pid)
    assert time.monotonic() - started < 1
    assert not pid_alive(running.pid) and not pid_alive(unreaped.pid)
    assert exited.returncode == 7  # read before: kept


@pytest.mark.parametrize("kind", ["gpu", "xvfb"])
def test_a_display_stops_as_soon_as_its_processes_have_exited(kind):
    if kind == "gpu" and not GPU_DISPLAY:
        pytest.skip("the GPU display needs weston and Xwayland")
    from gdh.display import open_display
    display = open_display(kind, "640x360")
    record = display.record()
    started = time.monotonic()
    display.stop()
    assert time.monotonic() - started < 1  # (it waited 4 s on its own exited processes)
    assert gone(record)


@needs_gpu_display
def test_a_capture_takes_little_more_than_godots_run(tmp_path):
    def capture():
        started = time.monotonic()
        proc = gdh("capture", "--project", TESTBED, "--scene", "res://live/arena.tscn", "--modes", "normal",
                   "--display", "gpu", "--out", tmp_path)
        assert "exit=0" in proc.stdout
        return time.monotonic() - started
    assert min(capture(), capture()) < 2.5  # (5.7 s while each stop waited on the display's exited processes)
    # A Godot exit code is kept: a scene that doesn't load exits 3.
    proc = gdh("capture", "--project", TESTBED, "--scene", "res://no/such.tscn", "--modes", "normal",
               "--display", "gpu", "--out", tmp_path / "missing", check=False)
    assert "exit=3" in proc.stdout and proc.returncode == 1


def test_unknown_display_is_refused(tmp_path):
    proc = run("capture", "--project", TESTBED, "--scene", "res://smoke/smoke.tscn", "--out", tmp_path,
               env={**os.environ, "GDH_DISPLAY": "wayland"})
    assert proc.returncode == 1
    assert "GDH_DISPLAY is 'wayland'" in proc.stderr


def test_stale_runtime_directories_are_swept(tmp_path):
    import fcntl

    from gdh.display import SOCKET, STALE_AFTER_S, sweep_stale
    root = tmp_path / "displays"
    held, unheld, starting, old = (root / name for name in ("held", "unheld", "starting", "old"))
    for directory in (held, unheld, starting, old):
        directory.mkdir(parents=True)
    (held / f"{SOCKET}.lock").touch()
    (unheld / f"{SOCKET}.lock").touch()
    os.utime(old, (time.time() - STALE_AFTER_S - 5,) * 2)
    with open(held / f"{SOCKET}.lock") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # as a running weston holds it
        sweep_stale(root)
    assert sorted(p.name for p in root.iterdir()) == ["held", "starting"]
    shutil.rmtree(root)
