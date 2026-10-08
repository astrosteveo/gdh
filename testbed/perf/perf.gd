extends Node3D
## A scene for gdh live bench, monitors and audio (tests/test_perf.py), picked with -- --mode NAME:
##   steady   a few boxes in view, and a node made and one freed every frame: the counts churn but stay flat
##   leak     a node added to the tree every frame and never freed
##   orphans  a node made every frame and never added to the tree or freed
##   sound    res://perf/beep.wav played over and over by an AudioStreamPlayer on the Master bus, an
##            AudioStreamPlayer2D on an "Effects" bus the scene adds, and an AudioStreamPlayer3D in front of the
##            camera; and a fourth player, Silent, that never plays
## Every mode has the camera and the boxes, so every frame draws something.

const BEEP := preload("res://perf/beep.wav")
const CHURN := 30  # steady: the nodes kept, each freed this many frames after it was made

var mode := "steady"
var _kept: Array[Node] = []


func _ready() -> void:
	var args := OS.get_cmdline_user_args()
	var at := args.find("--mode")
	if at >= 0 and at + 1 < args.size():
		mode = args[at + 1]
	var camera := Camera3D.new()
	camera.position = Vector3(0, 0, 4)
	add_child(camera)
	var light := DirectionalLight3D.new()
	light.rotation_degrees = Vector3(-45, 30, 0)
	add_child(light)
	for i in 3:
		var box := MeshInstance3D.new()
		box.mesh = BoxMesh.new()
		box.position = Vector3(i * 1.5 - 1.5, 0, 0)
		add_child(box)
	if mode == "sound":
		_add_players()


func _process(_delta: float) -> void:
	match mode:
		"steady":
			var node := Node.new()
			add_child(node)
			_kept.append(node)
			if _kept.size() > CHURN:
				_kept.pop_front().queue_free()
		"leak":
			add_child(Node.new())
		"orphans":
			_kept.append(Node.new())


func _add_players() -> void:
	var effects := AudioServer.bus_count
	AudioServer.add_bus(effects)
	AudioServer.set_bus_name(effects, "Effects")
	AudioServer.set_bus_send(effects, "Master")
	var plain := AudioStreamPlayer.new()
	plain.name = "Beep"
	var flat := AudioStreamPlayer2D.new()
	flat.name = "Beep2D"
	flat.bus = "Effects"
	flat.position = Vector2(640, 360)
	var spatial := AudioStreamPlayer3D.new()
	spatial.name = "Beep3D"
	spatial.position = Vector3(0, 0, 2)
	for player: Node in [plain, flat, spatial]:
		player.stream = BEEP
		player.autoplay = true
		player.finished.connect(player.play)  # over and over
		add_child(player)
	var silent := AudioStreamPlayer.new()
	silent.name = "Silent"
	silent.stream = BEEP
	add_child(silent)
