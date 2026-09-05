"""Real acquisition API through recipe, batch, policy review and immutable report."""
from uuid import uuid4

from fastapi.testclient import TestClient
import numpy as np

from virtual_microscopy.server import app, _compute_lock
from virtual_microscopy.xray_datasets import XrayDatasetStore
from test_acquisition_workflow_api import snapshot, wait_batch
from test_xray_recipes import request


def test_xray_recipe_batch_normalization_reopen_and_saved_source_preservation(tmp_path, monkeypatch):
    monkeypatch.setenv("VM_DATA_ROOT", str(tmp_path))
    with TestClient(app) as client:
        response = client.post("/api/v2/recipes", json={"name": "Photon pair", "request": request(noise=True)})
        assert response.status_code == 201, response.text
        recipe = response.json()
        assert recipe["kind"] == "xray_acquisition_recipe"
        proposal = {"recipe_id": recipe["recipe_id"], "field": "photons", "values": [1000, 8000]}
        preview = client.post("/api/v2/cases/preview", json=proposal)
        assert preview.status_code == 200, preview.text
        assert preview.json()["kind"] == "xray_projection_volume"
        assert client.get("/api/v2/jobs").json()["jobs"] == []
        submission = proposal | {"idempotency_key": str(uuid4())}
        response = client.post("/api/v2/batches", json=submission)
        assert response.status_code == 202, response.text
        batch = wait_batch(client, response.json()["batch_id"])
        assert batch["kind"] == "xray_projection_volume"
        assert all(c["progress_unit"] == "views" for c in batch["cases"])
        with monkeypatch.context() as patch:
            def forbidden(*args, **kwargs):
                raise AssertionError("Replay or saved processing attempted to construct an acquisition")
            patch.setattr("virtual_microscopy.recipe_api.build_case_plan", forbidden)
            with _compute_lock:
                retry = client.post("/api/v2/batches", json=submission)
            assert retry.status_code == 202 and retry.json()["batch_id"] == batch["batch_id"]
        a, b = [c["dataset_id"] for c in batch["cases"]]
        before = {i: snapshot(tmp_path/i) for i in (a, b)}
        config = {"reference_dataset_id": a, "candidate_dataset_id": b, "product": "transmission",
                  "view_index": 1, "detector_row": 4, "detector_col": 5}
        response = client.post("/api/v2/xray-comparisons", json=config)
        assert response.status_code == 422 and "photons" in response.text
        with monkeypatch.context() as patch:
            patch.setattr("virtual_microscopy.xray_volume.prepare_xray", forbidden)
            patch.setattr("virtual_microscopy.xray_volume.iter_xray_views", forbidden)
            source = client.post("/api/v2/recipes/from-dataset", json={"dataset_id": a, "name": "Frozen X-ray"})
            assert source.status_code == 201, source.text
            assert source.json()["request"] == batch["cases"][0]["request"]
            response = client.post("/api/v2/xray-comparisons", json=config | {"normalization": "per_source_incident"})
            assert response.status_code == 201, response.text
            report = response.json()
            store = XrayDatasetStore(tmp_path)
            ar = store.open_arrays(a)["transmission"][:].astype(float)
            br = store.open_arrays(b)["transmission"][:].astype(float)
            assert np.isclose(report["metrics"]["rmse"], np.sqrt(np.mean((br-ar)**2)), rtol=1e-13)
            prefix = f"/api/v2/xray-comparisons/{report['id']}"
            view = client.get(prefix + "/view?view_index=2&detector_row=3&detector_col=6")
            assert view.status_code == 200, view.text
            assert client.get(prefix+"/export?format=json").json() == report
            assert "candidate" in client.get(prefix+"/export?format=csv").text.lower()
            assert len(client.get("/api/v2/jobs").json()["jobs"]) == 2
        assert {i: snapshot(tmp_path/i) for i in (a, b)} == before
    with TestClient(app) as reopened:
        assert reopened.get(prefix).json() == report
        assert reopened.get(prefix+"/view?view_index=2&detector_row=3&detector_col=6").json() == view.json()
        assert reopened.get(f"/api/v2/batches/{batch['batch_id']}").json()["status"] == "completed"
        assert len(reopened.get("/api/v2/jobs").json()["jobs"]) == 2
        assert {i: snapshot(tmp_path/i) for i in (a, b)} == before


def test_xray_wrong_case_and_gate_are_rejected_without_work(tmp_path, monkeypatch):
    monkeypatch.setenv("VM_DATA_ROOT", str(tmp_path))
    with TestClient(app) as client:
        invalid = client.post("/api/v2/recipes", json={"name": "Wrong gate", "request": request(),
            "default_gate": {"start_us": .1, "end_us": .3}})
        assert invalid.status_code == 422
        recipe = client.post("/api/v2/recipes", json={"name": "Valid", "request": request()}).json()
        proposal = {"recipe_id": recipe["recipe_id"], "field": "frequency_mhz", "values": [50, 100]}
        assert client.post("/api/v2/cases/preview", json=proposal).status_code == 422
        assert client.post("/api/v2/batches", json=proposal | {"idempotency_key": str(uuid4())}).status_code == 422
        assert client.get("/api/v2/jobs").json()["jobs"] == []
