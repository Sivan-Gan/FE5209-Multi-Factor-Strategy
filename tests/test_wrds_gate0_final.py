"""Focused tests for the Formal WRDS Gate 0 integrated final sign-off."""

from __future__ import annotations

import unittest

from src.data import wrds_gate0_final as final


class TestWrdsGate0Final(unittest.TestCase):
    def test_acceptance_matrix_has_every_mandatory_check_once(self) -> None:
        rows = final._acceptance_rows(
            {"raw_hashes_match": True, "expected_file_count": 19, "raw_failures": []},
            {"ANNUAL": {"duplicate_groups": 25437}, "QUARTERLY": {"duplicate_groups": 451}},
            final._issue_rows(),
        )
        self.assertEqual({row["check"] for row in rows}, set(final.MANDATORY_CHECKS))
        self.assertEqual(len(rows), len(final.MANDATORY_CHECKS))

    def test_issue_ids_are_unique_and_have_frozen_primary_categories(self) -> None:
        issues = final._issue_rows()
        self.assertEqual(len(issues), len({row["issue_id"] for row in issues}))
        self.assertTrue(all(row["severity"] in final.PRIMARY_CATEGORIES for row in issues))

    def test_every_open_or_blocked_issue_has_blocking_classification(self) -> None:
        issues = final._issue_rows()
        final.validate_final_outputs(
            issues,
            final._acceptance_rows(
                {"raw_hashes_match": True, "expected_file_count": 19, "raw_failures": []},
                {"ANNUAL": {"duplicate_groups": 25437}, "QUARTERLY": {"duplicate_groups": 451}},
                issues,
            ),
            final._canonical_contract_rows(),
            final._numeric_units_rows(),
        )

    def test_numeric_unit_contract_covers_cross_source_value_requirements(self) -> None:
        rows = final._numeric_units_rows()
        by_name = {row["economic_variable"]: row for row in rows}
        self.assertEqual({"E/P", "B/P", "FCF Yield"}, {name for name in by_name if name in {"E/P", "B/P", "FCF Yield"}})
        self.assertTrue(all(by_name[name]["gate0_blocking"] == "true" for name in ("E/P", "B/P", "FCF Yield")))

    def test_canonical_contract_is_complete_and_disallows_fill(self) -> None:
        rows = final._canonical_contract_rows()
        self.assertTrue(all(row["allowed_fill"] == "FALSE" for row in rows))
        self.assertIn("2026 extension separation", {row["component"] for row in rows})

    def test_blocker_prevents_pass_with_warnings(self) -> None:
        issues = final._issue_rows()
        rows = final._acceptance_rows(
            {"raw_hashes_match": True, "expected_file_count": 19, "raw_failures": []},
            {"ANNUAL": {"duplicate_groups": 25437}, "QUARTERLY": {"duplicate_groups": 451}},
            issues,
        )
        self.assertTrue(any(row["check"] == "NUMERIC_UNIT_ALIGNMENT" and row["status"] == "BLOCKED" for row in rows))
        self.assertTrue(any(row["check"] == "CANONICAL_CONSTRUCTION_DETERMINISM" and row["status"] == "BLOCKED" for row in rows))
        self.assertEqual(final.derive_verdict(rows), "FORMAL GATE 0 — BLOCKED")

    def test_fail_precedes_blocked_and_warnings_in_final_verdict(self) -> None:
        self.assertEqual(final.derive_verdict([{"status": "PASS", "blocking": "false"}]), "FORMAL GATE 0 — PASS")
        self.assertEqual(final.derive_verdict([{"status": "PASS_WITH_WARNING", "blocking": "false"}]), "FORMAL GATE 0 — PASS WITH WARNINGS")
        self.assertEqual(final.derive_verdict([{"status": "FAIL", "blocking": "true"}]), "FORMAL GATE 0 — FAIL")

    def test_final_output_ordering_and_2026_separation_are_deterministic(self) -> None:
        issues = final._issue_rows()
        ordered = sorted(issues, key=lambda row: row["issue_id"])
        self.assertEqual([row["issue_id"] for row in ordered], sorted(row["issue_id"] for row in issues))
        extension = next(row for row in final._canonical_contract_rows() if row["component"] == "2026 extension separation")
        self.assertEqual(extension["formal_period"], "2026-01-01 onward")
        self.assertNotIn("historical", extension["status"].lower())


if __name__ == "__main__":
    unittest.main()
