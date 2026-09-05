"""Deliver three immutable comparisons of the already saved v0.13 HBM pair.

Run only after the coordinated Python freeze and native-QA completion signal:
  python -m tools.verify_causal_comparison_delivery artifacts/v014-causal-comparison-delivery

No acquisitions, propagation, source mutations or output overwrites. This QA
harness intentionally reads the small delivered pair in full for independent
array/Fraction oracles; production comparisons remain streamed by source row.
Metrics and gate maps are ordinary diagnostics, not new numerical certificates.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
import csv
from fractions import Fraction
import hashlib
import io
import json
import math
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import numpy as np

from tools.verify_acquisition_comparisons import API, source_hashes, write_bytes, write_json
from tools.verify_layered_acoustics import legacy_deliveries
from virtual_microscopy.causal_comparison_store import CausalComparisonStore
from virtual_microscopy.causal_datasets import CausalSamDatasetStore
from virtual_microscopy.datasets import canonical_json, now_iso


PREFIX = "/api/v2/causal-comparisons"
SIGNALS = ("rf", "imaginary", "envelope")
BOUND_KEYS = ("source_sum", "complex_arithmetic", "complex_total", "envelope_arithmetic", "envelope_total")
HELPERS = ("verify_causal_comparison_delivery.py", "verify_acquisition_comparisons.py",
           "verify_layered_acoustics.py", "build_hbm_microstructure_example.py", "build_h100_example.py")
ORACLE_TOLERANCE = {"relative": 1e-10, "absolute": 1e-12}


def directory_hashes(directory):
    if not directory.exists():
        return {}
    for path in (directory, *directory.rglob("*")):
        assert not path.is_symlink() and not getattr(path, "is_junction", lambda: False)()
        assert path.resolve().is_relative_to(directory.resolve())
    return source_hashes(directory)


def catalog(api):
    items, offset = [], 0
    while True:
        page = api.json(f"{PREFIX}?limit=100&offset={offset}")
        assert page["order"] == "id_desc"
        items.extend(page["comparisons"])
        offset = page["next_offset"]
        if offset is None:
            break
    identifiers = [item["id"] for item in items]
    assert identifiers == sorted(set(identifiers), reverse=True)
    return items


def source_arrays(root, identifier):
    store = CausalSamDatasetStore(root)
    manifest = store.verify_complete(identifier)
    group = store.open_arrays(identifier)
    arrays = {key: np.asarray(group[key][:]) for key in (*SIGNALS, "error_bound", "class_index", "x_mm", "y_mm", "time_us")}
    for key, value in arrays.items():
        assert value.dtype == np.dtype("uint16" if key == "class_index" else "float64")
        assert np.isfinite(value).all()
    # Independently recheck canonical typed row hashes, not legacy float32 hashes.
    for y in range(manifest["shape"][0]):
        for key in (*SIGNALS, "error_bound"):
            payload = np.asarray(arrays[key][y:y+1], dtype="<f8", order="C").tobytes()
            assert hashlib.sha256(payload).hexdigest() == manifest["completed_chunks"][str(y)][f"{key}_sha256"]
    for key in ("x_mm", "y_mm", "time_us"):
        assert hashlib.sha256(arrays[key].astype("<f8").tobytes()).hexdigest() == manifest["coordinates_sha256"][key]
    return manifest, arrays


def controlled_pair_identity(reference, candidate, a=None, b=None):
    """Pin the authorized single-feature experiment before naming any report."""
    assert reference["dataset_id"] != candidate["dataset_id"], "Controlled pair must use distinct saved datasets"
    left, right = reference["request"]["twin"], candidate["request"]["twin"]
    assert reference["request"]["acquisition"] == candidate["request"]["acquisition"]
    assert len(left["hbm_assemblies"]) == len(right["hbm_assemblies"]) == 6
    assert [part["id"] for part in left["hbm_assemblies"]] == [f"hbm-{i}" for i in range(1, 7)]
    assert left["hbm_assemblies"][:5] == right["hbm_assemblies"][:5]
    nominal = {obj["id"]: obj for obj in left["objects"]}
    changed = {obj["id"]: obj for obj in right["objects"]}
    assert len(nominal) == len(left["objects"]) == 574
    assert len(changed) == len(right["objects"]) == 575
    assert all(changed.get(key) == value for key, value in nominal.items())
    added = [obj for key, obj in changed.items() if key not in nominal]
    assert len(added) == 1
    target_id, defect_id = "hbm-6-mb-08-r03-c01", "missing-gap8-r3-c1"
    target, overlay = nominal[target_id], added[0]
    assert target["shape"] == "cylinder" and target["material"] == "solder" and target["role"] == "structure"
    assert overlay["id"] == f"hbm-6-defect-{defect_id}"
    assert overlay["assembly_id"] == "hbm-6" and overlay["layer_role"] == "microbump"
    assert overlay["role"] == "defect" and overlay["material"] == "epoxy"
    assert all(overlay[key] == target[key] for key in ("shape", "center_mm", "size_mm"))
    authored = left["hbm_assemblies"][-1]["microstructure"]["defects"]
    assert authored == [{"id": defect_id, "kind": "missing_bump", "row": 3,
                        "column": 1, "layer_index": 8, "enabled": False}]
    expected = deepcopy(right)
    assert expected["hbm_assemblies"][-1]["microstructure"]["defects"] == [{**authored[0], "enabled": True}]
    expected["hbm_assemblies"][-1]["microstructure"]["defects"][0]["enabled"] = False
    expected["objects"] = [obj for obj in expected["objects"] if obj["id"] != overlay["id"]]
    assert expected == left, "Any other twin or microstructure change invalidates the controlled fixture"
    result = {"distinct_saved_dataset_ids": True, "all_six_sites_preserved": True,
        "unrelated_and_nominal_primitives_identical": True, "single_enabled_defect": defect_id,
        "nominal_target": target, "added_epoxy_overlay": overlay,
        "exact_overlay_shape_size_center": True, "only_authored_change": "HBM6 gap8 row3 column1 missing bump enabled false to true"}
    if a is not None and b is not None:
        for key in ("x_mm", "y_mm", "time_us"):
            assert a[key].tobytes() == b[key].tobytes()
        cx, cy = target["center_mm"][:2]
        rx, ry = np.asarray(target["size_mm"][:2])/2
        inside = ((a["x_mm"][None, :]-cx)/rx)**2+((a["y_mm"][:, None]-cy)/ry)**2 <= 1
        delta = b["rf"]-a["rf"]
        changed_columns = np.any(delta != 0, axis=2)
        assert np.any(changed_columns) and np.any(changed_columns & inside), "Controlled RF pair must actually differ"
        assert not np.any(changed_columns & ~inside)
        for key in SIGNALS:
            assert a[key][~inside].tobytes() == b[key][~inside].tobytes()
        result.update(maximum_observed_rf_difference=float(np.max(abs(delta))),
            footprint_columns=int(inside.sum()), changed_rf_columns=int(changed_columns.sum()),
            outside_columns=int((~inside).sum()), outside_all_signal_bytes_identical=True,
            diagnostic_scope="Observed synthetic sensitivity only; these counts/maxima are ordinary arithmetic, not enclosed classifications or physical detectability.")
    return result


def assert_exact(actual, expected):
    converted = np.asarray(actual, dtype=np.float64)
    expected = np.asarray(expected, dtype=np.float64)
    assert converted.shape == expected.shape and converted.tobytes() == expected.tobytes()


def close(actual, expected):
    if expected is None:
        assert actual is None
        return 0.
    np.testing.assert_allclose(actual, expected, rtol=ORACLE_TOLERANCE["relative"],
                               atol=ORACLE_TOLERANCE["absolute"])
    return float(np.max(np.abs(np.asarray(actual)-expected)))


def locator(actual, index, coordinates, *, real=None, imaginary=None, signed=None):
    y, x, t = map(int, index)
    assert [actual["y_index"], actual["x_index"], actual["time_index"]] == [y, x, t]
    for key, position in (("y_mm", y), ("x_mm", x), ("time_us", t)):
        assert actual[key] == coordinates[key][position]
    if signed is not None:
        assert actual["signed_difference"] == signed
    if real is not None:
        assert actual["signed_real_difference"] == real
        assert actual["signed_imaginary_difference"] == imaginary


def metric_oracle(actual, a, b, lo, hi):
    """Direct whole-array oracle with reductions independent of production."""
    n = a["rf"][:, :, lo:hi].size
    residuals, differences = {}, {}
    for key in SIGNALS:
        aa, bb = a[key][:, :, lo:hi], b[key][:, :, lo:hi]
        d = bb-aa
        differences[key] = d
        index = np.unravel_index(int(np.argmax(np.abs(d))), d.shape)
        full_index = (*index[:2], int(index[2])+lo)
        ref2 = float(np.sum(aa*aa, dtype=np.float64))
        err2 = float(np.sum(d*d, dtype=np.float64))
        expected = {"bias": float(d.mean()), "mae": float(abs(d).mean()), "rmse": math.sqrt(err2/n),
            "reference_l2": math.sqrt(ref2), "relative_l2": None if ref2 == 0 else math.sqrt(err2/ref2),
            "max_absolute_difference": float(abs(d[index]))}
        assert actual[key]["sample_count"] == n
        residuals[key] = {field: close(actual[key][field], value) for field, value in expected.items()}
        locator(actual[key]["max_location"], full_index, a, signed=float(d[index]))
    dr, di = differences["rf"], differences["imaginary"]
    magnitude = np.hypot(dr, di)
    idx = np.unravel_index(int(np.argmax(magnitude)), magnitude.shape)
    err2 = float(np.sum(dr*dr)+np.sum(di*di))
    ar, ai = a["rf"][:, :, lo:hi], a["imaginary"][:, :, lo:hi]
    ref2 = float(np.sum(ar*ar)+np.sum(ai*ai))
    expected = {"bias_real": float(dr.mean()), "bias_imaginary": float(di.mean()),
        "rmse": math.sqrt(err2/n), "reference_l2": math.sqrt(ref2),
        "relative_l2": None if ref2 == 0 else math.sqrt(err2/ref2),
        "max_absolute_difference": float(magnitude[idx])}
    residuals["complex"] = {key: close(actual["complex"][key], value) for key, value in expected.items()}
    locator(actual["complex"]["max_location"], (*idx[:2], int(idx[2])+lo), a,
            real=float(dr[idx]), imaginary=float(di[idx]))
    assert actual["sample_count"] == n
    return {"sample_count": n, "absolute_oracle_discrepancies": residuals,
        "max_complex_index_yxt": [int(idx[0]), int(idx[1]), int(idx[2])+lo],
        "max_complex_magnitude_diagnostic": expected["max_absolute_difference"]}


def upward(exact):
    """Independent exact-rational conversion, with explicit outward readback."""
    result = float(exact)
    if Fraction(result) < exact:
        result = math.nextafter(result, math.inf)
    assert math.isfinite(result) and Fraction(result) >= exact
    return result


def fraction_bounds(report, manifests, a, b):
    # A class number is local to its source. Cover every distinct pair of class
    # numbers, and also every explicitly frozen representative from both sources.
    pairs = {}
    for y in range(a["rf"].shape[0]):
        for x in range(a["rf"].shape[1]):
            pair = (int(a["class_index"][y, x]), int(b["class_index"][y, x]))
            pairs.setdefault(pair, (y, x))
    representatives = set(pairs.values())
    for manifest in manifests:
        representatives.update(tuple(entry["representative_yx"]) for entry in manifest["estimate"]["stack_table"])
    u, eta = Fraction(1, 2**53), Fraction(1, 2**1074)
    expected_by_pair = {}
    records = []
    for y, x in sorted(representatives):
        eps_a, eps_b = Fraction(float(a["error_bound"][y, x])), Fraction(float(b["error_bound"][y, x]))
        source = upward(eps_a+eps_b)
        rho = {key: u*(Fraction(float(abs(a[key][y, x]).max()))+Fraction(float(abs(b[key][y, x]).max())))+eta
               for key in SIGNALS}
        ca, ea = upward(rho["rf"]+rho["imaginary"]), upward(rho["envelope"])
        expected = {"source_sum": source, "complex_arithmetic": ca, "complex_total": upward(Fraction(source)+Fraction(ca)),
            "envelope_arithmetic": ea, "envelope_total": upward(Fraction(source)+Fraction(ea))}
        for key, value in expected.items():
            assert report["bounds"][key][y][x] == value
        errors = {}
        for key in SIGNALS:
            aa, bb = a[key][y, x], b[key][y, x]
            d = bb-aa
            errors[key] = [abs(Fraction(float(dd))-(Fraction(float(bv))-Fraction(float(av))))
                           for av, bv, dd in zip(aa, bb, d)]
            assert all(error <= rho[key] for error in errors[key])
        complex_error = max(er+ei for er, ei in zip(errors["rf"], errors["imaginary"]))
        envelope_error = max(errors["envelope"])
        assert complex_error <= Fraction(ca)
        assert eps_a+eps_b+complex_error <= Fraction(expected["complex_total"])
        assert eps_a+eps_b+envelope_error <= Fraction(expected["envelope_total"])
        pair = (int(a["class_index"][y, x]), int(b["class_index"][y, x]))
        assert pair not in expected_by_pair or expected_by_pair[pair] == expected
        expected_by_pair[pair] = expected
        records.append({"y_index": y, "x_index": x, "reference_class": pair[0], "candidate_class": pair[1],
            "time_samples_checked_exactly": len(a["time_us"]), "bounds": expected,
            "maximum_actual_subtraction_complex_l1_error": float(complex_error),
            "maximum_actual_envelope_subtraction_error": float(envelope_error)})
    for pair, expected in expected_by_pair.items():
        selection = (a["class_index"] == pair[0]) & (b["class_index"] == pair[1])
        for key in BOUND_KEYS:
            assert np.all(np.asarray(report["bounds"][key])[selection] == expected[key])
    return {"all_column_bounds_match_independent_fraction_formula": True,
        "columns_covered": int(a["class_index"].size), "distinct_local_class_pairs": len(pairs),
        "checked_representatives": records,
        "scope": "Exact represented-input subtraction plus the two frozen source certificates. No enclosed RMSE, magnitude evaluation or detection-rate claim."}


def check_view(view, a, b, indices):
    x, y, ti = indices
    assert [view["cursor"][key] for key in ("x_index", "y_index", "time_index")] == [x, y, ti]
    for key in ("x_mm", "y_mm", "time_us"):
        assert_exact(view["coordinates"][key], a[key])
    for side, values in (("reference", a), ("candidate", b)):
        for key in SIGNALS:
            assert_exact(view["traces"][side][key], values[key][y, x])
            assert_exact(view["xy"][side][key], values[key][:, :, ti])
    for key in SIGNALS:
        assert_exact(view["traces"]["difference"][key], b[key][y, x]-a[key][y, x])
        assert_exact(view["xy"]["difference"][key], b[key][:, :, ti]-a[key][:, :, ti])


def verify_report(report, manifests, a, b):
    for key in ("x_mm", "y_mm", "time_us"):
        assert a[key].tobytes() == b[key].tobytes()
        assert_exact(report["coordinates"][key], a[key])
    assert report["sources"] == dict(zip(("reference", "candidate"), manifests))
    for role, manifest in zip(("reference", "candidate"), manifests):
        assert report["source_manifest_sha256"][role] == hashlib.sha256(canonical_json(manifest)).hexdigest()
    assert report["shape"] == list(a["rf"].shape) and report["axis_order"] == ["y", "x", "time"]
    request = report["request"]
    lo = int(np.searchsorted(a["time_us"], request["gate_start_us"], side="left"))
    hi = int(np.searchsorted(a["time_us"], request["gate_end_us"], side="right"))
    gate = report["gate"]
    assert gate["start_index"] == lo and gate["stop_index_exclusive"] == hi
    assert gate["sample_count"] == hi-lo
    assert gate["actual_start_us"] == a["time_us"][lo] and gate["actual_end_us"] == a["time_us"][hi-1]
    metrics = {"full_record": metric_oracle(report["metrics"]["full_record"], a, b, 0, len(a["time_us"])),
               "gate": metric_oracle(report["metrics"]["gate"], a, b, lo, hi)}
    for mode in ("peak_envelope", "rms_rf"):
        maps = {}
        for side, values in (("reference", a), ("candidate", b)):
            maps[side] = (values["envelope"][:, :, lo:hi].max(axis=2) if mode == "peak_envelope" else
                          np.sqrt(np.mean(values["rf"][:, :, lo:hi]**2, axis=2)))
            close(report["gate_maps"][mode][side], maps[side])
        close(report["gate_maps"][mode]["difference"], maps["candidate"]-maps["reference"])
    indices = tuple(report["initial_view"]["cursor"][key] for key in ("x_index", "y_index", "time_index"))
    check_view(report["initial_view"], a, b, indices)
    return {"metrics": metrics, "fraction_certificate_checks": fraction_bounds(report, manifests, a, b),
        "signed_trace_and_slice_bytes_exact": True, "gate_maps_match_independent_reductions": True,
        "shared_coordinates_bit_exact": True, "ordinary_metric_tolerances": ORACLE_TOLERANCE}


def historical_exports(api, root, directory, report):
    identifier, store = report["id"], CausalComparisonStore(root)
    route = f"{PREFIX}/{identifier}"
    expected_bytes = (root/"causal-comparisons"/f"{identifier}.json").read_bytes()
    assert api.json(route) == report
    json_export = api.bytes(route+"/export?format=json")
    csv_export = api.bytes(route+"/export?format=csv")
    assert json_export == expected_bytes == canonical_json(report)
    prior_limit = csv.field_size_limit(64*1024**2)
    try:
        decoded = {}
        for row in csv.DictReader(io.StringIO(csv_export.decode("utf8"))):
            assert row["section"] == "report" and row["field"] not in decoded
            decoded[row["field"]] = json.loads(row["value_json"])
    finally:
        csv.field_size_limit(prior_limit)
    assert decoded == report and canonical_json(decoded) == json_export
    assert report["report_sha256"] == hashlib.sha256(canonical_json({k: v for k, v in report.items() if k != "report_sha256"})).hexdigest()
    assert report["request_sha256"] == hashlib.sha256(canonical_json(report["request"])).hexdigest()
    with ExitStack() as traps:
        for target in ("virtual_microscopy.causal_comparisons.compute_causal_comparison",
            "virtual_microscopy.causal_comparisons.causal_comparison_view", "virtual_microscopy.causal_datasets.CausalSamDatasetStore.verify_complete",
            "virtual_microscopy.causal_sam.causal_gamma_response", "virtual_microscopy.causal_sam.estimate_causal_sam",
            "virtual_microscopy.causal_sam.prepare_causal_sam", "virtual_microscopy.layered_time.causal_gamma_response",
            "virtual_microscopy.layered_time.estimate_causal_gamma", "virtual_microscopy.causal_comparison_store._fingerprints",
            "virtual_microscopy.causal_comparison_store._runtime"):
            traps.enter_context(patch(target, side_effect=AssertionError("Historical report reopened current processing or source machinery")))
        assert store.read(identifier) == report
        assert store.view(identifier) == report["initial_view"]
        # Use a separate fresh root containing only this report. Source absence
        # is literal here; the original source directories are never moved.
        offline = directory/"offline-report-only"
        (offline/"causal-comparisons").mkdir(parents=True)
        write_bytes(offline/"causal-comparisons"/f"{identifier}.json", expected_bytes)
        historical = CausalComparisonStore(offline)
        assert historical.read(identifier) == report and historical.view(identifier) == report["initial_view"]
    assert api.json(route+"/view") == report["initial_view"]
    write_bytes(directory/"report-export.json", json_export)
    write_bytes(directory/"report-export.csv", csv_export)
    return {"json_bytes": len(json_export), "csv_bytes": len(csv_export),
        "json_exact_saved_file": True, "csv_lossless_json_cells": True,
        "offline_report_only_root_verified": True, "historical_source_core_and_kernel_traps_passed": True}


def rejected_incompatible(api, a, slab):
    before = catalog(api)
    jobs = api.json("/api/v2/jobs")
    request = {"name": "Expected incompatible HBM and prior slab rejection", "reference_dataset_id": a,
        "candidate_dataset_id": slab, "gate_start_us": .32, "gate_end_us": .4}
    try:
        with urlopen(Request(api.base+PREFIX, data=canonical_json(request),
                headers={"Content-Type": "application/json"}), timeout=180) as response:
            raise AssertionError(f"Incompatible report unexpectedly returned HTTP {response.status}")
    except HTTPError as exc:
        assert exc.code == 422
        result = json.loads(exc.read())
    assert catalog(api) == before and api.json("/api/v2/jobs") == jobs
    return {"request": request, "status": 422, "response": result,
        "comparison_catalog_unchanged": True, "job_catalog_unchanged": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8767")
    parser.add_argument("--data-root", type=Path, default=Path("artifacts/volumes-v07"))
    parser.add_argument("--active-instance", type=Path, default=Path("artifacts/ACTIVE_INSTANCE.json"))
    args = parser.parse_args()
    output, root = args.output.resolve(), args.data_root.resolve()
    output.mkdir(parents=True, exist_ok=False)  # Must precede all API calls.
    start = perf_counter()
    run = {"schema_version": 1, "run_id": str(uuid4()), "created_at": now_iso(), "api_url": args.url,
        "data_root": str(root), "no_overwrite": True, "version": "0.14.0",
        "helper_source_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in HELPERS},
        "evidence_status": "Numerical saved-data comparison evidence; no measured accuracy, physical detectability or new acquisition.",
        "oracle_scope": "Whole arrays only in this bounded delivery QA. Independent Fraction subtraction checks; ordinary direct metric and gate reductions."}
    write_json(output/"run.json", run)
    try:
        active, previous, legacy_ids = legacy_deliveries(args.active_instance.resolve(), Path(__file__).resolve().parents[1], root)
        api = API(args.url)
        health, jobs_before = api.json("/api/health"), api.json("/api/v2/jobs")
        assert health["status"] == "ok" and health["version"] == "0.14.0", health
        assert not any(j["status"] in {"queued", "running", "cancelling"} for j in jobs_before["jobs"])
        causal_ids = {j["dataset_id"] for j in jobs_before["jobs"] if j.get("kind") == "sam_causal_rf_volume"}
        old_sources = {identifier: source_hashes(root/identifier) for identifier in sorted(set(legacy_ids)|causal_ids)}
        assert sum(len(old_sources[i]) for i in legacy_ids) == 524
        report_directories = ("layered-reports", "comparisons", "xray-comparisons", "causal-comparisons")
        old_reports = {name: directory_hashes(root/name) for name in report_directories}
        assert len(old_reports["layered-reports"]) >= 19
        before_catalog = catalog(api)
        write_json(output/"active-instance-snapshot.json", active)
        write_json(output/"previous-deliveries.json", previous)
        write_json(output/"health.json", health)
        write_json(output/"jobs-before.json", jobs_before)
        write_json(output/"comparison-catalog-before.json", before_catalog)
        write_json(output/"source-hashes-before.json", old_sources)
        write_json(output/"report-hashes-before.json", old_reports)
        ids = active["delivered_causal_volume_dataset_ids"]
        intact, damaged, slab = ids["hbm6-intact"], ids["hbm6-missing-bump"], ids["analytic-silicon-slab"]
        am, a = source_arrays(root, intact)
        bm, b = source_arrays(root, damaged)
        assert am["shape"] == bm["shape"] == [64, 32, 1601]
        geometry = controlled_pair_identity(am, bm, a, b)
        write_json(output/"controlled-pair-identity.json", geometry)
        write_json(output/"rejected-incompatible.json", rejected_incompatible(api, intact, slab))
        cases = [("hbm6-controlled-defect-gate", intact, damaged, .32, .4, am, bm, a, b),
                 ("hbm6-same-source-zero", intact, intact, .32, .4, am, am, a, a),
                 ("hbm6-controlled-defect-full-record", intact, damaged, 0., 2., am, bm, a, b)]
        records, report_ids = [], {}
        for slug, aid, bid, start_us, end_us, ma, mb, av, bv in cases:
            directory = output/slug
            directory.mkdir()
            request = {"name": f"v0.14 {slug}", "reference_dataset_id": aid, "candidate_dataset_id": bid,
                "policy": "same_excitation_v1", "gate_start_us": start_us, "gate_end_us": end_us,
                "x_index": 9, "y_index": 42, "time_index": 265}
            write_json(directory/"request.json", request)
            report = api.json(PREFIX, request)
            write_json(directory/"report.json", report)
            assert report["request"] == request
            checks = verify_report(report, (ma, mb), av, bv)
            exports = historical_exports(api, root, directory, report)
            cursor = (10, 43, 266)
            current = api.json(f"{PREFIX}/{report['id']}/view?x_index={cursor[0]}&y_index={cursor[1]}&time_index={cursor[2]}")
            check_view(current, av, bv, cursor)
            for role, manifest in (("reference", ma), ("candidate", mb)):
                assert current["source_summaries"][role]["manifest_sha256"] == hashlib.sha256(canonical_json(manifest)).hexdigest()
            write_json(directory/"changed-cursor.json", current)
            write_json(directory/"verification.json", {**checks, "exports": exports})
            record = {"experiment": slug, "report_id": report["id"], "shape": report["shape"],
                "source_ids": [aid, bid], "gate": report["gate"], "metrics": report["metrics"],
                "selected_bounds": report["initial_view"]["selected_bounds"],
                "columns_covered_by_fraction_oracle": checks["fraction_certificate_checks"]["columns_covered"],
                "fraction_representatives": len(checks["fraction_certificate_checks"]["checked_representatives"]),
                "exports": exports}
            records.append(record)
            report_ids[slug] = report["id"]
            print(json.dumps({"status": "verified", "experiment": slug, "report_id": report["id"],
                "rf_max": report["metrics"]["gate"]["rf"]["max_absolute_difference"]}), flush=True)
        jobs_after, after_catalog = api.json("/api/v2/jobs"), catalog(api)
        assert jobs_before == jobs_after
        assert {r["id"] for r in after_catalog}-{r["id"] for r in before_catalog} == set(report_ids.values())
        after_sources = {identifier: source_hashes(root/identifier) for identifier in old_sources}
        assert after_sources == old_sources
        after_reports = {name: directory_hashes(root/name) for name in report_directories}
        for name, files in old_reports.items():
            assert all(after_reports[name].get(path) == record for path, record in files.items())
            if name != "causal-comparisons":
                assert after_reports[name] == files
        assert set(after_reports["causal-comparisons"])-set(old_reports["causal-comparisons"]) == {f"{value}.json" for value in report_ids.values()}
        for slug, identifier in report_ids.items():
            assert (root/"causal-comparisons"/f"{identifier}.json").read_bytes() == (output/slug/"report-export.json").read_bytes()
        write_json(output/"jobs-after.json", jobs_after)
        write_json(output/"comparison-catalog-after.json", after_catalog)
        write_json(output/"source-hashes-after.json", after_sources)
        write_json(output/"report-hashes-after.json", after_reports)
        final = {**run, "status": "passed", "elapsed_seconds": perf_counter()-start, "reports": records,
            "report_ids": report_ids, "controlled_pair": geometry, "volume_jobs_created": 0, "job_catalog_unchanged": True,
            "legacy_dataset_files_preserved": 524, "causal_datasets_preserved": sorted(causal_ids),
            "layered_report_files_preserved": len(old_reports["layered-reports"]),
            "preexisting_comparison_report_files_preserved": sum(len(old_reports[name]) for name in report_directories if name != "layered-reports"),
            "all_previous_files_unchanged": True, "all_created_reports_unchanged_by_read_views_exports": True,
            "comparison_reports_created": 3, "incompatible_source_rejection_catalogs_unchanged": True}
        write_json(output/"verification-report.json", final)
        print(json.dumps({"status": "passed", "output": str(output), "report_ids": report_ids,
                          "elapsed_seconds": final["elapsed_seconds"]}), flush=True)
    except BaseException as exc:
        write_json(output/"failure.json", {"type": type(exc).__name__, "message": str(exc), "elapsed_seconds": perf_counter()-start})
        raise


if __name__ == "__main__":
    main()
