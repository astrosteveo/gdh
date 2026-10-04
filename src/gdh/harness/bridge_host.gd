extends SceneTree
## Runs gdh's editor bridge (addon/gdh_bridge/server.gd) in a headless editor of gdh's own. `gdh bridge start` runs
## `godot --headless --editor --path <project> --script <this file>`: the editor starts as usual, with this script as
## its main loop, so nothing is installed into the project. Once the editor's first scan is done it adds the bridge,
## which writes .godot/gdh_bridge.json; if the project has the addon enabled, its bridge is already running and this
## only gives it the idle timeout.
##
## Options in GDH_ARGS: --server <path to server.gd>, --idle-timeout <seconds>, --timeout <seconds to start>.

var _opts := {}


func _initialize() -> void:
	var parsed = JSON.parse_string(OS.get_environment("GDH_ARGS"))
	var argv: Array = parsed if parsed is Array else []
	for i in range(0, argv.size() - 1, 2):
		_opts[str(argv[i]).trim_prefix("--")] = str(argv[i + 1])
	_run.call_deferred()


func _run() -> void:
	if not await _editor_ready(float(_opts.get("timeout", "300"))):
		printerr("gdh bridge: the editor didn't finish its first scan")
		quit(4)
		return
	var idle := float(_opts.get("idle-timeout", "1800"))
	var running := get_nodes_in_group("gdh_bridge_server")
	if not running.is_empty():
		running[0].idle_timeout_s = idle
		return
	var script: Script = load(_opts.get("server", ""))
	if script == null or not script.can_instantiate():
		printerr("gdh bridge: the bridge's server.gd didn't load")
		quit(5)
		return
	var server: Node = script.new()
	server.idle_timeout_s = idle
	root.add_child(server)


## Waits for the editor to finish starting: its first scan of the project done, and nothing importing.
func _editor_ready(timeout_s: float) -> bool:
	var deadline := Time.get_ticks_msec() + int(timeout_s * 1000)
	var quiet := 0
	while Time.get_ticks_msec() < deadline:
		await process_frame
		var fs := EditorInterface.get_resource_filesystem()
		if fs == null or fs.is_scanning() or fs.is_importing():
			quiet = 0
			continue
		quiet += 1
		if quiet >= 30:
			return true
	return false
