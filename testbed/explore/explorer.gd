extends CharacterBody2D
## A platformer to explore: ui_left and ui_right run at SPEED, ui_accept jumps. It reports to GdhEvents: "checkpoint"
## the first time it passes x 500, "trapped" when it walks into the Trap (from then on it ignores all input: a soft
## lock), "fell" once when it drops below FALL_Y (the pit between x 600 and 700 has no bottom).

const SPEED := 200.0
const GRAVITY := 1800.0
const JUMP := -560.0
const FALL_Y := 1200.0

var trapped := false
var checkpoint := false
var fell := false


func _physics_process(delta: float) -> void:
	var direction := 0.0 if trapped else Input.get_axis("ui_left", "ui_right")
	velocity.x = direction * SPEED
	if not trapped and is_on_floor() and Input.is_action_just_pressed("ui_accept"):
		velocity.y = JUMP
	velocity.y += GRAVITY * delta
	move_and_slide()
	if not checkpoint and global_position.x > 500:
		checkpoint = true
		GdhEvents.emit("checkpoint", {"x": snappedf(global_position.x, 0.1)})
	if not fell and global_position.y > FALL_Y:
		fell = true
		GdhEvents.emit("fell", {"x": snappedf(global_position.x, 0.1)})


func trap() -> void:
	if not trapped:
		trapped = true
		GdhEvents.emit("trapped", {"x": snappedf(global_position.x, 0.1)})
