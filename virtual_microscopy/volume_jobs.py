"""Single local spawn worker, durable SQLite queue and resumable microscopy jobs.

The worker never holds an HTTP request open. Cancellation is cooperative at
canonical row boundaries; shutdown interrupts a job and preserves committed
chunks. One manager may own a data directory at a time, including on Windows.
"""
from __future__ import annotations

from contextlib import contextmanager
import multiprocessing
import os
from pathlib import Path
import sqlite3
import threading
import time
from uuid import uuid4

from .datasets import DatasetStore, checked_id, check_disk_space, default_data_root, now_iso


TERMINAL_STATES = frozenset({"cancelled", "interrupted", "failed", "completed"})
RESUMABLE_STATES = frozenset({"cancelled", "interrupted", "failed"})


@contextmanager
def _connection(root: Path):
    connection = sqlite3.connect(str(root / "catalog.sqlite3"), timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def _init_catalog(root: Path) -> None:
    with _connection(root) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("""CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY, state TEXT NOT NULL, name TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            completed_rows INTEGER NOT NULL, total_rows INTEGER NOT NULL,
            error TEXT, kind TEXT NOT NULL DEFAULT 'sam_rf_volume'
        )""")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
        if "kind" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'sam_rf_volume'")
        connection.commit()


def _job_dict(row) -> dict:
    result = dict(row)
    kind = result.get("kind") or "sam_rf_volume"
    result.update(id=result["job_id"], dataset_id=result["job_id"],
                  status=result["state"], progress=result["completed_rows"] / result["total_rows"], kind=kind,
                  completed_units=result["completed_rows"], total_units=result["total_rows"],
                  progress_unit={"xray_projection_volume": "views", "xray_reconstruction": "slices", "sam_depth_volume": "slices"}.get(kind, "rows"))
    return result


def store_for_manifest(root: Path, manifest: dict) -> DatasetStore:
    kind = manifest.get("kind", "sam_rf_volume")
    if kind == "sam_rf_volume":
        return DatasetStore(root)
    if kind == "xray_projection_volume":
        from .xray_datasets import XrayDatasetStore
        return XrayDatasetStore(root)
    if kind == "xray_reconstruction":
        from .reconstruction_datasets import ReconstructionDatasetStore
        return ReconstructionDatasetStore(root)
    if kind == "sam_depth_volume":
        from .depth_datasets import DepthDatasetStore
        return DepthDatasetStore(root)
    raise ValueError(f"Unsupported dataset kind: {kind}.")


def _backend(kind: str):
    if kind == "sam_rf_volume":
        from .sam_volume import estimate_sam, prepare_sam, iter_sam_tiles
        from .volume_schemas import SamVolumeRequest
        return SamVolumeRequest, estimate_sam, prepare_sam, iter_sam_tiles
    if kind == "xray_projection_volume":
        from .xray_volume import estimate_xray, prepare_xray, iter_xray_views
        from .xray_schemas import XrayVolumeRequest
        return XrayVolumeRequest, estimate_xray, prepare_xray, iter_xray_views
    if kind == "xray_reconstruction":
        from .reconstruction import estimate_reconstruction, prepare_reconstruction, iter_reconstruction_slices
        from .reconstruction_schemas import ReconstructionRequest
        return ReconstructionRequest, estimate_reconstruction, prepare_reconstruction, iter_reconstruction_slices
    if kind == "sam_depth_volume":
        from .depth_mapping import estimate_depth, prepare_depth, iter_depth_slices
        from .depth_schemas import SamDepthRequest
        return SamDepthRequest, estimate_depth, prepare_depth, iter_depth_slices
    raise ValueError(f"Unsupported acquisition kind: {kind}.")


def _read_job(root: Path, identifier: str) -> dict:
    checked_id(identifier)
    with _connection(root) as connection:
        row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (identifier,)).fetchone()
    if row is None:
        raise KeyError(identifier)
    return _job_dict(row)


def _update_job(root: Path, identifier: str, state: str, completed_rows: int,
                error: str | None = None) -> None:
    with _connection(root) as connection:
        connection.execute("UPDATE jobs SET state = ?, completed_rows = ?, error = ?, updated_at = ? WHERE job_id = ?",
                           (state, completed_rows, error, now_iso(), identifier))
        connection.commit()


def _claim_job(root: Path) -> str | None:
    with _connection(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT job_id FROM jobs WHERE state = 'queued' ORDER BY created_at, job_id LIMIT 1").fetchone()
        if row is None:
            connection.commit()
            return None
        identifier = row["job_id"]
        connection.execute("UPDATE jobs SET state = 'running', updated_at = ? WHERE job_id = ?", (now_iso(), identifier))
        connection.commit()
        return identifier


def _checkpoint(root: Path, identifier: str, stop_event) -> str | None:
    parent = multiprocessing.parent_process()
    if stop_event.is_set() or (parent is not None and not parent.is_alive()):
        return "interrupted"
    if _read_job(root, identifier)["state"] == "cancelling":
        return "cancelled"
    return None


def _run_job(root: Path, identifier: str, stop_event) -> None:
    # Imported in the spawned process so solver memory and runtime are isolated
    # from the HTTP server. No unbounded full RF volume is retained in RAM.
    store = DatasetStore(root)
    prepared = None
    try:
        manifest = store.manifest(identifier)
        kind = manifest.get("kind", "sam_rf_volume")
        store = store_for_manifest(root, manifest)
        manifest = store.validate_identity(identifier)
        if manifest["complete"]:
            _update_job(root, identifier, "completed", manifest["total_rows"])
            return
        halted = _checkpoint(root, identifier, stop_event)
        if halted:
            store.set_state(identifier, halted)
            _update_job(root, identifier, halted, manifest["completed_rows"])
            return
        Request, estimate_acquisition, prepare_acquisition, iterate = _backend(kind)
        request = Request.model_validate(manifest["request"])
        if kind in {"xray_reconstruction", "sam_depth_volume"}:
            source, source_path = store.source_context(identifier, verify=True)
            acquisition_args = (request, source, source_path)
        else:
            acquisition_args = (request,)
        estimate = estimate_acquisition(*acquisition_args)
        if estimate != manifest["estimate"]:
            raise ValueError("Acquisition estimate changed; create a new dataset instead of resuming.")
        # Repeat the disk check immediately before preparing potentially large
        # arrays; another queued job or application may have used the space.
        remaining = estimate["total_bytes"] * (1 - manifest["completed_rows"] / manifest["total_rows"])
        check_disk_space(root, int(remaining) + estimate.get("estimated_temporary_bytes", estimate.get("workspace_disk_bytes", 0)))
        store.set_state(identifier, "running")
        prepared = prepare_acquisition(*acquisition_args)
        if kind == "sam_rf_volume":
            store.initialize_arrays(identifier, prepared.x_mm, prepared.y_mm, prepared.time_us, prepared.metadata)
        else:
            store.initialize_arrays(identifier, prepared)
        manifest = store.verify_chunks(identifier)
        with _connection(root) as connection:
            connection.execute("UPDATE jobs SET completed_rows = ?, updated_at = ? WHERE job_id = ?",
                               (manifest["completed_rows"], now_iso(), identifier))
            connection.commit()
        missing = [y for y in range(0, manifest["total_rows"], manifest["tile_rows"])
                   if str(y) not in manifest["completed_chunks"]]
        start = min(missing, default=manifest["total_rows"])
        start_key = {"sam_rf_volume": "start_row", "xray_projection_volume": "start_view",
                     "xray_reconstruction": "start_slice", "sam_depth_volume": "start_slice"}[kind]
        iterator = iterate(prepared, **{start_key: start})
        while True:
            halted = _checkpoint(root, identifier, stop_event)
            if halted:
                store.set_state(identifier, halted)
                _update_job(root, identifier, halted, manifest["completed_rows"])
                return
            try:
                item = next(iterator)
            except StopIteration:
                break
            y0, y1 = item[:2]
            if str(y0) in manifest["completed_chunks"]:
                continue
            check_disk_space(root, (y1 - y0) * manifest["shape"][1] * manifest["shape"][2] * 4 * len(store.signal_units))
            write_chunk = {"sam_rf_volume": "write_tile", "xray_projection_volume": "write_view",
                           "xray_reconstruction": "write_slice", "sam_depth_volume": "write_slice"}[kind]
            manifest = getattr(store, write_chunk)(identifier, *item)
            # Do not overwrite a concurrently requested cancellation with a
            # progress update. The manifest and catalog have separate duties.
            with _connection(root) as connection:
                connection.execute("UPDATE jobs SET completed_rows = ?, updated_at = ? WHERE job_id = ?",
                                   (manifest["completed_rows"], now_iso(), identifier))
                connection.commit()
        halted = _checkpoint(root, identifier, stop_event)
        if halted:
            store.set_state(identifier, halted)
            _update_job(root, identifier, halted, manifest["completed_rows"])
            return
        manifest = store.complete(identifier)
        _update_job(root, identifier, "completed", manifest["total_rows"])
    except Exception as exc:
        # A failed dataset remains inspectable and may be resumed after fixing
        # disk or I/O problems. A model mismatch intentionally stays blocked.
        error = f"{type(exc).__name__}: {exc}"
        try:
            manifest = store.set_state(identifier, "failed", error)
            rows = manifest["completed_rows"]
        except Exception:
            rows = _read_job(root, identifier)["completed_rows"]
        _update_job(root, identifier, "failed", rows, error)
    finally:
        close = getattr(prepared, "close", None)
        if close is not None:
            try:
                close()
            except Exception as exc:
                job = _read_job(root, identifier)
                previous = f"{job['error']} " if job["error"] else ""
                _update_job(root, identifier, job["state"], job["completed_rows"],
                            f"{previous}Temporary derived-volume cache cleanup failed: {exc}")


def _lock_stream(path: Path):
    stream = path.open("a+b")
    stream.seek(0, os.SEEK_END)
    if stream.tell() == 0:
        stream.write(b"0")
        stream.flush()
    stream.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise
    return stream


def _unlock_stream(stream):
    stream.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    stream.close()


def _worker_main(root_string: str, stop_event) -> None:
    root = Path(root_string)
    owner = _lock_stream(root / ".worker.lock")
    try:
        parent = multiprocessing.parent_process()
        while not stop_event.is_set() and (parent is None or parent.is_alive()):
            identifier = _claim_job(root)
            if identifier is None:
                stop_event.wait(0.1)
            else:
                _run_job(root, identifier, stop_event)
    finally:
        _unlock_stream(owner)


class VolumeJobManager:
    def __init__(self, root: Path | None = None):
        self.root = Path(root).resolve() if root is not None else default_data_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = DatasetStore(self.root)
        _init_catalog(self.root)
        self._context = multiprocessing.get_context("spawn")
        self._process = None
        self._stop_event = None
        self._lock_file = None
        self._mutex = threading.RLock()
        self._started = False

    def _acquire_owner(self):
        try:
            self._lock_file = _lock_stream(self.root / ".manager.lock")
        except OSError as exc:
            raise RuntimeError("Another workbench worker owns this volume data directory.") from exc

    def _release_owner(self):
        if self._lock_file is None:
            return
        _unlock_stream(self._lock_file)
        self._lock_file = None

    def _wait_for_orphan(self):
        # A hard-killed HTTP process may leave its child finishing one tile.
        # Wait for that child's parent-death checkpoint before touching files.
        deadline = time.monotonic() + 5
        while True:
            try:
                stream = _lock_stream(self.root / ".worker.lock")
                _unlock_stream(stream)
                return
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Previous volume worker is still stopping; retry startup shortly.") from exc
                time.sleep(0.05)

    def _recover(self):
        from .reconstruction_datasets import cleanup_reconstruction_caches
        from .depth_datasets import cleanup_depth_caches
        cleanup_reconstruction_caches(self.root)
        cleanup_depth_caches(self.root)
        with _connection(self.root) as connection:
            rows = connection.execute("SELECT * FROM jobs").fetchall()
        for row in rows:
            try:
                manifest = self.store.manifest(row["job_id"])
                if manifest["complete"]:
                    _update_job(self.root, row["job_id"], "completed", manifest["total_rows"])
                elif row["state"] in {"running", "cancelling"}:
                    error = "Worker stopped before completion; resume to verify and continue saved chunks."
                    self.store.set_state(row["job_id"], "interrupted", error)
                    _update_job(self.root, row["job_id"], "interrupted", manifest["completed_rows"], error)
            except (KeyError, OSError, ValueError) as exc:
                _update_job(self.root, row["job_id"], "failed", row["completed_rows"], str(exc))

    def _spawn(self):
        self._stop_event = self._context.Event()
        self._process = self._context.Process(target=_worker_main, args=(str(self.root), self._stop_event),
                                               name="virtual-microscopy-volume-worker", daemon=True)
        self._process.start()

    def start(self):
        with self._mutex:
            if self._started:
                self._ensure_worker()
                return
            self._acquire_owner()
            try:
                self._wait_for_orphan()
                self._recover()
                self._spawn()
                self._started = True
            except Exception:
                self._release_owner()
                raise

    def _ensure_worker(self):
        if not self._started:
            raise RuntimeError("Volume job manager has not been started.")
        if self._process is not None and not self._process.is_alive():
            self._process.join()
            self._process.close()
            self._recover()
            self._spawn()

    def close(self):
        with self._mutex:
            if not self._started:
                return
            try:
                self._stop_event.set()
                self._process.join(timeout=5)
                if self._process.is_alive():
                    self._process.terminate()
                    self._process.join(timeout=5)
                self._process.close()
                self._recover()
            finally:
                self._started = False
                self._process = None
                self._release_owner()

    def submit(self, request) -> dict:
        with self._mutex:
            self._ensure_worker()
            kind = request.get("kind", "sam_rf_volume") if isinstance(request, dict) else getattr(request, "kind", "sam_rf_volume")
            Request, estimate_acquisition, _, _ = _backend(kind)
            request = request if isinstance(request, Request) else Request.model_validate(request)
            if kind == "xray_reconstruction":
                from .reconstruction_datasets import reconstruction_source
                source, source_path = reconstruction_source(self.root, request.source_dataset_id)
                estimate = estimate_acquisition(request, source, source_path)
                name = "Reconstruction / " + source["request"]["twin"].get("name", "X-ray projections")
            elif kind == "sam_depth_volume":
                from .depth_datasets import depth_source
                source, source_path = depth_source(self.root, request.source_dataset_id)
                estimate = estimate_acquisition(request, source, source_path)
                name = "Depth estimate / " + source["request"]["twin"].get("name", "Raw-time SAM acquisition")
            else:
                estimate = estimate_acquisition(request)
                name = request.twin.name
            identifier = str(uuid4())
            store = store_for_manifest(self.root, {"kind": kind})
            manifest = store.create(identifier, request.model_dump(mode="json", exclude_none=True), estimate)
            with _connection(self.root) as connection:
                connection.execute("""INSERT INTO jobs
                    (job_id, state, name, created_at, updated_at, completed_rows, total_rows, error, kind)
                    VALUES (?, 'queued', ?, ?, ?, 0, ?, NULL, ?)""",
                                   (identifier, name, manifest["created_at"], manifest["updated_at"],
                                    manifest["total_rows"], kind))
                connection.commit()
            return _read_job(self.root, identifier)

    def list_jobs(self) -> list[dict]:
        with self._mutex:
            if self._started:
                self._ensure_worker()
            with _connection(self.root) as connection:
                return [_job_dict(row) for row in connection.execute("SELECT * FROM jobs ORDER BY created_at DESC, job_id DESC")]

    def estimate_reconstruction(self, request) -> dict:
        from .reconstruction_datasets import reconstruction_source
        Request, estimate_acquisition, _, _ = _backend("xray_reconstruction")
        request = request if isinstance(request, Request) else Request.model_validate(request)
        source, source_path = reconstruction_source(self.root, request.source_dataset_id)
        estimate = estimate_acquisition(request, source, source_path)
        check_disk_space(self.root, estimate["total_bytes"] + estimate.get("estimated_temporary_bytes", estimate.get("workspace_disk_bytes", 0)))
        return estimate

    def estimate_depth(self, request) -> dict:
        from .depth_datasets import depth_source
        Request, estimate_acquisition, _, _ = _backend("sam_depth_volume")
        request = request if isinstance(request, Request) else Request.model_validate(request)
        source, source_path = depth_source(self.root, request.source_dataset_id)
        estimate = estimate_acquisition(request, source, source_path)
        check_disk_space(self.root, estimate["total_bytes"] + estimate.get("estimated_temporary_bytes", estimate.get("workspace_disk_bytes", 0)))
        return estimate

    def get_job(self, identifier: str) -> dict:
        with self._mutex:
            if self._started:
                self._ensure_worker()
            return _read_job(self.root, identifier)

    def cancel(self, identifier: str) -> dict:
        with self._mutex:
            self._ensure_worker()
            checked_id(identifier)
            with _connection(self.root) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (identifier,)).fetchone()
                if row is None:
                    raise KeyError(identifier)
                if row["state"] == "completed":
                    raise ValueError("Completed datasets are immutable and cannot be cancelled.")
                state = "cancelled" if row["state"] == "queued" else "cancelling" if row["state"] == "running" else row["state"]
                connection.execute("UPDATE jobs SET state = ?, updated_at = ? WHERE job_id = ?", (state, now_iso(), identifier))
                connection.commit()
            if state == "cancelled":
                self.store.set_state(identifier, "cancelled")
            return _read_job(self.root, identifier)

    def resume(self, identifier: str) -> dict:
        with self._mutex:
            self._ensure_worker()
            job = _read_job(self.root, identifier)
            if job["state"] not in RESUMABLE_STATES:
                raise ValueError("Only cancelled, interrupted or failed jobs can be resumed.")
            store = self.get_store(identifier)
            manifest = store.validate_identity(identifier)
            if manifest["complete"]:
                raise ValueError("Completed datasets are immutable.")
            remaining = manifest["estimate"]["total_bytes"] * (1 - manifest["completed_rows"] / manifest["total_rows"])
            estimate = manifest["estimate"]
            check_disk_space(self.root, int(remaining) + estimate.get("estimated_temporary_bytes", estimate.get("workspace_disk_bytes", 0)))
            store.set_state(identifier, "queued")
            _update_job(self.root, identifier, "queued", manifest["completed_rows"])
            return _read_job(self.root, identifier)

    def list_datasets(self) -> list[dict]:
        result = []
        for job in self.list_jobs():
            try:
                manifest = self.store.manifest(job["dataset_id"])
                result.append({**job, "complete": manifest["complete"], "shape": manifest["shape"],
                               "axis_order": manifest["axis_order"], "input_sha256": manifest["input_sha256"],
                               "evidence_status": manifest["evidence_status"]})
            except (KeyError, OSError, ValueError):
                continue
        return result

    def get_manifest(self, identifier: str) -> dict:
        return self.store.manifest(identifier)

    def get_store(self, identifier: str) -> DatasetStore:
        return store_for_manifest(self.root, self.get_manifest(identifier))

    def dataset_path(self, identifier: str) -> Path:
        _read_job(self.root, identifier)
        return self.store.path(identifier)
