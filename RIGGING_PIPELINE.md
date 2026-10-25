# The rigging pipeline, exactly

How a foreign model becomes a BNG character: what the browser does, what the Python does, in
what order, with which formulas and which numbers. Written to be followed by an agent that has
to change something without breaking it.

Project root: `C:\Users\assko\Desktop\bng-projects`

---

## 0. The one invariant

**The page's world space is the bake's world space.** Every number the user clicks is a point
in the space the server will write the mesh in. If those two spaces ever differ, the bone the
user placed on the model's shoulder is not the bone that ends up on the model's shoulder, and
the skin is wrong in a way nobody can see until the character moves.

The three moves that define that space, and they are the same three on both sides:

| move | what it does | why |
|---|---|---|
| `flip_x` | mirror in X: `x → -x`, triangle winding reversed | glTF is right-handed with the character's left at **+X**; Unity is left-handed with it at **−X** |
| `ground` | translate in Y so the lowest vertex is at `y = 0` | the game draws characters with their soles on the floor |
| `repose` | rotate the arm/leg chains from the source's A-pose to BNG's T-pose | every clip is written against BNG's T-pose |

The first two are rigid, so the page can do them client-side instantly (`prepareInPlace()` in
`rigger/rigger.js`). The third is **not** rigid — it changes the mesh — so the server does it
and the page shows the server's re-posed copy. That is the whole reason there are two upload
modes (see §6).

---

## 1. The files

| file | what it is |
|---|---|
| `rigger/_rig_lib.js` | **source of truth** for the 50 bones, the 17 anchors, the derivation rules. Parsed by Python, imported by the browser |
| `rigger/bones.json` | the 50 bones + parents, generated from `_rig_lib.js` by `rigger/export_bones.py` |
| `rigger/bng_rest.json` | BNG's 50 rest **positions**, generated the same way. The page measures its derived bones against these |
| `rigger/index.html`, `rigger/rigger.js`, `rigger/rigger.css` | the page |
| `rigger/serve.py` | the local server: static files + `POST /api/model` + `POST /api/bake` |
| `rigger/preview.py` | `posed_source()` (the model in the bake's space), `write_model()` (the mesh the page shows + the anchors side-car), `write_rigged()` (the bake) |
| `rig.py` | the rigging maths: `load_glb`, `flip_x`, `repose`, `ground`, `bone_targets`, `reindex_weights`, `generate_weights`, `aim`, `align_mesh`, `bind`, `new_world`, `bindposes` |
| `glblib.py` | a 114-line GLB reader (numpy only): accessors, node world matrices, primitives |
| `skinlib.py` | read/write a skinned mesh in Unity's own vertex layout (the streams, the 16-byte alignment, `rebuild`, `set_geometry`) |
| `install_rig.py` | write the bones and the mesh into the bundle: `find_bones`, `write_bones`, `write_mesh` |
| `haze_tex.py` | Haze's own atlas (her three maps, a fixed 2048 layout) — the template the generic packer replaces |
| `build_haze.py` | the end-to-end build for one character: model → bundle, with the verify block |
| `build_character.py` | the material/texture half: clone `_BaseMap`, clone the material, repoint the renderer, preservation guard, deploy |
| `characters.py` | **new**: find the character slots in a bundle by name (renderer, mesh, bones, materials, texture, vertex count) instead of hard-coded path ids |
| `out/bng_female_medium_rig.json` | the rig side-car: BNG's 50 bones with their local matrices, read out of the bundle by `emit_rig.py` |
| `bundles/6e813a3edd180fe07ca4e438849b67b5.bundle` | the bundle every build reads and writes |
| `local/buildnow-gg/225/Build/StreamingAssets/aa/WebGL/` | the deploy target |

### The three check scripts (run all three after any change)

```
python rigger/_check_skinless.py     # a model with no skin at all: weights, bindposes, floor
python rigger/_check_preview.py      # the baked rig IS the bundle's rig (Haze, bit for bit)
python rigger/_check_skinless.py rigger/uploads/<file>.glb   # any other model
```

---

## 2. Coordinate system, units, conventions

* **Units**: metres, everywhere. glTF is metres by convention; Unity is metres.
* **Up**: +Y. **Forward**: +Z (the character faces +Z). **Right**: Unity's −X is the character's right; the character's **left is at −X** in Unity space, **+X** in glTF space.
* **The model's own node transform**: for a **skinned** mesh, glTF says the node transform is ignored — the mesh lives in the skeleton's space. `rig.load_glb` therefore leaves it alone. Haze's mesh node carries a `0.011` scale (Unreal cm → m) that would shrink her to 2 cm if it were applied. For an **unskinned** mesh the node transform *is* the placement, so it is baked into the vertices (`_place()`), and if its determinant is negative the triangle winding is reversed so the faces keep pointing outwards.
* **Mirror**: `F = diag(-1, 1, 1, 1)`. Positions and normals are multiplied by `diag(-1,1,1)`; the triangle order is reversed; the joint world matrices and the inverse bind matrices are **conjugated**: `X → F X F`. The conjugation is what keeps the skinning equation consistent: mirroring the joints but not the binds would leave the model deformed in its own bind pose.
* **UVs**: glTF's origin is top-left, Unity's is bottom-left. The page and the build both flip V: `v_unity = 1 - v_gltf` (`build_haze.py` step 6, `haze_tex.atlas_uv`).

---

## 3. BNG's rig — the exact numbers

50 bones. The **name** is the identity: the game's humanoid avatar matches on names, so the
names below are not cosmetic.

```
  0 Root             parent -                rest [0, 0, 0]
  1 Hips             parent Root             rest [0, 0.8763, 0]
  2 Spine_01         parent Hips             rest [0, 0.9790, -0.0155]
  3 Spine_02         parent Spine_01         rest [0, 1.1603, -0.0065]
  4 Spine_03         parent Spine_02         rest [0, 1.3374, -0.0327]
  5 Neck             parent Spine_03         rest [0, 1.4490, -0.0242]
  6 Head             parent Neck             rest [0, 1.5691, -0.0048]
  7 Eyes             parent Head             rest [0, 1.6636, 0.1179]
  8 Eyebrows         parent Head             rest [0, 1.6968, 0.1179]
  9 Jaw              parent Head             rest [0, 1.6316, 0.0168]
 10 Clavicle_L       parent Spine_03         rest [-0.0745, 1.3921, 0.0135]
 11 Shoulder_L       parent Clavicle_L       rest [-0.1910, 1.3904, -0.0484]
 12 Elbow_L          parent Shoulder_L       rest [-0.5292, 1.3609, -0.0532]
 13 Hand_L           parent Elbow_L          rest [-0.7996, 1.3609, -0.0342]
 14 Thumb_01         parent Hand_L           rest [-0.8348, 1.3492, 0.0109]
 15 Thumb_02         parent Thumb_01         rest [-0.8877, 1.3376, 0.0443]
 16 Thumb_03         parent Thumb_02         rest [-0.9358, 1.3119, 0.0599]
 17 IndexFinger_01   parent Hand_L           rest [-0.9036, 1.3649, -0.0074]
 18 IndexFinger_02   parent IndexFinger_01   rest [-0.9442, 1.3646, -0.0021]
 19 IndexFinger_03   parent IndexFinger_02   rest [-0.9811, 1.3568, 0.0020]
 20 IndexFinger_04   parent IndexFinger_03   rest [-1.0169, 1.3450, 0.0040]
 21 Finger_01        parent Hand_L           rest [-0.8995, 1.3598, -0.0670]
 22 Finger_02        parent Finger_01        rest [-0.9415, 1.3550, -0.0707]
 23 Finger_03        parent Finger_02        rest [-0.9762, 1.3495, -0.0724]
 24 Finger_04        parent Finger_03        rest [-1.0039, 1.3395, -0.0779]
 25 Clavicle_R       parent Spine_03         rest [ 0.0745, 1.3921, 0.0135]
 26 Shoulder_R       parent Clavicle_R       rest [ 0.1910, 1.3904, -0.0484]
 27 Elbow_R          parent Shoulder_R       rest [ 0.5292, 1.3609, -0.0532]
 28 Hand_R           parent Elbow_R          rest [ 0.7996, 1.3609, -0.0342]
 29 Thumb_01 1       parent Hand_R           rest [ 0.8348, 1.3492, 0.0109]
 30 Thumb_02 1       parent Thumb_01 1       rest [ 0.8877, 1.3376, 0.0443]
 31 Thumb_03 1       parent Thumb_02 1       rest [ 0.9358, 1.3120, 0.0599]
 32 IndexFinger_01 1 parent Hand_R           rest [ 0.9036, 1.3649, -0.0074]
 33 IndexFinger_02 1 parent IndexFinger_01 1 rest [ 0.9442, 1.3646, -0.0021]
 34 IndexFinger_03 1 parent IndexFinger_02 1 rest [ 0.9811, 1.3568, 0.0020]
 35 IndexFinger_04 1 parent IndexFinger_03 1 rest [ 1.0169, 1.3450, 0.0040]
 36 Finger_01 1      parent Hand_R           rest [ 0.8995, 1.3598, -0.0670]
 37 Finger_02 1      parent Finger_01 1      rest [ 0.9415, 1.3550, -0.0707]
 38 Finger_03 1      parent Finger_02 1      rest [ 0.9762, 1.3495, -0.0724]
 39 Finger_04 1      parent Finger_03 1      rest [ 1.0039, 1.3395, -0.0779]
 40 UpperLeg_R       parent Hips             rest [ 0.0990, 0.8316, -0.0205]
 41 LowerLeg_R       parent UpperLeg_R       rest [ 0.1116, 0.4325, -0.0205]
 42 Ankle_R          parent LowerLeg_R       rest [ 0.1144, 0.0557, -0.0369]
 43 Ball_R           parent Ankle_R          rest [ 0.1144, -0.0030, 0.0595]
 44 Toes_R           parent Ball_R           rest [ 0.1144, -0.0030, 0.1325]
 45 UpperLeg_L       parent Hips             rest [-0.0990, 0.8316, -0.0205]
 46 LowerLeg_L       parent UpperLeg_L       rest [-0.1116, 0.4325, -0.0205]
 47 Ankle_L          parent LowerLeg_L       rest [-0.1144, 0.0557, -0.0369]
 48 Ball_L           parent Ankle_L          rest [-0.1144, -0.0030, 0.0595]
 49 Toes_L           parent Ball_L           rest [-0.1144, -0.0030, 0.1325]
```

Three facts about these names that break code if forgotten:

1. **BNG's right-hand finger bones carry a trailing space and a `1`**: `Thumb_01 1`, `IndexFinger_02 1`. There is no `_R` variant. `fRANK` uses `.001` instead of ` 1` in some rigs.
2. **Bones with zero weight in the shipped mesh** (measured, not assumed): `Root`, `IndexFinger_04`, `Finger_04`, `IndexFinger_04 1`, `Finger_04 1`, `Toes_L`, `Toes_R`. They still need a position, but their position cannot dent the skin.
3. **BNG's rest pose is a T-pose, +5°**: `Shoulder_L → Elbow_L` is `(-0.996, -0.087, -0.014)` — 5° below horizontal. `Ankle_L → Ball_L` is `(0, -0.52, 0.854)` — the foot points forward and down.

The 50-bone rig's *orientations* are what the humanoid avatar measures its muscle curves
against. The pipeline therefore **never changes a bone's rotation**: `rig.new_world()` copies
the side-car's local rotations verbatim and only solves the local *positions*. That is why
"the bone orientations are bit-identical to the stock rig" is a stated requirement in
`RIGGING_PLAN.md`, and why `_check_preview.py` asserts the worst bone-rotation error is
`2.56e-07`.

---

## 4. The GUI: 17 clicks, 50 bones

`rigger/index.html` + `rigger/rigger.js` + `rigger/_rig_lib.js`.

### 4.1 The anchors

A rigger does not place 50 bones. The page asks for **17 anchors**, and the other 33 are
derived from BNG's own proportions. The anchors (`_rig_lib.js`, `ANCHORS`):

```
root  hips  neck  head  jaw
clav  shoulder  elbow  hand  thumb  index  middle
upleg  knee  ankle  ball  toes
```

Anchors with `side: 1` have a twin. **The user clicks the LEFT one; the right one is mirrored
across X** (`(-x, y, z)`), unless the user places it explicitly (the Symmetry button turns
the mirroring off, which is how an asymmetric character is rigged).

### 4.2 The derivation (`deriveBones()` in `_rig_lib.js`)

`rest` is `bng_rest.json` (BNG's rest positions). `k` is a scale factor.

1. **The anchors themselves**, plus their mirrors.
2. **The spine.** `Spine_01/02/03` sit at fixed fractions of the line `Hips → Neck`. The
   fractions are *read off the rest pose*, not hard-coded:
   ```
   f(name) = clamp( (rest[name] - rest.Hips) · (rest.Neck - rest.Hips) / |rest.Neck - rest.Hips|² , 0.02, 0.98 )
   bone = Hips + f * (Neck - Hips)
   ```
   For BNG's own rig: `f(Spine_01) = 0.18`, `f(Spine_02) = 0.50`, `f(Spine_03) = 0.81`.
3. **The face markers** `Eyes`, `Eyebrows`, `Jaw`: fixed offsets off the head, scaled by the
   head's size on this model:
   ```
   k = |Head - Neck| / |rest.Head - rest.Neck|
   bone = Head + k * (rest[bone] - rest.Head)
   ```
4. **The fingers.** For each digit chain, the joints past the knuckle keep BNG's shape scaled
   to this hand:
   ```
   k = |base - Hand| / |rest[base] - rest[Hand]|
   bone = prev + k * (rest[bone] - rest[prev])          # walked along the chain
   ```
   Chains: thumb `[Thumb_02, Thumb_03]`, index `[IndexFinger_02..04]`, middle `[Finger_02..04]`.
   BNG has **three** digit chains, not five: the ring and little fingers of a five-fingered
   model are weighted onto the middle chain, exactly as the game's own mitt hands are built.
5. **The toes**, the same way: `Toes = Ball + k * (rest.Toes - rest.Ball)`,
   `k = |Ball - Ankle| / |rest.Ball - rest.Ankle|`.
6. **The root**, if it was not placed: `(Hips.x, 0, Hips.z)` — on the ground under the pelvis.
7. **Anything still unplaced falls back to its parent** — the least wrong answer available.

### 4.3 Where the numbers live

**World space, not model space** (`setAnchor`, `anchorWorld`). The page shows the model
already mirrored and dropped, so the number under the pointer *is* the number the bake needs.
Stored rounded to 1e-6 (`round6`).

### 4.4 Auto-place

`autoPlace()` matches the model's own joints to the anchors by name, using `ALIASES`:
`root→root|armature`, `hips→pelvis|hips`, `neck→neck_01`, `head→head`, `jaw→c_jaw`,
`clav→clavicle_l`, `shoulder→upperarm_l`, `elbow→lowerarm_l`, `hand→hand_l`,
`thumb→thumb_01_l`, `index→index_01_l`, `middle→middle_01_l`, `upleg→thigh_l`,
`knee→calf_l`, `ankle→foot_l`, `ball→ball_l`, `toes→toe_l`. Names are lower-cased, and a
`.001`-style suffix is stripped as a fallback key.

A model with no rig gets no auto-place; the user clicks all 17.

### 4.5 The export

`export_bones.py` writes the shared definitions; the *page* exports the placement as JSON:

```json
{ "model": "<name>", "bones": { "Root": [x,y,z], "Hips": [x,y,z], ... 50 entries ... } }
```

`bones` has **exactly 50** entries or the bake refuses it. Positions are in world space.

---

## 5. The Python: the bake, exactly

`rigger/preview.py::write_rigged(bones_json, out, source=..., verbose=True)`, and the shared
maths in `rig.py`.

### 5.1 `load_glb(path)` — `rig.py`

Reads the GLB (`glblib.load_glb`: magic `b"glTF"`, JSON chunk, BIN chunk). Walks the **whole
node graph** (`G.world_nodes`), so a mesh that is not at the scene root is fine.

Returns a dict:

```
pos (N,3)   nrm (N,3)   uv (N,2)     the geometry, concatenated over the primitives
jnt (N,4)   wts (N,4)               the model's own skin
tris (M,3)  prim (N,)               the triangles, and which primitive each vertex came from
names []    parent {}   world (K,4,4) ibm (K,4,4)   the joints
```

* A **skinned** mesh: its node transform is ignored (glTF spec). The joint world matrices
  come from the node tree; the inverse bind matrices are read from the accessor and
  **transposed** (glTF stores column-major).
* An **unskinned** mesh: the joint tables are empty (`names = []`, `parent = {}`) and the node's
  world matrix is baked into the vertices by `_place()` (positions and normals; the winding is
  reversed if `det < 0`).
* `JOINTS_0` / `WEIGHTS_0` missing → zeros. No skin at all is **not** an error.

### 5.2 `flip_x(src)` — `rig.py`

`F = diag(-1,1,1,1)`; positions and normals × `diag(-1,1,1)`; `tris[:, ::-1]`;
`world → F world F`; `ibm → F ibm F`.

### 5.3 `repose(src, targets)` — `rig.py` (rigged models only)

`targets = R.pose_targets(bng)` — **12 chains, 6 per side**, with the direction read off
BNG's own rig:

```
(clavicle_l, upperarm_l)  (upperarm_l, lowerarm_l)  (lowerarm_l, hand_l)
(thigh_l, calf_l)         (calf_l, foot_l)           (foot_l, ball_l)
   and the same for _r
```

For each `(joint, child, direction)`: the smallest rotation `R` taking the current direction
(`world[child] - world[joint]`) onto the target, applied to the joint's **whole subtree** about
the joint's own origin:

```
D = [ R  p - R p ]        world[k] ← D · world[k]   for every k in the subtree
```

Then the mesh is re-skinned with the model's own weights — the ordinary LBS equation with the
pose change as the delta:

```
v' = Σ_k w_k · (W'_k · IBM_k) · v          (W' = the re-posed world matrix, IBM = the bind)
```

Nothing is resampled, nothing is rescaled: the proportions are bit-identical.

A model with **no rig** skips this entirely (`posed_source()`: `if src["names"]`).

### 5.4 `ground(src, bones=None)` — `rig.py`

`drop = min(pos.y)`; the geometry, the joints and (optionally) an array of bone positions are
all moved down by `drop`. Measured on real models: `icey.glb` drops **−2.9 mm**; the dyed
model drops **−0.2 mm** and later **97.8 mm** after it has been re-posed.

> The page must use the **precise** bounding box: `new THREE.Box3().setFromObject(root, true)`.
> The non-precise box includes the shape keys and inflates the bounds — 74 mm instead of 2.9 mm
> on `icey.glb`.

### 5.5 `bone_targets(src, rig, overrides)` — `rig.py` (rigged models, and the fallback)

Reads the 50 bone positions off the model's own joints:

* the spine is **arc-length resampled**: BNG's three spine bones are spread along the model's
  spine by the same fractions BNG uses (0.18 / 0.50 / 0.81 of the hips→neck line);
* everything else is a name match (`MODEL_JOINT`), with the fingers and toes **extrapolated**
  along the chain when the model has fewer joints than BNG;
* `Eyes` and `Eyebrows` are the midpoint of the model's two eyes / two brows;
* a bone that cannot be placed **falls back to its parent's position** — never to the origin
  (a zero-length bone means a nonsense bindpose and the mesh explodes around it).

`overrides` is the GUI's JSON and always wins.

### 5.6 `bind(src, bng, P)` — `rig.py` — the heart of the bake

```
world, local = new_world(bng, P)          # BNG's rotations, P's positions
weights:
    if the model has a rig:
        reindex_weights(src, names)
        if orphan > 5%  ->  generate_weights(...)     # the model's names meant nothing here
    else:
        generate_weights(...)
if the weights were generated:
    aim(...)        # the chains to BNG's T-pose, the mesh carried along
    align_mesh(...) # the mesh straightened onto the bones
ground(src, [P])
world, local = new_world(bng, P)
bind = bindposes(world)   # inv(world)
```

#### 5.6.1 `reindex_weights(src, bng_names)` — the model's own weights, re-pointed

* `_map_joints` builds `joint → BNG bone index` by name (`pelvis→Hips`, `upperarm_l→Shoulder_L`,
  `thigh_l→UpperLeg_L`, `c_jaw→Jaw`, `spine_0N→Spine_0min(N,3)`, the model's `*_r` joints to
  BNG's ` 1`-suffixed right-hand bones).
* A joint with no counterpart **folds onto its nearest mapped ancestor** (a twist bone or a
  cloth bone belongs to the body part it hangs off).
* The four influences are looked up and **merged** (several joints sharing one BNG bone add
  up), the four largest are kept, and the row is normalised.
* `rep["orphan"]` = the fraction of vertices that ended up with **no** weight at all. If that
  is **> 0.05**, the model's own skin is not usable and the fitted weights are used instead.

#### 5.6.2 `generate_weights(src, P, parent, names, skip=("Root",), falloff=3.0, reach=2.5)`

For a model with no skin, the weights are invented:

* the distance from a vertex to a bone is the distance to that bone's **segment** — the line
  from the bone's parent joint to the bone itself — so a forearm claims the forearm and not
  the elbow;
* the four nearest bones are taken;
* `w = 1 / max(d, 1e-4) ** 3.0` — inverse distance cubed;
* a bone is cut off when `d > 2.5 × d_nearest`, so a bone on the far side of a thin waist cannot
  creep in;
* the row is normalised to 1;
* `Root` is skipped (BNG gives it no weight).

Measured on `icey.glb`: 1,152 verts, 50 joints, weights sum 1.0 ± 1e-5, ≤ 4 influences,
bindposes exact (`0.00e+00`).

#### 5.6.3 `aim(world, parent, targets, mesh, tolerance=0.5, iterations=4)` — the pose fix

**This is the step that makes a skinless model work.** The reason it exists:

> Every clip the game plays is written against BNG's T-pose, and a clip's rotation is a
> **delta** from that pose. A mesh bound in an A-pose therefore has the clip's swing *added to
> its own arm angle*, and the arms end up behind the back. The user's screenshot of the dyed
> model showed exactly that: the bake's `Shoulder_L → Elbow_L` was **42.3°** off BNG's, the
> `Elbow_L → Hand_L` **41.7°** off and the `Ankle_L → Ball_L` **54.4°** off.

The aim turns each chain onto BNG's direction:

```
targets: 10 chains, 5 per side
   Shoulder→Elbow, Elbow→Hand, UpperLeg→LowerLeg, LowerLeg→Ankle, Ankle→Ball
   (the clavicle is NOT aimed: its geometry is the whole chest, and turning the shoulder root
    away from the anatomy would drag the arm with it)
direction = BNG's own (read off rig["world"], so it is BNG's, not a guess)
```

For each chain: the smallest rotation `R` from the chain's **current** direction to BNG's,
applied to the bone's whole subtree about the bone's origin, and the mesh is carried by the
same weights that will bind it:

```
D = [ R  p - R p ]         world[k] ← D · world[k]   (k in the subtree)
v' = Σ_k w_k (W'_k W_k^-1) v
```

**4 iterations**, because the skin blends the joints: the geometry does not turn quite as far
as the bone does, so each pass measures what is left to do.

#### 5.6.4 `align_mesh(pos, nrm, wts, jnt, world, parent, chains, iterations=4, tolerance=1.0)`

After the aim, the geometry is turned onto the bones **without moving the bones** — the
straightening the blend left behind, so the bind shape reads as the T-pose the game expects
while the joints stay exactly where the artist put them.

The direction is measured from the geometry: the **centre of influence** of a bone is the
weight-weighted mean of the vertices it drives,

```
c(bone) = Σ_v (Σ_k w_vk [joint_vk == bone]) · v  /  Σ_v (Σ_k w_vk [joint_vk == bone])
```

and the chain's direction is `c(child) - c(parent)`.

#### 5.6.5 `new_world(rig, positions)` — the bone matrices

A bone's world position is its parent's world position plus the parent's world rotation times
its local position, so the local position that lands a bone on its target is:

```
lp = R_parent^T · (target_i - target_parent)
L  = [ rig.local[i].R   lp ]        # the bone's OWN rotation, copied verbatim
world_i = world_parent · L
```

The rotations are **not** touched. That is what lets the game's humanoid avatar retarget the
rig, and it is what `_check_preview.py` verifies (worst rotation error `2.56e-07`).

#### 5.6.6 `bindposes(world)` — `inv(world)`, 50 matrices, one per bone, in bone order.

### 5.7 The measured result on the dyed model (`rigger/uploads/7ef41cfb87ef-dyed.glb`)

The model: 1,905 verts, 2,005 tris, **4 primitives, no skin**, 8 UV sets, COLOR_0, a node
scale of (1.197, 1, 1.053).

| | before | after |
|---|---|---|
| `Shoulder_L → Elbow_L` vs BNG | 42.3° | **0.0°** |
| `Elbow_L → Hand_L` vs BNG | 41.7° | **0.0°** |
| `Ankle_L → Ball_L` vs BNG | 54.4° | **0.0°** |
| the arm's direction **in the mesh** | 45° below horizontal | 3.7–4.7° from BNG's |
| the elbow's angle **in the mesh** | 145° | 171° (straight is 180°) |
| the foot's direction in the mesh | — | 0.1° from BNG's |
| the mesh's height | 1.684 m | 1.702 m, soles on `y = 0` |
| the bindposes | — | `|bindpose - inv(boneWorld)| = 0.00e+00` |

### 5.8 The output GLB (`preview.Glb`)

* node 0 = the container; nodes 1..50 = the bones, in BNG's order, each with
  `translation` + `rotation` (Shepperd's method, `mat_to_quat`);
* node 51 = the mesh, `skin: 0`;
* the skin's `joints` = the 50 bone nodes; `skeleton` = the root bone;
* `inverseBindMatrices` = `bind[i].T` (glTF is column-major), one `MAT4` accessor;
* the mesh: `POSITION`, `NORMAL`, `TEXCOORD_0`, `JOINTS_0` (uint16 VEC4), `WEIGHTS_0`
  (float32 VEC4), one primitive per source primitive, `material: i` per primitive.

---

## 6. The server and the two modes

`rigger/serve.py`, on `http://127.0.0.1:8899`, run from the project root (it does
`os.chdir(ROOT)` because the rig side-car is read relative to the root).

```
GET  /                    the page
GET  /model.glb           the mesh the page displays
GET  /uploads/<hash>.glb  an uploaded model
GET  /out/<name>.glb      a baked rig
POST /api/model           raw .glb bytes -> store it and say how it can be baked
POST /api/bake            {model, bones} -> bake a skinned glb, return its url
```

### `POST /api/model?name=<file>.glb`

Body: the raw bytes. Limit: **256 MB**. Stored at `rigger/uploads/<sha256[:12]>-<stem>.glb`
— content-addressed, so re-uploading the same file is free and the name is stable.

The server answers what the page should do next:

| answer | when | what the page does |
|---|---|---|
| `mode: "posed"` + `display` + `anchors` | the model has a rig whose names match BNG's re-pose targets (≥ 1 of 12) | load the server's **re-posed** copy; the re-pose is not rigid, so the clicks must be on the re-posed mesh |
| `mode: "raw"` | anything else | mirror in X and drop to the floor client-side (`prepareInPlace()`) — both rigid, so the page can do them itself |

Also returned: `skin` (bool), `joints` (count), `aimed` (how many re-pose targets matched),
`path` (the stored path), and `prepareError` if the posed path failed (the server then falls
back to `raw`).

### `POST /api/bake`

Body: `{"model": "<path>", "bones": {<50 names>: [x,y,z]}}`.

* `model` is resolved by `safe_relative()`: looked up next to the page first, then from the
  project root, must end in `.glb`, must exist. **No fallback to any other model** — a bake
  that quietly rigged a different character is worse than an error.
* `bones` must be a dict of exactly 50 entries.
* It runs `preview.write_rigged` in-process, with the library's chatter captured
  (`_quiet` returns `(result, log)`) and returned in the response.

Response: `{url, name, verts, bones, weights, log, mtime}` where `weights` is
`"reindexed"` or `"generated"`.

### The anchors side-car

`preview.write_model()` writes `<stem>_anchors.json` next to the display GLB:

```json
{ "model": "...", "anchors": { "<anchor id>": [x,y,z] }, "bones": { "<bone name>": [x,y,z] } }
```

The page fetches it as `url.replace(/\.glb$/, "") + "_anchors.json"`. It is only trustworthy
when the model's joint names actually matched — `bone_targets` collapses everything onto Hips
when nothing matches, so a side-car from a mismatched rig is worse than none.

---

## 7. Into the bundle

The browser is a preview. The shipping path is Python.

### 7.1 Finding the slot

`characters.py` (new) lists every `SkinnedMeshRenderer` in the bundle and, for each, the
mesh, the GameObject, the bones (in the renderer's order, with their names), the materials,
the `_BaseMap` texture, the vertex count, the channel set, the index format and the submesh
count. **Match on the MESH name**, not the GameObject's: several characters share a
GameObject (`Character_Male_FBI` and `Character_Female_FBI` both live on
`SM_Chr_GangMember_Male_03`).

In the shipped bundle: 145 skinned renderers, 24 of them with vertex data. The usable ones:

```
SM_Chr_Inspector_Female_01   50 bones  4,906 verts   16-bit  1 submesh
SM_Chr_Inspector_Male_01      50 bones  3,503 verts
SM_Chr_Builder_Male_01        50 bones  3,988 verts   (and the whole Builder family)
SM_Chr_Farmer_Male_01         49 bones  3,031 verts   (49 = BNG's 50 without "Jaw")
SM_Chr_FarmGirl_01            49 bones  4,703 verts
...
```

A 49-bone slot has the same names minus `Jaw`; the bindposes must be built for **the slot's own
bone list**, not for BNG's 50.

### 7.2 The mesh layout (`skinlib.py`)

Unity's pre-2019.3 `VertexData`: `m_Channels` describes the layout, `m_DataSize` is the
streams laid end to end. For a character body:

```
stream 0: position float32 x3 @0,  normal float32 x3 @12                     24 B/vertex
stream 1: uv0      float32 x2 @0                                          8 B/vertex
stream 2: weights  float32 x4 @0,  blendIndices uint32 x4 @16              32 B/vertex
                                                                          = 64 B/vertex
```

**Every stream starts on a 16-byte boundary** and only the last stream is unpadded. A
15,697-vertex mesh written without the padding puts stream 1 eight bytes early, and the engine
then reads the UVs and the blend weights from shifted bytes — the character skins to
somewhere off-screen. `skinlib.stream_spans()` rounds every stream but the last up to 16.

* `rebuild(mesh, arrays)` writes a mesh with a **different vertex count**; a channel the
  caller does not supply comes out **zeroed** and is reported back. `build_haze.py` refuses to
  save if anything was left zeroed.
* `set_geometry(mesh, tris, aabb)` writes the index buffer and the culling bounds. The index
  buffer is **16-bit**: a model must stay under **65,535 vertices** or it has to be decimated
  or split.

### 7.3 Writing the bones and the mesh

`install_rig.write_bones(env, bones, names, rig, positions)` — each bone Transform's
`m_LocalPosition` is set to the solved local position; **the rotation is not touched**.

`install_rig.write_mesh(env, mesh_pid, rig, positions, arrays)` — the geometry, the weights,
the indices, the 50 bindposes (each `e{r}{c}` written explicitly) and the per-bone
`m_BonesAABB` the renderer culls against.

> Everything goes through the **wrapped object** (`.read()` / `.save()`), never the typetree:
> each re-serialises from its own parsed copy, so writing the vertex data one way and the
> bindposes the other loses one of the two.

### 7.4 The texture

The game draws a character body with **one** material and **one** `_BaseMap` atlas, so the
model's own maps have to be packed into one page and each primitive's UVs moved into its own
region.

* `haze_tex.py` does this for Haze: a fixed 2048×2048 layout, body 1024², hair 1024², head
  2048×1024, an 8-texel gutter, and **the whole region pasted first, then the inset copy on
  top** — so the padding holds the art's own edge pixels and the seams do not fringe.
* Mips: `mip_count = max(1, log2(size) - 2)` = **9** at 2048. Past mip 7 each region is a few
  texels wide and the regions bleed into each other.
* The clone is written as **DXT1** (`TextureFormat` 10). The stock sheet is DXT1 *crunched*
  (format 28), which UnityPy can decode but not write back; DXT1 is the same codec without the
  crunching, and the atlas has no alpha to preserve.
* The material is **cloned**, and only the clone's `_BaseMap` is repointed: the stock material
  `PolygonConstruction_Mat_01_A` is used by **eighteen** objects, and editing it would recolour
  the whole construction set.
* The renderer is repointed at the cloned material.

---

## 8. What must never happen

1. **Never substitute another model.** An earlier version baked Haze whenever the uploaded
   model had no skin. The bake now either uses the file the page is showing or fails.
2. **Never warp or rescale the geometry.** The mesh keeps the artist's proportions; only the
   bones move. The one exception is the re-pose, which is a rigid rotation of a subtree.
3. **Never change a bone's orientation.** The avatar measures its muscle curves against BNG's
   rest pose.
4. **Never let a bone collapse to the origin.** A zero-length bone means a nonsense bindpose.
   The fallback is the parent's position.
5. **Never leave a channel zeroed.** `rebuild()` reports it; the build refuses to save.
6. **Never write the mesh without the matching bindposes**, and never write the vertex data
   and the bindposes through different APIs.

---

## 9. Known limits (as of this writing)

* **Shape keys / morph targets are dropped** by the bake. `icey.glb` has 2, `dyed.glb` has
  none, Haze has none. A model with face blendshapes loses them.
* The preview shows the model **mirrored** into Unity's handedness, so an asymmetric character
  looks flipped in the page.
* `.gltf` + an external `.bin` is refused — `glblib.load_glb` requires the `glTF` magic.
* A 49-bone slot (BNG's 50 without `Jaw`) needs its bindposes built for its own bone list.
* The 16-bit index buffer caps a model at 65,535 vertices.

---

## 10. The generic "any model → custom character" pipeline (in progress)

The goal: any GLB, rigged or not, becomes a character in BuildNow GG, not just Haze.

```
model.glb
  └─ rig.py         flip_x → repose (if rigged) → ground → bone_targets / GUI JSON
  └─ rig.bind      weights (reindexed or generated) → aim → align_mesh → bindposes
  └─ atlas.py      the model's own maps → one 2048² atlas → per-primitive UV remap
  └─ characters.py the slot in the bundle: renderer, mesh, bones, material, texture
  └─ install_rig   write_bones + write_mesh into the bundle
  └─ build_character.py   clone the texture, clone the material, repoint the renderer
  └─ verify        read the bundle back: geometry, weights, bindposes, AABB, preservation
  └─ deploy        local/buildnow-gg/225/Build/StreamingAssets/aa/WebGL/
```

`characters.py` exists; `atlas.py` and the CLI that drives the whole chain are the next
pieces. The rule that governs all of them: the *model* is the truth about itself, BNG's rig
is the truth about how the game animates, and nothing in the middle may quietly replace
either.
