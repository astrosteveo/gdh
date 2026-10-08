extends RefCounted
## Godot's Performance monitors, for `gdh live monitors` and `step --monitors`: the counts of objects, resources,
## nodes and orphan nodes (nodes out of the tree that nothing has freed), what the last frame drew, the GPU's and the
## engine's memory, active physics bodies, the render pipelines compiled so far, and the game's own monitors
## (Performance.add_custom_monitor), as "custom/<id>". Memory is in bytes. (Godot's process times aren't here: it
## updates them once a second, with the slowest frame's, so they say little about a step.)

const MONITORS := {
	"objects": Performance.OBJECT_COUNT,
	"resources": Performance.OBJECT_RESOURCE_COUNT,
	"nodes": Performance.OBJECT_NODE_COUNT,
	"orphan_nodes": Performance.OBJECT_ORPHAN_NODE_COUNT,
	"draw_calls": Performance.RENDER_TOTAL_DRAW_CALLS_IN_FRAME,
	"objects_drawn": Performance.RENDER_TOTAL_OBJECTS_IN_FRAME,
	"primitives": Performance.RENDER_TOTAL_PRIMITIVES_IN_FRAME,
	"video_mem": Performance.RENDER_VIDEO_MEM_USED,
	"texture_mem": Performance.RENDER_TEXTURE_MEM_USED,
	"buffer_mem": Performance.RENDER_BUFFER_MEM_USED,
	"static_mem": Performance.MEMORY_STATIC,
	"physics_2d_active": Performance.PHYSICS_2D_ACTIVE_OBJECTS,
	"physics_3d_active": Performance.PHYSICS_3D_ACTIVE_OBJECTS,
}

const PIPELINES := [
	Performance.PIPELINE_COMPILATIONS_CANVAS,
	Performance.PIPELINE_COMPILATIONS_MESH,
	Performance.PIPELINE_COMPILATIONS_SURFACE,
	Performance.PIPELINE_COMPILATIONS_DRAW,
	Performance.PIPELINE_COMPILATIONS_SPECIALIZATION,
]


## The monitors now, at game frame `frame`.
static func read(frame: int) -> Dictionary:
	var out := {"frame": frame}
	for key: String in MONITORS:
		out[key] = int(Performance.get_monitor(MONITORS[key]))
	var pipelines := 0
	for monitor: int in PIPELINES:
		pipelines += int(Performance.get_monitor(monitor))
	out.pipelines_compiled = pipelines
	for id in Performance.get_custom_monitor_names():
		var value: Variant = Performance.get_custom_monitor(id)
		if value is int or value is float:
			out["custom/" + str(id)] = value
	return out
