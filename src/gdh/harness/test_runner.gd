extends SceneTree
## gdh's own test runner, for projects without GUT or gdUnit4. Run through `gdh test`.
##
## A test file is a GDScript named test_*.gd that extends RefCounted or Node (a Node is added to the tree, so it can
## use get_tree(), timers and process frames). Each method whose name starts with "test" is one test; before_all,
## before_each, after_each and after_all run if the file has them. A test fails when it raises an engine error:
## a failed assert(), a push_error(), or a script error. Tests may await.
##
## Options in GDH_ARGS: --path res://... (repeatable; files or directories, default res://test and res://tests),
## --junit <file> to write the results to.

const ErrorCollector := preload("errors.gd")

var _errors := ErrorCollector.new()
var _paths: Array[String] = []
var _junit := ""


func _initialize() -> void:
	OS.add_logger(_errors)
	var parsed = JSON.parse_string(OS.get_environment("GDH_ARGS"))
	var argv: Array = parsed if parsed is Array else []
	for i in range(0, argv.size() - 1, 2):
		var key := str(argv[i]).trim_prefix("--")
		if key == "path":
			_paths.append(str(argv[i + 1]))
		elif key == "junit":
			_junit = str(argv[i + 1])
	if _paths.is_empty():
		_paths = ["res://test", "res://tests"]
	_run.call_deferred()


func _run() -> void:
	var files: Array[String] = []
	for p in _paths:
		if p.ends_with(".gd"):
			files.append(p)
		else:
			_collect(p, files)
	var suites := []
	var failed := 0
	_errors.drain()
	for file in files:
		var suite := await _run_file(file)
		suites.append(suite)
		failed += suite.cases.filter(func(c): return not c.failures.is_empty()).size()
	_write_junit(suites)
	quit(1 if failed > 0 else 0)


func _collect(dir: String, out: Array[String]) -> void:
	var d := DirAccess.open(dir)
	if d == null:
		return
	for f in d.get_files():
		if f.begins_with("test") and f.ends_with(".gd"):
			out.append(dir.path_join(f))
	for sub in d.get_directories():
		if not sub.begins_with("."):
			_collect(dir.path_join(sub), out)


func _run_file(file: String) -> Dictionary:
	var suite := {"name": file, "cases": []}
	var script: Script = load(file)
	var load_errors := _errors.drain()
	if script == null or not script.can_instantiate():
		suite.cases.append({"name": "(load)", "time": 0.0, "failures": _messages(load_errors, "the script didn't load")})
		return suite
	var obj = script.new()
	if obj is Node:
		root.add_child(obj)
	var names := []
	for m in script.get_script_method_list():
		if str(m.name).begins_with("test") and not names.has(m.name):
			names.append(m.name)
	if obj.has_method("before_all"):
		await obj.before_all()
	for name in names:
		_errors.drain()
		var started := Time.get_ticks_usec()
		if obj.has_method("before_each"):
			await obj.before_each()
		await obj.call(name)
		if obj.has_method("after_each"):
			await obj.after_each()
		var errs := _errors.drain().filter(func(e): return e.type != "warning")
		suite.cases.append({"name": name, "time": (Time.get_ticks_usec() - started) / 1e6, "failures": _messages(errs, "")})
		print("%s %s > %s" % ["FAILED" if not errs.is_empty() else "passed", file, name])
	if obj.has_method("after_all"):
		await obj.after_all()
	if obj is Node:
		obj.queue_free()
		await process_frame
	return suite


func _messages(errs: Array, fallback: String) -> Array:
	var out := errs.map(func(e): return "%s: %s at %s" % [e.type, e.message, e.where])
	if out.is_empty() and fallback != "":
		out.append(fallback)
	return out


func _write_junit(suites: Array) -> void:
	if _junit.is_empty():
		return
	var tests := 0
	var failures := 0
	var body := ""
	for s in suites:
		var failed: int = s.cases.filter(func(c): return not c.failures.is_empty()).size()
		tests += s.cases.size()
		failures += failed
		body += '  <testsuite name="%s" tests="%d" failures="%d">\n' % [s.name.xml_escape(true), s.cases.size(), failed]
		for c in s.cases:
			body += '    <testcase name="%s" classname="%s" time="%.4f">' % [str(c.name).xml_escape(true), s.name.xml_escape(true), c.time]
			if not c.failures.is_empty():
				body += '<failure message="%s">%s</failure>' % [str(c.failures[0]).xml_escape(true), "\n".join(c.failures).xml_escape()]
			body += "</testcase>\n"
		body += "  </testsuite>\n"
	var f := FileAccess.open(_junit, FileAccess.WRITE)
	if f:
		f.store_string('<?xml version="1.0" encoding="UTF-8"?>\n<testsuites name="gdh" tests="%d" failures="%d">\n%s</testsuites>\n' % [tests, failures, body])
		f.close()
