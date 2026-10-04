---
name: godot-project
description: Lay out and grow a Godot 4.7+ game project — scene structure, autoloads, the Input Map, physics layers, groups, scene changes, saving player data, and folder layout. Use when starting a Godot project or a new system in one (a player, a level, a menu, a save system), when adding input actions, autoloads or project settings, or when deciding how scenes should talk to each other.
---

# Structuring a Godot 4.7+ project

Change project settings through the editor while it's open (see the `godot-editor` skill): it rewrites
`project.godot` from memory, and the gdh hooks refuse disk edits to it while a bridge runs. With no editor open,
editing `project.godot` directly is fine.

## Folders

Group files by what they belong to, not by file type:

```
project.godot
player/        player.tscn, player.gd, player.png
enemies/       slime/slime.tscn, slime/slime.gd, ...
levels/        level_1.tscn, ...
ui/            main_menu.tscn, hud.tscn, theme.tres
autoload/      game.gd, audio.gd
data/          items/*.tres, ...
test/          test_*.gd
```

Use `snake_case` for files and folders and `PascalCase` for node names and `class_name`s. A folder holding a
`.gdignore` file is invisible to Godot (use it for source art, `.blend` files and the like).

## Scenes

- **One job per scene.** A player, an enemy, a door, a HUD. Compose levels by instancing them.
- **A scene works on its own.** Pass in what it needs with `@export` vars and signals; avoid reaching up with
  `get_parent()` or long `../..` paths. Run any scene alone with `gdh capture` or `gdh live --scene`.
- **Call down, signal up.** A parent calls methods on its children. A child emits signals its parent connects to.
  Siblings talk through their parent, or through an autoload for game-wide events.
- **Instance, don't copy.** Make a base scene and use inherited scenes or exported properties for variants.
- **Mark nodes other scripts need as unique names** (`%Name`) so moving them doesn't break paths.

## Autoloads

An autoload is a scene or script Godot loads at start and keeps for the whole game, reachable by name everywhere. Use
them for game-wide state and services: the current save, audio, scene changes, a global event bus. Don't make one
for anything that belongs to a level or a single scene.

Add one through the editor:

```gdscript
extends RefCounted

func run(editor):
	ProjectSettings.set_setting("autoload/Game", "*res://autoload/game.gd")  # "*" makes it a singleton
	return ProjectSettings.save()
```

```sh
gdh bridge exec add_autoload.gd
```

The editor picks up a new autoload when it next reloads the project; ask the user to restart the editor
(Project > Reload Current Project) if they want it in the Scene dock now. `gdh live`, `gdh capture` and `gdh test`
see it right away.

## Input Map

Read input through named actions, never raw keys, so players can rebind them and gamepads work too. Add actions
through the editor:

```gdscript
extends RefCounted

func run(editor):
	var key := InputEventKey.new()
	key.physical_keycode = KEY_SPACE
	key.device = -1  # every device
	var pad := InputEventJoypadButton.new()
	pad.button_index = JOY_BUTTON_A
	pad.device = -1
	ProjectSettings.set_setting("input/jump", {"deadzone": 0.2, "events": [key, pad]})
	return ProjectSettings.save()
```

Use `physical_keycode` so the key sits in the same place on every keyboard layout. Name actions for what they do
(`jump`, `move_left`, `interact`), not for the key. The `ui_*` actions are built in and drive Control focus; give
the game its own actions rather than reusing them.

## Physics layers and groups

- Name layers in Project Settings (`layer_names/2d_physics/layer_1` = `"world"`, and so on) and set each body's
  `collision_layer` (what it is) and `collision_mask` (what it hits). Use `@export_flags_2d_physics` to pick layers
  in the inspector.
- Groups tag nodes for lookups and broadcasts: `add_to_group("enemies")`, `get_tree().get_nodes_in_group("enemies")`,
  `get_tree().call_group("enemies", "stun")`. Add groups to nodes in the scene (persistent) rather than in code
  when they're part of what the node is.

## Changing scenes

```gdscript
get_tree().change_scene_to_file("res://levels/level_2.tscn")
get_tree().change_scene_to_packed(preload("res://ui/main_menu.tscn"))
```

The change happens at the end of the frame. To keep state across scenes, keep it in an autoload. For a loading
screen, load in the background with `ResourceLoader.load_threaded_request()` and poll
`load_threaded_get_status()`.

## Saving player data

Write under `user://`, never `res://` (read-only in an exported game):

```gdscript
func save_game(data: Dictionary) -> void:
	var file := FileAccess.open("user://save.json", FileAccess.WRITE)
	file.store_string(JSON.stringify(data))

func load_game() -> Dictionary:
	if not FileAccess.file_exists("user://save.json"):
		return {}
	var parsed = JSON.parse_string(FileAccess.get_file_as_string("user://save.json"))
	return parsed if parsed is Dictionary else {}
```

For game data with typed fields, save a custom `Resource` with `ResourceSaver.save(res, "user://save.tres")`, but
only load `.tres` files your game wrote: a resource file can carry scripts. Games run under gdh keep `user://` in
gdh's own directory, so tests never touch the user's saves.

## Main scene and window

Set the main scene with `ProjectSettings.set_setting("application/run/main_scene", "res://main.tscn")`. Window size
lives in `display/window/size/viewport_width` and `viewport_height`; pixel-art games usually also want
`display/window/stretch/mode` = `"viewport"` and `rendering/textures/canvas_textures/default_texture_filter` = `0`
(nearest).
