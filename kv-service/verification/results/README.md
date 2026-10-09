# Multi-Rail ownership verification receipts

The [paper supplement](../../../docs/paper/rewrite-race-and-server-failstop-2026-10-09.md) distinguishes the stronger unique-Generation race campaign from older repeated A/B-byte controls and documents both test-only server cleanup-error injections. These experiments extend, but do not turn into a proof, the [bounded two-Rail state model](../../../docs/paper/safety-model-plan.md).

- `state-explorer-20261009.json` is the **superseded** initial 6,045-state model, retained because independent review found a publication-before-stage-reuse gap.
- `state-explorer-revised-20261009.json` corresponds to the checked-in model source, explores 19,657 safe states and records six weakened-guard counterexamples.
- `unique-generation-sdk-call-20261009/`: **primary race evidence**. Two 8 MiB runs prepare every payload and its two segments **before** the SDK PUT call interval starts. Thirty committed distinct-content updates and 40 direct two-Rail read attempts yielded **31 SDK-call/read intersections**: six complete values matched the SDK-accepted Generation and 25 rejected with the sentinel unchanged. Nine other attempts did not intersect the SDK call. `result.json` checks event intervals, identity, modes, both Rail counters and terminal resources. A 20-file Linux build receipt binds the test binary to commit `9209d502`; launch manifests and the live server image are retained. Server commit overlap is unknown.
- `unique-generation-put-20261009/`: **earlier, wider race evidence**. Two 8 MiB runs use unique bytes and SDK-accepted identity logs but start each interval **before payload preparation**. Their 36 measured client-attempt intersections include ten accepted and 26 rejected; they bound SDK-call intersections only within those earlier runs and are not pooled with the primary 31 from new runs. Its own 20-file build and launch receipts remain preserved.
- `unique-generation-20261009/`: three earlier unique-content 8 MiB runs with 45 updates, 63 attempts and matching accepted SDK identities, but only **PUT-start through post-PUT Lookup-end** was timestamped. The 51 writer-round intersections are an **upper bound** on actual PUT overlap, and its source commit in launch manifests is an operator label without the later build-parity receipt.
- `rewrite-race-20261009/run*.log` plus `rewrite-race-64-20261009.json`: **historical, weaker** three 64 MiB A/B-pattern runs from a pre-enriched binary. The old raw logs do not contain object size, effective mode or Rail-byte fields; the supplied size is not independently proven by those transcripts. Alternating A/B content cannot expose mixing different Generations with the same bytes, and the 60 attempts were not all time-overlapping with writes.
- `rewrite-race-32-20261009/run*.log` plus `rewrite-race-32-20261009.json`: two 32 MiB historical A/B-pattern runs with logged size/direct mode and Rail bytes, but still repeated content and no exact overlap times.
- `server-failstop-runtime-20261009/`: **primary cleanup-error evidence**. Both test-only QP/MR fault cases include launch-time server/shim/config/script and client binary hashes, raw Verbs events, shell exit 134, failed trigger and same-object normal recovery. `result.json` checks all identities and paths.
- `server-failstop-20261009/`: earlier service-side shim run without launch-time binary manifests; preserved as historical continuity, not the strong binary attribution claim.
- `physical-topology-20261009/`: seven-node read-only Verbs/link-state receipts showing one ACTIVE/LINK_UP physical port per node despite two enumerated device names. [Interpretation](../../../docs/paper/physical-hca-availability-2026-10-09.md).
- `independent-skv2/`: the same raw receipts were copied to a **different physical SKV node** and re-audited under Python 3.8 without the original binaries. SDK-call race, preparation-inclusive race, earlier writer-round race, strict server fault and seven-node HCA inventory audit JSON match local outputs in full. This is replayability evidence, not an independent implementation or provider test.

`SHA256SUMS` covers the saved receipts except itself. The auditors work without the original binaries because the stronger runs recorded their hashes **before launch**; pass `--binary`, `--server-binary`, `--shim`, or `--launcher-script` when exact files are available to check those manifests against local artifacts.

```bash
python3 kv-service/verification/audit_unique_race.py \
  --logs kv-service/verification/results/unique-generation-sdk-call-20261009 \
  --object-mib 8 --expected-runs 2 --output /tmp/unique-sdk-call-audit.json
python3 kv-service/verification/audit_unique_race.py \
  --logs kv-service/verification/results/unique-generation-put-20261009 \
  --object-mib 8 --expected-runs 2 --output /tmp/unique-race-audit.json
python3 kv-service/verification/audit_race_build_receipt.py \
  --repo . \
  --receipt kv-service/verification/results/unique-generation-sdk-call-20261009/build-receipt \
  --commit 9209d5026c7e91e5ac728c47c412a44ac12d9515 \
  --output /tmp/unique-sdk-call-build-audit.json
python3 kv-service/verification/audit_race_build_receipt.py \
  --repo . \
  --receipt kv-service/verification/results/unique-generation-put-20261009/build-receipt \
  --commit d74b687738bfc803f6dccb24c568acc067fc48d7 \
  --output /tmp/unique-race-build-audit.json
python3 kv-service/verification/audit_server_failstop_runtime.py \
  --results kv-service/verification/results/server-failstop-runtime-20261009 \
  --object-key unique-race-1791532679692478926 \
  --expected-xxh3 fb2d91a1ef239d15 \
  --output /tmp/server-failstop-audit.json
```

The earlier A/B campaigns can still be checked with `audit_rewrite_race.py` and their respective `--object-mib`/`--expected-runs` arguments, but those parameters are supplied by the operator when absent from old raw logs and must not be upgraded into original evidence.
