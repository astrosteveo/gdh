extends RefCounted
## Large UI panels over the middle of the screen: a modal dialog, a popup, an overlay that hides the game.
##
##   var now: Array = Covered.sample(get_tree())
##   now: [{node, class, share, rect}] for each panel that draws over the screen's centre at this frame
##
## A recording samples it every few frames, and gdh flags a panel that's there for most of the samples (covered.py).
## Only what draws counts: a Panel or PanelContainer with a visible style, a ColorRect, a TextureRect or NinePatchRect
## with a texture, and an embedded Window (a popup or dialog), at least half opaque. The full-screen Control a HUD
## hangs its widgets from draws nothing, so it doesn't count, and neither do a HUD's small widgets: a panel must cover
## MIN_SHARE of the screen. A backdrop over the whole screen (MAX_SHARE and up: a menu's background, a fade) isn't a
## panel over the game. The outermost panel is reported, not the panels inside it.

const Common := preload("common.gd")

const MIN_SHARE := 0.2
const MAX_SHARE := 0.95
const MIN_ALPHA := 0.5


static func sample(tree: SceneTree) -> Array:
	var view := tree.root.get_visible_rect()
	var out := []
	for child in tree.root.get_children(true):
		_walk(child, tree, view, out)
	return out


static func _walk(node: Node, tree: SceneTree, view: Rect2, out: Array) -> void:
	if node is Window:
		var window := node as Window
		if window.visible and window.is_embedded():
			_consider(node, Rect2(window.position, window.size), 1.0, tree, view, out)
		return  # (its own contents are in its own viewport)
	if node is Viewport:
		return  # a SubViewport's contents aren't on the screen as they are
	if node is CanvasLayer and not (node as CanvasLayer).visible:
		return
	if node is CanvasItem and not (node as CanvasItem).is_visible_in_tree():
		return
	if node is Control and _draws(node as Control):
		var control := node as Control
		var rect := control.get_global_transform_with_canvas() * Rect2(Vector2.ZERO, control.size)
		if _consider(node, rect, _alpha(control), tree, view, out):
			return
	for child in node.get_children():
		_walk(child, tree, view, out)


## Adds node to out when it covers the centre and enough, but not all, of the screen. Returns whether it did.
static func _consider(node: Node, rect: Rect2, alpha: float, tree: SceneTree, view: Rect2, out: Array) -> bool:
	if alpha < MIN_ALPHA or not rect.has_point(view.get_center()):
		return false
	var share := rect.intersection(view).get_area() / maxf(view.get_area(), 1.0)
	if share < MIN_SHARE or share >= MAX_SHARE:
		return false
	var scene := tree.current_scene
	var path := str(scene.get_path_to(node)) if scene and scene.is_ancestor_of(node) else str(node.get_path())
	var shot := Common.to_shot(tree, rect)
	out.append({"node": path, "class": node.get_class(), "share": snappedf(share, 0.001),
			"rect": [snappedf(shot.position.x, 0.1), snappedf(shot.position.y, 0.1),
				snappedf(shot.size.x, 0.1), snappedf(shot.size.y, 0.1)]})
	return true


static func _draws(control: Control) -> bool:
	if control is ColorRect:
		return (control as ColorRect).color.a >= MIN_ALPHA
	if control is TextureRect:
		return (control as TextureRect).texture != null
	if control is NinePatchRect:
		return (control as NinePatchRect).texture != null
	if control is Panel or control is PanelContainer:
		var style := control.get_theme_stylebox("panel")
		if style == null or style is StyleBoxEmpty:
			return false
		if style is StyleBoxFlat:
			return (style as StyleBoxFlat).draw_center and (style as StyleBoxFlat).bg_color.a >= MIN_ALPHA
		return true
	return false


## How opaque a control draws: its own and its ancestors' modulate, and its own self_modulate.
static func _alpha(control: Control) -> float:
	var alpha := control.self_modulate.a
	var node: Node = control
	while node is CanvasItem:
		alpha *= (node as CanvasItem).modulate.a
		node = node.get_parent()
	return alpha

