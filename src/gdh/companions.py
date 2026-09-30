"""Companion processes for gdh live: programs a session starts beside the game.

A companion is a shell command, such as a game server, that gdh starts before
the game, waits for, hands to the game and stops with the session. gdh picks a
free port for each one and fills it into the commands and the game's arguments:

  {port}        the companion's own port (in its command and its ready check)
  {NAME.port}   any companion's port, anywhere (the game's arguments too)
  {instance}    in the game's arguments: which game instance (0, 1, ...)

The companion also finds its port in the GDH_PORT environment variable.
--companion-port NAME=PORT gives one a port of your choosing instead.
"""
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from gdh.godot import GdhError

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PLACEHOLDER = re.compile(r"\{(?:(?P<name>[A-Za-z_][A-Za-z0-9_]*)\.port|(?P<own>port)|(?P<instance>instance))\}")


def parse_named(specs, what):
    """NAME=VALUE pairs, in order, as a dict. Raises on a bad or repeated name."""
    out = {}
    for spec in specs:
        name, sep, value = spec.partition("=")
        if not sep or not NAME.match(name):
            raise GdhError(f"{what} takes NAME=VALUE, with NAME a plain identifier: got {spec!r}")
        if name in out:
            raise GdhError(f"{what} {name!r} is given twice.")
        out[name] = value
    return out


def free_port():
    """A TCP port on 127.0.0.1 that's free now."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_of(text):
    """A port given by hand."""
    try:
        port = int(text)
    except ValueError:
        port = 0
    if not 0 < port < 65536:
        raise GdhError(f"--companion-port takes a port from 1 to 65535, not {text!r}.")
    return port


def expand(text, ports, own=None, instance=None):
    """Fill the placeholders in text. Unknown names are an error; other braces are left alone."""
    def fill(match):
        if match.group("name"):
            if match.group("name") not in ports:
                raise GdhError(f"{match.group(0)} names no companion. Companions: {', '.join(ports) or 'none'}")
            return str(ports[match.group("name")])
        if match.group("own"):
            if own is None:
                raise GdhError("{port} works only in a companion's own command and ready check. Use {NAME.port}.")
            return str(ports[own])
        if instance is None:
            raise GdhError("{instance} works only in the game's arguments.")
        return str(instance)
    return PLACEHOLDER.sub(fill, text)


def start(name, command, port, ports, out, ready, timeout):
    """Start one companion in its own process group and wait until it's ready.

    ready is "tcp" (its port takes a connection), "none", or an http(s) URL that
    must answer 2xx. Returns the companion's record for the session file.
    """
    cmd = expand(command, ports, own=name)
    log_path = Path(out) / f"{name}.log"
    env = dict(os.environ)
    env["GDH_PORT"] = str(port)
    with open(log_path, "w") as log:
        proc = subprocess.Popen(["sh", "-c", cmd], stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=env, start_new_session=True)
    record = {"name": name, "pid": proc.pid, "port": port, "cmd": cmd, "log": str(log_path)}
    check = expand(ready, ports, own=name) if ready not in ("tcp", "none") else ready
    deadline = time.monotonic() + timeout
    while not is_ready(check, port):
        if proc.poll() is not None:
            raise CompanionError(record, f"Companion '{name}' exited with code {proc.returncode} before it was ready.")
        if time.monotonic() > deadline:
            raise CompanionError(record, f"Companion '{name}' wasn't ready after {timeout} s ({describe(check, port)}).")
        time.sleep(0.2)
    return record


def is_ready(check, port):
    if check == "none":
        return True
    if check == "tcp":
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            return False
    try:
        with urllib.request.urlopen(check, timeout=2) as reply:
            return 200 <= reply.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def describe(check, port):
    if check == "tcp":
        return f"waiting for port {port} to take a connection"
    if check == "none":
        return "no ready check"
    return f"waiting for {check} to answer 2xx"


class CompanionError(GdhError):
    """A companion that failed to start, with its record so the caller can stop it and show its log."""

    def __init__(self, record, message):
        super().__init__(message)
        self.record = record
