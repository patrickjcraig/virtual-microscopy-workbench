"""Persisted parallel-beam X-ray views with explicit detector poses and masks.

The common DatasetStore owns immutable inputs, atomic manifests, and checksummed
chunk commits. This subclass defines X-ray axes, physical coordinates, and the
relationship between observed counts and the derived projection arrays.
"""
from __future__ import annotations

import numpy as np
import zarr

from .datasets import DatasetStore, array_sha256, coordinate_sha256, solver_identity


class XrayDatasetStore(DatasetStore):
    kind = "xray_projection_volume"
    axis_order = ("view", "v", "u")
    signal_units = {
        "counts": "photons per detector pixel",
        "transmission": "observed counts divided by incident photons",
        "line_integrals": "dimensionless regularized negative log transmission",
        "valid_mask": "1 for an unregularized logarithm; 0 where zero counts require a log placeholder",
    }
    evidence_status = "Synthetic monoenergetic parallel-beam X-ray projections; not experimentally calibrated or CT reconstruction."
    pose_names = ("ray_direction_xyz", "detector_center_mm", "detector_u_xyz", "detector_v_xyz")
    coordinate_names = ("angles_deg", "u_mm", "v_mm", *pose_names)

    def _solver_identity(self, model_version: str) -> dict:
        return solver_identity(model_version, self.kind)

    def _signal_descriptors(self, shape: list[int], request: dict) -> dict:
        result = super()._signal_descriptors(shape, request)
        result["counts"]["units"] = ("observed photon counts per detector pixel" if request["acquisition"]["noise"] else
                                       "expected photons per detector pixel")
        result["valid_mask"]["note"] = "Zero counts remain valid count measurements; this mask only marks where an unregularized logarithm is undefined."
        return result

    def _coordinate_descriptors(self, shape: list[int]) -> dict:
        views, rows, cols = shape
        return {
            "angles_deg": {"path": "angles_deg", "shape": [views], "units": "degrees", "axes": ["view"], "dtype": "float64"},
            "u_mm": {"path": "u_mm", "shape": [cols], "units": "mm", "axes": ["u"], "dtype": "float64"},
            "v_mm": {"path": "v_mm", "shape": [rows], "units": "mm", "axes": ["v"], "dtype": "float64"},
            **{name: {"path": name, "shape": [views, 3], "units": "mm" if name == "detector_center_mm" else "unit vector",
                      "axes": ["view", "xyz"], "dtype": "float64"} for name in self.pose_names},
        }

    def create(self, identifier: str, request: dict, estimate: dict) -> dict:
        if request.get("kind") != self.kind or estimate.get("kind") != self.kind:
            raise ValueError("An X-ray dataset requires an explicit X-ray request and estimate.")
        if estimate["tile_rows"] != 1:
            raise ValueError("X-ray projections commit exactly one view per chunk.")
        return super().create(identifier, request, estimate)

    def initialize_arrays(self, identifier: str, prepared) -> dict:
        manifest = self.manifest(identifier)
        if manifest["complete"]:
            raise ValueError("Completed datasets are immutable.")
        if manifest["arrays_initialized"]:
            return manifest
        if manifest["completed_chunks"]:
            raise ValueError("Uninitialized dataset unexpectedly contains committed chunks.")
        coords = {name: np.ascontiguousarray(getattr(prepared, name), dtype="<f8")
                  for name in self.coordinate_names}
        descriptors = self._coordinate_descriptors(manifest["shape"])
        for name, values in coords.items():
            if list(values.shape) != descriptors[name]["shape"] or not np.isfinite(values).all():
                raise ValueError(f"Invalid X-ray coordinate or pose array: {name}.")
        self._validate_poses(coords)
        group = zarr.open_group(str(self.path(identifier) / "data.zarr"), mode="w", zarr_format=3)
        group.attrs.update({"dataset_id": identifier, "kind": self.kind,
                            "input_sha256": manifest["input_sha256"], "axis_order": list(self.axis_order),
                            "evidence_status": self.evidence_status})
        views, rows, cols = manifest["shape"]
        for name in self.signal_units:
            group.create_array(name, shape=(views, rows, cols), chunks=(1, rows, cols), dtype="float32",
                               fill_value=float("nan"), dimension_names=self.axis_order)
        for name, values in coords.items():
            group.create_array(name, data=values, dimension_names=tuple(descriptors[name]["axes"]))
        manifest.update(arrays_initialized=True, metadata=prepared.metadata,
                        coordinates_sha256={name: coordinate_sha256(values) for name, values in coords.items()})
        self.save(identifier, manifest)
        return manifest

    @staticmethod
    def _validate_poses(coords: dict) -> None:
        # Do not prescribe handedness here: ray direction describes travel,
        # while the detector vectors describe increasing detector coordinates.
        vectors = [coords[name] for name in ("ray_direction_xyz", "detector_u_xyz", "detector_v_xyz")]
        if any(not np.allclose(np.linalg.norm(v, axis=1), 1, rtol=0, atol=1e-10) for v in vectors):
            raise ValueError("X-ray ray and detector basis vectors must have unit length.")
        if any(not np.allclose(np.sum(vectors[a] * vectors[b], axis=1), 0, rtol=0, atol=1e-10)
               for a, b in ((0, 1), (0, 2), (1, 2))):
            raise ValueError("X-ray ray and detector basis vectors must be orthogonal.")

    @staticmethod
    def _verify_coordinates(group, manifest: dict) -> None:
        views, rows, cols = manifest["shape"]
        shapes = {"angles_deg": (views,), "u_mm": (cols,), "v_mm": (rows,),
                  **{name: (views, 3) for name in XrayDatasetStore.pose_names}}
        if set(manifest["coordinates_sha256"]) != set(shapes):
            raise ValueError("X-ray coordinate and pose checksum registry is incomplete.")
        coordinates = {}
        for name, shape in shapes.items():
            if group[name].shape != shape or group[name].dtype != np.dtype("float64"):
                raise ValueError(f"Invalid {name} coordinate shape or type.")
            values = group[name][:]
            if coordinate_sha256(values) != manifest["coordinates_sha256"][name]:
                raise ValueError(f"Coordinate checksum mismatch: {name}.")
            coordinates[name] = values
        XrayDatasetStore._validate_poses(coordinates)

    def write_view(self, identifier: str, view0: int, view1: int, arrays: dict) -> dict:
        manifest = self.manifest(identifier)
        if manifest["complete"]:
            raise ValueError("Completed datasets are immutable.")
        views, rows, cols = manifest["shape"]
        if view0 < 0 or view0 >= views or view1 != view0 + 1:
            raise ValueError("A projection chunk must contain exactly one valid view.")
        if str(view0) in manifest["completed_chunks"]:
            raise ValueError("Committed views must be verified and skipped, not overwritten.")
        if set(arrays) != set(self.signal_units):
            raise ValueError("Projection chunks require counts, transmission, line_integrals and valid_mask.")
        if any(np.asarray(a).shape != (1, rows, cols) or not np.isfinite(a).all() for a in arrays.values()):
            raise ValueError("Projection arrays must be finite and match the declared [1, v, u] shape.")
        acquisition = manifest["request"]["acquisition"]
        observed = np.asarray(arrays["counts"])
        if np.any(observed < 0):
            raise ValueError("Observed photon counts cannot be negative.")
        if acquisition["noise"] and (np.any(observed > 2 ** 24) or np.any(observed != np.floor(observed))):
            raise ValueError("Observed Poisson counts must be integers exactly representable in float32.")
        if not acquisition["noise"] and np.any(observed > acquisition["photons"]):
            raise ValueError("Noiseless expected counts cannot exceed the incident photons.")
        if any(np.any(np.abs(value) > np.finfo(np.float32).max) for value in arrays.values()):
            raise ValueError("Projection data cannot overflow float32 storage.")
        data = {name: np.ascontiguousarray(value, dtype="<f4") for name, value in arrays.items()}
        if any(not np.isfinite(a).all() for a in data.values()):
            raise ValueError("Projection data cannot overflow float32 storage.")
        expected_mask = (data["counts"] > 0).astype(np.float32)
        if not np.array_equal(data["valid_mask"], expected_mask):
            raise ValueError("Valid mask must be binary and must exclude zero-count samples.")
        counts64 = data["counts"].astype(np.float64)
        expected_transmission = counts64 / acquisition["photons"]
        expected_log = -np.log(np.where(counts64 > 0, counts64, 0.5) / acquisition["photons"])
        if not np.allclose(data["transmission"], expected_transmission, rtol=5e-7, atol=1e-44):
            raise ValueError("Transmission must equal stored counts divided by incident photons.")
        if not np.allclose(data["line_integrals"], expected_log, rtol=5e-7, atol=1e-6):
            raise ValueError("Line integrals must use the declared zero-only half-count substitution.")
        group = self.open_arrays(identifier, "r+")
        checksums = {}
        for name, values in data.items():
            group[name][view0:view1] = values
            expected = array_sha256(values)
            if array_sha256(group[name][view0:view1]) != expected:
                raise OSError(f"{name} projection chunk failed write verification.")
            checksums[f"{name}_sha256"] = expected
        manifest["completed_chunks"][str(view0)] = {"rows": [view0, view1], **checksums}
        manifest["completed_rows"] = len(manifest["completed_chunks"])
        self.save(identifier, manifest)
        return manifest
