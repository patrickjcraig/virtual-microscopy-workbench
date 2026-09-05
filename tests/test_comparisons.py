"""Independent stored-array oracles for SAM comparison science and integrity."""
from copy import deepcopy
import csv
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import numpy as np
import pytest
from pydantic import ValidationError
import zarr

from virtual_microscopy.comparison_schemas import SamComparisonRequest
from virtual_microscopy.comparisons import (ComparisonCompatibilityError, ComparisonStore,
    comparison_csv, compute_comparison)
from virtual_microscopy.datasets import (DatasetStore, array_sha256, coordinate_sha256,
                                         json_sha256)


TIME_ZERO = "transducer reference plane; specimen-top arrival includes two-way water standoff delay"


def signals(shape=(16, 20, 9)):
    y, x, t = np.indices(shape)
    rf = ((y-7)*.125+x*.0625+(t-4)*.25).astype(np.float32)
    envelope = (1+y*.125+x*.0625+t*.03125).astype(np.float32)
    return rf, envelope


def save(root, rf, envelope, *, coordinates=None, time_zero=TIME_ZERO, acquisition=None):
    store, identifier = DatasetStore(root), str(uuid4())
    ny, nx, nt = rf.shape
    request = {"twin": {"name": "Independent historical array fixture", "size_mm": [10, 10, 1]},
               "acquisition": {"record_start_us": .5, "sample_rate_mhz": 8,
                   "focus_mm": .25, **(acquisition or {})}}
    store.create(identifier, request, {"shape": [ny, nx, nt], "tile_rows": 4,
        "total_bytes": rf.size*8+(nx+ny+nt)*8, "model_version": "sam-volume-0.7.0"})
    coords = coordinates or {"x_mm": 2+(np.arange(nx)+.5)*.125,
                            "y_mm": 4+(np.arange(ny)+.5)*.25,
                            "time_us": .5+np.arange(nt)*.125}
    store.initialize_arrays(identifier, coords["x_mm"], coords["y_mm"], coords["time_us"],
                            {"time_zero": time_zero, "rf_unit": "relative signed pressure", "envelope_unit": "relative echo amplitude"})
    for y0 in range(0, ny, 4):
        store.write_tile(identifier, y0, min(ny, y0+4), rf[y0:y0+4], envelope[y0:y0+4])
    store.complete(identifier)
    return identifier


def config(a, b, **changes):
    return SamComparisonRequest(reference_dataset_id=a, candidate_dataset_id=b,
        gate_start_us=changes.pop("gate_start_us", .75), gate_end_us=changes.pop("gate_end_us", 1.25),
        **changes)


def file_hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def pair(tmp_path):
    rf, env = signals()
    candidate_rf, candidate_env = (2*rf+.5).astype(np.float32), (env*1.5+.25).astype(np.float32)
    a = save(tmp_path, rf, env)
    b = save(tmp_path, candidate_rf, candidate_env, acquisition={"focus_mm": .75})
    return tmp_path, a, b, rf, env, candidate_rf, candidate_env


def expected_metrics(a, b):
    a, b = a.astype(float), b.astype(float)
    d = b-a
    return {"bias": float(np.mean(d)), "mae": float(np.mean(abs(d))),
            "rmse": float(np.sqrt(np.mean(d*d))), "max_absolute_difference": float(np.max(abs(d))),
            "relative_l2": float(np.linalg.norm(d.ravel())/np.linalg.norm(a.ravel())),
            "reference_l2": float(np.linalg.norm(a.ravel()))}


def test_asymmetric_scaling_offset_metrics_gate_products_and_maximum_locator(pair):
    root, a, b, rf, env, brf, benv = pair
    result = compute_comparison(root, config(a, b, x_index=3, y_index=7))
    for name, left, right in (("rf", rf, brf), ("envelope", env, benv)):
        for key, expected in expected_metrics(left, right).items():
            assert result["metrics"][name][key] == pytest.approx(expected, rel=1e-13, abs=1e-13)
        index = np.unravel_index(np.argmax(abs(right.astype(float)-left.astype(float))), left.shape)
        locator = result["metrics"][name]["max_location"]
        assert [locator[k] for k in ("y_index", "x_index", "time_index")] == list(index)
        assert locator["signed_difference"] == float(right[index]-left[index])
        assert locator["x_mm"] == result["coordinates"]["x_mm"][index[1]]
        assert locator["time_us"] == result["coordinates"]["time_us"][index[2]]
    assert result["gate"]["sample_count"] == 5
    assert result["gate"]["start_index"] == 2 and result["gate"]["stop_index_exclusive"] == 7
    for mode, left, right in (("peak_envelope", env[:, :, 2:7].max(2), benv[:, :, 2:7].max(2)),
        ("rms_rf", np.sqrt(np.mean(rf[:, :, 2:7].astype(float)**2, 2)), np.sqrt(np.mean(brf[:, :, 2:7].astype(float)**2, 2)))):
        values = result["gate_maps"][mode]
        np.testing.assert_allclose(values["reference"], left, atol=0, rtol=1e-15)
        np.testing.assert_allclose(values["candidate"], right, atol=0, rtol=1e-15)
        np.testing.assert_allclose(values["difference"], right-left, atol=0, rtol=1e-15)
        assert values["metrics"]["sample_count"] == 16*20
        assert "time_index" not in values["metrics"]["max_location"]
    assert result["selected_trace"] == {"x_index": 3, "y_index": 7, "x_mm": 2.4375, "y_mm": 5.875}
    assert result["differences"]["settings"] == [{"path": "acquisition.focus_mm", "reference": .25, "candidate": .75}]


def test_identical_sources_have_exact_zero_and_first_ordered_maximum(pair):
    root, a, _, _, _, _, _ = pair
    result = compute_comparison(root, config(a, a))
    for metrics in (result["metrics"]["rf"], result["metrics"]["envelope"],
                    result["gate_maps"]["peak_envelope"]["metrics"], result["gate_maps"]["rms_rf"]["metrics"]):
        for key in ("bias", "mae", "rmse", "max_absolute_difference", "relative_l2"):
            assert metrics[key] == 0
        assert metrics["max_location"]["y_index"] == metrics["max_location"]["x_index"] == 0


def test_sign_reversal_keeps_signed_rf_and_does_not_replace_envelope(tmp_path):
    rf, env = signals()
    a, b = save(tmp_path, rf, env), save(tmp_path, -rf, env+.125)
    store = ComparisonStore(tmp_path)
    report = store.create(config(a, b))
    assert report["metrics"]["rf"]["relative_l2"] == 2
    assert report["metrics"]["envelope"]["mae"] == .125
    assert report["gate_maps"]["rms_rf"]["metrics"]["max_absolute_difference"] == 0
    assert report["gate_maps"]["peak_envelope"]["metrics"]["bias"] == .125
    view = store.view(report["id"], x_index=2, y_index=3, time_index=1)
    np.testing.assert_array_equal(view["traces"]["difference"]["rf"], -2*rf[3, 2])
    np.testing.assert_array_equal(view["traces"]["reference"]["envelope"], env[3, 2])
    assert np.any(np.asarray(view["traces"]["reference"]["rf"]) < 0)
    np.testing.assert_array_equal(view["instantaneous"]["rf"]["difference"], -2*rf[:, :, 1])
    maximum = max(abs(-2*rf[3, 2]))
    assert view["display_scales"]["traces"]["rf"]["difference"] == [-maximum, maximum]


@pytest.mark.parametrize("candidate", [0., 1.])
def test_zero_reference_norm_has_explicit_null_even_when_both_are_zero(tmp_path, candidate):
    values = np.zeros((16, 16, 5), dtype=np.float32)
    a, b = save(tmp_path, values, values), save(tmp_path, values+candidate, values+candidate)
    report = compute_comparison(tmp_path, config(a, b, gate_start_us=.5, gate_end_us=1))
    for metrics in [*report["metrics"].values(), *(v["metrics"] for v in report["gate_maps"].values())]:
        assert metrics["relative_l2"] is None
        assert "zero" in metrics["relative_l2_reason"]
        assert metrics["rmse"] == candidate


def test_gate_selection_matches_existing_saved_gate_tolerance(pair):
    from virtual_microscopy.volume_processing import gate_image
    root, a, b, _, _, _, _ = pair
    source = DatasetStore(root)
    group = source.open_arrays(a)
    start, stop = .75+5e-10, 1.25-5e-10
    result = compute_comparison(root, config(a, b, gate_start_us=start, gate_end_us=stop))
    assert result["gate"]["requested_start_us"] == start
    assert result["gate"]["start_us"] == .75 and result["gate"]["end_us"] == 1.25
    for mode in ("rms_rf", "peak_envelope"):
        expected, count = gate_image(source.path(a), group, group["time_us"][:], start, stop, mode)
        np.testing.assert_allclose(result["gate_maps"][mode]["reference"], expected, rtol=1e-7)
        assert result["gate"]["sample_count"] == count


@pytest.mark.parametrize("start,end,match", [(.1, 1., "bounds"), (.5, 2., "bounds"), (.51, .52, "no recorded")])
def test_invalid_gate_does_not_publish_report(pair, start, end, match):
    root, a, b, *_ = pair
    with pytest.raises(ValueError, match=match):
        ComparisonStore(root).create(config(a, b, gate_start_us=start, gate_end_us=end))
    assert not (root/"comparisons").exists()


@pytest.mark.parametrize("axis", ["x_mm", "y_mm", "time_us"])
def test_one_ulp_coordinate_difference_is_structured_incompatibility(tmp_path, axis):
    rf, env = signals()
    a = save(tmp_path, rf, env)
    group = DatasetStore(tmp_path).open_arrays(a)
    coords = {key: group[key][:] for key in ("x_mm", "y_mm", "time_us")}
    coords[axis][3] = np.nextafter(coords[axis][3], np.inf)
    b = save(tmp_path, rf, env, coordinates=coords)
    with pytest.raises(ComparisonCompatibilityError) as exc:
        compute_comparison(tmp_path, config(a, b))
    issue, = exc.value.issues
    assert issue["field"] == axis and issue["first_different_index"] == 3
    assert issue["max_absolute_difference"] == coords[axis][3]-group[axis][3]


def test_shifted_time_reference_rejects_even_with_identical_time_samples(tmp_path):
    rf, env = signals()
    a, b = save(tmp_path, rf, env), save(tmp_path, rf, env, time_zero="specimen top")
    with pytest.raises(ComparisonCompatibilityError) as exc:
        compute_comparison(tmp_path, config(a, b))
    assert exc.value.issues[0]["field"] == "time_zero"


def test_shape_mismatch_is_reported_without_trimming_or_resampling(tmp_path):
    rf, env = signals()
    a = save(tmp_path, rf, env)
    larger_rf, larger_env = signals((17, 20, 9))
    b = save(tmp_path, larger_rf, larger_env)
    with pytest.raises(ComparisonCompatibilityError) as exc:
        compute_comparison(tmp_path, config(a, b))
    assert {issue["field"] for issue in exc.value.issues} == {"shape", "y_mm"}


def mutate_manifest(root, identifier, callback):
    path = root/identifier/"manifest.json"
    manifest = json.loads(path.read_bytes())
    callback(manifest)
    path.write_text(json.dumps(manifest), encoding="utf-8")


@pytest.mark.parametrize("mode", ["units", "metadata_units", "missing_time", "kind", "incomplete", "array_axes"])
def test_invalid_or_incompatible_source_metadata_is_not_silently_inferred(pair, mode):
    root, a, b, *_ = pair
    def change(m):
        if mode == "units": m["arrays"]["rf"]["units"] = "calibrated Pa"
        if mode == "metadata_units": m["metadata"]["rf_unit"] = "Pa"
        if mode == "missing_time": m["metadata"].pop("time_zero")
        if mode == "kind": m["kind"] = "xray_projection_volume"
        if mode == "incomplete": m.update(complete=False, state="cancelled")
        if mode == "array_axes": m["arrays"]["rf"]["axes"] = ["x", "y", "time"]
    mutate_manifest(root, b, change)
    with pytest.raises(ValueError):
        ComparisonStore(root).create(config(a, b))
    assert not (root/"comparisons").exists()


@pytest.mark.parametrize("product", ["rf", "envelope", "x_mm"])
def test_corrupt_source_bytes_fail_before_report_publication(pair, product):
    root, a, b, *_ = pair
    group = zarr.open_group(str(root/b/"data.zarr"), mode="r+")
    if product == "x_mm": group[product][0] += .001
    else: group[product][0, 0, 0] += .25
    with pytest.raises(ValueError, match="checksum"):
        ComparisonStore(root).create(config(a, b))
    assert not (root/"comparisons").exists()


@pytest.mark.parametrize("product,value", [("rf", np.nan), ("rf", np.inf), ("envelope", -1.)])
def test_nonfinite_and_negative_envelope_fail_even_with_matching_stored_checksums(pair, product, value):
    root, a, b, *_ = pair
    group = zarr.open_group(str(root/b/"data.zarr"), mode="r+")
    group[product][0, 0, 0] = value
    checksum = array_sha256(group[product][:4])
    mutate_manifest(root, b, lambda m: m["completed_chunks"]["0"].update({f"{product}_sha256": checksum}))
    with pytest.raises(ValueError, match="nonfinite|nonnegative"):
        ComparisonStore(root).create(config(a, b))


def test_old_completed_source_never_calls_current_constructors_or_solver_and_preserves_bytes(pair, monkeypatch):
    import virtual_microscopy.schemas as schema
    import virtual_microscopy.sam_volume as solver
    root, a, b, *_ = pair
    # The fixture acquisition rate intentionally violates current forward-solver
    # validation. An old completed acquisition remains a valid read-only input.
    mutate_manifest(root, a, lambda m: m.pop("kind"))
    before = {identifier: file_hashes(root/identifier) for identifier in (a, b)}
    def forbidden(*args, **kwargs):
        pytest.fail("A comparison cannot construct or reacquire a historical source.")
    monkeypatch.setattr(schema.Twin, "model_validate", forbidden)
    monkeypatch.setattr(solver, "prepare_sam", forbidden)
    monkeypatch.setattr(solver, "estimate_sam", forbidden)
    monkeypatch.setattr(DatasetStore, "validate_identity", forbidden)
    store = ComparisonStore(root)
    report = store.create(config(a, b))
    store.view(report["id"], x_index=2, y_index=3, time_index=4)
    assert {identifier: file_hashes(root/identifier) for identifier in (a, b)} == before


def test_bounded_reads_never_materialize_a_complete_signal_volume(pair, monkeypatch):
    root, a, b, *_ = pair
    original, reads = zarr.Array.__getitem__, []
    def tracked(self, selection):
        if self.ndim == 3:
            first = selection[0] if isinstance(selection, tuple) else selection
            assert isinstance(first, (int, slice))
            if isinstance(first, slice):
                assert first.start is not None and first.stop is not None
                assert first.stop-first.start <= 8
            reads.append(selection)
        return original(self, selection)
    monkeypatch.setattr(zarr.Array, "__getitem__", tracked)
    store = ComparisonStore(root)
    report = store.create(config(a, b))
    store.view(report["id"])
    assert len(reads) > 10
    assert report["resources"]["estimated_peak_bytes"] <= 512*1024**2


def test_oversized_source_chunk_rejects_before_any_signal_read(pair, monkeypatch):
    import virtual_microscopy.comparisons as engine
    root, a, b, *_ = pair
    original = zarr.Array.__getitem__
    def guarded(self, selection):
        if self.ndim == 3:
            pytest.fail("Over-budget chunk must fail before decompression.")
        return original(self, selection)
    monkeypatch.setattr(zarr.Array, "__getitem__", guarded)
    monkeypatch.setattr(engine, "MAX_CHUNK_BYTES", 1)
    with pytest.raises(ValueError, match="bounded-read"):
        compute_comparison(root, config(a, b))


def test_small_json_with_pathological_container_expansion_is_rejected_before_parse(pair, monkeypatch):
    import virtual_microscopy.comparisons as engine
    root, a, b, *_ = pair
    mutate_manifest(root, a, lambda m: m["metadata"].update(historical_extra=[{} for _ in range(400_000)]))
    path = root/a/"manifest.json"
    # The reported regression was under2MiB on disk but over100MiB in Python.
    compact = json.dumps(json.loads(path.read_bytes()), separators=(",", ":"))
    path.write_text(compact)
    assert path.stat().st_size < 2*1024**2
    original = engine.json.loads
    def guard(payload, *args, **kwargs):
        if isinstance(payload, (bytes, str)) and len(payload) > 1_000_000:
            pytest.fail("Expanded-provenance guard must run before parsing the pathological JSON.")
        return original(payload, *args, **kwargs)
    monkeypatch.setattr(engine.json, "loads", guard)
    with pytest.raises(ValueError, match="expanded-provenance"):
        compute_comparison(root, config(a, b))
    assert not (root/"comparisons").exists()


def test_saved_report_expansion_is_bounded_before_json_parse(pair, monkeypatch):
    import virtual_microscopy.comparisons as engine
    root, a, b, *_ = pair
    report = ComparisonStore(root).create(config(a, b))
    monkeypatch.setattr(engine, "MAX_REPORT_EXPANDED_BYTES", 1)
    with pytest.raises(ValueError, match="expanded-provenance"):
        ComparisonStore(root).read(report["id"])


def test_workspace_and_provenance_budget_fail_without_publishing(pair, monkeypatch):
    import virtual_microscopy.comparisons as engine
    root, a, b, *_ = pair
    monkeypatch.setattr(engine, "MAX_WORKSPACE_BYTES", 1)
    with pytest.raises(ValueError, match="workspace"):
        ComparisonStore(root).create(config(a, b))
    assert not (root/"comparisons").exists()
    monkeypatch.setattr(engine, "MAX_SOURCE_MANIFEST_BYTES", 1)
    with pytest.raises(ValueError, match="provenance"):
        compute_comparison(root, config(a, b))


def test_source_independent_report_list_export_and_immutable_gate_revision(pair):
    root, a, b, *_ = pair
    store = ComparisonStore(root)
    report = store.create(config(a, b))
    path = root/"comparisons"/f'{report["id"]}.json'
    before = path.read_bytes()
    second = store.create(config(a, b, gate_start_us=.5, gate_end_us=.75))
    assert second["id"] != report["id"] and path.read_bytes() == before
    assert second["metrics"] == report["metrics"] and second["gate_maps"] != report["gate_maps"]
    (root/a).rename(root/f"unavailable-{a}")
    (root/b).rename(root/f"unavailable-{b}")
    assert store.read(report["id"]) == report
    assert len(store.list()) == 2
    rows = list(csv.reader(io.StringIO(comparison_csv(report))))
    frozen = json.loads(next(row[2] for row in rows if row[:2] == ["report", "provenance"]))
    assert frozen["reference_manifest"] == report["provenance"]["reference_manifest"]
    with pytest.raises(KeyError): store.view(report["id"])


def test_report_tampering_and_source_manifest_replacement_are_detected(pair):
    root, a, b, *_ = pair
    store = ComparisonStore(root)
    report = store.create(config(a, b))
    mutate_manifest(root, a, lambda m: m["metadata"].update(note="changed after comparison"))
    with pytest.raises(ValueError, match="frozen manifest"):
        store.view(report["id"])
    assert store.read(report["id"]) == report
    path = root/"comparisons"/f'{report["id"]}.json'
    damaged = deepcopy(report)
    damaged["metrics"]["rf"]["rmse"] = 999
    path.write_text(json.dumps(damaged))
    with pytest.raises(ValueError, match="checksum"):
        store.read(report["id"])


def test_signal_mutation_between_preflight_and_reduction_fails_without_report(pair, monkeypatch):
    import virtual_microscopy.comparisons as engine
    root, a, b, *_ = pair
    original = engine._reduce
    def mutate_then_reduce(left, right, gate, plan):
        group = zarr.open_group(str(root/b/"data.zarr"), mode="r+")
        group["rf"][0, 0, 0] += 1
        return original(left, right, gate, plan)
    monkeypatch.setattr(engine, "_reduce", mutate_then_reduce)
    with pytest.raises(ValueError, match="checksum changed"):
        ComparisonStore(root).create(config(a, b))
    assert not (root/"comparisons").exists()


@pytest.mark.parametrize("collision", ["report", "temporary"])
def test_atomic_publication_refuses_collisions_and_preserves_existing_bytes(pair, monkeypatch, collision):
    import virtual_microscopy.comparisons as engine
    root, a, b, *_ = pair
    identifier = str(uuid4())
    monkeypatch.setattr(engine, "uuid4", lambda: identifier)
    directory = root/"comparisons"
    directory.mkdir()
    target = directory/(f"{identifier}.json" if collision == "report" else f".{identifier}.tmp")
    target.write_bytes(b"existing data must survive")
    with pytest.raises(FileExistsError):
        ComparisonStore(root).create(config(a, b))
    assert target.read_bytes() == b"existing data must survive"


@pytest.mark.parametrize("field,value", [("reference_dataset_id", "../outside"), ("candidate_dataset_id", "123"),
    ("gate_start_us", float("nan")), ("gate_end_us", .5), ("x_index", True), ("y_index", 256), ("unknown", 1)])
def test_strict_request_validation(field, value):
    values = {"reference_dataset_id": str(uuid4()), "candidate_dataset_id": str(uuid4()), "gate_start_us": .5, "gate_end_us": 1.}
    values[field] = value
    with pytest.raises(ValidationError): SamComparisonRequest.model_validate(values)


def client(root):
    from virtual_microscopy.comparison_api import router
    app = FastAPI()
    app.state.volume_jobs = SimpleNamespace(root=root)
    app.include_router(router)
    return TestClient(app)


def test_api_create_list_reopen_views_exports_and_source_independence(pair):
    root, a, b, rf, *_ = pair
    http = client(root)
    response = http.post("/api/v2/comparisons", json=config(a, b, x_index=2, y_index=3).model_dump())
    assert response.status_code == 201, response.text
    report = response.json()
    url = "/api/v2/comparisons/"+report["id"]
    assert http.get(url).json() == report
    assert http.get("/api/v2/comparisons").json()["comparisons"][0]["id"] == report["id"]
    view = http.get(url+"/view?x_index=2&y_index=3&time_index=4")
    assert view.status_code == 200, view.text
    np.testing.assert_array_equal(view.json()["traces"]["reference"]["rf"], rf[3, 2])
    assert http.get(url+"/view?time_index=999").status_code == 422
    for extension in ("json", "csv"):
        export = http.get(url+"/export?format="+extension)
        assert export.status_code == 200 and extension in export.headers["content-disposition"]
    assert http.get(url+"/export?format=zip").status_code == 422
    assert http.get("/api/v2/comparisons/"+str(uuid4())).status_code == 404
    (root/a).rename(root/f"removed-{a}")
    assert http.get(url).status_code == http.get(url+"/export").status_code == 200
    assert http.get(url+"/view").status_code == 404


def test_queued_source_without_arrays_returns_incomplete_error_not_disk_error(pair):
    root, a, _, *_ = pair
    store, pending = DatasetStore(root), str(uuid4())
    store.create(pending, {"twin": {"name": "Queued"}, "acquisition": {}},
        {"shape": [16, 16, 9], "tile_rows": 4, "total_bytes": 18432, "model_version": "sam-volume-0.7.0"})
    assert not (root/pending/"data.zarr").exists()
    response = client(root).post("/api/v2/comparisons", json=config(a, pending).model_dump())
    assert response.status_code == 422, response.text
    assert "completed" in response.json()["detail"]


def test_api_compatibility_reports_maximum_axis_difference(pair):
    root, a, b, *_ = pair
    group = zarr.open_group(str(root/b/"data.zarr"), mode="r+")
    x = group["x_mm"][:]
    x[2] = np.nextafter(x[2], np.inf)
    group["x_mm"][:] = x
    mutate_manifest(root, b, lambda m: m["coordinates_sha256"].update(x_mm=coordinate_sha256(x)))
    response = client(root).post("/api/v2/comparisons", json=config(a, b).model_dump())
    assert response.status_code == 422, response.text
    issue, = response.json()["detail"]["issues"]
    assert issue["field"] == "x_mm" and issue["max_absolute_difference"] > 0


def test_comparison_api_shares_existing_processing_lock(monkeypatch, pair):
    import virtual_microscopy.comparison_api as api
    import virtual_microscopy.volume_api as volume_api
    assert api._processing_lock is volume_api._processing_lock
    entered = []
    class Lock:
        def __enter__(self): entered.append(True)
        def __exit__(self, *args): pass
    monkeypatch.setattr(api, "_processing_lock", Lock())
    root, a, b, *_ = pair
    http = client(root)
    report = http.post("/api/v2/comparisons", json=config(a, b).model_dump()).json()
    http.get("/api/v2/comparisons")
    http.get("/api/v2/comparisons/"+report["id"])
    http.get("/api/v2/comparisons/"+report["id"]+"/view")
    http.get("/api/v2/comparisons/"+report["id"]+"/export")
    assert len(entered) == 5
