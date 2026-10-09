"""Focused safety tests for the SEC fundamental design-review helpers."""

from __future__ import annotations

import unittest

from src.data import sec_fundamental_design_review as review
from src.data import sec_fundamental_spec as spec


class TestSecFundamentalDesignReviewHelpers(unittest.TestCase):
    def test_same_filed_conflict_is_classified_without_selecting_a_value(self) -> None:
        classification = review.classify_same_filed_conflict(
            [
                {
                    "accn": "0001",
                    "form": "10-K",
                    "fy": "2025",
                    "fp": "FY",
                    "frame": "CY2025",
                    "val": "100",
                },
                {
                    "accn": "0002",
                    "form": "10-K/A",
                    "fy": "2025",
                    "fp": "FY",
                    "frame": "CY2025",
                    "val": "90",
                },
            ]
        )
        self.assertTrue(classification["different_accession"])
        self.assertFalse(classification["same_accession"])
        self.assertTrue(classification["amendment"])
        self.assertFalse(classification["different_context_or_period_surrogate"])
        self.assertFalse(classification["other"])

    def test_same_accession_context_difference_is_not_collapsed(self) -> None:
        classification = review.classify_same_filed_conflict(
            [
                {"accn": "0001", "form": "10-Q", "fy": "2025", "fp": "Q1", "frame": "CY2025Q1"},
                {"accn": "0001", "form": "10-Q", "fy": "2025", "fp": "Q2", "frame": "CY2025Q2"},
            ]
        )
        self.assertFalse(classification["different_accession"])
        self.assertTrue(classification["same_accession"])
        self.assertTrue(classification["different_context_or_period_surrogate"])
        self.assertFalse(classification["other"])

    def test_gross_profit_pairs_never_imply_an_automatic_tag_merge(self) -> None:
        statuses = {pair.pair_id: pair.semantic_status for pair in review.GROSS_PROFIT_PAIRS}
        self.assertEqual(statuses["revenues_less_cost_of_revenue"], "not_universal")
        self.assertEqual(
            statuses["contract_revenue_ex_tax_less_cost_of_revenue"],
            "conditional_fallback_candidate",
        )

    def test_review_does_not_invent_the_absent_finance_lease_total_tag(self) -> None:
        numerator_tags = {tag for definition in review.LEVERAGE_DEFINITIONS for tag in definition.numerator_tags}
        self.assertNotIn("LongTermDebtAndFinanceLeaseObligations", numerator_tags)
        self.assertIn("LongTermDebtAndCapitalLeaseObligations", numerator_tags)

    def test_net_income_primary_metadata_matches_the_proposed_signoff_rule(self) -> None:
        candidates = {candidate.tag: candidate.proposal_role for candidate in spec.CONCEPT_CANDIDATES["net_income"]}
        self.assertEqual(candidates["NetIncomeLoss"], "primary_candidate")
        self.assertEqual(candidates["ProfitLoss"], "conditional_candidate")


if __name__ == "__main__":
    unittest.main()
