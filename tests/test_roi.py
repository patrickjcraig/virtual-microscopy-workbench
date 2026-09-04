"""ROI checks use global asymmetric landmarks and analytical path lengths.

An ROI refines lateral sampling; it must not truncate the material traversed
through the package thickness or turn global coordinates into local ones.
These are synthetic numerical checks, not experimental validation.
"""

from copy import deepcopy

import numpy as np
import pytest
from fastapi.testclient import TestClient

from tools.build_h100_example import h100
from virtual_microscopy.materials import linear_attenuation_mm
from virtual_microscopy.physics import LABELS, acoustic_echoes, simulate, voxelize
from virtual_microscopy.schemas import SimulationRequest
from virtual_microscopy.server import app


client = TestClient(app)


def box(identifier, material, center, size):
    return {"id": identifier, "name": identifier, "shape": "box", "material": material,
            "center_mm": center, "size_mm": size, "role": "structure"}


def layered_coupon():
    # Both layers span the whole specimen in x/y, including outside the ROI.
    # The first 0.2 mm and final 0.8 mm are immersion water (no primitive).
    return {"schema_version": 1, "name": "Layered ROI reference", "size_mm": [8, 6, 1.6],
            "objects": [box("copper", "copper", [4, 3, 0.3], [8, 6, 0.2]),
                        box("silicon", "silicon", [4, 3, 0.6], [8, 6, 0.4])]}


def acquisition(twin, **changes):
    settings = {"resolution": 64, "noise": False, "probe_x_mm": 3,
                "probe_y_mm": 4, "focus_mm": 0.4, "gate_start_us": 0,
                "gate_end_us": 0.9, **changes}
    request = SimulationRequest.model_validate({"twin": twin, "settings": settings})
    return request.settings.model_dump(mode="json")


def test_translated_roi_keeps_full_thickness_attenuation_and_acoustic_path():
    twin = layered_coupon()
    full = simulate(twin, acquisition(twin))
    cropped = simulate(twin, acquisition(twin, roi_mm=[2, 3, 4, 5]))
    expected = np.exp(-0.2 * linear_attenuation_mm("copper", 80)
                      - 0.4 * linear_attenuation_mm("silicon", 80))
    np.testing.assert_allclose(cropped["xray"]["image"], expected, rtol=1e-12)
    # A uniform lateral slab has the same path and time zero at any field size.
    np.testing.assert_allclose(cropped["ascan"]["time_us"], full["ascan"]["time_us"], atol=1e-12)
    np.testing.assert_allclose(cropped["ascan"]["amplitude"], full["ascan"]["amplitude"], atol=1e-7)
    assert cropped["metadata"]["voxel_depth_um"] == full["metadata"]["voxel_depth_um"]
    assert cropped["metadata"]["pixel_pitch_um"] == pytest.approx([31.25, 31.25])
    grid = voxelize(twin, 64, roi_mm=[2, 3, 4, 5])
    echoes = acoustic_echoes(grid, 50, 0.4, apply_focus=False)
    times = echoes.times_us[(echoes.rows == 32) & (echoes.cols == 32)]
    # Time zero stays at the package plane, not at the first ROI material.
    assert times.tolist() == pytest.approx([
        2 * 0.2 / 1.48,
        2 * 0.2 / 1.48 + 2 * 0.2 / 4.66,
        2 * 0.2 / 1.48 + 2 * 0.2 / 4.66 + 2 * 0.4 / 8.43,
    ], abs=1e-12)


def test_asymmetric_roi_landmarks_extents_and_global_probe_coordinates():
    twin = layered_coupon()
    twin["objects"] = [
        box("narrow-copper", "copper", [2.375, 3.625, 0.8], [0.25, 0.75, 1.6]),
        box("silicon-corner", "silicon", [3.625, 4.625, 0.8], [0.25, 0.25, 1.6]),
    ]
    config = acquisition(twin, roi_mm=[2, 3, 4, 5], probe_x_mm=2.39, probe_y_mm=3.51)
    result = simulate(twin, config)
    image = np.asarray(result["xray"]["image"])
    # Landmarks at independent row/column positions detect transpose/origin bugs.
    assert image[16, 12] == pytest.approx(np.exp(-1.6 * linear_attenuation_mm("copper", 80)), abs=1e-6)
    assert image[52, 52] == pytest.approx(np.exp(-1.6 * linear_attenuation_mm("silicon", 80)), abs=1e-6)
    assert image[52, 12] == pytest.approx(1, abs=1e-6)
    for modality in ("xray", "sam"):
        assert result[modality]["extent_mm"] == [2, 4, 3, 5]
    assert result["ascan"]["probe_mm"] == [2.39, 3.51]
    assert result["ascan"]["sampled_probe_mm"] == pytest.approx([2.390625, 3.515625])
    assert result["bscan"]["extent"][:2] == [2, 4]
    assert result["bscan"]["y_mm"] == pytest.approx(3.515625)


def test_independent_depth_sampling_recovers_thin_layer_without_changing_lateral_grid():
    twin = layered_coupon()
    # Exactly four 1024-grid cells, between neighboring 128-grid sample centers.
    twin["objects"] = [box("thin-copper", "copper", [4, 3, 0.5125], [8, 6, 0.00625])]
    coarse = simulate(twin, acquisition(twin, roi_mm=[2, 3, 4, 5], depth_samples=128))
    fine = simulate(twin, acquisition(twin, roi_mm=[2, 3, 4, 5], depth_samples=1024))
    np.testing.assert_allclose(coarse["xray"]["image"], 1, atol=1e-12)
    np.testing.assert_allclose(fine["xray"]["image"],
                               np.exp(-0.00625 * linear_attenuation_mm("copper", 80)), rtol=1e-12)
    assert coarse["metadata"]["pixel_pitch_um"] == fine["metadata"]["pixel_pitch_um"]
    assert fine["metadata"]["voxel_depth_um"] == pytest.approx(1.5625)
    assert fine["metadata"]["grid_shape"][-1] == 1024
    assert np.asarray(fine["xray"]["image"]).shape == (64, 64)


def test_omitted_optional_roi_and_depth_settings_preserve_legacy_arrays():
    twin = layered_coupon()
    config = acquisition(twin)
    legacy = deepcopy(config)
    legacy.pop("roi_mm", None)
    legacy.pop("depth_samples", None)
    old, current = simulate(twin, legacy), simulate(twin, config)
    for modality in ("xray", "sam"):
        np.testing.assert_array_equal(old[modality]["image"], current[modality]["image"])
    assert old["ascan"] == current["ascan"]


def test_roi_psf_halo_retains_neighbors_outside_scan_rectangle():
    twin = {"schema_version": 1, "name": "Outside-ROI contrast edge", "size_mm": [4, 4, 0.8],
            "objects": [box("polymer", "epoxy", [2, 2, 0.4], [4, 4, 0.8]),
                        box("outside-copper", "copper", [0.5, 2, 0.4], [1, 4, 0.8])]}
    # Identical physical lattice: full field is 128^2 and central half is 64^2.
    # The copper lies entirely outside the ROI, but both PSFs reach across x=1.
    common = {"depth_samples": 128, "probe_x_mm": 1.01, "probe_y_mm": 2,
              "frequency_mhz": 20, "gate_start_us": 0, "gate_end_us": 0.1}
    full = simulate(twin, acquisition(twin, resolution=128, **common))
    roi = simulate(twin, acquisition(twin, roi_mm=[1, 1, 3, 3], **common))
    for modality in ("xray", "sam"):
        expected = np.asarray(full[modality]["image"])[32:96, 32:96]
        np.testing.assert_allclose(roi[modality]["image"], expected, atol=1e-7, rtol=1e-6)
    np.testing.assert_allclose(roi["ascan"]["amplitude"], full["ascan"]["amplitude"], atol=1e-7)
    image = np.asarray(roi["sam"]["image"])
    assert abs(image[32, 0] - image[32, 32]) > 0.01


@pytest.mark.parametrize("changes", [
    {"roi_mm": [4, 3, 2, 5]},
    {"roi_mm": [2, 3, 2, 5]},
    {"roi_mm": [2, 3, 9, 5]},
    {"roi_mm": [-1, 3, 4, 5]},
    {"roi_mm": [2, 3, 4, 5], "probe_x_mm": 4.01},
    {"roi_mm": [2, 3, 4, 5], "probe_y_mm": 2.99},
    {"roi_mm": [2, 3, 4, 5], "angle_deg": 1},
    {"depth_samples": 2048},
])
def test_invalid_roi_acquisition_is_rejected_before_compute(changes):
    response = client.post("/api/simulate", json={"twin": layered_coupon(), "settings": {
        "resolution": 64, "probe_x_mm": 3, "probe_y_mm": 4, **changes}})
    assert response.status_code == 422
    assert response.json()["detail"]


def test_import_rejects_recommended_roi_outside_specimen_even_with_valid_probe():
    twin = layered_coupon()
    twin["recommended_settings"] = {"roi_mm": [2, 3, 9, 5], "probe_x_mm": 3, "probe_y_mm": 4}
    response = client.post("/api/validate", json=twin)
    assert response.status_code == 422


def test_roi_probe_upper_boundary_samples_last_global_pixel_and_matches_preview():
    twin = layered_coupon()
    settings = acquisition(twin, roi_mm=[2, 3, 4, 5], probe_x_mm=4, probe_y_mm=5)
    body = {"twin": twin, "settings": settings}
    preview = client.post("/api/simulate", json=body)
    inspect = client.post("/api/probe", json=body)
    assert preview.status_code == inspect.status_code == 200, (preview.text, inspect.text)
    result = inspect.json()
    assert result["ascan"]["probe_mm"] == [4, 5]
    assert result["ascan"]["sampled_probe_mm"] == pytest.approx([3.984375, 4.984375])
    np.testing.assert_allclose(result["ascan"]["amplitude"], preview.json()["ascan"]["amplitude"], atol=1e-7)


def section_label(section, coordinate_mm, depth_mm):
    image = np.asarray(section["image"])
    u0, u1, z0, z1 = section["extent_mm"]
    column = int((coordinate_mm - u0) / (u1 - u0) * image.shape[1])
    row = int((depth_mm - z0) / (z1 - z0) * image.shape[0])
    return image[row, column]


def test_hbm_material_sections_have_global_axes_and_resolved_layer_boundaries():
    twin = h100()
    # Two dissimilar marks live on different orthogonal section planes. They
    # also prove the section includes underlying package material and defects.
    twin["objects"].append(box("xz-fiducial", "copper", [8, 20, 1.10], [.3, .3, .06]))
    cavity = box("yz-fiducial", "air", [10.5, 22, 1.14], [.3, .3, .05])
    cavity["role"] = "defect"
    twin["objects"].append(cavity)
    sections = {}
    for axis in ("xz", "yz"):
        response = client.post("/api/hbm/section", json={"twin": twin, "assembly_id": "hbm-1",
                                                         "axis": axis, "resolution": 512})
        assert response.status_code == 200, response.text
        section = sections[axis] = response.json()
        assert np.asarray(section["image"]).shape == (512, 512)
        assert section["mode"] == "material_geometry"
        assert any("not an X-ray" in warning for warning in section["warnings"])
        assert {item["id"] for item in section["materials"]} >= {"silicon", "epoxy", "ambient"}
        coordinate = 10.5 if axis == "xz" else 20
        assert section_label(section, coordinate, .25) == LABELS["silicon"]
        assert section_label(section, coordinate, .2875) == LABELS["epoxy"]
        assert section_label(section, coordinate, .79) == LABELS["silicon"]
    assert sections["xz"]["extent_mm"] == pytest.approx([6.45, 14.55, .18, 1.27])
    assert sections["xz"]["fixed_coordinate_mm"] == 20
    assert sections["yz"]["extent_mm"] == pytest.approx([15.45, 24.55, .18, 1.27])
    assert sections["yz"]["fixed_coordinate_mm"] == 10.5
    assert section_label(sections["xz"], 8, 1.10) == LABELS["copper"]
    assert section_label(sections["yz"], 22, 1.14) == LABELS["air"]
    intact = client.post("/api/hbm/section", json={"twin": twin, "assembly_id": "hbm-1",
                                                   "axis": "yz", "include_defects": False})
    assert intact.status_code == 200
    assert section_label(intact.json(), 22, 1.14) == LABELS["silicon"]


@pytest.mark.parametrize("changes", [
    {"assembly_id": "nonexistent"}, {"axis": "xy"}, {"resolution": 2048},
])
def test_invalid_material_section_requests_fail_readably(changes):
    response = client.post("/api/hbm/section", json={"twin": h100(), "assembly_id": "hbm-1", **changes})
    assert response.status_code == 422
    assert response.json()["detail"]


def test_invalid_hbm_compose_then_state_change_keeps_physical_twin_intact():
    twin = h100()
    original = deepcopy(twin)
    # Twelve unchanged 50/15 um layers exceed the available height, so reject
    # the request instead of silently shrinking dies or moving the surface.
    invalid = client.post("/api/hbm/compose", json={"twin": twin, "assembly_id": "hbm-6",
                                                   "parameters": {"die_count": 12}})
    assert invalid.status_code == 422
    response = client.post("/api/hbm/compose", json={"twin": twin, "assembly_id": "hbm-6",
                                                    "parameters": {"functional_state": "disabled"}})
    assert response.status_code == 200, response.text
    result = response.json()["twin"]
    assert result["hbm_assemblies"][-1]["functional_state"] == "disabled"
    assert result["objects"] == original["objects"]
    assert twin == original
    assert client.post("/api/validate", json=result).status_code == 200
