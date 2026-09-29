extends SceneTree
## Loads one scene, renders it, saves a PNG per debug-draw mode, runs the
## probes in probes.gd, and writes report.json.
##
## Run through bin/gdh, or directly:
##   godot --path <project> --script <this file> -- --scene res://x.tscn --out /abs/dir
## Options: --warmup <frames> (default 30), --modes normal,wireframe,... (default: all).

const Probes := preload("probes.gd")

const MODES := {
	"normal": Viewport.DEBUG_DRAW_DISABLED,
	"unshaded": Viewport.DEBUG_DRAW_UNSHADED,
	"lighting": Viewport.DEBUG_DRAW_LIGHTING,
	"normals": Viewport.DEBUG_DRAW_NORMAL_BUFFER,
	"wireframe": Viewport.DEBUG_DRAW_WIREFRAME,
	"overdraw": Viewport.DEBUG_DRAW_OVERDRAW,
}

const MONITORS := {
	"fps": Performance.TIME_FPS,
	"objects_in_frame": Performance.RENDER_TOTAL_OBJECTS_IN_FRAME,
	"primitives_in_frame": Performance.RENDER_TOTAL_PRIMITIVES_IN_FRAME,
	"draw_calls_in_frame": Performance.RENDER_TOTAL_DRAW_CALLS_IN_FRAME,
	"video_mem_used": Performance.RENDER_VIDEO_MEM_USED,
	"node_count": Performance.OBJECT_NODE_COUNT,
	"orphan_node_count": Performance.OBJECT_ORPHAN_NODE_COUNT,
}


class ErrorCollector extends Logger:
	const TYPE_NAMES := ["error", "warning", "script", "shader"]
	var entries: Array[Dictionary] = []
	var _mutex := Mutex.new()

	func _log_error(function: String, file: String, line: int, code: String, rationale: String,
			_editor_notify: bool, error_type: int, _backtraces: Array[ScriptBacktrace]) -> void:
		_mutex.lock()
		entries.append({
			"type": TYPE_NAMES[error_type] if error_type < TYPE_NAMES.size() else str(error_type),
			"message": rationale if not rationale.is_empty() else code,
			"code": code,
			"where": "%s:%d (%s)" % [file, line, function],
		})
		_mutex.unlock()


var _errors := ErrorCollector.new()
var _args := {}


func _initialize() -> void:
	OS.add_logger(_errors)
	_args = _parse_args(OS.get_cmdline_user_args())
	if not _args.has("scene") or not _args.has("out"):
		printerr("capture.gd: --scene and --out are required")
		quit(2)
		return
	DirAccess.make_dir_recursive_absolute(_args.out)
	# Wireframe data is only built for meshes created after this call.
	RenderingServer.set_debug_generate_wireframes(true)
	var packed := load(_args.scene) as PackedScene
	if packed == null:
		_write_report({"loaded": false})
		quit(3)
		return
	change_scene_to_packed(packed)
	_run.call_deferred()


func _run() -> void:
	for i in int(_args.get("warmup", 30)):
		await process_frame
	var monitors := {}
	for key in MONITORS:
		monitors[key] = Performance.get_monitor(MONITORS[key])

	var captured: Array[String] = []
	for mode in _args.get("modes", MODES.keys()):
		if not MODES.has(mode):
			printerr("capture.gd: unknown mode ", mode)
			continue
		root.debug_draw = MODES[mode]
		for i in 3:
			await process_frame
		await RenderingServer.frame_post_draw
		root.get_texture().get_image().save_png(_args.out.path_join(mode + ".png"))
		captured.append(mode)
	root.debug_draw = Viewport.DEBUG_DRAW_DISABLED

	# Probes run after every image is saved, because they add temporary
	# collision bodies to the scene.
	var probes: Dictionary = await Probes.new().run(self)
	_write_report({
		"loaded": true,
		"captured": captured,
		"monitors": monitors,
		"findings": probes.findings,
		"probe_stats": probes.stats,
	})
	quit(0)


func _write_report(extra: Dictionary) -> void:
	var report := {
		"scene": _args.scene,
		"adapter": RenderingServer.get_video_adapter_name(),
		"rendering_method": RenderingServer.get_current_rendering_method(),
		"rendering_driver": RenderingServer.get_current_rendering_driver_name(),
		"resolution": [root.size.x, root.size.y],
		"viewport_size": [root.get_visible_rect().size.x, root.get_visible_rect().size.y],
		"errors": _errors.entries,
	}
	report.merge(extra)
	var f := FileAccess.open(_args.out.path_join("report.json"), FileAccess.WRITE)
	f.store_string(JSON.stringify(report, "  "))


func _parse_args(argv: PackedStringArray) -> Dictionary:
	var out := {}
	var i := 0
	while i < argv.size():
		var key := argv[i].trim_prefix("--")
		var value := argv[i + 1] if i + 1 < argv.size() else ""
		match key:
			"modes":
				out.modes = Array(value.split(",", false))
			_:
				out[key] = value
		i += 2
	return out
