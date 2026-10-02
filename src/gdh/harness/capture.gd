extends SceneTree
## Loads one scene, renders it, saves a PNG per debug-draw mode, runs the
## probes in probes.gd, and writes report.json.
##
## Run through `gdh capture`, or directly:
##   godot --path <project> --script <this file> -- --scene res://x.tscn --out /abs/dir
## Options: --warmup <frames> (default 30), --modes normal,wireframe,... (default: all).

const Common := preload("common.gd")
const ErrorCollector := preload("errors.gd")
const Probes := preload("probes.gd")

const MONITORS := {
	"fps": Performance.TIME_FPS,
	"objects_in_frame": Performance.RENDER_TOTAL_OBJECTS_IN_FRAME,
	"primitives_in_frame": Performance.RENDER_TOTAL_PRIMITIVES_IN_FRAME,
	"draw_calls_in_frame": Performance.RENDER_TOTAL_DRAW_CALLS_IN_FRAME,
	"video_mem_used": Performance.RENDER_VIDEO_MEM_USED,
	"node_count": Performance.OBJECT_NODE_COUNT,
	"orphan_node_count": Performance.OBJECT_ORPHAN_NODE_COUNT,
}


var _errors := ErrorCollector.new()
var _args := {}


func _initialize() -> void:
	OS.add_logger(_errors)
	_args = Common.harness_args()
	if not _args.has("scene") or not _args.has("out"):
		printerr("capture.gd: GDH_ARGS needs --scene and --out")
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

	var saved: Dictionary = await Common.save_views(self, _args.get("modes", Common.VIEWS.keys()), _args.out)
	var captured: Array = saved.keys()

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
		"window_size": [DisplayServer.window_get_size().x, DisplayServer.window_get_size().y],
		"image_size": Common.image_size(self),
		"errors": _errors.all(),
	}
	report.merge(extra)
	var f := FileAccess.open(_args.out.path_join("report.json"), FileAccess.WRITE)
	f.store_string(JSON.stringify(report, "  "))

