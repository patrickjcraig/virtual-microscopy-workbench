"""Reproduce three small saved-X-ray batch/comparison deliveries through the API.

Run only after coordinated backend freeze and completion of other live QA:
  python -m tools.verify_xray_comparisons artifacts/v010-xray-comparisons-delivery

Use --plan-only to validate and estimate the declared cases without contacting
the API or acquiring data. All output directories must be new. This independent
QA harness reads small saved arrays in full; production comparisons stream views.
No source is deleted, overwritten, registered, normalized anew or reacquired for
a report. Synthetic differences do not establish measured resolution or accuracy.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import csv
import hashlib
import io
import json
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import numpy as np
import zarr

from tools.build_hbm_microstructure_example import microstructure_example
from tools.verify_acquisition_comparisons import (
    API, source_hashes, verify_archive, wait_batch, write_bytes, write_json)
from virtual_microscopy.datasets import canonical_json, now_iso, validate_dataset_paths
from virtual_microscopy.xray_datasets import XrayDatasetStore
from virtual_microscopy.xray_schemas import XrayVolumeRequest


PRODUCTS = tuple(XrayDatasetStore.signal_units)
COORDINATES = XrayDatasetStore.coordinate_names
TOLERANCE = {"metric_rtol": 1e-10, "metric_atol": 1e-12,
             "photon_normalization_rtol": 1e-6, "photon_normalization_atol": 1e-7}


def experiments():
    coupon = {"name": "v0.10 assumed silicon/copper X-ray coupon", "size_mm": [4, 3, 1.6],
        "objects": [
            {"id": "silicon-body", "name": "Assumed silicon body", "shape": "box", "material": "silicon",
             "center_mm": [2, 1.5, .8], "size_mm": [4, 3, 1.2]},
            {"id": "off-center-copper", "name": "Assumed copper feature", "shape": "sphere", "material": "copper",
             "center_mm": [2.5, 1, .8], "size_mm": [.5, .5, .5]},
            {"id": "copper-trace", "name": "Assumed copper trace", "shape": "box", "material": "copper",
             "center_mm": [1.2, 2, .55], "size_mm": [1.5, .15, .15]}]}
    acquisition = {"geometry_nx": 64, "geometry_ny": 48, "geometry_nz": 64,
        "detector_cols": 64, "detector_rows": 48, "views": 12, "angle_start_deg": 0,
        "angle_span_deg": 360, "energy_kev": 80, "photons": 1000,
        "noise": False, "seed": 14515, "detector_fwhm_mm": 0, "include_defects": True}
    h100 = microstructure_example()
    result = [
        {"slug": "coupon-photon-normalization", "name": "v0.10 saved X-ray I0 1000 / 8000",
         "request": {"kind": "xray_projection_volume", "twin": coupon, "acquisition": acquisition},
         "proposal": {"field": "photons", "values": [1000, 8000]},
         "normalization": "per_source_incident", "expected_shape": [12, 48, 64]},
        {"slug": "coupon-detector-blur", "name": "v0.10 saved X-ray detector blur 0 / 0.35 mm",
         "request": {"kind": "xray_projection_volume", "twin": coupon,
                     "acquisition": {**acquisition, "photons": 30000}},
         "proposal": {"field": "detector_fwhm_mm", "values": [0, .35]},
         "normalization": "native", "expected_shape": [12, 48, 64]},
        {"slug": "h100-six-site-energy", "name": "v0.10 six-site H100 X-ray energy 60 / 120 keV",
         "request": {"kind": "xray_projection_volume", "twin": h100,
                     "acquisition": {**acquisition, "geometry_ny": 64, "geometry_nz": 128,
                                     "photons": 30000, "energy_kev": 60, "detector_fwhm_mm": .02}},
         "proposal": {"field": "energy_kev", "values": [60, 120]},
         "normalization": "native", "expected_shape": [12, 48, 64]},
    ]
    for item in result:
        item["request"] = XrayVolumeRequest.model_validate(item["request"]).model_dump(mode="json", exclude_none=True)
    return result


def direct_metrics(a, b, support):
    """Direct full-small-array float64 oracle, independent of streaming engine."""
    a, b = np.asarray(a, np.float64), np.asarray(b, np.float64)
    positions = np.flatnonzero(support)
    if not len(positions):
        return {"sample_count": 0, **dict.fromkeys(("bias", "mae", "rmse", "relative_l2", "reference_l2",
                "max_absolute_difference", "maximum_index", "maximum_signed_difference"))}
    left, difference = a.flat[positions], b.flat[positions]-a.flat[positions]
    reference2, error2 = float(np.sum(left*left)), float(np.sum(difference*difference))
    maximum = int(np.argmax(np.abs(difference)))
    return {"sample_count": len(positions), "bias": float(np.mean(difference)),
        "mae": float(np.mean(np.abs(difference))), "rmse": float(np.sqrt(error2/len(positions))),
        "relative_l2": None if reference2 == 0 else float(np.sqrt(error2/reference2)),
        "reference_l2": float(np.sqrt(reference2)), "max_absolute_difference": float(abs(difference[maximum])),
        "maximum_index": [int(i) for i in np.unravel_index(positions[maximum], a.shape)],
        "maximum_signed_difference": float(difference[maximum])}


def assert_metrics(actual, expected, coordinates, fixed_view=None):
    residuals = {}
    assert actual["sample_count"] == expected["sample_count"]
    for field in ("bias", "mae", "rmse", "relative_l2", "reference_l2", "max_absolute_difference"):
        if expected[field] is None:
            assert actual[field] is None
        else:
            np.testing.assert_allclose(actual[field], expected[field], rtol=TOLERANCE["metric_rtol"],
                                       atol=TOLERANCE["metric_atol"], err_msg=field)
            residuals[field] = abs(actual[field]-expected[field])
    if expected["maximum_index"] is None:
        assert actual["max_location"] is None and actual["metric_reason"]
    else:
        index = expected["maximum_index"]
        vi, row, col = index if fixed_view is None else (fixed_view, *index)
        locator = actual["max_location"]
        assert [locator["view_index"], locator["detector_row"], locator["detector_col"]] == [vi, row, col]
        assert locator["angle_deg"] == coordinates["angles_deg"][vi]
        assert locator["v_mm"] == coordinates["v_mm"][row] and locator["u_mm"] == coordinates["u_mm"][col]
        assert locator["signed_difference"] == expected["maximum_signed_difference"]
    return residuals


def support_counts(am, bm):
    return {"total": am.size, "reference_valid": int(am.sum()), "candidate_valid": int(bm.sum()),
        "common_valid": int((am & bm).sum()), "reference_only": int((am & ~bm).sum()),
        "candidate_only": int((~am & bm).sum()), "neither_valid": int((~am & ~bm).sum())}


def assert_support(actual, am, bm):
    expected = support_counts(am, bm)
    assert all(actual[key] == value for key, value in expected.items())
    assert all(actual["fractions"][key] == value/am.size for key, value in expected.items() if key != "total")
    return expected


def assert_display(actual, a, b, am, bm, product):
    full, common = np.ones(a.shape, bool), am & bm
    supports = (am, bm, common) if product == "line_integrals" else (full, full, full)
    for field, values, valid in zip(("reference", "candidate", "difference"),
                                  (a.astype(float), b.astype(float), b.astype(float)-a.astype(float)), supports):
        np.testing.assert_array_equal(np.asarray(actual[field], dtype=float), np.where(valid, values, np.nan))
    for field, values in (("reference_valid_mask", am), ("candidate_valid_mask", bm), ("common_valid_mask", common)):
        np.testing.assert_array_equal(actual[field], values)


def verify_view(view, arrays, product):
    a, b = arrays
    vi, row, col = [view["cursor"][key] for key in ("view_index", "detector_row", "detector_col")]
    assert view["cursor"]["angle_deg"] == a["angles_deg"][vi]
    assert view["cursor"]["u_mm"] == a["u_mm"][col] and view["cursor"]["v_mm"] == a["v_mm"][row]
    am, bm = a["valid_mask"].astype(bool), b["valid_mask"].astype(bool)
    assert_display(view["projection"], a[product][vi], b[product][vi], am[vi], bm[vi], product)
    assert_display(view["profile"], a[product][vi, row], b[product][vi, row], am[vi, row], bm[vi, row], product)
    assert_display(view["sinogram"], a[product][:, row], b[product][:, row], am[:, row], bm[:, row], product)
    for name in XrayDatasetStore.pose_names:
        np.testing.assert_array_equal(view["pose"][name], a[name][vi])
    np.testing.assert_array_equal(view["sinogram"]["angles_deg"], a["angles_deg"])
    np.testing.assert_array_equal(view["profile"]["u_mm"], a["u_mm"])


def verify_report(report, arrays):
    a, b = arrays
    for key in COORDINATES:
        np.testing.assert_array_equal(a[key], b[key])
        np.testing.assert_array_equal(report["coordinates"][key], a[key])
    product = report["product"]
    am, bm = a["valid_mask"].astype(bool), b["valid_mask"].astype(bool)
    support = am & bm if product == "line_integrals" else np.ones(am.shape, bool)
    metrics = direct_metrics(a[product], b[product], support)
    residuals = assert_metrics(report["metrics"], metrics, a)
    counts = assert_support(report["support"], am, bm)
    per_view_residuals = []
    for vi, value in enumerate(report["per_view"]):
        expected = direct_metrics(a[product][vi], b[product][vi], support[vi])
        per_view_residuals.append(assert_metrics(value["metrics"], expected, a, fixed_view=vi))
        assert_support(value["support"], am[vi], bm[vi])
    verify_view(report["initial_view"], arrays, product)
    return {"independent_metrics": metrics, "global_metric_absolute_residuals": residuals,
            "per_view_metric_absolute_residuals": per_view_residuals,
            "independent_log_support": counts, "exact_coordinates": True,
            "projection_profile_sinogram_values_and_masks_equal": True, "tolerance": TOLERANCE}


def assert_csv(payload, report):
    previous = csv.field_size_limit()
    try:
        csv.field_size_limit(64*1024**2)
        rows = list(csv.reader(io.StringIO(payload.decode("utf-8"))))
    finally:
        csv.field_size_limit(previous)
    assert rows[0] == ["section", "field", "value_json"]
    for section, field, value in rows[1:]:
        expected = report[field] if section == "report" else report[section][field]
        assert json.loads(value) == expected, f"CSV differs at {section}.{field}"
    return len(rows)-1


def expect_native_photon_rejection(api, body):
    request = Request(api.base+"/api/v2/xray-comparisons", data=canonical_json({**body, "normalization": "native"}),
                      headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=180) as response:
            raise AssertionError(f"Native policy unexpectedly accepted unequal photons: {response.status}")
    except HTTPError as exc:
        payload = json.loads(exc.read())
        assert exc.code == 422 and any(issue["field"] == "photons" for issue in payload["detail"]["issues"])
        return {"http_status": exc.code, "body": payload}


def geometry_evidence(experiment, cases):
    twins = [case["request"]["twin"] for case in cases]
    assert twins[0] == twins[1] == experiment["request"]["twin"]
    result = {"same_geometry": True, "primitive_count": len(twins[0]["objects"]),
              "material_grid_pitch_mm": [case["estimate"]["geometry_pitch_mm"] for case in cases],
              "full_specimen_material_grid": True, "detector_crop_does_not_refine_geometry": True}
    if experiment["slug"].startswith("h100"):
        assert [stack["id"] for stack in twins[0]["hbm_assemblies"]] == [f"hbm-{i}" for i in range(1, 7)]
        features = [obj for obj in twins[0]["objects"] if obj.get("layer_role") in {"microbump", "tsv"}]
        assert len(features) == 102
        result.update(all_six_hbm_sites_preserved=True, authored_explicit_microfeatures=len(features),
            microstructure_resolved=False,
            interpretation="Full six-site H100 geometry is retained. The 64×64×128 material grid cannot resolve the assumed 10 µm TSVs or 25 µm bumps. Detector density and nonzero energy residuals do not establish microstructure resolution, detectability or measured H100 accuracy.")
    return result


def compare_and_export(api, data_root, directory, experiment, completed, arrays, before):
    from virtual_microscopy.xray_comparisons import XrayComparisonStore
    results = {}
    jobs_before = api.json("/api/v2/jobs")
    for product in ("transmission", "line_integrals"):
        target = directory / product
        target.mkdir(exist_ok=False)
        body = {"reference_dataset_id": completed["cases"][0]["dataset_id"],
            "candidate_dataset_id": completed["cases"][1]["dataset_id"], "product": product,
            "normalization": experiment["normalization"], "observation_policy": "same_kind",
            "view_index": 0, "detector_row": 24, "detector_col": 32}
        write_json(target / "request.json", body)
        if experiment["normalization"] == "per_source_incident":
            write_json(target / "native-policy-rejection.json", expect_native_photon_rejection(api, body))
        report = api.json("/api/v2/xray-comparisons", body)
        write_json(target / "comparison.json", report)
        verification = verify_report(report, arrays)
        route = f"/api/v2/xray-comparisons/{report['id']}"
        assert api.json(route) == report
        view = api.json(route+"/view?view_index=5&detector_row=17&detector_col=21")
        write_json(target / "comparison-view.json", view)
        verify_view(view, arrays, product)
        # This local read traps construction/acquisition entry points, while
        # API job snapshots independently prove report processing made no jobs.
        with ExitStack() as stack:
            for name in ("virtual_microscopy.xray_volume.prepare_xray", "virtual_microscopy.xray_volume.estimate_xray",
                         "virtual_microscopy.xray_volume.iter_xray_views", "virtual_microscopy.physics.simulate",
                         "virtual_microscopy.schemas.Twin.model_validate", "virtual_microscopy.xray_datasets.XrayDatasetStore.validate_identity"):
                stack.enter_context(patch(name, side_effect=AssertionError("A report view cannot reacquire a source.")))
            assert XrayComparisonStore(data_root).view(report["id"], view_index=5, detector_row=17, detector_col=21) == view
        json_export, csv_export = api.bytes(route+"/export?format=json"), api.bytes(route+"/export?format=csv")
        assert json.loads(json_export) == report
        write_bytes(target / "comparison-export.json", json_export)
        write_bytes(target / "comparison.csv", csv_export)
        verification.update(comparison_id=report["id"], csv_fields_equal=True, csv_rows=assert_csv(csv_export, report),
            json_export_equal=True, forward_solver_trap_passed=True)
        if experiment["normalization"] == "per_source_incident":
            np.testing.assert_allclose(arrays[0][product], arrays[1][product],
                rtol=TOLERANCE["photon_normalization_rtol"], atol=TOLERANCE["photon_normalization_atol"])
            verification.update(normalization_agreement_within_declared_tolerance=True,
                                observed_exact_array_equality=bool(np.array_equal(arrays[0][product], arrays[1][product])))
        else:
            assert verification["independent_metrics"]["max_absolute_difference"] > 0
        write_json(target / "verification.json", verification)
        results[product] = verification
    archives = {}
    for case in completed["cases"]:
        identifier = case["dataset_id"]
        path = directory / f"xray-volume-{identifier}.zip"
        write_bytes(path, api.bytes(f"/api/v2/datasets/{identifier}/export"))
        archives[identifier] = verify_archive(path, data_root/identifier, before[identifier])
    assert api.json("/api/v2/jobs") == jobs_before, "Comparison/view/export changed acquisition jobs"
    return results, archives


def run_experiment(api, data_root, directory, experiment, run_id, reuse_directory=None):
    directory.mkdir(exist_ok=False)
    start = perf_counter()
    body = {"name": experiment["name"], "request": experiment["request"]}
    write_json(directory / "recipe-request.json", body)
    reuse = reuse_directory is not None and (reuse_directory / "batch-completed.json").is_file()
    if reuse:
        assert json.loads((reuse_directory / "recipe-request.json").read_bytes()) == body
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
    review = json.loads((reuse_directory / "review-plan.json").read_bytes()) if reuse else api.json("/api/v2/cases/preview", proposal)
    write_json(directory / "review-plan.json", review)
    geometry = geometry_evidence(experiment, review["cases"])
    for case in review["cases"]:
        assert case["estimate"]["shape"] == experiment["expected_shape"]
    submission = (json.loads((reuse_directory / "submission.json").read_bytes()) if reuse else
                  {**proposal, "idempotency_key": f"v010-{run_id}-{experiment['slug']}"})
    if reuse:
        assert json.loads((reuse_directory / "proposal.json").read_bytes()) == proposal
    write_json(directory / "submission.json", submission)
    batch = api.json("/api/v2/batches", submission)
    write_json(directory / "batch-initial.json", batch)
    replay = api.json("/api/v2/batches", submission)
    assert replay["id"] == batch["id"] and [c["id"] for c in replay["cases"]] == [c["id"] for c in batch["cases"]]
    if reuse:
        assert batch["id"] == json.loads((reuse_directory / "batch-completed.json").read_bytes())["id"]
        assert batch["state"] == "completed"
    completed = wait_batch(api, batch["id"])
    write_json(directory / "batch-completed.json", completed)
    arrays, manifests, before = [], [], {}
    store = XrayDatasetStore(data_root)
    for index, case in enumerate(completed["cases"]):
        identifier = case["dataset_id"]
        path = data_root/identifier
        validate_dataset_paths(path, identifier)
        manifest = store.verify_complete(identifier)
        assert manifest["input_sha256"] == case["input_sha256"]
        assert manifest["request"] == review["cases"][index]["request"]
        assert manifest["shape"] == experiment["expected_shape"] and manifest["axis_order"] == ["view", "v", "u"]
        group = zarr.open_group(str(path/"data.zarr"), mode="r")
        arrays.append({name: np.asarray(group[name][:]) for name in (*PRODUCTS, *COORDINATES)})
        manifests.append(manifest)
        before[identifier] = source_hashes(path)
    write_json(directory / "source-file-hashes-before.json", before)
    comparisons, archives = compare_and_export(api, data_root, directory, experiment, completed, arrays, before)
    after = {case["dataset_id"]: source_hashes(data_root/case["dataset_id"]) for case in completed["cases"]}
    assert before == after, "Immutable saved X-ray sources changed during comparison/export"
    assert api.bytes(f"/api/v2/recipes/{recipe['recipe_id']}/export") == recipe_export
    write_json(directory / "source-file-hashes-after.json", after)
    result = {"experiment": experiment["slug"], "recipe_id": recipe["recipe_id"], "batch_id": batch["id"],
        "dataset_ids": [c["dataset_id"] for c in completed["cases"]], "shape": experiment["expected_shape"],
        "geometry_checks": geometry, "source_input_sha256": [m["input_sha256"] for m in manifests],
        "source_files_unchanged": True, "idempotent_batch_replay": True, "reused_completed_batch": reuse,
        "acquisition_jobs_unchanged_during_processing": True, "comparisons": comparisons, "archives": archives,
        "elapsed_seconds": perf_counter()-start}
    write_json(directory / "verification.json", result)
    print(json.dumps({"experiment": experiment["slug"], "state": "verified", "batch_id": batch["id"],
                      "comparisons": {p: c["comparison_id"] for p, c in comparisons.items()}}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8767")
    parser.add_argument("--data-root", type=Path, default=Path("artifacts/volumes-v07"))
    parser.add_argument("--reuse-completed", type=Path, help="Explicit previous evidence directory; reuse only matching completed batches")
    parser.add_argument("--plan-only", action="store_true", help="Validate and estimate locally without API calls or acquisition")
    args = parser.parse_args()
    output, data_root = args.output.resolve(), args.data_root.resolve()
    output.mkdir(parents=True, exist_ok=False)
    run_id, start = str(uuid4()), perf_counter()
    run = {"schema_version": 1, "run_id": run_id, "created_at": now_iso(), "api_url": args.url,
        "data_root": str(data_root), "no_overwrite": True, "no_local_forward_acquisition": True,
        "mode": "local_estimates_only" if args.plan_only else "live_saved_acquisition_delivery",
        "tool_source_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                               for name in ("verify_xray_comparisons.py", "verify_acquisition_comparisons.py")},
        "evidence": "Synthetic numerical workflow verification; not measured device accuracy, resolution or detection performance.",
        "memory_scope": "Independent QA loads small saved arrays in full for direct float64 oracles. Production endpoints stream canonical views.",
        "reuse_completed_from": str(args.reuse_completed.resolve()) if args.reuse_completed else None}
    write_json(output / "run.json", run)
    try:
        items = experiments()
        if args.plan_only:
            from copy import deepcopy
            from virtual_microscopy.xray_volume import estimate_xray
            for item in items:
                cases = []
                for value in item["proposal"]["values"]:
                    request = deepcopy(item["request"])
                    request["acquisition"][item["proposal"]["field"]] = value
                    estimate = estimate_xray(request)
                    assert estimate["shape"] == item["expected_shape"]
                    cases.append({"request": request, "estimate": estimate})
                write_json(output/(item["slug"]+".json"), {**item, "cases": cases, "geometry_checks": geometry_evidence(item, cases)})
            write_json(output/"plan-verification.json", {"status": "passed", "cases": 6, "api_calls": 0, "acquisitions": 0})
            print(json.dumps({"status": "plans_validated", "output": str(output), "cases": 6}), flush=True)
            return
        api = API(args.url)
        health = api.json("/api/health")
        assert health["status"] == "ok" and health["version"] == "0.10.0", health
        write_json(output/"health.json", health)
        assert not any(job["status"] in {"queued", "running", "cancelling"}
                       for job in api.json("/api/v2/jobs")["jobs"]), "Wait for other live acquisition QA to finish"
        assert data_root.is_dir(), "Explicit data root must identify the API server's local catalog"
        results = [run_experiment(api, data_root, output/item["slug"], item, run_id,
                   args.reuse_completed.resolve()/item["slug"] if args.reuse_completed else None) for item in items]
        report = {**run, "status": "passed", "experiments": results, "elapsed_seconds": perf_counter()-start,
            "all_source_files_unchanged": True, "all_exact_coordinates": True, "all_archive_payloads_byte_equal": True,
            "interpretation": "Reference A is a chosen synthetic baseline. Photon normalization, blur and energy residuals verify the declared saved-data calculations; the small full-H100 material grid does not resolve its authored microfeatures."}
        write_json(output/"verification-report.json", report)
        print(json.dumps({"status": "passed", "output": str(output), "batches": len(results),
                          "elapsed_seconds": report["elapsed_seconds"]}), flush=True)
    except BaseException as exc:
        write_json(output/"failure.json", {"type": type(exc).__name__, "message": str(exc), "elapsed_seconds": perf_counter()-start})
        raise


if __name__ == "__main__":
    main()
