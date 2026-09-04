# Virtual microscopy 0.4 integration contract (compatible twin schema 1)

Local application: Python FastAPI serves a Vite/vanilla JS + Three.js client. Source in `virtual_microscopy/` and `web/`. Physical dimensions use millimetres. Coordinates x right, y down in image, z depth from specimen top; surrounding material is water for SAM and air for X-ray. Later primitives replace earlier ones. This is a reduced-order synthetic forward simulator, not experimentally validated or coupled full-wave multiphysics.

## Twin JSON

`{schema_version:1,name:string,description:string,size_mm:[x,y,z],objects:[...]}`

Object: `{id:string,name:string,shape:'box'|'sphere'|'cylinder',material:'silicon'|'copper'|'solder'|'epoxy'|'fr4'|'air',center_mm:[x,y,z],size_mm:[x,y,z],role:'structure'|'defect'}`. Coordinates span [0,size_mm] and size_mm means full extents; sphere uses size_mm[0] diameter (all extents equal); cylinder axis z with equal x/y diameter. Object bounds must stay inside twin bounds. No custom materials in v1. Model defaults are in `examples/`; reproducible generators are in `tools/`.

Optional primitive `display_label` supplies a short 3D label. Optional twin `reference` is `{product,summary,sources:[{id,title,url}],published_facts:[{label,value,source_ids}],assumptions:[string]}`; sources use valid HTTP(S) URLs, unique IDs, and each fact must cite existing source IDs. Optional `recommended_settings` follows the complete Settings schema below and must keep its probe/focus in bounds. These fields are retained in exported twins; absent optional fields are omitted to preserve legacy shapes. Specimen/probe x/y extents support 100 mm; depth supports 6 mm and primitive count is bounded at 600. Raster limits remain unchanged.

## Layered HBM extension

Optional `hbm_assemblies` contains at most 12 parameter assemblies: `id`, `name`, `center_xy_mm`, `footprint_mm`, `bottom_z_mm`, `die_count` (8 or 12), `die_thickness_um`, `gap_um`, `base_thickness_um`, `cap_thickness_um`, `functional_state` (enabled/disabled/unknown), `physical_present`, and `evidence`. Total height is base + die_count × (die + gap) + cap, in µm. Positive dimensions and specimen bounds are validated.

Compiled primitives use `assembly_id` and `layer_role` (base_die/dram_die/interdie_gap/cap/underfill/contact). Each group's primitives must be contiguous and match the compiler's ordered geometry exactly. This prevents import metadata divergence and edits that accidentally reorder unrelated material. Functional state does not affect material geometry. Arbitrary imported evidence is preserved; the default specimen describes its initial assumptions explicitly.

Optional `image_reference` contains SHA-256, raster width/height, pixel_size_um, scale_status (user_estimate/calibrated), title and source_note. Declared calibration is metadata supplied by the importer, not a server certification. Image bytes and filesystem paths are not carried in exported twins.

## API

- GET /api/health -> {status:'ok',version:'0.4.0'}
- GET /api/examples -> [{id,name,description,twin}]
- GET /api/materials -> list of material dicts with id,name,color,density_g_cm3,sound_speed_m_s,impedance_mrayl and provenance; extra properties permitted.
- POST /api/validate -> twin body -> {valid:true,twin:normalized twin,warnings:[]}; errors HTTP 422.
- POST /api/simulate -> `{twin,settings}`. Settings defaults: `{resolution:128,energy_kev:80,angle_deg:0,photons:50000,noise:true,frequency_mhz:50,gate_start_us:0.42,gate_end_us:0.56,focus_mm:0.5,probe_x_mm:3.1,probe_y_mm:3.1,include_defects:true,seed:42}`. resolution allowed 64,128,192; energy 40..150, angle -45..45 about y, photons 1000..1000000, frequency 10..150, gate_start 0..10, gate_end up to 12 with end>start; focus 0..specimen z; probe within xy. Water standoff excluded from time (t=0 at top plane). Probe coordinates are physical x/y, not detector coordinates when tilted.
- POST /api/probe same body/settings -> `{ascan:...,bscan:...}`; synthesizes a local strip. All runs bounded by dimensions, primitive counts and RF work budget.

Optional acquisition `roi_mm` is `[xmin,ymin,xmax,ymax]` in global millimetres with at least 0.05 mm width/height. ROI and probe must fit the specimen; probes must also fit the ROI. ROI acquisition requires angle_deg=0. Optional `depth_samples` accepts 128/256/512/1024 independently of the lateral raster; omission retains nz=2×resolution. The image extent is `[xmin,xmax,ymin,ymax]`. The sampled grid includes the complete specimen depth and a clipped 4-sigma lateral context for both Gaussian PSFs. Geometry allocation is capped at 64 million cells, in addition to the existing RF work budget. Missing optional settings are omitted from acquisition snapshots.

- POST /api/hbm/compose -> `{twin,assembly_id,parameters:partial HBM parameters}` -> `{twin,warnings}`. Invalid or null patch values return 422. The input snapshot is not mutated. Geometry updates preserve unrelated objects and fixed-coordinate defects; updated objects stay before those defects.
- POST /api/hbm/section -> `{twin,assembly_id,axis:'xz'|'yz',resolution:128|256|512,include_defects?:boolean}` -> material `image` labels, `materials` legend, `extent_mm:[u0,u1,z0,z1]`, axis, fixed_coordinate_mm, pixel_pitch_um and warnings. Section sampling includes intersecting package geometry. `mode:'material_geometry'` distinguishes it from microscope/reconstruction output.
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
future reconstruction; their view axis is not a spatial z axis.


## Simulation response (plain JSON numeric arrays)

```
{
  xray:{image:[[float]],unit:'I / I0',extent_mm:[0,x,0,y],min:float,max:float,mean_transmission:float},
  sam:{image:[[float]],unit:'relative echo amplitude',extent_mm:[0,x,0,y],min:float,max:float,peak_amplitude:float},
  ascan:{time_us:[float],amplitude:[float],envelope:[float],probe_mm:[x,y]},
  bscan:{image:[[float]],extent:[0,x,0,time_max_us],unit:'relative echo amplitude',y_mm:float},
  metadata:{runtime_ms:float,grid_shape:[ny,nx,nz],grid_origin_mm:[x,y,0],acquisition_shape:[n,n],roi_mm:null|[xmin,ymin,xmax,ymax],pixel_pitch_um:[dx*1000,dy*1000],voxel_depth_um:float,seed:42,model_version:'0.2.0',warnings:[string],assumptions:[string]}
}
```

Rows of xray/SAM are y; columns x. Bscan rows time, columns x; positive down. Image data remain quantitative (no per-image normalization); UI windows separately with displayed bounds. X-ray tilt uses the same detector extent but is a projection: no exact xy co-registration at nonzero angle. Arrays are lists. The API adds run_id, input_sha256, twin/settings/materials snapshots, evidence_status and timestamp for provenance. Browser exports keep subsequent probe data in `probe_inspection`, preserving the original acquisition arrays/settings/hash.

## Frontend

`npm run build` emits `web/dist`; Vite development proxies `/api` to `127.0.0.1:8765`. The app fetches examples and simulates the selected twin; `?specimen=<example-id>` selects a specific initial example. A validated recommended preset takes precedence over generic initial controls. Imports are validated before replacing the twin. Imported labels/references are rendered as text, with HTTP(S) links only. Geometry and acquired data are drawn with Three.js and canvas; acquisition values remain distinct from display windowing and exploded-view spacing.
