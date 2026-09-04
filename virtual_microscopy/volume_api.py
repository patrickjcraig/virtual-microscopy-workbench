"""Local volume jobs, immutable datasets and bounded display products."""
from pathlib import Path
import tempfile
import threading
from typing import Literal
import zipfile

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .volume_schemas import SamVolumeRequest
from .sam_volume import estimate_sam
from .volume_processing import sam_view

router = APIRouter(prefix="/api/v2", tags=["Saved volumes"])
# Aborting an HTTP fetch does not stop its synchronous disk reads. Serialize
# numerical views/archive construction to bound their aggregate working memory.
_processing_lock = threading.Lock()


def manager(request: Request):
    value = getattr(request.app.state, "volume_jobs", None)
    if value is None:
        raise HTTPException(503, "The local volume worker has not started. Restart the application.")
    return value


def invoke(function, *args):
    try:
        return function(*args)
    except KeyError as exc:
        raise HTTPException(404, "The requested volume job or dataset does not exist.") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(507, "The local dataset could not be read or written. Check available disk space and access.") from exc


def public_manifest(manifest):
    result = dict(manifest)
    request = result.get("request", {})
    acquisition = request.get("acquisition", result.get("acquisition", {}))
    twin = request.get("twin", {})
    result.setdefault("id", result.get("dataset_id"))
    result.setdefault("dataset_id", result.get("id"))
    result.setdefault("name", twin.get("name", "SAM volume"))
    result.setdefault("acquisition", acquisition)
    result.setdefault("shape", result.get("arrays", {}).get("rf", {}).get("shape"))
    roi = acquisition.get("roi_mm")
    size = twin.get("size_mm")
    if size:
        result.setdefault("extent_mm", [roi[0], roi[2], roi[1], roi[3]] if roi else [0, size[0], 0, size[1]])
    if acquisition and result.get("shape"):
        start = acquisition["record_start_us"]
        result.setdefault("time_range_us", [start, start + (result["shape"][2] - 1) / acquisition["sample_rate_mhz"]])
    return result


@router.post("/estimate")
def estimate(body: SamVolumeRequest):
    from .datasets import check_disk_space, default_data_root
    result = invoke(estimate_sam, body)
    result.update(invoke(check_disk_space, default_data_root(), result["total_bytes"]))
    return result


@router.post("/jobs", status_code=202)
def submit(body: SamVolumeRequest, request: Request):
    from .server import _compute_lock
    if not _compute_lock.acquire(blocking=False):
        raise HTTPException(409, "A preview is running. Wait for it to finish before starting a volume acquisition.")
    try:
        return invoke(manager(request).submit, body)
    finally:
        _compute_lock.release()


@router.get("/jobs")
def jobs(request: Request):
    return {"jobs": invoke(manager(request).list_jobs)}


@router.get("/jobs/{job_id}")
def job(job_id: str, request: Request):
    return invoke(manager(request).get_job, job_id)


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str, request: Request):
    return invoke(manager(request).cancel, job_id)


@router.post("/jobs/{job_id}/resume", status_code=202)
def resume(job_id: str, request: Request):
    from .server import _compute_lock
    if not _compute_lock.acquire(blocking=False):
        raise HTTPException(409, "A preview is running. Wait for it to finish before resuming a volume acquisition.")
    try:
        return invoke(manager(request).resume, job_id)
    finally:
        _compute_lock.release()


@router.get("/datasets")
def datasets(request: Request):
    return {"datasets": [public_manifest(m) for m in invoke(manager(request).list_datasets)]}


@router.get("/datasets/{dataset_id}")
def dataset(dataset_id: str, request: Request):
    return public_manifest(invoke(manager(request).get_manifest, dataset_id))


def complete_path(request, dataset_id):
    jobs = manager(request)
    manifest = invoke(jobs.get_manifest, dataset_id)
    if not manifest.get("complete") or manifest.get("state") != "completed":
        raise HTTPException(409, "This dataset is incomplete. Resume acquisition before opening numerical views or exports.")
    return invoke(jobs.dataset_path, dataset_id)


@router.get("/datasets/{dataset_id}/view")
def view(dataset_id: str, request: Request,
         x_index: int | None = Query(None, ge=0), y_index: int | None = Query(None, ge=0),
         time_index: int | None = Query(None, ge=0),
         gate_start_us: float | None = Query(None, ge=0, allow_inf_nan=False),
         gate_end_us: float | None = Query(None, gt=0, allow_inf_nan=False),
         gate_mode: Literal["peak_envelope", "rms_rf"] = "peak_envelope"):
    path = complete_path(request, dataset_id)
    try:
        with _processing_lock:
            return sam_view(path, x_index=x_index, y_index=y_index, time_index=time_index,
                            gate_start_us=gate_start_us, gate_end_us=gate_end_us, gate_mode=gate_mode)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/datasets/{dataset_id}/export")
def export(dataset_id: str, request: Request):
    path = complete_path(request, dataset_id)
    with _processing_lock:
        return _archive(path, dataset_id)


def _archive(path, dataset_id):
    from .datasets import DatasetStore, check_disk_space
    manifest = invoke(DatasetStore(path.parent).verify_complete, dataset_id)
    invoke(check_disk_space, path.parent, manifest.get("estimate", {}).get("total_bytes", 0))
    # Build on disk, not in API/browser RAM. Stored chunks already use their
    # declared Zarr compression. A unique temporary file prevents overwrites.
    with tempfile.NamedTemporaryFile(prefix="vm-export-", suffix=".zip", dir=path.parent, delete=False) as tmp:
        output = Path(tmp.name)
    try:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for child in [path / "manifest.json", *(path / "data.zarr").rglob("*")]:
                if child.is_symlink():
                    raise ValueError("Dataset exports do not follow symbolic links.")
                if child.is_file():
                    archive.write(child, child.relative_to(path).as_posix())
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return FileResponse(output, media_type="application/zip", filename=f"sam-volume-{dataset_id}.zip",
                        background=BackgroundTask(output.unlink, missing_ok=True))
