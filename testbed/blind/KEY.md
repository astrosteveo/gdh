# Blind test answer key

Scenes: testbed/blind/scene_01.tscn ... scene_10.tscn
Total planted defects: 17. Clean controls: scene_04 (3D) and scene_10 (UI).
Final verification run: all 10 scenes report "loaded": true. The report.json error count is 2 for scene_05 and 2 for scene_07. Every other scene has 0 errors and 0 warnings.
Note: 2D/UI scenes (07-10) render identically in all six debug views, so "normal.png" there stands for every PNG.

Incidental artifacts that were NOT planted (scorer should not count these as misses or hits):
- The scenes have no MSAA, so edge aliasing shows on thin geometry in every 3D scene.
- In scene_03 lighting.png, the grass-tuft cards look like dark scribbles because their backfaces face away from the sun.
- In scene_01, the floor wood grain shows mild moiré.

---

## scene_01 — Tavern interior
A stone-walled tavern with a bar and bottle shelves, a fireplace, two round tables with stools, and hanging lanterns. There is a cluster of four barrels by the left wall and a rug under the left table.

1. **Floating barrel**
   - (a) What: the front barrel of the four-barrel cluster by the left wall hovers about 0.3 m above the floor. Floor is visible under its base, and its bottom sits higher than its neighbours.
   - (b) How: node `Barrels/Barrel2` has translation y = 0.3 instead of 0.
   - (c) Outputs: normal.png, unshaded.png, lighting.png. It also shows in normals.png and wireframe.png. It is subtle: look at the left-side barrels.

2. **Rug blown out to flat pure white**
   - (a) What: the rug under the left table has no pattern and no shading. It reads as an overexposed white rectangle in a dim, warm room.
   - (b) How: `mat_rug` has albedo_color = Color(172, 52, 48, 1). These are 0-255 values where 0-1 was expected, and they multiply rug_pattern.png.
   - (c) Outputs: normal.png, unshaded.png (the white quad under the left table).

## scene_02 — City street corner
A daytime street between brick, cream and blue buildings. It has shopfronts (CAFE, BAKERY, BOOKS, FLOWERS), parked cars, street lamps, a crosswalk, a MAIN ST sign, a hydrant and a mailbox.

3. **Camera far plane too short**
   - (a) What: the street, buildings, cars and lamps are hard-cut about 38 m from the camera. Sky and horizon show where the street should continue, the building blocks are sliced off, and the road ends abruptly.
   - (b) How: `Camera3D.far = 38.0` (the default is 4000).
   - (c) Outputs: normal.png, unshaded.png, lighting.png. It also shows in normals.png and overdraw.png as an empty central band.

4. **Mirrored shop sign**
   - (a) What: the BAKERY sign on the right-hand shop reads backwards ("YREKAB"). CAFE, BOOKS and MAIN ST read correctly.
   - (b) How: the Label3D `Shops/Bakery/SignText` is rotated 180 degrees about Y, so it faces into the wall. Because it is double_sided, its back side is shown.
   - (c) Outputs: normal.png, unshaded.png.

## scene_03 — Forest clearing campsite
A grass clearing ringed by pines and oaks, with a tent, a campfire and cooking pot, log seats, a stump, rocks, bush cards and grass tufts, under a sun that casts shadows.

5. **Foliage alpha not enabled**
   - (a) What: the bushes render as opaque rectangular cards, with dark backgrounds around the leaves. The grass tufts use alpha correctly.
   - (b) How: `mat_bush` samples bush_leaves.png (RGBA) but has no transparency mode set (TRANSPARENCY_DISABLED). The tuft material uses alpha scissor.
   - (c) Outputs: normal.png, unshaded.png, lighting.png, normals.png, overdraw.png.

6. **Shadow acne**
   - (a) What: striped moiré self-shadowing noise covers lit surfaces: the dirt path, campsite patch, rocks, logs, tree cones and crowns, and the distant ground.
   - (b) How: the `Sun` DirectionalLight3D has shadow_bias = 0.0 and shadow_normal_bias = 0.0.
   - (c) Outputs: normal.png, lighting.png. It is clearest on the path, rocks and trees when zoomed in.

## scene_04 — Warehouse interior
CLEAN.
A brick-walled warehouse with orange and blue pallet racks holding crates and cartons, pallets with stacked boxes, blue and red drums, a roll-up shutter door, yellow floor lines and pendant lamps.

## scene_05 — Dungeon corridor
A stone corridor with a central water canal, pillars and arches, wall torches, barrels, a crate, a bucket, a chest, and a banded wooden door at the far end.

7. **Water shader compile error**
   - (a) What: the canal renders with the engine default flat pale grey/white material instead of dark rippling water.
   - (b) How: line 9 of `assets/shaders/canal_water.gdshader` is `vec3 ripple = texture(ripple_noise, uv);`, which assigns a vec4 to a vec3.
   - (c) Outputs:
     - report.json has two errors: shader "Invalid assignment of 'vec4' to 'vec3'." and "Shader compilation failed.".
     - godot.log has the same errors.
     - normal.png and unshaded.png show a flat pale strip down the canal.

8. **Procedural wall mesh with holes**
   - (a) What: the right wall is built at runtime by `stone_wall.gd` and has triangular gaps in its upper part near the camera. They show as a dark wedge of background void between the wall top and the ceiling. The wireframe shows long skewed triangles spanning the wall.
   - (b) How: in `stone_wall.gd` the quad index uses the wrong row stride: `var a := row * columns + col`. It should be `row * (columns + 1) + col`.
   - (c) Outputs: normal.png, unshaded.png, lighting.png, normals.png, overdraw.png (upper-right wedge), and wireframe.png (skewed diagonals).

## scene_06 — Study / home office
A room with a bookshelf, a desk with monitor, keyboard, mouse, lamp and books, an office chair, a rug, a potted plant, a window, framed pictures and cardboard storage boxes.

9. **Camera roll**
   - (a) What: the camera is rolled about 6 degrees. The bookshelf, room corners and window are tilted and the horizon is not level.
   - (b) How: the Camera3D basis includes a 6-degree roll about its view axis.
   - (c) Outputs: every image (normal.png, unshaded.png, lighting.png, normals.png, wireframe.png, overdraw.png).

10. **Smooth-shaded box**
    - (a) What: the front cardboard box (`Storage/BoxA`) has averaged corner normals. Its flat faces get gradient, soft "blobby" shading, unlike the flat-shaded boxes behind it.
    - (b) How: `assets/models/storage_box.obj` gives every face vertex a shared diagonal corner normal (8 `vn` entries, `s 1`).
    - (c) Outputs: normals.png (rainbow gradient on that box, where other boxes are flat) and lighting.png. normal.png shows it subtly.

11. **Oversized mug**
    - (a) What: the coffee mug on the desk is about 8x too large. It is taller than the monitor and hides the desk lamp.
    - (b) How: node `Desk/Mug` has scale 8.
    - (c) Outputs: normal.png, unshaded.png, lighting.png, normals.png, wireframe.png.

## scene_07 — Top-down RPG HUD
A pixel-art meadow with a hero, purple slimes, trees, rocks, a chest, paths and a pond. The HUD has a status panel with HP and MP bars, score and gold, a minimap, a quest tracker and a hotbar.

12. **Quest tracker never filled**
    - (a) What: the quest tracker shows the placeholder text "Quest Title" and "Objective text", and runtime errors are logged.
    - (b) How: `hud.gd` declares `@onready var quest_title_label: Label = $QuestPanel/Title`, but the node is really at QuestPanel/Margin/Rows/Title. The variable is null, so assigning to it fails and aborts `_refresh_quest`.
    - (c) Outputs:
      - report.json has two errors: `Node not found: "QuestPanel/Title"` and "Invalid assignment of property or key 'text' ... on a base object of type 'null instance'" at hud.gd:41 (_refresh_quest).
      - godot.log has the same errors.
      - normal.png shows the placeholder text.

13. **Empty health bar**
    - (a) What: the HP bar is empty while its label reads "85 / 100". The MP bar is correctly about 70% full.
    - (b) How: `hud.gd` sets `health_bar.value = health / max_health * 100` with both values as ints, so the integer division gives 0.
    - (c) Outputs: normal.png.

## scene_08 — Inventory screen
An RPG inventory window over a dimmed backdrop. It has tabs, a 6x5 item grid, a detail panel for the selected Iron Sword with stats and Equip/Drop buttons, and a gold and weight footer.

14. **Raw localization key**
    - (a) What: the selected item's name shows "ITEM_IRON_SWORD" instead of a display name like "Iron Sword". All other text is plain English.
    - (b) How: the text of Label `Window/DetailPanel/ItemName` is set to the translation key, and no translation is loaded.
    - (c) Outputs: normal.png.

15. **Misaligned icon**
    - (a) What: the shield icon (row 1, column 3) is shifted down and right in its slot. It is off-centre and overlaps the slot's bottom border, while every other icon is centred.
    - (b) How: the offsets of `Slot03/Icon` are (23, 21, 79, 77) instead of (9, 9, 65, 65).
    - (c) Outputs: normal.png.

## scene_09 — 2D platformer level chunk
Pixel art with sky, clouds and hills, grass ground with a pit, stone platforms and a step, coins, the hero, two slimes, a goal flag, and a HUD with a coin count, lives and "WORLD 1-2".

16. **Blurry sprite**
    - (a) What: the right-hand slime (near the flag) is blurry and bilinear-filtered, while the other slime and all other pixel art are crisp.
    - (b) How: the `Slime` node has texture_filter = Linear (2), which overrides the Nearest it would inherit from the root.
    - (c) Outputs: normal.png.

17. **Smeared platform texture**
    - (a) What: the right floating platform shows one proper stone tile, then horizontally smeared, stretched edge pixels instead of repeating tiles.
    - (b) How: `PlatformC` is parented to the scene root instead of the `Tiles` node, so it does not inherit texture_repeat = Enabled. Its region_rect is 3 tiles wide on a 1-tile texture, so with repeat disabled the texture clamps at the edge.
    - (c) Outputs: normal.png.

## scene_10 — Main menu
CLEAN.
The title screen "EMBERFALL — Chronicles of the Old Mill" over a pixel-art sunset backdrop with a castle. It has five menu buttons, a "What's New" panel, a version label and a hint line.
