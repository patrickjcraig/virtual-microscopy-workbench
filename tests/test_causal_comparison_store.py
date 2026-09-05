"""Immutable causal reports with bounded parsing/publication and offline views."""
from copy import deepcopy
import builtins
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from types import ModuleType
from uuid import uuid4

import pytest

import virtual_microscopy.causal_comparison_store as storage
from virtual_microscopy.causal_comparison_store import CausalComparisonStore


def analysis():
    a, b = str(uuid4()), str(uuid4())
    source = lambda identifier: {"dataset_id": identifier, "kind": "sam_causal_rf_volume",
        "complete": True, "state": "completed", "shape": [16, 16, 3],
        "solver": {"historical_version": "never import me"},
        "coordinates_sha256": {"time_us": "f"*64},
        "class_certificates": {"0": {"diagnostics": {"total_error_bound": 1e-7}}}}
    return {"schema_version": 1, "kind": storage.KIND,
        "processing_version": storage.PROCESSING_VERSION,
        "request": {"reference_dataset_id": a, "candidate_dataset_id": b, "name": "HBM6 \"signed\" Δ"},
        "shape": [16, 16, 3], "axis_order": ["y", "x", "time"],
        "coordinates": {"x_mm": [.1, .2], "y_mm": [.1, .2], "time_us": [0., .1, .2]},
        "extent_mm": [0., 0., 1., 1.],
        "sources": {"reference": source(a), "candidate": source(b)},
        "source_summaries": {"reference": {"id": a, "name": "intact"}, "candidate": {"id": b, "name": "defect"}},
        "compatibility": {"policy": "same_excitation_v1", "compatible": True, "differences": []},
        "metrics": {"full_record": {"rf": {"bias": -.01, "rmse": .1}}, "gate": {}},
        "gate": {"requested_start_us": 0., "requested_end_us": .2, "sample_count": 3},
        "bounds": {"source_sum": [[2e-7]], "complex_total": [[2.00000001e-7]],
            "definition": "Declared model only; not measured accuracy."},
        "gate_maps": {"peak_envelope": {"reference": [[.2]], "candidate": [[.3]], "difference": [[.1]]}},
        "initial_view": {"cursor": {"x_index": 0, "y_index": 0, "time_index": 1},
            "trace": {"rf": [0., -0., -.31], "imaginary": [-.3, .2, -.1], "time_us": [0., .1, .2]},
            "map": [[-.01, .02], [.03, -.04]], "note": "saved initial view"},
        "resource_estimate": {"estimated_peak_bytes": 32*1024**2}, "warnings": []}


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    module = ModuleType("virtual_microscopy.causal_comparisons")
    result = analysis()
    calls = []
    def compute(root, request):
        calls.append((root, request))
        return deepcopy(result)
    module.compute_causal_comparison = compute
    module.causal_comparison_view = lambda root, report, **kwargs: {"new_cursor": kwargs, "id": report["id"]}
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(storage, "_fingerprints", lambda: {"historical.py": "0"*64})
    return CausalComparisonStore(tmp_path), result, calls, module


def rehash(report):
    report["report_sha256"] = storage.json_measure({k: v for k, v in report.items() if k != "report_sha256"})["sha256"]


def write_report(store, report):
    store._path(report["id"]).write_bytes(storage.bounded_payload(report))


def test_publication_hashes_and_lossless_json_csv_roundtrip(fixture):
    store, result, calls, _ = fixture
    report = store.create(result["request"])
    assert len(calls) == 1
    assert store.read(report["id"]) == report
    assert report["request_sha256"] == storage.json_measure(result["request"])["sha256"]
    for role in ("reference", "candidate"):
        assert report["source_manifest_sha256"][role] == storage.json_measure(result["sources"][role])["sha256"]
    assert json.loads(storage.bounded_payload(report)) == report
    rows = list(csv.DictReader(io.StringIO(storage.causal_comparison_csv(report))))
    decoded = {row["field"]: json.loads(row["value_json"]) for row in rows}
    assert decoded == report
    assert len(rows) == len(report) and {row["section"] for row in rows} == {"report"}
    assert storage.bounded_payload(decoded) == store._path(report["id"]).read_bytes()
    assert b"-0.0" in store._path(report["id"]).read_bytes()
    assert not list(store.directory.glob(".*.tmp"))


def test_historical_read_export_initial_view_without_sources_solver_or_compute_import(fixture, monkeypatch):
    store, result, _, _ = fixture
    report = store.create(result["request"])
    before = store._path(report["id"]).read_bytes()
    monkeypatch.delitem(sys.modules, "virtual_microscopy.causal_comparisons")
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name == "flint" or name.endswith(("causal_comparisons", "causal_sam", "layered_time", "schemas")):
            pytest.fail("Historical report reader must not import a current solver or schema")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    loaded = CausalComparisonStore(store.root).read(report["id"])
    assert loaded == report
    assert store.view(report["id"]) == report["initial_view"]
    assert store.view(report["id"], x_index=None, time_index=None) == report["initial_view"]
    assert storage.causal_comparison_csv(loaded)
    assert store.list()[0]["id"] == report["id"]
    assert store._path(report["id"]).read_bytes() == before
    # There never were any original source directories in this synthetic fixture.
    assert not (store.root/result["request"]["reference_dataset_id"]).exists()


def test_changed_cursor_dispatch_and_caller_mutations_do_not_change_saved_report(fixture):
    store, result, _, _ = fixture
    report = store.create(result["request"])
    saved = store._path(report["id"]).read_bytes()
    assert store.view(report["id"], x_index=3, y_index=4)["new_cursor"] == {"x_index": 3, "y_index": 4}
    report["sources"]["reference"]["complete"] = False
    assert store.read(report["id"])["sources"]["reference"]["complete"] is True
    assert store._path(report["id"]).read_bytes() == saved


@pytest.mark.parametrize("collision", ["report", "temporary"])
def test_exclusive_publication_preserves_existing_collision(fixture, monkeypatch, collision):
    store, result, calls, _ = fixture
    identifier = str(uuid4())
    monkeypatch.setattr(storage, "uuid4", lambda: identifier)
    store.directory.mkdir()
    path = store.directory/(f"{identifier}.json" if collision == "report" else f".{identifier}.tmp")
    path.write_bytes(b"must survive")
    with pytest.raises(FileExistsError):
        store.create(result["request"])
    assert path.read_bytes() == b"must survive"
    assert len(calls) == (0 if collision == "report" else 1)
    assert len(list(store.directory.iterdir())) == 1


@pytest.mark.parametrize("failure", ["fsync", "link"])
def test_publication_failure_hides_partial_and_removes_only_owned_stage(fixture, monkeypatch, failure):
    store, result, _, _ = fixture
    def fail(*args): raise OSError("injected publication interruption")
    monkeypatch.setattr(storage.os, failure, fail)
    with pytest.raises(OSError, match="injected"):
        store.create(result["request"])
    assert list(store.directory.iterdir()) == []


def test_publication_is_complete_and_readable_at_exclusive_link_boundary(fixture, monkeypatch):
    store, result, _, _ = fixture
    original = os.link
    observed = []
    def link(source, destination):
        assert not list(store.directory.glob("*.json"))
        staged = json.loads(Path(source).read_bytes())
        original(source, destination)
        observed.append(store.read(staged["id"]))
    monkeypatch.setattr(storage.os, "link", link)
    report = store.create(result["request"])
    assert observed == [report]


@pytest.mark.parametrize("field", ["request", "sources", "bounds", "initial_view", "store_identity"])
def test_tampering_any_frozen_content_rejects(fixture, field):
    store, result, _, _ = fixture
    report = store.create(result["request"])
    report[field]["tampered"] = "changed"
    write_report(store, report)
    with pytest.raises(ValueError): store.read(report["id"])


@pytest.mark.parametrize("kind", ["request", "source"])
def test_inner_frozen_identity_hashes_verified_even_if_report_hash_recomputed(fixture, kind):
    store, result, _, _ = fixture
    report = store.create(result["request"])
    target = report["request"] if kind == "request" else report["sources"]["reference"]
    target["changed"] = True
    rehash(report)
    write_report(store, report)
    with pytest.raises(ValueError, match="checksum mismatch"):
        store.read(report["id"])


@pytest.mark.parametrize("payload", [b'{"same":1,"same":2}', b'{"same":1,"sa\\u006de":2}',
    b'{"value":NaN}', b'{"value":Infinity}', b'{"value":-Infinity}', b'{"value":1e400}',
    b'{"value":-1e400}', b'"\xff"', b'{broken', b'{"nested":{"a":1,"a":2}}'])
def test_bounded_parser_rejects_duplicate_nonfinite_and_malformed_json(tmp_path, payload):
    path = tmp_path/"data.json"
    path.write_bytes(payload)
    with pytest.raises(ValueError): storage.bounded_json_read(path)


@pytest.mark.parametrize("which", ["encoded", "expanded", "depth", "workspace"])
def test_raw_admission_precedes_json_decoder(tmp_path, monkeypatch, which):
    path = tmp_path/"data.json"
    path.write_bytes(b'[' + b'{},'*100 + b'{}]')
    kwargs = {}
    if which == "encoded": kwargs["byte_limit"] = 8
    elif which == "expanded": kwargs["expanded_limit"] = 1000
    elif which == "depth": path.write_bytes(b'['*65+b'0'+b']'*65)
    else: kwargs["retained_bytes"] = storage.MAX_WORKSPACE_BYTES
    monkeypatch.setattr(storage.json, "loads", lambda *a, **k: pytest.fail("Guard must precede decoder"))
    with pytest.raises(ValueError): storage.bounded_json_read(path, **kwargs)


def test_source_reader_accepts_more_than_old_two_megabyte_limit(tmp_path):
    path = tmp_path/"manifest.json"
    value = {"provenance": "s"*(2*1024**2+20)}
    path.write_text(json.dumps(value))
    decoded, expanded = storage.bounded_json_read(path)
    assert decoded == value and expanded < storage.MAX_SOURCE_EXPANDED_BYTES


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), {1: "bad key"},
    2**1025, (1, 2), object()])
def test_direct_serializer_rejects_nonplain_nonfinite_json(value):
    with pytest.raises(ValueError): storage.bounded_payload(value)


def test_direct_serializer_depth_and_cycle_guard():
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ValueError, match="cyclic"): storage.bounded_payload(cycle)
    deep = 1
    for _ in range(66): deep = [deep]
    with pytest.raises(ValueError, match="depth"): storage.bounded_payload(deep)


@pytest.mark.parametrize("budget", ["MAX_REPORT_BYTES", "MAX_REPORT_EXPANDED_BYTES", "MAX_WORKSPACE_BYTES",
    "MAX_SOURCE_MANIFEST_BYTES", "MAX_SOURCE_EXPANDED_BYTES", "MAX_SUMMARY_BYTES"])
def test_create_resource_rejection_precedes_any_publication(fixture, monkeypatch, budget):
    store, result, _, _ = fixture
    monkeypatch.setattr(storage, budget, 1)
    with pytest.raises(ValueError): store.create(result["request"])
    assert not store.directory.exists()


def test_disk_rejection_precedes_stage(fixture, monkeypatch):
    store, result, _, _ = fixture
    monkeypatch.setattr(storage, "_disk", lambda *a: (_ for _ in ()).throw(ValueError("disk reserve")))
    with pytest.raises(ValueError, match="disk reserve"): store.create(result["request"])
    assert not store.directory.exists()


@pytest.mark.parametrize("identifier", ["../outside", "../x.json", "x", "{00000000-0000-0000-0000-000000000000}",
    "ABCDEFAB-1234-1234-1234-123456789012", 3, None])
def test_canonical_identity_and_path_guards(tmp_path, identifier):
    store = CausalComparisonStore(tmp_path)
    with pytest.raises(ValueError): store.read(identifier)
    assert not (tmp_path/"causal-comparisons").exists()


def test_missing_report_is_key_error(tmp_path):
    with pytest.raises(KeyError): CausalComparisonStore(tmp_path).read(str(uuid4()))


@pytest.mark.parametrize("target", ["root", "directory", "leaf"])
def test_symlinks_rejected_before_json_read(tmp_path, monkeypatch, target):
    outside = tmp_path/"outside"
    outside.mkdir()
    root = tmp_path/"root"
    root.mkdir()
    store = CausalComparisonStore(root)
    identifier = str(uuid4())
    link = root if target == "root" else store.directory if target == "directory" else store._path(identifier)
    if target == "root": root.rmdir()
    elif target == "leaf": store.directory.mkdir()
    destination = outside if target != "leaf" else outside/"external.json"
    if target == "leaf": destination.write_text("{}")
    try: link.symlink_to(destination, target_is_directory=target != "leaf")
    except OSError: pytest.skip("Symbolic links unavailable on this host")
    monkeypatch.setattr(storage, "bounded_json_read", lambda *a, **k: pytest.fail("Link guard precedes decoding"))
    with pytest.raises(ValueError, match="links|junctions"): store.read(identifier)


def test_junction_guard_is_applied_even_without_native_junction_creation(tmp_path, monkeypatch):
    store = CausalComparisonStore(tmp_path)
    monkeypatch.setattr(Path, "is_junction", lambda path: path == store.directory, raising=False)
    with pytest.raises(ValueError, match="junction"): store.read(str(uuid4()))


def test_pagination_reads_only_page_and_ignores_preserved_crash_stage(fixture, monkeypatch):
    store, result, _, _ = fixture
    identifiers = ["00000000-0000-0000-0000-00000000000"+str(n) for n in range(1, 6)]
    for identifier in identifiers:
        monkeypatch.setattr(storage, "uuid4", lambda identifier=identifier: identifier)
        store.create(result["request"])
    stale = store.directory/".old-interrupted.tmp"
    stale.write_bytes(b"preserve unknown partial")
    original = store.read
    seen = []
    def read(identifier):
        seen.append(identifier)
        return original(identifier)
    monkeypatch.setattr(store, "read", read)
    page = store.list(limit=2, offset=1)
    assert [item["id"] for item in page] == identifiers[-2:-4:-1]
    assert seen == identifiers[-2:-4:-1]
    assert "sources" not in page[0] and "initial_view" not in page[0]
    assert stale.read_bytes() == b"preserve unknown partial"
    assert store.list(limit=2, offset=100) == []


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": 101}, {"limit": True}, {"offset": -1},
    {"offset": 10000}, {"offset": False}, {"limit": 2.0}])
def test_catalog_pagination_validation(tmp_path, kwargs):
    with pytest.raises(ValueError, match="pagination"): CausalComparisonStore(tmp_path).list(**kwargs)


def test_catalog_scan_cap_rejects_before_decoding_reports(fixture, monkeypatch):
    store, result, _, _ = fixture
    store.create(result["request"])
    (store.directory/".old.tmp").write_bytes(b"untouched")
    monkeypatch.setattr(storage, "MAX_CATALOG_FILES", 1)
    monkeypatch.setattr(store, "read", lambda *a: pytest.fail("Catalog cap precedes report reads"))
    with pytest.raises(ValueError, match="entry limit"): store.list(offset=0)


def test_catalog_refuses_unexpected_entries_and_nonregular_json(fixture):
    store, result, _, _ = fixture
    report = store.create(result["request"])
    unexpected = store.directory/"notes.txt"
    unexpected.write_text("untouched")
    with pytest.raises(ValueError, match="Unexpected"): store.list()
    unexpected.unlink()
    other = store.directory/f"{uuid4()}.json"
    other.mkdir()
    with pytest.raises(ValueError, match="regular"): store.list()
    assert store.read(report["id"]) == report


def test_report_estimate_and_embedded_source_completion_rejected(fixture):
    store, result, _, _ = fixture
    result["resource_estimate"]["estimated_peak_bytes"] = storage.MAX_WORKSPACE_BYTES+1
    with pytest.raises(ValueError, match="workspace"): store.create(result["request"])
    result["resource_estimate"]["estimated_peak_bytes"] = 10
    result["sources"]["reference"]["complete"] = False
    with pytest.raises(ValueError, match="complete"): store.create(result["request"])
    assert not store.directory.exists()


def test_creation_fingerprints_every_numerical_and_read_side_dependency():
    fingerprints = storage._fingerprints()
    for name in ("causal_comparison_math.py", "causal_comparisons.py", "causal_comparison_schemas.py",
                 "causal_comparison_store.py", "causal_datasets.py", "datasets.py"):
        expected = hashlib.sha256(Path(storage.__file__).with_name(name).read_bytes()).hexdigest()
        assert fingerprints[name] == expected
    runtime = storage._runtime()
    assert set(runtime["numerical_packages"]) == {"numpy", "zarr"}
    assert runtime["python"] and runtime["implementation"] and runtime["system"]
    assert "flint" not in runtime["numerical_packages"]


def test_historical_processing_identity_not_compared_with_current_implementation(fixture, monkeypatch):
    store, result, _, _ = fixture
    report = store.create(result["request"])
    monkeypatch.setattr(storage, "PROCESSING_VERSION", "hypothetical-future-processor")
    monkeypatch.setattr(storage, "_fingerprints", lambda: pytest.fail("Never fingerprint historical read"))
    monkeypatch.setattr(storage, "_runtime", lambda: pytest.fail("Never require current packages on read"))
    assert store.read(report["id"]) == report
    assert store.view(report["id"]) == report["initial_view"]
    assert storage.causal_comparison_csv(report)


def test_complete_embedded_large_causal_provenance_not_truncated(fixture):
    store, result, _, _ = fixture
    text = "immutable original provenance " + "a"*(2*1024**2+1)
    for role in ("reference", "candidate"):
        result["sources"][role]["large_provenance"] = text
    report = store.create(result["request"])
    readback = store.read(report["id"])
    # CSV contains large JSON cells; raise only this test reader's parser limit.
    previous = csv.field_size_limit(16*1024**2)
    try:
        rows = list(csv.DictReader(io.StringIO(storage.causal_comparison_csv(readback))))
    finally:
        csv.field_size_limit(previous)
    decoded = {row["field"]: json.loads(row["value_json"]) for row in rows}
    assert decoded == readback
    assert readback["sources"] == result["sources"]


def test_real_saved_source_integration_is_byte_immutable_and_report_remains_offline(tmp_path, monkeypatch):
    import virtual_microscopy.causal_sam as engine
    from virtual_microscopy.causal_datasets import CausalSamDatasetStore
    from virtual_microscopy.causal_comparison_schemas import CausalComparisonRequest
    raw = {"kind": "sam_causal_rf_volume", "twin": {"name": "Small analytical slab",
        "size_mm": [1., 1., .1], "objects": [{"id": "slab", "name": "Slab", "shape": "box",
            "center_mm": [.5, .5, .05], "size_mm": [1., 1., .1], "material": "silicon", "role": "structure"}]},
        "acquisition": {"scan_nx": 16, "scan_ny": 16, "center_frequency_mhz": 10.,
            "sample_rate_mhz": 80., "record_duration_us": .5}}
    prepared = engine.prepare_causal_sam(raw)
    identifier = str(uuid4())
    datasets = CausalSamDatasetStore(tmp_path)
    datasets.create(identifier, prepared.request.model_dump(mode="json", exclude_none=True), prepared.estimate)
    datasets.initialize_arrays(identifier, prepared)
    for item in engine.iter_causal_sam_rows(prepared):
        datasets.write_row(identifier, *item)
    datasets.complete(identifier)
    source = datasets.path(identifier)
    hashes = lambda: {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in source.rglob("*") if p.is_file()}
    before = hashes()
    monkeypatch.setattr(engine, "causal_gamma_response", lambda *a, **k: pytest.fail("No forward solver during comparison"))
    request = CausalComparisonRequest(reference_dataset_id=identifier, candidate_dataset_id=identifier,
        gate_start_us=0., gate_end_us=.5, x_index=2, y_index=3, time_index=4)
    store = CausalComparisonStore(tmp_path)
    report = store.create(request)
    assert report["source_manifest_sha256"]["reference"] == report["source_manifest_sha256"]["candidate"]
    assert report["initial_view"]["traces"]["difference"]["rf"] == [0.]*41
    moved = store.view(report["id"], x_index=5, y_index=6, time_index=7)
    assert moved["cursor"]["x_index"] == 5
    assert store.read(report["id"]) == report
    assert hashes() == before
    source.rename(tmp_path/f"removed-{identifier}")
    assert store.view(report["id"]) == report["initial_view"]
    assert storage.bounded_payload(store.read(report["id"])) == store._path(report["id"]).read_bytes()
    assert storage.causal_comparison_csv(report)
    with pytest.raises(KeyError): store.view(report["id"], x_index=1)
