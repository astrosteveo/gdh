"""Audio you can see: gdh measure audio on made-up sound, and audio.png beside a movie of testbed/audio/sync.tscn (a
ball landing every 40 frames, Thud playing as it lands, a quiet Hum all along; "--loud" makes Thud clip). And gdh live
soundsync on the same scene, with Thud `delay` frames after the landing."""
import json
import os
import wave

import numpy as np
import pytest

from conftest import TESTBED, gdh, gdh_json
from gdh.audio import analyse, read_samples
from gdh.soundsync import pair, rising

RATE = 44100


def write_wav(path, samples):
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())


def made_up(path):
    """0.6 s of a 440 Hz tone, 0.8 s of silence, 0.6 s clipping hard."""
    t = np.arange(int(RATE * 0.6)) / RATE
    write_wav(path, np.concatenate([0.4 * np.sin(2 * np.pi * 440 * t), np.zeros(int(RATE * 0.8)),
                                    np.clip(1.6 * np.sin(2 * np.pi * 220 * t), -1, 1)]))


def test_measure_audio_flags_clipping_and_silence(tmp_path):
    made_up(tmp_path / "s.wav")
    reply = gdh_json("measure", "audio", tmp_path / "s.wav", "--fps", "60")
    assert reply["seconds"] == pytest.approx(2.0, abs=0.01) and reply["sample_rate"] == RATE
    assert reply["peak_db"] == pytest.approx(0, abs=0.01) and reply["clip_runs"] > 50
    first = reply["clipping"][0]["from"]
    assert first["seconds"] == pytest.approx(1.4, abs=0.01) and first["frame"] == 84
    assert len(reply["silences"]) == 1
    quiet = reply["silences"][0]
    assert quiet["from"]["seconds"] == pytest.approx(0.6, abs=0.03) and quiet["to"]["seconds"] == pytest.approx(1.4, abs=0.03)
    assert (tmp_path / "s-audio.png").exists() and reply["image"] == str(tmp_path / "s-audio.png")
    proc = gdh("measure", "audio", tmp_path / "s.wav", "--fail", check=False)
    assert proc.returncode == 1 and "clipping: " in proc.stdout and "silent from 0.6 s to 1.4 s" in proc.stdout


def test_reading_and_analysing_sound(tmp_path):
    write_wav(tmp_path / "quiet.wav", 0.1 * np.sin(2 * np.pi * 1000 * np.arange(RATE) / RATE))
    samples, rate = read_samples(tmp_path / "quiet.wav")
    assert rate == RATE and len(samples) == RATE and abs(samples).max() == pytest.approx(0.1, abs=0.001)
    facts = analyse(samples, rate, 30)
    assert facts["clipping"] == [] and facts["silences"] == [] and facts["peak_db"] == pytest.approx(-20, abs=0.1)
    proc = gdh("measure", "audio", tmp_path / "nope.wav", check=False)
    assert proc.returncode == 1 and "No such file" in proc.stderr


def test_a_movie_draws_its_sound(tmp_path):
    out = tmp_path / "quiet"
    gdh("movie", "--project", TESTBED, "--scene", "res://audio/sync.tscn", "--out", out, "--frames", "90", "--fps", "60")
    report = json.loads((out / "report.json").read_text())
    assert (out / "audio.png").exists() and report["sound"]["image"] == str(out / "audio.png")
    assert report["sound"]["clipping"] == [] and report["sound"]["peak_db"] < 0
    loud = tmp_path / "loud"
    proc = gdh("movie", "--project", TESTBED, "--scene", "res://audio/sync.tscn", "--out", loud, "--frames", "90",
               "--fps", "60", "--", "--loud")
    assert "clipping" in proc.stdout
    report = json.loads((loud / "report.json").read_text())
    # Thud plays as the ball lands, on frame 40: the clipping starts there.
    assert report["sound"]["clipping"][0]["from"]["frame"] == pytest.approx(40, abs=2)
    gdh("movie", "--project", TESTBED, "--scene", "res://audio/sync.tscn", "--out", tmp_path / "none", "--frames", "30",
        "--no-sheet")
    assert not (tmp_path / "none" / "audio.png").exists()


# --- Sound in sync --------------------------------------------------------------------------------------------------

SESSION = f"test-audio-{os.getpid()}"


@pytest.fixture(scope="module")
def sync(tmp_path_factory, display):
    out = tmp_path_factory.mktemp("sync")
    gdh("live", "start", "--project", TESTBED, "--scene", "res://audio/sync.tscn", "--session", SESSION, "--out", out)
    yield out
    gdh("live", "stop", "--session", SESSION)


def test_soundsync_pairs_each_landing_with_its_thud(sync):
    gdh_json("live", "eval", "set('delay', 0)", "--session", SESSION)
    reply = gdh_json("live", "soundsync", "130", "--event", "land", "--session", SESSION)
    pairs = reply["pairs"]
    # The ball lands every 40 frames, and Thud plays on the frame it lands: offset 0, each time.
    assert len(pairs) == 3 and [p["offset"] for p in pairs] == [0, 0, 0]
    assert [b["event"] - a["event"] for a, b in zip(pairs, pairs[1:])] == [40, 40]
    assert all(p["player"].endswith("/Thud") for p in pairs) and reply["out_of_sync"] == 0
    # Hum was playing before the run: it didn't start in it.
    assert reply["unpaired_sounds"] == []


def test_soundsync_flags_a_late_sound(sync):
    gdh_json("live", "eval", "set('delay', 6)", "--session", SESSION)
    try:
        reply = gdh_json("live", "soundsync", "130", "--when", "landed", "--session", SESSION)
        proc = gdh("live", "soundsync", "90", "--when", "landed", "--fail", "--session", SESSION, check=False)
    finally:
        gdh_json("live", "eval", "set('delay', 0)", "--session", SESSION)
    assert [p["offset"] for p in reply["pairs"] if p["offset"] is not None][:2] == [6, 6]
    assert reply["out_of_sync"] >= 2
    assert proc.returncode == 1 and "Thud 6 frames (100 ms) late  <- out of sync" in proc.stdout


def test_soundsync_listens_to_the_players_asked_for(sync):
    reply = gdh_json("live", "soundsync", "45", "--event", "land", "--player", "Hum", "--session", SESSION)
    assert reply["no_sound"] == 1 and reply["pairs"][0]["sound"] is None
    for args, said in (([], "give one"), (["--event", "land", "--when", "landed"], "give one"),
                       (["--event", "land", "--player", "Nope"], "No node at Nope.")):
        proc = gdh("live", "soundsync", "5", *args, "--session", SESSION, check=False)
        assert proc.returncode == 1 and said in proc.stderr, (args, proc.stderr)


def test_pairing_rules():
    assert rising([1, 2, 3, 4, 5], [False, True, True, False, True]) == [2, 5]
    pairs, left = pair([10, 50], [(12, "a"), (9, "b"), (90, "c")], 5)
    assert [(p["event"], p["sound"], p["offset"]) for p in pairs] == [(10, 9, -1), (50, None, None)]
    assert left == [(12, "a"), (90, "c")]
