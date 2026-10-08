extends RefCounted
## Where nodes are on screen, and finding them there, for the live bridge: `find`, a step's clicks on a node or on what
## shows a text, `tree --visible-only`, and a shot's framing (a node's box, a crop, a zoom) and --no-ui. Everything is
## in screenshot pixels (common.gd).

const Common := preload("common.gd")

## At most this many matches in a reply, and this many of the hidden ones.
const MAX_MATCHES := 50
const MAX_HIDDEN := 10


# --- Boxes ----------------------------------------------------------------------

## A node's box on screen in screenshot pixels: a Control's rect, a Sprite2D's (anything with get_rect), a 3D
## object's bounds seen through the camera; a Rect2 of no size for a point (another 2D node, or a 3D one); null when it
## has no place on screen (a plain Node, a node drawn in a SubViewport, a 3D point behind the camera).
static func box(tree: SceneTree, node: Node) -> Variant:
	if node is CanvasItem:
		var xf: Variant = _to_root(tree, node)
		if xf == null:
			return null
		var rect := Rect2()
		if node is Control:
			rect = Rect2(Vector2.ZERO, (node as Control).size)
		elif node.has_method("get_rect") and node.get_rect() is Rect2:
			rect = node.get_rect()
		return Common.to_shot(tree, (xf as Transform2D) * rect)
	if node is Node3D:
		var camera := (node as Node3D).get_viewport().get_camera_3d()
		if camera == null or node.get_viewport() != tree.root:
			return null
		var corners: Array[Vector3] = [(node as Node3D).global_position]
		if node is VisualInstance3D:
			var aabb: AABB = (node as Node3D).global_transform * (node as VisualInstance3D).get_aabb()
			corners.clear()
			for i in 8:
				corners.append(aabb.get_endpoint(i))
		var seen := PackedVector2Array()
		for corner in corners:
			if not camera.is_position_behind(corner):
				seen.append(camera.unproject_position(corner))
		if seen.is_empty():
			return null
		var rect := Rect2(seen[0], Vector2.ZERO)
		for p in seen:
			rect = rect.expand(p)
		return Common.to_shot(tree, rect)
	return null


## A canvas item's transform into the root viewport's coordinates: through the embedded windows it's in (a dialog), or
## null when it's drawn elsewhere (a SubViewport, a window of its own).
static func _to_root(tree: SceneTree, item: CanvasItem) -> Variant:
	var xf := item.get_global_transform_with_canvas()
	var viewport := item.get_viewport()
	while viewport != tree.root:
		var window := viewport as Window
		if window == null or not window.is_embedded() or window.get_parent() == null:
			return null
		xf = Transform2D(0.0, Vector2(window.position)) * window.get_final_transform() * xf
		viewport = window.get_parent().get_viewport()
	return xf


## The part of a node's box that shows: inside the screen and every clipping Control above it. Returns
## {"box": Rect2} when some of it shows, else {"why": "hidden", "transparent", "off screen" or "not on screen"}.
static func seen(tree: SceneTree, node: Node) -> Dictionary:
	var why := hidden_by(node)
	if not why.is_empty():
		return {"why": why}
	var found: Variant = box(tree, node)
	if found == null:
		return {"why": "not on screen"}
	var rect: Rect2 = found
	var size := Common.image_size(tree)
	var clip := Rect2(0, 0, size[0], size[1])
	var parent := node.get_parent()
	while parent != null and parent != tree.root:
		if parent is Control and (parent as Control).clip_contents:
			var parent_box: Variant = box(tree, parent)
			if parent_box != null:
				clip = clip.intersection(parent_box)
		parent = parent.get_parent()
	if rect.has_area():
		rect = rect.intersection(clip)
		if not rect.has_area():
			return {"why": "off screen"}
	elif not clip.has_point(rect.position):
		return {"why": "off screen"}
	return {"box": rect}


## Why a node can't be seen whatever the screen shows: "hidden" (it, or something above it, isn't visible: a
## CanvasItem, a Node3D, a CanvasLayer, a Window), "transparent" (modulated to nothing), or "" when it can be.
static func hidden_by(node: Node) -> String:
	var at := node
	while at != null:
		if (at is CanvasItem or at is Node3D or at is CanvasLayer or at is Window) and not at.visible:
			return "hidden"
		if at is CanvasItem and ((at as CanvasItem).modulate.a <= 0.0 or (at == node and (at as CanvasItem).self_modulate.a <= 0.0)):
			return "transparent"
		at = at.get_parent()
	return ""


## Whether a node itself shows (its own visible flag and alpha), for `tree --visible-only`.
static func shows(node: Node) -> bool:
	if (node is CanvasItem or node is Node3D or node is CanvasLayer or node is Window) and not node.visible:
		return false
	return not (node is CanvasItem and (node as CanvasItem).modulate.a <= 0.0)


static func to_array(rect: Rect2) -> Array:
	if not rect.has_area():
		return [snappedf(rect.position.x, 0.1), snappedf(rect.position.y, 0.1)]
	return [snappedf(rect.position.x, 0.1), snappedf(rect.position.y, 0.1), snappedf(rect.size.x, 0.1),
		snappedf(rect.size.y, 0.1)]


# --- Finding --------------------------------------------------------------------

## The text a node shows: a Label's, a Button's, a LineEdit's (its placeholder while it's empty), a RichTextLabel's
## without its BBCode, a Label3D's; translated as the node draws it. "" for none.
static func text_of(node: Node) -> String:
	var text := ""
	if node is RichTextLabel:
		text = (node as RichTextLabel).get_parsed_text()
	elif "text" in node and node.get("text") is String:
		text = node.get("text")
		if text.is_empty() and "placeholder_text" in node and node.get("placeholder_text") is String:
			text = node.get("placeholder_text")
	return node.tr(text) if not text.is_empty() else ""


## Nodes matching every filter given ({"text": substring, any case ("" for any text); "name": pattern with * and ?, any case; "class":
## a class, built in or a script's class_name, or one it extends}), in tree order, the current scene's and every
## autoload's, the nodes Godot makes inside its controls included. Returns {"matches": [...], "hidden": [...]}: the
## ones that show, each {path, class, text, screen, disabled?}, and the ones that don't, each with "why".
static func find(tree: SceneTree, filters: Dictionary, limit := MAX_MATCHES) -> Dictionary:
	var shown := []
	var unseen := []
	var more := 0
	for node in _walk(tree.root, tree):
		if not _matches(node, filters):
			continue
		var where := seen(tree, node)
		var entry := describe(tree, node)
		if where.has("box"):
			if shown.size() >= limit:
				more += 1
				continue
			entry.screen = to_array(where.box)
			shown.append(entry)
		elif unseen.size() < MAX_HIDDEN:
			entry.why = where.why
			unseen.append(entry)
	return {"matches": shown, "hidden": unseen, "more": more}


static func describe(tree: SceneTree, node: Node) -> Dictionary:
	var entry := {"path": path_of(tree, node), "class": node.get_class()}
	var text := text_of(node)
	if not text.is_empty():
		entry.text = text.left(80)
	if node is BaseButton and (node as BaseButton).disabled:
		entry.disabled = true
	return entry


## A node's path: from the current scene when it's in it (as `tree` and `eval` take it), else from the root (/root/...).
static func path_of(tree: SceneTree, node: Node) -> String:
	var scene := tree.current_scene
	if scene != null and (scene == node or scene.is_ancestor_of(node)):
		return str(scene.get_path_to(node))
	return str(node.get_path())


## The node at a path from the current scene, or from the root when it starts with /root.
static func node_at(tree: SceneTree, path: String) -> Node:
	if path.begins_with("/"):
		return tree.root.get_node_or_null(path)
	var scene := tree.current_scene
	return scene.get_node_or_null(path) if scene != null else null


static func _matches(node: Node, filters: Dictionary) -> bool:
	if filters.has("name") and not str(node.name).matchn(str(filters.name)):
		return false
	if filters.has("class") and not _is_class(node, str(filters["class"])):
		return false
	if filters.has("text"):
		var text := text_of(node)
		var wanted := str(filters.text).to_lower()
		if text.is_empty():
			return false
		if not wanted.is_empty() and not (text.to_lower().contains(wanted) or str(node.get("text")).to_lower().contains(wanted)):
			return false
	return true


static func _is_class(node: Node, wanted: String) -> bool:
	if node.is_class(wanted):
		return true
	var script: Script = node.get_script()
	while script != null:
		if script.get_global_name() == StringName(wanted):
			return true
		script = script.get_base_script()
	return false


## Every node under `node`, internal ones included (a dialog's buttons), but not gdh's own (the root's Gdh* nodes).
static func _walk(node: Node, tree: SceneTree) -> Array[Node]:
	var out: Array[Node] = []
	for child in node.get_children(true):
		if node == tree.root and str(child.name).begins_with("Gdh"):
			continue
		out.append(child)
		out.append_array(_walk(child, tree))
	return out


# --- Aiming ---------------------------------------------------------------------

## Where to click a target, {"node": PATH} or {"text": TEXT}: the centre of the part of it that shows. Returns
## {"at": [x, y], "path", "class", "text"?} or {"error": why, with the candidates}. A text picks the one node showing exactly
## that text (any case), else the one whose text holds it; none, or several, is an error.
static func aim(tree: SceneTree, target: Dictionary) -> Dictionary:
	if target.has("node"):
		var path := str(target.node)
		var node := node_at(tree, path)
		if node == null:
			return {"error": "No node at %s." % path}
		var where := seen(tree, node)
		if not where.has("box"):
			return {"error": "%s (%s) can't be clicked: it's %s." % [path, node.get_class(), where.why]}
		return _aimed(tree, node, where.box)
	if not target.has("text"):
		return {"error": "A target is {\"node\": PATH} or {\"text\": TEXT}."}
	var wanted := str(target.text)
	var found := find(tree, {"text": wanted}, 1000)
	var exact: Array = found.matches.filter(func(m: Dictionary) -> bool: return m.text.strip_edges().to_lower() == wanted.strip_edges().to_lower())
	var pick: Array = exact if not exact.is_empty() else found.matches
	if pick.size() == 1:
		var node := node_at(tree, pick[0].path)
		return _aimed(tree, node, seen(tree, node).box)
	if pick.is_empty():
		var lines := ["Nothing on screen shows %s." % JSON.stringify(wanted)]
		if not found.hidden.is_empty():
			lines.append("Matches that don't show: %s." % "; ".join(found.hidden.map(_line)))
		var texts: Array = find(tree, {"text": ""}, 20).matches
		if not texts.is_empty():
			lines.append("Texts on screen: %s." % "; ".join(texts.map(_line)))
		return {"error": " ".join(lines)}
	return {"error": "%s matches %d nodes that show: %s. Click one with its path (--click-node PATH)."
			% [JSON.stringify(wanted), pick.size(), "; ".join(pick.slice(0, 10).map(_line))]}


static func _aimed(tree: SceneTree, node: Node, rect: Rect2) -> Dictionary:
	var at := rect.get_center()
	var out := describe(tree, node)
	out.at = [snappedf(at.x, 0.1), snappedf(at.y, 0.1)]
	return out


static func _line(m: Dictionary) -> String:
	var parts := ["%s (%s)" % [m.path, m["class"]]]
	if m.has("text"):
		parts.append(JSON.stringify(m.text))
	if m.has("screen"):
		parts.append(str(m.screen))
	if m.has("why"):
		parts.append(m.why)
	return " ".join(parts)


# --- Shots ----------------------------------------------------------------------

## A shot's framing from its args: the part of the screen to keep, crop [x, y, w, h] or node PATH (what shows of it,
## as find gives it) grown by margin PX; zoom K (each pixel K x K, nearest neighbour); max_width W (scaled down to at
## most W wide). Returns {"edit": Callable that makes the saved image from a frame (or none), "report": {crop, size}},
## or {"error"}.
static func framing(tree: SceneTree, args: Dictionary) -> Dictionary:
	var size := Common.image_size(tree)
	var screen := Rect2i(0, 0, size[0], size[1])
	var region := screen
	if args.has("crop") and args.has("node"):
		return {"error": "Give a shot crop or node, not both."}
	if args.has("crop"):
		var c: Array = args.crop
		if c.size() != 4 or float(c[2]) <= 0 or float(c[3]) <= 0:
			return {"error": "crop takes [x, y, w, h], with a width and height above 0."}
		region = _pixels(Rect2(float(c[0]), float(c[1]), float(c[2]), float(c[3])))
	elif args.has("node"):
		var node := node_at(tree, str(args.node))
		if node == null:
			return {"error": "No node at %s." % args.node}
		var where := seen(tree, node)
		if not where.has("box"):
			return {"error": "%s (%s) doesn't show: it's %s." % [args.node, node.get_class(), where.why]}
		var rect: Rect2 = (where.box as Rect2).grow(float(args.get("margin", 0)))
		if not rect.has_area():
			return {"error": "%s is a point on screen: give it a margin." % args.node}
		region = _pixels(rect)
	region = region.intersection(screen)
	if not region.has_area():
		return {"error": "That part of the screen is off it: the screen is %dx%d." % [size[0], size[1]]}
	var zoom := int(args.get("zoom", 1))
	if zoom < 1 or zoom > 16:
		return {"error": "zoom takes a whole number from 1 to 16."}
	var max_width := int(args.get("max_width", 0))
	if max_width < 0:
		return {"error": "max_width takes a width in pixels."}
	var zoomed := region.size * zoom
	var final := zoomed
	if max_width > 0 and zoomed.x > max_width:
		final = Vector2i(max_width, maxi(1, roundi(zoomed.y * float(max_width) / zoomed.x)))
	var report := {"crop": [region.position.x, region.position.y, region.size.x, region.size.y], "size": [final.x, final.y]}
	if region == screen and final == screen.size:
		return {"edit": Callable(), "report": report}
	var edit := func(image: Image) -> Image:
		var out := image.get_region(region) if region != Rect2i(Vector2i.ZERO, image.get_size()) else image
		if zoom > 1:
			out.resize(zoomed.x, zoomed.y, Image.INTERPOLATE_NEAREST)
		if final != zoomed:
			out.resize(final.x, final.y, Image.INTERPOLATE_LANCZOS)
		return out
	return {"edit": edit, "report": report}


## The whole pixels a rect touches.
static func _pixels(rect: Rect2) -> Rect2i:
	var start := Vector2i(rect.position.floor())
	return Rect2i(start, Vector2i(rect.end.ceil()) - start)


## For --no-ui: hides every CanvasLayer drawn over the game that shows (layer 1 and up, not following the viewport:
## HUDs, menus, overlays), and returns them for show_layers.
static func hide_ui(tree: SceneTree) -> Array:
	var hidden := []
	for node in _walk(tree.root, tree):
		var layer := node as CanvasLayer
		if layer != null and layer.visible and layer.layer > 0 and not layer.follow_viewport_enabled:
			layer.visible = false
			hidden.append(layer)
	return hidden


static func show_layers(layers: Array) -> void:
	for layer in layers:
		if is_instance_valid(layer):
			layer.visible = true
