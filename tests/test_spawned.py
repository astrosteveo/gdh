"""Processes a live game spawns: listed by status, ended with the session, and with --keep-children kept running, with
their display, after the game has handed off to them and exited (testbed/children: a launcher and its child)."""
import json
import os
import time

from conftest import TESTBED, gdh
from gdh.godot import pid_alive
from gdh.live import session_path

SESSION = f"test-spawned-{os.getpid()}"
LAUNCHER = "res://children/launcher.tscn"


def start(tmp_path, name, *options, quit_after=3, child_seconds=60):
    child_file = tmp_path / f"{name}-child.txt"
    gdh("live", "start", "--project", TESTBED, "--scene", LAUNCHER, "--session", name, "--out", tmp_path / name,
        *options, "--", "--quit-after", quit_after, "--child-seconds", child_seconds, "--child-file", child_file)
    return child_file


def status(name):
    return json.loads(gdh("live", "status", "--session", name, "--json").stdout)


def child_pid(name):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        found = [p for p in status(name)["spawned"] if "children/child.gd" in p["cmd"]]
        if found:
            return found[0]["pid"]
        time.sleep(0.2)
    raise AssertionError("the launcher's child never showed up in status")


def frames_written(child_file):
    try:
        return int(child_file.read_text().split()[1])
    except (OSError, IndexError, ValueError):
        return 0


def wait_for(condition, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.2)
    return False


def test_spawned_processes_are_listed_and_stop_with_the_session(tmp_path):
    name = f"{SESSION}-stop"
    start(tmp_path, name, quit_after=100000)
    try:
        pid = child_pid(name)
        text = gdh("live", "status", "--session", name).stdout
        assert f"spawned: pid {pid}: " in text
    finally:
        gdh("live", "stop", "--session", name)
    assert wait_for(lambda: not pid_alive(pid), 5)


def test_without_keep_children_a_handoff_ends_the_session(tmp_path):
    name = f"{SESSION}-handoff"
    start(tmp_path, name)
    pid = child_pid(name)
    try:
        gdh("live", "step", "5", "--session", name, check=False)  # the launcher quits during it
        assert wait_for(lambda: not pid_alive(pid))
        assert wait_for(lambda: not session_path(name).exists() or "has ended" in
                        gdh("live", "status", "--session", name, check=False).stderr)
    finally:
        gdh("live", "stop", "--session", name)


def test_keep_children_keeps_the_session_and_display_until_they_end(tmp_path):
    name = f"{SESSION}-keep"
    child_file = start(tmp_path, name, "--keep-children", child_seconds=12)
    try:
        pid = child_pid(name)
        session = json.loads(session_path(name).read_text())
        groups = [g for g in session["groups"] if g != session["pid"]]
        gdh("live", "step", "5", "--session", name, check=False)
        assert wait_for(lambda: not pid_alive(session["pid"]))

        reply = status(name)
        assert reply["exited"] == [0]
        assert [p["pid"] for p in reply["spawned"]] == [pid]
        assert "the game has exited" in gdh("live", "status", "--session", name).stdout
        # Still running on the session's display, drawing frames.
        before = frames_written(child_file)
        assert wait_for(lambda: frames_written(child_file) > before + 10, 10)
        # Commands that need the game say why they can't.
        shot = gdh("live", "shot", "--session", name, check=False)
        assert shot.returncode == 1 and "has exited, so it takes no commands" in shot.stderr
        assert session_path(name).exists()

        # Once the child ends by itself, the watchdog stops the display and the session ends.
        assert wait_for(lambda: not pid_alive(pid), 30)
        assert wait_for(lambda: not any(pid_alive(g) for g in groups), 15)
        ended = gdh("live", "status", "--session", name, check=False)
        assert ended.returncode == 1
        assert not session_path(name).exists()
    finally:
        gdh("live", "stop", "--session", name)


def test_keep_children_ends_after_the_idle_timeout_once_the_game_has_exited(tmp_path):
    """The idle timeout lives in the game's harness, so with the launcher gone the watchdog keeps it: gdh live
    commands hold the session open, and without them what the game spawned and the display are stopped."""
    name = f"{SESSION}-idle"
    idle = 4
    start(tmp_path, name, "--keep-children", "--idle-timeout", idle, child_seconds=120)
    try:
        pid = child_pid(name)
        session = json.loads(session_path(name).read_text())
        groups = [g for g in session["groups"] if g != session["pid"]]
        gdh("live", "step", "5", "--session", name, check=False)
        assert wait_for(lambda: not pid_alive(session["pid"]))

        # Commands count as activity: well past the timeout, the child still runs.
        until = time.monotonic() + 2.5 * idle
        while time.monotonic() < until:
            assert status(name)["exited"] == [0]
            time.sleep(1)
        assert pid_alive(pid)

        # Left alone, the session ends: the child, then the display.
        assert wait_for(lambda: not pid_alive(pid), idle + 10)
        assert wait_for(lambda: not any(pid_alive(g) for g in groups), 15)
        ended = gdh("live", "status", "--session", name, check=False)
        assert ended.returncode == 1 and "--idle-timeout" in ended.stderr
        assert not session_path(name).exists()
    finally:
        gdh("live", "stop", "--session", name)
