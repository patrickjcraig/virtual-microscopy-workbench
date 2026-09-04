"""Material sections of validated HBM assemblies, distinct from microscope data."""

from typing import Literal

import numpy as np
from pydantic import Field

from .materials import MATERIALS
from .physics import LABELS, MATERIAL_IDS
from .schemas import StrictModel, Twin


class HBMSectionRequest(StrictModel):
    twin: Twin
    assembly_id: str = Field(min_length=1, max_length=64)
    axis: Literal["xz", "yz"] = "xz"
    resolution: Literal[128, 256, 512] = 512
    include_defects: bool = True


def material_section(request: HBMSectionRequest) -> dict:
    """Point-sample ordered primitives on a physical section through a stack.

    This does not integrate attenuation, apply a PSF, or reconstruct a volume.
    It includes all package objects intersecting the plane, not only HBM layers.
    """
    twin = request.twin
    stack = next((item for item in twin.hbm_assemblies or [] if item.id == request.assembly_id), None)
    if stack is None:
        raise ValueError(f"Unknown HBM assembly '{request.assembly_id}'.")
    axis = 0 if request.axis == "xz" else 1
    fixed_axis = 1 - axis
    fixed = stack.center_xy_mm[fixed_axis]
    u0 = max(0.0, stack.center_xy_mm[axis] - stack.footprint_mm[axis] / 2 - .05)
    u1 = min(twin.size_mm[axis], stack.center_xy_mm[axis] + stack.footprint_mm[axis] / 2 + .05)
    height = (stack.base_thickness_um + stack.die_count * (stack.die_thickness_um + stack.gap_um)
              + stack.cap_thickness_um) / 1000
    z0, z1 = max(0.0, stack.bottom_z_mm - height - .02), min(twin.size_mm[2], stack.bottom_z_mm + .45)
    n = request.resolution
    du, dz = (u1 - u0) / n, (z1 - z0) / n
    u = u0 + (np.arange(n) + .5) * du
    z = z0 + (np.arange(n) + .5) * dz
    labels = np.zeros((n, n), dtype=np.uint8)
    for part in twin.objects:
        if part.role == "defect" and not request.include_defects:
            continue
        c, s = np.asarray(part.center_mm), np.asarray(part.size_mm)
        if abs(fixed - c[fixed_axis]) > s[fixed_axis] / 2:
            continue
        iu = np.flatnonzero(abs(u - c[axis]) <= s[axis] / 2)
        iz = np.flatnonzero(abs(z - c[2]) <= s[2] / 2)
        if not len(iu) or not len(iz):
            continue
        selection = np.ix_(iz, iu)
        if part.shape == "box":
            labels[selection] = LABELS[part.material]
        else:
            radial = ((u[iu] - c[axis]) / (s[axis] / 2)) ** 2
            radial += ((fixed - c[fixed_axis]) / (s[fixed_axis] / 2)) ** 2
            if part.shape == "sphere":
                mask = radial[None, :] + ((z[iz] - c[2])[:, None] / (s[2] / 2)) ** 2 <= 1
            else:
                mask = np.broadcast_to(radial <= 1, (len(iz), len(iu)))
            region = labels[selection]
            region[mask] = LABELS[part.material]
            labels[selection] = region
    warnings = ["Material geometry section; not an X-ray image or acoustic reconstruction.",
                "Horizontal and depth axes have independent display scales."]
    if min(stack.die_thickness_um, stack.gap_um, stack.base_thickness_um, stack.cap_thickness_um) < 2 * dz * 1000:
        warnings.append("Some layer thicknesses are below two section samples; increase section resolution.")
    if not stack.physical_present:
        warnings.append("This HBM is physically absent; the section shows remaining package geometry.")
    legend = [{"label": 0, "id": "ambient", "name": "Ambient", "color": "#edf4fa"}]
    legend += [{"label": LABELS[key], "id": key, "name": MATERIALS[key]["name"],
                "color": MATERIALS[key]["color"]} for key in MATERIAL_IDS]
    return {"image": labels.tolist(), "materials": legend, "extent_mm": [u0, u1, z0, z1],
            "axis": request.axis, "fixed_coordinate_mm": fixed, "pixel_pitch_um": [du * 1000, dz * 1000],
            "mode": "material_geometry", "assembly_id": stack.id, "warnings": warnings}
