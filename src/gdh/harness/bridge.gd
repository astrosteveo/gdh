extends Node
## Control channel into a running game. live.gd adds it to the root as an
## internal child, so game code walking the tree doesn't see it.
##
## Protocol: one JSON object per line over TCP on 127.0.0.1.
##   request  {"id": 1, "token": "...", "cmd": "step", "args": {...}}
##   reply    {"id": 1, "ok": true, "result": {...}, "errors": [...], "frame": 120, "held": true}
## "errors" holds engine errors raised since the previous reply, merged by message.
## "frame" counts game frames: frames in which the game ran. Rendered frames
## keep coming while it's held, and with --fixed-fps so do physics ticks.
##
## The game is held (SceneTree.paused) between commands, so no game time passes
## while the client thinks. "step" runs an exact number of frames. With
## --fixed-fps set to the physics tick rate, each frame is one physics tick.

const Common := preload("common.gd")
const ErrorCollector := preload("errors.gd")
const Probes := preload("probes.gd")

# Frame rate caps. Held: rendering continues, so cap it to spare the GPU.
# Running: about real time. Stepping: uncapped.
const HELD_FPS := 20
const TREE_MAX_NODES := 300

signal _frame_done

var errors: ErrorCollector
var token := ""
var out_dir := ""
var ready_file := ""
var idle_timeout_s := 1800.0
var warmup_frames := 10
var ticks_per_second := 60

var _server := TCPServer.new()
var _conns: Array[Dictionary] = []
var _queue: Array[Dictionary] = []
var _busy := false
var _held := true
var _last_request_ms := 0
var _notes: Array[String] = []
var _shot_count := 0
var _game_frames := 0


func _ready() -> void:
	process_mode = PROCESS_MODE_ALWAYS
	process_priority = 1 << 30  # Run after every game node each frame.
	get_tree().paused = true
	Engine.max_fps = HELD_FPS
	_last_request_ms = Time.get_ticks_msec()
	_start.call_deferred()


func _start() -> void:
	# Let the scene load, then render some held frames so shaders compile
	# before the first screenshot. No game time passes.
	while get_tree().current_scene == null:
		await get_tree().process_frame
	for i in warmup_frames:
		await get_tree().process_frame
	var err := _server.listen(0, "127.0.0.1")
	var info := {
		"port": _server.get_local_port() if err == OK else 0,
		"error": "" if err == OK else error_string(err),
		"status": _status(),
		"errors": errors.drain(),
	}
	write_ready(info)
	if err != OK:
		get_tree().quit(4)


## Writes the ready file in one step (temp file, then rename), so the CLI never
## reads it half-written.
func write_ready(info: Dictionary) -> void:
	var tmp := ready_file + ".tmp"
	var f := FileAccess.open(tmp, FileAccess.WRITE)
	f.store_string(JSON.stringify(info))
	f.close()
	DirAccess.rename_absolute(tmp, ready_file)


func _process(_delta: float) -> void:
	# The bridge runs last, so the pause state now is the state the game ran with.
	if not get_tree().paused:
		_game_frames += 1
	_frame_done.emit()
	if _held and not get_tree().paused:
		get_tree().paused = true
		_notes.append("The game unpaused itself while held. gdh paused it again.")
	_poll_network()
	if not _busy and not _queue.is_empty():
		_handle(_queue.pop_front())
	if idle_timeout_s > 0 and not _busy and Time.get_ticks_msec() - _last_request_ms > idle_timeout_s * 1000.0:
		print("gdh bridge: no requests for %d s, quitting." % idle_timeout_s)
		get_tree().quit()


# --- Network --------------------------------------------------------------------

func _poll_network() -> void:
	if not _server.is_listening():
		return
	while _server.is_connection_available():
		var peer := _server.take_connection()
		peer.set_no_delay(true)
		_conns.append({"peer": peer, "buf": PackedByteArray()})
	for conn in _conns.duplicate():
		var peer: StreamPeerTCP = conn.peer
		peer.poll()
		if peer.get_status() != StreamPeerTCP.STATUS_CONNECTED:
			_conns.erase(conn)
			continue
		var available := peer.get_available_bytes()
		if available > 0:
			conn.buf.append_array(peer.get_data(available)[1])
		var newline: int = conn.buf.find(10)
		while newline >= 0:
			var line: String = conn.buf.slice(0, newline).get_string_from_utf8()
			conn.buf = conn.buf.slice(newline + 1)
			_queue.append({"conn": conn, "line": line})
			newline = conn.buf.find(10)


func _reply(conn: Dictionary, id: Variant, ok: bool, payload: Variant) -> void:
	var reply := {
		"id": id,
		"ok": ok,
		"frame": _game_frames,
		"held": _held,
		"errors": errors.drain(),
	}
	reply["result" if ok else "error"] = payload
	if not _notes.is_empty():
		reply.notes = _notes
		_notes = []
	var peer: StreamPeerTCP = conn.peer
	if peer.get_status() == StreamPeerTCP.STATUS_CONNECTED:
		peer.put_data((JSON.stringify(reply) + "\n").to_utf8_buffer())


func _handle(item: Dictionary) -> void:
	_busy = true
	_last_request_ms = Time.get_ticks_msec()
	var request: Variant = JSON.parse_string(item.line)
	if not request is Dictionary:
		_reply(item.conn, null, false, "Request is not a JSON object.")
		_busy = false
		return
	if request.get("token", "") != token:
		_reply(item.conn, request.get("id"), false, "Wrong token.")
		(item.conn.peer as StreamPeerTCP).disconnect_from_host()
		_busy = false
		return
	var args: Dictionary = request.get("args", {})
	var result: Variant
	match request.get("cmd", ""):
		"status":
			result = _status()
		"step":
			result = await _cmd_step(args)
		"shot":
			result = await _cmd_shot(args)
		"probes":
			result = await _cmd_probes()
		"tree":
			result = _cmd_tree(args)
		"eval":
			result = _cmd_eval(args)
		"run":
			_set_held(false)
			Engine.max_fps = ticks_per_second
			result = _status()
		"pause":
			_set_held(true)
			result = _status()
		"quit":
			_reply(item.conn, request.get("id"), true, {"quitting": true})
			get_tree().quit.call_deferred()
			return
		var other:
			result = {"error": "Unknown command: %s" % other}
	if result is Dictionary and result.has("error"):
		_reply(item.conn, request.get("id"), false, result.error)
	else:
		_reply(item.conn, request.get("id"), true, result)
	_last_request_ms = Time.get_ticks_msec()
	_busy = false


# --- Commands -------------------------------------------------------------------

func _set_held(held: bool) -> void:
	_held = held
	get_tree().paused = held
	Engine.max_fps = HELD_FPS if held else 0


## args: frames, events [{at, ...event}], shot_every, views.
## Events with "at": k are injected before frame k+1 of the step (0 = before
## the first frame). They're injected right after unpausing, where real input
## arrives, so _input, is_action_just_pressed and is_action_pressed all see them.
func _cmd_step(args: Dictionary) -> Dictionary:
	var frames := maxi(int(args.get("frames", 1)), 1)
	var shot_every := int(args.get("shot_every", 0))
	var timeline := {}
	for spec in args.get("events", []):
		var event := _make_event(spec)
		if event == null:
			return {"error": "Bad input event: %s" % JSON.stringify(spec)}
		timeline.get_or_add(clampi(int(spec.get("at", 0)), 0, frames), []).append(event)
	var was_held := _held
	var start_frame := _game_frames
	_set_held(false)
	var shots := []
	for i in frames + 1:
		for event in timeline.get(i, []):
			Input.parse_input_event(event)
		if i == frames:
			break
		await _frame_done
		if shot_every > 0 and (i + 1) % shot_every == 0:
			await RenderingServer.frame_post_draw
			shots.append(_save_image("step-f%d" % (i + 1)))
	_set_held(was_held)
	if not was_held:
		Engine.max_fps = ticks_per_second
	return {"frames": _game_frames - start_frame, "shots": shots, "status": _status()}


func _cmd_shot(args: Dictionary) -> Dictionary:
	var views: Array = args.get("views", ["normal"])
	for view in views:
		if not Common.VIEWS.has(view):
			return {"error": "Unknown view %s. Views: %s" % [view, ", ".join(Common.VIEWS.keys())]}
	var label: String = args.get("label", "shot").validate_filename()
	_shot_count += 1
	var saved: Dictionary = await Common.save_views(get_tree(), views, out_dir.path_join("shots"),
			"%04d-%s-" % [_shot_count, label])
	return {"shots": saved, "image_size": Common.image_size(get_tree())}


func _cmd_probes() -> Dictionary:
	var probes: Dictionary = await Probes.new().run(get_tree())
	var views := ["normal"]
	for f in probes.findings:
		if f.view not in views:
			views.append(f.view)
	_shot_count += 1
	var saved: Dictionary = await Common.save_views(get_tree(), views, out_dir.path_join("shots"),
			"%04d-probes-" % _shot_count)
	return {"findings": probes.findings, "stats": probes.stats, "shots": saved, "image_size": Common.image_size(get_tree())}


func _cmd_tree(args: Dictionary) -> Dictionary:
	var scene := get_tree().current_scene
	if scene == null:
		return {"error": "No current scene."}
	var path: String = args.get("path", "")
	var start: Node = scene if path.is_empty() else scene.get_node_or_null(path)
	if start == null:
		return {"error": "No node at %s." % path}
	var budget := [int(args.get("max_nodes", TREE_MAX_NODES))]
	return {"tree": _describe(start, scene, int(args.get("depth", 4)), budget), "cut_nodes": -budget[0] if budget[0] < 0 else 0}


## Evaluates a Godot Expression with the current scene as base. Inputs: tree,
## scene, root, each autoload by name, and the engine's singletons (OS, Engine,
## Input, Time, RenderingServer...), as in GDScript.
func _cmd_eval(args: Dictionary) -> Dictionary:
	var expression := Expression.new()
	var scene := get_tree().current_scene
	var names := PackedStringArray(["tree", "scene", "root"])
	var values := [get_tree(), scene, get_tree().root]
	for setting in ProjectSettings.get_property_list():
		var autoload: String = setting.name.trim_prefix("autoload/")
		if setting.name.begins_with("autoload/") and get_tree().root.has_node(autoload):
			names.append(autoload)
			values.append(get_tree().root.get_node(autoload))
	for singleton in Engine.get_singleton_list():
		if not names.has(singleton):
			names.append(singleton)
			values.append(Engine.get_singleton(singleton))
	var err := expression.parse(args.get("expr", ""), names)
	if err != OK:
		return {"error": expression.get_error_text()}
	var value: Variant = expression.execute(values, scene, false)
	if expression.has_execute_failed():
		return {"error": "Evaluation failed: %s" % expression.get_error_text()}
	return {"value": _to_json(value)}


# --- Helpers --------------------------------------------------------------------

func _status() -> Dictionary:
	var scene := get_tree().current_scene
	return {
		"scene": scene.scene_file_path if scene else "",
		"held": _held,
		"frame": _game_frames,
		"ticks_per_second": ticks_per_second,
		"image_size": Common.image_size(get_tree()),
		"runs_while_held": _unpausable_nodes(),
	}


## Nodes whose own process mode keeps them running while the game is held.
func _unpausable_nodes() -> Array:
	var out := []
	var scene := get_tree().current_scene
	for node in _walk(get_tree().root):
		if node == self or out.size() >= 20:
			continue
		if node.process_mode in [PROCESS_MODE_ALWAYS, PROCESS_MODE_WHEN_PAUSED]:
			var path := str(scene.get_path_to(node)) if scene and scene.is_ancestor_of(node) else str(node.get_path())
			out.append({"node": path, "mode": "always" if node.process_mode == PROCESS_MODE_ALWAYS else "when_paused"})
	return out


func _walk(node: Node) -> Array[Node]:
	var out: Array[Node] = [node]
	for child in node.get_children():
		out.append_array(_walk(child))
	return out


func _describe(node: Node, scene: Node, depth: int, budget: Array) -> Dictionary:
	budget[0] -= 1
	var d := {"name": str(node.name), "class": node.get_class()}
	var script: Script = node.get_script()
	if script and not script.resource_path.is_empty():
		d.script = script.resource_path.get_file()
	if (node is CanvasItem or node is Node3D) and not node.visible:
		d.hidden = true
	if node is Node2D or node is Node3D:
		d.pos = _to_json(node.global_position)
	var screen: Variant = _screen_pos(node)
	if screen != null:
		d.screen = screen
	for property in ["text", "value", "velocity", "linear_velocity", "current_animation", "animation"]:
		if property in node:
			var v: Variant = node.get(property)
			if v is String and (v as String).is_empty():
				continue
			d[property] = str(v).left(60) if v is String or v is StringName else _to_json(v)
	if node.process_mode != PROCESS_MODE_INHERIT:
		d.process_mode = ["inherit", "pausable", "when_paused", "always", "disabled"][node.process_mode]
	var children := node.get_children()
	if children.is_empty():
		return d
	if depth <= 0 or budget[0] <= 0:
		d.more_children = children.size()
		budget[0] -= children.size()
		return d
	d.children = []
	for child in children:
		if budget[0] <= 0:
			d.more_children = children.size() - d.children.size()
			budget[0] -= d.more_children
			break
		d.children.append(_describe(child, scene, depth - 1, budget))
	return d


func _make_event(spec: Dictionary) -> InputEvent:
	var pressed: bool = spec.get("pressed", true)
	if spec.has("action"):
		var e := InputEventAction.new()
		e.action = spec.action
		e.pressed = pressed
		e.strength = float(spec.get("strength", 1.0)) if pressed else 0.0
		return e
	if spec.has("key"):
		var code := OS.find_keycode_from_string(spec.key)
		if code == KEY_NONE:
			return null
		var e := InputEventKey.new()
		e.keycode = code
		e.physical_keycode = code
		e.pressed = pressed
		return e
	if spec.has("mouse_button"):
		var e := InputEventMouseButton.new()
		e.button_index = int(spec.mouse_button)
		e.position = Common.shot_to_window(get_tree(), Vector2(spec.position[0], spec.position[1]))
		e.global_position = e.position
		e.pressed = pressed
		return e
	if spec.has("mouse_motion"):
		var e := InputEventMouseMotion.new()
		e.position = Common.shot_to_window(get_tree(), Vector2(spec.mouse_motion[0], spec.mouse_motion[1]))
		e.global_position = e.position
		return e
	return null


func _save_image(label: String) -> String:
	_shot_count += 1
	var dir := out_dir.path_join("shots")
	DirAccess.make_dir_recursive_absolute(dir)
	var path := dir.path_join("%04d-%s.png" % [_shot_count, label])
	get_tree().root.get_texture().get_image().save_png(path)
	return path


## Where a node appears in screenshot pixels: [x, y, w, h] for a Control,
## [x, y] for other 2D nodes and for 3D nodes in front of the camera.
func _screen_pos(node: Node) -> Variant:
	var tree := get_tree()
	if node is Control:
		var r := Common.to_shot(tree, (node as Control).get_global_transform_with_canvas() * Rect2(Vector2.ZERO, node.size))
		return [snappedf(r.position.x, 0.1), snappedf(r.position.y, 0.1), snappedf(r.size.x, 0.1), snappedf(r.size.y, 0.1)]
	if node is Node2D:
		var p := (node as Node2D).get_global_transform_with_canvas().origin * Common.shot_scale(tree)
		return [snappedf(p.x, 0.1), snappedf(p.y, 0.1)]
	if node is Node3D:
		var camera := get_viewport().get_camera_3d()
		var world_pos := (node as Node3D).global_position
		if camera and not camera.is_position_behind(world_pos):
			var p := camera.unproject_position(world_pos) * Common.shot_scale(tree)
			return [snappedf(p.x, 0.1), snappedf(p.y, 0.1)]
	return null


## Converts a Variant into something JSON can hold.
func _to_json(v: Variant) -> Variant:
	match typeof(v):
		TYPE_NIL, TYPE_BOOL, TYPE_INT, TYPE_FLOAT, TYPE_STRING:
			return v
		TYPE_STRING_NAME, TYPE_NODE_PATH:
			return str(v)
		TYPE_VECTOR2, TYPE_VECTOR2I:
			return [snappedf(v.x, 0.001), snappedf(v.y, 0.001)]
		TYPE_VECTOR3, TYPE_VECTOR3I:
			return [snappedf(v.x, 0.001), snappedf(v.y, 0.001), snappedf(v.z, 0.001)]
		TYPE_ARRAY, TYPE_PACKED_STRING_ARRAY, TYPE_PACKED_INT32_ARRAY, TYPE_PACKED_FLOAT32_ARRAY:
			return Array(v).slice(0, 100).map(_to_json)
		TYPE_DICTIONARY:
			var out := {}
			for key in v:
				out[str(key)] = _to_json(v[key])
			return out
		TYPE_OBJECT:
			if v == null:
				return null
			if v is Node:
				return {"node": str(v.get_path()), "class": v.get_class()}
			if v is Resource and not v.resource_path.is_empty():
				return {"resource": v.resource_path, "class": v.get_class()}
			return {"object": v.get_class()}
	return var_to_str(v)
