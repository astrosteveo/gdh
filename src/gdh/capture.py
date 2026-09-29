"""gdh capture and gdh import."""
import json
import os
import shutil
import subprocess
from pathlib import Path

from gdh.godot import HARNESS, alert_shims, godot_cmd, godot_env
from gdh.images import crop_findings, save_tiles

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
    cmd = godot_cmd(project, args.resolution, ["--script", str(CAPTURE_SCRIPT), "--", *user_args])
    with open(out_dir / "godot.log", "w") as log:
        try:
            proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT,
                                  env=godot_env(shims), timeout=args.timeout)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    report_path = out_dir / "report.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    if report:
        images = {p.stem: p for p in out_dir.glob("*.png")}
        crop_findings(report.get("findings", []), report.get("image_size"), images, out_dir / "crops", out_dir)
        if args.tiles and "normal" in images:
            save_tiles(images["normal"], out_dir / "crops")
        report_path.write_text(json.dumps(report, indent=2))
    pngs = sorted(p.name for p in out_dir.glob("*.png"))
    errors = sum(e.get("count", 1) for e in report.get("errors", []))
    findings = report.get("findings", [])
    warnings = sum(f["severity"] == "warning" for f in findings)
    print(f"{scene}: exit={code} adapter={report.get('adapter', '?')} errors={errors} "
          f"warnings={warnings} findings={len(findings)} images={','.join(pngs) or 'none'} -> {out_dir}")
    for f in findings:
        crop = f"  [{f['crop']}]" if f.get("crop") else ""
        print(f"  {f['severity']}: {f['probe']} {f['node']}: {f['message']}{crop}")
    return code == 0


def scene_out_name(scene):
    return scene.removeprefix("res://").removesuffix(".tscn").removesuffix(".scn").replace("/", "__")


def cmd_capture(args):
    project = Path(args.project).resolve()
    out_root = Path(args.out).resolve()
    ok = True
    with alert_shims() as shims:
        for scene in args.scene:
            out_dir = out_root if len(args.scene) == 1 else out_root / scene_out_name(scene)
            ok &= capture_one(project, scene, out_dir, args, shims)
    return 0 if ok else 1


def cmd_import(args):
    project = Path(args.project).resolve()
    cmd = [os.environ.get("GODOT", "godot"), "--headless", "--import", "--path", str(project)]
    with alert_shims() as shims:
        return subprocess.run(cmd, env=godot_env(shims)).returncode
