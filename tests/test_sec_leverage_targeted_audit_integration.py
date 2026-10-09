from __future__ import annotations

import csv
import json
from pathlib import Path
import unittest

from src.data import sec_leverage_targeted_audit as audit


ROOT = Path(__file__).resolve().parents[1]


class TestSecLeverageTargetedAuditArtifacts(unittest.TestCase):
    def test_outputs_are_present_and_manifest_passes(self) -> None:
        paths = audit.output_paths(ROOT)
        for path in paths.values():
            self.assertTrue(path.is_file(), path)
        summary = json.loads(paths["audit_json"].read_text(encoding="utf-8"))
        self.assertEqual(summary["evidence_stage"], "DEV_CURRENT_UNIVERSE")
        self.assertTrue(summary["post_scan_raw_manifest"]["passed"])
        self.assertEqual(summary["recommended_baseline"], "liabilities_over_assets")

    def test_definition_and_joint_availability_rows_preserve_scope(self) -> None:
        paths = audit.output_paths(ROOT)
        with paths["definition_audit"].open(newline="", encoding="utf-8") as handle:
            definitions = list(csv.DictReader(handle))
        baseline = next(
            row
            for row in definitions
            if row["definition_id"] == "liabilities_over_assets"
            and row["group"] == "ordinary_non_financial_ex_real_estate_current_gics"
        )
        self.assertEqual(baseline["recommendation_role"], "RECOMMENDED_BASELINE")
        self.assertEqual(baseline["numerator_tags"], "Liabilities")
        self.assertEqual(baseline["denominator_tag"], "Assets")
        self.assertGreater(int(baseline["same_filing_period_count"]), 0)

        with paths["quality_availability"].open(newline="", encoding="utf-8") as handle:
            availability = list(csv.DictReader(handle))
        required = {
            "roe_end_aligned_input",
            "exact_gpa_input",
            "leverage_liabilities_over_assets",
            "at_least_two_of_three_with_liabilities_over_assets",
        }
        observed = {
            row["metric_id"]
            for row in availability
            if row["group"] == "non_financial_current_gics"
        }
        self.assertTrue(required.issubset(observed))
        self.assertTrue(all(row["evidence_stage"] == "DEV_CURRENT_UNIVERSE" for row in definitions))

    def test_signoff_register_contains_exactly_five_approved_decisions(self) -> None:
        path = audit.output_paths(ROOT)["signoff_register"]
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 5)
        self.assertEqual({row["status"] for row in rows}, {"APPROVED_DEV"})
        self.assertEqual(
            {row["decision_id"] for row in rows},
            {
                "gross_profit_baseline",
                "fcf_primary_inputs",
                "negative_capex",
                "same_latest_filed_conflict",
                "foreign_forms",
            },
        )


if __name__ == "__main__":
    unittest.main()
