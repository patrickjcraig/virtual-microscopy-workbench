"""Bounded scalar plane-wave layers and a certified single-slab pulse series.

Pressure is referred to the incident wave at the first interface. Exterior
media are semi-infinite. Analysis uses exp(-i*omega*t); positive propagation
delay therefore contributes exp(-i*omega*tau). Loss is an explicitly supplied
frequency-independent pressure dB/mm coefficient, not a material model.

No focus, beam model, geometry constructor, FFT or instrument calibration is
used here. The multilayer frequency samples are not a certified RF waveform.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil, exp, isfinite, log, log1p, pi, sqrt
from numbers import Real

import numpy as np

MODEL_VERSION = "layered-scalar-pressure-0.11.0"
MAX_LAYERS = 256
MAX_FREQUENCY_SAMPLES = 16385
MAX_TIME_SAMPLES = 16384
MAX_ECHOES = 100000
MAX_RF_WORK = 50_000_000
MAX_WORKSPACE_BYTES = 512*1024**2
MAX_PHASE_RADIANS = 2**20
PASSIVITY_TOLERANCE = 1e-10


@dataclass
class LayeredResponse:
    reflection: np.ndarray
    transmission: np.ndarray
    primary_reflection: np.ndarray
    direct_transmission: np.ndarray
    reflectance: np.ndarray
    transmittance: np.ndarray
    absorptance: np.ndarray
    diagnostics: dict


@dataclass
class SlabImpulseSeries:
    times_us: np.ndarray
    amplitudes: np.ndarray
    diagnostics: dict


@dataclass
class SlabRFResponse:
    time_us: np.ndarray
    rf: np.ndarray
    envelope: np.ndarray
    primary_rf: np.ndarray
    primary_envelope: np.ndarray
    diagnostics: dict
    series: SlabImpulseSeries


def _scalar(value, name, *, positive=False, nonnegative=False):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite real number.")
    result = float(value)
    if not isfinite(result) or (positive and result <= 0) or (nonnegative and result < 0):
        raise ValueError(f"{name} must be finite" + (" and positive." if positive else " and nonnegative." if nonnegative else "."))
    return result


def _vector(values, name, limit, *, empty=False):
    if not isinstance(values, (list, tuple, np.ndarray)) or getattr(values, "ndim", 1) != 1:
        raise ValueError(f"{name} must be a one-dimensional real vector.")
    if len(values) > limit or (not empty and len(values) == 0):
        raise ValueError(f"{name} must contain {'zero' if empty else 'one'} to {limit} values.")
    # Reject nested objects before np.asarray could expand an unbounded shape.
    if any(isinstance(v, (bool, np.bool_)) or not isinstance(v, Real) for v in values):
        raise ValueError(f"{name} must contain finite real numbers.")
    result = np.asarray(values, dtype=np.float64).copy()
    if not np.isfinite(result).all():
        raise ValueError(f"{name} must contain finite real numbers.")
    return result


def _stack(thickness_mm, impedances_mrayl, sound_speeds_m_s, pressure_loss_db_mm):
    d = _vector(thickness_mm, "thickness_mm", MAX_LAYERS, empty=True)
    z = _vector(impedances_mrayl, "impedances_mrayl", MAX_LAYERS+2)
    c = _vector(sound_speeds_m_s, "sound_speeds_m_s", MAX_LAYERS, empty=True)
    loss = np.zeros(len(d)) if pressure_loss_db_mm is None else _vector(
        pressure_loss_db_mm, "pressure_loss_db_mm", MAX_LAYERS, empty=True)
    if len(z) != len(d)+2 or len(c) != len(d) or len(loss) != len(d):
        raise ValueError("A stack requires N thicknesses/speeds/losses and N+2 impedances, including both exterior media.")
    if np.any(d < 0) or np.any(z <= 0) or np.any(c <= 0) or np.any(loss < 0):
        raise ValueError("Layer thickness/loss must be nonnegative; impedances and speeds must be positive.")
    original_count = len(d)
    # Exactly zero thickness contains no medium or propagation. Remove it
    # algebraically, including for the explicitly defined primary baseline.
    keep = d > 0
    z = np.concatenate((z[:1], z[1:-1][keep], z[-1:]))
    d, c, loss = d[keep], c[keep], loss[keep]
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        delay = d/(c/1000.)
        nepers = loss*d*(log(10)/20)
    if not np.isfinite(delay).all() or not np.isfinite(nepers).all() or np.any(delay <= 0):
        raise ValueError("Layer delay or attenuation is not representable in float64.")
    # Scale each pair to avoid overflowing Z_i+Z_j. Reject contrasts that
    # round a finite positive-impedance interface into a perfect reflector.
    scale = np.maximum(z[:-1], z[1:])
    left, right = z[:-1]/scale, z[1:]/scale
    r = (right-left)/(right+left)
    t = 2*right/(right+left)
    reverse_t = 2*left/(right+left)
    if np.any(np.abs(r) >= 1) or np.any(t <= 0) or np.any(reverse_t <= 0):
        raise ValueError("Impedance contrast exceeds the supported float64 interface conditioning.")
    return d, z, delay, nepers, r, t, reverse_t, original_count


def layered_response(thickness_mm, impedances_mrayl, sound_speeds_m_s,
                     frequency_mhz, pressure_loss_db_mm=None):
    """Return stable pressure R/T and primary/direct baselines at exact frequencies.

    Frequencies may be unordered or negative; their input order is preserved.
    T is referred to the outgoing wave at the final interface, so propagation
    through each finite layer is included exactly once. Energy uses Z_in/Z_out.
    """
    f = _vector(frequency_mhz, "frequency_mhz", MAX_FREQUENCY_SAMPLES)
    d, z, delay, nepers, r, t, reverse_t, original_count = _stack(
        thickness_mm, impedances_mrayl, sound_speeds_m_s, pressure_loss_db_mm)
    n, nf = len(d), len(f)
    peak = 16*1024**2 + nf*256 + (original_count+2)*128
    if peak > MAX_WORKSPACE_BYTES:
        raise ValueError("Layered frequency response exceeds the 512 MiB workspace.")
    total_delay = float(np.sum(delay))
    phase_bound = 2*pi*float(np.max(np.abs(f)))*total_delay
    if not isfinite(total_delay) or not isfinite(phase_bound) or phase_bound > MAX_PHASE_RADIANS:
        raise ValueError("Accumulated propagation phase exceeds the float64 phase-accuracy bound.")
    reflection = np.full(nf, complex(r[-1]), dtype=np.complex128)
    transmission = np.full(nf, complex(t[-1]), dtype=np.complex128)
    minimum_denominator = 1.
    # Backward scattering recursion uses only decaying propagation factors.
    for j in range(n-1, -1, -1):
        p = np.exp(-nepers[j]-2j*pi*f*delay[j])
        q = reflection*p*p
        denominator = 1+r[j]*q
        smallest = float(np.min(np.abs(denominator)))
        minimum_denominator = min(minimum_denominator, smallest)
        if smallest <= 64*np.finfo(np.float64).eps:
            raise ValueError("Layered resonance is too ill-conditioned in float64; no denominator clipping is applied.")
        transmission = t[j]*p*transmission/denominator
        reflection = (r[j]+q)/denominator
    primary = np.full(nf, complex(r[0]), dtype=np.complex128)
    direct = np.full(nf, complex(t[0]), dtype=np.complex128)
    upstream = np.ones(nf, dtype=np.complex128)
    for j in range(n):
        p = np.exp(-nepers[j]-2j*pi*f*delay[j])
        upstream *= t[j]*reverse_t[j]*p*p
        primary += upstream*r[j+1]
        direct *= p*t[j+1]
    # Normalize transmitted pressure before squaring, avoiding a potentially
    # overflowing impedance ratio when impedances share extreme units/scales.
    flux_amplitude = transmission*(sqrt(float(z[0]))/sqrt(float(z[-1])))
    reflected_energy = np.abs(reflection)**2
    transmitted_energy = np.abs(flux_amplitude)**2
    absorption = 1-reflected_energy-transmitted_energy
    if (not all(np.isfinite(a).all() for a in (reflection, transmission, primary, direct, absorption)) or
            float(np.min(absorption)) < -PASSIVITY_TOLERANCE):
        raise ValueError("Layered response failed finite/passive float64 validation.")
    if not np.any(nepers) and float(np.max(np.abs(absorption))) > PASSIVITY_TOLERANCE:
        raise ValueError("Lossless layered response failed the impedance-weighted conservation tolerance.")
    return LayeredResponse(reflection, transmission, primary, direct,
        reflected_energy, transmitted_energy, absorption, {
            "model_version": MODEL_VERSION, "input_layer_count": original_count,
            "effective_layer_count": n, "frequency_samples": nf,
            "layer_frequency_work": n*nf, "estimated_peak_bytes": peak,
            "minimum_recursion_denominator": minimum_denominator,
            "maximum_accumulated_phase_rad": phase_bound,
            "one_way_delay_us": total_delay, "passivity_tolerance": PASSIVITY_TOLERANCE,
            "maximum_energy_residual": float(np.max(np.abs(absorption))) if not np.any(nepers) else None,
            "minimum_absorptance": float(np.min(absorption)),
            "loss_definition": "Explicit frequency-independent pressure dB/mm; alpha=ln(10)*loss/20. No dispersion or material law is inferred.",
            "boundary_definition": "Semi-infinite incident and terminal media; R at the first interface, T at the final interface; no exterior return path.",
            "fourier_definition": "Analysis exp(-i*omega*t), delay exp(-i*omega*tau). Frequencies are MHz, delays us.",
            "primary_definition": "Each interface reflected once with reciprocal upstream pressure transmissions and round-trip propagation; exactly zero-thickness layers are removed.",
            "direct_definition": "Single forward passage through all interfaces and finite layers, excluding all internal returns.",
            "energy_definition": "Reflectance=|R|²; transmittance=(Z_incident/Z_terminal)|T|²; absorptance=1-reflectance-transmittance. Numerical residuals are retained without clipping.",
            "limitations": ["Scalar normal-incidence longitudinal plane waves; no shear, oblique propagation, focus, beam or calibration.",
                "Independent frequency samples do not certify spectral interpolation, unresolved resonances, or a time waveform. No multilayer IFFT is supplied."]})


def _slab(thickness_mm, impedances_mrayl, sound_speed_m_s, pressure_loss_db_mm):
    thickness = _scalar(thickness_mm, "thickness_mm", nonnegative=True)
    speed = _scalar(sound_speed_m_s, "sound_speed_m_s", positive=True)
    loss = _scalar(pressure_loss_db_mm, "pressure_loss_db_mm", nonnegative=True)
    stack = _stack([thickness], impedances_mrayl, [speed], [loss])
    d, z, delay, nepers, r, t, reverse_t, _ = stack
    if not len(d):
        return float(r[0]), 0., 0., 0., True
    decay2 = exp(-2*float(nepers[0]))
    first = float(t[0]*reverse_t[0]*r[1])*decay2
    q = float(-r[0]*r[1])*decay2
    spacing = 2*float(delay[0])
    if not isfinite(spacing) or spacing <= 0 or not abs(q) < 1:
        raise ValueError("Slab return spacing or contraction is not representable.")
    return float(r[0]), first, q, spacing, False


def slab_impulse_series(thickness_mm, impedances_mrayl, sound_speed_m_s,
                        pressure_loss_db_mm=0., *, absolute_tolerance=1e-8,
                        max_echoes=MAX_ECHOES):
    """Causal reflected slab impulses with a global omitted-amplitude L1 bound.

    max_echoes includes the front return. For N retained internal returns the
    omitted absolute-amplitude sum is |A1|*|q|**N/(1-|q|). This bounds waveform
    error for any pulse whose absolute amplitude never exceeds one; it is not
    a statistical confidence interval or a bound on floating-point roundoff.
    """
    tolerance = _scalar(absolute_tolerance, "absolute_tolerance", positive=True)
    if type(max_echoes) is not int or not 1 <= max_echoes <= MAX_ECHOES:
        raise ValueError(f"max_echoes must be an integer between 1 and {MAX_ECHOES}.")
    front, first, q, spacing, collapsed = _slab(thickness_mm, impedances_mrayl,
                                              sound_speed_m_s, pressure_loss_db_mm)
    a, contraction = abs(first), abs(q)
    total_internal = a/(1-contraction)
    if not isfinite(total_internal):
        raise ValueError("Slab absolute-amplitude tail is not representable.")
    if total_internal <= tolerance:
        count = 0
    elif contraction == 0:
        count = 1
    else:
        count = max(0, ceil((log(tolerance)-log(a)+log1p(-contraction))/log(contraction)))
    def tail(n):
        if a == 0 or (contraction == 0 and n > 0):
            return 0.
        if n == 0:
            return total_internal
        return exp(log(a)+n*log(contraction)-log1p(-contraction))
    # Guard the ceil calculation at a floating-point integer boundary.
    while tail(count) > tolerance:
        count += 1
    if count+1 > max_echoes:
        raise ValueError(f"The global slab tail certificate requires {count+1} echoes, exceeding max_echoes={max_echoes}; the tolerance is not relaxed.")
    peak = 16*1024**2+(count+1)*48
    if peak > MAX_WORKSPACE_BYTES or not isfinite(count*spacing):
        raise ValueError("Certified slab series exceeds its workspace or representable time bound.")
    orders = np.arange(count+1, dtype=np.float64)
    times = orders*spacing
    amplitudes = np.empty(count+1, dtype=np.float64)
    amplitudes[0] = front
    if count:
        amplitudes[1:] = first*np.power(q, orders[:-1])
    if len(times) > 1 and not np.all(np.diff(times) > 0):
        raise ValueError("Slab return times are not strictly increasing in float64.")
    return SlabImpulseSeries(times, amplitudes, {
        "model_version": MODEL_VERSION, "echo_count": count+1,
        "internal_echo_count": count, "front_amplitude": front,
        "first_internal_amplitude": first, "signed_round_trip_ratio": q,
        "return_spacing_us": spacing, "zero_thickness_collapsed": collapsed,
        "absolute_tolerance": tolerance, "omitted_amplitude_l1_bound": tail(count),
        "complete_amplitude_l1_bound": abs(front)+total_internal,
        "estimated_peak_bytes": peak,
        "certificate_definition": "After N internal returns, omitted absolute amplitude <= |A1|*|q|^N/(1-|q|); roundoff is separate. No independent-path floor is used.",
        "series_definition": "Front r01 at t=0; internal return n>=1 has A1*q^(n-1) at n*2d/c; exterior media do not reflect outgoing waves."})


def slab_rf_response(thickness_mm, impedances_mrayl, sound_speed_m_s,
                     time_us, center_frequency_mhz, fractional_bandwidth=.5,
                     pressure_loss_db_mm=0., *, absolute_tolerance=1e-8,
                     max_echoes=MAX_ECHOES, surface_time_us=0.):
    """Direct coherent slab pulse synthesis on actual centers, with no IFFT.

    The declared complex Gaussian pulse is zero outside four envelope sigma.
    The supplied time origin shifts all impulses; it does not create an
    exterior cavity or add attenuation. No measured transducer is implied.
    """
    time = _vector(time_us, "time_us", MAX_TIME_SAMPLES)
    frequency = _scalar(center_frequency_mhz, "center_frequency_mhz", positive=True)
    bandwidth = _scalar(fractional_bandwidth, "fractional_bandwidth", positive=True)
    surface = _scalar(surface_time_us, "surface_time_us", nonnegative=True)
    if len(time) < 2 or np.any(time < 0) or not np.all(np.diff(time) > 0):
        raise ValueError("RF time centers must be nonnegative, finite and strictly increasing with at least two samples.")
    if not .2 <= bandwidth <= 1:
        raise ValueError("fractional_bandwidth must be between 0.2 and 1.")
    if np.max(np.diff(time))*frequency > (1/8)*(1+1e-10):
        raise ValueError("RF sampling must provide at least eight samples per carrier period.")
    sigma = sqrt(2*log(2))/(pi*bandwidth*frequency)
    half = 4*sigma
    if not isfinite(sigma) or sigma <= 0 or not isfinite(half):
        raise ValueError("Pulse duration is not representable in float64.")
    series = slab_impulse_series(thickness_mm, impedances_mrayl, sound_speed_m_s,
        pressure_loss_db_mm, absolute_tolerance=absolute_tolerance, max_echoes=max_echoes)
    centers = series.times_us+surface
    if not np.isfinite(centers).all() or (len(centers) > 1 and not np.all(np.diff(centers) > 0)):
        raise ValueError("Shifted slab return times are not representable in float64.")
    def bounds(event_centers):
        # A cancellation in center-half can move several closely spaced time
        # centers across the search bound. Expand by a conservative float64
        # arithmetic margin, then enforce support using the actual dt below.
        event_centers = np.asarray(event_centers)
        margin = 8*np.finfo(np.float64).eps*np.maximum(np.abs(event_centers), half)
        with np.errstate(over="ignore"):
            low, high = event_centers-half-margin, event_centers+half+margin
        starts = np.maximum(0, np.searchsorted(time, low, side="left")-1)
        stops = np.minimum(len(time), np.searchsorted(time, high, side="right")+1)
        return starts, stops
    lo, hi = bounds(centers)
    front = series.diagnostics["front_amplitude"]
    first = series.diagnostics["first_internal_amplitude"]
    spacing = series.diagnostics["return_spacing_us"]
    primary_centers = [surface] if series.diagnostics["zero_thickness_collapsed"] else [surface, surface+spacing]
    primary_amplitudes = [front] if len(primary_centers) == 1 else [front, first]
    primary_lo, primary_hi = bounds(primary_centers)
    work = int(np.sum(hi-lo, dtype=np.int64)+np.sum(primary_hi-primary_lo, dtype=np.int64))
    peak = series.diagnostics["estimated_peak_bytes"]+len(time)*160+len(centers)*40
    if work > MAX_RF_WORK or peak > MAX_WORKSPACE_BYTES:
        raise ValueError("Certified slab RF exceeds its 50 million pulse-sample work or 512 MiB workspace bound.")
    def synthesize(event_centers, amplitudes, starts, stops):
        signal = np.zeros(len(time), dtype=np.complex128)
        contributing = 0
        for center, amplitude, start, stop in zip(event_centers, amplitudes, starts, stops):
            if stop <= start or amplitude == 0:
                continue
            delta = time[start:stop]-center
            valid = np.abs(delta) <= half
            if not np.any(valid):
                continue
            contributing += 1
            pulse = np.exp(-.5*(delta/sigma)**2+2j*pi*frequency*delta)
            # Endpoint addition/subtraction can round in opposite directions;
            # enforce the declared compact support on the actual evaluated dt.
            pulse[~valid] = 0
            signal[start:stop] += amplitude*pulse
        return signal, contributing
    signal, contributing = synthesize(centers, series.amplitudes, lo, hi)
    primary, primary_contributing = synthesize(primary_centers, primary_amplitudes, primary_lo, primary_hi)
    if not np.isfinite(signal).all() or not np.isfinite(primary).all():
        raise ValueError("Slab RF synthesis produced a nonfinite field.")
    diagnostic = dict(series.diagnostics, rf_time_samples=len(time), pulse_sample_work=work,
        estimated_peak_bytes=peak, center_frequency_mhz=frequency, fractional_bandwidth=bandwidth,
        surface_time_us=surface, pulse_sigma_us=sigma, pulse_support_half_width_us=half,
        pulse_contributing_echo_count=contributing, primary_contributing_echo_count=primary_contributing,
        omitted_rf_peak_bound=series.diagnostics["omitted_amplitude_l1_bound"],
        infinite_gaussian_truncation_peak_bound=exp(-8)*series.diagnostics["complete_amplitude_l1_bound"],
        pulse_definition="Unit-peak exp(-0.5*(dt/sigma)^2)*exp(+i*2*pi*f*dt), zero for |dt|>4*sigma; directly evaluated at actual saved time centers.",
        rf_definition="Real part of coherent complex pressure sum; envelope is magnitude after summation. Primary includes the front and first internal return only.",
        certificate_scope="The global omitted-echo L1 bound also bounds RF/envelope error for the declared compact pulse, excluding floating-point roundoff. Pulse truncation relative to an infinite Gaussian is reported separately.",
        time_definition="Causal impulses referenced to the first interface plus the explicit surface_time_us shift; Gaussian pre-peak support is not an earlier physical interface.")
    return SlabRFResponse(time.copy(), signal.real.copy(), np.abs(signal),
                         primary.real.copy(), np.abs(primary), diagnostic, series)
