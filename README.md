# gdh

Renders Godot scenes off-screen on the real GPU and saves screenshots, debug views, engine errors and checks for likely defects. An AI agent or a script can then inspect what the game draws.

Linux only for now.

## Requirements

- Linux
- Godot 4.7 (tested with 4.7.2)
- `xvfb-run` (Arch: `xorg-server-xvfb`, Debian/Ubuntu: `xvfb`)
- A Vulkan driver for your GPU
- [uv](https://docs.astral.sh/uv/) (it provides Python 3.10+ and Pillow)

## Install

From a clone of this repo:

```sh
uv tool install .
```

This puts `gdh` on your `PATH`. After pulling changes, run `uv tool install --reinstall .`.

To run it from the repo without installing, use `uv run gdh ...`.

## Capture a scene

```sh
gdh capture --project path/to/game --scene res://levels/level_1.tscn --out captures/level_1
```

Nothing is installed into the project. For each scene, the output directory gets these files:

| File | Contents |
|---|---|
| `normal.png` | The frame as the player sees it |
| `unshaded.png` | Base colors with lighting off |
| `lighting.png` | Lighting only |
| `normals.png` | Surface directions (normal buffer) |
| `wireframe.png` | Triangle edges |
| `overdraw.png` | How many times each pixel is drawn |
| `report.json` | GPU used, engine errors and warnings, render stats, probe findings |
| `crops/` | A zoomed crop for each finding that has a screen area |
| `godot.log` | Full engine output |

Options:

| Option | Default | Meaning |
|---|---|---|
| `--scene` | required | Scene to capture. Repeat it to capture several scenes, each in its own subdirectory. |
| `--modes` | all six | Comma-separated list of views, e.g. `normal,wireframe` |
| `--warmup` | `30` | Frames to render before capturing |
| `--resolution` | `1280x720` | Size of the capture |
| `--timeout` | `120` | Seconds allowed per scene |
| `--tiles` | off | Also save `normal.png` as four 2× tiles in `crops/` |

Environment variables:

| Variable | Meaning |
|---|---|
| `GODOT` | Path to the Godot binary. Default: `godot`. |
| `GDH_GPU_INDEX` | Vulkan device index to render on. Default: Godot picks one. |

## Probes

After capturing, `gdh` checks the scene's data for likely defects. Examples are floating objects, a tilted camera, geometry cut off by the far plane, material values out of range, blurry pixel art, raw translation keys and misaligned UI items. It prints each finding and saves a zoomed crop of it. See [docs/probes.md](docs/probes.md) for the checks, their thresholds and test results.

## How it stays off your desktop

`gdh` runs Godot inside Xvfb with the X11 display driver and Vulkan, so windows never reach your session. For each run, it also writes stand-ins for `zenity`, `kdialog`, `Xdialog` and `xmessage` to a temporary directory and puts that directory first on `PATH`. When Godot pops up an alert, the stand-in writes the message to `godot.log` and no dialog opens.

## Layout

| Path | Contents |
|---|---|
| `src/gdh/cli.py` | The `gdh` command |
| `src/gdh/harness/capture.gd` | Runs inside Godot. Saves the views, runs the probes and writes `report.json`. |
| `src/gdh/harness/probes.gd` | The probes |
| `testbed/` | Godot project with test scenes |
| `docs/` | Test reports and probe documentation |

## License

To be decided.
