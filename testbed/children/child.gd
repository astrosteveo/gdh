extends SceneTree
## The process launcher.gd hands off to: a window on the same display that runs for --seconds, writing
## "<pid> <frames>" to --file each frame.

var _file := ""
var _until := 0
var _frames := 0


func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	var seconds := 20.0
	for i in range(0, args.size() - 1, 2):
		if args[i] == "--seconds":
			seconds = float(args[i + 1])
		elif args[i] == "--file":
			_file = args[i + 1]
	_until = Time.get_ticks_msec() + int(seconds * 1000)
	print("child: running on display ", DisplayServer.get_name())


func _process(_delta: float) -> bool:
	_frames += 1
	if _file:
		var f := FileAccess.open(_file, FileAccess.WRITE)
		if f:
			f.store_string("%d %d\n" % [OS.get_process_id(), _frames])
	return Time.get_ticks_msec() > _until
