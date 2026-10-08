# The editor bridge

`gdh bridge` lets an agent change a Godot project while the Godot editor has it open, through the editor instead of behind its back. The editor keeps open scenes in memory and owns the project's UIDs: a file changed on disk under it can be overwritten by its next save, or overwrite the user's unsaved work, and a made-up `uid://` points at nothing.

## Where it runs

The bridge is `src/gdh/addon/gdh_bridge/server.gd`, a node inside the editor. It runs in one of two places:

- **The user's editor.** `gdh bridge install` copies the addon to the project's `addons/gdh_bridge/`. The user turns it on in Project > Project Settings > Plugins (`--enable` writes it into `project.godot` instead, refused while an editor runs the bridge).
- **gdh's headless editor.** `gdh bridge start` runs `godot --headless --editor --script harness/bridge_host.gd`. The editor starts as usual, with its first scan, its importers and the project's own plugins; once the scan is done the harness adds the bridge. Nothing is installed in the project. The editor's settings and caches live in gdh's editor home (`GDH_EDITOR_HOME`), and a supervisor puts the project's `.godot/editor` (layout, open scenes) back as it was when the editor ends. It quits after `--idle-timeout` seconds without a request (default 1800), or on `gdh bridge stop`, which never stops a person's editor. If the project has the addon enabled, its bridge is the one that runs, and the harness only gives it the idle timeout.

Either way the bridge listens on the first free port from 47800 on 127.0.0.1 and writes the port, a random token, its process id, the Godot version and whether it's headless to `.godot/gdh_bridge.json`, which Godot already keeps out of version control. It removes the file when it stops, if the file is still its own.

## Protocol

Each request is an HTTP POST of a JSON object, with the token in an `X-Gdh-Token` header. The reply is a JSON object with `ok`, the command's fields, and `errors`: the errors and warnings the editor raised since the previous reply, merged by message with a count (an `OS.add_logger` logger). `{"cmd": "status", "peek": true}` instead answers `pending_errors`, a count, and leaves the errors for the next command: Claude Code's band polls this way.

| `cmd` | Fields | Reply |
|---|---|---|
| `status` | `peek` | `godot`, `project`, `headless`, `open_scenes`, `unsaved_scenes`, `current_scene`, `playing`, `playing_scene`, `scanning`, `importing` |
| `errors` | | just `errors` |
| `scan` | `paths` (optional) | `status` once the editor has finished scanning and importing: changed files are updated one by one (`EditorFileSystem.update_file`), or the whole project is rescanned |
| `uid` | `items`: paths or `uid://` | `uids`: each path's UID, or each UID's path; `null` when unknown |
| `open` | `path` | opens a scene in its tab, a script in the script editor, anything else in the inspector, and selects the file |
| `save` | `paths` (optional: every open scene) | `saved`, `failed`; each scene is made current, saved, and the scene that was current comes back |
| `reload` | `paths`, `force` | `reloaded`, `skipped`: a scene with unsaved changes is skipped unless `force` |
| `resave` | `paths` | `resaved` (each one's UID), `failed`: saves through Godot, which fills in the header's UID and each `ext_resource`'s. An open scene is saved by the editor (refused with unsaved changes); a closed one is loaded fresh from disk and saved. Nodes get their `unique_id` when the editor itself next saves the scene. |
| `check` | `paths` | `checked`: each script's errors after loading it again from disk (`CACHE_MODE_REPLACE`), and `earlier_errors` |
| `play` | `path`: empty, `current` or a scene | runs it in the editor |
| `stop` | | stops the running game |
| `exec` | `code` | compiles the GDScript, calls its `run(EditorInterface)`, and answers `result` (as JSON), `output` (what it printed); a compile error is `ok: false` with the parse error in `errors` |
| `quit` | | ends gdh's headless editor (refused in a person's editor) |

`gdh bridge check` with no bridge running loads each script in a headless Godot instead (`harness/check.gd`), as the game would, and reports its errors in the same form. It runs as the game's `SceneTree`, so a script that names an autoload compiles, which `godot --check-only` can't do. Godot creates the autoloads (their `_init` runs), but they're taken off the root before the tree starts, so their `_ready` and `_process` never run. The project is imported first when its import cache is stale, as before `capture` (README, "Importing"). That includes the class cache: a game knows global class names only from `.godot/global_script_class_cache.cfg`, which an import brings up to date, so a `class_name` added since the last import resolves too. A check takes about half a second, and the import a few seconds when one is needed.

## The hooks

The Claude Code plugin's hooks (`hooks/hooks.json`, `src/gdh/hooks.py`) run on every Write, Edit and MultiEdit of a file in a Godot project.

Before the edit they refuse, with a reason that says what to do instead:

- anything under `.godot/`;
- `project.godot` while an editor runs the bridge;
- a scene in the editor's `unsaved_scenes`;
- a `uid://` the edit adds that the project doesn't have: the bridge's `uid` answers, or with no bridge, the UIDs in the project's `.uid` files, `.import` files and scene and resource headers.

After the edit, with a bridge running, they `scan` the file, `check` a GDScript, `resave` a new `.tscn` or `.tres` that has no UID, or `reload` an open scene, and hand Claude Godot's errors (as a block, which Claude reads before going on) and a note of what was done. With no bridge they check a GDScript headless and note that a new scene has no UID yet.

They use only Python's standard library and run from the plugin's copy of gdh, so they need no install. `GDH_HOOKS=off` turns them off.

Moves, renames and deletes made with shell commands don't pass through the hooks; `gdh bridge scan` afterwards tells the editor.
