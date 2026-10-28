#!/usr/bin/env python3
"""Independent local read-only audit of wave2 release raw records; writes review evidence only.

No producer analyzer/validator is imported. The historical hash oracle is the
previous independently transcribed, MIT-licensed twox-hash 1.6.3 replay.
"""
from pathlib import Path
import collections, datetime, gzip, hashlib, importlib.util, json, math
import random, re, statistics, subprocess, tarfile

HERE = Path(__file__).resolve().parent
WAVE = HERE.parent
PROJECT = WAVE.parents[1]
CAMPAIGN = WAVE / 'campaign-release'
RAW = CAMPAIGN / 'client-evidence/performance-release-20261029-v1'
CONTROL = CAMPAIGN / 'client-evidence/performance-release-control-v1'
SOURCE = PROJECT / 'work/ContextStore-runtime-wave1'
checks = collections.Counter()

def load(p):
    return json.loads(p.read_text())

def check(value, label):
    assert value, label
    checks[label] += 1

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def gm(xs):
    return math.exp(statistics.mean(math.log(x) for x in xs))

def close(a, b, label):
    check(math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-10), label)

def fields(line):
    # This is a key=value log, not RFC CSV. Split only comma+key= boundaries;
    # preserve Rust Debug brackets and quoted values such as Some("...").
    head, sep, rest = line.partition(',')
    if not sep:
        return head, {}
    parts = re.split(r',(?=[A-Za-z_][A-Za-z_0-9]*=)', rest)
    out = {}
    for part in parts:
        k, eq, v = part.partition('=')
        check(eq and k not in out and bool(re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', k)), 'valid unique log fields')
        out[k] = v
    return head, out

def parse(text):
    result = collections.defaultdict(list)
    for line in text.splitlines():
        key, value = fields(line)
        result[key].append(value)
    return result

def one(rows, key):
    check(len(rows[key]) == 1, 'one ' + key)
    return rows[key][0]

def main():
    original_freeze = load(WAVE / 'campaign-freeze.json')['R_matrix']
    effective = load(CAMPAIGN / 'effective-configuration.json')
    freeze = effective['effective_R_matrix']
    protocol = load(CONTROL / 'protocol.json')
    manifest = load(RAW / 'manifest.json')
    index = load(RAW / 'run-index.json')
    expected_hash = load(RAW / 'known-pattern-hashes.json')
    check(protocol['R_matrix_original'] == original_freeze, 'original frozen matrix exact')
    amendment = load(WAVE / 'release-profile-amendment.json')
    check(protocol['amendment'] == amendment == load(CONTROL/'release-profile-amendment.json'), 'predeclared amendment exact')
    check(effective['source_protocol_sha256'] == sha(CONTROL/'protocol.json'), 'effective crosswalk bound to protocol')
    overridden = {'server_sha256', 'source_configuration'}
    check(all(freeze[k] == v for k,v in original_freeze.items() if k not in overridden), 'all non-profile matrix terms unchanged')
    check(freeze['server_sha256'] == protocol['server_build']['binary_sha256'] == 'f6607705f29e30bc293ada3f9e64422cda8e0dadd8dc2996d02cec8ce8d3f0d8', 'release binary override explicit')
    check(protocol['server_build']['frozen_utc'] < protocol['at'] < manifest['started'], 'release freeze before launch')
    for name in ['protocol.json','release-profile-amendment.json','release-server-build-freeze.json']:
        check(sha(CONTROL/name) == manifest['control_sha256'][name], 'control bound at process launch '+name)
    check(load(CAMPAIGN / 'protocol.json') == protocol, 'protocol copies exact')
    check(protocol['at'] < manifest['started'], 'protocol precedes campaign')
    check(manifest['state'] == 'COMPLETED' and manifest['run_count'] == 135, 'completed 135 record manifest')
    check(collections.Counter(x['phase'] for x in index) == {'seed': 9, 'measure': 120, 'diagnostic': 6}, 'all phases counted')
    check(len({x['name'] for x in index}) == 135, 'unique process names')
    check([(r['size_mib'],r['object_index']) for r in index if r['phase']=='seed']==[(s,o) for s in [1,8,64] for o in range(3)], 'exact nine fresh seed slots')
    for name, want in load(RAW / 'raw-sha256.json').items():
        check(sha(RAW / name) == want, 'raw hash verified')
    for name, want in protocol['control_sha256'].items():
        check(sha(CONTROL / name) == want, 'frozen control hash verified')
    for name, want in protocol['source_sha256'].items():
        check(sha(SOURCE / name) == want, 'current runtime source hash verified')
    check(subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip() == protocol['source_runtime_head'] == '1c3ee135d19fece67507b51acd1fc51ae9541208', 'runtime source revision verified')
    check(load(CAMPAIGN / 'collection-receipt.json')['source_sha256_after'] == protocol['source_sha256'], 'source before after exact')
    check(manifest['binary_sha256'] == manifest['binary_sha256_after'] == {'cs-rail-read-bench': freeze['benchmark_sha256'], 'cs-bench': freeze['seeder_sha256']}, 'client binaries before after exact')
    before, after = (load(CAMPAIGN / f'server-{phase}.json') for phase in ['before', 'after'])
    for key in ['pid', 'start_ticks', 'exe', 'exe_sha256', 'command', 'environment']:
        check(before['server'][key] == after['server'][key], 'server unchanged ' + key)
    check(before['server']['exe_sha256'] == freeze['server_sha256'], 'server binary frozen')
    check(before['server_config'] == after['server_config'], 'server config before after exact')
    check(hashlib.sha256(before['server_config']['content'].encode()).hexdigest() == before['server_config']['sha256'], 'server config raw hash exact')
    env = before['server']['environment']
    check(env['CS_FORCE_DISK_READ'] == '1' and env['CS_RDMA_SLAB_MB'] == '512' and env['CS_RDMA_DEVICES'] == 'mlx5_1:10.0.0.65:25553:3', 'server environment scope')
    check('striping_threshold = 524288' in before['server_config']['content'] and 'striping_chunk_size = 524288' in before['server_config']['content'], '512KiB server striping')
    cb, ca = (load(RAW / f'client-{phase}.json') for phase in ['before', 'after'])
    check(cb['binaries'] == ca['binaries'] == manifest['binary_sha256'], 'client snapshot binaries exact')
    check(cb['client_env'] == ca['client_env'] and cb['fabric'] == ca['fabric'], 'client env fabric unchanged')
    check(cb['hostname'] == ca['hostname'] == 'skv-node6' and before['hostname'] == after['hostname'] == 'skv-node5', 'physical host pair verified')
    check(before['fabric'] == after['fabric'] and '100 Gb/sec' in before['fabric']['rate'], 'server fabric stable')
    replay = [json.loads(line) for line in (RAW / 'replay-commands.jsonl').read_text().splitlines()]
    check(replay == [{k:r[k] for k in ['name', 'argv', 'environment']} for r in index], 'replay command coverage exact')
    actual_schedule = [{k:r[k] for k in ['phase','size_mib','concurrency','pair','object_index','order_position','variant','iterations']} for r in index if r['phase']=='measure']
    wanted_schedule = [dict(phase='measure', size_mib=s, concurrency=c, pair=b, object_index=b%3, order_position=pos, variant=v, iterations=35) for s in [1,8,64] for c in [1,4] for b in range(10) for pos,v in enumerate(['R0','R1'] if b%2==0 else ['R1','R0'])]
    check(actual_schedule == wanted_schedule == load(RAW / 'scheduled-slots.json'), 'exact full schedule and alternating order')
    check({p.name[:-len('.exit.json')] for p in RAW.glob('*.exit.json')} == {r['name'] for r in index}, 'no unindexed process exits')
    check(not (RAW / 'failure.json').exists(), 'release no failure artifact')
    blocks, identities, counts, diagnostic = {}, {}, collections.Counter(), []
    last_end = 0
    for rec in index:
        name = rec['name']; start = load(RAW / (name + '.started.json')); end = load(RAW / (name + '.exit.json'))
        check(all(end[k] == v for k,v in start.items()), 'start exit records agree')
        check(all(end[k] == v for k,v in rec.items() if k != 'inherited_from_campaign'), 'index exit records agree')
        check(rec['exit_code'] == 0 and 0 < rec['ended_unix_ns'] - rec['started_unix_ns'] < 240 * 10**9, 'zero exit within timeout')
        check(rec['started_unix_ns'] > last_end, 'sequential process timestamps')
        last_end = rec['ended_unix_ns']
        check('inherited_from_campaign' not in rec, 'all 135 launches fresh')
        check(rec['started'] > manifest['started'], 'launch after campaign begins')
        if rec['phase'] == 'seed':
            a=rec['argv']; size=rec['size_mib']; obj=rec['object_index']
            expected_seed=[a[0],'--endpoint','http://10.0.0.65:25551','--layers','1','--layer-mb',str(size),'--concurrency','1','--combined','--combined-count','1','--stream','--stream-chunk-mb','2','--prefix-base',f'wave2-perf-release-v1-{size}-{obj}-','--only-put']
            check(a==expected_seed and Path(a[0]).name=='cs-bench','fresh seed argv exact')
            check((RAW/(name+'.stderr')).read_text()=='' and '[PUT combined' in (RAW/(name+'.stdout')).read_text(),'fresh seed put successful')
            continue
        check('inherited_from_campaign' not in rec, 'no inherited measurement reuse')
        s, c, n, variant = (rec[k] for k in ['size_mib','concurrency','iterations','variant'])
        a = rec['argv']
        expected_argv = [a[0], '--coordinator','http://10.0.0.65:25551','--namespace','rust-bench','--object-key',f"wave2-perf-release-v1-{s}-{rec['object_index']}-0/__combined__",'--rail','single,mlx5_1,10.0.0.65:25553,10.0.0.65:25553,1,3','--environment','physical','--warmup','0','--iterations',str(n),'--concurrency',str(c),'--verify-pattern','--connection-pool-size',str(c if variant=='R1' else 0)]
        check(a == expected_argv and Path(a[0]).name == 'cs-rail-read-bench', 'exact argv and selected object')
        check(rec['environment'] == {**manifest['environment'], **({'CS_RAIL_TIMING':'1'} if rec['phase']=='diagnostic' else {})}, 'exact process environment')
        text = (RAW/(name+'.stdout')).read_text(); err = (RAW/(name+'.stderr')).read_text(); rows = parse(text)
        if rec['phase']=='measure': check(err == '', 'primary stderr empty tracing disabled')
        check(not re.search(r'\b(?:ERROR|WARN|panic|failed)\b',err), 'no diagnostic warning error')
        samples = rows['sample']; check(len(samples) == n*c, 'every request sampled')
        identity = one(rows, 'object_identity')
        check(identity == one(rows,'object_identity_after'), 'process identity stable')
        key = (s,rec['object_index'])
        check(identities.setdefault(key,identity) == identity, 'cross process identity stable')
        check(identity['generation']=='1' and identity['layout_version']=='1' and int(identity['stripe_count'])==s*2 and int(identity['chunk_size'])==524288, 'identity layout valid')
        check(one(rows,'expected_pattern') == dict(pattern='index_mod_251',bytes=str(s*1048576),xxh3=expected_hash[str(s)]), 'expected full known pattern')
        check(rows['mode'] == [{'direct_staging':'false'},{'contiguous_schedule':'false'},dict(connection_pool_size=str(c if variant=='R1' else 0),recovery_timeout_ms='None',verify_pattern='true',warmup='0',iterations=str(n),concurrency=str(c))], 'opt in modes exact')
        check(one(rows,'route') == dict(id='single',device='mlx5_1',port='1',gid='3',weight='1',advertised='10.0.0.65:25553',listener='10.0.0.65:25553'), 'one HCA route')
        plan = one(rows,'plan_assignment')
        check(plan['id']=='single' and json.loads(plan['stripes'])==list(range(s*2)) and int(plan['bytes'])==s*1048576, 'full stripe plan parser checked')
        observed, steady, startup = [], [], []
        for sample in samples:
            batch = int(sample['iteration' if c==1 else 'batch']); worker=int(sample.get('worker',0)); latency=int(sample['latency_us'])
            check(sample['environment']=='physical' and sample['rails']=='1' and int(sample['bytes'])==s*1048576 and sample['xxh3']==expected_hash[str(s)] and latency>0, 'all samples size hash path positive latency')
            check(c==1 or sample['concurrency']==str(c),'sample concurrency exact')
            observed.append((batch,worker)); (steady if batch>5 else startup).append(latency)
        check(observed == [(b,w) for b in range(1,n+1) for w in range(c)], 'sample batch worker order complete')
        pool = rows['pool_stats']; check([p['phase'] for p in pool]==['before_warmup','after_warmup','final'], 'three pool snapshots')
        if variant=='R1':
            for p in pool[:2]: check(p['enabled']=='true' and all(int(v)==0 for k,v in p.items() if k not in ['phase','enabled']), 'cold pool zero')
            p=pool[-1]; created=int(p['created']); reused=int(p['reused'])
            check(p['enabled']=='true' and 1<=created<=c and created+reused==n*c, 'pool creation reuse accounting')
            check(all(int(p[k])==0 for k in ['retired','rejected','connecting','active','retiring']) and int(p['idle'])==int(p['live'])==created, 'pool quiescence ownership')
            counts['server_connections_expected']+=created
        else:
            check(all(p=={'phase':phase,'enabled':'false'} for p,phase in zip(pool,['before_warmup','after_warmup','final'])), 'R0 pool disabled')
            counts['server_connections_expected']+=n*c
        check(one(rows,'mode_effective')==dict(one=str(n*c),packed_greedy='0',packed_contiguous='0',direct='0'),'single task effective mode')
        summary=one(rows,'summary' if c==1 else 'summary_concurrent')
        check(summary['environment']=='physical' and summary['rails']=='1' and json.loads(summary['rail_bytes'])==[s*1048576*n*c], 'summary rail byte accounting')
        check(int(summary['median_us' if c==1 else 'median_request_us'])==sorted(steady+startup)[n*c//2], 'CLI upper median matches raw')
        if c==1:
            rail=one(rows,'rail_stats')
            check(int(rail['reads_ok'])==n and int(rail['reads_err'])==0 and int(rail['bytes'])==s*1048576*n, 'serial rail completed counters')
            check(all(int(rail[k])==0 for k in ['inflight_requests','inflight_bytes','registered_bytes_reserved']), 'serial zero outstanding resources')
        else:
            batches=rows['batch']
            check([int(x['iteration']) for x in batches]==list(range(1,n+1)), 'concurrent batches complete')
            check(all(int(x['wall_us'])>=max(int(y['latency_us']) for y in samples if y['batch']==x['iteration']) for x in batches), 'batch contains request time')
        check(load(RAW/(name+'.validation.json'))['state']=='PASS', 'per process receipt present')
        if rec['phase']=='measure':
            counts['main_reads']+=n*c; counts['steady_reads']+=len(steady);counts['startup_reads']+=len(startup)
            blocks[(s,c,rec['pair'],variant)]={'median':statistics.median(steady),'p95':sorted(steady)[math.ceil(.95*len(steady))-1]}
        else:
            counts['diagnostic_reads']+=n*c
            stages={}
            for stage in ['RAIL_TRANSPORT_TIMING','RAIL_STAGED_TIMING','RAIL_OUTER_TIMING']:
                rr=[dict(x.split('=',1) for x in line.split()[1:]) for line in err.splitlines() if line.startswith(stage+' ')]
                check(len(rr)==6,'six diagnostic timing records per stage')
                stages[stage]={k:{'first_read':int(rr[0][k]),'next_five_median':statistics.median(int(x[k]) for x in rr[1:]),'next_five_values':[int(x[k]) for x in rr[1:]]} for k in rr[0] if k.endswith('_us')}
            diagnostic.append(dict(size_mib=s,variant=variant,stages=stages))
    check(counts['main_reads']==10500 and counts['steady_reads']==9000 and counts['startup_reads']==1500 and counts['diagnostic_reads']==36,'exact read totals')
    check(len(identities)==9 and all(len({json.dumps(v,sort_keys=True) for (size,obj),v in identities.items() if size==s})==3 for s in [1,8,64]),'three distinct identities each size')
    metadata=load(CAMPAIGN/'server-nine-object-metadata.json')
    check(len(metadata['objects'])==9,'nine server metadata objects')
    for obj in metadata['objects']:
        check(obj['stripe_count']==obj['chunk_files_verified']==obj['size_mib']*2 and obj['stripe_bytes']==524288 and obj['size']==obj['size_mib']*1048576 and obj['object_generation']==obj['layout_version']==1,'server metadata layout and file coverage')
    check(diagnostic==load(CAMPAIGN/'phase-diagnostics.json')['cells'],'all phase diagnostics independently recomputed')
    serverlog=gzip.decompress((CAMPAIGN/'server-matrix-window.log.gz').read_bytes()).decode()
    check(len(serverlog.encode())==after['server_log']['bytes']-before['server_log']['bytes'],'server log byte window complete')
    check(serverlog.count('RDMA_SUBSET_DETAIL ')==10536,'server detail lines equal every read')
    check(serverlog.count('RDMA client connected:')==counts['server_connections_expected']==5421,'server connection lines equal pool accounting')
    check(not re.search(r'fallback|\bWARN\b|\bERROR\b',serverlog,re.I),'server zero fallback warning error observations')
    old=WAVE/'campaign/client-evidence/performance-20261029-v2'
    for name,want in load(old/'raw-sha256.json').items():
        check(sha(old/name)==want,'debug raw evidence unchanged')
    old_review=load(HERE/'performance-review-math.json')
    check(sha(old/'raw-sha256.json')==old_review['raw_manifest_sha256'],'debug original manifest unchanged since audit')
    check(sha(HERE/'replay_performance_review.py')==old_review['script_sha256'],'debug audit script unchanged')
    oldkeys={x['argv'][x['argv'].index('--object-key')+1] for x in load(old/'run-index.json') if x['phase']!='seed'}
    newkeys={x['argv'][x['argv'].index('--object-key')+1] for x in index if x['phase']!='seed'}
    check(len(newkeys)==9 and oldkeys.isdisjoint(newkeys),'release data keys disjoint from debug')
    check(all('contextstore:wave2-release-matrix512k-20261029:' in x['key'] for x in metadata['objects']),'fresh release metadata namespace')
    build=protocol['server_build']; evidence=WAVE/'environment/release-matrix512k/server-build-source-evidence'
    check(build==load(CAMPAIGN/'release-server-build-freeze.json')==manifest['server_build'],'release build freeze consistent')
    check(build['profile']=='release' and build['server_features']==['io-uring','rdma','metrics'] and build['build_exit']==0,'release features profile build exit')
    check(sha(evidence/'green-source-sha256.json')==sha(CAMPAIGN/'server-build-source-sha256.json')==build['source_manifest_sha256'],'source manifest pinned')
    check(sha(evidence/'green-source.tar.gz')==build['source_archive_sha256'],'source archive pinned')
    source_manifest=load(CAMPAIGN/'server-build-source-sha256.json')
    check(len(source_manifest)==65,'65 build source files')
    with tarfile.open(evidence/'green-source.tar.gz') as archive:
        check(set(archive.getnames())==set(source_manifest),'source archive exact member set')
        for name,want in source_manifest.items():
            check(hashlib.sha256(archive.extractfile(name).read()).hexdigest()==want==sha(SOURCE/name),'all 65 source files archive current exact')
    buildlog=(evidence/'logs/make-build.log').read_text()
    check('profile: release' in buildlog and 'server features: io-uring,rdma,metrics' in buildlog and 'cargo build --release --features io-uring,rdma,metrics' in buildlog and 'Finished `release` profile [optimized]' in buildlog,'default release build log')
    check(build['candidate_source_sha256']==effective['guard_source_sha256']==source_manifest['kv-service/server/src/rdma/server.rs'],'reviewed guard source pinned')
    check(effective['tier_a_source_sha256']==source_manifest['kv-service/server/src/io_executor/tier_a.rs'],'tier_a source pinned')
    config=before['server_config']['content']
    check('kind = "tier_a"' in config and 'thread_pool_size = 4' in config and '/release-matrix512k/data/stripe0' in config and 'contextstore:wave2-release-matrix512k-20261029:' in config,'tier_a isolated storage config')
    check(before['server']['pid']==1863559 and before['server']['start_ticks']=='313697929','release process identity exact')
    cross=protocol['actual_server_before']
    check(cross['pid']==before['server']['pid'] and cross['start_ticks']==before['server']['start_ticks'] and cross['binary_sha256']==before['server']['exe_sha256'] and cross['config_sha256']==before['server_config']['sha256'] and cross['environment']==before['server']['environment'],'protocol actual server matches raw snapshot')
    check(sha(CAMPAIGN/'client-evidence.tar.gz')==load(CAMPAIGN/'collection-receipt.json')['client_archive_sha256'],'collected archive hash exact')
    for name,want in load(CAMPAIGN/'campaign-file-sha256.json').items():
        check(sha(CAMPAIGN/name)==want,'campaign file hash verified')
    table=load(CAMPAIGN/'ppt-performance-table.json')['rows']; analysis=load(RAW/'analysis.json')['cells']; cells=[]
    for s in [1,8,64]:
        for c in [1,4]:
            med={v:[blocks[(s,c,b,v)]['median'] for b in range(10)] for v in ['R0','R1']}
            tails={v:[blocks[(s,c,b,v)]['p95'] for b in range(10)] for v in ['R0','R1']}
            ratios=[a/b for a,b in zip(med['R0'],med['R1'])]; logs=list(map(math.log,ratios))
            rng=random.Random(20261029+s*10+c)
            boot=sorted(math.exp(statistics.mean(logs[rng.randrange(10)] for _ in range(10))) for _ in range(20000))
            half=2.2621571627409915*statistics.stdev(logs)/math.sqrt(10); mean=statistics.mean(logs)
            row=dict(size_mib=s,concurrency=c,independent_pairs=10,R0_geomean_process_median_us=gm(med['R0']),R1_geomean_process_median_us=gm(med['R1']),R0_over_R1=gm(ratios),bootstrap95_lower=boot[499],bootstrap95_upper=boot[19499],log_t95_lower=math.exp(mean-half),log_t95_upper=math.exp(mean+half),R1_slower_pairs=sum(x<1 for x in ratios),median_latency_reduction_percent=100*(1-1/gm(ratios)),p95_ratio_descriptive=gm([a/b for a,b in zip(tails['R0'],tails['R1'])]))
            want=next(r for r in table if (r['size_mib'],r['concurrency'])==(s,c))
            for k,v in row.items(): close(v,want[k],'PPT field independently reproduced '+k)
            aa=next(r for r in analysis if (r['size_mib'],r['concurrency'])==(s,c))
            check(med['R0']==aa['R0_medians_us'] and med['R1']==aa['R1_medians_us'] and tails['R0']==aa['R0_p95_us'] and tails['R1']==aa['R1_p95_us'],'all process medians p95 exact')
            cells.append({**row,'paired_ratios':ratios,'R0_process_medians_us':med['R0'],'R1_process_medians_us':med['R1'],'R0_process_p95_us':tails['R0'],'R1_process_p95_us':tails['R1']})
    # Re-run historical oracle locally, including existing source known-answer vectors.
    oracle_path=PROJECT/'outputs/implementation-wave1-20261029/reviews/replay_twox_hash.py'
    spec=importlib.util.spec_from_file_location('independent_twox_replay',oracle_path); oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(oracle)
    oracle_receipt=oracle.compute_receipt()
    for s in [1,8]:
        n=s*1048576; oracle_receipt['deterministic_pattern_i_mod_251_hashes'][s]=oracle.h((bytes(range(251))*(n//251+1))[:n])
    check(all(oracle_receipt['deterministic_pattern_i_mod_251_hashes'][int(k)]==v for k,v in expected_hash.items()),'historical hash oracle full data replay')
    oracle_receipt['replay_script_sha256']=sha(oracle_path)
    result={'state':'PASS_WITH_SCOPE_LIMITATIONS','audit_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'script_sha256':sha(Path(__file__)),'checks':dict(checks),'counts':dict(counts),'cells':cells,'diagnostics':diagnostic,'hash_oracle':oracle_receipt,'raw_manifest_sha256':sha(RAW/'raw-sha256.json'),'protocol_sha256':sha(CONTROL/'protocol.json'),'source_revision':protocol['source_runtime_head'],'server_profile':'release','server_binary_sha256':freeze['server_sha256'],'amendment_sha256':sha(WAVE/'release-profile-amendment.json')}
    (HERE/'release-performance-review-math.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'state':result['state'],'check_categories':len(checks),'assertions':sum(checks.values()),'counts':dict(counts),'rows':[{k:v for k,v in r.items() if not isinstance(v,list)} for r in cells]},indent=2))

if __name__=='__main__':
    main()
