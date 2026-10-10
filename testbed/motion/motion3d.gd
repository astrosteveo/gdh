extends Node3D
## Cube moves along x by 0.05 a physics frame, in front of the camera; Ghost moves from in front of the camera to
## behind it, 0.25 along z a frame, so part of its trail has no place on screen.

@onready var cube: Node3D = $Cube
@onready var ghost: Node3D = $Ghost


func _physics_process(_delta: float) -> void:
	cube.position.x += 0.05
	ghost.position.z += 0.25
