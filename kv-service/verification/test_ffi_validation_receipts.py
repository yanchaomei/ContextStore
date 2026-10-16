from __future__ import annotations

# Reject unrelated aborts and incomplete cleanup traces as native evidence.

import os
from pathlib import Path
import signal
import subprocess
import sys
import unittest

from run_ffi_lifecycle_validation import case_passed


class FfiReceiptTest(unittest.TestCase):
    def test_optimized_interpreters_reject_validation_before_argument_parsing(self):
        directory = Path(__file__).resolve().parent
        modes = (("-O", ["-O"], None), ("-OO", ["-OO"], None), ("environment", [], "1"))
        for script in (
            "ffi_lifecycle_probe.py",
            "run_ffi_lifecycle_validation.py",
            "ffi_rxe_late_write.py",
            "ffi_rxe_put_reconnect.py",
        ):
            for name, flags, environment_value in modes:
                with self.subTest(script=script, optimization=name):
                    env = os.environ.copy()
                    if environment_value is not None:
                        env["PYTHONOPTIMIZE"] = environment_value
                    process = subprocess.run(
                        [sys.executable, *flags, str(directory / script), "--help"],
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=10,
                    )
                    self.assertNotEqual(process.returncode, 0)
                    self.assertIn(b"Python", process.stderr)
                    self.assertIn(b"optimiz", process.stderr)
                    self.assertNotIn(b"SAFE_ERROR_RETURN", process.stdout)
                    self.assertNotIn(b"SAFE_INIT_FAILURE", process.stdout)

    def test_unrelated_abort_does_not_prove_qp_failstop(self):
        self.assertFalse(case_passed("free_qp", "destroy_qp", -signal.SIGABRT, b"", []))

    def test_later_mr_cleanup_invalidates_qp_failstop_order(self):
        self.assertFalse(
            case_passed("free_qp", "destroy_qp", -signal.SIGABRT, b"", ["destroy_qp", "dereg_mr"])
        )

    def test_expected_abort_stops_at_intended_verb(self):
        self.assertTrue(case_passed("free_qp", "destroy_qp", -signal.SIGABRT, b"", ["destroy_qp"]))
        self.assertTrue(case_passed("free_mr", "dereg_mr", -signal.SIGABRT, b"", ["dereg_mr"]))

    def test_returned_cleanup_marker_invalidates_abort(self):
        self.assertFalse(
            case_passed(
                "free_mr", "dereg_mr", -signal.SIGABRT, b"UNSAFE_CLEANUP_RETURNED", ["dereg_mr"]
            )
        )

    def test_normal_error_requires_asserted_return_and_settled_resources(self):
        events = ["destroy_qp", "dereg_mr", "dereg_mr", "destroy_cq", "dealloc_pd", "close_device"]
        self.assertFalse(case_passed("get_error", "", 0, b"", events))
        self.assertTrue(
            case_passed(
                "get_error", "", 0, b"SAFE_ERROR_RETURN,case=get_error,qp_retired=true", events
            )
        )


if __name__ == "__main__":
    unittest.main()
