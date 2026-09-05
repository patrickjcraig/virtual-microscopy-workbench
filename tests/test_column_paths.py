"""Independent continuous-path geometry and pre-pulse scalar integration checks."""
from copy import deepcopy
from math import exp, sqrt

import numpy as np
import pytest

from virtual_microscopy.column_paths import (ColumnPaths, LABELS, build_column_paths,
    column_acoustic_echoes, column_interface_bounds, column_xray_integrals)
from virtual_microscopy.materials import linear_attenuation_mm


def primitive(identifier, material="copper", z0=.2, z1=.6, **fields):
    return {"id": identifier, "shape": "box", "material": material,
            "center_mm": [1., 1., (z0+z1)/2], "size_mm": [2., 2., z1-z0], "role": "structure", **fields}


def twin(*objects, depth=1):
    return {"size_mm": [2, 2, depth], "objects": list(objects)}


def segments(paths, row=0, col=0):
    index = row*paths.shape[1]+col
    start, stop = map(int, paths.column_offsets[index:index+2])
    return paths.z_end_mm[start:stop], paths.material_label[start:stop]


def test_asymmetric_global_coordinates_row_major_dtype_and_full_depth_ambient():
    data = twin(primitive("translated", center_mm=[1.2, .8, .4], size_mm=[.2, .4, .3]))
    paths = build_column_paths(data, [1.2, .3], [.8, 1.8, .7])
    assert paths.shape == (3, 2)
    assert paths.column_offsets.dtype == np.uint64 and paths.z_end_mm.dtype == np.float64
    assert paths.material_label.dtype == np.uint8
    for row, col in ((0, 0), (2, 0)):
        z, labels = segments(paths, row, col)
        np.testing.assert_allclose(z, [.25, .55, 1], atol=1e-14)
        np.testing.assert_array_equal(labels, [0, LABELS["copper"], 0])
    for row, col in ((0, 1), (1, 0), (1, 1), (2, 1)):
        np.testing.assert_array_equal(segments(paths, row, col)[0], [1])
        np.testing.assert_array_equal(segments(paths, row, col)[1], [0])
    assert paths.diagnostics["segment_count"] == len(paths.z_end_mm)
    assert paths.diagnostics["allocated_path_bytes"] == sum(a.nbytes for a in (paths.column_offsets, paths.z_end_mm, paths.material_label))


@pytest.mark.parametrize("shape", ["box", "cylinder", "sphere"])
def test_single_primitive_interior_exterior_and_exact_tangency(shape):
    data = twin(primitive("shape", shape=shape, center_mm=[1, 1, .5], size_mm=[1, 1, 1]))
    paths = build_column_paths(data, [1, 1.25, 1.5, 1.51], [1])
    lengths = column_xray_integrals(paths, 80)[0] / linear_attenuation_mm("copper", 80)
    if shape == "sphere":
        np.testing.assert_allclose(lengths, [1, 2*sqrt(.5**2-.25**2), 0, 0], atol=1e-10)
    else:
        np.testing.assert_allclose(lengths, [1, 1, 1, 0], atol=1e-12)
    bounds = column_interface_bounds(data, [1, 1.25, 1.5, 1.51], [1])
    np.testing.assert_array_equal(bounds, [[2, 2, 2, 0]])


def test_normalized_sphere_chord_and_specimen_clipping():
    # Deliberately retain the normalized z extent rather than infer it from sx.
    data = twin(primitive("sphere", shape="sphere", center_mm=[1, 1, .1], size_mm=[.8, .8, .8+1e-9]))
    paths = build_column_paths(data, [1.2], [1.1])
    half = (.8+1e-9)/2*sqrt(1-(.2/.4)**2-(.1/.4)**2)
    expected = .1+half  # upper portion is clipped at the specimen's z=0
    z, labels = segments(paths)
    np.testing.assert_allclose(z, [expected, 1], atol=1e-12)
    np.testing.assert_array_equal(labels, [LABELS["copper"], 0])
    full = build_column_paths(twin(primitive("cover", z0=-2, z1=3)), [1], [1])
    np.testing.assert_array_equal(segments(full)[0], [1])
    np.testing.assert_array_equal(segments(full)[1], [LABELS["copper"]])


def test_ordered_nested_partial_and_global_defect_precedence_no_double_count():
    data = twin(primitive("z-first-id", "silicon", 0, 1), primitive("a-later-id", "copper", .2, .8),
                primitive("void", "air", .3, .4, role="defect"), primitive("late", "solder", .7, .9))
    paths = build_column_paths(data, [1], [1])
    np.testing.assert_allclose(segments(paths)[0], [.2, .3, .4, .7, .9, 1], atol=1e-14)
    np.testing.assert_array_equal(segments(paths)[1], [LABELS[n] for n in ("silicon", "copper", "air", "copper", "solder", "silicon")])
    expected = sum(length*linear_attenuation_mm(material, 80) for material, length in
                   (("silicon", .3), ("copper", .4), ("air", .1), ("solder", .2)))
    assert exp(-column_xray_integrals(paths, 80)[0, 0]) == pytest.approx(exp(-expected), rel=1e-10, abs=1e-12)
    no_defects = build_column_paths(data, [1], [1], include_defects=False)
    np.testing.assert_array_equal(segments(no_defects)[1], [LABELS[n] for n in ("silicon", "copper", "solder", "silicon")])
    assert column_interface_bounds(data, [1], [1], False)[0, 0] == 6


def test_coincident_boundaries_and_same_material_are_coalesced_without_false_echoes():
    data = twin(primitive("first", z0=.1, z1=.5), primitive("second", z0=.5, z1=.8))
    paths = build_column_paths(data, [1], [1])
    np.testing.assert_allclose(segments(paths)[0], [.1, .8, 1], atol=1e-14)
    np.testing.assert_array_equal(segments(paths)[1], [0, LABELS["copper"], 0])
    echoes = column_acoustic_echoes(paths, 50, .5, apply_focus=False)
    np.testing.assert_allclose(echoes.depths_mm, [.1, .8], atol=1e-14)
    assert paths.diagnostics["primitive_event_count"] == 4 and paths.diagnostics["interface_count"] == 2


def test_near_coincidence_grouping_is_nontransitive_and_preserves_exact_specimen_ends():
    tau = 32*np.finfo(float).eps
    data = twin(primitive("copper", z0=.1, z1=.5), primitive("silicon", "silicon", .5+.75*tau, .8),
                primitive("solder", "solder", .5+1.5*tau, 1-.5*tau))
    paths = build_column_paths(data, [1], [1])
    z, label = segments(paths)
    assert z[-1] == 1
    np.testing.assert_array_equal(label, [0, LABELS["copper"], LABELS["silicon"], LABELS["solder"]])
    assert 0 < z[2]-z[1] <= 2*tau
    assert z[2] > .5+tau  # third endpoint must not join a transitive group
    assert paths.diagnostics["adjusted_endpoint_count"] >= 2
    assert 0 < paths.diagnostics["max_endpoint_adjustment_mm"] <= tau
    repeat = build_column_paths(deepcopy(data), [1], [1])
    assert repeat.diagnostics == paths.diagnostics
    np.testing.assert_array_equal(repeat.z_end_mm, paths.z_end_mm)


@pytest.mark.parametrize("thickness", [1e-14, 1e-30])
def test_ambiguous_positive_thin_intersections_are_rejected_with_locator(thickness):
    data = twin(primitive("tiny", center_mm=[1, 1, .5], size_mm=[1, 1, thickness]))
    assert column_interface_bounds(data, [1], [1])[0, 0] == 2
    with pytest.raises(ValueError, match=r"tiny.*row=0, col=0.*2\*tau_z"):
        build_column_paths(data, [1], [1])


def test_fifteen_micron_non_grid_layer_exact_times_signed_loss_and_focus():
    # .213 mm of water, then a 15 um copper film. All values are independently
    # integrated from the existing material constants before any pulse sampling.
    data = twin(primitive("film", z0=.213, z1=.228))
    paths = build_column_paths(data, [1], [1])
    echoes = column_acoustic_echoes(paths, 75, .221)
    r = (8.96*4.66 - 1.48)/(8.96*4.66 + 1.48)
    water_loss = 10**(-2*.55*(75/50)**2*.213/20)
    copper_loss = 10**(-2*.12*(75/50)**1.5*.015/20)
    rayleigh = 2*(1.48/75)*2**2
    front = water_loss*r/(1+((.213-.221)/rayleigh)**2)
    back = water_loss*(1-r*r)*copper_loss*(-r)/(1+((.228-.221)/rayleigh)**2)
    np.testing.assert_allclose(echoes.times_us, [2*.213/1.48, 2*.213/1.48+2*.015/4.66], rtol=1e-10, atol=1e-10)
    np.testing.assert_allclose(echoes.amplitudes, [front, back], rtol=1e-10, atol=1e-12)
    assert echoes.amplitudes[0] > 0 > echoes.amplitudes[1]
    assert echoes.max_time_us == echoes.times_us[-1]
    moved = build_column_paths(twin(primitive("film", z0=.21337, z1=.22837)), [1], [1])
    translated = column_acoustic_echoes(moved, 75, .22137)
    np.testing.assert_allclose(translated.times_us-echoes.times_us, 2*.00037/1.48, rtol=1e-10, atol=1e-12)


def test_explicit_air_is_not_ambient_and_bottom_echo_returns_to_water():
    empty = build_column_paths(twin(), [1], [1])
    assert column_xray_integrals(empty, 80)[0, 0] == 0
    assert len(column_acoustic_echoes(empty, 50, .5).times_us) == 0
    paths = build_column_paths(twin(primitive("air", "air", 0, 1)), [1], [1])
    assert column_xray_integrals(paths, 80)[0, 0] == pytest.approx(linear_attenuation_mm("air", 80))
    echoes = column_acoustic_echoes(paths, 50, .5, apply_focus=False)
    r = (.001205*.343-1.48)/(.001205*.343+1.48)
    np.testing.assert_allclose(echoes.times_us, [0, 2/.343], rtol=1e-10, atol=1e-10)
    np.testing.assert_allclose(echoes.amplitudes, [r, (1-r*r)*10**(-2*10/20)*(-r)], rtol=1e-10, atol=1e-12)
    np.testing.assert_array_equal(echoes.depths_mm, [0, 1])


def test_multiple_media_transmission_and_losses_follow_independent_prefix_products():
    data = twin(primitive("silicon", "silicon", .2, .4), primitive("film", "copper", .4, .415),
                primitive("epoxy", "epoxy", .415, .5))
    echoes = column_acoustic_echoes(build_column_paths(data, [1], [1]), 50, .4, apply_focus=False)
    lengths = np.array([.2, .2, .015, .085])
    speeds = np.array([1.48, 8.43, 4.66, 2.6])
    impedance = np.array([1.48, 2.329*8.43, 8.96*4.66, 1.2*2.6, 1.48])
    reflection = np.diff(impedance)/(impedance[1:]+impedance[:-1])
    transmission = np.r_[1., np.cumprod(1-reflection[:-1]**2)]
    two_way_loss = 2*np.cumsum(lengths*np.array([.55, .08, .12, 2.]))
    expected_amplitudes = reflection*transmission*10**(-two_way_loss/20)
    np.testing.assert_allclose(echoes.times_us, 2*np.cumsum(lengths/speeds), rtol=1e-10, atol=1e-10)
    np.testing.assert_allclose(echoes.depths_mm, np.cumsum(lengths), atol=1e-12)
    np.testing.assert_allclose(echoes.amplitudes, expected_amplitudes, rtol=1e-10, atol=1e-12)


def test_discarded_early_echo_still_propagates_transmission_and_time(monkeypatch):
    import virtual_microscopy.column_paths as engine
    paths = build_column_paths(twin(primitive("film", z0=.75, z1=1)), [1], [1])
    all_echoes = column_acoustic_echoes(paths, 150, 1)
    assert abs(all_echoes.amplitudes[0]) < abs(all_echoes.amplitudes[1])
    monkeypatch.setattr(engine, "ECHO_FLOOR", abs(all_echoes.amplitudes).mean())
    selected = column_acoustic_echoes(paths, 150, 1)
    np.testing.assert_array_equal(selected.times_us, all_echoes.times_us[1:])
    np.testing.assert_array_equal(selected.amplitudes, all_echoes.amplitudes[1:])


def test_random_actual_counts_and_midpoint_occupancy_obey_continuous_bounds():
    rng = np.random.default_rng(3819)
    objects = [primitive(f"shape{i}", material=("copper", "silicon", "solder", "air")[i%4],
        shape=("box", "cylinder", "sphere")[i%3], center_mm=rng.uniform([.3,.3,.2],[1.7,1.7,.8]).tolist(),
        size_mm=[.6,.6,.4], role="defect" if i%7 == 0 else "structure") for i in range(30)]
    x, y = np.linspace(.2,1.8,9), np.linspace(.3,1.7,7)
    for included in (False, True):
        data = twin(*objects)
        bound = column_interface_bounds(data,x,y,included)
        paths = build_column_paths(data,x,y,included)
        assert bound.dtype == np.uint16
        assert paths.diagnostics["primitive_event_count"] <= int(bound.sum())
        assert paths.diagnostics["segment_count"] <= int(bound.sum()) + bound.size
        assert paths.diagnostics["interface_count"] <= int(bound.sum())
        echoes = column_acoustic_echoes(paths,50,.5)
        counts = np.bincount(echoes.rows*len(x)+echoes.cols, minlength=bound.size).reshape(bound.shape)
        assert np.all(counts <= bound)
        for row, yy in enumerate(y):
            for col, xx in enumerate(x):
                zends, labels = segments(paths,row,col)
                assert np.all(np.diff(zends,prepend=0)>0) and zends[-1] == 1
                assert len(labels) <= int(bound[row,col])+1
                previous = 0.
                for z1, actual in zip(zends,labels):
                    midpoint = (previous+z1)/2
                    expected = 0
                    for obj in objects:
                        if obj["role"] == "defect" and not included:
                            continue
                        scaled = (np.array([xx,yy,midpoint])-obj["center_mm"])/(np.array(obj["size_mm"])/2)
                        inside = np.all(abs(scaled)<=1)
                        if obj["shape"] == "cylinder":
                            inside = scaled[0]**2+scaled[1]**2<=1 and abs(scaled[2])<=1
                        if obj["shape"] == "sphere":
                            inside = np.dot(scaled,scaled)<=1
                        if inside:
                            expected = LABELS[obj["material"]]
                    assert actual == expected
                    previous = z1


def test_no_voxel_z_cap_or_sampled_depth_occupancy_in_bounds():
    objects = [primitive(f"layer{i}", z0=.2+i*.00001, z1=.200005+i*.00001) for i in range(100)]
    data = twin(*objects)
    data["depth_samples"] = 2
    assert column_interface_bounds(data,[1],[1])[0,0] == 200
    paths = build_column_paths(data,[1],[1])
    assert len(paths.z_end_mm) == 201
    data["depth_samples"] = 1024
    other = build_column_paths(data,[1],[1])
    np.testing.assert_array_equal(paths.z_end_mm,other.z_end_mm)
    assert paths.diagnostics == other.diagnostics


@pytest.mark.parametrize("limit,message", [("MAX_CANDIDATE_TESTS","candidate tests"),
    ("MAX_EVENT_WORK","event work"),("MAX_WORKSPACE_BYTES","path buffers")])
def test_direct_call_rejects_budget_before_global_path_arrays(monkeypatch,limit,message):
    import virtual_microscopy.column_paths as engine
    monkeypatch.setattr(engine,limit,1)
    original = np.empty
    def no_global_arrays(shape,*args,**kwargs):
        if shape == 13 or shape == 36:  # 12 column offsets +1; 3 segments ×12
            pytest.fail("Global path buffers allocated before rejecting the request")
        return original(shape,*args,**kwargs)
    monkeypatch.setattr(engine.np,"empty",no_global_arrays)
    with pytest.raises(ValueError,match=message):
        build_column_paths(twin(primitive("box")),[.7,.8,.9,1],[.7,.8,.9])


def test_column_cap_rejects_before_casting_large_coordinate_arrays(monkeypatch):
    import virtual_microscopy.column_paths as engine
    values=np.arange(500_001,dtype=np.int8)
    original=np.asarray
    def no_cast(value,*args,**kwargs):
        if value is values:
            pytest.fail("Over-limit vector cast before column preflight")
        return original(value,*args,**kwargs)
    monkeypatch.setattr(engine.np,"asarray",no_cast)
    with pytest.raises(ValueError,match="500,000"):
        build_column_paths(twin(),values,[1])


def test_echo_capacity_is_checked_before_output_allocation(monkeypatch):
    import virtual_microscopy.column_paths as engine
    paths = build_column_paths(twin(primitive("film")), [1], [1])
    monkeypatch.setattr(engine, "MAX_WORKSPACE_BYTES", 16*1024**2+paths.diagnostics["allocated_path_bytes"]+200)
    monkeypatch.setattr(engine.np, "empty", lambda *args, **kwargs: pytest.fail("Echo output allocated before budget rejection"))
    with pytest.raises(ValueError, match="echo arrays"):
        column_acoustic_echoes(paths, 50, .4)


@pytest.mark.parametrize("x,y", [([], [1]), ([float("nan")],[1]), ([-.1],[1]), ([1],[2.1]), ([[1]],[1])])
def test_invalid_column_coordinates_rejected(x,y):
    with pytest.raises(ValueError):
        build_column_paths(twin(),x,y)


def test_malformed_path_partitions_are_rejected_before_consumption():
    paths=ColumnPaths(np.array([0,2],dtype=np.uint64),np.array([.5,.4]),np.array([0,2],dtype=np.uint8),(1,1),{})
    with pytest.raises(ValueError,match="positive depth"):
        column_xray_integrals(paths,80)
    with pytest.raises(ValueError,match="positive depth"):
        column_acoustic_echoes(paths,50,.4)
