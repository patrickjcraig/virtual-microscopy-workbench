# Virtual microscopy v1 integration contract

Local application: Python FastAPI serves a Vite/vanilla JS + Three.js client. Source in `virtual_microscopy/` and `web/`. Physical dimensions use millimetres. Coordinates x right, y down in image, z depth from specimen top; surrounding material is water for SAM and air for X-ray. Later primitives replace earlier ones. This is a reduced-order synthetic forward simulator, not experimentally validated or coupled full-wave multiphysics.

## Twin JSON

`{schema_version:1,name:string,description:string,size_mm:[x,y,z],objects:[...]}`

Object: `{id:string,name:string,shape:'box'|'sphere'|'cylinder',material:'silicon'|'copper'|'solder'|'epoxy'|'fr4'|'air',center_mm:[x,y,z],size_mm:[x,y,z],role:'structure'|'defect'}`. Coordinates span [0,size_mm] and size_mm means full extents; sphere uses size_mm[0] diameter (all extents equal); cylinder axis z with equal x/y diameter. Object bounds must stay inside twin bounds. No custom materials in v1. Model defaults are in `examples/`; reproducible generators are in `tools/`.

Optional primitive `display_label` supplies a short 3D label. Optional twin `reference` is `{product,summary,sources:[{id,title,url}],published_facts:[{label,value,source_ids}],assumptions:[string]}`; sources use valid HTTP(S) URLs, unique IDs, and each fact must cite existing source IDs. Optional `recommended_settings` follows the complete Settings schema below and must keep its probe/focus in bounds. These fields are retained in exported twins; absent optional fields are omitted to preserve legacy shapes. Specimen/probe x/y extents support 100 mm; depth supports 6 mm and primitive count is bounded at 600. Raster limits remain unchanged.

## API

- GET /api/health -> {status:'ok',version:'0.1.0'}
- GET /api/examples -> [{id,name,description,twin}]
- GET /api/materials -> list of material dicts with id,name,color,density_g_cm3,sound_speed_m_s,impedance_mrayl and provenance; extra properties permitted.
- POST /api/validate -> twin body -> {valid:true,twin:normalized twin,warnings:[]}; errors HTTP 422.
- POST /api/simulate -> `{twin,settings}`. Settings defaults: `{resolution:128,energy_kev:80,angle_deg:0,photons:50000,noise:true,frequency_mhz:50,gate_start_us:0.42,gate_end_us:0.56,focus_mm:0.5,probe_x_mm:3.1,probe_y_mm:3.1,include_defects:true,seed:42}`. resolution allowed 64,128,192; energy 40..150, angle -45..45 about y, photons 1000..1000000, frequency 10..150, gate_start 0..10, gate_end up to 12 with end>start; focus 0..specimen z; probe within xy. Water standoff excluded from time (t=0 at top plane). Probe coordinates are physical x/y, not detector coordinates when tilted.
- POST /api/probe same body/settings -> `{ascan:...,bscan:...}`; synthesizes a local strip. All runs bounded by dimensions, primitive counts and RF work budget.

## Python engine callable

`virtual_microscopy.physics.simulate(twin:dict, settings:dict)->dict` receives fully populated validated settings; `probe(twin,settings)->dict`. `virtual_microscopy.materials.MATERIALS` is dict keyed material id; public values include id. Schemas/API live alongside those modules. Examples and analytical/API tests are independent of the frontend.

## Simulation response (plain JSON numeric arrays)

```
{
  xray:{image:[[float]],unit:'I / I0',extent_mm:[0,x,0,y],min:float,max:float,mean_transmission:float},
  sam:{image:[[float]],unit:'relative echo amplitude',extent_mm:[0,x,0,y],min:float,max:float,peak_amplitude:float},
  ascan:{time_us:[float],amplitude:[float],envelope:[float],probe_mm:[x,y]},
  bscan:{image:[[float]],extent:[0,x,0,time_max_us],unit:'relative echo amplitude',y_mm:float},
  metadata:{runtime_ms:float,grid_shape:[ny,nx,nz],pixel_pitch_um:[x/n*1000,y/n*1000],voxel_depth_um:float,seed:42,model_version:'0.1.0',warnings:[string],assumptions:[string]}
}
```

Rows of xray/SAM are y; columns x. Bscan rows time, columns x; positive down. Image data remain quantitative (no per-image normalization); UI windows separately with displayed bounds. X-ray tilt uses the same detector extent but is a projection: no exact xy co-registration at nonzero angle. Arrays are lists. The API adds run_id, input_sha256, twin/settings/materials snapshots, evidence_status and timestamp for provenance. Browser exports keep subsequent probe data in `probe_inspection`, preserving the original acquisition arrays/settings/hash.

## Frontend

`npm run build` emits `web/dist`; Vite development proxies `/api` to `127.0.0.1:8765`. The app fetches examples and simulates the selected twin; `?specimen=<example-id>` selects a specific initial example. A validated recommended preset takes precedence over generic initial controls. Imports are validated before replacing the twin. Imported labels/references are rendered as text, with HTTP(S) links only. Geometry and acquired data are drawn with Three.js and canvas; acquisition values remain distinct from display windowing and exploded-view spacing.
