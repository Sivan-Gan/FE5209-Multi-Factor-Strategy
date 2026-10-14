"""Focused contracts for the separate 2026 Quality/Investment support layer."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.data import wrds_recent_extension_quality_investment_support as support


def event(event_id: int, year: int, quarter: int, availability: str) -> support.Event:
    return support.Event(
        event_id=event_id, frequency="QUARTERLY", gvkey="000001", datadate=f"{year}-{quarter * 3:02d}-28",
        fiscal_year=year, fiscal_quarter=quarter, rdq=availability, availability_date=availability,
        availability_status="PIT_AVAILABLE_NEXT_LOCAL_TRADING_DAY", raw_row_count=1, conflict_flag=0,
        values={"niq": 10.0, "ceqq": 100.0, "atq": 200.0, "saleq": 50.0, "cogsq": 30.0, "dlcq": 5.0, "dlttq": 20.0},
        flow_values={}, ttm_eligible=1, ttm_reason="FOUR_CONSECUTIVE_PIT_ELIGIBLE_SINGLE_QUARTER_FLOWS",
    )


class TestRecentExtensionQualityInvestmentSupport(unittest.TestCase):
    def test_support_store_is_separate_and_lineage_is_explicit(self) -> None:
        self.assertEqual(support.DATA_LINEAGE, "RECENT_EXTENSION_MIXED_SOURCE")
        self.assertNotEqual(support.SUPPORT_STORE, support.MARKET_STORE)
        self.assertIn("wrds_recent_extension", support.SUPPORT_STORE.as_posix())

    def test_strict_next_trading_day_timing(self) -> None:
        days = [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)]
        self.assertEqual(support.next_trading_day(date(2026, 1, 2), days), date(2026, 1, 5))
        self.assertIsNone(support.next_trading_day(date(2026, 1, 6), days))

    def test_historical_gics_matching_is_inclusive_and_never_current_backfilled(self) -> None:
        intervals = {"000001": [{"indfrom": "2026-01-01", "indthru": "2026-03-31", "gsector": "45"}]}
        self.assertEqual(len(support.gics_at(intervals, "000001", "2026-01-01")), 1)
        self.assertEqual(len(support.gics_at(intervals, "000001", "2026-03-31")), 1)
        self.assertEqual(support.gics_at(intervals, "000001", "2026-04-01"), [])

    def test_issuer_identity_uses_only_approved_membership_mapping(self) -> None:
        exact = {"gvkey": "000001", "temporal_identity_rule": ""}
        self.assertEqual(support._issuer_identity(exact, {"permco": "123", "identity_mapping_status": ""}), ("PERMCO:123", "PERMCO", "EXACT_CONTINUOUS_ISSUER"))
        direct = {"gvkey": "000002", "temporal_identity_rule": ""}
        self.assertEqual(support._issuer_identity(direct, {"permco": "", "identity_mapping_status": "AUDITED_2026_ADDITION_GVKEY_IID"}), ("GVKEY:000002", "GVKEY", "NEW_2026_ISSUER"))
        self.assertEqual(support._issuer_identity(direct, {"permco": "", "identity_mapping_status": "UNRESOLVED"})[2], "REVIEW_REQUIRED")

    def test_quality_and_investment_metric_precedence_are_pit_asof(self) -> None:
        events = [event(index + 1, year, quarter, f"{year}-{quarter * 3:02d}-29") for index, (year, quarter) in enumerate([(2024, 1), (2024, 2), (2024, 3), (2024, 4), (2025, 1), (2025, 2), (2025, 3), (2025, 4)])]
        metrics = support.PITMetrics(events).metrics("000001", "2026-01-15")
        self.assertIsNotNone(metrics["roe"]["value"])
        self.assertIsNotNone(metrics["roa"]["value"])
        self.assertIsNotNone(metrics["gpa"]["value"])
        self.assertIsNotNone(metrics["negative_leverage"]["value"])
        self.assertIsNotNone(metrics["investment"]["value"])
        self.assertTrue(all(event_id <= 8 for event_id in metrics["investment"]["event_ids"]))

    def test_schema_contains_support_interfaces_and_no_factor_score_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = sqlite3.connect(Path(directory) / "support.sqlite")
            support._create_schema(connection)
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"pit_fundamental_events", "pit_fundamental_signal_asof", "historical_gics_signal", "issuer_market_cap_components", "issuer_market_cap_daily", "factor_input_readiness"}.issubset(tables))
            self.assertFalse(any("score" in table.lower() for table in tables))
            connection.close()


if __name__ == "__main__":
    unittest.main()
