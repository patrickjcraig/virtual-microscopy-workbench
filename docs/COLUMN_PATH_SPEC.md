# Next increment: continuous normal-incidence material columns

**Status: first implementation delivered in version 0.8.** This original design was prepared against v0.7; its proposed criteria and rationale remain below. See [COLUMN_PATHS.md](COLUMN_PATHS.md) for implemented behavior and [VERIFICATION.md](VERIFICATION.md) for executed checks. This numerical alternative is not an experimental accuracy claim.

## Deliverable and reason for the change

Add continuous intersections of the existing ordered box, vertical-cylinder and sphere primitives with each sampled vertical XY column. Use their actual layer lengths and interface depths for saved SAM RF acquisition and for the normal-incidence X-ray/SAM preview. Keep the stage raster, lateral Gaussian responses, pulse model, material presets, complete specimen depth and saved signal formats.

The v0.7 [microstructure experiment](../tools/verify_microstructure_sam.py) exposed substantial RF phase sensitivity to sampled Z boundaries. In that particular assumed H100 fixture, intact RF at 256 and 512 depth samples differed from the 1,024-depth comparison by approximately 183% and 82% relative L2. The 1,024-depth result was not continuum truth. The proposed change removes voxel-center quantization of vertical interface positions for supported primitives; it does not eliminate lateral sampling, finite RF sampling, material uncertainty or omitted wave physics.

The bounded demonstration is the existing HBM 6 patch, with intact and epoxy-filled missing-bump acquisitions, plus independent analytical slabs and curved-column fixtures. All six HBM sites and existing global defects remain in the twin. Tilted projections, saved full-angle X-ray acquisition, CT, refraction, multiple scattering and full-wave acoustics are outside this loop.

## Explicit solver selection and public contract

Add a strict `path_model` field to preview `Settings` in `schemas.py` and saved `SamVolumeSettings` in `volume_schemas.py`:

| Value | Behavior |
| --- | --- |
| `voxel_centers_v1` | Default for new requests and the interpretation of older requests that omit the field. Preserve current voxel-center material paths. |
| `continuous_columns_v1` | Explicit opt-in to continuous normal-incidence intersections with the authored primitives. |

Reject unknown model values. Preview selection of `continuous_columns_v1` requires `angle_deg == 0`, even when no ROI is selected; do not silently reset a nonzero angle. Do not add this choice to `XrayVolumeSettings` or route saved full-angle projections through it.

Preserve `depth_samples` in the saved request for schema continuity, but declare it **inactive for continuous-column acquisition**. It must not change continuous path geometry, echoes, canonical tiles, resource admission or arrays. The UI disables that acquisition control in this mode. A separate voxel-based material-section view may still use a Z raster and must label that distinction. Changing an inactive request field still creates a distinct frozen request identity; do not rewrite user requests to make hashes agree.

Retain saved kind `sam_rf_volume`, float32 `rf` and `envelope` with axes `[y,x,time]`, float64 `x_mm`, `y_mm`, `time_us`, and existing gate processing and time-to-depth mapping APIs. A continuous forward path does not make the derived depth mapper spatially exact: it continues to require its own declared velocity model.

Use a separate version identifier such as `sam-continuous-columns-0.8.0` and a shared path contract identifier `ordered-column-paths-1`. New normalized requests, estimates, processing metadata and frozen solver identity must all expose the chosen path model. Default-mode regression tests must remain in place; do not silently turn the new method on in previously authored presets.

## Ordered geometric semantics

Every evaluation column has global coordinates `(x,y)` at the existing raster center. Its domain is the entire specimen interval `0 <= z <= size_z_mm`; `z=0` remains the existing specimen top. A label of zero means ambient, interpreted as immersion water by SAM and the current open-beam reference by X-ray. Explicit `material='air'` retains its separate label.

Filter `role='defect'` primitives only when `include_defects=false`. For all remaining objects, **the last object in `Twin.objects` wins wherever its interior covers a positive-length interval**. Object IDs are provenance, never an alternate precedence key. HBM feature and local-defect order comes from the frozen compiled primitive list. Electrical functional-state metadata has no effect on occupancy.

For center `(cx,cy,cz)` and extents `(sx,sy,sz)`, intersect as follows, then clip to `[0,size_z_mm]`:

| Primitive | Column intersection |
| --- | --- |
| Box | If `abs(x-cx) <= sx/2` and `abs(y-cy) <= sy/2`, interval `[cz-sz/2,cz+sz/2]`; otherwise empty. |
| Vertical cylinder | Evaluate the same normalized radial predicate as current voxelization: `((x-cx)/(sx/2))^2 + ((y-cy)/(sy/2))^2 <= 1`. If true, interval `[cz-sz/2,cz+sz/2]`; otherwise empty. |
| Sphere | Let `q=1-((x-cx)/(sx/2))^2-((y-cy)/(sy/2))^2`. If `q>0`, interval `cz +/- (sz/2)*sqrt(q)`; otherwise empty. This normalized formula preserves the current extent semantics, including permitted floating-point differences between nominally equal diameters. |

A sphere tangent has zero path length and generates no material segment or echo. A column exactly on a box side or cylinder wall follows the inclusive lateral predicate above; its possible positive-length interval is intentional. This is a single sampled ray convention, not detector area integration. Do not expand radial support using a user-invisible geometric epsilon.

Clip intersections before constructing events. Keep only positive-length intervals. Insert their starts and ends plus specimen endpoints. At a coincident boundary, remove ending intervals and add starting intervals as one grouped update, then select the highest active object index for the open interval to its right. Never emit a zero-length intermediate material or an echo for event ordering alone. Start outside the specimen in ambient and return to ambient after its bottom boundary. Merge adjacent segments with the same **material label**, regardless of their object IDs.

Floating-point coincidence requires a versioned rule. Proposed v1 rule: use float64 endpoints and a fixed `tau_z = 32 * eps_float64 * max(1 mm, size_z_mm)`. Within each column, sort endpoints and group values at most `tau_z` above the group's first endpoint; anchor each group to that first endpoint, with `0` and `size_z_mm` preserved exactly. Do not allow transitive grouping to extend a group beyond `tau_z`. Record the tolerance, count of adjusted endpoints and maximum adjustment. Reject a positive primitive intersection no longer than `2*tau_z` with its object/column locator; do not silently erase a representationally ambiguous thin feature. Tangencies remain empty. This rule may merge only separations at the declared floating-point scale, not micron-scale gaps, and must be tested independently before adoption.

## Shared, bounded path representation

Implement one proposed `column_paths.py` module for both instruments. It consumes a validated, frozen primitive list, the full specimen bounds, global column coordinates and defect inclusion; it does not read Three.js meshes or material-section raster labels.

For one bounded row block, return a compact row-major representation:

```text
column_offsets: uint64[number_of_columns + 1]
z_end_mm:       float64[number_of_segments]
material_label:uint8[number_of_segments]
```

For each column, the first segment starts at zero, subsequent segments start at the previous endpoint, and the last endpoint is `size_z_mm`. Endpoints strictly increase and all lengths are positive. Every column, including an all-ambient column, partitions the full depth. The flattened order is Y then X; no object-order-dependent iteration of dictionaries or sets is allowed. Keep optional diagnostic winner IDs outside the minimum numerical representation and include their memory if enabled.

Use sorted boundary events and an active-object priority structure, with deterministic tie handling and coalescing. Avoid a `columns × objects × boundaries` array or Python object per global segment. It is acceptable to process columns sequentially inside one tile and to regenerate shared halo paths for successive canonical tiles. The shared module reduces duplicate geometry logic; it does not require retaining a complete specimen's paths or echoes in memory.

Proposed seams are `estimate_column_paths(...)`, `iter_column_tiles(...)`, `column_xray_integrals(...)`, and `column_acoustic_echoes(...)`. Keep dispatch at the existing preview/SAM preparation boundary, rather than adding path-specific conditions throughout storage and viewers.

## Forward integration and acquisition context

For normal-incidence X-ray, compute each ray's optical depth as `sum(mu_label_per_mm * length_mm)` in float64 and apply `exp(-optical_depth)`. Ambient has the current zero open-beam-referenced attenuation; explicit air uses its existing material coefficient. Retain the same intensity-domain detector blur and seeded Poisson sampling. Do not sum overlapping object contributions independently: use only the final ordered material partition, or overlap attenuation will be counted twice.

For SAM, traverse the same partition in ascending Z. Begin with ambient impedance, zero specimen-path travel time and unit path factor. At each material transition:

1. Compute signed `r=(Z_next-Z_previous)/(Z_next+Z_previous)` and evaluate the existing focus factor at the **continuous boundary depth**.
2. Emit the primary echo with accumulated two-way time and upstream pressure factor, applying the current pre-standoff `1e-8` echo floor. A discarded echo does not discard subsequent transmission or propagation.
3. Multiply the path factor by `1-r^2` for passage through that interface in both directions.
4. For the following segment of length `d`, advance time by `2*d/(sound_speed_m_s/1000)` microseconds and multiply amplitude by `10^(-2*alpha_db_per_mm*d/20)` at the selected center frequency.

Process the final material-to-ambient transition at the specimen bottom. Adjacent identical labels have no material transition. Use existing material values and loss exponents without fitting them to the image or the old voxel result. Keep external water standoff as the current additional round-trip delay and loss, with focus still measured from specimen top.

Retain the current complex Gaussian pulse, fractional-delay phase handling, RF sampling validation, four-sigma pulse support, coherent lateral filtering, signed real RF and independently stored analytic envelope. Never align traces by shifting peaks or use `abs(RF)` as a replacement envelope. Saved acquisition keeps its explicit record start/duration; changing its temporal crop does not alter retained samples except established numerical tolerance.

Preserve all lateral context: preview uses the larger of the acoustic and X-ray Gaussian halos, while saved SAM uses its complete acoustic halo. Global coordinate centers, ROI placement, specimen-boundary clipping and the current nearest-edge extension stay explicit. Cropping changes returned XY extent, not upstream/downstream material or propagation origin. A normal-incidence column outside the visible rectangle but inside the halo must contribute exactly as in an aligned larger-domain reference.

Preview also preserves the current complete A/B time record. If its end requires discovering the latest retained echo, perform a bounded, non-overlapping path prepass to obtain that scalar and any needed 2D X-ray integrals; do not allocate a global echo list. Count this prepass and subsequent halo path regeneration in preflight. Keep v0.7 visible-row tiling with the full temporal record when it fits. If the spatial halo plus full temporal workspace cannot satisfy the new path estimate, reject with a useful larger-ROI suggestion; do not shorten the trace. Gate-only or temporal-block synthesis would need separate equivalence tests and is not required by this loop.

## Resource preflight and rejection

Retain 600 primitives, current raster/ROI limits, 16,384 saved RF samples, 512 MiB saved output, 512 MiB estimated numerical workspace, 180 million saved RF work cells, disk-space checks and canonical save boundaries. Preview retains its 8-million RF work-cell limit per tile and 180-million aggregate limit; continuous path buffers must additionally fit the 512 MiB numerical workspace estimate. Reject before path/RF allocations exceed these limits; never change the request automatically.

Continuous mode has no voxel-label cube. Keep the existing 64-million-cell limit on every voxel-mode acquisition or material-grid allocation; do not fabricate a continuous `grid_shape` or use inactive `depth_samples` to admit/reject a path calculation. Add an explicit maximum of **500,000 padded XY columns** for continuous mode, plus the path-specific bounds below. Report which limit failed and useful reductions.

For each padded column, let `N_c` be the number of included primitive XY bounding boxes that cover its sampled XY center and have positive continuous Z overlap with the specimen. Define `B_c=2*N_c` and `S_c=B_c+1`. These conservatively bound primitive endpoints and possible material segments. **Do not reuse the voxel bound's `min(depth_samples+1, ...)` cap or its sampled-Z occupancy test**: a positive continuous layer may have no voxel center inside it. Curved shapes deliberately use bounding-box upper bounds at this stage.

Sum bounds over every actual canonical tile including its complete halo, and over any prepass. Proposed path-work limits are 50 million primitive-column candidate tests and 250 million event-work units per request. For a straightforward column implementation, conservatively count `objects_included * padded_columns_processed` candidate tests and `sum(B_c * (1 + ceil(log2(max(2,B_c)))))` event units, including repeated halo work. These are admission counters, not promised CPU timings; publish their definitions and tune only through an explicit versioned change with measurements.

Budget typed-array storage and working copies explicitly: column offsets, bounding-box map, coordinate vectors, event positions/indices/activity, sorted-event scratch space, final path arrays, material integration state, echo arrays, complex FFT/filter buffers and returned float32 signals. Start with at least 32 bytes per possible path segment, 64 bytes per possible event and the existing 160-byte allowance per possible echo, plus the current FFT-workspace allowance and 16 MiB reserve. Validate that the chosen implementation fits these allowances; increase estimates rather than undercounting Python containers. Record assumptions beside reported bytes. Estimate from the largest actual tile, not a nominal ROI-only tile.

Freeze deterministic saved tile rows selected from `8,4,2,1` by the estimate. Include full halo work even if amplitudes later vanish or the record excludes echoes. Prefer transient per-tile paths, with no new persistent cache. If a cache becomes necessary, it requires a separate app-owned marked-directory convention, explicit temporary disk bytes, checksums, cleanup and cancellation tests before use.

Expose `path_model`, contract/model versions, padded XY shape and origin, returned extent, XY pitch, `depth_samples_used=false`, candidate/event/segment bounds, maximum tile interfaces, canonical tile rows, output bytes and peak/work estimates. In continuous mode, feature warnings describe lateral diameter sampling and RF bandwidth/time sampling; do not display a fictitious Z voxel pitch or imply that continuous Z solves lateral under-sampling. Report actual path counts and endpoint adjustments after preparation as diagnostics, without making admission depend on favorable realized counts.

Start the saved comparison with the existing 64 by 64, 0.12 by 0.18 mm, 100 MHz/800 MHz, explicitly 0.2–0.7 microsecond record. Continuous-path resource acceptance remains to be established. For the preview, also estimate an explicitly selected **0.15 by 0.25 mm ROI at 64 by 64** as a practical fallback if the smaller ROI's halo is too expensive. Its lateral pitches are 2.34375 and 3.90625 micrometres: a nominal 10 micrometre TSV spans about 4.27 and 2.56 lateral samples. These arithmetic ratios do not establish resolution or prove that the request fits; the actual continuous preflight must decide. Keep the returned extent and any choice of a larger ROI visible, and never make that substitution silently.

## Frozen datasets and resume compatibility

The existing store fingerprints raw requests, material snapshots, numerical packages and solver source files, freezes the estimate, verifies committed chunks and rejects a changed re-estimate on resume. Preserve those defenses.

1. New acquisitions freeze explicit `path_model`, the exact compiled primitive sequence, geometry/compiler identity, numerical tolerance policy, path contract/model version, material snapshot and relevant source hashes. Include `column_paths.py` and every integration module actually used in continuous solver identity. Model-specific identity must not omit shared code that changes numerical results.
2. Completed historical datasets continue to view, gate, export and serve as depth-mapping inputs from their saved arrays and frozen metadata. Do not recompile their HBM geometry or reparse/normalize their request merely to display/export them. Missing `path_model` may be described as legacy voxel paths in a read-only response, never inserted into the stored manifest.
3. Never resume partial voxel data with continuous paths, change its solver tag, replace its estimate/tile layout, or merge chunks from two modes. Verify the original bytes/hashes before applying current schema defaults. Same-environment resume of a new continuous job must regenerate missing canonical tiles byte-identically while preserving committed good chunks.
4. An older partial job whose source/package identity no longer matches remains preserved and explicitly incompatible. It may resume only under its original compatible implementation, or the user may start a separate new acquisition. This loop need not ship historical runtimes; it must not pretend a model label alone restores compatibility. Do not delete or silently migrate partial data.
5. Cancellation during path preparation or between tiles leaves a valid partial dataset and releases all transient state. Worker restart must not convert an incomplete path pass into committed signal data. Completed raw-source and derived-depth integrity remains independent of whether current geometry code can compile the historical request.

Keep the stored RF format unchanged. Add tests that export old completed SAM, X-ray, reconstruction and depth fixtures after the new code is installed, and tests that mismatched partial jobs fail safely with unchanged array bytes. Numerical byte identity is required for resumed runs in the tested frozen environment; do not claim untested cross-platform bit identity.

## Independent acceptance tests and delivery sequence

Implement the following acceptance suite before enabling the option in the UI:

| Test family | Required evidence |
| --- | --- |
| Single primitives | Analytical box/cylinder lengths and sphere chords at asymmetric interior, exterior and tangent columns; global translation and specimen clipping. Use independently written formulas, not the new path helper as its own oracle. |
| Ordered overlap | Explicit hand-calculated partitions for nested, partially overlapping and coincident primitives of different materials; later global defects; exclusions; same-material coalescing; no zero-length echo; deterministic near-coincidence behavior and rejection of ambiguous tiny intersections. |
| Layered SAM | Independently calculated signed front/back reflection, exact two-way times, reciprocal transmission factors, focus at true depths and dB/mm losses. Include a non-grid-aligned 15 micrometre layer between two known media and an explicit sealed air gap. |
| Time/phase | Shift a planar interface continuously by a known non-voxel-aligned distance and verify the analytical delay/phase trend; vary inactive `depth_samples` and require identical continuous arrays. Compare different valid record windows over their shared samples, including pulse centers just outside both boundaries. |
| X-ray | No-noise/zero-blur Beer–Lambert results from independently calculated ordered material lengths. Explicitly distinguish ambient and air; prevent double-counted overlap. Retain blur-before-Poisson order and seed reproducibility. |
| ROI and halo | A translated small ROI matches an aligned larger-domain calculation in retained X/Y, with absorbing material above/below the patch and a feature only in the PSF halo. Verify no resetting of time zero or omission of outside columns. |
| Bounds and lifecycle | Random bounded primitive scenes satisfy actual events/segments/echoes <= their conservative per-column and tiled bounds. Over-budget inputs fail before allocations. Test cancellation, missing/corrupt chunk repair, exact resume, mode mismatch, old completed read/export and source-independent depth derivation. |
| HBM experiment | Repeat intact/missing-bump HBM 6 acquisitions with matched XY/frequency/record settings. Save full RF/envelope, inputs and metrics for both path modes. Report continuous-mode Z-setting invariance, residual XY and RF-rate sensitivity, elapsed time and independently observed process peaks. A nonzero defect effect is not an experimental detection criterion. |

Use concrete initial numerical tolerances on small, well-conditioned analytical fixtures, with exact tangent/coincidence tests handled by their declared classification rules:

| Quantity | Initial acceptance criterion |
| --- | --- |
| Ordered path endpoints and total material lengths | Absolute error <= `1e-10 mm + 1e-10 * expected_length_mm`; total column length agrees with specimen depth within `1e-10 mm`. Every returned segment remains strictly positive. |
| Pre-pulse interface arrival time | Absolute error <= `1e-10 us + 1e-10 * expected_time_us` against independently summed `2*d/c`, including a known water standoff. This checks geometry/integration before RF sampling. |
| Pre-pulse signed reflection/transmission/loss amplitude | Absolute error <= `1e-12 + 1e-10 * abs(expected_amplitude)` for the same declared scalar material/focus equations. Check signs separately. |
| Float32 RF/envelope against independent discrete pulse evaluation | `atol=3e-7`, `rtol=1e-5`, using the defined phase-aware two-bin deposition law. Do not apply this tolerance to a different continuous-pulse formula without accounting for interpolation error. |
| Single isolated sampled pulse peak time | Within one saved RF sample interval of the analytical arrival. This is a sampling check, not a timing-resolution claim. |
| No-noise, zero-blur X-ray transmission | Absolute error <= `1e-12 + 1e-10 * abs(expected_transmission)` before optional float32 conversion, using independently calculated ordered material lengths. |
| Aligned ROI/full-domain and overlapping record-window signals | `atol=3e-7`, `rtol=1e-5` in the same numerical environment, with fixtures away from deliberately ambiguous lateral tangencies. Assert exact coordinate agreement separately. |
| Inactive Z-setting changes and same-environment resume | Exact equality of saved float32 signal arrays and float64 time/XY coordinates; changing inactive Z settings may change request identity but must not change the computation. |
| Resource upper bounds | Exact integer inequality: measured event/segment/echo and processed-cell counts never exceed preflight. No percentage allowance. |

Record residuals and worst-case locators, not only pass/fail. These tolerances are proposed regression thresholds for deterministic equations and floating-point implementation; they are neither experimental accuracy specifications nor an acceptance bound for the entire H100 model. If an independent fixture fails, diagnose the equation, boundary policy or numerical conditioning before revising a threshold. Do not loosen thresholds to make the complex HBM comparison appear converged.

Build order: shared geometry/intersection tests; conservative estimator and typed path blocks; zero-angle X-ray and SAM consumers; explicit dispatch and frozen identity; saved-job lifecycle tests; UI controls and evidence labels; then the reproducible HBM comparison artifact. Keep each step independently testable, and stop admission rather than silently approximating when a case exceeds resource limits.

The UI should present **Voxel-center paths** and **Continuous normal-incidence paths** as numerical choices, show the selected choice on estimates and saved datasets, and mark a change as requiring a new acquisition. For continuous mode, explain: “Vertical interfaces follow the authored primitives. Lateral sampling and the reduced-order acoustic model still limit the result.” Preserve visible assumption labels for HBM dimensions, material proxies and the user's provisional image scale. Sphere intersections provide exact geometric column lengths under this contract; applying the existing normal-incidence scalar reflection law at a curved boundary remains an approximation. No “calibrated depth,” “resolved TSV,” “microstructure CT,” or measured-resolution badge follows from selecting this model.
