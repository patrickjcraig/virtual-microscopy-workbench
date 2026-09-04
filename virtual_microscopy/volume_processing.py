"""Bounded, read-only views of frozen SAM RF acquisitions.

Time remains time. The envelope is the saved complex-pulse magnitude, not a
depth reconstruction. Display pooling never modifies the authoritative arrays.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import threading

import numpy as np
import zarr

_gate_cache: OrderedDict = OrderedDict()
_gate_lock = threading.Lock()


def _coordinates(group):
    return tuple(np.asarray(group[key][:], dtype=float) for key in ("x_mm", "y_mm", "time_us"))


def _edges(values):
    step = float(values[1] - values[0])
    return [float(values[0] - step / 2), float(values[-1] + step / 2)]


def _image(values, extent, unit):
    return {"image": np.asarray(values).tolist(), "extent_mm": extent, "unit": unit}


def _section(values, time, spatial_extent, axis):
    # Store values as [position,time]; show time vertically, with exact bins.
    stride = max(1, int(np.ceil(len(time) / 512)))
    starts = np.arange(0, len(time), stride)
    image = np.maximum.reduceat(values, starts, axis=1).T
    time_edges = np.r_[time[0], (time[starts[1:]] + time[starts[1:] - 1]) / 2, time[-1]]
    return {"image": image.tolist(), "extent": [*spatial_extent, float(time[0]), float(time[-1])],
            "horizontal_axis": axis, "horizontal_unit": "mm", "vertical_axis": "time",
            "vertical_unit": "us", "unit": "relative echo amplitude",
            "time_bin_edges_us": time_edges.tolist(), "rf_samples_per_time_bin": stride,
            "time_reduction": "maximum saved analytic envelope per time bin"}


def gate_image(dataset_path: Path, group, time, start_us, end_us, mode):
    if not np.isfinite([start_us, end_us]).all() or end_us <= start_us:
        raise ValueError("Gate end must exceed its finite start time.")
    tolerance = 1e-9
    if start_us < time[0] - tolerance or end_us > time[-1] + tolerance:
        raise ValueError("Gate bounds must lie within the saved RF sample time range.")
    if mode not in ("peak_envelope", "rms_rf"):
        raise ValueError("Supported gates are peak_envelope and rms_rf.")
    lo = int(np.searchsorted(time, start_us - tolerance, side="left"))
    hi = int(np.searchsorted(time, end_us + tolerance, side="right"))
    if hi <= lo:
        raise ValueError("The gate contains no recorded samples. Widen the gate or move it to a sample.")
    key = (str(dataset_path.resolve()), lo, hi, mode)
    with _gate_lock:
        cached = _gate_cache.get(key)
        if cached is not None:
            _gate_cache.move_to_end(key)
            return cached.copy(), hi - lo
    source = group["envelope" if mode == "peak_envelope" else "rf"]
    result = np.empty(source.shape[:2], dtype=np.float32)
    # Keep the reduction bounded even when a complete record is requested.
    row_count = max(1, min(8, 8_000_000 // (source.shape[1] * (hi - lo) * 8)))
    for y0 in range(0, source.shape[0], row_count):
        y1 = min(source.shape[0], y0 + row_count)
        tile = np.asarray(source[y0:y1, :, lo:hi])
        if mode == "peak_envelope":
            result[y0:y1] = tile.max(axis=2)
        else:
            result[y0:y1] = np.sqrt(np.mean(np.square(tile, dtype=np.float64), axis=2))
    with _gate_lock:
        _gate_cache[key] = result.copy()
        while len(_gate_cache) > 16:
            _gate_cache.popitem(last=False)
    return result, hi - lo


def sam_view(dataset_path: Path, *, x_index=None, y_index=None, time_index=None,
             gate_start_us=None, gate_end_us=None, gate_mode="peak_envelope"):
    """Read requested slices and a post hoc gate; no forward solver is called."""
    group = zarr.open_group(str(dataset_path / "data.zarr"), mode="r")
    x, y, time = _coordinates(group)
    ny, nx, nt = group["rf"].shape
    xi = nx // 2 if x_index is None else x_index
    yi = ny // 2 if y_index is None else y_index
    ti = nt // 2 if time_index is None else time_index
    if not (0 <= xi < nx and 0 <= yi < ny and 0 <= ti < nt):
        raise ValueError(f"Slice indices must be inside x:0..{nx-1}, y:0..{ny-1}, time:0..{nt-1}.")
    t0 = float(time[0]) if gate_start_us is None else gate_start_us
    t1 = float(time[-1]) if gate_end_us is None else gate_end_us
    gated, count = gate_image(dataset_path, group, time, t0, t1, gate_mode)
    x_extent, y_extent = _edges(x), _edges(y)
    extent = [*x_extent, *y_extent]
    envelope = group["envelope"]
    xy = np.empty((ny, nx), dtype=np.float32)
    yt = np.empty((ny, nt), dtype=np.float32)
    # A narrow column still decompresses its whole Zarr row chunk. Read one
    # canonical row chunk at a time instead of scheduling all chunks at once.
    for y0 in range(0, ny, envelope.chunks[0]):
        y1 = min(ny, y0 + envelope.chunks[0])
        xy[y0:y1] = envelope[y0:y1, :, ti]
        yt[y0:y1] = envelope[y0:y1, xi, :]
    amplitude_unit = "relative echo amplitude"
    return {
        "xy": _image(xy, extent, amplitude_unit),
        "xt": _section(np.asarray(envelope[yi, :, :]), time, x_extent, "x"),
        "yt": _section(yt, time, y_extent, "y"),
        "ascan": {"time_us": time.tolist(), "amplitude": np.asarray(group["rf"][yi, xi, :]).tolist(),
                  "envelope": np.asarray(envelope[yi, xi, :]).tolist(),
                  "probe_mm": [float(x[xi]), float(y[yi])]},
        "cscan": _image(gated, extent, amplitude_unit),
        "cursor": {"x_index": xi, "y_index": yi, "time_index": ti,
                   "x_mm": float(x[xi]), "y_mm": float(y[yi]), "time_us": float(time[ti])},
        "gate": {"start_us": t0, "end_us": t1, "mode": gate_mode, "sample_count": count,
                 "processing_source": "saved envelope" if gate_mode == "peak_envelope" else "saved signed RF"},
        "metadata": {"axes": ["y", "x", "t"], "shape": [ny, nx, nt],
                     "evidence_status": "Synthetic reduced-order acquisition; not experimentally calibrated.",
                     "time_axis": "Recorded round-trip time, not reconstructed depth.",
                     "display_reduction": "Only X-time and Y-time display sections are max-pooled to at most 512 time bins."},
    }
