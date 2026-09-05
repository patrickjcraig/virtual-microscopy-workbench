"""Read-only SLS delivery checks against independent time inversion and exports.

Use the three reports already created by the native verifier. This tool creates
no reports, acquisitions or derived jobs and refuses an existing output file.
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
from mpmath import mp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.sls_independent_oracle import inverse_transfer
from virtual_microscopy.sls_reports import SLSReportStore


def verify(base_url, data_root, native_receipt, copy_receipt, output):
    if urlsplit(base_url).hostname not in ('127.0.0.1', 'localhost'):
        raise ValueError('Delivery verification requires a loopback service.')
    if output.exists():
        raise FileExistsError(output)
    native = json.loads(native_receipt.read_bytes())
    assert native['passed'] is True
    identifiers = native['report_ids']
    assert set(identifiers) == {'frequency', 'dispersive', 'elastic'}
    original = json.loads(copy_receipt.read_bytes())
    assert Path(original['destination']).resolve() == data_root.resolve()
    started = perf_counter()
    result = {'status': 'running', 'scope': 'Synthetic manually assumed standalone material controls; no material calibration.',
              'reports_created': 0, 'jobs_created': 0, 'report_ids': identifiers, 'reports': {}}

    def digest(path):
        with path.open('rb') as source:
            return hashlib.file_digest(source, 'sha256').hexdigest()

    # The existing manager rechecks saved datasets on startup and refreshes only
    # their catalog updated_at values. Account for that migration explicitly;
    # after this snapshot, not even timestamps may change during verification.
    source_catalog = Path(original['source'])/'catalog.sqlite3'
    with sqlite3.connect(f'{source_catalog.resolve().as_uri()}?mode=ro', uri=True) as source_db, \
            sqlite3.connect(f'{(data_root/"catalog.sqlite3").resolve().as_uri()}?mode=ro', uri=True) as active_db:
        source_dump = '\n'.join(source_db.iterdump())
        assert hashlib.sha256(source_dump.encode()).hexdigest() == original['catalog_logical_sha256']
        schema_query = 'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'
        assert source_db.execute(schema_query).fetchall() == active_db.execute(schema_query).fetchall()
        expected_tables = {'jobs','batches','batch_cases','recipes','observation_jobs','observation_exports'}
        assert {row[0] for row in source_db.execute("SELECT name FROM sqlite_master WHERE type='table'")} == expected_tables
        reconciliation = []
        for table in sorted(expected_tables):
            source_rows = source_db.execute(f'SELECT * FROM {table}').fetchall()
            active_rows = active_db.execute(f'SELECT * FROM {table}').fetchall()
            if table in ('jobs','observation_jobs'):
                columns = [row[1] for row in source_db.execute(f'PRAGMA table_info({table})')]
                stamp = columns.index('updated_at')
                source_map, active_map = ({row[0]: row for row in rows} for rows in (source_rows,active_rows))
                assert source_map.keys() == active_map.keys()
                for key, previous in source_map.items():
                    current = active_map[key]
                    assert previous[:stamp]+previous[stamp+1:] == current[:stamp]+current[stamp+1:]
                    if previous[stamp] != current[stamp]:
                        reconciliation.append({'table':table,'id':key,'field':'updated_at','before':previous[stamp],'after':current[stamp]})
            else:
                assert sorted(source_rows, key=repr) == sorted(active_rows, key=repr)
        catalog_after_startup = hashlib.sha256('\n'.join(active_db.iterdump()).encode()).hexdigest()
    result['startup_catalog_reconciliation'] = {'changed_timestamp_count':len(reconciliation),'changes':reconciliation,
        'copy_catalog_sha256':original['catalog_logical_sha256'],'running_catalog_sha256':catalog_after_startup,
        'scope':'Existing manager startup refreshed catalog updated_at only; IDs, state, progress, requests and all other table values unchanged.'}

    def check_original():
        for name, expected in original['sha256'].items():
            if digest(data_root/name) != expected:
                raise AssertionError(f'Pre-existing data changed: {name}')
        with sqlite3.connect(f'{(data_root/"catalog.sqlite3").resolve().as_uri()}?mode=ro', uri=True) as db:
            logical = '\n'.join(db.iterdump())
        assert hashlib.sha256(logical.encode()).hexdigest() == catalog_after_startup

    check_original()
    store = SLSReportStore(data_root)
    stored_hashes = {kind: digest(data_root/'sls-reports'/f'{identifier}.json') for kind,identifier in identifiers.items()}
    with httpx.Client(base_url=base_url, timeout=120) as client:
        def get(path):
            response = client.get(path)
            response.raise_for_status()
            return response

        assert get('/api/health').json()['version'] == '0.17.0'
        for kind, identifier in identifiers.items():
            report = get(f'/api/v2/sls-acoustics/reports/{identifier}').json()
            assert report == store.read(identifier)
            assert get(f'/api/v2/sls-acoustics/reports/{identifier}/export?format=json').json() == report
            previous_limit = csv.field_size_limit(64*1024**2)
            try:
                exported = get(f'/api/v2/sls-acoustics/reports/{identifier}/export?format=csv').text
                rows = list(csv.DictReader(io.StringIO(exported)))
                assert all(row['section']=='report' for row in rows)
                assert len(rows)==len(report) and {row['field']:json.loads(row['value_json']) for row in rows}==report
            finally:
                csv.field_size_limit(previous_limit)
            assert report['source_status'] == 'manual_assumptions'
            assert report['provenance']['proof_document']['sha256'] == digest(Path(__file__).resolve().parents[1]/'docs/SLS_MATERIAL_PROOF.md')
            for name, expected in report['provenance']['implementation_sha256'].items():
                assert digest(Path(__file__).resolve().parents[1]/'virtual_microscopy'/name) == expected
            item = {'report_sha256': report['report_sha256'], 'serialized_bytes': (data_root/'sls-reports'/f'{identifier}.json').stat().st_size,
                    'json_csv_roundtrip': True, 'frequency_samples': len(report['spectrum']['frequency_mhz']),
                    'resource_estimate': report['resources']}
            pulse = report['causal_pulse']
            if pulse is None:
                assert kind == 'frequency'
                assert len(report['stack']['layers']) == 2
            else:
                n = len(pulse['time_us'])
                indices = sorted({round((n-1)*fraction) for fraction in (.4,.6,.8,1.)})
                times = [pulse['time_us'][index] for index in indices]
                # The 50 MHz delivery fixtures require more inversion terms
                # than the shorter 10 MHz unit controls. Require convergence
                # explicitly rather than accepting a low-degree reference.
                coarse = inverse_transfer(report['stack'], report['request']['causal_pulse'], times, degree=96)
                fine = inverse_transfer(report['stack'], report['request']['causal_pulse'], times, degree=128)
                with mp.workdps(60):
                    differences, errors, magnitude_errors = [], [], []
                    for index, lower, upper in zip(indices, coarse['values'], fine['values']):
                        reference = mp.mpc(upper['real'], upper['imaginary'])
                        differences.append(abs(reference-mp.mpc(lower['real'], lower['imaginary'])))
                        errors.append(abs(mp.mpc(pulse['rf'][index], pulse['imaginary'][index])-reference))
                        magnitude_errors.append(abs(mp.mpf(pulse['envelope'][index])-abs(reference)))
                    assert max(differences) < mp.mpf('1e-14')
                    bound = mp.mpf(pulse['diagnostics']['total_error_bound'])
                    assert max(errors) <= bound and max(magnitude_errors) <= bound
                    item.update(time_samples=n, selected_indices=indices, coarse_oracle=coarse, fine_oracle=fine,
                                maximum_degree_difference=float(max(differences)), maximum_selected_complex_difference=float(max(errors)),
                                maximum_selected_magnitude_difference=float(max(magnitude_errors)), certificate=pulse['diagnostics'])
                if kind == 'elastic':
                    assert all(material['zero_relaxation'] for material in report['spectrum']['materials'])
                    assert all(value==0 for material in report['spectrum']['materials'] for value in material['attenuation_db_mm'])
            result['reports'][kind] = item
    check_original()
    assert all(digest(data_root/'sls-reports'/f'{identifiers[kind]}.json') == expected for kind, expected in stored_hashes.items())
    result.update(status='passed', elapsed_seconds=perf_counter()-started,
                  preexisting_files_preserved=len(original['sha256']), catalog_logical_sha256=catalog_after_startup,
                  report_file_sha256=stored_hashes,
                  oracle_scope='Independent de Hoog degree convergence and numerical agreement; no independently certified oracle remainder.')
    with output.open('x', encoding='utf8') as stream:
        json.dump(result, stream, indent=2)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--data-root', required=True, type=Path)
    parser.add_argument('--native-receipt', required=True, type=Path)
    parser.add_argument('--copy-receipt', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    answer = verify(**vars(args))
    print(json.dumps({key:answer[key] for key in ('status','elapsed_seconds','report_ids','preexisting_files_preserved','reports_created','jobs_created')}))
