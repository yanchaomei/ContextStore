# FFI / Python lifecycle safety: real RXE evidence, 2026-10-16

The frozen candidate passed the complete matrix. The isolated old-library negative controls **must fail with exact late-WRITE corruption**, not merely return a nonzero code. The final source manifest is `source-sha256.json`; independent source review approval is in `review-source-addendum.txt`.

## Final results and scope

- `rxe-final/results-verified/summary.json`: 12 accepted receipts. Two old-library bad-tag controls reproduce exact 1 MiB payload corruption, one on each RXE link. Eight candidate GET cases cover both links, 1/8 MiB, malformed response/100 ms control timeout, original still-registered MR reuse through terminal server observation, and same-handle PUT/GET recovery. Two controlled CQ-delay PUT cases consume a genuine old WC before error return, then complete three same-handle/same-registration PUT/GET rounds per link.
- `host-final/evidence/ffi-lifecycle-20261016-verified`: all 12 check exit files are zero; Python 165 passed / 11 skipped; exact-event native lifecycle probes 13/13; optimized-interpreter/receipt tests 6/6; full build/server tests, Clippy, rustfmt, Black and Ruff passed. Host unit interpreter and guest runtime are recorded; native allocation/fail-stop probes are not data-WR evidence.
- `rxe-final/demo-*`: fresh 64 MiB FFI seed/GET full-content verification and current Rust single/dual-rail reads, with matching object hashes, stable identity, and 32 MiB per rail. A single functional run is not a statistical performance claim.
- `audit-attribution.json`: all frozen files equal current source; native and Rust demo build source snapshots match before/after hashes and actual source bytes; all check exits and matrix cases are recorded.

## Exact evidence boundary

These are real libibverbs/RXE data transfers in two KVM guests sharing one host and virtual disks. They do not establish physical dual-HCA aggregation or all possible interleavings. The CQ observer deliberately returns zero while the QP is active, then delegates to the genuine provider and logs actual WCs; this is controlled poll suppression, not an uninjected natural-timeout experiment. RXE may reuse QPNs immediately; safety is checked by completion order and recovered content, not QPN inequality.

The GET relay retains the exact advertised registered allocation/rkey through error return, sentinel reuse, delayed forwarding, server response/EOF, and inspection. It rejects optimization, timeouts as terminal proof, and truncated responses. Candidate server EOF after QP retirement is admissible. Complete server logs preserve aggregate timeout/retirement observations but do not identify legacy GET keys; per-case ordering comes from dedicated relay receipts, not a per-key server trace.

The existing running server is bound by PID/executable/config hashes before and after; it was not replaced during this work. The current FFI and Rust demo binaries have separate fresh build-source snapshots. Python's legacy FFI is single-connection and may contain partial data after an error; it does not inherit RailReader's failure-atomic publication. Callers retain responsibility for the lifetime and concurrent use of raw borrowed views and externally owned allocations.

## Reproduction

Build on Linux with libibverbs/provider dependencies using `make build`; run `make test-server` and `pytest tests/ -v`. Build the two test-only shims with the exact commands in `host-final/ffi-verified-20261016.sh`. Run the native lifecycle runner against the built library and selected local device/GID. For real RXE, supply two isolated client/server links and the recorded server configuration; use the final `ffi_rxe_late_write.py` and `ffi_rxe_put_reconnect.py` probe commands in each `.command.json`, with a fresh output directory/event file and unique object key. `rxe-final/run-rxe-verified.py` is the exact captured orchestration driver; its paths describe this testbed and must be adjusted for another deployment.

The ordinary Python wrapper rejects an unsafe library before native allocation. Only the isolated baseline probe bypasses that capability check, exclusively to preserve the exact-corruption negative control.

## Preserved history

`rxe-round1`, `rxe-intermediate`, `historical-rxe-20261015`, `host-round1`, `historical-host`, and the failed intermediate check receipts under `host-final` are retained unmodified. They include the old QPN-inequality test failure, argument-validation red tests, interrupted-free red test, and interpreter-compatibility failure; they are not relabeled as final results. `binaries-rxe.tar.gz` preserves the exact candidate, old baseline and CQ observer used by the RXE campaigns. The final candidate binary hash equals the archived candidate hash; fresh source attribution is in the verified build receipt.

`SHA256SUMS` inventories the evidence files. Generated presentation assets, when present, have their own capture record and describe an edited narrated view of genuine outputs, not a continuous desktop screen recording. Registration, material publication, official submission and organizer receipt remain separate states.

## Checksum naming compatibility

The Rust SDK is locked to `twox-hash 1.6.3`. Its `twox_hash::xxh3::hash64` fixture result is `a0a4cbfa5cad46af` for the 64 MiB `index % 251` payload, matching all current demo reads. A contemporary Python `xxhash` implementation produces a different standardized XXH3 value for those same bytes. `demo-fixture-check.txt` binds an independently constructed fixture to the exact compiled Rust dependency, and the SHA-256 seed remains independently verified. This delivery does not migrate persisted checksums or claim cross-library XXH3 interoperability.
