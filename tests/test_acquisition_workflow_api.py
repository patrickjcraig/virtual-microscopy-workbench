"""Real worker/API integration across immutable recipes, batches and comparisons."""
from copy import deepcopy
import hashlib
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import numpy as np

from virtual_microscopy.datasets import DatasetStore
from virtual_microscopy.server import app, _compute_lock


def body():
    return {"twin": {"name": "API comparison coupon", "size_mm": [4, 3, 1],
            "objects": [{"id": "si", "name": "Silicon", "shape": "box", "material": "silicon",
                         "center_mm": [2, 1.5, .5], "size_mm": [4, 3, .5]},
                        {"id": "cu", "name": "Thin copper", "shape": "box", "material": "copper",
                         "center_mm": [1.5, 1.5, .314], "size_mm": [1, 2, .007]}]},
            "acquisition": {"path_model": "continuous_columns_v1", "scan_nx": 16, "scan_ny": 16,
                            "depth_samples": 128, "frequency_mhz": 50, "sample_rate_mhz": 400,
                            "record_start_us": 0, "record_duration_us": .75, "focus_mm": .25}}


def wait_batch(client, identifier):
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        batch = client.get(f"/api/v2/batches/{identifier}").json()
        if batch["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            assert batch["status"] == "completed", batch
            return batch
        time.sleep(.05)
    raise AssertionError(f"Batch did not complete: {batch}")


def snapshot(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob("*") if p.is_file()}


def test_full_recipe_batch_comparison_and_reopen_without_reacquisition(tmp_path, monkeypatch):
    monkeypatch.setenv("VM_DATA_ROOT", str(tmp_path))
    with TestClient(app) as client:
        created = client.post("/api/v2/recipes", json={"name": "Focus sweep", "request": body(),
                              "default_gate": {"start_us": .2, "end_us": .5}})
        assert created.status_code == 201, created.text
        recipe = created.json()
        assert client.get(f"/api/v2/recipes/{recipe['recipe_id']}/export").json() == recipe
        assert client.post("/api/v2/recipes/import", json=recipe).json() == recipe
        assert len(client.get("/api/v2/recipes").json()["recipes"]) == 1
        proposal = {"recipe_id": recipe["recipe_id"], "field": "focus_mm", "values": [.2, .4]}
        preview = client.post("/api/v2/cases/preview", json=proposal)
        assert preview.status_code == 200, preview.text
        assert preview.json()["case_count"] == 2
        assert preview.json()["cases"][1]["differences"]["settings"] == [{"field": "focus_mm", "before": .2, "after": .4}]
        assert client.get("/api/v2/jobs").json()["jobs"] == []
        submission = proposal | {"idempotency_key": str(uuid4())}
        first = client.post("/api/v2/batches", json=submission)
        assert first.status_code == 202, first.text
        batch = wait_batch(client, first.json()["batch_id"])
        assert batch["completed_cases"] == 2
        with monkeypatch.context() as patch:
            def forbidden(*args, **kwargs):
                raise AssertionError("An idempotent replay rebuilt the acquisition plan")
            patch.setattr("virtual_microscopy.recipe_api.build_case_plan", forbidden)
            with _compute_lock:
                retry = client.post("/api/v2/batches", json=submission)
            assert retry.status_code == 202 and retry.json()["batch_id"] == batch["batch_id"]
        conflict = client.post("/api/v2/batches", json=submission | {"values": [.3, .4]})
        assert conflict.status_code == 422
        a, b = [case["dataset_id"] for case in batch["cases"]]
        assert client.post(f"/api/v2/jobs/{a}/resume", json={}).status_code == 422
        assert client.post(f"/api/v2/batches/{batch['batch_id']}/cancel", json={}).status_code == 422
        source_recipe = client.post("/api/v2/recipes/from-dataset", json={"name": "Source snapshot", "dataset_id": a})
        assert source_recipe.status_code == 201, source_recipe.text
        assert source_recipe.json()["request"] == batch["cases"][0]["request"]
        before = {identifier: snapshot(tmp_path / identifier) for identifier in (a, b)}
        config = {"reference_dataset_id": a, "candidate_dataset_id": b, "gate_start_us": .2, "gate_end_us": .5,
                  "x_index": 5, "y_index": 6}
        response = client.post("/api/v2/comparisons", json=config)
        assert response.status_code == 201, response.text
        report = response.json()
        assert report["metrics"]["rf"]["rmse"] > 0
        store = DatasetStore(tmp_path)
        ar, br = store.open_arrays(a)["rf"][:].astype(float), store.open_arrays(b)["rf"][:].astype(float)
        assert np.isclose(report["metrics"]["rf"]["rmse"], np.sqrt(np.mean((br-ar)**2)), rtol=1e-13)
        prefix = f"/api/v2/comparisons/{report['id']}"
        initial = client.get(prefix + "/view?x_index=5&y_index=6&time_index=120")
        assert initial.status_code == 200, initial.text
        assert client.get(prefix + "/export?format=json").json() == report
        assert "candidate" in client.get(prefix + "/export?format=csv").text.lower()
        second = client.post("/api/v2/comparisons", json=config | {"gate_start_us": .3, "gate_end_us": .45})
        assert second.status_code == 201 and second.json()["id"] != report["id"]
        assert second.json()["metrics"] == report["metrics"]
        assert client.get(prefix).json() == report
        assert len(client.get("/api/v2/jobs").json()["jobs"]) == 2
        assert {identifier: snapshot(tmp_path / identifier) for identifier in (a, b)} == before
    with TestClient(app) as reopened:
        assert reopened.get(f"/api/v2/batches/{batch['batch_id']}").json()["status"] == "completed"
        assert reopened.get(prefix).json() == report
        assert reopened.get(prefix + "/view?x_index=5&y_index=6&time_index=120").json() == initial.json()
        assert len(reopened.get("/api/v2/jobs").json()["jobs"]) == 2
        assert {identifier: snapshot(tmp_path / identifier) for identifier in (a, b)} == before


def test_invalid_case_publishes_no_jobs_and_keeps_recipe(tmp_path, monkeypatch):
    monkeypatch.setenv("VM_DATA_ROOT", str(tmp_path))
    with TestClient(app) as client:
        recipe = client.post("/api/v2/recipes", json={"name": "Fixed rate", "request": body()}).json()
        proposal = {"recipe_id": recipe["recipe_id"], "field": "frequency_mhz", "values": [50, 100]}
        for endpoint, content in (("/cases/preview", proposal), ("/batches", proposal | {"idempotency_key": str(uuid4())})):
            response = client.post("/api/v2" + endpoint, json=content)
            assert response.status_code == 422 and "Case 2" in response.text
        assert client.get("/api/v2/jobs").json()["jobs"] == []
        assert client.get("/api/v2/batches").json()["batches"] == []
        assert client.get(f"/api/v2/recipes/{recipe['recipe_id']}").json() == recipe


def test_unknown_source_and_strict_import_api_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("VM_DATA_ROOT", str(tmp_path))
    with TestClient(app) as client:
        assert client.post("/api/v2/recipes/from-dataset", json={"name": "Missing", "dataset_id": str(uuid4())}).status_code == 404
        recipe = client.post("/api/v2/recipes", json={"name": "Valid", "request": body()}).json()
        invalid = deepcopy(recipe)
        invalid["request"]["acquisition"]["focus_mm"] = .6
        assert client.post("/api/v2/recipes/import", json=invalid).status_code == 422
        assert client.get("/api/v2/recipes").json()["recipes"][0]["recipe_id"] == recipe["recipe_id"]
