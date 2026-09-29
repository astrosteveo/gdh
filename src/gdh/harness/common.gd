extends RefCounted
## Shared by capture.gd, live.gd and bridge.gd.

## Capture views and the viewport debug-draw mode behind each.
const VIEWS := {
	"normal": Viewport.DEBUG_DRAW_DISABLED,
	"unshaded": Viewport.DEBUG_DRAW_UNSHADED,
	"lighting": Viewport.DEBUG_DRAW_LIGHTING,
	"normals": Viewport.DEBUG_DRAW_NORMAL_BUFFER,
	"wireframe": Viewport.DEBUG_DRAW_WIREFRAME,
	"overdraw": Viewport.DEBUG_DRAW_OVERDRAW,
}


## Screenshot pixels per viewport unit. With stretch mode canvas_items the
## screenshot is at window size while the viewport's own coordinates are at the
## base size. Everything gdh reports or accepts is in screenshot pixels.
static func shot_scale(tree: SceneTree) -> Vector2:
	return Vector2(_image_size(tree)) / tree.root.get_visible_rect().size


## Viewport coordinates to screenshot pixels.
static func to_shot(tree: SceneTree, rect: Rect2) -> Rect2:
	var s := shot_scale(tree)
	return Rect2(rect.position * s, rect.size * s)


## Screenshot pixels to window coordinates, where injected mouse events land.
static func shot_to_window(tree: SceneTree, point: Vector2) -> Vector2:
	return tree.root.get_final_transform() * (point / shot_scale(tree))


## The screenshot size, which is the space every screen rect is in.
static func image_size(tree: SceneTree) -> Array:
	var size := _image_size(tree)
	return [size.x, size.y]


static var _size_frame := -1
static var _size := Vector2i.ONE


## Measured from a real frame: the texture's own get_size() reports 2x the saved
## image under stretch mode canvas_items. Cached per rendered frame.
static func _image_size(tree: SceneTree) -> Vector2i:
	if _size_frame != Engine.get_frames_drawn():
		_size = tree.root.get_texture().get_image().get_size()
		_size_frame = Engine.get_frames_drawn()
	return _size


## Parses "--key value" pairs after "--" on the command line. "modes" becomes
## an Array.
## The harness's own settings, which gdh passes in GDH_ARGS (a JSON array in
## the same --key value form) so the command line after `--` is all the game's.
static func harness_args() -> Dictionary:
	var parsed = JSON.parse_string(OS.get_environment("GDH_ARGS"))
	var argv := PackedStringArray()
	if parsed is Array:
		for item in parsed:
			argv.append(str(item))
	return parse_args(argv)


static func parse_args(argv: PackedStringArray) -> Dictionary:
	var out := {}
	var i := 0
	while i < argv.size():
		var key := argv[i].trim_prefix("--")
		var value := argv[i + 1] if i + 1 < argv.size() else ""
		out[key] = Array(value.split(",", false)) if key == "modes" else value
		i += 2
	return out


## Renders `views` of the root viewport and saves each as <dir>/<prefix><view>.png.
## Waits a few frames after switching modes so the new mode is drawn. No game
## time passes while the tree is paused. Returns {view: path}.
static func save_views(tree: SceneTree, views: Array, dir: String, prefix := "") -> Dictionary:
	var saved := {}
	var root := tree.root
	DirAccess.make_dir_recursive_absolute(dir)
	for view in views:
		if not VIEWS.has(view):
			push_error("Unknown view: %s" % view)
			continue
		root.debug_draw = VIEWS[view]
		for i in 3:
			await tree.process_frame
		await RenderingServer.frame_post_draw
		var path := dir.path_join("%s%s.png" % [prefix, view])
		root.get_texture().get_image().save_png(path)
		saved[view] = path
	root.debug_draw = Viewport.DEBUG_DRAW_DISABLED
	return saved
