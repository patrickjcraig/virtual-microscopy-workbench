"""Short durable ZIP leases share disk admission without blocking the worker."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import shutil
import threading
from time import monotonic
from uuid import uuid4
import zipfile

import pytest

from virtual_microscopy import observation_api as api, observation_jobs as jobs, batch_jobs
from virtual_microscopy import datasets


@pytest.fixture
def export_case(tmp_path, monkeypatch):
    jobs.legacy._init_catalog(tmp_path)
    jobs.init_observations(tmp_path)
    identifier = str(uuid4())
    source = tmp_path/identifier
    source.mkdir()
    payload = source/"payload.bin"
    payload.write_bytes(b"x"*1024**2)
    # Numerical/file integrity belongs to the store's independent suite. This
    # fixture isolates lease ordering using one known ordinary source file.
    class ExportStore:
        def __init__(self, root): assert root == tmp_path
        def safe_export_files(self, selected):
            assert selected == identifier
            return [payload]
        def path(self, selected):
            assert selected == identifier
            return source
    monkeypatch.setattr(api, "ObservationStore", ExportStore)
    return tmp_path, identifier, payload


def leases(root):
    with jobs.legacy._connection(root) as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM observation_exports ORDER BY export_id")]


def add_pending(root, monkeypatch, *, kind="sam_rf_volume", size=1024**2):
    identifier, stamp = str(uuid4()), jobs.now_iso()
    table = "jobs" if kind == "sam_rf_volume" else "observation_jobs"
    with jobs.legacy._connection(root) as connection:
        connection.execute(f"INSERT INTO {table} VALUES (?,'queued','pending',?,?,0,1,NULL,?)",
                           (identifier, stamp, stamp, kind))
        connection.commit()
    manifest = {"total_rows": 1, "completed_rows": 0, "complete": False,
                "estimate": {"total_bytes": size, "estimated_temporary_bytes": 0}}
    if table == "jobs":
        monkeypatch.setattr(batch_jobs.DatasetStore, "manifest", lambda *a: manifest)
    else:
        from virtual_microscopy.observation_datasets import ObservationStore
        monkeypatch.setattr(ObservationStore, "manifest", lambda *a: manifest)
    return identifier


@pytest.mark.parametrize("kind", ["sam_rf_volume", "sam_coherent_observation_volume"])
def test_pending_output_reservation_rejects_export_before_zip_allocation(export_case, monkeypatch, kind):
    root, identifier, _ = export_case
    add_pending(root, monkeypatch, kind=kind)
    free = datasets.MIN_FREE_RESERVE_BYTES+int(1.5*1024**2)
    monkeypatch.setattr(datasets.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10**9, 10**9-free, free))
    assert batch_jobs.check_reservations(root)["pending_output_bytes"] == 1024**2
    with pytest.raises(OSError, match="Insufficient free disk"):
        api._archive(root, identifier)
    assert leases(root) == []
    assert list(root.glob("observation-export-*.zip")) == []


def test_export_lease_is_visible_while_copy_releases_sql_and_manager_locks(export_case, monkeypatch):
    root, identifier, payload = export_case
    job_id = add_pending(root, monkeypatch, kind="sam_coherent_observation_volume")
    entered, release = threading.Event(), threading.Event()
    mutex = threading.RLock()
    original = zipfile.ZipFile.write
    def blocked(self, *args, **kwargs):
        entered.set()
        assert release.wait(5), "Test did not release the owned copy"
        return original(self, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, "write", blocked)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(api._archive, root, identifier, admission_lock=mutex)
        try:
            assert entered.wait(5)
            rows = leases(root)
            assert len(rows) == 1 and rows[0]["reserved_bytes"] == payload.stat().st_size+2048
            value = batch_jobs.check_reservations(root)
            assert value["reserved_export_bytes"] == rows[0]["reserved_bytes"]
            assert value["pending_output_bytes"] == 1024**2+rows[0]["reserved_bytes"]
            assert mutex.acquire(timeout=.5), "ZIP copy must not hold the manager mutex"
            mutex.release()
            began = monotonic()
            jobs._update(root, job_id, "running", 0)
            with jobs.legacy._connection(root) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("UPDATE observation_jobs SET state='cancelling' WHERE job_id=?", (job_id,))
                connection.commit()
            assert monotonic()-began < 1.5
            assert jobs.read_job(root, job_id)["state"] == "cancelling"
        finally:
            release.set()
        output = future.result(timeout=5)
    assert leases(root) == []
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert archive.read("payload.bin") == payload.read_bytes()


@pytest.mark.parametrize("stage", ["allocation", "copy"])
def test_failed_export_releases_lease_and_removes_only_owned_zip(export_case, monkeypatch, stage):
    root, identifier, payload = export_case
    before = hashlib.sha256(payload.read_bytes()).hexdigest()
    unrelated = root/"observation-export-unrelated.zip"
    unrelated.write_bytes(b"retained unrelated data")
    def failed(*args, **kwargs):
        assert len(leases(root)) == 1
        raise OSError("injected export failure")
    if stage == "allocation":
        monkeypatch.setattr(api.tempfile, "NamedTemporaryFile", failed)
    else:
        monkeypatch.setattr(zipfile.ZipFile, "write", failed)
    with pytest.raises(OSError, match="injected export failure"):
        api._archive(root, identifier)
    assert leases(root) == []
    assert list(root.glob("observation-export-*.zip")) == [unrelated]
    assert unrelated.read_bytes() == b"retained unrelated data"
    assert hashlib.sha256(payload.read_bytes()).hexdigest() == before


def insert_lease(root, reserved=1000):
    identifier = str(uuid4())
    with jobs.legacy._connection(root) as connection:
        connection.execute("INSERT INTO observation_exports VALUES (?,?,?)", (identifier, reserved, jobs.now_iso()))
        connection.commit()
    return identifier


def test_startup_clears_stale_lease_and_preserves_orphan_bytes(tmp_path):
    manager = jobs.ObservationVolumeManager(tmp_path)
    jobs.init_observations(tmp_path)
    insert_lease(tmp_path)
    orphan = tmp_path/"observation-export-interrupted.zip"
    orphan.write_bytes(b"partial ZIP bytes retained after process death")
    before = orphan.read_bytes()
    manager._acquire_owner()
    try:
        manager._recover()  # Exact startup state: not started, sole ownership.
        assert leases(tmp_path) == []
        assert orphan.read_bytes() == before
    finally:
        manager._release_owner()


@pytest.mark.parametrize("started,owned", [(True, True), (False, False)])
def test_worker_recovery_and_unowned_recovery_cannot_drop_active_export(tmp_path, started, owned):
    manager = jobs.ObservationVolumeManager(tmp_path)
    jobs.init_observations(tmp_path)
    insert_lease(tmp_path)
    expected = leases(tmp_path)
    manager._started = started
    if owned:
        manager._acquire_owner()
    try:
        manager._recover()
        assert leases(tmp_path) == expected
    finally:
        manager._started = False
        manager._release_owner()


def test_successful_zip_is_physically_present_before_lease_removal(export_case, monkeypatch):
    root, identifier, _ = export_case
    original = api._connection
    observed = []
    from contextlib import contextmanager
    @contextmanager
    def checked(path):
        # Admission enters without a lease. Release enters with it, only after
        # the archive has a readable central directory and valid payload CRC.
        if leases(path):
            outputs = list(path.glob("observation-export-*.zip"))
            assert len(outputs) == 1
            with zipfile.ZipFile(outputs[0]) as archive:
                assert archive.testzip() is None
            observed.append(True)
        with original(path) as connection:
            yield connection
    monkeypatch.setattr(api, "_connection", checked)
    output = api._archive(root, identifier)
    assert observed == [True] and output.is_file() and leases(root) == []


@pytest.mark.parametrize("kind", ["sam_rf_volume", "sam_coherent_observation_volume"])
@pytest.mark.parametrize("state", ["running", "cancelling"])
def test_admitted_worker_survives_physical_zip_growth_without_double_charge(export_case, monkeypatch, kind, state):
    root, _, _ = export_case
    identifier = add_pending(root, monkeypatch, kind=kind)
    table = "jobs" if kind == "sam_rf_volume" else "observation_jobs"
    with jobs.legacy._connection(root) as connection:
        connection.execute(f"UPDATE {table} SET state=? WHERE job_id=?", (state, identifier)); connection.commit()
    amount = 1024**2
    insert_lease(root, amount)
    # Export was admitted at reserve + job + ZIP. Half its physical bytes now
    # exist while the full durable lease remains until the copy closes.
    free = datasets.MIN_FREE_RESERVE_BYTES+amount+amount//2
    monkeypatch.setattr(datasets.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10**9, 10**9-free, free))
    m = {"total_rows": 1, "completed_rows": 0, "complete": False,
         "estimate": {"total_bytes": amount, "estimated_temporary_bytes": 0}}
    value = batch_jobs.check_reservations(root, replacing={identifier: m})
    assert value["already_admitted_worker_recheck"] is True
    assert value["reserved_export_bytes"] == amount and value["counted_export_bytes"] == 0
    assert value["pending_output_bytes"] == amount
    # An actual new admission must still reserve the whole outstanding lease,
    # even if it supplies an otherwise-active replacement in the same call.
    with pytest.raises(OSError):
        batch_jobs.check_reservations(root, replacing={identifier: m}, extra_estimates=[{"total_bytes": 1}])


@pytest.mark.parametrize("kind", ["sam_rf_volume", "sam_coherent_observation_volume"])
@pytest.mark.parametrize("state", ["queued", "cancelled", "failed", "interrupted"])
def test_new_admission_or_inactive_resume_cannot_spend_export_lease(export_case, monkeypatch, kind, state):
    root, _, _ = export_case
    identifier = add_pending(root, monkeypatch, kind=kind)
    table = "jobs" if kind == "sam_rf_volume" else "observation_jobs"
    with jobs.legacy._connection(root) as connection:
        connection.execute(f"UPDATE {table} SET state=? WHERE job_id=?", (state, identifier)); connection.commit()
    amount = 1024**2
    insert_lease(root, amount)
    free = datasets.MIN_FREE_RESERVE_BYTES+amount+amount//2
    monkeypatch.setattr(datasets.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10**9, 10**9-free, free))
    m = {"total_rows": 1, "completed_rows": 0, "complete": False,
         "estimate": {"total_bytes": amount, "estimated_temporary_bytes": 0}}
    with pytest.raises(OSError):
        batch_jobs.check_reservations(root, replacing={identifier: m})
    with pytest.raises(OSError):
        batch_jobs.check_reservations(root, extra_estimates=[{"total_bytes": amount}])
