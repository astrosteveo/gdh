# Measuring frames

`gdh measure` puts numbers on what a game draws: whether a still picture flickers, whether a moving one swims, how wide a thin line is, how big each point of light is, whether a dissolve leaves holes or doubles, whether a NaN has drawn black, whether dark areas are crushed to black, what changed between two frames or since a saved baseline, and how long each frame and each render pass takes on the GPU. It works on any PNG frames, whoever saved them, and `gdh live` records frames and frame times from a running game to feed it.

```sh
gdh live record 120 --session s --out frames/still          # step 120 frames, saving each one
gdh measure flicker frames/still                             # then measure them
gdh live measure shimmer --frames 90 --session s --hold ui_left   # or both in one go
gdh live frames --clear --session s; gdh live step 600 --session s; gdh live frames --session s
gdh live bench 600 --budget-p99 8.3 --session s                  # the same in one call; exit 1 over 8.3 ms
```

## Definitions

Every image measure reads luminance: Rec. 709's weights (0.2126, 0.7152, 0.0722) on the 0–255 sRGB values the PNGs hold, in float32. Frames are taken in name order: give files, or directories of them. `--box X0,Y0,X1,Y1` (image pixels, the far edges excluded) and `--mask PNG` (white is measured) limit any measure to a region. `--json` prints the whole result, and `--save FILE.json` keeps it.

| Measure | What it reports | Use it for |
|---|---|---|
| `flicker` | `mean_change`: \|L(t) − L(t−1)\| averaged over the pixels, then the frame pairs. `range_p99`: the 99th percentile of each pixel's range (max − min) over the run. `share_changed_over_2`: the share of pixels that ever change by more than 2 (`--threshold`) from one frame to the next. | A still camera: anything over zero is something changing on its own (noise, a dither that moves, a light that blinks). |
| `shimmer` | The second difference over time, \|L(t−1) − 2L(t) + L(t+1)\|, per triple of frames: its mean, its 99th percentile and the share over 8 (`--threshold`), each averaged over the triples, and the largest seen. | A moving camera. Smooth motion leaves it small; swimming, crawling, sparkle and flicker don't. Moving edges count too, so compare like shots only. |
| `jitter` | A light's summed brightness each frame against its five-frame moving average, in percent. `--lights bright` (luminance over 200), `red` (r > 150, g < 90, b < 90, summing the red channel), or a rule such as `r>150,g<90,b<90` or `l>220`. | Running lights, beacons, lamps: a light that should glow steadily. |
| `term WITHOUT WITH` | What one setting adds: two runs of the same frames (each held frame shot without the setting and with it). Where the term (with − without) shows (its mean over 2): its own second difference, and the frame's there without and with; over the whole frame, `shimmer`'s numbers without and with, and the bright and red lights' jitter. Frames stream, so 4K runs fit in memory. | Judging a post effect, a filter or a lighting change: whether it adds swim. |
| `line` | Across a line from `--from X,Y` to `--to X,Y` (or `--lines FILE.json`), at 24 points along its middle 60%: the profile ±24 px across it in quarter pixels (bilinear), the background as the median of the outer four pixels each side, and the full width at half maximum above it, with the peak above the background. Medians over the points; points that don't stand 12 above the background (`--contrast`) are skipped, and so are lines under 40 px (`--min-length`) and points within 20 px of the edge. A line with a third coordinate over 1 is behind the camera and skipped. | Beams, plume cores, trails, hologram lines: whether thin stays about a pixel wide. A box n px wide reads n + 0.25. |
| `spots` | Isolated points of light in each frame: every local peak with nothing as bright within `--radius` pixels (5) each way, standing `--contrast` (12) above the background (the median of that window's rim), whose rim falls back to within a quarter of its peak of the background (so two points close together, or the edge of something larger, aren't counted), at least radius + 2 px from the edge. For each, its full width at half maximum as the diameter of a disc of the same area (the pixels at or over half its peak, sampled every quarter pixel, bilinear), its sigma from the second moment of its light above the background, and its peak; over them all, how many and the minimum, 10th percentile, median, 90th percentile and maximum of each. The brightest `--limit` (2000) in a frame. | Stars, space dust, sparks, distant lights: whether a point is never under a pixel or so across, and how big the largest grow. A Gaussian of sigma s reads 2.355 s wide. |
| `dissolve A B --region R` | Two layers drawn alone as masks (white where each draws): inside the region (where both draw when whole, shrunk by a pixel), `doubled_px` drawn by both, `empty_px` by neither, and `b_share`, the second's share. | A dither or screen-door fade between two models: both must be zero. |
| `black` | `black_px`: pixels at or under `--floor` (0) in every channel. `nan_px`: those in black shapes more than half of whose immediate neighbours (`--ring 1`) are lit, over 24 in luminance (`--lit`), listed with their boxes in `nan_shapes`. `--fail` exits 1 if any. | A NaN draws pure black with no error, and a blur, the glow or temporal anti-aliasing can spread it. A shadow fades into black through dark pixels; a NaN cuts a hard hole in something lit. |
| `crush` | In the region: `crushed_px`, pixels at the floor (every channel at or under `--floor`, 0) and their share; `near_floor_px`, within its last 6 steps (`--detail`); the darkest luminance; the largest crushed patch. `--fail` exits 1 if any. | Dark areas that should hold detail (a sky, smoke, a hull's shadowed side): a tone mapper clips the darkest values to 0, and a pixel there has lost its detail. |
| `diff A B` | What changed from A to B: the largest and mean difference, the pixels changed by more than 2 (`--threshold`) and their share, the box round them, and the changed regions; with `--out DIR`, a heatmap and a crop of the largest change ([below](#what-changed-diffs-and-baselines)). Two directories compare by file name. | Checking that a change changed what it should, and nothing else. |
| `mask WITH WITHOUT --out PNG` | Where something draws: the pixels where two shots of one held frame (with it, and with it hidden) differ by more than 1 in a channel, with the holes inside filled. | A region for the others: a hull's silhouette for `crush --mask`, say. |
| `times RECORD` | A frame-time record's summary (below). | Reading a record `gdh live frames --save` kept. |

`black` and `crush` have their limits. A black object drawn on purpose in front of something lit reads as a hole; give it a dark grey, or measure around it. A NaN on a surface lit under 24 slips past `--lit`. With little or no anti-aliasing, the last pixel or two of a dark crevice can reach pure black right beside lit pixels and read as a hole too. A pixel between two black shapes counts in both their rings. `crush` counts what's at the floor; deciding which regions should hold detail is the caller's.

## What changed: diffs and baselines

```sh
gdh measure diff before.png after.png --out diff               # one pair
gdh measure diff captures/before captures/after --out diff     # two directories, by file name
gdh capture --project game --scene res://level.tscn --out captures/level --baseline baselines/level --update-baseline
gdh capture --project game --scene res://level.tscn --out captures/level --baseline baselines/level   # exit 1 on a change
```

`gdh measure diff A B` compares two frames of one size, A before and B after. A pixel's difference is the largest of its three channels' differences, 0–255, and the pixel has changed when that is over `--threshold` (2, so rounding stays under it). It reports:

| Key | What it is |
|---|---|
| `max_diff`, `mean_diff` | The largest difference, and the mean over the pixels |
| `changed_px`, `changed_share` | The pixels changed, and their share of the frame (or of `--box` and `--mask`) |
| `box` | The box round every changed pixel: x0, y0, x1, y1 in image pixels, the far edges excluded, as `--box` takes it |
| `regions` | The changed pixels grouped: those in 8-pixel blocks that touch, diagonals included, are one region, so a change's ragged edge stays with it. Each region's pixels, box and largest difference, largest first (at most 10), and `region_count` |
| `identical` | No pixel differs at all, by however little |

With `--out DIR`, a pair that changed also gets two images. `heatmap.png` is B dimmed to grey with each changed pixel on a hot scale, red for the least change through yellow to white for 255 (logarithmic, so a change of a few levels shows as plainly as a large one), smaller differences in dim blue, and each region outlined in cyan. A frame over 1280 px long is shrunk by a whole factor, each pixel showing the largest difference it covers, so a single changed pixel still shows. `crop.png` is the largest region, padded, in A, in B and as their difference, side by side, each zoomed to about 400 px (nearest neighbor, at most 4×); the difference is amplified so its largest reads 255, and its title gives the factor.

Two directories compare the PNGs directly in both by file name, and each pair's heatmap and crop are named after it (`normal-heatmap.png`). `changed` lists the pairs that changed, `only_in_a` and `only_in_b` the names one directory lacks, and a pair of different sizes has an `error` instead of numbers. `--fail` exits 1 when more than `--tolerance` percent (0) of a pair's pixels changed, a pair differs in size, or a name is in one directory only.

Trust the numbers over your eyes. Looking at two frames misses small changes, and vision models are weakest at exactly the subtle regressions that matter (VideoGameQA-Bench); the diff counts every pixel. The heatmap shows where to look, and the crop shows what changed.

### Baselines

`gdh capture --baseline DIR` compares each view it saves with the PNG of the same name in DIR. With several scenes, DIR holds a subdirectory per scene, named as under `--out`. A view with pixels changed by more than `--threshold` (2) is a `baseline` finding named for the view (`normal.png`), whose message gives how much changed and the box round it, with the numbers above in its `data`, a crop in `crops/baseline-<view>-crop.png` (baseline, now, and the difference amplified) and a heatmap beside it. It's a warning when more than `--tolerance` percent (0) of the view's pixels changed, and capture then exits 1; within the tolerance it's info. A view missing from the baseline is a warning too, and a missing DIR stops gdh before Godot starts. `report.json` holds every view's numbers under `baseline`.

`--update-baseline` writes each saved view into DIR, replacing what was there and keeping views not captured this time. Where an old baseline exists it still compares, listing the changes it accepts as info, and never fails on them. It writes nothing when the capture failed.

Some views make steadier baselines than others. `unshaded` (the albedo alone) and `normals` (surface directions) change only when geometry, materials or the camera change. The lit frame (`normal`) and `lighting` also move with shadows, lights, the sky, fog and post effects, and anything that runs on time (an animated shader, particles, temporal anti-aliasing, a flickering light) can differ from one run to the next. For a check of what's built, capture `--modes unshaded,normals`; give the lit frame a tolerance, or hold what moves still. gdh draws a scene the same, pixel for pixel, run after run on one machine, on either display. Another GPU or driver may round or render differently, so keep a baseline per machine.

## Recording from a live session

| Command | What it does |
|---|---|
| `gdh live record N [--out DIR] [--every K]` | Steps N frames and saves every frame (or every Kth) as `DIR/frame-0000.png` on. `--hold`, `--press`, `--release` and `--move` go in as `step`'s do. The default directory is `<session out>/measure/record`. |
| `gdh live measure KIND [--frames N]` | Records N frames (120 by default) and measures them; any image measure but `dissolve` and `term`, with its options. The frames are deleted after unless `--keep`. |
| `gdh live frames [--clear] [--reset] [--save FILE]` | The frame times recorded since the record last started over. `--clear` starts it over now (before a run), `--reset` after reading. |
| `gdh live bench N [--budget-median MS] [--budget-p99 MS]` | Starts the record over, steps N frames (600 by default) and prints their times; exits 1 over a budget ([below](#a-budget-gdh-live-bench)). |
| `gdh measure chart FILE.csv --out PNG` | A line chart of a trace that `gdh live step --trace-out` wrote: a panel for each column, a line for each component of a vector. `--rates` adds rates of change ([live.md](live.md#waiting-and-tracing)). |
| `gdh measure changes FRAMES... --out PNG` | Where a run of frames changed and how often: each pixel by how many frame-to-frame steps it changed in, with the regions boxed and cropped ([motion.md](motion.md#change-maps)). |
| `gdh measure sheet FRAMES... --out PNG` | A contact sheet of PNG frames or of a video: 16 frames spread evenly over the run, labeled, about 1220 px wide ([movie.md](movie.md#the-contact-sheet)). |

`record` and `measure` also write a contact sheet of the frames beside their directory (`<dir>-sheet.png`), and a change map of where the frames changed and how often (`<dir>-changes.png`, [motion.md](motion.md#change-maps)); `--no-sheet` skips both. They also flag a UI panel that covered the middle of the screen for most of the frames, as a `covered` warning with a crop in `<dir>-crops/` ([movie.md](movie.md#panels-that-cover-the-screen)).

Saving a frame is two parts: reading it back from the GPU, which has to happen on the game's main thread between frames, and encoding it as a PNG, which is most of the cost. So each frame is read back on the main thread and encoded on Godot's worker threads (`WorkerThreadPool`, low priority) while the game goes on to the next, each into its own file; the step answers once every file is written. At most one frame per CPU core, less two, and no more than 16, wait to be encoded (a 3840x2160 frame is 33 MB in memory). The frames are the same, pixel for pixel, as frames saved one at a time. Shots of several views (`shot`, `capture`) are encoded the same way.

`gdh live record` on `testbed/measure/measure.tscn` (`--mode orbit`, a moving camera), on the RTX 5080 machine of [displays.md](displays.md), with another game running on the GPU and the CPU throughout. Wall time of the whole command, which includes about 0.12 s to start gdh and reach the game; three runs each on the GPU display, two on Xvfb, which agreed within 4%:

| Display | Size | Frames | Encoded on the main thread | On worker threads | Faster |
|---|---|---|---|---|---|
| `gpu` | 1920x1080 | 120 | 3.72-3.77 s (31 ms a frame) | 0.61-0.63 s (5.2 ms a frame) | 6.0x |
| `gpu` | 3840x2160 | 60 | 7.05-7.09 s (118 ms a frame) | 1.15-1.20 s (19.5 ms a frame) | 6.1x |
| `xvfb` | 1920x1080 | 60 | 3.47-3.50 s (58 ms a frame) | 1.91-1.93 s (32 ms a frame) | 1.8x |

Inside Godot, at 3840x2160, reading a frame back took about 10 ms and encoding it as a PNG about 105 ms (1920x1080: 2.5 ms and 27 ms). On Xvfb the rest is presenting each frame through the CPU ([displays.md](displays.md)), which no saving changes. High-priority tasks were about 20% faster again at 3840x2160 (15 ms a frame), but would compete with the game's own work on the pool, so the tasks stay low priority.

## Frame times

`gdh live` records each game frame's render times as the game runs (frames rendered while it's held don't count). The times come from the rendering device's captured timestamps, which arrive a frame or two late and not on every frame, so each frame the device hands over is recorded once: some game frames have no record of their own, none is counted twice, and none holds a stale value. Godot's `Viewport` measured render time repeats its last value on a frame with no new timestamps and stops changing when every frame is read back for a screenshot; the record holds no stale value, but while every frame is shot the device hands over few new timestamps, so few of those frames are measured (60 frames shot one by one gave about one record between them). Time a run without shooting it. The summary says how many frames were measured of the game frames run.

- **GPU and CPU time:** the root viewport's own timestamps (`vp_begin_N` to `vp_end_N`, which Godot's measured render time reads too), in ms.
- **Each render pass:** start the session with `gdh live start --gpu-passes`. It runs Godot with `--gpu-profile`, so the renderer captures a timestamp at each of its passes (and prints a GPU profile to the log each second). A pass is the time from its timestamp to the next, as Godot's own profile takes it; names starting `>` and `<` open and close a group (`Render 3D Scene`, a viewport), whose time is from one to the other.
- **A game's own passes:** call `RenderingDevice.capture_timestamp("Name")` where the pass starts (in a `CompositorEffect`'s `_render_callback`, say). It shows as a pass, with or without `--gpu-passes`.
- **The summary** of each: the median, the 99th percentile and the worst, nearest rank (the smallest value with at least that share of frames at or under it), and the mean. A pass missing from a frame counts as 0 there.

The GPU under gdh's display idles between frames and clocks down, so times read slower than in play; compare runs on the same machine, alone on the GPU.

The summary warns of two things that make its times read high:

- **`--gpu-passes`:** the renderer timing each of its passes adds GPU time to every frame (a game's 99th percentile read 17 ms with it and 9 ms without). It's on when the game runs with `--gpu-profile`, or the record holds the renderer's own groups. Time a budget in a session started without it.
- **Other games on the machine:** a Godot process other than the game measured (`pgrep -x` for `godot`, `godot-mono` and the name of `$GODOT`'s binary), or any gdh live session's game whatever its binary (the session files in `$XDG_RUNTIME_DIR/gdh/`), running as the record started over (`frames --clear` or `--reset` looks, and the game keeps what it found with the record) or as it's read. `bench` also looks every half second while it runs. A Godot run `--headless` draws nothing and isn't counted. Another instance of the same session counts too. Each is named, by its session or its project. A game that both started and ended between those looks is missed.

`--json` has them as `"warnings"`, with `"gpu_passes"` and `"other_games"` (`[{"pid", "what"}]`). `gdh measure times` shows them for a record `--save` kept.

## A budget: `gdh live bench`

`gdh live bench N` times N frames (600 by default) in one call. In one request to the game it draws held frames back to back for a second (`--warmup SECONDS`), so the GPU has clocked up and no game time passes; starts the frame record over; and steps N frames, all with no gap for the GPU to idle. Then it prints the summary, with its warnings, and checks the GPU's times against `--budget-median MS` and `--budget-p99 MS`: it prints each, and exits 1 if one is over, or if no frame was measured to check it against. `--save FILE.json` keeps the record. `--hold`, `--press`, `--release` and `--move` go in as `step`'s do, to time the game while it's played. Every instance steps; `--instance` picks whose times (and who gets the input). `--json` adds `"budgets"` (`[{"stat", "gpu_ms", "budget_ms", "over"}]`) and `"over_budget"` to the summary.

## Tests

`tests/test_diff.py` checks `diff` on made-up frames whose change is known (its numbers, box, regions, heatmap and crop, and directories by name), and `capture --baseline` on a copy of `testbed/smoke/smoke.tscn`: written, passed unchanged, failed with the box named when the box turns blue, passed within a tolerance, and written again.

`tests/test_measure.py` checks each definition on made-up frames whose answers are known, and each live path on `testbed/measure/measure.tscn`, whose modes (`-- --mode NAME`) draw a still scene, the same with noise, a smooth and a shaken orbit, lines 1, 3 and 6 px wide, points of three sizes beside a block that isn't one (made up, not live), a sphere whose shader makes a NaN beside a black patch in a dark frame, ACES over a dark gradient, a dissolve whose layers match or don't, and a compositor pass with a timestamp of its own.
