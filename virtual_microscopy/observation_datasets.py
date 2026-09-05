"""Typed immutable observation volumes with atomic signal/certificate rows."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
from importlib.metadata import version
import math
import os
from pathlib import Path
import platform
import threading

import numpy as np
import zarr

from .causal_comparison_store import bounded_json_read, bounded_payload, json_measure
from .causal_datasets import typed_sha256
from .datasets import atomic_json, checked_id, check_disk_space, now_iso
from .observation_plan import (KIND, MODEL_VERSION, ARITHMETIC_CONTRACT, MAX_BYTES,
    MAX_PLAN_BYTES, MAX_PLAN_EXPANDED_BYTES, guarded_root, measure, source_context, validate_plan)

SIGNALS = ("rf", "imaginary", "envelope")
BOUNDS = ("source_propagation", "complex_arithmetic", "complex_total", "magnitude_arithmetic", "magnitude_total")
PRODUCTS = (*SIGNALS, *BOUNDS)
COORDINATES = ("x_mm", "y_mm", "time_us")
CODECS = [{"name": "bytes", "configuration": {"endian": "little"}}]
CONTRACT = "coherent-observation-row-1"
IMPLEMENTATION_FILES = ("observation_plan.py", "observation_datasets.py", "observation_math.py",
    "observation_schemas.py", "observation_jobs.py", "batch_jobs.py", "causal_datasets.py",
    "causal_comparison_store.py", "datasets.py")
MAX_CACHE_ENTRIES = 1024
_row_cache = OrderedDict()
_cache_lock = threading.Lock()


def observation_identity():
    return {"model_version": MODEL_VERSION, "arithmetic_contract": ARITHMETIC_CONTRACT,
        "row_contract": CONTRACT,
        "source_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in IMPLEMENTATION_FILES},
        "runtime": {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "system": platform.system(), "machine": platform.machine(),
            "packages": {name: version(name) for name in ("numpy", "zarr")}}}


def _descriptors(shape):
    ny, nx, nt = shape
    units = {"rf": "relative signed pressure", "imaginary": "relative quadrature pressure",
             "envelope": "relative coherent pressure magnitude"}
    result = {name: {"shape": shape, "chunks": [1, nx, nt], "dtype": "float64", "axes": ["y", "x", "time"],
                     "units": units[name]} for name in SIGNALS}
    result.update({name: {"shape": [ny, nx], "chunks": [1, nx], "dtype": "float64", "axes": ["y", "x"],
                         "units": "absolute relative pressure error bound"} for name in BOUNDS})
    result.update({name: {"shape": [size], "chunks": [size], "dtype": "float64", "axes": [axis], "units": unit}
        for name, size, axis, unit in (("x_mm", nx, "x", "mm"), ("y_mm", ny, "y", "mm"), ("time_us", nt, "time", "us"))})
    return {name: {"path": name, **values} for name, values in result.items()}


def _digest(value):
    return measure(value)["sha256"]


def _input(m):
    return {key: m[key] for key in ("dataset_id", "kind", "schema_version", "request", "estimate_sha256", "solver",
        "shape", "axis_order", "dtype", "arrays", "certificate_contract", "evidence_status", "tile_rows", "total_rows")}


def _registry_hash(m):
    return _digest({"input_sha256": m["input_sha256"], "coordinates_sha256": m["coordinates_sha256"],
        "completed_chunks": m["completed_chunks"], "maximum_complex_bound": m["maximum_complex_bound"],
        "maximum_magnitude_bound": m["maximum_magnitude_bound"]})


class ObservationStore:
    kind = KIND
    axis_order = ("y", "x", "time")

    def __init__(self, root):
        self.root = guarded_root(root)

    def path(self, identifier):
        path = self.root/checked_id(identifier)
        if path.is_symlink() or path.is_junction() or path.resolve().parent != self.root:
            raise ValueError("Observation paths cannot follow links or leave their data root.")
        return path

    @staticmethod
    def _tree(path):
        if path.is_symlink() or path.is_junction():
            raise ValueError("Observation storage cannot contain links or junctions.")
        count = 0
        if not path.exists():
            return
        for directory, dirs, files in os.walk(path, followlinks=False):
            for name in dirs+files:
                count += 1
                child = Path(directory)/name
                if count > 4096:
                    raise ValueError("Observation store exceeds its bounded filesystem entry count.")
                if child.is_symlink() or child.is_junction() or not child.resolve().is_relative_to(path.resolve()):
                    raise ValueError("Observation storage cannot contain links or junctions.")

    def _frozen(self, m):
        try:
            if (m["kind"] != KIND or m["schema_version"] != 1 or m["certificate_contract"] != CONTRACT or
                m["axis_order"] != list(self.axis_order) or m["dtype"] != "float64"):
                raise ValueError("Unsupported observation dataset contract.")
            if _digest(m["estimate"]) != m["estimate_sha256"] or _digest(_input(m)) != m["input_sha256"]:
                raise ValueError("Observation frozen input checksum mismatch.")
            estimate = validate_plan(m["estimate"])
            if (m["request"] != estimate["request"] or m["shape"] != estimate["shape"] or
                m["total_rows"] != estimate["total_rows"] or m["tile_rows"] != 1 or
                m["arrays"] != _descriptors(m["shape"]) or m["coordinates_sha256"] != estimate["coordinates_sha256"] or
                m["evidence_status"] != estimate["evidence_status"]):
                raise ValueError("Observation descriptors differ from the frozen plan.")
            if (type(m["completed_chunks"]) is not dict or len(m["completed_chunks"]) > m["total_rows"] or
                type(m["completed_rows"]) is not int or m["completed_rows"] != len(m["completed_chunks"])):
                raise ValueError("Observation row completion registry is invalid.")
        except (KeyError, TypeError) as exc:
            raise ValueError("Observation manifest is incomplete.") from exc

    def manifest(self, identifier):
        path = self.path(identifier)/"manifest.json"
        if not path.exists() and not path.is_symlink():
            raise KeyError(identifier)
        m, _ = bounded_json_read(path, MAX_PLAN_BYTES, MAX_PLAN_EXPANDED_BYTES)
        if type(m) is not dict or m.get("dataset_id") != identifier:
            raise ValueError("Observation dataset identity mismatch.")
        self._frozen(m)
        return m

    def _admit(self, m):
        size = measure(m)
        estimate = m["estimate"]
        if (size["encoded_bytes"] > estimate["estimated_manifest_bytes"] or
                size["expanded_bytes"] > estimate["estimated_manifest_expanded_bytes"]):
            raise ValueError("Observation manifest exceeds its advertised output/workspace reserve.")
        numeric = sum(math.prod(d["shape"])*8 for d in m["arrays"].values())
        if numeric+size["encoded_bytes"]+12*16384 > estimate["total_bytes"]:
            raise ValueError("Observation output exceeds its advertised reservation.")

    def _save(self, identifier, m):
        previous = self.manifest(identifier)
        if previous["complete"]:
            raise ValueError("Completed observation datasets are immutable.")
        if _input(previous) != _input(m) or previous["estimate"] != m["estimate"]:
            raise ValueError("Observation frozen inputs are immutable.")
        for key, value in previous["completed_chunks"].items():
            if key in m["completed_chunks"] and m["completed_chunks"][key] != value:
                raise ValueError("A committed observation row cannot be overwritten.")
        self._frozen(m)
        self._admit(m)
        m["updated_at"] = now_iso()
        atomic_json(self.path(identifier)/"manifest.json", m)

    def create(self, identifier, request_dict, estimate):
        path = self.path(identifier)
        if path.exists():
            raise FileExistsError(path)
        validate_plan(estimate)
        if request_dict != estimate["request"]:
            raise ValueError("Observation request and frozen plan disagree.")
        source_context(estimate, self.root, verify=False)
        m = {"schema_version": 1, "dataset_id": identifier, "kind": KIND, "request": request_dict,
            "estimate": estimate, "estimate_sha256": _digest(estimate), "solver": observation_identity(),
            "shape": estimate["shape"], "axis_order": list(self.axis_order), "dtype": "float64",
            "arrays": _descriptors(estimate["shape"]), "certificate_contract": CONTRACT,
            "evidence_status": estimate["evidence_status"], "tile_rows": 1, "total_rows": estimate["total_rows"],
            "coordinates_sha256": estimate["coordinates_sha256"], "arrays_initialized": False,
            "complete": False, "state": "queued", "completed_rows": 0, "completed_chunks": {},
            "created_at": now_iso(), "updated_at": now_iso(), "error": None}
        m["input_sha256"] = _digest(_input(m))
        self._admit(m)
        check_disk_space(self.root, estimate["total_bytes"])
        path.mkdir(parents=True, exist_ok=False)
        atomic_json(path/"manifest.json", m)
        return m

    def validate_identity(self, identifier):
        m = self.manifest(identifier)
        if m["complete"]:
            return self.verify_complete(identifier)
        if m["solver"] != observation_identity():
            raise ValueError("Observation implementation/runtime changed; partial output cannot resume.")
        source_context(m["estimate"], self.root, verify=True)
        return m

    def initialize_arrays(self, identifier):
        m = self.manifest(identifier)
        if m["complete"]:
            raise ValueError("Completed observation datasets are immutable.")
        if m["arrays_initialized"]:
            self._open_checked(identifier, m)
            return m
        if m["completed_chunks"]:
            raise ValueError("Uninitialized observation has committed rows.")
        target = self.path(identifier)/"data.zarr"
        self._tree(target)
        group = zarr.open_group(str(target), mode="w", zarr_format=3)
        group.attrs.update(self._attrs(m))
        for name, d in m["arrays"].items():
            kwargs = {"chunks": tuple(d["chunks"]), "dimension_names": tuple(d["axes"]), "compressors": None}
            if name in COORDINATES:
                group.create_array(name, data=np.asarray(m["estimate"][name], dtype="<f8"), **kwargs)
            else:
                group.create_array(name, shape=tuple(d["shape"]), dtype="<f8", fill_value=float("nan"), **kwargs)
        m["arrays_initialized"] = True
        self._save(identifier, m)
        self._open_checked(identifier, m)
        return m

    @staticmethod
    def _attrs(m):
        return {"dataset_id": m["dataset_id"], "kind": KIND, "input_sha256": m["input_sha256"], "certificate_contract": CONTRACT}

    def _chunk(self, identifier, m, name, y):
        d = m["arrays"][name]
        parts = [str(y if name in PRODUCTS else 0)]+["0"]*(len(d["shape"])-1)
        path = self.path(identifier)/"data.zarr"/name/"c"
        for part in parts:
            path /= part
        if not path.is_file() or path.stat().st_size != math.prod(d["chunks"])*8:
            raise ValueError(f"Missing or noncanonical observation chunk: {name}, row {y}.")
        return path

    def _open_checked(self, identifier, m, mode="r"):
        self._frozen(m)
        if not m["arrays_initialized"] or mode not in {"r", "r+"} or (mode != "r" and m["complete"]):
            raise ValueError("Observation arrays require initialized read or partial-write state.")
        target = self.path(identifier)/"data.zarr"
        self._tree(target)
        raw, _ = bounded_json_read(target/"zarr.json", 16384, 262144)
        if (raw.get("zarr_format") != 3 or raw.get("node_type") != "group" or raw.get("attributes") != self._attrs(m) or
                raw.get("consolidated_metadata") is not None or {p.name for p in target.iterdir()} != {"zarr.json", *m["arrays"]}):
            raise ValueError("Observation Zarr group identity or registry mismatch.")
        for name, d in m["arrays"].items():
            raw, _ = bounded_json_read(target/name/"zarr.json", 16384, 262144)
            if (raw.get("zarr_format") != 3 or raw.get("node_type") != "array" or raw.get("shape") != d["shape"] or
                raw.get("data_type") != "float64" or raw.get("chunk_grid") != {"name": "regular", "configuration": {"chunk_shape": d["chunks"]}} or
                raw.get("chunk_key_encoding") != {"name": "default", "configuration": {"separator": "/"}} or
                raw.get("codecs") != CODECS or raw.get("dimension_names") != d["axes"] or raw.get("attributes") != {} or
                raw.get("storage_transformers", []) != [] or raw.get("fill_value") != (0 if name in COORDINATES else "NaN")):
                raise ValueError(f"Noncanonical observation Zarr array metadata: {name}.")
        group = zarr.open_group(str(target), mode=mode, use_consolidated=False)
        for name in COORDINATES:
            self._chunk(identifier, m, name, 0)
            actual = group[name][:]
            if typed_sha256(actual) != m["coordinates_sha256"][name] or actual.tobytes() != np.asarray(m["estimate"][name], dtype="<f8").tobytes():
                raise ValueError("Observation coordinate checksum mismatch.")
        return group

    @staticmethod
    def _bounds(m, y):
        return np.asarray(m["estimate"]["source_bound_map"][y:y+3], np.float64)

    def _validate_row(self, m, y, result):
        nx, nt = m["shape"][1:]
        if type(y) is not int or not 0 <= y < m["total_rows"] or set(result) != set(PRODUCTS):
            raise ValueError("Observation rows require all three signals and five bound maps.")
        for key, a in result.items():
            if (type(a) is not np.ndarray or a.dtype != np.dtype("float64") or
                a.shape != ((nx, nt) if key in SIGNALS else (nx,)) or not np.isfinite(a).all()):
                raise ValueError("Observation products require finite canonical float64 row arrays.")
        digests = {name: typed_sha256(value) for name, value in result.items()}
        bounds = self._bounds(m, y)
        cache_key = (CONTRACT, tuple(sorted(digests.items())), typed_sha256(bounds), m["request"]["absolute_tolerance"])
        with _cache_lock:
            if cache_key in _row_cache:
                _row_cache.move_to_end(cache_key)
                return digests
        from .observation_math import verify_saved_row
        verify_saved_row(result, bounds, absolute_tolerance=m["request"]["absolute_tolerance"])
        with _cache_lock:
            _row_cache[cache_key] = True
            while len(_row_cache) > MAX_CACHE_ENTRIES:
                _row_cache.popitem(last=False)
        return digests

    def _read_row(self, identifier, m, group, y):
        for name in PRODUCTS:
            self._chunk(identifier, m, name, y)
        result = {name: np.asarray(group[name][y]) for name in PRODUCTS}
        hashes = {name: typed_sha256(value) for name, value in result.items()}
        record = m["completed_chunks"].get(str(y))
        if (record is None or record.get("rows") != [y, y+1] or record.get("sha256") != hashes or
                record.get("row_sha256") != _digest({"y": y, "sha256": hashes, "input_sha256": m["input_sha256"]})):
            raise ValueError("Observation row checksum or completion record mismatch.")
        self._validate_row(m, y, result)
        return result

    def write_row(self, identifier, y, result, source_rows, *, cancelled=lambda: False):
        m = self.manifest(identifier)
        if m["complete"] or str(y) in m["completed_chunks"]:
            raise ValueError("Completed observation rows are immutable; verify and skip them.")
        if type(y) is not int or not 0 <= y < m["total_rows"]:
            raise ValueError("Observation row index is invalid.")
        if set(source_rows) != {"rf", "imaginary", "bounds"}:
            raise ValueError("Observation publication requires its exact three source rows.")
        source = m["estimate"]["source_manifest"]
        for key, values in source_rows.items():
            expected_shape = (3, source["shape"][1]) if key == "bounds" else (3, *source["shape"][1:])
            if type(values) is not np.ndarray or values.dtype != np.dtype("float64") or values.shape != expected_shape:
                raise ValueError("Observation source window has an invalid shape or dtype.")
            name = "error_bound" if key == "bounds" else key
            for offset in range(3):
                if typed_sha256(values[offset:offset+1]) != source["completed_chunks"][str(y+offset)][f"{name}_sha256"]:
                    raise ValueError("Observation source-window bytes differ from the frozen source.")
        from .observation_math import verify_row, ObservationCancelled
        if cancelled():
            raise ObservationCancelled()
        verify_row(source_rows["rf"], source_rows["imaginary"], source_rows["bounds"], result,
                   absolute_tolerance=m["request"]["absolute_tolerance"], cancelled=cancelled)
        hashes = self._validate_row(m, y, result)
        record = {"rows": [y, y+1], "sha256": hashes,
            "row_sha256": _digest({"y": y, "sha256": hashes, "input_sha256": m["input_sha256"]})}
        self._admit({**m, "completed_chunks": {**m["completed_chunks"], str(y): record}})
        check_disk_space(self.root, self.commit_bytes(m, y, y+1)+m["estimate"]["estimated_manifest_bytes"])
        if cancelled():
            raise ObservationCancelled()
        group = self._open_checked(identifier, m, "r+")
        for name, values in result.items():
            group[name][y] = values
            self._chunk(identifier, m, name, y)
            if typed_sha256(group[name][y]) != hashes[name]:
                raise OSError("Observation row failed typed write readback.")
        if cancelled():
            raise ObservationCancelled()
        m["completed_chunks"][str(y)] = record
        m["completed_rows"] = len(m["completed_chunks"])
        self._save(identifier, m)
        return m

    @staticmethod
    def commit_bytes(m, y0, y1):
        if type(y0) is not int or type(y1) is not int or not 0 <= y0 < m["total_rows"] or y1 != y0+1:
            raise ValueError("Observation commits contain exactly one output row.")
        return m["shape"][1]*(24*m["shape"][2]+40)

    def verify_chunks(self, identifier):
        m = self.validate_identity(identifier)
        if m["complete"]:
            raise ValueError("Completed observation rows cannot be repaired in place.")
        if not m["arrays_initialized"]:
            return m
        group = self._open_checked(identifier, m)
        valid = {}
        for key in m["completed_chunks"]:
            if not key.isdecimal() or str(int(key)) != key or not 0 <= int(key) < m["total_rows"]:
                raise ValueError("Invalid observation completion row key.")
            try:
                self._read_row(identifier, m, group, int(key))
            except (ValueError, OSError):
                continue
            valid[key] = m["completed_chunks"][key]
        m.update(completed_chunks=valid, completed_rows=len(valid))
        self._save(identifier, m)
        return m

    def complete(self, identifier, *, cancelled=lambda: False):
        from .observation_math import ObservationCancelled
        if cancelled():
            raise ObservationCancelled()
        m = self.verify_chunks(identifier)
        if cancelled():
            raise ObservationCancelled()
        if m["completed_rows"] != m["total_rows"]:
            raise ValueError("Observation cannot complete with missing or corrupt rows.")
        group = self._open_checked(identifier, m)
        maxima = {"complex_total": 0., "magnitude_total": 0.}
        for y in range(m["total_rows"]):
            row = self._read_row(identifier, m, group, y)
            for key in maxima:
                maxima[key] = max(maxima[key], float(row[key].max()))
            if cancelled():
                raise ObservationCancelled()
        m["maximum_complex_bound"] = maxima["complex_total"]
        m["maximum_magnitude_bound"] = maxima["magnitude_total"]
        m["completion_sha256"] = _registry_hash(m)
        m.update(complete=True, state="completed", error=None, completed_at=now_iso())
        if cancelled():
            raise ObservationCancelled()
        self._save(identifier, m)
        return self.verify_complete(identifier)

    def set_state(self, identifier, state, error=None):
        m = self.manifest(identifier)
        if state not in {"queued", "running", "cancelling", "cancelled", "interrupted", "failed"}:
            raise ValueError("Invalid partial observation job state.")
        m.update(state=state, error=error)
        self._save(identifier, m)
        return m

    def verify_complete(self, identifier):
        m = self.manifest(identifier)
        if not m["complete"] or m["state"] != "completed" or m["completed_rows"] != m["total_rows"]:
            raise ValueError("Only completed observation datasets are readable/exportable.")
        group = self._open_checked(identifier, m)
        maxima = {"complex_total": 0., "magnitude_total": 0.}
        for y in range(m["total_rows"]):
            row = self._read_row(identifier, m, group, y)
            for name in maxima:
                maxima[name] = max(maxima[name], float(row[name].max()))
        if (m.get("maximum_complex_bound") != maxima["complex_total"] or m.get("maximum_magnitude_bound") != maxima["magnitude_total"] or
                _registry_hash(m) != m.get("completion_sha256")):
            raise ValueError("Observation completed certificate registry mismatch.")
        self._admit(m)
        files = self._files(identifier, m)
        if sum(p.stat().st_size for p in files) > m["estimate"]["total_bytes"]:
            raise ValueError("Observation files exceed their advertised output budget.")
        return m

    def _files(self, identifier, m):
        path = self.path(identifier)
        self._tree(path)
        expected = {path/"manifest.json", path/"data.zarr"/"zarr.json"}
        for name in m["arrays"]:
            expected.add(path/"data.zarr"/name/"zarr.json")
            for y in range(m["total_rows"] if name in PRODUCTS else 1):
                expected.add(self._chunk(identifier, m, name, y))
        actual = {p for p in path.rglob("*") if p.is_file()}
        if actual != expected:
            raise ValueError("Observation export has unexpected or missing files.")
        return sorted(actual)

    def safe_export_files(self, identifier):
        m = self.verify_complete(identifier)
        return self._files(identifier, m)
