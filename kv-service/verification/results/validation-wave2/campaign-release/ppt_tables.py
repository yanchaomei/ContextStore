import pathlib,json,csv,math,statistics
P=pathlib.Path(__file__).resolve().parent;R=P/'client-evidence/performance-release-20261029-v1';a=json.loads((R/'analysis.json').read_text());rows=[];descriptive=[]
def gm(v):return math.exp(statistics.mean(math.log(x) for x in v))
for c in a['cells']:
 b=c['blocks'];row={'size_mib':c['size_mib'],'concurrency':c['concurrency'],'independent_pairs':10,'R0_geomean_process_median_us':gm(c['R0_medians_us']),'R1_geomean_process_median_us':gm(c['R1_medians_us']),'R0_over_R1':c['ratio_R0_over_R1'],'bootstrap95_lower':c['bootstrap_95pct'][0],'bootstrap95_upper':c['bootstrap_95pct'][1],'log_t95_lower':c['logratio_t95_df9'][0],'log_t95_upper':c['logratio_t95_df9'][1],'R1_slower_pairs':c['R1_slower_blocks'],'median_latency_reduction_percent':c['latency_reduction_percent'],'p95_ratio_descriptive':c['p95_ratio_descriptive']};rows.append(row)
 d={'size_mib':c['size_mib'],'concurrency':c['concurrency'],'p95_paired_ratios':[p['R0']['steady_p95_us']/p['R1']['steady_p95_us'] for p in b],'cpu_scope':'all 35 batches, including startup; compare R0/R1 only within this concurrency. Serial excludes later pattern hash; concurrent includes coordination and hash','variants':{}}
 for v in ['R0','R1']:
  d['variants'][v]={'cpu_user_plus_system_us_per_process':[int(p[v]['summary']['cpu_user_us'])+int(p[v]['summary']['cpu_system_us']) for p in b],'peak_rss_kib_per_process':[int(p[v]['summary']['peak_rss_kb']) for p in b],'startup_5batch_medians_us':[statistics.median(p[v]['startup_samples_us']) for p in b],'final_pool_per_process':[p[v]['final_pool'] for p in b]}
 descriptive.append(d)
with (P/'ppt-performance-table.csv').open('w') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
(P/'ppt-performance-table.json').write_text(json.dumps({'state':a['state'],'scope':a['scope'],'metric':a['primary_metric'],'rows':rows},indent=2)+'\n')
(P/'additional-descriptive-metrics.json').write_text(json.dumps(descriptive,indent=2)+'\n')
for r in rows:print(f"{r['size_mib']}MiB C{r['concurrency']}: {r['R0_over_R1']:.4f}x CI [{r['bootstrap95_lower']:.4f}, {r['bootstrap95_upper']:.4f}], R1 slower {r['R1_slower_pairs']}/10")
