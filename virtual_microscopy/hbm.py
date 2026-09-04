"""Deterministic compilation of editable, assumed layered HBM assemblies.

Layered silicon and epoxy represent interfaces. Aggregate attachment contacts
remain explicitly coarse: this compiler does not invent resolved microbumps or
TSVs. Functional state is metadata and never controls material occupancy.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schemas import Twin


def compile_hbm_stack(stack: dict) -> list[dict]:
    """Compile validated stack parameters in stable material override order."""
    if not stack["physical_present"]:
        return []
    identifier, name = stack["id"], stack["name"]
    x, y = stack["center_xy_mm"]
    width, height = stack["footprint_mm"]
    bottom = stack["bottom_z_mm"]
    parts: list[dict] = []

    def part(suffix, role, material, center, size, shape="box", label=None):
        item = {
            "id": f"{identifier}-{suffix}", "name": f"{name} / {suffix} (assumed)",
            "shape": shape, "material": material, "center_mm": center,
            "size_mm": size, "role": "structure", "assembly_id": identifier,
            "layer_role": role,
        }
        if label is not None:
            item["display_label"] = label
        parts.append(item)

    # Attachment is a fixed 180 um coarse package-level surrogate. It follows
    # stack movement and footprint edits but is not an actual microbump array.
    part("underfill", "underfill", "epoxy", [x, y, bottom + 0.09], [width, height, 0.18])
    diameter = min(width, height) * 0.275
    for index, offset in enumerate([-height * 5 / 18, 0, height * 5 / 18], 1):
        part(f"contact-{index:02}", "contact", "solder", [x, y + offset, bottom + 0.09],
             [diameter, diameter, 0.18], "cylinder")

    cursor = bottom

    def layer(suffix, role, material, thickness_um, label=None):
        nonlocal cursor
        thickness = thickness_um / 1000
        part(suffix, role, material, [x, y, round(cursor - thickness / 2, 12)],
             [width, height, thickness], label=label)
        cursor -= thickness

    layer("base", "base_die", "silicon", stack["base_thickness_um"])
    for index in range(1, stack["die_count"] + 1):
        # One base-to-first-die interface and one between each DRAM pair.
        layer(f"gap-{index:02}", "interdie_gap", "epoxy", stack["gap_um"])
        layer(f"dram-{index:02}", "dram_die", "silicon", stack["die_thickness_um"])
    layer("cap", "cap", "epoxy", stack["cap_thickness_um"], label=name)
    return parts


def validate_hbm_geometry(twin: Twin) -> None:
    """Reject imports where assembly metadata disagrees with material geometry."""
    from .schemas import Primitive

    assemblies = twin.hbm_assemblies or []
    ids = [stack.id for stack in assemblies]
    if len(ids) != len(set(ids)):
        raise ValueError("HBM assembly ids must be unique.")
    for obj in twin.objects:
        if obj.assembly_id is not None and obj.assembly_id not in ids:
            raise ValueError(f"Object '{obj.id}' names an unknown HBM assembly.")
        if (obj.assembly_id is None) != (obj.layer_role is None):
            raise ValueError("HBM assembly_id and layer_role must be supplied together.")
    for stack in assemblies:
        for center, span, bound in zip(stack.center_xy_mm, stack.footprint_mm, twin.size_mm[:2]):
            if center - span / 2 < -1e-8 or center + span / 2 > bound + 1e-8:
                raise ValueError(f"HBM assembly '{stack.id}' footprint exceeds the specimen bounds.")
        if stack.bottom_z_mm + 0.18 > twin.size_mm[2] + 1e-8:
            raise ValueError(f"HBM assembly '{stack.id}' attachment exceeds the specimen depth.")
        expected = [Primitive.model_validate(part).model_dump(exclude_none=True)
                    for part in compile_hbm_stack(stack.model_dump())]
        actual = [obj.model_dump(exclude_none=True) for obj in twin.objects if obj.assembly_id == stack.id]
        if actual != expected:
            raise ValueError(f"HBM assembly '{stack.id}' metadata and compiled primitives disagree; rebuild with compose_hbm.")
        positions = [index for index, obj in enumerate(twin.objects) if obj.assembly_id == stack.id]
        if positions and positions[-1] - positions[0] + 1 != len(positions):
            # Ordered CSG makes even a foreign, untagged primitive between two
            # layers significant. A replacement block must not move it across
            # the layers and thereby change its material overwrite precedence.
            raise ValueError(f"HBM assembly '{stack.id}' primitives must form one contiguous block; "
                             "interleaved foreign objects would change material precedence during an update.")


def compose_hbm(twin: dict, assembly_id: str, parameters: dict) -> dict:
    """Apply a strict parameter patch without mutating the original specimen.

    The targeted contiguous stack block is replaced at its existing position so later defects
    keep their precedence. Restoring a physically absent stack inserts it before
    the first defect. Unrelated package objects and all defect coordinates remain
    unchanged: moving an assembly does not silently move a fixed specimen defect.
    """
    from .schemas import HBMParameterUpdate, HBMStack, Twin

    validated = Twin.model_validate(twin)
    patch = HBMParameterUpdate.model_validate(parameters).model_dump(exclude_unset=True)
    result = deepcopy(twin)
    assemblies = result.get("hbm_assemblies") or []
    index = next((i for i, stack in enumerate(assemblies) if stack["id"] == assembly_id), None)
    if index is None:
        raise ValueError(f"Unknown HBM assembly '{assembly_id}'.")
    prior = validated.hbm_assemblies[index].model_dump(mode="json")
    updated = HBMStack.model_validate(prior | patch)
    assemblies[index] = updated.model_dump(mode="json")
    replacement = compile_hbm_stack(updated.model_dump())
    objects = result["objects"]
    positions = [i for i, obj in enumerate(objects) if obj.get("assembly_id") == assembly_id]
    insert_at = positions[0] if positions else next((i for i, obj in enumerate(objects) if obj.get("role") == "defect"), len(objects))
    survivors = [obj for obj in objects if obj.get("assembly_id") != assembly_id]
    result["objects"] = survivors[:insert_at] + replacement + survivors[insert_at:]
    Twin.model_validate(result)
    return result
