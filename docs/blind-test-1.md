# Blind defect test 1

A separate agent built 10 scenes in `testbed/blind/` and planted 17 defects. It wrote the answer key outside the repo. I graded using only the capture outputs. I didn't open the `.tscn` files or the assets.

The grading uses three channels, each adding to the one before it:

- **A:** `normal.png` only
- **B:** A plus the debug views (unshaded, lighting, normals, wireframe, overdraw)
- **C:** B plus `report.json` and `godot.log`

Confidence levels are H (high), M (medium) and L (low).

## Channel A: normal frame only

| Scene | Content | Findings |
|---|---|---|
| 01 | Tavern interior | 1. H: The rug under the left table is pure white, with no shading, texture or shadows. Its material is unlit or overexposed, or its texture is missing. 2. L: The bottle shelf behind the bar crosses the right window. |
| 02 | Street | 1. H: The "BAKERY" sign reads mirrored, so the text is flipped (negative scale or wrong facing). 2. L: The street ends in open sky at the vanishing point. The far geometry may be cut off by the camera's far plane. |
| 03 | Forest campsite | 1. H: The bush sprites show as opaque dark rectangles because alpha transparency isn't applied (the grass tufts render correctly). 2. M: The trees, rocks and path have a grainy, shimmering texture. Mipmaps or filtering are likely missing. 3. L: The edge of the ground plane shows at the right horizon. |
| 04 | Warehouse | Nothing found. |
| 05 | Dungeon corridor | 1. H: At the top right, there's a gap between the right wall and the ceiling, and the dark background shows through. 2. M: A flat, untextured pink strip runs down the middle of the floor. It could be a placeholder material or a stray object. |
| 06 | Home office | 1. H: The coffee mug is the size of the office chair and floats behind it, so it's scaled wrong. 2. L: The window glass is pure white. |
| 07 | Top-down RPG HUD | 1. H: The HP bar reads 85/100 but shows no fill. The mana bar fill is correct. 2. H: The quest panel shows placeholder text, "Quest Title" and "Objective text". |
| 08 | Inventory UI | 1. H: The item name shows the raw localization key `ITEM_IRON_SWORD`. 2. M: The shield icon in slot 3 sits off-center, shifted right and down. |
| 09 | 2D platformer | 1. H: The stone platform on the right has a stretched texture. Only its left end shows bricks, and the rest is a smeared band. |
| 10 | Main menu | Nothing found. |

## Channel B: adding the debug views

The debug views don't change 2D or UI scenes at all. Scenes 07–10 render the same in every view, because Godot's viewport debug modes apply only to 3D.

Changes from channel A:

| Scene | Change |
|---|---|
| 01 | Finding 1 now has a cause. The lighting view shows lamplight and stool shadows falling on the rug, yet the normal frame shows it as (255, 255, 255). Its material ignores lighting, so it's unshaded or strongly emissive. The unshaded view shows its albedo is plain white. |
| 03 | Finding 2 goes from M to H and has a new cause. The unshaded view shows clean, flat tree colors. The lighting view shows fine stripes on every tree, which means shadow acne (shadow bias too low). Mipmaps aren't the problem. |
| 05 | Finding 1 is confirmed. The normal buffer and overdraw views show no geometry in the gap. Finding 2 goes from M to H. In the unshaded view the strip is a raised slab with a plain white, untextured material. |
| 06 | New, H: the front cardboard box has smoothed normals. The normal buffer shows a rainbow gradient across its faces, where the other boxes show flat colors. The lighting view shows soft gradients with no hard edges. |
| 02, 04 | Nothing new. |

## Channel C: adding `report.json` and `godot.log`

Only two scenes logged errors. Every scene loaded.

| Scene | Change |
|---|---|
| 05 | Finding 2 now has a cause. `canal_water.gdshader:9` fails to compile ("Invalid assignment of 'vec4' to 'vec3'"). The canal falls back to the default white material. |
| 07 | Finding 2 now has a cause. In `hud.gd:41` (`_refresh_quest`), `get_node("QuestPanel/Title")` returns null, and setting `text` on it raises a script error. The placeholder text is never replaced. The logs don't explain the empty HP bar. |
| 06 | New, L: the scene draws 53,922 primitives, about as many as the full street and forest scenes. The wireframe shows the mug as a very dense mesh. |

## Score

I opened the answer key only after writing up channels A–C above.

| # | Scene | Planted defect | Found in | Cause identified in | Notes |
|---|---|---|---|---|---|
| 1 | 01 | Barrel floating 0.3 m above the floor | missed | — | Visible once zoomed in (checked after the key). |
| 2 | 01 | Rug albedo set with 0–255 values, rendering pure white | A | partly | I guessed unshaded or emissive. The actual cause is an albedo far above 1. |
| 3 | 02 | Camera far plane set to 38 m | A (L) | A | Flagged with low confidence. |
| 4 | 02 | Mirrored BAKERY sign | A | A | |
| 5 | 03 | Foliage alpha not enabled | A | A | |
| 6 | 03 | Shadow acne (bias 0) | A | B | Channel A blamed missing mipmaps. The lighting view showed the real cause. |
| 7 | 05 | Water shader compile error | A | C | |
| 8 | 05 | Procedural wall mesh with an index bug, leaving holes | A | — | I found the hole but not the index bug. The wireframe shows skewed triangles that I didn't check. |
| 9 | 06 | Camera rolled 6° | missed | — | |
| 10 | 06 | Box with smoothed normals | B | B | |
| 11 | 06 | Mug scaled 8× | A | A | |
| 12 | 07 | Quest tracker never filled (wrong node path) | A | C | |
| 13 | 07 | HP bar empty (integer division) | A | — | |
| 14 | 08 | Raw localization key | A | A | |
| 15 | 08 | Misaligned icon | A (M) | A | |
| 16 | 09 | One sprite with linear filtering (blurry) | missed | — | Visible once zoomed in (checked after the key). |
| 17 | 09 | Platform texture smeared (repeat off) | A | A | |

| Channel | Defects found | Share |
|---|---|---|
| A (normal frame) | 13 | 76% |
| A + B (debug views) | 14 | 82% |
| A + B + C (logs) | 14 | 82% |

The two clean control scenes (04 and 10) were both correctly reported clean. There were no false positives at high or medium confidence. There were four at low confidence: the bottle shelf across the window (01), the ground edge at the horizon (03), the white window glass (06) and the triangle count (06).

### Limits of this test

- I knew there were 17 defects and at least 2 clean scenes before I started grading, and that weakens the clean-control result.
- I wrote the list of defect categories that the scene agent picked from.
- This was one run with one grader.
- These scenes can't be used for a blind test again. The answer key is now in `testbed/blind/KEY.md`. A future blind round needs new scenes.

## What this means for the framework

1. **Engine data should catch the three misses (untested).** A floor-gap check would flag the barrel, and the camera's roll angle would flag the tilted view. Comparing texture filters across sprites would flag the blurry slime. None of these need an image. The blind scenes now serve as the acceptance test for engine-side checks, which pass only if they flag #1, #9 and #16, plus #2 (an albedo above 1).
2. **Comparing views can produce a wrong cause stated with confidence.** For the rug (#2), a pure white result in both the normal and unshaded views looked like an unshaded or emissive material. An albedo far above 1 produces the same result. A check that flags material values outside their valid range would have named the cause directly.
3. **Zoomed crops catch small defects.** The barrel and slime were visible at 2.5–3× zoom. A tool that cuts each frame into zoomed tiles makes this systematic.
4. **Never view downscaled contact sheets.** Sheets of six views tiled at 1932×780 showed dark streaks and blotches that aren't in the full-size PNGs. Resizing them for display caused it. Keep each image at about 1280 px wide or less.
5. **Debug views are 3D-only.** 2D and UI need their own aids, such as outlines of each `Control`'s rect and a report of each sprite's texture filter.
6. **Logs are cheap and precise for causes.** They pinned the exact file and line for the shader and script errors.
7. **The debug views earned their place in 3D.** The normal buffer found the smoothed box, and the lighting view gave the correct cause for the shadow acne.
