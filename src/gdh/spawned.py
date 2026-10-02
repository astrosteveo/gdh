"""Processes a live game spawned: a launcher's game, a server, a tool it runs.

Godot's OS.create_process and OS.execute_with_pipe start each child in a session of its own (setsid), and a child whose
parent exits is handed to init, so neither the game's process group nor its parent links find them. gdh marks the game
instead: GDH_MARK=<random> in its environment, which everything it spawns inherits. A process that clears its
environment, or sets GDH_MARK itself, isn't found.

  marked(mark, exclude)   every live process of this user's whose environment holds GDH_MARK=mark
  stop(pids)              SIGTERM, then SIGKILL for any that remain
"""
import os
import signal
import time

VAR = "GDH_MARK"


def marked(mark, exclude=()):
    """[{"pid", "cmd"}] for each running process whose environment holds GDH_MARK=mark, pid order."""
    if not mark:
        return []
    needle = f"{VAR}={mark}".encode()
    found = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit() or int(entry.name) in exclude:
            continue
        try:
            with open(f"/proc/{entry.name}/environ", "rb") as f:
                if needle not in f.read().split(b"\0"):
                    continue
            with open(f"/proc/{entry.name}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode(errors="replace").strip()
        except OSError:  # gone, or another user's
            continue
        if cmd:  # a zombie has no command line: it has ended
            found.append({"pid": int(entry.name), "cmd": cmd})
    return sorted(found, key=lambda p: p["pid"])


def stop(pids, wait=3.0):
    """Stop processes by pid: SIGTERM, then SIGKILL for any still there after `wait` seconds."""
    for sig, delay in ((signal.SIGTERM, wait), (signal.SIGKILL, 1.0)):
        remaining = [p for p in pids if _alive(p)]
        for pid in remaining:
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline and any(_alive(p) for p in remaining):
            time.sleep(0.1)


def _alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False
