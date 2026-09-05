"""Bounded, deduplicated immutable reports; no current solver on historical read."""
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

import virtual_microscopy.observation_comparison_store as storage
from virtual_microscopy.observation_comparison_store import ObservationComparisonStore


def analysis(*, same_source=False):
    identifiers = [str(uuid4()), str(uuid4())]
    source = lambda identifier: {"dataset_id": identifier, "kind": "sam_coherent_observation_volume",
        "complete": True, "state": "completed", "shape": [14, 14, 3],
        "input_sha256": "1"*64, "completion_sha256": "2"*64,
        "maximum_complex_bound": 1e-7, "maximum_magnitude_bound": 1.000000001e-7,
        "solver": {"model_version": "frozen-observation", "historical_version": "never import me"},
        "request": {"name": "HBM6 \"signed\" Δ", "absolute_tolerance": 1e-7},
        "estimate": {"source_manifest": {"kind": "sam_causal_rf_volume",
            "dataset_id": str(uuid4()), "complete": True, "nested_provenance": [1, "original"]},
            "source_manifest_sha256": "3"*64, "operator": {"model": "binomial_3x3_coherent_v1"},
            "inherited_excitation": {"acquisition": {"center_frequency_mhz": 50.,
                "roi_mm": [0., 0., 1., 1.], "precision_bits": 128}}}}
    sources = [source(i) for i in identifiers]
    if same_source:
        identifiers[1], sources[1] = identifiers[0], sources[0]
    snapshots = {storage.json_measure(s)["sha256"]: s for s in sources}
    def summary(source):
        return {"dataset_id": source["dataset_id"], "name": "HBM6 \"signed\" Δ",
            "shape": source["shape"], "manifest_sha256": storage.json_measure(source)["sha256"],
            "input_sha256": source["input_sha256"], "completion_sha256": source["completion_sha256"],
            "maximum_complex_bound": source["maximum_complex_bound"],
            "maximum_magnitude_bound": source["maximum_magnitude_bound"],
            "acquisition": {"center_frequency_mhz": 50., "roi_mm": [0., 0., 1., 1.], "precision_bits": 128},
            "model_version": "frozen-observation", "operator": "binomial_3x3_coherent_v1",
            "absolute_tolerance": 1e-7, "source_dataset_id": source["estimate"]["source_manifest"]["dataset_id"],
            "source_manifest_sha256": "3"*64, "solver_sha256": storage.json_measure(source["solver"])["sha256"]}
    refs = {role: summary(s) for role, s in zip(("reference", "candidate"), sources)}
    return {"schema_version": 1, "kind": storage.KIND, "processing_version": storage.PROCESSING_VERSION,
        "request": {"reference_dataset_id": identifiers[0], "candidate_dataset_id": identifiers[1], "name": "Controlled Δ"},
        "shape": [14, 14, 3], "axis_order": ["y", "x", "time"],
        "coordinates": {"x_mm": [i*.01 for i in range(14)], "y_mm": [i*.01 for i in range(14)], "time_us": [0., .1, .2]},
        "extent_mm": [-.005, .135, -.005, .135], "source_snapshots": snapshots,
        "source_reference": refs["reference"], "source_candidate": refs["candidate"], "source_summaries": refs,
        "compatibility": {"policy": "same_observation_and_excitation_v1", "compatible": True, "differences": []},
        "metrics": {"full_record": {"rf": {"bias": -.01, "rmse": .1}}, "gate": {}},
        "gate": {"requested_start_us": 0., "requested_end_us": .2, "sample_count": 3},
        "bounds": {key: [[2e-7]*14 for _ in range(14)] for key in storage.BOUND_KEYS},
        "gate_maps": {}, "initial_view": {"source_reference": refs["reference"], "source_candidate": refs["candidate"],
            "source_summaries": refs, "trace": {"rf": [0., -0., -.31], "imaginary": [-.3, .2, -.1]},
            "cursor": {"x_index": 0, "y_index": 0, "time_index": 1}},
        "resource_estimate": {"estimated_peak_bytes": 64*1024**2}, "warnings": []}


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    module = ModuleType("virtual_microscopy.observation_comparisons")
    result, calls = analysis(), []
    def compute(root, request):
        calls.append((root, request))
        return deepcopy(result)
    module.compute_observation_comparison = compute
    module.observation_comparison_view = lambda root, report, **kwargs: {"new_cursor": kwargs, "id": report["id"]}
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(storage, "_fingerprints", lambda: {"historical.py": "0"*64})
    return ObservationComparisonStore(tmp_path), result, calls, module


def rehash(report):
    report["report_sha256"] = storage.json_measure({k: v for k, v in report.items() if k != "report_sha256"})["sha256"]


def write_report(store, report):
    store._path(report["id"]).write_bytes(storage.bounded_payload(report))


def refresh_snapshot(result, old_digest):
    source = result["source_snapshots"].pop(old_digest)
    digest = storage.json_measure(source)["sha256"]
    result["source_snapshots"][digest] = source
    for role in ("reference", "candidate"):
        if result[f"source_{role}"]["manifest_sha256"] == old_digest:
            result[f"source_{role}"]["manifest_sha256"] = digest
    return digest


def test_atomic_publication_and_exact_json_csv_roundtrip(fixture):
    store, result, calls, _ = fixture
    report = store.create(result["request"])
    assert len(calls) == 1 and store.read(report["id"]) == report
    rows = list(csv.DictReader(io.StringIO(storage.observation_comparison_csv(report))))
    decoded = {row["field"]: json.loads(row["value_json"]) for row in rows}
    assert decoded == report and len(rows) == len(report)
    assert {row["section"] for row in rows} == {"report"}
    assert storage.bounded_payload(decoded) == store._path(report["id"]).read_bytes()
    assert b"-0.0" in store._path(report["id"]).read_bytes()
    assert not list(store.directory.glob(".*.tmp"))


def test_exact_self_comparison_stores_one_snapshot(fixture):
    store, result, _, _ = fixture
    result.clear(); result.update(analysis(same_source=True))
    report = store.create(result["request"])
    assert len(report["source_snapshots"]) == 1
    assert report["source_reference"] == report["source_candidate"]
    payload = store._path(report["id"]).read_bytes()
    assert payload.count(b'"nested_provenance"') == 1
    assert store.read(report["id"]) == report


def test_historical_reads_and_exports_do_not_import_sources_math_schema_or_flint(fixture, monkeypatch):
    store, result, _, _ = fixture
    report = store.create(result["request"])
    before = store._path(report["id"]).read_bytes()
    monkeypatch.delitem(sys.modules, "virtual_microscopy.observation_comparisons")
    monkeypatch.setattr(storage, "_fingerprints", lambda: pytest.fail("No current fingerprints on read"))
    monkeypatch.setattr(storage, "_runtime", lambda: pytest.fail("No current packages on read"))
    monkeypatch.setattr(storage, "PROCESSING_VERSION", "future")
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if (name == "flint" or name.endswith(("observation_comparisons", "observation_math", "observation_datasets",
                "observation_plan", "causal_sam", "layered_time", "schemas"))):
            pytest.fail("Historical report reader imported a current processor or source reader")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    assert ObservationComparisonStore(store.root).read(report["id"]) == report
    assert store.view(report["id"]) == report["initial_view"]
    assert store.view(report["id"], x_index=None) == report["initial_view"]
    assert storage.observation_comparison_csv(report)
    assert store.list()[0]["id"] == report["id"]
    assert store._path(report["id"]).read_bytes() == before
    assert not (store.root/result["request"]["reference_dataset_id"]).exists()


def test_new_cursor_lazy_dispatch_and_returned_mutations_preserve_saved_bytes(fixture):
    store, result, _, _ = fixture
    report = store.create(result["request"])
    saved = store._path(report["id"]).read_bytes()
    assert store.view(report["id"], x_index=3)["new_cursor"] == {"x_index": 3}
    next(iter(report["source_snapshots"].values()))["complete"] = False
    assert all(s["complete"] for s in store.read(report["id"])["source_snapshots"].values())
    assert store._path(report["id"]).read_bytes() == saved


@pytest.mark.parametrize("collision", ["report", "temporary"])
def test_exclusive_collision_preserves_prior_file(fixture, monkeypatch, collision):
    store, result, calls, _ = fixture
    identifier = str(uuid4()); monkeypatch.setattr(storage, "uuid4", lambda: identifier)
    store.directory.mkdir()
    path = store.directory/(f"{identifier}.json" if collision == "report" else f".{identifier}.tmp")
    path.write_bytes(b"preserve me")
    with pytest.raises(FileExistsError): store.create(result["request"])
    assert path.read_bytes() == b"preserve me" and len(list(store.directory.iterdir())) == 1
    assert len(calls) == (0 if collision == "report" else 1)


@pytest.mark.parametrize("failure", ["fsync", "link"])
def test_crash_before_publication_leaves_no_partial_record(fixture, monkeypatch, failure):
    store, result, _, _ = fixture
    def fail(*args): raise OSError("injected interruption")
    monkeypatch.setattr(storage.os, failure, fail)
    with pytest.raises(OSError, match="injected"): store.create(result["request"])
    assert list(store.directory.iterdir()) == []


def test_link_boundary_only_publishes_a_complete_valid_record(fixture, monkeypatch):
    store, result, _, _ = fixture
    original, observed = os.link, []
    def link(source, destination):
        assert not list(store.directory.glob("*.json"))
        staged = json.loads(Path(source).read_bytes())
        original(source, destination)
        observed.append(store.read(staged["id"]))
    monkeypatch.setattr(storage.os, "link", link)
    report = store.create(result["request"])
    assert observed == [report]


@pytest.mark.parametrize("field", ["request", "source_snapshots", "bounds", "initial_view", "store_identity"])
def test_frozen_content_tampering_rejected(fixture, field):
    store, result, _, _ = fixture
    report = store.create(result["request"])
    report[field]["tampered"] = "changed"
    write_report(store, report)
    with pytest.raises(ValueError): store.read(report["id"])


@pytest.mark.parametrize("target", ["snapshot", "request"])
def test_inner_hashes_still_verified_when_outer_hash_recomputed(fixture, target):
    store, result, _, _ = fixture
    report = store.create(result["request"])
    item = next(iter(report["source_snapshots"].values())) if target == "snapshot" else report["request"]
    item["changed"] = "new value"
    rehash(report); write_report(store, report)
    with pytest.raises(ValueError, match="checksum"): store.read(report["id"])


@pytest.mark.parametrize("target", ["reference", "alias", "initial", "unused", "same_id_twice", "hidden", "nested_summary"])
def test_snapshot_references_and_only_one_copy_contract(fixture, target):
    store, result, _, _ = fixture
    if target == "reference": result["source_reference"]["manifest_sha256"] = "f"*64
    elif target == "alias": result["source_summaries"] = {"reference": {}, "candidate": {}}
    elif target == "initial": result["initial_view"]["source_reference"] = {}
    elif target == "unused":
        result["request"]["candidate_dataset_id"] = result["request"]["reference_dataset_id"]
        result["source_candidate"] = result["source_reference"]
        result["source_summaries"]["candidate"] = result["source_reference"]
        result["initial_view"]["source_candidate"] = result["source_reference"]
    elif target == "same_id_twice":
        digest = result["source_candidate"]["manifest_sha256"]
        result["source_snapshots"][digest]["dataset_id"] = result["source_reference"]["dataset_id"]
        refresh_snapshot(result, digest)
    elif target == "hidden": result["initial_view"]["extra"] = next(iter(result["source_snapshots"].values()))
    else: result["source_reference"]["acquisition"]["new"] = next(iter(result["source_snapshots"].values()))
    with pytest.raises(ValueError): store.create(result["request"])
    assert not store.directory.exists()


@pytest.mark.parametrize("change", ["shape", "missing", "negative", "boolean"])
def test_six_bound_map_structural_contract(fixture, change):
    store, result, _, _ = fixture
    key = storage.BOUND_KEYS[-1]
    if change == "shape": result["bounds"][key] = [[1.]]
    elif change == "missing": del result["bounds"][key]
    else: result["bounds"][key][0][0] = -1. if change == "negative" else True
    with pytest.raises(ValueError, match="six finite"): store.create(result["request"])


@pytest.mark.parametrize("field,value", [("name", "inconsistent label"), ("acquisition", {"center_frequency_mhz": 99.}),
    ("operator", "other"), ("model_version", "other"), ("absolute_tolerance", 1e-6),
    ("source_dataset_id", "00000000-0000-0000-0000-000000000000"),
    ("source_manifest_sha256", "9"*64), ("solver_sha256", "9"*64)])
def test_lightweight_summary_must_agree_with_frozen_provenance(fixture, field, value):
    store, result, _, _ = fixture
    result["source_reference"][field] = value
    with pytest.raises(ValueError, match="summary provenance"): store.create(result["request"])
    assert not store.directory.exists()


@pytest.mark.parametrize("payload", [b'{"same":1,"same":2}', b'{"same":1,"sa\\u006de":2}',
    b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}', b'{"x":1e400}', b'{"x":-1e400}',
    b'"\xff"', b'{broken', b'{"nested":{"a":1,"a":2}}'])
def test_bounded_parser_rejects_malformed_duplicate_nonfinite(tmp_path, payload):
    path = tmp_path/"data.json"; path.write_bytes(payload)
    with pytest.raises(ValueError): storage.bounded_json_read(path)


@pytest.mark.parametrize("which", ["encoded", "expanded", "depth", "workspace"])
def test_raw_budget_guard_precedes_json_decode(tmp_path, monkeypatch, which):
    path = tmp_path/"data.json"; path.write_bytes(b'['+b'{},'*100+b'{}]')
    kwargs = {}
    if which == "encoded": kwargs["byte_limit"] = 8
    elif which == "expanded": kwargs["expanded_limit"] = 1000
    elif which == "depth": path.write_bytes(b'['*65+b'0'+b']'*65)
    else: kwargs["retained_bytes"] = storage.MAX_WORKSPACE_BYTES
    monkeypatch.setattr(json, "loads", lambda *a, **k: pytest.fail("Predecode admission required"))
    with pytest.raises(ValueError): storage.bounded_json_read(path, **kwargs)


def test_observation_source_reader_uses_64_mib_expansion_limit(tmp_path):
    path = tmp_path/"manifest.json"
    data = {"provenance": "p"*(4*1024**2+20)}
    path.write_text(json.dumps(data))
    decoded, expanded = storage.bounded_json_read(path)
    assert decoded == data and 32*1024**2 < expanded < 64*1024**2
    with pytest.raises(ValueError): storage._json.bounded_json_read(path)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), {1: "bad"}, 2**1025, (1, 2), object()])
def test_serializer_rejects_nonplain_or_nonfinite(value):
    with pytest.raises(ValueError): storage.bounded_payload(value)


def test_serializer_rejects_cycles_and_depth():
    cycle = []; cycle.append(cycle)
    with pytest.raises(ValueError, match="cyclic"): storage.bounded_payload(cycle)
    deep = 1
    for _ in range(66): deep = [deep]
    with pytest.raises(ValueError, match="depth"): storage.bounded_payload(deep)


@pytest.mark.parametrize("budget", ["MAX_REPORT_BYTES", "MAX_REPORT_EXPANDED_BYTES", "MAX_WORKSPACE_BYTES",
    "MAX_SOURCE_MANIFEST_BYTES", "MAX_SOURCE_EXPANDED_BYTES", "MAX_SUMMARY_BYTES"])
def test_budget_rejection_precedes_publication(fixture, monkeypatch, budget):
    store, result, _, _ = fixture
    monkeypatch.setattr(storage, budget, 1)
    with pytest.raises(ValueError): store.create(result["request"])
    assert not store.directory.exists()


@pytest.mark.parametrize("field,value", [("estimated_peak_bytes", 1), ("estimated_peak_bytes", 512*1024**2+1),
    ("estimated_report_expanded_bytes", 1)])
def test_actual_publication_footprint_cannot_be_underadvertised(fixture, field, value):
    store, result, _, _ = fixture
    result["resource_estimate"][field] = value
    with pytest.raises(ValueError, match="workspace|expansion"): store.create(result["request"])
    assert not store.directory.exists()


def test_disk_reserve_before_staging(fixture, monkeypatch):
    store, result, _, _ = fixture
    monkeypatch.setattr(storage, "_disk", lambda *a: (_ for _ in ()).throw(ValueError("disk reserve")))
    with pytest.raises(ValueError, match="disk reserve"): store.create(result["request"])
    assert not store.directory.exists()


@pytest.mark.parametrize("identifier", ["../outside", "x", "{00000000-0000-0000-0000-000000000000}",
    "ABCDEFAB-1234-1234-1234-123456789012", 3, None])
def test_strict_uuid_path_boundary(tmp_path, identifier):
    with pytest.raises(ValueError): ObservationComparisonStore(tmp_path).read(identifier)


def test_missing_report(tmp_path):
    with pytest.raises(KeyError): ObservationComparisonStore(tmp_path).read(str(uuid4()))


@pytest.mark.parametrize("target", ["root", "directory", "leaf"])
def test_link_guards_before_decode(tmp_path, monkeypatch, target):
    outside = tmp_path/"outside"; outside.mkdir()
    root = tmp_path/"root"; root.mkdir()
    store, identifier = ObservationComparisonStore(root), str(uuid4())
    link = root if target == "root" else store.directory if target == "directory" else store._path(identifier)
    if target == "root": root.rmdir()
    elif target == "leaf": store.directory.mkdir()
    destination = outside if target != "leaf" else outside/"external.json"
    if target == "leaf": destination.write_text("{}")
    try: link.symlink_to(destination, target_is_directory=target != "leaf")
    except OSError: pytest.skip("Symbolic links unavailable on this host")
    monkeypatch.setattr(storage, "bounded_json_read", lambda *a, **k: pytest.fail("Guard precedes decode"))
    with pytest.raises(ValueError, match="links|junctions"): store.read(identifier)


def test_junction_guard_even_without_native_privilege(tmp_path, monkeypatch):
    store = ObservationComparisonStore(tmp_path)
    monkeypatch.setattr(Path, "is_junction", lambda path: path == store.directory, raising=False)
    with pytest.raises(ValueError, match="junction"): store.read(str(uuid4()))


def test_stable_pagination_decodes_only_selected_page_and_preserves_stage(fixture, monkeypatch):
    store, result, _, _ = fixture
    identifiers = [f"00000000-0000-0000-0000-00000000000{n}" for n in range(1, 6)]
    for identifier in identifiers:
        monkeypatch.setattr(storage, "uuid4", lambda identifier=identifier: identifier)
        store.create(result["request"])
    stage = store.directory/".unknown.tmp"; stage.write_bytes(b"preserved")
    read, seen = store.read, []
    def observed(identifier): seen.append(identifier); return read(identifier)
    monkeypatch.setattr(store, "read", observed)
    page = store.list(limit=2, offset=1)
    assert [item["id"] for item in page] == identifiers[-2:-4:-1] == seen
    assert all("source_snapshots" not in item and "initial_view" not in item for item in page)
    assert stage.read_bytes() == b"preserved" and store.list(limit=2, offset=100) == []


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": 101}, {"limit": True}, {"offset": -1},
    {"offset": 10000}, {"offset": False}, {"limit": 2.0}])
def test_pagination_validation(tmp_path, kwargs):
    with pytest.raises(ValueError, match="pagination"): ObservationComparisonStore(tmp_path).list(**kwargs)


def test_catalog_cap_precedes_report_decoding(fixture, monkeypatch):
    store, result, _, _ = fixture
    store.create(result["request"]); (store.directory/".old.tmp").write_bytes(b"untouched")
    monkeypatch.setattr(storage, "MAX_CATALOG_FILES", 1)
    monkeypatch.setattr(store, "read", lambda *a: pytest.fail("Cap precedes reads"))
    with pytest.raises(ValueError, match="entry limit"): store.list()


def test_catalog_rejects_unexpected_and_nonregular_files(fixture):
    store, result, _, _ = fixture
    report = store.create(result["request"])
    extra = store.directory/"notes.txt"; extra.write_text("preserve")
    with pytest.raises(ValueError, match="Unexpected"): store.list()
    extra.unlink(); (store.directory/f"{uuid4()}.json").mkdir()
    with pytest.raises(ValueError, match="regular"): store.list()
    assert store.read(report["id"]) == report


def test_all_reused_math_and_read_dependencies_are_fingerprinted():
    fingerprints = storage._fingerprints()
    for name in storage.IMPLEMENTATION_FILES:
        assert fingerprints[name] == hashlib.sha256(Path(storage.__file__).with_name(name).read_bytes()).hexdigest()
    assert {"observation_comparison_math.py", "causal_comparison_math.py", "observation_datasets.py", "observation_plan.py"} <= fingerprints.keys()
    runtime = storage._runtime()
    assert set(runtime["numerical_packages"]) == {"numpy", "zarr"}
    assert runtime["python"] and runtime["implementation"]


def test_real_saved_observation_report_is_source_independent_and_never_refilters(tmp_path, monkeypatch):
    from test_observation_comparisons import source, observation, request, hashes
    import virtual_microscopy.observation_math as math
    parent = source(tmp_path)
    identifier = observation(tmp_path, parent)
    before = {p: hashes(tmp_path/p) for p in (parent, identifier)}
    monkeypatch.setattr(math, "observe_row", lambda *a, **k: pytest.fail("Never refilter saved observations"))
    store = ObservationComparisonStore(tmp_path)
    report = store.create(request(identifier, identifier))
    assert len(report["source_snapshots"]) == 1
    assert report["initial_view"]["traces"]["difference"]["rf"] == [0.]*121
    assert store.view(report["id"], x_index=5)["cursor"]["x_index"] == 5
    assert before == {p: hashes(tmp_path/p) for p in before}
    (tmp_path/identifier).rename(tmp_path/f"removed-{identifier}")
    (tmp_path/parent).rename(tmp_path/f"removed-{parent}")
    assert store.view(report["id"]) == report["initial_view"]
    assert storage.observation_comparison_csv(store.read(report["id"]))
    with pytest.raises(KeyError): store.view(report["id"], x_index=1)
