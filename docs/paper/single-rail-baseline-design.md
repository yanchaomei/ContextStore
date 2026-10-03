# Design: remove redundant assembly from the one-Rail baseline

**Date:** 2026-10-03. **Status:** implemented and evaluated on the research branch after evidence commit `b443a92`; [results](baseline-ablation-results.md). This design changes the one-Rail `RailReader` staged-read implementation without changing the stored stripe layout, public API, wire protocol, QP/MR lifetime or final caller-buffer publication gate.

## Problem

The physical single-HCA pilot measured a 53.2 ms median assembly stage for a 64 MiB object. With exactly one Rail, the existing planner visits stripes in object order and the transport's `packed` buffer already contains the complete object in that order. The reader nevertheless allocates a second object-sized vector and copies every stripe into it. This weakens the one-Rail baseline in the paper's one/two-Rail comparison.

## Considered choices

1. **Keep the uniform assembly path.** It is simple and already tested, but retains one full allocation/copy when it is unnecessary and risks overstating a multi-Rail advantage.
2. **Return the sole completed Rail buffer as `StagedRailRead` (chosen).** Require exactly one task, packed length equal to object size, and each stripe's packed offset equal to its object offset. Check the returned byte length before reusing it. Keep checksum verification and the later version check and publication unchanged. No new `unsafe`, MR reuse, or buffer pool is needed.
3. **Receive all Rails directly into a shared ordered staging region.** This could also remove multi-Rail assembly, but needs disjoint writable slice ownership, per-device registration and dummy SGE memory. It changes the transport interface and safety argument substantially; defer until option 2's baseline is measured.

## Ownership and failure semantics

The Verbs fetch already destroys its QP and drops its MR before returning `packed`. A sole successful result can therefore become the request-owned staged object by moving the vector. A failed fetch returns no vector, so publication remains unreachable. An incorrect length still produces `Incomplete`. A checksum mismatch still drops the private vector and leaves the caller untouched. The object remains private until `KvClient::read_multi_rail_into` rechecks descriptor/placement identity and performs the final copy under the cancellation gate. The existing `BudgetGuard` remains attached to `StagedRailRead` until publication or drop.

The fast path is guarded by descriptor-derived layout conditions, not just `tasks.len()==1`: this matters if a future planner can hand one Rail a noncontiguous partial assignment. Multi-Rail or noncontiguous cases continue through the existing assembly loop.

## Verification and paper decision

- First write a Mock transport test that records the returned receive-vector pointer and asserts the staged object's data pointer is identical for a complete one-Rail read. It must fail on `b443a92` for the expected reason.
- Add a single-Rail checksum-failure/sentinel test and rerun the client unit suite plus real one-Rail Verbs object reconstruction.
- Repeat physical single-HCA stage tracing on the same object and binary revision, and rerun paired software-RXE one/two-Rail blocks. Do not compare old and new requests as if they were randomized peers; label them as separate revisions and retain the prior negative results.
- A faster one-Rail baseline can reduce or reverse observed multi-Rail ratios. If so, update the claim ledger and paper abstract rather than defending the old result.

This is baseline fairness and safety-preserving copy elimination, not claimed as a novel paper mechanism by itself.
