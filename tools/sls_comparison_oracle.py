"""Independent exact-arithmetic and inverse-transfer checks for saved SLS pairs.

Developer verification only: no production comparison, material or pulse imports.
Degree convergence of the inverse transfer is a diagnostic, not a certificate.
"""
from fractions import Fraction as Q
import math
import struct

from mpmath import mp

from tools.sls_independent_oracle import inverse_transfer


def same_double_axis(a, b):
    return len(a) == len(b) and all(struct.pack('<d', x) == struct.pack('<d', y) for x, y in zip(a, b))


def upward(exact):
    value = float(exact)
    if Q(value) < exact:
        value = math.nextafter(value, math.inf)
    if not math.isfinite(value):
        raise ValueError('Independent bound is not finite.')
    assert Q(value) >= exact
    if value > 0:
        assert Q(math.nextafter(value, -math.inf)) < exact
    return value


def check_residual_arithmetic(reference, candidate, residual, bounds):
    """Check every saved subtraction against exact rational arithmetic."""
    a, b = reference['causal_pulse'], candidate['causal_pulse']
    assert same_double_axis(a['time_us'], b['time_us'])
    assert same_double_axis(reference['spectrum']['frequency_mhz'], candidate['spectrum']['frequency_mhz'])
    u, eta = Q(1, 2**53), Q(1, 2**1074)
    rho = {key: u*(Q(max(map(abs, a[key])))+Q(max(map(abs, b[key]))))+eta
           for key in ('rf', 'imaginary', 'envelope')}
    source = upward(Q(a['diagnostics']['total_error_bound'])+Q(b['diagnostics']['total_error_bound']))
    complex_arithmetic = upward(rho['rf']+rho['imaginary'])
    magnitude_arithmetic = upward(rho['envelope'])
    expected = {'source_sum': source, 'complex_arithmetic': complex_arithmetic,
                'magnitude_arithmetic': magnitude_arithmetic,
                'complex_total': upward(Q(source)+Q(complex_arithmetic)),
                'magnitude_total': upward(Q(source)+Q(magnitude_arithmetic))}
    assert set(bounds) == set(expected)
    assert all(struct.pack('<d', bounds[key]) == struct.pack('<d', value) for key, value in expected.items())
    errors = {}
    for key in ('rf', 'imaginary', 'envelope'):
        assert len(residual[key]) == len(a[key]) == len(b[key])
        errors[key] = []
        for left, right, actual in zip(a[key], b[key], residual[key]):
            exact = Q(right)-Q(left)
            expected_value = float(exact)
            # Under nearest rounding, -0 minus +0 is the one exact-zero case
            # here with a negative sign; cancellation of nonzero values is +0.
            if exact == 0 and right == left == 0 and math.copysign(1, right) < 0 < math.copysign(1, left):
                expected_value = -0.0
            assert math.isfinite(actual) and struct.pack('<d', actual) == struct.pack('<d', expected_value)
            error = abs(Q(actual)-exact)
            assert error <= rho[key]
            errors[key].append(error)
    for real, imaginary, magnitude in zip(errors['rf'], errors['imaginary'], errors['envelope']):
        assert real*real+imaginary*imaginary <= Q(complex_arithmetic)**2
        assert magnitude <= Q(magnitude_arithmetic)
    return {'verified_subtractions': sum(map(len, errors.values())), 'bounds': expected,
            'maximum_component_rounding_error': {key: float(max(value)) for key, value in errors.items()}}


def check_selected_inverse(reference, candidate, residual, bounds, *, cache=None):
    """Independent ideal-model residual at four actual saved time centers."""
    cache = {} if cache is None else cache
    n = len(reference['causal_pulse']['time_us'])
    indices = sorted({round((n-1)*fraction) for fraction in (.4, .6, .8, 1.)})
    times = [reference['causal_pulse']['time_us'][index] for index in indices]
    for source in (reference, candidate):
        key = source['report_sha256']
        if key not in cache:
            assert source['causal_pulse']['time_us'] == reference['causal_pulse']['time_us']
            coarse = inverse_transfer(source['stack'], source['request']['causal_pulse'], times, degree=96)
            fine = inverse_transfer(source['stack'], source['request']['causal_pulse'], times, degree=128)
            with mp.workdps(60):
                changes = [abs(mp.mpc(x['real'], x['imaginary'])-mp.mpc(y['real'], y['imaginary']))
                           for x, y in zip(coarse['values'], fine['values'])]
                assert max(changes) < mp.mpf('1e-14')
                maximum = float(max(changes))
            cache[key] = {'coarse': coarse, 'fine': fine, 'maximum_degree_change': maximum}
    a, b = (cache[source['report_sha256']] for source in (reference, candidate))
    with mp.workdps(60):
        complex_errors, magnitude_errors = [], []
        for index, av, bv in zip(indices, a['fine']['values'], b['fine']['values']):
            left, right = mp.mpc(av['real'], av['imaginary']), mp.mpc(bv['real'], bv['imaginary'])
            complex_errors.append(abs(mp.mpc(residual['rf'][index], residual['imaginary'][index])-(right-left)))
            magnitude_errors.append(abs(mp.mpf(residual['envelope'][index])-(abs(right)-abs(left))))
        assert max(complex_errors) <= mp.mpf(bounds['complex_total'])
        assert max(magnitude_errors) <= mp.mpf(bounds['magnitude_total'])
        return {'selected_indices': indices, 'selected_times_us': times,
                'maximum_complex_difference': float(max(complex_errors)),
                'maximum_saved_magnitude_difference': float(max(magnitude_errors)),
                'maximum_degree_change': max(a['maximum_degree_change'], b['maximum_degree_change']),
                'scope': 'Independent inverse-transfer agreement and degree convergence, without a certified oracle remainder.'}
