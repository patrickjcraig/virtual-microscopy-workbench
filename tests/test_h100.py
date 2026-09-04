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
from virtual_microscopy.hbm import compose_hbm
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
    assert len(twin.hbm_assemblies) == 6
    assert [stack.functional_state for stack in twin.hbm_assemblies] == ["enabled"] * 5 + ["unknown"]
    assert all(stack.physical_present for stack in twin.hbm_assemblies)
    assert sorted(stack.center_xy_mm for stack in twin.hbm_assemblies) == [
        (10.5, 20), (10.5, 30), (10.5, 40), (49.5, 20), (49.5, 30), (49.5, 40)]
    assert twin.reference.product == "NVIDIA H100 SXM (80 GB HBM3)"
    # Provenance must survive the same JSON boundary as an import or export.
    roundtrip = Twin.model_validate_json(twin.model_dump_json(exclude_none=True))
    assert roundtrip.reference == twin.reference
    assert "not a published" in " ".join(roundtrip.reference.assumptions)
    assert "unknown" in " ".join(roundtrip.reference.assumptions)
    assert roundtrip.image_reference.scale_status == "user_estimate"
    assert roundtrip.image_reference.width_px * roundtrip.image_reference.pixel_size_um == pytest.approx(3187.8)
    assert roundtrip.hbm_assemblies == twin.hbm_assemblies


def test_layered_stack_is_contiguous_and_has_one_label_per_site():
    twin = Twin.model_validate(h100())
    assert len(twin.objects) < 600
    for assembly in twin.hbm_assemblies:
        parts = [p for p in twin.objects if p.assembly_id == assembly.id]
        assert len([p for p in parts if p.layer_role == "dram_die"]) == 8
        assert len([p for p in parts if p.layer_role == "interdie_gap"]) == 8
        assert len([p for p in parts if p.display_label]) == 1
        layers = sorted([p for p in parts if p.layer_role not in ("underfill", "contact")],
                        key=lambda p: p.center_mm[2])
        assert layers[0].center_mm[2] - layers[0].size_mm[2] / 2 == pytest.approx(0.2)
        assert layers[-1].center_mm[2] + layers[-1].size_mm[2] / 2 == pytest.approx(0.82)
        for upper, lower in zip(layers, layers[1:]):
            assert upper.center_mm[2] + upper.size_mm[2] / 2 == pytest.approx(
                lower.center_mm[2] - lower.size_mm[2] / 2)


def test_functional_state_does_not_change_material_geometry():
    original = h100()
    disabled = compose_hbm(original, "hbm-1", {"functional_state": "disabled"})
    enabled_sixth = compose_hbm(original, "hbm-6", {"functional_state": "enabled"})
    assert disabled["objects"] == enabled_sixth["objects"] == original["objects"]
    assert original["hbm_assemblies"][0]["functional_state"] == "enabled"  # pure operation
    np.testing.assert_array_equal(voxelize(original, 64).labels, voxelize(disabled, 64).labels)


def test_twelve_high_update_preserves_unrelated_parts_and_defects():
    original = h100()
    updated = compose_hbm(original, "hbm-6", {"die_count": 12, "die_thickness_um": 34, "gap_um": 8})
    untouched = lambda twin: [p for p in twin["objects"] if p.get("assembly_id") != "hbm-6"]
    assert untouched(original) == untouched(updated)
    assert updated["image_reference"] == original["image_reference"]
    evidence = updated["hbm_assemblies"][5]["evidence"]
    assert "8-high" not in evidence and "eight-high" not in evidence
    assert "current layer count" in evidence
    assert "initial fixture" in updated["description"]
    enabled = compose_hbm(updated, "hbm-6", {"functional_state": "enabled"})
    assert enabled["hbm_assemblies"][5]["evidence"] == evidence
    assert "state unknown" not in evidence
    parts = [p for p in updated["objects"] if p.get("assembly_id") == "hbm-6"]
    assert len([p for p in parts if p["layer_role"] == "dram_die"]) == 12
    assert min(p["center_mm"][2] - p["size_mm"][2] / 2 for p in parts) == pytest.approx(0.216)
    for stack in list(updated["hbm_assemblies"]):
        updated = compose_hbm(updated, stack["id"], {"die_count": 12, "die_thickness_um": 34, "gap_um": 8})
    assert len(updated["objects"]) == 520 < 600
    assert original["hbm_assemblies"][5]["die_count"] == 8


def test_physical_presence_is_explicit_and_restoration_preserves_defect_precedence():
    original = h100()
    absent = compose_hbm(original, "hbm-6", {"physical_present": False})
    assert not any(p.get("assembly_id") == "hbm-6" for p in absent["objects"])
    restored = compose_hbm(absent, "hbm-6", {"physical_present": True})
    assembly_positions = [i for i, p in enumerate(restored["objects"]) if p.get("assembly_id") == "hbm-6"]
    defect_positions = [i for i, p in enumerate(restored["objects"]) if p["role"] == "defect"]
    assert max(assembly_positions) < min(defect_positions)
    np.testing.assert_array_equal(voxelize(original, 64).labels, voxelize(restored, 64).labels)


@pytest.mark.parametrize("mutation", ["thickness", "missing_layer", "extra_layer", "false_absence", "unknown_assembly"])
def test_import_rejects_assembly_geometry_divergence(mutation):
    data = h100()
    layer = next(p for p in data["objects"] if p["id"] == "hbm-1-dram-01")
    if mutation == "thickness":
        data["hbm_assemblies"][0]["die_thickness_um"] = 49
    elif mutation == "missing_layer":
        data["objects"].remove(layer)
    elif mutation == "extra_layer":
        data["objects"].append(layer | {"id": "duplicate-die"})
    elif mutation == "false_absence":
        data["hbm_assemblies"][0]["physical_present"] = False
    else:
        layer["assembly_id"] = "hbm-999"
    with pytest.raises(ValueError, match="compiled primitives disagree|unknown HBM assembly"):
        Twin.model_validate(data)


@pytest.mark.parametrize("parameters", [
    {"die_count": 9}, {"die_thickness_um": 150}, {"center_xy_mm": [1, 30]},
    {"bottom_z_mm": 2.6}, {"gap_um": None}, {"undeclared_parameter": 1},
])
def test_invalid_hbm_parameter_patches_are_rejected(parameters):
    with pytest.raises(ValueError):
        compose_hbm(h100(), "hbm-1", parameters)


def test_interleaved_foreign_material_is_rejected_before_metadata_only_update():
    data = h100()
    base_index = next(i for i, part in enumerate(data["objects"]) if part["id"] == "hbm-6-base")
    film = {
        "id": "foreign-copper-film", "name": "Interleaved film precedence regression",
        "shape": "box", "material": "copper", "role": "structure",
        "center_mm": [49.5, 40, 0.25], "size_mm": [8, 9, 0.05],
    }
    data["objects"].insert(base_index + 1, film)
    # The current ordering lets a later DRAM layer overwrite the copper. If an
    # edit silently grouped all assembly pieces at the base position, the film
    # would instead overwrite that silicon despite only electrical state changing.
    grid = voxelize(data, 64, roi_mm=[45.5, 35.5, 53.5, 44.5], depth_samples=1024)
    x, y, z = np.floor((np.array(film["center_mm"]) - grid.origin_mm) / grid.pitch_mm).astype(int)
    assert grid.labels[y, x, z] == LABELS["silicon"]
    with pytest.raises(ValueError, match="contiguous block"):
        Twin.model_validate(data)
    with pytest.raises(ValueError, match="contiguous block"):
        compose_hbm(data, "hbm-6", {"functional_state": "disabled"})


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
