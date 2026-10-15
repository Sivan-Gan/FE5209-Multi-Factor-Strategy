"""Focused contract checks for the local 2026 Recent Extension handoff."""
from __future__ import annotations

import csv
import hashlib
import math
import os
import tempfile
import unittest
from pathlib import Path

import pyarrow.parquet as pq

from src.factors import build_recent_extension_factor_library as library
from src.handoff import prepare_us_recent_extension_2026_handoff as handoff


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / handoff.PACKAGE
TEST_CANONICAL_ROOT = Path(os.environ.get("FE5209_TEST_CANONICAL_ROOT", str(handoff.DEFAULT_CANONICAL_ROOT)))


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@unittest.skipUnless(PACKAGE.is_dir(), "local 2026 handoff not materialized")
class RecentExtensionHandoffTests(unittest.TestCase):
    def test_00_source_root_is_explicit_and_project_relative(self) -> None:
        configured = handoff.canonical_root_path(ROOT, TEST_CANONICAL_ROOT)
        self.assertEqual(configured, ROOT / TEST_CANONICAL_ROOT)
        self.assertTrue(configured.is_dir())
        for store in (handoff.FACTOR_STORE, handoff.MARKET_STORE, handoff.SUPPORT_STORE):
            self.assertEqual(handoff.source_path(ROOT, store, TEST_CANONICAL_ROOT), configured / store)

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
            "data/factor_library_2026.parquet", "data/market_2026.parquet", "data/benchmark_daily_returns_2026.parquet", "factor_registry_2026.csv", "acceptance.csv", "README.md",
        })
        for row in rows:
            path = PACKAGE / row["relative_path"]
            with path.open("rb") as handle:
                actual = hashlib.file_digest(handle, "sha256").hexdigest()
            self.assertEqual(actual, row["sha256"])
            self.assertEqual(path.stat().st_size, int(row["file_size_bytes"]))
        factor_row = next(row for row in rows if row["relative_path"] == "data/factor_library_2026.parquet")
        market_row = next(row for row in rows if row["relative_path"] == "data/market_2026.parquet")
        benchmark_row = next(row for row in rows if row["relative_path"] == "data/benchmark_daily_returns_2026.parquet")
        self.assertEqual(factor_row["source_canonical_sha256"], handoff.sha256(handoff.source_path(ROOT, handoff.FACTOR_STORE, TEST_CANONICAL_ROOT)))
        self.assertEqual(market_row["source_canonical_sha256"], handoff.sha256(handoff.source_path(ROOT, handoff.MARKET_STORE, TEST_CANONICAL_ROOT)))
        self.assertEqual(benchmark_row["source_canonical_sha256"], handoff.sha256(handoff.source_path(ROOT, handoff.MARKET_STORE, TEST_CANONICAL_ROOT)))
        self.assertEqual(benchmark_row["redistribution_status"], handoff.REDISTRIBUTION_STATUS)

    def test_06_internal_support_and_no_strategy_outputs_are_documented(self) -> None:
        text = (PACKAGE / "README.md").read_text(encoding="utf-8")
        self.assertIn("internal build support only", text)
        self.assertIn(handoff.REDISTRIBUTION_STATUS, text)
        fields = set(pq.ParquetFile(PACKAGE / "data/factor_library_2026.parquet").schema_arrow.names)
        fields |= set(pq.ParquetFile(PACKAGE / "data/market_2026.parquet").schema_arrow.names)
        self.assertFalse(fields & handoff.FORBIDDEN_COLUMNS)

    def test_07_audit_validation_is_all_pass(self) -> None:
        rows = csv_rows(ROOT / handoff.AUDIT / "recent_extension_2026_handoff_validation.csv")
        self.assertTrue(rows)
        self.assertTrue(all(row["status"] == "PASS" for row in rows))

    def test_08_benchmark_interface_contract_is_exact(self) -> None:
        path = PACKAGE / "data/benchmark_daily_returns_2026.parquet"
        self.assertTrue(path.is_file())
        self.assertEqual(pq.ParquetFile(path).schema_arrow, handoff.BENCHMARK_SCHEMA)
        rows = pq.read_table(path).to_pylist()
        self.assertEqual(len(rows), handoff.EXPECTED_BENCHMARK_ROWS)
        self.assertEqual(len({row["date"] for row in rows}), len(rows))
        self.assertEqual(min(row["date"] for row in rows), handoff.EXPECTED_BENCHMARK_START)
        self.assertEqual(max(row["date"] for row in rows), handoff.EXPECTED_MARKET_END)
        self.assertEqual(
            {(row["gvkeyx"], row["index_code"], row["benchmark_name"]) for row in rows},
            {(handoff.BENCHMARK_GVKEYX, handoff.BENCHMARK_INDEX_CODE, handoff.BENCHMARK_NAME)},
        )
        self.assertEqual({row["data_lineage"] for row in rows}, {handoff.LINEAGE})
        self.assertEqual({row["return_method"] for row in rows}, {handoff.BENCHMARK_RETURN_METHOD})
        self.assertEqual({row["source_field"] for row in rows}, {handoff.BENCHMARK_SOURCE_FIELD})
        self.assertTrue(all(row["return_available"] == 1 and row["benchmark_return"] is not None and math.isfinite(row["benchmark_return"]) for row in rows))
        self.assertTrue(all(row["benchmark_return"] == row["total_return"] for row in rows))

    def test_09_benchmark_matches_canonical_formula_and_source_is_immutable(self) -> None:
        source = handoff.source_path(ROOT, handoff.MARKET_STORE, TEST_CANONICAL_ROOT)
        before = handoff.sha256(source)
        connection = handoff.sqlite_readonly(source)
        try:
            source_rows, source_metrics = handoff.benchmark_rows_from_sqlite(connection)
        finally:
            connection.close()
        exported = pq.read_table(PACKAGE / "data/benchmark_daily_returns_2026.parquet").to_pylist()
        self.assertTrue(source_metrics["formula_ok"])
        self.assertEqual(source_metrics["formula_pairs"], handoff.EXPECTED_BENCHMARK_ROWS - 1)
        self.assertEqual(exported, source_rows)
        with tempfile.TemporaryDirectory() as temporary:
            _, temporary_hash = handoff.write_parquet(
                Path(temporary) / "benchmark.parquet", handoff.BENCHMARK_SCHEMA, source_rows,
            )
        self.assertEqual(temporary_hash, handoff.sha256(PACKAGE / "data/benchmark_daily_returns_2026.parquet"))
        self.assertEqual(before, handoff.sha256(source))

    def test_10_existing_factor_and_market_payloads_remain_unchanged(self) -> None:
        self.assertEqual(
            handoff.sha256(PACKAGE / "data/factor_library_2026.parquet"),
            "d208dba173994ee4f1d26272c5dcf0ac9a731b070f6e61160049000e55f57ca2",
        )
        self.assertEqual(
            handoff.sha256(PACKAGE / "data/market_2026.parquet"),
            "3a8b4a90970a42b12edf8d42e90afaa9d4bf30c49605219344b4f361fb2f80c4",
        )


if __name__ == "__main__":
    unittest.main()
