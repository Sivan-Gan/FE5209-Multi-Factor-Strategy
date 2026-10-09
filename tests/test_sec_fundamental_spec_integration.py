"""Artifact checks for the DEV SEC fundamental-input specification audit."""

from __future__ import annotations

import csv
import json
import unittest

from src.data import sec_fundamental_spec as sec_spec


ROOT = sec_spec.project_root()


class TestSecFundamentalSpecificationArtifacts(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.paths = sec_spec.output_paths(ROOT)
        missing = [str(path) for path in cls.paths.values() if not path.is_file()]
        if missing:
            raise AssertionError(f"Run SEC specification audit before artifact checks: {missing}")
        with cls.paths["audit_json"].open("r", encoding="utf-8") as handle:
            cls.summary = json.load(handle)

    def test_full_sec_source_was_profiled_and_raw_integrity_passed(self) -> None:
        self.assertEqual(self.summary["archive_cik_count"], 497)
        self.assertEqual(self.summary["raw_observation_count"], 12_709_510)
        manifest = self.summary["post_scan_raw_manifest"]
        self.assertTrue(manifest["passed"], manifest)
        self.assertEqual(manifest["verified_payload_files"], 508)

    def test_required_concepts_and_actual_candidates_are_present(self) -> None:
        with self.paths["tag_coverage"].open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        concepts = {row["economic_concept"] for row in rows}
        self.assertTrue(
            {"net_income", "common_equity", "operating_cash_flow", "capital_expenditure", "total_assets", "gross_profit", "revenue", "total_debt_support"}.issubset(concepts)
        )
        expected = {
            ("net_income", "NetIncomeLoss"),
            ("common_equity", "StockholdersEquity"),
            ("operating_cash_flow", "NetCashProvidedByUsedInOperatingActivities"),
            ("capital_expenditure", "PaymentsToAcquirePropertyPlantAndEquipment"),
            ("total_assets", "Assets"),
        }
        observed = {(row["economic_concept"], row["xbrl_tag"]) for row in rows}
        self.assertTrue(expected.issubset(observed))
        self.assertTrue(all(row["evidence_stage"] == "DEV_CURRENT_UNIVERSE" for row in rows))

    def test_forms_units_periods_and_duplicates_remain_auditable(self) -> None:
        with self.paths["form_audit"].open("r", newline="", encoding="utf-8") as handle:
            forms = {row["form"]: row for row in csv.DictReader(handle)}
        self.assertEqual(len(forms), 32)
        self.assertEqual(forms["10-K"]["proposal_status"], "conditional_proposal")
        self.assertEqual(forms["10-Q"]["proposal_status"], "conditional_proposal")
        self.assertEqual(forms["8-K"]["proposal_status"], "OPEN_DECISION")
        with self.paths["unit_audit"].open("r", newline="", encoding="utf-8") as handle:
            units = list(csv.DictReader(handle))
        self.assertTrue(any(row["unit_status"] == "UNRESOLVED_UNIT" for row in units))
        with self.paths["duplicate_audit"].open("r", newline="", encoding="utf-8") as handle:
            duplicates = list(csv.DictReader(handle))
        self.assertTrue(any(int(row["duplicate_period_groups"]) > 0 for row in duplicates))
        self.assertTrue(all("filed<=signal" in row["pit_selector_proposal"] for row in duplicates))


if __name__ == "__main__":
    unittest.main()
