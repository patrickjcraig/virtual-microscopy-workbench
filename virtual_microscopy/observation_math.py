"""Exact finite coherent observation of already decoded causal pressure rows.

This is a nine-position discrete observation, not a calibrated beam. No source
I/O, forward solver, material lookup, resampling or floating-environment mutation.
All enclosures are relative to the represented source samples and their supplied
complex-pressure bounds. Summary statistics and physical uncertainty are outside
this contract.
"""
from fractions import Fraction
import math
import struct

import numpy as np


SIGNALS = ("rf", "imaginary", "envelope")
BOUNDS = ("source_propagation", "complex_arithmetic", "complex_total",
          "magnitude_arithmetic", "magnitude_total")
OPERATOR_MODEL = "binomial_3x3_coherent_v1"
ARITHMETIC_CONTRACT = "exact_dyadic_observation_v1"
CERTIFICATE_VERSION = "finite-coherent-observation-certificate-1"
WEIGHT_NUMERATORS = (1, 2, 1, 2, 4, 2, 1, 2, 1)
WEIGHT_DENOMINATOR = 16
OFFSETS_YX = tuple((y, x) for y in (-1, 0, 1) for x in (-1, 0, 1))
WEIGHTS = tuple(n / WEIGHT_DENOMINATOR for n in WEIGHT_NUMERATORS)
WEIGHTS_FLOAT64_HEX = struct.pack("<9d", *WEIGHTS).hex()
ADDITIONAL_PHASE_RADIANS = 0.0
MIN_SOURCE_X, MAX_SOURCE_X = 16, 64
MIN_TIME, MAX_TIME = 2, 2049
TIME_BLOCK = 64
MAX_MAGNITUDE_DOUBLINGS = 8
MAX_OUTPUT_ROW_BYTES = 3 * (MAX_SOURCE_X-2) * MAX_TIME * 8 + 5 * (MAX_SOURCE_X-2) * 8
ZERO_SIGN_POLICY = "exact zero is +0; nonzero values rounding to zero retain their sign"
OFFLINE_VERIFICATION_LIMIT = (
    "Saved-row checks verify source propagation, published bound composition and "
    "magnitude brackets. Exact weighted-sum conversion errors require source "
    "waveforms and are established by source-aware publication verification."
)
_UNIT_DENOMINATOR = 1 << 1074
_WEIGHTED_DENOMINATOR = 1 << 1078
_MAX_FINITE_BITS = 0x7FEFFFFFFFFFFFFF
_SIGN_BIT = 1 << 63
_MAX_FINITE = Fraction((1 << 53)-1) * (1 << 971)


class ObservationCancelled(Exception):
    """The caller requested cancellation before a complete row was returned."""


def operator_metadata():
    """Fresh JSON-compatible description; weights and evaluation order are fixed."""
    return {"operator": OPERATOR_MODEL, "arithmetic_contract": ARITHMETIC_CONTRACT,
            "certificate_version": CERTIFICATE_VERSION,
            "weight_numerators": list(WEIGHT_NUMERATORS), "weight_denominator": WEIGHT_DENOMINATOR,
            "weights_float64_le_hex": WEIGHTS_FLOAT64_HEX,
            "offsets_yx": [list(v) for v in OFFSETS_YX],
            "additional_phase_radians": ADDITIONAL_PHASE_RADIANS,
            "zero_sign_policy": ZERO_SIGN_POLICY, "time_block": TIME_BLOCK,
            "magnitude_initial_radius": "one ulp of the returned nonnegative magnitude",
            "magnitude_max_doublings": MAX_MAGNITUDE_DOUBLINGS,
            "offline_verification_limit": OFFLINE_VERIFICATION_LIMIT}


def _from_bits(value):
    return struct.unpack("<d", struct.pack("<Q", value))[0]


def _bits(value):
    return struct.unpack("<Q", struct.pack("<d", float(value)))[0]


def _units(value):
    """Exact signed integer representation in units of the least subnormal."""
    numerator, denominator = float(value).as_integer_ratio()
    return numerator << (1075-denominator.bit_length())


def upward_nonnegative(value):
    """Smallest finite binary64 >= an exact nonnegative Fraction, by integer ceil.

    Constructing bits avoids relying on a floating conversion's rounding mode.
    """
    if not isinstance(value, Fraction) or value < 0 or value > _MAX_FINITE:
        raise ValueError("Observation bound must be an exact nonnegative rational representable finitely.")
    if value == 0:
        return 0.0
    n, d = value.numerator, value.denominator
    if value <= Fraction(1, 1 << 1022):
        significand = ((n << 1074) + d-1) // d
        return _from_bits(significand)
    exponent = n.bit_length()-d.bit_length()
    below_power = n < (d << exponent) if exponent >= 0 else (n << -exponent) < d
    if below_power:
        exponent -= 1
    power = exponent-52
    numerator, denominator = (n, d << power) if power >= 0 else (n << -power, d)
    significand = (numerator+denominator-1) // denominator
    if significand == 1 << 53:
        significand >>= 1
        exponent += 1
    return _from_bits(((exponent+1023) << 52) | (significand-(1 << 52)))


def _tolerance(value):
    if type(value) not in (int, float):
        raise ValueError("Observation absolute tolerance must be a finite positive scalar.")
    try:
        tolerance = float(value)
    except OverflowError as exc:
        raise ValueError("Observation absolute tolerance must be a finite positive scalar.") from exc
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("Observation absolute tolerance must be a finite positive scalar.")
    return tolerance


def _cancel(cancelled):
    if not callable(cancelled):
        raise ValueError("Observation cancellation callback must be callable.")
    if cancelled():
        raise ObservationCancelled("Finite coherent observation was cancelled before row completion.")


def _array(value, shape, name):
    if type(value) is not np.ndarray or value.dtype != np.dtype(np.float64) or value.shape != shape:
        raise ValueError(f"Observation {name} requires native float64 shape {list(shape)}.")
    if not np.isfinite(value).all():
        raise ValueError(f"Observation {name} contains nonfinite values.")


def _source_shape(rf, imaginary, source_bounds):
    if (type(rf) is not np.ndarray or rf.ndim != 3 or rf.shape[0] != 3 or
            not MIN_SOURCE_X <= rf.shape[1] <= MAX_SOURCE_X or
            not MIN_TIME <= rf.shape[2] <= MAX_TIME):
        raise ValueError("Observation support must have shape [3,16..64,2..2049].")
    _array(rf, rf.shape, "real source pressure")
    _array(imaginary, rf.shape, "imaginary source pressure")
    _source_bounds(source_bounds, rf.shape[1])
    return rf.shape[1]-2, rf.shape[2]


def _source_bounds(bounds, source_nx):
    _array(bounds, (3, source_nx), "source bounds")
    if np.any(bounds < 0):
        raise ValueError("Observation source bounds must be nonnegative.")


def _result_shape(result, source_bounds, tolerance, shape=None):
    if not isinstance(result, dict) or set(result) != set(SIGNALS+BOUNDS):
        raise ValueError("Observation results require exactly three signals and five bound maps.")
    rf = result["rf"]
    if (type(rf) is not np.ndarray or rf.ndim != 2 or
            not MIN_SOURCE_X-2 <= rf.shape[0] <= MAX_SOURCE_X-2 or
            not MIN_TIME <= rf.shape[1] <= MAX_TIME or shape is not None and rf.shape != shape):
        raise ValueError("Observation output must have shape [14..62,2..2049] matching source support.")
    shape = rf.shape
    _source_bounds(source_bounds, shape[0]+2)
    for name in SIGNALS:
        _array(result[name], shape, name)
    for name in BOUNDS:
        _array(result[name], (shape[0],), name)
        if np.any(result[name] < 0):
            raise ValueError("Observation bound maps must be nonnegative.")
    if np.any(result["envelope"] < 0):
        raise ValueError("Observation magnitude must be nonnegative.")
    if np.any(result["complex_total"] > tolerance) or np.any(result["magnitude_total"] > tolerance):
        raise ValueError("Observation total bound exceeds the requested absolute tolerance.")
    return shape


def _weighted_units(values, x, t=None):
    # Producer uses the explicit flat nine-position dot product.
    return sum(w * _units(values[y, x+dx] if t is None else values[y, x+dx, t])
               for w, (y, dx) in zip(WEIGHT_NUMERATORS, ((y, dx) for y in range(3) for dx in range(3))))


def _reference_units(values, x, t):
    # Source-aware verifier independently arranges the exact separable integer
    # expression. There is no intermediate floating convolution or producer call.
    rows = []
    for y in range(3):
        rows.append(_units(values[y, x, t]) + 2*_units(values[y, x+1, t]) + _units(values[y, x+2, t]))
    return rows[0] + 2*rows[1] + rows[2]


def _rounded_component(numerator):
    value = float(Fraction(numerator, _WEIGHTED_DENOMINATOR))
    if not math.isfinite(value):
        raise ValueError("Observation component conversion is not finite.")
    return value, abs(16*_units(value)-numerator)


def _verify_nearest(numerator, value):
    """Independent exact midpoint/tie check in common integer units."""
    raw = _bits(value)
    if numerator == 0:
        if raw != 0:
            raise ValueError("Observation exact zero must be represented by positive zero.")
        return
    if bool(raw & _SIGN_BIT) != (numerator < 0):
        raise ValueError("Observation component conversion has an incorrect sign.")
    positive = raw & ~_SIGN_BIT
    center = abs(_units(value))
    previous = _units(_from_bits(positive-1)) if positive else 0
    following = (_units(_from_bits(positive+1)) if positive < _MAX_FINITE_BITS
                 else center+(center-previous))
    lower = 8*(center+previous) if positive else 0
    upper = 8*(center+following)
    q = abs(numerator)
    if q < lower or q > upper or (q in (lower, upper) and positive & 1):
        raise ValueError("Observation component is not the nearest-even exact weighted sum.")


def _magnitude_radius_units(real, imaginary, magnitude):
    """Validate an arbitrary returned magnitude; never assume hypot accuracy.

    Integer squares all share the exact factor 2^-2148. This is the rational
    squared-bracket test without temporary Fraction gcd or square-root operations.
    """
    if not math.isfinite(magnitude) or magnitude < 0:
        raise ValueError("Observation magnitude is unrepresentable or negative.")
    r, i, h = _units(real), _units(imaginary), _units(magnitude)
    square = r*r+i*i
    radius = _units(math.ulp(float(magnitude)))
    for _ in range(MAX_MAGNITUDE_DOUBLINGS+1):
        lo, hi = max(0, h-radius), h+radius
        if lo*lo <= square <= hi*hi:
            return radius
        radius *= 2
    raise ValueError("Observation magnitude failed the bounded exact enclosure check.")


def _bound_maps(source_bounds, complex_maxima, magnitude_maxima):
    nx = len(complex_maxima)
    maps = {key: np.empty(nx, dtype=np.float64) for key in BOUNDS}
    for x in range(nx):
        s = upward_nonnegative(Fraction(_weighted_units(source_bounds, x), _WEIGHTED_DENOMINATOR))
        c = upward_nonnegative(Fraction(complex_maxima[x], _WEIGHTED_DENOMINATOR))
        d = upward_nonnegative(Fraction(magnitude_maxima[x], _UNIT_DENOMINATOR))
        total = upward_nonnegative(Fraction(s)+Fraction(c))
        values = (s, c, total, d, upward_nonnegative(Fraction(total)+Fraction(d)))
        for key, value in zip(BOUNDS, values):
            maps[key][x] = value
    return maps


def _check_bound_maps(source_bounds, result, magnitude_maxima, complex_maxima=None):
    for x, radius in enumerate(magnitude_maxima):
        b = {key: Fraction(float(result[key][x])) for key in BOUNDS}
        s = Fraction(_weighted_units(source_bounds, x), _WEIGHTED_DENOMINATOR)
        if b["source_propagation"] < s:
            raise ValueError("Observation source propagation bound is under-reported.")
        if complex_maxima is not None and b["complex_arithmetic"] < Fraction(complex_maxima[x], _WEIGHTED_DENOMINATOR):
            raise ValueError("Observation complex arithmetic bound is under-reported.")
        if b["magnitude_arithmetic"] < Fraction(radius, _UNIT_DENOMINATOR):
            raise ValueError("Observation magnitude arithmetic bound is under-reported.")
        if b["complex_total"] < b["source_propagation"]+b["complex_arithmetic"]:
            raise ValueError("Observation complex total does not enclose its exact published component sum.")
        if b["magnitude_total"] < b["complex_total"]+b["magnitude_arithmetic"]:
            raise ValueError("Observation magnitude total does not enclose its exact published component sum.")


def observe_row(rf, imaginary, source_bounds, *, absolute_tolerance, cancelled=lambda: False):
    """Return three [output_x,time] float64 signals and five [output_x] maps.

    Input support is exactly three rows. Only interior X centers are produced;
    no edge padding, normalization, source-envelope averaging or time resampling.
    Cancellation is checked before and after every block of at most 64 times.
    """
    _cancel(cancelled)
    tolerance = _tolerance(absolute_tolerance)
    nx, nt = _source_shape(rf, imaginary, source_bounds)
    for x in range(nx):
        if Fraction(_weighted_units(source_bounds, x), _WEIGHTED_DENOMINATOR) > Fraction(tolerance):
            raise ValueError("Observation propagated source bound already exceeds the requested tolerance.")
    result = {key: np.empty((nx, nt), dtype=np.float64) for key in SIGNALS}
    complex_maxima, magnitude_maxima = [0]*nx, [0]*nx
    for start in range(0, nt, TIME_BLOCK):
        _cancel(cancelled)
        for x in range(nx):
            for t in range(start, min(nt, start+TIME_BLOCK)):
                real, er = _rounded_component(_weighted_units(rf, x, t))
                imag, ei = _rounded_component(_weighted_units(imaginary, x, t))
                h = math.hypot(real, imag)
                radius = _magnitude_radius_units(real, imag, h)
                result["rf"][x, t], result["imaginary"][x, t], result["envelope"][x, t] = real, imag, h
                complex_maxima[x] = max(complex_maxima[x], er+ei)
                magnitude_maxima[x] = max(magnitude_maxima[x], radius)
        _cancel(cancelled)
    result.update(_bound_maps(source_bounds, complex_maxima, magnitude_maxima))
    _result_shape(result, source_bounds, tolerance, (nx, nt))
    _cancel(cancelled)
    return result


def verify_row(rf, imaginary, source_bounds, result, *, absolute_tolerance, cancelled=lambda: False):
    """Source-aware publication check, without producer or hypot resynthesis.

    Recompute independent exact sums, validate returned component rounding and
    actual conversion errors, then check every magnitude bracket and map. Finite
    conservative maps are accepted; under-reported maps or totals are rejected.
    Success returns None. This function never mutates input or result arrays.
    """
    _cancel(cancelled)
    tolerance = _tolerance(absolute_tolerance)
    nx, nt = _source_shape(rf, imaginary, source_bounds)
    _result_shape(result, source_bounds, tolerance, (nx, nt))
    complex_maxima, magnitude_maxima = [0]*nx, [0]*nx
    for start in range(0, nt, TIME_BLOCK):
        _cancel(cancelled)
        for x in range(nx):
            for t in range(start, min(nt, start+TIME_BLOCK)):
                qr, qi = _reference_units(rf, x, t), _reference_units(imaginary, x, t)
                real, imag, h = (float(result[key][x, t]) for key in SIGNALS)
                _verify_nearest(qr, real)
                _verify_nearest(qi, imag)
                error = abs(16*_units(real)-qr)+abs(16*_units(imag)-qi)
                complex_maxima[x] = max(complex_maxima[x], error)
                magnitude_maxima[x] = max(magnitude_maxima[x], _magnitude_radius_units(real, imag, h))
        _cancel(cancelled)
    _check_bound_maps(source_bounds, result, magnitude_maxima, complex_maxima)
    _cancel(cancelled)


def verify_saved_row(result, source_bounds, *, absolute_tolerance):
    """Source-free mathematical consistency check; see OFFLINE_VERIFICATION_LIMIT.

    Requires source bounds recovered from the frozen verified class map. Hash and
    source-identity verification belong to the typed store. No forward imports or
    hypot evaluation are required. This cannot recompute complex conversion error.
    """
    nx, nt = _result_shape(result, source_bounds, _tolerance(absolute_tolerance))
    magnitude_maxima = [0]*nx
    for x in range(nx):
        for t in range(nt):
            real, imag, h = (float(result[key][x, t]) for key in SIGNALS)
            magnitude_maxima[x] = max(magnitude_maxima[x], _magnitude_radius_units(real, imag, h))
    _check_bound_maps(source_bounds, result, magnitude_maxima)
