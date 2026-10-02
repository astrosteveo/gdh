"""gdh movie against testbed/movie/movie.tscn (a spinning cube, a 440 Hz tone, and a HUD, a dialog or a popup picked
with the game's arguments), the override.cfg written for a run, the covered-screen check and the contact sheets."""
import json
import os
import shutil
import signal
import subprocess
import sys
import textwrap

import pytest

from conftest import TESTBED, gdh, gdh_json
from gdh.covered import covered_findings
from gdh.movie import audio_facts, video_facts

SCENE = "res://movie/movie.tscn"
SESSION = f"test-movie-{os.getpid()}"

if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
    pytest.skip("needs ffmpeg and ffprobe on PATH", allow_module_level=True)


def movie(out, *args, game=(), check=True):
    return gdh("movie", "--project", TESTBED, "--scene", SCENE, "--out", out, *args, *(["--", *game] if game else []),
               check=check)


def leftovers(out):
    return [p for p in [TESTBED / "override.cfg", *out.glob(".gdh-movie-*")] if p.exists()]


def test_a_movie_is_the_size_and_length_asked_for_with_its_sound(tmp_path):
    # 1600x900 is neither the project's size (1152x648) nor gdh's default: Movie Maker would record 1152x648 alone.
    movie(tmp_path, "--resolution", "1600x900", "--fps", "30", "--frames", "45", game=["--hud"])
    mp4 = tmp_path / "movie.mp4"
    facts = video_facts(mp4)
    assert (facts["width"], facts["height"], facts["frames"], facts["codec"]) == (1600, 900, 45, "h264")
    audio = audio_facts(mp4)
    assert audio["codec"] == "aac" and not audio["silent"]
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["problems"] == [] and report["findings"] == []  # the HUD covers nothing
    assert report["window_size"] == [1600, 900] and report["seconds"] == 1.5
    assert (tmp_path / "sheet.png").exists() and not (tmp_path / "movie.avi").exists()
    assert leftovers(tmp_path) == []


def test_seconds_quality_and_keep_avi(tmp_path):
    sizes = {}
    for quality in ("1.0", "0.5"):
        out = tmp_path / quality
        # (gdh stops if the quality Godot read isn't the one asked for)
        movie(out, "--resolution", "640x360", "--fps", "20", "--seconds", "1.5", "--quality", quality, "--keep-avi",
              "--no-sheet")
        assert video_facts(out / "movie.mp4")["frames"] == 30
        assert video_facts(out / "movie.avi")["codec"] == "mjpeg"
        assert not (out / "sheet.png").exists()
        sizes[quality] = (out / "movie.avi").stat().st_size
    assert sizes["0.5"] < sizes["1.0"]


def test_an_override_cfg_gdh_did_not_write_is_refused_and_left_alone(tmp_path):
    theirs = TESTBED / "override.cfg"
    theirs.write_text("[application]\n\nconfig/name=\"theirs\"\n")
    try:
        proc = movie(tmp_path, "--frames", "5", check=False)
        assert proc.returncode == 1 and "never changes a file it didn't write" in proc.stderr
        assert theirs.read_text() == "[application]\n\nconfig/name=\"theirs\"\n"
    finally:
        theirs.unlink()


def test_an_override_cfg_left_by_a_killed_gdh_is_removed(tmp_path):
    dead = subprocess.Popen(["true"])
    dead.wait()
    (TESTBED / "override.cfg").write_text(f"; gdh: written for one run by process {dead.pid}, and removed when it "
                                          f"ends. Delete it if no gdh is running.\n[display]\n")
    movie(tmp_path, "--frames", "5", "--resolution", "640x360", "--no-sheet")
    assert leftovers(tmp_path) == []


def test_the_override_is_removed_on_sigterm(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    script = textwrap.dedent(f"""
        import os, signal, time
        from gdh.override import project_override
        with project_override({str(project)!r}, {{"display/window/size/window_width_override": 640}}):
            assert os.path.exists({str(project / "override.cfg")!r})
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(5)
    """)
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 128 + signal.SIGTERM, proc.stderr
    assert not (project / "override.cfg").exists()


def test_a_game_that_quits_by_itself_keeps_its_movie_and_says_so(tmp_path):
    proc = movie(tmp_path, "--resolution", "640x360", "--frames", "30", "--no-sheet", game=["--quit-at", "10"],
                 check=False)
    assert proc.returncode == 1 and "the game quit by itself after 10 of the 30 frames" in proc.stderr
    assert 9 <= video_facts(tmp_path / "movie.mp4")["frames"] <= 11


def test_odd_sizes_are_refused(tmp_path):
    proc = movie(tmp_path, "--resolution", "641x360", "--frames", "5", check=False)
    assert proc.returncode == 1 and "even width and height" in proc.stderr


@pytest.mark.parametrize("game, node", [(["--hud", "--dialog"], "Modal/Dialog"), (["--hud", "--popup"], "Popup")])
def test_a_panel_over_the_middle_for_most_of_a_movie_is_flagged(tmp_path, game, node):
    movie(tmp_path, "--resolution", "640x360", "--fps", "30", "--frames", "30", game=game)
    findings = json.loads((tmp_path / "report.json").read_text())["findings"]
    assert [(f["probe"], f["node"]) for f in findings] == [("covered", node)]
    assert findings[0]["data"]["time_share"] == 1.0 and 0.3 < findings[0]["data"]["screen_share"] < 0.4
    assert (tmp_path / findings[0]["crop"]).exists()


def test_a_panel_up_for_the_end_only_is_not_flagged(tmp_path):
    movie(tmp_path, "--resolution", "640x360", "--fps", "30", "--frames", "40", game=["--hud", "--dialog-at", "30"])
    assert json.loads((tmp_path / "report.json").read_text())["findings"] == []


def test_covered_needs_most_of_the_samples():
    panel = {"node": "Modal/Dialog", "class": "Panel", "share": 0.4, "rect": [0, 0, 10, 10]}
    assert covered_findings([[panel], [panel], [], []])[0]["data"]["time_share"] == 0.5
    assert covered_findings([[panel], [], [], []]) == []
    assert covered_findings([]) == []


def test_record_flags_a_covering_panel_and_makes_a_sheet(tmp_path):
    gdh("live", "start", "--project", TESTBED, "--scene", SCENE, "--session", SESSION, "--out", tmp_path / "live",
        "--resolution", "640x360", "--", "--hud", "--dialog")
    try:
        out = gdh_json("live", "record", "24", "--out", tmp_path / "frames", "--session", SESSION)
    finally:
        gdh("live", "stop", "--session", SESSION)
    assert len(out["frames"]) == 24
    assert out["sheet"] == str(tmp_path / "frames-sheet.png") and (tmp_path / "frames-sheet.png").exists()
    assert [f["node"] for f in out["findings"]] == ["Modal/Dialog"]
    assert sorted(p.name for p in (tmp_path / "frames").iterdir()) == [f"frame-{i:04d}.png" for i in range(24)]


def test_measure_sheet_from_frames_and_from_a_video(tmp_path):
    movie(tmp_path / "m", "--resolution", "640x360", "--frames", "20", "--no-sheet")
    from PIL import Image
    sheet = gdh_json("measure", "sheet", tmp_path / "m/movie.mp4", "--out", tmp_path / "sheet.png")["sheet"]
    assert Image.open(sheet).width <= 1280
