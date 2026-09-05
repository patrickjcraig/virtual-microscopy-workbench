"""Local, resumable Zarr datasets with frozen inputs and committed row chunks.

The manifest is the commit record: a tile is complete only after both arrays
have been written and read back. Incomplete datasets never provide image data
through the API. Completed datasets are immutable through this module.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import time
from uuid import UUID, uuid4

import numpy as np
import zarr


DATASET_SCHEMA_VERSION = 1
MIN_FREE_RESERVE_BYTES = 64 * 1024 * 1024


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def json_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def array_sha256(value: np.ndarray) -> str:
    # Explicit endian and C order make the digest independent of array strides.
    return hashlib.sha256(np.ascontiguousarray(value, dtype="<f4").tobytes()).hexdigest()


def coordinate_sha256(value: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(value, dtype="<f8").tobytes()).hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(canonical_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        # Windows readers may briefly open an existing manifest without delete
        # sharing. Keep the replacement atomic, but tolerate that transient
        # sharing/access conflict while the catalog finishes its read.
        for attempt in range(7):
            try:
                os.replace(temporary, path)
                break
            except PermissionError as exc:
                if os.name != "nt" or getattr(exc, "winerror", None) not in (5, 32, 33) or attempt == 6:
                    raise
                time.sleep(.01 * 2 ** attempt)
    finally:
        temporary.unlink(missing_ok=True)


def checked_id(identifier: str) -> str:
    try:
        parsed = UUID(identifier)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Dataset ID must be a canonical UUID.") from exc
    if str(parsed) != identifier:
        raise ValueError("Dataset ID must be a canonical UUID.")
    return identifier


def default_data_root() -> Path:
    configured = os.environ.get("VM_DATA_ROOT")
    return (Path(configured).expanduser() if configured else
            Path(__file__).resolve().parents[1] / "artifacts" / "volumes").resolve()


def dataset_path(root: Path, identifier: str) -> Path:
    root = Path(root).resolve()
    target = (root / checked_id(identifier)).resolve()
    if target.parent != root:
        raise ValueError("Dataset path leaves the data directory.")
    return target


def validate_dataset_paths(path: Path, identifier: str, *, include_arrays: bool = True) -> list[Path]:
    """Check roots, junctions and descendants before reading a stored dataset."""
    path = Path(path)
    logical_path = path.parent / checked_id(identifier)

    def reject_link(target):
        if target.is_symlink() or target.is_junction():
            raise ValueError("Dataset reads and exports do not follow symbolic links or directory junctions.")

    reject_link(logical_path)
    reject_link(path)
    root = path.resolve(strict=True)
    if root != logical_path.resolve(strict=True):
        raise ValueError("Dataset path does not match its identifier.")

    def checked_child(child):
        reject_link(child)
        if not child.resolve(strict=True).is_relative_to(root):
            raise ValueError("Dataset path leaves the dataset directory.")
        return child

    manifest = checked_child(path / "manifest.json")
    if not manifest.is_file():
        raise ValueError("Dataset requires a regular manifest file.")
    files = [manifest]
    if not include_arrays:
        return files
    data = checked_child(path / "data.zarr")
    if not data.is_dir():
        raise ValueError("Dataset requires its Zarr directory.")
    for directory, directories, filenames in os.walk(data, followlinks=False):
        current = checked_child(Path(directory))
        for name in directories:
            checked_child(current / name)
        for name in filenames:
            child = checked_child(current / name)
            if child.is_file():
                files.append(child)
    return files


def check_disk_space(root: Path, required_bytes: int) -> dict:
    """Conservative preflight using uncompressed bytes and a small reserve."""
    probe = Path(root).resolve()
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    needed = int(required_bytes) + MIN_FREE_RESERVE_BYTES
    if needed > free:
        raise OSError(f"Insufficient free disk space: need {needed:,} bytes including "
                      f"a {MIN_FREE_RESERVE_BYTES:,}-byte reserve; {free:,} available.")
    return {"free_disk_bytes": free, "required_disk_bytes": needed,
            "reserve_bytes": MIN_FREE_RESERVE_BYTES}


def material_snapshot() -> dict:
    from . import materials
    return json.loads(canonical_json({
        "materials": materials.MATERIALS,
        "water": {"sound_speed_m_s": materials.WATER_SOUND_SPEED_M_S,
                  "impedance_mrayl": materials.WATER_IMPEDANCE_MRAYL,
                  "attenuation_db_mm_at_50mhz": materials.WATER_ATTENUATION_DB_MM_AT_50MHZ},
    }))


def solver_identity(model_version: str, kind: str = "sam_rf_volume") -> dict:
    """Refuse a mixed-version resume instead of silently changing the physics."""
    source_root = Path(__file__).resolve().parent
    source_hashes = {}
    if kind == "sam_rf_volume":
        filenames = ("sam_volume.py", "volume_schemas.py", "physics.py", "schemas.py", "materials.py")
    elif kind == "xray_projection_volume":
        filenames = ("xray_volume.py", "xray_schemas.py", "physics.py", "schemas.py", "materials.py")
    elif kind == "xray_reconstruction":
        filenames = ("reconstruction.py", "reconstruction_schemas.py")
    elif kind == "sam_depth_volume":
        filenames = ("depth_mapping.py", "depth_schemas.py")
    else:
        raise ValueError(f"Unknown dataset kind: {kind}.")
    for filename in filenames:
        path = source_root / filename
        if path.is_file():
            source_hashes[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"model_version": model_version, "source_sha256": source_hashes,
            "numerical_packages": {name: importlib.metadata.version(name)
                                   for name in ("numpy", "scipy")}}


class DatasetStore:
    kind = "sam_rf_volume"
    axis_order = ("y", "x", "time")
    signal_units = {"rf": "relative signed amplitude", "envelope": "relative envelope amplitude"}
    evidence_status = "Synthetic reduced-order SAM; not experimentally calibrated."

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, identifier: str) -> Path:
        return dataset_path(self.root, identifier)

    def _solver_identity(self, model_version: str) -> dict:
        return solver_identity(model_version)

    def _coordinate_descriptors(self, shape: list[int]) -> dict:
        return {"x_mm": {"path": "x_mm", "units": "mm", "axes": ["x"], "dtype": "float64"},
                "y_mm": {"path": "y_mm", "units": "mm", "axes": ["y"], "dtype": "float64"},
                "time_us": {"path": "time_us", "units": "us", "axes": ["time"], "dtype": "float64"}}

    def _signal_descriptors(self, shape: list[int], request: dict) -> dict:
        return {name: {"path": name, "shape": shape, "dtype": "float32",
                       "axes": list(self.axis_order), "units": units}
                for name, units in self.signal_units.items()}

    def _creation_provenance(self, request: dict) -> dict:
        return {}

    def _creation_materials(self, provenance: dict) -> dict:
        return material_snapshot()

    def _identity_payload(self, manifest: dict) -> dict:
        return {"request_sha256": manifest["request_sha256"],
                "materials_sha256": manifest["materials_sha256"], "solver": manifest["solver"]}

    def _verify_frozen_provenance(self, manifest: dict) -> None:
        pass

    def _verify_current_dependencies(self, manifest: dict) -> None:
        if json_sha256(material_snapshot()) != manifest["materials_sha256"]:
            raise ValueError("Material library changed; create a new dataset instead of resuming.")

    def manifest(self, identifier: str) -> dict:
        path = self.path(identifier) / "manifest.json"
        if not path.is_file():
            raise KeyError(identifier)
        return json.loads(path.read_text(encoding="utf-8"))

    def create(self, identifier: str, request: dict, estimate: dict) -> dict:
        shape = [int(v) for v in estimate["shape"]]
        if len(shape) != 3 or any(v <= 0 for v in shape):
            raise ValueError(f"Volume shape must be positive {list(self.axis_order)}.")
        tile_rows = int(estimate["tile_rows"])
        if tile_rows < 1:
            raise ValueError("Tile row count must be positive.")
        check_disk_space(self.root, estimate["total_bytes"] + estimate.get("estimated_temporary_bytes", estimate.get("workspace_disk_bytes", 0)))
        frozen_request = json.loads(canonical_json(request))
        provenance = self._creation_provenance(frozen_request)
        path = self.path(identifier)
        path.mkdir(exist_ok=False)
        materials = self._creation_materials(provenance)
        identity = self._solver_identity(estimate["model_version"])
        input_sha = json_sha256(frozen_request)
        materials_sha = json_sha256(materials)
        manifest = {
            "schema_version": DATASET_SCHEMA_VERSION, "dataset_id": identifier,
            "kind": self.kind, "created_at": now_iso(), "updated_at": now_iso(),
            "state": "queued", "complete": False, "arrays_initialized": False,
            "evidence_status": self.evidence_status,
            "request": frozen_request, "request_sha256": input_sha,
            "materials": materials, "materials_sha256": materials_sha,
            "solver": identity,
            "input_sha256": "",
            "estimate": estimate, "shape": shape, "axis_order": list(self.axis_order),
            "dtype": "float32", "zarr_format": 3, "store": "data.zarr",
            "tile_rows": tile_rows, "total_rows": shape[0], "completed_rows": 0,
            "completed_chunks": {}, "coordinates_sha256": {}, "metadata": {},
            "arrays": {**self._signal_descriptors(shape, frozen_request),
                       **self._coordinate_descriptors(shape)},
            **provenance,
        }
        manifest["input_sha256"] = json_sha256(self._identity_payload(manifest))
        atomic_json(path / "manifest.json", manifest)
        return manifest

    def save(self, identifier: str, manifest: dict) -> None:
        previous = self.manifest(identifier)
        if previous["complete"]:
            raise ValueError("Completed datasets are immutable.")
        if manifest.get("dataset_id") != identifier:
            raise ValueError("Dataset identity cannot change.")
        for field in ("request", "request_sha256", "materials", "materials_sha256", "solver",
                      "input_sha256", "estimate", "shape", "tile_rows", "total_rows", "kind",
                      "axis_order", "arrays", "dtype", "store", "zarr_format", "evidence_status",
                      "source_manifest", "source_manifest_sha256", "source_dataset_id"):
            if manifest.get(field) != previous.get(field):
                raise ValueError(f"Frozen dataset field cannot change: {field}.")
        manifest["updated_at"] = now_iso()
        atomic_json(self.path(identifier) / "manifest.json", manifest)

    def set_state(self, identifier: str, state: str, error: str | None = None) -> dict:
        manifest = self.manifest(identifier)
        if manifest["complete"]:
            return manifest
        manifest.update(state=state, error=error)
        self.save(identifier, manifest)
        return manifest

    def validate_identity(self, identifier: str) -> dict:
        manifest = self.manifest(identifier)
        if manifest.get("kind", "sam_rf_volume") != self.kind:
            raise ValueError("Dataset kind does not match its storage reader.")
        if json_sha256(manifest["request"]) != manifest["request_sha256"]:
            raise ValueError("Frozen request checksum mismatch; cannot resume this dataset.")
        if json_sha256(manifest["materials"]) != manifest["materials_sha256"]:
            raise ValueError("Frozen material checksum mismatch; cannot resume this dataset.")
        self._verify_frozen_provenance(manifest)
        self._verify_current_dependencies(manifest)
        if self._solver_identity(manifest["solver"]["model_version"]) != manifest["solver"]:
            raise ValueError("Solver or numerical package changed; create a new dataset instead of resuming.")
        expected = json_sha256(self._identity_payload(manifest))
        if expected != manifest["input_sha256"]:
            raise ValueError("Dataset identity checksum mismatch.")
        return manifest

    def initialize_arrays(self, identifier: str, x_mm, y_mm, time_us, metadata: dict) -> dict:
        manifest = self.manifest(identifier)
        if manifest["complete"]:
            raise ValueError("Completed datasets are immutable.")
        if manifest["arrays_initialized"]:
            return manifest
        if manifest["completed_chunks"]:
            raise ValueError("Uninitialized dataset unexpectedly contains committed chunks.")
        ny, nx, nt = manifest["shape"]
        coords = {"x_mm": np.asarray(x_mm, dtype="<f8"), "y_mm": np.asarray(y_mm, dtype="<f8"),
                  "time_us": np.asarray(time_us, dtype="<f8")}
        for name, length in (("x_mm", nx), ("y_mm", ny), ("time_us", nt)):
            if coords[name].shape != (length,) or not np.isfinite(coords[name]).all():
                raise ValueError(f"Invalid {name} coordinate vector.")
        group = zarr.open_group(str(self.path(identifier) / "data.zarr"), mode="w", zarr_format=3)
        group.attrs.update({"dataset_id": identifier, "input_sha256": manifest["input_sha256"],
                            "axis_order": manifest["axis_order"], "evidence_status": manifest["evidence_status"]})
        for name in self.signal_units:
            group.create_array(name, shape=(ny, nx, nt), chunks=(min(ny, manifest["tile_rows"]), nx, nt),
                               dtype="float32", fill_value=float("nan"),
                               dimension_names=("y", "x", "time"))
        for name, values in coords.items():
            group.create_array(name, data=values, dimension_names=(manifest["arrays"][name]["axes"][0],))
        manifest.update(arrays_initialized=True, metadata=metadata,
                        coordinates_sha256={name: coordinate_sha256(values) for name, values in coords.items()})
        self.save(identifier, manifest)
        return manifest

    def open_arrays(self, identifier: str, mode: str = "r"):
        if mode not in ("r", "r+"):
            raise ValueError("Only existing dataset reads and resumable writes are allowed.")
        manifest = self.manifest(identifier)
        if not manifest["arrays_initialized"]:
            raise ValueError("Dataset arrays have not been initialized.")
        if mode != "r" and manifest["complete"]:
            raise ValueError("Completed datasets are immutable.")
        return zarr.open_group(str(self.path(identifier) / "data.zarr"), mode=mode)

    @staticmethod
    def _verify_coordinates(group, manifest: dict) -> None:
        ny, nx, nt = manifest["shape"]
        lengths = {"x_mm": nx, "y_mm": ny, "time_us": nt}
        if set(manifest["coordinates_sha256"]) != set(lengths):
            raise ValueError("Coordinate checksum registry is incomplete.")
        for name, length in lengths.items():
            if group[name].shape != (length,) or group[name].dtype != np.dtype("float64"):
                raise ValueError(f"Invalid {name} coordinate shape or type.")
            if coordinate_sha256(group[name][:]) != manifest["coordinates_sha256"][name]:
                raise ValueError(f"Coordinate checksum mismatch: {name}.")

    def verify_chunks(self, identifier: str) -> dict:
        """Drop corrupt/missing partial commits; their rows are regenerated on resume."""
        manifest = self.validate_identity(identifier)
        if manifest["complete"]:
            raise ValueError("Completed datasets cannot be repaired in place.")
        if not manifest["arrays_initialized"]:
            return manifest
        group = self.open_arrays(identifier)
        if group.attrs.get("input_sha256") != manifest["input_sha256"]:
            raise ValueError("Zarr input identity does not match the manifest.")
        self._verify_coordinates(group, manifest)
        for name in self.signal_units:
            array = group[name]
            if list(array.shape) != manifest["shape"] or array.dtype != np.dtype("float32"):
                raise ValueError(f"Invalid {name} array shape or type.")
        valid = {}
        for key, chunk in manifest["completed_chunks"].items():
            y0, y1 = chunk["rows"]
            if (key != str(y0) or y0 % manifest["tile_rows"] or
                    y1 != min(manifest["total_rows"], y0 + manifest["tile_rows"]) or
                    y0 < 0 or y0 >= manifest["total_rows"]):
                raise ValueError("Invalid chunk completion registry.")
            try:
                good = all(array_sha256(group[name][y0:y1]) == chunk[f"{name}_sha256"]
                           for name in self.signal_units)
            except (OSError, ValueError, RuntimeError):
                good = False
            if good:
                valid[key] = chunk
        manifest["completed_chunks"] = valid
        manifest["completed_rows"] = sum(c["rows"][1] - c["rows"][0] for c in valid.values())
        self.save(identifier, manifest)
        return manifest

    def verify_complete(self, identifier: str) -> dict:
        """Read-only integrity check suitable before exporting an older dataset."""
        manifest = self.manifest(identifier)
        if manifest.get("kind", "sam_rf_volume") != self.kind:
            raise ValueError("Dataset kind does not match its storage reader.")
        if not manifest["complete"] or manifest["state"] != "completed":
            raise ValueError("Only completed datasets can be exported.")
        if (json_sha256(manifest["request"]) != manifest["request_sha256"] or
                json_sha256(manifest["materials"]) != manifest["materials_sha256"]):
            raise ValueError("Frozen dataset snapshot checksum mismatch.")
        self._verify_frozen_provenance(manifest)
        expected_input = json_sha256(self._identity_payload(manifest))
        if expected_input != manifest["input_sha256"]:
            raise ValueError("Dataset identity checksum mismatch.")
        if (manifest["shape"] != manifest["estimate"]["shape"] or
                manifest["tile_rows"] != manifest["estimate"]["tile_rows"] or
                manifest["total_rows"] != manifest["shape"][0] or
                manifest["completed_rows"] != manifest["total_rows"]):
            raise ValueError("Dataset layout or completion metadata mismatch.")
        group = self.open_arrays(identifier)
        if group.attrs.get("input_sha256") != manifest["input_sha256"]:
            raise ValueError("Zarr input identity does not match the manifest.")
        for name in self.signal_units:
            if list(group[name].shape) != manifest["shape"] or group[name].dtype != np.dtype("float32"):
                raise ValueError(f"Invalid {name} array shape or type.")
        self._verify_coordinates(group, manifest)
        for y0 in range(0, manifest["total_rows"], manifest["tile_rows"]):
            y1 = min(manifest["total_rows"], y0 + manifest["tile_rows"])
            chunk = manifest["completed_chunks"].get(str(y0))
            if not chunk or chunk["rows"] != [y0, y1]:
                raise ValueError("Dataset completion registry has missing rows.")
            for name in self.signal_units:
                if array_sha256(group[name][y0:y1]) != chunk[f"{name}_sha256"]:
                    raise ValueError(f"Stored {name} checksum mismatch at row {y0}.")
        return manifest

    def write_tile(self, identifier: str, y0: int, y1: int, rf, envelope) -> dict:
        manifest = self.manifest(identifier)
        if manifest["complete"]:
            raise ValueError("Completed datasets are immutable.")
        rows, nx, nt = manifest["shape"]
        if y0 < 0 or y0 >= rows or y0 % manifest["tile_rows"] or y1 != min(rows, y0 + manifest["tile_rows"]):
            raise ValueError("Tiles must match the dataset's canonical row chunks.")
        if str(y0) in manifest["completed_chunks"]:
            raise ValueError("Committed rows must be verified and skipped, not overwritten.")
        data = {"rf": np.ascontiguousarray(rf, dtype="<f4"),
                "envelope": np.ascontiguousarray(envelope, dtype="<f4")}
        if any(a.shape != (y1-y0, nx, nt) or not np.isfinite(a).all() for a in data.values()):
            raise ValueError("SAM tiles must be finite float32 arrays with the declared shape.")
        if np.any(data["envelope"] < 0):
            raise ValueError("Envelope amplitude must be nonnegative.")
        group = self.open_arrays(identifier, "r+")
        checksums = {}
        for name, values in data.items():
            group[name][y0:y1] = values
            expected = array_sha256(values)
            if array_sha256(group[name][y0:y1]) != expected:
                raise OSError(f"{name} chunk failed write verification.")
            checksums[f"{name}_sha256"] = expected
        manifest["completed_chunks"][str(y0)] = {"rows": [y0, y1], **checksums}
        manifest["completed_rows"] = sum(c["rows"][1] - c["rows"][0]
                                         for c in manifest["completed_chunks"].values())
        self.save(identifier, manifest)
        return manifest

    def complete(self, identifier: str) -> dict:
        manifest = self.verify_chunks(identifier)
        if manifest["completed_rows"] != manifest["total_rows"]:
            raise ValueError("Cannot complete a dataset with missing rows.")
        manifest.update(state="completed", complete=True, error=None, completed_at=now_iso())
        self.save(identifier, manifest)
        return manifest
