"""gdh live restart, input logs and replays, --seed, --recipe, a session's own user data and live reload, against
testbed/restart/walker.tscn.

The walker draws three numbers from the global random number generator as it starts (rolls), moves the player right
at 120 px/s while ui_right is held and a random step up or down every frame, counts its frames (ticks) and the Go
button's clicks, adds step_size() to distance every frame, and reads and writes files in user://.
"""
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from conftest import TESTBED, gdh, gdh_json
from gdh.godot import pid_alive

SESSION = f"test-restart-{os.getpid()}"
SCENE = "res://restart/walker.tscn"
STATE = "[ticks, clicks, player.position, distance, rolls]"


def start(name, out, *args, check=True):
    return gdh("live", "start", "--project", TESTBED, "--scene", SCENE, "--session", name, "--out", out, *args,
               check=check)


def live(name, *args):
    return gdh_json("live", *args, "--session", name)


def value(name, expr):
    return live(name, "eval", expr)["result"]["value"]


def session_file(name):
    from gdh.live import session_path
    return json.loads(session_path(name).read_text())


def run(*args, stdin=None, cwd=None):
    """gdh ARGS, unchecked, with stdin, in cwd."""
    return subprocess.run([sys.executable, "-m", "gdh", *map(str, args)], input=stdin, capture_output=True, text=True,
                          timeout=600, cwd=cwd)


def test_restart_comes_back_with_the_same_options_and_companions(tmp_path):
    name = f"{SESSION}-same"
    start(name, tmp_path, "--resolution", "800x600", "--seed", "11", "--companion", "srv=sleep 600",
          "--companion-ready", "srv=none", "--", "--level", "3", "--srv", "{srv.port}")
    try:
        first = session_file(name)
        rolls = value(name, "rolls")
        live(name, "step", "20", "--hold", "ui_right")
        proc = run("live", "restart", "--session", name, cwd=tmp_path)  # from anywhere
        assert proc.returncode == 0 and "held at frame 0" in proc.stdout
        again = session_file(name)
        assert again["pid"] != first["pid"] and not pid_alive(first["pid"])
        # The companion started again, and its new port reached the game's arguments.
        old, new = first["companions"][0], again["companions"][0]
        assert new["pid"] != old["pid"] and pid_alive(new["pid"]) and not pid_alive(old["pid"])
        assert value(name, "OS.get_cmdline_user_args()") == ["--level", "3", "--srv", str(new["port"])]
        assert live(name, "status")["result"]["window_size"] == [800, 600]
        assert value(name, "[ticks, rolls]") == [0, rolls]
        # A session that has ended starts again too.
        gdh("live", "stop", "--session", name)
        gdh("live", "restart", "--session", name)
        assert value(name, "rolls") == rolls
    finally:
        gdh("live", "stop", "--session", name)


def test_restart_replay_returns_to_the_same_frame_and_state(tmp_path):
    name = f"{SESSION}-replay"
    start(name, tmp_path / "a")
    try:
        live(name, "step", "20", "--hold", "ui_right")
        live(name, "step", "4", "--click-text", "Go")
        assert live(name, "step", "--until", "ticks >= 40", "--hold", "ui_right", "--max", "300")["result"]["frames"] == 16
        live(name, "eval", "set('distance', 500.0)")
        live(name, "run")
        time.sleep(0.5)
        live(name, "pause")
        pipe = run("live", "pipe", "--session", name, stdin=json.dumps(
            {"cmd": "step", "args": {"frames": 7, "events": [{"action": "ui_right", "pressed": True, "at": 0},
                                                            {"action": "ui_right", "pressed": False, "at": 7}]}}) + "\n")
        assert json.loads(pipe.stdout)["ok"]
        assert run("live", "batch", "--session", name, stdin="step 3 --hold ui_right\n").returncode == 0
        frame = live(name, "status")["result"]["frame"]
        state = value(name, STATE)
        assert state[1] == 1 and state[3] > 500

        log = tmp_path / "a" / "inputs.jsonl"
        header, *entries = [json.loads(line) for line in log.read_text().splitlines()]
        assert header["gdh_input_log"] == 1 and isinstance(header["seed"], int)
        assert [e["cmd"] for e in entries].count("step") == 5
        assert {"cmd": "eval", "args": {"expr": "set('distance', 500.0)"}} == {k: entries[3][k] for k in ("cmd", "args")}
        assert entries[2]["ran"] == 16 and entries[2]["frame"] == 40

        proc = gdh("live", "restart", "--session", name, "--replay")
        assert f"held at frame {frame}" in proc.stdout.splitlines()[-1]
        assert live(name, "status")["result"]["frame"] == frame
        assert value(name, STATE) == state
        assert (tmp_path / "a" / "inputs-before-restart.jsonl").exists()

        # The steps as a scenario would hold them: the frames each ran, the gap `run` let pass as a step of its own.
        from gdh.restart import logged_steps
        steps = logged_steps(name)
        assert sum(s["step"] for s in steps if "step" in s) == frame
        assert {"request": "eval", "args": {"expr": "set('distance', 500.0)"}} in steps

        # A saved log replays into a new session, with its seed.
        other = f"{name}-other"
        try:
            start(other, tmp_path / "b", "--replay", log)
            assert value(other, STATE) == state
        finally:
            gdh("live", "stop", "--session", other)
    finally:
        gdh("live", "stop", "--session", name)


def test_a_replay_steps_the_frames_each_step_ran():
    from gdh.restart import replay_plan
    press, release = {"action": "a", "pressed": True}, {"action": "a", "pressed": False}
    entries = [
        # --until held after 20 of 100 frames: the events then and at the end went, the one at 50 never did.
        {"cmd": "step", "args": {"frames": 100, "until": "x", "every": 1, "shot_every": 5,
                                 "events": [{**press, "at": 0}, {"action": "b", "pressed": True, "at": 20},
                                            {"action": "c", "pressed": True, "at": 50}, {**release, "at": 100}]},
         "instance": "0", "frame": 20, "ran": 20},
        {"cmd": "run", "args": {}, "instance": 0, "frame": 20},
        {"cmd": "pause", "args": {}, "instance": 0, "frame": 50},
        {"cmd": "camera", "args": {"release": True}, "instance": "all", "frame": 50},
    ]
    plan, end = replay_plan(entries)
    assert plan == [("step", {"frames": 20, "events": [{**press, "at": 0}, {"action": "b", "pressed": True, "at": 20},
                                                       {**release, "at": 20}]}, "0"),
                    ("step", {"frames": 30, "events": []}, 0),
                    ("camera", {"release": True}, "all")]
    assert end == 50


def test_seed_repeats_random_numbers_across_starts(tmp_path):
    name = f"{SESSION}-seed"
    draws = []
    try:
        for seed in ("5", "5", "6"):
            start(name, tmp_path, "--seed", seed)
            live(name, "step", "10")
            draws.append([value(name, "rolls"), value(name, "player.position"), value(name, "[randi(), randi()]")])
            gdh("live", "stop", "--session", name)
        assert draws[0] == draws[1]
        assert draws[2][0] != draws[0][0] and draws[2][1] != draws[0][1]
        # Without --seed, gdh picks one, and a restart keeps it.
        start(name, tmp_path)
        rolls = value(name, "rolls")
        gdh("live", "restart", "--session", name)
        assert value(name, "rolls") == rolls != draws[0][0]
    finally:
        gdh("live", "stop", "--session", name)


def test_recipe_runs_after_start_and_stops_at_a_failing_line(tmp_path):
    name = f"{SESSION}-recipe"
    recipe = tmp_path / "hangar.txt"
    recipe.write_text("# get to the button\nstep 10 --hold ui_right\n\nstep 2 --click-text Go\neval clicks\n")
    try:
        proc = start(name, tmp_path / "out", "--recipe", recipe)
        assert "> step 2 --click-text Go" in proc.stdout and "clicked UI/GoButton" in proc.stdout
        assert value(name, "[ticks, clicks]") == [12, 1]
        # A restart runs it again.
        gdh("live", "restart", "--session", name)
        assert value(name, "[ticks, clicks]") == [12, 1]
        gdh("live", "stop", "--session", name)

        recipe.write_text("step 5\nstep --until 'ticks > 1000' --max 3\nstep 50\n")
        proc = start(name, tmp_path / "out", "--recipe", recipe, check=False)
        assert proc.returncode == 1
        assert f"The recipe {recipe} failed at line 2" in proc.stderr and "didn't hold in 3 frames" in proc.stderr
        assert "> step 50" not in proc.stdout
        # The session stays, held where the recipe stopped.
        assert value(name, "ticks") == 8
        gdh("live", "stop", "--session", name)

        # A line that isn't a command stops the start before the game starts.
        recipe.write_text("step 5\nfly 3\n")
        proc = start(name, tmp_path / "out", "--recipe", recipe, check=False)
        assert proc.returncode == 1 and f"{recipe} line 2:" in proc.stderr
        assert run("live", "status", "--session", name).returncode == 1
    finally:
        gdh("live", "stop", "--session", name)


def test_user_data_fresh_and_from_give_the_session_its_own(tmp_path):
    from gdh.restart import project_user_dir
    name = f"{SESSION}-user"
    out = tmp_path / "out"
    fixture = tmp_path / "fixture"
    (fixture / "saves").mkdir(parents=True)
    (fixture / "saves" / "slot1.txt").write_text("level 4")
    try:
        start(name, out, "--user-data", "fresh")
        own = project_user_dir(TESTBED, out / "user-data")
        assert value(name, "OS.get_user_data_dir()") == str(own)
        assert value(name, "write_user('note.txt', 'hi')") is True
        assert (own / "note.txt").read_text() == "hi"
        # Its shader caches are the shared user data's.
        assert (own / "shader_cache").is_symlink() and (own / "vulkan").is_symlink()
        gdh("live", "restart", "--session", name)
        assert value(name, "read_user('note.txt')") == ""
        gdh("live", "stop", "--session", name)

        start(name, out, "--user-data-from", fixture)
        assert value(name, "read_user('saves/slot1.txt')") == "level 4"
        value(name, "write_user('saves/slot1.txt', 'level 5')")
        assert (fixture / "saves" / "slot1.txt").read_text() == "level 4"
        gdh("live", "restart", "--session", name)
        assert value(name, "read_user('saves/slot1.txt')") == "level 4"
        gdh("live", "stop", "--session", name)

        # Without either, the shared user data, which the others left alone.
        start(name, out)
        assert value(name, "read_user('note.txt')") == ""
        assert value(name, "OS.get_user_data_dir()") != str(own)
    finally:
        gdh("live", "stop", "--session", name)


def test_project_user_dir_follows_godots_naming(tmp_path):
    from gdh.restart import project_user_dir
    (tmp_path / "project.godot").write_text('[application]\n\nconfig/name="Void: Sector?"\n')
    assert str(project_user_dir(tmp_path, "/d")) == "/d/godot/app_userdata/Void- Sector-"
    (tmp_path / "project.godot").write_text('[application]\n\nconfig/name="Game"\nconfig/use_custom_user_dir=true\n'
                                            'config/custom_user_dir_name="studio/game"\n')
    assert str(project_user_dir(tmp_path, "/d")) == "/d/studio/game"


@pytest.fixture
def copy(tmp_path):
    """A project of its own holding the walker, whose script a test can change."""
    project = tmp_path / "project"
    shutil.copytree(TESTBED / "restart", project / "restart")
    (project / "project.godot").write_text('config_version=5\n\n[application]\n\nconfig/name="gdh reload test"\n'
                                           f'run/main_scene="{SCENE}"\n')
    return project


def test_reload_puts_changed_scripts_in_and_keeps_state(copy, tmp_path):
    name = f"{SESSION}-reload"
    script = copy / "restart" / "walker.gd"
    source = script.read_text()
    gdh("live", "start", "--project", copy, "--session", name, "--out", tmp_path / "out")
    try:
        live(name, "step", "10", "--hold", "ui_right")
        assert "no loaded script has changed" in gdh("live", "reload", "--session", name).stdout
        script.write_text(source.replace("return 1.0", "return 5.0").replace("var clicks := 0",
                                                                             "var clicks := 0\nvar bonus := 7"))
        proc = gdh("live", "reload", "--session", name)
        assert "reloaded res://restart/walker.gd" in proc.stdout and "new members bonus" in proc.stdout
        live(name, "step", "10")
        assert value(name, "[ticks, distance, bonus, player.position.x]") == [20, 60.0, 7, 220.0]
        # A script that doesn't compile leaves the one that ran.
        script.write_text(source.replace("return 1.0", "return 1.0 +"))
        proc = run("live", "reload", "--session", name)
        assert proc.returncode == 1 and "doesn't compile" in proc.stdout and "Parse Error" in proc.stderr
        live(name, "step", "10")
        assert value(name, "[ticks, distance]") == [30, 110.0]
    finally:
        gdh("live", "stop", "--session", name)
