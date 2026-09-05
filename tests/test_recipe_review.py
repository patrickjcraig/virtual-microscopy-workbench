"""Independent regressions for strict import and frozen historical recipe reads."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from virtual_microscopy.datasets import DatasetStore, json_sha256
from virtual_microscopy.recipes import RecipeStore
from virtual_microscopy.volume_schemas import SamVolumeRequest


def request():
    return {"twin": {"name": "Independent recipe review coupon", "size_mm": [4, 3, 1],
        "objects": [{"id": "si", "name": "Silicon", "shape": "box", "material": "silicon",
                     "center_mm": [2, 1.5, .5], "size_mm": [4, 3, .5]}]},
        "acquisition": {"scan_nx": 16, "scan_ny": 16, "frequency_mhz": 50,
                        "record_duration_us": .5, "focus_mm": .25}}


def historical_source(root):
    store, identifier = DatasetStore(root), str(uuid4())
    raw = SamVolumeRequest.model_validate(request()).model_dump(mode="json", exclude_none=True)
    raw["acquisition"].pop("path_model")
    raw["twin"]["retired_field"] = "Frozen historical metadata unsupported by today's constructor."
    store.create(identifier, raw, {"shape": [16, 16, 5], "tile_rows": 4,
        "total_bytes": 10240, "model_version": "sam-volume-0.7.0"})
    store.initialize_arrays(identifier, .125+np.arange(16)*.25, .09375+np.arange(16)*.1875,
                            np.arange(5)*.125, {})
    values = np.ones((4, 16, 5), dtype=np.float32)
    for lo in range(0, 16, 4):
        store.write_tile(identifier, lo, lo+4, values, values*2)
    manifest = store.complete(identifier)
    manifest.pop("kind")
    (root/identifier/"manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return store, identifier, manifest


@pytest.mark.parametrize("malformation", ["large_specimen", "null_primitive", "unknown_material", "too_many_primitives"])
def test_recomputed_hashes_do_not_admit_invalid_new_nonhistorical_recipe(tmp_path, malformation):
    store = RecipeStore(tmp_path)
    original = store.create({"name": "Valid original", "request": request()})
    invalid = deepcopy(original)
    invalid["recipe_id"] = str(uuid4())
    twin = invalid["request"]["twin"]
    if malformation == "large_specimen":
        twin["size_mm"][0] = 101
    elif malformation == "null_primitive":
        twin["objects"][0] = None
    elif malformation == "unknown_material":
        twin["objects"][0]["material"] = "unvalidated-material"
    else:
        twin["objects"] = [dict(twin["objects"][0], id=f"object-{i}") for i in range(601)]
    invalid["request_sha256"] = json_sha256(invalid["request"])
    invalid["recipe_sha256"] = json_sha256({key: value for key, value in invalid.items() if key != "recipe_sha256"})
    with pytest.raises(ValueError):
        store.import_record(invalid)
    assert store.get(original["recipe_id"]) == original
    assert [item["recipe_id"] for item in store.list()] == [original["recipe_id"]]


def test_missing_legacy_kind_reads_imports_and_reopens_without_current_constructor(tmp_path, monkeypatch):
    _, identifier, manifest = historical_source(tmp_path)
    path = tmp_path/identifier
    before = {p.relative_to(path): hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob("*") if p.is_file()}
    def forbidden(*args, **kwargs):
        pytest.fail("A historical source recipe must not invoke the current acquisition constructor.")
    monkeypatch.setattr(SamVolumeRequest, "model_validate", forbidden)
    store = RecipeStore(tmp_path)
    record = store.from_dataset({"dataset_id": identifier, "name": "Missing-kind historical source",
                                 "default_gate": {"start_us": .125, "end_us": .375}})
    assert record["provenance"]["source_manifest"] == manifest
    assert "kind" not in record["provenance"]["source_manifest"]
    assert "path_model" not in record["request"]["acquisition"]
    assert store.import_record(record) == record
    assert RecipeStore(tmp_path).get(record["recipe_id"]) == record
    after = {p.relative_to(path): hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob("*") if p.is_file()}
    assert after == before


@pytest.mark.parametrize("predicate", ["is_symlink", "is_junction"])
def test_source_link_guard_runs_before_any_verification_or_array_read(tmp_path, monkeypatch, predicate):
    _, identifier, _ = historical_source(tmp_path)
    redirected = tmp_path/identifier/"data.zarr"
    original = getattr(Path, predicate)
    def linked(path):
        return path == redirected or original(path)
    monkeypatch.setattr(Path, predicate, linked)
    def premature(*args, **kwargs):
        pytest.fail("Redirected source must fail its path guard before verification reads arrays.")
    monkeypatch.setattr(DatasetStore, "verify_complete", premature)
    monkeypatch.setattr(DatasetStore, "open_arrays", premature)
    with pytest.raises(ValueError, match="symbolic links|junctions"):
        RecipeStore(tmp_path).from_dataset({"dataset_id": identifier, "name": "Redirected source"})
    assert RecipeStore(tmp_path).list() == []


def test_recipe_from_queued_manifest_fails_completion_before_looking_for_arrays(tmp_path):
    identifier = str(uuid4())
    store = DatasetStore(tmp_path)
    store.create(identifier, request(), {"shape": [16, 16, 5], "tile_rows": 4,
        "total_bytes": 10240, "model_version": "sam-volume-0.7.0"})
    assert not (tmp_path/identifier/"data.zarr").exists()
    with pytest.raises((ValueError, RuntimeError), match="completed"):
        RecipeStore(tmp_path).from_dataset({"dataset_id": identifier, "name": "Queued source"})
