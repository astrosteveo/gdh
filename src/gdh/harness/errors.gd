extends Logger
## Collects engine errors and warnings. Repeats of the same error are merged
## into one entry with a count, so an error raised every frame stays one line.
## An error raised from a script keeps the script's backtrace.
##
## Also collects what the game prints (print, printerr): a line printed again
## at once is merged into one with a count, and past OUTPUT_HEAD + OUTPUT_TAIL
## lines only the first and the last are kept, with how many were cut.

const TYPE_NAMES := ["error", "warning", "script", "shader"]
const BACKTRACE_FRAMES := 12
const OUTPUT_HEAD := 10
const OUTPUT_TAIL := 30
const OUTPUT_LINE_CHARS := 300

var _entries: Array[Dictionary] = []
var _index := {}
var _mutex := Mutex.new()
var _head: Array[Dictionary] = []
var _tail: Array[Dictionary] = []
var _cut := 0


func _log_error(function: String, file: String, line: int, code: String, rationale: String,
		_editor_notify: bool, error_type: int, backtraces: Array[ScriptBacktrace]) -> void:
	var entry := {
		"type": TYPE_NAMES[error_type] if error_type < TYPE_NAMES.size() else str(error_type),
		"message": rationale if not rationale.is_empty() else code,
		"where": "%s:%d (%s)" % [file, line, function],
	}
	var key := "%s|%s|%s" % [entry.type, entry.message, entry.where]
	_mutex.lock()
	if _index.has(key):
		_entries[_index[key]].count += 1
	else:
		entry.count = 1
		var frames := _frames(backtraces)
		if not frames.is_empty():
			entry.backtrace = frames
		_index[key] = _entries.size()
		_entries.append(entry)
	_mutex.unlock()


## The first script backtrace's frames, innermost first: "res://player.gd:42 in _physics_process". gdh's own
## (the harness, which calls into the game for eval) are left out.
func _frames(backtraces: Array[ScriptBacktrace]) -> Array:
	var harness: String = get_script().resource_path.get_base_dir() + "/"
	for backtrace in backtraces:
		if backtrace == null or backtrace.is_empty():
			continue
		var out := []
		for i in backtrace.get_frame_count():
			if out.size() < BACKTRACE_FRAMES and not backtrace.get_frame_file(i).begins_with(harness):
				out.append("%s:%d in %s" % [backtrace.get_frame_file(i), backtrace.get_frame_line(i),
						backtrace.get_frame_function(i)])
		return out
	return []


func _log_message(message: String, _error: bool) -> void:
	var lines := message.trim_suffix("\n").split("\n")
	_mutex.lock()
	for line in lines:
		var text := line if line.length() <= OUTPUT_LINE_CHARS else line.left(OUTPUT_LINE_CHARS) + "..."
		var last: Array[Dictionary] = _tail if not _tail.is_empty() else _head
		if not last.is_empty() and last[-1].text == text:
			last[-1].count += 1
		elif _head.size() < OUTPUT_HEAD:
			_head.append({"text": text, "count": 1})
		else:
			_tail.append({"text": text, "count": 1})
			if _tail.size() > OUTPUT_TAIL:
				_tail.pop_front()
				_cut += 1
	_mutex.unlock()


## Every entry collected so far.
func all() -> Array[Dictionary]:
	_mutex.lock()
	var out := _entries.duplicate(true)
	_mutex.unlock()
	return out


## Entries collected since the last drain, then clears them.
func drain() -> Array[Dictionary]:
	_mutex.lock()
	var out := _entries
	_entries = []
	_index = {}
	_mutex.unlock()
	return out


## The lines printed since the last drain_output, then clears them: {"output": [line, ...], "output_cut": lines cut}.
## A merged repeat reads "line (xN)", and where lines were cut, a line says how many.
func drain_output() -> Dictionary:
	_mutex.lock()
	var lines := []
	for item in _head:
		lines.append(_line(item))
	if _cut > 0:
		lines.append("... %d lines cut ..." % _cut)
	for item in _tail:
		lines.append(_line(item))
	var out := {"output": lines, "output_cut": _cut}
	_head = []
	_tail = []
	_cut = 0
	_mutex.unlock()
	return out


func _line(item: Dictionary) -> String:
	return item.text if item.count == 1 else "%s (x%d)" % [item.text, item.count]
