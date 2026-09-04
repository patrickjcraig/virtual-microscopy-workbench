"""Derived-volume provenance, immutable source handling and worker recovery."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sqlite3
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest

from virtual_microscopy.datasets import atomic_json, canonical_json, json_sha256
from virtual_microscopy.reconstruction_datasets import (CACHE_OWNER, CACHE_PREFIX, ReconstructionDatasetStore,
    cleanup_reconstruction_caches, reconstruction_source)
from virtual_microscopy.reconstruction_schemas import ReconstructionRequest
from virtual_microscopy.volume_jobs import VolumeJobManager, _run_job, _update_job
from virtual_microscopy.xray_datasets import XrayDatasetStore
from virtual_microscopy.xray_schemas import XrayVolumeRequest


@pytest.fixture(scope="module")
def source_template(tmp_path_factory):
    from virtual_microscopy.xray_volume import estimate_xray, prepare_xray, iter_xray_views
    root = tmp_path_factory.mktemp("reconstruction-source")
    config = XrayVolumeRequest.model_validate({"kind": "xray_projection_volume", "twin": {
        "schema_version": 1, "name": "Derived-volume coupon", "size_mm": [8, 6, 1.6],
        "objects": [{"id": "silicon", "name": "Silicon layer", "shape": "box", "material": "silicon",
                     "center_mm": [4, 3, 0.6], "size_mm": [8, 6, 0.8], "role": "structure"}]},
        "acquisition": {"geometry_nx": 16, "geometry_ny": 16, "geometry_nz": 32,
                        "detector_rows": 16, "detector_cols": 24, "views": 16,
                        "detector_width_mm": 12, "detector_height_mm": 8,
                        "noise": False, "photons": 1000, "detector_fwhm_mm": 0}})
    identifier = str(uuid4())
    store = XrayDatasetStore(root)
    store.create(identifier, config.model_dump(mode="json", exclude_none=True), estimate_xray(config))
    prepared = prepare_xray(config)
    store.initialize_arrays(identifier, prepared)
    for view in iter_xray_views(prepared):
        store.write_view(identifier, *view)
    store.complete(identifier)
    return root / identifier


def copy_source(root, source_template):
    root.mkdir(parents=True, exist_ok=True)
    target = root / source_template.name
    shutil.copytree(source_template, target)
    return target.name


def request(identifier):
    return ReconstructionRequest.model_validate({"kind": "xray_reconstruction", "source_dataset_id": identifier,
        "reconstruction": {"nx": 16, "ny": 16, "nz": 16, "filter": "hann", "frequency_cutoff": 1,
                           "invalid_policy": "reject", "truncation_policy": "reject"}})


def small_store(root, source_template):
    source_id = copy_source(root, source_template)
    store = ReconstructionDatasetStore(root)
    identifier = str(uuid4())
    config = {"kind": "xray_reconstruction", "source_dataset_id": source_id,
              "reconstruction": {"nx": 5, "ny": 4, "nz": 3, "filter": "hann"}}
    estimate = {"kind": "xray_reconstruction", "model_version": "reconstruction-storage-test",
                "shape": [3, 4, 5], "tile_rows": 1, "total_bytes": 600, "estimated_temporary_bytes": 200}
    store.create(identifier, config, estimate)
    prepared = SimpleNamespace(x_mm=np.linspace(1, 7, 5), y_mm=np.linspace(1, 5, 4),
                               z_mm=np.linspace(0.3, 1.3, 3), metadata={"synthetic": True})
    store.initialize_arrays(identifier, prepared)
    return store, identifier, source_id


def slice_arrays(index=0):
    attenuation = np.linspace(-.2, .4, 20, dtype=np.float32).reshape(1, 4, 5) + index / 20
    coverage = np.ones((1, 4, 5), dtype=np.float32)
    coverage[0, 0, :2] = [0, .5]
    attenuation[0, 0, :2] = 0
    return {"attenuation": attenuation, "coverage": coverage}


def finish(store, identifier):
    for z in range(3):
        store.write_slice(identifier, z, z + 1, slice_arrays(z))
    return store.complete(identifier)


def insert_pending(manager, config, state="queued"):
    identifier = str(uuid4())
    estimate = manager.estimate_reconstruction(config)
    store = ReconstructionDatasetStore(manager.root)
    manifest = store.create(identifier, config.model_dump(mode="json", exclude_none=True), estimate)
    with sqlite3.connect(manager.root / "catalog.sqlite3") as connection:
        connection.execute("""INSERT INTO jobs
            (job_id, state, name, created_at, updated_at, completed_rows, total_rows, error, kind)
            VALUES (?, ?, 'Test reconstruction', ?, ?, 0, ?, NULL, 'xray_reconstruction')""",
            (identifier, state, manifest["created_at"], manifest["created_at"], manifest["total_rows"]))
    return identifier


def test_reconstruction_freezes_source_identity_axes_and_units(tmp_path, source_template):
    store, identifier, source_id = small_store(tmp_path, source_template)
    manifest = store.manifest(identifier)
    assert manifest["axis_order"] == ["z", "y", "x"]
    assert manifest["source_dataset_id"] == source_id
    assert manifest["source_manifest"]["dataset_id"] == source_id
    assert manifest["source_manifest_sha256"] == json_sha256(manifest["source_manifest"])
    assert manifest["materials"] == manifest["source_manifest"]["materials"]
    assert set(manifest["solver"]["source_sha256"]) == {"reconstruction.py", "reconstruction_schemas.py"}
    assert str(tmp_path).encode() not in canonical_json(manifest)
    group = store.open_arrays(identifier)
    assert group["attenuation"].dtype == group["coverage"].dtype == np.dtype("float32")
    assert group["attenuation"].metadata.dimension_names == ("z", "y", "x")
    assert group["attenuation"].shape == (3, 4, 5)
    for name in ("x_mm", "y_mm", "z_mm"):
        assert group[name].dtype == np.dtype("float64")


def test_supported_negative_values_and_coverage_zero_placeholders_are_retained(tmp_path, source_template):
    store, identifier, _ = small_store(tmp_path, source_template)
    finish(store, identifier)
    arrays = store.open_arrays(identifier)
    assert arrays["attenuation"][0, 0, 2] < 0
    assert arrays["attenuation"][0, 0, 0] == arrays["coverage"][0, 0, 0] == 0
    assert arrays["attenuation"][0, 0, 1] == 0 and arrays["coverage"][0, 0, 1] == .5
    assert store.verify_complete(identifier)["complete"]


@pytest.mark.parametrize("damage", ["nan", "overflow", "negative_coverage", "over_coverage", "unmasked_partial"])
def test_reconstruction_array_validation_precedes_any_commit(tmp_path, source_template, damage):
    store, identifier, _ = small_store(tmp_path, source_template)
    arrays = slice_arrays()
    if damage == "nan":
        arrays["attenuation"][0, 1, 1] = np.nan
    elif damage == "overflow":
        arrays["attenuation"] = arrays["attenuation"].astype(np.float64)
        arrays["attenuation"][0, 1, 1] = np.finfo(np.float64).max
    elif damage == "negative_coverage":
        arrays["coverage"][0, 1, 1] = -.1
    elif damage == "over_coverage":
        arrays["coverage"][0, 1, 1] = 1.1
    else:
        arrays["attenuation"][0, 0, 1] = 0.1
    with pytest.raises(ValueError):
        store.write_slice(identifier, 0, 1, arrays)
    assert store.manifest(identifier)["completed_rows"] == 0
    assert np.isnan(store.open_arrays(identifier)["attenuation"][:]).all()


def test_reconstruction_snapshot_cannot_change_and_export_detects_edited_provenance(tmp_path, source_template):
    store, identifier, _ = small_store(tmp_path, source_template)
    changed = deepcopy(store.manifest(identifier))
    changed["source_manifest"]["request"]["twin"]["name"] = "Changed source"
    with pytest.raises(ValueError, match="Frozen dataset"):
        store.save(identifier, changed)
    complete = finish(store, identifier)
    complete["source_manifest"]["request"]["twin"]["name"] = "Changed source"
    atomic_json(store.path(identifier) / "manifest.json", complete)
    with pytest.raises(ValueError, match="source manifest checksum"):
        store.verify_complete(identifier)


def test_completed_reconstruction_remains_readable_without_original_source(tmp_path, source_template):
    store, identifier, source_id = small_store(tmp_path, source_template)
    finish(store, identifier)
    source = tmp_path / source_id
    detached = tmp_path / "retained-detached-source"
    # Both resolved move targets stay in this test's explicit temporary root.
    assert source.resolve().parent == detached.resolve().parent == tmp_path.resolve()
    source.rename(detached)
    assert store.verify_complete(identifier)["complete"]
    np.testing.assert_array_equal(store.open_arrays(identifier)["attenuation"][0:1], slice_arrays()["attenuation"])
    with pytest.raises(ValueError, match="immutable"):
        store.write_slice(identifier, 0, 1, slice_arrays())


def test_resume_requires_unchanged_source_manifest_and_checks_original_chunks(tmp_path, source_template):
    store, identifier, source_id = small_store(tmp_path, source_template)
    source_store = XrayDatasetStore(tmp_path)
    source = source_store.manifest(source_id)
    changed = deepcopy(source)
    changed["metadata"]["review_note"] = "Changed after reconstruction was queued"
    atomic_json(source_store.path(source_id) / "manifest.json", changed)
    with pytest.raises(ValueError, match="source manifest changed"):
        store.validate_identity(identifier)
    atomic_json(source_store.path(source_id) / "manifest.json", source)
    import zarr
    zarr.open_group(str(source_store.path(source_id) / "data.zarr"), mode="r+")["line_integrals"][0, 0, 0] = 123
    with pytest.raises(ValueError, match="checksum mismatch"):
        store.source_context(identifier, verify=True)


def test_reconstruction_uses_frozen_source_materials_without_current_library_dependency(tmp_path, source_template, monkeypatch):
    store, identifier, _ = small_store(tmp_path, source_template)
    monkeypatch.setattr("virtual_microscopy.datasets.material_snapshot", lambda: {"future": "library"})
    assert store.validate_identity(identifier)["source_manifest_sha256"]


def test_slice_commits_are_atomic_and_corruption_is_repaired(tmp_path, source_template, monkeypatch):
    store, identifier, _ = small_store(tmp_path, source_template)
    save = store.save
    monkeypatch.setattr(store, "save", lambda *_: (_ for _ in ()).throw(OSError("interrupted commit")))
    with pytest.raises(OSError):
        store.write_slice(identifier, 0, 1, slice_arrays())
    assert store.manifest(identifier)["completed_chunks"] == {}
    monkeypatch.setattr(store, "save", save)
    for z in range(3):
        store.write_slice(identifier, z, z + 1, slice_arrays(z))
    store.open_arrays(identifier, "r+")["coverage"][1, 1, 1] = 0
    manifest = store.verify_chunks(identifier)
    assert manifest["completed_rows"] == 2 and set(manifest["completed_chunks"]) == {"0", "2"}
    store.write_slice(identifier, 1, 2, slice_arrays(1))
    store.complete(identifier)
    store.verify_complete(identifier)


def test_corrupted_source_is_rejected_before_solver_preparation(tmp_path, source_template, monkeypatch):
    source_id = copy_source(tmp_path, source_template)
    manager = VolumeJobManager(tmp_path)
    identifier = insert_pending(manager, request(source_id), "running")
    import zarr
    zarr.open_group(str(tmp_path / source_id / "data.zarr"), mode="r+")["valid_mask"][0, 0, 0] = 0
    def unexpected_prepare(*_):
        pytest.fail("Source integrity must be checked before reconstruction preparation.")
    monkeypatch.setattr("virtual_microscopy.reconstruction.prepare_reconstruction", unexpected_prepare)
    _run_job(tmp_path, identifier, threading.Event())
    job = manager.get_job(identifier)
    assert job["state"] == "failed" and "checksum mismatch" in job["error"]
    assert not list(tmp_path.glob(f"{CACHE_PREFIX}*"))


def test_real_reconstruction_cancel_resume_is_exact_and_cleans_cache(tmp_path, source_template, monkeypatch):
    from virtual_microscopy.reconstruction import prepare_reconstruction, iter_reconstruction_slices
    source_id = copy_source(tmp_path, source_template)
    manager = VolumeJobManager(tmp_path)
    config = request(source_id)
    identifier = insert_pending(manager, config, "running")
    source_before = {p.relative_to(tmp_path / source_id): p.read_bytes() for p in (tmp_path / source_id).rglob("*") if p.is_file()}
    write_slice = ReconstructionDatasetStore.write_slice
    def cancel_after_two(store, identifier, *args):
        manifest = write_slice(store, identifier, *args)
        if manifest["completed_rows"] == 2:
            _update_job(tmp_path, identifier, "cancelling", 2)
        return manifest
    monkeypatch.setattr(ReconstructionDatasetStore, "write_slice", cancel_after_two)
    _run_job(tmp_path, identifier, threading.Event())
    cancelled = manager.get_job(identifier)
    assert cancelled["state"] == "cancelled", cancelled
    assert cancelled["progress_unit"] == "slices" and cancelled["completed_units"] == 2
    assert not list(tmp_path.glob(f"{CACHE_PREFIX}*"))
    monkeypatch.setattr(ReconstructionDatasetStore, "write_slice", write_slice)
    _update_job(tmp_path, identifier, "running", 2)
    _run_job(tmp_path, identifier, threading.Event())
    final = manager.get_job(identifier)
    assert final["state"] == "completed", final
    assert not list(tmp_path.glob(f"{CACHE_PREFIX}*"))
    source, source_path = reconstruction_source(tmp_path, source_id, verify=True)
    prepared = prepare_reconstruction(config, source, source_path)
    try:
        expected = list(iter_reconstruction_slices(prepared))
    finally:
        prepared.close()
    for name in ("attenuation", "coverage"):
        np.testing.assert_array_equal(manager.get_store(identifier).open_arrays(identifier)[name][:],
                                      np.concatenate([part[2][name] for part in expected]))
    source_after = {p.relative_to(tmp_path / source_id): p.read_bytes() for p in (tmp_path / source_id).rglob("*") if p.is_file()}
    assert source_before == source_after
    assert not list(tmp_path.glob(f"{CACHE_PREFIX}*"))


def test_disk_preflight_counts_temporary_workspace_once(tmp_path, source_template, monkeypatch):
    source_id = copy_source(tmp_path, source_template)
    manager = VolumeJobManager(tmp_path)
    calls = []
    monkeypatch.setattr("virtual_microscopy.volume_jobs.check_disk_space", lambda root, size: calls.append(size))
    estimate = manager.estimate_reconstruction(request(source_id))
    assert calls == [estimate["total_bytes"] + estimate["estimated_temporary_bytes"]]
    assert "free_disk_bytes" not in estimate
    assert estimate["estimated_temporary_bytes"] == estimate["workspace_disk_bytes"]


def test_orphan_cache_cleanup_requires_uuid_marker_and_root_containment(tmp_path):
    owned = tmp_path / f"{CACHE_PREFIX}{uuid4()}"
    owned.mkdir()
    (owned / "owner.json").write_text(json.dumps(CACHE_OWNER))
    (owned / "filtered.f32").write_bytes(b"stale cache")
    unrelated = tmp_path / f"{CACHE_PREFIX}{uuid4()}"
    unrelated.mkdir()
    (unrelated / "owner.json").write_text('{"purpose":"user data"}')
    (unrelated / "preserve.txt").write_text("Preserve this unrelated file.")
    cleanup_reconstruction_caches(tmp_path)
    assert not owned.exists()
    assert (unrelated / "preserve.txt").read_text() == "Preserve this unrelated file."


def test_source_links_are_rejected_before_reading_projection_metadata(tmp_path, source_template):
    identifier = str(uuid4())
    link = tmp_path / identifier
    try:
        link.symlink_to(source_template, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks requires permission on this host.")
    with pytest.raises(ValueError, match="symbolic links"):
        reconstruction_source(tmp_path, identifier)


def test_spawn_worker_completes_derived_dataset_and_reopens_without_source_catalog_row(tmp_path, source_template):
    source_id = copy_source(tmp_path, source_template)
    manager = VolumeJobManager(tmp_path)
    manager.start()
    try:
        job = manager.submit(request(source_id))
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            final = manager.get_job(job["id"])
            if final["state"] in {"completed", "failed"}:
                break
            time.sleep(.05)
        assert final["state"] == "completed", final
        assert final["kind"] == "xray_reconstruction" and final["progress_unit"] == "slices"
        assert final["completed_units"] == final["total_units"] == 16
        assert manager.get_store(job["id"]).verify_complete(job["id"])["complete"]
        assert not list(tmp_path.glob(f"{CACHE_PREFIX}*"))
    finally:
        manager.close()
    reopened = VolumeJobManager(tmp_path)
    assert reopened.list_datasets()[0]["kind"] == "xray_reconstruction"
    assert reopened.get_store(job["id"]).verify_complete(job["id"])["complete"]
