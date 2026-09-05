"""Independent causal pressure oracles for the general gamma-pulse instrument.

The oracles enumerate physical transmitted/reflected events. They do not call
the frequency recurrence, the production gamma helper, or the v0.11 slab series.
"""
from copy import deepcopy
import heapq
import json
import math
from pathlib import Path

import numpy as np
import pytest

from virtual_microscopy.layered_time import estimate_causal_gamma, causal_gamma_response
import virtual_microscopy.layered_time as time_kernel


def medium(z, speed=1480.0):
    return {"name": "Explicit test medium", "impedance_mrayl": float(z),
            "sound_speed_m_s": float(speed)}


def layer(d=.25, z=8., speed=5000., loss=0.):
    return {**medium(z, speed), "thickness_mm": float(d), "pressure_loss_db_mm": float(loss)}


def stack(layers=None, incident=1.48, terminal=1.48):
    return {"incident": medium(incident), "terminal": medium(terminal),
            "layers": [layer()] if layers is None else layers}


def settings(**changes):
    return {"center_frequency_mhz": 50., "fractional_bandwidth": .5,
            "gamma_order": 12, "absolute_tolerance": 1e-7,
            "precision_bits": 128, "surface_standoff_mm": 0., **changes}


def gamma_oracle(time, config):
    """Analytic gamma pulse, in ordinary binary64 for independent comparisons."""
    time = np.asarray(time, dtype=float)
    order = config["gamma_order"]
    frequency = config["center_frequency_mhz"]
    # Amplitude half-maximum occurs at +/- fractional_bandwidth*frequency/2.
    rate = math.pi*frequency*config["fractional_bandwidth"] / math.sqrt(
        math.expm1(2*math.log(2)/(order+1)))
    result = np.zeros(time.shape, dtype=complex)
    positive = time > 0
    x = rate*time[positive]
    magnitude = np.exp(order*(1+np.log(x/order))-x)
    phase = 2*math.pi*frequency*(time[positive]-order/rate)
    result[positive] = magnitude*(np.cos(phase)+1j*np.sin(phase))
    return result


def pressure_coefficients(z_from, z_to):
    return (z_to-z_from)/(z_to+z_from), 2*z_to/(z_to+z_from)


def slab_oracle(specimen, time, config):
    finite = specimen["layers"][0]
    z0 = specimen["incident"]["impedance_mrayl"]
    z1 = finite["impedance_mrayl"]
    z2 = specimen["terminal"]["impedance_mrayl"]
    r01, t01 = pressure_coefficients(z0, z1)
    r10, t10 = pressure_coefficients(z1, z0)
    r12, _ = pressure_coefficients(z1, z2)
    delay = 2000*finite["thickness_mm"]/finite["sound_speed_m_s"]
    decay = 10**(-2*finite["pressure_loss_db_mm"]*finite["thickness_mm"]/20)
    surface = 2000*config["surface_standoff_mm"]/specimen["incident"]["sound_speed_m_s"]
    time = np.asarray(time)-surface
    answer = r01*gamma_oracle(time, config)
    # Causality makes every later event exactly irrelevant, independent of the
    # gamma pulse's infinite future tail. No echo-amplitude floor is used.
    last = max(0, math.floor(float(time.max())/delay))
    for n in range(1, last+1):
        amplitude = t01*t10*r12*decay*(r10*r12*decay)**(n-1)
        answer += amplitude*gamma_oracle(time-n*delay, config)
    return answer


def event_oracle(specimen, time, config):
    """Exact finite causal event topology for a short, positive-layer fixture."""
    layers = specimen["layers"]
    z = [specimen["incident"]["impedance_mrayl"]] + [p["impedance_mrayl"] for p in layers] + [specimen["terminal"]["impedance_mrayl"]]
    delay = [1000*p["thickness_mm"]/p["sound_speed_m_s"] for p in layers]
    assert all(t > 0 for t in delay)
    attenuation = [10**(-p["pressure_loss_db_mm"]*p["thickness_mm"]/20) for p in layers]
    surface = 2000*config["surface_standoff_mm"]/specimen["incident"]["sound_speed_m_s"]
    relative_time = np.asarray(time)-surface
    end = float(relative_time.max())
    reflection, transmission = pressure_coefficients(z[0], z[1])
    answer = reflection*gamma_oracle(relative_time, config)
    queue = [(0., 0, 1, transmission)]
    processed = 0
    while queue:
        start, index, direction, amplitude = heapq.heappop(queue)
        arrival = start+delay[index]
        if arrival > end:
            continue
        processed += 1
        assert processed < 100000, "Independent oracle fixture must remain bounded."
        amplitude *= attenuation[index]
        next_medium = index+2 if direction == 1 else index
        r, t = pressure_coefficients(z[index+1], z[next_medium])
        heapq.heappush(queue, (arrival, index, -direction, amplitude*r))
        next_layer = index+direction
        if 0 <= next_layer < len(layers):
            heapq.heappush(queue, (arrival, next_layer, direction, amplitude*t))
        elif direction == -1:
            answer += amplitude*t*gamma_oracle(relative_time-arrival, config)
    return answer, processed


def complex_output(result):
    return np.asarray(result["rf"])+1j*np.asarray(result["imaginary"])


def assert_certified(result, expected, requested):
    diagnostics = result["diagnostics"]
    bound = diagnostics["total_error_bound"]
    actual = complex_output(result)
    assert 0 <= bound <= requested
    assert np.isfinite(actual).all()
    assert np.max(np.abs(actual-expected)) <= bound
    assert np.max(np.abs(np.asarray(result["envelope"])-np.abs(expected))) <= bound
    return float(np.max(np.abs(actual-expected)))


@pytest.mark.parametrize("incident,terminal", [(1.48, 12.), (12., 1.48), (7., 7.)])
def test_single_interface_preserves_complex_carrier_polarity_and_causal_onset(incident, terminal):
    specimen, config = stack([], incident, terminal), settings()
    time = np.linspace(0, .3, 121).tolist()
    result = causal_gamma_response(specimen, time, config)
    r, _ = pressure_coefficients(incident, terminal)
    expected = r*gamma_oracle(time, config)
    assert_certified(result, expected, config["absolute_tolerance"])
    assert result["time_us"] == time
    if incident != terminal:
        assert np.max(np.abs(expected.imag)) > .1


@pytest.mark.parametrize("z,loss", [([1.48, 8., 1.48], 0.), ([8., 2., 5.], .7), ([1.48, .0004, 8.], 0.), ([1.48, 8., 3.], 100.)])
def test_general_slab_matches_independent_finite_causal_series(z, loss):
    specimen = stack([layer(d=.175, z=z[1], speed=4000., loss=loss)], z[0], z[2])
    config = settings(surface_standoff_mm=.05)
    time = np.linspace(0, .8, 321).tolist()
    expected = slab_oracle(specimen, time, config)
    assert_certified(causal_gamma_response(specimen, time, config), expected, config["absolute_tolerance"])


def test_two_unequal_layers_match_independent_pressure_event_paths():
    specimen = stack([layer(.25, 8., 4000., .7), layer(.32, 3., 6400., 2.)], 1.48, 5.)
    config = settings(surface_standoff_mm=.03)
    time = np.linspace(0, .5, 201).tolist()
    expected, count = event_oracle(specimen, time, config)
    assert count > 20
    assert_certified(causal_gamma_response(specimen, time, config), expected, config["absolute_tolerance"])


def test_matched_stack_reflection_is_zero_with_unequal_speeds_and_loss():
    specimen = stack([layer(.2, 5., 1300., 100.), layer(.13, 5., 6400., .7)], 5., 5.)
    config, time = settings(), np.linspace(0, .6, 241).tolist()
    result = causal_gamma_response(specimen, time, config)
    assert_certified(result, np.zeros(len(time), complex), config["absolute_tolerance"])


def test_zero_thickness_and_identical_layer_subdivision_preserve_response():
    base = stack([layer(.375, 8., 4000., .7)], 1.48, 3.)
    split = stack([layer(.125, 8., 4000., .7), layer(.25, 8., 4000., .7)], 1.48, 3.)
    inserted = stack([layer(0., .0004, 300., 100.), layer(.375, 8., 4000., .7), layer(0., 70., 5000., 20.)], 1.48, 3.)
    config, time = settings(), np.linspace(0, .8, 321).tolist()
    expected = slab_oracle(base, time, config)
    for specimen in (base, split, inserted):
        assert_certified(causal_gamma_response(specimen, time, config), expected, config["absolute_tolerance"])


def test_actual_nonuniform_float_time_centers_and_input_bytes_are_preserved():
    specimen, config = stack([], 1.48, 8.), settings()
    time = sorted((np.arange(81)/400).tolist()+[.001, .0010000000000000002, .023, .03125, .0731])
    original = deepcopy((specimen, time, config))
    result = causal_gamma_response(specimen, time, config)
    expected = pressure_coefficients(1.48, 8.)[0]*gamma_oracle(time, config)
    assert_certified(result, expected, config["absolute_tolerance"])
    assert (specimen, time, config) == original
    assert result["time_us"] == time


def test_nonzero_record_start_and_different_periods_agree_with_combined_bound():
    specimen, config = stack([layer(.175, 8., 4000.)]), settings()
    full_time = np.arange(201, dtype=float)/400
    full = causal_gamma_response(specimen, full_time.tolist(), config)
    # A different end time changes the automatic inversion period; shared actual
    # time values must still represent the same physical recording.
    start, stop = 19, 98
    cropped = causal_gamma_response(specimen, full_time[start:stop].tolist(), config)
    assert cropped["diagnostics"]["period_us"] != full["diagnostics"]["period_us"]
    difference = np.abs(complex_output(cropped)-complex_output(full)[start:stop])
    assert difference.max() <= cropped["diagnostics"]["total_error_bound"]+full["diagnostics"]["total_error_bound"]
    assert_certified(cropped, slab_oracle(specimen, full_time[start:stop], config), config["absolute_tolerance"])


def test_tighter_tolerance_and_higher_precision_retain_independent_accuracy():
    specimen, time = stack([layer(.2, 3., 4000.)], 8., 5.), np.linspace(0, .6, 241).tolist()
    coarse_config = settings(absolute_tolerance=1e-6, precision_bits=96)
    fine_config = settings(absolute_tolerance=1e-9, precision_bits=192)
    coarse = causal_gamma_response(specimen, time, coarse_config)
    fine = causal_gamma_response(specimen, time, fine_config)
    expected = slab_oracle(specimen, time, fine_config)
    assert_certified(coarse, expected, coarse_config["absolute_tolerance"])
    assert_certified(fine, expected, fine_config["absolute_tolerance"])
    assert fine["diagnostics"]["total_error_bound"] < coarse["diagnostics"]["total_error_bound"]
    assert fine["diagnostics"]["frequency_terms"] >= coarse["diagnostics"]["frequency_terms"]


@pytest.mark.parametrize("time", [[], [-.1, 0.], [0., float("nan")], [0., float("inf")], [0., 0.], [.2, .1]])
def test_invalid_time_axes_rejected(time):
    with pytest.raises((ValueError, TypeError)):
        estimate_causal_gamma(stack(), time, settings())


@pytest.mark.parametrize("field,value", [
    ("center_frequency_mhz", -1.), ("center_frequency_mhz", float("nan")),
    ("fractional_bandwidth", 0.), ("fractional_bandwidth", float("inf")),
    ("gamma_order", 3), ("gamma_order", 25), ("gamma_order", 12.5),
    ("precision_bits", 0), ("absolute_tolerance", 0.),
    ("absolute_tolerance", float("nan")), ("surface_standoff_mm", -.1),
])
def test_invalid_settings_rejected(field, value):
    with pytest.raises((ValueError, TypeError)):
        estimate_causal_gamma(stack(), [0., .001], settings(**{field: value}))


@pytest.mark.parametrize("field,value", [("thickness_mm", -.1), ("thickness_mm", float("inf")), ("impedance_mrayl", 0.), ("sound_speed_m_s", 0.), ("pressure_loss_db_mm", -.1)])
def test_invalid_layer_properties_rejected(field, value):
    specimen = stack()
    specimen["layers"][0][field] = value
    with pytest.raises((ValueError, TypeError)):
        estimate_causal_gamma(specimen, [0., .001], settings())


@pytest.mark.parametrize("order,frequency,bandwidth", [(4, 10., .2), (6, 100., 1.), (24, 150., .5)])
def test_gamma_order_and_bandwidth_extremes_use_the_declared_complex_pulse(order, frequency, bandwidth):
    config = settings(gamma_order=order, center_frequency_mhz=frequency, fractional_bandwidth=bandwidth)
    time = np.linspace(0, .2, 241).tolist()
    expected = pressure_coefficients(3., 8.)[0]*gamma_oracle(time, config)
    result = causal_gamma_response(stack([], 3., 8.), time, config)
    assert_certified(result, expected, config["absolute_tolerance"])


def test_low_precision_cannot_silently_relax_the_requested_certificate():
    specimen, time = stack(), (np.arange(201)/400).tolist()
    config = settings(absolute_tolerance=1e-12, precision_bits=64)
    estimate = estimate_causal_gamma(specimen, time, config)
    assert estimate["requested_tolerance"] == 1e-12
    assert "during synthesis" in estimate["arithmetic_status"]
    with pytest.raises(ValueError, match="combined alias, cutoff and arithmetic"):
        causal_gamma_response(specimen, time, config)
    accepted_config = {**config, "precision_bits": 128}
    result = causal_gamma_response(specimen, time, accepted_config)
    assert_certified(result, slab_oracle(specimen, time, accepted_config), 1e-12)


def test_estimated_resources_and_separate_error_terms_match_the_returned_record():
    specimen, config = stack(), settings()
    time = (np.arange(101)/400).tolist()
    estimate = estimate_causal_gamma(specimen, time, config)
    result = causal_gamma_response(specimen, time, config)
    report = result["diagnostics"]
    assert report["time_samples"] == len(time)
    assert report["frequency_terms"] % 2 == 1
    assert report["layer_frequency_work_units"] == report["frequency_terms"]*2
    assert report["inverse_work_units"] == report["frequency_terms"]*len(time)
    assert 0 < report["estimated_peak_bytes"] <= time_kernel.MAX_PEAK_BYTES
    assert 0 <= report["analytic_alias_bound"] <= config["absolute_tolerance"]/4
    assert 0 <= report["frequency_cutoff_bound"] <= config["absolute_tolerance"]/4
    assert report["arithmetic_complex_bound"] >= 0
    assert report["arithmetic_envelope_bound"] >= 0
    assert report["total_error_bound"] <= config["absolute_tolerance"]
    assert report["period_us"] > time[-1]
    assert report["minimum_denominator_lower_bound"] > 0
    for key in ("frequency_terms", "inverse_work_units", "estimated_peak_bytes", "period_us"):
        assert estimate[key] == report[key]


@pytest.mark.parametrize("reason", ["frequency", "inverse", "time_count", "layer_count"])
def test_resource_rejection_precedes_scattering_and_coefficient_allocation(monkeypatch, reason):
    specimen = stack()
    if reason == "frequency":
        config = settings(gamma_order=4, fractional_bandwidth=1., absolute_tolerance=1e-12)
        time = (np.arange(801)/400).tolist()
    elif reason == "inverse":
        config = settings(center_frequency_mhz=10., fractional_bandwidth=1., absolute_tolerance=1e-12)
        time = np.linspace(0, 12., 2049).tolist()
    elif reason == "time_count":
        config, time = settings(), (np.arange(2050)/400).tolist()
    else:
        specimen = stack([layer(.001) for _ in range(257)])
        config, time = settings(), [0., .001]
    original = deepcopy((specimen, time, config))

    def allocation_forbidden(*_args, **_kwargs):
        raise AssertionError("Rejected resource request reached scattering allocation.")

    monkeypatch.setattr(time_kernel, "_scattering_stack", allocation_forbidden)
    with pytest.raises(ValueError):
        causal_gamma_response(specimen, time, config)
    assert (specimen, time, config) == original


@pytest.mark.parametrize("time", [[0., .002500001], [True, .001], [0., 12.00001]])
def test_time_sampling_bool_and_range_validation(time):
    with pytest.raises(ValueError):
        estimate_causal_gamma(stack(), time, settings())


def test_contradictory_record_metadata_is_rejected_without_replacing_actual_times():
    config = settings(record_start_us=11., record_duration_us=1., sample_rate_mhz=2400.)
    time = (np.arange(41)/400).tolist()
    original = deepcopy((time, config))
    with pytest.raises(ValueError, match="do not match their explicit recording metadata"):
        causal_gamma_response(stack([], 3., 8.), time, config)
    assert (time, config) == original


def test_matching_record_metadata_preserves_exact_python_generated_time_centers():
    config = settings(record_start_us=.013, record_duration_us=.1, sample_rate_mhz=400.)
    count = math.floor(config["record_duration_us"]*config["sample_rate_mhz"]+1e-9)+1
    time = [config["record_start_us"]+i/config["sample_rate_mhz"] for i in range(count)]
    result = causal_gamma_response(stack([], 3., 8.), time, config)
    expected = pressure_coefficients(3., 8.)[0]*gamma_oracle(time, config)
    assert result["time_us"] == time
    assert_certified(result, expected, config["absolute_tolerance"])


@pytest.mark.parametrize("field", ["record_start_us", "record_duration_us", "sample_rate_mhz"])
def test_boolean_record_metadata_is_rejected(field):
    config = settings(record_start_us=0., record_duration_us=.1, sample_rate_mhz=400.)
    config[field] = False if field == "record_start_us" else True
    with pytest.raises(ValueError, match="finite real number"):
        estimate_causal_gamma(stack(), (np.arange(41)/400).tolist(), config)


@pytest.mark.parametrize("missing", ["record_start_us", "record_duration_us", "sample_rate_mhz"])
def test_partial_record_metadata_is_rejected(missing):
    config = settings(record_start_us=0., record_duration_us=.1, sample_rate_mhz=400.)
    del config[missing]
    with pytest.raises(ValueError, match="requires start, duration and sample rate together"):
        estimate_causal_gamma(stack(), (np.arange(41)/400).tolist(), config)


def test_one_ulp_metadata_coordinate_mismatch_is_rejected():
    config = settings(record_start_us=.013, record_duration_us=.1, sample_rate_mhz=400.)
    time = [config["record_start_us"]+i/config["sample_rate_mhz"] for i in range(41)]
    time[17] = math.nextafter(time[17], math.inf)
    with pytest.raises(ValueError, match="do not match their explicit recording metadata"):
        estimate_causal_gamma(stack(), time, config)


def test_actual_hbm_column_is_admitted_whole_and_shared_windows_remain_consistent():
    from virtual_microscopy.layered_analysis import extract_layered_column

    example = Path(__file__).resolve().parents[1]/"examples"/"nvidia-h100-hbm6-microstructure.json"
    twin = json.loads(example.read_text(encoding="utf-8"))
    source = extract_layered_column({"twin": twin, "x_mm": 49.475, "y_mm": 39.95, "include_defects": True})
    specimen, config = source["stack"], settings()
    assert len(specimen["layers"]) == 26
    assert sum(p["thickness_mm"] for p in specimen["layers"]) == pytest.approx(2.65)
    before = deepcopy(specimen)
    time = (np.arange(1001)/400).tolist()
    full = causal_gamma_response(specimen, time, config)
    shortened = causal_gamma_response(specimen, time[40:961], config)
    a, b = full["diagnostics"], shortened["diagnostics"]
    assert a["layer_frequency_work_units"] == a["frequency_terms"]*27
    assert a["total_error_bound"] <= config["absolute_tolerance"]
    assert b["total_error_bound"] <= config["absolute_tolerance"]
    assert a["period_us"] != b["period_us"]
    assert np.max(np.abs(complex_output(full)[40:961]-complex_output(shortened))) <= a["total_error_bound"]+b["total_error_bound"]
    assert np.max(np.abs(np.asarray(full["envelope"])[40:961]-np.asarray(shortened["envelope"]))) <= a["total_error_bound"]+b["total_error_bound"]
    assert specimen == before
