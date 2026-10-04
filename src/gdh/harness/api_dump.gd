extends SceneTree
## Writes the Godot editor's class reference (its help cache, editor_doc_cache-<version>.res) as JSON, for `gdh api`.
## Options in GDH_ARGS: --cache <the .res>, --out <the .json>.


func _initialize() -> void:
	var parsed = JSON.parse_string(OS.get_environment("GDH_ARGS"))
	var argv: Array = parsed if parsed is Array else []
	var opts := {}
	for i in range(0, argv.size() - 1, 2):
		opts[str(argv[i]).trim_prefix("--")] = str(argv[i + 1])
	var cache := ResourceLoader.load(opts.get("cache", ""))
	if cache == null or not cache.has_meta("classes"):
		printerr("gdh api: the help cache didn't load: ", opts.get("cache", ""))
		quit(2)
		return
	var f := FileAccess.open(opts.get("out", ""), FileAccess.WRITE)
	if f == null:
		printerr("gdh api: can't write ", opts.get("out", ""))
		quit(2)
		return
	f.store_string(JSON.stringify({"godot": Engine.get_version_info().string, "classes": cache.get_meta("classes")}))
	f.close()
	quit(0)
