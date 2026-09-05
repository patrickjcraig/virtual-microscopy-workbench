"""Independent SLS time-domain agreement check, never a numerical certificate.

This developer-only oracle uses mpmath's accelerated de Hoog inversion and a
pressure/velocity ODE matrix exponential. It imports no production material,
scattering, pulse, planner or inverse helpers. The real and quadrature transforms
are inverted separately because mpmath assumes a real-valued time function.
See https://mpmath.org/doc/current/calculus/inverselaplace.html.
"""
from math import isfinite

from mpmath import mp


def inverse_transfer(stack, settings, time_us, *, degree=64):
    """Return bounded-work high-precision comparisons at up to 32 positive times.

    Increasing degree changes both the inversion approximation and mpmath's
    internal arithmetic precision. Agreement between degrees is a convergence
    diagnostic, not an independently enclosed remainder. Moderate transfer
    fixtures only: matrix exponentials are unsuitable for extreme attenuation.
    """
    if type(degree) is not int or not 32 <= degree <= 128:
        raise ValueError("Independent inversion degree must be 32 to 128.")
    if not isinstance(time_us, (list, tuple)) or not 1 <= len(time_us) <= 32:
        raise ValueError("Independent oracle supports 1 to 32 selected times.")
    if any(isinstance(t, bool) or not isfinite(t) or not 0 < t <= 2 for t in time_us):
        raise ValueError("Independent oracle requires positive finite times <=2 us.")
    if not 0 <= len(stack['layers']) <= 8:
        raise ValueError("Independent oracle supports at most eight layers.")
    ctx = mp.clone()
    ctx.dps = 60
    counters = {'transfer_evaluations': 0, 'maximum_matrix_entry': ctx.mpf(0)}
    cache = {}
    pulse_parameters = {}

    def material_transfer(s):
        key = (ctx.prec, s._mpc_ if hasattr(s, '_mpc_') else s._mpf_)
        if key in cache:
            return cache[key]
        counters['transfer_evaluations'] += 1
        if counters['transfer_evaluations'] > 32 * (2 * degree + 1):
            raise ValueError("Independent transfer evaluation budget exceeded.")
        si_s = s * 1_000_000
        zi = ctx.mpf(stack['incident']['impedance_mrayl']) * 1_000_000
        zt = ctx.mpf(stack['terminal']['impedance_mrayl']) * 1_000_000
        q = ctx.eye(2)
        for layer in stack['layers']:
            if layer['thickness_mm'] == 0:
                continue
            density = ctx.mpf(layer['density_kg_m3'])
            relaxed = ctx.mpf(layer['relaxed_modulus_gpa']) * 1_000_000_000
            unrelaxed = ctx.mpf(layer['unrelaxed_modulus_gpa']) * 1_000_000_000
            tau = ctx.mpf(layer['relaxation_time_us']) / 1_000_000
            thickness = ctx.mpf(layer['thickness_mm']) / 1_000
            # Factored constitutive form, independently from the production law.
            modulus = (relaxed + unrelaxed*tau*si_s) / (1 + tau*si_s)
            # State [pressure, incident_impedance * particle_velocity] balances
            # units without introducing a propagation or impedance square root.
            a = ctx.matrix([[0, -density*si_s*thickness/zi],
                            [-zi*si_s*thickness/modulus, 0]])
            largest = max(abs(a[0, 1]), abs(a[1, 0]))
            counters['maximum_matrix_entry'] = max(counters['maximum_matrix_entry'], largest)
            if largest > 500:
                raise ValueError("Independent transfer fixture is too ill-conditioned; choose a smaller slab.")
            q = ctx.expm(a) * q
        w0, w1 = q[0, 0] - zt/zi*q[1, 0], q[0, 1] - zt/zi*q[1, 1]
        reflection = -(w0+w1)/(w0-w1)
        surface = 2000*ctx.mpf(settings['surface_standoff_mm'])/ctx.mpf(stack['incident']['sound_speed_m_s'])
        value = reflection * ctx.exp(-s*surface)
        cache[key] = value
        return value

    def transformed(s, quadrature):
        if ctx.prec not in pulse_parameters:
            order = settings['gamma_order']
            omega = 2*ctx.pi*ctx.mpf(settings['center_frequency_mhz'])
            rate = ctx.pi*ctx.mpf(settings['center_frequency_mhz'])*ctx.mpf(settings['fractional_bandwidth']) / ctx.sqrt(ctx.power(2, ctx.mpf(2)/(order+1))-1)
            numerator = ctx.power(rate*ctx.e/order, order)*ctx.factorial(order)
            pulse_parameters[ctx.prec] = order, omega, rate, numerator
        order, omega, rate, numerator = pulse_parameters[ctx.prec]
        plus = ctx.exp(-ctx.j*omega*order/rate)*numerator/(s+rate-ctx.j*omega)**(order+1)
        minus = ctx.exp(ctx.j*omega*order/rate)*numerator/(s+rate+ctx.j*omega)**(order+1)
        pulse = (plus-minus)/(2*ctx.j) if quadrature else (plus+minus)/2
        return material_transfer(s)*pulse

    values = []
    for actual in time_us:
        time = ctx.mpf(actual)
        real = ctx.invertlaplace(lambda s: transformed(s, False), time, method='dehoog', degree=degree)
        imaginary = ctx.invertlaplace(lambda s: transformed(s, True), time, method='dehoog', degree=degree)
        values.append({'time_us': actual, 'real': ctx.nstr(real, 55), 'imaginary': ctx.nstr(imaginary, 55)})
    return {'method': 'mpmath-dehoog-independent-pressure-velocity-transfer',
            'degree': degree, 'values': values,
            'transfer_evaluations': counters['transfer_evaluations'],
            'maximum_matrix_entry': float(counters['maximum_matrix_entry']),
            'scope': 'Independent numerical agreement and degree convergence only; no oracle error certificate.'}
