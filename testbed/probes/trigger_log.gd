extends Area3D

func _ready() -> void:
	body_entered.connect(func(body: Node3D) -> void: print("TRIGGER_ENTERED: ", body.name))
