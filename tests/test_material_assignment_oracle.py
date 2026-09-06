"""Closed-form controls for the delivery verifier, independent of production."""
from copy import deepcopy
import math

import pytest

from tools.material_assignment_oracle import analytical_column, check_segments


def fixture():
    return {'size_mm': [4., 3., 2.], 'objects': [
        {'shape': 'box', 'center_mm': [2., 1.5, 1.], 'size_mm': [2., 2., 1.], 'material': 'silicon', 'role': 'structure'},
        {'shape': 'cylinder', 'center_mm': [2., 1.5, 1.], 'size_mm': [1., 1., 1.5], 'material': 'copper', 'role': 'structure'},
        {'shape': 'sphere', 'center_mm': [2., 1.5, 1.], 'size_mm': [.5, .5, .5], 'material': 'air', 'role': 'defect'}]}


@pytest.mark.parametrize('x,defects,ends,materials', [
    (2., True, [.25, .75, 1.25, 1.75, 2.], ['ambient_water', 'copper', 'air', 'copper', 'ambient_water']),
    (2.125, True, [.25, 1-math.sqrt(3)/8, 1+math.sqrt(3)/8, 1.75, 2.], ['ambient_water', 'copper', 'air', 'copper', 'ambient_water']),
    (2.25, True, [.25, 1.75, 2.], ['ambient_water', 'copper', 'ambient_water']),
    (2.5, True, [.25, 1.75, 2.], ['ambient_water', 'copper', 'ambient_water']),
    (2.75, True, [.5, 1.5, 2.], ['ambient_water', 'silicon', 'ambient_water']),
    (3., True, [.5, 1.5, 2.], ['ambient_water', 'silicon', 'ambient_water']),
    (.5, True, [2.], ['ambient_water']),
    (2., False, [.25, 1.75, 2.], ['ambient_water', 'copper', 'ambient_water']),
])
def test_independent_analytical_endpoint_tables(x, defects, ends, materials):
    result = analytical_column(fixture(), x, 1.5, defects)
    assert [s['z_end_mm'] for s in result] == ends
    assert [s['material_id'] for s in result] == materials


@pytest.mark.parametrize('corruption', ['missing_interval', 'wrong_material', 'cropped_depth', 'gap', 'wrong_thickness'])
def test_delivery_oracle_detects_corrupted_saved_paths(corruption):
    twin = fixture()
    rows = deepcopy(analytical_column(twin, 2., 1.5, True))
    if corruption == 'missing_interval': rows.pop(2)
    elif corruption == 'wrong_material': rows[2]['material_id'] = 'silicon'
    elif corruption == 'cropped_depth': rows[-1]['z_end_mm'] = 1.9
    elif corruption == 'gap': rows[1]['z_start_mm'] += .001
    else: rows[1]['thickness_mm'] += .001
    with pytest.raises(AssertionError): check_segments(twin, 2., 1.5, True, rows)


def test_box_and_sphere_precedence_are_ordered():
    twin = fixture()
    twin['objects'].reverse()
    result = analytical_column(twin, 2., 1.5, True)
    assert [s['material_id'] for s in result] == ['ambient_water', 'copper', 'silicon', 'copper', 'ambient_water']
    assert [s['z_end_mm'] for s in result] == [.25, .5, 1.5, 1.75, 2.]


@pytest.mark.parametrize('xy,count', [
    ((49.42734375, 39.876953125), 26),
    ((49.469531249999996, 39.939453125), 26),
    ((49.47421875, 39.947265625), 26),
    ((10.5, 20.), 28),
])
def test_h100_published_geometry_against_independent_chord_oracle(xy, count):
    import json
    from pathlib import Path
    from virtual_microscopy.material_assignments import build_assignment, inspect_column
    twin = json.loads((Path(__file__).resolve().parents[1]/'examples/nvidia-h100-hbm6-microstructure.json').read_bytes())
    doc = build_assignment({'kind': 'sls_material_assignment', 'name': 'Independent full-depth H100 check',
        'twin': twin, 'include_defects': True, 'bindings': []}, {})
    col = inspect_column(doc, *xy)
    evidence = check_segments(doc['snapshots'][doc['twin_sha256']], *xy, True, col['segments'])
    assert evidence['segments_checked'] == count
    assert evidence['max_endpoint_difference_mm'] == 0
    assert col['coverage']['complete'] is False and col['propagation_available'] is False
    assert ('air' in col['coverage']['missing_material_ids']) == (xy == (10.5, 20.))
