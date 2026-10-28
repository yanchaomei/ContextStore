# Descriptor fallback guard — independent quality review

**Final verdict: QUALITY_PASS_SCOPED + PASS_NATIVE_BRANCH_REGRESSION_SCOPED. No actionable defects found. The matched native comparison confirms the server guard for one 8 KiB unstriped cold descriptor GET per variant; strict Clippy remains failing with unchanged baseline diagnostics.**

The initial static-only report is preserved byte-for-byte as `descriptor-guard-quality-static-v1.md` (SHA-256 `d92cb7071f861ef7383e3f3abfb96c964bf30f0b1ea3669e7dba1818b85f5d9a`). The native addendum below supersedes its pending build/native status. The source and lint findings below are unchanged.

## Reviewed scope

- Only the working-tree diff of `kv-service/server/src/rdma/server.rs` against `1c3ee135d19fece67507b51acd1fc51ae9541208` (59 additions, 9 deletions). Other uncommitted campaign files are excluded.
- Current file, `green-server.rs`, and the copy in `green-source.tar.gz` independently match SHA-256 `d7df834df7d4ffb86a04725a5055069cfadca2b253fc37ea092028fa50bd5b71`.
- `baseline-server.rs` independently matches the bytes from `git show HEAD:kv-service/server/src/rdma/server.rs` (SHA-256 `a3b4e2675366aa233ea7c813bf72100891e8bc7fe3a905101b5322ddb1087346`).
- Read the existing SPEC pass and repository `CLAUDE.md`. This is the subsequent quality axis, without changing the accepted requirements or requesting unrelated cleanup.

## Source and regression assessment

The local `descriptor_get_fallback` helper (`server.rs:572–584`) accepts the owned `anyhow::Error`, detects the typed `RetireLegacyGet` through context, and returns that same error before warning or invoking the fallback closure. Ordinary errors retain the previous storage lookup, response tuple, warning, and propagation behavior. The closure introduces no heap allocation or dynamic dispatch and is confined to the existing error branch.

The call-site `?` (`1737–1758`) exits the descriptor handler before sending a response; its caller's existing `?` (`314–324`) exits the connection loop. Ownership stays with existing RAII: the cold slab path moves an uncertain source extent into the QP (`924–930`), successful QP destruction precedes its retained-source release (`qp.rs:398–407`), and the CQ guard is declared before the QP (`257–260`). There is no new cleanup path, client pool policy, protocol change, dependency, or provider stub.

The regression observes a real fallback-closure side effect, preserves marker identity and outer context, and includes ordinary-error controls. It is not merely a duplicate predicate assertion. The RED snapshot differs from GREEN by the guard, its documentation, the ordinary-error control, and formatting; the RED log records the behavioral assertion failing with exit `101` because fallback executed. The focused GREEN receipt records four passing tests (two guard and two wire tests), exit `0`.

Added source comments, documentation, and messages are English. The documented error contract distinguishes uncertain WRITE retirement from safe fallback. No unnecessary abstraction or unrelated change was found. `git diff --check -- kv-service/server/src/rdma/server.rs` passed in this review.

## Independent lint comparison

I parsed the four raw JSON Clippy logs, rather than relying on `clippy-comparison.json`. Diagnostic identity includes severity, lint code, message, span filename, primary-span flag, and source text. Absolute positions are omitted because inserting the guard shifts existing lines.

| Configuration | Candidate emitted / unique | Baseline emitted / unique | New / removed | Both exits |
| --- | ---: | ---: | ---: | ---: |
| Default | 68 / 31 | 68 / 31 | 0 / 0 | 101 |
| `rdma,io-uring,metrics` | 116 / 53 | 116 / 53 | 0 / 0 | 101 |

Commands match within each pair, including `--all-targets --message-format=json -- -D warnings`. The baseline runner copies the frozen candidate snapshot and replaces only this server source with the verified HEAD snapshot. Server-local RDMA diagnostics are the same pre-existing documentation, range-loop, and argument-count findings, with only expected line shifts. **This supports no lint regression; it does not make strict Clippy green.**

## Initial receipt review

Receipts were read from `kv-service/verification/results/validation-wave2/descriptor-guard/`. Final-source formatting exits `0`; default server tests record `77 + 3 + 2 = 82` passes; RDMA server tests record `109 + 3 + 2 = 114` passes, all with zero failures. The runner starts a private real Redis process, its ping receipt is `PONG`, and both suites include the two Redis metadata tests. I did not rerun Rust tests locally or execute native jobs.

At the initial readback, `make-build.log` existed but `make-build.exit` did not; the build was pending in that copied snapshot. Its completed receipt is assessed below.

The prepared native probe correctly captures the tag-8 unstriped descriptor frame, uses the current fixed Rust client and a fully registered private 8 KiB target, waits for client QP retirement before forwarding, rejects truncated/nonempty server responses and read timeout, and retains the registration until server EOF. Exact fresh reads run before and after. No wrong wire-field offset or premature target release was found. At initial readback the native plan was `PREPARED_NOT_RUN`.

The fixed client alone can keep this target unchanged, even with the server bug. Therefore probe success/EOF is not independently a server-guard oracle: the plan's required server retirement marker and absence of the descriptor fallback warning must also be read back. A baseline fallback warning is branch evidence only. The initial review established no native execution; the addendum resolves that narrow gate.

## Independent native addendum

Read-only acceptance used `native-run-20261029-v1/` plus the preceding source/build freezes. I independently recalculated all 49 entries of `evidence-sha256.json`: zero mismatches. Both server logs and both client logs equal their respective tar archive members byte-for-byte. The native receipt hash is `0f64648ff023979c53378b9baf7e3721c2b40371467b11080e56d160f119e2e1`. No remote command, test, provider hook, or native operation was run by this reviewer.

### Matched build and runtime identity

The accepted pair is the corrected independent-target build, with debug profile and `rdma` for both servers. The builder selects `native-target-candidate` and `native-target-baseline`, records successful exits, then freezes the binaries. The earlier shared-target pair is explicitly retained as invalid and is excluded. Build-freeze hashes match both guest deployment and post-run hash records; process identities name those frozen executables.

| Artifact | SHA-256 |
| --- | --- |
| Baseline server | `4a6458a6a2183b9db110d893748673e4591839b97f087b6e57949c325ab77902` |
| Candidate server | `580fd1cbf62c69dc4f2a1edc726b64b51a50934f6ca99b60ef8dee7805e2a418` |
| Shared fixed-client probe | `9e9511d97ae6415d3a7c7d7db550c1f01a79e2068087275c7c4d299ebb089b1d` |
| Probe source | `c84a6ec2aaa5c24726ef796601d1a89cb102c31cbe2960c4328272648bdb51c2` |
| Fixed Rust client source | `62a73733c9455677cb529f563a49f157c58cdf687e08de6d2ced3ac059328d3d` |
| Runtime config | `aff9592c1690d3625997b09e7b4c93658eb2ca5195d07bd26a5fc12a72e2d896` |

The source freeze identifies only `server/src/rdma/server.rs` as a production-source difference. Its baseline and candidate hashes match the reviewed snapshots above. The archived fixed-client source independently matches its freeze; the working server still matches the reviewed candidate hash.

The common runtime freeze precedes execution and records server `10.81.1.1`, client `10.81.1.2`, device `rxe0`, GID index 1, forced disk read, 16 MiB slab, and 300 ms CQ timeout. The saved launcher applies these values to both variants, and both startup logs show the same actual device, GID, address, and slab. The runtime config's recalculated hash matches deployment and post-run records. This separately frozen address/device adjustment replaces the unexecuted `10.31`/`rxe_s0` template; it is not a source change.

### Full-window branch evidence

| Observation | Baseline | Candidate |
| --- | --- | --- |
| Complete raw server log | 26 lines | 25 lines |
| Controlled connection | `10.81.1.2:40302` | `10.81.1.2:56580` |
| Fallback count across complete window | 1, line 21 | 0 |
| Terminal server event | Line 22: second `poll_n` timeout | Line 21: original `RetireLegacyGet` display, including final-completion timeout |
| Surrounding fresh connections | `40288`, then `40312` | `56572`, then `56596` |
| Native client result | Exit 0, one passing test | Exit 0, one passing test |

Each isolated server window has exactly three sequential connections and two successful cold-read entries for the same unique canonical key and 8192-byte size. Baseline key: `16:descriptor-guarddescriptor-guard-1793221383353203862`; candidate key: `16:descriptor-guarddescriptor-guard-1793221398896451799`. The frozen probe uses one descriptor for both exact reads and forwards the captured descriptor request unchanged between them. This establishes the fault request's key association through probe control flow and the isolated connection sequence; the fault log itself does not print the key or dump the wire frame.

The baseline warning is followed by another CQ timeout on the controlled peer, consistent with the reviewed baseline executing fallback after uncertain completion. The candidate directly emits the original retirement error and closes that connection, without any descriptor fallback warning anywhere in the complete startup-through-last-read log. This is the native distinguishing evidence. Typed error identity is additionally established by the unit regression's downcast; a log message alone is not a runtime type inspection.

Both client logs record controlled EOF, an unchanged still-registered private target after server EOF, and exact fresh reads before and after. The frozen source compares all 8192 bytes against `byte[index] = index % 251`; the expected-vector file independently matches that definition and SHA-256 `25df2449b2e5a35fea14e02a7158e283801a1069c9f84631b9a9dacb2f809a7f`. That file is an expected vector, not a captured RDMA receive dump. The target is deregistered only after the proxy has observed server EOF and joined.

### Completed build and retained limits

The completed `make-build.exit` is `0`, with start `2026-10-28T20:19:44Z` and end `20:23:07Z`; the raw log finishes all release components. That release build is separate from the matched debug native comparison and provides no performance comparison. Existing formatting, RED/GREEN, 82/114 test, and unchanged-baseline Clippy findings remain as recorded above. **All four strict Clippy comparisons still exit 101.**

The native evidence supports the server guard on this full-object descriptor GET path, experimentally limited to one 8 KiB unstriped object per variant. Both clients passing is expected with the fixed client and is not the guard's causal contrast. No old unsafe FFI library, corruption/reuse experiment, provider return-code injection, or blocked FFI case was executed by this probe or review; no acceptance is granted for those cases. Post-run receipts show private processes/listeners gone, no remaining client MRs/RC QPs, and the pre-existing server fixtures still present.

The result does not cover every size/layout, striped descriptor requests, physical-HCA scaling, latency/throughput benefit, or general late-DMA/corruption safety. Within the stated branch-regression scope, no actionable evidence defect remains.
