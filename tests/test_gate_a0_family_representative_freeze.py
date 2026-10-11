"""Acceptance checks for the user-authorized human Gate A0 representative freeze."""
from __future__ import annotations

import csv
import unittest
from pathlib import Path

from src.validation import freeze_gate_a0_family_representatives as freeze


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "outputs/audit/wrds_formal"


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class GateA0FamilyRepresentativeFreezeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.decisions = rows(AUDIT / "gate_a0_family_representative_decision.csv")
        cls.by_family = {row["family"]: row for row in cls.decisions}
        cls.acceptance = {
            row["check"]: row for row in rows(AUDIT / "gate_a0_freeze_acceptance.csv")
        }

    def test_01_exactly_the_approved_seven_family_map_is_recorded(self) -> None:
        expected = {
            "Value": "VALUE_CURRENT",
            "Quality": "QUALITY_CURRENT",
            "Momentum": "MOMENTUM_12_1",
            "Low Risk": "LOWRISK_CURRENT",
            "Investment": "INVESTMENT_CURRENT",
            "Dividend": "DIVIDEND_CURRENT",
            "Size": "SIZE_CURRENT",
        }
        self.assertEqual(len(self.decisions), 7)
        self.assertEqual(
            {family: row["selected_specification_id"] for family, row in self.by_family.items()},
            expected,
        )

    def test_02_only_momentum_replaces_its_incumbent(self) -> None:
        replacement = [
            row for row in self.decisions if row["decision_type"] == "REPLACE_INCUMBENT"
        ]
        self.assertEqual(len(replacement), 1)
        self.assertEqual(
            replacement[0]["family"], "Momentum"
        )
        self.assertEqual(replacement[0]["incumbent_specification_id"], "MOMENTUM_CURRENT")
        self.assertEqual(replacement[0]["incumbent_retained"], "FALSE")

    def test_03_other_multi_specification_families_retain_the_incumbent(self) -> None:
        for family in ("Value", "Quality", "Low Risk"):
            row = self.by_family[family]
            self.assertEqual(row["decision_type"], "RETAIN_INCUMBENT")
            self.assertEqual(row["selected_specification_id"], row["incumbent_specification_id"])
            self.assertEqual(row["incumbent_retained"], "TRUE")

    def test_04_single_specification_families_are_passthrough(self) -> None:
        for family in ("Investment", "Dividend", "Size"):
            row = self.by_family[family]
            self.assertEqual(row["decision_type"], "SINGLE_SPECIFICATION_PASSTHROUGH")
            self.assertEqual(row["eligible_candidate_count"], "1")

    def test_05_components_are_not_promoted_and_round_two_is_not_authorized(self) -> None:
        self.assertFalse(
            any("COMPONENT" in row["selected_specification_id"] for row in self.decisions)
        )
        self.assertEqual(
            {row["round2_challenger_status"] for row in self.decisions},
            {"ROUND_2_NOT_AUTHORIZED"},
        )

    def test_06_provenance_records_a_human_2011_2015_only_decision(self) -> None:
        self.assertEqual({row["human_decision"] for row in self.decisions}, {"TRUE"})
        self.assertEqual(
            {row["decision_basis"] for row in self.decisions},
            {"APPROVED_HUMAN_GATE_A0_REVIEW__NO_AUTOMATED_RANKING"},
        )
        self.assertEqual({row["decision_evidence_period"] for row in self.decisions}, {"2011-2015"})
        self.assertEqual(
            {row["later_period_performance_accessed"] for row in self.decisions},
            {"FALSE"},
        )

    def test_07_ssot_and_report_preserve_the_stage_boundary(self) -> None:
        ssot = (ROOT / freeze.SSOT).read_text(encoding="utf-8")
        report = (AUDIT / "WRDS_GATE_A0_FAMILY_REPRESENTATIVE_FREEZE.md").read_text(
            encoding="utf-8"
        )
        for value in (
            "GATE A0 STATUS: FROZEN",
            "MOMENTUM_12_1",
            "ROUND 2 — NOT AUTHORIZED",
            "Gate A remains TBD",
        ):
            self.assertIn(value, ssot)
        self.assertTrue(all(f"## {index}." in report for index in range(1, 19)))
        self.assertIn("Gate A remains pending", report)

    def test_08_contamination_register_protects_later_performance(self) -> None:
        register = rows(AUDIT / "oos_contamination_register.csv")
        record = [
            row
            for row in register
            if row["stage"] == "FORMAL_GATE_A0_HUMAN_REPRESENTATIVE_FREEZE"
        ]
        self.assertEqual(len(record), 1)
        self.assertEqual(record[0]["performance_2016_2020"], "NOT_ACCESSED")
        self.assertEqual(record[0]["performance_2021_2025"], "NOT_ACCESSED")
        self.assertEqual(record[0]["post2015_returns_used_for_factor_choice"], "FALSE")
        self.assertIn("Gate A remains TBD", record[0]["formal_gate_a_evidence"])
        self.assertIn("Gate B has not begun", record[0]["formal_gate_a_evidence"])

    def test_09_acceptance_matrix_is_complete_and_has_no_blocker(self) -> None:
        self.assertEqual(len(self.acceptance), 21)
        self.assertFalse(
            any(row["status"] in {"FAIL", "BLOCKED"} for row in self.acceptance.values())
        )
        self.assertFalse(any(row["blocking"] == "true" for row in self.acceptance.values()))

    def test_10_freeze_module_has_no_automated_ranking_or_performance_engine(self) -> None:
        source = Path(freeze.__file__).read_text(encoding="utf-8").lower()
        self.assertNotIn("sqlite3", source)
        self.assertNotIn("spearman", source)
        self.assertNotIn("weighted evidence", source)
        self.assertNotIn("factor_score", source)


if __name__ == "__main__":
    unittest.main()
