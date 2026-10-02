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
    """Stop process groups: first SIGTERM, then SIGKILL for any that remain."""
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


def build_csharp(project):
    """Build a C# project's assemblies with `dotnet build`: Godot loads them
    from .godot/mono but never builds them when run from the command line, so
    a stale build would run old code. Does nothing for a GDScript project."""
    csproj = csharp_project(project)
    if csproj is None:
        return
    if not shutil.which("dotnet"):
        raise GdhError("This is a C# project, which needs the .NET SDK to build. Install it (dotnet) or pass --no-build.")
    proc = subprocess.run(["dotnet", "build", str(csproj), "-nologo", "-v:q"], capture_output=True, text=True)
    if proc.returncode != 0:
        lines = [line for line in (proc.stdout + proc.stderr).splitlines() if "error" in line.lower()]
        raise GdhError(f"dotnet build {csproj.name} failed:\n" + "\n".join(dict.fromkeys(lines[-20:] or [proc.stdout[-2000:]])))


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
