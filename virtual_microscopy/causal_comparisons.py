"""Bounded comparisons of authoritative saved float64 causal pressure products.

Only frozen represented sources are inspected. No twin construction, propagation,
registration, resampling, phase fitting or normalization occurs here.
"""
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .causal_datasets import (CausalSamDatasetStore, SIGNALS, COORDINATES,
    typed_sha256, bounded_payload as source_payload, _workspace,
    MAX_EXPANDED_BYTES, MAX_MANIFEST_BYTES)
from .datasets import json_sha256
from .causal_comparison_schemas import CausalComparisonRequest
from .causal_comparison_math import (checked_differences, column_bounds, Metrics,
                                    gate_products, check_arithmetic_environment)

KIND = "sam_causal_comparison"
PROCESSING_VERSION = "causal-comparison-0.14.0"
MAX_WORKSPACE_BYTES = 512*1024**2
BOUND_KEYS = ("source_sum", "complex_arithmetic", "complex_total", "envelope_arithmetic", "envelope_total")
SEMANTICS = {
    "path_model": "continuous_columns_v1", "path_contract_version": "ordered-column-paths-1",
    "response_model": "layered_causal_gamma_v1", "excitation_model": "causal_gamma_peak_phase_v1",
    "observation_model": "independent_columns_v1", "focus_model": "none",
    "certificate_version": "causal-column-certificate-1",
    "time_zero": "causal gamma onset at the transducer reference; lossless incident-medium standoff adds round-trip delay, and gamma peak is later than onset",
    "envelope_processing": "magnitude of full coherent complex gamma response; independently certified returned double, not a Hilbert transform of real RF",
    "material_model": "frozen nominal positive scalar impedances/speeds; every finite layer lossless; water exteriors and lossless standoff",
    "rf_unit": "relative signed pressure", "imaginary_unit": "relative quadrature pressure",
    "envelope_unit": "relative complex-pressure magnitude", "error_bound_unit": "absolute relative pressure",
    "coordinate_units": {"x": "mm", "y": "mm", "time": "us"},
    "depth_mapping_supported": False,
}
PULSE_FIELDS = ("center_frequency_mhz", "fractional_bandwidth", "gamma_order", "surface_standoff_mm")
WARNINGS = [
    "Reference A is a selected synthetic baseline, not measured ground truth. Differences are candidate B minus reference A.",
    "Samplewise bounds concern represented scalar model outputs and comparison subtraction. Summary statistics and gate maps are ordinary diagnostics without new enclosures.",
    "Geometry/material uncertainty, sampling, physical resolution and defect detectability are outside these numerical bounds.",
    "Gamma timing and repeated returns do not identify unique depths. No alignment, interpolation, phase fitting or amplitude normalization is performed.",
]


class CausalComparisonCompatibilityError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("Saved causal sources require identical coordinates and supported matching excitation/observation semantics.")


@dataclass
class Source:
    store: object
    identifier: str
    manifest: dict
    group: object
    coordinates: dict
    expanded_bytes: int
    manifest_sha256: str


def _budget(value, description):
    if value > MAX_WORKSPACE_BYTES:
        raise ValueError(f"Causal comparison {description} exceeds the 512 MiB owned-workspace limit.")


def _source(root, identifier, retained_bytes=0):
    # Reserve worst-case bounded manifest parsing before the first decode.
    _budget(retained_bytes + 3*MAX_EXPANDED_BYTES + 2*MAX_MANIFEST_BYTES + 32*1024**2, "source verification")
    store = CausalSamDatasetStore(root)
    manifest = store.verify_complete(identifier)
    payload = source_payload(manifest)
    expanded = _workspace(payload)
    group = store._open_checked(identifier, manifest)
    coords = {key: np.asarray(group[key][:], dtype=np.float64) for key in COORDINATES}
    return Source(store, identifier, manifest, group, coords, expanded, json_sha256(manifest))


def _numeric_stack(entry):
    stack = entry["stack"]
    return {"incident": {k:stack["incident"][k] for k in ("impedance_mrayl", "sound_speed_m_s")},
            "terminal": {k:stack["terminal"][k] for k in ("impedance_mrayl", "sound_speed_m_s")},
            "layers": [{k:layer[k] for k in ("thickness_mm", "impedance_mrayl", "sound_speed_m_s", "pressure_loss_db_mm")}
                       for layer in stack["layers"]]}


def _exterior(source):
    keys = ("impedance_mrayl", "sound_speed_m_s")
    values = []
    for entry in source.manifest["estimate"]["stack_table"]:
        stack = entry["stack"]
        values.append(tuple(stack[side][key] for side in ("incident", "terminal") for key in keys))
        if any(layer["pressure_loss_db_mm"] != 0 for layer in stack["layers"]):
            raise CausalComparisonCompatibilityError([{"field":"material_loss", "message":"Only the frozen lossless column contract is supported."}])
    if not values or any(v != values[0] for v in values):
        raise CausalComparisonCompatibilityError([{"field":"exterior", "message":"Every column must have the same incident/substrate media."}])
    return values[0]


def _compatible(a, b):
    issues = []
    def issue(field, left, right, message="Frozen values differ."):
        issues.append({"field":field, "reference":left, "candidate":right, "message":message})
    for side, source in (("reference", a), ("candidate", b)):
        m = source.manifest
        for field, expected in SEMANTICS.items():
            if m["metadata"].get(field) != expected:
                issue(f"{side}.metadata.{field}", m["metadata"].get(field), expected, "Missing or unsupported frozen semantic contract.")
        if m.get("certificate_contract") != "causal-column-certificate-1" or m["solver"].get("certificate_contract") != "causal-column-certificate-1":
            issue(f"{side}.certificate_contract", m.get("certificate_contract"), "causal-column-certificate-1")
    if a.manifest["shape"] != b.manifest["shape"]:
        issue("shape", a.manifest["shape"], b.manifest["shape"])
    for key in COORDINATES:
        av,bv = a.coordinates[key],b.coordinates[key]
        if av.shape != bv.shape or av.tobytes() != bv.tobytes():
            issue(key, typed_sha256(av), typed_sha256(bv), "Actual saved float64 coordinates must match bit-exactly.")
    for key in SIGNALS + ("error_bound",) + COORDINATES:
        for field in ("axes", "dtype", "units"):
            av,bv = a.manifest["arrays"][key].get(field),b.manifest["arrays"][key].get(field)
            if av != bv:
                issue(f"arrays.{key}.{field}",av,bv)
    if a.manifest["estimate"]["extent_mm"] != b.manifest["estimate"]["extent_mm"]:
        issue("extent_mm",a.manifest["estimate"]["extent_mm"],b.manifest["estimate"]["extent_mm"])
    aa,ba = (s.manifest["request"]["acquisition"] for s in (a,b))
    for key in PULSE_FIELDS:
        if key not in aa or key not in ba or aa[key] != ba[key]:
            issue(f"acquisition.{key}", aa.get(key), ba.get(key))
    if _exterior(a) != _exterior(b):
        issue("exterior_media",_exterior(a),_exterior(b))
    if issues:
        raise CausalComparisonCompatibilityError(issues)


def _differences(a,b):
    am,bm = a.manifest,b.manifest
    aa,ba = am["request"]["acquisition"],bm["request"]["acquisition"]
    fields = [{"field":key,"reference":aa.get(key),"candidate":ba.get(key)}
              for key in sorted(aa.keys()|ba.keys()) if aa.get(key) != ba.get(key)]
    material_names = sorted(am["metadata"]["resolved_materials"].keys() | bm["metadata"]["resolved_materials"].keys())
    materials = [{"material":key,"reference":am["metadata"]["resolved_materials"].get(key),
                  "candidate":bm["metadata"]["resolved_materials"].get(key)} for key in material_names
                 if am["metadata"]["resolved_materials"].get(key) != bm["metadata"]["resolved_materials"].get(key)]
    signatures = []
    for m in (am,bm):
        numeric = [json_sha256(_numeric_stack(entry)) for entry in m["estimate"]["stack_table"]]
        signatures.append([[numeric[i] for i in row] for row in m["estimate"]["class_index"]])
    return {"acquisition":fields, "resolved_materials":materials,
        "twin_changed":json_sha256(am["request"]["twin"]) != json_sha256(bm["request"]["twin"]),
        "geometry_paths_changed":am["metadata"]["geometry_path_sha256"] != bm["metadata"]["geometry_path_sha256"],
        "resolved_numeric_columns_changed":sum(av!=bv for ar,br in zip(*signatures) for av,bv in zip(ar,br)),
        "implementation_changed":am["solver"] != bm["solver"],
        "source_twin_sha256":{"reference":json_sha256(am["request"]["twin"]),"candidate":json_sha256(bm["request"]["twin"])},
        "interpretation":"Twin hashes include names and metadata. Class numbers are local identities; changed inputs do not establish a unique physical cause."}


def _read_row(source,y):
    m = source.manifest
    products = source.store._read_row(source.identifier,m,source.group,y)
    chunk = m["completed_chunks"][str(y)]
    if any(typed_sha256(value) != chunk.get(f"{name}_sha256") for name,value in products.items()):
        raise ValueError("A source row changed or failed its frozen typed checksum during comparison.")
    source.store._row_matches(m,y,products,m["class_certificates"])
    return {key:products[key][0] for key in SIGNALS},products["error_bound"][0]


def _unchanged(source):
    if json_sha256(source.store.manifest(source.identifier)) != source.manifest_sha256:
        raise ValueError("A source manifest changed during comparison.")


def _gate(time,start,end):
    if not np.isfinite([start,end]).all() or not time[0] <= start < end <= time[-1]:
        raise ValueError("Comparison gate must lie within the actual saved recording centers.")
    lo,hi = int(np.searchsorted(time,start,side="left")),int(np.searchsorted(time,end,side="right"))
    if lo >= hi:
        raise ValueError("The comparison gate contains no saved time centers.")
    return {"start_us":start,"end_us":end,"actual_start_us":float(time[lo]),"actual_end_us":float(time[hi-1]),
            "sample_count":hi-lo,"start_index":lo,"stop_index_exclusive":hi,
            "scope":"Inclusive actual saved centers; gate reductions are ordinary floating diagnostics."}


def _cursor(source,**indices):
    ny,nx,nt = source.manifest["shape"]
    result = {}
    for key,n in (("x",nx),("y",ny),("time",nt)):
        value = indices.get(f"{key}_index")
        value = n//2 if value is None else value
        if type(value) is not int or not 0 <= value < n:
            raise ValueError(f"Comparison {key} index must be an integer in 0..{n-1}.")
        result[f"{key}_index"] = value
        coordinate = f"{key}_{'us' if key == 'time' else 'mm'}"
        result[coordinate] = float(source.coordinates[coordinate][value])
    return result


def _summary(s):
    m = s.manifest
    return {"dataset_id":s.identifier,"name":m["request"]["twin"].get("name","Causal volume"),
            "shape":m["shape"],"manifest_sha256":s.manifest_sha256,"input_sha256":m["input_sha256"],
            "completion_sha256":m["completion_sha256"],"maximum_error_bound":m["total_error_bound"],
            "acquisition":m["request"]["acquisition"],"model_version":m["solver"]["model_version"],
            "solver":m["solver"]}


def _plan(a,b,retained_bytes=0):
    ny,nx,nt = a.manifest["shape"]
    # Bound Python numerical-list/report copies, source provenance, row buffers,
    # differences, selected traces/maps, serialization and publication copies.
    numeric_values = 40*ny*nx + 12*nt + 4*(ny+nx)
    report_expanded = a.expanded_bytes+b.expanded_bytes + numeric_values*384 + 8*1024**2
    row_workspace = nx*nt*8*40 + 8*1024**2
    peak = retained_bytes + 3*report_expanded + row_workspace + 32*1024**2
    _budget(peak,"reduction and report publication")
    return {"source_manifest_expanded_bytes":a.expanded_bytes+b.expanded_bytes,
        "estimated_report_expanded_bytes":report_expanded,"row_workspace_bytes":row_workspace,
        "estimated_peak_bytes":peak,"retained_report_expanded_bytes":retained_bytes,
        "limit_bytes":MAX_WORKSPACE_BYTES,"definition":"Conservative owned objects/copies, not total process RSS; one checked row from each source at a time."}


def _view_base(a,b,cursor,gate,gate_maps,bounds):
    return {"coordinates":{key:value.tolist() for key,value in a.coordinates.items()},
        "extent_mm":a.manifest["estimate"]["extent_mm"],"shape":a.manifest["shape"],
        "cursor":cursor,"gate":gate,"gate_maps":gate_maps,
        "selected_bounds":{key:bounds[key][cursor["y_index"]][cursor["x_index"]] for key in BOUND_KEYS},
        "source_summaries":{"reference":_summary(a),"candidate":_summary(b)},
        "metadata":{"difference":"candidate B minus reference A", "time_axis":"Actual saved gamma recording time; not unique depth",
                    "residual_magnitude":"A displayed hypot or summary norm is an ordinary diagnostic without its own enclosure.",
                    "data_origin":"Verified frozen source arrays; no propagation, interpolation or alignment."}}


def compute_causal_comparison(root,request):
    request = request if isinstance(request,CausalComparisonRequest) else CausalComparisonRequest.model_validate(request)
    arithmetic_policy = check_arithmetic_environment()
    a = _source(root,request.reference_dataset_id)
    b = _source(root,request.candidate_dataset_id,a.expanded_bytes)
    _compatible(a,b)
    plan = _plan(a,b)
    cursor = _cursor(a,x_index=request.x_index,y_index=request.y_index,time_index=request.time_index)
    gate = _gate(a.coordinates["time_us"],request.gate_start_us,request.gate_end_us)
    ny,nx,nt = a.manifest["shape"]
    bounds = {key:np.empty((ny,nx),np.float64) for key in BOUND_KEYS}
    maps = {mode:{side:np.empty((ny,nx),np.float64) for side in ("reference","candidate","difference")}
            for mode in ("peak_envelope","rms_rf")}
    xy = {side:{key:np.empty((ny,nx),np.float64) for key in SIGNALS} for side in ("reference","candidate","difference")}
    full,gated = Metrics(),Metrics()
    lo,hi = gate["start_index"],gate["stop_index_exclusive"]
    for y in range(ny):
        av,ae = _read_row(a,y)
        bv,be = _read_row(b,y)
        d = checked_differences(av,bv)
        cb = column_bounds(av,bv,ae,be)
        for key in BOUND_KEYS:
            bounds[key][y] = cb[key]
        full.add(av,bv,d,y)
        gated.add(av,bv,d,y,time_slice=slice(lo,hi))
        gm = gate_products(av,bv,d,lo,hi)
        for mode in maps:
            for side in maps[mode]:
                maps[mode][side][y] = gm[mode][side]
        for side,values in (("reference",av),("candidate",bv),("difference",d)):
            for key in SIGNALS:
                xy[side][key][y] = values[key][:,cursor["time_index"]]
        if y == cursor["y_index"]:
            traces = {side:{key:values[key][cursor["x_index"]].tolist() for key in SIGNALS}
                      for side,values in (("reference",av),("candidate",bv),("difference",d))}
    check_arithmetic_environment()
    _unchanged(a); _unchanged(b)
    maps = {mode:{side:value.tolist() for side,value in fields.items()} for mode,fields in maps.items()}
    maps["scope"] = "Ordinary candidate-gate minus reference-gate diagnostics; not a gate of the complex residual and not newly certified."
    bounds = {key:values.tolist() for key,values in bounds.items()}
    bounds.update(definition="Full-record column enclosures for complex subtraction and separately saved magnitude subtraction; source sum plus outward-rounded subtraction allowance.",arithmetic_policy=arithmetic_policy)
    view = _view_base(a,b,cursor,gate,maps,bounds)
    view.update(traces=traces,xy={side:{key:value.tolist() for key,value in fields.items()} for side,fields in xy.items()})
    return {"schema_version":1,"kind":KIND,"processing_version":PROCESSING_VERSION,
        "request":request.model_dump(mode="json"),"shape":a.manifest["shape"],"axis_order":["y","x","time"],
        "coordinates":view["coordinates"],"extent_mm":view["extent_mm"],
        "sources":{"reference":a.manifest,"candidate":b.manifest},"source_summaries":view["source_summaries"],
        "compatibility":{"policy":request.policy,"compatible":True,"checked_fields":[*SEMANTICS,*PULSE_FIELDS,*COORDINATES,"array units","extents","exterior media"],"differences":_differences(a,b)},
        "metrics":{"full_record":full.result(a.coordinates),"gate":gated.result(a.coordinates)},
        "gate":gate,"bounds":bounds,"gate_maps":maps,"initial_view":view,"resource_estimate":plan,"warnings":WARNINGS}


def causal_comparison_view(root,report,*,x_index=None,y_index=None,time_index=None):
    if report.get("processing_version") != PROCESSING_VERSION:
        raise ValueError("This comparison version supports only its frozen initial view.")
    from .causal_comparison_store import bounded_payload
    payload = bounded_payload(report)
    retained = _workspace(payload)
    del payload
    a = _source(root,report["request"]["reference_dataset_id"],retained)
    b = _source(root,report["request"]["candidate_dataset_id"],retained+a.expanded_bytes)
    for side,s in (("reference",a),("candidate",b)):
        if s.manifest_sha256 != json_sha256(report["sources"][side]):
            raise ValueError("Comparison source no longer matches its immutable report snapshot.")
    _compatible(a,b)
    _plan(a,b,retained)
    saved = report["initial_view"]["cursor"]
    cursor = _cursor(a,**{key:saved[key] if value is None else value for key,value in
                         (("x_index",x_index),("y_index",y_index),("time_index",time_index))})
    ny,nx,nt = a.manifest["shape"]
    xy = {side:{key:np.empty((ny,nx),np.float64) for key in SIGNALS} for side in ("reference","candidate","difference")}
    check_arithmetic_environment()
    for y in range(ny):
        av,_ = _read_row(a,y); bv,_ = _read_row(b,y)
        d = checked_differences(av,bv)
        for side,values in (("reference",av),("candidate",bv),("difference",d)):
            for key in SIGNALS:
                xy[side][key][y] = values[key][:,cursor["time_index"]]
        if y == cursor["y_index"]:
            traces = {side:{key:values[key][cursor["x_index"]].tolist() for key in SIGNALS}
                      for side,values in (("reference",av),("candidate",bv),("difference",d))}
    check_arithmetic_environment()
    _unchanged(a); _unchanged(b)
    view = _view_base(a,b,cursor,report["gate"],report["gate_maps"],report["bounds"])
    view.update(traces=traces,xy={side:{key:value.tolist() for key,value in fields.items()} for side,fields in xy.items()})
    return view
