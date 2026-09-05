"""Loopback-only workbench API; deterministic simulation snapshots and static UI."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .schemas import Twin, SimulationRequest, HBMUpdateRequest
from .inspection import HBMMicrostructureRequest, HBMSectionRequest, material_section, microstructure_details

ROOT = Path(__file__).resolve().parents[1]
_compute_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app):
    from .volume_jobs import VolumeJobManager
    jobs = VolumeJobManager()
    jobs.start()
    app.state.volume_jobs = jobs
    try:
        yield
    finally:
        jobs.close()
        app.state.volume_jobs = None


app = FastAPI(title="Virtual microscopy", version=__version__, lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Pydantic validator contexts may contain exception objects; expose readable
    # field paths without echoing an entire imported specimen in an error.
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]}
        for err in exc.errors()
    ]})


@app.get("/api/health")
def health():
    return {"status": "ok", "version": __version__}


@app.get("/api/examples")
def examples():
    result = []
    for path in sorted((ROOT / "examples").glob("*.json")):
        twin = Twin.model_validate_json(path.read_text(encoding="utf-8"))
        result.append({"id": path.stem, "name": twin.name, "description": twin.description, "twin": twin.model_dump(mode="json", exclude_none=True)})
    return result


@app.get("/api/materials")
def materials():
    from .materials import MATERIALS
    return list(MATERIALS.values())


@app.get("/api/twin-schema")
def twin_schema():
    return Twin.model_json_schema()


@app.post("/api/validate")
def validate(twin: Twin):
    warnings = []
    if not any(obj.role == "structure" for obj in twin.objects):
        warnings.append("The twin contains no objects marked as structure.")
    return {"valid": True, "twin": twin.model_dump(mode="json", exclude_none=True), "warnings": warnings}


@app.post("/api/hbm/compose")
def hbm_compose(request: HBMUpdateRequest):
    from .hbm import compose_hbm
    try:
        twin = compose_hbm(request.twin.model_dump(mode="json", exclude_none=True), request.assembly_id,
                           request.parameters.model_dump(mode="json", exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"twin": twin, **microstructure_details(Twin.model_validate(twin), request.assembly_id),
            "warnings": ["HBM dimensions and layer construction are modeling assumptions."]}


@app.post("/api/hbm/microstructure")
def hbm_microstructure(request: HBMMicrostructureRequest):
    try:
        return microstructure_details(request.twin, request.assembly_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/hbm/section")
def hbm_section(request: HBMSectionRequest):
    try:
        return material_section(request)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/reference-image")
def reference_image():
    # This single user-supplied image remains local and ignored by Git. A fresh
    # clone receives a normal unavailable response; no external fetch is made.
    expected = "e2b1274b5593236fff9c9a3183cd6f73808f890ab2acca32e267c057ec8d12d9"
    path = ROOT / "artifacts" / "reference-inputs" / f"h100-cross-section-{expected[:12]}.png"
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise HTTPException(404, "The original reference image is not installed in this local workspace.")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, no-store",
                                                             "X-Reference-SHA256": expected})


def run(request: SimulationRequest, probe_only=False):
    from .physics import simulate, probe
    if not _compute_lock.acquire(blocking=False):
        raise HTTPException(409, "A simulation is already running. Wait for completion and retry.")
    try:
        volume_jobs = getattr(app.state, "volume_jobs", None)
        if volume_jobs is not None and any(job["status"] in ("queued", "running", "cancelling")
                                           for job in volume_jobs.list_jobs()):
            raise HTTPException(409, "A saved volume acquisition is active. Inspect saved datasets now, or wait/cancel before running another preview.")
        twin = request.twin.model_dump(mode="json", exclude_none=True)
        settings = request.settings.model_dump(mode="json", exclude_none=True)
        try:
            result = (probe if probe_only else simulate)(twin, settings)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if probe_only:
            return result
        payload = json.dumps({"twin": twin, "settings": settings}, sort_keys=True, separators=(",", ":"))
        from .materials import MATERIALS
        result.update({
            "run_id": str(uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "input_sha256": hashlib.sha256(payload.encode()).hexdigest(),
            "evidence_status": "Synthetic reduced-order simulation; not experimentally calibrated.",
            "twin": twin,
            "settings": settings,
            "materials": {key: MATERIALS[key] for key in sorted({obj["material"] for obj in twin["objects"]})},
        })
        return result
    finally:
        _compute_lock.release()


@app.post("/api/simulate")
def simulate_api(request: SimulationRequest):
    return run(request)


@app.post("/api/probe")
def probe_api(request: SimulationRequest):
    return run(request, probe_only=True)


from .volume_api import router as volume_router
app.include_router(volume_router)

dist = ROOT / "web" / "dist"
if dist.is_dir():
    app.mount("/", StaticFiles(directory=dist, html=True), name="workbench")
else:
    @app.get("/")
    def frontend_missing():
        return JSONResponse(status_code=503, content={"detail": "Build the frontend: cd web; npm install; npm run build. Then restart the server."})
