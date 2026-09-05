"""Continuous SAM analytical timing, saved-array and resource invariants."""

from copy import deepcopy

import numpy as np
import pytest

from virtual_microscopy.materials import MATERIALS, WATER_ATTENUATION_DB_MM_AT_50MHZ
from virtual_microscopy.sam_volume import estimate_sam, prepare_sam, iter_sam_tiles
from virtual_microscopy.volume_schemas import SamVolumeRequest


def box(identifier, material, center, size, role="structure"):
    return {"id": identifier, "name": identifier, "shape": "box", "material": material,
            "center_mm": center, "size_mm": size, "role": role}


def slab():
    return {"name": "Non-grid-aligned analytical slab", "size_mm": [8, 6, 1.6],
            "objects": [box("s", "silicon", [4, 3, .6017], [8, 6, .8])]}


def request(twin=None, **changes):
    return SamVolumeRequest.model_validate({"twin": twin or slab(), "acquisition": {
        "path_model": "continuous_columns_v1", "scan_nx": 16, "scan_ny": 16,
        "depth_samples": 128, "focus_mm": .2017, "frequency_mhz": 50,
        "sample_rate_mhz": 800, "record_duration_us": 1.5, **changes}})


def acquire(config):
    prepared = prepare_sam(config)
    tiles = list(iter_sam_tiles(prepared))
    return prepared, np.concatenate([t[2] for t in tiles]), np.concatenate([t[3] for t in tiles])


def direct_pulse(time, arrival, amplitude, frequency, sigma, rate):
    dt = 1/rate
    position = (arrival-time[0])/dt
    left = int(np.floor(position))
    frac = position-left
    offset = np.arange(len(time))-left
    half = int(np.ceil(4*sigma*rate))
    g0 = np.where(abs(offset) <= half, np.exp(-.5*(offset*dt/sigma)**2), 0)
    g1 = np.where(abs(offset-1) <= half, np.exp(-.5*((offset-1)*dt/sigma)**2), 0)
    return amplitude*((1-frac)*g0+frac*g1)*np.exp(2j*np.pi*frequency*(time-arrival))


def test_non_grid_aligned_slab_signed_rf_matches_independent_path_and_pulse_equations():
    prepared, rf, envelope = acquire(request(water_standoff_mm=.37))
    material = MATERIALS["silicon"]
    r = (material["impedance_mrayl"]-1.48)/(material["impedance_mrayl"]+1.48)
    front = 2*.2017/1.48+.5
    back = front+2*.8/8.43
    water_gain = 10**(-2*WATER_ATTENUATION_DB_MM_AT_50MHZ*(.2017+.37)/20)
    rayleigh = 2*(1.48/50)*2**2
    rear_gain = (1-r*r)*10**(-2*material["attenuation_db_mm_at_50mhz"]*.8/20)/(1+(.8/rayleigh)**2)
    expected = direct_pulse(prepared.time_us, front, r*water_gain, 50, prepared.estimate["pulse_sigma_us"], 800)
    expected += direct_pulse(prepared.time_us, back, -r*water_gain*rear_gain, 50, prepared.estimate["pulse_sigma_us"], 800)
    np.testing.assert_allclose(rf[8, 8], expected.real, atol=3e-7, rtol=1e-5)
    np.testing.assert_allclose(envelope[8, 8], abs(expected), atol=3e-7, rtol=1e-5)
    assert rf[8, 8, np.argmin(abs(prepared.time_us-front))] > 0
    assert rf[8, 8, np.argmin(abs(prepared.time_us-back))] < 0
    assert prepared.metadata["path_diagnostics"]["segment_count"] == 16*16*3


def test_depth_setting_invariant_and_no_voxel_allocation(monkeypatch):
    import virtual_microscopy.physics as physics
    import virtual_microscopy.sam_volume as engine

    def forbidden(*args, **kwargs):
        pytest.fail("Continuous SAM must not allocate or read a voxel grid.")

    monkeypatch.setattr(physics, "voxelize", forbidden)
    monkeypatch.setattr(engine, "voxelize", forbidden)
    low, low_rf, low_env = acquire(request(depth_samples=128))
    high, high_rf, high_env = acquire(request(depth_samples=1024))
    assert low.estimate == high.estimate
    assert low.metadata == high.metadata
    assert low.grid is high.grid is None
    assert low.estimate["grid_shape"] is None
    assert low.estimate["voxel_depth_um"] is None
    assert low.estimate["depth_samples_used"] is False
    np.testing.assert_array_equal(low_rf, high_rf)
    np.testing.assert_array_equal(low_env, high_env)
    np.testing.assert_array_equal(low.time_us, high.time_us)


def test_overlapping_record_windows_preserve_pulse_tails_and_saved_envelope():
    full, rf, env = acquire(request())
    short, short_rf, short_env = acquire(request(record_start_us=.3, record_duration_us=.5))
    np.testing.assert_allclose(short.time_us, full.time_us[240:641], atol=1e-15)
    np.testing.assert_allclose(short_rf, rf[:, :, 240:641], atol=3e-7)
    np.testing.assert_allclose(short_env, env[:, :, 240:641], atol=3e-7)
    assert short_env[8, 8, 0] > .03  # Front pulse center precedes the record.
    assert np.max(short_env-abs(short_rf)) > .01


def test_translated_non_grid_aligned_interface_delays_continuously():
    twin = slab()
    twin["objects"][0]["center_mm"][2] += .0037
    shifted, rf, _ = acquire(request(twin, focus_mm=.2054))
    material = MATERIALS["silicon"]
    r = (material["impedance_mrayl"]-1.48)/(material["impedance_mrayl"]+1.48)
    expected_arrival = 2*(.2017+.0037)/1.48
    expected_gain = r*10**(-2*WATER_ATTENUATION_DB_MM_AT_50MHZ*.2054/20)
    pulse = direct_pulse(shifted.time_us, expected_arrival, expected_gain, 50,
                         shifted.estimate["pulse_sigma_us"], 800)
    before_rear_echo = shifted.time_us < .4
    np.testing.assert_allclose(rf[8, 8, before_rear_echo], pulse.real[before_rear_echo], atol=3e-7)


def lateral_specimen():
    return {"name": "Global-coordinate ROI halo fixture", "size_mm": [1, 1, 1.6], "objects": [
        box("halo", "silicon", [.21875, .5, .6017], [.0625, 1, .8]),
        box("lower", "copper", [.55, .72, .8], [.2, .18, .1])]}


def test_roi_matches_larger_domain_with_full_depth_and_halo():
    twin = lateral_specimen()
    _, full_rf, full_env = acquire(request(twin, scan_nx=64, scan_ny=64, record_duration_us=.7))
    small, small_rf, small_env = acquire(request(twin, scan_nx=32, scan_ny=32,
        roi_mm=[.25, .25, .75, .75], record_duration_us=.7))
    np.testing.assert_allclose(small_rf, full_rf[16:48, 16:48], atol=3e-7)
    np.testing.assert_allclose(small_env, full_env[16:48, 16:48], atol=3e-7)
    assert small_env[:, 0].max() > .01
    assert small.x_mm[0] == .2578125 and small.y_mm[0] == .2578125
    assert small.metadata["padded_shape_yx"][0] > 32


def test_saved_axes_are_exact_path_centers_at_decimal_roi_material_boundary():
    from virtual_microscopy.column_paths import build_column_paths, column_xray_integrals

    edge = .38515625
    twin = {"name": "One-ULP inclusive box edge", "size_mm": [1, 1, 1.6], "objects": [
        box("edge", "silicon", [edge/2, .5, .6], [edge, 1, .8])]}
    prepared = prepare_sam(request(twin, scan_nx=32, scan_ny=16, frequency_mhz=100,
        roi_mm=[.17, .24, .68, .87], record_duration_us=.1))
    p = prepared.layout
    actual_x = prepared.padded_x_mm[p["before_x"]:p["before_x"]+32]
    actual_y = prepared.padded_y_mm[p["before_y"]:p["before_y"]+16]
    np.testing.assert_array_equal(prepared.x_mm, actual_x)
    np.testing.assert_array_equal(prepared.y_mm, actual_y)
    assert not np.shares_memory(prepared.x_mm, prepared.padded_x_mm)
    assert not np.shares_memory(prepared.y_mm, prepared.padded_y_mm)

    recomputed_x = .17+(np.arange(32, dtype=np.float64)+.5)*((.68-.17)/32)
    assert recomputed_x[13] == edge < prepared.x_mm[13]
    saved_paths = build_column_paths(twin, prepared.x_mm[13:14], prepared.y_mm[7:8])
    rounded_paths = build_column_paths(twin, recomputed_x[13:14], prepared.y_mm[7:8])
    # The actual sampled ray is outside; an algebraically recomputed center is
    # on the inclusive side wall and would report a different material path.
    assert column_xray_integrals(saved_paths, 80)[0, 0] == 0
    assert column_xray_integrals(rounded_paths, 80)[0, 0] > 0


def test_canonical_tiles_have_no_seams_and_resume_is_byte_identical():
    prepared, rf, env = acquire(request(lateral_specimen(), scan_nx=24, scan_ny=19, record_duration_us=.7))
    resumed = list(iter_sam_tiles(prepared, start_row=8))
    np.testing.assert_array_equal(np.concatenate([t[2] for t in resumed]), rf[8:])
    np.testing.assert_array_equal(np.concatenate([t[3] for t in resumed]), env[8:])
    whole = deepcopy(prepared)
    whole.estimate["tile_rows"] = 19
    (_, _, all_rf, all_env), = list(iter_sam_tiles(whole))
    np.testing.assert_allclose(all_rf, rf, atol=3e-7)
    np.testing.assert_allclose(all_env, env, atol=3e-7)
    with pytest.raises(ValueError, match="canonical"):
        list(iter_sam_tiles(prepared, start_row=3))
    assert list(iter_sam_tiles(prepared, start_row=19)) == []


@pytest.mark.parametrize("changes,match", [
    ({"sample_rate_mhz": 2400, "record_duration_us": 12}, "16384"),
    ({"scan_nx": 256, "scan_ny": 256, "sample_rate_mhz": 1200, "record_duration_us": 2}, "saved-volume"),
    ({"scan_nx": 256, "scan_ny": 256, "roi_mm": [2, 2, 2.051, 2.051], "frequency_mhz": 10}, "500,000"),
])
def test_resource_failures_precede_path_and_rf_allocation(monkeypatch, changes, match):
    import virtual_microscopy.column_paths as paths

    def forbidden(*args, **kwargs):
        pytest.fail("Preflight must reject before path allocation.")

    monkeypatch.setattr(paths, "build_column_paths", forbidden)
    with pytest.raises(ValueError, match=match):
        prepare_sam(request(**changes))


@pytest.mark.parametrize("limit,message", [("MAX_PEAK_BYTES", "workspace"),
    ("MAX_RF_WORK_CELLS", "RF computation"), ("MAX_CANDIDATE_TESTS", "candidate"),
    ("MAX_EVENT_WORK", "event-work")])
def test_all_continuous_work_caps_are_enforced_before_paths(monkeypatch, limit, message):
    import virtual_microscopy.continuous_sam as engine
    import virtual_microscopy.column_paths as paths
    monkeypatch.setattr(engine, limit, 1)
    monkeypatch.setattr(paths, "build_column_paths", lambda *args, **kwargs: pytest.fail("Premature path allocation"))
    with pytest.raises(ValueError, match=message):
        prepare_sam(request())


def test_interface_bound_has_no_inactive_z_cap_or_thin_layer_occupancy_test():
    twin = slab()
    twin["objects"] = [box(f"thin-{i}", "copper", [4, 3, .4001+i*.00001], [8, 6, .000003]) for i in range(100)]
    estimate = estimate_sam(request(twin, record_duration_us=.1))
    assert estimate["maximum_column_interfaces"] == 200 > 128+1
    assert estimate_sam(request(twin, depth_samples=1024, record_duration_us=.1)) == estimate
