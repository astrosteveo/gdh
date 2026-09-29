"""Launching Godot off-screen: the command line, environment and alert stand-ins."""
import os
import re
import tempfile
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


def godot_env(shims):
    env = dict(os.environ)
    # Keep Godot and anything it spawns off the user's session. xvfb-run sets
    # its own DISPLAY. Wayland clients fall back to "wayland-0" when
    # WAYLAND_DISPLAY is unset, so point it at a socket that doesn't exist.
    env.pop("DISPLAY", None)
    env["WAYLAND_DISPLAY"] = "gdh-no-wayland"
    env["GDK_BACKEND"] = "x11"
    env["QT_QPA_PLATFORM"] = "xcb"
    # OS.alert() runs zenity/kdialog/etc. The shims log the message instead.
    env["PATH"] = f"{shims}{os.pathsep}{env.get('PATH', '')}"
    return env


def godot_cmd(project, resolution, extra):
    width, height = resolution.split("x")
    gpu = ["--gpu-index", os.environ["GDH_GPU_INDEX"]] if "GDH_GPU_INDEX" in os.environ else []
    return [
        "xvfb-run", "-a", "-s", f"-screen 0 {width}x{height}x24",
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
