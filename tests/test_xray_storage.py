"""X-ray view persistence, mixed queues and compatibility with saved SAM data."""
from copy import deepcopy
import sqlite3
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
import zarr

from virtual_microscopy.datasets import DatasetStore, array_sha256, solver_identity
from virtual_microscopy.volume_jobs import VolumeJobManager, _run_job, _update_job
from virtual_microscopy.xray_datasets import XrayDatasetStore
from virtual_microscopy.xray_schemas import XrayVolumeRequest
from virtual_microscopy.volume_schemas import SamVolumeRequest


def specimen():
    return {"schema_version": 1, "name": "Mixed microscopy coupon", "size_mm": [8, 6, 1.6],
            "objects": [{"id": "silicon", "name": "Silicon layer", "shape": "box", "material": "silicon",
                         "center_mm": [4, 3, 0.6], "size_mm": [8, 6, 0.8], "role": "structure"}]}


def xray_request(**changes):
    return XrayVolumeRequest.model_validate({"kind": "xray_projection_volume", "twin": specimen(),
        "acquisition": {"geometry_nx": 16, "geometry_ny": 16, "geometry_nz": 32,
                        "detector_rows": 16, "detector_cols": 16, "views": 3,
                        "noise": True, "seed": 415, "photons": 1000, "detector_fwhm_mm": 0, **changes}})


def sam_request():
    return SamVolumeRequest.model_validate({"twin": specimen(), "acquisition": {
        "scan_nx": 16, "scan_ny": 16, "depth_samples": 128, "focus_mm": 0.2, "record_duration_us": 0.6}})


def small_store(tmp_path, noise=True):
    store = XrayDatasetStore(tmp_path)
    identifier = str(uuid4())
    request = {"kind": "xray_projection_volume", "twin": specimen(), "acquisition": {"noise": noise, "photons": 1000}}
    estimate = {"kind": "xray_projection_volume", "model_version": "xray-storage-test",
                "shape": [3, 4, 5], "tile_rows": 1, "total_bytes": 2000}
    manifest = store.create(identifier, request, estimate)
    angles = np.array([0, 90, 180], dtype=np.float64)
    theta = np.deg2rad(angles)
    prepared = SimpleNamespace(
        angles_deg=angles, u_mm=np.linspace(-1, 1, 5), v_mm=np.linspace(-0.75, 0.75, 4),
        ray_direction_xyz=np.column_stack((np.sin(theta), np.zeros(3), np.cos(theta))),
        detector_u_xyz=np.column_stack((np.cos(theta), np.zeros(3), -np.sin(theta))),
        detector_v_xyz=np.tile([0., 1., 0.], (3, 1)), detector_center_mm=np.tile([4., 3., 0.8], (3, 1)),
        metadata={"synthetic": True, "zero_count_policy": "half-count log placeholder only at zero"})
    store.initialize_arrays(identifier, prepared)
    return store, identifier, prepared


def view_arrays(view=0, fractional=False):
    counts = (np.arange(20).reshape(1, 4, 5) + 990 + view).astype(np.float32)
    counts[0, 0, 0] = 0
    if fractional:
        counts = np.minimum(counts, 1000)
        counts[0, 0, 1] = 0.125
    return {"counts": counts, "transmission": (counts.astype(np.float64) / 1000).astype(np.float32),
            "line_integrals": (-np.log(np.where(counts > 0, counts, 0.5).astype(np.float64) / 1000)).astype(np.float32),
            "valid_mask": (counts > 0).astype(np.float32)}


def finish(store, identifier, fractional=False):
    for view in range(3):
        store.write_view(identifier, view, view + 1, view_arrays(view, fractional))
    return store.complete(identifier)


def insert_pending(manager, config, state="queued"):
    from virtual_microscopy.xray_volume import estimate_xray
    identifier = str(uuid4())
    store = XrayDatasetStore(manager.root)
    manifest = store.create(identifier, config.model_dump(mode="json", exclude_none=True), estimate_xray(config))
    with sqlite3.connect(manager.root / "catalog.sqlite3") as connection:
        connection.execute("""INSERT INTO jobs
            (job_id, state, name, created_at, updated_at, completed_rows, total_rows, error, kind)
            VALUES (?, ?, ?, ?, ?, 0, ?, NULL, ?)""",
            (identifier, state, config.twin.name, manifest["created_at"], manifest["created_at"],
             manifest["total_rows"], config.kind))
    return identifier


def wait_terminal(manager, identifier, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get_job(identifier)
        if job["state"] in {"completed", "failed", "interrupted", "cancelled"}:
            return job
        time.sleep(0.05)
    pytest.fail(f"Worker timed out: {manager.get_job(identifier)}")


def test_xray_storage_axes_units_and_coordinate_pose_identity(tmp_path):
    store, identifier, prepared = small_store(tmp_path)
    manifest = store.manifest(identifier)
    assert manifest["kind"] == "xray_projection_volume"
    assert manifest["axis_order"] == ["view", "v", "u"]
    assert manifest["arrays"]["counts"]["units"] == "observed photon counts per detector pixel"
    assert "only marks" in manifest["arrays"]["valid_mask"]["note"]
    assert set(manifest["solver"]["source_sha256"]) == {"xray_volume.py", "xray_schemas.py", "physics.py", "schemas.py", "hbm.py", "materials.py"}
    assert "sam_volume.py" in solver_identity("sam-test")["source_sha256"]
    assert "hbm.py" in solver_identity("sam-test")["source_sha256"]
    group = store.open_arrays(identifier)
    for name in store.signal_units:
        assert group[name].shape == (3, 4, 5)
        assert group[name].dtype == np.dtype("float32")
        assert group[name].metadata.dimension_names == ("view", "v", "u")
    for name in store.coordinate_names:
        assert group[name].dtype == np.dtype("float64")
        np.testing.assert_array_equal(group[name][:], getattr(prepared, name))


def test_fractional_expected_counts_keep_positive_sub_half_count_attenuation(tmp_path):
    store, identifier, _ = small_store(tmp_path, noise=False)
    finish(store, identifier, fractional=True)
    group = store.open_arrays(identifier)
    assert store.manifest(identifier)["arrays"]["counts"]["units"] == "expected photons per detector pixel"
    assert group["valid_mask"][0, 0, 1] == 1
    assert group["line_integrals"][0, 0, 1] == pytest.approx(-np.log(0.125 / 1000))
    assert group["counts"][0, 0, 0] == group["transmission"][0, 0, 0] == group["valid_mask"][0, 0, 0] == 0
    assert group["line_integrals"][0, 0, 0] == pytest.approx(-np.log(0.5 / 1000))
    # Above-flat Poisson samples remain valid and yield negative log values.
    noisy_store, noisy_id, _ = small_store(tmp_path / "noisy")
    finish(noisy_store, noisy_id)
    group = noisy_store.open_arrays(noisy_id)
    assert group["transmission"][0, 3, 4] > 1
    assert group["line_integrals"][0, 3, 4] < 0


@pytest.mark.parametrize("damage", ["negative_count", "fractional_poisson", "unrepresentable_count", "mask", "transmission", "log", "overflow"])
def test_projection_relationships_are_validated_before_writing(tmp_path, damage):
    store, identifier, _ = small_store(tmp_path)
    arrays = view_arrays()
    if damage == "negative_count":
        arrays["counts"][0, 0, 0] = -1
    elif damage == "fractional_poisson":
        arrays["counts"][0, 0, 0] = 0.5
    elif damage == "unrepresentable_count":
        arrays["counts"] = arrays["counts"].astype(np.float64)
        arrays["counts"][0, 0, 0] = 2 ** 24 + 1
    elif damage == "mask":
        arrays["valid_mask"][0, 0, 0] = 1
    elif damage == "transmission":
        arrays["transmission"][0, 0, 0] = 0.5
    elif damage == "overflow":
        arrays["transmission"] = arrays["transmission"].astype(np.float64)
        arrays["transmission"][0, 0, 0] = np.finfo(np.float64).max
    else:
        arrays["line_integrals"][0, 0, 0] = 0
    with pytest.raises(ValueError):
        store.write_view(identifier, 0, 1, arrays)
    assert store.manifest(identifier)["completed_rows"] == 0
    assert np.isnan(store.open_arrays(identifier)["counts"][:]).all()


def test_four_array_view_commit_is_atomic(tmp_path, monkeypatch):
    store, identifier, _ = small_store(tmp_path)
    save = store.save
    monkeypatch.setattr(store, "save", lambda *_: (_ for _ in ()).throw(OSError("commit interrupted")))
    with pytest.raises(OSError, match="interrupted"):
        store.write_view(identifier, 0, 1, view_arrays())
    assert store.manifest(identifier)["completed_chunks"] == {}
    monkeypatch.setattr(store, "save", save)
    assert store.verify_chunks(identifier)["completed_rows"] == 0
    manifest = store.write_view(identifier, 0, 1, view_arrays())
    assert set(manifest["completed_chunks"]["0"]) == {"rows", *[f"{name}_sha256" for name in store.signal_units]}


@pytest.mark.parametrize("damage", ["corrupt", "missing"])
def test_corrupted_projection_chunk_is_repaired_with_original_identity(tmp_path, damage):
    store, identifier, _ = small_store(tmp_path)
    for view in range(3):
        store.write_view(identifier, view, view + 1, view_arrays(view))
    original_input = store.manifest(identifier)["input_sha256"]
    if damage == "corrupt":
        store.open_arrays(identifier, "r+")["line_integrals"][1, 0, 0] = 123
    else:
        path = store.path(identifier) / "data.zarr" / "valid_mask" / "c" / "1" / "0" / "0"
        assert path.is_file()
        path.unlink()
    manifest = store.verify_chunks(identifier)
    assert manifest["completed_rows"] == 2
    assert set(manifest["completed_chunks"]) == {"0", "2"}
    store.write_view(identifier, 1, 2, view_arrays(1))
    store.complete(identifier)
    assert store.verify_complete(identifier)["input_sha256"] == original_input
    for name in store.signal_units:
        np.testing.assert_array_equal(store.open_arrays(identifier)[name][1:2], view_arrays(1)[name])


def test_pose_corruption_refuses_resume_and_export(tmp_path):
    store, identifier, _ = small_store(tmp_path / "partial")
    store.open_arrays(identifier, "r+")["detector_center_mm"][1, 0] += 0.01
    with pytest.raises(ValueError, match="Coordinate checksum"):
        store.verify_chunks(identifier)
    complete_store, complete_id, _ = small_store(tmp_path / "complete")
    finish(complete_store, complete_id)
    group = zarr.open_group(str(complete_store.path(complete_id) / "data.zarr"), mode="r+")
    group["detector_u_xyz"][0, 0] = 0.1
    with pytest.raises(ValueError, match="Coordinate checksum"):
        complete_store.verify_complete(complete_id)


def test_completed_xray_is_immutable_and_wrong_kind_reader_is_rejected(tmp_path):
    store, identifier, _ = small_store(tmp_path)
    finish(store, identifier)
    before = (store.path(identifier) / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="immutable"):
        store.write_view(identifier, 0, 1, view_arrays())
    with pytest.raises(ValueError, match="kind"):
        DatasetStore(tmp_path).verify_complete(identifier)
    assert before == (store.path(identifier) / "manifest.json").read_bytes()


def test_legacy_catalog_migration_preserves_completed_sam_bytes(tmp_path):
    from virtual_microscopy.sam_volume import estimate_sam, prepare_sam, iter_sam_tiles
    config = sam_request()
    identifier = str(uuid4())
    store = DatasetStore(tmp_path)
    manifest = store.create(identifier, config.model_dump(mode="json", exclude_none=True), estimate_sam(config))
    prepared = prepare_sam(config)
    store.initialize_arrays(identifier, prepared.x_mm, prepared.y_mm, prepared.time_us, prepared.metadata)
    for tile in iter_sam_tiles(prepared):
        store.write_tile(identifier, *tile)
    store.complete(identifier)
    original_manifest = (store.path(identifier) / "manifest.json").read_bytes()
    original_rf_hash = array_sha256(store.open_arrays(identifier)["rf"][:])
    with sqlite3.connect(tmp_path / "catalog.sqlite3") as connection:
        connection.execute("""CREATE TABLE jobs (job_id TEXT PRIMARY KEY, state TEXT NOT NULL,
            name TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            completed_rows INTEGER NOT NULL, total_rows INTEGER NOT NULL, error TEXT)""")
        connection.execute("INSERT INTO jobs VALUES (?, 'completed', ?, ?, ?, 16, 16, NULL)",
                           (identifier, config.twin.name, manifest["created_at"], manifest["created_at"]))
    manager = VolumeJobManager(tmp_path)
    job = manager.get_job(identifier)
    assert job["kind"] == "sam_rf_volume" and job["progress_unit"] == "rows"
    assert job["completed_units"] == job["total_units"] == 16
    assert isinstance(manager.get_store(identifier), DatasetStore)
    assert manager.get_store(identifier).verify_complete(identifier)["complete"]
    assert original_manifest == (store.path(identifier) / "manifest.json").read_bytes()
    assert original_rf_hash == array_sha256(store.open_arrays(identifier)["rf"][:])
    # Reopening an already migrated catalog is idempotent.
    assert VolumeJobManager(tmp_path).list_datasets()[0]["kind"] == "sam_rf_volume"


def test_xray_cancel_resume_is_seeded_byte_exact_after_damaged_view(tmp_path, monkeypatch):
    from virtual_microscopy.xray_volume import prepare_xray, iter_xray_views
    manager = VolumeJobManager(tmp_path)
    config = xray_request(views=4)
    identifier = insert_pending(manager, config, "running")
    original_write = XrayDatasetStore.write_view

    def stop_after_second(store, identifier, *args):
        manifest = original_write(store, identifier, *args)
        if manifest["completed_rows"] == 2:
            _update_job(tmp_path, identifier, "cancelling", manifest["completed_rows"])
        return manifest

    monkeypatch.setattr(XrayDatasetStore, "write_view", stop_after_second)
    _run_job(tmp_path, identifier, threading.Event())
    cancelled = manager.get_job(identifier)
    assert cancelled["state"] == "cancelled", cancelled
    assert cancelled["progress_unit"] == "views"
    assert cancelled["completed_units"] == 2 and cancelled["total_units"] == 4
    store = manager.get_store(identifier)
    store.open_arrays(identifier, "r+")["counts"][0, 0, 0] = 9999
    monkeypatch.setattr(XrayDatasetStore, "write_view", original_write)
    _update_job(tmp_path, identifier, "running", 2)
    _run_job(tmp_path, identifier, threading.Event())
    final = manager.get_job(identifier)
    assert final["state"] == "completed", final
    expected = list(iter_xray_views(prepare_xray(config)))
    for name in store.signal_units:
        np.testing.assert_array_equal(store.open_arrays(identifier)[name][:], np.concatenate([view[2][name] for view in expected]))
    store.verify_complete(identifier)


def test_spawned_single_worker_runs_mixed_sam_xray_queue(tmp_path):
    manager = VolumeJobManager(tmp_path)
    manager.start()
    try:
        sam = manager.submit(sam_request())
        xray = manager.submit(xray_request())
        assert sam["kind"] == "sam_rf_volume" and xray["kind"] == "xray_projection_volume"
        for job in (sam, xray):
            final = wait_terminal(manager, job["id"])
            assert final["state"] == "completed", final
            manager.get_store(job["id"]).verify_complete(job["id"])
        assert {dataset["kind"] for dataset in manager.list_datasets()} == {"sam_rf_volume", "xray_projection_volume"}
        assert manager.get_job(sam["id"])["progress_unit"] == "rows"
        assert manager.get_job(xray["id"])["progress_unit"] == "views"
    finally:
        manager.close()


def test_xray_identity_change_refuses_resume(tmp_path, monkeypatch):
    store, identifier, _ = small_store(tmp_path)
    monkeypatch.setattr("virtual_microscopy.xray_datasets.solver_identity", lambda *_: {"new": "solver"})
    with pytest.raises(ValueError, match="Solver"):
        store.validate_identity(identifier)
