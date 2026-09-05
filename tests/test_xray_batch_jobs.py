"""Mixed-instrument catalog compatibility and transactional X-ray batches."""
from copy import deepcopy
import hashlib
import json
import sqlite3
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest

from virtual_microscopy import batch_jobs
from virtual_microscopy.datasets import MIN_FREE_RESERVE_BYTES, atomic_json, canonical_json, json_sha256
from virtual_microscopy.sam_volume import estimate_sam
from virtual_microscopy.volume_jobs import VolumeJobManager, _claim_job, _run_job, _update_job
from virtual_microscopy.volume_schemas import SamVolumeRequest
from virtual_microscopy.xray_datasets import XrayDatasetStore
from virtual_microscopy.xray_schemas import XrayVolumeRequest
from virtual_microscopy.xray_volume import estimate_xray, iter_xray_views, prepare_xray


def specimen():
    return {"name": "X-ray batch coupon", "size_mm": [4, 3, 1], "objects": [
        {"id": "slab", "name": "Silicon slab", "shape": "box", "material": "silicon",
         "center_mm": [2, 1.5, .5], "size_mm": [4, 3, .6]},
        {"id": "copper", "name": "Off-center copper", "shape": "sphere", "material": "copper",
         "center_mm": [2.5, 1, .5], "size_mm": [.4, .4, .4]}]}


def xray_request(**changes):
    return XrayVolumeRequest.model_validate({"kind": "xray_projection_volume", "twin": specimen(),
        "acquisition": {"geometry_nx": 16, "geometry_ny": 16, "geometry_nz": 32,
                        "detector_cols": 16, "detector_rows": 16, "views": 4,
                        "noise": True, "seed": 515, "photons": 1000, "detector_fwhm_mm": 0, **changes}})


def sam_request():
    return SamVolumeRequest.model_validate({"twin": specimen(), "acquisition": {
        "scan_nx": 16, "scan_ny": 16, "depth_samples": 128, "record_duration_us": .6,
        "path_model": "continuous_columns_v1", "focus_mm": .2}})


def plan(kind="xray_projection_volume"):
    cases = []
    for index in range(2):
        config = xray_request(energy_kev=70+10*index) if kind == "xray_projection_volume" else sam_request()
        estimate = estimate_xray(config) if kind == "xray_projection_volume" else estimate_sam(config)
        cases.append({"label": f"Case {index + 1}", "overrides": {"energy_kev": 70+10*index} if kind == "xray_projection_volume" else {},
                      "request": config.model_dump(mode="json", exclude_none=True), "estimate": estimate,
                      "differences": {"settings": []}})
    return {"recipe": {"id": str(uuid4()), "name": "Immutable acquisition recipe", "schema_version": 1},
            "cases": cases, "proposal": {"field": "energy_kev" if kind == "xray_projection_volume" else "focus_mm",
                                         "values": [70, 80] if kind == "xray_projection_volume" else [.2, .2]}}


def dormant_manager(tmp_path, monkeypatch):
    manager = VolumeJobManager(tmp_path)
    monkeypatch.setattr(manager, "_ensure_worker", lambda: None)
    return manager


def finish_next(manager):
    identifier = _claim_job(manager.root)
    assert identifier is not None
    _run_job(manager.root, identifier, threading.Event())
    assert manager.get_job(identifier)["state"] == "completed", manager.get_job(identifier)
    return identifier


def hashes(path):
    return {p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob("*") if p.is_file()}


def frozen_rows(root):
    with sqlite3.connect(root / "catalog.sqlite3") as connection:
        return (connection.execute("SELECT plan_sha256,plan_json FROM batches ORDER BY batch_id").fetchall(),
                connection.execute("SELECT payload_sha256,payload_json FROM batch_cases ORDER BY case_id").fetchall())


def test_xray_plan_summary_staging_and_publication_are_kind_specific(tmp_path, monkeypatch):
    manager = dormant_manager(tmp_path, monkeypatch)
    proposal = plan()
    estimate = manager.estimate_batch(proposal)
    assert estimate["kind"] == "xray_projection_volume"
    for key in ("total_bytes", "projection_work_cells", "geometry_cells"):
        assert estimate[key] == sum(case["estimate"][key] for case in proposal["cases"])
    assert estimate["estimated_peak_bytes"] == max(c["estimate"]["estimated_peak_bytes"] for c in proposal["cases"])
    batch = manager.submit_batch(proposal, "xray-kind")
    assert batch["kind"] == "xray_projection_volume" and batch["plan"] == proposal
    assert batch["plan_sha256"] == json_sha256(proposal)
    for case in batch["cases"]:
        assert case["kind"] == "xray_projection_volume" and case["progress_unit"] == "views"
        assert case["total_units"] == case["total_rows"] == 4
        manifest = manager.get_store(case["id"]).manifest(case["id"])
        assert manifest["axis_order"] == ["view", "v", "u"]
        assert manifest["kind"] == "xray_projection_volume"
        assert manifest["request"] == case["request"] and manifest["solver"] == case["solver"]
        assert "xray_volume.py" in case["solver"]["source_sha256"]
        assert "sam_volume.py" not in case["solver"]["source_sha256"]
    assert manager.submit_batch(deepcopy(proposal), "xray-kind") == batch


@pytest.mark.parametrize("damage", ["mixed", "derived", "kind_type", "invalid", "stale", "aggregate", "count"])
def test_all_xray_cases_are_validated_before_any_staging(tmp_path, monkeypatch, damage):
    manager = dormant_manager(tmp_path, monkeypatch)
    proposal = plan()
    if damage == "mixed":
        proposal["cases"][1] = plan("sam_rf_volume")["cases"][1]
    elif damage == "derived":
        proposal["cases"][1]["request"]["kind"] = "xray_reconstruction"
    elif damage == "kind_type":
        proposal["cases"][1]["request"]["kind"] = []
    elif damage == "invalid":
        proposal["cases"][1]["request"]["acquisition"]["photons"] = 1
    elif damage == "stale":
        proposal["cases"][1]["estimate"]["total_bytes"] += 1
    elif damage == "count":
        proposal["cases"] *= 3
    else:
        monkeypatch.setattr(batch_jobs, "MAX_BATCH_BYTES", 1)
    with pytest.raises(ValueError):
        manager.submit_batch(proposal, "invalid-plan")
    assert manager.list_jobs() == manager.list_batches() == []
    assert not (tmp_path / ".batch-staging").exists()


@pytest.mark.parametrize(("changes", "message"), [
    ({"geometry_nx": 256, "geometry_ny": 256, "geometry_nz": 1024}, "64 million cells"),
    ({"views": 720, "detector_cols": 256, "detector_rows": 256}, "512 MiB saved-volume"),
    ({"views": 720, "detector_cols": 128, "detector_rows": 256}, "ray-segment work budget"),
])
def test_batch_dispatch_retains_real_xray_per_case_resource_limits(tmp_path, monkeypatch, changes, message):
    manager = dormant_manager(tmp_path, monkeypatch)
    proposal = plan()
    proposal["cases"][1]["request"]["acquisition"].update(changes)
    with pytest.raises(ValueError, match=message):
        manager.submit_batch(proposal, "over-budget-case")
    assert manager.list_jobs() == manager.list_batches() == []
    assert not (tmp_path / ".batch-staging").exists()


def test_xray_staging_failure_retains_evidence_without_publication(tmp_path, monkeypatch):
    manager = dormant_manager(tmp_path, monkeypatch)
    original, created = XrayDatasetStore.create, []

    def fail_second(self, *args):
        if created:
            raise OSError("Injected X-ray staging interruption")
        manifest = original(self, *args)
        created.append(args[0])
        return manifest

    monkeypatch.setattr(XrayDatasetStore, "create", fail_second)
    with pytest.raises(OSError, match="staging interruption"):
        manager.submit_batch(plan(), "stage-failure")
    assert manager.list_jobs() == manager.list_batches() == [] and _claim_job(tmp_path) is None
    stage, = (tmp_path / ".batch-staging").iterdir()
    assert (stage / created[0] / "manifest.json").is_file()
    assert json.loads((stage / "owner.json").read_text())["state"] == "abandoned"
    before = hashes(stage)
    manager._recover()
    assert hashes(stage) == before


def test_xray_publication_rolls_back_every_catalog_row(tmp_path, monkeypatch):
    manager = dormant_manager(tmp_path, monkeypatch)
    with sqlite3.connect(tmp_path / "catalog.sqlite3") as connection:
        connection.execute("""CREATE TRIGGER reject_last BEFORE INSERT ON batch_cases
            WHEN NEW.case_index=1 BEGIN SELECT RAISE(ABORT,'X-ray publication interrupted'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="publication interrupted"):
        manager.submit_batch(plan(), "rollback")
    assert manager.list_jobs() == manager.list_batches() == [] and _claim_job(tmp_path) is None
    stage, = (tmp_path / ".batch-staging").iterdir()
    marker = json.loads((stage / "owner.json").read_text())
    before = {job: hashes(tmp_path / job) for job in marker["job_ids"]}
    assert all(before.values())
    manager._recover()
    assert before == {job: hashes(tmp_path / job) for job in marker["job_ids"]}


def test_xray_postcommit_crash_replays_frozen_content_without_new_admission(tmp_path, monkeypatch):
    manager = dormant_manager(tmp_path, monkeypatch)
    proposal, publish = plan(), batch_jobs._publish_batch

    def fail_after_commit(*args):
        publish(*args)
        raise OSError("Response lost after X-ray commit")

    monkeypatch.setattr(batch_jobs, "_publish_batch", fail_after_commit)
    with pytest.raises(OSError, match="Response lost"):
        manager.submit_batch(proposal, "crashed-response")
    before = frozen_rows(tmp_path)
    manager._recover()
    monkeypatch.setattr("virtual_microscopy.xray_volume.estimate_xray", lambda *_: pytest.fail("Replay cannot estimate again"))
    batch = manager.submit_batch(proposal, "crashed-response")
    assert manager.get_batch_by_key("crashed-response")["id"] == batch["id"]
    assert len(manager.list_jobs()) == 2 and len(manager.list_batches()) == 1
    assert frozen_rows(tmp_path) == before
    changed = deepcopy(proposal)
    changed["cases"][1]["label"] = "Conflicting label"
    with pytest.raises(ValueError, match="Idempotency"):
        manager.submit_batch(changed, "crashed-response")


def test_seeded_xray_cancel_resume_repairs_one_view_and_preserves_all_other_bytes(tmp_path, monkeypatch):
    manager = dormant_manager(tmp_path, monkeypatch)
    batch = manager.submit_batch(plan(), "seeded-recovery")
    first = finish_next(manager)
    first_before = hashes(tmp_path / first)
    second = _claim_job(tmp_path)
    assert second == batch["cases"][1]["id"]
    original = XrayDatasetStore.write_view

    def commit_two_then_cancel(self, identifier, *args):
        manifest = original(self, identifier, *args)
        if manifest["completed_rows"] == 2:
            manager.cancel_batch(batch["id"])
        return manifest

    with monkeypatch.context() as patch:
        patch.setattr(XrayDatasetStore, "write_view", commit_two_then_cancel)
        _run_job(tmp_path, second, threading.Event())
    assert manager.get_batch(batch["id"])["state"] == "cancelled"
    assert manager.get_job(second)["completed_units"] == 2
    assert _claim_job(tmp_path) is None
    store = XrayDatasetStore(tmp_path)
    good_chunks = {name: (tmp_path / second / "data.zarr" / name / "c" / "1" / "0" / "0").read_bytes()
                   for name in store.signal_units}
    store.open_arrays(second, "r+")["counts"][0, 0, 0] = 99999
    manager._recover()
    manager.resume_batch(batch["id"])
    assert manager.get_job(second)["completed_units"] == 1
    assert finish_next(manager) == second
    assert manager.get_batch(batch["id"])["completed_cases"] == 2
    assert hashes(tmp_path / first) == first_before
    prepared = prepare_xray(batch["cases"][1]["request"])
    group = store.open_arrays(second)
    for v0, v1, expected in iter_xray_views(prepared):
        for name, values in expected.items():
            np.testing.assert_array_equal(group[name][v0:v1], values)
    for name in store.coordinate_names:
        np.testing.assert_array_equal(group[name][:], getattr(prepared, name))
    for name, contents in good_chunks.items():
        assert (tmp_path / second / "data.zarr" / name / "c" / "1" / "0" / "0").read_bytes() == contents
    store.verify_complete(second)


def test_xray_failure_blocks_later_cases_but_allows_ordinary_sam(tmp_path, monkeypatch):
    manager = dormant_manager(tmp_path, monkeypatch)
    batch = manager.submit_batch(plan(), "failure-order")
    first = _claim_job(tmp_path)
    for control in (manager.cancel, manager.resume):
        with pytest.raises(ValueError, match="use the batch"):
            control(first)
    _update_job(tmp_path, first, "failed", 0, "Injected detector failure")
    assert manager.get_batch(batch["id"])["state"] == "failed" and _claim_job(tmp_path) is None
    ordinary = manager.submit(sam_request())
    assert finish_next(manager) == ordinary["id"]
    manager.resume_batch(batch["id"])
    assert finish_next(manager) == first
    assert finish_next(manager) == batch["cases"][1]["id"]


@pytest.mark.parametrize("damage", ["solver", "kind", "pose"])
def test_xray_resume_validates_all_frozen_cases_before_requeue(tmp_path, monkeypatch, damage):
    manager = dormant_manager(tmp_path, monkeypatch)
    batch = manager.submit_batch(plan(), "identity")
    second = batch["cases"][1]["id"]
    store = XrayDatasetStore(tmp_path)
    if damage == "pose":
        store.initialize_arrays(second, prepare_xray(batch["cases"][1]["request"]))
        store.open_arrays(second, "r+")["detector_center_mm"][0, 0] += .01
    manager.cancel_batch(batch["id"])
    if damage != "pose":
        manifest = store.manifest(second)
        if damage == "solver":
            manifest["solver"]["source_sha256"]["xray_volume.py"] = "0"*64
        else:
            manifest["kind"] = "sam_rf_volume"
        atomic_json(tmp_path / second / "manifest.json", manifest)
    before = [manager.get_job(c["id"])["state"] for c in batch["cases"]]
    with pytest.raises(ValueError, match="identity|checksum|kind|Solver"):
        manager.resume_batch(batch["id"])
    assert [manager.get_job(c["id"])["state"] for c in batch["cases"]] == before
    assert _claim_job(tmp_path) is None


def test_catalog_kind_corruption_cannot_dispatch_frozen_xray_as_sam(tmp_path, monkeypatch):
    manager = dormant_manager(tmp_path, monkeypatch)
    batch = manager.submit_batch(plan(), "catalog-kind")
    with sqlite3.connect(tmp_path / "catalog.sqlite3") as connection:
        connection.execute("UPDATE jobs SET kind='sam_rf_volume' WHERE job_id=?", (batch["cases"][1]["id"],))
    with pytest.raises(ValueError, match="Frozen batch case"):
        manager.get_batch(batch["id"])


def test_disk_admission_accounts_for_both_instruments_and_worker_rechecks(tmp_path, monkeypatch):
    manager = dormant_manager(tmp_path, monkeypatch)
    ordinary = manager.submit(sam_request())
    sam_bytes = manager.store.manifest(ordinary["id"])["estimate"]["total_bytes"]
    proposal = plan()
    total = sum(c["estimate"]["total_bytes"] for c in proposal["cases"])
    free = MIN_FREE_RESERVE_BYTES + sam_bytes + total - 1
    monkeypatch.setattr("virtual_microscopy.datasets.shutil.disk_usage", lambda _: SimpleNamespace(free=free))
    with pytest.raises(OSError, match="Insufficient"):
        manager.submit_batch(proposal, "disk")
    assert len(manager.list_jobs()) == 1
    free += 1
    batch = manager.submit_batch(proposal, "disk")
    assert batch_jobs.check_reservations(tmp_path)["reserved_output_bytes"] == sam_bytes + total
    with pytest.raises(OSError, match="Insufficient"):
        manager.submit(xray_request())
    manager.cancel(ordinary["id"])
    identifier = _claim_job(tmp_path)
    free = MIN_FREE_RESERVE_BYTES
    monkeypatch.setattr("virtual_microscopy.xray_volume.prepare_xray", lambda *_: pytest.fail("No projection allocation before disk check"))
    _run_job(tmp_path, identifier, threading.Event())
    assert manager.get_batch(batch["id"])["state"] == "failed"
    assert "Insufficient" in manager.get_job(identifier)["error"]
    assert _claim_job(tmp_path) is None


def test_legacy_sam_plan_bytes_hashes_and_payloads_survive_xray_catalog_use(tmp_path, monkeypatch):
    manager = dormant_manager(tmp_path, monkeypatch)
    proposal = plan("sam_rf_volume")
    # The v0.9 normalizer omitted SAM kind. This is the historical input form,
    # including extra frozen UI metadata that dispatch must not normalize away.
    normalized = json.loads(canonical_json(proposal))
    for case in normalized["cases"]:
        case["request"] = SamVolumeRequest.model_validate(case["request"]).model_dump(mode="json", exclude_none=True)
        case.setdefault("overrides", {})
    assert batch_jobs.normalize_plan(proposal) == normalized
    assert all("kind" not in c["request"] for c in normalized["cases"])
    sam = manager.submit_batch(proposal, "legacy-sam")
    assert sam["plan_sha256"] == json_sha256(normalized)
    before = frozen_rows(tmp_path)
    manager.cancel_batch(sam["id"])
    manager._recover()
    assert frozen_rows(tmp_path) == before
    assert manager.get_batch(sam["id"])["kind"] == "sam_rf_volume"
    assert manager.submit_batch(proposal, "legacy-sam")["plan"] == normalized
    manager.resume_batch(sam["id"])
    assert frozen_rows(tmp_path) == before
    for _ in sam["cases"]:
        finish_next(manager)
    files = {case["id"]: hashes(tmp_path / case["id"]) for case in sam["cases"]}
    xray = manager.submit_batch(plan(), "new-xray")
    assert {batch["kind"] for batch in manager.list_batches()} == {"sam_rf_volume", "xray_projection_volume"}
    assert all(case["progress_unit"] == "rows" for case in manager.get_batch(sam["id"])["cases"])
    assert all(case["progress_unit"] == "views" for case in xray["cases"])
    assert {case["id"]: hashes(tmp_path / case["id"]) for case in sam["cases"]} == files


def test_real_spawn_xray_batch_reopens_without_reacquisition(tmp_path):
    manager = VolumeJobManager(tmp_path)
    manager.start()
    try:
        proposal = plan()
        batch = manager.submit_batch(proposal, "real-xray-worker")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            done = manager.get_batch(batch["id"])
            if done["state"] in {"completed", "failed", "interrupted", "cancelled"}:
                break
            time.sleep(.03)
        assert done["state"] == "completed", done
        before = {case["id"]: hashes(tmp_path / case["id"]) for case in done["cases"]}
        for case in done["cases"]:
            XrayDatasetStore(tmp_path).verify_complete(case["id"])
    finally:
        manager.close()
    reopened = VolumeJobManager(tmp_path)
    reopened.start()
    try:
        replayed = reopened.submit_batch(proposal, "real-xray-worker")
        assert replayed["id"] == batch["id"] and replayed["state"] == "completed"
        assert {case["id"]: hashes(tmp_path / case["id"]) for case in replayed["cases"]} == before
    finally:
        reopened.close()
