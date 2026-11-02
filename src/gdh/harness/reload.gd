extends RefCounted
## `gdh live reload`: puts the GDScript files that changed on disk into the running game, keeping its state. Each
## script is recompiled in place (Script.reload(true), as the editor's "Synchronize Script Changes" does for a game it
## runs), so every instance keeps its member values, static variables keep theirs, and connected methods and lambdas
## run the new code. The scripts that extend a changed one are recompiled after it, so they see its new members. An
## input or process callback (_input, _process...) that a reload adds is turned on in the nodes in the tree, as Node
## does for a script's callbacks when it becomes ready; one the script already had keeps the state the game left.
##
## What it can't carry over: a function suspended at an await in a reloaded script is cancelled (Godot warns), and a
## member variable that a reload adds starts at its initial value only in the nodes in the tree (from a fresh instance
## of the script; an @onready one stays null), elsewhere null. A script that doesn't compile is put back to the
## version that ran, so the game never runs without it.


## The callbacks Node turns on as it becomes ready when its script has them (Node::_notification, READY), each with
## the method that turns it on.
const CALLBACKS := {
	"_input": "set_process_input",
	"_shortcut_input": "set_process_shortcut_input",
	"_unhandled_input": "set_process_unhandled_input",
	"_unhandled_key_input": "set_process_unhandled_key_input",
	"_process": "set_process",
	"_physics_process": "set_physics_process",
}


## args.paths: the res:// scripts to reload (default: every loaded script whose file changed). Returns
## {"reloaded": [path], "failed": [{"path", "error"}], "unchanged": n, "filled": {path: [member]}}.
static func command(tree: SceneTree, args: Dictionary) -> Dictionary:
	var scripts := loaded_scripts(tree)
	var asked: Array = args.get("paths", [])
	for path in asked:
		if not scripts.has(path):
			if not ResourceLoader.has_cached(path):
				return {"error": "%s isn't loaded in the game, so there's nothing to reload." % path}
			var loaded: Resource = load(path)
			if not loaded is GDScript:
				return {"error": "%s isn't a GDScript." % path}
			scripts[path] = loaded
	var changed: Array[GDScript] = []
	var unchanged := 0
	for path in (asked if not asked.is_empty() else scripts.keys()):
		var script: GDScript = scripts[path]
		if FileAccess.get_file_as_string(path) == script.source_code:
			unchanged += 1
		else:
			changed.append(script)
	# Bases before the scripts that extend them, then those that extend a changed one, recompiled as they are.
	var order: Array[GDScript] = changed.duplicate()
	for script: GDScript in scripts.values():
		if not order.has(script) and changed.any(func(base: GDScript) -> bool: return _extends(script, base)):
			order.append(script)
	order.sort_custom(func(a: GDScript, b: GDScript) -> bool: return _depth(a) < _depth(b))
	var members := {}
	var methods := {}
	for script in order:
		members[script] = _member_names(script)
		methods[script] = _method_names(script)
	var reloaded := []
	var failed := []
	var broken: Array[GDScript] = []
	for script in order:
		if broken.any(func(base: GDScript) -> bool: return _extends(script, base)):
			continue  # its base is still the one that ran
		var before := script.source_code
		if changed.has(script):
			script.source_code = FileAccess.get_file_as_string(script.resource_path)
		var err := script.reload(true)
		if err != OK:
			# A script that doesn't compile does nothing at all, so the one that ran goes back in.
			script.source_code = before
			script.reload(true)
			broken.append(script)
			failed.append({"path": script.resource_path, "error": error_string(err)})
		elif changed.has(script):
			reloaded.append(script.resource_path)
	var filled := {}
	for script in order:
		if broken.has(script):
			continue
		var added := _member_names(script).filter(func(name: String) -> bool: return not members[script].has(name))
		if not added.is_empty() and _fill(tree, script, added) > 0:
			filled[script.resource_path] = added
		_turn_on_added_callbacks(tree, script, methods[script])
	return {"reloaded": reloaded, "failed": failed, "unchanged": unchanged, "filled": filled}


## The game's GDScripts from res://, by path: those of the nodes in the tree, the scripts they extend and preload as
## constants, and the global classes' that are loaded.
static func loaded_scripts(tree: SceneTree) -> Dictionary:
	var out := {}
	for node in _tree_nodes(tree):
		_add_script(out, node.get_script())
	for info in ProjectSettings.get_global_class_list():
		var path: String = info.get("path", "")
		if path.ends_with(".gd") and not out.has(path) and ResourceLoader.has_cached(path):
			_add_script(out, load(path))
	return out


static func _add_script(out: Dictionary, script: Variant) -> void:
	while script is GDScript and script.resource_path.begins_with("res://") and not out.has(script.resource_path):
		out[script.resource_path] = script
		for value in script.get_script_constant_map().values():
			if value is GDScript:
				_add_script(out, value)
		script = script.get_base_script()


static func _extends(script: Script, base: Script) -> bool:
	var s := script.get_base_script()
	while s != null:
		if s == base:
			return true
		s = s.get_base_script()
	return false


static func _depth(script: Script) -> int:
	var depth := 0
	var s := script.get_base_script()
	while s != null:
		depth += 1
		s = s.get_base_script()
	return depth


static func _member_names(script: Script) -> Array:
	return script.get_script_property_list().map(func(p: Dictionary) -> String: return p.name)


static func _method_names(script: Script) -> Array:
	return script.get_script_method_list().map(func(m: Dictionary) -> String: return m.name)


static func _tree_nodes(tree: SceneTree) -> Array[Node]:
	var out: Array[Node] = []
	var stack: Array[Node] = [tree.root]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		out.append(node)
		stack.append_array(node.get_children(true))
	return out


static func _nodes_with(tree: SceneTree, script: Script) -> Array[Node]:
	return _tree_nodes(tree).filter(func(node: Node) -> bool: return node.get_script() == script)


## Turns on, in the nodes in the tree whose script is `script`, the callbacks it has now and didn't have `before`.
static func _turn_on_added_callbacks(tree: SceneTree, script: GDScript, before: Array) -> void:
	var now := _method_names(script)
	var added := CALLBACKS.keys().filter(func(name: String) -> bool: return now.has(name) and not before.has(name))
	if added.is_empty():
		return
	for node in _nodes_with(tree, script):
		for name: String in added:
			node.call(CALLBACKS[name], true)


## Gives the nodes in the tree whose script is `script` the members a reload added, at their initial values in a fresh
## instance. Returns how many nodes it gave them to: none for a script whose _init needs arguments.
static func _fill(tree: SceneTree, script: GDScript, added: Array) -> int:
	for method in script.get_script_method_list():
		if method.name == "_init" and method.args.size() > method.default_args.size():
			return 0
	if not script.can_instantiate():
		return 0
	var fresh: Object = script.new()
	if fresh == null:
		return 0
	var nodes := _nodes_with(tree, script)
	for node in nodes:
		for name: String in added:
			var value: Variant = fresh.get(name)
			node.set(name, value.duplicate(true) if value is Array or value is Dictionary else value)
	if not fresh is RefCounted:
		fresh.free()
	return nodes.size()
