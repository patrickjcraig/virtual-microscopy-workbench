"""Bounded causal gamma-pulse reflection from scalar layers using Arb balls.

Carrier-centred Laplace samples approximate the causal complex pressure response.
Analytic future-alias and omitted-frequency bounds are combined with enclosures
of all numerical operations, including conversion to the actual saved doubles.
This separate excitation does not replace the existing compact Gaussian pulse.
"""
from math import ceil, floor, isfinite, nextafter, inf, factorial
from numbers import Real
from threading import RLock
from time import perf_counter

from flint import arb, acb, acb_poly, ctx

from .layered_schemas import LayeredStack

MODEL_VERSION = "layered-causal-gamma-arb-0.12.0"
MAX_FREQUENCY_TERMS = 16385
MAX_TIME_SAMPLES = 2049
MAX_LAYER_FREQUENCY_WORK = 5_000_000
MAX_INVERSE_WORK = 25_000_000
MAX_PEAK_BYTES = 96*1024**2
HORNER_BLOCK_SIZE = 32
_ARITHMETIC_LOCK = RLock()


def _upper_float(value):
    """Outward bound including the conversion from an Arb bound to binary64."""
    endpoint = value.upper()
    result = nextafter(float(endpoint), inf)
    if not isfinite(result) or not arb(result) >= endpoint:
        raise ValueError("A finite outward error bound cannot be encoded in binary64.")
    return result


def _lower_float(value):
    endpoint = value.lower()
    result = nextafter(float(endpoint), -inf)
    if endpoint >= 0 and result < 0:
        result = 0.
    if not isfinite(result) or not arb(result) <= endpoint:
        raise ValueError("A finite lower conditioning bound cannot be encoded in binary64.")
    return result


def _number(value, name, lower, upper):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite real number.")
    result = float(value)
    if not isfinite(result) or not lower <= result <= upper:
        raise ValueError(f"{name} must lie between {lower} and {upper}.")
    return result


def _validate(stack, time_us, settings):
    stack = LayeredStack.model_validate(stack).model_dump(mode="json")
    if not isinstance(time_us, (list, tuple)) or not 2 <= len(time_us) <= MAX_TIME_SAMPLES:
        raise ValueError("Causal layered RF requires 2 to 2,049 actual recorded time centers.")
    time = [_number(t, "Recorded time (us)", 0, 12) for t in time_us]
    if any(b <= a for a, b in zip(time, time[1:])):
        raise ValueError("Recorded times must be strictly increasing.")
    if not isinstance(settings, dict):
        raise ValueError("Causal gamma settings must be an explicit dictionary.")
    accepted = {"center_frequency_mhz", "fractional_bandwidth", "absolute_tolerance", "surface_standoff_mm",
                "gamma_order", "precision_bits", "sample_rate_mhz", "record_start_us", "record_duration_us"}
    if set(settings)-accepted:
        raise ValueError("Unsupported causal gamma settings were supplied.")
    options = {key: _number(settings.get(key, default), key, low, high) for key, default, low, high in (
        ("center_frequency_mhz", 50., 10, 150), ("fractional_bandwidth", .5, .2, 1),
        ("absolute_tolerance", 1e-7, 1e-12, 1e-3), ("surface_standoff_mm", 0., 0, 5))}
    order, precision = settings.get("gamma_order", 12), settings.get("precision_bits", 128)
    if type(order) is not int or not 4 <= order <= 24:
        raise ValueError("Gamma order must be an integer from 4 to 24.")
    if type(precision) is not int or precision not in (64, 96, 128, 192, 256):
        raise ValueError("Arb precision must be 64, 96, 128, 192 or 256 bits.")
    if max(b-a for a,b in zip(time,time[1:]))*options["center_frequency_mhz"] > (1/8)*(1+1e-10):
        raise ValueError("Causal layered RF requires at least eight samples per carrier period.")
    recording = {"sample_rate_mhz", "record_start_us", "record_duration_us"}
    if recording.intersection(settings):
        if not recording.issubset(settings):
            raise ValueError("Recording metadata requires start, duration and sample rate together.")
        rate = _number(settings["sample_rate_mhz"], "sample_rate_mhz", 8*options["center_frequency_mhz"], 2400)
        start = _number(settings["record_start_us"], "record_start_us", 0, 12)
        duration = _number(settings["record_duration_us"], "record_duration_us", .05, 12)
        expected = floor(duration*rate+1e-9)+1
        if start+duration > 12+1e-12 or expected != len(time) or any(t != start+i/rate for i,t in enumerate(time)):
            raise ValueError("The supplied time centers do not match their explicit recording metadata.")
    options.update(gamma_order=order, precision_bits=precision)
    return stack, time, options


def _pulse_parameters(options):
    order = options["gamma_order"]
    pi, frequency = arb.pi(), arb(options["center_frequency_mhz"])
    omega = 2*pi*frequency
    # FWHM of the complex-pulse amplitude spectrum, divided by its carrier.
    rate = pi*frequency*arb(options["fractional_bandwidth"])/(arb(2)**(arb(2)/(order+1))-1).sqrt()
    peak = order/rate
    normalization = (rate*arb(1).exp()/order)**order
    numerator = normalization*factorial(order)
    fourier_l1 = numerator*(arb(order)/2).gamma()/(2*pi.sqrt()*((arb(order)+1)/2).gamma()*rate**order)
    return omega, rate, peak, numerator, fourier_l1


def _plan(stack, time, options):
    omega, rate, peak, numerator, fourier_l1 = _pulse_parameters(options)
    order, tolerance, end = options["gamma_order"], arb(options["absolute_tolerance"]), arb(time[-1])
    period = arb(max(1., 4*time[-1]))
    if not period > end:
        raise ValueError("The causal inverse requires every recorded center inside its first period.")
    damping = arb(_upper_float((1+4*fourier_l1/tolerance).log()/period))
    alias = fourier_l1/((damping*period).exp()-1)
    delta = 2*arb.pi()/period
    needed = ((damping*end).exp()*numerator/(arb.pi()*order*(tolerance/4)))**(arb(1)/order)/delta
    if not needed < MAX_FREQUENCY_TERMS:
        raise ValueError("The requested causal error target exceeds the 16,385-frequency budget. Change the explicit tolerance or pulse order; the target was not relaxed.")
    half_count = ceil(_upper_float(needed))
    cutoff = (damping*end).exp()*numerator/(arb.pi()*order*(half_count*delta)**order)
    if not alias <= tolerance/4 or not cutoff <= tolerance/4:
        raise ValueError("The requested analytic bounds cannot be established at this precision.")
    count, nl, nt = 2*half_count+1, len(stack["layers"]), len(time)
    layer_work, inverse_work = count*(nl+1), count*nt
    peak_bytes = 24*1024**2+count*2048+nt*2048+nl*1024
    if count > MAX_FREQUENCY_TERMS or layer_work > MAX_LAYER_FREQUENCY_WORK or inverse_work > MAX_INVERSE_WORK or peak_bytes > MAX_PEAK_BYTES:
        raise ValueError("Causal layered RF exceeds its frequency/work/workspace budget. Reduce the recording or change explicit pulse/tolerance settings; layers and error targets were not silently changed.")
    return dict(omega=omega,rate=rate,peak=peak,numerator=numerator,fourier_l1=fourier_l1,
                tolerance=tolerance,period=period,damping=damping,delta=delta,half_count=half_count,
                alias=alias,cutoff=cutoff,count=count,layer_work=layer_work,inverse_work=inverse_work,
                peak_bytes=peak_bytes)


def _estimate_fields(plan, options, nt):
    return {"model_version": MODEL_VERSION, "frequency_terms": plan["count"], "time_samples": nt,
            "layer_frequency_work_units": plan["layer_work"], "inverse_work_units": plan["inverse_work"],
            "estimated_peak_bytes": plan["peak_bytes"], "period_us": float(plan["period"]),
            "laplace_damping_per_us": float(plan["damping"]), "precision_bits": options["precision_bits"],
            "gamma_order": options["gamma_order"], "gamma_peak_us": float(plan["peak"].mid()),
            "gamma_rate_per_us": float(plan["rate"].mid()),
            "half_frequency_span_mhz": float((plan["half_count"]/plan["period"]).mid()),
            "analytic_alias_bound": _upper_float(plan["alias"]), "frequency_cutoff_bound": _upper_float(plan["cutoff"]),
            "requested_tolerance": options["absolute_tolerance"],
            "arithmetic_status": "Checked during synthesis; estimate is not acceptance of its final enclosure.",
            "workspace_definition": "Conservative Arb coefficient/block copies and output vectors at the bounded precision; not total process RSS."}


def estimate_causal_gamma(stack, time_us, settings):
    stack, time, options = _validate(stack, time_us, settings)
    with _ARITHMETIC_LOCK, ctx.workprec(options["precision_bits"]):
        return _estimate_fields(_plan(stack,time,options),options,len(time))


def _scattering_stack(stack):
    layers = [layer for layer in stack["layers"] if layer["thickness_mm"] > 0]
    impedances = [arb(stack["incident"]["impedance_mrayl"])]+[arb(l["impedance_mrayl"]) for l in layers]+[arb(stack["terminal"]["impedance_mrayl"])]
    interfaces = [(b-a)/(a+b) for a,b in zip(impedances,impedances[1:])]
    delays = [1000*arb(l["thickness_mm"])/arb(l["sound_speed_m_s"]) for l in layers]
    losses = [arb(10).log()/20*arb(l["pressure_loss_db_mm"])*arb(l["thickness_mm"]) for l in layers]
    return interfaces, delays, losses


def causal_gamma_response(stack, time_us, settings):
    stack, time, options = _validate(stack,time_us,settings)
    started = perf_counter()
    with _ARITHMETIC_LOCK, ctx.workprec(options["precision_bits"]):
        p = _plan(stack,time,options)
        interfaces, delays, losses = _scattering_stack(stack)
        surface = 2000*arb(options["surface_standoff_mm"])/arb(stack["incident"]["sound_speed_m_s"])
        phase = acb(0,-p["omega"]*p["peak"]).exp()
        coefficients = []
        min_denominator = arb(1)
        layers = tuple(reversed(list(zip(interfaces[:-1],delays,losses))))
        for k in range(-p["half_count"],p["half_count"]+1):
            s = acb(p["damping"],p["omega"]+k*p["delta"])
            reflection = acb(interfaces[-1])
            for r, delay, loss in layers:
                q = reflection*(-2*loss-2*s*delay).exp()
                denominator = 1+r*q
                lower = denominator.abs_lower()
                if not lower > 0:
                    raise ValueError("Arb cannot exclude a zero scattering denominator at the requested precision.")
                if lower < min_denominator:
                    min_denominator = lower
                reflection = (r+q)/denominator
            pulse = phase*p["numerator"]/acb(p["damping"]+p["rate"],k*p["delta"])**(options["gamma_order"]+1)
            coefficients.append(reflection*pulse*(-s*surface).exp())
        # Independent block phases prevent the exponential rectangular-ball
        # wrapping of one thousands-degree complex Horner evaluation.
        blocks = [(i,acb_poly(coefficients[i:i+HORNER_BLOCK_SIZE])) for i in range(0,len(coefficients),HORNER_BLOCK_SIZE)]
        coefficient_seconds = perf_counter()-started
        rf, imaginary, envelope = [], [], []
        max_complex_error, max_envelope_error = arb(0), arb(0)
        for actual_time in time:
            t = arb(actual_time)  # Exactly the binary64 coordinate that is saved.
            z = acb(0,p["delta"]*t).exp()
            value = sum((poly(z)*acb(0,index*p["delta"]*t).exp() for index,poly in blocks),acb(0))
            value *= acb(p["damping"]*t,(p["omega"]-p["half_count"]*p["delta"])*t).exp()/p["period"]
            real, imag = float(value.real.mid()), float(value.imag.mid())
            error = (value-acb(arb(real),arb(imag))).abs_upper()
            magnitude = abs(value)
            env = float(magnitude.mid())
            env_error = (magnitude-arb(env)).abs_upper()
            if not all(isfinite(v) for v in (real,imag,env)):
                raise ValueError("The causal layered response produced a nonfinite field.")
            if not p["alias"]+p["cutoff"]+error+env_error < p["tolerance"]:
                raise ValueError("The requested combined alias, cutoff and arithmetic error bound cannot be met. Increase explicit arithmetic precision or change the requested tolerance; it was not relaxed.")
            if error > max_complex_error: max_complex_error = error
            if env_error > max_envelope_error: max_envelope_error = env_error
            rf.append(real); imaginary.append(imag); envelope.append(env)
        total = p["alias"]+p["cutoff"]+max_complex_error+max_envelope_error
        encoded_total = _upper_float(total)
        if not arb(encoded_total) <= p["tolerance"]:
            raise ValueError("The outward saved error bound exceeds the requested tolerance.")
        diagnostics = _estimate_fields(p,options,len(time))
        diagnostics.update(arithmetic_status="Accepted: every returned complex-pressure and envelope sample is covered by the saved global bound.",
            analytic_alias_bound=_upper_float(p["alias"]),frequency_cutoff_bound=_upper_float(p["cutoff"]),
            arithmetic_complex_bound=_upper_float(max_complex_error),arithmetic_envelope_bound=_upper_float(max_envelope_error),
            total_error_bound=encoded_total,surface_time_us=float(surface.mid()),
            minimum_denominator_lower_bound=_lower_float(min_denominator),
            coefficient_seconds=coefficient_seconds,elapsed_seconds=perf_counter()-started,
            pulse_definition="C*t^m*exp(-a*t)*exp(i*omega0*(t-m/a)) for t>=0; zero before onset; C=(a*e/m)^m, a=pi*f0*bandwidth/sqrt(2^(2/(m+1))-1). Unit envelope peak, infinite decaying causal support; not the compact Gaussian excitation.",
            bandwidth_definition="Full width at half maximum of the complex-pulse amplitude spectrum divided by carrier frequency.",
            time_definition="Actual saved binary64 time centers in us from the incident-medium transducer reference. A nonreflecting receiver and lossless standoff add a round-trip delay. Gamma excitation peak is a further delay after causal onset; multiples have no unique reflection depth.",
            certificate_definition="For the explicit scalar stack and gamma pulse at each saved time, complex-pressure and envelope error <= alias + cutoff + maximal Arb-to-output complex and envelope errors. Alias C0/(exp(sigma*T)-1) applies only for 0<=t<T; cutoff exp(sigma*b)*D/(pi*m*(K*delta)^m). All constants, coefficients, phases and blocked inverse sums use Arb enclosures; conversion is bounded against the actual returned doubles.",
            evidence_status="Numerical enclosure for exact represented input values under the stated scalar model; excludes material, geometry and measured-instrument uncertainty. No saved SAM volume or depth product is created.")
        return {"time_us":time,"rf":rf,"imaginary":imaginary,"envelope":envelope,"diagnostics":diagnostics}
