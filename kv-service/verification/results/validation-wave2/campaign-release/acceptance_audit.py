import pathlib,json,hashlib,datetime
P=pathlib.Path(__file__).resolve().parent;R=P/'client-evidence/performance-release-20261029-v1';C=P/'client-evidence/performance-release-control-v1'
a=json.loads((R/'analysis.json').read_text());m=json.loads((R/'manifest.json').read_text());i=json.loads((R/'run-index.json').read_text());c=json.loads((P/'collection-receipt.json').read_text());h=json.loads((P/'independent-hash-replay.json').read_text());d=json.loads((P/'server-nine-object-metadata.json').read_text());f=json.loads((C/'protocol.json').read_text());issues=[]
checks={
 'analysis_all_120_slots_and_hashes_valid':not a['issues'] and a['observed_measurement_processes']==120 and a['total_measured_requests']==10500,
 'manifest_completed':m['state']=='COMPLETED',
 'raw_evidence_matches_remote_sha_manifest':all(hashlib.sha256((R/n).read_bytes()).hexdigest()==want for n,want in json.loads((R/'raw-sha256.json').read_text()).items()),
 'R_matrix_matches_parent_frozen_contract':f['R_matrix_original']==json.loads((P.parent/'campaign-freeze.json').read_text())['R_matrix'],
 'binaries_unchanged':m['binaries_unchanged'],
 'server_pid_startticks_unchanged':c['server_same_pid_startticks'],
 'server_config_unchanged':c['server_same_config'],
 'server_environment_unchanged':c['server_same_environment'],
 'runtime_source_unchanged':c['source_unchanged'] and c['head_after']=='1c3ee135d19fece67507b51acd1fc51ae9541208',
 'runner_analyzer_snapshot_match_freeze':all(hashlib.sha256((C/n).read_bytes()).hexdigest()==sha for n,sha in f['control_sha256'].items()),
 'known_hashes_independent_KAT_replay':len(h['known_answer_vectors_passed'])==5 and all(h['deterministic_pattern_i_mod_251_hashes'][s]==v for s,v in json.loads((R/'known-pattern-hashes.json').read_text()).items()),
 'three_identities_per_size':len(a['identities'])==9,
 'all_nine_server_objects_correct_stripe_layout':len(d['objects'])==9 and all(o['stripe_count']==2*o['size_mib'] and o['stripe_bytes']==524288 and o['object_generation']==1 and o['layout_version']==1 for o in d['objects']),
 'primary_no_tracing_environment_or_output':all('CS_RAIL_TIMING' not in x['environment'] and 'RAIL_' not in (R/(x['name']+'.stderr')).read_text() for x in i if x['phase']=='measure'),
 'all_primary_and_diagnostic_processes_have_per_process_PASS_receipt':all(json.loads((R/(x['name']+'.validation.json')).read_text())['state']=='PASS' for x in i if x['phase']!='seed'),
 'release_amendment_predeclared':f['amendment']==json.loads((P.parent/'release-profile-amendment.json').read_text()),
 'release_server_binary_matches_build_freeze':json.loads((P/'server-after.json').read_text())['server']['exe_sha256']==f['server_build']['binary_sha256'],
 'actual_profile_release':m['server_profile']=='release' and f['server_build']['profile']=='release' and f['server_build']['server_features']==['io-uring','rdma','metrics'],
 'nine_fresh_seed_processes':len([x for x in i if x['phase']=='seed'])==9 and all('inherited_from_campaign' not in x for x in i),
 'fresh_release_metadata_namespace':all('contextstore:wave2-release-matrix512k-20261029:' in x['key'] for x in d['objects']),
 'debug_raw_evidence_unchanged':all(hashlib.sha256((P.parent/'campaign/client-evidence/performance-20261029-v2'/n).read_bytes()).hexdigest()==want for n,want in json.loads((P.parent/'campaign/client-evidence/performance-20261029-v2/raw-sha256.json').read_text()).items()),
 'no_primary_process_mixed_from_v1':all('inherited_from_campaign' not in x for x in i if x['phase']=='measure'),
}
for k,v in checks.items():
 if not v:issues.append(k)
result={'state':'LOCAL_ACCEPTANCE_PASS_PENDING_INDEPENDENT_REVIEW' if not issues else 'FAILED','at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'checks':checks,'issues':issues,'measurement_processes':120,'primary_steady_request_subsamples':9000,'startup_request_subsamples':1500,'diagnostic_reads':36,'object_count':9,'server_profile':'release','selection_rule':'main deployment result regardless of outcome; debug separate','storage_scope':'buffered tier_a4, application L1 bypass, warm OS page cache; not physical media or NVMe bandwidth','independent_performance_review':'pending'}
(P/'acceptance-audit.json').write_text(json.dumps(result,indent=2)+'\n')
files={str(p.relative_to(P)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(P.rglob('*')) if p.is_file() and p.name!='campaign-file-sha256.json' and '__pycache__' not in p.parts}
(P/'campaign-file-sha256.json').write_text(json.dumps(files,indent=2)+'\n');print(json.dumps(result,indent=2))
