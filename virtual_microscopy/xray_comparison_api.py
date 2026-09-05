"""Saved X-ray comparison endpoints using the shared processing memory lock."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from .datasets import canonical_json
from .volume_api import _processing_lock, manager
from .xray_comparison_schemas import XrayComparisonRequest
from .xray_comparisons import XrayComparisonCompatibilityError, XrayComparisonStore, xray_comparison_csv

router = APIRouter(prefix="/api/v2", tags=["Saved X-ray comparisons"])


def _store(request):
    return XrayComparisonStore(manager(request).root)


def _invoke(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except XrayComparisonCompatibilityError as exc:
        raise HTTPException(422, {"message": str(exc), "issues": exc.issues}) from exc
    except KeyError as exc:
        raise HTTPException(404, "The X-ray comparison or a source dataset is unavailable.") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(507, "X-ray comparison storage could not be read or written. Check local disk space and access.") from exc


@router.post("/xray-comparisons", status_code=201)
def create(body: XrayComparisonRequest, request: Request):
    with _processing_lock:
        return _invoke(_store(request).create, body)


@router.get("/xray-comparisons")
def listing(request: Request):
    with _processing_lock:
        return {"comparisons": _invoke(_store(request).list)}


@router.get("/xray-comparisons/{comparison_id}")
def read(comparison_id: str, request: Request):
    with _processing_lock:
        return _invoke(_store(request).read, comparison_id)


@router.get("/xray-comparisons/{comparison_id}/view")
def view(comparison_id: str, request: Request, view_index: int | None = Query(None, ge=0),
         detector_row: int | None = Query(None, ge=0), detector_col: int | None = Query(None, ge=0)):
    with _processing_lock:
        return _invoke(_store(request).view, comparison_id, view_index=view_index,
                       detector_row=detector_row, detector_col=detector_col)


@router.get("/xray-comparisons/{comparison_id}/export")
def export(comparison_id: str, request: Request, format: Literal["json", "csv"] = "json"):
    with _processing_lock:
        report = _invoke(_store(request).read, comparison_id)
        payload = canonical_json(report) if format == "json" else xray_comparison_csv(report).encode("utf-8")
    return Response(payload, media_type="application/json" if format == "json" else "text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="xray-comparison-{report["id"]}.{format}"'})
