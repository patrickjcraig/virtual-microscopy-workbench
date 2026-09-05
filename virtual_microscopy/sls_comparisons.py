"""Compatible comparisons of complete frozen standalone SLS reports only."""
from dataclasses import dataclass
import math

import numpy as np

from .sls_comparison_schemas import SLSComparisonRequest
from .sls_comparison_math import (SIGNALS, BOUNDS, BOUND_DEFINITION, DIAGNOSTIC_SCOPE,
    checked_differences, column_bounds, rf_metrics, gate_products, spectrum_comparison,
    check_arithmetic_environment)
from .sls_reports import SLSReportStore

KIND = "sls_analysis_comparison"
PROCESSING_VERSION = "sls-comparison-0.18.0"
POLICY = "same_sls_exteriors_excitation_v1"
MAX_REPORT_BYTES = 32*1024**2
MAX_REPORT_EXPANDED_BYTES = 128*1024**2
MAX_WORKSPACE_BYTES = 512*1024**2
SOURCE_BYTES = 16*1024**2
SOURCE_EXPANDED_BYTES = 64*1024**2
NUMERICAL_FILES = ("sls_schemas.py", "sls_acoustics.py", "sls_time.py", "sls_analysis.py", "layered_time.py", "schemas.py")
SPECTRUM_SEMANTICS = {
    "model_version":"scalar-sls-scattering-arb-0.17.0",
    "material_model_version":"single-relaxation-longitudinal-1",
    "phase_definition":"Wrapped degrees; null below the stated rounded pressure-magnitude threshold.",
    "energy_definition":"Reflectance=|R|^2; transmittance=|T|^2 Zi/Zt for real exterior impedances; absorptance is retained without clipping.",
    "material_definition":"M(s)=M0+(Minf-M0)s*tau/(1+s*tau); Z=sqrt(rho*M), gamma=rho*s/Z on the analytic positive-real branch. Longitudinal scalar modulus, not automatically bulk or Young's modulus.",
    "unit_conversion":"Enclosed SI conversions: GPa*10^9, MRayl*10^6, mm/1000, us/10^6, s_per_us*10^6.",
}
REFERENCE_PLANES = "Reflection at the incident face; transmission at the terminal face. External standoff is excluded."
PULSE_SEMANTICS = {
    "model_version":"scalar-sls-causal-gamma-arb-0.17.0",
    "material_model_version":"single-relaxation-longitudinal-1",
    "certificate_version":"scalar-sls-reflected-gamma-1",
    "pulse_definition":"C*t^m*exp(-a*t)*exp(i*omega0*(t-m/a)) for t>=0; zero before onset; C=(a*e/m)^m, a=pi*f0*bandwidth/sqrt(2^(2/(m+1))-1). Unit envelope peak, infinite decaying causal support; not the compact Gaussian excitation.",
    "bandwidth_definition":"Full width at half maximum of the complex-pulse amplitude spectrum divided by carrier frequency.",
    "time_definition":"Actual saved binary64 time centers in us from the incident-medium transducer reference. A nonreflecting receiver and lossless standoff add a round-trip delay. Gamma excitation peak is a further delay after causal onset; multiples have no unique reflection depth.",
    "certificate_definition":"For the explicit SLS stack and gamma pulse at each saved time, reflected complex-pressure and magnitude error <= the outward sum of the published alias, cutoff, maximal Arb-to-output complex and magnitude components. Alias C0/(exp(sigma*T)-1) applies only for 0<=t<T; cutoff exp(sigma*b)*D/(pi*m*(K*delta)^m). All represented inputs, exact SI conversions, SLS roots, complex interfaces, denominators, propagation, pulse constants, phases and blocked inverse sums use Arb enclosures; conversion is bounded against the actual returned doubles.",
}
WARNINGS = [
    "Reference A is a selected synthetic baseline, not measured ground truth; differences are candidate B minus reference A.",
    "All frequency R/T, energy and material curves are ordinary diagnostics. No reflected-RF certificate applies to frequency curves or transmitted RF.",
    "The reflected complex residual and difference of separately saved magnitudes have different meanings. The magnitude of a complex residual is an ordinary diagnostic.",
    "Each complete source total is used for both reflected pressure and saved magnitude; no source certificate component is stripped. Bounds do not certify gates or summary statistics.",
    "Layer differences are positional authored changes, not inferred material correspondence or isolated physical causes. No alignment, resampling, normalization, phase fitting or material calibration occurs.",
    "Saved snapshots preserve publication identities and certificates without reproducing forward calculations. Recording time has no unique depth interpretation.",
]


class SLSComparisonCompatibilityError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("Saved SLS reports require identical represented axes, exterior media and supported model/excitation semantics.")


@dataclass
class Source:
    identifier: str
    report: dict
    measurement: dict


def _budget(value, label):
    if type(value) is not int or not 0 <= value <= MAX_WORKSPACE_BYTES:
        raise ValueError(f"SLS comparison {label} exceeds the 512 MiB owned-workspace limit.")


def _source(root, identifier, retained_bytes=0):
    from .sls_comparison_store import bounded_json_read, validate_source
    # The unchanged historical validator owns at most 256 MiB, including its
    # decoded source/encoding buffers. Other already-retained objects are extra.
    _budget(retained_bytes+256*1024**2, "historical source verification")
    path = SLSReportStore(root)._path(identifier)
    if not path.exists():
        raise KeyError(identifier)
    report, size = bounded_json_read(path, SOURCE_BYTES, SOURCE_EXPANDED_BYTES, retained_bytes=retained_bytes)
    validate_source(report, expected_id=identifier)
    return Source(identifier, report, size)


def _exact(a, b):
    if type(a) in (int, float) and type(b) in (int, float):
        return float(a).hex() == float(b).hex()
    return type(a) is type(b) and a == b


def compatible(a, b, mode):
    """Pure checks on historical dictionaries; no current numerical identity lookup."""
    if mode not in ("spectrum_only", "spectrum_and_reflected_rf"):
        raise ValueError("Unsupported SLS comparison mode.")
    issues, matched = [], []
    def compare(field, left, right):
        if not _exact(left, right):
            issues.append({"field":field,"reference":left,"candidate":right,"message":"Frozen represented values differ."})
        else:
            matched.append(field)
    def expected(side, field, actual, wanted):
        if not _exact(actual, wanted):
            issues.append({"field":side+"."+field,"reference":actual,"candidate":wanted,"message":"Unsupported or missing frozen semantics."})
    for side, report in (("reference",a),("candidate",b)):
        expected(side,"kind",report.get("kind"),"sls_layered_analysis")
        expected(side,"store_contract",report["provenance"].get("store_contract"),"sls-standalone-report-1")
        expected(side,"reference_planes",report["spectrum"].get("reference_planes"),REFERENCE_PLANES)
        expected(side,"phase_magnitude_floor",report["spectrum"].get("phase_magnitude_floor"),1e-12)
        for key, value in SPECTRUM_SEMANTICS.items():
            expected(side,"spectrum."+key,report["spectrum"]["diagnostics"].get(key),value)
    for name in NUMERICAL_FILES:
        compare("implementation."+name,a["provenance"]["implementation_sha256"].get(name),b["provenance"]["implementation_sha256"].get(name))
    compare("proof_document",a["provenance"]["proof_document"],b["provenance"]["proof_document"])
    for side in ("incident", "terminal"):
        for key in ("impedance_mrayl", "sound_speed_m_s"):
            compare(f"stack.{side}.{key}",a["stack"][side][key],b["stack"][side][key])
    def axis(field, left, right):
        la, ra = np.asarray(left,np.float64), np.asarray(right,np.float64)
        if la.shape != ra.shape or la.tobytes() != ra.tobytes():
            import hashlib
            issues.append({"field":field,"reference":hashlib.sha256(la.tobytes()).hexdigest(),
                "candidate":hashlib.sha256(ra.tobytes()).hexdigest(),"message":"Actual saved float64 axes must match bit-exactly; no common subset is selected."})
        else:
            matched.append(field)
    axis("frequency_mhz",a["spectrum"]["frequency_mhz"],b["spectrum"]["frequency_mhz"])
    if mode == "spectrum_and_reflected_rf":
        if a["causal_pulse"] is None or b["causal_pulse"] is None:
            issues.append({"field":"causal_pulse","reference":a["causal_pulse"] is not None,"candidate":b["causal_pulse"] is not None,"message":"Reflected RF mode requires both saved recordings."})
        else:
            for side, report in (("reference",a),("candidate",b)):
                for key, value in PULSE_SEMANTICS.items():
                    expected(side,"causal_pulse."+key,report["causal_pulse"]["diagnostics"].get(key),value)
            axis("time_us",a["causal_pulse"]["time_us"],b["causal_pulse"]["time_us"])
            for key in ("center_frequency_mhz","fractional_bandwidth","gamma_order","surface_standoff_mm",
                        "sample_rate_mhz","record_start_us","record_duration_us"):
                compare("causal_pulse."+key,a["request"]["causal_pulse"][key],b["request"]["causal_pulse"][key])
    if issues:
        raise SLSComparisonCompatibilityError(issues)
    return {"policy":POLICY,"matched_fields":matched,"mode":mode,
        "source_semantics":"Frozen source-to-source identities; current installed kernels are not consulted.",
        "material_correspondence":"None inferred. Layer changes are positional authored differences."}


def parameter_differences(a, b):
    changes = []
    def walk(left, right, path):
        if isinstance(left,dict) and isinstance(right,dict):
            for key in sorted(left.keys() | right.keys()):
                walk(left.get(key),right.get(key),path+"."+key)
        elif isinstance(left,list) and isinstance(right,list):
            for i in range(max(len(left),len(right))):
                walk(left[i] if i<len(left) else None,right[i] if i<len(right) else None,f"{path}[{i}]")
        elif not _exact(left,right):
            changes.append({"path":path,"reference":left,"candidate":right,
                "meaning":"Positional authored change; no inferred layer correspondence or physical attribution."})
    walk(a["request"],b["request"],"request")
    walk(a["provenance"]["runtime"],b["provenance"]["runtime"],"provenance.runtime")
    walk(a["provenance"]["implementation_sha256"],b["provenance"]["implementation_sha256"],"provenance.implementation_sha256")
    return changes


def _plan(a, b, mode):
    nf = len(a.report["spectrum"]["frequency_mhz"])
    nt = 0 if mode == "spectrum_only" else len(a.report["causal_pulse"]["time_us"])
    if not 2 <= nf <= 8193 or not 0 <= nt <= 2049:
        raise ValueError("SLS comparison exceeds saved frequency/time limits.")
    unique = [a] if a is b else [a,b]
    source_encoded = sum(s.measurement["encoded_bytes"] for s in unique)
    source_expanded = sum(s.measurement["expanded_bytes"] for s in unique)
    numeric = 9*nf+3*nt
    encoded = source_encoded+32*numeric+512*1024
    expanded = source_expanded+512*numeric+4*1024**2
    workspace = 64*(nf+nt)+8*1024**2
    peak = max(source_expanded+256*1024**2,3*expanded+12*encoded+workspace+32*1024**2)
    if encoded>MAX_REPORT_BYTES or expanded>MAX_REPORT_EXPANDED_BYTES:
        raise ValueError("SLS comparison exceeds its 32 MiB encoded / 128 MiB expanded report limits.")
    _budget(peak,"processing and publication")
    return {"frequency_samples":nf,"time_samples":nt,"unique_source_snapshots":len(unique),
        "source_snapshot_encoded_bytes":source_encoded,"source_snapshot_expanded_bytes":source_expanded,
        "estimated_report_bytes":encoded,"estimated_report_expanded_bytes":expanded,"estimated_peak_bytes":peak,
        "numerical_workspace_bytes":workspace,"workspace_definition":"Owned simultaneous historical source verification, retained deduplicated snapshots, difference arrays, JSON copies and response buffers; not process RSS."}


def _gate(time, start, end):
    start, end = (float(time[0]),float(time[-1])) if start is None else (start,end)
    if start<time[0] or end>time[-1] or end<start:
        raise ValueError("Inclusive gate must stay within actual saved time centers.")
    indices = np.flatnonzero((time>=start)&(time<=end))
    if not len(indices):
        raise ValueError("Inclusive gate contains no actual saved time centers.")
    lo, hi = int(indices[0]),int(indices[-1])+1
    return {"requested_start_us":start,"requested_end_us":end,"start_index":lo,"stop_index_exclusive":hi,
        "sample_count":hi-lo,"actual_start_us":float(time[lo]),"actual_end_us":float(time[hi-1]),
        "selection":"Exact inclusive comparisons against actual saved time centers; no clipping or interpolation."}


def _prepare(root, request):
    request = request if isinstance(request,SLSComparisonRequest) else SLSComparisonRequest.model_validate(request)
    a = _source(root,request.reference_report_id)
    b = a if request.candidate_report_id==a.identifier else _source(root,request.candidate_report_id,a.measurement["expanded_bytes"])
    match = compatible(a.report,b.report,request.mode)
    plan = _plan(a,b,request.mode)
    if request.frequency_index is not None and request.frequency_index>=plan["frequency_samples"]:
        raise ValueError("Frequency cursor exceeds actual saved samples.")
    if request.time_index is not None and request.time_index>=plan["time_samples"]:
        raise ValueError("Time cursor exceeds actual saved samples.")
    gate = None if request.mode == "spectrum_only" else _gate(np.asarray(a.report["causal_pulse"]["time_us"],np.float64),request.gate_start_us,request.gate_end_us)
    return request,a,b,match,plan,gate


def _reference(source):
    return {"report_id":source.identifier,"name":source.report["name"],"snapshot_sha256":source.measurement["sha256"],"report_sha256":source.report["report_sha256"]}


def estimate_sls_comparison(root, request):
    """Historical source validation and admission only; no residual evaluation."""
    request,a,b,match,plan,gate = _prepare(root,request)
    return {**plan,"compatibility":match,"source_reference":_reference(a),"source_candidate":_reference(b),
        "parameter_differences":parameter_differences(a.report,b.report),"gate":gate}


def compute_sls_comparison(root, request):
    from .sls_comparison_store import json_measure
    request,a,b,match,plan,gate = _prepare(root,request)
    spectrum = spectrum_comparison(a.report["spectrum"],b.report["spectrum"],np.asarray(a.report["spectrum"]["frequency_mhz"],np.float64))
    pulse, arithmetic = None, None
    if request.mode == "spectrum_and_reflected_rf":
        arithmetic = check_arithmetic_environment()
        av,bv = ({key:np.asarray(s.report["causal_pulse"][key],np.float64) for key in SIGNALS} for s in (a,b))
        time = np.asarray(a.report["causal_pulse"]["time_us"],np.float64)
        difference = checked_differences(av,bv)
        bounds = column_bounds(av,bv,a.report["causal_pulse"]["diagnostics"]["total_error_bound"],b.report["causal_pulse"]["diagnostics"]["total_error_bound"])
        lo,hi = gate["start_index"],gate["stop_index_exclusive"]
        pulse = {"difference":{key:value.tolist() for key,value in difference.items()},"bounds":bounds,
            "bound_definition":BOUND_DEFINITION,"full_metrics":rf_metrics(av,bv,difference,time),
            "gate_metrics":rf_metrics(av,bv,difference,time,(lo,hi)),"gate_products":gate_products(av,bv,difference,lo,hi),
            "gate":gate,"diagnostic_scope":DIAGNOSTIC_SCOPE}
        check_arithmetic_environment()
    result = {"request":request.model_dump(mode="json"),"processing_version":PROCESSING_VERSION,"mode":request.mode,
        "source_snapshots":{s.measurement["sha256"]:s.report for s in (a,b)},"source_reference":_reference(a),"source_candidate":_reference(b),
        "compatibility":match,"parameter_differences":parameter_differences(a.report,b.report),"spectrum":spectrum,
        "causal_pulse":pulse,"resources":plan,"warnings":WARNINGS,"arithmetic_policy":arithmetic}
    size = json_measure(result)
    if size["encoded_bytes"]>plan["estimated_report_bytes"] or size["expanded_bytes"]>plan["estimated_report_expanded_bytes"]:
        raise ValueError("SLS comparison result exceeds its admitted serialization forecast.")
    for source in ([a] if a is b else [a,b]):
        current = _source(root,source.identifier,size["expanded_bytes"]+plan["numerical_workspace_bytes"])
        if current.measurement["sha256"] != source.measurement["sha256"]:
            raise ValueError("An SLS source changed during comparison.")
        del current
    return result
