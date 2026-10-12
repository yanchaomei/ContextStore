# Generation-transition witness: one two-RXE process run

This is a **one-process functional and safety receipt**, not a throughput experiment. One client Worker used two distinct software-RXE Rails in separate KVM guests on one SKV host. The test wrote 15 unique-content overwrites of one 8 MiB, two-stripe object while issuing 20 direct two-Rail reads.

- `run1.log`, `.launch`, `.exit`: the exact release test binary was hashed **before** execution and exited 0. Its SHA-256 is `fe3635ddb8e4e64466ecda79e8f699f5d2a1c479405fd9548e145cc62b147fdb`. `UNIQUE_CONFIG` declares `put_interval_scope=sdk_call,rejection_id_trace=true`. Each successful read verifies complete Generation-coded bytes; each failure verifies the unchanged caller sentinel.
- `result.json`: [the transition auditor](../../audit_generation_transition.py) first applies the existing whole-object/Rail/resource audit, then pairs each `VersionChanged` outcome with the sequential reader's SDK `RAIL_REJECTED_ID` trace. **Eight of twenty reads** observed strictly increasing Generation pairs across the two lookups and rejected safely; two other reads rejected with stale descriptors and have no such pair. All twenty intersected an SDK PUT call interval, ten accepted complete values, ten rejected, zero missing. Neither this audit nor the time intervals locate the server's exact commit point.
- `build-receipt/source-snapshot/` saves twenty Linux build inputs. `build-audit.json` checks their SHA-256 and Git blob identities against source commit `f1a3fbf4861918f68376ce7c09d287a331ff29bf`; the release output SHA matches `run1.launch`. The auditor requires its Git **index tree** to equal that source commit, so rerun it in a checkout of that commit with the saved receipt supplied by absolute path. It does not claim a hermetic reproducible build.
- `build-tamper-control.txt` records that adding one byte to a copy of the `Cargo.toml` source snapshot made the build auditor fail. `client-unit-tests.log` records 53 passed RDMA-feature client tests and one intentionally ignored Mock benchmark; `audit-unit-tests.log` records four passing fail-closed parser tests. These are separate from the real two-RXE E2E run.
- `server-live-before.txt` and `server-live-after.txt` contain the same PID, `/proc/PID/exe` SHA-256 and configuration SHA-256. `server.log.gz` retains the test-only service log. `topology-*.txt` records two active virtual RXE device/subnet pairs and separate host bridges. No physical dual-HCA conclusion follows.
- [The independent replay](../independent-skv2/transition-independent.json) was computed from the same raw files under Python 3.8 on a second physical SKV node and matches `result.json` byte for byte. This is independent recomputation, not another execution schedule.

The test VM had approximately 115 MiB free after this run, so no second process run was attempted. The one-run count is kept separate from the earlier 31 SDK-call overlaps in [`unique-generation-sdk-call-20261009`](../unique-generation-sdk-call-20261009/README.md).

From a checkout of the **evidence branch**, recompute the event audit:

```bash
python3 kv-service/verification/audit_generation_transition.py \
  --logs kv-service/verification/results/unique-generation-transition-20261012 \
  --object-mib 8 --expected-runs 1 \
  --expected-source-commit f1a3fbf4861918f68376ce7c09d287a331ff29bf \
  --output /tmp/contextstore-transition-audit.json
```

To recheck source-to-build identity, use the same saved receipt from a checkout whose index tree is exactly `f1a3fbf`:

```bash
python3 kv-service/verification/audit_race_build_receipt.py \
  --repo . \
  --receipt /absolute/path/to/evidence/kv-service/verification/results/unique-generation-transition-20261012/build-receipt \
  --commit f1a3fbf4861918f68376ce7c09d287a331ff29bf \
  --output /tmp/contextstore-transition-build-audit.json
```
