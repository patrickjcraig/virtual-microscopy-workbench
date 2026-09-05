"""Read-only spatial views of SAM amplitudes mapped by a declared velocity model."""
from pathlib import Path

import numpy as np
import zarr

from .depth_datasets import DepthDatasetStore


def _coordinates(group, name, count):
    array = group[name]
    values = np.asarray(array[:])
    if array.dtype != np.dtype("float64") or values.shape != (count,) or not np.isfinite(values).all():
        raise ValueError(f"Saved {name} coordinates are missing or corrupted.")
    delta = np.diff(values)
    if count < 2 or np.any(delta <= 0) or not np.allclose(delta, delta[0], atol=1e-10, rtol=1e-8):
        raise ValueError(f"Saved {name} coordinates must be uniformly increasing.")
    return values, [float(values[0] - delta[0] / 2), float(values[-1] + delta[0] / 2)]


def _image(values, valid, extent, horizontal, vertical, unit):
    supported = values[valid]
    return {"image": values.tolist(), "valid_mask": valid.tolist(),
            "invalid_mask": (~valid).tolist(), "extent_mm": extent,
            "horizontal_axis": horizontal, "vertical_axis": vertical, "unit": unit,
            "min": float(supported.min()) if supported.size else 0.,
            "max": float(supported.max()) if supported.size else 0.}


def depth_view(path: Path, manifest: dict, *, x_index=None, y_index=None, z_index=None, product="envelope"):
    if product not in ("rf", "envelope"):
        raise ValueError("SAM depth product must be rf or envelope.")
    group = zarr.open_group(str(path / "data.zarr"), mode="r")
    shape = tuple(manifest["shape"])
    arrays = {name: group[name] for name in ("rf", "envelope", "valid_mask")}
    if len(shape) != 3 or min(shape) < 2 or any(a.shape != shape or a.dtype != np.dtype("float32") for a in arrays.values()):
        raise ValueError("Saved SAM depth arrays have a corrupted shape or type.")
    nz, ny, nx = shape
    # This checks only small coordinate vectors and frozen metadata. Viewing
    # remains independent of the original raw-time source and current solver.
    DepthDatasetStore._verify_coordinates(group, manifest)
    x, xe = _coordinates(group, "x_mm", nx)
    y, ye = _coordinates(group, "y_mm", ny)
    z, ze = _coordinates(group, "z_mm", nz)
    time_array = group["travel_time_us"]
    times = np.asarray(time_array[:])
    metadata = manifest.get("metadata", {})
    model_valid = np.asarray(metadata.get("model_depth_valid"))
    if (time_array.dtype != np.dtype("float64") or times.shape != (nz,) or not np.isfinite(times).all()
            or model_valid.shape != (nz,) or model_valid.dtype != np.dtype("bool")):
        raise ValueError("Saved depth travel times or velocity-model support are missing or corrupted.")
    if np.any(np.diff(times[model_valid]) <= 0) or np.any(times[~model_valid] != 0):
        raise ValueError("Saved depth travel times do not match their velocity-model support.")
    t0, t1 = metadata["source_time_range_us"]
    tolerance = metadata["time_support_tolerance_us"]
    depth_valid = model_valid & (times >= t0 - tolerance) & (times <= t1 + tolerance)
    ix, iy, iz = (nx // 2 if x_index is None else x_index,
                  ny // 2 if y_index is None else y_index,
                  nz // 2 if z_index is None else z_index)
    if any(isinstance(v, bool) or not isinstance(v, (int, np.integer)) for v in (ix, iy, iz)):
        raise ValueError("SAM depth cursor indices must be integers.")
    if not (0 <= ix < nx and 0 <= iy < ny and 0 <= iz < nz):
        raise ValueError(f"SAM depth cursor must be within x=0..{nx-1}, y=0..{ny-1}, z=0..{nz-1}.")
    xz, yz = np.empty((nz, nx), dtype=np.float32), np.empty((nz, ny), dtype=np.float32)
    xzv, yzv = np.empty((nz, nx), dtype=bool), np.empty((nz, ny), dtype=bool)
    for k in range(nz):
        planes = {name: np.asarray(a[k]) for name, a in arrays.items()}
        if any(not np.isfinite(p).all() for p in planes.values()):
            raise ValueError("Saved SAM depth data is missing or corrupted; absent chunks are not valid zeros.")
        mask = planes["valid_mask"]
        if not np.all(mask == float(depth_valid[k])):
            raise ValueError("Saved SAM depth validity mask does not match the velocity model and source recording interval.")
        valid = mask == 1
        if (np.any(planes["envelope"] < 0) or np.any(planes["rf"][~valid] != 0)
                or np.any(planes["envelope"][~valid] != 0)):
            raise ValueError("Saved SAM depth envelope or masked placeholders are corrupted.")
        plane = planes[product]
        xz[k], yz[k], xzv[k], yzv[k] = plane[iy], plane[:, ix], valid[iy], valid[:, ix]
        if k == iz:
            xy, xyv = plane, valid
            cursor_rf, cursor_envelope = float(planes["rf"][iy, ix]), float(planes["envelope"][iy, ix])
    unit = "relative signed pressure" if product == "rf" else "relative echo amplitude"
    mapping = manifest["request"]["mapping"]
    return {
        "xy": _image(xy, xyv, [*xe, *ye], "x", "y", unit),
        "xz": _image(xz, xzv, [*xe, *ze], "x", "z", unit),
        "yz": _image(yz, yzv, [*ye, *ze], "y", "z", unit),
        "cursor": {"x_index": int(ix), "y_index": int(iy), "z_index": int(iz),
            "x_mm": float(x[ix]), "y_mm": float(y[iy]), "z_mm": float(z[iz]),
            "rf": cursor_rf, "envelope": cursor_envelope, "value": float(xy[iy, ix]),
            "valid": bool(xyv[iy, ix]), "model_valid": bool(model_valid[iz]),
            "sample_time_us": float(times[iz]) if model_valid[iz] else None},
        "profiles": {axis: {"coordinate_mm": coords.tolist(), "values": values.tolist(), "valid_mask": valid.tolist()}
            for axis, coords, values, valid in (("x", x, xy[iy], xyv[iy]),
                ("y", y, xy[:, ix], xyv[:, ix]), ("z", z, xz[:, ix], xzv[:, ix]))},
        "metadata": {"shape": list(shape), "axis_order": ["z", "y", "x"], "product": product, "unit": unit,
            "bounds_mm": [*xe, *ye, *ze], "voxel_pitch_mm": [float(x[1]-x[0]), float(y[1]-y[0]), float(z[1]-z[0])],
            "source_dataset_id": manifest["request"]["source_dataset_id"],
            "source_name": manifest.get("source_manifest", {}).get("request", {}).get("twin", {}).get("name", "Saved SAM source"),
            "mapping": mapping, "model_evidence": mapping["model_evidence"],
            "surface_time_us": metadata.get("surface_time_us"),
            "travel_time_us": [float(t) if valid else None for t, valid in zip(times, model_valid)],
            "model_depth_valid": model_valid.tolist(),
            "evidence_status": "SAM amplitude mapped through a declared one-dimensional velocity model; not impedance or full-wave reconstruction.",
            "sampling_note": "Depth spacing is sampling of an assumed travel-time map, not measured acoustic resolution.",
            "warnings": metadata.get("warnings", []), "processing": metadata},
    }
