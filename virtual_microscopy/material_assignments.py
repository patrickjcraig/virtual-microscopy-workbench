"""Pure, bounded material assumptions and continuous geometry coverage.

No file IO, numerical propagation, current SLS model construction or implicit
material inference. Historical publication validation belongs to the store;
new column inspection additionally requires the supported frozen path contract.
"""
from copy import deepcopy
import math

from .causal_comparison_store import json_measure
from .column_paths import MATERIAL_IDS as PATH_MATERIAL_IDS, PATH_CONTRACT_VERSION, build_column_paths
from .material_assignment_schemas import MaterialColumnRequest, MaterialParameters, SLSMaterialAssignmentRequest

KIND = "sls_material_assignment"
COLUMN_KIND = "sls_material_column"
PROCESSING_VERSION = "material-assignment-0.19.0"
COLUMN_PROCESSING_VERSION = "material-column-0.19.0"
CONTRACT_VERSION = "explicit-sls-material-assignment-1"
COLUMN_CONTRACT_VERSION = "material-column-coverage-1"
MAX_REPORT_BYTES = 32 * 1024**2
MAX_REPORT_EXPANDED_BYTES = 128 * 1024**2
MAX_WORKSPACE_BYTES = 512 * 1024**2
MAX_SOURCE_BYTES = 16 * 1024**2
MAX_SOURCE_EXPANDED_BYTES = 64 * 1024**2
MATERIAL_IDS = ("silicon", "copper", "solder", "epoxy", "fr4", "air")
PARAMETER_KEYS = ("density_kg_m3", "relaxed_modulus_gpa", "unrelaxed_modulus_gpa", "relaxation_time_us")
NOMINAL_DENSITIES = {"silicon": 2329., "copper": 8960., "solder": 7310.,
                     "epoxy": 1200., "fr4": 1850., "air": 1.205}
INVENTORY_DEFINITION = "All material IDs in included frozen primitives, including fully occluded primitives; not actual column or ROI coverage."
AMBIENT_POLICY = {
    "contract_version": "nominal-lossless-ambient-water-1", "material_id": "ambient_water", "material_label": 0,
    "name": "Nominal lossless ambient water", "impedance_mrayl": 1.48, "sound_speed_m_s": 1480.,
    "pressure_loss_db_mm": 0., "evidence": "uncalibrated_nominal_assumption",
    "definition": "Separate real lossless medium for uncovered finite intervals; not an SLS material binding and not explicit air.",
}
IDENTITY = {
    "contract_version": CONTRACT_VERSION, "column_contract_version": COLUMN_CONTRACT_VERSION,
    "path_contract": "ordered-column-paths-1",
    "material_label_map": {"0": "ambient_water", **{str(i + 1): name for i, name in enumerate(MATERIAL_IDS)}},
    "parameter_units": {"density_kg_m3": "kg/m3", "relaxed_modulus_gpa": "GPa",
                        "unrelaxed_modulus_gpa": "GPa", "relaxation_time_us": "us"},
    "material_definition": "M(s)=M0+(Minf-M0)*s*tau/(1+s*tau); manually assumed scalar longitudinal modulus, not automatically Young's or bulk modulus.",
    "binding_definition": "One binding per material ID applies to every included occurrence; no object, assembly or layer-role override.",
    "reference_definition": "Exactly four selected finite-layer parameters are copied. Thickness, exteriors, pulse, spectra and numerical certificates remain provenance only.",
    "geometry_definition": "Complete ordered compiled primitives, last included object wins, full specimen depth and positive-length continuous intervals; no Z raster.",
    "nominal_density_kg_m3": NOMINAL_DENSITIES,
    "nominal_density_definition": "Frozen nominal library comparison only; does not replace authored density or modify existing instruments.",
}
WARNINGS = [
    "Material coefficients are explicit uncalibrated assumptions. Source-report integrity and numerical certificates do not establish material calibration or suitability for this twin.",
    "Inventory, declared-scope coverage and one-column coverage are different. None establishes coverage of a new point, ROI or lateral observation support.",
    "Propagation is unavailable. Finite ambient/SLS resolution and more-than-eight-layer SLS propagation require separate supported contracts and numerical admission.",
    "The complete compiled twin and original material precedence are frozen; new workbench edits never update this document.",
]


def _measure(value, *, retained=0, source=False):
    return json_measure(value, MAX_SOURCE_BYTES if source else MAX_REPORT_BYTES,
                        MAX_SOURCE_EXPANDED_BYTES if source else MAX_REPORT_EXPANDED_BYTES,
                        retained_bytes=retained)


def _sha(value):
    return _measure(value)["sha256"]


def _budget(amount):
    if amount > MAX_WORKSPACE_BYTES:
        raise ValueError("Material assignment exceeds the 512 MiB owned-workspace limit.")


def _ordered(values):
    return [name for name in MATERIAL_IDS if name in values]


def _coverage(request, twin, records):
    objects = [o for o in twin["objects"] if request["include_defects"] or o.get("role", "structure") != "defect"]
    inventory = {o["material"] for o in objects}
    required = inventory if request["coverage_scope"] == "all_included" else set(request["required_material_ids"])
    supplied = {r["material_id"] for r in records}
    if not required.issubset(inventory) or not supplied.issubset(inventory):
        raise ValueError("Material bindings and required scope must belong to the included primitive inventory.")
    missing = required - supplied
    return {"scope": request["coverage_scope"], "inventory_material_ids": _ordered(inventory),
            "required_material_ids": _ordered(required), "supplied_material_ids": _ordered(supplied),
            "missing_material_ids": _ordered(missing), "complete": not missing,
            "status": "incomplete" if missing else "complete_for_" + request["coverage_scope"],
            "inventory_definition": INVENTORY_DEFINITION}


def _geometry(request, twin):
    excluded = sum(not request["include_defects"] and o.get("role", "structure") == "defect" for o in twin["objects"])
    return {"name": twin["name"], "size_mm": list(twin["size_mm"]), "primitive_count": len(twin["objects"]),
            "included_primitive_count": len(twin["objects"]) - excluded, "excluded_defect_count": excluded,
            "hbm_assembly_count": len(twin.get("hbm_assemblies") or [])}


def _records(request, sources, source_hashes):
    records = []
    for binding in request["bindings"]:
        origin = binding["origin"]
        if origin["kind"] == "manual":
            parameters = {key: origin[key] for key in PARAMETER_KEYS}
            provenance = {"kind": "manual"}
        else:
            identifier, index = origin["report_id"], origin["layer_index"]
            source = sources[identifier]
            if index >= len(source["stack"]["layers"]):
                raise ValueError(f"Source report {identifier} has no finite layer at index {index}.")
            layer = source["stack"]["layers"][index]
            parameters = {key: layer[key] for key in PARAMETER_KEYS}
            provenance = {**origin, "source_snapshot_sha256": source_hashes[identifier]}
        # Validate the four values only; do not instantiate an SLS finite stack
        # or change represented values copied from a historical source layer.
        MaterialParameters.model_validate(parameters)
        record = {"material_id": binding["material_id"], "name": binding["name"], "note": binding["note"],
                  "origin": provenance, "parameters": parameters, "parameters_sha256": _sha(parameters),
                  "evidence": "manual_scalar_longitudinal_assumption",
                  "nominal_density_kg_m3": NOMINAL_DENSITIES[binding["material_id"]],
                  "nominal_density_difference_kg_m3": parameters["density_kg_m3"] - NOMINAL_DENSITIES[binding["material_id"]]}
        record["assignment_sha256"] = _sha(record)
        records.append(record)
    return records


def _resource_plan(input_size, source_sizes, document_size, *, columns=0, verify_embedded_sources=False):
    retained = input_size["expanded_bytes"] + sum(size["expanded_bytes"] for size in source_sizes)
    # Incoming reports, normalized request, independent copied snapshots, final
    # publication/HTTP buffers and historical source validation are all owned.
    encoded = document_size["encoded_bytes"] + 64 * 1024
    expanded = document_size["expanded_bytes"] + 2 * 1024**2
    peak = max(retained + 256 * 1024**2 if source_sizes or verify_embedded_sources else 0,
               2 * retained + expanded + 12 * encoded + 32 * 1024**2)
    _budget(peak)
    if encoded > MAX_REPORT_BYTES or expanded > MAX_REPORT_EXPANDED_BYTES:
        raise ValueError("Material assignment exceeds its bounded document/expanded-memory limits.")
    return {"source_report_count": len(source_sizes), "estimated_report_bytes": encoded,
            "estimated_report_expanded_bytes": expanded, "estimated_peak_bytes": peak,
            "column_count": columns, "propagation_work_units": 0,
            "workspace_definition": "Retained input/source JSON, normalized request and copied snapshots, bounded publication/export buffers, historical source validation and one-column geometry reserve; not process RSS."}


def _prepare(request, sources):
    if type(sources) is not dict or len(sources) > 6:
        raise ValueError("Supply at most six source reports keyed by canonical report ID.")
    if isinstance(request, SLSMaterialAssignmentRequest):
        full = request.model_dump(mode="json", exclude_none=True)
        input_size = _measure(full)
    else:
        input_size = _measure(request)
        model = SLSMaterialAssignmentRequest.model_validate(request)
        full = model.model_dump(mode="json", exclude_none=True)
    input_size = _measure(full)
    needed = {b["origin"]["report_id"] for b in full["bindings"] if b["origin"]["kind"] == "report_layer"}
    if set(sources) != needed:
        raise ValueError("Verified source reports must exactly match referenced report IDs; missing or unused sources are not accepted.")
    source_sizes = []
    retained = input_size["expanded_bytes"]
    for identifier in sorted(needed):
        size = _measure(sources[identifier], retained=retained, source=True)
        retained += size["expanded_bytes"]
        source_sizes.append(size)
    _budget(retained + (256 * 1024**2 if needed else 32 * 1024**2))
    source_hashes = {}
    if needed:
        # The caller has already verified these complete reports. Recheck their
        # frozen hashes and historical structure to catch stale mutable inputs;
        # this imports no current SLS constructor or numerical implementation.
        from .sls_comparison_store import validate_source
        for identifier, size in zip(sorted(needed), source_sizes):
            validate_source(sources[identifier], expected_id=identifier)
            source_hashes[identifier] = size["sha256"]
    twin = full["twin"]
    twin_hash = _sha(twin)
    normalized = {key: value for key, value in full.items() if key != "twin"}
    normalized["twin_sha256"] = twin_hash
    records = _records(normalized, sources, source_hashes)
    snapshots = {twin_hash: twin, **{source_hashes[k]: sources[k] for k in sorted(needed)}}
    kinds = {twin_hash: "twin", **{h: "sls_report" for h in source_hashes.values()}}
    document = {"processing_version": PROCESSING_VERSION, "request": normalized,
                "input_request_sha256": _sha(full), "snapshots": snapshots, "snapshot_kinds": kinds,
                "twin_sha256": twin_hash, "bindings": records,
                "coverage": _coverage(normalized, twin, records), "ambient_policy": deepcopy(AMBIENT_POLICY),
                "identity": deepcopy(IDENTITY), "geometry": _geometry(normalized, twin),
                "propagation_available": False, "warnings": list(WARNINGS)}
    size = _measure(document, retained=retained)
    document["resources"] = _resource_plan(input_size, source_sizes, size)
    _measure(document, retained=retained)
    return document


def estimate_assignment(request, sources):
    """Pure validation, provenance resolution and resource estimate; no paths."""
    document = _prepare(request, sources)
    return {key: deepcopy(value) for key, value in document.items() if key not in ("snapshots", "snapshot_kinds")}


def build_assignment(request, sources):
    """Return an independent immutable-publication candidate, without file IO."""
    return deepcopy(_prepare(request, sources))


def _inspectable(document):
    size = _measure(document)
    if document.get("identity") != IDENTITY or document.get("ambient_policy") != AMBIENT_POLICY:
        raise ValueError("New column inspection requires the supported frozen assignment/path/ambient identity.")
    if PATH_CONTRACT_VERSION != IDENTITY["path_contract"] or tuple(PATH_MATERIAL_IDS) != MATERIAL_IDS:
        raise ValueError("Installed material labels or continuous path contract do not match this assignment.")
    if document.get("processing_version") != PROCESSING_VERSION or document.get("propagation_available") is not False:
        raise ValueError("Unsupported assignment processing contract.")
    try:
        snapshots, kinds = document["snapshots"], document["snapshot_kinds"]
        twin_hash, request = document["twin_sha256"], document["request"]
        if request["twin_sha256"] != twin_hash or kinds[twin_hash] != "twin" or set(kinds) != set(snapshots):
            raise ValueError("Assignment snapshot closure is inconsistent.")
        for digest, value in snapshots.items():
            if _sha(value) != digest:
                raise ValueError("Assignment snapshot checksum mismatch.")
        twin = snapshots[twin_hash]
        sources, hashes = {}, {}
        for digest, kind in kinds.items():
            if kind == "sls_report":
                source = snapshots[digest]
                identifier = source["id"]
                if identifier in sources:
                    raise ValueError("Duplicate source report identity in snapshot closure.")
                sources[identifier], hashes[identifier] = source, digest
            elif kind != "twin" or digest != twin_hash:
                raise ValueError("Unsupported snapshot kind or duplicate twin.")
        full = {key: value for key, value in request.items() if key != "twin_sha256"}
        full["twin"] = twin
        if _sha(full) != document["input_request_sha256"]:
            raise ValueError("Assignment normalized input checksum mismatch.")
        needed = {b["origin"]["report_id"] for b in request["bindings"] if b["origin"]["kind"] == "report_layer"}
        if set(sources) != needed or len(document["bindings"]) > 6 or len(snapshots) > 7:
            raise ValueError("Assignment source closure does not match referenced bindings.")
        records = _records(request, sources, hashes)
        if (_sha(records) != _sha(document["bindings"]) or _coverage(request, twin, records) != document["coverage"]
                or _geometry(request, twin) != document["geometry"]):
            raise ValueError("Assignment binding, inventory or geometry facts do not match frozen inputs.")
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as exc:
        raise ValueError("Malformed frozen material assignment.") from exc
    return twin, request, records, size


def inspect_column(document, x_mm, y_mm):
    """Inspect one complete geometry column; never solve or infer properties."""
    point = MaterialColumnRequest(x_mm=x_mm, y_mm=y_mm)
    twin, request, records, size = _inspectable(document)
    if point.x_mm > twin["size_mm"][0] or point.y_mm > twin["size_mm"][1]:
        raise ValueError("Column coordinates must lie inside the frozen specimen extent.")
    # One column can have at most 1+2*600 ordered positive intervals. Reserve
    # enough report/JSON/path space before calling geometry or copying closure.
    _budget(2 * size["expanded_bytes"] + 12 * size["encoded_bytes"] + 64 * 1024**2)
    paths = build_column_paths(twin, [point.x_mm], [point.y_mm], request["include_defects"])
    assignments = {r["material_id"]: r["assignment_sha256"] for r in records}
    segments, start, actual = [], 0., set()
    ambient_present = False
    for index, (end, label) in enumerate(zip(paths.z_end_mm, paths.material_label)):
        end, label = float(end), int(label)
        if not math.isfinite(end) or end <= start or str(label) not in IDENTITY["material_label_map"]:
            raise ValueError("Continuous path returned an invalid positive material partition.")
        material = IDENTITY["material_label_map"][str(label)]
        if label == 0:
            status, digest, ambient_present = "ambient_policy", None, True
        else:
            actual.add(material)
            digest = assignments.get(material)
            status = "assigned" if digest is not None else "missing"
        segments.append({"index": index, "z_start_mm": start, "z_end_mm": end, "thickness_mm": end - start,
                         "material_label": label, "material_id": material, "assignment_sha256": digest,
                         "coverage_status": status})
        start = end
    if start != twin["size_mm"][2]:
        raise ValueError("Continuous path did not retain the complete specimen depth.")
    supplied, missing = actual.intersection(assignments), actual.difference(assignments)
    coverage = {"required_material_ids": _ordered(actual), "supplied_material_ids": _ordered(supplied),
                "missing_material_ids": _ordered(missing), "complete": not missing,
                "status": "incomplete" if missing else "complete_for_column", "ambient_present": ambient_present}
    result = {"kind": COLUMN_KIND, "processing_version": COLUMN_PROCESSING_VERSION, "twin_sha256": document["twin_sha256"],
              "x_mm": point.x_mm, "y_mm": point.y_mm, "depth_mm": twin["size_mm"][2], "segments": segments,
              "coverage": coverage, "segment_count": len(segments), "path_contract": PATH_CONTRACT_VERSION,
              "diagnostics": paths.diagnostics, "ambient_policy": deepcopy(AMBIENT_POLICY),
              "propagation_available": False, "warnings": list(WARNINGS)}
    # Reserve one full independently readable column publication containing the
    # parent's deduplicated closure, including store headers and export buffers.
    row_size = _measure(result, retained=size["expanded_bytes"])
    combined = {key: size[key] + row_size[key] for key in ("encoded_bytes", "expanded_bytes")}
    result["resources"] = _resource_plan(size, [], combined, columns=1,
        verify_embedded_sources="sls_report" in document["snapshot_kinds"].values())
    return result
