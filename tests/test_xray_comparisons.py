"""Independent masked/normalized projection metrics and immutable-read checks."""
from copy import deepcopy
import csv
import hashlib
import io
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import numpy as np
import pytest
from pydantic import ValidationError
import zarr

from virtual_microscopy.datasets import array_sha256, coordinate_sha256, json_sha256
from virtual_microscopy.xray_comparison_schemas import XrayComparisonRequest
from virtual_microscopy.xray_comparisons import (SEMANTICS, XrayComparisonCompatibilityError,
    XrayComparisonStore, compute_xray_comparison, xray_comparison_csv)
from virtual_microscopy.xray_datasets import XrayDatasetStore


def products(counts, photons):
    counts = np.asarray(counts, dtype=np.float32)
    values = counts.astype(np.float64)
    return {"counts": counts, "transmission": (values/photons).astype(np.float32),
            "line_integrals": (-np.log(np.where(values > 0, values, .5)/photons)).astype(np.float32),
            "valid_mask": (counts > 0).astype(np.float32)}


def poses(views=4, rows=16, cols=20):
    angles = np.arange(views, dtype=np.float64)*(360/views)
    rad = np.deg2rad(angles)
    return {"angles_deg": angles, "u_mm": (np.arange(cols)+.5-cols/2)*.125,
        "v_mm": (np.arange(rows)+.5-rows/2)*.25,
        "ray_direction_xyz": np.column_stack((np.sin(rad), np.zeros(views), np.cos(rad))),
        "detector_center_mm": np.tile([2., 3., .8], (views, 1)),
        "detector_u_xyz": np.column_stack((np.cos(rad), np.zeros(views), -np.sin(rad))),
        "detector_v_xyz": np.tile([0., 1., 0.], (views, 1))}


def save(root, counts, *, photons=10000, noise=False, coordinates=None, acquisition=None):
    counts = np.asarray(counts, dtype=np.float32)
    identifier, store = str(uuid4()), XrayDatasetStore(root)
    request = {"kind": "xray_projection_volume", "twin": {"name": "Independent stored X-ray fixture", "size_mm": [4, 6, 1.6],
        "historical_extra": "Intentionally not accepted by today's twin constructor."},
        "acquisition": {"photons": photons, "noise": noise, "seed": 42, "angle_span_deg": 360, "energy_kev": 80, **(acquisition or {})}}
    shape = list(counts.shape)
    store.create(identifier, request, {"kind": "xray_projection_volume", "shape": shape,
        "tile_rows": 1, "total_bytes": counts.size*16, "model_version": "xray-projection-volume-0.4.0"})
    coordinate_values = coordinates or poses(*counts.shape)
    metadata = {key: f"Frozen supported definition for {key}" for key in SEMANTICS}
    metadata.update(truncated_view_indices=[1])
    prepared = SimpleNamespace(**coordinate_values, metadata=metadata)
    store.initialize_arrays(identifier, prepared)
    arrays = products(counts, photons)
    for index in range(shape[0]):
        store.write_view(identifier, index, index+1, {key: value[index:index+1] for key, value in arrays.items()})
    store.complete(identifier)
    return identifier, arrays


def config(a, b, **kwargs):
    return XrayComparisonRequest(reference_dataset_id=a, candidate_dataset_id=b, **kwargs)


@pytest.fixture
def pair(tmp_path):
    view, row, col = np.indices((4, 16, 20))
    a = (1000+view*100+row*10+col*2).astype(np.float32)
    b = (a*1.25+40).astype(np.float32)
    a[(view+row+col)%7 == 0] = 0
    b[(view+row+col)%5 == 0] = 0
    aid, aa = save(tmp_path, a)
    bid, bb = save(tmp_path, b, acquisition={"energy_kev": 100})
    return tmp_path, aid, bid, aa, bb


def mutate(root, identifier, callback):
    path = root/identifier/"manifest.json"
    value = json.loads(path.read_bytes())
    callback(value)
    path.write_text(json.dumps(value), encoding="utf-8")


def rehash_product(root, identifier, name, index=0):
    group = zarr.open_group(str(root/identifier/"data.zarr"), mode="r")
    checksum = array_sha256(group[name][index:index+1])
    mutate(root, identifier, lambda m: m["completed_chunks"][str(index)].update({f"{name}_sha256": checksum}))


def oracle(a, b, support):
    left, right = a.astype(float)[support], b.astype(float)[support]
    diff = right-left
    return {"sample_count": len(diff), "bias": diff.mean(), "mae": abs(diff).mean(), "rmse": np.sqrt(np.mean(diff**2)),
            "relative_l2": np.linalg.norm(diff)/np.linalg.norm(left), "max_absolute_difference": abs(diff).max()}


@pytest.mark.parametrize("product", ["counts", "transmission", "line_integrals"])
def test_independent_masked_and_full_support_metrics_and_locator(pair, product):
    root, aid, bid, a, b = pair
    report = compute_xray_comparison(root, config(aid, bid, product=product, view_index=2, detector_row=3, detector_col=4))
    am, bm = a["valid_mask"].astype(bool), b["valid_mask"].astype(bool)
    support = am & bm if product == "line_integrals" else np.ones(am.shape, bool)
    expected = oracle(a[product], b[product], support)
    for key, value in expected.items():
        assert report["metrics"][key] == pytest.approx(value, rel=1e-12, abs=1e-12)
    difference = b[product].astype(float)-a[product].astype(float)
    masked = np.where(support, abs(difference), -1)
    vi, row, col = np.unravel_index(np.argmax(masked), masked.shape)
    location = report["metrics"]["max_location"]
    assert [location[key] for key in ("view_index", "detector_row", "detector_col")] == [vi, row, col]
    assert location["signed_difference"] == difference[vi, row, col]
    assert location["angle_deg"] == report["coordinates"]["angles_deg"][vi]
    assert report["support"]["common_valid"] == int((am & bm).sum())
    assert report["support"]["reference_only"] == int((am & ~bm).sum())
    assert report["support"]["candidate_only"] == int((~am & bm).sum())
    assert report["support"]["neither_valid"] == int((~am & ~bm).sum())
    assert report["support"]["total"] == am.size
    for index, view in enumerate(report["per_view"]):
        for key, value in oracle(a[product][index], b[product][index], support[index]).items():
            assert view["metrics"][key] == pytest.approx(value)
    assert report["truncation"] == {"reference": [1], "candidate": [1]}
    assert report["differences"]["settings"] == [{"path": "acquisition.energy_kev", "reference": 80, "candidate": 100}]


def test_zero_count_logs_are_null_but_valid_zero_counts_remain_numeric(pair):
    root, aid, bid, a, b = pair
    log = compute_xray_comparison(root, config(aid, bid, product="line_integrals", view_index=0, detector_row=0))
    view = log["initial_view"]
    assert view["projection"]["reference"][0][0] is None
    assert view["projection"]["candidate"][0][0] is None
    assert view["projection"]["difference"][0][0] is None
    assert view["profile"]["reference"][0] is None
    assert view["sinogram"]["difference"][0][0] is None
    common = a["valid_mask"][0].astype(bool) & b["valid_mask"][0].astype(bool)
    values = np.array(view["projection"]["difference"], dtype=float)
    assert np.isnan(values[~common]).all()
    np.testing.assert_array_equal(values[common], (b["line_integrals"][0].astype(float)-a["line_integrals"][0])[common])
    for product in ("counts", "transmission"):
        result = compute_xray_comparison(root, config(aid, bid, product=product))
        assert result["initial_view"]["projection"]["reference"][0][0] == 0
        assert result["initial_view"]["projection"]["difference"][0][0] == 0
        assert result["metrics"]["sample_count"] == a[product].size


def test_empty_common_log_support_and_zero_reference_have_distinct_null_reasons(tmp_path):
    a = np.zeros((2, 16, 16), dtype=np.float32)
    b = a.copy()
    a[:, :, ::2], b[:, :, 1::2] = 100, 200
    aid, _ = save(tmp_path, a)
    bid, _ = save(tmp_path, b)
    log = compute_xray_comparison(tmp_path, config(aid, bid, product="line_integrals"))
    assert log["support"]["common_valid"] == log["metrics"]["sample_count"] == 0
    for field in ("bias", "mae", "rmse", "max_absolute_difference", "max_location", "relative_l2", "reference_l2"):
        assert log["metrics"][field] is None
    assert "empty" in log["metrics"]["metric_reason"]
    assert log["initial_view"]["display_scales"]["projection"]["difference"] == [None, None]
    zero, _ = save(tmp_path, np.zeros_like(a))
    counts = compute_xray_comparison(tmp_path, config(zero, bid, product="counts"))
    assert counts["metrics"]["relative_l2"] is None and "zero" in counts["metrics"]["relative_l2_reason"]
    assert counts["metrics"]["metric_reason"] is None
    assert counts["metrics"]["rmse"] == pytest.approx(np.sqrt(np.mean(b.astype(float)**2)))


def test_fractional_expected_counts_and_negative_noisy_logs_are_not_clipped(tmp_path):
    values = np.full((1, 16, 16), .125, dtype=np.float32)
    a, _ = save(tmp_path, values)
    b, _ = save(tmp_path, values*.5)
    report = compute_xray_comparison(tmp_path, config(a, b, product="line_integrals"))
    assert report["metrics"]["bias"] == pytest.approx(np.log(2), rel=2e-6)
    assert report["support"]["common_valid"] == values.size
    c, _ = save(tmp_path, np.full_like(values, 11000), noise=True)
    d, _ = save(tmp_path, np.full_like(values, 12000), noise=True)
    report = compute_xray_comparison(tmp_path, config(c, d, product="line_integrals"))
    assert report["initial_view"]["projection"]["reference"][0][0] < 0
    assert report["metrics"]["bias"] < 0
    assert compute_xray_comparison(tmp_path, config(c, d))["initial_view"]["projection"]["reference"][0][0] > 1


@pytest.mark.parametrize("product", ["transmission", "line_integrals"])
def test_different_photon_flux_requires_explicit_per_source_normalization(tmp_path, product):
    values = np.full((2, 16, 16), 250, dtype=np.float32)
    a, _ = save(tmp_path, values, photons=1000)
    b, _ = save(tmp_path, values*2, photons=2000)
    with pytest.raises(XrayComparisonCompatibilityError) as exc:
        compute_xray_comparison(tmp_path, config(a, b, product=product))
    assert exc.value.issues[0]["field"] == "photons"
    report = compute_xray_comparison(tmp_path, config(a, b, product=product, normalization="per_source_incident"))
    assert report["metrics"]["max_absolute_difference"] == 0
    assert report["reference"]["photons"] == 1000 and report["candidate"]["photons"] == 2000
    assert report["normalization"] == "per_source_incident"


def test_mixed_observation_kind_requires_explicit_normalized_comparison(tmp_path):
    values = np.full((2, 16, 16), 300, dtype=np.float32)
    a, _ = save(tmp_path, values)
    b, _ = save(tmp_path, values, noise=True)
    with pytest.raises(XrayComparisonCompatibilityError, match="policy"):
        compute_xray_comparison(tmp_path, config(a, b))
    report = compute_xray_comparison(tmp_path, config(a, b, observation_policy="observed_vs_expected"))
    assert report["metrics"]["rmse"] == 0
    assert report["reference"]["observation_kind"] == "expected"
    assert report["candidate"]["observation_kind"] == "observed"
    assert any("Observed versus expected" in item for item in report["warnings"])


@pytest.mark.parametrize("coordinate", list(poses()))
def test_one_ulp_coordinate_or_pose_disagreement_is_rejected_exactly(tmp_path, coordinate):
    values = np.full((4, 16, 20), 1000, dtype=np.float32)
    a, _ = save(tmp_path, values)
    coordinates = poses()
    # Move a nonzero component so the value changes one ULP without violating
    # the source's independent unit-length/orthogonality acceptance tolerance.
    flat = int(np.flatnonzero(coordinates[coordinate] != 0)[0])
    coordinates[coordinate].flat[flat] = np.nextafter(coordinates[coordinate].flat[flat], np.inf)
    b, _ = save(tmp_path, values, coordinates=coordinates)
    with pytest.raises(XrayComparisonCompatibilityError) as exc:
        compute_xray_comparison(tmp_path, config(a, b))
    issue, = exc.value.issues
    assert issue["field"] == coordinate and issue["max_absolute_difference"] > 0


@pytest.mark.parametrize("kind", ["pose_meaning", "signal_units", "incomplete", "wrong_kind"])
def test_source_semantic_registry_and_completion_guards(pair, kind):
    root, aid, bid, *_ = pair
    def change(m):
        if kind == "pose_meaning": m["metadata"]["pose_vector_definition"] = "pixel-scaled axes"
        if kind == "signal_units": m["arrays"]["transmission"]["units"] = "calibrated flux"
        if kind == "incomplete": m.update(complete=False, state="cancelled")
        if kind == "wrong_kind": m["kind"] = "sam_rf_volume"
    mutate(root, bid, change)
    with pytest.raises(ValueError): XrayComparisonStore(root).create(config(aid, bid))
    assert not (root/"xray-comparisons").exists()


@pytest.mark.parametrize("product,value", [("counts", -1), ("counts", .125), ("transmission", .99),
    ("line_integrals", 0), ("valid_mask", .5), ("line_integrals", np.nan), ("transmission", np.inf)])
def test_relationship_and_nonfinite_corruption_fails_even_when_chunk_hash_matches(tmp_path, product, value):
    counts = np.full((2, 16, 16), 400, dtype=np.float32)
    a, _ = save(tmp_path, counts, noise=True)
    b, _ = save(tmp_path, counts, noise=True)
    group = zarr.open_group(str(tmp_path/b/"data.zarr"), mode="r+")
    group[product][0, 0, 0] = value
    rehash_product(tmp_path, b, product)
    with pytest.raises(ValueError): XrayComparisonStore(tmp_path).create(config(a, b))
    assert not (tmp_path/"xray-comparisons").exists()


def test_nonfinite_invalid_log_pixel_is_corruption_not_excluded_support(tmp_path):
    counts = np.zeros((1, 16, 16), dtype=np.float32)
    a, _ = save(tmp_path, counts)
    b, _ = save(tmp_path, counts)
    group = zarr.open_group(str(tmp_path/b/"data.zarr"), mode="r+")
    group["line_integrals"][0, 0, 0] = np.nan
    rehash_product(tmp_path, b, "line_integrals")
    with pytest.raises(ValueError, match="nonfinite"):
        compute_xray_comparison(tmp_path, config(a, b, product="line_integrals"))


def hashes(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob("*") if p.is_file()}


def test_historical_arrays_are_read_without_solver_and_preserved_byte_for_byte(pair, monkeypatch):
    import virtual_microscopy.schemas as schemas
    import virtual_microscopy.xray_volume as solver
    root, aid, bid, a, b = pair
    before = {identifier: hashes(root/identifier) for identifier in (aid, bid)}
    def forbidden(*args, **kwargs): pytest.fail("Comparison must not construct or reacquire a historical specimen.")
    monkeypatch.setattr(schemas.Twin, "model_validate", forbidden)
    monkeypatch.setattr(solver, "prepare_xray", forbidden)
    monkeypatch.setattr(solver, "estimate_xray", forbidden)
    monkeypatch.setattr(XrayDatasetStore, "validate_identity", forbidden)
    store = XrayComparisonStore(root)
    report = store.create(config(aid, bid))
    view = store.view(report["id"], view_index=3, detector_row=5, detector_col=6)
    np.testing.assert_array_equal(view["profile"]["reference"], a["transmission"][3, 5])
    np.testing.assert_array_equal(view["sinogram"]["candidate"], b["transmission"][:, 5])
    assert view["cursor"]["detector_col"] == 6
    assert {identifier: hashes(root/identifier) for identifier in (aid, bid)} == before


def test_every_signal_read_is_a_single_view_and_changed_chunks_fail(pair, monkeypatch):
    import virtual_microscopy.xray_comparisons as engine
    root, aid, bid, *_ = pair
    original, calls = zarr.Array.__getitem__, []
    def tracked(self, selection):
        if self.ndim == 3:
            first = selection[0] if isinstance(selection, tuple) else selection
            assert isinstance(first, slice) and first.stop-first.start == 1
            calls.append(selection)
        return original(self, selection)
    monkeypatch.setattr(zarr.Array, "__getitem__", tracked)
    compute_xray_comparison(root, config(aid, bid))
    assert len(calls) >= 4*4*2
    previous = engine._view_products
    def modified(*args, **kwargs):
        group = zarr.open_group(str(root/bid/"data.zarr"), mode="r+")
        group["counts"][0, 0, 1] = float(original(group["counts"], (0, 0, 1)))+10
        return previous(*args, **kwargs)
    monkeypatch.setattr(engine, "_view_products", modified)
    with pytest.raises(ValueError, match="checksum changed"):
        XrayComparisonStore(root).create(config(aid, bid))


def test_report_reopen_and_exports_work_without_sources_and_detect_report_tamper(pair):
    root, aid, bid, *_ = pair
    store = XrayComparisonStore(root)
    report = store.create(config(aid, bid, product="line_integrals"))
    (root/aid).rename(root/f"preserved-{aid}")
    (root/bid).rename(root/f"preserved-{bid}")
    assert XrayComparisonStore(root).read(report["id"]) == report
    assert store.list()[0]["id"] == report["id"]
    rows = list(csv.reader(io.StringIO(xray_comparison_csv(report))))
    assert json.loads(next(row[2] for row in rows if row[:2] == ["report", "provenance"])) == report["provenance"]
    assert report["initial_view"]["projection"]["difference"][0][0] is None
    with pytest.raises(KeyError): store.view(report["id"])
    path = root/"xray-comparisons"/f'{report["id"]}.json'
    value = deepcopy(report)
    value["metrics"]["rmse"] = 999
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="checksum"): store.read(report["id"])


def test_source_manifest_change_blocks_new_view_but_not_frozen_report(pair):
    root, aid, bid, *_ = pair
    store = XrayComparisonStore(root)
    report = store.create(config(aid, bid))
    mutate(root, aid, lambda m: m["metadata"].update(note="changed after report"))
    with pytest.raises(ValueError, match="frozen X-ray"): store.view(report["id"])
    assert store.read(report["id"]) == report


def test_frozen_initial_view_equals_recomputed_default_selection(pair):
    root, aid, bid, *_ = pair
    store = XrayComparisonStore(root)
    report = store.create(config(aid, bid, product="line_integrals", view_index=3, detector_row=2, detector_col=1))
    assert report["initial_view"] == store.view(report["id"])


@pytest.mark.parametrize("field,value", [("dataset_id", "unrelated"), ("kind", "sam_rf_volume"), ("axis_order", ["u", "v", "view"])])
def test_zarr_identity_and_axis_metadata_must_match_source_manifest(pair, field, value):
    root, aid, bid, *_ = pair
    group = zarr.open_group(str(root/bid/"data.zarr"), mode="r+")
    group.attrs[field] = value
    with pytest.raises(ValueError, match="identity, kind or axes"):
        compute_xray_comparison(root, config(aid, bid))


@pytest.mark.parametrize("collision", ["report", "temporary"])
def test_report_publication_refuses_overwrites_and_preserves_collision_bytes(pair, monkeypatch, collision):
    import virtual_microscopy.xray_comparisons as engine
    root, aid, bid, *_ = pair
    identifier = str(uuid4())
    monkeypatch.setattr(engine, "uuid4", lambda: identifier)
    directory = root/"xray-comparisons"
    directory.mkdir()
    path = directory/(f"{identifier}.json" if collision == "report" else f".{identifier}.tmp")
    path.write_bytes(b"preserve this collision")
    with pytest.raises(FileExistsError): XrayComparisonStore(root).create(config(aid, bid))
    assert path.read_bytes() == b"preserve this collision"


def test_json_expansion_and_source_workspace_reject_before_signal_allocations(pair, monkeypatch):
    import virtual_microscopy.xray_comparisons as engine
    root, aid, bid, *_ = pair
    monkeypatch.setattr(engine, "MAX_WORKSPACE_BYTES", 1)
    monkeypatch.setattr(engine, "_read_view", lambda *args: pytest.fail("Over-budget read"))
    with pytest.raises(ValueError, match="workspace"): compute_xray_comparison(root, config(aid, bid))
    monkeypatch.setattr(engine, "MAX_WORKSPACE_BYTES", 512*1024**2)
    mutate(root, aid, lambda m: m["metadata"].update(extra=[{} for _ in range(400_000)]))
    path = root/aid/"manifest.json"
    path.write_text(json.dumps(json.loads(path.read_bytes()), separators=(",", ":")))
    with pytest.raises(ValueError, match="expanded-provenance"): compute_xray_comparison(root, config(aid, bid))


def test_oversized_zarr_metadata_rejected_before_zarr_parser(pair, monkeypatch):
    root, aid, bid, *_ = pair
    path = root/aid/"data.zarr"/"zarr.json"
    value = json.loads(path.read_bytes())
    value["attributes"]["extra"] = "a"*300_000
    path.write_text(json.dumps(value))
    monkeypatch.setattr(zarr, "open_group", lambda *args, **kwargs: pytest.fail("Metadata budget must precede Zarr parser"))
    with pytest.raises(ValueError, match="Zarr metadata"): compute_xray_comparison(root, config(aid, bid))


@pytest.mark.parametrize("name", ["u_mm", "detector_center_mm"])
def test_oversized_coordinate_chunks_rejected_before_any_array_read(pair, monkeypatch, name):
    root, aid, bid, *_ = pair
    group = zarr.open_group(str(root/aid/"data.zarr"), mode="a")
    values = group[name][:]
    # The logical shape and values/checksum are unchanged. Only the decoded
    # chunk workspace is enlarged, so logical-size preflight cannot catch it.
    del group[name]
    chunks = (1_000_000,) if values.ndim == 1 else (1_000_000, 3)
    group.create_array(name, data=values, chunks=chunks)
    original = zarr.Array.__getitem__
    def guarded(array, key):
        if array.path == name:
            pytest.fail("Noncanonical coordinate chunk must be rejected before decoding")
        return original(array, key)
    monkeypatch.setattr(zarr.Array, "__getitem__", guarded)
    with pytest.raises(ValueError, match="canonical chunk layout"):
        compute_xray_comparison(root, config(aid, bid))


def test_independent_seed_poisson_residual_statistics_use_known_variance(tmp_path):
    # Two independent equal-mean Poisson fields: D=B-A has E[D]=0,
    # Var(D)=2λ and Var(D²)=8λ²+2λ. Six standard-error tolerances are explicit.
    lam, shape = 4000., (8, 32, 32)
    samples = []
    for seed in (1763, 9871):
        values = np.stack([np.random.default_rng(np.random.SeedSequence([seed, view])).poisson(lam, shape[1:])
                           for view in range(shape[0])]).astype(np.float32)
        samples.append(values)
    a, _ = save(tmp_path, samples[0], noise=True)
    b, _ = save(tmp_path, samples[1], noise=True)
    report = compute_xray_comparison(tmp_path, config(a, b, product="counts"))
    n = int(np.prod(shape))
    assert abs(report["metrics"]["bias"]) < 6*np.sqrt(2*lam/n)
    assert abs(report["metrics"]["rmse"]**2-2*lam) < 6*np.sqrt((8*lam**2+2*lam)/n)
    # Identical stored realization gives exact zero; this is not independence.
    repeated = compute_xray_comparison(tmp_path, config(a, a, product="counts"))
    assert repeated["metrics"]["rmse"] == 0


def test_independent_slab_transmission_relationship_and_offset_metrics(tmp_path):
    thickness = np.linspace(.1, 1, 16)[None, None, :]
    ideal = np.broadcast_to(np.exp(-.35*thickness), (2, 16, 16))
    a, aa = save(tmp_path, 10000*ideal)
    b, bb = save(tmp_path, 10000*ideal*.75)
    report = compute_xray_comparison(tmp_path, config(a, b, product="line_integrals"))
    np.testing.assert_allclose(aa["transmission"], ideal, rtol=1e-7)
    assert report["metrics"]["bias"] == pytest.approx(-np.log(.75), abs=2e-7)
    assert report["metrics"]["rmse"] == pytest.approx(-np.log(.75), abs=2e-7)


@pytest.mark.parametrize("change", [{"product": "rf"}, {"normalization": "auto"}, {"observation_policy": "ignore"},
    {"product": "counts", "normalization": "per_source_incident"}, {"product": "counts", "observation_policy": "observed_vs_expected"},
    {"reference_dataset_id": "../outside"}, {"detector_col": True}, {"view_index": 720}, {"unknown": 1}])
def test_strict_request_policy_and_indices(change):
    values = {"reference_dataset_id": str(uuid4()), "candidate_dataset_id": str(uuid4()), **change}
    with pytest.raises(ValidationError): XrayComparisonRequest.model_validate(values)


def client(root):
    from virtual_microscopy.xray_comparison_api import router
    app = FastAPI()
    app.state.volume_jobs = SimpleNamespace(root=root)
    app.include_router(router)
    return TestClient(app)


def test_api_report_views_exports_errors_and_source_independence(pair):
    root, aid, bid, *_ = pair
    http = client(root)
    response = http.post("/api/v2/xray-comparisons", json=config(aid, bid, product="line_integrals").model_dump())
    assert response.status_code == 201, response.text
    report = response.json()
    url = "/api/v2/xray-comparisons/"+report["id"]
    assert http.get(url).json() == report
    assert http.get("/api/v2/xray-comparisons").json()["comparisons"][0]["id"] == report["id"]
    view = http.get(url+"/view?view_index=2&detector_row=3&detector_col=4")
    assert view.status_code == 200, view.text
    assert view.json()["cursor"]["view_index"] == 2
    assert view.json()["cursor"]["detector_col"] == 4
    assert http.get(url+"/view?view_index=999").status_code == 422
    for format in ("json", "csv"):
        result = http.get(url+"/export?format="+format)
        assert result.status_code == 200 and format in result.headers["content-disposition"]
    assert http.get(url+"/export?format=zip").status_code == 422
    (root/aid).rename(root/f"removed-{aid}")
    assert http.get(url+"/view").status_code == 404
    assert http.get(url+"/export").status_code == 200


def test_queued_source_is_completion_error_before_missing_arrays(pair):
    root, aid, *_ = pair
    identifier = str(uuid4())
    store = XrayDatasetStore(root)
    store.create(identifier, {"kind": "xray_projection_volume", "twin": {}, "acquisition": {"noise": False}},
        {"kind": "xray_projection_volume", "shape": [1, 16, 16], "tile_rows": 1,
         "total_bytes": 4096, "model_version": "xray-projection-volume-0.4.0"})
    response = client(root).post("/api/v2/xray-comparisons", json=config(aid, identifier).model_dump())
    assert response.status_code == 422 and "completed" in response.json()["detail"]


def test_xray_comparison_api_reuses_shared_processing_lock():
    from virtual_microscopy.xray_comparison_api import _processing_lock
    from virtual_microscopy.volume_api import _processing_lock as original
    assert _processing_lock is original
