# Descriptor fallback guard — independent quality review

**Verdict: QUALITY_PASS_SCOPED. No actionable defects in the reviewed server change. Strict Clippy remains failing with unchanged baseline diagnostics; build and native validation are not certified by this review.**

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

## Receipts and remaining boundaries

Receipts were read from `kv-service/verification/results/validation-wave2/descriptor-guard/`. Final-source formatting exits `0`; default server tests record `77 + 3 + 2 = 82` passes; RDMA server tests record `109 + 3 + 2 = 114` passes, all with zero failures. The runner starts a private real Redis process, its ping receipt is `PONG`, and both suites include the two Redis metadata tests. I did not rerun Rust tests locally or execute native jobs.

At review readback, `make-build.log` exists but `make-build.exit` does not, so `make build` remains pending in these copied receipts.

The prepared native probe correctly captures the tag-8 unstriped descriptor frame, uses the current fixed Rust client and a fully registered private 8 KiB target, waits for client QP retirement before forwarding, rejects truncated/nonempty server responses and read timeout, and retains the registration until server EOF. Exact fresh reads run before and after. No wrong wire-field offset or premature target release was found. The native plan remains `PREPARED_NOT_RUN`.

The fixed client alone can keep this target unchanged, even with the server bug. Therefore probe success/EOF is not independently a server-guard oracle: the plan's required server retirement marker and absence of the descriptor fallback warning must also be read back. A baseline fallback warning is branch evidence only. This review establishes neither native guard execution, late-DMA safety, corruption prevention, nor multi-rail performance.
