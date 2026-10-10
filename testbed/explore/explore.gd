extends Node2D
## The level: a floor from x 0 to 600, a pit with no bottom from 600 to 700, a floor from 700 to 1400 with a Wall at
## 1100 too high to jump, and a Trap between x 260 and 300 (left of the start) that soft-locks the Explorer.

@onready var explorer: CharacterBody2D = $Explorer


func _ready() -> void:
	$Trap.body_entered.connect(func(body: Node) -> void:
		if body == explorer:
			explorer.trap())
