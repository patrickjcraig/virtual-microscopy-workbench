"""Material assumptions and saved coverage evidence, without propagation jobs."""
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from .material_assignment_schemas import SLSMaterialAssignmentRequest, MaterialColumnRequest
from .material_assignment_store import MaterialAssignmentStore, bounded_payload
from .volume_api import _processing_lock, manager

router=APIRouter(prefix="/api/v2/material-assignments",tags=["Material assignments"])


def _store(request): return MaterialAssignmentStore(manager(request).root)


def _invoke(function,*args,**kwargs):
    try: return function(*args,**kwargs)
    except KeyError as exc: raise HTTPException(404,"The material assignment, column or required original source is unavailable.") from exc
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    except OSError as exc: raise HTTPException(507,"Material evidence storage could not be read or written. Check disk space and access.") from exc


def _json(value,status=200): return Response(_invoke(bounded_payload,value),status_code=status,media_type="application/json")


def _page(values,key,limit,offset):
    next_offset=offset+len(values) if len(values)==limit and offset+len(values)<10000 else None
    return {key:values,"limit":limit,"offset":offset,"order":"id_desc","next_offset":next_offset}


@router.post("/estimate")
def estimate(body:SLSMaterialAssignmentRequest,request:Request):
    with _processing_lock: return _json(_invoke(_store(request).estimate,body))


@router.post("",status_code=201)
def create(body:SLSMaterialAssignmentRequest,request:Request):
    with _processing_lock: return _json(_invoke(_store(request).create,body),201)


@router.get("")
def listing(request:Request,limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0,le=9999)):
    with _processing_lock: return _json(_page(_invoke(_store(request).list,limit=limit,offset=offset),"assignments",limit,offset))


@router.get("/{identifier}")
def read(identifier:str,request:Request):
    with _processing_lock: return _json(_invoke(_store(request).read,identifier))


@router.get("/{identifier}/export")
def export(identifier:str,request:Request):
    with _processing_lock:
        report=_invoke(_store(request).read,identifier);payload=_invoke(bounded_payload,report)
    return Response(payload,media_type="application/json",headers={"Content-Disposition":f'attachment; filename="material-assignment-{report["id"]}.json"'})


@router.post("/{identifier}/columns",status_code=201)
def create_column(identifier:str,body:MaterialColumnRequest,request:Request):
    with _processing_lock: return _json(_invoke(_store(request).create_column,identifier,body),201)


@router.get("/{identifier}/columns")
def columns(identifier:str,request:Request,limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0,le=9999)):
    with _processing_lock: return _json(_page(_invoke(_store(request).list_columns,identifier,limit=limit,offset=offset),"columns",limit,offset))


@router.get("/{identifier}/columns/{column_id}")
def read_column(identifier:str,column_id:str,request:Request):
    with _processing_lock: return _json(_invoke(_store(request).read_column,identifier,column_id))


@router.get("/{identifier}/columns/{column_id}/export")
def export_column(identifier:str,column_id:str,request:Request):
    with _processing_lock:
        report=_invoke(_store(request).read_column,identifier,column_id);payload=_invoke(bounded_payload,report)
    return Response(payload,media_type="application/json",headers={"Content-Disposition":f'attachment; filename="material-column-{report["id"]}.json"'})
