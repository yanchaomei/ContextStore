# Release deployment matrix: independent review

**Verdict: PASS_WITH_SCOPE_LIMITATIONS.** No blocking source, configuration, raw-data, slot-completeness, or statistical discrepancy was found. The six release rows can be used as the main deployment-build measurements, following the selection rule frozen before this campaign. Debug results remain separately preserved and must not be pooled with release results or used for a single-cause cross-profile claim.

The actual command `python3 outputs/validation-wave2-20261029/reviews/replay_release_performance_review.py` exited 0. [The exact replay script](replay_release_performance_review.py) reads raw stdout and independent process/configuration receipts without importing the producer analyzer. [Its mathematical audit](release-performance-review-math.json) contains all process medians/p95 values, paired ratios, bootstrap/t intervals, diagnostic phase values, evidence hashes and full-pattern hash replay. This review created only new review files; the prior debug review, raw evidence, source, binaries and analyzers were not changed. No new build or measurement was run.

## Verified measurements

Latency columns are geometric means of ten process-level ordinary medians, in milliseconds. Each process median uses its last 30 batches: 30 request subsamples at C=1 or 120 at C=4. R0/R1 is a latency ratio, not independently measured application throughput.

| Object | C | R0 median GM (ms) | R1 median GM (ms) | R0/R1 | Paired bootstrap 95% | Latency reduction | R1 slower blocks |
|---|---:|---:|---:|---:|---|---:|---:|
| 1 MiB | 1 | 15.289 | 2.823 | 5.41511 | [5.09810, 5.77085] | 81.53% | 0/10 |
| 1 MiB | 4 | 13.198 | 3.054 | 4.32094 | [4.18341, 4.47156] | 76.86% | 0/10 |
| 8 MiB | 1 | 21.706 | 9.710 | 2.23554 | [2.17909, 2.29735] | 55.27% | 0/10 |
| 8 MiB | 4 | 23.803 | 13.865 | 1.71674 | [1.67573, 1.75944] | 41.75% | 0/10 |
| 64 MiB | 1 | 91.313 | 84.934 | 1.07510 | [1.06528, 1.08540] | 6.99% | 0/10 |
| 64 MiB | 4 | 118.830 | 109.728 | 1.08295 | [1.06298, 1.10583] | 7.66% | 0/10 |

Every table field independently reproduces the submitted values. Bootstrap uses the frozen 20,000 paired-process resamples, Python `random.Random(20261029 + size_mib * 10 + concurrency)`, and zero-based sorted indexes 499/19499. All df=9 paired log-ratio t intervals also reproduce. Nearest-rank process p95 values and their descriptive ratios reproduce exactly: 5.55546, 4.53021, 2.25877, 1.64685, 1.13530, and 1.08220 in table order. No choice of metric, exclusion rule, or interval method was changed for the stronger release result.

## Profile amendment and source/build provenance

The captured protocol embeds the exact parent `release-profile-amendment.json`. The amendment and protocol digests are present in the launch manifest's control hashes. The source/build freeze is dated 20:45:02 UTC, the protocol 20:48:02 UTC, and the campaign begins 20:48:18 UTC on 2026-10-28. Fresh seed processes begin after the campaign start. Thus the recorded choice to use release as the main deployment result regardless of outcome precedes the new measurements.

The original R matrix is preserved verbatim in `protocol.json` as `R_matrix_original`; its old debug-server hash is not asserted to describe the release executable. The separately bound effective configuration replaces the server hash and storage wording, adds release features/profile, and preserves all sampling/statistical/adoption terms. The actual release server hash is:

`f6607705f29e30bc293ada3f9e64422cda8e0dadd8dc2996d02cec8ce8d3f0d8`.

The build log records default `make build`, `cargo build --release --features io-uring,rdma,metrics`, and an optimized release completion. The 65-file source manifest and source archive match their frozen digests; **all 65 archive members independently match both the manifest and the current local files**. Base HEAD is `1c3ee135d19fece67507b51acd1fc51ae9541208`; the pinned candidate source is additionally identified by the 65-file manifest rather than HEAD alone. The reviewed descriptor-guard file has SHA-256 `d7df834df7d4ffb86a04725a5055069cfadca2b253fc37ea092028fa50bd5b71`; the tier_a source has `ee0a6c5395135b05f73d0d59cc8b0a887a156d0be44b69701ecf1edfbcda3633`.

The server's PID 1863559, start ticks 313697929, executable hash/path, command, configuration and environment agree before/after. The configuration records four tier_a workers, 512 KiB stripes/threshold, 512 MiB RDMA slab, `CS_FORCE_DISK_READ=1`, and isolated release data directories/Redis prefix. Client source, binary, environment and fabric evidence also agree before/after. Both R0 and R1 use unchanged client binary `6d90ed413283a513d32cb3b70428ba47cf3c7311ab47752bdc810db726bf3ca0` and the same release server. The within-release intervention is pool disabled versus pool size equal to concurrency.

The descriptor-guard change is not a measured optimization. The benchmark issues nonempty stripe-subset requests; the server serves and returns from that branch before the changed legacy full-descriptor retirement handling. A debug-versus-release difference also changes server source/build features and campaign time, so compiler optimization or the guard cannot receive exclusive causal attribution.

## Completeness and independent cross-checks

- **135 fresh launches** are present: nine newly executed seeds, 120 primary processes, and six diagnostics. Unlike the earlier debug v2 campaign, no seed record is inherited. The nine object keys and release Redis namespace are disjoint from debug. Seed slot coverage and every exact seed/read command were checked.
- The 120 primary slots exactly match the frozen 3 × 2 × 10 × 2 schedule, alternating pair order and rotating `pair % 3`. Start/exit records, replay commands, index, timestamps and raw sample grids agree. Every process exited 0 inside the 240-second bound. No unindexed exit or campaign-failure artifact was found.
- Primary request count is exactly 10,500: 9,000 steady and 1,500 prespecified startup subsamples. Six independent tracing processes add 36 reads. The first five batches are retained and excluded uniformly; no observed-failure or latency-based outlier deletion occurs.
- Every read matches the expected byte count and full deterministic pattern hash. The historical twox-hash 1.6.3 independent Python oracle was rerun, including five pinned-source known-answer vectors. It reproduces 1 MiB `c061e1dcd62f6c77`, 8 MiB `dc2ba4087a0c5c99`, and 64 MiB `a0a4cbfa5cad46af`.
- Initial/final object fingerprints agree within all 126 read processes and across matching object identities. There are three distinct identities per size, with correct 512 KiB stripe layouts and all server chunk files accounted for. The unchanged client source additionally compares complete descriptors and placements on exit.
- Every R1 pool starts empty, creates at most C connections, has `created + reused == reads`, and ends with `idle == live == created`; active, connecting, retiring, retired and rejected counters are zero. R0 pooling is disabled. All effective-mode and rail-byte counters reconcile. The extra serial rail counters also reconcile; the concurrent CLI does not print that serial-only row.
- The captured server window contains 10,536 read-detail lines and 5,421 connections, exactly matching all reads and the independent R0-read-plus-R1-creation accounting. No fallback/WARN/ERROR record is observed in that window.
- All raw SHA-256 entries, the collected archive hash and campaign file manifest entries were independently verified. The prior debug raw manifest still matches the digest recorded in its completed audit, all its raw files still match, and its audit script is unchanged. Debug samples are not included in release statistics.

The producer's server-log scope string retains the old word “v2”; the reviewed paths, timestamps, process records and exact counts above establish that this report concerns `performance-release-20261029-v1`. This label does not change the measured dataset.

## Storage and timing boundaries

The measured path is SKV6 → SKV5, one physical `mlx5_1` HCA per host, port 1/GID 3, one route, recovery disabled, direct staging disabled, contiguous scheduling disabled. Soak uses other hosts. Machine snapshots do not establish exclusive occupancy, and no OS page-cache flushing was performed.

Source inspection confirms the tier_a pointer stream queues `Job::ReadIntoPtr` and invokes `read_into_ptr_impl`, which uses `std::fs::File::open`, seek and `read_exact` into the provided buffer. This is a **buffered storage path**. Bypassing the application cache does not prove each request reaches physical media; compiled `io-uring` support also does not imply that this configured tier_a pointer path used io_uring. No physical-disk/NVMe bandwidth, cold-storage, or io_uring speedup claim follows.

Request latency surrounds `read_multi_rail_into` and excludes the benchmark's later full-pattern hash validation. Internal API transfer/checksum/metadata work remains included. Concurrent batch wall time and CPU include coordination and post-read validation; serial CPU uses the narrower timed-read scope. Do not compare these CPU/throughput boundaries across C=1 and C=4 as if identical.

Diagnostic values were recalculated from every raw stage line. R1 first connection acquisitions are 11,176 / 13,232 / 12,541 µs for 1 / 8 / 64 MiB; next-five medians are 7 / 10 / 9 µs. This measures connection acquisition, including pool work and timeout refresh, not a pure hardware connection timer. Buffer registration still happens on every read; corresponding next-five registration medians are 191 / 806 / 2,949 µs. Diagnostics support connection reuse and do not establish MR reuse. They are separate from the untraced primary confidence intervals.

Source locations: `kv-service/server/src/io_executor/tier_a.rs:139`, `:207`, `:911`; `kv-service/server/src/rdma/server.rs:1646`; `kv-service/Makefile:42`, `:70`; client timing and registration remain as documented in the earlier performance review.

## Permitted use

Use the release table as the preselected deployment-build result with ten paired process blocks per cell. The three identities per size share one deterministic content distribution. Confidence intervals describe paired-block variation in this campaign; requests are subsamples rather than independent repetitions. Keep p95 descriptive, and preserve the ordinary-median convention separately from the first-wave upper-median pilot. The result supports this API-read microbenchmark, not p99, dual-HCA scaling, production reliability, end-to-end inference/Agent gains, or a general content-distribution claim. Defaults remain off.

No additional experiment is needed to accept these bounded release measurements. The prior debug result remains valid within its own frozen scope, including its approximately 1% 64 MiB gain; it is neither replaced as evidence nor combined with the larger release effect.
