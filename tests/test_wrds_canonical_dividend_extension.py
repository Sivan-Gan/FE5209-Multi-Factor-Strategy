"""Focused contracts for the separate formal CRSP cash-dividend extension."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.data import wrds_canonical_dividend_extension as dividend
from src.data import wrds_gate0_final as gate0


def split_row(*, factor: str = "6.0", detail: str = "STKSPL") -> dict[str, str]:
    return {"distype": "FRS", "disdetailtype": detail, "disordinaryflg": "N", "disfacpr": factor, "disfacshr": factor}


class TestWrdsCanonicalDividendExtension(unittest.TestCase):
    def test_standard_split_multiplier_uses_one_plus_disfacshr(self) -> None:
        self.assertEqual(dividend.standard_split_multiplier(split_row(factor="6.0")), 7.0)

    def test_reverse_split_multiplier_is_positive_fraction(self) -> None:
        self.assertAlmostEqual(dividend.standard_split_multiplier(split_row(factor="-0.9")) or 0.0, 0.1)

    def test_nonstandard_action_is_not_silently_used_as_split(self) -> None:
        row = split_row(); row["distype"] = "SP"
        self.assertIsNone(dividend.standard_split_multiplier(row))

    def test_only_phase2b_ordinary_events_receive_ordinary_flag(self) -> None:
        ordinary = dividend._event_row({"permno": "1", "disexdt": "2020-01-01", "disseqnbr": "1", "distype": "CD", "disordinaryflg": "Y", "disfacpr": "0", "disfacshr": "0"}, {"1"})
        nonordinary = dividend._event_row({"permno": "1", "disexdt": "2020-01-01", "disseqnbr": "2", "distype": "CG", "disordinaryflg": "N", "disfacpr": "0", "disfacshr": "0"}, {"1"})
        self.assertEqual(ordinary[20], 1)
        self.assertEqual(nonordinary[20], 0)

    def test_cash_dividend_is_normalized_to_later_share_basis(self) -> None:
        adjusted = dividend.normalize_dividend_to_signal_basis(3.29, date(2014, 5, 8), date(2014, 6, 13), [(date(2014, 6, 9), 7.0)])
        self.assertAlmostEqual(adjusted or 0.0, 0.47)

    def test_multiple_split_chain_is_multiplicative(self) -> None:
        adjusted = dividend.normalize_dividend_to_signal_basis(28.0, date(2014, 5, 8), date(2020, 9, 1), [(date(2014, 6, 9), 7.0), (date(2020, 8, 31), 4.0)])
        self.assertEqual(adjusted, 1.0)

    def test_no_future_split_enters_normalization(self) -> None:
        adjusted = dividend.normalize_dividend_to_signal_basis(1.0, date(2020, 1, 1), date(2020, 6, 1), [(date(2020, 7, 1), 4.0)])
        self.assertEqual(adjusted, 1.0)

    def test_event_date_is_ex_date_and_window_lower_bound_is_open(self) -> None:
        start, end = dividend.trailing_window_bounds(date(2020, 12, 31))
        self.assertEqual((start, end), (date(2020, 1, 1), date(2020, 12, 31)))
        self.assertFalse(start > start)
        self.assertTrue(start < date(2020, 1, 2) <= end)

    def test_zero_dividend_is_distinct_from_incomplete_coverage(self) -> None:
        self.assertEqual(dividend.dividend_window_status(date(2004, 12, 31), date(2004, 1, 2), date(2025, 12, 31)), "WINDOW_BEFORE_RAW_COVERAGE")
        self.assertEqual(dividend.dividend_window_status(date(2005, 1, 2), date(2004, 1, 2), date(2025, 12, 31)), "WINDOW_COMPLETE")

    def test_extension_schema_has_no_return_accounting_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            connection = sqlite3.connect(Path(temporary) / "events.sqlite")
            dividend._create_schema(connection)
            fields = {row[1].lower() for row in connection.execute("PRAGMA table_info(crsp_distribution_events)")}
            connection.close()
        self.assertNotIn("dlyret", fields)
        self.assertNotIn("dlyretx", fields)
        self.assertIn("ordinary_cash_dividend_flag", fields)

    def test_event_natural_key_preserves_same_date_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            connection = sqlite3.connect(Path(temporary) / "events.sqlite")
            dividend._create_schema(connection)
            values = ("1", "2020-01-01", "1") + ("",) * 23
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO crsp_distribution_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
                connection.execute("INSERT INTO crsp_distribution_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
            connection.close()

    def test_formal_cutoff_excludes_2026(self) -> None:
        self.assertEqual(dividend.FORMAL_CUTOFF, "2025-12-31")
        self.assertEqual(dividend.dividend_window_status(date(2026, 1, 2), date(2004, 1, 2), date(2025, 12, 31)), "WINDOW_AFTER_RAW_COVERAGE")

    def test_raw_package_integrity_remains_the_19_file_contract(self) -> None:
        result = gate0.verify_raw_integrity(dividend.project_root())
        self.assertEqual(result["expected_file_count"], 19)
        self.assertTrue(result["raw_hashes_match"], result)

    def test_logical_hash_is_deterministic_for_equivalent_event_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first, second = Path(temporary) / "first.sqlite", Path(temporary) / "second.sqlite"
            for path in (first, second):
                connection = sqlite3.connect(path); dividend._create_schema(connection)
                row = dividend._event_row({"permno": "1", "disexdt": "2020-01-01", "disseqnbr": "1", "distype": "CD", "disordinaryflg": "Y", "disfacpr": "0", "disfacshr": "0"}, {"1"})
                self.assertIsNotNone(row)
                connection.execute("INSERT INTO crsp_distribution_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
                connection.commit(); connection.close()
            self.assertEqual(dividend.extension_logical_hash(first), dividend.extension_logical_hash(second))


if __name__ == "__main__":
    unittest.main()
