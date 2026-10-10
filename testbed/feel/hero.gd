extends CharacterBody2D
## A platformer character whose feel is known exactly, at 60 physics frames a second: it accelerates at ACCEL to
## MAX_SPEED on the ground (AIR_ACCEL in the air), slows at FRICTION, jumps at JUMP_VELOCITY under GRAVITY, can still
## jump COYOTE_FRAMES frames after walking off a ledge, and a jump pressed up to BUFFER_FRAMES frames before landing
## jumps on landing. Each press of ui_accept sets `pressed_at` to that frame at once, and Lamp lights LAMP_DELAY frames
## later, for LAMP_FRAMES frames: its state and its pixels answer at different frames.

const MAX_SPEED := 240.0
const ACCEL := 1200.0
const AIR_ACCEL := 600.0
const FRICTION := 1800.0
const GRAVITY := 1800.0
const JUMP_VELOCITY := -600.0
const COYOTE_FRAMES := 6
const BUFFER_FRAMES := 5
const LAMP_DELAY := 3
const LAMP_FRAMES := 10

var frames_off_floor := 0
var buffered := 0
var pressed_at := -1
var ticks := 0

@onready var lamp: ColorRect = $"../UI/Lamp"


func _physics_process(delta: float) -> void:
	ticks += 1
	if delta == 0.0:
		return  # hit-stop: time stands still
	var direction := Input.get_axis("ui_left", "ui_right")
	var accel := ACCEL if is_on_floor() else AIR_ACCEL
	if direction != 0.0:
		velocity.x = move_toward(velocity.x, direction * MAX_SPEED, accel * delta)
	elif is_on_floor():
		velocity.x = move_toward(velocity.x, 0.0, FRICTION * delta)
	frames_off_floor = 0 if is_on_floor() else frames_off_floor + 1
	if Input.is_action_just_pressed("ui_accept"):
		buffered = BUFFER_FRAMES + 1
		pressed_at = ticks
	if buffered > 0 and frames_off_floor <= COYOTE_FRAMES and velocity.y >= 0.0:
		velocity.y = JUMP_VELOCITY
		buffered = 0
		frames_off_floor = COYOTE_FRAMES + 1
	buffered = maxi(buffered - 1, 0)
	if not is_on_floor():
		velocity.y += GRAVITY * delta
	move_and_slide()
	var lit := pressed_at >= 0 and ticks - pressed_at >= LAMP_DELAY and ticks - pressed_at < LAMP_DELAY + LAMP_FRAMES
	lamp.color = Color(1, 0.9, 0.3) if lit else Color(0.2, 0.2, 0.25)
