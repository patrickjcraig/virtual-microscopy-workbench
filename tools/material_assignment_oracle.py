"""Independent scalar geometry checks for saved material-assignment evidence.

Uses direct primitive chord equations and midpoint containment, with no producer,
path builder, material library, Pydantic model or acoustic response import.
Endpoint comparisons establish numerical geometry agreement, not measured accuracy.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import json
import math

MATERIAL_IDS = ('silicon', 'copper', 'solder', 'epoxy', 'fr4', 'air')
PARAMETERS = ('density_kg_m3', 'relaxed_modulus_gpa', 'unrelaxed_modulus_gpa', 'relaxation_time_us')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def content_digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def same_number(a, b):
    return type(a) in (int, float) and type(b) in (int, float) and float(a).hex() == float(b).hex()


def chord(obj, x, y, depth):
    """Analytical positive-length Z intersection; inclusive box/cylinder walls."""
    cx, cy, cz = obj['center_mm']
    hx, hy, hz = (v / 2 for v in obj['size_mm'])
    if abs(x-cx) > hx or abs(y-cy) > hy:
        return None
    shape = obj['shape']
    if shape == 'box':
        half = hz
    else:
        radial = ((x-cx)/hx)**2 + ((y-cy)/hy)**2
        if shape == 'cylinder':
            if radial > 1:
                return None
            half = hz
        elif shape == 'sphere':
            if radial >= 1:
                return None
            half = hz * math.sqrt(1-radial)
        else:
            raise ValueError('Unsupported primitive shape in independent oracle.')
    low, high = max(0., cz-half), min(depth, cz+half)
    return (low, high) if high > low else None


def analytical_column(twin, x, y, include_defects):
    """Direct chord endpoint partition, without the production heap/sweep code."""
    depth = float(twin['size_mm'][2])
    if not 0 <= x <= twin['size_mm'][0] or not 0 <= y <= twin['size_mm'][1]:
        raise ValueError('Global point is outside the specimen.')
    intervals = []
    for obj in twin['objects']:
        if obj['role'] == 'defect' and not include_defects:
            continue
        pair = chord(obj, x, y, depth)
        if pair:
            intervals.append((*pair, obj['material']))
    candidates = sorted([0., depth] + [v for low, high, _ in intervals for v in (low, high)])
    # The public geometry contract groups against the first endpoint, not a
    # transitive chain. Boundary priority preserves the exact specimen extent.
    tolerance = 32 * math.ulp(1.) * max(1., depth)
    groups = []
    for point in candidates:
        if not groups or point - groups[-1][0] > tolerance:
            groups.append([point])
        else:
            groups[-1].append(point)
    ends = [0. if 0. in group else depth if depth in group else group[0] for group in groups]
    result = []
    for low, high in zip(ends, ends[1:]):
        if high <= low:
            continue
        midpoint = low+(high-low)/2
        material = 'ambient_water'
        for first, last, name in intervals:
            if first <= midpoint <= last:
                material = name
        if result and result[-1]['material_id'] == material:
            result[-1]['z_end_mm'] = high
            result[-1]['thickness_mm'] = high-result[-1]['z_start_mm']
        else:
            result.append({'z_start_mm': low, 'z_end_mm': high, 'thickness_mm': high-low, 'material_id': material})
    return result


def check_segments(twin, x, y, include_defects, segments, *, endpoint_tolerance_mm=1e-12):
    expected = analytical_column(twin, x, y, include_defects)
    assert len(expected) == len(segments), 'Saved column omitted or added a material interval.'
    errors = []
    previous = 0.
    for actual, truth in zip(segments, expected):
        assert actual['material_id'] == truth['material_id'], 'Ordered material sequence disagrees.'
        assert same_number(actual['z_start_mm'], previous), 'Saved column is not contiguous.'
        assert actual['z_end_mm'] > actual['z_start_mm']
        assert same_number(actual['thickness_mm'], actual['z_end_mm']-actual['z_start_mm'])
        errors.extend(abs(actual[k]-truth[k]) for k in ('z_start_mm', 'z_end_mm'))
        previous = actual['z_end_mm']
    assert same_number(previous, twin['size_mm'][2]), 'The full specimen depth must be retained.'
    assert max(errors, default=0.) <= endpoint_tolerance_mm
    total = sum((Fraction(s['thickness_mm']) for s in segments), Fraction())
    error = abs(total-Fraction(twin['size_mm'][2]))
    assert error <= Fraction(8*math.ulp(float(twin['size_mm'][2])))
    return {'segments_checked': len(segments), 'max_endpoint_difference_mm': max(errors, default=0.),
        'thickness_sum_error_mm': float(error), 'acceptance_tolerance_mm': endpoint_tolerance_mm,
        'full_depth_mm': twin['size_mm'][2], 'independent_material_ids': [s['material_id'] for s in expected]}
