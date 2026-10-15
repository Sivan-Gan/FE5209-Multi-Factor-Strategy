"""Focused tests for the isolated 2025--2026 execution bridge."""
from __future__ import annotations

import csv
import os
import unittest
from pathlib import Path

import pyarrow.parquet as pq

from src.data import wrds_recent_extension_canonical as extension
from src.handoff import prepare_us_cross_boundary_2025_2026_bridge as bridge
from src.handoff import validate_us_cross_boundary_2025_2026_delivery as delivery


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT_VALUE = os.environ.get("FE5209_CROSS_BOUNDARY_SOURCE_ROOT")
PACKAGE = ROOT / bridge.PACKAGE


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@unittest.skipUnless(SOURCE_ROOT_VALUE, "set FE5209_CROSS_BOUNDARY_SOURCE_ROOT to a local source fixture")
class CrossBoundaryExecutionBridgeTests(unittest.TestCase):
    @property
    def source_root(self) -> Path:
        return Path(str(SOURCE_ROOT_VALUE))

    def test_01_frozen_gap_and_bridge_calendar_are_exact(self) -> None:
        frozen, recent = bridge.frozen_gap(ROOT)
        self.assertEqual(frozen["execution_date"], bridge.EXECUTION_DATE)
        self.assertEqual(frozen["next_execution_date"], "")
        self.assertEqual(frozen["return_period_complete"], 0)
        self.assertEqual(frozen["calendar_status"], "NO_NEXT_SIGNAL_WITHIN_FORMAL_CUTOFF")
        self.assertEqual(recent, {(bridge.NEXT_SIGNAL_DATE, bridge.NEXT_EXECUTION_DATE)})
        actual = pq.read_table(PACKAGE / "data/weekly_execution_calendar_bridge.parquet").to_pylist()
        self.assertEqual(actual, [{
            "signal_date": bridge.SIGNAL_DATE, "execution_date": bridge.EXECUTION_DATE,
            "next_signal_date": bridge.NEXT_SIGNAL_DATE, "next_execution_date": bridge.NEXT_EXECUTION_DATE,
            "return_period_complete": 1, "calendar_status": "CROSS_BOUNDARY_BRIDGE_COMPLETE",
            "data_lineage": bridge.LINEAGE,
        }])

    def test_02_identity_mapping_is_historical_and_complete(self) -> None:
        universe = bridge.requested_universe(ROOT)
        mapping = rows(PACKAGE / "identity_mapping_audit.csv")
        self.assertEqual(len(universe), 503)
        self.assertEqual(len(mapping), len(universe))
        self.assertEqual({row["mapping_status"] for row in mapping}, {"EXACT_APPROVED_IDENTITY"})
        self.assertTrue(all(row["mapping_method"].startswith("HISTORICAL_CCM_LIID_EXACT") for row in mapping))
        self.assertTrue(all("TICKER" not in row["mapping_method"] for row in mapping))
        self.assertEqual(len({row["PERMNO"] for row in mapping}), len(mapping))

    def test_03_security_contract_has_one_explicit_record_per_requested_security(self) -> None:
        expected = bridge.requested_universe(ROOT)
        actual = pq.read_table(PACKAGE / "data/security_weekly_execution_returns_bridge.parquet").to_pylist()
        self.assertEqual(pq.ParquetFile(PACKAGE / "data/security_weekly_execution_returns_bridge.parquet").schema_arrow, bridge.SECURITY_SCHEMA)
        self.assertEqual(len(actual), len(expected))
        self.assertEqual(len({(row["signal_date"], row["PERMNO"]) for row in actual}), len(actual))
        self.assertTrue(all(row["signal_date"] == bridge.SIGNAL_DATE and row["execution_date"] == bridge.EXECUTION_DATE and row["next_execution_date"] == bridge.NEXT_EXECUTION_DATE for row in actual))
        self.assertTrue(all(row["trading_day_count"] == 4 and row["path_complete"] == 1 for row in actual))
        self.assertTrue(all(row["return_available"] == 1 and row["weekly_total_return"] is not None and row["unavailable_reason"] == "AVAILABLE" for row in actual))
        self.assertNotIn(bridge.EXECUTION_DATE, bridge.HOLDING_DATES)
        self.assertIn(bridge.NEXT_EXECUTION_DATE, bridge.HOLDING_DATES)

    def test_04_security_formula_and_frozen_trfd_policy_match_source(self) -> None:
        source = bridge.source_root_path(ROOT, self.source_root)
        universe = bridge.requested_universe(ROOT)
        identities = bridge.build_identity_rows(universe, bridge.mapping_audit(source))
        authorized, ciena, _unvalidated = extension._load_authorized_identity_sets(source)
        expected, recovery_used = bridge.security_return_rows(universe, identities, bridge.security_index(source / bridge.SECURITY_SOURCE), authorized, ciena)
        actual = pq.read_table(PACKAGE / "data/security_weekly_execution_returns_bridge.parquet").to_pylist()
        self.assertEqual(actual, expected)
        self.assertTrue(recovery_used <= (authorized | ciena))
        self.assertTrue(all(row["return_source"] in {extension.RETURN_DIRECT, extension.RETURN_RECOVERY} for row in actual))

    def test_05_benchmark_and_risk_free_formulas_match_sources(self) -> None:
        source = bridge.source_root_path(ROOT, self.source_root)
        expected_benchmark, benchmark_ok = bridge.benchmark_row(source / bridge.BENCHMARK_SOURCE)
        expected_rf, rf_ok = bridge.risk_free_row(source / bridge.RF_SOURCE)
        actual_benchmark = pq.read_table(PACKAGE / "data/benchmark_weekly_execution_return_bridge.parquet").to_pylist()
        actual_rf = pq.read_table(PACKAGE / "data/risk_free_weekly_return_bridge.parquet").to_pylist()
        self.assertTrue(benchmark_ok)
        self.assertTrue(rf_ok)
        self.assertEqual(actual_benchmark, [expected_benchmark])
        self.assertEqual(actual_rf, [expected_rf])
        self.assertEqual(expected_benchmark["benchmark_id"], bridge.BENCHMARK_ID)
        self.assertEqual(expected_benchmark["return_method"], "COMPUSTAT_PRCCDDIV_TOTAL_RETURN")
        self.assertEqual(expected_rf["return_method"], "FAMA_FRENCH_DAILY_RF_DECIMAL_COMPOUND")

    def test_06_acceptance_and_schema_exclusions_pass(self) -> None:
        acceptance = rows(PACKAGE / "acceptance.csv")
        expected = delivery.validate(ROOT)["acceptance"]
        self.assertEqual(acceptance, expected)
        self.assertEqual(len(acceptance), 22)
        self.assertTrue(all(row["status"] == "PASS" for row in acceptance))
        fields = set(bridge.SECURITY_SCHEMA.names) | set(bridge.BENCHMARK_SCHEMA.names) | set(bridge.RF_SCHEMA.names)
        self.assertFalse(fields & bridge.FORBIDDEN_COLUMNS)

    def test_07_rebuild_is_deterministic_and_sources_remain_immutable(self) -> None:
        source = bridge.source_root_path(ROOT, self.source_root)
        before = bridge.source_snapshot(ROOT, source)
        first = bridge.build(ROOT, self.source_root)
        second = bridge.build(ROOT, self.source_root)
        self.assertEqual(before, bridge.source_snapshot(ROOT, source))
        for field in ("calendar_sha256", "security_sha256", "benchmark_sha256", "risk_free_sha256"):
            self.assertEqual(first[field], second[field])
        self.assertEqual(first["blockers"], 0)
        self.assertEqual(len(delivery.validate(ROOT)["acceptance"]), 22)


if __name__ == "__main__":
    unittest.main()
