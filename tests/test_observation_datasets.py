"""Frozen source plans, typed observation rows and source-independent history."""
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4

import numpy as np
import pytest
import zarr

from virtual_microscopy import observation_datasets as storage, observation_plan as planning
from virtual_microscopy.observation_datasets import ObservationStore
from virtual_microscopy.observation_plan import plan_observation


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    from virtual_microscopy.causal_sam import prepare_causal_sam, iter_causal_sam_rows
    from virtual_microscopy.causal_datasets import CausalSamDatasetStore
    from virtual_microscopy.observation_math import observe_row
    root = tmp_path_factory.mktemp("observation-template")
    p = prepare_causal_sam({"kind": "sam_causal_rf_volume", "twin": {"name": "Observation source slab",
        "size_mm": [1, 1, .1], "objects": [{"id": "slab", "name": "Si", "shape": "box",
        "center_mm": [.5, .5, .05], "size_mm": [1, 1, .1], "role": "structure", "material": "silicon"}]},
        "acquisition": {"scan_nx": 16, "scan_ny": 16, "center_frequency_mhz": 10.,
                        "sample_rate_mhz": 80., "record_duration_us": .5}})
    identifier = str(uuid4())
    source = CausalSamDatasetStore(root)
    source.create(identifier, p.request.model_dump(mode="json", exclude_none=True), p.estimate)
    source.initialize_arrays(identifier, p)
    for item in iter_causal_sam_rows(p): source.write_row(identifier, *item)
    source.complete(identifier)
    group = source.open_arrays(identifier)
    window = {"rf": group["rf"][:3], "imaginary": group["imaginary"][:3], "bounds": group["error_bound"][:3]}
    result = observe_row(window["rf"], window["imaginary"], window["bounds"], absolute_tolerance=1e-7)
    p.close()
    return root, identifier, window, result


@pytest.fixture
def prepared(tmp_path, template):
    root, source_id, window, result = template
    shutil.copytree(root/source_id, tmp_path/source_id)
    request = {"kind": planning.KIND, "name": "Stored finite coherent filter", "source_dataset_id": source_id,
               "operator": planning.OPERATOR, "absolute_tolerance": 1e-7}
    plan = plan_observation(tmp_path, request)
    store, identifier = ObservationStore(tmp_path), str(uuid4())
    store.create(identifier, request, plan)
    store.initialize_arrays(identifier)
    return store, identifier, plan, deepcopy(window), deepcopy(result)


def finish(prepared):
    store, identifier, plan, window, result = prepared
    for y in range(plan["total_rows"]): store.write_row(identifier, y, result, window)
    return store.complete(identifier)


def hashes(path):
    return {p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob("*") if p.is_file()}


def test_plan_exact_interior_source_indices_weights_and_budget(prepared):
    store, identifier, plan, _, _ = prepared
    source = plan["source_manifest"]
    assert plan["shape"] == [14, 14, 41] and plan["total_rows"] == 14
    for name in ("x_mm", "y_mm", "time_us"):
        original = np.asarray(source["estimate"][name], np.float64)
        expected = original if name == "time_us" else original[1:-1]
        assert np.asarray(plan[name], np.float64).tobytes() == expected.tobytes()
    assert plan["source_indices"]["x"] == plan["source_indices"]["y"] == list(range(1, 15))
    assert sum(plan["operator"]["weight_numerators"]) == plan["operator"]["weight_denominator"] == 16
    assert plan["physical_neighbor_offsets"]["x_mm"][0] == [-.0625, 0., .0625]
    assert plan["extent_mm"] == [.0625, .9375, .0625, .9375]
    assert plan["source_shape"] == [16, 16, 41]
    assert plan["source_summary"]["dataset_id"] == source["dataset_id"]
    assert plan["temporal_block_samples"] == 64 and plan["weighted_component_terms"] == 18*14*14*41
    assert plan["total_bytes"] <= planning.MAX_BYTES and plan["estimated_peak_bytes"] <= planning.MAX_BYTES
    assert str(store.root) not in json.dumps(plan)
    assert store.commit_bytes(store.manifest(identifier), 0, 1) == 14*(24*41+40)


def test_typed_complete_rows_and_safe_export_exact_inventory(prepared):
    store, identifier, plan, _, result = prepared
    source_path = store.root/plan["request"]["source_dataset_id"]
    before = hashes(source_path)
    m = finish(prepared)
    assert store.verify_complete(identifier) == m
    group = store._open_checked(identifier, m)
    for key in storage.PRODUCTS:
        assert group[key].dtype == np.dtype("float64")
        assert np.asarray(group[key][0]).tobytes() == result[key].tobytes()
    assert group["rf"][:].min() < 0 and np.any(group["imaginary"][:] != 0)
    assert m["maximum_complex_bound"] <= 1e-7 and m["maximum_magnitude_bound"] <= 1e-7
    files = store.safe_export_files(identifier)
    assert set(files) == {p for p in store.path(identifier).rglob("*") if p.is_file()}
    assert len(files) == 2+11+3+8*14
    assert sum(p.stat().st_size for p in files) < plan["total_bytes"]
    assert hashes(source_path) == before


def test_historical_complete_without_source_current_identity_or_forward(prepared, monkeypatch):
    import virtual_microscopy.observation_math as math_module
    store, identifier, plan, _, _ = prepared
    m = finish(prepared)
    before = hashes(store.path(identifier))
    (store.root/plan["request"]["source_dataset_id"]).rename(store.root/"source-unavailable")
    def blocked(*a, **k): pytest.fail("Historical observation must not rerun or reopen source")
    monkeypatch.setattr(storage, "observation_identity", blocked)
    monkeypatch.setattr(storage, "source_context", blocked)
    monkeypatch.setattr(math_module, "observe_row", blocked)
    monkeypatch.setattr(math_module, "verify_row", blocked)
    storage._row_cache.clear()
    assert store.verify_complete(identifier) == m
    assert store.validate_identity(identifier) == m
    assert store.safe_export_files(identifier)
    assert hashes(store.path(identifier)) == before


def test_cancel_resume_preserves_committed_row_bytes_and_deterministic_remaining(prepared):
    store, identifier, plan, window, result = prepared
    first = store.write_row(identifier, 0, result, window)
    row_bytes = {key: store._chunk(identifier, first, key, 0).read_bytes() for key in storage.PRODUCTS}
    store.set_state(identifier, "cancelled")
    fresh = ObservationStore(store.root)
    checked = fresh.verify_chunks(identifier)
    assert set(checked["completed_chunks"]) == {"0"}
    for y in range(1, plan["total_rows"]): fresh.write_row(identifier, y, result, window)
    complete = fresh.complete(identifier)
    for key, value in row_bytes.items():
        assert fresh._chunk(identifier, complete, key, 0).read_bytes() == value


@pytest.mark.parametrize("failure", ["missing", "corrupt"])
def test_partial_corruption_drops_only_bad_commit_and_regenerates(prepared, failure):
    store, identifier, _, window, result = prepared
    store.write_row(identifier, 0, result, window)
    m = store.write_row(identifier, 1, result, window)
    original = store._chunk(identifier, m, "rf", 1).read_bytes()
    target = store._chunk(identifier, m, "rf", 0)
    if failure == "missing": target.unlink()
    else: target.write_bytes(bytes([target.read_bytes()[0]^1])+target.read_bytes()[1:])
    checked = store.verify_chunks(identifier)
    assert set(checked["completed_chunks"]) == {"1"}
    store.write_row(identifier, 0, result, window)
    assert store._chunk(identifier, m, "rf", 1).read_bytes() == original


@pytest.mark.parametrize("field", ["rf", "imaginary", "bounds"])
def test_wrong_source_window_rejected_before_output_write(prepared, monkeypatch, field):
    store, identifier, _, window, result = prepared
    window[field].flat[0] += 1e-5 if field != "bounds" else 1e-12
    monkeypatch.setattr(store, "_open_checked", lambda *a, **k: pytest.fail("Bad source must reject before output writes"))
    with pytest.raises(ValueError, match="source-window"): store.write_row(identifier, 0, result, window)
    assert store.manifest(identifier)["completed_rows"] == 0


def test_forged_signal_and_underreported_bounds_rejected(prepared):
    store, identifier, _, window, result = prepared
    for mode in ("signal", "bounds"):
        bad = deepcopy(result)
        if mode == "signal": bad["rf"][0, 0] += .1
        else:
            for key in storage.BOUNDS: bad[key].fill(0.)
        with pytest.raises(ValueError): store.write_row(identifier, 0, bad, window)
    assert store.manifest(identifier)["completed_rows"] == 0


def test_cancel_checkpoint_leaves_no_committed_row(prepared):
    from virtual_microscopy.observation_math import ObservationCancelled
    store, identifier, _, window, result = prepared
    with pytest.raises(ObservationCancelled): store.write_row(identifier, 0, result, window, cancelled=lambda: True)
    assert store.manifest(identifier)["completed_rows"] == 0


def test_atomic_manifest_failure_leaves_uncommitted_owned_chunks_for_resume(prepared, monkeypatch):
    store, identifier, _, window, result = prepared
    original = storage.atomic_json
    monkeypatch.setattr(storage, "atomic_json", lambda *a, **k: (_ for _ in ()).throw(OSError("injected before publication")))
    with pytest.raises(OSError): store.write_row(identifier, 0, result, window)
    monkeypatch.setattr(storage, "atomic_json", original)
    assert store.manifest(identifier)["completed_rows"] == 0
    assert store.write_row(identifier, 0, result, window)["completed_rows"] == 1


def test_complete_is_immutable_even_after_data_corruption(prepared):
    store, identifier, _, window, result = prepared
    m = finish(prepared)
    for action in (lambda: store.write_row(identifier, 0, result, window),
                   lambda: store.set_state(identifier, "running"), lambda: store.verify_chunks(identifier)):
        with pytest.raises(ValueError): action()
    path = store._chunk(identifier, m, "rf", 0)
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError): store.verify_complete(identifier)


def test_partial_identity_and_changed_source_reject_resume(prepared, monkeypatch):
    store, identifier, plan, _, _ = prepared
    with monkeypatch.context() as context:
        context.setattr(storage, "observation_identity", lambda: {"changed": True})
        with pytest.raises(ValueError, match="implementation/runtime"): store.validate_identity(identifier)
    source_path = store.root/plan["request"]["source_dataset_id"] / "manifest.json"
    value = json.loads(source_path.read_bytes())
    value["updated_at"] = "changed source timestamp"
    source_path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="source changed"): store.validate_identity(identifier)


@pytest.mark.parametrize("target", ["signal_shape", "signal_chunks", "coordinate_chunks", "codec"])
def test_raw_zarr_metadata_guards_precede_decoder(prepared, monkeypatch, target):
    store, identifier, _, _, _ = prepared
    key = "x_mm" if target == "coordinate_chunks" else "rf"
    path = store.path(identifier)/"data.zarr"/key/"zarr.json"
    raw = json.loads(path.read_bytes())
    if target == "signal_shape": raw["shape"][0] = 1000000
    elif target == "codec": raw["codecs"].append({"name": "zstd"})
    else: raw["chunk_grid"]["configuration"]["chunk_shape"][0] = 1000000
    path.write_text(json.dumps(raw))
    monkeypatch.setattr(zarr, "open_group", lambda *a, **k: pytest.fail("Raw metadata guard precedes decoding"))
    with pytest.raises(ValueError, match="Noncanonical"): store._open_checked(identifier, store.manifest(identifier))


def test_fresh_row_hash_precedes_cached_numerical_read_acceptance(prepared, monkeypatch):
    import virtual_microscopy.observation_math as math_module
    store, identifier, _, window, result = prepared
    m = store.write_row(identifier, 0, result, window)
    group = store._open_checked(identifier, m)
    store._read_row(identifier, m, group, 0)
    monkeypatch.setattr(math_module, "verify_saved_row", lambda *a, **k: pytest.fail("Identical verified row should use bounded cache"))
    store._read_row(identifier, m, group, 0)
    target = store._chunk(identifier, m, "rf", 0)
    changed = result["rf"].copy(); changed[0, 0] += .1
    target.write_bytes(changed.astype("<f8").tobytes())
    # Typed registry rejection precedes any numerical verifier or cached result.
    with pytest.raises(ValueError, match="checksum"): store._read_row(identifier, m, group, 0)


def test_cache_size_is_bounded_and_retains_no_arrays(prepared, monkeypatch):
    store, identifier, _, _, result = prepared
    m = store.manifest(identifier)
    storage._row_cache.clear()
    monkeypatch.setattr(storage, "MAX_CACHE_ENTRIES", 2)
    for extra in (0., 1e-12, 2e-12):
        values = deepcopy(result)
        for key in ("complex_arithmetic", "complex_total", "magnitude_arithmetic", "magnitude_total"):
            values[key] += extra
        # Conservative totals must still enclose both displayed components.
        values["magnitude_total"] += 2*extra
        store._validate_row(m, 0, values)
    assert len(storage._row_cache) == 2
    assert all(value is True for value in storage._row_cache.values())


@pytest.mark.parametrize("field,value", [("total_bytes", 1), ("estimated_peak_bytes", 1),
    ("estimated_manifest_bytes", 1), ("estimated_manifest_expanded_bytes", 1), ("shape", [14, 14, 42]),
    ("source_indices", {"y": [0], "x": [0], "time": [0]}), ("extent_mm", [0, 0, 1, 1])])
def test_frozen_plan_tamper_rejected(prepared, field, value):
    _, _, plan, _, _ = prepared
    bad = deepcopy(plan); bad[field] = value
    with pytest.raises(ValueError): planning.validate_plan(bad)


def test_too_tight_source_bound_and_output_work_limit_reject(prepared, monkeypatch):
    store, _, plan, _, _ = prepared
    request = {**plan["request"], "absolute_tolerance": 1e-12}
    with pytest.raises(ValueError, match="propagated"): plan_observation(store.root, request)
    monkeypatch.setattr(planning, "MAX_OUTPUT_SAMPLES", 1)
    with pytest.raises(ValueError, match="three million"): plan_observation(store.root, plan["request"])


def test_source_json_nonfinite_duplicate_and_expansion_rejected_before_authority(prepared, monkeypatch):
    from virtual_microscopy.causal_datasets import CausalSamDatasetStore
    store, _, plan, _, _ = prepared
    path = store.root/plan["request"]["source_dataset_id"] / "manifest.json"
    monkeypatch.setattr(CausalSamDatasetStore, "verify_complete", lambda *a, **k: pytest.fail("JSON preflight precedes source arrays"))
    for payload in (b'{"a":1,"a":2}', b'{"a":1e400}', b'['*66+b'0'+b']'*66):
        path.write_bytes(payload)
        with pytest.raises(ValueError): plan_observation(store.root, plan["request"])


def test_path_and_junction_rejection(tmp_path, monkeypatch):
    store = ObservationStore(tmp_path)
    with pytest.raises(ValueError): store.manifest("../escape")
    identifier = str(uuid4())
    monkeypatch.setattr(Path, "is_junction", lambda p: p == tmp_path/identifier)
    with pytest.raises(ValueError, match="links"): store.path(identifier)


def decimal_source(plan):
    source = deepcopy(plan["source_manifest"])
    a = source["request"]["acquisition"]
    a["roi_mm"] = [49.425, 39.875, 49.575, 40.125]
    nx, ny = source["shape"][1], source["shape"][0]
    roi = a["roi_mm"]
    for name, count, start, end in (("x_mm", nx, roi[0], roi[2]), ("y_mm", ny, roi[1], roi[3])):
        values = start+(np.arange(count, dtype=np.float64)+.5)*((end-start)/count)
        source["estimate"][name] = values.tolist()
        source["coordinates_sha256"][name] = storage.typed_sha256(values)
    source["estimate"]["extent_mm"] = [roi[0], roi[2], roi[1], roi[3]]
    return source


def test_decimal_generator_preserves_actual_centers_and_single_rounded_midpoints(prepared):
    _, _, plan, _, _ = prepared
    source = decimal_source(plan)
    shape, coords, _, extent, _ = planning._facts(source)
    sx = np.asarray(source["estimate"]["x_mm"], np.float64)
    sy = np.asarray(source["estimate"]["y_mm"], np.float64)
    assert len(set(np.diff(sx))) > 1  # Honest binary64 spacing is not constant.
    assert coords["x_mm"].tobytes() == sx[1:-1].tobytes()
    assert coords["y_mm"].tobytes() == sy[1:-1].tobytes()
    assert extent == [float((Fraction(float(a[i]))+Fraction(float(a[j])))/2)
        for a, i, j in ((sx, 0, 1), (sx, -2, -1), (sy, 0, 1), (sy, -2, -1))]
    assert shape == [14, 14, 41]


@pytest.mark.parametrize("name", ["x_mm", "y_mm", "time_us"])
def test_rehashed_one_ulp_coordinate_mutation_fails_frozen_generator(prepared, name):
    _, _, plan, _, _ = prepared
    source = decimal_source(plan)
    values = np.asarray(source["estimate"][name], np.float64)
    values[4] = np.nextafter(values[4], np.inf)
    source["estimate"][name] = values.tolist()
    source["coordinates_sha256"][name] = storage.typed_sha256(values)
    with pytest.raises(ValueError, match="exact frozen coordinate generator"):
        planning._facts(source)


def test_output_cap_precedes_source_signal_decode(prepared, monkeypatch):
    store, _, plan, _, _ = prepared
    monkeypatch.setattr(planning, "MAX_OUTPUT_SAMPLES", 1)
    monkeypatch.setattr(planning.CausalSamDatasetStore, "verify_complete",
        lambda *a, **k: pytest.fail("Output work admission must precede source signals"))
    with pytest.raises(ValueError, match="three million"):
        plan_observation(store.root, plan["request"])


def test_large_frozen_metadata_is_accounted_and_stale_estimates_reject(prepared):
    _, _, plan, _, _ = prepared
    source = deepcopy(plan["source_manifest"])
    source["author_note"] = "x"*200_000
    base = planning._base(plan["request"], source)
    resources = planning._resources(base)
    assert resources["estimated_peak_bytes"] > plan["estimated_peak_bytes"]+10_000_000
    enlarged = {**base, **resources}
    planning.validate_plan(enlarged)
    for field in ("total_bytes", "estimated_peak_bytes", "estimated_manifest_bytes", "estimated_manifest_expanded_bytes"):
        bad = deepcopy(enlarged); bad[field] = plan[field]
        with pytest.raises(ValueError, match="resource estimate"):
            planning.validate_plan(bad)
    source["author_note"] = "x"*(4*1024**2)
    with pytest.raises(ValueError, match="bounded JSON/expanded"):
        planning._resources(planning._base(plan["request"], source))


def test_plan_numerical_contract_comes_from_math_metadata(prepared):
    from virtual_microscopy.observation_math import operator_metadata
    _, _, plan, _, _ = prepared
    assert all(plan["operator"][key] == value for key, value in operator_metadata().items())


def test_cancellation_during_final_checks_preserves_partial_commits(prepared):
    from virtual_microscopy.observation_math import ObservationCancelled
    store, identifier, plan, window, result = prepared
    for y in range(plan["total_rows"]): store.write_row(identifier, y, result, window)
    calls = 0
    def cancelled():
        nonlocal calls
        calls += 1
        return calls == 4
    with pytest.raises(ObservationCancelled): store.complete(identifier, cancelled=cancelled)
    m = store.manifest(identifier)
    assert not m["complete"] and m["completed_rows"] == 14
    assert store.complete(identifier)["complete"] is True


def test_frozen_source_zero_certificate_rejected_even_with_resealed_hashes(prepared):
    from virtual_microscopy.causal_datasets import CausalSamDatasetStore, json_sha256
    _, _, plan, _, _ = prepared
    source = deepcopy(plan["source_manifest"])
    for record in source["class_certificates"].values():
        for field in ("analytic_alias_bound", "frequency_cutoff_bound", "arithmetic_complex_bound",
                      "arithmetic_envelope_bound", "total_error_bound"):
            record["diagnostics"][field] = 0.
        record["diagnostics_sha256"] = json_sha256(record["diagnostics"])
        record["certificate_sha256"] = json_sha256({k: v for k, v in record.items() if k != "certificate_sha256"})
    for row in source["completed_chunks"].values():
        row["certificate_sha256"] = {key: source["class_certificates"][key]["certificate_sha256"]
                                     for key in row["certificate_sha256"]}
    source["total_error_bound"] = 0.
    source["completion_sha256"] = json_sha256(CausalSamDatasetStore._completion_payload(source))
    bad = planning._base(plan["request"], source)
    bad.update(planning._resources(bad))
    with pytest.raises(ValueError, match="admitted kernel plan"):
        planning.validate_plan(bad)


def test_plan_cache_is_bounded_and_keys_full_fresh_content(prepared, monkeypatch):
    _, _, plan, _, _ = prepared
    planning._plan_cache.clear()
    monkeypatch.setattr(planning, "MAX_PLAN_CACHE_ENTRIES", 2)
    for name in ("first", "second", "third"):
        base = planning._base({**plan["request"], "name": name}, plan["source_manifest"])
        base.update(planning._resources(base))
        planning.validate_plan(base)
    assert len(planning._plan_cache) == 2
    assert all(value is True for value in planning._plan_cache.values())
    bad = deepcopy(base); bad["estimated_peak_bytes"] = 1
    with pytest.raises(ValueError, match="resource estimate"):
        planning.validate_plan(bad)
