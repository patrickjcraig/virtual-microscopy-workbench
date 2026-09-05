"""Saved-ready, bounded independent causal columns with exact response reuse.

Geometry/preflight is separate from waveform synthesis. Identity observation
copies the certified float64 complex pressure/magnitude without further arithmetic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from math import floor

import numpy as np

from .causal_sam_schemas import CausalSamVolumeRequest
from .column_paths import (MATERIAL_IDS, PATH_CONTRACT_VERSION, build_column_paths,
                           column_interface_bounds)
from .layered_schemas import CausalGammaPulseSettings
from .layered_time import (MODEL_VERSION as KERNEL_VERSION, estimate_causal_gamma,
                           causal_gamma_response)
from .materials import MATERIALS, WATER_IMPEDANCE_MRAYL, WATER_SOUND_SPEED_M_S

MODEL_VERSION = "sam-causal-columns-0.13.0"
CERTIFICATE_VERSION = "causal-column-certificate-1"
RESPONSE_MODEL = "layered_causal_gamma_v1"
EXCITATION_MODEL = "causal_gamma_peak_phase_v1"
MAX_COLUMNS = 4096
MAX_VOLUME_BYTES = 512*1024**2
MAX_PEAK_BYTES = 512*1024**2
MAX_INVERSE_WORK = 100_000_000
MAX_LAYER_FREQUENCY_WORK = 5_000_000
MAX_PATH_CANDIDATE_TESTS = 50_000_000
MAX_PATH_EVENT_WORK = 250_000_000
MAX_PLAN_BYTES = 8*1024**2
MAX_PLAN_EXPANDED_BYTES = 32*1024**2
PREPASS_ROWS = 8
SIGNALS = ("rf", "imaginary", "envelope")
OPERATIONAL_DIAGNOSTICS = {"elapsed_seconds", "coefficient_seconds"}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _json_workspace(payload):
    # Count punctuation inside strings too: this conservative structural bound
    # handles many tiny containers, which encoded size alone cannot account for.
    return 8*len(payload)+256*sum(payload.count(t) for t in (b"{", b"[", b",", b":"))


def _admit_json(value, description):
    payload = _canonical(value)
    expanded = _json_workspace(payload)
    if len(payload) > MAX_PLAN_BYTES or expanded > MAX_PLAN_EXPANDED_BYTES:
        raise ValueError(f"{description} exceeds the 8 MiB serialized / 32 MiB expanded causal plan budget.")
    return len(payload), expanded


@dataclass
class PreparedCausalSam:
    request: CausalSamVolumeRequest
    estimate: dict
    metadata: dict
    x_mm: np.ndarray
    y_mm: np.ndarray
    time_us: np.ndarray
    class_index: np.ndarray
    stack_table: list
    pulse_settings: dict
    cache: dict = field(default_factory=dict)

    def close(self):
        self.cache.clear()


def _resolved_medium(identifier):
    if identifier == "water":
        return {"name": "Water", "impedance_mrayl": float(WATER_IMPEDANCE_MRAYL),
                "sound_speed_m_s": float(WATER_SOUND_SPEED_M_S)}
    material = MATERIALS[identifier]
    return {"name": material["name"], "impedance_mrayl": float(material["impedance_mrayl"]),
            "sound_speed_m_s": float(material["sound_speed_m_s"])}


def _stack(labels, endpoints):
    if len(labels) > 256:
        raise ValueError("A full causal column exceeds 256 finite layers. Layers were not dropped.")
    layers, previous = [], 0.
    for label, endpoint in zip(labels, endpoints):
        identifier = "water" if int(label) == 0 else MATERIAL_IDS[int(label)-1]
        end = float(endpoint)
        layers.append({**_resolved_medium(identifier), "thickness_mm": end-previous,
                       "pressure_loss_db_mm": 0., "material_id": identifier})
        previous = end
    return {"incident": _resolved_medium("water"), "terminal": _resolved_medium("water"),
            "layers": layers}


def _numerical_stack(stack):
    # Names/material labels preserve geometry provenance but do not affect the
    # scalar kernel. Every actually used represented numerical property does.
    fields = ("impedance_mrayl", "sound_speed_m_s")
    return {"incident": {k: stack["incident"][k] for k in fields},
            "terminal": {k: stack["terminal"][k] for k in fields},
            "layers": [{k: layer[k] for k in (*fields, "thickness_mm", "pressure_loss_db_mm")}
                       for layer in stack["layers"]]}


def _features(twin, roi, dx, dy, include_defects):
    features, warnings = [], []
    for obj in twin["objects"]:
        if obj.get("layer_role") not in ("microbump", "tsv"):
            continue
        x, y, _ = obj["center_mm"]
        sx, sy, sz = obj["size_mm"]
        intersects = x+sx/2 > roi[0] and x-sx/2 < roi[2] and y+sy/2 > roi[1] and y-sy/2 < roi[3]
        if not intersects:
            continue
        included = include_defects or obj.get("role", "structure") != "defect"
        samples = [sx/dx, sy/dy, None]
        undersampled = [axis for axis, count in zip(("x", "y"), samples[:2]) if count < 2]
        features.append({"id": obj["id"], "layer_role": obj["layer_role"], "included": included,
                         "size_um": [sx*1000, sy*1000, sz*1000], "samples_xyz": samples,
                         "undersampled_axes": undersampled})
        if included and undersampled:
            warnings.append(f"{obj['id']} has fewer than two samples across {', '.join(undersampled)}; continuous Z boundaries do not certify lateral representation or physical resolution.")
    return features, warnings


def _compile(request):
    request = request if isinstance(request, CausalSamVolumeRequest) else CausalSamVolumeRequest.model_validate(request)
    # Make every default explicit before the frozen plan is hashed. Nested twin
    # schemas predate this kind and may leave numeric defaults unvalidated; a
    # full JSON-model roundtrip makes their representation resume-stable too.
    request = CausalSamVolumeRequest.model_validate(request.model_dump(mode="json"))
    frozen = request.model_dump(mode="json")
    request_bytes, request_workspace = _admit_json(frozen, "Frozen causal request")
    a, twin = request.acquisition, frozen["twin"]
    nx, ny = a.scan_nx, a.scan_ny
    if nx*ny > MAX_COLUMNS:
        raise ValueError("Causal recording exceeds 4,096 independent columns.")
    roi = list(a.roi_mm or [0., 0., twin["size_mm"][0], twin["size_mm"][1]])
    dx, dy = (roi[2]-roi[0])/nx, (roi[3]-roi[1])/ny
    x = roi[0]+(np.arange(nx, dtype=np.float64)+.5)*dx
    y = roi[1]+(np.arange(ny, dtype=np.float64)+.5)*dy
    nt = floor(a.record_duration_us*a.sample_rate_mhz+1e-9)+1
    time = np.asarray([a.record_start_us+i/a.sample_rate_mhz for i in range(nt)], dtype=np.float64)
    pulse = {key: getattr(a, key) for key in CausalGammaPulseSettings.model_fields}
    # These small arrays are the actual coordinates used below and persisted.
    coordinate_bytes = (nx+ny+nt)*8
    signal_bytes, map_bytes = 3*nx*ny*nt*8, nx*ny*(8+2)
    if signal_bytes+map_bytes+coordinate_bytes > MAX_VOLUME_BYTES:
        raise ValueError("Causal float64 signals/maps exceed the 512 MiB saved output budget.")
    objects = sum(a.include_defects or obj.get("role", "structure") != "defect" for obj in twin["objects"])
    candidate_tests = 2*objects*nx*ny  # Full bounds pass plus full blocked path pass.
    if candidate_tests > MAX_PATH_CANDIDATE_TESTS:
        raise ValueError("Causal geometry exceeds 50 million candidate tests across its full prepass.")
    bounds = column_interface_bounds(twin, x, y, a.include_defects)
    b = bounds.astype(np.uint64)
    event_work = int((b*(1+np.ceil(np.log2(np.maximum(2, b))).astype(np.uint64))).sum())
    if event_work > MAX_PATH_EVENT_WORK:
        raise ValueError("Causal geometry exceeds 250 million event-work units across its full prepass.")
    path_workspace = 0
    for lo in range(0, ny, PREPASS_ROWS):
        block = b[lo:lo+PREPASS_ROWS]
        events, columns = int(block.sum()), block.size
        path_workspace = max(path_workspace, 32*(events+columns)+64*events+8*(columns+1)+bounds.nbytes+coordinate_bytes+16*1024**2)
    if path_workspace+4*request_workspace+32*1024**2 > MAX_PEAK_BYTES:
        raise ValueError("Causal path preparation exceeds the 512 MiB working-memory budget.")

    class_index = np.empty((ny, nx), dtype=np.uint16)
    geometry_signatures = [[None]*nx for _ in range(ny)]
    stack_table, lookup, geometry_classes = [], {}, set()
    total_inverse = total_layer_work = total_entry_bytes = total_entry_workspace = kernel_peak = 0
    time_list = time.tolist()
    context = {"kernel_version": KERNEL_VERSION, "response_model": RESPONSE_MODEL,
               "excitation_model": EXCITATION_MODEL, "pulse": pulse,
               "time_sha256": hashlib.sha256(time.astype("<f8", copy=False).tobytes()).hexdigest()}
    path_diagnostics = {"columns": nx*ny, "segment_count": 0, "adjusted_endpoint_count": 0,
                        "max_endpoint_adjustment_mm": 0., "contract_version": PATH_CONTRACT_VERSION}
    for lo in range(0, ny, PREPASS_ROWS):
        paths = build_column_paths(twin, x, y[lo:lo+PREPASS_ROWS], a.include_defects)
        d = paths.diagnostics
        path_diagnostics["segment_count"] += int(d["segment_count"])
        path_diagnostics["adjusted_endpoint_count"] += int(d["adjusted_endpoint_count"])
        path_diagnostics["max_endpoint_adjustment_mm"] = max(path_diagnostics["max_endpoint_adjustment_mm"], d["max_endpoint_adjustment_mm"])
        path_diagnostics["tau_z_mm"] = d["tau_z_mm"]
        path_diagnostics["coincidence_policy"] = d["coincidence_policy"]
        for column in range(paths.shape[0]*nx):
            row, col = lo+column//nx, column % nx
            start, stop = map(int, paths.column_offsets[column:column+2])
            labels, ends = paths.material_label[start:stop], paths.z_end_mm[start:stop]
            signature = hashlib.sha256(labels.tobytes()+ends.astype("<f8", copy=False).tobytes()).hexdigest()
            geometry_signatures[row][col] = signature
            geometry_classes.add(signature)
            resolved = _stack(labels, ends)
            key = _canonical({**context, "stack": _numerical_stack(resolved)})
            index = lookup.get(key)
            if index is None:
                estimate = estimate_causal_gamma(resolved, time_list, pulse)
                total_inverse += estimate["inverse_work_units"]
                total_layer_work += estimate["layer_frequency_work_units"]
                if total_inverse > MAX_INVERSE_WORK or total_layer_work > MAX_LAYER_FREQUENCY_WORK:
                    raise ValueError("Whole causal volume exceeds 100 million unique-stack inverse / 5 million layer-frequency work units. No columns or tolerance were changed.")
                kernel_peak = max(kernel_peak, estimate["estimated_peak_bytes"])
                index = len(stack_table)
                entry = {"class_id": index, "response_sha256": hashlib.sha256(key).hexdigest(),
                         "stack": resolved, "representative_yx": [row, col], "kernel_estimate": estimate}
                entry_bytes = _canonical(entry)
                total_entry_bytes += len(entry_bytes)+len(key)
                total_entry_workspace += _json_workspace(entry_bytes)+len(key)
                if total_entry_bytes > MAX_PLAN_BYTES or total_entry_workspace > MAX_PLAN_EXPANDED_BYTES:
                    raise ValueError("Exact causal stack registry exceeds its bounded plan storage. No approximate deduplication is permitted.")
                lookup[key] = index
                stack_table.append(entry)
            class_index[row, col] = index
        del paths
    features, warnings = _features(twin, roi, dx, dy, a.include_defects)
    metadata = {"path_model": a.path_model, "path_contract_version": PATH_CONTRACT_VERSION,
        "response_model": RESPONSE_MODEL, "excitation_model": EXCITATION_MODEL,
        "observation_model": a.observation_model, "certificate_version": CERTIFICATE_VERSION,
        "coordinate_units": {"x": "mm", "y": "mm", "time": "us"},
        "rf_unit": "relative signed pressure", "imaginary_unit": "relative quadrature pressure",
        "envelope_unit": "relative complex-pressure magnitude", "error_bound_unit": "absolute relative pressure",
        "time_zero": "causal gamma onset at the transducer reference; lossless incident-medium standoff adds round-trip delay, and gamma peak is later than onset",
        "envelope_processing": "magnitude of full coherent complex gamma response; independently certified returned double, not a Hilbert transform of real RF",
        "depth_mapping_supported": False, "depth_samples_used": False, "grid_shape": None,
        "padded_shape_yx": [ny, nx], "halo_pixels_yx": [0, 0], "focus_model": "none",
        "material_model": "frozen nominal positive scalar impedances/speeds; every finite layer lossless; water exteriors and lossless standoff",
        "resolved_materials": {identifier: _resolved_medium(identifier) for identifier in ("water", *MATERIAL_IDS)},
        "path_diagnostics": path_diagnostics, "geometry_path_sha256": geometry_signatures,
        "microfeature_sampling": features, "warnings": warnings,
        "assumptions": ["Independent unfocused scalar normal-incidence columns, complete specimen depth and original primitive precedence; no lateral mixing or calibrated beam.",
            "No refraction, shear, full elastic scattering, focus weighting, time apodization or legacy frequency-loss law.",
            "Exact represented resolved stacks/pulse/time inputs determine reuse; geometric labels/endpoints retain separate signatures.",
            "The saved numerical bound excludes primitive intersection rounding, geometric/material uncertainty, spatial discretization and measured-instrument error.",
            "Repeated returns and gamma excitation latency do not identify unique reflection depths; ordinary depth mapping is unsupported.",
            "Identity observation copies certified float64 outputs. Time gates are later processing, with no re-excitation or envelope reconstruction."]}
    estimate = {"schema_version": 1, "kind": request.kind, "model_version": MODEL_VERSION,
        "kernel_model_version": KERNEL_VERSION, "shape": [ny, nx, nt], "axis_order": ["y", "x", "time"],
        "dtype": "float64", "tile_rows": 1, "total_rows": ny, "chunks": [1, nx, nt],
        "time_samples": nt, "column_count": nx*ny, "unique_stack_count": len(stack_table),
        "unique_geometry_path_count": len(geometry_classes), "inverse_work_units": total_inverse,
        "layer_frequency_work_units": total_layer_work, "path_candidate_tests": candidate_tests,
        "path_event_work_units": event_work, "path_workspace_bytes": path_workspace,
        "signal_bytes": signal_bytes, "map_bytes": map_bytes, "coordinate_bytes": coordinate_bytes,
        "estimated_temporary_bytes": 0, "extent_mm": [roi[0], roi[2], roi[1], roi[3]],
        "roi_mm": roi, "pixel_pitch_um": [1000*dx, 1000*dy],
        "time_start_us": time_list[0], "time_end_us": time_list[-1],
        "requested_record_end_us": a.record_start_us+a.record_duration_us,
        "rf_sample_interval_us": 1/a.sample_rate_mhz, "path_model": a.path_model,
        "response_model": RESPONSE_MODEL, "excitation_model": EXCITATION_MODEL,
        "observation_model": a.observation_model, "certificate_version": CERTIFICATE_VERSION,
        "requested_tolerance": a.absolute_tolerance, "precision_bits": a.precision_bits,
        "stack_table": stack_table, "class_index": class_index.tolist(),
        "x_mm": x.tolist(), "y_mm": y.tolist(), "time_us": time_list, "metadata": metadata}
    plan_bytes, plan_workspace = _admit_json(estimate, "Frozen causal plan")
    cache_bytes = len(stack_table)*(3*nt*8+64*1024)
    row_bytes = 3*nx*nt*8+nx*8
    peak = (32*1024**2+4*request_workspace+4*plan_workspace+cache_bytes+
            max(path_workspace, kernel_peak+nt*192)+6*row_bytes)
    total = signal_bytes+map_bytes+coordinate_bytes+request_bytes+plan_bytes+len(stack_table)*64*1024+2*1024**2
    if peak > MAX_PEAK_BYTES or total > MAX_VOLUME_BYTES:
        raise ValueError("Causal volume cache/plan/row buffers exceed the 512 MiB working/output budget.")
    estimate.update(estimated_peak_bytes=peak, total_bytes=total, class_cache_bytes=cache_bytes,
                    plan_bytes=plan_bytes, plan_expanded_bytes=plan_workspace,
                    workspace_definition="Frozen request/plan Python copies, bounded exact-response cache, full-depth path blocks, one Arb solve, output rows and I/O copies; not process RSS.")
    # Recheck the small final scalar fields as part of the stored plan boundary.
    _admit_json(estimate, "Frozen causal plan")
    return PreparedCausalSam(request, estimate, metadata, x, y, time, class_index, stack_table, pulse)


def estimate_causal_sam(request):
    return _compile(request).estimate


def prepare_causal_sam(request):
    return _compile(request)


def _cache_record(value, prepared, index):
    diagnostics = {k: v for k, v in value["diagnostics"].items() if k not in OPERATIONAL_DIAGNOSTICS}
    bound = diagnostics.get("total_error_bound")
    if (isinstance(bound, bool) or not isinstance(bound, (int, float)) or not np.isfinite(bound)
            or not 0 <= bound <= prepared.request.acquisition.absolute_tolerance):
        raise ValueError("Cached causal class has an invalid error certificate.")
    if diagnostics.get("model_version") != KERNEL_VERSION:
        raise ValueError("Cached causal class has the wrong kernel identity.")
    expected = prepared.stack_table[index]["kernel_estimate"]
    for key in ("frequency_terms", "time_samples", "period_us", "precision_bits", "gamma_order",
                "requested_tolerance", "analytic_alias_bound", "frequency_cutoff_bound"):
        if diagnostics.get(key) != expected[key]:
            raise ValueError(f"Cached causal class has incompatible {key}.")
    record = {}
    for name in SIGNALS:
        array = np.asarray(value[name])
        if array.dtype != np.dtype("float64") or array.shape != (len(prepared.time_us),) or not np.isfinite(array).all():
            raise ValueError(f"Cached causal {name} must preserve finite float64 saved time samples.")
        if name == "envelope" and np.any(array < 0):
            raise ValueError("Cached causal magnitude cannot be negative.")
        record[name] = array
    record["diagnostics"] = diagnostics
    return record


def iter_causal_sam_rows(prepared, start_row=0, skip_rows=(), cancelled=None):
    ny, nx, nt = prepared.estimate["shape"]
    if type(start_row) is not int or not 0 <= start_row <= ny:
        raise ValueError("Causal resume row must be an integer canonical row boundary.")
    skipped = set(skip_rows)
    if any(type(row) is not int or not 0 <= row < ny for row in skipped):
        raise ValueError("Skipped causal rows must be integer indices inside the recording.")
    if cancelled is not None and not callable(cancelled):
        raise ValueError("Cancellation must be a callback when supplied.")
    for row in range(start_row, ny):
        if row in skipped:
            continue
        if cancelled is not None and cancelled():
            return
        classes = sorted(map(int, np.unique(prepared.class_index[row])))
        for index in classes:
            if cancelled is not None and cancelled():
                return
            if index not in prepared.cache:
                value = causal_gamma_response(prepared.stack_table[index]["stack"],
                                               prepared.time_us.tolist(), prepared.pulse_settings)
                if cancelled is not None and cancelled():
                    return
                prepared.cache[index] = _cache_record(value, prepared, index)
            else:
                prepared.cache[index] = _cache_record(prepared.cache[index], prepared, index)
        products = {name: np.empty((1, nx, nt), dtype=np.float64) for name in SIGNALS}
        products["error_bound"] = np.empty((1, nx), dtype=np.float64)
        for col, index in enumerate(prepared.class_index[row]):
            cached = prepared.cache[int(index)]
            for name in SIGNALS:
                products[name][0, col] = cached[name]
            products["error_bound"][0, col] = cached["diagnostics"]["total_error_bound"]
        if cancelled is not None and cancelled():
            return
        certificates = {str(index): prepared.cache[index]["diagnostics"] for index in classes}
        yield row, row+1, products, certificates
