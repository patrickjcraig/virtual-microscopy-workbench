"""Arb evaluation of scalar SLS materials and normal-incidence scattering.

The material and scattering operations enclose represented inputs and exact SI
conversions. Displayed spectra/phase/energy are ordinary rounded diagnostics;
only the separate reflected gamma calculation publishes a total time certificate.
"""
from math import isfinite
from numbers import Real

from flint import acb, arb, ctx
import numpy as np

from .layered_time import _ARITHMETIC_LOCK, _lower_float
from .sls_schemas import SLSStack

MODEL_VERSION = "scalar-sls-scattering-arb-0.17.0"
MATERIAL_MODEL_VERSION = "single-relaxation-longitudinal-1"
MAX_FREQUENCY_SAMPLES = 8193
MAX_MATERIAL_FREQUENCY_WORK = 2_000_000
MAX_PEAK_BYTES = 128*1024**2
PHASE_MAGNITUDE_FLOOR = 1e-12
SPECTRUM_PRECISION_BITS = 128


def _material(layer, s_us):
    """s is per microsecond; material is SI. No conversion through float."""
    rho = arb(layer["density_kg_m3"])
    m0 = arb(layer["relaxed_modulus_gpa"])*10**9
    mi = arb(layer["unrelaxed_modulus_gpa"])*10**9
    s = s_us*10**6
    if layer["relaxed_modulus_gpa"] == layer["unrelaxed_modulus_gpa"]:
        modulus = acb(m0)
    else:
        tau = arb(layer["relaxation_time_us"])/10**6
        pole = 1+s*tau
        if not pole.abs_lower() > 0:
            raise ValueError("Arb cannot exclude a constitutive denominator zero.")
        modulus = m0+(mi-m0)*s*tau/pole
    if not modulus.is_finite() or not modulus.real > 0:
        raise ValueError("Arb cannot establish the positive-real SLS material branch.")
    impedance = (rho*modulus).sqrt()
    if not impedance.is_finite() or not impedance.real > 0:
        raise ValueError("Arb cannot establish the analytic positive-real impedance root.")
    gamma = rho*s/impedance
    if not gamma.is_finite():
        raise ValueError("The enclosed SLS propagation constant is not finite.")
    return modulus, impedance, gamma


def _scattering(stack, s_us, *, transmission=True):
    """Full pressure R/T, referenced to the two exterior faces, no standoff."""
    layers = [layer for layer in stack["layers"] if layer["thickness_mm"] > 0]
    material = [_material(layer, s_us) for layer in layers]
    impedances = [acb(arb(stack["incident"]["impedance_mrayl"])*10**6)]+[v[1] for v in material]+[
        acb(arb(stack["terminal"]["impedance_mrayl"])*10**6)]
    interfaces, passes = [], []
    minimum = arb(1)
    for left, right in zip(impedances, impedances[1:]):
        denominator = left+right
        if not denominator.abs_lower() > 0:
            raise ValueError("Arb cannot exclude an interface impedance denominator zero.")
        interfaces.append((right-left)/denominator)
        passes.append(2*right/denominator)
    reflection, trans = interfaces[-1], passes[-1]
    for index in range(len(layers)-1, -1, -1):
        propagation = (-material[index][2]*arb(layers[index]["thickness_mm"])/1000).exp()
        q = reflection*propagation*propagation
        denominator = 1+interfaces[index]*q
        lower = denominator.abs_lower()
        if not lower > 0:
            raise ValueError("Arb cannot exclude a zero SLS scattering denominator at this precision.")
        if lower < minimum:
            minimum = lower
        if transmission:
            trans = passes[index]*propagation*trans/denominator
        reflection = (interfaces[index]+q)/denominator
    if not reflection.is_finite() or (transmission and not trans.is_finite()):
        raise ValueError("The enclosed SLS scattering result is not finite.")
    return reflection, trans if transmission else None, minimum


def _validate_spectrum(stack, frequencies_mhz):
    stack = SLSStack.model_validate(stack).model_dump(mode="json")
    if not isinstance(frequencies_mhz, (list, tuple, np.ndarray)) or not 1 <= len(frequencies_mhz) <= MAX_FREQUENCY_SAMPLES:
        raise ValueError("SLS spectra require 1 to 8,193 bounded actual frequencies.")
    frequency = []
    for f in frequencies_mhz:
        if isinstance(f, bool) or not isinstance(f, Real) or not isfinite(f) or not 0 <= f <= 300:
            raise ValueError("SLS frequencies must be finite real values from 0 to 300 MHz.")
        frequency.append(float(f))
    if any(b <= a for a, b in zip(frequency,frequency[1:])):
        raise ValueError("Actual spectrum frequencies must be strictly increasing.")
    return stack, frequency


def estimate_reflection_spectrum(stack, frequencies_mhz):
    stack, frequency = _validate_spectrum(stack, frequencies_mhz)
    nl, nf = len(stack["layers"]), len(frequency)
    work = nf*(24*nl+8)  # Retained material curves plus scattering materials.
    peak = 16*1024**2+nf*(1024+nl*768)+nl*8192
    if work > MAX_MATERIAL_FREQUENCY_WORK or peak > MAX_PEAK_BYTES:
        raise ValueError("SLS spectrum exceeds its material-frequency or workspace budget.")
    return {"model_version":MODEL_VERSION,"material_model_version":MATERIAL_MODEL_VERSION,
        "frequency_samples":nf,"layer_count":nl,"material_frequency_work_units":work,"estimated_peak_bytes":peak,
        "arithmetic_precision_bits":SPECTRUM_PRECISION_BITS,"diagnostic_scope":"Rounded discrete-frequency diagnostics, not a reflected-RF or physical-accuracy certificate."}


def _float(value):
    result = float(value.mid())
    if not isfinite(result):
        raise ValueError("An SLS spectrum value cannot be encoded finitely.")
    return result


def _response(values):
    real = np.array([v.real for v in values],dtype=np.float64)
    imag = np.array([v.imag for v in values],dtype=np.float64)
    magnitude = np.hypot(real,imag)
    phase = np.angle(real+1j*imag,deg=True).astype(object)
    phase[magnitude < PHASE_MAGNITUDE_FLOOR] = None
    return {"real":real.tolist(),"imag":imag.tolist(),"magnitude":magnitude.tolist(),"phase_deg":phase.tolist()}


def reflection_spectrum(stack, frequencies_mhz):
    estimate = estimate_reflection_spectrum(stack,frequencies_mhz)
    stack, frequency = _validate_spectrum(stack,frequencies_mhz)
    reflection, transmission, materials = [], [], []
    with _ARITHMETIC_LOCK, ctx.workprec(SPECTRUM_PRECISION_BITS):
        for layer in stack["layers"]:
            rho, m0, mi = arb(layer["density_kg_m3"]),arb(layer["relaxed_modulus_gpa"])*10**9,arb(layer["unrelaxed_modulus_gpa"])*10**9
            materials.append({"name":layer["name"],"low_frequency_speed_m_s":_float((m0/rho).sqrt()),
                "high_frequency_speed_m_s":_float((mi/rho).sqrt()),"zero_frequency_impedance_mrayl":_float((rho*m0).sqrt()/10**6),
                "attenuation_db_mm":[],"attenuation_np_m":[],"phase_speed_m_s":[],"impedance_real_mrayl":[],"impedance_imag_mrayl":[],
                "zero_relaxation":layer["relaxed_modulus_gpa"]==layer["unrelaxed_modulus_gpa"],
                "zero_thickness":layer["thickness_mm"]==0})
        minimum = arb(1)
        for f in frequency:
            s = acb(0,2*arb.pi()*arb(f))
            r,t,lower = _scattering(stack,s)
            if lower < minimum: minimum = lower
            reflection.append(complex(_float(r.real),_float(r.imag)))
            transmission.append(complex(_float(t.real),_float(t.imag)))
            for layer,record in zip(stack["layers"],materials):
                _,z,gamma = _material(layer,s)
                record["attenuation_np_m"].append(_float(gamma.real))
                record["attenuation_db_mm"].append(_float(20*gamma.real/(arb(10).log()*1000)))
                speed = (arb(layer["relaxed_modulus_gpa"])*10**9/arb(layer["density_kg_m3"])).sqrt() if f==0 else 2*arb.pi()*arb(f)*10**6/gamma.imag
                record["phase_speed_m_s"].append(_float(speed))
                record["impedance_real_mrayl"].append(_float(z.real/10**6))
                record["impedance_imag_mrayl"].append(_float(z.imag/10**6))
        minimum_float = _lower_float(minimum)
    reflectance = np.abs(reflection)**2
    transmittance = np.abs(transmission)**2*stack["incident"]["impedance_mrayl"]/stack["terminal"]["impedance_mrayl"]
    return {"frequency_mhz":frequency,"reflection":_response(reflection),"transmission":_response(transmission),
        "reflectance":reflectance.tolist(),"transmittance":transmittance.tolist(),"absorptance":(1-reflectance-transmittance).tolist(),
        "materials":materials,"phase_magnitude_floor":PHASE_MAGNITUDE_FLOOR,
        "reference_planes":"Reflection at the incident face; transmission at the terminal face. External standoff is excluded.",
        "diagnostics":{**estimate,"minimum_denominator_lower_bound":minimum_float,
            "phase_definition":"Wrapped degrees; null below the stated rounded pressure-magnitude threshold.",
            "energy_definition":"Reflectance=|R|^2; transmittance=|T|^2 Zi/Zt for real exterior impedances; absorptance is retained without clipping.",
            "material_definition":"M(s)=M0+(Minf-M0)s*tau/(1+s*tau); Z=sqrt(rho*M), gamma=rho*s/Z on the analytic positive-real branch. Longitudinal scalar modulus, not automatically bulk or Young's modulus.",
            "unit_conversion":"Enclosed SI conversions: GPa*10^9, MRayl*10^6, mm/1000, us/10^6, s_per_us*10^6.",
            "evidence_status":"Manual synthetic assumptions; discrete spectra/material/phase/energy curves are rounded diagnostics, not calibrated properties or transmitted-RF certificates."}}
