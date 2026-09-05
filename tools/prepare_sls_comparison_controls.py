"""Create two explicit assumed-material controls from existing saved SLS reports.

This developer delivery tool publishes exactly two standalone source reports, no
comparisons or acquisition jobs. The output receipt is exclusive and updated
after each accepted response; never repeat a POST after an uncertain outcome.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit

import httpx


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def prepare(base_url, elastic_id, dispersive_id, output):
    if urlsplit(base_url).hostname not in ('127.0.0.1', 'localhost'):
        raise ValueError('Controls require a loopback service.')
    result = {'status': 'preparing', 'source_ids': {}, 'new_sources': {},
              'comparisons_created': 0, 'jobs_created': 0,
              'scope': 'Assumed scalar material controls; not measured material validation.'}
    # An existing receipt may indicate a partially completed publication. Inspect
    # it and the catalog rather than silently creating replacement controls.
    with output.open('x', encoding='utf8') as stream:
        json.dump(result, stream, indent=2)
    started = perf_counter()
    with httpx.Client(base_url=base_url, timeout=120) as client:
        def get(path):
            response = client.get(path)
            response.raise_for_status()
            return response.json()

        assert get('/api/health')['version'] == '0.18.0'
        previous_jobs = {route: canonical(get(route)) for route in ('/api/v2/jobs', '/api/v2/observations/jobs')}
        originals = {role: get(f'/api/v2/sls-acoustics/reports/{identifier}')
                     for role, identifier in (('elastic', elastic_id), ('dispersive', dispersive_id))}
        for source in originals.values():
            assert source['kind'] == 'sls_layered_analysis'
            assert source['report_sha256'] == hashlib.sha256(canonical({k: v for k, v in source.items() if k != 'report_sha256'})).hexdigest()
            assert len(source['request']['stack']['layers']) == 1 and source['causal_pulse'] is not None
        elastic = originals['elastic']['request']['stack']['layers'][0]
        assert elastic['relaxed_modulus_gpa'] == elastic['unrelaxed_modulus_gpa']
        dispersive = originals['dispersive']['request']['stack']['layers'][0]
        assert dispersive['relaxed_modulus_gpa'] < dispersive['unrelaxed_modulus_gpa']
        result['source_ids'].update(elastic=elastic_id, dispersive=dispersive_id)

        for role, field, value in (
            ('elastic', 'relaxation_time_us', elastic['relaxation_time_us'] * 2),
            ('dispersive', 'unrelaxed_modulus_gpa', dispersive['unrelaxed_modulus_gpa'] * 1.125),
        ):
            request = deepcopy(originals[role]['request'])
            old = request['stack']['layers'][0][field]
            assert value != old
            request['stack']['layers'][0][field] = value
            estimate = client.post('/api/v2/sls-acoustics/estimate', json=request)
            estimate.raise_for_status()
            response = client.post('/api/v2/sls-acoustics/reports', json=request)
            response.raise_for_status()
            assert response.status_code == 201
            report = response.json()
            name = 'elastic_inactive_tau' if role == 'elastic' else 'single_modulus'
            result['source_ids'][name] = report['id']
            result['new_sources'][name] = {'id': report['id'], 'original_id': originals[role]['id'],
                'report_sha256': report['report_sha256'], 'changed_field': f'stack.layers[0].{field}',
                'before': old, 'after': value, 'request': request}
            output.write_text(json.dumps(result, indent=2), encoding='utf8')
            assert report['request'] == request
            restored = deepcopy(report['request'])
            restored['stack']['layers'][0][field] = old
            assert restored == originals[role]['request']
            assert report['provenance']['implementation_sha256'] == originals[role]['provenance']['implementation_sha256']
            assert report['provenance']['proof_document'] == originals[role]['provenance']['proof_document']
            assert get(f'/api/v2/sls-acoustics/reports/{report["id"]}') == report
        for role, original in originals.items():
            assert get(f'/api/v2/sls-acoustics/reports/{original["id"]}') == original
        assert all(canonical(get(route)) == expected for route, expected in previous_jobs.items())
    result.update(status='passed', standalone_sources_created=2, elapsed_seconds=perf_counter()-started,
        controls={'self': ['dispersive', 'dispersive'], 'elastic_inactive_tau': ['elastic', 'elastic_inactive_tau'],
                  'single_modulus': ['dispersive', 'single_modulus']})
    output.write_text(json.dumps(result, indent=2), encoding='utf8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--elastic-id', required=True)
    parser.add_argument('--dispersive-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    result = prepare(**vars(parser.parse_args()))
    print(json.dumps({key: result[key] for key in ('status', 'source_ids', 'standalone_sources_created', 'elapsed_seconds')}))
