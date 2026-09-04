"""Read saved projection planes, sinograms and detector-row profiles."""
from pathlib import Path

import numpy as np
import zarr


def _edges(values):
    step = float(values[1] - values[0])
    return [float(values[0] - step / 2), float(values[-1] + step / 2)]


def _finite_values(values, shape, label):
    values = np.asarray(values)
    if values.shape != shape or not np.isfinite(values).all():
        raise ValueError(f"Saved {label} data is missing or corrupted; its shape and values must be finite.")
    return values


def _invalid_log_mask(values, shape):
    mask = _finite_values(values, shape, "log-validity mask")
    if not np.all((mask == 0) | (mask == 1)):
        raise ValueError("Saved log-validity mask is corrupted; only binary 0 and 1 values are valid.")
    return mask == 0


def xray_view(path: Path, manifest: dict, *, view_index=0, detector_row=None, product="transmission"):
    if product not in ("transmission", "line_integrals", "counts"):
        raise ValueError("Choose transmission, line_integrals or counts.")
    group = zarr.open_group(str(path / "data.zarr"), mode="r")
    nviews, rows, cols = group[product].shape
    row = rows // 2 if detector_row is None else detector_row
    if not (0 <= view_index < nviews and 0 <= row < rows):
        raise ValueError(f"View index must be 0..{nviews-1} and detector row 0..{rows-1}.")
    u = _finite_values(group["u_mm"][:], (cols,), "detector-u coordinates")
    v = _finite_values(group["v_mm"][:], (rows,), "detector-v coordinates")
    angles = _finite_values(group["angles_deg"][:], (nviews,), "view angles")
    if rows < 2 or cols < 2:
        raise ValueError("Saved detector shape is corrupted; both detector dimensions require at least two samples.")
    plane = _finite_values(group[product][view_index], (rows, cols), product)
    invalid = _invalid_log_mask(group["valid_mask"][view_index], (rows, cols))
    sinogram = np.empty((nviews, cols), dtype=np.float32)
    invalid_sinogram = np.empty((nviews, cols), dtype=bool)
    # One view is one authoritative chunk. Do not launch a full stack of chunk
    # decompressions concurrently for a single requested detector row.
    for i in range(nviews):
        sinogram[i] = _finite_values(group[product][i, row, :], (cols,), product)
        invalid_sinogram[i] = _invalid_log_mask(group["valid_mask"][i, row, :], (cols,))
    acquisition = manifest["request"]["acquisition"]
    step = float(angles[1] - angles[0]) if nviews > 1 else float(acquisition["angle_span_deg"])
    angle_edges = np.r_[angles - step / 2, angles[-1] + step / 2]
    unit = {"transmission": "I / I0", "line_integrals": "dimensionless -ln(I / I0)",
            "counts": "photon counts" if acquisition.get("noise", True) else "expected photon counts"}[product]
    extent = [*_edges(u), *_edges(v)]
    pose = {name: _finite_values(group[name][view_index], (3,), name).tolist() for name in (
        "ray_direction_xyz", "detector_center_mm", "detector_u_xyz", "detector_v_xyz")}
    return {
        "projection": {"image": plane.tolist(), "extent_mm": extent, "unit": unit,
                       "invalid_mask": invalid.tolist(), "min": float(plane.min()), "max": float(plane.max())},
        "sinogram": {"image": sinogram.tolist(), "extent": [*_edges(u), float(angle_edges[0]), float(angle_edges[-1])],
                     "unit": unit, "angles_deg": angles.tolist(), "angle_bin_edges_deg": angle_edges.tolist(),
                     "invalid_mask": invalid_sinogram.tolist()},
        "profile": {"u_mm": u.tolist(), "values": plane[row].tolist(), "unit": unit,
                    "invalid_mask": invalid[row].tolist()},
        "cursor": {"view_index": view_index, "angle_deg": float(angles[view_index]),
                   "detector_row": row, "v_mm": float(v[row])},
        "pose": pose, "invalid_pixels": int(invalid.sum()), "product": product,
        "metadata": {"axes": ["view", "v", "u"], "shape": [nviews, rows, cols],
                     "evidence_status": "Synthetic monoenergetic parallel-beam acquisition; not experimentally calibrated.",
                     "coordinate_convention": "u/v are detector-local mm about the saved detector center. Basis vectors are unit vectors.",
                     "line_integral_processing": "-ln(counts / incident photons); only zero counts use a 0.5-count substitute and remain invalid. Positive fractional expected counts and negative noisy line integrals are retained.",
                     "sinogram_sampling": "Rows are the saved view angles. Display bin edges extend half an angular step around each view; they do not add measurements.",
                     "reconstruction_status": "Projection acquisition, not an xyz reconstruction.",
                     "warnings": manifest.get("metadata", {}).get("warnings", manifest.get("estimate", {}).get("warnings", []))},
    }
