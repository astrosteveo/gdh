"""gdh measure over made-up frames, and gdh live record, measure and frames against testbed/measure/measure.tscn.

The made-up frames check each measure's definition where the answer is known exactly. The testbed's modes check
that each one finds what it's for in what Godot draws: noise that changes each frame, a camera that shakes, lines
of known width, a NaN, a dark gradient through ACES, a dissolve whose layers match or don't, and the renderer's
passes with a game's own timestamp among them.
"""
import json
import os

import numpy as np
import pytest
from PIL import Image

from conftest import TESTBED, gdh, gdh_json
from gdh import measure as m

SCENE = "res://measure/measure.tscn"


def save(path, a):
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(path)
    return path


def grey(value, w=64, h=48):
    return np.full((h, w, 3), value, dtype=np.float32)


# --- The definitions, on made-up frames -------------------------------------------------------------------------


def test_still_frames_have_no_flicker_or_shimmer(tmp_path):
    frames = [save(tmp_path / f"f{i}.png", grey(100)) for i in range(5)]
    f = m.flicker(frames)
    s = m.shimmer(frames)
    assert f["mean_change"] == 0 and f["share_changed_over_2"] == 0 and f["range_p99"] == 0
    assert s["second_diff_mean"] == 0 and s["share_over_8"] == 0


def test_smooth_change_flickers_but_does_not_shimmer(tmp_path):
    # Brightness rising 10 a frame: every pixel changes (flicker), but steadily (no second difference).
    frames = [save(tmp_path / f"f{i}.png", grey(50 + 10 * i)) for i in range(6)]
    assert m.flicker(frames)["share_changed_over_2"] == 1
    assert m.shimmer(frames)["second_diff_mean"] == 0


def test_alternating_pixels_shimmer(tmp_path):
    frames = []
    for i in range(6):
        a = grey(100)
        a[:24, :, :] = 100 + (20 if i % 2 else 0)  # the top half blinks by 20
        frames.append(save(tmp_path / f"f{i}.png", a))
    s = m.shimmer(frames)
    assert s["share_over_8"] == pytest.approx(0.5)
    assert s["second_diff_max"] == pytest.approx(40, abs=0.5)
    # Only the bottom half: steady.
    assert m.shimmer(frames, m.Region([0, 24, 64, 48]))["share_over_8"] == 0


def test_line_width_and_brightness(tmp_path):
    a = grey(10, 200, 200)
    a[:, 100:103] = 210  # a line 3 px wide, 200 above the background
    path = save(tmp_path / "line.png", a)
    found = m.lines(path, [{"a": [101, 20], "b": [101, 180], "name": "three"}])
    assert len(found) == 1 and found[0]["name"] == "three"
    # Bilinear sampling every quarter pixel: a box n px wide reads n + 0.25 at half its height.
    assert found[0]["fwhm_px"] == pytest.approx(3.25)
    assert found[0]["peak_above_bg"] == pytest.approx(200)
    # Behind the camera, or too short: skipped.
    assert m.lines(path, [{"a": [101, 20, 2], "b": [101, 180, 2]}, {"a": [101, 20], "b": [101, 40]}]) == []


def test_spots_find_points_and_their_size(tmp_path):
    a = grey(10, 160, 120)
    yy, xx = np.mgrid[0:120, 0:160]
    for (x, y, sigma) in ((40, 40, 0.62), (100, 40, 1.5), (40, 90, 3.0)):
        a += (200 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma ** 2)))[..., None]
    a[80:100, 100:140] = 200  # a block: not a point
    path = save(tmp_path / "spots.png", np.clip(a, 0, 255))
    found = sorted(m.spots(path, radius=8), key=lambda s: s["x"] * 1000 + s["y"])
    assert [(s["x"], s["y"]) for s in found] == [(40, 40), (40, 90), (100, 40)]
    # A Gaussian's width at half maximum is 2.355 sigma; its second moment gives sigma back.
    by = {(s["x"], s["y"]): s for s in found}
    assert by[(100, 40)]["fwhm_px"] == pytest.approx(2.355 * 1.5, abs=0.3)
    assert by[(40, 90)]["fwhm_px"] == pytest.approx(2.355 * 3.0, abs=0.4)
    assert by[(40, 90)]["sigma_px"] == pytest.approx(3.0, abs=0.35)
    assert by[(100, 40)]["peak_above_bg"] == pytest.approx(200, abs=2)
    out = m.spots_summary(found)
    assert out["spots"] == 3 and out["fwhm_px"]["max"] == by[(40, 90)]["fwhm_px"]


def test_dissolve_counts_doubled_and_empty(tmp_path):
    rng = np.random.default_rng(3)
    h = rng.random((40, 40))
    whole = np.zeros((40, 40, 3)); whole[5:35, 5:35] = 255
    a = np.where((h < 0.5)[..., None], whole, 0)
    b = np.where((h >= 0.5)[..., None], whole, 0)
    paths = [save(tmp_path / f"{n}.png", x) for n, x in (("a", a), ("b", b), ("whole", whole))]
    good = m.dissolve(paths[0], paths[1], [paths[2]])
    assert good["doubled_px"] == 0 and good["empty_px"] == 0 and good["region_px"] == 28 * 28
    bad = m.dissolve(paths[0], paths[0], [paths[2]])  # the same layer twice: all doubled or empty
    assert bad["doubled_px"] + bad["empty_px"] == bad["region_px"]


def test_black_hole_in_lit_area_is_a_nan_and_dark_black_is_not(tmp_path):
    a = grey(150, 120, 80)
    a[10:20, 10:20] = 0  # a hole in something lit
    a[40:70, 60:110] = 3  # a dark area...
    a[50:60, 75:95] = 0  # ...with black inside it: a shadow's black
    r = m.black(save(tmp_path / "f.png", a))
    assert r["black_px"] == 100 + 200
    assert r["nan_px"] == 100 and r["nan_shape_count"] == 1
    assert r["nan_shapes"][0]["box"] == [10, 10, 20, 20]


def test_a_crevice_that_fades_to_black_is_not_a_nan(tmp_path):
    # Crushed shading inside a lit surface: black, ringed by dark pixels, then the light. A NaN's edge is hard.
    a = grey(140, 60, 60)
    a[20:32, 20:32] = 4
    a[24:28, 24:28] = 0
    r = m.black(save(tmp_path / "f.png", a))
    assert r["black_px"] == 16 and r["nan_px"] == 0
    a[24:28, 24:28] = 140
    a[25:27, 25:27] = 0  # now a hard hole in the lit middle
    assert m.black(save(tmp_path / "g.png", a))["nan_px"] == 4


def test_a_ring_pixel_between_two_shapes_counts_for_both(tmp_path):
    # A black pixel half in the light and half in the dark is no hole (4 of its 8 neighbours lit), even when a second
    # black pixel in the dark shares two of its dark neighbours.
    a = grey(150)
    a[11:14, :] = 3
    a[10, 11:] = 3
    a[10, 10] = 0
    a[11, 12] = 0
    r = m.black(save(tmp_path / "f.png", a))
    assert r["black_px"] == 2 and r["black_shapes"] == 2 and r["nan_px"] == 0
    a[10, 11] = 150  # now 5 of 8 lit: a hole
    assert m.black(save(tmp_path / "g.png", a))["nan_px"] == 1


def test_mask_is_where_a_thing_draws_with_its_holes_filled(tmp_path):
    without = grey(10, 60, 40)
    with_ = without.copy()
    with_[10:30, 10:40] = 120
    with_[15:20, 20:25] = 10  # a crevice that matches the background behind it
    a, b = save(tmp_path / "with.png", with_), save(tmp_path / "without.png", without)
    assert m.silhouette(a, b).sum() == 20 * 30
    assert m.silhouette(a, b, fill=False).sum() == 20 * 30 - 25
    out = json.loads(gdh("measure", "mask", a, b, "--out", tmp_path / "mask.png").stdout)
    assert out["pixels"] == 600 and out["box"] == [10, 10, 40, 30]
    assert m.crush(a, m.Region(mask=tmp_path / "mask.png"))["pixels"] == 600


def test_crush_counts_pixels_at_the_floor_in_the_region(tmp_path):
    a = grey(20, 100, 50)
    a[:, :10] = 0  # crushed
    a[:, 10:20] = 4  # its last steps
    path = save(tmp_path / "f.png", a)
    r = m.crush(path)
    assert r["crushed_px"] == 500 and r["near_floor_px"] == 1000 and r["largest_shape"]["box"] == [0, 0, 10, 50]
    assert m.crush(path, m.Region([20, 0, 100, 50]))["crushed_px"] == 0


def test_components_join_diagonals_and_keep_shapes_apart():
    mask = np.zeros((6, 8), dtype=bool)
    mask[0, 0] = mask[1, 1] = mask[2, 2] = True  # a diagonal: one shape
    mask[4:6, 5:8] = True
    labels, count = m.components(mask)
    assert count == 2
    assert labels[0, 0] == labels[2, 2] != labels[5, 7]


def test_jitter_of_a_steady_and_a_blinking_light(tmp_path):
    steady, blinking = [], []
    for i in range(9):
        a = grey(0)
        a[10:20, 10:20] = 255
        steady.append(save(tmp_path / f"s{i}.png", a))
        b = a.copy()
        b[10:20, 10:15] = 255 if i % 2 else 0
        blinking.append(save(tmp_path / f"b{i}.png", b))
    assert m.jitter(steady)["jitter_percent"] == 0
    assert m.jitter(blinking)["jitter_percent"] > 20
    assert m.light_pixels(grey(255)[:1, :1], "r>150,g<90")[0, 0] == False  # noqa: E712


def test_times_take_the_nearest_rank():
    frames = [{"gpu": float(i), "cpu": 1.0, "passes": {"A": float(i) / 2}} for i in range(1, 101)]
    t = m.times({"frames": frames})
    assert t["gpu_ms"] == {"p50": 50.0, "p99": 99.0, "max": 100.0, "mean": 50.5}
    assert t["passes"]["A"]["p99"] == 49.5


def test_the_cli_reads_directories_and_saves(tmp_path):
    for i in range(3):
        save(tmp_path / f"f{i}.png", grey(100))
    out = gdh_json("measure", "flicker", tmp_path, "--save", tmp_path / "r.json")
    assert out["frames"] == 3 and json.loads((tmp_path / "r.json").read_text())["mean_change"] == 0
    assert gdh("measure", "shimmer", tmp_path / "nowhere", check=False).returncode == 1


# --- In Godot -----------------------------------------------------------------------------------------------------


def session_for(tmp_path_factory, mode, *start):
    name = f"measure-{mode}-{os.getpid()}"
    out = tmp_path_factory.mktemp(mode)
    gdh("live", "start", "--project", TESTBED, "--scene", SCENE, "--session", name, "--out", out, *start,
        "--", "--mode", mode)
    return name, out


@pytest.fixture(scope="module")
def sessions(tmp_path_factory):
    started = {}

    def get(mode, *start):
        if mode not in started:
            started[mode] = session_for(tmp_path_factory, mode, *start)
        return started[mode]

    yield get
    for name, _ in started.values():
        gdh("live", "stop", "--session", name)


def measured(session, kind, *args):
    return gdh_json("live", "measure", kind, "--session", session, *args)


def test_still_camera_does_not_flicker(sessions):
    name, _ = sessions("still", "--gpu-passes")
    r = measured(name, "flicker", "--frames", "20")
    assert r["frames"] == 20 and r["mean_change"] == 0 and r["share_changed_over_2"] == 0


def test_noise_flickers(sessions):
    name, _ = sessions("flicker")
    r = measured(name, "flicker", "--frames", "20")
    assert r["share_changed_over_2"] > 0.005


def test_a_shaken_camera_shimmers_more_than_a_smooth_one(sessions):
    smooth = measured(sessions("orbit")[0], "shimmer", "--frames", "20")
    shaken = measured(sessions("swim")[0], "shimmer", "--frames", "20")
    assert shaken["second_diff_mean"] > 2 * smooth["second_diff_mean"]
    assert shaken["share_over_8"] > smooth["share_over_8"]


def test_line_widths(sessions):
    name, _ = sessions("lines")
    widths = []
    for x in (200, 401, 602.5, 800):
        r = measured(name, "line", "--frames", "1", "--from", f"{x},120", "--to", f"{x},580")
        widths.append((r["fwhm_px_median"], r["peak_above_bg_median"]))
    assert [w for w, _ in widths] == pytest.approx([1.25, 3.25, 6.25, 1.25], abs=0.3)
    assert widths[0][1] > 250 and 100 < widths[3][1] < 160  # the grey line is half as bright


def test_a_nan_is_found_and_a_dark_black_is_not(sessions):
    name, _ = sessions("nan")
    r = gdh("live", "measure", "black", "--frames", "1", "--fail", "--json", "--session", name, check=False)
    assert r.returncode == 1
    each = json.loads(r.stdout)["each"][0]
    assert each["nan_shape_count"] == 1 and each["nan_px"] > 2000
    assert each["black_px"] > each["nan_px"]  # the black patch in its dark frame isn't counted
    still = measured(sessions("still", "--gpu-passes")[0], "black", "--frames", "1")
    assert still["totals"]["nan_px"] == 0


def test_aces_crushes_the_darkest_values(sessions):
    name, _ = sessions("crush")
    from_zero = measured(name, "crush", "--frames", "1", "--box", "0,8,1280,352")
    lifted = measured(name, "crush", "--frames", "1", "--box", "0,368,1280,712")
    assert from_zero["totals"]["crushed_px"] > 1000
    assert lifted["totals"]["crushed_px"] == 0 and lifted["crush_free"]


def test_dissolve_layers_that_match_and_that_do_not(sessions, tmp_path):
    name, _ = sessions("dissolve")

    def layer(which, label):
        gdh("live", "eval", f"show_layer('{which}', 0.4)", "--session", name)
        gdh("live", "step", "2", "--session", name)
        shots = gdh_json("live", "shot", "--label", label, "--session", name)["result"]["shots"]
        return shots["normal"]

    a, b, other, whole = (layer(w, w) for w in ("a", "b", "b_unmatched", "whole"))
    good = json.loads(gdh("measure", "dissolve", a, b, "--region", whole).stdout)
    assert good["doubled_px"] == 0 and good["empty_px"] == 0
    assert good["b_share"] == pytest.approx(0.6, abs=0.02)
    bad = json.loads(gdh("measure", "dissolve", a, other, "--region", whole).stdout)
    assert bad["doubled_px"] > 0.15 * bad["region_px"] and bad["empty_px"] > 0.15 * bad["region_px"]


def test_frame_times_and_passes(sessions):
    name, _ = sessions("still", "--gpu-passes")
    gdh("live", "frames", "--clear", "--session", name)
    gdh("live", "step", "30", "--session", name)
    t = gdh_json("live", "frames", "--session", name)
    # Timestamps come back a frame or two late, and not every frame: each measured frame is counted once.
    assert t["game_frames"] == 30 and 10 <= t["frames"] <= 30
    assert t["gpu_ms"]["p50"] > 0 and t["gpu_ms"]["max"] >= t["gpu_ms"]["p99"]
    assert "Render Opaque Pass" in t["passes"] and "Testbed Effect" in t["passes"]
    assert "Render 3D Scene" in t["groups"]


def test_frame_times_without_passes(sessions):
    name, _ = sessions("orbit")
    gdh("live", "frames", "--clear", "--session", name)
    gdh("live", "step", "10", "--session", name)
    t = gdh_json("live", "frames", "--session", name)
    assert t["game_frames"] == 10 and t["frames"] >= 3 and t["gpu_ms"]["p50"] > 0
    # The game's own timestamps come through; the renderer's need --gpu-passes.
    assert "Testbed Effect" in t["passes"] and "Render Opaque Pass" not in t["passes"] and "groups" not in t


def test_frame_times_hold_while_every_frame_is_shot(sessions, tmp_path):
    # Reading every frame back for a shot stalls the timestamps; the record must not fill with a repeated value.
    name, _ = sessions("still", "--gpu-passes")
    gdh("live", "frames", "--clear", "--session", name)
    gdh("live", "record", "12", "--out", tmp_path / "shots", "--session", name)
    record = gdh_json("live", "frames", "--session", name)
    assert record["game_frames"] == 12 and record["frames"] <= 12


def test_record_saves_every_frame(sessions, tmp_path):
    name, _ = sessions("orbit")
    gdh("live", "record", "4", "--out", tmp_path / "rec", "--session", name)
    assert sorted(p.name for p in (tmp_path / "rec").glob("*.png")) == [f"frame-{i:04d}.png" for i in range(4)]
