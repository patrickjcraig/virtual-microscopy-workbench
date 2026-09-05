"""Independent exact arithmetic and ordinary diagnostic comparison fixtures."""
from fractions import Fraction
import json
import math

import numpy as np
import pytest

import virtual_microscopy.causal_comparison_math as cm


def signals(rf, imaginary=None, envelope=None):
    real = np.asarray(rf, dtype=np.float64)
    if real.ndim == 1:
        real = real[None, :]
    imag = np.zeros_like(real) if imaginary is None else np.asarray(imaginary, dtype=np.float64).reshape(real.shape)
    env = np.hypot(real, imag) if envelope is None else np.asarray(envelope, dtype=np.float64).reshape(real.shape)
    return {"rf": real.copy(), "imaginary": imag.copy(), "envelope": env.copy()}


def coordinates(nx=1, ny=1, nt=3):
    return {"x_mm": np.arange(nx, dtype=np.float64)+.125,
            "y_mm": np.arange(ny, dtype=np.float64)+.375,
            "time_us": np.arange(nt, dtype=np.float64)/8+.25}


def f(value):
    return Fraction(float(value))


@pytest.mark.parametrize("value", [Fraction(0), cm.MIN_SUBNORMAL/2, cm.MIN_SUBNORMAL,
    cm.MIN_SUBNORMAL*Fraction(3, 2), cm.MIN_SUBNORMAL/(1 << 100), Fraction(1),
    Fraction(1)+Fraction(1, 1 << 53), f(np.finfo(np.float64).max)])
def test_outward_conversion_has_exact_readback_and_smallest_upper_neighbor(value):
    result = cm.upward_nonnegative(value)
    assert math.isfinite(result) and f(result) >= value
    if result > 0:
        assert f(math.nextafter(result, -math.inf)) < value
    assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize("value", [-Fraction(1), 1., f(np.finfo(np.float64).max)+1,
                                  2*f(np.finfo(np.float64).max)])
def test_outward_conversion_rejects_negative_inexact_type_or_unrepresentable(value):
    with pytest.raises(ValueError):
        cm.upward_nonnegative(value)


def test_actual_numpy_binary64_path_admission_preserves_floating_error_policy():
    before = np.geterr()
    result = cm.check_arithmetic_environment()
    assert result["contract"] == "numpy-binary64-nearest-gradual-v1"
    assert result["probe_cases"] >= 13 and result["environment_changed"] is False
    assert np.geterr() == before


def test_arithmetic_admission_executes_contiguous_and_genuinely_strided_operands(monkeypatch):
    subtract = cm._subtract
    observed = []

    def inspect(a, b):
        observed.append((a.strides, b.strides, a.flags.c_contiguous, b.flags.c_contiguous,
                         set(zip(a.view(np.uint64).tolist(), b.view(np.uint64).tolist()))))
        return subtract(a, b)

    monkeypatch.setattr(cm, "_subtract", inspect)
    cm.check_arithmetic_environment()
    assert len(observed) == 2
    contiguous, strided = observed
    assert contiguous[:4] == ((8,), (8,), True, True)
    assert strided[:4] == ((48,), (48,), False, False)
    # Skipping alternate rows across the repeated odd-length probe sequence
    # must still exercise every operand pair, not just one parity of cases.
    assert strided[4] == contiguous[4] and len(strided[4]) == 13


@pytest.mark.parametrize("mode", ["directed", "double_round", "flush_result", "flush_input", "ties_away"])
def test_arithmetic_probes_reject_simulated_unsupported_subtraction_without_changing_fenv(monkeypatch, mode):
    actual = cm._subtract

    def adverse(a, b):
        result = actual(a, b)
        ab, bb = a.view(np.uint64), b.view(np.uint64)
        if mode == "directed":
            result[(ab == 0xBC90000000000000) & (bb == 0x3FF0000000000000)] = math.nextafter(1., math.inf)
        elif mode == "double_round":
            result[(ab == 0xBCA0000000000001) & (bb == 0x3FF0000000000000)] = 1.
        elif mode == "flush_result":
            result[(result != 0) & (np.abs(result) < np.finfo(np.float64).tiny)] = 0.
        elif mode == "flush_input":
            result[ab == 0x000FFFFFFFFFFFFF] = b[ab == 0x000FFFFFFFFFFFFF]
        else:
            result[(ab == 0xBCA0000000000000) & (bb == 0x3FF0000000000000)] = math.nextafter(1., math.inf)
        return result

    monkeypatch.setattr(cm, "_subtract", adverse)
    with pytest.raises(ValueError, match="Unsupported floating arithmetic"):
        cm.check_arithmetic_environment()


def test_unsupported_platform_is_rejected(monkeypatch):
    monkeypatch.setattr(cm.platform, "machine", lambda: "unverified-cpu")
    with pytest.raises(ValueError, match="x86-64"):
        cm.check_arithmetic_environment()


def test_changed_arithmetic_is_rejected_at_exit_without_publishing_result(monkeypatch):
    checks = []

    def check():
        checks.append(True)
        if len(checks) == 2:
            raise ValueError("simulated changed floating environment")

    monkeypatch.setattr(cm, "check_arithmetic_environment", check)
    with pytest.raises(ValueError, match="changed floating"):
        cm.checked_differences(signals([1.]), signals([2.]))
    assert len(checks) == 2


def test_signed_zero_and_halfulp_subtraction_preserves_source_bits():
    a = signals([0., -0., -2**-53, -float.fromhex("0x1.0000000000001p-53")], envelope=[0., 0., 0., 0.])
    b = signals([-0., 0., 1., 1.], envelope=[0., 0., 0., 0.])
    snapshots = [{key: value.tobytes() for key, value in source.items()} for source in (a, b)]
    difference = cm.checked_differences(a, b)
    np.testing.assert_array_equal(difference["rf"].view(np.uint64),
        np.array([[0x8000000000000000, 0, 0x3FF0000000000000, 0x3FF0000000000001]], np.uint64))
    assert snapshots == [{key: value.tobytes() for key, value in source.items()} for source in (a, b)]


def test_exact_component_and_ideal_complex_enclosures_on_random_binary64_patterns():
    rng = np.random.default_rng(14014)
    raw_a = rng.integers(0, np.iinfo(np.uint64).max, 2049, dtype=np.uint64).view(np.float64)
    raw_b = rng.integers(0, np.iinfo(np.uint64).max, 2049, dtype=np.uint64).view(np.float64)
    with np.errstate(over="ignore", invalid="ignore"):
        valid = np.isfinite(raw_a) & np.isfinite(raw_b) & np.isfinite(raw_b-raw_a)
    raw_a, raw_b = raw_a[valid], raw_b[valid]
    a = signals(raw_a, raw_a[::-1], abs(raw_a))
    b = signals(raw_b, raw_b[::-1], abs(raw_b))
    d = cm.checked_differences(a, b)
    eps_a, eps_b = np.array([1e-7]), np.array([3e-8])
    bounds = cm.column_bounds(a, b, eps_a, eps_b)
    source = f(eps_a[0])+f(eps_b[0])
    assert f(bounds["source_sum"][0]) >= source
    assert f(bounds["complex_total"][0]) >= f(bounds["source_sum"][0])+f(bounds["complex_arithmetic"][0])
    assert f(bounds["envelope_total"][0]) >= f(bounds["source_sum"][0])+f(bounds["envelope_arithmetic"][0])
    for t in range(len(raw_a)):
        er = f(d["rf"][0, t])-(f(b["rf"][0, t])-f(a["rf"][0, t]))
        ei = f(d["imaginary"][0, t])-(f(b["imaginary"][0, t])-f(a["imaginary"][0, t]))
        assert er*er+ei*ei <= f(bounds["complex_arithmetic"][0])**2
        # The two ideal source perturbations oppose each other along an exact
        # rational unit direction, exercising a correlated worst direction.
        ir, ii = er-source*Fraction(3, 5), ei-source*Fraction(4, 5)
        assert ir*ir+ii*ii <= f(bounds["complex_total"][0])**2
        ee = f(d["envelope"][0, t])-(f(b["envelope"][0, t])-f(a["envelope"][0, t]))
        assert abs(ee-source) <= f(bounds["envelope_total"][0])


def test_full_record_column_maxima_and_outward_published_component_composition():
    a = signals([[1., 0., 10.], [0., 1e-300, 0.]], [[2., 0., -20.], [0., 0., 0.]], [[3., 0., 30.], [0., 0., 0.]])
    b = signals([[0., 0., -11.], [0., -1e-300, 0.]], [[0., 0., 21.], [0., 0., 0.]], [[0., 0., 31.], [0., 0., 0.]])
    eps_a, eps_b = np.array([1., 0.]), np.array([2**-53, 0.])
    bounds = cm.column_bounds(a, b, eps_a, eps_b)
    assert bounds["source_sum"][0] == math.nextafter(1., math.inf)
    for x in range(2):
        rho = {}
        for name in cm.SIGNALS:
            ma = max(f(abs(v)) for v in a[name][x])
            mb = max(f(abs(v)) for v in b[name][x])
            rho[name] = (ma+mb)/2**53+Fraction(1, 2**1074)
        assert f(bounds["complex_arithmetic"][x]) >= rho["rf"]+rho["imaginary"]
        assert f(bounds["envelope_arithmetic"][x]) >= rho["envelope"]
        for prefix in ("complex", "envelope"):
            assert f(bounds[prefix+"_total"][x]) >= f(bounds["source_sum"][x])+f(bounds[prefix+"_arithmetic"][x])
    assert all(np.asarray(json.loads(json.dumps(v.tolist())), np.float64).tobytes() == v.tobytes() for v in bounds.values())


def test_large_rational_intermediate_is_admissible_but_residual_or_bound_overflow_rejects():
    maximum = np.finfo(np.float64).max
    a = signals([maximum], envelope=[maximum])
    zero = np.zeros(1)
    bounds = cm.column_bounds(a, a, zero, zero)
    assert np.isfinite(bounds["complex_total"]).all()
    assert 2*f(maximum) > f(maximum)
    b = signals([-maximum], envelope=[maximum])
    with pytest.raises(ValueError, match="overflow"):
        cm.checked_differences(a, b)
    with pytest.raises(ValueError, match="overflow"):
        cm.column_bounds(a, b, zero, zero)
    with pytest.raises(ValueError, match="finitely"):
        cm.column_bounds(a, a, np.array([maximum]), np.array([maximum]))


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_nonfinite_sources_or_certificates_are_rejected(bad):
    a, b = signals([1., 2.]), signals([3., 4.])
    b["imaginary"][0, 1] = bad
    with pytest.raises(ValueError, match="finite"):
        cm.checked_differences(a, b)
    with pytest.raises(ValueError, match="bounds"):
        cm.column_bounds(a, a, np.array([bad]), np.zeros(1))


@pytest.mark.parametrize("mutation", ["float32", "negative_envelope", "shape", "extra", "missing", "too_many_x", "too_many_time"])
def test_row_contract_rejections(mutation):
    a, b = signals([1., 2.]), signals([3., 4.])
    if mutation == "float32":
        b["rf"] = b["rf"].astype(np.float32)
    elif mutation == "negative_envelope":
        b["envelope"][0, 0] = -1
    elif mutation == "shape":
        b["imaginary"] = np.zeros((1, 3))
    elif mutation == "extra":
        b["error_bound"] = np.zeros((1, 2))
    elif mutation == "missing":
        del b["rf"]
    elif mutation == "too_many_x":
        b = signals(np.zeros((65, 1)))
    else:
        b = signals(np.zeros((1, 2050)))
    with pytest.raises(ValueError):
        cm.checked_differences(a, b)


def test_component_phase_and_independent_envelope_metrics_against_direct_equations():
    a = signals([[1., -2., 0.], [0., 0., 1.]], [[2., 0., -1.], [1., 0., 0.]])
    b = {"rf": -a["rf"], "imaginary": -a["imaginary"], "envelope": a["envelope"].copy()}
    d = cm.checked_differences(a, b)
    metric = cm.Metrics()
    metric.add(a, b, d, 0)
    result = metric.result(coordinates(2, 1, 3))
    for key in cm.SIGNALS:
        np.testing.assert_allclose(result[key]["bias"], np.mean(d[key]), atol=1e-15)
        np.testing.assert_allclose(result[key]["mae"], np.mean(abs(d[key])), atol=1e-15)
        np.testing.assert_allclose(result[key]["rmse"], np.sqrt(np.mean(d[key]**2)), atol=1e-15)
    assert result["envelope"]["rmse"] == 0.
    assert result["complex"]["relative_l2"] == pytest.approx(2.)
    assert result["complex"]["rmse"] == pytest.approx(np.sqrt(np.mean(d["rf"]**2+d["imaginary"]**2)))
    assert result["complex"]["max_location"]["signed_real_difference"] == -2.
    assert result["complex"]["max_location"]["signed_imaginary_difference"] == -4.
    assert "not enclosed" in result["diagnostic_scope"]
    json.dumps(result, allow_nan=False)


def test_maximum_ties_choose_first_global_y_x_time_even_for_out_of_order_rows_and_gate_offset():
    metrics = cm.Metrics()
    a = signals(np.zeros((2, 4)))
    b = signals([[100., -3., 3., 50.], [0., 3., -3., 0.]])
    d = cm.checked_differences(a, b)
    metrics.add(a, b, d, 1, time_slice=slice(1, 3))
    metrics.add(a, b, d, 0, time_slice=(1, 3))
    result = metrics.result(coordinates(2, 2, 4))
    for name in ("rf", "complex"):
        location = result[name]["max_location"]
        assert (location["y_index"], location["x_index"], location["time_index"]) == (0, 0, 1)
        assert location["time_us"] == .375 and location["x_mm"] == .125 and location["y_mm"] == .375
    assert result["sample_count"] == 8 and result["rf"]["max_location"]["signed_difference"] == -3


@pytest.mark.parametrize("scale", [1e200, 1e-200, float.fromhex("0x0.0000000000001p-1022")])
def test_scaled_norms_avoid_naive_square_overflow_and_underflow(scale):
    a, b = signals([scale, -scale]), signals([2*scale, 0.])
    metrics = cm.Metrics()
    metrics.add(a, b, cm.checked_differences(a, b), 0)
    result = metrics.result(coordinates(nt=2))
    assert result["rf"]["rmse"] == pytest.approx(scale, rel=1e-14, abs=0)
    assert result["rf"]["relative_l2"] == pytest.approx(1.)
    assert result["complex"]["rmse"] > 0


def test_exact_streamed_bias_survives_large_cancellation_without_retaining_sources():
    metrics = cm.Metrics()
    a = signals([0., 0., 0.])
    b = signals([1e308, 1., -1e308], envelope=[0., 0., 0.])
    metrics.add(a, b, cm.checked_differences(a, b), 0)
    b["rf"][:] = 0
    result = metrics.result(coordinates())
    assert result["rf"]["bias"] == float(Fraction(1, 3))
    assert result["rf"]["rmse"] == pytest.approx(math.sqrt(Fraction(2, 3))*1e308)
    assert result["rf"]["relative_l2"] is None and "zero" in result["rf"]["relative_l2_reason"]
    assert result["complex"]["relative_l2"] is None
    assert all(not isinstance(value, np.ndarray) for record in metrics.rows.values()
               for component in record["components"].values() for value in component.values())


def test_cross_row_cancellation_is_independent_of_row_submission_order():
    def evaluate(order):
        metric = cm.Metrics()
        for y in order:
            a, b = signals([0., 0.]), signals([[1e300, 1.], [-1e300, 0.]][y], envelope=[0., 0.])
            metric.add(a, b, cm.checked_differences(a, b), y)
        return metric.result(coordinates(ny=2, nt=2))
    assert evaluate([0, 1]) == evaluate([1, 0])
    assert evaluate([0, 1])["rf"]["bias"] == .25


def test_zero_source_and_zero_difference_have_null_relative_error_with_deterministic_locator():
    a = signals(np.zeros((2, 3)))
    metric = cm.Metrics()
    metric.add(a, a, cm.checked_differences(a, a), 0)
    result = metric.result(coordinates(2, 1, 3))
    for name in (*cm.SIGNALS, "complex"):
        assert result[name]["rmse"] == 0 and result[name]["relative_l2"] is None
        assert result[name]["max_location"]["x_index"] == 0


def test_metric_validation_and_rejected_add_does_not_commit_row():
    a, b = signals([1., 2., 3.]), signals([2., 4., 6.])
    d = cm.checked_differences(a, b)
    metric = cm.Metrics()
    wrong = {k: v.copy() for k, v in d.items()}
    wrong["rf"][0, 0] += 1
    with pytest.raises(ValueError, match="Supplied"):
        metric.add(a, b, wrong, 0)
    assert not metric.rows
    metric.add(a, b, d, 0)
    with pytest.raises(ValueError, match="exactly once"):
        metric.add(a, b, d, 0)
    with pytest.raises(ValueError, match="selections"):
        metric.add(a, b, d, 1, slice(1, 3))
    bad = coordinates()
    bad["time_us"][1] = bad["time_us"][0]
    with pytest.raises(ValueError, match="coordinates"):
        metric.result(bad)
    with pytest.raises(ValueError, match="No samples"):
        cm.Metrics().result(coordinates())


@pytest.mark.parametrize("selection", [(0, 0), (-1, 2), (0, 4), (False, 2), slice(0, 3, 2), [0, 2]])
def test_invalid_metric_time_selections_reject(selection):
    a = signals([0., 1., 2.])
    with pytest.raises(ValueError):
        cm.Metrics().add(a, a, cm.checked_differences(a, a), 0, selection)


def test_gate_differences_are_differences_of_source_statistics_not_statistics_of_residual():
    a = signals([3., 0.], envelope=[10., 2.])
    b = signals([0., 4.], envelope=[1., 9.])
    d = cm.checked_differences(a, b)
    gates = cm.gate_products(a, b, d, 0, 2)
    assert gates["peak_envelope"]["difference"][0] == -1
    assert np.max(d["envelope"]) == 7
    assert gates["rms_rf"]["difference"][0] == pytest.approx(1/math.sqrt(2))
    assert np.sqrt(np.mean(d["rf"]**2)) == pytest.approx(5/math.sqrt(2))
    assert "not the statistic of the residual" in gates["rms_rf"]["difference_definition"]
    assert "not enclosed" in gates["rms_rf"]["diagnostic_scope"]
    one = cm.gate_products(a, b, d, 1, 2)
    assert one["peak_envelope"]["difference"][0] == 7
    assert one["rms_rf"]["difference"][0] == 4


@pytest.mark.parametrize("lo,hi", [(0, 0), (1, 0), (-1, 2), (0, 3), (0, True)])
def test_empty_or_outside_gates_reject(lo, hi):
    a = signals([1., 2.])
    with pytest.raises(ValueError):
        cm.gate_products(a, a, cm.checked_differences(a, a), lo, hi)
