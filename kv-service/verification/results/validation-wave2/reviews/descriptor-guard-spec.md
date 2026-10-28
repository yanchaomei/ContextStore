# Descriptor fallback guard — independent SPEC review

**Verdict: PASS_scope — static review and observed unit-test receipts only.** No actionable spec findings. Native/RXE validation remains unproven.

- Scope: only the uncommitted `kv-service/server/src/rdma/server.rs` diff against `1c3ee135d19fece67507b51acd1fc51ae9541208`; unrelated recovery/soak changes excluded.
- Reviewed SHA-256: `d7df834df7d4ffb86a04725a5055069cfadca2b253fc37ea092028fa50bd5b71`, independently matched to the working file and `descriptor-guard/green-server.rs` snapshot.
- Spec source: parent's delegated requirement for the full-object/non-striped descriptor GET cold-slab error branch; no client pool-policy enablement.

## Requirement readback

1. **Typed retirement marker, including anyhow context, propagates before warning/fallback.** `server.rs:572–584` checks `downcast_ref::<RetireLegacyGet>()` before either side effect and returns the owned original error. The regression test (`2163–2184`) wraps the marker in context, observes whether fallback executes, checks marker pointer identity, and preserves outer context text.
2. **No reuse of uncertain QP/CQ.** The cold branch calls the guarded closure at `1737–1758`; its `?` exits `handle_descriptor_get` before response. The caller's `?` at `314–324` exits `handle_client` before its next request.
3. **Keep the source extent until connection teardown.** Existing cold-path uncertainty detection moves the extent into `RcQp` before returning the marker (`924–930`). Unchanged `qp.rs:373–374,398–407` retains it through successful QP destruction and aborts on destruction failure. The CQ guard is declared before QP (`server.rs:257–260`), so QP teardown precedes CQ teardown.
4. **Safe errors retain fallback.** Non-marker errors execute the same storage/read/WRITE fallback. Pre-submit failures remain ordinary errors. A storage error after prior WRITEs reaches ordinary error return only after successful drain (`907–934`). The guard does not alter that classification.
5. **Minimal scope.** One local helper, one call-site wrapper, documentation, and two tests; no client code/pool-policy change within this diff.

## Evidence and limits

Read local `kv-service/verification/results/validation-wave2/descriptor-guard/logs/`: RED exit `101` fails on actual fallback invocation; focused GREEN exit `0` has four tests (two guard tests plus two wire tests). Focused GREEN precedes the final formatting adjustment; final-source `server-rdma` exit `0` records `109 + 3 + 2` passing tests. `git diff --check` passed. No local Rust tests or native jobs were run by this reviewer.

The copied `clippy-default.exit` is `101`; this report does not approve overall quality/build gates or establish the cause of that failure. Full handler teardown and late-DMA behavior still need the separately scheduled native evidence.
