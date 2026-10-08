# Probes

`src/gdh/harness/probes.gd` checks the running scene's data for likely defects. `capture.gd` runs it after the images are saved. Its findings go into `report.json` under `findings`. For each finding that has a screen rect, `gdh` saves a zoomed crop in `crops/`.

Each finding has these fields:

| Field | Meaning |
|---|---|
| `probe` | Which check fired |
| `severity` | `warning` (likely wrong) or `info` (worth a look, often intended) |
| `node` | Path relative to the scene root |
| `message` | What's wrong, in one or two sentences |
| `data` | The measured values |
| `screen_rect` | `[x, y, w, h]` in screenshot pixels, or `null` |
| `view` | The capture that shows the problem best |
| `crop` | The crop `gdh` saved, relative to the output directory |

`gdh capture --baseline` adds findings of the same shape from its comparison with a saved baseline: `probe` is `baseline`, `node` the view's file (`normal.png`), and the crop puts the baseline, the new view and their difference side by side ([measure.md](measure.md#baselines)).

## Checks

The list of checks, their thresholds and known gaps is in [skills/gdh/references/probes.md](../skills/gdh/references/probes.md), which the Claude Code skill also reads.

## Results on the test scenes

Tested on the 10 scenes in `testbed/blind/` (answer key in `testbed/blind/KEY.md`) plus `testbed/smoke/`. These scenes are now the set the rules were tuned on, not a blind test. Scoring rules were set before the first run:

- A defect counts as caught when a warning names the planted node, one of its ancestors or one of its descendants.
- Every other warning counts as a false positive.

| # | Defect | Caught by |
|---|---|---|
| 1 | Barrel floating 0.3 m | `floating` (matching siblings) |
| 2 | Rug albedo 0–255 | `material_range` |
| 3 | Camera far plane 38 m | `camera_far` |
| 4 | Mirrored BAKERY sign | `label3d_facing` |
| 5 | Foliage alpha not enabled | `alpha_unused` |
| 6 | Shadow acne | `shadow_bias` |
| 7 | Water shader compile error | `report.json` errors, not a probe |
| 8 | Wall mesh holes | not caught |
| 9 | Camera roll 6° | `camera_roll` |
| 10 | Box with smoothed normals | not caught |
| 11 | Mug scaled 8× | not caught |
| 12 | Quest tracker never filled | `report.json` errors, not a probe |
| 13 | HP bar empty | not caught |
| 14 | Raw localization key | `text_key` |
| 15 | Misaligned icon | `sibling_offset` |
| 16 | Blurry sprite | `texture_filter` |
| 17 | Smeared platform texture | `region_smear` |

- **Probes:** 11 of 17 caught, with no false positives and no info findings across the 11 scenes.
- **Probes plus the error log:** 13 of 17.
- **Combined with blind test 1:** the probes catch all three defects I missed by eye (#1, #9, #16). Probes, logs and images together cover all 17. The four the probes miss (#8, #10, #11, #13) were all found in the images.

### Rules changed after seeing results

These changes were made after looking at the test scenes, so they may fit these scenes too closely. The next blind round, with new scenes, is the real test.

1. **Dropped the extreme-scale info finding** (any axis above 4 or below 0.25). It fired on almost every wall, beam and floor, because resizing primitive meshes is normal authoring.
2. **`floating`: dropped the info level and changed the warning rule.** The first version warned when a mesh touched nothing within 1 cm, and gave an info finding otherwise. That produced 7 false warnings (shop signs, a door ring, a lamp bulb, a chair back) and dozens of info findings. The barrel came out only as info, because it touches its neighbor. The warning now needs nothing within 5 cm, and the matching-siblings rule was added for cases like the barrel.
3. **`floating` proximity test fixed.** The hull was first grown outward from its center, which grows the bottom of a flat part by much less than 5 cm. It's now grown by 5 cm along each axis.
4. **`texture_filter` now requires a hard-edged texture.** It had fired on a smooth vignette gradient drawn at 3×.
5. **Repeated findings are merged.** `alpha_unused` had listed the same material 15 times.
