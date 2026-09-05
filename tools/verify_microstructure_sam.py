"""Saved-array experiment for an assumed H100 microstructure patch.

Run: python -m tools.verify_microstructure_sam artifacts/microstructure-sam-new
The output directory must not exist. This reports synthetic signal effects and
voxel-center aliasing sensitivity, not experimental defect detectability.
"""

import argparse
import json
import multiprocessing
from pathlib import Path
from time import perf_counter
import traceback

import numpy as np

from tools.build_h100_example import h100
from virtual_microscopy.datasets import json_sha256
from virtual_microscopy.hbm import compose_hbm
from virtual_microscopy.physics import MaterialGrid, acoustic_echoes
from virtual_microscopy.sam_volume import iter_sam_tiles, prepare_sam


def _peak_process_bytes():
    """Observed whole-process high-water mark, separate from numerical estimate."""
    import sys
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return int(counters.PeakWorkingSetSize)
        return None
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value if sys.platform == "darwin" else value * 1024)
    except (ImportError, AttributeError):
        return None


def _case_worker(output, request, target_id, connection):
    """Isolate each acquisition so process peak measurements are comparable."""
    try:
        output = Path(output)
        started = perf_counter()
        prepared = prepare_sam(request)
        shape = prepared.estimate["shape"]
        rf, envelope = np.empty(shape, np.float32), np.empty(shape, np.float32)
        for lo, hi, tile_rf, tile_envelope in iter_sam_tiles(prepared):
            rf[lo:hi], envelope[lo:hi] = tile_rf, tile_envelope
        elapsed = perf_counter() - started
        target = next(obj for obj in prepared.request.twin.objects if obj.id == target_id)
        ix, iy = int(np.argmin(abs(prepared.x_mm-target.center_mm[0]))), int(np.argmin(abs(prepared.y_mm-target.center_mm[1])))
        ys, xs = prepared.grid.image_slices
        gy, gx = ys.start + iy, xs.start + ix
        ray = MaterialGrid(prepared.grid.labels[gy:gy+1, gx:gx+1],
                           prepared.grid.pitch_mm * [1, 1, prepared.grid.labels.shape[2]],
                           prepared.grid.pitch_mm, [], prepared.grid.origin_mm + [gx*prepared.grid.pitch_mm[0], gy*prepared.grid.pitch_mm[1], 0])
        echoes = acoustic_echoes(ray, request["acquisition"]["frequency_mhz"], request["acquisition"]["focus_mm"])
        peak_bytes = _peak_process_bytes()
        np.savez_compressed(output.with_suffix(".npz"), rf=rf, envelope=envelope,
                            x_mm=prepared.x_mm, y_mm=prepared.y_mm, time_us=prepared.time_us)
        result = {"name": output.name, "request_sha256": json_sha256(prepared.request.model_dump(mode="json")),
                  "wall_seconds": elapsed, "observed_process_peak_bytes": peak_bytes,
                  "estimate": prepared.estimate, "target_id": target_id,
                  "target_center_mm": list(target.center_mm), "probe_index_xy": [ix, iy],
                  "probe_center_xy_mm": [float(prepared.x_mm[ix]), float(prepared.y_mm[iy])],
                  "probe_primary_echo_time_us": echoes.times_us.tolist(),
                  "probe_primary_echo_signed_pressure": echoes.amplitudes.tolist(),
                  "rf_min": float(rf.min()), "rf_max": float(rf.max()),
                  "envelope_max": float(envelope.max())}
        output.with_suffix(".json").write_text(json.dumps({**result,
            "request": prepared.request.model_dump(mode="json"), "metadata": prepared.metadata}, indent=2), encoding="utf-8")
        connection.send({"result": result})
    except BaseException:
        connection.send({"error": traceback.format_exc()})
    finally:
        connection.close()


def _case(output, request, target_id):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_case_worker, args=(str(output), request, target_id, child))
    process.start()
    child.close()
    try:
        if not parent.poll(180):
            process.terminate()
            raise TimeoutError("SAM validation case exceeded 180 seconds.")
        message = parent.recv()
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            raise RuntimeError("SAM validation worker did not exit after returning its result.")
        if "error" in message:
            raise RuntimeError(message["error"])
        if process.exitcode:
            raise RuntimeError(f"SAM validation worker exited with code {process.exitcode}.")
        return message["result"]
    finally:
        if process.is_alive():
            process.terminate()
        process.join()
        parent.close()


def run_validation(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = perf_counter()
    intact = compose_hbm(h100(), "hbm-6", {"microstructure": {}})
    defective = compose_hbm(intact, "hbm-6", {"microstructure": {"defects": [
        {"id": "missing", "kind": "missing_bump", "row": 3, "column": 2, "layer_index": 8}]}})
    target_id = "hbm-6-mb-08-r03-c02"
    cx, cy = intact["hbm_assemblies"][5]["center_xy_mm"]
    cases = []
    for depth in (256, 512, 1024):
        for label, twin in (("intact", intact), ("missing_bump", defective)):
            request = {"twin": twin, "acquisition": {"scan_nx": 64, "scan_ny": 64,
                "depth_samples": depth, "frequency_mhz": 100, "sample_rate_mhz": 800,
                "record_start_us": .2, "record_duration_us": .5, "focus_mm": .3,
                "roi_mm": [cx-.06, cy-.09, cx+.06, cy+.09], "include_defects": True}}
            result = _case(output / f"{label}-{depth}", request, target_id)
            cases.append(result)
            print(f"Finished {result['name']}: {result['wall_seconds']:.2f} s", flush=True)
    effects, sensitivity = [], []
    for depth in (256, 512, 1024):
        with np.load(output / f"intact-{depth}.npz") as nominal, np.load(output / f"missing_bump-{depth}.npz") as missing:
            difference = missing["rf"] - nominal["rf"]
            baseline = nominal["rf"]
            maximum_index = np.unravel_index(np.argmax(abs(difference)), difference.shape)
            effects.append({"depth_samples": depth,
                "rf_difference_relative_l2": float(np.linalg.norm(difference.ravel()) / np.linalg.norm(baseline.ravel())),
                "rf_difference_rms": float(np.sqrt(np.mean(difference.astype(np.float64)**2))),
                "rf_difference_max_abs": float(np.max(abs(difference))),
                "max_difference_at_xy_time": [float(nominal["x_mm"][maximum_index[1]]),
                    float(nominal["y_mm"][maximum_index[0]]), float(nominal["time_us"][maximum_index[2]])],
                "envelope_difference_max_abs": float(np.max(abs(missing["envelope"]-nominal["envelope"])))})
    for label in ("intact", "missing_bump"):
        with np.load(output / f"{label}-1024.npz") as saved_reference:
            reference = saved_reference["rf"]
        for depth in (256, 512):
            with np.load(output / f"{label}-{depth}.npz") as coarse:
                delta = coarse["rf"]-reference
                sensitivity.append({"case": label, "depth_samples": depth, "reference_depth_samples": 1024,
                    "rf_relative_l2_to_finest": float(np.linalg.norm(delta.ravel()) / np.linalg.norm(reference.ravel())),
                    "rf_max_abs_difference_to_finest": float(np.max(abs(delta)))})
    report = {"evidence_status": "Synthetic reduced-order SAM numerical experiment, not experimental validation.",
        "description": "An authored epoxy-filled missing bump changes one assumed HBM 6 inter-die connection. Full specimen depth, other geometry and Gaussian lateral context are retained.",
        "sampling_note": "Extents/pitch and grid-sensitivity results are sampling measures. Neither a nonzero numerical difference nor the source geometry establishes physical resolution or defect detectability.",
        "grid_sensitivity_note": "The 1024-depth grid is a comparison reference, not continuum truth. Voxel-center aliasing need not converge monotonically.",
        "resource_note": "Each acquisition runs in a fresh child process. Observed peak process working set includes interpreter/imports and retained validation arrays; numerical peak estimate has a different scope. NPZ serialization is excluded from reported acquisition wall time and peak observation.",
        "output_note": "NPZ files preserve full float32 signed RF and separately saved analytic envelope with global X/Y and transducer-reference time coordinates. JSON files preserve exact requests and processing metadata; these are validation artifacts, not catalog datasets.",
        "cases": cases, "missing_bump_effect": effects, "grid_sensitivity": sensitivity,
        "total_wall_seconds": perf_counter()-started}
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "missing_bump_effect": effects, "grid_sensitivity": sensitivity}, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    run_validation(parser.parse_args().output)
