"""Deliver exactly two finite coherent observations of the saved v0.13 HBM pair.

Run only after root authorizes backend freeze AND completed native QA:
  python -m tools.verify_observation_delivery artifacts/v015-observation-delivery

Refuses any existing output directory before network or source reads. Creates no
acquisition and performs no forward propagation. Independent rational oracles
cover every distinct ordered nine-neighbor waveform/bound signature; all other
columns must match their representative bytes. Full arrays are retained only for
the two pinned, bounded delivery fixtures, not as a production processing model.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import platform
import struct
from time import monotonic, perf_counter, sleep
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4
import zipfile

import numpy as np

from tools.verify_acquisition_comparisons import API, source_hashes, verify_archive, write_bytes, write_json
from tools.verify_causal_comparison_delivery import controlled_pair_identity, directory_hashes, source_arrays
from tools.verify_layered_acoustics import legacy_deliveries
from virtual_microscopy.causal_datasets import CausalSamDatasetStore
from virtual_microscopy.datasets import canonical_json, now_iso
from virtual_microscopy.observation_datasets import ObservationStore
from virtual_microscopy.observation_processing import observation_view


PREFIX = "/api/v2/observations"
SIGNALS = ("rf", "imaginary", "envelope")
BOUNDS = ("source_propagation", "complex_arithmetic", "complex_total", "magnitude_arithmetic", "magnitude_total")
COORDINATES = ("x_mm", "y_mm", "time_us")
IDS = {"hbm6-intact": "9e166ad9-6229-4c83-99a1-e831cc750fff",
       "hbm6-missing-bump": "e4cdc105-9658-4c6d-9d19-b08a29a12bcd"}
SOURCE_SHAPE, OUTPUT_SHAPE = [64, 32, 1601], [62, 30, 1601]
WEIGHTS = tuple(Fraction(n, 16) for n in (1, 2, 1, 2, 4, 2, 1, 2, 1))
HELPERS = ("verify_observation_delivery.py", "verify_causal_comparison_delivery.py",
           "verify_acquisition_comparisons.py", "verify_layered_acoustics.py",
           "build_hbm_microstructure_example.py", "build_h100_example.py")


def bits(value):
    return struct.unpack("<Q", struct.pack("<d", float(value)))[0]


def fraction(value):
    return Fraction.from_float(float(value))


def typed_digest(value):
    return hashlib.sha256(np.asarray(value, dtype="<f8", order="C").tobytes()).hexdigest()


def assert_exact(actual, expected):
    a, b = np.asarray(actual, dtype=np.float64), np.asarray(expected, dtype=np.float64)
    assert a.shape == b.shape and a.tobytes() == b.tobytes()


def upward(q):
    value = float(q)
    if fraction(value) < q:
        value = math.nextafter(value, math.inf)
    assert math.isfinite(value) and fraction(value) >= q
    assert value == 0 or fraction(math.nextafter(value, -math.inf)) < q
    return value


def nearest(q, value):
    """Independent exact midpoint/tie check, including signed underflow."""
    if q == 0:
        assert bits(value) == 0
        return
    assert math.copysign(1, value) == (1 if q > 0 else -1)
    q, value = abs(q), abs(float(value))
    center = fraction(value)
    lo = (fraction(math.nextafter(value, -math.inf))+center)/2 if value else Fraction(0)
    nxt = math.nextafter(value, math.inf)
    hi = ((fraction(nxt)+center)/2 if math.isfinite(nxt) else center+fraction(math.ulp(value))/2)
    assert lo <= q <= hi
    if q == lo or q == hi:
        assert bits(value) & 1 == 0


def magnitude_radius(real, imaginary, magnitude):
    """Exact rational squares, independent of the production integer bracket."""
    square = fraction(real)**2+fraction(imaginary)**2
    center, radius = fraction(magnitude), fraction(math.ulp(float(magnitude)))
    for doublings in range(9):
        if max(Fraction(0), center-radius)**2 <= square <= (center+radius)**2:
            return radius, doublings
        radius *= 2
    raise AssertionError("Independent saved magnitude bracket failed")


def observation_catalog(api):
    result, offset = [], 0
    while True:
        page = api.json(f"{PREFIX}/jobs?limit=100&offset={offset}")
        result.extend(page["jobs"])
        if len(page["jobs"]) < 100:
            break
        offset += 100
        assert offset <= 9999
    assert len(result) == len({item["dataset_id"] for item in result})
    return result


def wait_job(api, identifier, directory, started, timeout=1200):
    deadline, last, updates = monotonic()+timeout, None, []
    with (directory/"job-timeline.jsonl").open("xb") as stream:
        while monotonic() < deadline:
            job = api.json(f"{PREFIX}/jobs/{identifier}")
            state = (job["status"], job["completed_rows"])
            if state != last:
                record = {"observed_at": now_iso(), "elapsed_seconds": perf_counter()-started, "job": job}
                updates.append(record)
                stream.write(canonical_json(record)+b"\n")
                stream.flush()
                print(json.dumps({"dataset_id": identifier, "status": state[0], "rows": state[1],
                                  "total_rows": job["total_rows"]}), flush=True)
                last = state
            if job["status"] == "completed":
                return job, updates, perf_counter()-started
            if job["status"] in {"cancelled", "failed", "interrupted"}:
                raise AssertionError(f"Observation delivery did not complete: {job}")
            sleep(.25)
    raise TimeoutError(f"Observation delivery exceeded {timeout} seconds: {identifier}")


def read_source(root, identifier):
    # Bound the exact fixture BEFORE the helper's intentional whole-array read.
    manifest = CausalSamDatasetStore(root).verify_complete(identifier)
    assert manifest["shape"] == SOURCE_SHAPE
    return source_arrays(root, identifier)


def read_observation(root, identifier):
    store = ObservationStore(root)
    manifest = store.verify_complete(identifier)
    assert manifest["shape"] == OUTPUT_SHAPE and manifest["dtype"] == "float64"
    group = store._open_checked(identifier, manifest)
    arrays = {key: np.asarray(group[key][:]) for key in (*SIGNALS, *BOUNDS, *COORDINATES)}
    for key, values in arrays.items():
        assert values.dtype == np.dtype("float64") and np.isfinite(values).all()
        expected = (OUTPUT_SHAPE if key in SIGNALS else OUTPUT_SHAPE[:2] if key in BOUNDS
                    else [{"x_mm": 30, "y_mm": 62, "time_us": 1601}[key]])
        assert list(values.shape) == expected
    for y in range(OUTPUT_SHAPE[0]):
        record = manifest["completed_chunks"][str(y)]
        for key in (*SIGNALS, *BOUNDS):
            assert typed_digest(arrays[key][y]) == record["sha256"][key]
    for key in COORDINATES:
        assert typed_digest(arrays[key]) == manifest["coordinates_sha256"][key]
    return manifest, arrays


def spatial_contract(source_manifest, source, manifest, output):
    plan = manifest["estimate"]
    assert plan["source_manifest"] == source_manifest
    assert plan["source_shape"] == SOURCE_SHAPE and plan["shape"] == OUTPUT_SHAPE
    for key in COORDINATES:
        assert_exact(output[key], source[key] if key == "time_us" else source[key][1:-1])
    assert plan["source_indices"] == {"x": list(range(1, 31)), "y": list(range(1, 63)), "time": list(range(1601))}
    x, y = source["x_mm"], source["y_mm"]
    extent = [float((fraction(a[i])+fraction(a[j]))/2)
              for a, i, j in ((x, 0, 1), (x, -2, -1), (y, 0, 1), (y, -2, -1))]
    assert_exact(plan["extent_mm"], extent)
    assert_exact(plan["source_extent_mm"], source_manifest["estimate"]["extent_mm"])
    op = plan["operator"]
    assert op["model"] == "binomial_3x3_coherent_v1"
    assert op["weight_numerators"] == [1, 2, 1, 2, 4, 2, 1, 2, 1] and op["weight_denominator"] == 16
    assert op["offsets_yx"] == [[dy, dx] for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
    assert tuple(fraction(v) for v in op["weights"]) == WEIGHTS
    assert op["additional_phase_radians"] == 0
    for key in ("x_mm", "y_mm"):
        centers = source[key]
        expected = [[float(centers[i+d]-centers[i]) for d in (-1, 0, 1)] for i in range(1, len(centers)-1)]
        assert_exact(plan["physical_neighbor_offsets"][key], expected)
    return {"coordinates_byte_exact": True, "retained_cell_walls_exact_fraction_midpoints": extent,
            "full_nine_position_support": True, "additional_phase_radians": 0,
            "source_indices_exact": True, "no_time_resampling": True}


def neighborhood_groups(source_manifest, source):
    """Exact ordered signatures; class numbers themselves are only local labels."""
    tokens = {}
    for key, record in source_manifest["class_certificates"].items():
        tokens[int(key)] = tuple(record["waveform_sha256"][signal] for signal in SIGNALS) + (
            struct.pack("<d", record["diagnostics"]["total_error_bound"]).hex(),)
    groups = {}
    ny, nx = source["class_index"].shape
    for y in range(ny-2):
        for x in range(nx-2):
            classes = tuple(int(source["class_index"][y+dy, x+dx]) for dy in range(3) for dx in range(3))
            signature = tuple(tokens[c] for c in classes)
            groups.setdefault(signature, []).append((y, x))
    return groups


def neighborhood_oracle(source_manifest, source, output):
    """Use exact ordered waveform/bound signatures, never approximate classes."""
    started = perf_counter()
    groups = neighborhood_groups(source_manifest, source)
    records = []
    for signature, columns in groups.items():
        y, x = columns[0]
        exact_source = sum((weight*fraction(source["error_bound"][y+dy, x+dx])
                            for weight, (dy, dx) in zip(WEIGHTS, ((a, b) for a in range(3) for b in range(3)))), Fraction(0))
        maximum_error, maximum_radius, max_doublings = Fraction(0), Fraction(0), 0
        for t in range(1601):
            real, imag, h = (float(output[key][y, x, t]) for key in SIGNALS)
            qr = sum((weight*fraction(source["rf"][y+dy, x+dx, t])
                      for weight, (dy, dx) in zip(WEIGHTS, ((a, b) for a in range(3) for b in range(3)))), Fraction(0))
            qi = sum((weight*fraction(source["imaginary"][y+dy, x+dx, t])
                      for weight, (dy, dx) in zip(WEIGHTS, ((a, b) for a in range(3) for b in range(3)))), Fraction(0))
            nearest(qr, real)
            nearest(qi, imag)
            assert bits(real) == bits(float(qr)) and bits(imag) == bits(float(qi))
            maximum_error = max(maximum_error, abs(fraction(real)-qr)+abs(fraction(imag)-qi))
            radius, doublings = magnitude_radius(real, imag, h)
            maximum_radius, max_doublings = max(maximum_radius, radius), max(max_doublings, doublings)
        s, c, d = upward(exact_source), upward(maximum_error), upward(maximum_radius)
        total = upward(fraction(s)+fraction(c))
        expected = (s, c, total, d, upward(fraction(total)+fraction(d)))
        for key, value in zip(BOUNDS, expected):
            assert bits(output[key][y, x]) == bits(value)
        for yy, xx in columns:
            for key in SIGNALS:
                assert output[key][yy, xx].tobytes() == output[key][y, x].tobytes()
            for key in BOUNDS:
                assert bits(output[key][yy, xx]) == bits(output[key][y, x])
        records.append({"signature_sha256": hashlib.sha256(canonical_json(signature)).hexdigest(),
            "representative_yx": [y, x], "equivalent_columns": len(columns),
            "source_class_ids": source["class_index"][y:y+3, x:x+3].tolist(),
            "canonical_minimal_bounds": dict(zip(BOUNDS, expected)), "max_magnitude_doublings": max_doublings,
            "checked_time_centers": 1601})
    assert sum(item["equivalent_columns"] for item in records) == 1860
    return {"columns_covered": 1860, "distinct_ordered_neighborhoods": len(records), "representatives": records,
            "exact_weighted_component_checks": len(records)*1601*2,
            "exact_magnitude_bracket_checks": len(records)*1601,
            "all_five_minimal_maps_checked": True, "equivalent_full_traces_and_maps_byte_equal": True,
            "elapsed_seconds": perf_counter()-started,
            "scope": "Exact represented-input numerical verification, not a continuous-space quadrature or physical accuracy certificate."}


def check_view(view, arrays, manifest, cursor, gate, mode, product):
    x, y, ti = cursor
    time = arrays["time_us"]
    lo, hi = np.searchsorted(time, gate[0], side="left"), np.searchsorted(time, gate[1], side="right")
    assert view["shape"] == OUTPUT_SHAPE and view["metadata"]["source_required"] is False
    assert view["cursor"]["source_x_index"] == x+1 and view["cursor"]["source_y_index"] == y+1
    for key, index in (("x_mm", x), ("y_mm", y), ("time_us", ti)):
        assert view["cursor"][key] == arrays[key][index]
        assert_exact(view["coordinates"][key], arrays[key])
    for key in SIGNALS:
        assert_exact(view["ascan"][key], arrays[key][y, x])
        assert view["cursor"][key] == arrays[key][y, x, ti]
    assert_exact(view["xy"]["image"], arrays[product][:, :, ti])
    assert_exact(view["xt"]["image"], arrays[product][y].T)
    assert_exact(view["yt"]["image"], arrays[product][:, x, :].T)
    assert view["xt"]["rf_samples_per_time_bin"] == view["yt"]["rf_samples_per_time_bin"] == 1
    for key in BOUNDS:
        assert view["certificate"]["selected_bounds"][key] == arrays[key][y, x]
        assert view["certificate"]["volume_max_bounds"][key] == float(arrays[key].max())
        assert_exact(view["bound_maps"][key], arrays[key])
    values = arrays["envelope" if mode == "peak_envelope" else "rf"][:, :, lo:hi]
    if mode == "peak_envelope":
        assert_exact(view["cscan"]["image"], values.max(axis=2))
    else:
        # Ordinary independent direct RMS is safe for this bounded pressure fixture.
        np.testing.assert_allclose(view["cscan"]["image"], np.sqrt(np.mean(values*values, axis=2)), rtol=2e-15, atol=1e-300)
    assert view["gate"]["sample_count"] == hi-lo
    assert view["gate"]["actual_start_us"] == time[lo] and view["gate"]["actual_end_us"] == time[hi-1]
    assert_exact(view["extent_mm"], manifest["estimate"]["extent_mm"])


def exported_history(api, root, directory, identifier, manifest, arrays, view):
    source_path = root/identifier
    before = directory_hashes(source_path)
    archive_path = directory/"observation-raw.zip"
    write_bytes(archive_path, api.bytes(f"{PREFIX}/datasets/{identifier}/export"))
    archive_check = verify_archive(archive_path, source_path, before)
    offline_root = directory/"offline-data-root"
    offline_root.mkdir()
    copied = offline_root/identifier
    copied.mkdir()
    with zipfile.ZipFile(archive_path) as archive:
        for name in archive.namelist():
            pure = PurePosixPath(name)
            assert not pure.is_absolute() and ".." not in pure.parts
            target = copied.joinpath(*pure.parts)
            assert target.resolve().is_relative_to(copied.resolve())
            target.parent.mkdir(parents=True, exist_ok=True)
            write_bytes(target, archive.read(name))
    assert directory_hashes(copied) == before
    assert not (offline_root/manifest["request"]["source_dataset_id"]).exists()
    from virtual_microscopy import observation_datasets
    with observation_datasets._cache_lock:
        observation_datasets._row_cache.clear()  # Cold verification in this QA process only.
    with ExitStack() as traps:
        for name in ("virtual_microscopy.observation_math.observe_row", "virtual_microscopy.observation_math.verify_row",
                     "virtual_microscopy.observation_plan.plan_observation", "virtual_microscopy.observation_plan.source_context",
                     "virtual_microscopy.observation_plan.read_source", "virtual_microscopy.observation_datasets.source_context",
                     "virtual_microscopy.observation_datasets.observation_identity",
                     "virtual_microscopy.causal_datasets.CausalSamDatasetStore.verify_complete",
                     "virtual_microscopy.causal_datasets.CausalSamDatasetStore.open_arrays",
                     "virtual_microscopy.causal_sam.prepare_causal_sam", "virtual_microscopy.layered_time.causal_gamma_response"):
            traps.enter_context(patch(name, side_effect=AssertionError("Historical observation must not synthesize or open its source")))
        store = ObservationStore(offline_root)
        assert store.verify_complete(identifier) == manifest
        assert len(store.safe_export_files(identifier)) == len(before)
        historical = observation_view(offline_root, identifier, x_index=9, y_index=42, time_index=265,
                                     product="imaginary", gate_start_us=.32, gate_end_us=.4, gate_mode="peak_envelope")
        assert historical == view
        group = store._open_checked(identifier, manifest)
        for key in (*SIGNALS, *BOUNDS, *COORDINATES):
            assert_exact(group[key][:], arrays[key])
    assert directory_hashes(source_path) == before == directory_hashes(copied)
    write_json(directory/"derived-file-hashes.json", before)
    write_json(directory/"offline-view.json", historical)
    return {**archive_check, "typed_export_arrays_byte_exact": True, "cold_source_free_read_and_view": True,
            "current_synthesis_and_source_access_traps_passed": True, "original_and_copied_files_unchanged": True,
            "offline_verification_scope": "Hashes, exact source propagation/composition and magnitude brackets; component conversion maxima were established with source waveforms in the independent oracle."}


def rejected_tolerance(api, identifier):
    before, legacy = observation_catalog(api), api.json("/api/v2/jobs")
    body = {"kind": "sam_coherent_observation_volume", "name": "Rejected below-source tolerance control",
            "source_dataset_id": identifier, "operator": "binomial_3x3_coherent_v1", "absolute_tolerance": 1e-12}
    try:
        with urlopen(Request(api.base+PREFIX+"/jobs", data=canonical_json(body),
                             headers={"Content-Type": "application/json"}), timeout=180) as response:
            raise AssertionError(f"Insufficient tolerance unexpectedly accepted: HTTP {response.status}")
    except HTTPError as exc:
        assert exc.code == 422
        error = json.loads(exc.read())
    assert observation_catalog(api) == before and api.json("/api/v2/jobs") == legacy
    return {"request": body, "http_status": 422, "response": error,
            "observation_catalog_unchanged": True, "old_job_catalog_unchanged": True}


def effect_support(a, b, da, db, identity):
    target = identity["nominal_target"]
    cx, cy = target["center_mm"][:2]
    rx, ry = np.asarray(target["size_mm"][:2])/2
    footprint = ((a["x_mm"][None, :]-cx)/rx)**2+((a["y_mm"][:, None]-cy)/ry)**2 <= 1
    support = np.zeros((62, 30), dtype=bool)
    for dy in range(3):
        for dx in range(3):
            support |= footprint[dy:dy+62, dx:dx+30]
    for key in SIGNALS:
        assert a[key][~footprint].tobytes() == b[key][~footprint].tobytes()
        assert da[key][~support].tobytes() == db[key][~support].tobytes()
    changed = np.any(da["rf"] != db["rf"], axis=2)
    assert changed.any() and not np.any(changed & ~support)
    maximum = max(float(np.max(np.abs(db["rf"][y]-da["rf"][y]))) for y in range(62))
    return {"source_feature_footprint_columns": int(footprint.sum()), "derived_full_support_columns": int(support.sum()),
            "changed_derived_rf_columns_diagnostic": int(changed.sum()), "maximum_derived_rf_difference_diagnostic": maximum,
            "source_signals_unchanged_outside_feature": True, "derived_signals_byte_identical_outside_nine_position_support": True,
            "scope": "Controlled synthetic sensitivity and locality, not physical detection, resolution or enclosed change classification."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8767")
    parser.add_argument("--data-root", type=Path, default=Path("artifacts/volumes-v07"))
    parser.add_argument("--active-instance", type=Path, default=Path("artifacts/ACTIVE_INSTANCE.json"))
    args = parser.parse_args()
    output, root = args.output.resolve(), args.data_root.resolve()
    output.mkdir(parents=True, exist_ok=False)  # Before network or source reads.
    started = perf_counter()
    run = {"schema_version": 1, "run_id": str(uuid4()), "created_at": now_iso(), "version": "0.15.0",
           "api_url": args.url, "data_root": str(root), "no_overwrite": True,
           "helper_source_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in HELPERS},
           "runtime": {"python": platform.python_version(), "numpy": np.__version__, "platform": platform.platform()},
           "source_dataset_ids": IDS, "required_shape": OUTPUT_SHAPE,
           "evidence_status": "Finite coherent filter of synthetic saved columns; no new acquisition, calibrated beam or physical detection claim."}
    write_json(output/"run.json", run)
    try:
        project = Path(__file__).resolve().parents[1]
        active, previous, legacy_ids = legacy_deliveries(args.active_instance.resolve(), project, root)
        assert all(active["delivered_causal_volume_dataset_ids"][key] == value for key, value in IDS.items())
        api = API(args.url)
        health, old_jobs = api.json("/api/health"), api.json("/api/v2/jobs")
        assert health["status"] == "ok" and health["version"] == "0.15.0"
        old_observations = observation_catalog(api)
        assert not any(j["status"] in {"queued", "running", "cancelling"} for j in [*old_jobs["jobs"], *old_observations])
        causal_ids = {j["dataset_id"] for j in old_jobs["jobs"] if j.get("kind") == "sam_causal_rf_volume"}
        assert len(causal_ids) == 6
        preserved_ids = sorted(set(legacy_ids)|causal_ids|{j["dataset_id"] for j in old_observations})
        before_files = {identifier: directory_hashes(root/identifier) for identifier in preserved_ids}
        assert sum(len(before_files[identifier]) for identifier in legacy_ids) == 524
        report_names = ("layered-reports", "comparisons", "xray-comparisons", "causal-comparisons")
        reports = {name: directory_hashes(root/name) for name in report_names}
        assert len(reports["layered-reports"]) == 19
        for filename, value in (("active-instance-snapshot.json", active), ("previous-deliveries.json", previous),
            ("health.json", health), ("old-jobs-before.json", old_jobs), ("observation-jobs-before.json", old_observations),
            ("preserved-files-before.json", before_files), ("report-files-before.json", reports)):
            write_json(output/filename, value)
        am, a = read_source(root, IDS["hbm6-intact"])
        bm, b = read_source(root, IDS["hbm6-missing-bump"])
        identity = controlled_pair_identity(am, bm, a, b)
        write_json(output/"controlled-source-pair.json", identity)
        write_json(output/"rejected-tolerance.json", rejected_tolerance(api, IDS["hbm6-intact"]))
        records, outputs, created = [], {}, {}
        for slug, sm, source in (("hbm6-intact", am, a), ("hbm6-missing-bump", bm, b)):
            directory = output/slug
            directory.mkdir()
            request = {"kind": "sam_coherent_observation_volume", "name": f"v0.15 finite coherent {slug}",
                       "source_dataset_id": IDS[slug], "operator": "binomial_3x3_coherent_v1", "absolute_tolerance": 1e-7}
            write_json(directory/"request.json", request)
            estimate = api.json(PREFIX+"/estimate", request)
            assert estimate["shape"] == OUTPUT_SHAPE
            write_json(directory/"estimate.json", estimate)
            job_start = perf_counter()
            submitted = api.json(PREFIX+"/jobs", request)
            identifier = submitted["dataset_id"]
            created[slug] = identifier
            write_json(directory/"submitted-job.json", submitted)
            job, timeline, elapsed = wait_job(api, identifier, directory, job_start)
            write_json(directory/"completed-job.json", job)
            manifest, arrays = read_observation(root, identifier)
            assert manifest["request"] == request
            assert all(manifest["estimate"][key] == value for key, value in estimate.items()
                       if key not in {"coordinates", "acquisition"})
            write_json(directory/"manifest.json", manifest)
            public = api.json(f"{PREFIX}/datasets/{identifier}")
            write_json(directory/"public-manifest.json", public)
            assert public["complete"] and public["input_sha256"] == manifest["input_sha256"]
            spatial = spatial_contract(sm, source, manifest, arrays)
            numeric = neighborhood_oracle(sm, source, arrays)
            assert arrays["complex_total"].max() <= 1e-7 and arrays["magnitude_total"].max() <= 1e-7
            cursor, gate = (9, 42, 265), (.32, .4)
            view = api.json(f"{PREFIX}/datasets/{identifier}/view?x_index=9&y_index=42&time_index=265&product=imaginary&gate_start_us=.32&gate_end_us=.4&gate_mode=peak_envelope")
            check_view(view, arrays, manifest, cursor, gate, "peak_envelope", "imaginary")
            write_json(directory/"view.json", view)
            second = api.json(f"{PREFIX}/datasets/{identifier}/view?x_index=10&y_index=43&time_index=266&product=rf&gate_start_us=0&gate_end_us=2&gate_mode=rms_rf")
            check_view(second, arrays, manifest, (10, 43, 266), (0., 2.), "rms_rf", "rf")
            write_json(directory/"full-record-rms-view.json", second)
            exports = exported_history(api, root, directory, identifier, manifest, arrays, view)
            actual_bytes = sum(record["bytes"] for record in directory_hashes(root/identifier).values())
            assert actual_bytes <= manifest["estimate"]["total_bytes"] == estimate["total_bytes"]
            record = {"experiment": slug, "dataset_id": identifier, "source_dataset_id": IDS[slug],
                      "shape": OUTPUT_SHAPE, "job_elapsed_seconds": elapsed, "observed_progress_updates": len(timeline),
                      "spatial_checks": spatial, "numerical_checks": numeric, "exports": exports,
                      "actual_dataset_bytes": actual_bytes, "reserved_output_bytes": estimate["total_bytes"],
                      "maximum_bounds": {key: float(arrays[key].max()) for key in BOUNDS}}
            write_json(directory/"verification.json", record)
            records.append(record)
            outputs[slug] = arrays
            print(json.dumps({"status": "verified", "experiment": slug, "dataset_id": identifier,
                              "job_elapsed_seconds": elapsed, "distinct_neighborhoods": numeric["distinct_ordered_neighborhoods"]}), flush=True)
        locality = effect_support(a, b, outputs["hbm6-intact"], outputs["hbm6-missing-bump"], identity)
        old_jobs_after, observations_after = api.json("/api/v2/jobs"), observation_catalog(api)
        assert old_jobs_after == old_jobs
        before_map = {j["dataset_id"]: j for j in old_observations}
        after_map = {j["dataset_id"]: j for j in observations_after}
        assert set(after_map)-set(before_map) == set(created.values()) and len(created) == 2
        assert all(after_map[key] == value for key, value in before_map.items())
        after_files = {identifier: directory_hashes(root/identifier) for identifier in preserved_ids}
        reports_after = {name: directory_hashes(root/name) for name in report_names}
        assert after_files == before_files and reports_after == reports
        for slug, identifier in created.items():
            expected_files = json.loads((output/slug/"derived-file-hashes.json").read_bytes())
            assert directory_hashes(root/identifier) == expected_files
        for name, value in (("old-jobs-after.json", old_jobs_after), ("observation-jobs-after.json", observations_after),
                            ("preserved-files-after.json", after_files), ("report-files-after.json", reports_after)):
            write_json(output/name, value)
        final = {**run, "status": "passed", "elapsed_seconds": perf_counter()-started, "dataset_ids": created,
                 "experiments": records, "controlled_source_pair": identity, "effect_support": locality,
                 "observation_jobs_created": 2, "old_acquisitions_created": 0, "old_job_catalog_unchanged": True,
                 "legacy_dataset_files_preserved": 524, "source_causal_datasets_preserved": len(causal_ids),
                 "layered_reports_preserved": 19, "all_preexisting_comparison_reports_preserved": True,
                 "preexisting_native_observations_preserved": len(old_observations),
                 "all_preserved_file_bytes_unchanged": True, "new_observation_files_unchanged_by_inspection": True,
                 "rejected_control_created_no_jobs": True}
        write_json(output/"verification-report.json", final)
        print(json.dumps({"status": "passed", "output": str(output), "dataset_ids": created,
                          "elapsed_seconds": final["elapsed_seconds"]}), flush=True)
    except BaseException as exc:
        write_json(output/"failure.json", {"type": type(exc).__name__, "message": str(exc), "elapsed_seconds": perf_counter()-started})
        raise


if __name__ == "__main__":
    main()
