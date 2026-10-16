# RailFuse V4 independent mechanism-image review

Date: 2026-10-16

**Final recommendation: APPROVE the four reviewed source images and their report captions. No unresolved P1 or P2 semantic blocker remains.** One P2 wording issue was identified in the first CQ image and corrected; the replacement was opened and inspected again before approval.

## Scope and exact inputs

The reviewer actually opened all four PNGs with `view_image`, read their captions in `report-content.json`, and compared their technical meaning with the actual ContextStore source and the saved frozen/V2 live evidence. The source checkout is:

`/Users/yanchaomei/Documents/Codex/2026-10-01/daocloud-contextstore-1-kv-cache-nvme/work/ContextStore`

The reviewed images are under:

`/Users/yanchaomei/.codex/.chatgpt-projects/g-p-6a8427e4c9d08191a2929ee4368fe629/outputs/submission-20261016-v4/assets/`

The caption source is the parent V4 directory's `report-content.json`, SHA-256:

`b1693423d797fba0ad09320f386eebe951716c1616ec6cf870fb9066f5c02692`

Caption-only follow-up: all four new captions were independently read back and confirmed to add exactly the prefix `AI生成的机制示意。` to the previously reviewed caption text. The remaining text is unchanged. All four image hashes were recomputed and remain identical to the approved values below. This explicit AI/schematic label adds no technical or experimental claim; the prior semantic review, resolved P2 and approval remain valid. The caption JSON hash above binds this final labeled version.

The review covered data flow, budget arithmetic and lifecycle, object publication conditions, CQ event ordering, same-handle/same-registration three-round content validation, and claim boundaries. A read-only comparison found no runtime-source changes in `src`, `server`, `client-rs`, `rdma-ffi` or `vendor` since V3 commit `eb75f83`.

No local core build/test, new core experiment or new RXE execution was performed. This review approves the original PNG bytes and captions. The parent agent owns the final embedded PDF/video visual QA, rendering, audio and decode checks. Only this audit file was added; no delivery file or Git history was changed by the reviewer.

## Final image SHA-256 bindings

| Image | Report location | SHA-256 |
| --- | --- | --- |
| `staging-layout.png` | Section 07 figure | `2afd193f3e4b652bee7eb0c31df3671c174ed2af156095af024849118447cbec` |
| `publication-timeline.png` | Section 10 figure | `d19e689ccc3cf03a3a133c66c2357e3e61bef3a5fa7bd17c855f725dae66dffd` |
| `budget-lifecycle.png` | Section 09 figure | `61d56a5f867a0ed4341832b0d64f8fb94e2e5453b589c677ab30f7123f3a845b` |
| `cq-reconnect-timeline.png` | Section 16 figure, corrected version | `cfabcc289aab5854aecfe60cb638fef752b2aedc510045fe4f0425ff90a58d3b` |

## Image-specific checks

### 1. Staging layout — PASS

The packed-contiguous path shows two private receive regions, each holding 32 MiB of useful payload plus 4 MiB of dummy space; only the useful payload forms the 64 MiB ordered staging object. The direct path shows two disjoint 32 MiB regions within one request-private ordered object, with separate 4 MiB dummy areas. Both paths retain the final copy into the caller buffer after checks; neither is drawn as network DMA directly into the caller's final object.

The arithmetic agrees with `RailReader::reserve` in `kv-service/client-rs/src/rail_read.rs`: total registration reservation is `(32+4)*2 = 72 MiB` for either mode; packed staging reservation is `64+72 = 136 MiB`, while direct staging reservation is `64+8 = 72 MiB`. The image and caption explicitly scope these as reservations for this 64 MiB/two-Rail example, not measured RSS or hardware pin-page counts. The caller's final copy is outside this private staging reservation, as the diagram's separate caller box indicates.

### 2. Publication timeline — PASS

The image correctly separates initial identity lookup, private transfer with all started tasks converged, post-transfer lookup, publication gate and caller publication. The success path requires the complete descriptor identity and Placement to remain equal, prior integrity checks to have passed, and cancellation not to have won the publication gate. The rejected path preserves the caller's old bytes rather than publishing a partial/new value.

This matches `KvClient::read_multi_rail_into`, `commit_if_unchanged` and `RailCancel::publish_if_live`. The g/g+1 labels illustrate a version-change example; neither the caption nor the diagram claims a newly measured transition or an exact server commit time. Layout/Placement-only changes are covered by the full identity/Placement comparison and do not require a universal claim that every layout change increments Generation.

### 3. Budget lifecycle — PASS

The table accurately distinguishes transfer-period reservations from the staged-object lifetime. During transfer the example has packed/direct staging 136/72 MiB, registered reservation 72 MiB, effective in-flight payload 64 MiB and one active read. After transfer convergence, the registration, in-flight and transient staging reservations are returned, while 64 MiB of final staging and one active-read slot remain. Dropping the `StagedRailRead` after publication/discard returns those remaining reservations.

This agrees with `BudgetGuard::finish_transfer`, `BudgetGuard::drop`, `StagedRailRead` ownership and `reserve`. The footer/caption correctly scope the accounting to a shared Reader instance. The final zero column describes these Reader reservations; it is not a claim that the caller's published object or every process/hardware resource has disappeared.

### 4. CQ reconnect timeline — PASS after correction

The sequence is accurate: controlled CQ poll suppression -> shutdown/control retirement and successful old-QP destruction -> consume genuine provider completion -> return the failed PUT -> explicitly reconnect -> perform three PUT/GET full-content checks using the same native handle and still-registered region. The observer does not fabricate WCs. The image allows RXE QPN reuse and explicitly distinguishes this experiment from an uninjected natural timeout.

`kv-service/rdma-ffi/src/lib.rs::retire_connection` shuts down the control stream, calls `ibv_destroy_qp` with checked cleanup success, then polls the shared CQ until empty. `verbs_cq_delay_shim.c` returns zero before destruction, delegates to provider polling after successful destruction, and logs actual WC fields. `ffi_rxe_put_reconnect.py` snapshots the old completion after the error and before reconnect, asserts one genuine status=0 old WC, retains the same handle/region, then executes three rounds whose GET result is checked against the expected full payload. The V2 live `put-cq.json` and `.events` retain exactly four genuine status=0 completions in total and three recovery rounds. The three rounds use the expected deterministic fixture; the image does not imply three different payloads or independent MR registrations.

## P2 found and resolved

**Original location:** `cq-reconnect-timeline.png`, step 2, red old-QP box, parenthetical text `进入错误状态并退休`.

That wording could imply an explicit Verbs transition to `IBV_QPS_ERR`. The current FFI implementation and retained evidence establish successful QP destruction/retirement, not an explicit `ibv_modify_qp(...IBV_QPS_ERR)` operation. The image therefore introduced an unnecessary unimplemented/unobserved state-transition detail.

**Correction:** the same box now reads `关闭控制流并销毁 QP`. The reviewer opened the replacement image and confirmed that text and the retained destruction -> real WC -> error-return ordering. The original other three images remained byte-identical.

Original CQ-image SHA-256: `2ba7bfdeb21f102f4c2ef843ed6d0b32eb410ebb3c31f7866b7632285b0b34e2`.

Approved replacement SHA-256: `cfabcc289aab5854aecfe60cb638fef752b2aedc510045fe4f0425ff90a58d3b`.

**Disposition: resolved.** No other P1/P2 issue was found within the requested image/caption scope. The images explain existing mechanisms and saved evidence; they do not add physical dual-HCA performance, natural-timeout coverage, a new Generation experiment, a universal provider proof or Python multi-Rail atomic-publication claims.

## Final disposition

**APPROVED for use of these exact four source-image files and their reviewed captions.** No new core/RXE run is required for these explanatory-image changes. Any final PDF/video embedding, scaling, cropping, caption placement or narration change remains subject to the parent agent's final artifact QA and hash binding.
