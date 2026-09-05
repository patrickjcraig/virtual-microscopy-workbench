# Continuous normal-incidence material paths

Version 0.8 adds an explicit continuous-column option to the preview and saved
SAM acquisition. It follows the vertical intersections of the authored boxes,
cylinders and spheres, preserving the entire specimen depth and ordered material
replacement. It removes vertical voxel-center boundary quantization for those
primitives. Lateral sampling, temporal sampling and instrument-model assumptions
remain.

## Choose a numerical method

Both the main acquisition controls and the saved-SAM form offer **Material paths**:

| Choice | Geometry used for acquisition |
| --- | --- |
| Voxel-center paths | Existing material raster, including its adjustable Z depth samples. This remains the default. |
| Continuous normal-incidence paths | Ordered continuous intervals along each sampled XY column. The stored Z depth-sample value is inactive. |

Changing the method requires a new acquisition. Existing images remain labeled
stale until that acquisition succeeds. Requests preserve the explicitly selected
method, and saved datasets display their frozen method. Older saved acquisitions
without a method field are interpreted as voxel-center paths without modifying
their stored requests.

Continuous preview requires **0° X-ray incidence**. A nonzero angle is rejected;
the application does not silently reset it or substitute another method. Saved
full-angle X-ray projections still use their existing voxel projector. The new
option does not add refined local tomography, cone geometry or laminography.

The depth-sample control is visibly inactive in continuous mode, but its authored
value stays in the recipe. Changing that inactive value changes the frozen request
hash and leaves continuous arrays, coordinates and numerical estimates unchanged.
Material cross sections retain their separately labeled display raster; a section
pixel pitch is not the continuous acquisition's Z discretization.

## Inspect the HBM patch

1. Load **NVIDIA H100 SXM / HBM6 explicit patch**.
2. Select **Continuous normal-incidence paths** in the main controls.
3. Open the HBM assembly laboratory and select HBM6. For a preview, explicitly
   choose **Use 0.15 × 0.25 mm preview ROI**, then run the acquisition.
4. Inspect the signed A-scan, B-scan, X-ray transmission and gated SAM map.
   A/B traces retain the complete finite preview recording, including echoes
   beyond the C-scan gate.
5. For stored RF, open the saved-SAM workspace, choose the method there, set the
   record start/duration and sample rate, check the estimate and acquire.

The ready preset's smaller 0.125 × 0.175 mm rectangle exceeds the continuous
preview's conservative combined workspace limit at its current settings. The
larger-ROI button is an explicit user action and changes only the acquisition
rectangle/settings it describes. The full twin remains present. Requests are
never downsampled or recropped automatically.

The separate saved-SAM estimate can admit a short recording even when a full
preview trace cannot fit. An executed example uses a **0.12 × 0.18 mm ROI**,
64 × 64 raster, 100 MHz center frequency, 0.5 fractional bandwidth, 800 MHz RF
sampling, 0.2–0.7 µs record and 0.55 mm focus, with zero external water standoff.
Its lateral pitch is **1.875 × 2.8125 µm**. No Z voxel pitch is reported.

These parameters and the HBM structure are synthetic assumptions. The user's
approximately 4.6 µm/pixel reference scale remains independent provisional
metadata. Continuous interfaces do not establish experimental spatial resolution.

## Material and propagation semantics

At each sampled global XY coordinate, the path partitions the complete interval
from specimen top to bottom. Later included primitives replace earlier material.
Adjacent equal material segments merge. Explicit air voids remain distinct from
ambient immersion water. Disabling defects restores the nominal ordered geometry.

Boxes and vertical cylinders contribute their clipped Z intervals. Spheres
contribute their analytical vertical chords. A tangent sphere has zero length;
a column exactly on a box side or cylinder wall follows the inclusive lateral
predicate already used by voxel sampling. This is a sampled-ray convention, not
integration over a detector pixel's area.

Coincident endpoints follow the versioned `ordered-column-paths-1` policy.
Float64 endpoints within `32 × epsilon × max(1 mm, specimen depth)` of a group's
first endpoint are grouped without transitive extension. Specimen endpoints are
preserved exactly. Representationally ambiguous positive intersections no larger
than twice this tolerance are rejected with an object/column locator. Diagnostics
record endpoint adjustments and actual interval counts.

X-ray optical depth is the sum of material attenuation times its ordered path
length. The existing intensity-domain detector response and optional seeded
Poisson sampling are retained. Overlapping objects do not add duplicate lengths.

SAM uses continuous interface depths and accumulated two-way material travel
times with the existing signed reflection, reciprocal transmission, loss and
focus equations. The phase-aware complex Gaussian pulse and coherent lateral
response are retained. Saved RF and its analytic envelope remain separate arrays
with the original transducer time reference. External water standoff adds the
declared delay and attenuation.

This is still primary scalar normal-incidence longitudinal acoustics. Applying
that reflection model to a curved boundary is an approximation. Reverberation,
refraction, shear conversion, anisotropy and full elastic propagation are absent.
Time-to-depth mapping remains a separate operation with its own velocity model.

## Resources, storage and provenance

Preflight accounts for a bounded preparation pass and all repeated tile halos.
Paths are transient typed arrays; no global voxel cube or full global RF field is
allocated. Saved tile rows are chosen deterministically from 8, 4, 2 and 1.

Existing 512 MiB saved-output/numerical-workspace limits and 180-million RF work
cells remain. Preview retains its eight-million-cell per-tile limit. Continuous
mode also limits padded columns to 500,000, primitive-column candidate tests to
50 million and event-work units to 250 million. Estimates describe conservative
numerical workspace, not a guarantee of total process RSS. Both the direct path
functions and acquisition entry points enforce bounded allocation.

Unlike voxel mode, continuous interface bounds have no Z-raster cap. An authored
thin layer can contribute even if a voxel grid would miss it. Metadata reports
`path_model`, contract/model versions, padded XY shape, lateral pitch, work counts,
workspace estimates and path diagnostics. `grid_shape` and `voxel_depth_um` are
null, with `depth_samples_used=false`. Feature warnings address finite lateral
sampling; they do not invent a continuous-mode voxel depth.

Saved `sam_rf_volume` still contains float32 signed RF/envelope in `[y,x,time]`
and float64 global X/Y/time coordinates. New continuous acquisitions freeze the
method, compiled primitive order, materials, numerical packages and relevant
geometry/integration source hashes. Partial data cannot resume with a different
method, estimate or solver source. Completed historical SAM, projection, CT and
depth datasets remain readable/exportable using their saved arrays and metadata.

## Reproduce the comparison

Use a fresh output directory:

```powershell
uv run python -m tools.verify_continuous_hbm artifacts/continuous-hbm-comparison
```

The tool retains full signed RF/envelope NPZ files, exact requests, metadata,
source/material fingerprints, resource measurements and a report. It compares
three voxel Z grids with continuous paths, verifies exact continuous inactive-Z
invariance, records a targeted missing-bump effect and measures limited lateral
and RF-rate sensitivity. The X-ray check uses an independent continuous-ray
oracle from the earlier microstructure experiment.

See [VERIFICATION.md](VERIFICATION.md) for executed results and practical limits.
The [original specification](COLUMN_PATH_SPEC.md) retains the design rationale
and proposed acceptance criteria; this guide describes the shipped behavior.
