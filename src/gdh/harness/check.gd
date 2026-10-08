extends SceneTree
## gdh bridge check with no editor: loads scripts from disk and writes the errors each raised. `godot --check-only`
## can't compile a script that names an autoload (GameState.score), since the game makes autoloads known only when it
## runs a SceneTree. Running as one, this script gets them as the game does: Godot creates each autoload (its _init
## runs) and adds it to the root; here they're taken off the root before the tree starts, so their _ready and
## _process never run. Global class names come from the project's class cache, as in the game.
##
## Run through `gdh bridge check`: godot --headless --path <project> --script <this file>, with
## GDH_ARGS ["--out", <file>, <res:// path>...]. The out file gets {path: [error]}.

const ErrorCollector := preload("errors.gd")


func _initialize() -> void:
	var parsed = JSON.parse_string(OS.get_environment("GDH_ARGS"))
	var argv: Array = parsed if parsed is Array else []
	if argv.size() < 2 or argv[0] != "--out":
		printerr("check.gd: GDH_ARGS needs --out <file> and the scripts")
		quit(2)
		return
	var autoloads := root.get_children()
	for node in autoloads:
		root.remove_child(node)
	var errors := ErrorCollector.new()
	OS.add_logger(errors)
	var out := {}
	for p in argv.slice(2):
		errors.drain()
		# Compiled again from disk: an autoload's script, or one an autoload uses, is already loaded.
		var res := ResourceLoader.load(p, "", ResourceLoader.CACHE_MODE_IGNORE)
		var errs: Array = errors.drain()
		# "Failed to load script ... Parse error" only repeats the parse errors before it.
		if errs.any(func(e): return e.type == "script"):
			errs = errs.filter(func(e): return not str(e.message).begins_with("Failed to load script"))
		if res == null and errs.is_empty():
			errs.append({"type": "error", "message": "it didn't load", "where": p, "count": 1})
		out[p] = errs
	OS.remove_logger(errors)
	var f := FileAccess.open(argv[1], FileAccess.WRITE)
	f.store_string(JSON.stringify(out))
	f.close()
	for node in autoloads:
		node.free()
	quit(0)
