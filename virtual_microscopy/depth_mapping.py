"""Map saved acoustic signals from time into an explicitly declared depth model.

This resamples signed RF and its independently saved analytic envelope. It does
not solve acoustic propagation, estimate impedance, infer material velocities or
validate depth accuracy. Primitive/material labels are never read.
"""

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
from uuid import UUID, uuid4

import numpy as np
import zarr

from .depth_schemas import SamDepthRequest

MODEL_VERSION = "sam-time-depth-mapping-0.6.0"
MAX_BYTES = 512 * 1024 ** 2
TIME_TOLERANCE_US = 1e-12
CACHE_MARKER = {"schema_version": 1, "purpose": "virtual_microscopy_depth_mapping_cache"}


@dataclass
class PreparedDepth:
    request: SamDepthRequest
    estimate: dict
    metadata: dict
    x_mm: np.ndarray
    y_mm: np.ndarray
    z_mm: np.ndarray
    travel_time_us: np.ndarray
    depth_valid: np.ndarray
    cache: np.memmap | None
    cache_path: Path | None
    source_path: Path

    def close(self):
        if self.cache is not None:
            self.cache.flush()
            self.cache._mmap.close()
            self.cache = None
        if self.cache_path is not None:
            _remove_cache(self.cache_path, self.source_path.parent)
            self.cache_path = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def _remove_cache(path, root):
    path, root = Path(path), Path(root).resolve()
    prefix = ".depth-mapping-cache-"
    suffix = path.name[len(prefix):] if path.name.startswith(prefix) else ""
    try:
        canonical = str(UUID(suffix)) == suffix
    except ValueError:
        canonical = False
    if path.is_symlink() or path.is_junction() or path.resolve().parent != root or not canonical:
        raise ValueError("Depth-mapping cache cleanup path leaves its data directory.")
    if json.loads((path / "owner.json").read_text(encoding="utf-8")) != CACHE_MARKER:
        raise ValueError("Depth-mapping cache ownership marker is invalid.")
    shutil.rmtree(path)


def _validated(request):
    return request if isinstance(request, SamDepthRequest) else SamDepthRequest.model_validate(request)


def _source(request, manifest, path):
    if (manifest.get("kind", "sam_rf_volume") != "sam_rf_volume" or not manifest.get("complete") or
            manifest.get("state") != "completed"):
        raise ValueError("Depth mapping requires a completed saved SAM RF volume.")
    if manifest.get("dataset_id") != request.source_dataset_id:
        raise ValueError("Depth-mapping source identity does not match its manifest.")
    ny, nx, nt = manifest["shape"]
    if not (16 <= nx <= 256 and 16 <= ny <= 256 and 2 <= nt <= 16384):
        raise ValueError("Source RF shape is outside supported saved-volume dimensions.")
    group = zarr.open_group(str(Path(path) / "data.zarr"), mode="r")
    chunk_rows = 1
    for name in ("rf", "envelope"):
        if name not in group or group[name].shape != (ny, nx, nt) or group[name].dtype != np.dtype("float32"):
            raise ValueError(f"Source {name} must be a float32 array matching its declared shape.")
        chunk_rows = max(chunk_rows, min(ny, group[name].chunks[0]))
    coords = {}
    for name, length in (("x_mm", nx), ("y_mm", ny), ("time_us", nt)):
        if name not in group or group[name].shape != (length,):
            raise ValueError(f"Invalid source {name} coordinate shape.")
        values = np.asarray(group[name][:], dtype=np.float64)
        if not np.isfinite(values).all() or not np.all(np.diff(values) > 0):
            raise ValueError(f"Source {name} must be finite and strictly increasing.")
        if name != "time_us" and not np.allclose(np.diff(values), values[1]-values[0], rtol=0, atol=1e-10):
            raise ValueError(f"Source {name} must be uniformly sampled for spatial depth-volume inspection.")
        coords[name] = values
    specimen_depth = float(manifest["request"]["twin"]["size_mm"][2])
    if not np.isfinite(specimen_depth) or specimen_depth <= 0:
        raise ValueError("Source specimen depth is invalid.")
    s = request.mapping
    if s.z_min_mm < 0 or s.z_max_mm > specimen_depth:
        raise ValueError("Mapped depth bounds must lie within the source specimen depth.")
    if s.layers and s.layers[-1].end_depth_mm > specimen_depth:
        raise ValueError("Velocity-layer endpoints must lie within the source specimen depth.")
    if s.surface_reference == "explicit":
        surface = s.surface_time_us
    else:
        recorded = manifest.get("metadata", {}).get("water_round_trip_delay_us")
        estimated = manifest.get("estimate", {}).get("water_round_trip_delay_us")
        if recorded is not None and estimated is not None and not np.isclose(recorded, estimated, rtol=0, atol=1e-10):
            raise ValueError("Source water-delay records disagree; choose a verified source or explicit surface reference.")
        surface = recorded if recorded is not None else estimated
        if surface is None:
            raise ValueError("Source has no frozen water-delay metadata. Supply an explicit surface time.")
    if not np.isfinite(surface) or surface < 0:
        raise ValueError("Surface time must be finite and nonnegative on the saved transducer-reference axis.")
    return {"path": Path(path).resolve(), "shape": (ny, nx, nt), "chunk_rows": chunk_rows,
            "surface_time_us": float(surface), "specimen_depth_mm": specimen_depth, **coords}


def _travel_times(settings, surface):
    z = settings.z_min_mm + (np.arange(settings.nz, dtype=np.float64) + 0.5) * (settings.z_max_mm-settings.z_min_mm) / settings.nz
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        if settings.velocity_model == "homogeneous":
            time = surface + 2 * z / (settings.sound_speed_m_s / 1000)
            covered = np.ones(settings.nz, dtype=bool)
            nominal = np.array([surface, surface+np.divide(2*settings.z_max_mm, settings.sound_speed_m_s/1000)])
        else:
            time = np.full(settings.nz, surface)
            previous = 0.
            nominal = [surface]
            for layer in settings.layers:
                thickness = np.clip(z-previous, 0, layer.end_depth_mm-previous)
                time += 2 * thickness / (layer.sound_speed_m_s / 1000)
                nominal.append(nominal[-1]+np.divide(2*(layer.end_depth_mm-previous), layer.sound_speed_m_s/1000))
                previous = layer.end_depth_mm
            covered = z <= previous + 1e-12
            time[~covered] = 0.
    if not np.isfinite(time).all() or not np.isfinite(nominal).all():
        raise ValueError("The declared velocity produces nonfinite travel times; use a representable positive sound speed.")
    if not np.all(np.diff(time[covered]) > 0) or not np.all(np.diff(nominal) > 0):
        raise ValueError("Model-supported travel times must strictly increase at float64 precision. The declared velocity or surface reference cannot resolve these depth samples.")
    return z, time, covered


def _mapping(request, source):
    z, time, model_valid = _travel_times(request.mapping, source["surface_time_us"])
    recorded = source["time_us"]
    valid = model_valid & (time >= recorded[0]-TIME_TOLERANCE_US) & (time <= recorded[-1]+TIME_TOLERANCE_US)
    clamped = np.clip(time, recorded[0], recorded[-1])
    high = np.clip(np.searchsorted(recorded, clamped, side="right"), 0, len(recorded)-1)
    low = np.maximum(high-1, 0)
    denominator = recorded[high]-recorded[low]
    weight = np.divide(clamped-recorded[low], denominator, out=np.zeros_like(time), where=denominator != 0)
    return z, time, model_valid, valid, low, high, weight


def estimate_depth(request, source_manifest, source_path):
    request = _validated(request)
    source = _source(request, source_manifest, source_path)
    z, time, model_valid, valid, _, _, _ = _mapping(request, source)
    ny, nx, nt = source["shape"]
    nz = request.mapping.nz
    array_bytes = nz * ny * nx * 4
    coordinate_bytes = (nx + ny + 2 * nz) * 8
    total = array_bytes * 3 + coordinate_bytes
    cache_bytes = array_bytes * 2
    read_rows = min(8, ny)
    while True:
        chunk_rows = source["chunk_rows"]
        if read_rows % chunk_rows == 0 or chunk_rows % read_rows == 0:
            decoded_rows = min(ny, max(read_rows, chunk_rows))
        else:
            decoded_rows = min(ny, ((read_rows+2*chunk_rows-2)//chunk_rows)*chunk_rows)
        # Include decoder/input buffers, the requested float32 tile and its
        # finite-value mask, and the complete mapped cache. Mapping is not
        # treated as free RAM; only one source field is read at a time.
        peak = (cache_bytes + decoded_rows * nx * nt * 8 + read_rows * nx * nt * 5 +
                read_rows * nx * 64 + ny * nx * 32 + nt * 8 + coordinate_bytes + 16 * 1024 ** 2)
        if peak <= MAX_BYTES or read_rows == 1:
            break
        read_rows //= 2
    if total > MAX_BYTES:
        raise ValueError("Mapped RF, envelope and validity exceed the 512 MiB saved-volume budget. Reduce depth samples.")
    if cache_bytes > MAX_BYTES or peak > MAX_BYTES:
        raise ValueError("Estimated depth-mapping workspace exceeds 512 MiB including its disk-backed cache and source chunks. Reduce depth samples.")
    warnings = []
    if not model_valid.all():
        warnings.append(f"The declared velocity layers cover only {int(model_valid.sum())} of {nz} depth centers. Deeper layers are not extrapolated; unsupported values are masked placeholders.")
    outside_record = model_valid & ~valid
    if outside_record.any():
        warnings.append(f"Travel times for {int(outside_record.sum())} model-supported depth centers lie outside the saved recording centers. Their signal values are masked zeros, not evidence of missing material.")
    if request.mapping.model_evidence == "user_calibrated":
        warnings.append("User-calibrated is a supplied evidence label; the workbench has not independently verified the calibration or depth accuracy.")
    else:
        warnings.append("Depth accuracy depends on the declared surface time and velocity model. This mapping is not an impedance reconstruction or experimental validation.")
    dx = float(source["x_mm"][1]-source["x_mm"][0])
    dy = float(source["y_mm"][1]-source["y_mm"][0])
    dz = (request.mapping.z_max_mm-request.mapping.z_min_mm) / nz
    bounds = [float(source["x_mm"][0]-dx/2), float(source["x_mm"][-1]+dx/2),
              float(source["y_mm"][0]-dy/2), float(source["y_mm"][-1]+dy/2),
              request.mapping.z_min_mm, request.mapping.z_max_mm]
    return {"schema_version": 1, "kind": "sam_depth_volume", "model_version": MODEL_VERSION,
        "shape": [nz, ny, nx], "axis_order": ["z", "y", "x"], "dtype": "float32",
        "tile_rows": 1, "total_rows": nz, "chunks": [1, ny, nx], "read_tile_rows": read_rows,
        "rf_bytes": array_bytes, "envelope_bytes": array_bytes, "valid_mask_bytes": array_bytes,
        "coordinate_bytes": coordinate_bytes, "total_bytes": total, "estimated_peak_bytes": peak,
        "estimated_temporary_bytes": cache_bytes, "workspace_disk_bytes": cache_bytes,
        "mapping_work_cells": 2 * ny * nx * (nt+nz),
        "source_dataset_id": request.source_dataset_id, "source_shape": list(source["shape"]),
        "source_time_range_us": [float(source["time_us"][0]), float(source["time_us"][-1])],
        "surface_time_us": source["surface_time_us"], "depth_range_mm": [request.mapping.z_min_mm, request.mapping.z_max_mm],
        "bounds_mm": bounds, "voxel_pitch_mm": [dx, dy, dz], "voxel_pitch_um": [dx*1000, dy*1000, dz*1000],
        "depth_pitch_mm": dz,
        "model_supported_depth_samples": int(model_valid.sum()), "valid_depth_samples": int(valid.sum()),
        "model_evidence": request.mapping.model_evidence, "time_support_tolerance_us": TIME_TOLERANCE_US,
        "warnings": warnings}


def prepare_depth(request, source_manifest, source_path):
    request = _validated(request)
    estimate = estimate_depth(request, source_manifest, source_path)
    source = _source(request, source_manifest, source_path)
    z, time, model_valid, valid, low, high, weight = _mapping(request, source)
    nz, ny, nx = estimate["shape"]
    root = source["path"].parent
    path = root / f".depth-mapping-cache-{uuid4()}"
    path.mkdir(exist_ok=False)
    cache = None
    try:
        (path / "owner.json").write_text(json.dumps(CACHE_MARKER, sort_keys=True), encoding="utf-8")
        cache = np.memmap(path / "mapped.f32", mode="w+", dtype=np.float32, shape=(2, nz, ny, nx))
        cache[:] = 0
        group = zarr.open_group(str(source["path"] / "data.zarr"), mode="r")
        for field_index, name in enumerate(("rf", "envelope")):
            for start in range(0, ny, estimate["read_tile_rows"]):
                stop = min(ny, start+estimate["read_tile_rows"])
                tile = np.asarray(group[name][start:stop], dtype=np.float32)
                if not np.isfinite(tile).all() or (name == "envelope" and np.any(tile < 0)):
                    raise ValueError(f"Source {name} is nonfinite, missing or physically inconsistent with a saved envelope.")
                for k in np.flatnonzero(valid):
                    mapped = (1-weight[k])*tile[:, :, low[k]].astype(np.float64) + weight[k]*tile[:, :, high[k]]
                    cache[field_index, k, start:stop] = mapped.astype(np.float32)
        cache.flush()
        metadata = {**estimate, "model_depth_valid": model_valid.tolist(),
            "mapping_parameters": request.mapping.model_dump(mode="json", exclude_none=True),
            "source_input_sha256": source_manifest.get("input_sha256"),
            "source_evidence_status": source_manifest.get("evidence_status"),
            "coordinate_units": {"x": "mm", "y": "mm", "z": "mm", "travel_time": "us"},
            "time_mapping_definition": "t(z)=surface_time_us+2*integral_from_0_to_z[dz/(sound_speed_m_s/1000)] in microseconds. Layer starts are cumulative from depth zero; z_min does not reset propagation distance.",
            "travel_time_definition": "Computed round-trip time on the saved transducer-reference axis for model-supported depths, even outside the record. Outside the declared layered model, finite zero is a placeholder and model_depth_valid is false.",
            "valid_mask_definition": "One only where the velocity model covers the depth and computed travel time lies within actual recorded sample centers; zero otherwise. Signal zeros at invalid depths are placeholders, not measured air or absent material.",
            "envelope_processing": "Linear interpolation of the saved analytic envelope, independently of signed RF; not absolute RF and not a new finite-window Hilbert transform.",
            "assumptions": [
                "Time-to-depth resampling under a user-declared velocity and surface-time model. No impedance inversion, full-wave inversion, defect classification or validation of experimental depth accuracy.",
                "Source X/Y scan centers are preserved exactly, including ROI/global coordinates; there is no lateral interpolation or inferred material-label velocity lookup.",
                "Homogeneous speed applies from depth zero. Layered speeds apply to consecutive intervals starting at zero; the model is not extended beyond its final endpoint.",
                "The source water-delay option uses frozen source metadata, not current material constants. Explicit surface time refers to the same saved transducer-reference time axis and is not relative to recording start.",
                "RF remains signed and envelope remains its separately saved analytic magnitude. Both are linearly interpolated between actual saved time centers without extrapolation beyond the record; endpoint comparisons allow only 1e-12 microseconds of numerical tolerance.",
                "No amplitude compensation for attenuation, defocus, transmission losses or spreading is performed. A reflector's mapped depth depends on every overlying declared sound speed and the chosen surface reference.",
                "Output depth pitch is a display/sampling choice, not axial resolution. Velocity assumptions, pulse bandwidth and temporal sampling can dominate depth error or unresolved layers.",
                "Model evidence labels are supplied claims. User-calibrated and synthetic-truth labels are not verified by the workbench, and no synthetic-truth model is inferred from specimen primitives.",
            ]}
        return PreparedDepth(request, estimate, metadata, source["x_mm"], source["y_mm"], z, time,
                             valid, cache, path, source["path"])
    except Exception:
        if cache is not None:
            cache._mmap.close()
        marker = path / "owner.json"
        if marker.is_file():
            try:
                _remove_cache(path, root)
            except (ValueError, json.JSONDecodeError):
                # Only an incomplete ownership write can reach this branch:
                # no cache exists until that initial write has completed.
                if cache is None and path.resolve().parent == root:
                    marker.unlink(missing_ok=True)
                    path.rmdir()
                else:
                    raise
        elif path.resolve().parent == root:
            path.rmdir()
        raise


def iter_depth_slices(prepared, start_slice=0):
    if prepared.cache is None:
        raise ValueError("Depth-mapping workspace has been closed.")
    nz, ny, nx = prepared.estimate["shape"]
    if not isinstance(start_slice, int) or isinstance(start_slice, bool) or not 0 <= start_slice <= nz:
        raise ValueError("Resume slice must be an integer within the mapped depth range.")
    for k in range(start_slice, nz):
        yield k, k+1, {"rf": np.array(prepared.cache[0, k:k+1], dtype=np.float32, order="C"),
                      "envelope": np.array(prepared.cache[1, k:k+1], dtype=np.float32, order="C"),
                      "valid_mask": np.full((1, ny, nx), float(prepared.depth_valid[k]), dtype=np.float32)}
