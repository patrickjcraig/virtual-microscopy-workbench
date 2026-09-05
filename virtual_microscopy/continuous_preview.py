"""Bounded zero-angle preview/probe for continuous ordered material columns."""

from math import ceil
from time import perf_counter

import numpy as np
from scipy.ndimage import gaussian_filter

from .continuous_sam import (PATH_MODEL, base_metadata, bound_context, column_layout,
                             enforce_resources, path_prepass, resource_plan,
                             synthesize_echo_tile, tile_ranges)
from .physics import DETECTOR_FWHM_MM, MAX_ACQUISITION_US, _bscan_summary, _image_result

MODEL_VERSION = "continuous-preview-0.8.0"


def continuous_preview(twin, settings, *, full_image=True):
    if settings.get("angle_deg", 0) != 0:
        raise ValueError("Continuous material-column preview requires zero-angle normal incidence.")
    started = perf_counter()
    freq, resolution = settings["frequency_mhz"], settings["resolution"]
    p = column_layout(twin, resolution, resolution, settings.get("roi_mm"), freq, DETECTOR_FWHM_MM)
    include_defects = settings.get("include_defects", True)
    context = bound_context(twin, p, include_defects)
    x, y = context[:2]
    px = min(p["before_x"]+resolution-1, max(p["before_x"], int((settings["probe_x_mm"]-p["origin"][0])/p["dx"])))
    py = min(p["before_y"]+resolution-1, max(p["before_y"], int((settings["probe_y_mm"]-p["origin"][1])/p["dy"])))
    # The path-only prepass is separately bounded before any paths are built.
    # It discovers the same full-record latest echo used by the voxel preview.
    probe_row = None if full_image else py
    prepass = resource_plan(p, context, 16 if full_image else 1, 0, 0,
                           probe_row=probe_row, prepass_only=True,
                           extra_base_bytes=p["nx"]*p["ny"]*24)
    enforce_resources(prepass, preview=True)
    diagnostics, latest, optical_depth = path_prepass(twin, x, y, include_defects,
        frequency_mhz=freq, focus_mm=settings["focus_mm"],
        energy_kev=settings["energy_kev"] if full_image else None)
    dt, sigma = 1/(8*freq), .75/freq
    end = min(MAX_ACQUISITION_US, max(settings["gate_end_us"], latest+4*sigma, .2))
    nt, half = ceil(end/dt)+1, ceil(4*sigma/dt)
    time = np.arange(nt, dtype=np.float64)*dt
    extra = p["nx"]*p["ny"]*24+p["nx"]*nt*16+nt*24
    last_error = None
    for chunk in range(16 if full_image else 1, 0, -1):
        plan = resource_plan(p, context, chunk, nt, half, probe_row=probe_row, extra_base_bytes=extra)
        try:
            enforce_resources(plan, preview=True)
            break
        except ValueError as exc:
            last_error = exc
    else:
        raise last_error
    gate = (time >= settings["gate_start_us"]) & (time <= settings["gate_end_us"])
    if not np.any(gate):
        gate[np.argmin(abs(time-(settings["gate_start_us"]+settings["gate_end_us"])/2))] = True
    cscan = np.empty((resolution, resolution), dtype=np.float32) if full_image else None
    ascan = bscan = None
    from .column_paths import build_column_paths, column_acoustic_echoes
    for start, stop, lo, hi in tile_ranges(p, chunk, probe_row):
        paths = build_column_paths(twin, x, y[lo:hi], include_defects)
        echoes = column_acoustic_echoes(paths, freq, settings["focus_mm"])
        del paths
        signal = synthesize_echo_tile(echoes, (hi-lo, p["nx"]), 0., nt, 8*freq, freq, sigma,
                                      (p["sigma_y"], p["sigma_x"]))
        del echoes
        if full_image:
            retained = signal[start-lo:stop-lo, p["before_x"]:p["before_x"]+resolution]
            cscan[start-p["before_y"]:stop-p["before_y"]] = np.abs(retained[:, :, gate]).max(axis=2)
            del retained
        if start <= py < stop:
            line = signal[py-lo]
            envelope = np.abs(line)
            ascan = {"time_us": time.tolist(), "amplitude": line[px].real.astype(float).tolist(),
                     "envelope": envelope[px].astype(float).tolist(),
                     "probe_mm": [settings["probe_x_mm"], settings["probe_y_mm"]],
                     "sampled_probe_mm": [float(x[px]), float(y[py])]}
            bscan = _bscan_summary(envelope[p["before_x"]:p["before_x"]+resolution], time,
                p["roi"][2]-p["roi"][0], float(y[py]), p["roi"][0])
            del line, envelope
        del signal
    metadata = {**base_metadata(twin, p, context, include_defects), **plan,
        "path_diagnostics": diagnostics, "model_version": MODEL_VERSION,
        "seed": settings.get("seed", 42),
        "roi_mm": settings.get("roi_mm"), "acquisition_shape": [resolution, resolution],
        "rf_sample_interval_us": dt, "rf_tile_rows": chunk,
        "acoustic_lateral_fwhm_mm": p["lateral_fwhm"], "xray_detector_fwhm_mm": DETECTOR_FWHM_MM,
        "assumptions": ["Synthetic continuous normal-incidence paths through ordered authored primitives; no experimental calibration.",
            "Full specimen depth, global XY and complete Gaussian lateral halo are retained. Continuous Z interfaces do not remove lateral or RF sampling limitations.",
            "X-ray retains monochromatic Beer-Lambert integration with current material proxies, intensity blur and optional seeded Poisson counts; no scatter, beam hardening or CT.",
            "SAM retains primary scalar longitudinal signed reflection, two-way transmission/loss, F-number 2 focus, sigma=0.75/f complex Gaussian pulse and coherent lateral PSF; no reverberation, refraction, shear or full-wave scattering.",
            "Normal-incidence reflection at curved sphere boundaries is a scalar approximation. Ambient is water for SAM and open-beam referenced for X-ray; explicit air remains distinct.",
            "Preview time zero is the specimen top, excluding transducer standoff. A/B traces retain the complete finite acquisition record; C-scan is the peak analytic envelope inside the requested gate.",
            "Depth samples are inactive for this acquisition path model. Material-section raster views remain independently sampled geometry."]}
    if latest > MAX_ACQUISITION_US:
        metadata["warnings"].append("Echoes later than 12 us are outside the finite acquisition window.")
    if settings["gate_end_us"]-settings["gate_start_us"] < dt:
        metadata["warnings"].append("The acoustic gate is narrower than one RF sample; nearest sample used if necessary.")
    if not full_image:
        metadata["runtime_ms"] = round((perf_counter()-started)*1000, 1)
        return {"ascan": ascan, "bscan": bscan, "metadata": metadata}
    intensity = np.exp(-optical_depth)
    del optical_depth
    detector_sigma = DETECTOR_FWHM_MM/np.sqrt(8*np.log(2))
    intensity = gaussian_filter(intensity, (detector_sigma/p["dy"], detector_sigma/p["dx"]), mode="nearest")
    if settings["noise"]:
        intensity = np.random.default_rng(settings["seed"]).poisson(intensity*settings["photons"])/settings["photons"]
    intensity = intensity[p["before_y"]:p["before_y"]+resolution, p["before_x"]:p["before_x"]+resolution]
    xray = _image_result(intensity, "I / I0", metadata["extent_mm"])
    xray["mean_transmission"] = float(intensity.mean())
    sam = _image_result(cscan, "relative echo amplitude", metadata["extent_mm"])
    sam["peak_amplitude"] = float(cscan.max())
    metadata["runtime_ms"] = round((perf_counter()-started)*1000, 1)
    return {"xray": xray, "sam": sam, "ascan": ascan, "bscan": bscan, "metadata": metadata}
