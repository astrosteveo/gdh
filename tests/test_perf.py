"""gdh live bench, monitors and audio, and the frame record's warnings, against testbed/perf/perf.tscn.

The scene's modes churn nodes but keep their count flat (steady), leak a node into the tree each frame (leak), leak
an orphan node each frame (orphans), or play a beep over and over on three kinds of player and two buses (sound).
Other gdh runs may share the machine, so no test asserts a time.
"""
import json
import os

import pytest

from conftest import TESTBED, gdh, gdh_json
from gdh import perf

SCENE = "res://perf/perf.tscn"

# Sessions kept open at once (test_measure.py says why there's a limit).
OPEN_SESSIONS = 2


class Sessions:
    """Sessions on the scene, started on first use: sessions(mode, *start options) -> name. The least recently used
    is stopped to start another."""

    def __init__(self, out):
        self.out = out
        self.started = {}  # (mode, start options): name, least recently used first

    def __call__(self, mode, *start, fresh=False):
        key = (mode, start)
        if key in self.started and fresh:
            self.stop(self.started[key])
        if key in self.started:
            self.started[key] = self.started.pop(key)
            return self.started[key]
        while len(self.started) >= OPEN_SESSIONS:
            self.stop(next(iter(self.started.values())))
        name = f"s8-{mode}{'-passes' if start else ''}-{os.getpid()}"
        gdh("live", "start", "--project", TESTBED, "--scene", SCENE, "--session", name,
            "--out", self.out.mktemp(mode), *start, "--", "--mode", mode)
        self.started[key] = name
        return name

    def stop(self, name):
        gdh("live", "stop", "--session", name)
        self.started = {k: v for k, v in self.started.items() if v != name}


@pytest.fixture(scope="module")
def sessions(tmp_path_factory, display):
    started = Sessions(tmp_path_factory)
    yield started
    for name in list(started.started.values()):
        started.stop(name)


def frame(name):
    return gdh_json("live", "status", "--session", name)["result"]["frame"]


def session_pid(name):
    from gdh.live import SESSION_DIR
    return json.loads((SESSION_DIR / f"{name}.json").read_text())["pid"]


# --- bench ------------------------------------------------------------------------------------------------------------


def test_bench_prints_the_summary_and_passes_a_budget(sessions):
    name = sessions("steady")
    before = frame(name)
    out = gdh_json("live", "bench", "120", "--budget-median", "1000", "--budget-p99", "1000", "--session", name)
    # The warm-up draws held frames: no game time passes but the step's.
    assert frame(name) == before + 120
    assert out["game_frames"] == 120 and out["frames"] >= 10 and out["gpu_ms"]["p50"] > 0
    assert [b["stat"] for b in out["budgets"]] == ["median", "p99"]
    assert not out["over_budget"] and not any(b["over"] for b in out["budgets"])
    text = gdh("live", "bench", "30", "--warmup", "0.2", "--budget-p99", "1000", "--session", name).stdout
    assert "frames measured of 30 run" in text and "GPU: median" in text and "budget: GPU p99" in text and "within" in text


def test_bench_exits_1_over_a_budget(sessions, tmp_path):
    name = sessions("steady")
    proc = gdh("live", "bench", "60", "--budget-p99", "0.000001", "--save", tmp_path / "bench.json",
               "--session", name, check=False)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "budget: GPU p99" in proc.stdout and "OVER 1e-06 ms" in proc.stdout
    saved = json.loads((tmp_path / "bench.json").read_text())
    assert saved["summary"]["over_budget"] and len(saved["frames"]) == saved["summary"]["frames"]


def test_bench_warns_about_gpu_passes_and_other_games(sessions, tmp_path):
    passes = sessions("steady", "--gpu-passes")
    other = sessions("leak")
    out = gdh_json("live", "bench", "30", "--warmup", "0.2", "--save", tmp_path / "bench.json", "--session", passes)
    assert out["gpu_passes"] and any("--gpu-passes" in w for w in out["warnings"])
    games = {g["pid"]: g["what"] for g in out["other_games"]}
    assert games.get(session_pid(other)) == f"gdh session '{other}'"
    assert session_pid(passes) not in games  # the game measured isn't another
    assert any("other game" in w and f"gdh session '{other}'" in w for w in out["warnings"])
    # A record --save kept shows them too.
    times = gdh_json("measure", "times", tmp_path / "bench.json")
    assert times["gpu_passes"] and times["warnings"]
    # Without --gpu-passes, no such warning.
    plain = gdh_json("live", "bench", "30", "--warmup", "0.2", "--session", other)
    assert not plain["gpu_passes"] and not any("--gpu-passes" in w for w in plain["warnings"])


def test_frames_warns_about_a_game_that_ran_when_the_record_started(sessions):
    name = sessions("steady")
    other = sessions("leak")
    other_pid = session_pid(other)
    gdh("live", "frames", "--clear", "--session", name)
    sessions.stop(other)  # ended before the record is read: the game kept it from the start
    gdh("live", "step", "20", "--session", name)
    out = gdh_json("live", "frames", "--session", name)
    assert other_pid in [g["pid"] for g in out["other_games"]]
    text = gdh("live", "frames", "--session", name).stdout
    assert f"pid {other_pid}: gdh session '{other}'" in text and "warning:" in text


# --- monitors ---------------------------------------------------------------------------------------------------------


def test_monitors_read_the_counters(sessions):
    name = sessions("steady")
    r = gdh_json("live", "monitors", "--session", name)["result"]
    assert r["frame"] == frame(name)
    assert r["objects"] > r["nodes"] > 0 and r["resources"] > 0 and r["orphan_nodes"] == 0
    assert r["draw_calls"] > 0 and r["primitives"] > 0 and r["video_mem"] > 0
    text = gdh("live", "monitors", "--session", name).stdout
    assert "orphan nodes 0" in text and "draw calls" in text and "video memory" in text


def test_step_monitors_reads_before_and_after(sessions):
    name = sessions("leak")
    start = frame(name)
    readings = gdh_json("live", "step", "30", "--monitors", "--session", name)["result"]["monitors"]
    assert [r["frame"] for r in readings] == [start, start + 30]
    assert readings[1]["nodes"] - readings[0]["nodes"] == 30
    text = gdh("live", "step", "10", "--monitors", "--session", name).stdout
    assert "monitors over frames" in text and "nodes" in text and "+10" in text


@pytest.mark.parametrize("mode,grew", [("leak", {"nodes", "objects"}), ("orphans", {"orphan_nodes", "objects"}),
                                       ("steady", set())])
def test_leak_flags_a_scene_that_leaks_and_passes_one_that_does_not(sessions, mode, grew):
    name = sessions(mode)
    proc = gdh("live", "monitors", "--leak", "--frames", "240", "--every", "4", "--json", "--session", name,
               check=False)
    out = json.loads(proc.stdout)
    assert proc.returncode == (1 if grew else 0), proc.stdout + proc.stderr
    assert {leak["counter"] for leak in out["leaks"]} == grew
    assert len(out["samples"]) == 61 and out["warmup"] == 80
    for leak in out["leaks"]:
        assert leak["per_frame"] == pytest.approx(1, abs=0.05)
    text = gdh("live", "monitors", "--leak", "--frames", "120", "--session", name, check=False).stdout
    assert ("LEAK: nodes grew steadily" in text) if mode == "leak" else True
    assert ("no leak:" in text) == (not grew)


def readings(values, every=10):
    return [{"frame": i * every, "objects": v, "nodes": 10} for i, v in enumerate(values)]


def test_the_leak_rule():
    steady = readings([100 + i for i in range(40)])
    assert [leak["counter"] for leak in perf.leaks(steady, 0)] == ["objects"]
    assert perf.leaks(steady, 0)[0]["per_frame"] == pytest.approx(0.1)
    # A pool that churns, a count that jumps once and stays, and one that falls: none grows steadily.
    churn = readings([100 + (i * 7) % 13 for i in range(40)])
    jump = readings([100] * 20 + [150] * 20)
    falls = readings([200 - i for i in range(40)])
    for samples in (churn, jump, falls):
        assert perf.leaks(samples, 0) == []
    # Only what comes after the warm-up counts: growth that stops before it isn't a leak.
    settles = readings([100 + i for i in range(45)] + [145] * 15)
    assert perf.leaks(settles, 0) and perf.leaks(settles, 450) == []
    with pytest.raises(perf.PerfError):
        perf.leaks(steady, 350)


# --- audio ------------------------------------------------------------------------------------------------------------


def test_audio_reports_the_players_and_the_buses_levels(sessions):
    name = sessions("sound")
    # The Dummy driver mixes in real time, a block every 93 ms, and 600 frames of this scene can step in less: step
    # until a block was mixed during one.
    for _ in range(10):
        gdh("live", "step", "600", "--session", name)
        r = gdh_json("live", "audio", "--session", name)["result"]
        if r["mixes"]:
            break
    assert r["mixes"] >= 1 and r["driver"] == "Dummy" and r["frames"] == 600, r
    buses = {b["bus"]: b for b in r["buses"]}
    assert set(buses) == {"Master", "Effects"} and buses["Effects"]["send"] == "Master"
    # The beep is at about -6 dB; three players add up on Master, and the 2D player alone is on Effects.
    assert buses["Master"]["peak_db"] > -20 and buses["Effects"]["peak_db"] > -40
    players = {p["node"]: p for p in r["players"]}
    assert {n: p["class"] for n, p in players.items()} == {
        "Beep": "AudioStreamPlayer", "Beep2D": "AudioStreamPlayer2D", "Beep3D": "AudioStreamPlayer3D"}
    assert all(p["stream"] == "res://perf/beep.wav" and p["length"] == pytest.approx(1, abs=0.01)
               for p in players.values())
    assert players["Beep2D"]["bus"] == "Effects" and players["Beep3D"]["distance"] == pytest.approx(2)
    assert [p["node"] for p in r["idle"]] == ["Silent"] and r["idle_count"] == 1
    text = gdh("live", "audio", "--session", name).stdout
    assert "Master: " in text and "dB" in text and "Beep3D (AudioStreamPlayer3D): res://perf/beep.wav" in text
    assert "players that didn't play: Silent" in text


def test_audio_before_any_step(sessions):
    name = sessions("sound", fresh=True)
    r = gdh_json("live", "audio", "--session", name)["result"]
    assert r["frames"] == 0 and r["players"] == [] and all(b["peak_db"] is None for b in r["buses"])
    assert {p["node"] for p in r["idle"]} == {"Beep", "Beep2D", "Beep3D", "Silent"}
    assert "the game hasn't run yet" in gdh("live", "audio", "--session", name).stdout
