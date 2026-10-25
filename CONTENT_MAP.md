# Content map — where the editable things live

Written 2026-10-21. Companion to `tasks/plan.md` in `bng-tweaks` (the plan) and
`RIGGING_PIPELINE.md` (the character, now frozen).

Two shipping paths exist and both are already plumbed:

| what | file | served by |
|---|---|---|
| characters, world models, locker entries | `bundles/6e813a3edd180fe07ca4e438849b67b5.bundle` | overlay server + `catalog_patched.json` |
| locker tile art | `bundles/2aa55d46c4def6847148e4e9af03de6d.bundle` | same |
| **lobby, game modes, build props** | `local/buildnow-gg/225/Build/cdbbb83c0ef9757d51e8dedb4979dcc3.data` | **`swap.user.js`**, which already intercepts this exact file (`DATA_HASH`, `ensureDataBlob()`) |

So editing the `.data` needs no new plumbing — the swapper already serves a local copy of it.

---

## 1. The main data file

`cdbbb83c0ef9757d51e8dedb4979dcc3.data` — 69,590,517 bytes.

It is a **UnityWebData container**: several Unity serialized files concatenated
(`globalgamemanagers.assets`, `sharedassets1.assets`, `sharedassets2.assets`,
`resources.assets`, …). Consequences:

* **Path ids repeat across the sub-files.** A flat `{path_id: object}` map resolves the
  wrong object — it silently returns an unrelated material. Always resolve a reference
  inside the referencing object's own `assets_file`. `inspect_data.py` does this.
* A MonoBehaviour may have **no readable typetree** (IL2CPP build, no type trees shipped).
  The row's serialized data is still plain and can be read by hand.

Tool: `python tools/inspect_data.py {types,find,wood,mat,tex,modes,row,tree}`.

---

## 2. Build prop wood — target #1

Both materials live in `sharedassets2.assets` and both are worn by the **same 30 build
props**, so they are quality twins and both have to change:

| material | `_BaseMap` | size | format |
|---|---|---|---|
| `Wood` | `boards` | 256×256, 9 mips | 28 (crunched) |
| `Wood_Mobile` | `WallBaseDiffuse2` | 512×512, 10 mips | 28 (crunched) |

The 30 users, by GameObject: `WallFull`, `WallHalf`, `WallCentreDoor`, `WallSide`,
`WallSideDoor`, `WallArc`, `WallWindowCenter`, `WallWindowRightSide`,
`WallBottomHalfSide2`, `FloorFull`, `FloorHalf`, `FloorCorner`, `FloorEmptyCorner`,
`FloorRomb`, `RampHalf`, `RampUShape`, `RampLShape`, `RoofFull`, …

Extracted for reference: `out/wood_Wood_BaseMap.png` (vertical painted planks, warm
orange) and `out/wood_Wood_Mobile_BaseMap.png` (tall vertical planks, redder).

**The constraint is the one the character atlas already taught us:** format 28 is
*crunched* and cannot be written. The texture has to be replaced with **DXT1 (format 10)**
plus a rebuilt mip chain — exactly what `build_character.py` does.

Because nothing else uses these two textures, they can be **replaced in place** (same
object, new pixels + format), which reskins every build prop with no renderer sweep and
no material clone.

Not yet known: whether the game picks `Wood` or `Wood_Mobile` on WebGL quality tiers —
covering both makes the question moot.

---

## 3. Game modes — target #2 (`wwzw`)

16 `GameModeSettings` rows in `sharedassets1.assets`. Found by their script pointer:
`globalgamemanagers.assets` holds MonoScript `GameModeSettings` at path id **2110**, and
each row carries that path id as an int64 at byte offset 20 of its serialized data.

| asset | code | scene | display |
|---|---|---|---|
| `GameModeDefault` | `MainWorld` | MainWorld | Free Mode |
| `GameMode_AimTrainer` | `AimTraining` | Aim TrainingM | Aim Training |
| `GameMode_BR` | `Park` | Park | Mini BR |
| `GameMode_BoxFight` | `BoxFight` | Box Fight | Box Fight |
| `GameMode_BoxFight2v2` | `BF2V2` | BoxFight2v2 | Box Fight 2v2 |
| `GameMode_BoxFight_200Pump` | `BF_P200` | BoxFight_200Pump | 200 Pump Only |
| `GameMode_BoxFight_ZoneWars` | `BF_ZW` | BoxFight_ZoneWars | Box Fight Zone Wars |
| `GameMode_EditCourse` | `edc` | EditCourse | Classic Edit Course |
| `GameMode_Realistic1V1` | `R_1V1` | Realistic1V1 | Realistic 1v1 |
| `GameMode_TeamDeathmatch` | `tdm_bvr` | RedVsBlue | Red VS Blue |
| `GameMode_TheLava` | `LV_W` | TheLavaWars | The Lava Wars |
| `GameMode_V-Arena` | `VArena` | V-Arena | 1v1 Build Fight |
| **`GameMode_WildWestZW`** | **`wwzw`** | `WildWest_ZoneWars` | **Wild West Wars** |
| `GameMode_WinterWars` | `wntw` | WinterWars | Winter Wars |
| `GameMode_ZoneWarsFFA6` | `ZoneWars_6Players` | Zone Wars | Zone Wars, 6 players FFA |
| `GameMode_ZoneWars_LateGame` | `zw_lg` | `ZoneWars_LateGame_12Players` | Late Game Zone Wars |

Machine-readable: `out/modes.json`.

### Anatomy of a row

```
  0..16   m_GameObject   PPtr (file id, pad, path id)   — null on every row
 16..32   m_Script       PPtr — file 1, path 2110 ('GameModeSettings')
 32..     length-prefixed strings, then the binary fields
```

The `wwzw` row (`path_id` 3525, 624 B) in order:

| # | value |
|---|---|
| 1 | `GameMode_WildWestZW` — the asset name (m_Name) |
| 2 | `wwzw` — the mode code |
| 3 | `WildWest_ZoneWars` — the scene |
| 4 | `<color=#FBB654>Wild West</color> <color=#18f067>Wars</color> ` — display name (rich text) |
| 5 | `Zone Wars` — short name |
| 6 | `Zone Wars, 8-12 Players, Duo, Solo` — description / tags |
| 7 | `4d2d23d4fc6ca7f43af418080e14b794` — 32-hex GUID, meaning still unresolved |
| 8 | `wildWestDuo` — variant code (the user's "duo wild west") |
| 9 | `UnityEngine.Sprite, UnityEngine.CoreModule, …` — a serialized managed reference |

**Unknown, and the next thing to pin down:** the binary fields (player counts, resources,
loadout references) and what that GUID points at. Two ways to get the field layout:

1. **Offline, by diffing the 16 rows** — the rows share a class, so aligning their bytes
   exposes which offsets are strings, which are ints, which are asset references.
2. **Runtime, through the bridge** — the userscript's IL2CPP layer has the game's
   metadata (`listTypes`/`listMethods`/`searchMethods`), so it can dump the real
   `GameModeSettings` field layout. More certain, needs the game running.

Until then, what is *safely* editable is the string block: display names, descriptions,
codes, scene names. That is enough to prove the edit → save → swap → in-game pipeline.

---

## 4. What is not in this file

* IL2CPP metadata (`global-metadata.dat`) and the WASM: served by the page, not sitting in
  `local/buildnow-gg`. The bridge reads them at runtime.
* The prop *prefabs* a mode spawns, if they are addressable references — those live in
  bundles and are named by the GUIDs above.

---

## 5. The mode container, decoded

Which modes the lobby offers is decided by one object: **`GameModes_Container` [3529]**,
480 B, in `sharedassets1.assets` (script path id 873). It has no typetree, so it is read
by hand:

```
0x00  16 B   m_GameObject PPtr (null)
0x10  16 B   m_Script PPtr (1, 873)
0x1c  int32  name length, "GameModes_Container", padded to 4
      int32  tab count (6)
      per tab:  int32 name length, name, padded
                int32 mode count
                mode count x PPtr (int32 fileID=0 + int64 pathID)
      list 1:   int32 count (9) + 9 x PPtr     the strip the picker draws
      list 2:   int32 count (3) + 3 x PPtr     a small fixed set
```

The six tabs and their rows:

| tab | modes |
| --- | --- |
| 1V1 | 3524 V-Arena, 3521 Realistic 1v1, (null) |
| 2V2 | 3517 BoxFight2v2 |
| FFA | 3522 Red vs Blue |
| Practice | 3514 Aim Trainer, 3520 Edit Course, 3513 Free Mode |
| Box Fight | 3516 Box Fight, 3517 BF2v2, 3519 BF Zone Wars |
| Zone Wars | 3523 The Lava, 3525 wwzw, 3528 Late Game, 3519 BF Zone Wars |

**The four rows nothing references** — i.e. modes that exist but cannot be reached:

| row | name | what it is |
| --- | --- | --- |
| 3515 | `GameMode_BR` | Mini BR / Park |
| 3518 | `GameMode_BoxFight_200Pump` | 200 pump box fight |
| 3526 | `GameMode_WinterWars` | `wntw` |
| 3527 | `GameMode_ZoneWarsFFA6` | six-player zone wars FFA |

A mode is therefore "enabled" by putting a PPtr to its row in the picker's list — and, as
it turns out, **only** that list matters. The in-game picker draws `list 1` directly and
takes its heading from the first entry's category, which is why the screen can read
"Zone Wars" while showing Box Fight and Aim Training cards. The tabs are decorative for
this purpose. `build_modes.py` adds to both anyway, so the container stays coherent.

`build_modes.py` refuses to deploy unless the reloaded file shows the rows listed and
every other object byte-identical.

Note that **Edit Course is listed under Practice but was never in that list** — which is
exactly why it never appeared, despite existing in the game since forever. Its row is also
the odd one out: `-1` where every other mode carries an XP figure, and an empty prefab
list.

### Row layout, as far as it is confirmed

Rows are `GameModeSettings` (script 2110) and vary in length (456-624 B) only because of
their strings. After the 32 B header:

```
string  object name        "GameMode_WildWestZW"
PPtr    + list<PPtr>       prefabs/scene objects (empty for Edit Course)
string  code               "wwzw"
string  scene              "WildWest_ZoneWars"
ints/floats                players min/max, teams, timers, XP, tuning
PPtr, PPtr                 two more asset references
string  display name       with <color=...> markup
string  short name         "Zone Wars"
string  description        "Zone Wars, 8-12 Players, Duo, Solo"
string  guid               32 hex chars
string  variant            "wildWestDuo"
string  assembly type      "UnityEngine.Sprite, UnityEngine.CoreModule, ..."
```

### Saving the data file without inflating it
`env.file.save()` re-saves the one AssetBundle inside the package (`data.unity3d`)
**uncompressed** and turns 69.6 MB into 160.5 MB. Pass `packer="original"` to that bundle
and hand the other ten entries through untouched — `build_wood.save_data()` does this, and
the result is +82 B for a container edit and +11 KB for a texture repaint.

---

## 6. Animating the wood

`Wood` / `Wood_Mobile` are ShaderGraph ("EditableBlock") materials, and the graph carries a
whole burn / dissolve stage that the shipped material leaves switched off. The dissolve
threshold is compared against a noise the shader scrolls with time, so the stage animates
**by itself** once it is enabled — no per-frame script, no shader work.

| property | shipped | what it does |
| --- | --- | --- |
| `_BurnEnabled` | 0 | master switch for the stage |
| `_BurnAmount` | 0 (mobile: 0.53) | how much of the surface is dissolved |
| `_BurnGlow` | 1 (mobile: 4.33) | ember brightness |
| `_BurnTint` | (1, 0.35, 0.05) | ember colour |
| `_BurnNoiseSpeed` | (0.1, 0.05) | scroll speed of the burn noise = animation speed |
| `_BurnEdgeWidth`, `_BurnDarkness`, `_BurnNoiseStrength`, `_BurnNoiseTiling` | | edge shape |
| `_SmothstepEdge` | (0.15, 0.155) | edge falloff |
| `_DissolveMask` | null | optional mask texture |

`Wood_Mobile` already ships with `_BurnAmount 0.53` and `_BurnGlow 4.33` left behind — the
stage was tuned at some point and then disabled, which is why the effect is known to work
on this build.

`build_wood_burn.py` writes those properties and reads them back:

    python tools/build_wood_burn.py --amount 0.05                  # embers in the seams
    python tools/build_wood_burn.py --amount 0.4 --speed 0.6 0.35  # obvious travel
    python tools/build_wood_burn.py --off                          # shipped values
    python tools/build_wood_burn.py --deploy

Two gotchas for anyone editing these materials:

* A ShaderGraph property is a **float** in `m_Floats` or a **Vector2/3/4** stored in
  `m_Colors` as four channels — `_BurnNoiseSpeed` is the latter, which is why it reads as a
  colour.
* The entries of `m_Floats` come back as **tuples**; replace the element, do not assign
  into it. Colours are mutable dicts and can be assigned in place.

The dissolve is visual only: colliders are untouched, but a high `--amount` looks like the
prop has been eaten away, so treat the switch as an art control, not a physics one.

### The albedo row, and why `_BaseMap_ST` does nothing

The graph samples its Texture2D node with the node's own **Tiling And Offset** inputs, and
those are material properties in their own right. Both wood materials carry the same five
hashed `Vector2_*` entries in `m_Colors`:

| value | what it is |
|---|---|
| two reading (1, 1) | tilings |
| two reading (0, 0) | offsets |
| one reading (0.12, 1.05) | a noise scale — not a tiling, leave it alone |

Facts as of the last test (tint era, amended 2026-10-22 by F9/F10):

* Writing `_BaseMap_ST` scale/offset moves **nothing** on screen; the readback changes and
  the wood does not.
* Writing `_BaseColor` / `_Color` **tinted the wood** on the pre-VR atlas file (confirmed
  on screen then) — but on the variants file the readbacks moved without the draw
  following, and the write flattened the ghost build's alpha (F10). The tint system is
  retired: the colours live in the baked rows now, and nothing writes colour outside the
  ghost check.
* The effective window is the **bottom eighth** of the atlas: the deployed atlas is baked
  with offset 0 and the props show row 7, the *last* PNG row (camo). So row `r` sits at
  `(rows-1-r)/rows`, not `r/rows` — the flip is not a guess, it is what the camo proves.
* The only levers with a proven effect on screen are the **texture content** (the numbered
  probe atlas turned the walls magenta) and **swapping the texture object itself**
  (`SetTextureImpl(matrial, id, texturePtr)` — the snow probe turned the ramp white).

### Variants mode (deployed, `tools/build_wood_atlas.py --variants`)

Because row writes are inert on this build, the texture dial works the only way that
does reach the wood: **swapping texture objects**. The bake ships (post-F9 merge,
rebaked 2026-10-22):

* **seventeen textures per wood material**, `boards_VR0..16` and `WallBaseDiffuse2_VR0..16`,
  one look per texture, each stacked as **seventeen identical rows** so the whole look
  arrives whichever slice the shader samples. The roster is the merged dial (F9):
  rainbow, natural, charcoal, whitewash ("Whitewash / Snow"), gold, checker, planks,
  then the ten colour rows black, white, red, orange, green, teal, blue, purple, pink,
  grey — the old tint picker's looks as baked pixels (`SOLIDS` recipes keep the grain);
* `Wood` / `Wood_Mobile` pointed at the natural variant (`*​_VR1`) with tiling (1, 1/17),
  offset 0 — the stock look is what the game shows before the script runs;
* **34 material clones**, `<Wood material>_VR<n>` (`Wood_VR3`, ...), whose `_BaseMap` holds the
  matching variant — **this is what makes the textures load**; a texture nothing
  references is never loaded, and the runtime cannot swap to a pointer that was never
  materialised. The clones are appended to all 60 build-prop renderers' `m_Materials`
  (slots 1..17; one submesh per prop mesh, so Unity never draws them).

Camo (`_VR7` in the atlas era) was already retired; the colour rows reuse that tail.
Because the roster grew from 8 to 17, a persisted `woodTexture` index no longer meant
what it meant — migration v4 resets the dial to natural once per install.

The runtime (`swap.user.js` 5.36+) indexes the clones during every locate sweep
(`clone name → _BaseMap texture name → pointer`, shown as `tex N/34` on the panel), and
when a target's own albedo is a `*_VR<n>` texture it **swaps texture objects** instead of
writing rows: no `_BaseMap_ST`, no graph-vector writes in this mode. The old row path
still runs for any material that samples a `*_ATLAS` texture. Clone materials themselves
are excluded from discovery, and any wood-matched material captured with a partly
transparent colour is refused entirely — that is the **ghost build** (an instanced
`Wood` with its translucency in the colour alpha; excluded from every write, counted
as `ghost N` on the panel — F10).

The motion presets drive rows now (F9): `steady` holds the pick; `pulse`/`strobe`/`flash`
alternate it with the black row (7, `WOOD_ANIM_OFF_ROW`); `rotate` walks all 17 looks once
per cycle; Speed (default 6 s) paces the beats. Writes go through a budgeted per-target
loop (3-strike pruning for destroyed props).

The redeploy grew the file 74,238,014 → 92,164,325 bytes (md5 `c8f6c2ca…` → `d34fa1b1…`;
ten more looks × two materials, identical stacked rows compressing well in the LZ4
bundle). `--off` restores stock + drops VR textures/clones/renderer slots; rerunning
`--variants` is idempotent.

Also serving since 2026-10-22: the proxy serves the **live userscript** at
`http://127.0.0.1:8123/swap.user.js` (read from the project root at request time, never a
stale copy), which is what the script's `@updateURL`/`@downloadURL` point at —
Violentmonkey re-checks it and updates whenever `@version` moves. Ops note: Windows
`SO_REUSEADDR` lets multiple proxy instances bind port 8123 at once; if a rewritten
`proxy.py` seems not to take effect, kill **every** listener on 8123 and start one.

### Default camo, after the variants deploy (investigated 2026-10-22)

Symptom: every launch shows camo ("the ghost build texture is by default camo full").

Ruled out, with evidence:

* The deployed file is correct: `Wood`/`Wood_Mobile` point at `*_VR1`, and the bake
  constructs `_VR1` by stacking the **natural** variant (`ROWS[1] = "natural"`,
  `variant(stock, "natural")` returns the stock texture unchanged), so a fresh GM store
  cannot render camo from the file.
* Served state confirmed from logs/proxy.log: the 03:33 session fetched the current
  75,317,635-byte VR deploy (`data-req … s=plain p=cdbbb83c…data`, `data-blob n=75317635`).
* `BuildingGhost`/`BuildingGhost_GeometryLayer` are plain transparent blue holograms with
  no `_BaseMap`; they cannot source camo.

Root cause: a **persisted `woodTexture` GM index of 7**. In the pre-VR era nothing the
dial wrote reached the wood (`_BaseMap_ST` inert), so a click on "Camo" recorded a
no-op and left `woodTexture = 7` in storage. Once the variants deploy made pointer
swaps work, every tick faithfully repoints `_BaseMap` at `boards_VR7`/`…_VR7` — the
camo look — "by default", while the file's own default remains natural. The one-time
`migrateWoodTexture()` guard (`woodTextureFixed >= 1`) spends itself in the era that
couldn't apply the value, so the stale 7 survived into the working one.

Fix in `swap.user.js`: migration bumped to `woodTextureFixed >= 2`, resetting
`woodTexture` to `natural` once. Then completed by a v3 migration when camo was
retired from the dial entirely: the preset entry is gone, indices 0–6 keep their
`_VR<n>` meanings, and only a persisted 7 (old camo) is re-set to natural. The
current guard is **v4** (F9: the roster grew 8 → 17, so every persisted dial value
resets once per install). See `WOOD_TEXTURE_FINDINGS.md` for the verified
value-mapping table. The working record for this and every wood-panel fix is
`WOOD_TEXTURE_FINDINGS.md`.

Migrated-out caveat: if the *installed* Violentmonkey copy predates 5.34, it must be
reinsured/updated; the beacon tag `bng-swap v5.12` at line ~421 was a stale hard-coded
string (corrected to v5.34) and did **not** date the install.

### Snow swap preset — what "doesn't work" means (retired 2026-10-22, F9)

This preset is **gone**: with whitewash — the real snow look — baked at row 3 of the
merged 17-row dial, a separate probe entry was redundant. The historical record
remains, because it is what proved the `SetTextureImpl` swap path: the original
target, `_SnowMap`, was `'Noise x512'` (grey noise, mean 119 — not a snow texture), so
a "successful" swap looked like nothing; the retarget preferred the whitewash
variant (`boards_VR3`) and kept the noise read as the write-path fallback. On today's
dial, snow is row 3's pixels, and the same write path is exercised by every look pick.

### Serving-notes for anyone re-running this on the live CDN (2026-10-22)

* The CrazyGames wrapper switched its Unity paths to a **doubled** segment:
  `buildnow-gg/225/Build/Build/<hash>.<ext>` (loader/wasm/framework/`*.data.br`). The
  one-`Build/` keys that the workspace config constants still name 404. The proxy relay
  picks this up transparently; only hand-made URLs must use the doubled path.
* The edge answers inconsistently per POP: the same `.br` key may arrive brotli-encoded
  (`Content-Encoding: br`) or already decoded. `proxy.py` now requests
  `Accept-Encoding: identity`, passes through upstream `Content-Encoding`, and
  brotli-decodes server-side when a `br` body comes through (the `brotli` module is
  present), so the loader never sees a corrupt half-fold.
* Live rig: `local/vr-test.html` + `local/_test/` boots the game directly on the local
  patched data with the swap script's frame test forced on (no Violentmonkey needed;
  GM_* shims over localStorage). Confirms: VR data served, modkit metadata parsed,
  sweep reaches Unity's material space. Known rig limitation: the full locate sweep can
  block the page's main thread for minutes on a tall material list.
