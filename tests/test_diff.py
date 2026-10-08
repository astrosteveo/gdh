"""gdh measure diff over made-up frames, and gdh capture --baseline against testbed/smoke/smoke.tscn.

The made-up frames check the numbers where the answer is known exactly: what changed, by how much and where, and
that the heatmap and crop show it. The capture checks a scene against its own baseline, unchanged and then with one
object's colour changed.
"""
import json
import shutil

import numpy as np
from PIL import Image

from conftest import TESTBED, gdh, gdh_json
from gdh import measure as m


def save(path, a):
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(path)
    return path


def grey(value, w=320, h=200):
    return np.full((h, w, 3), value, dtype=np.float32)


def changed_frames(tmp_path):
    """A grey frame, and the same with a 40x30 block 60 brighter at (100, 50), one pixel 30 brighter at (290, 180),
    and one pixel a level off (under the threshold)."""
    a = grey(100)
    b = a.copy()
    b[50:80, 100:140] = 160
    b[180, 290] = 130
    b[10, 10, 2] = 101
    return save(tmp_path / "a.png", a), save(tmp_path / "b.png", b)


# --- measure diff ----------------------------------------------------------------------------------------------------


def test_identical_frames_differ_by_nothing(tmp_path):
    a = save(tmp_path / "a.png", grey(100))
    b = save(tmp_path / "b.png", grey(100))
    r = m.diff(a, b, out=tmp_path / "out")
    assert r["identical"] and r["max_diff"] == 0 and r["mean_diff"] == 0
    assert r["changed_px"] == 0 and r["changed_share"] == 0 and r["box"] is None and r["regions"] == []
    assert not (tmp_path / "out").exists()  # nothing changed: no heatmap or crop
    assert not m.diff_over(r)


def test_a_known_change_reports_its_box_and_share(tmp_path):
    a, b = changed_frames(tmp_path)
    r = m.diff(a, b)
    assert not r["identical"] and r["max_diff"] == 60
    assert r["changed_px"] == 40 * 30 + 1  # the pixel a level off stays under the threshold
    assert r["changed_share"] == round((40 * 30 + 1) / (320 * 200), 6)
    assert r["box"] == [100, 50, 291, 181]
    assert r["region_count"] == 2
    assert r["regions"][0] == {"px": 1200, "box": [100, 50, 140, 80], "max_diff": 60}
    assert r["regions"][1] == {"px": 1, "box": [290, 180, 291, 181], "max_diff": 30}
    assert r["mean_diff"] == round((1200 * 60 + 30 + 1) / (320 * 200), 4)
    # A higher threshold leaves only the block; a box round the single pixel only that.
    assert m.diff(a, b, threshold=40)["box"] == [100, 50, 140, 80]
    assert m.diff(a, b, region=m.Region([200, 100, 320, 200]))["changed_px"] == 1
    assert m.diff_over(r) and not m.diff_over(r, tolerance=2)


def test_the_heatmap_and_crop_show_the_change(tmp_path):
    a, b = changed_frames(tmp_path)
    r = m.diff(a, b, out=tmp_path / "out")
    heat = np.asarray(Image.open(r["heatmap"]).convert("RGB")).astype(int)
    assert heat.shape == (200, 320, 3)
    inside, outside = heat[55:75, 105:135], heat[120:160, 20:80]
    assert (inside[..., 0] == 255).all() and (inside[..., 1] > 150).all()  # hot: yellow to white
    assert (outside == outside[0, 0]).all() and outside[0, 0, 0] == outside[0, 0, 2] < 40  # dim grey
    assert heat[180, 290, 0] == 255 and heat[180, 290, 2] < 100  # the single pixel shows too
    assert heat[10, 10, 2] > heat[10, 10, 0]  # the change under the threshold, in blue
    assert tuple(heat[48, 120]) == (0, 220, 255)  # the region's outline, 2 px outside it
    crop = Image.open(r["crop"]).convert("RGB")
    third = (crop.width - 16) // 3
    panels = [np.asarray(crop.crop((4 + i * (third + 4), 22, 4 + i * (third + 4) + third, crop.height - 4))).astype(int)
              for i in range(3)]
    assert 1000 < crop.width < 1300  # three panels of about 400 px each
    assert not np.array_equal(panels[0], panels[1])
    # The difference panel, amplified: the block's change of 60 reads 255.
    assert panels[2].max() == 255 and (panels[2][panels[2].shape[0] // 2, panels[2].shape[1] // 2] == 255).all()
    assert (panels[2][2, 2] == 0).all()


def test_a_heatmap_of_a_large_frame_keeps_a_single_pixel(tmp_path):
    a = grey(50, 3000, 1000)
    b = a.copy()
    b[501, 1501] = 80
    r = m.diff(save(tmp_path / "a.png", a), save(tmp_path / "b.png", b), out=tmp_path)
    heat = np.asarray(Image.open(r["heatmap"]).convert("RGB")).astype(int)
    assert heat.shape == (334, 1000, 3)  # shrunk by 3
    assert heat[167, 500, 0] == 255 and r["box"] == [1501, 501, 1502, 502]


def test_directories_compare_by_name(tmp_path):
    a, b = changed_frames(tmp_path)
    for d in ("before", "after"):
        (tmp_path / d).mkdir()
    shutil.copy(a, tmp_path / "before" / "same.png")
    shutil.copy(a, tmp_path / "after" / "same.png")
    shutil.copy(a, tmp_path / "before" / "moved.png")
    shutil.copy(b, tmp_path / "after" / "moved.png")
    shutil.copy(a, tmp_path / "before" / "gone.png")
    save(tmp_path / "after" / "small.png", grey(100, 32, 32))
    save(tmp_path / "before" / "small.png", grey(100, 64, 32))
    r = m.diff_paths(tmp_path / "before", tmp_path / "after", out=tmp_path / "out")
    assert r["compared"] == 3 and r["changed"] == ["moved.png", "small.png"]
    assert r["only_in_a"] == ["gone.png"] and r["only_in_b"] == []
    each = {e["b"].rsplit("/", 1)[1]: e for e in r["each"]}
    assert each["same.png"]["identical"] and "different sizes" in each["small.png"]["error"]
    assert each["moved.png"]["box"] == [100, 50, 291, 181]
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["moved-crop.png", "moved-heatmap.png"]


def test_the_cli_prints_the_change_and_fails_over_the_tolerance(tmp_path):
    a, b = changed_frames(tmp_path)
    out = gdh("measure", "diff", a, b, "--out", tmp_path / "out").stdout
    assert "of pixels (1201) changed by more than 2, max 60" in out and "in the box 100,50,291,181" in out
    assert "the largest, 1200 px (max 60), in the box 100,50,140,80" in out
    assert (tmp_path / "out" / "heatmap.png").exists() and (tmp_path / "out" / "crop.png").exists()
    r = gdh_json("measure", "diff", a, b, "--save", tmp_path / "r.json")
    assert r["changed_px"] == 1201 and json.loads((tmp_path / "r.json").read_text())["box"] == r["box"]
    # --fail: exit 1 past the tolerance (0% by default); the change is 1.9% of the frame.
    assert gdh("measure", "diff", a, b, "--fail", check=False).returncode == 1
    assert gdh("measure", "diff", a, b, "--fail", "--tolerance", "2").returncode == 0
    assert gdh("measure", "diff", a, a, "--fail").stdout.strip().endswith("identical")
    small = save(tmp_path / "small.png", grey(100, 32, 32))
    wrong = gdh("measure", "diff", a, small, check=False)
    assert wrong.returncode == 1 and "different sizes" in wrong.stderr


# --- capture --baseline ----------------------------------------------------------------------------------------------


def capture(project, out, baseline, *more):
    return gdh("capture", "--project", project, "--scene", "res://smoke/smoke.tscn", "--out", out,
               "--baseline", baseline, *more, check=False)


def test_capture_compares_each_view_with_its_baseline(tmp_path):
    project = tmp_path / "testbed"
    shutil.copytree(TESTBED, project, ignore=shutil.ignore_patterns(".godot"))
    gdh("import", "--project", project)
    baseline = tmp_path / "baseline"
    missing = capture(project, tmp_path / "none", baseline)
    assert missing.returncode == 1 and "--update-baseline" in missing.stderr

    wrote = capture(project, tmp_path / "first", baseline, "--update-baseline")
    assert wrote.returncode == 0 and "baseline: wrote 6 views" in wrote.stdout
    views = sorted(p.name for p in (tmp_path / "first").glob("*.png"))
    assert sorted(p.name for p in baseline.glob("*.png")) == views and len(views) == 6
    assert all(np.array_equal(np.asarray(Image.open(baseline / v)), np.asarray(Image.open(tmp_path / "first" / v)))
               for v in views)

    same = capture(project, tmp_path / "same", baseline)
    assert same.returncode == 0 and "baseline passed" in same.stdout and "none changed" in same.stdout

    # The box, on the left of the frame, turns from red to blue.
    scene = project / "smoke" / "smoke.tscn"
    scene.write_text(scene.read_text().replace("Color(0.9, 0.3, 0.2, 1)", "Color(0.2, 0.3, 0.9, 1)"))
    changed = capture(project, tmp_path / "changed", baseline)
    assert changed.returncode == 1 and "baseline FAILED" in changed.stdout
    report = json.loads((tmp_path / "changed" / "report.json").read_text())
    found = {f["node"]: f for f in report["findings"] if f["probe"] == "baseline"}
    assert {"normal.png", "unshaded.png"} <= set(found)
    assert "lighting" not in {f["view"] for f in found.values()}  # its light is the same: steadier
    normal = found["normal.png"]
    x0, y0, x1, y1 = normal["data"]["box"]
    assert normal["severity"] == "warning" and f"in the box {x0},{y0},{x1},{y1}" in normal["message"]
    assert x1 < 640 and 0 < x1 - x0 < 300 and 0 < y1 - y0 < 300  # the box, not the sphere on the right
    assert "warning: baseline normal.png: " in changed.stdout and normal["crop"] in changed.stdout
    assert (tmp_path / "changed" / normal["crop"]).exists()
    assert (tmp_path / "changed" / normal["data"]["heatmap"]).exists()
    assert report["baseline"]["views"]["normal"]["over"] and not report["baseline"]["passed"]

    # Within the tolerance it passes, the changed views listed as info.
    tolerated = capture(project, tmp_path / "tolerated", baseline, "--tolerance", "5")
    assert tolerated.returncode == 0 and "info: baseline normal.png" in tolerated.stdout

    accepted = capture(project, tmp_path / "accepted", baseline, "--update-baseline")
    assert accepted.returncode == 0 and "baseline: wrote 6 views" in accepted.stdout
    assert capture(project, tmp_path / "again", baseline).returncode == 0
