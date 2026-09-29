extends RefCounted
## Engine-side checks. Each one finds a likely defect from scene data instead of pixels.
##
##   var result: Dictionary = await Probes.new().run(get_tree())
##   result.findings: Array of {probe, severity, node, message, data, screen_rect, view}
##   result.stats: counters that show whether each check ran
##
## severity is "warning" (likely wrong) or "info" (worth a look, often intended).
## screen_rect is [x, y, w, h] in viewport pixels, or null. view names the
## capture that shows the problem best.

# A gap under 2 cm reads as contact at normal viewing distance. Above 1 m an
# object is more likely hung or mounted on purpose.
const FLOAT_MIN_GAP := 0.02
const FLOAT_MAX_GAP := 1.0
# Parts closer than 5 cm read as attached (a sign on a wall, a bulb in a shade).
const FLOAT_NEAR := 0.05
# Rule 2 needs 3+ matching siblings, at least 60% of them resting.
const FLOAT_MIN_SIBLINGS := 3
const FLOAT_SIBLING_SHARE := 0.6
# The probe's collision copies live alone on the top physics layer and detect
# nothing, so they never touch the game's own bodies, areas or colliders.
const PROBE_LAYER := 1 << 31
# Horizon tilt most players notice.
const MAX_CAMERA_ROLL_DEG := 1.0
# Linear filtering visibly blurs a texture once each texel covers 2+ pixels,
# but only where the texture has hard edges. A texture counts as hard-edged
# when 5%+ of neighboring texel pairs differ by more than 20% in any channel.
const BLUR_MAGNIFICATION := 2.0
const HARD_EDGE_DIFF := 0.2
const HARD_EDGE_SHARE := 0.05
const EMISSION_INFO_MAX := 16.0
# A child sitting 3+ px away from where its siblings' matching children sit,
# when at least 75% of those agree within 1 px.
const OFFSET_OUTLIER_PX := 3.0
const OFFSET_AGREE_PX := 1.0
const OFFSET_AGREE_SHARE := 0.75
const OFFSET_MIN_GROUP := 4

const KEY_PATTERN := "^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$"
const PLACEHOLDER_PATTERN := "(?i)\\b(lorem ipsum|placeholder|todo|tbd|fixme|sample text|insert text here)\\b"

const LINEAR_FILTERS := [
	CanvasItem.TEXTURE_FILTER_LINEAR,
	CanvasItem.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS,
	CanvasItem.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS_ANISOTROPIC,
]
const NEAREST_FILTERS := [
	CanvasItem.TEXTURE_FILTER_NEAREST,
	CanvasItem.TEXTURE_FILTER_NEAREST_WITH_MIPMAPS,
	CanvasItem.TEXTURE_FILTER_NEAREST_WITH_MIPMAPS_ANISOTROPIC,
]

var _tree: SceneTree
var _viewport: Viewport
var _root: Node
var _camera: Camera3D
var _findings: Array[Dictionary] = []
var _stats := {}
var _alpha_cache := {}
var _edge_cache := {}
var _key_regex := RegEx.create_from_string(KEY_PATTERN)
var _placeholder_regex := RegEx.create_from_string(PLACEHOLDER_PATTERN)


func run(tree: SceneTree) -> Dictionary:
	_tree = tree
	_viewport = tree.root
	_root = tree.current_scene if tree.current_scene else tree.root
	_camera = _viewport.get_camera_3d()

	var nodes: Array[Node] = []
	_collect(_root, nodes)
	var meshes: Array[MeshInstance3D] = []
	var textured: Array[Dictionary] = []

	_check_camera_roll()
	for node in nodes:
		if node is Node3D and not (node as Node3D).is_visible_in_tree():
			continue
		if node is CanvasItem and not (node as CanvasItem).is_visible_in_tree():
			continue
		if node is MeshInstance3D and (node as MeshInstance3D).mesh:
			meshes.append(node)
		if node is GeometryInstance3D:
			_check_materials(node)
			_check_scale(node)
		if node is Label3D:
			_check_label3d(node)
		if node is Light3D:
			_check_light(node)
		if node is Sprite2D:
			_check_region(node)
		if node is CanvasItem:
			var entry := _texture_entry(node)
			if not entry.is_empty():
				textured.append(entry)
		_check_text(node)

	_check_texture_filters(textured)
	_check_sibling_offsets(nodes)
	_check_far_plane(meshes)
	await _check_floating(meshes)
	_merge_repeats()
	return {"findings": _findings, "stats": _stats}


## One finding per distinct message. Repeats go into data.also.
func _merge_repeats() -> void:
	var merged: Array[Dictionary] = []
	var index := {}
	for f in _findings:
		var key := "%s|%s|%s" % [f.probe, f.severity, f.message]
		if index.has(key):
			(merged[index[key]].data as Dictionary).get_or_add("also", []).append(f.node)
			continue
		index[key] = merged.size()
		merged.append(f)
	for f in merged:
		if f.data.has("also"):
			f.message += " The same applies to %d more nodes (data.also)." % f.data.also.size()
	_findings = merged


# --- 3D -----------------------------------------------------------------------

func _check_camera_roll() -> void:
	if _camera == null:
		return
	var forward := -_camera.global_basis.z.normalized()
	# Roll has no meaning when looking straight up or down.
	if absf(forward.y) > 0.99:
		return
	var roll := rad_to_deg(asin(clampf(_camera.global_basis.x.normalized().y, -1.0, 1.0)))
	if absf(roll) > MAX_CAMERA_ROLL_DEG:
		_add("camera_roll", "warning", _camera,
				"Camera is rolled %.1f°, so the horizon is tilted." % roll,
				{"roll_deg": snappedf(roll, 0.01)})


func _check_far_plane(meshes: Array[MeshInstance3D]) -> void:
	if _camera == null or _camera.projection != Camera3D.PROJECTION_PERSPECTIVE:
		return
	var origin := _camera.global_position
	var forward := -_camera.global_basis.z.normalized()
	var screen := _viewport.get_visible_rect()
	var cut: Array[String] = []
	var deepest := 0.0
	for mi in meshes:
		var box := mi.global_transform * mi.get_aabb()
		for i in 8:
			var p := box.get_endpoint(i)
			var depth := (p - origin).dot(forward)
			# Beyond the far plane but inside the side planes, so the cut shows.
			if depth > _camera.far and screen.has_point(_camera.unproject_position(p)):
				cut.append(_path(mi))
				deepest = maxf(deepest, depth)
				break
	if not cut.is_empty():
		_add("camera_far", "warning", _camera,
				"%d meshes reach past the camera's far plane (far = %.1f m, deepest point %.1f m) and are cut off." % [cut.size(), _camera.far, deepest],
				{"far": _camera.far, "deepest": snappedf(deepest, 0.1), "meshes": cut.slice(0, 10)})


func _check_materials(gi: GeometryInstance3D) -> void:
	for mat in _materials_of(gi):
		var m := mat as BaseMaterial3D
		if m == null:
			continue
		var c := m.albedo_color
		if c.r > 1.001 or c.g > 1.001 or c.b > 1.001 or c.r < 0.0 or c.g < 0.0 or c.b < 0.0:
			_add("material_range", "warning", gi,
					"Albedo color %s is outside 0–1, which blows the surface out. 0–255 values were probably used where 0–1 was expected." % _fmt_color(c),
					{"material": _res_name(m), "albedo": [c.r, c.g, c.b]}, _rect_3d(gi))
		if m.roughness < 0.0 or m.roughness > 1.0 or m.metallic < 0.0 or m.metallic > 1.0:
			_add("material_range", "warning", gi,
					"Roughness %.2f or metallic %.2f is outside 0–1." % [m.roughness, m.metallic],
					{"material": _res_name(m)}, _rect_3d(gi))
		if m.emission_enabled and m.emission_energy_multiplier > EMISSION_INFO_MAX:
			_add("material_range", "info", gi,
					"Emission energy is %.1f." % m.emission_energy_multiplier,
					{"material": _res_name(m)}, _rect_3d(gi))
		if m.albedo_texture and m.transparency == BaseMaterial3D.TRANSPARENCY_DISABLED \
				and _has_transparent_pixels(m.albedo_texture):
			_add("alpha_unused", "warning", gi,
					"Albedo texture %s has transparent pixels, but the material draws it opaque (transparency is disabled)." % _res_name(m.albedo_texture),
					{"material": _res_name(m), "texture": _res_name(m.albedo_texture)}, _rect_3d(gi))


func _check_scale(gi: GeometryInstance3D) -> void:
	if gi.global_basis.determinant() < 0.0 and not gi is Label3D:
		_add("scale", "info", gi, "Negative scale mirrors this object.",
				{"scale": _vec(gi.global_basis.get_scale())}, _rect_3d(gi))


func _check_label3d(label: Label3D) -> void:
	if _camera == null or label.billboard != BaseMaterial3D.BILLBOARD_DISABLED or label.text.is_empty():
		return
	if not _camera.is_position_in_frustum(label.global_position):
		return
	# A Label3D reads correctly from its +Z side.
	var facing_away := label.global_basis.z.dot(_camera.global_position - label.global_position) < 0.0
	var mirrored_scale := label.global_basis.determinant() < 0.0
	if facing_away and not label.double_sided:
		_add("label3d_facing", "info", label,
				"Label \"%s\" faces away from the camera and isn't drawn from this side." % label.text)
	elif facing_away != mirrored_scale:
		_add("label3d_facing", "warning", label,
				"Label \"%s\" reads mirrored: %s." % [label.text,
						"it faces away from the camera and is drawn double-sided" if facing_away else "its scale is negative"],
				{}, _rect_3d(label))


func _check_light(light: Light3D) -> void:
	if light.shadow_enabled and light.shadow_bias <= 0.0 and light.shadow_normal_bias <= 0.0:
		_add("shadow_bias", "warning", light,
				"Shadow bias and normal bias are both 0, which causes shadow acne (striped self-shadowing).",
				{"shadow_bias": light.shadow_bias, "shadow_normal_bias": light.shadow_normal_bias},
				null, "lighting")


func _check_floating(meshes: Array[MeshInstance3D]) -> void:
	_stats["floating"] = {"meshes": meshes.size(), "rays": 0, "hits": 0, "candidates": []}
	if meshes.is_empty():
		return
	# World-space collision copies of every mesh. Baking the transform into
	# the faces avoids physics trouble with scaled bodies.
	var holder := Node3D.new()
	holder.name = "__gdh_probe_bodies"
	_tree.root.add_child(holder)
	var body_of := {}
	for mi in meshes:
		var faces := mi.mesh.get_faces()
		if faces.is_empty():
			continue
		var xf := mi.global_transform
		for i in faces.size():
			faces[i] = xf * faces[i]
		var shape := ConcavePolygonShape3D.new()
		shape.set_faces(faces)
		shape.backface_collision = true
		var body := StaticBody3D.new()
		body.collision_layer = PROBE_LAYER
		body.collision_mask = 0
		var cs := CollisionShape3D.new()
		cs.shape = shape
		body.add_child(cs)
		holder.add_child(body)
		body_of[mi] = body.get_rid()
	# Bodies join the physics space on the next physics step.
	await _tree.physics_frame
	await _tree.physics_frame
	var space := _viewport.find_world_3d().direct_space_state
	var mesh_of := {}
	for mi in body_of:
		mesh_of[body_of[mi]] = mi

	# Rule 1: a mesh hovers over a surface with nothing else within reach.
	for mi: MeshInstance3D in body_of:
		var below := _gap_below(space, [mi], body_of, mesh_of)
		if below.gap <= FLOAT_MIN_GAP or below.gap > FLOAT_MAX_GAP:
			continue
		var near := _near_meshes(space, mi, body_of[mi], mesh_of)
		_stats.floating.candidates.append({"node": _path(mi), "gap": snappedf(below.gap, 0.001), "near": near.slice(0, 5)})
		if near.is_empty():
			_add("floating", "warning", mi,
					"Floats %.2f m above %s, with nothing else within %d cm." % [below.gap, below.name, roundi(FLOAT_NEAR * 100)],
					{"gap": snappedf(below.gap, 0.001), "below": below.name}, _rect_3d(mi))

	# Rule 2: matching siblings (Barrel1..Barrel4) rest on a surface, this one hovers.
	var groups := {}
	for mi: MeshInstance3D in body_of:
		var unit: Node = mi
		while unit != _root and unit.get_parent() != null:
			var parent := unit.get_parent()
			var key := "%s|%s" % [parent.get_instance_id(), _unit_signature(unit, body_of)]
			var members: Array = groups.get_or_add(key, [])
			if unit not in members:
				members.append(unit)
			unit = parent
			if not unit is Node3D:
				break
	for key in groups:
		var units: Array = groups[key]
		if units.size() < FLOAT_MIN_SIBLINGS:
			continue
		var gaps := {}
		var resting := 0
		for unit in units:
			var parts := _unit_meshes(unit, body_of)
			gaps[unit] = _gap_below(space, _lowest_parts(parts), body_of, mesh_of, parts)
			if gaps[unit].gap <= FLOAT_MIN_GAP:
				resting += 1
		if resting < 2 or resting < units.size() * FLOAT_SIBLING_SHARE:
			continue
		for unit in units:
			var below: Dictionary = gaps[unit]
			if below.gap > FLOAT_MIN_GAP and below.gap <= FLOAT_MAX_GAP:
				_add("floating", "warning", unit,
						"Floats %.2f m above %s, while %d of %d matching siblings rest on a surface." % [below.gap, below.name, resting, units.size()],
						{"gap": snappedf(below.gap, 0.001), "below": below.name}, _rect_unit(unit, body_of))
	holder.queue_free()


## Smallest drop from the bottom of `parts` to the first surface below, ignoring
## `ignore` (defaults to `parts`). Returns {gap, name}; gap is INF when nothing
## is below within reach.
func _gap_below(space: PhysicsDirectSpaceState3D, parts: Array, body_of: Dictionary,
		mesh_of: Dictionary, ignore: Array = []) -> Dictionary:
	var exclude: Array[RID] = []
	for part in (ignore if not ignore.is_empty() else parts):
		exclude.append(body_of[part])
	var best := {"gap": INF, "name": "the surface below"}
	for mi: MeshInstance3D in parts:
		var box := mi.global_transform * mi.get_aabb()
		var bottom := box.position.y
		var lift := clampf(box.size.y * 0.5, 0.005, 0.05)
		for offset in [Vector2(0.5, 0.5), Vector2(0.25, 0.25), Vector2(0.75, 0.25), Vector2(0.25, 0.75), Vector2(0.75, 0.75)]:
			var from := Vector3(box.position.x + box.size.x * offset.x, bottom + lift, box.position.z + box.size.z * offset.y)
			var query := PhysicsRayQueryParameters3D.create(from, from + Vector3.DOWN * (FLOAT_MAX_GAP + lift + 0.01), PROBE_LAYER, exclude)
			query.collide_with_areas = false
			_stats.floating.rays += 1
			var hit := space.intersect_ray(query)
			if hit.is_empty():
				continue
			_stats.floating.hits += 1
			var gap: float = bottom - (hit.position as Vector3).y
			if gap < best.gap:
				best = {"gap": gap, "name": _path(mesh_of[hit.rid]) if mesh_of.has(hit.rid) else "the surface below"}
	return best


## Paths of meshes within FLOAT_NEAR of this mesh's convex hull.
func _near_meshes(space: PhysicsDirectSpaceState3D, mi: MeshInstance3D, own: RID, mesh_of: Dictionary) -> Array:
	var hull := mi.mesh.create_convex_shape(true, false)
	if hull == null:
		return []
	# Grow the hull by FLOAT_NEAR along each axis (a Minkowski sum with an
	# octahedron) instead of relying on the query margin, which not every
	# physics engine honors.
	var grown := PackedVector3Array()
	for p in hull.points:
		var q := mi.global_transform * p
		for axis in [Vector3.RIGHT, Vector3.LEFT, Vector3.UP, Vector3.DOWN, Vector3.FORWARD, Vector3.BACK]:
			grown.append(q + axis * FLOAT_NEAR)
	hull.points = grown
	var params := PhysicsShapeQueryParameters3D.new()
	params.shape = hull
	params.collision_mask = PROBE_LAYER
	params.collide_with_areas = false
	var exclude: Array[RID] = [own]
	params.exclude = exclude
	var out := []
	for hit in space.intersect_shape(params, 8):
		if mesh_of.has(hit.rid):
			out.append(_path(mesh_of[hit.rid]))
	return out


## A unit is a mesh, or a Node3D holding meshes. Units match when they hold the
## same meshes (by relative path, type and size).
func _unit_signature(unit: Node, body_of: Dictionary) -> String:
	var parts := PackedStringArray()
	for mi in _unit_meshes(unit, body_of):
		var size := mi.get_aabb().size * mi.global_basis.get_scale()
		parts.append("%s:%s:%s" % [unit.get_path_to(mi), mi.mesh.get_class(), size.snapped(Vector3.ONE * 0.01)])
	parts.sort()
	return unit.get_class() + "[" + ";".join(parts) + "]"


func _unit_meshes(unit: Node, body_of: Dictionary) -> Array[MeshInstance3D]:
	var out: Array[MeshInstance3D] = []
	if unit is MeshInstance3D and body_of.has(unit):
		out.append(unit)
	for child in unit.get_children():
		out.append_array(_unit_meshes(child, body_of))
	return out


## The parts whose bottom is within FLOAT_MIN_GAP of the unit's lowest point.
func _lowest_parts(parts: Array[MeshInstance3D]) -> Array[MeshInstance3D]:
	var lowest := INF
	for mi in parts:
		lowest = minf(lowest, (mi.global_transform * mi.get_aabb()).position.y)
	var out: Array[MeshInstance3D] = []
	for mi in parts:
		if (mi.global_transform * mi.get_aabb()).position.y <= lowest + FLOAT_MIN_GAP:
			out.append(mi)
	return out


func _rect_unit(unit: Node, body_of: Dictionary) -> Variant:
	var rect: Variant = null
	for mi in _unit_meshes(unit, body_of):
		var r: Variant = _rect_3d(mi)
		if r is Rect2:
			rect = r if rect == null else (rect as Rect2).merge(r)
	return rect


# --- 2D and UI ----------------------------------------------------------------

## Describes how a textured CanvasItem draws its texture, or {} if it doesn't.
func _texture_entry(item: CanvasItem) -> Dictionary:
	var tex: Texture2D = null
	var magnification := 1.0
	var canvas_scale := item.get_global_transform_with_canvas().get_scale().abs()
	var scale := minf(canvas_scale.x, canvas_scale.y)
	if item is Sprite2D:
		tex = (item as Sprite2D).texture
		magnification = scale
	elif item is AnimatedSprite2D:
		var anim := item as AnimatedSprite2D
		if anim.sprite_frames and anim.sprite_frames.has_animation(anim.animation) \
				and anim.sprite_frames.get_frame_count(anim.animation) > 0:
			tex = anim.sprite_frames.get_frame_texture(anim.animation, anim.frame)
		magnification = scale
	elif item is TextureRect:
		var rect := item as TextureRect
		tex = rect.texture
		if tex:
			var ratio := rect.size / Vector2(tex.get_size())
			match rect.stretch_mode:
				TextureRect.STRETCH_SCALE:
					magnification = minf(ratio.x, ratio.y) * scale
				TextureRect.STRETCH_KEEP_ASPECT, TextureRect.STRETCH_KEEP_ASPECT_CENTERED:
					magnification = minf(ratio.x, ratio.y) * scale
				TextureRect.STRETCH_KEEP_ASPECT_COVERED:
					magnification = maxf(ratio.x, ratio.y) * scale
				_:
					magnification = scale
	if tex == null:
		return {}
	return {"item": item, "texture": tex, "filter": _effective_filter(item), "magnification": magnification}


func _check_texture_filters(entries: Array[Dictionary]) -> void:
	var any_nearest := entries.any(func(e): return e.filter in NEAREST_FILTERS)
	for e in entries:
		if e.filter in LINEAR_FILTERS and e.magnification >= BLUR_MAGNIFICATION and _is_hard_edged(e.texture):
			var item: CanvasItem = e.item
			var severity := "warning" if any_nearest else "info"
			var why := " Other textures in this scene use nearest filtering." if any_nearest else ""
			_add("texture_filter", severity, item,
					"Texture %s is drawn at %.1f× with linear filtering, so it looks blurry.%s" % [_res_name(e.texture), e.magnification, why],
					{"magnification": snappedf(e.magnification, 0.01), "filter": e.filter}, _rect_2d(item))


func _check_region(sprite: Sprite2D) -> void:
	if not sprite.region_enabled or sprite.texture == null:
		return
	var bounds := Rect2(Vector2.ZERO, sprite.texture.get_size())
	if bounds.encloses(sprite.region_rect):
		return
	if _effective_repeat(sprite) == CanvasItem.TEXTURE_REPEAT_DISABLED:
		_add("region_smear", "warning", sprite,
				"Region %s reaches outside the %dx%d texture while texture repeat is disabled, so the edge pixels smear." % [sprite.region_rect, bounds.size.x, bounds.size.y],
				{"region": [sprite.region_rect.position.x, sprite.region_rect.position.y, sprite.region_rect.size.x, sprite.region_rect.size.y]},
				_rect_2d(sprite))


func _check_text(node: Node) -> void:
	var text := ""
	if node is Label:
		text = (node as Label).text
	elif node is Button:
		text = (node as Button).text
	elif node is RichTextLabel:
		text = (node as RichTextLabel).get_parsed_text()
	elif node is Label3D:
		text = (node as Label3D).text
	text = text.strip_edges()
	if text.is_empty():
		return
	var rect: Variant = _rect_3d(node) if node is Node3D else _rect_2d(node)
	if _key_regex.search(text) and node.tr(text) == text:
		_add("text_key", "warning", node,
				"Shows \"%s\", which looks like a translation key with no translation loaded." % text, {}, rect)
	var marker := _placeholder_regex.search(text)
	if marker:
		_add("text_placeholder", "warning", node,
				"Text contains the placeholder marker \"%s\"." % marker.get_string(), {"text": text.left(80)}, rect)


func _check_sibling_offsets(nodes: Array[Node]) -> void:
	for parent in nodes:
		if not parent is Control:
			continue
		# Group grandchildren by name: Slot01/Icon, Slot02/Icon, ...
		var groups := {}
		for child in parent.get_children():
			if not child is Control or not (child as Control).is_visible_in_tree():
				continue
			for grandchild in child.get_children():
				if grandchild is Control and (grandchild as Control).is_visible_in_tree():
					var g := grandchild as Control
					var center := g.position + g.size * 0.5 - (child as Control).size * 0.5
					groups.get_or_add(g.name, []).append({"node": g, "center": center})
		for name in groups:
			var members: Array = groups[name]
			if members.size() < OFFSET_MIN_GROUP:
				continue
			var xs := members.map(func(m): return m.center.x)
			var ys := members.map(func(m): return m.center.y)
			xs.sort()
			ys.sort()
			var median := Vector2(xs[xs.size() / 2], ys[ys.size() / 2])
			var agree := members.filter(func(m): return m.center.distance_to(median) <= OFFSET_AGREE_PX).size()
			if agree < members.size() * OFFSET_AGREE_SHARE:
				continue
			for m in members:
				var off: Vector2 = m.center - median
				if off.length() > OFFSET_OUTLIER_PX:
					_add("sibling_offset", "warning", m.node,
							"Sits %s px off from where the matching node sits in %d of %d sibling containers." % [_vec(off), agree, members.size()],
							{"offset": _vec(off)}, _rect_2d(m.node))


# --- Helpers ------------------------------------------------------------------

func _collect(node: Node, out: Array[Node]) -> void:
	out.append(node)
	for child in node.get_children():
		_collect(child, out)


func _materials_of(gi: GeometryInstance3D) -> Array[Material]:
	var mats: Array[Material] = []
	if gi.material_overlay:
		mats.append(gi.material_overlay)
	if gi.material_override:
		mats.append(gi.material_override)
		return mats
	if gi is MeshInstance3D and (gi as MeshInstance3D).mesh:
		var mi := gi as MeshInstance3D
		for s in mi.mesh.get_surface_count():
			var m := mi.get_surface_override_material(s)
			if m == null:
				m = mi.mesh.surface_get_material(s)
			if m:
				mats.append(m)
	return mats


func _has_transparent_pixels(tex: Texture2D) -> bool:
	var key := tex.get_instance_id()
	if not _alpha_cache.has(key):
		var img := tex.get_image()
		var result := false
		if img:
			if img.is_compressed():
				img.decompress()
			result = img.detect_alpha() != Image.ALPHA_NONE
		_alpha_cache[key] = result
	return _alpha_cache[key]


func _is_hard_edged(tex: Texture2D) -> bool:
	var key := tex.get_instance_id()
	if _edge_cache.has(key):
		return _edge_cache[key]
	var img := tex.get_image()
	var result := false
	if img and img.get_width() > 1 and img.get_height() > 1:
		if img.is_compressed():
			img.decompress()
		# Sample at most about 128x128 positions, each against its right and lower neighbor.
		var step := maxi(1, maxi(img.get_width(), img.get_height()) / 128)
		var pairs := 0
		var hard := 0
		for y in range(0, img.get_height() - 1, step):
			for x in range(0, img.get_width() - 1, step):
				var c := img.get_pixel(x, y)
				for n in [img.get_pixel(x + 1, y), img.get_pixel(x, y + 1)]:
					if c.a < 0.5 and n.a < 0.5:
						continue
					pairs += 1
					if maxf(maxf(absf(c.r - n.r), absf(c.g - n.g)), maxf(absf(c.b - n.b), absf(c.a - n.a))) > HARD_EDGE_DIFF:
						hard += 1
		result = pairs > 0 and float(hard) / pairs >= HARD_EDGE_SHARE
	_edge_cache[key] = result
	return result


func _effective_filter(item: CanvasItem) -> int:
	var node: Node = item
	while node is CanvasItem:
		var f := (node as CanvasItem).texture_filter
		if f != CanvasItem.TEXTURE_FILTER_PARENT_NODE:
			return f
		node = node.get_parent()
	match item.get_viewport().canvas_item_default_texture_filter:
		Viewport.DEFAULT_CANVAS_ITEM_TEXTURE_FILTER_NEAREST:
			return CanvasItem.TEXTURE_FILTER_NEAREST
		Viewport.DEFAULT_CANVAS_ITEM_TEXTURE_FILTER_NEAREST_WITH_MIPMAPS:
			return CanvasItem.TEXTURE_FILTER_NEAREST_WITH_MIPMAPS
		Viewport.DEFAULT_CANVAS_ITEM_TEXTURE_FILTER_LINEAR_WITH_MIPMAPS:
			return CanvasItem.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS
	return CanvasItem.TEXTURE_FILTER_LINEAR


func _effective_repeat(item: CanvasItem) -> int:
	var node: Node = item
	while node is CanvasItem:
		var r := (node as CanvasItem).texture_repeat
		if r != CanvasItem.TEXTURE_REPEAT_PARENT_NODE:
			return r
		node = node.get_parent()
	match item.get_viewport().canvas_item_default_texture_repeat:
		Viewport.DEFAULT_CANVAS_ITEM_TEXTURE_REPEAT_ENABLED:
			return CanvasItem.TEXTURE_REPEAT_ENABLED
		Viewport.DEFAULT_CANVAS_ITEM_TEXTURE_REPEAT_MIRROR:
			return CanvasItem.TEXTURE_REPEAT_MIRROR
	return CanvasItem.TEXTURE_REPEAT_DISABLED


func _rect_3d(node: Node) -> Variant:
	if _camera == null or not node is VisualInstance3D:
		return null
	var vi := node as VisualInstance3D
	var box := vi.global_transform * vi.get_aabb()
	var rect := Rect2()
	for i in 8:
		var p := box.get_endpoint(i)
		if _camera.is_position_behind(p):
			return null
		var s := _camera.unproject_position(p)
		rect = Rect2(s, Vector2.ZERO) if i == 0 else rect.expand(s)
	rect = rect.intersection(_viewport.get_visible_rect())
	return rect if rect.has_area() else null


func _rect_2d(item: CanvasItem) -> Variant:
	var local: Rect2
	if item is Control:
		local = Rect2(Vector2.ZERO, (item as Control).size)
	elif item.has_method("get_rect"):
		local = item.call("get_rect")
	else:
		return null
	var rect := (item.get_global_transform_with_canvas() * local).intersection(_viewport.get_visible_rect())
	return rect if rect.has_area() else null


func _add(probe: String, severity: String, node: Node, message: String,
		data := {}, rect: Variant = null, view := "normal") -> void:
	var screen_rect: Variant = null
	if rect is Rect2:
		screen_rect = [roundi(rect.position.x), roundi(rect.position.y), roundi(rect.size.x), roundi(rect.size.y)]
	_findings.append({
		"probe": probe,
		"severity": severity,
		"node": _path(node),
		"message": message,
		"data": data,
		"screen_rect": screen_rect,
		"view": view,
	})


func _path(node: Node) -> String:
	if node == _root:
		return "."
	if _root.is_ancestor_of(node):
		return str(_root.get_path_to(node))
	return str(node.get_path())


func _res_name(res: Resource) -> String:
	if res == null:
		return "<none>"
	if not res.resource_path.is_empty():
		return res.resource_path.get_file() if "::" not in res.resource_path else res.resource_path.get_slice("::", 1)
	return res.resource_name if not res.resource_name.is_empty() else res.get_class()


func _fmt_color(c: Color) -> String:
	return "(%.2f, %.2f, %.2f)" % [c.r, c.g, c.b]


func _vec(v) -> Array:
	if v is Vector2:
		return [snappedf(v.x, 0.01), snappedf(v.y, 0.01)]
	return [snappedf(v.x, 0.01), snappedf(v.y, 0.01), snappedf(v.z, 0.01)]
