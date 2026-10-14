"""Focused contract tests for the isolated 2026 recent-extension canonical layer."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.data import wrds_recent_extension_canonical as extension


class TestWrdsRecentExtensionCanonical(unittest.TestCase):
    def test_lineage_is_explicit_and_not_formal_history(self) -> None:
        self.assertEqual(extension.DATA_LINEAGE, "RECENT_EXTENSION_MIXED_SOURCE")
        self.assertNotIn("wrds_formal", extension.STORE.as_posix())

    def test_direct_compustat_total_return_uses_trfd_without_dividing_by_100(self) -> None:
        previous = {"prccd": "100", "ajexdi": "2", "trfd": "1.5"}
        current = {"prccd": "110", "ajexdi": "2", "trfd": "1.5"}
        self.assertAlmostEqual(extension.direct_total_return(current, previous), 0.1)

    def test_constant_trfd_recovery_uses_adjusted_price_without_populating_trfd(self) -> None:
        previous = {"prccd": "100", "ajexdi": "1", "trfd": ""}
        current = {"prccd": "110", "ajexdi": "1", "trfd": ""}
        self.assertAlmostEqual(extension.adjusted_price_return(current, previous), 0.1)
        returned, available, method = extension._return_for(current, previous, {("000001", "01", "123456789")}, set(), False)
        self.assertIsNone(returned)
        self.assertFalse(available)
        self.assertEqual(method, extension.RETURN_UNVALIDATED)
        current.update({"gvkey": "000001", "iid": "01", "cusip": "123456789"})
        returned, available, method = extension._return_for(current, previous, {("000001", "01", "123456789")}, set(), False)
        self.assertAlmostEqual(returned, 0.1)
        self.assertTrue(available)
        self.assertEqual(method, extension.RETURN_RECOVERY)
        self.assertEqual(current["trfd"], "")

    def test_new_ctva_startup_is_never_recovered(self) -> None:
        row = {"gvkey": "077301", "iid": "01", "cusip": "22052L104", "prccd": "12", "ajexdi": "1", "trfd": ""}
        returned, available, method = extension._return_for(row, row, {("077301", "01", "22052L104")}, set(), True)
        self.assertIsNone(returned)
        self.assertFalse(available)
        self.assertEqual(method, extension.RETURN_NEW_CTVA)

    def test_market_cap_uses_absolute_price_times_unscaled_cshoc(self) -> None:
        cap, available = extension._market_cap_for({"prccd": "-10", "cshoc": "1000000"})
        self.assertTrue(available)
        self.assertEqual(cap, 10_000_000.0)

    def test_benchmark_formula_uses_prccddiv(self) -> None:
        self.assertAlmostEqual(extension.benchmark_return({"prccddiv": "121"}, {"prccddiv": "110"}), 0.1)

    def test_identical_supplement_may_override_provenance_but_not_values(self) -> None:
        main = {"gvkey": "035168", "iid": "01", "cusip": "92892R108", "datadate": "2026-10-01", "prccd": "10", "_source_dataset": "main"}
        supplement = {**main, "_source_dataset": "vylr"}
        merged = extension._merge_supplement_indexes(extension._indexed([main]), [supplement])
        self.assertEqual(merged[extension._source_key(main)]["_source_dataset"], "vylr")
        conflicting = {**supplement, "prccd": "11"}
        with self.assertRaises(ValueError):
            extension._merge_supplement_indexes(extension._indexed([main]), [conflicting])

    def test_store_schema_is_separate_and_has_required_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "extension.sqlite"
            connection = sqlite3.connect(path)
            extension._create_schema(connection)
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"canonical_membership_daily", "canonical_security_daily", "canonical_benchmark_daily", "source_manifest", "build_metadata"}.issubset(tables))
            columns = {row[1] for row in connection.execute("PRAGMA table_info(canonical_security_daily)")}
            self.assertTrue({"return_available", "return_method", "market_cap_available", "market_cap_reason", "temporal_identity_rule", "identity_provenance", "data_lineage"}.issubset(columns))
            connection.close()

    def test_target_counts_are_frozen(self) -> None:
        self.assertEqual(extension.EXPECTED_MEMBER_DATES, 97_587)
        self.assertEqual(extension.EXPECTED_RETURN_AVAILABLE, 96_959)
        self.assertEqual(extension.EXPECTED_MARKET_CAP_AVAILABLE, 97_582)
        self.assertEqual(extension.EXPECTED_AUTHORIZED_RECOVERIES, 13_375)


if __name__ == "__main__":
    unittest.main()
