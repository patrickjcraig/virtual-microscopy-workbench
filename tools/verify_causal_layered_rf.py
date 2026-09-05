"""Reproduce five immutable causal layered-RF reports after coordinated freeze.

  python -m tools.verify_causal_layered_rf artifacts/v012-causal-delivery

No existing output is overwritten. The independent slab oracle enumerates all
finite causal slab returns and evaluates the gamma pulse directly, without the
production transform kernel. The HBM comparisons establish declared numerical
consistency and parameter/column sensitivity, not measured device accuracy.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from math import expm1, log, pi, sqrt, ulp
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

from flint import arb, ctx
import numpy as np

from tools.verify_acquisition_comparisons import API, source_hashes, write_json
from tools.verify_layered_acoustics import PREFIX, legacy_deliveries, verify_saved
from virtual_microscopy.datasets import canonical_json, now_iso
from virtual_microscopy.layered_reports import LayeredReportStore
from virtual_microscopy.layered_schemas import LayeredAnalysisRequest


SOURCE_REPORT_ID = "9685c6a7-bdfa-40fe-8e24-24239746a644"
CERTIFICATE_FIELDS = ("analytic_alias_bound", "frequency_cutoff_bound",
                      "arithmetic_complex_bound", "arithmetic_envelope_bound")


def settings():
    return {"center_frequency_mhz": 50, "fractional_bandwidth": .5, "sample_rate_mhz": 400,
        "record_start_us": 0, "record_duration_us": 2.5, "surface_standoff_mm": 0,
        "absolute_tolerance": 1e-7, "gamma_order": 12, "precision_bits": 128}


def experiments(source_report, gap_column):
    water = {"name": "Assumed water", "impedance_mrayl": 1.48, "sound_speed_m_s": 1480}
    slab = {"incident": water, "terminal": deepcopy(water), "layers": [
        {"name": "Assumed 100 um silicon slab", "thickness_mm": .1,
         "impedance_mrayl": 19.63347, "sound_speed_m_s": 8430,
         "pressure_loss_db_mm": 0, "material_id": "silicon"}]}
    source = source_report["source_column"]
    lossy = deepcopy(source["stack"])
    changed_indices = []
    for index, layer in enumerate(lossy["layers"]):
        if layer["material_id"] == "solder":
            layer["pressure_loss_db_mm"] = 3
            changed_indices.append(index)
    assert changed_indices, "The chosen feature column contains no solder"
    common = {"spectrum": {"start_mhz": 0, "end_mhz": 150, "samples": 1025}, "causal_pulse": settings()}
    cases = [
        {"slug": "analytic-silicon-slab", "request": {**deepcopy(common), "name": "v0.12 analytic water/silicon/water causal slab", "stack": slab}},
        {"slug": "hbm6-bump-column", "request": {**deepcopy(common), "name": "v0.12 HBM6 bump causal response",
            "stack": source["stack"], "source_column": source["source_column"]}},
        {"slug": "hbm6-nearby-gap-column", "request": {**deepcopy(common), "name": "v0.12 HBM6 nearby gap causal response",
            "stack": gap_column["stack"], "source_column": gap_column["source_column"]}},
        {"slug": "hbm6-bump-solder-loss", "changed_layer_indices": changed_indices,
         "request": {**deepcopy(common), "name": "v0.12 HBM6 explicit solder loss 3 dB/mm",
            "stack": lossy, "source_column": source["source_column"]}},
        {"slug": "hbm6-bump-tighter-bound", "request": {**deepcopy(common), "name": "v0.12 HBM6 causal bound 1e-9 at 192 bits",
            "stack": source["stack"], "source_column": source["source_column"],
            "causal_pulse": settings() | {"absolute_tolerance": 1e-9, "precision_bits": 192}}},
    ]
    for case in cases:
        case["request"] = LayeredAnalysisRequest.model_validate(case["request"]).model_dump(mode="json")
    return cases


def check_certificate(report):
    pulse, config = report["causal_pulse"], report["request"]["causal_pulse"]
    diagnostic = pulse["diagnostics"]
    assert report["pulse"] is None
    assert set(pulse) == {"time_us", "rf", "imaginary", "envelope", "diagnostics"}
    expected_time = np.arange(1001, dtype=np.float64)/400
    assert np.array_equal(pulse["time_us"], expected_time)
    assert all(len(pulse[key]) == 1001 for key in ("rf", "imaginary", "envelope"))
    assert all(np.isfinite(pulse[key]).all() for key in ("time_us", "rf", "imaginary", "envelope"))
    total = diagnostic["total_error_bound"]
    components = [diagnostic[field] for field in CERTIFICATE_FIELDS]
    assert all(value >= 0 for value in components)
    assert 0 < total <= config["absolute_tolerance"] == diagnostic["requested_tolerance"]
    # Each public component and the public total are separately rounded upward.
    # Compare exact binary fractions, allowing only a few documented output ulps.
    output_slack = 8*max(map(ulp, [total, *components]))
    assert Fraction(total)+Fraction(output_slack) >= sum(map(Fraction, components))
    with ctx.workprec(192):
        m = config["gamma_order"]
        frequency, bandwidth = arb(config["center_frequency_mhz"]), arb(config["fractional_bandwidth"])
        rate = arb.pi()*frequency*bandwidth/(arb(2)**(arb(2)/(m+1))-1).sqrt()
        numerator = (rate*arb(1).exp()/m)**m*arb(m+1).gamma()
        c0 = numerator*(arb(m)/2).gamma()/(2*arb.pi().sqrt()*((arb(m)+1)/2).gamma()*rate**m)
        period, damping = arb(diagnostic["period_us"]), arb(diagnostic["laplace_damping_per_us"])
        count = diagnostic["frequency_terms"]
        assert type(count) is int and count % 2 == 1
        k = (count-1)//2
        cutoff = (damping*arb(float(expected_time[-1]))).exp()*numerator/(arb.pi()*m*(k*2*arb.pi()/period)**m)
        alias = c0/((damping*period).exp()-1)
        assert arb(diagnostic["analytic_alias_bound"]) >= alias.upper()
        assert arb(diagnostic["frequency_cutoff_bound"]) >= cutoff.upper()
    field = np.asarray(pulse["rf"])+1j*np.asarray(pulse["imaginary"])
    envelope_difference = float(np.max(abs(np.asarray(pulse["envelope"])-abs(field))))
    assert envelope_difference <= total
    assert diagnostic["precision_bits"] == config["precision_bits"]
    assert diagnostic["period_us"] > expected_time[-1]
    return {"total_error_bound": total, **{field: diagnostic[field] for field in CERTIFICATE_FIELDS},
        "requested_tolerance": config["absolute_tolerance"], "precision_bits": config["precision_bits"],
        "frequency_terms": diagnostic["frequency_terms"], "inverse_work_units": diagnostic["inverse_work_units"],
        "public_component_rounding_slack": output_slack, "analytic_bounds_independently_checked_with_arb": True,
        "exact_time_centers": True, "time_samples": 1001,
        "envelope_saved_complex_max_difference": envelope_difference}


def slab_oracle(report):
    """Direct causal echo formula using real-space delays, with no transform."""
    request, pulse = report["request"], report["causal_pulse"]
    stack, options = request["stack"], request["causal_pulse"]
    layer = stack["layers"][0]
    z0, z1, z2 = stack["incident"]["impedance_mrayl"], layer["impedance_mrayl"], stack["terminal"]["impedance_mrayl"]
    r01, r12 = (z1-z0)/(z1+z0), (z2-z1)/(z2+z1)
    decay = 10**(-2*layer["pressure_loss_db_mm"]*layer["thickness_mm"]/20)
    first = (1-r01*r01)*r12*decay
    q = -r01*r12*decay
    spacing = 2000*layer["thickness_mm"]/layer["sound_speed_m_s"]
    surface = 2000*options["surface_standoff_mm"]/stack["incident"]["sound_speed_m_s"]
    time = np.asarray(pulse["time_us"], np.float64)
    m, carrier = options["gamma_order"], options["center_frequency_mhz"]
    rate = pi*carrier*options["fractional_bandwidth"]/sqrt(expm1(2*log(2)/(m+1)))
    peak = m/rate

    def excitation(dt):
        result = np.zeros(len(dt), np.complex128)
        positive = dt > 0
        u = dt[positive]
        amplitude = np.exp(m*(1+np.log(rate*u/m))-rate*u)
        result[positive] = amplitude*np.exp(2j*pi*carrier*(u-peak))
        return result

    value = r01*excitation(time-surface)
    # One extra candidate protects division/endpoint rounding. Causality makes
    # every event beyond the recording exactly zero, so no infinite echo tail
    # is truncated by amplitude in this independent finite-record oracle.
    maximum_order = max(0, int((time[-1]-surface)/spacing)+1)
    assert maximum_order < 1000
    for order in range(1, maximum_order+1):
        value += first*q**(order-1)*excitation(time-surface-order*spacing)
    actual = np.asarray(pulse["rf"])+1j*np.asarray(pulse["imaginary"])
    errors = {"complex": float(np.max(abs(actual-value))),
        "rf": float(np.max(abs(actual.real-value.real))),
        "imaginary": float(np.max(abs(actual.imag-value.imag))),
        "envelope": float(np.max(abs(np.asarray(pulse["envelope"])-abs(value))))}
    assert max(errors.values()) <= pulse["diagnostics"]["total_error_bound"]
    return ({"independent_absolute_errors": errors, "candidate_internal_echo_count": maximum_order,
        "round_trip_spacing_us": spacing, "signed_round_trip_ratio": q,
        "first_reflection": r01, "first_internal_amplitude": first,
        "oracle": "Direct finite causal slab echoes convolved with the analytic gamma pulse; double-precision independent check, not a second production-kernel call."},
        {"time_us": time.tolist(), "rf": value.real.tolist(), "imaginary": value.imag.tolist(), "envelope": abs(value).tolist()})


def comparison(reference, candidate, consistency=False):
    a, b = reference["causal_pulse"], candidate["causal_pulse"]
    assert np.array_equal(a["time_us"], b["time_us"])
    left, right = np.asarray(a["rf"])+1j*np.asarray(a["imaginary"]), np.asarray(b["rf"])+1j*np.asarray(b["imaginary"])
    delta = right-left
    position = int(np.argmax(abs(delta)))
    rf_delta = right.real-left.real
    envelope_delta = np.asarray(b["envelope"])-a["envelope"]
    allowed = a["diagnostics"]["total_error_bound"]+b["diagnostics"]["total_error_bound"]
    maximum = float(np.max(abs(delta)))
    if consistency:
        assert maximum <= allowed and float(np.max(abs(envelope_delta))) <= allowed
    else:
        assert float(np.max(abs(rf_delta))) > allowed
    return {"reference_report_id": reference["id"], "candidate_report_id": candidate["id"],
        "complex_max_difference": maximum, "rf_max_absolute_difference": float(np.max(abs(rf_delta))),
        "rf_rmse": float(np.sqrt(np.mean(rf_delta*rf_delta))), "rf_bias": float(np.mean(rf_delta)),
        "envelope_max_absolute_difference": float(np.max(abs(envelope_delta))),
        "maximum_complex_difference_index": position, "maximum_complex_difference_time_us": a["time_us"][position],
        "signed_rf_difference_at_complex_maximum": float(rf_delta[position]),
        "sum_of_error_certificates": allowed, "same_time_centers": True,
        "interpretation": "Same-model numerical consistency within the sum of independently accepted certificates." if consistency else
            "Sensitivity to the declared changed column or assumed material loss. Neither report is measured ground truth; this does not establish detection performance or experimental accuracy."}


def report_file_hashes(directory):
    assert directory.is_dir() and not directory.is_symlink() and not getattr(directory,"is_junction",lambda:False)()
    for path in directory.rglob("*"):
        assert not path.is_symlink() and not getattr(path,"is_junction",lambda:False)()
        assert path.resolve().is_relative_to(directory.resolve())
    return source_hashes(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8767")
    parser.add_argument("--data-root", type=Path, default=Path("artifacts/volumes-v07"))
    parser.add_argument("--active-instance", type=Path, default=Path("artifacts/ACTIVE_INSTANCE.json"))
    args = parser.parse_args()
    output, data_root = args.output.resolve(), args.data_root.resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = perf_counter()
    run = {"schema_version": 1, "run_id": str(uuid4()), "created_at": now_iso(),
        "api_url": args.url, "data_root": str(data_root), "no_overwrite": True,
        "tool_source_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("verify_causal_layered_rf.py", "verify_layered_acoustics.py", "verify_acquisition_comparisons.py")},
        "evidence_status": "Synthetic scalar model, numerical certificate and immutable-report workflow verification; no measured accuracy claim."}
    write_json(output/"run.json", run)
    try:
        active, deliveries, identifiers = legacy_deliveries(args.active_instance.resolve(), Path(__file__).resolve().parents[1], data_root)
        write_json(output/"active-instance-snapshot.json", active)
        write_json(output/"previous-deliveries.json", deliveries)
        old_datasets = {identifier: source_hashes(data_root/identifier) for identifier in identifiers}
        old_reports = report_file_hashes(data_root/"layered-reports")
        assert sum(map(len,old_datasets.values())) == 524
        write_json(output/"source-dataset-hashes-before.json", old_datasets)
        write_json(output/"source-report-hashes-before.json", old_reports)
        api = API(args.url)
        health, jobs_before = api.json("/api/health"), api.json("/api/v2/jobs")
        assert health["status"] == "ok" and health["version"] == "0.12.0", health
        assert not any(job["status"] in {"queued","running","cancelling"} for job in jobs_before["jobs"])
        write_json(output/"health.json", health)
        write_json(output/"jobs-before.json", jobs_before)
        historical = LayeredReportStore(data_root).read(SOURCE_REPORT_ID)
        assert api.json(PREFIX+f"/reports/{SOURCE_REPORT_ID}") == historical
        assert "causal_pulse" not in historical
        write_json(output/"v011-source-report.json", historical)
        source = historical["source_column"]
        twin = source["source_column"]["twin"]
        assert len(twin["hbm_assemblies"]) == 6 and len(twin["objects"]) == 574
        gap_request = deepcopy(source["source_column"])
        gap_request["x_mm"] += .020
        gap = api.json(PREFIX+"/column", gap_request)
        assert gap["source_column"]["twin"] == twin and gap["source_column"]["y_mm"] == source["source_column"]["y_mm"]
        assert gap["stack"] != source["stack"]
        write_json(output/"nearby-gap-column.json", gap)
        cases, saved, results = experiments(historical,gap), {}, []
        for case in cases:
            directory = output/case["slug"]
            directory.mkdir(exist_ok=False)
            write_json(directory/"request.json", case["request"])
            estimate = api.json(PREFIX+"/estimate", case["request"])
            write_json(directory/"estimate.json", estimate)
            report = api.json(PREFIX+"/reports", case["request"])
            write_json(directory/"report.json", report)
            assert report["request"] == case["request"]
            assert report["estimate"] == estimate == report["resources"]
            # Extend the historical-read guard inherited from the v0.11 tool.
            with patch("virtual_microscopy.layered_time.causal_gamma_response",side_effect=AssertionError("Historical causal report reran synthesis")), \
                 patch("virtual_microscopy.layered_time.estimate_causal_gamma",side_effect=AssertionError("Historical causal report reran admission")):
                storage = verify_saved(api,data_root,directory,report)
            verification = {"experiment":case["slug"],"report_id":report["id"],**storage,**check_certificate(report)}
            if case["slug"] == "analytic-silicon-slab":
                independent, vectors = slab_oracle(report)
                verification.update(independent)
                write_json(directory/"independent-slab-oracle.json", vectors)
            elif case["slug"] == "hbm6-bump-solder-loss":
                assert report["source_column"] == source and report["source_status"] == "modified_from_extracted_column"
                assert report["stack_differences"] == [{"path":f"/layers/{index}/pressure_loss_db_mm","before":0.0,"after":3.0}
                    for index in case["changed_layer_indices"]]
            else:
                expected = gap if case["slug"] == "hbm6-nearby-gap-column" else source
                assert report["source_column"] == expected and report["source_status"] == "matches_extracted_column"
                assert report["stack_differences"] == []
            write_json(directory/"verification.json", verification)
            saved[case["slug"]] = report
            results.append(verification)
            print(json.dumps({"state":"verified","experiment":case["slug"],"report_id":report["id"],"bound":verification["total_error_bound"]}),flush=True)
        reference = saved["hbm6-bump-column"]
        comparisons = {"tighter_tolerance":comparison(reference,saved["hbm6-bump-tighter-bound"],True),
            "nearby_column":comparison(reference,saved["hbm6-nearby-gap-column"]),
            "explicit_solder_loss":comparison(reference,saved["hbm6-bump-solder-loss"])}
        write_json(output/"saved-response-comparisons.json", comparisons)
        jobs_after = api.json("/api/v2/jobs")
        write_json(output/"jobs-after.json", jobs_after)
        assert jobs_after == jobs_before
        new_datasets = {identifier:source_hashes(data_root/identifier) for identifier in identifiers}
        new_reports = report_file_hashes(data_root/"layered-reports")
        write_json(output/"source-dataset-hashes-after.json",new_datasets)
        write_json(output/"source-report-hashes-after.json",new_reports)
        assert new_datasets == old_datasets
        assert all(new_reports.get(path)==digest for path,digest in old_reports.items())
        assert set(new_reports)-set(old_reports) == {report["id"]+".json" for report in saved.values()}
        final = {**run,"status":"passed","reports":results,"comparisons":comparisons,
            "elapsed_seconds":perf_counter()-started,"previous_dataset_count":len(identifiers),
            "previous_dataset_file_count":sum(map(len,old_datasets.values())),"previous_layered_report_file_count":len(old_reports),
            "all_previous_files_unchanged":True,"volume_jobs_created":0,"standalone_reports_created":5,
            "exact_json_csv_exports":True,"source_v011_report_id":SOURCE_REPORT_ID,
            "bump_column_xy_mm":[source["source_column"]["x_mm"],source["source_column"]["y_mm"]],
            "gap_column_xy_mm":[gap["source_column"]["x_mm"],gap["source_column"]["y_mm"]],
            "all_six_hbm_sites_preserved":True}
        write_json(output/"verification-report.json",final)
        print(json.dumps({"status":"passed","output":str(output),"reports":5,"elapsed_seconds":final["elapsed_seconds"]}),flush=True)
    except BaseException as exc:
        write_json(output/"failure.json",{"type":type(exc).__name__,"message":str(exc),"elapsed_seconds":perf_counter()-started})
        raise


if __name__ == "__main__":
    main()
