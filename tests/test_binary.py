"""--binary sessions and gdh export --smoke: a build of the testbed's clicker (testbed/binary), and any X program, run
as they are on gdh's display, seen with shot, driven through XTest, waited on, and stopped with nothing left.

Two builds of the clicker run: Godot's release export template beside the clicker's pack, as gdh export writes a Linux
build (skipped without export templates for this Godot version), and gdh's Godot running the pack (--main-pack), which
needs none."""
import json
import os
import shutil
import time
from pathlib import Path

import pytest
from PIL import Image

from conftest import TESTBED, gdh
from gdh import blackbox, x11
from gdh.api import godot_version
from gdh.export import templates_dir
from gdh.godot import pid_alive
from gdh.live import LiveError, session_path

SESSION = f"test-binary-{os.getpid()}"
GODOT = shutil.which("godot")
DARK, GREEN = (25, 25, 76), (25, 204, 51)
BUTTON, FIELD = "640,360", "640,460"
PROJECT = 'config_version=5\n\n[application]\n\nconfig/name="clicker"\nrun/main_scene="res://binary/clicker.tscn"\n'
PRESETS = ('[preset.0]\n\nname="Linux"\nplatform="Linux"\nrunnable=true\nexport_filter="all_resources"\n'
           'include_filter=""\nexclude_filter=""\nexport_path="build/clicker.x86_64"\n\n[preset.0.options]\n\n'
           'binary_format/architecture="x86_64"\n')
ENGINE = ["--display-driver", "x11", "--audio-driver", "Dummy", "--resolution", "1280x720"]


def release_template():
    """Godot's Linux release export template for the installed version (the .NET build's will do), or None."""
    version = godot_version(GODOT)
    for csharp in (False, True):
        template = templates_dir(version, csharp) / "linux_release.x86_64"
        if template.is_file():
            return template
    return None


@pytest.fixture(scope="session")
def clicker(tmp_path_factory):
    """A project of testbed/binary with a Linux preset, and its pack."""
    project = tmp_path_factory.mktemp("clicker") / "game"
    shutil.copytree(TESTBED / "binary", project / "binary")
    (project / "project.godot").write_text(PROJECT)
    (project / "export_presets.cfg").write_text(PRESETS)
    gdh("export", "--project", project, "--preset", "Linux", "--pack")
    return project


@pytest.fixture(scope="session")
def pack(clicker):
    """How to start the clicker on gdh's Godot: {"binary", "options", "args"}, the user's arguments after args."""
    return {"binary": GODOT, "options": ["--raw"], "export": False,
            "args": ["--main-pack", clicker / "build" / "clicker.pck", *ENGINE, "--"]}


@pytest.fixture(scope="session", params=["export", "pack"])
def build(request, clicker, pack, tmp_path_factory):
    """The clicker as an export (Godot's release template beside its pack), and on gdh's Godot."""
    if request.param == "pack":
        yield pack
        return
    template = release_template()
    if template is None:
        pytest.skip("no Godot export templates for this version")
    directory = tmp_path_factory.mktemp("export")
    binary = directory / "clicker.x86_64"
    shutil.copy(template, binary)
    shutil.copy(clicker / "build" / "clicker.pck", directory / "clicker.pck")
    yield {"binary": binary, "options": [], "args": [], "export": True}
    binary.unlink()


def start(how, name, tmp_path, *user, options=()):
    gdh("live", "start", "--binary", how["binary"], *how["options"], "--session", name, "--out", tmp_path / name,
        *options, "--", *how["args"], *user)
    return json.loads(session_path(name).read_text())


def wait_log(name, regex, timeout=30):
    return gdh("live", "wait", "--log", regex, "--timeout", timeout, "--session", name).stdout


def shot(name):
    path = gdh("live", "shot", "--session", name).stdout.split("normal: ")[1].strip()
    return Image.open(path)


def near(pixel, color):
    return all(abs(a - b) <= 4 for a, b in zip(pixel, color))


def wait_for(condition, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.2)
    return False


def assert_nothing_left(session):
    """The program, its runner, the watchdog and the display have all stopped, and the session is gone."""
    display = session["instances"][0]["display"]
    pids = [session["pid"], session["runner"], session["watchdog"], *display["groups"]]
    assert wait_for(lambda: not any(pid_alive(p) for p in pids)), [p for p in pids if pid_alive(p)]
    assert not session_path(session["name"]).exists()
    if display.get("runtime_dir"):
        assert not Path(display["runtime_dir"]).exists()


def test_an_export_takes_a_click_that_changes_what_it_draws(build, tmp_path):
    name = f"{SESSION}-click"
    session = start(build, name, tmp_path)
    try:
        ready = wait_log(name, "clicker: ready")
        assert f"export {str(build['export']).lower()}" in ready
        assert near(shot(name).getpixel((20, 20)), DARK)
        sent = gdh("live", "input", "--click", BUTTON, "--session", name).stdout
        assert 'on window 1280x720 at 0,0 "clicker' in sent
        # One click: gdh gives the window the focus first, which Godot would otherwise take from the click.
        assert "clicker: clicked 1" in wait_log(name, "clicker: clicked")
        assert near(shot(name).getpixel((20, 20)), GREEN)
        status = json.loads(gdh("live", "status", "--session", name, "--json").stdout)
        assert status["running"] and status["export"] == build["export"]
        assert [(w["width"], w["height"]) for w in status["windows"]] == [(1280, 720)]
    finally:
        gdh("live", "stop", "--session", name)
    assert_nothing_left(session)


def test_keys_text_and_the_wheel_reach_it(pack, tmp_path):
    name = f"{SESSION}-keys"
    start(pack, name, tmp_path)
    try:
        wait_log(name, "clicker: ready")
        gdh("live", "input", "--click", FIELD, "--type", "Hi there!", "--key", "Return", "--move", "300,300",
            "--wheel", "up:2", "--right-click", "100,100", "--key", "ctrl+a", "--session", name)
        assert "clicker: typed Hi there!" in wait_log(name, "clicker: typed")
        assert "wheel up" in wait_log(name, "wheel up")
        assert "right click at 100,100" in wait_log(name, "right click")
        assert "Ctrl+A" in wait_log(name, "key Ctrl\\+A")
        # A position off the screen, or an unknown key, sends nothing.
        for bad in (["--click", "5000,10"], ["--key", "NoSuchKey"]):
            proc = gdh("live", "input", "--key", "Escape", *bad, "--session", name, check=False)
            assert proc.returncode == 1
        assert "key Escape" not in Path(tmp_path / name / "program.log").read_text()
    finally:
        gdh("live", "stop", "--session", name)


def test_wait_log_returns_when_the_line_appears_and_times_out(pack, tmp_path):
    name = f"{SESSION}-wait"
    start(pack, name, tmp_path, "--exit-after", "8")
    try:
        assert wait_log(name, "clicker: ready").strip() == "clicker: ready, export false"
        # The next --log looks after the line the last one matched.
        began = time.monotonic()
        late = gdh("live", "wait", "--log", "clicker: ready", "--timeout", "1", "--session", name, check=False)
        assert late.returncode == 1 and "No line matching 'clicker: ready'" in late.stderr
        assert 0.9 < time.monotonic() - began < 6
        assert "window 1280x720" in gdh("live", "wait", "--window", "--session", name).stdout
        assert gdh("live", "wait", "--seconds", "0.5", "--session", name).returncode == 0
        # A line written as it quits is found, after it has quit.
        assert "quitting" in wait_log(name, "quitting")
        assert "exited with code 0" in gdh("live", "wait", "--exit", "--session", name).stdout
    finally:
        gdh("live", "stop", "--session", name)


def test_an_exit_is_reported_with_its_code_and_log(pack, tmp_path):
    name = f"{SESSION}-exit"
    session = start(pack, name, tmp_path, "--exit-after", "1", "--code", "3")
    try:
        ended = gdh("live", "wait", "--exit", "--session", name).stdout
        assert "the program exited with code 3" in ended and "clicker: quitting with code 3" in ended
        status = gdh("live", "status", "--session", name)
        assert "exited with code 3" in status.stdout and "quitting with code 3" in status.stdout
        assert json.loads(gdh("live", "status", "--session", name, "--json").stdout)["exit"]["code"] == 3
        # Commands that need it running say how it ended.
        refused = gdh("live", "shot", "--session", name, check=False)
        assert refused.returncode == 1 and "exited with code 3" in refused.stderr
        assert "quitting with code 3" in refused.stderr
    finally:
        gdh("live", "stop", "--session", name)
    assert_nothing_left(session)


def test_a_killed_program_is_reported_with_its_signal(pack, tmp_path):
    name = f"{SESSION}-killed"
    session = start(pack, name, tmp_path, "--kill-after", "1")
    try:
        waited = gdh("live", "wait", "--seconds", "20", "--session", name, check=False)
        assert waited.returncode == 1
        assert "was ended with signal 9 (SIGKILL)" in waited.stderr and "clicker: killing itself" in waited.stderr
        assert json.loads(gdh("live", "status", "--session", name, "--json").stdout)["exit"]["code"] == -9
    finally:
        gdh("live", "stop", "--session", name)
    assert_nothing_left(session)


def test_commands_that_need_the_harness_say_so(pack, tmp_path):
    name = f"{SESSION}-harness"
    start(pack, name, tmp_path)
    try:
        for command in (["eval", "1"], ["step", "1"], ["tree"], ["probes"], ["shot", "--view", "wireframe"]):
            proc = gdh("live", *command, "--session", name, check=False)
            assert proc.returncode == 1 and "harness" in proc.stderr, command
    finally:
        gdh("live", "stop", "--session", name)


def test_the_program_is_kept_off_the_users_session(pack, tmp_path):
    name = f"{SESSION}-isolated"
    session = start(pack, name, tmp_path, "--open", "https://example.com/")
    try:
        assert "clicker: opening https://example.com/: OK" in wait_log(name, "clicker: opening")
        env = dict(item.split("=", 1) for item in
                   Path(f"/proc/{session['pid']}/environ").read_text().split("\0") if "=" in item)
        assert env["DISPLAY"] == session["instances"][0]["display"]["name"]
        assert env["WAYLAND_DISPLAY"] == "gdh-no-wayland"
        assert env["XDG_RUNTIME_DIR"].startswith(session["shims"]) and not os.listdir(env["XDG_RUNTIME_DIR"])
        assert env["DBUS_SESSION_BUS_ADDRESS"].startswith(f"unix:path={session['shims']}")
        assert env["PATH"].split(":")[0] == session["shims"] and env["DISABLE_MANGOHUD"] == "1"
        assert env["XDG_DATA_HOME"].endswith("gdh/user-data")
        # The URL went to the stand-in, which logged it, and no browser started.
        assert "gdh: xdg-open https://example.com/" in Path(session["log"]).read_text()
        assert json.loads(gdh("live", "status", "--session", name, "--json").stdout)["spawned"] == []
    finally:
        gdh("live", "stop", "--session", name)


def test_keep_children_drives_what_a_launcher_hands_off_to(pack, tmp_path):
    launcher = tmp_path / "launcher.sh"
    launcher.write_text('#!/bin/sh\necho "launcher: handing off"\n"$@" &\nexit 0\n')
    launcher.chmod(0o755)
    name = f"{SESSION}-launcher"
    session = start({**pack, "binary": launcher, "args": [GODOT, *pack["args"]]}, name, tmp_path,
                    options=["--keep-children"])
    try:
        wait_log(name, "clicker: ready")
        status = json.loads(gdh("live", "status", "--session", name, "--json").stdout)
        assert not status["running"] and status["exit"]["code"] == 0
        [child] = [p["pid"] for p in status["spawned"]]
        gdh("live", "input", "--click", BUTTON, "--session", name)
        wait_log(name, "clicker: clicked 1")
        assert near(shot(name).getpixel((20, 20)), GREEN)
        assert "the session lasts while the processes it spawned run" in gdh("live", "status", "--session", name).stdout
    finally:
        gdh("live", "stop", "--session", name)
    assert wait_for(lambda: not pid_alive(child))
    assert_nothing_left(session)


def test_any_x_program_runs_and_is_driven(tmp_path):
    if not shutil.which("xmessage"):
        pytest.skip("needs xmessage")
    name = f"{SESSION}-xmessage"
    started = gdh("live", "start", "--binary", "xmessage", "--session", name, "--out", tmp_path, "--",
                  "-center", "-default", "okay", "-buttons", "okay:7,no:4", "hello from gdh").stdout
    try:
        assert "run as it is" in started and '"xmessage"' in started
        gdh("live", "input", "--key", "Return", "--session", name)
        assert "exited with code 7" in gdh("live", "wait", "--exit", "--session", name).stdout
    finally:
        gdh("live", "stop", "--session", name)


def test_a_program_that_ends_before_its_window_fails_start(tmp_path):
    name = f"{SESSION}-early"
    proc = gdh("live", "start", "--binary", "sh", "--session", name, "--out", tmp_path, "--",
               "-c", "echo going; exit 4", check=False)
    assert proc.returncode == 1
    assert "exited with code 4" in proc.stderr and "before it showed a window" in proc.stderr and "going" in proc.stderr
    assert not session_path(name).exists()
    missing = gdh("live", "start", "--binary", tmp_path / "nothing", "--session", name, check=False)
    assert missing.returncode == 1 and "No program" in missing.stderr


def test_the_idle_timeout_stops_the_program(tmp_path):
    name = f"{SESSION}-idle"
    gdh("live", "start", "--binary", "sh", "--no-wait", "--idle-timeout", "2", "--session", name, "--out", tmp_path,
        "--", "-c", "echo idling; sleep 60")
    session = json.loads(session_path(name).read_text())
    try:
        assert wait_for(lambda: not pid_alive(session["pid"]), 10)
        status = json.loads(gdh("live", "status", "--session", name, "--json").stdout)
        assert status["exit"]["idle"]
        assert "--idle-timeout" in gdh("live", "status", "--session", name).stdout
    finally:
        gdh("live", "stop", "--session", name)
    assert_nothing_left(session)


def test_export_smoke_passes_a_good_build_and_fails_a_bad_one(clicker, tmp_path):
    def smoke(*user):
        return gdh("export", "--project", clicker, "--preset", "Linux", "--pack", "--smoke", "2",
                   "--smoke-out", tmp_path / "smoke", *(["--", *user] if user else []), check=False)

    good = smoke()
    assert good.returncode == 0 and "smoke: passed: ran 2 s" in good.stdout
    assert near(Image.open(tmp_path / "smoke" / "smoke.png").getpixel((20, 20)), DARK)
    erring = smoke("--error")
    assert erring.returncode == 1 and "1 engine error in its log" in erring.stdout
    assert "clicker: an error as it starts" in erring.stdout
    early = smoke("--exit-after", "0.2")
    assert early.returncode == 1 and "exited with code 0" in early.stdout and "before 2 s" in early.stdout


def test_export_smoke_runs_a_real_export(clicker, tmp_path):
    if not templates_dir(godot_version(GODOT), False).is_dir():
        pytest.skip("no Godot export templates for this version")
    proc = gdh("export", "--project", clicker, "--preset", "Linux", "--smoke", "2", "--smoke-out", tmp_path, check=False)
    assert proc.returncode == 0 and "smoke: passed" in proc.stdout, proc.stdout + proc.stderr


def test_exports_are_told_from_other_programs_and_endings_described(tmp_path):
    embedded = tmp_path / "embedded.x86_64"
    embedded.write_bytes(b"\x7fELF...pack...GDPC")
    beside = tmp_path / "game.x86_64"
    beside.write_bytes(b"\x7fELF")
    (tmp_path / "game.pck").write_bytes(b"GDPC")
    other = tmp_path / "tool"
    other.write_bytes(b"\x7fELF")
    assert blackbox.export_pack(embedded) == embedded
    assert blackbox.export_pack(beside) == tmp_path / "game.pck"
    assert blackbox.export_pack(other) is None
    assert blackbox.command(other, ["--a"], "640x360", None) == [str(other), "--a"]
    assert blackbox.command(beside, ["--a"], "640x360", beside)[-2:] == ["--", "--a"]
    log = tmp_path / "log"
    log.write_text("handle_crash: Program crashed with signal 11\n")
    assert blackbox.describe_exit({"code": -11, "seconds": 1, "idle": False}, log).startswith("crashed with signal 11 "
                                                                                            "(SIGSEGV)")
    assert blackbox.describe_exit({"code": -15, "seconds": 1, "idle": False}, tmp_path / "none").startswith(
        "was ended with signal 15 (SIGTERM)")
    assert x11.parse_key("ctrl+s") == [0xffe3, ord("s")]
    assert x11.parse_key("Enter") == [0xff0d]
    with pytest.raises(LiveError):
        blackbox.plan([("click", "10,5000")], (1280, 720))
    with pytest.raises(x11.X11Error):
        x11.parse_key("NoSuchKey")
