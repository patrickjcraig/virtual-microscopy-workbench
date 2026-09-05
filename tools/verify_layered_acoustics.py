"""Deliver three immutable layered-acoustic reports and independent evidence.

Run after the backend freezes and other native report-count QA finishes:
  python -m tools.verify_layered_acoustics artifacts/v011-layered-delivery

This bounded QA reads small report vectors in full. Its slab equations and
direct pulse sums are independent of the production layered kernel. It creates
standalone reports only, refuses an existing output directory, and hashes all
v0.9/v0.10 delivered acquisition files identified through ACTIVE_INSTANCE.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
import csv
import hashlib
import io
import json
from math import log, pi, sqrt
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

import numpy as np

from tools.build_hbm_microstructure_example import microstructure_example
from tools.verify_acquisition_comparisons import API, source_hashes, write_bytes, write_json
from virtual_microscopy.datasets import canonical_json, checked_id, now_iso, validate_dataset_paths
from virtual_microscopy.layered_reports import LayeredReportStore, layered_report_csv
from virtual_microscopy.schemas import Twin


PREFIX = "/api/v2/layered-acoustics"
TOLERANCE = {"complex_absolute": 5e-13, "energy_absolute": 5e-12,
             "impulse_absolute": 5e-14, "rf_absolute": 5e-13}


def slab_request(loss_db_mm):
    water = {"name": "Assumed water exterior", "impedance_mrayl": 1.48, "sound_speed_m_s": 1480}
    return {"name": f"v0.11 analytic water / silicon / water, {loss_db_mm:g} dB/mm",
        "stack": {"incident": water, "terminal": deepcopy(water), "layers": [
            {"name": "Assumed silicon slab", "impedance_mrayl": 19.63347,
             "sound_speed_m_s": 8430, "thickness_mm": .1,
             "pressure_loss_db_mm": loss_db_mm, "material_id": "silicon"}]},
        "spectrum": {"start_mhz": 0, "end_mhz": 150, "samples": 1025},
        "pulse": {"center_frequency_mhz": 50, "fractional_bandwidth": .5,
                  "sample_rate_mhz": 800, "record_start_us": 0,
                  "record_duration_us": 1, "surface_standoff_mm": .148,
                  "absolute_tolerance": 1e-10, "max_echoes": 100000}}


def complex_vector(record):
    return np.asarray(record["real"], np.float64)+1j*np.asarray(record["imag"], np.float64)


def maximum_error(actual, expected, tolerance):
    left, right = np.asarray(actual), np.asarray(expected)
    np.testing.assert_allclose(left, right, rtol=0, atol=tolerance)
    return float(np.max(np.abs(left-right))) if left.size else 0.


def independent_slab(report):
    """Closed slab formulas and unoptimized finite-pulse sums, no kernel call."""
    request, spectrum, pulse = report["request"], report["spectrum"], report["pulse"]
    stack, settings = request["stack"], request["pulse"]
    layer = stack["layers"][0]
    z0, z1, z2 = stack["incident"]["impedance_mrayl"], layer["impedance_mrayl"], stack["terminal"]["impedance_mrayl"]
    d, speed = layer["thickness_mm"], layer["sound_speed_m_s"]
    r01, r12 = (z1-z0)/(z1+z0), (z2-z1)/(z2+z1)
    t01, t10, t12 = 1+r01, 1-r01, 1+r12
    one_way_us = 1000*d/speed
    one_way_amplitude = 10**(-layer["pressure_loss_db_mm"]*d/20)
    frequency = np.asarray(spectrum["frequency_mhz"], np.float64)
    expected_frequency = np.linspace(request["spectrum"]["start_mhz"], request["spectrum"]["end_mhz"], request["spectrum"]["samples"])
    assert np.array_equal(frequency, expected_frequency)
    propagation = one_way_amplitude*np.exp(-2j*pi*frequency*one_way_us)
    denominator = 1+r01*r12*propagation**2
    formula = {"reflection": (r01+r12*propagation**2)/denominator,
        "transmission": t01*t12*propagation/denominator,
        "primary_reflection": r01+t01*t10*r12*propagation**2,
        "direct_transmission": t01*t12*propagation}
    errors = {name: maximum_error(complex_vector(spectrum[name]), value, TOLERANCE["complex_absolute"])
              for name, value in formula.items()}
    reflectance = abs(formula["reflection"])**2
    transmittance = (z0/z2)*abs(formula["transmission"])**2
    for name, value in (("reflectance", reflectance), ("transmittance", transmittance),
                        ("absorptance", 1-reflectance-transmittance)):
        errors[name] = maximum_error(spectrum[name], value, TOLERANCE["energy_absolute"])
    energy_residual = float(np.max(abs(reflectance+transmittance-1)))
    if layer["pressure_loss_db_mm"] == 0:
        assert energy_residual < TOLERANCE["energy_absolute"]
    else:
        assert np.min(1-reflectance-transmittance) > 0

    first = t01*t10*r12*one_way_amplitude**2
    q = (-r01)*r12*one_way_amplitude**2
    # Count by repeated multiplication, independently of the kernel's log/ceil.
    count, tail = 0, abs(first)/(1-abs(q))
    while tail > settings["absolute_tolerance"]:
        count += 1
        tail *= abs(q)
        assert count < 10000, "The declared small slab oracle exceeded its QA bound"
    spacing = 2*one_way_us
    surface = 2000*settings["surface_standoff_mm"]/stack["incident"]["sound_speed_m_s"]
    amplitudes = np.r_[r01, first*q**np.arange(count)]
    centers = surface+spacing*np.arange(count+1)
    errors["impulse_amplitudes"] = maximum_error(pulse["echoes"]["amplitudes"], amplitudes, TOLERANCE["impulse_absolute"])
    errors["impulse_times_us"] = maximum_error(pulse["echoes"]["time_us"], centers, TOLERANCE["impulse_absolute"])
    assert amplitudes[0] > 0 and np.all(amplitudes[1:] < 0)
    diagnostic = pulse["diagnostics"]
    assert len(amplitudes) == diagnostic["echo_count"]
    assert diagnostic["omitted_amplitude_l1_bound"] <= settings["absolute_tolerance"]
    errors["omitted_tail"] = maximum_error(diagnostic["omitted_amplitude_l1_bound"], tail, 1e-20)
    assert np.isclose(diagnostic["signed_round_trip_ratio"], q, rtol=0, atol=5e-16)
    assert np.isclose(diagnostic["return_spacing_us"], spacing, rtol=0, atol=5e-16)
    expected_time = settings["record_start_us"]+np.arange(int(settings["record_duration_us"]*settings["sample_rate_mhz"]+1e-9)+1)/settings["sample_rate_mhz"]
    assert np.array_equal(pulse["time_us"], expected_time)
    sigma = sqrt(2*log(2))/(pi*settings["fractional_bandwidth"]*settings["center_frequency_mhz"])

    def direct_signal(event_times, event_amplitudes):
        signal = np.zeros(len(expected_time), np.complex128)
        for center, amplitude in zip(event_times, event_amplitudes):
            dt = expected_time-center
            excitation = np.exp(-.5*(dt/sigma)**2+2j*pi*settings["center_frequency_mhz"]*dt)
            excitation[abs(dt) > 4*sigma] = 0
            signal += amplitude*excitation
        return signal

    full = direct_signal(centers, amplitudes)
    primary = direct_signal([surface, surface+spacing], [r01, first])
    for name, value in (("rf", full.real), ("envelope", abs(full)),
                        ("primary_rf", primary.real), ("primary_envelope", abs(primary))):
        errors[name] = maximum_error(pulse[name], value, TOLERANCE["rf_absolute"])
    extended_count = count+500
    extended = direct_signal(surface+spacing*np.arange(extended_count+1), np.r_[r01, first*q**np.arange(extended_count)])
    observed_tail_error = float(np.max(abs(extended-full)))
    assert observed_tail_error <= tail+TOLERANCE["rf_absolute"]
    assert float(np.max(abs(full.real-primary.real))) > 0
    return {"independent_absolute_errors": errors, "echo_count": count+1,
        "impulse_spacing_us": spacing, "signed_round_trip_ratio": q,
        "independent_omitted_tail_l1": tail, "observed_extended_series_error": observed_tail_error,
        "lossless_energy_residual": energy_residual if layer["pressure_loss_db_mm"] == 0 else None,
        "minimum_absorptance": float(np.min(1-reflectance-transmittance)),
        "full_primary_rf_max_difference": float(np.max(abs(full.real-primary.real))),
        "positive_front_and_negative_internal_echoes": True,
        "exact_requested_frequency_and_time_arrays": True}


def legacy_deliveries(active_path, project_root, data_root):
    """Use release records to identify all delivered v0.9/v0.10 source UUIDs."""
    active = json.loads(active_path.read_bytes())
    assert Path(active["data_root"]).resolve() == data_root
    records = [active, active.get("previous_release", {}), *active.get("release_history", [])]
    manifests = {}
    for record in records:
        if record.get("version") in {"0.9.0", "0.10.0"}:
            relative = record["delivery_report"]
            path = (project_root/relative).resolve()
            assert path.is_relative_to(project_root) and not path.is_symlink()
            delivery = json.loads(path.read_bytes())
            assert delivery["status"] == "passed"
            manifests[record["version"]] = {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "dataset_ids": [checked_id(identifier) for item in delivery["experiments"] for identifier in item["dataset_ids"]]}
    assert set(manifests) == {"0.9.0", "0.10.0"}, "ACTIVE_INSTANCE must identify both earlier deliveries"
    identifiers = sorted({identifier for item in manifests.values() for identifier in item["dataset_ids"]})
    assert len(identifiers) == 12
    for identifier in identifiers:
        validate_dataset_paths(data_root/identifier, identifier)
    return active, manifests, identifiers


def verify_saved(api, data_root, directory, report):
    identifier = report["id"]
    payload_path = data_root/"layered-reports"/f"{identifier}.json"
    before = hashlib.sha256(payload_path.read_bytes()).hexdigest()
    route = PREFIX+f"/reports/{identifier}"
    assert api.json(route) == report
    json_export, csv_export = api.bytes(route+"/export?format=json"), api.bytes(route+"/export?format=csv")
    assert json.loads(json_export) == report
    assert json_export == payload_path.read_bytes()
    csv.field_size_limit(16*1024**2)
    decoded = {}
    for row in csv.DictReader(io.StringIO(csv_export.decode("utf-8"))):
        assert row["section"] == "report" and row["field"] not in decoded
        decoded[row["field"]] = json.loads(row["value_json"])
    assert decoded == report
    assert report["request_sha256"] == hashlib.sha256(canonical_json(report["request"])).hexdigest()
    assert report["report_sha256"] == hashlib.sha256(canonical_json({k: v for k, v in report.items() if k != "report_sha256"})).hexdigest()
    assert report["provenance"]["request_sha256"] == report["request_sha256"]
    with ExitStack() as context:
        for target in ("virtual_microscopy.layered_analysis.analyze_layered", "virtual_microscopy.layered_analysis.estimate_layered",
                       "virtual_microscopy.layered_analysis.extract_layered_column", "virtual_microscopy.layered_acoustics.layered_response",
                       "virtual_microscopy.layered_acoustics.slab_rf_response", "virtual_microscopy.layered_reports._source_fingerprints"):
            context.enter_context(patch(target, side_effect=AssertionError("A historical report cannot run the current solver")))
        historical = LayeredReportStore(data_root).read(identifier)
        assert historical == report
        assert {row["field"]: json.loads(row["value_json"]) for row in csv.DictReader(io.StringIO(layered_report_csv(historical)))} == report
    assert hashlib.sha256(payload_path.read_bytes()).hexdigest() == before
    write_bytes(directory/"report-export.json", json_export)
    write_bytes(directory/"report.csv", csv_export)
    return {"json_decode_exact": True, "csv_decode_exact": True, "canonical_json_file_byte_equal": True,
        "request_and_report_hashes_valid": True, "historical_read_solver_trap_passed": True,
        "saved_report_file_unchanged": True, "saved_report_file_sha256": before,
        "json_bytes": len(json_export), "csv_bytes": len(csv_export)}


def create_report(api, data_root, directory, request):
    directory.mkdir(exist_ok=False)
    write_json(directory/"request.json", request)
    estimate = api.json(PREFIX+"/estimate", request)
    write_json(directory/"estimate.json", estimate)
    report = api.json(PREFIX+"/reports", request)
    write_json(directory/"report.json", report)
    assert report["estimate"] == estimate == report["resources"]
    return report, verify_saved(api, data_root, directory, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8767")
    parser.add_argument("--data-root", type=Path, default=Path("artifacts/volumes-v07"))
    parser.add_argument("--active-instance", type=Path, default=Path("artifacts/ACTIVE_INSTANCE.json"))
    args = parser.parse_args()
    output, data_root = args.output.resolve(), args.data_root.resolve()
    output.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    run = {"schema_version": 1, "run_id": str(uuid4()), "created_at": now_iso(), "api_url": args.url,
        "data_root": str(data_root), "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "no_overwrite": True, "evidence_status": "Synthetic scalar model verification, not experimental calibration or measured accuracy.",
        "oracle_scope": "Small full report vectors; independent closed slab formulas and direct finite-support Gaussian pulse sums.",
        "tolerance": TOLERANCE}
    write_json(output/"run.json", run)
    try:
        active, previous, identifiers = legacy_deliveries(args.active_instance.resolve(), Path(__file__).resolve().parents[1], data_root)
        write_json(output/"active-instance-snapshot.json", active)
        write_json(output/"previous-deliveries.json", previous)
        before = {identifier: source_hashes(data_root/identifier) for identifier in identifiers}
        write_json(output/"source-file-hashes-before.json", before)
        api = API(args.url)
        health, jobs_before = api.json("/api/health"), api.json("/api/v2/jobs")
        assert health["status"] == "ok" and health["version"] == "0.11.0", health
        assert not any(job["status"] in {"queued", "running", "cancelling"} for job in jobs_before["jobs"])
        write_json(output/"health.json", health)
        write_json(output/"jobs-before.json", jobs_before)
        results = []
        for slug, loss in (("lossless-silicon-slab", 0), ("constant-loss-silicon-slab", 3)):
            report, storage = create_report(api, data_root, output/slug, slab_request(loss))
            verification = {"experiment": slug, "report_id": report["id"], **storage, **independent_slab(report)}
            write_json(output/slug/"verification.json", verification)
            results.append(verification)
            print(json.dumps({"experiment": slug, "report_id": report["id"], "state": "verified"}), flush=True)

        twin = microstructure_example()
        feature = next(p for p in twin["objects"] if p.get("assembly_id") == "hbm-6" and p.get("layer_role") == "microbump")
        column_request = {"twin": twin, "x_mm": feature["center_mm"][0], "y_mm": feature["center_mm"][1], "include_defects": True}
        column = api.json(PREFIX+"/column", column_request)
        expected_twin = Twin.model_validate(twin).model_dump(mode="json")
        assert column["source_column"]["twin"] == expected_twin
        assert len(expected_twin["hbm_assemblies"]) == 6 and len(expected_twin["objects"]) == 574
        assert sum(p.get("layer_role") in {"microbump", "tsv"} for p in expected_twin["objects"]) == 102
        segments = column["segments"]
        assert segments[0]["z_start_mm"] == 0 and segments[-1]["z_end_mm"] == twin["size_mm"][2]
        assert all(a["z_end_mm"] == b["z_start_mm"] for a, b in zip(segments, segments[1:]))
        assert any(s["material_id"] == "solder" and s["z_start_mm"] < feature["center_mm"][2] < s["z_end_mm"] for s in segments)
        request = {"name": "v0.11 H100 HBM6 explicit microbump full-depth column",
            "stack": column["stack"], "source_column": column["source_column"],
            "spectrum": {"start_mhz": 0, "end_mhz": 150, "samples": 1025}}
        slug = "hbm6-extracted-feature-column"
        report, storage = create_report(api, data_root, output/slug, request)
        write_json(output/slug/"column-request.json", column_request)
        write_json(output/slug/"extracted-column.json", column)
        assert report["source_column"] == column and report["source_status"] == "matches_extracted_column"
        assert report["stack_differences"] == [] and report["pulse"] is None
        energy = np.asarray(report["spectrum"]["reflectance"])+report["spectrum"]["transmittance"]
        residual = float(np.max(abs(energy-1)))
        assert residual < TOLERANCE["energy_absolute"]
        verification = {"experiment": slug, "report_id": report["id"], **storage,
            "feature_id": feature["id"], "column_xy_mm": feature["center_mm"][:2],
            "finite_layer_count": len(segments), "full_depth_mm": twin["size_mm"][2],
            "six_sites_preserved": True, "primitive_count": 574, "nominal_microfeature_count": 102,
            "source_twin_exact": True, "lossless_energy_residual": residual,
            "multilayer_rf_absent": True, "interpretation": "An assumed single geometrical column. No focused beam, lateral scattering, measured microstructure dimensions or general multilayer RF accuracy is established."}
        write_json(output/slug/"verification.json", verification)
        results.append(verification)
        print(json.dumps({"experiment": slug, "report_id": report["id"], "state": "verified"}), flush=True)
        listed = {item["id"] for item in api.json(PREFIX+"/reports")["reports"]}
        assert all(item["report_id"] in listed for item in results)
        jobs_after = api.json("/api/v2/jobs")
        write_json(output/"jobs-after.json", jobs_after)
        assert jobs_after == jobs_before, "Standalone report operations changed volume jobs"
        after = {identifier: source_hashes(data_root/identifier) for identifier in identifiers}
        write_json(output/"source-file-hashes-after.json", after)
        assert after == before, "Earlier delivered acquisition files changed"
        final = {**run, "status": "passed", "reports": results, "elapsed_seconds": perf_counter()-start,
            "previous_dataset_ids": identifiers, "previous_dataset_count": len(identifiers),
            "previous_source_file_count": sum(map(len, before.values())), "all_previous_source_files_unchanged": True,
            "volume_jobs_created": 0, "job_catalog_unchanged": True,
            "standalone_reports_created": len(results), "all_exports_decode_exactly": True}
        write_json(output/"verification-report.json", final)
        print(json.dumps({"status": "passed", "output": str(output), "report_ids": [r["report_id"] for r in results], "elapsed_seconds": final["elapsed_seconds"]}), flush=True)
    except BaseException as exc:
        write_json(output/"failure.json", {"type": type(exc).__name__, "message": str(exc), "elapsed_seconds": perf_counter()-start})
        raise


if __name__ == "__main__":
    main()
