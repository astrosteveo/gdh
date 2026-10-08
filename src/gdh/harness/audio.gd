extends Node
## Listens to the game's audio for `gdh live audio`: each bus's peak level over the last step (or since `run`), and
## the AudioStreamPlayers, AudioStreamPlayer2Ds and AudioStreamPlayer3Ds that played in it. live.gd adds it beside
## the bridge.
##
## gdh runs Godot with the Dummy audio driver. It mixes as a sound card's driver does, on a thread of its own in real
## time, a block of 4096 samples at a time (93 ms at 44.1 kHz), and sends the result nowhere: the buses' levels are
## real, and nothing reaches the speakers. Mixing follows the clock on the wall, not game time, so a step that runs
## faster than real time mixes less sound than the game time it covers, and a step shorter than a block may mix none.
##
## Held, the game's players pause with it, so the levels are taken only from blocks mixed while the game ran: once a
## block that began in the step has had time to finish, each bus's level (AudioServer.get_bus_peak_volume_*_db, the
## end of the block) counts toward its peak.

const SILENT_DB := -200.0
const MIX_DONE_US := 5000  # a block has mixed this long after it began
const MAX_IDLE := 50

var _players := {}  # instance id: every player in the tree
var _played := {}  # instance id: what each player that played in the step was playing, as first seen
var _playing_now := {}  # instance id: true, for the players playing in the step's last frame
var _peaks := PackedFloat64Array()
var _mixes := 0
var _before_mix := 0  # when the last block mixed before the step began
var _last_mix := 0
var _started_us := 0
var _ended_us := 0
var _frames := 0
var _running := false


func _ready() -> void:
	process_mode = PROCESS_MODE_ALWAYS
	process_priority = (1 << 30) - 2  # after every game node, before the frame record and the bridge
	get_tree().node_added.connect(_on_node_added)
	get_tree().node_removed.connect(_on_node_removed)
	for node in get_tree().root.find_children("*", "", true, false):
		_on_node_added(node)


func _on_node_added(node: Node) -> void:
	if node is AudioStreamPlayer or node is AudioStreamPlayer2D or node is AudioStreamPlayer3D:
		_players[node.get_instance_id()] = node


func _on_node_removed(node: Node) -> void:
	_players.erase(node.get_instance_id())


func _process(_delta: float) -> void:
	if get_tree().paused:
		_running = false
		return
	var now := Time.get_ticks_usec()
	var mix := _mix_began()
	if not _running:
		_running = true
		_played.clear()
		_peaks.clear()
		_mixes = 0
		_frames = 0
		_started_us = now
		_before_mix = mix
		_last_mix = mix
	_frames += 1
	_ended_us = now
	_take_levels(mix, now)
	_playing_now.clear()
	for id: int in _players:
		var player: Node = _players[id]
		if player.playing:
			_playing_now[id] = true
			if not _played.has(id):
				_played[id] = _describe(player)


## When the last block began mixing, in Time.get_ticks_usec() time.
func _mix_began() -> int:
	return Time.get_ticks_usec() - int(AudioServer.get_time_since_last_mix() * 1e6)


## Counts the blocks begun since the step began, and once one has finished, takes each bus's level into its peak.
func _take_levels(mix: int, now: int) -> void:
	if absi(mix - _before_mix) < 1000:
		return  # no block since the step began: the levels are from before it
	if absi(mix - _last_mix) >= 1000:
		_mixes += 1
		_last_mix = mix
	if now - mix < MIX_DONE_US:
		return  # still mixing: the levels may be the block's before
	while _peaks.size() < AudioServer.bus_count:
		_peaks.append(SILENT_DB)
	for bus in AudioServer.bus_count:
		for channel in AudioServer.get_bus_channels(bus):
			_peaks[bus] = maxf(_peaks[bus], maxf(AudioServer.get_bus_peak_volume_left_db(bus, channel),
					AudioServer.get_bus_peak_volume_right_db(bus, channel)))


## gdh live audio: each bus with its peak over the last step (null when no block mixed in it), the players that
## played in it, and the players in the tree that didn't.
func command(_args: Dictionary) -> Dictionary:
	var now := Time.get_ticks_usec()
	var mix := _mix_began()
	if _running or mix - _ended_us < 1000:
		_take_levels(mix, now)  # a block begun in the step and finished since
	var buses := []
	for bus in AudioServer.bus_count:
		var peak: Variant = null
		if _mixes > 0 and bus < _peaks.size():
			peak = snappedf(maxf(_peaks[bus], SILENT_DB), 0.1)
		buses.append({"bus": str(AudioServer.get_bus_name(bus)), "peak_db": peak,
				"volume_db": snappedf(AudioServer.get_bus_volume_db(bus), 0.1), "mute": AudioServer.is_bus_mute(bus),
				"send": str(AudioServer.get_bus_send(bus))})
	var players := []
	for id: int in _played:
		var described: Dictionary = _played[id]
		var player: Object = instance_from_id(id)
		if is_instance_valid(player) and _players.has(id):
			described = _describe(player)
		else:
			described.gone = true  # freed, or out of the tree
		described.playing = _playing_now.has(id)
		players.append(described)
	var idle := []
	var idle_count := 0
	for id: int in _players:
		if not _played.has(id):
			idle_count += 1
			if idle.size() < MAX_IDLE:
				idle.append(_describe(_players[id]))
	return {
		"buses": buses,
		"players": players,
		"idle": idle,
		"idle_count": idle_count,
		"mixes": _mixes,
		"frames": _frames,
		"wall_ms": snappedf((_ended_us - _started_us) / 1000.0, 0.1),
		"running": _running,
		"driver": AudioServer.get_driver_name(),
		"mix_rate": AudioServer.get_mix_rate(),
	}


func _describe(player: Node) -> Dictionary:
	var scene := get_tree().current_scene
	var stream: AudioStream = player.stream
	var out := {
		"node": str(scene.get_path_to(player)) if scene and scene.is_ancestor_of(player) else str(player.get_path()),
		"class": player.get_class(),
		"stream": null if stream == null else (stream.resource_path if not stream.resource_path.is_empty() else stream.get_class()),
		"bus": str(player.bus),
		"volume_db": snappedf(player.volume_db, 0.1),
		"position": snappedf(player.get_playback_position(), 0.01),
	}
	if stream:
		out.length = snappedf(stream.get_length(), 0.01)
	var listener: Variant = _listener(player)
	if listener != null:
		out.distance = snappedf(player.global_position.distance_to(listener), 0.01)
		out.max_distance = player.max_distance
	return out


## Where a 2D or 3D player is heard from: its viewport's listener, else its camera, else (2D) the screen's centre.
func _listener(player: Node) -> Variant:
	var viewport := player.get_viewport()
	if player is AudioStreamPlayer3D:
		if viewport.get_audio_listener_3d():
			return viewport.get_audio_listener_3d().global_position
		return viewport.get_camera_3d().global_position if viewport.get_camera_3d() else null
	if player is AudioStreamPlayer2D:
		if viewport.get_audio_listener_2d():
			return viewport.get_audio_listener_2d().global_position
		if viewport.get_camera_2d():
			return viewport.get_camera_2d().get_screen_center_position()
		return viewport.get_canvas_transform().affine_inverse() * (viewport.get_visible_rect().size / 2)
	return null
