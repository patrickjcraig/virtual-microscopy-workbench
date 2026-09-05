"""Immutable saved SAM comparisons, sharing the bounded processing lock."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from .comparison_schemas import SamComparisonRequest
from .comparisons import ComparisonCompatibilityError, ComparisonStore, comparison_csv
from .datasets import canonical_json
from .volume_api import _processing_lock, manager

router = APIRouter(prefix="/api/v2", tags=["Saved SAM comparisons"])


def _store(request):
    return ComparisonStore(manager(request).root)


def _invoke(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except ComparisonCompatibilityError as exc:
        raise HTTPException(422, {"message": str(exc), "issues": exc.issues}) from exc
    except KeyError as exc:
        raise HTTPException(404, "The comparison or one of its source datasets is unavailable.") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(507, "Comparison storage could not be read or written. Check local disk space and access.") from exc


@router.post("/comparisons", status_code=201)
def create_comparison(body: SamComparisonRequest, request: Request):
    with _processing_lock:
        return _invoke(_store(request).create, body)


@router.get("/comparisons")
def list_comparisons(request: Request):
    with _processing_lock:
        return {"comparisons": _invoke(_store(request).list)}


@router.get("/comparisons/{comparison_id}")
def get_comparison(comparison_id: str, request: Request):
    with _processing_lock:
        return _invoke(_store(request).read, comparison_id)


@router.get("/comparisons/{comparison_id}/view")
def view_comparison(comparison_id: str, request: Request,
                    x_index: int | None = Query(None, ge=0),
                    y_index: int | None = Query(None, ge=0),
                    time_index: int | None = Query(None, ge=0)):
    with _processing_lock:
        return _invoke(_store(request).view, comparison_id, x_index=x_index, y_index=y_index, time_index=time_index)


@router.get("/comparisons/{comparison_id}/export")
def export_comparison(comparison_id: str, request: Request, format: Literal["json", "csv"] = "json"):
    with _processing_lock:
        report = _invoke(_store(request).read, comparison_id)
        payload = canonical_json(report) if format == "json" else comparison_csv(report).encode("utf-8")
    media_type = "application/json" if format == "json" else "text/csv; charset=utf-8"
    return Response(payload, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="sam-comparison-{report["id"]}.{format}"'})
