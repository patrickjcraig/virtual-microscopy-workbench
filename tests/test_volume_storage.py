"""Durability and process lifecycle checks for synthetic SAM datasets."""
from copy import deepcopy
from pathlib import Path
import sqlite3
import threading
import time
from uuid import uuid4

import numpy as np
import pytest
import zarr

from virtual_microscopy.datasets import (DatasetStore, array_sha256, check_disk_space,
                                         atomic_json, dataset_path, json_sha256, material_snapshot)
from virtual_microscopy.sam_volume import estimate_sam, prepare_sam, iter_sam_tiles
from virtual_microscopy.volume_jobs import VolumeJobManager, _run_job, _update_job
from virtual_microscopy.volume_schemas import SamVolumeRequest


def request():
    return SamVolumeRequest.model_validate({
        "twin": {"schema_version": 1, "name": "Storage coupon", "size_mm": [8, 6, 1.6],
                 "objects": [{"id": "silicon", "name": "Silicon layer", "shape": "box", "material": "silicon",
                              "center_mm": [4, 3, 0.6], "size_mm": [8, 6, 0.8], "role": "structure"}]},
        "acquisition": {"scan_nx": 16, "scan_ny": 16, "depth_samples": 128,
                        "focus_mm": 0.2, "record_duration_us": 0.6},
    })


def tiny_store(tmp_path):
    store = DatasetStore(tmp_path)
    identifier = str(uuid4())
    config = {"twin": {"name": "Frozen test"}, "acquisition": {"scan_nx": 3}}
    estimate = {"shape": [4, 3, 5], "tile_rows": 2, "total_bytes": 600, "model_version": "storage-test"}
    store.create(identifier, config, estimate)
    store.initialize_arrays(identifier, [1.125, 1.375, 1.625], [2.125, 2.375, 2.625, 2.875],
                            0.111111111 + np.arange(5) / 400, {"synthetic": True})
    return store, identifier, config


def tile(y0=0):
    rf = np.arange(30, dtype=np.float32).reshape(2, 3, 5) / 7 - 1 + y0
    return rf, abs(rf)


def insert_pending(manager, state="queued"):
    config = request()
    identifier = str(uuid4())
    manifest = manager.store.create(identifier, config.model_dump(mode="json", exclude_none=True), estimate_sam(config))
    with sqlite3.connect(manager.root / "catalog.sqlite3") as connection:
        connection.execute("INSERT INTO jobs VALUES (?, ?, ?, ?, ?, 0, ?, NULL)",
                           (identifier, state, config.twin.name, manifest["created_at"],
                            manifest["created_at"], manifest["total_rows"]))
    return identifier, config


def test_frozen_snapshot_and_array_coordinate_identity(tmp_path):
    store, identifier, config = tiny_store(tmp_path)
    config["twin"]["name"] = "Changed caller input"
    manifest = store.manifest(identifier)
    assert manifest["request"]["twin"]["name"] == "Frozen test"
    assert manifest["request_sha256"] == json_sha256(manifest["request"])
    assert manifest["materials_sha256"] == json_sha256(material_snapshot())
    group = store.open_arrays(identifier)
    assert group["rf"].shape == (4, 3, 5)
    assert group["rf"].dtype == np.dtype("float32")
    assert group["time_us"].dtype == np.dtype("float64")
    assert group["rf"].metadata.dimension_names == ("y", "x", "time")
    assert np.isnan(group["rf"][:]).all()
    assert group["time_us"][0] == 0.111111111


def test_create_refuses_collision_and_frozen_metadata_edits(tmp_path):
    store, identifier, config = tiny_store(tmp_path)
    before = (store.path(identifier) / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        store.create(identifier, config, store.manifest(identifier)["estimate"])
    modified = deepcopy(store.manifest(identifier))
    modified["request"]["twin"]["name"] = "Changed"
    with pytest.raises(ValueError, match="Frozen"):
        store.save(identifier, modified)
    assert before == (store.path(identifier) / "manifest.json").read_bytes()


@pytest.mark.parametrize("identifier", ["../outside", "C:/outside", "", "123", str(uuid4()).upper()])
def test_ids_reject_path_escape_and_noncanonical_names(tmp_path, identifier):
    with pytest.raises(ValueError, match="UUID"):
        dataset_path(tmp_path, identifier)


def test_real_symlink_escape_is_rejected_when_supported(tmp_path):
    root = tmp_path / "datasets"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    identifier = str(uuid4())
    try:
        (root / identifier).symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks requires permission on this host.")
    with pytest.raises(ValueError, match="leaves"):
        dataset_path(root, identifier)


def test_tile_commit_is_atomic_across_arrays_and_manifest(tmp_path, monkeypatch):
    store, identifier, _ = tiny_store(tmp_path)
    original_save = store.save
    monkeypatch.setattr(store, "save", lambda *_: (_ for _ in ()).throw(OSError("simulated manifest failure")))
    with pytest.raises(OSError, match="manifest failure"):
        store.write_tile(identifier, 0, 2, *tile())
    assert store.manifest(identifier)["completed_rows"] == 0
    monkeypatch.setattr(store, "save", original_save)
    assert store.verify_chunks(identifier)["completed_rows"] == 0
    manifest = store.write_tile(identifier, 0, 2, *tile())
    assert manifest["completed_rows"] == 2
    assert manifest["completed_chunks"]["0"]["rf_sha256"] == array_sha256(tile()[0])


@pytest.mark.parametrize("damage", ["corrupt", "missing"])
def test_resume_drops_damaged_chunks_and_regenerates_exact_bytes(tmp_path, damage):
    store, identifier, _ = tiny_store(tmp_path)
    store.write_tile(identifier, 0, 2, *tile())
    store.write_tile(identifier, 2, 4, *tile(2))
    if damage == "corrupt":
        store.open_arrays(identifier, "r+")["rf"][0, 0, 0] = 99
    else:
        chunk_path = store.path(identifier) / "data.zarr" / "rf" / "c" / "0" / "0" / "0"
        assert chunk_path.is_file()
        chunk_path.unlink()
    manifest = store.verify_chunks(identifier)
    assert manifest["completed_rows"] == 2
    assert list(manifest["completed_chunks"]) == ["2"]
    store.write_tile(identifier, 0, 2, *tile())
    store.complete(identifier)
    np.testing.assert_array_equal(store.open_arrays(identifier)["rf"][:2], tile()[0])
    assert store.verify_complete(identifier)["complete"]


def test_completed_dataset_is_immutable_and_export_detects_corruption(tmp_path):
    store, identifier, _ = tiny_store(tmp_path)
    with pytest.raises(ValueError, match="missing rows"):
        store.complete(identifier)
    store.write_tile(identifier, 0, 2, *tile())
    store.write_tile(identifier, 2, 4, *tile(2))
    completed = store.complete(identifier)
    before = (store.path(identifier) / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="immutable"):
        store.write_tile(identifier, 0, 2, *tile())
    with pytest.raises(ValueError, match="immutable"):
        store.save(identifier, completed)
    with pytest.raises(ValueError, match="immutable"):
        store.open_arrays(identifier, "r+")
    assert before == (store.path(identifier) / "manifest.json").read_bytes()
    # Simulate external disk corruption, beyond the application's write API.
    group = zarr.open_group(str(store.path(identifier) / "data.zarr"), mode="r+")
    group["envelope"][2, 0, 0] = 123
    with pytest.raises(ValueError, match="checksum mismatch"):
        store.verify_complete(identifier)


def test_coordinate_corruption_and_material_change_refuse_resume(tmp_path, monkeypatch):
    store, identifier, _ = tiny_store(tmp_path)
    store.open_arrays(identifier, "r+")["x_mm"][0] = 100
    with pytest.raises(ValueError, match="Coordinate checksum"):
        store.verify_chunks(identifier)
    monkeypatch.setattr("virtual_microscopy.datasets.material_snapshot", lambda: {"changed": True})
    with pytest.raises(ValueError, match="Material library changed"):
        store.validate_identity(identifier)


@pytest.mark.parametrize("damage", ["solver_identity", "array_shape", "coordinate_shape", "coordinate_registry"])
def test_export_rejects_metadata_corruption_without_requiring_current_solver(tmp_path, monkeypatch, damage):
    store, identifier, _ = tiny_store(tmp_path)
    store.write_tile(identifier, 0, 2, *tile())
    store.write_tile(identifier, 2, 4, *tile(2))
    store.complete(identifier)
    monkeypatch.setattr("virtual_microscopy.datasets.solver_identity", lambda _: {"future": "solver"})
    assert store.verify_complete(identifier)["complete"]  # old complete data stays exportable
    manifest = store.manifest(identifier)
    if damage == "solver_identity":
        manifest["solver"]["model_version"] = "incorrectly changed provenance"
        atomic_json(store.path(identifier) / "manifest.json", manifest)
    elif damage == "coordinate_registry":
        manifest["coordinates_sha256"].pop("x_mm")
        atomic_json(store.path(identifier) / "manifest.json", manifest)
    else:
        group = zarr.open_group(str(store.path(identifier) / "data.zarr"), mode="r+")
        if damage == "array_shape":
            group["rf"].resize((5, 3, 5))
        else:
            group["x_mm"].resize((4,))
    with pytest.raises(ValueError, match="mismatch|Invalid|incomplete"):
        store.verify_complete(identifier)


def test_disk_budget_preflight_rejects_before_dataset_creation(tmp_path, monkeypatch):
    from collections import namedtuple
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr("virtual_microscopy.datasets.shutil.disk_usage", lambda _: usage(100, 99, 1))
    with pytest.raises(OSError, match="Insufficient free disk"):
        check_disk_space(tmp_path, 1000)
    store = DatasetStore(tmp_path)
    identifier = str(uuid4())
    with pytest.raises(OSError):
        store.create(identifier, {}, {"shape": [4, 3, 5], "tile_rows": 2, "total_bytes": 600})
    assert not store.path(identifier).exists()


def test_cancellation_preserves_commits_and_resume_matches_fresh_solver(tmp_path, monkeypatch):
    manager = VolumeJobManager(tmp_path)
    identifier, config = insert_pending(manager, "running")
    original_write = DatasetStore.write_tile

    def cancel_after_first(store, identifier, *args):
        manifest = original_write(store, identifier, *args)
        _update_job(tmp_path, identifier, "cancelling", manifest["completed_rows"])
        return manifest

    monkeypatch.setattr(DatasetStore, "write_tile", cancel_after_first)
    _run_job(tmp_path, identifier, threading.Event())
    cancelled = manager.get_job(identifier)
    assert cancelled["state"] == "cancelled"
    assert cancelled["completed_rows"] == estimate_sam(config)["tile_rows"]
    assert not manager.get_manifest(identifier)["complete"]
    monkeypatch.setattr(DatasetStore, "write_tile", original_write)
    _update_job(tmp_path, identifier, "running", cancelled["completed_rows"])
    _run_job(tmp_path, identifier, threading.Event())
    assert manager.get_job(identifier)["state"] == "completed"
    expected = list(iter_sam_tiles(prepare_sam(config)))
    group = manager.store.open_arrays(identifier)
    np.testing.assert_array_equal(group["rf"][:], np.concatenate([t[2] for t in expected]))
    np.testing.assert_array_equal(group["envelope"][:], np.concatenate([t[3] for t in expected]))


def test_cancellation_during_prepare_is_not_lost_by_progress_update(tmp_path, monkeypatch):
    manager = VolumeJobManager(tmp_path)
    identifier, config = insert_pending(manager, "running")
    original_prepare = prepare_sam

    def cancel_during_prepare(config):
        _update_job(tmp_path, identifier, "cancelling", 0)
        return original_prepare(config)

    monkeypatch.setattr("virtual_microscopy.sam_volume.prepare_sam", cancel_during_prepare)
    _run_job(tmp_path, identifier, threading.Event())
    assert manager.get_job(identifier)["state"] == "cancelled"
    assert manager.get_manifest(identifier)["completed_rows"] == 0


def test_restart_recovers_interrupted_and_respects_completed_manifest(tmp_path):
    original = VolumeJobManager(tmp_path)
    identifier, config = insert_pending(original, "running")
    prepared = prepare_sam(config)
    original.store.initialize_arrays(identifier, prepared.x_mm, prepared.y_mm, prepared.time_us, prepared.metadata)
    first = next(iter_sam_tiles(prepared))
    original.store.write_tile(identifier, *first)
    recovered = VolumeJobManager(tmp_path)
    recovered._recover()
    assert recovered.get_job(identifier)["state"] == "interrupted"
    assert recovered.get_job(identifier)["completed_rows"] == first[1]
    _update_job(tmp_path, identifier, "running", first[1])
    _run_job(tmp_path, identifier, threading.Event())
    _update_job(tmp_path, identifier, "running", first[1])  # crash before final SQLite write
    recovered._recover()
    assert recovered.get_job(identifier)["state"] == "completed"
    assert recovered.get_job(identifier)["progress"] == 1


def wait_terminal(manager, identifier, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get_job(identifier)
        if job["state"] in {"completed", "failed", "interrupted", "cancelled"}:
            return job
        time.sleep(0.05)
    pytest.fail(f"Worker timed out: {manager.get_job(identifier)}")


def test_spawned_worker_completes_and_owner_lock_blocks_second_server(tmp_path):
    manager = VolumeJobManager(tmp_path)
    second = VolumeJobManager(tmp_path)
    manager.start()
    try:
        with pytest.raises(RuntimeError, match="owns"):
            second.start()
        job = manager.submit(request())
        assert job["job_id"] == job["dataset_id"] == job["id"]
        final = wait_terminal(manager, job["id"])
        assert final["state"] == "completed", final
        assert final["completed_rows"] == 16
        assert manager.store.verify_complete(job["id"])["complete"]
        with pytest.raises(ValueError, match="immutable"):
            manager.cancel(job["id"])
        with pytest.raises(ValueError, match="Only"):
            manager.resume(job["id"])
    finally:
        manager.close()
        second.close()
    assert manager._process is None
    second.start()
    second.close()


def test_worker_stop_event_retains_interrupted_dataset(tmp_path):
    manager = VolumeJobManager(tmp_path)
    identifier, _ = insert_pending(manager, "running")
    stop = threading.Event()
    stop.set()
    _run_job(tmp_path, identifier, stop)
    assert manager.get_job(identifier)["state"] == "interrupted"
    assert manager.get_manifest(identifier)["complete"] is False


def test_queued_cancel_resume_and_worker_replacement(tmp_path):
    manager = VolumeJobManager(tmp_path)
    identifier, _ = insert_pending(manager, "cancelled")
    manager.start()
    try:
        manager._process.terminate()
        manager._process.join(timeout=5)
        # A read detects the dead worker and replaces it before resume.
        assert manager.get_job(identifier)["state"] == "cancelled"
        assert manager._process.is_alive()
        manager.resume(identifier)
        final = wait_terminal(manager, identifier)
        assert final["state"] == "completed", final
    finally:
        manager.close()
