"""Immutable, masked comparisons of verified saved parallel X-ray products.

Only frozen measurements are read. No geometry constructor or forward projector
is used, and no registration, angle matching or amplitude fitting is performed.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
from uuid import uuid4

import numpy as np
import zarr

from .comparisons import ComparisonStore, _bounded_json, _differences, _json_workspace
from .datasets import (array_sha256, canonical_json, check_disk_space,
                       coordinate_sha256, json_sha256, now_iso, validate_dataset_paths)
from .xray_comparison_schemas import XrayComparisonRequest
from .xray_datasets import XrayDatasetStore

PROCESSING_VERSION = "xray-projection-comparison-0.10.0"
KIND = "xray_projection_comparison"
MAX_SOURCE_BYTES = MAX_WORKSPACE_BYTES = 512*1024**2
MAX_SOURCE_MANIFEST_BYTES = 2*1024**2
MAX_SOURCE_EXPANDED_BYTES = 64*1024**2
MAX_REPORT_BYTES = 64*1024**2
MAX_REPORT_EXPANDED_BYTES = 192*1024**2
PRODUCTS = ("counts", "transmission", "line_integrals", "valid_mask")
COORDINATES = XrayDatasetStore.coordinate_names
SEMANTICS = ("pose_vector_definition", "rotation_convention", "transmission_definition",
             "line_integrals_definition", "valid_mask_definition")
FORMULAS = {
    "difference": "candidate B minus reference A; no angle matching, resampling, fitting or image normalization",
    "bias": "sum(B-A)/N over the declared product support",
    "mae": "sum(abs(B-A))/N over the declared product support",
    "rmse": "sqrt(sum((B-A)^2)/N) over the declared product support",
    "relative_l2": "sqrt(sum((B-A)^2)/sum(A^2)); null for empty support or a zero reference norm",
    "counts_transmission_support": "all saved detector pixels, including zero counts",
    "line_integrals_support": "intersection of the two positive-count masks; zero-only half-count placeholders excluded",
    "mask_meaning": "positive-count logarithm support; not experimental quality or projected specimen-envelope coverage",
    "normalization": "stored counts/I0 and stored negative logarithms retain each source's frozen I0; no new count scaling",
    "maximum_locator": "first maximum absolute supported difference in view, detector row v, detector column u order",
    "accumulation": "float64 streaming reductions of authoritative float32 saved values",
}


class XrayComparisonCompatibilityError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("Saved X-ray sources are incompatible with the explicit geometry, units or normalization policy.")


@dataclass
class Source:
    path: Path
    manifest: dict
    group: object
    coordinates: dict
    manifest_workspace_bytes: int
    verification_peak_bytes: int
    photons: int
    noise: bool


def _load_source(root, identifier, retained_bytes=0):
    store = XrayDatasetStore(root)
    path = store.path(identifier)
    if not path.is_dir():
        raise KeyError(identifier)
    validate_dataset_paths(path, identifier, include_arrays=False)
    manifest, provenance_bytes = _bounded_json(path/"manifest.json", MAX_SOURCE_MANIFEST_BYTES,
        MAX_SOURCE_EXPANDED_BYTES, "X-ray comparison source provenance")
    if not isinstance(manifest, dict) or manifest.get("dataset_id") != identifier or manifest.get("kind") != "xray_projection_volume":
        raise ValueError("X-ray comparisons require saved X-ray projection sources with matching identities.")
    if manifest.get("complete") is not True or manifest.get("state") != "completed":
        raise ValueError("X-ray comparison sources must be completed datasets.")
    shape = manifest.get("shape")
    if (not isinstance(shape, list) or len(shape) != 3 or any(type(v) is not int for v in shape) or
            not (1 <= shape[0] <= 720 and 16 <= shape[1] <= 256 and 16 <= shape[2] <= 256)):
        raise ValueError("X-ray comparison source shape exceeds supported saved projection dimensions.")
    views, rows, cols = shape
    if views*rows*cols*16 > MAX_SOURCE_BYTES:
        raise ValueError("X-ray comparison source products exceed the 512 MiB saved-volume bound.")
    if manifest.get("tile_rows") != 1 or manifest.get("axis_order") != ["view", "v", "u"]:
        raise ValueError("X-ray comparisons require one-view chunks and view,v,u axes.")
    verification_peak = retained_bytes+2*provenance_bytes+rows*cols*64+16*1024**2
    if verification_peak > MAX_WORKSPACE_BYTES:
        raise ValueError("X-ray comparison source verification exceeds the 512 MiB workspace.")
    files = validate_dataset_paths(path, identifier)
    # Bound Zarr metadata before its parser runs as well as the top-level
    # manifest. Unexpected JSON attributes must not bypass provenance limits.
    zarr_json_bytes = zarr_expansion = 0
    for child in files:
        if child.name == "zarr.json":
            if child.stat().st_size > 256*1024:
                raise ValueError("X-ray Zarr metadata exceeds its bounded JSON limit.")
            with child.open("rb") as stream:
                payload = stream.read(256*1024+1)
            zarr_json_bytes += len(payload)
            zarr_expansion += _json_workspace(payload)
    if zarr_json_bytes > 1024**2 or zarr_expansion > 16*1024**2:
        raise ValueError("X-ray Zarr metadata exceeds its bounded expanded workspace.")
    provenance_bytes += zarr_expansion
    verification_peak += 2*zarr_expansion
    if verification_peak > MAX_WORKSPACE_BYTES:
        raise ValueError("X-ray comparison metadata verification exceeds the 512 MiB workspace.")
    acquisition = manifest.get("request", {}).get("acquisition", {})
    photons, noise = acquisition.get("photons"), acquisition.get("noise")
    if type(photons) is not int or not 1000 <= photons <= 1_000_000 or type(noise) is not bool:
        raise ValueError("X-ray source must declare its frozen incident photons and observed/expected noise status.")
    group = zarr.open_group(str(path/"data.zarr"), mode="r")
    if (group.attrs.get("dataset_id") != identifier or group.attrs.get("kind") != "xray_projection_volume" or
            group.attrs.get("axis_order") != ["view", "v", "u"]):
        raise ValueError("X-ray Zarr dataset identity, kind or axes disagree with its frozen manifest.")
    descriptors = manifest.get("arrays", {})
    expected_units = dict(XrayDatasetStore.signal_units)
    expected_units["counts"] = "observed photon counts per detector pixel" if noise else "expected photons per detector pixel"
    for name in PRODUCTS:
        if (name not in group or group[name].shape != tuple(shape) or group[name].dtype != np.dtype("float32") or
                group[name].chunks != (1, rows, cols)):
            raise ValueError(f"Invalid X-ray {name} array shape, dtype or canonical view chunk layout.")
        desc = descriptors.get(name, {})
        if (desc.get("path") != name or desc.get("shape") != shape or desc.get("axes") != ["view", "v", "u"] or
                desc.get("dtype") != "float32" or desc.get("units") != expected_units[name]):
            raise ValueError(f"Unsupported or inconsistent frozen X-ray {name} array registry/units.")
    shapes = {"angles_deg": (views,), "u_mm": (cols,), "v_mm": (rows,),
              **{name: (views, 3) for name in XrayDatasetStore.pose_names}}
    coordinates = {}
    for name, expected_shape in shapes.items():
        expected_unit = "degrees" if name == "angles_deg" else "mm" if name in ("u_mm", "v_mm", "detector_center_mm") else "unit vector"
        expected_axes = ["view", "xyz"] if len(expected_shape) == 2 else [{"angles_deg": "view", "u_mm": "u", "v_mm": "v"}[name]]
        desc = descriptors.get(name, {})
        if (name not in group or group[name].shape != expected_shape or group[name].dtype != np.dtype("float64") or
                group[name].chunks != expected_shape or
                desc.get("shape") != list(expected_shape) or desc.get("path") != name or desc.get("dtype") != "float64" or
                desc.get("axes") != expected_axes or desc.get("units") != expected_unit):
            raise ValueError(f"Invalid X-ray coordinate or pose registry or canonical chunk layout: {name}.")
        values = np.asarray(group[name][:], dtype=np.float64)
        if coordinate_sha256(values) != manifest.get("coordinates_sha256", {}).get(name):
            raise ValueError(f"X-ray coordinate checksum mismatch: {name}.")
        if not np.isfinite(values).all() or np.any(np.abs(values) > 1000):
            raise ValueError(f"X-ray coordinate or pose is nonfinite or outside supported bounds: {name}.")
        if len(expected_shape) == 1 and len(values) > 1:
            if not np.all(np.diff(values) > 0) or not np.allclose(np.diff(values), values[1]-values[0], rtol=0, atol=1e-10):
                raise ValueError(f"X-ray comparison requires increasing uniformly sampled {name}; no interpolation is performed.")
        coordinates[name] = values
    XrayDatasetStore._validate_poses(coordinates)
    if any(not isinstance(manifest.get("metadata", {}).get(key), str) or not manifest["metadata"][key].strip() for key in SEMANTICS):
        raise ValueError("X-ray source lacks frozen projection/pose interpretation; no semantics will be inferred.")
    verified = store.verify_complete(identifier)
    if json_sha256(verified) != json_sha256(manifest):
        raise ValueError("X-ray source manifest changed during verification.")
    return Source(path, manifest, group, coordinates, provenance_bytes, verification_peak, photons, noise)


def _compatible(a, b, request):
    issues = []
    if a.manifest["shape"] != b.manifest["shape"]:
        issues.append({"field": "shape", "reason": "Shape mismatch", "reference_shape": a.manifest["shape"], "candidate_shape": b.manifest["shape"]})
    for name in COORDINATES:
        left, right = a.coordinates[name], b.coordinates[name]
        if left.shape != right.shape:
            issues.append({"field": name, "reason": "Coordinate/pose shape mismatch", "reference_shape": list(left.shape), "candidate_shape": list(right.shape)})
        elif not np.array_equal(left, right):
            flat = int(np.flatnonzero(left != right)[0])
            index = [int(v) for v in np.unravel_index(flat, left.shape)]
            issues.append({"field": name, "reason": "Exact equality required; no one-ULP relabeling, angle matching or pose adjustment",
                "first_different_index": index, "max_absolute_difference": float(np.max(np.abs(right-left))),
                "reference_value": float(left.flat[flat]), "candidate_value": float(right.flat[flat])})
    for name in SEMANTICS:
        left, right = a.manifest["metadata"][name], b.manifest["metadata"][name]
        if left != right:
            issues.append({"field": f"metadata.{name}", "reason": "Frozen numerical interpretation mismatch", "reference": left, "candidate": right})
    if request.normalization == "native" and a.photons != b.photons:
        issues.append({"field": "photons", "reason": "Native comparison requires equal I0; explicitly choose per_source_incident with transmission or line_integrals", "reference": a.photons, "candidate": b.photons})
    if a.noise != b.noise and request.observation_policy != "observed_vs_expected":
        issues.append({"field": "noise", "reason": "Observed versus expected normalized signals require explicit observed_vs_expected policy", "reference": a.noise, "candidate": b.noise})
    if issues:
        raise XrayComparisonCompatibilityError(issues)


def _read_view(source, index):
    arrays = {}
    for name in PRODUCTS:
        values = np.asarray(source.group[name][index:index+1, :, :])
        expected = source.manifest["completed_chunks"][str(index)][f"{name}_sha256"]
        if array_sha256(values) != expected:
            raise ValueError(f"Stored {name} checksum changed while reading X-ray comparison view {index}.")
        if not np.isfinite(values).all():
            raise ValueError(f"Saved X-ray {name} contains nonfinite values, including outside log support.")
        arrays[name] = values[0].astype(np.float64)
    counts = arrays["counts"]
    if np.any(counts < 0):
        raise ValueError("Saved photon counts must be nonnegative.")
    if source.noise and (np.any(counts > 2**24) or np.any(counts != np.floor(counts))):
        raise ValueError("Observed Poisson counts must be exactly represented nonnegative integers.")
    if not source.noise and np.any(counts > source.photons):
        raise ValueError("Expected counts cannot exceed frozen incident photons.")
    expected_mask = counts > 0
    if not np.array_equal(arrays["valid_mask"], expected_mask):
        raise ValueError("Saved valid_mask must be binary and equal positive-count log support.")
    if not np.allclose(arrays["transmission"], counts/source.photons, rtol=5e-7, atol=1e-44):
        raise ValueError("Saved transmission does not match stored counts divided by source incident photons.")
    logarithm = -np.log(np.where(expected_mask, counts, .5)/source.photons)
    if not np.allclose(arrays["line_integrals"], logarithm, rtol=5e-7, atol=1e-6):
        raise ValueError("Saved line_integrals do not match the frozen zero-only half-count logarithm.")
    return arrays


SUPPORT_KEYS = ("total", "reference_valid", "candidate_valid", "common_valid", "reference_only", "candidate_only", "neither_valid")


def _support(a, b):
    a, b = a.astype(bool), b.astype(bool)
    return {"total": a.size, "reference_valid": int(a.sum()), "candidate_valid": int(b.sum()),
        "common_valid": int((a & b).sum()), "reference_only": int((a & ~b).sum()),
        "candidate_only": int((b & ~a).sum()), "neither_valid": int((~a & ~b).sum())}


def _fractions(counts):
    return {**counts, "fractions": {key: counts[key]/counts["total"] for key in SUPPORT_KEYS if key != "total"},
            "meaning": FORMULAS["mask_meaning"]}


class Metrics:
    def __init__(self):
        self.count = 0
        self.bias = self.absolute = self.error2 = self.reference2 = 0.
        self.maximum = -1.
        self.location = None

    def add(self, a, b, support, view_index, coordinates):
        positions = np.flatnonzero(support)
        if not len(positions):
            return
        left, right = a.flat[positions], b.flat[positions]
        difference = right-left
        self.count += len(positions)
        self.bias += float(difference.sum(dtype=np.float64))
        self.absolute += float(np.abs(difference).sum(dtype=np.float64))
        self.error2 += float(np.square(difference).sum(dtype=np.float64))
        self.reference2 += float(np.square(left).sum(dtype=np.float64))
        where = int(np.argmax(np.abs(difference)))
        maximum = float(abs(difference[where]))
        if maximum > self.maximum:
            row, col = np.unravel_index(positions[where], a.shape)
            self.maximum = maximum
            self.location = {"view_index": view_index, "detector_row": int(row), "detector_col": int(col),
                "angle_deg": float(coordinates["angles_deg"][view_index]), "u_mm": float(coordinates["u_mm"][col]),
                "v_mm": float(coordinates["v_mm"][row]), "signed_difference": float(difference[where])}

    def result(self):
        empty, zero = self.count == 0, self.reference2 == 0
        reason = "No common valid logarithm samples; metric support is empty." if empty else None
        return {"sample_count": self.count, "bias": None if empty else self.bias/self.count,
            "mae": None if empty else self.absolute/self.count, "rmse": None if empty else float(np.sqrt(self.error2/self.count)),
            "max_absolute_difference": None if empty else self.maximum, "max_location": self.location,
            "relative_l2": None if empty or zero else float(np.sqrt(self.error2/self.reference2)),
            "relative_l2_reason": reason if empty else "Reference L2 norm is zero; relative error is undefined." if zero else None,
            "reference_l2": None if empty else float(np.sqrt(self.reference2)), "metric_reason": reason}


def _cursor(source, view_index=None, detector_row=None, detector_col=None):
    views, rows, cols = source.manifest["shape"]
    vi, row, col = 0 if view_index is None else view_index, rows//2 if detector_row is None else detector_row, cols//2 if detector_col is None else detector_col
    if (any(type(v) is not int for v in (vi, row, col)) or
            not (0 <= vi < views and 0 <= row < rows and 0 <= col < cols)):
        raise ValueError(f"X-ray comparison indices must lie inside view:0..{views-1}, detector_row:0..{rows-1}, detector_col:0..{cols-1}.")
    coords = source.coordinates
    return {"view_index": vi, "detector_row": row, "detector_col": col,
            "angle_deg": float(coords["angles_deg"][vi]), "v_mm": float(coords["v_mm"][row]), "u_mm": float(coords["u_mm"][col])}


def _plan(a, b, retained_report_bytes=0):
    views, rows, cols = a.manifest["shape"]
    # Initial report includes three planes, three row profiles, three sinograms
    # and their validity masks. Include Python display lists as well as arrays.
    display_cells = rows*cols+views*cols+cols
    provenance = 2*(a.manifest_workspace_bytes+b.manifest_workspace_bytes)
    peak = max(a.verification_peak_bytes, b.verification_peak_bytes,
        retained_report_bytes+provenance+rows*cols*256+display_cells*384+views*8192+16*1024**2)
    if peak > MAX_WORKSPACE_BYTES:
        raise ValueError("X-ray comparison exceeds the 512 MiB bounded numerical/report workspace.")
    return {"view_block_size": 1, "estimated_peak_bytes": peak,
            "estimated_provenance_workspace_bytes": provenance,
            "workspace_definition": "One view from each source, all four product relationship checks, float64 reductions, bounded display lists and overlapping frozen provenance copies; not process RSS."}


def _unit(source, product):
    return source.manifest["arrays"][product]["units"]


def _values(values, support):
    # JSON null at unsupported samples; a numerical zero remains an actual value.
    output = values.astype(object)
    output[~support] = None
    return output.tolist()


def _display(a, b, am, bm, product, unit):
    common = am & bm
    full = np.ones(a.shape, dtype=bool)
    sa, sb, sd = (am, bm, common) if product == "line_integrals" else (full, full, full)
    return {"reference": _values(a, sa), "candidate": _values(b, sb), "difference": _values(b-a, sd),
        "reference_valid_mask": am.tolist(), "candidate_valid_mask": bm.tolist(), "common_valid_mask": common.tolist(), "unit": unit}


def _scale(a, b, am, bm, product):
    if product != "line_integrals":
        am, bm = np.ones(a.shape, bool), np.ones(b.shape, bool)
    finite_source = [values[mask] for values, mask in ((a, am), (b, bm)) if np.any(mask)]
    shared = [float(min(v.min() for v in finite_source)), float(max(v.max() for v in finite_source))] if finite_source else [None, None]
    common = am & bm
    maximum = float(np.max(abs(b[common]-a[common]))) if np.any(common) else None
    return {"shared": shared, "difference": [-maximum, maximum] if maximum is not None else [None, None]}


def _edges(values):
    pitch = values[1]-values[0]
    return [float(values[0]-pitch/2), float(values[-1]+pitch/2)]


def _view_products(a, b, request, cursor, *, metrics=False):
    views, rows, cols = a.manifest["shape"]
    vi, row = cursor["view_index"], cursor["detector_row"]
    sino_a, sino_b = np.empty((views, cols)), np.empty((views, cols))
    sino_am, sino_bm = np.empty((views, cols), bool), np.empty((views, cols), bool)
    accumulator, total_support, per_view = Metrics(), dict.fromkeys(SUPPORT_KEYS, 0), []
    selected = None
    for index in range(views):
        aa, bb = _read_view(a, index), _read_view(b, index)
        av, bv = aa[request.product], bb[request.product]
        am, bm = aa["valid_mask"].astype(bool), bb["valid_mask"].astype(bool)
        sino_a[index], sino_b[index], sino_am[index], sino_bm[index] = av[row], bv[row], am[row], bm[row]
        counts = _support(am, bm)
        for key in SUPPORT_KEYS:
            total_support[key] += counts[key]
        if metrics:
            supported = am & bm if request.product == "line_integrals" else np.ones(av.shape, bool)
            current = Metrics()
            current.add(av, bv, supported, index, a.coordinates)
            accumulator.add(av, bv, supported, index, a.coordinates)
            per_view.append({"view_index": index, "angle_deg": float(a.coordinates["angles_deg"][index]),
                             "metrics": current.result(), "support": _fractions(counts)})
        if index == vi:
            selected = (av.copy(), bv.copy(), am.copy(), bm.copy())
        del aa, bb, av, bv, am, bm
    coords, unit = a.coordinates, _unit(a, request.product)
    angles = coords["angles_deg"]
    step = float(angles[1]-angles[0]) if views > 1 else float(a.manifest["request"]["acquisition"]["angle_span_deg"])
    if not np.isfinite(step) or step <= 0:
        raise ValueError("Saved X-ray angular display interval must be finite and positive.")
    angular_edges = np.r_[angles-step/2, angles[-1]+step/2]
    plane = _display(*selected, request.product, unit)
    plane["extent_mm"] = [*_edges(coords["u_mm"]), *_edges(coords["v_mm"])]
    profile = _display(*(values[row] for values in selected), request.product, unit)
    profile["u_mm"] = coords["u_mm"].tolist()
    sinogram = _display(sino_a, sino_b, sino_am, sino_bm, request.product, unit)
    sinogram.update(angles_deg=angles.tolist(), angle_bin_edges_deg=angular_edges.tolist(),
                    extent=[*_edges(coords["u_mm"]), float(angular_edges[0]), float(angular_edges[-1])])
    result = {"product": request.product, "unit": unit, "cursor": cursor, "projection": plane,
        "profile": profile, "sinogram": sinogram, "support": _fractions(total_support),
        "pose": {name: coords[name][vi].tolist() for name in XrayDatasetStore.pose_names},
        "display_scales": {"projection": _scale(*selected, request.product),
            "profile": _scale(*(values[row] for values in selected), request.product),
            "sinogram": _scale(sino_a, sino_b, sino_am, sino_bm, request.product)}}
    return result, accumulator.result() if metrics else None, per_view


def _summary(source):
    m = source.manifest
    return {"dataset_id": m["dataset_id"], "name": m["request"].get("twin", {}).get("name", "X-ray acquisition"),
        "manifest_sha256": json_sha256(m), "input_sha256": m["input_sha256"], "request_sha256": m["request_sha256"],
        "materials_sha256": m["materials_sha256"], "solver": m["solver"], "photons": source.photons,
        "observation_kind": "observed" if source.noise else "expected"}


def _unchanged(source):
    current, _ = _bounded_json(source.path/"manifest.json", MAX_SOURCE_MANIFEST_BYTES,
        MAX_SOURCE_EXPANDED_BYTES, "X-ray comparison source provenance")
    if json_sha256(current) != json_sha256(source.manifest):
        raise ValueError("X-ray source manifest changed during comparison processing.")


def compute_xray_comparison(root, request):
    request = request if isinstance(request, XrayComparisonRequest) else XrayComparisonRequest.model_validate(request)
    a = _load_source(root, request.reference_dataset_id)
    b = _load_source(root, request.candidate_dataset_id, a.manifest_workspace_bytes)
    _compatible(a, b, request)
    cursor = _cursor(a, request.view_index, request.detector_row, request.detector_col)
    plan = _plan(a, b)
    view, metrics, per_view = _view_products(a, b, request, cursor, metrics=True)
    _unchanged(a)
    _unchanged(b)
    differences = _differences(a.manifest, b.manifest)
    warnings = ["Synthetic projection products; reference A is a chosen baseline, not attenuation truth or experimental validation.",
        "Log-validity support records positive counts, not experimental quality or detector truncation coverage.",
        "Raw stored products retain blur, counting noise and material-grid approximation. Numerical differences do not establish resolution or detectability.",
        "Detector u/v are local coordinates with offsets already in the saved unit-vector poses; a detector ray is not an xyz specimen point."]
    if a.noise or b.noise:
        warnings.append("A single Poisson draw does not establish statistical performance. Equal seeds with changed intensities do not guarantee independent or cancelled noise.")
    if a.photons != b.photons:
        warnings.append("Incident photon counts differ; the explicit per-source-incident policy uses each source's saved normalized product without rescaling raw counts.")
    if a.noise != b.noise:
        warnings.append("Observed versus expected signals were explicitly selected; their numerical residual is not a detection rate.")
    if len(differences["settings"])+sum(differences[key] for key in ("geometry_changed", "materials_changed", "solver_changed")) > 1:
        warnings.append("Multiple settings or provenance fields differ; inspect them before attributing the result to a single cause.")
    return {"schema_version": 1, "kind": KIND, "processing_version": PROCESSING_VERSION,
        "request": request.model_dump(mode="json"), "reference": _summary(a), "candidate": _summary(b),
        "shape": a.manifest["shape"], "axis_order": ["view", "v", "u"], "product": request.product,
        "unit": _unit(a, request.product), "normalization": request.normalization, "observation_policy": request.observation_policy,
        "coordinates": {name: values.tolist() for name, values in a.coordinates.items()}, "selected_cursor": cursor,
        "metrics": metrics, "per_view": per_view, "support": view["support"], "initial_view": view,
        "differences": differences, "warnings": warnings, "formulas": FORMULAS, "resources": plan,
        "truncation": {side: source.manifest.get("metadata", {}).get("truncated_view_indices",
            source.manifest.get("estimate", {}).get("truncated_view_indices", [])) for side, source in (("reference", a), ("candidate", b))},
        "provenance": {"reference_manifest": a.manifest, "candidate_manifest": b.manifest,
            "numerical_packages": {"numpy": np.__version__, "zarr": zarr.__version__},
            "processing_source_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                for name in ("xray_comparisons.py", "xray_comparison_schemas.py", "comparisons.py", "xray_datasets.py", "datasets.py")}}}


def xray_comparison_view(root, report, *, view_index=None, detector_row=None, detector_col=None):
    retained = _json_workspace(canonical_json(report))
    if retained > MAX_REPORT_EXPANDED_BYTES:
        raise ValueError("X-ray comparison report exceeds the expanded-provenance workspace.")
    a = _load_source(root, report["reference"]["dataset_id"], retained)
    b = _load_source(root, report["candidate"]["dataset_id"], retained+a.manifest_workspace_bytes)
    for side, source in (("reference", a), ("candidate", b)):
        if json_sha256(source.manifest) != report[side]["manifest_sha256"]:
            raise ValueError(f"The {side} source no longer matches its frozen X-ray comparison manifest.")
    request = XrayComparisonRequest.model_validate(report["request"])
    _compatible(a, b, request)
    prior = report["selected_cursor"]
    cursor = _cursor(a, prior["view_index"] if view_index is None else view_index,
                    prior["detector_row"] if detector_row is None else detector_row,
                    prior["detector_col"] if detector_col is None else detector_col)
    _plan(a, b, retained)
    result, _, _ = _view_products(a, b, request, cursor)
    _unchanged(a)
    _unchanged(b)
    return {"comparison_id": report["id"], **result, "warnings": report["warnings"]}


class XrayComparisonStore(ComparisonStore):
    def __init__(self, root):
        super().__init__(root)
        self.directory = self.root/"xray-comparisons"

    def create(self, request):
        report = compute_xray_comparison(self.root, request)
        identifier = str(uuid4())
        report.update(id=identifier, comparison_id=identifier, created_at=now_iso())
        report["initial_view"] = {"comparison_id": identifier, **report["initial_view"], "warnings": report["warnings"]}
        report["report_sha256"] = json_sha256(report)
        payload = canonical_json(report)
        if len(payload) > MAX_REPORT_BYTES or _json_workspace(payload) > MAX_REPORT_EXPANDED_BYTES:
            raise ValueError("X-ray comparison report exceeds its bounded serialized/expanded JSON limit. Select sources with fewer detector/view samples.")
        check_disk_space(self.root, len(payload))
        path = self._path(identifier)
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary, staged = self.directory/f".{identifier}.tmp", False
        try:
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
        report, _ = _bounded_json(path, MAX_REPORT_BYTES, MAX_REPORT_EXPANDED_BYTES, "X-ray comparison report")
        if not isinstance(report, dict) or report.get("id") != identifier or report.get("comparison_id") != identifier or report.get("kind") != KIND:
            raise ValueError("X-ray comparison report identity is invalid.")
        if report.get("report_sha256") != json_sha256({key: value for key, value in report.items() if key != "report_sha256"}):
            raise ValueError("X-ray comparison report checksum mismatch.")
        return report

    def list(self):
        self._path("00000000-0000-0000-0000-000000000000")
        if not self.directory.exists():
            return []
        fields = ("id", "comparison_id", "kind", "created_at", "processing_version", "reference", "candidate",
                  "product", "unit", "normalization", "observation_policy", "selected_cursor", "shape", "metrics", "support")
        values = [{key: report[key] for key in fields} for report in (self.read(path.stem) for path in self.directory.glob("*.json"))]
        return sorted(values, key=lambda item: (item["created_at"], item["id"]), reverse=True)

    def view(self, identifier, **kwargs):
        return xray_comparison_view(self.root, self.read(identifier), **kwargs)


def xray_comparison_csv(report):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["section", "field", "value_json"])
    for key, value in report.items():
        if key in ("metrics", "support"):
            for metric, result in value.items():
                writer.writerow([key, metric, canonical_json(result).decode("utf-8")])
        else:
            writer.writerow(["report", key, canonical_json(value).decode("utf-8")])
    return stream.getvalue()
