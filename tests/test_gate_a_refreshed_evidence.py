"""Focused acceptance checks for the frozen-representative Gate A evidence package."""
from __future__ import annotations

import csv
import sqlite3
import unittest
from pathlib import Path

from src.validation import build_formal_factor_validation as validation
from src.validation import build_gate_a_refreshed_evidence as refreshed
from src.validation import build_gate_a0_within_family_validation as gate_a0


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / refreshed.AUDIT


def rows(name: str) -> list[dict[str, str]]:
    with (AUDIT / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class GateARefreshedEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.master = rows("gate_a_refreshed_human_review_master.csv")
        cls.by_family = {row["family"]: row for row in cls.master}
        cls.acceptance = {
            row["check"]: row for row in rows("gate_a_refreshed_evidence_acceptance.csv")
        }
        cls.connection = sqlite3.connect(
            f"file:{ROOT / refreshed.STORE}?mode=ro", uri=True
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def test_01_exactly_seven_frozen_representatives_are_used(self) -> None:
        self.assertEqual(len(self.master), 7)
        self.assertEqual(
            {row["family"]: row["representative_specification_id"] for row in self.master},
            refreshed.EXPECTED,
        )

    def test_02_gate_a0_decision_map_is_the_direct_input(self) -> None:
        _rows, selected = refreshed.decision_map(ROOT)
        self.assertEqual(selected, refreshed.EXPECTED)
        self.assertEqual(
            rows("gate_a0_family_representative_decision.csv")[2]["selected_specification_id"],
            "MOMENTUM_12_1",
        )

    def test_03_momentum_is_12_1_and_old_composite_is_excluded(self) -> None:
        self.assertEqual(
            self.by_family["Momentum"]["representative_specification_id"], "MOMENTUM_12_1"
        )
        self.assertNotIn("MOMENTUM_CURRENT", {row["representative_specification_id"] for row in self.master})

    def test_04_incumbents_and_passthrough_families_are_preserved(self) -> None:
        expected = {
            "Value": "VALUE_CURRENT",
            "Quality": "QUALITY_CURRENT",
            "Low Risk": "LOWRISK_CURRENT",
            "Investment": "INVESTMENT_CURRENT",
            "Dividend": "DIVIDEND_CURRENT",
            "Size": "SIZE_CURRENT",
        }
        for family, specification in expected.items():
            self.assertEqual(self.by_family[family]["representative_specification_id"], specification)

    def test_05_all_formal_observations_are_2011_2015_only(self) -> None:
        minimum, maximum, periods = self.connection.execute(
            "SELECT MIN(signal_date),MAX(signal_date),GROUP_CONCAT(DISTINCT research_period) FROM validation_observations"
        ).fetchone()
        self.assertEqual((minimum, maximum, periods), ("2011-01-07", "2015-12-31", "FACTOR_SELECTION"))
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM validation_observations WHERE signal_date > '2015-12-31'"
            ).fetchone()[0],
            0,
        )

    def test_06_post_2015_firewall_is_all_zero(self) -> None:
        firewall = rows("gate_a_refreshed_post2015_firewall_qa.csv")[0]
        self.assertTrue(all(value == "0" for value in firewall.values()))

    def test_07_common_seven_sample_requires_all_seven_scores(self) -> None:
        invalid = self.connection.execute(
            """SELECT COUNT(*) FROM representative_scores s
               JOIN validation_observations v USING(signal_date,permno)
               WHERE v.common7_available=1 AND s.score_final IS NULL"""
        ).fetchone()[0]
        self.assertEqual(invalid, 0)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(DISTINCT specification_id) FROM representative_scores"
            ).fetchone()[0],
            7,
        )

    def test_08_score_matrix_is_a_symmetric_seven_by_seven_fixed_order_matrix(self) -> None:
        matrix = {row["factor"]: row for row in rows("gate_a_refreshed_score_correlation_matrix.csv")}
        self.assertEqual(tuple(matrix), refreshed.FAMILIES)
        for left in refreshed.FAMILIES:
            self.assertEqual(float(matrix[left][left]), 1.0)
            for right in refreshed.FAMILIES:
                self.assertAlmostEqual(float(matrix[left][right]), float(matrix[right][left]))

    def test_09_ic_matrices_are_two_symmetric_seven_by_seven_matrices(self) -> None:
        source = rows("gate_a_refreshed_ic_correlation_matrix.csv")
        for horizon in ("1W", "4W"):
            matrix = {row["factor"]: row for row in source if row["horizon"] == horizon}
            self.assertEqual(tuple(matrix), refreshed.FAMILIES)
            for left in refreshed.FAMILIES:
                self.assertEqual(float(matrix[left][left]), 1.0)
                for right in refreshed.FAMILIES:
                    self.assertAlmostEqual(float(matrix[left][right]), float(matrix[right][left]))

    def test_10_spread_matrix_is_a_symmetric_seven_by_seven_matrix(self) -> None:
        matrix = {row["factor"]: row for row in rows("gate_a_refreshed_spread_correlation_matrix.csv")}
        self.assertEqual(tuple(matrix), refreshed.FAMILIES)
        for left in refreshed.FAMILIES:
            self.assertEqual(float(matrix[left][left]), 1.0)
            for right in refreshed.FAMILIES:
                self.assertAlmostEqual(float(matrix[left][right]), float(matrix[right][left]))

    def test_11_cross_family_detail_counts_are_complete(self) -> None:
        self.assertEqual(len(rows("gate_a_refreshed_score_correlation_detail.csv")), 21)
        self.assertEqual(len(rows("gate_a_refreshed_ic_correlation_detail.csv")), 42)
        self.assertEqual(len(rows("gate_a_refreshed_spread_correlation_detail.csv")), 21)
        self.assertEqual(len(rows("gate_a_refreshed_holdings_overlap.csv")), 42)

    def test_12_cross_family_bh_groups_have_exactly_seven_primary_hypotheses(self) -> None:
        summary = rows("gate_a_refreshed_ic_summary.csv")
        primary = [row for row in summary if row["sample_role"] == refreshed.PRIMARY]
        self.assertEqual(len(primary), 14)
        self.assertTrue(all(row["tested_hypotheses_count"] == "7" for row in primary))
        self.assertTrue(all(row["bh_fdr_q_value"] != "" for row in primary))

    def test_13_yearly_and_rolling_evidence_never_crosses_2015(self) -> None:
        yearly = rows("gate_a_refreshed_yearly_ic.csv")
        self.assertEqual(
            {row["year"] for row in yearly if row["year"] != "SUMMARY"},
            {str(year) for year in range(2011, 2016)},
        )
        rolling = rows("gate_a_refreshed_rolling_ic.csv")
        self.assertTrue(rolling)
        self.assertTrue(all("2011-01-01" <= row["period_end"] <= "2015-12-31" for row in rolling))

    def test_14_master_has_evidence_only_fields_without_an_automatic_outcome(self) -> None:
        forbidden = ("rank", "winner", "keep", "watch", "drop", "selected", "rejected", "final_factor")
        columns = {column.lower() for column in self.master[0]}
        self.assertFalse(any(word in column for word in forbidden for column in columns))
        self.assertFalse(any("COMPONENT" in row["representative_specification_id"] for row in self.master))

    def test_15_cards_are_non_decisional_and_complete(self) -> None:
        cards = AUDIT / "gate_a_refreshed_factor_cards"
        expected = {family.upper().replace(" ", "_") + ".md" for family in refreshed.FAMILIES}
        self.assertEqual({path.name for path in cards.glob("*.md")}, expected)
        forbidden = ("keep", "watch", "drop", "selected", "rejected", "final factor set")
        for path in cards.glob("*.md"):
            content = path.read_text(encoding="utf-8").lower()
            self.assertFalse(any(word in content for word in forbidden), path.name)

    def test_16_report_has_required_sections_and_momentum_disclosure(self) -> None:
        report = (AUDIT / "WRDS_GATE_A_REFRESHED_HUMAN_EVIDENCE_REVIEW.md").read_text(encoding="utf-8")
        self.assertTrue(all(f"## {index}." in report for index in range(1, 28)))
        self.assertIn("MOMENTUM_12_1", report)
        self.assertIn("not used in this package", report)

    def test_17_oos_register_has_one_correctly_firewalled_stage_entry(self) -> None:
        entries = [
            row for row in rows("oos_contamination_register.csv")
            if row["stage"] == "FORMAL_GATE_A_REFRESHED_HUMAN_EVIDENCE_PRE_DECISION"
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["performance_2016_2020"], "NOT_ACCESSED")
        self.assertEqual(entries[0]["performance_2021_2025"], "NOT_ACCESSED")
        self.assertEqual(entries[0]["post2015_returns_used_for_factor_choice"], "FALSE")
        self.assertIn("no Human Gate A Final Factor Set selection recorded", entries[0]["formal_gate_a_evidence"])

    def test_18_determinism_is_recorded(self) -> None:
        row = rows("gate_a_refreshed_evidence_determinism.csv")[0]
        self.assertEqual(row["run_1_hash"], row["run_2_hash"])
        self.assertEqual((row["equal"], row["key_difference_count"], row["value_difference_count"]), ("true", "0", "0"))

    def test_19_acceptance_has_no_blocker_and_only_the_cost_warning(self) -> None:
        self.assertEqual(len(self.acceptance), 37)
        self.assertFalse(any(row["status"] in {"FAIL", "BLOCKED"} for row in self.acceptance.values()))
        self.assertEqual(
            [row["check"] for row in self.acceptance.values() if row["status"] == "PASS_WITH_WARNING"],
            ["NO_FROZEN_COST_GRID_DISCLOSURE"],
        )

    def test_20_protected_inputs_remain_unchanged(self) -> None:
        self.assertEqual(validation.validation_hash(ROOT / gate_a0.PRIOR_VALIDATION), gate_a0.PRIOR_VALIDATION_HASH)
        self.assertEqual(construction_hash := validation.input_freeze(ROOT)["factor_hash"], validation.FACTOR_HASH)
        self.assertEqual(validation.input_freeze(ROOT)["canonical_hash"], validation.CANONICAL_HASH)
        self.assertEqual(gate_a0.construction.logical_hash(ROOT / gate_a0.SCORE_STORE), gate_a0.frozen_inputs(ROOT)["gate_a0_score_store_hash"])
        self.assertTrue(validation.gate0.verify_raw_integrity(ROOT)["raw_hashes_match"])

    def test_21_no_erroneous_nested_output_directory_remains(self) -> None:
        self.assertFalse((AUDIT / "outputs").exists())

    def test_22_no_gate_b_or_fmom_stage_entry_exists(self) -> None:
        entries = rows("oos_contamination_register.csv")
        self.assertFalse(any(row["stage"].startswith("FORMAL_GATE_B") for row in entries))
        self.assertEqual(self.acceptance["NO_FMOM_CONFIGURATION"]["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
