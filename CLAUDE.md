# gdh

A CLI and Claude Code plugin that renders and drives Godot 4.7 games off-screen on the real GPU, so an agent can see
and check what a game draws (README.md).

## Goals
- Let an agent see and play a Godot game without a desktop: `gdh capture`, `live`, `editor`, `movie`, `measure`.
- Let an agent change a project without fighting the Godot editor: `gdh bridge`, `api`, `test`, `export`, and the
  hooks in `hooks/hooks.json`.

## Non-goals
- Platforms other than Linux (README: "Linux only for now").
- Installing anything into the user's project to run it: each harness is passed to Godot with `--script`. The
  exceptions are `gdh bridge install` and `gdh events install`, run only when asked, and the `override.cfg` that
  `gdh movie` and `gdh capture --matrix` (with `--quality` or `--set`) write for one run (`src/gdh/override.py`).

## Stack
- Python 3.10+, with numpy and Pillow as the only Python dependencies (`pyproject.toml`), run through uv. Code is in
  `src/gdh/`; the `gdh` command is `gdh.cli:main`. The hooks are the exception: they run on the system `python3`.
- GDScript that runs inside Godot: `src/gdh/harness/` (capture, live, editor, probes) and `src/gdh/addon/` (the
  bridge and `GdhEvents`).
- The Claude Code plugin: `skills/`, `agents/playtester.md`, `.claude-plugin/`, and `hooks/` (`hooks/register.tsx` is
  the band, TypeScript).

## Conventions
- A failure the user should read is a `GdhError` (`src/gdh/godot.py`), never `SystemExit` or an uncaught exception.
  `cli.main` prints it as `gdh: <message>` and exits 1, with no traceback. `live.py`'s input parsing and `client.py`
  still raise `SystemExit`; don't copy them.
- `gdh live` prints results on stdout, and engine errors, `DEFECT:` lines and notes on stderr, so they survive a
  discarded stdout (`live.py`). `capture`, `movie`, `editor` and `--binary` sessions print them on stdout.
- Every Godot that runs a project or a script gets its environment from `godot_env` (`src/gdh/godot.py`): a Wayland
  display that doesn't exist, the overlays off, the alert stand-ins on PATH, and, for a game, gdh's own `user://`.
  The import, `editor`, the bridge and `api`'s help cache also get `editor_build_env`, so their settings and caches
  are gdh's and kept apart for each Godot binary. `gdh export` uses the user's.
- Tests run on both displays through the `display` fixture in `tests/conftest.py`. A module-scoped fixture that
  starts a game must depend on it. A test of a headless command runs once and skips on the second display, as
  `tests/test_api.py` does.
- Every `gdh` example in README.md, `docs/`, `skills/*/SKILL.md` and `agents/playtester.md` may only use flags the
  command takes, and `gdh guide` stays under 70 lines (`tests/test_guide.py`).
- A commit for an issue ends its subject with the issue's number, like `(#30)`.
- The hooks run on the system `python3` with no dependencies (`hooks/hooks.json`). `hooks.py` and what it imports
  (`editor_bridge.py`, `godot.py`, `imports.py`) use only the standard library at module level. The suite runs the
  hooks with the venv's Python, so it won't catch a break.
- Start each process with `start_new_session=True` and stop it with `kill_groups` after reading its exit code
  (`godot.py`). A process that outlives the command goes in the session's `groups`, so `live stop` and the watchdog
  stop it.
- The harness reads its settings from `GDH_ARGS`, a JSON list of `--key value` pairs; every option takes a value
  (`harness/common.gd`). Arguments after `--` are the game's. A live game's token is in `GDH_TOKEN`, never on the
  command line.

## Check
`uv run pytest -q`

It runs real games on the GPU display and on Xvfb, so it takes a while. Only one run uses the machine at a time
(`conftest.py` takes a lock). Without `godot` or `Xvfb` on PATH, pytest stops with an error in `conftest.py`. The
band's tests are separate: `claude plugin test .`.

## More
- README.md: every command and its main options; `gdh <command> --help` has all of them. `docs/`: one page per area
  (live, editor, measure, probes and more).
- REVIEW.md: what review checks. `.github/workflows/ci.yml`: CI runs pytest on a self-hosted GPU runner.
