extends Node2D
## Things that move by known amounts each physics frame, for the motion tools (trace charts, trails, onion skins,
## filmstrips and change maps): Ball moves right 4 px a frame while `moving`; Spinner turns 6 degrees a frame in place;
## Blinker changes color every 10 frames; the rest stands still. With `follow`, the camera keeps Ball at the screen's
## centre, so Ball stands still on screen while the world moves past it.

var moving := true
var follow := false
var ticks := 0

@onready var ball: Node2D = $Ball
@onready var spinner: Node2D = $Spinner
@onready var blinker: ColorRect = $Blinker
@onready var camera: Camera2D = $Camera


func _physics_process(_delta: float) -> void:
	ticks += 1
	if moving:
		ball.position.x += 4.0
	spinner.rotation_degrees += 6.0
	blinker.color = Color(0.95, 0.85, 0.2) if (ticks / 10) % 2 == 0 else Color(0.2, 0.4, 0.95)
	if follow:
		camera.position = ball.position
