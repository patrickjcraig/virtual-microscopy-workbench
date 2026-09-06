"""Read-only independent checks of three native mixed-media control reports.

Creates no report or job and refuses to overwrite evidence. The independent
transfer/inverse methods supply agreement diagnostics, not oracle certificates.
"""
from __future__ import annotations

import argparse
import csv
from fractions import Fraction
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
from time import perf_counter
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from mpmath import mp

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.mixed_independent_oracle import frequency_transfer, inverse_transfer
from tools.verify_sls_comparison_delivery import canonical, catalog_reconciliation, digest


def content_digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def verify(base_url, data_root, native_receipt, copy_receipt, output):
    if urlsplit(base_url).hostname not in ('127.0.0.1','localhost'):
        raise ValueError('Delivery verification requires a loopback service.')
    if output.exists(): raise FileExistsError(output)
    started=perf_counter();workspace=Path(__file__).resolve().parents[1]
    native,copied=(json.loads(p.read_bytes()) for p in (native_receipt,copy_receipt))
    assert native['passed'] and not native['errors'] and native['jobs_created']==0
    ids=native['report_ids'];assert set(ids)=={'real','dispersive','water_spacer'}
    assert len(set(ids.values()))==3 and all(str(UUID(value))==value for value in ids.values())
    assert Path(copied['destination']).resolve()==data_root.resolve()
    # Hash every new file before the first API call, not after a read that might
    # accidentally have rewritten it. Frozen bytes anchor all later comparisons.
    paths={name:data_root/'mixed-reports'/f'{identifier}.json' for name,identifier in ids.items()}
    original_hashes={name:digest(path) for name,path in paths.items()}
    raw_reports={name:json.loads(path.read_bytes()) for name,path in paths.items()}
    reconciliation=catalog_reconciliation(copied,data_root)
    def unchanged():
        assert all(digest(data_root/name)==sha for name,sha in copied['sha256'].items())
        assert all(digest(paths[name])==sha for name,sha in original_hashes.items())
        with sqlite3.connect(f'{(data_root/"catalog.sqlite3").resolve().as_uri()}?mode=ro',uri=True) as db:
            assert hashlib.sha256('\n'.join(db.iterdump()).encode()).hexdigest()==reconciliation['running_catalog_sha256']
    unchanged()
    result={'status':'running','reports_created':0,'jobs_created':0,'report_ids':ids,'reports':{},
        'startup_catalog_reconciliation':reconciliation,
        'input_receipt_sha256':{str(p):digest(p) for p in (native_receipt,copy_receipt)},
        'verification_sha256':{name:digest(workspace/name) for name in (
            'tools/verify_mixed_delivery.py','tools/mixed_independent_oracle.py','tools/verify_sls_comparison_delivery.py')}}
    ctx=mp.clone();ctx.dps=75
    def complex_value(response,i): return ctx.mpc(response['real'][i],response['imag'][i])
    with httpx.Client(base_url=base_url,timeout=120) as client:
        def get(path):
            response=client.get(path);response.raise_for_status();return response
        assert get('/api/health').json()=={'status':'ok','version':'0.20.0'}
        catalogs={route:get(route).content for route in ('/api/v2/jobs','/api/v2/observations/jobs',
            '/api/v2/sls-acoustics/reports','/api/v2/material-assignments','/api/v2/mixed-acoustics/reports')}
        for name,identifier in ids.items():
            route=f'/api/v2/mixed-acoustics/reports/{identifier}'
            doc=get(route).json();assert canonical(doc)==canonical(raw_reports[name])
            assert canonical(get(route+'/export?format=json').json())==canonical(doc)
            previous_limit=csv.field_size_limit(64*1024**2)
            try:
                rows=list(csv.DictReader(io.StringIO(get(route+'/export?format=csv').text)))
                assert len(rows)==len(doc) and all(row['section']=='report' for row in rows)
                rebuilt={row['field']:json.loads(row['value_json']) for row in rows}
                assert canonical(rebuilt)==canonical(doc)
            finally: csv.field_size_limit(previous_limit)
            assert doc['kind']=='mixed_layered_analysis' and doc['id']==doc['report_id']==identifier
            assert doc['report_sha256']==content_digest({k:v for k,v in doc.items() if k!='report_sha256'})
            assert doc['request_sha256']==content_digest(doc['request']) and doc['stack_sha256']==content_digest(doc['stack'])
            assert canonical(doc['request']['stack'])==canonical(doc['stack'])
            assert doc['source_status']=='manual_assumptions'
            assert doc['provenance']['proof_document']['sha256']==digest(workspace/'docs/MIXED_MATERIAL_PROOF.md')
            for file,sha in doc['provenance']['implementation_sha256'].items():
                assert digest(workspace/'virtual_microscopy'/file)==sha
            spectrum=doc['spectrum'];frequencies=spectrum['frequency_mhz']
            assert len(frequencies)==257
            oracle=frequency_transfer(doc['stack'],frequencies)
            r_errors=[];t_errors=[]
            for i,row in enumerate(oracle['values']):
                r_errors.append(abs(complex_value(spectrum['reflection'],i)-ctx.mpc(row['reflection']['real'],row['reflection']['imaginary'])))
                t_errors.append(abs(complex_value(spectrum['transmission'],i)-ctx.mpc(row['transmission']['real'],row['transmission']['imaginary'])))
            assert max(r_errors)<ctx.mpf('1e-12') and max(t_errors)<ctx.mpf('1e-12')
            item={'report_sha256':doc['report_sha256'],'json_csv_exact_roundtrip':True,'frequency_samples':len(frequencies),
                'spectrum_oracle':oracle,'maximum_reflection_difference':float(max(r_errors)),
                'maximum_transmission_difference':float(max(t_errors)),'resource_estimate':doc['resources']}
            for layer,curve in zip(doc['stack']['layers'],spectrum['materials']):
                assert curve['kind']==layer['kind']
                if layer['kind']=='lossless_real':
                    assert curve['zero_relaxation'] is None
                    assert all(value==0 for field in ('attenuation_db_mm','attenuation_np_m','impedance_imag_mrayl') for value in curve[field])
                    assert all(float(value).hex()==float(layer['sound_speed_m_s']).hex() for value in curve['phase_speed_m_s'])
                    assert all(float(value).hex()==float(layer['impedance_mrayl']).hex() for value in curve['impedance_real_mrayl'])
            pulse=doc['causal_pulse']
            if name=='real':
                assert pulse is None and len(doc['stack']['layers'])==1 and doc['stack']['layers'][0]['kind']=='lossless_real'
                energy=max(abs(ctx.mpf(r)+ctx.mpf(t)-1) for r,t in zip(spectrum['reflectance'],spectrum['transmittance']))
                assert energy<ctx.mpf('1e-12');item['maximum_energy_residual']=float(energy)
            else:
                assert pulse is not None and len(pulse['time_us'])==81
                indices=[pulse['time_us'].index(time) for time in (.125,.25,.375)]
                selected=[pulse['time_us'][i] for i in indices]
                low=inverse_transfer(doc['stack'],doc['request']['causal_pulse'],selected,degree=72)
                high=inverse_transfer(doc['stack'],doc['request']['causal_pulse'],selected,degree=96)
                differences=[];errors=[];magnitude_errors=[]
                for i,a,b in zip(indices,low['values'],high['values']):
                    reference=ctx.mpc(b['real'],b['imaginary'])
                    differences.append(abs(reference-ctx.mpc(a['real'],a['imaginary'])))
                    errors.append(abs(ctx.mpc(pulse['rf'][i],pulse['imaginary'][i])-reference))
                    magnitude_errors.append(abs(ctx.mpf(pulse['envelope'][i])-abs(reference)))
                assert max(differences)<ctx.mpf('1e-18')
                diag=pulse['diagnostics'];bound=ctx.mpf(diag['total_error_bound'])
                assert max(errors)<=bound and max(magnitude_errors)<=bound
                components=('analytic_alias_bound','frequency_cutoff_bound','arithmetic_complex_bound','arithmetic_envelope_bound')
                assert all(diag[key]>=0 for key in components)
                assert sum((Fraction(diag[key]) for key in components),Fraction())<=Fraction(diag['total_error_bound'])
                assert 0<bound<=ctx.mpf(doc['request']['causal_pulse']['absolute_tolerance'])
                item.update(selected_indices=indices,coarse_oracle=low,fine_oracle=high,
                    maximum_degree_difference=float(max(differences)),maximum_selected_complex_difference=float(max(errors)),
                    maximum_selected_magnitude_difference=float(max(magnitude_errors)),certificate=diag)
            result['reports'][name]=item
        a,b=raw_reports['dispersive'],raw_reports['water_spacer']
        assert any(layer['kind']=='sls' and layer['thickness_mm']>0 and
                   layer['unrelaxed_modulus_gpa']>layer['relaxed_modulus_gpa'] for layer in a['stack']['layers'])
        assert canonical(a['request']['causal_pulse'])==canonical(b['request']['causal_pulse'])
        assert canonical(a['request']['spectrum'])==canonical(b['request']['spectrum'])
        assert canonical(a['stack']['layers'])==canonical(b['stack']['layers'][1:])
        assert all(canonical(a['stack'][k])==canonical(b['stack'][k]) for k in ('incident','terminal'))
        spacer=b['stack']['layers'][0]
        assert spacer['kind']=='lossless_real' and spacer['thickness_mm']>0
        assert spacer['impedance_mrayl']==a['stack']['incident']['impedance_mrayl']
        assert spacer['sound_speed_m_s']==a['stack']['incident']['sound_speed_m_s']
        delay=ctx.mpf(1000)*ctx.mpf(spacer['thickness_mm'])/ctx.mpf(spacer['sound_speed_m_s'])
        r_differences=[];t_differences=[]
        for i,frequency in enumerate(a['spectrum']['frequency_mhz']):
            p=ctx.exp(-ctx.j*2*ctx.pi*ctx.mpf(frequency)*delay)
            r_differences.append(abs(complex_value(b['spectrum']['reflection'],i)-complex_value(a['spectrum']['reflection'],i)*p*p))
            t_differences.append(abs(complex_value(b['spectrum']['transmission'],i)-complex_value(a['spectrum']['transmission'],i)*p))
        assert max(r_differences)<ctx.mpf('1e-12') and max(t_differences)<ctx.mpf('1e-12')
        observed_change=max(abs(complex_value(b['spectrum']['reflection'],i)-complex_value(a['spectrum']['reflection'],i))
                            for i in range(len(a['spectrum']['frequency_mhz'])))
        assert observed_change>ctx.mpf('1e-5')
        result['matched_front_spacer']={'single_pass_delay_us':ctx.nstr(delay,60),
            'maximum_reflection_double_pass_difference':float(max(r_differences)),
            'maximum_transmission_single_pass_difference':float(max(t_differences)),
            'maximum_observed_complex_reflection_change':float(observed_change),
            'scope':'Discrete-frequency phase relation with every other stack/pulse setting unchanged; ordinary diagnostic.'}
        assert all(get(route).content==before for route,before in catalogs.items())
    unchanged()
    result.update(status='passed',elapsed_seconds=perf_counter()-started,preexisting_files_preserved=len(copied['sha256']),
        report_file_sha256=original_hashes,oracle_scope='Independent p/v transfer and deHoog degree agreement; no independently certified oracle remainder or measured accuracy.')
    with output.open('x',encoding='utf8') as stream:json.dump(result,stream,indent=2)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',required=True)
    for name in ('data-root','native-receipt','copy-receipt','output'):
        parser.add_argument('--'+name,required=True,type=Path)
    result=verify(**vars(parser.parse_args()))
    print(json.dumps({key:result[key] for key in ('status','elapsed_seconds','report_ids','preexisting_files_preserved','reports_created','jobs_created')}))
