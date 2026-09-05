"""Deterministic compilation of editable, assumed layered HBM assemblies.

Layered silicon and epoxy represent interfaces. An optional bounded patch adds
assumed explicit bump/TSV cylinders and local synthetic defects. Package-level
attachment contacts remain coarse. Electrical state never controls occupancy.
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
            item["display_label"] = label[:48]
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
    micro_parts, _, _ = _microstructure_geometry(stack, parts)
    parts.extend(micro_parts)
    return parts


def _feature_id(identifier: str, family: str, layer: int, row: int, column: int) -> str:
    prefix = "mb" if family == "microbump" else "tsv"
    return f"{identifier}-{prefix}-{layer:02}-r{row:02}-c{column:02}"


def _microstructure_geometry(stack: dict, layers: list[dict]) -> tuple[list, list, list]:
    """One canonical generator supplies primitives and public feature identity."""
    micro = stack.get("microstructure")
    if not micro:
        return [], [], []
    identifier, name = stack["id"], stack["name"]
    defects = [{"id": defect["id"], "kind": defect["kind"],
                "target_id": _feature_id(identifier, "tsv" if defect["kind"] == "tsv_void" else "microbump",
                                         defect["layer_index"], defect["row"], defect["column"]),
                "primitive_id": f"{identifier}-defect-{defect['id']}", "enabled": defect["enabled"]}
               for defect in micro["defects"]]
    if not micro["enabled"]:
        return [], [], defects
    layer_by_id = {part["id"]: part for part in layers}
    parts, features = [], []
    for index in range(stack["die_count"] + 1):
        families = ("tsv",) if index == 0 else ("microbump", "tsv")
        for family in families:
            suffix = ("base" if index == 0 else f"dram-{index:02}") if family == "tsv" else f"gap-{index:02}"
            layer = layer_by_id[f"{identifier}-{suffix}"]
            diameter = micro["tsv_diameter_um" if family == "tsv" else "bump_diameter_um"] / 1000
            for row in range(1, micro["rows"] + 1):
                for column in range(1, micro["columns"] + 1):
                    x = stack["center_xy_mm"][0] + (micro["center_offset_xy_um"][0] + (column - (micro["columns"] + 1) / 2) * micro["pitch_x_um"]) / 1000
                    y = stack["center_xy_mm"][1] + (micro["center_offset_xy_um"][1] + (row - (micro["rows"] + 1) / 2) * micro["pitch_y_um"]) / 1000
                    feature_id = _feature_id(identifier, family, index, row, column)
                    center = [round(x, 12), round(y, 12), layer["center_mm"][2]]
                    size = [diameter, diameter, layer["size_mm"][2]]
                    features.append({"id": feature_id, "kind": family, "row": row, "column": column,
                                     "layer_index": index, "center_mm": center, "size_mm": size})
                    parts.append({"id": feature_id, "name": f"{name} / {feature_id[len(identifier)+1:]} (assumed)",
                                  "shape": "cylinder", "material": "copper" if family == "tsv" else "solder",
                                  "center_mm": center, "size_mm": size, "role": "structure",
                                  "assembly_id": identifier, "layer_role": family})
    targets = {part["id"]: part for part in parts}
    for authored, descriptor in zip(micro["defects"], defects):
        if not authored["enabled"]:
            continue
        target = targets[descriptor["target_id"]]
        missing = authored["kind"] == "missing_bump"
        diameter = None if missing else authored["void_diameter_um"] / 1000
        parts.append({"id": descriptor["primitive_id"], "name": f"{name} / defect-{authored['id']} (assumed)",
                      "shape": "cylinder" if missing else "sphere", "material": "epoxy" if missing else "air",
                      "center_mm": list(target["center_mm"]),
                      "size_mm": list(target["size_mm"]) if missing else [diameter] * 3,
                      "role": "defect", "assembly_id": identifier, "layer_role": target["layer_role"]})
    return parts, features, defects


def microstructure_summary(stack: dict) -> dict:
    """Describe applied feature centers and a padded global XY scan rectangle.

    Feature bounds use [x0,y0,z0,x1,y1,z1]; ROI uses [x0,y0,x1,y1].
    Disabled patches retain authored defect identities but have no active geometry.
    """
    from .schemas import HBMStack

    stack = HBMStack.model_validate(stack).model_dump(mode="json", exclude_none=True)
    micro = stack.get("microstructure")
    parts = compile_hbm_stack(stack)
    generated, features, defects = _microstructure_geometry(stack, parts)
    summary = {"enabled": bool(micro and micro["enabled"]),
               "model_version": micro["model_version"] if micro else None,
               "nominal_feature_count": len(features), "defect_count": sum(part["role"] == "defect" for part in generated),
               "feature_bounds_mm": None, "roi_mm": None, "features": features, "defects": defects}
    if not features:
        return summary
    lo = [min(feature["center_mm"][axis] - feature["size_mm"][axis] / 2 for feature in features) for axis in range(3)]
    hi = [max(feature["center_mm"][axis] + feature["size_mm"][axis] / 2 for feature in features) for axis in range(3)]
    summary["feature_bounds_mm"] = lo + hi
    roi_lo, roi_hi = [], []
    for axis, pitch in enumerate((micro["pitch_x_um"], micro["pitch_y_um"])):
        margin = max(pitch / 2000, 0.025)
        minimum = stack["center_xy_mm"][axis] - stack["footprint_mm"][axis] / 2
        maximum = stack["center_xy_mm"][axis] + stack["footprint_mm"][axis] / 2
        low, high = max(minimum, lo[axis] - margin), min(maximum, hi[axis] + margin)
        if high - low < 0.05:
            low = max(minimum, min((low + high) / 2 - 0.025, maximum - 0.05))
            high = low + 0.05
        roi_lo.append(low)
        roi_hi.append(high)
    summary["roi_mm"] = roi_lo + roi_hi
    return summary


def validate_hbm_geometry(twin: Twin) -> None:
    """Reject imports where assembly metadata disagrees with material geometry."""
    from .schemas import Primitive

    assemblies = twin.hbm_assemblies or []
    ids = [stack.id for stack in assemblies]
    if len(ids) != len(set(ids)):
        raise ValueError("HBM assembly ids must be unique.")
    if sum(bool(stack.microstructure and stack.microstructure.enabled) for stack in assemblies) > 1:
        raise ValueError("Only one enabled explicit microstructure patch is supported per twin.")
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
    the first global defect. Unrelated package objects and globally positioned
    defect coordinates remain unchanged; component-local defects follow their assembly.
    """
    from .schemas import HBMParameterUpdate, HBMStack, Twin

    validated = Twin.model_validate(twin)
    patch = HBMParameterUpdate.model_validate(parameters).model_dump(exclude_unset=True)
    result = deepcopy(twin)
    assemblies = result.get("hbm_assemblies") or []
    index = next((i for i, stack in enumerate(assemblies) if stack["id"] == assembly_id), None)
    if index is None:
        raise ValueError(f"Unknown HBM assembly '{assembly_id}'.")
    prior = validated.hbm_assemblies[index].model_dump(mode="json", exclude_none=True)
    if "microstructure" in patch and prior.get("microstructure") is not None:
        patch["microstructure"] = prior["microstructure"] | patch["microstructure"]
    updated = HBMStack.model_validate(prior | patch)
    assemblies[index] = updated.model_dump(mode="json", exclude_none=True)
    replacement = compile_hbm_stack(updated.model_dump())
    objects = result["objects"]
    positions = [i for i, obj in enumerate(objects) if obj.get("assembly_id") == assembly_id]
    insert_at = positions[0] if positions else next((i for i, obj in enumerate(objects)
                                                   if obj.get("role") == "defect" and obj.get("assembly_id") is None), len(objects))
    survivors = [obj for obj in objects if obj.get("assembly_id") != assembly_id]
    result["objects"] = survivors[:insert_at] + replacement + survivors[insert_at:]
    if len(result["objects"]) > 600:
        raise ValueError(f"Composed twin has {len(result['objects'])} primitives; the supported limit is 600. Reduce patch rows/columns or disable it.")
    Twin.model_validate(result)
    return result
