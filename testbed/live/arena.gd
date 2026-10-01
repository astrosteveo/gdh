extends Node2D
## Moves the player right while ui_right is held (120 px/s) and counts how
## ui_accept, the Go button and a held right button reach the game.

const SPEED := 120.0

var just_physics := 0
var just_process := 0
var input_events := 0
var clicks := 0
var right_held_frames := 0
var right_releases := 0
var right_drag := Vector2.ZERO

@onready var player: Node2D = $Player
@onready var stats: Label = $UI/Stats


func _ready() -> void:
	$UI/GoButton.pressed.connect(func(): clicks += 1)


func _physics_process(delta: float) -> void:
	if Input.is_action_pressed("ui_right"):
		player.position.x += SPEED * delta
	if Input.is_action_just_pressed("ui_accept"):
		just_physics += 1
		GameState.jumps += 1
	if Input.is_mouse_button_pressed(MOUSE_BUTTON_RIGHT):
		right_held_frames += 1


func _process(_delta: float) -> void:
	if Input.is_action_just_pressed("ui_accept"):
		just_process += 1
	stats.text = "x %.1f  jumps %d  clicks %d" % [player.position.x, GameState.jumps, clicks]


func _input(event: InputEvent) -> void:
	if event.is_action_pressed("ui_accept"):
		input_events += 1
	if event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_RIGHT and not event.pressed:
		right_releases += 1
	if event is InputEventMouseMotion and event.button_mask & MOUSE_BUTTON_MASK_RIGHT:
		right_drag += event.relative
