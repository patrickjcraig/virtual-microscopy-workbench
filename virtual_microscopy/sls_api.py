"""Separate synchronous SLS instrument, immutable reports and lossless exports."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from .sls_reports import SLSReportStore, bounded_payload, sls_report_csv
from .sls_schemas import SLSAnalysisRequest
from .volume_api import _processing_lock, manager

router = APIRouter(prefix="/api/v2/sls-acoustics", tags=["Scalar SLS acoustics"])


def _store(request):
    return SLSReportStore(manager(request).root)


def _invoke(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(404, "The SLS acoustic report is unavailable.") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(507, "SLS acoustic storage could not be read or written. Check local disk space and access.") from exc


def _json(value, status=200):
    return Response(_invoke(bounded_payload, value), status_code=status, media_type="application/json")


@router.post("/estimate")
def estimate(body: SLSAnalysisRequest):
    from .sls_analysis import estimate_sls
    with _processing_lock:
        return _json(_invoke(estimate_sls, body))


@router.post("/reports", status_code=201)
def create(body: SLSAnalysisRequest, request: Request):
    with _processing_lock:
        return _json(_invoke(_store(request).create, body), 201)


@router.get("/reports")
def listing(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=9999)):
    with _processing_lock:
        page = _invoke(_store(request).list, limit=limit, offset=offset)
        next_offset = offset+len(page) if len(page) == limit and offset+len(page) < 10000 else None
        return _json({"reports": page, "limit": limit, "offset": offset, "order": "id_desc", "next_offset": next_offset})


@router.get("/reports/{report_id}")
def read(report_id: str, request: Request):
    with _processing_lock:
        return _json(_invoke(_store(request).read, report_id))


@router.get("/reports/{report_id}/export")
def export(report_id: str, request: Request, format: Literal["json", "csv"] = "json"):
    with _processing_lock:
        report = _invoke(_store(request).read, report_id)
        payload = _invoke(bounded_payload, report) if format == "json" else _invoke(sls_report_csv, report).encode("ascii")
    return Response(payload, media_type="application/json" if format == "json" else "text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="sls-acoustics-{report["id"]}.{format}"'})
