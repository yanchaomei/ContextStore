"""Fail-closed checks for the rejection identity receipt format."""

import unittest

from audit_generation_transition import audit_rejection_ids


VERSION_CHANGED = "object version changed during rail read"
STALE = "object descriptor became stale"


class RejectionIdentityAuditTest(unittest.TestCase):
    def test_counts_only_observed_generation_changes(self):
        reads = [
            {"result": "rejected", "error": VERSION_CHANGED},
            {"result": "rejected", "error": STALE},
            {"result": "rejected", "error": VERSION_CHANGED},
        ]
        raw = "\n".join(
            [
                "RAIL_REJECTED_ID,namespace=rail-e2e,object_key=race-key,reason=version_changed,initial_generation=1,current_generation=2,initial_layout_version=1,current_layout_version=1",
                "RAIL_REJECTED_ID,namespace=rail-e2e,object_key=race-key,reason=version_changed,initial_generation=2,current_generation=3,initial_layout_version=1,current_layout_version=1",
            ]
        )
        result = audit_rejection_ids(raw, reads, "race-key")
        self.assertEqual(result["generation_transition_rejections"], 2)
        self.assertEqual(result["same_generation_identity_rejections"], 0)
        self.assertEqual(result["transitions"], [[1, 2], [2, 3]])
        self.assertEqual(result["transition_attempts"], [0, 2])

    def test_missing_identity_trace_fails(self):
        with self.assertRaisesRegex(ValueError, "trace count"):
            audit_rejection_ids(
                "", [{"result": "rejected", "error": VERSION_CHANGED}], "race-key"
            )

    def test_same_generation_identity_rejection_is_not_a_transition(self):
        reads = [{"result": "rejected", "error": VERSION_CHANGED}]
        raw = "RAIL_REJECTED_ID,namespace=rail-e2e,object_key=race-key,reason=version_changed,initial_generation=2,current_generation=2,initial_layout_version=1,current_layout_version=1"
        result = audit_rejection_ids(raw, reads, "race-key")
        self.assertEqual(result["generation_transition_rejections"], 0)
        self.assertEqual(result["same_generation_identity_rejections"], 1)

    def test_unrelated_object_trace_fails(self):
        reads = [{"result": "rejected", "error": VERSION_CHANGED}]
        raw = "RAIL_REJECTED_ID,namespace=rail-e2e,object_key=other,reason=version_changed,initial_generation=1,current_generation=2,initial_layout_version=1,current_layout_version=1"
        with self.assertRaisesRegex(ValueError, "another object"):
            audit_rejection_ids(raw, reads, "race-key")


if __name__ == "__main__":
    unittest.main()
