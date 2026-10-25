# Haze skin: rebuild the rigging the Mixamo way

## What was wrong

`retarget.py` + `weightxfer.py` made the model's GEOMETRY fit BNG's skeleton:

* the torso was resampled by arc length and every bone segment rescaled to BNG's bone length — a
  per-bone, per-axis rewrite of the mesh. The shoulder band came out x1.29 and the thigh x0.867
  of what the artist authored: the "tall hips and waist".
* BNG's OWN body weights were then copied onto the model, vertex by vertex, by nearest point on a
  DIFFERENT character's mesh. 10,035 of 10,771 body vertices changed which bone drives them.
* the mesh was finally re-laid through those borrowed weights, which moved the rest shape by up
  to 1.604 m.

Three deformations of one model, in three directions. The result was the monster.

## What this does instead

The model already ships a professional rig: 192 joints, four weights per vertex, artist-authored.
That rig is the truth about the model. So:

1. **FLIP X.** The source is a glTF (right-handed, character's left at +X); BNG is Unity
   (left-handed, character's left at -X). The two conventions disagree about which side is
   which, and the old name map had to paper over it by swapping every _l/_r. Flipping the mesh
   once, here, makes every bone name line up with BNG's with no exceptions.
2. **RE-POSE A-pose -> T-pose, with the model's own weights.** The source stands in an A-pose
   (shoulder->elbow 41 degrees below horizontal); BNG's rig binds in a T-pose (+5 degrees). The
   re-pose is a plain linear blend skinning operation against the model's own joints, so her
   proportions do not change — her arm does not get longer or shorter by a millimetre.
3. **SUBSAMPLE** her 192 joints onto BNG's 50 bones. Each BNG bone takes the POSITION of the
   matching joint on the re-posed model; its ROTATION stays exactly BNG's, so every bone's
   orientation in the world is bit-identical to the stock rig — which is what lets the game's
   humanoid avatar retarget it.
4. **RE-INDEX her weights.** Her weights are already correct for her mesh; only the bone indices
   change (a joint becomes the BNG bone it corresponds to; joints with no counterpart fold onto
   their nearest mapped ancestor). Nothing is measured against another character, and nothing is
   invented.
5. **BINDPOSES** from the new bone positions, so the mesh renders exactly as authored.

The result: she keeps her own proportions, sits on her own joints, and is animated by the game's
own clips. Verified: her limb lengths change by 0.00%, the median mesh edge ratio is 1.000, and
the head and hair are untouched by the re-pose.

## The web GUI

`rigger/index.html` — a three.js tool: import the custom model, pick a bone, click on the mesh to
put it where you want it. The click is raycast onto the mesh, so a click IS a position on the
surface. It writes the JSON that `build_haze.py` reads (`BW_BONES=`), so the auto-derived
skeleton is a starting point the user can correct by hand — the same loop the game's own
auto-rigger does.

## Files

| file | what it is |
|---|---|
| `rig.py` | the core: load the model, mirror, re-pose, place the bones, re-index the weights, bindposes |
| `install_rig.py` | write the bones and the mesh into the bundle |
| `build_haze.py` | drives the above, plus the atlas/UV step, and verifies what it wrote |
| `check_rig.py` | offline proof renders, at rest and posed |
| `compare_pose.py` | before/after proof that the re-pose does not change her proportions |
| `rigger/` | the three.js marker-placement GUI |
