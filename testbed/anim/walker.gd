extends Node3D
## A walker with a scripted walk cycle, for gdh live footslide: Walker moves along x at SPEED; its Skeleton3D's feet
## ("foot.L" and "foot.R") take turns, each planted for half of a CYCLE-frame stride and swinging (lifted LIFT) for the
## other half. Planted, a foot stays where it landed in the world; with `slide`, a planted foot moves along with the
## body instead, sliding at SPEED. A small box on each foot (a BoneAttachment3D) shows it.

const SPEED := 1.2
const CYCLE := 40
const STRIDE := SPEED * CYCLE / 60.0
const LIFT := 0.15

var slide := false
var ticks := 0
var planted_at := [0.0, 0.0]
var last_x := [0.0, 0.0]

@onready var walker: Node3D = $Walker
@onready var skeleton: Skeleton3D = $Walker/Skeleton


func _ready() -> void:
	var hips := skeleton.add_bone("Hips")
	skeleton.set_bone_rest(hips, Transform3D(Basis(), Vector3(0, 0.9, 0)))
	for side in ["L", "R"]:
		var foot := skeleton.add_bone("foot." + side)
		skeleton.set_bone_parent(foot, hips)
		skeleton.set_bone_rest(foot, Transform3D(Basis(), Vector3(0, -0.9, -0.15 if side == "L" else 0.15)))
	skeleton.reset_bone_poses()
	planted_at = [0.0, 0.0]
	last_x = [0.0, 0.0]
	for side in ["L", "R"]:
		var attach := BoneAttachment3D.new()
		attach.bone_name = "foot." + side
		var box := MeshInstance3D.new()
		var mesh := BoxMesh.new()
		mesh.size = Vector3(0.12, 0.06, 0.25)
		box.mesh = mesh
		attach.add_child(box)
		skeleton.add_child(attach)
	_pose()


func _physics_process(_delta: float) -> void:
	ticks += 1
	walker.position.x += SPEED / 60.0
	_pose()


## Each foot's pose: planted (on the ground, where it landed) for half the cycle, then swinging ahead.
func _pose() -> void:
	for i in 2:
		var bone := skeleton.find_bone("foot.L" if i == 0 else "foot.R")
		var phase := float((ticks + i * CYCLE / 2) % CYCLE) / CYCLE
		var rest := skeleton.get_bone_rest(bone).origin
		var world_x: float
		var lift := 0.0
		if phase < 0.5:  # planted
			if phase == 0.0:
				planted_at[i] = last_x[i]  # where the swing put it down
			world_x = walker.position.x if slide else planted_at[i]
			if slide:
				planted_at[i] = world_x
		else:  # swinging to land half a stride ahead
			var t := (phase - 0.5) / 0.5
			world_x = lerpf(planted_at[i], walker.position.x + STRIDE / 2, t)
			lift = sin(t * PI) * LIFT
		last_x[i] = world_x
		skeleton.set_bone_pose_position(bone, rest + Vector3(world_x - walker.position.x, lift, 0))
