extends Control
## A window for --binary sessions, run as an export: the background turns from dark blue to green when the button
## is clicked, and it prints what reaches it (clicks, keys, typed text, the wheel) so a session can wait for it in the
## log. User arguments: --exit-after SECONDS [--code N] quits with code N; --kill-after SECONDS ends the process
## with SIGKILL, as a crash would end it; --error raises an engine error as it starts; --open URL opens it with
## OS.shell_open.

const GREEN := Color(0.1, 0.8, 0.2)

var clicks := 0
var _args := {}


func _ready() -> void:
	_args = _user_args()
	$Button.pressed.connect(_on_pressed)
	$Field.text_submitted.connect(_on_submitted)
	if _args.has("error"):
		push_error("clicker: an error as it starts")
	if _args.has("exit-after"):
		get_tree().create_timer(float(_args["exit-after"])).timeout.connect(_quit)
	if _args.has("kill-after"):
		get_tree().create_timer(float(_args["kill-after"])).timeout.connect(_kill)
	if _args.has("open"):
		print("clicker: opening ", _args["open"], ": ", error_string(OS.shell_open(_args["open"])))
	print("clicker: ready, export %s" % (not OS.has_feature("editor")))


func _input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		print("clicker: key ", event.as_text_keycode())
	elif event is InputEventMouseButton and event.pressed:
		match event.button_index:
			MOUSE_BUTTON_WHEEL_UP:
				print("clicker: wheel up")
			MOUSE_BUTTON_WHEEL_DOWN:
				print("clicker: wheel down")
			MOUSE_BUTTON_RIGHT:
				print("clicker: right click at %d,%d" % [event.position.x, event.position.y])


func _on_pressed() -> void:
	clicks += 1
	$Background.color = GREEN
	print("clicker: clicked ", clicks)


func _on_submitted(text: String) -> void:
	print("clicker: typed ", text)


func _quit() -> void:
	print("clicker: quitting with code ", _args.get("code", "0"))
	get_tree().quit(int(_args.get("code", "0")))


func _kill() -> void:
	print("clicker: killing itself")
	OS.kill(OS.get_process_id())


func _user_args() -> Dictionary:
	var out := {}
	var args := OS.get_cmdline_user_args()
	for i in args.size():
		if args[i].begins_with("--"):
			var has_value := i + 1 < args.size() and not args[i + 1].begins_with("--")
			out[args[i].trim_prefix("--")] = args[i + 1] if has_value else ""
	return out
