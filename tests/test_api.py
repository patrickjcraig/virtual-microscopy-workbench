"""Public input boundaries and reproducible end-to-end run contracts."""
from copy import deepcopy
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from virtual_microscopy.server import app, _compute_lock

client = TestClient(app)


def specimen():
    return {"schema_version": 1, "name": "Analytical silicon coupon", "description": "Synthetic", "size_mm": [4, 4, 1], "objects": [{"id": "slab", "name": "Silicon slab", "shape": "box", "material": "silicon", "center_mm": [2,2,0.5], "size_mm": [4,4,1], "role": "structure"}]}


def request():
    return {"twin": specimen(), "settings": {"resolution": 64, "noise": False}}


def test_examples_are_valid_and_have_named_truth():
    assert client.get("/api/health").json()["status"] == "ok"
    examples = client.get("/api/examples").json()
    assert {item["id"] for item in examples} == {"flip-chip-bga", "power-die", "nvidia-h100-sxm", "nvidia-h100-hbm6-microstructure"}
    for ex in examples:
        response = client.post("/api/validate", json=ex["twin"])
        assert response.status_code == 200, response.text
        assert any(obj["role"] == "defect" for obj in ex["twin"]["objects"])


@pytest.mark.parametrize("change", [
    lambda t: t.update(size_mm=[-1,4,1]),
    lambda t: t["objects"][0].update(center_mm=[0,0,0]),
    lambda t: t["objects"][0].update(material="unidentified"),
    lambda t: t["objects"].append(deepcopy(t["objects"][0])),
    lambda t: t.update(schema_version=2),
    lambda t: t["objects"][0].update(shape="sphere", size_mm=[4,3,1]),
])
def test_bad_import_is_rejected_with_readable_error(change):
    twin = specimen()
    change(twin)
    response = client.post("/api/validate", json=twin)
    assert response.status_code == 422
    assert response.json()["detail"][0]["msg"]


@pytest.mark.parametrize("settings", [
    {"gate_start_us": 1, "gate_end_us": .5},
    {"frequency_mhz": 0},
    {"energy_kev": 20},
    {"resolution": 5000},
    {"probe_x_mm": 5},
    {"focus_mm": 2},
])
def test_invalid_acquisition_is_rejected(settings):
    body = request()
    body["settings"].update(settings)
    assert client.post("/api/simulate", json=body).status_code == 422


def test_concurrent_compute_returns_actionable_conflict():
    with _compute_lock:
        response = client.post("/api/simulate", json=request())
    assert response.status_code == 409
    assert "already running" in response.json()["detail"]


def test_real_simulation_exports_inputs_and_reproduces_data():
    body = request()
    responses = [client.post("/api/simulate", json=body) for _ in range(2)]
    assert all(r.status_code == 200 for r in responses), [r.text[:500] for r in responses]
    first, second = [r.json() for r in responses]
    assert first["run_id"] != second["run_id"]
    assert first["input_sha256"] == second["input_sha256"]
    assert first["twin"] == body["twin"]
    assert first["settings"]["seed"] == 42
    for modality in ("xray", "sam"):
        image = np.asarray(first[modality]["image"])
        assert image.shape == (64,64)
        assert np.isfinite(image).all()
        np.testing.assert_array_equal(image, second[modality]["image"])
    assert np.min(first["xray"]["image"]) > 0
    assert np.max(first["xray"]["image"]) < 1
    assert "Synthetic" in first["evidence_status"]
    probe = client.post("/api/probe", json=body)
    assert probe.status_code == 200
    np.testing.assert_allclose(probe.json()["ascan"]["amplitude"], first["ascan"]["amplitude"], atol=1e-7)


def test_materials_and_machine_readable_schema():
    response = client.get("/api/materials")
    assert response.status_code == 200
    materials = response.json()
    assert {"copper", "silicon", "solder", "epoxy", "fr4", "air"} <= {m["id"] for m in materials}
    assert "properties" in client.get("/api/twin-schema").json()


def test_large_specimen_probe_and_reference_roundtrip():
    example = next(item for item in client.get("/api/examples").json() if item["id"] == "nvidia-h100-sxm")
    twin = example["twin"]
    response = client.post("/api/validate", json=twin)
    assert response.status_code == 200, response.text
    assert response.json()["twin"]["reference"] == twin["reference"]
    settings = {**twin["recommended_settings"], "probe_x_mm": 45, "probe_y_mm": 35, "resolution": 64}
    response = client.post("/api/probe", json={"twin": twin, "settings": settings})
    assert response.status_code == 200, response.text
    assert response.json()["ascan"]["probe_mm"] == [45, 35]


@pytest.mark.parametrize("change", [
    lambda t: t["reference"]["published_facts"][0].update(source_ids=["missing-source"]),
    lambda t: t["reference"]["sources"][0].update(url="javascript:alert(1)"),
    lambda t: t["recommended_settings"].update(probe_x_mm=61),
    lambda t: t.update(size_mm=[101,60,2.65]),
])
def test_bad_reference_or_package_preset_is_rejected(change):
    twin = next(item["twin"] for item in client.get("/api/examples").json() if item["id"] == "nvidia-h100-sxm")
    change(twin)
    assert client.post("/api/validate", json=twin).status_code == 422
