# Virtual microscopy 0.20 integration contract (compatible twin schema 1)

Local application: Python FastAPI serves a Vite/vanilla JS + Three.js client. Source in `virtual_microscopy/` and `web/`. Physical dimensions use millimetres. Coordinates x right, y down in image, z depth from specimen top; surrounding material is water for SAM and air for X-ray. Later primitives replace earlier ones. This is a reduced-order synthetic forward simulator, not experimentally validated or coupled full-wave multiphysics.

## Twin JSON

`{schema_version:1,name:string,description:string,size_mm:[x,y,z],objects:[...]}`

Object: `{id:string,name:string,shape:'box'|'sphere'|'cylinder',material:'silicon'|'copper'|'solder'|'epoxy'|'fr4'|'air',center_mm:[x,y,z],size_mm:[x,y,z],role:'structure'|'defect'}`. Coordinates span [0,size_mm] and size_mm means full extents; sphere uses size_mm[0] diameter (all extents equal); cylinder axis z with equal x/y diameter. Object bounds must stay inside twin bounds. No custom materials in v1. Model defaults are in `examples/`; reproducible generators are in `tools/`.

Optional primitive `display_label` supplies a short 3D label. Optional twin `reference` is `{product,summary,sources:[{id,title,url}],published_facts:[{label,value,source_ids}],assumptions:[string]}`; sources use valid HTTP(S) URLs, unique IDs, and each fact must cite existing source IDs. Optional `recommended_settings` follows the complete Settings schema below and must keep its probe/focus in bounds. These fields are retained in exported twins; absent optional fields are omitted to preserve legacy shapes. Specimen/probe x/y extents support 100 mm; depth supports 6 mm and primitive count is bounded at 600. Raster limits remain unchanged.

## Layered HBM extension

Optional `hbm_assemblies` contains at most 12 parameter assemblies: `id`, `name`, `center_xy_mm`, `footprint_mm`, `bottom_z_mm`, `die_count` (8 or 12), `die_thickness_um`, `gap_um`, `base_thickness_um`, `cap_thickness_um`, `functional_state` (enabled/disabled/unknown), `physical_present`, and `evidence`. Total height is base + die_count × (die + gap) + cap, in µm. Positive dimensions and specimen bounds are validated.

Compiled primitives use `assembly_id` and `layer_role` (base_die/dram_die/interdie_gap/cap/underfill/contact). Each group's primitives must be contiguous and match the compiler's ordered geometry exactly. This prevents import metadata divergence and edits that accidentally reorder unrelated material. Functional state does not affect material geometry. Arbitrary imported evidence is preserved; the default specimen describes its initial assumptions explicitly.

Optional `image_reference` contains SHA-256, raster width/height, pixel_size_um, scale_status (user_estimate/calibrated), title and source_note. Declared calibration is metadata supplied by the importer, not a server certification. Image bytes and filesystem paths are not carried in exported twins.

Optional assembly `microstructure` adds `model_version:'hbm-explicit-patch-1'`,
`enabled`, signed `center_offset_xy_um`, `columns`, `rows` (1–8), positive
`pitch_x_um`, `pitch_y_um`, `bump_diameter_um`, `tsv_diameter_um`, `evidence`,
`source_note`, and at most four `defects`. Only one enabled patch is allowed in
the twin; total primitives remain capped at 600. Defaults are 2 columns, 3 rows,
50 µm pitches, 25 µm bumps and 10 µm TSVs. Cylinder height comes from its host gap
or die. New compiled layer roles are `microbump` and `tsv`.

Each defect has `id` (letter-led ASCII letters/digits/underscore/hyphen, max 20),
`kind:'missing_bump'|'bump_void'|'tsv_void'`, `row`, `column` (one-based),
`layer_index` (bump gaps 1..D; TSV base 0/dies 1..D), `enabled`, and
`void_diameter_um` for voids. Missing bumps replace solder with epoxy; voids are
contained air spheres. Duplicate enabled targets, orphaned dormant targets,
overlapping nominal cylinders and out-of-footprint patches are rejected. Local
defects follow their assembly; global defects preserve existing coordinates.

## API

### Mixed finite-media instrument (0.20)

`/api/v2/mixed-acoustics` owns standalone `mixed_layered_analysis` reports.
POST `/estimate`, POST/GET `/reports`, GET `/reports/{id}` and GET
`/reports/{id}/export?format=json|csv` supply preflight, atomic publication,
bounded ID-desc history, full historical reads and lossless exports.

The request is `{kind:"mixed_layered_analysis",name,stack,spectrum,causal_pulse}`.
Kind is required. Spectrum may use its documented defaults and causal_pulse may
be null. Stack has explicit real incident/terminal media and zero-to-eight authored
finite layers. Every layer requires a discriminator: `lossless_real` has name,
thickness_mm, impedance_mrayl and sound_speed_m_s; `sls` has name, thickness_mm,
density_kg_m3, relaxed_modulus_gpa, unrelaxed_modulus_gpa and relaxation_time_us.
Other fields reject. Zero-thickness entries remain authored evidence and count
toward eight; exact represented total thickness is at most 6 mm.

Spectrum stores actual frequencies, complex pressure R/T, energy diagnostics and
one typed material record per authored layer. Optional causal_pulse stores actual
times, signed real/quadrature pressure, magnitude and the new reflected-only
`scalar-mixed-reflected-gamma-1` certificate. Its outward total encloses the exact
sum of published alias, cutoff and both output-conversion components, within the
requested tolerance. The five count fields are layer_count, authored_layer_count,
active_layer_count, lossless_real_layer_count and sls_layer_count.

Reports use the separate `mixed-reports` directory, with complete typed requests,
arrays, runtime/proof/implementation provenance and request/stack/report hashes.
Historical reads validate frozen contracts without current kernels or proof-file
lookup. No Twin, assignment, HBM-column, acquisition or worker input is accepted.
See [MIXED_ACOUSTICS.md](docs/MIXED_ACOUSTICS.md) and the
[mixed proof](docs/MIXED_MATERIAL_PROOF.md) for units, bounds and scope.

### Material assignment instrument (0.19)

`/api/v2/material-assignments` owns immutable `sls_material_assignment` documents.
POST `/estimate` validates the full request and reports coverage/resources; POST
the collection publishes a document and GET lists documents. GET `/{id}` and
`/{id}/export` return complete saved JSON. POST `/{id}/columns` with exactly
`{x_mm,y_mm}` publishes one full-depth `sls_material_column`; GET lists those
columns, and GET `/{id}/columns/{column_id}` or its `/export` suffix returns the
complete saved column and deduplicated parent/source closure.

Requests explicitly supply kind, name, Twin, strict include_defects, coverage_scope
(`all_included` or `selected_materials`), required_material_ids and zero-to-six
bindings. Each binding has material_id, name, explanatory note and a discriminated
origin. Manual origins supply density_kg_m3, relaxed_modulus_gpa,
unrelaxed_modulus_gpa and relaxation_time_us. Report-layer origins supply canonical
report_id and zero-based layer_index; the server copies exactly four source values.
Incomplete evidence can be saved, with explicit scope-qualified missing coverage.

Historical exports retain complete compiled twins, source reports, parameter and
geometry identities. Saved-column reads do not run geometry. New inspection
requires the supported frozen geometry identity. All results retain
`propagation_available:false`. See [MATERIAL_ASSIGNMENTS.md](docs/MATERIAL_ASSIGNMENTS.md)
for ambient/air policy, resources, binding scope and physical limitations.

### Shared and legacy routes

- GET /api/health -> {status:'ok',version:'0.20.0'}
- GET /api/examples -> [{id,name,description,twin}]
- GET /api/materials -> list of material dicts with id,name,color,density_g_cm3,sound_speed_m_s,impedance_mrayl and provenance; extra properties permitted.
- POST /api/validate -> twin body -> {valid:true,twin:normalized twin,warnings:[]}; errors HTTP 422.
- POST /api/simulate -> `{twin,settings}`. Settings defaults: `{resolution:128,energy_kev:80,angle_deg:0,photons:50000,noise:true,frequency_mhz:50,gate_start_us:0.42,gate_end_us:0.56,focus_mm:0.5,probe_x_mm:3.1,probe_y_mm:3.1,include_defects:true,seed:42}`. resolution allowed 64,128,192; energy 40..150, angle -45..45 about y, photons 1000..1000000, frequency 10..150, gate_start 0..10, gate_end up to 12 with end>start; focus 0..specimen z; probe within xy. Water standoff excluded from time (t=0 at top plane). Probe coordinates are physical x/y, not detector coordinates when tilted.
- POST /api/probe same body/settings -> `{ascan:...,bscan:...}`; synthesizes a local strip. All runs bounded by dimensions, primitive counts and RF work budget.

Optional acquisition `roi_mm` is `[xmin,ymin,xmax,ymax]` in global millimetres with at least 0.05 mm width/height. ROI and probe must fit the specimen; probes must also fit the ROI. ROI acquisition requires angle_deg=0. Optional `depth_samples` accepts 128/256/512/1024 independently of the lateral raster; omission retains nz=2×resolution. The image extent is `[xmin,xmax,ymin,ymax]`. The sampled grid includes the complete specimen depth and a clipped 4-sigma lateral context for both Gaussian PSFs. Geometry allocation is capped at 64 million cells, in addition to the existing RF work budget. Missing optional settings are omitted from acquisition snapshots.

Preview Settings and saved SamVolumeSettings additionally accept strict
`path_model:'voxel_centers_v1'|'continuous_columns_v1'`, defaulting to voxel
paths. Continuous preview requires angle_deg=0 even without an ROI. The authored
`depth_samples` value remains valid and frozen but is computationally inactive
in continuous mode. No continuous choice is added to saved full-angle X-ray.
Continuous metadata reports `path_model`, `path_contract_version`,
`depth_samples_used:false`, `grid_shape:null`, `voxel_depth_um:null`,
`padded_shape_yx`, global origin/extent, actual XY pitch, candidate/event/RF work,
estimated workspace and path diagnostics. Saved signals/axes retain their
existing formats. The scalar material paths span the complete specimen and
retain surrounding lateral response context; see [COLUMN_PATHS.md](docs/COLUMN_PATHS.md).

Saved-SAM compact catalog entries and detail responses expose the frozen
`path_model`. A missing historical method is interpreted as voxel paths in the
response without inserting a field into the stored request or manifest.

- POST /api/hbm/compose -> `{twin,assembly_id,parameters:partial HBM parameters}` -> `{twin,warnings}`. Invalid or null patch values return 422. The input snapshot is not mutated. Geometry updates preserve unrelated objects and fixed-coordinate defects; updated objects stay before those defects.
- POST /api/hbm/section -> `{twin,assembly_id,axis:'xz'|'yz',resolution:128|256|512,include_defects?:boolean}` -> material `image` labels, `materials` legend, `extent_mm:[u0,u1,z0,z1]`, axis, fixed_coordinate_mm, pixel_pitch_um and warnings. Section sampling includes intersecting package geometry. `mode:'material_geometry'` distinguishes it from microscope/reconstruction output.
- POST /api/hbm/microstructure -> `{twin,assembly_id}` -> `{microstructure,primitive_count,remaining_primitives}`. The same fields accompany compose responses. Summary contains enabled/model_version, nominal_feature_count/defect_count, feature_bounds_mm `[x0,y0,z0,x1,y1,z1]`, roi_mm `[x0,y0,x1,y1]`, canonical features `{id,kind,row,column,layer_index,center_mm,size_mm}` and authored defect identity mappings `{id,kind,target_id,primitive_id,enabled}`. Disabled patches have no active features or ROI. Nested compose patches merge with authored microstructure parameters.
- HBM section additionally accepts `feature_id` for an active nominal bump/TSV, `bounds_mm:[u0,u1,z0,z1]`, or `fixed_coordinate_mm`. Feature and explicit fixed coordinate are mutually exclusive. Bounds lie within the specimen with positive spans. The response includes the selected `feature` or null, plus sampling warnings for included local features/defects, including those falling between sample centers.
- GET /api/reference-image -> the locally installed user image (PNG) or 404. Its identity is returned as `X-Reference-SHA256`. The client compares it to the selected twin before display; the endpoint serves only the fixed local reference and accepts no arbitrary file path.

## Python engine callable

`virtual_microscopy.physics.simulate(twin:dict, settings:dict)->dict` receives fully populated validated settings; `probe(twin,settings)->dict`. `virtual_microscopy.materials.MATERIALS` is dict keyed material id; public values include id. Schemas/API live alongside those modules. Examples and analytical/API tests are independent of the frontend.

## Saved-volume API (v2 routes, dataset schema 1)

`SamVolumeRequest` is `{twin,acquisition}`. The strict acquisition schema has
`scan_nx`, `scan_ny`, `depth_samples`, optional `roi_mm`, `frequency_mhz`,
`fractional_bandwidth`, `focus_mm`, `record_start_us`, `record_duration_us`,
`sample_rate_mhz`, `water_standoff_mm` and `include_defects`. Gate/display fields
are not acquisition settings. Defaults and numerical bounds are documented in
[SAVED_VOLUMES.md](docs/SAVED_VOLUMES.md) and `/openapi.json`.

- POST `/api/v2/estimate` -> shape `[y,x,time]`, RF/envelope/coordinate/total bytes,
  estimated numerical peak workspace, time range/sample count, pitch, grid/PSF
  metadata, warnings and local disk preflight. Invalid/out-of-budget requests fail
  before allocation.
- POST `/api/v2/jobs` -> HTTP 202 job; GET `/api/v2/jobs` -> `{jobs:[...]}`;
  GET `/api/v2/jobs/{id}` -> job. Jobs include ID, dataset ID, status, row progress,
  timestamps and any error.
- POST `/api/v2/jobs/{id}/cancel` or `/resume` -> updated job. Resume keeps immutable
  inputs and checks solver/material identity and committed data. Processing runs
  in a single local child process; status reads and saved-data inspection remain
  available. Preview and volume propagation cannot run concurrently through API.
- GET `/api/v2/datasets` -> `{datasets:[summary,...]}` with names, IDs, shape,
  state, completion flag and input hash. GET `/api/v2/datasets/{id}` -> the full
  manifest, including frozen inputs and the completed-chunk registry. Incomplete
  states explicitly report `complete:false`. Detail responses also include
  acquisition, extent_mm and time_range_us for the viewer.
- GET `/api/v2/datasets/{id}/view` query parameters: x_index, y_index, time_index,
  gate_start_us, gate_end_us, gate_mode (`peak_envelope` or `rms_rf`). Returns
  `xy`, `xt`, `yt`, `ascan`, `cscan`, `cursor`, `gate`, and `metadata` read from
  stored arrays. XY/Cscan extents use `[xmin,xmax,ymin,ymax]`; XT/YT use
  `[position_min,position_max,time_min,time_max]`. Sections report exact temporal
  bin edges; all trace samples are returned. Indices/gates outside recorded bounds
  or gates with no samples fail with 422. Incomplete datasets return 409.
- GET `/api/v2/datasets/{id}/export` -> integrity-checked ZIP containing
  `manifest.json` and `data.zarr/`. Full float32 RF/envelope, float64 coordinates;
  authoritative axes `[y,x,time]`. Incomplete datasets return 409.

Catalog/data root defaults to ignored `artifacts/volumes`, configurable through
`VM_DATA_ROOT`. UUID-only identifiers cannot select arbitrary filesystem paths.
No API supports replacing completed datasets. `time` is not a depth coordinate;
relative modeled amplitudes are not calibrated Pa or volts.

### X-ray projection-volume extension (0.4)

The same estimate/job routes accept `XrayVolumeRequest`:
`{kind:"xray_projection_volume",twin,acquisition}`. Its independent geometry,
detector, source and angular fields are defined in `xray_schemas.py`,
[XRAY_VOLUMES.md](docs/XRAY_VOLUMES.md) and `/openapi.json`. The legacy untagged
SAM request remains accepted. Unknown kinds or cross-instrument settings fail
validation. Projection acquisition does not use the preview's ±45° restriction.

X-ray estimates report `[view,v,u]`, four signal-array sizes, coordinate/pose
bytes, material and detector pitches, actual angle range, excluded stop angle,
PSF halo, truncation warnings and bounded numerical work. Jobs of either kind
share one local process queue. Job responses retain the row aliases and add
`kind`, `completed_units`, `total_units` and `progress_unit` (`views` or `rows`).
Existing catalog rows migrate with the SAM kind; existing dataset bytes and
source identity inputs remain unchanged.

X-ray dataset details include `detector_extent_mm:[u0,u1,v0,v1]` in local
coordinates and `angles_range_deg:[first,last_actual_angle]`. They have no
acoustic time range. GET `/api/v2/datasets/{id}/xray-view` accepts `view_index`,
`detector_row` and `product` (`counts`, `transmission`, `line_integrals`). It returns:

- `projection`: saved `[v,u]` image, local `extent_mm`, unit, extrema and invalid mask.
- `sinogram`: saved `[view,u]` image at one detector row, physical `extent`, actual
  `angles_deg`, explicit `angle_bin_edges_deg`, unit and invalid mask.
- `profile`: local `u_mm`, saved values, unit and invalid mask for the chosen row/view.
- `cursor`: view index, actual angle, detector row and local v coordinate.
- `pose`: stored unit ray direction, virtual detector center and unit U/V basis.
- `metadata`: axes, processing definitions, assumptions and sampling warnings.

Both view endpoints return 409 for incomplete data and 422 for the wrong dataset
kind or invalid indices/settings. Viewing reads saved arrays without acquiring
new projections. Sinogram angular-bin edges are display support, not extra views.
Masks describe undefined zero-count logarithms; zero raw counts/transmission are
valid observations and must not be presented as missing measurements.

The common export route dispatches integrity verification by dataset kind and
returns `xray-projections-{id}.zip` or `sam-volume-{id}.zip`. X-ray archives contain
four float32 `[view,v,u]` arrays (`counts`, `transmission`, `line_integrals`,
`valid_mask`) and seven float64 coordinate/pose arrays. Offsets occur once in
`detector_center_mm`; `u_mm` and `v_mm` stay local and centered. Unit U/V vectors
are not pixel-pitch-scaled ASTRA vectors. Counts and every coordinate, pose and
committed signal chunk are verified before export. Angular stacks are inputs to
reconstruction; their view axis is not a spatial z axis.

### Spatial X-ray reconstruction extension (0.5)

POST `/api/v2/estimate` and POST `/api/v2/jobs` also accept:

```json
{
  "kind": "xray_reconstruction",
  "source_dataset_id": "<completed X-ray dataset UUID>",
  "reconstruction": {
    "nx": 96, "ny": 64, "nz": 64,
    "filter": "hann", "frequency_cutoff": 1,
    "invalid_policy": "interpolate", "truncation_policy": "reject"
  }
}
```

No twin accompanies this request. The source must be a complete, integrity-checked
`xray_projection_volume` with at least 16 uniformly spaced endpoint-excluded views
over exactly 180° or 360° and canonical parallel rotation about Y. The geometry
validator checks stored coordinates and poses. Limited-angle or nonuniform data
are not accepted by this baseline.

Each output count accepts 16–256. Optional `bounds_mm` is
`[xmin,xmax,ymin,ymax,zmin,zmax]` within the source specimen; omission uses its full
envelope. Filters are `hann` and `ram_lak`; cutoff accepts 0.1–1 of detector-U
Nyquist. Invalid-log policy is `interpolate` or `reject`; truncation policy is
`reject` or explicit `allow`. Source files are immutable. The estimate includes
output bytes, temporary filtering-cache bytes, workspace, work, physical pitches
and warnings. Shared preflight caps and disk checks apply.

The existing job endpoints expose `kind:'xray_reconstruction'`,
`progress_unit:'slices'`, and committed/total Z planes. Cancellation and resume
keep the same dataset and verify the frozen source/solver identities.

GET `/api/v2/datasets/{id}/reconstruction-view` accepts optional zero-based
`x_index`, `y_index`, `z_index`. It returns `xy`, `xz`, `yz` with `image`,
`coverage`, `invalid_mask`, `extent_mm`, axis labels and `unit:'mm^-1'`; a common
`cursor` with indices, global millimetres, attenuation and coverage; and `profiles`
along x/y/z. Metadata retains `[z,y,x]` shape, bounds, pitch, source, processing,
warnings and evidence status. Images are respectively `[y,x]`, `[z,x]`, `[z,y]`.
Invalid indices/wrong kind return 422; incomplete datasets return 409. Reading
slices or changing browser display limits does not create jobs or change arrays.

Saved Zarr products are float32 `attenuation` and `coverage` in `[z,y,x]`, with
float64 global-center `x_mm`, `y_mm`, `z_mm`. Negative supported attenuation is
retained. Coverage is the fraction of views with geometric detector support,
not a confidence score. At coverage below `1 - 1e-6`, attenuation is a masked
finite zero placeholder. Even full coverage does not correct truncation bias.

The manifest embeds the complete source-manifest snapshot and its SHA-256, used
in the derived input identity, plus algorithm and numerical-package identity,
processing settings and per-plane checksums. Completed reconstructions can be
read/exported without the original source directory. GET `/export` produces
`ct-reconstruction-{id}.zip` containing the derived arrays and manifest, without
duplicating original projections. See [Reconstruction](docs/RECONSTRUCTION.md).


### SAM spatial depth extension (0.6)

POST `/api/v2/estimate` and `/api/v2/jobs` accept a fourth kind:

```json
{
  "kind": "sam_depth_volume",
  "source_dataset_id": "<completed saved SAM UUID>",
  "mapping": {
    "nz": 128, "z_min_mm": 0, "z_max_mm": 2.65,
    "surface_reference": "source_water_delay",
    "velocity_model": "homogeneous", "sound_speed_m_s": 5000,
    "layers": [], "model_evidence": "user_assumed",
    "model_note": "Illustrative assumed velocity; not calibrated H100 depth."
  }
}
```

The source must be complete raw-time SAM. It is integrity-checked before mapping;
no preview twin accompanies the request. X/Y shape and coordinates remain
identical to the source. Depth bounds lie within the source specimen; `nz` accepts
16–1024. `velocity_model:'layered'` requires up to 128 ordered
`{end_depth_mm,sound_speed_m_s}` layers with positive speeds and increasing
endpoints, starting implicitly at zero. The layer model need not cover the full
requested output range; uncovered depths are masked. A homogeneous model requires
an empty layer list. `surface_reference:'explicit'` requires nonnegative
`surface_time_us` on the saved recording axis; omit it for `source_water_delay`.
Evidence is `user_assumed`, `user_calibrated`, or `synthetic_truth`, with a note.

The estimate includes shape `[nz,source_ny,source_nx]`, X/Y/Z bounds and pitches,
output/cache/workspace bytes, support warnings and resolved surface time. Shared
jobs report slice progress and preserve source/processing identities on resume.

GET `/api/v2/datasets/{id}/depth-view` accepts optional `x_index`, `y_index`,
`z_index` and `product:'rf'|'envelope'` (default `envelope`). It returns `xy`,
`xz`, `yz` images in `[y,x]`, `[z,x]`, `[z,y]`, boolean `valid_mask` and
`invalid_mask`, physical extents, original amplitude units, and a shared cursor.
The cursor includes indices, global x/y/z, both `rf` and `envelope`, the selected
`value`, `valid`, `model_valid`, and `sample_time_us` (null outside model support).
Profiles retain spatial coordinates, values and validity. Metadata retains the
declared mapping, source, model evidence, surface time, mapped-time vector,
model-support vector and warnings. Wrong kind/indices or corrupted arrays return
422; incomplete datasets return 409.

Saved `rf`, `envelope`, `valid_mask` are float32 `[z,y,x]`, with one Z plane per
chunk. `x_mm`, `y_mm`, `z_mm`, `travel_time_us` are float64 vectors, the latter
indexed by Z. Unsupported amplitudes are finite zero placeholders. Outside the
velocity model, time is also a zero placeholder distinguished by the frozen
`metadata.model_depth_valid` vector; a defined time outside the recording remains
available even though its amplitude is invalid. The mask is binary numerical
support, not a confidence or experimental-validity score.

The derived manifest embeds the complete raw-source manifest/hash and immutable
mapping metadata with its checksum. Completed derived data can be inspected and
exported independently of source-directory availability. `/export` returns
`sam-depth-{id}.zip`, without duplicating raw-time arrays. See
[SAM_DEPTH.md](docs/SAM_DEPTH.md) for the scientific interpretation.

## SAM recipes, batches and comparisons (0.9)

The original saved dataset schema and forward solver identities are unchanged.
New strict models are in `recipe_schemas.py` and `comparison_schemas.py`.

- `POST /api/v2/recipes`: `{name,request:SamVolumeRequest,parent_recipe_id?,default_gate?:{start_us,end_us}}` → immutable recipe record (201).
- `GET /api/v2/recipes`: `{recipes:[summaries]}`; `GET /recipes/{id}` → exact record; `GET /recipes/{id}/export` → JSON download.
- `POST /api/v2/recipes/import`: complete original exported JSON → preserved record (201); duplicate identical ID is idempotent, conflicting ID/content fails. Preserve the original JSON text through browser import, including floating-point spelling.
- `POST /api/v2/recipes/from-dataset`: `{dataset_id,name,default_gate?}` → original completed SAM request and frozen source provenance, without current geometry construction.
- `POST /api/v2/cases/preview`: `{recipe_id,field,values,assembly_id?,defect_id?}` → recipe, expanded cases, differences relative to case 1, per-case estimates and aggregate disk/workspace admission. Fields: `frequency_mhz`, `focus_mm`, `fractional_bandwidth`, `path_model`, active `depth_samples`, `defect`, or global `include_defects`. Two to four unique values; isolated/global defect pairs use exactly boolean false/true. Isolated pairs require both IDs and active defect participation.
- `POST /api/v2/batches`: proposal plus `idempotency_key` → transactional batch (202). `GET /batches` → `{batches:[...]}`; `GET /batches/{id}` → frozen plan plus case/job states; `POST /batches/{id}/cancel` and `/resume` preserve completed cases. Job responses identify `batch_id` and `case_index`; use batch controls for those jobs.
- `POST /api/v2/comparisons`: `{reference_dataset_id,candidate_dataset_id,gate_start_us,gate_end_us,x_index?,y_index?}` → immutable report (201). `GET /comparisons` → `{comparisons:[summaries]}`; `GET /comparisons/{id}` and `/export?format=json|csv` need no sources. `/view?x_index=&y_index=&time_index=` verifies sources and returns synchronized traces/maps. New gate → new report; display changes never alter stored metrics.

Recipe identity includes schema/kind, UUID, parent/name/time, full request, default
gate, optional complete source provenance, request hash and record hash. Historical
reads preserve exact JSON. Batch publication stages every case manifest before
one SQLite transaction exposes jobs. Invalid cases publish no jobs; duplicate
keys replay frozen results before current numerical admission. All limits and
original geometry changes remain explicit.

Comparisons require complete SAM arrays, exact shape/X/Y/time coordinates,
declared units and time reference. The 422 response for incompatible sources
contains `{message,issues}` with axis/count/max-difference details. RF/envelope
statistics and gated peak/RMS maps use float64 B−A reductions. Relative L2 is null
with a reason for a zero reference norm. Reports retain formulas, exact coordinates,
source manifests/hashes and processing identity. Source arrays are read-only.
See [ACQUISITION_COMPARISONS.md](docs/ACQUISITION_COMPARISONS.md) for precise metrics,
resource estimates and the distinction between numerical contrast and accuracy.

## X-ray recipes and comparisons (0.10)

The generic recipe endpoints also accept `request:{kind:'xray_projection_volume',twin,acquisition}` and store `kind:'xray_acquisition_recipe'`. An omitted request kind retains the existing SAM interpretation. X-ray recipes cannot have acoustic gates. Recipe lists include instrument kind; frozen historical SAM record/plan JSON and hashes are unchanged. A parent revision must retain its acquisition kind. X-ray exports use an `xray-recipe-` filename.

X-ray case fields are `energy_kev`, `photons`, `detector_fwhm_mm`, `geometry_nx`, `geometry_ny`, `geometry_nz`, `noise`, `seed`, `include_defects`, and the existing isolated `defect` selector. Each batch contains a single instrument kind. Computed batch responses expose `kind`; X-ray progress units are views. Resource, staging, idempotency, cancellation and resume contracts remain shared with SAM.

- `POST /api/v2/xray-comparisons`: `{reference_dataset_id,candidate_dataset_id,product:'counts'|'transmission'|'line_integrals',normalization:'native'|'per_source_incident',observation_policy:'same_kind'|'observed_vs_expected',view_index?,detector_row?,detector_col?}` creates a frozen report (201). Defaults are transmission/native/same_kind.
- `GET /api/v2/xray-comparisons` returns summaries. `GET /api/v2/xray-comparisons/{id}` and `/export?format=json|csv` return frozen data without requiring source datasets.
- `/view?view_index=&detector_row=&detector_col=` verifies unchanged sources and returns linked projection/profile/sinogram products, masks, shared scales, pose and support. New display coordinates do not change frozen metrics.

Require exact shape, angles, u/v and all four float64 pose arrays, frozen semantic definitions and units. Incompatibility returns 422 with `{message,issues}`. Native requires equal source photons; counts additionally require the same observed/expected kind. Normalized observed-versus-expected comparisons require the explicit observation policy. Counts/transmission metrics include zero counts; log metrics use only common positive-count support. Unsupported log displays are null. Negative logs and transmission above one remain quantitative values. See [XRAY_COMPARISONS.md](docs/XRAY_COMPARISONS.md) for formulas, limitations and provenance.

## Simulation response (plain JSON numeric arrays)

```
{
  xray:{image:[[float]],unit:'I / I0',extent_mm:[0,x,0,y],min:float,max:float,mean_transmission:float},
  sam:{image:[[float]],unit:'relative echo amplitude',extent_mm:[0,x,0,y],min:float,max:float,peak_amplitude:float},
  ascan:{time_us:[float],amplitude:[float],envelope:[float],probe_mm:[x,y]},
  bscan:{image:[[float]],extent:[0,x,0,time_max_us],unit:'relative echo amplitude',y_mm:float},
  metadata:{runtime_ms:float,grid_shape:[ny,nx,nz],grid_origin_mm:[x,y,0],acquisition_shape:[n,n],roi_mm:null|[xmin,ymin,xmax,ymax],pixel_pitch_um:[dx*1000,dy*1000],voxel_depth_um:float,seed:42,model_version:'0.7.0',rf_tile_rows:int,rf_max_tile_work_cells:int,rf_work_cells:int,warnings:[string],assumptions:[string]}
}
```

Rows of xray/SAM are y; columns x. Bscan rows time, columns x; positive down. Image data remain quantitative (no per-image normalization); UI windows separately with displayed bounds. X-ray tilt uses the same detector extent but is a projection: no exact xy co-registration at nonzero angle. Arrays are lists. The API adds run_id, input_sha256, twin/settings/materials snapshots, evidence_status and timestamp for provenance. Browser exports keep subsequent probe data in `probe_inspection`, preserving the original acquisition arrays/settings/hash.

## Frontend

Version 0.11 adds standalone endpoints under `/api/v2/layered-acoustics`:
`POST /column` takes `{twin,x_mm,y_mm,include_defects}`; `POST /estimate` and
`POST /reports` take the strict `LayeredAnalysisRequest` from
`virtual_microscopy/layered_schemas.py`; report creation returns 201.
`GET /reports` returns `{reports:[summaries]}`; `GET /reports/{id}` and
`GET /reports/{id}/export?format=json|csv` read immutable reports. These operations
use the shared processing lock and never create volume jobs. Validation errors
return 422, missing reports 404 and storage errors 507. A request contains name,
stack (incident/terminal media and ordered finite layers), spectrum settings,
optional single-slab pulse settings and optional original source-column request.
Responses retain real/imaginary/magnitude/wrapped-phase arrays, raw energy,
optional RF/echo arrays, provenance and source-difference status. Phase is null
below pressure magnitude 1e-12. See [LAYERED_ACOUSTICS.md](docs/LAYERED_ACOUSTICS.md)
for units, limits, reference planes, coefficients and immutable CSV conventions.

Version 0.12 extends `LayeredAnalysisRequest` with optional
`causal_pulse:CausalGammaPulseSettings`, mutually exclusive with Gaussian slab
`pulse`. It permits all valid finite stacks, including empty/zero-thickness
stacks. Controls are carrier/bandwidth, gamma order (4–24), recording/standoff,
absolute tolerance (1e-12–1e-3) and precision (64/96/128/192/256 bits). It retains
at most 2,049 actual time centers. The response's `causal_pulse` contains
`time_us`, `rf`, `imaginary`, `envelope` and diagnostics with separate
`analytic_alias_bound`, `frequency_cutoff_bound`, `arithmetic_complex_bound`,
`arithmetic_envelope_bound`, and the accepted `total_error_bound`. Preflight
exposes its independent contour/work estimate under `causal_pulse`; successful
preflight does not guarantee that synthesis will close the arithmetic bound.
Historical JSON reports retain their original fields and hashes. New causal
reports record python-flint and native FLINT versions. No saved volume schema,
job semantics or depth-mapping contract changes in this release.

`npm run build` emits `web/dist`; Vite development proxies `/api` to `127.0.0.1:8765`. The app fetches examples and simulates the selected twin; `?specimen=<example-id>` selects a specific initial example. A validated recommended preset takes precedence over generic initial controls. Imports are validated before replacing the twin. Imported labels/references are rendered as text, with HTTP(S) links only. Geometry and acquired data are drawn with Three.js and canvas; acquisition values remain distinct from display windowing and exploded-view spacing.

## Saved causal column volumes (0.13)

`POST /api/v2/estimate` and `POST /api/v2/jobs` accept the explicitly tagged
`{kind:'sam_causal_rf_volume',twin,acquisition}`. The strict acquisition combines
the v0.12 gamma settings with `scan_nx`, `scan_ny` (16–64), optional global
`roi_mm`, `include_defects`, fixed `path_model:'continuous_columns_v1'`, and
fixed `observation_model:'independent_columns_v1'`. Unsupported focus, Z-grid,
noise or alternate observation fields reject. Omitted-kind historical SAM
requests keep their original meaning. The existing job status/cancel/resume and
dataset manifest/catalog/export endpoints handle the new kind explicitly.

The estimate freezes actual `x_mm`, `y_mm`, `time_us`, complete `stack_table`,
`class_index`, shape `[y,x,time]`, resource/work counts, material assumptions and
observation/excitation identities. Three float64 signal arrays (`rf`,
`imaginary`, `envelope`) accompany a float64 `[y,x]` `error_bound` and uint16
`class_index`. Every row commits its signals and class certificates together;
the completed `total_error_bound` is the maximum retained per-column bound.

`GET /api/v2/causal-datasets/{id}/view` accepts `x_index`, `y_index`, `time_index`,
`product:'rf'|'imaginary'|'envelope'`, and optional `gate_start_us`, `gate_end_us`,
`gate_mode:'peak_envelope'|'rms_rf'`. It returns `xy`, `xt`, `yt`, `ascan`,
`cscan`, `cursor`, `gate`, `certificate`, and `metadata`. All time centers are
retained without time pooling. The gate must lie in the saved record and contain
samples; its response includes the actual first/last included centers. The
per-sample certificate is distinct from derived gate statistics.

Numerical views and exports require a completed, verified dataset. Wrong-kind
legacy views, depth mapping, ordinary SAM/X-ray comparisons, recipes and batches
reject this new kind. Version 0.14 adds its separate comparison routes below.
Export is a `sam-causal-volume-` Zarr ZIP of
the original typed bytes and frozen manifest. See
[CAUSAL_SAM_VOLUMES.md](docs/CAUSAL_SAM_VOLUMES.md) for model, resource, time and
historical-reader conventions.

## Saved causal-volume comparisons (0.14)

- `POST /api/v2/causal-comparisons` accepts strict
  `{name?,reference_dataset_id,candidate_dataset_id,policy:'same_excitation_v1',gate_start_us,gate_end_us,x_index?,y_index?,time_index?}`
  and returns an immutable `sam_causal_comparison` report (201). Policy defaults
  to `same_excitation_v1`; the initial cursor defaults to the source midpoint.
  Gate bounds must be ordered, lie in the recording and include actual saved
  centers. Dataset IDs are canonical UUIDs and indices are strict integers.
- `GET /api/v2/causal-comparisons?limit=50&offset=0` returns
  `{comparisons,limit,offset,order:'id_desc',next_offset}`. The limit is 1–100,
  offset 0–9,999 and scan ceiling 10,000 report filenames. Only the requested
  page is decoded. UUID order is stable, not chronological.
- `GET /api/v2/causal-comparisons/{id}` and `/export?format=json|csv` return the
  frozen report. CSV uses `section,field,value_json`, retaining each complete
  top-level field in a lossless JSON cell, including maps and source manifests.
- `GET /api/v2/causal-comparisons/{id}/view` without indices returns the frozen
  initial view without source access. An explicit `x_index`, `y_index` or
  `time_index` requests a current source-backed view; both source manifest hashes
  and typed row bytes must still match the frozen report. Its changed cursor does
  not modify the report, metrics, maps or gate.

Compatibility requires identical typed X/Y/time coordinates, extents, axes,
units, excitation parameters, time reference, normalization, supported observation
and certificate semantics, with independent unfocused lossless columns and water
exteriors. Different twin inputs, represented layer properties, tolerances and
precision are retained and disclosed. Source-local class IDs are not cross-source
identities; changed columns are counted using resolved numerical stacks.

The report retains complete source manifests and hashes, compatibility details,
full-record and gated metrics, peak-magnitude/RMS-real gate maps, an initial
linked view, provenance and five `[y,x]` bound maps: `source_sum`,
`complex_arithmetic`, `complex_total`, `envelope_arithmetic`, `envelope_total`.
Real/imaginary residuals are signed B−A; the envelope residual subtracts the
separately saved magnitudes. Source sums and subtraction allowances are rounded
outward using exact represented-input rational arithmetic. Gate statistics and
summary metrics remain ordinary diagnostics outside these samplewise bounds.

Sources retain their 8 MiB serialized / 32 MiB expanded limits; reports are capped
at 64 MiB serialized / 192 MiB expanded. All phases also satisfy a conservative
512 MiB owned-workspace admission bound. Exclusive publication never overwrites
an existing report. Historical reads and exports require saved integrity, not a
matching current solver or installed FLINT runtime. New numerical operations
require the supported binary64 subtraction environment. Encoding occurs under
the shared processing lock. No endpoint creates acquisition jobs.

Compatibility errors return 422 with `{message,issues}`; other invalid requests
return 422, missing reports/sources 404 and storage failures 507. See
[CAUSAL_COMPARISONS.md](docs/CAUSAL_COMPARISONS.md) for exact numerical semantics,
resource admission, offline reopening and the distinction from measured accuracy.

## Derived coherent observations (0.15)

The separate kind `sam_coherent_observation_volume` uses
`/api/v2/observations` routes: estimate; job creation/list/detail/cancel/resume;
and dataset list/detail/view/export. A strict request contains `kind?`, `name?`,
`source_dataset_id`, `operator:'binomial_3x3_coherent_v1'` and
`absolute_tolerance` (default `1e-7`, range `1e-12..1e-3`). Only completed
supported independent causal sources are accepted. Lists are chronological with
an ID tie-breaker and bounded `limit`/`offset` pagination.

The fixed nine-neighbor exact dyadic stencil mixes complex pressure with zero
added phase, crops unsupported borders and preserves actual interior X/Y and
all recording-time centers. Outputs are three float64 `[y,x,time]` signal arrays
and five float64 `[y,x]` maps: `source_propagation`, `complex_arithmetic`,
`complex_total`, `magnitude_arithmetic`, `magnitude_total`. Magnitude is computed
after complex mixing. Numerical admission and typed publication are specified in
[COHERENT_OBSERVATIONS.md](docs/COHERENT_OBSERVATIONS.md).

Jobs commit complete output rows through the same owned worker as historical
acquisitions, preserving the old dispatcher, batch controls and fingerprints.
The queues have separate catalogs and shared pending disk reservations. Views
accept actual saved X/Y/time indices, product and recording-time gates; return
linked maps/traces, source indices and all five bound maps; and never reinterpret
multiple returns as unique depth. Completed views and typed Zarr ZIP exports
require only the verified derived dataset. Resume requires the matching parent
source and implementation. Historical verification explicitly distinguishes
rechecking saved bound composition from reconstructing source-dependent
component conversion maxima.

## Saved observation comparisons (0.16)

The separate `sam_observation_comparison` kind uses
`/api/v2/observation-comparisons`: POST to create; GET to list or read `/{id}`;
`/{id}/view` for the frozen initial or source-backed explicit cursor; and
`/{id}/export?format=json|csv` for lossless report exports. Requests contain
reference/candidate dataset IDs, finite ordered recording gate endpoints,
optional name/cursor, and fixed `same_observation_and_excitation_v1` policy.
Only completed supported finite coherent observation volumes are admitted.

Compatibility includes exact retained and full surrounding coordinates,
extents/indices/neighbor offsets, array units/axes, operator weights/order/phase,
supported numerical contracts and frozen nested excitation/time/exterior media.
Six `[y,x]` bound maps separate `complex_source_sum`, `magnitude_source_sum`,
`complex_arithmetic`, `complex_total`, `magnitude_arithmetic`, `magnitude_total`.
Full-record and gated metrics remain ordinary diagnostics. No new jobs are made.

Complete source manifests appear once in `source_snapshots`, keyed by canonical
SHA-256; lightweight `source_reference`/`source_candidate` reference those hashes.
Self comparisons deduplicate the exact snapshot. Each includes its complete
nested causal provenance. Historical report reads, initial views and exports
require no source arrays; new cursors require matching observation data.
Source limits are 16 MiB serialized/64 MiB expanded, reports 64/192 MiB, with
512 MiB owned-workspace admission and at most three million complex positions.
Publication is exclusive and atomic. Existing source-free observation certificate
limitations are retained. See [OBSERVATION_COMPARISONS.md](docs/OBSERVATION_COMPARISONS.md).

## Standalone scalar SLS material response (0.17)

The separate `sls_layered_analysis` report kind uses `/api/v2/sls-acoustics`:
POST `/estimate`, POST `/reports` (201), paginated GET `/reports`, GET
`/reports/{id}` and GET `/reports/{id}/export?format=json|csv`. Reports are
exclusive immutable JSON documents in `sls-reports`. No acquisition job is made.

Requests contain `name`, `stack`, `spectrum` and optional `causal_pulse`. The
manual stack has real lossless incident/terminal media and at most eight layers;
each layer specifies `name`, `thickness_mm`, `density_kg_m3`,
`relaxed_modulus_gpa`, `unrelaxed_modulus_gpa`, and `relaxation_time_us`.
The unrelaxed modulus cannot be smaller than the relaxed modulus. Redundant
finite-layer speed/impedance and old/source-column fields are rejected.
Displayed-unit values are exact represented inputs; SI conversions are enclosed
inside the numerical kernel. Terminal speed is retained metadata and does not
affect scattering at a fixed terminal impedance; incident speed sets standoff
delay when standoff is nonzero.

Spectra retain frequency, complex pressure R/T and phase, exterior energy
diagnostics and each authored material's attenuation/phase-speed/impedance curves.
Optional RF retains actual `time_us`, signed `rf`, `imaginary`, `envelope` and
its reflected-only gamma diagnostics. The numerical total is an outward sum of
the published alias, cutoff, complex arithmetic and magnitude arithmetic bounds.
It does not cover transmission, calibrated material uncertainty or physical
resolution. Historical reads/exports use saved structural contracts without
current material kernels. Limits, proof and workflow are documented in
[SLS_ACOUSTICS.md](docs/SLS_ACOUSTICS.md).

## Saved scalar SLS comparisons (0.18)

The separate `sls_analysis_comparison` report kind uses `/api/v2/sls-comparisons`:
POST `/estimate`, POST `/reports` (201), paginated GET `/reports`, GET
`/reports/{id}`, GET `/reports/{id}/view?frequency_index=...&time_index=...`
and GET `/reports/{id}/export?format=json|csv`. Immutable JSON files live in
`sls-comparisons`. No acquisition or forward-material job is created.

Requests specify `reference_report_id`, `candidate_report_id`, `mode`
(`spectrum_only` or `spectrum_and_reflected_rf`), optional paired inclusive
`gate_start_us` / `gate_end_us`, and optional initial `frequency_index` /
`time_index`. RF gates and time cursors are unavailable in spectrum-only mode.
Comparison admission validates complete original source reports, exact represented
axes and exterior/excitation/phase/reference-plane/model semantics. Allowed
material and numerical-setting differences remain explicit; no automatic layer
correspondence, alignment, scaling or resampling occurs.

`source_snapshots` retains complete source reports keyed by their canonical
content digests. `source_reference` and `source_candidate` refer to those entries;
self-comparisons deduplicate. `spectrum.difference` and optional
`causal_pulse.difference` contain signed B-minus-A residuals. Source A/B arrays
remain in snapshots, with no duplicate view copies. RF `bounds` contains
`source_sum`, `complex_arithmetic`, `complex_total`, `magnitude_arithmetic`
and `magnitude_total`; both totals enclose their already published components.
These full-record bounds concern reflected model residuals, including the
difference of separately saved magnitudes. Spectral and statistical products
are ordinary diagnostics. Gates never tighten the numerical bounds.

Historical report, cursor and export operations validate frozen snapshots and
comparison identity without the original source files or current model kernels.
The comparison retains its own `store_identity` in addition to complete source
identities. Serialized/expanded comparison ceilings are 32/128 MiB, within a
512 MiB owned-workspace admission; source manifests remain bounded to 16/64 MiB.
Publication is exclusive and atomic. See [SLS_COMPARISONS.md](docs/SLS_COMPARISONS.md)
for the workflow, exact outward bound composition and evidence limits.
