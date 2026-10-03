# Baseline fairness changes the multi-Rail conclusion

**Date:** 2026-10-03. **Code change:** [single-Rail handoff design](single-rail-baseline-design.md) and its implementation on this research branch. When the sole Rail has received every stripe in object order, the reader moves that private, already quiesced receive vector into `StagedRailRead`. It still verifies checksums, rechecks the object identity and copies to the caller only at the publication gate. The two-Rail assembly path is unchanged. This is an optimized baseline and a performance control, not a new multi-Rail scheduling algorithm.

## Physical single-HCA code-revision ablation

The old and optimized client binaries read the **same** 64 MiB / 16-stripe object on node1 → node2 through `mlx5_1` port 1/GID 3 and the same server/configuration. Every block consists of two independently launched client processes, with old/new order alternating AB/BA; each process performs one warmup and five measured full-object reads. The baseline binary SHA-256 is `41ee6b203c806653c1b422ec1157494f9ccf18f9b8de1fd6c299b8685e6e9679`; the candidate is `fc85231d073248620d036ee3ece85a42a4fbc6cb500e43cb15c1fc6daee8a7eb`. The [raw arm outputs, CSVs and audit](../../kv-service/benchmarks/results/paper-fastbaseline-hca-2026-10-03/) verify the same object hash (`a0a4cbfa5cad46af`), exact bytes and alternating order. The [figure](figures/physical-single-rail-nocopy-ablation.svg) shows every block.

| Metric | Original one Rail | Optimized one Rail |
| --- | ---: | ---: |
| Median of ten process medians | 144.802 ms | 108.127 ms |
| Median CPU user+system per read | 120.2 ms | 68.5 ms |
| Median client peak RSS | 199.2 MiB | 135.3 MiB |
| Object assembly stage, traced marginal median | 53.2 ms | approximately 0 μs |

The **geometric mean of the ten paired old/new latency ratios is 1.426×**, with a seeded 20,000-draw 95% block-bootstrap interval of **1.342–1.534×**; all ten blocks favor the optimized baseline. The ratio of the two aggregate medians in the table is a different statistic. Both binaries keep private receive memory and the same final caller-buffer copy; the candidate removes one avoidable full-object allocation/copy. The traced stage observations come from a separate two-process pilot on each revision and are diagnostic, not part of the ten-block revision interval. This is a single-HCA application improvement, not network aggregation.

The new pointer-identity test failed on the original implementation and passed after the vector handoff. The [recorded RDMA-feature test run](../../kv-service/benchmarks/results/paper-fastbaseline-hca-2026-10-03/client-rdma-tests.log) reports 46 passed, one pre-existing Mock performance test ignored; the [default-feature build/test run](../../kv-service/benchmarks/results/paper-fastbaseline-hca-2026-10-03/client-default-tests.log) also passed. A new one-Rail checksum-failure test confirms the caller sentinel remains unchanged. These test outcomes supplement, rather than replace, the real-Verbs read and fault receipts.

## Repeated one/two-Rail RXE matrix after the baseline fix

The **same optimized client binary** (`fc85231d…`) was used in both arms of each new one/two-Rail block on the same two KVM guests and independently configured RXE paths as the original matrix. Each workload has ten AB/BA process blocks. The [full raw dataset](../../kv-service/benchmarks/results/paperfast-rxe-2026-10-03/), [audit JSON](../../kv-service/benchmarks/results/paperfast-rxe-2026-10-03/audit.json) and [figure](figures/rxe-fastbaseline-ten-blocks.svg) retain every negative block. Intervals again resample **blocks**, never requests within a process.

| Object | Concurrent reads | Two/one Rail geometric mean | 95% block bootstrap | Blocks favoring two Rails | Median CPU ms/read, one → two | Median client peak RSS MiB, one → two |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 64 MiB | 1 | **0.904×** | **0.874–0.934×** | **0/10** | 84.0 → 148.5 | 134.4 → 206.3 |
| 256 MiB | 1 | 0.958× | 0.908–1.013× | 4/10 | 284.7 → 509.2 | 518.5 → 782.5 |
| 64 MiB | 4 | 0.991× | 0.959–1.022× | 5/10 | 95.5 → 157.4 | 583.2 → 809.2 |
| 256 MiB | 2 | 1.037× | 0.995–1.077× | 8/10 | 336.2 → 568.7 | 1286.8 → 1814.6 |

The optimized 64 MiB sequential one-Rail path wins every paired block; none of the other three cells gives a clear two-Rail gain with this ten-block sample. The 256 MiB/concurrency-2 cell used unlimited `RLIMIT_MEMLOCK` for both arms because the default budget rejects two simultaneous 256 MiB registrations; other cells used the guest's default cap. This policy is identical within each comparison cell. All arms passed object-hash, byte-conservation and per-Rail accounting checks. CPU and peak RSS favor one Rail in every cell.

The earlier [unoptimized matrix](evaluation-results.md) remains a valid measurement of its own code revision: it reported modest apparent two-Rail gains of 1.038–1.101×. Its conclusion does **not** transfer to the faster baseline. The two matrices ran in separate VM sessions, so do not interpret their difference as a randomized cross-revision treatment effect; the physical AB/BA code-revision experiment above is the controlled evidence that the redundant copy mattered.

## Scientific interpretation and next gate

The result rules out a general “two Rails make one ContextStore read faster” claim for the available software-RoCE workloads. The original scheduling and safety prototype remains functionally valid, but its performance story depended on an avoidably expensive one-Rail baseline. A defensible systems paper could focus on the object-level failure/ownership contract and the **cost of preserving it**, then test a safety-preserving direct-to-ordered-staging multi-Rail design. That mechanism would need disjoint writable regions, a per-Rail dummy SGE target, provider-safe MR/QP retirement and the same failure injection suite. No such lower-copy multi-Rail mechanism has been implemented or measured here. Physical multi-HCA aggregation still requires two independent online HCA paths and a workload that actually saturates one path.

Reproduce the physical revision audit with `python3 kv-service/benchmarks/audit_revision_ab.py --results kv-service/benchmarks/results/paper-fastbaseline-hca-2026-10-03 --output kv-service/benchmarks/results/paper-fastbaseline-hca-2026-10-03/audit.json`. Reproduce the new RXE audit with `python3 kv-service/benchmarks/paper_pair_audit.py --results kv-service/benchmarks/results/paperfast-rxe-2026-10-03 --prefix paperfast-sequential --prefix paperfast-c4 --prefix paperfast-c2 --output kv-service/benchmarks/results/paperfast-rxe-2026-10-03/audit.json`. Each dataset includes `SHA256SUMS` and raw per-arm logs.
