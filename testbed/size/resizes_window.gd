extends Node3D
## A game that sizes its window itself, as one applying a saved setting does: gdh must say its window isn't the size
## asked for.


func _ready() -> void:
	get_window().size = Vector2i(640, 360)
