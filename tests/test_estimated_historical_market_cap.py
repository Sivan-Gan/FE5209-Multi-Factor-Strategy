"""Safety and contract tests for the DEV estimated-market-cap object only."""

from __future__ import annotations

import json
import math
import sqlite3
import unittest
from datetime import date

from src.data import estimated_historical_market_cap as market_cap
from src.data import historical_market_cap_signoff_audit as signoff
from src.data import source_audit


ROOT = market_cap.project_root()


class TestEstimatedMarketCapRules(unittest.TestCase):
    def test_market_cap_formula_requires_positive_finite_inputs(self) -> None:
        self.assertEqual(market_cap.estimated_market_cap(10.0, 200.0), 2_000.0)
        self.assertIsNone(market_cap.estimated_market_cap(None, 200.0))
        self.assertIsNone(market_cap.estimated_market_cap(10.0, 0.0))
        self.assertIsNone(market_cap.estimated_market_cap(math.inf, 200.0))

    def test_standard_reverse_and_multiple_split_chain_are_explicit(self) -> None:
        chain = market_cap.split_chain(
            (
                (date(2020, 1, 1), 7.0, True),
                (date(2020, 2, 1), 0.1, True),
                (date(2020, 3, 1), 4.0, True),
            ),
            date(2019, 12, 31),
        )
        self.assertAlmostEqual(chain.factor, 2.8)
        self.assertEqual([factor for _day, factor in chain.standard_events], [7.0, 0.1, 4.0])

    def test_split_chain_excludes_pre_asof_event_and_separates_nonstandard_action(self) -> None:
        chain = market_cap.split_chain(
            (
                (date(2020, 1, 1), 7.0, True),
                (date(2020, 1, 2), 1.398, False),
            ),
            date(2020, 1, 1),
        )
        self.assertEqual(chain.factor, 1.0)
        self.assertEqual(chain.standard_events, ())
        self.assertEqual(chain.nonstandard_events, ((date(2020, 1, 2), 1.398),))

    def test_no_future_price_or_fill_and_zero_volume_review_are_separate(self) -> None:
        self.assertEqual(signoff.price_status(None), ("NO_SIGNAL_DATE_CLOSE", False))
        zero_volume = {
            "close": "10", "is_ohlc_plausible": "true", "is_zero_volume": "true",
            "is_review_security": "false",
        }
        self.assertEqual(signoff.price_status(zero_volume), ("PRICE_REVIEW_FLAG_ZERO_VOLUME", True))

    def test_exclusion_reason_priority_rejects_multiclass_and_nonstandard(self) -> None:
        base = {
            "sec_mapping_available": True, "share_status": "AVAILABLE", "raw_records": [],
            "share_value": 100.0, "usable_price": True, "price_code": "PRICE_VALID",
        }
        self.assertEqual(market_cap._primary_reason(**base, is_multiclass=True, has_nonstandard_action=False), "MULTICLASS_UNRESOLVED")
        self.assertEqual(market_cap._primary_reason(**base, is_multiclass=False, has_nonstandard_action=True), "NONSTANDARD_CORPORATE_ACTION")


class TestEstimatedMarketCapArtifact(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = market_cap.paths(ROOT)
        self.connection = sqlite3.connect(f"file:{self.paths['canonical']}?mode=ro", uri=True)

    def tearDown(self) -> None:
        self.connection.close()

    def _scalar(self, sql: str) -> int:
        return int(self.connection.execute(sql).fetchone()[0])

    def test_complete_grid_and_downstream_schema(self) -> None:
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(weekly_estimated_market_cap)")}
        required = {"signal_date", "ticker", "estimated_market_cap", "market_cap_source", "availability_flag", "exclusion_reason", "evidence_stage"}
        self.assertTrue(required.issubset(columns))
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM weekly_estimated_market_cap"), 503 * 1081)
        self.assertEqual(self._scalar("SELECT COUNT(*) - COUNT(DISTINCT ticker || '|' || signal_date) FROM weekly_estimated_market_cap"), 0)

    def test_pit_exact_signal_price_and_share_validity(self) -> None:
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM weekly_estimated_market_cap WHERE availability_flag = 1 AND shares_filed_date > signal_date"), 0)
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM weekly_estimated_market_cap WHERE availability_flag = 1 AND price_date <> signal_date"), 0)
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM weekly_estimated_market_cap WHERE availability_flag = 1 AND reported_shares <= 1"), 0)
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM weekly_estimated_market_cap WHERE availability_flag = 1 AND (price_close IS NULL OR price_date IS NULL)"), 0)

    def test_available_values_have_complete_provenance_and_positive_formula(self) -> None:
        self.assertEqual(self._scalar("""
            SELECT COUNT(*) FROM weekly_estimated_market_cap
            WHERE availability_flag = 1 AND (
                estimated_market_cap <= 0 OR estimated_market_cap != estimated_market_cap OR
                ABS(estimated_market_cap - price_close * aligned_shares) > 0.000001 * estimated_market_cap OR
                shares_source_tag <> 'dei:EntityCommonStockSharesOutstanding' OR
                shares_unit <> 'shares' OR shares_accession IS NULL OR split_adjustment_factor IS NULL
            )
        """), 0)

    def test_missing_price_multiclass_and_nonstandard_rows_never_produce_market_cap(self) -> None:
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM weekly_estimated_market_cap WHERE price_status = 'NO_SIGNAL_DATE_CLOSE' AND availability_flag = 1"), 0)
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM weekly_estimated_market_cap WHERE multi_class_flag = 1 AND availability_flag = 1"), 0)
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM weekly_estimated_market_cap WHERE corporate_action_flag = 1 AND availability_flag = 1"), 0)
        self.assertGreater(self._scalar("SELECT COUNT(*) FROM weekly_estimated_market_cap WHERE availability_flag = 1 AND price_zero_volume_flag = 1 AND price_review_flag = 1"), 0)

    def test_unavailable_rows_have_a_primary_reason_and_preserve_all_flags(self) -> None:
        rows = self.connection.execute("""
            SELECT exclusion_reason, all_exclusion_flags_json
            FROM weekly_estimated_market_cap WHERE availability_flag = 0
        """)
        count = 0
        for reason, flags in rows:
            count += 1
            self.assertIn(reason, signoff.REQUIRED_REASON_CODES)
            self.assertIsInstance(json.loads(flags), dict)
        self.assertGreater(count, 0)

    def test_representative_split_cases_pass_without_mechanical_jump(self) -> None:
        with market_cap.paths(ROOT)["splits"].open(encoding="utf-8") as handle:
            text = handle.read()
        for case in ("AAPL_2014_7_FOR_1", "AAPL_2020_4_FOR_1", "AMZN_2022_20_FOR_1", "C_2011_1_FOR_10_REVERSE"):
            self.assertIn(f"{case}", text)
        self.assertNotIn(",FAIL\n", text)

    def test_raw_package_integrity_and_acceptance_boundary(self) -> None:
        manifest = source_audit.audit_manifest(source_audit.package_root(ROOT))
        report = self.paths["acceptance"].read_text(encoding="utf-8")
        self.assertTrue(manifest["passed"])
        self.assertEqual(manifest["verified_payload_files"], 508)
        self.assertIn("PASS WITH WARNINGS", report)
        self.assertIn("No valuation ratio, factor, IC, portfolio, FMOM, or strategy", report)
        self.assertIn("separately authorized semantic share-scale rule", report)


if __name__ == "__main__":
    unittest.main()
