"""Bounded manual SLS spectra and reflected gamma analysis; no acquisition."""
from math import floor

from .sls_reports import json_measure, MAX_REPORT_BYTES, MAX_REPORT_EXPANDED_BYTES, MAX_WORKSPACE_BYTES

PROCESSING_VERSION = "sls-analysis-0.17.0"
MATERIAL_MODEL_VERSION = "single-relaxation-longitudinal-1"
WARNINGS = [
    "Manually assumed scalar longitudinal relaxation parameters, not calibrated HBM or material-library properties; the modulus is not automatically Young's or bulk modulus.",
    "Normal incidence with real lossless exterior media; no shear conversion, lateral scattering, focusing or measured instrument response.",
    "Frequency reflection, transmission, material curves and energy fractions are diagnostics at the saved frequencies; sampled passivity does not prove global passivity or resolve every resonance.",
    "Pressure transmission magnitude may exceed one. The reflected gamma certificate does not certify transmitted RF, internal fields or experimental accuracy.",
    "Gamma recording time and excitation peak latency do not identify unique physical depths. This standalone analysis creates no saved volume, acquisition or filtering job.",
]


def _request(request):
    from .sls_schemas import SLSAnalysisRequest
    return request if isinstance(request, SLSAnalysisRequest) else SLSAnalysisRequest.model_validate(request)


def _record_time(settings):
    count = floor(settings["record_duration_us"]*settings["sample_rate_mhz"]+1e-9)+1
    return [settings["record_start_us"]+i/settings["sample_rate_mhz"] for i in range(count)]


def _frequencies(settings):
    import numpy as np
    return np.linspace(settings["start_mhz"], settings["end_mhz"], settings["samples"], dtype=np.float64).tolist()


def estimate_sls(request):
    from .sls_acoustics import estimate_reflection_spectrum
    request = _request(request)
    frozen = request.model_dump(mode="json")
    size = json_measure(frozen)
    nf, nl = request.spectrum.samples, len(request.stack.layers)
    spectrum = estimate_reflection_spectrum(frozen["stack"], _frequencies(frozen["spectrum"]))
    causal = None
    nt = 0
    if request.causal_pulse is not None:
        from .sls_time import estimate_causal_gamma
        time = _record_time(frozen["causal_pulse"])
        nt = len(time)
        causal = estimate_causal_gamma(frozen["stack"], time, frozen["causal_pulse"])
    # Frequency + two four-field complex spectra + three energy arrays; five
    # material curves per authored layer and four optional recording arrays.
    cells = (12+5*nl)*nf + 3*nl + 4*nt
    encoded = 3*size["encoded_bytes"] + 32*cells + 256*1024
    expanded = 3*size["expanded_bytes"] + 512*cells + 2*1024**2
    numerical = max(spectrum["estimated_peak_bytes"], 0 if causal is None else causal["estimated_peak_bytes"])
    peak = 3*expanded + 12*encoded + numerical + 8*1024**2
    if (encoded > MAX_REPORT_BYTES or expanded > MAX_REPORT_EXPANDED_BYTES or peak > MAX_WORKSPACE_BYTES):
        raise ValueError("SLS analysis exceeds the 16 MiB serialized / 64 MiB expanded report or 256 MiB owned workspace limit. Reduce samples; the stack and tolerance were not changed.")
    return {"kind": "sls_layered_analysis", "processing_version": PROCESSING_VERSION,
        "layer_count": nl, "frequency_samples": nf, "time_samples": nt,
        "material_frequency_work_units": spectrum["material_frequency_work_units"],
        "estimated_rf_work_units": 0 if causal is None else causal["inverse_work_units"],
        "estimated_report_bytes": encoded, "estimated_report_expanded_bytes": expanded,
        "estimated_peak_bytes": peak, "spectrum": spectrum, "causal_pulse": causal,
        "workspace_definition": "Conservative simultaneous numerical kernel, retained result, JSON objects and encoding/HTTP buffers; not total process RSS. Spectra and reflected RF are computed sequentially."}


def analyze_sls(request):
    from .sls_acoustics import reflection_spectrum
    request = _request(request)
    frozen = request.model_dump(mode="json")
    resources = estimate_sls(request)
    spectrum = reflection_spectrum(frozen["stack"], _frequencies(frozen["spectrum"]))
    causal = None
    if request.causal_pulse is not None:
        from .sls_time import causal_gamma_response
        causal = causal_gamma_response(frozen["stack"], _record_time(frozen["causal_pulse"]), frozen["causal_pulse"])
    return {"processing_version": PROCESSING_VERSION, "name": frozen["name"],
        "material_model_version": MATERIAL_MODEL_VERSION, "stack": frozen["stack"],
        "source_status": "manual_assumptions", "spectrum": spectrum, "causal_pulse": causal,
        "resources": resources, "warnings": WARNINGS,
        "provenance": {"request_sha256": json_measure(frozen)["sha256"],
            "material_model_version": MATERIAL_MODEL_VERSION,
            "equation_sources": ["https://doi.org/10.1093/gji/ggz407", "https://arxiv.org/abs/1706.04828",
                "https://arxiv.org/abs/1302.0402", "https://dlmf.nist.gov/5.9.E1",
                "https://flintlib.org/doc/acb.html", "https://flintlib.org/doc/using.html"],
            "input_interpretation": "Exact represented displayed-unit inputs; the numerical kernel encloses GPa-to-Pa, us-to-s, mm-to-m and MHz conversions. No independently rounded SI request is substituted.",
            "exterior_speed_scope": "Exterior-plane scattering uses the real exterior impedances. Incident speed enters only the lossless reflected standoff delay; terminal speed is retained explicit metadata and does not affect this response.",
            "evidence_status": "Assumed scalar material law and synthetic response; numerical enclosures are distinct from physical or calibration accuracy."}}
