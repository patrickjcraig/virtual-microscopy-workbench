"""One-column spectra and distinct certified pulse models; no SAM acquisition."""
from copy import deepcopy
from math import floor

import numpy as np

from .column_paths import MATERIAL_IDS, build_column_paths
from .comparisons import _json_workspace
from .datasets import canonical_json, json_sha256
from .layered_schemas import LayeredAnalysisRequest, LayeredColumnRequest
from .materials import MATERIALS, WATER_IMPEDANCE_MRAYL, WATER_SOUND_SPEED_M_S
from .recipes import _json_differences

PROCESSING_VERSION = "layered-analysis-0.12.0"
MAX_PEAK_BYTES = 256*1024**2
MAX_REPORT_BYTES = 16*1024**2
MAX_REPORT_EXPANDED_BYTES = 64*1024**2
MAX_SPECTRUM_WORK = 5_000_000
MAX_RF_WORK = 25_000_000
PHASE_MAGNITUDE_FLOOR = 1e-12


def _medium(material_id):
    if material_id == "water":
        return {"name": "Water", "impedance_mrayl": WATER_IMPEDANCE_MRAYL,
                "sound_speed_m_s": WATER_SOUND_SPEED_M_S}
    material = MATERIALS[material_id]
    return {"name": material["name"], "impedance_mrayl": material["impedance_mrayl"],
            "sound_speed_m_s": material["sound_speed_m_s"]}


def extract_layered_column(request):
    request = request if isinstance(request, LayeredColumnRequest) else LayeredColumnRequest.model_validate(request)
    frozen = request.model_dump(mode="json")
    paths = build_column_paths(frozen["twin"], [request.x_mm], [request.y_mm], request.include_defects)
    if len(paths.z_end_mm) > 256:
        raise ValueError("This full column exceeds 256 finite layers. Choose another column; layers were not dropped.")
    layers, segments, start = [], [], 0.
    for end, label in zip(paths.z_end_mm, paths.material_label):
        identifier = "water" if label == 0 else MATERIAL_IDS[int(label)-1]
        layer = {**_medium(identifier), "thickness_mm": float(end-start),
                 "pressure_loss_db_mm": 0., "material_id": identifier}
        layers.append(layer)
        segments.append({"z_start_mm": start, "z_end_mm": float(end), **layer})
        start = float(end)
    stack = {"incident": _medium("water"), "terminal": _medium("water"), "layers": layers}
    return {"stack": stack, "source_column": frozen, "segments": segments,
            "diagnostics": paths.diagnostics, "source_twin_sha256": json_sha256(frozen["twin"]),
            "stack_sha256": json_sha256(stack), "assumptions": [
                "Complete continuous material column from specimen top to bottom, with original primitive precedence; ambient water and explicit air are distinct.",
                "Nominal material impedances and longitudinal speeds. Extraction defaults every layer to lossless; the existing illustrative frequency loss laws are not applied.",
                "One geometrical column is not a focused acoustic beam, a saved SAM volume or a measurement of feature resolution."]}


def _parameters(stack):
    return ([layer.thickness_mm for layer in stack.layers],
            [stack.incident.impedance_mrayl]+[layer.impedance_mrayl for layer in stack.layers]+[stack.terminal.impedance_mrayl],
            [layer.sound_speed_m_s for layer in stack.layers],
            [layer.pressure_loss_db_mm for layer in stack.layers])


def _pulse_series(request):
    from .layered_acoustics import slab_impulse_series
    layer, pulse = request.stack.layers[0], request.pulse
    return slab_impulse_series(layer.thickness_mm, _parameters(request.stack)[1], layer.sound_speed_m_s,
                               layer.pressure_loss_db_mm, absolute_tolerance=pulse.absolute_tolerance,
                               max_echoes=pulse.max_echoes)


def _record_time(settings):
    count = floor(settings.record_duration_us*settings.sample_rate_mhz+1e-9)+1
    return settings.record_start_us+np.arange(count, dtype=np.float64)/settings.sample_rate_mhz


def estimate_layered(request):
    request = request if isinstance(request, LayeredAnalysisRequest) else LayeredAnalysisRequest.model_validate(request)
    frozen = request.model_dump(mode="json")
    input_bytes = len(canonical_json(frozen))
    input_workspace = _json_workspace(canonical_json(frozen))
    nf, nl = request.spectrum.samples, len(request.stack.layers)
    spectrum_work = nf*(nl+1)
    if spectrum_work > MAX_SPECTRUM_WORK:
        raise ValueError("Layered spectrum exceeds five million layer-frequency units. Reduce requested samples or layers.")
    nt = echoes = rf_work = 0
    causal_estimate = None
    series = None
    if request.pulse is not None:
        nt = floor(request.pulse.record_duration_us*request.pulse.sample_rate_mhz+1e-9)+1
        series = _pulse_series(request)
        echoes = len(series.times_us)
        # This bounds direct synthesis even when every pulse overlaps every
        # recorded sample. The kernel may evaluate fewer compact-support terms.
        rf_work = nt*(echoes+2)  # Also synthesize the independent primary baseline.
        if rf_work > MAX_RF_WORK:
            raise ValueError("Certified slab RF exceeds 25 million echo-sample units. Shorten the record or relax its explicit tail tolerance; echoes were not silently dropped.")
    elif request.causal_pulse is not None:
        from .layered_time import estimate_causal_gamma
        time = _record_time(request.causal_pulse)
        nt = len(time)
        causal_estimate = estimate_causal_gamma(frozen["stack"], time.tolist(), frozen["causal_pulse"])
        rf_work = causal_estimate["inverse_work_units"]
    # All four complex responses retain real, imaginary, magnitude and phase;
    # the spectrum also retains its frequency and three energy arrays.
    cells = 20*nf + (4 if causal_estimate is not None else 5)*nt + 2*echoes
    serialized = 4*input_bytes + 32*cells + 128*1024
    expanded = 4*input_workspace + 512*cells + 1024*1024
    peak = 2*expanded + 4*input_workspace + nf*384 + nt*128 + echoes*64 + 32*1024**2
    if causal_estimate is not None:
        peak += causal_estimate["estimated_peak_bytes"]
    if serialized > MAX_REPORT_BYTES or expanded > MAX_REPORT_EXPANDED_BYTES:
        raise ValueError("Layered report exceeds its 16 MiB serialized / 64 MiB expanded budget. Reduce frequency or time samples; the selected physical stack was not changed.")
    if peak > MAX_PEAK_BYTES:
        raise ValueError("Layered analysis exceeds its 256 MiB estimated numerical/serialization workspace.")
    return {"kind": "layered_acoustic_analysis", "layer_count": nl, "frequency_samples": nf,
            "spectrum_work_units": spectrum_work, "time_samples": nt, "impulse_echo_count": echoes,
            "estimated_rf_work_units": rf_work, "estimated_report_bytes": serialized,
            "estimated_report_expanded_bytes": expanded, "estimated_peak_bytes": peak,
            "causal_pulse": causal_estimate,
            "slab_tail_certificate": None if series is None else series.diagnostics,
            "workspace_definition": "Conservative Python/JSON report copies, spectrum and pulse scratch; causal mode also includes the bounded Arb coefficient/polynomial workspace. Not total process RSS."}


def _response(values):
    magnitude = np.abs(values)
    phase = np.angle(values, deg=True).astype(object)
    phase[magnitude < PHASE_MAGNITUDE_FLOOR] = None
    return {"real": values.real.tolist(), "imag": values.imag.tolist(), "magnitude": magnitude.tolist(),
            "phase_deg": phase.tolist()}


def analyze_layered(request):
    from .layered_acoustics import layered_response, slab_rf_response
    request = request if isinstance(request, LayeredAnalysisRequest) else LayeredAnalysisRequest.model_validate(request)
    estimate = estimate_layered(request)
    frozen = request.model_dump(mode="json")
    source = extract_layered_column(request.source_column) if request.source_column is not None else None
    stack = frozen["stack"]
    differences = _json_differences(source["stack"], stack) if source else []
    status = "manual_assumptions" if source is None else "modified_from_extracted_column" if differences else "matches_extracted_column"
    f = np.linspace(request.spectrum.start_mhz, request.spectrum.end_mhz, request.spectrum.samples, dtype=np.float64)
    thickness, impedance, speed, loss = _parameters(request.stack)
    response = layered_response(thickness, impedance, speed, f, loss)
    spectrum = {"frequency_mhz": f.tolist(),
                **{name: _response(getattr(response, name)) for name in
                   ("reflection", "transmission", "primary_reflection", "direct_transmission")},
                **{name: getattr(response, name).tolist() for name in ("reflectance", "transmittance", "absorptance")},
                "diagnostics": response.diagnostics,
                "phase_magnitude_floor": PHASE_MAGNITUDE_FLOOR,
                "phase_definition": "Wrapped phase in degrees; null below the declared pressure-magnitude floor. No unwrapping or group-delay inference.",
                "reference_planes": "Reflection at the incident face z=0; transmission at the terminal face after all finite layers. External standoff is excluded from these spectra.",
                "sampling": "Coefficients evaluated at the requested discrete frequencies; between-sample resonances are not certified as resolved."}
    pulse = None
    if request.pulse is not None:
        p = request.pulse
        time = _record_time(p)
        surface_time = 2000*p.surface_standoff_mm/request.stack.incident.sound_speed_m_s
        result = slab_rf_response(thickness[0], impedance, speed[0], time, p.center_frequency_mhz,
                                 p.fractional_bandwidth, loss[0], absolute_tolerance=p.absolute_tolerance,
                                 max_echoes=p.max_echoes, surface_time_us=surface_time)
        pulse = {name: getattr(result, name).tolist() for name in ("time_us", "rf", "envelope", "primary_rf", "primary_envelope")}
        pulse.update(echoes={"time_us": (result.series.times_us+surface_time).tolist(),
                             "amplitudes": result.series.amplitudes.tolist()}, diagnostics=result.diagnostics,
                     surface_time_us=surface_time,
                     time_definition="Absolute time from the transducer reference; standoff is a lossless incident-medium round trip with a nonreflecting receiver. Later multiples do not identify unique depths.")
    causal_pulse = None
    if request.causal_pulse is not None:
        from .layered_time import causal_gamma_response
        causal_pulse = causal_gamma_response(stack, _record_time(request.causal_pulse).tolist(), frozen["causal_pulse"])
    warnings = ["Synthetic scalar normal-incidence pressure response with explicit positive real impedances; no shear, oblique beam, lateral scattering, focusing or calibrated instrument response.",
                "Primary reflection and direct transmission are approximation baselines. Energy conservation/passivity applies to the full coherent response only.",
                "Constant per-layer pressure loss is an explicitly assumed nondispersive model, not the material library's frequency-dependent attenuation law.",
                "Discrete frequency samples do not certify resolution of narrow resonances. Refine sampling independently when inspecting spectra.",
                "This is a standalone normal-incidence column instrument; saved SAM volumes retain their existing primary-interface response."]
    if source:
        warnings.extend(source["assumptions"])
    if differences:
        warnings.append("The edited layer stack differs from its extracted source column; results use the explicit edited assumptions.")
    if pulse:
        warnings.append("The finite-support Gaussian pulse is an assumed excitation. Its omitted-echo certificate is distinct from pulse support truncation and experimental accuracy.")
    if causal_pulse:
        warnings.extend([
            "The causal gamma excitation has an order-dependent delay to its peak and an infinite future tail; it is a different waveform from the finite Gaussian slab pulse.",
            "The causal response error bound combines analytic alias and frequency omission with validated arithmetic and output rounding. It bounds the declared synthetic model, not measured device accuracy.",
            "Later reverberations do not identify unique physical depths. No primary RF baseline or time-to-depth mapping is supplied for this causal response."])
    return {"processing_version": PROCESSING_VERSION, "name": request.name, "stack": stack,
            "source_status": status, "source_column": source, "stack_differences": differences,
            "spectrum": spectrum, "pulse": pulse, "causal_pulse": causal_pulse, "resources": estimate, "warnings": warnings,
            "provenance": {"request_sha256": json_sha256(frozen),
                "materials": deepcopy(MATERIALS) if source else None,
                "equation_sources": [
                    "https://live.ocw.mit.edu/courses/6-013-electromagnetics-and-applications-spring-2009/d3be4ea78b036a6362230fb41780cf54_MIT6_013S09_notes.pdf#page=406",
                    "https://publications-cnrc.canada.ca/eng/view/object/?id=056a54bb-ab25-4f71-8161-43b193d53a23"] + ([
                    "https://dlmf.nist.gov/5.9.E1",
                    "https://www.columbia.edu/~ww2040/IEOR3106F06/ExtraCreditLectureLT.pdf",
                    "https://flintlib.org/doc/using.html"] if causal_pulse is not None else []),
                "evidence_status": "Assumed numerical model; no experimental calibration or accuracy claim."}}
