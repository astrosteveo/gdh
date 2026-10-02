extends Node
## A launcher that hands off to another process and exits, as a game's launcher does: on ready it starts
## child.gd in a second Godot (same display, same environment), and quits after --quit-after frames (default 3) of
## game time. --child-seconds N is how long the child runs (default 20), --child-file PATH where it writes.

var _frames := 0
var _quit_after := 3


func _ready() -> void:
	var args := _user_args()
	_quit_after = int(args.get("quit-after", "3"))
	var child_args := ["--path", ProjectSettings.globalize_path("res://"), "--script", "res://children/child.gd", "--",
			"--seconds", args.get("child-seconds", "20"), "--file", args.get("child-file", "")]
	var pid := OS.create_process(OS.get_executable_path(), child_args)
	print("launcher: started child ", pid)


func _process(_delta: float) -> void:
	_frames += 1
	if _frames >= _quit_after:
		get_tree().quit()


func _user_args() -> Dictionary:
	var out := {}
	var args := OS.get_cmdline_user_args()
	for i in range(0, args.size() - 1, 2):
		out[args[i].trim_prefix("--")] = args[i + 1]
	return out
