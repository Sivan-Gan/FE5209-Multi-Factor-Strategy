"""Read-only delivery tests for the already-materialized cross-boundary bridge."""
from __future__ import annotations

import csv
import unittest
from pathlib import Path

from src.handoff import validate_us_cross_boundary_2025_2026_delivery as delivery


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / delivery.PACKAGE


class CrossBoundaryBridgeDeliveryTests(unittest.TestCase):
    def test_01_existing_bridge_passes_read_only_delivery_validation(self) -> None:
        result = delivery.validate(ROOT)
        self.assertEqual(result["blockers"], 0)
        self.assertEqual(len(result["acceptance"]), 22)
        self.assertTrue(all(row["status"] == "PASS" for row in result["acceptance"]))

    def test_02_manifest_covers_each_delivered_artifact_except_itself(self) -> None:
        with (PACKAGE / "handoff_manifest.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 7)
        self.assertEqual({row["redistribution_status"] for row in rows}, {delivery.REDISTRIBUTION})
        self.assertNotIn("handoff_manifest.csv", {row["relative_path"] for row in rows})
        self.assertTrue(all(len(row["sha256"]) == 64 for row in rows))

    def test_03_lfs_delivery_targets_are_exactly_the_four_bridge_parquets(self) -> None:
        expected = {
            "data/weekly_execution_calendar_bridge.parquet",
            "data/security_weekly_execution_returns_bridge.parquet",
            "data/benchmark_weekly_execution_return_bridge.parquet",
            "data/risk_free_weekly_return_bridge.parquet",
        }
        manifest = delivery.manifest_rows(ROOT)
        parquet = {row["relative_path"] for row in manifest if row["artifact_type"] == "parquet"}
        self.assertEqual(parquet, expected)


if __name__ == "__main__":
    unittest.main()
