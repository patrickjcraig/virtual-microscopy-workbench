"""Immutable saved SLS comparisons; historical inspection uses frozen JSON only."""
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

KIND = "sls_analysis_comparison"
PROCESSING_VERSION = "sls-comparison-0.18.0"
STORE_CONTRACT = "sls-comparison-report-1"
MAX_SOURCE_MANIFEST_BYTES = 16*1024**2
MAX_SOURCE_EXPANDED_BYTES = 64*1024**2
MAX_REPORT_BYTES = 32*1024**2
MAX_REPORT_EXPANDED_BYTES = 128*1024**2
MAX_WORKSPACE_BYTES = 512*1024**2
MAX_CATALOG_FILES = 10_000
MAX_PAGE_SIZE = 100
CATALOG_ORDER = "id_desc"
BOUND_KEYS = ("source_sum", "complex_arithmetic", "complex_total", "magnitude_arithmetic", "magnitude_total")
IMPLEMENTATION_FILES = ("sls_comparison_store.py", "sls_comparison_api.py", "sls_comparisons.py",
    "sls_comparison_schemas.py", "sls_comparison_math.py", "causal_comparison_math.py",
    "causal_comparison_store.py", "sls_reports.py")
# Frozen v1 semantics: historical validation does not import the current service.
POLICY = "same_sls_exteriors_excitation_v1"
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

BASE_FIELDS = {"processing_version", "request", "mode", "source_snapshots", "source_reference",
    "source_candidate", "compatibility", "parameter_differences", "spectrum", "causal_pulse",
    "resources", "warnings", "arithmetic_policy"}
STORED_FIELDS = {"schema_version", "kind", "id", "comparison_id", "created_at", "request_sha256",
    "report_sha256", "store_identity"}


def json_measure(value, byte_limit=None, expanded_limit=None, *, retained_bytes=0):
    return _json.json_measure(value, MAX_REPORT_BYTES if byte_limit is None else byte_limit,
        MAX_REPORT_EXPANDED_BYTES if expanded_limit is None else expanded_limit, retained_bytes=retained_bytes)


def bounded_json_read(path, byte_limit=None, expanded_limit=None, *, retained_bytes=0):
    """Guard expansion before decoding, including any already retained source."""
    report, _ = _json.bounded_json_read(path,
        MAX_SOURCE_MANIFEST_BYTES if byte_limit is None else byte_limit,
        MAX_SOURCE_EXPANDED_BYTES if expanded_limit is None else expanded_limit, retained_bytes=retained_bytes)
    measured = json_measure(report, MAX_SOURCE_MANIFEST_BYTES if byte_limit is None else byte_limit,
        MAX_SOURCE_EXPANDED_BYTES if expanded_limit is None else expanded_limit, retained_bytes=retained_bytes)
    return report, measured


def bounded_payload(value):
    json_measure(value)
    stream = io.BytesIO()
    for part in _json._tokens(value): stream.write(part)
    return stream.getvalue()


def validate_source(report, expected_id=None):
    """Validate the unchanged frozen SLS contract, never its current solver."""
    from . import sls_reports
    measured = json_measure(report, MAX_SOURCE_MANIFEST_BYTES, MAX_SOURCE_EXPANDED_BYTES)
    try:
        sls_reports._validate(report, historical=True)
        sls_reports._account(report, measured)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as exc:
        raise ValueError("SLS comparison source has a malformed frozen contract.") from exc
    identifier = _json._checked_id(report["id"])
    if report["report_id"] != identifier or (expected_id is not None and identifier != expected_id):
        raise ValueError("SLS comparison source report identity mismatch.")
    for field, value in (("request_sha256", report["request"]), ("stack_sha256", report["stack"]),
            ("report_sha256", {k: v for k, v in report.items() if k != "report_sha256"})):
        if report[field] != json_measure(value, MAX_SOURCE_MANIFEST_BYTES, MAX_SOURCE_EXPANDED_BYTES)["sha256"]:
            raise ValueError("SLS comparison frozen source checksum mismatch.")
    return measured


def _fingerprints():
    return {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in IMPLEMENTATION_FILES}


def _runtime():
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
        "system": platform.system(), "machine": platform.machine(), "numerical_packages": {"numpy": version("numpy")}}


def _disk(root, required):
    import shutil
    probe = root
    while not probe.exists() and probe != probe.parent: probe = probe.parent
    if shutil.disk_usage(probe).free < required+64*1024**2:
        raise ValueError("Insufficient free disk space for the SLS comparison and reserve.")


def _digest(value):
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _number(value, nonnegative=False):
    try:
        return type(value) in (int, float) and math.isfinite(value) and (not nonnegative or value >= 0)
    except OverflowError:
        return False


def _vector(value, count):
    return type(value) is list and len(value) == count and all(_number(v) for v in value)


def _same_floats(a, b):
    return len(a) == len(b) and all(float(x).hex() == float(y).hex() for x, y in zip(a, b))


def _validate_identity(identity):
    if (type(identity) is not dict or set(identity) != {"contract", "implementation_sha256", "runtime"} or
            identity["contract"] != STORE_CONTRACT):
        raise ValueError("SLS comparison store identity is unsupported.")
    fingerprints = identity["implementation_sha256"]
    if (type(fingerprints) is not dict or set(fingerprints) != set(IMPLEMENTATION_FILES) or
            any(not _digest(value) for value in fingerprints.values())):
        raise ValueError("SLS comparison implementation fingerprints are incomplete or invalid.")
    runtime = identity["runtime"]
    if (type(runtime) is not dict or set(runtime) != {"python", "implementation", "system", "machine", "numerical_packages"} or
            any(type(runtime[k]) is not str or not 1 <= len(runtime[k]) <= 128
                for k in ("python", "implementation", "system", "machine")) or
            type(runtime["numerical_packages"]) is not dict or set(runtime["numerical_packages"]) != {"numpy"} or
            type(runtime["numerical_packages"]["numpy"]) is not str or
            not 1 <= len(runtime["numerical_packages"]["numpy"]) <= 128):
        raise ValueError("SLS comparison runtime provenance is malformed.")


def _no_duplicate_snapshots(report):
    frames = [iter(v for k, v in report.items() if k != "source_snapshots")]
    while frames:
        try: value = next(frames[-1])
        except StopIteration:
            frames.pop(); continue
        if type(value) is dict:
            if "source_snapshots" in value or value.get("kind") == "sls_layered_analysis" or {"stack_sha256", "spectrum"} <= value.keys():
                raise ValueError("Complete SLS sources belong only in the top-level snapshot table.")
            frames.append(iter(value.values()))
        elif type(value) is list: frames.append(iter(value))


def _residual(actual, a, b):
    if not _vector(actual, len(a)) or len(a) != len(b):
        raise ValueError("SLS comparison residual dimensions are inconsistent.")
    # Rational conversion establishes nearest binary64 independently of NumPy
    # or the current producer. Zero's sign follows IEEE subtraction explicitly.
    for saved, av, bv in zip(actual, a, b):
        exact = Fraction(bv)-Fraction(av)
        if exact == 0:
            expected = -0.0 if float(bv).hex().startswith('-') and bv == 0 and av == 0 and not float(av).hex().startswith('-') else 0.0
        else:
            try: expected = float(exact)
            except OverflowError as exc: raise ValueError("SLS residual overflow.") from exc
        if float(saved).hex() != expected.hex():
            raise ValueError("SLS comparison residual disagrees with frozen B-minus-A inputs.")


def _bounds(bounds, a, b):
    if type(bounds) is not dict or set(bounds) != set(BOUND_KEYS) or any(not _number(bounds[k], True) for k in BOUND_KEYS):
        raise ValueError("SLS RF comparisons require five finite nonnegative full-record bounds.")
    source = Fraction(a["diagnostics"]["total_error_bound"])+Fraction(b["diagnostics"]["total_error_bound"])
    unit = Fraction(1, 1 << 53); tiny = Fraction(1, 1 << 1074)
    rhos = {key: unit*(max(abs(Fraction(x)) for x in a[key])+max(abs(Fraction(x)) for x in b[key]))+tiny
        for key in ("rf", "imaginary", "envelope")}
    minimum = {"source_sum": source, "complex_arithmetic": rhos["rf"]+rhos["imaginary"],
        "magnitude_arithmetic": rhos["envelope"],
        "complex_total": Fraction(bounds["source_sum"])+Fraction(bounds["complex_arithmetic"]),
        "magnitude_total": Fraction(bounds["source_sum"])+Fraction(bounds["magnitude_arithmetic"])}
    if any(Fraction(bounds[k]) < value for k, value in minimum.items()):
        raise ValueError("SLS RF residual bound under-reports its frozen sources or subtraction arithmetic.")


def _validate_content(report, *, stored=False):
    if (type(report) is not dict or set(report) != BASE_FIELDS | (STORED_FIELDS if stored else set()) or
            type(report["processing_version"]) is not str or not 1 <= len(report["processing_version"]) <= 128 or
            (not stored and report["processing_version"] != PROCESSING_VERSION)):
        raise ValueError("SLS comparison output contract is unsupported.")
    request = report["request"]
    if (type(request) is not dict or set(request) != {"reference_report_id", "candidate_report_id", "mode", "gate_start_us", "gate_end_us", "frequency_index", "time_index"} or
            report["mode"] not in ("spectrum_only", "spectrum_and_reflected_rf") or request.get("mode") != report["mode"]):
        raise ValueError("SLS comparison requires its frozen mode/request.")
    for key in ("reference_report_id", "candidate_report_id"): _json._checked_id(request[key])
    for key, maximum in (("frequency_index", 8192), ("time_index", 2048)):
        if request[key] is not None and (type(request[key]) is not int or not 0 <= request[key] <= maximum):
            raise ValueError("SLS comparison cursor must be a bounded integer index.")
    if ((request["gate_start_us"] is None) != (request["gate_end_us"] is None) or
            any(value is not None and (not _number(value) or not 0 <= value <= 12)
                for value in (request["gate_start_us"], request["gate_end_us"])) or
            (report["mode"] == "spectrum_only" and (request["gate_start_us"] is not None or request["time_index"] is not None))):
        raise ValueError("SLS comparison recording scope is invalid.")
    snapshots = report["source_snapshots"]
    if type(snapshots) is not dict or not 1 <= len(snapshots) <= 2:
        raise ValueError("SLS comparison requires one or two unique source snapshots.")
    seen_ids = set()
    for digest, source in snapshots.items():
        if not _digest(digest) or validate_source(source)["sha256"] != digest:
            raise ValueError("SLS comparison source snapshot checksum mismatch.")
        if source["id"] in seen_ids:
            raise ValueError("One SLS report cannot have two frozen identities in a comparison.")
        seen_ids.add(source["id"])
    roles = []
    referenced = set()
    for role in ("reference", "candidate"):
        value = report[f"source_{role}"]
        if type(value) is not dict or set(value) != {"report_id", "name", "snapshot_sha256", "report_sha256"}:
            raise ValueError("SLS source references must remain lightweight identities.")
        digest = value["snapshot_sha256"]
        if not _digest(digest) or digest not in snapshots: raise ValueError("SLS source reference has no snapshot.")
        source = snapshots[digest]
        if (value["report_id"] != source["id"] or value["name"] != source["name"] or
                value["report_sha256"] != source["report_sha256"] or request.get(f"{role}_report_id") != source["id"]):
            raise ValueError("SLS source reference disagrees with its complete frozen source.")
        referenced.add(digest); roles.append(source)
    if referenced != set(snapshots): raise ValueError("SLS comparison cannot retain unused snapshots.")
    _no_duplicate_snapshots(report)
    a, b = roles
    _compatibility(report["compatibility"], a, b, report["mode"])
    _parameter_differences(report["parameter_differences"], a, b)
    af, bf = a["spectrum"], b["spectrum"]
    if not _same_floats(af["frequency_mhz"], bf["frequency_mhz"]):
        raise ValueError("SLS comparison saved frequency centers differ.")
    spectrum = report["spectrum"]
    if type(spectrum) is not dict or set(spectrum) != {"difference", "metrics", "diagnostic_scope"}:
        raise ValueError("SLS comparison spectrum output is malformed.")
    differences = spectrum["difference"]
    if type(differences) is not dict or set(differences) != {"reflection", "transmission", "reflectance", "transmittance", "absorptance"}:
        raise ValueError("SLS comparison spectral products are unsupported.")
    for key in ("reflection", "transmission"):
        if type(differences[key]) is not dict or set(differences[key]) != {"real", "imag", "magnitude"}:
            raise ValueError("SLS comparison cannot subtract wrapped phases.")
        for component in differences[key]: _residual(differences[key][component], af[key][component], bf[key][component])
    for key in ("reflectance", "transmittance", "absorptance"): _residual(differences[key], af[key], bf[key])
    nf = len(af["frequency_mhz"])
    if request["frequency_index"] is not None and request["frequency_index"] >= nf:
        raise ValueError("SLS frequency cursor exceeds actual saved samples.")
    _spectral_metrics(spectrum["metrics"], differences, af["frequency_mhz"])
    causal = report["causal_pulse"]
    if report["mode"] == "spectrum_only":
        if causal is not None or report["arithmetic_policy"] is not None:
            raise ValueError("Spectrum-only comparisons cannot publish RF or its certificate.")
    else:
        if (type(causal) is not dict or set(causal) != {"difference", "bounds", "bound_definition", "full_metrics", "gate_metrics", "gate_products", "gate", "diagnostic_scope"} or
                a["causal_pulse"] is None or b["causal_pulse"] is None):
            raise ValueError("SLS RF comparison requires both frozen reflected recordings.")
        ap, bp = a["causal_pulse"], b["causal_pulse"]
        if not _same_floats(ap["time_us"], bp["time_us"]): raise ValueError("SLS comparison time centers differ.")
        if request["time_index"] is not None and request["time_index"] >= len(ap["time_us"]):
            raise ValueError("SLS time cursor exceeds actual saved samples.")
        if type(causal["difference"]) is not dict or set(causal["difference"]) != {"rf", "imaginary", "envelope"}:
            raise ValueError("SLS RF comparison requires signed real, imaginary and saved-magnitude differences.")
        for key in causal["difference"]: _residual(causal["difference"][key], ap[key], bp[key])
        _bounds(causal["bounds"], ap, bp)
        _gate(causal["gate"], request, ap["time_us"])
        _rf_metrics(causal["full_metrics"], causal["difference"], ap["time_us"], 0, len(ap["time_us"]))
        _rf_metrics(causal["gate_metrics"], causal["difference"], ap["time_us"], causal["gate"]["start_index"], causal["gate"]["stop_index_exclusive"])
        _gate_products(causal["gate_products"], ap, bp, causal["gate"])
        policy = report["arithmetic_policy"]
        if type(policy) is not dict or policy.get("contract") != "numpy-binary64-nearest-gradual-v1" or policy.get("environment_changed") is not False:
            raise ValueError("SLS RF comparison subtraction policy is unsupported.")
    if (type(report["compatibility"]) is not dict or type(report["parameter_differences"]) is not list or
            type(report["warnings"]) is not list or any(type(w) is not str for w in report["warnings"])):
        raise ValueError("SLS comparison requires explicit compatibility and differences.")
    resources = report["resources"]
    if type(resources) is not dict:
        raise ValueError("SLS comparison requires its resource admission record.")
    for key, limit in (("estimated_report_bytes", MAX_REPORT_BYTES), ("estimated_report_expanded_bytes", MAX_REPORT_EXPANDED_BYTES),
            ("estimated_peak_bytes", MAX_WORKSPACE_BYTES)):
        if type(resources.get(key)) is not int or not 0 < resources[key] <= limit:
            raise ValueError("SLS comparison resource estimate is invalid or exceeds its ceiling.")
    if stored:
        if report["kind"] != KIND or type(report["schema_version"]) is not int or report["schema_version"] != 1:
            raise ValueError("SLS comparison kind or schema is unsupported.")
        _validate_identity(report["store_identity"])
        _json._checked_id(report["id"])
        if report["comparison_id"] != report["id"]:
            raise ValueError("SLS comparison publication IDs disagree.")
        try:
            created = datetime.fromisoformat(report["created_at"])
            if created.tzinfo is None: raise ValueError
        except (ValueError, TypeError) as exc:
            raise ValueError("SLS comparison publication timestamp must include its timezone.") from exc


def _exact(a, b):
    return float(a).hex() == float(b).hex() if type(a) in (int, float) and type(b) in (int, float) else type(a) is type(b) and a == b


def _compatibility(record, a, b, mode):
    matched = []
    def require(field, left, right):
        if not _exact(left, right): raise ValueError(f"Frozen SLS compatibility mismatch: {field}.")
        matched.append(field)
    for source in (a, b):
        spec = source["spectrum"]
        if (spec["reference_planes"] != REFERENCE_PLANES or not _exact(spec["phase_magnitude_floor"], 1e-12) or
                any(not _exact(spec["diagnostics"].get(k), v) for k, v in SPECTRUM_SEMANTICS.items())):
            raise ValueError("Frozen SLS spectral semantics are unsupported.")
    for name in NUMERICAL_FILES:
        require("implementation."+name, a["provenance"]["implementation_sha256"][name], b["provenance"]["implementation_sha256"][name])
    require("proof_document", a["provenance"]["proof_document"], b["provenance"]["proof_document"])
    for side in ("incident", "terminal"):
        for key in ("impedance_mrayl", "sound_speed_m_s"):
            require(f"stack.{side}.{key}", a["stack"][side][key], b["stack"][side][key])
    if not _same_floats(a["spectrum"]["frequency_mhz"], b["spectrum"]["frequency_mhz"]):
        raise ValueError("Frozen SLS frequency axes differ.")
    matched.append("frequency_mhz")
    if mode == "spectrum_and_reflected_rf":
        if a["causal_pulse"] is None or b["causal_pulse"] is None:
            raise ValueError("Frozen SLS RF comparison requires two reflected recordings.")
        for source in (a, b):
            if any(not _exact(source["causal_pulse"]["diagnostics"].get(k), v) for k, v in PULSE_SEMANTICS.items()):
                raise ValueError("Frozen SLS reflected excitation semantics are unsupported.")
        if not _same_floats(a["causal_pulse"]["time_us"], b["causal_pulse"]["time_us"]):
            raise ValueError("Frozen SLS time axes differ.")
        matched.append("time_us")
        for key in ("center_frequency_mhz", "fractional_bandwidth", "gamma_order", "surface_standoff_mm",
                "sample_rate_mhz", "record_start_us", "record_duration_us"):
            require("causal_pulse."+key, a["request"]["causal_pulse"][key], b["request"]["causal_pulse"][key])
    expected = {"policy": POLICY, "matched_fields": matched, "mode": mode,
        "source_semantics": "Frozen source-to-source identities; current installed kernels are not consulted.",
        "material_correspondence": "None inferred. Layer changes are positional authored differences."}
    if record != expected: raise ValueError("Frozen SLS comparison compatibility record is inconsistent.")


def _parameter_differences(changes, a, b):
    expected = []
    def walk(left, right, path):
        if type(left) is dict and type(right) is dict:
            for key in sorted(left.keys() | right.keys()): walk(left.get(key), right.get(key), path+"."+key)
        elif type(left) is list and type(right) is list:
            for i in range(max(len(left), len(right))):
                walk(left[i] if i < len(left) else None, right[i] if i < len(right) else None, f"{path}[{i}]")
        elif not _exact(left, right):
            expected.append({"path": path, "reference": left, "candidate": right,
                "meaning": "Positional authored change; no inferred layer correspondence or physical attribution."})
    walk(a["request"], b["request"], "request")
    walk(a["provenance"]["runtime"], b["provenance"]["runtime"], "provenance.runtime")
    walk(a["provenance"]["implementation_sha256"], b["provenance"]["implementation_sha256"], "provenance.implementation_sha256")
    if changes != expected: raise ValueError("SLS comparison parameter differences omit or misstate frozen changes.")


def _metric(metric, values, axis, axis_name, lo, hi, *, imaginary=None, rf=False):
    complex_value = imaginary is not None
    fields = {"sample_count", "rmse", "relative_l2", "relative_l2_reason", "max_absolute_difference", "max_location"}
    if not complex_value: fields |= {"bias", "mae"}
    if rf: fields.add("reference_l2")
    if rf and complex_value: fields |= {"bias_real", "bias_imaginary"}
    if type(metric) is not dict or set(metric) != fields or type(metric["sample_count"]) is not int or metric["sample_count"] != hi-lo:
        raise ValueError("SLS comparison metric shape/count is malformed.")
    for key in fields-{"max_location", "relative_l2_reason", "relative_l2", "sample_count"}:
        if not _number(metric[key], key not in ("bias", "bias_real", "bias_imaginary")):
            raise ValueError("SLS comparison metric requires finite, correctly signed diagnostics.")
    if metric["relative_l2"] is None:
        if type(metric["relative_l2_reason"]) is not str or not metric["relative_l2_reason"]:
            raise ValueError("Undefined SLS relative metrics require their reason.")
    elif not _number(metric["relative_l2"], True) or metric["relative_l2_reason"] is not None:
        raise ValueError("SLS relative metric/reason is inconsistent.")
    location = metric["max_location"]
    index_key, value_key = ("time_index", "time_us") if axis_name == "time" else ("frequency_index", "frequency_mhz")
    location_keys = {index_key, value_key} | ({"signed_real_difference", "signed_imaginary_difference"} if complex_value else {"signed_difference"})
    if type(location) is not dict or set(location) != location_keys or type(location[index_key]) is not int or not lo <= location[index_key] < hi:
        raise ValueError("SLS maximum locator is outside its saved selection.")
    index = location[index_key]
    if not _exact(location[value_key], axis[index]): raise ValueError("SLS maximum locator coordinate is inconsistent.")
    if complex_value:
        if not _exact(location["signed_real_difference"], values[index]) or not _exact(location["signed_imaginary_difference"], imaginary[index]):
            raise ValueError("SLS complex locator loses the signed residual components.")
        magnitudes = [math.hypot(values[i], imaginary[i]) for i in range(lo, hi)]
        peak = max(magnitudes)
        # Hypot is an ordinary saved diagnostic; NumPy/libm can differ by a few
        # ulps. Do not promote this historical plausibility check to a certificate.
        tolerance = 4*math.ulp(peak)
        if abs(metric["max_absolute_difference"]-peak) > tolerance or peak-magnitudes[index-lo] > tolerance:
            raise ValueError("SLS complex maximum diagnostic is inconsistent.")
    else:
        expected = max(range(lo, hi), key=lambda i: abs(values[i]))
        if index != expected or not _exact(location["signed_difference"], values[index]) or not _exact(metric["max_absolute_difference"], abs(values[index])):
            raise ValueError("SLS component maximum must retain the first signed maximum.")


def _spectral_metrics(metrics, differences, axis):
    if type(metrics) is not dict or set(metrics) != set(differences): raise ValueError("SLS spectral metric products are malformed.")
    for key in ("reflection", "transmission"):
        if type(metrics[key]) is not dict or set(metrics[key]) != {"real", "imag", "magnitude", "complex"}:
            raise ValueError("SLS spectral metrics require all components and complex diagnostics.")
        for component in ("real", "imag", "magnitude"):
            _metric(metrics[key][component], differences[key][component], axis, "frequency", 0, len(axis))
        _metric(metrics[key]["complex"], differences[key]["real"], axis, "frequency", 0, len(axis), imaginary=differences[key]["imag"])
    for key in ("reflectance", "transmittance", "absorptance"):
        _metric(metrics[key], differences[key], axis, "frequency", 0, len(axis))


def _rf_metrics(metrics, difference, axis, lo, hi):
    if (type(metrics) is not dict or set(metrics) != {"sample_count", "diagnostic_scope", "rf", "imaginary", "envelope", "complex"} or
            type(metrics["sample_count"]) is not int or metrics["sample_count"] != hi-lo):
        raise ValueError("SLS reflected metrics have invalid dimensions.")
    for key in ("rf", "imaginary", "envelope"): _metric(metrics[key], difference[key], axis, "time", lo, hi, rf=True)
    _metric(metrics["complex"], difference["rf"], axis, "time", lo, hi, imaginary=difference["imaginary"], rf=True)


def _gate_products(products, a, b, gate):
    if type(products) is not dict or set(products) != {"peak_envelope", "rms_rf", "residual_gate"}:
        raise ValueError("SLS gate reductions are malformed.")
    for key in ("peak_envelope", "rms_rf"):
        product = products[key]
        if type(product) is not dict or set(product) != {"reference", "candidate", "difference", "difference_definition", "diagnostic_scope"}:
            raise ValueError("SLS gate product must distinguish statistic differences from residual statistics.")
        if any(not _number(product[field], field != "difference") for field in ("reference", "candidate", "difference")):
            raise ValueError("SLS gate statistics are invalid.")
        _residual([product["difference"]], [product["reference"]], [product["candidate"]])
    lo, hi = gate["start_index"], gate["stop_index_exclusive"]
    if any(not _exact(products["peak_envelope"][role], max(source["envelope"][lo:hi])) for role, source in (("reference", a), ("candidate", b))):
        raise ValueError("SLS gate peak disagrees with the saved magnitude.")
    residual = products["residual_gate"]
    if (type(residual) is not dict or set(residual) != {"rms_rf", "peak_absolute_saved_magnitude_difference", "definition", "diagnostic_scope"} or
            any(not _number(residual[key], True) for key in ("rms_rf", "peak_absolute_saved_magnitude_difference"))):
        raise ValueError("SLS residual gate statistics are malformed.")


def _gate(gate, request, time):
    if type(gate) is not dict or set(gate) != {"requested_start_us", "requested_end_us", "start_index", "stop_index_exclusive",
            "sample_count", "actual_start_us", "actual_end_us", "selection"}:
        raise ValueError("SLS comparison inclusive gate contract is invalid.")
    start = time[0] if request["gate_start_us"] is None else request["gate_start_us"]
    end = time[-1] if request["gate_end_us"] is None else request["gate_end_us"]
    if not _number(start) or not _number(end) or not time[0] <= start <= end <= time[-1]:
        raise ValueError("SLS comparison gate is outside the saved record.")
    selected = [i for i, t in enumerate(time) if start <= t <= end]
    if not selected: raise ValueError("SLS comparison gate contains no saved samples.")
    expected = {"requested_start_us": start, "requested_end_us": end, "start_index": selected[0],
        "stop_index_exclusive": selected[-1]+1, "sample_count": len(selected), "actual_start_us": time[selected[0]],
        "actual_end_us": time[selected[-1]]}
    if (any(gate[k] != v for k, v in expected.items()) or
            any(type(gate[k]) is not int for k in ("start_index", "stop_index_exclusive", "sample_count")) or
            gate["selection"] != "Exact inclusive comparisons against actual saved time centers; no clipping or interpolation."):
        raise ValueError("SLS comparison gate disagrees with its exact saved centers.")


def _account(report, measured):
    resource = report["resources"]
    if (resource["estimated_report_bytes"] < measured["encoded_bytes"] or
            resource["estimated_report_expanded_bytes"] < measured["expanded_bytes"] or
            resource["estimated_peak_bytes"] < measured["expanded_bytes"]+12*measured["encoded_bytes"]+16*1024**2):
        raise ValueError("SLS comparison actual output exceeds its advertised resource admission.")


def _summary(report):
    value = {key: report[key] for key in ("id", "comparison_id", "kind", "created_at", "processing_version", "report_sha256",
        "mode", "source_reference", "source_candidate")}
    source = report["source_snapshots"][report["source_reference"]["snapshot_sha256"]]
    value.update(name=f'{report["source_candidate"]["name"]} minus {report["source_reference"]["name"]}',
        frequency_samples=len(source["spectrum"]["frequency_mhz"]),
        time_samples=len(source["causal_pulse"]["time_us"]) if report["causal_pulse"] else 0,
        gate=report["causal_pulse"]["gate"] if report["causal_pulse"] else None)
    json_measure(value, 64*1024, 1024**2)
    return value


class SLSComparisonStore:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.directory = self.root/"sls-comparisons"

    def _path(self, identifier):
        identifier = _json._checked_id(identifier)
        for parent in (self.root, *self.root.parents, self.directory): _json._reject_link(parent)
        path = self.directory/f"{identifier}.json"
        _json._reject_link(path)
        if self.directory.resolve().parent != self.root.resolve() or path.resolve().parent != self.directory.resolve():
            raise ValueError("SLS comparison path leaves its data directory.")
        return path

    def create(self, request):
        from .sls_comparisons import compute_sls_comparison
        identifier = str(uuid4()); path = self._path(identifier)
        if path.exists(): raise FileExistsError(path)
        result = compute_sls_comparison(self.root, request)
        json_measure(result); _validate_content(result)
        report = {**result, "schema_version": 1, "kind": KIND, "id": identifier, "comparison_id": identifier,
            "created_at": datetime.now(timezone.utc).isoformat(), "request_sha256": json_measure(result["request"])["sha256"],
            "store_identity": {"contract": STORE_CONTRACT, "implementation_sha256": _fingerprints(), "runtime": _runtime()}}
        report["report_sha256"] = json_measure(report)["sha256"]
        measured = json_measure(report); _validate_content(report, stored=True); _account(report, measured); _summary(report)
        _disk(self.root, measured["encoded_bytes"])
        self.directory.mkdir(parents=True, exist_ok=True); path = self._path(identifier)
        temporary = self.directory/f".{identifier}.tmp"; staged = False
        try:
            with temporary.open("xb") as stream:
                staged = True
                for part in _json._tokens(report): stream.write(part)
                stream.flush(); os.fsync(stream.fileno())
            self._path(identifier); os.link(temporary, path)
        finally:
            if staged: temporary.unlink(missing_ok=True)
        return report

    def read(self, identifier):
        path = self._path(identifier)
        if not path.exists(): raise KeyError(identifier)
        report, _ = bounded_json_read(path, MAX_REPORT_BYTES, MAX_REPORT_EXPANDED_BYTES)
        measured = json_measure(report); _validate_content(report, stored=True); _account(report, measured)
        if report["id"] != identifier or report["comparison_id"] != identifier:
            raise ValueError("SLS comparison report identity mismatch.")
        if (report["request_sha256"] != json_measure(report["request"])["sha256"] or
                report["report_sha256"] != json_measure({k: v for k, v in report.items() if k != "report_sha256"})["sha256"]):
            raise ValueError("SLS comparison report/request checksum mismatch.")
        return report

    def list(self, limit=50, offset=0):
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE or type(offset) is not int or not 0 <= offset < MAX_CATALOG_FILES:
            raise ValueError("SLS comparison pagination requires limit 1..100 and offset 0..9999.")
        self._path("00000000-0000-0000-0000-000000000000")
        if not self.directory.exists(): return []
        identifiers = []
        with os.scandir(self.directory) as entries:
            for count, entry in enumerate(entries, 1):
                if count > MAX_CATALOG_FILES: raise ValueError("SLS comparison catalog exceeds its bounded entry limit.")
                if entry.name.startswith(".") and entry.name.endswith(".tmp"): continue
                if not entry.name.endswith(".json"): raise ValueError("Unexpected entry in SLS comparison catalog.")
                identifier = _json._checked_id(entry.name[:-5])
                if not stat.S_ISREG(self._path(identifier).stat().st_mode): raise ValueError("SLS comparison catalog requires regular files.")
                identifiers.append(identifier)
        identifiers.sort(reverse=True)
        result = [_summary(self.read(identifier)) for identifier in identifiers[offset:offset+limit]]
        json_measure(result, 8*1024**2, 8*1024**2)
        return result

    def view(self, identifier, *, frequency_index=None, time_index=None):
        report = self.read(identifier)
        return comparison_view(report, frequency_index=frequency_index, time_index=time_index)


def comparison_view(report, *, frequency_index=None, time_index=None):
    """Exact-index readouts only; gates remain immutable and plots use snapshots."""
    a, b = (report["source_snapshots"][report[f"source_{role}"]["snapshot_sha256"]] for role in ("reference", "candidate"))
    fi = report["request"].get("frequency_index") if frequency_index is None else frequency_index
    fi = 0 if fi is None else fi
    if type(fi) is not int or not 0 <= fi < len(a["spectrum"]["frequency_mhz"]):
        raise ValueError("SLS frequency cursor is outside the saved spectrum.")
    def at_frequency(source):
        spectrum = source["spectrum"]
        return {**{key: {component: values[fi] for component, values in spectrum[key].items()}
            for key in ("reflection", "transmission")},
            **{key: spectrum[key][fi] for key in ("reflectance", "transmittance", "absorptance")},
            "materials": [{key: value[fi] if type(value) is list else value for key, value in material.items()}
                for material in spectrum["materials"]]}
    difference = report["spectrum"]["difference"]
    result = {"id": report["id"], "comparison_id": report["comparison_id"], "kind": KIND,
        "source_reference": report["source_reference"], "source_candidate": report["source_candidate"],
        "frequency_index": fi, "frequency_mhz": a["spectrum"]["frequency_mhz"][fi], "time_index": None, "time_us": None,
        "parameter_differences": report["parameter_differences"],
        "spectrum": {"reference": at_frequency(a), "candidate": at_frequency(b),
            "difference": {key: {k: v[fi] for k, v in value.items()} if type(value) is dict else value[fi]
                for key, value in difference.items()}}, "causal_pulse": None}
    if report["causal_pulse"] is None:
        if time_index is not None: raise ValueError("Spectrum-only comparisons have no RF cursor.")
    else:
        ti = report["request"].get("time_index") if time_index is None else time_index
        ti = 0 if ti is None else ti
        if type(ti) is not int or not 0 <= ti < len(a["causal_pulse"]["time_us"]):
            raise ValueError("SLS time cursor is outside the saved recording.")
        result["causal_pulse"] = {"time_index": ti, "time_us": a["causal_pulse"]["time_us"][ti],
            "reference": {key: a["causal_pulse"][key][ti] for key in ("rf", "imaginary", "envelope")},
            "candidate": {key: b["causal_pulse"][key][ti] for key in ("rf", "imaginary", "envelope")},
            "difference": {key: values[ti] for key, values in report["causal_pulse"]["difference"].items()},
            "bounds": report["causal_pulse"]["bounds"], "gate": report["causal_pulse"]["gate"]}
        result["time_index"] = ti; result["time_us"] = a["causal_pulse"]["time_us"][ti]
    json_measure(result, 1024**2, 4*1024**2)
    return result


def sls_comparison_csv(report):
    json_measure(report)
    stream = io.BytesIO(); stream.write(b"section,field,value_json\r\n")
    for key in sorted(report):
        if not key.isascii(): raise ValueError("SLS comparison CSV requires ASCII top-level keys.")
        stream.write(b'"report","'+key.encode("ascii").replace(b'"', b'""')+b'","')
        for part in _json._tokens(report[key]): stream.write(part.replace(b'"', b'""'))
        stream.write(b'"\r\n')
    return stream.getvalue().decode("ascii")
