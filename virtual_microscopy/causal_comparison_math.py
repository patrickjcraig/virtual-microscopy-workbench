"""Bounded comparison arithmetic for already decoded causal pressure rows.

Only the component subtraction enclosures are certificates. Summary statistics,
gate reductions and complex magnitudes are ordinary floating diagnostics.
No source I/O, material lookup, forward kernel or floating-environment mutation.
"""
from fractions import Fraction
import math
import platform
import sys

import numpy as np


SIGNALS = ("rf", "imaginary", "envelope")
MAX_X = 64
MAX_TIME = 2049
MAX_ROWS = 64
UNIT_ROUNDOFF = Fraction(1, 1 << 53)
MIN_SUBNORMAL = Fraction(1, 1 << 1074)
DIAGNOSTIC_SCOPE = (
    "Ordinary numerical diagnostics of candidate B minus reference A; not enclosed "
    "summary statistics, physical accuracy, detection performance or ground truth."
)
BOUND_DEFINITION = (
    "Full-record column bounds for returned float64 component differences. The "
    "source bound sum and subtraction arithmetic bounds are rounded outward with "
    "exact rational arithmetic; totals enclose the sum of published components. "
    "Complex magnitude evaluation and gate/statistical reductions are not certified."
)


def _subtract(a, b):
    """The one explicit binary64 evaluation path used by probes and residuals."""
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        return np.subtract(b, a, dtype=np.float64)


def check_arithmetic_environment():
    """Read-only platform admission and entry/exit probes of actual subtraction.

    Bit-constructed operands test both tie parities/signs, hidden double rounding,
    gradual underflow, subnormal inputs and signed zero. No fenv is set or reset.
    The surrounding processing must not introduce unprobed foreign arithmetic.
    """
    if (platform.python_implementation() != "CPython" or
            sys.platform not in ("win32", "linux") or sys.byteorder != "little" or
            platform.machine().lower() not in ("amd64", "x86_64") or
            np.__version__.split(".")[0] != "2" or
            np.dtype(np.float64).itemsize != 8 or np.finfo(np.float64).nmant != 52):
        raise ValueError("Certified comparison subtraction supports CPython/NumPy 2 on little-endian Windows or Linux x86-64 only.")
    # Rows are A bits, B bits, correctly rounded B-A bits. Expected subnormal
    # values are never constructed by arithmetic that might itself be flushed.
    cases = np.array([
        [0xBC90000000000000, 0x3FF0000000000000, 0x3FF0000000000000],
        [0xBCA0000000000001, 0x3FF0000000000000, 0x3FF0000000000001],
        [0xBCA0000000000000, 0x3FF0000000000000, 0x3FF0000000000000],
        [0xBCA0000000000000, 0x3FF0000000000001, 0x3FF0000000000002],
        [0x3C90000000000000, 0xBFF0000000000000, 0xBFF0000000000000],
        [0x3CA0000000000001, 0xBFF0000000000000, 0xBFF0000000000001],
        [0x3CA0000000000000, 0xBFF0000000000001, 0xBFF0000000000002],
        [0x0010000000000000, 0x0018000000000000, 0x0008000000000000],
        [0x000FFFFFFFFFFFFF, 0x0010000000000000, 0x0000000000000001],
        [0x0010000000000000, 0x000FFFFFFFFFFFFF, 0x8000000000000001],
        [0x0000000000000000, 0x8000000000000000, 0x8000000000000000],
        [0x8000000000000000, 0x0000000000000000, 0x0000000000000000],
        [0x8000000000000000, 0x8000000000000000, 0x0000000000000000],
    ], dtype=np.uint64)
    # Exercise vector blocks and their scalar remainder, plus a strided path.
    repeated = np.tile(cases, (79, 1))
    for rows in (repeated, repeated[::2]):
        a, b = ((np.ascontiguousarray(rows[:, column]) if rows is repeated else rows[:, column]).view(np.float64)
                for column in (0, 1))
        actual = _subtract(a, b)
        if actual.dtype != np.dtype(np.float64) or not np.array_equal(actual.view(np.uint64), rows[:, 2]):
            raise ValueError("Unsupported floating arithmetic: causal comparison requires correctly rounded binary64 subtraction with gradual underflow.")
    return {"contract": "numpy-binary64-nearest-gradual-v1", "platform": sys.platform,
            "machine": platform.machine(), "python": platform.python_version(), "numpy": np.__version__,
            "probe_cases": len(cases), "environment_changed": False}


def upward_nonnegative(value):
    """Smallest finite binary64 value at least the exact nonnegative Fraction."""
    if not isinstance(value, Fraction) or value < 0:
        raise ValueError("An outward bound requires an exact nonnegative Fraction.")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError("The comparison bound cannot be represented finitely.") from exc
    if not math.isfinite(result):
        raise ValueError("The comparison bound cannot be represented finitely.")
    if Fraction(result) < value:
        result = math.nextafter(result, math.inf)
    if not math.isfinite(result) or Fraction(result) < value:
        raise ValueError("The comparison bound cannot be rounded outward finitely.")
    return result


def _signals(values, *, source=True, shape=None):
    if not isinstance(values, dict) or set(values) != set(SIGNALS):
        raise ValueError("A causal row requires exactly rf, imaginary and envelope arrays.")
    for name in SIGNALS:
        array = values[name]
        if (type(array) is not np.ndarray or array.dtype != np.dtype(np.float64) or
                array.ndim != 2 or not 1 <= array.shape[0] <= MAX_X or
                not 1 <= array.shape[1] <= MAX_TIME):
            raise ValueError("Comparison rows require native float64 [x,time] arrays within 64 by 2049 samples.")
        if shape is None:
            shape = array.shape
        if array.shape != shape or not np.isfinite(array).all():
            raise ValueError("Comparison row shapes must match and all samples must be finite.")
    if source and np.any(values["envelope"] < 0):
        raise ValueError("A saved complex-pressure magnitude cannot be negative.")
    return shape


def _sources(a, b):
    shape = _signals(a)
    _signals(b, shape=shape)
    return shape


def checked_differences(a, b):
    """Return three finite float64 arrays, each one NumPy subtraction B-A."""
    check_arithmetic_environment()
    _sources(a, b)
    result = {name: _subtract(a[name], b[name]) for name in SIGNALS}
    if any(not np.isfinite(value).all() for value in result.values()):
        raise ValueError("A causal comparison difference overflowed; no finite residual can be published.")
    check_arithmetic_environment()
    return result


def _difference(a, b, difference):
    shape = _sources(a, b)
    _signals(difference, source=False, shape=shape)
    for name in SIGNALS:
        expected = _subtract(a[name], b[name])
        if not np.array_equal(difference[name].view(np.uint64), expected.view(np.uint64)):
            raise ValueError("Supplied comparison residuals differ from the declared float64 B-A subtraction.")
    return shape


def column_bounds(a, b, eps_a, eps_b):
    """Outward full-record per-column error maps, with no rounded intermediates."""
    check_arithmetic_environment()
    nx, _ = _sources(a, b)
    for value in (eps_a, eps_b):
        if (type(value) is not np.ndarray or value.dtype != np.dtype(np.float64) or
                value.shape != (nx,) or not np.isfinite(value).all() or np.any(value < 0)):
            raise ValueError("Source certificates require finite nonnegative float64 [x] bounds.")
    # Check residual representability independently of the bound's intermediate
    # rational sums, which are allowed to exceed the largest float.
    for name in SIGNALS:
        if not np.isfinite(_subtract(a[name], b[name])).all():
            raise ValueError("A causal comparison difference overflowed.")
    maxima = {name: (np.max(np.abs(a[name]), axis=1), np.max(np.abs(b[name]), axis=1)) for name in SIGNALS}
    result = {key: np.empty(nx, dtype=np.float64) for key in (
        "source_sum", "complex_arithmetic", "complex_total", "envelope_arithmetic", "envelope_total")}
    for x in range(nx):
        source = upward_nonnegative(Fraction(float(eps_a[x]))+Fraction(float(eps_b[x])))
        rho = {name: UNIT_ROUNDOFF*(Fraction(float(pair[0][x]))+Fraction(float(pair[1][x])))+MIN_SUBNORMAL
               for name, pair in maxima.items()}
        arithmetic = upward_nonnegative(rho["rf"]+rho["imaginary"])
        envelope = upward_nonnegative(rho["envelope"])
        result["source_sum"][x] = source
        result["complex_arithmetic"][x] = arithmetic
        result["envelope_arithmetic"][x] = envelope
        result["complex_total"][x] = upward_nonnegative(Fraction(source)+Fraction(arithmetic))
        result["envelope_total"][x] = upward_nonnegative(Fraction(source)+Fraction(envelope))
    check_arithmetic_environment()
    return result


def _sum_units(values):
    """Exact streamed sum in units of 2^-1074, avoiding cancellation/overflow.

    The integers occupy bounded binary64 exponent range plus log(sample count),
    not an unbounded retained list of source samples.
    """
    signed = absolute = 0
    for item in values.flat:
        numerator, denominator = float(item).as_integer_ratio()
        integer = numerator << (1075-denominator.bit_length())
        signed += integer
        absolute += abs(integer)
    return signed, absolute


def _squares(values):
    scale = float(np.max(np.abs(values)))
    if scale == 0:
        return 0., 0.
    with np.errstate(under="ignore"):
        normalized = values/scale
        total = math.fsum(np.square(normalized).flat)
    return scale, total


def _merge_squares(parts):
    scale = max((part[0] for part in parts), default=0.)
    if scale == 0:
        return 0., 0.
    return scale, math.fsum(total*(local/scale)**2 for local, total in parts)


def _finite(value):
    if not math.isfinite(value):
        raise ValueError("A comparison diagnostic cannot be represented finitely.")
    return value


def _multiply(a, b):
    if a == 0 or b == 0:
        return 0.
    am, ae = math.frexp(a)
    bm, be = math.frexp(b)
    try:
        return _finite(math.ldexp(am*bm, ae+be))
    except OverflowError as exc:
        raise ValueError("A comparison diagnostic cannot be represented finitely.") from exc


def _norm(parts, divisor=1):
    scale, total = _merge_squares(parts)
    return _multiply(scale, math.sqrt(total/divisor))


def _relative(parts, reference):
    scale, total = _merge_squares(parts)
    base, base_total = _merge_squares(reference)
    if base == 0:
        return None, "Reference L2 norm is zero; relative error is undefined."
    if scale == 0:
        return 0., None
    sm, se = math.frexp(scale)
    bm, be = math.frexp(base)
    try:
        value = math.ldexp((sm/bm)*math.sqrt(total/base_total), se-be)
    except OverflowError as exc:
        raise ValueError("Relative comparison norm cannot be represented finitely.") from exc
    return _finite(value), None


def _selection(nt, selection):
    if selection is None:
        return 0, nt
    if isinstance(selection, slice):
        if selection.step not in (None, 1):
            raise ValueError("Metric time slices require unit step.")
        lo, hi = (0 if selection.start is None else selection.start,
                  nt if selection.stop is None else selection.stop)
    elif isinstance(selection, tuple) and len(selection) == 2:
        lo, hi = selection
    else:
        raise ValueError("Metric time selection requires a slice or (start,stop) pair.")
    if type(lo) is not int or type(hi) is not int or not 0 <= lo < hi <= nt:
        raise ValueError("A metric time slice must contain recorded samples.")
    return lo, hi


def _location(index, coordinates, **values):
    row, x, time = index
    return {"y_index": row, "x_index": x, "time_index": time,
            "y_mm": float(coordinates["y_mm"][row]), "x_mm": float(coordinates["x_mm"][x]),
            "time_us": float(coordinates["time_us"][time]), **values}


class Metrics:
    """At most 64 row summaries; sources and residual cubes are never retained."""
    def __init__(self):
        self.rows = {}
        self.shape = None
        self.selection = None

    def add(self, a, b, difference, row_index, time_slice=None):
        check_arithmetic_environment()
        nx, nt = _difference(a, b, difference)
        if type(row_index) is not int or not 0 <= row_index < MAX_ROWS or row_index in self.rows:
            raise ValueError("Metrics require each bounded Y row exactly once.")
        lo, hi = _selection(nt, time_slice)
        if self.shape is not None and (self.shape != (nx, nt) or self.selection != (lo, hi)):
            raise ValueError("Metric row shapes and time selections must remain identical.")
        record = {"count": nx*(hi-lo), "components": {}}
        for name in SIGNALS:
            d = difference[name][:, lo:hi]
            signed, absolute = _sum_units(d)
            flat = int(np.argmax(np.abs(d)))
            x, t = np.unravel_index(flat, d.shape)
            record["components"][name] = {
                "sum": signed, "absolute": absolute, "squares": _squares(d),
                "reference": _squares(a[name][:, lo:hi]), "max": float(abs(d.flat[flat])),
                "location": (row_index, int(x), lo+int(t)), "signed_max": float(d.flat[flat])}
        with np.errstate(over="ignore", under="ignore", invalid="ignore"):
            magnitude = np.hypot(difference["rf"][:, lo:hi], difference["imaginary"][:, lo:hi])
        if not np.isfinite(magnitude).all():
            raise ValueError("The diagnostic complex residual magnitude is not finite.")
        flat = int(np.argmax(magnitude))
        x, t = np.unravel_index(flat, magnitude.shape)
        record["complex_max"] = {"max": float(magnitude.flat[flat]),
            "location": (row_index, int(x), lo+int(t)),
            "real": float(difference["rf"][x, lo+t]), "imaginary": float(difference["imaginary"][x, lo+t])}
        check_arithmetic_environment()
        self.shape, self.selection = (nx, nt), (lo, hi)
        self.rows[row_index] = record

    def result(self, coordinates):
        check_arithmetic_environment()
        if not self.rows:
            raise ValueError("No samples were added to the comparison metrics.")
        if not isinstance(coordinates, dict):
            raise ValueError("Metrics require actual X/Y/time coordinate arrays.")
        for key, required, maximum in (("x_mm", self.shape[0], MAX_X),
                                      ("y_mm", max(self.rows)+1, MAX_ROWS),
                                      ("time_us", self.shape[1], MAX_TIME)):
            values = coordinates.get(key)
            if (type(values) is not np.ndarray or values.dtype != np.dtype(np.float64) or values.ndim != 1 or
                    not required <= len(values) <= maximum or (key != "y_mm" and len(values) != required) or
                    not np.isfinite(values).all() or np.any(np.diff(values) <= 0)):
                raise ValueError("Metric coordinates must be finite ordered float64 vectors matching the sampled rows.")
        records = [self.rows[index] for index in sorted(self.rows)]
        count = sum(row["count"] for row in records)
        result = {"sample_count": count, "diagnostic_scope": DIAGNOSTIC_SCOPE}
        for name in SIGNALS:
            parts = [row["components"][name] for row in records]
            squares, reference = [p["squares"] for p in parts], [p["reference"] for p in parts]
            relative, reason = _relative(squares, reference)
            largest = min(parts, key=lambda p: (-p["max"], p["location"]))
            result[name] = {"sample_count": count,
                "bias": float(Fraction(sum(p["sum"] for p in parts), count << 1074)),
                "mae": float(Fraction(sum(p["absolute"] for p in parts), count << 1074)),
                "rmse": _norm(squares, count), "reference_l2": _norm(reference),
                "relative_l2": relative, "relative_l2_reason": reason,
                "max_absolute_difference": largest["max"],
                "max_location": _location(largest["location"], coordinates, signed_difference=largest["signed_max"])}
        squares = [row["components"][key]["squares"] for row in records for key in ("rf", "imaginary")]
        reference = [row["components"][key]["reference"] for row in records for key in ("rf", "imaginary")]
        relative, reason = _relative(squares, reference)
        largest = min((row["complex_max"] for row in records), key=lambda p: (-p["max"], p["location"]))
        result["complex"] = {"sample_count": count, "bias_real": result["rf"]["bias"],
            "bias_imaginary": result["imaginary"]["bias"], "rmse": _norm(squares, count),
            "reference_l2": _norm(reference), "relative_l2": relative, "relative_l2_reason": reason,
            "max_absolute_difference": largest["max"], "max_location": _location(largest["location"], coordinates,
                signed_real_difference=largest["real"], signed_imaginary_difference=largest["imaginary"])}
        check_arithmetic_environment()
        return result


def _rms_columns(values):
    scale = np.max(np.abs(values), axis=1)
    normalized = np.zeros_like(values)
    with np.errstate(under="ignore"):
        np.divide(values, scale[:, None], out=normalized, where=scale[:, None] != 0)
        return scale*np.sqrt(np.mean(normalized*normalized, axis=1))


def gate_products(a, b, difference, lo, hi):
    """Ordinary statistic(B)-statistic(A), never statistic(B-A)."""
    check_arithmetic_environment()
    _, nt = _difference(a, b, difference)
    lo, hi = _selection(nt, (lo, hi))
    result = {}
    for name in ("peak_envelope", "rms_rf"):
        if name == "peak_envelope":
            left, right = (v["envelope"][:, lo:hi].max(axis=1) for v in (a, b))
        else:
            left, right = (_rms_columns(v["rf"][:, lo:hi]) for v in (a, b))
        delta = _subtract(left, right)
        if not np.isfinite(delta).all():
            raise ValueError("A comparison gate difference is not finite.")
        result[name] = {"reference": left, "candidate": right, "difference": delta,
            "difference_definition": "statistic(candidate B) minus statistic(reference A); not the statistic of the residual",
            "diagnostic_scope": DIAGNOSTIC_SCOPE}
    check_arithmetic_environment()
    return result
