# Saved acoustic acquisitions

Version 0.3 introduces a local dataset catalog, background acquisition jobs, and
signed SAM RF volumes. It records a waveform at every raster location and retains
the complex-pulse magnitude as an envelope product. These are numerical synthetic
acquisitions. The third axis is **round-trip time**, not reconstructed depth.

## Acquire and inspect

1. Load a specimen and, for H100 detail, select an HBM footprint as the scan ROI.
2. Open **Saved volumes**. The acquisition workspace snapshots the current twin
   and relevant acoustic settings. Update the snapshot after editing the specimen.
3. Set raster dimensions, material depth samples, frequency, focus, pulse bandwidth,
   water standoff, record start/duration and sample rate. Read the resource estimate.
4. Start the SAM volume. A separate local process writes checked chunks while the
   browser displays progress. Cancellation takes effect at a computation checkpoint.
5. Open a completed dataset to inspect an XY time plane, X–time and Y–time sections,
   a signed RF A-scan, and a gated C-scan. Change the gate to evaluate the **saved**
   values; this does not run the forward model again.
6. Reopen the application to browse the same local catalog. Resume a cancelled or
   interrupted acquisition with its original inputs, or create a new acquisition
   when the specimen or instrument settings should change.

The original single-image preview remains available. Its legacy gate-dependent
record window is separate from the explicit saved-volume acquisition settings.
Only one volume job runs at a time. Preview propagation is unavailable while a
volume is queued/running; saved-data inspection remains available.

## Acquisition settings

| Setting | Meaning and current limits |
| --- | --- |
| `scan_nx`, `scan_ny` | Independent raster dimensions, 16–256 each; coordinates are cell centers in the selected ROI |
| `depth_samples` | 128, 256, 512 or 1024 material planes across the entire specimen depth |
| `roi_mm` | Optional `[xmin,ymin,xmax,ymax]` in global mm; minimum side 0.05 mm; the full depth and a neighboring acoustic PSF halo are retained |
| `frequency_mhz` | Gaussian-pulse carrier, 10–150 MHz |
| `fractional_bandwidth` | Positive-frequency **amplitude spectrum FWHM / center frequency**, 0.2–1.0; default 0.5 |
| `focus_mm` | Focus measured from specimen top, inside its depth; nominal F-number remains 2 |
| `water_standoff_mm` | 0–5 mm outside the specimen; contributes two-way delay **and attenuation** |
| `record_start_us` | First recorded sample, relative to the transducer reference plane |
| `record_duration_us` | 0.05–12 µs; start + duration must be at most 12 µs |
| `sample_rate_mhz` | Samples per µs, at least eight times carrier frequency and at most 2400; at most 16,384 recorded samples |
| `include_defects` | Includes or omits objects marked as synthetic defects |

The sample count is `floor(duration × sample_rate) + 1`. The last recorded sample
is at or before the requested end. The manifest records both times. Increasing
sample rate does not increase instrument bandwidth. Lateral material sampling
currently shares the stage raster; depth discretization and time sampling are
independent. Arbitrary independent lateral wave/geometry grids remain future work.

The pulse uses `sigma_t = sqrt(2 ln 2) / (pi × bandwidth × frequency)`. A bandwidth
of approximately 0.4997083337 reproduces the legacy `sigma_t = 0.75 / frequency`.
The time buffer retains contributing pulse tails outside the recorded window.
The envelope is computed from the coherently summed complex pulse after lateral
pressure blur, then cropped alongside RF. It is not recomputed by applying a
finite-window Hilbert transform to a cropped trace.

## On-disk layout and reproducibility

The default directory is `artifacts/volumes`, ignored by Git. Set `VM_DATA_ROOT`
before starting the server to use a different local directory. Run one application
instance against a catalog. Each acquisition gets a UUID and its own directory:

```text
artifacts/volumes/
  catalog.sqlite3
  <dataset UUID>/
    manifest.json
    data.zarr/
```

Dataset folders and their manifests are the portable products.
**Download Zarr archive** packages a completed
dataset's manifest and Zarr directory. The export verifies coordinate and chunk
checksums first and refuses incomplete datasets. The supplied reference-image
bytes are not included. Its identity/provisional scale metadata may appear in the
frozen twin snapshot.

| Array | Axes | Stored type | Units |
| --- | --- | --- | --- |
| `rf` | `[y,x,time]` | float32 | Relative signed pressure amplitude |
| `envelope` | `[y,x,time]` | float32 | Relative analytic-envelope amplitude |
| `x_mm` | `[x]` | float64 | mm, global specimen coordinates |
| `y_mm` | `[y]` | float64 | mm, global specimen coordinates |
| `time_us` | `[time]` | float64 | µs, transducer reference |

The manifest retains the full normalized request, material and water properties,
input hashes, solver source hashes, numerical dependency versions, coordinate
checksums, geometry/PSF/timing metadata, assumptions and completed-row registry.
Missing chunks have a NaN fill value and are never accepted as zero measurements.
Both RF and envelope must pass read-back checks before a row chunk is committed.

Resume checks the frozen inputs and current solver/material identity, verifies
committed chunks, and regenerates missing or damaged partial chunks. The solver
uses deterministic canonical tiles with neighboring context. It has no stochastic
digitizer noise in this release. A solver/material/dependency change prevents
mixed-model resume; existing completed datasets can still be read and exported.
Completed acquisitions cannot be changed through the API.

Python can read an extracted export without loading the whole cube:

```python
import json
from pathlib import Path
import zarr

path = Path("my-extracted-sam-volume")
manifest = json.loads((path / "manifest.json").read_text())
data = zarr.open_group(str(path / "data.zarr"), mode="r")
trace = data["rf"][10, 20, :]
time_us = data["time_us"][:]
xy_at_time_index_100 = data["envelope"][:, :, 100]
```

Zarr provides persistent, chunked arrays with partial reads; the format and Python
API are documented in the [Zarr array guide](https://zarr.readthedocs.io/en/stable/user-guide/arrays/).

## Processing and limits

Gate bounds are inclusive and must lie within the stored sample range. An empty
gate is rejected. `peak_envelope` selects the maximum saved envelope in the gate;
`rms_rf` computes `sqrt(mean(RF²))` over its samples. Both retain relative-amplitude
units. Gate results are read-only views, and a small memory cache avoids repeating
unchanged reductions. Raw values and acquisition provenance remain untouched.

Server-side numerical views and archive construction are serialized; cancelling
a browser fetch cannot create concurrent large decoding operations. XY/Y–time
reads also iterate over canonical row chunks to bound decompression memory.

XY and gated images retain the saved raster. X–time/Y–time display sections use
maximum envelope within time bins when there are more than 512 time samples;
the response includes exact bin edges. The displayed A-scan retains all RF samples.
Display windows affect color only. Downloaded arrays always retain full sampling.

Preflight rejects requests above 64 million material cells, 512 MiB combined
uncompressed RF/envelope/coordinates, 512 MiB estimated numerical workspace,
16,384 time samples, or the bounded RF work budget. Workspace estimates include
tile/FFT/echo buffers and an allowance, but exclude interpreter, API and compression
overhead; they are not measured process RAM. Disk checks include a 64 MiB reserve.
No requested sampling is silently reduced. Reduce raster dimensions or recording
length to reduce saved-array size. ROI controls coverage and spatial pitch; a
smaller physical ROI at a fixed raster does not reduce saved-array bytes and can
increase the work required for neighboring PSF context.

This is still a primary longitudinal echo model with nominal material properties,
approximate focus and no reverberation, refraction, shear conversion or full elastic
propagation. A recorded signal's absence cannot establish the absence of material.
Acoustic depth mapping requires a declared layered velocity/travel-time model and
is not implemented here. Multiangle X-ray stacks and CT reconstruction follow in
M4/M5 of [the expansion plan](EXPANSION_PLAN.md).
