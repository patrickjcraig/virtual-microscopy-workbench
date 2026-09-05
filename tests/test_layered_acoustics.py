"""Independent boundary/series/energy oracles for scalar layered acoustics."""
from math import exp, log, pi, sqrt

import numpy as np
import pytest

from virtual_microscopy.layered_acoustics import (
    layered_response, slab_impulse_series, slab_rf_response)


def coefficients(a, b):
    return (b-a)/(b+a), 2*b/(a+b)


@pytest.mark.parametrize("impedances", [[1.5, 12.], [12., 1.5], [1.5, 1.5]])
def test_one_interface_pressure_coefficients_and_energy_in_both_directions(impedances):
    f = [-100., 0., 27., 100.]
    result = layered_response([], impedances, [], f)
    r, t = coefficients(*impedances)
    np.testing.assert_array_equal(result.reflection, np.full(4, r, complex))
    np.testing.assert_array_equal(result.transmission, np.full(4, t, complex))
    np.testing.assert_array_equal(result.primary_reflection, result.reflection)
    np.testing.assert_array_equal(result.direct_transmission, result.transmission)
    np.testing.assert_allclose(result.reflectance+result.transmittance, 1, atol=2e-15)
    assert result.reflection.dtype == np.complex128 and result.reflectance.dtype == np.float64
    if impedances[1] > impedances[0]:
        assert t > 1 and result.transmittance[0] <= 1


def test_matched_layer_propagation_phase_and_constant_pressure_loss():
    f = np.array([0., 1.25, 3., 15.])
    d, speed, loss = .37, 5000., 2.7
    result = layered_response([d], [7., 7., 7.], [speed], f, [loss])
    expected = np.exp(-log(10)*loss*d/20)*np.exp(-2j*pi*f*d/(speed/1000))
    np.testing.assert_array_equal(result.reflection, np.zeros(len(f)))
    np.testing.assert_allclose(result.transmission, expected, atol=1e-15)
    np.testing.assert_allclose(result.direct_transmission, expected, atol=1e-15)
    np.testing.assert_allclose(result.transmittance, 10**(-loss*d/10), atol=1e-15)
    assert np.all(result.absorptance > 0)


@pytest.mark.parametrize("z", [[1.5, 13., 3.], [8., 2., 5.], [1.5, 13., 1.5]])
@pytest.mark.parametrize("loss", [0., 4.])
def test_slab_frequency_matches_independent_enumerated_echo_and_transmission_series(z, loss):
    # Independent infinite-series approximation: 600 paths make the omitted
    # absolute amplitude <1e-30 for these moderate contrasts.
    f = np.linspace(-93, 93, 251)
    d, c = .43, 4.8
    r01, t01 = coefficients(z[0], z[1])
    r12, t12 = coefficients(z[1], z[2])
    r10, t10 = coefficients(z[1], z[0])
    decay = 10**(-loss*d/20)
    q = r10*r12*decay**2
    expected_r = np.full(len(f), r01, dtype=complex)
    expected_t = np.zeros(len(f), complex)
    for order in range(600):
        expected_r += t01*t10*r12*decay**2*q**order*np.exp(-2j*pi*f*(order+1)*2*d/c)
        expected_t += t01*t12*decay*q**order*np.exp(-2j*pi*f*(2*order+1)*d/c)
    result = layered_response([d], z, [c*1000], f, [loss])
    np.testing.assert_allclose(result.reflection, expected_r, atol=2e-14, rtol=2e-14)
    np.testing.assert_allclose(result.transmission, expected_t, atol=2e-14, rtol=2e-14)
    primary = r01+t01*t10*r12*decay**2*np.exp(-2j*pi*f*2*d/c)
    direct = t01*t12*decay*np.exp(-2j*pi*f*d/c)
    np.testing.assert_allclose(result.primary_reflection, primary, atol=1e-14)
    np.testing.assert_allclose(result.direct_transmission, direct, atol=1e-14)


def test_arbitrary_multilayer_lossless_flux_conservation_and_negative_frequency_symmetry():
    rng = np.random.default_rng(4811)
    d, z, c = rng.uniform(.005, .15, 13), rng.uniform(.8, 32, 15), rng.uniform(1000, 9000, 13)
    f = np.linspace(0, 150, 1001)
    forward = layered_response(d, z, c, f)
    reverse_frequency = layered_response(d, z, c, -f)
    np.testing.assert_allclose(forward.reflectance+forward.transmittance, 1, atol=1e-12)
    np.testing.assert_array_equal(reverse_frequency.reflection, forward.reflection.conjugate())
    np.testing.assert_array_equal(reverse_frequency.transmission, forward.transmission.conjugate())
    # Reciprocity is pressure-impedance weighted, not equality of pressure T.
    reversed_stack = layered_response(d[::-1], z[::-1], c[::-1], f)
    np.testing.assert_allclose(reversed_stack.transmittance, forward.transmittance, atol=1e-12)


def test_zero_thickness_and_identical_material_subdivision_invariance():
    f = np.linspace(0, 150, 1201)
    base = layered_response([.3], [1.5, 12., 4.], [5000], f, [.4])
    split = layered_response([.1, .2], [1.5, 12., 12., 4.], [5000, 5000], f, [.4, .4])
    inserted = layered_response([0., .3, 0.], [1.5, .0004, 12., 30., 4.], [300, 5000, 2000], f, [10, .4, 50])
    for name in ("reflection", "transmission", "primary_reflection", "direct_transmission"):
        np.testing.assert_allclose(getattr(split, name), getattr(base, name), atol=5e-14)
        np.testing.assert_array_equal(getattr(inserted, name), getattr(base, name))
    collapsed = layered_response([0.], [1.5, .0004, 4.], [300.], f)
    direct = layered_response([], [1.5, 4.], [], f)
    np.testing.assert_array_equal(collapsed.reflection, direct.reflection)


def test_strong_absorption_is_stable_and_passive_without_growing_exponentials():
    result = layered_response([2., 1.], [1.5, 13., .0004, 3.], [5000, 300], [0., 50., 100.], [1e6, 1e6])
    r, _ = coefficients(1.5, 13.)
    np.testing.assert_allclose(result.reflection, np.full(3, r, complex), atol=2e-16, rtol=0)
    np.testing.assert_array_equal(result.transmission, np.zeros(3, complex))
    assert np.all(result.absorptance >= 0)


def test_loss_may_increase_coherent_reflection_at_a_lossless_transmission_resonance():
    # Equal exteriors and a half-wave layer: lossless front/back returns cancel.
    a = layered_response([.5], [1.5, 10., 1.5], [5000], [5.])
    b = layered_response([.5], [1.5, 10., 1.5], [5000], [5.], [5.])
    assert a.reflectance[0] < 1e-28
    assert b.reflectance[0] > .01 and b.absorptance[0] > 0


def test_slab_impulse_polarity_spacing_and_global_tail_certificate():
    z, d, speed, loss = [8., 2., 5.], .2, 4000., .7
    series = slab_impulse_series(d, z, speed, loss, absolute_tolerance=1e-11)
    r01, t01 = coefficients(z[0], z[1])
    r12, _ = coefficients(z[1], z[2])
    r10, t10 = coefficients(z[1], z[0])
    a1 = t01*t10*r12*10**(-2*loss*d/20)
    q = r10*r12*10**(-2*loss*d/20)
    n = series.diagnostics["internal_echo_count"]
    np.testing.assert_allclose(series.times_us, np.arange(n+1)*2*d/(speed/1000), atol=1e-15)
    np.testing.assert_allclose(series.amplitudes, [r01]+[a1*q**i for i in range(n)], atol=1e-16)
    expected_tail = abs(a1)*abs(q)**n/(1-abs(q))
    assert series.diagnostics["omitted_amplitude_l1_bound"] == pytest.approx(expected_tail, rel=1e-13)
    assert expected_tail <= 1e-11
    if n:
        assert abs(a1)*abs(q)**(n-1)/(1-abs(q)) > 1e-11


def test_negative_return_ratio_alternates_internal_polarity():
    series = slab_impulse_series(.5, [1., 4., 8.], 5000., absolute_tolerance=1e-10)
    assert series.diagnostics["signed_round_trip_ratio"] < 0
    assert np.all(series.amplitudes[1:-1]*series.amplitudes[2:] < 0)


def test_high_contrast_air_tail_rejects_required_echo_count_without_relaxation():
    with pytest.raises(ValueError, match="global slab tail certificate requires"):
        slab_impulse_series(.01, [40., .0004, 40.], 343., absolute_tolerance=1e-8, max_echoes=100000)


def test_zero_thickness_slab_is_direct_interface_without_infinite_zero_time_events():
    series = slab_impulse_series(0., [1.5, .0004, 5.], 343.)
    np.testing.assert_array_equal(series.times_us, [0.])
    np.testing.assert_array_equal(series.amplitudes, [coefficients(1.5, 5.)[0]])
    assert series.diagnostics["omitted_amplitude_l1_bound"] == 0
    assert series.diagnostics["zero_thickness_collapsed"]


def pulse_oracle(time, times, amplitudes, f, bandwidth, shift=0):
    sigma = sqrt(2*log(2))/(pi*bandwidth*f)
    signal = np.zeros(len(time), complex)
    for center, amplitude in zip(times, amplitudes):
        delta = time-center-shift
        valid = abs(delta) <= 4*sigma
        signal[valid] += amplitude*np.exp(-.5*(delta[valid]/sigma)**2)*np.exp(2j*pi*f*delta[valid])
    return signal


def test_causal_complex_rf_matches_independent_series_and_envelope_is_not_abs_rf():
    d, z, speed, f, bw = .4, [1.5, 8., 2.], 4000., 25., .5
    time = np.arange(1801)/800
    result = slab_rf_response(d, z, speed, time, f, bw, surface_time_us=.17, absolute_tolerance=1e-11)
    # Enumerate 200 analytic terms independently of the engine series.
    r01, t01 = coefficients(z[0], z[1])
    r10, t10 = coefficients(z[1], z[0])
    r12, _ = coefficients(z[1], z[2])
    echo_times = np.arange(201)*2*d/(speed/1000)
    amplitudes = [r01]+[t01*t10*r12*(r10*r12)**i for i in range(200)]
    expected = pulse_oracle(time, echo_times, amplitudes, f, bw, .17)
    np.testing.assert_allclose(result.rf, expected.real, atol=1e-11)
    np.testing.assert_allclose(result.envelope, abs(expected), atol=1e-11)
    expected_primary = pulse_oracle(time, echo_times[:2], amplitudes[:2], f, bw, .17)
    np.testing.assert_allclose(result.primary_rf, expected_primary.real, atol=2e-14)
    np.testing.assert_allclose(result.primary_envelope, abs(expected_primary), atol=2e-14)
    assert np.max(result.envelope-abs(result.rf)) > .1
    assert np.any(result.rf < 0)
    assert result.diagnostics["omitted_rf_peak_bound"] <= 1e-11
    assert np.all(result.rf[time < .17-result.diagnostics["pulse_support_half_width_us"]] == 0)


def test_record_window_independence_and_pulses_just_outside_both_edges():
    full_time = np.arange(1201)/800
    kwargs = dict(thickness_mm=.4, impedances_mrayl=[1.5, 8., 2.], sound_speed_m_s=4000.,
                  center_frequency_mhz=25., surface_time_us=.1)
    full = slab_rf_response(time_us=full_time, **kwargs)
    # Echo centers .1,.3,.5...; the first/last centers outside this record
    # still have pulse tails inside it. A record does not restart the physics.
    mask = (full_time >= .11) & (full_time <= .49)
    cut = slab_rf_response(time_us=full_time[mask], **kwargs)
    np.testing.assert_array_equal(cut.rf, full.rf[mask])
    np.testing.assert_array_equal(cut.envelope, full.envelope[mask])
    assert cut.envelope[0] > 0 and cut.envelope[-1] > 0


def test_compact_support_endpoint_rounding_does_not_skip_a_valid_sample():
    # Addition rounds center-half above t=.001, although actual t-center
    # subtraction equals -half. A post-evaluation mask alone cannot recover it.
    time, shift = np.arange(51)/1000, .7505625005171104
    result = slab_rf_response(.3, [1.48, 8., 8.], 5000., time, 10., .2, surface_time_us=shift)
    expected = pulse_oracle(time, [0.], [coefficients(1.48, 8.)[0]], 10., .2, shift)
    assert expected[1].real == pytest.approx(-.00023063185781672113, abs=1e-18)
    np.testing.assert_allclose(result.rf, expected.real, rtol=0, atol=1e-18)
    np.testing.assert_allclose(result.primary_rf, expected.real, rtol=0, atol=1e-18)
    assert result.diagnostics["omitted_rf_peak_bound"] == 0


def test_compact_support_rounding_handles_multiple_nonuniform_centers_near_endpoint():
    shift = .7505625005171104
    time = .001+np.arange(-12, 13)*np.spacing(.001)
    result = slab_rf_response(.3, [1.48, 8., 8.], 5000., time, 10., .2, surface_time_us=shift)
    expected = pulse_oracle(time, [0.], [coefficients(1.48, 8.)[0]], 10., .2, shift)
    assert np.count_nonzero(expected) > 1
    np.testing.assert_allclose(result.rf, expected.real, rtol=0, atol=1e-18)
    assert result.diagnostics["pulse_contributing_echo_count"] == 1
    outside = slab_rf_response(.3, [1.48, 8., 8.], 5000., [.0, .0001], 10., .2, surface_time_us=2.)
    assert outside.diagnostics["pulse_contributing_echo_count"] == 0
    assert outside.diagnostics["primary_contributing_echo_count"] == 0


def test_tighter_tail_reduces_certified_error_against_independent_long_series():
    time = np.arange(6001)/800
    kwargs = dict(thickness_mm=.02, impedances_mrayl=[1.5, 20., 1.5], sound_speed_m_s=4000.,
                  time_us=time, center_frequency_mhz=25.)
    coarse = slab_rf_response(**kwargs, absolute_tolerance=1e-3)
    fine = slab_rf_response(**kwargs, absolute_tolerance=1e-10)
    bound = coarse.diagnostics["omitted_rf_peak_bound"]+fine.diagnostics["omitted_rf_peak_bound"]
    assert np.max(abs(coarse.rf-fine.rf)) <= bound+1e-14
    assert coarse.series.diagnostics["echo_count"] < fine.series.diagnostics["echo_count"]
    assert fine.diagnostics["omitted_rf_peak_bound"] < coarse.diagnostics["omitted_rf_peak_bound"]


def test_time_sampling_refinement_preserves_common_centers_for_off_grid_returns():
    kwargs = dict(thickness_mm=.317, impedances_mrayl=[1.5, 8., 2.], sound_speed_m_s=4317.,
                  center_frequency_mhz=37., fractional_bandwidth=.7, surface_time_us=.143)
    coarse = slab_rf_response(time_us=np.arange(1001)/800, **kwargs)
    fine = slab_rf_response(time_us=np.arange(2001)/1600, **kwargs)
    np.testing.assert_array_equal(coarse.rf, fine.rf[::2])
    np.testing.assert_array_equal(coarse.envelope, fine.envelope[::2])
    assert coarse.series.times_us[1]*800 != round(coarse.series.times_us[1]*800)


def test_overlapping_thin_slab_returns_cancel_before_envelope():
    time = np.arange(401)/1600
    result = slab_rf_response(.0001, [1.5, 12., 1.5], 5000., time, 50., surface_time_us=.125)
    sigma = result.diagnostics["pulse_sigma_us"]
    # Independent incoherent envelope sum is deliberately the wrong observable.
    incoherent = np.zeros(len(time))
    for center, amplitude in zip(result.series.times_us, result.series.amplitudes):
        dt = time-center-.125
        incoherent += abs(amplitude)*np.exp(-.5*(dt/sigma)**2)*(abs(dt) <= 4*sigma)
    assert np.max(incoherent) > 1
    assert np.max(result.envelope) < .1*np.max(incoherent)


def test_primary_first_return_is_retained_when_global_tail_tolerance_omits_it():
    time = np.arange(801)/800
    result = slab_rf_response(.4, [1.5, 8., 8.001], 4000., time, 25.,
                              surface_time_us=.1, absolute_tolerance=1e-3)
    assert len(result.series.times_us) == 1
    index = int(round((.1+.2)*800))
    assert result.primary_envelope[index] > 0
    assert result.envelope[index] == 0
    assert abs(result.primary_rf[index]) <= result.diagnostics["omitted_rf_peak_bound"]


def test_input_arrays_remain_unchanged_and_output_does_not_alias_inputs():
    d, z, c, f = np.array([.3]), np.array([1.5, 5., 2.]), np.array([4000.]), np.arange(50.)
    copies = [v.copy() for v in (d, z, c, f)]
    layered_response(d, z, c, f)
    for a, b in zip((d, z, c, f), copies): np.testing.assert_array_equal(a, b)
    time = np.arange(100)/800
    rf = slab_rf_response(.3, z, 4000., time, 25.)
    assert not np.shares_memory(rf.time_us, time)


@pytest.mark.parametrize("change", [
    {"thickness_mm": [-1.]}, {"impedances_mrayl": [1., 0., 2.]},
    {"impedances_mrayl": [1., 2.]}, {"sound_speeds_m_s": [0.]},
    {"pressure_loss_db_mm": [-1.]}, {"frequency_mhz": [float("nan")]},
    {"frequency_mhz": [1j]}, {"frequency_mhz": [True]},
    {"thickness_mm": [[.3]*100]}, {"frequency_mhz": []},
    {"impedances_mrayl": [1e-300, 1e300, 1.]},
    {"sound_speeds_m_s": [1e-300]},
])
def test_invalid_or_unrepresentable_inputs_rejected(change):
    kwargs = dict(thickness_mm=[.3], impedances_mrayl=[1.5, 5., 2.],
                  sound_speeds_m_s=[4000.], frequency_mhz=[50.])
    with pytest.raises(ValueError): layered_response(**(kwargs | change))


def test_frequency_and_rf_resource_preflight_precedes_large_synthesis(monkeypatch):
    import virtual_microscopy.layered_acoustics as engine
    with pytest.raises(ValueError, match="16385"):
        layered_response([.3], [1.5, 5., 2.], [4000.], np.zeros(16386))
    with pytest.raises(ValueError, match="256"):
        layered_response([.3]*257, [1.5]*259, [4000.]*257, [25.])
    monkeypatch.setattr(engine, "MAX_RF_WORK", 1)
    with pytest.raises(ValueError, match="pulse-sample work"):
        slab_rf_response(.3, [1.5, 5., 2.], 4000., np.arange(100)/800, 25.)
    monkeypatch.setattr(engine, "MAX_WORKSPACE_BYTES", 1)
    with pytest.raises(ValueError, match="workspace"):
        layered_response([.3], [1.5, 5., 2.], [4000.], [25.])


@pytest.mark.parametrize("kwargs", [
    {"time_us": [0., .1]}, {"time_us": [0., 0.]}, {"time_us": [-.01, 0.]},
    {"fractional_bandwidth": .01}, {"absolute_tolerance": 0}, {"max_echoes": True},
    {"surface_time_us": float("inf")}, {"surface_time_us": -1.},
])
def test_rf_validation(kwargs):
    defaults = dict(thickness_mm=.3, impedances_mrayl=[1.5, 5., 2.], sound_speed_m_s=4000.,
                    time_us=np.arange(100)/800, center_frequency_mhz=25.)
    with pytest.raises(ValueError): slab_rf_response(**(defaults | kwargs))
