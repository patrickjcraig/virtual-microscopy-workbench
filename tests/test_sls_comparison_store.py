"""Immutable standalone comparison storage, bounded parsing and offline inspection."""
from copy import deepcopy
import builtins
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from virtual_microscopy import sls_comparison_store as storage
from virtual_microscopy.sls_reports import SLSReportStore
from tests.test_sls_reports import request as source_request


@pytest.fixture(scope="module")
def originals(tmp_path_factory):
    root = tmp_path_factory.mktemp("sls-comparison-originals")
    body = source_request()
    first = SLSReportStore(root).create(body)
    body["stack"]["layers"][0]["unrelaxed_modulus_gpa"] = 4.1
    second = SLSReportStore(root).create(body)
    return first, second


@pytest.fixture
def fixture(tmp_path, originals):
    directory = tmp_path/"sls-reports"; directory.mkdir()
    for report in originals:
        (directory/f'{report["id"]}.json').write_bytes(storage.bounded_payload(report))
    body = {"reference_report_id": originals[0]["id"], "candidate_report_id": originals[1]["id"],
        "mode": "spectrum_and_reflected_rf", "gate_start_us": .1, "gate_end_us": .4,
        "frequency_index": 3, "time_index": 44}
    return storage.SLSComparisonStore(tmp_path), body


def rehash(report):
    report["request_sha256"] = storage.json_measure(report["request"])["sha256"]
    report["report_sha256"] = storage.json_measure({k: v for k, v in report.items() if k != "report_sha256"})["sha256"]


def write(store, report):
    store._path(report["id"]).write_bytes(storage.bounded_payload(report))


def hashes(directory):
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.rglob("*") if p.is_file()}


def client(root):
    from virtual_microscopy.sls_comparison_api import router
    app = FastAPI(); app.state.volume_jobs = SimpleNamespace(root=root); app.include_router(router)
    return TestClient(app)


def test_real_pair_freezes_each_complete_source_once_and_preserves_all_bytes(fixture, originals):
    store, body = fixture
    before = hashes(store.root/"sls-reports")
    report = store.create(body)
    assert len(report["source_snapshots"]) == 2
    assert report["kind"] == "sls_analysis_comparison"
    for role, original in zip(("reference", "candidate"), originals):
        ref = report[f"source_{role}"]
        assert report["source_snapshots"][ref["snapshot_sha256"]] == original
        assert ref["snapshot_sha256"] == storage.json_measure(original)["sha256"]
        assert set(ref) == {"name", "report_id", "snapshot_sha256", "report_sha256"}
    assert len(report["parameter_differences"]) == 1
    assert report["parameter_differences"][0]["path"] == "request.stack.layers[0].unrelaxed_modulus_gpa"
    assert any(report["causal_pulse"]["difference"]["rf"])
    assert store.read(report["id"]) == report
    assert hashes(store.root/"sls-reports") == before
    assert store.list()[0]["comparison_id"] == report["id"]
    size = storage.json_measure(report)
    assert size["encoded_bytes"] <= report["resources"]["estimated_report_bytes"]
    assert size["expanded_bytes"] <= report["resources"]["estimated_report_expanded_bytes"]


def test_self_comparison_deduplicates_without_discarding_positive_bounds(fixture):
    store, body = fixture; body["candidate_report_id"] = body["reference_report_id"]
    report = store.create(body)
    assert len(report["source_snapshots"]) == 1
    assert report["source_reference"] == report["source_candidate"]
    for values in report["causal_pulse"]["difference"].values(): assert values == [0.]*201
    assert all(v > 0 for v in report["causal_pulse"]["bounds"].values())
    assert report["causal_pulse"]["full_metrics"]["rf"]["max_location"]["time_index"] == 0
    assert report["causal_pulse"]["gate_metrics"]["rf"]["max_location"]["time_index"] == 40


def test_offline_read_cursors_json_csv_without_source_or_current_imports(fixture, monkeypatch):
    store, body = fixture; report = store.create(body)
    shutil.rmtree(store.root/"sls-reports")
    def forbidden(*a, **k): pytest.fail("Historical comparison consulted current computation")
    monkeypatch.setattr(storage, "_fingerprints", forbidden); monkeypatch.setattr(storage, "_runtime", forbidden)
    real_import = builtins.__import__
    forbidden_modules = {"sls_comparisons", "sls_comparison_schemas", "sls_comparison_math", "sls_schemas", "sls_acoustics", "sls_time", "sls_analysis", "flint"}
    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if name.rsplit('.',1)[-1] in forbidden_modules or any(item in forbidden_modules for item in fromlist or ()):
            pytest.fail(f"Historical import: {name} / {fromlist}")
        return real_import(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, "__import__", guarded)
    assert store.read(report["id"]) == report
    view = store.view(report["id"], frequency_index=16, time_index=200)
    a = report["source_snapshots"][report["source_reference"]["snapshot_sha256"]]
    assert view["frequency_mhz"] == a["spectrum"]["frequency_mhz"][16]
    assert view["time_us"] == a["causal_pulse"]["time_us"][200]
    assert view["causal_pulse"]["reference"]["rf"] == a["causal_pulse"]["rf"][200]
    assert view["causal_pulse"]["difference"]["imaginary"] == report["causal_pulse"]["difference"]["imaginary"][200]
    assert len(store.list()) == 1
    before = csv.field_size_limit()
    try:
        csv.field_size_limit(storage.MAX_REPORT_BYTES)
        decoded = {row["field"]: json.loads(row["value_json"]) for row in csv.DictReader(io.StringIO(storage.sls_comparison_csv(report)))}
    finally: csv.field_size_limit(before)
    assert storage.bounded_payload(decoded) == storage.bounded_payload(report)


def test_spectrum_only_does_not_publish_or_admit_rf_controls(fixture):
    store, body = fixture
    body.update(mode="spectrum_only", gate_start_us=None, gate_end_us=None, time_index=None)
    report = store.create(body)
    assert report["causal_pulse"] is None and report["arithmetic_policy"] is None
    assert store.view(report["id"])["time_us"] is None
    with pytest.raises(ValueError): store.view(report["id"], time_index=0)
    assert store.read(report["id"]) == report


@pytest.mark.parametrize("change", [
    lambda r: r.update(kind="sls_layered_analysis"),
    lambda r: r["source_reference"].update(name="wrong"),
    lambda r: r["source_candidate"].update(report_sha256="0"*64),
    lambda r: r["source_candidate"].update(snapshot_sha256=[]),
    lambda r: r.update(created_at="2026-09-05"),
    lambda r: r["request"].update(extra=True),
    lambda r: r["request"].update(frequency_index=99),
    lambda r: r["request"].update(time_index=202),
    lambda r: r["request"].update(time_index=True),
    lambda r: r["compatibility"].update(matched_fields=[]),
    lambda r: r.update(parameter_differences=[]),
    lambda r: r["causal_pulse"]["gate"].update(start_index=0),
    lambda r: r["causal_pulse"]["gate"].update(actual_start_us=.11),
    lambda r: r["causal_pulse"]["gate"].update(selection="Interpolated"),
    lambda r: r["causal_pulse"]["bounds"].update(source_sum=0.),
    lambda r: r["causal_pulse"]["bounds"].update(complex_arithmetic=0.),
    lambda r: r["causal_pulse"]["bounds"].update(magnitude_total=0.),
    lambda r: r["causal_pulse"]["difference"]["rf"].__setitem__(0, 1.),
    lambda r: r["causal_pulse"]["difference"]["imaginary"].pop(),
    lambda r: r["causal_pulse"]["full_metrics"]["rf"]["max_location"].update(time_us=9.),
    lambda r: r["causal_pulse"]["gate_metrics"]["rf"].update(sample_count=1),
    lambda r: r["causal_pulse"]["gate_products"]["peak_envelope"].update(reference=-1.),
    lambda r: r["spectrum"]["difference"]["reflection"].update(phase_deg=[0.]*17),
    lambda r: r["spectrum"]["metrics"]["reflection"]["real"]["max_location"].update(frequency_mhz=12.),
    lambda r: r["resources"].update(estimated_peak_bytes=1),
    lambda r: r["resources"].update(estimated_report_bytes=1),
    lambda r: r["resources"].update(estimated_report_expanded_bytes=1),
    lambda r: r["store_identity"]["implementation_sha256"].pop("sls_comparison_math.py"),
    lambda r: r["store_identity"]["runtime"].update(python=""),
])
def test_resealed_structural_semantic_and_resource_corruption_rejected(fixture, change):
    store, body = fixture; report = store.create(body)
    change(report); rehash(report); write(store, report)
    with pytest.raises(ValueError): store.read(report["id"])


def mutate_snapshot(report, change):
    reference = report["source_candidate"]
    old = reference["snapshot_sha256"]
    source = report["source_snapshots"].pop(old)
    change(source)
    source["stack_sha256"] = storage.json_measure(source["stack"])["sha256"]
    source["request_sha256"] = storage.json_measure(source["request"])["sha256"]
    source["provenance"]["request_sha256"] = source["request_sha256"]
    source["report_sha256"] = storage.json_measure({k:v for k,v in source.items() if k != "report_sha256"})["sha256"]
    digest = storage.json_measure(source)["sha256"]
    report["source_snapshots"][digest] = source
    reference.update(snapshot_sha256=digest, report_sha256=source["report_sha256"])


@pytest.mark.parametrize("change", [
    lambda s: s["stack"]["incident"].update(impedance_mrayl=1.49),
    lambda s: s["stack"]["terminal"].update(sound_speed_m_s=1500.),
    lambda s: s["request"]["causal_pulse"].update(center_frequency_mhz=49.),
    lambda s: s["spectrum"].update(reference_planes="different"),
    lambda s: s["spectrum"].update(phase_magnitude_floor=1e-11),
    lambda s: s["provenance"]["proof_document"].update(sha256="0"*64),
    lambda s: s["provenance"]["implementation_sha256"].update(sls_time_py="0"*64),
    lambda s: s["spectrum"]["frequency_mhz"].__setitem__(2, 18.750000000000004),
])
def test_resealed_incompatible_sources_rejected_without_current_core(fixture, change):
    store, body = fixture; report = store.create(body)
    # Copy identity-sharing request/stack deliberately as they are decoded on disk.
    report = json.loads(storage.bounded_payload(report))
    def mutate(source):
        change(source)
        source["request"]["stack"] = deepcopy(source["stack"])
    mutate_snapshot(report, mutate); rehash(report); write(store, report)
    with pytest.raises(ValueError): store.read(report["id"])


@pytest.mark.parametrize("payload", [b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e400}', b'{"x":'+b'1'*311+b'}'])
def test_duplicate_nonfinite_or_oversized_integer_json_rejected(tmp_path, payload):
    path = tmp_path/"bad.json"; path.write_bytes(payload)
    with pytest.raises(ValueError): storage.bounded_json_read(path)


def test_raw_expansion_retained_first_source_and_depth_reject_before_decode(tmp_path, monkeypatch):
    path = tmp_path/"large.json"; path.write_bytes(b'['+b'{},'*400_000+b'{}]')
    def forbidden(*a, **k): pytest.fail("Decoded before expanded admission")
    monkeypatch.setattr(storage._json.json, "loads", forbidden)
    with pytest.raises(ValueError, match="expanded|limit"): storage.bounded_json_read(path)
    path.write_bytes(b'{"ok":1}')
    with pytest.raises(ValueError, match="workspace"): storage.bounded_json_read(path, retained_bytes=512*1024**2)
    path.write_bytes(b'['*66+b'0'+b']'*66)
    with pytest.raises(ValueError, match="depth"): storage.bounded_json_read(path)


@pytest.mark.parametrize("identifier", ["../escape", "ABC", "00000000-0000-0000-0000-00000000000A", "{00000000-0000-0000-0000-000000000000}"])
def test_canonical_uuid_path_boundary(tmp_path, identifier):
    with pytest.raises(ValueError): storage.SLSComparisonStore(tmp_path).read(identifier)


def test_linked_directory_and_leaf_rejected_before_read(tmp_path, monkeypatch):
    store = storage.SLSComparisonStore(tmp_path)
    identifier = str(uuid4()); actual = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda p: p == store.directory or actual(p))
    with pytest.raises(ValueError, match="links|junctions"): store._path(identifier)
    monkeypatch.setattr(Path, "is_symlink", lambda p: p == store.directory/f"{identifier}.json" or actual(p))
    with pytest.raises(ValueError, match="links|junctions"): store._path(identifier)


def test_exclusive_publication_and_stage_collision_preserve_bytes(fixture, monkeypatch):
    store, body = fixture; identifier = str(uuid4())
    monkeypatch.setattr(storage, "uuid4", lambda: identifier)
    first = store.create(body); before = store._path(identifier).read_bytes()
    with pytest.raises(FileExistsError): store.create(body)
    assert store._path(identifier).read_bytes() == before
    second = str(uuid4()); monkeypatch.setattr(storage, "uuid4", lambda: second)
    stage = store.directory/f".{second}.tmp"; stage.write_bytes(b"untouched")
    with pytest.raises(FileExistsError): store.create(body)
    assert stage.read_bytes() == b"untouched"
    assert store.list()[0]["id"] == first["id"]


def test_failed_atomic_link_cleans_owned_stage_only(fixture, monkeypatch):
    store, body = fixture
    def fail(*a, **k): raise OSError("Injected publication failure")
    monkeypatch.setattr(storage.os, "link", fail)
    with pytest.raises(OSError): store.create(body)
    assert list(store.directory.iterdir()) == []


def test_disk_failure_does_not_publish(fixture, monkeypatch):
    store, body = fixture
    monkeypatch.setattr(shutil, "disk_usage", lambda p: SimpleNamespace(free=0))
    with pytest.raises(ValueError, match="disk"): store.create(body)
    assert not store.directory.exists()


def test_catalog_pagination_is_bounded_uuid_order(fixture):
    store, body = fixture
    reports = [store.create(body) for _ in range(3)]
    expected = sorted((r["id"] for r in reports), reverse=True)
    assert [r["id"] for r in store.list(limit=2)] == expected[:2]
    assert [r["id"] for r in store.list(limit=2, offset=2)] == expected[2:]
    for kwargs in ({"limit":0}, {"limit":101}, {"offset":10000}, {"limit":True}):
        with pytest.raises(ValueError): store.list(**kwargs)


def test_real_api_estimate_creation_offline_view_exports_and_kind_isolation(fixture):
    store, body = fixture; api = client(store.root)
    estimate = api.post("/api/v2/sls-comparisons/estimate", json=body)
    assert estimate.status_code == 200, estimate.text
    assert not store.directory.exists()
    created = api.post("/api/v2/sls-comparisons/reports", json=body)
    assert created.status_code == 201, created.text
    report = created.json(); route = f'/api/v2/sls-comparisons/reports/{report["id"]}'
    assert api.get("/api/v2/sls-comparisons/reports?limit=1").json()["reports"][0]["id"] == report["id"]
    shutil.rmtree(store.root/"sls-reports")
    assert api.get(route).json() == report
    assert api.get(route+"/view?frequency_index=16&time_index=200").json()["time_index"] == 200
    assert api.get(route+"/view?frequency_index=17").status_code == 422
    assert api.get(route+"/export?format=json").content == storage.bounded_payload(report)
    assert api.get(route+"/export?format=csv").status_code == 200
    assert api.get(route+"/export?format=zip").status_code == 422
    assert api.get(f'/api/v2/sls-comparisons/reports/{body["reference_report_id"]}').status_code == 404
    assert api.post("/api/v2/sls-comparisons/reports", json=body).status_code == 404
    assert api.post("/api/v2/sls-comparisons/reports", json={**body, "kind":"sls_layered_analysis"}).status_code == 422


def test_compatibility_error_preserves_accepted_catalog_and_structured_issues(fixture):
    store, body = fixture; accepted = store.create(body); before = hashes(store.directory)
    source_store = SLSReportStore(store.root)
    request = source_request(); request["stack"]["incident"]["impedance_mrayl"] = 1.5
    other = source_store.create(request)
    response = client(store.root).post("/api/v2/sls-comparisons/reports", json={**body, "candidate_report_id":other["id"]})
    assert response.status_code == 422 and response.json()["detail"]["issues"]
    assert hashes(store.directory) == before and store.read(accepted["id"]) == accepted


def test_frozen_semantic_constants_match_current_producer_without_runtime_import_requirement():
    from virtual_microscopy import sls_comparisons
    for key in ("NUMERICAL_FILES", "SPECTRUM_SEMANTICS", "REFERENCE_PLANES", "PULSE_SEMANTICS", "POLICY"):
        assert getattr(storage, key) == getattr(sls_comparisons, key)


def test_subnormal_and_signed_zero_residual_replay_is_bit_exact():
    tiny = float.fromhex('0x0.0000000000001p-1022')
    storage._residual([tiny, -tiny, -0.0, 0.0], [0., tiny, 0., -0.0], [tiny, 0., -0.0, 0.])
    with pytest.raises(ValueError): storage._residual([0.], [0.], [tiny])
    with pytest.raises(ValueError): storage._residual([0.], [0.], [-0.0])


def test_source_and_report_encoded_caps_precede_decode(tmp_path, monkeypatch):
    path = tmp_path/'oversized.json'; path.write_bytes(b' ' * 65)
    monkeypatch.setattr(storage._json.json, 'loads', lambda *a, **k: pytest.fail('Decoded oversized JSON'))
    with pytest.raises(ValueError, match='byte'): storage.bounded_json_read(path, byte_limit=64)


def test_missing_source_and_corrupt_source_do_not_publish(fixture):
    store, body = fixture
    source_path = store.root/'sls-reports'/f'{body["candidate_report_id"]}.json'
    source_path.write_bytes(b'{"kind":"sam_rf_volume"}')
    with pytest.raises(ValueError): store.create(body)
    assert not store.directory.exists()
    source_path.unlink()
    with pytest.raises(KeyError): store.create(body)
    assert not store.directory.exists()


def test_comparison_provenance_fingerprints_include_all_numeric_and_reader_dependencies():
    identities = storage._fingerprints()
    for name in ('sls_comparison_math.py','causal_comparison_math.py','sls_reports.py','causal_comparison_store.py'):
        assert identities[name] == hashlib.sha256(Path(storage.__file__).with_name(name).read_bytes()).hexdigest()


def test_historical_processing_identity_does_not_require_current_version(fixture):
    store, body = fixture; report = store.create(body)
    report['processing_version'] = 'sls-comparison-historical-compatible'
    rehash(report); write(store, report)
    assert store.read(report['id']) == report
