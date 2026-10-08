extends Node
## Control channel into a running game. live.gd adds it to the root as an
## internal child, so game code walking the tree doesn't see it.
##
## Protocol: one JSON object per line over TCP on 127.0.0.1.
##   request  {"id": 1, "token": "...", "cmd": "step", "args": {...}}
##   reply    {"id": 1, "ok": true, "result": {...}, "errors": [...], "frame": 120, "held": true}
## "errors" holds engine errors raised since the previous reply, merged by message, each raised from a script with
## its "backtrace". "output" holds what the game printed since the previous reply (errors.gd caps it, and
## "output_cut" says how many lines it cut).
## "frame" counts game frames: frames in which the game ran. Rendered frames
## keep coming while it's held, and with --fixed-fps so do physics ticks.
##
## The game is held (SceneTree.paused) between commands, so no game time passes
## while the client thinks. "step" runs an exact number of frames. With
## --fixed-fps set to the physics tick rate, each frame is one physics tick.

const Camera := preload("camera.gd")
const Common := preload("common.gd")
const Covered := preload("covered.gd")
const ErrorCollector := preload("errors.gd")
const Probes := preload("probes.gd")
const Screen := preload("screen.gd")

# Frame rate caps. Held: rendering continues, so cap it to spare the GPU.
# Running: about real time. Stepping, or answering a command: uncapped.
# The bridge paces frames itself (_pace): under --fixed-fps Godot skips its
# own frame limiter, so Engine.max_fps does nothing.
const HELD_FPS := 20
const TREE_MAX_NODES := 300
## An array in a reply keeps this many items, then says how many more there were.
const JSON_MAX_ITEMS := 100

signal _frame_done

var errors: ErrorCollector
var token := ""
var out_dir := ""
var ready_file := ""
var idle_timeout_s := 1800.0
var warmup_frames := 10
var ticks_per_second := 60
## The display gdh started the game on: "gpu" or "xvfb".
var display := ""
## The window size gdh asked for (--resolution), or zero: a step notes once if the game's window isn't that size.
var resolution := Vector2i.ZERO
var recorder: Node  # frames.gd: each game frame's render times

var _server := TCPServer.new()
var _conns: Array[Dictionary] = []
var _queue: Array[Dictionary] = []
var _busy := false
var _held := true
var _last_request_ms := 0
var _notes: Array[String] = []
var _shot_count := 0
var _game_frames := 0
var _vsync_noted := false
var _size_noted := false
var _next_frame_us := 0


func _ready() -> void:
	process_mode = PROCESS_MODE_ALWAYS
	process_priority = 1 << 30  # Run after every game node each frame.
	get_tree().paused = true
	_last_request_ms = Time.get_ticks_msec()
	_start.call_deferred()


func _start() -> void:
	# Let the scene load, then render some held frames so shaders compile
	# before the first screenshot. No game time passes.
	while get_tree().current_scene == null:
		await get_tree().process_frame
	for i in warmup_frames:
		await get_tree().process_frame
	# Shots number on from the highest already there, so a session started again in the same out dir keeps them.
	_shot_count = _last_shot_number()
	var err := _server.listen(0, "127.0.0.1")
	var info := {
		"port": _server.get_local_port() if err == OK else 0,
		"error": "" if err == OK else error_string(err),
		"status": _status(),
		"errors": errors.drain(),
		"output": errors.drain_output().output,
	}
	write_ready(info)
	if err != OK:
		get_tree().quit(4)


## The highest number among the shots in out_dir/shots, or 0.
func _last_shot_number() -> int:
	var dir := out_dir.path_join("shots")
	var last := 0
	if DirAccess.dir_exists_absolute(dir):
		for file in DirAccess.get_files_at(dir):
			var number := file.get_slice("-", 0)
			if number.is_valid_int():
				last = maxi(last, number.to_int())
	return last


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
	_pace()


## Sleeps out the rest of the frame: HELD_FPS while held, the tick rate (real
## time) while running, and not at all while a command is under way (a step).
## A request that comes in wakes it at once, so a script's commands never wait.
func _pace() -> void:
	var fps := 0 if _busy or not _queue.is_empty() else (HELD_FPS if _held else ticks_per_second)
	if fps <= 0:
		_next_frame_us = 0
		return
	var period := 1000000 / fps
	var now := Time.get_ticks_usec()
	_next_frame_us = now if _next_frame_us == 0 else _next_frame_us + period
	if now - _next_frame_us > period:
		_next_frame_us = now  # A frame that ran long (or a pause): no sleep, and count again from here.
	while now < _next_frame_us and not _request_waiting():
		OS.delay_usec(mini(_next_frame_us - now, 1000))
		now = Time.get_ticks_usec()


func _request_waiting() -> bool:
	if _server.is_connection_available():
		return true
	for conn in _conns:
		var peer: StreamPeerTCP = conn.peer
		peer.poll()
		# (A client that has hung up is dropped by _poll_network; asking it for bytes is an engine error.)
		if peer.get_status() == StreamPeerTCP.STATUS_CONNECTED and peer.get_available_bytes() > 0:
			return true
	return false


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
	var printed := errors.drain_output()
	if not printed.output.is_empty():
		reply.output = printed.output
		if printed.output_cut > 0:
			reply.output_cut = printed.output_cut
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
		"find":
			result = _cmd_find(args)
		"camera":
			result = Camera.command(get_tree(), args)
		"frames":
			result = recorder.command(args)
		"eval":
			result = _cmd_eval(args)
		"run":
			_set_held(false)
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


## args: frames, events [{at, ...event}], shot_every, views, cover_every (sample the panels over the screen's centre
## every K frames: covered.gd), until, trace and every (_watch_start).
## Events with "at": k are injected before frame k+1 of the step (0 = before
## the first frame). They're injected right after unpausing, where real input
## arrives, so _input, is_action_just_pressed and is_action_pressed all see them.
func _cmd_step(args: Dictionary) -> Dictionary:
	var aimed: Variant = _aim_events(args.get("events", []))
	if aimed is String:
		return {"error": aimed}
	var frames := maxi(int(args.get("frames", 1)), 1)
	var shot_every := int(args.get("shot_every", 0))
	var cover_every := int(args.get("cover_every", 0))
	var cover_samples := []
	var timeline := {}
	for spec in args.get("events", []):
		var event := _make_event(spec)
		if event == null:
			return {"error": "Bad input event: %s" % JSON.stringify(spec)}
		timeline.get_or_add(clampi(int(spec.get("at", 0)), 0, frames), []).append(event)
	var watch := _watch_start(args)
	if watch.has("error"):
		return {"error": watch.error}
	_note_vsync()
	_note_window_size()
	var was_held := _held
	var start_frame := _game_frames
	_set_held(false)
	var shots := []
	for i in frames + 1:
		for event in timeline.get(i, []):
			Input.parse_input_event(event)
		if i == frames:
			# The step's last events (a --hold's release) go to the game now, while it still runs. Held, a paused
			# node never sees them, so a game that tracks its keys by their events would keep them down.
			Input.flush_buffered_events()
			break
		await _frame_done
		if shot_every > 0 and (i + 1) % shot_every == 0:
			await RenderingServer.frame_post_draw
			shots.append(_save_image("step-f%d" % (i + 1)))
		if cover_every > 0 and (i + 1) % cover_every == 0:
			cover_samples.append(Covered.sample(get_tree()))
		if not watch.is_empty() and _watch_check(watch, i + 1):
			# --until holds: the step ends here. The events due before the next frame go, and those at the step's end
			# (a --hold's release), so nothing is left down that the step would have let go; later ones don't.
			var due: Array = timeline.get(i + 1, [])
			if i + 1 < frames:
				due = due + timeline.get(frames, [])
			for event in due:
				Input.parse_input_event(event)
			Input.flush_buffered_events()
			break
	_set_held(was_held)
	Common.wait_saves()  # every frame written before the reply names it
	var result := {"frames": _game_frames - start_frame, "shots": shots, "status": _status()}
	if cover_every > 0:
		result.cover_samples = cover_samples
	if not watch.is_empty():
		result.merge(_watch_result(watch))
	if not aimed.is_empty():
		result.aimed = aimed
	return result


## gdh starts the game with V-Sync off, so steps run as fast as the GPU goes. On the GPU
## display a game that turns it on itself waits for each frame's turn at the refresh rate.
func _note_vsync() -> void:
	if _vsync_noted or display != "gpu" or DisplayServer.window_get_vsync_mode() == DisplayServer.VSYNC_DISABLED:
		return
	_vsync_noted = true
	_notes.append("The game turned V-Sync on, so each frame waits for the display's refresh: steps run at about 60 "
			+ "frames a second, not as fast as the GPU can draw them.")


## The window was the size asked for when the game started (gdh checks); a game that resizes it later is noted once.
func _note_window_size() -> void:
	var size := DisplayServer.window_get_size()
	if _size_noted or resolution == Vector2i.ZERO or size == resolution:
		return
	_size_noted = true
	_notes.append("The game's window is now %dx%d, not the %dx%d asked for: the game resized it. Shots are at the "
			% [size.x, size.y, resolution.x, resolution.y] + "window's new size.")


func _cmd_shot(args: Dictionary) -> Dictionary:
	var views: Array = args.get("views", ["normal"])
	for view in views:
		if not Common.VIEWS.has(view):
			return {"error": "Unknown view %s. Views: %s" % [view, ", ".join(Common.VIEWS.keys())]}
	var label: String = args.get("label", "shot").validate_filename()
	var framing := Screen.framing(get_tree(), args)
	if framing.has("error"):
		return framing
	var paths: Variant = _shot_paths(args.get("out", ""), views)
	if paths is String:
		return {"error": paths}
	_shot_count += 1
	var hidden := Screen.hide_ui(get_tree()) if args.get("no_ui", false) else []
	var saved: Dictionary = await Common.save_views(get_tree(), views, out_dir.path_join("shots"),
			"%04d-%s-" % [_shot_count, label], framing.edit, paths)
	Screen.show_layers(hidden)
	var result := {"shots": saved, "image_size": Common.image_size(get_tree())}
	if framing.edit.is_valid():
		result.merge(framing.report)
	return result


## --out: the file a shot goes to, with the view's name added when there are several (shot.png: shot-normal.png,
## shot-wireframe.png). A relative path is under the session's output directory. Returns {view: path}, or an error.
func _shot_paths(out: String, views: Array) -> Variant:
	if out.is_empty():
		return {}
	if out.is_relative_path():
		out = out_dir.path_join(out)
	if DirAccess.dir_exists_absolute(out):
		return "--out takes a file, and %s is a directory." % out
	if out.get_extension().is_empty():
		out += ".png"
	elif out.get_extension().to_lower() != "png":
		return "--out writes a PNG, so give it a .png file, not %s." % out.get_file()
	DirAccess.make_dir_recursive_absolute(out.get_base_dir())
	var paths := {}
	for view in views:
		paths[view] = out if views.size() == 1 else "%s-%s.png" % [out.get_basename(), view]
	return paths


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
	var described := _describe(start, scene, int(args.get("depth", 4)), budget, args.get("visible_only", false))
	return {"tree": described, "cut_nodes": -budget[0] if budget[0] < 0 else 0}


## args: text (shown, any case), name (a pattern with * and ?), class (or one it extends); each given must match.
## Nodes that show only, each with its box on screen; up to 10 that match but don't show, with why.
func _cmd_find(args: Dictionary) -> Dictionary:
	var filters := {}
	for key in ["text", "name", "class"]:
		if args.has(key) and not str(args[key]).is_empty():
			filters[key] = str(args[key])
	if filters.is_empty():
		return {"error": "find takes a text, a name or a class."}
	return Screen.find(get_tree(), filters)


## Events aimed at a node: "on": {"node": PATH} or {"text": TEXT} puts a mouse event at the centre of the part of it
## that shows, worked out once before the step's first frame. Returns [{path, class, text?, at}], one for each
## target, or an error naming the candidates.
func _aim_events(events: Array) -> Variant:
	var aimed := {}
	for spec in events:
		if not (spec is Dictionary and spec.has("on")):
			continue
		var key := JSON.stringify(spec.on)
		if not aimed.has(key):
			var target: Dictionary = Screen.aim(get_tree(), spec.on if spec.on is Dictionary else {})
			if target.has("error"):
				return target.error
			aimed[key] = target
		if spec.has("mouse_motion"):
			spec.mouse_motion = aimed[key].at
		else:
			spec.position = aimed[key].at
		spec.erase("on")
	return aimed.values()


## Evaluates a Godot Expression with the current scene as base. Inputs: tree,
## scene, root, each autoload by name, and the engine's singletons (OS, Engine,
## Input, Time, RenderingServer...), as in GDScript.
func _cmd_eval(args: Dictionary) -> Dictionary:
	var expression := Expression.new()
	var scene := get_tree().current_scene
	var inputs := _eval_inputs()
	var err := expression.parse(args.get("expr", ""), inputs[0])
	if err != OK:
		return {"error": expression.get_error_text()}
	var value: Variant = expression.execute(inputs[1], scene, false)
	if expression.has_execute_failed():
		return {"error": "Evaluation failed: %s" % expression.get_error_text()}
	return {"value": _to_json(value)}


## An expression's input names and their values: [names, values]. "scene" is the second.
func _eval_inputs() -> Array:
	var names := PackedStringArray(["tree", "scene", "root"])
	var values := [get_tree(), get_tree().current_scene, get_tree().root]
	for setting in ProjectSettings.get_property_list():
		var autoload: String = setting.name.trim_prefix("autoload/")
		if setting.name.begins_with("autoload/") and get_tree().root.has_node(autoload):
			names.append(autoload)
			values.append(get_tree().root.get_node(autoload))
	for singleton in Engine.get_singleton_list():
		if not names.has(singleton):
			names.append(singleton)
			values.append(Engine.get_singleton(singleton))
	return [names, values]


## A step's --until and --trace: Godot Expressions, evaluated as eval does after every `every` frames of the step.
## args.until: the step ends after the first check where it's truthy (args.frames is then the most it runs).
## args.trace: [expression, ...], each one's value at every check. Returns {} when there are neither, or {"error"}.
func _watch_start(args: Dictionary) -> Dictionary:
	var until: String = "" if args.get("until") == null else str(args.until)
	var traces: Array = []
	if args.get("trace") is String:
		traces = [args.trace]
	elif args.get("trace") is Array:
		traces = args.trace
	if until.is_empty() and traces.is_empty():
		return {}
	var inputs := _eval_inputs()
	var watch := {"every": maxi(int(args.get("every", 1)), 1), "names": inputs[0], "values": inputs[1],
			"traces": [], "texts": traces, "rows": [], "failed": {}}
	for text in traces:
		var expression := Expression.new()
		if expression.parse(str(text), inputs[0]) != OK:
			return {"error": "trace %s: %s" % [text, expression.get_error_text()]}
		watch.traces.append(expression)
	if not until.is_empty():
		var expression := Expression.new()
		if expression.parse(until, inputs[0]) != OK:
			return {"error": "until %s: %s" % [until, expression.get_error_text()]}
		watch.until = expression
		watch.state = {"expr": until, "met": false, "value": null, "frame": _game_frames, "checks": 0}
	return watch


## Checks after the step's nth frame, when n is a multiple of every. Returns whether until holds.
func _watch_check(watch: Dictionary, n: int) -> bool:
	if n % watch.every != 0:
		return false
	var scene := get_tree().current_scene
	watch.values[1] = scene
	if not watch.traces.is_empty():
		var row := [_game_frames]
		for i in watch.traces.size():
			row.append(_watch_value(watch, watch.traces[i], watch.texts[i], scene))
		watch.rows.append(row)
	if not watch.has("until"):
		return false
	var state: Dictionary = watch.state
	state.checks += 1
	state.frame = _game_frames
	state.erase("error")
	var expression: Expression = watch.until
	var value: Variant = expression.execute(watch.values, scene, false)
	if expression.has_execute_failed():
		# A node that isn't there yet, a scene changing: not yet, and the reply says why if it never holds.
		state.value = null
		state.error = expression.get_error_text()
		return false
	state.value = _to_json(value)
	state.met = true if value else false
	return state.met


## A traced expression's value, or null when it fails (and the result's "failed" says why).
func _watch_value(watch: Dictionary, expression: Expression, text: String, scene: Node) -> Variant:
	var value: Variant = expression.execute(watch.values, scene, false)
	if expression.has_execute_failed():
		watch.failed[text] = expression.get_error_text()
		return null
	return _to_json(value)


func _watch_result(watch: Dictionary) -> Dictionary:
	var out := {}
	if watch.has("until"):
		out.until = watch.state
	if not watch.traces.is_empty():
		out.trace = {"exprs": watch.texts, "every": watch.every, "rows": watch.rows}
		if not watch.failed.is_empty():
			out.trace.failed = watch.failed
	return out


# --- Helpers --------------------------------------------------------------------

func _status() -> Dictionary:
	var scene := get_tree().current_scene
	return {
		"scene": scene.scene_file_path if scene else "",
		"held": _held,
		"frame": _game_frames,
		"ticks_per_second": ticks_per_second,
		"image_size": Common.image_size(get_tree()),
		"window_size": [DisplayServer.window_get_size().x, DisplayServer.window_get_size().y],
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


func _describe(node: Node, scene: Node, depth: int, budget: Array, visible_only := false) -> Dictionary:
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
	if visible_only:
		children = children.filter(func(child: Node) -> bool: return Screen.shows(child))
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
		d.children.append(_describe(child, scene, depth - 1, budget, visible_only))
	return d


## Where the injected pointer is (window coordinates) and which buttons it holds: a motion carries how far it moved
## and the buttons down, as a real one does, so a drag or a point-while-held reaches the game the way a player's does.
var _mouse_at := Vector2.ZERO
var _mouse_mask := 0


func _make_event(spec: Dictionary) -> InputEvent:
	var pressed: bool = spec.get("pressed", true)
	if spec.has("action"):
		var e := InputEventAction.new()
		e.action = spec.action
		e.pressed = pressed
		e.strength = float(spec.get("strength", 1.0)) if pressed else 0.0
		return e
	if spec.has("text"):
		# A typed character: the key it's on (when it has one) and the character itself, with Shift for a capital,
		# so a LineEdit or TextEdit takes it as a keyboard's typing.
		var ch: String = spec.text
		var e := InputEventKey.new()
		var key := OS.find_keycode_from_string(ch.to_upper()) if ch != " " else KEY_SPACE
		e.keycode = key
		e.physical_keycode = key
		e.unicode = ch.unicode_at(0)
		e.shift_pressed = ch != ch.to_lower()
		e.pressed = pressed
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
		# Without a position, the button goes where the pointer is.
		e.position = Common.shot_to_window(get_tree(), Vector2(spec.position[0], spec.position[1])) if spec.has("position") else _mouse_at
		e.global_position = e.position
		e.pressed = pressed
		var bit := 1 << (e.button_index - 1)
		_mouse_mask = (_mouse_mask | bit) if pressed else (_mouse_mask & ~bit)
		e.button_mask = _mouse_mask
		_mouse_at = e.position
		return e
	if spec.has("mouse_motion"):
		var e := InputEventMouseMotion.new()
		e.position = Common.shot_to_window(get_tree(), Vector2(spec.mouse_motion[0], spec.mouse_motion[1]))
		e.global_position = e.position
		e.relative = e.position - _mouse_at
		e.screen_relative = e.relative
		e.button_mask = _mouse_mask
		_mouse_at = e.position
		return e
	return null


func _save_image(label: String) -> String:
	_shot_count += 1
	var dir := out_dir.path_join("shots")
	DirAccess.make_dir_recursive_absolute(dir)
	var path := dir.path_join("%04d-%s.png" % [_shot_count, label])
	Common.save_frame(get_tree(), path)  # written by the time the step replies (Common.wait_saves)
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
			var items := Array(v)
			var kept := items.slice(0, JSON_MAX_ITEMS).map(_to_json)
			if items.size() > JSON_MAX_ITEMS:
				kept.append("... %d more (%d in all)" % [items.size() - JSON_MAX_ITEMS, items.size()])
			return kept
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
