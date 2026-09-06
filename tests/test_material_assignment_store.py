"""Immutable assignment closure, saved geometry evidence and bounded HTTP IO."""
from copy import deepcopy
import builtins
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
from uuid import uuid4
import weakref

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from virtual_microscopy import material_assignment_store as storage
from virtual_microscopy import material_assignments as core
from virtual_microscopy.sls_reports import SLSReportStore
from tests.test_material_assignments import request, manual, reference, primitive, twin


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    medium={"name":"Nominal water","impedance_mrayl":1.48,"sound_speed_m_s":1480.}
    layer={"name":"Arbitrary source material","thickness_mm":.05,**{k:v for k,v in manual()["origin"].items() if k!="kind"}}
    return SLSReportStore(tmp_path_factory.mktemp("material-source")).create({"name":"Frozen SLS provenance","stack":{"incident":medium,"terminal":medium,"layers":[layer]},
        "spectrum":{"start_mhz":0.,"end_mhz":20.,"samples":3}})


@pytest.fixture
def fixture(tmp_path,source):
    directory=tmp_path/"sls-reports";directory.mkdir()
    (directory/f'{source["id"]}.json').write_bytes(storage.bounded_payload(source))
    return storage.MaterialAssignmentStore(tmp_path),request(bindings=[reference(source,"copper")]),source


def hashes(root): return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def rehash(report):
    report["report_sha256"]=storage.json_measure({k:v for k,v in report.items() if k!="report_sha256"})["sha256"]


def write(store,report):
    path=store._path(report["id"]) if report["kind"]==storage.KIND else store._path(report["assignment_id"],report["id"])
    path.write_bytes(storage.bounded_payload(report))


def client(root):
    from virtual_microscopy.material_assignment_api import router
    app=FastAPI();app.state.volume_jobs=SimpleNamespace(root=root);app.include_router(router)
    return TestClient(app)


def test_actual_reference_assignment_keeps_four_exact_values_and_complete_source_once(fixture):
    store,body,source=fixture;before=hashes(store.root/'sls-reports')
    estimate=store.estimate(body);assert not store.directory.exists()
    report=store.create(body);assert store.read(report['id'])==report
    assert len(report['snapshots'])==2 and report['snapshot_kinds'][report['twin_sha256']]=='twin'
    record=report['bindings'][0];digest=record['origin']['source_snapshot_sha256']
    assert report['snapshots'][digest]==source
    assert set(record['parameters'])==set(storage.PARAMETERS)
    assert record['parameters']=={k:source['stack']['layers'][0][k] for k in storage.PARAMETERS}
    assert 'thickness_mm' not in record['parameters']
    assert record['evidence']=='manual_scalar_longitudinal_assumption'
    assert report['coverage']['missing_material_ids']==['silicon','air']
    assert report['resources']==estimate['resources'] and report['propagation_available'] is False
    assert hashes(store.root/'sls-reports')==before


def test_column_flattened_closure_complete_without_parent_or_source_files(fixture,monkeypatch):
    store,body,_=fixture;parent=store.create(body);column=store.create_column(parent['id'],{'x_mm':2.,'y_mm':1.5})
    assert column['segment_count']==5
    assert len(column['snapshots'])==2 and 'snapshots' not in column['assignment_record'] and 'snapshot_kinds' not in column['assignment_record']
    restored={**column['assignment_record'],'snapshots':column['snapshots'],'snapshot_kinds':column['snapshot_kinds']}
    assert storage.bounded_payload(restored)==storage.bounded_payload(parent)
    assert column['assignment_sha256']==parent['report_sha256']
    assert column['coverage']['missing_material_ids']==['air'] and column['coverage']['ambient_present']
    assert column['segments'][0]['z_start_mm']==0. and column['segments'][-1]['z_end_mm']==2.
    store._path(parent['id']).unlink();shutil.rmtree(store.root/'sls-reports')
    monkeypatch.setattr(core,'inspect_column',lambda *a,**k:pytest.fail('Recomputed historical column'))
    monkeypatch.setattr(storage,'_fingerprints',lambda:pytest.fail('Read current source identity'))
    real_import=builtins.__import__
    forbidden={'material_assignments','material_assignment_schemas','schemas','hbm','column_paths','sls_schemas','sls_acoustics','sls_time','sls_analysis','flint'}
    def guard(name,globals=None,locals=None,fromlist=(),level=0):
        if name.rsplit('.',1)[-1] in forbidden or any(v in forbidden for v in fromlist or ()):pytest.fail(f'Historical current import {name}')
        return real_import(name,globals,locals,fromlist,level)
    monkeypatch.setattr(builtins,'__import__',guard)
    assert store.read_column(parent['id'],column['id'])==column
    assert store.list_columns(parent['id'])[0]['id']==column['id']
    assert json.loads(storage.bounded_payload(column))==column


def test_assignment_history_without_sources_or_current_constructors(fixture,monkeypatch):
    store,body,_=fixture;report=store.create(body)
    shutil.rmtree(store.root/'sls-reports')
    def forbidden(*a,**k):pytest.fail('Historical current producer called')
    monkeypatch.setattr(core,'build_assignment',forbidden);monkeypatch.setattr(core,'estimate_assignment',forbidden)
    monkeypatch.setattr(storage,'_fingerprints',forbidden)
    assert store.read(report['id'])==report and store.list()[0]['id']==report['id']


def test_all_six_hbm_sites_and_every_column_segment_remain(fixture):
    from tools.build_h100_example import h100
    from virtual_microscopy.hbm import compose_hbm
    store,_,_=fixture
    geometry=compose_hbm(h100(),'hbm-6',{'microstructure':{}})
    report=store.create(request(twin=geometry))
    assert report['geometry']['hbm_assembly_count']==6 and report['geometry']['primitive_count']==574
    assert report['bindings']==[]
    column=store.create_column(report['id'],{'x_mm':49.47421875,'y_mm':39.947265625})
    assert column['segment_count']==26 and column['depth_mm']==2.65
    assert column['segments'][0]['material_id']=='ambient_water'
    assert column['coverage']['required_material_ids']==['silicon','copper','solder','epoxy','fr4']
    assert not column['propagation_available']
    assert store.read_column(report['id'],column['id'])==column


def test_subset_complete_does_not_authorize_missing_air_at_actual_column(fixture):
    store,body,_=fixture;body.update(coverage_scope='selected_materials',required_material_ids=['copper'])
    report=store.create(body);column=store.create_column(report['id'],{'x_mm':2.,'y_mm':1.5})
    assert report['coverage']['status']=='complete_for_selected_materials'
    assert column['coverage']['status']=='incomplete' and column['coverage']['missing_material_ids']==['air']
    assert all(s['coverage_status']=='ambient_policy' for s in column['segments'] if s['material_label']==0)


def test_defect_exclusion_is_frozen_and_cannot_be_overridden_by_column(fixture):
    store,body,_=fixture;body['include_defects']=False
    report=store.create(body);column=store.create_column(report['id'],{'x_mm':2.,'y_mm':1.5})
    assert column['coverage']['complete'] and 'air' not in report['coverage']['inventory_material_ids']
    with pytest.raises(ValueError):store.create_column(report['id'],{'x_mm':2.,'y_mm':1.5,'include_defects':True})


@pytest.mark.parametrize('change',[
    lambda r:r.update(propagation_available=True),
    lambda r:r['coverage'].update(complete=True),
    lambda r:r['coverage'].update(missing_material_ids=[]),
    lambda r:r['geometry'].update(primitive_count=1),
    lambda r:r['bindings'][0]['parameters'].update(density_kg_m3=3000.),
    lambda r:r['bindings'][0]['origin'].update(layer_index=1),
    lambda r:r['bindings'][0].update(evidence='calibrated'),
    lambda r:r['bindings'][0].update(nominal_density_kg_m3=1000.),
    lambda r:r['bindings'][0].update(nominal_density_difference_kg_m3=0.),
    lambda r:r['request'].update(include_defects=1),
    lambda r:r['request'].update(coverage_scope='selected_materials'),
    lambda r:r['request']['bindings'][0]['origin'].update(density_kg_m3=1000.),
    lambda r:r['identity']['material_label_map'].update({'0':'air'}),
    lambda r:r['ambient_policy'].update(sound_speed_m_s=1500.),
    lambda r:r['resources'].update(estimated_peak_bytes=1),
    lambda r:r['resources'].update(estimated_report_expanded_bytes=1),
    lambda r:r['resources'].update(propagation_work_units=1),
    lambda r:r['store_identity']['implementation_sha256'].pop('sls_comparison_store.py'),
    lambda r:r.update(created_at='2026-09-05'),
])
def test_resealed_assignment_corruption_rejected(fixture,change):
    store,body,_=fixture;report=store.create(body);change(report);rehash(report);write(store,report)
    with pytest.raises(ValueError):store.read(report['id'])


def retwin(report,change):
    old=report['twin_sha256'];t=report['snapshots'].pop(old);report['snapshot_kinds'].pop(old)
    change(t);new=storage.json_measure(t)['sha256'];report['snapshots'][new]=t;report['snapshot_kinds'][new]='twin'
    report['twin_sha256']=new;report['request']['twin_sha256']=new
    full={k:v for k,v in report['request'].items() if k!='twin_sha256'};full['twin']=t
    report['input_request_sha256']=storage.json_measure(full)['sha256'];rehash(report)


@pytest.mark.parametrize('change',[
    lambda t:t['objects'][0].update(material='gold'),
    lambda t:t['objects'][0].update(shape='mesh'),
    lambda t:t['objects'][0].update(center_mm=[200.,1.5,1.]),
    lambda t:t['objects'][0].update(size_mm=[0.,2.,1.]),
    lambda t:t['objects'][0].update(id=t['objects'][1]['id']),
    lambda t:t['objects'][1].update(size_mm=[1.,2.,1.5]),
    lambda t:t.update(size_mm=[4.,3.,8.]),
    lambda t:t.update(extra_metadata=True),
    lambda t:t['objects'][0].update(assembly_id='hbm-99'),
])
def test_resealed_twin_shape_domain_and_metadata_rejected_without_constructor(fixture,change):
    store,body,_=fixture;report=store.create(body);retwin(report,change);write(store,report)
    with pytest.raises(ValueError):store.read(report['id'])


@pytest.mark.parametrize('change',[
    lambda c:c.update(x_mm=5.),
    lambda c:c.update(depth_mm=1.),
    lambda c:c.update(segment_count=1),
    lambda c:c.update(propagation_available=True),
    lambda c:c['segments'][0].update(z_start_mm=.01),
    lambda c:c['segments'][1].update(thickness_mm=.1),
    lambda c:c['segments'][2].update(material_id='ambient_water'),
    lambda c:c['segments'][2].update(assignment_sha256='0'*64),
    lambda c:c['segments'][0].update(coverage_status='assigned'),
    lambda c:c['coverage'].update(complete=True),
    lambda c:c['diagnostics'].update(event_work_bound=0),
    lambda c:c['diagnostics'].update(estimated_path_workspace_bytes=0),
    lambda c:c['diagnostics'].update(adjusted_endpoint_count=1000000),
    lambda c:c['diagnostics'].update(allocated_path_bytes=0),
    lambda c:c['resources'].update(estimated_peak_bytes=1),
    lambda c:c['assignment_record']['bindings'][0]['parameters'].update(relaxation_time_us=.005),
    lambda c:c.update(assignment_sha256='0'*64),
])
def test_resealed_column_evidence_corruption_rejected_without_geometry(fixture,change,monkeypatch):
    store,body,_=fixture;parent=store.create(body);column=store.create_column(parent['id'],{'x_mm':2.,'y_mm':1.5})
    change(column);rehash(column);write(store,column)
    monkeypatch.setattr(core,'inspect_column',lambda *a,**k:pytest.fail('Historical geometry called'))
    with pytest.raises(ValueError):store.read_column(parent['id'],column['id'])


def test_new_columns_require_supported_frozen_geometry_before_path_call(fixture,monkeypatch):
    store,body,_=fixture;parent=store.create(body);column=store.create_column(parent['id'],{'x_mm':2.,'y_mm':1.5})
    fingerprints=storage._fingerprints();fingerprints['column_paths.py']='0'*64
    monkeypatch.setattr(storage,'_fingerprints',lambda:fingerprints)
    monkeypatch.setattr(core,'inspect_column',lambda *a,**k:pytest.fail('Geometry called despite identity mismatch'))
    with pytest.raises(ValueError,match='geometry'):store.create_column(parent['id'],{'x_mm':2.,'y_mm':1.5})
    assert store.read(parent['id'])==parent and store.read_column(parent['id'],column['id'])==column


def test_six_sources_deduplicate_and_seventh_or_unused_are_not_accepted(fixture):
    store,body,source=fixture
    geometry={'name':'six material labels','size_mm':[4.,3.,2.],'objects':[primitive(str(i),m) for i,m in enumerate(storage.MATERIAL_IDS)]}
    bindings=[]
    for material in storage.MATERIAL_IDS:
        copied=deepcopy(source);identifier=str(uuid4());copied['id']=copied['report_id']=identifier
        copied['report_sha256']=storage.json_measure({k:v for k,v in copied.items() if k!='report_sha256'})['sha256']
        (store.root/'sls-reports'/f'{identifier}.json').write_bytes(storage.bounded_payload(copied));bindings.append(reference(copied,material))
    report=store.create(request(twin=geometry,bindings=bindings))
    assert len(report['snapshots'])==7 and report['resources']['source_report_count']==6
    dedup=store.create(request(twin=geometry,bindings=[reference(source,m) for m in storage.MATERIAL_IDS]))
    assert len(dedup['snapshots'])==2 and dedup['resources']['source_report_count']==1


def test_aggregate_retention_limits_second_source_before_decode(fixture,monkeypatch):
    store,body,source=fixture
    second=deepcopy(source);second['id']=second['report_id']=str(uuid4());rehash(second)
    second_path=store.root/'sls-reports'/f'{second["id"]}.json';second_path.write_bytes(storage.bounded_payload(second))
    body['bindings']=[reference(source,'copper'),reference(second,'silicon')]
    real=storage.bounded_json_read;seen=[]
    def reader(path,*a,**k):
        seen.append((str(path),k.get('retained_bytes',0)))
        value,measure=real(path,*a,**k)
        if len(seen)==1:measure['expanded_bytes']=storage.MAX_REPORT_EXPANDED_BYTES
        return value,measure
    monkeypatch.setattr(storage,'bounded_json_read',reader)
    with pytest.raises(ValueError,match='aggregate|retained|Retained'):store.create(body)
    assert len(seen)==1 and seen[0][1]>0 and not store.directory.exists()


def test_final_source_recheck_streams_without_second_decode(fixture,monkeypatch):
    store,body,_=fixture
    original=core.build_assignment
    def build(*a,**k):
        result=original(*a,**k)
        monkeypatch.setattr(storage,'bounded_json_read',lambda *a,**k:pytest.fail('Second source JSON decode during publication'))
        return result
    monkeypatch.setattr(core,'build_assignment',build)
    assert store.create(body)['id']


def test_column_parent_final_recheck_does_not_decode_again(fixture,monkeypatch):
    store,body,_=fixture;parent=store.create(body);original=core.inspect_column
    def inspect(*a,**k):
        result=original(*a,**k)
        monkeypatch.setattr(storage,'bounded_json_read',lambda *a,**k:pytest.fail('Second parent decode during publication'))
        return result
    monkeypatch.setattr(core,'inspect_column',inspect)
    assert store.create_column(parent['id'],{'x_mm':2.,'y_mm':1.5})['id']


def test_source_changed_after_validated_copy_prevents_publication(fixture,monkeypatch):
    store,body,source=fixture;original=core.build_assignment
    def build(*a,**k):
        result=original(*a,**k)
        (store.root/'sls-reports'/f'{source["id"]}.json').write_bytes(b'{}')
        return result
    monkeypatch.setattr(core,'build_assignment',build)
    with pytest.raises(ValueError,match='changed'):store.create(body)
    assert not store.directory.exists()


def test_catalog_releases_full_snapshot_before_next_read(fixture,monkeypatch):
    store,body,_=fixture;docs=[store.create(body) for _ in range(3)]
    actual=store.read;previous=[]
    class Tracked(dict):pass
    def read(identifier):
        assert not previous or previous[-1]() is None
        value=Tracked(actual(identifier));previous.append(weakref.ref(value));return value
    monkeypatch.setattr(store,'read',read)
    assert len(store.list(limit=3))==3
    assert all(r() is None for r in previous)


@pytest.mark.parametrize('payload',[b'{"a":1,"a":2}',b'{"a":NaN}',b'{"a":1e400}',b'['*66+b'0'+b']'*66])
def test_duplicate_nonfinite_depth_json_rejected(tmp_path,payload):
    path=tmp_path/'bad.json';path.write_bytes(payload)
    with pytest.raises(ValueError):storage.bounded_json_read(path)


def test_expansion_and_retention_reject_before_json_decode(tmp_path,monkeypatch):
    path=tmp_path/'large.json';path.write_bytes(b'['+b'{},'*300000+b'{}]')
    monkeypatch.setattr(storage._json.json,'loads',lambda *a,**k:pytest.fail('Decoded before raw expansion guard'))
    with pytest.raises(ValueError):storage.bounded_json_read(path,expanded_limit=64*1024**2)
    path.write_bytes(b'{}')
    with pytest.raises(ValueError):storage.bounded_json_read(path,retained_bytes=512*1024**2)


@pytest.mark.parametrize('identifier',['../escape','ABC','00000000-0000-0000-0000-00000000000A'])
def test_assignment_and_nested_column_paths_require_canonical_ids(tmp_path,identifier):
    store=storage.MaterialAssignmentStore(tmp_path)
    with pytest.raises(ValueError):store.read(identifier)
    with pytest.raises(ValueError):store.read_column(str(uuid4()),identifier)


def test_junction_ancestor_rejected_before_open(tmp_path,monkeypatch):
    store=storage.MaterialAssignmentStore(tmp_path);actual=getattr(Path,'is_junction',lambda p:False)
    monkeypatch.setattr(Path,'is_junction',lambda p:p==store.columns_directory or actual(p),raising=False)
    with pytest.raises(ValueError,match='junction'):store.read_column(str(uuid4()),str(uuid4()))


def test_exclusive_publication_collision_and_failure_preserve_existing_bytes(fixture,monkeypatch):
    store,body,_=fixture;identifier=str(uuid4());monkeypatch.setattr(storage,'uuid4',lambda:identifier)
    report=store.create(body);raw=store._path(identifier).read_bytes()
    with pytest.raises(FileExistsError):store.create(body)
    assert store._path(identifier).read_bytes()==raw
    new=str(uuid4());monkeypatch.setattr(storage,'uuid4',lambda:new)
    stage=store.directory/f'.{new}.tmp';stage.write_bytes(b'preserve')
    with pytest.raises(FileExistsError):store.create(body)
    assert stage.read_bytes()==b'preserve'
    newest=str(uuid4());monkeypatch.setattr(storage,'uuid4',lambda:newest)
    monkeypatch.setattr(storage.os,'link',lambda *a,**k:(_ for _ in ()).throw(OSError('injected')))
    with pytest.raises(OSError):store.create(body)
    assert not (store.directory/f'.{newest}.tmp').exists() and store._path(identifier).read_bytes()==raw


def test_disk_rejection_does_not_publish(fixture,monkeypatch):
    store,body,_=fixture;monkeypatch.setattr(shutil,'disk_usage',lambda p:SimpleNamespace(free=0))
    with pytest.raises(ValueError,match='disk'):store.create(body)
    assert not store.directory.exists()


def test_actual_api_history_exports_and_no_jobs(fixture):
    store,body,source=fixture;api=client(store.root);base='/api/v2/material-assignments'
    estimate=api.post(base+'/estimate',json=body);assert estimate.status_code==200,estimate.text
    assert not store.directory.exists()
    created=api.post(base,json=body);assert created.status_code==201,created.text
    report=created.json();route=f'{base}/{report["id"]}'
    assert api.get(base).json()['assignments'][0]['name']==body['name']
    result=api.post(route+'/columns',json={'x_mm':2.,'y_mm':1.5});assert result.status_code==201,result.text
    column=result.json();column_route=f'{route}/columns/{column["id"]}'
    assert api.get(route+'/columns').json()['columns'][0]['x_mm']==2.
    assert api.get(route+'/export').content==storage.bounded_payload(report)
    assert api.get(column_route+'/export').content==storage.bounded_payload(column)
    assert api.post(route+'/columns',json={'x_mm':9.,'y_mm':1.5}).status_code==422
    assert api.post(base,json={**body,'propagation_available':True}).status_code==422
    assert api.get(f'{base}/{source["id"]}').status_code==404
    assert not (store.root/'catalog.sqlite3').exists()
    shutil.rmtree(store.root/'sls-reports');store._path(report['id']).unlink()
    assert api.get(column_route).json()==column and api.get(column_route+'/export').content==storage.bounded_payload(column)


def test_frozen_constants_match_core_and_all_reader_fingerprints_present():
    for name in ('IDENTITY','AMBIENT_POLICY','WARNINGS','NOMINAL_DENSITIES','INVENTORY_DEFINITION'):
        assert getattr(storage,name)==getattr(core,name)
    identities=storage._fingerprints()
    assert all(identities[name]==hashlib.sha256(Path(storage.__file__).with_name(name).read_bytes()).hexdigest() for name in storage.IMPLEMENTATION_FILES)
    assert 'sls_comparison_store.py' in identities


@pytest.mark.parametrize('field',['twin_name','primitive_name','primitive_id'])
def test_original_twin_length_only_labels_are_preserved(fixture,field):
    store,body,_=fixture
    if field=='twin_name':body['twin']['name']=' '
    elif field=='primitive_name':body['twin']['objects'][0]['name']=' '
    else:body['twin']['objects'][0]['id']=' '
    report=store.create(body)
    assert store.read(report['id'])==report


def test_column_embedded_report_validation_reserve_cannot_be_understated(fixture):
    store,body,_=fixture;parent=store.create(body);column=store.create_column(parent['id'],{'x_mm':2.,'y_mm':1.5})
    assert column['resources']['estimated_peak_bytes']>=storage.json_measure(parent)['expanded_bytes']+256*1024**2
    column['resources']['estimated_peak_bytes']=128*1024**2;rehash(column);write(store,column)
    with pytest.raises(ValueError,match='forecast|workspace'):store.read_column(parent['id'],column['id'])
