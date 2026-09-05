# Proposed next loop: X-ray recipes and projection comparisons

**Status: proposed and unimplemented.** Prepared against v0.9 on 4 September 2026. This is a bounded extension of saved acquisition workflows, not a new X-ray solver or an experimental validation claim.

## Scope and existing foundation

Extend immutable recipes, explicit two-to-four-case plans and transactional batches to [XrayVolumeRequest](../virtual_microscopy/xray_schemas.py). Reuse the existing CPU parallel-beam projector, [XrayDatasetStore](../virtual_microscopy/xray_datasets.py), single worker, per-view checksums and [projection viewer](../virtual_microscopy/xray_processing.py). Generalize the SAM-specific dispatch in [batch_jobs.py](../virtual_microscopy/batch_jobs.py); do not implement batches as successive public job submissions.

Add a discriminated X-ray recipe kind while preserving existing SAM records and endpoints. Freeze complete validated requests, explicit defaults, parent revisions and provenance. Loading/importing never starts jobs. New recipes require current validation; historical completed-source recipes remain readable/exportable without current twin construction. Validate paths, completion and bounded JSON before arrays. A historical request rejected for reacquisition stays preserved.

## Cases and durable execution

Initially allow one sweep field: `energy_kev`, `photons`, `detector_fwhm_mm`, `geometry_nx`, `geometry_ny`, `geometry_nz`, `noise`, `seed` or `include_defects`. Use existing types/ranges, two-to-four unique values, and exactly two values for booleans. Preserve every other field. `path_model` is not an X-ray acquisition setting. Defer view-count, angle, detector-size, offset and rotation-center sweeps because this comparison increment requires matching measurement geometry.

Also support the established isolated authored HBM-defect pair: toggle one identified local defect while retaining other defects, all six sites and primitive precedence. Keep the global `include_defects` pair explicitly labeled as changing every defect's participation. Show exact setting/primitive differences and geometry/material/solver hashes.

Each batch contains one acquisition kind. Dispatch validation, estimation, storage creation and catalog job kind explicitly; X-ray progress counts views. Preflight all cases, freeze identities and stage every manifest before one transaction publishes runnable jobs. Preserve idempotent replay, staged-record ownership, disk reservations across queued work, cancellation checkpoints, failure blocking and resume without reacquiring completed cases. Older SAM journals and batches retain their interpretation.

Retain four-case/2-GiB batch caps and current per-case limits: 512-MiB output, 512-MiB estimated numerical workspace, 64-million material cells and 250-million projection-work units. Publish geometry, halo, truncation and work estimates; sum output/work but take maximum sequential workspace. Reject the entire proposal on any failure. Never silently reduce geometry, views or photon settings.

## Exact comparison compatibility

Introduce immutable `xray_projection_comparison` reports and explicit comparison requests selecting reference A, candidate B, product and normalization policy. Proposed `/api/v2/xray-comparisons` create/list/read/view/export operations mirror SAM comparisons; recipe/case endpoints dispatch by frozen kind. Require completed X-ray sources; verify frozen identities, array relationships and every consumed canonical view read-only. Historical reads must not invoke a solver.

Require identical `[view,v,u]` shapes and exact float64 equality of `angles_deg`, `u_mm`, `v_mm`, `ray_direction_xyz`, `detector_center_mm`, `detector_u_xyz` and `detector_v_xyz`. Check descriptors, units and pose conventions. Report incompatible fields, first disagreement and maximum numerical difference, including one-ULP differences. Do not reorder views, equate angles modulo 360°, match opposed projections, shift virtual planes or interpolate coordinates implicitly.

Detector coordinates are local millimetres; saved basis vectors are unit vectors and offsets already belong to detector centers. A ray is not a unique specimen point. A comparison baseline is chosen by the user. Saved “raw projections” mean authoritative acquisition products; they are not exact attenuation truth. The stored negative logarithm includes detector blur, finite counting and zero-count handling.

## Products, normalization and masks

Preserve all four original products and their meanings:

- `counts`: observed integer Poisson draws or fractional expected counts when noise is off. Zero counts are legitimate measurements.
- `transmission`: stored counts divided by that source's frozen incident photons, `I0`.
- `line_integrals`: `-log(counts/I0)` for positive counts; only zero counts use a half-count placeholder.
- `valid_mask`: binary positive-count support for an unregularized logarithm, not experimental quality or detector coverage.

Native raw-count comparison requires equal `I0` and matching observed/expected status. With differing `I0`, require an explicit, frozen `per_source_incident` policy and transmission or logarithm selection; use saved normalized products and retain both photon values. Never scale one count array to resemble the other. Default `observation_policy` to `same_kind`; comparing observed against expected normalized signals requires explicit `observed_vs_expected` selection and separate role labels. Keep noise-induced transmission above one and negative logarithms.

For counts/transmission, report full-detector metrics including zeros. For logarithms, use common support `M=A.valid_mask AND B.valid_mask`; exclude both half-count placeholders. Optionally show a separately named common-support summary for other products. Report total, A-valid, B-valid, common-valid, A-only, B-only and neither-valid counts/fractions globally and per view. This support is distinct from specimen-envelope truncation warnings. Unsupported differences display null/masked values, never synthetic zeros. Reject nonfinite data even outside common support.

Stream view blocks with float64 reductions. Define differences as B−A; report bias, MAE, RMSE, maximum absolute difference with `(view,v,u)`, angle/local coordinates and relative L2. Empty support or zero reference norm returns null with its reason and denominator count. Freeze formulas, policies, selected cursor, masks, source manifests/hashes and all relevant setting differences.

## Inspection, resources and acceptance

Link view, detector row and pixel across A/B/difference planes, row profiles and sinograms. Use shared source scales and zero-centered difference scales, raw tooltips and visible invalid masks. Display scaling cannot change metrics. Saved JSON/CSV reports remain readable without sources; new views require unchanged sources. Export source Zarr archives separately. Bound decompression, reduction, report size and JSON expansion before allocations using the v0.9 comparison safeguards; share the processing lock. Do not retain two full projection stacks.

Acceptance requires independent stored-array tests for identity, offsets, scaling, signed logarithms, zero-reference and empty-support cases; mixed masks must prove exact denominators and exclusion of placeholders. Add one-ULP coordinate/pose rejection, checksum/nonfinite failures, bounded reads, source-byte preservation and source-independent export tests. Independently calculated slab Beer–Lambert values test acquisition relationships without using the projector as its own oracle.

Test exact same-seed replay/resume separately from Poisson statistics over independent seeds. The per-view seed scheme is frozen; equal seeds across changed intensities do not guarantee independent noise or cancel it. Statistical tolerances must follow sample counts and expected variance, not one favorable realization. A noisy-versus-expected residual is not a detection rate.

Deliver native-browser recipe round-trip, reviewed two-case photon or blur sweep, cancel/restart/resume, synchronized comparison and downloads. Preserve the six-site H100/HBM demonstration and declared geometry assumptions. Saved X-ray always uses the full voxelized specimen; detector cropping does not refine material geometry, and the SAM ROI does not crop it. No resolved-TSV claim follows from a denser detector. GPU acceleration, cone beams, new wave/material physics, registration and reconstruction comparisons remain separate later work.
