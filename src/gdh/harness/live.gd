extends SceneTree
## Starts a game held at frame 0 with a control channel (bridge.gd) attached.
## Run through `gdh live start`. The project's autoloads load as usual.
##
## Options after "--": --scene res://x.tscn (default: the project's main scene),
## --ready-file <path>, --out <dir>, --idle-timeout <seconds>, --ticks <physics
## ticks per second>, --display <gpu or xvfb>, --resolution <WxH asked for>, --seed <the global RNG's seed>. The request token comes from the GDH_TOKEN environment
## variable, which other users can't read.

const Bridge := preload("bridge.gd")
const Common := preload("common.gd")
const ErrorCollector := preload("errors.gd")
const Frames := preload("frames.gd")
const Audio := preload("audio.gd")


func _initialize() -> void:
	var errors := ErrorCollector.new()
	OS.add_logger(errors)
	var args := Common.harness_args()
	# --seed: the global random number generator's seed (randi, randf...), set before the autoloads' _ready and the
	# scene's, so a game's random choices repeat from one start to the next.
	if args.has("seed"):
		seed(int(args.seed))
	Common.apply_locale(args)
	# Wireframe data is only built for meshes created after this call.
	RenderingServer.set_debug_generate_wireframes(true)
	# Hold before the scene's first frame: _ready runs, _process doesn't.
	paused = true
	var scene: String = args.get("scene", "")
	if scene.is_empty():
		scene = ProjectSettings.get_setting("application/run/main_scene", "")
	var bridge := Bridge.new()
	bridge.name = "GdhBridge"
	bridge.errors = errors
	bridge.token = OS.get_environment("GDH_TOKEN")
	bridge.out_dir = args.get("out", OS.get_user_data_dir().path_join("gdh"))
	bridge.ready_file = args.get("ready-file", bridge.out_dir.path_join("ready.json"))
	bridge.idle_timeout_s = float(args.get("idle-timeout", "1800"))
	bridge.ticks_per_second = int(args.get("ticks", "60"))
	bridge.display = args.get("display", "")
	var asked: PackedStringArray = args.get("resolution", "").split("x")
	if asked.size() == 2:
		bridge.resolution = Vector2i(int(asked[0]), int(asked[1]))
	root.add_child(bridge, false, Node.INTERNAL_MODE_BACK)
	var frames := Frames.new()
	frames.name = "GdhFrames"
	bridge.recorder = frames
	root.add_child(frames, false, Node.INTERNAL_MODE_BACK)
	var audio := Audio.new()
	audio.name = "GdhAudio"
	bridge.listener = audio
	root.add_child(audio, false, Node.INTERNAL_MODE_BACK)
	if scene.is_empty() or change_scene_to_file(scene) != OK:
		push_error("Can't load scene \"%s\"." % scene)
		# The bridge waits for a current scene, so report the failure directly.
		bridge.write_ready({"port": 0, "error": "Can't load scene \"%s\"." % scene, "errors": errors.drain()})
		quit(3)
