"""Transactional admission and durable coordination of bounded acquisition batches.

Only catalog-published jobs can run. Unpublished staged manifests are retained
with an owned journal after failures; recovery never deletes arbitrary paths.
"""
from __future__ import annotations

import json
from math import ceil
from pathlib import Path
from uuid import uuid4

from .datasets import (DatasetStore, atomic_json, canonical_json, checked_id,
                       check_disk_space, json_sha256, now_iso, validate_dataset_paths)

MAX_BATCH_CASES = 4
MAX_BATCH_BYTES = 2 * 1024**3
# Retain the original ownership marker so startup can reconcile v0.9 journals.
# Acquisition kind is frozen in each request, not inferred from this marker.
STAGING_PURPOSE = "virtual_microscopy_sam_batch_staging"
ACTIVE_RESERVATIONS = ("queued", "running", "cancelling")


def init_tables(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS batches (
        batch_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
        plan_sha256 TEXT NOT NULL, plan_json TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        control TEXT NOT NULL DEFAULT 'active'
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS batch_cases (
        case_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL,
        case_index INTEGER NOT NULL, job_id TEXT NOT NULL UNIQUE,
        payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
        UNIQUE(batch_id, case_index),
        FOREIGN KEY(batch_id) REFERENCES batches(batch_id),
        FOREIGN KEY(job_id) REFERENCES jobs(job_id)
    )""")
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(batch_cases)")}
    if "payload_sha256" not in columns:
        connection.execute("ALTER TABLE batch_cases ADD COLUMN payload_sha256 TEXT NOT NULL DEFAULT ''")


def _jobs_module():
    from . import volume_jobs
    return volume_jobs


def _request_kind(request):
    # Do not insert the inferred SAM kind into an existing normalized request:
    # historical recipe, plan and case hashes include the original omission.
    if not isinstance(request, dict):
        raise ValueError("Each batch case requires an acquisition request.")
    kind = request.get("kind", "sam_rf_volume")
    if not isinstance(kind, str) or kind not in {"sam_rf_volume", "xray_projection_volume"}:
        raise ValueError("Batches support saved SAM or X-ray projection acquisitions only.")
    return kind


def _plan_kind(plan):
    kinds = {_request_kind(case.get("request")) for case in plan["cases"]}
    if len(kinds) != 1:
        raise ValueError("All batch cases must have the same acquisition kind; mixed kinds are unsupported.")
    return kinds.pop()


def _store(manager, kind):
    # Preserve the established SAM manager/store instance and its reader API.
    return manager.store if kind == "sam_rf_volume" else _jobs_module().store_for_manifest(manager.root, {"kind": kind})


def _summary(kind, estimates):
    work_keys = (("projection_work_cells", "geometry_cells") if kind == "xray_projection_volume"
                 else ("rf_work_cells", "path_candidate_tests", "path_event_work_units"))
    return {"case_count": len(estimates), "total_bytes": sum(e["total_bytes"] for e in estimates),
            "estimated_peak_bytes": max(e["estimated_peak_bytes"] for e in estimates),
            **{key: sum(e.get(key, 0) for e in estimates) for key in work_keys}}


def _safe_directory(parent: Path, name: str, *, create=False):
    parent = parent.resolve()
    target = parent / name
    if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
        raise ValueError("Batch staging paths cannot contain links or junctions.")
    if target.resolve().parent != parent:
        raise ValueError("Batch staging path leaves its owned parent.")
    if create:
        target.mkdir(exist_ok=False)
    return target


def _temporary(estimate):
    return int(estimate.get("estimated_temporary_bytes", estimate.get("workspace_disk_bytes", 0)))


def _remaining(manifest):
    total, completed = manifest["total_rows"], manifest["completed_rows"]
    output = manifest["estimate"]["total_bytes"]
    if (type(total) is not int or type(completed) is not int or
            total <= 0 or not 0 <= completed <= total or
            type(output) is not int or output < 0):
        raise ValueError("Dataset progress or output reservation is invalid.")
    if manifest["complete"]:
        return 0
    return ceil(output * (total - completed) / total)


def check_reservations(root: Path, *, extra_estimates=(), replacing=None, connection=None):
    """Reserve pending uncompressed output and the largest single-worker cache.

    ``replacing`` supplies current manifests for jobs being resumed, including
    inactive jobs. Existing reservations for those IDs are replaced, not added.
    Dynamic disk numbers are never persisted in an acquisition estimate.

    A worker rechecking only already-running/cancelling jobs does not count ZIP
    leases again: each export was admitted against all pending job output before
    it began, so physical ZIP growth consumes its own admitted slack. Charging
    both that growth and the full lease can spuriously fail an admitted worker.
    New jobs, inactive resumes, queued-only checks and new exports always retain
    the full lease charge. Their admission is therefore conservative during a
    copy. This does not protect against unrelated external disk consumers.
    """
    if connection is None:
        with _jobs_module()._connection(root) as current:
            return check_reservations(root, extra_estimates=extra_estimates,
                                      replacing=replacing, connection=current)
    replacing = replacing or {}
    extra_estimates = tuple(extra_estimates)
    pending = connection.execute("SELECT job_id,kind,state FROM jobs WHERE state IN ('queued','running','cancelling')").fetchall()
    store, output, temporary = DatasetStore(root), 0, 0
    kinds = {row["job_id"]: row["kind"] for row in pending}
    states = {row["job_id"]: row["state"] for row in pending}
    # New observations have a separate catalog, but share this one worker's
    # future disk output. Old-only reservation arithmetic remains identical.
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='observation_jobs'").fetchone():
        for row in connection.execute("SELECT job_id,kind,state FROM observation_jobs WHERE state IN ('queued','running','cancelling')"):
            if row["job_id"] in kinds:
                raise ValueError("A job ID cannot belong to both acquisition and observation catalogs.")
            kinds[row["job_id"]] = row["kind"]
            states[row["job_id"]] = row["state"]
    identifiers = set(kinds) | set(replacing)
    for identifier in identifiers:
        manifest = replacing.get(identifier)
        if manifest is None:
            if kinds[identifier] == "sam_coherent_observation_volume":
                from .observation_datasets import ObservationStore
                reader = ObservationStore(root)
            else:
                reader = (_jobs_module().store_for_manifest(root, {"kind": kinds[identifier]})
                          if kinds[identifier] == "sam_causal_rf_volume" else store)
            manifest = reader.manifest(identifier)
        output += _remaining(manifest)
        if not manifest["complete"]:
            temporary = max(temporary, _temporary(manifest["estimate"]))
    worker_recheck = bool(replacing) and not extra_estimates and all(
        states.get(identifier) in {"running", "cancelling"} for identifier in replacing)
    reserved_exports = 0
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='observation_exports'").fetchone():
        for row in connection.execute("SELECT export_id,reserved_bytes FROM observation_exports"):
            checked_id(row["export_id"])
            value = row["reserved_bytes"]
            if type(value) is not int or value < 0:
                raise ValueError("Observation export reservation is invalid.")
            reserved_exports += value
        if not worker_recheck:
            output += reserved_exports
    reserved_output = output
    for estimate in extra_estimates:
        output += int(estimate["total_bytes"])
        temporary = max(temporary, _temporary(estimate))
    disk = check_disk_space(root, output + temporary)
    return {"reserved_output_bytes": reserved_output,
            "reserved_export_bytes": reserved_exports,
            "counted_export_bytes": 0 if worker_recheck else reserved_exports,
            "already_admitted_worker_recheck": worker_recheck,
            "pending_output_bytes": output, "maximum_temporary_bytes": temporary,
            "required_free_bytes": disk["required_disk_bytes"], **disk}


def normalize_plan(plan):
    if not isinstance(plan, dict) or not isinstance(plan.get("recipe"), dict):
        raise ValueError("A batch requires an immutable recipe record.")
    result = json.loads(canonical_json(plan))
    cases = result.get("cases")
    if not isinstance(cases, list) or not 2 <= len(cases) <= MAX_BATCH_CASES:
        raise ValueError("An acquisition batch requires two to four explicit cases.")
    labels = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Each batch case must be a request record.")
        label = case.get("label")
        if not isinstance(label, str) or not label.strip() or len(label) > 120 or label in labels:
            raise ValueError("Batch case labels must be unique, nonempty and at most 120 characters.")
        labels.add(label)
        if not isinstance(case.get("overrides", {}), (dict, list)):
            raise ValueError("Batch overrides must be explicit structured data.")
        Request = _jobs_module()._backend(_request_kind(case.get("request")))[0]
        request = Request.model_validate(case["request"])
        case["request"] = request.model_dump(mode="json", exclude_none=True)
        case.setdefault("overrides", {})
        if not isinstance(case.get("estimate"), dict):
            raise ValueError("Every batch case requires its reviewed acquisition estimate.")
    _plan_kind(result)
    return result


def _estimate_plan(plan):
    kind = _plan_kind(plan)
    estimate_acquisition = _jobs_module()._backend(kind)[1]
    cases = []
    for index, case in enumerate(plan["cases"]):
        estimate = estimate_acquisition(case["request"])
        if estimate != case["estimate"]:
            raise ValueError(f"Case {index + 1} ({case['label']}) estimate changed; review a fresh plan.")
        cases.append(estimate)
    total = sum(case["total_bytes"] for case in cases)
    if total > MAX_BATCH_BYTES:
        raise ValueError("Acquisition batch output exceeds the 2 GiB aggregate limit.")
    return _summary(kind, cases)


def estimate_batch(manager, plan):
    normalized = normalize_plan(plan)
    estimate = _estimate_plan(normalized)
    disk = check_reservations(manager.root, extra_estimates=[case["estimate"] for case in normalized["cases"]])
    return {**estimate, **disk, "kind": _plan_kind(normalized), "cases": normalized["cases"],
            "plan_sha256": json_sha256(normalized)}


def _key(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("Batch idempotency key must be a nonempty string of at most 128 characters.")
    return value


def _existing(root, key, digest):
    with _jobs_module()._connection(root) as connection:
        row = connection.execute("SELECT batch_id,plan_sha256 FROM batches WHERE idempotency_key=?", (key,)).fetchone()
    if row is None:
        return None
    if row["plan_sha256"] != digest:
        raise ValueError("Idempotency key already belongs to different batch content.")
    return get_batch(root, row["batch_id"])


def _publish_batch(root, batch_id, key, digest, plan, cases, timestamp):
    """The sole runnable-publication point; injectable for crash regression tests."""
    with _jobs_module()._connection(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        check_reservations(root, extra_estimates=[case["estimate"] for case in plan["cases"]], connection=connection)
        connection.execute("INSERT INTO batches VALUES (?,?,?,?,?,?,'active')",
                           (batch_id, key, digest, canonical_json(plan).decode(), timestamp, timestamp))
        for index, case in enumerate(cases):
            identifier = case["job_id"]
            connection.execute("""INSERT INTO jobs
                (job_id,state,name,created_at,updated_at,completed_rows,total_rows,error,kind)
                VALUES (?,'queued',?,?,?,0,?,NULL,?)""",
                (identifier, case["label"], timestamp, timestamp, case["manifest"]["total_rows"],
                 _request_kind(case["request"])))
            payload = {name: value for name, value in case.items() if name != "manifest"}
            connection.execute("""INSERT INTO batch_cases
                (case_id,batch_id,case_index,job_id,payload_json,payload_sha256) VALUES (?,?,?,?,?,?)""",
                (case["case_id"], batch_id, index, identifier, canonical_json(payload).decode(), json_sha256(payload)))
        connection.commit()


def submit_batch(manager, plan, idempotency_key):
    plan = normalize_plan(plan)
    key, digest = _key(idempotency_key), json_sha256(plan)
    previous = _existing(manager.root, key, digest)
    if previous is not None:
        return previous
    _estimate_plan(plan)
    check_reservations(manager.root, extra_estimates=[case["estimate"] for case in plan["cases"]])
    identifier, timestamp = str(uuid4()), now_iso()
    staging_root = _safe_directory(manager.root, ".batch-staging")
    staging_root.mkdir(exist_ok=True)
    stage = _safe_directory(staging_root, identifier, create=True)
    cases = [{**case, "case_id": str(uuid4()), "job_id": str(uuid4())} for case in plan["cases"]]
    journal = {"schema_version": 1, "purpose": STAGING_PURPOSE, "batch_id": identifier,
               "state": "staging", "created_at": timestamp, "idempotency_key": key,
               "plan_sha256": digest, "job_ids": [case["job_id"] for case in cases]}
    atomic_json(stage / "owner.json", journal)
    atomic_json(stage / "plan.json", plan)
    stage_store = _jobs_module().store_for_manifest(stage, {"kind": _plan_kind(plan)})
    try:
        for case in cases:
            case["manifest"] = stage_store.create(case["job_id"], case["request"], case["estimate"])
            case["dataset_id"] = case["job_id"]
            case["input_sha256"] = case["manifest"]["input_sha256"]
            case["request_sha256"] = case["manifest"]["request_sha256"]
            case["materials_sha256"] = case["manifest"]["materials_sha256"]
            case["solver"] = case["manifest"]["solver"]
        # No arrays exist yet. Every move is inside the owned root and every
        # destination is a fresh UUID; there is no overwrite or recursive move.
        for case in cases:
            source = _safe_directory(stage, checked_id(case["job_id"]))
            target = _safe_directory(manager.root, case["job_id"])
            validate_dataset_paths(source, case["job_id"], include_arrays=False)
            if target.exists():
                raise FileExistsError(target)
            source.rename(target)
        _publish_batch(manager.root, identifier, key, digest, plan, cases, timestamp)
    except BaseException:
        # SQL may have committed before a connection/reporting failure. The
        # journal is advisory; catalog publication is authoritative.
        try:
            previous = _existing(manager.root, key, digest)
            journal["state"] = "published" if previous else "abandoned"
            atomic_json(stage / "owner.json", journal)
        except Exception:
            pass
        raise
    # A failed acknowledgement must not make a successful transaction look like
    # an acquisition failure; startup can reconcile an unchanged staging marker.
    try:
        journal["state"] = "published"
        atomic_json(stage / "owner.json", journal)
    except OSError:
        pass
    return get_batch(manager.root, identifier)


def recover_staging(root):
    base = _safe_directory(root, ".batch-staging")
    if not base.exists():
        return
    for stage in base.iterdir():
        try:
            checked_id(stage.name)
        except ValueError:
            continue
        stage = _safe_directory(base, stage.name)
        marker = stage / "owner.json"
        if marker.is_symlink() or not marker.is_file():
            continue
        try:
            journal = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if journal.get("purpose") != STAGING_PURPOSE or journal.get("schema_version") != 1 or journal.get("batch_id") != stage.name:
            continue
        with _jobs_module()._connection(root) as connection:
            published = connection.execute("SELECT 1 FROM batches WHERE batch_id=?", (stage.name,)).fetchone()
        state = "published" if published else "abandoned"
        if journal.get("state") != state:
            journal["state"] = state
            atomic_json(marker, journal)


def _batch_state(control, states):
    if all(state == "completed" for state in states):
        return "completed"
    if "cancelling" in states or (control == "cancelled" and "running" in states):
        return "cancelling"
    if "running" in states:
        return "running"
    if control == "cancelled":
        return "cancelled"
    for state in ("failed", "interrupted", "cancelled"):
        if state in states:
            return state
    return "queued"


def get_batch(root, identifier):
    checked_id(identifier)
    jobs = _jobs_module()
    with jobs._connection(root) as connection:
        batch = connection.execute("SELECT * FROM batches WHERE batch_id=?", (identifier,)).fetchone()
        rows = connection.execute("""SELECT c.case_id,c.case_index,c.payload_json,c.payload_sha256,j.*
            FROM batch_cases c JOIN jobs j ON j.job_id=c.job_id
            WHERE c.batch_id=? ORDER BY c.case_index""", (identifier,)).fetchall()
    if batch is None:
        raise KeyError(identifier)
    plan = json.loads(batch["plan_json"])
    if json_sha256(plan) != batch["plan_sha256"] or len(rows) != len(plan["cases"]):
        raise ValueError("Frozen batch identity or case registry is corrupt.")
    kind = _plan_kind(plan)
    cases = []
    for index, (row, frozen) in enumerate(zip(rows, plan["cases"])):
        payload = json.loads(row["payload_json"])
        if (row["case_index"] != index or json_sha256(payload) != row["payload_sha256"] or
                payload.get("case_id") != row["case_id"] or payload.get("job_id") != row["job_id"] or
                (row["kind"] or "sam_rf_volume") != kind or
                any(payload.get(key) != value for key, value in frozen.items())):
            raise ValueError("Frozen batch case content changed.")
        job = jobs._job_dict({key: row[key] for key in row.keys() if key not in {"payload_json", "payload_sha256", "case_id", "case_index"}})
        cases.append({**payload, **job, "case_id": row["case_id"], "case_index": row["case_index"],
                      "case_sha256": row["payload_sha256"], "batch_id": identifier})
    state = _batch_state(batch["control"], [case["state"] for case in cases])
    estimates = [case["estimate"] for case in cases]
    return {"id": identifier, "batch_id": identifier, "kind": kind, "state": state, "status": state,
            "created_at": batch["created_at"],
            "updated_at": max([batch["updated_at"]]+[case["updated_at"] for case in cases]),
            "case_count": len(cases), "total_cases": len(cases),
            "completed_cases": sum(case["state"] == "completed" for case in cases),
            "plan_sha256": batch["plan_sha256"], "recipe": plan["recipe"], "plan": plan,
            "proposal": plan.get("proposal"), "comparison_basis": plan.get("comparison_basis"), "cases": cases,
            "input_sha256": json_sha256({"plan_sha256": batch["plan_sha256"],
                                         "case_sha256": [case["case_sha256"] for case in cases]}),
            "error": next((case["error"] for case in cases if case["error"]), None),
            "estimate": _summary(kind, estimates)}


def list_batches(root):
    with _jobs_module()._connection(root) as connection:
        identifiers = connection.execute("SELECT batch_id FROM batches ORDER BY created_at DESC,batch_id DESC").fetchall()
    return [get_batch(root, row["batch_id"]) for row in identifiers]


def get_batch_by_key(root, key):
    with _jobs_module()._connection(root) as connection:
        row = connection.execute("SELECT batch_id FROM batches WHERE idempotency_key=?", (_key(key),)).fetchone()
    return None if row is None else get_batch(root, row["batch_id"])


def reject_individual_control(root, identifier):
    with _jobs_module()._connection(root) as connection:
        row = connection.execute("SELECT batch_id FROM batch_cases WHERE job_id=?", (identifier,)).fetchone()
    if row is not None:
        raise ValueError(f"This job belongs to batch {row['batch_id']}; use the batch cancel/resume action.")


def validate_case_identity(root, identifier, manifest):
    with _jobs_module()._connection(root) as connection:
        row = connection.execute("SELECT batch_id FROM batch_cases WHERE job_id=?", (identifier,)).fetchone()
    if row is None:
        return
    batch = get_batch(root, row["batch_id"])
    case = next(case for case in batch["cases"] if case["job_id"] == identifier)
    if (manifest.get("kind", "sam_rf_volume") != batch["kind"] or
            manifest["input_sha256"] != case["input_sha256"] or
            manifest["request"] != case["request"] or manifest["estimate"] != case["estimate"] or
            manifest["solver"] != case["solver"] or manifest["materials_sha256"] != case["materials_sha256"]):
        raise ValueError("Frozen batch case dataset identity changed.")


def cancel_batch(manager, identifier):
    batch = get_batch(manager.root, identifier)
    if batch["state"] == "completed":
        raise ValueError("Completed batches are immutable and cannot be cancelled.")
    with _jobs_module()._connection(manager.root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("UPDATE batches SET control='cancelled',updated_at=? WHERE batch_id=?", (now_iso(), identifier))
        connection.execute("""UPDATE jobs SET state=CASE WHEN state='running' THEN 'cancelling'
            WHEN state='queued' THEN 'cancelled' ELSE state END,updated_at=?
            WHERE job_id IN (SELECT job_id FROM batch_cases WHERE batch_id=?) AND state!='completed'""", (now_iso(), identifier))
        connection.commit()
    for case in get_batch(manager.root, identifier)["cases"]:
        if case["state"] == "cancelled":
            _store(manager, batch["kind"]).set_state(case["job_id"], "cancelled")
    return get_batch(manager.root, identifier)


def resume_batch(manager, identifier):
    batch = get_batch(manager.root, identifier)
    if batch["state"] not in {"failed", "interrupted", "cancelled"}:
        raise ValueError("Only failed, interrupted or cancelled batches can be resumed.")
    store = _store(manager, batch["kind"])
    estimate_acquisition = _jobs_module()._backend(batch["kind"])[1]
    unfinished = {}
    # Validate every case before changing any case's queue state. Completed
    # historical data is checked with its frozen reader, never current solver.
    for case in batch["cases"]:
        path = manager.root / case["job_id"]
        validate_dataset_paths(path, case["job_id"], include_arrays=False)
        manifest = store.manifest(case["job_id"])
        if manifest["arrays_initialized"]:
            validate_dataset_paths(path, case["job_id"])
        validate_case_identity(manager.root, case["job_id"], manifest)
        if manifest["complete"]:
            store.verify_complete(case["job_id"])
        else:
            manifest = store.validate_identity(case["job_id"])
            if estimate_acquisition(manifest["request"]) != manifest["estimate"]:
                raise ValueError("Batch case acquisition estimate changed; create a new batch.")
            unfinished[case["job_id"]] = manifest
    for job_id, manifest in list(unfinished.items()):
        if manifest["arrays_initialized"]:
            unfinished[job_id] = store.verify_chunks(job_id)
    check_reservations(manager.root, replacing=unfinished)
    for job_id in unfinished:
        store.set_state(job_id, "queued")
    with _jobs_module()._connection(manager.root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        check_reservations(manager.root, replacing=unfinished, connection=connection)
        for case in batch["cases"]:
            manifest = unfinished.get(case["job_id"])
            state, rows = ("queued", manifest["completed_rows"]) if manifest else ("completed", case["total_rows"])
            connection.execute("UPDATE jobs SET state=?,completed_rows=?,error=NULL,updated_at=? WHERE job_id=?",
                               (state, rows, now_iso(), case["job_id"]))
        connection.execute("UPDATE batches SET control='active',updated_at=? WHERE batch_id=?", (now_iso(), identifier))
        connection.commit()
    return get_batch(manager.root, identifier)
