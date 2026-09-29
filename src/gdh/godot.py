"""Launching Godot off-screen: Xvfb, the command line, environment and alert stand-ins."""
import os
import re
import select
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

HARNESS = Path(__file__).resolve().parent / "harness"
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


def godot_env(shims, display):
    env = dict(os.environ)
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


def start_xvfb(resolution, timeout=15):
    """Start Xvfb on a free display, in a new process group.

    -displayfd lets Xvfb pick the display number itself, so concurrent runs
    never collide the way `xvfb-run -a` can. -terminate makes it exit when its
    last client (Godot) disconnects. Returns (process, ":N"). Xvfb gets its own
    session and process group, so it outlives the gdh command that started it.
    """
    width, height = resolution.split("x")
    read_fd, write_fd = os.pipe()
    proc = subprocess.Popen(
        ["Xvfb", "-displayfd", str(write_fd), "-screen", "0", f"{width}x{height}x24",
         "-nolisten", "tcp", "-terminate"],
        pass_fds=[write_fd], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    os.close(write_fd)
    # Xvfb writes the number and its newline separately, and dies if the pipe
    # is closed between the two, so read up to the newline.
    data = b""
    deadline = time.monotonic() + timeout
    try:
        while b"\n" not in data:
            ready, _, _ = select.select([read_fd], [], [], max(deadline - time.monotonic(), 0))
            chunk = os.read(read_fd, 64) if ready else b""
            if not chunk:
                break
            data += chunk
    finally:
        os.close(read_fd)
    number = data.decode().strip() if data.endswith(b"\n") else ""
    if not number:
        kill_groups(proc.pid)
        raise RuntimeError("Xvfb didn't start. Is it installed (Arch: xorg-server-xvfb, Debian/Ubuntu: xvfb)?")
    return proc, f":{number}"


def godot_cmd(project, resolution, extra):
    gpu = ["--gpu-index", os.environ["GDH_GPU_INDEX"]] if "GDH_GPU_INDEX" in os.environ else []
    return [
        os.environ.get("GODOT", "godot"),
        "--display-driver", "x11",
        "--rendering-driver", "vulkan",
        *gpu,
        "--audio-driver", "Dummy",
        "--resolution", resolution,
        "--path", str(project),
        *extra,
    ]


def project_ticks(project):
    """The project's physics ticks per second (Godot's default is 60)."""
    text = (Path(project) / "project.godot").read_text()
    section = re.search(r"^\[physics\]\n(.*?)(?=^\[|\Z)", text, re.M | re.S)
    if section:
        match = re.search(r"^common/physics_ticks_per_second=(\d+)", section.group(1), re.M)
        if match:
            return int(match.group(1))
    return 60
