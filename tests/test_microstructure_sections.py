"""Independent physical-coordinate checks for bounded HBM feature sections."""
from copy import deepcopy

import numpy as np
import pytest
from fastapi.testclient import TestClient

from tools.build_h100_example import h100
from virtual_microscopy.hbm import compose_hbm
from virtual_microscopy.inspection import HBMSectionRequest, material_section
from virtual_microscopy.physics import LABELS
from virtual_microscopy.server import app


@pytest.fixture
def patch():
    return compose_hbm(h100(), "hbm-6", {"microstructure": {"center_offset_xy_um": [130, -80]}})


def section(twin, **changes):
    return material_section(HBMSectionRequest.model_validate({"twin": twin, "assembly_id": "hbm-6",
        "feature_id": "hbm-6-mb-04-r02-c02", **changes}))


def label_at(view, u, z):
    image = np.asarray(view["image"])
    u0, u1, z0, z1 = view["extent_mm"]
    col = int((u-u0)/(u1-u0)*image.shape[1])
    row = int((z-z0)/(z1-z0)*image.shape[0])
    return image[row, col]


@pytest.mark.parametrize("axis", ["xz", "yz"])
def test_off_center_bump_section_uses_global_feature_plane(patch, axis):
    # Independent placement: HBM6 center (49.5,40), patch offset (+.13,-.08),
    # column2 of two at +.025; row2 of three at 0. Gap4 is below three .065 mm periods.
    x, y, z = 49.655, 39.92, .82-.07-3*.065-.015/2
    view = section(patch, axis=axis)
    assert view["fixed_coordinate_mm"] == pytest.approx(y if axis == "xz" else x)
    assert view["feature"]["center_mm"] == pytest.approx([x, y, z])
    assert label_at(view, x if axis == "xz" else y, z) == LABELS["solder"]
    assert view["mode"] == "material_geometry"
    assert view["pixel_pitch_um"][0] < 1
    assert view["pixel_pitch_um"][1] < 1


@pytest.mark.parametrize("axis", ["xz", "yz"])
def test_selected_tsv_and_surrounding_silicon_in_same_section(patch, axis):
    x, y, z = 49.655, 39.92, .82-.07-3*.065-.015-.05/2
    view = section(patch, axis=axis, feature_id="hbm-6-tsv-04-r02-c02")
    coordinate = x if axis == "xz" else y
    assert label_at(view, coordinate, z) == LABELS["copper"]
    assert label_at(view, coordinate+.015, z) == LABELS["silicon"]
    assert view["fixed_coordinate_mm"] == pytest.approx(y if axis == "xz" else x)


@pytest.mark.parametrize("kind,diameter,feature_id,z,nominal,replacement", [
    ("missing_bump", None, "hbm-6-mb-04-r02-c02", .5475, "solder", "epoxy"),
    ("bump_void", 8, "hbm-6-mb-04-r02-c02", .5475, "solder", "air"),
    ("tsv_void", 6, "hbm-6-tsv-04-r02-c02", .515, "copper", "air"),
])
def test_material_section_shows_actual_defect_occupancy_and_exclusion(patch, kind, diameter, feature_id, z, nominal, replacement):
    micro = deepcopy(patch["hbm_assemblies"][-1]["microstructure"])
    defect = {"id": "local", "kind": kind, "row": 2, "column": 2, "layer_index": 4}
    if diameter is not None:
        defect["void_diameter_um"] = diameter
    micro["defects"] = [defect]
    changed = compose_hbm(patch, "hbm-6", {"microstructure": micro})
    for axis, coordinate in (("xz", 49.655), ("yz", 39.92)):
        defective = section(changed, axis=axis, feature_id=feature_id)
        intact = section(changed, axis=axis, feature_id=feature_id, include_defects=False)
        assert label_at(defective, coordinate, z) == LABELS[replacement]
        assert label_at(intact, coordinate, z) == LABELS[nominal]


def test_explicit_section_bounds_and_fixed_plane_retain_package_context(patch):
    patch["objects"].append({"id": "section-landmark", "name": "Independent section landmark", "role": "structure",
        "shape": "box", "material": "copper", "center_mm": [49.65,39.92,1.2], "size_mm": [.04,.02,.04]})
    view = section(patch, feature_id=None, fixed_coordinate_mm=39.92,
                   bounds_mm=[49.6,49.7,1.1,1.3], resolution=256)
    assert view["extent_mm"] == [49.6,49.7,1.1,1.3]
    assert view["fixed_coordinate_mm"] == 39.92
    assert label_at(view,49.65,1.2) == LABELS["copper"]
    assert view["pixel_pitch_um"] == pytest.approx([100/256,200/256])


def test_feature_section_reports_undersampling_when_broad_bounds_requested(patch):
    view = section(patch, bounds_mm=[0,60,0,2.65], resolution=128)
    assert any("selected feature is below two" in warning for warning in view["warnings"])


def test_unrepresented_local_void_warns_even_when_nominal_host_is_sampled(patch):
    twin = compose_hbm(patch, "hbm-6", {"microstructure": {"defects": [
        {"id":"tiny","kind":"bump_void","row":2,"column":2,"layer_index":4,"void_diameter_um":.1}]}})
    defective = section(twin)
    intact = section(twin, include_defects=False)
    # The void falls between these section sample centers: equality alone must
    # not be interpreted as proof that the underlying geometry is intact.
    np.testing.assert_array_equal(defective["image"], intact["image"])
    assert any("hbm-6-defect-tiny" in warning and "below two" in warning for warning in defective["warnings"])
    assert not any("hbm-6-defect-tiny" in warning for warning in intact["warnings"])


@pytest.mark.parametrize("changes", [
    {"bounds_mm": [1,1,0,1]}, {"bounds_mm": [-1,2,0,1]}, {"bounds_mm": [0,61,0,1]},
    {"bounds_mm": [0,1,0,3]}, {"bounds_mm": [0,1,1,.5]}, {"bounds_mm": [0,1,float("nan"),1]},
    {"fixed_coordinate_mm": 40}, {"feature_id": "hbm-1-mb-04-r02-c02"},
    {"feature_id": "hbm-6-base"}, {"feature_id": None,"fixed_coordinate_mm": 61},
])
def test_bad_feature_or_section_domain_rejected_without_changing_twin(patch, changes):
    original = deepcopy(patch)
    with pytest.raises(ValueError):
        section(patch, **changes)
    assert patch == original


def test_summary_and_section_api_use_canonical_geometry(patch, tmp_path, monkeypatch):
    monkeypatch.setenv("VM_DATA_ROOT", str(tmp_path))
    with TestClient(app) as client:
        summary = client.post("/api/hbm/microstructure",json={"twin":patch,"assembly_id":"hbm-6"})
        assert summary.status_code == 200, summary.text
        details = summary.json()
        assert details["primitive_count"] == 574
        assert details["remaining_primitives"] == 26
        assert details["microstructure"]["nominal_feature_count"] == 102
        assert len(details["microstructure"]["features"]) == 102
        assert details["microstructure"]["roi_mm"] == pytest.approx([49.5675,39.8325,49.6925,40.0075])
        response = client.post("/api/hbm/section",json={"twin":patch,"assembly_id":"hbm-6",
            "feature_id":"hbm-6-mb-04-r02-c02","axis":"yz"})
        assert response.status_code == 200, response.text
        assert response.json()["fixed_coordinate_mm"] == pytest.approx(49.655)
        assert client.post("/api/hbm/microstructure",json={"twin":patch,"assembly_id":"hbm-99"}).status_code == 422
        assert client.post("/api/hbm/section",json={"twin":patch,"assembly_id":"hbm-6",
            "feature_id":"missing"}).status_code == 422


def test_disabled_patch_has_no_active_section_or_roi(patch, tmp_path, monkeypatch):
    monkeypatch.setenv("VM_DATA_ROOT", str(tmp_path))
    micro = deepcopy(patch["hbm_assemblies"][-1]["microstructure"])
    micro["enabled"] = False
    twin = compose_hbm(patch,"hbm-6",{"microstructure":micro})
    with pytest.raises(ValueError,match="active nominal"):
        section(twin)
    with TestClient(app) as client:
        result = client.post("/api/hbm/microstructure",json={"twin":twin,"assembly_id":"hbm-6"}).json()
    assert result["primitive_count"] == 472
    assert result["microstructure"]["roi_mm"] is None
