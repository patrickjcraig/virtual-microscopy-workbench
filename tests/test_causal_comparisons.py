"""Saved-source causal compatibility, analytic residuals and immutable API views."""
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4

from fastapi.testclient import TestClient
import numpy as np
import pytest

from virtual_microscopy.causal_sam import prepare_causal_sam, iter_causal_sam_rows
from virtual_microscopy.causal_sam_schemas import CausalSamVolumeRequest
from virtual_microscopy.causal_datasets import CausalSamDatasetStore, IMPLEMENTATION_FILES
from virtual_microscopy.causal_comparison_schemas import CausalComparisonRequest
from virtual_microscopy.causal_comparison_store import CausalComparisonStore, bounded_payload
from virtual_microscopy.causal_comparisons import (compute_causal_comparison, causal_comparison_view,
    CausalComparisonCompatibilityError, _source, _compatible, _plan, _read_row)
from tools.verify_causal_layered_rf import slab_oracle


def source(root,thickness=.1,frequency=50):
    body = CausalSamVolumeRequest.model_validate({"kind":"sam_causal_rf_volume","twin":{
        "schema_version":1,"name":f"Assumed silicon slab {thickness} mm","size_mm":[4,3,thickness],
        "objects":[{"id":"slab","name":"Silicon","shape":"box","material":"silicon","role":"structure",
                    "center_mm":[2,1.5,thickness/2],"size_mm":[4,3,thickness]}]},
        "acquisition":{"scan_nx":16,"scan_ny":16,"record_duration_us":.3,"sample_rate_mhz":400,
                       "center_frequency_mhz":frequency,"precision_bits":128,"absolute_tolerance":1e-7}})
    prepared = prepare_causal_sam(body)
    store = CausalSamDatasetStore(root)
    identifier = str(uuid4())
    store.create(identifier,prepared.request.model_dump(mode="json",exclude_none=True),prepared.estimate)
    store.initialize_arrays(identifier,prepared)
    for y0,y1,products,certs in iter_causal_sam_rows(prepared):
        store.write_row(identifier,y0,y1,products,certs)
    store.complete(identifier)
    prepared.close()
    return identifier


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    root=tmp_path_factory.mktemp("causal-comparison-sources")
    return root,source(root),source(root,.12),source(root,.1,40)


def request(a,b,**kwargs):
    return {"reference_dataset_id":a,"candidate_dataset_id":b,"gate_start_us":.04,"gate_end_us":.2,
            "x_index":3,"y_index":7,"time_index":23,**kwargs}


def hashes(path):
    return {str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob("*") if p.is_file()}


def oracle(root,identifier):
    store=CausalSamDatasetStore(root)
    m=store.verify_complete(identifier); g=store.open_arrays(identifier)
    acq=m['request']['acquisition']
    from virtual_microscopy.layered_schemas import CausalGammaPulseSettings
    pulse={key:g[key][0,0].tolist() for key in ('rf','imaginary','envelope')}
    pulse.update(time_us=g['time_us'][:].tolist(),diagnostics=m['class_certificates']['0']['diagnostics'])
    _,expected=slab_oracle({'request':{'stack':m['estimate']['stack_table'][0]['stack'],
        'causal_pulse':{k:acq[k] for k in CausalGammaPulseSettings.model_fields}},'causal_pulse':pulse})
    return expected


def test_analytic_saved_pair_residual_bounds_and_no_source_changes(sources):
    root,a,b,_=sources
    before=hashes(root)
    report=compute_causal_comparison(root,request(a,b))
    av,bv=oracle(root,a),oracle(root,b)
    # Oracle output names are checked directly, never derived through the comparison kernel.
    ar=np.asarray(av['rf']); ai=np.asarray(av['imaginary'])
    br=np.asarray(bv['rf']); bi=np.asarray(bv['imaginary'])
    d=report['initial_view']['traces']['difference']
    err=np.hypot(np.asarray(d['rf'])-(br-ar),np.asarray(d['imaginary'])-(bi-ai))
    assert err.max() <= report['initial_view']['selected_bounds']['complex_total']
    envelope_error=np.abs(np.asarray(d['envelope'])-(np.asarray(bv['envelope'])-np.asarray(av['envelope'])))
    assert envelope_error.max() <= report['initial_view']['selected_bounds']['envelope_total']
    bounds=report['initial_view']['selected_bounds']
    assert Fraction(bounds['complex_total']) >= Fraction(bounds['source_sum'])+Fraction(bounds['complex_arithmetic'])
    assert report['compatibility']['differences']['resolved_numeric_columns_changed']==256
    assert report['metrics']['full_record']['rf']['rmse']>0
    assert report['gate']['sample_count']==65
    assert report['gate']['actual_start_us']==.04 and report['gate']['actual_end_us']==.2
    assert hashes(root)==before


def test_same_source_zero_and_finite_conservative_bounds(sources):
    root,a,_,_=sources
    r=compute_causal_comparison(root,request(a,a))
    for key in ('rf','imaginary','envelope','complex'):
        assert r['metrics']['full_record'][key]['rmse']==0
    for key in ('rf','imaginary','envelope'):
        assert not np.asarray(r['initial_view']['traces']['difference'][key]).any()
    assert r['initial_view']['selected_bounds']['complex_total']>0
    assert r['compatibility']['differences']['resolved_numeric_columns_changed']==0


@pytest.mark.parametrize('field,value',[
    ('excitation_model','future'),('observation_model','focused'),('time_zero','shifted'),
    ('rf_unit','Pa'),('envelope_processing','hilbert'),('material_model','frequency_loss'),
    ('certificate_version',None),('focus_model','gaussian')])
def test_unsupported_frozen_semantics_rejected(sources,field,value):
    root,a,b,_=sources
    left,right=_source(root,a),_source(root,b)
    right.manifest['metadata'][field]=value
    with pytest.raises(CausalComparisonCompatibilityError) as exc:
        _compatible(left,right)
    assert any(field in issue['field'] for issue in exc.value.issues)


@pytest.mark.parametrize('coordinate',['x_mm','y_mm','time_us'])
def test_one_ulp_actual_coordinates_rejected(sources,coordinate):
    root,a,b,_=sources
    left,right=_source(root,a),_source(root,b)
    right.coordinates[coordinate][2]=np.nextafter(right.coordinates[coordinate][2],np.inf)
    with pytest.raises(CausalComparisonCompatibilityError):
        _compatible(left,right)


def test_changed_excitation_rejected_and_numerical_tolerance_allowed(sources):
    root,a,b,incompatible=sources
    with pytest.raises(CausalComparisonCompatibilityError):
        compute_causal_comparison(root,request(a,incompatible))
    left,right=_source(root,a),_source(root,b)
    right.manifest['request']['acquisition'].update(absolute_tolerance=1e-9,precision_bits=192)
    _compatible(left,right)


@pytest.mark.parametrize('overrides',[{'gate_start_us':-.1},{'gate_end_us':.4},
    {'gate_start_us':.1001,'gate_end_us':.1002},{'x_index':16},{'time_index':121}])
def test_invalid_gate_or_cursor_has_no_report(sources,overrides):
    root,a,b,_=sources
    before=hashes(root)
    with pytest.raises(ValueError):
        compute_causal_comparison(root,request(a,b,**overrides))
    assert hashes(root)==before


def test_workspace_guard_before_decoding(sources,monkeypatch):
    root,a,b,_=sources
    monkeypatch.setattr(CausalSamDatasetStore,'verify_complete',lambda *_:pytest.fail('decoded before workspace admission'))
    with pytest.raises(ValueError,match='workspace'):
        _source(root,a,512*1024**2)


def test_changed_row_rejected_on_second_read(sources,tmp_path):
    root,a,_,_=sources
    shutil.copytree(root/a,tmp_path/a)
    s=_source(tmp_path,a)
    path=tmp_path/a/'data.zarr'/'rf'/'c'/'0'/'0'/'0'
    data=bytearray(path.read_bytes()); data[16]^=1; path.write_bytes(data)
    with pytest.raises(ValueError,match='checksum|waveform|inconsistent'):
        _read_row(s,0)


def test_immutable_offline_report_and_api(sources,tmp_path,monkeypatch):
    root,a,b,bad=sources
    for identifier in (a,b,bad):
        shutil.copytree(root/identifier,tmp_path/identifier)
    monkeypatch.setenv('VM_DATA_ROOT',str(tmp_path))
    from virtual_microscopy.server import app
    from virtual_microscopy import causal_sam,layered_time
    before={identifier:hashes(tmp_path/identifier) for identifier in (a,b,bad)}
    for module,name in ((causal_sam,'prepare_causal_sam'),(causal_sam,'estimate_causal_sam'),(layered_time,'causal_gamma_response')):
        monkeypatch.setattr(module,name,lambda *_a,**_k:pytest.fail('comparison ran a forward solver'))
    with TestClient(app) as client:
        jobs=client.get('/api/v2/jobs').json()
        r=client.post('/api/v2/causal-comparisons',json=request(a,b,name='Analytic pair'))
        assert r.status_code==201,r.text
        report=r.json(); identifier=report['id']; prefix=f'/api/v2/causal-comparisons/{identifier}'
        raw=(tmp_path/'causal-comparisons'/f'{identifier}.json').read_bytes()
        assert client.get(prefix+'/export').content==raw
        csv=client.get(prefix+'/export?format=csv')
        assert csv.status_code==200 and 'report_sha256' in csv.text
        assert client.get(prefix+'/view').json()==report['initial_view']
        live=client.get(prefix+'/view?x_index=8&y_index=4&time_index=55')
        assert live.status_code==200,live.text
        assert live.json()['cursor']['time_us']==.1375
        rejection=client.post('/api/v2/causal-comparisons',json=request(a,bad))
        assert rejection.status_code==422 and rejection.json()['detail']['issues']
        catalog=client.get('/api/v2/causal-comparisons?limit=1').json()
        assert catalog['order']=='id_desc' and catalog['comparisons'][0]['id']==identifier
        assert client.get('/api/v2/jobs').json()==jobs
        assert before=={i:hashes(tmp_path/i) for i in before}
        # Remove one owned temporary source manifest only; no source is required
        # for the retained report/initial view/export, but a new cursor needs it.
        (tmp_path/a/'manifest.json').unlink()
        assert client.get(prefix).json()==report
        assert client.get(prefix+'/export').content==raw
        assert client.get(prefix+'/view').json()==report['initial_view']
        assert client.get(prefix+'/view?x_index=0').status_code==404
        assert (tmp_path/'causal-comparisons'/f'{identifier}.json').read_bytes()==raw
