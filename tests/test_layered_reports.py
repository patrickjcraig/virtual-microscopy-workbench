"""Immutable layered reports: bounded publication, historical reads and APIs."""
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

from virtual_microscopy import layered_reports
from virtual_microscopy.datasets import canonical_json, json_sha256
from virtual_microscopy.layered_reports import LayeredReportStore, bounded_payload, layered_report_csv
from virtual_microscopy.layered_schemas import LayeredAnalysisRequest


def request(pulse=False):
    water = {"name": "Water reference", "impedance_mrayl": 1.48, "sound_speed_m_s": 1480}
    value = {"name": "Independent slab report", "stack": {"incident": water, "terminal": water,
        "layers": [{"name": "Assumed silicon slab", "impedance_mrayl": 19.63347,
                    "sound_speed_m_s": 8430, "thickness_mm": .1, "pressure_loss_db_mm": 0}]},
        "spectrum": {"start_mhz": 0, "end_mhz": 150, "samples": 33}}
    if pulse:
        value["pulse"] = {"center_frequency_mhz": 50, "sample_rate_mhz": 400,
                          "record_start_us": 0, "record_duration_us": .5}
    return value


def file_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def mocked_analysis(monkeypatch):
    from virtual_microscopy import layered_analysis
    calls = []
    estimate = {"estimated_peak_bytes": 1024, "estimated_report_bytes": 65536, "estimated_report_expanded_bytes": 262144,
        "spectrum_work_units": 33, "frequency_samples": 33, "layer_count": 1,
        "max_echoes": 0, "estimated_rf_work_units": 0}

    def preflight(value):
        calls.append("estimate")
        return deepcopy(estimate)

    def analyze(value):
        calls.append("analyze")
        return {"stack": value.stack.model_dump(mode="json", exclude_none=True),
            "source_status": "manual_assumptions", "source_column": None, "stack_differences": [],
            "spectrum": {"frequency_mhz": [0., 75., 150.],
                "reflection": {"real": [0., -.25, .25], "imag": [0., .5, -.5]}},
            "pulse": None, "warnings": ["Synthetic scalar layered approximation."],
            "resources": deepcopy(estimate), "provenance": {"scientific_note": "Frozen convention"}}

    monkeypatch.setattr(layered_analysis, "estimate_layered", preflight)
    monkeypatch.setattr(layered_analysis, "analyze_layered", analyze)
    return SimpleNamespace(calls=calls, estimate=estimate, module=layered_analysis)


def test_report_freezes_input_output_estimate_and_source_fingerprints(tmp_path, mocked_analysis):
    proposal = request()
    store = LayeredReportStore(tmp_path)
    report = store.create(proposal)
    assert mocked_analysis.calls == ["estimate", "analyze"]
    assert report["kind"] == "layered_acoustic_report" and report["id"] == report["report_id"]
    assert report["request"] == LayeredAnalysisRequest.model_validate(proposal).model_dump(mode="json")
    assert report["request_sha256"] == json_sha256(report["request"])
    assert report["report_sha256"] == json_sha256({key: value for key, value in report.items() if key != "report_sha256"})
    assert report["estimate"] == mocked_analysis.estimate
    assert set(report["provenance"]["implementation_sha256"]) == set(layered_reports.IMPLEMENTATION_FILES)
    # The wrapper uses recipes._json_differences to label edited assumptions;
    # assert this dependency independently of the fingerprint registry itself.
    recipes_source = Path(layered_reports.__file__).with_name("recipes.py")
    assert report["provenance"]["implementation_sha256"]["recipes.py"] == hashlib.sha256(recipes_source.read_bytes()).hexdigest()
    assert all(len(value) == 64 for value in report["provenance"]["implementation_sha256"].values())
    saved = deepcopy(report)
    proposal["stack"]["layers"][0]["thickness_mm"] = .9
    report["spectrum"]["reflection"]["real"][0] = 123
    assert store.read(saved["id"]) == saved
    assert store.list() == [{"id": saved["id"], "report_id": saved["id"], "kind": saved["kind"],
        "created_at": saved["created_at"], "report_sha256": saved["report_sha256"], "name": saved["request"]["name"],
        "source_status": "manual_assumptions", "layer_count": 1, "frequency_samples": 33, "pulse_available": False}]
    assert not (tmp_path / "catalog.sqlite3").exists(), "Standalone reports cannot create acquisition jobs"


def test_historical_read_and_exports_do_not_recompute_or_revalidate(tmp_path, monkeypatch, mocked_analysis):
    store = LayeredReportStore(tmp_path)
    report = store.create(request())
    before = file_hashes(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("Historical report must not invoke current analysis, schema or source fingerprints")

    monkeypatch.setattr(mocked_analysis.module, "estimate_layered", forbidden)
    monkeypatch.setattr(mocked_analysis.module, "analyze_layered", forbidden)
    monkeypatch.setattr(LayeredAnalysisRequest, "model_validate", forbidden)
    monkeypatch.setattr(layered_reports, "_source_fingerprints", forbidden)
    reopened = LayeredReportStore(tmp_path)
    assert reopened.read(report["id"]) == report
    assert reopened.list()[0]["id"] == report["id"]
    assert json.loads(bounded_payload(reopened.read(report["id"]))) == report
    rows = list(csv.reader(io.StringIO(layered_report_csv(reopened.read(report["id"])))))
    assert rows[0] == ["section", "field", "value_json"]
    assert len(rows)-1 == len(report)
    for section, field, value in rows[1:]:
        assert section == "report" and json.loads(value) == report[field]
    assert file_hashes(tmp_path) == before


@pytest.mark.parametrize("field", ["spectrum", "request", "identity", "request_digest"])
def test_tampered_report_or_independent_input_hash_is_rejected(tmp_path, mocked_analysis, field):
    store = LayeredReportStore(tmp_path)
    report = store.create(request())
    if field == "spectrum":
        report["spectrum"]["reflection"]["real"][0] += .01
    elif field == "request":
        report["request"]["name"] = "Changed input"
    elif field == "identity":
        report["report_id"] = str(uuid4())
    else:
        report["request_sha256"] = "0"*64
        report["report_sha256"] = json_sha256({key: value for key, value in report.items() if key != "report_sha256"})
    (tmp_path / "layered-reports" / f'{report["id"]}.json').write_bytes(canonical_json(report))
    with pytest.raises(ValueError, match="identity|checksum"):
        store.read(report["id"])


def test_uuid_collision_refuses_overwrite_before_analyzing(tmp_path, monkeypatch, mocked_analysis):
    identifier = uuid4()
    monkeypatch.setattr(layered_reports, "uuid4", lambda: identifier)
    store = LayeredReportStore(tmp_path)
    report = store.create(request())
    before = file_hashes(tmp_path)
    mocked_analysis.calls.clear()
    with pytest.raises(FileExistsError):
        store.create(request())
    assert mocked_analysis.calls == [] and file_hashes(tmp_path) == before
    assert store.read(report["id"])["report_sha256"] == report["report_sha256"]


def test_publication_failure_has_no_visible_partial_report(tmp_path, monkeypatch, mocked_analysis):
    store = LayeredReportStore(tmp_path)
    monkeypatch.setattr(layered_reports.os, "link", lambda *_: (_ for _ in ()).throw(OSError("Injected atomic publish failure")))
    with pytest.raises(OSError, match="publish failure"):
        store.create(request())
    assert store.list() == [] and list((tmp_path / "layered-reports").iterdir()) == []


def test_existing_temporary_file_is_never_removed_or_replaced(tmp_path, monkeypatch, mocked_analysis):
    identifier = uuid4()
    monkeypatch.setattr(layered_reports, "uuid4", lambda: identifier)
    directory = tmp_path / "layered-reports"
    directory.mkdir()
    temporary = directory / f".{identifier}.tmp"
    temporary.write_bytes(b"Preserved previous unpublished evidence")
    with pytest.raises(FileExistsError):
        LayeredReportStore(tmp_path).create(request())
    assert temporary.read_bytes() == b"Preserved previous unpublished evidence"
    assert LayeredReportStore(tmp_path).list() == []


@pytest.mark.parametrize("field,value", [("estimated_peak_bytes", 256*1024**2+1),
    ("estimated_report_bytes", 16*1024**2+1), ("estimated_report_expanded_bytes", 64*1024**2+1),
    ("estimated_peak_bytes", -1), ("estimated_peak_bytes", True)])
def test_resource_preflight_rejects_before_analysis_or_publication(tmp_path, mocked_analysis, field, value):
    mocked_analysis.estimate[field] = value
    with pytest.raises(ValueError, match="resource limit"):
        LayeredReportStore(tmp_path).create(request())
    assert mocked_analysis.calls == ["estimate"]
    assert not (tmp_path / "layered-reports").exists()


def test_disk_preflight_rejects_before_analysis(tmp_path, monkeypatch, mocked_analysis):
    monkeypatch.setattr(layered_reports, "check_disk_space", lambda *_: (_ for _ in ()).throw(OSError("Insufficient disk")))
    with pytest.raises(OSError, match="Insufficient"):
        LayeredReportStore(tmp_path).create(request())
    assert mocked_analysis.calls == ["estimate"]
    assert not (tmp_path / "layered-reports").exists()


def test_payload_budget_rejects_actual_result_before_hash_or_publish(tmp_path, monkeypatch, mocked_analysis):
    monkeypatch.setattr(mocked_analysis.module, "analyze_layered", lambda *_: {
        "provenance": {}, "oversized": "x"*(16*1024**2+1)})
    with pytest.raises(ValueError, match="JSON byte limit"):
        LayeredReportStore(tmp_path).create(request())
    assert not (tmp_path / "layered-reports").exists()


def test_bounded_json_is_canonical_and_rejects_expansion_before_decoding(tmp_path, monkeypatch):
    sample = {"unicode": "µs/mm", "phase": [-0., .1234567890123456, None], "nested": {"x": True}}
    assert bounded_payload(sample) == canonical_json(sample)
    for value in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            bounded_payload({"invalid": value})
    directory = tmp_path / "layered-reports"
    directory.mkdir()
    identifier = str(uuid4())
    (directory / f"{identifier}.json").write_bytes(b'{"extra":['+b'{},'*140_000+b'{}]}')
    monkeypatch.setattr("virtual_microscopy.comparisons.json.loads", lambda *_args, **_kwargs: pytest.fail("Expanded JSON must be rejected before decode"))
    with pytest.raises(ValueError, match="expanded-provenance"):
        LayeredReportStore(tmp_path).read(identifier)


@pytest.mark.parametrize("identifier", ["../outside", "not-a-uuid", "00000000000000000000000000000000"])
def test_report_paths_require_canonical_uuid(tmp_path, identifier):
    with pytest.raises(ValueError):
        LayeredReportStore(tmp_path).read(identifier)


@pytest.mark.parametrize("target", ["directory", "report"])
def test_linked_report_paths_never_touch_external_evidence(tmp_path, monkeypatch, mocked_analysis, target):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("unrelated evidence")
    store = LayeredReportStore(root)
    identifier = str(uuid4())
    try:
        if target == "directory":
            (root / "layered-reports").symlink_to(outside, target_is_directory=True)
        else:
            (root / "layered-reports").mkdir()
            (root / "layered-reports" / f"{identifier}.json").symlink_to(marker)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this Windows account")
    with pytest.raises(ValueError, match="symbolic link|junction"):
        store.read(identifier)
    if target == "directory":
        with pytest.raises(ValueError, match="symbolic link|junction"):
            store.create(request())
        assert mocked_analysis.calls == []
    assert marker.read_text() == "unrelated evidence"


def client(root):
    from virtual_microscopy.layered_api import router
    app = FastAPI()
    app.state.volume_jobs = SimpleNamespace(root=root)
    app.include_router(router)
    return TestClient(app)


def test_api_routes_use_shared_lock_and_preserve_self_contained_exports(tmp_path, mocked_analysis):
    from virtual_microscopy.layered_api import _processing_lock
    from virtual_microscopy.volume_api import _processing_lock as shared
    assert _processing_lock is shared
    http = client(tmp_path)
    assert http.post("/api/v2/layered-acoustics/estimate", json=request()).json() == mocked_analysis.estimate
    response = http.post("/api/v2/layered-acoustics/reports", json=request())
    assert response.status_code == 201, response.text
    report = response.json()
    route = "/api/v2/layered-acoustics/reports/"+report["id"]
    before = file_hashes(tmp_path)
    assert http.get(route).json() == report
    assert http.get("/api/v2/layered-acoustics/reports").json()["reports"][0]["id"] == report["id"]
    for format in ("json", "csv"):
        exported = http.get(route+"/export?format="+format)
        assert exported.status_code == 200 and format in exported.headers["content-disposition"]
        if format == "json":
            assert exported.json() == report
    assert http.get(route+"/export?format=zip").status_code == 422
    assert http.get("/api/v2/layered-acoustics/reports/"+str(uuid4())).status_code == 404
    assert http.get("/api/v2/layered-acoustics/reports/invalid").status_code == 422
    assert file_hashes(tmp_path) == before


def test_api_errors_map_admission_and_storage_without_publishing(tmp_path, monkeypatch, mocked_analysis):
    http = client(tmp_path)
    bad = request()
    bad["spectrum"]["samples"] = True
    assert http.post("/api/v2/layered-acoustics/reports", json=bad).status_code == 422
    assert mocked_analysis.calls == []
    monkeypatch.setattr(layered_reports, "check_disk_space", lambda *_: (_ for _ in ()).throw(OSError("Insufficient")))
    assert http.post("/api/v2/layered-acoustics/reports", json=request()).status_code == 507
    assert LayeredReportStore(tmp_path).list() == []


def test_real_lossless_slab_report_and_column_api_are_standalone(tmp_path):
    http = client(tmp_path)
    column = {"twin": {"name": "Column fixture", "size_mm": [2, 2, 1],
        "objects": [{"id": "slab", "name": "Silicon", "shape": "box", "material": "silicon",
                     "center_mm": [1, 1, .5], "size_mm": [2, 2, .6]}]}, "x_mm": 1., "y_mm": 1.}
    extraction = http.post("/api/v2/layered-acoustics/column", json=column)
    assert extraction.status_code == 200, extraction.text
    result = http.post("/api/v2/layered-acoustics/reports", json=request(pulse=True))
    assert result.status_code == 201, result.text
    report = result.json()
    assert report["request_sha256"] == report["provenance"]["request_sha256"]
    spectrum = report["spectrum"]
    np.testing.assert_allclose(np.asarray(spectrum["reflectance"])+spectrum["transmittance"], 1, atol=1e-10, rtol=0)
    assert report["pulse"] is not None
    assert np.max(np.abs(np.asarray(report["pulse"]["rf"])-report["pulse"]["primary_rf"])) > 0
    assert not (tmp_path / "catalog.sqlite3").exists()
    assert LayeredReportStore(tmp_path).read(report["id"]) == report
