extends RefCounted
## What's drawn at a point on screen, for `gdh live pick`: every node whose drawing covers it, topmost first.
##
##   var found: Array = Pick.at(get_tree(), [Vector2(640, 360)])
##   found: [{"at": [x, y], "hits": [{path, class, kind, ...}], "more": n, "takes_click"?: {path, class, ...}}]
##
## The canvas draws over the 3D world, so the 2D hits come first: by CanvasLayer (highest first; the world's canvas is
## layer 0), then by z_index, then the later in the tree. Y-sorting isn't followed, so among y-sorted siblings the
## order is the tree's. A CanvasItem covers the point when it falls inside what it draws in its own coordinates: a
## Control's rect, a Sprite2D's opaque texels (with the texture and the texel), a Polygon2D's polygon, a Line2D's
## width, any other's rect. The 3D hits are by distance from the camera: a ray from the camera through the point, met
## against each mesh's triangles (in its rest pose for a skinned mesh), with the surface it hit, its material, the
## hit's world position and normal. A GeometryInstance3D with no triangles to test (particles, a Label3D) counts by
## its bounds. Only what's drawn in the root viewport and the windows and SubViewportContainers in it is picked.

const Screen := preload("screen.gd")
const Common := preload("common.gd")

## The most hits listed at a point.
const MAX_HITS := 8


static func at(tree: SceneTree, points: Array) -> Array:
	var items := []
	var meshes := []
	var order := 0
	for node in _walk(tree.root, tree):
		order += 1
		if not Screen.hidden_by(node).is_empty():
			continue
		if node is CanvasItem:
			items.append([node, order])
		elif node is GeometryInstance3D:
			meshes.append(node)
	items.sort_custom(func(a: Array, b: Array) -> bool: return _above(a[0], a[1], b[0], b[1]))
	var cache := {}
	var out := []
	for p in points:
		var point := Vector2(p[0], p[1])
		var hits := []
		for entry in items:
			var hit: Variant = _canvas_hit(tree, entry[0], point)
			if hit != null:
				hits.append(hit)
		hits.append_array(_mesh_hits(tree, meshes, point, cache))
		var result := {"at": [point.x, point.y], "hits": hits.slice(0, MAX_HITS), "more": maxi(hits.size() - MAX_HITS, 0)}
		var taker := Screen.taker_at(tree, point)
		if taker != null:
			result.takes_click = Screen.took(tree, taker)
		out.append(result)
	return out


static func _walk(node: Node, tree: SceneTree) -> Array[Node]:
	var out: Array[Node] = []
	for child in node.get_children():
		if node == tree.root and str(child.name).begins_with("Gdh"):
			continue
		out.append(child)
		out.append_array(_walk(child, tree))
	return out


## Whether CanvasItem a (at tree position ia) draws above b: a higher CanvasLayer, then a higher z, then later.
static func _above(a: CanvasItem, ia: int, b: CanvasItem, ib: int) -> bool:
	var la := _layer(a)
	var lb := _layer(b)
	if la != lb:
		return la > lb
	var za := _z(a)
	var zb := _z(b)
	if za != zb:
		return za > zb
	return ia > ib


static func _layer(item: CanvasItem) -> int:
	var layer := item.get_canvas_layer_node()
	return layer.layer if layer != null else 0


## A CanvasItem's z: its z_index, plus its parent's while it's relative.
static func _z(item: CanvasItem) -> int:
	var z := 0
	var at: Node = item
	while at is CanvasItem:
		z += (at as CanvasItem).z_index
		if not (at as CanvasItem).z_as_relative:
			break
		at = at.get_parent()
	return z


## The hit for a CanvasItem at a point on screen, or null when it doesn't draw there.
static func _canvas_hit(tree: SceneTree, item: CanvasItem, point: Vector2) -> Variant:
	if not _draws(item):
		return null
	var to_root: Variant = Screen._to_root(tree, item.get_viewport())
	if to_root == null:
		return null
	var xf: Transform2D = (to_root as Transform2D) * item.get_global_transform_with_canvas()
	if is_zero_approx(xf.determinant()):
		return null
	var local: Vector2 = xf.affine_inverse() * (point / Common.shot_scale(tree))
	var hit := {"path": Screen.path_of(tree, item), "class": item.get_class(), "kind": _kind(item),
			"local": [snappedf(local.x, 0.1), snappedf(local.y, 0.1)]}
	if item is Control:
		if not Rect2(Vector2.ZERO, (item as Control).size).has_point(local):
			return null
		var text := Screen.text_of(item)
		if not text.is_empty():
			hit.text = text.left(80)
		var texture: Variant = item.get("texture")
		if texture is Texture2D:
			hit.texture = _resource(texture)
	elif item is Sprite2D:
		var sprite := item as Sprite2D
		if not sprite.get_rect().has_point(local) or not sprite.is_pixel_opaque(local):
			return null
		hit.texture = _resource(sprite.texture)
		hit.texel = _texel(sprite, local)
	elif item is Polygon2D:
		var polygon := item as Polygon2D
		var points := PackedVector2Array()
		for p in polygon.polygon:
			points.append(p + polygon.offset)
		if not Geometry2D.is_point_in_polygon(local, points):
			return null
		if polygon.texture != null:
			hit.texture = _resource(polygon.texture)
	elif item is Line2D:
		var line := item as Line2D
		var near := false
		for i in line.points.size() - 1:
			var on := Geometry2D.get_closest_point_to_segment(local, line.points[i], line.points[i + 1])
			if on.distance_to(local) <= line.width / 2:
				near = true
				break
		if not near:
			return null
	else:
		var rect := Screen._drawn_rect(item)
		if item.has_method("get_rect") and item.get_rect() is Rect2:
			rect = item.get_rect()
		if not rect.has_area() or not rect.has_point(local):
			return null
		var texture: Variant = item.get("texture")
		if texture is Texture2D:
			hit.texture = _resource(texture)
	return hit


## Whether a CanvasItem draws anything of its own (as annotate.gd's draws(), which this mirrors for the 2D side).
static func _draws(item: CanvasItem) -> bool:
	if item is Control:
		if item.get_class() == "Control" or item is Container and not item is PanelContainer:
			return item.get_script() != null and item.has_method("_draw")
		return true
	if item.get_class() in ["Node2D", "CanvasGroup"] or item is CollisionObject2D or item is CollisionShape2D \
			or item is CollisionPolygon2D or item is Camera2D or item is Marker2D:
		return item.get_script() != null and item.has_method("_draw")
	return true


static func _kind(item: CanvasItem) -> String:
	var layer := item.get_canvas_layer_node()
	return "ui" if layer != null and not layer.follow_viewport_enabled else "2d"


## The texel of a Sprite2D's texture under a point in its own coordinates.
static func _texel(sprite: Sprite2D, local: Vector2) -> Array:
	var rect := sprite.get_rect()
	var region := sprite.region_rect if sprite.region_enabled else Rect2(Vector2.ZERO, sprite.texture.get_size())
	var frame := region.size / Vector2(sprite.hframes, sprite.vframes)
	var origin := region.position + frame * Vector2(sprite.frame % sprite.hframes, sprite.frame / sprite.hframes)
	var t := (local - rect.position) / rect.size
	if sprite.flip_h:
		t.x = 1.0 - t.x
	if sprite.flip_v:
		t.y = 1.0 - t.y
	var texel := origin + t * frame
	return [floori(texel.x), floori(texel.y)]


static func _resource(resource: Resource) -> Variant:
	if resource == null:
		return null
	if not resource.resource_path.is_empty():
		return resource.resource_path
	return resource.get_class() + (" " + resource.resource_name if not resource.resource_name.is_empty() else "")


# --- 3D ----------------------------------------------------------------------------------------------------------

## The 3D hits under a point, nearest first: a ray from the root viewport's camera met against each mesh's triangles.
static func _mesh_hits(tree: SceneTree, meshes: Array, point: Vector2, cache: Dictionary) -> Array:
	var hits := []
	for node in meshes:
		var geometry := node as GeometryInstance3D
		var viewport := geometry.get_viewport()
		var camera := viewport.get_camera_3d()
		var to_root: Variant = Screen._to_root(tree, viewport)
		if camera == null or to_root == null:
			continue
		var on_viewport: Vector2 = (to_root as Transform2D).affine_inverse() * (point / Common.shot_scale(tree))
		var origin := camera.project_ray_origin(on_viewport)
		var direction := camera.project_ray_normal(on_viewport)
		var hit: Variant = _mesh_hit(geometry, origin, direction, cache)
		if hit == null:
			continue
		hit.path = Screen.path_of(tree, geometry)
		hit["class"] = geometry.get_class()
		hit.kind = "3d"
		hit.distance = snappedf(origin.distance_to(hit.world), 0.001)
		hit.world = _xyz(hit.world)
		hits.append(hit)
	hits.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return a.distance < b.distance)
	return hits


static func _mesh_hit(geometry: GeometryInstance3D, origin: Vector3, direction: Vector3, cache: Dictionary) -> Variant:
	var xf := geometry.global_transform
	var bounds := xf * geometry.get_aabb()
	if bounds.intersects_ray(origin, direction) == null:
		return null
	var mesh: Mesh = geometry.mesh if geometry is MeshInstance3D else null
	if mesh == null:
		# Nothing to test triangle by triangle: its bounds.
		var at: Vector3 = bounds.intersects_ray(origin, direction)
		return {"world": at, "by": "bounds"}
	if not cache.has(mesh):
		cache[mesh] = mesh.generate_triangle_mesh()
	var triangles: TriangleMesh = cache[mesh]
	if triangles == null:
		return null
	var inverse := xf.affine_inverse()
	var found := triangles.intersect_ray(inverse * origin, (inverse.basis * direction).normalized())
	if found.is_empty():
		return null
	var surface := _surface_of(mesh, int(found.face_index))
	var hit := {"world": xf * (found.position as Vector3), "normal": _xyz((xf.basis * (found.normal as Vector3)).normalized()),
			"surface": surface, "mesh": _resource(mesh), "by": "triangles"}
	if surface >= 0:
		hit.material = _resource((geometry as MeshInstance3D).get_active_material(surface))
	if geometry is MeshInstance3D and (geometry as MeshInstance3D).skin != null:
		hit.note = "skinned: tested in its rest pose"
	return hit


## Which surface a triangle of generate_triangle_mesh() came from: it lists each triangle surface's faces in turn.
static func _surface_of(mesh: Mesh, face: int) -> int:
	var first := 0
	for s in mesh.get_surface_count():
		var arrays := mesh.surface_get_arrays(s)
		var primitive := Mesh.PRIMITIVE_TRIANGLES
		if mesh is ArrayMesh:
			primitive = (mesh as ArrayMesh).surface_get_primitive_type(s)
		if primitive != Mesh.PRIMITIVE_TRIANGLES and primitive != Mesh.PRIMITIVE_TRIANGLE_STRIP:
			continue
		var indices: Variant = arrays[Mesh.ARRAY_INDEX]
		var count: int = (indices as PackedInt32Array).size() if indices != null and (indices as PackedInt32Array).size() > 0 \
				else (arrays[Mesh.ARRAY_VERTEX] as PackedVector3Array).size()
		var faces := count / 3 if primitive == Mesh.PRIMITIVE_TRIANGLES else maxi(count - 2, 0)
		if face < first + faces:
			return s
		first += faces
	return -1


static func _xyz(v: Vector3) -> Array:
	return [snappedf(v.x, 0.001), snappedf(v.y, 0.001), snappedf(v.z, 0.001)]
