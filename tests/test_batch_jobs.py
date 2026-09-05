"""Atomic batch admission, ordered worker execution and immutable case recovery."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest

from virtual_microscopy import batch_jobs
from virtual_microscopy.datasets import (DatasetStore, MIN_FREE_RESERVE_BYTES, atomic_json)
from virtual_microscopy.sam_volume import estimate_sam
from virtual_microscopy.volume_jobs import (VolumeJobManager, _claim_job, _read_job,
                                            _run_job, _update_job)
from virtual_microscopy.volume_schemas import SamVolumeRequest


def request(focus=.2):
    return SamVolumeRequest.model_validate({"twin": {
        "name": "Batch analytical coupon", "size_mm": [2, 2, 1],
        "objects": [{"id": "slab", "name": "Slab", "shape": "box", "material": "silicon",
                     "center_mm": [1, 1, .5], "size_mm": [2, 2, .6]}]},
        "acquisition": {"path_model": "continuous_columns_v1", "scan_nx": 16, "scan_ny": 16,
                        "frequency_mhz": 50, "sample_rate_mhz": 400, "record_duration_us": .6,
                        "focus_mm": focus, "depth_samples": 128}})


def plan():
    cases = []
    for focus in (.2, .3):
        config = request(focus)
        cases.append({"label": f"Focus {focus}", "overrides": {"focus_mm": focus},
                      "request": config.model_dump(mode="json", exclude_none=True),
                      "estimate": estimate_sam(config)})
    return {"recipe": {"id": str(uuid4()), "name": "Frozen focus experiment", "schema_version": 1},
            "cases": cases}


def manager_without_worker(tmp_path, monkeypatch):
    manager = VolumeJobManager(tmp_path)
    monkeypatch.setattr(manager, "_ensure_worker", lambda: None)
    return manager


def hashes(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob("*") if p.is_file()}


def finish_next(manager):
    identifier = _claim_job(manager.root)
    assert identifier is not None
    _run_job(manager.root, identifier, threading.Event())
    assert _read_job(manager.root, identifier)["state"] == "completed"
    return identifier


def wait_batch(manager, identifier, timeout=25):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        batch = manager.get_batch(identifier)
        if batch["state"] in {"completed", "failed", "interrupted", "cancelled"}:
            return batch
        time.sleep(.03)
    pytest.fail(f"Batch did not finish: {manager.get_batch(identifier)}")


def test_admission_is_frozen_ordered_and_idempotent(tmp_path, monkeypatch):
    manager = manager_without_worker(tmp_path, monkeypatch)
    proposal = plan()
    estimate = manager.estimate_batch(proposal)
    assert estimate["case_count"] == 2
    assert estimate["total_bytes"] == sum(c["estimate"]["total_bytes"] for c in proposal["cases"])
    assert estimate["estimated_peak_bytes"] == max(c["estimate"]["estimated_peak_bytes"] for c in proposal["cases"])
    batch = manager.submit_batch(proposal, "same-request")
    again = manager.submit_batch(deepcopy(proposal), "same-request")
    assert again == batch
    assert len(manager.list_jobs()) == 2 and len(manager.list_batches()) == 1
    assert batch["cases"][0]["case_index"] == 0
    assert batch["cases"][0]["batch_id"] == batch["id"]
    proposal["recipe"]["name"] = "Mutable caller changed its record"
    proposal["cases"][0]["request"]["twin"]["name"] = "Mutated"
    assert manager.get_batch(batch["id"])["recipe"]["name"] == "Frozen focus experiment"
    with pytest.raises(ValueError, match="Idempotency"):
        manager.submit_batch(proposal, "same-request")
    for case in batch["cases"]:
        manifest = manager.store.manifest(case["job_id"])
        assert case["input_sha256"] == manifest["input_sha256"]
        assert case["solver"] == manifest["solver"]
        assert case["materials_sha256"] == manifest["materials_sha256"]
    # UUID sorting cannot reorder a batch's explicitly reviewed case order.
    assert finish_next(manager) == batch["cases"][0]["job_id"]
    assert finish_next(manager) == batch["cases"][1]["job_id"]
    done = manager.get_batch(batch["id"])
    assert done["state"] == "completed" and done["completed_cases"] == 2
    assert done["input_sha256"] == batch["input_sha256"]


@pytest.mark.parametrize("damage", ["invalid", "estimate", "count", "bytes"])
def test_every_case_preflights_before_any_stage_or_job(tmp_path, monkeypatch, damage):
    manager = manager_without_worker(tmp_path, monkeypatch)
    proposal = plan()
    if damage == "invalid":
        proposal["cases"][1]["request"]["acquisition"]["sample_rate_mhz"] = 1
    elif damage == "estimate":
        proposal["cases"][1]["estimate"]["total_bytes"] += 1
    elif damage == "count":
        proposal["cases"] = proposal["cases"][:1]
    else:
        monkeypatch.setattr(batch_jobs, "MAX_BATCH_BYTES", 1)
    with pytest.raises(ValueError):
        manager.submit_batch(proposal, "invalid")
    assert manager.list_jobs() == [] and manager.list_batches() == []
    assert not (tmp_path / ".batch-staging").exists()


def test_staging_failure_leaves_no_published_jobs_and_preserves_owned_record(tmp_path, monkeypatch):
    manager = manager_without_worker(tmp_path, monkeypatch)
    original = DatasetStore.create
    calls = []

    def fail_second(self, *args):
        calls.append(args[0])
        if len(calls) == 2:
            raise OSError("Injected second manifest failure")
        return original(self, *args)

    monkeypatch.setattr(DatasetStore, "create", fail_second)
    with pytest.raises(OSError, match="second manifest"):
        manager.submit_batch(plan(), "staging-failure")
    assert manager.list_jobs() == [] and _claim_job(tmp_path) is None
    stage, = (tmp_path / ".batch-staging").iterdir()
    assert json.loads((stage / "owner.json").read_text())["state"] == "abandoned"
    assert (stage / calls[0] / "manifest.json").is_file()
    before = hashes(stage)
    manager._recover()
    assert hashes(stage) == before


def test_publication_failure_rolls_back_all_rows_and_recovery_preserves_artifacts(tmp_path, monkeypatch):
    manager = manager_without_worker(tmp_path, monkeypatch)
    with sqlite3.connect(tmp_path / "catalog.sqlite3") as connection:
        connection.execute("""CREATE TRIGGER fail_second_case BEFORE INSERT ON batch_cases
            WHEN NEW.case_index=1 BEGIN SELECT RAISE(ABORT,'injected atomic publication failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="publication failure"):
        manager.submit_batch(plan(), "sql-failure")
    assert manager.list_jobs() == [] and manager.list_batches() == []
    assert _claim_job(tmp_path) is None
    stage, = (tmp_path / ".batch-staging").iterdir()
    marker = json.loads((stage / "owner.json").read_text())
    assert marker["state"] == "abandoned"
    manifests = [(tmp_path / job / "manifest.json") for job in marker["job_ids"]]
    assert all(p.is_file() for p in manifests)
    before = [p.read_bytes() for p in manifests]
    manager._recover()
    assert before == [p.read_bytes() for p in manifests]


def test_lost_postcommit_response_replays_same_batch_without_duplicate_cases(tmp_path, monkeypatch):
    manager = manager_without_worker(tmp_path, monkeypatch)
    proposal, original = plan(), batch_jobs._publish_batch

    def commit_then_fail(*args):
        original(*args)
        raise OSError("Lost response after commit")

    monkeypatch.setattr(batch_jobs, "_publish_batch", commit_then_fail)
    with pytest.raises(OSError, match="Lost response"):
        manager.submit_batch(proposal, "lost-response")
    assert len(manager.list_jobs()) == 2
    manager._recover()
    retried = manager.submit_batch(proposal, "lost-response")
    assert retried["id"] == manager.list_batches()[0]["id"]
    assert len(manager.list_batches()) == 1 and len(manager.list_jobs()) == 2


def test_batch_cancellation_blocks_pending_cases_and_individual_bypass(tmp_path, monkeypatch):
    manager = manager_without_worker(tmp_path, monkeypatch)
    batch = manager.submit_batch(plan(), "cancel")
    first, second = batch["cases"]
    for action in (manager.cancel, manager.resume):
        with pytest.raises(ValueError, match="use the batch"):
            action(first["job_id"])
    assert _claim_job(tmp_path) == first["job_id"]
    cancelled = manager.cancel_batch(batch["id"])
    assert cancelled["state"] == "cancelling"
    assert [case["state"] for case in cancelled["cases"]] == ["cancelling", "cancelled"]
    _run_job(tmp_path, first["job_id"], threading.Event())
    assert manager.get_batch(batch["id"])["state"] == "cancelled"
    assert _claim_job(tmp_path) is None
    resumed = manager.resume_batch(batch["id"])
    assert resumed["state"] == "queued"
    assert finish_next(manager) == first["job_id"]
    assert finish_next(manager) == second["job_id"]


def test_failure_holds_remaining_batch_but_does_not_block_unrelated_job(tmp_path, monkeypatch):
    manager = manager_without_worker(tmp_path, monkeypatch)
    batch = manager.submit_batch(plan(), "failed-case")
    first, second = batch["cases"]
    assert _claim_job(tmp_path) == first["job_id"]
    _update_job(tmp_path, first["job_id"], "failed", 0, "Injected failure")
    assert manager.get_batch(batch["id"])["state"] == "failed"
    assert _claim_job(tmp_path) is None
    ordinary = manager.submit(request())
    assert _claim_job(tmp_path) == ordinary["id"]
    _run_job(tmp_path, ordinary["id"], threading.Event())
    manager.resume_batch(batch["id"])
    assert finish_next(manager) == first["job_id"]
    assert finish_next(manager) == second["job_id"]


def test_completed_cases_remain_byte_immutable_through_cancel_restart_resume(tmp_path, monkeypatch):
    manager = manager_without_worker(tmp_path, monkeypatch)
    proposal = plan()
    batch = manager.submit_batch(proposal, "preserve-completed")
    first = finish_next(manager)
    before = hashes(tmp_path / first)
    manager.cancel_batch(batch["id"])
    manager._recover()
    manager.resume_batch(batch["id"])
    assert finish_next(manager) == batch["cases"][1]["job_id"]
    assert manager.get_batch(batch["id"])["state"] == "completed"
    assert hashes(tmp_path / first) == before
    assert manager.submit_batch(proposal, "preserve-completed")["state"] == "completed"


def test_partial_corrupt_chunk_repaired_and_good_chunk_preserved_on_batch_resume(tmp_path, monkeypatch):
    manager = manager_without_worker(tmp_path, monkeypatch)
    batch = manager.submit_batch(plan(), "repair")
    identifier = _claim_job(tmp_path)
    original = DatasetStore.write_tile

    def first_then_stop(self, current, *tile):
        manifest = original(self, current, *tile)
        _update_job(tmp_path, current, "cancelling", manifest["completed_rows"])
        return manifest

    with monkeypatch.context() as patch:
        patch.setattr(DatasetStore, "write_tile", first_then_stop)
        _run_job(tmp_path, identifier, threading.Event())
    saved = manager.store.manifest(identifier)
    assert saved["completed_rows"] == saved["tile_rows"]
    expected = manager.store.open_arrays(identifier)["rf"][:saved["tile_rows"]].copy()
    manager.store.open_arrays(identifier, "r+")["rf"][0, 0, 0] = 999
    manager.resume_batch(batch["id"])
    assert manager.get_job(identifier)["completed_rows"] == 0
    finish_next(manager)
    np.testing.assert_array_equal(manager.store.open_arrays(identifier)["rf"][:saved["tile_rows"]], expected)


def test_resume_validates_all_identities_before_any_case_is_requeued(tmp_path, monkeypatch):
    manager = manager_without_worker(tmp_path, monkeypatch)
    batch = manager.submit_batch(plan(), "identity")
    manager.cancel_batch(batch["id"])
    second = batch["cases"][1]["job_id"]
    manifest = manager.store.manifest(second)
    manifest["solver"]["source_sha256"]["sam_volume.py"] = "0"*64
    atomic_json(tmp_path / second / "manifest.json", manifest)
    before = [manager.get_job(c["job_id"])["state"] for c in batch["cases"]]
    with pytest.raises(ValueError, match="Solver|identity"):
        manager.resume_batch(batch["id"])
    assert [manager.get_job(c["job_id"])["state"] for c in batch["cases"]] == before
    assert _claim_job(tmp_path) is None


def test_disk_reservations_cover_existing_batch_and_ordinary_jobs(tmp_path, monkeypatch):
    manager = manager_without_worker(tmp_path, monkeypatch)
    proposal = plan()
    ordinary = manager.submit(request())
    existing = manager.store.manifest(ordinary["id"])["estimate"]["total_bytes"]
    total = sum(c["estimate"]["total_bytes"] for c in proposal["cases"])
    free = MIN_FREE_RESERVE_BYTES + existing + total - 1
    monkeypatch.setattr("virtual_microscopy.datasets.shutil.disk_usage", lambda _: SimpleNamespace(free=free))
    with pytest.raises(OSError, match="Insufficient"):
        manager.submit_batch(proposal, "disk-reject")
    assert len(manager.list_jobs()) == 1
    free += 1
    batch = manager.submit_batch(proposal, "disk-accept")
    estimate = batch_jobs.check_reservations(tmp_path)
    assert estimate["reserved_output_bytes"] == existing+total
    with pytest.raises(OSError, match="Insufficient"):
        manager.submit(request())
    assert len(manager.list_jobs()) == 3
    manager.cancel_batch(batch["id"])
    assert batch_jobs.check_reservations(tmp_path)["reserved_output_bytes"] == existing


def test_worker_rechecks_reservations_before_solver_allocation(tmp_path, monkeypatch):
    manager = manager_without_worker(tmp_path, monkeypatch)
    batch = manager.submit_batch(plan(), "worker-disk")
    identifier = _claim_job(tmp_path)
    monkeypatch.setattr("virtual_microscopy.datasets.shutil.disk_usage", lambda _: SimpleNamespace(free=MIN_FREE_RESERVE_BYTES))
    monkeypatch.setattr("virtual_microscopy.sam_volume.prepare_sam", lambda *_: pytest.fail("Allocation before disk admission"))
    _run_job(tmp_path, identifier, threading.Event())
    assert manager.get_batch(batch["id"])["state"] == "failed"
    assert "Insufficient" in manager.get_job(identifier)["error"]
    assert _claim_job(tmp_path) is None


def test_recovery_reconciles_completed_manifest_before_next_case_and_interruption(tmp_path, monkeypatch):
    manager = manager_without_worker(tmp_path, monkeypatch)
    batch = manager.submit_batch(plan(), "completion-gap")
    first = finish_next(manager)
    before = hashes(tmp_path / first)
    # Simulate process death after manifest complete but before catalog update.
    _update_job(tmp_path, first, "running", 8)
    manager._recover()
    assert manager.get_job(first)["state"] == "completed"
    assert hashes(tmp_path / first) == before
    second = _claim_job(tmp_path)
    manager._recover()
    assert manager.get_job(second)["state"] == "interrupted"
    assert manager.get_batch(batch["id"])["state"] == "interrupted"
    assert _claim_job(tmp_path) is None


def test_real_spawn_worker_completes_batch_and_reopen_preserves_identity(tmp_path):
    manager = VolumeJobManager(tmp_path)
    manager.start()
    try:
        proposal = plan()
        batch = manager.submit_batch(proposal, "spawn-worker")
        completed = wait_batch(manager, batch["id"])
        assert completed["state"] == "completed", completed
        snapshots = {case["job_id"]: hashes(tmp_path/case["job_id"]) for case in completed["cases"]}
    finally:
        manager.close()
    reopened = VolumeJobManager(tmp_path)
    reopened.start()
    try:
        again = reopened.submit_batch(proposal, "spawn-worker")
        assert again["state"] == "completed" and again["id"] == batch["id"]
        for case in again["cases"]:
            assert hashes(tmp_path/case["job_id"]) == snapshots[case["job_id"]]
    finally:
        reopened.close()


def test_old_catalog_migration_retains_jobs_and_adds_batch_tables(tmp_path):
    with sqlite3.connect(tmp_path / "catalog.sqlite3") as connection:
        connection.execute("""CREATE TABLE jobs(job_id TEXT PRIMARY KEY,state TEXT,name TEXT,
            created_at TEXT,updated_at TEXT,completed_rows INTEGER,total_rows INTEGER,error TEXT)""")
        connection.execute("INSERT INTO jobs VALUES (?,'cancelled','Legacy','then','then',0,16,NULL)", (str(uuid4()),))
    manager = VolumeJobManager(tmp_path)
    assert len(manager.list_jobs()) == 1
    assert manager.list_jobs()[0]["kind"] == "sam_rf_volume"
    assert manager.list_batches() == []


def test_staging_boundary_rejects_symlink_without_touching_external_files(tmp_path, monkeypatch):
    manager = manager_without_worker(tmp_path / "root", monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("unrelated")
    try:
        (manager.root / ".batch-staging").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this Windows account.")
    with pytest.raises(ValueError, match="links|junctions"):
        manager.submit_batch(plan(), "unsafe-stage")
    assert marker.read_text() == "unrelated"
    assert manager.list_jobs() == []


def test_case_payload_checksum_and_positional_registry_are_frozen(tmp_path, monkeypatch):
    manager = manager_without_worker(tmp_path, monkeypatch)
    batch = manager.submit_batch(plan(), "case-registry")
    identifier = batch["cases"][0]["case_id"]
    with sqlite3.connect(tmp_path / "catalog.sqlite3") as connection:
        raw, = connection.execute("SELECT payload_json FROM batch_cases WHERE case_id=?", (identifier,)).fetchone()
        payload = json.loads(raw)
        payload["solver"]["model_version"] = "wrong-solver"
        connection.execute("UPDATE batch_cases SET payload_json=? WHERE case_id=?", (json.dumps(payload), identifier))
    with pytest.raises(ValueError, match="Frozen batch case"):
        manager.get_batch(batch["id"])


def test_reservations_sum_remaining_output_but_only_maximum_temporary_workspace(tmp_path, monkeypatch):
    manager = manager_without_worker(tmp_path, monkeypatch)
    proposal = plan()
    one = proposal["cases"][0]["estimate"]
    existing = manager.submit(request())
    manifest = manager.store.manifest(existing["id"])
    manifest["completed_rows"] = 8
    # This override models repaired progress/resume without changing stored
    # solver estimates; workspace aliases must never be double-counted.
    manifest["estimate"] = dict(manifest["estimate"], estimated_temporary_bytes=101, workspace_disk_bytes=101)
    extras = [dict(one, estimated_temporary_bytes=300, workspace_disk_bytes=300),
              dict(one, estimated_temporary_bytes=200)]
    result = batch_jobs.check_reservations(tmp_path, extra_estimates=extras, replacing={existing["id"]: manifest})
    expected = (one["total_bytes"]+1)//2
    assert result["reserved_output_bytes"] == expected
    assert result["pending_output_bytes"] == expected+2*one["total_bytes"]
    assert result["maximum_temporary_bytes"] == 300
    assert result["required_free_bytes"] == result["pending_output_bytes"]+300+MIN_FREE_RESERVE_BYTES


def test_recovery_marks_prepublication_crash_abandoned_and_ignores_unowned_files(tmp_path, monkeypatch):
    manager = manager_without_worker(tmp_path, monkeypatch)
    base = tmp_path / ".batch-staging"
    base.mkdir()
    identifier = str(uuid4())
    stage = base / identifier
    stage.mkdir()
    owner = {"schema_version": 1, "purpose": batch_jobs.STAGING_PURPOSE,
             "batch_id": identifier, "state": "staging", "job_ids": []}
    atomic_json(stage / "owner.json", owner)
    unknown = base / str(uuid4())
    unknown.mkdir()
    untouched = unknown / "owner.json"
    untouched.write_text("This is not an owned JSON journal.")
    manager._recover()
    assert json.loads((stage / "owner.json").read_text())["state"] == "abandoned"
    assert untouched.read_text() == "This is not an owned JSON journal."
    assert manager.list_jobs() == []


def test_completed_case_is_not_resumed_under_current_solver_after_partial_batch_stop(tmp_path, monkeypatch):
    manager = manager_without_worker(tmp_path, monkeypatch)
    batch = manager.submit_batch(plan(), "old-completed")
    first = finish_next(manager)
    before = hashes(tmp_path / first)
    manager.cancel_batch(batch["id"])
    original = manager.store.validate_identity
    calls = []

    def only_partial(identifier):
        assert identifier != first, "Historical completed case must not validate against current solver."
        calls.append(identifier)
        return original(identifier)

    monkeypatch.setattr(manager.store, "validate_identity", only_partial)
    manager.resume_batch(batch["id"])
    assert calls == [batch["cases"][1]["job_id"]]
    assert hashes(tmp_path / first) == before


def test_batch_key_lookup_preserves_full_proposal_without_current_admission(tmp_path, monkeypatch):
    manager = manager_without_worker(tmp_path, monkeypatch)
    proposal = plan()
    proposal["proposal"] = {"field": "focus_mm", "values": [.2, .3]}
    proposal["cases"][1]["differences"] = {"settings": [{"field": "focus_mm", "a": .2, "b": .3}]}
    batch = manager.submit_batch(proposal, "raw-replay")
    monkeypatch.setattr("virtual_microscopy.sam_volume.estimate_sam", lambda *_: pytest.fail("Replay cannot re-estimate acquisition"))
    by_key = manager.get_batch_by_key("raw-replay")
    assert by_key["id"] == batch["id"]
    assert by_key["plan"] == proposal
    assert by_key["cases"][1]["differences"] == proposal["cases"][1]["differences"]
    assert manager.submit_batch(proposal, "raw-replay")["id"] == batch["id"]
    assert manager.get_batch_by_key("unknown") is None
