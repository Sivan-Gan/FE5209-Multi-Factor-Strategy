"""Governance and delivery checks for the frozen 20-specification archive."""
from __future__ import annotations

import csv
import subprocess
import sys
import unittest
from pathlib import Path

import pyarrow.parquet as pq

from src.handoff import load_factor_research_archive as loader
from src.handoff import prepare_factor_research_archive as archive


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / archive.PACKAGE
DATA = PACKAGE / "data"
AUDIT = ROOT / archive.AUDIT


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class FactorResearchArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = rows(PACKAGE / "all_factor_spec_registry.csv")
        cls.acceptance = {row["check"]: row for row in rows(PACKAGE / "factor_archive_acceptance.csv")}
        cls.delivery_acceptance = {row["check"]: row for row in rows(AUDIT / "factor_archive_github_data_acceptance.csv")}
        cls.reproduction = rows(PACKAGE / "factor_archive_reproduction_qa.csv")
        cls.determinism = rows(PACKAGE / "factor_archive_determinism.csv")

    def test_01_exact_formal_archive_universe_and_family_counts(self) -> None:
        self.assertEqual(tuple(row["specification_id"] for row in self.registry), archive.SPEC_IDS)
        self.assertEqual(len(self.registry), 20)
        for check in ("FORMAL_SPEC_COUNT_20", "EXACT_SPEC_IDS", "VALUE_SPEC_COUNT_5", "QUALITY_SPEC_COUNT_6", "MOMENTUM_SPEC_COUNT_3", "LOWRISK_SPEC_COUNT_3", "INVESTMENT_SPEC_COUNT_1", "DIVIDEND_SPEC_COUNT_1", "SIZE_SPEC_COUNT_1"):
            self.assertEqual(self.acceptance[check]["status"], "PASS")

    def test_02_final_active_boundary_is_exact_k4(self) -> None:
        active = tuple(row["specification_id"] for row in self.registry if row["final_active"] == "TRUE")
        self.assertEqual(active, archive.FINAL_IDS)
        self.assertTrue(all(row["eligible_for_base_strategy"] == row["eligible_for_fmom_universe"] == row["final_active"] for row in self.registry))
        self.assertTrue(all(self.acceptance[check]["status"] == "PASS" for check in ("FINAL_ACTIVE_COUNT_4", "QUALITY_CURRENT_FINAL_ACTIVE", "MOMENTUM_12_1_FINAL_ACTIVE", "LOWRISK_CURRENT_FINAL_ACTIVE", "INVESTMENT_CURRENT_FINAL_ACTIVE")))

    def test_03_inactive_and_diagnostic_specifications_cannot_be_strategy_factors(self) -> None:
        inactive = {row["specification_id"] for row in self.registry if row["final_active"] == "FALSE"}
        self.assertTrue({"MOMENTUM_CURRENT", "VALUE_CURRENT", "DIVIDEND_CURRENT", "SIZE_CURRENT", *archive.DIAGNOSTICS}.issubset(inactive))
        self.assertTrue(all(row["eligible_for_base_strategy"] == "FALSE" for row in self.registry if row["specification_id"] in inactive))
        self.assertTrue(all(self.acceptance[check]["status"] == "PASS" for check in ("MOMENTUM_CURRENT_NOT_ACTIVE", "VALUE_CURRENT_NOT_ACTIVE", "DIVIDEND_CURRENT_NOT_ACTIVE", "SIZE_CURRENT_NOT_ACTIVE", "DIAGNOSTIC_COMPONENTS_NOT_ACTIVE", "NO_ROUND2_FACTORS")))

    def test_04_gate_a_decision_is_not_misattributed_to_a_challenger(self) -> None:
        for row in self.registry:
            if row["gate_a_representative"] == "FALSE":
                self.assertEqual(row["gate_a_decision"], "NOT_GATE_A_REPRESENTATIVE")

    def test_05_score_and_availability_schema_and_natural_key_are_governed(self) -> None:
        scores = pq.ParquetFile(DATA / "all_factor_scores_weekly.parquet")
        availability = pq.ParquetFile(DATA / "all_factor_availability_weekly.parquet")
        self.assertTrue({"signal_date", "PERMNO", "PERMCO", "GVKEY", *archive.SPEC_IDS}.issubset(scores.schema_arrow.names))
        self.assertTrue({f"{specification}__available" for specification in archive.SPEC_IDS}.issubset(availability.schema_arrow.names))
        self.assertTrue({f"{specification}__unavailable_reason" for specification in archive.SPEC_IDS}.issubset(availability.schema_arrow.names))
        self.assertEqual(scores.metadata.num_rows, 523_909)
        self.assertEqual(self.acceptance["NATURAL_KEY_UNIQUE"]["status"], "PASS")

    def test_06_no_2026_or_performance_or_strategy_columns(self) -> None:
        names = {name.lower() for name in pq.ParquetFile(DATA / "all_factor_scores_weekly.parquet").schema_arrow.names}
        self.assertFalse(any(token in name for name in names for token in ("future_return", "factor_return", "fmom", "strategy_weight", "portfolio")))
        self.assertTrue(all(self.acceptance[check]["status"] == "PASS" for check in ("NO_2026_SCORE_ROWS", "NO_FUTURE_RETURN_COLUMNS", "NO_FACTOR_RETURN_COLUMNS", "NO_FMOM_COLUMNS", "NO_STRATEGY_WEIGHT_COLUMNS")))

    def test_07_all_twenty_scores_and_availability_reproduce_exactly(self) -> None:
        self.assertEqual(tuple(row["specification_id"] for row in self.reproduction), archive.SPEC_IDS)
        for row in self.reproduction:
            self.assertEqual((row["max_abs_difference"], row["mean_abs_difference"], row["score_mismatch_count"], row["availability_mismatch_count"], row["unavailable_reason_mismatch_count"], row["natural_key_mismatch_count"], row["status"]), ("0.0", "0.0", "0", "0", "0", "0", "PASS"))

    def test_08_archive_export_is_deterministic_and_sample_is_pre2016(self) -> None:
        self.assertEqual({row["equal"] for row in self.determinism}, {"true"})
        self.assertTrue(all(row["key_difference_count"] == row["value_difference_count"] == "0" for row in self.determinism))
        sample = rows(DATA / "factor_scores_weekly_sample.csv")
        self.assertEqual(len(sample), 30)
        self.assertTrue(all(row["signal_date"] < "2016-01-01" for row in sample))
        self.assertTrue(set(archive.SPEC_IDS).issubset(sample[0]))

    def test_09_final_and_archive_loaders_are_explicitly_separate(self) -> None:
        final = loader.load_final_factor_scores(ROOT)
        research = loader.load_factor_research_archive(ROOT)
        self.assertTrue({"quality_score", "momentum_score", "lowrisk_score", "investment_score"}.issubset(final.column_names))
        self.assertFalse(set(archive.SPEC_IDS) & set(final.column_names))
        self.assertTrue(set(archive.SPEC_IDS).issubset(research.column_names))

    def test_10_final_factor_interface_and_upstream_inputs_remain_unchanged(self) -> None:
        immutability = rows(PACKAGE / "upstream_immutability_qa.csv")
        self.assertTrue(all(row["status"] == "PASS" for row in immutability))
        self.assertEqual(self.acceptance["FINAL_FACTOR_INTERFACE_UNCHANGED"]["status"], "PASS")

    def test_11_lfs_is_narrowly_configured_and_all_approved_pointers_are_valid(self) -> None:
        for check in ("GIT_LFS_AVAILABLE", "GIT_LFS_NARROW_TRACKING", "APPROVED_DERIVED_FILE_COUNT_20", "FINAL_FACTOR_DATA_LFS", "FACTOR_ARCHIVE_DATA_LFS", "US_DOWNSTREAM_DATA_LFS", "LFS_POINTERS_ALL_20", "LFS_POINTER_METADATA_MATCH", "UNAPPROVED_LFS_FILES_ZERO"):
            self.assertEqual(self.delivery_acceptance[check]["status"], "PASS")
        receiver = self.delivery_acceptance["FRESH_CLONE_LFS_TEST"]
        self.assertIn(receiver["status"], {"PASS", "BLOCKED"})
        if receiver["status"] == "BLOCKED":
            self.assertIn("pending", receiver["details"].lower())
        pointer_rows = rows(AUDIT / "github_lfs_pointer_validation.csv")
        self.assertEqual(len(pointer_rows), 20)
        self.assertTrue(all(row["status"] == "PASS" for row in pointer_rows))
        attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
        rules = [line for line in attrs if line and not line.startswith("#")]
        self.assertEqual(len(rules), 20)
        self.assertFalse(any("*.parquet" in line or "/data/ filter=" in line for line in rules))
        ignored = subprocess.run(("git", "check-ignore", "--no-index", "-q", "handoff/factor_research_archive/data/all_factor_scores_weekly.parquet"), cwd=ROOT)
        self.assertEqual(ignored.returncode, 0)

    def test_12_delivery_manifest_records_all_approved_parquets_as_lfs_tracked(self) -> None:
        manifest = rows(ROOT / "GITHUB_DELIVERY_MANIFEST.csv")
        derived = [row for row in manifest if row["category"] == "DERIVED_HANDOFF_DATA"]
        self.assertEqual(len(derived), 20)
        self.assertEqual({row["tracked"] for row in derived}, {"true"})
        self.assertEqual({row["Git_storage"] for row in derived}, {"GIT_LFS"})
        self.assertEqual({row["approved_for_team_repo"] for row in derived}, {"true"})
        self.assertEqual({row["source_vendor_derived"] for row in derived}, {"true"})
        self.assertEqual({row["private_team_only"] for row in derived}, {"true"})
        transfer = rows(ROOT / "GITHUB_DERIVED_DATA_TRANSFER_MANIFEST.csv")
        self.assertEqual(len(transfer), 20)
        self.assertEqual({row["status"] for row in transfer}, {"DERIVED_DATA_PRIVATE_TEAM_GITHUB_APPROVED__GIT_LFS_TRACKED"})

    def test_13_raw_leak_secret_and_no_gate_b_checks_pass(self) -> None:
        self.assertTrue(all(row["status"] == "PASS" for row in rows(AUDIT / "github_raw_data_leak_audit.csv")))
        self.assertFalse(any(row["confirmed_secret"] == "true" for row in rows(AUDIT / "github_complete_delivery_secret_audit.csv")))
        for check in ("NO_GATE_B", "NO_NEW_PERFORMANCE_RESEARCH", "NO_FMOM", "NO_STRATEGY_BACKTEST", "UPSTREAM_IMMUTABILITY"):
            self.assertEqual(self.delivery_acceptance[check]["status"], "PASS")

    def test_14_receiver_smoke_reports_the_three_delivery_boundaries(self) -> None:
        completed = subprocess.run((sys.executable, "-B", "scripts/smoke_test_complete_delivery.py"), cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("STRATEGY_ACTIVE_FACTORS=4", completed.stdout)
        self.assertIn("FORMAL_ARCHIVED_FACTOR_SPECS=20", completed.stdout)
        self.assertIn("DOWNSTREAM_US_DATA=READY", completed.stdout)


if __name__ == "__main__":
    unittest.main()
