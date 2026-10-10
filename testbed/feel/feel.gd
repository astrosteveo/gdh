extends Node2D
## The feel testbed's level: Hero on a floor from x 0 to 800 with a ledge at its right end. hit() freezes time for
## HITSTOP_FRAMES frames (Engine.time_scale 0) and shakes the camera for SHAKE_FRAMES frames: an offset of
## SHAKE_AMPLITUDE px along x at SHAKE_HZ, decaying linearly to nothing. The camera follows Hero with position
## smoothing (speed 5). Lamp is on a CanvasLayer, so it stays put on screen.

const HITSTOP_FRAMES := 6
const SHAKE_FRAMES := 30
const SHAKE_AMPLITUDE := 8.0
const SHAKE_HZ := 10.0

var frozen := 0
var shaking := 0

@onready var camera: Camera2D = $Hero/Camera


func hit() -> bool:
	frozen = HITSTOP_FRAMES
	shaking = SHAKE_FRAMES
	Engine.time_scale = 0.0
	return true


## Counted in rendered frames, which go on while time stands still.
func _process(_delta: float) -> void:
	if frozen > 0:
		frozen -= 1
		if frozen == 0:
			Engine.time_scale = 1.0
	if shaking > 0:
		var t := float(SHAKE_FRAMES - shaking) / 60.0
		camera.offset = Vector2(SHAKE_AMPLITUDE * (float(shaking) / SHAKE_FRAMES) * sin(TAU * SHAKE_HZ * t), 0)
		shaking -= 1
		if shaking == 0:
			camera.offset = Vector2.ZERO
