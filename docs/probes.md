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

## Checks

| Probe | Fires when | Why that threshold |
|---|---|---|
| `floating` | A mesh is 2 cm–1 m above the surface below it, and nothing else is within 5 cm. Or it's that far up while at least 60% of 3+ matching siblings rest on a surface. Siblings match when they hold the same meshes (by path, type and size). | Under 2 cm reads as contact. Over 1 m is usually hung or mounted on purpose. Parts within 5 cm read as attached. |
| `camera_roll` | The active camera is rolled more than 1°. | Most players notice a tilt of about this size. |
| `camera_far` | Geometry inside the view reaches past the camera's far plane. | That geometry is cut off on screen. |
| `material_range` | A `BaseMaterial3D` has an albedo channel outside 0–1, or roughness or metallic outside 0–1. Emission energy above 16 gives an info finding. | Albedo above 1 is physically invalid. It usually means 0–255 values were used. |
| `alpha_unused` | The albedo texture has transparent pixels and the material's transparency is disabled. | The cut-out shape renders as an opaque card. |
| `shadow_bias` | A light casts shadows with both bias and normal bias at 0. | This causes shadow acne. |
| `label3d_facing` | A `Label3D` reads mirrored, because it faces away and is double-sided, or because its scale is negative. An info finding covers labels that face away and aren't drawn. | Mirrored text is never intended. |
| `scale` (info) | Negative scale mirrors a mesh. | Often intended for symmetric props. |
| `texture_filter` | A hard-edged texture is drawn at 2× or more with linear filtering. It's a warning when the scene also uses nearest filtering, and info otherwise. | Linear filtering blurs visibly only at hard edges. A texture counts as hard-edged when at least 5% of neighboring texel pairs differ by more than 20%. |
| `region_smear` | A `Sprite2D` region reaches outside its texture while texture repeat is disabled. | The edge pixels stretch across the rest of the sprite. |
| `text_key` | Text looks like `SOME_KEY` and has no translation loaded. | A raw translation key is showing. |
| `text_placeholder` | Text contains lorem ipsum, placeholder, TODO, TBD, FIXME, "sample text" or "insert text here". | These are generic placeholder markers. |
| `sibling_offset` | The same-named child of 4+ sibling containers sits 3 px or more off, while 75% of the others agree within 1 px. Positions are compared by center, so icons of different sizes still match. | This catches a misplaced grid slot or list-row item. |

Identical findings are merged into one, and the rest are listed in `data.also`.

The `floating` probe adds temporary collision copies of every mesh for two physics frames. They sit alone on physics layer 32 and detect nothing. Probe rays and shape queries see only that layer, so the game's own colliders, areas and scripts never see the copies, and the copies never affect the game. `testbed/probes/isolation.tscn` tests this with a hidden collider and a trigger area.

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

## Known gaps

- Only `MeshInstance3D` is checked. CSG shapes, `MultiMeshInstance3D` and `GridMap` are skipped.
- `texture_filter` doesn't account for the project's stretch settings when it computes magnification.
- Deferred checks:
  - Stretched triangles (#8). A pole or chain has edges many times longer than its ring edges, so a simple ratio fires constantly.
  - Hard edges shaded smooth (#10). A smooth 6-sided cylinder measures close to a smoothed box.
  - Text overflowing its container.
