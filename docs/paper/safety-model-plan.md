# Bounded state exploration plan for the multi-Rail read contract

**Status:** specification and acceptance criteria, 2026-10-09. No model checker has run on this document; [safety-contract.md](safety-contract.md) remains an implementation-linked argument rather than a formal proof. The goal is to make a future TLA+/explicit-state run traceable to actual fault-injection points.

## Model boundary and assumptions

Model one request, two Rails, one stripe assigned to each Rail, one caller destination, two candidate object versions, and one possible delayed RDMA WRITE per Rail. This is enough to explore the ordering classes that threaten publication and reuse; larger stripe counts should not change the ownership rules but need separate coverage tests. The model assumes (A1) a successful provider QP-destruction operation prevents any subsequent WRITE through that QP, (A2) completion accounting correctly identifies the WR set for that QP, (A3) each object rewrite changes the descriptor/placement identity or fails an enabled checksum, and (A4) the caller only accesses its destination after the read returns or after cancellation returns. A model cannot establish these provider and application assumptions by itself; real Verbs tests must accompany it.

## State variables and events

| State | Domain |
| --- | --- |
| `phase` | `new`, `transferring`, `quiesced`, `verified`, `published`, `failed` |
| `qp[r]` | `absent`, `open`, `retired` for each Rail `r` |
| `wr[r]` | `none`, `posted`, `completed`, `uncertain` |
| `mr[r]` | `unregistered`, `registered`, `deregistered` |
| `target[r]` | `private`, `reusable`; includes the private ordered staging slice in the proposed direct mode |
| `source[r]` | `pinned`, `released` |
| `v0`, `v1`, `storage_version` | first lookup, second lookup, current storage identity; each version is `A` or `B` |
| `covered`, `checksums_ok`, `cancelled`, `caller_changed` | booleans |

Allow transitions for `Lookup0`, `Post(r)`, `Complete(r)`, `Timeout(r)`, `RetireQP(r)`, `Deregister(r)`, `ReleaseSource(r)`, `ReuseTarget(r)`, `Join`, `CheckCoverage`, `CheckChecksums`, `Rewrite`, `Lookup1`, `Publish`, `Cancel` and `LateWrite(r)`. A timeout marks `wr[r]` uncertain but does **not** itself free the MR, source or target. `RetireQP(r)` is the required quiescence step before those resources can be released. `Publish` and `Cancel` are serialized as one atomic choice at the publication gate; a cancellation that begins after publication may return afterward and cannot undo the completed read.

## Safety properties to check

1. **Atomic caller visibility:** `caller_changed` implies `phase = published`; every failure state retains the caller's entry sentinel. No Rail WRITE targets the caller allocation.
2. **One version and complete coverage:** `phase = published` implies `covered`, `checksums_ok` when checksums are supplied, `v0 = v1`, and each required stripe came from the descriptor/placement identity accepted at both lookups. A rewrite after `Lookup1` is allowed: the published bytes remain a valid snapshot observed at the second lookup, but the API does not promise that they are current at return.
3. **Target lifetime:** `target[r] = reusable` implies either `qp[r] = retired` or every WR that can address `target[r]` is known complete and its CQ accounting is finished. Under the implementation's stricter policy, require `qp[r] = retired` before reuse on every error path.
4. **Source lifetime:** `source[r] = released` cannot coexist with an open QP and a posted/uncertain WRITE that reads it. The server's retained extent/`(MR, Bytes)` lists discharge this obligation only after QP destruction.
5. **Budget closure:** after quiescence and result drop, active requests, per-Rail tasks, registered bytes and in-flight bytes return to their pre-request values. The model treats admission as an atomic reservation; a refused request has no posted WR.

## Negative controls and expected traces

- **Unsafe target reuse:** change `Timeout(r) → Deregister(r) → ReuseTarget(r)` to omit `RetireQP(r)`. The checker should find `LateWrite(r)` into a reusable allocation. If it does not, the model is too weak to evaluate the intended property.
- **Partial publication:** permit `Publish` after only Rail 0 completes. The checker should find `caller_changed` with incomplete coverage.
- **Version race:** permit publication without the second identity comparison, then interleave `Rewrite`; the checker should find mixed or stale publication against the stated contract.
- **Cancel race:** split the final copy and cancellation into two unordered actions. The checker should find publication after cancellation returns; reintroducing the shared gate should remove this trace.

## Connection to executable tests

| Model event/class | ContextStore test or required injection |
| --- | --- |
| `Timeout → RetireQP → LateWrite` | existing two-RXE late-second-Rail and CQ-retirement receipts; repeat on the direct-staging path with target-address reuse sentinel |
| `Cancel` before/during publish | existing `RailCancel` unit and one/two-Rail cancellation tests; add a direct-mode completion race |
| `Rewrite → Lookup1` | stale Generation/placement tests plus sustained concurrent writer/readers run that records accepted version IDs |
| `Deregister/ReleaseSource` | client `RdmaClient::drop`, server retained-source/QP drop trace; instrument release times and MR/QP identities |
| admission refusal | `RLIMIT_MEMLOCK`, total/per-Rail registered/in-flight byte and queue-depth tests |

The first executable model should cover both the original packed-buffer path and the proposed direct ordered staging path by changing only their private-target ownership map. A model check is accepted only with the exact source revision, state bounds, checker version, full command, explored-state count, invariant results and raw counterexample traces saved alongside the real-Verbs receipts. If the checker cannot run on the current remote environment, keep this file labeled a plan and make no formal-verification claim.
