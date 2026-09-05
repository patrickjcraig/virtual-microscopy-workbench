"""Explicit instrument recipes, frozen source identity, and isolated X-ray cases."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import pytest
import zarr

from virtual_microscopy.datasets import json_sha256
from virtual_microscopy.hbm import compose_hbm
from virtual_microscopy.recipe_schemas import CaseProposal
from virtual_microscopy.recipes import RecipeStore, build_case_plan
from virtual_microscopy.xray_datasets import XrayDatasetStore
from virtual_microscopy.xray_schemas import XrayVolumeRequest
from virtual_microscopy.xray_volume import estimate_xray, iter_xray_views, prepare_xray
from test_recipes import hashes, request as sam_request
from test_xray_batch_jobs import xray_request


def request(**changes):
    return xray_request(**changes).model_dump(mode="json", exclude_none=True)


def saved(root, historical=False):
    config = xray_request(views=2)
    raw = config.model_dump(mode="json", exclude_none=True)
    if historical:
        raw["twin"]["historical_annotation"] = "Unsupported by today's constructor"
    store, identifier = XrayDatasetStore(root), str(uuid4())
    store.create(identifier, raw, estimate_xray(config))
    prepared = prepare_xray(config)
    store.initialize_arrays(identifier, prepared)
    for view in iter_xray_views(prepared):
        store.write_view(identifier, *view)
    return store.complete(identifier)


def test_complete_xray_defaults_roundtrip_revisions_and_mixed_catalog(tmp_path):
    store = RecipeStore(tmp_path)
    body = request(detector_offset_u_mm=.1, detector_offset_v_mm=-.2, rotation_center_mm=[1, 1, .4])
    original = store.create({"name": "Projection recipe", "request": body})
    assert original["kind"] == "xray_acquisition_recipe"
    assert original["request"] == body and original["default_gate"] is None
    assert store.import_record(json.loads(json.dumps(original))) == original
    revision = store.create({"name": "Revision", "request": body, "parent_recipe_id": original["recipe_id"]})
    assert revision["request_sha256"] == original["request_sha256"]
    assert revision["recipe_id"] != original["recipe_id"]
    sam = store.create({"name": "SAM", "request": sam_request()})
    assert sam["kind"] == "sam_acquisition_recipe" and "kind" not in sam["request"]
    assert {r["kind"] for r in store.list()} == {"sam_acquisition_recipe", "xray_acquisition_recipe"}
    assert RecipeStore(tmp_path).get(original["recipe_id"]) == original
    with pytest.raises(ValueError, match="acquisition kind"):
        store.create({"name": "Wrong instrument", "request": body, "parent_recipe_id": sam["recipe_id"]})
    with pytest.raises(ValueError, match="time gate"):
        store.create({"name": "Wrong gate", "request": body, "default_gate": {"start_us": .1, "end_us": .2}})


def test_xray_must_be_explicit_and_record_kind_cannot_be_relabelled(tmp_path):
    store = RecipeStore(tmp_path)
    raw = request()
    raw.pop("kind")
    with pytest.raises(ValueError):
        store.create({"name": "Ambiguous", "request": raw})
    original = store.create({"name": "Valid", "request": request()})
    forged = deepcopy(original)
    forged["recipe_id"] = str(uuid4())
    forged["kind"] = "sam_acquisition_recipe"
    forged["recipe_sha256"] = json_sha256({k: v for k, v in forged.items() if k != "recipe_sha256"})
    with pytest.raises(ValueError, match="matching explicit"):
        store.import_record(forged)
    assert len(store.list()) == 1


@pytest.mark.parametrize("field,values", [("energy_kev", [60, 100]), ("photons", [1000, 10000]),
    ("detector_fwhm_mm", [0, .1]), ("geometry_nx", [16, 32]), ("geometry_ny", [16, 32]),
    ("geometry_nz", [32, 64]), ("noise", [False, True]), ("seed", [0, 4294967295]),
    ("include_defects", [False, True])])
def test_xray_cases_change_only_declared_setting_and_preserve_detector_pose(tmp_path, field, values):
    record = RecipeStore(tmp_path).create({"name": "Sweep", "request": request(
        detector_width_mm=6, detector_height_mm=2, detector_offset_u_mm=.2,
        detector_offset_v_mm=-.1, rotation_center_mm=[1, 1, .4], angle_start_deg=-20, angle_span_deg=170)})
    plan = build_case_plan(tmp_path, {"recipe_id": record["recipe_id"], "field": field, "values": values})
    for case, value in zip(plan["cases"], values):
        expected = deepcopy(record["request"])
        expected["acquisition"][field] = value
        assert case["request"] == expected
        assert case["estimate"]["kind"] == "xray_projection_volume"
    assert plan["cases"][1]["differences"]["settings"] == [{"field": field, "before": values[0], "after": values[1]}]
    assert plan["cases"][1]["differences"]["primitives"] == []


@pytest.mark.parametrize("kind,field,values", [("xray", "focus_mm", [.2, .4]),
    ("xray", "path_model", ["voxel_centers_v1", "continuous_columns_v1"]),
    ("sam", "photons", [1000, 2000]), ("sam", "noise", [False, True])])
def test_instrument_specific_case_fields_reject_without_jobs(tmp_path, kind, field, values):
    record = RecipeStore(tmp_path).create({"name": "Instrument", "request": request() if kind == "xray" else sam_request()})
    with pytest.raises(ValueError, match="not an active supported"):
        build_case_plan(tmp_path, {"recipe_id": record["recipe_id"], "field": field, "values": values})
    assert not any(p.is_dir() for p in tmp_path.iterdir())


@pytest.mark.parametrize("field,values", [("photons", [1000.0, 2000]), ("seed", [True, 2]),
    ("geometry_nz", ["32", 64]), ("noise", [0, 1]), ("noise", [False, True, 2])])
def test_new_case_types_require_explicit_values(field, values):
    with pytest.raises(ValueError):
        CaseProposal.model_validate({"recipe_id": str(uuid4()), "field": field, "values": values})


def test_historical_xray_source_preserved_without_constructor_or_forward_model(tmp_path, monkeypatch):
    manifest = saved(tmp_path, historical=True)
    identifier = manifest["dataset_id"]
    before = hashes(tmp_path / identifier)
    store = RecipeStore(tmp_path)
    with monkeypatch.context() as patch:
        def forbidden(*args, **kwargs):
            raise AssertionError("Historical load invoked a current constructor or acquisition")
        patch.setattr(XrayVolumeRequest, "model_validate", forbidden)
        patch.setattr("virtual_microscopy.xray_volume.prepare_xray", forbidden)
        record = store.from_dataset({"dataset_id": identifier, "name": "Historical projections"})
        assert record["request"] == manifest["request"]
        assert record["provenance"]["source_manifest"] == manifest
        assert record["kind"] == "xray_acquisition_recipe"
        assert store.import_record(record) == record
        assert store.get(record["recipe_id"]) == record
    assert hashes(tmp_path / identifier) == before
    with pytest.raises(ValueError, match="remains readable"):
        build_case_plan(tmp_path, {"recipe_id": record["recipe_id"], "field": "energy_kev", "values": [60, 80]})
    with pytest.raises(ValueError, match="time gate"):
        store.from_dataset({"dataset_id": identifier, "name": "Invalid gate", "default_gate": {"start_us": .1, "end_us": .2}})
    (tmp_path / identifier).rename(tmp_path / "archived-source")
    assert RecipeStore(tmp_path).get(record["recipe_id"]) == record


@pytest.mark.parametrize("name", ["u_mm", "detector_center_mm"])
def test_recipe_source_rejects_oversized_coordinate_chunks_before_decoding(tmp_path, monkeypatch, name):
    manifest = saved(tmp_path)
    identifier = manifest["dataset_id"]
    group = zarr.open_group(str(tmp_path/identifier/"data.zarr"), mode="a")
    values = group[name][:]
    del group[name]
    chunks = (1_000_000,) if values.ndim == 1 else (1_000_000, 3)
    group.create_array(name, data=values, chunks=chunks)
    before = hashes(tmp_path/identifier)
    original = zarr.Array.__getitem__
    def guarded(array, key):
        if array.path == name:
            pytest.fail("Oversized source coordinate chunks must fail before decoding")
        return original(array, key)
    monkeypatch.setattr(zarr.Array, "__getitem__", guarded)
    with pytest.raises(ValueError, match="canonical chunk layout"):
        RecipeStore(tmp_path).from_dataset({"dataset_id": identifier, "name": "Oversized coordinate chunk"})
    assert RecipeStore(tmp_path).list() == []
    assert hashes(tmp_path/identifier) == before


def test_isolated_xray_hbm_toggle_preserves_other_defects_and_all_six_sites(tmp_path):
    twin = json.loads((Path(__file__).resolve().parents[1] / "examples/nvidia-h100-hbm6-microstructure.json").read_text("utf-8"))
    defects = [{"id": "selected", "kind": "missing_bump", "layer_index": 8, "row": 1, "column": 2, "enabled": True},
               {"id": "other", "kind": "missing_bump", "layer_index": 7, "row": 2, "column": 1, "enabled": True}]
    twin = compose_hbm(twin, "hbm-6", {"microstructure": {"defects": defects}})
    record = RecipeStore(tmp_path).create({"name": "Six HBM sites", "request": request() | {"twin": twin}})
    plan = build_case_plan(tmp_path, {"recipe_id": record["recipe_id"], "field": "defect", "values": [False, True],
                                    "assembly_id": "hbm-6", "defect_id": "selected"})
    a, b = [c["request"]["twin"] for c in plan["cases"]]
    assert len(a["hbm_assemblies"]) == len(b["hbm_assemblies"]) == 6
    assert [o for o in b["objects"] if o["id"] != "hbm-6-defect-selected"] == a["objects"]
    assert any(o["id"] == "hbm-6-defect-other" for o in a["objects"])
    assert [d["id"] for d in plan["cases"][1]["differences"]["primitives"]] == ["hbm-6-defect-selected"]
