"""Full-angle CPU parallel radiography using exact paths through sampled voxels.

The shared grid contains the whole specimen. Detector field of view and pixel
sampling are independent of this geometry grid. Saved axes are [view, v, u].
This module generates projections; it does not reconstruct a CT volume.
"""

from dataclasses import dataclass
from math import hypot, log, sqrt

import numpy as np
from scipy.ndimage import gaussian_filter

from .materials import linear_attenuation_mm
from .physics import MATERIAL_IDS, MaterialGrid, voxelize
from .xray_schemas import XrayVolumeRequest

MODEL_VERSION = "xray-projection-volume-0.4.0"
MAX_VOLUME_BYTES = 512 * 1024 ** 2
MAX_PEAK_BYTES = 512 * 1024 ** 2
MAX_PROJECTION_WORK = 250_000_000


@dataclass
class PreparedXrayVolume:
    request: XrayVolumeRequest
    estimate: dict
    grid: MaterialGrid
    metadata: dict
    angles_deg: np.ndarray
    u_mm: np.ndarray
    v_mm: np.ndarray
    ray_direction_xyz: np.ndarray
    detector_center_mm: np.ndarray
    detector_u_xyz: np.ndarray
    detector_v_xyz: np.ndarray


def _validated(request):
    return request if isinstance(request, XrayVolumeRequest) else XrayVolumeRequest.model_validate(request)


def _layout(request):
    a = request.acquisition
    sx, sy, sz = request.twin.size_mm
    center = a.rotation_center_mm or (sx / 2, sy / 2, sz / 2)
    # Centered defaults reduce to sqrt(sx^2+sz^2), sy. An explicitly moved
    # rotation center needs a larger auto field to remain safe at every angle.
    width = a.detector_width_mm or 2 * hypot(max(center[0], sx - center[0]), max(center[2], sz - center[2]))
    height = a.detector_height_mm or 2 * max(center[1], sy - center[1])
    du, dv = width / a.detector_cols, height / a.detector_rows
    sigma = a.detector_fwhm_mm / sqrt(8 * log(2))
    hu, hv = int(4 * sigma / du + 0.5), int(4 * sigma / dv + 0.5)
    return dict(center=np.asarray(center, dtype=float), width=width, height=height,
                du=du, dv=dv, sigma=sigma, hu=hu, hv=hv,
                nu=a.detector_cols + 2 * hu, nv=a.detector_rows + 2 * hv)


def _poses(request, layout):
    a = request.acquisition
    angles = a.angle_start_deg + np.arange(a.views, dtype=np.float64) * a.angle_span_deg / a.views
    radians = np.deg2rad(angles)
    sin, cos = np.sin(radians), np.cos(radians)
    # Exact cardinal direction values prevent boundary round-off from making
    # an on-axis ray skim a neighboring voxel or an outside detector row.
    sin[np.abs(sin) < 1e-14] = 0
    cos[np.abs(cos) < 1e-14] = 0
    direction = np.column_stack((sin, np.zeros(a.views), cos))
    basis_u = np.column_stack((cos, np.zeros(a.views), -sin))
    basis_v = np.tile([0.0, 1.0, 0.0], (a.views, 1))
    detector_center = (layout["center"] + a.detector_offset_u_mm * basis_u +
                       a.detector_offset_v_mm * basis_v)
    return angles, direction, detector_center, basis_u, basis_v


def _ray_intervals(origins_xz, direction_xz, size_xz):
    """Intersect parallel rays with the specimen rectangle; no tangent division."""
    lo = np.full(len(origins_xz), -np.inf)
    hi = np.full(len(origins_xz), np.inf)
    inside = np.ones(len(origins_xz), dtype=bool)
    for axis in range(2):
        direction = direction_xz[axis]
        if abs(direction) < 1e-14:
            inside &= (origins_xz[:, axis] >= 0) & (origins_xz[:, axis] < size_xz[axis])
        else:
            enter = -origins_xz[:, axis] / direction
            leave = (size_xz[axis] - origins_xz[:, axis]) / direction
            lo = np.maximum(lo, np.minimum(enter, leave))
            hi = np.minimum(hi, np.maximum(enter, leave))
    inside &= hi > lo
    return lo, hi, inside


def estimate_xray(request: XrayVolumeRequest | dict) -> dict:
    """Validate memory, saved-array and ray-segment work before voxel allocation."""
    request = _validated(request)
    a, p = request.acquisition, _layout(request)
    geometry_cells = a.geometry_nx * a.geometry_ny * a.geometry_nz
    if geometry_cells > 64_000_000:
        raise ValueError("X-ray geometry exceeds 64 million cells. Reduce material geometry dimensions.")
    array_bytes = a.views * a.detector_rows * a.detector_cols * 4
    coordinate_bytes = (a.detector_rows + a.detector_cols + a.views * 13) * 8
    total_bytes = 4 * array_bytes + coordinate_bytes
    if total_bytes > MAX_VOLUME_BYTES:
        raise ValueError("The four projection arrays exceed the 512 MiB saved-volume budget. Reduce views or detector raster dimensions.")
    segment_bound = a.geometry_nx + a.geometry_nz + 1
    # One padded projection plus noise/filter/output buffers, and one column's
    # vectorized material-index/path workspace. This is a numerical estimate,
    # not measured process RSS or a bound on external compression/API overhead.
    projection_peak = (geometry_cells + p["nu"] * p["nv"] * 80 +
                       p["nv"] * segment_bound * 32 + a.detector_rows * a.detector_cols * 16 +
                       coordinate_bytes + 16 * 1024 ** 2)
    pitch = np.asarray(request.twin.size_mm) / [a.geometry_nx, a.geometry_ny, a.geometry_nz]
    raster = np.array([a.geometry_nx, a.geometry_ny, a.geometry_nz])
    geometry_scratch = 0
    for obj in request.twin.objects:
        if not a.include_defects and obj.role == "defect":
            continue
        occupied = np.minimum(raster, np.ceil(np.asarray(obj.size_mm) / pitch).astype(int) + 1)
        cells = int(occupied.prod())
        if obj.shape == "sphere":
            # Voxelization constructs a broadcast float64 radial+z temporary
            # and a boolean mask; these precede the much smaller ray workspace.
            geometry_scratch = max(geometry_scratch, 10 * cells)
        elif obj.shape == "cylinder":
            geometry_scratch = max(geometry_scratch, 2 * cells + int(occupied[0] * occupied[1]) * 64)
    geometry_peak = geometry_cells + geometry_scratch + coordinate_bytes + 16 * 1024 ** 2
    peak = max(projection_peak, geometry_peak)
    if peak > MAX_PEAK_BYTES:
        raise ValueError("Estimated X-ray numerical workspace exceeds 512 MiB including geometry construction and detector PSF halo. Reduce material/detector dimensions or blur, or enlarge the detector field of view.")
    angles, direction, centers, basis_u, basis_v = _poses(request, p)
    u = (np.arange(p["nu"]) + 0.5 - p["hu"] - a.detector_cols / 2) * p["du"]
    v = (np.arange(p["nv"]) + 0.5 - p["hv"] - a.detector_rows / 2) * p["dv"]
    sx, sy, sz = request.twin.size_mm
    dx, dy, dz = sx / a.geometry_nx, sy / a.geometry_ny, sz / a.geometry_nz
    valid_rows = int(np.count_nonzero((centers[0, 1] + v >= 0) & (centers[0, 1] + v < sy)))
    ray_segment_work = 0
    truncated = []
    corners = np.array([[x, y, z] for x in (0, sx) for y in (0, sy) for z in (0, sz)])
    for index in range(a.views):
        origins = centers[index, [0, 2]] + u[:, None] * basis_u[index, [0, 2]]
        lo, hi, inside = _ray_intervals(origins, direction[index, [0, 2]], [sx, sz])
        length = hi[inside] - lo[inside]
        # Number of crossed x and z cell boundaries plus two endpoints. This
        # adapts to actual ray lengths and angle without enumerating materials.
        count = np.ceil(length * abs(direction[index, 0]) / dx) + np.ceil(length * abs(direction[index, 2]) / dz) + 1
        ray_segment_work += int(count.sum()) * valid_rows
        pu, pv = (corners - centers[index]) @ basis_u[index], (corners - centers[index]) @ basis_v[index]
        if (pu.min() < -p["width"] / 2 - 1e-9 or pu.max() > p["width"] / 2 + 1e-9 or
                pv.min() < -p["height"] / 2 - 1e-9 or pv.max() > p["height"] / 2 + 1e-9):
            truncated.append(index)
    work = ray_segment_work + a.views * p["nu"] * p["nv"]
    if work > MAX_PROJECTION_WORK:
        raise ValueError("X-ray acquisition exceeds the local ray-segment work budget. Reduce views, detector raster or material geometry dimensions; a larger field can reduce halo overhead.")
    warnings = []
    if truncated:
        warnings.append(f"The detector field truncates the projected specimen envelope in {len(truncated)} of {a.views} views. Attenuation still uses the complete specimen; missing detector coverage is not reconstructed.")
    if a.detector_fwhm_mm and max(p["du"], p["dv"]) > a.detector_fwhm_mm / 2:
        warnings.append("Detector pixel pitch undersamples the modeled Gaussian PSF; pixel pitch and voxel pitch are not measured spatial resolution.")
    return {
        "schema_version": 1, "kind": "xray_projection_volume", "model_version": MODEL_VERSION,
        "shape": [a.views, a.detector_rows, a.detector_cols], "axis_order": ["view", "v", "u"],
        "dtype": "float32", "tile_rows": 1, "total_rows": a.views,
        "chunks": [1, a.detector_rows, a.detector_cols],
        "counts_bytes": array_bytes, "transmission_bytes": array_bytes,
        "line_integrals_bytes": array_bytes, "valid_mask_bytes": array_bytes,
        "coordinate_bytes": coordinate_bytes, "total_bytes": total_bytes,
        "estimated_peak_bytes": peak, "estimated_geometry_peak_bytes": geometry_peak,
        "estimated_projection_peak_bytes": projection_peak, "ray_segment_work": ray_segment_work,
        "projection_work_cells": work, "geometry_cells": geometry_cells,
        "grid_shape": [a.geometry_ny, a.geometry_nx, a.geometry_nz],
        "geometry_pitch_mm": [dx, dy, dz], "geometry_pitch_um": [dx * 1000, dy * 1000, dz * 1000],
        "detector_width_mm": p["width"], "detector_height_mm": p["height"],
        "detector_extent_mm": [-p["width"] / 2, p["width"] / 2, -p["height"] / 2, p["height"] / 2],
        "detector_pixel_pitch_um": [p["du"] * 1000, p["dv"] * 1000],
        "detector_halo_pixels": [p["hu"], p["hv"]],
        "detector_padded_shape": [p["nv"], p["nu"]],
        "rotation_center_mm": p["center"].tolist(),
        "angle_start_deg": a.angle_start_deg, "angle_step_deg": a.angle_span_deg / a.views,
        "angle_end_deg": float(angles[-1]),
        "angle_stop_exclusive_deg": a.angle_start_deg + a.angle_span_deg,
        "angles_range_deg": [float(angles[0]), float(angles[-1])],
        "truncated_view_indices": truncated, "truncated_views": len(truncated),
        "warnings": warnings,
    }


def prepare_xray(request: XrayVolumeRequest | dict) -> PreparedXrayVolume:
    request = _validated(request)
    estimate = estimate_xray(request)
    a, p = request.acquisition, _layout(request)
    grid = voxelize(request.twin.model_dump(mode="json"), (a.geometry_nx, a.geometry_ny),
                    a.include_defects, depth_samples=a.geometry_nz)
    angles, direction, centers, basis_u, basis_v = _poses(request, p)
    u = (np.arange(a.detector_cols, dtype=np.float64) + 0.5 - a.detector_cols / 2) * p["du"]
    v = (np.arange(a.detector_rows, dtype=np.float64) + 0.5 - a.detector_rows / 2) * p["dv"]
    metadata = {**estimate, "warnings": grid.warnings + estimate["warnings"],
        "coordinate_units": {"u": "mm", "v": "mm", "angle": "degrees", "detector_center": "mm"},
        "ray_equation": "r(s)=detector_center_mm[view]+u_mm[u]*detector_u_xyz[view]+v_mm[v]*detector_v_xyz[view]+s*ray_direction_xyz[view]",
        "pose_vector_definition": "Ray direction and detector u/v vectors are unit vectors, not pixel-pitch-scaled ASTRA vectors. u/v coordinates are detector-local; offsets are included only in detector_center_mm.",
        "rotation_convention": "Rotation around specimen Y: ray direction=(sin(theta),0,cos(theta)); detector u=(cos(theta),0,-sin(theta)); detector v=(0,1,0).",
        "counts_definition": "Poisson counts with noise enabled; fractional expected counts with noise disabled; retained as float32.",
        "transmission_definition": "Stored counts divided by incident photons; may exceed one under Poisson noise.",
        "line_integrals_definition": "-log(where(stored counts>0,stored counts,0.5)/incident photons). Positive fractional expected counts are unchanged. This is a zero-regularized logarithm of measured blurred transmission, not the ideal unblurred attenuation path integral.",
        "valid_mask_definition": "float32 one when stored counts>0, zero otherwise. Only zero counts use a half-count numerical substitute to prevent an undefined logarithm; substituted values are not attenuation truth. Nonzero mask does not imply experimental validity.",
        "noise_rng": "NumPy default_rng(SeedSequence([seed, zero-based view index])); reproducible per canonical view independent of resume order.",
        "assumptions": [
            "Synthetic monoenergetic parallel-beam forward acquisition; no CT or laminography reconstruction and no experimental calibration or validation.",
            "Exact plane-intersection lengths through a piecewise-constant voxel-center-sampled material grid. Exact voxel paths do not remove geometry discretization error, disappearing thin features or aliasing.",
            "Full specimen geometry contributes along every ray. A cropped or shifted detector changes measured coverage, not the material domain; all-angle paths remain finite at cardinal angles.",
            "Detector pixel samples and material geometry dimensions are independent. Rays sample detector pixel centers; no pixel-area quadrature is modeled. Neither pitch is a measured physical resolution.",
            "Beer-Lambert attenuation uses the current 40-150 keV material tables and documented material surrogates; ambient outside primitives is open-beam referenced.",
            "Gaussian detector PSF acts on ideal transmitted intensity before photon sampling. Four-sigma detector halos include attenuation outside a requested crop; outside-specimen transmission is one.",
            "No source spectrum, finite focal spot, scatter, beam hardening, fluorescence, phase contrast, calibrated detector/electronics model or experimental exposure-to-count conversion.",
            "Angles use start+k*span/views with the endpoint excluded. A full360-degree scan includes opposed projections rather than duplicating its first view at360 degrees.",
            "Counts are retained. Zero-count logs use an explicit half-count numerical floor and invalid mask; floor values are not attenuation truth. Noise can produce transmission>1 and negative logarithms.",
        ]}
    return PreparedXrayVolume(request, estimate, grid, metadata, angles, u, v,
                              direction, centers, basis_u, basis_v)


def _segments(origin, direction, size_xz, counts_xz, pitch_xz, low, high):
    """Siddon-style plane crossings for one detector column, shared across v."""
    crossings = [np.array([low, high])]
    for axis in range(2):
        if abs(direction[axis]) >= 1e-14:
            planes = np.arange(1, counts_xz[axis]) * pitch_xz[axis]
            t = (planes - origin[axis]) / direction[axis]
            crossings.append(t[(t > low) & (t < high)])
    edges = np.unique(np.concatenate(crossings))
    lengths = np.diff(edges)
    mid = (edges[:-1] + edges[1:]) / 2
    keep = lengths > 1e-12 * max(1.0, high - low)
    middle = origin + mid[keep, None] * direction
    indices = np.floor(middle / pitch_xz).astype(np.int64)
    indices = np.clip(indices, 0, np.asarray(counts_xz) - 1)
    return indices[:, 0], indices[:, 1], lengths[keep]


def _project_view(prepared, view_index):
    a, grid = prepared.request.acquisition, prepared.grid
    p = _layout(prepared.request)
    u = (np.arange(p["nu"]) + 0.5 - p["hu"] - a.detector_cols / 2) * p["du"]
    v = (np.arange(p["nv"]) + 0.5 - p["hv"] - a.detector_rows / 2) * p["dv"]
    center, direction, basis = prepared.detector_center_mm[view_index], prepared.ray_direction_xyz[view_index], prepared.detector_u_xyz[view_index]
    ys = center[1] + v
    inside_y = (ys >= 0) & (ys < grid.size_mm[1])
    rows = np.flatnonzero(inside_y)
    y_indices = np.floor(ys[inside_y] / grid.pitch_mm[1]).astype(np.int64)
    origins = center[[0, 2]] + u[:, None] * basis[[0, 2]]
    lo, hi, inside = _ray_intervals(origins, direction[[0, 2]], grid.size_mm[[0, 2]])
    lookup = np.array([0.0] + [linear_attenuation_mm(material, a.energy_kev) for material in MATERIAL_IDS])
    attenuation = np.zeros((p["nv"], p["nu"]), dtype=np.float64)
    if len(rows):
        for col in np.flatnonzero(inside):
            ix, iz, lengths = _segments(origins[col], direction[[0, 2]], grid.size_mm[[0, 2]],
                                       (a.geometry_nx, a.geometry_nz), grid.pitch_mm[[0, 2]], lo[col], hi[col])
            labels = grid.labels[y_indices[:, None], ix[None, :], iz[None, :]]
            attenuation[rows, col] = np.sum(lookup[labels] * lengths, axis=1)
    transmission = np.exp(-attenuation)
    if a.detector_fwhm_mm:
        transmission = gaussian_filter(transmission, (p["sigma"] / p["dv"], p["sigma"] / p["du"]), mode="constant", cval=1.0)
    return transmission[p["hv"]:p["hv"] + a.detector_rows, p["hu"]:p["hu"] + a.detector_cols]


def iter_xray_views(prepared: PreparedXrayVolume, start_view: int = 0):
    """Yield canonical float32 [1,v,u] views with independent deterministic RNG."""
    a = prepared.request.acquisition
    if not isinstance(start_view, int) or isinstance(start_view, bool) or not 0 <= start_view <= a.views:
        raise ValueError("Resume view must be an integer within the acquired view range.")
    for index in range(start_view, a.views):
        ideal = _project_view(prepared, index)
        expected_counts = ideal * a.photons
        if a.noise:
            rng = np.random.default_rng(np.random.SeedSequence([a.seed, index]))
            counts = rng.poisson(expected_counts).astype(np.float32)
        else:
            counts = expected_counts.astype(np.float32)
        # Derive all products from representable stored counts. This keeps mask
        # and logs consistent when extremely opaque noiseless counts underflow.
        transmission = (counts.astype(np.float64) / a.photons).astype(np.float32)
        line_integrals = (-np.log(np.where(counts > 0, counts.astype(np.float64), 0.5) / a.photons)).astype(np.float32)
        valid_mask = (counts > 0).astype(np.float32)
        yield index, index + 1, {name: np.ascontiguousarray(array[None], dtype=np.float32)
                                for name, array in {"counts": counts, "transmission": transmission,
                                                    "line_integrals": line_integrals, "valid_mask": valid_mask}.items()}
