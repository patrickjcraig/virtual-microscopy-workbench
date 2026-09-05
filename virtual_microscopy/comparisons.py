"""Bounded float64 comparisons of immutable, verified saved SAM measurements.

This reader never constructs a twin or runs a forward solver. A reference is a
chosen baseline, not truth. Saved comparison reports need no source directory;
new synchronized signal views require both original, unchanged sources.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
from uuid import uuid4

import numpy as np
import zarr

from .comparison_schemas import SamComparisonRequest
from .datasets import (DatasetStore, canonical_json, checked_id, check_disk_space,
                       array_sha256, coordinate_sha256, json_sha256, now_iso, validate_dataset_paths)

PROCESSING_VERSION = "sam-comparison-0.9.0"
MAX_SOURCE_BYTES = 512 * 1024**2
MAX_WORKSPACE_BYTES = 512 * 1024**2
MAX_CHUNK_BYTES = 64 * 1024**2
MAX_REPORT_BYTES = 64 * 1024**2
MAX_SOURCE_MANIFEST_BYTES = 2 * 1024**2
MAX_SOURCE_EXPANDED_BYTES = 64 * 1024**2
MAX_REPORT_EXPANDED_BYTES = 192 * 1024**2
GATE_TOLERANCE_US = 1e-9
AXES = {"x_mm": "x", "y_mm": "y", "time_us": "time"}
FORMULAS = {
    "difference": "candidate B minus reference A; no resampling, alignment, scaling or amplitude fitting",
    "bias": "sum(B-A)/N",
    "mae": "sum(abs(B-A))/N",
    "rmse": "sqrt(sum((B-A)^2)/N)",
    "relative_l2": "sqrt(sum((B-A)^2)/sum(A^2)); null when every reference sample is zero",
    "peak_envelope": "maximum of the independently saved analytic envelope over the shared inclusive gate",
    "rms_rf": "sqrt(mean(saved signed RF^2)) over the shared inclusive gate",
    "max_location": "first maximum absolute difference in Y, X, time order; signed difference is B-A",
    "accumulation": "streamed float64 reductions of authoritative float32 samples",
}


class ComparisonCompatibilityError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("Saved SAM sources are incompatible; exact axes, units and time-reference meaning are required.")


@dataclass
class Source:
    path: Path
    manifest: dict
    group: object
    coordinates: dict
    chunk_bytes: int
    manifest_workspace_bytes: int
    verification_peak_bytes: int


def _json_workspace(payload):
    # Deliberately count punctuation even inside strings: overcounting is safe.
    # Eight bytes per encoded byte covers scalar/string storage; 256 bytes per
    # container, separator or mapping entry covers Python container expansion.
    # This rejects tiny JSON files containing huge numbers of empty containers.
    return len(payload)*8 + 256*sum(payload.count(token) for token in (b"{", b"[", b",", b":"))


def _bounded_json(path, byte_limit, expanded_limit, description):
    if path.stat().st_size > byte_limit:
        raise ValueError(f"{description} exceeds its bounded JSON byte limit.")
    with path.open("rb") as stream:
        payload = stream.read(byte_limit+1)
    if len(payload) > byte_limit:
        raise ValueError(f"{description} exceeds its bounded JSON byte limit.")
    workspace = _json_workspace(payload)
    if workspace > expanded_limit:
        raise ValueError(f"{description} exceeds its bounded expanded-provenance workspace.")
    try:
        value = json.loads(payload, parse_constant=lambda item: (_ for _ in ()).throw(ValueError("Nonfinite report JSON.")))
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise ValueError(f"{description} is not supported finite, bounded JSON.") from exc
    return value, workspace


def _source(root, identifier, retained_manifest_bytes=0):
    store = DatasetStore(root)
    path = store.path(identifier)
    if not path.is_dir():
        raise KeyError(identifier)
    validate_dataset_paths(path, identifier, include_arrays=False)
    manifest, provenance_bytes = _bounded_json(path / "manifest.json", MAX_SOURCE_MANIFEST_BYTES,
        MAX_SOURCE_EXPANDED_BYTES, "Comparison source provenance")
    if manifest.get("dataset_id") != identifier or manifest.get("kind", "sam_rf_volume") != "sam_rf_volume":
        raise ValueError("Comparison sources must be saved SAM RF volumes with matching dataset identities.")
    if not manifest.get("complete") or manifest.get("state") != "completed":
        raise ValueError("Comparison sources must be completed SAM datasets.")
    validate_dataset_paths(path, identifier)
    shape = manifest.get("shape")
    if (not isinstance(shape, list) or len(shape) != 3 or
            any(type(n) is not int for n in shape) or
            not (16 <= shape[0] <= 256 and 16 <= shape[1] <= 256 and 2 <= shape[2] <= 16384)):
        raise ValueError("Comparison source shape exceeds supported saved SAM dimensions.")
    ny, nx, nt = shape
    if ny*nx*nt*8 > MAX_SOURCE_BYTES:
        raise ValueError("Comparison source RF and envelope exceed the 512 MiB saved-volume bound.")
    tile = manifest.get("tile_rows")
    if type(tile) is not int or not 1 <= tile <= ny or tile*nx*nt*4 > MAX_CHUNK_BYTES:
        raise ValueError("Comparison source row chunk exceeds the 64 MiB bounded-read limit.")
    verification_peak = retained_manifest_bytes+2*provenance_bytes+3*tile*nx*nt*4+16*1024**2
    if verification_peak > MAX_WORKSPACE_BYTES:
        raise ValueError("Comparison source verification exceeds the 512 MiB bounded workspace.")
    if manifest.get("axis_order") != ["y", "x", "time"]:
        raise ValueError("Comparison source axis order must be Y, X, time.")
    group = zarr.open_group(str(path / "data.zarr"), mode="r")
    chunk_bytes = 0
    for name in ("rf", "envelope"):
        if name not in group or group[name].shape != tuple(shape) or group[name].dtype != np.dtype("float32"):
            raise ValueError(f"Invalid comparison source {name} shape or dtype.")
        array = group[name]
        if array.chunks != (min(ny, tile), nx, nt):
            raise ValueError("Comparison requires the saved canonical full-X/time row chunk layout.")
        chunk_bytes = max(chunk_bytes, int(np.prod(array.chunks))*4)
        descriptor = manifest.get("arrays", {}).get(name, {})
        if (descriptor.get("path") != name or descriptor.get("axes") != ["y", "x", "time"] or
                descriptor.get("shape") != shape or descriptor.get("dtype") != "float32" or
                not isinstance(descriptor.get("units"), str) or not descriptor["units"].strip()):
            raise ValueError(f"Missing or invalid frozen {name} array registry/units.")
    coordinates = {}
    for name, length in (("x_mm", nx), ("y_mm", ny), ("time_us", nt)):
        if (name not in group or group[name].shape != (length,) or group[name].dtype != np.dtype("float64") or
                group[name].chunks != (length,)):
            raise ValueError(f"Invalid comparison source {name} coordinate vector or canonical chunk layout.")
        values = np.asarray(group[name][:], dtype=np.float64)
        if coordinate_sha256(values) != manifest.get("coordinates_sha256", {}).get(name):
            raise ValueError(f"Coordinate checksum mismatch: {name}.")
        if not np.isfinite(values).all() or not np.all(np.diff(values) > 0):
            raise ValueError(f"Comparison source {name} must be finite and strictly increasing.")
        if np.any(values < 0) or np.any(values > (12+GATE_TOLERANCE_US if name == "time_us" else 100)):
            raise ValueError(f"Comparison source {name} exceeds the supported coordinate range.")
        if name != "time_us" and not np.allclose(np.diff(values), values[1]-values[0], rtol=0, atol=1e-10):
            raise ValueError(f"Comparison map inspection requires uniform source {name} sampling.")
        descriptor = manifest.get("arrays", {}).get(name, {})
        expected_unit = "us" if name == "time_us" else "mm"
        if (descriptor.get("path") != name or descriptor.get("axes") != [AXES[name]] or
                descriptor.get("dtype") != "float64" or descriptor.get("units") != expected_unit):
            raise ValueError(f"Invalid comparison coordinate registry or units: {name}.")
        coordinates[name] = values
    time_zero = manifest.get("metadata", {}).get("time_zero")
    if not isinstance(time_zero, str) or not time_zero.strip():
        raise ValueError("Source lacks a frozen time-reference definition; it cannot be silently inferred.")
    # Historical read-only verification checks original bytes and identities,
    # never current solver hashes, schema defaults or geometry constructors.
    verified = store.verify_complete(identifier)
    if json_sha256(verified) != json_sha256(manifest):
        raise ValueError("Source manifest changed during comparison verification.")
    return Source(path, manifest, group, coordinates, chunk_bytes, provenance_bytes, verification_peak)


def _compatible(reference, candidate):
    issues = []
    if reference.manifest["shape"] != candidate.manifest["shape"]:
        issues.append({"field": "shape", "reason": "Shape mismatch", "reference_shape": reference.manifest["shape"],
                       "candidate_shape": candidate.manifest["shape"]})
    for name in AXES:
        a, b = reference.coordinates[name], candidate.coordinates[name]
        if a.shape != b.shape:
            issues.append({"field": name, "reason": "Coordinate length mismatch", "reference_shape": list(a.shape), "candidate_shape": list(b.shape)})
        elif not np.array_equal(a, b):
            index = int(np.flatnonzero(a != b)[0])
            issues.append({"field": name, "reason": "Coordinates must match exactly; even one ULP is incompatible",
                "max_absolute_difference": float(np.max(np.abs(b-a))), "first_different_index": index,
                "reference_value": float(a[index]), "candidate_value": float(b[index])})
    for name in ("rf", "envelope"):
        a, b = (source.manifest["arrays"][name]["units"] for source in (reference, candidate))
        if a != b:
            issues.append({"field": f"{name}.units", "reason": "Signal unit mismatch", "reference": a, "candidate": b})
        key = f"{name}_unit"
        a, b = (source.manifest.get("metadata", {}).get(key) for source in (reference, candidate))
        if a != b:
            issues.append({"field": f"metadata.{key}", "reason": "Signal metadata unit mismatch", "reference": a, "candidate": b})
    a, b = (source.manifest["metadata"]["time_zero"] for source in (reference, candidate))
    if a != b:
        issues.append({"field": "time_zero", "reason": "Time-reference meaning mismatch", "reference": a, "candidate": b})
    if issues:
        raise ComparisonCompatibilityError(issues)


def _gate(time, start, end):
    if not np.isfinite([start, end]).all() or end <= start:
        raise ValueError("Comparison gate end must exceed its finite start.")
    if start < time[0]-GATE_TOLERANCE_US or end > time[-1]+GATE_TOLERANCE_US:
        raise ValueError("Comparison gate bounds must lie within the saved RF sample time range.")
    lo = int(np.searchsorted(time, start-GATE_TOLERANCE_US, side="left"))
    hi = int(np.searchsorted(time, end+GATE_TOLERANCE_US, side="right"))
    if hi <= lo:
        raise ValueError("The comparison gate contains no recorded samples.")
    return {"requested_start_us": start, "requested_end_us": end,
            "start_us": float(time[lo]), "end_us": float(time[hi-1]), "sample_count": hi-lo,
            "start_index": lo, "stop_index_exclusive": hi, "tolerance_us": GATE_TOLERANCE_US,
            "selection": "inclusive saved samples using the existing SAM gate tolerance; no temporal interpolation"}


def _indices(shape, x_index=None, y_index=None, time_index=None):
    ny, nx, nt = shape
    xi, yi, ti = nx//2 if x_index is None else x_index, ny//2 if y_index is None else y_index, nt//2 if time_index is None else time_index
    if any(type(v) is not int for v in (xi, yi, ti)) or not (0 <= xi < nx and 0 <= yi < ny and 0 <= ti < nt):
        raise ValueError(f"Comparison cursor indices must lie inside x:0..{nx-1}, y:0..{ny-1}, time:0..{nt-1}.")
    return xi, yi, ti


def _locator(index, coords, difference):
    yi, xi = index[:2]
    result = {"y_index": yi, "x_index": xi, "y_mm": float(coords["y_mm"][yi]),
              "x_mm": float(coords["x_mm"][xi]), "signed_difference": float(difference)}
    if len(index) == 3:
        result.update(time_index=index[2], time_us=float(coords["time_us"][index[2]]))
    return result


class _Metrics:
    def __init__(self):
        self.count = 0
        self.bias_sum = self.abs_sum = self.error2 = self.reference2 = 0.
        self.maximum = -1.
        self.index = None
        self.signed_maximum = 0.

    def add(self, a, b, row_start=0):
        if not np.isfinite(a).all() or not np.isfinite(b).all():
            raise ValueError("Saved comparison signals contain nonfinite values.")
        d = b-a
        self.count += d.size
        self.bias_sum += float(np.sum(d, dtype=np.float64))
        absolute = np.abs(d)
        self.abs_sum += float(np.sum(absolute, dtype=np.float64))
        self.error2 += float(np.sum(np.square(d), dtype=np.float64))
        self.reference2 += float(np.sum(np.square(a), dtype=np.float64))
        flat = int(np.argmax(absolute))
        value = float(absolute.flat[flat])
        if value > self.maximum:
            index = list(np.unravel_index(flat, d.shape))
            index[0] += row_start
            self.index = [int(v) for v in index]
            self.maximum, self.signed_maximum = value, float(d.flat[flat])

    def result(self, coords):
        zero = self.reference2 == 0
        return {"sample_count": self.count, "bias": self.bias_sum/self.count,
            "mae": self.abs_sum/self.count, "rmse": float(np.sqrt(self.error2/self.count)),
            "max_absolute_difference": self.maximum,
            "max_location": _locator(self.index, coords, self.signed_maximum),
            "relative_l2": None if zero else float(np.sqrt(self.error2/self.reference2)),
            "relative_l2_reason": "Reference L2 norm is zero; relative error is undefined." if zero else None,
            "reference_l2": float(np.sqrt(self.reference2))}


def _plan(a, b, retained_report_bytes=0):
    ny, nx, nt = a.manifest["shape"]
    # Two decompressed canonical chunks, float64 sources/differences/reduction
    # scratch, six small 2D maps and a reserve for reports/coordinates/metadata.
    provenance = 2*(a.manifest_workspace_bytes+b.manifest_workspace_bytes)
    base = a.chunk_bytes+b.chunk_bytes+ny*nx*8*12+provenance+retained_report_bytes+16*1024**2
    rows = max(1, min(8, ny, (64*1024**2)//(nx*nt*64)))
    peak = max(base+rows*nx*nt*64, a.verification_peak_bytes, b.verification_peak_bytes)
    if peak > MAX_WORKSPACE_BYTES:
        raise ValueError("Comparison exceeds the 512 MiB bounded numerical workspace.")
    return {"row_block_size": rows, "estimated_peak_bytes": peak,
            "estimated_provenance_workspace_bytes": provenance,
            "workspace_definition": "Two source chunks, bounded float64 row reductions, maps, two conservative expanded copies of each source manifest and a 16 MiB reserve; source JSON structure is bounded before parsing. Not process RSS."}


def _extent(coords):
    result = []
    for key in ("x_mm", "y_mm"):
        values = coords[key]
        pitch = values[1]-values[0]
        result.extend([float(values[0]-pitch/2), float(values[-1]+pitch/2)])
    return result


def _read_rows(source, name, start, stop):
    """Verify the exact blocks consumed, including a mutation after preflight.

    A request block can cross source chunk boundaries. Read and hash each whole
    canonical source chunk, then copy only its requested rows into float64.
    """
    ny, nx, nt = source.manifest["shape"]
    chunk = source.manifest["tile_rows"]
    result = np.empty((stop-start, nx, nt), dtype=np.float64)
    for lo in range((start//chunk)*chunk, stop, chunk):
        hi = min(ny, lo+chunk)
        values = np.asarray(source.group[name][lo:hi, :, :])
        expected = source.manifest["completed_chunks"][str(lo)][f"{name}_sha256"]
        if array_sha256(values) != expected:
            raise ValueError(f"Stored {name} checksum changed while reading comparison source rows {lo}:{hi}.")
        if not np.isfinite(values).all():
            raise ValueError("Saved comparison signals contain nonfinite values.")
        if name == "envelope" and np.any(values < 0):
            raise ValueError("Saved analytic envelopes must be nonnegative.")
        a, b = max(lo, start), min(hi, stop)
        result[a-start:b-start] = values[a-lo:b-lo]
    return result


def _reduce(a, b, gate, plan):
    ny, nx, _ = a.manifest["shape"]
    lo, hi = gate["start_index"], gate["stop_index_exclusive"]
    maps, metrics = {}, {}
    for name, mode in (("rf", "rms_rf"), ("envelope", "peak_envelope")):
        accumulator = _Metrics()
        reference = np.empty((ny, nx), dtype=np.float64)
        candidate = np.empty_like(reference)
        for y0 in range(0, ny, plan["row_block_size"]):
            y1 = min(ny, y0+plan["row_block_size"])
            aa = _read_rows(a, name, y0, y1)
            bb = _read_rows(b, name, y0, y1)
            accumulator.add(aa, bb, y0)
            if name == "envelope" and (np.any(aa < 0) or np.any(bb < 0)):
                raise ValueError("Saved analytic envelopes must be nonnegative.")
            if name == "rf":
                reference[y0:y1] = np.sqrt(np.mean(np.square(aa[:, :, lo:hi]), axis=2, dtype=np.float64))
                candidate[y0:y1] = np.sqrt(np.mean(np.square(bb[:, :, lo:hi]), axis=2, dtype=np.float64))
            else:
                reference[y0:y1], candidate[y0:y1] = aa[:, :, lo:hi].max(2), bb[:, :, lo:hi].max(2)
            del aa, bb
        metrics[name] = accumulator.result(a.coordinates)
        map_metrics = _Metrics()
        map_metrics.add(reference, candidate)
        maps[mode] = {"reference": reference.tolist(), "candidate": candidate.tolist(),
            "difference": (candidate-reference).tolist(), "metrics": map_metrics.result(a.coordinates),
            "unit": a.manifest["arrays"][name]["units"], "extent_mm": _extent(a.coordinates),
            "processing_source": "saved signed RF" if name == "rf" else "independently saved analytic envelope"}
    return metrics, maps


def _differences(a, b):
    settings = []

    def visit(left, right, path):
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(left.keys() | right.keys()):
                if key not in left or key not in right:
                    settings.append({"path": f"{path}.{key}", "reference": left.get(key), "candidate": right.get(key),
                                     "reference_present": key in left, "candidate_present": key in right})
                else:
                    visit(left[key], right[key], f"{path}.{key}")
        elif left != right:
            settings.append({"path": path, "reference": left, "candidate": right})

    visit(a["request"].get("acquisition", {}), b["request"].get("acquisition", {}), "acquisition")
    geometry = {"reference": json_sha256(a["request"].get("twin", {})), "candidate": json_sha256(b["request"].get("twin", {}))}
    return {"settings": settings, "geometry_changed": geometry["reference"] != geometry["candidate"],
            "geometry_sha256": geometry, "materials_changed": a["materials_sha256"] != b["materials_sha256"],
            "solver_changed": a["solver"] != b["solver"]}


def _source_summary(manifest):
    return {"dataset_id": manifest["dataset_id"], "name": manifest["request"].get("twin", {}).get("name", "SAM recording"),
            "manifest_sha256": json_sha256(manifest), "input_sha256": manifest["input_sha256"],
            "request_sha256": manifest["request_sha256"], "materials_sha256": manifest["materials_sha256"],
            "path_model": manifest["request"].get("acquisition", {}).get("path_model", "voxel_centers_v1"),
            "solver": manifest["solver"]}


def _unchanged(source):
    current, _ = _bounded_json(source.path / "manifest.json", MAX_SOURCE_MANIFEST_BYTES,
                              MAX_SOURCE_EXPANDED_BYTES, "Comparison source provenance")
    if json_sha256(current) != json_sha256(source.manifest):
        raise ValueError("Source manifest changed during comparison processing.")


def compute_comparison(root, request):
    request = request if isinstance(request, SamComparisonRequest) else SamComparisonRequest.model_validate(request)
    a = _source(root, request.reference_dataset_id)
    b = _source(root, request.candidate_dataset_id, a.manifest_workspace_bytes)
    _compatible(a, b)
    xi, yi, _ = _indices(a.manifest["shape"], request.x_index, request.y_index)
    gate = _gate(a.coordinates["time_us"], request.gate_start_us, request.gate_end_us)
    plan = _plan(a, b)
    metrics, maps = _reduce(a, b, gate, plan)
    _unchanged(a)
    _unchanged(b)
    differences = _differences(a.manifest, b.manifest)
    warnings = ["Synthetic relative signals; the selected reference is a baseline, not ground truth or experimental validation.",
                "Differences are candidate B minus reference A. No registration, resampling, peak alignment or amplitude normalization was applied.",
                "A nonzero numerical difference is not a detection threshold, resolution measurement or accuracy claim."]
    changed = len(differences["settings"])+sum(differences[k] for k in ("geometry_changed", "materials_changed", "solver_changed"))
    if changed > 1:
        warnings.append("Multiple acquisition, geometry, material or solver differences prevent attribution to a single variable.")
    if differences["geometry_changed"]:
        warnings.append("The full frozen twin records differ. Their hashes include metadata as well as geometry; inspect the source snapshots before attributing a signal change.")
    return {"schema_version": 1, "kind": "sam_comparison", "processing_version": PROCESSING_VERSION,
        "request": request.model_dump(mode="json"), "reference": _source_summary(a.manifest), "candidate": _source_summary(b.manifest),
        "shape": a.manifest["shape"], "axis_order": ["y", "x", "time"],
        "coordinates": {name: values.tolist() for name, values in a.coordinates.items()},
        "units": {name: a.manifest["arrays"][name]["units"] for name in ("rf", "envelope")},
        "time_reference": a.manifest["metadata"]["time_zero"], "gate": gate,
        "selected_trace": {"x_index": xi, "y_index": yi, "x_mm": float(a.coordinates["x_mm"][xi]), "y_mm": float(a.coordinates["y_mm"][yi])},
        "metrics": metrics, "gate_maps": maps, "differences": differences, "warnings": warnings,
        "formulas": FORMULAS, "resources": plan,
        "provenance": {"reference_manifest": a.manifest, "candidate_manifest": b.manifest,
            "numerical_packages": {"numpy": np.__version__, "zarr": zarr.__version__},
            "processing_source_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                         for name in ("comparisons.py", "comparison_schemas.py")}}}


def _scale(a, b):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    maximum = float(np.max(np.abs(b-a)))
    return {"shared": [float(min(a.min(), b.min())), float(max(a.max(), b.max()))],
            "difference": [-maximum, maximum]}


def comparison_view(root, report, *, x_index=None, y_index=None, time_index=None):
    retained_report_bytes = _json_workspace(canonical_json(report))
    if retained_report_bytes > MAX_REPORT_EXPANDED_BYTES:
        raise ValueError("Comparison report exceeds the bounded expanded-provenance workspace.")
    a = _source(root, report["reference"]["dataset_id"], retained_report_bytes)
    b = _source(root, report["candidate"]["dataset_id"], retained_report_bytes+a.manifest_workspace_bytes)
    for side, source in (("reference", a), ("candidate", b)):
        if json_sha256(source.manifest) != report[side]["manifest_sha256"]:
            raise ValueError(f"The {side} source no longer matches the comparison's frozen manifest.")
    _compatible(a, b)
    shape, coords = a.manifest["shape"], a.coordinates
    xi, yi, ti = _indices(shape, report["selected_trace"]["x_index"] if x_index is None else x_index,
                         report["selected_trace"]["y_index"] if y_index is None else y_index, time_index)
    plan = _plan(a, b, retained_report_bytes)
    traces = {"time_us": coords["time_us"].tolist(), "reference": {}, "candidate": {}, "difference": {}}
    instantaneous = {}
    scales = {"traces": {}, "instantaneous": {}, "gate_maps": {}}
    for name in ("rf", "envelope"):
        aa = _read_rows(a, name, yi, yi+1)[0, xi].copy()
        bb = _read_rows(b, name, yi, yi+1)[0, xi].copy()
        if not np.isfinite(aa).all() or not np.isfinite(bb).all():
            raise ValueError("Saved comparison traces contain nonfinite values.")
        traces["reference"][name], traces["candidate"][name], traces["difference"][name] = aa.tolist(), bb.tolist(), (bb-aa).tolist()
        scales["traces"][name] = _scale(aa, bb)
        am, bm = np.empty(shape[:2], dtype=np.float64), np.empty(shape[:2], dtype=np.float64)
        for y0 in range(0, shape[0], plan["row_block_size"]):
            y1 = min(shape[0], y0+plan["row_block_size"])
            am[y0:y1] = _read_rows(a, name, y0, y1)[:, :, ti]
            bm[y0:y1] = _read_rows(b, name, y0, y1)[:, :, ti]
        if not np.isfinite(am).all() or not np.isfinite(bm).all():
            raise ValueError("Saved comparison maps contain nonfinite values.")
        instantaneous[name] = {"reference": am.tolist(), "candidate": bm.tolist(), "difference": (bm-am).tolist(),
                               "extent_mm": _extent(coords), "unit": report["units"][name]}
        scales["instantaneous"][name] = _scale(am, bm)
    for mode, values in report["gate_maps"].items():
        scales["gate_maps"][mode] = _scale(values["reference"], values["candidate"])
    _unchanged(a)
    _unchanged(b)
    return {"comparison_id": report["id"], "cursor": {"x_index": xi, "y_index": yi, "time_index": ti,
        "x_mm": float(coords["x_mm"][xi]), "y_mm": float(coords["y_mm"][yi]), "time_us": float(coords["time_us"][ti])},
        "traces": traces, "gate": report["gate"], "gate_maps": report["gate_maps"],
        "instantaneous": instantaneous, "display_scales": scales, "warnings": report["warnings"]}


def _report_summary(report):
    return {key: report[key] for key in ("id", "comparison_id", "kind", "created_at", "processing_version",
                                        "reference", "candidate", "gate", "shape", "selected_trace")}


class ComparisonStore:
    """Exclusive new-file writes; no update, delete, resume or source mutation."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.directory = self.root / "comparisons"

    def _path(self, identifier):
        if self.directory.is_symlink() or self.directory.is_junction():
            raise ValueError("Comparison storage must not be a symbolic link or directory junction.")
        target = self.directory / f"{checked_id(identifier)}.json"
        if target.is_symlink() or target.is_junction():
            raise ValueError("Comparison reports must not be symbolic links or directory junctions.")
        if target.resolve().parent != self.directory.resolve():
            raise ValueError("Comparison path leaves its data directory.")
        return target

    def create(self, request):
        report = compute_comparison(self.root, request)
        identifier = str(uuid4())
        report.update(id=identifier, comparison_id=identifier, created_at=now_iso())
        report["report_sha256"] = json_sha256(report)
        payload = canonical_json(report)
        if len(payload) > MAX_REPORT_BYTES:
            raise ValueError("Comparison report exceeds the 64 MiB local report limit.")
        if _json_workspace(payload) > MAX_REPORT_EXPANDED_BYTES:
            raise ValueError("Comparison report exceeds the bounded expanded-provenance workspace.")
        check_disk_space(self.root, len(payload))
        path = self._path(identifier)
        self.directory.mkdir(parents=True, exist_ok=True)
        # Stage a non-report filename then publish without overwrite by a hard
        # link. Readers never see an incomplete .json; UUID collision is an error.
        temporary = self.directory / f".{identifier}.tmp"
        staged = False
        try:
            import os
            with temporary.open("xb") as stream:
                staged = True
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, path)
        finally:
            if staged:
                temporary.unlink(missing_ok=True)
        return report

    def read(self, identifier):
        path = self._path(identifier)
        if not path.is_file():
            raise KeyError(identifier)
        report, _ = _bounded_json(path, MAX_REPORT_BYTES, MAX_REPORT_EXPANDED_BYTES, "Comparison report")
        if not isinstance(report, dict) or report.get("id") != identifier or report.get("comparison_id") != identifier or report.get("kind") != "sam_comparison":
            raise ValueError("Comparison report identity is invalid.")
        expected = report.get("report_sha256")
        if expected != json_sha256({key: value for key, value in report.items() if key != "report_sha256"}):
            raise ValueError("Comparison report checksum mismatch.")
        return report

    def list(self):
        self._path("00000000-0000-0000-0000-000000000000")
        if not self.directory.exists():
            return []
        items = [_report_summary(self.read(path.stem)) for path in self.directory.glob("*.json")]
        return sorted(items, key=lambda value: (value["created_at"], value["id"]), reverse=True)

    def view(self, identifier, **kwargs):
        return comparison_view(self.root, self.read(identifier), **kwargs)


def comparison_csv(report):
    """Self-contained tidy report: JSON cells retain provenance and exact axes."""
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["section", "field", "value_json"])
    for key in ("id", "comparison_id", "kind", "schema_version", "created_at", "processing_version", "report_sha256",
                "request", "reference", "candidate", "shape", "axis_order", "units", "time_reference", "gate",
                "selected_trace", "differences", "warnings", "formulas", "resources", "coordinates", "provenance"):
        writer.writerow(["report", key, canonical_json(report[key]).decode("utf-8")])
    for name, values in report["metrics"].items():
        for key, value in values.items():
            writer.writerow([f"metrics.{name}", key, canonical_json(value).decode("utf-8")])
    for name, values in report["gate_maps"].items():
        for key, value in values.items():
            writer.writerow([f"gate_maps.{name}", key, canonical_json(value).decode("utf-8")])
    return stream.getvalue()
