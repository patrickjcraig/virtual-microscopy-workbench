"""Actual source-backed observation jobs, shared scheduling and historical API."""
from copy import deepcopy
import hashlib
from io import BytesIO
from pathlib import Path
import shutil
import sqlite3
import threading
import time
from uuid import uuid4
import zipfile

from fastapi.testclient import TestClient
import numpy as np
import pytest
import zarr

from virtual_microscopy import observation_jobs as jobs
from virtual_microscopy.observation_datasets import ObservationStore
from virtual_microscopy.observation_plan import plan_observation
from virtual_microscopy.observation_processing import observation_view
from virtual_microscopy.observation_schemas import ObservationRequest


@pytest.fixture(scope="module")
def source_template(tmp_path_factory):
    from virtual_microscopy.causal_sam import prepare_causal_sam, iter_causal_sam_rows
    from virtual_microscopy.causal_datasets import CausalSamDatasetStore
    root=tmp_path_factory.mktemp("observation-workflow-source")
    body={"kind":"sam_causal_rf_volume","twin":{"schema_version":1,"name":"Asymmetric silicon source",
        "size_mm":[4.,3.,.1],"objects":[{"id":"slab","name":"Silicon","shape":"box","material":"silicon",
            "role":"structure","center_mm":[1.,1.5,.05],"size_mm":[2.,3.,.1]}]},
        "acquisition":{"scan_nx":16,"scan_ny":16,"record_duration_us":.2,"sample_rate_mhz":400,
                       "center_frequency_mhz":50,"precision_bits":128,"absolute_tolerance":1e-7}}
    prepared=prepare_causal_sam(body)
    identifier=str(uuid4())
    store=CausalSamDatasetStore(root)
    store.create(identifier,prepared.request.model_dump(mode="json",exclude_none=True),prepared.estimate)
    store.initialize_arrays(identifier,prepared)
    for item in iter_causal_sam_rows(prepared):
        store.write_row(identifier,*item)
    store.complete(identifier)
    prepared.close()
    return root,identifier


@pytest.fixture
def case(tmp_path,source_template):
    root,identifier=source_template
    shutil.copytree(root/identifier,tmp_path/identifier)
    body=ObservationRequest(source_dataset_id=identifier).model_dump(mode="json")
    return tmp_path,identifier,body


def snapshot(path):
    return {p.relative_to(path).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob("*") if p.is_file()}


def staged_manager(root):
    # Unit lifecycle tests own a temporary root and explicitly run the worker
    # entrypoint in this process so failures/cancellation can be injected.
    manager=jobs.ObservationVolumeManager(root)
    jobs.init_observations(root)
    manager._started=True
    return manager


def run_one(manager):
    identifier=jobs._claim(manager.root)
    assert identifier
    jobs._run(manager.root,identifier,threading.Event())
    return manager.observations.get_job(identifier)


def test_cancel_committed_row_resume_skips_it_and_matches_uninterrupted(case,monkeypatch):
    from virtual_microscopy import observation_math
    root,source_id,body=case
    before=snapshot(root/source_id)
    manager=staged_manager(root)
    job=manager.observations.submit(body)
    original=ObservationStore.write_row
    def cancel_after_first(self,identifier,y,result,source_rows,**kwargs):
        m=original(self,identifier,y,result,source_rows,**kwargs)
        if y==0:
            manager.observations.cancel(identifier)
        return m
    monkeypatch.setattr(ObservationStore,"write_row",cancel_after_first)
    cancelled=run_one(manager)
    assert cancelled["status"]=="cancelled" and cancelled["completed_rows"]==1
    saved=ObservationStore(root).manifest(job["id"])["completed_chunks"]["0"]
    monkeypatch.setattr(ObservationStore,"write_row",original)
    original_observe=observation_math.observe_row
    calls=[]
    def counted(*args,**kwargs):
        calls.append(1)
        return original_observe(*args,**kwargs)
    monkeypatch.setattr(observation_math,"observe_row",counted)
    manager.observations.resume(job["id"])
    assert run_one(manager)["status"]=="completed"
    assert len(calls)==13
    assert ObservationStore(root).manifest(job["id"])["completed_chunks"]["0"]==saved
    other=manager.observations.submit(body)
    assert run_one(manager)["status"]=="completed"
    first=zarr.open_group(str(root/job["id"]/"data.zarr"),mode="r")
    second=zarr.open_group(str(root/other["id"]/"data.zarr"),mode="r")
    assert all(first[key][:].tobytes()==second[key][:].tobytes() for key in first.array_keys())
    assert snapshot(root/source_id)==before


def test_cancel_inside_temporal_block_publishes_no_row(case,monkeypatch):
    from virtual_microscopy import observation_math
    root,_,body=case
    manager=staged_manager(root)
    job=manager.observations.submit(body)
    original=observation_math.observe_row
    def cancel_during(*args,**kwargs):
        callback=kwargs["cancelled"]
        checks=0
        def trigger():
            nonlocal checks
            checks+=1
            if checks==3:
                manager.observations.cancel(job["id"])
            return callback()
        return original(*args,**{**kwargs,"cancelled":trigger})
    monkeypatch.setattr(observation_math,"observe_row",cancel_during)
    value=run_one(manager)
    assert value["state"]=="cancelled" and value["completed_rows"]==0
    assert not ObservationStore(root).manifest(job["id"])["completed_chunks"]


def test_cancel_accepted_during_final_source_verification_wins(case,monkeypatch):
    from virtual_microscopy import observation_plan
    root,_,body=case
    manager=staged_manager(root)
    job=manager.observations.submit(body)
    original=observation_plan.source_context
    def intercepted(estimate,root,**kwargs):
        value=original(estimate,root,**kwargs)
        m=ObservationStore(root).manifest(job["id"])
        if kwargs.get("verify") and m["completed_rows"]==m["total_rows"]:
            manager.observations.cancel(job["id"])
        return value
    monkeypatch.setattr(observation_plan,"source_context",intercepted)
    result=run_one(manager)
    assert result["state"]=="cancelled" and result["completed_rows"]==14
    assert not ObservationStore(root).manifest(job["id"])["complete"]


def test_single_pump_interleaves_real_claims_and_retains_legacy_batch_hold(case,monkeypatch):
    root,_,body=case
    manager=staged_manager(root)
    a,b=(manager.observations.submit({**body,"name":name}) for name in ("A","B"))
    legacy_id,legacy_second,held_id=(str(uuid4()) for _ in range(3))
    with jobs.legacy._connection(root) as con:
        stamp=jobs.now_iso()
        for index,identifier in enumerate((legacy_id,legacy_second,held_id)):
            created=f"2020-01-01T00:00:0{index}Z"
            con.execute("INSERT INTO jobs VALUES (?,'queued','legacy',?,?,0,1,NULL,'sam_rf_volume')",(identifier,created,stamp))
        batch_id=str(uuid4())
        con.execute("INSERT INTO batches VALUES (?,?,?,?,?,?,'paused')",(batch_id,str(uuid4()),'held','{}',stamp,stamp))
        con.execute("INSERT INTO batch_cases VALUES (?,?,0,?,'{}','held')",(str(uuid4()),batch_id,held_id))
        con.commit()
    stop=threading.Event()
    observed=[]
    active=0
    def runner(root,identifier,event):
        nonlocal active
        active+=1
        assert active==1
        with jobs.legacy._connection(root) as con:
            running=con.execute("SELECT COUNT(*) FROM jobs WHERE state='running'").fetchone()[0]
            running+=con.execute("SELECT COUNT(*) FROM observation_jobs WHERE state='running'").fetchone()[0]
        assert running==1
        observed.append(identifier)
        (jobs.legacy._update_job if identifier in (legacy_id,legacy_second) else jobs._update)(root,identifier,"completed",1)
        active-=1
        if len(observed)==4:
            event.set()
    monkeypatch.setattr(jobs.legacy,"_run_job",lambda *args:runner(*args))
    monkeypatch.setattr(jobs,"_run",lambda *args:runner(*args))
    jobs._pump(str(root),stop)
    assert observed==[legacy_id,a["id"],legacy_second,b["id"]]
    with jobs.legacy._connection(root) as con:
        assert con.execute("SELECT state FROM jobs WHERE job_id=?",(held_id,)).fetchone()[0]=='queued'


def test_combined_reservations_count_both_queues_without_double_count(case,monkeypatch):
    from virtual_microscopy import batch_jobs
    root,_,body=case
    manager=staged_manager(root)
    observation=manager.observations.submit(body)
    old_id=str(uuid4())
    old={"total_rows":4,"completed_rows":1,"complete":False,"estimate":{"total_bytes":1000,"estimated_temporary_bytes":300}}
    with jobs.legacy._connection(root) as con:
        stamp=jobs.now_iso()
        con.execute("INSERT INTO jobs VALUES (?,'queued','legacy',?,?,1,4,NULL,'sam_rf_volume')",(old_id,stamp,stamp))
        con.commit()
    original=batch_jobs.DatasetStore.manifest
    monkeypatch.setattr(batch_jobs.DatasetStore,"manifest",lambda self,identifier:deepcopy(old) if identifier==old_id else original(self,identifier))
    seen=[]
    monkeypatch.setattr(batch_jobs,"check_disk_space",lambda root,need:(seen.append(need) or {"required_disk_bytes":need}))
    output=ObservationStore(root).manifest(observation["id"])
    value=batch_jobs.check_reservations(root,replacing={observation["id"]:output})
    assert value["pending_output_bytes"]==output["estimate"]["total_bytes"]+750
    assert value["maximum_temporary_bytes"]==300
    assert seen==[value["pending_output_bytes"]+300]


def test_failed_sql_publication_retains_unpublished_owned_manifest(case,monkeypatch):
    from virtual_microscopy import batch_jobs
    root,_,body=case
    manager=staged_manager(root)
    original=batch_jobs.check_reservations
    def fail(root,**kwargs):
        staged=any(p.parent.name!=body["source_dataset_id"] for p in root.glob("*/manifest.json"))
        if kwargs.get("connection") is not None and staged:
            raise sqlite3.OperationalError("injected publication failure")
        return original(root,**kwargs)
    monkeypatch.setattr(batch_jobs,"check_reservations",fail)
    with pytest.raises(sqlite3.OperationalError):
        manager.observations.submit(body)
    assert manager.observations.list_jobs()==[]
    manifests=[p for p in root.glob("*/manifest.json") if p.parent.name!=body["source_dataset_id"]]
    assert len(manifests)==1
    m=ObservationStore(root).manifest(manifests[0].parent.name)
    assert m["state"]=="failed" and not m["complete"]


def wait(client,identifier):
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        job=client.get(f"/api/v2/observations/jobs/{identifier}").json()
        if job["status"] in ("completed","failed","cancelled","interrupted"):
            return job
        time.sleep(.05)
    raise AssertionError(job)


def test_export_preserves_pending_reservations_before_allocating_zip(case,monkeypatch):
    from virtual_microscopy import observation_api,batch_jobs
    root,source_id,_=case
    staged_manager(root)
    identifier=str(uuid4())
    path=root/source_id/'manifest.json'
    size=path.stat().st_size
    pending={"complete":False,"completed_rows":0,"total_rows":1,
             "estimate":{"total_bytes":1024**2,"estimated_temporary_bytes":0}}
    with jobs.legacy._connection(root) as con:
        stamp=jobs.now_iso()
        con.execute("INSERT INTO jobs VALUES (?,'queued','legacy',?,?,0,1,NULL,'sam_rf_volume')",(identifier,stamp,stamp))
        con.commit()
    monkeypatch.setattr(ObservationStore,'safe_export_files',lambda self,id:[path])
    monkeypatch.setattr(batch_jobs.DatasetStore,'manifest',lambda self,id:pending)
    required=[]
    def disk(root,need):
        required.append(need)
        if need>1024**2+size:
            raise OSError('ZIP would consume reserved pending outputs')
        return {'required_disk_bytes':need}
    monkeypatch.setattr(batch_jobs,'check_disk_space',disk)
    with pytest.raises(OSError,match='reserved pending'):
        observation_api._archive(root,source_id)
    assert required==[1024**2+size+2048]
    assert not list(root.glob('observation-export-*.zip'))


def test_real_spawn_api_views_exports_offline_and_kind_isolation(case,monkeypatch):
    from virtual_microscopy.server import app
    root,source_id,body=case
    before=snapshot(root/source_id)
    monkeypatch.setenv("VM_DATA_ROOT",str(root))
    with TestClient(app) as client:
        estimate=client.post("/api/v2/observations/estimate",json=body)
        assert estimate.status_code==200,estimate.text
        e=estimate.json()
        assert e["shape"]==[14,14,81] and e["source_shape"]==[16,16,81]
        assert "source_manifest" not in e
        assert e["extent_mm"]==[.25,3.75,.1875,2.8125]
        for bad in ({**body,"focus_mm":1},{**body,"operator":"blur"},{**body,"absolute_tolerance":1e-12}):
            assert client.post("/api/v2/observations/estimate",json=bad).status_code==422
        response=client.post("/api/v2/observations/jobs",json=body)
        assert response.status_code==202,response.text
        identifier=response.json()["id"]
        done=wait(client,identifier)
        assert done["status"]=="completed",done
        manifest=client.get(f"/api/v2/observations/datasets/{identifier}").json()
        assert manifest["complete"] and manifest["source_dataset_id"]==source_id
        catalog=client.get("/api/v2/observations/datasets").json()["datasets"]
        assert [m["id"] for m in catalog]==[identifier]
        assert client.get("/api/v2/datasets").json()["datasets"]==[]
        saved=snapshot(root/identifier)
        for product in ("rf","imaginary","envelope"):
            response=client.get(f"/api/v2/observations/datasets/{identifier}/view?product={product}&x_index=6&y_index=4&time_index=17&gate_start_us=.04&gate_end_us=.08&gate_mode=rms_rf")
            assert response.status_code==200,response.text
            v=response.json()
            assert v["cursor"]["source_x_index"]==7 and v["cursor"]["source_y_index"]==5
            assert v["gate"]["sample_count"]==17
            assert len(v["bound_maps"])==5 and len(v["xt"]["image"])==81
            g=zarr.open_group(str(root/identifier/"data.zarr"),mode="r")
            assert np.array_equal(v["xy"]["image"],g[product][:,:,17])
            assert np.array_equal(v["yt"]["image"],g[product][:,6,:].T)
            assert np.array_equal(v["ascan"][product],g[product][4,6,:])
        assert client.get(f"/api/v2/observations/datasets/{identifier}/view?x_index=14").status_code==422
        assert client.get(f"/api/v2/observations/datasets/{identifier}/view?gate_end_us=4").status_code==422
        assert client.post("/api/v2/observations/estimate",json={**body,"source_dataset_id":identifier}).status_code==422
        assert client.get(f"/api/v2/causal-datasets/{identifier}/view").status_code in (404,422)
        assert client.post(f"/api/v2/observations/jobs/{identifier}/cancel").status_code==422
        assert client.post(f"/api/v2/observations/jobs/{identifier}/resume").status_code==422
        # Move only the owned fixture inside its verified temporary test root.
        moved=root/"unavailable-source"
        assert moved.resolve().parent==root.resolve() and (root/source_id).resolve().parent==root.resolve()
        (root/source_id).rename(moved)
        assert snapshot(moved)==before
        assert client.get(f"/api/v2/observations/datasets/{identifier}/view").status_code==200
        export=client.get(f"/api/v2/observations/datasets/{identifier}/export")
        assert export.status_code==200,export.text
        with zipfile.ZipFile(BytesIO(export.content)) as archive:
            assert {name:hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()}==saved
        assert snapshot(root/identifier)==saved
        assert client.get("/api/v2/jobs").json()["jobs"]==[]
