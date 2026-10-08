# Working on gdh

gdh renders and drives Godot 4 off-screen. `README.md` covers what it does and needs, and `docs/` covers each command. This file covers developing gdh itself.

## Never run Godot by hand

Run Godot only through gdh: `uv run gdh ...` from this folder. A bare `godot` run can open windows or error dialogs on the owner's desktop. Godot's alerts use zenity, which finds the Wayland desktop even with `WAYLAND_DISPLAY` unset. gdh isolates every run. Don't wrap gdh in your own `xvfb-run` either, because gdh picks and manages its displays.

## Tests

- Run `timeout 1200 uv run pytest -q` before merging to `main`. It runs real Godot on the GPU display and on Xvfb, and takes about 11 minutes. Run a single file, such as `uv run pytest tests/test_spawned.py`, while you work.
- After a run, check that nothing is left: `pgrep -x Xvfb`, `pgrep -x weston`, `pgrep -x godot`.
- Only one gdh test run goes at a time on this machine: `tests/conftest.py` takes a lock (`$XDG_RUNTIME_DIR/gdh/test-suite.lock`) and waits for another worktree's run to finish, saying so. Two at once ran the GPU out of channels for new Vulkan devices (`journalctl -k` shows `NVRM ... NV_ERR_STATE_IN_USE`) and failed tests that pass alone. A run from a branch older than the lock doesn't take it, so check `ps -eo pid,args | grep pytest` before a full run until every branch has it.
- pytest collects any function named `test*`, so name helpers in `tests/conftest.py` something else, like `make_testbed_variant`.
- `testbed/project.godot` must keep `run/main_scene`. Tests depend on it. When an agent builds testbed scenes, tell it not to edit `project.godot`, and check `git diff testbed/project.godot` afterwards.

## Processes

- Never use `pkill -f` or `pgrep -f`. The pattern matches your own command, so `pkill -f` can kill the shell running it, and `pgrep -f` counts itself. Use `pgrep -x`, or stop a known PID.
- Other agents may be working in gdh at the same time, in worktrees of their own. Stop only your own processes. Stage explicit paths, not `git add -A`.

## Shipping changes

- gdh's remote is `origin`, [github.com/astrosteveo/gdh](https://github.com/astrosteveo/gdh). gdh also runs in place from this folder, so the local `main` is what everyone on this machine uses. Push `main` to `origin` after each merge.
- Work in your own worktree, on a branch named `<area>/<name>` from `main`.
- Merge into `main` with a merge commit, named like the existing history: `Merge <branch>: <what it adds>`. Run the full tests first. Then remove your worktree and branch, and delete the branch on `origin` too if you pushed it.
- Update `README.md`, the matching file in `docs/`, and `skills/gdh/SKILL.md` along with any change users will see.
- Keep gdh generic. It serves any Godot project, not only the one that found the problem.

## Splitting work across agents

The test lock limits parallel work, not the number of agents: one test run at a time, and the full suite takes about 11 minutes.

- Each agent runs only the test files it adds or touches. The coordinator merges the ready branches in an integration worktree (`integrate/<name>`, each branch still its own `Merge <branch>: ...` commit), runs the full suite once for the batch, then fast-forwards `main` and pushes.
- Merge first, and alone, any change that makes the others' test runs faster.
- `skills/gdh/SKILL.md` and `README.md` conflict across parallel branches. Agents update their own file in `docs/` and give the coordinator a few lines for the skill and the README, which go in once.
- Branches that add to the same code (`step` options in `live.py` and `bridge.gd`, say) add contiguous blocks and merge one after another, or go to one agent.
- Fix a small problem you've already diagnosed yourself. An agent would first have to learn the codebase.
- Start work that builds on another branch once that branch has merged.

## This machine

- `rsync` isn't installed. Copy with `cp -a`, or `shutil.copytree` in Python.
- Godot uses the NVIDIA RTX 5080 (Vulkan device 0) by default.
