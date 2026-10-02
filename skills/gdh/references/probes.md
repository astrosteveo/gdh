# Probes reference

The probes check scene data after each capture, and on `gdh live probes`. Every finding has these fields:

| Field | Meaning |
|---|---|
| `probe` | Which check fired |
| `severity` | `warning` (likely wrong) or `info` (worth a look, often intended) |
| `node` | Path relative to the scene root |
| `message` | What's wrong |
| `data` | The measured values |
| `screen_rect` | `[x, y, w, h]` in screenshot pixels, or `null` |
| `view` | The capture that shows the problem best |
| `crop` | Path of the zoomed crop |

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

One more check runs over a recording rather than a frame (`gdh movie`, `gdh live record`, `gdh live measure`):

| Probe | Fires when | Why that threshold |
|---|---|---|
| `covered` | A UI panel that draws (a `Panel` or `PanelContainer` with a background, a `ColorRect`, a textured `TextureRect` or `NinePatchRect`, an embedded `Window` such as a dialog), at least half opaque, covers the screen's centre and 20% to 95% of the screen in at least half of about 48 samples over the run. | A HUD's widgets are far under 20% and its full-screen root draws nothing; a whole-screen backdrop (95% and up) is a screen of its own. Half the run means it hid the game, not that it flashed up. |

Identical findings are merged into one, and the rest are listed in `data.also`.

The `floating` probe adds temporary collision copies of every mesh for two physics frames. They sit alone on physics layer 32 and detect nothing. Probe rays and shape queries see only that layer, so the game's own colliders, areas and scripts never see the copies, and the copies never affect the game. `testbed/probes/isolation.tscn` tests this with a hidden collider and a trigger area.

## How far to trust them

The rules were tuned on a small set of test scenes, so expect false positives and misses in real projects. Confirm every warning on its crop, and look at the images even when there are no findings. Many defects show only in the images, or need judgment the probes don't have. Examples are holes in a mesh, shading errors, objects at the wrong scale or position, and UI whose state doesn't match its numbers or labels.

## Known gaps

- Only `MeshInstance3D` is checked. CSG shapes, `MultiMeshInstance3D` and `GridMap` are skipped.
- `texture_filter` doesn't account for the project's stretch settings when it computes magnification.
- Deferred checks:
  - Stretched triangles (#8). A pole or chain has edges many times longer than its ring edges, so a simple ratio fires constantly.
  - Hard edges shaded smooth (#10). A smooth 6-sided cylinder measures close to a smoothed box.
  - Text overflowing its container.
