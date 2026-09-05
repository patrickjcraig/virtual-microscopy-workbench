"""Opt-in continuous material-column SAM with bounded transient paths and RF."""

from dataclasses import dataclass
from math import ceil, floor, log, pi, sqrt

import numpy as np
from scipy.ndimage import gaussian_filter
from scipy.signal import fftconvolve

from .materials import WATER_ATTENUATION_DB_MM_AT_50MHZ, WATER_SOUND_SPEED_M_S
from .physics import SAM_F_NUMBER

MODEL_VERSION = "sam-continuous-columns-0.8.0"
PATH_MODEL = "continuous_columns_v1"
PATH_CONTRACT_VERSION = "ordered-column-paths-1"
MAX_COLUMNS = 500_000
MAX_PEAK_BYTES = 512 * 1024**2
MAX_VOLUME_BYTES = 512 * 1024**2
MAX_RF_WORK_CELLS = 180_000_000
MAX_CANDIDATE_TESTS = 50_000_000
MAX_EVENT_WORK = 250_000_000
MAX_TIME_SAMPLES = 16_384
PREPASS_ROWS = 8


@dataclass
class PreparedContinuousSam:
    request: object
    estimate: dict
    metadata: dict
    time_us: np.ndarray
    x_mm: np.ndarray
    y_mm: np.ndarray
    layout: dict
    padded_x_mm: np.ndarray
    padded_y_mm: np.ndarray
    twin: dict
    grid: None = None


def column_layout(twin, nx, ny, roi, frequency_mhz, detector_fwhm_mm=0.):
    """Same global center/halo convention as the voxel acquisition, without Z."""
    size = twin["size_mm"]
    roi = list(roi or [0., 0., size[0], size[1]])
    dx, dy = (roi[2]-roi[0])/nx, (roi[3]-roi[1])/ny
    fwhm = 1.02 * SAM_F_NUMBER * WATER_SOUND_SPEED_M_S / 1000 / frequency_mhz
    acoustic_sigma = fwhm / sqrt(8*log(2))
    context_sigma = max(fwhm, detector_fwhm_mm) / sqrt(8*log(2))
    hx, hy = (int(4*context_sigma/d+.5) for d in (dx, dy))
    bx, by = min(hx, floor(roi[0]/dx+1e-9)), min(hy, floor(roi[1]/dy+1e-9))
    ax, ay = min(hx, floor((size[0]-roi[2])/dx+1e-9)), min(hy, floor((size[1]-roi[3])/dy+1e-9))
    px, py = nx+bx+ax, ny+by+ay
    if px*py > MAX_COLUMNS:
        raise ValueError("Continuous paths exceed 500,000 padded XY columns. Reduce raster size or enlarge the ROI to reduce halo overhead.")
    origin = [roi[0]-bx*dx, roi[1]-by*dy, 0.]
    return {"roi": roi, "scan_nx": nx, "scan_ny": ny, "nx": px, "ny": py,
            "dx": dx, "dy": dy, "before_x": bx, "before_y": by, "origin": origin,
            "halo_x": hx, "halo_y": hy, "acoustic_halo_y": int(4*acoustic_sigma/dy+.5),
            "lateral_fwhm": fwhm, "sigma_x": acoustic_sigma/dx, "sigma_y": acoustic_sigma/dy}


def padded_coordinates(p):
    return (p["origin"][0] + (np.arange(p["nx"], dtype=np.float64)+.5)*p["dx"],
            p["origin"][1] + (np.arange(p["ny"], dtype=np.float64)+.5)*p["dy"])


def tile_ranges(p, chunk, probe_row=None):
    starts = range(p["before_y"], p["before_y"]+p["scan_ny"], chunk) if probe_row is None else [probe_row]
    for start in starts:
        stop = min(start+chunk, p["before_y"]+p["scan_ny"]) if probe_row is None else start+1
        yield start, stop, max(0, start-p["acoustic_halo_y"]), min(p["ny"], stop+p["acoustic_halo_y"])


def bound_context(twin, p, include_defects):
    from .column_paths import column_interface_bounds
    x, y = padded_coordinates(p)
    bounds = column_interface_bounds(twin, x, y, include_defects)
    indices = np.arange(int(bounds.max())+1, dtype=np.uint64)
    scores = indices * (1+np.ceil(np.log2(np.maximum(2, indices))).astype(np.uint64))
    event_work = scores[bounds]
    objects = sum(include_defects or obj.get("role", "structure") != "defect" for obj in twin["objects"])
    base = bounds.nbytes + event_work.nbytes + x.nbytes + y.nbytes + 16*1024**2
    return x, y, bounds, event_work, objects, base


def path_phase_bytes(interfaces, columns):
    # Typed paths/sorted-event scratch, live echo conversion, and offsets.
    return 32*(interfaces+columns) + 64*interfaces + 160*interfaces + 8*(columns+1)


def resource_plan(p, context, chunk, nt, half, *, probe_row=None, full_prepass=True,
                  prepass_only=False, extra_base_bytes=0):
    _, _, bounds, event_work, objects, base = context
    base += extra_base_bytes
    ranges = [] if prepass_only else list(tile_ranges(p, chunk, probe_row))
    candidate_tests = objects*p["nx"]*p["ny"] if full_prepass else 0
    event_units = int(event_work.sum(dtype=np.uint64)) if full_prepass else 0
    peak, max_path, max_echo, rf_work, max_cells = base, 0, 0, 0, 0
    for lo in range(0, p["ny"], PREPASS_ROWS) if full_prepass else ():
        hi = min(p["ny"], lo+PREPASS_ROWS)
        count = int(bounds[lo:hi].sum(dtype=np.uint64))
        path_bytes = path_phase_bytes(count, (hi-lo)*p["nx"])
        peak = max(peak, base+path_bytes)
        max_path, max_echo = max(max_path, path_bytes), max(max_echo, 160*count)
    max_interfaces = 0
    for start, stop, lo, hi in ranges:
        columns = (hi-lo)*p["nx"]
        interfaces = int(bounds[lo:hi].sum(dtype=np.uint64))
        cells = columns*(nt+2*half)
        candidate_tests += objects*columns
        event_units += int(event_work[lo:hi].sum(dtype=np.uint64))
        path_bytes = path_phase_bytes(interfaces, columns)
        # Paths are released before RF/FFT allocation. The echo arrays remain.
        rf_bytes = 160*interfaces + 96*cells + (stop-start)*p["scan_nx"]*nt*8
        peak = max(peak, base+max(path_bytes, rf_bytes))
        max_path, max_echo = max(max_path, path_bytes), max(max_echo, 160*interfaces)
        rf_work += cells
        max_cells, max_interfaces = max(max_cells, cells), max(max_interfaces, interfaces)
    return {"tile_rows": chunk, "estimated_peak_bytes": int(peak),
            "estimated_path_workspace_bytes": int(max_path), "echo_workspace_bytes": max_echo,
            "maximum_tile_interfaces": max_interfaces, "rf_work_cells": rf_work,
            "rf_max_tile_work_cells": max_cells, "path_candidate_tests": candidate_tests,
            "path_event_work_units": event_units}


def enforce_resources(plan, *, preview=False):
    if plan["estimated_peak_bytes"] > MAX_PEAK_BYTES:
        raise ValueError("Continuous-path numerical workspace exceeds 512 MiB. Reduce raster/record length or explicitly enlarge the ROI; settings were not changed.")
    if plan["rf_work_cells"] > MAX_RF_WORK_CELLS or (preview and plan["rf_max_tile_work_cells"] > 8_000_000):
        raise ValueError("Continuous-path acquisition exceeds the local RF computation budget including full halos. Reduce raster/record length or enlarge the ROI.")
    if plan["path_candidate_tests"] > MAX_CANDIDATE_TESTS:
        raise ValueError("Continuous paths exceed 50 million primitive-column candidate tests. Reduce raster, primitive count or halo overhead.")
    if plan["path_event_work_units"] > MAX_EVENT_WORK:
        raise ValueError("Continuous paths exceed 250 million event-work units. Reduce intersecting primitive count, raster or halo overhead.")


def feature_sampling(twin, p, include_defects):
    features, warnings = [], []
    origin, high = np.asarray(p["origin"]), np.array([p["origin"][0]+p["nx"]*p["dx"], p["origin"][1]+p["ny"]*p["dy"], twin["size_mm"][2]])
    for obj in twin["objects"]:
        if obj.get("layer_role") not in ("microbump", "tsv"):
            continue
        size, center = np.asarray(obj["size_mm"]), np.asarray(obj["center_mm"])
        low, upper = center-size/2, center+size/2
        counts = [float(size[0]/p["dx"]), float(size[1]/p["dy"]), None]
        axes = [axis for axis, count in zip(("x", "y"), counts[:2]) if count < 2]
        included = include_defects or obj.get("role", "structure") != "defect"
        overlap = bool(np.all(upper > origin) and np.all(low < high))
        features.append({"id": obj["id"], "assembly_id": obj.get("assembly_id"),
            "layer_role": obj["layer_role"], "role": obj.get("role", "structure"), "shape": obj["shape"],
            "size_um": (size*1000).tolist(), "samples_xyz": counts, "undersampled_axes": axes,
            "included": included, "intersects_geometry_domain": overlap,
            "intersects_roi": bool(upper[0] > p["roi"][0] and low[0] < p["roi"][2] and upper[1] > p["roi"][1] and low[1] < p["roi"][3])})
        if included and overlap and axes:
            warnings.append(f"Microfeature {obj['id']} has fewer than two lateral samples across {', '.join(axes)}; continuous vertical paths do not guarantee lateral representation or acoustic resolution.")
    if max(p["dx"], p["dy"]) > p["lateral_fwhm"]/2:
        warnings.append("The scan pitch undersamples the modeled focal spot; pitch is not acoustic resolution.")
    warnings.append("Continuous normal-incidence paths use authored vertical interfaces; depth_samples is inactive. Lateral and RF sampling remain finite.")
    return features, warnings


def base_metadata(twin, p, context, include_defects):
    _, _, bounds, _, objects, _ = context
    features, warnings = feature_sampling(twin, p, include_defects)
    return {"path_model": PATH_MODEL, "path_contract_version": PATH_CONTRACT_VERSION,
        "depth_samples_used": False, "grid_shape": None, "geometry_cells": 0,
        "padded_shape_yx": [p["ny"], p["nx"]], "column_count": p["ny"]*p["nx"],
        "grid_origin_mm": p["origin"], "extent_mm": [p["roi"][0], p["roi"][2], p["roi"][1], p["roi"][3]],
        "pixel_pitch_um": [p["dx"]*1000, p["dy"]*1000], "voxel_depth_um": None,
        "primitive_count": len(twin["objects"]), "included_primitive_count": objects,
        "maximum_column_interfaces": int(bounds.max()), "interface_bound_map_bytes": bounds.nbytes,
        "path_segment_bound": int(bounds.sum(dtype=np.uint64))+bounds.size,
        "interface_bound_definition": "Two endpoints per included primitive with sampled XY bounding-box coverage and positive continuous Z overlap; no voxel-depth cap; full tile halos and preparation prepass counted.",
        "microfeature_sampling": features, "warnings": warnings,
        "microfeature_sampling_definition": "Lateral extents divided by XY pitch. The null Z count is inactive for continuous paths; geometric inclusion is not physical resolution.",
        "workspace_definition": "Maximum of live path/event/echo preparation and RF/FFT/echo synthesis, plus persistent bounds, coordinates, output tiles and a 16 MiB reserve; not process RSS."}


def estimate_continuous_sam(request):
    a = request.acquisition
    twin = request.twin.model_dump(mode="json")
    p = column_layout(twin, a.scan_nx, a.scan_ny, a.roi_mm, a.frequency_mhz)
    nt = floor(a.record_duration_us*a.sample_rate_mhz+1e-8)+1
    if nt > MAX_TIME_SAMPLES:
        raise ValueError("Recording exceeds 16384 RF samples. Shorten duration or reduce sample rate within its validated minimum.")
    sigma = sqrt(2*log(2))/(pi*a.fractional_bandwidth*a.frequency_mhz)
    half = ceil(4*sigma*a.sample_rate_mhz)
    rf_bytes, coordinate_bytes = a.scan_nx*a.scan_ny*nt*4, (a.scan_nx+a.scan_ny+nt)*8
    total = 2*rf_bytes+coordinate_bytes
    if total > MAX_VOLUME_BYTES:
        raise ValueError("RF and envelope exceed the 512 MiB saved-volume budget. Reduce raster, duration or sample rate.")
    context = bound_context(twin, p, a.include_defects)
    last_error = None
    for chunk in (8, 4, 2, 1):
        plan = resource_plan(p, context, chunk, nt, half, extra_base_bytes=coordinate_bytes)
        try:
            enforce_resources(plan)
            break
        except ValueError as exc:
            last_error = exc
    else:
        raise last_error
    return {**base_metadata(twin, p, context, a.include_defects), **plan,
        "schema_version": 1, "kind": "sam_rf_volume", "model_version": MODEL_VERSION,
        "shape": [a.scan_ny, a.scan_nx, nt], "axis_order": ["y", "x", "time"], "dtype": "float32",
        "time_samples": nt, "chunks": [plan["tile_rows"], a.scan_nx, nt], "rf_bytes": rf_bytes,
        "envelope_bytes": rf_bytes, "coordinate_bytes": coordinate_bytes, "total_bytes": total,
        "estimated_temporary_bytes": 0, "time_start_us": a.record_start_us,
        "time_end_us": a.record_start_us+(nt-1)/a.sample_rate_mhz,
        "requested_record_end_us": a.record_start_us+a.record_duration_us,
        "rf_sample_interval_us": 1/a.sample_rate_mhz, "acoustic_lateral_fwhm_mm": p["lateral_fwhm"],
        "pulse_sigma_us": sigma, "pulse_envelope_fwhm_us": sqrt(8*log(2))*sigma,
        "water_round_trip_delay_us": 2*a.water_standoff_mm/(WATER_SOUND_SPEED_M_S/1000)}


def path_prepass(twin, x, y, include_defects, *, frequency_mhz=None, focus_mm=None, energy_kev=None):
    from .column_paths import build_column_paths, column_acoustic_echoes, column_xray_integrals
    sum_keys = ("columns", "segment_count", "interface_count", "primitive_intersections",
                "primitive_event_count", "adjusted_endpoint_count", "candidate_tests",
                "event_bound", "event_work_bound", "segment_bound")
    diagnostics = {"contract_version": PATH_CONTRACT_VERSION,
                   **{name: 0 for name in sum_keys},
                   "max_endpoint_adjustment_mm": 0., "maximum_allocated_path_bytes": 0,
                   "maximum_estimated_path_workspace_bytes": 0,
                   "scope": "Non-overlapping full padded XY preparation pass; acquisition work estimates also count repeated synthesis halos."}
    latest = 0.
    optical_depth = np.empty((len(y), len(x)), dtype=np.float64) if energy_kev is not None else None
    for lo in range(0, len(y), PREPASS_ROWS):
        hi = min(lo+PREPASS_ROWS, len(y))
        paths = build_column_paths(twin, x, y[lo:hi], include_defects)
        diagnostic = paths.diagnostics
        for name in sum_keys:
            diagnostics[name] += int(diagnostic.get(name, 0))
        diagnostics["max_endpoint_adjustment_mm"] = max(diagnostics["max_endpoint_adjustment_mm"], float(diagnostic.get("max_endpoint_adjustment_mm", 0.)))
        diagnostics["maximum_allocated_path_bytes"] = max(diagnostics["maximum_allocated_path_bytes"], int(diagnostic.get("allocated_path_bytes", 0)))
        diagnostics["maximum_estimated_path_workspace_bytes"] = max(diagnostics["maximum_estimated_path_workspace_bytes"], int(diagnostic.get("estimated_path_workspace_bytes", 0)))
        if "tau_z_mm" in diagnostic:
            diagnostics["tau_z_mm"] = float(diagnostic["tau_z_mm"])
        for name in ("coincidence_policy", "specimen_depth_mm"):
            if name in diagnostic:
                diagnostics[name] = diagnostic[name]
        if frequency_mhz is not None:
            echo = column_acoustic_echoes(paths, frequency_mhz, focus_mm)
            latest = max(latest, echo.max_time_us)
            del echo
        if optical_depth is not None:
            optical_depth[lo:hi] = column_xray_integrals(paths, energy_kev)
        del paths
    return diagnostics, latest, optical_depth


def prepare_continuous_sam(request):
    estimate = estimate_continuous_sam(request)
    a, twin = request.acquisition, request.twin.model_dump(mode="json")
    p = column_layout(twin, a.scan_nx, a.scan_ny, a.roi_mm, a.frequency_mhz)
    x, y = padded_coordinates(p)
    diagnostics, _, _ = path_prepass(twin, x, y, a.include_defects)
    time = a.record_start_us+np.arange(estimate["time_samples"], dtype=np.float64)/a.sample_rate_mhz
    metadata = {**estimate, "path_diagnostics": diagnostics,
        "coordinate_units": {"x": "mm", "y": "mm", "time": "us"},
        "rf_unit": "relative signed pressure", "envelope_unit": "relative echo amplitude",
        "envelope_processing": "absolute value of coherently summed complex analytic Gaussian pressure after lateral PSF; stored independently from signed RF",
        "fractional_bandwidth_definition": "full width at half maximum of positive-frequency Gaussian amplitude spectrum divided by center frequency",
        "time_zero": "transducer reference plane; specimen-top arrival includes two-way water standoff delay",
        "assumptions": ["Synthetic continuous normal-incidence material paths; no experimental calibration.",
            "Full specimen depth and global XY columns retained. Later included primitives overwrite earlier ones; explicit air differs from ambient immersion water.",
            "Primary scalar longitudinal reflections at continuous boundary depths, with nominal material velocities, reciprocal transmission, loss and F-number 2 focus; no reverberation, refraction, shear or full elastic propagation.",
            "Scalar normal-incidence reflection at a curved primitive surface remains an approximation.",
            "Lateral material sampling remains coupled to the scan raster; continuous vertical boundaries do not establish physical spatial resolution.",
            "The complex Gaussian pulse retains fractional-delay phase and four-sigma temporal support; pulse tails may enter from outside the record.",
            "Stored axes are y,x,time; echo time is not geometric depth. Depth mapping requires a separate declared velocity model."]}
    # Persist the exact float64 centers used by the path predicates. Recomputing
    # algebraically equivalent ROI centers can move an inclusive edge by one ULP.
    return PreparedContinuousSam(request, estimate, metadata, time,
        x[p["before_x"]:p["before_x"]+a.scan_nx].copy(),
        y[p["before_y"]:p["before_y"]+a.scan_ny].copy(), p, x, y, twin)


def synthesize_echo_tile(echoes, shape_yx, time_start, nt, sample_rate, frequency, sigma_t, sigma_yx):
    """Full pulse-support, phase-aware pressure synthesis shared by both clients."""
    dt, half = 1/sample_rate, ceil(4*sigma_t*sample_rate)
    nt_work = nt+2*half
    pulse_t = np.arange(-half, half+1)*dt
    wavelet = (np.exp(-.5*(pulse_t/sigma_t)**2)*np.exp(2j*np.pi*frequency*pulse_t)).astype(np.complex64)
    cube = np.zeros((*shape_yx, nt_work), dtype=np.complex64)
    selected = ((echoes.times_us >= time_start-(half+1)*dt) &
                (echoes.times_us <= time_start+(nt-1+half+1)*dt))
    rows, cols = echoes.rows[selected], echoes.cols[selected]
    position = (echoes.times_us[selected]-time_start)/dt+half
    bins = np.floor(position).astype(np.int64)
    fraction, amp = position-bins, echoes.amplitudes[selected]
    valid = (bins >= 0) & (bins < nt_work)
    weights = amp*(1-fraction)*np.exp(-2j*np.pi*frequency*dt*fraction)
    np.add.at(cube, (rows[valid], cols[valid], bins[valid]), weights[valid])
    valid = (bins+1 >= 0) & (bins+1 < nt_work)
    weights = amp*fraction*np.exp(2j*np.pi*frequency*dt*(1-fraction))
    np.add.at(cube, (rows[valid], cols[valid], bins[valid]+1), weights[valid])
    signal = fftconvolve(cube, wavelet[None, None, :], mode="same", axes=2)
    del cube
    signal = gaussian_filter(signal, (*sigma_yx, 0), mode="nearest")
    return signal[:, :, half:half+nt]


def iter_continuous_sam_tiles(prepared, start_row=0):
    from .column_paths import build_column_paths, column_acoustic_echoes
    a, p, chunk = prepared.request.acquisition, prepared.layout, prepared.estimate["tile_rows"]
    if type(start_row) is not int or not 0 <= start_row <= a.scan_ny or (start_row != a.scan_ny and start_row % chunk):
        raise ValueError("Resume row must be a canonical tile boundary within the scan.")
    water_loss = WATER_ATTENUATION_DB_MM_AT_50MHZ*(a.frequency_mhz/50)**2
    standoff_gain = np.exp(-2*np.log(10)/20*water_loss*a.water_standoff_mm)
    for start, stop, lo, hi in tile_ranges(p, chunk):
        if start-p["before_y"] < start_row:
            continue
        paths = build_column_paths(prepared.twin, prepared.padded_x_mm, prepared.padded_y_mm[lo:hi], a.include_defects)
        echoes = column_acoustic_echoes(paths, a.frequency_mhz, a.focus_mm)
        del paths
        echoes.times_us += prepared.estimate["water_round_trip_delay_us"]
        echoes.amplitudes *= standoff_gain
        signal = synthesize_echo_tile(echoes, (hi-lo, p["nx"]), a.record_start_us,
            len(prepared.time_us), a.sample_rate_mhz, a.frequency_mhz,
            prepared.estimate["pulse_sigma_us"], (p["sigma_y"], p["sigma_x"]))
        del echoes
        retained = signal[start-lo:stop-lo, p["before_x"]:p["before_x"]+a.scan_nx]
        rf = np.ascontiguousarray(retained.real, dtype=np.float32)
        envelope = np.ascontiguousarray(np.abs(retained), dtype=np.float32)
        del retained, signal
        yield start-p["before_y"], stop-p["before_y"], rf, envelope
