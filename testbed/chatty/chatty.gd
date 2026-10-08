extends Node2D
## A game that talks: it prints "tick N" every 10 physics frames, prints what
## it's asked to, and raises errors from inside its own functions, so a reply
## carries the game's output and a script's backtrace. It counts the frames
## ui_right is held.

var ticks := 0
var right_frames := 0


func _ready() -> void:
	print("chatty ready")


func _physics_process(_delta: float) -> void:
	ticks += 1
	if Input.is_action_pressed("ui_right"):
		right_frames += 1
	if ticks % 10 == 0:
		print("tick %d" % ticks)


## Prints `count` lines, line 0 to line count - 1, and returns count.
func say(count: int) -> int:
	for i in count:
		print("line %d" % i)
	return count


## Prints the same line `count` times.
func repeat(count: int) -> int:
	for i in count:
		print("again")
	return count


## push_error from two calls down.
func fail() -> bool:
	return _outer()


func _outer() -> bool:
	return _inner()


func _inner() -> bool:
	push_error("chatty failed on purpose")
	return false
