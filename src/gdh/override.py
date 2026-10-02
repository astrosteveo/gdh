"""A project's override.cfg, written for one Godot run and removed as soon as Godot has read it.

Some settings can only come from the project: Movie Maker records at the window size the project's settings give
(display/window/size/window_width_override, else viewport_width), never at --resolution, and its quality is a project
setting too. Godot reads <project>/override.cfg over project.godot at startup, so gdh writes one, starts Godot, and
removes it once the game says it has read its settings; it is also removed if anything fails, on Ctrl-C, and on
SIGTERM or SIGHUP. An override.cfg gdh didn't write is never touched: the run is refused. One gdh left behind (its
process killed outright) names its process, and the next run removes it.
"""
import os
import re
import signal
from contextlib import contextmanager
from pathlib import Path

from gdh.godot import GdhError, pid_alive

MARKER = "; gdh: written for one run by process {pid}, and removed when it ends. Delete it if no gdh is running."
MARKER_RE = re.compile(r"^; gdh: written for one run by process (\d+)")


class OverrideError(GdhError):
    pass


def format_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return '"' + str(value).replace('"', '\\"') + '"'


def render(settings):
    """override.cfg's text: settings is {"section/key/path": value}, split at the first slash as project.godot is."""
    sections = {}
    for name, value in settings.items():
        section, key = name.split("/", 1)
        sections.setdefault(section, []).append(f"{key}={format_value(value)}")
    text = [MARKER.format(pid=os.getpid()), ""]
    for section, lines in sections.items():
        text += [f"[{section}]", "", *lines, ""]
    return "\n".join(text)


def check_free(path):
    """Refuse when an override.cfg is there, unless it's one a gdh process that has gone left behind (removed)."""
    if not path.exists():
        return
    first = path.read_text(errors="replace").split("\n", 1)[0]
    mine = MARKER_RE.match(first)
    if mine and not pid_alive(int(mine.group(1))):
        path.unlink()
        return
    if mine:
        raise OverrideError(f"{path} is in use by another gdh run (process {mine.group(1)}). Wait for it to finish.")
    raise OverrideError(f"{path} exists, and gdh needs to write its own there for this run (Godot takes these settings "
                        f"from the project only). gdh never changes a file it didn't write: move it away for the run, "
                        f"or put its settings in project.godot.")


class Override:
    def __init__(self, path):
        self.path = path

    def remove(self):
        """Remove the file, if it's still the one written for this run."""
        try:
            first = self.path.read_text(errors="replace").split("\n", 1)[0]
        except OSError:
            return
        mine = MARKER_RE.match(first)
        if mine and int(mine.group(1)) == os.getpid():
            self.path.unlink(missing_ok=True)


@contextmanager
def project_override(project, settings):
    """Write project/override.cfg with settings for the body's run; remove it on the way out, however that goes.
    The body calls .remove() once Godot has read it, to keep the window in which another run could see it short."""
    path = Path(project) / "override.cfg"
    check_free(path)
    override = Override(path)

    def stop(signum, _frame):
        raise SystemExit(128 + signum)

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            raise OverrideError(f"{path} appeared as gdh was about to write it: another run is using it.") from None
        with os.fdopen(fd, "w") as f:
            f.write(render(settings))
        yield override
    finally:
        override.remove()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
