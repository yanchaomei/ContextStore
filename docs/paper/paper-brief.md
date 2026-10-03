# Paper brief: failure-atomic multi-rail reads for versioned KV-cache objects

**Status:** research framing, not a submission-ready paper. **Implementation:** ContextStore PR #32 at `c1c1de1`. **Evaluation:** physical single HCA and two-rail Soft-RoCE have distinct evidence boundaries.

## One-sentence thesis

An object store can exploit independent RDMA paths for one striped KV-cache object while preserving an all-or-nothing, single-version read, if each Rail writes only into request-owned registered staging memory and a request cannot publish or reclaim that memory until the transport and version checks settle.

## Why this is a systems problem

The stored object already has a stripe layout, owner placement, Generation, ETag and Layout Version. A network scheduler can assign those stripes to different Rails without moving them. The hard part is the boundary between partial asynchronous WRITEs and caller-visible object state: a failed or cancelled Rail may still have a posted WRITE, a CQE may arrive after timeout, and another request may reuse the destination or a server-side source. A correct read must treat path selection, completion, object identity and memory reclamation as one state machine.

## Mechanism chain

1. **Owner-bound discovery and routing:** optional Placement capabilities bind an actual stripe owner and primary endpoint to reachable fabric/listener pairs. Local device/port/GID configuration supplies the other half. Owner, listener and route-count checks reject ambiguous paths before data transfer. This is integration and safety context, not claimed as new topology scheduling.
2. **Private multi-Rail transfer:** weighted whole-stripe planning reuses the existing disk layout and tag-15 SGE path. Each Rail has its QP/CQ/MR and compact target buffer; a shared budget caps staging, registration and in-flight bytes. Each server path validates descriptor identity and requested stripe ranges.
3. **Failure-atomic completion:** the reader joins all started tasks, verifies exact byte/stripe coverage and optional checksums, performs a second `LookupObject`, compares descriptor plus Placement, and copies the complete object under the cancellation gate. Transport errors, stale versions and incomplete data leave the caller buffer untouched.
4. **Safe retirement:** client control errors destroy the QP before releasing its target MR. Server-side uncertain WRITEs retain slab extents, cache pins or `(MR, Bytes)` sources until QP destruction; a connection with uncertain CQ state is not reused. This applies to both new stripe-subset and older complete-object GET paths.

## Evidence already measured

- Two isolated RXE Rails restored a 64 MiB / 16-stripe object, 32 MiB per Rail; link shutdown, timeout, checksum/version failure, cancellation and late-WRITE controls returned whole-object failure and preserved a reused buffer in the injected traces. Server/client release suites and isolated two-node E2E pass. These are **correctness checks**, not a formal proof.
- An expanded [ten-block Soft-RoCE evaluation](evaluation-results.md) on the research binary reports paired geometric-mean two/one-Rail throughput ratios of 1.056× (64 MiB/c1), 1.101× (256 MiB/c1), 1.038× (64 MiB/c4) and 1.067× (256 MiB/c2). The c4 95% block-bootstrap interval, 0.983–1.088×, crosses parity and three blocks lose, worst 0.891×. [Every block](figures/rxe-ten-blocks.svg), raw arm stdout, CPU/RSS and a memory-lock admission failure are retained. These are software RXE results on two guests sharing one host and one virtual disk, not physical multi-HCA evidence. Earlier three-/two-block results remain separate and [audited](../../kv-service/benchmarks/results/paper-paired-audit-2026-10-03.json).
- Three physical single-HCA `ib_write_bw` calibrations achieved 97.96/97.85/97.94 Gb/s. The older full ContextStore 64 MiB single-Rail read had median 163,271 us (0.383 GiB/s payload); the NIC is not shown to be the limiting resource in that setup. A new [same-object stage pilot](stage-trace-pilot.md) used two traced and two untraced independent processes. Its ten traced reads have a 153.061 ms median and close the stage-to-wall accounting within 1.66%. Marginal medians for buffer initialization, assembly and publication were 32.220, 53.182 and 19.656 ms, versus 19.165 ms for the composite GET/CQ interval. The [figure](figures/physical-single-hca-stage.svg) shows every read. This is descriptive; it does not isolate wire time or demonstrate a causal optimization.

## Novelty and claim boundary

[UCX](https://github.com/openucx/ucx/blob/master/docs/source/faq.md) already supports multi-rail transfer. [TENT](https://arxiv.org/abs/2604.00368) applies dynamic multi-rail slice scheduling to disaggregated LLM serving and is stronger than this static scheduler on that axis. [Mooncake](https://arxiv.org/abs/2407.00079) and [NIXL](https://docs.nvidia.com/nixl/getting-started/architecture/) cover RDMA-based KV transfer and capability metadata; RDMA memory-key/buffer lifetime hazards are established in [RFC 5042](https://datatracker.ietf.org/doc/rfc5042/) and later design-guideline work. This project should **not** claim “first multi-rail KV-cache transfer,” “novel static striping,” “automatic topology optimization,” or physical bandwidth aggregation.

The plausible research contribution is an **object-level failure and version contract** composed with multi-Rail RDMA and a way to reduce its measured staging cost safely. This would require a formal invariant or systematic state exploration, a decisive simpler baseline, a causal staging-cost ablation and an actual network-limited physical workload before making a strong conference performance claim. Until then the implementation is best described as a rigorous open-source artifact with a promising safety question.

## Provisional abstract (evidence-bounded)

> Large KV-cache objects may be striped across storage devices, while loading one object through several RDMA paths creates a correctness boundary: partial completions, object-version changes and late WRITEs can outlive a request's target buffer. We implement an opt-in multi-rail read in ContextStore that preserves the stored stripe layout and existing read API. The design advertises owner-bound Rail capabilities, transfers stripes into private registered buffers, verifies a single object version and publishes the complete result only after all paths settle. It retires uncertain QPs before their source or destination memory can be reused. Tests on two independent Soft-RoCE paths exercise complete reads and injected failure cases; physical single-Rail tests cover the Verbs path. In ten paired process blocks per workload, software-RoCE two/one-Rail geometric-mean throughput ratios span 1.038–1.101×; at 64 MiB and concurrency 4 the interval includes no gain. Physical single-HCA stage accounting shows substantial buffer initialization and copy costs, while a link-capacity calibration shows the available application workload is not demonstrated to be NIC-limited. Physical multi-rail aggregation and a staging-cost optimization remain open evaluation questions.

## Suggested paper organization

1. Workload and problem: object stripes, version identity, asynchronous WRITE lifetimes; demonstrate the actual bottleneck rather than assuming it.
2. Contract and state machine: atomic visibility, version identity and QP/MR lifetime invariants.
3. Design and implementation: owner-bound discovery, weighted assignment, private targets, checks and retirement.
4. Cost model and stage accounting: lookup, QP/MR, server I/O, wire, assembly, checksum and publish.
5. Evaluation: controlled 1/2-Rail hardware and RXE settings, fault matrix, overhead ablations and failure cases.
6. Related work and limits: TENT/UCX/Mooncake/NIXL, existing RDMA safety principles, no transparent same-request retry.

The [claim ledger](claim-evidence.md), [safety contract and proof obligations](safety-contract.md), [experiment plan](experiment-plan.md) and [primary-source map](related-work-map.md) are the review checkpoints before drafting a full paper.
