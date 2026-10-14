"""Focused contract tests for the separate 2026 factor-library builder."""
from __future__ import annotations

import csv
import unittest
import sqlite3
from pathlib import Path

from src.factors import build_recent_extension_factor_library as library


ROOT = Path(__file__).resolve().parents[1]


class RecentExtensionFactorLibraryTests(unittest.TestCase):
    def test_registry_and_final_k4_are_frozen(self) -> None:
        rows = library.registry(ROOT)
        self.assertEqual(len(rows), 20)
        self.assertEqual(len({row["canonical_factor_name"] for row in rows}), 20)
        self.assertEqual(
            tuple(row["canonical_factor_name"] for row in rows if row["final_k4"] == "TRUE"),
            library.FINAL_K4,
        )

    def test_only_explicit_formal_permno_history_is_permitted(self) -> None:
        self.assertEqual(
            library.history_permno({
                "history_source": "FORMAL_CRSP_2025_PLUS_RECENT_EXTENSION_2026",
                "membership_security_id": "CRSP_PERMNO:18592",
            }),
            "18592",
        )
        self.assertIsNone(library.history_permno({
            "history_source": "RECENT_EXTENSION_ONLY",
            "membership_security_id": "CRSP_PERMNO:18592",
        }))
        self.assertIsNone(library.history_permno({
            "history_source": "FORMAL_CRSP_2025_PLUS_RECENT_EXTENSION_2026",
            "membership_security_id": "COMPUSTAT_GVKEY_IID:035168:01",
        }))

    def test_governed_unavailable_contract_is_exact(self) -> None:
        self.assertEqual(library.UNAVAILABLE, {
            "LOWRISK_IVOL": "CRSP_1000080_2026_SOURCE_UNAVAILABLE",
            "DIVIDEND_CURRENT": "CRSP_DISTRIBUTION_TAXONOMY_NOT_REPRODUCIBLE",
        })

    def test_schema_has_no_outcome_or_strategy_field(self) -> None:
        fields = {line.strip().split()[0] for line in library.SCHEMA.splitlines() if line.strip() and not line.lstrip().startswith(("CREATE", "PRIMARY"))}
        self.assertFalse(fields & library.FORBIDDEN_OUTPUT_COLUMNS)

    def test_calendar_contract_excludes_incomplete_october_week(self) -> None:
        self.assertEqual((library.EXPECTED_START, library.EXPECTED_END, library.EXPECTED_DATES), ("2026-01-02", "2026-10-02", 40))

    def test_completed_library_preserves_governed_missingness(self) -> None:
        path = ROOT / library.OUTPUT
        self.assertTrue(path.is_file(), "Run the authorized 2026 library builder before this integration check.")
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            cardinality = connection.execute(
                "SELECT COUNT(*),COUNT(DISTINCT signal_date),COUNT(DISTINCT factor_name),MIN(signal_date),MAX(signal_date) "
                "FROM factor_observations"
            ).fetchone()
            self.assertEqual(cardinality, (402420, 40, 20, "2026-01-02", "2026-10-02"))
            for name, reason in library.UNAVAILABLE.items():
                row = connection.execute(
                    "SELECT COUNT(*),SUM(raw_signal IS NOT NULL),SUM(processed_signal IS NOT NULL),"
                    "SUM(neutralized_signal IS NOT NULL),SUM(final_score IS NOT NULL),SUM(rank_or_percentile IS NOT NULL),"
                    "SUM(missing_reason=?) FROM factor_observations WHERE factor_name=?",
                    (reason, name),
                ).fetchone()
                self.assertEqual(row, (20121, 0, 0, 0, 0, 0, 20121))

    def test_registry_validation_uses_complete_security_date_grid(self) -> None:
        path = ROOT / "outputs/audit/wrds_2026_bridge/recent_extension_factor_library_registry_validation.csv"
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 20)
        self.assertEqual({row["status"] for row in rows}, {"PASS"})
        self.assertEqual({int(row["registry_factor_count"]) for row in rows}, {20})
        self.assertEqual({int(row["observed_factor_count"]) for row in rows}, {20})
        self.assertEqual({int(row["constructible_factor_count"]) for row in rows}, {18})
        self.assertEqual({int(row["signal_date_count"]) for row in rows}, {40})
        self.assertEqual({row["signal_date_start"] for row in rows}, {"2026-01-02"})
        self.assertEqual({row["signal_date_end"] for row in rows}, {"2026-10-02"})
        self.assertEqual({int(row["pit_violation_count"]) for row in rows}, {0})
        self.assertEqual({int(row["expected_observation_count"]) for row in rows}, {20_121})
        self.assertEqual({int(row["observation_count"]) for row in rows}, {20_121})
        unavailable = {row["factor_name"]: row for row in rows if row["governed_unavailable_reason"]}
        self.assertEqual(
            {name: row["governed_unavailable_reason"] for name, row in unavailable.items()},
            library.UNAVAILABLE,
        )
        self.assertEqual({row["unavailable_contract_status"] for row in unavailable.values()}, {"PASS"})


if __name__ == "__main__":
    unittest.main()
