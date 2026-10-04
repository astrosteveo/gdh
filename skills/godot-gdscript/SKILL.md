---
name: godot-gdscript
description: Write GDScript that is correct for Godot 4.7+ — typed, idiomatic, and free of Godot 3 leftovers. Use when writing or reviewing any .gd file, a @tool script, a custom Resource, signals, await/coroutines, exports, input handling, or physics movement, and whenever you're unsure whether an API name or GDScript feature exists in Godot 4.
---

# GDScript for Godot 4.7+

Most mistakes in GDScript come from Godot 3 habits and from APIs that don't exist. Two tools catch them:

- `gdh api Class.member` checks a name against the installed Godot (see the `gdh` skill). Use it whenever you aren't
  sure. Don't guess method names, argument orders or enum names.
- The gdh hooks check each `.gd` file after you write it and hand back Godot's parse errors. Fix them before going on.

## Write typed code

Static types catch errors when the script loads, not when the line runs, and make GDScript faster.

```gdscript
class_name Player
extends CharacterBody2D

signal died
signal health_changed(old: int, new: int)

enum State { IDLE, RUN, JUMP }

const MAX_SPEED := 200.0

@export var speed: float = 120.0
@export_range(0, 100, 1) var health: int = 100
@export var state: State = State.IDLE

@onready var sprite: Sprite2D = %Sprite
@onready var hurt_sound: AudioStreamPlayer2D = $HurtSound

var inventory: Array[Item] = []
var scores: Dictionary[String, int] = {}


func take_damage(amount: int) -> void:
	var old := health
	health = maxi(health - amount, 0)
	health_changed.emit(old, health)
	if health == 0:
		died.emit()
```

- Use `:=` when the type is obvious from the right side, and `: Type =` when it isn't.
- Give every function argument and return a type, `-> void` included.
- Typed arrays (`Array[Item]`) and typed dictionaries (`Dictionary[String, int]`) both work.
- Use `maxi`/`mini`/`clampi` for ints and `maxf`/`minf`/`clampf` for floats. The untyped `max`/`min`/`clamp`
  return Variant.

## Nodes and scenes

- Get child nodes in `@onready` vars, not in `_init()`. Children don't exist until the node enters the tree.
- `%Name` reaches a node marked "Access as Unique Name" in its scene. It survives the node being moved. Prefer it to
  long `$A/B/C` paths.
- `get_node_or_null()` when the node may be missing; `get_node()` (and `$`) print an error when it is.
- Make scenes from code with `preload("res://enemy.tscn").instantiate()`. It's `instantiate()`, not Godot 3's
  `instance()`.
- Free nodes with `queue_free()`, not `free()`, unless you know nothing touches them later in the frame.
- A node you add from code to a scene being edited needs `owner` set to be saved. See the `godot-editor` skill.

## Signals

```gdscript
button.pressed.connect(_on_button_pressed)
health_changed.connect(func(old: int, new: int) -> void: bar.value = new)
died.emit()
timer.timeout.connect(_on_timeout, CONNECT_ONE_SHOT)
```

- Connect to the signal object: `node.signal_name.connect(callable)`. Emit with `signal_name.emit(args)`.
- Don't use Godot 3's `connect("signal", self, "method")` or `emit_signal("name")` in new code.
- `callable.bind(x)` adds arguments to a connection.
- Disconnect a connection to a node that may be freed first, or use `CONNECT_ONE_SHOT`.

## await

```gdscript
await get_tree().create_timer(0.5).timeout
await get_tree().process_frame
await animation_player.animation_finished
var result = await load_level()   # a function that awaits is a coroutine; await its call
```

`yield` is gone. A function that uses `await` must itself be awaited by callers that need its result.

## Movement and physics

```gdscript
func _physics_process(delta: float) -> void:
	if not is_on_floor():
		velocity += get_gravity() * delta
	if Input.is_action_just_pressed("jump") and is_on_floor():
		velocity.y = JUMP_VELOCITY
	var direction := Input.get_axis("move_left", "move_right")
	velocity.x = direction * speed if direction else move_toward(velocity.x, 0, speed)
	move_and_slide()
```

- Move physics bodies in `_physics_process`, not `_process`.
- `CharacterBody2D`/`3D` replaced Godot 3's `KinematicBody`. `velocity` is a property and `move_and_slide()` takes no
  arguments.
- Read input with actions from the Input Map (`Input.get_axis`, `Input.get_vector`,
  `Input.is_action_just_pressed`), not raw key codes. The `godot-project` skill covers adding actions.
- Scale per-frame changes by `delta`. `move_and_slide()` already does.

## Resources for data

```gdscript
class_name Item
extends Resource

@export var name: String
@export var icon: Texture2D
@export var price: int = 0
```

Save these as `.tres` files and `@export var item: Item` them into scenes. A resource loaded twice is the same
object: call `duplicate()` before changing one per instance.

## Tool scripts

`@tool` at the top makes a script run in the editor. Guard game-only code with `if Engine.is_editor_hint(): return`.
A tool script that changes the scene every frame keeps the editor redrawing (`gdh editor` reports this).
`@export_tool_button("Label") var action := do_something` adds an inspector button, and needs `@tool`.

## Newer features (4.5+)

- `@abstract` on a class or a method: the class can't be instantiated, the method must be overridden.
- Variadic functions: `func sum(...values: Array) -> int`.
- `is not`: `if node is not Enemy:`.

## Godot 3 names to avoid

| Godot 3 | Godot 4 |
| --- | --- |
| `KinematicBody2D`, `move_and_slide(velocity, up)` | `CharacterBody2D`, `velocity` property, `move_and_slide()` |
| `instance()` | `instantiate()` |
| `yield(obj, "signal")` | `await obj.signal` |
| `connect("sig", self, "method")` | `sig.connect(method)` |
| `onready var`, `export var` | `@onready var`, `@export var` |
| `get_tree().change_scene("res://x.tscn")` | `get_tree().change_scene_to_file("res://x.tscn")` |
| `Tween` node, `interpolate_property` | `create_tween().tween_property(...)` |
| `File.new()`, `Directory.new()` | `FileAccess.open(...)`, `DirAccess.open(...)` |
| `deg2rad`, `rad2deg`, `stepify` | `deg_to_rad`, `rad_to_deg`, `snapped` |
| `Spatial`, `Position2D`, `Sprite` (3D) | `Node3D`, `Marker2D`, `Sprite3D` |
| `rand_range` | `randf_range`, `randi_range` |
| `PoolStringArray` | `PackedStringArray` |
| `setget` | `var x: int: set = _set_x, get = _get_x` or inline `set(value):` |

When in doubt, `gdh api --search <word>` finds the Godot 4 name.
