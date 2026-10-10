"""Focused invariants for Formal WRDS Gate 0 Phase 2B."""

from __future__ import annotations

import unittest
from datetime import date

from src.data import wrds_gate0_phase2b as phase2b


class TestWrdsGate0Phase2B(unittest.TestCase):
    def test_delisting_event_match_uses_ciz_daily_return_directly(self) -> None:
        daily = {"DlyDelFlg": "Y", "DlyRet": "-0.600000"}
        self.assertEqual(phase2b.classify_delisting_event(-0.6, daily), "DLYRET_INCORPORATES_DELISTING")
        self.assertEqual(phase2b.classify_delisting_event(None, {"DlyDelFlg": "Y", "DlyRet": ""}), "NO_DELISTING_RETURN_AVAILABLE")
        self.assertEqual(phase2b.classify_delisting_event(-0.6, {"DlyDelFlg": "N", "DlyRet": "-0.6"}), "AMBIGUOUS")

    def test_event_window_selection_is_symmetric_and_bounded(self) -> None:
        event_date = date(2020, 1, 10)
        self.assertTrue(phase2b.event_window_contains(date(2020, 1, 3), event_date))
        self.assertTrue(phase2b.event_window_contains(date(2020, 1, 17), event_date))
        self.assertFalse(phase2b.event_window_contains(date(2020, 1, 2), event_date))

    def test_dlycap_scale_reconciliation(self) -> None:
        self.assertEqual(phase2b.dlycap_ratio(1_000.0, 10.0, 100.0), 1.0)
        self.assertIsNone(phase2b.dlycap_ratio(1_000.0, 10.0, 0.0))
        self.assertEqual(phase2b.dlycap_ratio(1_000.0, 10.0, 100.0, 1000.0), 0.001)

    def test_distribution_zero_factor_is_not_a_split(self) -> None:
        self.assertEqual(
            phase2b._distribution_class({"disordinaryflg": "Y", "disfacpr": "0.000000", "disfacshr": "0.000000"}),
            "ORDINARY_DIVIDEND",
        )
        self.assertEqual(
            phase2b._distribution_class({"disordinaryflg": "N", "disfacpr": "-1.000000", "disfacshr": "-1.000000"}),
            "SPLIT_OR_SHARE_ADJUSTMENT",
        )

    def test_share_history_interval_is_inclusive(self) -> None:
        self.assertTrue(phase2b.interval_contains("2020-01-01", "2020-01-01", "2020-01-31"))
        self.assertTrue(phase2b.interval_contains("2020-01-31", "2020-01-01", "2020-01-31"))
        self.assertFalse(phase2b.interval_contains("2020-02-01", "2020-01-01", "2020-01-31"))

    def test_restricted_member_extract_requires_supplemental_issuer_evidence(self) -> None:
        self.assertEqual(phase2b.classify_issuer_sufficiency(False, 2), "SUPPLEMENTAL_CRSP_EXTRACTION_REQUIRED")
        self.assertEqual(phase2b.classify_issuer_sufficiency(True, 2), "CURRENT_EXTRACTION_SUFFICIENT_WITH_EVIDENCE")

    def test_index_coverage_check(self) -> None:
        self.assertEqual(phase2b.index_coverage_status({"a", "b"}, {"a", "b"}), (0, "PASS"))
        self.assertEqual(phase2b.index_coverage_status({"a", "b"}, {"a"}), (1, "WARN"))

    def test_deterministic_example_retention(self) -> None:
        rows: list[dict[str, object]] = []
        phase2b._smallest(rows, {"value": "z"}, ("z",), limit=2)
        phase2b._smallest(rows, {"value": "a"}, ("a",), limit=2)
        phase2b._smallest(rows, {"value": "m"}, ("m",), limit=2)
        self.assertEqual([row["value"] for row in rows], ["a", "m"])


if __name__ == "__main__":
    unittest.main()
