"""Analytical forward-model checks, independent of the browser/UI.

These verify equations and numerical invariants, not experimental accuracy.
"""

import copy

import numpy as np
import pytest

from virtual_microscopy.materials import MATERIALS, linear_attenuation_mm
from virtual_microscopy.physics import (LABELS, acoustic_echoes, probe, project_xray,
                                        reflection_coefficient, simulate, voxelize, _bscan_summary)


def slab(material="epoxy", thickness=0.8, width=8.0):
    return {"schema_version": 1, "name": "Analytical slab", "description": "", "size_mm": [width, width, thickness],
            "objects": [{"id": "slab", "name": "Homogeneous slab", "shape": "box", "material": material,
                         "center_mm": [width / 2, width / 2, thickness / 2],
                         "size_mm": [width, width, thickness], "role": "structure"}]}


def settings(**changes):
    result = {"resolution": 64, "energy_kev": 80, "angle_deg": 0, "photons": 50000, "noise": False,
              "frequency_mhz": 50, "gate_start_us": 0.1, "gate_end_us": 0.65, "focus_mm": 0.5,
              "probe_x_mm": 4, "probe_y_mm": 4, "include_defects": True, "seed": 42}
    result.update(changes)
    return result


def column_echoes(echoes, row=32, col=32):
    selected = (echoes.rows == row) & (echoes.cols == col)
    return echoes.times_us[selected], echoes.amplitudes[selected]


def test_nist_coefficients_and_unit_conversion():
    assert linear_attenuation_mm("copper", 80) == pytest.approx(0.7630 * 8.96 / 10)
    assert linear_attenuation_mm("silicon", 40) == pytest.approx(0.7012 * 2.329 / 10)
    assert linear_attenuation_mm("solder", 150) == pytest.approx(0.6091 * 7.31 / 10)
    middle = np.sqrt(60 * 80)
    assert linear_attenuation_mm("copper", middle) == pytest.approx(np.sqrt(1.593 * 0.7630) * 8.96 / 10)
    with pytest.raises(ValueError, match="40 and 150"):
        linear_attenuation_mm("copper", 20)


def test_material_library_is_explicit_about_surrogates():
    assert set(MATERIALS) == {"silicon", "copper", "solder", "epoxy", "fr4", "air"}
    for name, material in MATERIALS.items():
        assert material["id"] == name
        assert material["impedance_mrayl"] == pytest.approx(material["density_g_cm3"] * material["sound_speed_m_s"] / 1000)
        assert material["provenance"]["xray_sources"]
        assert "uncalibrated" in material["attenuation_status"]
    assert "PMMA" in MATERIALS["epoxy"]["provenance"]["xray"]
    assert "Pure tin" in MATERIALS["solder"]["provenance"]["xray"]


@pytest.mark.parametrize("angle", [0, -45, 45])
def test_beer_lambert_uniform_slab_and_tilt_path_length(angle):
    grid = voxelize(slab("copper"), 64)
    image = project_xray(grid, 80, angle, detector_fwhm_mm=0)
    expected = np.exp(-linear_attenuation_mm("copper", 80) * 0.8 / np.cos(np.deg2rad(angle)))
    assert image[32, 32] == pytest.approx(expected, rel=1e-12)
    if angle == 0:
        np.testing.assert_allclose(image, expected, rtol=1e-12)


def test_increasing_energy_increases_copper_transmission():
    grid = voxelize(slab("copper"), 64)
    assert project_xray(grid, 40).mean() < project_xray(grid, 80).mean() < project_xray(grid, 150).mean()


def test_xray_tilt_moves_off_midplane_object_in_opposite_directions():
    twin = slab("copper", thickness=2)
    twin["objects"][0].update(center_mm=[4, 4, 1.6], size_mm=[1, 1, 0.3])
    grid = voxelize(twin, 128)
    coordinate = (np.arange(128) + 0.5) * 8 / 128
    centroids = []
    for angle in [-30, 0, 30]:
        contrast = 1 - project_xray(grid, 80, angle, detector_fwhm_mm=0)
        centroids.append(float((contrast.sum(axis=0) * coordinate).sum() / contrast.sum()))
    assert centroids[0] > centroids[1] > centroids[2]
    assert centroids[1] == pytest.approx(4)
    assert centroids[0] - 4 == pytest.approx(4 - centroids[2], abs=0.02)


def test_seeded_poisson_noise_and_count_variance():
    grid = voxelize(slab("copper"), 64)
    low = project_xray(grid, 80, photons=1000, noise=True, seed=7)
    same = project_xray(grid, 80, photons=1000, noise=True, seed=7)
    other = project_xray(grid, 80, photons=1000, noise=True, seed=8)
    high = project_xray(grid, 80, photons=1000000, noise=True, seed=7)
    np.testing.assert_array_equal(low, same)
    assert not np.array_equal(low, other)
    assert low.var() > 500 * high.var()
    expected = np.exp(-linear_attenuation_mm("copper", 80) * 0.8)
    assert low.mean() == pytest.approx(expected, abs=0.003)


def test_background_is_transparent_for_xray_and_water_matched_for_sam():
    twin = slab()
    twin["objects"] = []  # engine-level primitive-empty reference, not an import fixture
    grid = voxelize(twin, 64)
    np.testing.assert_array_equal(project_xray(grid, 80), 1)
    assert len(acoustic_echoes(grid, 50, 0.5).amplitudes) == 0
    twin["objects"] = slab("air")["objects"]
    explicit_air = acoustic_echoes(voxelize(twin, 64), 50, 0, apply_focus=False)
    assert column_echoes(explicit_air)[1][0] < -0.999


def test_signed_reflection_pressure_and_reciprocity():
    assert reflection_coefficient(3.12, 3.12) == 0
    assert reflection_coefficient(1.48, 3.12) > 0
    assert reflection_coefficient(3.12, 1.48) == -reflection_coefficient(1.48, 3.12)
    assert reflection_coefficient(1.48, 0.000413) < -0.999


def test_slab_primary_echo_time_transmission_and_attenuation():
    grid = voxelize(slab(), 64)
    times, amplitude = column_echoes(acoustic_echoes(grid, 50, 0, apply_focus=False))
    r = reflection_coefficient(1.48, 1.2 * 2.6)
    expected_bottom = -r * (1 - r * r) * 10 ** (-2 * 2.0 * 0.8 / 20)
    assert times.tolist() == pytest.approx([0, 2 * 0.8 / 2.6], abs=1e-12)
    assert amplitude.tolist() == pytest.approx([r, expected_bottom], rel=1e-12)
    assert len(amplitude) == 2  # primary-only model must not invent reverberations


def test_acoustic_loss_increases_with_frequency():
    grid = voxelize(slab(), 64)
    low = column_echoes(acoustic_echoes(grid, 10, 0, apply_focus=False))[1][-1]
    high = column_echoes(acoustic_echoes(grid, 100, 0, apply_focus=False))[1][-1]
    assert abs(high) < abs(low)


def test_air_void_phase_and_shadow_and_defect_toggle():
    twin = slab()
    twin["objects"].append({"id": "void", "name": "Air layer", "shape": "box", "material": "air",
                             "center_mm": [4, 4, 0.4125], "size_mm": [2, 2, 0.025], "role": "defect"})
    good = voxelize(twin, 64, include_defects=False)
    bad = voxelize(twin, 64, include_defects=True)
    _, good_amp = column_echoes(acoustic_echoes(good, 50, 0, apply_focus=False))
    bad_time, bad_amp = column_echoes(acoustic_echoes(bad, 50, 0, apply_focus=False))
    void_entry = np.argmin(abs(bad_time - 2 * 0.4 / 2.6))
    assert bad_amp[void_entry] < -0.5
    assert abs(bad_amp[-1]) < abs(good_amp[-1]) * 1e-3
    assert project_xray(bad, 80)[32, 32] > project_xray(good, 80)[32, 32]
    assert bad.labels[32, 32, 65] == LABELS["air"]


@pytest.mark.parametrize("shape", ["sphere", "cylinder"])
def test_curved_primitive_sampling_and_ordered_overwrite(shape):
    twin = slab("epoxy", thickness=2)
    twin["objects"].append({"id": "metal", "name": "Metal", "shape": shape, "material": "copper",
                             "center_mm": [4, 4, 1], "size_mm": [1, 1, 1], "role": "structure"})
    grid = voxelize(twin, 64)
    assert grid.labels[32, 32, 64] == LABELS["copper"]
    assert grid.labels[0, 0, 64] == LABELS["epoxy"]
    assert grid.labels[28, 28, 64] == LABELS["epoxy"]  # outside radial footprint


def test_cscan_gate_and_full_rate_signed_ascan():
    early = simulate(slab(), settings(focus_mm=0, gate_start_us=0, gate_end_us=0.1))
    quiet = simulate(slab(), settings(focus_mm=0, gate_start_us=0.1, gate_end_us=0.2))
    r = float(reflection_coefficient(1.48, 3.12))
    assert early["sam"]["image"][32][32] == pytest.approx(r, abs=2e-7)
    assert quiet["sam"]["peak_amplitude"] < 2e-7
    time = np.array(early["ascan"]["time_us"])
    amplitude = np.array(early["ascan"]["amplitude"])
    envelope = np.array(early["ascan"]["envelope"])
    assert amplitude[0] == pytest.approx(r, abs=2e-7)
    assert amplitude[4] < 0  # cosine carrier has a negative half cycle
    assert time[1] - time[0] == pytest.approx(1 / (8 * 50))
    assert np.all(envelope + 1e-7 >= abs(amplitude))
    assert np.array(early["bscan"]["image"]).shape[0] <= 512


def test_probe_matches_full_simulation_and_arrays_are_quantitative():
    twin = slab("silicon")
    acquisition = settings(probe_x_mm=3.17, probe_y_mm=4.21)
    full = simulate(twin, acquisition)
    local = probe(twin, acquisition)
    np.testing.assert_allclose(local["ascan"]["amplitude"], full["ascan"]["amplitude"], atol=1e-7)
    np.testing.assert_allclose(local["bscan"]["image"], full["bscan"]["image"], atol=1e-7)
    assert full["xray"]["max"] == pytest.approx(full["xray"]["min"])
    assert full["sam"]["max"] < 1
    assert full["metadata"]["grid_shape"] == [64, 64, 128]
    assert full["metadata"]["voxel_depth_um"] == pytest.approx(6.25)


def test_sampling_and_tilt_warnings_are_explicit():
    twin = slab()
    twin["objects"][0]["size_mm"][2] = 0.001
    result = simulate(twin, settings(angle_deg=30))
    messages = " ".join(result["metadata"]["warnings"])
    assert "below two grid samples" in messages
    assert "not exactly co-registered" in messages


def test_extreme_requested_acquisition_fails_with_actionable_budget_error():
    with pytest.raises(ValueError, match="computation budget"):
        simulate(slab(), settings(resolution=192, frequency_mhz=150, gate_end_us=12))


def test_input_twin_and_settings_not_modified():
    twin, acquisition = slab(), settings()
    original = copy.deepcopy((twin, acquisition))
    simulate(twin, acquisition)
    assert (twin, acquisition) == original


def test_bscan_final_single_sample_bin_has_positive_width_and_retains_peak():
    time = np.arange(513) * 0.01
    envelope = np.zeros((3, 513))
    envelope[1, -1] = 0.25
    scan = _bscan_summary(envelope, time, 8, 4)
    assert np.all(np.diff(scan["time_bin_edges_us"]) > 0)
    assert scan["image"][-1][1] == 0.25
    assert scan["time_bin_edges_us"][-1] == scan["extent"][-1] == time[-1]
    assert len(scan["time_bin_edges_us"]) == len(scan["image"]) + 1
