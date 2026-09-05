# Verification record — 4 September 2026

This record concerns the first local implementation in `E:\git\Dissertation`. It establishes software and analytical-model behavior, not experimental imaging accuracy.

## Version 0.8 — continuous normal-incidence paths

The complete Windows suite passed **480 tests in 105.03 seconds**, with the same
two third-party deprecation warnings. The 82 new cases comprise 27 continuous
geometry/integral tests, 23 saved/preview numerical tests, 24 path-contract and
historical-data tests, and eight saved-lifecycle/API tests. Locked environment
synchronization and the final production frontend build pass.

Independent geometry tests cover box/cylinder intervals, sphere chords and
tangency, clipped full-depth paths, ordered overlap, equal-material coalescing,
nontransitive endpoint coincidence and ambiguous tiny-intersection rejection.
Scalar tests independently compute signed reflection, material travel times,
transmission/loss/focus, a non-grid-aligned 15 µm film and explicit air/water
behavior. Pulse tests cover the declared discrete fractional-delay law, record
edges, full preview time traces, halo-only features, translated ROI placement,
seeded X-ray noise, inactive-Z invariance and allocation rejection.

Read-only review found and fixed a one-ULP coordinate provenance defect. For an
ordinary decimal ROI, recomputing a saved X coordinate from the unpadded origin
gave 0.38515625 mm while its calculated path used 0.38515625000000003 mm. A box
ending at the former coordinate classified the two columns differently. Saved
axes now copy the exact padded-coordinate slices used by geometry; the boundary
counterexample is a regression. Preview/probe coordinates are independently
checked against those same sampled vectors. Instrumentation also confirmed that
actual cumulative candidate/event work equals the published preflight counts for
saved, full-preview and probe paths, including preparation and repeated halos.

Lifecycle tests acquire real continuous arrays, cancel during preparation and
after a committed tile, resume exactly, repair missing/corrupt chunks while
preserving good chunks, and reject changed methods, source identity or estimates
before signal writes. Two actual API acquisitions with inactive Z counts 128 and
1,024 have different frozen request hashes and identical RF/envelope/coordinates.
Views, post hoc gates, exports and restart/reopen preserve stored data. Completed
legacy records without a path field remain readable under a changed solver.

The standalone `tools/verify_continuous_hbm.py` experiment retains complete signed
RF/envelope arrays and frozen inputs for a 0.12 × 0.18 mm HBM6 ROI, 64 × 64 raster,
100 MHz carrier, 0.5 fractional bandwidth, 800 MHz sampling, 0.2–0.7 µs record,
0.55 mm focus and zero external water standoff. Results are local in
`artifacts/v08-hbm-independent-final/`:

| Numerical comparison | Executed result |
| --- | --- |
| Continuous Z setting 128 versus 1,024 | All RF/envelope/XY/time arrays exactly identical; request hashes differ |
| Voxel RF at 256 / 512 / 1,024 Z planes versus continuous RF | 189.40% / 73.56% / 30.76% relative L2 difference |
| Continuous missing gap-8, row-3, column-2 bump | 5.082% RF relative L2 difference; max RF 0.013328, envelope 0.011491 in relative units |
| 32² versus 64² raster at the fixed physical probe | 1.506% RF relative L2 difference, using declared bilinear interpolation |
| 800 versus 1,600 MHz RF sampling on the 32² raster | 0.220% RF relative L2 difference at shared saved times |
| Continuous X-ray optical depth versus independent ordered-ray oracle | Maximum absolute difference 8.89 × 10⁻¹⁶ over nine rays for each intact/defect case |

These are differences among numerical configurations under common assumed
materials and propagation. The continuous path equations remove vertical
voxel-boundary quantization for the supported primitives; neither two-grid
sensitivity nor exact Z-setting invariance establishes convergence of all
sampling choices, physical resolution or experimental defect detectability.
The X-ray oracle uses a separate midpoint-interval implementation and was
established against analytical slabs in the preceding release.

The continuous intact 64² recording took **8.343 seconds** in one fresh process,
with **234,946,560 bytes** observed peak working set, versus **2.116 seconds** and
**269,238,272 bytes** for the 1,024-plane voxel recording. Numerical admission
estimates 284,614,216 bytes for the continuous case, with eight-row tiles,
30,614,864 candidate tests, 18,860,768 event-work units and 18,649,664 RF cells.
Process peaks include interpreter and retained validation arrays; estimates have
a different numerical scope. These observations are not performance guarantees.

The compact 0.125 × 0.175 mm HBM preview is rejected under continuous combined
workspace limits. The explicitly chosen **0.15 × 0.25 mm** preview is admitted
at **530,295,424 estimated bytes**, with 15-row cores and the full finite A/B
record. No ROI, method, angle or sampling request is silently changed.

Native Chrome `verify:continuous-paths` passed method selection, rejected tilt,
inactive-Z equality, explicit larger HBM ROI, saved signed RF, post hoc gates,
download/reload, imported/exported choices, historical read-only export and
390-pixel layouts. It caught a compact-catalog method label being defaulted after
the request was stripped; catalog summaries now preserve the frozen method and
have separate regressions. Existing `verify:h100` and `verify:volumes` passed
sequentially. No uncaught browser page errors occurred in the passing flows.
The [workspace screenshot](images/continuous-hbm-workspace.png) contains generated
geometry/signals only; its broad-gate C-scan is weak-contrast on the visibly fixed
0–0.50 display window, without hidden normalization.

Two delivered continuous datasets are saved in the active local catalog:

| Case | Dataset ID | ZIP bytes |
| --- | --- | ---: |
| H100 HBM6 / continuous intact | `912a631d-6432-4faa-8323-a3118812ad51` | 6,260,717 |
| H100 HBM6 / continuous missing gap8 r3 c2 | `18c32ae0-059c-4fc8-b6e2-6ab0d307567d` | 9,643,565 |

Each contains 64 × 64 × 401 RF/envelope samples and **13,144,200 uncompressed
array/coordinate bytes** under the protocol above. All committed chunks,
coordinate hashes and ZIP CRCs verify. Every array and coordinate is exactly
equal to its corresponding offline experiment file. Reopened API traces match
all 401 saved samples; views and exports leave all dataset files byte-identical.
Additional completed SAM, projection, CT and depth examples pass integrity
verification with current code. The paired preview uses the separately declared
larger ROI and has maximum transmission difference 0.009541 after the normal
detector response with noise disabled. Archives, preview snapshots and reports
remain outside Git in `artifacts/v08-hbm6-delivery/`; no measured image is included.

## Version 0.7 — explicit HBM bumps, TSVs and local defects

The complete Windows suite passed **398 tests in 87.76 seconds**, with the same
two third-party deprecation warnings. The 83 new cases comprise 39 geometry,
22 section/API, 18 saved-SAM microstructure and four preview tests. The locked
Python environment also synchronizes successfully.

The default HBM6 patch adds 48 solder-proxy bumps and 54 copper TSV cylinders to
the six-site H100 twin, for **574 primitives**. Geometry tests verify canonical
ordering, stable identities, local translations, strict imports, partial edits,
disabled-parameter retention, restoration of absent sites, primitive limits and
rejected overlaps or orphaned defects. Missing bumps replace solder with epoxy;
voids are contained air spheres. These dimensions and materials are assumptions.

Sections are checked against asymmetric global coordinates and independently
selected feature centers. The tests cover XZ/YZ planes, explicit bounds, fixed
planes, excluded defects and invalid selections. A 0.1 µm void deliberately falls
between display samples: the image is unchanged but warns that the included
defect is undersampled. Removing that defect from the view removes its warning.

Saved-SAM resource tests compare the conservative column-interface bound with
actual voxel transitions and generated echoes, including random primitive
overlaps, edge columns and response halos. They verify unchanged hard budgets,
target-specific sampling warnings, actual missing-bump RF changes, nominal
restoration when defects are excluded, and byte-identical resumed output.
The geometry compiler is now part of SAM/X-ray forward-source fingerprints.
Previously completed SAM, projection, CT and depth datasets all pass integrity
verification with current code in the new local catalog.

Fine-ROI previews now tile only visible output rows while retaining full spatial
halos and the original RF time record. An independent direct pulse calculation
matches the returned C-scan, A-scan and B-scan, including halo-only sources,
pulses centered outside both gate boundaries and a later out-of-gate echo.
Separate tests reject both hard work limits before complex RF allocation.
Independent code review found no coordinate, halo, gate or budget regression.
The ready 64 × 64 × 1,024, 100 MHz preset used 16-row cores, at most **6,884,136**
work cells per tile and **27,536,544** aggregate work cells. Its full trace has
1,051 samples ending at 1.3125 µs despite the 0.26–0.7 µs gate. One fresh-process
measurement took **1.676 seconds**, with **452,222,976 bytes** peak working set;
this observation is not a runtime or process-memory guarantee.

`tools/verify_microstructure_xray.py` compares voxel projections with an
independent continuous vertical-ray oracle through ordered primitive intervals.
The selected gap-4 missing bump changes the continuous 80 keV transmission from
0.3454153 to 0.3569677. The expected log-transmission ratio is **0.032897805**;
sampled ratios at 256/512/1,024 depth planes are **0.0454058 / 0.0340544 /
0.0283786**. The finest result still has approximately **13.7% relative error in
this contrast**, and refinement is nonmonotonic. The oracle separately confirms
0.0768032 optical depth from material outside the chosen HBM assembly. The
experiment retains the surrounding package and disables noise/detector blur;
it does not establish instrument observability. Arrays and report are local in
`artifacts/v07-xray-independent/`.

`tools/verify_microstructure_sam.py` records a separate 0.12 × 0.18 mm ROI at
64 × 64 lateral positions, 100 MHz carrier and 800 MHz time sampling over
0.2–0.7 µs. Its gap-8, row-3, column-2 missing bump changes the finest-grid RF
by **3.54% relative L2**, with maximum absolute RF difference **0.0813** and
envelope difference **0.0728** in relative units. Intact 256/512-plane RF differs
from the 1,024-plane comparison by approximately **183% / 82% relative L2**.
The finest grid is a comparison reference, not converged truth: voxel boundary
motion strongly affects coherent phase. Full signed RF/envelope arrays, exact
requests, resource measurements and reports are retained in
`artifacts/m7-microstructure-sam-validation/`. These numerical signal changes
do not establish physical resolution, calibrated accuracy or defect detectability.
The next increment is continuous material-path timing and its independent tests.

Native Chrome `verify:microstructure` passed 16 workflow checks: loading the
ready preset, atomic patch edits, all six sites, asymmetric XZ/YZ sections,
missing-bump/void occupancy, defect exclusion, authored disabled records, close
3D view and return, rejected-edit retention, actual fine-ROI preview, saved
signed SAM RF with frozen patch provenance, post hoc gating, ZIP download,
reload, twin import/export and 390-pixel layout. The native `verify:hbm` and
`verify:volumes` regressions also passed sequentially. These checks found and
fixed a site-selection reset when reopening the editor. No uncaught page errors
occurred in the passing workflows. Browser reports and downloads remain local in
`web/test-artifacts/`.

The final production frontend build passes. Additional focused native checks
verify the revised close-view labels at 390 pixels, three local-defect markers,
hidden nominal missing-bump mesh and restoration of all seven package labels.
The inspected [editor](images/hbm-microstructure-editor.png) and
[close-view](images/hbm-microstructure-focus.png) screenshots show generated
geometry; the supplied reference pane is closed and its image is excluded.

The delivered comparison pair is saved in the active local catalog at
`http://127.0.0.1:8767`, using the ready preset's **0.125 × 0.175 mm** ROI.
Both datasets contain **64 × 64 × 401** signed RF/envelope samples over
0.2–0.7 µs at 800 MHz, with 100 MHz carrier, 0.5 fractional bandwidth, 0.55 mm
focus, zero external standoff and 1,024 material depth planes. This ROI differs
from the standalone SAM experiment above and samples different lateral rays.

| Saved case | Dataset ID | ZIP bytes |
| --- | --- | ---: |
| H100 HBM6 / intact explicit patch | `3f5a1694-987a-489a-886a-c6803264f3a9` | 6,265,269 |
| H100 HBM6 / missing bump gap8 r3 c2 | `7fb0140d-a602-4fbb-be11-a6df5e83e088` | 9,663,688 |

Each stores **13,144,200 uncompressed array/coordinate bytes**. All committed
coordinates/chunks and archive CRCs pass verification; exporting leaves every
dataset file byte-identical. X/Y/time coordinates match across the pair. The
reopened API views reproduce all 401 saved RF/envelope/time samples exactly at
the largest-difference location, with every dataset file still unchanged. The
RF relative L2 difference is **6.417%**, maximum absolute RF difference is
**0.013512**, and envelope difference is **0.012174**. The corresponding actual
X-ray previews, with the normal detector response and noise disabled, differ by
at most **0.009847 transmission**. Full preview snapshots, signed differences,
ZIP archives and the numerical report are retained locally in
`artifacts/v07-hbm6-delivery/`. No measured reference image or generated volume
is included in Git. These values characterize this synthetic configuration only.

## Version 0.6 — SAM time-to-depth estimates

The complete Windows suite passed **315 tests in 86.83 seconds**, including
the 247 existing cases and 68 depth-mapping/storage/view cases. The same two
third-party deprecation warnings remain. The new tests comprise 24 analytical
mapping cases, 20 storage cases and 24 view/API cases. A separate combined
SAM/X-ray/CT/depth storage regression passed 80 cases.

Analytical tests verify homogeneous and cumulative layered travel times, surface
water delay from frozen metadata, explicit surface references, nonzero recording
starts, nonuniform source time samples, exact source X/Y retention, signed RF and
independent saved envelope interpolation. Out-of-record and out-of-model depths
have distinct semantics with masked placeholders. Invalid layer ordering,
nonuniform spatial grids, numeric overflow and flattened travel times are
rejected. Bounds and temporary/output/workspace requirements are checked before
allocation. No primitive material labels enter the velocity model.

The independent experiment `tools/verify_depth_mapping.py` constructs analytic
Gaussian RF pulses at known reflector times without using the production SAM
forward simulator. It uses **3.90625 µm output depth sampling**:

| Synthetic timing check | Observed result | Analytic expectation |
| --- | --- | --- |
| Matching homogeneous model, reflectors at 0.6/1.4 mm | Peak errors −0.391/+0.391 µm | Zero |
| Matching layered model, reflectors at 0.25/1.25 mm | Peak errors +1.953/−1.953 µm | Zero |
| Assumed speed 10% high | Peak shifts +58.59/+140.63 µm | +60/+140 µm |
| Surface time 20 ns late | Both peaks shift −39.06 µm | −40 µm |

The profile arrays and report are retained locally in the ignored
`artifacts/m6-depth-independent-validation/` directory. The differences above
reflect interpolation and finite output sampling in a synthetic test. They do
not establish acoustic resolution or experimental H100 depth accuracy.

Storage/API tests verify frozen source and processing metadata hashes, exact
X/Y identity, coordinate and signal checksums, binary masks, signed RF,
nonnegative envelope, safe paths, conservative disk requirements, cache cleanup,
cancel/resume equality and damaged partial-chunk repair. Source corruption is
rejected before mapping. Completed derived data verify without requiring the
raw source directory. A real saved-SAM acquisition is mapped, exported, reopened
after application restart and checked for byte-identical source files.
Independent asymmetric arrays verify all three spatial slice axes, profiles,
original-time cursor values, mask semantics and immutable browsing.
Final review added view-side coordinate checksum and support-interval checks:
finite monotone coordinate edits, changed lateral coordinates and zero-signal
mask flips are rejected. A supported zero amplitude remains valid. These bounded
checks use frozen derived metadata and require no raw-source directory.

Native Chrome `verify:depth` passed the saved-source mapping workflow with a
translated ROI, explicit surface offset, two velocity layers, signed RF,
nonnegative envelope, and both out-of-record and out-of-model masks. It checked
exact source X/Y retention, null travel times beyond the model, gray invalid
pixels, linked mouse/keyboard navigation, weak-signal display windowing without
new jobs, ZIP export, reload persistence, four separate dataset catalogs, and
desktop/390-pixel layouts. `verify:depth-jobs` cancelled after **2 of 128 committed
slices**, then resumed the same dataset to completion. Existing slice checksums,
coordinate checksums, model metadata, request and source manifest were unchanged.
Both workflows completed with zero uncaught browser page errors. Locked Python
environment synchronization and the production frontend build also pass.
The existing native H100, saved-SAM and saved-source CT workflows pass against
0.6 as well, including their acquisition, inspection, export and reload paths.
Saved-SAM and CT checks ran sequentially after mapping jobs finished, respecting
the shared worker's intentional preview/acquisition exclusion.

The delivered HBM6 example maps source
`b407c38d-244b-4158-8683-bc4e56c03b37` to dataset
`a3f7a787-2eed-417f-85d6-a6f18a08efa5`. The **128 × 64 × 64 Z/Y/X** volume
retains the 8 × 9 mm HBM6 footprint at X 45.5–53.5 and Y 35.5–44.5 mm, with depth
0–2.65 mm. X/Y/Z pitch is **125 / 140.625 / 20.703125 µm**. It explicitly assumes
a uniform **5,000 m/s** speed and uses the saved source water-delay reference;
this is not a calibrated H100 velocity or material-depth estimate. Its source
retains all six physical HBM assemblies.

All derived samples are supported by this assumed model and the recorded time
interval. That validity does not certify the model. RF ranges from approximately
−0.091698 to +0.104059; envelope peaks at 0.112287, in their original relative
units. The three signals and four coordinates occupy **6,294,528 uncompressed
bytes**; the archive is **448,677 bytes** and passes ZIP CRC checks. The observed
queue/mapping/export interval was **52.81 seconds** while other verification
work was active; this is not a runtime guarantee. Original raw files and X/Y
coordinates remained unchanged. The [depth workspace screenshot](images/sam-depth-workspace.png)
shows actual saved data under an explicitly chosen display window.

## Version 0.5 — saved-source CPU CT reconstruction

The complete Windows Python suite passed **247 tests in 95.69 seconds**, including
the 197 existing cases and 50 reconstruction/atomic-storage cases. The same two
third-party deprecation warnings remain. Locked environment synchronization and
the production frontend build pass. Tests cover physical attenuation scaling,
asymmetric ellipses with displaced rotation/detector centers, multiple Y planes,
equivalent 180°/360° weighting, detector-sampling refinement, filter/cutoff effects,
negative values, invalid logarithms, truncation masks and physical half-pixel
edges. Nonuniform, limited-angle and noncanonical poses are rejected. Resource
estimates are checked before allocating the filtered-projection cache.

The independent experiment in `tools/verify_reconstruction.py` uses closed-form
continuous ellipse projections rather than the production forward projector.
For 128 views over 180°, a 32 × 216 detector and a 64 × 16 × 64 reconstruction,
the selected plane's interior mean is **0.6125577 mm⁻¹** against an analytic
**0.6125000 mm⁻¹** (relative bias **0.00942%**). Whole-plane RMSE is
**0.0189471 mm⁻¹**. An independent bilinear ray sampler evaluates seven unseen
angles using 2,048 midpoint samples per ray: relative projection L2 error is
**3.7455%** and projection RMSE is **0.00744037**. The retained report and NPZ are
in the ignored local `artifacts/m5-fbp-validation/` directory. These are results
for this synthetic numerical experiment, not experimental or H100 accuracy.

Storage/API tests verify frozen source-manifest identity, source immutability,
negative attenuation, finite masked placeholders, coordinate/chunk checksums,
safe paths, temporary-cache cleanup, bounded disk preflight and byte-identical
resume. Completed reconstructions remain inspectable and exportable after their
source directory becomes unavailable. Independent asymmetric saved arrays verify
exact XY/XZ/YZ slices, profiles and physical cursor coordinates. A real API job
also survives application restart and exports all arrays with embedded provenance.

Live Windows testing exposed a transient manifest-read/atomic-replace sharing
conflict. Atomic writes now retry only the Windows sharing/access errors over a
bounded 0.63-second interval. Tests verify transient recovery, permanent failure,
preservation of the old manifest and temporary-file cleanup. The affected live
job resumed from 8 of 16 committed slices and completed; a fresh complete CT
browser workflow then passed without interruption.

Native Chrome `verify:reconstruction` verifies a frozen synthetic source while
the preview contains H100, non-cubic output and translated bounds, exact slice
axes/values, click and keyboard cursor movement, negative display limits without
new jobs, Zarr ZIP download, reload, SAM/X-ray/CT catalog separation and desktop/
390-pixel layouts. `verify:reconstruction-jobs` cancelled a 96-slice H100-derived
job after **three committed slices**, confirmed incomplete data were unavailable,
then used the native resume button to complete it. The original committed slice
checksums and source manifest were unchanged. Neither workflow had uncaught page
errors. The native H100, saved-X-ray and saved-SAM regressions also pass against
0.5, including the legacy RF/time/gating workflow and its desktop/mobile layout.

The delivered H100 reconstruction is dataset
`1a3b7510-4bf1-4b18-942d-87426a62900c`, derived from the previously delivered
60-view source `391fa82b-60cf-4a40-9794-cfddac1f1c3b`. It has shape
**64 × 64 × 96 in Z/Y/X**, Hann filtering at detector Nyquist, and the full
60 × 60 × 2.65 mm envelope. Output pitch X/Y/Z is **625 / 937.5 / 41.40625 µm**.
Its attenuation range is approximately **−0.22575 to 0.59075 mm⁻¹**. The working
copy interpolated 46 invalid source log samples; the original projection files
were byte-identical after reconstruction and export. All output voxels have full
geometric detector support; that does not establish reconstruction accuracy.

The source retains all six HBM assemblies. Its coarse detector and sparse angles
cannot resolve fine HBM microstructure, despite the much smaller output Z pitch.
Attenuation, coverage and coordinate arrays occupy **3,147,520 uncompressed bytes**;
the local archive is **1,640,714 bytes** and passes ZIP CRC checks. Reconstruction
and export took approximately **16.80 seconds** in this observed run. These are
example timings, not a performance guarantee. The
[CT workspace screenshot](images/ct-reconstruction-workspace.png) shows actual
saved attenuation. Generated volumes, validation arrays and the supplied reference
image remain outside Git. Cone CT, laminography, acoustic depth conversion and
experimental calibration are future work.

## Version 0.4 — saved full-angle X-ray projections

The complete Python suite passed **197 tests in 49.55 seconds**, including all
139 prior cases and 58 X-ray solver/storage/view and integration cases. The same
two third-party deprecation warnings remain. Independent analytical cases cover
axis-aligned, negative and oblique box paths, 90° incidence, material replacement,
asymmetric pose conventions, surrounding attenuation outside a detector crop,
PSF halo continuity, geometry convergence, Poisson statistics, byte-identical
seeded resume, negative noisy logarithms, zero-only log substitution and float32
underflow. Oversized geometry, halo or ray work is rejected before allocation.

Storage tests acquire real mixed SAM/X-ray queues through spawned workers and
verify cancellation/resume, damaged partial-view regeneration, atomic read-back
of all four products, pose/coordinate checksums, source identity and immutable
completed datasets. An actual legacy eight-column SQLite catalog migrates without
changing the SAM manifest or RF bytes. Export rejects redirected roots and nested
symlinks/junctions before reading them; the Windows suite includes an actual
directory-junction case. Missing/nonfinite products and nonbinary/nonfinite
log-validity masks produce explicit errors instead of plausible displayed data.

Independent asymmetric saved arrays verify exact projection, detector-row
sinogram and profile selection, physical extents and angular display-bin edges.
A real API acquisition verifies distinct 0°/90° views, persistent data after an
application restart, all seven coordinate/pose arrays in the ZIP, instrument-kind
guards and the old untagged SAM request. View/product changes leave dataset bytes
and the number of acquisition jobs unchanged.

The native Chrome `verify:xray` workflow passed using an H100 dataset with
**8 × 32 × 48** samples and actual angles 0°, 45°, ..., 315°. It checks the full
specimen remains in the X-ray request despite a selected acoustic ROI, independent
geometry/detector sampling, distinct 0°/90° data and orthogonal rays, stored
profile/sinogram agreement, all three displayed products, zero-count mask
semantics, native ZIP download, reopening after reload, separate SAM/X-ray
catalogs and desktop/390 px layouts. No uncaught page errors occurred.

The separate native `verify:xray-jobs` check cancels a 72-view acquisition before
its first committed view, confirms the incomplete catalog entry is disabled and
the view API returns 409, then resumes the same frozen request and dataset through
completion. Backend tests separately cover already-committed partial views. The
browser also verifies automatic field sizing after moving the rotation center and
independently reported material/detector pitches. No uncaught page errors occurred.
The native H100 regression passes with seven component labels, retained reference
metadata, exports, imported presets and probe behavior. The production build passes.
The native `verify:volumes` SAM regression also passes against the final 0.4
server: acquisition, frozen HBM ROI, signed RF, linked time slices, RMS gating,
native ZIP download, reload and desktop/mobile layout. The previously delivered
0.3 HBM6 dataset still returns all 801 A-scan samples after the server restart.

A separate delivered acquisition retains six HBM assemblies and uses a
**96 × 96 × 1,024** material grid, a **64-row × 96-column** detector, and
**60 angles from 0° through 354° in 6° steps**. It uses 80 keV, 50,000 incident
photons/pixel, seeded Poisson noise and a nominal 20 µm detector FWHM. Its
material pitch is 625 × 625 × 2.588 µm, while detector pitch is approximately
625.609 × 937.5 µm. The detector undersamples the nominal blur and cannot resolve
fine HBM microstructure; these values are sampling, not physical resolution.

All four signal arrays and seven coordinate/pose arrays passed export integrity
checks. They occupy **5,905,760 uncompressed bytes**; the local Zarr ZIP is
**3,386,007 bytes** and passes ZIP CRC verification. Acquisition plus the two
cardinal view reads and export took approximately **23.77 seconds** locally,
which is an observed example rather than a runtime guarantee. The dataset ID is
`391fa82b-60cf-4a40-9794-cfddac1f1c3b`; generated data remain outside Git.

This release provides synthetic monoenergetic parallel-beam projection stacks,
not reconstructed xyz attenuation, cone geometry or experimentally calibrated
H100 microscopy. The [projection workspace screenshot](images/xray-volume-workspace.png)
shows actual saved data. The supplied reference image remains outside Git.

## Version 0.3 — saved acoustic volumes

The complete Python suite passed **139 tests in 19.58 seconds**, including the 82
existing regression cases and 57 new solver, storage, view and process/API cases.
The same two third-party deprecation warnings remain. Analytical checks cover
front/back-interface timing, pressure polarity and loss, explicit external water
delay/attenuation, pulse bandwidth, arbitrary recording starts and contributing
tails, and correspondence with the legacy preview at its equivalent bandwidth.
Rectangular axes, global ROI coordinates, neighboring PSF context, tile seams and
byte-identical resumed RF/envelope data are tested.

Storage checks run actual spawned workers and verify owner locks, cancellation,
restart recovery, worker replacement, frozen inputs, missing/corrupted partial
chunks, read-back commits, completed-dataset immutability, disk budgets and path
boundaries. Completed export checks independently verify combined input identity,
coordinate shape/type/checksum, RF/envelope shape/type and every committed row.

Independent asymmetric arrays verify XY/X–time/Y–time axis order, exact signed
traces, temporal display pooling and gate equations. View/gate calls leave all
dataset bytes unchanged. A real API job is acquired, gated, exported, and reopened
after an application lifespan restart; traces remain identical and a subsequent
legacy preview succeeds. Requests beyond resource limits fail before allocation.

The native Chrome H100 regression check passed on version 0.3, retaining seven
component labels, references, exports, imported presets and probe behavior. This
record distinguishes these software checks from measured microscopy validation.

The native Chrome `verify:volumes` check passed with a real HBM6 ROI dataset of
**24 × 32 × 401** samples. It verifies the frozen ROI, retained RF polarity,
XY/X–time/Y–time orientation and physical cursor, post hoc RMS gating without a
new acquisition, a native Zarr ZIP download, catalog reopening after page reload,
and desktop/390 px mobile layouts without horizontal overflow. No uncaught page
errors occurred. The 17-check HBM editor/ROI regression also passed.

The separate native Chrome `verify:volume-jobs` check cancelled a 64-row HBM6
acquisition before its first committed row, verified the incomplete dataset's
disabled UI and HTTP 409 view response, then resumed the same dataset and frozen
request through completion. No uncaught page errors occurred. This browser check
complements the Python tests for already-committed partial chunks. The
[saved-volume workspace screenshot](images/sam-volume-workspace.png) shows the
completed time volume and its retained signed RF; the source image is absent.

A separate delivered HBM6 dataset records **64 × 64 × 801** samples over 0–2 µs
at 400 MHz, using 1,024 material depth samples, a 50 MHz carrier and 0.5 fractional
bandwidth. All committed coordinates and chunks passed export integrity checks.
Its uncompressed arrays/coordinates occupy **26,254,600 bytes**; the local Zarr ZIP
is **1,921,799 bytes**. This compression ratio is an observation for this synthetic
layered specimen, not a preflight assumption or general guarantee. The local
dataset ID is `b407c38d-244b-4158-8683-bc4e56c03b37`; generated volumes and browser
test artifacts stay outside Git.

## Version 0.2 — layered HBM and region scans

The complete numerical/API suite passed **82 tests in 13.33 seconds**. The same two third-party deprecation warnings remain. Twenty HBM cases cover six physical sites, contiguous layers, parameter edits, 12-high templates, functional-state material invariance, strict import consistency, material-precedence protection, fixed defects and generator safety. Twenty ROI/section cases cover analytical full-depth attenuation and echo times, independent depth sampling, global coordinates and endpoint probes, PSF context outside the ROI, XZ/YZ material sections and invalid input. Existing physics/API cases remain passing.

The final production build and native Chrome `verify:h100`, `verify:hbm` and `verify:exports` checks passed. The HBM report contains 17 checks, including seven visible component labels, 8/12-high edits, rejected geometry retention, original acquisition snapshots after edits, electrical-state material invariance, ROI acquisition and global mouse/keyboard coordinates, restoring full-field/automatic-depth settings, cancellation of pending editor responses, and a 390 px dialog layout. Full-map-to-ROI and ROI-to-different-ROI transitions verify that stale-map interactions cannot overwrite the pending probe coordinates. Reference-image checks verify local SHA-256 identity, natural raster size, imported scale/FOV metadata, mismatched identity handling and an unavailable-image fallback. No uncaught browser errors occurred in the tested flows.

The CLI command with `--hbm-roi hbm-6 --resolution 128 --no-noise` completed in **961 ms** with 1,024 depth samples and global ROI coordinates. The saved synthetic run is `21c5a16c-c80c-4154-9608-2f4f6d5349c1` in ignored `artifacts/m1-hbm6-cli/`. This is an observed local runtime, not a performance guarantee.

The [layer editor screenshot](images/hbm-layer-editor.png) shows an applied 12-high template and its material YZ section. The original user image stays outside Git; only its identity and provisional scale metadata are published. Material sections are geometric views; this version has no saved RF/projection volume, CT reconstruction or experimental H100 calibration.

## H100 extension and public repository

The later H100 extension passed **46 tests in 6.55 seconds** with the same two third-party deprecation warnings. The new cases cover H100 geometry/source metadata, preserved legacy examples, larger probe coordinates, rejection of malformed references/presets, synthetic contrast at 128/192 grids, and generator refusal to overwrite existing files. See [H100_REVIEW.md](H100_REVIEW.md) for the independent source/geometry review and [H100_MODEL.md](H100_MODEL.md) for the explicit assumed stackup.

The H100 headless delivery run used the specimen's recommended settings (80 keV, 50 MHz, 0.34–0.45 µs gate, 0.85 mm focus, probe 22.5/24 mm, noise off) and completed in 519 ms. Its JSON retains all three NVIDIA references, facts/assumptions, and actual acquisition settings. All 46 tests are distinct from any claim of experimental validation; whole-package lateral sampling remains coarse.

The final frontend production build, `npm run verify:h100`, and `npm run verify:exports` passed. The H100 native Chrome check covered URL/specimen selection, published references, six non-overlapping component labels, JSON downloads and retained provenance/warnings, probe coordinates beyond 30 mm, exact imported acquisition presets, plain-text rendering, and restored BGA defaults. No uncaught browser errors occurred in these flows. The [delivery screenshot](images/h100-workbench.png) shows the tested H100 workbench; generated verification reports and downloads remain in ignored `web/test-artifacts/`.

The project now has an independent Git repository and public origin at `https://github.com/patrickjcraig/virtual-microscopy-workbench`. The parent Sandia repository was not modified. GitHub Actions is configured for locked Python setup, the complete numerical/API suite, and a frontend production build on both Windows and Linux.

## Numerical and API checks

Command: `.\.venv\Scripts\python.exe -m pytest -q`

**37 passed in 2.38 seconds.** Two third-party deprecation warnings were emitted by the Starlette test client; there were no test failures.

The 21 physics cases cover NIST coefficients and millimetre conversion, Beer–Lambert slabs at normal incidence and ±45 degrees, energy response, off-plane projection movement, Poisson reproducibility/variance, material precedence and explicit air cavities, signed reflection, round-trip time/transmission/loss, air-gap shadowing, gate exclusion, coherent RF/envelope behavior, probe/full-scan consistency, positive B-scan time bins, geometry sampling warnings and excessive-compute rejection. The 16 API cases include parameterized geometry/acquisition rejection cases, bundled specimen validation, concurrency handling, finite array outputs, input hashes and repeated acquisitions.

A default 128 × 128 BGA run completed in approximately **0.55–0.64 seconds** in the observed runs. The recorded headless export in `artifacts/verified-bga/` contains 128 × 128 X-ray and SAM arrays, 918 RF samples and a 459 × 128 B-scan; all values were checked finite. Its JSON includes settings, twin, material values/provenance, timestamp, model version and input hash. The gate is 0.42–0.56 µs.

An intact/defective comparison with noise disabled changed X-ray transmission by a maximum absolute 0.2402 and gated SAM amplitude by 0.0386. Those are numerical differences within the synthetic model, not detection accuracy. A broad 0.1–0.65 µs gate initially masked the delamination in the C-scan because it included a stronger earlier echo; the example default was adjusted to isolate die attach.

## Frontend and browser checks

- Production frontend build passed.
- Full `npm audit --audit-level=moderate` reported zero vulnerabilities at verification time.
- Chromium loaded the workbench and completed an automatic acquisition; no console errors or uncaught browser exceptions were observed in the checked flows.
- Defect toggle marked data stale; re-acquisition changed the SAM peak from approximately 0.129 to 0.091.
- Exploded geometry view, specimen selection, model-specific gate defaults, valid JSON upload, keyboard probe movement, linked A/B inspection and invalid-gate messaging were exercised.
- Axe 4.12.1 returned **0 violations, 39 passes and 0 incomplete checks** on the tested desktop state after contrast and scroll-region fixes. This automated result is not a comprehensive accessibility certification.
- At a 390 px viewport, document width remained 390 px: no horizontal overflow.
- Independent Playwright verification completed native Chrome downloads of both JSON exports. The twin was 42,301 bytes and acquisition 4,695,877 bytes; both parsed and contained the expected geometry/data/provenance. Probe inspection preserved the original acquisition and stored its own arrays/settings separately. The agent-browser helper's canceled-download result did not reproduce in this native download test.

The repeatable export check is `web/scripts/verify-exports.mjs`, exposed as `npm run verify:exports`. Start the API first and set `MICROSCOPY_CHROME_PATH` to a local Chrome/Chromium executable if the Playwright default browser is unavailable. Generated test files live in ignored `web/test-artifacts/`.

## Delivery state and limits

The loopback service was restarted with the final backend and checked again at `http://127.0.0.1:8765`; the default acquisition completed. The Windows launch script passed PowerShell syntax parsing. The finished desktop view is saved in `artifacts/workbench-final.png`.

Imports currently use ordered primitive JSON geometry and the fixed material library. Native CAD/EDA import, measured transducer/source calibration, elastic full-wave SAM, polychromatic/scattered X-ray transport, cone CT/laminography reconstruction and coupling to electrical/thermal/mechanical fields are future work. The 0.5 baseline supports parallel-beam CT from saved synthetic projections. The docs and UI identify sampling limits and illustrative material/PSF choices.
