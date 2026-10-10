extends Node2D
## Script time with a known shape, for gdh live profile: light() runs every physics frame and costs next to nothing;
## every SPIKE_EVERY frames heavy() runs, which spends its time in crunch(), a busy loop of CRUNCH steps, so heavy()'s
## total time is large and its self time small. It also prints, and can raise a script error (fail()), so a test can
## check the game's output and errors still come through with gdh's debugger attached.

const SPIKE_EVERY := 30
const CRUNCH := 400000

var ticks := 0
var total := 0


func _physics_process(_delta: float) -> void:
	ticks += 1
	light()
	if ticks % SPIKE_EVERY == 0:
		heavy()


func light() -> void:
	total += 1


func heavy() -> void:
	total += crunch()


func crunch() -> int:
	var sum := 0
	for i in CRUNCH:
		sum += i % 7
	return sum


func fail() -> bool:
	print("about to fail")
	var nothing: Node = null
	return nothing.is_inside_tree()
