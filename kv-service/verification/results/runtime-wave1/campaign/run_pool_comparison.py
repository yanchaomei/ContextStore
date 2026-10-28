#!/usr/bin/env python3
"""Run the frozen paired-process R0/R1 pilot on the remote client host."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bin-dir', required=True, type=Path)
    parser.add_argument('--benchmark-name', default='cs-rail-read-bench-recovery-off')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--coordinator', default='http://10.0.0.64:25351')
    parser.add_argument('--listener', default='10.0.0.64:25353')
    parser.add_argument('--device', default='mlx5_1')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    benchmark = args.bin_dir / args.benchmark_name
    seeder = args.bin_dir / 'cs-bench'
    binaries = {str(p): sha256(p) for p in [benchmark, seeder]}
    runs = []

    def run(name, command, attributes):
        start = time.time_ns()
        with (args.output / (name + '.stdout')).open('wb') as out:
            with (args.output / (name + '.stderr')).open('wb') as err:
                try:
                    result = subprocess.run(command, stdout=out, stderr=err, timeout=240)
                    code = result.returncode
                except subprocess.TimeoutExpired:
                    code = 124
        record = dict(name=name, argv=[str(x) for x in command], exit_code=code,
                      started_unix_ns=start, ended_unix_ns=time.time_ns(), **attributes)
        runs.append(record)
        (args.output / 'run-index.json').write_text(json.dumps(runs, indent=2) + '\n')
        print(json.dumps(record), flush=True)
        return code

    manifest = {'protocol': 'experiment-freeze.json, 2026-10-29',
                'environment': 'physical single HCA at each endpoint; one route',
                'binary_sha256': binaries,
                'measured_implementation': 'same client binary, pool OFF/ON only; recovery OFF',
                'server_build': 'fixed debug baseline, CS_FORCE_DISK_READ=1; OS page cache not dropped',
                'samples': 'one warmup + ten measured reads per independent process',
                'order': 'alternate pair order; all runs retained; no outlier removal'}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    for mib in [16, 64]:
        prefix = 'wave1-pool-%s-%d-' % (args.output.name, mib)
        code = run('seed-%d' % mib, [str(seeder), '--endpoint', args.coordinator,
                   '--layers', '1', '--layer-mb', str(mib), '--concurrency', '1',
                   '--combined', '--combined-count', '1', '--stream',
                   '--stream-chunk-mb', '2', '--prefix-base', prefix, '--only-put'],
                   {'phase': 'seed', 'size_mib': mib})
        if code:
            continue
        for pair in range(10):
            order = ['R0', 'R1'] if pair % 2 == 0 else ['R1', 'R0']
            for position, variant in enumerate(order):
                command = [str(benchmark), '--coordinator', args.coordinator,
                           '--namespace', 'rust-bench', '--object-key', prefix + '0/__combined__',
                           '--rail', 'single,%s,%s,%s,1,3' % (args.device, args.listener, args.listener),
                           '--environment', 'physical', '--warmup', '1', '--iterations', '10',
                           '--concurrency', '1', '--verify-pattern',
                           '--connection-pool-size', '1' if variant == 'R1' else '0']
                run('%d-pair%02d-%s' % (mib, pair, variant), command,
                    {'phase': 'measure', 'size_mib': mib, 'pair': pair,
                     'order_position': position, 'variant': variant})
    manifest['binary_sha256_after'] = {str(p): sha256(p) for p in [benchmark, seeder]}
    manifest['binaries_unchanged'] = binaries == manifest['binary_sha256_after']
    manifest['all_processes_successful'] = all(r['exit_code'] == 0 for r in runs)
    manifest['run_count'] = len(runs)
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    main()
