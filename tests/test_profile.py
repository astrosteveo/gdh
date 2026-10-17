"""gdh live start --profiler and gdh live profile against testbed/profile/profile.tscn: light() every physics frame,
and every 30th frame heavy(), whose time is all in crunch(), a busy loop. Also the debugger's protocol (variant.py)."""
import os
import struct

import pytest

from conftest import TESTBED, gdh, gdh_json
from gdh import variant
from gdh.debugger import Debugger, parse_frame
from gdh.profile import summarize

SESSION = f"test-profile-{os.getpid()}"


@pytest.fixture(scope="module")
def game(tmp_path_factory, display):
    out = tmp_path_factory.mktemp("profile")
    gdh("live", "start", "--project", TESTBED, "--scene", "res://profile/profile.tscn", "--session", SESSION,
        "--profiler", "--out", out)
    yield out
    gdh("live", "stop", "--session", SESSION)


def test_profile_ranks_the_games_functions_and_finds_the_spikes(game):
    reply = gdh_json("live", "profile", "90", "--session", SESSION)
    top = {row["function"]: row for row in reply["top"]["by_self"]}
    # gdh's own functions (its harness) are left out: only the game's.
    assert set(top) == {"crunch", "_physics_process", "light", "heavy"}
    assert all(row["script"] == "res://profile/profile.gd" for row in top.values())
    assert reply["top"]["by_self"][0]["function"] == "crunch" and top["crunch"]["calls"] == 3
    # The profile holds every frame stepped, the first included, and only those: a tick each.
    assert top["crunch"]["line"] == 30 and top["light"]["calls"] == top["_physics_process"]["calls"] == 90
    # heavy() spends its time in crunch(): its total is crunch's, its self time next to nothing.
    assert top["heavy"]["total_ms"] == pytest.approx(top["crunch"]["total_ms"], rel=0.05)
    assert top["heavy"]["self_ms"] < 0.05 * top["heavy"]["total_ms"]
    assert "physics_2d" in reply["servers_ms"] and reply["frames"] >= 90
    # With a budget between a typical frame and one that runs heavy(), the 3 frames it ran in (their physics taking
    # the time) are among the spikes, 30 frames apart; and on the spikes it's heavy() and crunch() that took it. (The
    # engine has slow frames of its own now and then, with no script in them, which count too.)
    heavy = top["crunch"]["total_ms"] / 3
    budget = reply["game_ms"]["median"] + heavy / 2
    reply = gdh_json("live", "profile", "90", "--budget", f"{budget:.3f}", "--session", SESSION)
    assert {row["function"]: row["calls"] for row in reply["top"]["by_self"]}["light"] == 90
    frames = [s["frame"] for s in reply["spikes"] if s["physics_ms"] > heavy / 2]
    assert len(frames) == 3 and [b - a for a, b in zip(frames, frames[1:])] == [30, 30]
    assert {"heavy", "crunch"} <= {row["function"] for row in reply["spike_top"]["by_total"][:4]}
    assert reply["spike_top"]["by_self"][0]["function"] == "crunch"


def test_profile_takes_a_budget_and_prints(game):
    reply = gdh_json("live", "profile", "30", "--budget", "1000", "--session", SESSION)
    assert reply["spikes"] == [] and reply["spike_rule"] == "over the budget, 1000 ms" and "spike_top" not in reply
    proc = gdh("live", "profile", "60", "--top", "2", "--session", SESSION)
    assert "the game's top functions by self time (4 called):" in proc.stdout
    assert "    crunch (res://profile/profile.gd:30): self " in proc.stdout
    assert "left out of the frames' times" in proc.stdout


def test_the_game_runs_on_through_errors_under_the_debugger(game):
    proc = gdh("live", "eval", "fail()", "--session", SESSION)
    assert "game: about to fail" in proc.stderr and "Cannot call method 'is_inside_tree' on a null value" in proc.stderr
    assert gdh_json("live", "step", "5", "--session", SESSION)["result"]["frames"] == 5


def test_profile_needs_the_debugger(tmp_path):
    session = f"{SESSION}-plain"
    gdh("live", "start", "--project", TESTBED, "--scene", "res://profile/profile.tscn", "--session", session,
        "--out", tmp_path)
    try:
        proc = gdh("live", "profile", "10", "--session", session, check=False)
        assert proc.returncode == 1 and "start the session with gdh live start --profiler" in proc.stderr
    finally:
        gdh("live", "stop", "--session", session)
    proc = gdh("live", "start", "--project", TESTBED, "--session", f"{SESSION}-two", "--profiler", "--instances", "2",
               "--out", tmp_path / "two", check=False)
    assert proc.returncode == 1 and "--profiler profiles one game" in proc.stderr


def test_the_debugger_says_when_the_game_sends_no_profile():
    debugger = Debugger()
    debugger.connected = True  # with no game to send to, and so no frames
    assert debugger.answer({"cmd": "start", "timeout": 0.05}) == {
        "error": "The game sent gdh's debugger no profiled frames."}
    assert not debugger.recording
    debugger.recording = True
    debugger.frames = [{"frame": 41}]
    assert debugger.answer({"cmd": "stop", "until": 42, "timeout": 0.05}) == {
        "error": "The game sent gdh's debugger no profile of frame 42."}


def test_variants_round_trip_and_frames_parse():
    value = ["profiler:servers", 1, [True, [256, False], 2.5, None, {"a": -3}, 2 ** 40]]
    assert variant.decode(variant.encode(value)) == value
    packet = variant.message("continue", [])
    assert struct.unpack("<I", packet[:4])[0] == len(packet) - 4
    assert variant.decode(packet[4:]) == ["continue", 1, []]
    frame = parse_frame([7, 0.01, 0.006, 0.004, 1 / 60, 0.002, 2, "physics_2d", 2, "step", 0.001, "audio", 0,
                         10, 0, 3, 0.0015, 0.002, 0.0, 1, 1, 0.0005, 0.0005, 0.0])
    assert frame["frame"] == 7 and frame["servers"] == {"physics_2d": {"step": 0.001}, "audio": {}}
    assert frame["functions"] == [[0, 3, 0.0015, 0.002, 0.0], [1, 1, 0.0005, 0.0005, 0.0]]


def test_the_default_spike_rule():
    def frame(n, ms, fn):
        return {"frame": n, "frame_time": 0.016, "process_time": ms / 1000, "physics_time": 0.0,
                "physics_frame_time": 1 / 60, "script_time": 0.0, "servers": {}, "functions": [[fn, 1, ms / 1000, ms / 1000, 0.0]]}
    signatures = {"0": "res://a.gd::3::cheap", "1": "res://a.gd::9::costly"}
    frames = [frame(i, 2.0, 0) for i in range(40)] + [frame(40, 7.5, 1), frame(41, 2.9, 0)]
    out = summarize(frames, signatures, 60)
    # Over 3 times the median (2 ms), and 1 ms over it: 7.5 ms is a spike, 2.9 ms isn't.
    assert out["spike_threshold_ms"] == 6 and [s["frame"] for s in out["spikes"]] == [40]
    assert out["spike_top"]["by_self"][0]["function"] == "costly"
    out = summarize([frame(i, 0.2, 0) for i in range(10)] + [frame(10, 1.0, 1)], signatures, 60)
    assert out["spikes"] == []  # 5 times the median, but not 1 ms over it
