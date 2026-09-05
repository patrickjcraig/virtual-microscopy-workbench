"""Row-streamed comparisons of immutable finite coherent observations.

Only saved arrays and frozen provenance are read. The acquisition and observation
implementations keep their existing fingerprints and historical contracts.
"""
from dataclasses import dataclass
import math

import numpy as np

from .causal_datasets import typed_sha256
from .causal_comparisons import (Source as CausalSource, _compatible as causal_compatible,
    _differences as causal_differences, _gate, CausalComparisonCompatibilityError)
from .observation_datasets import ObservationStore, COORDINATES, PRODUCTS, CONTRACT
from .observation_plan import MAX_PLAN_BYTES, MAX_PLAN_EXPANDED_BYTES, measure, MODEL_VERSION
from .observation_math import ARITHMETIC_CONTRACT, operator_metadata
from .observation_comparison_schemas import ObservationComparisonRequest
from .observation_comparison_math import (SIGNALS, checked_differences, column_bounds,
    Metrics, gate_products, check_arithmetic_environment)

KIND = "sam_observation_comparison"
PROCESSING_VERSION = "observation-comparison-0.16.0"
MAX_WORKSPACE_BYTES = 512*1024**2
MAX_SAMPLES = 3_000_000
BOUND_KEYS = ("complex_source_sum", "magnitude_source_sum", "complex_arithmetic",
              "complex_total", "magnitude_arithmetic", "magnitude_total")
WARNINGS = [
    "Reference A is a selected synthetic baseline, not measured ground truth. Differences are candidate B minus reference A.",
    "Complex pressure subtraction and subtraction of separately saved magnitudes use distinct source-bound sums. Correlated source errors are allowed.",
    "Summary statistics and gate maps are ordinary diagnostics without new numerical enclosures; these are not detection or physical-resolution measurements.",
    "Offline observation verification preserves publication checksums but cannot re-establish weighted-sum conversion maxima without the original source waveforms.",
    "The operator is a fixed finite spatial filter, not a calibrated beam. Recording time is not unique depth; no resampling, registration, alignment, phase fitting or normalization occurs.",
]


class ObservationComparisonCompatibilityError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("Saved observations require identical coordinates, full support, operator and supported excitation semantics.")


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
    if type(value) is not int or value < 0 or value > MAX_WORKSPACE_BYTES:
        raise ValueError(f"Observation comparison {description} exceeds the 512 MiB owned-workspace limit.")


def _source(root, identifier, retained_bytes=0):
    # Admit simultaneous source decoding, historical plan validation and row
    # checking before the first source reader can allocate JSON or array data.
    _budget(retained_bytes + 4*MAX_PLAN_EXPANDED_BYTES + 2*MAX_PLAN_BYTES + 32*1024**2,
            "source verification")
    store = ObservationStore(root)
    manifest = store.verify_complete(identifier)
    size = measure(manifest)
    if math.prod(manifest["shape"]) > MAX_SAMPLES:
        raise ValueError("Observation comparison exceeds three million complex sample positions.")
    group = store._open_checked(identifier, manifest)
    coordinates = {name: np.asarray(group[name][:], dtype=np.float64) for name in COORDINATES}
    return Source(store, identifier, manifest, group, coordinates, size["expanded_bytes"], size["sha256"])


def _parent(source):
    m = source.manifest["estimate"]["source_manifest"]
    return CausalSource(None, m["dataset_id"], m, None,
        {key: np.asarray(m["estimate"][key], np.float64) for key in COORDINATES},
        0, source.manifest["estimate"]["source_manifest_sha256"])


def _compatible(a, b):
    issues = []
    def issue(field, left, right, message="Frozen values differ."):
        issues.append({"field":field, "reference":left, "candidate":right, "message":message})
    for side, s in (("reference", a), ("candidate", b)):
        m, plan = s.manifest, s.manifest["estimate"]
        for field, expected in (("model_version", MODEL_VERSION), ("arithmetic_contract", ARITHMETIC_CONTRACT),
                                ("row_contract", CONTRACT)):
            if m["solver"].get(field) != expected:
                issue(f"{side}.solver.{field}", m["solver"].get(field), expected, "Unsupported numerical contract.")
        if m.get("certificate_contract") != CONTRACT:
            issue(f"{side}.certificate_contract", m.get("certificate_contract"), CONTRACT)
        for field, expected in operator_metadata().items():
            if plan["operator"].get(field) != expected:
                issue(f"{side}.operator.{field}", plan["operator"].get(field), expected, "Unsupported finite operator.")
    for field in ("shape", "axis_order", "dtype"):
        if a.manifest[field] != b.manifest[field]:
            issue(field, a.manifest[field], b.manifest[field])
    for key in COORDINATES:
        av, bv = a.coordinates[key], b.coordinates[key]
        if av.shape != bv.shape or av.tobytes() != bv.tobytes():
            issue(key, typed_sha256(av), typed_sha256(bv), "Actual float64 centers must match bit-exactly.")
    for key in (*PRODUCTS, *COORDINATES):
        for field in ("axes", "dtype", "units"):
            av, bv = a.manifest["arrays"][key].get(field), b.manifest["arrays"][key].get(field)
            if av != bv:
                issue(f"arrays.{key}.{field}", av, bv)
    ap, bp = a.manifest["estimate"], b.manifest["estimate"]
    for field in ("operator", "source_shape", "source_indices", "extent_mm", "source_extent_mm",
                  "physical_neighbor_offsets", "axis_order", "dtype"):
        # Compare canonical encodings too, so signed zeros in support metadata
        # cannot disappear through ordinary Python float equality.
        if measure(ap[field])["sha256"] != measure(bp[field])["sha256"]:
            issue(field, measure(ap[field])["sha256"], measure(bp[field])["sha256"],
                  "Operator and complete retained/support geometry must match exactly.")
    try:
        # The existing strict independent-column comparison validates the full
        # surrounding coordinates, reference timing, pulse and exterior media.
        # Only its pure frozen-provenance checks are reused; no kind is relaxed.
        causal_compatible(_parent(a), _parent(b))
    except CausalComparisonCompatibilityError as exc:
        issues.extend({**entry, "field":"parent."+entry["field"]} for entry in exc.issues)
    if issues:
        raise ObservationComparisonCompatibilityError(issues)


def _differences(a, b):
    result = causal_differences(_parent(a), _parent(b))
    result["observation_tolerance"] = {"reference":a.manifest["request"]["absolute_tolerance"],
                                       "candidate":b.manifest["request"]["absolute_tolerance"]}
    result["observation_implementation_changed"] = a.manifest["solver"] != b.manifest["solver"]
    result["observation_solver_sha256"] = {"reference":measure(a.manifest["solver"])["sha256"],
                                           "candidate":measure(b.manifest["solver"])["sha256"]}
    return result


def _read_row(source, y):
    row = source.store._read_row(source.identifier, source.manifest, source.group, y)
    return {key:row[key] for key in SIGNALS}, row["complex_total"], row["magnitude_total"]


def _unchanged(source):
    if measure(source.store.manifest(source.identifier))["sha256"] != source.manifest_sha256:
        raise ValueError("An observation source manifest changed during comparison.")


def _cursor(source, **indices):
    ny, nx, nt = source.manifest["shape"]
    result = {}
    for key, n in (("x", nx), ("y", ny), ("time", nt)):
        value = indices.get(f"{key}_index")
        value = n//2 if value is None else value
        if type(value) is not int or not 0 <= value < n:
            raise ValueError(f"Comparison {key} index must be an integer in 0..{n-1}.")
        result[f"{key}_index"] = value
        coordinate = f"{key}_{'us' if key == 'time' else 'mm'}"
        result[coordinate] = float(source.coordinates[coordinate][value])
    plan = source.manifest["estimate"]
    result.update(source_x_index=plan["source_indices"]["x"][result["x_index"]],
                  source_y_index=plan["source_indices"]["y"][result["y_index"]])
    return result


def _summary(s):
    m, p = s.manifest, s.manifest["estimate"]
    return {"dataset_id":s.identifier, "name":m["request"].get("name") or p["source_summary"]["name"],
        "shape":m["shape"], "manifest_sha256":s.manifest_sha256, "input_sha256":m["input_sha256"],
        "completion_sha256":m["completion_sha256"], "maximum_complex_bound":m["maximum_complex_bound"],
        "maximum_magnitude_bound":m["maximum_magnitude_bound"],
        "acquisition":p["inherited_excitation"]["acquisition"], "model_version":m["solver"]["model_version"],
        "operator":p["operator"]["model"], "absolute_tolerance":m["request"]["absolute_tolerance"],
        "source_dataset_id":p["source_manifest"]["dataset_id"],
        "source_manifest_sha256":p["source_manifest_sha256"], "solver_sha256":measure(m["solver"])["sha256"]}


def _plan(a, b, retained_bytes=0):
    ny, nx, nt = a.manifest["shape"]
    if math.prod((ny,nx,nt)) > MAX_SAMPLES:
        raise ValueError("Observation comparison exceeds three million sample positions.")
    # Snapshots are stored once; self comparisons share the one source object.
    sources = a.expanded_bytes + (0 if a is b else b.expanded_bytes)
    numeric_values = 48*ny*nx + 16*nt + 8*(ny+nx)
    report_expanded = sources + numeric_values*384 + 8*1024**2
    row_workspace = nx*nt*8*40 + 8*1024**2
    peak = retained_bytes + 3*report_expanded + row_workspace + 32*1024**2
    _budget(peak, "reduction and publication")
    return {"source_manifest_expanded_bytes":sources, "estimated_report_expanded_bytes":report_expanded,
        "row_workspace_bytes":row_workspace, "estimated_peak_bytes":peak,
        "retained_report_expanded_bytes":retained_bytes, "limit_bytes":MAX_WORKSPACE_BYTES,
        "definition":"Conservative owned source/provenance, numerical lists, rows, serialization and response copies; not total process RSS. One row per source at a time; snapshots deduplicate by digest."}


def _view_base(a, b, cursor, gate, gate_maps, bounds):
    summaries = {"reference":_summary(a), "candidate":_summary(b)}
    return {"coordinates":{key:value.tolist() for key,value in a.coordinates.items()},
        "extent_mm":a.manifest["estimate"]["extent_mm"], "shape":a.manifest["shape"],
        "cursor":cursor, "gate":gate, "gate_maps":gate_maps,
        "selected_bounds":{key:bounds[key][cursor["y_index"]][cursor["x_index"]] for key in BOUND_KEYS},
        "source_reference":summaries["reference"], "source_candidate":summaries["candidate"],
        "source_summaries":summaries,
        "metadata":{"difference":"candidate B minus reference A", "time_axis":"Inherited actual gamma recording time; not unique depth",
            "residual_magnitude":"Saved magnitude B minus saved magnitude A differs from the magnitude of complex B minus A.",
            "data_origin":"Verified frozen observation arrays; no forward calculation, interpolation or alignment.",
            "certificate_scope":WARNINGS[3]}}


def compute_observation_comparison(root, request):
    request = request if isinstance(request, ObservationComparisonRequest) else ObservationComparisonRequest.model_validate(request)
    arithmetic_policy = check_arithmetic_environment()
    a = _source(root, request.reference_dataset_id)
    b = a if request.candidate_dataset_id == a.identifier else _source(root, request.candidate_dataset_id, a.expanded_bytes)
    _compatible(a, b)
    plan = _plan(a, b)
    cursor = _cursor(a, x_index=request.x_index, y_index=request.y_index, time_index=request.time_index)
    gate = _gate(a.coordinates["time_us"], request.gate_start_us, request.gate_end_us)
    ny, nx, nt = a.manifest["shape"]
    bounds = {key:np.empty((ny,nx), np.float64) for key in BOUND_KEYS}
    maps = {mode:{side:np.empty((ny,nx), np.float64) for side in ("reference","candidate","difference")}
            for mode in ("peak_envelope","rms_rf")}
    xy = {side:{key:np.empty((ny,nx),np.float64) for key in SIGNALS} for side in ("reference","candidate","difference")}
    full, gated = Metrics(), Metrics()
    lo, hi = gate["start_index"], gate["stop_index_exclusive"]
    for y in range(ny):
        av, ac, am = _read_row(a, y)
        bv, bc, bm = (av, ac, am) if a is b else _read_row(b, y)
        d = checked_differences(av, bv)
        cb = column_bounds(av, bv, ac, bc, am, bm)
        for key in BOUND_KEYS:
            bounds[key][y] = cb[key]
        full.add(av, bv, d, y)
        gated.add(av, bv, d, y, time_slice=slice(lo,hi))
        gm = gate_products(av, bv, d, lo, hi)
        for mode in maps:
            for side in maps[mode]:
                maps[mode][side][y] = gm[mode][side]
        for side, values in (("reference",av),("candidate",bv),("difference",d)):
            for key in SIGNALS:
                xy[side][key][y] = values[key][:,cursor["time_index"]]
        if y == cursor["y_index"]:
            traces = {side:{key:values[key][cursor["x_index"]].tolist() for key in SIGNALS}
                for side,values in (("reference",av),("candidate",bv),("difference",d))}
    check_arithmetic_environment()
    _unchanged(a)
    if a is not b:
        _unchanged(b)
    maps = {mode:{side:value.tolist() for side,value in fields.items()} for mode,fields in maps.items()}
    maps["scope"] = "Ordinary candidate-gate minus reference-gate diagnostics, not a gate of the complex residual and not newly certified."
    bounds = {key:values.tolist() for key,values in bounds.items()}
    bounds.update(definition="Full-record complex and saved-magnitude enclosures with distinct source sums plus outward-rounded subtraction allowances.",
                  arithmetic_policy=arithmetic_policy)
    view = _view_base(a,b,cursor,gate,maps,bounds)
    view.update(traces=traces, xy={side:{key:value.tolist() for key,value in fields.items()} for side,fields in xy.items()})
    return {"schema_version":1, "kind":KIND, "processing_version":PROCESSING_VERSION,
        "request":request.model_dump(mode="json"), "shape":a.manifest["shape"], "axis_order":["y","x","time"],
        "coordinates":view["coordinates"], "extent_mm":view["extent_mm"],
        "source_snapshots":{s.manifest_sha256:s.manifest for s in (a,b)},
        "source_reference":view["source_reference"], "source_candidate":view["source_candidate"],
        "source_summaries":view["source_summaries"],
        "compatibility":{"policy":request.policy, "compatible":True,
            "checked_fields":[*COORDINATES,"full support coordinates","retained and source extents","source indices",
                "physical neighbor offsets","operator weights/order/phase","array axes/dtypes/units",
                "numerical contracts","nested excitation/time zero/normalization","exterior media"],
            "differences":_differences(a,b)},
        "metrics":{"full_record":full.result(a.coordinates),"gate":gated.result(a.coordinates)},
        "gate":gate, "bounds":bounds, "gate_maps":maps, "initial_view":view, "resource_estimate":plan, "warnings":WARNINGS}


def observation_comparison_view(root, report, *, x_index=None, y_index=None, time_index=None):
    if report.get("processing_version") != PROCESSING_VERSION:
        raise ValueError("This comparison version supports only its frozen initial view.")
    from .observation_comparison_store import json_measure
    retained = json_measure(report)["expanded_bytes"]
    a = _source(root, report["request"]["reference_dataset_id"], retained)
    b = a if report["request"]["candidate_dataset_id"] == a.identifier else _source(
        root, report["request"]["candidate_dataset_id"], retained+a.expanded_bytes)
    for role, s in (("reference",a),("candidate",b)):
        if s.manifest_sha256 != report[f"source_{role}"]["manifest_sha256"]:
            raise ValueError("Comparison source no longer matches its immutable report snapshot.")
    _compatible(a,b)
    _plan(a,b,retained)
    saved = report["initial_view"]["cursor"]
    cursor = _cursor(a, **{key:saved[key] if value is None else value for key,value in
        (("x_index",x_index),("y_index",y_index),("time_index",time_index))})
    ny,nx,nt = a.manifest["shape"]
    xy = {side:{key:np.empty((ny,nx),np.float64) for key in SIGNALS} for side in ("reference","candidate","difference")}
    check_arithmetic_environment()
    for y in range(ny):
        av,_,_ = _read_row(a,y)
        bv = av if a is b else _read_row(b,y)[0]
        d = checked_differences(av,bv)
        for side,values in (("reference",av),("candidate",bv),("difference",d)):
            for key in SIGNALS:
                xy[side][key][y] = values[key][:,cursor["time_index"]]
        if y == cursor["y_index"]:
            traces = {side:{key:values[key][cursor["x_index"]].tolist() for key in SIGNALS}
                for side,values in (("reference",av),("candidate",bv),("difference",d))}
    check_arithmetic_environment()
    _unchanged(a)
    if a is not b:
        _unchanged(b)
    view = _view_base(a,b,cursor,report["gate"],report["gate_maps"],report["bounds"])
    view.update(traces=traces,xy={side:{key:value.tolist() for key,value in fields.items()} for side,fields in xy.items()})
    return view
