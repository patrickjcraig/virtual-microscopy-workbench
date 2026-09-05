"""Separate immutable SLS comparison routes; no acquisition or forward jobs."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from .sls_comparison_schemas import SLSComparisonRequest
from .sls_comparison_store import SLSComparisonStore, bounded_payload, sls_comparison_csv
from .volume_api import _processing_lock, manager

router = APIRouter(prefix="/api/v2/sls-comparisons", tags=["Saved SLS comparisons"])


def _store(request):
    return SLSComparisonStore(manager(request).root)


def _invoke(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(404, "The SLS comparison or required original source report is unavailable.") from exc
    except ValueError as exc:
        detail = {"message": str(exc), "issues": exc.issues} if hasattr(exc, "issues") else str(exc)
        raise HTTPException(422, detail) from exc
    except OSError as exc:
        raise HTTPException(507, "SLS comparison storage could not be read or written. Check disk space and access.") from exc


def _json(value, status=200):
    return Response(_invoke(bounded_payload, value), status_code=status, media_type="application/json")


@router.post("/estimate")
def estimate(body: SLSComparisonRequest, request: Request):
    from .sls_comparisons import estimate_sls_comparison
    with _processing_lock:
        return _json(_invoke(estimate_sls_comparison, manager(request).root, body))


@router.post("/reports", status_code=201)
def create(body: SLSComparisonRequest, request: Request):
    with _processing_lock:
        return _json(_invoke(_store(request).create, body), 201)


@router.get("/reports")
def listing(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=9999)):
    with _processing_lock:
        page = _invoke(_store(request).list, limit=limit, offset=offset)
        next_offset = offset+len(page) if len(page) == limit and offset+len(page) < 10000 else None
        return _json({"reports": page, "limit": limit, "offset": offset, "order": "id_desc", "next_offset": next_offset})


@router.get("/reports/{identifier}")
def read(identifier: str, request: Request):
    with _processing_lock:
        return _json(_invoke(_store(request).read, identifier))


@router.get("/reports/{identifier}/view")
def view(identifier: str, request: Request, frequency_index: int | None = Query(None, ge=0, le=8192),
         time_index: int | None = Query(None, ge=0, le=2048)):
    with _processing_lock:
        return _json(_invoke(_store(request).view, identifier, frequency_index=frequency_index, time_index=time_index))


@router.get("/reports/{identifier}/export")
def export(identifier: str, request: Request, format: Literal["json", "csv"] = "json"):
    with _processing_lock:
        report = _invoke(_store(request).read, identifier)
        payload = _invoke(bounded_payload, report) if format == "json" else _invoke(sls_comparison_csv, report).encode("ascii")
    return Response(payload, media_type="application/json" if format == "json" else "text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="sls-comparison-{report["id"]}.{format}"'})
