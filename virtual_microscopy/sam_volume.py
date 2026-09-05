"""Bounded, deterministic SAM RF tiles with acquisition-independent processing.

Axes are [y, x, time]. These are synthetic pressure measurements in time, not a
reconstructed depth volume. A complex Gaussian pulse is summed coherently, then
its real part and analytic magnitude are retained together for later inspection.
"""

from dataclasses import dataclass
from math import ceil, floor, log, pi, sqrt

import numpy as np
from scipy.ndimage import gaussian_filter
from scipy.signal import fftconvolve

from .materials import WATER_ATTENUATION_DB_MM_AT_50MHZ, WATER_SOUND_SPEED_M_S
from .physics import MaterialGrid, SAM_F_NUMBER, acoustic_echoes, voxelize
from .volume_schemas import SamVolumeRequest

MODEL_VERSION = "sam-volume-0.7.0"
MAX_VOLUME_BYTES = 512 * 1024 ** 2
MAX_PEAK_BYTES = 512 * 1024 ** 2
MAX_RF_WORK_CELLS = 180_000_000
MAX_TIME_SAMPLES = 16_384
LEGACY_FRACTIONAL_BANDWIDTH = sqrt(2 * log(2)) / (0.75 * pi)


@dataclass
class PreparedSamVolume:
    request: SamVolumeRequest
    estimate: dict
    grid: MaterialGrid
    metadata: dict
    time_us: np.ndarray
    x_mm: np.ndarray
    y_mm: np.ndarray


def _validated(request):
    return request if isinstance(request, SamVolumeRequest) else SamVolumeRequest.model_validate(request)


def _layout(request):
    a = request.acquisition
    size = request.twin.size_mm
    roi = a.roi_mm or (0.0, 0.0, size[0], size[1])
    dx, dy = (roi[2] - roi[0]) / a.scan_nx, (roi[3] - roi[1]) / a.scan_ny
    lateral_fwhm = 1.02 * SAM_F_NUMBER * WATER_SOUND_SPEED_M_S / 1000 / a.frequency_mhz
    lateral_sigma = lateral_fwhm / sqrt(8 * log(2))
    halo_x, halo_y = (int(4 * lateral_sigma / d + 0.5) for d in (dx, dy))
    before_x = min(halo_x, floor(roi[0] / dx + 1e-9))
    before_y = min(halo_y, floor(roi[1] / dy + 1e-9))
    after_x = min(halo_x, floor((size[0] - roi[2]) / dx + 1e-9))
    after_y = min(halo_y, floor((size[1] - roi[3]) / dy + 1e-9))
    nx, ny = a.scan_nx + before_x + after_x, a.scan_ny + before_y + after_y
    sigma_t = sqrt(2 * log(2)) / (pi * a.fractional_bandwidth * a.frequency_mhz)
    nt = floor(a.record_duration_us * a.sample_rate_mhz + 1e-8) + 1
    half = ceil(4 * sigma_t * a.sample_rate_mhz)
    return dict(roi=roi, dx=dx, dy=dy, nx=nx, ny=ny, nt=nt,
                before_y=before_y, before_x=before_x, halo_x=halo_x, halo_y=halo_y,
                lateral_fwhm=lateral_fwhm, sigma_t=sigma_t, half=half,
                nt_work=nt + 2 * half)


def _interface_bound_map(request: SamVolumeRequest, layout: dict) -> np.ndarray:
    """Conservatively bound vertical interfaces at each padded XY sample.

    Each supported primitive is convex along z, so contributes at most two
    interval boundaries to a sampled column. Ordered overlaps can hide those
    boundaries, never create additional ones. XY boxes deliberately overbound
    sphere and cylinder occupancy. Coordinate arithmetic matches voxelize().
    """
    a, p = request.acquisition, layout
    origin = np.array([p["roi"][0] - p["before_x"] * p["dx"],
                       p["roi"][1] - p["before_y"] * p["dy"], 0.])
    pitch = np.array([p["dx"], p["dy"], request.twin.size_mm[2] / a.depth_samples])
    shape_xyz = np.array([p["nx"], p["ny"], a.depth_samples])
    upper = origin + pitch * shape_xyz
    x = origin[0] + (np.arange(p["nx"]) + .5) * pitch[0]
    y = origin[1] + (np.arange(p["ny"]) + .5) * pitch[1]
    z = (np.arange(a.depth_samples) + .5) * pitch[2]
    # At most 600 primitives yield 1200 boundaries before clipping to nz+1.
    bound = np.zeros((p["ny"], p["nx"]), dtype=np.uint16)
    for obj in request.twin.objects:
        if not a.include_defects and obj.role == "defect":
            continue
        center, extent = np.asarray(obj.center_mm), np.asarray(obj.size_mm)
        low, high = center - extent / 2, center + extent / 2
        if np.any(high <= origin) or np.any(low >= upper):
            continue
        x0, x1 = np.searchsorted(x, low[0], side="left"), np.searchsorted(x, high[0], side="right")
        y0, y1 = np.searchsorted(y, low[1], side="left"), np.searchsorted(y, high[1], side="right")
        z0, z1 = np.searchsorted(z, low[2], side="left"), np.searchsorted(z, high[2], side="right")
        if x0 < x1 and y0 < y1 and z0 < z1:
            bound[y0:y1, x0:x1] += 2
    np.minimum(bound, a.depth_samples + 1, out=bound)
    return bound


def _microfeature_sampling(request: SamVolumeRequest, layout: dict) -> tuple[list[dict], list[str]]:
    """Describe primitive extents in grid samples, without a resolution claim."""
    a, p = request.acquisition, layout
    pitch = np.array([p["dx"], p["dy"], request.twin.size_mm[2] / a.depth_samples])
    roi_low = np.array([p["roi"][0], p["roi"][1], 0.])
    roi_high = np.array([p["roi"][2], p["roi"][3], request.twin.size_mm[2]])
    grid_low = roi_low - np.array([p["before_x"] * pitch[0], p["before_y"] * pitch[1], 0.])
    grid_high = grid_low + pitch * [p["nx"], p["ny"], a.depth_samples]
    features, warnings = [], []
    for obj in request.twin.objects:
        if obj.layer_role not in ("microbump", "tsv"):
            continue
        size = np.asarray(obj.size_mm)
        low, high = np.asarray(obj.center_mm) - size / 2, np.asarray(obj.center_mm) + size / 2
        counts = size / pitch
        axes = [axis for axis, count in zip(("x", "y", "z"), counts) if count < 2]
        included = a.include_defects or obj.role != "defect"
        overlap = bool(np.all(high > grid_low) and np.all(low < grid_high))
        features.append({"id": obj.id, "assembly_id": obj.assembly_id,
            "layer_role": obj.layer_role, "role": obj.role, "shape": obj.shape,
            "size_um": (size * 1000).tolist(), "samples_xyz": counts.tolist(),
            "included": included, "intersects_roi": bool(np.all(high > roi_low) and np.all(low < roi_high)),
            "intersects_geometry_domain": overlap, "undersampled_axes": axes})
        if included and overlap and axes:
            detail = ", ".join(f"{axis}={count:.3g}" for axis, count in zip(("x", "y", "z"), counts) if count < 2)
            warnings.append(f"Microfeature {obj.id} ({obj.layer_role}, {obj.role}) has fewer than two samples "
                            f"across {detail}; sampled occupancy can be inaccurate or disappear. Sampling is not acoustic resolution.")
    return features, warnings


def estimate_sam(request: SamVolumeRequest | dict) -> dict:
    """Reject impractical requests before allocating geometry, echoes or RF cubes.

The peak is a conservative numerical working estimate, not a process RSS promise:
it includes the full byte label grid, twice the maximum interface echo storage,
complex FFT/filter workspace, output copies and an additional 16 MiB allowance.
It excludes interpreter, HTTP and disk compression implementation overhead.
"""
    request = _validated(request)
    if getattr(request.acquisition, "path_model", "voxel_centers_v1") == "continuous_columns_v1":
        from .continuous_sam import estimate_continuous_sam
        return estimate_continuous_sam(request)
    a, p = request.acquisition, _layout(request)
    nt, nx, ny = p["nt"], p["nx"], p["ny"]
    if nt > MAX_TIME_SAMPLES:
        raise ValueError(f"Recording exceeds {MAX_TIME_SAMPLES} RF samples. Shorten duration or lower sample rate within its validated minimum.")
    grid_cells = nx * ny * a.depth_samples
    if grid_cells > 64_000_000:
        raise ValueError("Geometry exceeds 64 million cells including acoustic halo. Reduce raster or depth samples, or enlarge the ROI.")
    rf_bytes = a.scan_ny * a.scan_nx * nt * 4
    coordinate_bytes = (a.scan_nx + a.scan_ny + nt) * 8
    total_bytes = 2 * rf_bytes + coordinate_bytes
    if total_bytes > MAX_VOLUME_BYTES:
        raise ValueError("RF and envelope exceed the 512 MiB saved-volume budget. Reduce raster size, duration or sample rate.")
    objects = [o for o in request.twin.objects if a.include_defects or o.role != "defect"]
    interface_bound = _interface_bound_map(request, p)
    # Include the bound map and its transient coordinate vectors in the peak
    # allowance even though they are released before geometry is allocated.
    bound_workspace = interface_bound.nbytes + (nx + ny + a.depth_samples) * 8
    tile_rows = 8
    while True:
        row_counts, tile_interfaces = [], []
        for start in range(0, a.scan_ny, tile_rows):
            low = max(0, p["before_y"] + start - p["halo_y"])
            high = min(ny, p["before_y"] + min(start + tile_rows, a.scan_ny) + p["halo_y"])
            row_counts.append(high - low)
            tile_interfaces.append(int(interface_bound[low:high].sum(dtype=np.uint64)))
        max_tile_cells = max(row_counts) * nx * p["nt_work"]
        echo_workspace = max(tile_interfaces) * 160
        # 96 B/work cell covers FFT length padding, complex inputs/outputs and
        # filtering temporaries, with separate returned RF/envelope allocations.
        peak = (grid_cells + bound_workspace + max_tile_cells * 96 + echo_workspace +
                tile_rows * a.scan_nx * nt * 8 + coordinate_bytes + 16 * 1024 ** 2)
        if peak <= MAX_PEAK_BYTES or tile_rows == 1:
            break
        tile_rows //= 2
    if peak > MAX_PEAK_BYTES:
        raise ValueError("Estimated SAM working memory exceeds 512 MiB even with one-row tiles. Reduce raster size, duration, sample rate or halo overhead.")
    work_cells = sum(row_counts) * nx * p["nt_work"]
    if work_cells > MAX_RF_WORK_CELLS:
        raise ValueError("SAM acquisition exceeds the local RF computation budget including tile halos. Reduce raster, duration or sample rate, or enlarge a small ROI.")
    features, warnings = _microfeature_sampling(request, p)
    if max(p["dx"], p["dy"]) > p["lateral_fwhm"] / 2:
        warnings.append("The scan pitch undersamples the modeled focal spot; scan pitch is not acoustic resolution.")
    return {
        "schema_version": 1, "kind": "sam_rf_volume", "model_version": MODEL_VERSION,
        "shape": [a.scan_ny, a.scan_nx, nt], "axis_order": ["y", "x", "time"],
        "dtype": "float32", "time_samples": nt, "tile_rows": tile_rows,
        "chunks": [tile_rows, a.scan_nx, nt], "rf_bytes": rf_bytes,
        "envelope_bytes": rf_bytes, "coordinate_bytes": coordinate_bytes,
        "total_bytes": total_bytes, "estimated_peak_bytes": peak,
        "rf_work_cells": work_cells, "geometry_cells": grid_cells,
        "primitive_count": len(request.twin.objects), "included_primitive_count": len(objects),
        "interface_bound_map_bytes": int(interface_bound.nbytes),
        "interface_bound_workspace_bytes": int(bound_workspace),
        "maximum_column_interfaces": int(interface_bound.max()),
        "maximum_tile_interfaces": max(tile_interfaces), "echo_workspace_bytes": echo_workspace,
        "interface_bound_definition": "At most two z boundaries per included primitive covering each sampled XY bounding box, capped at depth_samples+1; summed over each canonical tile and its complete acoustic halo.",
        "microfeature_sampling": features,
        "microfeature_sampling_definition": "Compiled primitive extents divided by geometry pitch in x,y,z; these ratios are sampling, not measured resolution or a guarantee of occupied voxel centers.",
        "grid_shape": [ny, nx, a.depth_samples],
        "grid_origin_mm": [p["roi"][0] - p["before_x"] * p["dx"],
                           p["roi"][1] - p["before_y"] * p["dy"], 0.0],
        "extent_mm": [p["roi"][0], p["roi"][2], p["roi"][1], p["roi"][3]],
        "pixel_pitch_um": [p["dx"] * 1000, p["dy"] * 1000],
        "voxel_depth_um": request.twin.size_mm[2] / a.depth_samples * 1000,
        "time_start_us": a.record_start_us,
        "time_end_us": a.record_start_us + (nt - 1) / a.sample_rate_mhz,
        "requested_record_end_us": a.record_start_us + a.record_duration_us,
        "rf_sample_interval_us": 1 / a.sample_rate_mhz,
        "acoustic_lateral_fwhm_mm": p["lateral_fwhm"],
        "pulse_sigma_us": p["sigma_t"],
        "pulse_envelope_fwhm_us": sqrt(8 * log(2)) * p["sigma_t"],
        "water_round_trip_delay_us": 2 * a.water_standoff_mm / (WATER_SOUND_SPEED_M_S / 1000),
        "warnings": warnings,
    }


def prepare_sam(request: SamVolumeRequest | dict) -> PreparedSamVolume:
    request = _validated(request)
    if getattr(request.acquisition, "path_model", "voxel_centers_v1") == "continuous_columns_v1":
        from .continuous_sam import prepare_continuous_sam
        return prepare_continuous_sam(request)
    estimate = estimate_sam(request)
    a, p = request.acquisition, _layout(request)
    grid = voxelize(request.twin.model_dump(mode="json"), (a.scan_nx, a.scan_ny),
                    a.include_defects, roi_mm=a.roi_mm, depth_samples=a.depth_samples,
                    halo_pixels=(p["halo_x"], p["halo_y"]))
    time = a.record_start_us + np.arange(p["nt"], dtype=np.float64) / a.sample_rate_mhz
    x = p["roi"][0] + (np.arange(a.scan_nx, dtype=np.float64) + 0.5) * p["dx"]
    y = p["roi"][1] + (np.arange(a.scan_ny, dtype=np.float64) + 0.5) * p["dy"]
    metadata = dict(estimate)
    metadata.update({
        "warnings": grid.warnings + estimate["warnings"],
        "coordinate_units": {"x": "mm", "y": "mm", "time": "us"},
        "rf_unit": "relative signed pressure", "envelope_unit": "relative echo amplitude",
        "envelope_processing": "absolute value of the coherently summed complex analytic Gaussian pulse, after lateral complex-pressure PSF; computed before recording-window crop",
        "fractional_bandwidth_definition": "full width at half maximum of the positive-frequency Gaussian amplitude spectrum divided by center frequency",
        "time_zero": "transducer reference plane; specimen-top arrival includes two-way water standoff delay",
        "assumptions": [
            "Synthetic reduced-order forward model; no experimental calibration or validation.",
            "Stored axes are y, x, time. Echo time is not geometric depth; layered velocities and interfaces prevent a universal time-to-depth conversion.",
            "Scan coordinates and material lateral sampling share the same raster. Voxel depth is independent. Sample pitch is not physical resolution.",
            "ROI retains complete specimen depth and neighboring Gaussian PSF context; geometry is voxel-center sampled with later primitives taking precedence.",
            "Primary normal-incidence longitudinal pressure echoes with signed reflection, round-trip transmission and frequency-dependent attenuation; no reverberation, refraction, shear conversion or full elastic wave propagation.",
            "Interior ambient is water; explicit air voids remain air. Water standoff adds round-trip propagation delay and attenuation; focus remains measured from specimen top.",
            "Nominal acoustic material inputs, uncalibrated loss laws, F-number 2 focus model and frequency-dependent Gaussian lateral PSF.",
            "Complex Gaussian pulse truncated at four temporal sigma; phase-aware fractional-delay deposition. At least eight samples per carrier period, satisfying Nyquist through four spectral sigma at the maximum supported bandwidth.",
            "Coherent complex pressure is blurred before storing signed RF and analytic envelope. Stored envelope retains processing lineage and avoids a new finite-window Hilbert transform during re-gating.",
            "Recording includes its start and every sample at or before its requested end. Pulses centered outside the recording can contribute tails; time gates are subsequent processing operations.",
            "Primary echoes below 1e-8 before external water-standoff loss are omitted. Echoes outside the recording pulse support are excluded.",
        ],
    })
    return PreparedSamVolume(request, estimate, grid, metadata, time, x, y)


def iter_sam_tiles(prepared: PreparedSamVolume, start_row: int = 0):
    """Yield canonical contiguous float32 [tile-y,x,time] arrays for resume.

    Each tile includes the exact scipy Gaussian support from its neighboring
    geometry rows. No whole-volume echo list or complex RF cube is allocated.
    """
    if getattr(prepared.request.acquisition, "path_model", "voxel_centers_v1") == "continuous_columns_v1":
        from .continuous_sam import iter_continuous_sam_tiles
        yield from iter_continuous_sam_tiles(prepared, start_row)
        return
    a, grid = prepared.request.acquisition, prepared.grid
    p = _layout(prepared.request)
    chunk = prepared.estimate["tile_rows"]
    if not isinstance(start_row, int) or start_row < 0 or start_row > a.scan_ny or (start_row != a.scan_ny and start_row % chunk):
        raise ValueError("Resume row must be a canonical tile boundary within the scan.")
    dt, half, nt = 1 / a.sample_rate_mhz, p["half"], p["nt"]
    pulse_t = np.arange(-half, half + 1) * dt
    wavelet = (np.exp(-0.5 * (pulse_t / p["sigma_t"]) ** 2) *
               np.exp(2j * np.pi * a.frequency_mhz * pulse_t)).astype(np.complex64)
    sigma_mm = p["lateral_fwhm"] / sqrt(8 * log(2))
    sigma_x, sigma_y = sigma_mm / p["dx"], sigma_mm / p["dy"]
    standoff_delay = prepared.estimate["water_round_trip_delay_us"]
    water_loss = WATER_ATTENUATION_DB_MM_AT_50MHZ * (a.frequency_mhz / 50) ** 2
    standoff_gain = np.exp(-2 * np.log(10) / 20 * water_loss * a.water_standoff_mm)
    sy, sx = grid.image_slices
    for start in range(start_row, a.scan_ny, chunk):
        stop = min(start + chunk, a.scan_ny)
        lo, hi = max(0, sy.start + start - p["halo_y"]), min(grid.labels.shape[0], sy.start + stop + p["halo_y"])
        tile_grid = MaterialGrid(grid.labels[lo:hi],
                                 grid.pitch_mm * [grid.labels.shape[1], hi - lo, a.depth_samples],
                                 grid.pitch_mm, [], grid.origin_mm + [0, lo * p["dy"], 0])
        echoes = acoustic_echoes(tile_grid, a.frequency_mhz, a.focus_mm)
        echoes.times_us += standoff_delay
        echoes.amplitudes *= standoff_gain
        cube = np.zeros((hi - lo, grid.labels.shape[1], p["nt_work"]), dtype=np.complex64)
        # Include tails from echoes on both sides of the independent record.
        # Fractional deposition occupies two adjacent impulse bins. One extra
        # bin on either side retains the interpolation's final truncated tail.
        selected = ((echoes.times_us >= prepared.time_us[0] - (half + 1) * dt) &
                    (echoes.times_us <= prepared.time_us[-1] + (half + 1) * dt))
        rows, cols = echoes.rows[selected], echoes.cols[selected]
        position = (echoes.times_us[selected] - a.record_start_us) / dt + half
        bins = np.floor(position).astype(np.int64)
        fraction, amp = position - bins, echoes.amplitudes[selected]
        w0 = amp * (1 - fraction) * np.exp(-2j * np.pi * a.frequency_mhz * dt * fraction)
        valid = (bins >= 0) & (bins < p["nt_work"])
        np.add.at(cube, (rows[valid], cols[valid], bins[valid]), w0[valid])
        w1 = amp * fraction * np.exp(2j * np.pi * a.frequency_mhz * dt * (1 - fraction))
        valid = (bins + 1 >= 0) & (bins + 1 < p["nt_work"])
        np.add.at(cube, (rows[valid], cols[valid], bins[valid] + 1), w1[valid])
        signal = fftconvolve(cube, wavelet[None, None, :], mode="same", axes=2)
        del cube, echoes
        signal = gaussian_filter(signal, (sigma_y, sigma_x, 0), mode="nearest")
        signal = signal[sy.start + start - lo:sy.start + stop - lo, sx, half:half + nt]
        rf = np.ascontiguousarray(signal.real, dtype=np.float32)
        envelope = np.ascontiguousarray(np.abs(signal), dtype=np.float32)
        yield start, stop, rf, envelope
