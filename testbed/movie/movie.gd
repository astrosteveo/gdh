extends Node3D
## gdh movie's test scene: a spinning, lit cube under a moving light, a 440 Hz tone, and, picked with arguments after
## "--": --hud (corner labels, a health bar and a crosshair under a full-screen root Control, as a game's HUD is),
## --dialog (a panel over the middle of the screen, as a modal is), --dialog-at N (the same, from frame N on),
## --popup (an embedded AcceptDialog over the middle), --quit-at N (the game quits by itself at frame N).

var cube: MeshInstance3D
var light: OmniLight3D
var frame := 0
var dialog_at := -1
var dialog: Control
var quit_at := -1


func _ready() -> void:
	var args := OS.get_cmdline_user_args()
	var env := WorldEnvironment.new()
	env.environment = Environment.new()
	env.environment.background_mode = Environment.BG_COLOR
	env.environment.background_color = Color(0.1, 0.12, 0.2)
	add_child(env)
	var camera := Camera3D.new()
	camera.position = Vector3(0, 1.2, 4)
	add_child(camera)
	camera.look_at(Vector3.ZERO)
	cube = MeshInstance3D.new()
	cube.mesh = BoxMesh.new()
	var material := StandardMaterial3D.new()
	material.albedo_color = Color(0.9, 0.5, 0.2)
	cube.material_override = material
	add_child(cube)
	light = OmniLight3D.new()
	light.omni_range = 10
	light.light_energy = 3
	add_child(light)
	_tone()
	if "--hud" in args:
		_hud()
	if "--dialog" in args or "--dialog-at" in args:
		_dialog()
	if "--dialog-at" in args:
		dialog_at = int(args[args.find("--dialog-at") + 1])
		dialog.visible = false
	if "--quit-at" in args:
		quit_at = int(args[args.find("--quit-at") + 1])
	if "--popup" in args:
		var popup := AcceptDialog.new()
		popup.name = "Popup"
		popup.dialog_text = "Connection lost"
		add_child(popup)
		popup.popup_centered_ratio(0.6)


func _process(_delta: float) -> void:
	frame += 1
	if frame == dialog_at:
		dialog.visible = true
	if frame == quit_at:
		get_tree().quit()
	cube.rotation = Vector3(frame * 0.02, frame * 0.03, 0)
	light.position = Vector3(cos(frame * 0.05) * 3, 2, sin(frame * 0.05) * 3)


## A 440 Hz tone, looping: something for the movie's audio to hold.
func _tone() -> void:
	var rate := 44100
	var data := PackedByteArray()
	data.resize(rate * 2)
	for i in rate:
		data.encode_s16(i * 2, int(sin(TAU * 440.0 * i / rate) * 12000))
	var wav := AudioStreamWAV.new()
	wav.format = AudioStreamWAV.FORMAT_16_BITS
	wav.mix_rate = rate
	wav.data = data
	wav.loop_mode = AudioStreamWAV.LOOP_FORWARD
	wav.loop_end = rate
	var player := AudioStreamPlayer.new()
	player.stream = wav
	add_child(player)
	player.play()


func _hud() -> void:
	var layer := CanvasLayer.new()
	layer.name = "HUD"
	add_child(layer)
	var root := Control.new()
	root.name = "Root"
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	layer.add_child(root)
	var score := Label.new()
	score.text = "SCORE 12400"
	score.position = Vector2(16, 12)
	root.add_child(score)
	var health := Panel.new()
	health.name = "Health"
	health.set_anchors_preset(Control.PRESET_BOTTOM_LEFT)
	health.position = Vector2(16, -40)
	health.size = Vector2(220, 24)
	root.add_child(health)
	var crosshair := ColorRect.new()
	crosshair.name = "Crosshair"
	crosshair.color = Color.WHITE
	crosshair.size = Vector2(6, 6)
	crosshair.set_anchors_and_offsets_preset(Control.PRESET_CENTER, Control.PRESET_MODE_KEEP_SIZE)
	root.add_child(crosshair)


func _dialog() -> void:
	var layer := CanvasLayer.new()
	layer.name = "Modal"
	add_child(layer)
	var panel := PanelContainer.new()
	panel.name = "Dialog"
	panel.anchor_left = 0.2
	panel.anchor_top = 0.2
	panel.anchor_right = 0.8
	panel.anchor_bottom = 0.8
	layer.add_child(panel)
	var label := Label.new()
	label.text = "Connection lost. Reconnecting..."
	label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	panel.add_child(label)
	dialog = panel
