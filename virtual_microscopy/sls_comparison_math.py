"""Bounded SLS residuals; old binary64 comparison arithmetic remains unchanged.

Only reflected RF component subtraction has the five stated error bounds.
Frequency curves, residual magnitudes, gates and reductions are diagnostics.
"""
import math

import numpy as np

from . import causal_comparison_math as _base

SIGNALS = _base.SIGNALS
BOUNDS = ("source_sum", "complex_arithmetic", "complex_total", "magnitude_arithmetic", "magnitude_total")
check_arithmetic_environment = _base.check_arithmetic_environment
DIAGNOSTIC_SCOPE = "Ordinary candidate B minus reference A diagnostics; not enclosed summaries, ground truth, detection or physical accuracy."
BOUND_DEFINITION = (
    "Full-record reflected RF bounds. Each complete source total_error_bound covers both complex pressure "
    "and separately saved magnitude. Source totals are summed outward; actual binary64 subtraction "
    "arithmetic is added outward using the already-published components. No source component is stripped. "
    "Complex residual magnitude, spectral curves, gates and summary reductions are ordinary diagnostics."
)


def _vector(value, maximum, *, count=None):
    if (type(value) is not np.ndarray or value.dtype != np.dtype(np.float64) or value.ndim != 1 or
            not 1 <= len(value) <= maximum or (count is not None and len(value) != count) or not np.isfinite(value).all()):
        raise ValueError("SLS comparison requires finite bounded native float64 vectors of matching length.")
    return value


def _rows(signals):
    if type(signals) is not dict or set(signals) != set(SIGNALS):
        raise ValueError("Reflected recording requires exactly rf, imaginary and envelope.")
    count = len(_vector(signals["rf"], 2049))
    return {key: _vector(signals[key], 2049, count=count)[None, :] for key in SIGNALS}


def checked_differences(a, b):
    return {key: value[0] for key, value in _base.checked_differences(_rows(a), _rows(b)).items()}


def column_bounds(a, b, eps_a, eps_b):
    """Five scalars enclosing a whole standalone recording, with no spatial axis."""
    for value in (eps_a, eps_b):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("Source totals must be finite nonnegative numbers.")
    old = _base.column_bounds(_rows(a), _rows(b), np.array([eps_a], np.float64), np.array([eps_b], np.float64))
    rename = {"envelope_arithmetic": "magnitude_arithmetic", "envelope_total": "magnitude_total"}
    return {rename.get(key, key): float(value[0]) for key, value in old.items()}


def rf_metrics(a, b, difference, time_us, selection=None):
    time = _vector(time_us, 2049, count=len(a["rf"]))
    metrics = _base.Metrics()
    metrics.add(_rows(a), _rows(b), _rows(difference), 0, time_slice=selection)
    result = metrics.result({"x_mm":np.array([0.]), "y_mm":np.array([0.]), "time_us":time})
    for key in (*SIGNALS, "complex"):
        location = result[key]["max_location"]
        for field in ("x_index", "y_index", "x_mm", "y_mm"):
            location.pop(field)
    result["diagnostic_scope"] = DIAGNOSTIC_SCOPE
    return result


def gate_products(a, b, difference, lo, hi):
    products = _base.gate_products(_rows(a), _rows(b), _rows(difference), lo, hi)
    result = {key:{field:float(value[0]) if isinstance(value, np.ndarray) else value
                  for field, value in product.items()} for key, product in products.items()}
    result["residual_gate"] = {
        "rms_rf": float(_base._rms_columns(difference["rf"][None, lo:hi])[0]),
        "peak_absolute_saved_magnitude_difference": float(np.max(np.abs(difference["envelope"][lo:hi]))),
        "definition": "Statistics of the residual; distinct from statistic(B) minus statistic(A).",
        "diagnostic_scope": DIAGNOSTIC_SCOPE}
    return result


def _metric(a, d, frequency):
    signed, absolute = _base._sum_units(d)
    count = len(d)
    from fractions import Fraction
    relative, reason = _base._relative([_base._squares(d)], [_base._squares(a)])
    index = int(np.argmax(np.abs(d)))
    return {"sample_count":count, "bias":float(Fraction(signed, count << 1074)),
        "mae":float(Fraction(absolute, count << 1074)), "rmse":_base._norm([_base._squares(d)], count),
        "relative_l2":relative, "relative_l2_reason":reason,
        "max_absolute_difference":float(abs(d[index])),
        "max_location":{"frequency_index":index,"frequency_mhz":float(frequency[index]),"signed_difference":float(d[index])}}


def spectrum_comparison(a, b, frequency_mhz):
    """Nine signed sampled spectral differences; never subtract wrapped phases."""
    frequency = _vector(frequency_mhz, 8193)
    if np.any(np.diff(frequency) <= 0):
        raise ValueError("Saved frequencies must be strictly increasing.")
    count = len(frequency)
    differences, metrics = {}, {}
    def pair(left, right):
        left, right = (_vector(np.asarray(v, np.float64), 8193, count=count) for v in (left, right))
        value = _base._subtract(left, right)
        if not np.isfinite(value).all():
            raise ValueError("A spectral diagnostic difference overflowed.")
        return left, value
    for name in ("reflection", "transmission"):
        differences[name], metrics[name] = {}, {}
        arrays, residuals = {}, {}
        for key in ("real", "imag", "magnitude"):
            arrays[key], residuals[key] = pair(a[name][key], b[name][key])
            differences[name][key] = residuals[key].tolist()
            metrics[name][key] = _metric(arrays[key], residuals[key], frequency)
        with np.errstate(over="ignore", invalid="ignore", under="ignore"):
            magnitude = np.hypot(residuals["real"], residuals["imag"])
        if not np.isfinite(magnitude).all():
            raise ValueError("A spectral complex residual magnitude overflowed.")
        index = int(np.argmax(magnitude))
        squares = [_base._squares(residuals[k]) for k in ("real", "imag")]
        reference = [_base._squares(arrays[k]) for k in ("real", "imag")]
        relative, reason = _base._relative(squares, reference)
        metrics[name]["complex"] = {"sample_count":count, "rmse":_base._norm(squares,count),
            "relative_l2":relative,"relative_l2_reason":reason,"max_absolute_difference":float(magnitude[index]),
            "max_location":{"frequency_index":index,"frequency_mhz":float(frequency[index]),
                "signed_real_difference":float(residuals["real"][index]),"signed_imaginary_difference":float(residuals["imag"][index])}}
    for name in ("reflectance", "transmittance", "absorptance"):
        left, value = pair(a[name], b[name])
        differences[name] = value.tolist()
        metrics[name] = _metric(left, value, frequency)
    return {"difference":differences,"metrics":metrics,"diagnostic_scope":DIAGNOSTIC_SCOPE}
