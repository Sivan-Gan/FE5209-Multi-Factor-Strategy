"""Focused non-decisional checks for the Gate-A human evidence package."""
from __future__ import annotations

import csv
import unittest
from pathlib import Path

from src.validation import build_formal_factor_validation as validation
from src.validation import build_gate_a_human_review as review


ROOT = review.project_root()
AUDIT = ROOT / review.AUDIT


def rows(name: str) -> list[dict[str, str]]:
    with (AUDIT / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class GateAHumanReviewTests(unittest.TestCase):
    def test_01_fixed_factor_order(self) -> None:
        self.assertEqual(review.FACTORS, ("Size", "Momentum", "Low Risk", "Value", "Quality", "Investment", "Dividend"))

    def test_02_yearly_ic_is_2011_2015_only(self) -> None:
        self.assertEqual({int(row["year"]) for row in rows("gate_a_yearly_ic_review.csv")}, set(range(2011, 2016)))

    def test_03_rolling_ic_never_crosses_2015(self) -> None:
        source = rows("factor_validation_ic_stability.csv")
        self.assertFalse(any(row["record_type"] == "ROLLING_52W" and row["period_end"] > review.END for row in source if row["sample_role"] == review.PRIMARY))

    def test_04_yearly_portfolio_is_2011_2015_only(self) -> None:
        self.assertEqual({int(row["year"]) for row in rows("gate_a_yearly_portfolio_review.csv")}, set(range(2011, 2016)))

    def test_05_score_matrix_is_symmetric(self) -> None:
        matrix = {row["factor"]: row for row in rows("gate_a_score_correlation_matrix.csv")}
        for left in review.FACTORS:
            for right in review.FACTORS:
                self.assertAlmostEqual(float(matrix[left][right]), float(matrix[right][left]))

    def test_06_score_matrix_diagonal_is_one(self) -> None:
        for row in rows("gate_a_score_correlation_matrix.csv"):
            self.assertEqual(float(row[row["factor"]]), 1.0)

    def test_07_return_matrix_is_symmetric(self) -> None:
        matrix = {row["factor"]: row for row in rows("gate_a_spread_return_correlation_matrix.csv")}
        for left in review.FACTORS:
            for right in review.FACTORS:
                self.assertAlmostEqual(float(matrix[left][right]), float(matrix[right][left]))

    def test_08_return_matrix_diagonal_is_one(self) -> None:
        for row in rows("gate_a_spread_return_correlation_matrix.csv"):
            self.assertEqual(float(row[row["factor"]]), 1.0)

    def test_09_pairwise_details_have_21_pairs(self) -> None:
        self.assertEqual(len(rows("gate_a_score_correlation_detail.csv")), 21)
        self.assertEqual(len(rows("gate_a_spread_return_correlation_detail.csv")), 21)

    def test_10_sector_breadth_declares_historical_gics(self) -> None:
        self.assertEqual({row["gics_basis"] for row in rows("gate_a_sector_breadth_review.csv")}, {"historical_gics_at_signal_date"})

    def test_11_primary_and_common_remain_distinct(self) -> None:
        source = rows("gate_a_common_sample_robustness.csv")
        self.assertTrue(all(row["sample_role_primary"] == review.PRIMARY and row["sample_role_common"] == review.COMMON for row in source))

    def test_12_dev_context_is_not_gate_a_evidence(self) -> None:
        self.assertEqual({row["sample_role"] for row in rows("gate_a_yearly_ic_dev_context.csv")}, {review.DEV})

    def test_13_master_has_no_rank_field(self) -> None:
        self.assertFalse(any("rank" in key.lower() for key in rows("gate_a_human_review_master.csv")[0]))

    def test_14_master_has_no_keep_field(self) -> None:
        self.assertFalse(any("keep" in key.lower() for key in rows("gate_a_human_review_master.csv")[0]))

    def test_15_master_has_no_drop_field(self) -> None:
        self.assertFalse(any("drop" in key.lower() for key in rows("gate_a_human_review_master.csv")[0]))

    def test_16_no_selection_threshold_logic(self) -> None:
        source = Path(review.__file__).read_text(encoding="utf-8")
        self.assertNotIn("mean_ic >", source)

    def test_17_no_weighted_evidence_score(self) -> None:
        source = Path(review.__file__).read_text(encoding="utf-8").lower()
        self.assertNotIn("weighted evidence", source)

    def test_18_post_2015_firewall_is_zero(self) -> None:
        firewall = rows("gate_a_review_post2015_firewall_qa.csv")[0]
        self.assertTrue(all(int(value) == 0 for key, value in firewall.items() if key not in {"status", "evidence"}))

    def test_19_no_2016_crossing_rolling_window(self) -> None:
        self.assertEqual(int(rows("gate_a_review_post2015_firewall_qa.csv")[0]["rolling_validation_windows_crossing_2016"]), 0)

    def test_20_validation_database_is_unchanged(self) -> None:
        self.assertEqual(validation.validation_hash(ROOT / review.VALIDATION), review.VALIDATION_HASH)

    def test_21_factor_store_is_unchanged(self) -> None:
        self.assertEqual(validation.input_freeze(ROOT)["factor_hash"], validation.FACTOR_HASH)

    def test_22_canonical_dataset_is_unchanged(self) -> None:
        self.assertEqual(validation.input_freeze(ROOT)["canonical_hash"], validation.CANONICAL_HASH)

    def test_23_raw_data_is_unchanged(self) -> None:
        self.assertTrue(validation.gate0.verify_raw_integrity(ROOT)["raw_hashes_match"])

    def test_24_deterministic_rebuild_recorded(self) -> None:
        row = rows("gate_a_human_review_determinism.csv")[0]
        self.assertEqual(row["status"], "PASS")
        self.assertEqual(row["run_1_logical_hash"], row["run_2_logical_hash"])

    def test_25_acceptance_includes_determinism_and_no_blocker(self) -> None:
        acceptance = rows("gate_a_human_review_acceptance.csv")
        by_check = {row["check"]: row for row in acceptance}
        self.assertIn("DETERMINISM", by_check)
        self.assertEqual(by_check["DETERMINISM"]["status"], "PASS")
        self.assertFalse(any(row["blocking"] == "TRUE" for row in acceptance))

    def test_26_contamination_register_has_one_gate_a_entry(self) -> None:
        register = rows("oos_contamination_register.csv")
        entries = [row for row in register if row["stage"] == "FORMAL_GATE_A_HUMAN_EVIDENCE_REVIEW_PRE_DECISION"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["post2015_returns_used_for_factor_choice"], "FALSE")


if __name__ == "__main__":
    unittest.main()
