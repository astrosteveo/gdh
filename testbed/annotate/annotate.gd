extends Node2D
## A scene to annotate: Player (a CharacterBody2D moving right at 120 px/s, drawn by its Body) with a box collision
## shape, a Coin (an Area2D with a circle, in the group pickups), a Wall whose capsule shape is disabled, a navigation
## region of one square, five Pebbles packed close together, a Marker sprite at 900,200 scaled 4x whose 16 px texture
## is opaque on its left half only, a backdrop over the whole screen, and UI on a CanvasLayer.

@onready var player: CharacterBody2D = $Player


func _ready() -> void:
	player.velocity = Vector2(120, 0)
