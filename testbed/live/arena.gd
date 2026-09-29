extends Node2D
## Moves the player right while ui_right is held (120 px/s) and counts how
## ui_accept and the Go button reach the game.

const SPEED := 120.0

var just_physics := 0
var just_process := 0
var input_events := 0
var clicks := 0

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


func _process(_delta: float) -> void:
	if Input.is_action_just_pressed("ui_accept"):
		just_process += 1
	stats.text = "x %.1f  jumps %d  clicks %d" % [player.position.x, GameState.jumps, clicks]


func _input(event: InputEvent) -> void:
	if event.is_action_pressed("ui_accept"):
		input_events += 1
