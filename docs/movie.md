# Recording a movie

`gdh movie` records a video of a game with Godot's Movie Maker, off-screen on the GPU, at the size asked for, with the game's audio:

```sh
gdh movie --project path/to/game --out captures/trailer --seconds 20 --resolution 3840x2160
gdh movie --project path/to/game --scene res://levels/level_1.tscn --out captures/l1 --frames 600 --fps 30 -- --demo
```

The output directory gets:

| File | Contents |
|---|---|
| `movie.mp4` | H.264 (yuv420p, `+faststart`) with AAC audio, at `--resolution` and `--fps` |
| `sheet.png` | A contact sheet: 16 frames spread evenly over the movie, each labeled with its frame and time ([below](#the-contact-sheet)) |
| `report.json` | Frames, fps, size, the window's size, the audio (codec, rate, channels, peak, whether it's silent), timings, engine errors, resources that failed to load, `findings` (panels that covered the screen) and `problems` |
| `crops/` | A crop for each finding, from the movie's middle frame |
| `godot.log`, `display.log` | The engine's and the display's output |
| `movie.avi` | Movie Maker's own recording, with `--keep-avi` only |

## How it records

- Godot runs the game with `--write-movie` into an MJPEG AVI at `--quality` (1.0, its best), and `--fixed-fps` at `--fps` (60 by default), so each frame is 1/fps of game time however long it takes to draw: the movie is smooth and the game's timing exact even when recording runs slower than real time. Movie Maker records the game's audio from the same frames, whatever audio driver is running.
- gdh's harness (`harness/movie.gd`) loads the scene (`--scene`, or the project's main scene) before the first frame, so the movie's first frame is the game's, runs exactly the frames asked for (`--frames`, or `--seconds` times `--fps`) and quits. The project's autoloads load as usual, nothing is installed into the project, and `user://` is gdh's own, as for `capture` and `live`.
- ffmpeg encodes the AVI to H.264 at `--crf` 18 (lower is better and larger), `-preset medium`, yuv420p so every player takes it (so the width and height must be even), and AAC at 192 kb/s, with the index at the front of the file (`+faststart`) so it starts playing before it has downloaded.
- gdh checks what it made: the AVI's size and frame count against what was asked, the MP4's against the AVI's, the game's window size, and that Movie Maker's quality setting took. Anything off is printed and kept in `report.json` under `problems`, and gdh exits 1.
- The AVI is large (MJPEG at quality 1.0), so it's written to a temporary directory inside the output directory, on the output's disk rather than a RAM-backed `/tmp`, with a `.gdignore` so Godot never scans it, and removed after encoding. It never goes into the project.

## The size: override.cfg

Movie Maker doesn't record at `--resolution`. It records at the size the project's settings give the window: `display/window/size/window_width_override` and `window_height_override` when set, else `viewport_width` and `viewport_height` (1152x648 by default). With `--resolution 3840x2160` alone, the window is 3840x2160 and the movie 1152x648, scaled down. This held with a larger display and with `--fullscreen`.

So gdh gives Movie Maker the size through the project's settings, for this run only. It writes `<project>/override.cfg`, which Godot reads over `project.godot` at startup, with:

- `display/window/size/window_width_override` and `window_height_override`: the size asked for. These set the window and the movie without changing the project's base size (`viewport_width` and `viewport_height`), so a game under stretch mode `canvas_items` or `viewport` lays out its UI as it always does, scaled to the window.
- `editor/movie_writer/video_quality`: Movie Maker's MJPEG quality, from 0 to 1, which is `--quality`: 1.0 by default, Godot's own default being 0.75. Godot silently ignores a setting it doesn't know in `override.cfg`, so a misspelled or renamed key would leave the default quality with no error. The harness reports the value Godot read, and gdh stops if it isn't the one asked for.

The file is gdh's for as short a time as it can be:

- If the project already has an `override.cfg` that gdh didn't write, gdh refuses to run and leaves it as it is: move it away for the run, or put its settings in `project.godot`.
- gdh's file names the gdh process that wrote it on its first line. One left behind by a gdh process that no longer runs (killed with SIGKILL, say) is removed by the next run; one whose process still runs belongs to another recording, and gdh refuses.
- gdh removes it as soon as the harness says Godot has read its settings (Godot reads them before any script runs), and in every case on the way out: when the run fails, on Ctrl-C, and on SIGTERM or SIGHUP.

## Speed and quality

Movie Maker writes `.png` frames too (`--write-movie frames.png`), but each frame is encoded as a PNG on Godot's main thread. On a C# game at 3840x2160 on Xvfb, PNG output took about 1.3 s a frame, with one CPU core at 100% and the GPU about 7% busy. MJPEG AVI output at `video_quality=1.0` recorded the same 60 frames in 17 s against 89 s for PNG, start-up included: about 5 times faster. Its frames were within 46 dB PSNR of the PNG frames, and it recorded the game's audio, which PNG output can't.

On gdh's testbed (`testbed/movie/movie.tscn`), the RTX 5080 machine described in [displays.md](displays.md):

MEASUREMENTS

## The contact sheet

`sheet.png` is 16 frames spread evenly over the run, first and last included, four across, each about 300 px wide and labeled with its frame number and time: about 1220 px wide in all. It shows at a glance which screen was up when: a dialog left open over most of a recording, a menu where gameplay was expected, a loading screen that never ended, a black stretch. Its tiles are scaled down, so never judge pixel detail on it (thin lines, noise, aliasing, shimmer): scaling makes some and hides others. Open the frames themselves for that.

`gdh live record` and `gdh live measure` write one too, beside the frames' directory (`<dir>-sheet.png`), and `gdh measure sheet FRAMES... --out sheet.png` makes one from any PNG frames or from a video.

## Panels that cover the screen

A recording can be spoiled by a panel no one meant to leave up, such as a modal dialog over the middle of the screen. `gdh movie`, `gdh live record` and `gdh live measure` check for one. Every few frames (about 48 times a run) the harness lists the UI panels drawn over the screen's centre (`harness/covered.gd`), and a panel that's there in at least half of those samples is a `covered` warning, with the share of the screen it covered, the share of the run, and a crop:

```
warning: covered Modal/Dialog: PanelContainer covered 36% of the screen, its centre included, in 100% of the recording's samples: the game was hidden behind it.
```

What counts as a panel is read from the scene, not the pixels, so the camera can move, stop or show anything:

- **It draws:** a `Panel` or `PanelContainer` whose style draws a background, a `ColorRect`, a `TextureRect` or `NinePatchRect` with a texture, or an embedded `Window` (a popup or a dialog, such as an `AcceptDialog`), visible, and at least half opaque (its own and its parents' `modulate`, and its `self_modulate`). The full-screen `Control` a HUD hangs its widgets from draws nothing, so it never counts.
- **It covers the centre** of the screen, and **20% to 95% of the screen**. A HUD's widgets (a score, a health bar, a crosshair at the centre) are far under 20%. A backdrop over the whole screen (a menu's background, a fade) is a screen of its own rather than a panel over the game.
- The outermost panel is reported, not the panels inside it. A panel inside a `SubViewport` isn't on the screen as it is, and isn't read.

Its blind spots: a panel drawn without any of these nodes (`_draw` on a plain `Control`, a 3D quad in front of the camera, a full-screen shader) isn't seen, and neither is a game paused behind a screen-sized panel (95% and more). A check on the pixels alone was left out: on a still camera every pixel holds still, panel or not, so stillness can't tell a panel from the game, and the scene says what is a panel exactly. Look at the contact sheet as well.

## Options

| Option | Default | Meaning |
|---|---|---|
| `--seconds S` or `--frames N` | required | How long to record: game seconds, or frames |
| `--fps` | `60` | Frames a second, fixed |
| `--resolution` | `1920x1080` | The movie's size and the window's; even numbers |
| `--scene` | the main scene | Scene to record |
| `--quality` | `1.0` | Movie Maker's MJPEG quality (0 to 1), which the H.264 encode starts from |
| `--crf` | `18` | The H.264 encode's quality: lower is better and larger |
| `--keep-avi` | off | Keep Movie Maker's AVI as `movie.avi` |
| `--no-sheet` | off | Don't make `sheet.png` |
| `--timeout` | 120 s plus 1 s a frame | Time allowed for the recording |
| `--display`, `--no-build`, `--no-import` | | As for `capture` |

Arguments after `--` go to the game. A movie needs `ffmpeg` and `ffprobe` on `PATH`.

## Tests

`tests/test_movie.py` records `testbed/movie/movie.tscn` (a spinning cube, a 440 Hz tone, and a HUD, a dialog, a dialog that opens late or a popup, picked with the game's arguments) and checks:

- the MP4's size, frame count and codec at a size that's neither the project's nor gdh's default, its AAC audio and that it isn't silent, the sheet, `--seconds`, `--keep-avi` and `--no-sheet`
- that no `override.cfg` or temporary directory is left behind, that an `override.cfg` gdh didn't write is refused and left as it was, that one left by a gdh process that's gone is removed, and that SIGTERM removes it
- odd sizes refused
- a dialog and a popup over the middle flagged, a HUD and a dialog up for only the last quarter of the run not flagged
- `live record`'s `covered` finding and sheet, and `gdh measure sheet` from a video
