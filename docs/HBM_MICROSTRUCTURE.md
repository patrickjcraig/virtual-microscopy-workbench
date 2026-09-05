# Explicit HBM microstructure

Version 0.7 adds one editable patch of inter-die bump and through-silicon via
(TSV) cylinders to the existing layered HBM twin. All six physical H100 sites
remain present. The patch changes material occupancy used by the numerical
simulators; it is not only a 3D display overlay.

The dimensions are synthetic authoring defaults. They are not verified H100 or
HBM3 dimensions and were not inferred from the supplied reference image. Its
approximately 4.6 µm/pixel scale remains a separate, provisional user estimate.

## Starting the example

Choose **NVIDIA H100 SXM / HBM6 explicit patch** in the specimen picker, or use
`?specimen=nvidia-h100-hbm6-microstructure`. It includes the original package
and six HBM stacks, with a selected patch in HBM6:

| Parameter | Synthetic default |
| --- | --- |
| Rows × columns | 3 × 2 |
| X/Y pitch | 50 / 50 µm |
| Inter-die bump diameter | 25 µm |
| Bump height | Existing gap thickness, initially 15 µm |
| TSV diameter | 10 µm |
| Nominal connections | 48 bumps in eight gaps; 54 TSVs through eight DRAM dies and the base |
| Full twin count | 574 primitives; 26 remaining under the 600-object limit |
| Recommended ROI | X 49.4375–49.5625, Y 39.9125–40.0875 mm |
| Recommended material raster | 64 × 64 laterally; 1,024 planes through the full 2.65 mm package |

The recommended X/Y/Z sampling pitches are **1.953125 / 2.734375 / 2.587891 µm**.
These numbers do not establish image resolution. The original H100 example
remains available without an enabled patch; enable and apply one in its HBM
editor to explore the same construction.

Open the HBM editor to change patch dimensions and offsets. Changes are drafts
until **Apply stack changes** succeeds. Applying edits preserves the other five
assembly blocks and existing saved acquisitions. Counts, bounds, overlapping
nominal cylinders, invalid defect targets and incompatible layer changes are
validated before returning a new twin. The previous twin survives a rejected
edit. Only one enabled patch is supported per twin in this increment.

## Local defects and inspection

Up to four local defects can be attached to nominal features. Rows and columns
start at one. Bump layers refer to gaps 1–8 in an eight-high stack. TSV layer zero
is the base die, followed by DRAM dies 1–8.

| Local defect | Material change |
| --- | --- |
| Missing bump | Replace its solder cylinder with the epoxy proxy |
| Bump void | A centered air sphere strictly inside the solder cylinder |
| TSV void | A centered air sphere strictly inside the copper cylinder |

Turning defect inclusion off restores the nominal material occupancy. A missing
bump does not imply an air-filled crack. A void must be smaller than both the
host's diameter and height. Two enabled defects cannot target the same feature.
Moving a stack moves its local features and defects; unrelated global defects
keep their existing coordinates. Reducing layer count cannot silently discard
or retarget an authored defect, even when that defect is disabled.

Select a nominal feature to put the material section through its actual center.
XZ and YZ views have independent display scales and global millimeter axes.
Feature views crop around the selected connection; whole-stack views remain
available. Explicit section bounds and fixed planes are also supported by the
API. Material sections point-sample the complete ordered specimen, including
surrounding package objects and later global defects.

**Focus patch** brings the geometry into a close 3D view; **Return to package**
restores the overview. The renderer hides a nominal missing bump when its defect
is included. Contained voids are labeled markers: existing overlapping meshes
do not perform Boolean cavity subtraction. Use the material section to inspect
actual replacement occupancy.

## Acquisitions and sampling

The microstructure ROI keeps the full specimen depth and surrounding Gaussian
response context. It never substitutes an isolated HBM cutout or resets global
coordinates. Normal-incidence X-ray previews integrate the entire sampled
material path. Saved SAM keeps signed RF and analytic envelope with its original
time reference, full geometry snapshot and acquisition settings.

The saved-SAM estimate reports padded grid shape, output bytes, peak numerical
workspace, and each included microfeature's extent in X/Y/Z samples. Features
below two samples on any axis receive a warning. The echo workspace is bounded
by the number of primitive intervals intersecting each padded column, including
halo columns, rather than counting every package object in every column. The
64-million-cell, 512 MiB output/workspace and RF-work limits remain enforced.
The estimate is a conservative numerical allowance, not a process-RSS guarantee.

The interactive preview computes visible scan rows in bounded tiles with their
complete spatial halo. It retains the full RF trace, including echoes beyond the
selected C-scan gate. Its unchanged work limits are eight million cells per tile
and 180 million aggregate cells; tile size and work counts are reported in run
metadata. This avoids repeatedly recomputing large halos in one-row tiles for
fine ROIs.

An illustrative saved recording uses 100 MHz center frequency, 800 MHz RF
sampling and a 0.5 µs duration. Set its start time to the interfaces being
examined. Record start is not a new specimen surface reference. Existing
bandwidth, standoff, focus and gating controls remain available. Depth mapping
uses its independently declared velocity model; adding local copper or voids
does not calibrate that model.

Saved full-angle X-ray projections still use a material grid over the whole
specimen. Cropping or offsetting the detector does **not** refine that grid.
The new fine-scale X-ray demonstration is the zero-angle ROI preview. A future
local all-angle projector must combine refined local material and surrounding
attenuation along each ray without counting either contribution twice.

## API and provenance

Use `POST /api/hbm/compose` with `parameters.microstructure` to author the patch.
The nested update merges with the previous authored record, so `{"enabled":false}`
preserves dimensions and defects. Empty `{}` supplies defaults for a new patch.
Use `POST /api/hbm/microstructure` with `{twin,assembly_id}` to retrieve the
canonical feature list, nominal/defect counts, global ROI and remaining capacity.
The browser consumes the server's geometry and feature identities.

`POST /api/hbm/section` accepts optional `feature_id`, `bounds_mm:[u0,u1,z0,z1]`
and `fixed_coordinate_mm`. A feature-centered plane and an explicit fixed plane
are mutually exclusive. Returned extents, fixed coordinate and sampling pitches
describe the actual section. Unknown or disabled features and invalid bounds
return 422.

The authored record includes `model_version: "hbm-explicit-patch-1"`, evidence
and source notes. Imports must reproduce the canonical expanded primitive block.
Frozen acquisition inputs contain both parameters and primitives; their input
hash identifies that geometry and the acquisition settings. SAM/X-ray solver
fingerprints now include the HBM compiler. Changed code or inputs reject partial
resume; completed historical datasets remain readable and exportable.

The patch uses existing silicon, copper, epoxy, air and pure-tin solder proxies.
It omits liners, barriers, redistribution routing, joint curvature, electrical
connectivity and specimen-specific composition. Acoustic propagation remains the
documented primary normal-incidence model, without reverberation or shear waves.

## Reproducing numerical checks

Run the tools with fresh output directories; they refuse overwrites:

```powershell
uv run python -m tools.verify_microstructure_xray artifacts/hbm-xray-validation
uv run python -m tools.verify_microstructure_sam artifacts/hbm-sam-validation
uv run python -m tools.build_hbm_microstructure_example --output artifacts/hbm6-patch.json
```

The X-ray check compares production voxel projections to an independent analytic
vertical-ray oracle through ordered primitive intervals. The SAM experiment
records intact/defective RF differences under specified sampling and response
assumptions. Both include depth-grid sensitivity. Numerical contrast in these
synthetic cases is not evidence of experimental defect detectability. See
[VERIFICATION.md](VERIFICATION.md) for executed results and
[PHYSICS.md](PHYSICS.md) for model equations and material provenance.

Version 0.8 adds the opt-in [continuous material-path method](COLUMN_PATHS.md)
to address vertical voxel-center boundary quantization for supported
normal-incidence paths. The original voxel solver remains available for
comparison; the patch dimensions and reduced-order propagation remain assumed.
