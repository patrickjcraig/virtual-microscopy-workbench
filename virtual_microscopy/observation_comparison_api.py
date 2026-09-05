"""Immutable saved-observation comparison routes."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from .observation_comparison_schemas import ObservationComparisonRequest
from .observation_comparisons import ObservationComparisonCompatibilityError
from .observation_comparison_store import ObservationComparisonStore, bounded_payload, observation_comparison_csv
from .volume_api import _processing_lock, manager

router = APIRouter(prefix="/api/v2/observation-comparisons", tags=["Saved observation comparisons"])


def _store(request):
    return ObservationComparisonStore(manager(request).root)


def _invoke(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except ObservationComparisonCompatibilityError as exc:
        raise HTTPException(422, {"message":str(exc), "issues":exc.issues}) from exc
    except KeyError as exc:
        raise HTTPException(404, "The observation comparison or a required source is unavailable. Its frozen initial view may still be available.") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(507, "Observation comparison storage could not be read or written.") from exc


def _json(value, status=200):
    return Response(_invoke(bounded_payload, value), status_code=status, media_type="application/json")


@router.post("", status_code=201)
def create(body:ObservationComparisonRequest, request:Request):
    with _processing_lock:
        return _json(_invoke(_store(request).create, body), 201)


@router.get("")
def catalog(request:Request, limit:int=Query(50, ge=1, le=100), offset:int=Query(0, ge=0, le=9999)):
    with _processing_lock:
        page = _invoke(_store(request).list, limit=limit, offset=offset)
        next_offset = offset+len(page) if len(page)==limit and offset+len(page)<10000 else None
        return _json({"comparisons":page, "limit":limit, "offset":offset, "order":"id_desc", "next_offset":next_offset})


@router.get("/{identifier}")
def read(identifier:str, request:Request):
    with _processing_lock:
        return _json(_invoke(_store(request).read, identifier))


@router.get("/{identifier}/view")
def view(identifier:str, request:Request, x_index:int|None=Query(None, ge=0, le=61),
         y_index:int|None=Query(None, ge=0, le=61), time_index:int|None=Query(None, ge=0, le=2048)):
    indices = {key:value for key,value in (("x_index",x_index),("y_index",y_index),("time_index",time_index)) if value is not None}
    with _processing_lock:
        return _json(_invoke(_store(request).view, identifier, **indices))


@router.get("/{identifier}/export")
def export(identifier:str, request:Request, format:Literal["json","csv"]="json"):
    with _processing_lock:
        report = _invoke(_store(request).read, identifier)
        payload = _invoke(bounded_payload, report) if format=="json" else _invoke(observation_comparison_csv, report).encode("utf-8")
    return Response(payload, media_type="application/json" if format=="json" else "text/csv; charset=utf-8",
        headers={"Content-Disposition":f'attachment; filename="observation-comparison-{report["id"]}.{format}"'})
