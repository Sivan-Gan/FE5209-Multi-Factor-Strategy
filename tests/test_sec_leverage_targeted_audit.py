from __future__ import annotations

from decimal import Decimal
import unittest

from src.data import sec_leverage_targeted_audit as audit


class TestSecLeverageTargetedAuditHelpers(unittest.TestCase):
    def test_candidate_scope_and_recommendation_are_explicit(self) -> None:
        by_id = {candidate.definition_id: candidate for candidate in audit.LEVERAGE_CANDIDATES}
        self.assertEqual(
            set(by_id),
            {
                "long_term_debt_components_over_assets",
                "liabilities_over_assets",
                "assets_over_stockholders_equity",
                "liabilities_over_stockholders_equity",
            },
        )
        self.assertEqual(
            by_id["liabilities_over_assets"].recommendation_role,
            "RECOMMENDED_BASELINE",
        )
        self.assertEqual(
            by_id["long_term_debt_components_over_assets"].numerator_tags,
            ("LongTermDebtCurrent", "LongTermDebtNoncurrent"),
        )

    def test_20f_is_eligible_but_non_reviewed_forms_are_not(self) -> None:
        self.assertTrue(audit._eligible_form({"form": "20-F"}))
        self.assertTrue(audit._eligible_form({"form": "20-F/A"}))
        self.assertFalse(audit._eligible_form({"form": "40-F"}))
        self.assertFalse(audit._eligible_form({"form": "8-K"}))

    def test_conflicting_same_key_values_are_not_stable(self) -> None:
        key = (1, "2025-12-31", "10-K", "2026-02-20", "0001")
        self.assertEqual(audit._value_token("1.0"), audit._value_token("1.00"))
        self.assertEqual(
            audit._stable_values({key: {audit._value_token("1.0")}}),
            {key: Decimal("1.0")},
        )
        self.assertEqual(
            audit._stable_values({key: {audit._value_token("1.0"), audit._value_token("2.0")}}),
            {},
        )

    def test_all_recorded_signoffs_are_approved_dev(self) -> None:
        rows = audit._signoff_rows()
        self.assertEqual(len(rows), 5)
        self.assertEqual({row["status"] for row in rows}, {"APPROVED_DEV"})
        self.assertIn("negative_capex", {row["decision_id"] for row in rows})


if __name__ == "__main__":
    unittest.main()
