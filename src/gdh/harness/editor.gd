extends SceneTree
## Opens scenes in the Godot editor and saves what it shows: the 3D (or 2D) viewport, the whole editor window, and a
## report. Run through `gdh editor`, which starts `godot --editor --path <project> --script <this file>`: the editor
## runs as usual, with this script as its main loop, so the project's tool scripts, plugins and importers all run as
## they do for a person, and nothing is installed into the project.
##
## Options come in GDH_ARGS ("--key value" pairs): --out <dir>; --scene res://x.tscn (repeatable; each scene's files
## go in a folder of its own when there are several); --warmup <frames> after a scene opens; --idle <seconds> to count
## the editor's redraws while nothing happens; --set <node>:<property>=<value> (repeatable, applied in memory, never
## saved); --select <node>; --focus <node> (the View menu's Focus Selection); --view <x,y,z>:<x,y,z> (look from the
## first point at the second, through a camera the harness adds and previews); --far <metres> for that camera;
## --timeout <seconds> to wait for the editor; --save to save each scene through the editor (File → Save Scene) after
## it's captured, as a person would, so a test can see what a save writes.

const ErrorCollector := preload("errors.gd")

## Options that can be given more than once.
const LISTS := ["scene", "set"]

var _errors := ErrorCollector.new()
var _opts := {}
var _failed := false


func _initialize() -> void:
	OS.add_logger(_errors)
	_opts = _parse_args()
	_run.call_deferred()


func _parse_args() -> Dictionary:
	var parsed = JSON.parse_string(OS.get_environment("GDH_ARGS"))
	var argv: Array = parsed if parsed is Array else []
	var out := {}
	for key in LISTS:
		out[key] = []
	var i := 0
	while i < argv.size():
		var key := str(argv[i]).trim_prefix("--")
		var value := str(argv[i + 1]) if i + 1 < argv.size() else ""
		if key in LISTS:
			out[key].append(value)
		else:
			out[key] = value
		i += 2
	return out


func _run() -> void:
	var out: String = _opts.get("out", "")
	var started := Time.get_ticks_msec()
	if not await _editor_ready(float(_opts.get("timeout", "300"))):
		_write_json(out.path_join("editor.json"), {"ready": false, "errors": _errors.drain()})
		quit(4)
		return
	var startup := {"ready": true, "ready_ms": Time.get_ticks_msec() - started, "errors": _errors.drain(),
		"adapter": RenderingServer.get_video_adapter_name(), "godot": Engine.get_version_info().string}
	_write_json(out.path_join("editor.json"), startup)
	var scenes: Array = _opts.scene
	for scene in scenes:
		var dir := out if scenes.size() == 1 else out.path_join(_folder_name(scene))
		DirAccess.make_dir_recursive_absolute(dir)
		await _capture(scene, dir)
	quit(1 if _failed else 0)


## Waits for the editor to finish starting: its first scan of the project done, and nothing importing.
func _editor_ready(timeout_s: float) -> bool:
	var deadline := Time.get_ticks_msec() + int(timeout_s * 1000)
	var quiet := 0
	while Time.get_ticks_msec() < deadline:
		await process_frame
		var fs := EditorInterface.get_resource_filesystem()
		if fs == null or fs.is_scanning():
			quiet = 0
			continue
		quiet += 1
		if quiet >= 30:
			return true
	return false


func _capture(scene: String, dir: String) -> void:
	var report := {"scene": scene}
	if not ResourceLoader.exists(scene):
		report.error = "No scene at %s." % scene
		_finish(report, dir)
		return
	_errors.drain()
	var opened_at := Time.get_ticks_msec()
	EditorInterface.open_scene_from_path(scene)
	var edited := await _edited_root(scene, 600)
	if edited == null:
		report.error = "The editor didn't open %s." % scene
		report.errors = _errors.drain()
		_finish(report, dir)
		return
	var is_3d := edited is Node3D
	EditorInterface.set_main_screen_editor("3D" if is_3d else "2D")
	await _drawn()
	report.open_ms = Time.get_ticks_msec() - opened_at
	report.open_errors = _errors.drain()

	var notes: Array[String] = []
	for spec in _opts.set:
		notes.append_array(_apply_set(edited, spec))
	var camera: Camera3D = null
	if is_3d and _opts.has("view"):
		camera = await _view_camera(edited, _opts.view, notes)
	else:
		if _opts.has("focus"):
			if is_3d:
				notes.append_array(await _focus(edited, _opts.focus))
			else:
				notes.append_array(await _frame_2d(edited, _opts.focus))
		if is_3d and (_opts.has("orbit") or _opts.has("zoom")):
			notes.append_array(await _navigate(_opts.get("orbit", ""), int(_opts.get("zoom", "0"))))
	if _opts.has("select"):
		var node := edited.get_node_or_null(NodePath(_opts.select))
		if node == null:
			notes.append("--select: no node %s" % _opts.select)
		else:
			await _select(node)
	for i in int(_opts.get("warmup", "60")):
		await process_frame
	await _drawn()

	var viewport: SubViewport = EditorInterface.get_editor_viewport_3d(0) if is_3d else EditorInterface.get_editor_viewport_2d()
	report.viewport_size = [viewport.size.x, viewport.size.y]
	report.window_size = [root.size.x, root.size.y]
	report.idle = await _idle(float(_opts.get("idle", "2")))
	report.redraw = await _redraw_cost(viewport)
	await _drawn()
	viewport.get_texture().get_image().save_png(dir.path_join("viewport.png"))
	root.get_texture().get_image().save_png(dir.path_join("editor.png"))
	if is_3d:
		var eye := (viewport.get_camera_3d() if camera == null else camera)
		report.camera = {"position": _vec(eye.global_position), "forward": _vec(-eye.global_basis.z),
			"fov": eye.fov, "near": eye.near, "far": eye.far, "previewing": camera != null}
	report.tree = _tree(edited, edited, 0)
	report.notes = notes
	report.errors = _errors.drain()
	if camera != null:
		_end_preview(camera)
	if _opts.has("save"):
		var path := ProjectSettings.globalize_path(scene)
		var was := FileAccess.get_file_as_string(path)
		EditorInterface.save_scene()
		for i in 5:
			await process_frame
		report.saved = {"changed": FileAccess.get_file_as_string(path) != was, "errors": _errors.drain()}
	_finish(report, dir)
	EditorInterface.close_scene()
	for i in 5:
		await process_frame


## A frame drawn now. The editor draws only when something changes (low processor mode), so a frame waited for may
## never come.
func _drawn() -> void:
	RenderingServer.force_draw(true)
	await process_frame


## The edited scene's root once the editor has it open (null if it doesn't within `frames`).
func _edited_root(scene: String, frames: int) -> Node:
	for i in frames:
		await process_frame
		var edited := EditorInterface.get_edited_scene_root()
		if edited != null and edited.scene_file_path == scene:
			return edited
	return null


## --set node:property=value, in memory only. The value is read as Godot's own syntax (str_to_var); a res:// path to a
## resource is that resource, loaded; anything else is plain text.
func _apply_set(edited: Node, spec: String) -> Array[String]:
	var colon := spec.find(":")
	var equals := spec.find("=", colon)
	if colon < 0 or equals < 0:
		return ["--set takes node:property=value, not %s" % spec]
	var node := edited.get_node_or_null(NodePath(spec.substr(0, colon)))
	if node == null:
		return ["--set: no node %s" % spec.substr(0, colon)]
	var property := spec.substr(colon + 1, equals - colon - 1)
	var text := spec.substr(equals + 1)
	var value = str_to_var(text)
	if value == null and text != "null":
		value = load(text) if text.begins_with("res://") and ResourceLoader.exists(text) else text
	if not property in node:
		return ["--set: %s has no property %s" % [spec.substr(0, colon), property]]
	node.set(property, value)
	return []


## Centers the view on a node, as the View menu's Focus Selection does (the view keeps its distance).
func _focus(edited: Node, path: String) -> Array[String]:
	var node := edited.get_node_or_null(NodePath(path))
	if node == null:
		return ["--focus: no node %s" % path]
	await _select(node)
	var item := _view_menu_item("Focus Selection")
	if item.is_empty():
		return ["--focus: the 3D view's View menu has no Focus Selection"]
	item.popup.id_pressed.emit(item.id)
	return []


## Moves the first 3D view's camera as a person would: dragging with the middle button (orbit, in pixels), then
## turning the mouse wheel (zoom: steps out, or in when negative).
func _navigate(orbit: String, zoom: int) -> Array[String]:
	var surface := _viewport_surface()
	if surface == null:
		return ["--orbit/--zoom: no 3D view to move"]
	var at := surface.size / 2
	if not orbit.is_empty():
		var drag := orbit.split(",")
		if drag.size() != 2:
			return ["--orbit takes DX,DY, not %s" % orbit]
		var total := Vector2(float(drag[0]), float(drag[1]))
		var steps := maxi(1, int(ceil(total.length() / 20.0)))
		for i in steps:
			var motion := InputEventMouseMotion.new()
			motion.button_mask = MOUSE_BUTTON_MASK_MIDDLE
			motion.position = at
			motion.relative = total / steps
			motion.screen_relative = total / steps
			surface.gui_input.emit(motion)
			await process_frame
	for i in absi(zoom):
		var wheel := InputEventMouseButton.new()
		wheel.button_index = MOUSE_BUTTON_WHEEL_DOWN if zoom > 0 else MOUSE_BUTTON_WHEEL_UP
		wheel.pressed = true
		wheel.factor = 1.0
		wheel.position = at
		surface.gui_input.emit(wheel)
		await process_frame
	return []


## The control over the first 3D view that takes the mouse (the viewport's surface).
func _viewport_surface() -> Control:
	var panel := _viewport_panel()
	if panel == null:
		return null
	for child in panel.get_children():
		if child.get_class() == "Control":
			return child
	return null


## Frames a node in the 2D view, as the 2D View menu's Frame Selection does: centred, zoomed to fit.
func _frame_2d(edited: Node, path: String) -> Array[String]:
	var node := edited.get_node_or_null(NodePath(path))
	if node == null:
		return ["--focus: no node %s" % path]
	await _select(node)
	var editor: Node = EditorInterface.get_editor_viewport_2d()
	while editor != null and editor.get_class() != "CanvasItemEditor":
		editor = editor.get_parent()
	if editor == null:
		return ["--focus: no 2D editor"]
	for button in editor.find_children("*", "MenuButton", true, false):
		var popup: PopupMenu = button.get_popup()
		for i in popup.item_count:
			if popup.get_item_text(i) == "Frame Selection":
				popup.id_pressed.emit(popup.get_item_id(i))
				for f in 5:
					await process_frame
				return []
	return ["--focus: the 2D view's View menu has no Frame Selection"]


## Selects a node, as clicking it in the Scene dock does: the inspector shows it, and the editor takes a few frames to
## see the selection (its gizmos, the viewport's Preview box for a camera).
func _select(node: Node) -> void:
	EditorInterface.get_selection().clear()
	EditorInterface.get_selection().add_node(node)
	EditorInterface.edit_node(node)
	for i in 5:
		await process_frame


## An item of the first 3D viewport's View menu, by its text: {popup, id}, or {} if there's none.
func _view_menu_item(text: String) -> Dictionary:
	var panel := _viewport_panel()
	if panel == null:
		return {}
	for button in panel.find_children("*", "MenuButton", true, false):
		var popup: PopupMenu = button.get_popup()
		for i in popup.item_count:
			if popup.get_item_text(i) == text:
				return {"popup": popup, "id": popup.get_item_id(i)}
	return {}


## The control that holds the first 3D viewport and its overlay (its menus and the Preview box).
func _viewport_panel() -> Control:
	var node: Node = EditorInterface.get_editor_viewport_3d(0)
	while node != null and node.get_class() != "Node3DEditorViewport":
		node = node.get_parent()
	return node as Control


## --view x,y,z:x,y,z: a camera at the first point looking at the second, added to the edited scene (not owned, so
## never saved) and shown through the viewport's Preview. Its lens is the editor camera's unless --far says otherwise.
func _view_camera(edited: Node, spec: String, notes: Array[String]) -> Camera3D:
	var points := spec.split(":")
	if points.size() != 2:
		notes.append("--view takes x,y,z:x,y,z, not %s" % spec)
		return null
	var from := _parse_vec(points[0])
	var at := _parse_vec(points[1])
	var editor_camera := EditorInterface.get_editor_viewport_3d(0).get_camera_3d()
	var camera := Camera3D.new()
	camera.name = "GdhView"
	camera.fov = editor_camera.fov
	camera.near = editor_camera.near
	camera.far = float(_opts.get("far", str(editor_camera.far)))
	edited.add_child(camera, false, Node.INTERNAL_MODE_BACK)
	camera.look_at_from_position(from, at, Vector3.UP if absf((at - from).normalized().y) < 0.99 else Vector3.FORWARD)
	await _select(camera)
	var preview := _preview_box()
	if preview == null:
		notes.append("--view: the viewport has no Preview box to show the camera through")
		return camera
	preview.button_pressed = true
	return camera


func _preview_box() -> CheckBox:
	var panel := _viewport_panel()
	if panel == null:
		return null
	for box in panel.find_children("*", "CheckBox", true, false):
		if box.text == "Preview":
			return box
	return null


func _end_preview(camera: Camera3D) -> void:
	var preview := _preview_box()
	if preview != null:
		preview.button_pressed = false
	EditorInterface.get_selection().clear()
	camera.queue_free()


## How often the editor redraws over `seconds` when nothing happens: a scene whose tool scripts change something
## every frame keeps it redrawing all the time.
func _idle(seconds: float) -> Dictionary:
	var frames := Engine.get_frames_drawn()
	var started := Time.get_ticks_usec()
	await create_timer(seconds).timeout
	var elapsed := (Time.get_ticks_usec() - started) / 1e6
	return {"seconds": snappedf(elapsed, 0.01), "redraws": Engine.get_frames_drawn() - frames,
		"redraws_per_second": snappedf((Engine.get_frames_drawn() - frames) / elapsed, 0.1)}


## What one redraw of the viewport costs: its render time on the CPU and the GPU, as Godot measures them, over 30
## redraws.
func _redraw_cost(viewport: SubViewport) -> Dictionary:
	var rid := viewport.get_viewport_rid()
	RenderingServer.viewport_set_measure_render_time(rid, true)
	var cpu: Array[float] = []
	var gpu: Array[float] = []
	for i in 32:
		RenderingServer.force_draw(false)
		if i >= 2:
			cpu.append(RenderingServer.viewport_get_measured_render_time_cpu(rid))
			gpu.append(RenderingServer.viewport_get_measured_render_time_gpu(rid))
		await process_frame
	RenderingServer.viewport_set_measure_render_time(rid, false)
	cpu.sort()
	gpu.sort()
	return {"frames": cpu.size(), "cpu_ms_median": snappedf(cpu[cpu.size() / 2], 0.01),
		"gpu_ms_median": snappedf(gpu[gpu.size() / 2], 0.01), "gpu_ms_worst": snappedf(gpu[-1], 0.01)}


## The scene as the Scene dock lists it: each node the scene owns, and how many nodes its tool scripts made under it
## (not owned, so never saved).
func _tree(node: Node, edited: Node, depth: int) -> Dictionary:
	var entry := {"name": str(node.name), "class": node.get_class()}
	var script: Script = node.get_script()
	if script != null:
		entry.script = script.resource_path
	if node != edited and not node.scene_file_path.is_empty():
		entry.instance = node.scene_file_path
	var children := []
	var made := 0
	for child in node.get_children(true):
		if _owned_by(child, edited):
			if depth < 6:
				children.append(_tree(child, edited, depth + 1))
		else:
			made += 1 + _count(child)
	if not children.is_empty():
		entry.children = children
	if made > 0:
		entry.made_by_scripts = made
	return entry


## Whether a node is part of the edited scene: owned by it, or by a scene instanced in it.
func _owned_by(node: Node, edited: Node) -> bool:
	var holder := node.owner
	while holder != null:
		if holder == edited:
			return true
		holder = holder.owner
	return false


func _count(node: Node) -> int:
	var n := 0
	for child in node.get_children(true):
		n += 1 + _count(child)
	return n


func _finish(report: Dictionary, dir: String) -> void:
	if report.has("error"):
		_failed = true
	_write_json(dir.path_join("report.json"), report)


func _write_json(path: String, data: Dictionary) -> void:
	var f := FileAccess.open(path, FileAccess.WRITE)
	if f == null:
		push_error("Can't write %s" % path)
		return
	f.store_string(JSON.stringify(data, "  "))


func _folder_name(scene: String) -> String:
	return scene.trim_prefix("res://").trim_suffix(".tscn").trim_suffix(".scn").replace("/", "__")


func _parse_vec(text: String) -> Vector3:
	var parts := text.split(",")
	if parts.size() != 3:
		return Vector3.ZERO
	return Vector3(float(parts[0]), float(parts[1]), float(parts[2]))


func _vec(v: Vector3) -> Array:
	return [snappedf(v.x, 0.01), snappedf(v.y, 0.01), snappedf(v.z, 0.01)]
