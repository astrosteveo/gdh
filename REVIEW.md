# Review guide

Keep this short. The reviewer reads all of it on every run, and short guides get followed.

## What failure looks like here
- gdh reports success (exit 0, no `DEFECT:` line) when the game raised engine errors, failed to load resources, came up
  at the wrong window size, or failed a check (`--strict`, `--fail`, a scenario's checks, a baseline past tolerance).
- gdh touches the user's own state: their desktop or Wayland session, their `user://` or editor settings, or files in
  the project it doesn't put back (`override.cfg`, `.godot/editor`).
- A process gdh started (Godot, weston, Xwayland, Xvfb, a companion, a child the game spawned) outlives its session.
- The GPU and Xvfb displays give different results, or what gdh captures isn't what the player would see.
- README, `docs/`, the skills or `gdh guide` describe a command, flag or behavior the CLI doesn't have.

## Always check
<!-- Bug classes this project has shipped before. -->

## Don't flag
- Style, formatting, naming, or cleanup. Linters and /simplify handle those.
- Missing tests or docs, unless a line above asks for them.

## Intentional patterns
<!-- Deliberate choices the reviewer keeps flagging as bugs, each with the reason. -->

## Evidence
- Cite file:line and the input or path that triggers each finding. Do not guess from a name.
- Flag a bug class once and list every instance, rather than one finding per instance.
