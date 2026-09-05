# Proposed next loop: SAM recipes, bounded sweeps and saved comparisons

**Status: proposed, unimplemented.** Prepared against the v0.8 continuous-column work on 4 September 2026. This document describes the next increment; its interfaces, controls and tests do not yet exist. All results remain synthetic, uncalibrated relative signals.

## Deliverable

Let a user save a complete SAM recipe, review two to four explicit acquisition cases, run them through the existing single-worker queue, and compare two completed datasets using their immutable RF and envelope arrays. Start with a focus or frequency sweep, a voxel/continuous method pair, and an intact/defect pair. Exclude new instruments, cloud execution, optimization, arbitrary Cartesian products and automatic registration/resampling.

Reuse [SamVolumeRequest](../virtual_microscopy/volume_schemas.py), [estimate_sam](../virtual_microscopy/sam_volume.py), [VolumeJobManager](../virtual_microscopy/volume_jobs.py), the existing dataset integrity checks and [saved gate processing](../virtual_microscopy/volume_processing.py). Batch records coordinate existing jobs; they are not another solver or dataset format.

## Recipes and explicit cases

A versioned recipe contains a name, canonical UUID, schema version, the full validated twin and acquisition settings, and optional default processing gate. Save it as a new immutable local record; editing creates a revision with a parent recipe ID. Export/import strict JSON with finite values and existing size/primitive limits. Import never executes code or accepts filesystem destinations. Defaults must be visible before saving, including path model and inactive depth samples.

The browser offers “Save recipe,” “Load recipe,” “Create cases,” and a case table. Loading stages controls for review; it does not submit jobs. A recipe loaded from a completed dataset preserves its original request and provenance. If current validation rejects historical geometry, keep the historical record readable and explain why a new acquisition cannot yet be created.

Allow one explicitly selected sweep field: frequency, focus, fractional bandwidth, path model, or active voxel depth samples. Accept two to four unique values; preserve all other acquisition fields, including XY raster and recorded time coordinates. Reject inactive-depth sweeps in continuous mode. Frequency cases must each meet the fixed sample rate's eight-samples-per-period constraint; never raise the rate automatically.

An intact/defect pair instead selects one named authored HBM microstructure defect and compiles two frozen twins with only that defect's enabled state changed. Preserve all six HBM sites and unrelated defects. Display the exact primitive/request differences. A separate global “include defects” pair must explicitly say that it changes every defect's participation. Do not describe that global switch as an isolated missing-bump experiment.

## Preflight and frozen batch identity

Preflight every fully expanded case before submission using the selected solver's actual estimator. Show per-case output bytes, peak numerical workspace, canonical tile rows, padded extent, RF work, applicable voxel/path limits and sampling warnings. One rejected case rejects the complete proposed batch; retain the table so the user can edit it. No automatic coarsening, shortened records or dropped cases.

Cap a batch at four cases and 2 GiB of estimated final arrays. Existing per-case limits still apply. Display summed output and work counters; sequential execution means peak workspace is the maximum case estimate, not their sum. Reserve enough free disk for all pending output, the largest simultaneous temporary workspace and the existing safety reserve, accounting for previously admitted unfinished jobs. Recheck disk immediately before each worker starts or resumes a case. Reservation bookkeeping cannot guarantee against external disk consumption.

Freeze a batch UUID, creation time, recipe revision/hash, ordered cases, case UUIDs, named overrides, complete requests, estimates, material snapshots and solver identities. Hash canonical JSON, excluding mutable progress and timestamps from the numerical input identity. Each case links to exactly one reserved job/dataset UUID. A submitted batch cannot be edited; create a new batch for changed inputs. Repeated submission with the same idempotency key returns the same batch; changed content with that key is rejected.

Add small batch/case tables to the existing SQLite catalog. Stage all dataset manifests before one transaction publishes their jobs and batch links to the worker. A preparation failure publishes no runnable cases; recover unpublished staged records by their explicit batch ownership, never by deleting arbitrary directories. This is a necessary extension to submission, not a loop of independent public `submit()` calls that can leave an accidental half-submitted batch.

## Execution, cancellation and recovery

Use the existing single worker and canonical chunk checksums. Show per-case state, completed rows, dataset link and error, plus “completed cases / total cases.” Do not average percentages with incompatible denominators or imply a time prediction.

Cancel marks the batch as cancellation requested, prevents pending cases from starting, and asks the active case to checkpoint through the existing cancellation mechanism. It does not delete data or interrupt unrelated jobs. Completed cases remain completed. Resume reconciles job/catalog/manifest state, verifies partial identities and chunks, and queues only compatible unfinished cases. A changed solver or damaged immutable source is an explicit failure; completed cases are never reacquired. Stop scheduling later batch cases after a failure until the user resumes or starts a corrected batch.

Crash tests must cover staging, publication, a running chunk, and the gap between case completion and batch-status refresh. Derive aggregate state from case/job records so a crash cannot duplicate a completed acquisition. Read and export old completed datasets without validating them against current geometry constructors.

## Comparisons from immutable measurements

Choose reference A and candidate B from completed `sam_rf_volume` datasets, including compatible historical datasets. Verify paths, manifests and chunk/coordinate checksums before comparison. Freeze a separate comparison UUID, processing version, both dataset identities, manifest snapshots/hashes, array registry, gate and selected trace coordinates. Source arrays are always opened read-only.

Require identical shapes and exact equality of float64 `x_mm`, `y_mm` and `time_us` arrays, plus matching signal units and time-reference meaning. Show incompatible axes and their maximum coordinate differences. Even an unexpected one-ULP disagreement is reported; never relabel coordinates to make the comparison pass. Resampling, temporal intersection, peak alignment and amplitude fitting are deferred. Use exactly representable aligned coordinates for the first method-pair demonstration.

Show all differing settings and geometry hashes. Permit declared method or defect differences; warn when multiple differences prevent attribution to one variable. Historical material or solver differences remain visible. A reference is a chosen comparison baseline, not ground truth.

Stream corresponding row chunks with float64 reductions; do not load two complete volumes. Define differences as B minus A. Compute signed RF bias, RMSE, maximum absolute difference with its `(x,y,time)` locator, and relative L2 `||B-A||₂/||A||₂`. Return null with an explicit zero-reference reason when the denominator is zero. Independently compute envelope MAE, RMSE and relative L2 from the saved envelope, never `abs(RF)`.

For one shared inclusive gate, reuse existing saved-sample selection and tolerance, record actual first/last time and sample count, and reject an empty gate. Produce peak-envelope and RMS-RF maps for A and B, their signed differences, and map-level error metrics. Changing the gate recomputes only comparison products. No peak shifting or per-image normalization enters metrics.

The browser links trace location and time across both sources and differences. Use one shared amplitude scale for A/B, a zero-centered difference scale, labeled units, and raw numeric tooltips. Display scaling never changes stored products. Export a comparison JSON/CSV report with formulas, counts, coordinates and frozen provenance; source Zarr archives remain available separately. A finished report remains readable without rerunning acquisition or requiring both source directories, while new views require the source arrays.

## Acceptance and delivery

- **Analytical:** identical inputs give exact zero differences; known sign reversal, constant offsets, scaling and zero-reference arrays verify every metric independently. Gate boundaries and signed differences match hand calculations; negative RF remains negative. Nonfinite data fails explicitly.
- **Compatibility:** reject mismatched axes, one-ULP coordinate changes, shifted time references, incomplete/corrupt datasets and unsupported kinds. Comparing old completed data never invokes a forward solver or recompiles its twin.
- **Resources/lifecycle:** one invalid case publishes zero jobs; duplicate submissions create no duplicates; disk reservations include other queued output. Instrument bounded reads. Cancel/restart/resume preserves completed dataset bytes and repairs only corrupt uncommitted/partial chunks under existing rules.
- **Browser:** save/load recipe round-trip, review all case differences and limits, run a two-case sweep, cancel/resume, reopen after restart, select A/B, synchronize traces, change gates, and download a reproducible report. Switching display scales leaves metrics unchanged.
- **HBM demonstration:** acquire an isolated HBM 6 intact/missing-bump pair with the other five sites retained; show raw signed traces, common-scale gates and numerical residuals. Report synthetic differences without a detection-rate, measured-resolution or accuracy claim.

Build recipe/case validation first, then transactional batch lifecycle, bounded comparison processing, and browser controls. Deliver one reproducible pair and one small sweep with saved inputs, outputs and verification evidence before broadening the comparison types.
