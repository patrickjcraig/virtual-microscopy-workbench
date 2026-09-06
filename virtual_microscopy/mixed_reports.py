"""Bounded immutable manual mixed reports; historical reads need no material kernel."""
from __future__ import annotations

from datetime import datetime, timezone
from fractions import Fraction
import hashlib
from importlib.metadata import version
import io
import math
import os
from pathlib import Path
import platform
import stat
from uuid import uuid4

from . import causal_comparison_store as _json

KIND = "mixed_layered_analysis"
PROCESSING_VERSION = "mixed-analysis-0.20.0"
STORE_CONTRACT = "mixed-standalone-report-1"
MATERIAL_MODEL_VERSION = "lossless-real-or-single-relaxation-longitudinal-1"
CERTIFICATE_VERSION = "scalar-mixed-reflected-gamma-1"
MAX_REPORT_BYTES = 16*1024**2
MAX_REPORT_EXPANDED_BYTES = 64*1024**2
MAX_WORKSPACE_BYTES = 256*1024**2
MAX_CATALOG_FILES = 10_000
CATALOG_ORDER = "id_desc"
IMPLEMENTATION_FILES = ("mixed_reports.py", "mixed_api.py", "mixed_analysis.py", "mixed_schemas.py",
    "mixed_acoustics.py", "mixed_time.py", "layered_time.py", "causal_comparison_store.py")
_BASE_FIELDS = {"processing_version", "name", "material_model_version", "stack", "source_status", "spectrum",
    "causal_pulse", "resources", "warnings", "provenance"}
_STORED_FIELDS = {"schema_version", "kind", "id", "report_id", "created_at", "request", "request_sha256",
    "stack_sha256", "report_sha256"}


# Literal copies of the v0.20 report semantics; historical reads import no producer.
SPECTRUM_TEXT = {'energy_definition': 'Reflectance=|R|^2; transmittance=|T|^2 Zi/Zt for real exterior impedances; '
                      'absorptance is retained without clipping.',
 'evidence_status': 'Manual synthetic assumptions; discrete spectra/material/phase/energy curves are rounded '
                    'diagnostics, not calibrated properties or transmitted-RF certificates.',
 'material_definition': 'Explicit lossless_real: authored Z and c, rho=Z/c, M=Z*c, gamma=s/c; no relaxation '
                        'parameter. Explicit sls: M(s)=M0+(Minf-M0)s*tau/(1+s*tau); Z=sqrt(rho*M), '
                        'gamma=rho*s/Z on the analytic positive-real branch. Longitudinal scalar modulus, '
                        "not automatically bulk or Young's modulus.",
 'phase_definition': 'Wrapped degrees; null below the stated rounded pressure-magnitude threshold.',
 'unit_conversion': 'Enclosed SI conversions: GPa*10^9, MRayl*10^6, mm/1000, us/10^6, s_per_us*10^6.'}
CAUSAL_TEXT = {'bandwidth_definition': 'Full width at half maximum of the complex-pulse amplitude spectrum divided by '
                         'carrier frequency.',
 'certificate_definition': 'For the explicit typed real/SLS stack and gamma pulse at each saved time, '
                           'reflected complex-pressure and magnitude error <= the outward sum of the '
                           'published alias, cutoff, maximal Arb-to-output complex and magnitude components. '
                           'Alias C0/(exp(sigma*T)-1) applies only for 0<=t<T; cutoff '
                           'exp(sigma*b)*D/(pi*m*(K*delta)^m). All represented inputs, exact SI conversions, '
                           'typed real Z/c propagation and SLS roots, complex interfaces, denominators, '
                           'propagation, pulse constants, phases and blocked inverse sums use Arb '
                           'enclosures; conversion is bounded against the actual returned doubles.',
 'evidence_status': 'Reflected-response numerical enclosure for exact represented input values under the '
                    'passive typed lossless-real/single-relaxation scalar model; excludes material, geometry '
                    'and measured-instrument uncertainty. No transmitted RF, saved SAM volume or depth '
                    'product is created.',
 'pulse_definition': 'C*t^m*exp(-a*t)*exp(i*omega0*(t-m/a)) for t>=0; zero before onset; C=(a*e/m)^m, '
                     'a=pi*f0*bandwidth/sqrt(2^(2/(m+1))-1). Unit envelope peak, infinite decaying causal '
                     'support; not the compact Gaussian excitation.',
 'time_definition': 'Actual saved binary64 time centers in us from the incident-medium transducer reference. '
                    'A nonreflecting receiver and lossless standoff add a round-trip delay. Gamma excitation '
                    'peak is a further delay after causal onset; multiples have no unique reflection depth.'}
PROVENANCE_TEXT = {'equation_sources': ['https://arxiv.org/abs/1706.04828',
                      'https://dlmf.nist.gov/5.9.E1',
                      'https://flintlib.org/doc/acb.html',
                      'https://flintlib.org/doc/using.html'],
 'evidence_status': 'Assumed scalar material law and synthetic response; numerical enclosures are distinct '
                    'from physical or calibration accuracy.',
 'exterior_speed_scope': 'Exterior-plane scattering uses the real exterior impedances. Incident speed enters '
                         'only the lossless reflected standoff delay; terminal speed is retained explicit '
                         'metadata and does not affect this response.',
 'input_interpretation': 'Exact represented displayed-unit inputs; the numerical kernel encloses '
                         'MRayl-to-Pa-s/m, GPa-to-Pa, us-to-s, mm-to-m and MHz conversions. No independently '
                         'rounded SI request is substituted.'}
FROZEN_WARNINGS = ['Explicit manually assumed lossless real Z/c or scalar longitudinal SLS parameters; no calibration or '
 "automatic conversion between layer kinds. SLS modulus is not automatically Young's or bulk modulus.",
 'Normal incidence with real lossless exterior media; no shear conversion, lateral scattering, focusing or '
 'measured instrument response.',
 'Frequency reflection, transmission, material curves and energy fractions are diagnostics at the saved '
 'frequencies; sampled passivity does not prove global passivity or resolve every resonance.',
 'Pressure transmission magnitude may exceed one. The reflected gamma certificate does not certify '
 'transmitted RF, internal fields or experimental accuracy.',
 'Gamma recording time and excitation peak latency do not identify unique physical depths. This standalone '
 'analysis creates no saved volume, acquisition or filtering job.']


def json_measure(value, byte_limit=None, expanded_limit=None, *, retained_bytes=0):
    measured = _json.json_measure(value, MAX_REPORT_BYTES if byte_limit is None else byte_limit,
        MAX_REPORT_EXPANDED_BYTES if expanded_limit is None else expanded_limit, retained_bytes=retained_bytes)
    if retained_bytes+measured["expanded_bytes"]+12*measured["encoded_bytes"]+16*1024**2 > MAX_WORKSPACE_BYTES:
        raise ValueError("Mixed report exceeds the 256 MiB owned workspace limit.")
    return measured


def bounded_payload(value):
    json_measure(value)
    stream = io.BytesIO()
    for part in _json._tokens(value): stream.write(part)
    return stream.getvalue()


def _read_json(path, *, retained_bytes=0):
    # Conservative parser guard includes retained/encoding space and precedes
    # decoding. The shared absolute guard is 512 MiB; reserving its other half
    # yields this instrument's 256 MiB owned limit without changing old helpers.
    return _json.bounded_json_read(path, MAX_REPORT_BYTES, MAX_REPORT_EXPANDED_BYTES,
        retained_bytes=_json.MAX_WORKSPACE_BYTES-MAX_WORKSPACE_BYTES+retained_bytes)[0]


def _fingerprints():
    return {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in IMPLEMENTATION_FILES}


def _runtime():
    import flint
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
        "system": platform.system(), "machine": platform.machine(),
        "numerical_packages": {"numpy": version("numpy"), "python-flint": version("python-flint"),
            "flint": flint.__FLINT_VERSION__, "flint_release": flint.__FLINT_RELEASE__}}


def _proof_identity():
    path = Path(__file__).resolve().parents[1]/"docs/MIXED_MATERIAL_PROOF.md"
    return {"path": "docs/MIXED_MATERIAL_PROOF.md", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _disk(root, required):
    import shutil
    probe = root
    while not probe.exists() and probe != probe.parent: probe = probe.parent
    if shutil.disk_usage(probe).free < required+64*1024**2:
        raise ValueError("Insufficient free disk space for the Mixed report and reserve.")


def _number(value, nonnegative=False):
    try:
        return type(value) in (int, float) and math.isfinite(value) and (not nonnegative or value >= 0)
    except OverflowError:
        return False


def _vector(value, count, *, nonnegative=False, nullable=False):
    return (type(value) is list and len(value) == count and all(
        (v is None and nullable) or _number(v, nonnegative) for v in value))


def _digest(value):
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _name(value, maximum=100):
    return type(value) is str and 1 <= len(value) <= maximum and bool(value.strip())


def _exact(a, b):
    return _number(a) and _number(b) and float(a).hex() == float(b).hex()


def _axis(settings):
    """Frozen binary64 linspace construction; no NumPy/kernel import on reads."""
    start, end, count = settings['start_mhz'], settings['end_mhz'], settings['samples']
    delta = float(end)-float(start); divisor = count-1; step = delta/divisor
    result = [(float(i)*step if step else (float(i)/divisor)*delta)+float(start) for i in range(count)]
    result[-1] = float(end)
    return result


def _counts(stack):
    layers = stack['layers']
    return {'layer_count': len(layers), 'authored_layer_count': len(layers),
        'active_layer_count': sum(layer['thickness_mm'] > 0 for layer in layers),
        'lossless_real_layer_count': sum(layer['kind'] == 'lossless_real' for layer in layers),
        'sls_layer_count': sum(layer['kind'] == 'sls' for layer in layers)}


def resource_forecast(request, spectrum, causal):
    """One frozen conservative formula shared by preflight and historic admission.

    Three expanded outputs account retained spectra during RF, publication input
    and output objects, plus twelve encodings for JSON/CSV/HTTP copies. Numerical
    kernels run sequentially; their maximum is added, not silently discarded.
    """
    size = json_measure(request)
    counts = _counts(request['stack']); nl = counts['layer_count']; nf = request['spectrum']['samples']
    nt = 0 if request['causal_pulse'] is None else math.floor(request['causal_pulse']['record_duration_us']*request['causal_pulse']['sample_rate_mhz']+1e-9)+1
    cells = (12+5*nl)*nf+3*nl+4*nt
    encoded = 3*size['encoded_bytes']+32*cells+256*1024
    expanded = 3*size['expanded_bytes']+512*cells+2*1024**2
    numerical = max(spectrum['estimated_peak_bytes'], 0 if causal is None else causal['estimated_peak_bytes'])
    peak = 3*expanded+12*encoded+numerical+8*1024**2
    if (encoded>MAX_REPORT_BYTES or expanded>MAX_REPORT_EXPANDED_BYTES or peak>MAX_WORKSPACE_BYTES):
        raise ValueError('Mixed analysis exceeds the 16 MiB serialized / 64 MiB expanded report or 256 MiB owned workspace limit. Reduce samples; the stack and tolerance were not changed.')
    return {'kind':KIND,'processing_version':PROCESSING_VERSION,**counts,'frequency_samples':nf,'time_samples':nt,
        'material_frequency_work_units':spectrum['material_frequency_work_units'],
        'estimated_rf_work_units':0 if causal is None else causal['inverse_work_units'],
        'estimated_report_bytes':encoded,'estimated_report_expanded_bytes':expanded,'estimated_peak_bytes':peak,
        'spectrum':spectrum,'causal_pulse':causal,
        'workspace_definition':'Conservative simultaneous numerical kernel, retained spectra/results, JSON objects and JSON/CSV/HTTP buffers; not total process RSS. Spectra and reflected RF are computed sequentially.'}


def _range(value, lo, hi):
    return _number(value) and lo <= value <= hi


def _provenance(value):
    if (set(value)!={'request_sha256','material_model_version','store_contract','implementation_sha256','runtime','proof_document',*PROVENANCE_TEXT} or
            any(value.get(key)!=expected for key,expected in PROVENANCE_TEXT.items())):
        raise ValueError('Mixed frozen provenance must retain the supported evidence and input interpretations.')
    fingerprints = value.get("implementation_sha256")
    if (type(fingerprints) is not dict or set(fingerprints) != set(IMPLEMENTATION_FILES) or
            any(not _digest(digest) for digest in fingerprints.values())):
        raise ValueError("Mixed frozen implementation fingerprints require the complete contract and canonical SHA-256 digests.")
    proof = value.get("proof_document")
    if (type(proof) is not dict or set(proof) != {"path", "sha256"} or
            proof["path"] != "docs/MIXED_MATERIAL_PROOF.md" or not _digest(proof["sha256"])):
        raise ValueError("Mixed frozen proof identity is malformed.")
    runtime = value.get("runtime")
    if (type(runtime) is not dict or set(runtime) != {"python", "implementation", "system", "machine", "numerical_packages"} or
            any(not _name(runtime[key], 128) for key in ("python", "implementation", "system", "machine"))):
        raise ValueError("Mixed frozen runtime identity is malformed.")
    packages = runtime["numerical_packages"]
    if (type(packages) is not dict or set(packages) != {"numpy", "python-flint", "flint", "flint_release"} or
            any(not _name(packages[key], 128) for key in ("numpy", "python-flint", "flint")) or
            type(packages["flint_release"]) is not int or packages["flint_release"] <= 0):
        raise ValueError("Mixed frozen numerical package identity is malformed.")


def _magnitude_consistency(real, imaginary, magnitude):
    # Only consistency of stored rounded components, not a forward evaluation or
    # new numerical certificate. Four ulps allow independent scalar rounding.
    radius = 4*max(math.ulp(float(v)) for v in (real,imaginary,magnitude))
    square = Fraction(real)**2+Fraction(imaginary)**2
    low=max(Fraction(),Fraction(magnitude)-Fraction(radius)); high=Fraction(magnitude)+Fraction(radius)
    if not low*low<=square<=high*high:
        raise ValueError('Mixed saved magnitude is inconsistent with its rounded signed components.')


def _root_consistency(value, square):
    delta=Fraction(4*math.ulp(float(value)))
    low=max(Fraction(),Fraction(value)-delta); high=Fraction(value)+delta
    if not low*low<=square<=high*high:
        raise ValueError('Mixed material limit is inconsistent with its frozen constitutive parameters.')


def _material_consistency(material,layer,frequency):
    curves=('attenuation_db_mm','attenuation_np_m','phase_speed_m_s','impedance_real_mrayl','impedance_imag_mrayl')
    if layer['kind']=='lossless_real':
        if material['zero_relaxation'] is not None:
            raise ValueError('A real medium has no SLS relaxation flag.')
        expected={'attenuation_db_mm':0.,'attenuation_np_m':0.,'phase_speed_m_s':layer['sound_speed_m_s'],
            'impedance_real_mrayl':layer['impedance_mrayl'],'impedance_imag_mrayl':0.}
        for key in curves:
            if any(not _exact(v,expected[key]) for v in material[key]):
                raise ValueError('Finite real material curves must exactly preserve the authored lossless Z/c.')
        for key,expected_value in (('low_frequency_speed_m_s',layer['sound_speed_m_s']),('high_frequency_speed_m_s',layer['sound_speed_m_s']),('zero_frequency_impedance_mrayl',layer['impedance_mrayl'])):
            if not _exact(material[key],expected_value): raise ValueError('Finite real limits must equal authored Z/c.')
        return
    relaxed=layer['relaxed_modulus_gpa']==layer['unrelaxed_modulus_gpa']
    if type(material['zero_relaxation']) is not bool or material['zero_relaxation']!=relaxed:
        raise ValueError('SLS relaxation flag disagrees with its authored moduli.')
    rho=Fraction(layer['density_kg_m3']); m0=Fraction(layer['relaxed_modulus_gpa'])*10**9; mi=Fraction(layer['unrelaxed_modulus_gpa'])*10**9
    _root_consistency(material['low_frequency_speed_m_s'],m0/rho)
    _root_consistency(material['high_frequency_speed_m_s'],mi/rho)
    _root_consistency(material['zero_frequency_impedance_mrayl'],rho*m0/10**12)
    low,high=material['low_frequency_speed_m_s'],material['high_frequency_speed_m_s']
    for i,f in enumerate(frequency):
        attenuation,nepers,speed,zr,zi=(material[key][i] for key in curves)
        if (attenuation<0 or nepers<0 or zr<=0 or zi<0 or
                speed<low-4*math.ulp(low) or speed>high+4*math.ulp(high)):
            raise ValueError('SLS material curves violate their passive positive-speed/impedance contract.')
        if abs(attenuation-nepers*20/(math.log(10)*1000))>8*max(math.ulp(float(attenuation)),math.ulp(float(nepers*20/(math.log(10)*1000)))):
            raise ValueError('SLS attenuation unit curves disagree.')
        if f==0 or relaxed:
            if (attenuation!=0 or nepers!=0 or zi!=0 or not _exact(speed,low) or
                    not _exact(zr,material['zero_frequency_impedance_mrayl'])):
                raise ValueError('SLS zero-frequency/elastic material curves disagree with their frozen limits.')


def _admit_estimate(value):
    if type(value) is not dict:
        raise ValueError("Mixed analysis requires a frozen resource estimate.")
    for key, limit in (("estimated_report_bytes", MAX_REPORT_BYTES),
        ("estimated_report_expanded_bytes", MAX_REPORT_EXPANDED_BYTES), ("estimated_peak_bytes", MAX_WORKSPACE_BYTES)):
        if type(value.get(key)) is not int or not 0 < value[key] <= limit:
            raise ValueError(f"Mixed {key} exceeds the bounded resource limit or is invalid.")
    if (type(value.get("layer_count")) is not int or not 0 <= value["layer_count"] <= 8 or
            type(value.get("frequency_samples")) is not int or not 2 <= value["frequency_samples"] <= 8193 or
            type(value.get("time_samples")) is not int or not 0 <= value["time_samples"] <= 2049):
        raise ValueError("Mixed resource dimensions exceed the standalone instrument bounds.")


def _certificate(causal, plan, settings, stack):
    if type(causal) is not dict or set(causal) != {"time_us", "rf", "imaginary", "envelope", "diagnostics"}:
        raise ValueError("Mixed causal output must contain only reflected time/real/imaginary/magnitude and diagnostics.")
    if type(plan) is not dict or type(settings) is not dict:
        raise ValueError("Mixed reflected certificate requires its frozen plan and settings.")
    for key in ("record_duration_us", "sample_rate_mhz", "record_start_us", "absolute_tolerance"):
        if not _number(settings.get(key), True) or (key != "record_start_us" and settings[key] <= 0):
            raise ValueError("Mixed reflected certificate has invalid recording settings.")
    pulse_fields = {"center_frequency_mhz", "fractional_bandwidth", "sample_rate_mhz", "record_start_us",
        "record_duration_us", "surface_standoff_mm", "absolute_tolerance", "gamma_order", "precision_bits"}
    if (set(settings) != pulse_fields or not _range(settings["center_frequency_mhz"], 10, 150) or
            not _range(settings["fractional_bandwidth"], .2, 1) or
            not _range(settings["sample_rate_mhz"], 8*settings["center_frequency_mhz"], 2400) or
            not _range(settings["record_start_us"], 0, 12) or not _range(settings["record_duration_us"], .05, 12) or
            settings["record_start_us"]+settings["record_duration_us"] > 12+1e-12 or
            not _range(settings["surface_standoff_mm"], 0, 5) or not _range(settings["absolute_tolerance"], 1e-12, 1e-3) or
            type(settings["gamma_order"]) is not int or not 4 <= settings["gamma_order"] <= 24 or
            type(settings["precision_bits"]) is not int or settings["precision_bits"] not in (64, 96, 128, 192, 256)):
        raise ValueError("Mixed reflected settings are outside the frozen supported contract.")
    count = math.floor(settings["record_duration_us"]*settings["sample_rate_mhz"]+1e-9)+1
    if not 2 <= count <= 2049 or any(not _vector(causal[key], count, nonnegative=key == "envelope")
            for key in ("time_us", "rf", "imaginary", "envelope")):
        raise ValueError("Mixed causal output has invalid finite reflected signal arrays.")
    expected_time = [settings["record_start_us"]+i/settings["sample_rate_mhz"] for i in range(count)]
    if any(float(a).hex() != float(b).hex() for a, b in zip(causal["time_us"], expected_time)):
        raise ValueError("Mixed reflected recording centers disagree with the frozen request.")
    diagnostics = causal["diagnostics"]
    if (type(diagnostics) is not dict or diagnostics.get("certificate_version") != CERTIFICATE_VERSION or
            plan.get("model_version") != "scalar-mixed-causal-gamma-arb-0.20.0" or
            plan.get("material_model_version") != MATERIAL_MODEL_VERSION or
            plan.get("time_samples") != count):
        raise ValueError("Mixed reflected certificate version is unsupported.")
    if (set(diagnostics)!=set(plan)|{'arithmetic_complex_bound','arithmetic_envelope_bound','total_error_bound','surface_time_us',
            'minimum_denominator_lower_bound','coefficient_seconds','elapsed_seconds',*CAUSAL_TEXT} or
            any(diagnostics.get(key)!=value for key,value in CAUSAL_TEXT.items()) or
            diagnostics.get('arithmetic_status')!='Accepted: every returned complex-pressure and envelope sample is covered by the saved global bound.'):
        raise ValueError('Mixed reflected diagnostic/evidence scope is unsupported.')
    # Planning fields are deterministic, whereas status/timing text changes
    # during synthesis. Published analytic bounds must retain their planned
    # values; a resealed zero alias/cutoff claim cannot pass this check.
    for key, value in plan.items():
        if key not in {"arithmetic_status", "workspace_definition"} and diagnostics.get(key) != value:
            raise ValueError(f"Mixed reflected certificate disagrees with planned {key}.")
    keys = ("analytic_alias_bound", "frequency_cutoff_bound", "arithmetic_complex_bound", "arithmetic_envelope_bound")
    if any(not _number(diagnostics.get(key), True) for key in (*keys, "total_error_bound", "requested_tolerance")):
        raise ValueError("Mixed reflected certificate bounds must be finite and nonnegative.")
    if diagnostics["analytic_alias_bound"] <= 0 or diagnostics["frequency_cutoff_bound"] <= 0:
        raise ValueError("Mixed finite gamma planning requires strictly positive analytic remainder bounds.")
    total, tolerance = diagnostics["total_error_bound"], settings["absolute_tolerance"]
    if (diagnostics["requested_tolerance"] != tolerance or total > tolerance or
            Fraction(total) < sum((Fraction(diagnostics[key]) for key in keys), Fraction())):
        raise ValueError("Mixed reflected total must enclose all published components within the requested tolerance.")
    _causal_plan(plan,settings,stack,count,causal['time_us'][-1])
    radius=Fraction(diagnostics['arithmetic_complex_bound'])+Fraction(diagnostics['arithmetic_envelope_bound'])
    for real,imaginary,envelope in zip(causal['rf'],causal['imaginary'],causal['envelope']):
        square=Fraction(real)**2+Fraction(imaginary)**2
        lo=max(Fraction(),Fraction(envelope)-radius);hi=Fraction(envelope)+radius
        if not lo*lo<=square<=hi*hi:
            raise ValueError('Mixed reflected magnitude is inconsistent with the saved signed components and arithmetic enclosures.')
    surface=Fraction(2000)*Fraction(settings['surface_standoff_mm'])/Fraction(stack['incident']['sound_speed_m_s'])
    if not _number(diagnostics.get('surface_time_us'),True) or abs(Fraction(diagnostics['surface_time_us'])-surface)>Fraction(2*math.ulp(float(surface))):
        raise ValueError('Mixed reflected exterior standoff time disagrees with its exact input conversion.')
    for key in ('minimum_denominator_lower_bound','coefficient_seconds','elapsed_seconds'):
        if not _number(diagnostics.get(key),True):raise ValueError('Mixed reflected numerical diagnostics must be finite/nonnegative.')


def _plan_counts(plan,stack):
    counts=_counts(stack)
    if any(type(plan.get(key)) is not int or plan[key]!=value for key,value in counts.items()):
        raise ValueError('Mixed numerical plan counts must preserve every authored typed layer including zeros.')


def _spectrum_plan(plan,stack,nf):
    _plan_counts(plan,stack);nl=len(stack['layers'])
    fields={'model_version','material_model_version','frequency_samples','layer_count','authored_layer_count','active_layer_count','lossless_real_layer_count','sls_layer_count',
        'material_frequency_work_units','estimated_peak_bytes','arithmetic_precision_bits','diagnostic_scope'}
    if (set(plan)!=fields or plan['frequency_samples']!=nf or type(plan['frequency_samples']) is not int or
            plan['arithmetic_precision_bits']!=128 or type(plan['arithmetic_precision_bits']) is not int or
            plan['material_frequency_work_units']!=nf*(24*nl+8) or type(plan['material_frequency_work_units']) is not int or
            plan['estimated_peak_bytes']!=16*1024**2+nf*(1024+nl*768)+nl*8192 or type(plan['estimated_peak_bytes']) is not int or
            plan['material_frequency_work_units']>2_000_000 or plan['estimated_peak_bytes']>128*1024**2 or
            not _name(plan['diagnostic_scope'],2048)):
        raise ValueError('Mixed spectrum plan violates its frozen precision/work/space contract.')


def _causal_plan(plan,settings,stack,nt,end):
    _plan_counts(plan,stack);nl=len(stack['layers']);terms=plan.get('frequency_terms')
    fields={'model_version','certificate_version','material_model_version',*_counts(stack),
        'material_frequency_work_units','frequency_terms','time_samples','layer_frequency_work_units','inverse_work_units','estimated_peak_bytes',
        'period_us','laplace_damping_per_us','precision_bits','gamma_order','gamma_peak_us','gamma_rate_per_us','half_frequency_span_mhz',
        'analytic_alias_bound','frequency_cutoff_bound','requested_tolerance','arithmetic_status','workspace_definition'}
    if set(plan)!=fields or not _name(plan['arithmetic_status'],2048) or not _name(plan['workspace_definition'],2048):
        raise ValueError('Mixed reflected plan must preserve its complete frozen fields.')
    if (type(terms) is not int or not 3<=terms<=16385 or terms%2!=1 or
            plan.get('inverse_work_units')!=terms*nt or type(plan.get('inverse_work_units')) is not int or terms*nt>25_000_000 or
            plan.get('layer_frequency_work_units')!=terms*(12*nl+8) or type(plan.get('layer_frequency_work_units')) is not int or
            plan.get('material_frequency_work_units')!=terms*(12*nl+8) or type(plan.get('material_frequency_work_units')) is not int or terms*(12*nl+8)>2_000_000 or
            plan.get('estimated_peak_bytes')!=32*1024**2+terms*4096+nt*4096+nl*16384 or type(plan.get('estimated_peak_bytes')) is not int or
            plan['estimated_peak_bytes']>128*1024**2 or plan.get('gamma_order')!=settings['gamma_order'] or
            type(plan.get('gamma_order')) is not int or plan.get('precision_bits')!=settings['precision_bits'] or
            type(plan.get('precision_bits')) is not int or not _exact(plan.get('period_us'),max(1.,4*end)) or not end<plan['period_us']):
        raise ValueError('Mixed reflected plan violates its frozen counts, work, precision or first-period contract.')
    for key in ('gamma_rate_per_us','gamma_peak_us','laplace_damping_per_us','half_frequency_span_mhz'):
        if not _number(plan.get(key)) or plan[key]<=0:raise ValueError('Mixed gamma plan requires positive finite scalar parameters.')
    half=(terms-1)//2
    if abs(plan['half_frequency_span_mhz']-half/plan['period_us'])>4*math.ulp(half/plan['period_us']):
        raise ValueError('Mixed gamma frequency span disagrees with the saved integer frequency count.')
    # Historical consistency of the analytic planning scalars only: these ordinary
    # libm checks do not reconstruct a response or establish a new certificate.
    order=settings['gamma_order'];rate=math.pi*settings['center_frequency_mhz']*settings['fractional_bandwidth']/math.sqrt(math.expm1(2*math.log(2)/(order+1)))
    c0=math.exp(order-order*math.log(order)+math.lgamma(order+1)+math.lgamma(order/2)-math.log(2)-.5*math.log(math.pi)-math.lgamma((order+1)/2))
    damping=math.log1p(4*c0/settings['absolute_tolerance'])/plan['period_us']
    cutoff=math.exp(plan['laplace_damping_per_us']*end+order*(math.log(rate)+1-math.log(order))+math.lgamma(order+1)-math.log(math.pi*order)-order*math.log(half*2*math.pi/plan['period_us']))
    expected={'gamma_rate_per_us':rate,'gamma_peak_us':order/rate,'laplace_damping_per_us':damping,
        'analytic_alias_bound':c0/math.expm1(plan['laplace_damping_per_us']*plan['period_us']),'frequency_cutoff_bound':cutoff}
    for key,value in expected.items():
        if not math.isclose(plan[key],value,rel_tol=2e-11,abs_tol=0):
            raise ValueError('Mixed gamma analytic plan scalars disagree with the frozen excitation/recording contract.')


def _validate(report, *, historical=False):
    try:
        return _validate_contract(report, historical=historical)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as exc:
        raise ValueError('Mixed report contains a malformed frozen contract.') from exc


def _validate_contract(report, *, historical=False):
    if (type(report) is not dict or set(report) != _BASE_FIELDS | _STORED_FIELDS or
            report.get("kind") != KIND or type(report.get("schema_version")) is not int or report["schema_version"] != 1 or
            type(report.get("processing_version")) is not str or not 1 <= len(report["processing_version"]) <= 128 or
            (not historical and report["processing_version"] != PROCESSING_VERSION)):
        raise ValueError("Mixed report identity or standalone output contract is unsupported.")
    request = report["request"]
    if type(request) is not dict or set(request) != {"kind", "name", "stack", "spectrum", "causal_pulse"} or request['kind'] != KIND:
        raise ValueError("Mixed report requires only its explicit manual request fields.")
    if (report["name"] != request["name"] or json_measure(report["stack"])['sha256'] != json_measure(request["stack"])['sha256'] or
            not _name(request["name"], 160) or not request["name"].strip() or
            report["source_status"] != "manual_assumptions" or report["material_model_version"] != MATERIAL_MODEL_VERSION):
        raise ValueError("Mixed report assumptions disagree with its frozen request.")
    if report['warnings']!=FROZEN_WARNINGS:raise ValueError('Mixed report evidence warnings differ from the frozen contract.')
    _admit_estimate(report["resources"])
    nl, nf, nt = (report["resources"][key] for key in ("layer_count", "frequency_samples", "time_samples"))
    stack, spectrum = report["stack"], report["spectrum"]
    if (type(stack) is not dict or set(stack) != {"incident", "terminal", "layers"} or
            type(stack["layers"]) is not list or len(stack["layers"]) != nl or
            type(request["spectrum"]) is not dict or request["spectrum"].get("samples") != nf or type(spectrum) is not dict):
        raise ValueError("Mixed stack/spectrum dimensions disagree with their resource plan.")
    spectrum_fields = {"frequency_mhz", "reflection", "transmission", "reflectance", "transmittance",
        "absorptance", "materials", "phase_magnitude_floor", "reference_planes", "diagnostics"}
    if (set(spectrum) != spectrum_fields or not _exact(spectrum["phase_magnitude_floor"], 1e-12) or
            not _name(spectrum["reference_planes"], 2048)):
        raise ValueError("Mixed spectrum may retain only its supported frequency diagnostics.")
    for medium in (stack["incident"], stack["terminal"]):
        if (type(medium) is not dict or set(medium) != {"name", "impedance_mrayl", "sound_speed_m_s"} or
                not _name(medium["name"]) or not _range(medium["impedance_mrayl"], .0001, 100) or
                not _range(medium["sound_speed_m_s"], 100, 20000)):
            raise ValueError("Mixed exterior provenance must retain only its explicit lossless fields.")
    for layer in stack["layers"]:
        if (type(layer) is not dict or layer.get('kind') not in ('lossless_real','sls') or
                not _name(layer.get('name')) or not _range(layer.get('thickness_mm'),0,6)):
            raise ValueError('Mixed finite layers require an explicit supported kind and bounded thickness/name.')
        if layer['kind']=='lossless_real':
            if (set(layer)!={'kind','name','thickness_mm','impedance_mrayl','sound_speed_m_s'} or
                    not _range(layer['impedance_mrayl'],.0001,100) or not _range(layer['sound_speed_m_s'],100,20000)):
                raise ValueError('Finite real media retain exactly their authored Z/c; no SLS parameters or loss field.')
        elif (set(layer)!={'kind','name','thickness_mm','density_kg_m3','relaxed_modulus_gpa','unrelaxed_modulus_gpa','relaxation_time_us'} or
                not _range(layer["density_kg_m3"], 1, 30000) or not _range(layer["relaxed_modulus_gpa"], 1e-6, 1000) or
                not _range(layer["unrelaxed_modulus_gpa"], layer["relaxed_modulus_gpa"], 1000) or
                not _range(layer["relaxation_time_us"], 1e-6, 100)):
            raise ValueError("Mixed frozen material parameters violate the positive passive single-relaxation contract.")
    if sum((Fraction(layer["thickness_mm"]) for layer in stack["layers"]), Fraction()) > 6:
        raise ValueError("Mixed frozen thickness exceeds the 6 mm contract.")
    if (set(request["spectrum"]) != {"start_mhz", "end_mhz", "samples"} or
            not _range(request["spectrum"]["start_mhz"], 0, 300) or
            not _range(request["spectrum"]["end_mhz"], 0, 300) or
            request['spectrum']['start_mhz']>=request['spectrum']['end_mhz'] or type(request['spectrum']['samples']) is not int):
        raise ValueError("Mixed frozen spectrum request is malformed.")
    spectrum_plan = report["resources"].get("spectrum")
    if (type(spectrum_plan) is not dict or type(spectrum.get("diagnostics")) is not dict or
            spectrum_plan.get("model_version") != "scalar-mixed-scattering-arb-0.20.0" or
            spectrum_plan.get("material_model_version") != MATERIAL_MODEL_VERSION or
            any(spectrum["diagnostics"].get(key) != value for key, value in spectrum_plan.items())):
        raise ValueError("Mixed spectrum diagnostics disagree with the frozen kernel plan.")
    _spectrum_plan(spectrum_plan,stack,nf)
    if (set(spectrum['diagnostics'])!=set(spectrum_plan)|{'minimum_denominator_lower_bound',*SPECTRUM_TEXT} or
            any(spectrum['diagnostics'].get(key)!=value for key,value in SPECTRUM_TEXT.items()) or
            not _number(spectrum['diagnostics']['minimum_denominator_lower_bound'],True) or
            spectrum['reference_planes']!='Reflection at the incident face; transmission at the terminal face. External standoff is excluded.'):
        raise ValueError('Mixed spectrum diagnostic/evidence scope is unsupported.')
    if not _vector(spectrum.get("frequency_mhz"), nf, nonnegative=True):
        raise ValueError("Mixed spectrum requires a finite frequency axis.")
    frequency = spectrum["frequency_mhz"]
    if (any(a >= b for a, b in zip(frequency, frequency[1:])) or
            any(not _exact(a,b) for a,b in zip(frequency,_axis(request['spectrum'])))):
        raise ValueError("Mixed spectrum frequency axis disagrees with its frozen request.")
    for name in ("reflection", "transmission"):
        values = spectrum.get(name)
        if (type(values) is not dict or set(values) != {"real", "imag", "magnitude", "phase_deg"} or
                any(not _vector(values[key], nf, nonnegative=key == "magnitude", nullable=key == "phase_deg") for key in values)):
            raise ValueError("Mixed spectrum requires finite pressure components/magnitudes and nullable phase.")
        for real, imaginary, magnitude, phase in zip(*(values[key] for key in ('real','imag','magnitude','phase_deg'))):
            if ((phase is None)!=(magnitude<1e-12) or (phase is not None and not -180<=phase<=180)):
                raise ValueError('Mixed phase must be undefined exactly below the frozen magnitude floor.')
            _magnitude_consistency(real, imaginary, magnitude)
    for name in ("reflectance", "transmittance", "absorptance"):
        if not _vector(spectrum.get(name), nf):
            raise ValueError("Mixed energy diagnostics must retain finite raw values.")
    for i in range(nf):
        reflection=spectrum['reflection'];transmission=spectrum['transmission']
        a=reflection['real'][i]**2+reflection['imag'][i]**2
        b=(transmission['real'][i]**2+transmission['imag'][i]**2)*stack['incident']['impedance_mrayl']/stack['terminal']['impedance_mrayl']
        for saved,expected in ((spectrum['reflectance'][i],a),(spectrum['transmittance'][i],b),
                (spectrum['absorptance'][i],1-spectrum['reflectance'][i]-spectrum['transmittance'][i])):
            if abs(saved-expected)>16*max(math.ulp(float(saved)),math.ulp(float(expected)),math.ulp(a),math.ulp(b)):
                raise ValueError('Mixed saved energy fractions disagree with their rounded pressure components.')
    materials = spectrum.get("materials")
    if type(materials) is not list or len(materials) != nl:
        raise ValueError("Mixed material curves require every authored layer, including zero thickness.")
    for index, (material, layer) in enumerate(zip(materials, stack["layers"])):
        material_fields = {"name", "attenuation_db_mm", "attenuation_np_m", "phase_speed_m_s", "impedance_real_mrayl",
            "impedance_imag_mrayl", "low_frequency_speed_m_s", "high_frequency_speed_m_s", "zero_frequency_impedance_mrayl",
            "zero_relaxation", "zero_thickness",'layer_index','kind','thickness_mm'}
        if (type(material) is not dict or set(material) != material_fields or material.get("name") != layer.get("name") or
                type(material['layer_index']) is not int or material['layer_index']!=index or material['kind']!=layer['kind'] or
                not _exact(material['thickness_mm'],layer['thickness_mm']) or type(material["zero_thickness"]) is not bool or
                material["zero_thickness"] != (layer["thickness_mm"] == 0)):
            raise ValueError("Mixed material curve identity disagrees with its authored layer.")
        for key in ("attenuation_db_mm", "attenuation_np_m", "phase_speed_m_s", "impedance_real_mrayl", "impedance_imag_mrayl"):
            if not _vector(material.get(key), nf):
                raise ValueError("Mixed material curves must retain finite raw values.")
        for key in ("low_frequency_speed_m_s", "high_frequency_speed_m_s", "zero_frequency_impedance_mrayl"):
            if not _number(material.get(key), True) or material[key] == 0:
                raise ValueError("Mixed material limits must be finite positive values.")
        _material_consistency(material,layer,frequency)
    if request["causal_pulse"] is None:
        if report["causal_pulse"] is not None or report["resources"].get("causal_pulse") is not None or nt != 0:
            raise ValueError("Frequency-only Mixed reports cannot contain a causal recording.")
    else:
        _certificate(report["causal_pulse"], report["resources"].get("causal_pulse"), request["causal_pulse"], stack)
        if len(report["causal_pulse"]["time_us"]) != nt:
            raise ValueError("Mixed recording count disagrees with its resource plan.")
    provenance = report["provenance"]
    if (type(provenance) is not dict or provenance.get("store_contract") != STORE_CONTRACT or
            provenance.get("material_model_version") != MATERIAL_MODEL_VERSION or
            provenance.get("request_sha256") != report["request_sha256"]):
        raise ValueError("Mixed frozen provenance contract is inconsistent.")
    _provenance(provenance)
    if report['id']!=report['report_id'] or _json._checked_id(report['id'])!=report['id']:
        raise ValueError('Mixed report IDs disagree.')
    try:
        if datetime.fromisoformat(report['created_at']).tzinfo is None: raise ValueError
    except (ValueError,TypeError) as exc:
        raise ValueError('Mixed report requires a timezone-aware creation time.') from exc
    for key,value in (('request_sha256',request),('stack_sha256',stack),('report_sha256',{k:v for k,v in report.items() if k!='report_sha256'})):
        if report[key]!=json_measure(value)['sha256']:
            raise ValueError('Mixed frozen report/request/stack checksum mismatch.')


def _account(report, measured):
    estimate = report["resources"]
    if (measured["encoded_bytes"] > estimate["estimated_report_bytes"] or
            measured["expanded_bytes"] > estimate["estimated_report_expanded_bytes"] or
            measured["expanded_bytes"]+12*measured["encoded_bytes"]+16*1024**2 > estimate["estimated_peak_bytes"]):
        raise ValueError("Mixed published output exceeds its advertised resource estimate.")
    expected=resource_forecast(report['request'],estimate['spectrum'],estimate['causal_pulse'])
    expected['processing_version']=report['processing_version']
    if json_measure(expected)['sha256']!=json_measure(estimate)['sha256']:
        raise ValueError('Mixed resource estimate disagrees with the complete frozen numerical/publication forecast.')


def _summary(report):
    result = {key: report[key] for key in ("id", "report_id", "kind", "created_at", "name", "processing_version", "report_sha256", "source_status", "material_model_version")}
    result.update(layer_count=report["resources"]["layer_count"], frequency_samples=report["resources"]["frequency_samples"],
        time_samples=report["resources"]["time_samples"], causal_pulse_available=report["causal_pulse"] is not None)
    result.update({key:report['resources'][key] for key in ('authored_layer_count','active_layer_count','lossless_real_layer_count','sls_layer_count')})
    json_measure(result, 64*1024, 1024**2)
    return result


class MixedReportStore:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.directory = self.root/"mixed-reports"

    def estimate(self, request):
        from .mixed_analysis import estimate_mixed
        return estimate_mixed(request)

    def _path(self, identifier):
        identifier = _json._checked_id(identifier)
        for parent in (self.root, *self.root.parents, self.directory): _json._reject_link(parent)
        path = self.directory/f"{identifier}.json"
        _json._reject_link(path)
        if self.directory.resolve().parent != self.root.resolve() or path.resolve().parent != self.directory.resolve():
            raise ValueError("Mixed report path leaves its data directory.")
        return path

    def create(self, request):
        from .mixed_analysis import analyze_mixed, estimate_mixed
        from .mixed_schemas import MixedLayeredAnalysisRequest
        identifier = str(uuid4())
        path = self._path(identifier)
        if path.exists(): raise FileExistsError(path)
        request = MixedLayeredAnalysisRequest.model_validate(request)
        normalized = request.model_dump(mode="json")
        request_sha = json_measure(normalized)["sha256"]
        estimate = estimate_mixed(request)
        _admit_estimate(estimate)
        _disk(self.root, estimate["estimated_report_bytes"])
        result = analyze_mixed(request)
        json_measure(result)
        if (type(result) is not dict or set(result) != _BASE_FIELDS or result.get("resources") != estimate or
                type(result.get("provenance")) is not dict):
            raise ValueError("Mixed analysis must preserve its admitted output/resource contract.")
        report = {**result, "schema_version": 1, "kind": KIND, "id": identifier, "report_id": identifier,
            "created_at": datetime.now(timezone.utc).isoformat(), "request": normalized,
            "request_sha256": request_sha, "stack_sha256": json_measure(normalized["stack"])["sha256"],
            "provenance": {**result["provenance"], "store_contract": STORE_CONTRACT,
                "implementation_sha256": _fingerprints(), "runtime": _runtime(),
                "proof_document": _proof_identity()}}
        report["report_sha256"] = json_measure(report)["sha256"]
        measured = json_measure(report)
        _validate(report); _account(report, measured); _summary(report)
        _disk(self.root, measured["encoded_bytes"])
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(identifier)
        temporary = self.directory/f".{identifier}.tmp"
        staged = False
        try:
            with temporary.open("xb") as stream:
                staged = True
                for part in _json._tokens(report): stream.write(part)
                stream.flush(); os.fsync(stream.fileno())
            self._path(identifier)
            os.link(temporary, path)
        finally:
            if staged: temporary.unlink(missing_ok=True)
        return report

    def read(self, identifier, *, retained_bytes=0):
        path = self._path(identifier)
        if not path.exists(): raise KeyError(identifier)
        report = _read_json(path, retained_bytes=retained_bytes)
        measured = json_measure(report, retained_bytes=retained_bytes)
        _validate(report, historical=True); _account(report, measured)
        if report["id"] != identifier or report["report_id"] != identifier:
            raise ValueError("Mixed report identity mismatch.")
        for key, value in (("request_sha256", report["request"]), ("stack_sha256", report["stack"]),
                ("report_sha256", {k: v for k, v in report.items() if k != "report_sha256"})):
            if report[key] != json_measure(value)["sha256"]:
                raise ValueError("Mixed frozen report/request/stack checksum mismatch.")
        return report

    def list(self, limit=50, offset=0):
        if (type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset < MAX_CATALOG_FILES):
            raise ValueError("Mixed pagination requires limit 1..100 and offset 0..9999.")
        self._path("00000000-0000-0000-0000-000000000000")
        if not self.directory.exists(): return []
        identifiers = []
        with os.scandir(self.directory) as entries:
            for count, entry in enumerate(entries, 1):
                if count > MAX_CATALOG_FILES: raise ValueError("Mixed report catalog exceeds its bounded entry limit.")
                if entry.name.startswith(".") and entry.name.endswith(".tmp"): continue
                if not entry.name.endswith(".json"): raise ValueError("Unexpected entry in Mixed report catalog.")
                identifier = _json._checked_id(entry.name[:-5])
                if not stat.S_ISREG(self._path(identifier).stat().st_mode):
                    raise ValueError("Mixed catalog requires regular report files.")
                identifiers.append(identifier)
        identifiers.sort(reverse=True)
        result=[];retained=0
        for identifier in identifiers[offset:offset+limit]:
            report=self.read(identifier,retained_bytes=retained)
            result.append(_summary(report));del report
            retained=json_measure(result,8*1024**2,8*1024**2)['expanded_bytes']
        return result


def mixed_report_csv(report):
    if type(report) is not dict or any(not key.isascii() for key in report if type(key) is str):
        raise ValueError("Mixed CSV requires ASCII top-level field names.")
    json_measure(report)
    stream = io.BytesIO(); stream.write(b"section,field,value_json\r\n")
    for key in sorted(report):
        stream.write(b'"report","'+key.encode("ascii").replace(b'"', b'""')+b'","')
        for part in _json._tokens(report[key]): stream.write(part.replace(b'"', b'""'))
        stream.write(b'"\r\n')
    return stream.getvalue().decode("ascii")
