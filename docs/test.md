# Running tests

`gdh test --project <dir> [PATH...]` runs a project's tests and reports each failure.

## Frameworks

`--framework auto` (the default) picks by what the project has:

| Framework | When | How gdh runs it |
|---|---|---|
| GUT | `addons/gut/gut_cmdln.gd` exists | `-s res://addons/gut/gut_cmdln.gd -gexit -gjunit_xml_file=<out>/junit.xml`, with `-gdir` for the paths given (or `res://test` and `res://tests`, unless the project has a `.gutconfig.json`) and `-gtest` for files |
| gdUnit4 | `addons/gdUnit4/bin/GdUnitCmdTool.gd` exists | `GdUnitCmdTool.gd -a <path> -c --ignoreHeadlessMode`, its reports in `res://.godot/gdh/gdunit` (it takes a report directory relative to the project), the newest `results.xml` copied to `<out>/junit.xml` |
| gdh | neither | `harness/test_runner.gd` |

gdh's own runner takes every `test*.gd` file under the paths (default `res://test` and `res://tests`). A file extends `RefCounted` or `Node`; a `Node` is added to the tree, so it can await frames and timers. Each method whose name starts with `test` is a test, run in order with `before_all`, `before_each`, `after_each` and `after_all` if the file has them. A test fails when it raises an engine error: a failed `assert()`, a `push_error()`, a script error. A file that doesn't load is one failed test, `(load)`, with the parse errors.

## Running

Before running, gdh builds a C# project, imports the project if its import cache is stale, and imports it if the editor's class cache (`.godot/global_script_class_cache.cfg`) is missing or lacks a `class_name` a script declares, since the frameworks find classes by `class_name`. Tests run headless by default; `--display gpu`, `xvfb` or `auto` runs them on a display of gdh's, for tests that render or need input events. Games under gdh keep `user://` in gdh's own directory. `--timeout` stops a run that hangs (default 600 s).

## Output

gdh prints a summary line, then each failure with its message. The output directory (`--out`, or a new temporary one) holds `junit.xml`, `godot.log` and `report.json`: `tests`, `failed`, `skipped`, `failures` (`suite`, `test`, `message`), `framework`, `exit`, `engine_errors` (errors in the log, up to 50) and `timed_out`. It exits 0 when every test passed, and 1 when one failed, none ran, or the run timed out.
