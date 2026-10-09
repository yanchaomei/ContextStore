# Raw receipts for the direct-staging paper experiment

This directory is an **RXE software-Verbs** result, not a physical multi-HCA benchmark. [The method and interpretation](../../../../docs/paper/direct-staging-eval-2026-10-09.md) describe the five arms, paired-block bootstrap, source fallback and limits. [`provenance.json`](provenance.json) pins the source commit, final binary hashes, build/test receipts and environment class. [`SHA256SUMS`](SHA256SUMS) hashes every file in this result tree except itself.

- `64mib/` and `256mib/`: final binary `v3`, ten balanced blocks each, fifty raw process logs per size, all samples, environment identities and audited ratios. Each `audit.json` embeds hashes of the source raw logs. The final client binary SHA is `4b511dfa061afb1131da39daac85e1d60f98b971b4f38b952e023d67865f0ee0`.
- `replication-v2/`: an earlier near-identical normal-path build with a different cleanup-error log path, retained as a sensitivity check. It is **not** pooled into final block intervals. Its 64 MiB direct/one-Rail `rxe_c0` interval crosses 1, while the same-plan direct/packed-contiguous primary contrast is above 1 in both sessions.
- `faults/`: final direct read, in-flight cancellation, dead second listener and delayed second-Rail WRITE test logs, plus compressed server log.
- `failstop-injection/`: a test-only LD_PRELOAD shim returns an error from QP destruction or MR deregistration with stderr closed. Its `result.json` records the abort signal and the Verbs cleanup events observed before abort. This tests the Rust cleanup branch, not actual firmware/provider failure.
- `stage-pilot/`: three measured reads per arm with research timing enabled, recorded after the primary matrix. These fixed-order traces diagnose assembly/checksum cost but are not independent causal blocks.
- `topology-*-v3.txt`, `server.toml`, `server-v3-full.log.gz`: software-device, bridge, CPU/disk, server configuration and source-fallback evidence.

To re-audit a size from its raw process logs:

```bash
python3 kv-service/benchmarks/audit_rail_modes.py \
  --results kv-service/benchmarks/results/paper-direct-final-2026-10-09/64mib \
  --output /tmp/contextstore-direct-64-audit.json
```

The same command accepts `256mib` or a `replication-v2/*` directory. Compare the regenerated `comparisons`, `resources` and `source_sha256` with the checked-in audit. To verify file integrity, run `sha256sum -c SHA256SUMS` from this directory on Linux or `shasum -a 256 -c SHA256SUMS` on macOS.
