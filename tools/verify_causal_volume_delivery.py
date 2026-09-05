"""Deliver and independently inspect three saved causal ROI acquisitions.

Run only after the coordinated Python freeze AND all native UI QA completes:
  python -m tools.verify_causal_volume_delivery artifacts/v013-causal-volume-delivery

Refuses existing output before any HTTP request. The slab oracle sums finite
causal echoes directly; HBM checks compare sampled columns with the standalone
instrument. These establish numerical consistency/sensitivity, not measured
device accuracy. Production code is never modified by this delivery harness.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from math import ulp
from pathlib import Path
from time import monotonic, perf_counter, sleep
from unittest.mock import patch
from uuid import uuid4

from flint import arb, ctx
import numpy as np

from tools.build_hbm_microstructure_example import microstructure_example
from tools.verify_acquisition_comparisons import API, source_hashes, verify_archive, write_bytes, write_json
from tools.verify_causal_layered_rf import CERTIFICATE_FIELDS, report_file_hashes, slab_oracle
from tools.verify_layered_acoustics import legacy_deliveries
from virtual_microscopy.causal_datasets import CausalSamDatasetStore
from virtual_microscopy.causal_processing import causal_sam_view
from virtual_microscopy.causal_sam_schemas import CausalSamVolumeRequest
from virtual_microscopy.datasets import canonical_json, now_iso
from virtual_microscopy.hbm import compose_hbm
from virtual_microscopy.layered_analysis import analyze_layered
from virtual_microscopy.layered_schemas import CausalGammaPulseSettings, LayeredAnalysisRequest


def experiments():
    nominal = microstructure_example()
    defect = {"id": "missing-gap8-r3-c1", "kind": "missing_bump", "row": 3,
              "column": 1, "layer_index": 8, "enabled": False}
    intact = compose_hbm(nominal, "hbm-6", {"microstructure": {"defects": [defect]}})
    damaged = compose_hbm(intact, "hbm-6", {"microstructure": {"defects": [{**defect, "enabled": True}]}})
    assert intact["objects"] == nominal["objects"]
    assert len(intact["hbm_assemblies"]) == len(damaged["hbm_assemblies"]) == 6
    assert intact["hbm_assemblies"][:5] == damaged["hbm_assemblies"][:5]
    before, after = {o["id"]: o for o in intact["objects"]}, {o["id"]: o for o in damaged["objects"]}
    assert all(after[key] == obj for key, obj in before.items())
    added = [obj for key, obj in after.items() if key not in before]
    assert len(added) == 1 and added[0]["role"] == "defect" and added[0]["material"] == "epoxy"
    target_id = "hbm-6-mb-08-r03-c01"
    target = before[target_id]
    assert all(added[0][key] == target[key] for key in ("center_mm", "size_mm", "shape"))
    assert target["shape"] == "cylinder" and target["material"] == "solder"
    settings = {"path_model": "continuous_columns_v1", "observation_model": "independent_columns_v1",
        "scan_nx": 32, "scan_ny": 64, "roi_mm": [49.425, 39.875, 49.575, 40.125],
        "center_frequency_mhz": 100, "sample_rate_mhz": 800, "fractional_bandwidth": .5,
        "record_start_us": 0, "record_duration_us": 2, "surface_standoff_mm": 0,
        "absolute_tolerance": 1e-7, "precision_bits": 128, "gamma_order": 12, "include_defects": True}
    # The entire finite specimen is one silicon slab, so no ambient finite
    # segments need to be collapsed by the independent closed-form oracle.
    slab = {"schema_version": 1, "name": "v0.13 analytic full silicon slab raster", "size_mm": [4, 3, .1],
        "objects": [{"id": "silicon-slab", "name": "Assumed 100 um silicon slab", "shape": "box",
            "material": "silicon", "role": "structure", "center_mm": [2, 1.5, .05], "size_mm": [4, 3, .1]}]}
    cases = [
        {"slug": "analytic-silicon-slab", "request": {"kind": "sam_causal_rf_volume", "twin": slab,
            "acquisition": {**settings, "scan_nx": 16, "scan_ny": 16, "roi_mm": [0, 0, 4, 3],
                "center_frequency_mhz": 50, "sample_rate_mhz": 400, "record_duration_us": 2.5}}},
        {"slug": "hbm6-intact", "request": {"kind": "sam_causal_rf_volume", "twin": intact, "acquisition": settings}},
        {"slug": "hbm6-missing-bump", "request": {"kind": "sam_causal_rf_volume", "twin": damaged, "acquisition": settings}},
    ]
    for case in cases:
        case["request"] = CausalSamVolumeRequest.model_validate(case["request"]).model_dump(mode="json", exclude_none=True)
    return cases, {"all_six_sites_preserved": True, "first_five_sites_identical": True,
        "all_nominal_primitives_identical": True, "added_defect_overlay": added[0],
        "exact_nominal_target_id": target_id, "target_geometry": target,
        "overlay_target_shape_size_center_exact": True,
        "intact_primitive_count": len(before), "damaged_primitive_count": len(after),
        "changed_control": {"assembly_id": "hbm-6", "defect_id": defect["id"], "enabled": [False, True]},
        "evidence": "Synthetic editable patch. Dimensions/composition are model assumptions; the approximately 4.6 um/pixel supplied image scale remains provisional."}


def wait_job(api, identifier, timeout=600):
    deadline, updates, last = monotonic()+timeout, [], None
    while monotonic() < deadline:
        job = api.json(f"/api/v2/jobs/{identifier}")
        state = (job["status"], job["completed_rows"])
        if state != last:
            updates.append(job)
            print(json.dumps({"dataset_id": identifier, "status": state[0], "rows": state[1], "total_rows": job["total_rows"]}), flush=True)
            last = state
        if job["status"] == "completed":
            return job, updates
        if job["status"] in {"cancelled", "failed", "interrupted"}:
            raise AssertionError(f"Delivery job did not complete: {job}")
        sleep(.25)
    raise TimeoutError(f"Delivery job {identifier} did not finish within {timeout} seconds")


def independent_certificate(diagnostic, config, time):
    total = diagnostic["total_error_bound"]
    components = [diagnostic[field] for field in CERTIFICATE_FIELDS]
    assert np.isfinite([total, *components]).all() and all(v >= 0 for v in components)
    assert 0 < total <= config["absolute_tolerance"] == diagnostic["requested_tolerance"]
    slack = 8*max(map(ulp, [total, *components]))
    assert Fraction(total)+Fraction(slack) >= sum(map(Fraction, components))
    assert diagnostic["precision_bits"] == config["precision_bits"]
    with ctx.workprec(192):
        m = config["gamma_order"]
        frequency, bandwidth = arb(config["center_frequency_mhz"]), arb(config["fractional_bandwidth"])
        rate = arb.pi()*frequency*bandwidth/(arb(2)**(arb(2)/(m+1))-1).sqrt()
        numerator = (rate*arb(1).exp()/m)**m*arb(m+1).gamma()
        c0 = numerator*(arb(m)/2).gamma()/(2*arb.pi().sqrt()*((arb(m)+1)/2).gamma()*rate**m)
        period, sigma = arb(diagnostic["period_us"]), arb(diagnostic["laplace_damping_per_us"])
        k = (diagnostic["frequency_terms"]-1)//2
        assert arb(float(time[-1])) < period
        alias = c0/((sigma*period).exp()-1)
        cutoff = (sigma*arb(float(time[-1]))).exp()*numerator/(arb.pi()*m*(k*2*arb.pi()/period)**m)
        assert arb(diagnostic["analytic_alias_bound"]) >= alias.upper()
        assert arb(diagnostic["frequency_cutoff_bound"]) >= cutoff.upper()
    return {"total_error_bound": total, **{key: diagnostic[key] for key in CERTIFICATE_FIELDS},
        "requested_tolerance": config["absolute_tolerance"], "precision_bits": config["precision_bits"],
        "frequency_terms": diagnostic["frequency_terms"], "public_component_rounding_slack": slack,
        "analytic_bounds_independently_checked_with_arb": True}


def check_view(view, group, x, y, ti):
    assert view["metadata"]["dtype"] == "float64" and view["metadata"]["product"] == "imaginary"
    for key in ("rf", "imaginary", "envelope"):
        np.testing.assert_array_equal(view["ascan"][key], group[key][y, x])
    np.testing.assert_array_equal(view["ascan"]["time_us"], group["time_us"][:])
    for row in range(group["rf"].shape[0]):
        np.testing.assert_array_equal(view["xy"]["image"][row], group["imaginary"][row, :, ti])
        expected = group["envelope"][row].max(axis=1)
        np.testing.assert_array_equal(view["cscan"]["image"][row], expected)
    np.testing.assert_array_equal(view["xt"]["image"], group["imaginary"][y].T)
    np.testing.assert_array_equal(view["yt"]["image"], group["imaginary"][:, x, :].T)
    assert view["certificate"]["selected_bound"] == group["error_bound"][y, x]


def verify_saved(api, root, directory, identifier):
    store = CausalSamDatasetStore(root)
    source = store.path(identifier)
    before = source_hashes(source)
    write_json(directory/"source-hashes-before.json", before)
    with ExitStack() as traps:
        for target in ("virtual_microscopy.causal_sam.estimate_causal_sam", "virtual_microscopy.causal_sam.prepare_causal_sam",
            "virtual_microscopy.causal_sam.iter_causal_sam_rows", "virtual_microscopy.causal_sam.causal_gamma_response",
            "virtual_microscopy.layered_time.causal_gamma_response", "virtual_microscopy.layered_time.estimate_causal_gamma",
            "virtual_microscopy.causal_datasets.causal_solver_identity"):
            traps.enter_context(patch(target, side_effect=AssertionError("Historical causal read invoked current forward machinery")))
        manifest = store.verify_complete(identifier)
        group = store.open_arrays(identifier)
        y, x = manifest["estimate"]["stack_table"][0]["representative_yx"]
        ti = len(group["time_us"][:])//2
        local = causal_sam_view(source, x_index=x, y_index=y, time_index=ti, product="imaginary")
        cache = store.restore_class_cache(identifier)
        assert len(cache) == len(manifest["estimate"]["stack_table"])
    check_view(local, group, x, y, ti)
    view = api.json(f"/api/v2/causal-datasets/{identifier}/view?x_index={x}&y_index={y}&time_index={ti}&product=imaginary")
    assert view == local
    write_json(directory/"saved-view.json", view)
    write_json(directory/"manifest.json", manifest)
    jobs_before = api.json("/api/v2/jobs")
    export = api.bytes(f"/api/v2/datasets/{identifier}/export")
    write_bytes(directory/"dataset.zip", export)
    archive = verify_archive(directory/"dataset.zip", source, before)
    assert api.json("/api/v2/jobs") == jobs_before
    assert source_hashes(source) == before
    write_json(directory/"source-hashes-after.json", before)
    a = manifest["request"]["acquisition"]
    time = group["time_us"][:]
    expected_time = np.asarray([a["record_start_us"]+i/a["sample_rate_mhz"] for i in range(len(time))], np.float64)
    assert time.tobytes() == expected_time.tobytes()
    assert manifest["metadata"]["halo_pixels_yx"] == [0, 0]
    assert manifest["metadata"]["focus_model"] == "none" and not manifest["metadata"]["depth_mapping_supported"]
    assert all(layer["pressure_loss_db_mm"] == 0 for entry in manifest["estimate"]["stack_table"] for layer in entry["stack"]["layers"])
    certificates = {key: independent_certificate(value["diagnostics"], a, time) for key, value in manifest["class_certificates"].items()}
    assert manifest["total_error_bound"] == max(d["total_error_bound"] for d in certificates.values())
    for key in ("rf", "imaginary", "envelope", "error_bound", "x_mm", "y_mm", "time_us"):
        assert group[key].dtype == np.dtype("float64")
    assert group["class_index"].dtype == np.dtype("uint16")
    return manifest, {"dataset_id": identifier, "shape": manifest["shape"], "class_count": len(certificates),
        "certificates": certificates, "maximum_error_bound": manifest["total_error_bound"],
        "actual_output_bytes": sum(r["bytes"] for r in before.values()), "advertised_output_bytes": manifest["estimate"]["total_bytes"],
        "exact_float64_signals_and_coordinates": True, "historical_forward_traps_passed": True,
        "saved_view_matches_direct_arrays": True, "source_files_unchanged_by_read_export": True,
        "canonical_zip": archive, "zero_jobs_created_by_views_exports": True,
        "observation": "independent unfocused columns; no lateral mixing", "material_loss": "all finite layers explicitly lossless"}


def standalone_checks(root, directory, manifest, slab=False):
    group = CausalSamDatasetStore(root).open_arrays(manifest["dataset_id"])
    settings = {k: manifest["request"]["acquisition"][k] for k in CausalGammaPulseSettings.model_fields}
    records = []
    for entry in manifest["estimate"]["stack_table"]:
        index = entry["class_id"]
        y, x = entry["representative_yx"]
        diag = manifest["class_certificates"][str(index)]["diagnostics"]
        pulse = {key: group[key][y, x].tolist() for key in ("rf", "imaginary", "envelope")}
        pulse.update(time_us=group["time_us"][:].tolist(), diagnostics=diag)
        if slab:
            assert len(entry["stack"]["layers"]) == 1
            record, oracle = slab_oracle({"request": {"stack": entry["stack"], "causal_pulse": settings}, "causal_pulse": pulse})
            write_json(directory/"independent-slab-oracle.json", oracle)
            records.append({"class_id": index, **record})
            continue
        source = {"twin": manifest["request"]["twin"], "x_mm": float(group["x_mm"][x]),
                  "y_mm": float(group["y_mm"][y]), "include_defects": manifest["request"]["acquisition"]["include_defects"]}
        request = LayeredAnalysisRequest.model_validate({"name": f"Independent delivery check of saved causal class {index}",
            "stack": entry["stack"], "source_column": source, "causal_pulse": settings,
            "spectrum": {"start_mhz": 0, "end_mhz": 150, "samples": 1025}})
        report = analyze_layered(request)
        write_json(directory/f"standalone-class-{index}.json", {"request": request.model_dump(mode="json"), **report})
        assert report["source_status"] == "matches_extracted_column" and not report["stack_differences"]
        other = report["causal_pulse"]
        np.testing.assert_array_equal(other["time_us"], pulse["time_us"])
        field = np.asarray(pulse["rf"])+1j*np.asarray(pulse["imaginary"])
        reference = np.asarray(other["rf"])+1j*np.asarray(other["imaginary"])
        error = float(np.max(np.abs(field-reference)))
        envelope_error = float(np.max(np.abs(np.asarray(pulse["envelope"])-other["envelope"])))
        allowance = diag["total_error_bound"]+other["diagnostics"]["total_error_bound"]
        assert max(error, envelope_error) <= allowance
        records.append({"class_id": index, "representative_yx": [y, x], "actual_xy_mm": [source["x_mm"], source["y_mm"]],
            "complex_max_difference": error, "envelope_max_difference": envelope_error,
            "sum_of_numerical_certificates": allowance, "full_extracted_stack_matches": True,
            "evidence": "Saved-raster/standalone-instrument agreement under the same represented scalar model, not independent physical accuracy."})
    write_json(directory/"independent-column-checks.json", records)
    return records


def sensitivity(root, intact_id, defect_id, overlay):
    store = CausalSamDatasetStore(root)
    a, b = store.open_arrays(intact_id), store.open_arrays(defect_id)
    for name in ("x_mm", "y_mm", "time_us"):
        assert a[name][:].tobytes() == b[name][:].tobytes()
    totals = {key: {"sum": 0., "sum2": 0., "absolute": 0., "max": 0., "count": 0} for key in ("rf", "imaginary", "envelope")}
    changed_columns = np.zeros(a["error_bound"].shape, dtype=bool)
    x, y = a["x_mm"][:], a["y_mm"][:]
    cx, cy = overlay["center_mm"][:2]
    sx, sy = overlay["size_mm"][:2]
    assert overlay["shape"] == "cylinder"
    # Independent global-coordinate footprint test of the one authored cylinder.
    inside = ((x[None, :]-cx)/(sx/2))**2+((y[:, None]-cy)/(sy/2))**2 <= 1
    assert inside.any() and not inside.all()
    max_bound_sum = float(np.max(a["error_bound"][:]+b["error_bound"][:]))
    for y in range(a["rf"].shape[0]):
        for key, values in totals.items():
            left, right = a[key][y], b[key][y]
            assert left[~inside[y]].tobytes() == right[~inside[y]].tobytes(), "Independent columns changed outside the single defect footprint"
            delta = right-left
            values["sum"] += float(np.sum(delta, dtype=np.float64))
            values["sum2"] += float(np.sum(delta*delta, dtype=np.float64))
            values["absolute"] += float(np.sum(abs(delta), dtype=np.float64))
            values["count"] += delta.size
            maximum = float(np.max(abs(delta)))
            if maximum > values["max"]:
                x, t = map(int, np.unravel_index(np.argmax(abs(delta)), delta.shape))
                values.update(max=maximum, maximum_index_yxt=[y, x, t], signed_difference_at_maximum=float(delta[x, t]))
            if key == "rf":
                changed_columns[y] = np.max(abs(delta), axis=1) > a["error_bound"][y]+b["error_bound"][y]
    metrics = {key: {"sample_count": v["count"], "bias": v["sum"]/v["count"],
        "mae": v["absolute"]/v["count"], "rmse": float(np.sqrt(v["sum2"]/v["count"])),
        "max_absolute_difference": v["max"], "maximum_index_yxt": v.get("maximum_index_yxt"),
        "signed_difference_at_maximum": v.get("signed_difference_at_maximum")} for key, v in totals.items()}
    assert metrics["rf"]["max_absolute_difference"] > max_bound_sum
    assert changed_columns.any() and not changed_columns.all()
    return {"reference_dataset_id": intact_id, "candidate_dataset_id": defect_id,
        "coordinates_byte_identical": True, "metrics": metrics,
        "columns_with_rf_change_above_sum_of_bounds": int(changed_columns.sum()),
        "changed_column_mask_yx": changed_columns.tolist(), "maximum_sum_of_bounds": max_bound_sum,
        "defect_footprint_mask_yx": inside.tolist(), "sampled_columns_inside_defect_footprint": int(inside.sum()),
        "all_three_signals_byte_identical_outside_defect_footprint": True,
        "comparison_arithmetic_scope": "Diagnostic float64 subtraction, reduction and bound addition only; comparison arithmetic is not enclosed. The bound-crossing mask is not a newly certified residual or classification.",
        "interpretation": "Candidate minus intact sensitivity to one declared synthetic missing-bump overlay. No measured ground truth, detection accuracy or amplitude normalization."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8767")
    parser.add_argument("--data-root", type=Path, default=Path("artifacts/volumes-v07"))
    parser.add_argument("--active-instance", type=Path, default=Path("artifacts/ACTIVE_INSTANCE.json"))
    args = parser.parse_args()
    output, root = args.output.resolve(), args.data_root.resolve()
    output.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    run = {"schema_version": 1, "run_id": str(uuid4()), "created_at": now_iso(), "api_url": args.url,
        "data_root": str(root), "no_overwrite": True,
        "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "helper_source_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("verify_causal_volume_delivery.py", "verify_causal_layered_rf.py",
                         "verify_layered_acoustics.py", "verify_acquisition_comparisons.py",
                         "build_hbm_microstructure_example.py", "build_h100_example.py")},
        "evidence_status": "Synthetic scalar numerical/storage workflow verification; no measured accuracy claim."}
    write_json(output/"run.json", run)
    try:
        active, previous, legacy_ids = legacy_deliveries(args.active_instance.resolve(), Path(__file__).resolve().parents[1], root)
        api = API(args.url)
        health, before_jobs = api.json("/api/health"), api.json("/api/v2/jobs")
        assert health["status"] == "ok" and health["version"] == "0.13.0", health
        assert not any(j["status"] in {"queued", "running", "cancelling"} for j in before_jobs["jobs"])
        causal_ids = {j["dataset_id"] for j in before_jobs["jobs"] if j.get("kind") == "sam_causal_rf_volume"}
        old = {identifier: source_hashes(root/identifier) for identifier in sorted(set(legacy_ids)|causal_ids)}
        assert sum(len(old[i]) for i in legacy_ids) == 524
        old_reports = report_file_hashes(root/"layered-reports")
        write_json(output/"active-instance-snapshot.json", active)
        write_json(output/"previous-deliveries.json", previous)
        write_json(output/"health.json", health)
        write_json(output/"jobs-before.json", before_jobs)
        write_json(output/"source-dataset-hashes-before.json", old)
        write_json(output/"source-report-hashes-before.json", old_reports)
        cases, geometry = experiments()
        write_json(output/"controlled-geometry-change.json", geometry)
        records, new_ids = [], {}
        for case in cases:
            directory = output/case["slug"]
            directory.mkdir(exist_ok=False)
            write_json(directory/"request.json", case["request"])
            estimate = api.json("/api/v2/estimate", case["request"])
            write_json(directory/"estimate.json", estimate)
            job = api.json("/api/v2/jobs", case["request"])
            write_json(directory/"submitted-job.json", job)
            identifier = job["dataset_id"]
            final_job, updates = wait_job(api, identifier)
            write_json(directory/"job-history.json", updates)
            write_json(directory/"completed-job.json", final_job)
            manifest, record = verify_saved(api, root, directory, identifier)
            assert manifest["request"] == case["request"]
            for key, value in manifest["estimate"].items():
                assert estimate[key] == value
            record["standalone_checks"] = standalone_checks(root, directory, manifest, case["slug"] == "analytic-silicon-slab")
            write_json(directory/"verification.json", record)
            records.append({"experiment": case["slug"], **record})
            new_ids[case["slug"]] = identifier
            print(json.dumps({"status": "verified", "experiment": case["slug"], "dataset_id": identifier,
                "shape": record["shape"], "class_count": record["class_count"], "bound": record["maximum_error_bound"]}), flush=True)
        comparison = sensitivity(root, new_ids["hbm6-intact"], new_ids["hbm6-missing-bump"], geometry["added_defect_overlay"])
        write_json(output/"controlled-defect-sensitivity.json", comparison)
        new_before = {identifier: json.loads((output/slug/"source-hashes-before.json").read_text())
                      for slug, identifier in new_ids.items()}
        new_after = {identifier: source_hashes(root/identifier) for identifier in new_ids.values()}
        assert new_before == new_after
        write_json(output/"delivered-source-hashes-after-all-checks.json", new_after)
        after_jobs = api.json("/api/v2/jobs")
        assert {j["id"] for j in after_jobs["jobs"]}-{j["id"] for j in before_jobs["jobs"]} == set(new_ids.values())
        after = {identifier: source_hashes(root/identifier) for identifier in old}
        after_reports = report_file_hashes(root/"layered-reports")
        assert old == after and old_reports == after_reports
        write_json(output/"jobs-after.json", after_jobs)
        write_json(output/"source-dataset-hashes-after.json", after)
        write_json(output/"source-report-hashes-after.json", after_reports)
        final = {**run, "status": "passed", "elapsed_seconds": perf_counter()-start,
            "datasets": records, "dataset_ids": new_ids, "controlled_geometry": geometry, "sensitivity": comparison,
            "previous_legacy_dataset_file_count": 524, "preserved_preexisting_causal_datasets": sorted(causal_ids),
            "previous_layered_report_count": len(old_reports), "all_previous_files_unchanged": True,
            "all_delivered_files_unchanged_by_standalone_checks_and_comparison": True,
            "volume_jobs_created": 3, "standalone_reports_persisted_to_application": 0,
            "standalone_checks_saved_in_delivery_only": True}
        write_json(output/"verification-report.json", final)
        print(json.dumps({"status": "passed", "output": str(output), "dataset_ids": new_ids,
            "elapsed_seconds": final["elapsed_seconds"]}), flush=True)
    except BaseException as exc:
        write_json(output/"failure.json", {"type": type(exc).__name__, "message": str(exc), "elapsed_seconds": perf_counter()-start})
        raise


if __name__ == "__main__":
    main()
