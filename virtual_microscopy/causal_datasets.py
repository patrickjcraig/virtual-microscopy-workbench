"""Typed, immutable causal-column volumes and atomic signal/certificate commits.

Completed readers use frozen identities and never import the forward solver.
Only resume checks the current numerical implementation and native FLINT build.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import platform

import numpy as np
import zarr

from .datasets import (DatasetStore, atomic_json, canonical_json, checked_id,
                       check_disk_space, json_sha256, material_snapshot, now_iso)


KIND = "sam_causal_rf_volume"
CERTIFICATE_CONTRACT = "causal-column-certificate-1"
MAX_MANIFEST_BYTES = 8 * 1024**2
MAX_EXPANDED_BYTES = 32 * 1024**2
MAX_VOLUME_BYTES = MAX_PEAK_BYTES = 512 * 1024**2
MAX_CLASS_CERTIFICATE_BYTES = 64 * 1024
SIGNALS = ("rf", "imaginary", "envelope")
PRODUCTS = (*SIGNALS, "error_bound")
COORDINATES = ("x_mm", "y_mm", "time_us")
CODECS = [{"name": "bytes", "configuration": {"endian": "little"}}]
IMPLEMENTATION_FILES = ("causal_datasets.py", "causal_sam_schemas.py", "causal_sam.py",
    "column_paths.py", "layered_time.py", "layered_schemas.py", "volume_jobs.py",
    "schemas.py", "hbm.py", "materials.py", "datasets.py")


def typed_sha256(value, dtype="<f8"):
    """Hash actual canonical typed bytes, without the legacy float32 conversion."""
    return hashlib.sha256(np.ascontiguousarray(value, dtype=dtype).tobytes()).hexdigest()


def _workspace(payload):
    return len(payload)*8 + 256*sum(payload.count(token) for token in (b"{", b"[", b",", b":"))


def bounded_payload(value):
    output = io.BytesIO()
    size = expanded = 0
    try:
        for token in json.JSONEncoder(allow_nan=False, ensure_ascii=True, sort_keys=True,
                                      separators=(",", ":")).iterencode(value):
            if len(token) > MAX_MANIFEST_BYTES:
                raise ValueError("Causal manifest exceeds its bounded JSON limit.")
            part = token.encode("utf8")
            size += len(part)
            expanded += _workspace(part)
            if size > MAX_MANIFEST_BYTES or expanded > MAX_EXPANDED_BYTES:
                raise ValueError("Causal manifest exceeds its bounded JSON/expanded-memory limit.")
            output.write(part)
    except (TypeError, RecursionError, OverflowError) as exc:
        raise ValueError("Causal manifest requires finite bounded JSON.") from exc
    return output.getvalue()


def _read_json(path, byte_limit=MAX_MANIFEST_BYTES, expanded_limit=MAX_EXPANDED_BYTES):
    if path.stat().st_size > byte_limit:
        raise ValueError("Causal metadata exceeds its bounded JSON limit.")
    with path.open("rb") as stream:
        payload = stream.read(byte_limit+1)
    if len(payload) > byte_limit or _workspace(payload) > expanded_limit:
        raise ValueError("Causal metadata exceeds its bounded JSON/expanded-memory limit.")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate causal metadata key.")
            result[key] = value
        return result
    def nonfinite(value):
        raise ValueError("Nonfinite causal metadata value.")
    try:
        return json.loads(payload, object_pairs_hook=pairs, parse_constant=nonfinite)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Causal metadata requires finite bounded JSON.") from exc


def causal_solver_identity(model_version):
    import flint  # Only called during creation/resume, never historical reads.
    files = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
             for name in IMPLEMENTATION_FILES}
    return {"model_version": model_version, "certificate_contract": CERTIFICATE_CONTRACT,
        "source_sha256": files, "numerical_packages": {
            name: importlib.metadata.version(name) for name in ("numpy", "zarr", "python-flint")},
        "runtime": {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "system": platform.system(), "machine": platform.machine(),
            "flint": flint.__FLINT_VERSION__, "flint_release": flint.__FLINT_RELEASE__}}


def _positive_int(value, low, high, description):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"Invalid bounded causal {description}.")
    return value


def _descriptors(shape):
    ny, nx, nt = shape
    result = {name: {"path": name, "shape": shape, "chunks": [1, nx, nt], "dtype": "float64",
        "axes": ["y", "x", "time"], "units": unit} for name, unit in (
            ("rf", "relative signed pressure"), ("imaginary", "relative quadrature pressure"),
            ("envelope", "relative echo amplitude"))}
    result["error_bound"] = {"path": "error_bound", "shape": [ny, nx], "chunks": [1, nx],
        "dtype": "float64", "axes": ["y", "x"], "units": "absolute normalized pressure error bound"}
    result["class_index"] = {"path": "class_index", "shape": [ny, nx], "chunks": [ny, nx],
        "dtype": "uint16", "axes": ["y", "x"], "units": "exact stack class index"}
    for name, axis, size, unit in (("x_mm", "x", nx, "mm"), ("y_mm", "y", ny, "mm"),
                                 ("time_us", "time", nt, "us")):
        result[name] = {"path": name, "shape": [size], "chunks": [size], "dtype": "float64",
                        "axes": [axis], "units": unit}
    return result


def _plan(estimate):
    if not isinstance(estimate, dict) or estimate.get("kind") != KIND:
        raise ValueError("A causal volume requires its explicit causal estimate.")
    shape = estimate.get("shape")
    if not isinstance(shape, list) or len(shape) != 3:
        raise ValueError("Causal shape must be [y,x,time].")
    ny, nx, nt = (_positive_int(shape[0], 16, 64, "rows"), _positive_int(shape[1], 16, 64, "columns"),
                  _positive_int(shape[2], 2, 2049, "time samples"))
    if estimate.get("tile_rows") != 1 or estimate.get("total_rows", ny) != ny:
        raise ValueError("Causal acquisitions require one-row canonical commits.")
    total = _positive_int(estimate.get("total_bytes"), 1, MAX_VOLUME_BYTES, "output bytes")
    _positive_int(estimate.get("estimated_peak_bytes"), 1, MAX_PEAK_BYTES, "peak bytes")
    minimum = 3*ny*nx*nt*8 + ny*nx*10 + (ny+nx+nt)*8
    if total < minimum:
        raise ValueError("Causal estimate undercounts float64 signals, maps or coordinates.")
    classes = estimate.get("stack_table")
    if not isinstance(classes, list) or not 1 <= len(classes) <= ny*nx:
        raise ValueError("Invalid bounded causal stack table.")
    for index, entry in enumerate(classes):
        if (not isinstance(entry, dict) or entry.get("class_id") != index or
                not isinstance(entry.get("stack"), dict) or
                not isinstance(entry.get("response_sha256"), str) or len(entry["response_sha256"]) != 64 or
                not isinstance(entry.get("kernel_estimate"), dict)):
            raise ValueError("Invalid frozen causal stack class.")
        kernel = entry["kernel_estimate"]
        _positive_int(kernel.get("frequency_terms"), 1, 16385, "kernel frequency count")
        _positive_int(kernel.get("inverse_work_units"), 1, 25_000_000, "kernel inverse work")
        _positive_int(kernel.get("layer_frequency_work_units"), 1, 5_000_000, "kernel layer work")
        if not isinstance(entry["stack"].get("layers"), list) or len(entry["stack"]["layers"]) > 256:
            raise ValueError("A causal stack exceeds the 256-layer limit.")
    for key, limit in (("inverse_work_units", 100_000_000), ("layer_frequency_work_units", 5_000_000)):
        value = _positive_int(estimate.get(key), 1, limit, key)
        if value != sum(entry["kernel_estimate"][key] for entry in classes):
            raise ValueError(f"Causal aggregate {key} differs from its exact-class plan.")
    raw_map = estimate.get("class_index")
    if (not isinstance(raw_map, list) or len(raw_map) != ny or
        any(not isinstance(row, list) or len(row) != nx or
            any(type(i) is not int or not 0 <= i < len(classes) for i in row) for row in raw_map)):
        raise ValueError("Invalid causal class map.")
    index_map = np.asarray(raw_map, dtype="<u2")
    if set(np.unique(index_map).tolist()) != set(range(len(classes))):
        raise ValueError("Every frozen causal class must have a sampled column.")
    for index, entry in enumerate(classes):
        representative = entry.get("representative_yx")
        if (not isinstance(representative, list) or len(representative) != 2 or
                any(type(i) is not int for i in representative) or
                not 0 <= representative[0] < ny or not 0 <= representative[1] < nx or
                int(index_map[tuple(representative)]) != index):
            raise ValueError("Invalid causal class representative.")
    coords = {}
    for name, length in (("x_mm", nx), ("y_mm", ny), ("time_us", nt)):
        raw = estimate.get(name)
        if not isinstance(raw, list) or len(raw) != length:
            raise ValueError(f"Invalid frozen {name} coordinates.")
        values = np.asarray(raw, dtype="<f8")
        if (not np.isfinite(values).all() or np.any(np.diff(values) <= 0) or
                np.any(values < 0) or np.any(values > (12 if name == "time_us" else 100))):
            raise ValueError(f"Invalid frozen {name} coordinates.")
        coords[name] = values
    return shape, index_map, coords


class CausalSamDatasetStore(DatasetStore):
    kind = KIND
    axis_order = ("y", "x", "time")
    signal_units = {name: value["units"] for name, value in _descriptors([16, 16, 2]).items() if name in PRODUCTS}
    evidence_status = "Synthetic scalar causal column responses with numerical enclosures; independent unfocused columns, not calibrated measurements or unique reflection depths."

    def path(self, identifier):
        target = self.root / checked_id(identifier)
        if target.is_symlink() or target.is_junction() or target.resolve().parent != self.root:
            raise ValueError("Causal dataset paths cannot contain links or junctions.")
        return target

    def manifest(self, identifier):
        path = self.path(identifier) / "manifest.json"
        if path.is_symlink() or path.is_junction():
            raise ValueError("Causal manifest paths cannot contain links or junctions.")
        if not path.is_file():
            raise KeyError(identifier)
        value = _read_json(path)
        if not isinstance(value, dict) or value.get("dataset_id") != identifier or value.get("kind") != KIND:
            raise ValueError("Causal manifest identity or kind mismatch.")
        required = {"schema_version", "certificate_contract", "request", "request_sha256", "materials",
            "materials_sha256", "estimate", "estimate_sha256", "solver", "shape", "axis_order", "dtype",
            "store", "zarr_format", "tile_rows", "total_rows", "completed_rows", "completed_chunks",
            "class_certificates", "metadata", "coordinates_sha256", "arrays", "input_sha256", "evidence_status",
            "state", "complete", "arrays_initialized"}
        if not required.issubset(value) or type(value["complete"]) is not bool or type(value["arrays_initialized"]) is not bool:
            raise ValueError("Causal manifest has missing or invalid contract fields.")
        for name in ("request", "materials", "estimate", "solver", "arrays", "completed_chunks", "class_certificates", "metadata"):
            if not isinstance(value[name], dict):
                raise ValueError(f"Invalid causal manifest {name} record.")
        if value["arrays_initialized"] and not {"metadata_sha256", "class_index_sha256", "initialization_sha256"}.issubset(value):
            raise ValueError("Causal initialization registry is incomplete.")
        return value

    @staticmethod
    def _input_payload(m):
        return {key: m[key] for key in ("kind", "schema_version", "certificate_contract", "request_sha256",
            "materials_sha256", "estimate_sha256", "solver", "arrays", "shape", "axis_order", "dtype",
            "evidence_status", "tile_rows", "total_rows", "zarr_format", "store")}

    @staticmethod
    def _required_output(m, future=True):
        """All owned output, including bounded future publication records.

        The engine reserves 64 KiB per class and 2 MiB of general overhead in
        addition to request/plan and numeric bytes. This reader enforces that
        those advertised bytes really cover its copied metadata and registries.
        """
        numeric = sum(math.prod(d["shape"])*np.dtype(d["dtype"]).itemsize for d in m["arrays"].values())
        publication = len(bounded_payload(m))
        if future:
            publication += 2048  # initialization/completion keys and timestamps
            if not m["arrays_initialized"]:
                publication += len(bounded_payload(m["estimate"]["metadata"]))
            publication += (len(m["estimate"]["stack_table"])-len(m["class_certificates"]))*MAX_CLASS_CERTIFICATE_BYTES
            publication += (m["total_rows"]-len(m["completed_chunks"]))* (1024+96*m["shape"][1])
        # The canonical uncompressed writer needs at most nine small JSON
        # headers. Their read guard enforces this ceiling, including attributes.
        required = numeric+publication+(1+len(m["arrays"]))*16384
        if required > m["estimate"]["total_bytes"]:
            raise ValueError("Advertised causal output does not cover its data, metadata and certificate registries.")
        return required

    def create(self, identifier, request, estimate):
        # Admit bounded JSON before any cloning, hash encoding or array allocation.
        frozen = json.loads(bounded_payload({"request": request, "estimate": estimate}))
        request, estimate = frozen["request"], frozen["estimate"]
        if request.get("kind") != KIND:
            raise ValueError("A causal volume requires its explicit request kind.")
        shape, _, _ = _plan(estimate)
        check_disk_space(self.root, estimate["total_bytes"])
        materials = material_snapshot()
        timestamp = now_iso()
        m = {"dataset_id": checked_id(identifier), "kind": KIND, "schema_version": 1,
            "certificate_contract": CERTIFICATE_CONTRACT, "created_at": timestamp, "updated_at": timestamp,
            "state": "queued", "complete": False, "arrays_initialized": False, "evidence_status": self.evidence_status,
            "request": request, "request_sha256": json_sha256(request), "materials": materials,
            "materials_sha256": json_sha256(materials), "estimate": estimate, "estimate_sha256": json_sha256(estimate),
            "solver": causal_solver_identity(estimate["model_version"]), "shape": shape,
            "axis_order": list(self.axis_order), "dtype": "float64", "zarr_format": 3, "store": "data.zarr",
            "tile_rows": 1, "total_rows": shape[0], "completed_rows": 0, "completed_chunks": {},
            "class_certificates": {}, "metadata": {}, "coordinates_sha256": {}, "arrays": _descriptors(shape)}
        m["input_sha256"] = json_sha256(self._input_payload(m))
        bounded_payload(m)
        self._required_output(m)
        self.path(identifier).mkdir(exist_ok=False)
        atomic_json(self.path(identifier)/"manifest.json", m)
        return m

    def save(self, identifier, manifest):
        old = self.manifest(identifier)
        if old["complete"]:
            raise ValueError("Completed causal datasets are immutable.")
        mutable = {"state", "error", "updated_at", "complete", "completed_at", "completed_rows", "completed_chunks",
            "class_certificates", "total_error_bound", "completion_sha256"}
        if not old["arrays_initialized"]:
            mutable |= {"arrays_initialized", "metadata", "metadata_sha256", "coordinates_sha256",
                        "class_index_sha256", "initialization_sha256"}
        for key in set(old) | set(manifest):
            if key not in mutable and old.get(key) != manifest.get(key):
                raise ValueError(f"Frozen causal dataset field cannot change: {key}.")
        for key, certificate in old["class_certificates"].items():
            if manifest["class_certificates"].get(key) != certificate:
                raise ValueError("Committed causal class certificates are immutable.")
        for key, chunk in old["completed_chunks"].items():
            if key in manifest["completed_chunks"] and manifest["completed_chunks"][key] != chunk:
                raise ValueError("Committed causal rows cannot be overwritten.")
        manifest["updated_at"] = now_iso()
        bounded_payload(manifest)
        self._required_output(manifest, future=not manifest["complete"])
        atomic_json(self.path(identifier)/"manifest.json", manifest)

    def _frozen(self, m):
        if (m.get("schema_version") != 1 or m.get("certificate_contract") != CERTIFICATE_CONTRACT or
                m.get("dtype") != "float64" or m.get("axis_order") != list(self.axis_order) or
                m.get("store") != "data.zarr" or m.get("zarr_format") != 3):
            raise ValueError("Unsupported causal dataset contract.")
        for value, digest in (("request", "request_sha256"), ("estimate", "estimate_sha256"),
                              ("materials", "materials_sha256")):
            if json_sha256(m[value]) != m[digest]:
                raise ValueError(f"Frozen causal {value} checksum mismatch.")
        if json_sha256(self._input_payload(m)) != m["input_sha256"]:
            raise ValueError("Causal dataset input identity checksum mismatch.")
        shape, index_map, coords = _plan(m["estimate"])
        if (m["shape"] != shape or m["arrays"] != _descriptors(shape) or m["total_rows"] != shape[0] or
                m["tile_rows"] != 1 or type(m["completed_rows"]) is not int or not 0 <= m["completed_rows"] <= shape[0]):
            raise ValueError("Causal dataset descriptor or layout mismatch.")
        return index_map, coords

    def validate_identity(self, identifier):
        m = self.manifest(identifier)
        self._frozen(m)
        if m["solver"] != causal_solver_identity(m["solver"]["model_version"]):
            raise ValueError("Causal solver or numerical runtime changed; create a new dataset instead of resuming.")
        if json_sha256(material_snapshot()) != m["materials_sha256"]:
            raise ValueError("Causal material library changed; create a new dataset instead of resuming.")
        return m

    @staticmethod
    def _initialization_payload(m):
        return {key: m[key] for key in ("input_sha256", "coordinates_sha256", "class_index_sha256", "metadata_sha256")}

    def initialize_arrays(self, identifier, prepared):
        m = self.validate_identity(identifier)
        if m["complete"]:
            raise ValueError("Completed causal datasets are immutable.")
        index_map, coords = self._frozen(m)
        if prepared.estimate != m["estimate"] or prepared.stack_table != m["estimate"]["stack_table"]:
            raise ValueError("Prepared causal plan differs from its frozen acquisition.")
        for name, values in {**coords, "class_index": index_map}.items():
            actual = np.asarray(getattr(prepared, name))
            if actual.dtype != values.dtype or actual.shape != values.shape or actual.tobytes() != values.tobytes():
                raise ValueError(f"Prepared causal {name} differs from frozen coordinates/classes.")
        metadata = json.loads(bounded_payload(prepared.metadata))
        if metadata != m["estimate"]["metadata"]:
            raise ValueError("Prepared causal metadata differs from its frozen plan.")
        if m["arrays_initialized"]:
            if metadata != m["metadata"]:
                raise ValueError("Prepared causal metadata differs from its frozen acquisition.")
            self._open_checked(identifier, m)
            return m
        if m["completed_chunks"] or m["class_certificates"]:
            raise ValueError("Uninitialized causal dataset contains committed data.")
        target = self.path(identifier)/"data.zarr"
        self._reject_tree_links(target)
        group = zarr.open_group(str(target), mode="w", zarr_format=3)
        group.attrs.update({"dataset_id": identifier, "kind": KIND, "input_sha256": m["input_sha256"],
                            "certificate_contract": CERTIFICATE_CONTRACT})
        for name, descriptor in m["arrays"].items():
            values = index_map if name == "class_index" else coords.get(name)
            kwargs = {"chunks": tuple(descriptor["chunks"]), "compressors": None,
                      "dimension_names": tuple(descriptor["axes"])}
            if values is None:
                group.create_array(name, shape=tuple(descriptor["shape"]), dtype="<f8", fill_value=float("nan"), **kwargs)
            else:
                group.create_array(name, data=values, **kwargs)
        m.update(arrays_initialized=True, metadata=metadata, metadata_sha256=json_sha256(metadata),
            coordinates_sha256={name: typed_sha256(values) for name, values in coords.items()},
            class_index_sha256=typed_sha256(index_map, "<u2"))
        m["initialization_sha256"] = json_sha256(self._initialization_payload(m))
        self.save(identifier, m)
        self._open_checked(identifier, m)
        return m

    @staticmethod
    def _reject_tree_links(target):
        if target.is_symlink() or target.is_junction():
            raise ValueError("Causal data paths cannot contain links or junctions.")
        if not target.exists():
            return
        count = 0
        root = target.resolve()
        for directory, directories, files in os.walk(target, followlinks=False):
            for name in directories + files:
                child = Path(directory)/name
                count += 1
                if count > 4096:
                    raise ValueError("Causal store contains too many filesystem entries.")
                if child.is_symlink() or child.is_junction() or not child.resolve().is_relative_to(root):
                    raise ValueError("Causal data paths cannot contain links or junctions.")

    def _open_checked(self, identifier, m, mode="r"):
        if not m["arrays_initialized"]:
            raise ValueError("Causal arrays have not been initialized.")
        if mode != "r" and m["complete"]:
            raise ValueError("Completed causal datasets are immutable.")
        index_map, coords = self._frozen(m)
        if (json_sha256(m["metadata"]) != m["metadata_sha256"] or
                json_sha256(self._initialization_payload(m)) != m["initialization_sha256"]):
            raise ValueError("Causal initialization metadata checksum mismatch.")
        target = self.path(identifier)/"data.zarr"
        self._reject_tree_links(target)
        root_metadata = _read_json(target/"zarr.json", 16384, 262144)
        if (root_metadata.get("zarr_format") != 3 or root_metadata.get("node_type") != "group" or
            root_metadata.get("consolidated_metadata") is not None or root_metadata.get("attributes") != {
                "dataset_id": identifier, "kind": KIND, "input_sha256": m["input_sha256"],
                "certificate_contract": CERTIFICATE_CONTRACT}):
            raise ValueError("Causal Zarr group identity mismatch.")
        if {p.name for p in target.iterdir()} != {"zarr.json", *m["arrays"]}:
            raise ValueError("Causal Zarr array registry mismatch.")
        # Inspect raw metadata BEFORE Zarr can construct a decoder or allocate a chunk.
        for name, d in m["arrays"].items():
            raw = _read_json(target/name/"zarr.json", 16384, 262144)
            if (raw.get("shape") != d["shape"] or raw.get("data_type") != d["dtype"] or
                raw.get("chunk_grid") != {"name": "regular", "configuration": {"chunk_shape": d["chunks"]}} or
                raw.get("chunk_key_encoding") != {"name": "default", "configuration": {"separator": "/"}} or
                raw.get("codecs") != CODECS or raw.get("dimension_names") != d["axes"] or
                raw.get("zarr_format") != 3 or raw.get("node_type") != "array" or
                raw.get("storage_transformers", []) != [] or raw.get("attributes") != {} or
                raw.get("fill_value") != ("NaN" if name in PRODUCTS else 0)):
                raise ValueError(f"Noncanonical causal array metadata: {name}.")
        group = zarr.open_group(str(target), mode=mode, use_consolidated=False)
        if set(m["coordinates_sha256"]) != set(COORDINATES):
            raise ValueError("Causal coordinate checksum registry is incomplete.")
        for name, expected in {**coords, "class_index": index_map}.items():
            self._chunk_size(identifier, m, name, 0)
            actual = group[name][:]
            dtype = "<u2" if name == "class_index" else "<f8"
            digest = m["class_index_sha256"] if name == "class_index" else m["coordinates_sha256"][name]
            if typed_sha256(actual, dtype) != digest or actual.tobytes() != expected.tobytes():
                raise ValueError(f"Causal {name} coordinate/class checksum mismatch.")
        return group

    def open_arrays(self, identifier, mode="r"):
        if mode not in {"r", "r+"}:
            raise ValueError("Only existing causal dataset reads and resumable writes are allowed.")
        return self._open_checked(identifier, self.manifest(identifier), mode)

    def _chunk_size(self, identifier, m, name, row):
        d = m["arrays"][name]
        indices = [str(row if name in PRODUCTS else 0)] + ["0"]*(len(d["shape"])-1)
        path = self.path(identifier)/"data.zarr"/name/"c"
        for index in indices:
            path /= index
        # Zarr legitimately omits all-fill chunks (e.g. an all-zero class map).
        if path.exists() and path.stat().st_size != math.prod(d["chunks"])*np.dtype(d["dtype"]).itemsize:
            raise ValueError(f"Invalid canonical causal chunk byte size: {name} row {row}.")

    @staticmethod
    def commit_bytes(manifest, y0, y1):
        if type(y0) is not int or type(y1) is not int or not 0 <= y0 < manifest["shape"][0] or y1 != y0+1:
            raise ValueError("A causal commit requires exactly one valid row.")
        return manifest["shape"][1] * (3*manifest["shape"][2]*8 + 8)

    @staticmethod
    def _pulse(m):
        return m["request"]["acquisition"]

    def _certificate(self, m, class_id, diagnostics, waveform_sha256):
        d = json.loads(bounded_payload(diagnostics))
        settings = self._pulse(m)
        components = ("analytic_alias_bound", "frequency_cutoff_bound", "arithmetic_complex_bound", "arithmetic_envelope_bound")
        for key in (*components, "total_error_bound"):
            value = d.get(key)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid causal certificate bound: {key}.")
        total = float(d["total_error_bound"])
        if (d.get("requested_tolerance") != settings["absolute_tolerance"] or
            d.get("precision_bits") != settings["precision_bits"] or total > settings["absolute_tolerance"]):
            raise ValueError("Causal certificate does not meet the frozen tolerance/precision.")
        # Components are independently outward-rounded displays; their encoded
        # sum may exceed the independently encoded total by a few ULPs.
        exact_sum = sum((Fraction(float(d[k])) for k in components), Fraction())
        slack = 8*max(math.ulp(float(d[k])) for k in (*components, "total_error_bound"))
        if any(d[k] > total for k in components) or exact_sum > Fraction(total)+Fraction(slack):
            raise ValueError("Causal certificate component/total bounds disagree.")
        entry = m["estimate"]["stack_table"][class_id]
        # These values are deterministic outputs of the admitted kernel plan.
        # The arithmetic enclosure is only known after solving, but its analytic
        # components cannot be replaced with smaller values at publication.
        planned = ("model_version", "frequency_terms", "period_us", "laplace_damping_per_us",
            "gamma_order", "time_samples", "precision_bits", "requested_tolerance",
            "layer_frequency_work_units", "inverse_work_units", "estimated_peak_bytes",
            "analytic_alias_bound", "frequency_cutoff_bound")
        for key in planned:
            if key not in entry["kernel_estimate"] or key not in d or d[key] != entry["kernel_estimate"][key]:
                raise ValueError(f"Causal certificate differs from its admitted kernel plan: {key}.")
        if d["model_version"] != m["estimate"].get("kernel_model_version"):
            raise ValueError("Causal certificate kernel version differs from its frozen acquisition.")
        record = {"diagnostics": d, "diagnostics_sha256": json_sha256(d),
                  "response_sha256": entry["response_sha256"], "waveform_sha256": waveform_sha256}
        record["certificate_sha256"] = json_sha256(record)
        if len(bounded_payload(record)) > MAX_CLASS_CERTIFICATE_BYTES:
            raise ValueError("Causal class certificate exceeds its reserved 64 KiB output budget.")
        return record

    def _certificates(self, m):
        registry = m.get("class_certificates")
        if not isinstance(registry, dict) or len(registry) > len(m["estimate"]["stack_table"]):
            raise ValueError("Invalid causal class certificate registry.")
        for key, record in registry.items():
            if not key.isdecimal() or str(int(key)) != key or not 0 <= int(key) < len(m["estimate"]["stack_table"]):
                raise ValueError("Invalid causal certificate class identifier.")
            if not isinstance(record, dict) or not isinstance(record.get("diagnostics"), dict):
                raise ValueError("Invalid causal class certificate record.")
            hashes = record.get("waveform_sha256", {})
            if set(hashes) != set(SIGNALS) or any(not isinstance(v, str) or len(v) != 64 for v in hashes.values()):
                raise ValueError("Causal class waveform checksum registry is incomplete.")
            if self._certificate(m, int(key), record["diagnostics"], hashes) != record:
                raise ValueError("Causal class certificate checksum mismatch.")

    def _read_row(self, identifier, m, group, y):
        for name in PRODUCTS:
            self._chunk_size(identifier, m, name, y)
        products = {name: group[name][y:y+1] for name in PRODUCTS}
        self._validate_products(m, y, products)
        return products

    def _validate_products(self, m, y, products):
        nx, nt = m["shape"][1:]
        if set(products) != set(PRODUCTS):
            raise ValueError("Causal rows require real, imaginary, envelope and error-bound products.")
        for name, value in products.items():
            a = np.asarray(value)
            shape = (1, nx) if name == "error_bound" else (1, nx, nt)
            if a.dtype != np.dtype("float64") or a.shape != shape or not np.isfinite(a).all():
                raise ValueError(f"Causal {name} must contain finite float64 values with canonical row shape.")
        if np.any(products["envelope"] < 0) or np.any(products["error_bound"] < 0):
            raise ValueError("Causal magnitude and error bounds must be nonnegative.")
        if np.any(products["error_bound"] > self._pulse(m)["absolute_tolerance"]):
            raise ValueError("Causal row exceeds its requested numerical error tolerance.")
        magnitude = np.hypot(products["rf"], products["imaginary"])
        allowance = 2*products["error_bound"][:, :, None] + 32*np.finfo(float).eps*np.maximum(1., magnitude)
        if not np.isfinite(magnitude).all() or np.any(np.abs(products["envelope"]-magnitude) > allowance):
            raise ValueError("Causal saved envelope is inconsistent with its complex pressure and bounds.")

    def _row_matches(self, m, y, products, registry):
        classes = m["estimate"]["class_index"][y]
        for x, class_id in enumerate(classes):
            record = registry.get(str(class_id))
            if record is None or products["error_bound"][0, x] != record["diagnostics"]["total_error_bound"]:
                raise ValueError("Causal row is missing its matching class certificate/error bound.")
            for name in SIGNALS:
                if typed_sha256(products[name][0, x]) != record["waveform_sha256"][name]:
                    raise ValueError("Causal class waveform differs between saved representative columns.")

    def write_row(self, identifier, y0, y1, products, certificates):
        m = self.manifest(identifier)
        if m["complete"]:
            raise ValueError("Completed causal datasets are immutable.")
        self.commit_bytes(m, y0, y1)
        if str(y0) in m["completed_chunks"]:
            raise ValueError("Committed causal rows must be verified and skipped.")
        group = self._open_checked(identifier, m, "r+")
        self._certificates(m)
        self._validate_products(m, y0, products)
        expected = {str(i) for i in m["estimate"]["class_index"][y0]}
        if not isinstance(certificates, dict) or set(certificates) != expected:
            raise ValueError("Causal row certificates must cover exactly its sampled classes.")
        registry = deepcopy(m["class_certificates"])
        for key in sorted(expected, key=int):
            x = m["estimate"]["class_index"][y0].index(int(key))
            hashes = {name: typed_sha256(products[name][0, x]) for name in SIGNALS}
            record = self._certificate(m, int(key), certificates[key], hashes)
            if key in registry and registry[key] != record:
                raise ValueError("Causal regenerated class does not match its committed certificate/waveform.")
            registry[key] = record
        self._row_matches(m, y0, products, registry)
        checksums = {f"{name}_sha256": typed_sha256(value) for name, value in products.items()}
        chunk = {"rows": [y0, y1], **checksums,
                 "certificate_sha256": {key: registry[key]["certificate_sha256"] for key in sorted(expected)}}
        m["class_certificates"] = registry
        m["completed_chunks"][str(y0)] = chunk
        m["completed_rows"] = len(m["completed_chunks"])
        bounded_payload(m)  # Reject oversized publication before any signal write.
        self._required_output(m)
        check_disk_space(self.root, self.commit_bytes(m, y0, y1)+len(bounded_payload(m)))
        for name, values in products.items():
            group[name][y0:y1] = np.ascontiguousarray(values, dtype="<f8")
            if typed_sha256(group[name][y0:y1]) != checksums[f"{name}_sha256"]:
                raise OSError(f"Causal {name} chunk failed write verification.")
        self.save(identifier, m)
        return m

    def _verify_rows(self, identifier, m, group, repair):
        self._certificates(m)
        valid = {}
        registry = m["completed_chunks"]
        if not isinstance(registry, dict) or len(registry) > m["total_rows"]:
            raise ValueError("Invalid causal row completion registry.")
        for key, chunk in registry.items():
            if (not key.isdecimal() or str(int(key)) != key or not 0 <= int(key) < m["total_rows"] or
                    chunk.get("rows") != [int(key), int(key)+1]):
                raise ValueError("Invalid causal row completion registry.")
            y = int(key)
            expected = {str(i) for i in m["estimate"]["class_index"][y]}
            hashes = {k: m["class_certificates"].get(k, {}).get("certificate_sha256") for k in expected}
            if chunk.get("certificate_sha256") != hashes or any(v is None for v in hashes.values()):
                raise ValueError("Causal row certificate registry is inconsistent.")
            try:
                products = self._read_row(identifier, m, group, y)
                if any(typed_sha256(v) != chunk.get(f"{name}_sha256") for name, v in products.items()):
                    raise ValueError(f"Causal row {y} signal checksum mismatch.")
                self._row_matches(m, y, products, m["class_certificates"])
            except (OSError, ValueError, RuntimeError):
                if not repair:
                    raise
            else:
                valid[key] = chunk
        return valid

    def verify_chunks(self, identifier):
        m = self.validate_identity(identifier)
        if m["complete"]:
            raise ValueError("Completed causal datasets cannot be repaired in place.")
        if not m["arrays_initialized"]:
            return m
        group = self._open_checked(identifier, m)
        m["completed_chunks"] = self._verify_rows(identifier, m, group, True)
        m["completed_rows"] = len(m["completed_chunks"])
        self.save(identifier, m)
        return m

    @staticmethod
    def _completion_payload(m):
        return {key: m[key] for key in ("input_sha256", "initialization_sha256", "class_certificates",
                                       "completed_chunks", "total_error_bound")}

    def complete(self, identifier):
        m = self.verify_chunks(identifier)
        if m["completed_rows"] != m["total_rows"]:
            raise ValueError("Cannot complete a causal dataset with missing rows.")
        if set(m["class_certificates"]) != {str(i) for i in range(len(m["estimate"]["stack_table"]))}:
            raise ValueError("Cannot complete a causal dataset with missing class certificates.")
        m["total_error_bound"] = max(c["diagnostics"]["total_error_bound"] for c in m["class_certificates"].values())
        m["completion_sha256"] = json_sha256(self._completion_payload(m))
        m.update(complete=True, state="completed", error=None, completed_at=now_iso())
        self._required_output(m, future=False)
        self.save(identifier, m)
        return m

    def verify_complete(self, identifier):
        m = self.manifest(identifier)
        if not m["complete"] or m["state"] != "completed":
            raise ValueError("Only completed causal datasets can be viewed or exported.")
        group = self._open_checked(identifier, m)
        valid = self._verify_rows(identifier, m, group, False)
        if (len(valid) != m["total_rows"] or m["completed_rows"] != m["total_rows"] or
            set(m["class_certificates"]) != {str(i) for i in range(len(m["estimate"]["stack_table"]))} or
            m["total_error_bound"] != max(c["diagnostics"]["total_error_bound"] for c in m["class_certificates"].values()) or
            json_sha256(self._completion_payload(m)) != m.get("completion_sha256")):
            raise ValueError("Causal completion/certificate identity mismatch.")
        self._required_output(m, future=False)
        root = self.path(identifier)
        if {p.name for p in root.iterdir()} != {"manifest.json", "data.zarr"}:
            raise ValueError("Completed causal dataset has unexpected output files.")
        actual = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
        if actual > m["estimate"]["total_bytes"]:
            raise ValueError("Causal saved files exceed their advertised output budget.")
        return m

    def restore_class_cache(self, identifier):
        m = self.manifest(identifier)
        m = self.verify_complete(identifier) if m["complete"] else self.verify_chunks(identifier)
        group = self._open_checked(identifier, m)
        cache = {}
        for key in sorted(m["completed_chunks"], key=int):
            y = int(key)
            for x, class_id in enumerate(m["estimate"]["class_index"][y]):
                if class_id in cache:
                    continue
                cache[class_id] = {name: np.ascontiguousarray(group[name][y, x], dtype="<f8") for name in SIGNALS}
                cache[class_id]["diagnostics"] = deepcopy(m["class_certificates"][str(class_id)]["diagnostics"])
        return cache
