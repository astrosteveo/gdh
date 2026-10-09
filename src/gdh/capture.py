"""gdh capture and gdh import."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

from gdh import measure as m
from gdh.display import open_display, parse_resolution
from gdh.godot import HARNESS, GdhError, alert_shims, build_csharp, godot_cmd, godot_env, kill_groups, size_mismatch
from gdh.images import crop_findings, save_tiles
from gdh.imports import describe_missing, ensure_imported, missing_resources

CAPTURE_SCRIPT = HARNESS / "capture.gd"


def capture_one(project, scene, out_dir, args, shims, baseline=None):
    out_dir.mkdir(parents=True, exist_ok=True)
    # Clear outputs from earlier runs so nothing stale is mistaken for new.
    for old in [*out_dir.glob("*.png"), out_dir / "report.json"]:
        old.unlink(missing_ok=True)
    shutil.rmtree(out_dir / "crops", ignore_errors=True)
    user_args = ["--scene", scene, "--out", str(out_dir), "--warmup", str(args.warmup)]
    if args.modes:
        user_args += ["--modes", args.modes]
    if getattr(args, "locale", None):
        user_args += ["--locale", args.locale]
    cmd = godot_cmd(project, args.resolution, ["--script", str(CAPTURE_SCRIPT)], args.game_args)
    display = open_display(args.display, args.resolution, out_dir / "display.log")
    try:
        with open(out_dir / "godot.log", "w") as log:
            proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    env=godot_env(shims, display.name, user_args), start_new_session=True)
            try:
                code = proc.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                code = "timeout"
    finally:
        # Godot may have left children, so stop its whole group, then the display.
        if "proc" in locals():
            kill_groups(proc.pid)
            proc.wait()
        display.stop()
    report_path = out_dir / "report.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    baseline_ok = True
    mismatch = size_mismatch(report.get("window_size"), args.resolution) if report else None
    missing = missing_resources(report.get("errors", []))
    if report:
        report["display"] = display.kind
        if mismatch:
            report["size_mismatch"] = mismatch
        if missing:
            report["missing_resources"] = missing
        images = {p.stem: p for p in out_dir.glob("*.png")}
        crop_findings(report.get("findings", []), report.get("image_size"), images, out_dir / "crops", out_dir)
        if args.tiles and "normal" in images:
            save_tiles(images["normal"], out_dir / "crops")
        if baseline:
            baseline_ok = check_baseline(images, baseline, args, out_dir, report, code == 0 and not mismatch)
        report_path.write_text(json.dumps(report, indent=2))
    pngs = sorted(p.name for p in out_dir.glob("*.png"))
    errors = sum(e.get("count", 1) for e in report.get("errors", []))
    findings = report.get("findings", [])
    warnings = sum(f["severity"] == "warning" for f in findings)
    print(f"{scene}: exit={code} adapter={report.get('adapter', '?')} display={display.kind} errors={errors} "
          f"warnings={warnings} findings={len(findings)} images={','.join(pngs) or 'none'} -> {out_dir}")
    for f in findings:
        crop = f"  [{f['crop']}]" if f.get("crop") else ""
        print(f"  {f['severity']}: {f['probe']} {f['node']}: {f['message']}{crop}")
    if missing:
        print(f"  {describe_missing(missing)}")
    if report.get("baseline"):
        print(f"  {baseline_brief(report['baseline'])}")
    if mismatch:
        print(f"  gdh: {mismatch}", file=sys.stderr)
    return code == 0 and not mismatch and baseline_ok


def check_baseline(images, baseline, args, out_dir, report, write):
    """Compare each saved view with its baseline, the PNG of the same name in `baseline` (measure.diff). A view with
    pixels changed by more than --threshold is a `baseline` finding naming the view and where it changed, with a
    heatmap and a crop of its largest change side by side (baseline, now, the difference amplified) in crops/: a
    warning when more than --tolerance percent of its pixels changed, info within it. A view with no baseline, or of
    another size, is a warning. With --update-baseline nothing fails, and the views are copied into the baseline when
    `write` (the capture went through). The numbers go in the report under "baseline". Returns whether every view is
    within the tolerance."""
    baseline = Path(baseline)
    findings = report.setdefault("findings", [])
    size = report.get("image_size")
    severity = "info" if args.update_baseline else "warning"
    views = {}
    for view, path in sorted(images.items()):
        old = baseline / path.name
        finding = {"probe": "baseline", "node": path.name, "view": view}
        if not old.exists():
            views[view] = {"missing": True}
            if not args.update_baseline:
                findings.append({**finding, "severity": "warning",
                                 "message": f"no baseline: {old} doesn't exist (--update-baseline writes it)"})
            continue
        try:
            d = m.diff(old, path, args.threshold, out=out_dir / "crops", name=f"baseline-{view}-",
                       labels=("baseline", "now"))
        except m.MeasureError as e:
            views[view] = {"error": str(e)}
            findings.append({**finding, "severity": severity, "message": str(e)})
            continue
        views[view] = {k: d[k] for k in ("max_diff", "mean_diff", "changed_px", "changed_share", "box", "regions")}
        if not d["changed_px"]:
            continue
        views[view]["over"] = m.diff_over(d, args.tolerance)
        big = d["regions"][0]
        message = (f"{100 * d['changed_px'] / d['pixels']:.3g}% of pixels ({d['changed_px']}) changed by more than "
                   f"{args.threshold:g} from the baseline, max {d['max_diff']}, in the box {box_text(d['box'])}")
        if d["region_count"] > 1:
            message += f"; the largest change, {big['px']} px, in the box {box_text(big['box'])}"
        x0, y0, x1, y1 = big["box"]
        sx, sy = (size[0] / d["size"][0], size[1] / d["size"][1]) if size else (1, 1)
        findings.append({**finding, "severity": severity if views[view]["over"] else "info", "message": message,
                         "data": {**views[view], "heatmap": str(Path(d["heatmap"]).relative_to(out_dir))},
                         "screen_rect": [round(x0 * sx, 1), round(y0 * sy, 1), round((x1 - x0) * sx, 1),
                                         round((y1 - y0) * sy, 1)],
                         "crop": str(Path(d["crop"]).relative_to(out_dir))})
    passed = args.update_baseline or not any(v.get("missing") or "error" in v or v.get("over") for v in views.values())
    if args.update_baseline and write:
        baseline.mkdir(parents=True, exist_ok=True)
        for path in images.values():
            shutil.copyfile(path, baseline / path.name)
    report["baseline"] = {"dir": str(baseline), "threshold": args.threshold, "tolerance": args.tolerance,
                          "views": views, "passed": bool(passed), "updated": bool(args.update_baseline and write)}
    return bool(passed)


def baseline_brief(b):
    views = b["views"]
    if b["updated"]:
        return f"baseline: wrote {len(views)} views to {b['dir']}"
    changed = [v for v, r in views.items() if r.get("changed_px") or "error" in r]
    missing = [v for v, r in views.items() if r.get("missing")]
    parts = [f"changed: {', '.join(changed)}"] * bool(changed) + [f"no baseline: {', '.join(missing)}"] * bool(missing)
    return (f"baseline {'passed' if b['passed'] else 'FAILED'} (tolerance {b['tolerance']:g}%), {len(views)} views "
            f"against {b['dir']}: {'; '.join(parts) or 'none changed'}")


def box_text(box):
    return ",".join(str(v) for v in box)


def scene_out_name(scene):
    return scene.removeprefix("res://").removesuffix(".tscn").removesuffix(".scn").replace("/", "__")


def cmd_capture(args):
    project = Path(args.project).resolve()
    out_root = Path(args.out).resolve()
    parse_resolution(args.resolution)
    baseline_root = Path(args.baseline).resolve() if args.baseline else None
    if args.update_baseline and not baseline_root:
        raise GdhError("--update-baseline needs --baseline DIR: where the baseline goes.")
    if baseline_root and not args.update_baseline and not baseline_root.is_dir():
        raise GdhError(f"No baseline at {baseline_root}: capture with --update-baseline to write one.")
    if not args.no_build:
        build_csharp(project)
    if not args.no_import:
        ensure_imported(project)
    ok = True
    with alert_shims() as shims:
        for scene in args.scene:
            out_dir = out_root if len(args.scene) == 1 else out_root / scene_out_name(scene)
            baseline = baseline_root
            if baseline and len(args.scene) > 1:
                baseline = baseline_root / scene_out_name(scene)
            ok &= capture_one(project, scene, out_dir, args, shims, baseline)
    return 0 if ok else 1
