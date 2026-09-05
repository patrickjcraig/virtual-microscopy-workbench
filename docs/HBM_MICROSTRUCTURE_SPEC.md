# Next increment: explicit HBM microstructure in a bounded ROI

Status: implementation specification, prepared 4 September 2026 against the v0.6 working tree. This document does not report implemented microstructure or experimentally validated dimensions. It narrows the next autonomous build loop from the broader [expansion plan](EXPANSION_PLAN.md).

## Recommended deliverable

Add one editable, component-local patch of explicit inter-die bumps and through-silicon via (TSV) cylinders to one selected HBM assembly, with a small list of targeted synthetic defects. Start with a **2-column by 3-row patch spanning the existing eight DRAM dies and base die**. Retain all six physical HBM sites, their existing layer geometry, the rest of the package, and the selected site's separate electrical-state metadata.

The working demonstration is: choose HBM 6, inspect the explicit patch in material sections and a close 3D view, replace one bump with epoxy or introduce a void, acquire a normal-incidence X-ray ROI preview and a saved SAM RF ROI, then inspect or depth-map that saved SAM data using the existing workflows. All geometry and defect parameters must survive twin export/import and saved acquisition snapshots.

Use the existing Pydantic, NumPy/SciPy, Three.js, Zarr and process-worker stack. Expanded ordinary primitives are sufficient for this bounded deliverable. Full-array procedural geometry, high-resolution all-angle local CT, new materials, liner/passivation films, electrical connectivity and full-wave acoustics are outside this increment.

## What the current code actually supports

| Current implementation | Consequence for this increment |
| --- | --- |
| `hbm.py:compile_hbm_stack()` emits a base, silicon DRAM layers, epoxy gaps/cap, and three coarse attachment contacts. An eight-high stack has 22 primitives. | Preserve those layers and attachment contacts. Add resolved inter-die structure inside the silicon/gap volumes. The three package attachment contacts remain explicitly coarse. |
| `schemas.py:Twin.objects` permits at most 600 ordered primitives. The supplied H100 JSON currently has **472**: 340 non-assembly objects plus 22 objects at each of six HBM sites. | Only 128 additional primitives fit the current default twin. Raising the cap is unnecessary for a small patch and would not solve voxel or signal-workspace limits. |
| `validate_hbm_geometry()` requires a deterministic, contiguous compiled block per assembly. `compose_hbm()` replaces that block in place, preserving later global defects. | Generated microstructure and its local defect overlays must join the same canonical assembly block. Imports must reject disagreement between parameters and expanded geometry. |
| `physics.py:voxelize()` samples ordered primitive occupancy at voxel centers in `[y,x,z]`; later objects overwrite earlier ones. | Existing vertical cylinders represent the first bump/TSV approximation. No mesh Boolean library is required. Thin geometry can disappear, and its visibility in Three.js proves nothing about sampled occupancy. |
| XY ROI voxelization retains the complete specimen Z extent and clips objects against a padded XY domain. Preview and saved SAM retain Gaussian lateral context. | Reuse full-depth ROI acquisition. Do not turn the microstructure patch into an isolated specimen or reset its global origin/time zero. |
| Saved X-ray geometry is currently a uniform grid over the whole specimen; detector crop/offset does not refine it. | The fine microstructure demonstration uses the existing zero-angle ROI preview and saved SAM. A tightly cropped saved X-ray detector must not be presented as a high-resolution material ROI. |
| `inspection.py:material_section()` uses the stack-center plane and full stack width. `scene.js:TwinViewer` limits zoom relative to the whole package and creates one mesh per primitive. | Add feature-centered section bounds/plane selection and a close-view camera mode. Whole-stack sections and whole-package camera limits cannot inspect a 10 µm feature adequately. |

The object count above is an inventory of the current local JSON, not a published H100 construction count. Recompute it from each edited/imported twin.

## Geometry and parameter contract

Add an optional, strict `microstructure` record to an HBM assembly and its parameter-update schema. Absence preserves today's compiled objects exactly. Permit one enabled patch across the twin in this first increment; moving it to another site is an explicit edit. A physically absent assembly cannot enable a patch. Electrical `functional_state` has no effect on any material occupancy.

Proposed fields and initial authoring bounds:

| Field | Meaning and first supported behavior |
| --- | --- |
| `model_version` | Fixed compiler contract identifier, for example `hbm-explicit-patch-1`. |
| `enabled` | Boolean controlling compilation of this patch and its local defects. Disabling preserves its authored parameters for re-enabling. |
| `center_offset_xy_um` | Patch center relative to the assembly's XY center. Coordinates are signed local offsets; generated primitives remain global millimetres. |
| `columns`, `rows` | Positive integers, individually at most 8; actual supported combinations are constrained by the 600-object budget below. Default 2 and 3. |
| `pitch_x_um`, `pitch_y_um` | Positive center spacing. Suggested initial synthetic preset: 50 µm in each direction. |
| `bump_diameter_um` | Suggested synthetic preset: 25 µm. Height is exactly the existing inter-die `gap_um`; it is not an independent conflicting layer height. |
| `tsv_diameter_um` | Suggested synthetic preset: 10 µm. Each silicon die receives its own cylinder spanning that die's existing thickness. |
| `defects` | At most four component-local defect records, described below. |
| `evidence`, `source_note` | Required explanation of assumed or supplied parameters. Default explicitly says synthetic, uncalibrated geometry. |

These are **chosen simulation defaults**, not asserted H100/HBM3 feature dimensions. User edits must remain finite and positive; reject overlapping nominal neighboring cylinders (`diameter >= pitch`), patches crossing the selected footprint, and any generated primitive outside specimen bounds. Keep a small positive spacing between adjacent nominal features. Do not silently shrink or drop rows to fit.

Compile each lattice position deterministically:

1. One solder-proxy cylinder in every inter-die epoxy gap, centered on the gap with its full height. The existing `gap-01` joins the base and first DRAM die; indexing increases away from the base toward the cap.
2. One copper-proxy TSV cylinder through the base and each DRAM die, aligned with that lattice position. No TSV extends through the cap or the coarse package attachment region.
3. Local defect overlays after all nominal layers and connections. Later unrelated specimen defects retain their current precedence.

Define local coordinates with zero XY at the assembly center. Derive Z extents from the same layer-boundary calculation used by the existing compiler. Stable IDs must identify assembly, feature family, layer, row and column, for example `hbm-6-mb-03-r01-c02`. Add `microbump` and `tsv` to the strict layer-role vocabulary and retain the canonical association for defect overlays. Compiler ordering and identifiers must not depend on dictionary iteration or rendering order.

For `N = rows × columns` and `D = die_count`, the extra nominal primitive count is:

```text
N × D bump cylinders + N × (D + 1) TSV cylinders = N × (2D + 1)
```

The default eight-high 2×3 patch adds **48 bumps + 54 TSVs = 102** primitives. Starting from 472 objects gives 574, or 578 with four defect overlays. A twelve-high 2×2 patch adds 100 primitives; changing one current eight-high assembly to twelve-high also adds eight layer primitives, so that specific configuration totals 580 before defects. Other edited twins can have less room. Validate the **entire newly composed twin**, display its actual count and remaining capacity, and reject overflow atomically while preserving the prior twin.

## Component-local defects

Start with three explicitly different constructions; preserve the underlying nominal primitive so `include_defects=false` restores it:

| Defect | Target and generated overlay | Interpretation |
| --- | --- | --- |
| `missing_bump` | One indexed bump; epoxy cylinder with the same bounds replaces its solder occupancy. | Missing solder with the gap filled by the existing epoxy proxy. This is not automatically an air-filled crack. |
| `bump_void` | One indexed bump; centered air sphere contained strictly inside it. | Assumed sealed cavity in solder. |
| `tsv_void` | One indexed die TSV; centered air sphere contained strictly inside it. | Assumed sealed cavity in copper. |

A defect record contains a stable ID, type, row/column, gap or die index, an enabled flag, and void diameter where applicable. For the first version, voids are centered and must be smaller than both the host cylinder's diameter and height. Reject duplicate enabled defect targets; avoid ambiguous overlap precedence. Display inherited global defects separately from these newly attached local defects.

Moving an assembly moves its microstructure and local defects. Layer-thickness edits recompute their Z positions from the target layer. Existing untagged, globally positioned specimen defects retain today's fixed global coordinates. A layer-count reduction that invalidates a local target must fail validation rather than retarget or discard the defect. Disabling the whole patch removes its generated structures and defect overlays while preserving its authored parameter record.

## Acquisition domain, numerical bounds and honest sampling

The microstructure rectangle is an **XY acquisition selection**, not a material cutout. Continue passing the entire twin into `voxelize()`. Preserve global XY coordinates, `z=0` at the existing specimen top, all intervening water/material columns, and material below the HBM. Preserve the larger required preview Gaussian halo and the saved-SAM acoustic halo, including geometry outside the visible ROI. This retains the surrounding paths modeled by the current normal-incidence solvers; it does not claim to retain oblique, refracted or full-wave paths.

A suitable starting synthetic scan around the 2×3, 50 µm-pitch patch is **0.12 × 0.18 mm**, **64 × 64** lateral samples, **1,024** depth samples through the complete 2.65 mm fixture. Its pitches are approximately **1.875 × 2.8125 × 2.588 µm**. These values describe sampling only. The candidate saved SAM preset is 100 MHz, 800 MHz RF sampling and a short 0.5 µs record with an explicitly displayed start time; inspect its predicted interface times and budget before choosing the delivered record window. The record start remains an acquisition setting, never an automatic redefinition of surface time. Use the existing transducer and detector response assumptions unchanged.

Two supporting numerical changes are required:

1. **Bound echo work by intersecting columns.** `estimate_sam()` currently uses `min(nz+1, 2 × all_object_count)` interfaces for every column. The 472 package objects make this excessively conservative for tiny ROIs with many halo pixels. Compute a conservative 2D upper-bound map: add two possible crossings for each included primitive's sampled XY bounding-box coverage, then clamp each column to `nz+1`. Sum that bound over each canonical tile plus halo to estimate echo workspace. A cylinder's box deliberately overestimates its support. Reuse current 160 bytes/interface and FFT-workspace allowances, and account for the new small bound-map allocation. Do not assume only visible ROI columns contribute. Verify this bound against actual echo counts for overlapping boxes, spheres, cylinders, exclusions and edge clipping before using it to admit jobs.
2. **Report numerical representation of requested features.** Preflight should report padded shape, pitch, feature diameter/height in samples, primitive count, signal output bytes, estimated peak workspace and actual returned ROI. Emit targeted warnings when a microfeature or void has fewer than two samples across any extent; a missed feature must never be described as resolved merely because its source primitive exists. Preserve existing 64-million-cell, 512 MiB saved-output/working estimates, RF-work and disk limits. An inadmissible request gets an actionable error; it is never silently downsampled.

Keep zero-angle ROI X-ray restriction in this increment. The existing saved full-angle X-ray workflow remains available at its stated full-specimen material pitch, with thin-feature warnings. The microstructure panel must clearly distinguish that from the fine normal-incidence ROI preview and must not offer “microstructure CT” by cropping a detector. A later all-angle refinement requires integrating a fine local domain and all outside attenuation exactly once along each ray; that is a separate backend change.

## Controls, sections and rendering

Extend the existing HBM editor with a Microstructure tab or compact subpanel: physical site, patch center, rows/columns, pitch, diameters, layer selection, defect target/type/size, compiled count, and sampling estimate. Keep draft/apply behavior: sections and previous acquisitions remain attached to the last applied twin until successful validation. Applying geometry marks current acquisition views stale and never changes an already saved dataset.

Add a “Scan microstructure ROI” action that sets global XY bounds and centers the probe on a chosen feature. Add feature row/column and layer selectors to material sections. Extend `HBMSectionRequest` with optional bounded lateral/depth extents and fixed coordinate, preserving the existing defaults. The section plane must pass through the selected cylinder center; the present stack-center plane can miss both columns of an even-column lattice. The renderer must use returned axes and global coordinates, and continue to label these images as material geometry.

Provide a “Focus patch” camera action with target, minimum distance and near plane based on the patch, plus “Return to package.” Keep the full six-site overview. Optional visibility/clipping in the close view affects display only and must not alter the acquisition twin. Existing meshes are enough for roughly 100 new features; if batching is helpful, group equal geometry/material cylinders into `THREE.InstancedMesh`, retain an instance-index-to-feature-ID table, update instance matrices and bounding volumes, and dispose resources on rebuild. The official [Three.js InstancedMesh documentation](https://threejs.org/docs/pages/InstancedMesh.html) describes shared geometry/material instances and the required update/bounds behavior. Check the installed Three.js version before relying on additional APIs.

Three.js currently draws overlapping source solids rather than performing Boolean subtraction. The authoritative material section and voxel labels must show missing material correctly. For 3D, hide a targeted missing-bump mesh when its defect is included; show contained voids as clearly labeled overlays/markers unless a genuine cutaway is implemented. Do not present an orange sphere drawn over a solid as evidence of a geometrically subtracted cavity. Disabling defects restores nominal rendering and sampled occupancy consistently.

## Evidence and saved-data compatibility

Retain `image_reference` unchanged: the source raster is 693×502 pixels and its **approximately 4.6 µm/pixel** scale is a user estimate, with unknown acquisition, resizing history, variant and feature identity. Under that estimate it spans about 3.19×2.31 mm; this arithmetic is not calibration. Do not derive the proposed 25 µm bump diameter, 10 µm TSV diameter, pitch or layer dimensions from that image. Keep these dimensions separately labeled as editable simulation assumptions.

Reuse the existing copper, solder, epoxy, silicon and air presets without introducing new physical-property claims. In particular, the current solder is a pure-tin proxy and the epoxy has illustrative properties, as documented in [PHYSICS.md](PHYSICS.md). The cylinders omit barriers, liners, redistribution routing, joint curvature and specimen-specific composition.

Frozen acquisition inputs must contain both the authored microstructure/local-defect parameters and their canonical expanded primitives. Include a geometry-compiler version and deterministic geometry identity in the saved provenance, and update affected solver/schema fingerprints when code changes. Resume must reject changed frozen inputs or incompatible code. Existing completed SAM, X-ray, reconstruction and depth datasets must continue to open/export without recompiling their historic geometry. Derived SAM depth still uses a declared global velocity model; introducing copper/void columns does not make that model locally exact or calibrated.

## Acceptance criteria for this loop

1. **Six-site preservation:** enabling a patch at HBM 6 leaves all six sites physically present. Other assembly blocks and unrelated package/global-defect primitives are byte-equivalent after canonical serialization. Changing electrical state does not change occupancy.
2. **Deterministic construction:** the eight-high 2×3 preset has exactly 48 new solder cylinders and 54 copper cylinders at independently calculated layer boundaries; no unintended cap/attachment occupancy. Repeated compose and export/import preserve identifiers, parameters and geometry exactly.
3. **Atomic bounds and edit behavior:** count overflow, out-of-footprint arrays, diameter/pitch conflict, invalid void size and orphaned defect targets return validation errors without modifying the active twin. Valid assembly moves and layer edits move only their attached geometry as specified.
4. **Defect semantics:** sampled missing bumps contain epoxy; contained voids contain air; defect exclusion restores the exact intact labels. An asymmetric row/column target distinguishes swapped axes and incorrect lattice indexing. Existing global defects retain their precedence.
5. **Path preservation:** compare a translated ROI against analytically known full-depth slab attenuation and echo delays. Retain top water, upstream transmission/losses and deeper material. A feature just outside the displayed ROI but inside the PSF halo contributes identically to an aligned larger-domain reference.
6. **Sampling and physics checks:** no-noise, zero-blur X-ray primitive tests agree with the existing Beer–Lambert model using sampled path lengths. SAM tests retain signed reflection and time shifts/shadowing for the specified material substitutions. Grid refinement reports aliasing sensitivity; no acceptance criterion claims experimental detectability or requires every tiny void to remain visible after modeled blur.
7. **Honest resource admission:** on bounded analytic/random geometry fixtures, the new per-column interface estimate never understates actual echo storage requirements. The delivered HBM microstructure preset passes existing resource checks; low-frequency/high-halo requests that exceed them fail before allocation. Record wall time and peak-process observations separately from the numerical estimate.
8. **Saved workflow:** acquire intact and defective ROI SAM datasets, reopen them after restart, inspect RF/envelope and optionally derive depth. Preserve original arrays and geometry snapshots. Existing cancellation/resume and checksum tests remain green with the explicit patch. Saved full-angle X-ray controls communicate their coarser geometry rather than implying local refinement.
9. **Browser workflow:** select HBM 6, apply the 2×3 patch, inspect a selected off-center TSV/bump in XZ and YZ, target a defect, see a focused 3D representation, run an admissible ROI preview and saved SAM acquisition, inspect/export the result, then return to the full six-site overview. Labels clearly separate geometry, time signals and depth estimates.

## Implementation order and exit boundary

Implement compiler/schema and the count/defect tests first; then section bounds and the conservative SAM estimate; then editor/close-view controls; finally the end-to-end acquisition/export demonstration and browser checks. Keep shared geometry generation on the server and use its returned canonical primitives in the browser. This avoids two competing microstructure compilers.

The increment is complete when that one bounded patch is inspectable and actually contributes to saved SAM data and the normal-incidence X-ray ROI under the stated numerical model. Requests exceeding 600 primitives remain explicit validation failures. If larger patches become the next priority, introduce a versioned procedural array record plus bounded material sampling/render-instance iterators, with one common overwrite-order contract and a new primitive/feature-work budget. Such a seam must reach voxelization, material sections, rendering, estimates and frozen provenance together; a larger schema cap alone is not that implementation.
