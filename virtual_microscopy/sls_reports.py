"""Bounded immutable manual SLS reports; historical reads need no material kernel."""
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

KIND = "sls_layered_analysis"
PROCESSING_VERSION = "sls-analysis-0.17.0"
STORE_CONTRACT = "sls-standalone-report-1"
MATERIAL_MODEL_VERSION = "single-relaxation-longitudinal-1"
CERTIFICATE_VERSION = "scalar-sls-reflected-gamma-1"
MAX_REPORT_BYTES = 16*1024**2
MAX_REPORT_EXPANDED_BYTES = 64*1024**2
MAX_WORKSPACE_BYTES = 256*1024**2
MAX_CATALOG_FILES = 10_000
CATALOG_ORDER = "id_desc"
IMPLEMENTATION_FILES = ("sls_reports.py", "sls_api.py", "sls_analysis.py", "sls_schemas.py",
    "sls_acoustics.py", "sls_time.py", "layered_time.py", "schemas.py", "causal_comparison_store.py")
_BASE_FIELDS = {"processing_version", "name", "material_model_version", "stack", "source_status", "spectrum",
    "causal_pulse", "resources", "warnings", "provenance"}
_STORED_FIELDS = {"schema_version", "kind", "id", "report_id", "created_at", "request", "request_sha256",
    "stack_sha256", "report_sha256"}


def json_measure(value, byte_limit=None, expanded_limit=None, *, retained_bytes=0):
    measured = _json.json_measure(value, MAX_REPORT_BYTES if byte_limit is None else byte_limit,
        MAX_REPORT_EXPANDED_BYTES if expanded_limit is None else expanded_limit, retained_bytes=retained_bytes)
    if retained_bytes+measured["expanded_bytes"]+12*measured["encoded_bytes"]+16*1024**2 > MAX_WORKSPACE_BYTES:
        raise ValueError("SLS report exceeds the 256 MiB owned workspace limit.")
    return measured


def bounded_payload(value):
    json_measure(value)
    stream = io.BytesIO()
    for part in _json._tokens(value): stream.write(part)
    return stream.getvalue()


def _read_json(path):
    # Conservative parser guard includes retained/encoding space and precedes
    # decoding. The shared absolute guard is 512 MiB; reserving its other half
    # yields this instrument's 256 MiB owned limit without changing old helpers.
    return _json.bounded_json_read(path, MAX_REPORT_BYTES, MAX_REPORT_EXPANDED_BYTES,
        retained_bytes=_json.MAX_WORKSPACE_BYTES-MAX_WORKSPACE_BYTES)[0]


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
    path = Path(__file__).resolve().parents[1]/"docs/SLS_MATERIAL_PROOF.md"
    return {"path": "docs/SLS_MATERIAL_PROOF.md", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _disk(root, required):
    import shutil
    probe = root
    while not probe.exists() and probe != probe.parent: probe = probe.parent
    if shutil.disk_usage(probe).free < required+64*1024**2:
        raise ValueError("Insufficient free disk space for the SLS report and reserve.")


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
    return type(value) is str and 1 <= len(value) <= maximum


def _range(value, lo, hi):
    return _number(value) and lo <= value <= hi


def _provenance(value):
    fingerprints = value.get("implementation_sha256")
    if (type(fingerprints) is not dict or set(fingerprints) != set(IMPLEMENTATION_FILES) or
            any(not _digest(digest) for digest in fingerprints.values())):
        raise ValueError("SLS frozen implementation fingerprints require the complete contract and canonical SHA-256 digests.")
    proof = value.get("proof_document")
    if (type(proof) is not dict or set(proof) != {"path", "sha256"} or
            proof["path"] != "docs/SLS_MATERIAL_PROOF.md" or not _digest(proof["sha256"])):
        raise ValueError("SLS frozen proof identity is malformed.")
    runtime = value.get("runtime")
    if (type(runtime) is not dict or set(runtime) != {"python", "implementation", "system", "machine", "numerical_packages"} or
            any(not _name(runtime[key], 128) for key in ("python", "implementation", "system", "machine"))):
        raise ValueError("SLS frozen runtime identity is malformed.")
    packages = runtime["numerical_packages"]
    if (type(packages) is not dict or set(packages) != {"numpy", "python-flint", "flint", "flint_release"} or
            any(not _name(packages[key], 128) for key in ("numpy", "python-flint", "flint")) or
            type(packages["flint_release"]) is not int or packages["flint_release"] <= 0):
        raise ValueError("SLS frozen numerical package identity is malformed.")


def _admit_estimate(value):
    if type(value) is not dict:
        raise ValueError("SLS analysis requires a frozen resource estimate.")
    for key, limit in (("estimated_report_bytes", MAX_REPORT_BYTES),
        ("estimated_report_expanded_bytes", MAX_REPORT_EXPANDED_BYTES), ("estimated_peak_bytes", MAX_WORKSPACE_BYTES)):
        if type(value.get(key)) is not int or not 0 < value[key] <= limit:
            raise ValueError(f"SLS {key} exceeds the bounded resource limit or is invalid.")
    if (type(value.get("layer_count")) is not int or not 0 <= value["layer_count"] <= 8 or
            type(value.get("frequency_samples")) is not int or not 2 <= value["frequency_samples"] <= 8193 or
            type(value.get("time_samples")) is not int or not 0 <= value["time_samples"] <= 2049):
        raise ValueError("SLS resource dimensions exceed the standalone instrument bounds.")


def _certificate(causal, plan, settings):
    if type(causal) is not dict or set(causal) != {"time_us", "rf", "imaginary", "envelope", "diagnostics"}:
        raise ValueError("SLS causal output must contain only reflected time/real/imaginary/magnitude and diagnostics.")
    if type(plan) is not dict or type(settings) is not dict:
        raise ValueError("SLS reflected certificate requires its frozen plan and settings.")
    for key in ("record_duration_us", "sample_rate_mhz", "record_start_us", "absolute_tolerance"):
        if not _number(settings.get(key), True) or (key != "record_start_us" and settings[key] <= 0):
            raise ValueError("SLS reflected certificate has invalid recording settings.")
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
        raise ValueError("SLS reflected settings are outside the frozen supported contract.")
    count = math.floor(settings["record_duration_us"]*settings["sample_rate_mhz"]+1e-9)+1
    if not 2 <= count <= 2049 or any(not _vector(causal[key], count, nonnegative=key == "envelope")
            for key in ("time_us", "rf", "imaginary", "envelope")):
        raise ValueError("SLS causal output has invalid finite reflected signal arrays.")
    expected_time = [settings["record_start_us"]+i/settings["sample_rate_mhz"] for i in range(count)]
    if any(float(a).hex() != float(b).hex() for a, b in zip(causal["time_us"], expected_time)):
        raise ValueError("SLS reflected recording centers disagree with the frozen request.")
    diagnostics = causal["diagnostics"]
    if (type(diagnostics) is not dict or diagnostics.get("certificate_version") != CERTIFICATE_VERSION or
            plan.get("model_version") != "scalar-sls-causal-gamma-arb-0.17.0" or
            plan.get("material_model_version") != MATERIAL_MODEL_VERSION or
            plan.get("time_samples") != count):
        raise ValueError("SLS reflected certificate version is unsupported.")
    # Planning fields are deterministic, whereas status/timing text changes
    # during synthesis. Published analytic bounds must retain their planned
    # values; a resealed zero alias/cutoff claim cannot pass this check.
    for key, value in plan.items():
        if key not in {"arithmetic_status", "workspace_definition"} and diagnostics.get(key) != value:
            raise ValueError(f"SLS reflected certificate disagrees with planned {key}.")
    keys = ("analytic_alias_bound", "frequency_cutoff_bound", "arithmetic_complex_bound", "arithmetic_envelope_bound")
    if any(not _number(diagnostics.get(key), True) for key in (*keys, "total_error_bound", "requested_tolerance")):
        raise ValueError("SLS reflected certificate bounds must be finite and nonnegative.")
    if diagnostics["analytic_alias_bound"] <= 0 or diagnostics["frequency_cutoff_bound"] <= 0:
        raise ValueError("SLS finite gamma planning requires strictly positive analytic remainder bounds.")
    total, tolerance = diagnostics["total_error_bound"], settings["absolute_tolerance"]
    if (diagnostics["requested_tolerance"] != tolerance or total > tolerance or
            Fraction(total) < sum((Fraction(diagnostics[key]) for key in keys), Fraction())):
        raise ValueError("SLS reflected total must enclose all published components within the requested tolerance.")


def _validate(report, *, historical=False):
    if (type(report) is not dict or set(report) != _BASE_FIELDS | _STORED_FIELDS or
            report.get("kind") != KIND or type(report.get("schema_version")) is not int or report["schema_version"] != 1 or
            type(report.get("processing_version")) is not str or not 1 <= len(report["processing_version"]) <= 128 or
            (not historical and report["processing_version"] != PROCESSING_VERSION)):
        raise ValueError("SLS report identity or standalone output contract is unsupported.")
    request = report["request"]
    if type(request) is not dict or set(request) != {"name", "stack", "spectrum", "causal_pulse"}:
        raise ValueError("SLS report requires only its explicit manual request fields.")
    if (report["name"] != request["name"] or report["stack"] != request["stack"] or
            not _name(request["name"], 160) or not request["name"].strip() or
            report["source_status"] != "manual_assumptions" or report["material_model_version"] != MATERIAL_MODEL_VERSION):
        raise ValueError("SLS report assumptions disagree with its frozen request.")
    _admit_estimate(report["resources"])
    nl, nf, nt = (report["resources"][key] for key in ("layer_count", "frequency_samples", "time_samples"))
    stack, spectrum = report["stack"], report["spectrum"]
    if (type(stack) is not dict or set(stack) != {"incident", "terminal", "layers"} or
            type(stack["layers"]) is not list or len(stack["layers"]) != nl or
            type(request["spectrum"]) is not dict or request["spectrum"].get("samples") != nf or type(spectrum) is not dict):
        raise ValueError("SLS stack/spectrum dimensions disagree with their resource plan.")
    spectrum_fields = {"frequency_mhz", "reflection", "transmission", "reflectance", "transmittance",
        "absorptance", "materials", "phase_magnitude_floor", "reference_planes", "diagnostics"}
    if (set(spectrum) != spectrum_fields or not _number(spectrum["phase_magnitude_floor"], True) or
            not _name(spectrum["reference_planes"], 2048)):
        raise ValueError("SLS spectrum may retain only its supported frequency diagnostics.")
    for medium in (stack["incident"], stack["terminal"]):
        if (type(medium) is not dict or set(medium) != {"name", "impedance_mrayl", "sound_speed_m_s"} or
                not _name(medium["name"]) or not _range(medium["impedance_mrayl"], .0001, 100) or
                not _range(medium["sound_speed_m_s"], 100, 20000)):
            raise ValueError("SLS exterior provenance must retain only its explicit lossless fields.")
    layer_fields = {"name", "thickness_mm", "density_kg_m3", "relaxed_modulus_gpa", "unrelaxed_modulus_gpa", "relaxation_time_us"}
    if any(type(layer) is not dict or set(layer) != layer_fields for layer in stack["layers"]):
        raise ValueError("SLS finite layers require only their explicit single-relaxation fields.")
    for layer in stack["layers"]:
        if (not _name(layer["name"]) or not _range(layer["thickness_mm"], 0, 6) or
                not _range(layer["density_kg_m3"], 1, 30000) or not _range(layer["relaxed_modulus_gpa"], 1e-6, 1000) or
                not _range(layer["unrelaxed_modulus_gpa"], layer["relaxed_modulus_gpa"], 1000) or
                not _range(layer["relaxation_time_us"], 1e-6, 100)):
            raise ValueError("SLS frozen material parameters violate the positive passive single-relaxation contract.")
    if sum((Fraction(layer["thickness_mm"]) for layer in stack["layers"]), Fraction()) > 6:
        raise ValueError("SLS frozen thickness exceeds the 6 mm contract.")
    if (set(request["spectrum"]) != {"start_mhz", "end_mhz", "samples"} or
            not _range(request["spectrum"]["start_mhz"], 0, 300) or
            not _range(request["spectrum"]["end_mhz"], 0, 300)):
        raise ValueError("SLS frozen spectrum request is malformed.")
    spectrum_plan = report["resources"].get("spectrum")
    if (type(spectrum_plan) is not dict or type(spectrum.get("diagnostics")) is not dict or
            spectrum_plan.get("model_version") != "scalar-sls-scattering-arb-0.17.0" or
            spectrum_plan.get("material_model_version") != MATERIAL_MODEL_VERSION or
            any(spectrum["diagnostics"].get(key) != value for key, value in spectrum_plan.items())):
        raise ValueError("SLS spectrum diagnostics disagree with the frozen kernel plan.")
    if not _vector(spectrum.get("frequency_mhz"), nf, nonnegative=True):
        raise ValueError("SLS spectrum requires a finite frequency axis.")
    frequency = spectrum["frequency_mhz"]
    if (any(a >= b for a, b in zip(frequency, frequency[1:])) or
            frequency[0] != request["spectrum"].get("start_mhz") or frequency[-1] != request["spectrum"].get("end_mhz")):
        raise ValueError("SLS spectrum frequency axis disagrees with its frozen request.")
    for name in ("reflection", "transmission"):
        values = spectrum.get(name)
        if (type(values) is not dict or set(values) != {"real", "imag", "magnitude", "phase_deg"} or
                any(not _vector(values[key], nf, nonnegative=key == "magnitude", nullable=key == "phase_deg") for key in values)):
            raise ValueError("SLS spectrum requires finite pressure components/magnitudes and nullable phase.")
    for name in ("reflectance", "transmittance", "absorptance"):
        if not _vector(spectrum.get(name), nf):
            raise ValueError("SLS energy diagnostics must retain finite raw values.")
    materials = spectrum.get("materials")
    if type(materials) is not list or len(materials) != nl:
        raise ValueError("SLS material curves require every authored layer, including zero thickness.")
    for material, layer in zip(materials, stack["layers"]):
        material_fields = {"name", "attenuation_db_mm", "attenuation_np_m", "phase_speed_m_s", "impedance_real_mrayl",
            "impedance_imag_mrayl", "low_frequency_speed_m_s", "high_frequency_speed_m_s", "zero_frequency_impedance_mrayl",
            "zero_relaxation", "zero_thickness"}
        if (type(material) is not dict or set(material) != material_fields or material.get("name") != layer.get("name") or
                type(material["zero_relaxation"]) is not bool or type(material["zero_thickness"]) is not bool or
                material["zero_relaxation"] != (layer["relaxed_modulus_gpa"] == layer["unrelaxed_modulus_gpa"]) or
                material["zero_thickness"] != (layer["thickness_mm"] == 0)):
            raise ValueError("SLS material curve identity disagrees with its authored layer.")
        for key in ("attenuation_db_mm", "attenuation_np_m", "phase_speed_m_s", "impedance_real_mrayl", "impedance_imag_mrayl"):
            if not _vector(material.get(key), nf):
                raise ValueError("SLS material curves must retain finite raw values.")
        for key in ("low_frequency_speed_m_s", "high_frequency_speed_m_s", "zero_frequency_impedance_mrayl"):
            if not _number(material.get(key), True) or material[key] == 0:
                raise ValueError("SLS material limits must be finite positive values.")
    if request["causal_pulse"] is None:
        if report["causal_pulse"] is not None or report["resources"].get("causal_pulse") is not None or nt != 0:
            raise ValueError("Frequency-only SLS reports cannot contain a causal recording.")
    else:
        _certificate(report["causal_pulse"], report["resources"].get("causal_pulse"), request["causal_pulse"])
        if len(report["causal_pulse"]["time_us"]) != nt:
            raise ValueError("SLS recording count disagrees with its resource plan.")
    provenance = report["provenance"]
    if (type(provenance) is not dict or provenance.get("store_contract") != STORE_CONTRACT or
            provenance.get("material_model_version") != MATERIAL_MODEL_VERSION or
            provenance.get("request_sha256") != report["request_sha256"]):
        raise ValueError("SLS frozen provenance contract is inconsistent.")
    _provenance(provenance)


def _account(report, measured):
    estimate = report["resources"]
    if (measured["encoded_bytes"] > estimate["estimated_report_bytes"] or
            measured["expanded_bytes"] > estimate["estimated_report_expanded_bytes"] or
            measured["expanded_bytes"]+12*measured["encoded_bytes"]+16*1024**2 > estimate["estimated_peak_bytes"]):
        raise ValueError("SLS published output exceeds its advertised resource estimate.")


def _summary(report):
    result = {key: report[key] for key in ("id", "report_id", "kind", "created_at", "name", "processing_version", "report_sha256", "source_status", "material_model_version")}
    result.update(layer_count=report["resources"]["layer_count"], frequency_samples=report["resources"]["frequency_samples"],
        time_samples=report["resources"]["time_samples"], causal_pulse_available=report["causal_pulse"] is not None)
    json_measure(result, 64*1024, 1024**2)
    return result


class SLSReportStore:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.directory = self.root/"sls-reports"

    def _path(self, identifier):
        identifier = _json._checked_id(identifier)
        for parent in (self.root, *self.root.parents, self.directory): _json._reject_link(parent)
        path = self.directory/f"{identifier}.json"
        _json._reject_link(path)
        if self.directory.resolve().parent != self.root.resolve() or path.resolve().parent != self.directory.resolve():
            raise ValueError("SLS report path leaves its data directory.")
        return path

    def create(self, request):
        from .sls_analysis import analyze_sls, estimate_sls
        from .sls_schemas import SLSAnalysisRequest
        identifier = str(uuid4())
        path = self._path(identifier)
        if path.exists(): raise FileExistsError(path)
        request = SLSAnalysisRequest.model_validate(request)
        normalized = request.model_dump(mode="json")
        request_sha = json_measure(normalized)["sha256"]
        estimate = estimate_sls(request)
        _admit_estimate(estimate)
        _disk(self.root, estimate["estimated_report_bytes"])
        result = analyze_sls(request)
        json_measure(result)
        if (type(result) is not dict or set(result) != _BASE_FIELDS or result.get("resources") != estimate or
                type(result.get("provenance")) is not dict):
            raise ValueError("SLS analysis must preserve its admitted output/resource contract.")
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

    def read(self, identifier):
        path = self._path(identifier)
        if not path.exists(): raise KeyError(identifier)
        report = _read_json(path)
        measured = json_measure(report)
        _validate(report, historical=True); _account(report, measured)
        if report["id"] != identifier or report["report_id"] != identifier:
            raise ValueError("SLS report identity mismatch.")
        for key, value in (("request_sha256", report["request"]), ("stack_sha256", report["stack"]),
                ("report_sha256", {k: v for k, v in report.items() if k != "report_sha256"})):
            if report[key] != json_measure(value)["sha256"]:
                raise ValueError("SLS frozen report/request/stack checksum mismatch.")
        return report

    def list(self, limit=50, offset=0):
        if (type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset < MAX_CATALOG_FILES):
            raise ValueError("SLS pagination requires limit 1..100 and offset 0..9999.")
        self._path("00000000-0000-0000-0000-000000000000")
        if not self.directory.exists(): return []
        identifiers = []
        with os.scandir(self.directory) as entries:
            for count, entry in enumerate(entries, 1):
                if count > MAX_CATALOG_FILES: raise ValueError("SLS report catalog exceeds its bounded entry limit.")
                if entry.name.startswith(".") and entry.name.endswith(".tmp"): continue
                if not entry.name.endswith(".json"): raise ValueError("Unexpected entry in SLS report catalog.")
                identifier = _json._checked_id(entry.name[:-5])
                if not stat.S_ISREG(self._path(identifier).stat().st_mode):
                    raise ValueError("SLS catalog requires regular report files.")
                identifiers.append(identifier)
        identifiers.sort(reverse=True)
        result = [_summary(self.read(identifier)) for identifier in identifiers[offset:offset+limit]]
        json_measure(result, 8*1024**2, 8*1024**2)
        return result


def sls_report_csv(report):
    if type(report) is not dict or any(not key.isascii() for key in report if type(key) is str):
        raise ValueError("SLS CSV requires ASCII top-level field names.")
    json_measure(report)
    stream = io.BytesIO(); stream.write(b"section,field,value_json\r\n")
    for key in sorted(report):
        stream.write(b'"report","'+key.encode("ascii").replace(b'"', b'""')+b'","')
        for part in _json._tokens(report[key]): stream.write(part.replace(b'"', b'""'))
        stream.write(b'"\r\n')
    return stream.getvalue().decode("ascii")
