"""Companions and instances in gdh live: a barrier (testbed/live/barrier.py)
started beside two instances of testbed/live/lockstep.tscn, which each wait at
it every frame, as clients of a server on a test clock do.
"""
import json
import os
import sys
import time
import urllib.request

import pytest

from conftest import TESTBED, gdh, gdh_json
from gdh.godot import pid_alive

SESSION = f"test-companions-{os.getpid()}"
BARRIER = TESTBED / "live" / "barrier.py"


def session_file(name):
    from gdh.live import session_path
    return json.loads(session_path(name).read_text())


@pytest.fixture(scope="module")
def pair(tmp_path_factory, display):
    out = tmp_path_factory.mktemp("pair")
    gdh("live", "start", "--project", TESTBED, "--scene", "res://live/lockstep.tscn", "--session", SESSION,
        "--out", out, "--instances", "2", "--companion", f"barrier={sys.executable} {BARRIER} {{port}} 2",
        "--", "--barrier", "{barrier.port}", "--me", "{instance}")
    yield session_file(SESSION)
    gdh("live", "stop", "--session", SESSION)


def live(*args):
    return gdh_json("live", *args, "--session", SESSION)


def test_placeholders_reach_each_instance(pair):
    port = str(pair["companions"][0]["port"])
    reply = live("eval", "OS.get_cmdline_user_args()", "--instance", "all")
    assert [r["result"]["value"] for r in reply["instances"]] == [["--barrier", port, "--me", "0"],
                                                                  ["--barrier", port, "--me", "1"]]


def test_instances_step_together(pair):
    started = time.monotonic()
    reply = live("step", "60")
    # Stepped one after the other, each frame of the first would wait out the barrier (5 s).
    assert time.monotonic() - started < 30
    assert [r["result"]["frames"] for r in reply["instances"]] == [60, 60]
    counts = live("eval", "[synced, timed_out]", "--instance", "all")
    assert [r["result"]["value"] for r in counts["instances"]] == [[60, 0], [60, 0]]


def test_input_goes_to_the_instance_named(pair):
    before = [r["result"]["value"] for r in live("eval", "held_right", "--instance", "all")["instances"]]
    live("step", "10", "--hold", "ui_right", "--instance", "1")
    after = [r["result"]["value"] for r in live("eval", "held_right", "--instance", "all")["instances"]]
    assert [a - b for a, b in zip(after, before)] == [0, 10]


def test_a_hold_is_let_go_by_its_event(pair):
    live("step", "5", "--hold", "ui_right")
    # Released as the step ends: a node that tracks its keys by their events sees it go, though the game is held.
    assert live("eval", "right_down")["result"]["value"] is False
    live("step", "5", "--press", "ui_right")
    assert live("eval", "right_down")["result"]["value"] is True
    live("step", "1", "--release", "ui_right")
    assert live("eval", "right_down")["result"]["value"] is False


def test_one_instance_answers_alone(pair):
    reply = live("eval", "synced", "--instance", "1")
    assert reply["instance"] == 1 and "instances" not in reply
    assert gdh("live", "eval", "1", "--instance", "2", "--session", SESSION, check=False).returncode == 1


def test_pipe_takes_many_requests(pair):
    import subprocess
    requests = [{"cmd": "step", "args": {"frames": 5}},
                {"cmd": "eval", "args": {"expr": "synced"}, "instance": "all"},
                {"cmd": "eval", "args": {"expr": "nothing_here"}, "instance": 0}]
    proc = subprocess.run([sys.executable, "-m", "gdh", "live", "pipe", "--session", SESSION],
                          input="".join(json.dumps(r) + "\n" for r in requests), capture_output=True, text=True,
                          timeout=120)
    replies = [json.loads(line) for line in proc.stdout.splitlines()]
    assert proc.returncode == 0 and len(replies) == 3
    assert replies[0]["ok"] and [r["result"]["frames"] for r in replies[0]["instances"]] == [5, 5]
    synced = [r["result"]["value"] for r in replies[1]["instances"]]
    assert synced[0] == synced[1]
    assert not replies[2]["ok"]


def test_status_shows_the_companion(pair):
    text = gdh("live", "status", "--session", SESSION).stdout
    assert "[0] held at frame" in text and "[1] held at frame" in text
    assert "companion 'barrier': running" in text


def test_stop_stops_the_companion(tmp_path):
    name = f"{SESSION}-stop"
    gdh("live", "start", "--project", TESTBED, "--scene", "res://live/lockstep.tscn", "--session", name,
        "--out", tmp_path, "--companion", f"web={sys.executable} -m http.server {{port}} --bind 127.0.0.1",
        "--companion-ready", "web=http://127.0.0.1:{port}/")
    web = session_file(name)["companions"][0]
    with urllib.request.urlopen(f"http://127.0.0.1:{web['port']}/", timeout=5) as reply:
        assert reply.status == 200
    gdh("live", "stop", "--session", name)
    assert not pid_alive(web["pid"])
    assert (tmp_path / "web.log").exists()


def test_companion_goes_when_the_game_ends_by_itself(tmp_path):
    name = f"{SESSION}-idle"
    gdh("live", "start", "--project", TESTBED, "--scene", "res://live/lockstep.tscn", "--session", name,
        "--out", tmp_path, "--idle-timeout", "2", "--companion", f"barrier={sys.executable} {BARRIER} {{port}} 1")
    companion = session_file(name)["companions"][0]["pid"]
    deadline = time.monotonic() + 15
    while pid_alive(companion) and time.monotonic() < deadline:
        time.sleep(0.5)
    assert not pid_alive(companion)
    assert "has ended" in gdh("live", "status", "--session", name, check=False).stderr


def test_companion_that_fails_stops_the_start(tmp_path):
    name = f"{SESSION}-bad"
    proc = gdh("live", "start", "--project", TESTBED, "--scene", "res://live/lockstep.tscn", "--session", name,
               "--out", tmp_path, "--companion", "broken=echo about to fail; exit 3", check=False)
    assert proc.returncode == 1
    assert "exited with code 3" in proc.stderr and "about to fail" in proc.stderr
    assert gdh("live", "status", "--session", name, check=False).returncode == 1


def test_unknown_placeholder_is_refused(tmp_path):
    proc = gdh("live", "start", "--project", TESTBED, "--scene", "res://live/lockstep.tscn",
               "--session", f"{SESSION}-typo", "--out", tmp_path, "--", "--server", "{nothing.port}", check=False)
    assert proc.returncode == 1
    assert "{nothing.port} names no companion" in proc.stderr
