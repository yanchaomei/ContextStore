# Recovery QUALITY review — final holdout-v2 / v4 native binary

**FINAL QUALITY_PASS_scoped — launcher CLEAR for holdout-v2. The frozen formal run may start. This is a source and development-evidence gate, not acceptance of the pending holdout.**

Final pin: test source `b0670c77df08a67884ee4759175a2d55b7b75eb6d763901f4124161e6dcea087`; native binary `714913de399181eb58e5289ef4d08b76cf9a1afea5a43d3b177b8a4364fc343c`; runner `641f13137038022b41d31ddf6377690ac9aa5473d6d455daf6eeb0ba159edc1f`; auditor `2c74a030d62e6a17e51415492b1d7c9323c2644fe822851726286ce76bb7dda8`. The current source files, archived final source snapshots and runner/auditor files were independently hashed and match `kv-service/verification/results/validation-wave2/recovery/holdout-v2/freeze.json`. Binary identity is recorded consistently in the v4 development and final manifests; the runner verifies the binary hash before launch.

The actual v3-to-v4 Rust diff is exactly one `FrameCapture` tuple type alias plus three redundant borrow removals. It changes no control flow, values, timings, routes, allocation lifetime or test assertions. Remote build/Clippy logs end successfully; the unit log records **90 passed, 0 failed, 6 ignored**. Independent v4 raw readback confirms control12 = 36 frames / 12 strict adjacent key-peer old-WR retirements and publication8 = 24 frames / 8 such retirements: **20 operations, 60 frames, 20 strict old-WR terminals**, all expected frozen matrices and raw log hashes matched. Those are the v4 smoke counts; the larger v3 results below remain explicitly v3 evidence.

The final auditor now rejects orphan/extra frame IDs, compares global frame/late/held-ACK counts with expected totals, and requires the immediately preceding exact-key WR failure before exact-peer retirement. The final runner contains owned-child-only termination with a 10-second grace, exit 124 / `INCOMPLETE_TIMEOUT`, per-suite limits of 2400/900/1200/1200/900/300/600 seconds in manifest run order, and the launch script sets a 5400-second total campaign budget. The final numeric resource aggregation safely skips unavailable `/proc` fields while retaining raw samples. The earlier audit/watchdog follow-ups are closed for this launch.

Original v3 gate: reviewed all 1,688 lines of `kv-service/client-rs/src/rail_read/recovery_campaign.rs`, SHA256 `df69cd3250e30bcb4eab3d2be37902d6f5b4c06e4892e4e5c3a4168b2d8d7099`, the child-module addition in `rail_read/tests.rs`, runner, audit script, v1 prepared holdout freeze, and five local v3 raw logs. Production `rail_read.rs` remains `1d46ea5f1813cc78693299adb8e3dc6cbdf97a66370d96c09e810dfe3f8f2ab5`; the v3 binary was `60df58f3b0a0a9b4be30a969b7599527c547faa3f516e7aa1944a51f1d3471c9` and is superseded by the v4 pin above. No source edits, local Rust tests, remote workload, or changes to the separate soak work were performed by this reviewer.

No must-fix correctness or safety finding was found within the frozen contract. The following conclusions are deliberately scoped to the dedicated RXE fixture, sequential operations, and exact binary/input manifest.

- F0/F1 use the production planner, resource guard, transport cleanup certificate, checksum checks, original absolute deadline and final authoritative lookup. Initial workers are joined before retry; packed successes are discarded before F1 replacement allocation; direct ranges have no old mutable worker borrows when reused. F2 calls the actual `KvClient` API. Exact 0 / whole-object / failed-task retry payload checks support the intended bytes comparison.
- Fault gates cannot silently turn arbitrary errors into expected success. Old fault requests must receive server EOF; held ACK cases require the actual successful server ACK; cancellation, deadline, overwrite/delete and publication cases require their specific error type and unchanged caller sentinel. PUT success and authoritative per-stripe checksums bind the fixture to the expected payload before testing.
- Operations are serialized and use a fixed key set. Persistent listeners reap completed workers. A mode's normal teardown drops the Reader before stopping/joining its relay listeners, allowing pooled sockets to close before worker joins. Each operation replaces its state and checks budget/pool quiescence. This supports bounded repeated client state; it does not claim that fixture objects are deleted after the campaign. Fixed fixture keys intentionally remain, rather than growing with operation count.
- The publication relay uses one fresh connection and exactly two unary lookups, so stream 1 / stream 3 is valid for this fixture. It forwards other frames, bounds inbound frames at 64 KiB, rejects padding/compression/fragmentation it does not support, decodes the actual protobuf response, and changes only the capability field (or delays the response). Unsupported framing yields failure, and generic gRPC errors cannot satisfy the typed final-error checks. The successful path shuts down both sockets before joining the forwarding thread. This is an explicit control-plane response injection, not a production HTTP/2 proxy or evidence of actual server capability reconfiguration.

Independent raw readback reproduced the development counts and verified every late-write failure on the **immediately preceding** server log line with its exact canonical key, followed by the exact peer retirement; all 80 server terminals were unique across the five suites. Each recorded client log hash matched its original audit.

| Development suite | Operations | Native frames | Exact adjacent key/peer retirement | Held successful ACK |
| --- | ---: | ---: | ---: | ---: |
| controls | 24 | 72 | 24 | 0 |
| cuts | 48 | 128 | 24 | 16 |
| lifetime | 40 | 80 | 0 | 0 |
| publication | 16 | 48 | 16 | 0 |
| admission | 24 | 16 | 16 | 0 |
| Total | 152 | 344 | 80 | 16 |

The strengthened auditor's frozen mode/lane/control/cut/lifetime/capability matrix passed all five suites. Removing one native frame from each suite caused rejection in all five local Python-only negative checks. Details are in `recovery-quality-smoke-readback.json` beside this report.

Final receipt gates, which do not block running the unchanged native binary:

1. Use the final frozen-manifest matrix audit, not the older proof-join-only summaries. Reconcile all operation IDs, expected per-operation and global native-frame/late-frame/held-ACK totals, four control payload identities, full mode and failed-lane coverage, lifetime categories and frozen inputs. The final auditor now rejects orphan/extra frame IDs as well as missing frames.
2. Keep final exact-key/peer proof at least as strict as this independent readback. The final auditor uses immediate adjacency; do not fall back to the earlier preceding-20-lines search for an ambiguous or missing terminal.
3. Inspect process exit, launch manifest, resource samples, frozen hashes and final runner/auditor hashes with the formal results. One-second resource samples support observed sampled peaks; they do not establish that no shorter peak occurred. The repeated fixture has no performance acceptance claim.

Remaining operational limit: relay frame recording follows ACK forwarding; a scheduling delay could omit a frame from a prematurely captured per-operation receipt in the control/cut suites. The strict final frame-count audit must treat that as incomplete evidence, never PASS. Coordinator calls are not forcibly interrupted by the Reader deadline, but the final owned-child watchdog now bounds a stalled run and records it as incomplete. These conditions do not change the correctness or bytes conclusion from completed development traces.

The 1,000 F2 holdout operations, 240 matched controls, 480 cuts, 1,000 healthy reuse plus 1,000 Reader recreations, 160 publication gates, 24 admission cases and four 16 MiB representative cases are still pending. No holdout result, latency improvement, physical multi-HCA scaling, production deployment or upstream acceptance is claimed.
