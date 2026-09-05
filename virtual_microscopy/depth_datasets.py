"""Derived SAM depth grids with frozen raw-time provenance and support masks."""
from __future__ import annotations

import json
from pathlib import Path
import shutil

import numpy as np
import zarr

from .datasets import (DatasetStore, array_sha256, canonical_json, checked_id,
                       coordinate_sha256, json_sha256, solver_identity, validate_dataset_paths)


CACHE_PREFIX = ".depth-mapping-cache-"
CACHE_OWNER = {"schema_version": 1, "purpose": "virtual_microscopy_depth_mapping_cache"}


def cleanup_depth_caches(root: Path) -> None:
    """Delete only identified orphan workspaces inside the declared data root."""
    root = Path(root).resolve()
    for candidate in root.glob(f"{CACHE_PREFIX}*"):
        try:
            checked_id(candidate.name[len(CACHE_PREFIX):])
        except ValueError:
            continue
        if candidate.is_symlink() or candidate.is_junction() or not candidate.is_dir():
            continue
        target = candidate.resolve(strict=True)
        if target.parent != root:
            continue
        marker = target / "owner.json"
        if marker.is_symlink() or not marker.is_file() or marker.resolve(strict=True).parent != target:
            continue
        try:
            owner = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # Resolve and verify the exact recursive target before deleting it.
        if owner == CACHE_OWNER:
            shutil.rmtree(target)


def depth_source(root: Path, identifier: str, *, verify: bool = False) -> tuple[dict, Path]:
    root = Path(root).resolve()
    path = root / checked_id(identifier)
    if not path.exists() and not path.is_symlink():
        raise KeyError(identifier)
    validate_dataset_paths(path, identifier, include_arrays=False)
    store = DatasetStore(root)
    manifest = store.manifest(identifier)
    if manifest.get("kind", "sam_rf_volume") != "sam_rf_volume":
        raise ValueError("SAM depth mapping requires a saved raw-time SAM acquisition.")
    if not manifest.get("complete") or manifest.get("state") != "completed":
        raise ValueError("SAM depth mapping requires a completed raw-time acquisition.")
    validate_dataset_paths(path, identifier)
    if verify:
        store.verify_complete(identifier)
    return manifest, path


class DepthDatasetStore(DatasetStore):
    kind = "sam_depth_volume"
    axis_order = ("z", "y", "x")
    signal_units = {"rf": "relative signed pressure", "envelope": "relative echo amplitude",
                    "valid_mask": "1 where the velocity model and acquired time window support the depth; otherwise 0"}
    evidence_status = "Depth estimate derived from saved synthetic SAM time signals and a declared velocity model; not measured geometry or experimental calibration."

    def _solver_identity(self, model_version: str) -> dict:
        return solver_identity(model_version, self.kind)

    def _creation_provenance(self, request: dict) -> dict:
        source, _ = depth_source(self.root, request["source_dataset_id"])
        return {"source_dataset_id": request["source_dataset_id"], "source_manifest": source,
                "source_manifest_sha256": json_sha256(source)}

    def _creation_materials(self, provenance: dict) -> dict:
        return provenance["source_manifest"]["materials"]

    def _identity_payload(self, manifest: dict) -> dict:
        return {**super()._identity_payload(manifest), "source_manifest_sha256": manifest["source_manifest_sha256"]}

    def _verify_frozen_provenance(self, manifest: dict) -> None:
        source = manifest["source_manifest"]
        if json_sha256(source) != manifest["source_manifest_sha256"]:
            raise ValueError("Frozen raw-time source manifest checksum mismatch.")
        source_id = checked_id(manifest["request"]["source_dataset_id"])
        if source_id != manifest["source_dataset_id"] or source_id != source.get("dataset_id"):
            raise ValueError("Frozen SAM depth source identity mismatch.")
        if source.get("kind", "sam_rf_volume") != "sam_rf_volume" or not source.get("complete") or source.get("state") != "completed":
            raise ValueError("Frozen SAM depth source must be a completed raw-time acquisition.")
        if canonical_json(manifest["materials"]) != canonical_json(source["materials"]):
            raise ValueError("SAM depth material provenance differs from its raw-time source.")
        if manifest["shape"][1:] != source["shape"][:2]:
            raise ValueError("SAM depth mapping must preserve the source lateral grid.")

    def _verify_current_dependencies(self, manifest: dict) -> None:
        self._source_for_manifest(manifest)

    def _source_for_manifest(self, manifest: dict, *, verify: bool = False) -> tuple[dict, Path]:
        current, path = depth_source(self.root, manifest["source_dataset_id"], verify=verify)
        if json_sha256(current) != manifest["source_manifest_sha256"]:
            raise ValueError("Raw-time source manifest changed; create a new depth mapping instead of resuming.")
        return current, path

    def source_context(self, identifier: str, *, verify: bool = False) -> tuple[dict, Path]:
        manifest = self.manifest(identifier)
        self._verify_frozen_provenance(manifest)
        return self._source_for_manifest(manifest, verify=verify)

    def _coordinate_descriptors(self, shape: list[int]) -> dict:
        nz, ny, nx = shape
        return {name: {"path": name, "shape": [size], "units": units, "axes": [axis], "dtype": "float64"}
                for name, size, axis, units in (("x_mm", nx, "x", "mm"), ("y_mm", ny, "y", "mm"),
                                               ("z_mm", nz, "z", "mm"), ("travel_time_us", nz, "z", "us"))}

    def _signal_descriptors(self, shape: list[int], request: dict) -> dict:
        descriptors = super()._signal_descriptors(shape, request)
        descriptors["valid_mask"]["note"] = "Invalid RF and envelope values are finite zero placeholders, not measurements. Supported zero amplitudes remain valid."
        return descriptors

    def create(self, identifier: str, request: dict, estimate: dict) -> dict:
        if request.get("kind") != self.kind or estimate.get("kind") != self.kind:
            raise ValueError("A SAM depth dataset requires an explicit depth-mapping request and estimate.")
        if estimate["tile_rows"] != 1:
            raise ValueError("SAM depth datasets commit one z slice per chunk.")
        source, _ = depth_source(self.root, request["source_dataset_id"])
        if estimate["shape"][1:] != source["shape"][:2]:
            raise ValueError("SAM depth mapping must preserve the source lateral grid.")
        return super().create(identifier, request, estimate)

    def save(self, identifier: str, manifest: dict) -> None:
        previous = self.manifest(identifier)
        if previous["arrays_initialized"]:
            for field in ("metadata", "metadata_sha256"):
                if previous.get(field) != manifest.get(field):
                    raise ValueError("Initialized SAM depth processing metadata is immutable.")
        super().save(identifier, manifest)

    @staticmethod
    def _verify_model_metadata(manifest: dict) -> None:
        metadata = manifest["metadata"]
        if json_sha256(metadata) != manifest.get("metadata_sha256"):
            raise ValueError("SAM depth processing metadata checksum mismatch.")
        support = metadata.get("model_depth_valid")
        if not isinstance(support, list) or len(support) != manifest["shape"][0] or any(type(v) is not bool for v in support):
            raise ValueError("SAM depth velocity-model support metadata is incomplete.")
        interval = metadata.get("source_time_range_us")
        if (not isinstance(interval, list) or len(interval) != 2 or not np.isfinite(interval).all() or
                interval[0] >= interval[1] or interval != manifest["estimate"].get("source_time_range_us")):
            raise ValueError("SAM depth source recording interval does not match its frozen estimate.")
        tolerance = metadata.get("time_support_tolerance_us")
        if not isinstance(tolerance, (float, int)) or not np.isfinite(tolerance) or not 0 <= tolerance <= 1e-9:
            raise ValueError("SAM depth time-support tolerance is missing or invalid.")

    def initialize_arrays(self, identifier: str, prepared) -> dict:
        manifest = self.manifest(identifier)
        if manifest["complete"]:
            raise ValueError("Completed datasets are immutable.")
        if manifest["arrays_initialized"]:
            return manifest
        if manifest["completed_chunks"]:
            raise ValueError("Uninitialized dataset unexpectedly contains committed chunks.")
        descriptors = self._coordinate_descriptors(manifest["shape"])
        coords = {name: np.ascontiguousarray(getattr(prepared, name), dtype="<f8") for name in descriptors}
        for name, values in coords.items():
            if list(values.shape) != descriptors[name]["shape"] or not np.isfinite(values).all():
                raise ValueError(f"Invalid SAM depth coordinate array: {name}.")
        for name in ("x_mm", "y_mm"):
            if coordinate_sha256(coords[name]) != manifest["source_manifest"]["coordinates_sha256"][name]:
                raise ValueError(f"SAM depth {name} must exactly preserve the saved source coordinates.")
        metadata = json.loads(canonical_json(prepared.metadata))
        manifest.update(metadata=metadata, metadata_sha256=json_sha256(metadata))
        self._verify_model_metadata(manifest)
        unsupported = ~np.asarray(metadata["model_depth_valid"], dtype=bool)
        if np.any(coords["travel_time_us"][unsupported] != 0):
            raise ValueError("Depths outside the declared velocity model require zero time placeholders.")
        group = zarr.open_group(str(self.path(identifier) / "data.zarr"), mode="w", zarr_format=3)
        group.attrs.update({"dataset_id": identifier, "kind": self.kind, "input_sha256": manifest["input_sha256"],
                            "source_dataset_id": manifest["source_dataset_id"],
                            "source_manifest_sha256": manifest["source_manifest_sha256"],
                            "axis_order": list(self.axis_order), "evidence_status": self.evidence_status})
        nz, ny, nx = manifest["shape"]
        for name in self.signal_units:
            group.create_array(name, shape=(nz, ny, nx), chunks=(1, ny, nx), dtype="float32",
                               fill_value=float("nan"), dimension_names=self.axis_order)
        for name, values in coords.items():
            group.create_array(name, data=values, dimension_names=tuple(descriptors[name]["axes"]))
        manifest.update(arrays_initialized=True,
                        coordinates_sha256={name: coordinate_sha256(values) for name, values in coords.items()})
        self.save(identifier, manifest)
        return manifest

    @staticmethod
    def _verify_coordinates(group, manifest: dict) -> None:
        DepthDatasetStore._verify_model_metadata(manifest)
        nz, ny, nx = manifest["shape"]
        lengths = {"x_mm": nx, "y_mm": ny, "z_mm": nz, "travel_time_us": nz}
        if set(manifest["coordinates_sha256"]) != set(lengths):
            raise ValueError("SAM depth coordinate checksum registry is incomplete.")
        for name, length in lengths.items():
            if group[name].shape != (length,) or group[name].dtype != np.dtype("float64"):
                raise ValueError(f"Invalid {name} coordinate shape or type.")
            if coordinate_sha256(group[name][:]) != manifest["coordinates_sha256"][name]:
                raise ValueError(f"Coordinate checksum mismatch: {name}.")
        for name in ("x_mm", "y_mm"):
            if manifest["coordinates_sha256"][name] != manifest["source_manifest"]["coordinates_sha256"][name]:
                raise ValueError(f"SAM depth {name} no longer matches its raw-time source coordinates.")
        if (group.attrs.get("source_dataset_id") != manifest["source_dataset_id"] or
                group.attrs.get("source_manifest_sha256") != manifest["source_manifest_sha256"]):
            raise ValueError("Stored SAM depth source identity mismatch.")
        model_valid = np.asarray(manifest["metadata"]["model_depth_valid"], dtype=bool)
        if np.any(group["travel_time_us"][:][~model_valid] != 0):
            raise ValueError("Unsupported velocity-model depths require zero time placeholders.")

    def write_slice(self, identifier: str, z0: int, z1: int, arrays: dict) -> dict:
        manifest = self.manifest(identifier)
        if manifest["complete"]:
            raise ValueError("Completed datasets are immutable.")
        nz, ny, nx = manifest["shape"]
        if z0 < 0 or z0 >= nz or z1 != z0 + 1:
            raise ValueError("A SAM depth chunk must contain exactly one valid z slice.")
        if str(z0) in manifest["completed_chunks"]:
            raise ValueError("Committed z slices must be verified and skipped, not overwritten.")
        if set(arrays) != set(self.signal_units):
            raise ValueError("SAM depth chunks require rf, envelope and valid_mask.")
        if any(np.asarray(a).shape != (1, ny, nx) or not np.isfinite(a).all() for a in arrays.values()):
            raise ValueError("SAM depth arrays must be finite with the declared [1,y,x] shape.")
        if any(np.any(np.abs(a) > np.finfo(np.float32).max) for a in arrays.values()):
            raise ValueError("SAM depth arrays cannot overflow float32 storage.")
        data = {name: np.ascontiguousarray(value, dtype="<f4") for name, value in arrays.items()}
        mask = data["valid_mask"]
        if not np.all((mask == 0) | (mask == 1)):
            raise ValueError("SAM depth validity mask must contain only binary 0 and 1 values.")
        if np.any(data["envelope"] < 0):
            raise ValueError("SAM depth envelope amplitude must be nonnegative.")
        invalid = mask == 0
        if np.any(data["rf"][invalid] != 0) or np.any(data["envelope"][invalid] != 0):
            raise ValueError("Unsupported SAM depths require zero RF and envelope placeholders.")
        group = self.open_arrays(identifier, "r+")
        metadata = manifest["metadata"]
        time_us = float(group["travel_time_us"][z0])
        t0, t1 = metadata["source_time_range_us"]
        tolerance = metadata["time_support_tolerance_us"]
        supported = (metadata["model_depth_valid"][z0] and t0 - tolerance <= time_us <= t1 + tolerance)
        if not np.all(mask == float(supported)):
            raise ValueError("Depth validity must match the declared velocity model and actual source recording interval.")
        checksums = {}
        for name, values in data.items():
            group[name][z0:z1] = values
            expected = array_sha256(values)
            if array_sha256(group[name][z0:z1]) != expected:
                raise OSError(f"{name} SAM depth chunk failed write verification.")
            checksums[f"{name}_sha256"] = expected
        manifest["completed_chunks"][str(z0)] = {"rows": [z0, z1], **checksums}
        manifest["completed_rows"] = len(manifest["completed_chunks"])
        self.save(identifier, manifest)
        return manifest
