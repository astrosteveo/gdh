extends Node
## Records the frames' render times for `gdh live frames`: the root viewport's GPU and CPU time, in ms, and, when the
## renderer captures its timestamps (gdh live start --gpu-passes, which runs Godot with --gpu-profile), each render
## pass's GPU time. live.gd adds it beside the bridge.
##
## The times come from the rendering device's captured timestamps, which arrive a frame or two late and not on every
## frame: each game frame, if the device has handed over a frame not yet recorded, that frame is recorded, so no
## frame is counted twice and none holds a stale value (Viewport's measured render time repeats its last value on a
## frame with no new timestamps, and stays put when every frame is read back for a screenshot). The root viewport's
## times are its own "vp_begin_N" to "vp_end_N" stamps, which Viewport's measured render time reads too. Held frames
## aren't recorded, so the record covers the frames stepped (or run).
##
## A pass is the time from its timestamp to the next one, as Godot's own GPU profile takes it: names starting ">"
## open a group and "<" close it (a group's time is from its ">" to its "<"); every other name is a pass, and repeats
## of one name in a frame add up. A game's own passes show too: RenderingDevice.capture_timestamp("Name") where its
## pass starts (in a CompositorEffect's _render_callback, say).

const MAX_FRAMES := 36000  # ten minutes at 60 frames a second; the oldest go first

var frames: Array[Dictionary] = []
var game_frames := 0  # game frames run since the record started over
var others: Array = []  # the other games gdh found on the machine when the record started over (gdh live frames)
var _last_captured := -1


func _ready() -> void:
	process_mode = PROCESS_MODE_ALWAYS
	# After every game node, before the bridge (which holds the game as a step ends).
	process_priority = (1 << 30) - 1
	# The root viewport's own timestamps, vp_begin_N and vp_end_N.
	RenderingServer.viewport_set_measure_render_time(get_tree().root.get_viewport_rid(), true)


func _process(_delta: float) -> void:
	if get_tree().paused:
		return
	game_frames += 1
	var rd := RenderingServer.get_rendering_device()
	if rd == null:
		return
	var count := rd.get_captured_timestamps_count()
	var captured := rd.get_captured_timestamps_frame()
	if count < 2 or captured == _last_captured:
		return
	_last_captured = captured
	var record := _read(rd, count)
	if record.is_empty():
		return
	frames.append(record)
	if frames.size() > MAX_FRAMES:
		frames.pop_front()


func _read(rd: RenderingDevice, count: int) -> Dictionary:
	var root_id := str(get_tree().root.get_viewport_rid().get_id())
	var gpu := PackedFloat64Array()
	var cpu := PackedFloat64Array()
	var names := PackedStringArray()
	for i in count:
		gpu.append(rd.get_captured_timestamp_gpu_time(i) / 1e6)  # the GPU's clock is in nanoseconds
		cpu.append(rd.get_captured_timestamp_cpu_time(i) / 1e3)  # the CPU's in microseconds
		names.append(rd.get_captured_timestamp_name(i))
	var begin := names.find("vp_begin_" + root_id)
	var end := names.find("vp_end_" + root_id)
	if begin < 0 or end < begin:
		return {}
	var record := {"gpu": gpu[end] - gpu[begin], "cpu": cpu[end] - cpu[begin], "frame": rd.get_captured_timestamps_frame()}
	var passes := {}
	var groups := {}
	var open := {}
	for i in count:
		var name := names[i]
		if name.begins_with(">"):
			open[name.substr(1).strip_edges()] = gpu[i]
		elif name.begins_with("<"):
			var group := name.substr(1).strip_edges()
			if open.has(group):
				groups[group] = groups.get(group, 0.0) + gpu[i] - open[group]
				open.erase(group)
		elif i + 1 < count and not name.begins_with("vp_"):
			passes[name] = passes.get(name, 0.0) + maxf(gpu[i + 1] - gpu[i], 0.0)
	if not passes.is_empty():
		record["passes"] = passes
	if not groups.is_empty():
		record["groups"] = groups
	return record


## gdh live frames: {"reset": bool, "clear": bool, "others": [...]}. Returns the frames recorded since the record
## last started over, how many game frames ran in that time, the window's size and the GPU. "reset" starts the record
## over after reading it; "clear" starts it over and returns nothing. "others" (the other games gdh found running as
## the record starts over) is kept with the record and returned with it as "others_at_start".
func command(args: Dictionary) -> Dictionary:
	if args.get("clear", false):
		frames.clear()
		game_frames = 0
		others = args.get("others", [])
		return {"frames": [], "game_frames": 0, "cleared": true}
	var size := get_tree().root.get_visible_rect().size
	var out := {"frames": frames.duplicate(), "game_frames": game_frames, "size": [size.x, size.y],
			"adapter": RenderingServer.get_video_adapter_name(), "others_at_start": others}
	if args.get("reset", false):
		frames.clear()
		game_frames = 0
		others = args.get("others", [])
	return out
