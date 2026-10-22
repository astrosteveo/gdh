extends Node2D
## A game with a pause menu: ui_cancel pauses it and shows the menu, whose Resume button (the menu runs while the
## game is paused) lets it go on. The mover counts the frames the game ran in, a pixel each.

@onready var mover: Node2D = $Mover
@onready var menu: CanvasLayer = $Menu


func _ready() -> void:
	$Menu/Resume.pressed.connect(resume)


func _physics_process(_delta: float) -> void:
	mover.position.x += 1.0


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("ui_cancel"):
		get_tree().paused = true
		menu.visible = true


func resume() -> void:
	menu.visible = false
	get_tree().paused = false
