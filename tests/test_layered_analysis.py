"""Geometry-to-layer provenance and a separate, bounded scalar experiment."""
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pytest

from virtual_microscopy.datasets import canonical_json
from virtual_microscopy.layered_analysis import analyze_layered, estimate_layered, extract_layered_column
from virtual_microscopy.layered_schemas import LayeredAnalysisRequest


def request(pulse=False):
    water = {"name": "Water", "impedance_mrayl": 1.48, "sound_speed_m_s": 1480}
    return {"name": "Independent slab", "stack": {"incident": water, "terminal": water,
        "layers": [{"name": "Silicon", "impedance_mrayl": 19.63347, "sound_speed_m_s": 8430,
                    "thickness_mm": .05, "pressure_loss_db_mm": 0, "material_id": "silicon"}]},
        "spectrum": {"start_mhz": 0, "end_mhz": 150, "samples": 257},
        "pulse": {"record_start_us": 0, "record_duration_us": .5} if pulse else None}


def coupon():
    return {"name": "Full-depth column coupon", "size_mm": [4, 3, 1], "objects": [
        {"id": "si", "name": "Si", "material": "silicon", "shape": "box", "center_mm": [2, 1.5, .5], "size_mm": [4, 3, .5]},
        {"id": "cu", "name": "Copper film", "material": "copper", "shape": "box", "center_mm": [2, 1.5, .314], "size_mm": [1, 1, .007]},
        {"id": "air", "name": "Small void", "material": "air", "role": "defect", "shape": "sphere", "center_mm": [2, 1.5, .314], "size_mm": [.002, .002, .002]}]}


def test_column_partitions_full_specimen_with_continuous_metal_void_and_water():
    twin = coupon()
    before = canonical_json(twin)
    extracted = extract_layered_column({"twin": twin, "x_mm": 2, "y_mm": 1.5})
    segments = extracted["segments"]
    assert [s["material_id"] for s in segments] == ["water", "silicon", "copper", "air", "copper", "silicon", "water"]
    np.testing.assert_allclose([s["z_end_mm"] for s in segments], [.25, .3105, .313, .315, .3175, .75, 1], rtol=0, atol=1e-15)
    assert sum(s["thickness_mm"] for s in segments) == 1
    assert all(s["pressure_loss_db_mm"] == 0 for s in segments)
    assert extracted["stack"]["incident"] == extracted["stack"]["terminal"]
    assert canonical_json(twin) == before
    without = extract_layered_column({"twin": twin, "x_mm": 2, "y_mm": 1.5, "include_defects": False})
    assert [s["material_id"] for s in without["segments"]] == ["water", "silicon", "copper", "silicon", "water"]
    assert without["source_column"]["twin"]["objects"] == extracted["source_column"]["twin"]["objects"]


def test_extracted_hbm_column_preserves_six_sites_and_nominal_microfeatures():
    twin = json.loads((Path(__file__).parents[1]/"examples/nvidia-h100-hbm6-microstructure.json").read_text("utf-8"))
    feature = next(p for p in twin["objects"] if p.get("assembly_id") == "hbm-6" and p.get("layer_role") == "microbump")
    x, y, _ = feature["center_mm"]
    extracted = extract_layered_column({"twin": twin, "x_mm": x, "y_mm": y})
    assert len(extracted["source_column"]["twin"]["hbm_assemblies"]) == 6
    assert len(extracted["source_column"]["twin"]["objects"]) == len(twin["objects"])
    assert extracted["segments"][-1]["z_end_mm"] == twin["size_mm"][2]
    assert any(s["material_id"] == "solder" and s["thickness_mm"] < .05 for s in extracted["segments"])
    assert all(s["pressure_loss_db_mm"] == 0 for s in extracted["segments"])


def test_extracted_source_and_edited_assumptions_remain_distinct():
    extracted = extract_layered_column({"twin": coupon(), "x_mm": 2, "y_mm": 1.5})
    body = request() | {"stack": extracted["stack"], "source_column": extracted["source_column"]}
    report = analyze_layered(body)
    assert report["source_status"] == "matches_extracted_column" and report["stack_differences"] == []
    changed = deepcopy(body)
    changed["stack"]["layers"][2]["impedance_mrayl"] = 30
    edited = analyze_layered(changed)
    assert edited["source_status"] == "modified_from_extracted_column"
    assert edited["source_column"] == report["source_column"]
    assert edited["stack_differences"] == [{"path": "/layers/2/impedance_mrayl", "before": body["stack"]["layers"][2]["impedance_mrayl"], "after": 30.0}]
    assert report["spectrum"]["reflection"] != edited["spectrum"]["reflection"]


def test_manual_slab_spectra_preserve_raw_complex_zero_phase_and_energy():
    report = analyze_layered(request())
    assert report["source_status"] == "manual_assumptions" and report["source_column"] is None
    spectrum = report["spectrum"]
    assert spectrum["reflection"]["phase_deg"][0] is None
    assert abs(spectrum["reflection"]["real"][0]) < 1e-14
    z0, z1 = 1.48, 19.63347
    r = (z1-z0)/(z1+z0)
    f = np.asarray(spectrum["frequency_mhz"])
    p = np.exp(-2j*np.pi*f*.05/(8430/1000))
    # Independent equal-exterior slab expression, not the recursive kernel.
    expected = r*(1-p*p)/(1-r*r*p*p)
    actual = np.array(spectrum["reflection"]["real"])+1j*np.array(spectrum["reflection"]["imag"])
    np.testing.assert_allclose(actual, expected, rtol=0, atol=2e-14)
    np.testing.assert_allclose(np.asarray(spectrum["reflectance"])+spectrum["transmittance"], 1, rtol=0, atol=2e-14)
    assert report["pulse"] is None and estimate_layered(request())["time_samples"] == 0
    canonical_json(report)


def test_causal_slab_record_crop_and_standoff_preserve_shared_samples():
    body = request(True)
    body["pulse"].update(record_duration_us=1, surface_standoff_mm=.148)
    full = analyze_layered(body)["pulse"]
    cropped = deepcopy(body)
    cropped["pulse"].update(record_start_us=.25, record_duration_us=.5)
    selected = analyze_layered(cropped)["pulse"]
    assert full["surface_time_us"] == pytest.approx(.2)
    assert full["echoes"]["time_us"][0] == pytest.approx(.2)
    for name in ("rf", "envelope", "primary_rf", "primary_envelope"):
        np.testing.assert_allclose(selected[name], full[name][100:301], rtol=0, atol=2e-13)
    assert len(full["echoes"]["time_us"]) == estimate_layered(body)["impulse_echo_count"]


@pytest.mark.parametrize("mutation", ["negative_loss", "bool_impedance", "text_speed", "nan_thickness", "order", "float_samples", "too_many_samples", "bad_rf_rate", "multilayer_rf", "outside_column", "extra"])
def test_strict_scientific_request_rejections(mutation):
    body = request(True)
    layer = body["stack"]["layers"][0]
    if mutation == "negative_loss": layer["pressure_loss_db_mm"] = -.1
    elif mutation == "bool_impedance": layer["impedance_mrayl"] = True
    elif mutation == "text_speed": layer["sound_speed_m_s"] = "8430"
    elif mutation == "nan_thickness": layer["thickness_mm"] = float("nan")
    elif mutation == "order": body["spectrum"]["start_mhz"] = 151
    elif mutation == "float_samples": body["spectrum"]["samples"] = 257.0
    elif mutation == "too_many_samples": body["spectrum"]["samples"] = 8194
    elif mutation == "bad_rf_rate": body["pulse"]["sample_rate_mhz"] = 100
    elif mutation == "multilayer_rf": body["stack"]["layers"].append(deepcopy(layer))
    elif mutation == "outside_column": body["source_column"] = {"twin": coupon(), "x_mm": 5, "y_mm": 1}
    else: body["destination_path"] = "unrequested-file"
    with pytest.raises(ValueError): LayeredAnalysisRequest.model_validate(body)


def test_estimate_rejects_oversized_expanded_report_before_frequency_kernel(monkeypatch):
    body = request(True)
    body["spectrum"]["samples"] = 8193
    body["pulse"].update(record_duration_us=6, sample_rate_mhz=2400)
    def forbidden(*args, **kwargs):
        raise AssertionError("Frequency kernel ran before resource admission")
    monkeypatch.setattr("virtual_microscopy.layered_acoustics.layered_response", forbidden)
    with pytest.raises(ValueError, match="budget|units|workspace"):
        analyze_layered(body)
