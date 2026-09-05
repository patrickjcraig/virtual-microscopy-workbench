"""Independent waveform and travel-time checks for declared SAM depth mapping."""

from copy import deepcopy
from uuid import uuid4

import numpy as np
import pytest
from pydantic import ValidationError
import zarr

from virtual_microscopy.depth_schemas import SamDepthRequest
from virtual_microscopy.depth_mapping import estimate_depth, prepare_depth, iter_depth_slices


def source(tmp_path, *, time=None, rf_function=None, envelope_function=None, water_delay=0.4,
           nx=24, ny=16, depth=3):
    identifier = str(uuid4())
    path = tmp_path / identifier
    path.mkdir()
    time = np.asarray(time if time is not None else np.linspace(0.3, 3.0, 541), dtype=np.float64)
    x = 42+(np.arange(nx)+0.5)*6/nx
    y = 5+(np.arange(ny)+0.5)*4/ny
    gain = 1+np.arange(ny)[:, None]*0.01+np.arange(nx)[None, :]*0.001
    rf = (rf_function(time) if rf_function else 2*time-2)[None, None, :]*gain[:, :, None]
    envelope = (envelope_function(time) if envelope_function else 2+0.2*time)[None, None, :]*gain[:, :, None]
    group = zarr.open_group(str(path / "data.zarr"), mode="w", zarr_format=3)
    group.create_array("rf", data=rf.astype(np.float32), chunks=(8, nx, len(time)))
    group.create_array("envelope", data=envelope.astype(np.float32), chunks=(8, nx, len(time)))
    for name, values in (("x_mm", x), ("y_mm", y), ("time_us", time)):
        group.create_array(name, data=values)
    manifest = {"dataset_id": identifier, "kind": "sam_rf_volume", "complete": True, "state": "completed",
        "shape": [ny, nx, len(time)], "input_sha256": "independent-synthetic-waveform",
        "evidence_status": "Independent synthetic waveform test source.",
        "request": {"twin": {"size_mm": [60, 10, depth], "objects": [{"unused": "velocity must not come from labels"}]}},
        "metadata": {"water_round_trip_delay_us": water_delay},
        "estimate": {"water_round_trip_delay_us": water_delay}}
    return path, manifest, gain


def request(manifest, **changes):
    return SamDepthRequest.model_validate({"source_dataset_id": manifest["dataset_id"], "mapping": {
        "nz": 64, "z_max_mm": 2, "sound_speed_m_s": 2000, **changes}})


def acquire(path, manifest, **changes):
    prepared = prepare_depth(request(manifest, **changes), manifest, path)
    try:
        slices = list(iter_depth_slices(prepared))
        arrays = {name: np.concatenate([data[name] for _, _, data in slices]) for name in slices[0][2]}
        coords = (prepared.x_mm.copy(), prepared.y_mm.copy(), prepared.z_mm.copy(), prepared.travel_time_us.copy())
        return arrays, coords, prepared.metadata
    finally:
        prepared.close()


def test_homogeneous_mapping_preserves_signed_rf_and_independent_envelope(tmp_path):
    path, manifest, gain = source(tmp_path)
    data, (x, y, z, time), meta = acquire(path, manifest)
    np.testing.assert_allclose(time, 0.4+z, atol=1e-14)
    np.testing.assert_allclose(data["rf"], (2*time-2)[:, None, None]*gain, atol=5e-7)
    np.testing.assert_allclose(data["envelope"], (2+0.2*time)[:, None, None]*gain, atol=5e-7)
    assert np.any(data["rf"] < 0)
    assert np.any(data["rf"] > 0)
    assert not np.allclose(data["envelope"], abs(data["rf"]))
    assert np.all(data["valid_mask"] == 1)
    assert meta["surface_time_us"] == 0.4
    assert meta["source_time_range_us"] == [0.3, 3.0]


def test_analytical_reflector_arrives_at_declared_depth_with_negative_polarity(tmp_path):
    reflector_depth = 1.265625
    reflector_time = 0.4+2*reflector_depth/5
    sigma, carrier = 0.018, 12
    rf = lambda t: -0.8*np.exp(-0.5*((t-reflector_time)/sigma)**2)*np.cos(2*np.pi*carrier*(t-reflector_time))
    env = lambda t: 0.8*np.exp(-0.5*((t-reflector_time)/sigma)**2)
    path, manifest, gain = source(tmp_path, rf_function=rf, envelope_function=env,
                                 time=np.arange(0.3, 2.0001, 0.0025))
    data, (_, _, z, time), _ = acquire(path, manifest, sound_speed_m_s=5000, nz=96, z_max_mm=3)
    k = np.argmax(data["envelope"][:, 3, 17])
    assert z[k] == pytest.approx(reflector_depth, abs=(3/96)/2)
    assert time[k] == pytest.approx(reflector_time, abs=1e-12)
    assert data["rf"][k, 3, 17] < 0
    assert data["envelope"][k, 3, 17] == pytest.approx(0.8*gain[3, 17], rel=0.004)


def test_layered_travel_times_integrate_from_zero_and_do_not_extend_last_layer(tmp_path):
    path, manifest, _ = source(tmp_path, time=np.linspace(0, 4, 801), water_delay=0.2)
    layers = [{"end_depth_mm": 0.5, "sound_speed_m_s": 1000},
              {"end_depth_mm": 1.5, "sound_speed_m_s": 2000}]
    data, (_, _, z, time), meta = acquire(path, manifest, velocity_model="layered", layers=layers,
                                        z_min_mm=0.25, z_max_mm=2, sound_speed_m_s=99999)
    model_valid = z <= 1.5
    expected = np.where(z <= 0.5, 0.2+2*z, 0.2+1+(z-0.5))
    expected[~model_valid] = 0
    np.testing.assert_allclose(time, expected, atol=1e-14)
    np.testing.assert_array_equal(meta["model_depth_valid"], model_valid)
    np.testing.assert_array_equal(data["valid_mask"][:, 0, 0], model_valid.astype(float))
    for name in ("rf", "envelope"):
        assert np.all(data[name][~model_valid] == 0)
    assert time[0] > 0.7  # z_min=.25 did not restart the travel path at zero.


def test_explicit_surface_is_on_saved_axis_not_relative_to_nonzero_record_start(tmp_path):
    path, manifest, _ = source(tmp_path, time=np.linspace(0.8, 2.4, 321), water_delay=0.4)
    source_data, (_, _, z, source_time), source_meta = acquire(path, manifest)
    explicit_data, (_, _, _, explicit_time), explicit_meta = acquire(path, manifest,
                        surface_reference="explicit", surface_time_us=0.9)
    np.testing.assert_allclose(source_time, 0.4+z, atol=1e-14)
    np.testing.assert_allclose(explicit_time, 0.9+z, atol=1e-14)
    np.testing.assert_allclose(explicit_time-source_time, 0.5, atol=1e-14)
    source_valid = (source_time >= 0.8) & (source_time <= 2.4)
    explicit_valid = (explicit_time >= 0.8) & (explicit_time <= 2.4)
    np.testing.assert_array_equal(source_data["valid_mask"][:, 0, 0], source_valid)
    np.testing.assert_array_equal(explicit_data["valid_mask"][:, 0, 0], explicit_valid)
    assert source_meta["model_depth_valid"] == [True]*64
    assert explicit_meta["surface_time_us"] == 0.9
    assert np.all(explicit_data["rf"][~explicit_valid] == 0)


def test_actual_nonuniform_record_time_centers_control_interpolation(tmp_path):
    # A deliberately nonuniform saved axis prevents accidental reliance on a
    # sample-rate setting or zero-origin index arithmetic.
    time = 0.3+np.linspace(0, 1, 601)**1.3*3
    path, manifest, gain = source(tmp_path, time=time)
    data, (_, _, _, mapped), _ = acquire(path, manifest)
    np.testing.assert_allclose(data["rf"], (2*mapped-2)[:, None, None]*gain, atol=6e-7)


def test_preserves_exact_global_source_xy_and_never_reads_velocity_from_labels(tmp_path):
    path, manifest, gain = source(tmp_path)
    before = deepcopy(manifest)
    data, (x, y, z, time), _ = acquire(path, manifest)
    group = zarr.open_group(str(path / "data.zarr"), mode="r")
    np.testing.assert_array_equal(x, group["x_mm"][:])
    np.testing.assert_array_equal(y, group["y_mm"][:])
    assert data["rf"].shape == (64, 16, 24)
    assert x[0] > 42 and y[0] > 5
    assert manifest == before
    assert data["rf"][30, 3, 17] == pytest.approx((2*time[30]-2)*gain[3, 17], abs=2e-7)


def test_source_arrays_and_metadata_remain_byte_identical_after_mapping(tmp_path):
    path, manifest, _ = source(tmp_path)
    group = zarr.open_group(str(path / "data.zarr"), mode="r")
    original = {name: group[name][:].tobytes() for name in ("rf", "envelope", "x_mm", "y_mm", "time_us")}
    frozen = deepcopy(manifest)
    acquire(path, manifest)
    assert all(group[name][:].tobytes() == value for name, value in original.items())
    assert manifest == frozen


def test_resume_is_byte_identical_and_cache_cleanup_preserves_source(tmp_path):
    path, manifest, _ = source(tmp_path)
    prepared = prepare_depth(request(manifest), manifest, path)
    cache = prepared.cache_path
    try:
        full = list(iter_depth_slices(prepared))
        resumed = list(iter_depth_slices(prepared, start_slice=17))
        for previous, current in zip(full[17:], resumed):
            for name in previous[2]:
                np.testing.assert_array_equal(previous[2][name], current[2][name])
        assert list(iter_depth_slices(prepared, start_slice=64)) == []
        with pytest.raises(ValueError, match="Resume slice"):
            list(iter_depth_slices(prepared, start_slice=65))
    finally:
        prepared.close()
    assert not cache.exists()
    assert path.exists()
    prepared.close()


def test_frozen_source_water_metadata_is_required_and_must_agree(tmp_path):
    path, manifest, _ = source(tmp_path)
    del manifest["metadata"]["water_round_trip_delay_us"]
    assert estimate_depth(request(manifest), manifest, path)["surface_time_us"] == 0.4
    del manifest["estimate"]["water_round_trip_delay_us"]
    with pytest.raises(ValueError, match="no frozen"):
        estimate_depth(request(manifest), manifest, path)
    explicit = request(manifest, surface_reference="explicit", surface_time_us=0.1)
    assert estimate_depth(explicit, manifest, path)["surface_time_us"] == 0.1
    manifest["metadata"]["water_round_trip_delay_us"] = 0.3
    manifest["estimate"]["water_round_trip_delay_us"] = 0.4
    with pytest.raises(ValueError, match="disagree"):
        estimate_depth(request(manifest), manifest, path)


def test_estimate_uses_coordinates_only_and_corrupt_source_is_rejected_during_prepare(tmp_path):
    path, manifest, _ = source(tmp_path)
    group = zarr.open_group(str(path / "data.zarr"), mode="r+")
    group["rf"][0, 0, 0] = np.nan
    estimate = estimate_depth(request(manifest), manifest, path)
    assert estimate["shape"] == [64, 16, 24]
    with pytest.raises(ValueError, match="nonfinite"):
        prepare_depth(request(manifest), manifest, path)
    assert not list(tmp_path.glob(".depth-mapping-cache-*"))
    group["rf"][0, 0, 0] = 0
    group["envelope"][0, 0, 0] = -0.1
    with pytest.raises(ValueError, match="inconsistent"):
        prepare_depth(request(manifest), manifest, path)
    assert not list(tmp_path.glob(".depth-mapping-cache-*"))


def test_resource_estimate_counts_three_products_and_two_cached_products(tmp_path, monkeypatch):
    import virtual_microscopy.depth_mapping as engine

    path, manifest, _ = source(tmp_path)
    estimate = estimate_depth(request(manifest), manifest, path)
    assert estimate["total_bytes"] == 64*16*24*12+(24+16+128)*8
    assert estimate["estimated_temporary_bytes"] == 64*16*24*8
    assert estimate["estimated_peak_bytes"] > estimate["estimated_temporary_bytes"]
    assert estimate["bounds_mm"] == [42, 48, 5, 9, 0, 2]
    assert estimate["voxel_pitch_mm"] == [0.25, 0.25, 2/64]
    monkeypatch.setattr(engine, "MAX_BYTES", 100)
    with pytest.raises(ValueError, match="saved-volume budget"):
        prepare_depth(request(manifest), manifest, path)
    assert not list(tmp_path.glob(".depth-mapping-cache-*"))


def test_model_evidence_remains_a_declared_claim_and_does_not_change_numbers(tmp_path):
    path, manifest, _ = source(tmp_path)
    assumed, _, _ = acquire(path, manifest)
    calibrated, _, metadata = acquire(path, manifest, model_evidence="user_calibrated",
                                       model_note="User supplied external calibration claim.")
    for name in assumed:
        np.testing.assert_array_equal(assumed[name], calibrated[name])
    assert metadata["model_evidence"] == "user_calibrated"
    assert any("not independently verified" in warning for warning in metadata["warnings"])


@pytest.mark.parametrize("changes,match", [
    ({"z_min_mm": 2, "z_max_mm": 1}, "exceed"),
    ({"surface_reference": "explicit"}, "requires surface_time"),
    ({"surface_time_us": 0.1}, "Omit surface_time"),
    ({"velocity_model": "layered"}, "at least one"),
    ({"layers": [{"end_depth_mm": 1, "sound_speed_m_s": 2000}]}, "empty layers"),
    ({"velocity_model": "layered", "layers": [{"end_depth_mm": 1, "sound_speed_m_s": 2000},
                                                 {"end_depth_mm": 0.8, "sound_speed_m_s": 2000}]}, "strictly increase"),
    ({"sound_speed_m_s": 0}, "greater than"),
    ({"model_evidence": "verified_by_app"}, "Input should"),
    ({"nz": 16.5}, "integer"),
])
def test_invalid_declared_mapping_settings_are_rejected(tmp_path, changes, match):
    manifest = {"dataset_id": str(uuid4())}
    with pytest.raises(ValidationError, match=match):
        request(manifest, **changes)


def test_source_bounds_and_unrepresentable_velocities_are_rejected(tmp_path):
    path, manifest, _ = source(tmp_path)
    with pytest.raises(ValueError, match="within the source"):
        estimate_depth(request(manifest, z_max_mm=4), manifest, path)
    with pytest.raises(ValueError, match="within the source"):
        estimate_depth(request(manifest, velocity_model="layered", layers=[{"end_depth_mm": 4, "sound_speed_m_s": 2000}]), manifest, path)
    with pytest.raises(ValueError, match="nonfinite travel"):
        estimate_depth(request(manifest, sound_speed_m_s=1e-320), manifest, path)
    with pytest.raises(ValueError, match="strictly increase"):
        estimate_depth(request(manifest, sound_speed_m_s=1e300), manifest, path)
    with pytest.raises(ValueError, match="strictly increase"):
        estimate_depth(request(manifest, surface_reference="explicit", surface_time_us=1e300), manifest, path)
    # Even when no output center falls inside the extremely short declared
    # layer, its nominal endpoint travel time must remain representable.
    with pytest.raises(ValueError, match="nonfinite travel"):
        estimate_depth(request(manifest, velocity_model="layered", layers=[
            {"end_depth_mm": 0.001, "sound_speed_m_s": 1e-320}]), manifest, path)


def test_legacy_source_kind_and_spatial_sampling_contract(tmp_path):
    path, manifest, _ = source(tmp_path)
    del manifest["kind"]
    assert estimate_depth(request(manifest), manifest, path)["source_shape"] == manifest["shape"]
    group = zarr.open_group(str(path / "data.zarr"), mode="r+")
    group["x_mm"][5] += 0.01
    with pytest.raises(ValueError, match="uniformly sampled"):
        estimate_depth(request(manifest), manifest, path)


def test_only_completed_sam_sources_with_matching_identity_are_accepted(tmp_path):
    path, manifest, _ = source(tmp_path)
    config = request(manifest)
    manifest["kind"] = "sam_depth_volume"
    with pytest.raises(ValueError, match="completed saved SAM RF"):
        estimate_depth(config, manifest, path)
    manifest["kind"] = "sam_rf_volume"
    manifest["dataset_id"] = str(uuid4())
    with pytest.raises(ValueError, match="identity"):
        estimate_depth(config, manifest, path)
