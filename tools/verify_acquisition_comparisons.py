"""Reproduce three saved-SAM batch/comparison deliveries through the local API.

Run only after backend freeze and after other live acquisition QA has finished:
  python -m tools.verify_acquisition_comparisons artifacts/v09-acquisition-delivery

This independent QA tool intentionally reads the six SMALL saved arrays in full
for direct float64 oracles. Production comparisons remain streamed. It never
reacquires a signal locally, overwrites an output directory, or deletes a source.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
import csv
import hashlib
import io
import json
from pathlib import Path
from time import monotonic, perf_counter, sleep
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4
import zipfile

import numpy as np
import zarr

from tools.build_hbm_microstructure_example import microstructure_example
from virtual_microscopy.datasets import DatasetStore, canonical_json, json_sha256, now_iso, validate_dataset_paths
from virtual_microscopy.hbm import compose_hbm
from virtual_microscopy.volume_schemas import SamVolumeRequest


def write_bytes(path, payload):
    with Path(path).open("xb") as stream:
        stream.write(payload)


def write_json(path, value):
    write_bytes(path, canonical_json(value))


class API:
    def __init__(self, base_url):
        self.base = base_url.rstrip("/")

    def bytes(self, route, body=None):
        payload = None if body is None else canonical_json(body)
        request = Request(self.base+route, data=payload,
                          headers={} if body is None else {"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=180) as response:
                return response.read()
        except HTTPError as exc:
            raise RuntimeError(f"API {request.get_method()} {route}: HTTP {exc.code}: "
                               f"{exc.read().decode('utf-8', errors='replace')}") from exc

    def json(self, route, body=None):
        return json.loads(self.bytes(route, body))


def experiments():
    nominal = microstructure_example()
    defect = {"id": "missing-gap8-r3-c2", "kind": "missing_bump", "row": 3,
              "column": 2, "layer_index": 8, "enabled": False}
    authored = compose_hbm(nominal, "hbm-6", {"microstructure": {"defects": [defect]}})
    assert authored["objects"] == nominal["objects"]
    assert authored["hbm_assemblies"][:5] == nominal["hbm_assemblies"][:5]
    acquisition = {"path_model": "continuous_columns_v1", "scan_nx": 64, "scan_ny": 64,
        "depth_samples": 1024, "roi_mm": [49.44, 39.91, 49.56, 40.09],
        "frequency_mhz": 100, "sample_rate_mhz": 800, "fractional_bandwidth": .5,
        "focus_mm": .55, "record_start_us": .2, "record_duration_us": .5,
        "water_standoff_mm": 0, "include_defects": True}
    focus = {**acquisition, "scan_nx": 32, "scan_ny": 32,
             "roi_mm": [49.375, 39.875, 49.625, 40.125], "focus_mm": .45}
    coupon = {"name": "v0.9 assumed off-grid copper-film coupon", "size_mm": [4, 3, 1],
        "objects": [
            {"id": "silicon-body", "name": "Assumed silicon body", "shape": "box", "material": "silicon",
             "center_mm": [2, 1.5, .5], "size_mm": [4, 3, .8]},
            {"id": "off-grid-copper-film", "name": "Assumed 7.1 um copper film", "shape": "box", "material": "copper",
             "center_mm": [2, 1.5, .42385], "size_mm": [4, 3, .0071]}]}
    method = {"path_model": "voxel_centers_v1", "scan_nx": 16, "scan_ny": 16,
        "depth_samples": 128, "frequency_mhz": 50, "sample_rate_mhz": 400,
        "fractional_bandwidth": .5, "focus_mm": .42385, "record_start_us": .1,
        "record_duration_us": .5, "water_standoff_mm": 0, "include_defects": True}
    result = [
        {"slug": "hbm6-isolated-missing-bump", "name": "v0.9 HBM6 isolated missing bump",
         "request": {"twin": authored, "acquisition": acquisition},
         "proposal": {"field": "defect", "values": [False, True], "assembly_id": "hbm-6", "defect_id": defect["id"]},
         "gate": {"start_us": .26, "end_us": .7}, "expected_shape": [64, 64, 401]},
        {"slug": "hbm6-focus-sweep", "name": "v0.9 HBM6 focus 0.45 / 0.65 mm",
         "request": {"twin": authored, "acquisition": focus},
         "proposal": {"field": "focus_mm", "values": [.45, .65]},
         "gate": {"start_us": .26, "end_us": .7}, "expected_shape": [32, 32, 401]},
        {"slug": "copper-film-method-pair", "name": "v0.9 copper-film voxel / continuous paths",
         "request": {"twin": coupon, "acquisition": method},
         "proposal": {"field": "path_model", "values": ["voxel_centers_v1", "continuous_columns_v1"]},
         "gate": {"start_us": .2, "end_us": .5}, "expected_shape": [16, 16, 201]},
    ]
    for item in result:
        item["request"] = SamVolumeRequest.model_validate(item["request"]).model_dump(mode="json", exclude_none=True)
    return result


def source_hashes(directory):
    records = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024**2), b""):
                    digest.update(block)
            records[path.relative_to(directory).as_posix()] = {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}
    return records


def verify_archive(path, directory, expected):
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None, "Source ZIP CRC failed"
        assert len(archive.namelist()) == len(set(archive.namelist()))
        assert set(archive.namelist()) == set(expected), "Source ZIP inventory changed"
        for name, info in expected.items():
            payload = archive.read(name)
            assert len(payload) == info["bytes"]
            assert hashlib.sha256(payload).hexdigest() == info["sha256"]
            assert payload == (directory / name).read_bytes(), "Source ZIP payload differs from source file"
    return {"crc_valid": True, "payloads_byte_equal": True, "files": len(expected),
            "archive_bytes": path.stat().st_size}


def direct_metrics(reference, candidate):
    a, b = np.asarray(reference, dtype=np.float64), np.asarray(candidate, dtype=np.float64)
    difference = b-a
    reference2 = float(np.sum(a*a, dtype=np.float64))
    error2 = float(np.sum(difference*difference, dtype=np.float64))
    index = tuple(int(v) for v in np.unravel_index(np.argmax(np.abs(difference)), difference.shape))
    return {"sample_count": difference.size, "bias": float(difference.mean()),
        "mae": float(np.abs(difference).mean()), "rmse": float(np.sqrt(error2/difference.size)),
        "max_absolute_difference": float(np.abs(difference).max()),
        "relative_l2": None if reference2 == 0 else float(np.sqrt(error2/reference2)),
        "reference_l2": float(np.sqrt(reference2)), "maximum_index": list(index),
        "maximum_signed_difference": float(difference[index])}


def assert_metrics(actual, expected, coordinates):
    residuals = {}
    for key in ("bias", "mae", "rmse", "max_absolute_difference", "relative_l2", "reference_l2"):
        if expected[key] is None:
            assert actual[key] is None and actual["relative_l2_reason"]
        else:
            np.testing.assert_allclose(actual[key], expected[key], rtol=1e-10, atol=1e-12,
                                       err_msg=f"Independent metric mismatch: {key}")
            residuals[key] = abs(actual[key]-expected[key])
    assert actual["sample_count"] == expected["sample_count"]
    locator, index = actual["max_location"], expected["maximum_index"]
    assert [locator["y_index"], locator["x_index"]] == index[:2]
    assert locator["y_mm"] == coordinates["y_mm"][index[0]]
    assert locator["x_mm"] == coordinates["x_mm"][index[1]]
    if len(index) == 3:
        assert locator["signed_difference"] == expected["maximum_signed_difference"]
        assert locator["time_index"] == index[2]
        assert locator["time_us"] == coordinates["time_us"][index[2]]
    else:
        # RMS reduction order can differ between a complete QA array and a
        # streamed production tile. Keep sign/location exact and use the same
        # declared float64 tolerance as the other reduced metric values.
        assert np.sign(locator["signed_difference"]) == np.sign(expected["maximum_signed_difference"])
        np.testing.assert_allclose(locator["signed_difference"], expected["maximum_signed_difference"], rtol=1e-10, atol=1e-12)
        assert "time_index" not in locator
    return residuals


def verify_report(report, arrays, requested_gate):
    reference, candidate = arrays
    for key in ("x_mm", "y_mm", "time_us"):
        np.testing.assert_array_equal(reference[key], candidate[key])
        np.testing.assert_array_equal(report["coordinates"][key], reference[key])
    checks, metrics = {}, {}
    for name in ("rf", "envelope"):
        metrics[name] = direct_metrics(reference[name], candidate[name])
        checks[name] = assert_metrics(report["metrics"][name], metrics[name], reference)
    time = reference["time_us"]
    selection = ((time >= requested_gate["start_us"]-1e-9) &
                 (time <= requested_gate["end_us"]+1e-9))
    indices = np.flatnonzero(selection)
    assert report["gate"]["sample_count"] == len(indices) > 0
    assert report["gate"]["start_us"] == time[indices[0]]
    assert report["gate"]["end_us"] == time[indices[-1]]
    for mode in ("peak_envelope", "rms_rf"):
        values = []
        for source in arrays:
            if mode == "peak_envelope":
                values.append(source["envelope"][:, :, selection].astype(np.float64).max(axis=2))
            else:
                rf = source["rf"][:, :, selection].astype(np.float64)
                values.append(np.sqrt(np.mean(rf*rf, axis=2)))
        actual = report["gate_maps"][mode]
        for key, expected in zip(("reference", "candidate", "difference"), (values[0], values[1], values[1]-values[0])):
            np.testing.assert_allclose(actual[key], expected, rtol=1e-12, atol=1e-12)
        metrics[mode] = direct_metrics(*values)
        checks[mode] = assert_metrics(actual["metrics"], metrics[mode], reference)
    assert metrics["rf"]["max_absolute_difference"] > 0, "The declared pair should change the synthetic RF"
    return {"coordinates_identical": True, "gate_sample_count": len(indices),
            "independent_metrics": metrics, "streamed_metric_absolute_residuals": checks,
            "tolerance": {"metric_rtol": 1e-10, "metric_atol": 1e-12, "gate_map_rtol": 1e-12, "gate_map_atol": 1e-12}}


def assert_csv(payload, report):
    previous = csv.field_size_limit()
    try:
        # Full frozen HBM manifests intentionally occupy provenance JSON cells.
        # Keep parsing bounded, while allowing the documented report maximum.
        csv.field_size_limit(64*1024**2)
        rows = list(csv.reader(io.StringIO(payload.decode("utf-8"))))
    finally:
        csv.field_size_limit(previous)
    assert rows[0] == ["section", "field", "value_json"]
    for section, field, value in rows[1:]:
        group = report if section == "report" else report[section.split(".")[0]][section.split(".")[1]]
        assert json.loads(value) == group[field], f"CSV value differs: {section}.{field}"
    return len(rows)-1


def wait_batch(api, identifier, timeout=600):
    deadline, logged = monotonic()+timeout, None
    while monotonic() < deadline:
        batch = api.json(f"/api/v2/batches/{identifier}")
        progress = [(case["state"], case["completed_rows"]) for case in batch["cases"]]
        if progress != logged:
            print(json.dumps({"batch_id": identifier, "state": batch["state"], "progress": progress}), flush=True)
            logged = progress
        if batch["state"] == "completed":
            return batch
        if batch["state"] in {"failed", "cancelled", "interrupted"}:
            raise RuntimeError(f"Batch stopped: {json.dumps(batch)}")
        sleep(.25)
    raise TimeoutError(f"Batch {identifier} exceeded {timeout} seconds; saved work is preserved.")


def verify_twins(experiment, cases):
    if not experiment["slug"].startswith("hbm6"):
        assert cases[0]["request"]["twin"] == cases[1]["request"]["twin"]
        return {"same_geometry": True}
    source = experiment["request"]["twin"]
    for case in cases:
        twin = case["request"]["twin"]
        assert [stack["id"] for stack in twin["hbm_assemblies"]] == [f"hbm-{i}" for i in range(1, 7)]
        assert twin["hbm_assemblies"][:5] == source["hbm_assemblies"][:5]
    if experiment["slug"] == "hbm6-isolated-missing-bump":
        a, b = [case["request"]["twin"] for case in cases]
        before = {obj["id"]: obj for obj in a["objects"]}
        after = {obj["id"]: obj for obj in b["objects"]}
        assert set(before).issubset(after)
        assert all(after[key] == value for key, value in before.items())
        added = list(set(after)-set(before))
        assert len(added) == 1
        assert after[added[0]]["role"] == "defect" and after[added[0]]["material"] == "epoxy"
        assert after[added[0]]["assembly_id"] == "hbm-6"
        return {"all_six_hbm_sites_preserved": True, "unrelated_primitives_byte_equivalent_as_json": True,
                "only_added_primitive_id": added[0], "primitive_counts": [len(before), len(after)]}
    assert cases[0]["request"]["twin"] == cases[1]["request"]["twin"] == source
    return {"all_six_hbm_sites_preserved": True, "same_geometry": True}


def run_experiment(api, data_root, directory, experiment, run_id, reuse_directory=None):
    directory.mkdir(exist_ok=False)
    started = perf_counter()
    body = {"name": experiment["name"], "request": experiment["request"], "default_gate": experiment["gate"]}
    write_json(directory / "recipe-request.json", body)
    reuse = (reuse_directory is not None and (reuse_directory / "batch-completed.json").is_file())
    if reuse:
        previous_body = json.loads((reuse_directory / "recipe-request.json").read_bytes())
        assert previous_body == body, "Reused batch must match the exact declared experiment"
        recipe = json.loads((reuse_directory / "recipe.json").read_bytes())
        assert api.json(f"/api/v2/recipes/{recipe['recipe_id']}") == recipe
    else:
        recipe = api.json("/api/v2/recipes", body)
    write_json(directory / "recipe.json", recipe)
    recipe_export = api.bytes(f"/api/v2/recipes/{recipe['recipe_id']}/export")
    assert json.loads(recipe_export) == recipe
    write_bytes(directory / "recipe-export.json", recipe_export)
    proposal = {"recipe_id": recipe["recipe_id"], **experiment["proposal"]}
    write_json(directory / "proposal.json", proposal)
    if reuse:
        review = json.loads((reuse_directory / "review-plan.json").read_bytes())
        assert json.loads((reuse_directory / "proposal.json").read_bytes()) == proposal
    else:
        review = api.json("/api/v2/cases/preview", proposal)
    write_json(directory / "review-plan.json", review)
    geometry = verify_twins(experiment, review["cases"])
    for case in review["cases"]:
        assert case["estimate"]["shape"] == experiment["expected_shape"]
    submission = (json.loads((reuse_directory / "submission.json").read_bytes()) if reuse else
                  {**proposal, "idempotency_key": f"v09-{run_id}-{experiment['slug']}"})
    write_json(directory / "submission.json", submission)
    batch = api.json("/api/v2/batches", submission)
    write_json(directory / "batch-initial.json", batch)
    replay = api.json("/api/v2/batches", submission)
    assert replay["batch_id"] == batch["batch_id"]
    assert [c["job_id"] for c in replay["cases"]] == [c["job_id"] for c in batch["cases"]]
    if reuse:
        previous_batch = json.loads((reuse_directory / "batch-completed.json").read_bytes())
        assert batch["batch_id"] == previous_batch["batch_id"] and batch["state"] == "completed"
    completed = wait_batch(api, batch["batch_id"])
    write_json(directory / "batch-completed.json", completed)
    store = DatasetStore(data_root)
    before, arrays, manifests = {}, [], []
    for index, case in enumerate(completed["cases"]):
        identifier = case["dataset_id"]
        path = data_root / identifier
        validate_dataset_paths(path, identifier)
        manifest = store.verify_complete(identifier)
        assert manifest["input_sha256"] == case["input_sha256"]
        assert manifest["request"] == review["cases"][index]["request"]
        assert manifest["shape"] == experiment["expected_shape"]
        manifests.append(manifest)
        before[identifier] = source_hashes(path)
        group = zarr.open_group(str(path / "data.zarr"), mode="r")
        arrays.append({key: np.asarray(group[key][:]) for key in ("rf", "envelope", "x_mm", "y_mm", "time_us")})
    write_json(directory / "source-file-hashes-before.json", before)
    xi, yi = experiment["expected_shape"][1]//2, experiment["expected_shape"][0]//2
    comparison_request = {"reference_dataset_id": completed["cases"][0]["dataset_id"],
        "candidate_dataset_id": completed["cases"][1]["dataset_id"],
        "gate_start_us": experiment["gate"]["start_us"], "gate_end_us": experiment["gate"]["end_us"],
        "x_index": xi, "y_index": yi}
    write_json(directory / "comparison-request.json", comparison_request)
    jobs_before = api.json("/api/v2/jobs")
    report = api.json("/api/v2/comparisons", comparison_request)
    write_json(directory / "comparison.json", report)
    oracle = verify_report(report, arrays, experiment["gate"])
    comparison_id = report["id"]
    route = f"/api/v2/comparisons/{comparison_id}"
    assert api.json(route) == report
    ti = int(np.argmin(abs(arrays[0]["time_us"]-(experiment["gate"]["start_us"]+experiment["gate"]["end_us"])/2)))
    view = api.json(f"{route}/view?x_index={xi}&y_index={yi}&time_index={ti}")
    write_json(directory / "comparison-view.json", view)
    for name in ("rf", "envelope"):
        for label, source in zip(("reference", "candidate"), arrays):
            np.testing.assert_array_equal(view["traces"][label][name], source[name][yi, xi])
            np.testing.assert_array_equal(view["instantaneous"][name][label], source[name][:, :, ti])
    # Independent local trap exercises the same saved-data reader without any
    # forward-construction path; API checks additionally prove no jobs were made.
    from virtual_microscopy.comparisons import ComparisonStore
    with ExitStack() as stack:
        for target in ("virtual_microscopy.sam_volume.prepare_sam", "virtual_microscopy.sam_volume.estimate_sam",
                       "virtual_microscopy.physics.simulate", "virtual_microscopy.physics.probe",
                       "virtual_microscopy.schemas.Twin.model_validate"):
            stack.enter_context(patch(target, side_effect=AssertionError("Report views cannot reacquire a source.")))
        local = ComparisonStore(data_root).view(comparison_id, x_index=xi, y_index=yi, time_index=ti)
        assert local == view
    json_export = api.bytes(route+"/export?format=json")
    csv_export = api.bytes(route+"/export?format=csv")
    assert json.loads(json_export) == report
    write_bytes(directory / "comparison-export.json", json_export)
    write_bytes(directory / "comparison.csv", csv_export)
    csv_rows = assert_csv(csv_export, report)
    archives = {}
    for case in completed["cases"]:
        identifier = case["dataset_id"]
        archive_path = directory / f"sam-volume-{identifier}.zip"
        write_bytes(archive_path, api.bytes(f"/api/v2/datasets/{identifier}/export"))
        archives[identifier] = verify_archive(archive_path, data_root / identifier, before[identifier])
    assert api.bytes(f"/api/v2/recipes/{recipe['recipe_id']}/export") == recipe_export
    assert api.json("/api/v2/jobs") == jobs_before, "Comparison/view/export created or changed acquisition jobs"
    after = {case["dataset_id"]: source_hashes(data_root / case["dataset_id"]) for case in completed["cases"]}
    assert after == before, "A comparison or export changed immutable source bytes"
    write_json(directory / "source-file-hashes-after.json", after)
    result = {"experiment": experiment["slug"], "recipe_id": recipe["recipe_id"],
        "batch_id": batch["batch_id"], "comparison_id": comparison_id,
        "dataset_ids": [case["dataset_id"] for case in completed["cases"]],
        "shape": experiment["expected_shape"], "geometry_checks": geometry,
        "source_input_sha256": [m["input_sha256"] for m in manifests],
        "source_files_unchanged": True, "idempotent_batch_replay": True, "reused_completed_batch": reuse,
        "report_views_forward_solver_trap_passed": True, "acquisition_jobs_unchanged_during_processing": True,
        "comparison_json_export_equal": True, "comparison_csv_fields_equal": True, "csv_rows": csv_rows,
        "archives": archives, **oracle, "elapsed_seconds": perf_counter()-started}
    write_json(directory / "verification.json", result)
    print(json.dumps({"experiment": experiment["slug"], "state": "verified", "batch_id": result["batch_id"],
        "comparison_id": comparison_id, "rf_relative_l2": result["independent_metrics"]["rf"]["relative_l2"]}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8767")
    parser.add_argument("--data-root", type=Path, default=Path("artifacts/volumes-v07"))
    parser.add_argument("--reuse-completed", type=Path,
                        help="Explicit prior artifact directory: reuse matching completed batches, preserving all old evidence")
    args = parser.parse_args()
    output, data_root = args.output.resolve(), args.data_root.resolve()
    output.mkdir(parents=True, exist_ok=False)
    run_id, started = str(uuid4()), perf_counter()
    api = API(args.url)
    run = {"schema_version": 1, "run_id": run_id, "created_at": now_iso(), "api_url": args.url,
        "data_root": str(data_root), "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "evidence": "Synthetic numerical verification, not measured device validation, detection accuracy or resolution.",
        "memory_scope": "Independent QA loads full small saved arrays for direct float64 oracles. Production comparison endpoints stream source row blocks.",
        "no_overwrite": True, "no_local_forward_acquisition": True,
        "reuse_completed_from": str(args.reuse_completed.resolve()) if args.reuse_completed else None}
    write_json(output / "run.json", run)
    try:
        health = api.json("/api/health")
        assert health["status"] == "ok" and health["version"] == "0.9.0", health
        write_json(output / "health.json", health)
        jobs = api.json("/api/v2/jobs")["jobs"]
        assert not any(job["status"] in {"queued", "running", "cancelling"} for job in jobs), "Wait for other live acquisition QA to finish"
        assert data_root.is_dir(), "Explicit data root must refer to the API server's existing local catalog"
        results = [run_experiment(api, data_root, output / item["slug"], item, run_id,
                   args.reuse_completed.resolve() / item["slug"] if args.reuse_completed else None) for item in experiments()]
        verification = {**run, "status": "passed", "experiments": results, "elapsed_seconds": perf_counter()-started,
            "all_source_files_unchanged": True, "all_exact_coordinates": True, "all_archive_payloads_byte_equal": True,
            "interpretation": "These differences verify the declared synthetic calculation and saved-data workflow. They establish neither experimental detectability nor model accuracy; the reference is a comparison baseline."}
        write_json(output / "verification-report.json", verification)
        print(json.dumps({"status": "passed", "output": str(output), "batches": len(results),
                          "elapsed_seconds": verification["elapsed_seconds"]}), flush=True)
    except BaseException as exc:
        write_json(output / "failure.json", {"type": type(exc).__name__, "message": str(exc),
                                            "elapsed_seconds": perf_counter()-started})
        raise


if __name__ == "__main__":
    main()
