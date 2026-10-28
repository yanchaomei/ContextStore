# Recovery MVP — independent quality review

2026-10-29. **PASS for the bounded recovery quality review and final four-case native evidence readback. Both findings are closed; no production safety blocker remains.** This reviewer performed source review and independent raw-receipt validation, without editing production source or running builds/tests. `git diff --check 75dba7a` passed again after the fixes. This is not a blanket acceptance of the parent's remaining full-build/default/native-suite/performance campaign.

Scope: working-tree diff against `75dba7aa5243ef62df4f9104aec098d4659adbee` in `work/ContextStore-runtime-wave1`, using repository `CLAUDE.md` and the independent `recovery-spec.md`. Review is bounded to the approved synchronous, opt-in, striped-only, once-per-failed-task MVP.

## Closed findings

1. **[P2, closed] Do not count a no-WR terminal as late-WR evidence.** The original helper accepted `not_found_ack` and TCP reset as success. At current `kv-service/client-rs/tests/rail_read_e2e.rs:1533–1558`, the original frame is preserved before fault injection, the relay records its precise server-facing peer, and only EOF can proceed. ACK, reset, and timeout fail the case (ACK and I/O failures report INCONCLUSIVE). Final four-case readback independently found exactly one poll-final timeout with `got 0/8` for each unique case key and exactly one corresponding retirement for the failed relay peer in the raw server window. The recorded client frame fields and ordering match the raw client log. This closes both source and final evidence concerns; EOF alone was not accepted as WRITE proof.
2. **[P3, closed] Preserve the underlying connect failure in public errors.** `rail_read/connection_pool.rs:44–50` now uses `format!("{error:#}")` in both branches. The structured source still drives transient classification while both certified and ordinary errors retain the contextual chain, including the OS cause. Only diagnostic display changed.

## Reviewed quality and safety

- The opaque certificate is created only after `finish_transfer` retires QP then MR. Provider teardown failures already fail-stop; panic outcomes do not become certified transport failures. All initial workers join before any failed direct range is reborrowed.
- The retry implementation shares admission, cancellation, timing, counters, and failure cooldown through `attempt`; frozen task cloning changes only its route. Sequential retries retain global reservations, release initial rail credits after the barrier, and atomically check alternate-rail limits. No accounting leak or double-release path was found.
- Deadline checks remain at request entry, attempt admission, join/recovery, final staging, and the publication gate. The zero-I/O-timeout compatibility fix preserves the legacy transport error. Default and unstriped paths remain outside recovery/pooling.
- Successful normal reads do not gain a second payload checksum pass. The added normal-path work is task-result/liveness handling, not another object copy; runtime performance remains for the parent's pilot to measure.
- Tests use controlled barriers for cleanup, join, cancellation, and resource admission; mocks establish Rust ownership/control-flow properties only. The final native relay evidence below additionally establishes the tested real software-RoCE WRITE-attempt interleaving and failed-task recovery.
- Public additions are documented in English and remain within the approved MVP. No additional interface or duplication issue warrants expanding this change's scope.

## Reviewed source pins

SHA-256 under `kv-service/client-rs/src/`:

- `rail_read.rs`: `1d46ea5f1813cc78693299adb8e3dc6cbdf97a66370d96c09e810dfe3f8f2ab5`
- `rail_read/connection_pool.rs`: `cf9dc48aa77b9320af5b5fb0bce405aeb47ebed87047c091558472845a7a03df`
- `lib.rs`: `e6c1c9f78ce0709fd90a247f42e627550b387c145e0ed77e02faa692a5569964`
- `rdma.rs`: `62a73733c9455677cb529f563a49f157c58cdf687e08de6d2ced3ac059328d3d`

Evidence sources at static reinspection: `tests/rail_read_e2e.rs` = `346bc6680ee2b65684fc343e2315eb4deaa72c094c3c7e22cf658913d795be0a`; repository `README.md` = `e3051b1c84ecc1f78cf9044b4423bfd413fee7e478c929287814203423cc46e3`. All eight source hashes recorded in the final receipt independently match the current working tree. The two-path binary checksum receipt records the same final test binary SHA-256 on the build and guest paths: `7dbe55f923e073bbf6530c56f8bb843f5afb2c5b77650ca75ba21789954393d4`.

## Final native evidence readback

Authority files under `work/ContextStore-runtime-wave1/kv-service/verification/results/runtime-wave1/recovery/`: `recovery-verification-receipt.json` (`final_native_validation` only), `30-rxe-four-modes-v3.log`, `30-rxe-server-window.log`, and `30-rxe-binary-v3.sha256`. Older runs in the receipt are retained history and were not substituted for this final pin.

| Mode | Pool | Unique object key | Failed server peer | Raw server warning / retirement lines |
| --- | --- | --- | --- | --- |
| Direct | On | `rail-e2e-1793210195886485932` | `10.81.1.2:50344` | 9 / 10 |
| Direct | Off | `rail-e2e-1793210233252537176` | `10.81.1.2:35774` | 23 / 24 |
| Packed | On | `rail-e2e-1793210270755257871` | `10.81.1.2:41798` | 34 / 35 |
| Packed | Off | `rail-e2e-1793210306768835688` | `10.81.1.2:49960` | 48 / 49 |

All four cases passed (raw summary: `4 passed; 0 failed`, 144.14 s). Each recovered a 16 MiB object with exactly one 8 MiB failed-task retry, exact final bytes (`xxh3=614d9758e87638c7`), unchanged descriptor/placement, disjoint retained-success stripes, and retry stripes exactly equal to the failed stripes. Direct cases reused precisely the failed private addresses. In every case the retry frame was captured before the old request was forwarded, the old request ended with EOF after its server WR-completion timeout/retirement, and only then was the retry frame forwarded. Client role/peer/QPN/timestamp fields and server key/peer records were matched individually against the raw logs, not a global timeout search.

Evidence remains limited to these real Linux RXE paths in two KVM guests on one SKV2 host. It does not establish a WRITE already in flight at injected EOF, all provider failure cases, physical dual-HCA failover, or performance improvement.
