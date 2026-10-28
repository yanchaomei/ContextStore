# Recovery SPEC review — development-v1

Status: **PASS_scoped for A staging parity; final native/campaign acceptance pending.**
Reviewed runtime `1c3ee135d19fece67507b51acd1fc51ae9541208`, test source SHA256 `955db3436e0836e706a805dbe6cebf77525a4ebd96cf93e35706f2199accf353`. Specification: `outputs/validation-wave2-20261029/campaign-freeze.json`, F_controls_and_native_boundaries. No runtime edits or local Rust tests performed.

Actionable findings:

1. **[P2] Representative fixture needs a separate timeout contract.** `recovery_campaign.rs:625–646,741–747` blocks the healthy retry until old-peer EOF, while the current reader has 8-second I/O and a 30-second absolute deadline. The required original fixture has a 30-second server CQ timeout, so this choreography cannot complete the representative recovery successfully unchanged. The 1000ms small fixture is coherent. Implementer agrees to separate representative I/O 90s / deadline 120s; require a new frozen receipt and retain diagnostic-only timing.
2. **[P2] Prove each fixture write's success before trusting its ground truth.** `recovery_campaign.rs:859–862,1040–1047,1068–1075,1126–1133,1233–1240` discards `put_stream_chunks`'s returned boolean (`lib.rs:512–513`). A logical PUT/reset failure can proceed; rejection-only cut rows print the generated A hash without verifying that A was installed. Assert success and bind initial descriptor/stripe checksums to the expected payload and exact fixture dimensions before launch. Keep pre-fix frozen results separately scoped.

A static parity: direct staging matches the frozen production body after helper renaming; packed staging differs only in retry helper/control argument and formatting. All initial workers join; successful initial tasks are validated; F1 clears successful packed allocations before replacements; global reservations survive rail release; eligibility requires certified transient failure, frozen same-owner placement, and healthy alternative; retries clone frozen tasks. Absolute deadline begins before lookup, final lookup and commit gates are shared. F2 invokes actual `KvClient::read_multi_rail_into`. Scope is striped fixtures, cancel=None for F0/F1; no unstriped/error-interface generalization.

B: successful server ACK is explicitly observed before withholding; cancellation follows initial successful WRITE or complete retry registration; mutation awaits final successful retry ACK, awaits writer/lookup, then releases ACK and demands actual VersionChanged with unchanged caller bytes. No sleep-only phase proof. Late-request EOF alone remains insufficient: acceptance still requires exact canonical key+peer WRITE failure and retirement join; not_found cannot substitute.

C present implementation uses four bounded payload/key slots, persistent reader/listeners per mode, explicit rearm, exact retry bytes/tasks, byte equality, and zero terminal software budgets. Lifetime counts distinguish 1000 healthy reads from 1000 close/recreate reads; startup reads are included, actual warm reuse remains a separate counter. Final source/binary/input schedule, all-input audit, server joins, compressed-log cap and complete 1000 receipts remain pending.

Smoke logs contain 12 controls and 48 cuts (six cuts × four modes × two failed lanes), but source text was not independently archived; exploratory only. No F1/F2 timing claim accepted.

