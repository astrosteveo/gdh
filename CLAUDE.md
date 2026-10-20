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
  exceptions are `gdh bridge install` and `gdh events install`, run only when asked, and `gdh movie`'s
  `override.cfg`, removed once Godot has read it (`src/gdh/override.py`).

## Stack
- Python 3.10+, with numpy and Pillow as the only dependencies (`pyproject.toml`), run through uv. Code is in
  `src/gdh/`; the `gdh` command is `gdh.cli:main`.
- GDScript that runs inside Godot: `src/gdh/harness/` (capture, live, editor, probes) and `src/gdh/addon/` (the
  bridge and `GdhEvents`).
- The Claude Code plugin: `skills/`, `agents/playtester.md`, `.claude-plugin/`, and `hooks/` (`hooks/register.tsx` is
  the band, TypeScript).
- README's "Layout" table maps each command to its file.

## Conventions
- A failure the user should read is a `GdhError` (`src/gdh/godot.py`). `cli.main` prints it as `gdh: <message>` and
  exits 1, with no traceback.
- Results go to stdout. Engine errors, `DEFECT:` lines and notes go to stderr, so they survive a discarded stdout.
- Every Godot process gets its environment from `godot_env` (`src/gdh/godot.py`): its own `user://`, a Wayland
  display that doesn't exist, the overlays off, the alert stand-ins on PATH. Editors also get `editor_build_env`, so
  their settings and caches are gdh's and kept apart for each Godot binary.
- Every test runs on both displays through the `display` fixture in `tests/conftest.py`. A module-scoped fixture that
  starts a game must depend on it.
- Every `gdh` example in README.md, `docs/`, `skills/*/SKILL.md` and `agents/playtester.md` may only use flags the
  command takes, and `gdh guide` stays under 70 lines (`tests/test_guide.py`).
- A commit for an issue ends its subject with the issue's number, like `(#30)`.

## Check
`uv run pytest -q`

It runs real games, every test on the GPU display and on Xvfb, so it takes a while. Only one run uses the machine at a
time (`conftest.py` takes a lock). Without `godot` or `Xvfb` on PATH, conftest skips every test and pytest still
passes. The band's tests are separate: `claude plugin test .`.

## More
- README.md: every command and its options. `docs/`: one page per area (live, editor, measure, probes and more).
- REVIEW.md: what review checks. `.github/workflows/ci.yml`: CI runs pytest on a self-hosted GPU runner.
