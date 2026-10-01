"""The X display each Godot run gets, off the user's desktop.

Godot always runs with its X11 driver, on one of two displays:

gpu   weston's headless backend, compositing on the GPU, with a rootful Xwayland
      as its one client. Xwayland has DRI3, so Vulkan hands each frame to it as a
      GPU buffer, and the game runs at the GPU's own speed.
xvfb  Xvfb, which has no DRI3: Vulkan copies each frame through the CPU, about
      100 ms a frame at 3840x2160, and the GPU idles in between.

"auto" (the default) is the GPU display when it starts, else Xvfb with a note.
Each GPU display has a runtime directory of its own for weston's socket, under
$XDG_RUNTIME_DIR/gdh/displays, so nothing of the user's session is touched.

Both end by themselves when Godot does: Xvfb and Xwayland are started with
-terminate, and weston exits when Xwayland, the program it runs, exits.
"""
import fcntl
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from gdh.godot import GdhError, kill_groups

CHOICES = ("auto", "gpu", "xvfb")
SOCKET = "gdh-wayland"
# Xwayland never makes its screen smaller than this (RandR's minimum).
MIN_WIDTH, MIN_HEIGHT = 320, 200
# A runtime directory with no live weston, older than this, is left over from a run that was killed.
STALE_AFTER_S = 60

# Why the GPU display can't start in this process, once a try has failed, so
# "auto" doesn't try again for every scene or instance.
_unavailable = None


class DisplayUnavailable(GdhError):
    """The GPU display can't start here."""


class Display:
    """A running display: its X name, its process group leader, and anything to clean up."""

    def __init__(self, kind, name, proc, groups, runtime_dir=None, log=None, device=None):
        self.kind = kind
        self.name = name
        self.proc = proc
        self.groups = groups
        self.runtime_dir = runtime_dir
        self.log = log
        self.device = device

    def record(self):
        """What a live session's file keeps, so another gdh command can stop it."""
        return {"kind": self.kind, "name": self.name, "pid": self.proc.pid, "groups": self.groups,
                "runtime_dir": self.runtime_dir, "log": self.log, "device": self.device}

    def stop(self):
        stop_displays([self.record()])
        self.proc.wait()


def stop_displays(records):
    """Stop displays by their records, and remove their runtime directories."""
    kill_groups(*[g for r in records for g in r.get("groups", [r["pid"]])])
    for record in records:
        if record.get("runtime_dir"):
            shutil.rmtree(record["runtime_dir"], ignore_errors=True)


def default_choice():
    choice = os.environ.get("GDH_DISPLAY", "auto") or "auto"
    if choice not in CHOICES:
        raise GdhError(f"GDH_DISPLAY is {choice!r}: it takes {', '.join(CHOICES)}.")
    return choice


def open_display(choice, resolution, log=None):
    """Start a display for one Godot run. choice is auto, gpu or xvfb.

    "gpu" fails if the GPU display can't start; "auto" falls back to Xvfb and
    says why on stderr, once per gdh command. log is where the display's own
    output goes (default: nowhere for Xvfb, its runtime directory for weston).
    """
    global _unavailable
    if choice == "xvfb":
        return start_xvfb(resolution, log)
    if _unavailable is None:
        try:
            return start_gpu(resolution, log)
        except DisplayUnavailable as e:
            _unavailable = str(e)
            if choice == "auto":
                print(f"gdh: note: running on Xvfb, which copies every frame through the CPU, because the GPU display "
                      f"can't start: {_unavailable} (--display xvfb or GDH_DISPLAY=xvfb picks Xvfb without this note)",
                      file=sys.stderr)
    if choice == "gpu":
        raise GdhError(f"The GPU display can't start: {_unavailable}")
    return start_xvfb(resolution, log)


def parse_resolution(resolution):
    match = re.fullmatch(r"(\d+)x(\d+)", resolution)
    if not match or not all(int(v) > 0 for v in match.groups()):
        raise GdhError(f"--resolution takes WIDTHxHEIGHT, such as 1280x720, not {resolution!r}.")
    return int(match.group(1)), int(match.group(2))


def read_display_number(fd, timeout):
    """The display number an X server writes to -displayfd, or "" if it didn't in time.

    The number and its newline can come separately, and the server dies if the
    pipe is closed between the two, so read up to the newline."""
    data = b""
    deadline = time.monotonic() + timeout
    try:
        while b"\n" not in data:
            ready, _, _ = select.select([fd], [], [], max(deadline - time.monotonic(), 0))
            chunk = os.read(fd, 64) if ready else b""
            if not chunk:
                break
            data += chunk
    finally:
        os.close(fd)
    return data.decode().strip() if data.endswith(b"\n") else ""


def start_xvfb(resolution, log=None, timeout=15):
    """Start Xvfb on a free display, in a new process group.

    -displayfd lets Xvfb pick the display number itself, so concurrent runs
    never collide the way `xvfb-run -a` can. -terminate makes it exit when its
    last client (Godot) disconnects. Xvfb gets its own session and process
    group, so it outlives the gdh command that started it.
    """
    width, height = parse_resolution(resolution)
    if not shutil.which("Xvfb"):
        raise GdhError("Xvfb isn't installed (Arch: xorg-server-xvfb, Debian/Ubuntu: xvfb).")
    read_fd, write_fd = os.pipe()
    with open(log or os.devnull, "w") as out:
        proc = subprocess.Popen(
            ["Xvfb", "-displayfd", str(write_fd), "-screen", "0", f"{width}x{height}x24", "-nolisten", "tcp",
             "-terminate"],
            pass_fds=[write_fd], stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
            start_new_session=True)
    os.close(write_fd)
    number = read_display_number(read_fd, timeout)
    if not number:
        kill_groups(proc.pid)
        proc.wait()
        raise GdhError("Xvfb didn't start. Is it installed (Arch: xorg-server-xvfb, Debian/Ubuntu: xvfb)?")
    return Display("xvfb", f":{number}", proc, [proc.pid], log=str(log) if log else None)


def displays_root():
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base and os.path.isdir(base):
        return Path(base) / "gdh" / "displays"
    return Path(tempfile.gettempdir()) / f"gdh-{os.getuid()}" / "displays"


def sweep_stale(root):
    """Remove runtime directories that no weston holds any more: left by a run that was killed."""
    if not root.is_dir():
        return
    now = time.time()
    for directory in root.iterdir():
        lock = directory / f"{SOCKET}.lock"
        try:
            if lock.exists():
                # libwayland-server holds an exclusive flock on the lock file for as long as weston runs.
                with open(lock) as f:
                    try:
                        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
            elif now - directory.stat().st_mtime < STALE_AFTER_S:
                continue  # (a display that's starting now)
        except OSError:
            continue
        shutil.rmtree(directory, ignore_errors=True)


def child_pids(pid):
    """The children of a process, from /proc."""
    children = []
    for task in Path(f"/proc/{pid}/task").glob("*/children"):
        try:
            children += [int(c) for c in task.read_text().split()]
        except OSError:
            pass
    return children


def start_gpu(resolution, log=None, timeout=15):
    """Start weston (headless, GL renderer) running a rootful Xwayland, in a new process group.

    The screen is the resolution, or Xwayland's smallest (320x200) if that's
    larger; Godot's window is the resolution either way. weston's kiosk shell
    shows Xwayland's one window full screen, and there's no window manager in
    Xwayland, as with Xvfb. Raises DisplayUnavailable if it can't start here.
    """
    missing = [p for p in ("weston", "Xwayland") if not shutil.which(p)]
    if missing:
        raise DisplayUnavailable(f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not installed "
                                 "(Arch: weston xorg-xwayland, Debian/Ubuntu: weston xwayland)")
    width, height = parse_resolution(resolution)
    width, height = max(width, MIN_WIDTH), max(height, MIN_HEIGHT)
    root = displays_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    sweep_stale(root)
    runtime = tempfile.mkdtemp(dir=root)
    log = str(log or Path(runtime) / "display.log")
    # weston and Xwayland see only their own runtime directory, never the user's session.
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "WAYLAND_SOCKET")}
    env["XDG_RUNTIME_DIR"] = runtime
    read_fd, write_fd = os.pipe()
    cmd = ["weston", "--backend=headless", "--renderer=gl", f"--width={width}", f"--height={height}",
           f"--socket={SOCKET}", "--shell=kiosk", "--no-config", "--idle-time=0",
           "--", "Xwayland", "-displayfd", str(write_fd), "-geometry", f"{width}x{height}", "-nolisten", "tcp",
           "-terminate"]
    with open(log, "w") as out:
        proc = subprocess.Popen(cmd, pass_fds=[write_fd], env=env, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True)
    os.close(write_fd)
    number = read_display_number(read_fd, timeout)
    # weston starts Xwayland in a session of its own, so its process group is its own too.
    display = Display("gpu", f":{number}", proc, [proc.pid, *child_pids(proc.pid)], runtime, log)
    try:
        if not number:
            raise DisplayUnavailable(f"weston and Xwayland didn't start: {log_tail(log)}")
        text = Path(log).read_text(errors="replace")
        device = re.search(r"Using rendering device: (\S+)", text)
        if not device:
            renderer = re.search(r"GL renderer: (.*)", text)
            raise DisplayUnavailable(f"weston found no GPU to composite on (its GL renderer: "
                                     f"{renderer.group(1).strip() if renderer else 'none'})")
        if "falling back to sw" in text:
            raise DisplayUnavailable("Xwayland couldn't use the GPU (glamor)")
    except DisplayUnavailable:
        display.stop()
        raise
    display.device = device.group(1)
    return display


def log_tail(path, lines=6):
    try:
        text = [line for line in Path(path).read_text(errors="replace").splitlines() if line.strip()]
    except OSError:
        return "(no log)"
    return " / ".join(text[-lines:]) or "(empty log)"
