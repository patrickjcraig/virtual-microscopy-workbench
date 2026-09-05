"""Read-only delivery validation of three saved SLS comparison controls.

Reuses native comparison reports; creates no source reports or acquisition jobs.
Checks exact residual arithmetic independently and selected ideal-model residuals
with a separate transfer/inverse implementation. Refuses an existing receipt.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
from time import perf_counter
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.sls_comparison_oracle import check_residual_arithmetic, check_selected_inverse, same_double_axis


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def catalog_reconciliation(copy, root):
    with sqlite3.connect(f'{(Path(copy["source"])/"catalog.sqlite3").resolve().as_uri()}?mode=ro', uri=True) as before, \
            sqlite3.connect(f'{(root/"catalog.sqlite3").resolve().as_uri()}?mode=ro', uri=True) as after:
        assert hashlib.sha256('\n'.join(before.iterdump()).encode()).hexdigest() == copy['catalog_logical_sha256']
        schema = 'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'
        assert before.execute(schema).fetchall() == after.execute(schema).fetchall()
        tables = {row[0] for row in before.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {'jobs', 'batches', 'batch_cases', 'recipes', 'observation_jobs', 'observation_exports'}
        changes = []
        for table in sorted(tables):
            old, new = (db.execute(f'SELECT * FROM {table}').fetchall() for db in (before, after))
            if table not in ('jobs', 'observation_jobs'):
                assert sorted(old, key=repr) == sorted(new, key=repr)
                continue
            columns = [row[1] for row in before.execute(f'PRAGMA table_info({table})')]
            stamp = columns.index('updated_at')
            a, b = ({row[0]: row for row in rows} for rows in (old, new))
            assert a.keys() == b.keys()
            for key, left in a.items():
                right = b[key]
                assert left[:stamp]+left[stamp+1:] == right[:stamp]+right[stamp+1:]
                if left[stamp] != right[stamp]:
                    changes.append({'table': table, 'id': key, 'field': 'updated_at', 'before': left[stamp], 'after': right[stamp]})
        return {'changes': changes, 'changed_timestamp_count': len(changes),
                'running_catalog_sha256': hashlib.sha256('\n'.join(after.iterdump()).encode()).hexdigest(),
                'scope': 'Only existing startup reconciliation timestamps differ from the idle source catalog.'}


def verify(base_url, data_root, native_receipt, controls_receipt, copy_receipt, output):
    if urlsplit(base_url).hostname not in ('127.0.0.1', 'localhost'):
        raise ValueError('Delivery verification requires a loopback service.')
    if output.exists():
        raise FileExistsError(output)
    native, controls, copy = (json.loads(path.read_bytes()) for path in (native_receipt, controls_receipt, copy_receipt))
    assert native['passed'] is True and controls['status'] == 'passed'
    identifiers = native['report_ids']
    assert set(identifiers) == {'self', 'elastic_inactive_tau', 'single_modulus'}
    assert Path(copy['destination']).resolve() == data_root.resolve()
    started = perf_counter()
    reconciliation = catalog_reconciliation(copy, data_root)
    def unchanged():
        assert all(digest(data_root/name) == expected for name, expected in copy['sha256'].items())
        with sqlite3.connect(f'{(data_root/"catalog.sqlite3").resolve().as_uri()}?mode=ro', uri=True) as db:
            assert hashlib.sha256('\n'.join(db.iterdump()).encode()).hexdigest() == reconciliation['running_catalog_sha256']
    unchanged()
    result = {'status': 'running', 'report_ids': identifiers, 'reports_created': 0, 'jobs_created': 0,
              'startup_catalog_reconciliation': reconciliation, 'reports': {}}
    workspace = Path(__file__).resolve().parents[1]
    result['verification_sha256'] = {name: digest(workspace/name) for name in (
        'tools/verify_sls_comparison_delivery.py', 'tools/sls_comparison_oracle.py',
        'tools/sls_independent_oracle.py', 'tools/prepare_sls_comparison_controls.py')}
    result['input_receipt_sha256'] = {str(path): digest(path) for path in (native_receipt, controls_receipt, copy_receipt)}
    oracle_cache = {}
    with httpx.Client(base_url=base_url, timeout=120) as client:
        def get(path):
            response = client.get(path)
            response.raise_for_status()
            return response
        assert get('/api/health').json()['version'] == '0.18.0'
        catalogs = {route: get(route).content for route in ('/api/v2/jobs', '/api/v2/observations/jobs', '/api/v2/sls-comparisons/reports')}
        for name, identifier in identifiers.items():
            path = f'/api/v2/sls-comparisons/reports/{identifier}'
            report = get(path).json()
            assert report['kind'] == 'sls_analysis_comparison'
            assert report['report_sha256'] == hashlib.sha256(canonical({k: v for k, v in report.items() if k != 'report_sha256'})).hexdigest()
            assert get(path+'/export?format=json').json() == report
            limit = csv.field_size_limit()
            try:
                csv.field_size_limit(32*1024**2)
                rows = list(csv.DictReader(io.StringIO(get(path+'/export?format=csv').text)))
            finally:
                csv.field_size_limit(limit)
            exported = {row['field']: json.loads(row['value_json']) for row in rows if row['section'] == 'report'}
            assert exported == report
            a, b = (report['source_snapshots'][report[role]['snapshot_sha256']] for role in ('source_reference', 'source_candidate'))
            for role, source in (('source_reference', a), ('source_candidate', b)):
                assert hashlib.sha256(canonical(source)).hexdigest() == report[role]['snapshot_sha256']
                assert get(f'/api/v2/sls-acoustics/reports/{source["id"]}').json() == source
                assert source['report_sha256'] == hashlib.sha256(canonical({k: v for k, v in source.items() if k != 'report_sha256'})).hexdigest()
            expected_roles = controls['controls'][name]
            assert [a['id'], b['id']] == [controls['source_ids'][role] for role in expected_roles]
            assert same_double_axis(a['spectrum']['frequency_mhz'], b['spectrum']['frequency_mhz'])
            spectral_count = 0
            for field in ('reflection', 'transmission'):
                for part in ('real', 'imag', 'magnitude'):
                    actual = report['spectrum']['difference'][field][part]
                    assert same_double_axis(actual, [y-x for x, y in zip(a['spectrum'][field][part], b['spectrum'][field][part])])
                    spectral_count += len(actual)
            for field in ('reflectance', 'transmittance', 'absorptance'):
                actual = report['spectrum']['difference'][field]
                assert same_double_axis(actual, [y-x for x, y in zip(a['spectrum'][field], b['spectrum'][field])])
                spectral_count += len(actual)
            pulse = report['causal_pulse']
            arithmetic = check_residual_arithmetic(a, b, pulse['difference'], pulse['bounds'])
            independent = check_selected_inverse(a, b, pulse['difference'], pulse['bounds'], cache=oracle_cache)
            cursors = []
            nf, nt = len(a['spectrum']['frequency_mhz']), len(a['causal_pulse']['time_us'])
            for fi, ti in ((0, 0), (nf//2, nt//2), (nf-1, nt-1)):
                view = get(path+f'/view?frequency_index={fi}&time_index={ti}').json()
                assert view['frequency_index'] == fi and view['frequency_mhz'] == a['spectrum']['frequency_mhz'][fi]
                assert view['time_index'] == ti and view['time_us'] == a['causal_pulse']['time_us'][ti]
                assert view['causal_pulse']['bounds'] == pulse['bounds'] and view['causal_pulse']['gate'] == pulse['gate']
                for role, source in (('reference', a), ('candidate', b)):
                    assert view['spectrum'][role]['reflection']['real'] == source['spectrum']['reflection']['real'][fi]
                    for key in ('rf', 'imaginary', 'envelope'):
                        assert view['causal_pulse'][role][key] == source['causal_pulse'][key][ti]
                        assert view['causal_pulse']['difference'][key] == pulse['difference'][key][ti]
                cursors.append({'frequency_index': fi, 'time_index': ti})
            assert get(path).json() == report
            gate = pulse['gate']
            times = a['causal_pulse']['time_us']
            selected = [i for i, t in enumerate(times) if gate['requested_start_us'] <= t <= gate['requested_end_us']]
            assert selected == list(range(gate['start_index'], gate['stop_index_exclusive']))
            assert len(selected) == gate['sample_count'] and times[selected[0]] == gate['actual_start_us'] and times[selected[-1]] == gate['actual_end_us']
            if name == 'self':
                assert len(report['source_snapshots']) == 1 and a == b
                assert all(value == 0 for values in pulse['difference'].values() for value in values)
                assert pulse['bounds']['source_sum'] > 0
            else:
                from copy import deepcopy
                restored = deepcopy(b['request'])
                field = 'relaxation_time_us' if name == 'elastic_inactive_tau' else 'unrelaxed_modulus_gpa'
                restored['stack']['layers'][0][field] = a['request']['stack']['layers'][0][field]
                assert restored == a['request']
                changed = [item['path'] for item in report['parameter_differences'] if item['path'].startswith('request.')]
                assert changed == [f'request.stack.layers[0].{field}']
                if name == 'elastic_inactive_tau':
                    for source in (a, b):
                        layer = source['stack']['layers'][0]
                        assert layer['relaxed_modulus_gpa'] == layer['unrelaxed_modulus_gpa']
                    assert max(abs(x) for key in ('rf', 'imaginary') for x in pulse['difference'][key]) <= pulse['bounds']['complex_total']
            result['reports'][name] = {'report_sha256': report['report_sha256'], 'json_csv_roundtrip': True,
                'source_ids': [a['id'], b['id']], 'unique_snapshots': len(report['source_snapshots']),
                'spectral_subtractions_checked': spectral_count, 'arithmetic': arithmetic, 'independent_inverse': independent,
                'gate': gate, 'verified_cursors': cursors, 'resources': report['resources'], 'parameter_differences': report['parameter_differences']}
        assert all(get(route).content == expected for route, expected in catalogs.items())
    unchanged()
    result.update(status='passed', elapsed_seconds=perf_counter()-started,
                  preexisting_files_preserved=len(copy['sha256']), independent_source_oracles=oracle_cache)
    with output.open('x', encoding='utf8') as stream:
        json.dump(result, stream, indent=2)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    for argument in ('data-root', 'native-receipt', 'controls-receipt', 'copy-receipt', 'output'):
        parser.add_argument('--'+argument, required=True, type=Path)
    answer = verify(**vars(parser.parse_args()))
    print(json.dumps({key: answer[key] for key in ('status', 'elapsed_seconds', 'report_ids', 'reports_created', 'jobs_created', 'preexisting_files_preserved')}))
