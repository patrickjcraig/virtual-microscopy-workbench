"""Derived attenuation volumes with frozen, inspectable projection provenance."""
from __future__ import annotations

from pathlib import Path
import json
import shutil

import numpy as np
import zarr

from .datasets import (DatasetStore, array_sha256, canonical_json, checked_id,
                       coordinate_sha256, json_sha256, solver_identity, validate_dataset_paths)


COVERAGE_TOLERANCE = 1e-6
CACHE_PREFIX = ".reconstruction-cache-"
CACHE_OWNER = {"schema_version": 1, "purpose": "virtual_microscopy_reconstruction_cache"}


def cleanup_reconstruction_caches(root: Path) -> None:
    """Remove only recognized orphan caches after the worker lock is released."""
    root = Path(root).resolve()
    for candidate in root.glob(f"{CACHE_PREFIX}*"):
        try:
            checked_id(candidate.name[len(CACHE_PREFIX):])
        except ValueError:
            continue
        # Recursive deletion is allowed only after resolving the final target
        # inside this exact data root and checking the app-owned marker.
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
        if owner == CACHE_OWNER:
            shutil.rmtree(target)


def reconstruction_source(root: Path, identifier: str, *, verify: bool = False) -> tuple[dict, Path]:
    """Resolve a completed source without following redirected stored paths."""
    from .xray_datasets import XrayDatasetStore
    root = Path(root).resolve()
    path = root / checked_id(identifier)
    if not path.exists() and not path.is_symlink():
        raise KeyError(identifier)
    validate_dataset_paths(path, identifier, include_arrays=False)
    store = XrayDatasetStore(root)
    manifest = store.manifest(identifier)
    if manifest.get("kind") != "xray_projection_volume":
        raise ValueError("Reconstruction requires a saved X-ray projection dataset.")
    if not manifest.get("complete") or manifest.get("state") != "completed":
        raise ValueError("Reconstruction requires a completed projection acquisition.")
    validate_dataset_paths(path, identifier)
    if verify:
        store.verify_complete(identifier)
    return manifest, path


class ReconstructionDatasetStore(DatasetStore):
    kind = "xray_reconstruction"
    axis_order = ("z", "y", "x")
    signal_units = {"attenuation": "mm^-1", "coverage": "fraction of angular views with geometric support"}
    evidence_status = "Filtered backprojection of synthetic X-ray measurements; not experimentally calibrated."

    def _solver_identity(self, model_version: str) -> dict:
        return solver_identity(model_version, self.kind)

    def _creation_provenance(self, request: dict) -> dict:
        source, _ = reconstruction_source(self.root, request["source_dataset_id"])
        return {"source_dataset_id": request["source_dataset_id"],
                "source_manifest": source, "source_manifest_sha256": json_sha256(source)}

    def _creation_materials(self, provenance: dict) -> dict:
        # These are source provenance, not a dependency on the current material
        # library. Reconstruction consumes saved measured projections only.
        return provenance["source_manifest"]["materials"]

    def _identity_payload(self, manifest: dict) -> dict:
        return {**super()._identity_payload(manifest),
                "source_manifest_sha256": manifest["source_manifest_sha256"]}

    def _verify_frozen_provenance(self, manifest: dict) -> None:
        source = manifest["source_manifest"]
        if json_sha256(source) != manifest["source_manifest_sha256"]:
            raise ValueError("Frozen projection source manifest checksum mismatch.")
        source_id = checked_id(manifest["request"]["source_dataset_id"])
        if source_id != manifest["source_dataset_id"] or source_id != source.get("dataset_id"):
            raise ValueError("Frozen reconstruction source identity mismatch.")
        if source.get("kind") != "xray_projection_volume" or not source.get("complete") or source.get("state") != "completed":
            raise ValueError("Frozen reconstruction source must be a completed X-ray projection dataset.")
        if canonical_json(manifest["materials"]) != canonical_json(source["materials"]):
            raise ValueError("Reconstruction material provenance differs from its projection source.")

    def _verify_current_dependencies(self, manifest: dict) -> None:
        self._source_for_manifest(manifest)

    def _source_for_manifest(self, manifest: dict, *, verify: bool = False) -> tuple[dict, Path]:
        current, path = reconstruction_source(self.root, manifest["source_dataset_id"], verify=verify)
        if json_sha256(current) != manifest["source_manifest_sha256"]:
            raise ValueError("Projection source manifest changed; create a new reconstruction instead of resuming.")
        return current, path

    def source_context(self, identifier: str, *, verify: bool = False) -> tuple[dict, Path]:
        manifest = self.manifest(identifier)
        self._verify_frozen_provenance(manifest)
        return self._source_for_manifest(manifest, verify=verify)

    def _coordinate_descriptors(self, shape: list[int]) -> dict:
        nz, ny, nx = shape
        return {name: {"path": name, "shape": [size], "units": "mm", "axes": [axis], "dtype": "float64"}
                for name, size, axis in (("x_mm", nx, "x"), ("y_mm", ny, "y"), ("z_mm", nz, "z"))}

    def _signal_descriptors(self, shape: list[int], request: dict) -> dict:
        descriptors = super()._signal_descriptors(shape, request)
        descriptors["attenuation"]["note"] = "Finite zero placeholders where coverage < 1-1e-6 are masked data, not air or quantitative attenuation. Supported negative values are retained."
        descriptors["coverage"]["note"] = "Geometric angular-support fraction only; this is not confidence, accuracy or quality."
        return descriptors

    def create(self, identifier: str, request: dict, estimate: dict) -> dict:
        if request.get("kind") != self.kind or estimate.get("kind") != self.kind:
            raise ValueError("A reconstruction dataset requires an explicit reconstruction request and estimate.")
        if estimate["tile_rows"] != 1:
            raise ValueError("Reconstruction datasets commit one z slice per chunk.")
        return super().create(identifier, request, estimate)

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
                raise ValueError(f"Invalid reconstruction coordinate array: {name}.")
        group = zarr.open_group(str(self.path(identifier) / "data.zarr"), mode="w", zarr_format=3)
        group.attrs.update({"dataset_id": identifier, "kind": self.kind,
                            "input_sha256": manifest["input_sha256"], "axis_order": list(self.axis_order),
                            "source_dataset_id": manifest["source_dataset_id"],
                            "source_manifest_sha256": manifest["source_manifest_sha256"],
                            "evidence_status": self.evidence_status})
        nz, ny, nx = manifest["shape"]
        for name in self.signal_units:
            group.create_array(name, shape=(nz, ny, nx), chunks=(1, ny, nx), dtype="float32",
                               fill_value=float("nan"), dimension_names=self.axis_order)
        for name, values in coords.items():
            group.create_array(name, data=values, dimension_names=tuple(descriptors[name]["axes"]))
        manifest.update(arrays_initialized=True, metadata=prepared.metadata,
                        coordinates_sha256={name: coordinate_sha256(values) for name, values in coords.items()})
        self.save(identifier, manifest)
        return manifest

    @staticmethod
    def _verify_coordinates(group, manifest: dict) -> None:
        nz, ny, nx = manifest["shape"]
        lengths = {"x_mm": nx, "y_mm": ny, "z_mm": nz}
        if set(manifest["coordinates_sha256"]) != set(lengths):
            raise ValueError("Reconstruction coordinate checksum registry is incomplete.")
        for name, length in lengths.items():
            if group[name].shape != (length,) or group[name].dtype != np.dtype("float64"):
                raise ValueError(f"Invalid {name} coordinate shape or type.")
            if coordinate_sha256(group[name][:]) != manifest["coordinates_sha256"][name]:
                raise ValueError(f"Coordinate checksum mismatch: {name}.")
        if (group.attrs.get("source_dataset_id") != manifest["source_dataset_id"] or
                group.attrs.get("source_manifest_sha256") != manifest["source_manifest_sha256"]):
            raise ValueError("Stored reconstruction source identity mismatch.")

    def write_slice(self, identifier: str, z0: int, z1: int, arrays: dict) -> dict:
        manifest = self.manifest(identifier)
        if manifest["complete"]:
            raise ValueError("Completed datasets are immutable.")
        nz, ny, nx = manifest["shape"]
        if z0 < 0 or z0 >= nz or z1 != z0 + 1:
            raise ValueError("A reconstruction chunk must contain exactly one valid z slice.")
        if str(z0) in manifest["completed_chunks"]:
            raise ValueError("Committed z slices must be verified and skipped, not overwritten.")
        if set(arrays) != set(self.signal_units):
            raise ValueError("Reconstruction chunks require attenuation and coverage.")
        if any(np.asarray(a).shape != (1, ny, nx) or not np.isfinite(a).all() for a in arrays.values()):
            raise ValueError("Reconstruction arrays must be finite with the declared [1,y,x] shape.")
        if any(np.any(np.abs(a) > np.finfo(np.float32).max) for a in arrays.values()):
            raise ValueError("Reconstruction data cannot overflow float32 storage.")
        coverage = np.asarray(arrays["coverage"])
        if np.any(coverage < 0) or np.any(coverage > 1):
            raise ValueError("Coverage must be a geometric angular-support fraction between 0 and 1.")
        if np.any(np.asarray(arrays["attenuation"])[coverage < 1 - COVERAGE_TOLERANCE] != 0):
            raise ValueError("Unsupported reconstruction samples must use zero placeholders with their coverage mask.")
        data = {name: np.ascontiguousarray(values, dtype="<f4") for name, values in arrays.items()}
        group = self.open_arrays(identifier, "r+")
        checksums = {}
        for name, values in data.items():
            group[name][z0:z1] = values
            expected = array_sha256(values)
            if array_sha256(group[name][z0:z1]) != expected:
                raise OSError(f"{name} reconstruction chunk failed write verification.")
            checksums[f"{name}_sha256"] = expected
        manifest["completed_chunks"][str(z0)] = {"rows": [z0, z1], **checksums}
        manifest["completed_rows"] = len(manifest["completed_chunks"])
        self.save(identifier, manifest)
        return manifest
