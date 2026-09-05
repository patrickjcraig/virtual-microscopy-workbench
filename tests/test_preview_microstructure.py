"""Fine-ROI preview tiling preserves complete temporal and spatial responses."""

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from virtual_microscopy.physics import Echoes, MaterialGrid, _sam_signals, simulate


def settings(**changes):
    return {"frequency_mhz": 100, "focus_mm": .55, "gate_start_us": .26, "gate_end_us": .30,
            "probe_x_mm": 1.292, "probe_y_mm": 2.405, **changes}


def test_visible_tile_responses_equal_independent_full_field_pulses_across_gate_edges(monkeypatch):
    import virtual_microscopy.physics as engine

    pitch = np.array([.008, .01, .00625])
    grid = MaterialGrid(np.zeros((29, 31, 128), np.uint8), pitch * [31, 29, 128], pitch, [],
                        np.array([1.2, 2.3, 0.]), (slice(5, 24), slice(6, 26)))
    # Two pulse centers lie outside opposite gate boundaries; another later
    # echo must remain in the full probe/B-scan record. Two sources lie in XY
    # halo columns/rows outside the returned ROI.
    echoes = Echoes(np.array([4, 21, 10, 8]), np.array([10, 25, 11, 5]),
                    np.array([.25, .315, .5013, .27365]), np.array([.7, -.6, .4, -.3]), .5013)
    monkeypatch.setattr(engine, "acoustic_echoes", lambda *args, **kwargs: echoes)
    result = _sam_signals(grid, settings(), full_image=True)
    time = np.asarray(result["ascan"]["time_us"])
    n = np.arange(len(time))
    dt, sigma, half = 1/800, .75/100, 24
    expected = np.zeros((29, 31, len(time)), dtype=np.complex128)
    # Evaluate the old interpolation law directly at every output sample,
    # independently of FFT convolution, tile decomposition and deposition.
    for row, col, arrival, amplitude in zip(echoes.rows, echoes.cols, echoes.times_us, echoes.amplitudes):
        position = arrival/dt
        left = int(np.floor(position))
        fraction = position-left
        offset = n-left
        left_envelope = np.where(abs(offset) <= half, np.exp(-.5*(offset*dt/sigma)**2), 0)
        right_envelope = np.where(abs(offset-1) <= half, np.exp(-.5*((offset-1)*dt/sigma)**2), 0)
        expected[row, col] += amplitude * ((1-fraction)*left_envelope + fraction*right_envelope) * np.exp(2j*np.pi*100*(time-arrival))
    sigma_mm = 1.02*2*1.48/100/np.sqrt(8*np.log(2))
    expected = gaussian_filter(expected, (sigma_mm/pitch[1], sigma_mm/pitch[0], 0), mode="nearest")
    envelope = abs(expected)
    gate = (time >= .26) & (time <= .30)
    np.testing.assert_allclose(result["image"][grid.image_slices], envelope[:, :, gate].max(axis=2)[grid.image_slices], atol=3e-7)
    np.testing.assert_allclose(result["ascan"]["amplitude"], expected[10, 11].real, atol=3e-7)
    np.testing.assert_allclose(result["ascan"]["envelope"], envelope[10, 11], atol=3e-7)
    assert result["image"][5, 10] > 1e-3
    assert result["image"][21, 25] > 1e-3
    assert time[np.argmax(result["ascan"]["envelope"])] == pytest.approx(.5013, abs=dt)
    stride = int(np.ceil(len(time)/512))
    expected_bscan = np.maximum.reduceat(envelope[10, 6:26], np.arange(0, len(time), stride), axis=1).T
    np.testing.assert_allclose(result["bscan"]["image"], expected_bscan, atol=3e-7)
    probe = _sam_signals(grid, settings(), full_image=False)
    np.testing.assert_allclose(probe["ascan"]["amplitude"], result["ascan"]["amplitude"], atol=3e-7)
    np.testing.assert_allclose(probe["bscan"]["image"], result["bscan"]["image"], atol=3e-7)
    assert result["rf_tile_rows"] == 16
    halo = int(4*sigma_mm/pitch[1]+.5)
    assert result["rf_work_cells"] == sum((min(29, stop+halo)-max(0, start-halo))*31*(len(time)+48)
                                         for start, stop in ((5, 21), (21, 24)))


@pytest.mark.parametrize("shape,pitch,latest", [((64, 64, 128), [.001, .001, .00625], 11.9),
                                                   ((256, 100, 128), [.005, .0015, .00625], 1.25)])
def test_preview_hard_tile_and_aggregate_budgets_still_reject_before_rf_allocation(monkeypatch, shape, pitch, latest):
    import virtual_microscopy.physics as engine

    pitch = np.asarray(pitch)
    grid = MaterialGrid(np.zeros(shape, np.uint8), pitch*np.array(shape)[[1, 0, 2]], pitch, [])
    echoes = Echoes(np.array([0]), np.array([0]), np.array([latest]), np.array([.1]), latest)
    monkeypatch.setattr(engine, "acoustic_echoes", lambda *args, **kwargs: echoes)
    original_zeros = np.zeros

    def no_rf_allocation(*args, **kwargs):
        if np.issubdtype(np.dtype(kwargs.get("dtype", float)), np.complexfloating):
            pytest.fail("Over-budget previews must fail before allocating complex RF work.")
        return original_zeros(*args, **kwargs)

    monkeypatch.setattr(engine.np, "zeros", no_rf_allocation)
    with pytest.raises(ValueError, match="RF computation budget"):
        _sam_signals(grid, settings(probe_x_mm=.02, probe_y_mm=.02), full_image=True)


def test_actual_h100_microstructure_preview_preserves_full_trace_within_hard_budgets():
    from tools.build_h100_example import h100
    from virtual_microscopy.hbm import compose_hbm, microstructure_summary
    from virtual_microscopy.schemas import SimulationRequest

    twin = compose_hbm(h100(), "hbm-6", {"microstructure": {}})
    roi = microstructure_summary(twin["hbm_assemblies"][5])["roi_mm"]
    request = SimulationRequest.model_validate({"twin": twin, "settings": {
        "resolution": 64, "depth_samples": 1024, "roi_mm": roi, "frequency_mhz": 100,
        "gate_start_us": .26, "gate_end_us": .7, "focus_mm": .55,
        "probe_x_mm": 49.525, "probe_y_mm": 40.05, "noise": False}})
    result = simulate(request.twin.model_dump(mode="json"), request.settings.model_dump())
    assert np.asarray(result["xray"]["image"]).shape == (64, 64)
    assert np.asarray(result["sam"]["image"]).shape == (64, 64)
    assert result["metadata"]["grid_shape"] == [102, 116, 1024]
    assert result["metadata"]["rf_tile_rows"] == 16
    assert result["metadata"]["rf_max_tile_work_cells"] <= 8_000_000
    assert result["metadata"]["rf_work_cells"] <= 180_000_000
    assert result["ascan"]["time_us"][-1] > 1.28 > request.settings.gate_end_us
    assert min(result["ascan"]["amplitude"]) < 0 < max(result["ascan"]["amplitude"])
