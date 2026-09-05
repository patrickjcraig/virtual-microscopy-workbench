"""Independent rational oracles for two distinct saved-observation errors."""
from fractions import Fraction
import json
import math

import numpy as np
import pytest

from virtual_microscopy import causal_comparison_math as legacy
from virtual_microscopy import observation_comparison_math as om


def signals(real, imaginary=None, envelope=None):
    real = np.asarray(real, dtype=np.float64)
    if real.ndim == 1:
        real = real[None, :]
    imaginary = np.zeros_like(real) if imaginary is None else np.asarray(imaginary, dtype=np.float64).reshape(real.shape)
    envelope = np.hypot(real, imaginary) if envelope is None else np.asarray(envelope, dtype=np.float64).reshape(real.shape)
    return dict(rf=real.copy(), imaginary=imaginary.copy(), envelope=envelope.copy())


def exact(value):
    return Fraction(float(value))


def smallest_upper(actual, target):
    assert math.isfinite(actual) and exact(actual) >= target
    if actual:
        assert exact(math.nextafter(float(actual), -math.inf)) < target


def oracle(a, b, ca, cb, ma, mb, bounds):
    assert tuple(bounds) == om.BOUNDS
    for x in range(a["rf"].shape[0]):
        rho = {}
        for signal in om.SIGNALS:
            left = max(exact(abs(v)) for v in a[signal][x])
            right = max(exact(abs(v)) for v in b[signal][x])
            rho[signal] = (left+right)/2**53+Fraction(1, 2**1074)
        smallest_upper(bounds["complex_arithmetic"][x], rho["rf"]+rho["imaginary"])
        smallest_upper(bounds["magnitude_arithmetic"][x], rho["envelope"])
        for prefix, left, right in (("complex", ca, cb), ("magnitude", ma, mb)):
            smallest_upper(bounds[prefix+"_source_sum"][x], exact(left[x])+exact(right[x]))
            smallest_upper(bounds[prefix+"_total"][x],
                exact(bounds[prefix+"_source_sum"][x])+exact(bounds[prefix+"_arithmetic"][x]))


def test_frozen_binary64_and_diagnostic_implementations_are_direct_aliases():
    for name in ("SIGNALS", "check_arithmetic_environment", "checked_differences", "Metrics", "gate_products"):
        assert getattr(om, name) is getattr(legacy, name)
    assert om.check_arithmetic_environment()["environment_changed"] is False


def test_unequal_source_errors_and_minimal_published_compositions():
    a = signals([[1., -2., 10.], [0., 1e-300, 0.]], [[2., 0., -20.], [0., 0., 0.]])
    b = signals([[0., 0., -11.], [0., -1e-300, 0.]], [[0., 0., 21.], [0., 0., 0.]])
    ca, cb = np.array([1., 0.]), np.array([2**-53, 0.])
    ma, mb = np.array([2., 1e-299]), np.array([2**-52, 3e-300])
    snapshots = [{k: v.tobytes() for k, v in s.items()} for s in (a, b)]
    maps = om.column_bounds(a, b, ca, cb, ma, mb)
    oracle(a, b, ca, cb, ma, mb, maps)
    assert maps["complex_source_sum"][0] == math.nextafter(1., math.inf)
    assert maps["magnitude_source_sum"][0] == math.nextafter(2., math.inf)
    assert maps["magnitude_total"][1] > maps["complex_total"][1]
    assert snapshots == [{k: v.tobytes() for k, v in s.items()} for s in (a, b)]
    for value in maps.values():
        assert value.dtype == np.float64 and value.shape == (2,)
        assert np.asarray(json.loads(json.dumps(value.tolist())), np.float64).tobytes() == value.tobytes()


@pytest.mark.parametrize("scale", [0., float.fromhex("0x0.0000000000001p-1022"), 1e-300, 1e-8, 1., 1e290])
def test_independent_complex_and_magnitude_perturbations_are_enclosed(scale):
    a = signals([[scale, -scale, scale/2]], [[-scale, scale/2, scale]])
    b = signals([[-scale/3, scale, scale/7]], [[scale/5, -scale, -scale/3]])
    eta = float.fromhex("0x0.0000000000001p-1022")
    ca, cb, ma, mb = (np.array([value]) for value in (eta, 2*eta, 1e-10, 3e-9))
    maps = om.column_bounds(a, b, ca, cb, ma, mb)
    d = om.checked_differences(a, b)
    oracle(a, b, ca, cb, ma, mb, maps)
    complex_source, magnitude_source = exact(ca[0])+exact(cb[0]), exact(ma[0])+exact(mb[0])
    for t in range(3):
        errors = {key: exact(d[key][0, t])-(exact(b[key][0, t])-exact(a[key][0, t])) for key in om.SIGNALS}
        er = errors["rf"]-complex_source*Fraction(3, 5)
        ei = errors["imaginary"]-complex_source*Fraction(4, 5)
        assert er*er+ei*ei <= exact(maps["complex_total"][0])**2
        assert abs(errors["envelope"]-magnitude_source) <= exact(maps["magnitude_total"][0])


def test_distinct_bound_inputs_never_form_discarded_legacy_total():
    maximum = float(np.finfo(np.float64).max)
    below = math.nextafter(maximum, 0.)
    a = signals([maximum], [maximum], [0.])
    zero, huge = np.zeros(1), np.array([below])
    maps = om.column_bounds(a, a, zero, zero, huge, zero)
    oracle(a, a, zero, zero, huge, zero, maps)
    assert maps["magnitude_total"][0] == maximum
    # This irrelevant total is intentionally not part of the adapter contract.
    with pytest.raises(ValueError, match="finitely"):
        legacy.column_bounds(a, a, huge, zero)


@pytest.mark.parametrize("position", range(4))
@pytest.mark.parametrize("bad", [np.array([np.nan]), np.array([np.inf]), np.array([-1.]),
    np.array([1.], dtype=np.float32), np.array([[1.]]), np.array([1., 2.]), [1.]])
def test_every_source_certificate_rejects_invalid_values_types_and_shapes(position, bad):
    errors = [np.zeros(1) for _ in range(4)]
    errors[position] = bad
    with pytest.raises(ValueError, match="bounds"):
        om.column_bounds(signals([1.]), signals([2.]), *errors)


@pytest.mark.parametrize("prefix", ["complex", "magnitude"])
def test_required_source_sum_or_total_overflow_rejects(prefix):
    largest = np.array([np.finfo(np.float64).max])
    errors = [np.zeros(1) for _ in range(4)]
    offset = 0 if prefix == "complex" else 2
    errors[offset] = largest
    a = signals([1.])
    with pytest.raises(ValueError, match="finitely"):
        om.column_bounds(a, a, *errors)
    errors[offset+1] = largest
    with pytest.raises(ValueError, match="finitely"):
        om.column_bounds(a, a, *errors)


@pytest.mark.parametrize("mutation", ["nonfinite", "overflow", "shape", "negative_magnitude", "missing", "dtype", "too_many_x"])
def test_legacy_signal_guards_remain_active(mutation):
    a, b = signals([1., 2.]), signals([3., 4.])
    if mutation == "nonfinite":
        b["imaginary"][0, 0] = np.nan
    elif mutation == "overflow":
        a["rf"][0, 0], b["rf"][0, 0] = -np.finfo(float).max, np.finfo(float).max
    elif mutation == "shape":
        b["imaginary"] = np.zeros((1, 3))
    elif mutation == "negative_magnitude":
        b["envelope"][0, 0] = -1.
    elif mutation == "missing":
        del a["rf"]
    elif mutation == "dtype":
        b["rf"] = b["rf"].astype(np.float32)
    else:
        a = signals(np.zeros((65, 2)))
    with pytest.raises(ValueError):
        om.column_bounds(a, b, *(np.zeros(1) for _ in range(4)))


def test_same_source_control_and_phase_reversal_keep_magnitude_difference_distinct():
    a = signals([[1., -2., 0.]], [[2., 0., -1.]])
    b = {"rf": -a["rf"], "imaginary": -a["imaginary"], "envelope": a["envelope"].copy()}
    coordinates = {"x_mm": np.array([.25]), "y_mm": np.array([.5]), "time_us": np.array([0., .1, .2])}
    for candidate, factor in ((a, 0.), (b, 2.)):
        d = om.checked_differences(a, candidate)
        metrics = om.Metrics()
        metrics.add(a, candidate, d, 0)
        result = metrics.result(coordinates)
        assert result["envelope"]["rmse"] == 0.
        assert result["complex"]["relative_l2"] == factor
        gates = om.gate_products(a, candidate, d, 0, 3)
        assert np.array_equal(gates["peak_envelope"]["difference"], [0.])
        assert np.array_equal(gates["rms_rf"]["difference"], [0.])
    assert np.any(om.checked_differences(a, b)["rf"] != 0)


def test_adapter_entry_and_exit_environment_guards(monkeypatch):
    checks = []
    def check():
        checks.append(True)
        if len(checks) == 2:
            raise ValueError("changed environment")
    monkeypatch.setattr(om, "check_arithmetic_environment", check)
    with pytest.raises(ValueError, match="changed environment"):
        om.column_bounds(signals([0.]), signals([0.]), *(np.zeros(1) for _ in range(4)))
    assert len(checks) == 2
