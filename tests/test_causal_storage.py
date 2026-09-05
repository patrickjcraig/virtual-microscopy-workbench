"""Causal typed bytes, class certificates and resumable atomic row publication."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
import zarr

from virtual_microscopy import causal_datasets as storage
from virtual_microscopy.causal_datasets import CausalSamDatasetStore, typed_sha256
from virtual_microscopy.datasets import atomic_json, json_sha256


@pytest.fixture(scope="module")
def prepared_template():
    from virtual_microscopy.causal_sam import prepare_causal_sam, iter_causal_sam_rows
    from virtual_microscopy.causal_sam_schemas import CausalSamVolumeRequest
    request = CausalSamVolumeRequest.model_validate({"kind": "sam_causal_rf_volume", "twin": {
        "schema_version": 1, "name": "Causal storage analytic slab", "size_mm": [4, 3, .5],
        "objects": [{"id": "slab", "name": "Silicon slab", "shape": "box", "material": "silicon",
            "center_mm": [2, 1.5, .25], "size_mm": [4, 3, .1], "role": "structure"}]},
        "acquisition": {"scan_nx": 16, "scan_ny": 16, "center_frequency_mhz": 10,
            "sample_rate_mhz": 80, "record_duration_us": .5}})
    prepared = prepare_causal_sam(request)
    first = next(iter_causal_sam_rows(prepared))
    return prepared, first


def make_store(tmp_path, prepared_template):
    prepared, first = deepcopy(prepared_template)
    store, identifier = CausalSamDatasetStore(tmp_path), str(uuid4())
    store.create(identifier, prepared.request.model_dump(mode="json", exclude_none=True), prepared.estimate)
    store.initialize_arrays(identifier, prepared)
    return store, identifier, prepared, first


def finish(store, identifier, first):
    for y in range(16):
        store.write_row(identifier, y, y+1, deepcopy(first[2]), deepcopy(first[3]))
    return store.complete(identifier)


def files(path):
    return {p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob("*") if p.is_file()}


def test_real_solver_float64_signals_and_certificate_registry(tmp_path, prepared_template):
    store, identifier, prepared, first = make_store(tmp_path, prepared_template)
    manifest = finish(store, identifier, first)
    checked = store.verify_complete(identifier)
    assert manifest == checked
    group = store.open_arrays(identifier)
    for name in storage.SIGNALS:
        assert group[name].dtype == np.dtype("float64")
        assert group[name].chunks == (1, 16, 41)
        np.testing.assert_array_equal(group[name][0:1], first[2][name])
    assert group["rf"][:].min() < 0
    assert group["class_index"].dtype == np.dtype("uint16")
    assert group["error_bound"].dtype == np.dtype("float64")
    assert np.all(group["error_bound"][:] == manifest["total_error_bound"])
    assert manifest["total_error_bound"] <= prepared.request.acquisition.absolute_tolerance
    for name in storage.COORDINATES:
        assert group[name][:].tobytes() == getattr(prepared, name).tobytes()
    assert manifest["class_certificates"]["0"]["diagnostics"] == first[3]["0"]
    assert set(manifest["arrays"]) == {*storage.PRODUCTS, *storage.COORDINATES, "class_index"}
    assert store.commit_bytes(manifest, 0, 1) == 16*(3*41*8+8)
    assert "flint" in manifest["solver"]["runtime"]
    assert "causal_sam_schemas.py" in manifest["solver"]["source_sha256"]
    assert "volume_jobs.py" in manifest["solver"]["source_sha256"]
    assert "causal_processing.py" not in manifest["solver"]["source_sha256"]
    assert str(tmp_path).encode() not in storage.bounded_payload(manifest)


def test_completed_historical_read_without_current_solver_and_immutable(tmp_path, prepared_template, monkeypatch):
    store, identifier, _, first = make_store(tmp_path, prepared_template)
    finish(store, identifier, first)
    before = files(store.path(identifier))
    def forbidden(*args, **kwargs):
        raise AssertionError("Historical reads must not import or run a current solver")
    monkeypatch.setattr(storage, "causal_solver_identity", forbidden)
    import virtual_microscopy.causal_sam as engine
    import virtual_microscopy.layered_time as kernel
    monkeypatch.setattr(engine, "causal_gamma_response", forbidden)
    monkeypatch.setattr(kernel, "causal_gamma_response", forbidden)
    store.verify_complete(identifier)
    cache = store.restore_class_cache(identifier)
    assert cache[0]["rf"].tobytes() == first[2]["rf"][0, 0].tobytes()
    with pytest.raises(ValueError, match="immutable"):
        store.open_arrays(identifier, "r+")
    with pytest.raises(ValueError, match="immutable"):
        store.write_row(identifier, 0, 1, first[2], first[3])
    assert files(store.path(identifier)) == before


def test_cancel_restart_restore_avoids_new_class_forward_work(tmp_path, prepared_template, monkeypatch):
    store, identifier, prepared, first = make_store(tmp_path, prepared_template)
    store.write_row(identifier, *first)
    store.set_state(identifier, "cancelled")
    cache = CausalSamDatasetStore(tmp_path).restore_class_cache(identifier)
    assert set(cache) == {0}
    for name in storage.SIGNALS:
        assert cache[0][name].tobytes() == first[2][name][0, 0].tobytes()
    import virtual_microscopy.causal_sam as engine
    monkeypatch.setattr(engine, "causal_gamma_response", lambda *a, **k: (_ for _ in ()).throw(AssertionError("reacquired")))
    prepared.cache = cache
    for item in engine.iter_causal_sam_rows(prepared, start_row=0, skip_rows=(0,)):
        store.write_row(identifier, *item)
    store.complete(identifier)
    assert store.verify_complete(identifier)["completed_rows"] == 16


@pytest.mark.parametrize("fault", ["rf", "imaginary", "envelope", "error_bound", "class_index", "x_mm", "time_us"])
def test_completed_low_bit_corruption_rejected(tmp_path, prepared_template, fault):
    store, identifier, _, first = make_store(tmp_path, prepared_template)
    finish(store, identifier, first)
    g = zarr.open_group(str(store.path(identifier)/"data.zarr"), mode="r+")
    selection = (0,)*g[fault].ndim
    old = g[fault][selection]
    g[fault][selection] = 1 if fault == "class_index" else np.nextafter(old, float("inf"))
    with pytest.raises(ValueError):
        store.verify_complete(identifier)


def test_partial_corrupt_row_is_repaired_byte_exact(tmp_path, prepared_template):
    store, identifier, _, first = make_store(tmp_path, prepared_template)
    store.write_row(identifier, *first)
    initial = store.manifest(identifier)
    g = zarr.open_group(str(store.path(identifier)/"data.zarr"), mode="r+")
    g["imaginary"][0, 0, 0] = np.nextafter(g["imaginary"][0, 0, 0], float("inf"))
    assert store.verify_chunks(identifier)["completed_rows"] == 0
    assert store.restore_class_cache(identifier) == {}
    store.write_row(identifier, *first)
    repaired = store.manifest(identifier)
    assert repaired["completed_chunks"] == initial["completed_chunks"]
    assert repaired["class_certificates"] == initial["class_certificates"]
    np.testing.assert_array_equal(store.open_arrays(identifier)["imaginary"][0:1], first[2]["imaginary"])


@pytest.mark.parametrize("stop_after", storage.PRODUCTS)
def test_failure_between_product_writes_never_commits_row(tmp_path, prepared_template, monkeypatch, stop_after):
    store, identifier, _, first = make_store(tmp_path, prepared_template)
    original = zarr.Array.__setitem__
    def interrupted(array, selection, value):
        original(array, selection, value)
        if array.name.rsplit("/", 1)[-1] == stop_after:
            raise OSError("injected write interruption")
    with monkeypatch.context() as patch:
        patch.setattr(zarr.Array, "__setitem__", interrupted)
        with pytest.raises(OSError, match="injected"):
            store.write_row(identifier, *first)
    m = store.manifest(identifier)
    assert not m["completed_chunks"] and not m["class_certificates"] and m["completed_rows"] == 0
    store.write_row(identifier, *first)
    assert store.verify_chunks(identifier)["completed_rows"] == 1


def test_manifest_publication_failure_leaves_arrays_uncommitted(tmp_path, prepared_template, monkeypatch):
    store, identifier, _, first = make_store(tmp_path, prepared_template)
    with monkeypatch.context() as patch:
        patch.setattr(storage, "atomic_json", lambda *a: (_ for _ in ()).throw(OSError("publish interrupted")))
        with pytest.raises(OSError, match="publish interrupted"):
            store.write_row(identifier, *first)
    assert store.manifest(identifier)["class_certificates"] == {}
    store.write_row(identifier, *first)
    assert store.verify_chunks(identifier)["completed_rows"] == 1


@pytest.mark.parametrize("damage", ["dtype", "shape", "chunks", "codec", "chunk_bytes", "fill"])
def test_layout_rejected_before_decode(tmp_path, prepared_template, monkeypatch, damage):
    store, identifier, _, first = make_store(tmp_path, prepared_template)
    store.write_row(identifier, *first)
    path = store.path(identifier)/"data.zarr"/"rf"
    raw = json.loads((path/"zarr.json").read_text())
    if damage == "dtype": raw["data_type"] = "float32"
    if damage == "shape": raw["shape"] = [10000, 10000, 10000]
    if damage == "chunks": raw["chunk_grid"]["configuration"]["chunk_shape"] = [10000, 10000, 10000]
    if damage == "codec": raw["codecs"] = [{"name": "zstd", "configuration": {"level": 3}}]
    if damage == "fill": raw["fill_value"] = 0
    if damage == "chunk_bytes":
        (path/"c"/"0"/"0"/"0").write_bytes(b"oversized or short chunk")
        assert store.verify_chunks(identifier)["completed_rows"] == 0
        return
    atomic_json(path/"zarr.json", raw)
    monkeypatch.setattr(zarr, "open_group", lambda *a, **k: (_ for _ in ()).throw(AssertionError("decoded malformed layout")))
    with pytest.raises(ValueError, match="metadata"):
        store.open_arrays(identifier)


@pytest.mark.parametrize("damage", ["nan", "f32", "negative_envelope", "magnitude", "bound", "missing_product", "missing_certificate", "precision", "component", "class_waveform"])
def test_invalid_signals_certificates_never_write(tmp_path, prepared_template, damage):
    store, identifier, _, first = make_store(tmp_path, prepared_template)
    products, certificates = deepcopy(first[2:])
    if damage == "nan": products["rf"][0, 0, 0] = np.nan
    if damage == "f32": products["rf"] = products["rf"].astype("float32")
    if damage == "negative_envelope": products["envelope"][0, 0, 0] = -.1
    if damage == "magnitude": products["envelope"][0, 0, 0] += 1
    if damage == "bound": products["error_bound"][:] = 1
    if damage == "missing_product": products.pop("imaginary")
    if damage == "missing_certificate": certificates.clear()
    if damage == "precision": certificates["0"]["precision_bits"] = 256
    if damage == "component": certificates["0"]["arithmetic_complex_bound"] = 1
    if damage == "class_waveform":
        products["rf"][0, 1, 0] = np.nextafter(products["rf"][0, 1, 0], float("inf"))
    with pytest.raises(ValueError):
        store.write_row(identifier, 0, 1, products, certificates)
    assert store.manifest(identifier)["completed_rows"] == 0
    assert np.isnan(store.open_arrays(identifier)["rf"][:]).all()


@pytest.mark.parametrize("field", ["request", "estimate", "materials", "metadata", "class_certificates", "completion_sha256", "evidence_status"])
def test_completed_manifest_identity_damage_rejected(tmp_path, prepared_template, field):
    store, identifier, _, first = make_store(tmp_path, prepared_template)
    m = finish(store, identifier, first)
    if field == "completion_sha256": m[field] = "0"*64
    elif field == "evidence_status": m[field] = "Measured calibrated experiment"
    elif field == "class_certificates": m[field]["0"]["diagnostics"]["total_error_bound"] /= 2
    else: m[field]["corrupt"] = True
    atomic_json(store.path(identifier)/"manifest.json", m)
    with pytest.raises(ValueError):
        store.verify_complete(identifier)


def test_resume_rejects_changed_solver_but_completed_can_reopen(tmp_path, prepared_template, monkeypatch):
    store, identifier, _, first = make_store(tmp_path, prepared_template)
    original = storage.causal_solver_identity
    monkeypatch.setattr(storage, "causal_solver_identity", lambda v: {**original(v), "modified": True})
    with pytest.raises(ValueError, match="runtime changed"):
        store.validate_identity(identifier)


@pytest.mark.parametrize("field,value", [("total_bytes", 1), ("estimated_peak_bytes", 2**30), ("inverse_work_units", 100_000_001), ("layer_frequency_work_units", 5_000_001)])
def test_creation_resource_rejection_is_prepublication(tmp_path, prepared_template, field, value):
    prepared, _ = deepcopy(prepared_template)
    estimate = deepcopy(prepared.estimate)
    estimate[field] = value
    store, identifier = CausalSamDatasetStore(tmp_path), str(uuid4())
    with pytest.raises(ValueError):
        store.create(identifier, prepared.request.model_dump(mode="json"), estimate)
    assert not store.path(identifier).exists()


def test_creation_disk_failure_is_prepublication(tmp_path, prepared_template, monkeypatch):
    prepared, _ = deepcopy(prepared_template)
    store, identifier = CausalSamDatasetStore(tmp_path), str(uuid4())
    monkeypatch.setattr(storage, "check_disk_space", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        store.create(identifier, prepared.request.model_dump(mode="json"), prepared.estimate)
    assert not store.path(identifier).exists()


def test_strict_ids_and_link_guard(tmp_path, prepared_template, monkeypatch):
    store, identifier, _, first = make_store(tmp_path, prepared_template)
    for value in ("../escape", identifier.upper(), "{bad}"):
        with pytest.raises(ValueError): store.path(value)
    target = store.path(identifier)/"data.zarr"/"rf"
    original = Path.is_junction
    monkeypatch.setattr(Path, "is_junction", lambda p: p == target or original(p))
    monkeypatch.setattr(zarr, "open_group", lambda *a, **k: (_ for _ in ()).throw(AssertionError("decoded linked path")))
    with pytest.raises(ValueError, match="junction"):
        store.open_arrays(identifier)


def test_bounded_json_rejects_structure_before_parse(tmp_path, prepared_template, monkeypatch):
    store, identifier, _, _ = make_store(tmp_path, prepared_template)
    (store.path(identifier)/"manifest.json").write_bytes(b"["+b"{},"*100000+b"{}]")
    monkeypatch.setattr(json, "loads", lambda *a, **k: (_ for _ in ()).throw(AssertionError("expanded huge JSON")))
    with pytest.raises(ValueError, match="expanded-memory"):
        store.manifest(identifier)


def test_typed_hash_preserves_float64_low_bits():
    a = np.array([1.], dtype="float64")
    b = np.nextafter(a, np.inf)
    assert a.astype("float32").tobytes() == b.astype("float32").tobytes()
    assert typed_sha256(a) != typed_sha256(b)


@pytest.mark.parametrize("damage", ["zero_bounds", "model_version", "inverse_work_units", "layer_frequency_work_units", "analytic_alias_bound", "frequency_cutoff_bound"])
def test_certificate_must_match_admitted_deterministic_kernel_plan(tmp_path, prepared_template, damage):
    store, identifier, _, first = make_store(tmp_path, prepared_template)
    products, certificates = deepcopy(first[2:])
    diag = certificates["0"]
    if damage == "zero_bounds":
        for key in ("analytic_alias_bound", "frequency_cutoff_bound", "arithmetic_complex_bound",
                    "arithmetic_envelope_bound", "total_error_bound"):
            diag[key] = 0.
        products["error_bound"][:] = 0.
    elif damage == "model_version":
        diag[damage] = "unrelated-kernel"
    elif damage.endswith("work_units"):
        diag[damage] += 1
    else:
        diag[damage] /= 2
    with pytest.raises(ValueError, match="kernel plan"):
        store.write_row(identifier, 0, 1, products, certificates)
    assert store.manifest(identifier)["completed_rows"] == 0
    assert np.isnan(store.open_arrays(identifier)["rf"][:]).all()


def test_restore_uses_another_committed_representative_after_damage(tmp_path, prepared_template):
    store, identifier, _, first = make_store(tmp_path, prepared_template)
    store.write_row(identifier, *first)
    store.write_row(identifier, 1, 2, first[2], first[3])
    g = zarr.open_group(str(store.path(identifier)/"data.zarr"), mode="r+")
    g["rf"][0, 0, 0] = 1.
    cache = store.restore_class_cache(identifier)
    assert store.manifest(identifier)["completed_chunks"].keys() == {"1"}
    assert cache[0]["rf"].tobytes() == first[2]["rf"][0, 0].tobytes()


def test_actual_output_and_near_certificate_limit_fit_advertised_bytes(tmp_path, prepared_template):
    store, identifier, _, first = make_store(tmp_path, prepared_template)
    # Exercise almost all of the reserved per-class serialized space, with
    # actual canonical headers, copied metadata and all sixteen row registries.
    existing_size = len(storage.bounded_payload(first[3]["0"]))
    first[3]["0"]["test_provenance_padding"] = "p"*(storage.MAX_CLASS_CERTIFICATE_BYTES-existing_size-1100)
    m = finish(store, identifier, first)
    encoded = storage.bounded_payload(m)
    certificate_bytes = len(storage.bounded_payload(m["class_certificates"]["0"]))
    assert .95*storage.MAX_CLASS_CERTIFICATE_BYTES < certificate_bytes <= storage.MAX_CLASS_CERTIFICATE_BYTES
    assert len(encoded) <= storage.MAX_MANIFEST_BYTES
    assert storage._workspace(encoded) <= storage.MAX_EXPANDED_BYTES
    actual = sum(p.stat().st_size for p in store.path(identifier).rglob("*") if p.is_file())
    assert actual <= m["estimate"]["total_bytes"]
    assert store.verify_complete(identifier)["completion_sha256"] == m["completion_sha256"]


def test_underestimated_certificate_metadata_reserve_rejected_before_directory(tmp_path, prepared_template):
    prepared, _ = deepcopy(prepared_template)
    estimate = deepcopy(prepared.estimate)
    ny, nx, nt = estimate["shape"]
    estimate["total_bytes"] = 3*ny*nx*nt*8+ny*nx*10+(ny+nx+nt)*8
    store, identifier = CausalSamDatasetStore(tmp_path), str(uuid4())
    with pytest.raises(ValueError, match="metadata and certificate"):
        store.create(identifier, prepared.request.model_dump(mode="json"), estimate)
    assert not store.path(identifier).exists()


def test_distinct_classes_restored_and_unsolved_class_evaluated_once(tmp_path, prepared_template, monkeypatch):
    from virtual_microscopy import causal_sam as engine
    request = prepared_template[0].request.model_dump(mode="json")
    request["twin"]["objects"][0]["center_mm"][1] = .75
    request["twin"]["objects"][0]["size_mm"][1] = 1.5
    prepared = engine.prepare_causal_sam(request)
    assert len(prepared.stack_table) == 2
    store, identifier = CausalSamDatasetStore(tmp_path), str(uuid4())
    store.create(identifier, prepared.request.model_dump(mode="json"), prepared.estimate)
    store.initialize_arrays(identifier, prepared)
    first = next(engine.iter_causal_sam_rows(prepared))
    store.write_row(identifier, *first)
    prepared.cache = store.restore_class_cache(identifier)
    calls = []
    original = engine.causal_gamma_response
    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(engine, "causal_gamma_response", counting)
    for row in engine.iter_causal_sam_rows(prepared, skip_rows=(0,)):
        store.write_row(identifier, *row)
    store.complete(identifier)
    assert len(calls) == 1
    assert set(store.verify_complete(identifier)["class_certificates"]) == {"0", "1"}
