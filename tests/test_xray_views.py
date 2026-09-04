"""Asymmetric saved-array fixtures and full HTTP acquisition integration."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4
import zipfile

import numpy as np
import pytest
import zarr
from fastapi.testclient import TestClient

from virtual_microscopy.xray_processing import xray_view


@pytest.fixture
def saved(tmp_path):
    path = tmp_path / str(uuid4())
    path.mkdir()
    group = zarr.open_group(str(path / "data.zarr"), mode="w")
    a, v, u = np.indices((4, 16, 20))
    counts = ((a * 100 + v * 10 + u + 1) * 10).astype(np.float32)
    counts[2, 5, 7] = 0
    arrays = {"counts": counts, "transmission": counts / 1000,
              "line_integrals": -np.log(np.where(counts > 0, counts, .5) / 1000),
              "valid_mask": (counts > 0).astype(np.float32)}
    angles = np.array([-45, 45, 135, 225], dtype=np.float64)
    radians = np.deg2rad(angles)
    coords = {"u_mm": -.95 + np.arange(20) * .1, "v_mm": -1.5 + np.arange(16) * .2,
              "angles_deg": angles, "detector_center_mm": np.tile([2., 3., .5], (4, 1)),
              "ray_direction_xyz": np.column_stack([np.sin(radians), np.zeros(4), np.cos(radians)]),
              "detector_u_xyz": np.column_stack([np.cos(radians), np.zeros(4), -np.sin(radians)]),
              "detector_v_xyz": np.tile([0., 1., 0.], (4, 1))}
    for name, values in arrays.items():
        group.create_array(name, data=values.astype(np.float32), chunks=(1, 16, 20))
    for name, values in coords.items():
        group.create_array(name, data=values)
    manifest = {"dataset_id": path.name, "kind": "xray_projection_volume", "complete": True,
        "state": "completed", "shape": [4, 16, 20], "request": {"twin": {"name": "Asymmetric X-ray", "size_mm": [4, 6, 1]},
            "acquisition": {"angle_start_deg": -45, "angle_span_deg": 360, "noise": False}},
        "estimate": {"detector_extent_mm": [-1, 1, -1.6, 1.6]}, "metadata": {"warnings": ["Synthetic test fixture."]}}
    (path / "manifest.json").write_text(json.dumps(manifest))
    return path, manifest, arrays, coords


def test_projection_sinogram_profile_axes_and_zero_mask(saved):
    path, manifest, arrays, coords = saved
    result = xray_view(path, manifest, view_index=2, detector_row=5, product="line_integrals")
    np.testing.assert_array_equal(result["projection"]["image"], arrays["line_integrals"][2])
    np.testing.assert_array_equal(result["sinogram"]["image"], arrays["line_integrals"][:, 5])
    np.testing.assert_array_equal(result["profile"]["values"], arrays["line_integrals"][2, 5])
    np.testing.assert_array_equal(result["profile"]["u_mm"], coords["u_mm"])
    np.testing.assert_allclose(result["projection"]["extent_mm"], [-1, 1, -1.6, 1.6])
    assert result["cursor"] == {"view_index": 2, "angle_deg": 135., "detector_row": 5, "v_mm": -.5}
    assert result["invalid_pixels"] == 1
    assert result["projection"]["invalid_mask"][5][7]
    assert result["sinogram"]["invalid_mask"][2][7]
    assert result["profile"]["invalid_mask"][7]
    np.testing.assert_array_equal(result["sinogram"]["angle_bin_edges_deg"], [-90, 0, 90, 180, 270])
    for name in result["pose"]:
        np.testing.assert_array_equal(result["pose"][name], coords[name][2])


def test_scrubbing_products_changes_no_dataset_bytes(saved):
    path, manifest, arrays, _ = saved
    def hashes():
        return {p.relative_to(path): hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob("*") if p.is_file()}
    before = hashes()
    for product in arrays.keys() - {"valid_mask"}:
        result = xray_view(path, manifest, view_index=3, detector_row=8, product=product)
        np.testing.assert_array_equal(result["projection"]["image"], arrays[product][3])
    assert hashes() == before


@pytest.mark.parametrize("kwargs", [{"view_index": 4}, {"detector_row": 16}, {"view_index": -1}, {"product": "reconstruction"}])
def test_bad_view_rejected(saved, kwargs):
    with pytest.raises(ValueError):
        xray_view(saved[0], saved[1], **kwargs)


@pytest.mark.parametrize("array_name,missing_view", [("valid_mask", 2), ("valid_mask", 1), ("counts", 2), ("counts", 1)])
def test_missing_projection_or_mask_chunks_are_rejected_before_json(saved, array_name, missing_view):
    path, manifest, arrays, _ = saved
    group = zarr.open_group(str(path / "data.zarr"), mode="r+")
    # Production datasets use NaN fill values so absent chunks cannot look like
    # valid zero measurements. Match that policy in this asymmetric fixture.
    group.create_array(array_name, data=arrays[array_name].astype(np.float32), chunks=(1, 16, 20),
                       fill_value=float("nan"), overwrite=True)
    chunk = path / "data.zarr" / array_name / "c" / str(missing_view) / "0" / "0"
    assert chunk.is_file()
    chunk.unlink()
    with pytest.raises(ValueError, match="missing or corrupted"):
        xray_view(path, manifest, view_index=2, detector_row=5, product="counts")


def test_nonbinary_mask_is_rejected_and_healthy_zero_counts_remain_available(saved):
    path, manifest, arrays, _ = saved
    healthy = xray_view(path, manifest, view_index=2, detector_row=5, product="counts")
    assert healthy["projection"]["image"][5][7] == 0
    assert healthy["projection"]["invalid_mask"][5][7] is True
    group = zarr.open_group(str(path / "data.zarr"), mode="r+")
    group["valid_mask"][2, 5, 7] = 0.5
    with pytest.raises(ValueError, match="binary"):
        xray_view(path, manifest, view_index=2, detector_row=5, product="counts")


def test_corrupted_view_api_returns_a_readable_422(saved):
    from virtual_microscopy.server import app
    path, manifest, _, _ = saved
    group = zarr.open_group(str(path / "data.zarr"), mode="r+")
    group["valid_mask"][2, 5, 7] = np.nan
    class Manager:
        def get_manifest(self, identifier):
            return manifest
        def dataset_path(self, identifier):
            return path
    previous = getattr(app.state, "volume_jobs", None)
    app.state.volume_jobs = Manager()
    try:
        client = TestClient(app)
        response = client.get(f"/api/v2/datasets/{path.name}/xray-view?view_index=2&detector_row=5&product=counts")
        assert response.status_code == 422, response.text
        assert "log-validity mask" in response.json()["detail"]
        assert "missing or corrupted" in response.json()["detail"]
    finally:
        app.state.volume_jobs = previous


@pytest.mark.parametrize("link_location", ["store_root", "nested_directory", "manifest"])
def test_exports_reject_symlinks_before_integrity_reads(tmp_path, monkeypatch, link_location):
    from virtual_microscopy.datasets import DatasetStore
    from virtual_microscopy.volume_api import _archive
    dataset = tmp_path / str(uuid4())
    dataset.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "unrelated.txt").write_text("This unrelated file must never be exported.")
    (dataset / "manifest.json").write_text("{}")
    if link_location == "store_root":
        link, target, is_dir = dataset / "data.zarr", outside, True
    elif link_location == "nested_directory":
        (dataset / "data.zarr").mkdir()
        link, target, is_dir = dataset / "data.zarr" / "redirect", outside, True
    else:
        (dataset / "data.zarr").mkdir()
        (dataset / "manifest.json").unlink()
        (outside / "manifest.json").write_text("{}")
        link, target, is_dir = dataset / "manifest.json", outside / "manifest.json", False
    try:
        link.symlink_to(target, target_is_directory=is_dir)
    except OSError:
        pytest.skip("Creating symlinks requires permission on this host.")
    def unexpected_read(*_):
        pytest.fail("Integrity reads must not follow a redirected dataset.")
    monkeypatch.setattr(DatasetStore, "verify_complete", unexpected_read)
    with pytest.raises(ValueError, match="symbolic links"):
        _archive(dataset, dataset.name)
    assert not list(tmp_path.glob("vm-export-*.zip"))


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junction semantics")
def test_exports_reject_windows_store_junction_before_integrity_reads(tmp_path, monkeypatch):
    from virtual_microscopy.datasets import DatasetStore
    from virtual_microscopy.volume_api import _archive
    dataset = tmp_path / str(uuid4())
    dataset.mkdir()
    (dataset / "manifest.json").write_text("{}")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = dataset / "data.zarr"
    command = "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path $env:VM_TEST_LINK -Target $env:VM_TEST_TARGET | Out-Null"
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                            env={**os.environ, "VM_TEST_LINK": str(link), "VM_TEST_TARGET": str(outside)},
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert link.is_junction()
    def unexpected_read(*_):
        pytest.fail("Integrity reads must not follow a junction.")
    monkeypatch.setattr(DatasetStore, "verify_complete", unexpected_read)
    with pytest.raises(ValueError, match="junctions"):
        _archive(dataset, dataset.name)
    assert not list(tmp_path.glob("vm-export-*.zip"))


def test_export_path_errors_are_mapped_to_422(saved, monkeypatch):
    from virtual_microscopy.server import app
    path, manifest, _, _ = saved
    class Manager:
        def get_manifest(self, identifier):
            return manifest
        def dataset_path(self, identifier):
            return path
    def reject_path(*_):
        raise ValueError("Dataset export path leaves the dataset directory.")
    monkeypatch.setattr("virtual_microscopy.volume_api._export_files", reject_path)
    previous = getattr(app.state, "volume_jobs", None)
    app.state.volume_jobs = Manager()
    try:
        response = TestClient(app).get(f"/api/v2/datasets/{path.name}/export")
        assert response.status_code == 422, response.text
        assert "leaves" in response.json()["detail"]
    finally:
        app.state.volume_jobs = previous


def test_view_api_kind_guards_and_metadata(saved):
    from virtual_microscopy.server import app
    path, manifest, _, _ = saved
    class Manager:
        def get_manifest(self, identifier):
            if identifier != path.name:
                raise KeyError(identifier)
            return manifest
        def dataset_path(self, identifier):
            return path
    previous = getattr(app.state, "volume_jobs", None)
    app.state.volume_jobs = Manager()
    try:
        client = TestClient(app)
        prefix = f"/api/v2/datasets/{path.name}"
        assert client.get(prefix + "/view").status_code == 422
        detail = client.get(prefix).json()
        assert detail["detector_extent_mm"] == [-1, 1, -1.6, 1.6]
        assert detail["angles_range_deg"] == [-45, 225]
        assert "time_range_us" not in detail
        response = client.get(prefix + "/xray-view?view_index=2&detector_row=5&product=counts")
        assert response.status_code == 200, response.text
        assert response.json()["projection"]["unit"] == "expected photon counts"
        assert client.get(prefix + "/xray-view?view_index=4").status_code == 422
        manifest["complete"] = False
        assert client.get(prefix + "/xray-view").status_code == 409
    finally:
        app.state.volume_jobs = previous


def test_real_xray_job_views_export_restart_and_legacy_sam(monkeypatch, tmp_path):
    from virtual_microscopy.server import app
    monkeypatch.setenv("VM_DATA_ROOT", str(tmp_path))
    twin = {"schema_version": 1, "name": "X-ray API coupon", "size_mm": [4, 3, 1],
        "objects": [{"id": "si", "name": "Silicon", "shape": "box", "material": "silicon",
            "center_mm": [2, 1.5, .5], "size_mm": [4, 3, 1], "role": "structure"}]}
    body = {"kind": "xray_projection_volume", "twin": twin, "acquisition": {
        "views": 4, "detector_rows": 16, "detector_cols": 24, "geometry_nx": 32, "geometry_ny": 16,
        "geometry_nz": 64, "noise": True, "seed": 79, "detector_fwhm_mm": 0}}
    with TestClient(app) as client:
        estimate = client.post("/api/v2/estimate", json=body)
        assert estimate.status_code == 200, estimate.text
        assert estimate.json()["shape"] == [4, 16, 24]
        submitted = client.post("/api/v2/jobs", json=body)
        assert submitted.status_code == 202, submitted.text
        job_id = submitted.json()["id"]
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            job = client.get(f"/api/v2/jobs/{job_id}").json()
            if job["status"] in ("completed", "failed"):
                break
            time.sleep(.05)
        assert job["status"] == "completed", job
        assert job["kind"] == "xray_projection_volume" and job["progress_unit"] == "views"
        prefix = f"/api/v2/datasets/{job_id}"
        front = client.get(prefix + "/xray-view?view_index=0").json()
        side = client.get(prefix + "/xray-view?view_index=1").json()
        assert front["cursor"]["angle_deg"] == 0 and side["cursor"]["angle_deg"] == 90
        assert front["projection"]["image"] != side["projection"]["image"]
        assert len(client.get("/api/v2/jobs").json()["jobs"]) == 1
        exported = client.get(prefix + "/export")
        assert exported.status_code == 200, exported.text
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["kind"] == "xray_projection_volume"
            assert "data.zarr/counts/zarr.json" in archive.namelist()
            assert "data.zarr/ray_direction_xyz/zarr.json" in archive.namelist()
        # The original untagged SAM API contract stays available.
        sam = client.post("/api/v2/estimate", json={"twin": twin, "acquisition": {"scan_nx": 16, "scan_ny": 16}})
        assert sam.status_code == 200 and sam.json()["kind"] == "sam_rf_volume"
        bad = client.post("/api/v2/jobs", json={**body, "kind": "unknown"})
        assert bad.status_code == 422
    with TestClient(app) as reopened:
        assert reopened.get(prefix + "/xray-view?view_index=0").json()["projection"] == front["projection"]
