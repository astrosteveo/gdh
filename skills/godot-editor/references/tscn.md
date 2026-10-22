# Scene and resource files

What a `.tscn` and a `.tres` hold, as Godot 4.7 writes them. Leave out `uid=` and `unique_id=` when you write one:
Godot fills them in (the skill's UIDs section).

## A scene

A menu: a script on the root, a styled panel, a box of buttons laid out by their container, one button with a
group, a unique name and theme overrides, an instanced scene, and a signal connected in the scene.

```
[gd_scene format=3]

[ext_resource type="Script" path="res://ui/menu.gd" id="1_menu"]
[ext_resource type="PackedScene" path="res://ui/slot.tscn" id="2_slot"]

[sub_resource type="StyleBoxFlat" id="StyleBoxFlat_bg"]
bg_color = Color(0.1, 0.1, 0.15, 1)

[node name="Menu" type="Control"]
layout_mode = 3
anchors_preset = 15
anchor_right = 1.0
anchor_bottom = 1.0
script = ExtResource("1_menu")

[node name="Panel" type="PanelContainer" parent="."]
layout_mode = 0
theme_override_styles/panel = SubResource("StyleBoxFlat_bg")

[node name="Buttons" type="VBoxContainer" parent="Panel"]
layout_mode = 2
theme_override_constants/separation = 8

[node name="Play" type="Button" parent="Panel/Buttons" groups=["menu_buttons"]]
unique_name_in_owner = true
layout_mode = 2
theme_override_colors/font_color = Color(1, 0.8, 0, 1)
theme_override_font_sizes/font_size = 32
text = "Play"

[node name="Slot" parent="Panel/Buttons" instance=ExtResource("2_slot")]
layout_mode = 2

[connection signal="pressed" from="Panel/Buttons/Play" to="." method="_on_play_pressed"]
```

- **Order.** The header, then `ext_resource`s (other files), then `sub_resource`s (resources kept in this file), then
  the nodes, then the connections. Declare a resource before anything uses it.
- **ids.** An `ext_resource`'s or `sub_resource`'s `id` is any string unique in the file, and `ExtResource("1_menu")`
  or `SubResource("StyleBoxFlat_bg")` refers to it.
- **Nodes.** The first node is the root and has no `parent`. Every other node's `parent` is its parent's path from
  the root: `"."` for the root itself, `"Panel/Buttons"` further down. Its properties follow its header as
  `name = value` in Godot's text form: `Vector2(10, 4)`, `Color(1, 0.8, 0, 1)`, `"text"`, `&"name"` for a
  StringName, `NodePath("../Target")`. A property left out keeps its default.
- **An instanced scene** has `instance=ExtResource(...)` and no `type`; the properties under it are the ones this
  scene overrides.
- **Groups** go in the node's header (`groups=["enemies"]`). `unique_name_in_owner = true` is what lets a script
  reach the node as `%Play`.
- **Theme overrides** are `theme_override_<kind>s/<name>`, the kind being what `gdh api CLASS` shows in parentheses
  after the theme property: `color`, `constant`, `font`, `font_size`, `icon`, `style`. Keep the `s` in the pattern:
  `font_color: Color (color)` is `theme_override_colors/font_color`.
- **Layout.** `layout_mode = 2` marks a child a container lays out: the container sets its position and size, so
  give it none. A Control outside a container is placed by its anchors and offsets; the root of a screen usually
  fills it (`anchors_preset = 15`, `anchor_right` and `anchor_bottom` 1.0).
- **Connections** name the signal, the emitting node and the receiving node by their paths from the root, and the
  method, which the receiver's script must have. A connection made here shows in the editor's Node dock; one made
  in `_ready()` doesn't.

## A resource

A custom `Resource` saved as data, here an `Item` from `res://data/item.gd` (`class_name Item`, `extends Resource`,
with exported `name`, `icon` and `price`):

```
[gd_resource type="Resource" script_class="Item" format=3]

[ext_resource type="Script" path="res://data/item.gd" id="1_item"]
[ext_resource type="Texture2D" path="res://art/sword.png" id="2_icon"]

[resource]
script = ExtResource("1_item")
name = "Iron sword"
icon = ExtResource("2_icon")
price = 40
```

A built-in resource type works the same way, with its own type and no script: `[gd_resource type="StyleBoxFlat"
format=3]`, then `[resource]` and its properties.

## Letting Godot write it

Built in an `exec` script (`gdh bridge exec build.gd`, with a bridge running: `gdh bridge start` when the user's
editor isn't open), a scene like the one above comes out with its UIDs and `unique_id`s, and a misspelt property is
an error (exit 1, with the error in the reply) instead of a silent loss.

```gdscript
extends RefCounted

func run(editor):
	var root := Control.new()
	root.name = "Menu"
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.set_script(load("res://ui/menu.gd"))
	var panel := PanelContainer.new()
	panel.name = "Panel"
	var style := StyleBoxFlat.new()
	style.bg_color = Color(0.1, 0.1, 0.15)
	panel.add_theme_stylebox_override("panel", style)
	root.add_child(panel)
	panel.owner = root  # set owner on every node you add, or it isn't saved
	var play := Button.new()
	play.name = "Play"
	play.text = "Play"
	play.unique_name_in_owner = true
	play.add_to_group("menu_buttons", true)  # true: saved with the scene
	play.add_theme_font_size_override("font_size", 32)
	panel.add_child(play)
	play.owner = root
	play.pressed.connect(Callable(root, "_on_play_pressed"), Object.CONNECT_PERSIST)  # saved with the scene
	var slot: Node = load("res://ui/slot.tscn").instantiate(PackedScene.GEN_EDIT_STATE_INSTANCE)
	panel.add_child(slot)
	slot.owner = root
	var packed := PackedScene.new()
	packed.pack(root)
	var err := ResourceSaver.save(packed, "res://ui/menu.tscn")
	root.free()
	return error_string(err)
```

`ResourceSaver.save(resource, "res://data/sword.tres")` saves a resource the same way. Run `gdh bridge scan` after,
so the editor sees the new file.
