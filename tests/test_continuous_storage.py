"""Actual continuous SAM durability: bounded cancellation, repair and API round trips."""
from copy import deepcopy
import hashlib
import io
import json
import sqlite3
import threading
import time
from uuid import uuid4
import zipfile

from fastapi.testclient import TestClient
import numpy as np
import pytest

from virtual_microscopy.datasets import DatasetStore, atomic_json, json_sha256
from virtual_microscopy.sam_volume import estimate_sam, prepare_sam, iter_sam_tiles
from virtual_microscopy.server import app
from virtual_microscopy.volume_jobs import VolumeJobManager, _run_job, _update_job, _read_job
from virtual_microscopy.volume_schemas import SamVolumeRequest


CONTINUOUS = "continuous_columns_v1"


def request(depth_samples=128):
    return SamVolumeRequest.model_validate({
        "twin": {"name": "Continuous storage / assumed off-grid film", "size_mm": [4, 3, 1.2],
                 "objects": [
                     {"id": "silicon", "name": "Silicon body", "shape": "box", "material": "silicon",
                      "center_mm": [2, 1.5, .5], "size_mm": [3.8, 2.8, .8]},
                     {"id": "film", "name": "Off-grid copper film", "shape": "box", "material": "copper",
                      "center_mm": [2, 1.5, .22385], "size_mm": [3.6, 2.6, .0071]},
                     {"id": "void", "name": "Asymmetric assumed air cavity", "shape": "sphere",
                      "material": "air", "role": "defect", "center_mm": [1.2, 1.1, .45],
                      "size_mm": [.3, .3, .3]}]},
        "acquisition": {"path_model": CONTINUOUS, "scan_nx": 16, "scan_ny": 24,
                        "depth_samples": depth_samples, "frequency_mhz": 50,
                        "sample_rate_mhz": 400, "focus_mm": .4, "record_duration_us": .75,
                        "roi_mm": [.6, .3, 3.4, 2.4]},
    })


def fresh_arrays(config):
    prepared = prepare_sam(config)
    tiles = list(iter_sam_tiles(prepared))
    return {"rf": np.concatenate([item[2] for item in tiles]),
            "envelope": np.concatenate([item[3] for item in tiles]),
            "x_mm": prepared.x_mm.copy(), "y_mm": prepared.y_mm.copy(),
            "time_us": prepared.time_us.copy()}


def insert_running(manager, config):
    identifier = str(uuid4())
    manifest = manager.store.create(identifier, config.model_dump(mode="json", exclude_none=True), estimate_sam(config))
    with sqlite3.connect(manager.root / "catalog.sqlite3") as connection:
        connection.execute("""INSERT INTO jobs
            (job_id,state,name,created_at,updated_at,completed_rows,total_rows,error,kind)
            VALUES (?, 'running', ?, ?, ?, 0, ?, NULL, 'sam_rf_volume')""",
                           (identifier, config.twin.name, manifest["created_at"], manifest["created_at"],
                            manifest["total_rows"]))
    return identifier


def hashes(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}


def assert_complete(manager, identifier, expected):
    job = _read_job(manager.root, identifier)
    assert job["status"] == "completed", job
    manifest = manager.store.verify_complete(identifier)
    assert manifest["metadata"]["path_model"] == CONTINUOUS
    assert manifest["metadata"]["depth_samples_used"] is False
    assert manifest["estimate"]["model_version"] == "sam-continuous-columns-0.8.0"
    for name, array in expected.items():
        np.testing.assert_array_equal(manager.store.open_arrays(identifier)[name][:], array)
    return manifest


def run_cancelled_after_tiles(manager, identifier, monkeypatch, count=1):
    original = DatasetStore.write_tile
    calls = []

    def commit_then_cancel(store, current, *tile):
        manifest = original(store, current, *tile)
        calls.append(tile[0])
        if len(calls) == count:
            _update_job(manager.root, current, "cancelling", manifest["completed_rows"])
        return manifest

    with monkeypatch.context() as patch:
        patch.setattr(DatasetStore, "write_tile", commit_then_cancel)
        _run_job(manager.root, identifier, threading.Event())
    job = _read_job(manager.root, identifier)
    assert job["status"] == "cancelled", job
    assert 0 < job["completed_rows"] < job["total_rows"]
    assert len(calls) == count
    return manager.store.manifest(identifier)


def test_cancellation_during_real_prepare_then_resume_exactly(tmp_path, monkeypatch):
    manager, config = VolumeJobManager(tmp_path), request()
    expected = fresh_arrays(config)
    identifier = insert_running(manager, config)
    original = prepare_sam

    def cancel_during_prepare(frozen):
        _update_job(tmp_path, identifier, "cancelling", 0)
        return original(frozen)

    with monkeypatch.context() as patch:
        patch.setattr("virtual_microscopy.sam_volume.prepare_sam", cancel_during_prepare)
        _run_job(tmp_path, identifier, threading.Event())
    assert _read_job(tmp_path, identifier)["status"] == "cancelled"
    assert manager.store.manifest(identifier)["completed_chunks"] == {}
    assert np.isnan(manager.store.open_arrays(identifier)["rf"][:]).all()
    _update_job(tmp_path, identifier, "running", 0)
    _run_job(tmp_path, identifier, threading.Event())
    assert_complete(manager, identifier, expected)


def test_cancel_after_committed_tile_preserves_frozen_recipe_and_good_bytes(tmp_path, monkeypatch):
    manager, config = VolumeJobManager(tmp_path), request()
    expected = fresh_arrays(config)
    identifier = insert_running(manager, config)
    frozen = manager.store.manifest(identifier)
    config.twin.name = "Caller changed name after dispatch"
    config.acquisition.path_model = "voxel_centers_v1"
    partial = run_cancelled_after_tiles(manager, identifier, monkeypatch)
    chunks = {name: (manager.store.path(identifier) / "data.zarr" / name / "c" / "0" / "0" / "0").read_bytes()
              for name in ("rf", "envelope")}
    _update_job(tmp_path, identifier, "running", partial["completed_rows"])
    _run_job(tmp_path, identifier, threading.Event())
    result = assert_complete(manager, identifier, expected)
    for key in ("request", "request_sha256", "materials", "materials_sha256", "solver", "input_sha256", "estimate"):
        assert result[key] == frozen[key]
    assert result["request_sha256"] == json_sha256(result["request"])
    for name, data in chunks.items():
        assert (manager.store.path(identifier) / "data.zarr" / name / "c" / "0" / "0" / "0").read_bytes() == data


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_real_continuous_resume_repairs_only_bad_or_uncommitted_tiles(tmp_path, monkeypatch, damage):
    manager, config = VolumeJobManager(tmp_path), request()
    expected = fresh_arrays(config)
    identifier = insert_running(manager, config)
    partial = run_cancelled_after_tiles(manager, identifier, monkeypatch, count=2)
    assert partial["tile_rows"] == 8  # The small fixture has three canonical tiles.
    data_path = manager.store.path(identifier) / "data.zarr"
    good_paths = [data_path / name / "c" / "1" / "0" / "0" for name in ("rf", "envelope")]
    good_bytes = [path.read_bytes() for path in good_paths]
    if damage == "missing":
        (data_path / "rf" / "c" / "0" / "0" / "0").unlink()
    else:
        manager.store.open_arrays(identifier, "r+")["rf"][0, 0, 0] = 99
    written, original = [], DatasetStore.write_tile

    def observe_write(store, current, *tile):
        written.append(tile[0])
        return original(store, current, *tile)

    with monkeypatch.context() as patch:
        patch.setattr(DatasetStore, "write_tile", observe_write)
        _update_job(tmp_path, identifier, "running", partial["completed_rows"])
        _run_job(tmp_path, identifier, threading.Event())
    assert written == [0, 16], "The undamaged second committed tile must not be written again."
    assert [path.read_bytes() for path in good_paths] == good_bytes
    assert_complete(manager, identifier, expected)


@pytest.mark.parametrize("change", ["source", "mode", "estimate"])
def test_resume_rejects_changed_continuous_identity_before_preparing_or_writing(tmp_path, monkeypatch, change):
    manager, config = VolumeJobManager(tmp_path), request()
    identifier = insert_running(manager, config)
    manifest = run_cancelled_after_tiles(manager, identifier, monkeypatch)
    data_path = manager.store.path(identifier) / "data.zarr"
    before = hashes(data_path)
    if change == "source":
        from virtual_microscopy import datasets
        original_identity = datasets.solver_identity

        def changed_source(*args, **kwargs):
            identity = deepcopy(original_identity(*args, **kwargs))
            identity["source_sha256"]["column_paths.py"] = "0" * 64
            return identity

        monkeypatch.setattr(datasets, "solver_identity", changed_source)
    else:
        edited = deepcopy(manifest)
        if change == "mode":
            edited["request"]["acquisition"]["path_model"] = "voxel_centers_v1"
        else:
            edited["estimate"]["path_event_work_units"] += 1
        atomic_json(manager.store.path(identifier) / "manifest.json", edited)
    prepared = []
    monkeypatch.setattr("virtual_microscopy.sam_volume.prepare_sam", lambda *_: prepared.append(True))
    _update_job(tmp_path, identifier, "running", manifest["completed_rows"])
    _run_job(tmp_path, identifier, threading.Event())
    job = _read_job(tmp_path, identifier)
    assert job["status"] == "failed", job
    assert any(word in job["error"].lower() for word in ("checksum", "changed", "identity", "estimate")), job
    assert prepared == []
    assert hashes(data_path) == before


def await_job(client, identifier):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = client.get(f"/api/v2/jobs/{identifier}").json()
        if job["status"] in ("completed", "failed", "cancelled", "interrupted"):
            assert job["status"] == "completed", job
            return job
        time.sleep(.05)
    raise AssertionError(f"Continuous job did not finish: {job}")


def test_continuous_api_inactive_z_identity_gate_archive_and_reopen(tmp_path, monkeypatch):
    monkeypatch.setenv("VM_DATA_ROOT", str(tmp_path))
    identifiers, manifests = [], []
    store = DatasetStore(tmp_path)
    with TestClient(app) as client:
        for depth in (128, 1024):
            body = request(depth).model_dump(mode="json", exclude_none=True)
            estimate = client.post("/api/v2/estimate", json=body)
            assert estimate.status_code == 200, estimate.text
            assert estimate.json()["shape"] == [24, 16, 301]
            assert estimate.json()["depth_samples_used"] is False
            response = client.post("/api/v2/jobs", json=body)
            assert response.status_code == 202, response.text
            identifier = response.json()["id"]
            identifiers.append(identifier)
            await_job(client, identifier)
            manifest = store.verify_complete(identifier)
            manifests.append(manifest)
            assert manifest["request"]["acquisition"]["depth_samples"] == depth
            assert manifest["request"]["acquisition"]["path_model"] == CONTINUOUS
            archive = client.get(f"/api/v2/datasets/{identifier}/export")
            assert archive.status_code == 200, archive.text
            with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
                exported = json.loads(zipped.read(next(name for name in zipped.namelist() if name.endswith("manifest.json"))))
                assert exported["request"] == manifest["request"]
                assert exported["solver"] == manifest["solver"]
        assert manifests[0]["request_sha256"] != manifests[1]["request_sha256"]
        assert manifests[0]["input_sha256"] != manifests[1]["input_sha256"]
        assert manifests[0]["estimate"] == manifests[1]["estimate"]
        first, second = [store.open_arrays(identifier) for identifier in identifiers]
        for name in ("rf", "envelope", "x_mm", "y_mm", "time_us"):
            np.testing.assert_array_equal(first[name][:], second[name][:])
        assert first["rf"].metadata.dimension_names == ("y", "x", "time")
        assert first["rf"].dtype == np.dtype("float32")
        assert first["x_mm"].dtype == np.dtype("float64")
        assert np.any(first["rf"][:] < 0) and np.any(first["rf"][:] > 0)
        prefix = f"/api/v2/datasets/{identifiers[0]}"
        before = hashes(store.path(identifiers[0]))
        query = "/view?x_index=3&y_index=4&time_index=100"
        peak = client.get(prefix + query)
        rms = client.get(prefix + query + "&gate_start_us=.2&gate_end_us=.5&gate_mode=rms_rf")
        assert peak.status_code == rms.status_code == 200
        assert peak.json()["ascan"] == rms.json()["ascan"]
        assert peak.json()["gate"] != rms.json()["gate"]
        assert len(client.get("/api/v2/jobs").json()["jobs"]) == 2
    with TestClient(app) as reopened:
        result = reopened.get(prefix + query)
        assert result.status_code == 200, result.text
        assert result.json()["ascan"] == peak.json()["ascan"]
        saved = reopened.get(prefix).json()
        assert saved["path_model"] == CONTINUOUS
        assert saved["request"]["acquisition"]["depth_samples"] == 128
        assert reopened.get(prefix + "/export").status_code == 200
        assert len(reopened.get("/api/v2/jobs").json()["jobs"]) == 2
    assert hashes(store.path(identifiers[0])) == before
