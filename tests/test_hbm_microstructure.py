"""Explicit assumed HBM patches: geometry, local defects and safe composition."""

from copy import deepcopy
import json

import numpy as np
import pytest

from tools.build_h100_example import h100
from virtual_microscopy.hbm import compile_hbm_stack, compose_hbm, microstructure_summary
from virtual_microscopy.physics import LABELS, voxelize
from virtual_microscopy.schemas import HBMStack, Primitive, Twin


def patch(twin=None, **parameters):
    return compose_hbm(h100() if twin is None else twin, "hbm-6", {"microstructure": parameters})


def local_defect(kind="bump_void", **parameters):
    result = {"id": "cavity", "kind": kind, "row": 1, "column": 1, "layer_index": 1}
    if kind != "missing_bump":
        result["void_diameter_um"] = 6
    return result | parameters


def stack(twin):
    return next(item for item in twin["hbm_assemblies"] if item["id"] == "hbm-6")


def part(twin, identifier):
    return next(item for item in twin["objects"] if item["id"] == identifier)


def test_default_patch_has_independent_analytic_positions_and_six_sites():
    original = h100()
    edited = patch(original, center_offset_xy_um=[100, -200])
    assert len(original["objects"]) == 472
    assert len(edited["objects"]) == 574
    assert all(item["physical_present"] for item in edited["hbm_assemblies"])
    assert len(edited["hbm_assemblies"]) == 6
    assert edited["hbm_assemblies"][:5] == original["hbm_assemblies"][:5]
    assert [item for item in edited["objects"] if item.get("assembly_id") != "hbm-6"] == [
        item for item in original["objects"] if item.get("assembly_id") != "hbm-6"]
    assert edited["image_reference"] == original["image_reference"]
    nominal = [item for item in edited["objects"] if item.get("layer_role") in {"microbump", "tsv"}]
    assert sum(item["layer_role"] == "microbump" for item in nominal) == 48
    assert sum(item["layer_role"] == "tsv" for item in nominal) == 54
    assert all(item["shape"] == "cylinder" and item["role"] == "structure" for item in nominal)
    for index in range(1, 9):
        bump = part(edited, f"hbm-6-mb-{index:02}-r03-c02")
        via = part(edited, f"hbm-6-tsv-{index:02}-r03-c02")
        np.testing.assert_allclose(bump["center_mm"], [49.625, 39.85, .82 - .07 - (index-1)*.065 - .015/2], atol=1e-12)
        np.testing.assert_allclose(via["center_mm"], [49.625, 39.85, .82 - .07 - index*.015 - (index-.5)*.05], atol=1e-12)
        assert bump["size_mm"] == [.025, .025, .015] and bump["material"] == "solder"
        assert via["size_mm"] == [.01, .01, .05] and via["material"] == "copper"
    assert part(edited, "hbm-6-tsv-00-r01-c01")["center_mm"] == [49.575, 39.75, .785]
    assert part(edited, "hbm-6-tsv-00-r01-c01")["size_mm"] == [.01, .01, .07]
    assert min(item["center_mm"][2] - item["size_mm"][2]/2 for item in nominal) == pytest.approx(.23)
    assert max(item["center_mm"][2] + item["size_mm"][2]/2 for item in nominal) == pytest.approx(.82)


def test_compilation_roundtrip_absence_and_electrical_state_preserve_materials():
    original = h100()
    authored = patch(original, defects=[local_defect("missing_bump")])
    assert compose_hbm(authored, "hbm-6", {"functional_state": "disabled"})["objects"] == authored["objects"]
    assert compose_hbm(authored, "hbm-6", {})["objects"] == authored["objects"]
    serialized = Twin.model_validate(authored).model_dump(mode="json", exclude_none=True)
    assert Twin.model_validate_json(json.dumps(serialized)).model_dump(mode="json", exclude_none=True) == serialized
    disabled = patch(authored, enabled=False)
    assert disabled["objects"] == original["objects"]
    assert stack(disabled)["microstructure"]["defects"] == stack(authored)["microstructure"]["defects"]
    assert patch(disabled, enabled=True)["objects"] == authored["objects"]
    assert compile_hbm_stack(stack(original)) == compile_hbm_stack(stack(original) | {"microstructure": None})
    assert original == h100()


def test_summary_describes_applied_features_and_disabled_identity():
    authored = patch(defects=[local_defect("missing_bump", id="missing", row=3, column=2, layer_index=8),
                             local_defect("tsv_void", id="disabled", layer_index=0, enabled=False)])
    summary = microstructure_summary(stack(authored))
    assert summary["enabled"] and summary["model_version"] == "hbm-explicit-patch-1"
    assert summary["nominal_feature_count"] == 102 and summary["defect_count"] == 1
    assert summary["roi_mm"] == pytest.approx([49.4375, 39.9125, 49.5625, 40.0875])
    assert summary["feature_bounds_mm"] == pytest.approx([49.4625, 39.9375, .23, 49.5375, 40.0625, .82])
    assert summary["defects"][0] == {"id": "missing", "kind": "missing_bump", "target_id": "hbm-6-mb-08-r03-c02",
                                     "primitive_id": "hbm-6-defect-missing", "enabled": True}
    for feature in summary["features"]:
        target = part(authored, feature["id"])
        assert target["center_mm"] == feature["center_mm"] and target["size_mm"] == feature["size_mm"]
    inactive = microstructure_summary(stack(patch(authored, enabled=False)))
    assert not inactive["enabled"] and inactive["nominal_feature_count"] == inactive["defect_count"] == 0
    assert inactive["roi_mm"] is inactive["feature_bounds_mm"] is None
    assert inactive["features"] == [] and inactive["defects"] == summary["defects"]
    absent = microstructure_summary(stack(h100()))
    assert absent["features"] == absent["defects"] == [] and absent["model_version"] is None


def test_roi_is_clipped_to_small_footprint_without_losing_minimum_scan_extent():
    authored = HBMStack.model_validate(stack(h100()) | {"footprint_mm": [.05, .05], "center_xy_mm": [.025, .025],
        "microstructure": {"rows": 1, "columns": 1, "bump_diameter_um": 10, "tsv_diameter_um": 5,
                            "center_offset_xy_um": [20, -20]}})
    summary = microstructure_summary(authored.model_dump())
    assert summary["roi_mm"] == pytest.approx([0, 0, .05, .05])
    assert summary["feature_bounds_mm"][0] >= 0 and summary["feature_bounds_mm"][4] <= .05


def test_local_defects_follow_translation_and_layer_changes_but_global_defects_do_not():
    authored = patch(defects=[local_defect("missing_bump", layer_index=3),
                             local_defect("tsv_void", id="via", layer_index=0, row=2, column=2)])
    moved = compose_hbm(authored, "hbm-6", {"center_xy_mm": [49, 39], "bottom_z_mm": .9,
                                            "die_thickness_um": 45, "base_thickness_um": 80, "gap_um": 20})
    for identifier in ("hbm-6-defect-cavity", "hbm-6-defect-via"):
        before, after = part(authored, identifier), part(moved, identifier)
        np.testing.assert_allclose(np.asarray(after["center_mm"][:2]) - before["center_mm"][:2], [-.5, -1])
    assert part(moved, "hbm-6-defect-cavity")["center_mm"][2] == pytest.approx(.9-.08-2*(.045+.02)-.02/2)
    assert part(moved, "hbm-6-defect-cavity")["size_mm"][2] == .02
    assert part(moved, "hbm-6-defect-via")["center_mm"][2] == pytest.approx(.9-.08/2)
    globals_only = lambda twin: [item for item in twin["objects"] if item["role"] == "defect" and item.get("assembly_id") is None]
    assert globals_only(authored) == globals_only(moved)


def test_defect_materials_restore_exact_nominal_occupancy_when_excluded():
    defects = [local_defect("missing_bump", id="missing"),
               local_defect("bump_void", id="bump", row=2, column=2, layer_index=2, void_diameter_um=10),
               local_defect("tsv_void", id="via", row=3, column=1, layer_index=0)]
    defective = patch(defects=defects)
    intact = patch()
    roi = microstructure_summary(stack(defective))["roi_mm"]
    good = voxelize(intact, 64, include_defects=False, roi_mm=roi, depth_samples=1024)
    excluded = voxelize(defective, 64, include_defects=False, roi_mm=roi, depth_samples=1024)
    bad = voxelize(defective, 64, include_defects=True, roi_mm=roi, depth_samples=1024)
    np.testing.assert_array_equal(good.labels, excluded.labels)
    for identifier, nominal_material, defective_material in (("missing", "solder", "epoxy"), ("bump", "solder", "air"), ("via", "copper", "air")):
        target = part(defective, f"hbm-6-defect-{identifier}")
        x, y, z = np.floor((np.array(target["center_mm"]) - bad.origin_mm) / bad.pitch_mm).astype(int)
        assert good.labels[y, x, z] == LABELS[nominal_material]
        assert bad.labels[y, x, z] == LABELS[defective_material]
    positions = [index for index, item in enumerate(defective["objects"]) if item.get("assembly_id") == "hbm-6"]
    assert positions == list(range(min(positions), max(positions)+1))


@pytest.mark.parametrize("mutation", ["missing", "material", "position", "descriptor", "reordered", "defect_shape"])
def test_import_rejects_tampered_explicit_geometry(mutation):
    authored = patch(defects=[local_defect()])
    target = part(authored, "hbm-6-mb-01-r01-c01")
    if mutation == "missing":
        authored["objects"].remove(target)
    elif mutation == "material":
        target["material"] = "copper"
    elif mutation == "position":
        target["center_mm"][0] += .001
    elif mutation == "descriptor":
        stack(authored)["microstructure"]["pitch_x_um"] = 55
    elif mutation == "reordered":
        index = authored["objects"].index(target)
        authored["objects"][index:index+2] = reversed(authored["objects"][index:index+2])
    else:
        part(authored, "hbm-6-defect-cavity")["shape"] = "cylinder"
    with pytest.raises(ValueError, match="compiled primitives disagree"):
        Twin.model_validate(authored)


@pytest.mark.parametrize("parameters", [
    {"columns": 8, "rows": 8}, {"columns": 0}, {"rows": 2.0}, {"rows": True},
    {"pitch_x_um": 25}, {"tsv_diameter_um": 50}, {"center_offset_xy_um": [4000, 0]},
    {"pitch_y_um": float("inf")}, {"center_offset_xy_um": [float("nan"), 0]},
    {"enabled": 1}, {"model_version": "invented"}, {"unknown_field": True}, {"pitch_x_um": None},
    {"defects": [local_defect("missing_bump", void_diameter_um=5)]},
    {"defects": [local_defect("bump_void", void_diameter_um=15)]},
    {"defects": [local_defect("tsv_void", void_diameter_um=10)]},
    {"defects": [local_defect("tsv_void", void_diameter_um=None)]},
    {"defects": [local_defect("missing_bump", layer_index=0)]},
    {"defects": [local_defect(row=4, enabled=False)]},
    {"defects": [local_defect(id="invalid space")]},
    {"defects": [local_defect(), local_defect(id="other", kind="missing_bump", void_diameter_um=None)]},
    {"defects": [local_defect(), local_defect(row=2)]},
    {"defects": [local_defect(id=f"void{i}", row=1+(i%3), column=1+i//3) for i in range(5)]},
])
def test_invalid_microstructure_patch_is_rejected_without_mutating_twin(parameters):
    original = h100()
    before = deepcopy(original)
    with pytest.raises(ValueError):
        patch(original, **parameters)
    assert original == before


def test_disabled_and_partial_authored_parameters_validate_against_current_stack():
    authored = patch(rows=6, columns=1)
    # The target row is valid in the prior authored six-row lattice, not in the default three-row lattice.
    authored = patch(authored, defects=[local_defect(row=6)])
    assert stack(authored)["microstructure"]["rows"] == 6
    assert microstructure_summary(stack(authored))["defect_count"] == 1
    with pytest.raises(ValueError, match="row/column"):
        patch(authored, rows=3)
    twelve = compose_hbm(h100(), "hbm-6", {"die_count": 12, "die_thickness_um": 34, "gap_um": 8,
        "microstructure": {"rows": 2, "columns": 2, "enabled": False,
                            "defects": [local_defect(layer_index=12, enabled=False)]}})
    with pytest.raises(ValueError, match="layer_index"):
        compose_hbm(twelve, "hbm-6", {"die_count": 8, "die_thickness_um": 50, "gap_um": 15})
    enabled = patch(twelve, enabled=True)
    assert len(enabled["objects"]) == 580
    with pytest.raises(ValueError, match="600"):
        patch(enabled, rows=3)


def test_only_one_active_patch_and_presence_are_enforced():
    authored = patch(rows=1, columns=1)
    with pytest.raises(ValueError, match="one enabled"):
        compose_hbm(authored, "hbm-1", {"microstructure": {"rows": 1, "columns": 1}})
    disabled_other = compose_hbm(authored, "hbm-1", {"microstructure": {"enabled": False}})
    assert disabled_other["objects"] == authored["objects"]
    with pytest.raises(ValueError, match="physically present"):
        compose_hbm(authored, "hbm-6", {"physical_present": False})
    absent = compose_hbm(authored, "hbm-6", {"physical_present": False, "microstructure": {"enabled": False}})
    assert not any(item.get("assembly_id") == "hbm-6" for item in absent["objects"])
    with pytest.raises(ValueError, match="physically present"):
        patch(absent, enabled=True)


def test_restoring_another_site_preserves_local_defect_block_and_global_precedence():
    authored = patch(defects=[local_defect()])
    absent = compose_hbm(authored, "hbm-1", {"physical_present": False})
    restored = compose_hbm(absent, "hbm-1", {"physical_present": True})
    Twin.model_validate(restored)
    selected = lambda twin: [item for item in twin["objects"] if item.get("assembly_id") == "hbm-6"]
    assert selected(authored) == selected(restored)
    global_defects = [i for i, item in enumerate(restored["objects"]) if item["role"] == "defect" and not item.get("assembly_id")]
    new_site = [i for i, item in enumerate(restored["objects"]) if item.get("assembly_id") == "hbm-1"]
    assert max(new_site) < min(global_defects)


def test_maximum_authored_identifier_lengths_fit_primitive_schema():
    authored = HBMStack.model_validate(stack(h100()) | {"id": "hbm-" + "1"*60, "name": "H"*120,
        "microstructure": {"defects": [local_defect(id="a"*20)]}})
    for item in compile_hbm_stack(authored.model_dump()):
        Primitive.model_validate(item)
