#!/usr/bin/env python3
"""Validate every process and summarize independent paired-block measurements."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import statistics


def records(text, kind):
    return [dict(field.split('=', 1) for field in line.split(',')[1:] if '=' in field)
            for line in text.splitlines() if line.startswith(kind + ',')]


def geometric_mean(values):
    return math.exp(statistics.mean(math.log(x) for x in values))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    root = args.directory
    index = json.loads((root / 'run-index.json').read_text())
    manifest = json.loads((root / 'manifest.json').read_text())
    issues, blocks, identities = [], {}, {}
    if not manifest.get('binaries_unchanged'):
        issues.append('binary identity changed or final identity check missing')
    if len(index) != 42:
        issues.append('expected 2 seed processes and 40 measurement processes')
    for run in index:
        name = run['name']
        if run['exit_code'] != 0:
            issues.append('%s exit %s' % (name, run['exit_code']))
        if run['phase'] != 'measure':
            continue
        text = (root / (name + '.stdout')).read_text()
        try:
            samples = records(text, 'sample')
            expected, = records(text, 'expected_pattern')
            before, = records(text, 'object_identity')
            after, = records(text, 'object_identity_after')
            summary, = records(text, 'summary')
            if before != after:
                raise ValueError('object identity changed during process')
            size = run['size_mib']
            if identities.setdefault(size, before) != before:
                raise ValueError('object identity differs across paired processes')
            if len(samples) != 10 or any(int(x['bytes']) != size * 1024 * 1024
                                        or x['xxh3'] != expected['xxh3'] for x in samples):
                raise ValueError('missing sample, wrong size, or wrong deterministic payload hash')
            times = sorted(int(x['latency_us']) for x in samples)
            if times[0] <= 0 or int(summary['median_us']) != times[len(times) // 2]:
                raise ValueError('invalid latency or inconsistent printed median')
            if 'recovery_timeout_ms=None' not in text or 'verify_pattern=true' not in text:
                raise ValueError('unexpected recovery/payload-verification mode')
            final, = [x for x in records(text, 'pool_stats') if x['phase'] == 'final']
            if run['variant'] == 'R1':
                want = {'created': 1, 'reused': 10, 'retired': 0, 'rejected': 0,
                        'live': 1, 'connecting': 0, 'active': 0, 'idle': 1, 'retiring': 0}
                if final['enabled'] != 'true' or any(int(final[k]) != v for k, v in want.items()):
                    raise ValueError('unexpected pooled lifecycle counters: %s' % final)
            elif final['enabled'] != 'false':
                raise ValueError('R0 unexpectedly uses pooling')
            key = (size, run['pair'])
            blocks.setdefault(key, {})[run['variant']] = {
                'name': name, 'median_us': times[len(times) // 2], 'samples_us': times,
                'cpu_us': int(summary['cpu_user_us']) + int(summary['cpu_system_us']),
                'peak_rss_kib': int(summary['peak_rss_kb']), 'pool_final': final}
        except (KeyError, ValueError) as error:
            issues.append('%s: %s' % (name, error))
    if len(blocks) != 20 or any(set(blocks.get((size, pair), {})) != {'R0', 'R1'}
                              for size in [16, 64] for pair in range(10)):
        issues.append('paired blocks incomplete or duplicated')
    result = {'state': 'INVALID_OR_INCOMPLETE' if issues else 'VALIDATED_PAIRED_PILOT',
              'issues': issues, 'all_processes': len(index), 'sizes': {},
              'analysis_unit': 'one independent process pair; 10 reads per process are subsamples',
              'ratio_definition': 'R0 process upper-median latency / R1 process upper-median latency; >1 favors R1',
              'scope': 'single physical HCA, 1 route, concurrency1, fixed debug server, release client, warm OS page cache; no inference or dual-HCA claim',
              'adoption': 'pool remains opt-in; pilot does not replace long-running and native fault validation'}
    for size in [16, 64]:
        pairs = [blocks.get((size, pair), {}) for pair in range(10)]
        if any(set(p) != {'R0', 'R1'} for p in pairs):
            result['sizes'][size] = {'state': 'INCOMPLETE', 'ratios_not_reported': True}
            continue
        ratios = [p['R0']['median_us'] / p['R1']['median_us'] for p in pairs]
        rng = random.Random(20261029 + size)
        resampled = sorted(geometric_mean([ratios[rng.randrange(10)] for _ in range(10)])
                           for _ in range(10000))
        gm = geometric_mean(ratios)
        result['sizes'][size] = {
            'independent_pairs': 10, 'paired_ratios': ratios, 'geometric_mean_ratio': gm,
            'paired_bootstrap_95pct_percentile': [resampled[249], resampled[9749]],
            'geometric_mean_latency_reduction_fraction': 1 - 1 / gm,
            'R0_block_medians_us': [p['R0']['median_us'] for p in pairs],
            'R1_block_medians_us': [p['R1']['median_us'] for p in pairs],
            'R0_mean_cpu_us_per_process': statistics.mean(p['R0']['cpu_us'] for p in pairs),
            'R1_mean_cpu_us_per_process': statistics.mean(p['R1']['cpu_us'] for p in pairs),
            'R0_peak_rss_kib_range': [min(p['R0']['peak_rss_kib'] for p in pairs), max(p['R0']['peak_rss_kib'] for p in pairs)],
            'R1_peak_rss_kib_range': [min(p['R1']['peak_rss_kib'] for p in pairs), max(p['R1']['peak_rss_kib'] for p in pairs)],
            'identity': identities.get(size), 'blocks': pairs}
    result['raw_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in sorted(root.iterdir()) if p.suffix in {'.stdout', '.stderr'}}
    (root / 'analysis.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in {'raw_sha256', 'sizes'}}, indent=2))
    for size, data in result['sizes'].items():
        print(size, json.dumps({k: v for k, v in data.items() if k not in {'blocks', 'identity'}}, indent=2))


if __name__ == '__main__':
    main()
