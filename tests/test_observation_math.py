"""Independent exact oracles for finite coherent spatial observation."""
from fractions import Fraction
import math
import struct
import sys

import numpy as np
import pytest

import virtual_microscopy.observation_math as om


def f(value):
    return Fraction.from_float(float(value))


def bits(value):
    return struct.unpack('<Q', struct.pack('<d', float(value)))[0]


def inputs(nx=16, nt=2, real=0., imaginary=0., bound=0.):
    return (np.full((3, nx, nt), real, dtype=np.float64),
            np.full((3, nx, nt), imaginary, dtype=np.float64),
            np.full((3, nx), bound, dtype=np.float64))


def clone(result):
    return {key: value.copy() for key, value in result.items()}


def oracle_up(q):
    value = float(q)
    if f(value) < q:
        value = math.nextafter(value, math.inf)
    assert f(value) >= q
    assert not value or f(math.nextafter(value, -math.inf)) < q
    return value


def weighted(values, x, t=None):
    # Independent Fraction expression; no production constants or integer helper.
    weights = [[Fraction(1, 16), Fraction(1, 8), Fraction(1, 16)],
               [Fraction(1, 8), Fraction(1, 4), Fraction(1, 8)],
               [Fraction(1, 16), Fraction(1, 8), Fraction(1, 16)]]
    return sum((weights[y][dx] * f(values[y, x+dx] if t is None else values[y, x+dx, t])
                for y in range(3) for dx in range(3)), Fraction(0))


def independent_nearest(q, value):
    if q == 0:
        assert bits(value) == 0
        return
    assert math.copysign(1., value) == (1 if q > 0 else -1)
    a, exact = abs(value), abs(q)
    center = f(a)
    lo = (f(math.nextafter(a, -math.inf))+center)/2 if a else Fraction(0)
    next_value = math.nextafter(a, math.inf)
    hi = ((f(next_value)+center)/2 if math.isfinite(next_value)
          else center+f(math.ulp(a))/2)
    assert lo <= exact <= hi
    if exact in (lo, hi):
        assert bits(a) & 1 == 0


def independent_radius(real, imag, magnitude):
    square, center = f(real)**2+f(imag)**2, f(magnitude)
    radius = f(math.ulp(float(magnitude)))
    for _ in range(9):
        if max(Fraction(0), center-radius)**2 <= square <= (center+radius)**2:
            return radius
        radius *= 2
    raise AssertionError('Independent magnitude bracket failed')


def check_oracle(rf, imag, bounds, result):
    for x in range(rf.shape[1]-2):
        complex_max, magnitude_max = Fraction(0), Fraction(0)
        for t in range(rf.shape[2]):
            qr, qi = weighted(rf, x, t), weighted(imag, x, t)
            r, i, h = (result[key][x, t] for key in om.SIGNALS)
            independent_nearest(qr, r)
            independent_nearest(qi, i)
            assert bits(r) == bits(float(qr))
            assert bits(i) == bits(float(qi))
            complex_max = max(complex_max, abs(f(r)-qr)+abs(f(i)-qi))
            magnitude_max = max(magnitude_max, independent_radius(r, i, h))
        s, c, d = oracle_up(weighted(bounds, x)), oracle_up(complex_max), oracle_up(magnitude_max)
        total = oracle_up(f(s)+f(c))
        expected = (s, c, total, d, oracle_up(f(total)+f(d)))
        assert tuple(float(result[key][x]) for key in om.BOUNDS) == expected


@pytest.mark.parametrize('value', [Fraction(0), Fraction(1, 1 << 1075), Fraction(1, 1 << 1074),
    Fraction(3, 1 << 1075), Fraction(1, 1 << 2000), Fraction(1, 1 << 1022),
    Fraction(1, 1 << 1022)-Fraction(1, 1 << 1075),
    Fraction(1, 1 << 1022)+Fraction(1, 1 << 1075), Fraction(1),
    Fraction(1)+Fraction(1, 1 << 1074), Fraction(2)-Fraction(1, 1 << 1074),
    Fraction(3, 7), f(sys.float_info.max), f(sys.float_info.max)-Fraction(1)])
def test_integer_upward_conversion_is_exact_minimal_finite(value):
    assert bits(om.upward_nonnegative(value)) == bits(oracle_up(value))


@pytest.mark.parametrize('value', [-Fraction(1), 1., 0, None, f(sys.float_info.max)+1])
def test_upward_conversion_rejects_invalid_or_unrepresentable(value):
    with pytest.raises(ValueError):
        om.upward_nonnegative(value)


def test_operator_weights_phase_and_metadata_are_exact_and_copies():
    metadata = om.operator_metadata()
    weights = struct.unpack('<9d', bytes.fromhex(metadata['weights_float64_le_hex']))
    assert tuple(f(v) for v in weights) == tuple(Fraction(n, 16) for n in [1, 2, 1, 2, 4, 2, 1, 2, 1])
    assert sum(map(f, weights)) == 1
    assert metadata['additional_phase_radians'] == 0
    assert metadata['offsets_yx'] == [[y, x] for y in (-1, 0, 1) for x in (-1, 0, 1)]
    metadata['weight_numerators'][0] = 100
    assert om.operator_metadata()['weight_numerators'][0] == 1


def test_constant_complex_field_preserves_pressure_and_propagates_source_bounds():
    a, b, eps = inputs(real=3., imaginary=4., bound=2e-8)
    result = om.observe_row(a, b, eps, absolute_tolerance=1e-7)
    assert set(result) == set(om.SIGNALS+om.BOUNDS)
    assert np.all(result['rf'] == 3) and np.all(result['imaginary'] == 4)
    assert np.all(result['envelope'] == 5)
    assert np.all(result['complex_arithmetic'] == 0)
    assert all(v.dtype == np.float64 for v in result.values())
    assert om.verify_row(a, b, eps, result, absolute_tolerance=1e-7) is None
    assert om.verify_saved_row(result, eps, absolute_tolerance=1e-7) is None
    check_oracle(a, b, eps, result)


def test_complex_phase_cancellation_recomputes_magnitude_after_mixing():
    a, b, eps = inputs(nt=3)
    a[:] = np.where(np.arange(16) % 2, -1., 1.)[None, :, None]
    b[:] = 2*a
    result = om.observe_row(a, b, eps, absolute_tolerance=1e-7)
    assert np.all(np.hypot(a, b) > 0)
    for key in om.SIGNALS:
        assert not np.any(result[key])
    assert np.all(result['magnitude_arithmetic'] == math.ulp(0))
    assert not np.any(result['complex_arithmetic'])
    check_oracle(a, b, eps, result)


@pytest.mark.parametrize('source_row,source_col', [(0, 0), (1, 8), (2, 15)])
def test_asymmetric_spatial_impulse_has_full_support_and_no_opposite_edge_leak(source_row, source_col):
    a, b, eps = inputs(nt=3)
    a[source_row, source_col, 1] = 16
    b[source_row, source_col, 1] = -32
    result = om.observe_row(a, b, eps, absolute_tolerance=1e-7)
    expected = np.zeros((14, 3))
    matrix = [[1, 2, 1], [2, 4, 2], [1, 2, 1]]
    for x in range(14):
        dx = source_col-x
        if 0 <= dx <= 2:
            expected[x, 1] = matrix[source_row][dx]
    assert np.array_equal(result['rf'], expected)
    assert np.array_equal(result['imaginary'], -2*expected)
    assert np.all(result['rf'][:, (0, 2)] == 0)
    check_oracle(a, b, eps, result)


def test_independent_random_fraction_oracle_and_unequal_source_error_propagation():
    rng = np.random.default_rng(152015)
    a = rng.normal(size=(3, 17, 7))
    b = rng.normal(size=a.shape)
    eps = rng.uniform(0, 3e-8, size=(3, 17))
    result = om.observe_row(a, b, eps, absolute_tolerance=1e-7)
    check_oracle(a, b, eps, result)
    om.verify_row(a, b, eps, result, absolute_tolerance=1e-7)
    om.verify_saved_row(result, eps, absolute_tolerance=1e-7)


def test_extreme_and_subnormal_components_have_exact_error_and_rounding_oracles():
    rng = np.random.default_rng(161516)
    mantissa = rng.integers(0, 1 << 52, size=(3, 16, 5), dtype=np.uint64)
    exponent = rng.integers(0, 2018, size=(3, 16, 5), dtype=np.uint64)
    sign = rng.integers(0, 2, size=(3, 16, 5), dtype=np.uint64)
    a = (mantissa | (exponent << np.uint64(52)) | (sign << np.uint64(63))).view(np.float64)
    b = np.flip(a, axis=2).copy()
    eps = np.zeros((3, 16))
    a[:, :, 0] = 0
    b[:, :, 0] = 0
    a[1, 7, 0] = 2*math.ulp(0)
    b[1, 7, 0] = -2*math.ulp(0)
    a[1, 11, 0] = 6*math.ulp(0)
    b[1, 11, 0] = -6*math.ulp(0)
    result = om.observe_row(a, b, eps, absolute_tolerance=sys.float_info.max)
    assert bits(result['rf'][6, 0]) == 0
    assert bits(result['imaginary'][6, 0]) == 1 << 63
    assert bits(result['rf'][10, 0]) == 2
    assert bits(result['imaginary'][10, 0]) == (1 << 63)+2
    check_oracle(a, b, eps, result)
    om.verify_row(a, b, eps, result, absolute_tolerance=sys.float_info.max)


def test_near_maximum_finite_components_do_not_overflow_integer_accumulation():
    a, b, eps = inputs(real=sys.float_info.max, imaginary=0.)
    result = om.observe_row(a, b, eps, absolute_tolerance=sys.float_info.max)
    assert np.all(result['rf'] == sys.float_info.max)
    assert not np.any(result['complex_arithmetic'])
    check_oracle(a, b, eps, result)
    om.verify_row(a, b, eps, result, absolute_tolerance=sys.float_info.max)


def test_unrepresentable_magnitude_rejects_even_when_components_are_finite():
    a, b, eps = inputs(real=sys.float_info.max, imaginary=sys.float_info.max)
    with pytest.raises(ValueError, match='magnitude'):
        om.observe_row(a, b, eps, absolute_tolerance=sys.float_info.max)


def test_record_maxima_include_final_partial_time_block_and_blocking_is_invariant(monkeypatch):
    a, b, eps = inputs(nt=130)
    a[0, 7, -1] = 1.
    a[0, 8, -1] = math.ulp(1.)
    b[0, 7, -1] = -1.
    b[0, 8, -1] = -math.ulp(1.)
    first = om.observe_row(a, b, eps, absolute_tolerance=1e-7)
    assert first['complex_arithmetic'].max() > 0
    assert first['magnitude_arithmetic'].max() > math.ulp(0)
    monkeypatch.setattr(om, 'TIME_BLOCK', 17)
    second = om.observe_row(a, b, eps, absolute_tolerance=1e-7)
    for key in first:
        assert first[key].tobytes() == second[key].tobytes()
    check_oracle(a, b, eps, first)


@pytest.mark.parametrize('name', om.BOUNDS)
def test_underreported_bound_component_or_total_rejects(name):
    rng = np.random.default_rng(128)
    a, b = rng.normal(size=(3, 16, 3)), rng.normal(size=(3, 16, 3))
    eps = rng.uniform(1e-9, 2e-8, size=(3, 16))
    result = om.observe_row(a, b, eps, absolute_tolerance=1e-7)
    assert result[name][0] > 0
    result[name][0] = math.nextafter(result[name][0], -math.inf)
    with pytest.raises(ValueError, match='under-reported|published'):
        om.verify_row(a, b, eps, result, absolute_tolerance=1e-7)
    if name != 'complex_arithmetic':
        with pytest.raises(ValueError, match='under-reported|published'):
            om.verify_saved_row(result, eps, absolute_tolerance=1e-7)


def test_totals_must_cover_exact_published_components_even_when_float_sum_hides_them():
    a, b, eps = inputs(real=1., bound=1.)
    result = om.observe_row(a, b, eps, absolute_tolerance=2.)
    result['complex_arithmetic'][:] = math.ulp(0)
    result['complex_total'][:] = 1.
    assert result['source_propagation'][0]+result['complex_arithmetic'][0] == 1.
    with pytest.raises(ValueError, match='exact published'):
        om.verify_saved_row(result, eps, absolute_tolerance=2.)


def test_conservative_finite_bounds_are_accepted_but_ceiling_is_enforced():
    a, b, eps = inputs(real=1., imaginary=.5, bound=1e-9)
    result = om.observe_row(a, b, eps, absolute_tolerance=1e-7)
    for key, value in zip(om.BOUNDS, (2e-9, 1e-10, 3e-9, 1e-10, 4e-9)):
        result[key][:] = value
    om.verify_row(a, b, eps, result, absolute_tolerance=1e-7)
    om.verify_saved_row(result, eps, absolute_tolerance=1e-7)
    with pytest.raises(ValueError, match='tolerance'):
        om.verify_saved_row(result, eps, absolute_tolerance=2e-9)


def test_offline_check_explicitly_cannot_recompute_conversion_error():
    rng = np.random.default_rng(33)
    a, b = rng.normal(size=(3, 16, 2)), rng.normal(size=(3, 16, 2))
    eps = np.zeros((3, 16))
    result = om.observe_row(a, b, eps, absolute_tolerance=1e-7)
    assert result['complex_arithmetic'].max() > 0
    result['complex_arithmetic'][:] = 0
    result['complex_total'][:] = 0
    result['magnitude_total'][:] = result['magnitude_arithmetic']
    om.verify_saved_row(result, eps, absolute_tolerance=1e-7)
    with pytest.raises(ValueError, match='complex arithmetic'):
        om.verify_row(a, b, eps, result, absolute_tolerance=1e-7)
    assert 'require source' in om.OFFLINE_VERIFICATION_LIMIT


def test_verifier_rejects_wrong_pressure_even_with_generous_conservative_maps():
    a, b, eps = inputs(real=1.)
    result = om.observe_row(a, b, eps, absolute_tolerance=1e-7)
    result['rf'][0, 0] = math.nextafter(1., math.inf)
    result['complex_arithmetic'][:] = 1e-9
    result['complex_total'][:] = 1e-8
    result['magnitude_total'][:] = 2e-8
    with pytest.raises(ValueError, match='nearest-even'):
        om.verify_row(a, b, eps, result, absolute_tolerance=1e-7)


def test_verifier_rejects_wrong_zero_sign_independently():
    a, b, eps = inputs()
    result = om.observe_row(a, b, eps, absolute_tolerance=1e-7)
    result['rf'][0, 0] = -0.
    with pytest.raises(ValueError, match='positive zero'):
        om.verify_row(a, b, eps, result, absolute_tolerance=1e-7)


def test_saved_magnitude_is_bracket_verified_not_resynthesized(monkeypatch):
    a, b, eps = inputs(real=3., imaginary=4.)
    result = om.observe_row(a, b, eps, absolute_tolerance=1e-7)

    def forbidden(*args, **kwargs):
        raise AssertionError('No producer or hypot resynthesis during verification')

    monkeypatch.setattr(om.math, 'hypot', forbidden)
    monkeypatch.setattr(om, 'observe_row', forbidden)
    om.verify_row(a, b, eps, result, absolute_tolerance=1e-7)
    om.verify_saved_row(result, eps, absolute_tolerance=1e-7)
    result['envelope'][0, 0] = 5.1
    with pytest.raises(ValueError, match='enclosure'):
        om.verify_saved_row(result, eps, absolute_tolerance=1e-7)


def test_magnitude_enclosure_search_expands_but_has_a_fixed_bound(monkeypatch):
    original = om.math.hypot

    def displaced(r, i):
        return original(r, i)+4*math.ulp(original(r, i))

    a, b, eps = inputs(real=3., imaginary=4.)
    monkeypatch.setattr(om.math, 'hypot', displaced)
    result = om.observe_row(a, b, eps, absolute_tolerance=1e-7)
    assert np.all(result['magnitude_arithmetic'] == 4*math.ulp(5.))
    om.verify_row(a, b, eps, result, absolute_tolerance=1e-7)
    om.verify_saved_row(result, eps, absolute_tolerance=1e-7)
    monkeypatch.setattr(om.math, 'hypot', lambda r, i: 5.+512*math.ulp(5.))
    with pytest.raises(ValueError, match='enclosure'):
        om.observe_row(a, b, eps, absolute_tolerance=1e-7)


@pytest.mark.parametrize('mode', ['produce', 'verify'])
def test_cancellation_stops_after_no_more_than_one_temporal_block(monkeypatch, mode):
    a, b, eps = inputs(nt=130, real=1.)
    result = om.observe_row(a, b, eps, absolute_tolerance=1e-7)
    count = 0
    name = '_rounded_component' if mode == 'produce' else '_reference_units'
    original = getattr(om, name)

    def counted(*args):
        nonlocal count
        count += 1
        return original(*args)

    monkeypatch.setattr(om, name, counted)
    limit = 2*14*64
    kwargs = {'absolute_tolerance': 1e-7, 'cancelled': lambda: count >= limit}
    with pytest.raises(om.ObservationCancelled):
        if mode == 'produce':
            om.observe_row(a, b, eps, **kwargs)
        else:
            om.verify_row(a, b, eps, result, **kwargs)
    assert count == limit


def test_initial_cancellation_does_not_enter_weighted_arithmetic(monkeypatch):
    a, b, eps = inputs()
    monkeypatch.setattr(om, '_weighted_units', lambda *a: pytest.fail('Arithmetic before cancellation'))
    with pytest.raises(om.ObservationCancelled):
        om.observe_row(a, b, eps, absolute_tolerance=1e-7, cancelled=lambda: True)


def test_strided_readonly_inputs_outputs_are_preserved_and_geometry_is_not_an_input():
    rng = np.random.default_rng(24)
    storage = rng.normal(size=(3, 32, 6))
    a, b = storage[:, ::2, ::2], -storage[:, ::2, ::2]
    eps = np.zeros((3, 16))
    for value in (a, b, eps):
        value.setflags(write=False)
    before = [v.tobytes() for v in (a, b, eps)]
    result = om.observe_row(a, b, eps, absolute_tolerance=1e-7)
    saved = {key: value.tobytes() for key, value in result.items()}
    for value in result.values():
        value.setflags(write=False)
    om.verify_row(a, b, eps, result, absolute_tolerance=1e-7)
    om.verify_saved_row(result, eps, absolute_tolerance=1e-7)
    assert before == [v.tobytes() for v in (a, b, eps)]
    assert saved == {key: value.tobytes() for key, value in result.items()}
    with pytest.raises(TypeError):
        om.observe_row(a, b, eps, absolute_tolerance=1e-7, pixel_pitch_mm=.00390625)


@pytest.mark.parametrize('nx,nt', [(15, 2), (65, 2), (16, 1), (16, 2050)])
def test_dimension_resource_rejection_precedes_sample_arithmetic(monkeypatch, nx, nt):
    a, b, eps = inputs(nx=nx, nt=nt)
    monkeypatch.setattr(om, '_weighted_units', lambda *a: pytest.fail('Arithmetic before shape rejection'))
    with pytest.raises(ValueError, match='shape'):
        om.observe_row(a, b, eps, absolute_tolerance=1e-7)


@pytest.mark.parametrize('value', [None, True, False, -1., 0., math.nan, math.inf, -math.inf, '1e-7',
                                  pytest.param(10**1000, id='unrepresentable_integer')])
def test_invalid_tolerance_rejects(value):
    a, b, eps = inputs()
    with pytest.raises(ValueError, match='tolerance'):
        om.observe_row(a, b, eps, absolute_tolerance=value)


@pytest.mark.parametrize('target', ['rf', 'imaginary', 'source_bounds'])
@pytest.mark.parametrize('bad', [math.nan, math.inf, -math.inf])
def test_nonfinite_source_values_reject(target, bad):
    a, b, eps = inputs()
    data = {'rf': a, 'imaginary': b, 'source_bounds': eps}
    data[target].flat[0] = bad
    with pytest.raises(ValueError, match='nonfinite'):
        om.observe_row(a, b, eps, absolute_tolerance=1e-7)


@pytest.mark.parametrize('dtype', [np.float32, '>f8', np.int64])
def test_source_dtype_is_strict_native_float64(dtype):
    a, b, eps = inputs()
    with pytest.raises(ValueError, match='native float64'):
        om.observe_row(a.astype(dtype), b, eps, absolute_tolerance=1e-7)


def test_shape_mismatch_negative_bounds_and_early_source_tolerance_rejection(monkeypatch):
    a, b, eps = inputs()
    with pytest.raises(ValueError, match='shape'):
        om.observe_row(a, b[:, :, :1], eps, absolute_tolerance=1e-7)
    eps[0, 0] = -math.ulp(0)
    with pytest.raises(ValueError, match='nonnegative'):
        om.observe_row(a, b, eps, absolute_tolerance=1e-7)
    eps[:] = 2e-7
    monkeypatch.setattr(om, '_rounded_component', lambda *a: pytest.fail('Known insufficient tolerance reached synthesis'))
    with pytest.raises(ValueError, match='already exceeds'):
        om.observe_row(a, b, eps, absolute_tolerance=1e-7)


def test_exact_bound_sum_above_maximum_float_rejects_instead_of_rounding_down():
    a, b, eps = inputs(real=1., bound=sys.float_info.max)
    with pytest.raises(ValueError, match='representable finitely'):
        om.observe_row(a, b, eps, absolute_tolerance=sys.float_info.max)


@pytest.mark.parametrize('key', om.SIGNALS+om.BOUNDS)
def test_every_nonfinite_returned_product_rejects_before_publication(key):
    a, b, eps = inputs(real=1.)
    result = om.observe_row(a, b, eps, absolute_tolerance=1e-7)
    result[key].flat[-1] = math.nan
    with pytest.raises(ValueError, match='nonfinite'):
        om.verify_row(a, b, eps, result, absolute_tolerance=1e-7)
    with pytest.raises(ValueError, match='nonfinite'):
        om.verify_saved_row(result, eps, absolute_tolerance=1e-7)


def test_result_registry_shape_dtype_and_nonnegative_requirements():
    a, b, eps = inputs(real=1.)
    result = om.observe_row(a, b, eps, absolute_tolerance=1e-7)
    for mutate in (lambda r: r.pop('complex_total'),
                   lambda r: r.update(extra=np.zeros(14)),
                   lambda r: r.update(rf=r['rf'][:, :1]),
                   lambda r: r.update(imaginary=r['imaginary'].astype(np.float32)),
                   lambda r: r.update(envelope=-r['envelope']),
                   lambda r: r.update(complex_arithmetic=-np.ones(14)),
                   lambda r: r.update(source_propagation=np.zeros((1, 14)))):
        broken = clone(result)
        mutate(broken)
        with pytest.raises(ValueError):
            om.verify_saved_row(broken, eps, absolute_tolerance=1e-7)


def test_inclusive_maximum_dimensions_are_admitted_without_a_whole_volume():
    a, b, eps = inputs(nx=64, nt=2049)
    result = om.observe_row(a, b, eps, absolute_tolerance=1e-7)
    assert result['rf'].shape == (62, 2049)
    assert sum(value.nbytes for value in result.values()) == om.MAX_OUTPUT_ROW_BYTES
    om.verify_saved_row(result, eps, absolute_tolerance=1e-7)
