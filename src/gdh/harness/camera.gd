extends RefCounted
## gdh's own camera in a live game (`gdh live camera`), so an agent can look anywhere without game code: a Camera3D
## looking from one point at another, or a Camera2D centred on a point, made current in place of the game's camera. It
## is an internal child of the root, so game code walking the tree doesn't see it, and stays until released, when the
## game's camera, the one it replaced, is current again.

const Screen := preload("screen.gd")

const NAMES := {"3d": "GdhCamera3D", "2d": "GdhCamera2D"}


## args: from [x, y, z] and at [x, y, z] (3D; fov in degrees, far in metres), or at [x, y] (2D; zoom, 2 twice as
## close), or release: true.
static func command(tree: SceneTree, args: Dictionary) -> Dictionary:
	if args.get("release", false):
		return release(tree)
	var at: Array = args.get("at", [])
	if args.has("from"):
		var from: Array = args.from
		if from.size() != 3 or at.size() != 3:
			return {"error": "A 3D view takes from [x, y, z] and at [x, y, z]."}
		return _look_3d(tree, Vector3(from[0], from[1], from[2]), Vector3(at[0], at[1], at[2]), args)
	if at.size() == 2:
		return _look_2d(tree, Vector2(at[0], at[1]), float(args.get("zoom", 1.0)))
	return {"error": "camera takes from and at (3D), at [x, y] (2D), or release."}


static func _look_3d(tree: SceneTree, from: Vector3, at: Vector3, args: Dictionary) -> Dictionary:
	if from.is_equal_approx(at):
		return {"error": "The camera can't look at the point it's at."}
	var root := tree.root
	var camera := root.get_node_or_null(NAMES["3d"]) as Camera3D
	if camera == null:
		camera = Camera3D.new()
		camera.name = NAMES["3d"]
		var game := root.get_camera_3d()
		if game != null:
			# The game camera's lens and what it sees through (its environment, its layers).
			for property in ["fov", "near", "far", "cull_mask", "environment", "attributes", "keep_aspect"]:
				camera.set(property, game.get(property))
			camera.set_meta("replaced", weakref(game))
		root.add_child(camera, false, Node.INTERNAL_MODE_BACK)
	if args.has("fov"):
		camera.fov = clampf(float(args.fov), 1.0, 179.0)
	if args.has("far"):
		camera.far = float(args.far)
	var up := Vector3.UP if absf((at - from).normalized().y) < 0.99 else Vector3.FORWARD
	camera.look_at_from_position(from, at, up)
	camera.make_current()
	return {"camera": "3d", "from": _vec(from), "at": _vec(at), "fov": snappedf(camera.fov, 0.01),
		"near": camera.near, "far": camera.far, "replaced": _replaced(tree, camera)}


static func _look_2d(tree: SceneTree, at: Vector2, zoom: float) -> Dictionary:
	if zoom <= 0.0:
		return {"error": "zoom takes a number above 0."}
	var root := tree.root
	var camera := root.get_node_or_null(NAMES["2d"]) as Camera2D
	if camera == null:
		camera = Camera2D.new()
		camera.name = NAMES["2d"]
		# It moves the view while the game is held, when a pausable camera never updates.
		camera.process_mode = Node.PROCESS_MODE_ALWAYS
		var game := root.get_camera_2d()
		if game != null:
			camera.set_meta("replaced", weakref(game))
		camera.set_meta("canvas_transform", root.canvas_transform)
		root.add_child(camera, false, Node.INTERNAL_MODE_BACK)
	camera.position = at
	camera.zoom = Vector2(zoom, zoom)
	camera.make_current()
	camera.reset_smoothing()
	camera.force_update_scroll()
	return {"camera": "2d", "at": [snappedf(at.x, 0.01), snappedf(at.y, 0.01)], "zoom": zoom,
		"replaced": _replaced(tree, camera)}


## Frees gdh's cameras and makes the ones they replaced current again.
static func release(tree: SceneTree) -> Dictionary:
	var root := tree.root
	var restored := []
	var released := false
	for kind in NAMES:
		var camera := root.get_node_or_null(NAMES[kind])
		if camera == null:
			continue
		released = true
		var game: Node = camera.get_meta("replaced").get_ref() if camera.has_meta("replaced") else null
		if camera.has_meta("canvas_transform"):
			root.canvas_transform = camera.get_meta("canvas_transform")
		root.remove_child(camera)
		camera.free()
		if game != null and game.is_inside_tree():
			game.make_current()
			if game is Camera2D:
				(game as Camera2D).force_update_scroll()
			restored.append(Screen.path_of(tree, game))
	return {"released": released, "restored": restored}


static func _replaced(tree: SceneTree, camera: Node) -> String:
	var game: Node = camera.get_meta("replaced").get_ref() if camera.has_meta("replaced") else null
	return Screen.path_of(tree, game) if game != null else ""


static func _vec(v: Vector3) -> Array:
	return [snappedf(v.x, 0.01), snappedf(v.y, 0.01), snappedf(v.z, 0.01)]
