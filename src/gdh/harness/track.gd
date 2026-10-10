extends RefCounted
## Where nodes are, frame by frame, during a step: the motion tools' record (gdh live step --trail, onion, filmstrip).
##
##   var track := Track.new(get_tree(), ["Ball", "Spinner"])   # null error when a path names no node
##   track.sample(game_frame)                                    # after each frame
##   track.result()                                              # {path: {class, rows, points, ...}}
##
## Each sample keeps the node's extent on screen at that frame (none while it's hidden), in screenshot pixels: the box round it and its visible
## descendants (Screen.box of each), since a Node2D or Node3D is often drawn by its children. And it keeps where the node is
## in its world: a Node2D's global position, a Node3D's, or for a Control the centre of its box (UI doesn't move with a
## camera). At the end the world positions are seen through the last frame's view, its 2D canvas transform or its 3D
## camera, so a trail drawn on the last frame shows the path through the world even when the camera followed the node.

const Common := preload("common.gd")
const Screen := preload("screen.gd")

## The most nodes under a tracked node whose boxes make up its extent.
const EXTENT_NODES := 256

var error := ""
var _tree: SceneTree
var _nodes := {}  # path: {node, class, rows: [[frame, box or null, world or null]], view: the last view seen}


func _init(tree: SceneTree, paths: Array) -> void:
	_tree = tree
	for path in paths:
		var node := Screen.node_at(tree, str(path))
		if node == null:
			error = "No node at %s." % path
			return
		if not (node is CanvasItem or node is Node3D):
			error = "%s (%s) has no place on screen: track a Node2D, a Control or a Node3D." % [path, node.get_class()]
			return
		_nodes[str(path)] = {"node": node, "class": node.get_class(), "control": node is Control, "rows": [],
				"view": null}


func sample(frame: int) -> void:
	for path in _nodes:
		var entry: Dictionary = _nodes[path]
		var node: Node = entry.node
		if not is_instance_valid(node) or not node.is_inside_tree():
			entry.rows.append([frame, null, null])
			continue
		var found: Variant = extent(_tree, node) if Screen.hidden_by(node).is_empty() else null
		var box: Variant = Screen.to_array(found) if found != null else null
		var world: Variant = null
		if node is Control:
			var own: Variant = Screen.box(_tree, node)
			if own != null:
				world = (own as Rect2).get_center()
		elif node is Node2D:
			world = (node as Node2D).global_position
			entry.view = _view_2d(node as Node2D)
		elif node is Node3D:
			world = (node as Node3D).global_position
			entry.view = _view_3d(node as Node3D)
		entry.rows.append([frame, box, world])


## The box round a node and its visible descendants on screen (at most EXTENT_NODES of them), or null when none of
## them has a place there. A node that is a point (a Node2D with nothing drawn under it) gives a box of no size.
static func extent(tree: SceneTree, node: Node) -> Variant:
	var out: Variant = null
	var queue: Array[Node] = [node]
	var seen := 0
	while not queue.is_empty() and seen < EXTENT_NODES:
		var at: Node = queue.pop_front()
		seen += 1
		if at != node and ((at is CanvasItem and not (at as CanvasItem).visible) or (at is Node3D and not (at as Node3D).visible)
				or at is CanvasLayer or at is Viewport):
			continue  # hidden, or drawn in a space of its own
		var found: Variant = Screen.box(tree, at)
		if found != null:
			out = found if out == null else (out as Rect2).merge(found)
		queue.append_array(at.get_children())
	return out


## The view a Node2D was last seen through: from its world's coordinates to screenshot pixels.
func _view_2d(node: Node2D) -> Variant:
	var to_root: Variant = Screen._to_root(_tree, node.get_viewport())
	if to_root == null:
		return null
	return (to_root as Transform2D) * node.get_global_transform_with_canvas() * node.global_transform.affine_inverse()


## The view a Node3D was last seen through: its viewport's camera, and the way to the root viewport.
func _view_3d(node: Node3D) -> Variant:
	var camera := node.get_viewport().get_camera_3d()
	var to_root: Variant = Screen._to_root(_tree, node.get_viewport())
	if camera == null or to_root == null:
		return null
	return {"camera": camera, "to_root": to_root}


## A world position seen through a view, in screenshot pixels; null when it can't be (behind the camera).
func _project(entry: Dictionary, world: Variant) -> Variant:
	if world == null:
		return null
	if entry.control:
		return _xy(world)
	var view: Variant = entry.view
	if view == null:
		return null
	if view is Transform2D:
		return _xy(Common.to_shot(_tree, Rect2((view as Transform2D) * (world as Vector2), Vector2.ZERO)).position)
	var camera: Camera3D = view.camera
	if not is_instance_valid(camera) or camera.is_position_behind(world):
		return null
	var point: Vector2 = (view.to_root as Transform2D) * camera.unproject_position(world)
	return _xy(Common.to_shot(_tree, Rect2(point, Vector2.ZERO)).position)


static func _xy(p: Vector2) -> Array:
	return [snappedf(p.x, 0.1), snappedf(p.y, 0.1)]


## {path: {"class", "rows": [[frame, box or null], ...], "points": [[frame, x, y] or [frame, null, null]]}}: each
## sample's box at its own frame, and the node's world position at each sample seen through the last view.
func result() -> Dictionary:
	var out := {}
	for path in _nodes:
		var entry: Dictionary = _nodes[path]
		var rows := []
		var points := []
		for row in entry.rows:
			rows.append([row[0], row[1]])
			var p: Variant = _project(entry, row[2])
			points.append([row[0], p[0], p[1]] if p != null else [row[0], null, null])
		out[path] = {"class": entry["class"], "rows": rows, "points": points}
	return out
