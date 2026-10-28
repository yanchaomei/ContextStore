# Recovery SPEC review — v3 final code gate

**SPEC_CODE_PASS. Eligible for quality review and frozen formal execution. Formal results are not yet accepted.**

Reviewed test SHA256 `df69cd3250e30bcb4eab3d2be37902d6f5b4c06e4892e4e5c3a4168b2d8d7099`; frozen binary `60df58f3b0a0a9b4be30a969b7599527c547faa3f516e7aa1944a51f1d3471c9`. Current files match all source hashes and three archived v3 snapshots. Production `rail_read.rs` remains frozen `1c3ee135`; tracked change is only the test child-module declaration. No implementation edits or local Rust tests performed.

Both previous P2 findings are resolved in source: all fixture PUTs assert the returned success value, and `assert_payload_identity` binds authoritative stripe checksums/size to expected A/B bytes. Timeout parameters are frozen separately: small 8s I/O / 30s deadline; representative 16MiB uses 90s / 120s with the original 30s server CQ fixture. Representative execution remains pending.

A parity remains PASS_scoped: initial stage matches production; all workers join; successful packed buffers drop before F1 replacements; global reservations persist; retry eligibility uses certified transient failure, frozen same-owner placement, and live alternative; both lookups and publication gates retain the original absolute deadline. F2 calls actual KvClient. No timing comparison accepted.

Independent raw v3 audit, beyond reading supplied summaries:

| Suite | Operations | Frames | Strict late-WR key/peer proof |
|---|---:|---:|---:|
| controls | 24 | 72 | 24 |
| cuts | 48 | 128 | 24 |
| lifetime | 40 | 80 | 0 |
| publication | 16 | 48 | 16 |
| admission | 24 | 16 | 16 |

All 152 operation IDs are complete/unique. All 344 captured frames identify 1MiB / four 256KiB stripes and retain one frozen wire identity within each operation. All 80 late-WR requests have an immediately adjacent exact-key CQ timeout followed by their exact-peer retirement, with no terminal reused and no not_found substitution. Cuts include 16 observed successful ACK holds. Controls, cuts and publication cover both failed lanes in every selected mode; lifetime separates 20 healthy reads and 20 close/recreate reads.

Publication source intercepts the actual second lookup after native transfer/checksum/ensure_live: delay causes typed DeadlineExceeded; valid protobuf capability injection causes typed VersionChanged; caller sentinel and actual server identity remain unchanged. This proves native execution plus a control-plane response injection, not actual server capability reconfiguration. Admission checks typed rejection, unchanged sentinel and zero retry.

Holdout-v1 freezes new seeds, F2 1000 total / 250 per mode, controls240, cuts480, publication160, lifetime1000+1000, admission24 and representative4, separately from development counts. Fixed keys, persistent mode readers/listeners, explicit rearm, and exact task/byte checks remain.

**Final acceptance gate, not an execution blocker:** bundled audit currently reports mode counts and validates only present late frames. Final receipts must additionally reconcile expected late/frame counts, full mode/failed-lane matrix, four exercised control hashes, lifetime categories and every input against the freeze. Process peaks are one-second sampled observations. Formal counts, representative results and performance claims remain unaccepted.
