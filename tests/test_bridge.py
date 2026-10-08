"""gdh bridge and the Claude Code hooks: gdh's headless editor runs the bridge with nothing installed in the project,
works through the editor (UIDs, checks, exec, open, save, reload), puts the project's editor state back when it stops;
the addon starts the same bridge from the project; and the hooks refuse edits that would fight the editor."""
import json
import os
import shutil
import subprocess
import sys

import pytest

from conftest import GPU_DISPLAY, ROOT, TESTBED, gdh

HOOK = ROOT / "hooks" / "gdh_hook.py"
PLAYER = 'extends Node2D\n\nfunc _ready() -> void:\n\tprint("ready")\n'
MAIN = ('[gd_scene format=3]\n\n[ext_resource type="Script" path="res://player.gd" id="1"]\n\n'
        '[node name="Main" type="Node2D"]\nscript = ExtResource("1")\n')
MOVE_ROOT = ('extends RefCounted\n\nfunc run(editor):\n\tvar root = editor.get_edited_scene_root()\n'
             '\tvar ur = editor.get_editor_undo_redo()\n\tur.create_action("move")\n'
             '\tur.add_do_property(root, "position", Vector2(5, 5))\n'
             '\tur.add_undo_property(root, "position", root.position)\n\tur.commit_action()\n'
             '\tprint("moved ", root.name)\n\treturn {"name": root.name, "position": root.position}\n')


def make_project(directory):
    directory.mkdir(parents=True)
    (directory / "project.godot").write_text('config_version=5\n\n[application]\n\nconfig/name="Bridge"\n')
    (directory / "player.gd").write_text(PLAYER)
    (directory / "main.tscn").write_text(MAIN)
    return directory


def bridge(project, *args, check=True):
    proc = gdh("bridge", *args, "--project", project, "--json", check=False)
    if check and proc.returncode != 0:
        raise AssertionError(f"gdh bridge {' '.join(map(str, args))} failed:\n{proc.stdout}\n{proc.stderr}")
    return json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else proc


def hook(stage, tool, file_path, **tool_input):
    event = {"tool_name": tool, "tool_input": {"file_path": str(file_path), **tool_input}}
    proc = subprocess.run([sys.executable, str(HOOK), stage], input=json.dumps(event), capture_output=True, text=True,
                          timeout=200)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def denied(reply):
    return reply and reply["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.fixture(scope="module")
def editor_home(tmp_path_factory):
    home = tmp_path_factory.mktemp("bridge-editor-home")
    os.environ["GDH_EDITOR_HOME"] = str(home)
    yield home
    os.environ.pop("GDH_EDITOR_HOME", None)


@pytest.fixture(scope="module")
def project(tmp_path_factory, display, editor_home):
    """A small project with gdh's headless editor running the bridge. The bridge needs no display, so it runs once."""
    if display != "gpu" and os.environ.get("GDH_BRIDGE_TESTS_RAN"):
        pytest.skip("the bridge runs headless: once is enough")
    os.environ["GDH_BRIDGE_TESTS_RAN"] = "1"
    directory = make_project(tmp_path_factory.mktemp("bridge") / "game")
    (directory / ".godot" / "editor").mkdir(parents=True)
    (directory / ".godot" / "editor" / "marker.cfg").write_text("kept\n")
    gdh("bridge", "start", "--project", directory, "--idle-timeout", "900")
    yield directory
    gdh("bridge", "stop", "--project", directory, check=False)


def test_the_headless_editor_answers_and_installs_nothing(project):
    status = bridge(project, "status")
    assert status["ok"] and status["headless"]
    assert status["godot"].startswith("4.")
    assert not (project / "addons").exists()
    assert "editor_plugins" not in (project / "project.godot").read_text()


def test_scan_gives_a_new_script_its_uid(project):
    (project / "enemy.gd").write_text("extends Node\n")
    bridge(project, "scan", "res://enemy.gd")
    uid = (project / "enemy.gd.uid").read_text().strip()
    assert uid.startswith("uid://")
    assert bridge(project, "uid", "res://enemy.gd")["uids"]["res://enemy.gd"] == uid
    assert bridge(project, "uid", uid)["uids"][uid] == "res://enemy.gd"


def test_resave_fills_in_a_new_scenes_uids(project):
    (project / "level.tscn").write_text(MAIN.replace('"Main"', '"Level"'))
    bridge(project, "scan", "res://level.tscn")
    reply = bridge(project, "resave", "res://level.tscn")
    uid = reply["resaved"]["res://level.tscn"]
    text = (project / "level.tscn").read_text()
    assert text.startswith(f'[gd_scene format=3 uid="{uid}"]')
    script_uid = (project / "player.gd.uid").read_text().strip()
    assert f'uid="{script_uid}" path="res://player.gd"' in text


def test_check_returns_parse_errors_and_passes_good_scripts(project):
    (project / "broken.gd").write_text("extends Node\nfunc f(:\n")
    try:
        reply = bridge(project, "check", "res://broken.gd", "res://player.gd", check=False)
        assert reply["ok"] is False
        errors = reply["checked"]["res://broken.gd"]
        assert any("Parse Error" in e["message"] and "res://broken.gd:2" in e["where"] for e in errors)
        assert reply["checked"]["res://player.gd"] == []
    finally:
        (project / "broken.gd").unlink()
        bridge(project, "scan")


def test_exec_edits_the_open_scene_and_reload_spares_unsaved_work(project, tmp_path):
    bridge(project, "open", "res://main.tscn")
    assert bridge(project, "status")["current_scene"] == "res://main.tscn"
    script = tmp_path / "move.gd"
    script.write_text(MOVE_ROOT)
    reply = bridge(project, "exec", script)
    assert reply["result"]["name"] == "Main"
    assert reply["output"] == ["moved Main"]
    assert "res://main.tscn" in bridge(project, "status")["unsaved_scenes"]
    reply = bridge(project, "reload", "res://main.tscn")
    assert reply["reloaded"] == [] and "unsaved" in reply["skipped"]["res://main.tscn"]
    bridge(project, "save", "res://main.tscn")
    assert bridge(project, "status")["unsaved_scenes"] == []
    assert "position = Vector2(5, 5)" in (project / "main.tscn").read_text()


def test_exec_reports_a_script_that_doesnt_compile(project):
    reply = bridge(project, "exec", "-", check=False)  # empty stdin: no run()
    assert reply["ok"] is False


def test_hooks_refuse_edits_that_fight_the_editor(project):
    assert denied(hook("pre", "Write", project / ".godot" / "x.cfg", content="x"))
    assert denied(hook("pre", "Edit", project / "project.godot", old_string="a", new_string="b"))
    fake = hook("pre", "Write", project / "new.tscn", content='[gd_scene format=3 uid="uid://notreal123"]\n')
    assert denied(fake) and "uid://notreal123" in fake["hookSpecificOutput"]["permissionDecisionReason"]
    real = (project / "player.gd.uid").read_text().strip()
    assert hook("pre", "Edit", project / "main.tscn", old_string="x", new_string=real) is None
    assert hook("pre", "Write", project / "notes.txt", content="hello") is None
    # A scene with unsaved changes in the editor.
    bridge(project, "open", "res://main.tscn")
    move = project.parent / "move.gd"
    move.write_text(MOVE_ROOT.replace("Vector2(5, 5)", "Vector2(9, 9)"))
    bridge(project, "exec", move)
    try:
        assert denied(hook("pre", "Edit", project / "main.tscn", old_string="a", new_string="b"))
    finally:
        bridge(project, "save")


def test_hooks_after_an_edit_fill_in_uids_and_return_script_errors(project):
    scene = project / "room.tscn"
    scene.write_text(MAIN.replace('"Main"', '"Room"'))
    reply = hook("post", "Write", scene)
    assert "fill in its UID" in reply["hookSpecificOutput"]["additionalContext"]
    assert scene.read_text().startswith('[gd_scene format=3 uid="uid://')
    bad = project / "bad.gd"
    bad.write_text("extends Node\nfunc f(:\n")
    try:
        reply = hook("post", "Write", bad)
        assert reply["decision"] == "block" and "Parse Error" in reply["reason"]
    finally:
        bad.unlink()
        bridge(project, "scan")


def test_check_without_a_bridge_parses_headlessly(tmp_path, project):
    other = make_project(tmp_path / "plain")
    (other / "broken.gd").write_text("extends Node\nfunc f(:\n")
    reply = bridge(other, "check", "res://broken.gd", "res://player.gd", check=False)
    assert reply["ok"] is False and "no editor bridge" in reply["note"]
    assert any("Parse Error" in e["message"] for e in reply["checked"]["res://broken.gd"])
    assert reply["checked"]["res://player.gd"] == []
    reply = hook("post", "Write", other / "broken.gd")
    assert reply["decision"] == "block"
    assert denied(hook("pre", "Write", other / "x.tscn", content='[ext_resource uid="uid://madeup1"]'))


def test_check_without_a_bridge_knows_autoloads_and_class_names(tmp_path, display):
    """With no editor, the check loads scripts as the game does: an autoload (a script or a scene) and a global class
    name resolve, a class added since the last import too, and the autoloads never enter the tree."""
    if display == "xvfb" and GPU_DISPLAY:
        pytest.skip("headless: once is enough")
    reply = bridge(TESTBED, "check", "res://live/arena.gd")  # names the autoload GameState
    assert reply["ok"] and reply["checked"]["res://live/arena.gd"] == []
    assert hook("post", "Edit", TESTBED / "live" / "arena.gd") is None
    other = make_project(tmp_path / "autoloads")
    with open(other / "project.godot", "a") as f:
        f.write('\n[autoload]\n\nState="*res://state.gd"\nHud="*res://hud.tscn"\n')
    (other / "state.gd").write_text('extends Node\n\nvar jumps := 0\n\nfunc _ready() -> void:\n'
                                    '\tFileAccess.open("res://ready_ran.txt", FileAccess.WRITE).store_string("ran")\n')
    (other / "hud.gd").write_text("extends CanvasLayer\n\nvar score := 0\n\nfunc flash() -> void:\n\tpass\n")
    (other / "hud.tscn").write_text('[gd_scene format=3]\n\n[ext_resource type="Script" path="res://hud.gd" id="1"]\n\n'
                                    '[node name="Hud" type="CanvasLayer"]\nscript = ExtResource("1")\n')
    (other / "enemy.gd").write_text("class_name Enemy\nextends Node2D\n\nvar hp := 3\n")
    (other / "uses.gd").write_text("extends Node\n\nfunc _ready() -> void:\n\tState.jumps += 1\n\tHud.score += 1\n"
                                   "\tHud.flash()\n\tvar e := Enemy.new()\n\te.hp -= 1\n")
    (other / "bad.gd").write_text("extends Node\n\nfunc _ready() -> void:\n\tNoSuchThing.go()\n")
    reply = bridge(other, "check", "res://uses.gd", "res://bad.gd", "res://state.gd", check=False)
    assert reply["ok"] is False
    assert reply["checked"]["res://uses.gd"] == [] and reply["checked"]["res://state.gd"] == []
    assert any('"NoSuchThing" not declared' in e["message"] and "res://bad.gd:4" in e["where"]
               for e in reply["checked"]["res://bad.gd"])
    assert not (other / "ready_ran.txt").exists()
    # A class added since the import: the check imports first, so the class cache knows it.
    (other / "boss.gd").write_text("@tool\nclass_name Boss extends Enemy\n\nvar rage := 1\n")
    (other / "fight.gd").write_text("extends Node\n\nfunc _ready() -> void:\n\tvar b := Boss.new()\n"
                                    "\tb.rage += State.jumps + b.hp\n")
    reply = bridge(other, "check", "res://fight.gd")
    assert reply["ok"] and reply["checked"]["res://fight.gd"] == []


def test_the_addon_runs_the_bridge_and_stop_puts_editor_state_back(tmp_path, project):
    other = make_project(tmp_path / "with-addon")
    gdh("bridge", "install", "--enable", "--project", other)
    assert (other / "addons" / "gdh_bridge" / "server.gd").exists()
    assert 'res://addons/gdh_bridge/plugin.cfg' in (other / "project.godot").read_text()
    gdh("bridge", "start", "--project", other, "--idle-timeout", "120")
    try:
        assert bridge(other, "status")["ok"]
    finally:
        gdh("bridge", "stop", "--project", other)
    assert not (other / ".godot" / "gdh_bridge.json").exists()
    # The module's editor: stop it and check its state came back, then start it again for the tests after.
    gdh("bridge", "stop", "--project", project)
    assert (project / ".godot" / "editor" / "marker.cfg").read_text() == "kept\n"
    gdh("bridge", "start", "--project", project, "--idle-timeout", "900")



def test_a_peek_counts_errors_and_leaves_them_for_the_next_command(project):
    """Claude Code's band polls with a peek; the errors stay for Claude's next bridge command."""
    import time
    from gdh import editor_bridge
    bridge(project, "errors")
    # An error raised after exec's reply: a deferred lookup of a node that doesn't exist.
    later = ('extends RefCounted\n\nfunc run(editor):\n'
             '\teditor.get_base_control().get_node.call_deferred("NoSuchNodeForGdh")\n\treturn 1\n')
    editor_bridge.call(project, {"cmd": "exec", "code": later})
    time.sleep(0.5)
    peek = editor_bridge.call(project, {"cmd": "status", "peek": True})
    assert "errors" not in peek and peek["pending_errors"] >= 1
    errors = editor_bridge.call(project, {"cmd": "errors"})["errors"]
    assert any("NoSuchNodeForGdh" in e["message"] for e in errors)
    assert editor_bridge.call(project, {"cmd": "status", "peek": True})["pending_errors"] == 0
