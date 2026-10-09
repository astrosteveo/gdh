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
## --locale: the locale the game's text is translated to (TranslationServer.set_locale), or "pseudo", Godot's
## pseudolocalization: every translated text 40% longer, with accents and in brackets, so text that won't fit shows
## up. Set before the scene loads; a game that sets its locale itself afterwards (from a saved setting) wins.
static func apply_locale(args: Dictionary) -> void:
	var locale := str(args.get("locale", ""))
	if locale.is_empty():
		return
	if locale != "pseudo":
		TranslationServer.set_locale(locale)
		return
	var settings := {"expansion_ratio": 0.4, "replace_with_accents": true, "double_vowels": false, "fake_bidi": false,
			"override": false, "prefix": "[", "suffix": "]", "skip_placeholders": true}
	for key in settings:
		ProjectSettings.set_setting("internationalization/pseudolocalization/" + key, settings[key])
	TranslationServer.pseudolocalization_enabled = true
	TranslationServer.reload_pseudolocalization()


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


## PNG saving off the main thread. Encoding a PNG is most of what saving a frame costs (about 27 ms at 1920x1080 and
## 105 ms at 3840x2160, against 2.5 and 10 ms to read the frame back from the GPU), so the frame is read back here, on
## the main thread, and encoded on the WorkerThreadPool while the game goes on. Every frame is its own file, so their
## order is in their names. Whoever starts saves waits for them (wait_saves) before it reports the files.
## At most max_pending_saves() frames wait to be encoded (each 33 MB at 3840x2160): one more waits for the oldest
## first. The tasks are low priority, so the engine's own work on the pool goes first.
static var _pending: Array[int] = []


static func max_pending_saves() -> int:
	return clampi(OS.get_processor_count() - 2, 2, 16)


## Reads the root viewport's frame back now and saves it as a PNG at path on a worker thread. edit, given, makes the
## image saved from the frame (a crop, a zoom: screen.gd's framing).
static func save_frame(tree: SceneTree, path: String, edit := Callable()) -> void:
	var image := tree.root.get_texture().get_image()
	if edit.is_valid():
		image = edit.call(image)
	while _pending.size() >= max_pending_saves():
		WorkerThreadPool.wait_for_task_completion(_pending.pop_front())
	_pending.append(WorkerThreadPool.add_task(func() -> void: image.save_png(path), false, "gdh: save a frame"))


## Waits until every frame save_frame started is written.
static func wait_saves() -> void:
	while not _pending.is_empty():
		WorkerThreadPool.wait_for_task_completion(_pending.pop_front())


## Renders `views` of the root viewport and saves each as <dir>/<prefix><view>.png, or where paths says ({view:
## path}), each made by edit (save_frame). Waits a few frames after switching modes so the new mode is drawn. No game
## time passes while the tree is paused. Returns {view: path}.
static func save_views(tree: SceneTree, views: Array, dir: String, prefix := "", edit := Callable(), paths := {}) -> Dictionary:
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
		var path: String = paths.get(view, dir.path_join("%s%s.png" % [prefix, view]))
		save_frame(tree, path, edit)
		saved[view] = path
	root.debug_draw = Viewport.DEBUG_DRAW_DISABLED
	wait_saves()
	return saved
