"""Runs a --binary session's program as its parent, so how it ended is known after gdh has returned.

gdh live start runs this detached. Only a process's parent learns its exit status, so this process starts the program,
waits for it, and writes {"code", "seconds", "idle"} to --exit-file: the exit code, or minus the signal that ended it.
The program gets a session of its own (stopping its process group stops it), GDH_MARK in its environment (spawned.py)
and a core size limit of 0, so a crash leaves no core dump for a desktop's crash reporter to show the user. It runs
under stdbuf, so its output is line-buffered and its log current: a program whose output isn't a terminal otherwise
holds its prints until its buffer fills, and a release export of a Godot game until it exits.

It also holds a connection to the program's display ($DISPLAY) for as long as the program runs. gdh's displays are
started with -terminate, so they exit when their last client leaves: without it, a gdh command that looked at the
screen before the program had connected would take the display down as it left.

There's no harness in the program to keep the session's idle timeout, so this process keeps it: once no gdh live
command has touched the session file for --idle-timeout seconds, it notes so in the log and stops the program.

  python -m gdh.runner --log LOG --exit-file FILE --mark MARK [--idle-timeout S --session-file PATH] -- COMMAND...

The first line on stdout is {"pid": N}, or {"error": "..."} if the program couldn't start.
"""
import argparse
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import time

from gdh import spawned, x11
from gdh.godot import GdhError, kill_groups


def main():
    parser = argparse.ArgumentParser(prog="gdh.runner")
    parser.add_argument("--log", required=True)
    parser.add_argument("--exit-file", required=True)
    parser.add_argument("--mark", required=True)
    parser.add_argument("--idle-timeout", type=float, default=0)
    parser.add_argument("--session-file")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if shutil.which("stdbuf"):
        command = [shutil.which("stdbuf"), "-oL", "-eL", *command]
    resource.setrlimit(resource.RLIMIT_CORE, (0, resource.getrlimit(resource.RLIMIT_CORE)[1]))
    env = dict(os.environ)
    env[spawned.VAR] = args.mark
    started = time.time()
    try:
        held = x11.Display(env["DISPLAY"])  # until this process exits (never closed: the display may be gone by then)
    except GdhError as e:
        print(json.dumps({"error": str(e)}), flush=True)
        return 1
    try:
        with open(args.log, "w") as log:
            proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                                    start_new_session=True)
    except OSError as e:
        print(json.dumps({"error": f"Couldn't run {command[0]}: {e.strerror or e}"}), flush=True)
        return 1
    print(json.dumps({"pid": proc.pid}), flush=True)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, sys.stdout.fileno())
    idle = False
    while proc.poll() is None:
        if args.idle_timeout > 0 and time.time() - max(started, last_command(args.session_file)) >= args.idle_timeout:
            idle = True
            with open(args.log, "a") as log:
                log.write(f"\ngdh live: stopped the program after {args.idle_timeout:g} seconds without a gdh live "
                          f"command (--idle-timeout)\n")
            kill_groups(proc.pid)
            proc.wait()
            break
        time.sleep(0.2)
    # gdh's cleanup stops this process's group once the program has ended: write how it ended first.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    record = {"code": proc.returncode, "seconds": round(time.time() - started, 2), "idle": idle}
    tmp = f"{args.exit_file}.tmp"
    with open(tmp, "w") as f:
        json.dump(record, f)
    os.replace(tmp, args.exit_file)
    return 0


def last_command(session_file):
    try:
        return os.stat(session_file).st_mtime
    except (OSError, TypeError):
        return 0


if __name__ == "__main__":
    sys.exit(main())
