"""Whether a project's import cache (.godot/imported) is missing or stale, and importing it when it is.

Godot run from the command line, as gdh runs a game, never imports: a texture or model whose imported copy is missing
fails to load ("Failed loading resource: res://.godot/imported/....ctex"), and one whose copy is stale draws as it was.
A fresh checkout or worktree has no .godot at all. So capture, live and movie check first, by file times alone, and
import only when something changed; a check costs a stat of each asset, an import a Godot start.
"""
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

from gdh.godot import alert_shims, build_csharp, godot_binary, godot_env

# Files Godot imports. A source of one of these kinds with no .import beside it has never been imported.
IMPORTABLE = {
    "png", "jpg", "jpeg", "webp", "svg", "bmp", "tga", "exr", "hdr", "dds", "ktx",
    "glb", "gltf", "obj", "fbx", "dae",
    "wav", "ogg", "mp3",
    "ttf", "otf", "woff", "woff2", "fnt",
}
# What an import that couldn't fix the cache last time left as its reason: the same reason again doesn't import again.
UNFIXABLE = ".godot/gdh-import-unfixable"
SKIPPED_DIRS = {".godot", ".git", ".import", "node_modules", "__pycache__"}

# Engine errors that mean a resource didn't load. A missing imported copy shows as both.
MISSING = (re.compile(r"Failed loading resource: (res://\S+?)\.?$"),
           re.compile(r"Unable to open file: (res://\.godot/imported/\S+?)\.?$"))


def project_files(project):
    """Every file in the project outside .godot, skipping directories Godot ignores (.gdignore) and hidden ones."""
    for root, dirs, files in os.walk(project):
        if ".gdignore" in files and Path(root) != Path(project):
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in SKIPPED_DIRS and not d.startswith(".")]
        for name in files:
            yield Path(root) / name


def res_path(project, res):
    return Path(project) / res.removeprefix("res://")


def md5_of(path):
    digest = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def stale_reason(project):
    """Why the project needs importing, in a phrase, or None when its import cache is up to date."""
    project = Path(project)
    godot_dir = project / ".godot"
    if not godot_dir.is_dir():
        return "it has no .godot directory (never imported here)"
    if not (godot_dir / "uid_cache.bin").exists():
        return ".godot/uid_cache.bin is missing"
    imports = set()
    sources = []
    for path in project_files(project):
        if path.suffix == ".import":
            imports.add(path)
        elif path.suffix[1:].lower() in IMPORTABLE:
            sources.append(path)
    for source in sources:
        if source.with_name(source.name + ".import") not in imports:
            return f"{source.relative_to(project)} has never been imported"
    for imp in sorted(imports):
        reason = import_file_stale(project, imp)
        if reason:
            return reason
    return None


def import_file_stale(project, imp):
    """Why one asset's imported copy is missing or out of date, or None."""
    text = imp.read_text(errors="replace")
    dest = re.search(r"^dest_files=\[(.*)\]", text, re.M)
    if not dest:
        return None  # skipped or kept as it is: nothing imported to check
    files = re.findall(r'"(res://[^"]+)"', dest.group(1))
    if not files:
        return None
    for res in files:
        if not res_path(project, res).exists():
            return f"{imp.relative_to(project).with_suffix('')}'s imported copy is missing"
    source = imp.with_suffix("")
    if not source.exists():
        return None  # a stray .import; Godot drops it on its next import
    md5 = res_path(project, files[0]).with_suffix(".md5")
    try:
        if source.stat().st_mtime <= md5.stat().st_mtime:
            return None
        # Newer than its import: changed, or only touched (a checkout). The hash tells.
        stored = re.search(r'^source_md5="([0-9a-f]+)"', md5.read_text(), re.M)
    except OSError:
        return f"{source.relative_to(project)} has no import record"
    if not stored or stored.group(1) != md5_of(source):
        return f"{source.relative_to(project)} changed since it was imported"
    return None


def run_import(project, quiet=False):
    """Godot's headless import of the project. Returns its exit code."""
    cmd = [godot_binary(project), "--headless", "--import", "--path", str(project)]
    out = subprocess.DEVNULL if quiet else None
    with alert_shims() as shims:
        # Headless needs no display. The bogus one keeps any child off the desktop.
        env = godot_env(shims, ":gdh-no-display", game=False)
        code = subprocess.run(cmd, env=env, stdout=out, stderr=out).returncode
        if code != 0:
            # Godot 4.7's editor sometimes aborts at the end of a headless import
            # ("Parameter "singleton" is null" in EditorNode::is_cmdline_mode, exit 134),
            # in the standard and .NET builds alike. The next run succeeds.
            print(f"gdh: Godot exited with code {code} while importing; importing again", file=sys.stderr)
            code = subprocess.run(cmd, env=env, stdout=out, stderr=out).returncode
    return code


def ensure_imported(project):
    """Import the project when its import cache is missing or stale; do nothing (but stat its files) when it isn't.

    Says on stderr why it imports. An import that leaves the same reason behind (an asset Godot can't import, say) is
    remembered, so later runs don't import again for it."""
    project = Path(project)
    reason = stale_reason(project)
    if reason is None:
        return
    unfixable = project / UNFIXABLE
    if unfixable.exists() and unfixable.read_text().strip() == reason:
        return
    print(f"gdh: importing the project first: {reason}", file=sys.stderr)
    run_import(project, quiet=True)
    after = stale_reason(project)
    if after:
        print(f"gdh: note: still out of date after importing ({after}); not importing again for it", file=sys.stderr)
        (project / ".godot").mkdir(exist_ok=True)
        unfixable.write_text(after + "\n")
    else:
        unfixable.unlink(missing_ok=True)


def missing_resources(errors):
    """The res:// paths that engine errors say failed to load: the sources (a texture, a scene), or the imported
    copies when no source is named."""
    found = []
    for e in errors:
        for pattern in MISSING:
            match = pattern.search(e.get("message", ""))
            if match and match.group(1) not in found:
                found.append(match.group(1))
    sources = [p for p in found if not p.startswith("res://.godot/")]
    return sources or found


def describe_missing(paths):
    """A line naming resources that didn't load, for capture, live and movie to print as a defect."""
    shown = ", ".join(paths)
    shown = shown if len(shown) < 300 else shown[:300] + "..."
    return (f"DEFECT: {len(paths)} resource(s) failed to load ({shown}). The game draws without them. An import cache "
            f"that is missing or stale does this: run gdh import --project <dir>, or check the paths.")


def cmd_import(args):
    project = Path(args.project).resolve()
    if not args.no_build:
        build_csharp(project)
    return run_import(project)
