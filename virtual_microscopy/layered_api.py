"""Bounded standalone layered calculations and immutable report endpoints."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from .layered_reports import LayeredReportStore, bounded_payload, layered_report_csv
from .layered_schemas import LayeredAnalysisRequest, LayeredColumnRequest
from .volume_api import _processing_lock, manager


router = APIRouter(prefix="/api/v2/layered-acoustics", tags=["Layered acoustics"])


def _store(request):
    return LayeredReportStore(manager(request).root)


def _invoke(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(404, "The layered acoustic report is unavailable.") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(507, "Layered acoustic storage could not be read or written. Check local disk space and access.") from exc


@router.post("/column")
def column(body: LayeredColumnRequest):
    from .layered_analysis import extract_layered_column
    with _processing_lock:
        return _invoke(extract_layered_column, body)


@router.post("/estimate")
def estimate(body: LayeredAnalysisRequest):
    from .layered_analysis import estimate_layered
    with _processing_lock:
        return _invoke(estimate_layered, body)


@router.post("/reports", status_code=201)
def create(body: LayeredAnalysisRequest, request: Request):
    with _processing_lock:
        return _invoke(_store(request).create, body)


@router.get("/reports")
def listing(request: Request):
    with _processing_lock:
        return {"reports": _invoke(_store(request).list)}


@router.get("/reports/{report_id}")
def read(report_id: str, request: Request):
    with _processing_lock:
        return _invoke(_store(request).read, report_id)


@router.get("/reports/{report_id}/export")
def export(report_id: str, request: Request, format: Literal["json", "csv"] = "json"):
    with _processing_lock:
        report = _invoke(_store(request).read, report_id)
        payload = _invoke(bounded_payload, report) if format == "json" else _invoke(layered_report_csv, report).encode("utf-8")
    return Response(payload, media_type="application/json" if format == "json" else "text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="layered-acoustics-{report["id"]}.{format}"'})
