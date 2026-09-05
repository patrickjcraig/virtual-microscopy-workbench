"""Independent raster science and admission/lifecycle checks for causal SAM."""
from copy import deepcopy
import json
import math
from pathlib import Path

import numpy as np
import pytest

import virtual_microscopy.causal_sam as engine
from virtual_microscopy.causal_sam_schemas import CausalSamVolumeRequest
from virtual_microscopy.materials import MATERIALS, WATER_IMPEDANCE_MRAYL, WATER_SOUND_SPEED_M_S


def box(identifier, material, x, width, *, role="structure", depth=.25, z=.125):
    return {"id": identifier, "name": identifier, "shape": "box", "material": material,
            "center_mm": [x, .5, z], "size_mm": [width, 1., depth], "role": role}


def request(**changes):
    return {"kind": "sam_causal_rf_volume", "twin": {"name": "Independent split slab",
        "size_mm": [1., 1., .25], "objects": [box("left", "silicon", .25, .5), box("right", "copper", .75, .5)]},
        "acquisition": {"scan_nx": 16, "scan_ny": 16, "record_duration_us": .3,
                        "sample_rate_mhz": 400., **changes}}


def gamma(time, a):
    time = np.asarray(time, dtype=float)
    m, f = a.gamma_order, a.center_frequency_mhz
    rate = math.pi*f*a.fractional_bandwidth/math.sqrt(math.expm1(2*math.log(2)/(m+1)))
    result = np.zeros(time.shape, dtype=complex)
    valid = time > 0
    scaled = rate*time[valid]
    magnitude = np.exp(m*(1+np.log(scaled/m))-scaled)
    phase = 2*math.pi*f*(time[valid]-m/rate)
    result[valid] = magnitude*(np.cos(phase)+1j*np.sin(phase))
    return result


def symmetric_slab_oracle(time, a, identifier, thickness=.25):
    # Independently enumerate the exact finite set of causal slab returns.
    medium = MATERIALS[identifier]
    z0, z1 = WATER_IMPEDANCE_MRAYL, medium["impedance_mrayl"]
    r = (z1-z0)/(z1+z0)
    delay = 2000*thickness/medium["sound_speed_m_s"]
    t = np.asarray(time)-2000*a.surface_standoff_mm/WATER_SOUND_SPEED_M_S
    expected = r*gamma(t, a)
    for n in range(1, max(0, math.floor(float(t.max())/delay))+1):
        expected -= (1-r*r)*r*(r*r)**(n-1)*gamma(t-n*delay, a)
    return expected


def arrays(prepared, **kwargs):
    rows = list(engine.iter_causal_sam_rows(prepared, **kwargs))
    return rows, {name: np.concatenate([r[2][name] for r in rows]) for name in (*engine.SIGNALS, "error_bound")}


def test_independent_multi_pixel_slab_pressure_magnitude_and_certificates():
    original = request(surface_standoff_mm=.025, record_start_us=.01)
    before = deepcopy(original)
    p = engine.prepare_causal_sam(original)
    rows, saved = arrays(p)
    assert original == before
    assert p.estimate["unique_stack_count"] == 2
    for col, x in enumerate(p.x_mm):
        expected = symmetric_slab_oracle(p.time_us, p.request.acquisition, "silicon" if x < .5 else "copper")
        actual = saved["rf"][:, col]+1j*saved["imaginary"][:, col]
        assert np.all(np.max(np.abs(actual-expected), axis=1) <= saved["error_bound"][:, col])
        assert np.all(np.max(np.abs(saved["envelope"][:, col]-abs(expected)), axis=1) <= saved["error_bound"][:, col])
    for name, value in saved.items():
        assert value.dtype == np.dtype("float64") and np.isfinite(value).all()
    assert np.max(saved["error_bound"]) <= p.request.acquisition.absolute_tolerance
    assert np.any(saved["rf"] < 0) and np.any(saved["imaginary"] != 0)
    assert not np.allclose(saved["envelope"], np.abs(saved["rf"]), atol=1e-3)
    for row, stop, _, certificates in rows:
        assert stop == row+1
        assert set(certificates) == {str(int(i)) for i in p.class_index[row]}
        assert all(not engine.OPERATIONAL_DIAGNOSTICS.intersection(c) for c in certificates.values())


def test_estimation_preparation_are_pure_and_synthesis_solves_each_exact_class_once(monkeypatch):
    raw = request()
    calls = []
    actual = engine.causal_gamma_response

    def recorded(*args, **kwargs):
        calls.append(args)
        return actual(*args, **kwargs)

    monkeypatch.setattr(engine, "causal_gamma_response", recorded)
    estimate = engine.estimate_causal_sam(raw)
    p = engine.prepare_causal_sam(raw)
    assert not calls and p.cache == {}
    assert estimate == p.estimate
    arrays(p)
    assert len(calls) == 2 == p.estimate["unique_stack_count"]
    assert p.estimate["inverse_work_units"] == sum(e["kernel_estimate"]["inverse_work_units"] for e in p.stack_table)
    assert p.estimate["layer_frequency_work_units"] == sum(e["kernel_estimate"]["layer_frequency_work_units"] for e in p.stack_table)
    p.close()
    assert p.cache == {}


def test_whole_depth_global_coordinates_and_exact_persisted_center_use(monkeypatch):
    raw = request(roi_mm=[.17, .24, .68, .87], scan_nx=32, scan_ny=16)
    observed = []
    builder = engine.build_column_paths

    def record(twin, x, y, defects):
        observed.append((x.copy(), y.copy()))
        return builder(twin, x, y, defects)

    monkeypatch.setattr(engine, "build_column_paths", record)
    p = engine.prepare_causal_sam(raw)
    np.testing.assert_array_equal(p.y_mm, np.concatenate([y for _, y in observed]))
    assert all(np.array_equal(p.x_mm, x) for x, _ in observed)
    np.testing.assert_array_equal(p.x_mm, p.estimate["x_mm"])
    np.testing.assert_array_equal(p.y_mm, p.estimate["y_mm"])
    assert p.time_us.tolist() == [i/400 for i in range(121)]
    assert all(sum(l["thickness_mm"] for l in entry["stack"]["layers"]) == .25 for entry in p.stack_table)
    assert p.metadata["halo_pixels_yx"] == [0, 0] and p.metadata["focus_model"] == "none"
    assert p.metadata["depth_mapping_supported"] is False


def test_one_ulp_boundary_change_is_not_approximately_deduplicated():
    raw = request()
    raw["twin"]["objects"] = [box("left", "silicon", .25, .5, depth=.125, z=.0625),
        box("right", "silicon", .75, .5, depth=math.nextafter(.125, math.inf), z=math.nextafter(.125, math.inf)/2)]
    p = engine.prepare_causal_sam(raw)
    assert p.estimate["unique_stack_count"] == 2
    assert p.stack_table[0]["response_sha256"] != p.stack_table[1]["response_sha256"]


def test_distinct_geometry_labels_only_share_identical_resolved_properties(monkeypatch):
    raw = request()
    copper = deepcopy(engine.MATERIALS["copper"])
    copper["sound_speed_m_s"] = engine.MATERIALS["silicon"]["sound_speed_m_s"]
    copper["impedance_mrayl"] = engine.MATERIALS["silicon"]["impedance_mrayl"]
    monkeypatch.setitem(engine.MATERIALS, "copper", copper)
    p = engine.prepare_causal_sam(raw)
    assert p.estimate["unique_geometry_path_count"] == 2
    assert p.estimate["unique_stack_count"] == 1
    assert p.metadata["geometry_path_sha256"][0][0] != p.metadata["geometry_path_sha256"][0][-1]


@pytest.mark.parametrize("field,value", [("center_frequency_mhz", 100.), ("surface_standoff_mm", .1),
    ("record_start_us", .1), ("precision_bits", 192), ("absolute_tolerance", 1e-8)])
def test_resolved_response_identity_includes_pulse_standoff_time_and_precision(field, value):
    base = engine.prepare_causal_sam(request())
    changes = {field: value}
    if field == "center_frequency_mhz":
        changes["sample_rate_mhz"] = 800.
    changed = engine.prepare_causal_sam(request(**changes))
    assert base.stack_table[0]["response_sha256"] != changed.stack_table[0]["response_sha256"]


def test_include_defects_changes_full_paths_and_signal_without_changing_source():
    raw = request()
    raw["twin"]["objects"].append(box("void", "air", .25, .5, role="defect", depth=.05, z=.125))
    before = deepcopy(raw)
    defective = engine.prepare_causal_sam(raw)
    intact = engine.prepare_causal_sam({**raw, "acquisition": {**raw["acquisition"], "include_defects": False}})
    d = next(engine.iter_causal_sam_rows(defective))[2]
    i = next(engine.iter_causal_sam_rows(intact))[2]
    assert np.max(abs(d["rf"][:, :8]-i["rf"][:, :8])) > 1e-3
    np.testing.assert_array_equal(d["rf"][:, 8:], i["rf"][:, 8:])
    assert raw == before


def test_deterministic_rows_and_certificates_resume_from_verified_cache_without_solve(monkeypatch):
    p = engine.prepare_causal_sam(request())
    first = next(engine.iter_causal_sam_rows(p))
    restored = engine.prepare_causal_sam(request())
    restored.cache = deepcopy(p.cache)

    def forbidden(*args, **kwargs):
        raise AssertionError("A restored class must not be recomputed.")

    monkeypatch.setattr(engine, "causal_gamma_response", forbidden)
    resumed = list(engine.iter_causal_sam_rows(restored, skip_rows={0, 3}))
    assert [r[0] for r in resumed] == [1, 2, *range(4, 16)]
    assert all(r[3] == first[3] for r in resumed)
    for row in resumed:
        for name in (*engine.SIGNALS, "error_bound"):
            np.testing.assert_array_equal(row[2][name], first[2][name])


def test_cancel_never_publishes_partial_row_and_skip_happens_before_synthesis(monkeypatch):
    p = engine.prepare_causal_sam(request())
    stopped = False
    iterator = engine.iter_causal_sam_rows(p, cancelled=lambda: stopped)
    assert next(iterator)[0] == 0
    stopped = True
    assert list(iterator) == []
    empty = engine.prepare_causal_sam(request())
    monkeypatch.setattr(engine, "causal_gamma_response", lambda *a, **k: pytest.fail("Skipped/canceled rows reached synthesis."))
    assert list(engine.iter_causal_sam_rows(empty, skip_rows=range(16))) == []
    assert list(engine.iter_causal_sam_rows(empty, cancelled=lambda: True)) == []


def test_cancel_after_bounded_class_solve_discards_unpublished_class(monkeypatch):
    p = engine.prepare_causal_sam(request())
    stopped = False
    kernel = engine.causal_gamma_response

    def solve(*args, **kwargs):
        nonlocal stopped
        result = kernel(*args, **kwargs)
        stopped = True
        return result

    monkeypatch.setattr(engine, "causal_gamma_response", solve)
    assert list(engine.iter_causal_sam_rows(p, cancelled=lambda: stopped)) == []
    assert p.cache == {}


@pytest.mark.parametrize("option,value", [("focus_mm", .1), ("depth_samples", 1024),
    ("water_standoff_mm", .1), ("loss_db_mm", 1.), ("path_model", "voxel_centers_v1"),
    ("observation_model", "gaussian"), ("include_defects", 1), ("scan_nx", True),
    ("scan_ny", 65), ("precision_bits", 128.), ("roi_mm", [.2, .2, .1, .8]),
    ("roi_mm", [-.1, 0., .5, .5]), ("roi_mm", [0., 0., 1.1, 1.]),
    ("roi_mm", [0., 0., float("nan"), 1.])])
def test_strict_unsupported_and_invalid_requests(option, value):
    with pytest.raises(ValueError):
        CausalSamVolumeRequest.model_validate(request(**{option: value}))


@pytest.mark.parametrize("limit", ["MAX_INVERSE_WORK", "MAX_LAYER_FREQUENCY_WORK", "MAX_PLAN_EXPANDED_BYTES",
    "MAX_VOLUME_BYTES", "MAX_PEAK_BYTES", "MAX_PATH_CANDIDATE_TESTS", "MAX_PATH_EVENT_WORK"])
def test_aggregate_and_memory_bounds_reject_without_any_waveform_allocation(monkeypatch, limit):
    monkeypatch.setattr(engine, limit, 1)
    monkeypatch.setattr(engine, "causal_gamma_response", lambda *a, **k: pytest.fail("Preflight reached synthesis."))
    with pytest.raises(ValueError):
        engine.prepare_causal_sam(request())


def test_real_aggregate_work_rejection_occurs_after_counting_exact_classes(monkeypatch):
    raw = request(record_duration_us=2., center_frequency_mhz=100., sample_rate_mhz=800.)
    raw["twin"]["objects"] = [box(str(i), "silicon", (i+.5)/16, 1/16, depth=.1+i*.005, z=(.1+i*.005)/2) for i in range(16)]
    monkeypatch.setattr(engine, "causal_gamma_response", lambda *a, **k: pytest.fail("Rejected plan evaluated a waveform."))
    with pytest.raises(ValueError, match="Whole causal volume exceeds"):
        engine.prepare_causal_sam(raw)


def test_hbm_six_class_complete_geometry_admission_and_sample_representation():
    twin = json.loads((Path(__file__).resolve().parents[1]/"examples"/"nvidia-h100-hbm6-microstructure.json").read_text())
    p = engine.prepare_causal_sam({"kind": "sam_causal_rf_volume", "twin": twin, "acquisition": {"scan_nx": 32, "scan_ny": 64,
        "roi_mm": [49.425, 39.875, 49.575, 40.125], "center_frequency_mhz": 100., "sample_rate_mhz": 800.}})
    assert p.estimate["shape"] == [64, 32, 1601]
    assert p.estimate["unique_stack_count"] == 6 == p.estimate["unique_geometry_path_count"]
    assert p.estimate["inverse_work_units"] == 62_852_058
    assert p.estimate["signal_bytes"] == 78_692_352
    assert {len(e["stack"]["layers"]) for e in p.stack_table} == {26, 28}
    assert all(sum(l["thickness_mm"] for l in e["stack"]["layers"]) == pytest.approx(2.65) for e in p.stack_table)
    assert all(min(f["samples_xyz"][:2]) >= 2 for f in p.metadata["microfeature_sampling"] if f["included"])
    assert p.cache == {}
    rows = 0
    observed_classes = set()
    for row, stop, products, certificates in engine.iter_causal_sam_rows(p):
        assert row == rows and stop == row+1
        assert all(products[name].shape == (1, 32, 1601) for name in engine.SIGNALS)
        assert np.all(products["error_bound"] <= 1e-7)
        assert all(np.isfinite(products[name]).all() for name in (*engine.SIGNALS, "error_bound"))
        observed_classes.update(certificates)
        rows += 1
    assert rows == 64 and observed_classes == {str(i) for i in range(6)}
    assert len(p.cache) == 6


@pytest.mark.parametrize("kwargs", [{"start_row": True}, {"start_row": 17}, {"skip_rows": [-1]}, {"skip_rows": [True]}, {"cancelled": False}])
def test_invalid_resume_boundary_rejects(kwargs):
    with pytest.raises(ValueError):
        list(engine.iter_causal_sam_rows(engine.prepare_causal_sam(request()), **kwargs))


def test_causal_kind_must_be_explicit_to_preserve_untagged_legacy_request_dispatch():
    raw = request(center_frequency_mhz=50.)
    del raw["kind"]
    with pytest.raises(ValueError, match="kind"):
        CausalSamVolumeRequest.model_validate(raw)


def test_frozen_plan_is_byte_identical_after_default_request_json_roundtrip():
    model = CausalSamVolumeRequest.model_validate(request())
    model.acquisition = type(model.acquisition)(scan_nx=16, scan_ny=16, record_duration_us=.3)
    assert isinstance(model.acquisition.center_frequency_mhz, float)
    first = engine.estimate_causal_sam(model)
    persisted = json.loads(json.dumps(model.model_dump(mode="json", exclude_none=True)))
    second = engine.estimate_causal_sam(persisted)
    assert engine._canonical(first) == engine._canonical(second)
    assert first["stack_table"][0]["response_sha256"] == second["stack_table"][0]["response_sha256"]
