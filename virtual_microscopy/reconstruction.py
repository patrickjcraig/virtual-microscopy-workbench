"""Independent CPU filtered backprojection of saved parallel-beam measurements.

Only projection logarithms, validity masks, coordinates and poses enter the
reconstruction. Specimen bounds provide the output coordinate domain; material
labels and primitive geometry never enter the inversion.
"""

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
from uuid import UUID, uuid4

import numpy as np
from scipy.fft import rfft, irfft, rfftfreq
import zarr

from .reconstruction_schemas import ReconstructionRequest

MODEL_VERSION = "cpu-parallel-fbp-0.5.0"
MAX_VOLUME_BYTES = 512 * 1024 ** 2
MAX_PEAK_BYTES = 512 * 1024 ** 2
MAX_BACKPROJECTION_WORK = 250_000_000
COVERAGE_TOLERANCE = 1e-6
CACHE_MARKER = {"schema_version": 1, "purpose": "virtual_microscopy_reconstruction_cache"}


@dataclass
class PreparedReconstruction:
    request: ReconstructionRequest
    estimate: dict
    metadata: dict
    x_mm: np.ndarray
    y_mm: np.ndarray
    z_mm: np.ndarray
    source: dict
    filtered: np.memmap | None
    y_covered: np.ndarray
    cache_path: Path | None

    def close(self):
        if self.filtered is not None:
            self.filtered.flush()
            self.filtered._mmap.close()
            self.filtered = None
        if self.cache_path is not None:
            path = self.cache_path
            _remove_cache(path, self.source["path"].parent)
            self.cache_path = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def _remove_cache(path, root):
    path, root = Path(path), Path(root).resolve()
    prefix = ".reconstruction-cache-"
    suffix = path.name[len(prefix):] if path.name.startswith(prefix) else ""
    try:
        canonical = str(UUID(suffix)) == suffix
    except ValueError:
        canonical = False
    if (path.is_symlink() or path.is_junction() or path.resolve().parent != root or not canonical):
        raise ValueError("Reconstruction cache cleanup path leaves its data directory.")
    if json.loads((path / "owner.json").read_text(encoding="utf-8")) != CACHE_MARKER:
        raise ValueError("Reconstruction cache ownership marker is invalid.")
    shutil.rmtree(path)


def _validated(request):
    return request if isinstance(request, ReconstructionRequest) else ReconstructionRequest.model_validate(request)


def _vector(group, name, shape):
    try:
        array = group[name]
        if array.shape != shape:
            raise ValueError(f"Source {name} has an unexpected shape.")
        values = np.asarray(array[:], dtype=np.float64)
    except KeyError as exc:
        raise ValueError(f"Source coordinate or pose is missing: {name}.") from exc
    if not np.isfinite(values).all():
        raise ValueError(f"Source {name} contains nonfinite values.")
    return values


def _source_geometry(request, manifest, path):
    if (manifest.get("kind") != "xray_projection_volume" or not manifest.get("complete") or
            manifest.get("state") != "completed"):
        raise ValueError("Reconstruction requires a completed X-ray projection dataset.")
    if manifest.get("dataset_id") != request.source_dataset_id:
        raise ValueError("Reconstruction source identity does not match its manifest.")
    nviews, rows, cols = manifest["shape"]
    if not (16 <= nviews <= 720 and 16 <= rows <= 256 and 16 <= cols <= 256):
        raise ValueError("CPU FBP requires at least 16 views and supported detector dimensions.")
    group = zarr.open_group(str(Path(path) / "data.zarr"), mode="r")
    for name in ("line_integrals", "valid_mask"):
        if name not in group or group[name].shape != (nviews, rows, cols):
            raise ValueError(f"Source {name} must match the declared projection shape.")
    angles = _vector(group, "angles_deg", (nviews,))
    u, v = _vector(group, "u_mm", (cols,)), _vector(group, "v_mm", (rows,))
    step = float(angles[1] - angles[0])
    span = step * nviews
    if (step <= 0 or not np.allclose(np.diff(angles), step, rtol=0, atol=1e-8) or
            not (np.isclose(span, 180, rtol=0, atol=1e-6) or np.isclose(span, 360, rtol=0, atol=1e-6))):
        raise ValueError("CPU FBP requires uniformly spaced endpoint-excluded 180° or 360° views; limited-angle and nonuniform sources are unsupported.")
    acquisition = manifest["request"]["acquisition"]
    if not np.isclose(acquisition["angle_span_deg"], span, rtol=0, atol=1e-6):
        raise ValueError("Saved angles do not match the declared angular span.")
    du, dv = float(u[1] - u[0]), float(v[1] - v[0])
    if (du <= 0 or dv <= 0 or not np.allclose(np.diff(u), du, atol=1e-10, rtol=0) or
            not np.allclose(np.diff(v), dv, atol=1e-10, rtol=0)):
        raise ValueError("CPU FBP requires increasing uniformly spaced detector coordinates.")
    poses = {name: _vector(group, name, (nviews, 3)) for name in
             ("ray_direction_xyz", "detector_center_mm", "detector_u_xyz", "detector_v_xyz")}
    rad = np.deg2rad(angles)
    directions = np.column_stack((np.sin(rad), np.zeros(nviews), np.cos(rad)))
    eu = np.column_stack((np.cos(rad), np.zeros(nviews), -np.sin(rad)))
    ev = np.tile([0., 1., 0.], (nviews, 1))
    size = np.asarray(manifest["request"]["twin"]["size_mm"], dtype=float)
    center = np.asarray(acquisition.get("rotation_center_mm") or size / 2)
    centers = center + acquisition.get("detector_offset_u_mm", 0) * eu + acquisition.get("detector_offset_v_mm", 0) * ev
    if any(not np.allclose(poses[name], expected, rtol=0, atol=1e-9) for name, expected in
           (("ray_direction_xyz", directions), ("detector_u_xyz", eu),
            ("detector_v_xyz", ev), ("detector_center_mm", centers))):
        raise ValueError("CPU FBP supports only the saved canonical parallel-beam rotation about specimen Y; cone, tilted or noncanonical poses are unsupported.")
    edges_u = [u[0] - du / 2, u[-1] + du / 2]
    edges_v = [v[0] - dv / 2, v[-1] + dv / 2]
    corners = np.array([[x, y, z] for x in (0, size[0]) for y in (0, size[1]) for z in (0, size[2])])
    truncated = []
    for i in range(nviews):
        pu, pv = (corners - centers[i]) @ eu[i], (corners - centers[i]) @ ev[i]
        if (pu.min() < edges_u[0] - 1e-8 or pu.max() > edges_u[1] + 1e-8 or
                pv.min() < edges_v[0] - 1e-8 or pv.max() > edges_v[1] + 1e-8):
            truncated.append(i)
    if truncated and request.reconstruction.truncation_policy == "reject":
        raise ValueError("The source detector truncates the specimen envelope. Acquire full coverage or explicitly allow truncated FBP with its bias and coverage mask.")
    return {"path": Path(path).resolve(), "shape": (nviews, rows, cols), "angles_deg": angles,
            "u_mm": u, "v_mm": v, "du": du, "dv": dv, "span_deg": span,
            "size_mm": size, "u_edges": edges_u, "v_edges": edges_v,
            "truncated_view_indices": truncated, **poses}


def _output_coordinates(request, source):
    s = request.reconstruction
    size = source["size_mm"]
    bounds = s.bounds_mm or (0, size[0], 0, size[1], 0, size[2])
    if any(bounds[2*i] < 0 or bounds[2*i+1] > size[i] for i in range(3)):
        raise ValueError("Reconstruction bounds must lie within the source specimen bounds.")
    coords = tuple(bounds[2*i] + (np.arange(n) + 0.5) * (bounds[2*i+1]-bounds[2*i]) / n
                   for i, n in enumerate((s.nx, s.ny, s.nz)))
    return list(bounds), coords


def estimate_reconstruction(request, source_manifest, source_path):
    """Read coordinates/poses only; never load full projection products to estimate."""
    request = _validated(request)
    source = _source_geometry(request, source_manifest, source_path)
    s = request.reconstruction
    bounds, (x, y, z) = _output_coordinates(request, source)
    nviews, rows, cols = source["shape"]
    voxels = s.nx * s.ny * s.nz
    signal_bytes = voxels * 4
    coordinate_bytes = (s.nx + s.ny + s.nz) * 8
    total = 2 * signal_bytes + coordinate_bytes
    cache = nviews * s.ny * cols * 4
    nfft = max(64, 1 << int(np.ceil(np.log2(2 * cols))))
    peak = (cache + rows * nfft * 64 + s.ny * cols * 64 + s.ny * s.nx * 128 +
            nviews * s.ny * 16 + coordinate_bytes + 16 * 1024 ** 2)
    work = voxels * nviews
    if total > MAX_VOLUME_BYTES:
        raise ValueError("Reconstruction attenuation/coverage exceed the 512 MiB saved-volume budget. Reduce output dimensions.")
    if peak > MAX_PEAK_BYTES:
        raise ValueError("Estimated reconstruction workspace exceeds 512 MiB including the mapped filtered-projection cache. Reduce output Y samples.")
    if work > MAX_BACKPROJECTION_WORK:
        raise ValueError("Reconstruction exceeds the 250 million backprojection work budget. Reduce output dimensions.")
    warnings = []
    if source["truncated_view_indices"]:
        warnings.append("Truncated FBP was explicitly allowed. Missing projection tails bias the ramp filter even where local coverage equals 1; this is not quantitative attenuation recovery.")
    yv = y - source["detector_center_mm"][0, 1]
    covered_y = (yv >= source["v_edges"][0] - 1e-9) & (yv <= source["v_edges"][1] + 1e-9)
    if not covered_y.all():
        warnings.append("Some output Y positions are outside the detector field. Their attenuation is a masked zero placeholder, not measured air.")
    pitch = [(bounds[2*i+1]-bounds[2*i]) / n for i, n in enumerate((s.nx, s.ny, s.nz))]
    if min(pitch[0], pitch[2]) < source["du"] / 2:
        warnings.append("Output X/Z voxels oversample detector-U pitch; smaller voxels do not restore unresolved source detail.")
    unique_views = nviews if np.isclose(source["span_deg"], 180) or nviews % 2 else nviews // 2
    if unique_views < np.pi * cols / 2:
        warnings.append("Angular sampling is sparse relative to detector-U sampling; streaks and angular aliasing can dominate fine detail.")
    if s.invalid_policy == "interpolate":
        warnings.append("Invalid logarithms will be interpolated along detector U before filtering. Their count is reported during preparation; interpolation can bias attenuation and does not validate the measurements.")
    return {"schema_version": 1, "kind": "xray_reconstruction", "model_version": MODEL_VERSION,
            "shape": [s.nz, s.ny, s.nx], "axis_order": ["z", "y", "x"], "dtype": "float32",
            "tile_rows": 1, "total_rows": s.nz, "chunks": [1, s.ny, s.nx],
            "attenuation_bytes": signal_bytes, "coverage_bytes": signal_bytes,
            "coordinate_bytes": coordinate_bytes, "total_bytes": total,
            "estimated_peak_bytes": peak, "estimated_temporary_bytes": cache, "workspace_disk_bytes": cache,
            "backprojection_work": work, "source_shape": list(source["shape"]),
            "source_dataset_id": request.source_dataset_id, "source_angle_span_deg": source["span_deg"],
            "source_unique_angles_mod180": unique_views, "bounds_mm": bounds, "voxel_pitch_mm": pitch,
            "voxel_pitch_um": [p * 1000 for p in pitch], "detector_u_pitch_mm": source["du"],
            "filter": s.filter, "frequency_cutoff": s.frequency_cutoff,
            "filter_cutoff_cycles_per_mm": s.frequency_cutoff / (2 * source["du"]),
            "fft_length": nfft, "angular_weight_radians": float(np.pi / nviews),
            "truncated_view_indices": source["truncated_view_indices"], "warnings": warnings}


def _filter_response(cols, du, filter_name, cutoff):
    """Discrete Ram-Lak convolution, with physical detector pitch in millimetres.

    h[0]=1/(4du), h[n odd]=-1/(pi²n²du), h[n even]=0. This is the sampled
    ramp convolution including its integration step; pi/N weights complete
    the FBP angular quadrature. Hann tapers the amplitude response to cutoff.
    """
    length = max(64, 1 << int(np.ceil(np.log2(2 * cols))))
    n = np.arange(length)
    signed = np.where(n <= length // 2, n, n - length)
    h = np.zeros(length)
    h[0] = 1 / (4 * du)
    odd = (signed % 2) != 0
    h[odd] = -1 / (np.pi ** 2 * signed[odd] ** 2 * du)
    response = rfft(h).real
    f = rfftfreq(length, d=du)
    limit = cutoff / (2 * du)
    inside = f <= limit + 1e-12
    response[~inside] = 0
    if filter_name == "hann":
        response[inside] *= 0.5 * (1 + np.cos(np.pi * f[inside] / limit))
    return length, response


def _interpolation_indices(coordinates, targets, edges):
    covered = (targets >= edges[0] - 1e-9) & (targets <= edges[1] + 1e-9)
    # Constant extension to the physical half-pixel edge, never beyond it.
    position = np.clip((targets - coordinates[0]) / (coordinates[1] - coordinates[0]), 0, len(coordinates) - 1)
    low = np.floor(position).astype(int)
    high = np.minimum(low + 1, len(coordinates) - 1)
    return low, high, position - low, covered


def prepare_reconstruction(request, source_manifest, source_path):
    request = _validated(request)
    estimate = estimate_reconstruction(request, source_manifest, source_path)
    source = _source_geometry(request, source_manifest, source_path)
    _, (x, y, z) = _output_coordinates(request, source)
    s = request.reconstruction
    nviews, rows, cols = source["shape"]
    root = source["path"].parent
    path = root / f".reconstruction-cache-{uuid4()}"
    path.mkdir(exist_ok=False)
    (path / "owner.json").write_text(json.dumps(CACHE_MARKER, sort_keys=True), encoding="utf-8")
    filtered = None
    try:
        filtered = np.memmap(path / "filtered.f32", mode="w+", dtype=np.float32, shape=(nviews, s.ny, cols))
        group = zarr.open_group(str(source["path"] / "data.zarr"), mode="r")
        length, response = _filter_response(cols, source["du"], s.filter, s.frequency_cutoff)
        v_targets = y - source["detector_center_mm"][0, 1]
        low, high, fraction, covered = _interpolation_indices(source["v_mm"], v_targets, source["v_edges"])
        interpolated = 0
        affected_rows = 0
        for index in range(nviews):
            values = np.asarray(group["line_integrals"][index], dtype=np.float64)
            mask = np.asarray(group["valid_mask"][index])
            if (values.shape != (rows, cols) or mask.shape != (rows, cols) or
                    not np.isfinite(values).all() or not np.all((mask == 0) | (mask == 1))):
                raise ValueError("Saved projection values or validity masks are missing or corrupted.")
            invalid = mask == 0
            count = int(invalid.sum())
            if count and s.invalid_policy == "reject":
                raise ValueError("Source contains invalid zero-count logarithms. Choose interpolation explicitly or acquire a suitable source.")
            interpolated += count
            for row in np.flatnonzero(invalid.any(axis=1)):
                good = mask[row] == 1
                if good.sum() < 2:
                    raise ValueError("A source detector row has fewer than two valid logarithms; interpolation cannot recover this row.")
                values[row, ~good] = np.interp(source["u_mm"][~good], source["u_mm"][good], values[row, good])
                affected_rows += 1
            filtered_view = irfft(rfft(values, n=length, axis=1) * response, n=length, axis=1)[:, :cols]
            filtered[index] = ((1 - fraction[:, None]) * filtered_view[low] + fraction[:, None] * filtered_view[high]).astype(np.float32)
        filtered.flush()
        metadata = {**estimate, "interpolated_invalid_samples": interpolated,
                    "interpolated_detector_rows": affected_rows,
                    "source_evidence_status": source_manifest.get("evidence_status"),
                    "source_input_sha256": source_manifest.get("input_sha256"),
                    "attenuation_unit": "mm^-1", "coordinate_units": {"x": "mm", "y": "mm", "z": "mm"},
                    "coverage_definition": "Fraction of acquired angles whose detector U/V physical pixel field covers the output point. This describes geometric support only, not accuracy, recovered missing input samples or full projection-tail coverage.",
                    "unsupported_voxel_definition": "Where coverage<1-1e-6 attenuation is a finite zero placeholder. It is masked, not air, not quantitative attenuation, and not a partial-view sum rescaled to full coverage.",
                    "assumptions": [
                        "Independent CPU filtered backprojection from saved line_integrals, valid_mask and coordinate/pose arrays. Source twin primitives and material labels are not used by the inversion.",
                        "Canonical parallel-beam rotation about specimen Y; uniform endpoint-excluded 180 or 360 degrees only. Each Y plane is reconstructed by a 2D FBP, with linear detector-V interpolation between measured rows.",
                        "Discrete Ram-Lak ramp convolution with physical detector-U pitch; optional Hann taper and cutoff relative to detector Nyquist. Angular weight is pi/view_count for both 180 and 360 degrees; opposed 360-degree measurements carry the corresponding half weight.",
                        "Filtering zero-pads beyond the measured detector-U field. Output sampling linearly interpolates U and V, using constant nearest-center extension only within the outer half-pixel edges. No numerical extension beyond physical detector edges is interpreted as coverage.",
                        "Invalid logarithm interpolation occurs only in the reconstruction workspace along detector U; endpoint gaps use nearest valid edge values. Fewer than two valid samples in any affected detector row is an error. Source data are unchanged; interpolated data are not measurements or a validation result.",
                        "Geometric coverage does not certify attenuation accuracy. Allowed source truncation biases filtering even at points with coverage 1; sparse angles, detector blur, photon noise and discretization also cause artifacts.",
                        "Output units are inverse millimetres. Negative reconstructed values are preserved wherever coverage is complete; they are not clipped to material-library labels or forced to zero.",
                        "Synthetic numerical reconstruction, not experimental calibration, a material segmentation, cone-beam FDK, iterative reconstruction or laminography.",
                    ]}
        if interpolated:
            metadata["warnings"] = [*estimate["warnings"], f"Interpolated {interpolated} invalid logarithm samples across {affected_rows} detector rows. Attenuation may be biased even where geometric coverage is complete."]
        return PreparedReconstruction(request, estimate, metadata, x, y, z, source, filtered, covered, path)
    except Exception:
        if filtered is not None:
            filtered._mmap.close()
        _remove_cache(path, root)
        raise


def iter_reconstruction_slices(prepared, start_slice=0):
    """Yield bounded canonical [1,y,x] planes, preserving negative supported values."""
    if prepared.filtered is None:
        raise ValueError("Reconstruction workspace has been closed.")
    s, source = prepared.request.reconstruction, prepared.source
    if not isinstance(start_slice, int) or isinstance(start_slice, bool) or not 0 <= start_slice <= s.nz:
        raise ValueError("Resume slice must be an integer within the reconstruction range.")
    nviews = source["shape"][0]
    x = prepared.x_mm
    for k in range(start_slice, s.nz):
        result = np.zeros((s.ny, s.nx), dtype=np.float64)
        supported_views = np.zeros(s.nx, dtype=np.int32)
        z = prepared.z_mm[k]
        for index in range(nviews):
            eu, center = source["detector_u_xyz"][index], source["detector_center_mm"][index]
            target_u = (x - center[0]) * eu[0] + (z - center[2]) * eu[2]
            low, high, fraction, covered = _interpolation_indices(source["u_mm"], target_u, source["u_edges"])
            view = prepared.filtered[index]
            result += ((1 - fraction) * view[:, low] + fraction * view[:, high]) * covered
            supported_views += covered
        coverage = np.broadcast_to(supported_views / nviews, (s.ny, s.nx)).copy()
        coverage *= prepared.y_covered[:, None]
        result *= np.pi / nviews
        result[coverage < 1 - COVERAGE_TOLERANCE] = 0
        if not np.isfinite(result).all():
            raise ValueError("Reconstructed attenuation overflowed or became nonfinite.")
        yield k, k + 1, {"attenuation": np.ascontiguousarray(result[None], dtype=np.float32),
                         "coverage": np.ascontiguousarray(coverage[None], dtype=np.float32)}
