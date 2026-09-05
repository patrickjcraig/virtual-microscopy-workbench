"""Real queued causal acquisition, float64 views, resume and historical guards."""
from contextlib import contextmanager
import hashlib
from io import BytesIO
import sqlite3
import threading
import time
import zipfile

from fastapi.testclient import TestClient
import numpy as np
import pytest
import zarr

from virtual_microscopy.server import app, _compute_lock
from virtual_microscopy.causal_processing import _section


def request():
    return {"kind": "sam_causal_rf_volume", "twin": {
        "schema_version": 1, "name": "Asymmetric causal coupon", "size_mm": [4, 3, .2],
        "objects": [{"id": "si", "name": "Silicon", "shape": "box", "material": "silicon",
                     "center_mm": [1, 1.5, .1], "size_mm": [2, 3, .2], "role": "structure"}]},
        "acquisition": {"scan_nx": 24, "scan_ny": 16, "record_duration_us": .25,
                        "sample_rate_mhz": 400, "center_frequency_mhz": 50}}


def wait(client, identifier):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = client.get(f"/api/v2/jobs/{identifier}").json()
        if job["status"] in ("completed", "failed", "cancelled", "interrupted"):
            return job
        time.sleep(.04)
    raise AssertionError(f"Causal job did not finish: {job}")


def snapshot(path):
    return {str(file.relative_to(path)): hashlib.sha256(file.read_bytes()).hexdigest()
            for file in path.rglob("*") if file.is_file()}


def test_unpooled_signed_sections_retain_exact_time_samples():
    t = .1 + np.arange(801)/400
    values = np.arange(2403, dtype=np.float64).reshape(3, 801)/13 - 80
    result = _section(values, t, [1, 2], "x", "signed pressure")
    assert np.array_equal(result["image"], values.T)
    assert np.array_equal(result["time_us"], t)
    assert result["rf_samples_per_time_bin"] == 1
    assert len(result["time_bin_edges_us"]) == 802
    assert np.array_equal(result["time_bin_edges_us"][1:-1], (t[:-1]+t[1:])/2)


def test_real_causal_job_typed_views_export_guards_and_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("VM_DATA_ROOT", str(tmp_path))
    with TestClient(app) as client:
        body = request()
        estimate = client.post("/api/v2/estimate", json=body)
        assert estimate.status_code == 200, estimate.text
        assert estimate.json()["shape"] == [16, 24, 101]
        with _compute_lock:
            assert client.post("/api/v2/jobs", json=body).status_code == 409
        submitted = client.post("/api/v2/jobs", json=body)
        assert submitted.status_code == 202, submitted.text
        identifier = submitted.json()["id"]
        job = wait(client, identifier)
        assert job["status"] == "completed", job.get("error") or job
        prefix = f"/api/v2/datasets/{identifier}"
        view_path = f"/api/v2/causal-datasets/{identifier}/view"
        manifest = client.get(prefix).json()
        assert manifest["kind"] == body["kind"] and manifest["dtype"] == "float64"
        assert manifest["total_error_bound"] <= manifest["request"]["acquisition"]["absolute_tolerance"]
        group = zarr.open_group(str(tmp_path/identifier/"data.zarr"), mode="r")
        before = snapshot(tmp_path/identifier)
        for product in ("rf", "imaginary", "envelope"):
            response = client.get(view_path, params={"x_index": 4, "y_index": 3, "time_index": 27,
                                                   "product": product, "gate_start_us": .05, "gate_end_us": .15})
            assert response.status_code == 200, response.text
            view = response.json()
            raw = group[product][:]
            assert raw.dtype == np.dtype("float64")
            assert np.array_equal(view["xy"]["image"], raw[:, :, 27])
            assert np.array_equal(view["xt"]["image"], raw[3].T)
            assert np.array_equal(view["yt"]["image"], raw[:, 4].T)
            assert np.array_equal(view["ascan"][product], raw[3, 4])
            assert view["cursor"][product] == raw[3, 4, 27]
            assert view["ascan"]["error_bound"] == group["error_bound"][3, 4]
            assert np.array_equal(view["cscan"]["image"], group["envelope"][:, :, 20:61].max(axis=2))
        rms = client.get(view_path, params={"gate_mode": "rms_rf", "gate_start_us": .05, "gate_end_us": .15}).json()
        assert np.array_equal(rms["cscan"]["image"], np.sqrt(np.mean(group["rf"][:, :, 20:61]**2, axis=2)))
        assert client.get(view_path+"?x_index=24").status_code == 422
        assert client.get(view_path+"?gate_start_us=2&gate_end_us=3").status_code == 422
        assert client.get(view_path+"?product=counts").status_code == 422
        assert client.get(prefix+"/view").status_code == 422
        assert client.post("/api/v2/estimate", json={"kind": "sam_depth_volume", "source_dataset_id": identifier}).status_code == 422
        assert client.post("/api/v2/recipes/from-dataset", json={"dataset_id": identifier, "name": "Unsupported"}).status_code == 422
        assert client.post("/api/v2/recipes", json={"name": "Unsupported", "request": body}).status_code == 422
        assert client.post("/api/v2/comparisons", json={"reference_dataset_id": identifier, "candidate_dataset_id": identifier}).status_code == 422
        archive = client.get(prefix+"/export")
        assert archive.status_code == 200, archive.text
        with zipfile.ZipFile(BytesIO(archive.content)) as zipped:
            assert zipped.testzip() is None
            assert {name: hashlib.sha256(zipped.read(name)).hexdigest() for name in zipped.namelist()} == {
                key.replace("\\", "/"): value for key, value in before.items()}
        assert snapshot(tmp_path/identifier) == before
        assert len(client.get("/api/v2/jobs").json()["jobs"]) == 1
    def forbidden(*args, **kwargs):
        raise AssertionError("Historical causal data invoked current numerical code")
    with monkeypatch.context() as patch:
        patch.setattr("virtual_microscopy.causal_sam.prepare_causal_sam", forbidden)
        patch.setattr("virtual_microscopy.layered_time.causal_gamma_response", forbidden)
        patch.setattr("virtual_microscopy.layered_time.estimate_causal_gamma", forbidden)
        with TestClient(app) as client:
            reopened = client.get(view_path, params={"x_index": 4, "y_index": 3, "time_index": 27})
            assert reopened.status_code == 200, reopened.text
            assert reopened.json()["ascan"] == view["ascan"]
            assert client.get(prefix+"/export").status_code == 200
            assert snapshot(tmp_path/identifier) == before
            assert client.post(f"/api/v2/jobs/{identifier}/resume").status_code == 422


@pytest.mark.parametrize("change", [{"focus_mm": .1}, {"depth_samples": 128}, {"observation_model": "focused"},
                                     {"scan_nx": 65}, {"precision_bits": True}, {"sample_rate_mhz": 100}])
def test_invalid_causal_acquisition_creates_no_jobs(monkeypatch, tmp_path, change):
    monkeypatch.setenv("VM_DATA_ROOT", str(tmp_path))
    with TestClient(app) as client:
        body = request()
        body["acquisition"].update(change)
        assert client.post("/api/v2/estimate", json=body).status_code == 422
        assert client.post("/api/v2/jobs", json=body).status_code == 422
        assert client.get("/api/v2/jobs").json()["jobs"] == []


def test_worker_resume_uses_committed_classes_and_exact_float64_disk_budget(tmp_path, monkeypatch):
    from virtual_microscopy.causal_datasets import CausalSamDatasetStore
    from virtual_microscopy.volume_jobs import VolumeJobManager, _run_job, _update_job
    manager = VolumeJobManager(tmp_path)
    monkeypatch.setattr(manager, "_ensure_worker", lambda: None)
    identifier = manager.submit(request())["id"]
    original = CausalSamDatasetStore.write_row
    checks = []
    monkeypatch.setattr("virtual_microscopy.volume_jobs.check_disk_space", lambda root, count: checks.append(count))
    def cancel_after_row(store, identifier, *args):
        manifest = original(store, identifier, *args)
        _update_job(tmp_path, identifier, "cancelling", manifest["completed_rows"])
        return manifest
    monkeypatch.setattr(CausalSamDatasetStore, "write_row", cancel_after_row)
    _run_job(tmp_path, identifier, threading.Event())
    job = manager.get_job(identifier)
    assert job["status"] == "cancelled", job.get("error") or job
    assert job["completed_rows"] == 1
    store = manager.get_store(identifier)
    group = store.open_arrays(identifier)
    first = {key: group[key][0:1] for key in ("rf", "imaginary", "envelope", "error_bound")}
    certificate = manager.get_manifest(identifier)["class_certificates"]
    monkeypatch.setattr(CausalSamDatasetStore, "write_row", original)
    def no_new_classes(*args, **kwargs):
        raise AssertionError("Resume recomputed a verified committed class")
    monkeypatch.setattr("virtual_microscopy.causal_sam.causal_gamma_response", no_new_classes)
    manager.resume(identifier)
    _run_job(tmp_path, identifier, threading.Event())
    job = manager.get_job(identifier)
    assert job["status"] == "completed", job.get("error") or job
    assert manager.get_manifest(identifier)["class_certificates"] == certificate
    for key, row in first.items():
        assert np.array_equal(store.open_arrays(identifier)[key][:], np.repeat(row, 16, axis=0))
    assert len(checks) == 16 and set(checks) == {24*(3*101*8+8)}
    before = snapshot(tmp_path/identifier)
    manager._recover()
    assert manager.get_job(identifier)["status"] == "completed"
    assert snapshot(tmp_path/identifier) == before


@pytest.mark.parametrize("phase", ["before_insert", "after_commit"])
def test_catalog_publication_failure_does_not_make_an_unpublished_causal_job_runnable(tmp_path, monkeypatch, phase):
    from virtual_microscopy import volume_jobs
    manager = volume_jobs.VolumeJobManager(tmp_path)
    monkeypatch.setattr(manager, "_ensure_worker", lambda: None)
    original = volume_jobs._connection
    fired = False
    class Proxy:
        inserted = False
        def __init__(self, connection): self.connection = connection
        def execute(self, sql, *args):
            nonlocal fired
            if sql.lstrip().startswith("INSERT INTO jobs"):
                self.inserted = True
                if phase == "before_insert" and not fired:
                    fired = True
                    raise sqlite3.OperationalError("Injected publication failure")
            return self.connection.execute(sql, *args)
        def commit(self):
            nonlocal fired
            self.connection.commit()
            if phase == "after_commit" and self.inserted and not fired:
                fired = True
                raise sqlite3.OperationalError("Injected post-commit response failure")
    @contextmanager
    def connection(root):
        with original(root) as db:
            yield Proxy(db)
    monkeypatch.setattr(volume_jobs, "_connection", connection)
    if phase == "before_insert":
        with pytest.raises(sqlite3.OperationalError, match="Injected"):
            manager.submit(request())
        assert manager.list_jobs() == []
        assert volume_jobs._claim_job(tmp_path) is None
        manifests = list(tmp_path.glob("*/manifest.json"))
        assert len(manifests) == 1
        import json
        assert json.loads(manifests[0].read_text())["state"] == "failed"
    else:
        submitted = manager.submit(request())
        assert len(manager.list_jobs()) == 1
        assert volume_jobs._claim_job(tmp_path) == submitted["id"]
    assert fired
