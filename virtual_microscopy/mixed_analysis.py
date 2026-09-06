"""Bounded manual mixed spectra and reflected gamma analysis; no acquisition."""
from math import floor

from .mixed_reports import json_measure, resource_forecast

PROCESSING_VERSION = "mixed-analysis-0.20.0"
MATERIAL_MODEL_VERSION = "lossless-real-or-single-relaxation-longitudinal-1"
WARNINGS = [
    "Explicit manually assumed lossless real Z/c or scalar longitudinal SLS parameters; no calibration or automatic conversion between layer kinds. SLS modulus is not automatically Young's or bulk modulus.",
    "Normal incidence with real lossless exterior media; no shear conversion, lateral scattering, focusing or measured instrument response.",
    "Frequency reflection, transmission, material curves and energy fractions are diagnostics at the saved frequencies; sampled passivity does not prove global passivity or resolve every resonance.",
    "Pressure transmission magnitude may exceed one. The reflected gamma certificate does not certify transmitted RF, internal fields or experimental accuracy.",
    "Gamma recording time and excitation peak latency do not identify unique physical depths. This standalone analysis creates no saved volume, acquisition or filtering job.",
]


def _request(request):
    from .mixed_schemas import MixedLayeredAnalysisRequest
    return request if isinstance(request, MixedLayeredAnalysisRequest) else MixedLayeredAnalysisRequest.model_validate(request)


def _record_time(settings):
    count = floor(settings["record_duration_us"]*settings["sample_rate_mhz"]+1e-9)+1
    return [settings["record_start_us"]+i/settings["sample_rate_mhz"] for i in range(count)]


def _frequencies(settings):
    import numpy as np
    return np.linspace(settings["start_mhz"], settings["end_mhz"], settings["samples"], dtype=np.float64).tolist()


def estimate_mixed(request):
    from .mixed_acoustics import estimate_reflection_spectrum
    request = _request(request)
    frozen = request.model_dump(mode="json")
    spectrum = estimate_reflection_spectrum(frozen["stack"], _frequencies(frozen["spectrum"]))
    causal = None
    nt = 0
    if request.causal_pulse is not None:
        from .mixed_time import estimate_causal_gamma
        time = _record_time(frozen["causal_pulse"])
        nt = len(time)
        causal = estimate_causal_gamma(frozen["stack"], time, frozen["causal_pulse"])
    return resource_forecast(frozen, spectrum, causal)


def analyze_mixed(request):
    from .mixed_acoustics import reflection_spectrum
    request = _request(request)
    frozen = request.model_dump(mode="json")
    resources = estimate_mixed(request)
    spectrum = reflection_spectrum(frozen["stack"], _frequencies(frozen["spectrum"]))
    causal = None
    if request.causal_pulse is not None:
        from .mixed_time import causal_gamma_response
        causal = causal_gamma_response(frozen["stack"], _record_time(frozen["causal_pulse"]), frozen["causal_pulse"])
    return {"processing_version": PROCESSING_VERSION, "name": frozen["name"],
        "material_model_version": MATERIAL_MODEL_VERSION, "stack": frozen["stack"],
        "source_status": "manual_assumptions", "spectrum": spectrum, "causal_pulse": causal,
        "resources": resources, "warnings": WARNINGS,
        "provenance": {"request_sha256": json_measure(frozen)["sha256"],
            "material_model_version": MATERIAL_MODEL_VERSION,
            "equation_sources": ["https://arxiv.org/abs/1706.04828", "https://dlmf.nist.gov/5.9.E1",
                "https://flintlib.org/doc/acb.html", "https://flintlib.org/doc/using.html"],
            "input_interpretation": "Exact represented displayed-unit inputs; the numerical kernel encloses MRayl-to-Pa-s/m, GPa-to-Pa, us-to-s, mm-to-m and MHz conversions. No independently rounded SI request is substituted.",
            "exterior_speed_scope": "Exterior-plane scattering uses the real exterior impedances. Incident speed enters only the lossless reflected standoff delay; terminal speed is retained explicit metadata and does not affect this response.",
            "evidence_status": "Assumed scalar material law and synthetic response; numerical enclosures are distinct from physical or calibration accuracy."}}
