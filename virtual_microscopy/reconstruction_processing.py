"""Bounded read-only orthogonal views of saved spatial attenuation volumes."""
from pathlib import Path

import numpy as np
import zarr


def _coordinates(group, name, count):
    array = group[name]
    values = np.asarray(array[:])
    if array.dtype != np.dtype("float64") or values.shape != (count,) or not np.isfinite(values).all():
        raise ValueError(f"Saved {name} coordinates are missing or corrupted.")
    delta = np.diff(values)
    if count < 2 or np.any(delta <= 0) or not np.allclose(delta, delta[0], rtol=1e-8, atol=1e-10):
        raise ValueError(f"Saved {name} coordinates must be uniformly increasing.")
    return values, [float(values[0] - delta[0] / 2), float(values[-1] + delta[0] / 2)]


def _image(values, coverage, extent, horizontal, vertical):
    return {"image": values.tolist(), "coverage": coverage.tolist(),
            "invalid_mask": (coverage < 1 - 1e-6).tolist(), "extent_mm": extent,
            "horizontal_axis": horizontal, "vertical_axis": vertical,
            "unit": "mm^-1", "min": float(values.min()), "max": float(values.max())}


def reconstruction_view(path: Path, manifest: dict, *, x_index=None, y_index=None, z_index=None):
    group = zarr.open_group(str(path / "data.zarr"), mode="r")
    attenuation, coverage = group["attenuation"], group["coverage"]
    shape = tuple(manifest["shape"])
    if (len(shape) != 3 or min(shape) < 2 or attenuation.shape != shape or coverage.shape != shape or
            attenuation.dtype != np.dtype("float32") or coverage.dtype != np.dtype("float32")):
        raise ValueError("Saved reconstruction arrays have a corrupted shape or type.")
    nz, ny, nx = shape
    x, xe = _coordinates(group, "x_mm", nx)
    y, ye = _coordinates(group, "y_mm", ny)
    z, ze = _coordinates(group, "z_mm", nz)
    ix, iy, iz = (nx // 2 if x_index is None else x_index,
                  ny // 2 if y_index is None else y_index,
                  nz // 2 if z_index is None else z_index)
    if any(isinstance(v, bool) or not isinstance(v, (int, np.integer)) for v in (ix, iy, iz)):
        raise ValueError("Reconstruction cursor indices must be integers.")
    if not (0 <= ix < nx and 0 <= iy < ny and 0 <= iz < nz):
        raise ValueError(f"Reconstruction cursor must be within x=0..{nx-1}, y=0..{ny-1}, z=0..{nz-1}.")
    xz = np.empty((nz, nx), dtype=np.float32)
    yz = np.empty((nz, ny), dtype=np.float32)
    xzc, yzc = np.empty_like(xz), np.empty_like(yz)
    # Z is the authoritative chunk axis. Read one plane at a time instead of
    # scheduling hundreds of concurrent decompressions for orthogonal slices.
    for k in range(nz):
        plane, support = np.asarray(attenuation[k]), np.asarray(coverage[k])
        if not np.isfinite(plane).all() or not np.isfinite(support).all():
            raise ValueError("Saved reconstruction data is missing or corrupted; incomplete chunks are not valid zeros.")
        if np.any((support < 0) | (support > 1)):
            raise ValueError("Saved coverage values must be between zero and one.")
        xz[k], yz[k], xzc[k], yzc[k] = plane[iy], plane[:, ix], support[iy], support[:, ix]
        if k == iz:
            xy, xyc = plane, support
    request = manifest["request"]
    settings = request["reconstruction"]
    source = manifest.get("source_manifest", {})
    pitch = [float(x[1] - x[0]), float(y[1] - y[0]), float(z[1] - z[0])]
    return {
        "xy": _image(xy, xyc, [*xe, *ye], "x", "y"),
        "xz": _image(xz, xzc, [*xe, *ze], "x", "z"),
        "yz": _image(yz, yzc, [*ye, *ze], "y", "z"),
        "cursor": {"x_index": int(ix), "y_index": int(iy), "z_index": int(iz),
                   "x_mm": float(x[ix]), "y_mm": float(y[iy]), "z_mm": float(z[iz]),
                   "attenuation": float(xy[iy, ix]), "coverage": float(xyc[iy, ix])},
        "profiles": {axis: {"coordinate_mm": coords.tolist(), "values": values.tolist(),
                             "coverage": support.tolist()}
                     for axis, coords, values, support in (("x", x, xy[iy], xyc[iy]),
                         ("y", y, xy[:, ix], xyc[:, ix]), ("z", z, xz[:, ix], xzc[:, ix]))},
        "metadata": {"axis_order": ["z", "y", "x"], "shape": list(shape), "unit": "mm^-1",
            "bounds_mm": [*xe, *ye, *ze], "voxel_pitch_mm": pitch,
            "source_dataset_id": request["source_dataset_id"],
            "source_name": source.get("request", {}).get("twin", {}).get("name", "Saved X-ray projections"),
            "filter": settings["filter"], "frequency_cutoff": settings["frequency_cutoff"],
            "coverage_definition": "Fraction of angular views with detector support. Partial coverage is masked; stored zero placeholders there are not measured air. Coverage is not confidence or experimental validity.",
            "sampling_note": "Reconstruction voxel pitch is independent of source detector pitch and is not measured spatial resolution.",
            "evidence_status": "CPU filtered backprojection from synthetic parallel-beam measurements; no experimental validation.",
            "warnings": manifest.get("metadata", {}).get("warnings", manifest.get("estimate", {}).get("warnings", [])),
            "processing": manifest.get("metadata", {})},
    }
