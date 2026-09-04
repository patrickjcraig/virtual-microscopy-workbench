"""Continuous-ellipse FBP validation with independent held-out ray integration.

Run: python -m tools.verify_reconstruction artifacts/fbp-validation-new
The target directory must not exist. No production forward projector is used.
"""

import argparse
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import numpy as np
from scipy.ndimage import map_coordinates
import zarr

from virtual_microscopy.reconstruction import prepare_reconstruction, iter_reconstruction_slices


def ellipse_projections(angles_deg, u_mm, world_y, *, rotation_center, offset_u,
                        ellipse_center, radii, mu, y_gradient):
    """Closed-form Radon transform of a continuous ellipse, independently of voxels."""
    ax, az = radii
    projections = np.empty((len(angles_deg), len(world_y), len(u_mm)))
    for k, angle in enumerate(angles_deg):
        cos, sin = np.cos(np.deg2rad(angle)), np.sin(np.deg2rad(angle))
        radius = np.hypot(ax * cos, az * sin)
        shift = ((ellipse_center[0] - rotation_center[0]) * cos -
                 (ellipse_center[1] - rotation_center[2]) * sin - offset_u)
        chord = 2 * ax * az / radius * np.sqrt(np.maximum(0, 1 - ((u_mm - shift) / radius) ** 2))
        projections[k] = (mu + y_gradient * np.asarray(world_y))[:, None] * chord
    return projections


def independent_ray_integrals(plane_zx, x_mm, z_mm, angles_deg, u_mm, *, rotation_center, offset_u, samples=2048):
    """Integrate a reconstructed plane using midpoint quadrature and bilinear values.

    This is deliberately different from acquisition voxel paths and from FBP's
    detector interpolation. It operates solely on the returned reconstruction.
    """
    xmin, xmax = x_mm[0] - (x_mm[1]-x_mm[0])/2, x_mm[-1] + (x_mm[1]-x_mm[0])/2
    zmin, zmax = z_mm[0] - (z_mm[1]-z_mm[0])/2, z_mm[-1] + (z_mm[1]-z_mm[0])/2
    half_length = 2 * np.hypot(xmax-xmin, zmax-zmin)
    ds = 2 * half_length / samples
    t = -half_length + (np.arange(samples) + 0.5) * ds
    result = np.empty((len(angles_deg), len(u_mm)))
    for k, angle in enumerate(angles_deg):
        cos, sin = np.cos(np.deg2rad(angle)), np.sin(np.deg2rad(angle))
        xx = rotation_center[0] + (u_mm[:, None] + offset_u) * cos + t * sin
        zz = rotation_center[2] - (u_mm[:, None] + offset_u) * sin + t * cos
        indices = np.stack(((zz-z_mm[0])/(z_mm[1]-z_mm[0]), (xx-x_mm[0])/(x_mm[1]-x_mm[0])))
        result[k] = map_coordinates(plane_zx, indices, order=1, mode="constant", cval=0, prefilter=False).sum(axis=1) * ds
    return result


def run_validation(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = perf_counter()
    source_id = str(uuid4())
    source_path = output / source_id
    source_path.mkdir()
    views, rows, cols = 128, 32, 216
    width, height = 7.2, 3.
    size = [4., 2., 4.]
    rotation_center = np.array([1.5, 0.8, 1.7])
    offset_u, offset_v = 0.17, 0.12
    ellipse_center, radii, mu, gradient = (2.55, 1.45), (0.7, 0.4), 0.4, 0.2
    angles = -23 + np.arange(views) * 180 / views
    u = (np.arange(cols)+0.5-cols/2)*width/cols
    v = (np.arange(rows)+0.5-rows/2)*height/rows
    world_y = rotation_center[1]+offset_v+v
    projections = ellipse_projections(angles, u, world_y, rotation_center=rotation_center,
        offset_u=offset_u, ellipse_center=ellipse_center, radii=radii, mu=mu, y_gradient=gradient)
    projections[:, (world_y < 0) | (world_y >= size[1]), :] = 0
    rad = np.deg2rad(angles)
    eu = np.column_stack((np.cos(rad), np.zeros(views), -np.sin(rad)))
    ev = np.tile([0., 1., 0.], (views, 1))
    group = zarr.open_group(str(source_path / "data.zarr"), mode="w", zarr_format=3)
    for name, values in {"line_integrals": projections.astype(np.float32),
                         "valid_mask": np.ones(projections.shape, dtype=np.float32)}.items():
        group.create_array(name, data=values, chunks=(1, rows, cols))
    for name, values in {"angles_deg": angles, "u_mm": u, "v_mm": v,
        "ray_direction_xyz": np.column_stack((np.sin(rad), np.zeros(views), np.cos(rad))),
        "detector_u_xyz": eu, "detector_v_xyz": ev,
        "detector_center_mm": rotation_center+offset_u*eu+offset_v*ev}.items():
        group.create_array(name, data=values)
    source_manifest = {"dataset_id": source_id, "kind": "xray_projection_volume", "complete": True,
        "state": "completed", "shape": [views, rows, cols],
        "evidence_status": "Continuous analytic ellipse validation fixture; not a catalog acquisition.",
        "request": {"twin": {"size_mm": size}, "acquisition": {"angle_span_deg": 180,
            "rotation_center_mm": rotation_center.tolist(), "detector_offset_u_mm": offset_u,
            "detector_offset_v_mm": offset_v}}}
    (source_path / "manifest.json").write_text(json.dumps(source_manifest, indent=2), encoding="utf-8")
    request = {"source_dataset_id": source_id, "reconstruction": {"nx": 64, "ny": 16,
        "nz": 64, "filter": "ram_lak", "frequency_cutoff": 1, "invalid_policy": "reject"}}
    prepared = prepare_reconstruction(request, source_manifest, source_path)
    try:
        volume = np.concatenate([arrays["attenuation"] for _, _, arrays in iter_reconstruction_slices(prepared)])
        x, y, z = prepared.x_mm.copy(), prepared.y_mm.copy(), prepared.z_mm.copy()
        estimate = prepared.estimate
    finally:
        prepared.close()
    row = len(y)//2
    truth = (((x[None, :]-ellipse_center[0])/radii[0])**2 +
             ((z[:, None]-ellipse_center[1])/radii[1])**2) < 1
    interior = (((x[None, :]-ellipse_center[0])/radii[0])**2 +
                ((z[:, None]-ellipse_center[1])/radii[1])**2) < 0.4
    coefficient = mu+gradient*y[row]
    plane = volume[:, row]
    held_angles = np.array([-21.37, -7.11, 13.73, 47.39, 78.13, 104.61, 139.87])
    if np.min(abs(held_angles[:, None]-angles)) < 1e-8:
        raise AssertionError("Held-out angles must not overlap acquisition angles.")
    held_u = np.linspace(-width/2+0.05, width/2-0.05, 121)
    held_truth = ellipse_projections(held_angles, held_u, [y[row]], rotation_center=rotation_center,
        offset_u=offset_u, ellipse_center=ellipse_center, radii=radii, mu=mu, y_gradient=gradient)[:, 0]
    held_prediction = independent_ray_integrals(plane, x, z, held_angles, held_u,
        rotation_center=rotation_center, offset_u=offset_u)
    result = {"evidence_status": "Independent synthetic numerical validation; no experimental accuracy claim.",
        "phantom": {"type": "continuous axis-aligned ellipse with linear Y attenuation", "center_xz_mm": ellipse_center,
                    "radii_xz_mm": radii, "attenuation_mm_inv": coefficient, "selected_y_mm": float(y[row])},
        "training": {"angles": views, "span_deg": 180, "detector_shape": [rows, cols],
                     "source": "closed-form Radon integrals; production forward projector not used"},
        "reconstruction": {"shape": list(volume.shape), "filter": "ram_lak", "estimate": estimate},
        "metrics": {"interior_mean_mm_inv": float(plane[interior].mean()),
            "interior_relative_bias": float(plane[interior].mean()/coefficient-1),
            "plane_rmse_mm_inv": float(np.sqrt(np.mean((plane-truth*coefficient)**2))),
            "held_out_projection_relative_l2": float(np.linalg.norm(held_prediction-held_truth)/np.linalg.norm(held_truth)),
            "held_out_projection_rmse": float(np.sqrt(np.mean((held_prediction-held_truth)**2)))},
        "held_out": {"angles_deg": held_angles.tolist(), "detector_u_samples": len(held_u),
                     "method": "2048 midpoint samples per ray; independent bilinear integration of reconstructed XZ array"},
        "runtime_seconds": round(perf_counter()-started, 3)}
    np.savez_compressed(output / "validation.npz", attenuation=volume, x_mm=x, y_mm=y, z_mm=z,
                        truth_xz_mm_inv=truth*coefficient, held_out_angles_deg=held_angles, held_out_u_mm=held_u,
                        held_out_true=held_truth, held_out_reconstructed=held_prediction)
    (output / "report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New output directory; existing directories are refused.")
    args = parser.parse_args()
    print(json.dumps(run_validation(args.output), indent=2))
