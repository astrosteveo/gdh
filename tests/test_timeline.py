"""gdh live start --timeline: every command in <out>/timeline/, against testbed/live/arena.tscn."""
import json
import os
import subprocess
import sys

from conftest import TESTBED, gdh

SESSION = f"test-timeline-{os.getpid()}"


def entries(out):
    lines = (out / "timeline" / "timeline.js").read_text().splitlines()
    assert all(line.startswith("T(") and line.endswith(");") for line in lines)
    return [json.loads(line[2:-2]) for line in lines]


def test_the_timeline_keeps_every_command_with_its_frames_errors_and_thumbnails(tmp_path, display):
    out = tmp_path / "live"
    started = gdh("live", "start", "--project", TESTBED, "--scene", "res://live/arena.tscn", "--session", SESSION,
                  "--out", out, "--timeline")
    try:
        assert f"timeline: {out / 'timeline' / 'index.html'}" in started.stdout
        gdh("live", "step", "10", "--hold", "ui_right", "--session", SESSION)
        gdh("live", "step", "3", "--click-text", "Go", "--session", SESSION)
        gdh("live", "eval", "get_node('Player').position.x", "--session", SESSION)
        gdh("live", "eval", "get_node('Nope').position", "--session", SESSION, check=False)
        gdh("live", "status", "--session", SESSION)  # not kept: it changes nothing
        subprocess.run([sys.executable, "-m", "gdh", "live", "batch", "--session", SESSION],
                       input="step 2\nshot\n", text=True, capture_output=True, check=True)
    finally:
        stopped = gdh("live", "stop", "--session", SESSION)
    assert "timeline:" in stopped.stdout
    kept = entries(out)
    assert [e["cmd"] for e in kept] == ["start", "step", "step", "eval", "eval", "step", "shot", "stop"]
    start, hold, click, value, failed, batch_step, shot, _ = kept
    assert start["scene"] == "res://live/arena.tscn" and isinstance(start["seed"], int)
    assert hold["ran"] == 10 and hold["replies"][0]["frame"] == 10
    assert click["replies"][0]["result"]["aimed"][0]["path"] == "UI/GoButton"
    assert value["replies"][0]["result"]["value"] > 0
    assert not failed["ok"] and "error" in failed["replies"][0]
    # A thumbnail after each step, small, beside the page; a shot's own file linked from the page.
    for e in (hold, click, batch_step):
        [thumb] = e["thumbs"]
        assert (out / "timeline" / thumb).is_file()
    assert "thumbs" not in value
    [linked] = shot["replies"][0]["shots"]
    assert (out / "timeline" / linked).is_file()
    assert "<script src=\"timeline.js\"></script>" in (out / "timeline" / "index.html").read_text()


def test_a_restart_adds_to_the_timeline_and_a_new_start_begins_it_again(tmp_path, display):
    out = tmp_path / "live"
    start = ("live", "start", "--project", TESTBED, "--scene", "res://live/arena.tscn", "--session", SESSION,
             "--out", out, "--timeline")
    gdh(*start)
    try:
        gdh("live", "step", "5", "--session", SESSION)
        gdh("live", "restart", "--replay", "--session", SESSION)
        # The replay sends the step again, after the mark.
        assert [e["cmd"] for e in entries(out)] == ["start", "step", "stop", "restart", "step"]
    finally:
        gdh("live", "stop", "--session", SESSION)
    gdh(*start)
    gdh("live", "stop", "--session", SESSION)
    assert [e["cmd"] for e in entries(out)] == ["start", "stop"]


def test_the_timeline_keeps_pipe_requests_and_every_instance(tmp_path, display):
    out = tmp_path / "live"
    gdh("live", "start", "--project", TESTBED, "--scene", "res://live/arena.tscn", "--session", SESSION,
        "--out", out, "--timeline", "--instances", "2")
    try:
        requests = [{"cmd": "step", "args": {"frames": 4}}, {"cmd": "eval", "args": {"expr": "1 + 1"}, "instance": 1}]
        subprocess.run([sys.executable, "-m", "gdh", "live", "pipe", "--session", SESSION], check=True, text=True,
                       input="".join(json.dumps(r) + "\n" for r in requests), capture_output=True)
    finally:
        gdh("live", "stop", "--session", SESSION)
    start, step, value, _ = entries(out)
    assert start["instances"] == 2
    # A step goes to both instances: a reply and a thumbnail from each.
    assert [r["instance"] for r in step["replies"]] == [0, 1] and [r["frame"] for r in step["replies"]] == [4, 4]
    assert len(step["thumbs"]) == 2 and all((out / "timeline" / t).is_file() for t in step["thumbs"])
    assert value["instance"] == 1 and value["replies"][0]["result"]["value"] == 2
