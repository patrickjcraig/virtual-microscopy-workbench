"""Saved-only compatibility, bounded admission, identity and signed diagnostics."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from virtual_microscopy import sls_comparisons as core
from virtual_microscopy.sls_comparison_schemas import SLSComparisonRequest
from virtual_microscopy.sls_reports import SLSReportStore, bounded_payload, json_measure


@pytest.fixture(scope='module')
def template(tmp_path_factory):
    root=tmp_path_factory.mktemp('sls-comparison-source')
    medium={'name':'Assumed water','impedance_mrayl':1.48,'sound_speed_m_s':1480.}
    body={'name':'Manufactured comparison source','stack':{'incident':medium,'terminal':medium,
        'layers':[{'name':'A','thickness_mm':.05,'density_kg_m3':1000.,'relaxed_modulus_gpa':2.25,
                   'unrelaxed_modulus_gpa':4.,'relaxation_time_us':.003}]},
        'spectrum':{'start_mhz':0.,'end_mhz':150.,'samples':5},
        'causal_pulse':{'record_duration_us':.05}}
    return SLSReportStore(root).create(body)


def reseal(report):
    report['name']=report['request']['name']
    report['stack']=deepcopy(report['request']['stack'])
    report['request_sha256']=json_measure(report['request'])['sha256']
    report['stack_sha256']=json_measure(report['stack'])['sha256']
    report['provenance']['request_sha256']=report['request_sha256']
    report['report_sha256']=json_measure({k:v for k,v in report.items() if k!='report_sha256'})['sha256']
    return report


def save(root, source, change=lambda r:None):
    report=deepcopy(source);identifier=str(uuid4())
    report['id']=report['report_id']=identifier
    change(report);reseal(report)
    path=root/'sls-reports'/f'{identifier}.json';path.parent.mkdir(exist_ok=True)
    path.write_bytes(bounded_payload(report))
    return report


def request(a,b=None,**changes):
    return {'reference_report_id':a['id'],'candidate_report_id':(a if b is None else b)['id'],**changes}


def source_hashes(root):
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*.json')}


def test_self_deduplicates_complete_sources_and_preserves_all_bytes(tmp_path,template):
    a=save(tmp_path,template);before=source_hashes(tmp_path)
    result=core.compute_sls_comparison(tmp_path,request(a))
    assert source_hashes(tmp_path)==before
    assert len(result['source_snapshots'])==1
    assert result['source_reference']==result['source_candidate']
    frozen=result['source_snapshots'][result['source_reference']['snapshot_sha256']]
    assert frozen==a
    assert all(v==0 for values in result['causal_pulse']['difference'].values() for v in values)
    assert result['causal_pulse']['bounds']['source_sum']>=2*a['causal_pulse']['diagnostics']['total_error_bound']
    assert 'time_us' not in result['causal_pulse']
    assert 'frequency_mhz' not in result['spectrum']
    assert 'phase_deg' not in result['spectrum']['difference']['reflection']
    assert json_measure(result)['encoded_bytes']<=result['resources']['estimated_report_bytes']


def test_independent_toy_recording_signs_metrics_gate_and_time_locator(tmp_path,template):
    def waveform(r):
        n=len(r['causal_pulse']['time_us'])
        r['causal_pulse'].update(rf=[3.]*n,imaginary=[4.]*n,envelope=[5.]*n)
    a=save(tmp_path,template,waveform)
    def change(r):
        waveform(r);r['causal_pulse']['rf'][4]=-3.;r['causal_pulse']['imaginary'][4]=-4.
    b=save(tmp_path,template,change)
    result=core.compute_sls_comparison(tmp_path,request(a,b,gate_start_us=.0075,gate_end_us=.0125))
    pulse=result['causal_pulse'];assert pulse['difference']['envelope']==[0.]*21
    assert pulse['difference']['rf'][4]==-6. and pulse['difference']['imaginary'][4]==-8.
    assert pulse['full_metrics']['complex']['max_absolute_difference']==10.
    assert pulse['full_metrics']['complex']['rmse']==pytest.approx(10/np.sqrt(21))
    assert pulse['gate']['start_index']==3 and pulse['gate']['stop_index_exclusive']==6
    assert pulse['gate_metrics']['rf']['max_location']=={'time_index':4,'time_us':.01,'signed_difference':-6.}
    assert pulse['gate_products']['rms_rf']['difference']==0.
    assert pulse['gate_products']['residual_gate']['rms_rf']>0.


def test_history_does_not_call_installed_solver_constructor_proof_or_fingerprints(tmp_path,template,monkeypatch):
    a=save(tmp_path,template,lambda r:r.update(processing_version='historical-writer'))
    import virtual_microscopy.sls_acoustics as acoustic
    import virtual_microscopy.sls_time as causal
    import virtual_microscopy.sls_schemas as schemas
    import virtual_microscopy.sls_reports as reports
    forbidden=lambda *a,**k:pytest.fail('Current numerical implementation was consulted')
    monkeypatch.setattr(acoustic,'reflection_spectrum',forbidden)
    monkeypatch.setattr(causal,'causal_gamma_response',forbidden)
    monkeypatch.setattr(causal,'estimate_causal_gamma',forbidden)
    monkeypatch.setattr(schemas.SLSAnalysisRequest,'model_validate',forbidden)
    monkeypatch.setattr(reports,'_fingerprints',forbidden)
    monkeypatch.setattr(reports,'_proof_identity',forbidden)
    assert core.compute_sls_comparison(tmp_path,request(a))['mode']=='spectrum_and_reflected_rf'


@pytest.mark.parametrize('change,field',[
    (lambda r:r['spectrum']['frequency_mhz'].__setitem__(1,float(np.nextafter(37.5,np.inf))),'frequency_mhz'),
    (lambda r:r['spectrum'].__setitem__('phase_magnitude_floor',1e-10),'phase_magnitude_floor'),
    (lambda r:r['spectrum'].__setitem__('reference_planes','Other reference plane'),'reference_planes'),
    (lambda r:r['spectrum']['diagnostics'].__setitem__('unit_conversion','Unknown units'),'unit_conversion'),
    (lambda r:r['spectrum']['diagnostics'].__setitem__('energy_definition','Normalized gain'),'energy_definition'),
    (lambda r:r['causal_pulse']['diagnostics'].__setitem__('pulse_definition','Different phase'),'pulse_definition'),
    (lambda r:r['causal_pulse']['diagnostics'].__setitem__('time_definition','Depth axis'),'time_definition'),
    (lambda r:r['request']['stack']['terminal'].__setitem__('sound_speed_m_s',1481.),'terminal.sound_speed_m_s'),
    (lambda r:r['request']['stack']['incident'].__setitem__('impedance_mrayl',1.49),'incident.impedance_mrayl'),
    (lambda r:r['provenance']['implementation_sha256'].__setitem__('sls_time.py','f'*64),'implementation.sls_time.py'),
    (lambda r:r['provenance']['proof_document'].__setitem__('sha256','e'*64),'proof_document'),
])
def test_represented_axis_semantics_exterior_and_frozen_identity_rejections(tmp_path,template,change,field):
    a=save(tmp_path,template);b=save(tmp_path,template,change)
    with pytest.raises(core.SLSComparisonCompatibilityError) as error:
        core.compute_sls_comparison(tmp_path,request(a,b))
    assert any(field in item['field'] for item in error.value.issues)


def test_both_sources_with_same_unsupported_semantics_do_not_establish_compatibility(tmp_path,template):
    def change(r):r['spectrum']['diagnostics']['unit_conversion']='Same unknown units'
    a=save(tmp_path,template,change);b=save(tmp_path,template,change)
    with pytest.raises(core.SLSComparisonCompatibilityError):core.compute_sls_comparison(tmp_path,request(a,b))


def test_exact_time_axis_changes_reject_even_without_interpolation(tmp_path,template):
    a=save(tmp_path,template);b=deepcopy(a)
    b['causal_pulse']['time_us'][1]=float(np.nextafter(b['causal_pulse']['time_us'][1],np.inf))
    with pytest.raises(core.SLSComparisonCompatibilityError) as error:core.compatible(a,b,'spectrum_and_reflected_rf')
    assert any(i['field']=='time_us' for i in error.value.issues)
    broken=save(tmp_path,template,lambda r:r['causal_pulse']['time_us'].__setitem__(1,b['causal_pulse']['time_us'][1]))
    with pytest.raises(ValueError,match='centers'):core.compute_sls_comparison(tmp_path,request(a,broken))


@pytest.mark.parametrize('source_system',['Windows','Linux'])
def test_disclosed_layer_changes_names_and_runtime_do_not_infer_correspondence(tmp_path,template,source_system):
    template=deepcopy(template)
    template['provenance']['runtime']['system']=source_system
    a=save(tmp_path,template)
    def changes(r):
        r['request']['stack']['layers'][0]['unrelaxed_modulus_gpa']=4.5
        r['request']['stack']['layers'][0]['relaxation_time_us']=.02
        r['request']['stack']['terminal']['name']='Relabeled boundary'
        r['provenance']['runtime']['system']='Linux' if source_system=='Windows' else 'Windows'
    b=save(tmp_path,template,changes)
    result=core.compute_sls_comparison(tmp_path,request(a,b))
    fields={v['path'] for v in result['parameter_differences']}
    assert 'request.stack.layers[0].unrelaxed_modulus_gpa' in fields
    assert 'request.stack.layers[0].relaxation_time_us' in fields
    assert 'request.stack.terminal.name' in fields
    assert 'provenance.runtime.system' in fields
    assert 'None inferred' in result['compatibility']['material_correspondence']
    assert core.estimate_sls_comparison(tmp_path,request(a,b))['parameter_differences']==result['parameter_differences']


@pytest.mark.parametrize('field',['center_frequency_mhz','fractional_bandwidth','gamma_order','surface_standoff_mm',
    'sample_rate_mhz','record_start_us','record_duration_us'])
def test_every_physical_excitation_or_record_definition_is_compatible_only_when_exact(template,field):
    b=deepcopy(template)
    value=b['request']['causal_pulse'][field]
    b['request']['causal_pulse'][field]=value+1 if field=='gamma_order' else float(np.nextafter(value,np.inf))
    with pytest.raises(core.SLSComparisonCompatibilityError) as error:
        core.compatible(template,b,'spectrum_and_reflected_rf')
    assert any(item['field']=='causal_pulse.'+field for item in error.value.issues)


def test_different_layer_lengths_are_positional_without_zipping_away_added_layers(template):
    b=deepcopy(template)
    b['request']['stack']['layers'].append(deepcopy(b['request']['stack']['layers'][0]))
    differences=core.parameter_differences(template,b)
    added=[item for item in differences if item['path']=='request.stack.layers[1]']
    assert len(added)==1 and added[0]['reference'] is None
    assert added[0]['candidate']==b['request']['stack']['layers'][1]


def test_precision_tolerance_changes_are_disclosed_and_both_complete_source_bounds_retained(tmp_path,template):
    a=save(tmp_path,template)
    def change(r):
        r['request']['causal_pulse'].update(precision_bits=192,absolute_tolerance=2e-7)
        for target in (r['resources']['causal_pulse'],r['causal_pulse']['diagnostics']):
            target.update(precision_bits=192,requested_tolerance=2e-7)
    b=save(tmp_path,template,change)
    result=core.compute_sls_comparison(tmp_path,request(a,b))
    assert {v['path'] for v in result['parameter_differences']}=={'request.causal_pulse.precision_bits','request.causal_pulse.absolute_tolerance'}


def test_frequency_only_accepts_absent_or_ignored_pulses_without_rf_claims(tmp_path,template):
    a=save(tmp_path,template)
    def no_pulse(r):
        r['request']['causal_pulse']=r['causal_pulse']=r['resources']['causal_pulse']=None
        r['resources']['time_samples']=0
    b=save(tmp_path,template,no_pulse)
    result=core.compute_sls_comparison(tmp_path,request(a,b,mode='spectrum_only'))
    assert result['causal_pulse'] is None and result['arithmetic_policy'] is None
    assert result['parameter_differences']
    with pytest.raises(core.SLSComparisonCompatibilityError):core.compute_sls_comparison(tmp_path,request(a,b))


@pytest.mark.parametrize('extra',[{'mode':'other'},{'gate_start_us':.01},{'gate_start_us':.02,'gate_end_us':.01},
    {'frequency_index':True},{'time_index':False},{'mode':'spectrum_only','time_index':0},
    {'mode':'spectrum_only','gate_start_us':0.,'gate_end_us':.02},{'normalization':'unit'},{'twin':{}}])
def test_request_rejects_unsupported_controls(extra):
    with pytest.raises(ValueError):SLSComparisonRequest.model_validate({'reference_report_id':str(uuid4()),'candidate_report_id':str(uuid4()),**extra})


@pytest.mark.parametrize('extra',[{'gate_start_us':.051,'gate_end_us':.052},{'gate_start_us':.0001,'gate_end_us':.0002},
    {'frequency_index':5},{'time_index':21}])
def test_bad_gate_and_cursor_reject_before_any_spectral_or_rf_reduction(tmp_path,template,monkeypatch,extra):
    a=save(tmp_path,template)
    monkeypatch.setattr(core,'spectrum_comparison',lambda *a:pytest.fail('Reduction before cursor/gate admission'))
    with pytest.raises(ValueError):core.compute_sls_comparison(tmp_path,request(a,**extra))


def test_estimate_does_not_compute_residuals_and_rejects_report_budget_early(tmp_path,template,monkeypatch):
    a=save(tmp_path,template)
    forbidden=lambda *a,**k:pytest.fail('Residual evaluation before admission')
    monkeypatch.setattr(core,'spectrum_comparison',forbidden);monkeypatch.setattr(core,'checked_differences',forbidden)
    estimate=core.estimate_sls_comparison(tmp_path,request(a))
    assert estimate['unique_source_snapshots']==1 and estimate['time_samples']==21
    monkeypatch.setattr(core,'MAX_REPORT_BYTES',1)
    with pytest.raises(ValueError,match='encoded'):core.compute_sls_comparison(tmp_path,request(a))


def test_source_corruption_and_nonfinite_values_reject(tmp_path,template):
    a=save(tmp_path,template);path=tmp_path/'sls-reports'/f"{a['id']}.json"
    broken=deepcopy(a);broken['causal_pulse']['rf'][0]+=1
    path.write_text(json.dumps(broken))
    with pytest.raises(ValueError,match='checksum'):core.compute_sls_comparison(tmp_path,request(a))
    broken['causal_pulse']['rf'][0]=float('nan');path.write_text(json.dumps(broken))
    with pytest.raises(ValueError):core.compute_sls_comparison(tmp_path,request(a))


def test_source_byte_limit_rejects_before_json_decode(tmp_path,template,monkeypatch):
    a=save(tmp_path,template)
    monkeypatch.setattr(core,'SOURCE_BYTES',1)
    import virtual_microscopy.causal_comparison_store as shared
    monkeypatch.setattr(shared.json,'loads',lambda *a,**k:pytest.fail('Oversized input decoded'))
    with pytest.raises(ValueError,match='byte limit'):core.compute_sls_comparison(tmp_path,request(a))


def test_source_change_during_reduction_is_detected_before_return(tmp_path,template,monkeypatch):
    a=save(tmp_path,template);path=tmp_path/'sls-reports'/f"{a['id']}.json"
    original=core.spectrum_comparison
    def changed(*args):
        value=original(*args)
        updated=deepcopy(a);updated['request']['name']='Concurrent edit';reseal(updated)
        path.write_bytes(bounded_payload(updated))
        return value
    monkeypatch.setattr(core,'spectrum_comparison',changed)
    with pytest.raises(ValueError,match='changed during'):core.compute_sls_comparison(tmp_path,request(a))


def test_independent_historical_store_freezes_same_supported_semantic_contract():
    from virtual_microscopy import sls_comparison_store as store
    for name in ('NUMERICAL_FILES','SPECTRUM_SEMANTICS','REFERENCE_PLANES','PULSE_SEMANTICS','POLICY'):
        assert getattr(core,name)==getattr(store,name)
