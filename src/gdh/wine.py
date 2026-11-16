"""Running a Windows build under Wine, for gdh export --smoke, off the user's desktop and out of their own Wine prefix.

Each run gets a Wine prefix of its own, in the run's temporary directory: wineboot makes it (about 2 s), the build runs
in it, and wineserver -k stops every Wine process in it afterwards. The game's user:// is in that prefix
(C:/users/<user>/AppData/Roaming/Godot/app_userdata/<name>), so each run starts with none, as a new player does.

Wine counts a window's title bar against the screen though there's no window manager to draw one: on a screen exactly
the window's size, Godot shrinks the window by it (1280x720 comes up 1280x698). A smoke run's screen is FRAME_ROOM
taller than the window, and the screen it saves is the window's part of it.

A new prefix links its user folders (Desktop, Documents, Downloads) to the same folders in $HOME, found through
$XDG_CONFIG_HOME/user-dirs.dirs. HOME is an empty directory beside the prefix and XDG_CONFIG_HOME is unset, so they're
plain folders in the prefix and nothing the game writes reaches the user's own. XDG_CACHE_HOME stays the user's, so the
GPU driver's shader cache stays warm as it does for a Linux build.

Wine's own programs that would reach the user's desktop or wait for a person are turned off: winemenubuilder (menu
entries and file associations), the Mono and Gecko installers, and winedbg, which Wine starts when a program crashes
and which would keep the crashed game running until it was killed. With it off, a crash ends the game at once, its exit
code the low byte of the Windows exception's (29 for an illegal instruction, 5 for an access violation).
"""
import os
import shutil
import subprocess
from pathlib import Path

from gdh.godot import GdhError

# Wine's programs that a smoke run turns off: "" or "d" is disabled.
DLL_OVERRIDES = "mscoree,mshtml,winemenubuilder.exe,winedbg.exe=d"
BOOT_TIMEOUT = 120
# How much taller than the window a Windows build's screen is: room for the title bar Wine counts (22 px at 96 dpi).
FRAME_ROOM = 100


def find():
    """The wine program, or a GdhError saying how to get it."""
    wine = shutil.which("wine")
    if not wine:
        raise GdhError("--smoke runs a Windows build under Wine, and wine isn't on PATH. Install Wine (pacman -S "
                       "wine, apt install wine), or smoke-test a Linux preset.")
    return wine


def version(wine):
    """What wine --version prints (wine-11.22 (Staging)), or "Wine" if it prints nothing."""
    try:
        out = subprocess.run([wine, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    return out or "Wine"


def environment(env, root):
    """env for a Wine program with a prefix of its own in root: root/prefix, HOME root/home."""
    env = dict(env)
    home = Path(root) / "home"
    home.mkdir(parents=True, exist_ok=True)
    env["XDG_CACHE_HOME"] = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    env["HOME"] = str(home)
    env.pop("XDG_CONFIG_HOME", None)
    env["WINEPREFIX"] = str(Path(root) / "prefix")
    env["WINEDLLOVERRIDES"] = DLL_OVERRIDES
    # Wine's err: lines and a crash's stay in the log; its fixme: lines, hundreds of them, don't.
    env["WINEDEBUG"] = "fixme-all"
    return env


def boot(wine, env, log):
    """Make the prefix in env's WINEPREFIX, writing wineboot's output to log. Raises a GdhError if it fails."""
    with open(log, "w") as out:
        try:
            proc = subprocess.run([wine, "wineboot", "--init"], env=env, stdout=out, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL, timeout=BOOT_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise GdhError(f"Wine didn't make its prefix in {BOOT_TIMEOUT} s (wineboot). Its output: {log}") from None
    if proc.returncode != 0:
        raise GdhError(f"Wine couldn't make its prefix (wineboot exited with code {proc.returncode}). Its output: "
                       f"{log}")


def stop(wine, env):
    """Stop every Wine process in env's prefix, and its wineserver."""
    server = Path(wine).with_name("wineserver")
    server = str(server) if server.is_file() else shutil.which("wineserver")
    if not server:
        return
    try:
        subprocess.run([server, "-k"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       stdin=subprocess.DEVNULL, timeout=15)
    except subprocess.TimeoutExpired:
        pass
