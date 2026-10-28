#!/usr/bin/env python3
"""Replay the actual independent local pilot audit (553 checks and paired math).

Packages the inline Python used during the audit, including the subsequent
80 corrected hash checks and four server-continuity checks. The initial
incompatible modern-XXH3 diagnostic remains in the original receipt's
audit_history; final payload checks use the independently transcribed pinned
algorithm, exactly as the corrected audit did. No new validation method is
substituted here. Packaging changes: relative input discovery, output option,
calling replay_twox_hash, and comparing replay values with the original receipt.

Uses Python stdlib plus the already-present scipy.stats for the same diagnostic
t interval. It does not install dependencies or change the repository, raw
data, runner, analyzer, or original receipts. Default output is reviews/replay/.
Run: python3 /absolute/path/to/reviews/replay_pilot_audit.py
"""
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import math
import random
import statistics
from scipy.stats import t
from replay_twox_hash import compute_receipt

HERE = Path(__file__).resolve().parent
base = HERE.parent
root = base / 'campaign/pilot-v3'
repo = base.parent.parent / 'work/ContextStore-runtime-wave1'


def main(output):
    a = json.loads((root / 'analysis.json').read_text())
    ix = json.loads((root / 'run-index.json').read_text())
    man = json.loads((root / 'manifest.json').read_text())
    freeze = json.loads((base / 'experiment-freeze.json').read_text())
    pin = json.loads((base / 'campaign/pilot-source-freeze-v3.json').read_text())
    original = json.loads((HERE / 'pilot-evidence-math.json').read_text())
    checks = {}

    def ck(label, condition):
        checks[label] = bool(condition)

    def rec(text, kind):
        return [dict(f.split('=', 1) for f in s.split(',')[1:] if '=' in f)
                for s in text.splitlines() if s.startswith(kind + ',')]

    def one(text, kind):
        out = rec(text, kind)
        assert len(out) == 1, (kind, len(out))
        return out[0]

    def sha(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()

    expected_names = []
    for size in (16, 64):
        expected_names.append(f'seed-{size}')
        for pair in range(10):
            for v in (('R0', 'R1') if pair % 2 == 0 else ('R1', 'R0')):
                expected_names.append(f'{size}-pair{pair:02d}-{v}')
    ck('exact_42_runs_in_expected_order', [r['name'] for r in ix] == expected_names)
    ck('unique_run_names', len({r['name'] for r in ix}) == 42)
    ck('all_42_exit_zero', all(r['exit_code'] == 0 for r in ix))
    ck('timestamps_positive_sequential_no_overlap', all(r['ended_unix_ns'] > r['started_unix_ns'] for r in ix)
       and all(b['started_unix_ns'] >= aa['ended_unix_ns'] for aa, b in zip(ix, ix[1:])))
    raws = {p.name: sha(p) for p in root.iterdir() if p.suffix in ('.stdout', '.stderr')}
    ck('exact_raw_file_inventory', set(raws) == {n + s for n in expected_names for s in ('.stdout', '.stderr')})
    ck('all_84_raw_sha256_match_analysis', raws == a['raw_sha256'])
    ck('all_42_stderr_empty', all((root / (r['name'] + '.stderr')).stat().st_size == 0 for r in ix))
    ck('manifest_counts_and_outcomes', man['run_count'] == 42 and man['all_processes_successful'] and man['binaries_unchanged'])
    ck('client_seeder_hashes_before_after', man['binary_sha256'] == man['binary_sha256_after'])
    bins = man['binary_sha256']
    bench_path = next(p for p in bins if p.endswith('/cs-rail-read-bench'))
    seed_path = next(p for p in bins if p.endswith('/cs-bench'))
    ck('client_seeder_hashes_match_source_pin', bins[bench_path] == pin['benchmark_sha256'] and bins[seed_path] == pin['seeder_sha256'])
    source_checks = {n: {'expected': want, 'actual': sha(repo / n)} for n, want in pin['source_sha256'].items()}
    ck('all_9_source_and_config_pins_match_current_checkout', len(source_checks) == 9
       and all(v['actual'] == v['expected'] for v in source_checks.values()))
    blocks, identities = {}, {}
    hash_receipt = compute_receipt()
    hashes = hash_receipt['deterministic_pattern_i_mod_251_hashes']
    counts = {'measured_reads': 0, 'warmup_reads_indicated_by_rail_counters': 0,
              'pooled_processes': 0, 'unpooled_processes': 0}
    for r in ix:
        size, name = r['size_mib'], r['name']
        txt = (root / (name + '.stdout')).read_text()
        n = size * 1024 * 1024
        if r['phase'] == 'seed':
            want = [seed_path, '--endpoint', 'http://10.0.0.64:25351', '--layers', '1', '--layer-mb', str(size),
                    '--concurrency', '1', '--combined', '--combined-count', '1', '--stream', '--stream-chunk-mb', '2',
                    '--prefix-base', f'wave1-pool-pilot-v3-{size}-', '--only-put']
            ck(name + '_argv', r['argv'] == want)
            ck(name + '_successful_put_output', f'[PUT combined    ] 1 x {size}MB = {size}MB' in txt)
            continue
        pair, v = r['pair'], r['variant']
        pool = 1 if v == 'R1' else 0
        want = [bench_path, '--coordinator', 'http://10.0.0.64:25351', '--namespace', 'rust-bench',
                '--object-key', f'wave1-pool-pilot-v3-{size}-0/__combined__', '--rail',
                'single,mlx5_1,10.0.0.64:25353,10.0.0.64:25353,1,3', '--environment', 'physical',
                '--warmup', '1', '--iterations', '10', '--concurrency', '1', '--verify-pattern',
                '--connection-pool-size', str(pool)]
        ck(name + '_argv_matches_frozen_design', r['argv'] == want)
        expected_order = ('R0', 'R1') if pair % 2 == 0 else ('R1', 'R0')
        ck(name + '_pair_order_position', expected_order[r['order_position']] == v)
        b, aft = one(txt, 'object_identity'), one(txt, 'object_identity_after')
        ex, samples, summ, rs = one(txt, 'expected_pattern'), rec(txt, 'sample'), one(txt, 'summary'), one(txt, 'rail_stats')
        ck(name + '_identity_stable', b == aft and identities.setdefault(size, b) == b)
        ck(name + '_identity_shape', b['generation'] == '1' and b['layout_version'] == '1'
           and int(b['stripe_count']) == size // 4 and int(b['chunk_size']) == 4 * 1024 * 1024)
        ck(name + '_independent_expected_payload_hash', ex == {'pattern': 'index_mod_251', 'bytes': str(n), 'xxh3': hashes[size]})
        ck(name + '_all_ten_samples_complete_verified', len(samples) == 10
           and [int(s['iteration']) for s in samples] == list(range(1, 11))
           and all(s['xxh3'] == hashes[size] and int(s['bytes']) == n and s['environment'] == 'physical'
                   and s['rails'] == '1' and int(s['latency_us']) > 0 for s in samples))
        ck(name + '_options_match', f'mode,connection_pool_size={pool},recovery_timeout_ms=None,verify_pattern=true,warmup=1,iterations=10,concurrency=1' in txt
           and 'mode,direct_staging=false' in txt and 'mode,contiguous_schedule=false' in txt)
        ck(name + '_one_route_and_effective_mode', len(rec(txt, 'route')) == 1
           and one(txt, 'route') == {'id': 'single', 'device': 'mlx5_1', 'port': '1', 'gid': '3', 'weight': '1',
                                    'advertised': '10.0.0.64:25353', 'listener': '10.0.0.64:25353'}
           and one(txt, 'mode_effective') == {'one': '10', 'packed_greedy': '0', 'packed_contiguous': '0', 'direct': '0'})
        ck(name + '_assignment', f'plan_assignment,id=single,stripes={list(range(size // 4))},bytes={n}' in txt)
        times = sorted(int(s['latency_us']) for s in samples)
        ck(name + '_summary', int(summ['iters']) == 10 and int(summ['bytes_per_iter']) == n
           and int(summ['median_us']) == times[5] and summ['rail_bytes'] == f'[{n * 10}]'
           and all(int(summ[k]) >= 0 for k in ('cpu_user_us', 'cpu_system_us')) and int(summ['peak_rss_kb']) > 0)
        ck(name + '_rail_lifecycle', rs['healthy'] == 'true' and int(rs['reads_ok']) == 11
           and int(rs['reads_err']) == 0 and int(rs['bytes']) == n * 11
           and all(int(rs[k]) == 0 for k in ('inflight_requests', 'inflight_bytes', 'registered_bytes_reserved'))
           and int(rs['peak_inflight_bytes']) == n and int(rs['peak_registered_bytes']) == n)
        pools = rec(txt, 'pool_stats')
        ck(name + '_three_pool_snapshots', [x['phase'] for x in pools] == ['before_warmup', 'after_warmup', 'final'])
        if pool:
            before = {k: 0 for k in ('created', 'reused', 'retired', 'rejected', 'live', 'connecting', 'active', 'idle', 'retiring')}
            after = {**before, 'created': 1, 'live': 1, 'idle': 1}
            final = {**after, 'reused': 10}
            ck(name + '_pool_counters_all_phases', all(s['enabled'] == 'true' and all(int(s[k]) == val for k, val in exp.items())
               for s, exp in zip(pools, (before, after, final))))
            counts['pooled_processes'] += 1
        else:
            ck(name + '_pool_disabled_all_phases', all(s == {'phase': phase, 'enabled': 'false'}
               for s, phase in zip(pools, ('before_warmup', 'after_warmup', 'final'))))
            counts['unpooled_processes'] += 1
        counts['measured_reads'] += len(samples)
        counts['warmup_reads_indicated_by_rail_counters'] += int(rs['reads_ok']) - len(samples)
        blocks.setdefault(size, {}).setdefault(pair, {})[v] = {
            'upper_median_us': times[5], 'ordinary_median_us': statistics.median(times), 'samples_us': times,
            'cpu_timed_reads_us': int(summ['cpu_user_us']) + int(summ['cpu_system_us']),
            'peak_rss_kib': int(summ['peak_rss_kb'])}
    results = {}
    for size in (16, 64):
        data = blocks[size]
        ratios = [data[p]['R0']['upper_median_us'] / data[p]['R1']['upper_median_us'] for p in range(10)]
        logs = list(map(math.log, ratios))
        gm = math.exp(statistics.mean(logs))
        rng = random.Random(20261029 + size)
        boot = sorted(math.exp(statistics.mean(logs[rng.randrange(10)] for j in range(10))) for i in range(10000))
        ci = [boot[249], boot[9749]]
        saved = a['sizes'][str(size)]
        ck(f'{size}_ratio_and_gm_match_analysis', ratios == saved['paired_ratios'] and math.isclose(gm, saved['geometric_mean_ratio'], rel_tol=1e-14))
        ck(f'{size}_bootstrap_matches_analysis', all(math.isclose(x, y, rel_tol=1e-14) for x, y in zip(ci, saved['paired_bootstrap_95pct_percentile'])))
        ck(f'{size}_latency_reduction_matches_analysis', math.isclose(1 - 1 / gm, saved['geometric_mean_latency_reduction_fraction'], rel_tol=1e-14))
        for v in ('R0', 'R1'):
            ck(f'{size}_{v}_all_per_process_analysis_values', all(data[p][v]['upper_median_us'] == saved['blocks'][p][v]['median_us']
               and data[p][v]['samples_us'] == saved['blocks'][p][v]['samples_us']
               and data[p][v]['cpu_timed_reads_us'] == saved['blocks'][p][v]['cpu_us']
               and data[p][v]['peak_rss_kib'] == saved['blocks'][p][v]['peak_rss_kib'] for p in range(10)))
            ck(f'{size}_{v}_summary_cpu_rss', statistics.mean(data[p][v]['cpu_timed_reads_us'] for p in range(10)) == saved[f'{v}_mean_cpu_us_per_process']
               and [min(data[p][v]['peak_rss_kib'] for p in range(10)), max(data[p][v]['peak_rss_kib'] for p in range(10))] == saved[f'{v}_peak_rss_kib_range'])
        se = statistics.stdev(logs) / math.sqrt(10)
        crit = float(t.ppf(.975, 9))
        diag = [math.exp(statistics.mean(logs) - crit * se), math.exp(statistics.mean(logs) + crit * se)]
        ordinary = math.exp(statistics.mean(math.log(data[p]['R0']['ordinary_median_us'] / data[p]['R1']['ordinary_median_us']) for p in range(10)))
        results[size] = {'paired_ratios': ratios, 'geometric_mean_ratio': gm, 'bootstrap_95_percentile': ci,
                         'latency_reduction_pct': 100 * (1 - 1 / gm),
                         'latency_reduction_pct_interval': [100 * (1 - 1 / x) for x in ci],
                         'r1_slower_pairs_zero_based': [p for p, r in enumerate(ratios) if r < 1],
                         'diagnostic_log_paired_t_95': diag, 'diagnostic_ordinary_median_ratio_gm': ordinary,
                         'diagnostic_ordinary_median_latency_reduction_pct': 100 * (1 - 1 / ordinary), 'blocks': data}
    prior = json.loads((base / 'environment/provider-ready-receipt.json').read_text())['active_fixture']
    after = json.loads((base / 'campaign/server-after-pilot.json').read_text())
    pref = json.loads((base / 'campaign/parent-preflight-observation.json').read_text())
    ck('server_before_after_same_pid_and_start_ticks', prior['server_pid'] == after['pid'] and prior['server_start_ticks'] == after['start_ticks'])
    ck('server_before_after_same_exe_and_environment', prior['server_actual_exe_sha256'] == after['exe_sha256'] == freeze['server_binary_sha256']
       and prior['server_environment'] == after['task_env'] and prior['server_exe'] == after['exe_link'])
    ck('server_snapshots_bracket_pilot', datetime.datetime.fromisoformat(prior['captured_at']).timestamp()
       < ix[0]['started_unix_ns'] / 1e9 < ix[-1]['ended_unix_ns'] / 1e9 < datetime.datetime.fromisoformat(after['captured_at_utc']).timestamp())
    ck('parent_preflight_reports_same_hash', pref['record_written_after_pilot'] and not pref['exact_utc_timestamp_captured']
       and pref['observed_exit_code'] == 0 and pref['observed_stdout'].startswith(freeze['server_binary_sha256']))
    normalized_results = json.loads(json.dumps(results))
    replay_comparison = {
        'all_553_check_names_and_outcomes_identical': checks == original['checks'],
        'counts_identical': counts == original['counts'],
        'all_raw_sha256_identical': raws == original['raw_sha256'],
        'source_checks_identical': source_checks == original['source_checks'],
        'all_results_and_diagnostics_identical': normalized_results == original['results'],
        'payload_hashes_identical': json.loads(json.dumps(hashes)) == original['independent_expected_payload_xxh3'],
    }
    failures = [k for k, v in checks.items() if not v]
    receipt = {'status': 'PASS' if not failures and all(replay_comparison.values()) else 'FAIL',
               'audit_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'checks_count': len(checks), 'checks_passed': sum(checks.values()), 'failures': failures,
               'replay_comparison': replay_comparison, 'counts': counts, 'checks': checks,
               'raw_sha256': raws, 'source_checks': source_checks, 'results': normalized_results,
               'hash_validation': hash_receipt,
               'script_sha256': {p.name: sha(p) for p in (HERE / 'replay_pilot_audit.py', HERE / 'replay_twox_hash.py')},
               'original_receipt_sha256': sha(HERE / 'pilot-evidence-math.json')}
    output.mkdir(parents=True, exist_ok=True)
    (output / 'pilot-evidence-math.json').write_text(json.dumps(receipt, indent=2) + '\n')
    (output / 'pilot-hash-compatibility.json').write_text(json.dumps(hash_receipt, indent=2) + '\n')
    print(json.dumps({k: receipt[k] for k in ('status', 'checks_count', 'checks_passed', 'failures', 'replay_comparison')}, indent=2))
    assert receipt['status'] == 'PASS', (failures, replay_comparison)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=HERE / 'replay')
    main(parser.parse_args().output)
