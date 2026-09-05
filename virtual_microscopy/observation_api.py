"""Separate derived observation lifecycle, catalogs and source-free inspection."""
from pathlib import Path
import tempfile
from typing import Literal
import zipfile

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from .batch_jobs import check_reservations
from .volume_jobs import _connection
from .causal_comparison_store import bounded_payload
from .observation_schemas import ObservationRequest
from .observation_datasets import ObservationStore
from .observation_processing import observation_view
from .volume_api import invoke, manager, _processing_lock

router = APIRouter(prefix="/api/v2/observations",tags=["Finite coherent observations"])


def _jobs(request):
    return manager(request).observations


def _json(value,status=200):
    return Response(invoke(bounded_payload,value),status_code=status,media_type="application/json")


def public_estimate(estimate):
    excluded = {"source_manifest","source_bound_map","minimum_source_propagation"}
    result = {key:value for key,value in estimate.items() if key not in excluded}
    result["coordinates"] = {key:estimate[key] for key in ("x_mm","y_mm","time_us")}
    result["acquisition"] = estimate["source_summary"]["acquisition"]
    return result


def public_manifest(manifest, *, summary=False, job=None):
    e = manifest["estimate"]
    result = ({key:manifest[key] for key in ("dataset_id","kind","shape","axis_order","dtype","complete","state",
                  "completed_rows","total_rows","created_at","updated_at","input_sha256","evidence_status")} if summary else dict(manifest))
    if job:
        result.update(job)
    result.update(id=manifest["dataset_id"],source_dataset_id=manifest["request"]["source_dataset_id"],
                  source_summary=e["source_summary"],acquisition=e["source_summary"]["acquisition"],
                  name=manifest["request"]["name"] or "Finite coherent filter / "+e["source_summary"]["name"],
                  coordinates={key:e[key] for key in ("x_mm","y_mm","time_us")})
    for key in ("source_shape","source_indices","extent_mm","source_extent_mm","operator","resources"):
        result[key]=e[key]
    result["requested_tolerance"]=manifest["request"]["absolute_tolerance"]
    return result


@router.post("/estimate")
def estimate(body:ObservationRequest,request:Request):
    with _processing_lock:
        return _json(public_estimate(invoke(_jobs(request).estimate,body)))


@router.post("/jobs",status_code=202)
def submit(body:ObservationRequest,request:Request):
    with _processing_lock:
        return _json(invoke(_jobs(request).submit,body),202)


@router.get("/jobs")
def jobs(request:Request,limit:int=Query(100,ge=1,le=100),offset:int=Query(0,ge=0,le=9999)):
    with _processing_lock:
        return _json({"jobs":invoke(_jobs(request).list_jobs,limit,offset),"limit":limit,"offset":offset})


@router.get("/jobs/{identifier}")
def job(identifier:str,request:Request):
    return _json(invoke(_jobs(request).get_job,identifier))


@router.post("/jobs/{identifier}/cancel",status_code=202)
def cancel(identifier:str,request:Request):
    return _json(invoke(_jobs(request).cancel,identifier),202)


@router.post("/jobs/{identifier}/resume",status_code=202)
def resume(identifier:str,request:Request):
    with _processing_lock:
        return _json(invoke(_jobs(request).resume,identifier),202)


@router.get("/datasets")
def datasets(request:Request,limit:int=Query(100,ge=1,le=100),offset:int=Query(0,ge=0,le=9999)):
    with _processing_lock:
        records = invoke(_jobs(request).list_jobs,limit,offset)
        values=[]
        for record in records:
            manifest=invoke(_jobs(request).manifest,record["dataset_id"])
            values.append(public_manifest(manifest,summary=True,job=record))
        return _json({"datasets":values,"limit":limit,"offset":offset})


@router.get("/datasets/{identifier}")
def dataset(identifier:str,request:Request):
    with _processing_lock:
        return _json(public_manifest(invoke(_jobs(request).manifest,identifier)))


@router.get("/datasets/{identifier}/view")
def view(identifier:str,request:Request,x_index:int|None=Query(None,ge=0,le=61),
         y_index:int|None=Query(None,ge=0,le=61),time_index:int|None=Query(None,ge=0,le=2048),
         product:Literal["rf","imaginary","envelope"]="envelope",
         gate_start_us:float|None=Query(None,ge=0,allow_inf_nan=False),gate_end_us:float|None=Query(None,gt=0,allow_inf_nan=False),
         gate_mode:Literal["peak_envelope","rms_rf"]="peak_envelope"):
    invoke(_jobs(request).get_job,identifier)
    with _processing_lock:
        return _json(invoke(observation_view,manager(request).root,identifier,x_index=x_index,y_index=y_index,
                           time_index=time_index,product=product,gate_start_us=gate_start_us,gate_end_us=gate_end_us,gate_mode=gate_mode))


def _archive(root, identifier, *, admission_lock=None):
    from contextlib import nullcontext
    from uuid import uuid4
    from .datasets import now_iso
    from .observation_jobs import init_observations
    store = ObservationStore(root)
    files = store.safe_export_files(identifier)
    size=sum(path.stat().st_size for path in files)
    export_id = str(uuid4())
    required = size+len(files)*2048
    # A short, durable lease reserves the whole not-yet-written ZIP alongside
    # both queues. The copy holds no SQLite transaction or manager mutex, so
    # worker progress and cancellation remain responsive on slow storage.
    with admission_lock if admission_lock is not None else nullcontext():
        init_observations(root)
        with _connection(root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            check_reservations(root,extra_estimates=[{"total_bytes":required,
                              "estimated_temporary_bytes":0}],connection=connection)
            connection.execute("INSERT INTO observation_exports VALUES (?,?,?)", (export_id,required,now_iso()))
            connection.commit()
    output = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f"observation-export-{identifier}-",suffix=".zip",dir=root,delete=False) as stream:
            output=Path(stream.name)
        with zipfile.ZipFile(output,"w",compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
            for path in files:
                archive.write(path,arcname=path.relative_to(store.path(identifier)).as_posix())
    except BaseException:
        if output is not None:
            output.unlink(missing_ok=True)
        raise
    finally:
        # Once closed, physical ZIP bytes are reflected by disk_usage. On
        # failure, delete only this function's owned temporary file first.
        with admission_lock if admission_lock is not None else nullcontext():
            with _connection(root) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("DELETE FROM observation_exports WHERE export_id=?", (export_id,))
                connection.commit()
    return output


@router.get("/datasets/{identifier}/export")
def export(identifier:str,request:Request):
    invoke(_jobs(request).get_job,identifier)
    with _processing_lock:
        output=invoke(_archive,manager(request).root,identifier,admission_lock=manager(request)._mutex)
    return FileResponse(output,media_type="application/zip",filename=f"sam-coherent-observation-{identifier}.zip",
                        background=BackgroundTask(output.unlink,missing_ok=True))
