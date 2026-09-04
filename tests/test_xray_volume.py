"""Analytical and sampling checks for full-angle saved parallel radiography."""

import numpy as np
import pytest
from pydantic import ValidationError

from virtual_microscopy.xray_schemas import XrayVolumeRequest
from virtual_microscopy.xray_volume import estimate_xray, prepare_xray, iter_xray_views


MU_COPPER_80 = 0.7630 * 8.96 / 10
MU_SILICON_80 = 0.2228 * 2.329 / 10


def box(identifier, material, center, size, role="structure"):
    return dict(id=identifier, name=identifier, shape="box", material=material,
                center_mm=center, size_mm=size, role=role)


def specimen(objects=None, size=(4, 3, 2)):
    return dict(schema_version=1, name="Analytical X-ray specimen", size_mm=list(size),
                objects=objects or [box("copper", "copper", (np.asarray(size) / 2).tolist(), list(size))])


def request(twin=None, **changes):
    return XrayVolumeRequest.model_validate(dict(twin=twin or specimen(), acquisition={
        "geometry_nx": 64, "geometry_ny": 48, "geometry_nz": 64,
        "detector_cols": 32, "detector_rows": 24, "views": 4,
        "noise": False, "detector_fwhm_mm": 0, **changes}))


def acquire(config):
    prepared = prepare_xray(config)
    views = list(iter_xray_views(prepared))
    return prepared, {name: np.concatenate([arrays[name] for _, _, arrays in views])
                      for name in views[0][2]}


def box_path(point, direction, center, size):
    """Independent analytic intersection with an exact primitive, not voxel data."""
    low, high = -np.inf, np.inf
    center, size = np.asarray(center), np.asarray(size)
    for axis in range(3):
        a, b = center[axis] - size[axis] / 2, center[axis] + size[axis] / 2
        if abs(direction[axis]) < 1e-12:
            if point[axis] < a or point[axis] >= b:
                return 0.0
        else:
            ends = sorted(((a - point[axis]) / direction[axis], (b - point[axis]) / direction[axis]))
            low, high = max(low, ends[0]), min(high, ends[1])
    return max(0.0, high - low)


def test_cardinal_views_have_exact_thickness_paths_without_singularities():
    p, data = acquire(request(detector_width_mm=1, detector_height_mm=1))
    np.testing.assert_array_equal(p.angles_deg, [0, 90, 180, 270])
    for index, length in enumerate([2, 4, 2, 4]):
        np.testing.assert_allclose(data["line_integrals"][index], MU_COPPER_80 * length, atol=2e-7)
        np.testing.assert_allclose(data["transmission"][index], np.exp(-MU_COPPER_80 * length), atol=3e-8)
    assert all(array.dtype == np.float32 and np.isfinite(array).all() for array in data.values())
    assert np.all(data["valid_mask"] == 1)


def test_asymmetric_boxes_match_independent_paths_at_negative_and_oblique_angles():
    objects = [box("cu", "copper", [0.75, 0.625, 0.375], [0.5, 0.5, 0.5]),
               box("si", "silicon", [2.5, 2.0, 1.375], [1.0, 0.75, 0.75])]
    twin = specimen(objects)
    p, data = acquire(request(twin, views=12, angle_start_deg=-180,
                             detector_offset_u_mm=0.1, detector_offset_v_mm=-0.125))
    seen = []
    for k, degrees in enumerate(p.angles_deg):
        theta = np.deg2rad(degrees)
        d = np.array([np.sin(theta), 0, np.cos(theta)])
        eu = np.array([np.cos(theta), 0, -np.sin(theta)])
        expected = np.zeros((len(p.v_mm), len(p.u_mm)))
        for row, v in enumerate(p.v_mm):
            for col, u in enumerate(p.u_mm):
                point = np.array([2, 1.5, 1]) + (u + 0.1) * eu + [0, v - 0.125, 0]
                expected[row, col] = sum(mu * box_path(point, d, obj["center_mm"], obj["size_mm"])
                                         for mu, obj in zip([MU_COPPER_80, MU_SILICON_80], objects))
        np.testing.assert_allclose(data["line_integrals"][k], expected, atol=1.8e-7)
        seen.append(np.count_nonzero(expected > 0))
    assert min(seen) > 0
    assert len(set(seen)) > 2


def test_layer_overwrite_uses_path_replacement_not_additive_double_counting():
    twin = specimen([box("silicon", "silicon", [2, 1.5, 1], [4, 3, 2]),
                     box("copper-layer", "copper", [2, 1.5, 0.625], [4, 3, 0.25])])
    _, data = acquire(request(twin, views=1, detector_width_mm=1, detector_height_mm=1))
    expected = MU_SILICON_80 * 1.75 + MU_COPPER_80 * 0.25
    np.testing.assert_allclose(data["line_integrals"], expected, atol=1e-7)


def test_cropped_detector_keeps_full_oblique_material_path():
    _, data = acquire(request(views=1, angle_start_deg=45, detector_width_mm=0.5,
                              detector_height_mm=0.5))
    np.testing.assert_allclose(data["line_integrals"], MU_COPPER_80 * 2 * np.sqrt(2), atol=2e-7)


def test_detector_psf_crop_retains_neighboring_attenuation_and_air_context():
    twin = specimen([box("copper-half", "copper", [0.25, 0.5, 0.5], [0.5, 1, 1])], size=(1, 1, 1))
    _, full = acquire(request(twin, geometry_ny=64, detector_cols=64, detector_rows=64,
                              detector_width_mm=1, detector_height_mm=1, detector_fwhm_mm=0.08, views=1))
    p, crop = acquire(request(twin, geometry_ny=64, detector_cols=32, detector_rows=32,
                             detector_width_mm=0.5, detector_height_mm=0.5,
                             detector_offset_u_mm=0.25, detector_fwhm_mm=0.08, views=1))
    np.testing.assert_allclose(crop["transmission"][0], full["transmission"][0, 16:48, 32:64], atol=1e-7)
    assert crop["transmission"][0, 16, 0] < 0.9
    assert crop["transmission"][0, 16, -1] == pytest.approx(1, abs=1e-7)
    assert p.estimate["detector_halo_pixels"][0] > 0
    assert p.estimate["truncated_views"] == 1


def test_detector_and_geometry_sampling_are_independent_with_thin_layer_convergence():
    twin = specimen([box("thin", "copper", [2, 1.5, 0.515625], [4, 3, 0.015625])])
    common = dict(views=1, detector_width_mm=1, detector_height_mm=1)
    _, coarse = acquire(request(twin, geometry_nz=32, **common))
    fine_p, fine = acquire(request(twin, geometry_nz=256, **common))
    _, more_pixels = acquire(request(twin, geometry_nz=32, detector_cols=64, detector_rows=64, **common))
    np.testing.assert_allclose(coarse["line_integrals"], 0, atol=1e-7)
    np.testing.assert_allclose(more_pixels["line_integrals"], 0, atol=1e-7)
    np.testing.assert_allclose(fine["line_integrals"], MU_COPPER_80 * 0.015625, atol=1e-7)
    assert fine_p.estimate["geometry_pitch_mm"][2] == pytest.approx(2 / 256)


def test_pose_vectors_and_local_coordinates_encode_offsets_exactly_once():
    config = request(rotation_center_mm=[1, 1, 0.5], detector_offset_u_mm=0.3,
                     detector_offset_v_mm=-0.2, detector_width_mm=2, detector_height_mm=1)
    p = prepare_xray(config)
    np.testing.assert_allclose(np.linalg.norm(p.ray_direction_xyz, axis=1), 1)
    np.testing.assert_allclose(np.linalg.norm(p.detector_u_xyz, axis=1), 1)
    np.testing.assert_allclose(np.sum(p.ray_direction_xyz * p.detector_u_xyz, axis=1), 0, atol=1e-15)
    np.testing.assert_allclose(p.detector_center_mm, [1, 1, 0.5] + 0.3 * p.detector_u_xyz - 0.2 * p.detector_v_xyz)
    assert p.u_mm.mean() == pytest.approx(0)
    assert p.v_mm.mean() == pytest.approx(0)
    assert p.estimate["detector_extent_mm"] == [-1, 1, -0.5, 0.5]


def test_auto_field_remains_rotation_safe_when_rotation_center_is_moved():
    estimate = estimate_xray(request(rotation_center_mm=[0, 0, 0], views=12))
    assert estimate["detector_width_mm"] == pytest.approx(2 * np.hypot(4, 2))
    assert estimate["detector_height_mm"] == 6
    assert estimate["truncated_view_indices"] == []


def test_endpoint_is_excluded_and_fractional_spans_are_retained():
    p = prepare_xray(request(views=3, angle_start_deg=-20, angle_span_deg=100))
    np.testing.assert_allclose(p.angles_deg, [-20, -20 + 100/3, -20 + 200/3])
    assert p.estimate["angle_stop_exclusive_deg"] == 80
    assert p.estimate["angle_end_deg"] < 80


def test_per_view_poisson_stream_is_reproducible_and_resume_is_byte_identical():
    p, whole = acquire(request(noise=True, views=7, detector_width_mm=1, detector_height_mm=1))
    resumed = list(iter_xray_views(p, start_view=3))
    for offset, (_, _, arrays) in enumerate(resumed, start=3):
        for name in arrays:
            np.testing.assert_array_equal(arrays[name][0], whole[name][offset])
    _, again = acquire(p.request)
    for name in whole:
        np.testing.assert_array_equal(again[name], whole[name])
    assert not np.array_equal(whole["counts"][0], whole["counts"][-1])
    assert list(iter_xray_views(p, start_view=7)) == []
    with pytest.raises(ValueError, match="Resume view"):
        list(iter_xray_views(p, start_view=-1))


def test_poisson_mean_variance_and_unclipped_transmission():
    # Tiny angle span keeps a uniform exact path while using independent views.
    _, data = acquire(request(views=16, angle_span_deg=1e-9, detector_width_mm=1,
                              detector_height_mm=1, detector_cols=64, detector_rows=64,
                              noise=True, photons=1000))
    expected = 1000 * np.exp(-MU_COPPER_80 * 2)
    counts = data["counts"]
    assert counts.mean() == pytest.approx(expected, rel=0.005)
    assert counts.var() == pytest.approx(expected, rel=0.04)
    assert np.array_equal(counts, np.floor(counts))
    # A shifted field completely outside the specimen is an open beam. Noise
    # above I0 is valid and produces negative measured logarithms.
    _, air = acquire(request(views=2, noise=True, photons=1000,
                             detector_offset_v_mm=20, detector_width_mm=1, detector_height_mm=1))
    assert np.any(air["transmission"] > 1)
    assert np.any(air["line_integrals"] < 0)
    assert np.all(air["valid_mask"] == 1)


def test_only_zero_counts_are_substituted_and_float32_underflow_is_masked():
    twin = specimen(size=(100, 2, 6))
    _, data = acquire(request(twin, geometry_nx=16, geometry_ny=16, geometry_nz=32,
                              detector_cols=16, detector_rows=16, detector_width_mm=0.5,
                              detector_height_mm=1, energy_kev=40, photons=1000))
    assert np.all((data["counts"][0] > 0) & (data["counts"][0] < 0.5))
    assert np.all(data["valid_mask"][0] == 1)
    np.testing.assert_allclose(data["line_integrals"][0], 4.862 * 8.96 / 10 * 6, atol=2e-6)
    assert np.all(data["counts"][1] == 0)
    assert np.all(data["valid_mask"][1] == 0)
    np.testing.assert_allclose(data["line_integrals"][1], -np.log(0.5 / 1000), atol=1e-6)
    _, noisy = acquire(request(twin, geometry_nx=16, geometry_ny=16, geometry_nz=32,
                               detector_width_mm=0.5, detector_height_mm=1,
                               energy_kev=40, photons=1000, views=1, noise=True))
    assert np.all(noisy["counts"] == 0)
    assert np.all(noisy["valid_mask"] == 0)


@pytest.mark.parametrize("changes,match", [
    ({"energy_kev": 20}, "greater than"),
    ({"detector_width_mm": 0.001}, "greater than"),
    ({"rotation_center_mm": [5, 1, 1]}, "inside"),
    ({"views": 0}, "greater than"),
    ({"angle_span_deg": 0}, "greater than"),
    ({"geometry_nx": 16.5}, "integer"),
    ({"roi_mm": [0, 0, 1, 1]}, "Extra inputs"),
])
def test_invalid_acquisitions_are_rejected(changes, match):
    with pytest.raises(ValidationError, match=match):
        request(**changes)


def test_budget_checks_precede_geometry_allocation(monkeypatch):
    import virtual_microscopy.xray_volume as engine

    def forbidden(*args, **kwargs):
        raise AssertionError("geometry allocated before rejected estimate")

    monkeypatch.setattr(engine, "voxelize", forbidden)
    with pytest.raises(ValueError, match="64 million"):
        prepare_xray(request(geometry_nx=256, geometry_ny=256, geometry_nz=1024))
    with pytest.raises(ValueError, match="saved-volume"):
        prepare_xray(request(views=720, detector_rows=256, detector_cols=256))
    with pytest.raises(ValueError, match="workspace"):
        prepare_xray(request(detector_width_mm=0.05, detector_height_mm=0.05,
                             detector_cols=256, detector_rows=256, detector_fwhm_mm=1))
    with pytest.raises(ValueError, match="work budget"):
        prepare_xray(request(views=720, detector_cols=128, detector_rows=128,
                             geometry_nx=256, geometry_nz=512))
    sphere = specimen([dict(box("sphere", "silicon", [3, 3, 3], [6, 6, 6]), shape="sphere")], size=(6, 6, 6))
    with pytest.raises(ValueError, match="workspace"):
        prepare_xray(request(sphere, views=1, geometry_nx=256, geometry_ny=256, geometry_nz=960))


def test_h100_default_estimate_counts_four_arrays_and_all_pose_coordinates():
    from tools.build_h100_example import h100

    estimate = estimate_xray({"twin": h100()})
    assert estimate["shape"] == [72, 64, 96]
    assert estimate["total_bytes"] == 72 * 64 * 96 * 16 + (72 * 13 + 64 + 96) * 8
    assert estimate["truncated_views"] == 0
    assert estimate["estimated_peak_bytes"] < 512 * 1024 ** 2
