"""Manual-only SLS service admission, complete publication and source-free history."""
from copy import deepcopy
import builtins
import csv
from fractions import Fraction
import hashlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import numpy as np
import pytest

import virtual_microscopy.mixed_analysis as analysis
import virtual_microscopy.mixed_reports as storage
from virtual_microscopy.mixed_reports import MixedReportStore
from virtual_microscopy.mixed_schemas import MixedLayeredAnalysisRequest


def request(*, causal=True):
    medium = {"name": "Nominal water", "impedance_mrayl": 1.48, "sound_speed_m_s": 1480.}
    return {"kind":"mixed_layered_analysis", "name": "Assumed \"slab\" Î”", "stack": {"incident": medium, "terminal": medium,
        "layers": [{"kind":"sls","name": "A", "thickness_mm": .05, "density_kg_m3": 1000.,
            "relaxed_modulus_gpa": 2.25, "unrelaxed_modulus_gpa": 4., "relaxation_time_us": .003}]},
        "spectrum": {"start_mhz": 0., "end_mhz": 150., "samples": 17},
        "causal_pulse": {"record_duration_us": .5} if causal else None}


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*") if p.is_file()}


def client(root):
    from virtual_microscopy.mixed_api import router
    app = FastAPI(); app.state.volume_jobs = SimpleNamespace(root=root); app.include_router(router)
    return TestClient(app)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    body = MixedLayeredAnalysisRequest.model_validate(request())
    result = analysis.analyze_mixed(body)
    calls = []
    def calculate(*args): calls.append("calculate"); return deepcopy(result)
    monkeypatch.setattr(analysis, "analyze_mixed", calculate)
    monkeypatch.setattr(analysis, "estimate_mixed", lambda *args: deepcopy(result["resources"]))
    monkeypatch.setattr(storage, "_fingerprints", lambda: {name: "1"*64 for name in storage.IMPLEMENTATION_FILES})
    monkeypatch.setattr(storage, "_proof_identity", lambda: {"path": "docs/MIXED_MATERIAL_PROOF.md", "sha256": "2"*64})
    return MixedReportStore(tmp_path), body, result, calls


def rehash(report):
    report["report_sha256"] = storage.json_measure({k: v for k, v in report.items() if k != "report_sha256"})["sha256"]


def rewrite(store, report):
    store._path(report["id"]).write_bytes(storage.bounded_payload(report))


def test_real_roundtrip_freezes_exact_units_spectrum_and_reflected_certificate(tmp_path):
    body = request()
    report = MixedReportStore(tmp_path).create(body)
    normalized = MixedLayeredAnalysisRequest.model_validate(body).model_dump(mode="json")
    assert report["request"] == normalized and report["stack"] == normalized["stack"]
    assert report["stack"]["layers"][0]["relaxation_time_us"] == .003
    assert report["spectrum"]["materials"][0]["low_frequency_speed_m_s"] == 1500.
    assert report["spectrum"]["materials"][0]["high_frequency_speed_m_s"] == 2000.
    assert report["kind"] == "mixed_layered_analysis" and report["source_status"] == "manual_assumptions"
    assert "transmitted_rf" not in report and report["resources"]["time_samples"] == 201
    diagnostic = report["causal_pulse"]["diagnostics"]
    keys = ("analytic_alias_bound", "frequency_cutoff_bound", "arithmetic_complex_bound", "arithmetic_envelope_bound")
    assert sum((Fraction(diagnostic[key]) for key in keys), Fraction()) <= Fraction(diagnostic["total_error_bound"]) <= Fraction(1e-7)
    assert MixedReportStore(tmp_path).read(report["id"]) == report
    raw = storage.bounded_payload(report)
    csv_rows = csv.DictReader(io.StringIO(storage.mixed_report_csv(report)))
    decoded = {row["field"]: json.loads(row["value_json"]) for row in csv_rows}
    assert storage.bounded_payload(decoded) == raw
    assert len(raw) <= report["resources"]["estimated_report_bytes"]
    assert storage.json_measure(report)["expanded_bytes"] <= report["resources"]["estimated_report_expanded_bytes"]
    assert list(tmp_path.iterdir()) == [tmp_path/"mixed-reports"]


def test_frequency_only_zero_layer_and_zero_thickness_keep_authored_materials(tmp_path):
    body = request(causal=False)
    body["stack"]["layers"][0]["thickness_mm"] = 0.
    report = MixedReportStore(tmp_path).create(body)
    assert report["spectrum"]["materials"][0]["zero_thickness"] is True
    assert report["causal_pulse"] is None and report["resources"]["time_samples"] == 0
    assert report["spectrum"]["reflection"]["real"] == [0.]*17
    body["stack"]["layers"] = []
    empty = MixedReportStore(tmp_path).create(body)
    assert empty["spectrum"]["materials"] == []
    assert empty["spectrum"]["reflection"]["phase_deg"] == [None]*17


def test_actual_binary64_time_centers_reach_plan_and_response(monkeypatch):
    from virtual_microscopy import mixed_time
    body = request(); body["causal_pulse"].update(record_start_us=.10000000000000002, record_duration_us=.137, sample_rate_mhz=800.)
    normalized = MixedLayeredAnalysisRequest.model_validate(body).model_dump(mode="json")
    expected = [normalized["causal_pulse"]["record_start_us"]+i/800 for i in range(110)]
    calls = []
    original_estimate, original_response = mixed_time.estimate_causal_gamma, mixed_time.causal_gamma_response
    def preflight(stack, time, settings): calls.append(time); return original_estimate(stack, time, settings)
    def synthesize(stack, time, settings): calls.append(time); return original_response(stack, time, settings)
    monkeypatch.setattr(mixed_time, "estimate_causal_gamma", preflight); monkeypatch.setattr(mixed_time, "causal_gamma_response", synthesize)
    result = analysis.analyze_mixed(body)
    assert len(calls) == 2
    assert all(np.asarray(value).tobytes() == np.asarray(expected).tobytes() for value in calls)
    assert result["causal_pulse"]["time_us"] == expected


def test_large_combined_request_rejected_before_spectrum_or_rf_synthesis(monkeypatch):
    from virtual_microscopy import mixed_acoustics, mixed_time
    body = request(); body["stack"]["layers"] *= 8; body["spectrum"]["samples"] = 8193
    forbidden = lambda *a, **k: pytest.fail("Synthesis preceded combined admission")
    monkeypatch.setattr(mixed_acoustics, "reflection_spectrum", forbidden)
    monkeypatch.setattr(mixed_time, "causal_gamma_response", forbidden)
    with pytest.raises(ValueError, match="budget|workspace|limit"): analysis.analyze_mixed(body)


def test_large_admitted_spectrum_actual_serialization_within_forecast(tmp_path):
    body = request(causal=False); body["spectrum"]["samples"] = 6001
    store = MixedReportStore(tmp_path)
    report = store.create(body)
    size = storage.json_measure(report)
    assert size["encoded_bytes"] <= report["resources"]["estimated_report_bytes"]
    assert size["expanded_bytes"] <= report["resources"]["estimated_report_expanded_bytes"]
    assert store.read(report["id"]) == report


@pytest.mark.parametrize("key,value", [("analytic_alias_bound", 0.), ("frequency_cutoff_bound", 0.),
    ("model_version", "other"), ("frequency_terms", 1), ("precision_bits", 64), ("total_error_bound", 0.),
    ("arithmetic_complex_bound", -1.), ("arithmetic_envelope_bound", 1.), ("certificate_version", "other")])
def test_forged_certificate_rejected_before_publication(fixture, key, value):
    store, body, result, _ = fixture
    result["causal_pulse"]["diagnostics"][key] = value
    with pytest.raises(ValueError): store.create(body)
    assert not store.directory.exists()


def test_resealed_zero_analytic_plan_rejected_offline(fixture):
    store, body, _, _ = fixture
    report = store.create(body)
    for key in ("analytic_alias_bound", "frequency_cutoff_bound"):
        report["resources"]["causal_pulse"][key] = 0.
        report["causal_pulse"]["diagnostics"][key] = 0.
    rehash(report); rewrite(store, report)
    with pytest.raises(ValueError, match="strictly positive"): store.read(report["id"])


@pytest.mark.parametrize("change", ["time", "nonfinite", "shape", "transmitted_rf", "material", "spectrum_identity", "nested_rf", "zero_flag"])
def test_output_contract_preserves_shapes_and_signal_scope(fixture, change):
    store, body, result, _ = fixture
    if change == "time": result["causal_pulse"]["time_us"][10] = float(np.nextafter(result["causal_pulse"]["time_us"][10], np.inf))
    elif change == "nonfinite": result["causal_pulse"]["rf"][1] = float("nan")
    elif change == "shape": result["spectrum"]["reflection"]["real"].pop()
    elif change == "transmitted_rf": result["transmitted_rf"] = [0.]
    elif change == "material": result["spectrum"]["materials"] = []
    elif change == "spectrum_identity": result["spectrum"]["diagnostics"]["model_version"] = "other"
    elif change == "nested_rf": result["spectrum"]["transmitted_rf"] = [0.]
    else: result["spectrum"]["materials"][0]["zero_relaxation"] = True
    with pytest.raises(ValueError): store.create(body)
    assert not store.directory.exists()


def test_raw_energy_and_negative_rf_are_not_clipped(fixture):
    store, body, result, _ = fixture
    result["spectrum"]["absorptance"][0] = -1e-16
    result["causal_pulse"]["rf"][0] = -0.0
    result["causal_pulse"]["imaginary"][0] = 0.0
    result["causal_pulse"]["envelope"][0] = 0.0
    report = store.create(body)
    assert report["spectrum"]["absorptance"][0] == -1e-16
    assert b"-0.0" in store._path(report["id"]).read_bytes()
    assert min(report["causal_pulse"]["rf"]) < 0


def test_historical_reader_avoids_current_schema_solver_proof_and_flint(fixture, monkeypatch):
    store, body, _, _ = fixture
    report = store.create(body); before = hashes(store.root)
    monkeypatch.setattr(storage, "PROCESSING_VERSION", "future")
    for name in ("_fingerprints", "_proof_identity", "_runtime"):
        monkeypatch.setattr(storage, name, lambda: pytest.fail("No current provenance lookup on historical read"))
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name == "flint" or name.endswith(("mixed_analysis", "mixed_acoustics", "mixed_time", "mixed_schemas")):
            pytest.fail("Historical read imported a current solver/schema")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    assert MixedReportStore(store.root).read(report["id"]) == report
    assert storage.mixed_report_csv(report)
    assert MixedReportStore(store.root).list()[0]["id"] == report["id"]
    assert hashes(store.root) == before


@pytest.mark.parametrize("target", ["missing_fingerprint", "bad_fingerprint", "proof_path", "proof_digest", "runtime", "package"])
def test_resealed_malformed_provenance_rejected_without_current_lookup(fixture, target):
    store, body, _, _ = fixture
    report = store.create(body); value = report["provenance"]
    if target == "missing_fingerprint": value["implementation_sha256"].pop("mixed_time.py")
    elif target == "bad_fingerprint": value["implementation_sha256"]["mixed_time.py"] = "z"*64
    elif target == "proof_path": value["proof_document"]["path"] = "../../outside"
    elif target == "proof_digest": value["proof_document"]["sha256"] = "f"
    elif target == "runtime": value["runtime"]["python"] = ""
    else: value["runtime"]["numerical_packages"]["flint_release"] = True
    rehash(report); rewrite(store, report)
    with pytest.raises(ValueError): store.read(report["id"])


@pytest.mark.parametrize("target,value", [("density_kg_m3", True), ("density_kg_m3", -1.),
    ("unrelaxed_modulus_gpa", 1.), ("relaxation_time_us", 0.), ("name", ""), ("thickness_mm", 7.)])
def test_resealed_invalid_passive_material_rejected(fixture, target, value):
    store, body, _, _ = fixture
    report = store.create(body)
    report["request"]["stack"]["layers"][0][target] = value
    report["stack"]["layers"][0][target] = value
    report["request_sha256"] = storage.json_measure(report["request"])["sha256"]
    report["provenance"]["request_sha256"] = report["request_sha256"]
    report["stack_sha256"] = storage.json_measure(report["stack"])["sha256"]
    rehash(report); rewrite(store, report)
    with pytest.raises(ValueError): store.read(report["id"])


@pytest.mark.parametrize("field", ["request", "stack", "spectrum", "causal_pulse", "provenance"])
def test_tampered_report_rejected(fixture, field):
    store, body, _, _ = fixture
    report = store.create(body); report[field]["tampered"] = True; rewrite(store, report)
    with pytest.raises(ValueError): store.read(report["id"])


@pytest.mark.parametrize("which", ["report", "temporary"])
def test_exclusive_collision_preserves_prior_bytes(fixture, monkeypatch, which):
    store, body, _, calls = fixture
    identifier = str(uuid4()); monkeypatch.setattr(storage, "uuid4", lambda: identifier)
    store.directory.mkdir()
    path = store.directory/(f"{identifier}.json" if which == "report" else f".{identifier}.tmp")
    path.write_bytes(b"preserve")
    with pytest.raises(FileExistsError): store.create(body)
    assert path.read_bytes() == b"preserve" and len(list(store.directory.iterdir())) == 1
    assert len(calls) == (0 if which == "report" else 1)


@pytest.mark.parametrize("which", ["fsync", "link"])
def test_publication_failure_has_no_visible_partial_report(fixture, monkeypatch, which):
    store, body, _, _ = fixture
    def fail(*args): raise OSError("injected publication failure")
    monkeypatch.setattr(os, which, fail)
    with pytest.raises(OSError, match="injected"): store.create(body)
    assert list(store.directory.iterdir()) == []


def test_complete_record_visible_at_atomic_link_boundary(fixture, monkeypatch):
    store, body, _, _ = fixture
    link, seen = os.link, []
    def publish(source, target):
        assert not list(store.directory.glob("*.json"))
        identifier = json.loads(Path(source).read_bytes())["id"]
        link(source, target); seen.append(store.read(identifier))
    monkeypatch.setattr(os, "link", publish)
    report = store.create(body)
    assert seen == [report]


@pytest.mark.parametrize("key", ["estimated_peak_bytes", "estimated_report_bytes", "estimated_report_expanded_bytes"])
def test_understated_forecast_rejects_before_publication(fixture, key):
    store, body, result, _ = fixture
    result["resources"][key] = 1
    with pytest.raises(ValueError): store.create(body)
    assert not store.directory.exists()


def test_disk_preflight_before_any_synthesis(fixture, monkeypatch):
    store, body, _, calls = fixture
    monkeypatch.setattr(storage, "_disk", lambda *a: (_ for _ in ()).throw(ValueError("disk reserve")))
    with pytest.raises(ValueError, match="disk reserve"): store.create(body)
    assert calls == [] and not store.directory.exists()


@pytest.mark.parametrize("payload", [b'{"x":1,"x":2}', b'{"x":1e400}', b'{"x":NaN}', b'{"x":Infinity}', b'{broken'])
def test_parser_rejects_duplicate_nonfinite_malformed(tmp_path, payload):
    path = tmp_path/"raw.json"; path.write_bytes(payload)
    with pytest.raises(ValueError): storage._read_json(path)


@pytest.mark.parametrize("which", ["bytes", "expanded", "depth", "workspace"])
def test_raw_admission_precedes_json_decoder(tmp_path, monkeypatch, which):
    path = tmp_path/"raw.json"; path.write_bytes(b'['+b'{},'*100+b'{}]')
    if which == "bytes": monkeypatch.setattr(storage, "MAX_REPORT_BYTES", 1)
    elif which == "expanded": monkeypatch.setattr(storage, "MAX_REPORT_EXPANDED_BYTES", 1)
    elif which == "workspace": monkeypatch.setattr(storage, "MAX_WORKSPACE_BYTES", 1)
    else: path.write_bytes(b'['*65+b'0'+b']'*65)
    monkeypatch.setattr(json, "loads", lambda *a, **k: pytest.fail("Guard must precede decode"))
    with pytest.raises(ValueError): storage._read_json(path)


@pytest.mark.parametrize("identifier", ["../outside", "x", None, True, "ABCDEFAB-1234-1234-1234-123456789012"])
def test_canonical_uuid_and_path_guard(tmp_path, identifier):
    with pytest.raises(ValueError): MixedReportStore(tmp_path).read(identifier)


@pytest.mark.parametrize("target", ["root", "directory", "leaf"])
def test_links_rejected_before_parse(tmp_path, monkeypatch, target):
    outside = tmp_path/"outside"; outside.mkdir(); root = tmp_path/"root"; root.mkdir()
    store, identifier = MixedReportStore(root), str(uuid4())
    link = root if target == "root" else store.directory if target == "directory" else store._path(identifier)
    if target == "root": root.rmdir()
    elif target == "leaf": store.directory.mkdir()
    destination = outside if target != "leaf" else outside/"data.json"
    if target == "leaf": destination.write_text("{}")
    try: link.symlink_to(destination, target_is_directory=target != "leaf")
    except OSError: pytest.skip("Symlinks unavailable on this host")
    monkeypatch.setattr(storage, "_read_json", lambda *a: pytest.fail("Path guard before parse"))
    with pytest.raises(ValueError, match="links|junctions"): store.read(identifier)


def test_junction_guard_before_parse(tmp_path, monkeypatch):
    store = MixedReportStore(tmp_path)
    monkeypatch.setattr(Path, "is_junction", lambda p: p == store.directory, raising=False)
    with pytest.raises(ValueError, match="junctions"): store.read(str(uuid4()))


def test_catalog_is_paginated_and_preserves_unpublished_stages(fixture, monkeypatch):
    store, body, _, _ = fixture
    ids = [f"00000000-0000-0000-0000-00000000000{i}" for i in range(1, 4)]
    for identifier in ids:
        monkeypatch.setattr(storage, "uuid4", lambda identifier=identifier: identifier); store.create(body)
    stage = store.directory/".old.tmp"; stage.write_bytes(b"preserve")
    original, seen = store.read, []
    def read(identifier, **kwargs): seen.append(identifier); return original(identifier, **kwargs)
    monkeypatch.setattr(store, "read", read)
    page = store.list(limit=1, offset=1)
    assert seen == [ids[1]] and page[0]["id"] == ids[1]
    assert "spectrum" not in page[0] and "request" not in page[0]
    assert stage.read_bytes() == b"preserve"
    monkeypatch.setattr(storage, "MAX_CATALOG_FILES", 1)
    seen.clear()
    with pytest.raises(ValueError, match="entry limit"): store.list()
    assert seen == []


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": True}, {"offset": -1}, {"offset": 10000}])
def test_catalog_validation(tmp_path, params):
    with pytest.raises(ValueError, match="pagination"): MixedReportStore(tmp_path).list(**params)


def test_new_identity_covers_kernel_units_shared_lock_and_proof():
    for name, value in storage._fingerprints().items():
        assert value == hashlib.sha256(Path(storage.__file__).with_name(name).read_bytes()).hexdigest()
    assert {"mixed_acoustics.py", "mixed_time.py", "mixed_schemas.py", "layered_time.py", "causal_comparison_store.py"} <= storage._fingerprints().keys()
    proof = storage._proof_identity()
    assert proof["sha256"] == hashlib.sha256((Path(__file__).parents[1]/proof["path"]).read_bytes()).hexdigest()
    runtime = storage._runtime()
    assert runtime["numerical_packages"]["python-flint"] and runtime["numerical_packages"]["flint"]


def test_api_estimate_create_list_exact_exports_and_restart_preserve_unrelated_files(tmp_path):
    (tmp_path/"legacy.json").write_bytes(b"untouched")
    with client(tmp_path) as api:
        assert api.post("/api/v2/mixed-acoustics/estimate", json=request()).status_code == 200
        assert not (tmp_path/"mixed-reports").exists()
        response = api.post("/api/v2/mixed-acoustics/reports", json=request())
        assert response.status_code == 201, response.text
        report = response.json(); prefix = f'/api/v2/mixed-acoustics/reports/{report["id"]}'
        before = hashes(tmp_path)
        assert api.get(prefix).json() == report
        assert api.get(prefix+"/export").content == storage.bounded_payload(report)
        rows = csv.DictReader(io.StringIO(api.get(prefix+"/export?format=csv").text))
        assert {row["field"]: json.loads(row["value_json"]) for row in rows} == report
        page = api.get("/api/v2/mixed-acoustics/reports?limit=1").json()
        assert page["reports"][0]["id"] == report["id"] and page["order"] == "id_desc"
        assert api.get(prefix+"/export?format=png").status_code == 422
        assert api.get("/api/v2/mixed-acoustics/reports/not-a-uuid").status_code == 422
        assert api.get(f"/api/v2/mixed-acoustics/reports/{uuid4()}").status_code == 404
        assert hashes(tmp_path) == before
    with client(tmp_path) as restarted:
        assert restarted.get(prefix).json() == report and hashes(tmp_path) == before
    assert (tmp_path/"legacy.json").read_bytes() == b"untouched"
    assert not (tmp_path/"catalog.sqlite3").exists()


@pytest.mark.parametrize("change", ["source_column", "pulse", "kind", "density_bool", "moduli", "redundant_speed", "exterior_loss"])
def test_api_rejects_wrong_instrument_or_material_fields_without_publication(tmp_path, change):
    body = request()
    if change in {"source_column", "pulse", "kind"}: body[change] = {} if change != "kind" else "sam_causal_rf_volume"
    elif change == "density_bool": body["stack"]["layers"][0]["density_kg_m3"] = True
    elif change == "moduli": body["stack"]["layers"][0]["unrelaxed_modulus_gpa"] = 1.
    elif change == "redundant_speed": body["stack"]["layers"][0]["sound_speed_m_s"] = 1500.
    else: body["stack"]["terminal"]["pressure_loss_db_mm"] = 1.
    with client(tmp_path) as api:
        assert api.post("/api/v2/mixed-acoustics/reports", json=body).status_code == 422
        assert api.get("/api/v2/mixed-acoustics/reports").json()["reports"] == []
    assert not list(tmp_path.iterdir())


def test_service_keeps_calculation_and_serialization_inside_shared_processing_lock(tmp_path, monkeypatch):
    import virtual_microscopy.mixed_api as api_module
    entered = []
    class Lock:
        active = False
        def __enter__(self): self.active = True; entered.append("enter")
        def __exit__(self, *args): self.active = False; entered.append("exit")
    lock = Lock()
    monkeypatch.setattr(api_module, "_processing_lock", lock)
    original_estimate, original_payload = analysis.estimate_mixed, api_module.bounded_payload
    def estimate(body): assert lock.active; return original_estimate(body)
    def encode(value): assert lock.active; return original_payload(value)
    monkeypatch.setattr(analysis, "estimate_mixed", estimate); monkeypatch.setattr(api_module, "bounded_payload", encode)
    with client(tmp_path) as api:
        assert api.post("/api/v2/mixed-acoustics/estimate", json=request(causal=False)).status_code == 200
    assert entered == ["enter", "exit"]


def real_layer(*, thickness=.125):
    return {'kind':'lossless_real','name':'Explicit spacer','thickness_mm':thickness,
        'impedance_mrayl':2.,'sound_speed_m_s':2000.}


def test_mixed_real_sls_zero_thickness_retains_typed_curves_and_five_counts(tmp_path):
    body=request();body['stack']['layers']=[real_layer(),body['stack']['layers'][0],real_layer(thickness=0.)]
    store=MixedReportStore(tmp_path);report=store.create(body)
    expected={'layer_count':3,'authored_layer_count':3,'active_layer_count':2,'lossless_real_layer_count':2,'sls_layer_count':1}
    for location in (report['resources'],report['resources']['spectrum'],report['resources']['causal_pulse'],
            report['spectrum']['diagnostics'],report['causal_pulse']['diagnostics'],store.list()[0]):
        assert {k:location[k] for k in expected}==expected
    assert report['stack']['layers']==MixedLayeredAnalysisRequest.model_validate(body).model_dump(mode='json')['stack']['layers']
    for index in (0,2):
        curves=report['spectrum']['materials'][index]
        assert curves['kind']=='lossless_real' and curves['layer_index']==index and curves['zero_relaxation'] is None
        assert curves['phase_speed_m_s']==[2000.]*17 and curves['impedance_real_mrayl']==[2.]*17
        assert curves['attenuation_np_m']==curves['impedance_imag_mrayl']==[0.]*17
    assert storage.bounded_payload(store.read(report['id']))==storage.bounded_payload(report)


@pytest.mark.parametrize('field,value',[('kind','sls'),('layer_index',True),('zero_relaxation',False),
    ('phase_speed_m_s',[1999.]*17),('impedance_real_mrayl',[2.01]*17),('attenuation_np_m',[.001]*17),
    ('low_frequency_speed_m_s',1999.),('thickness_mm',.25)])
def test_resealed_real_material_curves_cannot_impersonate_other_media(tmp_path,field,value):
    body=request(causal=False);body['stack']['layers']=[real_layer()]
    store=MixedReportStore(tmp_path);report=store.create(body)
    report['spectrum']['materials'][0][field]=value;rehash(report);rewrite(store,report)
    with pytest.raises(ValueError):store.read(report['id'])


@pytest.mark.parametrize('field,value',[('phase_speed_m_s',[1.]*17),('impedance_imag_mrayl',[-.1]*17),
    ('attenuation_db_mm',[1.]*17),('high_frequency_speed_m_s',1999.)])
def test_resealed_sls_material_physical_consistency_rejected(fixture,field,value):
    store,body,_,_=fixture;report=store.create(body)
    report['spectrum']['materials'][0][field]=value;rehash(report);rewrite(store,report)
    with pytest.raises(ValueError):store.read(report['id'])


def test_interior_frequency_ulp_mutation_rejected_without_forward_import(fixture):
    store,body,_,_=fixture;report=store.create(body)
    report['spectrum']['frequency_mhz'][7]=float(np.nextafter(report['spectrum']['frequency_mhz'][7],np.inf))
    rehash(report);rewrite(store,report)
    with pytest.raises(ValueError,match='frequency axis'):store.read(report['id'])


@pytest.mark.parametrize('start,end,count',[(.10000000000000002,149.7,1025),(0.,np.nextafter(0.,1.),2),
    (0.,np.finfo(float).tiny,31),(2.2222222222,300.,8193),(0.,150.,17)])
def test_frozen_linspace_replay_matches_actual_binary64_numpy(start,end,count):
    settings={'start_mhz':float(start),'end_mhz':float(end),'samples':count}
    assert np.asarray(storage._axis(settings),dtype=np.float64).tobytes()==np.linspace(start,end,count,dtype=np.float64).tobytes()


@pytest.mark.parametrize('field,value',[('analytic_alias_bound',1e-100),('frequency_cutoff_bound',1e-100),
    ('gamma_rate_per_us',1.),('gamma_peak_us',1.),('laplace_damping_per_us',1.),('half_frequency_span_mhz',1.),
    ('period_us',10.),('inverse_work_units',1),('estimated_peak_bytes',1),('lossless_real_layer_count',1)])
def test_resealed_plan_and_diagnostics_cannot_jointly_forge_relationships(fixture,field,value):
    store,body,_,_=fixture;report=store.create(body)
    report['resources']['causal_pulse'][field]=value;report['causal_pulse']['diagnostics'][field]=value
    rehash(report);rewrite(store,report)
    with pytest.raises(ValueError):store.read(report['id'])


@pytest.mark.parametrize('field',['real','imag','magnitude','phase_deg'])
def test_resealed_pressure_magnitude_and_phase_contracts(fixture,field):
    store,body,_,_=fixture;report=store.create(body)
    report['spectrum']['reflection'][field][4]=None if field=='phase_deg' else 100.
    rehash(report);rewrite(store,report)
    with pytest.raises(ValueError):store.read(report['id'])


def test_resealed_causal_magnitude_rejects_internal_inconsistency(fixture):
    store,body,_,_=fixture;report=store.create(body)
    report['causal_pulse']['envelope'][20]+=1.
    rehash(report);rewrite(store,report)
    with pytest.raises(ValueError,match='magnitude'):store.read(report['id'])


def test_actual_resource_measure_includes_retained_numerics_and_csv_http(fixture):
    store,body,_,_=fixture;report=store.create(body);resources=report['resources']
    numerical=max(resources['spectrum']['estimated_peak_bytes'],resources['causal_pulse']['estimated_peak_bytes'])
    assert resources['estimated_peak_bytes']==3*resources['estimated_report_expanded_bytes']+12*resources['estimated_report_bytes']+numerical+8*1024**2
    assert len(storage.mixed_report_csv(report).encode('ascii'))<=2*resources['estimated_report_bytes']
    report['resources']['estimated_peak_bytes']-=numerical
    rehash(report);rewrite(store,report)
    with pytest.raises(ValueError,match='forecast'):store.read(report['id'])


def test_listing_predecode_receives_compact_retention_and_releases_full_record(fixture,monkeypatch):
    import weakref
    store,body,_,_=fixture
    for _ in range(3):store.create(body)
    original=store.read;refs=[];retained=[]
    class Tracked(dict):pass
    def read(identifier,*,retained_bytes=0):
        assert not refs or refs[-1]() is None
        retained.append(retained_bytes);value=Tracked(original(identifier,retained_bytes=retained_bytes));refs.append(weakref.ref(value));return value
    monkeypatch.setattr(store,'read',read)
    assert len(store.list())==3 and retained[0]==0 and 0<retained[1]<retained[2]


def test_retained_catalog_budget_is_checked_before_decode(tmp_path,monkeypatch):
    path=tmp_path/'record.json';path.write_bytes(b'{}')
    monkeypatch.setattr(json,'loads',lambda *a,**k:pytest.fail('Retained budget must reject before JSON decode'))
    with pytest.raises(ValueError):storage._read_json(path,retained_bytes=256*1024**2)


def test_prior_sls_request_and_missing_tags_are_isolated_without_publication(tmp_path):
    body=request();body.pop('kind')
    with client(tmp_path) as api:
        assert api.post('/api/v2/mixed-acoustics/reports',json=body).status_code==422
        body['kind']='mixed_layered_analysis';body['stack']['layers'][0].pop('kind')
        assert api.post('/api/v2/mixed-acoustics/reports',json=body).status_code==422
        assert api.get('/api/v2/mixed-acoustics/reports').json()['reports']==[]
    assert not list(tmp_path.iterdir())
