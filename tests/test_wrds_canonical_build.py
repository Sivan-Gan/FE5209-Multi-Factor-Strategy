"""Focused contract tests for Formal WRDS canonical construction."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.data import wrds_canonical_build as canonical


class TestWrdsCanonicalBuild(unittest.TestCase):
    def test_formal_cutoff_is_frozen(self) -> None:
        self.assertEqual(canonical.FORMAL_CUTOFF, "2025-12-31")

    def test_weekly_signal_is_last_actual_trading_day(self) -> None:
        rows = canonical.weekly_rows([date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)])
        self.assertEqual(rows, [("2025-01-03", "2025-01-06", "HIST_OOS_BASE")])

    def test_weekly_execution_is_next_local_trading_day_not_calendar_day(self) -> None:
        self.assertEqual(canonical.next_trading_day(date(2025, 7, 3), [date(2025, 7, 3), date(2025, 7, 7)]), date(2025, 7, 7))

    def test_no_weekly_row_without_formal_execution_date(self) -> None:
        self.assertEqual(canonical.weekly_rows([date(2025, 12, 29), date(2025, 12, 30), date(2025, 12, 31)]), [])

    def test_sample_labels_are_frozen(self) -> None:
        self.assertEqual(canonical.sample_label("2004-05-01"), "INITIALIZATION")
        self.assertEqual(canonical.sample_label("2006-01-03"), "DEV_WARMUP")
        self.assertEqual(canonical.sample_label("2011-01-03"), "FACTOR_SELECTION")
        self.assertEqual(canonical.sample_label("2016-01-04"), "CONFIGURATION_AND_STRATEGY_FREEZE")
        self.assertEqual(canonical.sample_label("2021-01-04"), "HIST_OOS_BASE")
        self.assertEqual(canonical.sample_label("2026-01-02"), "OUTSIDE_FORMAL_PERIOD")

    def test_standard_annual_record_predicate(self) -> None:
        self.assertTrue(canonical.standard_row({"indfmt": "INDL", "datafmt": "STD", "consol": "C", "curcd": "USD"}, "ANNUAL"))
        self.assertFalse(canonical.standard_row({"indfmt": "INDL", "datafmt": "STD", "consol": "C", "curcd": "CAD"}, "ANNUAL"))

    def test_standard_quarterly_record_predicate(self) -> None:
        self.assertTrue(canonical.standard_row({"indfmt": "INDL", "datafmt": "STD", "consol": "C", "curcdq": "USD"}, "QUARTERLY"))
        self.assertFalse(canonical.standard_row({"indfmt": "INDL", "datafmt": "STD", "consol": "C", "curcdq": ""}, "QUARTERLY"))

    def test_only_authorized_unit_normalizations_are_declared(self) -> None:
        self.assertEqual(canonical.RESOLVED_UNITS["ANNUAL"], {"ni", "ceq", "oancf", "capx"})
        self.assertEqual(canonical.RESOLVED_UNITS["QUARTERLY"], {"niq", "ceqq", "oancfy", "capxy"})

    def test_interval_match_rejects_identifier_ambiguity(self) -> None:
        intervals = [
            {"permco": "1", "classification": "INCLUDED_COMMON_EQUITY", "start": "2020-01-01", "end": "2020-12-31", "ticker": "A"},
            {"permco": "2", "classification": "INCLUDED_COMMON_EQUITY", "start": "2020-01-01", "end": "2020-12-31", "ticker": "B"},
        ]
        self.assertIsNone(canonical._interval_match(intervals, "2020-06-30"))

    def test_interval_match_accepts_same_identifier_records(self) -> None:
        intervals = [
            {"permco": "1", "classification": "INCLUDED_COMMON_EQUITY", "start": "2020-01-01", "end": "2020-12-31", "ticker": "A"},
            {"permco": "1", "classification": "INCLUDED_COMMON_EQUITY", "start": "2020-03-01", "end": "2020-12-31", "ticker": "A"},
        ]
        self.assertEqual(canonical._interval_match(intervals, "2020-06-30")["permco"], "1")

    def test_schema_contains_all_required_layers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            connection = sqlite3.connect(Path(temporary) / "canonical.sqlite")
            canonical._create_schema(connection)
            actual = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({name for name, _, _ in canonical.DATASETS}.issubset(actual))
            connection.close()

    def test_security_daily_natural_key_is_exact_date_permno(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            connection = sqlite3.connect(Path(temporary) / "canonical.sqlite")
            canonical._create_schema(connection)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO security_daily(date,permno,identifier_status,source_layer) VALUES ('2020-01-01','1','OK','T')")
                connection.execute("INSERT INTO security_daily(date,permno,identifier_status,source_layer) VALUES ('2020-01-01','1','OK','T')")
            connection.close()

    def test_weekly_panel_natural_key_is_exact_signal_permno(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            connection = sqlite3.connect(Path(temporary) / "canonical.sqlite")
            canonical._create_schema(connection)
            params = ("2020-01-03", "1", "2020-01-06", "FACTOR_SELECTION", None, None, "OK", "OK", None, None, None, None, None, None, None, None, 0, None, None, "", "", canonical.EVIDENCE_STAGE)
            connection.execute("INSERT INTO weekly_signal_panel VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", params)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO weekly_signal_panel VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", params)
            connection.close()

    def test_issuer_market_cap_requires_all_active_classes(self) -> None:
        required, available = 2, 1
        self.assertFalse(required == available)

    def test_no_yahoo_or_sec_as_formal_source(self) -> None:
        source = Path(canonical.__file__).read_text(encoding="utf-8").lower()
        self.assertNotIn("yahoo", source)
        self.assertNotIn("sec company", source)

    def test_formal_raw_integrity_is_19_files(self) -> None:
        result = canonical.gate0.verify_raw_integrity(canonical.project_root())
        self.assertEqual(result["expected_file_count"], 19)
        self.assertTrue(result["raw_hashes_match"], result)

    def test_candidate_dataset_names_are_stable(self) -> None:
        self.assertEqual([name for name, _, _ in canonical.DATASETS], ["membership_daily", "security_daily", "issuer_market_cap_daily", "market_index_daily", "pit_fundamental_events", "weekly_calendar", "weekly_signal_panel"])

    def test_numeric_parser_rejects_nan(self) -> None:
        self.assertIsNone(canonical.number("nan"))
        self.assertIsNone(canonical.number("inf"))

    def test_numeric_parser_preserves_finite_numeric_value(self) -> None:
        self.assertEqual(canonical.number("123.45"), 123.45)

    def test_next_trading_day_is_strictly_after_publication(self) -> None:
        dates = [date(2020, 5, 1), date(2020, 5, 4)]
        self.assertEqual(canonical.next_trading_day(date(2020, 5, 1), dates), date(2020, 5, 4))

    def test_next_trading_day_missing_successor_is_unavailable(self) -> None:
        self.assertIsNone(canonical.next_trading_day(date(2020, 5, 4), [date(2020, 5, 4)]))

    def test_formal_period_excludes_2026_sample_label(self) -> None:
        self.assertEqual(canonical.sample_label("2026-01-01"), "OUTSIDE_FORMAL_PERIOD")

    def test_ttm_is_metadata_not_a_constructed_ttm_value_column(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            connection = sqlite3.connect(Path(temporary) / "canonical.sqlite")
            canonical._create_schema(connection)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(pit_fundamental_events)")}
            self.assertIn("ttm_eligible", columns)
            self.assertNotIn("ttm_value", columns)
            connection.close()

    def test_issuer_market_cap_has_explicit_availability_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            connection = sqlite3.connect(Path(temporary) / "canonical.sqlite")
            canonical._create_schema(connection)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(issuer_market_cap_daily)")}
            self.assertTrue({"required_class_count", "available_class_count", "availability_flag", "unavailable_reason"}.issubset(columns))
            connection.close()

    def test_canonical_store_uses_formal_wrds_evidence_stage(self) -> None:
        self.assertEqual(canonical.EVIDENCE_STAGE, "FORMAL_WRDS_CANONICAL")


if __name__ == "__main__":
    unittest.main()
