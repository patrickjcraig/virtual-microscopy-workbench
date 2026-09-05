"""Quantitative, reduced-order X-ray and pulse-echo acoustic forward models.

The shared representation is a voxel-center sampled material grid [y, x, z].
This is not a full-wave solver, a CT reconstruction, or validated defect NDE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter

import numpy as np
from scipy.ndimage import gaussian_filter
from scipy.signal import fftconvolve

from .materials import (MATERIALS, WATER_ATTENUATION_DB_MM_AT_50MHZ,
                        WATER_IMPEDANCE_MRAYL, WATER_SOUND_SPEED_M_S,
                        linear_attenuation_mm)

MATERIAL_IDS = tuple(MATERIALS)
LABELS = {name: index + 1 for index, name in enumerate(MATERIAL_IDS)}
DETECTOR_FWHM_MM = 0.020
SAM_F_NUMBER = 2.0
ECHO_FLOOR = 1.0e-8
MAX_ACQUISITION_US = 12.0


@dataclass
class MaterialGrid:
    labels: np.ndarray
    size_mm: np.ndarray
    pitch_mm: np.ndarray  # x, y, z order
    warnings: list[str]
    origin_mm: np.ndarray = field(default_factory=lambda: np.zeros(3))
    scan_slices: tuple[slice, slice] | None = None

    @property
    def image_slices(self):
        return self.scan_slices or (slice(0, self.labels.shape[0]), slice(0, self.labels.shape[1]))

    @property
    def image_extent_mm(self):
        ys, xs = self.image_slices
        return [float(self.origin_mm[0] + xs.start * self.pitch_mm[0]),
                float(self.origin_mm[0] + xs.stop * self.pitch_mm[0]),
                float(self.origin_mm[1] + ys.start * self.pitch_mm[1]),
                float(self.origin_mm[1] + ys.stop * self.pitch_mm[1])]


@dataclass
class Echoes:
    rows: np.ndarray
    cols: np.ndarray
    times_us: np.ndarray
    amplitudes: np.ndarray
    max_time_us: float


def reflection_coefficient(z1, z2):
    """Signed normal-incidence pressure reflection coefficient."""
    return (np.asarray(z2) - np.asarray(z1)) / (np.asarray(z2) + np.asarray(z1))


def voxelize(twin: dict, resolution: int | tuple[int, int], include_defects: bool = True,
             roi_mm=None, depth_samples=None, halo_pixels=(0, 0)) -> MaterialGrid:
    """Sample ordered CSG primitives; zero means ambient, never an explicit void."""
    specimen = np.asarray(twin["size_mm"], dtype=float)
    lateral = np.asarray([resolution, resolution] if np.isscalar(resolution) else resolution, dtype=int)
    if lateral.shape != (2,) or np.any(lateral <= 0):
        raise ValueError("Lateral resolution must be a positive integer or (nx, ny) pair.")
    nz = int(depth_samples or 2 * int(lateral.max()))
    bounds = np.asarray(roi_mm if roi_mm is not None else [0, 0, *specimen[:2]], dtype=float)
    if (bounds.shape != (4,) or not np.isfinite(bounds).all() or
            np.any(bounds[:2] < 0) or np.any(bounds[2:] > specimen[:2]) or
            np.any(bounds[2:] <= bounds[:2])):
        raise ValueError("ROI bounds must be finite, ordered and inside the specimen.")
    pitch = np.r_[(bounds[2:] - bounds[:2]) / lateral, specimen[2] / nz]
    padding = np.asarray(halo_pixels, dtype=int)
    before = np.minimum(padding, np.floor(bounds[:2] / pitch[:2] + 1e-9).astype(int))
    after = np.minimum(padding, np.floor((specimen[:2] - bounds[2:]) / pitch[:2] + 1e-9).astype(int))
    nx, ny = (lateral + before + after).tolist()
    if nx * ny * nz > 64_000_000:
        raise ValueError("This geometry grid exceeds 64 million cells. Reduce depth samples or raster size, "
                         "or enlarge a very small ROI to reduce acoustic halo overhead.")
    origin = np.r_[bounds[:2] - before * pitch[:2], 0.0]
    size = pitch * [nx, ny, nz]
    scan = (slice(int(before[1]), int(before[1] + lateral[1])),
            slice(int(before[0]), int(before[0] + lateral[0])))
    labels = np.zeros((ny, nx, nz), dtype=np.uint8)
    x = origin[0] + (np.arange(nx) + 0.5) * pitch[0]
    y = origin[1] + (np.arange(ny) + 0.5) * pitch[1]
    z = (np.arange(nz) + 0.5) * pitch[2]
    warnings = []
    for obj in twin["objects"]:
        if not include_defects and obj["role"] == "defect":
            continue
        center = np.asarray(obj["center_mm"], dtype=float)
        extent = np.asarray(obj["size_mm"], dtype=float)
        lo, hi = center - extent / 2, center + extent / 2
        if np.any(hi <= origin) or np.any(lo >= origin + size):
            continue
        ix = np.flatnonzero((x >= lo[0]) & (x <= hi[0]))
        iy = np.flatnonzero((y >= lo[1]) & (y <= hi[1]))
        iz = np.flatnonzero((z >= lo[2]) & (z <= hi[2]))
        if np.any(extent < 2 * pitch):
            warnings.append(
                f"Object {obj['id']} has an extent below two grid samples; "
                "its shape/thickness can be inaccurate or disappear. Increase resolution."
            )
        if not (len(ix) and len(iy) and len(iz)):
            continue
        sl = np.ix_(iy, ix, iz)
        if obj["shape"] == "box":
            labels[sl] = LABELS[obj["material"]]
        else:
            rx = (x[ix] - center[0])[None, :, None] / (extent[0] / 2)
            ry = (y[iy] - center[1])[:, None, None] / (extent[1] / 2)
            radial = rx * rx + ry * ry
            if obj["shape"] == "sphere":
                rz = (z[iz] - center[2])[None, None, :] / (extent[2] / 2)
                mask = radial + rz * rz <= 1
            else:  # schema has already restricted this to z-axis cylinders
                mask = np.broadcast_to(radial <= 1, (len(iy), len(ix), len(iz)))
            region = labels[sl]
            region[mask] = LABELS[obj["material"]]
            labels[sl] = region
    return MaterialGrid(labels, size, pitch, warnings, origin, scan)


def acquisition_grid(twin: dict, settings: dict) -> MaterialGrid:
    """Sample a full-depth ROI with numerical context for both Gaussian PSFs."""
    roi = settings.get("roi_mm")
    halo = (0, 0)
    if roi is not None:
        if settings.get("angle_deg", 0) != 0:
            raise ValueError("ROI X-ray scans currently require 0° incidence. Use the full specimen for tilted scans.")
        dx, dy = (np.asarray(roi[2:]) - np.asarray(roi[:2])) / settings["resolution"]
        acoustic_fwhm = 1.02 * SAM_F_NUMBER * (WATER_SOUND_SPEED_M_S / 1000) / settings["frequency_mhz"]
        sigma_mm = max(DETECTOR_FWHM_MM, acoustic_fwhm) / np.sqrt(8 * np.log(2))
        halo = tuple(int(4 * sigma_mm / d + 0.5) for d in (dx, dy))
    return voxelize(twin, settings["resolution"], settings["include_defects"],
                    roi_mm=roi, depth_samples=settings.get("depth_samples"), halo_pixels=halo)


def project_xray(grid: MaterialGrid, energy_kev: float, angle_deg: float = 0.0,
                 photons: int = 50000, noise: bool = False, seed: int = 42,
                 detector_fwhm_mm: float = DETECTOR_FWHM_MM) -> np.ndarray:
    """Monochromatic parallel-beam Beer--Lambert projection onto detector x/y.

    The specimen rotates about its center around y. For a detector coordinate u,
    x(z) = cx + (u-cx)/cos(theta) + (z-cz)*tan(theta), ds=dz/cos(theta).
    Sampling attenuation at those coordinates includes side exits and parallax.
    Air attenuation outside primitives is omitted (open-beam referenced).
    """
    ny, nx, nz = grid.labels.shape
    dx, dy, dz = grid.pitch_mm
    lookup = np.array([0.0] + [linear_attenuation_mm(m, energy_kev) for m in MATERIAL_IDS])
    angle = np.deg2rad(angle_deg)
    if abs(angle) < 1e-12:
        optical_depth = lookup[grid.labels].sum(axis=2) * dz
    else:
        cos_a = np.cos(angle)
        detector_x = (np.arange(nx) + 0.5) * dx
        center_x, center_z = grid.size_mm[[0, 2]] / 2
        optical_depth = np.zeros((ny, nx), dtype=float)
        for k in range(nz):
            physical_x = center_x + (detector_x - center_x) / cos_a
            physical_x += ((k + 0.5) * dz - center_z) * np.tan(angle)
            # Piecewise-constant sampled material geometry. Each ray samples one
            # voxel per depth slab; no interpolation invents fractional materials.
            indices = np.floor(physical_x / dx).astype(int)
            inside = (indices >= 0) & (indices < nx)
            optical_depth[:, inside] += lookup[grid.labels[:, indices[inside], k]] * dz / cos_a
    intensity = np.exp(-optical_depth)
    if detector_fwhm_mm > 0:
        sigma = detector_fwhm_mm / np.sqrt(8 * np.log(2))
        intensity = gaussian_filter(intensity, (sigma / dy, sigma / dx), mode="nearest")
    if noise:
        # Expected detector intensity is blurred first, then independent counts
        # sampled. I/I0 may exceed 1 under Poisson noise; never clip the result.
        intensity = np.random.default_rng(seed).poisson(intensity * photons) / photons
    return intensity


def _acoustic_lookup(frequency_mhz: float):
    materials = [MATERIALS[name] for name in MATERIAL_IDS]
    speeds = np.array([WATER_SOUND_SPEED_M_S] + [m["sound_speed_m_s"] for m in materials]) / 1000
    impedance = np.array([WATER_IMPEDANCE_MRAYL] + [m["impedance_mrayl"] for m in materials])
    loss = [WATER_ATTENUATION_DB_MM_AT_50MHZ * (frequency_mhz / 50) ** 2]
    loss += [m["attenuation_db_mm_at_50mhz"] * (frequency_mhz / 50) **
             m["attenuation_frequency_exponent"] for m in materials]
    return speeds, impedance, np.asarray(loss)


def acoustic_echoes(grid: MaterialGrid, frequency_mhz: float, focus_mm: float,
                    apply_focus: bool = True) -> Echoes:
    """Primary echoes at sampled interfaces, with upstream round-trip losses.

    time=0 at the specimen top plane. Interior ambient is immersion water;
    explicit material='air' primitives are non-infiltrated cavities. Each path
    is independent and vertical: no mode conversion, refraction or reverberation.
    """
    speeds, impedance, loss_db_mm = _acoustic_lookup(frequency_mhz)
    ny, nx, nz = grid.labels.shape
    dz = grid.pitch_mm[2]
    current_time = np.zeros((ny, nx))
    path_factor = np.ones((ny, nx))
    previous = np.zeros((ny, nx), dtype=np.uint8)
    rows, cols, times, amplitudes = [], [], [], []
    wavelength_mm = (WATER_SOUND_SPEED_M_S / 1000) / frequency_mhz
    rayleigh_mm = 2 * wavelength_mm * SAM_F_NUMBER ** 2
    max_time = 0.0
    for k in range(nz + 1):
        current = grid.labels[:, :, k] if k < nz else np.zeros((ny, nx), dtype=np.uint8)
        r = reflection_coefficient(impedance[previous], impedance[current])
        focus_gain = 1 / (1 + ((k * dz - focus_mm) / rayleigh_mm) ** 2) if apply_focus else 1
        echo = path_factor * r * focus_gain
        active = np.abs(echo) >= ECHO_FLOOR
        if np.any(active):
            yy, xx = np.nonzero(active)
            rows.append(yy)
            cols.append(xx)
            times.append(current_time[active].copy())
            amplitudes.append(echo[active])
            max_time = max(max_time, float(current_time[active].max()))
        # t12*t21 = (1+r)*(1-r) = 1-r^2 for each traversed interface.
        path_factor *= 1 - r * r
        if k < nz:
            path_factor *= np.exp(-2 * np.log(10) / 20 * loss_db_mm[current] * dz)
            current_time += 2 * dz / speeds[current]
        previous = current
    if not rows:
        return Echoes(np.array([], dtype=int), np.array([], dtype=int), np.array([]), np.array([]), 0.0)
    return Echoes(np.concatenate(rows), np.concatenate(cols), np.concatenate(times),
                  np.concatenate(amplitudes), max_time)


def _bscan_summary(envelope: np.ndarray, time: np.ndarray, size_x: float, y_mm: float,
                   x_origin_mm: float = 0.0) -> dict:
    """Max-pool envelope time bins; signed RF is never decimated into an image."""
    nt = len(time)
    stride = max(1, int(np.ceil(nt / 512)))
    starts = np.arange(0, nt, stride)
    image = np.maximum.reduceat(envelope, starts, axis=1).T
    # Internal edges bisect neighboring RF sample times. Endpoint bins end at
    # the acquisition boundary, retaining positive width even for one final sample.
    edges = np.r_[time[0], (starts[1:] - 0.5) * (time[1] - time[0]), time[-1]]
    return {
        "image": image.astype(float).tolist(),
        "extent": [float(x_origin_mm), float(x_origin_mm + size_x), 0.0, float(time[-1])],
        "unit": "relative echo amplitude",
        "y_mm": float(y_mm),
        "time_reduction": "maximum analytic envelope per time bin",
        "time_bin_edges_us": edges.tolist(),
        "rf_samples_per_time_bin": stride,
    }


def _sam_signals(grid: MaterialGrid, settings: dict, full_image: bool) -> dict:
    freq = settings["frequency_mhz"]
    echoes = acoustic_echoes(grid, freq, settings["focus_mm"])
    dt = 1.0 / (8 * freq)
    sigma_t = 0.75 / freq
    end = min(MAX_ACQUISITION_US, max(settings["gate_end_us"], echoes.max_time_us + 4 * sigma_t, 0.2))
    nt = int(np.ceil(end / dt)) + 1
    time = np.arange(nt) * dt
    half = int(np.ceil(4 * sigma_t / dt))
    nt_work = nt + 2 * half  # retain pulse tails at both acquisition boundaries
    pulse_t = np.arange(-half, half + 1) * dt
    wavelet = (np.exp(-0.5 * (pulse_t / sigma_t) ** 2) *
               np.exp(2j * np.pi * freq * pulse_t)).astype(np.complex64)
    ny, nx, _ = grid.labels.shape
    dx, dy, _ = grid.pitch_mm
    lateral_fwhm = 1.02 * SAM_F_NUMBER * (WATER_SOUND_SPEED_M_S / 1000) / freq
    sigma_x = lateral_fwhm / np.sqrt(8 * np.log(2)) / dx
    sigma_y = lateral_fwhm / np.sqrt(8 * np.log(2)) / dy
    # scipy truncates its Gaussian at 4 sigma; exact halo avoids tile seams.
    halo = int(4 * sigma_y + 0.5)
    scan_y, scan_x = grid.image_slices
    px = min(scan_x.stop - 1, max(scan_x.start, int((settings["probe_x_mm"] - grid.origin_mm[0]) / dx)))
    py = min(scan_y.stop - 1, max(scan_y.start, int((settings["probe_y_mm"] - grid.origin_mm[1]) / dy)))
    cscan = np.zeros((ny, nx), dtype=np.float32) if full_image else None
    gate = (time >= settings["gate_start_us"]) & (time <= settings["gate_end_us"])
    if not np.any(gate):
        # A mathematically narrower-than-one-sample gate is evaluated at its
        # nearest sample and called out in metadata, instead of an empty max.
        gate[np.argmin(abs(time - (settings["gate_start_us"] + settings["gate_end_us"]) / 2))] = True
    # Compute only returned scan rows, while retaining the complete spatial
    # halo for every tile. A soft tile-size target can otherwise force one-row
    # cores when the halo alone exceeds it, repeating that halo excessively.
    # Choose the largest bounded core under the existing hard work limits;
    # the full RF time record and pulse support are unchanged.
    for chunk in range(min(16, scan_y.stop - scan_y.start) if full_image else 1, 0, -1):
        starts = range(scan_y.start, scan_y.stop, chunk) if full_image else [py]
        tile_cells = []
        for start in starts:
            stop = min(start + chunk, scan_y.stop) if full_image else py + 1
            tile_cells.append((min(ny, stop + halo) - max(0, start - halo)) * nx * nt_work)
        if max(tile_cells) <= 8_000_000 and sum(tile_cells) <= 180_000_000:
            break
    else:
        raise ValueError(
            "This specimen/acquisition exceeds the local RF computation budget. "
            "Reduce resolution, shorten the gate end, or reduce acoustic frequency; "
            "a very small lateral specimen may also require a higher frequency. "
            "No acquisition settings were changed automatically."
        )
    ascan = bscan = None
    for start in starts:
        stop = min(start + chunk, scan_y.stop) if full_image else py + 1
        lo, hi = max(0, start - halo), min(ny, stop + halo)
        cube = np.zeros((hi - lo, nx, nt_work), dtype=np.complex64)
        chosen = ((echoes.rows >= lo) & (echoes.rows < hi) &
                  (echoes.times_us <= time[-1] + 4 * sigma_t))
        erow, ecol = echoes.rows[chosen] - lo, echoes.cols[chosen]
        position = echoes.times_us[chosen] / dt + half
        bins = np.floor(position).astype(int)
        fraction = position - bins
        amp = echoes.amplitudes[chosen]
        # Phase-aware fractional deposition interpolates the pulse envelope
        # without the spurious carrier attenuation of real linear deposition.
        valid = bins < nt_work
        w0 = amp * (1 - fraction) * np.exp(-2j * np.pi * freq * dt * fraction)
        np.add.at(cube, (erow[valid], ecol[valid], bins[valid]), w0[valid])
        valid = bins + 1 < nt_work
        w1 = amp * fraction * np.exp(2j * np.pi * freq * dt * (1 - fraction))
        np.add.at(cube, (erow[valid], ecol[valid], bins[valid] + 1), w1[valid])
        rf = fftconvolve(cube, wavelet[None, None, :], mode="same", axes=2)
        # Blur complex pressure, not the envelope. This preserves cancellation
        # between unresolved interfaces and neighboring columns.
        rf = gaussian_filter(rf, (sigma_y, sigma_x, 0), mode="nearest")
        rf = rf[start - lo:stop - lo, :, half:half + nt]
        envelope = np.abs(rf)
        if full_image:
            cscan[start:stop] = envelope[:, :, gate].max(axis=2)
        if start <= py < stop:
            line = rf[py - start]
            line_envelope = envelope[py - start]
            ascan = {
                "time_us": time.tolist(),
                "amplitude": line[px].real.astype(float).tolist(),
                "envelope": line_envelope[px].astype(float).tolist(),
                "probe_mm": [settings["probe_x_mm"], settings["probe_y_mm"]],
                "sampled_probe_mm": [float(grid.origin_mm[0] + (px + 0.5) * dx),
                                     float(grid.origin_mm[1] + (py + 0.5) * dy)],
            }
            extent = grid.image_extent_mm
            bscan = _bscan_summary(line_envelope[scan_x], time, extent[1] - extent[0],
                                  grid.origin_mm[1] + (py + 0.5) * dy, extent[0])
    warnings = []
    if echoes.max_time_us > MAX_ACQUISITION_US:
        warnings.append("Echoes later than 12 us are outside the finite acquisition window.")
    if settings["gate_end_us"] - settings["gate_start_us"] < dt:
        warnings.append("The acoustic gate is narrower than one RF time sample; nearest sample used if necessary.")
    if max(dx, dy) > lateral_fwhm / 2:
        warnings.append("The lateral grid undersamples the modeled acoustic focal spot; pixel pitch limits resolved detail.")
    return {"image": cscan, "ascan": ascan, "bscan": bscan, "warnings": warnings,
            "rf_sample_interval_us": dt, "acoustic_lateral_fwhm_mm": lateral_fwhm,
            "rf_tile_rows": chunk, "rf_max_tile_work_cells": max(tile_cells),
            "rf_work_cells": sum(tile_cells)}


def _image_result(image: np.ndarray, unit: str, extent) -> dict:
    return {"image": image.astype(float).tolist(), "unit": unit,
            "extent_mm": list(extent),
            "min": float(image.min()), "max": float(image.max())}


def simulate(twin: dict, settings: dict) -> dict:
    """Compute both registered modalities from a validated twin and settings."""
    start = perf_counter()
    grid = acquisition_grid(twin, settings)
    xray = project_xray(grid, settings["energy_kev"], settings["angle_deg"],
                       settings["photons"], settings["noise"], settings["seed"])
    sam = _sam_signals(grid, settings, full_image=True)
    xray = xray[grid.image_slices]
    sam["image"] = sam["image"][grid.image_slices]
    warnings = grid.warnings + sam["warnings"]
    if settings["angle_deg"]:
        warnings.append("Tilted X-ray pixels are detector projection coordinates; they are not exactly co-registered "
                        "with the specimen x/y acoustic scan. The fixed detector extent can crop the projection.")
    xray_result = _image_result(xray, "I / I0", grid.image_extent_mm)
    xray_result["mean_transmission"] = float(xray.mean())
    sam_result = _image_result(sam["image"], "relative echo amplitude", grid.image_extent_mm)
    sam_result["peak_amplitude"] = float(sam["image"].max())
    return {
        "xray": xray_result, "sam": sam_result, "ascan": sam["ascan"], "bscan": sam["bscan"],
        "metadata": {
            "runtime_ms": round((perf_counter() - start) * 1000, 1),
            "grid_shape": list(grid.labels.shape),
            "grid_origin_mm": grid.origin_mm.tolist(),
            "acquisition_shape": list(xray.shape),
            "roi_mm": settings.get("roi_mm"),
            "pixel_pitch_um": (grid.pitch_mm[:2] * 1000).tolist(),
            "voxel_depth_um": float(grid.pitch_mm[2] * 1000),
            "seed": settings["seed"], "model_version": "0.7.0", "warnings": warnings,
            "rf_sample_interval_us": sam["rf_sample_interval_us"],
            "rf_tile_rows": sam["rf_tile_rows"],
            "rf_max_tile_work_cells": sam["rf_max_tile_work_cells"],
            "rf_work_cells": sam["rf_work_cells"],
            "acoustic_lateral_fwhm_mm": sam["acoustic_lateral_fwhm_mm"],
            "xray_detector_fwhm_mm": DETECTOR_FWHM_MM,
            "assumptions": [
                "Synthetic reduced-order forward simulation; no experimental validation or calibration.",
                "Shared voxel-center sampled geometry: later primitives overwrite earlier; pixel pitch is not physical resolution.",
                "ROI scans retain the complete specimen depth and a lateral Gaussian PSF halo; ROI X-ray incidence is restricted to 0 degrees. Geometry depth samples can be set independently from the lateral raster.",
                "X-ray: monochromatic parallel beams, NIST mass attenuation, Beer-Lambert line integral; no scatter, beam hardening or CT reconstruction.",
                "X-ray detector: illustrative 20 um FWHM Gaussian PSF and optional seeded Poisson photon counts; values are not clipped or normalized per image.",
                "Solder uses pure tin; epoxy uses PMMA attenuation; FR-4 uses an illustrative 60 wt% silica/40 wt% PMMA mixture.",
                "SAM: immersion water surrounds primitives; explicit air voids remain air; time zero is specimen top plane, excluding transducer standoff.",
                "SAM: normal-incidence primary longitudinal echoes with signed pressure reflection, round-trip transmission and frequency-dependent attenuation; no multiple reflections, refraction, shear conversion or full-wave scattering.",
                "SAM acoustic speeds/densities are nominal inputs and loss laws are uncalibrated estimates; interfaces are treated as perfectly bonded except explicit voids.",
                "SAM finite bandwidth: analytic Gaussian cosine pulse with sigma=0.75/f; eight RF samples per period and phase-aware fractional delays.",
                "SAM illustrative focus model: F-number 2, lateral Gaussian FWHM=1.02*F#*water wavelength, depth gain=1/(1+((z-focus)/(2*wavelength*F#^2))^2).",
                "SAM lateral PSF smooths complex pressure before envelope; Cscan is peak analytic envelope in the selected gate; Bscan is envelope maximum per time bin.",
                "Echo amplitudes below 1e-8 are omitted and acquisition ends at 12 us; probe coordinates sample the nearest material-grid column.",
            ],
        },
    }


def probe(twin: dict, settings: dict) -> dict:
    """Synthesize a local RF strip for linked A/B inspection without a full Cscan."""
    grid = acquisition_grid(twin, settings)
    sam = _sam_signals(grid, settings, full_image=False)
    return {"ascan": sam["ascan"], "bscan": sam["bscan"]}
