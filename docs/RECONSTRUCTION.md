# Saved X-ray reconstruction

Version 0.5 adds an independent CPU filtered-backprojection (FBP) baseline. It
creates a new spatial attenuation dataset from a completed saved X-ray acquisition.
The output has axes **[z,y,x]**, coordinates in millimetres, and attenuation in
**mm⁻¹**. The inversion uses saved logarithms, masks and detector poses; it does
not use the twin's primitive material labels to fill the reconstructed volume.

## Workflow and controls

Open **CT reconstruction** and choose a completed X-ray source. This selection
uses the frozen source, independently of the twin or acoustic ROI currently open
in the preview. Set bounds and output sampling, choose the filter and processing
policies, inspect the estimate, and start reconstruction. Jobs share the existing
local worker and support cancellation/resume at committed Z-plane boundaries.

| Setting | Meaning |
| --- | --- |
| Source dataset | Completed `xray_projection_volume`; at least 16 uniformly spaced views covering exactly 180° or 360° with the endpoint excluded |
| `nx`, `ny`, `nz` | Output counts, each 16–256; defaults 96, 64, 64 |
| `bounds_mm` | Optional `[xmin,xmax,ymin,ymax,zmin,zmax]` within the source specimen; omission uses the full source envelope |
| `filter` | `hann` (default) or `ram_lak` |
| `frequency_cutoff` | 0.1–1 of the source detector-U Nyquist frequency; default 1 |
| `invalid_policy` | `interpolate` (default) or `reject` for zero-count logarithms |
| `truncation_policy` | `reject` (default) or explicitly `allow` truncated source data |

Each output pitch is the chosen bound width divided by its count. Reducing voxel
size changes output sampling; it does not restore information absent from the
source detector or angular sampling. H100's thin package can be sampled densely
in Z while remaining poorly resolved by a coarse detector. The estimate reports
output pitch, detector-U pitch, angular sampling and reconstruction warnings.

The baseline accepts only the saved canonical parallel-beam rotation about
specimen Y. It validates the actual angle, detector-coordinate and pose arrays.
Limited-angle, nonuniform, cone-beam and tilted-axis acquisitions are rejected.
The `allow` truncation setting does not override these geometry restrictions.

## Numerical definition

At each requested Y, interpolate the measured detector-V rows linearly. Apply a
discrete ramp convolution along detector U, then backproject using the stored
detector center and unit U vector:

```text
u(point, view) = dot(point - detector_center[view], detector_u[view])
mu(point) = (pi / number_of_views) * sum(filtered_projection[view, v(point), u(point)])
```

The ramp kernel includes the detector pitch `du` in millimetres:
`h[0] = 1/(4du)`, `h[n] = -1/(pi² n² du)` for odd n, and zero for nonzero even n.
FFT filtering pads to a power of two at least twice the detector width. The Hann
option applies a cosine taper to the cutoff; Ram-Lak retains the ramp up to the
selected cutoff. The angular factor gives opposed views in a 360° scan the
corresponding half weight, preserving attenuation scale relative to a 180° scan.

Detector sampling interpolates U/V linearly and extends the nearest pixel center
only to its outer half-pixel edge. Points beyond the physical detector field have
incomplete support. Source/detector offsets occur in the saved detector center
and are not applied twice. Negative supported reconstructed values are retained;
display limits do not clip the stored array.

This implementation follows the parallel FBP framework described by
[Kak and Slaney, Chapter 3](https://www.slaney.org/pct/pct-toc.html).
[ASTRA's FBP documentation](https://astra-toolbox.com/docs/algs/FBP.html) provides
an independent reference for the algorithm's parallel geometry and filter choices.
The application uses its own NumPy/SciPy implementation and does not install or
call ASTRA.

## Invalid inputs, truncation and coverage

The projection acquisition preserves raw zero counts and records where the
unregularized logarithm is undefined. `reject` refuses a source containing these
samples. `interpolate` repairs a working copy along detector U before filtering;
internal gaps use linear interpolation and endpoint gaps use the nearest valid
value. Any affected row with fewer than two valid values is rejected. The
reconstruction metadata reports the number of replaced samples and affected
rows. Original counts, logarithms and masks remain byte-identical.

The `coverage` output is the fraction of acquired angular views whose physical
detector field covers a point. It measures geometric support, not confidence,
uncertainty, corrected input validity or experimental accuracy. Where coverage
is below `1 - 1e-6`, attenuation is a **masked zero placeholder**. The viewer
distinguishes partial and absent support; these values must not be interpreted
as air or quantitative attenuation. A partial backprojection is not renormalized
to pretend that missing angles were observed.

Detector truncation can bias the ramp filtering even where coverage equals one.
Allowing truncation is an explicit exploratory choice, not a correction. Sparse
angles, detector blur, photon noise, invalid-sample interpolation and geometry
sampling can also create artifacts. Output values carry inverse-length units,
but this does not establish calibrated material recovery on experimental data.

## Saved views and provenance

The three linked images read exact saved array slices:

- XY: `attenuation[z,:,:]`, displayed as `[y,x]`.
- XZ: `attenuation[:,y,:]`, displayed as `[z,x]`.
- YZ: `attenuation[:,:,x]`, displayed as `[z,y]`.

Sliders, map clicks and keyboard arrows update the shared physical cursor. The
viewer reports attenuation only where support is complete, permits negative
display minima, and labels independently scaled image axes. Slice navigation
and display-window changes do not create reconstruction or projection jobs.

Each derived dataset includes the public reconstruction request, a complete
immutable source-manifest snapshot, its SHA-256, the reconstruction solver and
numerical-package identity, filter settings, coordinates, warnings and checksums.
The source-manifest hash participates in the derived input identity. The worker
checks source integrity before preparation; resume refuses changed source data
or solver inputs. A completed reconstruction can be inspected and exported even
if the original source directory is unavailable, because its provenance is
embedded. The archive does not duplicate the original projection arrays.

| Array | Storage | Meaning |
| --- | --- | --- |
| `attenuation` | float32 `[z,y,x]` | Reconstructed mm⁻¹, with finite masked placeholders at incomplete support |
| `coverage` | float32 `[z,y,x]` | Geometric angular-support fraction, 0–1 |
| `x_mm`, `y_mm`, `z_mm` | float64 vectors | Global output voxel centers |

Zarr chunks contain one complete Z plane. Both arrays must pass read-back
checksums before a slice commits. Missing/incomplete chunks use NaN fill and are
not silently interpreted as zeros. Completed archives verify coordinates,
provenance, all chunks and safe filesystem paths before export.

```python
import numpy as np
import zarr

volume = zarr.open_group("extracted-ct-archive/data.zarr", mode="r")
mu = volume["attenuation"][:]
coverage = volume["coverage"][:]
supported_mu = np.where(coverage >= 1 - 1e-6, mu, np.nan)
x, y, z = (volume[name][:] for name in ("x_mm", "y_mm", "z_mm"))
```

## Resource bounds and verification

Preflight limits saved arrays and estimated numerical workspace to 512 MiB each
and backprojection work to 250 million voxel-view evaluations. A disk-backed
filtered-projection cache avoids repeatedly decoding/filtering each source view.
Estimates include that cache in disk needs and conservatively count its mapping
in numerical workspace. Runtime, interpreter and compression overhead can differ
from the estimate. Settings are never silently reduced.

Normal completion, cancellation and exceptions close the cache. Startup recovery
removes only recognized UUID cache directories with the application ownership
marker, after acquiring the dataset worker lock. Cancel during source validation
or filtering takes effect at the next reconstruction checkpoint.

Run the full suite with `uv run pytest -q`. The standalone independent numerical
experiment creates analytic continuous-ellipse projections and checks previously
unseen angles using a separate bilinear ray-integration method:

```powershell
uv run python -m tools.verify_reconstruction artifacts/my-fbp-validation
```

The output directory must be new. The tool retains its numerical data and JSON
metrics and does not use the production forward projector. See
[VERIFICATION.md](VERIFICATION.md) for executed results and their scope.

This is the first M5 CPU baseline. GPU/cone CT, iterative reconstruction,
laminography, material segmentation, calibrated experimental import and
uncertainty estimation remain future extensions. See the
[expansion plan](EXPANSION_PLAN.md).
