#!/usr/bin/env bash
set -euo pipefail
source /home/railtest/cswave1/wave2/small/client.env
cd /home/railtest/cswave1/wave2/recovery-development-v3
CS_RECOVERY_OPS=24 CS_RECOVERY_SEED=2026102925 ./recovery-test --ignored --nocapture --exact rail_read::tests::recovery_campaign::native_recovery_control_campaign > control-smoke24.log 2>&1
tail -4 control-smoke24.log
CS_RECOVERY_CUT_REPEATS=2 CS_RECOVERY_SEED=2026102927 ./recovery-test --ignored --nocapture --exact rail_read::tests::recovery_campaign::native_recovery_cut_campaign > cut-smoke48.log 2>&1
tail -4 cut-smoke48.log
CS_LIFETIME_OPS=20 CS_RECOVERY_SEED=2026102929 ./recovery-test --ignored --nocapture --exact rail_read::tests::recovery_campaign::native_recovery_reader_lifetime_campaign > lifetime-smoke40.log 2>&1
tail -4 lifetime-smoke40.log
CS_PUBLICATION_REPEATS=2 CS_RECOVERY_SEED=2026102931 ./recovery-test --ignored --nocapture --exact rail_read::tests::recovery_campaign::native_recovery_publication_gate_campaign > publication-smoke16.log 2>&1
tail -4 publication-smoke16.log
CS_RECOVERY_SEED=2026102933 ./recovery-test --ignored --nocapture --exact rail_read::tests::recovery_campaign::native_recovery_admission_campaign > admission-smoke24.log 2>&1
tail -4 admission-smoke24.log
