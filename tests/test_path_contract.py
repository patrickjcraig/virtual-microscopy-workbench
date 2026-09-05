"""Explicit numerical method selection and immutable historical interpretation."""
from copy import deepcopy
import hashlib
from uuid import uuid4

import numpy as np
import pytest
from pydantic import ValidationError

from virtual_microscopy.datasets import DatasetStore, solver_identity
from virtual_microscopy.schemas import Settings
from virtual_microscopy.volume_api import public_manifest
from virtual_microscopy.volume_processing import sam_view
from virtual_microscopy.volume_schemas import SamVolumeSettings
from virtual_microscopy.volume_jobs import VolumeJobManager


@pytest.mark.parametrize("schema", [Settings, SamVolumeSettings])
def test_absent_method_has_explicit_voxel_default(schema):
    config = schema()
    assert config.path_model == "voxel_centers_v1"
    assert config.model_dump()["path_model"] == "voxel_centers_v1"


@pytest.mark.parametrize("schema", [Settings, SamVolumeSettings])
@pytest.mark.parametrize("method", [None, "continuous", "", 0, True])
def test_method_rejects_unknown_or_null_choices(schema, method):
    with pytest.raises(ValidationError):
        schema(path_model=method)


@pytest.mark.parametrize("angle", [-45, -.00001, .00001, 45])
def test_continuous_preview_rejects_tilt_without_changing_request(angle):
    values = {"path_model": "continuous_columns_v1", "angle_deg": angle}
    original = deepcopy(values)
    with pytest.raises(ValidationError, match="0°"):
        Settings(**values)
    assert values == original
    assert Settings(path_model="voxel_centers_v1", angle_deg=angle).angle_deg == angle


@pytest.mark.parametrize("schema", [Settings, SamVolumeSettings])
def test_continuous_choice_preserves_authored_inactive_z_count(schema):
    for depth in (128, 256, 512, 1024):
        config = schema(path_model="continuous_columns_v1", depth_samples=depth)
        assert config.model_dump()["depth_samples"] == depth
        assert config.model_dump()["path_model"] == "continuous_columns_v1"


def test_public_legacy_method_is_read_only_and_does_not_label_other_kinds():
    legacy = {"dataset_id": str(uuid4()), "kind": "sam_rf_volume", "shape": [2, 3, 4],
              "request": {"twin": {"name": "Historical", "size_mm": [4, 3, 1]},
                          "acquisition": {"record_start_us": .2, "sample_rate_mhz": 400}}}
    before = deepcopy(legacy)
    response = public_manifest(legacy)
    assert response["path_model"] == "voxel_centers_v1"
    assert legacy == before
    assert "path_model" not in response["request"]["acquisition"]
    for kind in ("xray_projection_volume", "xray_reconstruction", "sam_depth_volume"):
        other = {"dataset_id": str(uuid4()), "kind": kind}
        assert "path_model" not in public_manifest(other)


def test_public_continuous_method_is_explicit():
    source = {"kind": "sam_rf_volume", "request": {
        "acquisition": {"path_model": "continuous_columns_v1"}}}
    assert public_manifest(source)["path_model"] == "continuous_columns_v1"


@pytest.mark.parametrize("choice", [None, "continuous_columns_v1"])
def test_compact_catalog_retains_frozen_method_before_dropping_request(tmp_path, monkeypatch, choice):
    manager, identifier = VolumeJobManager(tmp_path), str(uuid4())
    acquisition = {} if choice is None else {"path_model": choice}
    config = {"twin": {"name": "Catalog method"}, "acquisition": acquisition}
    manager.store.create(identifier, config, {"shape": [2, 3, 4], "tile_rows": 1,
        "total_bytes": 264, "model_version": "sam-continuous-columns-0.8.0" if choice else "sam-volume-0.7.0"})
    monkeypatch.setattr(manager, "list_jobs", lambda: [{"dataset_id": identifier, "kind": "sam_rf_volume"}])
    path = manager.store.path(identifier) / "manifest.json"
    before = path.read_bytes()
    summary = public_manifest(manager.list_datasets()[0])
    assert "request" not in summary
    assert summary["path_model"] == (choice or "voxel_centers_v1")
    assert path.read_bytes() == before


def test_completed_legacy_without_path_field_reads_under_new_solver(tmp_path, monkeypatch):
    store, identifier = DatasetStore(tmp_path), str(uuid4())
    request = {"twin": {"name": "Legacy data", "size_mm": [4, 3, 1]},
               "acquisition": {"record_start_us": .2, "sample_rate_mhz": 400}}
    estimate = {"shape": [2, 3, 4], "tile_rows": 1, "total_bytes": 264,
                "model_version": "sam-volume-0.7.0"}
    store.create(identifier, request, estimate)
    store.initialize_arrays(identifier, [.5, 1.5, 2.5], [.25, .75],
                            .2 + np.arange(4) / 400, {"historical": True})
    rf = np.arange(24, dtype=np.float32).reshape(2, 3, 4) / 30 - .5
    for row in range(2):
        store.write_tile(identifier, row, row + 1, rf[row:row+1], abs(rf[row:row+1]))
    store.complete(identifier)
    files = list(path for path in store.path(identifier).rglob("*") if path.is_file())
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    monkeypatch.setattr("virtual_microscopy.datasets.solver_identity", lambda *_: {"future": True})
    manifest = store.verify_complete(identifier)
    assert "path_model" not in manifest["request"]["acquisition"]
    assert public_manifest(manifest)["path_model"] == "voxel_centers_v1"
    view = sam_view(store.path(identifier), x_index=1, y_index=0, time_index=2)
    np.testing.assert_array_equal(view["ascan"]["amplitude"], rf[0, 1])
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}


def test_continuous_identity_includes_shared_path_and_integration_sources():
    identity = solver_identity("sam-continuous-columns-0.8.0")
    assert {"column_paths.py", "continuous_sam.py", "sam_volume.py", "physics.py",
            "schemas.py", "volume_schemas.py", "hbm.py", "materials.py"} <= set(identity["source_sha256"])
    assert "column_paths.py" not in solver_identity("sam-volume-0.7.0")["source_sha256"]
