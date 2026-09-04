"""Independent continuous ellipse sinograms, not projections from our voxelizer."""

from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
import zarr

from virtual_microscopy.reconstruction import (estimate_reconstruction, prepare_reconstruction,
                                                iter_reconstruction_slices)
from virtual_microscopy.reconstruction_schemas import ReconstructionRequest


def analytic_source(tmp_path, *, views=128, cols=192, rows=24, span=180,
                    width=6., height=2., offset_u=0., offset_v=0., rotation_center=None,
                    ellipse_center=(2., 2.), radii=(0.8, 0.8), mu=0.7, y_gradient=0.):
    """Exact Radon integral of an axis-aligned ellipse with linear Y amplitude."""
    identifier = str(uuid4())
    path = tmp_path / identifier
    path.mkdir()
    size = np.array([4., 2., 4.])
    rotation_center = np.asarray(rotation_center if rotation_center is not None else size / 2)
    angles = -23 + np.arange(views) * span / views
    theta = np.deg2rad(angles)
    d = np.column_stack((np.sin(theta), np.zeros(views), np.cos(theta)))
    eu = np.column_stack((np.cos(theta), np.zeros(views), -np.sin(theta)))
    ev = np.tile([0., 1., 0.], (views, 1))
    centers = rotation_center + offset_u * eu + offset_v * ev
    u = (np.arange(cols) + 0.5 - cols / 2) * width / cols
    v = (np.arange(rows) + 0.5 - rows / 2) * height / rows
    values = np.empty((views, rows, cols), dtype=np.float32)
    ax, az = radii
    for index in range(views):
        radius = np.sqrt((ax * eu[index, 0]) ** 2 + (az * eu[index, 2]) ** 2)
        shift = ((ellipse_center[0] - centers[index, 0]) * eu[index, 0] +
                 (ellipse_center[1] - centers[index, 2]) * eu[index, 2])
        chord = 2 * ax * az / radius * np.sqrt(np.maximum(0., 1 - ((u - shift) / radius) ** 2))
        world_y = centers[index, 1] + v
        coefficient = mu + y_gradient * world_y
        coefficient[(world_y < 0) | (world_y >= 2)] = 0
        values[index] = coefficient[:, None] * chord
    group = zarr.open_group(str(path / "data.zarr"), mode="w", zarr_format=3)
    for name, array in {"line_integrals": values, "valid_mask": np.ones_like(values)}.items():
        group.create_array(name, data=array, chunks=(1, rows, cols))
    for name, array in {"u_mm": u, "v_mm": v, "angles_deg": angles,
                        "ray_direction_xyz": d, "detector_u_xyz": eu,
                        "detector_v_xyz": ev, "detector_center_mm": centers}.items():
        group.create_array(name, data=array)
    manifest = {"dataset_id": identifier, "kind": "xray_projection_volume", "state": "completed",
                "complete": True, "shape": [views, rows, cols], "input_sha256": "analytic-test-source",
                "evidence_status": "Continuous analytical ellipse sinogram; no voxel projector used.",
                "request": {"twin": {"size_mm": size.tolist(), "objects": []}, "acquisition": {
                    "angle_span_deg": span, "rotation_center_mm": rotation_center.tolist(),
                    "detector_offset_u_mm": offset_u, "detector_offset_v_mm": offset_v}}}
    return path, manifest


def request(manifest, **changes):
    return ReconstructionRequest.model_validate({"source_dataset_id": manifest["dataset_id"],
        "reconstruction": {"nx": 64, "ny": 16, "nz": 64, "filter": "ram_lak", **changes}})


def reconstruct(path, manifest, **settings):
    prepared = prepare_reconstruction(request(manifest, **settings), manifest, path)
    try:
        arrays = list(iter_reconstruction_slices(prepared))
        result = {name: np.concatenate([values[name] for _, _, values in arrays]) for name in arrays[0][2]}
        coords = (prepared.x_mm.copy(), prepared.y_mm.copy(), prepared.z_mm.copy())
        return result, coords, prepared.metadata
    finally:
        prepared.close()


def test_continuous_disc_recovers_attenuation_in_inverse_mm(tmp_path):
    path, manifest = analytic_source(tmp_path)
    data, (x, y, z), metadata = reconstruct(path, manifest)
    radius = np.hypot(x[None, :] - 2, z[:, None] - 2)
    values = data["attenuation"][:, 8]
    assert values[radius < 0.55].mean() == pytest.approx(0.7, rel=0.02)
    assert np.quantile(abs(values[radius > 1.1]), 0.8) < 0.025
    assert np.all(data["coverage"] == 1)
    assert metadata["attenuation_unit"] == "mm^-1"
    assert data["attenuation"].shape == (64, 16, 64)
    assert not list(tmp_path.glob(".reconstruction-cache-*"))


def test_asymmetric_ellipse_offcenter_poses_and_multiple_y_planes(tmp_path):
    center = (2.55, 1.45)
    path, manifest = analytic_source(tmp_path, views=144, cols=216, rows=32, width=7.2, height=3,
                                    rotation_center=[1.5, 0.8, 1.7], offset_u=0.17, offset_v=0.12,
                                    ellipse_center=center, radii=(0.7, 0.4), mu=0.4, y_gradient=0.2)
    data, (x, y, z), _ = reconstruct(path, manifest)
    interior = ((x[None, :] - center[0]) / 0.7) ** 2 + ((z[:, None] - center[1]) / 0.4) ** 2 < 0.4
    for row in (1, 8, 14):
        assert data["attenuation"][:, row][interior].mean() == pytest.approx(0.4 + 0.2 * y[row], rel=0.03)
    plane = data["attenuation"][:, 8]
    selected = plane > (0.4 + 0.2 * y[8]) * 0.5
    assert np.broadcast_to(x, selected.shape)[selected].mean() == pytest.approx(center[0], abs=0.04)
    assert np.broadcast_to(z[:, None], selected.shape)[selected].mean() == pytest.approx(center[1], abs=0.04)


def test_180_and_redundant_360_scans_have_same_physical_normalization(tmp_path):
    path180, m180 = analytic_source(tmp_path, views=64, span=180)
    path360, m360 = analytic_source(tmp_path, views=128, span=360)
    data180, _, _ = reconstruct(path180, m180, nx=32, nz=32)
    data360, _, _ = reconstruct(path360, m360, nx=32, nz=32)
    np.testing.assert_allclose(data180["attenuation"], data360["attenuation"], atol=3e-7)


def test_refined_continuous_sinogram_improves_reconstruction_without_inverse_crime(tmp_path):
    coarse_path, coarse_m = analytic_source(tmp_path, views=24, cols=48)
    fine_path, fine_m = analytic_source(tmp_path, views=128, cols=192)
    coarse, (x, y, z), _ = reconstruct(coarse_path, coarse_m)
    fine, _, _ = reconstruct(fine_path, fine_m)
    radius = np.hypot(x[None, :] - 2, z[:, None] - 2)
    truth = (radius < 0.8) * 0.7
    rmse_coarse = np.sqrt(np.mean((coarse["attenuation"][:, 8] - truth) ** 2))
    rmse_fine = np.sqrt(np.mean((fine["attenuation"][:, 8] - truth) ** 2))
    assert rmse_fine < rmse_coarse * 0.75


def test_hann_and_cutoff_smooth_ringing_without_per_image_rescaling(tmp_path):
    path, manifest = analytic_source(tmp_path, cols=96, views=64)
    sharp, (x, y, z), _ = reconstruct(path, manifest)
    smooth, _, metadata = reconstruct(path, manifest, filter="hann", frequency_cutoff=0.7)
    sharp_variation = np.sum(np.diff(sharp["attenuation"][:, 8], axis=0) ** 2)
    smooth_variation = np.sum(np.diff(smooth["attenuation"][:, 8], axis=0) ** 2)
    assert smooth_variation < sharp_variation * 0.75
    radius = np.hypot(x[None, :] - 2, z[:, None] - 2)
    assert smooth["attenuation"][:, 8][radius < 0.45].mean() == pytest.approx(0.7, rel=0.03)
    assert metadata["filter_cutoff_cycles_per_mm"] == pytest.approx(0.7 / (2 * 6 / 96))


def test_supported_negative_reconstructed_values_are_not_clipped(tmp_path):
    # Signed projection logarithms can be negative; this tests the linear
    # inverse operator rather than prescribing a negative physical material.
    path, manifest = analytic_source(tmp_path, mu=-0.3, views=32, cols=96)
    data, (x, y, z), _ = reconstruct(path, manifest, nx=32, nz=32)
    interior = np.hypot(x[None, :] - 2, z[:, None] - 2) < 0.5
    assert data["attenuation"][:, 8][interior].mean() == pytest.approx(-0.3, rel=0.03)


def test_interpolated_invalid_input_is_counted_and_source_arrays_stay_unchanged(tmp_path):
    path, manifest = analytic_source(tmp_path, views=32, cols=96)
    group = zarr.open_group(str(path / "data.zarr"), mode="r+")
    group["line_integrals"][3, 8, 48] = 99.0
    group["valid_mask"][3, 8, 48] = 0
    with pytest.raises(ValueError, match="invalid zero-count"):
        prepare_reconstruction(request(manifest, invalid_policy="reject"), manifest, path)
    data, _, metadata = reconstruct(path, manifest, nx=32, nz=32, invalid_policy="interpolate")
    assert metadata["interpolated_invalid_samples"] == 1
    assert metadata["interpolated_detector_rows"] == 1
    assert float(group["line_integrals"][3, 8, 48]) == 99
    assert float(group["valid_mask"][3, 8, 48]) == 0
    assert np.all(data["coverage"] == 1)
    assert not list(tmp_path.glob(".reconstruction-cache-*"))


def test_invalid_row_with_insufficient_information_rejects_and_cleans_cache(tmp_path):
    path, manifest = analytic_source(tmp_path, views=16, cols=32)
    group = zarr.open_group(str(path / "data.zarr"), mode="r+")
    group["valid_mask"][2, 1, :] = 0
    with pytest.raises(ValueError, match="fewer than two"):
        prepare_reconstruction(request(manifest), manifest, path)
    assert not list(tmp_path.glob(".reconstruction-cache-*"))


def test_truncation_is_rejected_by_default_and_explicit_allow_masks_missing_support(tmp_path):
    path, manifest = analytic_source(tmp_path, views=32, cols=64, width=2, height=1)
    with pytest.raises(ValueError, match="truncates"):
        estimate_reconstruction(request(manifest), manifest, path)
    data, _, metadata = reconstruct(path, manifest, truncation_policy="allow", nx=32, nz=32)
    coverage = data["coverage"]
    assert np.any(coverage == 1)
    assert np.any((coverage > 0) & (coverage < 1))
    assert np.all(coverage[:, :4] == 0)
    assert np.all(data["attenuation"][coverage < 1 - 1e-6] == 0)
    assert metadata["truncated_view_indices"]


def test_half_pixel_edge_extension_is_covered_without_extrapolating_beyond_detector(tmp_path):
    path, manifest = analytic_source(tmp_path, views=32, cols=96, rows=16, y_gradient=0.2)
    data, (x, y, z), _ = reconstruct(path, manifest, nx=32, nz=32, ny=32)
    interior = np.hypot(x[None, :] - 2, z[:, None] - 2) < 0.5
    assert np.all(data["coverage"] == 1)
    # First output y=.03125 precedes the first input center at.0625; only the
    # stated half-pixel nearest-center extension is used, not extrapolation.
    assert data["attenuation"][:, 0][interior].mean() == pytest.approx(0.7 + 0.2 * 0.0625, rel=0.03)


def test_resume_slice_is_byte_identical_and_close_removes_only_owned_cache(tmp_path):
    path, manifest = analytic_source(tmp_path, views=16, cols=48)
    prepared = prepare_reconstruction(request(manifest, nx=16, nz=16), manifest, path)
    cache_path = prepared.cache_path
    try:
        assert (cache_path / "owner.json").is_file()
        full = list(iter_reconstruction_slices(prepared))
        resumed = list(iter_reconstruction_slices(prepared, start_slice=7))
        for previous, current in zip(full[7:], resumed):
            for name in previous[2]:
                np.testing.assert_array_equal(previous[2][name], current[2][name])
        assert list(iter_reconstruction_slices(prepared, start_slice=16)) == []
    finally:
        prepared.close()
    assert not cache_path.exists()
    assert path.exists()
    prepared.close()


@pytest.mark.parametrize("span,views,match", [(90, 32, "180"), (180, 12, "at least 16")])
def test_unsupported_angular_coverage_is_rejected(tmp_path, span, views, match):
    path, manifest = analytic_source(tmp_path, span=span, views=views, cols=48)
    with pytest.raises(ValueError, match=match):
        estimate_reconstruction(request(manifest), manifest, path)


def test_nonuniform_angles_and_noncanonical_poses_are_rejected(tmp_path):
    path, manifest = analytic_source(tmp_path, views=16, cols=48)
    group = zarr.open_group(str(path / "data.zarr"), mode="r+")
    original = float(group["angles_deg"][4])
    group["angles_deg"][4] = original + 0.1
    with pytest.raises(ValueError, match="uniformly"):
        estimate_reconstruction(request(manifest), manifest, path)
    group["angles_deg"][4] = original
    group["detector_u_xyz"][3, :] = [0, 1, 0]
    with pytest.raises(ValueError, match="canonical"):
        estimate_reconstruction(request(manifest), manifest, path)


def test_bounds_and_work_preflight_reject_before_preparation(tmp_path):
    path, manifest = analytic_source(tmp_path, views=128, cols=48)
    with pytest.raises(ValueError, match="within"):
        estimate_reconstruction(request(manifest, bounds_mm=[0, 5, 0, 2, 0, 4]), manifest, path)
    with pytest.raises(ValueError, match="work budget"):
        estimate_reconstruction(request(manifest, nx=256, ny=256, nz=256), manifest, path)
    assert not list(tmp_path.glob(".reconstruction-cache-*"))


def test_estimate_reads_only_coordinates_and_does_not_require_twin_materials(tmp_path):
    path, manifest = analytic_source(tmp_path, views=16, cols=48)
    group = zarr.open_group(str(path / "data.zarr"), mode="r+")
    group["line_integrals"][:] = np.nan
    manifest["request"]["twin"]["objects"] = [{"irrelevant": "not a material geometry"}]
    estimate = estimate_reconstruction(request(manifest, nx=16, nz=16), manifest, path)
    assert estimate["shape"] == [16, 16, 16]
    assert estimate["estimated_temporary_bytes"] == 16 * 16 * 48 * 4
    assert estimate["total_bytes"] == 16 ** 3 * 8 + (16 + 16 + 16) * 8
    with pytest.raises(ValueError, match="corrupted"):
        prepare_reconstruction(request(manifest), manifest, path)


def test_held_out_analytic_projection_residual_uses_an_independent_ray_sampler(tmp_path):
    from tools.verify_reconstruction import ellipse_projections, independent_ray_integrals

    path, manifest = analytic_source(tmp_path, views=128, cols=192,
                                    ellipse_center=(2.5, 1.45), radii=(0.7, 0.4))
    data, (x, y, z), _ = reconstruct(path, manifest)
    held_angles = np.array([-21.37, -7.11, 13.73, 47.39, 78.13, 104.61, 139.87])
    u = np.linspace(-2.9, 2.9, 121)
    training_angles = -23 + np.arange(128)*180/128
    assert np.min(abs(held_angles[:, None]-training_angles)) > 1e-8
    truth = ellipse_projections(held_angles, u, [y[8]], rotation_center=[2, 1, 2], offset_u=0,
                                ellipse_center=(2.5, 1.45), radii=(0.7, 0.4), mu=0.7, y_gradient=0)[:, 0]
    predicted = independent_ray_integrals(data["attenuation"][:, 8], x, z, held_angles, u,
                                          rotation_center=[2, 1, 2], offset_u=0)
    assert np.linalg.norm(predicted-truth)/np.linalg.norm(truth) < 0.08
