"""Reproducible HBM path-method and sampling experiment, without measured data.

Run: python -m tools.verify_continuous_hbm artifacts/continuous-hbm-new
Each recording runs in a fresh child process. Output directories are new-only.
"""
import argparse
import json
import multiprocessing
from pathlib import Path
from time import perf_counter
import traceback

import numpy as np

from tools.build_hbm_microstructure_example import microstructure_example
from tools.verify_microstructure_sam import _peak_process_bytes
from tools.verify_microstructure_xray import continuous_ray
from virtual_microscopy.datasets import json_sha256, material_snapshot, solver_identity
from virtual_microscopy.hbm import compose_hbm
from virtual_microscopy.sam_volume import iter_sam_tiles, prepare_sam


def _worker(output, config, channel):
    try:
        output = Path(output)
        started = perf_counter()
        prepared = prepare_sam(config)
        rf = np.empty(prepared.estimate["shape"], dtype=np.float32)
        envelope = np.empty_like(rf)
        for lo, hi, tile_rf, tile_envelope in iter_sam_tiles(prepared):
            rf[lo:hi], envelope[lo:hi] = tile_rf, tile_envelope
        elapsed, peak = perf_counter() - started, _peak_process_bytes()
        request = prepared.request.model_dump(mode="json", exclude_none=True)
        record = {"case": output.name, "request_sha256": json_sha256(request),
                  "elapsed_seconds": elapsed, "observed_process_peak_bytes": peak,
                  "estimate": prepared.estimate, "rf_min": float(rf.min()),
                  "rf_max": float(rf.max()), "envelope_max": float(envelope.max())}
        np.savez_compressed(output.with_suffix(".npz"), rf=rf, envelope=envelope,
                            x_mm=prepared.x_mm, y_mm=prepared.y_mm, time_us=prepared.time_us)
        frozen = {**record, "request": request, "metadata": prepared.metadata,
                  "materials": material_snapshot(),
                  "solver": solver_identity(prepared.estimate["model_version"])}
        output.with_suffix(".json").write_text(json.dumps(frozen, indent=2), encoding="utf-8")
        channel.send({"result": record})
    except BaseException:
        channel.send({"error": traceback.format_exc()})
    finally:
        channel.close()


def run_case(output, config):
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(str(output), config, sender))
    process.start()
    sender.close()
    started = perf_counter()
    try:
        while not receiver.poll(10):
            if not process.is_alive():
                raise RuntimeError(f"{output.name} exited without a result: {process.exitcode}")
            if perf_counter() - started > 300:
                raise TimeoutError(f"{output.name} exceeded the 300-second experiment limit")
        response = receiver.recv()
        process.join(timeout=10)
        if process.is_alive():
            raise RuntimeError("Experiment process did not exit after returning its result")
        if "error" in response:
            raise RuntimeError(response["error"])
        return response["result"]
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=10)
        receiver.close()


def relative_l2(values, reference):
    denominator = float(np.linalg.norm(np.asarray(reference, dtype=float).ravel()))
    return float(np.linalg.norm((np.asarray(values, dtype=float)-reference).ravel()) / denominator)


def probe_trace(data, xy):
    """Declared bilinear interpolation of saved RF at a fixed physical probe."""
    x, y = data["x_mm"], data["y_mm"]
    assert x[0] <= xy[0] <= x[-1] and y[0] <= xy[1] <= y[-1]
    ix = min(max(int(np.searchsorted(x, xy[0])) - 1, 0), len(x)-2)
    iy = min(max(int(np.searchsorted(y, xy[1])) - 1, 0), len(y)-2)
    wx, wy = (xy[0]-x[ix])/(x[ix+1]-x[ix]), (xy[1]-y[iy])/(y[iy+1]-y[iy])
    rf = data["rf"]
    return ((1-wy)*((1-wx)*rf[iy, ix]+wx*rf[iy, ix+1]) +
            wy*((1-wx)*rf[iy+1, ix]+wx*rf[iy+1, ix+1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    intact = microstructure_example()
    defective = compose_hbm(intact, "hbm-6", {"microstructure": {"defects": [
        {"id": "missing-gap8-r3-c2", "kind": "missing_bump", "row": 3,
         "column": 2, "layer_index": 8}]}})
    acquisition = {"scan_nx": 64, "scan_ny": 64, "depth_samples": 1024,
                   "roi_mm": [49.44, 39.91, 49.56, 40.09], "frequency_mhz": 100,
                   "fractional_bandwidth": .5, "focus_mm": .55,
                   "record_start_us": .2, "record_duration_us": .5,
                   "sample_rate_mhz": 800, "water_standoff_mm": 0,
                   "include_defects": True}
    cases = [(f"voxel-{depth}", intact, {"path_model": "voxel_centers_v1", "depth_samples": depth})
             for depth in (256, 512, 1024)]
    cases += [("continuous-128", intact, {"path_model": "continuous_columns_v1", "depth_samples": 128}),
              ("continuous-1024", intact, {"path_model": "continuous_columns_v1"}),
              ("continuous-missing", defective, {"path_model": "continuous_columns_v1"}),
              ("continuous-32-800", intact, {"path_model": "continuous_columns_v1", "scan_nx": 32, "scan_ny": 32}),
              ("continuous-32-1600", intact, {"path_model": "continuous_columns_v1", "scan_nx": 32, "scan_ny": 32, "sample_rate_mhz": 1600})]
    records = []
    for name, twin, changes in cases:
        record = run_case(args.output / name, {"twin": twin, "acquisition": {**acquisition, **changes}})
        records.append(record)
        print(json.dumps({key: record[key] for key in ("case", "elapsed_seconds", "observed_process_peak_bytes")}), flush=True)
    arrays = {name: np.load(args.output / f"{name}.npz") for name, _, _ in cases}
    fine = arrays["continuous-1024"]
    for key in ("rf", "envelope", "x_mm", "y_mm", "time_us"):
        np.testing.assert_array_equal(arrays["continuous-128"][key], fine[key])
    assert records[3]["request_sha256"] != records[4]["request_sha256"]
    for key in ("x_mm", "y_mm", "time_us"):
        np.testing.assert_array_equal(arrays["continuous-missing"][key], fine[key])
    delta = arrays["continuous-missing"]["rf"] - fine["rf"]
    assert np.max(abs(delta)) > 0
    coarse, temporal = arrays["continuous-32-800"], arrays["continuous-32-1600"]
    np.testing.assert_array_equal(coarse["time_us"], temporal["time_us"][::2])
    for key in ("x_mm", "y_mm"):
        np.testing.assert_array_equal(coarse[key], temporal[key])
    from virtual_microscopy.column_paths import build_column_paths, column_xray_integrals
    x = np.array([49.475, 49.509, 49.525])
    y = np.array([39.95, 40., 40.05])
    xray_checks = []
    for name, twin in (("intact", intact), ("missing_bump", defective)):
        actual = column_xray_integrals(build_column_paths(twin, x, y), 80)
        expected = np.array([[continuous_ray(twin, float(xx), float(yy))["optical_depth"] for xx in x] for yy in y])
        np.testing.assert_allclose(actual, expected, atol=1e-10, rtol=1e-10)
        xray_checks.append({"case": name, "maximum_optical_depth_error": float(np.max(abs(actual-expected))),
                            "optical_depth": actual.tolist(), "independent_optical_depth": expected.tolist()})
    probe = [49.525, 40.05]
    report = {
        "evidence": "Synthetic numerical path and sampling comparison; no measured reference or experimental calibration.",
        "protocol": acquisition, "cases": records,
        "continuous_inactive_z_arrays_identical": True,
        "continuous_inactive_z_request_hashes_different": True,
        "voxel_rf_relative_l2_to_continuous": {str(depth): relative_l2(arrays[f"voxel-{depth}"]["rf"], fine["rf"]) for depth in (256, 512, 1024)},
        "continuous_missing_rf_relative_l2": relative_l2(arrays["continuous-missing"]["rf"], fine["rf"]),
        "continuous_missing_rf_max_abs_difference": float(np.max(abs(delta))),
        "continuous_missing_envelope_max_abs_difference": float(np.max(abs(arrays["continuous-missing"]["envelope"]-fine["envelope"]))),
        "probe_xy_mm": probe,
        "lateral_32_to_64_probe_rf_relative_l2": relative_l2(probe_trace(coarse, probe), probe_trace(fine, probe)),
        "temporal_800_to_1600_rf_relative_l2_at_shared_samples": relative_l2(coarse["rf"], temporal["rf"][:, :, ::2]),
        "xray_xy_mm": {"x": x.tolist(), "y": y.tolist()}, "xray_independent_checks": xray_checks,
        "interpretation": "Continuous mode removes vertical voxel-center boundary quantization for these authored primitives. The lateral probe comparison uses declared bilinear interpolation without registration or phase alignment. Two lateral/time grids establish sensitivity only, not convergence, physical resolution, or defect detectability. Acoustic propagation and material assumptions remain reduced-order and uncalibrated.",
        "resource_scope": "Fresh-process peaks include interpreter/imports and full retained validation RF/envelope arrays. Acquisition timing and peaks exclude NPZ serialization. Numerical preflight has a different scope from whole-process memory."
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}, indent=2))


if __name__ == "__main__":
    main()
