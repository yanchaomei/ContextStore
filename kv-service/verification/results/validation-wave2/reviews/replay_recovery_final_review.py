#!/usr/bin/env python3
"""Read-only independent replay of the sealed seven-suite recovery holdout.

Writes only review artifacts. Does not build Rust or execute native tests.
Independent protocol/schedule/connection-interval checks below do not import
the producer's auditor or finalizer. The frozen auditor is separately replayed
unchanged, retaining its original RED. Hash computation uses the credited
MIT-licensed Python transcription in campaign/hash-oracle/replay_twox_hash.py
(derived from twox-hash 1.6.3 by the earlier independent audit).
"""
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime, timezone
import ast, bisect, copy, gzip, hashlib, importlib.util, json, os, re, subprocess, sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
WAVE = HERE.parent
PROJECT = WAVE.parents[1]
REPO = PROJECT / 'work/ContextStore-runtime-wave1'
ROOT = REPO / 'kv-service/verification/results/validation-wave2/recovery/holdout-v2'
CLIENT = ROOT / 'collected/client'
SERVER = ROOT / 'collected/server'
checks = Counter()
PREFIX = ('RECOVERY_REQUEST ', 'RECOVERY_CUT ', 'RECOVERY_LIFETIME ', 'RECOVERY_PUBLICATION_GATE ', 'RECOVERY_ADMISSION ')
EXPECTED_TOTALS = {'f2-recovery1000': (1000,1000,3000,1000), 'matched-controls240': (240,160,720,240), 'cuts480': (480,80,1280,240), 'lifetime2000': (2000,2000,4000,0), 'publication160': (160,0,480,160), 'admission24': (24,0,16,16), 'representative16mib4': (4,4,12,4)}

def ck(value, label):
    assert value, label
    checks[label] += 1

def load(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def fields(s): return dict(re.findall(r'\b([a-z_]+)=([^ ]+)', s))
def timestamp(s): return datetime.fromisoformat(s.split()[0].replace('Z','+00:00')).timestamp()
def strip_ansi(s): return re.sub(r'\x1b\[[0-9;]*m','',s)

def decode_frame(line, lineno):
    v = fields(line)
    wire = bytes.fromhex(line.split(' wire=[',1)[1][:-1].replace(',',''))
    pos = 0
    def num(n):
        nonlocal pos
        ck(pos+n <= len(wire), 'protocol bounds')
        x = int.from_bytes(wire[pos:pos+n], 'little'); pos += n
        return x
    def string():
        nonlocal pos
        n = num(2); ck(pos+n <= len(wire), 'protocol string bounds')
        s = wire[pos:pos+n].decode(); pos += n
        return s
    ck(num(1) == 15, 'native descriptor SGE tag')
    identity = dict(key=string(),handle=string(),generation=num(8),etag=string(),layout=num(8),size=num(8),striped=num(1),stripe_count=num(4),chunk_size=num(8))
    legacy = [num(8),num(4),num(8)]
    stripes = [num(4) for _ in range(num(2))]
    segments = [[num(8),num(4),num(8)] for _ in range(num(2))]
    ck(pos == len(wire), 'protocol complete decode')
    ck(stripes == ast.literal_eval(line.split(' stripes=',1)[1].split(' segments=',1)[0]), 'wire stripe echo exact')
    ck(segments == [list(x) for x in ast.literal_eval(line.split(' segments=',1)[1].split(' terminal=',1)[0])], 'wire segment echo exact')
    return dict(op=int(v['op']),lane=int(v['lane']),sequence=int(v['sequence']),peer=v['peer'],qpn=int(v['qpn']),control=v['control'],terminal=v['terminal'],identity=identity,legacy=legacy,stripes=stripes,segments=segments,client_line=lineno,wire_bytes=len(wire),wire_sha256=hashlib.sha256(wire).hexdigest())

def schedule(run):
    seed=run['CS_RECOVERY_SEED']; kind=run['test']; out=[]
    def add(**x):
        x['op']=len(out); x['seed']=seed; out.append(x)
    if kind=='native_recovery_control_campaign':
        controls=run['CS_RECOVERY_CONTROLS'].split(',')
        for direct in (False,True):
            for pooled in (False,True):
                for control in controls:
                    for i in range(run['CS_RECOVERY_OPS']//(4*len(controls))):
                        add(direct=direct,pooled=pooled,control=control,failed_lane=(i+seed)%2,key=f'native-recovery-{seed}-{(i+seed)%4}',local=i)
    elif kind=='native_recovery_cut_campaign':
        for direct in (False,True):
            for pooled in (False,True):
                for cut in ('AckTimeout','CancelInitial','CancelRetry','DeadlineRetryGate','Overwrite','Delete'):
                    for i in range(run['CS_RECOVERY_CUT_REPEATS']):
                        add(direct=direct,pooled=pooled,cut=cut,repeat=i,failed_lane=(i+seed)%2,key=f'native-cuts-{seed}-{str(direct).lower()}-{str(pooled).lower()}-{cut}')
    elif kind=='native_recovery_reader_lifetime_campaign':
        for close_each in (False,True):
            for direct in (False,True):
                for i in range(run['CS_LIFETIME_OPS']//2):
                    add(direct=direct,pooled=True,close_each=close_each,key=f'native-lifetime-{seed}',local=i)
    elif kind=='native_recovery_publication_gate_campaign':
        for direct in (False,True):
            for pooled in (False,True):
                for capability_control_injection in (False,True):
                    for i in range(run['CS_PUBLICATION_REPEATS']):
                        add(direct=direct,pooled=pooled,capability_control_injection=capability_control_injection,repeat=i,failed_lane=(i+seed)%2,key=f'native-publication-{seed}')
    else:
        for direct in (False,True):
            for pooled in (False,True):
                for failed_lane in (0,1):
                    for cut in ('single_available_path','unsupported_alternate_owner','registered_byte_cap'):
                        add(direct=direct,pooled=pooled,failed_lane=failed_lane,cut=cut,key=f'native-admission-{seed}')
    return out

def server_events(path):
    lines=strip_ansi(path.read_text()).splitlines(); opens=defaultdict(list); terms=[]; details=[]
    for index,line in enumerate(lines,1):
        m=re.search(r'RDMA client connected: (\S+) \(nic_idx=(\d+)\)',line)
        if m: opens[m[1]].append(dict(line=index,peer=m[1],lane=int(m[2]),time=timestamp(line)))
        m=re.search(r'RDMA_SUBSET_DETAIL key=(\S+) bytes=(\d+) stripes=(\d+) requests=(\d+)',line)
        if m: details.append(dict(line=index,key=m[1],bytes=int(m[2]),stripes=int(m[3]),requests=int(m[4]),time=timestamp(line)))
        m=re.search(r'RDMA client (\S+) \(nic_idx=(\d+)\) disconnected: RDMA connection retired after uncertain stripe-subset completion: (.*)',line)
        if m:
            prior=lines[index-2]
            fail=re.search(r'stripe-subset stream failed without deleting object metadata key=(\S+) error=(.*)',prior)
            ck(fail is not None and fail[2]==m[3], 'server immediate exact failure retirement adjacency')
            ck(re.fullmatch(r'poll final RDMA completions: poll_n timeout after (1000|30000)ms, got 0/(2|8|4|16)',fail[2]) is not None, 'server native WR poll failure shape')
            candidates=opens[m[1]]
            ck(bool(candidates), 'terminal has preceding exact-peer open')
            opened=candidates[-1]
            ck(opened['lane']==int(m[2]) and opened['line'] < index-1, 'terminal connection lane and order')
            terms.append(dict(key=fail[1],peer=m[1],lane=int(m[2]),line=index,failure_line=index-1,time=timestamp(line),failure_time=timestamp(prior),open_line=opened['line'],open_time=opened['time'],failure=fail[2]))
    return lines,opens,terms,details

def strict_bijection(frames,terminals):
    assert len(frames)==len(terminals), 'terminal missing/extra'
    assert [x['op'] for x in frames]==sorted(set(x['op'] for x in frames)), 'old frame order/duplicate'
    assert all(a['line']<b['line'] and a['time']<=b['time'] for a,b in zip(terminals,terminals[1:])), 'server event order/duplicate'
    assert len({x['line'] for x in terminals})==len(terminals), 'server event reused'
    assert len({x['open_line'] for x in terminals})==len(terminals), 'connection generation reused'
    previous={}; result=[]
    for f,t in zip(frames,terminals):
        assert (f['identity']['key'],f['peer'],f['lane'])==(t['key'],t['peer'],t['lane']), 'exact occurrence key/peer/lane mismatch'
        assert t['open_line']<t['failure_line']<t['line'], 'invalid connection interval'
        if t['peer'] in previous: assert previous[t['peer']] < t['open_line'], 'overlapping repeated-peer generation'
        previous[t['peer']]=t['line']
        result.append(dict(op=f['op'],client_frame_line=f['client_line'],**t))
    return result

def negative_checks(frames,terminals):
    def rejects(ts):
        try: strict_bijection(frames,ts)
        except AssertionError: return True
        return False
    n=len(terminals); counts=Counter()
    for i in range(n):
        assert rejects(terminals[:i]+terminals[i+1:]); counts['delete_each_terminal']+=1
        assert rejects(terminals[:i]+[terminals[i]]+terminals[i:]); counts['duplicate_each_terminal']+=1
    for i in range(n-1):
        ts=terminals.copy(); ts[i],ts[i+1]=ts[i+1],ts[i]
        assert rejects(ts); counts['swap_each_adjacent_terminal']+=1
    pairs=defaultdict(list)
    for i,t in enumerate(terminals): pairs[(t['key'],t['peer'])].append(i)
    for indices in pairs.values():
        if len(indices)>1:
            ts=terminals.copy(); a,b=indices[0],indices[-1]; ts[a],ts[b]=ts[b],ts[a]
            assert rejects(ts); counts['swap_repeated_same_key_peer_occurrences']+=1
    return dict(counts)

def main():
    frozen=load(ROOT/'freeze.json'); manifest=load(ROOT/'collected-evidence-sha256.json')
    for path,record in manifest.items():
        p=ROOT/path; ck(p.stat().st_size==record['bytes'] and sha(p)==record['sha256'],'collected file size and SHA256')
    analysis_manifest=load(ROOT/'analysis-files-sha256.json')
    for path,record in analysis_manifest.items():
        p=ROOT/path; ck(p.stat().st_size==record['bytes'] and sha(p)==record['sha256'],'producer analysis file size and SHA256')
    ck((ROOT/'freeze.json').read_bytes()==(CLIENT/'freeze.json').read_bytes(),'freeze copies identical')
    ck(sha(CLIENT/'recovery-test')==frozen['binary_sha256'],'actual collected native binary hash')
    for name,field in [('run_recovery_campaign.py','runner_sha256'),('audit_recovery_campaign.py','audit_sha256')]:
        ck(sha(ROOT/name)==sha(CLIENT/name)==frozen[field],'runner or frozen auditor unchanged')
    for path,want in frozen['source_hashes'].items():
        ck(sha(REPO/path)==want,'current source frozen hash')
        snapshot=ROOT/Path(path).name
        if snapshot.exists(): ck(sha(snapshot)==want,'source snapshot frozen hash')
    ck(subprocess.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip()==frozen['core_revision'],'core revision exact')
    for source in ['kv-service/client-rs/src/rail_read.rs','kv-service/client-rs/src/rail_read/connection_pool.rs','kv-service/client-rs/src/rdma.rs','kv-service/client-rs/src/lib.rs']:
        ck(subprocess.check_output(['git','-C',str(REPO),'show',frozen['core_revision']+':'+source])==(REPO/source).read_bytes(),'production certificate and reader unchanged at core revision')
    envpaths={'small_client_env':'rxe-small-client-evidence/client.env','large_client_env':'rxe-client-evidence/client.env','small_server_config':'rxe-small-server-evidence/server.toml','large_server_config':'rxe-server-evidence/server.toml'}
    for k,path in envpaths.items(): ck(sha(WAVE/'environment'/path)==frozen['environment_sha256'][k],'environment frozen file hash')
    cutoffs=load(SERVER/'server-cutoffs.json'); server={}
    for name,c in cutoffs.items():
        p=SERVER/name
        ck(sha(p)==c['sha256'] and p.stat().st_size==c['bytes'] and len(p.read_bytes().splitlines())==c['end_line'],'sealed server cutoff size hash lines')
        ck(p.read_text().splitlines()[-1]==c['last_line'],'sealed server final line')
        server[name]=server_events(p)
    spec=importlib.util.spec_from_file_location('credited_hash_oracle',WAVE/'campaign/hash-oracle/replay_twox_hash.py')
    oracle=importlib.util.module_from_spec(spec); spec.loader.exec_module(oracle)
    gen=2654435761; buf=bytearray()
    for _ in range(2243): buf.append(gen>>56); gen=(gen*11400714785074694797)&((1<<64)-1)
    for n,want in {403:'4834389b15d981e8',512:'6a1b982631f059a8',2048:'efefd4449323cdd4',2240:'998c0437486672c7',2243:'a559d20581d742d3'}.items(): ck(oracle.h(buf[:n])==want,'historical hash known-answer vector')
    summaries=[]; allops={}; allpairs={}; frozen_status=[]; hash_receipts={}; intervals=[]; native_exits=[]; controls=Counter()
    for run in frozen['runs']:
        name=run['name']; folder=CLIENT/name; want_schedule=schedule(run); size=run['CS_RAIL_OBJECT_MIB']*1048576; seed=run['CS_RECOVERY_SEED']; kind=run['test']
        raw=(folder/'run.log').read_bytes(); text=raw.decode(); lines=text.splitlines(); launch=load(folder/'launch.json'); ex=load(folder/'exit.json'); native_exits.append(ex)
        ck(gzip.decompress((folder/'run.log.gz').read_bytes())==raw,'raw gzip byte equality')
        ck(ex['returncode']==ex['native_returncode']==0 and ex['state']=='FINISHED','native exit success')
        ck('test result: ok. 1 passed; 0 failed;' in text and 'panicked at' not in text,'native test completed exactly one selected test')
        ck(launch['command']==['/home/railtest/cswave1/wave2/recovery-holdout-v2/recovery-test','--ignored','--nocapture','--exact','rail_read::tests::recovery_campaign::'+kind],'native launch exact argv')
        for k,v in run.items():
            if k.startswith('CS_'): ck(launch['environment'][k]==str(v),'native frozen environment')
        envtext=(WAVE/'environment'/envpaths['large_client_env' if size>1048576 else 'small_client_env']).read_text()
        for k,v in re.findall(r'^export (CS_RAIL_\w+)=(.+)$',envtext,re.M):
            ck(launch['environment'][k]==str(run.get(k,v)),'route device gid environment exact')
        end=launch['started_unix_seconds']+ex['elapsed_seconds']; intervals.append((launch['started_unix_seconds'],end))
        ck(ex['elapsed_seconds']<ex['effective_budget_seconds']<=run['CS_RECOVERY_RUN_TIMEOUT_SECS'] and end<ex['campaign_deadline_unix_seconds'],'native suite watchdog and campaign deadline')
        ck(datetime.fromisoformat(frozen['frozen_before_launch_utc']).timestamp()<launch['started_unix_seconds'],'freeze precedes launch')
        ck(ex['raw_log_bytes']==len(raw) and ex['gzip_log_bytes']==(folder/'run.log.gz').stat().st_size<524288000,'log sizes and limit')
        samples=[json.loads(x) for x in (folder/'resources.jsonl').read_text().splitlines()]
        ck(len(samples)==ex['resource_sample_count']+1 and all(s['pid']==int((folder/'pid').read_text()) for s in samples),'resource sample PID and count')
        ck(samples[-1].get('process_exited') is True,'last sample process exited')
        gaps=[b['monotonic_seconds']-a['monotonic_seconds'] for a,b in zip(samples[:-1],samples[1:-1])]
        ck(all(0<g<1.02 for g in gaps),'resource sample gaps bounded observed')
        def nums(k): return [int(str(s[k]).split()[0]) for s in samples[:-1] if str(s.get(k,'')).split() and str(s[k]).split()[0].isdigit()]
        peaks=dict(rss_kib=max(nums('VmRSS')),file_descriptors=max(nums('file_descriptors')),threads=max(nums('Threads')))
        ck(peaks==ex['process_resource_peaks'] and all(peaks[k]<=v for k,v in ex['process_resource_limits'].items()),'sampled resource peaks recomputed')
        rows=[(i,l) for i,l in enumerate(lines,1) if l.startswith(PREFIX)]; frames=[decode_frame(l,i) for i,l in enumerate(lines,1) if l.startswith('RECOVERY_FRAME ')]
        ck(len(rows)==len(want_schedule)==EXPECTED_TOTALS[name][0],'complete operation schedule length')
        ck([int(fields(l)['op']) for _,l in rows]==list(range(len(rows))),'strict complete serial operation IDs')
        byop=defaultdict(list)
        for f in frames: byop[f['op']].append(f)
        ck(set(byop)<=set(range(len(rows))),'no orphan frame IDs')
        factor=19 if 'lifetime' in kind else 23 if 'publication' in kind else 29 if 'admission' in kind else 17
        base=bytes((i*factor+seed)%251 for i in range(size)); payloads={0:base}
        if 'control_campaign' in kind: payloads={slot:bytes((b+slot*41)%256 for b in base) for slot in set((int(w['key'].rsplit('-',1)[1]) for w in want_schedule))}
        hashes={slot:oracle.h(p) for slot,p in payloads.items()}; hash_receipts[name]=hashes
        version_b_hash=oracle.h(bytes((x+73)%256 for x in base)) if name=='cuts480' else None
        operations=[]; publications=0; late=[]; ack=0; key_identities={}; matrix=Counter(); previous_row=0
        mutation_rows=[(i,fields(l)) for i,l in enumerate(lines,1) if l.startswith('RECOVERY_MUTATION ')]
        mutation_map={int(v['op']):(i,v) for i,v in mutation_rows}
        ck(len(mutation_map)==len(mutation_rows),'unique mutation records')
        post=[(i,fields(l)) for i,l in enumerate(lines,1) if l.startswith('RECOVERY_POST_LOOKUP_RELAY ')]
        for (lineno,line),want in zip(rows,want_schedule):
            v=fields(line); op=want['op']; fs=byop[op]; cut=want.get('cut'); control=want.get('control','F2'); failed=want.get('failed_lane'); canonical='17:recovery-campaign'+want['key']
            for k,w in want.items():
                if k in ('local','key') and k not in v: continue
                ck(v[k]==(str(w).lower() if isinstance(w,bool) else str(w)),'per-operation frozen schedule field')
            ck(all(previous_row<f['client_line']<lineno for f in fs),'all operation frames before own receipt and after prior receipt')
            if name not in ('publication160',): ck(v.get('budget_quiescent')=='true','per-operation budget quiescent receipt')
            expected_late=expected_ack=0
            if 'control_campaign' in kind:
                needed={'F0':2,'F1':4,'F2':3}[control]; expected_late=1
                retry={'F0':0,'F1':size,'F2':size//2}[control]
                ck(int(v['expected_retry'])==int(v['observed_retry'])==retry,'control exact requested retry bytes')
                ck(v['old_terminal']=='true' and v['manual_rearm']=='true' and int(v['frames'])==needed,'control old terminal manual rearm and frame receipt')
                ck(v['expected_hash']==hashes[int(want['key'].rsplit('-',1)[1])],'control independent generated payload hash')
                ck(v['terminal']==('expected_error' if control=='F0' else 'exact_publication'),'control exact terminal')
                if want['pooled']:
                    pool={k:int(x) for k,x in re.findall(r'(\w+): (\d+)',line.split('pool=',1)[1])}
                    i=want['local']; nr={'F0':0,'F1':2,'F2':1}[control]
                    ck(pool==dict(created=i+2,reused=i*(1+nr)+nr,retired=i+1,rejected=0,live=1,connecting=0,active=0,idle=1,retiring=0),'control exact pool creation reuse retirement counters')
                else: ck(v['pool']=='None','unpooled control has no pool')
                publications+=int(control!='F0')
                if name=='matched-controls240': controls[control]+=retry
            elif name=='cuts480':
                needed=2 if cut in ('CancelInitial','DeadlineRetryGate') else 3
                expected_late=int(cut in ('CancelRetry','Overwrite','Delete')); expected_ack=int(cut in ('AckTimeout','DeadlineRetryGate'))
                retry=0 if cut in ('CancelInitial','DeadlineRetryGate') else size//2
                ck(int(v['retry_bytes'])==retry and int(v['retry_tasks'])==int(retry>0),'cut exact requested retry bytes and tasks')
                ck(v['initial_hash']==hashes[0] and v['old_terminal']==str(cut!='CancelInitial').lower(),'cut independent payload and terminal state')
                ck(v['terminal']==('exact_publication_after_ack_timeout' if cut=='AckTimeout' else 'expected_atomic_rejection'),'cut expected terminal'); publications+=int(cut=='AckTimeout')
                if cut in ('Overwrite','Delete'):
                    mi,m=mutation_map[op]; ck(previous_row<mi<lineno and m['key']==want['key'] and m['cut']==cut,'mutation inside own operation')
                    ck(int(m['old_generation'])==fs[0]['identity']['generation'],'mutation old generation binds frames')
                    if cut=='Overwrite': ck(int(m['new_generation'])>int(m['old_generation']) and m['version_b_hash']==version_b_hash and m['observed_before_retry_ack_release']=='true','overwrite real changed generation known B before release')
                    else: ck(m['observed_deleted_before_retry_ack_release']=='true','delete acknowledged before retry ACK release')
            elif name=='lifetime2000':
                needed=2; retry=0; publications+=1
                ck(v['exact_payload']=='true' and v['expected_hash']==hashes[0],'lifetime independent payload hash and exact publish')
                pool={k:int(x) for k,x in re.findall(r'(\w+): (\d+)',line.split('pool=',1)[1])}
                ck(pool==dict(created=2,reused=0 if want['close_each'] else 2*want['local'],retired=0,rejected=0,live=2,connecting=0,active=0,idle=2,retiring=0),'lifetime every exact pool counter')
            elif name=='publication160':
                needed=3; expected_late=1; retry=size//2
                ck(v['actual_postlookup_response_intercepted']==v['sentinel_unchanged']==v['server_identity_unchanged']=='true','publication sentinel identity interception')
                ck(line.endswith('terminal='+('object version changed during rail read' if want['capability_control_injection'] else 'rail read deadline exceeded')),'publication typed terminal')
                pi,p=post[op]; ck(previous_row<pi<lineno and p['stream']=='3' and p['initial_response_seen']==p['complete_actual_response']=='true' and p['capability_control_injection']==v['capability_control_injection'] and int(p['delay_ms'])==(0 if want['capability_control_injection'] else 4000),'actual postlookup relay per-operation receipt')
            else:
                needed=int(cut!='registered_byte_cap'); expected_late=needed; retry=0
                ck(v['sentinel_unchanged']=='true' and v['retry_tasks']=='0','admission atomic zero retry')
                ck(('terminal=rail resource limit: registered bytes exceed' if cut=='registered_byte_cap' else 'terminal=rail transfer failed: failed to fill whole buffer') in line,'admission expected typed error text')
            ck(len(fs)==needed,'per-operation exact frame count')
            ck(sum(f['terminal']=='eof_after_late_request' for f in fs)==expected_late,'per-operation exact old terminal count')
            ck(sum(f['terminal']=='success_ack_held_until_client_timeout' for f in fs)==expected_ack,'per-operation exact held success ACK count'); ack+=expected_ack
            ck(len({(f['lane'],f['sequence']) for f in fs})==len(fs),'frame lane sequence unique')
            if fs:
                ident=fs[0]['identity']; ck(all(f['identity']==ident for f in fs),'per-operation immutable identity')
                ck(ident['key']==canonical and ident['size']==size and ident['chunk_size']==size//4 and ident['striped']==1 and ident['stripe_count']==4 and ident['layout']==1 and ident['generation']>0 and bool(ident['handle']) and bool(ident['etag']),'full frame expected frozen object identity')
                if name!='cuts480':
                    ck(key_identities.setdefault(canonical,ident)==ident,'cross-operation same key frozen identity')
                if 'generation' in v: ck(int(v['generation'])==ident['generation'],'receipt generation binds wire')
                if name=='publication160': ck(int(post[op][1]['descriptor_generation'])==ident['generation'],'postlookup generation binds wire')
                initial=[f for f in fs if f['sequence']==1]; retries=[f for f in fs if f['sequence']>1]
                ck(sorted(s for f in initial for s in f['stripes'])==list(range(4)),'initial frames exactly cover all four stripes')
                for f in fs:
                    ck(f['control']==control and f['lane'] in (0,1) and f['peer'].startswith(('10.81.1.2:' if f['lane']==0 else '10.82.1.2:')),'frame control and actual lane peer')
                    ck(len(f['segments'])==4 and all(s[0]>0 and s[1]>0 and s[2]==size//4 for s in f['segments']),'all native SGE sizes addresses keys')
                    ck(len(f['stripes'])==len(set(f['stripes'])) and all(0<=s<4 for s in f['stripes']),'native stripe subset valid')
                    ck(f['terminal'] in ('success_ack','eof_after_late_request','success_ack_held_until_client_timeout'),'only supported frame terminal states')
                    if f['sequence']==1 and name!='admission24':
                        # Production contiguous_for_single_owner assigns adjacent
                        # half-object tasks in direct staging; packed uses parity.
                        expected_stripes=list(range(f['lane']*2,f['lane']*2+2)) if want['direct'] else list(range(f['lane'],4,2))
                        ck(f['stripes']==expected_stripes,'initial lane stripe assignment exact')
                    if f['terminal']=='eof_after_late_request': ck(f['lane']==failed and f['sequence']==1,'old frame exact failed lane'); late.append(f)
                    if f['terminal']=='success_ack_held_until_client_timeout': ck(f['lane']==failed and f['sequence']==1,'held ACK exact failed lane')
                ck(sum(sum(f['segments'][s][2] for s in f['stripes']) for f in retries)==retry,'wire requested retry payload exact')
                if retries:
                    ck(all(f['lane']==1-failed for f in retries),'all retries use healthy lane')
                    ck(sorted(f['sequence'] for f in retries)==list(range(2,2+len(retries))),'retry sequence exact')
                    if control=='F1': ck(sorted(s for f in retries for s in f['stripes'])==list(range(4)),'F1 retries complete frozen object')
                    else:
                        old=next(f for f in initial if f['lane']==failed)
                        ck(len(retries)==1 and retries[0]['stripes']==old['stripes'],'F2 retry only failed frozen task')
                        if want['direct']:
                            ck(all(retries[0]['segments'][s][0]==old['segments'][s][0] for s in old['stripes']),'direct retry reuses exact retired range')
            matrix[tuple((k,str(want[k])) for k in ('direct','pooled','control','cut','close_each','capability_control_injection','failed_lane') if k in want)]+=1
            operations.append(dict(**want,client_receipt_line=lineno,terminal=line.split(' terminal=',1)[1].split(' elapsed_us=',1)[0] if ' terminal=' in line else 'exact_publication',requested_retry_bytes=retry,frames=fs))
            previous_row=lineno
        ck(len(mutation_map)==(160 if name=='cuts480' else 0),'all mutation receipts accounted')
        ck(len(post)==(160 if name=='publication160' else 0),'all postlookup receipts accounted')
        servername='large-server.log' if size>1048576 else 'small-server.log'; slines,opens,terms,details=server[servername]
        keys={'17:recovery-campaign'+w['key'] for w in want_schedule}; selected=[t for t in terms if t['key'] in keys]
        proof=strict_bijection(late,selected)
        producer_proof=load(ROOT/'audits'/f'{name}-ordered-terminal-bijection.json')['proof']
        ck([(p['op'],p['key'],p['peer'],p['failure_line'],p['line']) for p in proof]==[(p['op'],p['canonical_key'],p['peer'],p['server_failure_line'],p['server_terminal_line']) for p in producer_proof],'producer supplemental mapping independently reproduced')
        ck(len(frames)==EXPECTED_TOTALS[name][2] and len(proof)==EXPECTED_TOTALS[name][3] and publications==EXPECTED_TOTALS[name][1],'suite totals independently reconciled')
        selected_details=[d for d in details if d['key'] in keys]
        ck(Counter((f['identity']['key'],sum(f['segments'][s][2] for s in f['stripes']),len(f['stripes'])) for f in frames)==Counter((d['key'],d['bytes'],d['stripes']) for d in selected_details),'all native frames reconciled to complete server request detail multiset')
        ck(all(d['requests']==d['bytes']//min(size//4,1048576) for d in selected_details),'server WR request counts match fixture chunking')
        # All source routines await the current read (and every injected old
        # terminal) before their next op. Split the complete same-suite server
        # request stream at the independently derived per-op frame counts.
        # Server detail lacks peer, so attribution of identical initial tasks
        # is deliberately only to the operation, not to an individual lane.
        detail_cursor=0; proof_by_op={p['op']:p for p in proof}
        for operation in operations:
            n=len(operation['frames']); chunk=selected_details[detail_cursor:detail_cursor+n]; detail_cursor+=n
            ck(Counter((f['identity']['key'],sum(f['segments'][s][2] for s in f['stripes']),len(f['stripes'])) for f in operation['frames'])==Counter((d['key'],d['bytes'],d['stripes']) for d in chunk),'each operation complete server detail payload multiset')
            if operation['op'] in proof_by_op:
                terminal=proof_by_op[operation['op']]
                ck(any(d['line']==terminal['failure_line']-1 and d['key']==terminal['key'] for d in chunk),'old terminal previous request detail belongs to same operation')
            operation['server_request_details']=chunk
        ck(detail_cursor==len(selected_details),'no omitted server requests after operation mapping')
        ck(all(launch['started_unix_seconds']<=t['time']<=end for t in selected),'coarse walltime suite containment not latency or identity')
        ck(all(t['failure'].startswith('poll final RDMA completions: poll_n timeout after '+('30000' if size>1048576 else '1000')+'ms') for t in selected),'actual native server CQ timeout fixture')
        # Exact key/peer overlap is resolved by unique recorded connection-open
        # events plus serial occurrence, not by global source-port uniqueness.
        negative=negative_checks(late,selected) if selected else {}
        output=HERE/'recovery-frozen-auditor-replay'/name
        output.parent.mkdir(exist_ok=True)
        proc=subprocess.run([sys.executable,str(CLIENT/'audit_recovery_campaign.py'),'--client',str(folder/'run.log.gz'),'--server',str(SERVER/servername),'--expected-operations',str(len(rows)),'--freeze',str(ROOT/'freeze.json'),'--name',name,'--output',str(output.with_suffix('.json'))],capture_output=True,text=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
        output.with_suffix('.log').write_text(proc.stdout+proc.stderr)
        ck(proc.returncode==(1 if name=='f2-recovery1000' else 0),'unchanged frozen auditor expected original status')
        if proc.returncode: ck('missing/ambiguous exact-peer WR retirement' in proc.stderr,'frozen RED same exact cause')
        frozen_status.append(dict(suite=name,returncode=proc.returncode))
        groups=defaultdict(list)
        for t in proof: groups[(t['key'],t['peer'])].append(t)
        reused=[dict(key=k[0],peer=k[1],occurrences=v) for k,v in groups.items() if len(v)>1]
        allops[name]=operations; allpairs[name]=proof
        summaries.append(dict(suite=name,operations=len(rows),publications=publications,atomic_rejections=len(rows)-publications,frames=len(frames),old_terminals=len(proof),held_success_ack=ack,server_request_details=len(selected_details),reused_key_peer_groups=reused,negative_tests=negative,matrix=[dict(fields=dict(k),count=v) for k,v in matrix.items()],sampled_peaks=peaks,sampled_hwm_kib=max(nums('VmHWM')),resource_samples=len(samples)-1,sample_max_gap=max(gaps),started=launch['started_unix_seconds'],finished=end,elapsed=ex['elapsed_seconds'],watchdog=ex['run_timeout_seconds']))
        print(json.dumps(dict(suite=name,operations=len(rows),frames=len(frames),terminals=len(proof),frozen_audit=proc.returncode,negative_tests=negative)),flush=True)
    ck(all(a[1]<b[0] for a,b in zip(intervals,intervals[1:])),'native suites serial nonoverlapping launch intervals')
    server_span_audit={}
    for servername,(_,opens,terms,details) in server.items():
        suite_names=[r['name'] for r in frozen['runs'] if (r['CS_RAIL_OBJECT_MIB']>1)==(servername=='large-server.log')]
        ownership={f['identity']['key']:name for name in suite_names for op in allops[name] for f in op['frames']}
        chosen=[d for d in details if d['key'] in ownership]
        span=[d for d in details if chosen[0]['line']<=d['line']<=chosen[-1]['line']]
        ck(span==chosen,'no foreign data requests inside entire formal server stream')
        expected_stream=[name for name in suite_names for _ in range(EXPECTED_TOTALS[name][2])]
        ck([ownership[d['key']] for d in chosen]==expected_stream,'all server suite request groups serial and complete')
        formalterms=[t for t in terms if chosen[0]['line']<=t['line']<=len(server[servername][0])]
        ck(len(formalterms)==sum(EXPECTED_TOTALS[name][3] for name in suite_names) and all(t['key'] in ownership for t in formalterms),'no extra old terminal or foreign old request before sealed cutoff')
        server_span_audit[servername]=dict(first_formal_request_line=chosen[0]['line'],last_formal_request_line=chosen[-1]['line'],formal_request_count=len(chosen),formal_terminal_count=len(formalterms),foreign_requests_in_formal_span=0,extra_old_terminals_until_cutoff=0,scope='Recorded fixture data-request stream; not a claim of global host process exclusivity.')
    ck(len({e['campaign_deadline_unix_seconds'] for e in native_exits})==1,'one frozen campaign deadline')
    decoder=json.JSONDecoder(); rest=(CLIENT/'launcher.log').read_text(); launcher=[]
    while rest.strip():
        rest=rest.lstrip(); obj,n=decoder.raw_decode(rest); launcher.append(obj); rest=rest[n:]
    ck(launcher==native_exits,'launcher all seven native receipts same order')
    ck(controls=={'F0':0,'F1':80*1048576,'F2':80*524288},'matched 80 per-control bytes exact')
    q=load(CLIENT/'guest-quiescence.json')
    ck(q['owned_process_count']==0 and q['owned_processes_remaining']==[] and q['tmux_has_session_returncode']==1 and q['rdma_mr_readback']=='' and len(q['rdma_qp_readback'].splitlines())==2 and all('type GSI' in l for l in q['rdma_qp_readback'].splitlines()),'post-run guest owned processes and registered MR snapshot empty')
    ck(q['observed_unix_seconds']>intervals[-1][1],'quiescence snapshot after whole recovery campaign')
    for path,record in manifest.items(): ck(sha(ROOT/path)==record['sha256'],'collected evidence unmodified after replay')
    totals={k:sum(s[k] for s in summaries) for k in ('operations','publications','atomic_rejections','frames','old_terminals','held_success_ack')}
    ck(totals==dict(operations=3908,publications=3244,atomic_rejections=664,frames=9508,old_terminals=1660,held_success_ack=160),'formal totals')
    summary=load(ROOT/'local-validation-summary.json'); ppt=load(ROOT/'ppt-recovery-summary.json')
    for k,v in [('formal_operations',3908),('exact_publications',3244),('expected_atomic_rejections',664),('native_frames',9508)]: ck(summary[k]==ppt[k]==v,'producer summary reported totals agree with independent replay')
    ck(summary['frozen_auditor_failed_suites']==1 and ppt['latency_throughput_claim'] is False,'summary retains RED and no performance inference')
    math=dict(status='PASS_SCOPED_SUPPLEMENTAL_TERMINAL_PROOF_ACCEPTED',audit_utc=datetime.now(timezone.utc).isoformat(),scope='Seven sealed native RXE suites; correctness, atomicity, requested retry bytes. Original frozen auditor remains 6 PASS / 1 RED.',checks=dict(checks),totals=totals,frozen_auditor=frozen_status,suites=summaries,server_span_audit=server_span_audit,matched_requested_retry_bytes=dict(controls),independent_payload_hashes=hash_receipts,full_operation_map='recovery-final-operation-map.json',full_terminal_map='recovery-final-terminal-map.json',hash_oracle_credit='Imported unchanged campaign/hash-oracle/replay_twox_hash.py; MIT twox-hash 1.6.3 historical algorithm; five known-answer vectors rerun.',hash_oracle_sha256=sha(WAVE/'campaign/hash-oracle/replay_twox_hash.py'),freeze_sha256=sha(ROOT/'freeze.json'),collected_manifest_sha256=sha(ROOT/'collected-evidence-sha256.json'))
    (HERE/'recovery-final-operation-map.json').write_text(json.dumps(allops,indent=2)+'\n')
    (HERE/'recovery-final-terminal-map.json').write_text(json.dumps(allpairs,indent=2)+'\n')
    (HERE/'recovery-final-review-math.json').write_text(json.dumps(math,indent=2)+'\n')
    print(json.dumps(dict(status=math['status'],totals=totals,checks=sum(checks.values()))))

if __name__=='__main__': main()
