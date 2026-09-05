"""Saved observation controls, exact support compatibility and immutable API."""
from copy import deepcopy
from fractions import Fraction
import csv
import io
import json
import shutil
from uuid import uuid4

from fastapi.testclient import TestClient
import numpy as np
import pytest

from test_causal_comparisons import source, hashes, oracle
from virtual_microscopy.causal_datasets import CausalSamDatasetStore
from virtual_microscopy.observation_datasets import ObservationStore
from virtual_microscopy.observation_plan import plan_observation
from virtual_microscopy.observation_schemas import ObservationRequest
from virtual_microscopy.observation_math import observe_row
from virtual_microscopy.observation_comparisons import (compute_observation_comparison, _source,
    _compatible, _read_row, _plan, ObservationComparisonCompatibilityError, BOUND_KEYS)
from virtual_microscopy.observation_comparison_store import ObservationComparisonStore


def observation(root, parent):
    body = ObservationRequest(source_dataset_id=parent).model_dump(mode="json")
    plan = plan_observation(root, body)
    store, identifier = ObservationStore(root), str(uuid4())
    store.create(identifier, body, plan)
    store.initialize_arrays(identifier)
    group = CausalSamDatasetStore(root).open_arrays(parent)
    # These analytic slabs are invariant in X/Y. Assert rather than assume this
    # before reusing one exact observation window for every retained row.
    values = {key:np.asarray(group[key][:]) for key in ("rf","imaginary","error_bound")}
    for v in values.values():
        assert all(np.array_equal(v[y], v[0]) for y in range(v.shape[0]))
    window = {"rf":values["rf"][:3], "imaginary":values["imaginary"][:3], "bounds":values["error_bound"][:3]}
    result = observe_row(window["rf"], window["imaginary"], window["bounds"], absolute_tolerance=1e-7)
    for y in range(plan["total_rows"]):
        store.write_row(identifier, y, result, window)
    store.complete(identifier)
    return identifier


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    root = tmp_path_factory.mktemp("observation-comparison-sources")
    parents = [source(root), source(root,.12), source(root,.1,40)]
    return root, [observation(root, p) for p in parents], parents


def request(a, b, **kwargs):
    return {"reference_dataset_id":a, "candidate_dataset_id":b, "gate_start_us":.04,
        "gate_end_us":.2, "x_index":3, "y_index":7, "time_index":23, **kwargs}


def test_analytic_slab_control_six_bounds_preservation_and_no_forward(sources, monkeypatch):
    root, (a,b,_), (pa,pb,_) = sources
    av,bv = oracle(root,pa), oracle(root,pb)
    before = hashes(root)
    import virtual_microscopy.observation_math as observation_math
    monkeypatch.setattr(observation_math, "observe_row", lambda *a,**k:pytest.fail("Comparison must not refilter"))
    r = compute_observation_comparison(root, request(a,b))
    d = r["initial_view"]["traces"]["difference"]
    bounds = r["initial_view"]["selected_bounds"]
    assert set(bounds) == set(BOUND_KEYS)
    er = np.asarray(d["rf"])-(np.asarray(bv["rf"])-np.asarray(av["rf"]))
    ei = np.asarray(d["imaginary"])-(np.asarray(bv["imaginary"])-np.asarray(av["imaginary"]))
    assert np.hypot(er,ei).max() <= bounds["complex_total"]
    assert np.abs(np.asarray(d["envelope"])-(np.asarray(bv["envelope"])-np.asarray(av["envelope"]))).max() <= bounds["magnitude_total"]
    assert Fraction(bounds["complex_total"]) >= Fraction(bounds["complex_source_sum"])+Fraction(bounds["complex_arithmetic"])
    assert Fraction(bounds["magnitude_total"]) >= Fraction(bounds["magnitude_source_sum"])+Fraction(bounds["magnitude_arithmetic"])
    assert bounds["magnitude_source_sum"] >= bounds["complex_source_sum"]
    assert r["metrics"]["full_record"]["rf"]["rmse"] > 0
    assert r["gate"]["sample_count"] == 65
    assert r["initial_view"]["cursor"]["source_x_index"] == 4
    assert r["initial_view"]["cursor"]["source_y_index"] == 8
    assert len(r["source_snapshots"]) == 2
    assert r["compatibility"]["differences"]["resolved_numeric_columns_changed"] == 256
    assert hashes(root) == before


def test_same_source_zero_and_single_snapshot(sources):
    root, (a,_,_), _ = sources
    r = compute_observation_comparison(root, request(a,a))
    assert len(r["source_snapshots"]) == 1
    assert r["source_reference"] == r["source_candidate"]
    for key in ("rf","imaginary","envelope","complex"):
        assert r["metrics"]["full_record"][key]["rmse"] == 0
    for key in ("rf","imaginary","envelope"):
        assert not np.asarray(r["initial_view"]["traces"]["difference"][key]).any()
    assert r["initial_view"]["selected_bounds"]["complex_total"] > 0


@pytest.mark.parametrize("key", ["x_mm","y_mm","time_us"])
def test_one_ulp_coordinates_rejected(sources, key):
    root, (a,b,_), _ = sources
    left,right = _source(root,a),_source(root,b)
    right.coordinates[key][2] = np.nextafter(right.coordinates[key][2],np.inf)
    with pytest.raises(ObservationComparisonCompatibilityError): _compatible(left,right)


@pytest.mark.parametrize("key", ["x_mm","y_mm"])
def test_matching_interior_but_changed_full_support_rejected(sources, key):
    root, (a,b,_), _ = sources
    left,right = _source(root,a),_source(root,b)
    parent = right.manifest["estimate"]["source_manifest"]
    parent["estimate"][key][0] = float(np.nextafter(parent["estimate"][key][0],np.inf))
    with pytest.raises(ObservationComparisonCompatibilityError) as exc: _compatible(left,right)
    assert any("parent."+key == i["field"] for i in exc.value.issues)


@pytest.mark.parametrize("key,value", [("additional_phase_radians",.1), ("weight_denominator",32),
    ("weights_float64_le_hex","bad"), ("certificate_version","future"), ("operator","gaussian"),
    ("arithmetic_contract","future")])
def test_unsupported_operator_rejected(sources, key, value):
    root,(a,b,_),_ = sources
    left,right = _source(root,a),_source(root,b)
    right.manifest["estimate"]["operator"][key] = value
    with pytest.raises(ObservationComparisonCompatibilityError): _compatible(left,right)


@pytest.mark.parametrize("field", ["source_indices","extent_mm","source_extent_mm","physical_neighbor_offsets"])
def test_retained_support_metadata_rejected(sources, field):
    root,(a,b,_),_ = sources
    left,right = _source(root,a),_source(root,b)
    p = right.manifest["estimate"]
    if field == "source_indices": p[field]["x"][0] = 2
    elif field == "physical_neighbor_offsets": p[field]["x_mm"][0][0] *= 2
    else: p[field][0] += .01
    with pytest.raises(ObservationComparisonCompatibilityError): _compatible(left,right)


@pytest.mark.parametrize("field,value", [("time_zero","shifted"),("envelope_processing","hilbert"),
    ("rf_unit","Pa"),("focus_model","focused"),("material_model","lossy")])
def test_nested_semantics_rejected(sources, field, value):
    root,(a,b,_),_ = sources
    left,right = _source(root,a),_source(root,b)
    right.manifest["estimate"]["source_manifest"]["metadata"][field] = value
    with pytest.raises(ObservationComparisonCompatibilityError): _compatible(left,right)


def test_pulse_change_rejected_numerical_tolerance_and_implementation_allowed(sources):
    root,(a,b,bad),_ = sources
    with pytest.raises(ObservationComparisonCompatibilityError): compute_observation_comparison(root,request(a,bad))
    left,right = _source(root,a),_source(root,b)
    right.manifest["request"]["absolute_tolerance"] = 1e-6
    right.manifest["solver"]["source_sha256"]["observation_math.py"] = "0"*64
    right.manifest["estimate"]["source_manifest"]["request"]["acquisition"].update(precision_bits=192,absolute_tolerance=1e-9)
    _compatible(left,right)


@pytest.mark.parametrize("overrides", [{"gate_start_us":-.1},{"gate_end_us":.4},
    {"gate_start_us":.1001,"gate_end_us":.1002},{"x_index":14},{"time_index":121},
    {"policy":"same_excitation_v1"},{"x_index":True}])
def test_invalid_requests_preserve_sources(sources, overrides):
    root,(a,b,_),_ = sources
    before = hashes(root)
    with pytest.raises(ValueError): compute_observation_comparison(root,request(a,b,**overrides))
    assert hashes(root) == before


def test_workspace_admitted_before_reader(sources, monkeypatch):
    root,(a,_,_),_ = sources
    monkeypatch.setattr(ObservationStore,"verify_complete",lambda *_:pytest.fail("Read before admission"))
    with pytest.raises(ValueError,match="workspace"): _source(root,a,512*1024**2)


def test_row_corruption_on_second_read(sources, tmp_path):
    root,(a,_,_),_ = sources
    shutil.copytree(root/a,tmp_path/a)
    s = _source(tmp_path,a)
    path = s.store._chunk(a,s.manifest,"rf",0)
    data = bytearray(path.read_bytes()); data[16] ^= 1; path.write_bytes(data)
    with pytest.raises(ValueError,match="checksum"): _read_row(s,0)


def test_api_offline_history_exports_and_no_jobs(sources,tmp_path,monkeypatch):
    root,(a,b,bad),parents = sources
    for identifier in (a,b,bad): shutil.copytree(root/identifier,tmp_path/identifier)
    # Original v0.13 parent arrays are deliberately absent even at creation.
    monkeypatch.setenv("VM_DATA_ROOT",str(tmp_path))
    from virtual_microscopy.server import app
    before = {i:hashes(tmp_path/i) for i in (a,b,bad)}
    with TestClient(app) as client:
        jobs = client.get("/api/v2/jobs").json()
        observations = client.get("/api/v2/observations/jobs").json()
        response = client.post("/api/v2/observation-comparisons",json=request(a,b,name="Analytic filtered pair"))
        assert response.status_code == 201,response.text
        r = response.json(); identifier = r["id"]; prefix = f"/api/v2/observation-comparisons/{identifier}"
        raw = (tmp_path/"observation-comparisons"/f"{identifier}.json").read_bytes()
        assert client.get(prefix+"/export").content == raw
        csv_response = client.get(prefix+"/export?format=csv")
        original_limit = csv.field_size_limit(64*1024**2)
        try:
            reconstructed = {row["field"]:json.loads(row["value_json"]) for row in csv.DictReader(io.StringIO(csv_response.text))}
        finally:
            csv.field_size_limit(original_limit)
        assert reconstructed == r
        assert client.get(prefix+"/view").json() == r["initial_view"]
        v = client.get(prefix+"/view?x_index=8&y_index=4&time_index=55")
        assert v.status_code == 200,v.text
        assert v.json()["cursor"]["time_us"] == .1375
        rejection = client.post("/api/v2/observation-comparisons",json=request(a,bad))
        assert rejection.status_code == 422 and rejection.json()["detail"]["issues"]
        assert client.get("/api/v2/observation-comparisons?limit=1").json()["comparisons"][0]["id"] == identifier
        assert client.get("/api/v2/jobs").json() == jobs
        assert client.get("/api/v2/observations/jobs").json() == observations
        assert before == {i:hashes(tmp_path/i) for i in before}
        (tmp_path/a/"manifest.json").unlink()
        assert client.get(prefix).json() == r
        assert client.get(prefix+"/export").content == raw
        assert client.get(prefix+"/view").json() == r["initial_view"]
        assert client.get(prefix+"/view?x_index=0").status_code == 404
        assert (tmp_path/"observation-comparisons"/f"{identifier}.json").read_bytes() == raw


def test_primary_causal_source_cannot_enter_observation_comparison(sources):
    root,(a,_,_),parents = sources
    with pytest.raises(ValueError): compute_observation_comparison(root,request(a,parents[0]))
