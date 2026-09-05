"""Acquisition recipe staging and reviewed batches on the existing local worker."""
from fastapi import APIRouter, Request
from fastapi.responses import Response

from .datasets import canonical_json
from .recipe_schemas import BatchSubmission, CaseProposal, RecipeCreate, RecipeFromDataset
from .recipes import RecipeStore, build_case_plan
from .volume_api import _processing_lock, invoke, manager

router = APIRouter(prefix="/api/v2", tags=["Acquisition recipes and batches"])


def _store(request):
    return RecipeStore(manager(request).root)


@router.post("/recipes", status_code=201)
def create_recipe(body: RecipeCreate, request: Request):
    return invoke(_store(request).create, body)


@router.get("/recipes")
def list_recipes(request: Request):
    return {"recipes": invoke(_store(request).list)}


@router.post("/recipes/import", status_code=201)
def import_recipe(body: dict, request: Request):
    return invoke(_store(request).import_record, body)


@router.post("/recipes/from-dataset", status_code=201)
def recipe_from_dataset(body: RecipeFromDataset, request: Request):
    with _processing_lock:
        return invoke(_store(request).from_dataset, body)


@router.get("/recipes/{recipe_id}")
def get_recipe(recipe_id: str, request: Request):
    return invoke(_store(request).get, recipe_id)


@router.get("/recipes/{recipe_id}/export")
def export_recipe(recipe_id: str, request: Request):
    record = invoke(_store(request).get, recipe_id)
    instrument = "xray" if record["kind"] == "xray_acquisition_recipe" else "sam"
    return Response(canonical_json(record), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{instrument}-recipe-{record["recipe_id"]}.json"'})


@router.post("/cases/preview")
def preview_cases(body: CaseProposal, request: Request):
    worker = manager(request)
    with _processing_lock:
        plan = invoke(build_case_plan, worker.root, body)
        admission = invoke(worker.estimate_batch, plan)
    return plan | admission | {"cases": plan["cases"]}


@router.post("/batches", status_code=202)
def submit_batch(body: BatchSubmission, request: Request):
    from fastapi import HTTPException
    from .server import _compute_lock
    worker = manager(request)
    proposal = CaseProposal.model_validate(body.model_dump(exclude={"idempotency_key"}))
    existing = invoke(worker.get_batch_by_key, body.idempotency_key)
    if existing is not None:
        authored = proposal.model_dump(mode="json", exclude_none=True)
        if existing["proposal"] != authored:
            raise HTTPException(422, "Idempotency key already belongs to a different case proposal.")
        return existing
    if not _compute_lock.acquire(blocking=False):
        raise HTTPException(409, "A preview is running. Wait for it to finish before submitting the batch.")
    try:
        with _processing_lock:
            plan = invoke(build_case_plan, worker.root, proposal)
            return invoke(worker.submit_batch, plan, body.idempotency_key)
    finally:
        _compute_lock.release()


@router.get("/batches")
def list_batches(request: Request):
    return {"batches": invoke(manager(request).list_batches)}


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, request: Request):
    return invoke(manager(request).get_batch, batch_id)


@router.post("/batches/{batch_id}/cancel")
def cancel_batch(batch_id: str, request: Request):
    return invoke(manager(request).cancel_batch, batch_id)


@router.post("/batches/{batch_id}/resume", status_code=202)
def resume_batch(batch_id: str, request: Request):
    from fastapi import HTTPException
    from .server import _compute_lock
    if not _compute_lock.acquire(blocking=False):
        raise HTTPException(409, "A preview is running. Wait for it to finish before resuming the batch.")
    try:
        return invoke(manager(request).resume_batch, batch_id)
    finally:
        _compute_lock.release()
