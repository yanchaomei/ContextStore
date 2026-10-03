# Historical unoptimized-baseline evaluation (2026-10-03)

**Revision notice:** the one-Rail implementation measured here performed an avoidable full-object reassembly copy. A later [controlled baseline ablation and complete RXE rerun](baseline-ablation-results.md) found that removing that copy speeds one physical-HCA Rail by 1.426× and changes the one/two-Rail software conclusion: the optimized 64 MiB sequential one-Rail path wins all ten paired blocks. Use the later result as the current performance assessment. This page preserves the original code revision and negative blocks for auditability.

This is an evidence record for the paper candidate, not a claim of physical multi-HCA aggregation. The complete [ten-block Soft-RoCE dataset](../../kv-service/benchmarks/results/paper10-rxe-2026-10-03/) includes per-arm raw stdout, samples, batches, summaries, topology, the server log, binary hashes, a failed resource-boundary run and an [audited JSON](../../kv-service/benchmarks/results/paper10-rxe-2026-10-03/audit.json). The [editable figure](figures/rxe-ten-blocks.svg) plots every paired block and its interval. The independent [physical HCA stage pilot](stage-trace-pilot.md) answers a different question.

## Setup and unit of analysis

The server and one client Worker ran in separate Ubuntu 22.04 KVM guests on one SKV host, each with four vCPUs and 4 GiB RAM. Two virtio Ethernet pairs used separate Linux bridges and independently discovered `rxe_s0/rxe_c0` and `rxe_s1/rxe_c1` Soft-RoCE devices. The server's two stripe directories were both on `/dev/vda1` (one virtual disk). The server binary SHA-256 was `51e13244c4309c15d2cd519050caa3b42edac558243112c0487dbcbf4329add7`; the client benchmark binary was `41ee6b203c806653c1b422ec1157494f9ccf18f9b8de1fd6c299b8685e6e9679`. This client includes opt-in research timing code but was run with timing disabled. The test objects were 64 MiB/16 stripes (`xxh3=a0a4cbfa5cad46af`) and 256 MiB/64 stripes (`xxh3=fe51c755b1336171`), with the same descriptor/layout used in both arms of each cell.

One **block** is a pair of separately launched one-Rail and two-Rail client processes. The arms alternate AB/BA by block number. Each process performs one warmup, then five measured sequential reads or three measured concurrent batches. The unit for an interval is the ten paired *blocks*, never the repeated reads within a process. The audit recomputes sequential ratios from raw arm medians and concurrent ratios from raw batch wall times, verifies identical object hashes and byte counts, and checks that per-Rail completed bytes sum to the requested payload. Intervals are seeded 20,000-draw percentile bootstraps of the paired log throughput ratios, exponentiated to the geometric-mean scale. They are exploratory intervals over these ten launched blocks; they do not cover VM-host variation, multiple comparison correction, or a physical NIC population.

| Object | Concurrent reads | Two/one Rail geometric mean | 95% block bootstrap | Blocks favoring two Rails | Median CPU ms/read, one → two | Median client peak RSS MiB, one → two |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 64 MiB | 1 | 1.056× | 1.010–1.098× | 7/10 | 136.0 → 148.4 | 198.2 → 206.3 |
| 256 MiB | 1 | 1.101× | 1.049–1.151× | 8/10 | 493.9 → 517.2 | 774.4 → 782.5 |
| 64 MiB | 4 | 1.038× | 0.983–1.088× | 7/10 | 148.7 → 156.6 | 803.2 → 813.3 |
| 256 MiB | 2 | 1.067× | 1.032–1.102× | 9/10 | 554.5 → 576.3 | 1798.4 → 1814.7 |

The 64 MiB/concurrency-4 cell has three blocks favoring one Rail, with a worst dual/single ratio of **0.891×**; its interval crosses 1. The 64 MiB sequential cell also has three negative blocks. The 256 MiB cells show higher central ratios, yet both still include negative individual blocks. Added Rail bookkeeping costs CPU and client memory in every cell. These measurements characterize the **unoptimized implementation only** and show that adding a second Rail was not uniformly helpful even there. They do not establish HCA offload, NUMA, PCIe or physical bandwidth aggregation.

## Resource-boundary observation

The first 256 MiB/concurrency-2 collector run failed before completing a paired block. The guest's default `RLIMIT_MEMLOCK` was 512,012,288 bytes; the implementation caps its admissible registered-memory budget at 80%, or **409,609,831 bytes**. Two simultaneous 256 MiB single-Rail receive regions exceed that budget, so the request returned `rail resource limit` without publishing an incomplete object. The [failure log](../../kv-service/benchmarks/results/paper10-rxe-2026-10-03/memlock-failure.log) is retained. A minimal reproduction succeeded after raising the isolated client process's lock limit to unlimited; all ten 256 MiB/concurrency-2 blocks were then run under that same limit for both arms. This was a resource-policy change for that cell, not a performance fix. Comparisons of CPU/RSS *between different cells* also differ in memory-lock policy and concurrency; only within-cell one/two-Rail pairs are controlled.

## Physical-path interpretation

On a separate node1-to-node2 physical ConnectX-6 Dx path, three 1-QP `ib_write_bw` runs reached a median 97.94 Gb/s on a 100 Gb/s port, while a checksummed 64 MiB ContextStore single-Rail read remained around 0.4 GiB/s payload. A same-object stage pilot attributes substantial time to receive-buffer initialization, assembly and final publication. The `GET/CQ` interval includes remote service, CQ and transfer, so it is not pure wire time. This evidence rejects a *demonstrated* NIC bottleneck for that workload; it does not prove that another, larger or better optimized workload cannot saturate the link. The physical setup exposed only one online HCA path per node, and no physical two-Rail result exists.

The [raw perftest/server diagnostic logs](../../kv-service/benchmarks/results/paper-hca-link-raw-2026-10-03/) and `build_hca_link_evidence.py` reproduce the checked-in [capacity comparison JSON](../../kv-service/benchmarks/results/2026-10-03-skv-single-hca-link.json) byte-for-byte. The perftest and ContextStore reads are deliberately different workloads; their ratio is a link-capacity sanity check, not an application speedup estimate.

## Reproduction and audit

1. Inspect `topology-server.txt`, `topology-client.txt`, `server.toml` and each `*-environment.json` in the raw dataset. The two physical paths here are **virtual RXE** paths on one host, with one virtual disk.
2. Rerun `python3 kv-service/benchmarks/paper_pair_audit.py --results kv-service/benchmarks/results/paper10-rxe-2026-10-03 --prefix paper10-sequential --prefix paper10-c4 --prefix paper10-c2 --output kv-service/benchmarks/results/paper10-rxe-2026-10-03/audit.json`.
3. Rerun `python3 kv-service/benchmarks/plot_paper10_rxe.py --audit kv-service/benchmarks/results/paper10-rxe-2026-10-03/audit.json --output-prefix docs/paper/figures/rxe-ten-blocks`.

The evaluation still lacks ten independent **host/environment** repetitions, matched link-byte and storage-source counters, a verified physical dual-HCA setup and a causal ablation of staging costs. The current sample supports a bounded experience/evaluation claim; a strong systems-performance paper needs those controls and a distinct mechanism beyond static multi-rail scheduling.
