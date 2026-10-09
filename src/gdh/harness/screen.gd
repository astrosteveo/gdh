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
## has no place on screen (a plain Node, a 3D point behind the camera, a node drawn somewhere gdh can't follow).
static func box(tree: SceneTree, node: Node) -> Variant:
	if not (node is CanvasItem or node is Node3D):
		return null
	var to_root: Variant = _to_root(tree, node.get_viewport())
	if to_root == null:
		return null
	if node is CanvasItem:
		var rect := Rect2()
		if node is Control:
			rect = Rect2(Vector2.ZERO, (node as Control).size)
		elif node.has_method("get_rect") and node.get_rect() is Rect2:
			rect = node.get_rect()
		return Common.to_shot(tree, (to_root as Transform2D) * (node as CanvasItem).get_global_transform_with_canvas() * rect)
	var camera := node.get_viewport().get_camera_3d()
	if camera == null:
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
	return Common.to_shot(tree, (to_root as Transform2D) * rect)


## From a viewport's coordinates to the root viewport's: through the windows embedded in it (a dialog) and the
## SubViewportContainers that show a SubViewport, or null when it's drawn elsewhere (a window of its own, a SubViewport
## shown some other way).
static func _to_root(tree: SceneTree, viewport: Viewport) -> Variant:
	var xf := Transform2D.IDENTITY
	while viewport != tree.root:
		var parent := viewport.get_parent()
		if viewport is Window and (viewport as Window).is_embedded() and parent != null:
			xf = Transform2D(0.0, Vector2((viewport as Window).position)) * viewport.get_final_transform() * xf
		elif viewport is SubViewport and parent is SubViewportContainer:
			var container := parent as SubViewportContainer
			var shrink := float(container.stretch_shrink) if container.stretch else 1.0
			xf = container.get_global_transform_with_canvas() * Transform2D(0.0, Vector2(shrink, shrink), 0.0, Vector2.ZERO) * xf
		else:
			return null
		viewport = parent.get_viewport()
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
		if parent is Control and ((parent as Control).clip_contents or parent is SubViewportContainer):
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


# --- What takes a click ----------------------------------------------------------

const MOUSE_FILTERS := ["Stop", "Pass", "Ignore"]


## The node that takes a click at a screenshot pixel, as Godot picks it: it's sent a pointer motion there (so the
## controls' hover follows), then each viewport's hovered control is read, into an embedded window under the pointer
## and through a SubViewportContainer into its SubViewport. A visible exclusive window (a modal dialog) takes every
## click outside itself. null when no control takes it: the click goes to the game's _unhandled_input.
static func taker_at(tree: SceneTree, at: Vector2) -> Node:
	var motion := InputEventMouseMotion.new()
	motion.position = Common.shot_to_window(tree, at)
	motion.global_position = motion.position
	tree.root.push_input(motion)
	return _taker(tree.root)


static func _taker(viewport: Viewport) -> Node:
	var windows := viewport.get_embedded_subwindows()
	for i in range(windows.size() - 1, -1, -1):
		var window := windows[i]
		if not window.visible:
			continue
		var inside := _taker(window)
		if inside != null:
			return inside
		var frame := Rect2(Vector2(window.position), Vector2(window.size)).grow_individual(0, window.get_theme_constant("title_height"), 0, 0)
		if frame.has_point(viewport.get_mouse_position()) or window.exclusive:
			return window
	var hovered := viewport.gui_get_hovered_control()
	if hovered is SubViewportContainer:
		for child in hovered.get_children():
			if child is SubViewport:
				var inner := _taker(child)
				if inner != null:
					return inner
	return hovered


## Why a click meant for `target` doesn't reach it when `taker` takes it ("" when it does). It does when the taker is
## the target, or a node inside it that passes the click up (no mouse_filter Stop on the way); for a target that lets
## clicks through (a Label, a mouse_filter Ignore), when the taker is under it or nothing takes it; and for a node
## that isn't a Control (a Sprite2D, a 3D object), when no control on the taker's way up stops the click (mouse_filter
## Stop), so the game's _unhandled_input gets it.
static func blocked(tree: SceneTree, target: Node, taker: Node) -> String:
	if taker == target:
		return ""
	if taker != null and target.is_ancestor_of(taker):
		var at := taker
		while at != target:
			if at is Control and (at as Control).mouse_filter == Control.MOUSE_FILTER_STOP:
				return "%s inside it takes the click first (mouse_filter Stop)" % _named(tree, at)
			at = at.get_parent()
		return ""
	if taker is Window:
		return "%s is over it and takes the click%s" % [_named(tree, taker), " (an exclusive window)" if (taker as Window).exclusive else ""]
	if not (target is Control):
		var stop := _stopper(taker)
		if stop == null:
			return ""
		return "%s takes the click, so the game's _unhandled_input never gets it (mouse_filter Stop)" % _named(tree, stop)
	if (target as Control).mouse_filter == Control.MOUSE_FILTER_IGNORE and (taker == null or taker.is_ancestor_of(target)):
		return ""
	if taker == null:
		return "nothing takes the click there"
	return "%s is over it and takes the click (mouse_filter %s)" % [_named(tree, taker), MOUSE_FILTERS[(taker as Control).mouse_filter]]


## The control that stops a click on its way up from the one that took it (mouse_filter Stop), or null.
static func _stopper(taker: Node) -> Control:
	var at := taker
	while at is Control:
		if (at as Control).mouse_filter == Control.MOUSE_FILTER_STOP:
			return at
		if (at as Control).top_level:
			break
		at = at.get_parent()
	return null


## What takes a click, for a reply: {path, class, mouse_filter?}.
static func took(tree: SceneTree, taker: Node) -> Dictionary:
	var out := {"path": path_of(tree, taker), "class": taker.get_class()}
	if taker is Control:
		out.mouse_filter = MOUSE_FILTERS[(taker as Control).mouse_filter]
	return out


static func _named(tree: SceneTree, node: Node) -> String:
	return "%s (%s)" % [path_of(tree, node), node.get_class()]


static func _line(m: Dictionary) -> String:
	var parts := ["%s (%s)" % [m.path, m["class"]]]
	if m.has("text"):
		parts.append(JSON.stringify(m.text))
	if m.has("screen"):
		parts.append(str(m.screen))
	if m.has("why"):
		parts.append(m.why)
	return " ".join(parts)


# --- Snapshots ------------------------------------------------------------------

## A text outline of the UI on screen for `gdh live snapshot`, as a browser test's accessibility snapshot is: the
## nodes that show something to read or use (a text, a button, a field, a slider, tabs, a list) and the named nodes
## that group them, from `from` (the current scene and the autoloads when null). Each is
## {name, class, path, children, text?, states?, value?, box?}; a group with only one child is left out, its child
## in its place. Hidden nodes and everything under them are left out, and so are the parts Godot makes inside a field
## or a slider, and scroll bars.
static func snapshot(tree: SceneTree, from: Node) -> Array:
	var roots: Array[Node] = []
	if from != null:
		roots.append(from)
	else:
		for child in tree.root.get_children():
			if not str(child.name).begins_with("Gdh"):
				roots.append(child)
	var out := []
	for node in roots:
		out.append_array(_snap(tree, node))
	return out


## The entries for a node: [its own] with what's under it as children, or what's under it when it's left out.
static func _snap(tree: SceneTree, node: Node) -> Array:
	if not shows(node) or node is ScrollBar:
		return []
	var readable := _readable(node)
	var children := []
	for child in node.get_children(not readable):
		children.append_array(_snap(tree, child))
	var where := seen(tree, node) if readable else {}
	if readable and where.has("box"):
		var entry := _snap_entry(tree, node)
		entry.box = to_array(where.box).map(func(v: float) -> int: return roundi(v))
		entry.children = children
		return [entry]
	if node is Window and node != tree.root:
		# A dialog or a window of the game's: always a group of its own, with its title.
		var window := {"name": str(node.name), "class": node.get_class(), "path": path_of(tree, node), "children": children}
		if not (node as Window).title.is_empty():
			window.text = node.tr((node as Window).title)
		return [window]
	var named := (node is CanvasLayer or node is Control) and not str(node.name).begins_with("@")
	if named and children.size() > 1 or node == tree.current_scene and not children.is_empty():
		return [{"name": str(node.name), "class": node.get_class(), "path": path_of(tree, node), "children": children}]
	return children


static func _readable(node: Node) -> bool:
	if node is BaseButton or node is LineEdit or node is TextEdit or node is Range or node is TabContainer \
			or node is ItemList:
		return true
	return (node is Control or node is Label3D) and not text_of(node).is_empty()


static func _snap_entry(tree: SceneTree, node: Node) -> Dictionary:
	var entry := {"name": str(node.name), "class": node.get_class(), "path": path_of(tree, node)}
	var text := text_of(node)
	if node is LineEdit or node is TextEdit:
		text = node.tr(node.get("text"))
		if text.is_empty() and not str(node.get("placeholder_text")).is_empty():
			entry.placeholder = node.tr(node.get("placeholder_text"))
	if not text.is_empty():
		entry.text = text.left(200)
	var states := []
	if node is BaseButton:
		var button := node as BaseButton
		if button.disabled:
			states.append("disabled")
		if button.toggle_mode and button.button_pressed:
			states.append("checked" if node is CheckBox or node is CheckButton else "pressed")
	if (node is LineEdit or node is TextEdit) and not node.get("editable"):
		states.append("read-only")
	if node is Control and (node as Control).has_focus():
		states.append("focused")
	if not states.is_empty():
		entry.states = states
	if node is Range:
		var range := node as Range
		entry.value = "%s/%s" % [_num(range.value), _num(range.max_value)]
	elif node is TabContainer:
		var tabs := node as TabContainer
		if tabs.current_tab >= 0:
			entry.value = "tab %s" % JSON.stringify(tabs.get_tab_title(tabs.current_tab))
	elif node is ItemList:
		var list := node as ItemList
		var picked := Array(list.get_selected_items()).map(func(i: int) -> String: return JSON.stringify(list.get_item_text(i)))
		entry.value = "%d items%s" % [list.item_count, ", selected " + ", ".join(picked) if not picked.is_empty() else ""]
	return entry


static func _num(v: float) -> String:
	return str(roundi(v)) if is_equal_approx(v, roundf(v)) else "%.2f" % v


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
## HUDs, menus, overlays), and returns them for show_layers, with the control that had the focus first. Hiding a
## control takes its focus and its hover away, which show_layers gives back, so the next frame draws as before.
static func hide_ui(tree: SceneTree) -> Array:
	var focused := tree.root.gui_get_focus_owner()
	# Focus a click gave is drawn without the focus ring: has_focus(true) is false for it.
	var hidden := [[focused, focused != null and not focused.has_focus(true)]]
	for node in _walk(tree.root, tree):
		var layer := node as CanvasLayer
		if layer != null and layer.visible and layer.layer > 0 and not layer.follow_viewport_enabled:
			layer.visible = false
			hidden.append(layer)
	return hidden


static func show_layers(tree: SceneTree, layers: Array) -> void:
	if layers.is_empty():
		return
	for layer in layers.slice(1):
		if is_instance_valid(layer):
			layer.visible = true
	var focused: Variant = layers[0][0]
	if is_instance_valid(focused) and (focused as Control).is_visible_in_tree():
		(focused as Control).grab_focus(layers[0][1])
	# The pointer hasn't moved: a motion where it is puts the hover back on what's under it.
	var motion := InputEventMouseMotion.new()
	motion.position = tree.root.get_mouse_position()
	motion.global_position = motion.position
	tree.root.push_input(motion)
