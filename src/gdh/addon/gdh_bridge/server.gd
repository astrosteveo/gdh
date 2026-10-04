@tool
extends Node
## gdh's bridge into a running Godot editor: an HTTP server on 127.0.0.1 that
## gdh (`gdh bridge ...`) and Claude Code's hooks call to work through the
## editor instead of behind its back.
##
## The addon's plugin.gd adds it to a person's editor. `gdh bridge start` adds
## it to a headless editor of gdh's own, with nothing installed in the project.
## It writes its port and a random token to res://.godot/gdh_bridge.json.
## Every request is a POST of a JSON object {"cmd": ...}; every reply carries
## "errors": the editor's errors and warnings since the previous reply, except a
## {"cmd": "status", "peek": true}, which counts them and leaves them.

const INFO_PATH := "res://.godot/gdh_bridge.json"
const FIRST_PORT := 47800
const PORT_TRIES := 20
const CLIENT_TIMEOUT_MS := 15000
const SCAN_TIMEOUT_MS := 300000
const SCENE_EXTENSIONS := ["tscn", "scn"]


## Collects the editor's errors and warnings, merged by message, and the
## printed output while an exec runs.
class ErrorLog extends Logger:
	const TYPES := ["error", "warning", "script", "shader"]
	var entries: Array[Dictionary] = []
	var index := {}
	var output: PackedStringArray = []
	var capturing := false
	var mutex := Mutex.new()

	func _log_error(function: String, file: String, line: int, code: String, rationale: String,
			_editor_notify: bool, error_type: int, _backtraces: Array[ScriptBacktrace]) -> void:
		var entry := {
			"type": TYPES[error_type] if error_type < TYPES.size() else str(error_type),
			"message": rationale if not rationale.is_empty() else code,
			"where": "%s:%d (%s)" % [file, line, function],
		}
		var key := "%s|%s|%s" % [entry.type, entry.message, entry.where]
		mutex.lock()
		if index.has(key):
			entries[index[key]].count += 1
		else:
			entry.count = 1
			index[key] = entries.size()
			entries.append(entry)
		mutex.unlock()

	func _log_message(message: String, _error: bool) -> void:
		mutex.lock()
		if capturing and output.size() < 500:
			output.append(message.trim_suffix("\n"))
		mutex.unlock()

	func drain() -> Array[Dictionary]:
		mutex.lock()
		var out := entries
		entries = []
		index = {}
		mutex.unlock()
		return out

	func count() -> int:
		mutex.lock()
		var n := 0
		for e in entries:
			if e.type != "warning":
				n += e.count
		mutex.unlock()
		return n

	## Puts entries taken with drain() back, for the next reply.
	func restore(kept: Array[Dictionary]) -> void:
		mutex.lock()
		for e in kept:
			index["%s|%s|%s" % [e.type, e.message, e.where]] = entries.size()
			entries.append(e)
		mutex.unlock()

	func capture(on: bool) -> PackedStringArray:
		mutex.lock()
		var out := output
		output = []
		capturing = on
		mutex.unlock()
		return out


## Seconds without a request before the editor quits (0: never). gdh sets it for its own headless editor.
var idle_timeout_s := 0.0

var _server := TCPServer.new()
var _port := 0
var _token := ""
var _log := ErrorLog.new()
var _clients: Array[Dictionary] = []
var _waiting: Array[Dictionary] = []
var _last_request_ms := 0


func _enter_tree() -> void:
	add_to_group("gdh_bridge_server")
	OS.add_logger(_log)
	for i in PORT_TRIES:
		if _server.listen(FIRST_PORT + i, "127.0.0.1") == OK:
			_port = FIRST_PORT + i
			break
	if _port == 0:
		push_error("gdh bridge: no free port in %d-%d." % [FIRST_PORT, FIRST_PORT + PORT_TRIES - 1])
		return
	_token = Crypto.new().generate_random_bytes(16).hex_encode()
	_last_request_ms = Time.get_ticks_msec()
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path("res://.godot"))
	var f := FileAccess.open(INFO_PATH, FileAccess.WRITE)
	if f:
		f.store_string(JSON.stringify({
			"port": _port,
			"token": _token,
			"pid": OS.get_process_id(),
			"godot": Engine.get_version_info().string,
			"headless": DisplayServer.get_name() == "headless",
		}))
		f.close()
	print("gdh bridge: listening on 127.0.0.1:%d" % _port)


func _exit_tree() -> void:
	OS.remove_logger(_log)
	_server.stop()
	for c in _clients:
		c.peer.disconnect_from_host()
	_clients.clear()
	_waiting.clear()
	if _port == 0:
		return
	# Remove the info file only if it's still ours: another editor may have started since.
	var f := FileAccess.open(INFO_PATH, FileAccess.READ)
	if f:
		var info = JSON.parse_string(f.get_as_text())
		f.close()
		if info is Dictionary and info.get("token") == _token:
			DirAccess.remove_absolute(ProjectSettings.globalize_path(INFO_PATH))


func _process(_delta: float) -> void:
	if not _server.is_listening():
		return
	while _server.is_connection_available():
		_clients.append({"peer": _server.take_connection(), "buf": PackedByteArray(), "t": Time.get_ticks_msec()})
	for c in _clients.duplicate():
		_read_client(c)
	for w in _waiting.duplicate():
		_check_waiting(w)
	if idle_timeout_s > 0 and _waiting.is_empty() and Time.get_ticks_msec() - _last_request_ms > idle_timeout_s * 1000:
		print("gdh bridge: no requests for %d s; quitting" % int(idle_timeout_s))
		get_tree().quit()


# --- HTTP ---------------------------------------------------------------------

func _read_client(c: Dictionary) -> void:
	var peer: StreamPeerTCP = c.peer
	peer.poll()
	if peer.get_status() != StreamPeerTCP.STATUS_CONNECTED or Time.get_ticks_msec() - c.t > CLIENT_TIMEOUT_MS:
		_clients.erase(c)
		return
	var buf: PackedByteArray = c.buf  # A copy: packed arrays are values, so it's stored back below.
	var n := peer.get_available_bytes()
	if n > 0:
		var got := peer.get_data(n)
		if got[0] == OK:
			buf.append_array(got[1])
			c.buf = buf
	var head_end := _find_head_end(buf)
	if head_end < 0:
		return
	var head := buf.slice(0, head_end).get_string_from_utf8()
	var length := 0
	var token := ""
	for line in head.split("\r\n"):
		var lower := line.to_lower()
		if lower.begins_with("content-length:"):
			length = line.substr(15).strip_edges().to_int()
		elif lower.begins_with("x-gdh-token:"):
			token = line.substr(12).strip_edges()
	var body_start := head_end + 4
	if buf.size() - body_start < length:
		return
	_clients.erase(c)
	if token != _token:
		_respond(peer, {"ok": false, "error": "bad token"}, 403)
		return
	_last_request_ms = Time.get_ticks_msec()
	var req = JSON.parse_string(buf.slice(body_start, body_start + length).get_string_from_utf8())
	if typeof(req) != TYPE_DICTIONARY:
		_respond(peer, {"ok": false, "error": "the body must be a JSON object"}, 400)
		return
	_handle(peer, req)


func _find_head_end(buf: PackedByteArray) -> int:
	for i in range(0, buf.size() - 3):
		if buf[i] == 13 and buf[i + 1] == 10 and buf[i + 2] == 13 and buf[i + 3] == 10:
			return i
	return -1


func _respond(peer: StreamPeerTCP, data: Dictionary, code := 200, drain := true) -> void:
	if drain:
		data["errors"] = _log.drain()
	var body := JSON.stringify(data).to_utf8_buffer()
	var head := "HTTP/1.1 %d OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % [code, body.size()]
	peer.put_data(head.to_utf8_buffer())
	peer.put_data(body)
	peer.disconnect_from_host()


# --- Commands -----------------------------------------------------------------

func _handle(peer: StreamPeerTCP, req: Dictionary) -> void:
	var paths: Array = req.get("paths", [])
	match str(req.get("cmd", "")):
		"status":
			if req.get("peek", false):
				# A look that leaves the errors for the next command (Claude Code's status band polls this way).
				var status := _status()
				status["pending_errors"] = _log.count()
				_respond(peer, status, 200, false)
			else:
				_respond(peer, _status())
		"errors":
			_respond(peer, {"ok": true})
		"scan":
			# Files known to have changed are updated directly, which is faster than a full scan.
			# Either way, the reply waits until the editor is idle.
			var fs := EditorInterface.get_resource_filesystem()
			if paths.is_empty():
				fs.scan()
			else:
				for p in paths:
					fs.update_file(p)
				fs.scan_sources()
			_waiting.append({"peer": peer, "t": Time.get_ticks_msec(), "frames": 0})
		"uid":
			_respond(peer, _uid(req.get("items", [])))
		"open":
			_respond(peer, _open(str(req.get("path", ""))))
		"save":
			_respond(peer, _save(paths))
		"reload":
			_respond(peer, _reload(paths, bool(req.get("force", false))))
		"resave":
			_respond(peer, _resave(paths))
		"check":
			_respond(peer, _check(paths))
		"play":
			var path := str(req.get("path", ""))
			if path.is_empty():
				EditorInterface.play_main_scene()
			elif path == "current":
				EditorInterface.play_current_scene()
			else:
				EditorInterface.play_custom_scene(path)
			_respond(peer, {"ok": true})
		"stop":
			EditorInterface.stop_playing_scene()
			_respond(peer, {"ok": true})
		"exec":
			_respond(peer, _exec(str(req.get("code", ""))))
		"quit":
			if DisplayServer.get_name() != "headless":
				_respond(peer, {"ok": false, "error": "this is a person's editor; only gdh's headless editor quits on request"})
				return
			_respond(peer, {"ok": true})
			get_tree().quit.call_deferred()
		var other:
			_respond(peer, {"ok": false, "error": "unknown cmd '%s'" % other}, 400)


func _check_waiting(w: Dictionary) -> void:
	var fs := EditorInterface.get_resource_filesystem()
	w.frames += 1
	var busy := fs.is_scanning() or fs.is_importing()
	var timed_out: bool = Time.get_ticks_msec() - w.t > SCAN_TIMEOUT_MS
	# Wait a few frames first, because a scan may not report as started yet.
	if (w.frames > 5 and not busy) or timed_out:
		_waiting.erase(w)
		var res := _status()
		if timed_out:
			res.ok = false
			res.error = "the scan was still running after %d s" % (SCAN_TIMEOUT_MS / 1000)
		_respond(w.peer, res)


func _status() -> Dictionary:
	var fs := EditorInterface.get_resource_filesystem()
	var root := EditorInterface.get_edited_scene_root()
	return {
		"ok": true,
		"godot": Engine.get_version_info().string,
		"project": ProjectSettings.globalize_path("res://"),
		"headless": DisplayServer.get_name() == "headless",
		"open_scenes": Array(EditorInterface.get_open_scenes()).filter(func(s): return s != ""),
		"unsaved_scenes": Array(EditorInterface.get_unsaved_scenes()).filter(func(s): return s != ""),
		"current_scene": root.scene_file_path if root else "",
		"playing": EditorInterface.is_playing_scene(),
		"playing_scene": EditorInterface.get_playing_scene(),
		"scanning": fs.is_scanning(),
		"importing": fs.is_importing(),
	}


func _uid(items: Array) -> Dictionary:
	var out := {}
	for item in items:
		var s := str(item)
		if s.begins_with("uid://"):
			var id := ResourceUID.text_to_id(s)
			out[s] = ResourceUID.get_id_path(id) if id != ResourceUID.INVALID_ID and ResourceUID.has_id(id) else null
		else:
			var id := ResourceLoader.get_resource_uid(s)
			out[s] = ResourceUID.id_to_text(id) if id != ResourceUID.INVALID_ID else null
	return {"ok": true, "uids": out}


func _open(path: String) -> Dictionary:
	if not FileAccess.file_exists(path):
		return {"ok": false, "error": "no such file: " + path}
	var ext := path.get_extension().to_lower()
	if ext in SCENE_EXTENSIONS:
		EditorInterface.open_scene_from_path(path)
	else:
		var res := load(path)
		if res is Script:
			EditorInterface.edit_script(res)
		elif res:
			EditorInterface.edit_resource(res)
	EditorInterface.select_file(path)
	return {"ok": true}


## Makes `path` the edited scene, runs `action`, then goes back to the scene that was current.
func _with_scene(path: String, action: Callable) -> Variant:
	var root := EditorInterface.get_edited_scene_root()
	var back: String = root.scene_file_path if root else ""
	EditorInterface.open_scene_from_path(path)
	var result = action.call()
	if back != "" and back != path:
		EditorInterface.open_scene_from_path(back)
	return result


func _save(paths: Array) -> Dictionary:
	if paths.is_empty():
		EditorInterface.save_all_scenes()
		return {"ok": true, "saved": "all"}
	var open := EditorInterface.get_open_scenes()
	var saved := []
	var failed := {}
	for p in paths:
		if not open.has(p):
			failed[p] = "not open in the editor"
			continue
		var err: Error = _with_scene(p, EditorInterface.save_scene)
		if err == OK:
			saved.append(p)
		else:
			failed[p] = error_string(err)
	return {"ok": failed.is_empty(), "saved": saved, "failed": failed}


func _reload(paths: Array, force: bool) -> Dictionary:
	var open := EditorInterface.get_open_scenes()
	var unsaved := EditorInterface.get_unsaved_scenes()
	var reloaded := []
	var skipped := {}
	for p in paths:
		if not open.has(p):
			skipped[p] = "not open"
		elif unsaved.has(p) and not force:
			skipped[p] = "it has unsaved changes in the editor"
		else:
			var earlier := _log.drain()
			EditorInterface.reload_scene_from_path(p)
			reloaded.append(p)
			# Godot 4.7's reload reports that it declined to free the old scene's root; the reload works regardless.
			for e in _log.drain():
				if not str(e.message).begins_with("Something attempted to free the root Node of a scene"):
					earlier.append(e)
			_log.restore(earlier)
	return {"ok": true, "reloaded": reloaded, "skipped": skipped}


## Saves scenes and resources through Godot so it fills in what it owns: the file's UID, each ext_resource's UID and
## each node's unique_id. An open scene is saved by the editor (and refused if it has unsaved changes, which aren't
## ours to save); a closed one is loaded fresh from disk and saved.
func _resave(paths: Array) -> Dictionary:
	var open := EditorInterface.get_open_scenes()
	var unsaved := EditorInterface.get_unsaved_scenes()
	var fs := EditorInterface.get_resource_filesystem()
	var done := {}
	var failed := {}
	for p in paths:
		if unsaved.has(p):
			failed[p] = "it has unsaved changes in the editor"
			continue
		var err := OK
		if open.has(p):
			err = _with_scene(p, EditorInterface.save_scene)
		else:
			var res := ResourceLoader.load(p, "", ResourceLoader.CACHE_MODE_REPLACE)
			if res == null:
				failed[p] = "it didn't load"
				continue
			err = ResourceSaver.save(res, p)
		if err != OK:
			failed[p] = error_string(err)
			continue
		fs.update_file(p)
		var id := ResourceLoader.get_resource_uid(p)
		done[p] = ResourceUID.id_to_text(id) if id != ResourceUID.INVALID_ID else null
	return {"ok": failed.is_empty(), "resaved": done, "failed": failed}


## Loads scripts again from disk, as the editor does when they change, and returns the errors each raised.
func _check(paths: Array) -> Dictionary:
	var out := {}
	var ok := true
	var earlier := _log.drain()  # Raised before this check: kept apart so they aren't blamed on these scripts.
	for p in paths:
		var res := ResourceLoader.load(p, "", ResourceLoader.CACHE_MODE_REPLACE)
		var errs: Array = _log.drain()
		# "Failed to load script ... Parse error" only repeats the parse errors before it.
		if errs.any(func(e): return e.type == "script"):
			errs = errs.filter(func(e): return not str(e.message).begins_with("Failed to load script"))
		if res == null and errs.is_empty():
			errs.append({"type": "error", "message": "it didn't load", "where": p, "count": 1})
		out[p] = errs
		if errs.any(func(e): return e.type != "warning"):
			ok = false
	return {"ok": ok, "checked": out, "earlier_errors": earlier}


func _exec(code: String) -> Dictionary:
	var script := GDScript.new()
	script.source_code = code
	_log.capture(true)
	var err := script.reload()
	if err != OK:
		_log.capture(false)
		return {"ok": false, "error": "the script didn't compile (%s): see errors" % error_string(err)}
	var obj = script.new()
	if obj == null or not obj.has_method("run"):
		_log.capture(false)
		return {"ok": false, "error": "the script needs func run(editor), and must extend RefCounted"}
	var result = obj.run(EditorInterface)
	var output := _log.capture(false)
	return {"ok": true, "result": _plain(result), "output": Array(output)}


func _plain(v: Variant) -> Variant:
	match typeof(v):
		TYPE_NIL, TYPE_BOOL, TYPE_INT, TYPE_FLOAT, TYPE_STRING:
			return v
		TYPE_STRING_NAME, TYPE_NODE_PATH:
			return str(v)
		TYPE_ARRAY:
			return (v as Array).map(_plain)
		TYPE_DICTIONARY:
			var d := {}
			for k in v:
				d[str(k)] = _plain(v[k])
			return d
		_:
			return var_to_str(v)
