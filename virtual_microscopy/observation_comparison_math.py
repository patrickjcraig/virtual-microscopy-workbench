"""Compare saved coherent observations using their two distinct certificates.

The binary64 subtraction and ordinary diagnostics retain the frozen causal
comparison arithmetic contract. Complex pressure and independently stored
magnitude have different source certificates, so their source sums must never
be interchanged. This module performs no source I/O or forward computation.
"""
from fractions import Fraction

import numpy as np

from . import causal_comparison_math as _causal


SIGNALS = _causal.SIGNALS
check_arithmetic_environment = _causal.check_arithmetic_environment
checked_differences = _causal.checked_differences
Metrics = _causal.Metrics
gate_products = _causal.gate_products
BOUNDS = ("complex_source_sum", "magnitude_source_sum", "complex_arithmetic",
          "complex_total", "magnitude_arithmetic", "magnitude_total")
BOUND_DEFINITION = (
    "Full-record per-column bounds on saved B-minus-A residuals relative to the "
    "two ideal declared observations. Complex pressure uses each source's "
    "complex_total; separately saved magnitude uses each source's magnitude_total. "
    "Source sums and subtraction bounds are rounded outward using exact rational "
    "arithmetic, then totals enclose sums of the published components. Gate "
    "reductions, residual magnitudes and summary statistics are ordinary diagnostics."
)


def column_bounds(a, b, complex_a, complex_b, magnitude_a, magnitude_b):
    """Return six outward float64 [x] maps for complete [x,time] rows.

    Obtain the unchanged legacy subtraction enclosures with zero source error,
    then compose only the two required totals. Computing both legacy totals with
    either source certificate would unnecessarily form an unrelated total and
    could overflow even when every required result is representable.
    """
    check_arithmetic_environment()
    if (not isinstance(a, dict) or type(a.get("rf")) is not np.ndarray or
            a["rf"].ndim != 2 or not 1 <= a["rf"].shape[0] <= _causal.MAX_X):
        raise ValueError("Observation comparison requires bounded float64 [x,time] signal rows.")
    nx = a["rf"].shape[0]
    for value in (complex_a, complex_b, magnitude_a, magnitude_b):
        if (type(value) is not np.ndarray or value.dtype != np.dtype(np.float64) or
                value.shape != (nx,) or not np.isfinite(value).all() or np.any(value < 0)):
            raise ValueError("Source certificates require finite nonnegative float64 [x] bounds.")
    zero = np.zeros(nx, dtype=np.float64)
    arithmetic = _causal.column_bounds(a, b, zero, zero)
    result = {key: np.empty(nx, dtype=np.float64) for key in BOUNDS}
    result["complex_arithmetic"][:] = arithmetic["complex_arithmetic"]
    result["magnitude_arithmetic"][:] = arithmetic["envelope_arithmetic"]
    for x in range(nx):
        for prefix, left, right in (("complex", complex_a, complex_b),
                                    ("magnitude", magnitude_a, magnitude_b)):
            source = _causal.upward_nonnegative(Fraction(float(left[x]))+Fraction(float(right[x])))
            result[prefix+"_source_sum"][x] = source
            result[prefix+"_total"][x] = _causal.upward_nonnegative(
                Fraction(source)+Fraction(float(result[prefix+"_arithmetic"][x])))
    check_arithmetic_environment()
    return result
