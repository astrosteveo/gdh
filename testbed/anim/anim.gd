extends Node2D
## Animations with known behaviour, for gdh live anim: Player plays "bob" (1 s, looping) on Body's y; Tree is an
## AnimationTree whose state machine goes from "idle" to "move" while `moving`, "move" being a 1D blend space of
## "walk" and "run" by `speed` (0 to 1). Two bugs can be switched on: `restart_bug` restarts Player's "bob" every
## frame, and `flip_bug` sends Tree's state machine between idle and move every 2 frames.

var moving := false
var speed := 0.0
var restart_bug := false
var flip_bug := false
var ticks := 0

@onready var player: AnimationPlayer = $Player
@onready var tree: AnimationTree = $Tree


func _ready() -> void:
	var library := AnimationLibrary.new()
	for spec in [["bob", 1.0, 20.0], ["walk", 0.8, 10.0], ["run", 0.4, 30.0], ["idle", 1.0, 2.0]]:
		var animation := Animation.new()
		animation.length = spec[1]
		animation.loop_mode = Animation.LOOP_LINEAR
		var track := animation.add_track(Animation.TYPE_VALUE)
		animation.track_set_path(track, "Body:position:y")
		animation.track_insert_key(track, 0.0, 0.0)
		animation.track_insert_key(track, spec[1] / 2, -spec[2])
		animation.track_insert_key(track, spec[1], 0.0)
		library.add_animation(spec[0], animation)
	player.add_animation_library("", library)
	player.play("bob")

	var machine := AnimationNodeStateMachine.new()
	var idle := AnimationNodeAnimation.new()
	idle.animation = "idle"
	var blend := AnimationNodeBlendSpace1D.new()
	var walk := AnimationNodeAnimation.new()
	walk.animation = "walk"
	var run := AnimationNodeAnimation.new()
	run.animation = "run"
	blend.add_blend_point(walk, 0.0)
	blend.add_blend_point(run, 1.0)
	machine.add_node("idle", idle)
	machine.add_node("move", blend)
	machine.add_transition("Start", "idle", AnimationNodeStateMachineTransition.new())
	for pair in [["idle", "move"], ["move", "idle"]]:
		var transition := AnimationNodeStateMachineTransition.new()
		transition.switch_mode = AnimationNodeStateMachineTransition.SWITCH_MODE_IMMEDIATE
		machine.add_transition(pair[0], pair[1], transition)
	var tree_player := AnimationPlayer.new()
	tree_player.name = "TreePlayer"
	add_child(tree_player)
	tree_player.add_animation_library("", library.duplicate())
	tree.anim_player = tree.get_path_to(tree_player)
	tree.tree_root = machine
	tree.active = true


func _physics_process(_delta: float) -> void:
	ticks += 1
	if restart_bug:
		player.stop()
		player.play("bob")
	var playback: AnimationNodeStateMachinePlayback = tree.get("parameters/playback")
	var want := "move" if moving else "idle"
	if flip_bug:
		want = "move" if (ticks / 2) % 2 == 0 else "idle"
	if playback.get_current_node() != want:
		playback.travel(want)
	tree.set("parameters/move/blend_position", speed)
