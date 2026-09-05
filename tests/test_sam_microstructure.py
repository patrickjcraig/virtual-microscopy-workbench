"""Conservative SAM resource bounds and explicit microfeature sampling checks."""

import numpy as np
import pytest

from virtual_microscopy.physics import MaterialGrid, acoustic_echoes, voxelize
from virtual_microscopy.sam_volume import (_interface_bound_map, _layout, estimate_sam,
                                           iter_sam_tiles, prepare_sam)
from virtual_microscopy.volume_schemas import SamVolumeRequest


def primitive(identifier, shape, material, center, size, role="structure"):
    return {"id": identifier, "name": identifier, "shape": shape, "material": material,
            "center_mm": center, "size_mm": size, "role": role}


def config(objects, **settings):
    return SamVolumeRequest.model_validate({"twin": {"name": "Independent overlapping primitives",
        "size_mm": [1, .8, 1.2], "objects": objects}, "acquisition": {
            "scan_nx": 31, "scan_ny": 23, "depth_samples": 128, "focus_mm": .4,
            "frequency_mhz": 100, "sample_rate_mhz": 800, "record_duration_us": .1,
            "roi_mm": [.17, .09, .77, .69], **settings}})


def bound_and_truth(request):
    p = _layout(request)
    bound = _interface_bound_map(request, p)
    a = request.acquisition
    grid = voxelize(request.twin.model_dump(mode="json"), (a.scan_nx, a.scan_ny), a.include_defects,
                    roi_mm=a.roi_mm, depth_samples=a.depth_samples,
                    halo_pixels=(p["halo_x"], p["halo_y"]))
    labels = grid.labels
    # This stronger count includes all sampled label boundaries, even those
    # whose acoustic amplitude falls below the solver's primary-echo threshold.
    actual = (labels[:, :, 0] != 0).astype(int) + (labels[:, :, -1] != 0)
    actual += np.count_nonzero(labels[:, :, 1:] != labels[:, :, :-1], axis=2)
    echoes = acoustic_echoes(grid, a.frequency_mhz, a.focus_mm, apply_focus=False)
    echo_counts = np.zeros(bound.shape, dtype=int)
    np.add.at(echo_counts, (echoes.rows, echoes.cols), 1)
    assert bound.shape == grid.labels.shape[:2]
    assert np.all(bound >= actual)
    assert np.all(bound >= echo_counts)
    return p, bound, grid, actual


@pytest.mark.parametrize("seed", [5, 17, 37, 53, 91])
@pytest.mark.parametrize("include_defects", [False, True])
def test_column_bound_covers_random_overlapping_primitives(seed, include_defects):
    rng = np.random.default_rng(seed)
    objects = [primitive("background", "box", "epoxy", [.5, .4, .6], [1, .8, 1.2])]
    for i in range(36):
        shape = ("box", "sphere", "cylinder")[i % 3]
        extent = rng.uniform(.018, .32, 3)
        if shape != "box":
            extent[1] = extent[0]
        if shape == "sphere":
            extent[2] = extent[0]
        center = rng.uniform(extent / 2, np.array([1, .8, 1.2]) - extent / 2)
        objects.append(primitive(f"object-{i}", shape, ("silicon", "copper", "air", "solder")[i % 4],
                                 center.tolist(), extent.tolist(), "defect" if i % 5 == 0 else "structure"))
    _, bound, _, actual = bound_and_truth(config(objects, include_defects=include_defects))
    assert actual.max() > 2
    assert bound.max() < min(129, 2 * len(objects))


def test_edge_centers_empty_depth_support_and_clipping_do_not_underbound():
    objects = [
        primitive("edge", "box", "silicon", [.25, .2, .6], [.5, .4, 1.2]),
        primitive("sphere", "sphere", "copper", [.5, .4, .6], [.2, .2, .2]),
        primitive("cylinder", "cylinder", "solder", [.75, .65, .7], [.1, .1, .4]),
        primitive("between-z-centers", "box", "copper", [.5, .4, .3], [.9, .7, .0001]),
    ]
    request = config(objects, scan_nx=32, scan_ny=24, roi_mm=[0, 0, 1, .8])
    _, bound, grid, _ = bound_and_truth(request)
    without = config(objects[:-1], scan_nx=32, scan_ny=24, roi_mm=[0, 0, 1, .8])
    np.testing.assert_array_equal(bound, _interface_bound_map(without, _layout(without)))
    assert grid.origin_mm.tolist() == [0, 0, 0]


def test_bound_clamps_to_number_of_sampled_interfaces():
    objects = [primitive(f"overlap-{i}", "box", "silicon", [.5, .4, .6], [1, .8, 1.2]) for i in range(100)]
    _, bound, _, actual = bound_and_truth(config(objects))
    assert np.all(bound == 129)
    assert np.all(actual == 2)


def test_canonical_tile_bound_includes_full_halo_and_final_partial_tile():
    objects = [primitive("halo-only", "cylinder", "copper", [.16, .31, .6], [.04, .04, .4]),
               primitive("lower", "box", "silicon", [.55, .60, .6], [.4, .2, .8]),
               primitive("upper", "sphere", "solder", [.30, .15, .45], [.2, .2, .2])]
    request = config(objects, scan_nx=40, scan_ny=27, roi_mm=[.18, .1, .78, .7], frequency_mhz=50,
                     sample_rate_mhz=400)
    p, bound, grid, _ = bound_and_truth(request)
    estimate = estimate_sam(request)
    chunk, counts = estimate["tile_rows"], []
    for start in range(0, request.acquisition.scan_ny, chunk):
        stop = min(start + chunk, request.acquisition.scan_ny)
        lo = max(0, grid.image_slices[0].start + start - p["halo_y"])
        hi = min(grid.labels.shape[0], grid.image_slices[0].start + stop + p["halo_y"])
        tile = MaterialGrid(grid.labels[lo:hi], grid.pitch_mm * [grid.labels.shape[1], hi-lo, 128],
                            grid.pitch_mm, [], grid.origin_mm + [0, lo * p["dy"], 0])
        count = int(bound[lo:hi].sum())
        assert count >= len(acoustic_echoes(tile, 50, .4).times_us)
        counts.append(count)
    assert estimate["maximum_tile_interfaces"] == max(counts)
    assert estimate["echo_workspace_bytes"] == 160 * max(counts)
    assert estimate["interface_bound_map_bytes"] == bound.nbytes
    assert estimate["interface_bound_workspace_bytes"] >= bound.nbytes
    assert bound[:, :grid.image_slices[1].start].sum() > 0
    assert request.acquisition.scan_ny % chunk != 0


def patched_h100(defects=()):
    from tools.build_h100_example import h100
    from virtual_microscopy.hbm import compose_hbm
    return compose_hbm(h100(), "hbm-6", {"microstructure": {"defects": list(defects)}})


def patch_config(twin, **settings):
    center = twin["hbm_assemblies"][5]["center_xy_mm"]
    x, y = center
    return SamVolumeRequest.model_validate({"twin": twin, "acquisition": {
        "scan_nx": 64, "scan_ny": 64, "depth_samples": 1024, "focus_mm": .6,
        "frequency_mhz": 100, "sample_rate_mhz": 800, "record_duration_us": .5,
        "roi_mm": [x-.06, y-.09, x+.06, y+.09], **settings}})


def test_microfeature_sampling_is_per_axis_and_preset_fits_existing_budgets():
    estimate = estimate_sam(patch_config(patched_h100()))
    features = estimate["microfeature_sampling"]
    assert estimate["primitive_count"] == 574
    assert sum(f["layer_role"] == "microbump" for f in features) == 48
    assert sum(f["layer_role"] == "tsv" for f in features) == 54
    bump = next(f for f in features if f["layer_role"] == "microbump")
    tsv = next(f for f in features if f["layer_role"] == "tsv" and f["size_um"][2] == 50)
    np.testing.assert_allclose(bump["samples_xyz"], np.array([25, 25, 15]) / [1.875, 2.8125, 2650/1024])
    np.testing.assert_allclose(tsv["samples_xyz"], np.array([10, 10, 50]) / [1.875, 2.8125, 2650/1024])
    assert all(f["intersects_roi"] and f["included"] for f in features)
    assert not any(f["undersampled_axes"] for f in features)
    assert estimate["grid_shape"] == [100, 118, 1024]
    assert estimate["tile_rows"] == 8
    assert estimate["estimated_peak_bytes"] < 512 * 1024**2
    assert estimate["rf_work_cells"] < 180_000_000
    assert estimate["total_bytes"] == 13_144_200


def test_compiled_h100_microstructure_bound_exceeds_actual_interfaces():
    request = patch_config(patched_h100([{"id": "missing", "kind": "missing_bump", "row": 3,
                                         "column": 2, "layer_index": 8}]))
    _, bound, _, actual = bound_and_truth(request)
    assert bound.sum() >= actual.sum() > 0
    assert estimate_sam(request)["maximum_column_interfaces"] == int(bound.max())


def test_saved_signed_rf_changes_for_missing_bump_and_exclusion_restores_nominal():
    intact = patched_h100()
    defective = patched_h100([{"id": "missing", "kind": "missing_bump", "row": 3,
                               "column": 2, "layer_index": 8}])

    def acquire(twin, include_defects):
        prepared = prepare_sam(patch_config(twin, scan_nx=16, scan_ny=16, depth_samples=512,
                                            record_start_us=.2, focus_mm=.3, include_defects=include_defects))
        tiles = list(iter_sam_tiles(prepared))
        return prepared, np.concatenate([tile[2] for tile in tiles]), np.concatenate([tile[3] for tile in tiles])

    _, nominal_rf, _ = acquire(intact, True)
    prepared, defective_rf, defective_envelope = acquire(defective, True)
    assert np.max(abs(defective_rf-nominal_rf)) > 1e-3
    assert defective_rf.min() < 0 < defective_rf.max()
    assert np.all(defective_envelope >= 0)
    assert np.max(defective_envelope-abs(defective_rf)) > 1e-3
    _, baseline_excluded, baseline_envelope = acquire(intact, False)
    _, defect_excluded, excluded_envelope = acquire(defective, False)
    np.testing.assert_array_equal(defect_excluded, baseline_excluded)
    np.testing.assert_array_equal(excluded_envelope, baseline_envelope)
    resumed = list(iter_sam_tiles(prepared, start_row=8))
    np.testing.assert_array_equal(np.concatenate([tile[2] for tile in resumed]), defective_rf[8:])
    np.testing.assert_array_equal(np.concatenate([tile[3] for tile in resumed]), defective_envelope[8:])
    assert prepared.metadata["microfeature_sampling"] == prepared.estimate["microfeature_sampling"]


def test_void_sampling_warnings_respect_defect_exclusion_and_padded_domain():
    twin = patched_h100([{"id": "tiny", "kind": "bump_void", "row": 3, "column": 2,
                          "layer_index": 2, "void_diameter_um": 3}])
    requested = patch_config(twin)
    estimate = estimate_sam(requested)
    void = next(f for f in estimate["microfeature_sampling"] if f["role"] == "defect")
    assert void["undersampled_axes"] == ["x", "y", "z"]
    assert any(void["id"] in message and "fewer than two" in message for message in estimate["warnings"])
    excluded = estimate_sam(patch_config(twin, include_defects=False))
    assert not next(f for f in excluded["microfeature_sampling"] if f["role"] == "defect")["included"]
    assert not any(void["id"] in message for message in excluded["warnings"])
    x, y = twin["hbm_assemblies"][5]["center_xy_mm"]
    halo = estimate_sam(patch_config(twin, roi_mm=[x+.027, y-.09, x+.147, y+.09]))
    halo_void = next(f for f in halo["microfeature_sampling"] if f["role"] == "defect")
    assert not halo_void["intersects_roi"] and halo_void["intersects_geometry_domain"]
    assert any(void["id"] in message for message in halo["warnings"])
    remote = estimate_sam(patch_config(twin, roi_mm=[0, 0, .12, .18]))
    assert not any(f["intersects_geometry_domain"] for f in remote["microfeature_sampling"])
    assert not any(void["id"] in message for message in remote["warnings"])


def test_microstructure_low_frequency_halo_failure_precedes_voxel_allocation(monkeypatch):
    import virtual_microscopy.sam_volume as engine

    def forbidden(*args, **kwargs):
        pytest.fail("Inadmissible requests must fail before geometry allocation.")

    monkeypatch.setattr(engine, "voxelize", forbidden)
    with pytest.raises(ValueError, match="64 million|working memory|computation budget"):
        prepare_sam(patch_config(patched_h100(), frequency_mhz=10, sample_rate_mhz=80))
