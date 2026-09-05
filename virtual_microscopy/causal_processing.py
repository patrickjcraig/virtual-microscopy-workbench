"""Read-only float64 time views of independently observed causal columns."""
from pathlib import Path

import numpy as np
import zarr

from .causal_datasets import CausalSamDatasetStore


PRODUCT_UNITS = {"rf": "relative signed real pressure", "imaginary": "relative signed imaginary pressure",
                 "envelope": "relative complex-pressure magnitude"}


def _section(values, time, extent, axis, unit):
    edges = np.r_[time[0], (time[:-1] + time[1:])/2, time[-1]]
    return {"image": values.T.tolist(), "extent": [*extent, float(time[0]), float(time[-1])],
            "horizontal_axis": axis, "horizontal_unit": "mm", "vertical_axis": "time",
            "vertical_unit": "us", "unit": unit, "time_us": time.tolist(),
            "time_bin_edges_us": edges.tolist(), "rf_samples_per_time_bin": 1,
            "time_reduction": "none; actual saved time centers"}


def causal_sam_view(dataset_path: Path, *, x_index=None, y_index=None, time_index=None,
                    product="envelope", gate_start_us=None, gate_end_us=None, gate_mode="peak_envelope"):
    """Validate saved bytes, then select values without invoking a forward kernel.

    Array shapes, codecs, chunks, coordinates and certificates are bounded by the
    store before data are decoded. Reads below retain one canonical row at a time.
    Gate statistics are ordinary floating reductions, not newly certified outputs.
    """
    if product not in PRODUCT_UNITS or gate_mode not in ("peak_envelope", "rms_rf"):
        raise ValueError("Select a supported causal pressure product and time-gate statistic.")
    store = CausalSamDatasetStore(dataset_path.parent)
    manifest = store.verify_complete(dataset_path.name)
    group = zarr.open_group(str(dataset_path / "data.zarr"), mode="r")
    x, y, time = (np.asarray(group[key][:], dtype=np.float64) for key in ("x_mm", "y_mm", "time_us"))
    ny, nx, nt = manifest["shape"]
    xi, yi, ti = (nx//2 if x_index is None else x_index,
                  ny//2 if y_index is None else y_index, nt//2 if time_index is None else time_index)
    if any(type(v) is not int for v in (xi, yi, ti)) or not (0 <= xi < nx and 0 <= yi < ny and 0 <= ti < nt):
        raise ValueError(f"Slice indices must lie within x:0..{nx-1}, y:0..{ny-1}, time:0..{nt-1}.")
    t0 = float(time[0]) if gate_start_us is None else gate_start_us
    t1 = float(time[-1]) if gate_end_us is None else gate_end_us
    if not np.isfinite([t0, t1]).all() or t1 <= t0:
        raise ValueError("A time gate requires finite start < end.")
    if t0 < time[0] or t1 > time[-1]:
        raise ValueError("The time gate must lie within the actual saved recording centers.")
    lo, hi = int(np.searchsorted(time, t0, side="left")), int(np.searchsorted(time, t1, side="right"))
    if lo >= hi:
        raise ValueError("The gate contains no saved time centers.")
    # Extents come from the frozen requested ROI, not a recomputed grid.
    a = manifest["request"]["acquisition"]
    roi = a.get("roi_mm") or [0, 0, *manifest["request"]["twin"]["size_mm"][:2]]
    extent = [roi[0], roi[2], roi[1], roi[3]]
    xy, yt, gated = np.empty((ny, nx)), np.empty((ny, nt)), np.empty((ny, nx))
    selected = group[product]
    gate_key = "envelope" if gate_mode == "peak_envelope" else "rf"
    gate_source = group[gate_key]
    for row in range(ny):
        values = np.asarray(selected[row, :, :], dtype=np.float64)
        xy[row], yt[row] = values[:, ti], values[xi]
        if row == yi:
            xt = values.copy()
        gate_values = values[:, lo:hi] if product == gate_key else np.asarray(gate_source[row, :, lo:hi], dtype=np.float64)
        gated[row] = gate_values.max(axis=1) if gate_mode == "peak_envelope" else np.sqrt(np.mean(gate_values**2, axis=1))
    ascan = {key: np.asarray(group[key][yi, xi, :], dtype=np.float64).tolist() for key in PRODUCT_UNITS}
    error = float(group["error_bound"][yi, xi])
    class_id = int(group["class_index"][yi, xi])
    unit = PRODUCT_UNITS[product]
    diagnostics = manifest["class_certificates"][str(class_id)]["diagnostics"]
    return {
        "xy": {"image": xy.tolist(), "extent_mm": extent, "unit": unit},
        "xt": _section(xt, time, extent[:2], "x", unit),
        "yt": _section(yt, time, extent[2:], "y", unit),
        "ascan": {**ascan, "amplitude": ascan["rf"], "time_us": time.tolist(),
                  "probe_mm": [float(x[xi]), float(y[yi])], "error_bound": error},
        "cscan": {"image": gated.tolist(), "extent_mm": extent,
                  "unit": "peak complex-pressure magnitude" if gate_mode == "peak_envelope" else "RMS real pressure"},
        "cursor": {"x_index": xi, "y_index": yi, "time_index": ti, "x_mm": float(x[xi]),
                   "y_mm": float(y[yi]), "time_us": float(time[ti]), "error_bound": error,
                   "class_index": class_id, **{key: values[ti] for key, values in ascan.items()}},
        "gate": {"start_us": t0, "end_us": t1, "mode": gate_mode, "sample_count": hi-lo,
                 "actual_start_us": float(time[lo]), "actual_end_us": float(time[hi-1]),
                 "processing_source": "saved complex-pressure magnitude" if gate_mode == "peak_envelope" else "saved real pressure",
                 "certificate_scope": "The stored bound covers the original complex-pressure and magnitude samples; this derived statistic has no additional numerical certificate."},
        "certificate": {"selected_bound": error, "diagnostics": diagnostics,
                        "volume_max_bound": manifest["total_error_bound"], "requested_tolerance": a["absolute_tolerance"],
                        "definition": "Per-column absolute error for saved complex pressure and magnitude under the declared scalar model, excluding geometry/material uncertainty and experimental accuracy."},
        "metadata": {"axes": ["y", "x", "time"], "shape": [ny, nx, nt], "product": product,
                     "dtype": "float64", "observation_model": "independent_columns_v1",
                     "evidence_status": manifest["evidence_status"],
                     "time_axis": "Gamma onset reference plus propagation; repeated returns have no unique physical depth.",
                     "display_reduction": "No time pooling; slices and traces use the actual saved time centers."},
    }
