"""Artifact checks for the DEV SEC fundamental design-review audit."""

from __future__ import annotations

import csv
import json
import unittest

from src.data import sec_fundamental_design_review as review


ROOT = review.project_root()


class TestSecFundamentalDesignReviewArtifacts(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.paths = review.output_paths(ROOT)
        missing = [str(path) for path in cls.paths.values() if not path.is_file()]
        if missing:
            raise AssertionError(f"Run SEC design review before artifact checks: {missing}")
        with cls.paths["audit_json"].open("r", encoding="utf-8") as handle:
            cls.summary = json.load(handle)

    def test_raw_manifest_and_scope_are_preserved(self) -> None:
        manifest = self.summary["post_scan_raw_manifest"]
        self.assertTrue(manifest["passed"], manifest)
        self.assertEqual(manifest["verified_payload_files"], 508)
        prohibited = set(self.summary["prohibited_outputs_not_created"])
        self.assertIn("canonical PIT fundamental panel", prohibited)
        self.assertIn("FCF Yield or historical market capitalization", prohibited)

    def test_gross_profit_and_fcf_review_evidence_is_present(self) -> None:
        with self.paths["gross_profit"].open("r", newline="", encoding="utf-8") as handle:
            gross_rows = list(csv.DictReader(handle))
        self.assertEqual(len(gross_rows), 5)
        candidate = next(
            row for row in gross_rows
            if row["pair_id"] == "sales_revenue_net_less_cost_of_goods_and_services_sold"
        )
        self.assertGreater(int(candidate["overlap_period_count"]), 0)
        self.assertEqual(candidate["semantic_status"], "conditional_fallback_candidate")
        with self.paths["fcf"].open("r", newline="", encoding="utf-8") as handle:
            fcf = next(csv.DictReader(handle))
        self.assertGreater(int(fcf["compatible_ocf_capex_period_count"]), 0)
        self.assertGreater(int(fcf["capex_positive_count"]), int(fcf["capex_negative_count"]))
        self.assertIn("no FCF calculation persisted", fcf["audit_scope"])

    def test_foreign_forms_conflicts_and_signoffs_remain_explicit(self) -> None:
        with self.paths["foreign_forms"].open("r", newline="", encoding="utf-8") as handle:
            foreign_rows = list(csv.DictReader(handle))
        gross_20f = next(
            row for row in foreign_rows
            if row["foreign_form_family"] == "20-F" and row["economic_concept"] == "gross_profit"
        )
        self.assertEqual(int(gross_20f["issuer_count_with_foreign_form"]), 2)
        self.assertIn("CRH", gross_20f["issuer_tickers_with_foreign_form"])
        self.assertIn("NXPI", gross_20f["issuer_tickers_with_foreign_form"])
        self.assertTrue(all(
            int(row["issuer_count_with_foreign_form"]) == 0
            for row in foreign_rows if row["foreign_form_family"] == "40-F"
        ))
        conflict = self.summary["same_filed_conflict_summary"]
        self.assertGreater(conflict["same_tag_conflicting_group_count"], 0)
        self.assertLess(conflict["same_tag_conflict_share"], 0.001)
        with self.paths["signoff"].open("r", newline="", encoding="utf-8") as handle:
            signoffs = list(csv.DictReader(handle))
        self.assertEqual(len(signoffs), 10)
        self.assertTrue(all(row["recommendation"] == "RECOMMEND_SIGNOFF" for row in signoffs))


if __name__ == "__main__":
    unittest.main()
