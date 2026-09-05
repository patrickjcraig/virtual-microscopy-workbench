"""One owned spawn worker for historical acquisitions and derived observations.

Old jobs retain their original table, dispatcher and resume fingerprints. New
observation jobs use a separate table and alternate with eligible old jobs.
"""
from __future__ import annotations

import multiprocessing
from pathlib import Path
from uuid import uuid4

import numpy as np

from .datasets import checked_id, now_iso
from . import volume_jobs as legacy

KIND = "sam_coherent_observation_volume"
ORCHESTRATION_VERSION = "shared-observation-worker-0.15.0"
ACTIVE = ("queued", "running", "cancelling")


def init_observations(root):
    with legacy._connection(root) as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS observation_jobs (
            job_id TEXT PRIMARY KEY, state TEXT NOT NULL, name TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            completed_rows INTEGER NOT NULL, total_rows INTEGER NOT NULL,
            error TEXT, kind TEXT NOT NULL)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS observation_exports (
            export_id TEXT PRIMARY KEY, reserved_bytes INTEGER NOT NULL CHECK(reserved_bytes>=0),
            created_at TEXT NOT NULL)""")
        connection.commit()


def _record(row):
    value = legacy._job_dict(row)
    value["progress_unit"] = "output rows"
    return value


def read_job(root, identifier):
    checked_id(identifier)
    with legacy._connection(root) as connection:
        row = connection.execute("SELECT * FROM observation_jobs WHERE job_id=?", (identifier,)).fetchone()
    if row is None:
        raise KeyError(identifier)
    return _record(row)


def _update(root, identifier, state, completed_rows, error=None):
    with legacy._connection(root) as connection:
        connection.execute("UPDATE observation_jobs SET state=?,completed_rows=?,error=?,updated_at=? WHERE job_id=?",
                           (state, completed_rows, error, now_iso(), identifier))
        connection.commit()


def _claim(root):
    with legacy._connection(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT job_id FROM observation_jobs WHERE state='queued' ORDER BY created_at,job_id LIMIT 1").fetchone()
        if row is None:
            connection.commit()
            return None
        connection.execute("UPDATE observation_jobs SET state='running',updated_at=? WHERE job_id=?", (now_iso(), row["job_id"]))
        connection.commit()
        return row["job_id"]


def _checkpoint(root, identifier, stop_event):
    parent = multiprocessing.parent_process()
    if stop_event.is_set() or (parent is not None and not parent.is_alive()):
        return "interrupted"
    return "cancelled" if read_job(root, identifier)["state"] == "cancelling" else None


def checked_source_row(store, identifier, manifest, group, y):
    from .causal_datasets import typed_sha256
    products = store._read_row(identifier, manifest, group, y)
    record = manifest["completed_chunks"].get(str(y), {})
    if any(typed_sha256(value) != record.get(f"{key}_sha256") for key, value in products.items()):
        raise ValueError("Observation source row changed after verification.")
    store._row_matches(manifest, y, products, manifest["class_certificates"])
    return products


def _run(root, identifier, stop_event):
    from .batch_jobs import check_reservations
    from .causal_datasets import CausalSamDatasetStore
    from .observation_datasets import ObservationStore
    from .observation_plan import source_context
    from .observation_math import observe_row, ObservationCancelled

    store = ObservationStore(root)
    try:
        manifest = store.manifest(identifier)
        if manifest["complete"]:
            store.verify_complete(identifier)
            _update(root, identifier, "completed", manifest["total_rows"])
            return
        manifest = store.validate_identity(identifier)
        halted = lambda: _checkpoint(root, identifier, stop_event)
        if halted():
            state = halted()
            store.set_state(identifier, state)
            _update(root, identifier, state, manifest["completed_rows"])
            return
        check_reservations(root, replacing={identifier: manifest})
        source, source_path = source_context(manifest["estimate"], root, verify=True)
        source_store = CausalSamDatasetStore(root)
        group = source_store._open_checked(source_path.name, source)
        store.set_state(identifier, "running")
        store.initialize_arrays(identifier)
        manifest = store.verify_chunks(identifier)
        window = {}
        for y in range(manifest["total_rows"]):
            if halted():
                raise ObservationCancelled()
            if str(y) in manifest["completed_chunks"]:
                continue
            for row in list(window):
                if row not in range(y, y+3):
                    del window[row]
            for row in range(y, y+3):
                if row not in window:
                    window[row] = checked_source_row(source_store, source_path.name, source, group, row)
            source_rows = {key: np.concatenate([window[row][source_key] for row in range(y,y+3)],axis=0)
                           for key,source_key in (("rf","rf"),("imaginary","imaginary"),("bounds","error_bound"))}
            result = observe_row(source_rows["rf"], source_rows["imaginary"], source_rows["bounds"],
                                 absolute_tolerance=manifest["request"]["absolute_tolerance"], cancelled=halted)
            if halted():
                raise ObservationCancelled()
            check_reservations(root, replacing={identifier: manifest})
            manifest = store.write_row(identifier, y, result, source_rows, cancelled=halted)
            # A progress update must not erase a concurrently requested cancellation.
            with legacy._connection(root) as connection:
                connection.execute("UPDATE observation_jobs SET completed_rows=?,updated_at=? WHERE job_id=?",
                                   (manifest["completed_rows"],now_iso(),identifier))
                connection.commit()
        if halted():
            raise ObservationCancelled()
        source_context(manifest["estimate"], root, verify=True)
        if halted():
            raise ObservationCancelled()
        # Linearize final publication with cancellation acceptance. A cancel
        # already accepted wins; one arriving during finalization waits and sees
        # the completed state, rather than returning a lost accepted request.
        with legacy._connection(root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if halted():
                raise ObservationCancelled()
            manifest = store.complete(identifier, cancelled=halted)
            connection.execute("UPDATE observation_jobs SET state='completed',completed_rows=?,error=NULL,updated_at=? WHERE job_id=?",
                               (manifest["total_rows"],now_iso(),identifier))
            connection.commit()
    except ObservationCancelled:
        state = _checkpoint(root, identifier, stop_event) or "interrupted"
        manifest = store.set_state(identifier, state)
        _update(root, identifier, state, manifest["completed_rows"])
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        try:
            rows = store.set_state(identifier, "failed", error)["completed_rows"]
        except Exception:
            rows = read_job(root, identifier)["completed_rows"]
        _update(root, identifier, "failed", rows, error)


def _pump(root_string, stop_event):
    root = Path(root_string)
    owner = legacy._lock_stream(root/".worker.lock")
    try:
        parent = multiprocessing.parent_process()
        prefer_observation = False
        while not stop_event.is_set() and (parent is None or parent.is_alive()):
            choices = ((_claim,_run),(legacy._claim_job,legacy._run_job))
            if not prefer_observation:
                choices = choices[::-1]
            for claim, run in choices:
                identifier = claim(root)
                if identifier is not None:
                    run(root,identifier,stop_event)
                    prefer_observation = run is legacy._run_job
                    break
            else:
                stop_event.wait(.1)
    finally:
        legacy._unlock_stream(owner)


class ObservationVolumeManager(legacy.VolumeJobManager):
    """Legacy public methods remain inherited; a facade owns the new table."""
    def __init__(self, root=None):
        super().__init__(root)
        self.observations = ObservationJobs(self)

    def _recover(self):
        super()._recover()
        init_observations(self.root)
        # Only true startup owns the directory without an active HTTP export.
        # Worker replacement and close also call _recover while _started=True;
        # they must not drop an in-progress copy's disk reservation. Retain all
        # orphan physical ZIPs: actual free-space accounting already sees them.
        if not self._started and self._lock_file is not None:
            with legacy._connection(self.root) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("DELETE FROM observation_exports")
                connection.commit()
        from .observation_datasets import ObservationStore
        store = ObservationStore(self.root)
        with legacy._connection(self.root) as connection:
            rows = connection.execute("SELECT * FROM observation_jobs").fetchall()
        for row in rows:
            try:
                manifest = store.manifest(row["job_id"])
                if manifest["complete"]:
                    store.verify_complete(row["job_id"])
                    _update(self.root,row["job_id"],"completed",manifest["total_rows"])
                elif row["state"] in ("running","cancelling"):
                    error = "Worker stopped; resume to verify and continue committed output rows."
                    store.set_state(row["job_id"],"interrupted",error)
                    _update(self.root,row["job_id"],"interrupted",manifest["completed_rows"],error)
            except (KeyError,OSError,ValueError) as exc:
                _update(self.root,row["job_id"],"failed",row["completed_rows"],str(exc))

    def _spawn(self):
        self._stop_event = self._context.Event()
        self._process = self._context.Process(target=_pump,args=(str(self.root),self._stop_event),
                                             name="virtual-microscopy-shared-worker",daemon=True)
        self._process.start()


class ObservationJobs:
    def __init__(self, manager):
        self.manager = manager
        self.root = manager.root

    def estimate(self, request):
        from .observation_plan import plan_observation
        from .batch_jobs import check_reservations
        with self.manager._mutex:
            estimate = plan_observation(self.root, request)
            check_reservations(self.root, extra_estimates=[estimate])
            return estimate

    def submit(self, request):
        from .observation_schemas import ObservationRequest
        from .observation_datasets import ObservationStore
        from .batch_jobs import check_reservations
        with self.manager._mutex:
            self.manager._ensure_worker()
            request = request if isinstance(request,ObservationRequest) else ObservationRequest.model_validate(request)
            estimate = self.estimate(request)
            identifier = str(uuid4())
            store = ObservationStore(self.root)
            manifest = store.create(identifier,request.model_dump(mode="json"),estimate)
            name = request.name or "Finite coherent filter / " + estimate["source_summary"].get("name","Causal recording")
            try:
                with legacy._connection(self.root) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    check_reservations(self.root,extra_estimates=[estimate],connection=connection)
                    connection.execute("""INSERT INTO observation_jobs
                        (job_id,state,name,created_at,updated_at,completed_rows,total_rows,error,kind)
                        VALUES (?,'queued',?,?,?,0,?,NULL,?)""",
                        (identifier,name,manifest["created_at"],manifest["updated_at"],manifest["total_rows"],KIND))
                    connection.commit()
            except Exception:
                try:
                    return read_job(self.root,identifier)
                except KeyError:
                    store.set_state(identifier,"failed","Observation was not published to the job catalog.")
                raise
            return read_job(self.root,identifier)

    def list_jobs(self, limit=100, offset=0):
        if type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or not 0<=offset<=9999:
            raise ValueError("Observation catalog requires limit1..100 and offset0..9999.")
        with self.manager._mutex:
            if self.manager._started:
                self.manager._ensure_worker()
            with legacy._connection(self.root) as connection:
                rows = connection.execute("SELECT * FROM observation_jobs ORDER BY created_at DESC,job_id DESC LIMIT ? OFFSET ?",(limit,offset)).fetchall()
            return [_record(row) for row in rows]

    def get_job(self, identifier):
        with self.manager._mutex:
            if self.manager._started:
                self.manager._ensure_worker()
            return read_job(self.root,identifier)

    def cancel(self, identifier):
        from .observation_datasets import ObservationStore
        with self.manager._mutex:
            self.manager._ensure_worker()
            checked_id(identifier)
            with legacy._connection(self.root) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT * FROM observation_jobs WHERE job_id=?",(identifier,)).fetchone()
                if row is None:
                    raise KeyError(identifier)
                if row["state"]=="completed":
                    raise ValueError("Completed observations are immutable.")
                state = "cancelled" if row["state"]=="queued" else "cancelling" if row["state"]=="running" else row["state"]
                connection.execute("UPDATE observation_jobs SET state=?,updated_at=? WHERE job_id=?",(state,now_iso(),identifier))
                connection.commit()
            if state=="cancelled":
                ObservationStore(self.root).set_state(identifier,state)
            return read_job(self.root,identifier)

    def resume(self, identifier):
        from .observation_datasets import ObservationStore
        from .batch_jobs import check_reservations
        with self.manager._mutex:
            self.manager._ensure_worker()
            job = read_job(self.root,identifier)
            if job["state"] not in legacy.RESUMABLE_STATES:
                raise ValueError("Only cancelled, interrupted or failed observations can resume.")
            store = ObservationStore(self.root)
            manifest = store.validate_identity(identifier)
            if manifest["complete"]:
                raise ValueError("Completed observations are immutable.")
            with legacy._connection(self.root) as connection:
                connection.execute("BEGIN IMMEDIATE")
                check_reservations(self.root,replacing={identifier:manifest},connection=connection)
                store.set_state(identifier,"queued")
                connection.execute("UPDATE observation_jobs SET state='queued',error=NULL,updated_at=? WHERE job_id=?",(now_iso(),identifier))
                connection.commit()
            return read_job(self.root,identifier)

    def manifest(self, identifier):
        from .observation_datasets import ObservationStore
        read_job(self.root,identifier)
        return ObservationStore(self.root).manifest(identifier)
