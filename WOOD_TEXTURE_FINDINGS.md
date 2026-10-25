# Wood texture mod — findings, bugs and fixes (2026-10-22)

Working record for the build-prop wood panel. Context and deploy details live in
`CONTENT_MAP.md`; this file is the bug-level view: what was wrong, what it caused,
what was changed, and what was deliberately left alone.

## How the wood pipeline works (one paragraph)

The variants deploy bakes seventeen vertical rows (`ROWS = rainbow, natural,
charcoal, whitewash, gold, checker, planks` plus the ten colour rows `black…grey`
that F9 merged into the dial; camo was row 7 and is retired from both file and
dial) into each wood albedo as stacked `*_VR<n>` textures, points
`Wood`/`Wood_Mobile` at `_VR1` (the natural row), and leaves clone
materials (`Wood_VR<n>`) on the props so every variant texture stays loaded. The
userscript (`swap.user.js`) sweeps Unity's material space every 2.5 s, identifies wood
materials by name or by "samples a *_VR/_ATLAS texture", skips anything captured
with a partly-transparent colour (the ghost build — F10), builds a
texture-name → pointer index from the clones, and re-points `_BaseMap` at the
selected row's VR texture (or, on an old atlas file, writes `_BaseMap` scale/offset
tiling). The motion presets (pulse/strobe/flash/rotate) drive texture rows now, not
tint (F9). A small in-page panel shows presets on one side and wiring diagnostics on
the other; the dials persist in GM storage and the script itself updates from the
proxy via `@updateURL`/`@downloadURL` (`/swap.user.js`). Current state as of 5.36.

---

## Findings and fixes

### F1 — Camo forced onto every build (root-cause + fix)

**Symptom:** every launch shows camo wood, "no texture applied" at start.

**Root cause (evidence-backed, ruled out file/cdn/ghost first):** a persisted GM
value `woodTexture = 7`. In the atlas era nothing the dial wrote reached the wood
(`_BaseMap_ST` inert), so clicking Camo recorded a no-op. The variants deploy made
the index *mean* something again, and the one-time migration guard
(`woodTextureFixed >= 1`) had already spent itself in the era that could not apply
the value — so the stale 7 survived and every sweep faithfully re-pointed
`_BaseMap` at `boards_VR7` (camo). The deployed `.data` itself is correct
(`Wood`/`Wood_Mobile` → `_VR1`, built from the natural row; verified in the bake's
construction and in logs/proxy.log's serving trail).

**Fix (migration v2):**

* `migrateWoodTexture()` guard bumped to `woodTextureFixed >= 2`; it resets
  `woodTexture` to the `natural` index **once per install**. After that the dial
  behaves normally:
  - **Natural at start** — a reset value (and every fresh install) resolves to the
    natural index; nothing forces camo.
  - **Camo selectable up to 5.34** — clicking camo persisted and continued to
    apply; it was simply no longer the launch default.

**Completed by migration v3 (camo retired):** the camo entry is removed from
`WOOD_TEXTURES` entirely, so no runtime path can ever point a build prop's
`_BaseMap` at `boards_VR7` again. Preserved semantics, verified by simulation:

* dial indices 0–6 (`rainbow`…`planks`) are unchanged, so every surviving preset
  still resolves to the same `_VR<n>` texture it always meant;
* a persisted `7` (camo in the nine-entry roster) is re-set to natural **once** by
  the v3 migration (guard bumped to `woodTextureFixed >= 3`, value reset only when
  it equals 7 — every other user choice keeps its meaning);
* a persisted `8` (the old snow-probe slot) falls out of range and the index
  clamp resolves it to the natural default on the next read.
* Migration ordering made crash-safe: the guard flag is written **last**, after the
  value it guards, so a partial GM write failure leaves the migration to re-run on
  the next load instead of freezing the dial mid-repair. (The old order set the
  flag first; a throw after it could permanently strand the camo index — the exact
  freeze this migration exists to cure.)

**Requires:** the installed Violentmonkey copy must be at workspace 5.34+; OBE
(install copies) report via the beacon tag.

### F2 — Snow preset could never look like snow (fix)

**Symptom:** clicking the snow preset either reported `no-snow-texture` /
`no-set-texture` / `no-variant-texture` in the panel, or worked — and the wood
turned grey mottling that looked like "nothing happened".

**Root cause:** the preset's write target was the material's own `_SnowMap`
object, which on this file is `'Noise x512'` (pid 97): plain grey value noise, **not
a snow texture**. A successful swap therefore produced no snow look by design; a
failed clone sweep reported failure before any write.

**Fix (retargeted, probe kept as fallback):** the snow pick now prefers the bake's
**whitewash variant** (`<base>_VR3` from the clone index), which is the actual
white-washed wood row, and only falls back to the `_SnowMap` noise read when the
clone index is not filled yet. Either way the write goes through `SetTextureImpl`
with a texture pointer, so the probe keeps proving that runtime texture writes
reach the wood. The panel status line prints which path was taken:

* `snow via variant` — whitewash row applied (the real snow look);
* `snow via noise probe` — clone index empty; grey noise proves the write path only;
* failure reasons unchanged: `no-snow-texture`, `no-set-texture`, `no-variant-texture`,
  `swap-failed`.

The preset label changed `Swap test (snow)` → `Snow` (id `snow` unchanged, so
persisted values and the `woodTexture` index semantics are untouched; row ids above
7 still stay out of the atlas maths).

**Closed by F9 (5.36):** with whitewash — the actual snow look — baked at row 3 of
the merged dial, the probe entry was redundant and is gone; the whole
retarget/probe mechanism (`snow via variant` / `snow via noise probe`, the
`_SnowMap` read) retired with it. Snow on the dial is row 3's pixels, nothing
else.

### F3 — Beacon tag dated installs wrong (fix)

`/__beacon?tag=` carried a hard-coded `bng-swap v5.12` while the script was 5.34, so
beacons could not date an installed copy — which made the F1 "did anyone even have
the new script?" question unanswerable. Fixed: the tag now derives from
`GM_info.script.version` and can no longer go stale.

### F7 — Camo texture physically in the deployed file (fix, completed after F1)

The dial retirement (F1/v3) made `_VR7` unreachable, but the bake still shipped the
camo texture, its clone material, and two clone slots per prop renderer. Removed
properly: `ROWS` lost `camo` (a tail row, so `_VR0..6` and every persisted dial value
keep their meanings), the dead `variant()` camo generator and `noise_field` were
deleted, and `tools/build_wood_atlas.py --variants --deploy` reran:

* `drop_previous()` cleared the old bake (480 renderer slots, 16 clone materials,
  16 variant textures) and rebuilt 14 variants (7 looks × 2 materials);
* verify re-read the saved file: 14 variant textures, stacked-row spread 0.0,
  `Wood`/`Wood_Mobile` → natural (`_VR1`), 60 renderers carry 7 clone slots each,
  62 pre-existing objects changed (2 materials + 60 renderers) — nothing else
  touched;
* deployed file shrank 75,317,635 → 74,238,014 bytes (md5 `c3ab840d…` → `c8f6c2ca…`).

Runtime constants track it: `WOOD_ATLAS_ROWS` is now 7, so the panel index line
reads `tex N/14` and the row-count fallbacks match the bake.

### F9 — Colour/texture merge: one look dial, colours as baked textures (fix)

**Symptom (post-VR-deploy session):** the colour picker "did nothing to the
texture", the animation and rainbow effects never played, and the whole three-dial
setup felt disconnected from what the wood actually showed.

**Root cause:** the colour and animation dials were a *tint* system — `_BaseColor`
/ `_Color` writes driven by `woodColourAt(phase)`. That path had only ever been
confirmed on the pre-VR atlas file; on the variants file the readbacks moved but the
draw did not follow, so both dials read as dead. Worse, the tint write was the
thing flattening the ghost build's alpha (F10).

**Fix (user-confirmed direction — "merge colour and texture into one dial", colours
as actual textures, not tint):**

* **All ten solids baked as rows 7–16.** `tools/build_wood_atlas.py` gained a `SOLIDS`
  recipe table: each colour row is the stock grain's luminance mapped through the
  old tint picker's exact sRGB values (`tinted(lum, rgb, lo, hi)`), so red/black/
  grey keep grain instead of flattening to paint. Dial roster is now 17 entries,
  indices 0–6 unchanged, natural still at 1.
* **One dial, no tint.** `WOOD_COLOURS`, `woodColourAt`, `woodHsv`, `woodRotate`,
  `woodScale`, `WOOD_SATURATION`, `writeMaterialColour` and the colour-tick /
  restore machinery are gone. The panel shows one **Look** dropdown (17 entries)
  plus Motion and Speed; menu commands lost the colour entry.
* **Animation keep + retarget.** Since a texture cannot fade, the motion presets
  now drive *rows* via `woodTextureAt(phase)`: `pulse` = on/off beat against the
  black row 7 (`WOOD_ANIM_OFF_ROW`), `strobe` = faster alternation, `flash` =
  brief black blips, `rotate` = walk all 17 rows once per cycle, `steady` = hold
  the pick. Pacing still comes from the Speed dial (default 6 s); the tick writes
  through the budgeted `applyTextureRow` loop (misses/pruning/strikes carried over
  from the tint era) and skips entirely when the wanted row is already on screen.
* **Migration v4** (`woodTextureFixed >= 4`): the roster grew from 8 to 17, so a
  persisted index no longer means what it meant — `woodTexture` resets to natural
  once per install. The tint-era storage keys (`woodColour`) are simply ignored
  now; the `woodAnim` ids survive unchanged.
* The separate **snow probe entry is removed** (with the whitewash row on the dial
  it duplicated the real snow look); its role as a write-path diagnostic is
  covered by the main dial itself, and `snowMode`/`readMaterialTexture` went with
  it. Panel status keeps the `uv N/N` write counter and gains a `ghost N` line
  (F10).
* Runtime contract tracked: `WOOD_ATLAS_ROWS = WOOD_TEXTURES.length` = 17, so the
  panel reads `tex N/34` and the variant-texture cap (rows × 2 materials = 34)
  matches the bake.

### F10 — Ghost build went neon and opaque (root-cause + fix)

**Symptom:** the ghost build preview (a piece held before placing) showed a neon
planks texture, fully opaque, instead of the game's own semi-transparent wood.

**Root cause:** the ghost is an **instanced `Wood` material** — the same name the
matcher deliberately forgives (`' (Instance)'` suffix), with its translucency
stored in the colour's alpha channel. Two writes destroyed it: the texture dial
re-pointed its albedo at the selected VR row (neon), and the tint era's colour
writes set alpha to 1 (opaque). The default rainbow pose alone guaranteed opaque,
every session.

**Fix:** `locate()`/`collect()` now refuses any wood-matched material whose
*captured original* `_BaseColor`/`_Color` alpha is below 0.95 — a plain prop is
always opaque, so the only things partly clear are previews. Excluded materials are
never written, counted in `diag.ghostSkipped`, and shown on the panel as
`ghost N` (so a live session can confirm the exclusion fires; a counter of 0 while
a ghost is visible means the alpha signature is not where we think, and that is
the follow-up datum). The exclusion is detection-by-value rather than by name on
purpose: the ghost's runtime name is not known and name-matching instanced clones
was already load-bearing.

### F8 — "Update" had no path (fix)

Updating an installed Violentmonkey copy meant hand-copying a file. Now:

* `proxy.py` serves the **live workspace copy** at
  `http://127.0.0.1:8123/swap.user.js`, read from disk per request (no stale twin);
* the script header pins `@updateURL`/`@downloadURL` to that URL, so Violentmonkey
  re-checks it and updates whenever `@version` moves (bump the version with every
  edit; 5.36 as of this pass). Remote players on a tunnel must repoint those two
  lines at their own copy and reinstall manually.

Ops note born of it: Windows `SO_REUSEADDR` allowed six stale proxy copies to hold
port 8123 concurrently (one answered requests with old code, which looked like
"the new handler does not work"). All were killed and one fresh instance started;
the served script now reports 5.35.

### F4 — Proxy served a promising-looking failure (fix)

When server-side brotli decode threw (new CDN behaviour: per-POP inconsistent
`Content-Encoding`), `_handle` blanked the body to `b''` and still answered with the
upstream status code — the loader received a truncated "success" and reported it as
a corrupt-file parse error, blaming the wrong layer. Fixed: a failed decode now
answers **502** so the proxy is unmistakable as the failing hop. The relay itself
(`Accept-Encoding: identity` + upstream CE pass-through + server-side decode) is
unchanged and correct.

### F5 — Path traversal check was prefix-based (fix)

`abspath(local_file).startswith(LOCAL_ROOT)` accepted sibling-root names
(`local_roots/...`). Replaced with `os.path.commonpath`, which also accepts `GET /`
and is safe across differing drives; verified against `/../secrets.txt` (block) and
`/sub/../..` (block).

---

## Potential bugs considered and deliberately not changed

* **Stuck-after-swap on a cold material cache.** A VR material first seen *after*
  its albedo had been swapped elsewhere would cache a non-VR texture name and could
  then never re-resolve variants. Analysis: unreachable in practice — the script
  only swaps materials it has already collected (and therefore cached), and the game
  rebuilds props from the asset file, not from runtime-modified materials. Left as
  documented behaviour; the fallbacks keep every path diagnosable.
* **`diag.textureRows` in snow mode** reports the *cached* baked row count (8) even
  after the albedo points at a non-atlas texture. Cosmetic: rows drive the panel
  line layout, and correctness of the row value does not affect any write.
* **Natural re-write per sweep.** With the natural dial, every relocate re-points
  the wood at its own `_VR1`. Cost is one pointer write per material per 2.5 s
  (Unity treats same-pointer copies as no-ops) and it is what keeps a rebuilt prop
  on the right texture; not worth a state machine.

## Known open limitation

The empirical rig (`local/vr-test.html` + `local/_test/`) can wedge the page's main
thread during the full material sweep (`findObjectsOfType` + per-material name
reads over thousands of materials). It has validated the pipeline up to Unity's
material space, but a clean end-to-end snow-preset observation was not captured
before the live fix; the panel diagnostics above are the intended verification
path for the real session.

## Verification checklist (user side)

1. Install/refresh from the proxy so Violentmonkey holds **5.36** (`@updateURL`/`@downloadURL`
   point at `http://127.0.0.1:8123/swap.user.js`), then **hard-refresh the game
   page** — a running session keeps the old `.data` blob in memory. The beacon's
   `data-blob n=` should show the new size (92,164,325).
2. Look dial: pick any of the 17 rows (7 original looks + 10 colour textures).
   Colours are baked pixels now — nothing tints, so nothing can be "inert"; every
   pick lands on the next sweep (≤2.5 s) via the clone index. A persisted dial was
   reset to natural once by migration v4.
3. Motion dial: `pulse`/`strobe`/`flash` beat the wood against the **black row**
   (7) at the Speed pacing; `rotate` walks all 17 looks once per cycle; `steady`
   holds the pick. If a beat does not land, the panel's `uv N/N` line is the datum.
4. Ghost build: hold a piece before placing — the preview must show **natural wood,
   semi-transparent**, and the panel should count it (`ghost N`, N ≥ 1 while a ghost
   is visible). `ghost 0` with a ghost on screen means the alpha signature is not
   in `_Color`/`_BaseColor` where the exclude expects it — report that.
5. Beacons: the `logs/proxy.log` line for a session should now read
   `bng-swap v5.36 gm …` (or newer).

## Files touched in the colour/texture merge pass (5.36)

* `swap.user.js` — merged 17-entry `WOOD_TEXTURES` (7 looks + 10 colour rows),
  motion presets retargeted at rows (`woodTextureAt`, `WOOD_ANIM_OFF_ROW` = 7),
  tint machinery deleted (`WOOD_COLOURS`, `woodColourAt`, `woodHsv`, `woodRotate`,
  `woodScale`, `WOOD_SATURATION`, `writeMaterialColour`, colour tick/restore),
  budgeted-per-target `applyTextureRow` with row overrides, ghost alpha exclusion
  in `collect()` + `diag.ghostSkipped` and a `ghost N` panel line, snow probe entry
  and `snowMode` removed, migration v4, `WOOD_ATLAS_ROWS = WOOD_TEXTURES.length`,
  panel Colour row gone (one Look + Motion), menu colour entry gone, `@version`
  5.36.
* `tools/build_wood_atlas.py` — `ROWS` 7 → 17 (colour rows 7–16 via the `SOLIDS`
  recipe table: the old tint picker's sRGB values as `tinted()` lo/hi ranges),
  row-contract docstring extended; `--variants --deploy` reran.
* deployed file — 34 variant textures (17 per material, stacked-row spread 0.0),
  `Wood`/`Wood_Mobile` → `boards_VR1`/`WallBaseDiffuse2_VR1`, 60 renderers carry
  17 clone slots each, 62 pre-existing objects changed; 74,238,014 → 92,164,325
  bytes (md5 `c8f6c2ca…` → `d34fa1b1…`).
* `CONTENT_MAP.md` — variants section rewritten for the merged dial and 17 rows.
* `WOOD_TEXTURE_FINDINGS.md` — this file (F9, F10, F2 closure, checklist reworked).

## Files touched in the earlier passes (5.34–5.35)

* `swap.user.js` — migration v3 (camo-retirement index reset), camo preset removed,
  `WOOD_ATLAS_ROWS` 8 → 7, `@version` 5.35, `@updateURL`/`@downloadURL` pinned to the
  proxy, snow-preset retarget (`Snow` label, `_VR3`-first, noise fallback, `snowMode`
  in panel status), beacon tag from `GM_info`.
* `proxy.py` — live `/swap.user.js` endpoint for Violentmonkey updates,
  502-on-brotli-failure, `commonpath` traversal fix.
* `tools/build_wood_atlas.py` — `ROWS` without camo, dead camo generator (`noise_field`)
  removed, row-count wording and bake prints now follow `len(ROWS)`.
* `update` — deployed file re-baked without `_VR7`: 74,238,014 bytes,
  md5 `c8f6c2ca…`, verify-clean.
* `CONTENT_MAP.md` — variants section, serving notes, cross-references updated.
* `WOOD_TEXTURE_FINDINGS.md` — this file.
