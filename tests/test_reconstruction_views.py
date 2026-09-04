"""Independent asymmetric spatial arrays verify CT axes and immutable browsing."""
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

from virtual_microscopy.reconstruction_processing import reconstruction_view


@pytest.fixture
def saved(tmp_path):
    path = tmp_path / str(uuid4())
    path.mkdir()
    group = zarr.open_group(str(path / "data.zarr"), mode="w")
    k, j, i = np.indices((16, 18, 20))
    attenuation = ((100 * k + 10 * j + i - 700) / 1000).astype(np.float32)
    coverage = np.ones_like(attenuation)
    coverage[4, 8, 9] = .5
    coverage[9, 2, 5] = 0
    attenuation[coverage < 1] = 0
    for name, values in (("attenuation", attenuation), ("coverage", coverage)):
        group.create_array(name, data=values, chunks=(1, 18, 20), fill_value=float("nan"))
    coords = {"x_mm": 10.1 + np.arange(20) * .2, "y_mm": 20.25 + np.arange(18) * .5,
              "z_mm": .525 + np.arange(16) * .05}
    for name, values in coords.items():
        group.create_array(name, data=values)
    manifest = {"dataset_id": path.name, "kind": "xray_reconstruction", "complete": True,
        "state": "completed", "shape": [16, 18, 20], "request": {"kind": "xray_reconstruction",
            "source_dataset_id": str(uuid4()), "reconstruction": {"filter": "hann", "frequency_cutoff": .8}},
        "source_manifest": {"request": {"twin": {"name": "Independent asymmetric CT test"}}},
        "estimate": {"bounds_mm": [10, 14, 20, 29, .5, 1.3], "voxel_pitch_mm": [.2, .5, .05]},
        "metadata": {"warnings": ["Synthetic fixture; no experimental validation."]}}
    (path / "manifest.json").write_text(json.dumps(manifest))
    return path, manifest, attenuation, coverage, coords


def test_orthogonal_spatial_axes_profiles_cursor_and_negative_values(saved):
    path, manifest, a, c, coords = saved
    v = reconstruction_view(path, manifest, x_index=9, y_index=8, z_index=4)
    for key, image, mask, extent in (("xy", a[4], c[4], [10, 14, 20, 29]),
            ("xz", a[:, 8], c[:, 8], [10, 14, .5, 1.3]),
            ("yz", a[:, :, 9], c[:, :, 9], [20, 29, .5, 1.3])):
        np.testing.assert_array_equal(v[key]["image"], image)
        np.testing.assert_array_equal(v[key]["coverage"], mask)
        np.testing.assert_array_equal(v[key]["invalid_mask"], mask < 1)
        np.testing.assert_allclose(v[key]["extent_mm"], extent)
        assert v[key]["unit"] == "mm^-1"
    assert v["cursor"] == {"x_index": 9, "y_index": 8, "z_index": 4,
        "x_mm": coords["x_mm"][9], "y_mm": coords["y_mm"][8], "z_mm": coords["z_mm"][4],
        "attenuation": 0., "coverage": .5}
    for axis, values in (("x", a[4, 8]), ("y", a[4, :, 9]), ("z", a[:, 8, 9])):
        np.testing.assert_array_equal(v["profiles"][axis]["values"], values)
        np.testing.assert_array_equal(v["profiles"][axis]["coordinate_mm"], coords[f"{axis}_mm"])
    assert v["xy"]["min"] < 0
    assert v["metadata"]["axis_order"] == ["z", "y", "x"]
    assert v["metadata"]["source_dataset_id"] == manifest["request"]["source_dataset_id"]


def test_views_leave_all_saved_bytes_unchanged_and_do_not_need_parent(saved):
    path, manifest, *_ = saved
    def digests():
        return {p.relative_to(path): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in path.rglob("*") if p.is_file()}
    before = digests()
    assert not (path.parent / manifest["request"]["source_dataset_id"]).exists()
    for index in (0, 5, 15):
        reconstruction_view(path, manifest, x_index=index, y_index=index, z_index=index)
    assert before == digests()


@pytest.mark.parametrize("kwargs", [{"x_index": 20}, {"y_index": 18}, {"z_index": 16},
                                     {"z_index": -1}, {"x_index": .5}, {"x_index": True}])
def test_invalid_cursor_is_rejected(saved, kwargs):
    with pytest.raises(ValueError, match="cursor"):
        reconstruction_view(saved[0], saved[1], **kwargs)


@pytest.mark.parametrize("name", ["attenuation", "coverage"])
def test_missing_plane_is_not_rendered_as_zero(saved, name):
    path, manifest, *_ = saved
    chunk = path / "data.zarr" / name / "c" / "3" / "0" / "0"
    assert chunk.is_file()
    chunk.unlink()
    with pytest.raises(ValueError, match="missing or corrupted"):
        reconstruction_view(path, manifest)


def test_bad_coverage_and_coordinate_data_are_rejected(saved):
    path, manifest, *_ = saved
    group = zarr.open_group(str(path / "data.zarr"), mode="r+")
    group["coverage"][4, 8, 9] = 2
    with pytest.raises(ValueError, match="coverage"):
        reconstruction_view(path, manifest)
    group["coverage"][4, 8, 9] = .5
    group["x_mm"][2] = np.nan
    with pytest.raises(ValueError, match="coordinates"):
        reconstruction_view(path, manifest)


def test_api_kind_incomplete_detail_and_corruption_guards(saved):
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
        client = TestClient(app)
        route = f"/api/v2/datasets/{path.name}"
        view = client.get(route + "/reconstruction-view?x_index=9&y_index=8&z_index=4")
        assert view.status_code == 200, view.text
        assert view.json()["cursor"]["coverage"] == .5
        detail = client.get(route).json()
        assert detail["source_dataset_id"] == manifest["request"]["source_dataset_id"]
        assert detail["bounds_mm"] == manifest["estimate"]["bounds_mm"]
        assert "time_range_us" not in detail
        assert client.get(route + "/view").status_code == 422
        assert client.get(route + "/xray-view").status_code == 422
        assert client.get(route + "/reconstruction-view?x_index=20").status_code == 422
        manifest["complete"] = False
        assert client.get(route + "/reconstruction-view").status_code == 409
        manifest["complete"] = True
        group = zarr.open_group(str(path / "data.zarr"), mode="r+")
        group["coverage"][4, 8, 9] = np.nan
        assert client.get(route + "/reconstruction-view").status_code == 422
    finally:
        app.state.volume_jobs = previous


def test_real_source_reconstruction_export_and_restart_preserve_parent(tmp_path, monkeypatch):
    from virtual_microscopy.server import app
    monkeypatch.setenv("VM_DATA_ROOT", str(tmp_path))
    twin = {"schema_version": 1, "name": "Asymmetric reconstruction API coupon",
        "size_mm": [4, 2, 4], "objects": [
            {"id": "copper", "name": "Copper block", "shape": "box", "material": "copper",
             "center_mm": [1.2, .65, 2.7], "size_mm": [.7, .7, .8], "role": "structure"},
            {"id": "silicon", "name": "Silicon block", "shape": "box", "material": "silicon",
             "center_mm": [2.9, 1.4, 1.1], "size_mm": [.5, .6, .7], "role": "structure"}]}
    def wait(client, identifier):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            response = client.get(f"/api/v2/jobs/{identifier}")
            assert response.status_code == 200, response.text
            job = response.json()
            assert job["status"] not in {"failed", "cancelled", "interrupted"}, job
            if job["status"] == "completed":
                return job
            time.sleep(.1)
        pytest.fail("Real reconstruction workflow did not complete within 60 seconds.")
    def digests(path):
        return {p.relative_to(path): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in path.rglob("*") if p.is_file()}
    with TestClient(app) as client:
        source_response = client.post("/api/v2/jobs", json={"kind": "xray_projection_volume", "twin": twin,
            "acquisition": {"geometry_nx": 48, "geometry_ny": 16, "geometry_nz": 48,
                "detector_cols": 64, "detector_rows": 16, "views": 32, "angle_span_deg": 180,
                "noise": False, "detector_fwhm_mm": 0}})
        assert source_response.status_code == 202, source_response.text
        source_id = source_response.json()["dataset_id"]
        wait(client, source_id)
        before = digests(tmp_path / source_id)
        request = {"kind": "xray_reconstruction", "source_dataset_id": source_id,
            "reconstruction": {"nx": 24, "ny": 16, "nz": 32, "filter": "hann", "frequency_cutoff": .8,
                               "invalid_policy": "reject", "truncation_policy": "reject"}}
        estimate = client.post("/api/v2/estimate", json=request)
        assert estimate.status_code == 200, estimate.text
        assert estimate.json()["shape"] == [32, 16, 24]
        assert estimate.json()["estimated_temporary_bytes"] > 0
        response = client.post("/api/v2/jobs", json=request)
        assert response.status_code == 202, response.text
        identifier = response.json()["dataset_id"]
        job = wait(client, identifier)
        assert job["progress_unit"] == "slices"
        assert job["completed_units"] == 32
        route = f"/api/v2/datasets/{identifier}"
        detail = client.get(route).json()
        assert detail["source_manifest"]["dataset_id"] == source_id
        view = client.get(route + "/reconstruction-view?x_index=7&y_index=5&z_index=20")
        assert view.status_code == 200, view.text
        data = view.json()
        assert np.asarray(data["xy"]["image"]).shape == (16, 24)
        assert np.asarray(data["xz"]["image"]).shape == (32, 24)
        assert np.asarray(data["yz"]["image"]).shape == (32, 16)
        np.testing.assert_allclose(data["metadata"]["bounds_mm"], [0, 4, 0, 2, 0, 4], atol=1e-12)
        assert np.max(data["xz"]["image"]) > .01
        archive = client.get(route + "/export")
        assert archive.status_code == 200, archive.text[:200]
        with zipfile.ZipFile(io.BytesIO(archive.content)) as saved_archive:
            assert saved_archive.testzip() is None
            assert "data.zarr/attenuation/zarr.json" in saved_archive.namelist()
            assert "data.zarr/coverage/zarr.json" in saved_archive.namelist()
            frozen = json.loads(saved_archive.read("manifest.json"))
            assert frozen["source_manifest"]["dataset_id"] == source_id
            assert frozen["axis_order"] == ["z", "y", "x"]
        assert digests(tmp_path / source_id) == before
        assert len(client.get("/api/v2/jobs").json()["jobs"]) == 2
        assert client.post("/api/v2/jobs", json={**request, "source_dataset_id": identifier}).status_code == 422
        assert not list(tmp_path.glob(".reconstruction-cache-*"))
    with TestClient(app) as client:
        reloaded = client.get(route + "/reconstruction-view?x_index=7&y_index=5&z_index=20")
        assert reloaded.status_code == 200, reloaded.text
        assert reloaded.json() == data
        assert digests(tmp_path / source_id) == before
