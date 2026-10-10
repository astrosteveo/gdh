extends Node3D
## Clipping: Reacher's Skeleton3D has a "hand" bone swinging along x between 2.4 and 3.4 every SWING frames; Wall is a
## static box from x 3.0 to 3.5, so the hand is inside it while past 3.0. Reacher's own collision capsule is left out
## of the check (it's the character's).

const SWING := 60

var ticks := 0

@onready var skeleton: Skeleton3D = $Reacher/Skeleton


func _ready() -> void:
	var root := skeleton.add_bone("root")
	skeleton.set_bone_rest(root, Transform3D(Basis(), Vector3(0, 1, 0)))
	var hand := skeleton.add_bone("hand")
	skeleton.set_bone_parent(hand, root)
	skeleton.set_bone_rest(hand, Transform3D(Basis(), Vector3(0.4, 0, 0)))
	skeleton.reset_bone_poses()
	_pose()


func _physics_process(_delta: float) -> void:
	ticks += 1
	_pose()


func _pose() -> void:
	# Reacher stands at x 2; the hand's x goes from 2.4 to 3.4 and back.
	var reach := 0.5 - 0.5 * cos(TAU * float(ticks) / SWING)
	skeleton.set_bone_pose_position(skeleton.find_bone("hand"), Vector3(0.4 + reach, 0, 0))
