#!/usr/bin/env bash
set -euo pipefail
cd /home/railtest/cswave1/wave2/recovery-holdout-v2
export CS_RECOVERY_CAMPAIGN_DEADLINE_UNIX=$(( $(date +%s) + 5400 ))
source /home/railtest/cswave1/wave2/small/client.env
CS_RECOVERY_OPS=1000 CS_RECOVERY_CONTROLS=F2 CS_RECOVERY_SEED=2026102934 CS_RAIL_OBJECT_MIB=1 CS_RECOVERY_IO_SECS=8 CS_RECOVERY_DEADLINE_SECS=30 CS_RECOVERY_RSS_MAX_KIB=524288 CS_RECOVERY_RUN_TIMEOUT_SECS=2400 python3 ./run_recovery_campaign.py --binary /home/railtest/cswave1/wave2/recovery-holdout-v2/recovery-test --test native_recovery_control_campaign --freeze ./freeze.json --name f2-recovery1000 --output ./f2-recovery1000
source /home/railtest/cswave1/wave2/small/client.env
CS_RECOVERY_OPS=240 CS_RECOVERY_CONTROLS=F0,F1,F2 CS_RECOVERY_SEED=2026102936 CS_RAIL_OBJECT_MIB=1 CS_RECOVERY_IO_SECS=8 CS_RECOVERY_DEADLINE_SECS=30 CS_RECOVERY_RSS_MAX_KIB=524288 CS_RECOVERY_RUN_TIMEOUT_SECS=900 python3 ./run_recovery_campaign.py --binary /home/railtest/cswave1/wave2/recovery-holdout-v2/recovery-test --test native_recovery_control_campaign --freeze ./freeze.json --name matched-controls240 --output ./matched-controls240
source /home/railtest/cswave1/wave2/small/client.env
CS_RECOVERY_CUT_REPEATS=20 CS_RECOVERY_SEED=2026102938 CS_RAIL_OBJECT_MIB=1 CS_RECOVERY_IO_SECS=8 CS_RECOVERY_DEADLINE_SECS=30 CS_RECOVERY_RSS_MAX_KIB=524288 CS_RECOVERY_RUN_TIMEOUT_SECS=1200 python3 ./run_recovery_campaign.py --binary /home/railtest/cswave1/wave2/recovery-holdout-v2/recovery-test --test native_recovery_cut_campaign --freeze ./freeze.json --name cuts480 --output ./cuts480
source /home/railtest/cswave1/wave2/small/client.env
CS_LIFETIME_OPS=1000 CS_RECOVERY_SEED=2026102940 CS_RAIL_OBJECT_MIB=1 CS_RECOVERY_IO_SECS=8 CS_RECOVERY_DEADLINE_SECS=30 CS_RECOVERY_RSS_MAX_KIB=524288 CS_RECOVERY_RUN_TIMEOUT_SECS=1200 python3 ./run_recovery_campaign.py --binary /home/railtest/cswave1/wave2/recovery-holdout-v2/recovery-test --test native_recovery_reader_lifetime_campaign --freeze ./freeze.json --name lifetime2000 --output ./lifetime2000
source /home/railtest/cswave1/wave2/small/client.env
CS_PUBLICATION_REPEATS=20 CS_RECOVERY_SEED=2026102942 CS_RAIL_OBJECT_MIB=1 CS_RECOVERY_IO_SECS=8 CS_RECOVERY_DEADLINE_SECS=30 CS_RECOVERY_RSS_MAX_KIB=524288 CS_RECOVERY_RUN_TIMEOUT_SECS=900 python3 ./run_recovery_campaign.py --binary /home/railtest/cswave1/wave2/recovery-holdout-v2/recovery-test --test native_recovery_publication_gate_campaign --freeze ./freeze.json --name publication160 --output ./publication160
source /home/railtest/cswave1/wave2/small/client.env
CS_RECOVERY_SEED=2026102944 CS_RAIL_OBJECT_MIB=1 CS_RECOVERY_IO_SECS=8 CS_RECOVERY_DEADLINE_SECS=30 CS_RECOVERY_RSS_MAX_KIB=524288 CS_RECOVERY_RUN_TIMEOUT_SECS=300 python3 ./run_recovery_campaign.py --binary /home/railtest/cswave1/wave2/recovery-holdout-v2/recovery-test --test native_recovery_admission_campaign --freeze ./freeze.json --name admission24 --output ./admission24
source /home/railtest/cswave1/wave2/client.env
CS_RECOVERY_OPS=4 CS_RECOVERY_CONTROLS=F2 CS_RECOVERY_SEED=2026102946 CS_RAIL_OBJECT_MIB=16 CS_RECOVERY_IO_SECS=90 CS_RECOVERY_DEADLINE_SECS=120 CS_RECOVERY_RSS_MAX_KIB=1048576 CS_RECOVERY_RUN_TIMEOUT_SECS=600 python3 ./run_recovery_campaign.py --binary /home/railtest/cswave1/wave2/recovery-holdout-v2/recovery-test --test native_recovery_control_campaign --freeze ./freeze.json --name representative16mib4 --output ./representative16mib4
