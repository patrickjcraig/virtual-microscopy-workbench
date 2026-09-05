"""Independent spatial fixtures verify SAM depth axes, masks and saved-only reads."""
import hashlib
import io
import json
import time
from uuid import uuid4
import zipfile

import numpy as np
import pytest
import zarr
from fastapi.testclient import TestClient

from virtual_microscopy.depth_processing import depth_view
from virtual_microscopy.datasets import coordinate_sha256, json_sha256


@pytest.fixture
def saved(tmp_path):
    path = tmp_path / str(uuid4())
    path.mkdir()
    group = zarr.open_group(str(path / "data.zarr"), mode="w")
    k, j, i = np.indices((16, 18, 20))
    rf = ((100*k + 10*j + i - 700) / 1000).astype(np.float32)
    envelope = (np.abs(rf) + .25).astype(np.float32)
    valid = np.ones_like(rf)
    valid[:3] = 0  # Valid velocity model but outside the recorded interval.
    valid[15] = 0  # Outside the velocity model as well.
    rf[valid == 0], envelope[valid == 0] = 0, 0
    rf[4, 8, 9], envelope[4, 8, 9] = 0, 0  # Supported zero amplitudes stay valid.
    for name, array in (("rf", rf), ("envelope", envelope), ("valid_mask", valid)):
        group.create_array(name, data=array, chunks=(1, 18, 20), fill_value=float("nan"))
    coords = {"x_mm": 10.1+np.arange(20)*.2, "y_mm": 20.25+np.arange(18)*.5,
              "z_mm": .525+np.arange(16)*.05, "travel_time_us": .8+np.arange(16)*.02}
    coords["travel_time_us"][-1] = 0
    for name, array in coords.items():
        group.create_array(name, data=array)
    coordinate_hashes = {name: coordinate_sha256(array) for name, array in coords.items()}
    source_id = str(uuid4())
    source = {"dataset_id": source_id,
        "coordinates_sha256": {name: coordinate_hashes[name] for name in ("x_mm", "y_mm")},
        "request": {"twin": {"name": "Independent asymmetric depth fixture"}}}
    manifest = {"dataset_id": path.name, "kind": "sam_depth_volume", "complete": True, "state": "completed",
        "shape": [16, 18, 20], "request": {"kind": "sam_depth_volume", "source_dataset_id": source_id,
            "mapping": {"velocity_model": "homogeneous", "sound_speed_m_s": 5000, "model_evidence": "user_assumed"}},
        "source_dataset_id": source_id, "source_manifest": source, "source_manifest_sha256": json_sha256(source),
        "coordinates_sha256": coordinate_hashes,
        "estimate": {"bounds_mm": [10, 14, 20, 29, .5, 1.3], "voxel_pitch_mm": [.2, .5, .05],
                     "source_time_range_us": [.86, 1.08]},
        "metadata": {"model_depth_valid": [True]*15+[False], "surface_time_us": .59,
                     "source_time_range_us": [.86, 1.08], "time_support_tolerance_us": 1e-12,
                     "warnings": ["Synthetic fixture, not calibrated depth."]}}
    manifest["metadata_sha256"] = json_sha256(manifest["metadata"])
    group.attrs.update({"source_dataset_id": source_id, "source_manifest_sha256": manifest["source_manifest_sha256"]})
    (path / "manifest.json").write_text(json.dumps(manifest))
    return path, manifest, rf, envelope, valid, coords


@pytest.mark.parametrize("product", ["rf", "envelope"])
def test_exact_spatial_slices_masks_and_profiles(saved, product):
    path, manifest, rf, env, valid, coords = saved
    data = rf if product == "rf" else env
    v = depth_view(path, manifest, x_index=9, y_index=8, z_index=4, product=product)
    for key, expected, mask, extent in (("xy", data[4], valid[4], [10, 14, 20, 29]),
            ("xz", data[:, 8], valid[:, 8], [10, 14, .5, 1.3]),
            ("yz", data[:, :, 9], valid[:, :, 9], [20, 29, .5, 1.3])):
        np.testing.assert_array_equal(v[key]["image"], expected)
        np.testing.assert_array_equal(v[key]["valid_mask"], mask == 1)
        np.testing.assert_allclose(v[key]["extent_mm"], extent)
    for axis, expected in (("x", data[4, 8]), ("y", data[4, :, 9]), ("z", data[:, 8, 9])):
        np.testing.assert_array_equal(v["profiles"][axis]["values"], expected)
        np.testing.assert_array_equal(v["profiles"][axis]["coordinate_mm"], coords[f"{axis}_mm"])
    assert v["cursor"]["rf"] == v["cursor"]["envelope"] == 0
    assert v["cursor"]["valid"]
    assert v["cursor"]["model_valid"]
    assert v["cursor"]["sample_time_us"] == coords["travel_time_us"][4]
    assert v["metadata"]["axis_order"] == ["z", "y", "x"]
    if product == "rf":
        assert v["xy"]["min"] < 0


def test_out_of_model_and_out_of_record_are_distinguished(saved):
    path, manifest, *_ = saved
    outside_record = depth_view(path, manifest, z_index=2)
    outside_model = depth_view(path, manifest, z_index=15)
    assert not outside_record["cursor"]["valid"]
    assert outside_record["cursor"]["sample_time_us"] == pytest.approx(.84)
    assert not outside_model["cursor"]["valid"]
    assert not outside_model["cursor"]["model_valid"]
    assert outside_model["cursor"]["sample_time_us"] is None


def test_saved_views_are_byte_immutable_without_source(saved):
    path, manifest, *_ = saved
    def hashes():
        return {p.relative_to(path): hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob("*") if p.is_file()}
    before = hashes()
    assert not (path.parent / manifest["request"]["source_dataset_id"]).exists()
    for product in ("rf", "envelope"):
        depth_view(path, manifest, product=product)
    assert hashes() == before


@pytest.mark.parametrize("name", ["x_mm", "y_mm", "z_mm", "travel_time_us"])
def test_finite_monotone_coordinate_tampering_rejected_before_signal_reads(saved, name, monkeypatch):
    path, manifest, *_ = saved
    group = zarr.open_group(str(path / "data.zarr"), mode="r+")
    changed = group[name][:]
    supported = np.asarray(manifest["metadata"]["model_depth_valid"])
    if name == "travel_time_us":
        changed[supported] += .001
        assert np.all(np.diff(changed[supported]) > 0)
    else:
        changed += .001
        assert np.all(np.diff(changed) > 0)
    assert np.isfinite(changed).all()
    group[name][:] = changed
    original_getitem = zarr.Array.__getitem__

    def coordinates_only(array, selection):
        if array.ndim == 3:
            pytest.fail("Corrupted coordinates must be rejected before reading signal data.")
        return original_getitem(array, selection)

    monkeypatch.setattr(zarr.Array, "__getitem__", coordinates_only)
    with pytest.raises(ValueError, match=f"Coordinate checksum mismatch: {name}"):
        depth_view(path, manifest)


def test_updated_lateral_checksum_cannot_override_frozen_source_coordinates(saved):
    path, manifest, *_ = saved
    group = zarr.open_group(str(path / "data.zarr"), mode="r+")
    changed = group["x_mm"][:] + .001
    group["x_mm"][:] = changed
    manifest["coordinates_sha256"]["x_mm"] = coordinate_sha256(changed)
    assert not (path.parent / manifest["source_dataset_id"]).exists()
    with pytest.raises(ValueError, match="no longer matches its raw-time source"):
        depth_view(path, manifest)


@pytest.mark.parametrize("where", ["outside_record", "outside_model", "supported_zero"])
def test_zero_signal_mask_flips_are_rejected(saved, where):
    path, manifest, *_ = saved
    group = zarr.open_group(str(path / "data.zarr"), mode="r+")
    index = {"outside_record": 2, "outside_model": 15, "supported_zero": (4, 8, 9)}[where]
    assert np.all(group["rf"][index] == 0)
    assert np.all(group["envelope"][index] == 0)
    group["valid_mask"][index] = 0 if where == "supported_zero" else 1
    with pytest.raises(ValueError, match="validity mask.*recording interval"):
        depth_view(path, manifest)


@pytest.mark.parametrize("kwargs", [{"x_index": -1}, {"y_index": 18}, {"z_index": 16}, {"z_index": .5}, {"x_index": True}])
def test_invalid_cursor_rejected(saved, kwargs):
    with pytest.raises(ValueError, match="cursor"):
        depth_view(saved[0], saved[1], **kwargs)


@pytest.mark.parametrize("name", ["rf", "envelope", "valid_mask"])
def test_missing_chunks_are_not_valid_zeros(saved, name):
    path, manifest, *_ = saved
    (path / "data.zarr" / name / "c" / "3" / "0" / "0").unlink()
    with pytest.raises(ValueError, match="missing or corrupted"):
        depth_view(path, manifest)


def test_bad_mask_envelope_travel_time_and_product(saved):
    path, manifest, *_ = saved
    with pytest.raises(ValueError, match="product"):
        depth_view(path, manifest, product="impedance")
    group = zarr.open_group(str(path / "data.zarr"), mode="r+")
    group["valid_mask"][4, 2, 3] = .5
    with pytest.raises(ValueError, match="mask"):
        depth_view(path, manifest)
    group["valid_mask"][4, 2, 3] = 1
    group["envelope"][4, 2, 3] = -.2
    with pytest.raises(ValueError, match="envelope"):
        depth_view(path, manifest)
    group["envelope"][4, 2, 3] = .2
    group["travel_time_us"][2] = np.nan
    with pytest.raises(ValueError, match="Coordinate checksum"):
        depth_view(path, manifest)


def test_modified_velocity_support_metadata_is_rejected(saved):
    path, manifest, *_ = saved
    manifest["metadata"]["model_depth_valid"][-1] = True
    with pytest.raises(ValueError, match="metadata checksum"):
        depth_view(path, manifest)


def test_api_detail_kind_incomplete_and_corruption_guards(saved):
    from virtual_microscopy.server import app
    path, manifest, *_ = saved
    class Manager:
        def get_manifest(self, identifier):
            return manifest
        def dataset_path(self, identifier):
            return path
    previous = getattr(app.state, "volume_jobs", None)
    app.state.volume_jobs = Manager()
    try:
        client = TestClient(app, raise_server_exceptions=True)
        route = f"/api/v2/datasets/{path.name}"
        assert client.get(route + "/depth-view?product=rf").status_code == 200
        detail = client.get(route).json()
        assert detail["name"].startswith("Depth estimate /")
        assert detail["mapping"]["model_evidence"] == "user_assumed"
        assert client.get(route + "/reconstruction-view").status_code == 422
        assert client.get(route + "/view").status_code == 422
        manifest["complete"] = False
        assert client.get(route + "/depth-view").status_code == 409
        manifest["complete"] = True
        group = zarr.open_group(str(path / "data.zarr"), mode="r+")
        original_x = group["x_mm"][:]
        group["x_mm"][:] = original_x + .001
        coordinate_response = client.get(route + "/depth-view")
        assert coordinate_response.status_code == 422
        assert "Coordinate checksum" in coordinate_response.json()["detail"]
        group["x_mm"][:] = original_x
        group["valid_mask"][2] = 1
        mask_response = client.get(route + "/depth-view")
        assert mask_response.status_code == 422
        assert "recording interval" in mask_response.json()["detail"]
        group["valid_mask"][2] = 0
        del group["rf"]
        assert client.get(route + "/depth-view").status_code == 422
    finally:
        app.state.volume_jobs = previous


def test_real_sam_source_depth_export_and_restart_preserve_raw(tmp_path, monkeypatch):
    from virtual_microscopy.server import app
    monkeypatch.setenv("VM_DATA_ROOT", str(tmp_path))
    twin = {"name": "SAM depth API silicon coupon", "size_mm": [4, 3, 2], "objects": [
        {"id": "slab", "name": "Silicon slab", "shape": "box", "material": "silicon",
         "center_mm": [2, 1.5, 1], "size_mm": [3, 2, 1]}]}
    def wait(client, identifier):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = client.get(f"/api/v2/jobs/{identifier}").json()
            assert job["status"] not in {"failed", "cancelled", "interrupted"}, job
            if job["status"] == "completed":
                return job
            time.sleep(.1)
        pytest.fail("Real SAM depth workflow exceeded 60 seconds.")
    def digests(path):
        return {p.relative_to(path): hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob("*") if p.is_file()}
    with TestClient(app) as client:
        response = client.post("/api/v2/jobs", json={"twin": twin, "acquisition": {
            "scan_nx": 16, "scan_ny": 24, "depth_samples": 128, "frequency_mhz": 20,
            "sample_rate_mhz": 160, "water_standoff_mm": .25, "record_duration_us": 1.2}})
        assert response.status_code == 202, response.text
        source_id = response.json()["dataset_id"]
        wait(client, source_id)
        before = digests(tmp_path / source_id)
        request = {"kind": "sam_depth_volume", "source_dataset_id": source_id, "mapping": {
            "nz": 32, "z_min_mm": .1, "z_max_mm": 1.9, "velocity_model": "layered",
            "layers": [{"end_depth_mm": .8, "sound_speed_m_s": 2000},
                       {"end_depth_mm": 1.5, "sound_speed_m_s": 5000}],
            "surface_reference": "source_water_delay", "model_evidence": "user_assumed",
            "model_note": "Deliberately assumed layered mapping for API verification."}}
        estimate = client.post("/api/v2/estimate", json=request)
        assert estimate.status_code == 200, estimate.text
        assert estimate.json()["shape"] == [32, 24, 16]
        response = client.post("/api/v2/jobs", json=request)
        assert response.status_code == 202, response.text
        identifier = response.json()["dataset_id"]
        assert wait(client, identifier)["progress_unit"] == "slices"
        route = f"/api/v2/datasets/{identifier}"
        view_response = client.get(route + "/depth-view?x_index=7&y_index=5&z_index=20&product=rf")
        assert view_response.status_code == 200, view_response.text
        view = view_response.json()
        assert np.asarray(view["xy"]["image"]).shape == (24, 16)
        assert np.asarray(view["xz"]["image"]).shape == (32, 16)
        assert np.asarray(view["yz"]["image"]).shape == (32, 24)
        assert not any(view["xz"]["valid_mask"][-1])
        assert view["metadata"]["travel_time_us"][-1] is None
        assert view["metadata"]["model_evidence"] == "user_assumed"
        np.testing.assert_allclose(view["metadata"]["bounds_mm"], [0, 4, 0, 3, .1, 1.9], atol=1e-12)
        archive = client.get(route + "/export")
        assert archive.status_code == 200, archive.text[:200]
        with zipfile.ZipFile(io.BytesIO(archive.content)) as saved_archive:
            assert saved_archive.testzip() is None
            for array in ("rf", "envelope", "valid_mask", "x_mm", "y_mm", "z_mm", "travel_time_us"):
                assert f"data.zarr/{array}/zarr.json" in saved_archive.namelist()
            frozen = json.loads(saved_archive.read("manifest.json"))
            assert frozen["source_manifest"]["dataset_id"] == source_id
            assert frozen["axis_order"] == ["z", "y", "x"]
        assert digests(tmp_path / source_id) == before
        assert len(client.get("/api/v2/jobs").json()["jobs"]) == 2
        assert client.post("/api/v2/jobs", json={**request, "source_dataset_id": identifier}).status_code == 422
    with TestClient(app) as client:
        assert client.get(route + "/depth-view?x_index=7&y_index=5&z_index=20&product=rf").json() == view
        assert digests(tmp_path / source_id) == before
