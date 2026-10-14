"""Focused contract checks for the local 2026 Recent Extension handoff."""
from __future__ import annotations

import csv
import hashlib
import unittest
from pathlib import Path

import pyarrow.parquet as pq

from src.factors import build_recent_extension_factor_library as library
from src.handoff import prepare_us_recent_extension_2026_handoff as handoff


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / handoff.PACKAGE


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@unittest.skipUnless(PACKAGE.is_dir(), "local 2026 handoff not materialized")
class RecentExtensionHandoffTests(unittest.TestCase):
    def test_01_acceptance_has_no_failures(self) -> None:
        rows = csv_rows(PACKAGE / "acceptance.csv")
        self.assertTrue(rows)
        self.assertFalse(any(row["status"] in {"FAIL", "BLOCKED"} for row in rows))

    def test_02_factor_library_contract_is_exact(self) -> None:
        path = PACKAGE / "data/factor_library_2026.parquet"
        table = pq.read_table(path, columns=["signal_date", "factor_name", "final_k4", "availability", "missing_reason", "raw_signal", "processed_signal", "neutralized_signal", "final_score", "rank_or_percentile", "source_lineage"])
        rows = table.to_pylist()
        self.assertEqual(len(rows), handoff.EXPECTED_FACTOR_ROWS)
        self.assertEqual(len({row["factor_name"] for row in rows}), handoff.EXPECTED_FACTOR_COUNT)
        self.assertEqual(len({row["signal_date"] for row in rows}), handoff.EXPECTED_SIGNAL_DATES)
        self.assertEqual(max(row["signal_date"] for row in rows), handoff.EXPECTED_SIGNAL_END)
        self.assertEqual({row["factor_name"] for row in rows if row["final_k4"] == 1}, set(library.FINAL_K4))
        for row in rows:
            if row["factor_name"] in library.UNAVAILABLE:
                self.assertEqual(row["availability"], 0)
                self.assertEqual(row["missing_reason"], library.UNAVAILABLE[row["factor_name"]])
                self.assertTrue(all(row[field] is None for field in ("raw_signal", "processed_signal", "neutralized_signal", "final_score", "rank_or_percentile")))
        self.assertEqual({row["source_lineage"] for row in rows}, {handoff.LINEAGE})

    def test_03_market_interface_is_minimal_and_current(self) -> None:
        path = PACKAGE / "data/market_2026.parquet"
        schema = pq.ParquetFile(path).schema_arrow
        self.assertEqual(schema.names, handoff.MARKET_SCHEMA.names)
        table = pq.read_table(path, columns=["date", "canonical_security_id", "data_lineage", "daily_return", "return_available"])
        rows = table.to_pylist()
        self.assertEqual(len(rows), 97_587)
        self.assertEqual(max(row["date"] for row in rows), handoff.EXPECTED_MARKET_END)
        self.assertEqual(len({(row["date"], row["canonical_security_id"]) for row in rows}), len(rows))
        self.assertEqual({row["data_lineage"] for row in rows}, {handoff.LINEAGE})

    def test_04_registry_preserves_governed_states(self) -> None:
        rows = csv_rows(PACKAGE / "factor_registry_2026.csv")
        self.assertEqual(len(rows), handoff.EXPECTED_FACTOR_COUNT)
        self.assertEqual(sum(row["availability_status"] == "CONSTRUCTIBLE" for row in rows), handoff.EXPECTED_CONSTRUCTIBLE)
        unavailable = {row["factor_name"]: row["governed_unavailability_reason"] for row in rows if row["availability_status"] == "UNAVAILABLE_GOVERNED"}
        self.assertEqual(unavailable, library.UNAVAILABLE)
        self.assertEqual(tuple(row["factor_name"] for row in rows if row["final_k4"] == "1"), library.FINAL_K4)

    def test_05_manifest_hashes_and_sources_match(self) -> None:
        rows = csv_rows(PACKAGE / "handoff_manifest.csv")
        self.assertEqual({row["relative_path"] for row in rows}, {
            "data/factor_library_2026.parquet", "data/market_2026.parquet", "factor_registry_2026.csv", "acceptance.csv", "README.md",
        })
        for row in rows:
            path = PACKAGE / row["relative_path"]
            with path.open("rb") as handle:
                actual = hashlib.file_digest(handle, "sha256").hexdigest()
            self.assertEqual(actual, row["sha256"])
            self.assertEqual(path.stat().st_size, int(row["file_size_bytes"]))
        factor_row = next(row for row in rows if row["relative_path"] == "data/factor_library_2026.parquet")
        self.assertEqual(factor_row["source_canonical_sha256"], library.sha256(ROOT / handoff.FACTOR_STORE))

    def test_06_internal_support_and_no_strategy_outputs_are_documented(self) -> None:
        text = (PACKAGE / "README.md").read_text(encoding="utf-8")
        self.assertIn("internal build support only", text)
        self.assertIn("TECHNICALLY READY BUT REDISTRIBUTION AUTHORIZATION REQUIRED", text)
        fields = set(pq.ParquetFile(PACKAGE / "data/factor_library_2026.parquet").schema_arrow.names)
        fields |= set(pq.ParquetFile(PACKAGE / "data/market_2026.parquet").schema_arrow.names)
        self.assertFalse(fields & handoff.FORBIDDEN_COLUMNS)

    def test_07_audit_validation_is_all_pass(self) -> None:
        rows = csv_rows(ROOT / handoff.AUDIT / "recent_extension_2026_handoff_validation.csv")
        self.assertTrue(rows)
        self.assertTrue(all(row["status"] == "PASS" for row in rows))


if __name__ == "__main__":
    unittest.main()
