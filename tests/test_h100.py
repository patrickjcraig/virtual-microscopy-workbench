"""H100 specimen provenance, usable imaging presets, and generation safety.

These are synthetic numerical checks, never measured-device validation.
"""

import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from tools.build_h100_example import h100
from virtual_microscopy.physics import LABELS, simulate, voxelize
from virtual_microscopy.schemas import SimulationRequest, Twin


ROOT = Path(__file__).resolve().parents[1]


def test_h100_file_reproduces_referenced_generator_and_valid_preset():
    data = json.loads((ROOT / "examples" / "nvidia-h100-sxm.json").read_text(encoding="utf-8"))
    assert data == h100()
    twin = Twin.model_validate(data)
    request = SimulationRequest(twin=twin, settings=twin.recommended_settings)
    assert request.settings.probe_x_mm == 22.5
    gpu = next(part for part in twin.objects if part.id == "gh100")
    assert gpu.size_mm[0] * gpu.size_mm[1] == pytest.approx(814)
    assert len([part for part in twin.objects if part.id.startswith("hbm-") and part.id[4:].isdigit()]) == 5
    assert twin.reference.product == "NVIDIA H100 SXM (80 GB HBM3)"
    # Provenance must survive the same JSON boundary as an import or export.
    roundtrip = Twin.model_validate_json(twin.model_dump_json(exclude_none=True))
    assert roundtrip.reference == twin.reference
    assert "not a published" in " ".join(roundtrip.reference.assumptions)
    assert "unknown" in " ".join(roundtrip.reference.assumptions)


@pytest.mark.parametrize("resolution", [128, 192])
def test_h100_preset_resolves_seeded_changes_in_both_modalities(resolution):
    data = h100()
    settings = data["recommended_settings"] | {"resolution": resolution}
    healthy = simulate(data, settings | {"include_defects": False})
    defective = simulate(data, settings)
    x_good, x_bad = (np.asarray(result["xray"]["image"]) for result in [healthy, defective])
    s_good, s_bad = (np.asarray(result["sam"]["image"]) for result in [healthy, defective])

    for image in [x_good, x_bad, s_good, s_bad]:
        assert image.shape == (resolution, resolution)
        assert np.isfinite(image).all()
    assert np.min(x_bad - x_good) >= -1e-12  # replacing solids by air cannot increase attenuation

    # Use the preset's physically selected probe; the gate must expose the
    # intended die/underfill difference, not merely any global image change.
    row = int(settings["probe_y_mm"] / data["size_mm"][1] * resolution)
    col = int(settings["probe_x_mm"] / data["size_mm"][0] * resolution)
    assert s_bad[row, col] > s_good[row, col] + 0.04
    time = np.asarray(defective["ascan"]["time_us"])
    in_gate = (time >= settings["gate_start_us"]) & (time <= settings["gate_end_us"])
    assert max(np.asarray(defective["ascan"]["envelope"])[in_gate]) == pytest.approx(s_bad[row, col], abs=1e-7)

    # A buried terminal void should also produce a local X-ray change, while a
    # remote healthy corner stays invariant when only the defects are toggled.
    void = next(part for part in data["objects"] if part["id"] == "terminal-void")
    vx, vy, _ = void["center_mm"]
    vr, vc = int(vy / 60 * resolution), int(vx / 60 * resolution)
    assert x_bad[vr, vc] > x_good[vr, vc] + 0.05
    assert x_bad[0, 0] == x_good[0, 0]
    assert s_bad[0, 0] == s_good[0, 0]

    # All named synthetic defects must be represented at the supported presets.
    # This does not claim their sampled boundaries or sizes are accurate.
    grid_bad = voxelize(data, resolution, include_defects=True)
    grid_good = voxelize(data, resolution, include_defects=False)
    for defect in [part for part in data["objects"] if part["role"] == "defect"]:
        x, y, z = np.floor(np.asarray(defect["center_mm"]) / grid_bad.pitch_mm).astype(int)
        assert grid_bad.labels[y, x, z] == LABELS["air"]
        assert grid_good.labels[y, x, z] not in [0, LABELS["air"]]
    assert defective["metadata"]["pixel_pitch_um"] == pytest.approx([60_000 / resolution] * 2)
    assert any("undersamples" in warning for warning in defective["metadata"]["warnings"])


def test_h100_builder_refuses_to_overwrite_existing_output(tmp_path):
    destination = tmp_path / "existing-twin.json"
    original = b"preserve supplied user data\n"
    destination.write_bytes(original)
    result = subprocess.run([sys.executable, str(ROOT / "tools" / "build_h100_example.py"),
                             "--output", str(destination)], capture_output=True, text=True)
    assert result.returncode != 0
    assert "FileExistsError" in result.stderr
    assert destination.read_bytes() == original
