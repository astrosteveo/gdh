"""Launching Godot off-screen: the command line, environment and alert stand-ins. The display is display.py's."""
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

HARNESS = Path(__file__).resolve().parent / "harness"


class GdhError(Exception):
    """A failure gdh reports in a sentence, without a traceback."""
# Dialog programs Godot's OS.alert() runs on Linux.
ALERT_PROGRAMS = ("zenity", "kdialog", "Xdialog", "xmessage")
ALERT_SHIM = """#!/bin/sh
# Stands in for a dialog program Godot calls from OS.alert(), so the alert
# lands in the log instead of opening a window on the user's desktop.
echo "GODOT ALERT ($(basename "$0")): $*" >&2
exit 0
"""


def write_alert_shims(directory):
    """Write stand-ins for the dialog programs into directory."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name in ALERT_PROGRAMS:
        path = directory / name
        path.write_text(ALERT_SHIM)
        path.chmod(0o755)
    return directory


@contextmanager
def alert_shims():
    """A temporary directory of stand-ins for the dialog programs."""
    with tempfile.TemporaryDirectory(prefix="gdh-shims-") as tmp:
        yield write_alert_shims(tmp)


def user_data_home():
    """Where a game run under gdh keeps user:// (its saves, settings, logs and caches): never the player's own, so a
    test run can't rotate out their logs or touch their saves. GDH_USER_DATA names another directory, or "real" for
    the player's own. Kept between runs, so a game's caches stay warm."""
    chosen = os.environ.get("GDH_USER_DATA", "")
    if chosen == "real":
        return None
    if chosen:
        return os.path.abspath(os.path.expanduser(chosen))
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "gdh", "user-data")


def godot_env(shims, display, harness_args=(), game=True):
    env = dict(os.environ)
    # A game's user:// lives under XDG_DATA_HOME on Linux: point it at gdh's own (the editor's import keeps the real
    # one, where its settings and templates are).
    home = user_data_home() if game else None
    if home:
        os.makedirs(home, exist_ok=True)
        env["XDG_DATA_HOME"] = home
    # The harness's settings travel in the environment, so the command line
    # after `--` holds only the game's own arguments.
    env["GDH_ARGS"] = json.dumps([str(a) for a in harness_args])
    # Keep Godot and anything it spawns off the user's session. Wayland
    # clients fall back to "wayland-0" when WAYLAND_DISPLAY is unset, so point
    # it at a socket that doesn't exist.
    env["DISPLAY"] = display
    env["WAYLAND_DISPLAY"] = "gdh-no-wayland"
    env["GDK_BACKEND"] = "x11"
    env["QT_QPA_PLATFORM"] = "xcb"
    # OS.alert() runs zenity/kdialog/etc. The shims log the message instead.
    env["PATH"] = f"{shims}{os.pathsep}{env.get('PATH', '')}"
    return env


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill_groups(*pgids):
    """Stop process groups: first SIGTERM, then SIGKILL for any that remain.

    A group's leader that is our own child (Godot, a display) stays in its group until it's reaped, so this reaps any
    of ours that have exited: read a child's exit code (Popen.wait or poll) before stopping its group, since
    Popen.wait() after this returns 0."""
    for sig, wait in ((signal.SIGTERM, 3.0), (signal.SIGKILL, 1.0)):
        remaining = [p for p in pgids if _group_alive(p)]
        if not remaining:
            return
        for pgid in remaining:
            try:
                os.killpg(pgid, sig)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and any(_group_alive(p) for p in remaining):
            time.sleep(0.1)


def _group_alive(pgid):
    try:
        while os.waitpid(-pgid, os.WNOHANG)[0]:
            pass
    except ChildProcessError:
        pass  # none of the group's processes is our child
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def csharp_project(project):
    """The project's .csproj if it's a C# project, else None."""
    found = sorted(Path(project).glob("*.csproj"))
    return found[0] if found else None


def godot_binary(project):
    """GODOT if it's set. Otherwise Godot's .NET build (godot-mono) for a C#
    project, which the standard build can't run, and godot for the rest."""
    if os.environ.get("GODOT"):
        return os.environ["GODOT"]
    if csharp_project(project):
        if shutil.which("godot-mono"):
            return "godot-mono"
        raise GdhError("This is a C# project, which needs Godot's .NET build. Put godot-mono on PATH or set GODOT.")
    return "godot"


def build_csharp(project, force=False):
    """Build a C# project's assemblies with `dotnet build`: Godot loads them
    from .godot/mono but never builds them when run from the command line, so
    a stale build would run old code. Does nothing for a GDScript project.

    Skipped when nothing the build reads has changed since gdh's last successful build (build_inputs), unless force.
    Returns whether it built: None for a GDScript project."""
    csproj = csharp_project(project)
    if csproj is None:
        return None
    stamp = Path(project) / BUILD_STAMP
    inputs = build_inputs(project)
    if (not force and inputs is not None and stamp.is_file() and stamp.read_text() == inputs
            and (Path(project) / BUILD_OUTPUT).is_dir()):
        return False
    if not shutil.which("dotnet"):
        raise GdhError("This is a C# project, which needs the .NET SDK to build. Install it (dotnet) or pass --no-build.")
    proc = subprocess.run(["dotnet", "build", str(csproj), "-nologo", "-v:q"], capture_output=True, text=True)
    if proc.returncode != 0:
        stamp.unlink(missing_ok=True)
        lines = [line for line in (proc.stdout + proc.stderr).splitlines() if "error" in line.lower()]
        raise GdhError(f"dotnet build {csproj.name} failed:\n" + "\n".join(dict.fromkeys(lines[-20:] or [proc.stdout[-2000:]])))
    if inputs is not None and (Path(project) / BUILD_OUTPUT).is_dir():
        stamp.write_text(inputs)
    else:
        stamp.unlink(missing_ok=True)
    return True


# Where Godot's C# build puts its assemblies, and where gdh keeps the stamp of what it built them from: deleting the
# build deletes the stamp, so the next run builds again.
BUILD_OUTPUT = ".godot/mono/temp/bin"
BUILD_STAMP = ".godot/mono/temp/gdh-build-stamp"
# What a C# build reads: the code and the project files in each project's folder, and the files MSBuild and the SDK
# look for in the folders above it.
BUILD_SUFFIXES = (".cs", ".csproj", ".sln", ".slnx", ".props", ".targets")
BUILD_FILES = ("global.json", "nuget.config", "packages.lock.json", ".editorconfig")
BUILD_FILES_ABOVE = ("Directory.Build.props", "Directory.Build.targets", "Directory.Packages.props", *BUILD_FILES)


def build_inputs(project):
    """A stamp of the files a C# build reads, one a line (path, size, modification time): the code and project files
    in the Godot project's folder and in the folder of every project its .csproj references (followed through their
    own references), the files they import or compile by a path outside those folders, and the build files MSBuild
    looks for in the folders above each. Leaves out hidden folders (.godot, .git) and bin and obj.

    None when a reference can't be followed (a project that isn't there, a path made of MSBuild properties or items),
    so that gdh builds every time rather than run stale code."""
    project = Path(project).resolve()
    folders, files = [project], []
    queue = [p for p in [csharp_project(project)] if p]
    seen = set()
    while queue:
        csproj = queue.pop().resolve()
        if csproj in seen:
            continue
        seen.add(csproj)
        found = msbuild_paths(csproj)
        if found is None:
            return None
        references, more_folders, more_files = found
        for reference in references:
            if not reference.is_file():
                return None
            folders.append(reference.parent)
            queue.append(reference)
        folders += more_folders
        files += more_files
    lines = {}
    roots = []
    for folder in sorted(set(folders), key=lambda f: (len(f.parts), str(f))):
        if not any(folder == root or root in folder.parents for root in roots):
            roots.append(folder)
    for root in roots:
        for directory, dirs, names in os.walk(root):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in ("bin", "obj"))
            for name in sorted(names):
                if name.endswith(BUILD_SUFFIXES) or name.lower() in BUILD_FILES:
                    lines[Path(directory) / name] = None
        for parent in root.parents:
            try:
                names = sorted(os.listdir(parent))
            except OSError:
                continue
            for name in names:
                if name in BUILD_FILES_ABOVE or name.lower() in BUILD_FILES:
                    lines[parent / name] = None
    for path in files:
        lines[path] = None
    out = []
    for path in lines:
        try:
            st = path.stat()
            out.append(f"{path}\t{st.st_size}\t{st.st_mtime_ns}")
        except OSError:
            out.append(f"{path}\tmissing")
    return "\n".join(out) + "\n"


def msbuild_paths(csproj):
    """What a .csproj names outside itself, as paths: ([project it references], [folder a wildcard compiles from],
    [file it imports or compiles]). None when it can't be read, or names a path through an MSBuild property or item
    other than the project's own folder."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(csproj).getroot()
    except (ET.ParseError, OSError):
        return None
    references, folders, files = [], [], []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "Import" and not element.get("Sdk"):
            spec = element.get("Project")
        elif tag in ("ProjectReference", "Compile"):
            spec = element.get("Include")
        else:
            continue
        for item in (spec or "").split(";"):
            item = item.strip()
            if not item:
                continue
            for name in ("MSBuildThisFileDirectory", "MSBuildProjectDirectory"):
                item = item.replace(f"$({name})", str(csproj.parent) + "/")
            if any(mark in item for mark in ("$(", "@(", "%(")):
                return None
            path = Path(os.path.normpath(csproj.parent / item.replace("\\", "/")))
            if tag == "ProjectReference":
                references.append(path)
            elif any(ch in item for ch in "*?"):
                cut = next(i for i, part in enumerate(path.parts) if "*" in part or "?" in part)
                folders.append(Path(*path.parts[:cut]))
            else:
                files.append(path)
    return references, folders, files


def godot_cmd(project, resolution, extra, game_args=()):
    gpu = ["--gpu-index", os.environ["GDH_GPU_INDEX"]] if "GDH_GPU_INDEX" in os.environ else []
    return [
        godot_binary(project),
        "--display-driver", "x11",
        "--rendering-driver", "vulkan",
        *gpu,
        "--audio-driver", "Dummy",
        # gdh paces the frames itself (held, running, stepping uncapped), so V-Sync
        # mustn't hold them to the display's refresh. The game can still turn it on.
        "--disable-vsync",
        "--resolution", resolution,
        "--path", str(project),
        *extra,
        *(["--", *game_args] if game_args else []),
    ]


def size_mismatch(window_size, resolution):
    """A sentence when the game's window isn't the size asked for, else None. window_size is [w, h] as the harness
    reported it (DisplayServer.window_get_size()): the window, not the image, since under stretch mode "viewport" the
    image is at the project's base size whatever the window is."""
    if not window_size:
        return None
    want = [int(v) for v in resolution.split("x")]
    got = [int(round(v)) for v in window_size]
    if got == want:
        return None
    return (f"the game's window is {got[0]}x{got[1]}, not the {resolution} asked for: the game most likely sizes its "
            f"window itself (DisplayServer.window_set_size, Window.size, or a settings file it applies). Its images "
            f"are at the window's size; change what sizes it, or ask for {got[0]}x{got[1]}.")


def project_ticks(project):
    """The project's physics ticks per second (Godot's default is 60)."""
    text = (Path(project) / "project.godot").read_text()
    section = re.search(r"^\[physics\]\n(.*?)(?=^\[|\Z)", text, re.M | re.S)
    if section:
        match = re.search(r"^common/physics_ticks_per_second=(\d+)", section.group(1), re.M)
        if match:
            return int(match.group(1))
    return 60
