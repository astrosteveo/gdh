extends RefCounted
## Where a character's points are inside solid geometry, frame by frame, for `gdh live clipping`.
##
##   var clip := Clip.new(get_tree(), "Player", 0xFFFFFFFF)   # clip.error says why when it can't
##   clip.sample(game_frame)                                    # after each frame
##   clip.result()                                              # {"points": n, "hits": [[frame, [hit, ...]], ...]}
##
## The points are each bone of every Skeleton3D under the node, and the vertices (at most MESH_POINTS of each) of
## every MeshInstance3D under it that isn't skinned. A skinned mesh's vertices move with its bones on the GPU, where
## gdh can't read them, so its bones stand for it. Each point is tested with the physics space's intersect_point
## against the collision shapes on `mask`, leaving out the collision bodies under the node itself (the character's
## own). intersect_point finds a point inside a solid shape: a box, a sphere, a capsule, a convex polygon, a height
## map; a concave trimesh has no inside, so it can't be found this way.

const Common := preload("common.gd")
const Screen := preload("screen.gd")

const MESH_POINTS := 200

var error := ""
var _tree: SceneTree
var _node: Node3D
var _points := []  # [name, Skeleton3D and bone index, or MeshInstance3D and local position]
var _exclude: Array[RID] = []
var _mask := 0xFFFFFFFF
var _hits := []


func _init(tree: SceneTree, path: String, mask: int) -> void:
	_tree = tree
	_mask = mask
	var node := Screen.node_at(tree, path)
	if node == null:
		error = "No node at %s." % path
		return
	if not node is Node3D:
		error = "%s (%s) isn't a Node3D: clipping follows 3D geometry." % [path, node.get_class()]
		return
	_node = node
	for child in [node] + node.find_children("*", "", true, false):
		if child is CollisionObject3D:
			_exclude.append((child as CollisionObject3D).get_rid())
		if child is Skeleton3D:
			var skeleton := child as Skeleton3D
			for i in skeleton.get_bone_count():
				_points.append([skeleton.get_bone_name(i), skeleton, i])
		elif child is MeshInstance3D and (child as MeshInstance3D).mesh != null and (child as MeshInstance3D).skin == null \
				and str((child as MeshInstance3D).skeleton).is_empty():
			var mesh := (child as MeshInstance3D).mesh
			var vertices := PackedVector3Array()
			for s in mesh.get_surface_count():
				vertices.append_array(mesh.surface_get_arrays(s)[Mesh.ARRAY_VERTEX])
			var step := maxi(1, ceili(vertices.size() / float(MESH_POINTS)))
			for v in range(0, vertices.size(), step):
				_points.append(["%s vertex %d" % [child.name, v], child, vertices[v]])
	if _points.is_empty():
		error = "%s has no Skeleton3D or mesh under it to test." % path


func _world(point: Array) -> Vector3:
	if point[1] is Skeleton3D:
		var skeleton := point[1] as Skeleton3D
		return skeleton.global_transform * skeleton.get_bone_global_pose(point[2]).origin
	return (point[1] as Node3D).global_transform * (point[2] as Vector3)


func sample(frame: int) -> void:
	if _node == null or not is_instance_valid(_node) or not _node.is_inside_tree():
		return
	var space := _node.get_world_3d().direct_space_state
	var camera := _node.get_viewport().get_camera_3d()
	var params := PhysicsPointQueryParameters3D.new()
	params.collision_mask = _mask
	params.exclude = _exclude
	params.collide_with_areas = false
	var found := []
	for point in _points:
		if not is_instance_valid(point[1]):
			continue
		var at := _world(point)
		params.position = at
		for hit in space.intersect_point(params, 4):
			var collider: Object = hit.get("collider")
			var entry := {"point": point[0], "collider": Screen.path_of(_tree, collider) if collider is Node else str(collider),
					"at": [snappedf(at.x, 0.001), snappedf(at.y, 0.001), snappedf(at.z, 0.001)]}
			if camera != null and not camera.is_position_behind(at):
				var screen := camera.unproject_position(at) * Common.shot_scale(_tree)
				entry.screen = [snappedf(screen.x, 0.1), snappedf(screen.y, 0.1)]
			found.append(entry)
	if not found.is_empty():
		_hits.append([frame, found])


func result() -> Dictionary:
	return {"points": _points.size(), "hits": _hits}
