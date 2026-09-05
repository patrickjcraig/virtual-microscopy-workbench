"""Causal gamma instrument admission, immutable provenance and old report reads."""
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

from virtual_microscopy.comparisons import _json_workspace
from virtual_microscopy.datasets import canonical_json, json_sha256
from virtual_microscopy.layered_analysis import analyze_layered, estimate_layered, extract_layered_column
from virtual_microscopy.layered_reports import LayeredReportStore, layered_report_csv
from virtual_microscopy.layered_schemas import CausalGammaPulseSettings, LayeredAnalysisRequest


def request():
    water = {"name": "Water", "impedance_mrayl": 1.48, "sound_speed_m_s": 1480}
    return {"name": "Causal slab", "stack": {"incident": water, "terminal": water,
        "layers": [{"name": "Assumed silicon", "thickness_mm": .1, "impedance_mrayl": 19.63347,
                    "sound_speed_m_s": 8430, "pressure_loss_db_mm": 0, "material_id": "silicon"}]},
        "spectrum": {"start_mhz": 0, "end_mhz": 150, "samples": 33},
        "causal_pulse": {"record_duration_us": .5, "record_start_us": .1, "surface_standoff_mm": .148}}


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def client(root):
    from virtual_microscopy.layered_api import router
    app = FastAPI()
    app.state.volume_jobs = SimpleNamespace(root=root)
    app.include_router(router)
    return TestClient(app)


def test_causal_defaults_and_mutually_exclusive_pulse_choices():
    defaults = CausalGammaPulseSettings().model_dump(mode="json")
    assert defaults == {"center_frequency_mhz": 50, "fractional_bandwidth": .5,
        "sample_rate_mhz": 400, "record_start_us": 0, "record_duration_us": 2,
        "surface_standoff_mm": 0, "absolute_tolerance": 1e-7, "gamma_order": 12, "precision_bits": 128}
    proposal = request()
    proposal["pulse"] = {}
    with pytest.raises(ValueError, match="either"):
        LayeredAnalysisRequest.model_validate(proposal)


@pytest.mark.parametrize("changes", [
    {"gamma_order": True}, {"gamma_order": 12.0}, {"gamma_order": 3}, {"gamma_order": 25},
    {"precision_bits": 128.0}, {"precision_bits": "128"}, {"precision_bits": 80},
    {"center_frequency_mhz": True}, {"fractional_bandwidth": "0.5"},
    {"sample_rate_mhz": 399}, {"record_start_us": 12},
    {"record_duration_us": 6}, {"absolute_tolerance": 0},
    {"surface_standoff_mm": float("nan")}, {"unbounded_period_us": 1},
])
def test_strict_causal_admission(changes):
    with pytest.raises(ValueError):
        CausalGammaPulseSettings.model_validate(changes)


@pytest.mark.parametrize("layers", [[], [0], [.05, .1]])
def test_causal_accepts_empty_zero_thickness_and_multiple_finite_layers(layers):
    proposal = request()
    layer = proposal["stack"]["layers"][0]
    proposal["stack"]["layers"] = [layer | {"thickness_mm": thickness} for thickness in layers]
    report = analyze_layered(proposal)
    assert report["pulse"] is None and report["causal_pulse"] is not None
    assert report["resources"]["layer_count"] == len(layers)
    if not layers or layers == [0]:
        assert not np.any(report["causal_pulse"]["rf"])


def test_causal_settings_and_actual_float_time_centers_reach_both_preflight_and_solver(monkeypatch):
    from virtual_microscopy import layered_time
    proposal = request()
    proposal["causal_pulse"].update(record_start_us=.10000000000000002, record_duration_us=.137,
                                   sample_rate_mhz=800, precision_bits=96)
    settings = LayeredAnalysisRequest.model_validate(proposal).causal_pulse.model_dump(mode="json")
    expected_time = settings["record_start_us"]+np.arange(110)/800
    seen = []
    original_estimate, original_response = layered_time.estimate_causal_gamma, layered_time.causal_gamma_response

    def estimate(stack, time, options):
        seen.append(("estimate", time, options))
        return original_estimate(stack, time, options)

    def response(stack, time, options):
        seen.append(("response", time, options))
        return original_response(stack, time, options)

    monkeypatch.setattr(layered_time, "estimate_causal_gamma", estimate)
    monkeypatch.setattr(layered_time, "causal_gamma_response", response)
    report = analyze_layered(proposal)
    assert [kind for kind, *_ in seen] == ["estimate", "response"]
    assert all(options == settings and np.array_equal(time, expected_time) for _, time, options in seen)
    assert np.array_equal(report["causal_pulse"]["time_us"], expected_time)
    assert report["resources"]["time_samples"] == 110


def test_combined_workspace_rejects_before_either_synthesis_kernel(tmp_path, monkeypatch):
    from virtual_microscopy import layered_time
    from virtual_microscopy import layered_acoustics

    def forbidden(*args, **kwargs):
        pytest.fail("Synthesis ran before combined report/workspace admission")

    monkeypatch.setattr(layered_time, "estimate_causal_gamma", lambda *_: {
        "inverse_work_units": 100, "estimated_peak_bytes": 256*1024**2})
    monkeypatch.setattr(layered_time, "causal_gamma_response", forbidden)
    monkeypatch.setattr(layered_acoustics, "layered_response", forbidden)
    with pytest.raises(ValueError, match="256 MiB"):
        LayeredReportStore(tmp_path).create(request())
    assert not (tmp_path / "layered-reports").exists()


def test_real_causal_report_freezes_certificate_dependencies_and_output_without_jobs(tmp_path):
    import flint
    proposal = request()
    store = LayeredReportStore(tmp_path)
    estimate = estimate_layered(proposal)
    report = store.create(proposal)
    causal = report["causal_pulse"]
    assert report["pulse"] is None and set(causal) == {"time_us", "rf", "imaginary", "envelope", "diagnostics"}
    assert causal["diagnostics"]["total_error_bound"] <= report["request"]["causal_pulse"]["absolute_tolerance"]
    assert all(len(causal[name]) == estimate["time_samples"] for name in ("time_us", "rf", "imaginary", "envelope"))
    assert estimate["causal_pulse"]["inverse_work_units"] == estimate["estimated_rf_work_units"]
    assert estimate["impulse_echo_count"] == 0
    assert estimate["causal_pulse"]["time_samples"] == estimate["time_samples"]
    assert report["provenance"]["numerical_packages"]["python-flint"] == "0.9.0"
    assert report["provenance"]["numerical_packages"]["flint"] == flint.__FLINT_VERSION__
    assert report["provenance"]["numerical_packages"]["flint_release"] == flint.__FLINT_RELEASE__
    assert report["provenance"]["equation_sources"][-3:] == ["https://dlmf.nist.gov/5.9.E1",
        "https://www.columbia.edu/~ww2040/IEOR3106F06/ExtraCreditLectureLT.pdf", "https://flintlib.org/doc/using.html"]
    source = Path(__file__).parents[1]/"virtual_microscopy/layered_time.py"
    assert report["provenance"]["implementation_sha256"]["layered_time.py"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert report["request_sha256"] == report["provenance"]["request_sha256"]
    assert report["processing_version"] == "layered-analysis-0.12.0"
    payload = canonical_json(report)
    assert len(payload) <= estimate["estimated_report_bytes"]
    assert _json_workspace(payload) <= estimate["estimated_report_expanded_bytes"]
    assert store.list()[0]["causal_pulse_available"] is True
    before = hashes(tmp_path)
    saved = deepcopy(report)
    proposal["causal_pulse"]["record_duration_us"] = .7
    report["causal_pulse"]["rf"][0] = 99
    assert store.read(saved["id"]) == saved and hashes(tmp_path) == before
    assert not (tmp_path / "catalog.sqlite3").exists()


def test_v011_report_with_no_causal_fields_reopens_and_exports_without_current_solvers(tmp_path, monkeypatch):
    from virtual_microscopy import layered_time, layered_analysis, layered_reports
    identifier = str(uuid4())
    old_request = request()
    del old_request["causal_pulse"]
    old_request.update(pulse=None, source_column=None)
    historical = {"schema_version": 1, "kind": "layered_acoustic_report", "id": identifier,
        "report_id": identifier, "created_at": "2026-09-05T00:00:00+00:00",
        "request": old_request, "request_sha256": json_sha256(old_request),
        "processing_version": "layered-analysis-0.11.0", "pulse": None,
        "spectrum": {"frequency_mhz": [0, 150], "reflection": {"real": [0, .2], "imag": [0, -.3]}},
        "provenance": {"implementation_sha256": {"layered_acoustics.py": "0"*64}},
        "estimate": {"layer_count": 1, "frequency_samples": 2}, "source_status": "manual_assumptions"}
    historical["report_sha256"] = json_sha256(historical)
    directory = tmp_path/"layered-reports"
    directory.mkdir()
    (directory/f"{identifier}.json").write_bytes(canonical_json(historical))

    def forbidden(*args, **kwargs):
        pytest.fail("Historical report consulted the current numerical implementation")

    for module, name in ((layered_analysis, "analyze_layered"), (layered_analysis, "estimate_layered"),
                         (layered_time, "estimate_causal_gamma"), (layered_time, "causal_gamma_response"),
                         (layered_reports, "_source_fingerprints")):
        monkeypatch.setattr(module, name, forbidden)
    before = hashes(tmp_path)
    store = LayeredReportStore(tmp_path)
    assert store.read(identifier) == historical
    assert "causal_pulse_available" not in store.list()[0]
    http = client(tmp_path)
    assert http.get(f"/api/v2/layered-acoustics/reports/{identifier}/export?format=json").json() == historical
    decoded = {row["field"]: json.loads(row["value_json"]) for row in csv.DictReader(io.StringIO(layered_report_csv(store.read(identifier))))}
    assert decoded == historical and hashes(tmp_path) == before


def test_hbm_source_causal_api_retains_full_column_and_six_sites(tmp_path):
    twin = json.loads((Path(__file__).parents[1]/"examples/nvidia-h100-hbm6-microstructure.json").read_text("utf-8"))
    feature = next(p for p in twin["objects"] if p.get("assembly_id") == "hbm-6" and p.get("layer_role") == "microbump")
    source = extract_layered_column({"twin": twin, "x_mm": feature["center_mm"][0], "y_mm": feature["center_mm"][1]})
    proposal = request() | {"name": "HBM6 causal column", "stack": source["stack"], "source_column": source["source_column"]}
    http = client(tmp_path)
    response = http.post("/api/v2/layered-acoustics/reports", json=proposal)
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["source_column"] == source and report["source_status"] == "matches_extracted_column"
    assert len(report["source_column"]["source_column"]["twin"]["hbm_assemblies"]) == 6
    assert report["source_column"]["segments"][-1]["z_end_mm"] == twin["size_mm"][2]
    assert report["stack_differences"] == [] and report["pulse"] is None
    assert report["causal_pulse"]["diagnostics"]["total_error_bound"] <= 1e-7
    assert not (tmp_path/"catalog.sqlite3").exists()
    before = hashes(tmp_path)
    route = f"/api/v2/layered-acoustics/reports/{report['id']}"
    assert http.get(route).json() == report
    assert http.get(route+"/export?format=json").json() == report
    csv.field_size_limit(16*1024**2)
    rows = csv.DictReader(io.StringIO(http.get(route+"/export?format=csv").text))
    assert {row["field"]: json.loads(row["value_json"]) for row in rows} == report
    assert hashes(tmp_path) == before


def test_legacy_gaussian_mode_does_not_record_causal_dependency_version(tmp_path):
    proposal = request()
    del proposal["causal_pulse"]
    proposal["pulse"] = {"record_duration_us": .5}
    report = LayeredReportStore(tmp_path).create(proposal)
    assert report["pulse"] is not None and report["causal_pulse"] is None
    assert "python-flint" not in report["provenance"]["numerical_packages"]
    assert "flint" not in report["provenance"]["numerical_packages"]
    assert "https://dlmf.nist.gov/5.9.E1" not in report["provenance"]["equation_sources"]
