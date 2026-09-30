"""Stops a live session's processes once any of its games has ended.

gdh live start runs this detached, beside the session. A game can end without a
gdh command (its idle timeout, a crash, a quit of its own), and nothing else
would then stop the companions and the other instances it leaves behind.

  python -m gdh.watchdog --watch PID [PID...] --groups PGID [PGID...]
"""
import argparse
import os
import signal
import time

from gdh.godot import kill_groups, pid_alive


def main():
    parser = argparse.ArgumentParser(prog="gdh.watchdog")
    parser.add_argument("--watch", type=int, nargs="+", required=True, help="Game processes: any of them ending ends the session")
    parser.add_argument("--groups", type=int, nargs="*", default=[], help="Process groups to stop then")
    parser.add_argument("--interval", type=float, default=0.5)
    args = parser.parse_args()
    # gdh live stop stops this process's group too, and that's no reason to stop anything else.
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    while all(pid_alive(pid) for pid in args.watch):
        time.sleep(args.interval)
    kill_groups(*args.watch, *args.groups)


if __name__ == "__main__":
    main()
