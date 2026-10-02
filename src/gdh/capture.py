"""gdh capture and gdh import."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

from gdh.display import open_display, parse_resolution
from gdh.godot import HARNESS, alert_shims, build_csharp, godot_cmd, godot_env, kill_groups, size_mismatch
from gdh.images import crop_findings, save_tiles
from gdh.imports import describe_missing, ensure_imported, missing_resources

CAPTURE_SCRIPT = HARNESS / "capture.gd"


def capture_one(project, scene, out_dir, args, shims):
    out_dir.mkdir(parents=True, exist_ok=True)
    # Clear outputs from earlier runs so nothing stale is mistaken for new.
    for old in [*out_dir.glob("*.png"), out_dir / "report.json"]:
        old.unlink(missing_ok=True)
    shutil.rmtree(out_dir / "crops", ignore_errors=True)
    user_args = ["--scene", scene, "--out", str(out_dir), "--warmup", str(args.warmup)]
    if args.modes:
        user_args += ["--modes", args.modes]
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
    if mismatch:
        print(f"  gdh: {mismatch}", file=sys.stderr)
    return code == 0 and not mismatch


def scene_out_name(scene):
    return scene.removeprefix("res://").removesuffix(".tscn").removesuffix(".scn").replace("/", "__")


def cmd_capture(args):
    project = Path(args.project).resolve()
    out_root = Path(args.out).resolve()
    parse_resolution(args.resolution)
    if not args.no_build:
        build_csharp(project)
    if not args.no_import:
        ensure_imported(project)
    ok = True
    with alert_shims() as shims:
        for scene in args.scene:
            out_dir = out_root if len(args.scene) == 1 else out_root / scene_out_name(scene)
            ok &= capture_one(project, scene, out_dir, args, shims)
    return 0 if ok else 1
