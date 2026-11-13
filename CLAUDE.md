# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

- Run this checkout with `uv run gdh ...`. The `gdh` on PATH is an older `uv tool` install, and this session's gdh plugin (skills, hooks, agent) loads from `~/.claude/plugins/cache/`, not from this folder.
- REVIEW.md lists what counts as a bug here: gdh reporting success after a failure, touching the user's desktop, `user://` or editor settings, or leaving a process it started running. Keep to it.

## Tests
- `uv run pytest` runs most tests once per display (GPU and Xvfb), so run the file you changed: `uv run pytest tests/test_<name>.py -k <test>`. `GDH_TEST_DISPLAYS=gpu` runs one display.
- `tests/conftest.py` skips every test when godot or Xvfb isn't on PATH, and pytest still passes. Read the skip summary (`-ra`) before trusting a green run.
- `tests/test_guide.py` checks every `gdh ...` example in README.md, `docs/`, `skills/*/SKILL.md` and `agents/playtester.md` against the CLI's flags. When you add, rename or remove a flag, update those files and run that test.
- `claude plugin test .` runs the hook tests in `hooks/band.test.tsx`.
- CI has no GPU, so the GPU display's tests skip there. Run the full suite on this machine before a release.

## Style
- No formatter or linter is set up. Don't run ruff over the repo: its defaults would rewrite almost every Python file.

## Git and releases
- `main` is protected: a PR needs the `pytest` and `Band tests` checks green (about 25 minutes) and must be up to date with `main`.
- The version is in `pyproject.toml`, `.claude-plugin/plugin.json` and the install example in README.md. Bump all three together, then run `uv lock`. Installed copies of the plugin only update when `plugin.json`'s version changes.
