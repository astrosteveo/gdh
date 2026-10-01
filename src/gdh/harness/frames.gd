extends Node
## Records each game frame's render times for `gdh live frames`: the root viewport's GPU and CPU time (Godot's
## measured render time, in ms), and, when the renderer captures its timestamps (gdh live start --gpu-passes, which
## runs Godot with --gpu-profile), each render pass's GPU time. live.gd adds it beside the bridge.
##
## Frames run while held aren't recorded, so the record covers the frames stepped (or run). A pass is the time from
## its timestamp to the next one, as Godot's own GPU profile takes it: names starting ">" open a group and "<" close
## it (a group's time is from its ">" to its "<"); every other name is a pass, and repeats of one name in a frame add
## up. A game's own passes show too: RenderingDevice.capture_timestamp("Name") where its pass starts (in a
## CompositorEffect's _render_callback, say). Timestamps arrive a frame or two late, so each frame records the
## newest captured frame not yet recorded.

const MAX_FRAMES := 36000  # ten minutes at 60 frames a second; the oldest go first

var frames: Array[Dictionary] = []
var _last_captured := -1


func _ready() -> void:
	process_mode = PROCESS_MODE_ALWAYS
	# After every game node, before the bridge (which pauses the game as a step ends).
	process_priority = (1 << 30) - 1
	RenderingServer.viewport_set_measure_render_time(get_tree().root.get_viewport_rid(), true)


func _process(_delta: float) -> void:
	if get_tree().paused:
		return
	var viewport := get_tree().root.get_viewport_rid()
	var record := {
		"gpu": RenderingServer.viewport_get_measured_render_time_gpu(viewport),
		"cpu": RenderingServer.viewport_get_measured_render_time_cpu(viewport),
	}
	var rd := RenderingServer.get_rendering_device()
	if rd != null and rd.get_captured_timestamps_count() > 1:
		var captured := rd.get_captured_timestamps_frame()
		if captured != _last_captured:
			_last_captured = captured
			_add_passes(rd, record)
	frames.append(record)
	if frames.size() > MAX_FRAMES:
		frames.pop_front()


func _add_passes(rd: RenderingDevice, record: Dictionary) -> void:
	var passes := {}
	var groups := {}
	var open := {}
	var count := rd.get_captured_timestamps_count()
	var times := PackedFloat64Array()
	var names := PackedStringArray()
	for i in count:
		# The GPU's clock is in nanoseconds.
		times.append(rd.get_captured_timestamp_gpu_time(i) / 1e6)
		names.append(rd.get_captured_timestamp_name(i))
	for i in count:
		var name := names[i]
		if name.begins_with(">"):
			open[name.substr(1).strip_edges()] = times[i]
		elif name.begins_with("<"):
			var group := name.substr(1).strip_edges()
			if open.has(group):
				groups[group] = groups.get(group, 0.0) + times[i] - open[group]
				open.erase(group)
		elif i + 1 < count and not name.begins_with("vp_"):
			passes[name] = passes.get(name, 0.0) + maxf(times[i + 1] - times[i], 0.0)
	record["passes"] = passes
	record["groups"] = groups


## gdh live frames: {"reset": bool, "clear": bool}. Returns the frames recorded since the record was last started
## over, the window's size and the GPU; "reset" starts it over after reading, "clear" starts it over and returns none.
func command(args: Dictionary) -> Dictionary:
	if args.get("clear", false):
		frames.clear()
		return {"frames": [], "cleared": true}
	var out := {"frames": frames.duplicate(), "size": [get_tree().root.get_visible_rect().size.x, get_tree().root.get_visible_rect().size.y],
			"adapter": RenderingServer.get_video_adapter_name()}
	if args.get("reset", false):
		frames.clear()
	return out
