"""Immutable material assumptions and source-free saved column evidence.

Only new inspections may invoke the version-matched geometry implementation.
Historical reads validate the frozen contracts and never reconstruct the Twin.
"""
from __future__ import annotations

from datetime import datetime, timezone
from fractions import Fraction
import hashlib
from importlib.metadata import version
import io
import math
import os
from pathlib import Path
import platform
import stat
from uuid import uuid4

from . import causal_comparison_store as _json

KIND = "sls_material_assignment"
COLUMN_KIND = "sls_material_column"
PROCESSING_VERSION = "material-assignment-0.19.0"
COLUMN_PROCESSING_VERSION = "material-column-0.19.0"
STORE_CONTRACT = "material-assignment-store-1"
COLUMN_STORE_CONTRACT = "material-column-store-1"
MAX_REPORT_BYTES = 32*1024**2
MAX_REPORT_EXPANDED_BYTES = 128*1024**2
MAX_WORKSPACE_BYTES = 512*1024**2
MAX_SOURCE_BYTES = 16*1024**2
MAX_SOURCE_EXPANDED_BYTES = 64*1024**2
MAX_CATALOG_FILES = 10_000
MAX_PAGE_SIZE = 100
CATALOG_ORDER = "id_desc"
MATERIAL_IDS = ("silicon", "copper", "solder", "epoxy", "fr4", "air")
PARAMETERS = ("density_kg_m3", "relaxed_modulus_gpa", "unrelaxed_modulus_gpa", "relaxation_time_us")
NOMINAL_DENSITIES = {"silicon": 2329., "copper": 8960., "solder": 7310., "epoxy": 1200., "fr4": 1850., "air": 1.205}
GEOMETRY_FILES = ("material_assignments.py", "material_assignment_schemas.py", "column_paths.py", "schemas.py", "hbm.py", "materials.py")
IMPLEMENTATION_FILES = (*GEOMETRY_FILES, "material_assignment_store.py", "material_assignment_api.py", "sls_reports.py", "sls_comparison_store.py", "causal_comparison_store.py")
# Versioned historical semantics; no current core imports for saved reads.
CONTRACT_VERSION = "explicit-sls-material-assignment-1"
COLUMN_CONTRACT_VERSION = "material-column-coverage-1"
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

BASE_FIELDS = {"processing_version", "request", "input_request_sha256", "snapshots", "snapshot_kinds", "twin_sha256", "bindings",
    "coverage", "ambient_policy", "identity", "geometry", "resources", "propagation_available", "warnings"}
STORED_FIELDS = {"schema_version", "kind", "id", "assignment_id", "created_at", "report_sha256", "store_identity"}
COLUMN_FIELDS = {"kind", "processing_version", "twin_sha256", "x_mm", "y_mm", "depth_mm", "segments", "coverage", "segment_count",
    "path_contract", "diagnostics", "ambient_policy", "propagation_available", "warnings", "resources"}
COLUMN_STORED_FIELDS = {"schema_version", "id", "column_id", "assignment_id", "assignment_sha256", "assignment_record", "snapshots", "snapshot_kinds",
    "created_at", "report_sha256", "store_identity"}


def json_measure(value, byte_limit=None, expanded_limit=None, *, retained_bytes=0):
    return _json.json_measure(value, MAX_REPORT_BYTES if byte_limit is None else byte_limit,
        MAX_REPORT_EXPANDED_BYTES if expanded_limit is None else expanded_limit, retained_bytes=retained_bytes)


def bounded_payload(value):
    json_measure(value)
    stream = io.BytesIO()
    for part in _json._tokens(value): stream.write(part)
    return stream.getvalue()


def bounded_json_read(path, byte_limit=None, expanded_limit=None, *, retained_bytes=0):
    report, _ = _json.bounded_json_read(path, MAX_REPORT_BYTES if byte_limit is None else byte_limit,
        MAX_REPORT_EXPANDED_BYTES if expanded_limit is None else expanded_limit, retained_bytes=retained_bytes)
    measured = json_measure(report, MAX_REPORT_BYTES if byte_limit is None else byte_limit,
        MAX_REPORT_EXPANDED_BYTES if expanded_limit is None else expanded_limit, retained_bytes=retained_bytes)
    return report, measured


def _digest(value):
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _number(value):
    try: return type(value) in (int, float) and math.isfinite(value)
    except OverflowError: return False


def _range(value, lo, hi):
    return _number(value) and lo <= value <= hi


def _text(value, maximum, *, empty=False):
    return type(value) is str and (empty or bool(value.strip())) and len(value) <= maximum


def _label(value, maximum, *, empty=False):
    """Inherited Twin labels retain their original length-only contract."""
    return type(value) is str and (empty or len(value)>0) and len(value)<=maximum


def _vector(value, length, lo, hi):
    return type(value) is list and len(value) == length and all(_range(v, lo, hi) for v in value)


def _exact(a, b):
    return float(a).hex() == float(b).hex() if _number(a) and _number(b) else type(a) is type(b) and a == b


def _fingerprints():
    return {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in IMPLEMENTATION_FILES}


def _runtime():
    return {"python": platform.python_version(), "implementation": platform.python_implementation(), "system": platform.system(),
        "machine": platform.machine(), "numerical_packages": {"numpy": version("numpy")}}


def _disk(root, required):
    import shutil
    probe = root
    while not probe.exists() and probe != probe.parent: probe = probe.parent
    if shutil.disk_usage(probe).free < required+64*1024**2:
        raise ValueError("Insufficient free disk space for immutable material evidence and reserve.")


def _parameters(parameters):
    if (type(parameters) is not dict or set(parameters) != set(PARAMETERS) or
            not _range(parameters["density_kg_m3"], 1, 30000) or not _range(parameters["relaxed_modulus_gpa"], 1e-6, 1000) or
            not _range(parameters["unrelaxed_modulus_gpa"], parameters["relaxed_modulus_gpa"], 1000) or
            not _range(parameters["relaxation_time_us"], 1e-6, 100)):
        raise ValueError("Material binding violates the frozen positive passive SLS parameter contract.")


def _validate_source(source, identifier=None):
    from . import sls_reports
    measured = json_measure(source, MAX_SOURCE_BYTES, MAX_SOURCE_EXPANDED_BYTES)
    try:
        sls_reports._validate(source, historical=True); sls_reports._account(source, measured)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as exc:
        raise ValueError("Material assignment source has a malformed historical SLS contract.") from exc
    _json._checked_id(source["id"])
    if source["report_id"] != source["id"] or (identifier is not None and source["id"] != identifier):
        raise ValueError("Material source report identity mismatch.")
    for key, value in (("request_sha256", source["request"]), ("stack_sha256", source["stack"]),
            ("report_sha256", {k: v for k, v in source.items() if k != "report_sha256"})):
        if source[key] != json_measure(value, MAX_SOURCE_BYTES, MAX_SOURCE_EXPANDED_BYTES)["sha256"]:
            raise ValueError("Material source report checksum mismatch.")
    return measured


def _twin(twin):
    fields = {"schema_version", "name", "description", "size_mm", "objects", "reference", "recommended_settings", "hbm_assemblies", "image_reference"}
    required={"schema_version","name","description","size_mm","objects"}
    if (type(twin) is not dict or not required <= set(twin) <= fields or type(twin["schema_version"]) is not int or twin["schema_version"] != 1 or
            not _label(twin["name"], 160) or not _label(twin["description"], 4000, empty=True) or
            not _vector(twin["size_mm"], 3, .01, 100) or min(twin["size_mm"][:2]) < .05 or twin["size_mm"][2] > 6 or
            type(twin["objects"]) is not list or not 1 <= len(twin["objects"]) <= 600):
        raise ValueError("Frozen Twin identity, dimensions or primitive count is unsupported.")
    ids = set()
    primitive_fields = {"id", "name", "shape", "material", "center_mm", "size_mm", "role", "display_label", "assembly_id", "layer_role"}
    for obj in twin["objects"]:
        if (type(obj) is not dict or not {"id","name","shape","material","center_mm","size_mm","role"} <= set(obj) <= primitive_fields or not _label(obj["id"], 100) or obj["id"] in ids or
                not _label(obj["name"], 160) or obj["shape"] not in ("box", "sphere", "cylinder") or obj["material"] not in MATERIAL_IDS or
                not _vector(obj["center_mm"], 3, 0, 100) or not _vector(obj["size_mm"], 3, 0, 100) or min(obj["size_mm"]) <= 0 or
                obj["role"] not in ("structure", "defect")):
            raise ValueError("Frozen Twin requires bounded unique ordered supported primitives.")
        ids.add(obj["id"])
        for c, size, bound in zip(obj["center_mm"], obj["size_mm"], twin["size_mm"]):
            if c-size/2 < -1e-8 or c+size/2 > bound+1e-8: raise ValueError("Frozen primitive extends outside the specimen.")
        sx, sy, sz = obj["size_mm"]
        if (obj["shape"] in ("sphere", "cylinder") and abs(sx-sy) > 1e-8) or (obj["shape"] == "sphere" and abs(sx-sz) > 1e-8):
            raise ValueError("Frozen curved primitive dimensions are inconsistent.")
        for key, maximum in (("display_label", 48), ("assembly_id", 64)):
            if obj.get(key) is not None and not _label(obj[key], maximum, empty=key == "display_label"):
                raise ValueError("Frozen primitive metadata label is malformed.")
        if obj.get("layer_role") is not None and obj["layer_role"] not in ("base_die", "dram_die", "interdie_gap", "cap", "underfill", "contact", "microbump", "tsv"):
            raise ValueError("Frozen primitive layer role is unsupported.")
    _twin_metadata(twin)


def _twin_metadata(twin):
    reference = twin.get("reference")
    if reference is not None:
        if (type(reference) is not dict or set(reference) != {"product", "summary", "sources", "published_facts", "assumptions"} or
                not _label(reference["product"], 160) or not _label(reference["summary"], 2000) or
                type(reference["sources"]) is not list or not 1 <= len(reference["sources"]) <= 16 or
                type(reference["published_facts"]) is not list or not 1 <= len(reference["published_facts"]) <= 24 or
                type(reference["assumptions"]) is not list or not 1 <= len(reference["assumptions"]) <= 32 or
                any(not _label(v, 1600) for v in reference["assumptions"])):
            raise ValueError("Frozen Twin reference metadata is malformed.")
        sources = set()
        for source in reference["sources"]:
            if (type(source) is not dict or set(source) != {"id", "title", "url"} or not _label(source["id"], 64) or
                    source["id"] in sources or not _label(source["title"], 240) or not _label(source["url"], 4096) or
                    not source["url"].startswith(("http://", "https://"))):
                raise ValueError("Frozen Twin reference source is malformed.")
            sources.add(source["id"])
        for fact in reference["published_facts"]:
            if (type(fact) is not dict or set(fact) != {"label", "value", "source_ids"} or not _label(fact["label"], 120) or
                    not _label(fact["value"], 500) or type(fact["source_ids"]) is not list or not 1 <= len(fact["source_ids"]) <= 8 or
                    any(type(v) is not str or v not in sources for v in fact["source_ids"])):
                raise ValueError("Frozen Twin published fact cites an invalid reference.")
    image = twin.get("image_reference")
    if image is not None and (type(image) is not dict or set(image) != {"sha256", "width_px", "height_px", "pixel_size_um", "scale_status", "title", "source_note"} or
            not _digest(image["sha256"]) or any(type(image[k]) is not int or not 0 < image[k] <= 100000 for k in ("width_px", "height_px")) or
            not _range(image["pixel_size_um"], 0, 100000) or image["pixel_size_um"] == 0 or image["scale_status"] not in ("user_estimate", "calibrated") or
            not _label(image["title"], 240) or not _label(image["source_note"], 2000)):
        raise ValueError("Frozen Twin image-reference provenance is malformed.")
    stacks = twin.get("hbm_assemblies")
    if stacks is not None:
        if type(stacks) is not list or len(stacks) > 12: raise ValueError("Frozen Twin HBM metadata exceeds the supported inventory.")
        ids = set()
        for stack in stacks:
            _hbm_metadata(stack)
            if stack["id"] in ids: raise ValueError("Frozen HBM assembly IDs must be unique.")
            ids.add(stack["id"])
        if any(obj.get("assembly_id") is not None and obj["assembly_id"] not in ids for obj in twin["objects"]):
            raise ValueError("Frozen primitive references an absent HBM assembly.")
    elif any(obj.get("assembly_id") is not None for obj in twin["objects"]):
        raise ValueError("Frozen assembled primitives require their retained HBM metadata.")
    settings = twin.get("recommended_settings")
    if settings is not None:
        # Settings are frozen display metadata; no current defaults are applied.
        fields = {"path_model", "resolution", "depth_samples", "roi_mm", "energy_kev", "angle_deg", "photons", "noise", "frequency_mhz",
            "gate_start_us", "gate_end_us", "focus_mm", "probe_x_mm", "probe_y_mm", "include_defects", "seed"}
        if (type(settings) is not dict or not fields-{"depth_samples","roi_mm"} <= set(settings) <= fields or settings["path_model"] not in ("voxel_centers_v1", "continuous_columns_v1") or
                type(settings["resolution"]) is not int or settings["resolution"] not in (64,128,192) or settings.get("depth_samples") not in (None,128,256,512,1024) or
                any(type(settings[k]) is not bool for k in ("noise", "include_defects"))):
            raise ValueError("Frozen recommended settings are malformed.")
        for key, lo, hi in (("energy_kev",40,150),("angle_deg",-45,45),("frequency_mhz",10,150),("gate_start_us",0,10),
                ("gate_end_us",0,12),("focus_mm",0,twin["size_mm"][2]),("probe_x_mm",0,twin["size_mm"][0]),("probe_y_mm",0,twin["size_mm"][1])):
            if not _range(settings[key],lo,hi): raise ValueError("Frozen recommended setting exceeds its range.")
        if settings["gate_end_us"] <= settings["gate_start_us"]: raise ValueError("Frozen recommended gate is reversed.")
        for key,lo,hi in (("photons",1000,1000000),("seed",0,4294967295)):
            if type(settings[key]) is not int or not lo <= settings[key] <= hi: raise ValueError("Frozen integer setting is invalid.")
        roi = settings.get("roi_mm")
        if roi is not None and (not _vector(roi,4,0,100) or roi[2]-roi[0]<.05 or roi[3]-roi[1]<.05 or
                roi[2]>twin["size_mm"][0] or roi[3]>twin["size_mm"][1] or settings["angle_deg"] != 0 or
                not roi[0] <= settings["probe_x_mm"] <= roi[2] or not roi[1] <= settings["probe_y_mm"] <= roi[3]):
            raise ValueError("Frozen recommended ROI is invalid.")


def _hbm_metadata(stack):
    fields = {"id", "name", "center_xy_mm", "footprint_mm", "bottom_z_mm", "die_count", "die_thickness_um", "gap_um", "base_thickness_um",
        "cap_thickness_um", "functional_state", "physical_present", "evidence", "microstructure"}
    import re
    if (type(stack) is not dict or not fields-{"microstructure"} <= set(stack) <= fields or not _label(stack["id"],64) or not re.fullmatch(r"hbm-[1-9][0-9]*",stack["id"]) or
            not _label(stack["name"],120) or not _vector(stack["center_xy_mm"],2,0,100) or not _vector(stack["footprint_mm"],2,0,100) or
            min(stack["footprint_mm"])<=0 or type(stack["die_count"]) is not int or stack["die_count"] not in (8,12) or
            type(stack["physical_present"]) is not bool or stack["functional_state"] not in ("enabled","disabled","unknown") or not _label(stack["evidence"],1000)):
        raise ValueError("Frozen HBM assembly metadata is malformed.")
    for key,lo,hi in (("bottom_z_mm",0,6),("die_thickness_um",5,200),("gap_um",1,100),("base_thickness_um",5,300),("cap_thickness_um",1,300)):
        if not _range(stack[key],lo,hi) or stack[key]<=0: raise ValueError("Frozen HBM dimensions are unsupported.")
    if (stack["base_thickness_um"]+stack["die_count"]*(stack["die_thickness_um"]+stack["gap_um"])+stack["cap_thickness_um"])/1000 > stack["bottom_z_mm"]+1e-8:
        raise ValueError("Frozen HBM stack extends above the surface.")
    patch=stack.get("microstructure")
    if patch is None: return
    fields={"model_version","enabled","center_offset_xy_um","columns","rows","pitch_x_um","pitch_y_um","bump_diameter_um","tsv_diameter_um","evidence","source_note","defects"}
    if (type(patch) is not dict or set(patch)!=fields or patch["model_version"]!="hbm-explicit-patch-1" or type(patch["enabled"]) is not bool or
            not _vector(patch["center_offset_xy_um"],2,-100000,100000) or any(type(patch[k]) is not int or not 1<=patch[k]<=8 for k in ("rows","columns")) or
            any(not _range(patch[k],0,100000) or patch[k]<=0 for k in ("pitch_x_um","pitch_y_um","bump_diameter_um","tsv_diameter_um")) or
            not _label(patch["evidence"],1000) or not _label(patch["source_note"],2000) or type(patch["defects"]) is not list or len(patch["defects"])>4):
        raise ValueError("Frozen HBM microstructure metadata is malformed.")
    if max(patch["bump_diameter_um"],patch["tsv_diameter_um"])>=min(patch["pitch_x_um"],patch["pitch_y_um"]):
        raise ValueError("Frozen HBM feature diameters overlap their lattice pitches.")
    ids=set(); targets=set()
    for defect in patch["defects"]:
        if (type(defect) is not dict or not {"id","kind","row","column","layer_index","enabled"} <= set(defect) <= {"id","kind","row","column","layer_index","enabled","void_diameter_um"} or
                not _label(defect["id"],20) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*",defect["id"]) or defect["id"] in ids or
                defect["kind"] not in ("missing_bump","bump_void","tsv_void") or type(defect["enabled"]) is not bool or
                any(type(defect[k]) is not int or not 1<=defect[k]<=patch[bound] for k,bound in (("row","rows"),("column","columns"))) or
                type(defect["layer_index"]) is not int or not (0 if defect["kind"]=="tsv_void" else 1)<=defect["layer_index"]<=stack["die_count"]):
            raise ValueError("Frozen HBM defect target is malformed.")
        ids.add(defect["id"])
        family="tsv" if defect["kind"]=="tsv_void" else "microbump"
        target=(family,defect["layer_index"],defect["row"],defect["column"])
        if defect["enabled"] and target in targets: raise ValueError("Frozen HBM enabled targets must be unique.")
        if defect["enabled"]: targets.add(target)
        diameter=defect.get("void_diameter_um")
        if defect["kind"]=="missing_bump":
            if diameter is not None: raise ValueError("Missing bumps cannot carry a void diameter.")
        else:
            height=(stack["base_thickness_um"] if defect["layer_index"]==0 else stack["die_thickness_um"]) if family=="tsv" else stack["gap_um"]
            width=patch["tsv_diameter_um"] if family=="tsv" else patch["bump_diameter_um"]
            if not _number(diameter) or not 0<diameter<min(height,width): raise ValueError("Frozen HBM void is not contained in its target.")


def _validate_store_identity(value, *, column=False):
    expected=COLUMN_STORE_CONTRACT if column else STORE_CONTRACT
    if type(value) is not dict or set(value)!={"contract","implementation_sha256","runtime"} or value["contract"]!=expected:
        raise ValueError("Frozen material evidence store contract is unsupported.")
    hashes=value["implementation_sha256"]
    if type(hashes) is not dict or set(hashes)!=set(IMPLEMENTATION_FILES) or any(not _digest(v) for v in hashes.values()):
        raise ValueError("Frozen material evidence implementation identity is invalid.")
    runtime=value["runtime"]
    if (type(runtime) is not dict or set(runtime)!={"python","implementation","system","machine","numerical_packages"} or
            any(not _text(runtime[k],128) for k in ("python","implementation","system","machine")) or
            type(runtime["numerical_packages"]) is not dict or set(runtime["numerical_packages"])!={"numpy"} or not _text(runtime["numerical_packages"]["numpy"],128)):
        raise ValueError("Frozen material evidence runtime identity is malformed.")


def _account(report, measured):
    estimate=report["resources"]
    for key,limit in (("estimated_report_bytes",MAX_REPORT_BYTES),("estimated_report_expanded_bytes",MAX_REPORT_EXPANDED_BYTES),("estimated_peak_bytes",MAX_WORKSPACE_BYTES)):
        if type(estimate.get(key)) is not int or not 0<estimate[key]<=limit: raise ValueError("Material evidence resources exceed their supported bounds.")
    if (measured["encoded_bytes"]>estimate["estimated_report_bytes"] or measured["expanded_bytes"]>estimate["estimated_report_expanded_bytes"] or
            measured["expanded_bytes"]+12*measured["encoded_bytes"]+16*1024**2>estimate["estimated_peak_bytes"]):
        raise ValueError("Material evidence output exceeds its advertised workspace/serialization forecast.")
    minimum=_minimum_resources(report)
    if any(estimate[key]<value for key,value in minimum.items()):
        raise ValueError("Material evidence resource forecast under-reports its simultaneous frozen inputs and publication copies.")


def _minimum_resources(report):
    if report["kind"]==KIND:
        full={k:v for k,v in report["request"].items() if k!="twin_sha256"};full["twin"]=report["snapshots"][report["twin_sha256"]]
        retained=json_measure(full)["expanded_bytes"]
        source_count=0
        for digest,kind in report["snapshot_kinds"].items():
            if kind=="sls_report": retained+=json_measure(report["snapshots"][digest])["expanded_bytes"];source_count+=1
        base={key:report[key] for key in BASE_FIELDS-{"resources"}}
        size=json_measure(base)
    else:
        parent={**report["assignment_record"],"snapshots":report["snapshots"],"snapshot_kinds":report["snapshot_kinds"]}
        parent_size=json_measure(parent);retained=parent_size["expanded_bytes"];source_count=0
        row=json_measure({key:report[key] for key in COLUMN_FIELDS-{"resources"}})
        size={key:parent_size[key]+row[key] for key in ("encoded_bytes","expanded_bytes")}
    encoded=size["encoded_bytes"]+64*1024;expanded=size["expanded_bytes"]+2*1024**2
    peak=max(retained+256*1024**2 if source_count else 0,2*retained+expanded+12*encoded+32*1024**2)
    if report["kind"]==COLUMN_KIND and "sls_report" in report["snapshot_kinds"].values():
        peak=max(peak,retained+256*1024**2)
    return {"estimated_report_bytes":encoded,"estimated_report_expanded_bytes":expanded,"estimated_peak_bytes":peak}


def _timestamp(value):
    try:
        if datetime.fromisoformat(value).tzinfo is None: raise ValueError
    except (ValueError,TypeError) as exc: raise ValueError("Saved material evidence requires a timezone-aware timestamp.") from exc


def _ordered(values): return [name for name in MATERIAL_IDS if name in values]


def _binding_records(request, sources, digests):
    authored=request["bindings"]
    if type(authored) is not list or len(authored)>6: raise ValueError("Material assignment supports at most six bindings.")
    records=[]; ids=set()
    for binding in authored:
        if (type(binding) is not dict or set(binding)!={"material_id","name","note","origin"} or binding["material_id"] not in MATERIAL_IDS or
                binding["material_id"] in ids or not _text(binding["name"],160) or not _text(binding["note"],2000) or type(binding["origin"]) is not dict):
            raise ValueError("Frozen material binding identity is invalid or duplicated.")
        ids.add(binding["material_id"]); origin=binding["origin"]
        if origin.get("kind")=="manual":
            if set(origin)!={"kind",*PARAMETERS}: raise ValueError("Manual binding requires exactly four explicit parameters.")
            parameters={k:origin[k] for k in PARAMETERS}; provenance={"kind":"manual"}
        elif origin.get("kind")=="report_layer":
            if set(origin)!={"kind","report_id","layer_index"}: raise ValueError("Referenced bindings cannot override copied parameters.")
            identifier=_json._checked_id(origin["report_id"]); index=origin["layer_index"]
            if (type(index) is not int or not 0<=index<=7 or identifier not in sources or index>=len(sources[identifier]["stack"]["layers"])):
                raise ValueError("Material binding selects an absent source report/layer.")
            parameters={k:sources[identifier]["stack"]["layers"][index][k] for k in PARAMETERS}
            provenance={**origin,"source_snapshot_sha256":digests[identifier]}
        else: raise ValueError("Material binding origin is unsupported.")
        _parameters(parameters)
        record={"material_id":binding["material_id"],"name":binding["name"],"note":binding["note"],"origin":provenance,
            "parameters":parameters,"parameters_sha256":json_measure(parameters)["sha256"],"evidence":"manual_scalar_longitudinal_assumption",
            "nominal_density_kg_m3":NOMINAL_DENSITIES[binding["material_id"]],
            "nominal_density_difference_kg_m3":parameters["density_kg_m3"]-NOMINAL_DENSITIES[binding["material_id"]]}
        record["assignment_sha256"]=json_measure(record)["sha256"]; records.append(record)
    return records


def _validate_assignment(report):
    try: return _assignment_contract(report)
    except (KeyError,TypeError,AttributeError,IndexError,OverflowError) as exc:
        raise ValueError("Material assignment has a malformed frozen contract.") from exc


def _assignment_contract(report):
    if (type(report) is not dict or set(report)!=BASE_FIELDS|STORED_FIELDS or report["kind"]!=KIND or
            type(report["schema_version"]) is not int or report["schema_version"]!=1 or not _text(report["processing_version"],128) or
            report["propagation_available"] is not False or report["identity"]!=IDENTITY or report["ambient_policy"]!=AMBIENT_POLICY or report["warnings"]!=WARNINGS):
        raise ValueError("Frozen material assignment identity/scope is unsupported.")
    identifier=_json._checked_id(report["id"])
    if report["assignment_id"]!=identifier: raise ValueError("Material assignment IDs disagree.")
    _timestamp(report["created_at"]); _validate_store_identity(report["store_identity"])
    request=report["request"]
    if (type(request) is not dict or set(request)!={"kind","name","include_defects","coverage_scope","required_material_ids","bindings","twin_sha256"} or
            request["kind"]!=KIND or not _text(request["name"],160) or type(request["include_defects"]) is not bool or
            request["coverage_scope"] not in ("all_included","selected_materials") or type(request["required_material_ids"]) is not list or
            len(request["required_material_ids"])>6 or any(type(v) is not str or v not in MATERIAL_IDS for v in request["required_material_ids"]) or
            len(set(request["required_material_ids"]))!=len(request["required_material_ids"]) or
            (request["coverage_scope"]=="selected_materials")!=bool(request["required_material_ids"])):
        raise ValueError("Frozen material assignment request/scope is malformed.")
    snapshots,kinds=report["snapshots"],report["snapshot_kinds"]
    twin_sha=report["twin_sha256"]
    if (type(snapshots) is not dict or not 1<=len(snapshots)<=7 or type(kinds) is not dict or set(kinds)!=set(snapshots) or
            not _digest(twin_sha) or request["twin_sha256"]!=twin_sha or kinds.get(twin_sha)!="twin"):
        raise ValueError("Material assignment snapshot closure is malformed.")
    sources={};digests={}
    for digest,snapshot in snapshots.items():
        if not _digest(digest) or json_measure(snapshot)["sha256"]!=digest: raise ValueError("Material snapshot checksum mismatch.")
        if kinds[digest]=="twin":
            if digest!=twin_sha: raise ValueError("Material assignment may freeze exactly one Twin.")
            _twin(snapshot)
        elif kinds[digest]=="sls_report":
            _validate_source(snapshot)
            if snapshot["id"] in sources: raise ValueError("One source report cannot have multiple frozen snapshots.")
            sources[snapshot["id"]]=snapshot; digests[snapshot["id"]]=digest
        else: raise ValueError("Unsupported material snapshot kind.")
    twin=snapshots[twin_sha]
    full={k:v for k,v in request.items() if k!="twin_sha256"}; full["twin"]=twin
    if json_measure(full)["sha256"]!=report["input_request_sha256"]: raise ValueError("Material assignment normalized input checksum mismatch.")
    records=_binding_records(request,sources,digests)
    if json_measure(records)["sha256"]!=json_measure(report["bindings"])["sha256"]:
        raise ValueError("Resolved material values/labels/evidence differ from their exact authored or referenced origin.")
    needed={r["origin"]["report_id"] for r in records if r["origin"]["kind"]=="report_layer"}
    if set(sources)!=needed: raise ValueError("Material assignment contains unused or missing source snapshots.")
    objects=[o for o in twin["objects"] if request["include_defects"] or o["role"]!="defect"]
    inventory={o["material"] for o in objects}
    required=inventory if request["coverage_scope"]=="all_included" else set(request["required_material_ids"])
    supplied={r["material_id"] for r in records}; missing=required-supplied
    if not supplied<=inventory or not required<=inventory: raise ValueError("Material bindings/scope exceed included primitive inventory.")
    expected={"scope":request["coverage_scope"],"inventory_material_ids":_ordered(inventory),"required_material_ids":_ordered(required),
        "supplied_material_ids":_ordered(supplied),"missing_material_ids":_ordered(missing),"complete":not missing,
        "status":"incomplete" if missing else "complete_for_"+request["coverage_scope"],"inventory_definition":INVENTORY_DEFINITION}
    if json_measure(report["coverage"])["sha256"]!=json_measure(expected)["sha256"]: raise ValueError("Material coverage facts disagree with the frozen scope and inventory.")
    geometry={"name":twin["name"],"size_mm":twin["size_mm"],"primitive_count":len(twin["objects"]),"included_primitive_count":len(objects),
        "excluded_defect_count":len(twin["objects"])-len(objects),"hbm_assembly_count":len(twin.get("hbm_assemblies") or [])}
    if json_measure(report["geometry"])["sha256"]!=json_measure(geometry)["sha256"]: raise ValueError("Material geometry summary disagrees with its frozen Twin.")
    _resource_identity(report["resources"],sources=len(sources),columns=0)
    if report["report_sha256"]!=json_measure({k:v for k,v in report.items() if k!="report_sha256"})["sha256"]:
        raise ValueError("Material assignment report checksum mismatch.")
    return twin,request,records


def _resource_identity(resources, *, sources, columns):
    if (type(resources) is not dict or set(resources)!={"source_report_count","estimated_report_bytes","estimated_report_expanded_bytes","estimated_peak_bytes",
            "column_count","propagation_work_units","workspace_definition"} or type(resources["source_report_count"]) is not int or resources["source_report_count"]!=sources or
            type(resources["column_count"]) is not int or resources["column_count"]!=columns or type(resources["propagation_work_units"]) is not int or resources["propagation_work_units"]!=0 or
            not _text(resources["workspace_definition"],2000)):
        raise ValueError("Material resource identity must describe zero propagation work and its complete snapshot closure.")


def _validate_column(report):
    try: return _column_contract(report)
    except (KeyError,TypeError,AttributeError,IndexError,OverflowError) as exc:
        raise ValueError("Saved material column has a malformed frozen contract.") from exc


def _column_contract(report):
    if (type(report) is not dict or set(report)!=COLUMN_FIELDS|COLUMN_STORED_FIELDS or report["kind"]!=COLUMN_KIND or
            type(report["schema_version"]) is not int or report["schema_version"]!=1 or not _text(report["processing_version"],128) or
            report["propagation_available"] is not False or report["path_contract"]!=IDENTITY["path_contract"] or
            report["ambient_policy"]!=AMBIENT_POLICY or report["warnings"]!=WARNINGS):
        raise ValueError("Saved material column identity/scope is unsupported.")
    identifier=_json._checked_id(report["id"])
    if report["column_id"]!=identifier: raise ValueError("Saved material column IDs disagree.")
    _json._checked_id(report["assignment_id"]);_timestamp(report["created_at"]);_validate_store_identity(report["store_identity"],column=True)
    record=report["assignment_record"]
    if type(record) is not dict or set(record)!=(BASE_FIELDS|STORED_FIELDS)-{"snapshots","snapshot_kinds"}:
        raise ValueError("Saved column must flatten its parent snapshot closure exactly once.")
    parent={**record,"snapshots":report["snapshots"],"snapshot_kinds":report["snapshot_kinds"]}
    twin,request,bindings=_validate_assignment(parent)
    _account(parent,json_measure(parent))
    if parent["id"]!=report["assignment_id"] or parent["report_sha256"]!=report["assignment_sha256"] or parent["twin_sha256"]!=report["twin_sha256"]:
        raise ValueError("Saved column parent/twin identity mismatch.")
    if any(report["store_identity"]["implementation_sha256"][k]!=parent["store_identity"]["implementation_sha256"][k] for k in GEOMETRY_FILES):
        raise ValueError("Saved column geometry identity differs from its frozen parent.")
    if (not _range(report["x_mm"],0,twin["size_mm"][0]) or not _range(report["y_mm"],0,twin["size_mm"][1]) or
            not _exact(report["depth_mm"],twin["size_mm"][2]) or type(report["segments"]) is not list or
            type(report["segment_count"]) is not int or report["segment_count"]!=len(report["segments"]) or not 1<=report["segment_count"]<=1201):
        raise ValueError("Saved column coordinates or segment dimensions are invalid.")
    assignments={b["material_id"]:b["assignment_sha256"] for b in bindings}; start=0.; previous=None; actual=set(); ambient=False
    for i,segment in enumerate(report["segments"]):
        fields={"index","z_start_mm","z_end_mm","thickness_mm","material_label","material_id","assignment_sha256","coverage_status"}
        if (type(segment) is not dict or set(segment)!=fields or type(segment["index"]) is not int or segment["index"]!=i or
                not _exact(segment["z_start_mm"],start) or not _number(segment["z_end_mm"]) or not start<segment["z_end_mm"]<=report["depth_mm"] or
                not _exact(segment["thickness_mm"],segment["z_end_mm"]-start) or type(segment["material_label"]) is not int or
                str(segment["material_label"]) not in IDENTITY["material_label_map"] or segment["material_label"]==previous):
            raise ValueError("Saved column must be an ordered positive full-depth material partition.")
        label=segment["material_label"]; material=IDENTITY["material_label_map"][str(label)]
        if material!=segment["material_id"]: raise ValueError("Saved column material label/ID mismatch.")
        if label==0: status,digest,ambient="ambient_policy",None,True
        else:
            actual.add(material); digest=assignments.get(material);status="assigned" if digest else "missing"
            if material not in parent["coverage"]["inventory_material_ids"]: raise ValueError("Saved column material is outside the included primitive inventory.")
        if segment["coverage_status"]!=status or segment["assignment_sha256"]!=digest:
            raise ValueError("Saved column assignment evidence disagrees with frozen bindings/ambient policy.")
        start=segment["z_end_mm"];previous=label
    if not _exact(start,report["depth_mm"]): raise ValueError("Saved column does not retain the entire specimen depth.")
    error=abs(sum((Fraction(s["thickness_mm"]) for s in report["segments"]),Fraction())-Fraction(report["depth_mm"]))
    if error>Fraction(8*math.ulp(report["depth_mm"])): raise ValueError("Saved column thickness sum is inconsistent.")
    supplied=actual&set(assignments);missing=actual-set(assignments)
    coverage={"required_material_ids":_ordered(actual),"supplied_material_ids":_ordered(supplied),"missing_material_ids":_ordered(missing),
        "complete":not missing,"status":"incomplete" if missing else "complete_for_column","ambient_present":ambient}
    if json_measure(report["coverage"])["sha256"]!=json_measure(coverage)["sha256"]: raise ValueError("Saved column coverage exceeds its actual positive-length materials.")
    _diagnostics(report,twin,request)
    _resource_identity(report["resources"],sources=0,columns=1)
    if report["report_sha256"]!=json_measure({k:v for k,v in report.items() if k!="report_sha256"})["sha256"]:
        raise ValueError("Saved material column checksum mismatch.")
    return parent


def _diagnostics(report,twin,request):
    diag=report["diagnostics"]
    fields={"candidate_tests","event_bound","event_work_bound","segment_bound","estimated_path_workspace_bytes","contract_version","tau_z_mm","columns",
        "primitive_intersections","adjusted_endpoint_count","max_endpoint_adjustment_mm","interface_count","specimen_depth_mm","coincidence_policy",
        "segment_count","primitive_event_count","allocated_path_bytes"}
    if type(diag) is not dict or set(diag)!=fields: raise ValueError("Saved path diagnostics require their complete frozen contract.")
    integers=fields-{"contract_version","tau_z_mm","max_endpoint_adjustment_mm","specimen_depth_mm","coincidence_policy"}
    if any(type(diag[k]) is not int or diag[k]<0 for k in integers): raise ValueError("Saved path counters must be nonnegative integers.")
    included=sum(request["include_defects"] or o["role"]!="defect" for o in twin["objects"])
    count=report["segment_count"];tau=32*2**-52*max(1.,report["depth_mm"])
    interfaces=count-1+int(report["segments"][0]["material_label"]!=0)+int(report["segments"][-1]["material_label"]!=0)
    if (diag["contract_version"]!=IDENTITY["path_contract"] or diag["columns"]!=1 or diag["candidate_tests"]!=included or
            diag["segment_count"]!=count or not count<=diag["segment_bound"]<=1201 or diag["segment_bound"]!=diag["event_bound"]+1 or
            diag["event_bound"]>2*included or diag["primitive_event_count"]!=2*diag["primitive_intersections"] or
            diag["primitive_event_count"]>diag["event_bound"] or diag["interface_count"]!=interfaces or diag["allocated_path_bytes"]!=16+9*count or
            not _exact(diag["tau_z_mm"],tau) or not _range(diag["max_endpoint_adjustment_mm"],0,tau) or
            not _exact(diag["specimen_depth_mm"],report["depth_mm"]) or diag["coincidence_policy"]!="nontransitive first-endpoint groups; exact specimen endpoints take precedence"):
        raise ValueError("Saved path diagnostics disagree with the full-depth evidence.")
    bound=diag["event_bound"]
    if (bound%2 or diag["event_work_bound"]!=bound*(1+math.ceil(math.log2(max(2,bound)))) or
            diag["estimated_path_workspace_bytes"]!=32*(bound+1)+64*bound+34+16*1024**2 or
            diag["adjusted_endpoint_count"]>diag["primitive_event_count"] or
            bool(diag["adjusted_endpoint_count"])!=(diag["max_endpoint_adjustment_mm"]>0)):
        raise ValueError("Saved path resource and endpoint diagnostics violate their frozen arithmetic relationships.")


def _summary(report, *, column=False):
    value={k:report[k] for k in ("id","kind","created_at","report_sha256","processing_version","coverage","propagation_available")}
    if column: value.update(column_id=report["column_id"],assignment_id=report["assignment_id"],x_mm=report["x_mm"],y_mm=report["y_mm"],depth_mm=report["depth_mm"],segment_count=report["segment_count"])
    else: value.update(assignment_id=report["assignment_id"],name=report["request"]["name"],twin_sha256=report["twin_sha256"],geometry=report["geometry"],binding_count=len(report["bindings"]))
    json_measure(value,64*1024,1024**2);return value


def _publish(path, report, recheck):
    path.parent.mkdir(parents=True,exist_ok=True); recheck()
    temporary=path.with_name(f".{path.stem}.tmp"); owned=False
    try:
        with temporary.open("xb") as stream:
            owned=True
            for part in _json._tokens(report): stream.write(part)
            stream.flush(); os.fsync(stream.fileno())
        recheck(); os.link(temporary,path)
    finally:
        if owned: temporary.unlink(missing_ok=True)


def _file_hash(path, limit=MAX_REPORT_BYTES):
    """Bounded raw identity checks need no second retained JSON decode."""
    _json._reject_link(path); info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size>limit: raise ValueError("Material evidence source must be a bounded regular file.")
    digest=hashlib.sha256(); total=0
    with path.open("rb") as stream:
        opened=os.fstat(stream.fileno())
        if (opened.st_dev,opened.st_ino)!=(info.st_dev,info.st_ino): raise ValueError("Material source changed while opening.")
        while block:=stream.read(64*1024):
            total+=len(block)
            if total>limit: raise ValueError("Material evidence source exceeds its byte limit.")
            digest.update(block)
    if total!=info.st_size: raise ValueError("Material source size changed while reading.")
    return digest.hexdigest()


class MaterialAssignmentStore:
    def __init__(self, root):
        self.root=Path(root).absolute()
        self.directory=self.root/"material-assignments"
        self.columns_directory=self.root/"material-assignment-columns"

    def _path(self, identifier, column_id=None):
        identifier=_json._checked_id(identifier)
        directory=self.directory if column_id is None else self.columns_directory/identifier
        leaf=identifier if column_id is None else _json._checked_id(column_id)
        path=directory/f"{leaf}.json"
        for ancestor in (self.root,*self.root.parents,self.directory,self.columns_directory,directory,path): _json._reject_link(ancestor)
        if directory.resolve().parent != (self.root if column_id is None else self.columns_directory).resolve() or path.resolve().parent != directory.resolve():
            raise ValueError("Material evidence path leaves its data directory.")
        return path

    def _load(self, request):
        from .material_assignment_schemas import SLSMaterialAssignmentRequest
        from .sls_reports import SLSReportStore
        request=SLSMaterialAssignmentRequest.model_validate(request)
        normalized=request.model_dump(mode="json")
        measure=json_measure(normalized)
        retained=measure["expanded_bytes"]; sources={}; digests={}
        ids=sorted({b["origin"]["report_id"] for b in normalized["bindings"] if b["origin"]["kind"]=="report_layer"})
        if len(ids)>6: raise ValueError("Material assignments support at most six distinct reports.")
        for identifier in ids:
            path=SLSReportStore(self.root)._path(identifier)
            if not path.exists(): raise KeyError(identifier)
            remaining=MAX_REPORT_EXPANDED_BYTES-retained-8*1024**2
            if remaining<=0 or retained+256*1024**2>MAX_WORKSPACE_BYTES:
                raise ValueError("Retained material sources exceed the aggregate admission budget.")
            raw_hash=_file_hash(path,MAX_SOURCE_BYTES)
            source,size=bounded_json_read(path,MAX_SOURCE_BYTES,min(MAX_SOURCE_EXPANDED_BYTES,remaining),retained_bytes=retained)
            _validate_source(source,identifier)
            if _file_hash(path,MAX_SOURCE_BYTES)!=raw_hash: raise ValueError("A source changed during material assignment validation.")
            retained+=size["expanded_bytes"]; sources[identifier]=source; digests[identifier]=raw_hash
        return request,sources,digests

    def estimate(self, request):
        from .material_assignments import estimate_assignment
        request,sources,_=self._load(request)
        result=estimate_assignment(request,sources)
        json_measure(result)
        return result

    def create(self, request):
        from .material_assignments import build_assignment
        identifier=str(uuid4()); path=self._path(identifier)
        if path.exists(): raise FileExistsError(path)
        request,sources,digests=self._load(request)
        result=build_assignment(request,sources)
        report={**result,"schema_version":1,"kind":KIND,"id":identifier,"assignment_id":identifier,
            "created_at":datetime.now(timezone.utc).isoformat(),
            "store_identity":{"contract":STORE_CONTRACT,"implementation_sha256":_fingerprints(),"runtime":_runtime()}}
        report["report_sha256"]=json_measure(report)["sha256"]
        measured=json_measure(report); _validate_assignment(report); _account(report,measured)
        self._recheck_sources(digests,measured["expanded_bytes"])
        _disk(self.root,measured["encoded_bytes"]); _publish(path,report,lambda:self._path(identifier))
        return report

    def _recheck_sources(self, digests, retained):
        from .sls_reports import SLSReportStore
        for identifier,digest in digests.items():
            path=SLSReportStore(self.root)._path(identifier)
            if not path.exists(): raise KeyError(identifier)
            if _file_hash(path,MAX_SOURCE_BYTES)!=digest: raise ValueError("A source report changed during material assignment publication.")

    def read(self, identifier):
        path=self._path(identifier)
        if not path.exists(): raise KeyError(identifier)
        report,measure=bounded_json_read(path)
        _validate_assignment(report); _account(report,measure)
        if report["id"]!=identifier: raise ValueError("Material assignment file identity mismatch.")
        return report

    def create_column(self, identifier, request):
        from .material_assignment_schemas import MaterialColumnRequest
        from .material_assignments import inspect_column
        parent_path=self._path(identifier)
        if not parent_path.exists(): raise KeyError(identifier)
        parent_raw_hash=_file_hash(parent_path)
        parent=self.read(identifier)
        if _file_hash(self._path(identifier))!=parent_raw_hash: raise ValueError("Material assignment changed while being validated.")
        current=_fingerprints()
        if any(parent["store_identity"]["implementation_sha256"][k]!=current[k] for k in GEOMETRY_FILES):
            raise ValueError("New columns require the exact supported frozen geometry/path implementation. Historical saved columns remain available.")
        request=MaterialColumnRequest.model_validate(request)
        column_id=str(uuid4()); path=self._path(identifier,column_id)
        if path.exists(): raise FileExistsError(path)
        result=inspect_column(parent,request.x_mm,request.y_mm)
        report={**result,"schema_version":1,"id":column_id,"column_id":column_id,"assignment_id":identifier,
            "assignment_sha256":parent["report_sha256"],"assignment_record":{k:v for k,v in parent.items() if k not in ("snapshots","snapshot_kinds")},
            "snapshots":parent["snapshots"],"snapshot_kinds":parent["snapshot_kinds"],"created_at":datetime.now(timezone.utc).isoformat(),
            "store_identity":{"contract":COLUMN_STORE_CONTRACT,"implementation_sha256":current,"runtime":_runtime()}}
        report["report_sha256"]=json_measure(report)["sha256"]
        measured=json_measure(report); _validate_column(report); _account(report,measured)
        if _file_hash(self._path(identifier))!=parent_raw_hash: raise ValueError("Material assignment changed during column inspection.")
        _disk(self.root,measured["encoded_bytes"]); _publish(path,report,lambda:self._path(identifier,column_id))
        return report

    inspect=create_column

    def read_column(self, identifier, column_id):
        path=self._path(identifier,column_id)
        if not path.exists(): raise KeyError(column_id)
        report,measured=bounded_json_read(path)
        _validate_column(report); _account(report,measured)
        if report["assignment_id"]!=identifier or report["column_id"]!=column_id: raise ValueError("Saved material column file identity mismatch.")
        return report

    def _list(self, identifier=None, *, limit=50, offset=0):
        if type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or not 0<=offset<MAX_CATALOG_FILES:
            raise ValueError("Material evidence pagination requires limit1..100 and offset0..9999.")
        if identifier is None:
            self._path("00000000-0000-0000-0000-000000000000"); directory=self.directory
        else:
            self._path(identifier,"00000000-0000-0000-0000-000000000000"); directory=self.columns_directory/identifier
        if not directory.exists(): return []
        ids=[]
        with os.scandir(directory) as entries:
            for count,entry in enumerate(entries,1):
                if count>MAX_CATALOG_FILES: raise ValueError("Material evidence catalog exceeds its bounded entry count.")
                if entry.name.startswith('.') and entry.name.endswith('.tmp'): continue
                if not entry.name.endswith('.json'): raise ValueError("Unexpected entry in material evidence catalog.")
                leaf=_json._checked_id(entry.name[:-5]); path=self._path(leaf) if identifier is None else self._path(identifier,leaf)
                if not stat.S_ISREG(path.stat().st_mode): raise ValueError("Material evidence catalog requires regular files.")
                ids.append(leaf)
        ids.sort(reverse=True)
        results=[]
        for leaf in ids[offset:offset+limit]:
            report=self.read(leaf) if identifier is None else self.read_column(identifier,leaf)
            results.append(_summary(report,column=identifier is not None));del report
            json_measure(results,8*1024**2,8*1024**2)
        return results

    def list(self, limit=50, offset=0): return self._list(limit=limit,offset=offset)
    def list_columns(self, identifier, limit=50, offset=0): return self._list(identifier,limit=limit,offset=offset)
