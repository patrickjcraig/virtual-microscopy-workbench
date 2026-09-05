"""Independent reflector timing and declared-velocity sensitivity validation.

Run: python -m tools.verify_depth_mapping artifacts/depth-validation-new
The new output directory is never overwritten. No acoustic solver or primitive
velocity lookup is used to construct these analytical input waveforms.
"""

import argparse
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import numpy as np
import zarr

from virtual_microscopy.depth_mapping import prepare_depth, iter_depth_slices


def _waveform_source(root, times, surface_time):
    identifier = str(uuid4())
    path = root / identifier
    path.mkdir()
    time = 0.25+np.arange(1351)/1000
    pulse = np.zeros(time.shape, dtype=np.complex128)
    for arrival, amplitude in zip(times, (-0.9, 0.7)):
        pulse += amplitude*np.exp(-0.5*((time-arrival)/0.012)**2)*np.exp(2j*np.pi*25*(time-arrival))
    group = zarr.open_group(str(path / "data.zarr"), mode="w", zarr_format=3)
    for name, trace in (("rf", pulse.real), ("envelope", abs(pulse))):
        values = np.broadcast_to(trace, (16, 16, len(time))).astype(np.float32)
        group.create_array(name, data=values, chunks=(8, 16, len(time)))
    for name, values in (("x_mm", 41+(np.arange(16)+0.5)/2),
                         ("y_mm", 22+(np.arange(16)+0.5)/2), ("time_us", time)):
        group.create_array(name, data=values)
    manifest = {"dataset_id": identifier, "kind": "sam_rf_volume", "complete": True, "state": "completed",
        "shape": [16, 16, len(time)],
        "evidence_status": "Independent continuous-waveform timing fixture; not a production acquisition or experimental record.",
        "request": {"twin": {"size_mm": [60, 60, 2]}},
        "metadata": {"water_round_trip_delay_us": surface_time},
        "estimate": {"water_round_trip_delay_us": surface_time}}
    (path / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path, manifest


def _map(path, manifest, expected_depths, **settings):
    request = {"source_dataset_id": manifest["dataset_id"], "mapping": {
        "nz": 512, "z_max_mm": 2, "sound_speed_m_s": 4000,
        "model_evidence": "synthetic_truth", "model_note": "The validation waveform has analytically prescribed travel times.",
        **settings}}
    prepared = prepare_depth(request, manifest, path)
    try:
        profiles = {name: [] for name in ("rf", "envelope", "valid_mask")}
        for _, _, arrays in iter_depth_slices(prepared):
            for name in profiles:
                profiles[name].append(float(arrays[name][0, 8, 8]))
        z = prepared.z_mm.copy()
        peaks = []
        for expected in expected_depths:
            candidates = np.flatnonzero(abs(z-expected) < 0.07)
            peak = candidates[np.argmax(np.asarray(profiles["envelope"])[candidates])]
            peaks.append({"depth_mm": float(z[peak]), "rf_amplitude": profiles["rf"][peak],
                          "envelope_amplitude": profiles["envelope"][peak]})
        return {"mapping": request["mapping"], "peaks": peaks, "estimate": prepared.estimate}, {
            "z_mm": z, "travel_time_us": prepared.travel_time_us.copy(),
            **{name: np.asarray(values) for name, values in profiles.items()}}
    finally:
        prepared.close()


def run_validation(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = perf_counter()
    surface = 0.35
    true_depths = np.array([0.6, 1.4])
    path, manifest = _waveform_source(output, surface+2*true_depths/4, surface)
    baseline, baseline_profiles = _map(path, manifest, true_depths)
    fast, fast_profiles = _map(path, manifest, true_depths*1.1, sound_speed_m_s=4400,
        model_evidence="user_assumed", model_note="A deliberately 10% high assumed speed for sensitivity testing.")
    late, late_profiles = _map(path, manifest, true_depths-0.04,
        surface_reference="explicit", surface_time_us=surface+0.02,
        model_evidence="user_assumed", model_note="A deliberately 20 ns late surface reference for sensitivity testing.")
    layered_depths = np.array([0.25, 1.25])
    # Independent explicit path lengths: first reflector in 2500 m/s material;
    # second after 0.5 mm at 2500 m/s plus 0.75 mm at 5000 m/s.
    layered_path, layered_manifest = _waveform_source(output, [surface+2*0.25/2.5,
                                                              surface+2*0.5/2.5+2*0.75/5], surface)
    layered, layered_profiles = _map(layered_path, layered_manifest, layered_depths,
        velocity_model="layered", layers=[{"end_depth_mm": 0.5, "sound_speed_m_s": 2500},
                                           {"end_depth_mm": 2, "sound_speed_m_s": 5000}])
    peaks = lambda case: np.array([p["depth_mm"] for p in case["peaks"]])
    report = {"evidence_status": "Independent synthetic timing validation; no experimental depth-accuracy claim.",
        "waveform": {"carrier_mhz": 25, "gaussian_sigma_us": 0.012, "sample_interval_us": 0.001,
                     "record_time_range_us": [0.25, 1.6], "surface_time_us": surface,
                     "construction": "Sum of analytic Gaussian complex pulses; signed RF and complex magnitude stored independently."},
        "true_homogeneous_reflector_depths_mm": true_depths.tolist(),
        "true_layered_reflector_depths_mm": layered_depths.tolist(),
        "cases": {"matching_homogeneous": baseline, "speed_10_percent_high": fast,
                  "surface_20_ns_late": late, "matching_layered": layered},
        "metrics": {"depth_sample_pitch_mm": 2/512,
            "homogeneous_peak_errors_mm": (peaks(baseline)-true_depths).tolist(),
            "layered_peak_errors_mm": (peaks(layered)-layered_depths).tolist(),
            "speed_error_peak_shifts_mm": (peaks(fast)-peaks(baseline)).tolist(),
            "speed_error_theoretical_shifts_mm": (true_depths*0.1).tolist(),
            "surface_error_peak_shifts_mm": (peaks(late)-peaks(baseline)).tolist(),
            "surface_error_theoretical_shift_mm": -0.04},
        "runtime_seconds": round(perf_counter()-started, 3)}
    arrays = {}
    for case, profiles in (("matching_homogeneous", baseline_profiles), ("speed_high", fast_profiles),
                           ("surface_late", late_profiles), ("matching_layered", layered_profiles)):
        arrays.update({f"{case}_{name}": values for name, values in profiles.items()})
    np.savez_compressed(output / "depth_profiles.npz", **arrays)
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New output directory; existing directories are refused.")
    args = parser.parse_args()
    print(json.dumps(run_validation(args.output), indent=2))
