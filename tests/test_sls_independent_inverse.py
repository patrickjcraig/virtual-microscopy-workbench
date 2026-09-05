"""Dispersive time checks using a separate transfer/inversion implementation.

The high-precision oracle is not an independently certified solution. Its degree
convergence and cross-method agreement exercise the production enclosure without
being used to define or tighten that enclosure.
"""
from copy import deepcopy

from mpmath import mp
import numpy as np
import pytest

from tools.sls_independent_oracle import inverse_transfer
from virtual_microscopy.sls_time import causal_gamma_response


def fixture(two_layers=False):
    layer = {'name': 'Manufactured material A', 'thickness_mm': .025,
             'density_kg_m3': 2000., 'relaxed_modulus_gpa': 32.,
             'unrelaxed_modulus_gpa': 72., 'relaxation_time_us': .006}
    second = {'name': 'Manufactured material B', 'thickness_mm': .015,
              'density_kg_m3': 1500., 'relaxed_modulus_gpa': 6.,
              'unrelaxed_modulus_gpa': 15., 'relaxation_time_us': .012}
    medium = lambda z: {'name': 'Assumed real exterior', 'impedance_mrayl': float(z), 'sound_speed_m_s': 2000.}
    return {'incident': medium(3), 'terminal': medium(5), 'layers': [layer, second] if two_layers else [layer]}


def settings(**changes):
    return {'center_frequency_mhz': 10., 'fractional_bandwidth': .8,
            'gamma_order': 12, 'surface_standoff_mm': .005,
            'absolute_tolerance': 1e-8, 'precision_bits': 192, **changes}


@pytest.mark.parametrize('two_layers', [False, True])
def test_dispersive_reflected_rf_agrees_with_independent_accelerated_transfer_inverse(two_layers):
    stack, options = fixture(two_layers), settings()
    original = deepcopy((stack, options))
    time = [i/200 for i in range(61)]
    indices = [16, 26, 42, 60]
    selected = [time[i] for i in indices]
    coarse = inverse_transfer(stack, options, selected, degree=48)
    fine = inverse_transfer(stack, options, selected, degree=72)
    observed = causal_gamma_response(stack, time, options)
    bound = observed['diagnostics']['total_error_bound']
    assert 0 < bound <= options['absolute_tolerance']
    assert (stack, options) == original
    assert observed['time_us'] == time
    # This uses the saved high-precision strings, not float-rounded references.
    with mp.workdps(60):
        for index, low, high in zip(indices, coarse['values'], fine['values']):
            reference = mp.mpc(high['real'], high['imaginary'])
            degree_change = abs(reference-mp.mpc(low['real'], low['imaginary']))
            assert degree_change < mp.mpf('1e-18')
            actual = mp.mpc(observed['rf'][index], observed['imaginary'][index])
            assert abs(actual-reference) <= mp.mpf(bound)
            assert abs(mp.mpf(observed['envelope'][index])-abs(reference)) <= mp.mpf(bound)
    assert coarse['transfer_evaluations'] <= len(selected)*(2*48+1)
    assert fine['transfer_evaluations'] <= len(selected)*(2*72+1)
    assert 'no oracle error certificate' in fine['scope']


def test_independent_inverse_real_and_quadrature_split_matches_analytic_gamma_interface():
    stack = fixture()
    stack['layers'] = []
    options = settings(surface_standoff_mm=0.)
    oracle = inverse_transfer(stack, options, [.1, .2], degree=48)
    with mp.workdps(60):
        order = options['gamma_order']
        frequency = mp.mpf(options['center_frequency_mhz'])
        rate = mp.pi*frequency*mp.mpf(options['fractional_bandwidth'])/mp.sqrt(mp.power(2, mp.mpf(2)/(order+1))-1)
        r = mp.mpf(5-3)/(5+3)
        for row in oracle['values']:
            time = mp.mpf(row['time_us'])
            exact = r*mp.power(rate*mp.e/order, order)*mp.power(time, order)*mp.exp(-rate*time)*mp.exp(mp.j*2*mp.pi*frequency*(time-order/rate))
            assert abs(mp.mpc(row['real'], row['imaginary'])-exact) < mp.mpf('1e-35')


def test_dispersive_slab_before_causal_standoff_is_bounded_without_relabeling_time_as_depth():
    time = [i/200 for i in range(61)]
    observed = causal_gamma_response(fixture(True), time, settings(surface_standoff_mm=.04))
    z = np.asarray(observed['rf'])+1j*np.asarray(observed['imaginary'])
    bound = observed['diagnostics']['total_error_bound']
    before = np.asarray(time) < .04
    assert np.max(np.abs(z[before])) <= bound
    assert np.max(np.asarray(observed['envelope'])[before]) <= bound
    assert np.max(np.abs(z[~before])) > .01


@pytest.mark.parametrize('degree,times', [(31,[.1]), (129,[.1]), (True,[.1]), (48,[]), (48,[0.]), (48,[.1]*33)])
def test_independent_oracle_work_is_bounded(degree, times):
    with pytest.raises(ValueError):
        inverse_transfer(fixture(), settings(), times, degree=degree)
