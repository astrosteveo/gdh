---
name: godot-editor
description: Change a Godot 4.7+ project without fighting the Godot editor, and keep its UIDs right. Use whenever you create, edit, move or delete scenes (.tscn), resources (.tres), GDScript, shaders or project settings in a folder with project.godot; whenever uid:// references, .uid files or unique_id come up; and when the user wants a scene opened, saved or run in their editor. Covers gdh bridge (a live link into the editor), editing open scenes so the user can undo, and what the gdh hooks refuse and why.
---

# Changing a Godot project with the editor open

The Godot editor keeps open scenes in memory and owns the project's UIDs. Edit files behind its back and it can
overwrite your change on its next save, overwrite the user's unsaved work, or leave references pointing at nothing.
`gdh bridge` is a live link into the editor, so you work through it instead. The `gdh` skill covers seeing and
playing the game; this one covers changing it.

Run gdh as the `gdh` skill's Setup says (`gdh`, or `uv run --project "${CLAUDE_PLUGIN_ROOT}" gdh`). Every `gdh bridge`
command takes `--project DIR`, or finds project.godot above the current directory.

## Set up the bridge

Run `gdh bridge status`.

- **It answers** with `headless: false`: the user's editor is open with the bridge. Use it.
- **It answers** with `headless: true`: it's gdh's own headless editor. Fine for UIDs, checks and edits; the user
  can't see it.
- **Exit 3, no bridge.** Ask the user whether they have the project open in the Godot editor.
  - If yes: offer to install the addon (`gdh bridge install`), then ask them to enable **gdh bridge** in
    Project > Project Settings > Plugins. Don't edit project.godot under an open editor. Until it's on, follow
    "Without the bridge" below.
  - If no: `gdh bridge start` runs gdh's own headless editor with the bridge and installs nothing in the project. It
    quits after 30 idle minutes; `gdh bridge stop` ends it sooner. Stop it before the user opens the project
    themselves.

The addon is a dev tool. Ask before adding it, and mention `addons/gdh_bridge/` can go in `.gitignore`.

## Before changing a file

Look at `open_scenes` and `unsaved_scenes` in `gdh bridge status`.

| The file is... | Do this |
| --- | --- |
| Not open in the editor | Edit it on disk. The hooks rescan it afterwards. |
| Open, no unsaved changes | Edit it on disk; the hooks reload it in the editor. Or edit it live with `exec`. |
| Open, with unsaved changes | Edit it live with `exec`, on top of the user's changes. Never `reload --force`: it throws their work away. |
| `project.godot`, editor open | Use `exec`: `ProjectSettings.set_setting(...)` then `ProjectSettings.save()`. |
| Anything in `.godot/` | Never touch it. It's the editor's cache. |

Moving, renaming or deleting with shell commands isn't seen by the hooks: run `gdh bridge scan` afterwards. If the
user's editor is open, prefer asking them to move files in the FileSystem dock, which fixes every reference.

## UIDs

Godot 4 refers to files by UID (`uid://c4dwtq6e4fyme`) as well as by path. A UID keeps working when the file moves.

- **Never make up a UID or a node `unique_id`.** The pre-edit hook refuses a `uid://` the project doesn't have.
- **Writing a new scene or resource:** leave `uid=` out of the header, and out of every `ext_resource` whose UID you
  don't know. `path=` alone works:

  ```
  [gd_scene format=3]

  [ext_resource type="Script" path="res://player.gd" id="1"]

  [node name="Main" type="Node2D"]
  script = ExtResource("1")
  ```

  With a bridge running, the post-edit hook has Godot save it again (`gdh bridge resave`), which fills in the
  header's UID and each `ext_resource`'s. The file changes on disk, so read it again before your next edit. Nodes get
  their `unique_id` the next time the editor itself saves the scene.
- **Where UIDs live:** a script's or shader's in a `.uid` sidecar (`player.gd.uid`); an imported asset's in its
  `.import` file; a scene's or resource's in its first line. `gdh bridge uid res://x.gd` looks one up, and
  `gdh bridge uid uid://...` gives the path back.
- **New scripts, shaders and assets** get their `.uid` or `.import` file when the editor scans them; the hook does that.
- **Moving a file yourself:** move its `.uid` or `.import` file with it, fix `path=` strings that point at it, then
  `gdh bridge scan`. UID references keep working.
- **Copying a scene or resource:** remove `uid=` from the copy's header, or the two files share a UID.
- Commit `.uid` files. They belong to the project.

## Live edits with exec

`exec` runs GDScript inside the editor. The change shows at once, the user can undo it with Ctrl+Z, and the scene is
marked unsaved. The script extends `RefCounted` and has `func run(editor)`; `editor` is `EditorInterface`. What `run`
returns comes back as JSON, with anything it printed (`output`) and the editor's errors.

```gdscript
extends RefCounted

func run(editor):
	var root = editor.get_edited_scene_root()
	var ur = editor.get_editor_undo_redo()
	var sprite = Sprite2D.new()
	sprite.name = "Hero"
	sprite.texture = load("res://art/hero.png")
	ur.create_action("Claude: add Hero")
	ur.add_do_method(root, "add_child", sprite, true)
	ur.add_do_method(sprite, "set_owner", root)
	ur.add_do_reference(sprite)
	ur.add_undo_method(root, "remove_child", sprite)
	ur.commit_action()
	return root.get_children().map(func(c): return c.name)
```

```sh
gdh bridge open res://main.tscn   # exec works on the current scene
gdh bridge exec edit.gd           # or: gdh bridge exec - < edit.gd
```

- Open the right scene first; `exec` sees only the current one.
- Put every change in an undo action. Change properties with `add_do_property` / `add_undo_property`.
- Set `owner` to the scene root on every node you add, children included. A node with no owner isn't saved.
- Don't save unless the user asked. Leave them to look and save (or `gdh bridge save res://main.tscn` when asked).
- Keep exec scripts out of the project, in a scratch directory.

## Checking scripts

After each `.gd` edit the hook checks the script and hands you Godot's errors. Fix them before going on. To check by
hand: `gdh bridge check res://a.gd res://b.gd` (with no bridge it parses them with a headless Godot). `gdh bridge
errors` returns the editor's errors since your last bridge command.

## Show the user

- `gdh bridge open res://levels/level_1.tscn` after making or changing a scene they'll want to see.
- `gdh bridge open res://player.gd` opens a script in the script editor.
- `gdh bridge play` runs the main scene in their editor, `play current` the open scene, `play res://x.tscn` that
  scene; `gdh bridge play --stop` stops it. To check the game yourself, use `gdh capture` or `gdh live` instead.

## Without the bridge

- Ask whether the editor is open. If it is, tell the user which scenes you changed, so they reload them, or ask them
  to close the editor while you work.
- The hooks still refuse `.godot/` edits and made-up UIDs, and still check GDScript.
- New scenes stay without UIDs until Godot next saves them. `gdh bridge start` then `gdh bridge resave PATH` fills
  them in.
- `gdh import --project DIR` imports assets and writes `.uid` files.

## The hooks

The gdh plugin's hooks run on every Write and Edit in a Godot project:

- **Before:** refuse edits to `.godot/`, to project.godot while an editor runs the bridge, to scenes with unsaved
  changes, and any `uid://` the project doesn't have. The refusal says what to do instead.
- **After:** rescan the file, reload it if it's open, fill in a new scene's UIDs, and check GDScript.

`GDH_HOOKS=off` in the environment turns them off.
