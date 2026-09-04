"""Numerical SAM volume invariants; these do not claim experimental validation."""

from copy import deepcopy

import numpy as np
import pytest
from pydantic import ValidationError

from virtual_microscopy.materials import (MATERIALS, WATER_ATTENUATION_DB_MM_AT_50MHZ,
                                          WATER_IMPEDANCE_MRAYL)
from virtual_microscopy.physics import _sam_signals, voxelize
from virtual_microscopy.sam_volume import (LEGACY_FRACTIONAL_BANDWIDTH, estimate_sam,
                                           iter_sam_tiles, prepare_sam)
from virtual_microscopy.volume_schemas import SamVolumeRequest, SamVolumeSettings


def box(identifier, material, center, size, role="structure"):
    return dict(id=identifier, name=identifier, shape="box", material=material,
                center_mm=center, size_mm=size, role=role)


def specimen():
    return dict(schema_version=1, name="Analytical SAM specimen", size_mm=[8, 6, 1.6],
                objects=[box("silicon", "silicon", [4, 3, 0.6], [8, 6, 0.8])])


def request(twin=None, **changes):
    return SamVolumeRequest.model_validate(dict(twin=twin or specimen(), acquisition={
        "scan_nx": 16, "scan_ny": 16, "depth_samples": 128,
        "focus_mm": 0.2, "record_duration_us": 1, **changes}))


def acquire(config):
    prepared = prepare_sam(config)
    tiles = list(iter_sam_tiles(prepared))
    return prepared, np.concatenate([t[2] for t in tiles]), np.concatenate([t[3] for t in tiles])


def test_rectangular_grid_and_volume_axes_retain_global_coordinates():
    p, rf, env = acquire(request(scan_nx=24, scan_ny=16, roi_mm=[2, 3, 5, 5]))
    assert rf.shape == env.shape == (16, 24, 401)
    assert rf.dtype == env.dtype == np.float32
    assert rf.flags.c_contiguous and env.flags.c_contiguous
    assert p.x_mm[[0, -1]].tolist() == pytest.approx([2.0625, 4.9375])
    assert p.y_mm[[0, -1]].tolist() == pytest.approx([3.0625, 4.9375])
    assert p.metadata["extent_mm"] == [2, 5, 3, 5]
    assert p.grid.pitch_mm.tolist() == pytest.approx([0.125, 0.125, 0.0125])
    assert p.metadata["axis_order"] == ["y", "x", "time"]
    assert any("not geometric depth" in text for text in p.metadata["assumptions"])


def test_first_interface_time_polarity_and_round_trip_loss_are_analytical():
    p, rf, env = acquire(request(sample_rate_mhz=800))
    silicon = MATERIALS["silicon"]
    r = (silicon["impedance_mrayl"] - WATER_IMPEDANCE_MRAYL) / (silicon["impedance_mrayl"] + WATER_IMPEDANCE_MRAYL)
    front = 2 * 0.2 / 1.48
    back = front + 2 * 0.8 / 8.43
    first = np.argmin(abs(p.time_us - front))
    last = np.argmin(abs(p.time_us - back))
    water_factor = 10 ** (-2 * WATER_ATTENUATION_DB_MM_AT_50MHZ * 0.2 / 20)
    rayleigh = 2 * (1.48 / 50) * 2 ** 2
    material_factor = 10 ** (-2 * silicon["attenuation_db_mm_at_50mhz"] * 0.8 / 20)
    expected_back = r * (1 - r * r) * water_factor * material_factor / (1 + (0.8 / rayleigh) ** 2)
    assert rf[8, 8, first] > 0
    assert rf[8, 8, last] < 0
    assert env[8, 8, first] == pytest.approx(r * water_factor, rel=0.004)
    assert env[8, 8, last] == pytest.approx(expected_back, rel=0.004)
    peak_time = p.time_us[np.argmax(env[8, 8])]
    assert peak_time == pytest.approx(front, abs=1 / 800)


def test_water_standoff_adds_both_round_trip_delay_and_pressure_loss():
    # 0.37 mm gives exactly 0.5 us of round-trip water delay at 1.48 mm/us.
    p0, rf0, env0 = acquire(request(record_duration_us=1.5))
    p1, rf1, env1 = acquire(request(record_duration_us=1.5, water_standoff_mm=0.37))
    assert p1.metadata["water_round_trip_delay_us"] == pytest.approx(0.5)
    scale = 10 ** (-2 * WATER_ATTENUATION_DB_MM_AT_50MHZ * 0.37 / 20)
    np.testing.assert_allclose(rf1[:, :, 200:], rf0[:, :, :-200] * scale, atol=3e-7)
    np.testing.assert_allclose(env1[:, :, 200:], env0[:, :, :-200] * scale, atol=3e-7)
    assert np.max(env1[:, :, :80]) < 2e-7


def test_recording_window_does_not_change_retained_rf_or_envelope():
    p, rf, env = acquire(request(record_duration_us=1.5))
    short, short_rf, short_env = acquire(request(record_start_us=0.3, record_duration_us=0.5))
    np.testing.assert_allclose(short.time_us, p.time_us[120:321], atol=1e-15)
    np.testing.assert_allclose(short_rf, rf[:, :, 120:321], atol=3e-7)
    np.testing.assert_allclose(short_env, env[:, :, 120:321], atol=3e-7)
    # The front interface occurs before 0.3 us; its Gaussian tail still enters.
    assert short_env[8, 8, 0] > 0.05


def test_fractional_echo_retains_last_interpolated_tail_at_record_boundary():
    # The front echo is 24.892 samples before the new recording start. Its
    # fractionally delayed pulse has a final contribution at sample 133 even
    # though the integer pulse half-width is only 24 samples.
    _, full_rf, full_env = acquire(request())
    _, crop_rf, crop_env = acquire(request(record_start_us=0.3325, record_duration_us=0.05))
    np.testing.assert_allclose(crop_rf, full_rf[:, :, 133:154], atol=3e-7)
    np.testing.assert_allclose(crop_env, full_env[:, :, 133:154], atol=3e-7)


def test_legacy_fractional_bandwidth_reproduces_preview_trace():
    twin = specimen()
    config = request(scan_nx=64, scan_ny=64, fractional_bandwidth=LEGACY_FRACTIONAL_BANDWIDTH)
    prepared, rf, env = acquire(config)
    grid = voxelize(twin, 64)
    legacy = _sam_signals(grid, dict(frequency_mhz=50, focus_mm=0.2,
                                    probe_x_mm=4, probe_y_mm=3,
                                    gate_start_us=0, gate_end_us=1), full_image=False)
    np.testing.assert_allclose(legacy["ascan"]["time_us"], prepared.time_us, atol=1e-15)
    np.testing.assert_allclose(legacy["ascan"]["amplitude"], rf[32, 32], atol=3e-7)
    np.testing.assert_allclose(legacy["ascan"]["envelope"], env[32, 32], atol=3e-7)
    assert prepared.metadata["pulse_sigma_us"] == pytest.approx(0.75 / 50)


def test_canonical_tiles_have_no_gaussian_seams():
    twin = dict(schema_version=1, name="Lateral seam target", size_mm=[0.6, 0.6, 1.6],
                objects=[box("silicon", "silicon", [0.3, 0.18, 0.6], [0.6, 0.36, 0.8]),
                         box("copper", "copper", [0.22, 0.42, 0.8], [0.44, 0.36, 0.4])])
    prepared, tiled_rf, tiled_env = acquire(request(twin, scan_nx=24, scan_ny=24))
    # Compare to one whole-field pressure convolution, eliminating internal edges.
    whole = deepcopy(prepared)
    whole.estimate["tile_rows"] = 24
    (_, _, full_rf, full_env), = list(iter_sam_tiles(whole))
    np.testing.assert_allclose(tiled_rf, full_rf, atol=3e-7)
    np.testing.assert_allclose(tiled_env, full_env, atol=3e-7)
    assert np.max(tiled_env[7:9]) > 0.05
    assert np.max(tiled_env[15:17]) > 0.01


def test_resuming_at_a_canonical_tile_is_byte_identical():
    prepared = prepare_sam(request(scan_ny=19))
    full = list(iter_sam_tiles(prepared))
    resumed = list(iter_sam_tiles(prepared, start_row=8))
    assert [(a, b) for a, b, _, _ in full] == [(0, 8), (8, 16), (16, 19)]
    for previous, current in zip(full[1:], resumed):
        np.testing.assert_array_equal(previous[2], current[2])
        np.testing.assert_array_equal(previous[3], current[3])
    assert list(iter_sam_tiles(prepared, start_row=19)) == []
    with pytest.raises(ValueError, match="canonical"):
        list(iter_sam_tiles(prepared, start_row=1))


def test_roi_matches_full_field_with_material_in_neighboring_psf_halo():
    twin = dict(schema_version=1, name="ROI boundary target", size_mm=[1, 1, 1.6],
                objects=[box("nearby", "silicon", [0.21875, 0.5, 0.6], [0.0625, 1, 0.8])])
    _, full_rf, full_env = acquire(request(twin, scan_nx=64, scan_ny=64))
    p, roi_rf, roi_env = acquire(request(twin, scan_nx=32, scan_ny=32, roi_mm=[0.25, 0.25, 0.75, 0.75]))
    assert p.grid.labels.shape[1] > 32
    np.testing.assert_allclose(roi_rf, full_rf[16:48, 16:48], atol=3e-7)
    np.testing.assert_allclose(roi_env, full_env[16:48, 16:48], atol=3e-7)
    assert roi_env[:, 0].max() > 0.01


@pytest.mark.parametrize("changes,message", [
    ({"frequency_mhz": 80, "sample_rate_mhz": 400}, "eight times"),
    ({"record_start_us": 11, "record_duration_us": 2}, "before 12"),
    ({"roi_mm": [0, 0, 9, 4]}, "inside"),
    ({"roi_mm": [1, 1, 1.01, 2]}, "0.05"),
    ({"focus_mm": 2}, "within"),
    ({"scan_nx": 16.5}, "integer"),
    ({"fractional_bandwidth": 0.1}, "greater than"),
    ({"gate_start_us": 0.1}, "Extra inputs"),
])
def test_invalid_or_processing_parameters_are_rejected(changes, message):
    with pytest.raises(ValidationError, match=message):
        request(**changes)


def test_bandwidth_controls_physical_pulse_width_independently_from_sampling():
    narrow = estimate_sam(request(fractional_bandwidth=0.25))
    broad = estimate_sam(request(fractional_bandwidth=1))
    assert narrow["shape"] == broad["shape"]
    assert narrow["pulse_envelope_fwhm_us"] == pytest.approx(4 * broad["pulse_envelope_fwhm_us"])
    assert SamVolumeSettings(frequency_mhz=150, sample_rate_mhz=1200, fractional_bandwidth=1)


def test_fractional_duration_uses_only_samples_inside_requested_window():
    estimate = estimate_sam(request(record_start_us=0.001, record_duration_us=0.051))
    assert estimate["time_samples"] == 21
    assert estimate["time_end_us"] == pytest.approx(0.051)
    assert estimate["requested_record_end_us"] == pytest.approx(0.052)


def test_budget_failures_precede_geometry_allocation(monkeypatch):
    import virtual_microscopy.sam_volume as engine

    def allocation_forbidden(*args, **kwargs):
        raise AssertionError("preflight must run before geometry allocation")

    monkeypatch.setattr(engine, "voxelize", allocation_forbidden)
    with pytest.raises(ValueError, match="RF samples"):
        prepare_sam(request(sample_rate_mhz=2400, record_duration_us=12))
    with pytest.raises(ValueError, match="64 million"):
        prepare_sam(request(scan_nx=256, scan_ny=256, depth_samples=1024))
    with pytest.raises(ValueError, match="saved-volume"):
        prepare_sam(request(scan_nx=256, scan_ny=256, sample_rate_mhz=1200, record_duration_us=2))


def test_h100_roi_estimate_accounts_for_both_arrays_and_independent_depth():
    from tools.build_h100_example import h100

    twin = h100()
    hbm = twin["hbm_assemblies"][5]
    x, y = hbm["center_xy_mm"]
    w, h = hbm["footprint_mm"]
    config = request(twin, scan_nx=64, scan_ny=64, depth_samples=1024,
                     roi_mm=[x - w / 2, y - h / 2, x + w / 2, y + h / 2], record_duration_us=2)
    estimate = estimate_sam(config)
    assert estimate["shape"] == [64, 64, 801]
    assert estimate["rf_bytes"] == estimate["envelope_bytes"] == 64 * 64 * 801 * 4
    assert estimate["total_bytes"] > 2 * estimate["rf_bytes"]
    assert estimate["estimated_peak_bytes"] < 512 * 1024 ** 2
