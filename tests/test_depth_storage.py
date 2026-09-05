"""Frozen SAM depth provenance, support semantics and durable derived jobs."""
from copy import deepcopy
import json
import shutil
import sqlite3
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
import zarr

from virtual_microscopy.datasets import DatasetStore, atomic_json, canonical_json, json_sha256
from virtual_microscopy.depth_datasets import (CACHE_OWNER, CACHE_PREFIX, DepthDatasetStore,
                                               cleanup_depth_caches, depth_source)
from virtual_microscopy.depth_schemas import SamDepthRequest
from virtual_microscopy.volume_jobs import VolumeJobManager, _run_job, _update_job


@pytest.fixture(scope="module")
def source_template(tmp_path_factory):
    from virtual_microscopy.sam_volume import estimate_sam, prepare_sam, iter_sam_tiles
    from virtual_microscopy.volume_schemas import SamVolumeRequest
    root = tmp_path_factory.mktemp("depth-source")
    config = SamVolumeRequest.model_validate({"twin": {"schema_version": 1, "name": "Raw SAM depth coupon",
        "size_mm": [8, 6, 1.6], "objects": [{"id": "silicon", "name": "Silicon layer", "shape": "box",
            "material": "silicon", "center_mm": [4, 3, 0.6], "size_mm": [8, 6, .8], "role": "structure"}]},
        "acquisition": {"scan_nx": 24, "scan_ny": 16, "depth_samples": 128,
            "roi_mm": [2, 3, 5, 5], "focus_mm": .2, "frequency_mhz": 10, "sample_rate_mhz": 80,
            "record_start_us": .2, "record_duration_us": .5}})
    identifier = str(uuid4())
    store = DatasetStore(root)
    store.create(identifier, config.model_dump(mode="json", exclude_none=True), estimate_sam(config))
    prepared = prepare_sam(config)
    store.initialize_arrays(identifier, prepared.x_mm, prepared.y_mm, prepared.time_us, prepared.metadata)
    for part in iter_sam_tiles(prepared):
        store.write_tile(identifier, *part)
    store.complete(identifier)
    return root / identifier


def copy_source(root, source_template):
    root.mkdir(parents=True, exist_ok=True)
    target = root / source_template.name
    shutil.copytree(source_template, target)
    return target.name


def request(source_id):
    return SamDepthRequest.model_validate({"kind": "sam_depth_volume", "source_dataset_id": source_id,
        "mapping": {"nz": 16, "z_min_mm": 0, "z_max_mm": 1.6, "velocity_model": "homogeneous",
                    "sound_speed_m_s": 3000, "surface_reference": "explicit", "surface_time_us": 0}})


def small_store(root, source_template):
    source_id = copy_source(root, source_template)
    source_group = DatasetStore(root).open_arrays(source_id)
    interval = [float(source_group["time_us"][0]), float(source_group["time_us"][-1])]
    store = DepthDatasetStore(root)
    identifier = str(uuid4())
    config = {"kind": "sam_depth_volume", "source_dataset_id": source_id, "mapping": {}}
    estimate = {"kind": "sam_depth_volume", "model_version": "sam-depth-storage-test", "shape": [3, 16, 24],
                "tile_rows": 1, "total_bytes": 3*16*24*12, "estimated_temporary_bytes": 3*16*24*8,
                "source_time_range_us": interval, "time_support_tolerance_us": 1e-12}
    store.create(identifier, config, estimate)
    metadata = {"model_depth_valid": [True, True, False], "source_time_range_us": interval,
                "time_support_tolerance_us": 1e-12, "synthetic_test_fixture": True}
    prepared = SimpleNamespace(x_mm=source_group["x_mm"][:], y_mm=source_group["y_mm"][:],
                               z_mm=np.array([.5, 1.5, 2.5]), travel_time_us=np.array([.3, .8, 0.]), metadata=metadata)
    store.initialize_arrays(identifier, prepared)
    return store, identifier, source_id


def slice_arrays(index=0):
    rf = np.linspace(-.5, .5, 16*24, dtype=np.float32).reshape(1, 16, 24)
    envelope = abs(rf) + .1
    valid = np.ones_like(rf)
    if index > 0:
        rf[:] = envelope[:] = valid[:] = 0
    return {"rf": rf, "envelope": envelope, "valid_mask": valid}


def finish(store, identifier):
    for z in range(3):
        store.write_slice(identifier, z, z+1, slice_arrays(z))
    return store.complete(identifier)


def insert_pending(manager, config, state="queued"):
    identifier = str(uuid4())
    estimate = manager.estimate_depth(config)
    store = DepthDatasetStore(manager.root)
    manifest = store.create(identifier, config.model_dump(mode="json", exclude_none=True), estimate)
    with sqlite3.connect(manager.root / "catalog.sqlite3") as connection:
        connection.execute("""INSERT INTO jobs
            (job_id, state, name, created_at, updated_at, completed_rows, total_rows, error, kind)
            VALUES (?, ?, 'Depth estimate test', ?, ?, 0, ?, NULL, 'sam_depth_volume')""",
            (identifier, state, manifest["created_at"], manifest["created_at"], manifest["total_rows"]))
    return identifier


def test_depth_source_identity_global_xy_coordinates_and_units(tmp_path, source_template):
    store, identifier, source_id = small_store(tmp_path, source_template)
    manifest = store.manifest(identifier)
    assert manifest["source_dataset_id"] == source_id
    assert manifest["source_manifest_sha256"] == json_sha256(manifest["source_manifest"])
    assert manifest["axis_order"] == ["z", "y", "x"]
    assert set(manifest["solver"]["source_sha256"]) == {"depth_mapping.py", "depth_schemas.py"}
    assert manifest["arrays"]["rf"]["units"] == "relative signed pressure"
    assert manifest["arrays"]["envelope"]["units"] == "relative echo amplitude"
    assert str(tmp_path).encode() not in canonical_json(manifest)
    group = store.open_arrays(identifier)
    for name in store.signal_units:
        assert group[name].shape == (3, 16, 24) and group[name].dtype == np.dtype("float32")
        assert group[name].metadata.dimension_names == ("z", "y", "x")
    for name in ("x_mm", "y_mm", "z_mm", "travel_time_us"):
        assert group[name].dtype == np.dtype("float64")
    for name in ("x_mm", "y_mm"):
        assert manifest["coordinates_sha256"][name] == manifest["source_manifest"]["coordinates_sha256"][name]
        np.testing.assert_array_equal(group[name][:], DatasetStore(tmp_path).open_arrays(source_id)[name][:])


def test_signed_rf_and_record_vs_model_support_are_distinct(tmp_path, source_template):
    store, identifier, _ = small_store(tmp_path, source_template)
    finish(store, identifier)
    group = store.open_arrays(identifier)
    assert group["rf"][0, 0, 0] < 0 and group["envelope"][0, 0, 0] > 0
    assert group["valid_mask"][0, 0, 0] == 1
    assert group["valid_mask"][1, 0, 0] == group["valid_mask"][2, 0, 0] == 0
    assert group["travel_time_us"][1] == .8  # model exists, but the time was not recorded
    assert group["travel_time_us"][2] == 0   # no declared layered model at this depth
    assert store.verify_complete(identifier)["metadata"]["model_depth_valid"] == [True, True, False]


@pytest.mark.parametrize("damage", ["nan", "overflow", "negative_envelope", "nonbinary", "invalid_nonzero", "out_of_record_valid", "out_of_model_valid"])
def test_depth_masks_and_signal_constraints_are_checked_before_commit(tmp_path, source_template, damage):
    store, identifier, _ = small_store(tmp_path, source_template)
    index = 0
    arrays = slice_arrays()
    if damage == "nan":
        arrays["rf"][0, 0, 0] = np.nan
    elif damage == "overflow":
        arrays["rf"] = arrays["rf"].astype(np.float64)
        arrays["rf"][0, 0, 0] = np.finfo(np.float64).max
    elif damage == "negative_envelope":
        arrays["envelope"][0, 0, 0] = -.1
    elif damage == "nonbinary":
        arrays["valid_mask"][0, 0, 0] = .5
    elif damage == "invalid_nonzero":
        arrays["valid_mask"][0, 0, 0] = 0
    elif damage == "out_of_record_valid":
        index = 1
    else:
        index = 2
    with pytest.raises(ValueError):
        store.write_slice(identifier, index, index+1, arrays)
    assert store.manifest(identifier)["completed_rows"] == 0
    assert np.isnan(store.open_arrays(identifier)["rf"][:]).all()


def test_source_and_processing_provenance_are_frozen_and_checksum_verified(tmp_path, source_template):
    store, identifier, _ = small_store(tmp_path, source_template)
    modified = deepcopy(store.manifest(identifier))
    modified["metadata"]["model_depth_valid"][2] = True
    with pytest.raises(ValueError, match="immutable"):
        store.save(identifier, modified)
    completed = finish(store, identifier)
    completed["metadata"]["model_depth_valid"][2] = True
    atomic_json(store.path(identifier) / "manifest.json", completed)
    with pytest.raises(ValueError, match="metadata checksum"):
        store.verify_complete(identifier)


@pytest.mark.parametrize("damage", ["missing_time_registry", "time_corruption", "lateral_corruption"])
def test_export_validates_all_four_coordinate_arrays(tmp_path, source_template, damage):
    store, identifier, _ = small_store(tmp_path, source_template)
    manifest = finish(store, identifier)
    group = zarr.open_group(str(store.path(identifier) / "data.zarr"), mode="r+")
    if damage == "missing_time_registry":
        manifest["coordinates_sha256"].pop("travel_time_us")
        atomic_json(store.path(identifier) / "manifest.json", manifest)
    elif damage == "time_corruption":
        group["travel_time_us"][1] = .6
    else:
        group["x_mm"][0] += .01
    with pytest.raises(ValueError, match="checksum"):
        store.verify_complete(identifier)


def test_source_independent_completed_integrity_and_legacy_raw_kind(tmp_path, source_template):
    source_id = copy_source(tmp_path, source_template)
    raw = DatasetStore(tmp_path).manifest(source_id)
    raw.pop("kind")
    atomic_json(tmp_path / source_id / "manifest.json", raw)
    assert depth_source(tmp_path, source_id, verify=True)[0].get("kind") is None
    store, identifier, child_source_id = small_store(tmp_path / "derived", source_template)
    finish(store, identifier)
    source = store.root / child_source_id
    detached = store.root / "retained-source"
    assert source.resolve().parent == detached.resolve().parent == store.root
    source.rename(detached)
    assert store.verify_complete(identifier)["complete"]
    with pytest.raises(ValueError, match="immutable"):
        store.write_slice(identifier, 0, 1, slice_arrays())


def test_partial_chunk_commits_and_missing_mask_repair(tmp_path, source_template, monkeypatch):
    store, identifier, _ = small_store(tmp_path, source_template)
    save = store.save
    monkeypatch.setattr(store, "save", lambda *_: (_ for _ in ()).throw(OSError("commit interrupted")))
    with pytest.raises(OSError):
        store.write_slice(identifier, 0, 1, slice_arrays())
    assert store.manifest(identifier)["completed_chunks"] == {}
    monkeypatch.setattr(store, "save", save)
    for index in range(3):
        store.write_slice(identifier, index, index+1, slice_arrays(index))
    chunk = store.path(identifier) / "data.zarr" / "valid_mask" / "c" / "1" / "0" / "0"
    assert chunk.is_file()
    chunk.unlink()
    assert store.verify_chunks(identifier)["completed_rows"] == 2
    store.write_slice(identifier, 1, 2, slice_arrays(1))
    store.complete(identifier)
    store.verify_complete(identifier)


def test_changed_raw_source_fails_resume_and_corruption_fails_before_prepare(tmp_path, source_template, monkeypatch):
    source_id = copy_source(tmp_path, source_template)
    manager = VolumeJobManager(tmp_path)
    identifier = insert_pending(manager, request(source_id), "running")
    source_store = DatasetStore(tmp_path)
    original = source_store.manifest(source_id)
    modified = deepcopy(original)
    modified["metadata"]["review_note"] = "Changed raw source"
    atomic_json(source_store.path(source_id) / "manifest.json", modified)
    with pytest.raises(ValueError, match="source manifest changed"):
        manager.get_store(identifier).validate_identity(identifier)
    atomic_json(source_store.path(source_id) / "manifest.json", original)
    zarr.open_group(str(tmp_path / source_id / "data.zarr"), mode="r+")["rf"][0, 0, 0] = 123
    def unexpected_prepare(*_):
        pytest.fail("Corrupt raw samples must be rejected before mapping preparation.")
    monkeypatch.setattr("virtual_microscopy.depth_mapping.prepare_depth", unexpected_prepare)
    _run_job(tmp_path, identifier, threading.Event())
    job = manager.get_job(identifier)
    assert job["state"] == "failed" and "checksum mismatch" in job["error"]
    assert not list(tmp_path.glob(f"{CACHE_PREFIX}*"))


def test_real_depth_cancel_resume_preserves_raw_bytes_and_cleans_workspaces(tmp_path, source_template, monkeypatch):
    from virtual_microscopy.depth_mapping import prepare_depth, iter_depth_slices
    source_id = copy_source(tmp_path, source_template)
    manager = VolumeJobManager(tmp_path)
    config = request(source_id)
    identifier = insert_pending(manager, config, "running")
    source_path = tmp_path / source_id
    before = {p.relative_to(source_path): p.read_bytes() for p in source_path.rglob("*") if p.is_file()}
    write_slice = DepthDatasetStore.write_slice
    def cancel_after_two(store, identifier, *args):
        manifest = write_slice(store, identifier, *args)
        if manifest["completed_rows"] == 2:
            _update_job(tmp_path, identifier, "cancelling", 2)
        return manifest
    monkeypatch.setattr(DepthDatasetStore, "write_slice", cancel_after_two)
    _run_job(tmp_path, identifier, threading.Event())
    cancelled = manager.get_job(identifier)
    assert cancelled["state"] == "cancelled", cancelled
    assert cancelled["completed_units"] == 2 and cancelled["progress_unit"] == "slices"
    assert not list(tmp_path.glob(f"{CACHE_PREFIX}*"))
    monkeypatch.setattr(DepthDatasetStore, "write_slice", write_slice)
    _update_job(tmp_path, identifier, "running", 2)
    _run_job(tmp_path, identifier, threading.Event())
    final = manager.get_job(identifier)
    assert final["state"] == "completed", final
    assert not list(tmp_path.glob(f"{CACHE_PREFIX}*"))
    source, path = depth_source(tmp_path, source_id, verify=True)
    prepared = prepare_depth(config, source, path)
    try:
        expected = list(iter_depth_slices(prepared))
    finally:
        prepared.close()
    for name in ("rf", "envelope", "valid_mask"):
        np.testing.assert_array_equal(manager.get_store(identifier).open_arrays(identifier)[name][:],
                                      np.concatenate([part[2][name] for part in expected]))
    after = {p.relative_to(source_path): p.read_bytes() for p in source_path.rglob("*") if p.is_file()}
    assert before == after
    assert not list(tmp_path.glob(f"{CACHE_PREFIX}*"))


def test_depth_budget_includes_cache_once_and_estimate_is_deterministic(tmp_path, source_template, monkeypatch):
    source_id = copy_source(tmp_path, source_template)
    manager = VolumeJobManager(tmp_path)
    calls = []
    monkeypatch.setattr("virtual_microscopy.volume_jobs.check_disk_space", lambda root, size: calls.append((root, size)))
    estimate = manager.estimate_depth(request(source_id))
    assert calls == [(tmp_path.resolve(), estimate["total_bytes"] + estimate["estimated_temporary_bytes"])]
    assert estimate["estimated_temporary_bytes"] == estimate["workspace_disk_bytes"]
    assert "free_disk_bytes" not in estimate


def test_depth_cleanup_and_source_paths_respect_ownership(tmp_path, source_template):
    owned = tmp_path / f"{CACHE_PREFIX}{uuid4()}"
    owned.mkdir()
    (owned / "owner.json").write_text(json.dumps(CACHE_OWNER))
    (owned / "mapped.f32").write_bytes(b"orphan cache")
    unknown = tmp_path / f"{CACHE_PREFIX}{uuid4()}"
    unknown.mkdir()
    (unknown / "owner.json").write_text('{"purpose":"unrelated user data"}')
    cleanup_depth_caches(tmp_path)
    assert not owned.exists() and unknown.is_dir()
    link_id = str(uuid4())
    try:
        (tmp_path / link_id).symlink_to(source_template, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires permission on this host.")
    with pytest.raises(ValueError, match="symbolic links"):
        depth_source(tmp_path, link_id)


def test_spawned_depth_job_completes_and_reopens(tmp_path, source_template):
    source_id = copy_source(tmp_path, source_template)
    manager = VolumeJobManager(tmp_path)
    manager.start()
    try:
        submitted = manager.submit(request(source_id))
        assert submitted["name"].startswith("Depth estimate / ")
        deadline = time.monotonic()+25
        while time.monotonic() < deadline:
            final = manager.get_job(submitted["id"])
            if final["state"] in {"failed", "completed"}:
                break
            time.sleep(.05)
        assert final["state"] == "completed", final
        assert final["kind"] == "sam_depth_volume" and final["progress_unit"] == "slices"
        assert final["completed_units"] == final["total_units"] == 16
        manager.get_store(final["id"]).verify_complete(final["id"])
        assert not list(tmp_path.glob(f"{CACHE_PREFIX}*"))
    finally:
        manager.close()
    reopened = VolumeJobManager(tmp_path)
    assert reopened.list_datasets()[0]["kind"] == "sam_depth_volume"
    assert reopened.get_store(final["id"]).verify_complete(final["id"])["complete"]
