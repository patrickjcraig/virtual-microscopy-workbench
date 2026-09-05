"""Bounded, ordered continuous vertical material paths for scalar instruments.

This removes Z-raster quantization for authored convex primitives. It does not
model lateral area integration, elastic waves, refraction or experimental truth.
"""
from __future__ import annotations

from dataclasses import dataclass
import heapq
from math import exp, log, sqrt

import numpy as np

from .materials import (MATERIALS, WATER_ATTENUATION_DB_MM_AT_50MHZ,
                        WATER_IMPEDANCE_MRAYL, WATER_SOUND_SPEED_M_S,
                        linear_attenuation_mm)

PATH_CONTRACT_VERSION = "ordered-column-paths-1"
MAX_COLUMNS = 500_000
MAX_CANDIDATE_TESTS = 50_000_000
MAX_EVENT_WORK = 250_000_000
MAX_WORKSPACE_BYTES = 512 * 1024 ** 2
MATERIAL_IDS = tuple(MATERIALS)
LABELS = {name: index + 1 for index, name in enumerate(MATERIAL_IDS)}
ECHO_FLOOR = 1e-8
SAM_F_NUMBER = 2.0


@dataclass
class ColumnPaths:
    column_offsets: np.ndarray
    z_end_mm: np.ndarray
    material_label: np.ndarray
    shape: tuple[int, int]
    diagnostics: dict


@dataclass
class ColumnEchoes:
    rows: np.ndarray
    cols: np.ndarray
    times_us: np.ndarray
    amplitudes: np.ndarray
    max_time_us: float
    depths_mm: np.ndarray


def _inputs(twin, x_mm, y_mm, include_defects):
    """Cheap bounded checks; the public boundary already validates full twins."""
    size = np.asarray(twin["size_mm"], dtype=np.float64)
    if size.shape != (3,) or not np.isfinite(size).all() or np.any(size <= 0) or np.any(size > [100, 100, 6]):
        raise ValueError("Continuous columns require finite positive specimen extents up to 100 × 100 × 6 mm.")
    try:
        count_x, count_y = len(x_mm), len(y_mm)
    except TypeError as exc:
        raise ValueError("Column X/Y coordinates must be nonempty one-dimensional vectors.") from exc
    if count_x * count_y > MAX_COLUMNS:
        raise ValueError("Continuous columns exceed the 500,000 padded XY-column limit. Reduce the raster or halo.")
    if any(getattr(values, "ndim", 1) != 1 for values in (x_mm, y_mm)):
        raise ValueError("Column X/Y coordinates must be nonempty one-dimensional vectors.")
    x, y = np.asarray(x_mm, dtype=np.float64), np.asarray(y_mm, dtype=np.float64)
    if x.ndim != 1 or y.ndim != 1 or not x.size or not y.size:
        raise ValueError("Column X/Y coordinates must be nonempty one-dimensional vectors.")
    if x.size * y.size > MAX_COLUMNS:
        raise ValueError("Continuous columns exceed the 500,000 padded XY-column limit. Reduce the raster or halo.")
    if (not np.isfinite(x).all() or not np.isfinite(y).all() or
            np.any(x < 0) or np.any(x > size[0]) or np.any(y < 0) or np.any(y > size[1])):
        raise ValueError("Column centers must be finite global coordinates inside the specimen.")
    if len(twin["objects"]) > 600:
        raise ValueError("Continuous columns support at most 600 ordered primitives.")
    objects = [obj for obj in twin["objects"] if include_defects or obj.get("role", "structure") != "defect"]
    candidate_tests = len(objects) * int(x.size) * int(y.size)
    if candidate_tests > MAX_CANDIDATE_TESTS:
        raise ValueError("Continuous path candidate tests exceed 50 million. Reduce the raster, objects or halo.")
    centers = np.empty((len(objects), 3), dtype=np.float64)
    extents = np.empty_like(centers)
    materials = np.empty(len(objects), dtype=np.uint8)
    shapes = np.empty(len(objects), dtype=np.uint8)
    for index, obj in enumerate(objects):
        c, s = np.asarray(obj["center_mm"], dtype=np.float64), np.asarray(obj["size_mm"], dtype=np.float64)
        if c.shape != (3,) or s.shape != (3,) or not np.isfinite(c).all() or not np.isfinite(s).all() or np.any(s <= 0):
            raise ValueError(f"Invalid primitive coordinates or extents: {obj.get('id', index)}.")
        if obj["shape"] not in ("box", "cylinder", "sphere") or obj["material"] not in LABELS:
            raise ValueError(f"Unsupported continuous primitive or material: {obj.get('id', index)}.")
        centers[index], extents[index] = c, s
        shapes[index] = {"box": 0, "cylinder": 1, "sphere": 2}[obj["shape"]]
        materials[index] = LABELS[obj["material"]]
    low, high = centers - extents / 2, centers + extents / 2
    positive_z = ((np.minimum(high[:, 2], size[2]) > np.maximum(low[:, 2], 0)) |
                  ((centers[:, 2] >= 0) & (centers[:, 2] <= size[2])))
    return size, x, y, objects, centers, extents, low, high, shapes, materials, positive_z, candidate_tests


def _bounds(inputs):
    _, x, y, _, _, _, low, high, _, _, positive_z, _ = inputs
    bound = np.zeros((len(y), len(x)), dtype=np.uint16)
    for index in np.flatnonzero(positive_z):
        ix = np.flatnonzero((x >= low[index, 0]) & (x <= high[index, 0]))
        iy = np.flatnonzero((y >= low[index, 1]) & (y <= high[index, 1]))
        if ix.size and iy.size:
            bound[np.ix_(iy, ix)] += 2
    return bound


def column_interface_bounds(twin_dict, x_mm_1d, y_mm_1d, include_defects=True) -> np.ndarray:
    """Return uint16 B=2*N per column, with continuous Z overlap and XY boxes.

    Coordinates need not be sorted: output order is exactly Y then X as supplied.
    The result is independent of depth_samples and deliberately includes curved
    shape tangencies and bounding-box corners as conservative upper bounds.
    """
    return _bounds(_inputs(twin_dict, x_mm_1d, y_mm_1d, include_defects))


def _path_preflight(inputs, bound):
    _, x, y, _, _, _, _, _, _, _, _, candidate_tests = inputs
    b = bound.astype(np.uint64)
    event_bound = int(b.sum())
    segment_bound = event_bound + bound.size
    event_work = int((b * (1 + np.ceil(np.log2(np.maximum(2, b))).astype(np.uint64))).sum())
    if event_work > MAX_EVENT_WORK:
        raise ValueError("Continuous path event work exceeds 250 million units. Reduce overlap, raster or halo.")
    workspace = (32 * segment_bound + 64 * event_bound + 8 * (bound.size + 1) +
                 bound.nbytes + 8 * (x.size + y.size) + 16 * 1024 ** 2)
    if workspace > MAX_WORKSPACE_BYTES:
        raise ValueError("Continuous path buffers exceed the 512 MiB numerical workspace budget. Use smaller row blocks or reduce raster/overlap.")
    return {"candidate_tests": candidate_tests, "event_bound": event_bound,
            "event_work_bound": event_work, "segment_bound": segment_bound,
            "estimated_path_workspace_bytes": workspace}


def build_column_paths(twin_dict, x_mm_1d, y_mm_1d, include_defects=True) -> ColumnPaths:
    """Intersect ordered primitives and return compact full-depth partitions.

    Only one column's events and active heap are Python working state. Global
    paths are preallocated typed arrays and compacted once; 32 bytes/possible
    segment covers simultaneous capacity/final arrays and 64 bytes/possible event
    plus the reserve conservatively covers event sorting, the heap and metadata.
    """
    inputs = _inputs(twin_dict, x_mm_1d, y_mm_1d, include_defects)
    size, x, y, objects, centers, extents, low, high, shapes, materials, positive_z, _ = inputs
    bound = _bounds(inputs)
    diagnostics = _path_preflight(inputs, bound)
    depth = float(size[2])
    tolerance = float(32 * np.finfo(np.float64).eps * max(1., depth))
    diagnostics.update(contract_version=PATH_CONTRACT_VERSION, tau_z_mm=tolerance,
                       columns=bound.size, primitive_intersections=0, adjusted_endpoint_count=0,
                       max_endpoint_adjustment_mm=0., interface_count=0, specimen_depth_mm=depth,
                       coincidence_policy="nontransitive first-endpoint groups; exact specimen endpoints take precedence")
    offsets = np.empty(bound.size + 1, dtype=np.uint64)
    ends = np.empty(diagnostics["segment_bound"], dtype=np.float64)
    labels = np.empty(diagnostics["segment_bound"], dtype=np.uint8)
    used = 0
    for row, yy in enumerate(y):
        y_candidates = positive_z & (yy >= low[:, 1]) & (yy <= high[:, 1])
        for col, xx in enumerate(x):
            flat = row * len(x) + col
            offsets[flat] = used
            candidates = np.flatnonzero(y_candidates & (xx >= low[:, 0]) & (xx <= high[:, 0]))
            # A typed event list has two specimen endpoints and at most B events.
            positions = np.empty(2 * len(candidates) + 2, dtype=np.float64)
            owners = np.full(len(positions), -1, dtype=np.int32)
            entering = np.zeros(len(positions), dtype=bool)
            positions[:2] = (0., depth)
            count = 2
            for index in candidates:
                bottom, top = float(low[index, 2]), float(high[index, 2])
                if shapes[index]:
                    rx = (xx - centers[index, 0]) / (extents[index, 0] / 2)
                    ry = (yy - centers[index, 1]) / (extents[index, 1] / 2)
                    radial = rx * rx + ry * ry
                    if shapes[index] == 1:
                        if radial > 1:
                            continue
                    else:
                        q = 1 - rx * rx - ry * ry
                        if q <= 0:  # an exact sphere tangent has no volume path
                            continue
                        half_chord = extents[index, 2] / 2 * sqrt(q)
                        bottom, top = centers[index, 2] - half_chord, centers[index, 2] + half_chord
                bottom, top = max(0., bottom), min(depth, top)
                if top <= bottom and not 0 <= centers[index, 2] <= depth:
                    continue
                if top - bottom <= 2 * tolerance:
                    raise ValueError(f"Primitive {objects[index].get('id', index)} has an ambiguous thin intersection at column "
                                     f"(row={row}, col={col}, x={xx:.17g}, y={yy:.17g}): "
                                     f"{top-bottom:.17g} mm <= 2*tau_z ({2*tolerance:.17g} mm).")
                positions[count:count+2] = bottom, top
                owners[count:count+2] = index
                entering[count] = True
                count += 2
            diagnostics["primitive_intersections"] += (count - 2) // 2
            order = np.argsort(positions[:count], kind="stable")
            active = np.zeros(len(objects), dtype=bool)
            heap = []
            previous_boundary, winner_label = 0., 0
            first = 0
            while first < count:
                last = first + 1
                first_value = float(positions[order[first]])
                while last < count and positions[order[last]] - first_value <= tolerance:
                    last += 1
                anchor = depth if positions[order[last-1]] == depth else first_value
                # Specimen zero cannot share a group with a negative endpoint,
                # because every interval was already clipped to the domain.
                for location in order[first:last]:
                    delta = abs(float(positions[location]) - anchor)
                    if delta:
                        diagnostics["adjusted_endpoint_count"] += 1
                        diagnostics["max_endpoint_adjustment_mm"] = max(diagnostics["max_endpoint_adjustment_mm"], delta)
                if anchor > previous_boundary:
                    if used > int(offsets[flat]) and labels[used-1] == winner_label:
                        ends[used-1] = anchor
                    else:
                        ends[used], labels[used] = anchor, winner_label
                        used += 1
                # Apply all coincident starts/ends before observing a winner.
                for location in order[first:last]:
                    owner = int(owners[location])
                    if owner >= 0:
                        active[owner] = entering[location]
                        if entering[location]:
                            heapq.heappush(heap, -owner)
                while heap and not active[-heap[0]]:
                    heapq.heappop(heap)
                winner_label = int(materials[-heap[0]]) if heap else 0
                previous_boundary, first = anchor, last
            begin = int(offsets[flat])
            diagnostics["interface_count"] += used - begin - 1 + int(labels[begin] != 0) + int(labels[used-1] != 0)
    offsets[-1] = used
    # Compact copies keep no hidden oversized backing arrays in the returned object.
    ends, labels = ends[:used].copy(), labels[:used].copy()
    diagnostics.update(segment_count=used, primitive_event_count=2*diagnostics["primitive_intersections"],
                       allocated_path_bytes=offsets.nbytes + ends.nbytes + labels.nbytes)
    return ColumnPaths(offsets, ends, labels, (len(y), len(x)), diagnostics)


def _check_paths(paths: ColumnPaths):
    if (len(paths.shape) != 2 or any(type(n) is not int or n < 1 for n in paths.shape)
            or paths.shape[0] * paths.shape[1] > MAX_COLUMNS):
        raise ValueError("Invalid continuous path shape.")
    n = paths.shape[0] * paths.shape[1]
    if sum(array.nbytes for array in (paths.column_offsets, paths.z_end_mm, paths.material_label)) > MAX_WORKSPACE_BYTES:
        raise ValueError("Continuous path arrays exceed the 512 MiB numerical workspace budget.")
    if (paths.column_offsets.dtype != np.dtype("uint64") or paths.column_offsets.shape != (n+1,)
            or paths.z_end_mm.dtype != np.dtype("float64") or paths.z_end_mm.ndim != 1
            or paths.material_label.dtype != np.dtype("uint8") or paths.material_label.shape != paths.z_end_mm.shape
            or paths.column_offsets[0] != 0 or paths.column_offsets[-1] != len(paths.z_end_mm)
            or np.any(paths.column_offsets[1:] <= paths.column_offsets[:-1])
            or not np.isfinite(paths.z_end_mm).all() or np.any(paths.material_label > len(MATERIAL_IDS))):
        raise ValueError("Invalid continuous path arrays, offsets or material labels.")
    depth = None
    interfaces = 0
    for column in range(n):
        start, stop = int(paths.column_offsets[column]), int(paths.column_offsets[column+1])
        z = paths.z_end_mm[start:stop]
        if z[0] <= 0 or np.any(np.diff(z) <= 0):
            raise ValueError("Every continuous path segment must have positive depth length.")
        if depth is None:
            depth = float(z[-1])
        if z[-1] != depth or depth > 6:
            raise ValueError("Continuous columns must partition one identical full specimen depth.")
        label = paths.material_label[start:stop]
        interfaces += np.count_nonzero(label[1:] != label[:-1]) + int(label[0] != 0) + int(label[-1] != 0)
    return n, int(interfaces), paths.column_offsets.nbytes + paths.z_end_mm.nbytes + paths.material_label.nbytes


def column_xray_integrals(paths: ColumnPaths, energy_kev: float) -> np.ndarray:
    """Float64 optical depth, using only the final ordered material partition."""
    n, _, path_bytes = _check_paths(paths)
    if path_bytes + n*8 + 16*1024**2 > MAX_WORKSPACE_BYTES:
        raise ValueError("Continuous X-ray integration exceeds the 512 MiB numerical workspace budget.")
    lookup = np.array([0.] + [linear_attenuation_mm(name, energy_kev) for name in MATERIAL_IDS])
    optical_depth = np.empty(n, dtype=np.float64)
    for col in range(n):
        start, stop = int(paths.column_offsets[col]), int(paths.column_offsets[col+1])
        z = paths.z_end_mm[start:stop]
        lengths = np.diff(z, prepend=0.)
        optical_depth[col] = np.dot(lengths, lookup[paths.material_label[start:stop]])
    return optical_depth.reshape(paths.shape)


def column_acoustic_echoes(paths: ColumnPaths, frequency_mhz: float, focus_mm: float,
                           *, apply_focus: bool = True) -> ColumnEchoes:
    """Primary signed pressure echoes at exact authored continuous depths.

    Return pre-standoff echoes on the specimen-top time axis. Upstream loss and
    reciprocal interface transmission continue even when an echo is below floor.
    The scalar normal-incidence law at a curved boundary remains an approximation.
    """
    n, capacity, path_bytes = _check_paths(paths)
    if not np.isfinite(frequency_mhz) or not 10 <= frequency_mhz <= 150 or not np.isfinite(focus_mm) or not 0 <= focus_mm <= 6:
        raise ValueError("Acoustic frequency/focus must be finite and within 10–150 MHz / 0–6 mm.")
    if path_bytes + capacity*160 + 16*1024**2 > MAX_WORKSPACE_BYTES:
        raise ValueError("Continuous echo arrays exceed the 512 MiB numerical workspace budget. Use smaller row blocks.")
    records = [MATERIALS[name] for name in MATERIAL_IDS]
    speed = np.array([WATER_SOUND_SPEED_M_S] + [m["sound_speed_m_s"] for m in records]) / 1000
    impedance = np.array([WATER_IMPEDANCE_MRAYL] + [m["impedance_mrayl"] for m in records])
    losses = np.array([WATER_ATTENUATION_DB_MM_AT_50MHZ * (frequency_mhz/50)**2] +
                      [m["attenuation_db_mm_at_50mhz"] * (frequency_mhz/50)**m["attenuation_frequency_exponent"] for m in records])
    rayleigh = 2 * (WATER_SOUND_SPEED_M_S/1000/frequency_mhz) * SAM_F_NUMBER**2
    rows, cols = np.empty(capacity, dtype=np.int64), np.empty(capacity, dtype=np.int64)
    times, amplitudes, depths = (np.empty(capacity, dtype=np.float64) for _ in range(3))
    count, latest = 0, 0.
    for col in range(n):
        start, stop = int(paths.column_offsets[col]), int(paths.column_offsets[col+1])
        previous, z, travel, factor = 0, 0., 0., 1.
        for segment in range(start, stop+1):
            current = int(paths.material_label[segment]) if segment < stop else 0
            r = (impedance[current] - impedance[previous]) / (impedance[current] + impedance[previous])
            gain = 1 / (1 + ((z-focus_mm)/rayleigh)**2) if apply_focus else 1.
            amplitude = factor * r * gain
            if abs(amplitude) >= ECHO_FLOOR:
                rows[count], cols[count] = divmod(col, paths.shape[1])
                times[count], amplitudes[count], depths[count] = travel, amplitude, z
                latest = max(latest, travel)
                count += 1
            factor *= 1-r*r
            if segment < stop:
                end = float(paths.z_end_mm[segment])
                length = end-z
                travel += 2*length/speed[current]
                factor *= exp(-2*log(10)/20*losses[current]*length)
                z = end
            previous = current
    return ColumnEchoes(rows[:count].copy(), cols[:count].copy(), times[:count].copy(),
                        amplitudes[:count].copy(), float(latest), depths[:count].copy())
