extends Logger
## Collects engine errors and warnings. Repeats of the same error are merged
## into one entry with a count, so an error raised every frame stays one line.

const TYPE_NAMES := ["error", "warning", "script", "shader"]

var _entries: Array[Dictionary] = []
var _index := {}
var _mutex := Mutex.new()


func _log_error(function: String, file: String, line: int, code: String, rationale: String,
		_editor_notify: bool, error_type: int, _backtraces: Array[ScriptBacktrace]) -> void:
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
		_index[key] = _entries.size()
		_entries.append(entry)
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
