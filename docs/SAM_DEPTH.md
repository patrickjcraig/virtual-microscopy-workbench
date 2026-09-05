# SAM depth estimates from saved acoustic recordings

Version 0.6 adds a derived spatial volume of signed RF and echo envelope using
an explicit one-dimensional velocity model. The source remains a saved
`sam_rf_volume` with axes `[y,x,time]`; the new dataset has axes `[z,y,x]`.
This is time-to-depth resampling, not recovery of acoustic impedance, material
identity, or a full wavefield.

## Workflow

Open **SAM depth**, select a completed saved acoustic recording, then specify
the depth range, number of depth samples, surface-time reference and velocity
model. Inspect the estimate and support warnings before starting the mapping.
The current preview twin does not change the saved source or its scan footprint.
X/Y coordinates are retained exactly from that source; this operation does not
add lateral scan information.

Choose a homogeneous speed for an exploratory estimate, or enter a table of
layer-bottom depths and longitudinal speeds. Layers start at depth zero and
continue in order without gaps. This is one laterally uniform table applied to
every saved A-scan; a package with different materials along neighboring rays
does not generally satisfy that assumption.

The default 5,000 m/s is a declared illustrative assumption, not an H100 effective
velocity or an instrument calibration. The interface records whether the model
is user-assumed, supplied as calibrated, or based on synthetic truth, together
with a note. A user-supplied calibration label is provenance, not a certification
by the application. The mapping never silently constructs a velocity profile
from the source twin's primitive material labels.

Completed depth estimates have linked XY/XZ/YZ views. Select signed RF or saved
analytic envelope, move the shared physical cursor, adjust display limits, and
inspect the corresponding original recording time. Display operations read the
saved derived volume and do not rerun propagation or depth mapping. Gray masks
identify unsupported samples; they are not zero-amplitude measurements.

## Time reference and mapping

The saved source records time at the transducer reference plane. For the default
surface reference, the mapper uses the frozen source water round-trip delay.
Alternatively, supply an explicit specimen-surface arrival time on that same
saved time axis. Do not subtract the recording start again; the stored time
coordinate already includes it. Changing the surface time shifts the depth map
and must be recorded as part of the processing request.

For a homogeneous model, the mapping is:

```text
time_us(z) = surface_time_us + 2 * z_mm / (sound_speed_m_s / 1000)
```

For a layer table, sum the two-way travel time through each preceding layer and
the traversed part of the current layer. Layer coordinates refer to specimen
depth, independent of a requested cropped output interval. Thus an output range
starting at 1 mm still includes propagation through the first millimeter.

This follows the pulse-echo distance/time relationship in
[Evident's thickness-gauging theory](https://ims.evidentscientific.com/en/learn/ndt-tutorials/thickness-gauge/introduction/operation).
Their [velocity and zero-calibration guide](https://ims.evidentscientific.com/en/learn/ndt-tutorials/thickness-gauge/zero-calibration)
explains why material velocity and non-specimen delays require separate treatment.
These references support the timing convention; they do not validate the
workbench's velocity assumptions or synthetic H100 images.

At each output voxel-center depth, linearly interpolate both the stored signed
RF and the stored analytic envelope at the mapped time. The envelope is not
replaced by absolute RF, and neither quantity receives per-volume normalization.
This preserves their separate meanings but is not an ideal band-limited
resampler. Coarse output depth sampling can undersample the RF oscillation.

## Support and interpretation

A depth is supported only when it lies within the declared velocity model and
its mapped time lies between actual recorded time sample centers. There is no
extrapolation beyond the recorded interval. A layer table that ends before the
requested output depth leaves deeper samples masked rather than extending the
last speed. Original X/Y positions remain the source's measured or synthetic
scan positions.

Unsupported RF/envelope values are finite zero placeholders paired with a zero
validity mask. The viewer distinguishes an undefined model time from a defined
time outside the recording. The validity mask describes numerical support;
it does not establish that the selected velocity profile is physically correct.

Neither output Z spacing nor source time sampling establishes acoustic axial
resolution. Bandwidth, focus, attenuation, interface response, material-path
uncertainty and velocity errors remain relevant. Mapping the existing primary-
echo simulator does not add reverberation, refraction, shear conversion,
diffraction correction, attenuation compensation, or a spatially varying
velocity inversion.

## Storage, controls and provenance

| Field or array | Meaning |
| --- | --- |
| `source_dataset_id` | Completed saved acoustic source UUID |
| `nz` | Number of output depth samples, 16–1,024 |
| `z_min_mm`, `z_max_mm` | Ordered output depth bounds inside the source specimen |
| `surface_reference` | `source_water_delay` or `explicit` |
| `surface_time_us` | Explicit surface arrival on the saved recording axis, when selected |
| `velocity_model` | `homogeneous` or `layered` |
| `sound_speed_m_s` | Assumed homogeneous longitudinal speed |
| `layers` | Ordered `{end_depth_mm, sound_speed_m_s}` records for a layered model |
| `model_evidence`, `model_note` | Declared provenance and explanation of the velocity model |
| `rf` | float32 `[z,y,x]`, relative signed pressure |
| `envelope` | float32 `[z,y,x]`, relative echo amplitude |
| `valid_mask` | float32 `[z,y,x]`, binary numerical support |
| `x_mm`, `y_mm`, `z_mm` | float64 global spatial sample centers |
| `travel_time_us` | float64 `[z]`, mapped source times; zero placeholder outside model support |

`metadata.model_depth_valid` marks where `travel_time_us` is meaningful. Each
derived manifest retains the complete frozen source manifest, its hash, the
mapping request, numerical implementation identity and array checksums. Raw
RF/envelope/time arrays are preserved. Completed depth estimates remain
inspectable and exportable if the original source directory is unavailable;
resuming incomplete work requires the unchanged original source.

Jobs share the local queue with SAM, X-ray and CT. They commit one Z plane at a
time and can cancel/resume. Preflight reports uncompressed output bytes and
numerical workspace with explicit caps. Values are never silently downsampled
to fit those caps. Download the Zarr ZIP to retain all derived arrays and the
source provenance snapshot; it does not duplicate the original RF acquisition.

```python
import numpy as np
import zarr

data = zarr.open_group("extracted-depth-archive/data.zarr", mode="r")
rf = data["rf"][:]
valid = data["valid_mask"][:] == 1
supported_rf = np.where(valid, rf, np.nan)
x, y, z = (data[name][:] for name in ("x_mm", "y_mm", "z_mm"))
```

See [VERIFICATION.md](VERIFICATION.md) for executed checks and
[EXPANSION_PLAN.md](EXPANSION_PLAN.md) for the next fidelity increments.

The independent analytic reflector experiment also measures sensitivity to an
incorrect speed and surface-time reference. Run it into a new output directory:

```powershell
uv run python -m tools.verify_depth_mapping artifacts/my-depth-validation
```

Its report and numerical profiles are synthetic checks with declared reflector
positions. They do not use the production acoustic forward simulator.
