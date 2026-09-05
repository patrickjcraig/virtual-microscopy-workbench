"""Continuous preview/probe integration and unchanged normal-incidence products."""

from copy import deepcopy

import numpy as np
import pytest
from pydantic import ValidationError

from virtual_microscopy.materials import linear_attenuation_mm
from virtual_microscopy.physics import simulate, probe
from virtual_microscopy.schemas import SimulationRequest


def slab(front=.2017):
    return {"name": "Independent normal-incidence slab", "size_mm": [8, 6, 1.6], "objects": [
        {"id": "s", "name": "s", "shape": "box", "material": "silicon",
         "center_mm": [4, 3, front+.4], "size_mm": [8, 6, .8]}]}


def request(twin=None, **changes):
    return SimulationRequest.model_validate({"twin": twin or slab(), "settings": {
        "path_model": "continuous_columns_v1", "resolution": 64, "depth_samples": 128,
        "frequency_mhz": 50, "focus_mm": .2017, "gate_start_us": .28, "gate_end_us": .445,
        "probe_x_mm": 4, "probe_y_mm": 3, "noise": False, **changes}})


def run(r):
    return simulate(r.twin.model_dump(mode="json"), r.settings.model_dump())


def test_non_grid_aligned_xray_length_and_full_rf_probe_record():
    r = request()
    result = run(r)
    expected_transmission = np.exp(-linear_attenuation_mm("silicon", 80)*.8)
    np.testing.assert_allclose(result["xray"]["image"], expected_transmission, atol=1e-12, rtol=1e-10)
    time = np.asarray(result["ascan"]["time_us"])
    envelope = np.asarray(result["ascan"]["envelope"])
    front, rear = 2*.2017/1.48, 2*.2017/1.48+2*.8/8.43
    assert front < r.settings.gate_start_us < r.settings.gate_end_us < rear
    assert time[-1] >= rear+.06  # Full pulse support after the last echo.
    assert time[np.argmax(envelope)] == pytest.approx(front, abs=1/400)
    assert min(result["ascan"]["amplitude"]) < 0 < max(result["ascan"]["amplitude"])
    gate = (time >= r.settings.gate_start_us) & (time <= r.settings.gate_end_us)
    np.testing.assert_allclose(result["sam"]["image"], envelope[gate].max(), atol=3e-7)
    local = probe(r.twin.model_dump(mode="json"), r.settings.model_dump())
    assert local["ascan"] == result["ascan"]
    assert local["bscan"] == result["bscan"]
    assert result["metadata"]["path_diagnostics"]["columns"] == 64*64


def test_grid_aligned_fixture_matches_prior_voxel_preview_with_gate_edge_tails():
    continuous = run(request(slab(.2), focus_mm=.2))
    voxel = run(request(slab(.2), path_model="voxel_centers_v1", focus_mm=.2))
    for modality in ("xray", "sam"):
        np.testing.assert_allclose(continuous[modality]["image"], voxel[modality]["image"], atol=3e-7)
    np.testing.assert_array_equal(continuous["ascan"]["time_us"], voxel["ascan"]["time_us"])
    np.testing.assert_allclose(continuous["ascan"]["amplitude"], voxel["ascan"]["amplitude"], atol=3e-7)
    np.testing.assert_allclose(continuous["ascan"]["envelope"], voxel["ascan"]["envelope"], atol=3e-7)
    np.testing.assert_allclose(continuous["bscan"]["image"], voxel["bscan"]["image"], atol=3e-7)


def test_preview_and_probe_report_exact_sampled_column_centers_for_decimal_roi():
    from virtual_microscopy.continuous_sam import column_layout, padded_coordinates
    from virtual_microscopy.physics import DETECTOR_FWHM_MM

    twin = slab()
    r = request(twin, roi_mm=[.17, .24, .68, .87], frequency_mhz=100,
                probe_x_mm=.38515625, probe_y_mm=.48)
    p = column_layout(twin, 64, 64, r.settings.roi_mm, 100, DETECTOR_FWHM_MM)
    x, y = padded_coordinates(p)
    ix = int((r.settings.probe_x_mm-p["origin"][0])/p["dx"])
    iy = int((r.settings.probe_y_mm-p["origin"][1])/p["dy"])
    preview = run(r)
    local = probe(r.twin.model_dump(mode="json"), r.settings.model_dump())
    for result in (preview, local):
        assert result["ascan"]["sampled_probe_mm"] == [x[ix], y[iy]]
        assert result["bscan"]["y_mm"] == y[iy]
        assert result["metadata"]["grid_origin_mm"] == p["origin"]
    assert preview["ascan"] == local["ascan"]


def test_inactive_z_setting_and_seeded_noise_are_exactly_invariant_without_voxels(monkeypatch):
    import virtual_microscopy.physics as physics
    monkeypatch.setattr(physics, "voxelize", lambda *args, **kwargs: pytest.fail("Continuous preview allocated voxels"))
    low = run(request(depth_samples=128, noise=True, seed=9))
    high = run(request(depth_samples=1024, noise=True, seed=9))
    for key in ("xray", "sam", "ascan", "bscan"):
        assert low[key] == high[key]
    for key in ("grid_shape", "voxel_depth_um"):
        assert low["metadata"][key] is high["metadata"][key] is None
    assert low["metadata"]["depth_samples_used"] is False
    assert low["metadata"]["path_model"] == "continuous_columns_v1"
    other = run(request(noise=True, seed=10))
    assert other["xray"]["image"] != low["xray"]["image"]
    assert other["sam"] == low["sam"]


def test_roi_matches_aligned_larger_field_including_outside_psf_material():
    twin = {"name": "Independent off-ROI material", "size_mm": [1, 1, 1.6], "objects": [
        {"id": "halo", "name": "halo", "shape": "box", "material": "silicon",
         "center_mm": [.23828125, .5, .6017], "size_mm": [.0234375, 1, .8]},
        {"id": "remote", "name": "remote", "shape": "box", "material": "copper",
         "center_mm": [.60, .61, .8], "size_mm": [.13, .11, .17]}]}
    full = run(request(twin, resolution=128, probe_x_mm=.40, probe_y_mm=.45))
    cropped = run(request(twin, resolution=64, roi_mm=[.25, .25, .75, .75], probe_x_mm=.40, probe_y_mm=.45))
    for modality in ("xray", "sam"):
        np.testing.assert_allclose(cropped[modality]["image"], np.asarray(full[modality]["image"])[32:96, 32:96], atol=3e-7)
    np.testing.assert_allclose(cropped["ascan"]["amplitude"], full["ascan"]["amplitude"], atol=3e-7)
    assert np.asarray(cropped["sam"]["image"])[:, 0].max() > .01
    assert cropped["sam"]["extent_mm"] == [.25, .75, .25, .75]
    assert cropped["metadata"]["padded_shape_yx"][0] > 64


def test_continuous_preview_rejects_tilt_in_schema_and_engine():
    with pytest.raises(ValidationError, match="zero-angle|normal incidence|0"):
        request(angle_deg=1)
    r = request()
    settings = r.settings.model_dump() | {"angle_deg": 1}
    with pytest.raises(ValueError, match="zero-angle"):
        simulate(r.twin.model_dump(mode="json"), settings)


def test_preview_prepass_and_final_rf_allocations_have_separate_resource_guards(monkeypatch):
    import virtual_microscopy.column_paths as paths
    import virtual_microscopy.continuous_sam as shared
    import virtual_microscopy.continuous_preview as preview
    original = paths.build_column_paths
    monkeypatch.setattr(shared, "MAX_PEAK_BYTES", 1)
    monkeypatch.setattr(paths, "build_column_paths", lambda *args, **kwargs: pytest.fail("Path allocation before preflight"))
    with pytest.raises(ValueError, match="workspace"):
        run(request())
    monkeypatch.setattr(shared, "MAX_PEAK_BYTES", 512*1024**2)
    monkeypatch.setattr(shared, "MAX_RF_WORK_CELLS", 1)
    monkeypatch.setattr(paths, "build_column_paths", original)
    monkeypatch.setattr(preview, "synthesize_echo_tile", lambda *args, **kwargs: pytest.fail("RF allocation before full preflight"))
    with pytest.raises(ValueError, match="RF computation"):
        run(request())


def test_practical_h100_preview_is_explicitly_admitted_with_complete_trace():
    from tools.build_h100_example import h100
    from virtual_microscopy.hbm import compose_hbm
    twin = compose_hbm(h100(), "hbm-6", {"microstructure": {}})
    x, y = twin["hbm_assemblies"][5]["center_xy_mm"]
    r = request(twin, frequency_mhz=100, depth_samples=1024, focus_mm=.55,
        roi_mm=[x-.075, y-.125, x+.075, y+.125], gate_start_us=.26, gate_end_us=.7,
        probe_x_mm=x+.025, probe_y_mm=y+.05)
    result = run(r)
    metadata = result["metadata"]
    assert metadata["estimated_peak_bytes"] <= 512*1024**2
    assert metadata["rf_max_tile_work_cells"] <= 8_000_000
    assert metadata["rf_work_cells"] <= 180_000_000
    assert metadata["path_candidate_tests"] <= 50_000_000
    assert metadata["path_event_work_units"] <= 250_000_000
    assert result["ascan"]["time_us"][-1] > 1.2
    assert result["sam"]["extent_mm"] == [x-.075, x+.075, y-.125, y+.125]
    assert all(feature["samples_xyz"][2] is None for feature in metadata["microfeature_sampling"])
