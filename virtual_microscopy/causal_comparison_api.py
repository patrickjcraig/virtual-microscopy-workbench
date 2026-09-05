"""Immutable causal comparisons with serialized bounded encoding and inspection."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from .causal_comparison_schemas import CausalComparisonRequest
from .causal_comparisons import CausalComparisonCompatibilityError
from .causal_comparison_store import CausalComparisonStore, bounded_payload, causal_comparison_csv
from .volume_api import _processing_lock, manager

router = APIRouter(prefix="/api/v2/causal-comparisons", tags=["Saved causal comparisons"])


def _store(request):
    return CausalComparisonStore(manager(request).root)


def _invoke(function,*args,**kwargs):
    try:
        return function(*args,**kwargs)
    except CausalComparisonCompatibilityError as exc:
        raise HTTPException(422,{"message":str(exc),"issues":exc.issues}) from exc
    except KeyError as exc:
        raise HTTPException(404,"The causal comparison or a required source is unavailable. Its frozen initial view may still be available.") from exc
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    except OSError as exc:
        raise HTTPException(507,"Causal comparison storage could not be read or written.") from exc


def _json(value,status=200):
    return Response(_invoke(bounded_payload,value),status_code=status,media_type="application/json")


@router.post("",status_code=201)
def create(body:CausalComparisonRequest,request:Request):
    with _processing_lock:
        return _json(_invoke(_store(request).create,body),201)


@router.get("")
def catalog(request:Request,limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0,le=9999)):
    with _processing_lock:
        page = _invoke(_store(request).list,limit=limit,offset=offset)
        next_offset = offset+len(page) if len(page)==limit and offset+len(page)<10000 else None
        return _json({"comparisons":page,"limit":limit,"offset":offset,"order":"id_desc","next_offset":next_offset})


@router.get("/{identifier}")
def read(identifier:str,request:Request):
    with _processing_lock:
        return _json(_invoke(_store(request).read,identifier))


@router.get("/{identifier}/view")
def view(identifier:str,request:Request,x_index:int|None=Query(None,ge=0,le=63),
         y_index:int|None=Query(None,ge=0,le=63),time_index:int|None=Query(None,ge=0,le=2048)):
    indices = {key:value for key,value in (("x_index",x_index),("y_index",y_index),("time_index",time_index)) if value is not None}
    with _processing_lock:
        return _json(_invoke(_store(request).view,identifier,**indices))


@router.get("/{identifier}/export")
def export(identifier:str,request:Request,format:Literal["json","csv"]="json"):
    with _processing_lock:
        report = _invoke(_store(request).read,identifier)
        payload = _invoke(bounded_payload,report) if format=="json" else _invoke(causal_comparison_csv,report).encode("utf-8")
    return Response(payload,media_type="application/json" if format=="json" else "text/csv; charset=utf-8",
                    headers={"Content-Disposition":f'attachment; filename="causal-comparison-{report["id"]}.{format}"'})
