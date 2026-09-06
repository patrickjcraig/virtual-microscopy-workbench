"""Separate synchronous Mixed instrument, immutable reports and lossless exports."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from .mixed_reports import MixedReportStore, bounded_payload, mixed_report_csv
from .mixed_schemas import MixedLayeredAnalysisRequest
from .volume_api import _processing_lock, manager

router = APIRouter(prefix="/api/v2/mixed-acoustics", tags=["Scalar Mixed acoustics"])


def _store(request):
    return MixedReportStore(manager(request).root)


def _invoke(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(404, "The Mixed acoustic report is unavailable.") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(507, "Mixed acoustic storage could not be read or written. Check local disk space and access.") from exc


def _json(value, status=200):
    return Response(_invoke(bounded_payload, value), status_code=status, media_type="application/json")


@router.post("/estimate")
def estimate(body: MixedLayeredAnalysisRequest):
    from .mixed_analysis import estimate_mixed
    with _processing_lock:
        return _json(_invoke(estimate_mixed, body))


@router.post("/reports", status_code=201)
def create(body: MixedLayeredAnalysisRequest, request: Request):
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
        payload = _invoke(bounded_payload, report) if format == "json" else _invoke(mixed_report_csv, report).encode("ascii")
    return Response(payload, media_type="application/json" if format == "json" else "text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="mixed-acoustics-{report["id"]}.{format}"'})
