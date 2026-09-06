# Virtual microscopy expansion plan

Prepared 4 September 2026 against commit `140f433`. Status: roadmap; the implementation note below identifies delivered work.

Implementation note: version 0.2 delivered the first M1 increment: six physical HBM sites, editable layered assemblies, material sections, provisional reference-image metadata and full-depth HBM ROI previews with independent depth sampling. Version 0.3 adds the M2 dataset/job foundation and first M3 SAM volume workflow: chunked signed RF/envelope storage, frozen provenance, estimates, process worker, progress/cancel/resume, independent record/bandwidth/standoff controls, saved slices and post hoc gates. See [SAVED_VOLUMES.md](SAVED_VOLUMES.md). Version 0.4 delivers M4: full-angle CPU parallel projection, independent material/detector grids, source/detector/rotation controls, chunked counts/transmission/log/mask arrays, saved poses, sinograms and a shared mixed-instrument job queue. See [XRAY_VOLUMES.md](XRAY_VOLUMES.md). Acoustic lateral sampling still shares its material raster. Reconstruction, depth conversion, hierarchical transforms/materials and explicit TSV/microbump geometry remain planned below.

Version 0.5 delivers the first M5 increment: CPU filtered backprojection from
frozen saved X-ray projections, independent spatial bounds/counts, Hann/Ram-Lak
filters, linked XY/XZ/YZ views, retained negative values and geometric coverage,
and resumable derived datasets with source provenance. See
[RECONSTRUCTION.md](RECONSTRUCTION.md). This resolves the CPU reconstruction item
in the earlier implementation note; advanced reconstruction remains planned.

Version 0.6 adds declared homogeneous/layered time-to-depth mapping from saved
acoustic RF and envelope. It preserves source X/Y and raw recordings, records
surface-time/velocity provenance, masks unsupported times and model depths, and
provides linked spatial slices with original-time readouts. See
[SAM_DEPTH.md](SAM_DEPTH.md).

The current app has a six-site H100 package, editable layered HBM regions, saved
X-ray projections and reconstructed attenuation volumes, saved acoustic RF and
derived depth estimates, and independently controlled acquisition parameters.
Version 0.7 implements the first explicit local HBM microstructure increment:
48 bumps, 54 TSVs, targeted local defects, feature-centered sections, close views,
fine full-depth ROI previews and saved SAM signals. See
[HBM_MICROSTRUCTURE.md](HBM_MICROSTRUCTURE.md). The sections below
retain the broader expansion design; the milestone table distinguishes shipped
baselines from future fidelity work.

Version 0.8 delivers the opt-in continuous normal-incidence material-path
increment for preview and saved SAM. It removes vertical voxel-center boundary
quantization for the authored primitives, retains the full specimen and lateral
halos, and preserves the original voxel method and immutable historical data.
See [COLUMN_PATHS.md](COLUMN_PATHS.md) for implementation and limitations.

Version 0.9 delivers immutable SAM recipes, reviewed two-to-four-case batches,
transactional publication through the existing worker, and comparisons of saved
signed RF/envelope signals with exact coordinates, shared scales, inclusive gates
and frozen reports. See [ACQUISITION_COMPARISONS.md](ACQUISITION_COMPARISONS.md).
This implements the acquisition-comparison part of M6; the acoustic propagation
model itself is unchanged.

Version 0.10 adds the corresponding X-ray recipe/batch workflow and saved counts,
transmission and logarithm comparisons. Exact detector/angle/pose compatibility,
explicit photon/observation policies and common logarithm support protect the
interpretation of residuals. See [XRAY_COMPARISONS.md](XRAY_COMPARISONS.md).
The X-ray forward model and saved product schema are unchanged.

Version 0.11 delivers a usable standalone layered-acoustics instrument: full
continuous-column extraction, explicit layer and exterior-medium editing,
coherent complex spectra against primary/direct baselines, and causal single-slab
RF with an aggregate omitted-echo certificate. Immutable reports retain geometry,
assumptions and numerical arrays. See [LAYERED_ACOUSTICS.md](LAYERED_ACOUSTICS.md).
This partially implements [LAYERED_ACOUSTICS_SPEC.md](LAYERED_ACOUSTICS_SPEC.md).
The next dependency is a bounded general multilayer time-response method with
independent tail/window and sampling checks, followed by opt-in saved SAM ROI
integration. Existing SAM volume propagation and time-to-depth semantics are
unchanged; spectral passivity alone is insufficient to certify reverberant RF.

Version 0.12 resolves that standalone time-response dependency for an explicitly
declared causal gamma excitation. It uses carrier-centred Laplace inversion with
separate analytic alias/cutoff bounds and Arb arithmetic enclosures, including
the exact returned numeric values. Full HBM column RF, quadrature and envelope
are inspectable with pulse/record/tolerance/precision controls and immutable
reports. See [CAUSAL_LAYERED_RF.md](CAUSAL_LAYERED_RF.md). The new gamma waveform
does not replace the original Gaussian slab excitation. The next milestone is
opt-in saved SAM ROI integration with explicit observation/focus/standoff
semantics, complete lateral context, resource bounds, original solver identities,
and rejection of ordinary unique-depth interpretation of multiple reflections.

Implementation loops now continue without waiting for another user prompt.
Each loop must finish a bounded working increment, exercise its numerical and
browser behavior, update these records, and publish verified code to the existing
public repository. Unavailable measured calibration or hardware is a concrete
dependency, not a reason to substitute synthetic evidence for measured results.

Version 0.13 delivers the first opt-in saved causal ROI: full-depth independent
columns, exact resolved-stack reuse, float64 real/quadrature/magnitude arrays,
per-column numerical certificates, resumable row commits, time views and raw
exports. The observation is explicitly unfocused and lossless; it does not
inherit primary-echo focus, Gaussian lateral blur, or unique-depth mapping.
See [CAUSAL_SAM_VOLUMES.md](CAUSAL_SAM_VOLUMES.md).

Version 0.14 delivers [compatible saved causal comparisons](CAUSAL_COMPARISONS.md):
exact source coordinates and excitation/observation checks, signed complex
residuals, separately propagated source and subtraction bounds, linked time
inspection, immutable reports and lossless exports. The first controlled HBM
comparison uses the saved intact and missing-bump recordings without reacquisition.
A defined finite lateral observation with propagated numerical bounds is delivered
in v0.15 below. Additional material loss assumptions and measured calibration
remain extensions.

Version 0.15 delivers [finite coherent spatial observations](COHERENT_OBSERVATIONS.md)
as a distinct derived dataset: fixed exact 3 × 3 weights, fully supported interior
centers, signed complex mixing before magnitude, separate source/arithmetic/total
bounds, resumable typed row commits, and source-free completed views/exports.
The shared background worker preserves historical acquisition identities and
batch controls. This is an explicit discrete filter; focus, lens geometry,
diffraction, material losses and measured beam calibration remain unimplemented.

Version 0.16 implements [compatible comparisons of the saved derived observations](OBSERVATION_COMPARISONS.md).
Exact retained and full support coordinates, stencil/phase, excitation and
numerical contracts are checked before comparison. Distinct complex and magnitude
source bounds propagate into six residual-bound maps. Immutable reports preserve
ordinary gate statistics, linked initial views and complete deduplicated source
snapshots. The existing independent-column comparison retains its original guards.

Version 0.17 introduces a separate [scalar SLS material instrument](SLS_ACOUSTICS.md)
with up to eight manually authored layers, explicit density/moduli/relaxation,
coupled attenuation and dispersion, discrete-frequency material/scattering
diagnostics and reflected causal gamma RF with a separately identified numerical
certificate. Its [proof](SLS_MATERIAL_PROOF.md) and independent transfer/time
checks establish the computational contract; the assumed material values are
not measured HBM data. Earlier schemas, acquisition kernels and resume identities
remain unchanged. The existing constant-loss-plus-delay model is itself causal.

Version 0.18 adds [compatible saved SLS comparisons](SLS_COMPARISONS.md), with
explicit material differences, exact shared frequency/time axes, complete source
snapshots and five full-record reflected residual bounds. Spectral and statistical
products remain ordinary diagnostics.

Version 0.19 adds [explicit material assignments and column coverage](MATERIAL_ASSIGNMENTS.md):
complete frozen twins, authored material-ID bindings or exact saved report-layer
parameters, separate whole-inventory/subset status, and one full-depth ordered
column with missing-material locators. Assignments and saved column evidence are
immutable and remain readable without their original sources. No propagation
follows from a complete coverage status. The observed HBM6 columns have 26/28
segments, exceeding the standalone SLS eight-layer limit; finite ambient water
also needs an explicit supported medium resolver.

Version 0.20 resolves standalone finite real/SLS mixing with
[explicit typed layers](MIXED_ACOUSTICS.md), exact represented real-medium
impedance/speed, a mixed flux/analyticity proof, coherent frequency diagnostics
and reflected gamma RF with outward numerical bounds. Finite water spacing and
exterior standoff remain separate controls. Saved reports preserve typed inputs,
all arrays and their own model/proof identities. This does not connect frozen
assignments or extend the eight-layer admission limit.

Next establish larger-stack numerical/resource contracts and an explicit frozen
assignment resolver before creating saved SLS/HBM responses. Do not truncate or average geometry to
fit the old cap, invent an ambient-water relaxation, or infer calibrated laws from
one attenuation value. Frequency-dependent X-ray spectra and calibrated
source/detector effects remain separate physics work.

## 1. Correct the specimen and preserve the image evidence

NVIDIA's [Hopper architecture article](https://developer.nvidia.com/blog/nvidia-hopper-architecture-in-depth/) describes six possible HBM stacks for full GH100 and five enabled HBM3 stacks for the 80 GB SXM configuration. Its Figure 1 rendering depicts six peripheral package bodies. Omitting the sixth physical position was an inappropriate simplification for microscopy. The revision should have six physical sites and separate functional configuration. The rendering does not establish the sixth body's internal construction or every production revision.

Represent each site with physical presence, material construction, functional state, placement, and evidence. An electrically disabled component still participates in attenuation and acoustics. Start with six visible positions; the sixth position can use a clearly marked assumed stack construction, with configurable alternatives and unknown functional state. Do not label its internals as verified or silently call it a dummy.

The user-supplied cross-section is 693 × 502 pixels. The user estimates **approximately 4.6 µm/pixel**. If that scale applies to this exact raster, the field of view is approximately **3.1878 × 2.3092 mm**. Preserve this as a user estimate with unknown calibration uncertainty and resizing history. Pixel spacing is not instrument resolution.

The unchanged image is preserved locally in `artifacts/reference-inputs/h100-cross-section-e2b1274b5593.png`, outside version control. SHA-256: `e2b1274b5593236fff9c9a3183cd6f73808f890ab2acca32e267c057ec8d12d9`.

Visible observations are repeated fine bright rows, layered bands, a larger bright joint row, and lower elongated features. Candidate interpretations include inter-die connections and larger package interconnects. Material identities, exact die count, image orientation, projection versus reconstructed-slice status, and whether the lower streaks are structures or artifacts remain unresolved. One cross-section does not uniquely specify a 3D bump map.

Add an image-reference workspace: preserve the original raster, attach scale and orientation metadata, draw measurement lines, identify candidate interfaces, and compare a matching simulated projection or reconstructed slice. Record selected pixel endpoints and parameter provenance. Match acquisition type before comparing images. Use the current image as a structural constraint, not as an experimental validation set.

## 2. Replace solid HBM blocks with reusable assemblies

Create a hierarchy: package → HBM site → stack → base/DRAM die → interface → TSV/microbump array. Use local rigid transforms, stable component IDs, procedural arrays and instanced rendering. Avoid expanding millions of repeated structures into the current flat 600-object JSON format.

| Part | Parameters and behavior |
| --- | --- |
| Stack envelope | Footprint, orientation, stack height, molding/cap dimensions, placement and evidence |
| Base die | Separate thickness, silicon body, representative routing/connection layers |
| DRAM dies | Configurable die count, per-die thickness, lateral offset, orientation and optional warpage |
| Inter-die regions | Gap, underfill, bond condition, microbump height/diameter/pitch, pad and barrier layers |
| TSV arrays | Diameter, depth, pitch, copper fill and optional dielectric liner; explicit geometry within selected regions |
| Stack-to-interposer connection | Microbump/pad arrays, local underfill, interposer silicon and representative redistribution layers |
| Defects | Missing/bridged bump, internal bump void, TSV void, interface delamination, crack and layer misalignment; size and location tied to a specific component/interface |
| Materials | Density, energy-dependent X-ray attenuation, longitudinal/shear speeds where supported, frequency-dependent loss, elastic properties where supported, source and uncertainty |

Provide generic 8-high and 12-high HBM3 templates, while keeping the exact H100 variant configurable and unverified until supported by specimen-specific evidence. SK hynix describes 16 GB/eight-die and 24 GB/twelve-die HBM3 constructions; that establishes available templates rather than the identity of this image. [SK hynix HBM3 stack reference](https://news.skhynix.com/en/meet-the-sk-hynix-team-behind-the-worlds-first-12-layer-hbm3/)

The manufacturer also describes TSVs, microbumps and protective material between dies, and distinguishes a base die from memory core dies. These support the component hierarchy; dimensions and formulations still need measurement or explicit assumptions. [Packaging reference](https://news.skhynix.com/en/small-size-big-impact/), [base/core-die reference](https://news.skhynix.com/en/rulebreakers-revolutions-design-scheme-elevates-hbm3e/)

Use three simulation detail levels:

1. **Package overview:** coarse material bodies for navigation and full-field scans.
2. **Layered HBM:** explicit silicon/interface layers with effective interconnect regions for affordable volume acquisitions. Preserve acoustic interfaces analytically where voxel averaging would erase them.
3. **Microstructure ROI:** explicit bumps, TSVs, pads and defects within a fine region of interest. Preserve the coarse surrounding package along X-ray paths and upstream acoustic paths; cropping an ROI must not silently remove surrounding attenuation or acoustic loading.

Visual detail and numerical detail must be reported independently. Show which components actually contribute to each simulation and which are undersampled.

## 3. Define the volumetric products precisely

| Product | Canonical axes | Interpretation |
| --- | --- | --- |
| Material truth | `[z,y,x]` plus geometry/material records | Known synthetic material labels, interfaces and defect geometry |
| X-ray projection acquisition | `[view,v,u]` | Raw counts or ideal intensity, detector coordinates, source/detector pose for each view |
| X-ray processed projections | `[view,v,u]` | Corrected transmission and line integrals derived from raw data; retain dark/flat references and invalid-pixel masks |
| X-ray reconstruction | `[z,y,x]` | Reconstructed attenuation; use mm⁻¹ for calibrated monoenergetic output, explicit effective/reconstruction units otherwise |
| SAM RF acquisition | `[y,x,t]` | Signed waveform at every scan location; relative pressure initially, Pa/volts only with an applicable calibrated model |
| SAM analytic/envelope products | `[y,x,t]` | Derived I/Q, envelope, phase or energy, each with processing history |
| SAM depth estimate | `[z,y,x]` | Derived amplitude mapped through a specified velocity/travel-time model, with validity and uncertainty information |

An angular projection stack and an xyz reconstruction are different datasets. A SAM time axis is not a depth axis. Retain the original RF even when displaying depth. For layered media, begin with known-layer travel times and interface tracking; a single global conversion `z = c t / 2` is insufficient. Track whether the depth map used synthetic truth, user-supplied layer velocities, or estimated velocities so reconstruction does not secretly use unavailable ground truth.

Focus-series and repeated acquisitions introduce extra dimensions, such as `[focus,repeat,y,x,t]`. Averaging, envelope extraction and gate changes create derived products. They do not overwrite original records. Raw wavefields throughout the specimen, if added later, are separate from scanned RF volumes and have much greater storage costs.

## 4. Architecture grounded in the current code

| Current code | Required change |
| --- | --- |
| `physics.py:voxelize()` ties nx/ny to one resolution and nz to twice it | Separate specimen discretization, detector sampling, acoustic stage spacing, time sampling and reconstruction voxels; permit anisotropic grids and local bounds |
| `physics.py:project_xray()` uses z slabs with tan(angle)/cos(angle) | Introduce general ray/volume intersection and traversal. The present projector cannot be looped through 360° because its parameterization fails near 90° |
| `physics.py:_sam_signals()` computes complex RF tiles and discards most of them | Refactor into a tile generator and storage sink; persist signed RF and optionally analytic data before making A/B/C views |
| SAM record duration currently depends partly on the selected gate | Make record start, duration and sample rate acquisition settings; make gates independent processing settings |
| `server.py` holds a global lock during a synchronous request | Add a local job worker, persisted queue/catalog, progress, cancellation checkpoints and recoverable output |
| Browser stores complete JSON arrays and exports blobs | Send manifests, previews and requested slices; export authoritative binary arrays from disk |
| `schemas.py` has flat primitives and fixed materials | Add hierarchical TwinV2, material records, instrument/scan definitions and processing requests, with a v1 adapter |

Keep FastAPI, NumPy/SciPy and the existing Three.js client initially. Split the code by responsibility rather than replace the whole app:

```text
virtual_microscopy/
  geometry/       assemblies, HBM generators, transforms, ROI sampling
  materials/      versioned records and interpolation models
  acquisitions/   X-ray and SAM configurations, trajectories, validation
  solvers/        CPU reference, optional ASTRA, layered acoustics, future elastic ROI
  processing/     corrections, reconstruction, gates, depth conversion
  datasets/       manifests, Zarr stores, slice/chunk access, exports
  jobs/           local workers, progress, cancellation, resume
web/src/
  specimen-editor, scan-planner, acquisition-monitor, volume-viewer
```

Use a local process worker and SQLite job catalog first. Add `/api/v2/estimate`, `/api/v2/jobs`, job status/cancel/resume, dataset listing, slice/chunk access and export endpoints. Keep the legacy preview endpoint through an adapter. Cap concurrency by a resource budget and keep interactive slice reads available during acquisition.

Use **Zarr** as the primary chunked array store, JSON for manifests and small metadata, NPZ for small convenience exports, and TIFF stacks with a sidecar for interoperable image exchange. Zarr defines chunked multidimensional typed arrays suitable for partial reads. Benchmark chunk shapes and compressors on both projections and RF before fixing defaults. [Zarr specification](https://zarr-specs.readthedocs.io/en/latest/v3/core/)

Every dataset needs axes, units, shape/dtype, coordinate arrays or origin/spacing, specimen-to-instrument transform, per-view/stage poses, immutable input/material snapshots, backend/version, random seed scheme, processing lineage, calibration status, completion masks and checksums. Convert the current internal `[y,x,z]` representation explicitly to stored `[z,y,x]`. Test all backend transpositions with asymmetric fiducials.

Write chunks transactionally and mark incomplete datasets clearly; missing chunks are not valid zero measurements. Cancellation must leave a readable manifest and known completed chunks. Resume must reproduce the same data, including noise, regardless of tile partitioning. Browse downsampled previews without changing the saved full-resolution values.

## 5. X-ray acquisition and reconstruction

First implement a general CPU reference projector using bounded ray intersections and voxel traversal or controlled ray marching. Validate axis-aligned and oblique boxes/spheres, including 90° and rays entering through side faces. Forward-project material-dependent attenuation, then apply the detector model. Keep geometry voxels and detector pixels independent.

Add full-angle parallel-beam acquisition first, followed by cone-beam geometry with source position, detector basis, distances, offsets and rotation axis. Save every pose; include detector truncation checks. Source-to-detector distance must exceed source-to-object distance for the intended geometry. Display magnification and derived object-plane sampling rather than accepting inconsistent independent values.

Add spectrum-weighted transmission, source focal-spot effects, detector PSF/MTF, photon and electronic noise, dark/flat references, saturation and bit depth in documented stages. Tube kVp becomes active only when a spectrum model exists; it is not synonymous with monoenergetic keV. Current/exposure becomes photon fluence only through a declared source-response calibration. A detector model can produce a real numerical projection stack while remaining synthetic.

Spectrum/filtration support first requires expanded attenuation data. The current material library covers 40–150 keV, above the tin K edge. A tube spectrum may include lower energies and filter-material absorption edges. Extend the material/filter tables over all selected energy bins and use edge-aware interpolation; do not reuse smooth interpolation across discontinuities, extrapolate unsupported bins, or silently omit them. Include this data extension in M6 before enabling those controls.

For reconstruction, deliver a small CPU parallel-beam baseline, then optionally use ASTRA for larger 3D data. ASTRA supports vector-defined parallel/cone geometry and GPU projection/reconstruction; its CPU-only build is limited to 2D. Its projection axis order requires an adapter. Treat GPU availability as a capability check and retain a small independent CPU path. [ASTRA installation](https://astra-toolbox.com/docs/install.html), [forward projection](https://astra-toolbox.com/docs/algs/FP3D_CUDA.html), [data layout](https://astra-toolbox.com/docs/data3d.html)

Use FDK for a supported circular cone-beam trajectory and isotropic reconstruction voxels. Use iterative SIRT initially for vector-defined laminography/limited-angle geometry. Arbitrary trajectory support does not restore missing information. ASTRA documents artifacts for substantially noncircular FDK inputs and rejects anisotropic FDK voxels. Show geometry-dependent resolution and artifact limits. [FDK](https://astra-toolbox.com/docs/algs/FDK_CUDA.html), [geometry](https://astra-toolbox.com/docs/geom3d.html), [SIRT](https://astra-toolbox.com/docs/algs/SIRT3D_CUDA.html)

Define laminography tilt using the actual rotation-axis/beam vectors, with an instrument diagram and saved transforms. Never reuse an ambiguous angle slider. Support full rotation at an oblique beam-to-axis angle and separately identify limited angular spans. Add regularization, missing/bad-view masks and calibration corrections after the baseline round trip is verified.

ASTRA declares GPLv3; evaluate its fit before bundling it into the public distribution. TIGRE is a BSD-3-Clause alternative if licensing or algorithms make it preferable. Keep the adapter boundary explicit; it does not itself decide license compatibility. [ASTRA](https://astra-toolbox.com/#license), [TIGRE](https://github.com/CERN/TIGRE)

## 6. SAM volume and progressively richer propagation

The fastest useful volume milestone is preserving the existing RF tiles. Make record start/duration and sample rate independent of gate settings. Define timing from the transducer with explicit water standoff and a stored specimen-surface time reference; retain a compatibility option for the old top-plane time zero.

Read A-scans, arbitrary X/time and Y/time B-scans, gates and surface tracks from the frozen RF store. Add multiple C-scan gates with peak, RMS, integrated-envelope, polarity and arrival-time outputs. Preserve peak/RMS definitions and units. Depth conversion produces a new dataset with its velocity model and inaccessible/shadowed regions; avoid treating absence of signal as proof of no material.

Next introduce configurable pulse bandwidth or measured transmit/receive impulse response, aperture/focal length, depth-dependent beam model, water-path effects and layer reverberation. A one-dimensional layered transfer model can improve repeated-interface behavior cheaply, while remaining an approximation to a focused three-dimensional beam. Benchmark coherent phase and attenuation against analytical layered cases.

Add an optional wave-solver adapter for bounded ROIs after the RF pipeline works. k-Wave's acoustic `kspaceFirstOrder3D` models compressional propagation with heterogeneous properties; its elastic `pstdElastic3D` includes compressional/shear propagation in isotropic elastic/viscoelastic media. Single-crystal silicon anisotropy, actual bond laws and measured high-frequency loss require additional models. Do not advertise a fluid acoustic solver as full solid-material physics. [Acoustic solver](https://www.k-wave.org/documentation/kspaceFirstOrder3D.php), [elastic solver](https://www.k-wave.org/documentation/pstdElastic3D.php), [layered elastic example](https://www.k-wave.org/documentation/example_ewp_layered_medium.php)

Treat wave-grid spacing, time step, boundary absorption, bandwidth and domain size as convergence choices. CPU/GPU support must be checked for the selected solver; the compiled acoustic GPU backend is not automatically an elastic GPU backend. A compute H100 and the H100 specimen are independent concepts. [k-Wave downloads](https://www.k-wave.org/download.php)

## 7. Scanning controls and where they take effect

The UI should group controls into Specimen, Instrument, Trajectory, Acquisition, Processing and Display. Add named presets, save/load scan recipes, and parameter sweeps. Show units and coupled constraints; unsupported settings should be disabled with an explanation. Every enabled control must change a specified computation or an explicitly labeled timing estimate.

| Group | Controls | Stage |
| --- | --- | --- |
| Shared specimen | ROI bounds, component detail level, geometry spacing x/y/z, material assignment, defect parameters | Geometry/material construction |
| X-ray source | Monoenergy or spectrum, kVp with spectrum model, filter material/thickness, photon fluence, focal-spot size; calibrated current/exposure where available | Source and transport |
| X-ray geometry | Parallel/cone, source/object/detector distances, detector rows/columns/pitch, detector offsets/tilt, rotation center/axis, start/span/views or explicit poses, laminography angle | Ray geometry |
| X-ray detector | Binning, PSF/MTF, quantum/electronic noise, gain, dark/flat fields, saturation, ADC depth, repeated frames | Detection |
| X-ray reconstruction | Algorithm, volume bounds/spacing, filter, iterations/stopping rule, regularization, masks, corrections | Processing of saved projections |
| SAM transducer | Center frequency, fractional bandwidth/pulse or impulse response, aperture and focal length with derived F-number, focus, focus series, standoff, coupling-medium properties | Transmit/receive propagation |
| SAM stage | ROI x/y, independent dx/dy, raster direction, stage rotation, dwell, scan speed, repeats | Positions and timing; enforce speed/dwell/PRF consistency where modeled |
| SAM digitizer | Record start/duration, sample rate, trigger reference/jitter, ADC depth/noise/clipping, analog gain where modeled | RF acquisition |
| SAM processing | Multiple gates, surface tracking, envelope/polarity/phase, peak/RMS/integral, digital gain/TGC, velocity model, depth conversion | Processing of saved RF |
| Display | Contrast window, colormap, opacity, interpolation, clipping planes, slice position | Visualization only |

Validate temporal Nyquist against the supported pulse bandwidth and require numerical convergence beyond that minimum. Validate focal/aperture combinations and prohibit invalid geometry. Preserve raw ADC/pressure traces before digital gain and envelope processing. Stage timing controls initially affect an explicit acquisition-time estimate unless a motion/noise model uses them; they must not pretend to improve spatial resolution.

For source spectrum, acoustic bandwidth and material properties, reject requests outside the validated interpolation/model range instead of silently extrapolating. Export the effective configuration, including derived values, with the run.

## 8. Volume exploration and reference comparison

Add a dataset browser with linked XY/XZ/YZ slices, a 3D volume view, shared physical cursor, scale bars and quantitative values. Give projections an angle scrubber/sinogram view and RF a time axis. Display ground truth, reconstruction and measured data with distinct evidence labels.

Keep exploded geometry as a viewing transform only. Use the true geometry for simulated slices and overlays. Support multiple acquisition tabs, intact/defective comparison, synchronized coordinates and saved camera/slice state. Projection pixels should not map to a unique 3D point without a ray or reconstruction model.

The reference-image pane should overlay candidate layer positions, compare line profiles and retain the user's provisional 4.6 µm/pixel calibration. Imported raw projections need geometry, dark/flat data and pixel units; imported SAM RF needs sample timing, stage coordinates, waveform units and calibration metadata. Unknown quantities remain unknown rather than being filled with quiet defaults.

## 9. Resource budget

These are uncompressed arithmetic estimates, not runtime benchmarks:

| Dataset | Size | One array |
| --- | --- | --- |
| 60 × 60 × 2.65 mm package at 5 µm isotropic pitch | 12,000 × 12,000 × 530 | 71.08 GiB uint8 labels; 284.31 GiB per float32 field |
| 1 × 1 × 0.8 mm ROI at 2 µm isotropic pitch | 500 × 500 × 400 | 0.373 GiB per float32 field |
| 720 X-ray views, 1024² pixels | 720 × 1024 × 1024 | 2.8125 GiB float32 |
| 512² acoustic scan, 4096 time samples | 512 × 512 × 4096 | 4 GiB float32 RF; another 4 GiB for an equally sized envelope |

This requires multiscale geometry and chunked processing from the beginning. A wave solver needs several fields, boundary layers and working buffers beyond the listed scalar array. Spectral projections and raw repeats also multiply storage. Preflight estimates must include peak RAM/VRAM, disk, temporary buffers and the number of saved products; compression savings cannot be assumed.

Never silently lower the requested sampling. Offer a smaller ROI, fewer views, shorter record or coarser preview as explicit alternatives. Benchmark a small representative job to estimate larger-run time and report uncertainty. Store only requested products while retaining enough raw data and metadata for reproducible processing.

## 10. Implementation sequence and acceptance gates

| Milestone | Deliverable | Acceptance gate |
| --- | --- | --- |
| M1 — Geometry and evidence | Six physical HBM sites, independent functional state, layered HBM generator, reference calibration metadata, ROI selection, v1 adapter | Six sites survive export/import; electrical state alone leaves imaging geometry unchanged; source facts and assumptions remain separate; deterministic generators and valid layer/defect placement |
| M2 — Dataset foundation | Independent acquisition/grid schemas, Zarr store, local worker, job progress/cancel/resume, preflight estimate | Binary round trip preserves axes/units/transforms; partial chunks are identified; resume matches uninterrupted output; out-of-budget requests fail before allocation |
| M3 — SAM volume | Saved signed RF at every raster position, adjustable record/bandwidth settings, post hoc gates, XY/X-time/Y-time browser | Stored traces match the preview baseline; gates reproduce baseline C-scans; gate/display changes do not rerun propagation; timing/polarity and tile-boundary tests pass |
| M4 — X-ray projection volume (delivered in 0.4) | Full-angle CPU parallel projector, independent detector/material sampling, source/detector/rotation controls, saved poses and counts/transmission/log/mask arrays, view/sinogram browser | Analytical lengths at 0°, 90° and oblique views; no angle singularities; asymmetric orientation tests; seeded noise statistics, byte-identical resume and preserved legacy SAM datasets |
| M5 — Reconstruction (CPU baseline delivered in 0.5) | CPU parallel FBP from saved projections, linked spatial slices, filter/bounds controls, coverage masks and source provenance; GPU cone CT and iterative laminography remain future | Independent analytical ellipse scale/orientation, detector-sampling convergence, held-out projection residuals, 180°/360° weighting, invalid/truncated/limited-angle handling, byte-preserving resume and source independence after completion |
| SAM depth extension (delivered in 0.6) | Declared homogeneous/layered velocity, source-water or explicit surface time, independent RF/envelope mapping, spatial slices, support masks and frozen source provenance | Known reflector depths, velocity/reference sensitivity, layered travel times, nonzero recordings, preserved signed RF/X/Y, out-of-model/record masks, resumable byte-identical data and read-only reopened views |
| M6 — HBM microstructure (patch 0.7; continuous columns 0.8; SAM comparisons 0.9; X-ray comparisons 0.10; standalone layered acoustics 0.11–0.12; causal ROI 0.13; causal comparisons 0.14) | Explicit bumps/TSVs/local defects, inspection, continuous material paths, saved SAM/X-ray parameter comparisons, standalone causal multilayer RF, saved independent-column causal ROI and bounded causal residual reports delivered; finite lateral observation and spectrum/detector response follow | Feature/path and sampling sensitivity; surrounding-package contributions preserved; independent analytical comparisons; defect observability reported per instrument/configuration |
| M7 — Wave physics and calibration | Bounded elastic ROI, measured instrument responses, measured-data import/comparison | Time/grid/domain convergence, interface/transmission/mode checks, matched acquisition geometry, held-out measurement agreement and uncertainty |
| M8 — Coupled multiphysics | Temperature/deformation/stress fields driving material and geometry updates between acquisitions | First validate one-way coupling and unit/coordinate transfer, then introduce validated feedback loops if the research requires them |

M3 and M4 can proceed in parallel after M2. M5 depends on M4. M6 builds on M1 and the saved-data pipelines. Full-wave work follows a stable RF/ROI system; it should not delay useful numerical volumes.

Keep the existing regression suite, then add tests that measure the new scientific and storage behaviors. Do not use visual similarity or a forward/inverse pair with identical discretization as the only validation. Separate sampling pitch, image resolution, registration error, reconstruction error and defect detectability. For registered measured volumes, report held-out 3D landmark error separately from pixel size.

## Recommended next release

The first M1–M5 increments, SAM depth extension, bounded M6 bump/TSV patch,
continuous-column numerical method and both SAM/X-ray recipes, batches and saved
comparisons are delivered. Versions 0.11–0.12 also deliver standalone coherent
multilayer spectra, Gaussian single-slab RF, and a separately selected causal
gamma multilayer RF response with numerical error bounds and immutable reports.
Version 0.13 also implements the first
[saved causal column-response ROI](CAUSAL_SAM_VOLUMES.md), using explicit identity
observation and verified typed row/certificate storage. Version 0.14 adds controlled
comparisons of compatible saved causal data with separate source and subtraction
bounds. Versions 0.15–0.16 add the separately defined finite coherent lateral
observation and its compatible saved comparisons. Their complete support,
explicit weights/phase, complex mixing and distinct pressure/magnitude bounds
are documented above. Version 0.17 adds a standalone SLS material law and its
independent controls. Version 0.18 adds compatible saved SLS comparisons.
Version 0.19 adds immutable material assignments and full-depth column coverage.
Version 0.20 adds [standalone mixed real/SLS finite layers](MIXED_ACOUSTICS.md),
with explicit material tags, finite water spacers, coherent spectra, reflected
gamma RF and separate numerical error bounds. Eight authored layers remain the
limit. Next establish larger-stack admission with independent conditioning and
resource evidence, then resolve frozen assignments and ambient/reference-plane
placement before expanding saved spatial propagation. Preserve historical
data and numerical identities, distinguish excitation/observation changes, and
keep ordinary unique-depth mapping unavailable for repeated returns.

Then extend richer propagation and acquisition response.
Extend material data before spectrum controls. Optional GPU/cone/laminography
backends and refined all-angle local X-ray integration remain separate extensions.
Reuse job, provenance and dataset foundations throughout. Continuous vertical
geometry does not establish convergence of lateral sampling, RF sampling or
omitted wave physics, and no numerical method certifies experimental resolution.

Before assigning specimen-specific dimensions, resolve the original image scale/resizing history, image type and orientation, H100 revision, HBM vendor/stack construction and instrument settings as information becomes available. Those unknowns do not block the architecture, generic generators or synthetic volume work.
