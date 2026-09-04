"""Real process/API integration: frozen acquisition, restart, and preview coexistence."""
import time

from fastapi.testclient import TestClient

from virtual_microscopy.server import app, _compute_lock


def body():
    return {"twin": {"schema_version": 1, "name": "API volume coupon", "size_mm": [4, 3, 1],
        "objects": [{"id": "si", "name": "Silicon", "shape": "box", "material": "silicon",
            "center_mm": [2, 1.5, .5], "size_mm": [4, 3, .5], "role": "structure"}]},
        "acquisition": {"scan_nx": 24, "scan_ny": 16, "depth_samples": 128,
                        "record_duration_us": .75, "focus_mm": .25}}


def await_job(client, job_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        job = client.get(f"/api/v2/jobs/{job_id}").json()
        if job["status"] in ("completed", "failed", "cancelled", "interrupted"):
            return job
        time.sleep(.05)
    raise AssertionError(f"Volume job did not finish: {job}")


def test_actual_api_acquire_gate_export_and_reopen(monkeypatch, tmp_path):
    monkeypatch.setenv("VM_DATA_ROOT", str(tmp_path))
    with TestClient(app) as client:
        estimate = client.post("/api/v2/estimate", json=body())
        assert estimate.status_code == 200, estimate.text
        assert estimate.json()["shape"] == [16, 24, 301]
        assert estimate.json()["free_disk_bytes"] > estimate.json()["required_disk_bytes"]
        with _compute_lock:
            assert client.post("/api/v2/jobs", json=body()).status_code == 409
        response = client.post("/api/v2/jobs", json=body())
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        assert "status" in response.json()
        job = await_job(client, job_id)
        assert job["status"] == "completed", job
        assert job["progress"] == 1
        prefix = f"/api/v2/datasets/{job_id}"
        first = client.get(prefix + "/view?x_index=3&y_index=4&time_index=150")
        assert first.status_code == 200, first.text
        second = client.get(prefix + "/view?x_index=3&y_index=4&time_index=150&gate_start_us=.3&gate_end_us=.5&gate_mode=rms_rf")
        assert second.status_code == 200, second.text
        assert first.json()["ascan"] == second.json()["ascan"]
        assert len(client.get("/api/v2/jobs").json()["jobs"]) == 1
        assert client.get(prefix + "/export").status_code == 200
        # A completed catalog must not break legacy previews (status alias).
        preview = client.post("/api/simulate", json={"twin": body()["twin"], "settings": {
            "resolution": 64, "noise": False, "probe_x_mm": 2, "probe_y_mm": 1.5}})
        assert preview.status_code == 200, preview.text
    with TestClient(app) as reopened:
        datasets = reopened.get("/api/v2/datasets").json()["datasets"]
        assert datasets[0]["dataset_id"] == job_id
        assert datasets[0]["complete"]
        result = reopened.get(prefix + "/view?x_index=3&y_index=4&time_index=150")
        assert result.json()["ascan"] == first.json()["ascan"]
