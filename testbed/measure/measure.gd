extends Node
## Scenes for gdh's measures (tests/test_measure.py), picked with -- --mode NAME:
##   still     a lit scene from a still camera (nothing should flicker)
##   flicker   the same, with a patch of noise that changes every frame
##   orbit     the camera circling the scene smoothly
##   swim      the same orbit, the camera shaken by under a pixel each frame
##   lines     white lines 1, 3 and 6 px wide and a grey one 1 px wide on black, upright at x = 200, 400, 600, 800
##   nan       the still scene, with a sphere whose shader makes a NaN, and a black patch in a dark frame (not a NaN)
##   crush     ACES tone mapping over a dark gradient: the top half from linear 0, the bottom half lifted to 0.004
##   dissolve  two layers that split the screen between them by a fixed pattern (show_layer picks what draws)
## Every 3D mode has a CompositorEffect that captures a timestamp named "Testbed Effect", as a game's own pass would.

const NAN_SHADER := """
shader_type spatial;
uniform float below = -1.0;
void fragment() {
	ALBEDO = vec3(0.8) * sqrt(below);  // the square root of a negative number: a NaN
}
"""

const NOISE_SHADER := """
shader_type spatial;
render_mode unshaded;
void fragment() {
	vec2 cell = floor(FRAGCOORD.xy);
	ALBEDO = vec3(fract(sin(dot(cell, vec2(12.9898, 78.233)) + TIME * 37.0) * 43758.5453));
}
"""

const GRADIENT_SHADER := """
shader_type spatial;
render_mode unshaded;
uniform float lift = 0.0;
void fragment() {
	// Linear values from `lift` to 0.02 across the quad.
	ALBEDO = vec3(mix(lift, 0.02, UV.x));
}
"""

const DISSOLVE_SHADER := """
shader_type canvas_item;
uniform float share = 0.5;
uniform bool second = false;
uniform float salt = 0.0;
float hash(vec2 p) {
	return fract(sin(dot(p, vec2(12.9898, 78.233)) + salt) * 43758.5453);
}
void fragment() {
	float h = hash(floor(FRAGCOORD.xy));
	bool mine = second ? h >= share : h < share;
	COLOR = mine ? vec4(1.0) : vec4(0.0, 0.0, 0.0, 1.0);
}
"""

var mode := "still"
var frame := 0
var camera: Camera3D
var _layers := {}


func _ready() -> void:
	var args := OS.get_cmdline_user_args()
	var at := args.find("--mode")
	if at >= 0 and at + 1 < args.size():
		mode = args[at + 1]
	match mode:
		"lines":
			_lines()
		"dissolve":
			_dissolve()
		"crush":
			_crush()
		_:
			_world()


func _process(_delta: float) -> void:
	frame += 1
	if mode in ["orbit", "swim"]:
		var angle := frame * 0.004
		camera.position = Vector3(sin(angle) * 6.0, 2.5, cos(angle) * 6.0)
		camera.look_at(Vector3(0, 0.5, 0))
		if mode == "swim":
			# About a pixel of shake at most, different each frame (a fixed sequence, so runs repeat).
			camera.h_offset = (fmod(frame * 0.618034, 1.0) - 0.5) * 0.012
			camera.v_offset = (fmod(frame * 0.414214, 1.0) - 0.5) * 0.012


func _environment(tonemap: int, background: Color) -> void:
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = background
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.25, 0.27, 0.3)
	env.tonemap_mode = tonemap
	var world := WorldEnvironment.new()
	world.environment = env
	add_child(world)


func _camera() -> void:
	camera = Camera3D.new()
	camera.position = Vector3(0, 2.5, 6)
	add_child(camera)
	camera.look_at(Vector3(0, 0.5, 0))
	var compositor := Compositor.new()
	compositor.compositor_effects = [preload("timestamp_effect.gd").new()]
	camera.compositor = compositor


func _mesh(mesh: Mesh, at: Vector3, material: Material = null) -> MeshInstance3D:
	var node := MeshInstance3D.new()
	node.mesh = mesh
	node.position = at
	if material:
		node.material_override = material
	add_child(node)
	return node


func _world() -> void:
	_environment(Environment.TONE_MAPPER_LINEAR, Color(0.35, 0.45, 0.6))
	_camera()
	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-50, 30, 0)
	sun.shadow_enabled = true
	add_child(sun)
	var ground := PlaneMesh.new()
	ground.size = Vector2(12, 12)
	_mesh(ground, Vector3.ZERO)
	var red := StandardMaterial3D.new()
	red.albedo_color = Color(0.9, 0.3, 0.2)
	_mesh(BoxMesh.new(), Vector3(-1.2, 0.5, 0), red)
	_mesh(SphereMesh.new(), Vector3(1.2, 0.5, 0))
	if mode == "flicker":
		var noise := ShaderMaterial.new()
		noise.shader = _shader(NOISE_SHADER)
		var quad := QuadMesh.new()
		quad.size = Vector2(1, 1)
		_mesh(quad, Vector3(0, 1.5, 0.5), noise)
	if mode == "nan":
		var bad := ShaderMaterial.new()
		bad.shader = _shader(NAN_SHADER)
		_mesh(SphereMesh.new(), Vector3(0, 1.4, 0.5), bad)
		# Black on purpose, in a dark frame: a shadow-like black that isn't a NaN.
		var dark := StandardMaterial3D.new()
		dark.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		dark.albedo_color = Color(0.01, 0.01, 0.01)
		var black := StandardMaterial3D.new()
		black.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		black.albedo_color = Color(0, 0, 0)
		var frame_quad := QuadMesh.new()
		frame_quad.size = Vector2(1.2, 1.2)
		_mesh(frame_quad, Vector3(-2.6, 1.6, 0.5), dark)
		var hole := QuadMesh.new()
		hole.size = Vector2(0.6, 0.6)
		_mesh(hole, Vector3(-2.6, 1.6, 0.51), black)


func _crush() -> void:
	_environment(Environment.TONE_MAPPER_ACES, Color(0, 0, 0))
	_camera()
	camera.position = Vector3(0, 0, 2)
	camera.rotation = Vector3.ZERO
	camera.projection = Camera3D.PROJECTION_ORTHOGONAL
	camera.size = 2.0
	for row in 2:
		var gradient := ShaderMaterial.new()
		gradient.shader = _shader(GRADIENT_SHADER)
		gradient.set_shader_parameter("lift", 0.0 if row == 0 else 0.004)
		var quad := QuadMesh.new()
		quad.size = Vector2(4, 1)
		_mesh(quad, Vector3(0, 0.5 - row, 0), gradient)


func _lines() -> void:
	var layer := CanvasLayer.new()
	add_child(layer)
	var back := ColorRect.new()
	back.color = Color.BLACK
	back.set_anchors_preset(Control.PRESET_FULL_RECT)
	layer.add_child(back)
	for spec in [[200, 1, Color.WHITE], [400, 3, Color.WHITE], [600, 6, Color.WHITE], [800, 1, Color(0.5, 0.5, 0.5)]]:
		var line := ColorRect.new()
		line.color = spec[2]
		line.position = Vector2(spec[0], 100)
		line.size = Vector2(spec[1], 500)
		layer.add_child(line)


func _dissolve() -> void:
	var layer := CanvasLayer.new()
	add_child(layer)
	var back := ColorRect.new()
	back.color = Color.BLACK
	back.set_anchors_preset(Control.PRESET_FULL_RECT)
	layer.add_child(back)
	for key in ["a", "b", "b_unmatched"]:
		var rect := ColorRect.new()
		rect.position = Vector2(100, 100)
		rect.size = Vector2(400, 300)
		var material := ShaderMaterial.new()
		material.shader = _canvas_shader(DISSOLVE_SHADER)
		material.set_shader_parameter("second", key != "a")
		material.set_shader_parameter("salt", 3.0 if key == "b_unmatched" else 0.0)
		rect.material = material
		rect.visible = false
		layer.add_child(rect)
		_layers[key] = rect
	_layers["whole"] = _whole(layer)


func _whole(layer: CanvasLayer) -> ColorRect:
	var rect := ColorRect.new()
	rect.position = Vector2(100, 100)
	rect.size = Vector2(400, 300)
	rect.color = Color.WHITE
	rect.visible = false
	layer.add_child(rect)
	return rect


## Shows one layer of the dissolve alone: "a", "b", "b_unmatched" (a second layer whose pattern doesn't match the
## first's) or "whole"; share is how far the dissolve has gone.
func show_layer(which: String, share: float = 0.5) -> String:
	for key in _layers:
		_layers[key].visible = key == which
		if _layers[key].material:
			_layers[key].material.set_shader_parameter("share", share)
	return which


func _shader(code: String) -> Shader:
	var shader := Shader.new()
	shader.code = code
	return shader


func _canvas_shader(code: String) -> Shader:
	return _shader(code)
