"""Synthetic PIT-safety and classification tests for SEC input specification."""

from __future__ import annotations

from datetime import date
import unittest

from src.data import sec_fundamental_spec as sec_spec


class TestSecFundamentalSpecification(unittest.TestCase):
    def test_filed_date_rule_blocks_future_filing(self) -> None:
        original = {"filed": "2025-02-20", "val": "100", "accn": "original", "form": "10-K"}
        amendment = {"filed": "2025-03-15", "val": "90", "accn": "amendment", "form": "10-K/A"}
        self.assertTrue(sec_spec.observation_available_on_or_before(original, date(2025, 3, 1)))
        self.assertFalse(sec_spec.observation_available_on_or_before(amendment, date(2025, 3, 1)))

    def test_amendment_switches_only_after_its_own_filing_date(self) -> None:
        original = {"filed": "2025-02-20", "val": "100", "accn": "original", "form": "10-K"}
        amendment = {"filed": "2025-03-15", "val": "90", "accn": "amendment", "form": "10-K/A"}
        before, before_reason = sec_spec.select_latest_pit_observation_proposal(
            [original, amendment], date(2025, 3, 1)
        )
        after, after_reason = sec_spec.select_latest_pit_observation_proposal(
            [original, amendment], date(2025, 3, 21)
        )
        self.assertEqual(before_reason, "selected_latest_eligible_filing")
        self.assertEqual(before["val"], "100")
        self.assertEqual(after_reason, "selected_latest_eligible_filing")
        self.assertEqual(after["val"], "90")

    def test_same_filed_conflicting_duplicates_remain_open(self) -> None:
        records = [
            {"filed": "2025-02-20", "val": "100", "accn": "one", "form": "10-K"},
            {"filed": "2025-02-20", "val": "90", "accn": "two", "form": "10-K"},
        ]
        selected, reason = sec_spec.select_latest_pit_observation_proposal(records, date(2025, 2, 21))
        self.assertIsNone(selected)
        self.assertEqual(reason, "OPEN_DECISION_ambiguous_same_filed_date_duplicates")

    def test_unit_filtering_never_silently_converts(self) -> None:
        self.assertEqual(sec_spec.unit_status("net_income", "USD"), "proposed_canonical_unit")
        self.assertEqual(sec_spec.unit_status("net_income", "shares"), "excluded_nonmonetary_or_ratio_unit")
        self.assertEqual(sec_spec.unit_status("net_income", "EUR"), "UNRESOLVED_UNIT")

    def test_duration_and_instant_classification_uses_more_than_fp(self) -> None:
        annual, annual_days, _ = sec_spec.classify_period(
            {"start": "2024-01-01", "end": "2024-12-31", "form": "10-K", "fp": "FY"}
        )
        quarterly, quarterly_days, _ = sec_spec.classify_period(
            {"start": "2024-01-01", "end": "2024-03-31", "form": "10-Q", "fp": "Q1"}
        )
        ytd, ytd_days, _ = sec_spec.classify_period(
            {"start": "2024-01-01", "end": "2024-06-30", "form": "10-Q", "fp": "Q2"}
        )
        instant, instant_days, _ = sec_spec.classify_period(
            {"end": "2024-12-31", "form": "10-K", "fp": "FY"}
        )
        self.assertEqual((annual, annual_days), ("annual_candidate", 365))
        self.assertEqual((quarterly, quarterly_days), ("quarterly_candidate", 90))
        self.assertEqual((ytd, ytd_days), ("ytd_candidate", 181))
        self.assertEqual((instant, instant_days), ("instant", None))
        irregular, _, _ = sec_spec.classify_period(
            {"start": "2024-01-01", "end": "2024-12-31", "form": "10-Q", "fp": "Q1"}
        )
        self.assertEqual(irregular, "irregular_duration")


if __name__ == "__main__":
    unittest.main()
