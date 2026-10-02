"""Stops a live session's processes once any of its games has ended.

gdh live start runs this detached, beside the session. A game can end without a
gdh command (its idle timeout, a crash, a quit of its own), and nothing else
would then stop the companions, the displays and the other instances it leaves
behind, the processes the games spawned (spawned.py), or remove the displays'
runtime directories. With --keep-children the session lasts while any game or
any process they spawned runs, so a launcher can hand off to the game it starts.
The games' idle timeout lives in gdh's harness, in the games, so once every game
has exited the watchdog applies it instead: with no gdh command on the session
(each one touches the session file) for --idle-timeout seconds, counted from the
later of the last command and the last game's exit, it ends the session as above.

  python -m gdh.watchdog --watch PID [PID...] --groups PGID [PGID...] --remove DIR [DIR...]
                         [--marks MARK [MARK...]] [--keep-children]
                         [--idle-timeout SECONDS --session-file PATH --logs LOG [LOG...]]
"""
import argparse
import os
import shutil
import signal
import time

from gdh import spawned
from gdh.godot import kill_groups, pid_alive


def main():
    parser = argparse.ArgumentParser(prog="gdh.watchdog")
    parser.add_argument("--watch", type=int, nargs="+", required=True, help="Game processes: any of them ending ends the session")
    parser.add_argument("--groups", type=int, nargs="*", default=[], help="Process groups to stop then")
    parser.add_argument("--remove", nargs="*", default=[], help="Directories to remove then (displays' runtime directories)")
    parser.add_argument("--marks", nargs="*", default=[], help="The games' GDH_MARK values: what they spawned")
    parser.add_argument("--keep-children", action="store_true",
                        help="Last while any game or any process they spawned runs, not just while every game runs")
    parser.add_argument("--idle-timeout", type=float, default=0,
                        help="With --keep-children, once every game has exited: end the session after this many "
                             "seconds without a gdh command on it (0: never)")
    parser.add_argument("--session-file", help="The session file, whose mtime is the last gdh command's time")
    parser.add_argument("--logs", nargs="*", default=[], help="The games' logs, to note an idle stop in")
    parser.add_argument("--interval", type=float, default=0.5)
    args = parser.parse_args()
    # gdh live stop stops this process's group too, and that's no reason to stop anything else.
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    def children():
        return [p["pid"] for mark in args.marks for p in spawned.marked(mark, exclude=args.watch)]

    if args.keep_children:
        exited = None  # when the watchdog first saw no game running
        while any(pid_alive(pid) for pid in args.watch) or children():
            if any(pid_alive(pid) for pid in args.watch):
                exited = None
            elif args.idle_timeout > 0:
                exited = exited or time.time()
                idle = time.time() - max(exited, last_command(args.session_file))
                if idle >= args.idle_timeout:
                    for log in args.logs:
                        note(log, f"gdh live: stopped what the game spawned, and the session, after "
                                  f"{args.idle_timeout:g} seconds without a gdh command (--idle-timeout)")
                    break
            time.sleep(args.interval)
    else:
        while all(pid_alive(pid) for pid in args.watch):
            time.sleep(args.interval)
    kill_groups(*args.watch)
    spawned.stop(children())
    kill_groups(*args.groups)
    for directory in args.remove:
        shutil.rmtree(directory, ignore_errors=True)


def last_command(session_file):
    """When a gdh command last touched the session (0 if the file isn't there yet, or is gone)."""
    try:
        return os.stat(session_file).st_mtime
    except (OSError, TypeError):
        return 0


def note(log, line):
    try:
        with open(log, "a") as f:
            f.write(f"\n{line}\n")
    except OSError:
        pass


if __name__ == "__main__":
    main()
