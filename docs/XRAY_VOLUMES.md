# Saved X-ray projection acquisitions

Version 0.4 adds full-angle CPU parallel-beam radiography and saved projection
stacks. It uses the entire sampled specimen to calculate attenuation at each
angle, including side views at 90° and 270°. Saved arrays have axes
**[view, detector row v, detector column u]**. They are synthetic projection
acquisitions, not reconstructed xyz volumes or experimentally calibrated images.

## Workflow

Open **X-ray volumes** in the workbench. The acquisition form snapshots the current
digital twin and defect selection. The original SAM ROI does not crop the X-ray
material model. Set source, detector, material-grid and rotation settings; inspect
the resource estimate and sampling/truncation warnings; then start an acquisition.

The existing local worker queues both acoustic and X-ray jobs, one at a time.
X-ray progress counts views. Cancellation retains committed views; resume verifies
them and continues the same frozen acquisition. Completed datasets remain in the
catalog after a restart. The original acoustic volume viewer and saved RF files
remain available.

Open a completed X-ray dataset to scrub its views, choose a detector row, and read
the corresponding sinogram and row profile. Switch between photon counts,
transmission and negative-log transmission. These actions read saved arrays and
do not run propagation. Download the archive to retain all projection values,
coordinate vectors, poses and provenance for reconstruction. Version 0.5 adds a
separate [CT workspace](RECONSTRUCTION.md) for compatible completed sources.

## Acquisition controls

| Field | Meaning and allowed range |
| --- | --- |
| `geometry_nx`, `geometry_ny` | Full-specimen material-grid counts, 16–256; defaults 96 each |
| `geometry_nz` | Material samples through depth, 32–1024; default 256 |
| `detector_cols`, `detector_rows` | Independent detector sampling, 16–256; defaults 96 × 64 |
| `detector_width_mm`, `detector_height_mm` | Optional field dimensions, 0.05–300 mm; omission chooses a field covering the specimen envelope through a full rotation about the chosen center before detector offsets |
| `detector_offset_u_mm`, `detector_offset_v_mm` | Detector-center displacement along the detector's local unit axes, −100 to 100 mm |
| `rotation_center_mm` | Optional global `[x,y,z]` inside specimen bounds; defaults to specimen center |
| `views` | Number of projections, 1–720; default 72 |
| `angle_start_deg` | First view angle, −360° to 360° |
| `angle_span_deg` | Positive angular span, up to 360°; the end is excluded |
| `energy_kev` | Monoenergetic photon energy, 40–150 keV; default 80 |
| `photons` | Incident photons per detector pixel, 1,000–1,000,000; default 50,000 |
| `noise`, `seed` | Poisson counting noise toggle and reproducible integer seed |
| `detector_fwhm_mm` | Illustrative Gaussian detector PSF width, 0–1 mm; default 0.020 mm |
| `include_defects` | Includes or omits primitives marked as synthetic defects |

Angles are `start + k × span / views`, for `k = 0 ... views-1`. Thus 72 views over
360° are 0°, 5°, ..., 355°, with no duplicated endpoint. A single view records
only its start angle. The requested span does not imply measurements between the
actual saved angles.

Detector pitch is field size divided by detector count. Material pitch is specimen
size divided by geometry count. Neither is a measured instrument resolution. A
small detector field does not refine the material grid; it may simply sample the
same coarse material representation more densely. Features smaller than material
spacing can disappear. Increase the relevant geometry counts and check convergence
when interpreting thin layers or interconnects.

## Coordinates and forward model

The specimen coordinate convention remains x right, y down, z into the specimen.
Rotation is about **Y**. For angle `theta`:

```text
ray direction d = [sin(theta), 0, cos(theta)]
detector axis U = [cos(theta), 0, -sin(theta)]
detector axis V = [0, 1, 0]
detector center D = rotation_center + offset_u * U + offset_v * V
ray(u,v,s) = D + u * U + v * V + s * d
```

`u_mm` and `v_mm` are detector-local pixel-center coordinates. Offsets are already
included in `D`; they must not be applied a second time. The saved detector center
defines a **virtual projection reference plane**. In parallel geometry, moving
that plane along the ray direction does not change a complete line integral.
No finite source distance, cone-beam magnification or source spectrum is modeled.
A detector pixel defines a ray through the specimen, not a unique specimen point.

The CPU projector intersects each ray with the specimen envelope and with crossed
x/z voxel planes, then sums each segment's length times its material attenuation.
It handles cardinal directions without the old preview's tangent/cosine
parameterization. Integration is exact through the piecewise-constant **sampled
voxel grid**; voxelization of curved or sub-voxel geometry remains approximate.
Air outside primitives has the same open-beam reference convention as the preview.

The model evaluates `I/I0 = exp(-sum(mu * path_length))`, blurs ideal transmission
with the detector PSF, then optionally samples Poisson counts. The detector halo
includes the Gaussian support outside the requested field; attenuation still uses
the entire material grid. Truncation warnings describe projected specimen-envelope
coverage and do not repair missing detector data.

Material attenuation comes from the existing 40–150 keV library and its declared
surrogates. Spectrum, tube kVp/current calibration, focal-spot blur, scatter, beam
hardening, dark/flat calibration uncertainty and electronic detector noise remain
future work. Public geometry conventions are documented in the
[ASTRA geometry reference](https://astra-toolbox.com/docs/geom3d.html); this release
uses an independent CPU implementation and does not install or invoke ASTRA.
Its unit detector basis vectors must be multiplied by pixel pitch, and axes
adapted, before constructing an ASTRA vector geometry.

## Numerical products and integrity

Each dataset contains `manifest.json` and `data.zarr/` in the existing local volume
directory. The manifest retains the exact twin/request, material/water snapshot,
source/numerical identities, detector and voxel pitch, truncation/sampling warnings,
seed scheme, array descriptors and per-view completion/checksum registry.

| Array | Type and axes | Interpretation |
| --- | --- | --- |
| `counts` | float32 `[view,v,u]` | Integer-valued Poisson photon counts when noise is on; fractional expected counts when off |
| `transmission` | float32 `[view,v,u]` | Stored counts divided by incident photons |
| `line_integrals` | float32 `[view,v,u]` | Dimensionless negative log transmission; not attenuation per mm |
| `valid_mask` | float32 `[view,v,u]` | 1 where stored counts are positive and the logarithm is defined; 0 at zero counts |
| `angles_deg` | float64 `[view]` | Every actual acquisition angle |
| `u_mm`, `v_mm` | float64 `[u]`, `[v]` | Detector-local pixel-center coordinates |
| `ray_direction_xyz` | float64 `[view,xyz]` | Unit illumination direction |
| `detector_center_mm` | float64 `[view,xyz]` | Global virtual detector-plane center |
| `detector_u_xyz`, `detector_v_xyz` | float64 `[view,xyz]` | Unit detector basis vectors |

Zero counts are valid raw count observations, but their logarithm is undefined.
Only those pixels use a 0.5-count substitute for a finite stored line integral,
and they remain marked with `valid_mask=0`. Positive fractional expected counts
are not floored. An extremely small noiseless expected count can underflow to zero
in float32 storage; it then uses the same log substitute and zero validity mask.
Negative noisy line integrals can occur when counts exceed the
incident reference; they are retained. A display window may clip colors without
changing these stored values. Reconstruction must account for the validity mask
and the stated detector/noise model.

Each view uses an independent deterministic random stream based on its saved seed
and view index. Resume preserves the exact per-view samples under the same solver
and numerical versions. Every signal and pose/coordinate array is checksummed.
Missing or damaged partial views are regenerated; completed datasets are immutable
through the API and are integrity-checked before export. The catalog migration
identifies existing acoustic records as SAM without changing their saved arrays.

The sinogram displays one detector row across the saved angles. Its bin edges
extend half an angular step around each sample; those edges are display support,
not additional measurements. Browsing reads one stored view chunk at a time and
shares the bounded processing queue with acoustic views and archive construction.

```python
from pathlib import Path
import json
import zarr

path = Path("my-extracted-projection-archive")
manifest = json.loads((path / "manifest.json").read_text())
data = zarr.open_group(str(path / "data.zarr"), mode="r")
projection = data["transmission"][0, :, :]
sinogram = data["line_integrals"][:, 20, :]
angles = data["angles_deg"][:]
valid = data["valid_mask"][:, 20, :]
```

Preflight caps material cells at 64 million, the uncompressed saved arrays at
512 MiB, estimated numerical workspace at 512 MiB, and ray-segment work at its
local budget. The estimate excludes interpreter, API and compression overhead and
does not assume compression savings. Disk checks include a reserve. Settings are
never silently reduced. Fewer views/detector pixels reduce saved data; changing
the field alone does not change its array size.

Version 0.5 provides a CPU parallel-beam reconstruction baseline using these saved
inputs. It creates a separate spatial attenuation dataset; a projection stack's
view axis remains angular. Optional GPU cone-beam and laminography support need
additional geometry and validation. See [Reconstruction](RECONSTRUCTION.md) and
[the expansion plan](EXPANSION_PLAN.md).
