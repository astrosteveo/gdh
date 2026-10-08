extends Node2D
## Records how the wheel, modifiers, a gamepad, touches and mouse-look reach the game. Ctrl and the wheel zoom the
## Map, as a map view does, and "save" is Ctrl+S, added to the Input Map here. The gamepad goes through Godot's
## built-in actions: ui_select is its Y button, ui_left its D-pad's left, ui_right its left stick to the right.

var wheel := []  # [button, ctrl on the event, Ctrl key down] for each wheel notch _input saw
var zoom := 1.0
var keys := []  # [key, pressed, ctrl, shift] for each key _input saw
var saves := 0
var touches := []  # [index, pressed, x, y]
var drags := []  # [index, x, y, relative x, relative y]
var motions := 0  # mouse motions _input saw
var look := Vector2.ZERO  # relative motion summed while the mouse is captured
var look_at := Vector2.ZERO  # where the last of it was
var select_frames := 0  # physics frames with ui_select down
var select_just := 0  # physics frames in which ui_select was just pressed
var right_strength := 0.0  # ui_right's strength in the last physics frame
var taps := 0  # presses of the Tap button


func _ready() -> void:
	var save := InputEventKey.new()
	save.keycode = KEY_S
	save.ctrl_pressed = true
	InputMap.add_action("save")
	InputMap.action_add_event("save", save)
	$Map.gui_input.connect(_on_map_input)
	$Tap.pressed.connect(func(): taps += 1)


func _physics_process(_delta: float) -> void:
	if Input.is_action_pressed("ui_select"):
		select_frames += 1
	if Input.is_action_just_pressed("ui_select"):
		select_just += 1
	if Input.is_action_just_pressed("save"):
		saves += 1
	right_strength = Input.get_action_strength("ui_right")


func _input(event: InputEvent) -> void:
	var button := event as InputEventMouseButton
	if button and button.pressed and button.button_index >= MOUSE_BUTTON_WHEEL_UP \
			and button.button_index <= MOUSE_BUTTON_WHEEL_RIGHT:
		wheel.append([button.button_index, button.ctrl_pressed, Input.is_key_pressed(KEY_CTRL)])
	elif event is InputEventKey:
		keys.append([OS.get_keycode_string(event.keycode), event.pressed, event.ctrl_pressed, event.shift_pressed])
	elif event is InputEventScreenTouch:
		touches.append([event.index, event.pressed, event.position.x, event.position.y])
	elif event is InputEventScreenDrag:
		drags.append([event.index, event.position.x, event.position.y, event.relative.x, event.relative.y])
	elif event is InputEventMouseMotion:
		motions += 1
		if Input.mouse_mode == Input.MOUSE_MODE_CAPTURED:
			look += event.relative
			look_at = event.position


## Ctrl and the wheel zoom the map a notch at a time; the wheel alone does nothing here.
func _on_map_input(event: InputEvent) -> void:
	var button := event as InputEventMouseButton
	if button and button.pressed and button.ctrl_pressed \
			and button.button_index in [MOUSE_BUTTON_WHEEL_UP, MOUSE_BUTTON_WHEEL_DOWN]:
		zoom *= 1.25 if button.button_index == MOUSE_BUTTON_WHEEL_UP else 0.8
		$Map.accept_event()
