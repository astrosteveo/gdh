---
name: godot-export
description: Build a Godot 4.7+ game for players — export presets, export templates, debug and release builds, and checking the build runs. Use when the user wants to export, package, ship or build their Godot game, make a Linux/Windows/macOS/web build, or when an export fails.
---

# Exporting a Godot 4.7+ game

`gdh export` runs Godot's export headless, off the user's desktop, and says plainly what's missing.

```sh
gdh export --project <dir>                                   # list the presets
gdh export --project <dir> --preset Linux                    # release build to the preset's path
gdh export --project <dir> --preset Linux --debug            # debug build (prints errors, has the debugger)
gdh export --project <dir> --preset Linux --out build/x.x86_64
gdh export --project <dir> --preset Linux --pack             # only the game's data (.pck), no templates needed
```

## What an export needs

1. **A preset.** Presets live in `export_presets.cfg`, made in the editor's Project > Export dialog. They hold
   choices for the user to make: platform, icon, signing, which files go in. If there are none, ask the user to add
   one there rather than writing the file by hand. Once one exists, you can edit plain options in it (the export
   path, include and exclude filters) when the editor isn't open.
2. **Export templates for this exact Godot version.** They live in
   `~/.local/share/godot/export_templates/<version>/` (for example `4.7.2.stable`, or `4.7.2.stable.mono` for
   C#). If `gdh export` says they're missing, ask the user to install them: Editor > Manage Export Templates >
   Download and Install. They are about a gigabyte, so don't download them without asking.

`export_presets.cfg` can hold passwords for signing (`keystore/release_password` and the like). Godot keeps those in
`.godot/export_credentials.cfg` instead when the editor saves them; never copy them into files you commit.

## Before exporting

- Run the project's tests (`gdh test`) and look at the main scene (`gdh capture`).
- Check the main scene is set (`application/run/main_scene`).
- Resources loaded by path at run time (`load("res://levels/%s.tscn" % name)`) are found only if the preset
  exports them. With `export_filter="all_resources"` they are; with a narrower filter, add them.
- Files that aren't resources (`.json`, `.txt`, `.csv`) are exported only when the preset's `include_filter` names
  them (`*.json`), or when read through a resource.

## After exporting

- Report the files written and their sizes (`gdh export` prints them).
- Smoke-test a Linux build: `gdh export --project <dir> --preset Linux --smoke 10` exports, runs the build off-screen
  for 10 s, and fails on a crash, an early exit or engine errors in its log; look at the `smoke.png` it saves.
  Arguments after `--` go to the game. A Windows or macOS build can't be run here; say so.
- To drive the build itself (a menu, a launcher; where `OS.has_feature("editor")` matters), run it as a black box:
  `gdh live start --binary build/game.x86_64`, then `gdh live wait --log REGEX`, `shot`, `input --click X,Y`,
  `status` and `stop` (docs/live.md, "Programs as they are"). Never run the build bare: it would open on the
  user's desktop.
- `--debug` builds print script errors and warnings; use them to chase a problem that only shows in the exported
  game.

## C# projects

A C# project needs Godot's .NET build (`godot-mono`) and the `.mono` templates. `gdh export` builds the C# code
first, as the other gdh commands do.
