"""Frozen, bounded plans for a finite coherent filter of saved causal columns."""
from __future__ import annotations

from collections import OrderedDict
from fractions import Fraction
import math
from pathlib import Path
import threading

import numpy as np

from .causal_comparison_store import bounded_json_read, json_measure
from .causal_datasets import CausalSamDatasetStore, typed_sha256, json_sha256
from .datasets import checked_id
from .observation_schemas import ObservationRequest
from .observation_math import (OPERATOR_MODEL, ARITHMETIC_CONTRACT, WEIGHT_NUMERATORS,
    WEIGHT_DENOMINATOR, OFFSETS_YX, TIME_BLOCK, operator_metadata)

KIND = "sam_coherent_observation_volume"
MODEL_VERSION = "coherent-observation-0.15.0"
OPERATOR = OPERATOR_MODEL
MAX_BYTES = 512*1024**2
MAX_PLAN_BYTES = 16*1024**2
MAX_PLAN_EXPANDED_BYTES = 64*1024**2
MAX_OUTPUT_SAMPLES = 3_000_000
MAX_PLAN_CACHE_ENTRIES = 32
_plan_cache = OrderedDict()
_plan_cache_lock = threading.Lock()
WEIGHTS = list(WEIGHT_NUMERATORS)
OFFSETS = [list(offset) for offset in OFFSETS_YX]
SOURCE_SEMANTICS = {
    "path_model": "continuous_columns_v1", "path_contract_version": "ordered-column-paths-1",
    "response_model": "layered_causal_gamma_v1", "excitation_model": "causal_gamma_peak_phase_v1",
    "observation_model": "independent_columns_v1", "focus_model": "none",
    "certificate_version": "causal-column-certificate-1", "depth_mapping_supported": False,
    "rf_unit": "relative signed pressure", "imaginary_unit": "relative quadrature pressure",
    "envelope_unit": "relative complex-pressure magnitude", "error_bound_unit": "absolute relative pressure",
    "coordinate_units": {"x": "mm", "y": "mm", "time": "us"},
    "time_zero": "causal gamma onset at the transducer reference; lossless incident-medium standoff adds round-trip delay, and gamma peak is later than onset",
    "envelope_processing": "magnitude of full coherent complex gamma response; independently certified returned double, not a Hilbert transform of real RF",
    "material_model": "frozen nominal positive scalar impedances/speeds; every finite layer lossless; water exteriors and lossless standoff",
}


def measure(value):
    return json_measure(value, MAX_PLAN_BYTES, MAX_PLAN_EXPANDED_BYTES)


def guarded_root(root):
    root = Path(root).absolute()
    for path in (root, *root.parents):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("Observation roots cannot follow symbolic links or junctions.")
    return root.resolve()


def read_source(root, identifier, *, verify=True):
    root = guarded_root(root)
    path = root/checked_id(identifier)
    if path.is_symlink() or path.is_junction():
        raise ValueError("Observation sources cannot follow links or junctions.")
    if not path.is_dir():
        raise KeyError(identifier)
    # Strong finite/depth/duplicate/expansion admission precedes the original
    # historical reader and any Zarr metadata/array decode.
    manifest, expanded = bounded_json_read(path/"manifest.json")
    if (not isinstance(manifest, dict) or manifest.get("dataset_id") != identifier or
            manifest.get("kind") != "sam_causal_rf_volume" or
            manifest.get("complete") is not True or manifest.get("state") != "completed"):
        raise ValueError("Observation requires a completed independent causal source volume.")
    if 4*expanded+64*1024**2 > MAX_BYTES:
        raise ValueError("Observation source verification exceeds its owned workspace.")
    # Reject unsupported output work and coordinates before decoding any source
    # signal chunk. This is admission only; the original store remains authority.
    try:
        _facts(manifest)
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError("Observation source has an invalid frozen coordinate plan.") from exc
    store = CausalSamDatasetStore(root)
    if verify:
        checked = store.verify_complete(identifier)
        if measure(checked)["sha256"] != measure(manifest)["sha256"]:
            raise ValueError("Observation source manifest changed during verification.")
    return manifest, path


def _facts(source):
    if any(source.get("metadata", {}).get(key) != value for key, value in SOURCE_SEMANTICS.items()):
        raise ValueError("Observation source has unsupported frozen excitation/observation semantics.")
    if source.get("certificate_contract") != "causal-column-certificate-1":
        raise ValueError("Observation source certificate contract is unsupported.")
    shape = source.get("shape")
    if (type(shape) is not list or len(shape) != 3 or any(type(v) is not int for v in shape) or
            not 16 <= shape[0] <= 64 or not 16 <= shape[1] <= 64 or not 2 <= shape[2] <= 2049):
        raise ValueError("Observation source exceeds supported shape limits.")
    ny, nx, nt = shape
    if (ny-2)*(nx-2)*nt > MAX_OUTPUT_SAMPLES:
        raise ValueError("Observation exceeds three million output complex samples.")
    acquisition = source["request"]["acquisition"]
    if (source["estimate"].get("model_version") != "sam-causal-columns-0.13.0" or
            acquisition.get("scan_nx") != nx or acquisition.get("scan_ny") != ny):
        raise ValueError("Observation requires the supported frozen v0.13 coordinate generator.")
    roi = acquisition.get("roi_mm") or [0., 0., *source["request"]["twin"]["size_mm"][:2]]
    if (type(roi) is not list or len(roi) != 4 or
            any(type(v) not in (int, float) or not math.isfinite(v) for v in roi) or
            not roi[0] < roi[2] or not roi[1] < roi[3]):
        raise ValueError("Observation source ROI is invalid.")
    dx, dy = (roi[2]-roi[0])/nx, (roi[3]-roi[1])/ny
    start, duration, rate = (acquisition.get(k) for k in
                            ("record_start_us", "record_duration_us", "sample_rate_mhz"))
    if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, duration, rate)) or
            start < 0 or duration <= 0 or rate <= 0 or
            not math.isfinite(duration*rate) or math.floor(duration*rate+1e-9)+1 != nt):
        raise ValueError("Observation source recording coordinates are invalid.")
    # Preserve the exact operation order of the saved v0.13 generator. Decimal
    # origins legitimately give unequal adjacent float64 differences; an
    # allclose/uniform-step test would admit changed centers and reject none.
    generated = {
        "x_mm": roi[0]+(np.arange(nx, dtype=np.float64)+.5)*dx,
        "y_mm": roi[1]+(np.arange(ny, dtype=np.float64)+.5)*dy,
        "time_us": np.asarray([start+i/rate for i in range(nt)], np.float64),
    }
    if source["estimate"].get("extent_mm") != [roi[0], roi[2], roi[1], roi[3]]:
        raise ValueError("Observation source extent differs from its frozen ROI.")
    coordinates = {}
    for name, size in (("x_mm", nx), ("y_mm", ny), ("time_us", nt)):
        raw_values = source["estimate"].get(name)
        if type(raw_values) is not list or len(raw_values) != size:
            raise ValueError(f"Observation requires exact saved {name} coordinates.")
        values = np.asarray(raw_values, dtype=np.float64)
        if (values.shape != (size,) or not np.isfinite(values).all() or np.any(np.diff(values) <= 0) or
                typed_sha256(values) != source["coordinates_sha256"].get(name)):
            raise ValueError(f"Observation requires exact saved {name} coordinates.")
        if values.tobytes() != generated[name].tobytes():
            raise ValueError(f"Observation {name} differs from the exact frozen coordinate generator.")
        coordinates[name] = values.copy() if name == "time_us" else values[1:-1].copy()
    class_map = np.asarray(source["estimate"].get("class_index"))
    if class_map.shape != (ny, nx):
        raise ValueError("Observation source class map shape mismatch.")
    try:
        bounds = np.asarray([[source["class_certificates"][str(int(i))]["diagnostics"]["total_error_bound"]
                              for i in row] for row in class_map], dtype=np.float64)
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("Observation source has incomplete class certificates.") from exc
    if not np.isfinite(bounds).all() or np.any(bounds < 0):
        raise ValueError("Observation source bounds must be finite and nonnegative.")
    sx, sy = (np.asarray(source["estimate"][name], np.float64) for name in ("x_mm", "y_mm"))
    # Each new outer cell wall is the exact midpoint of the discarded border
    # center and its retained neighbor, rounded to float64 only once.
    extent = [float((Fraction(float(a[i]))+Fraction(float(a[j])))/2)
              for a, i, j in ((sx, 0, 1), (sx, -2, -1), (sy, 0, 1), (sy, -2, -1))]
    physical_offsets = {"x_mm": [[float(sx[xi+dx]-sx[xi]) for dx in (-1, 0, 1)] for xi in range(1, nx-1)],
                        "y_mm": [[float(sy[yi+dy]-sy[yi]) for dy in (-1, 0, 1)] for yi in range(1, ny-1)]}
    return [ny-2, nx-2, nt], coordinates, bounds, extent, physical_offsets


def _upward(value):
    f = float(value)
    if Fraction(f) < value:
        f = math.nextafter(f, math.inf)
    return f


def _base(request, source):
    shape, coords, bounds, extent, physical = _facts(source)
    propagated = []
    for y in range(shape[0]):
        row = []
        for x in range(shape[1]):
            exact = sum((Fraction(float(bounds[y+dy+1, x+dx+1]))*w for (dy, dx), w in zip(OFFSETS, WEIGHTS)), Fraction())/WEIGHT_DENOMINATOR
            row.append(_upward(exact))
        propagated.append(row)
    if np.max(propagated) > request["absolute_tolerance"]:
        raise ValueError("Observation tolerance is below the propagated source bound; no output can be admitted.")
    return {"schema_version": 1, "kind": KIND, "model_version": MODEL_VERSION,
        "request": request, "source_manifest": source, "source_manifest_sha256": measure(source)["sha256"],
        "source_summary": {"dataset_id": source["dataset_id"], "name": source["request"]["twin"].get("name", "Causal source"),
                           "acquisition": source["request"]["acquisition"]},
        "shape": shape, "source_shape": list(source["shape"]), "axis_order": ["y", "x", "time"], "dtype": "float64", "tile_rows": 1, "total_rows": shape[0],
        **{key: values.tolist() for key, values in coords.items()},
        "coordinates_sha256": {key: typed_sha256(values) for key, values in coords.items()},
        "source_indices": {"y": list(range(1, shape[0]+1)), "x": list(range(1, shape[1]+1)), "time": list(range(shape[2]))},
        "extent_mm": extent, "source_extent_mm": source["estimate"]["extent_mm"], "physical_neighbor_offsets": physical,
        "operator": {**operator_metadata(), "model": OPERATOR,
                     "weight_float64_sha256": typed_sha256(np.asarray(WEIGHTS, np.float64)/WEIGHT_DENOMINATOR),
                     "weights": [w/WEIGHT_DENOMINATOR for w in WEIGHTS]},
        "source_bound_map": bounds.tolist(), "minimum_source_propagation": propagated,
        "inherited_excitation": {"acquisition": source["request"]["acquisition"],
            "time_zero": source["metadata"]["time_zero"], "excitation_model": source["metadata"]["excitation_model"]},
        "temporal_block_samples": TIME_BLOCK, "output_complex_samples": math.prod(shape), "weighted_component_terms": math.prod(shape)*18,
        "estimated_temporary_bytes": 0,
        "evidence_status": "Finite coherent spatial filter of synthetic saved columns; not calibrated focused SAM or unique reflection depth.",
        "certificate_scope": "Exact represented weighted sums plus frozen source bounds; magnitude recomputed after coherent mixing. Offline checks cannot re-establish component conversion maxima without the source signals."}


def _resources(base):
    # All source provenance is stored once, inside this plan. Reserve complete
    # manifest overhead and row registries rather than copying the source again.
    size = measure(base)
    numeric = math.prod(base["shape"])*24+base["shape"][0]*base["shape"][1]*40
    coordinate = sum(len(base[key])*8 for key in ("x_mm", "y_mm", "time_us"))
    result = {"total_bytes": numeric+coordinate+size["encoded_bytes"]+2*1024**2,
              "estimated_manifest_bytes": size["encoded_bytes"]+2*1024**2,
              "estimated_manifest_expanded_bytes": size["expanded_bytes"]+4*1024**2}
    source_expanded = json_measure(base["source_manifest"], 8*1024**2, 32*1024**2)["expanded_bytes"]
    result["estimated_peak_bytes"] = 4*source_expanded+3*result["estimated_manifest_expanded_bytes"]+96*1024**2
    result["workspace_definition"] = "Owned source verification/provenance copies, three input rows, one output row, bounded temporal arithmetic and serialization; not process RSS."
    if result["total_bytes"] > MAX_BYTES or result["estimated_peak_bytes"] > MAX_BYTES:
        raise ValueError("Observation output or owned workspace exceeds 512 MiB.")
    result["resources"] = {**result, "output_complex_samples": base["output_complex_samples"],
        "weighted_component_terms": base["weighted_component_terms"], "temporal_block_samples": TIME_BLOCK,
        "estimated_temporary_bytes": 0}
    return result


def plan_observation(root, request):
    normalized = ObservationRequest.model_validate(request).model_dump(mode="json")
    source, _ = read_source(root, normalized["source_dataset_id"], verify=True)
    result = _base(normalized, source)
    result.update(_resources(result))
    measure(result)
    return result


def _source_contract(source):
    """Validate frozen source identities/certificates without a path or solver."""
    json_measure(source, 8*1024**2, 32*1024**2)
    # These authority methods are pure manifest checks. Bypass the filesystem
    # constructor deliberately: completed exports need no original source root.
    authority = object.__new__(CausalSamDatasetStore)
    index, _ = authority._frozen(source)
    authority._certificates(source)
    if (source["arrays_initialized"] is not True or source["metadata"] != source["estimate"]["metadata"] or
            json_sha256(source["metadata"]) != source["metadata_sha256"] or
            json_sha256(authority._initialization_payload(source)) != source["initialization_sha256"] or
            json_sha256(authority._completion_payload(source)) != source["completion_sha256"] or
            set(source["completed_chunks"]) != {str(y) for y in range(source["shape"][0])} or
            source["completed_rows"] != source["shape"][0] or
            set(source["class_certificates"]) != {str(i) for i in range(len(source["estimate"]["stack_table"]))}):
        raise ValueError("Frozen observation source completion/certificate identity mismatch.")
    if source["total_error_bound"] != max(c["diagnostics"]["total_error_bound"] for c in source["class_certificates"].values()):
        raise ValueError("Frozen observation source maximum bound is inconsistent.")
    for y, row in enumerate(index):
        record = source["completed_chunks"][str(y)]
        expected = {str(int(i)): source["class_certificates"][str(int(i))]["certificate_sha256"] for i in row}
        if record.get("rows") != [y, y+1] or record.get("certificate_sha256") != expected:
            raise ValueError("Frozen observation source row certificate registry is inconsistent.")


def validate_plan(estimate):
    measured = measure(estimate)
    if type(estimate) is not dict:
        raise ValueError("Invalid frozen observation plan.")
    cache_key = (measured["sha256"], MODEL_VERSION, MAX_BYTES, MAX_OUTPUT_SAMPLES,
                 MAX_PLAN_BYTES, MAX_PLAN_EXPANDED_BYTES)
    with _plan_cache_lock:
        if cache_key in _plan_cache:
            _plan_cache.move_to_end(cache_key)
            return estimate
    try:
        source = estimate["source_manifest"]
        normalized = ObservationRequest.model_validate(estimate["request"]).model_dump(mode="json")
        if source["dataset_id"] != normalized["source_dataset_id"] or source.get("complete") is not True or source.get("state") != "completed":
            raise ValueError("Frozen observation source identity or completion mismatch.")
        if source.get("kind") != "sam_causal_rf_volume":
            raise ValueError("Derived-on-derived observation is unsupported.")
        _source_contract(source)
        expected = _base(normalized, source)
        for key, value in expected.items():
            if key not in estimate or measure(estimate[key])["sha256"] != measure(value)["sha256"]:
                raise ValueError(f"Frozen observation plan mismatch: {key}.")
        resources = _resources(expected)
        for key, value in resources.items():
            if type(estimate.get(key)) is not type(value) or estimate[key] != value:
                raise ValueError(f"Observation resource estimate is not canonical: {key}.")
        if set(estimate) != set(expected) | set(resources):
            raise ValueError("Observation plan contains unknown unfunded fields.")
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError("Invalid frozen observation plan.") from exc
    with _plan_cache_lock:
        _plan_cache[cache_key] = True
        while len(_plan_cache) > MAX_PLAN_CACHE_ENTRIES:
            _plan_cache.popitem(last=False)
    return estimate


def source_context(estimate, root, *, verify=True):
    validate_plan(estimate)
    current, path = read_source(root, estimate["request"]["source_dataset_id"], verify=verify)
    if measure(current)["sha256"] != estimate["source_manifest_sha256"]:
        raise ValueError("Observation source changed; partial output cannot be resumed.")
    return current, path
