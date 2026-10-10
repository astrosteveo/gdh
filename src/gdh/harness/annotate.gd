extends RefCounted
## What's on screen, as geometry in screenshot pixels, for `gdh live shot --annotate` to draw over a shot.
##
##   var found: Dictionary = Annotate.collect(get_tree(), ["names", "collisions"], {"path": "Level", "class": "Enemy"})
##
## Layers:
##   names       each node that draws (a sprite, a mesh, a label, a button; not the plain Controls and containers that
##               only lay others out), with its box: {"nodes": [{path, class, box, kind}]}, kind "ui", "2d" or "3d". With a class or group
##               filter, the matching nodes instead, drawn or not, each boxed round what it and its children draw.
##   collisions  each CollisionShape2D/3D and CollisionPolygon2D's outline: {"shapes": [{path, kind, lines}]}, kind
##               "body", "area" or "disabled"; a line is {"points": [[x, y], ...], "closed": bool}
##   nav         each NavigationRegion2D/3D's polygons: {"nav": [{path, polygons: [[[x, y], ...], ...]}]}
##   velocity    each body's velocity as an arrow to where it would be in VELOCITY_SECONDS: {"velocity": [{path, from,
##               to, speed, unit}]}
## Filters (all given must match): path (only under this node), group, class (built in or a class_name, or one it
## extends).

const Common := preload("common.gd")
const Screen := preload("screen.gd")
const Track := preload("track.gd")

const LAYERS := ["names", "collisions", "nav", "velocity"]
## The most nodes boxed, and the most line points one shape or navigation region gives.
const MAX_NODES := 120
const MAX_POINTS := 4000
## A box covering this share of the screen or more is a backdrop: listed, not boxed.
const BACKDROP_SHARE := 0.9
## A velocity arrow reaches where the body would be after this long.
const VELOCITY_SECONDS := 0.5
const CIRCLE_POINTS := 32


static func collect(tree: SceneTree, layers: Array, filters: Dictionary) -> Dictionary:
	var start: Node = tree.root
	if filters.has("path"):
		start = Screen.node_at(tree, str(filters.path))
		if start == null:
			return {"error": "No node at %s." % filters.path}
	var nodes := _walk(start, tree)
	var out := {"layers": layers}
	if "names" in layers:
		out.merge(_names(tree, nodes, filters))
	if "collisions" in layers:
		out.shapes = _collisions(tree, nodes, filters)
	if "nav" in layers:
		out.nav = _nav(tree, nodes, filters)
	if "velocity" in layers:
		out.velocity = _velocities(tree, nodes, filters)
	return out


## `node` and everything under it that isn't a control's own internal part or gdh's.
static func _walk(node: Node, tree: SceneTree) -> Array[Node]:
	var out: Array[Node] = []
	if node != tree.root:
		out.append(node)
	for child in node.get_children():
		if node == tree.root and str(child.name).begins_with("Gdh"):
			continue
		if child is Window and child != tree.root and not (child as Window).visible:
			continue
		out.append_array(_walk(child, tree))
	return out


static func _wanted(node: Node, filters: Dictionary) -> bool:
	if filters.has("group") and not node.is_in_group(StringName(str(filters.group))):
		return false
	if filters.has("class") and not Screen._is_class(node, str(filters["class"])):
		return false
	return true


## Whether a node draws something of its own: not a plain Control or a container that only places others, not a
## Node2D or Node3D that only holds others.
static func draws(node: Node) -> bool:
	if node is Control:
		if node.get_class() == "Control" or node.get_class() == "SubViewportContainer":
			return node.get_script() != null and node.has_method("_draw")
		if node is Container:
			return node is PanelContainer
		return true
	if node is CanvasItem:
		if node.get_class() in ["Node2D", "CanvasGroup"] or node is CollisionObject2D or node is CollisionShape2D \
				or node is CollisionPolygon2D or node is Camera2D or node is Marker2D:
			return node.get_script() != null and node.has_method("_draw")
		return true
	return node is GeometryInstance3D


## "ui" for a CanvasItem on a CanvasLayer (one that doesn't follow the camera), "2d" for one in the world, "3d".
static func _kind(node: Node) -> String:
	if not node is CanvasItem:
		return "3d"
	var layer := (node as CanvasItem).get_canvas_layer_node()
	return "ui" if layer != null and not layer.follow_viewport_enabled else "2d"


static func _names(tree: SceneTree, nodes: Array[Node], filters: Dictionary) -> Dictionary:
	var size := Common.image_size(tree)
	var screen_area := float(size[0] * size[1])
	var chosen := filters.has("group") or filters.has("class")
	var boxed := []
	var backdrops := []
	var more := 0
	for node in nodes:
		if not (node is CanvasItem or node is Node3D):
			continue
		if chosen:
			if not _wanted(node, filters):
				continue
		elif not draws(node):
			continue
		if not Screen.hidden_by(node).is_empty():
			continue
		var box: Variant = null
		if chosen:
			box = Track.extent(tree, node)
			if box != null:
				box = (box as Rect2).intersection(Rect2(0, 0, size[0], size[1]))
				if not (box as Rect2).has_area():
					box = null
		else:
			var where := Screen.seen(tree, node)
			box = where.get("box")
		if box == null or not (box as Rect2).has_area():
			continue
		var entry := {"path": Screen.path_of(tree, node), "class": node.get_class(), "box": Screen.to_array(box),
				"kind": _kind(node)}
		if not chosen and (box as Rect2).get_area() >= BACKDROP_SHARE * screen_area:
			backdrops.append(entry)
			continue
		if boxed.size() >= MAX_NODES:
			more += 1
			continue
		boxed.append(entry)
	return {"nodes": boxed, "backdrops": backdrops, "more": more}


# --- Collision shapes --------------------------------------------------------------------------------------------

static func _collisions(tree: SceneTree, nodes: Array[Node], filters: Dictionary) -> Array:
	var out := []
	for node in nodes:
		if not (node is CollisionShape2D or node is CollisionPolygon2D or node is CollisionShape3D):
			continue
		var owner_body := node.get_parent()
		if not (_wanted(node, filters) or (owner_body != null and _wanted(owner_body, filters))):
			continue
		if not Screen.hidden_by(node).is_empty():
			continue
		var lines := []
		if node is CollisionShape2D:
			var shape: Shape2D = (node as CollisionShape2D).shape
			if shape != null:
				lines = _lines_2d(tree, node as CanvasItem, _shape_2d(shape))
		elif node is CollisionPolygon2D:
			var polygon := (node as CollisionPolygon2D).polygon
			lines = _lines_2d(tree, node as CanvasItem, [{"points": polygon, "closed": true}])
		else:
			var shape3d: Shape3D = (node as CollisionShape3D).shape
			if shape3d != null:
				lines = _lines_3d(tree, node as Node3D, _debug_lines(shape3d.get_debug_mesh()))
		if lines.is_empty():
			continue
		var disabled: bool = node.get("disabled") == true
		var kind := "disabled" if disabled else ("area" if owner_body is Area2D or owner_body is Area3D else "body")
		out.append({"path": Screen.path_of(tree, node), "kind": kind, "lines": lines})
	return out


## A 2D shape's outline in its own coordinates: [{"points": PackedVector2Array, "closed": bool}].
static func _shape_2d(shape: Shape2D) -> Array:
	if shape is RectangleShape2D:
		var h: Vector2 = (shape as RectangleShape2D).size / 2
		return [{"points": PackedVector2Array([-h, Vector2(h.x, -h.y), h, Vector2(-h.x, h.y)]), "closed": true}]
	if shape is CircleShape2D:
		return [{"points": _arc(Vector2.ZERO, (shape as CircleShape2D).radius, 0.0, TAU, CIRCLE_POINTS), "closed": true}]
	if shape is CapsuleShape2D:
		var capsule := shape as CapsuleShape2D
		var r := capsule.radius
		var half := maxf(capsule.height / 2 - r, 0.0)
		var points := _arc(Vector2(0, -half), r, PI, TAU, CIRCLE_POINTS / 2)
		points.append_array(_arc(Vector2(0, half), r, 0.0, PI, CIRCLE_POINTS / 2))
		return [{"points": points, "closed": true}]
	if shape is SegmentShape2D:
		var segment := shape as SegmentShape2D
		return [{"points": PackedVector2Array([segment.a, segment.b]), "closed": false}]
	if shape is SeparationRayShape2D:
		return [{"points": PackedVector2Array([Vector2.ZERO, Vector2(0, (shape as SeparationRayShape2D).length)]),
				"closed": false}]
	if shape is ConvexPolygonShape2D:
		return [{"points": (shape as ConvexPolygonShape2D).points, "closed": true}]
	if shape is ConcavePolygonShape2D:
		var segments := (shape as ConcavePolygonShape2D).segments
		var out := []
		for i in range(0, segments.size() - 1, 2):
			out.append({"points": PackedVector2Array([segments[i], segments[i + 1]]), "closed": false})
		return out
	if shape is WorldBoundaryShape2D:
		var boundary := shape as WorldBoundaryShape2D
		var along := boundary.normal.orthogonal() * 100000.0
		var at := boundary.normal * boundary.distance
		return [{"points": PackedVector2Array([at - along, at + along]), "closed": false}]
	return []


static func _arc(centre: Vector2, radius: float, from: float, to: float, count: int) -> PackedVector2Array:
	var out := PackedVector2Array()
	for i in count + 1:
		var a := lerpf(from, to, float(i) / count)
		out.append(centre + Vector2(cos(a), sin(a)) * radius)
	return out


## Lines in a CanvasItem's own coordinates, to screenshot pixels.
static func _lines_2d(tree: SceneTree, node: CanvasItem, lines: Array) -> Array:
	var to_root: Variant = Screen._to_root(tree, node.get_viewport())
	if to_root == null:
		return []
	var xf: Transform2D = (to_root as Transform2D) * node.get_global_transform_with_canvas()
	var scale := Common.shot_scale(tree)
	var out := []
	var budget := MAX_POINTS
	for line in lines:
		var points := []
		for p in line.points:
			if budget <= 0:
				break
			budget -= 1
			points.append(_xy((xf * p) * scale))
		out.append({"points": points, "closed": line.closed})
	return out


## A debug mesh's line segments (PRIMITIVE_LINES: each pair of vertices one segment), as [[a, b], ...].
static func _debug_lines(mesh: Mesh) -> Array:
	var out := []
	if mesh == null or mesh.get_surface_count() == 0:
		return out
	var vertices: PackedVector3Array = mesh.surface_get_arrays(0)[Mesh.ARRAY_VERTEX]
	for i in range(0, mini(vertices.size(), MAX_POINTS) - 1, 2):
		out.append([vertices[i], vertices[i + 1]])
	return out


## Segments in a Node3D's own coordinates, through its viewport's camera to screenshot pixels; a segment with an end
## behind the camera is left out.
static func _lines_3d(tree: SceneTree, node: Node3D, segments: Array) -> Array:
	var camera := node.get_viewport().get_camera_3d()
	var to_root: Variant = Screen._to_root(tree, node.get_viewport())
	if camera == null or to_root == null:
		return []
	var xf := node.global_transform
	var out := []
	for segment in segments:
		var a: Variant = _project(tree, camera, to_root, xf * (segment[0] as Vector3))
		var b: Variant = _project(tree, camera, to_root, xf * (segment[1] as Vector3))
		if a != null and b != null:
			out.append({"points": [a, b], "closed": false})
	return out


static func _project(tree: SceneTree, camera: Camera3D, to_root: Transform2D, world: Vector3) -> Variant:
	if camera.is_position_behind(world):
		return null
	return _xy((to_root * camera.unproject_position(world)) * Common.shot_scale(tree))


static func _xy(p: Vector2) -> Array:
	return [snappedf(p.x, 0.1), snappedf(p.y, 0.1)]


# --- Navigation ------------------------------------------------------------------------------------------------------

static func _nav(tree: SceneTree, nodes: Array[Node], filters: Dictionary) -> Array:
	var out := []
	for node in nodes:
		if not (node is NavigationRegion2D or node is NavigationRegion3D) or not _wanted(node, filters):
			continue
		if not Screen.hidden_by(node).is_empty():
			continue
		var polygons := []
		if node is NavigationRegion2D:
			var nav := (node as NavigationRegion2D).navigation_polygon
			if nav == null:
				continue
			var shapes := []
			var vertices := nav.get_vertices()
			for i in nav.get_polygon_count():
				var points := PackedVector2Array()
				for index in nav.get_polygon(i):
					points.append(vertices[index])
				shapes.append({"points": points, "closed": true})
			if shapes.is_empty():  # not baked yet: its outlines
				for i in nav.get_outline_count():
					shapes.append({"points": nav.get_outline(i), "closed": true})
			for line in _lines_2d(tree, node as CanvasItem, shapes):
				polygons.append(line.points)
		else:
			var mesh := (node as NavigationRegion3D).navigation_mesh
			var camera := node.get_viewport().get_camera_3d()
			var to_root: Variant = Screen._to_root(tree, node.get_viewport())
			if mesh == null or camera == null or to_root == null:
				continue
			var vertices := mesh.get_vertices()
			var xf := (node as Node3D).global_transform
			for i in mini(mesh.get_polygon_count(), MAX_POINTS):
				var points := []
				for index in mesh.get_polygon(i):
					var p: Variant = _project(tree, camera, to_root, xf * vertices[index])
					if p == null:
						points.clear()
						break
					points.append(p)
				if points.size() >= 3:
					polygons.append(points)
		if not polygons.is_empty():
			out.append({"path": Screen.path_of(tree, node), "polygons": polygons})
	return out


# --- Velocity --------------------------------------------------------------------------------------------------------

static func _velocities(tree: SceneTree, nodes: Array[Node], filters: Dictionary) -> Array:
	var out := []
	for node in nodes:
		if not _wanted(node, filters) or not Screen.hidden_by(node).is_empty():
			continue
		var v: Variant = null
		if node is CharacterBody2D or node is CharacterBody3D:
			v = node.velocity
		elif node is RigidBody2D or node is RigidBody3D:
			v = node.linear_velocity
		if v == null or (v is Vector2 and (v as Vector2).length() < 0.01) or (v is Vector3 and (v as Vector3).length() < 0.001):
			continue
		var from: Variant = null
		var to: Variant = null
		if node is Node2D:
			var to_root: Variant = Screen._to_root(tree, node.get_viewport())
			if to_root == null:
				continue
			var item := node as Node2D
			var xf: Transform2D = (to_root as Transform2D) * item.get_global_transform_with_canvas() * item.global_transform.affine_inverse()
			var scale := Common.shot_scale(tree)
			from = _xy((xf * item.global_position) * scale)
			to = _xy((xf * (item.global_position + (v as Vector2) * VELOCITY_SECONDS)) * scale)
		else:
			var camera := node.get_viewport().get_camera_3d()
			var to_root3: Variant = Screen._to_root(tree, node.get_viewport())
			if camera == null or to_root3 == null:
				continue
			var at := (node as Node3D).global_position
			from = _project(tree, camera, to_root3, at)
			to = _project(tree, camera, to_root3, at + (v as Vector3) * VELOCITY_SECONDS)
		if from == null or to == null:
			continue
		out.append({"path": Screen.path_of(tree, node), "from": from, "to": to,
				"speed": snappedf(v.length(), 0.01), "unit": "px/s" if node is Node2D else "m/s"})
	return out
