"""Independent asymmetric array fixtures verify saved-data views and export axes."""
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import zipfile

import numpy as np
import pytest
import zarr
from fastapi.testclient import TestClient

from virtual_microscopy.volume_processing import sam_view


@pytest.fixture
def saved(tmp_path):
    path = tmp_path / "01234567-89ab-4def-8123-456789abcdef"
    path.mkdir()
    group = zarr.open_group(str(path / "data.zarr"), mode="w")
    y, x, t = np.indices((16, 20, 1001))
    rf = (y * 100_000 + x * 2000 + t).astype(np.float32) * np.where(t % 2, -1, 1)
    rf = rf.astype(np.float32)
    envelope = np.abs(rf)
    for name, values, chunks in [("rf", rf, (4, 20, 1001)), ("envelope", envelope, (4, 20, 1001)),
                                ("x_mm", 3.05 + np.arange(20) * .1, (20,)),
                                ("y_mm", 8.1 + np.arange(16) * .2, (16,)),
                                ("time_us", .5 + np.arange(1001) * .0025, (1001,))]:
        group.create_array(name, data=values, chunks=chunks)
    manifest = {"dataset_id": path.name, "state": "completed", "complete": True,
                "shape": [16, 20, 1001], "request": {"twin": {"name": "Asymmetric fixture", "size_mm": [10, 15, 1]},
                "acquisition": {"record_start_us": .5, "sample_rate_mhz": 400, "roi_mm": [3, 8, 5, 11.2]}}}
    from virtual_microscopy.datasets import array_sha256, coordinate_sha256, json_sha256
    manifest.update(arrays_initialized=True, materials={}, tile_rows=4, total_rows=16,
                    completed_rows=16, solver={}, axis_order=["y", "x", "time"],
                    dtype="float32", completed_chunks={},
                    estimate={"shape": [16, 20, 1001], "tile_rows": 4})
    manifest["request_sha256"] = json_sha256(manifest["request"])
    manifest["materials_sha256"] = json_sha256(manifest["materials"])
    manifest["input_sha256"] = json_sha256({"request_sha256": manifest["request_sha256"],
        "materials_sha256": manifest["materials_sha256"], "solver": manifest["solver"]})
    manifest["coordinates_sha256"] = {name: coordinate_sha256(group[name][:]) for name in ("x_mm", "y_mm", "time_us")}
    group.attrs["input_sha256"] = manifest["input_sha256"]
    for y0 in range(0, 16, 4):
        manifest["completed_chunks"][str(y0)] = {"rows": [y0, y0 + 4],
            "rf_sha256": array_sha256(rf[y0:y0 + 4]), "envelope_sha256": array_sha256(envelope[y0:y0 + 4])}
    (path / "manifest.json").write_text(json.dumps(manifest))
    return path, rf, envelope, manifest


def test_saved_views_preserve_asymmetric_axes_and_signed_samples(saved):
    path, rf, envelope, _ = saved
    result = sam_view(path, x_index=3, y_index=7, time_index=25, gate_start_us=.52, gate_end_us=.55)
    np.testing.assert_array_equal(result["xy"]["image"], envelope[:, :, 25])
    np.testing.assert_array_equal(result["ascan"]["amplitude"], rf[7, 3])
    np.testing.assert_array_equal(result["ascan"]["envelope"], envelope[7, 3])
    np.testing.assert_allclose(result["xy"]["extent_mm"], [3, 5, 8, 11.2])
    np.testing.assert_allclose(result["ascan"]["probe_mm"], [3.35, 9.5])
    assert result["cursor"]["time_us"] == pytest.approx(.5625)
    assert result["xt"]["horizontal_axis"] == "x"
    assert result["yt"]["horizontal_axis"] == "y"
    for section, original in [(result["xt"], envelope[7]), (result["yt"], envelope[:, 3])]:
        image = np.asarray(section["image"])
        assert len(image) == 501
        # Independent two-sample maximum followed by a one-sample final bin.
        np.testing.assert_array_equal(image[:-1], original[:, :1000].reshape(original.shape[0], 500, 2).max(2).T)
        np.testing.assert_array_equal(image[-1], original[:, -1])
        assert len(section["time_bin_edges_us"]) == len(image) + 1
    np.testing.assert_array_equal(result["cscan"]["image"], envelope[:, :, 8:21].max(2))
    assert result["gate"]["sample_count"] == 13


def test_gate_is_read_only_and_rms_has_correct_definition(saved):
    path, rf, _, _ = saved
    def hashes():
        return {p.relative_to(path): hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob("*") if p.is_file()}
    before = hashes()
    peak = sam_view(path, gate_start_us=.5, gate_end_us=.55)
    rms = sam_view(path, gate_start_us=.5, gate_end_us=.55, gate_mode="rms_rf")
    np.testing.assert_allclose(rms["cscan"]["image"], np.sqrt(np.mean(rf[:, :, :21].astype(float) ** 2, axis=2)), rtol=1e-7)
    assert rms["cscan"]["image"] != peak["cscan"]["image"]
    assert hashes() == before


@pytest.mark.parametrize("kwargs", [{"x_index": 20}, {"y_index": 16}, {"time_index": 1001},
    {"gate_start_us": 0}, {"gate_end_us": 4}, {"gate_start_us": .501, "gate_end_us": .502},
    {"gate_start_us": float("nan")}, {"gate_mode": "invented"}])
def test_invalid_slice_or_gate_rejected(saved, kwargs):
    with pytest.raises(ValueError):
        sam_view(saved[0], **kwargs)


@contextmanager
def dataset_client(saved):
    from virtual_microscopy.server import app
    path, _, _, manifest = saved
    class Manager:
        def get_manifest(self, dataset_id):
            if dataset_id != path.name:
                raise KeyError(dataset_id)
            return manifest
        def list_datasets(self):
            return [manifest]
        def dataset_path(self, dataset_id):
            self.get_manifest(dataset_id)
            return path
    previous = getattr(app.state, "volume_jobs", None)
    app.state.volume_jobs = Manager()
    try:
        yield TestClient(app)
    finally:
        app.state.volume_jobs = previous


def test_api_frozen_views_export_and_incomplete_guard(saved):
    path, rf, _, manifest = saved
    with dataset_client(saved) as client:
        prefix = f"/api/v2/datasets/{path.name}"
        listing = client.get("/api/v2/datasets").json()["datasets"]
        assert listing[0]["name"] == "Asymmetric fixture"
        assert listing[0]["time_range_us"] == [.5, 3]
        result = client.get(prefix + "/view?x_index=2&y_index=3&time_index=7")
        assert result.status_code == 200, result.text
        np.testing.assert_array_equal(result.json()["ascan"]["amplitude"], rf[3, 2])
        assert client.get(prefix + "/view?time_index=99999").status_code == 422
        assert client.get(prefix + "/view?gate_mode=bad").status_code == 422
        assert client.get("/api/v2/datasets/missing").status_code == 404
        response = client.get(prefix + "/export")
        assert response.status_code == 200, response.text
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert json.loads(archive.read("manifest.json"))["dataset_id"] == path.name
            for name in archive.namelist():
                assert not name.startswith("/") and ".." not in Path(name).parts
                assert archive.read(name) == (path / name).read_bytes()
        assert not list(path.parent.glob("vm-export-*.zip"))
        manifest["complete"] = False
        manifest["state"] = "cancelled"
        assert client.get(prefix + "/view").status_code == 409
        assert client.get(prefix + "/export").status_code == 409
