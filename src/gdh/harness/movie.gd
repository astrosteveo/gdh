extends SceneTree
## gdh movie's main loop. Godot runs with --write-movie, so Movie Maker writes every frame drawn and the game's audio;
## this loads the scene, lets it run an exact number of frames, and writes what it saw beside the movie.
##
## GDH_ARGS: --frames N, --started-file PATH (written once Godot has read the project's settings, so gdh can remove
## the override.cfg it wrote), --result-file PATH, --scene res://x.tscn (default: the main scene).

const Common := preload("common.gd")
const Covered := preload("covered.gd")
const ErrorCollector := preload("errors.gd")

# About this many samples of what covers the screen over a run.
const COVER_SAMPLES := 48

var _errors := ErrorCollector.new()
var _args := {}
var _frames := 0
var _target := 1
var _every := 1
var _samples := []
var _window := []
var _done := false


func _initialize() -> void:
	OS.add_logger(_errors)
	_args = Common.harness_args()
	_target = maxi(int(_args.get("frames", "1")), 1)
	_every = maxi(_target / COVER_SAMPLES, 1)
	_write(_args.get("started-file", ""), {"settings": {
		"window_width_override": ProjectSettings.get_setting("display/window/size/window_width_override", 0),
		"window_height_override": ProjectSettings.get_setting("display/window/size/window_height_override", 0),
		"video_quality": ProjectSettings.get_setting("editor/movie_writer/video_quality", -1.0),
	}})
	var scene: String = _args.get("scene", "")
	if scene.is_empty():
		scene = ProjectSettings.get_setting("application/run/main_scene", "")
	var packed := load(scene) as PackedScene if not scene.is_empty() else null
	if packed == null:
		_finish("Can't load scene \"%s\"." % scene, 3)
		return
	# Added now, not at the next frame as change_scene_to_* would, so the movie's first frame is the scene's.
	var node := packed.instantiate()
	root.add_child(node)
	current_scene = node
	process_frame.connect(_on_frame)


func _on_frame() -> void:
	_frames += 1
	if _frames == 2:
		var size := DisplayServer.window_get_size()
		_window = [size.x, size.y]
	if _frames % _every == 0:
		_samples.append(Covered.sample(self))
	if _frames >= _target:
		process_frame.disconnect(_on_frame)
		_finish("", 0)


## The game quit by itself before the frames asked for: say so, and how far it got.
func _finalize() -> void:
	if not _done:
		_finish("", 0, true)


func _finish(error: String, code: int, quit_early := false) -> void:
	_done = true
	_write(_args.get("result-file", ""), {
		"quit_early": quit_early,
		"error": error,
		"frames": _frames,
		"window_size": _window,
		"image_size": Common.image_size(self) if error.is_empty() and not quit_early else [],
		"scene": current_scene.scene_file_path if current_scene else "",
		"adapter": RenderingServer.get_video_adapter_name(),
		"cover_samples": _samples,
		"errors": _errors.all(),
	})
	if not quit_early:
		quit(code)


func _write(path: String, data: Dictionary) -> void:
	if path.is_empty():
		return
	var tmp := path + ".tmp"
	var f := FileAccess.open(tmp, FileAccess.WRITE)
	f.store_string(JSON.stringify(data))
	f.close()
	DirAccess.rename_absolute(tmp, path)
