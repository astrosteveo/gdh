extends Node2D
## For tests/test_restart.py: a game whose state hangs on its input and its random numbers. It draws three numbers
## from the global random number generator as it starts (rolls), moves the player right at 120 px/s while ui_right is
## held and a random step up or down every frame, counts its frames (ticks) and the Go button's clicks, and reads and
## writes files in user://. distance adds step_size() every frame, so a reload that changes it shows.

const SPEED := 120.0

var rolls: Array[int] = []
var ticks := 0
var clicks := 0
var distance := 0.0

@onready var player: Node2D = $Player


func _ready() -> void:
	rolls = [randi(), randi(), randi()]
	$UI/GoButton.pressed.connect(func() -> void: clicks += 1)


func _physics_process(delta: float) -> void:
	ticks += 1
	distance += step_size()
	if Input.is_action_pressed("ui_right"):
		player.position.x += SPEED * delta
	player.position.y += randf_range(-2.0, 2.0)


func step_size() -> float:
	return 1.0


## The text of user://name, or "" when there's no such file.
func read_user(file: String) -> String:
	return FileAccess.get_file_as_string("user://" + file)


func write_user(file: String, text: String) -> bool:
	var f := FileAccess.open("user://" + file, FileAccess.WRITE)
	if f == null:
		return false
	f.store_string(text)
	return true
