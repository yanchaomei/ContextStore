# Bounded state exploration of multi-Rail read ownership

**Status (2026-10-09):** the revised [explicit-state explorer](../../kv-service/verification/multirail_state_explorer.py) ran on SKV_2 (Python 3.8.10). Its [raw JSON](../../kv-service/verification/results/state-explorer-revised-20261009.json) records source SHA-256, every explored-state count and the shortest counterexample for each negative control. The safe abstraction exhausted **19,657 states** without violating its stated invariants. Six deliberately weakened guards produced counterexamples. The earlier 6,045-state [result](../../kv-service/verification/results/state-explorer-20261009.json) remains available as a superseded, weaker model; independent review found that it allowed a direct-staging slice to be reused between verification and publication.

This is an **abstract contract check**, not a proof of the Rust code, libibverbs, NIC behavior, operating-system crash behavior or implementation-level linearizability. The model has two Rails and one required stripe per Rail. It does not explore all stripe counts, all provider outcomes, resource limits or every interleaving in the real program.

## Contract encoded

The state records request phase; each Rail's QP, MR, WR, stripe coverage, source ownership and target ownership; the first lookup identity, current version and second checked identity; whether a checksum was supplied; corruption; whether the private staging bytes remain valid; cancellation before the publish gate; caller publication; and a late WRITE into a reused target. `Complete short WRITE` marks a WR complete without marking its stripe covered. `Corrupt one stripe` can occur with or without a checksum. Without an advertised checksum, the checker allows undetected byte corruption: the implementation cannot claim to detect it either.

The direct-staging target has **two independent lifetime obligations**. A QP must be retired before its MR or target allocation can be recycled, and the private staging bytes must remain owned by this request through checksum/identity checks and final caller publication (or request failure). Since both Rail slices occupy one vector, even failed direct requests must retire **both** QPs and join all workers before any target slice can be reused. In packed mode, per-Rail receive buffers can instead be released after their bytes have been assembled into a separate request-private staging vector. The current model conservatively uses the *direct* shared-vector rule for both modes; it does not model packed assembly.

`Cancel` and `Publish` are mutually exclusive transitions at the final gate. A cancellation before publication moves the request to a failed terminal state; a cancellation after publication cannot undo the completed read. A read publishes only after both WRs completed, both stripes are covered, both QPs retired, any supplied checksum passed, and the second lookup matched the first. The caller's allocation changes only at this publication event. The model does not encode the exact instruction-level lock implementation or a caller racing with cancellation after return.

## Assumptions and limits

1. **Provider retirement:** a *successful* `ibv_destroy_qp` prevents future WRITEs through that QP. This assumption must be validated against provider behavior. The client and server now fail-stop if QP destruction returns an error, before their retained memory can be dropped. A test-only interposer has exercised **both client and server** QP/MR error-return branches; it has not reproduced a real provider or device fault.
2. **Accounting:** WR completion, short-transfer detection and per-stripe coverage are represented as truthful events. The model is not wired to actual CQE and response parsing. Implementation tests must check wrong byte counts, duplicate/missing stripes and partial responses.
3. **Identity:** a relevant object rewrite changes the checked descriptor/placement identity or an available checksum. The model has only versions A and B and one rewrite; ETag collision, ABA and concurrent layout mutation outside that assumption are not proven away.
4. **Checksum availability:** corruption is rejected only when a checksum is supplied. A stripe with no checksum can be silently damaged; neither the model nor current code can guarantee detection in that case.
5. **No resource model:** queue depth, registered bytes, admission refusal and budget release are tested in code but are **not** state variables here. The result provides no formal budget-closure evidence.

## Negative-control receipts

| Removed guard | Shortest observed counterexample | Scope of evidence |
| --- | --- | --- |
| QP retirement before target reuse | `Timeout r0 → Deregister MR r0 → Reuse target r0 → Late WRITE r0` | A late WRITE can corrupt a recycled allocation. |
| Private staging ownership until publication | both Rails complete and retire → Check → Reuse target r0 → Publish | DMA quiescence alone does not preserve checked staged bytes. |
| QP retirement before source release | `Post r0 → Timeout r0 → Release source r0` | A posted/uncertain WRITE can still read released source memory. |
| Full coverage before publication | both WRs and QPs finish, but Rail 1 reports a short stripe → Join → accept incomplete coverage → Publish | Completion alone does not establish every expected stripe byte. |
| Final cancel/publish gate | both Rails complete → Check → Cancel returns → Publish | A fully verified read can still publish after cancellation without the gate. |
| Matching second identity | object A→B → both Rails complete → accept mismatched Lookup1 → Publish | Coverage and checksum alone do not establish the chosen object version. |

The shortest traces are for this finite abstraction; they do not reproduce a real provider's exact sequence. The first negative trace *does* include a `Late WRITE`; the previous explorer stopped at target reuse and supported only a risk statement. The cancellation trace reaches the final gate after both Rails complete and validate; the previous trace cancelled before any WR. The version trace contains a mismatched second lookup; the previous trace had no second checked identity. These corrections came from independent review.

## Connection to code and remaining checks

The [safety contract](safety-contract.md) and [code-to-model map](code-to-model-refinement-map-2026-10-09.md) connect these events to Rust ownership, QP/MR drop order and end-to-end tests. The revised model checks the event-level argument only. Exact-binary two-RXE cancellation, delayed-WRITE and dead-listener receipts are retained; the stronger [unique-Generation concurrent overwrite campaign](rewrite-race-and-server-failstop-2026-10-09.md) compares accepted bytes with the SDK's accepted identity across **31 timestamp-verified SDK PUT-call/read intersections after payload preparation (server commit overlap unproven)**, while test-only server cleanup-return injections cover two exceptional branches with launch-time binary manifests. A stronger model should represent admission and release counters, multi-stripe descriptors, provider destruction failure and the atomic publication lock more directly. Stronger claims require an actual refinement proof and hardware-path evidence.
