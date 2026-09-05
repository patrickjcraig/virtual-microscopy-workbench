"""Recipe identity, historical preservation and isolated one-variable cases."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

import numpy as np
import pytest

from virtual_microscopy.datasets import DatasetStore, json_sha256
from virtual_microscopy.hbm import compose_hbm
from virtual_microscopy.recipe_schemas import CaseProposal
from virtual_microscopy.recipes import RecipeStore, build_case_plan
from virtual_microscopy.sam_volume import estimate_sam, prepare_sam, iter_sam_tiles
from virtual_microscopy.volume_schemas import SamVolumeRequest


def request():
    return {"twin": {"name": "Recipe coupon", "size_mm": [4, 3, 1],
                     "objects": [{"id": "si", "name": "Silicon", "shape": "box", "material": "silicon",
                                  "center_mm": [2, 1.5, .5], "size_mm": [4, 3, .5]}]},
            "acquisition": {"scan_nx": 16, "scan_ny": 16, "depth_samples": 128,
                            "record_duration_us": .75, "frequency_mhz": 50, "focus_mm": .25}}


def saved(root, historical=False):
    config = SamVolumeRequest.model_validate(request())
    raw = config.model_dump(mode="json", exclude_none=True)
    if historical:
        raw["acquisition"].pop("path_model")
        raw["twin"]["legacy_geometry_note"] = "A historical field unsupported by today's strict constructor."
    store = DatasetStore(root)
    identifier = str(uuid4())
    store.create(identifier, raw, estimate_sam(config))
    prepared = prepare_sam(config)
    store.initialize_arrays(identifier, prepared.x_mm, prepared.y_mm, prepared.time_us, prepared.metadata)
    for tile in iter_sam_tiles(prepared):
        store.write_tile(identifier, *tile)
    return store.complete(identifier)


def hashes(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob("*") if p.is_file()}


def test_recipe_explicit_defaults_immutable_revision_roundtrip_and_reopen(tmp_path):
    store = RecipeStore(tmp_path)
    original = store.create({"name": "First", "request": request(), "default_gate": {"start_us": .2, "end_us": .5}})
    assert original["request"]["acquisition"]["path_model"] == "voxel_centers_v1"
    assert original["request"]["acquisition"]["sample_rate_mhz"] == 400
    assert store.import_record(json.loads(json.dumps(original))) == original
    assert len(store.list()) == 1
    revision = store.create({"name": "Second", "request": request(), "parent_recipe_id": original["recipe_id"]})
    assert revision["recipe_id"] != original["recipe_id"]
    assert revision["request_sha256"] == original["request_sha256"]
    assert store.get(original["recipe_id"]) == original
    assert RecipeStore(tmp_path).get(revision["recipe_id"]) == revision
    assert len(store.list()) == 2
    changed = deepcopy(original)
    changed["name"] = "Overwrite attempt"
    changed["recipe_sha256"] = json_sha256({k: v for k, v in changed.items() if k != "recipe_sha256"})
    with pytest.raises(ValueError, match="different immutable"):
        store.import_record(changed)
    assert store.get(original["recipe_id"]) == original


@pytest.mark.parametrize("mutation", ["nan", "unknown", "checksum", "path", "missing_default", "noncanonical_id"])
def test_strict_import_rejects_malformed_records_without_writes(tmp_path, mutation):
    store = RecipeStore(tmp_path)
    original = store.create({"name": "Base", "request": request()})
    changed = deepcopy(original)
    if mutation == "nan":
        changed["request"]["acquisition"]["focus_mm"] = float("nan")
    elif mutation == "unknown":
        changed["destination_path"] = "unrequested-write.json"
    elif mutation == "checksum":
        changed["request"]["acquisition"]["focus_mm"] = .6
    elif mutation == "path":
        changed["recipe_id"] = "../outside"
    elif mutation == "missing_default":
        changed.pop("provenance")
    else:
        changed["recipe_id"] = changed["recipe_id"].upper()
    with pytest.raises(ValueError):
        store.import_record(changed)
    assert store.get(original["recipe_id"]) == original and len(store.list()) == 1


def test_recipe_default_gate_uses_actual_recorded_sample_times(tmp_path):
    store = RecipeStore(tmp_path)
    body = request()
    body["acquisition"].update(record_start_us=.2, record_duration_us=.0511)
    valid = store.create({"name": "Endpoint", "request": body, "default_gate": {"start_us": .2, "end_us": .25}})
    assert valid["default_gate"]["end_us"] == .25
    for gate in ({"start_us": 0, "end_us": .2}, {"start_us": .24, "end_us": .2511},
                 {"start_us": .2003, "end_us": .2004}):
        with pytest.raises(ValueError, match="gate|Gate"):
            store.create({"name": "Rejected gate", "request": body, "default_gate": gate})


def test_historical_recipe_preserves_source_and_is_readable_without_current_constructor(tmp_path, monkeypatch):
    manifest = saved(tmp_path, historical=True)
    store = RecipeStore(tmp_path)
    dataset_path = tmp_path / manifest["dataset_id"]
    before = hashes(dataset_path)
    with monkeypatch.context() as patch:
        def forbidden(*args, **kwargs):
            raise AssertionError("Historical read called current constructor")
        patch.setattr(SamVolumeRequest, "model_validate", forbidden)
        record = store.from_dataset({"dataset_id": manifest["dataset_id"], "name": "Historical",
                                     "default_gate": {"start_us": .2, "end_us": .5}})
        assert record["request"] == manifest["request"]
        assert record["provenance"]["source_manifest"] == manifest
        assert store.get(record["recipe_id"]) == record
        assert store.import_record(record) == record
    assert hashes(dataset_path) == before
    with pytest.raises(ValueError, match="remains readable"):
        build_case_plan(tmp_path, {"recipe_id": record["recipe_id"], "field": "focus_mm", "values": [.2, .4]})
    # Report storage is independent of source presence; no source re-read on load.
    dataset_path.rename(tmp_path / "preserved-historical-source")
    assert RecipeStore(tmp_path).get(record["recipe_id"]) == record


@pytest.mark.parametrize("field,values", [("focus_mm", [.2, .4]), ("frequency_mhz", [25, 50]),
                                         ("fractional_bandwidth", [.4, .8]), ("depth_samples", [128, 256]),
                                         ("path_model", ["voxel_centers_v1", "continuous_columns_v1"]),
                                         ("include_defects", [False, True])])
def test_case_construction_changes_only_declared_acquisition_setting(tmp_path, field, values):
    record = RecipeStore(tmp_path).create({"name": "Sweep", "request": request()})
    plan = build_case_plan(tmp_path, {"recipe_id": record["recipe_id"], "field": field, "values": values})
    assert len(plan["cases"]) == 2
    for case, value in zip(plan["cases"], values):
        expected = deepcopy(record["request"])
        expected["acquisition"][field] = value
        assert case["request"] == expected
        assert case["estimate"]["total_bytes"] > 0
    assert plan["cases"][0]["differences"] == {"settings": [], "primitives": [], "twin_metadata": []}
    assert plan["cases"][1]["differences"]["settings"] == [{"field": field, "before": values[0], "after": values[1]}]
    assert plan["estimated_peak_bytes"] == max(case["estimate"]["estimated_peak_bytes"] for case in plan["cases"])
    assert plan["total_bytes"] == sum(case["estimate"]["total_bytes"] for case in plan["cases"])


@pytest.mark.parametrize("field,values", [("frequency_mhz", [50, 75]), ("focus_mm", [.25, 1.5])])
def test_invalid_case_rejects_whole_plan_without_changing_recipe(tmp_path, field, values):
    store = RecipeStore(tmp_path)
    record = store.create({"name": "Rejected", "request": request()})
    with pytest.raises(ValueError, match="No cases were admitted.*Case 2"):
        build_case_plan(tmp_path, {"recipe_id": record["recipe_id"], "field": field, "values": values})
    assert store.get(record["recipe_id"]) == record
    with sqlite3.connect(tmp_path / "catalog.sqlite3") as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='jobs'").fetchone() is None


@pytest.mark.parametrize("field,values", [("focus_mm", [.2, .2]), ("focus_mm", ["0.2", "0.4"]),
                                         ("focus_mm", [False, .2]), ("focus_mm", [.1]),
                                         ("focus_mm", [.1, .2, .3, .4, .5]), ("focus_mm", [float("inf"), .4]),
                                         ("depth_samples", [128.0, 256]), ("defect", [0, 1]),
                                         ("path_model", ["automatic", "continuous_columns_v1"])])
def test_case_values_are_explicit_strict_unique_and_bounded(field, values):
    with pytest.raises(ValueError):
        CaseProposal.model_validate({"recipe_id": str(uuid4()), "field": field, "values": values})


def test_inactive_continuous_depth_cannot_be_swept(tmp_path):
    config = request()
    config["acquisition"]["path_model"] = "continuous_columns_v1"
    record = RecipeStore(tmp_path).create({"name": "Continuous", "request": config})
    with pytest.raises(ValueError, match="inactive"):
        build_case_plan(tmp_path, {"recipe_id": record["recipe_id"], "field": "depth_samples", "values": [128, 1024]})


def test_isolated_hbm_defect_pair_retains_six_sites_and_every_other_primitive(tmp_path):
    twin = json.loads((Path(__file__).resolve().parents[1] / "examples/nvidia-h100-hbm6-microstructure.json").read_text("utf-8"))
    defects = [{"id": "selected", "kind": "missing_bump", "layer_index": 8, "row": 1, "column": 2, "enabled": True},
               {"id": "other", "kind": "missing_bump", "layer_index": 7, "row": 2, "column": 1, "enabled": True}]
    twin = compose_hbm(twin, "hbm-6", {"microstructure": {"defects": defects}})
    config = {"twin": twin, "acquisition": {"path_model": "continuous_columns_v1", "scan_nx": 16, "scan_ny": 16,
              "frequency_mhz": 100, "sample_rate_mhz": 800, "focus_mm": .55,
              "record_start_us": .2, "record_duration_us": .5, "roi_mm": [49.4, 39.85, 49.6, 40.15]}}
    record = RecipeStore(tmp_path).create({"name": "Isolated defect", "request": config})
    plan = build_case_plan(tmp_path, {"recipe_id": record["recipe_id"], "field": "defect", "values": [False, True],
                                    "assembly_id": "hbm-6", "defect_id": "selected"})
    a, b = (case["request"]["twin"] for case in plan["cases"])
    assert [s["id"] for s in a["hbm_assemblies"]] == [s["id"] for s in b["hbm_assemblies"]]
    assert len(a["hbm_assemblies"]) == 6
    assert [o for o in b["objects"] if o["id"] != "hbm-6-defect-selected"] == a["objects"]
    assert any(o["id"] == "hbm-6-defect-other" for o in a["objects"])
    assert [c["id"] for c in plan["cases"][1]["differences"]["primitives"]] == ["hbm-6-defect-selected"]
    assert plan["cases"][1]["differences"]["settings"] == []
    assert RecipeStore(tmp_path).get(record["recipe_id"]) == record
