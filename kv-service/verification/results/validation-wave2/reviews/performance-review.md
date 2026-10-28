# Expanded R matrix independent review

**Verdict: PASS_WITH_SCOPE_LIMITATIONS.** The six performance rows are reproducible from the preserved v2 raw stdout and process receipts. No blocking arithmetic, missing-slot, failed-process exclusion, identity, pool-accounting, or frozen-configuration discrepancy was found. The existing PPT table may be used with the scope and qualifications below. This verdict is about the measured R0/R1 experiment; it does not establish production readiness or inference performance.

The actual local audit was `python3 outputs/validation-wave2-20261029/reviews/replay_performance_review.py`, which exited 0. Its machine-readable output is [performance-review-math.json](performance-review-math.json); the exact script used is [replay_performance_review.py](replay_performance_review.py). The script reads original evidence without importing or running the producer analyzer. It writes only the independent review JSON. No Rust files, raw campaign files, analyzer, binaries, remote services, or benchmark runs were changed.

## Independently reproduced results

The latency columns are geometric means of the ten ordinary process medians, in milliseconds. Each process median uses the 30 steady batches after the five prespecified startup batches. Ratio means R0 latency divided by R1 latency. Intervals resample ten paired process blocks, not individual requests.

| Object | Concurrency | R0 median GM (ms) | R1 median GM (ms) | R0/R1 | Paired bootstrap 95% | Latency reduction | R1 slower blocks |
|---|---:|---:|---:|---:|---|---:|---:|
| 1 MiB | 1 | 28.311 | 15.209 | 1.86150 | [1.78838, 1.91674] | 46.28% | 0/10 |
| 1 MiB | 4 | 26.093 | 16.120 | 1.61868 | [1.59006, 1.64480] | 38.22% | 0/10 |
| 8 MiB | 1 | 99.300 | 85.992 | 1.15475 | [1.14920, 1.15926] | 13.40% | 0/10 |
| 8 MiB | 4 | 102.146 | 90.544 | 1.12814 | [1.12307, 1.13273] | 11.36% | 0/10 |
| 64 MiB | 1 | 681.884 | 674.983 | 1.01022 | [1.00690, 1.01405] | 1.01% | 0/10 |
| 64 MiB | 4 | 711.208 | 705.204 | 1.00851 | [1.00604, 1.01099] | 0.84% | 0/10 |

All per-process medians and nearest-rank p95 values, all six geometric-mean ratios, all paired bootstrap endpoints, all df=9 log-ratio t intervals, all descriptive p95 ratios, and all slower-block counts reproduce the submitted table. Bootstrap uses exactly 20,000 paired resamples, Python `random.Random(20261029 + size_mib * 10 + concurrency)`, and sorted zero-based indexes 499/19499. The p95 ratios are 1.78903, 1.54478, 1.13161, 1.09615, 1.01012, and 1.00649 in table order; these remain descriptive tail observations.

The 64 MiB result is a small, approximately 1% latency change in this campaign. The nominal confidence intervals do not make that change large or establish application-level value. No adoption threshold was imposed after seeing the data; both optimizations remain opt-in.

## Raw evidence and completeness

- Exactly 120 scheduled primary processes are present: 3 sizes × 2 concurrency settings × 10 paired blocks × 2 variants. Their start/exit records, replay commands, index entries, sample grids, object rotation, and alternating R0/R1 order agree exactly. Every v2 process has exit code 0 and finishes inside the frozen 240-second timeout. There are no unindexed exit records and no v2 failure artifact.
- Primary reads total 10,500: 9,000 steady and 1,500 startup. The startup exclusion is by batch number 1–5 from the frozen protocol, applied to both variants and every worker; no latency-based outlier removal is used. All raw startup samples remain available.
- Six additional diagnostic processes contribute 36 reads. The manifest has **135 records**, comprising nine inherited seed records, 120 new primary runs, and six new diagnostics. It does not mean 135 new v2 launches.
- Every primary and diagnostic sample has the correct byte count and the full known-pattern hash. All 126 read processes preserve initial/final identity. Each size has exactly three distinct object identities used consistently across variants and concurrency, with 512 KiB stripes and 2/16/128 stripes respectively. The frozen client source additionally compares the complete descriptor and placement structures, not just their printed fingerprints.
- R1 starts each process with an empty pool; final `created <= concurrency`, `created + reused == reads`, `idle == live == created`, and active/connecting/retiring/retired/rejected counts are zero. R0 reports pooling disabled. Single-request rail success, byte, and outstanding-resource counters also reconcile. The concurrent CLI does not print the extra serial `rail_stats` row; its per-request samples, effective-mode counts, pool counters, and rail-byte summary were checked instead.
- The server byte-window log contains exactly 10,536 `RDMA_SUBSET_DETAIL` records, matching every primary and diagnostic read. Its 5,421 connection records independently match R0 read counts plus R1 pool creations. No fallback/WARN/ERROR record appears in that captured log window. This is a log observation, not a claim that uninstrumented fallback is impossible.
- Every entry in the collected raw SHA-256 manifest matches its current file. The local replay does not rely on the producer's 17 acceptance booleans.

## Frozen executable, source, and environment

The control protocol precedes the v2 campaign start. Its R matrix exactly equals the parent freeze. Frozen runner, analyzer, and snapshot hashes match the collected controls. All seven recorded runtime-source hashes match both the after-collection receipt and the current local checkout, whose HEAD remains `1c3ee135d19fece67507b51acd1fc51ae9541208`.

R0 and R1 use the same benchmark binary, SHA-256 `6d90ed413283a513d32cb3b70428ba47cf3c7311ab47752bdc810db726bf3ca0`; before/after binary records match. The server binary remains `0e9c081f15f809839684bab64e24b75c547ec1ce4e0e1a4a2060f6146c76c87c`. Its PID 1882561 and start ticks 313266078 are unchanged. Server executable path, command, environment, config content/hash, and fabric records are unchanged. Client binaries, environment and fabric records also agree before/after. These checks use the retained contemporaneous receipts; they do not create a new live performance run.

This is SKV6 → SKV5, one `mlx5_1` physical HCA per host, port 1/GID 3, one route, recovery disabled, direct staging disabled, contiguous scheduling disabled. The server uses a 512 MiB RDMA slab, 512 KiB striping, and `CS_FORCE_DISK_READ=1`. Warm OS page cache was not purged; “forced disk read” must not be presented as cold-device latency. These are separate hosts from the soak campaign, but the system snapshots contain unrelated background workloads and do not establish otherwise exclusive machines.

## Hash-version failure retained and resolved

The original v1 campaign, its one measured slot, and its `failure.json` remain intact. The benchmark itself exited 0; the external validator used an incompatible XXH3 version. The preserved adjudication is `VALIDATOR_HASH_VERSION_MISMATCH`. The corrected v2 campaign reran all primary slots; no v1 measurement was mixed into the result. Only the nine completed seed records and unchanged objects were inherited.

This audit reran the separate Python integer transcription of historical **twox-hash 1.6.3**, first checking its five pinned-source known-answer vectors, then reproducing the deterministic full-buffer hashes. Values match: 1 MiB `c061e1dcd62f6c77`, 8 MiB `dc2ba4087a0c5c99`, 64 MiB `a0a4cbfa5cad46af`. The replay also records the source and oracle-script digests. The reused oracle and its MIT license remain under `outputs/implementation-wave1-20261029/reviews/`; modern xxhash is not substituted as an oracle.

## Timing and mechanism interpretation

Source inspection confirms that request latency wraps `read_multi_rail_into`. It includes that API's internal transfer/checksum/metadata work and excludes the benchmark's subsequent full-pattern verification. Serial CPU samples use the narrower timed-read boundary. Concurrent batch wall time and CPU include task coordination, result collection, and subsequent verification; these must not be treated as directly comparable CPU or throughput boundaries across C=1 and C=4.

The six tracing-enabled diagnostic processes are separate from the untraced primary matrix. Every reported phase value was recomputed from the raw stderr. For R1, first-read connection acquisition is 9,591 / 11,786 / 13,450 µs for 1 / 8 / 64 MiB; the median of the next five acquisitions is 10 / 10 / 11 µs. `connect_us` measures the acquisition path, including pool lookup and timeout refresh, or initial connection establishment. It is not a pure hardware connection timer.

The source still allocates the transfer buffer and invokes `register_buffer` on every read. In those same R1 diagnostics, later registration medians are 212 / 889 / 2,886 µs. The mechanism supported here is connection reuse; no MR reuse result follows from it. First-read and later acquisition observations should be shown as a diagnostic explanation, not mixed into the primary confidence intervals.

Relevant source locations: `kv-service/client-rs/src/bin/rail_read_bench.rs:160` (identity), `:198` (concurrent timing/verification), `:400` (full known pattern), `:456` (serial timing); `kv-service/client-rs/src/rail_read.rs:2067` (acquisition), `:2121` (allocation/registration/transfer timing).

## Reporting boundaries

1. Report ten paired process blocks per cell. The 30 or 120 steady requests inside a process are subsamples. The repeated server, cache state, time sequence, and only three fixed identities limit generalization; the confidence intervals quantify this campaign's paired-block variation.
2. All three keys of a given size contain the same deterministic `index % 251` distribution. This is identity repetition, not a diverse content/workload study.
3. Use ordinary medians for this expanded matrix. The first-wave pilot's upper-median convention is a different statistic; do not combine or relabel its values as this matrix.
4. Keep p95 descriptive and make no p99, cold-storage, dual-HCA scaling, broad throughput-scaling, end-to-end Agent, inference, or production claim from this data.
5. The log format is custom comma-separated `key=value`, including Rust Debug bracket arrays and quoted fields, rather than general CSV. The producer's simple split preserves the numeric fields used in this specific primary calculation. The independent parser additionally preserves bracket-comma arrays and verifies every full stripe assignment; no current numeric-field ambiguity or mismatch was found.

No additional experiment is required to accept these six bounded measurements. Broader claims require a separately frozen workload and evaluation; they are outside this review.
