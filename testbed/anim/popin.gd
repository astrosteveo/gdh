extends Node3D
## Pop-in: Camera glides toward -z at SPEED a frame, past a row of posts on each side; Far, a cube ahead in the middle
## of the view, has a visibility range that ends at POP metres, so it appears all at once when the camera gets that
## close: on frame POP_FRAME.

const SPEED := 0.1
const POP := 12.0
const START := 0.0
const FAR_Z := -20.0
const POP_FRAME := 81  # the first frame the camera is under POP from Far: START - SPEED * frame < FAR_Z + POP

@onready var camera: Camera3D = $Camera


func _physics_process(_delta: float) -> void:
	camera.position.z -= SPEED
