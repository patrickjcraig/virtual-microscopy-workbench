"""Read-only validation of native material-assignment and column controls.

Uses saved API products only. No acquisition, propagation, source analysis or
assignment is created. Refuses overwriting an existing verification receipt.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from time import perf_counter
from urllib.parse import urlsplit
from uuid import UUID

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.material_assignment_oracle import MATERIAL_IDS, PARAMETERS, canonical, content_digest, check_segments, same_number
from tools.verify_sls_comparison_delivery import catalog_reconciliation, digest


def verify_assignment(doc):
    assert doc['kind'] == 'sls_material_assignment' and doc['id'] == doc['assignment_id']
    assert doc['report_sha256'] == content_digest({k: v for k, v in doc.items() if k != 'report_sha256'})
    assert doc['propagation_available'] is False
    snapshots, kinds = doc['snapshots'], doc['snapshot_kinds']
    assert set(snapshots) == set(kinds) and len(snapshots) <= 7
    for sha, value in snapshots.items(): assert content_digest(value) == sha
    twin = snapshots[doc['twin_sha256']]
    assert kinds[doc['twin_sha256']] == 'twin'
    full = {k: v for k, v in doc['request'].items() if k != 'twin_sha256'}
    full['twin'] = twin
    assert content_digest(full) == doc['input_request_sha256']
    inventory = {o['material'] for o in twin['objects'] if full['include_defects'] or o['role'] != 'defect'}
    required = inventory if full['coverage_scope'] == 'all_included' else set(full['required_material_ids'])
    supplied = {r['material_id'] for r in doc['bindings']}
    assert len(supplied) == len(doc['bindings']) and supplied <= inventory and required <= inventory
    coverage = doc['coverage']
    assert set(coverage['inventory_material_ids']) == inventory
    assert set(coverage['required_material_ids']) == required
    assert set(coverage['supplied_material_ids']) == supplied
    assert set(coverage['missing_material_ids']) == required-supplied
    assert coverage['complete'] == (not required-supplied)
    references = set()
    assert len(doc['bindings']) == len(full['bindings'])
    for record, authored in zip(doc['bindings'], full['bindings']):
        assert record['assignment_sha256'] == content_digest({k: v for k, v in record.items() if k != 'assignment_sha256'})
        assert record['parameters_sha256'] == content_digest(record['parameters'])
        assert set(record['parameters']) == set(PARAMETERS)
        assert all(record[k] == authored[k] for k in ('material_id', 'name', 'note'))
        if authored['origin']['kind'] == 'manual':
            expected = authored['origin']
        else:
            sha = record['origin']['source_snapshot_sha256']
            references.add(sha)
            assert kinds[sha] == 'sls_report'
            source = snapshots[sha]
            assert source['id'] == authored['origin']['report_id']
            assert source['report_sha256'] == content_digest({k: v for k, v in source.items() if k != 'report_sha256'})
            expected = source['stack']['layers'][authored['origin']['layer_index']]
            assert record['origin']['layer_index'] == authored['origin']['layer_index']
        assert all(same_number(record['parameters'][k], expected[k]) for k in PARAMETERS)
        assert record['evidence'] == 'manual_scalar_longitudinal_assumption'
    assert references == {sha for sha, kind in kinds.items() if kind == 'sls_report'}
    assert set(kinds.values()) <= {'twin', 'sls_report'}
    assert doc['ambient_policy']['material_id'] == 'ambient_water'
    assert doc['ambient_policy']['evidence'] == 'uncalibrated_nominal_assumption'
    return twin, {'bindings_checked': len(doc['bindings']), 'complete_snapshots': len(snapshots),
        'required_ids': sorted(required), 'missing_ids': sorted(required-supplied), 'coverage_complete': not required-supplied}


def verify(base_url, data_root, native_receipt, copy_receipt, output):
    if urlsplit(base_url).hostname not in ('127.0.0.1', 'localhost'):
        raise ValueError('Delivery verification requires a loopback instance.')
    if output.exists(): raise FileExistsError(output)
    native, copied = (json.loads(p.read_bytes()) for p in (native_receipt, copy_receipt))
    assert native['passed'] and native['jobs_created'] == 0
    ids = native['assignment_ids']
    assert set(ids) == {'hbm_unassigned', 'coupon_complete', 'coupon_missing_air', 'report_layer'}
    assert len(set(ids.values())) == 4
    assert all(str(UUID(identifier)) == identifier for identifier in ids.values())
    expected_columns = {
        'hbm_off_bump': ('hbm_unassigned', 49.42734375, 39.876953125),
        'hbm_bump_core': ('hbm_unassigned', 49.47421875, 39.947265625),
        'coupon_complete': ('coupon_complete', 1., 1.),
        'coupon_missing_air': ('coupon_missing_air', 1., 1.),
        'report_layer': ('report_layer', 1., 1.),
    }
    assert len(native['columns']) == 5
    assert {c['name'] for c in native['columns']} == set(expected_columns)
    assert len({c['column_id'] for c in native['columns']}) == 5
    for col in native['columns']:
        assert str(UUID(col['column_id'])) == col['column_id']
        role, x, y = expected_columns[col['name']]
        assert col['assignment_id'] == ids[role]
        assert same_number(col['x_mm'], x) and same_number(col['y_mm'], y)
    assert Path(copied['destination']).resolve() == data_root.resolve()
    started = perf_counter()
    reconciliation = catalog_reconciliation(copied, data_root)
    def unchanged():
        assert all(digest(data_root/path) == sha for path, sha in copied['sha256'].items())
        with sqlite3.connect(f'{(data_root/"catalog.sqlite3").resolve().as_uri()}?mode=ro', uri=True) as db:
            import hashlib
            assert hashlib.sha256('\n'.join(db.iterdump()).encode()).hexdigest() == reconciliation['running_catalog_sha256']
    unchanged()
    # Freeze every native-created file before the first HTTP call, including
    # the first read/export of that file. The isolated copy predates these nine.
    evidence_files = [data_root/'material-assignments'/f'{identifier}.json' for identifier in ids.values()]
    evidence_files += [data_root/'material-assignment-columns'/c['assignment_id']/f'{c["column_id"]}.json' for c in native['columns']]
    saved_hashes = {str(file): digest(file) for file in evidence_files}
    workspace = Path(__file__).resolve().parents[1]
    result = {'status': 'running', 'assignment_ids': ids, 'assignments_created': 0, 'columns_created': 0, 'jobs_created': 0,
        'input_receipt_sha256': {str(p): digest(p) for p in (native_receipt, copy_receipt)},
        'verification_sha256': {p: digest(workspace/p) for p in ('tools/material_assignment_oracle.py',
            'tools/verify_material_assignment_delivery.py', 'tools/verify_sls_comparison_delivery.py')},
        'startup_catalog_reconciliation': reconciliation, 'assignments': {}, 'columns': []}
    base = '/api/v2/material-assignments'
    with httpx.Client(base_url=base_url, timeout=90) as client:
        def get(route):
            response = client.get(route)
            response.raise_for_status()
            return response
        assert get('/api/health').json()['version'] == '0.19.0'
        routes = ['/api/v2/jobs', '/api/v2/observations/jobs', '/api/v2/sls-acoustics/reports',
                  '/api/v2/sls-comparisons/reports', base]
        before_routes = {path: get(path).content for path in routes}
        documents = {}
        for name, identifier in ids.items():
            path = f'{base}/{identifier}'
            doc = get(path).json()
            assert doc['id'] == identifier
            twin, evidence = verify_assignment(doc)
            assert canonical(get(path+'/export').json()) == canonical(doc)
            file = data_root/'material-assignments'/f'{identifier}.json'
            assert canonical(json.loads(file.read_bytes())) == canonical(doc)
            assert digest(file) == saved_hashes[str(file)]
            for sha, kind in doc['snapshot_kinds'].items():
                if kind == 'sls_report':
                    source = doc['snapshots'][sha]
                    assert canonical(get(f'/api/v2/sls-acoustics/reports/{source["id"]}').json()) == canonical(source)
            documents[identifier] = doc
            result['assignments'][name] = {'id': identifier, 'report_sha256': doc['report_sha256'],
                'twin_sha256': doc['twin_sha256'], 'geometry': doc['geometry'], **evidence}
        a = documents[ids['hbm_unassigned']]
        assert not a['bindings'] and not a['coverage']['complete'] and a['geometry']['hbm_assembly_count'] == 6
        intact, defect = (documents[ids[name]] for name in ('coupon_complete', 'coupon_missing_air'))
        assert intact['coverage']['complete'] and not defect['coverage']['complete']
        assert intact['twin_sha256'] == defect['twin_sha256']
        assert not intact['request']['include_defects'] and defect['request']['include_defects']
        assert canonical(intact['bindings']) == canonical(defect['bindings'])
        assert defect['coverage']['missing_material_ids'] == ['air']
        assert any(r['origin']['kind'] == 'report_layer' for r in documents[ids['report_layer']]['bindings'])
        for locator in native['columns']:
            parent_id, column_id = locator['assignment_id'], locator['column_id']
            parent = documents[parent_id]
            path = f'{base}/{parent_id}/columns/{column_id}'
            col = get(path).json()
            assert col['kind'] == 'sls_material_column' and col['id'] == col['column_id'] == column_id
            assert col['report_sha256'] == content_digest({k: v for k, v in col.items() if k != 'report_sha256'})
            assert col['assignment_id'] == parent_id and col['assignment_sha256'] == parent['report_sha256']
            assert canonical({**col['assignment_record'], 'snapshots': col['snapshots'], 'snapshot_kinds': col['snapshot_kinds']}) == canonical(parent)
            assert canonical(get(path+'/export').json()) == canonical(col)
            assert col['propagation_available'] is False
            _, expected_x, expected_y = expected_columns[locator['name']]
            assert same_number(col['x_mm'], expected_x) and same_number(col['y_mm'], expected_y)
            twin = parent['snapshots'][parent['twin_sha256']]
            checks = check_segments(twin, col['x_mm'], col['y_mm'], parent['request']['include_defects'], col['segments'])
            bindings = {b['material_id']: b['assignment_sha256'] for b in parent['bindings']}
            required = {s['material_id'] for s in col['segments']} - {'ambient_water'}
            missing = required-set(bindings)
            assert set(col['coverage']['missing_material_ids']) == missing
            assert set(col['coverage']['required_material_ids']) == required
            assert set(col['coverage']['supplied_material_ids']) == required.intersection(bindings)
            assert col['coverage']['complete'] == (not missing)
            for index, segment in enumerate(col['segments']):
                assert segment['index'] == index
                if segment['material_id'] == 'ambient_water':
                    assert segment['material_label'] == 0 and segment['assignment_sha256'] is None
                    assert segment['coverage_status'] == 'ambient_policy'
                else:
                    assert segment['material_label'] == MATERIAL_IDS.index(segment['material_id'])+1
                    assert segment['assignment_sha256'] == bindings.get(segment['material_id'])
                    assert segment['coverage_status'] == ('missing' if segment['material_id'] in missing else 'assigned')
            file = data_root/'material-assignment-columns'/parent_id/f'{column_id}.json'
            assert canonical(json.loads(file.read_bytes())) == canonical(col)
            assert digest(file) == saved_hashes[str(file)]
            result['columns'].append({**locator, 'x_mm': col['x_mm'], 'y_mm': col['y_mm'],
                'report_sha256': col['report_sha256'], 'missing_ids': sorted(missing), **checks})
        assert any(c['assignment_id'] == ids['hbm_unassigned'] and c['segments_checked'] > 8 for c in result['columns'])
        assert any(c['assignment_id'] == ids['coupon_missing_air'] and c['missing_ids'] == ['air'] for c in result['columns'])
        assert all(get(path).content == raw for path, raw in before_routes.items())
        assert all(digest(Path(path)) == sha for path, sha in saved_hashes.items())
    unchanged()
    result.update(status='passed', elapsed_seconds=perf_counter()-started, saved_file_sha256=saved_hashes,
        preexisting_files_preserved=len(copied['sha256']), columns_checked=len(result['columns']),
        scope='Independent numerical geometry and exact provenance/binding checks; no measured calibration or propagation accuracy claim.')
    with output.open('x', encoding='utf8') as stream: json.dump(result, stream, indent=2)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--native-receipt', type=Path, required=True)
    parser.add_argument('--copy-receipt', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    receipt = verify(args.base_url, args.data_root, args.native_receipt, args.copy_receipt, args.output)
    print(json.dumps({k: receipt[k] for k in ('status', 'elapsed_seconds', 'columns_checked', 'preexisting_files_preserved')}))
