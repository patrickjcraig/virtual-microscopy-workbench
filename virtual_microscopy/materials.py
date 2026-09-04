"""Small, explicit material library for the reduced-order virtual instruments.

NIST values below are mass attenuation coefficients mu/rho [cm^2/g], NOT
mass energy-absorption coefficients. Only 40--150 keV is supported, above the
tin K edge. Polymer/composite substitutions and all acoustic loss laws are
illustrative, uncalibrated inputs; they are deliberately exposed to clients.
"""

from __future__ import annotations

import numpy as np

ENERGIES_KEV = [40.0, 50.0, 60.0, 80.0, 100.0, 150.0]
_NIST = "https://physics.nist.gov/PhysRefData/XrayMassCoef/"
_ACOUSTICS = "https://adobeassets.evidentscientific.com/content/dam/downloads/276829456/UT_Technical_Notes_201907.pdf"
_SI = [0.7012, 0.4385, 0.3207, 0.2228, 0.1835, 0.1448]
_O = [0.2585, 0.2132, 0.1907, 0.1678, 0.1551, 0.1361]
_PMMA = [0.2350, 0.2074, 0.1924, 0.1751, 0.1641, 0.1456]
# A named and reproducible surrogate, not a claim about a particular FR-4 grade.
_SILICA = (28.0855 * np.array(_SI) + 31.998 * np.array(_O)) / 60.0835
_FR4 = (0.60 * _SILICA + 0.40 * np.array(_PMMA)).tolist()


def _material(identifier, name, color, density, speed, loss, power, mu, xray_note,
              sources, acoustic_note):
    return {
        "id": identifier,
        "name": name,
        "color": color,
        "density_g_cm3": density,
        "sound_speed_m_s": speed,
        "impedance_mrayl": density * speed / 1000.0,
        "attenuation_db_mm_at_50mhz": loss,
        "attenuation_frequency_exponent": power,
        "attenuation_status": "uncalibrated illustrative acoustic loss law",
        "energy_kev": ENERGIES_KEV.copy(),
        "mass_attenuation_cm2_g": list(mu),
        "provenance": {
            "xray": xray_note,
            "xray_sources": sources,
            "acoustic": acoustic_note,
            "acoustic_reference": _ACOUSTICS,
            "calibration": "No specimen or instrument calibration; no measured validation.",
        },
    }


MATERIALS = {
    "silicon": _material(
        "silicon", "Silicon", "#66768e", 2.329, 8430.0, 0.08, 1.5, _SI,
        "Elemental silicon: NIST tabulated mu/rho; log-log interpolation.",
        [_NIST + "ElemTab/z14.html"],
        "Nominal [100] longitudinal speed, treated isotropically; density nominal; "
        "speed and attenuation are uncalibrated model inputs.",
    ),
    "copper": _material(
        "copper", "Copper", "#c47f53", 8.96, 4660.0, 0.12, 1.5,
        [4.862, 2.613, 1.593, 0.7630, 0.4584, 0.2217],
        "Elemental copper: NIST tabulated mu/rho; log-log interpolation.",
        [_NIST + "ElemTab/z29.html"],
        "4660 m/s nominal longitudinal speed from Olympus technical notes; density "
        "nominal; attenuation illustrative, not inferred from the reference.",
    ),
    "solder": _material(
        "solder", "Solder (tin proxy)", "#b6bcc6", 7.31, 3320.0, 0.25, 1.5,
        [19.42, 10.70, 6.564, 3.029, 1.676, 0.6091],
        "Pure tin surrogate for solder, not a calibrated SAC or leaded alloy.",
        [_NIST + "ElemTab/z50.html"],
        "Pure-tin surrogate: 3320 m/s nominal longitudinal speed from Olympus "
        "technical notes; density nominal; attenuation illustrative.",
    ),
    "epoxy": _material(
        "epoxy", "Epoxy (polymer proxy)", "#bcaa89", 1.20, 2600.0, 2.0, 1.2,
        _PMMA,
        "NIST PMMA mass attenuation is an illustrative unfilled-epoxy surrogate; "
        "density is 1.20 g/cm3. Fillers, cure and composition are not modeled.",
        [_NIST + "ComTab/pmma.html"],
        "Density, 2600 m/s longitudinal speed and attenuation are uncalibrated "
        "generic epoxy estimates; no universal epoxy material is implied.",
    ),
    "fr4": _material(
        "fr4", "FR-4 (composite proxy)", "#658878", 1.85, 3000.0, 3.0, 1.2,
        _FR4,
        "Illustrative 60 wt% SiO2 + 40 wt% PMMA mass-mixture surrogate using NIST "
        "Si/O/PMMA; not a manufacturer's FR-4 composition or glass weave.",
        [_NIST + "ElemTab/z14.html", _NIST + "ElemTab/z08.html", _NIST + "ComTab/pmma.html"],
        "Homogeneous isotropic approximation. Density, 3000 m/s speed and "
        "attenuation are uncalibrated estimates; weave/anisotropy are omitted.",
    ),
    "air": _material(
        "air", "Air / void", "#dd7373", 0.001205, 343.0, 10.0, 1.0,
        [0.2485, 0.2080, 0.1875, 0.1662, 0.1541, 0.1356],
        "NIST dry air coefficients with nominal sea-level density; explicit voids only.",
        [_NIST + "ComTab/air.html"],
        "Nominal ambient-air speed/density; highly attenuating loss surrogate. "
        "Explicit air cavities remain air in immersion and strongly reflect ultrasound.",
    ),
}

# Ambient is an internal label, not a seventh importable twin material.
WATER_SOUND_SPEED_M_S = 1480.0
WATER_IMPEDANCE_MRAYL = 1.48
WATER_ATTENUATION_DB_MM_AT_50MHZ = 0.55  # illustrative f^2 loss near room temperature


def linear_attenuation_mm(material_id: str, energy_kev: float) -> float:
    """Return mu in mm^-1 using log-log interpolation within the supplied table."""
    if not 40.0 <= energy_kev <= 150.0:
        raise ValueError("Energy must be between 40 and 150 keV (tabulated range).")
    material = MATERIALS[material_id]
    coefficient = np.exp(np.interp(np.log(energy_kev), np.log(ENERGIES_KEV),
                                  np.log(material["mass_attenuation_cm2_g"])))
    return float(coefficient * material["density_g_cm3"] / 10.0)
