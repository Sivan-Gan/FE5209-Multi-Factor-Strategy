"""Focused governance checks for the approved human Gate A Final Factor Set."""
from __future__ import annotations

import csv
import unittest
from pathlib import Path

from src.validation import freeze_gate_a_final_factor_set as freeze


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / freeze.AUDIT


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class GateAFinalFactorSetFreezeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.decisions = rows(AUDIT / "gate_a_final_factor_set.csv")
        cls.by_family = {row["family"]: row for row in cls.decisions}
        cls.registry = rows(AUDIT / "gate_a_final_factor_registry.csv")
        cls.acceptance = {row["check"]: row for row in rows(AUDIT / "gate_a_final_freeze_acceptance.csv")}
        cls.oos = rows(AUDIT / "oos_contamination_register.csv")
        cls.ssot = (ROOT / freeze.SSOT).read_text(encoding="utf-8")
        cls.report = (AUDIT / "WRDS_GATE_A_FINAL_FACTOR_SET_FREEZE.md").read_text(encoding="utf-8")

    def test_01_final_factor_count_is_exactly_four(self) -> None:
        self.assertEqual(len(self.registry), 4)
        self.assertEqual({row["final_factor_count"] for row in self.decisions}, {"4"})
        self.assertEqual(sum(row["included_in_final_factor_set"] == "TRUE" for row in self.decisions), 4)

    def test_02_exact_retained_ids_and_order_are_frozen(self) -> None:
        expected = ["QUALITY_CURRENT", "MOMENTUM_12_1", "LOWRISK_CURRENT", "INVESTMENT_CURRENT"]
        self.assertEqual([row["specification_id"] for row in self.registry], expected)
        self.assertEqual([row["final_factor_order"] for row in self.registry], ["1", "2", "3", "4"])

    def test_03_exact_dropped_ids_are_not_final_factors(self) -> None:
        dropped = {family: row["gate_a0_representative_specification_id"] for family, row in self.by_family.items() if row["included_in_final_factor_set"] == "FALSE"}
        self.assertEqual(dropped, {"Value": "VALUE_CURRENT", "Dividend": "DIVIDEND_CURRENT", "Size": "SIZE_CURRENT"})

    def test_04_each_human_keep_drop_decision_matches_authority(self) -> None:
        expected = {
            "Quality": "KEEP", "Momentum": "KEEP", "Low Risk": "KEEP", "Investment": "KEEP",
            "Value": "DROP", "Dividend": "DROP", "Size": "DROP",
        }
        self.assertEqual({family: row["gate_a_decision"] for family, row in self.by_family.items()}, expected)
        self.assertEqual({row["human_decision"] for row in self.decisions}, {"TRUE"})
        self.assertEqual({row["decision_evidence_period"] for row in self.decisions}, {"2011–2015"})

    def test_05_momentum_12_1_is_retained_and_old_momentum_is_excluded(self) -> None:
        self.assertEqual(self.by_family["Momentum"]["gate_a0_representative_specification_id"], "MOMENTUM_12_1")
        self.assertNotIn("MOMENTUM_CURRENT", {row["specification_id"] for row in self.registry})

    def test_06_quality_low_risk_and_investment_are_retained(self) -> None:
        for family in ("Quality", "Low Risk", "Investment"):
            self.assertEqual(self.by_family[family]["included_in_final_factor_set"], "TRUE")

    def test_07_no_component_or_round_two_factor_is_promoted(self) -> None:
        ids = {row["specification_id"] for row in self.registry}
        self.assertFalse(any("COMPONENT" in identifier for identifier in ids))
        self.assertIn("ROUND_2_FACTOR_SEARCH = NOT_AUTHORIZED", self.ssot)
        self.assertIn("ROUND_2_FACTOR_SEARCH = NOT_AUTHORIZED", self.report)

    def test_08_gate_a_is_frozen_and_gate_a0_map_is_unchanged(self) -> None:
        self.assertIn("GATE A STATUS: FROZEN", self.ssot)
        self.assertIn("GATE A0 STATUS: FROZEN", self.ssot)
        self.assertEqual(
            {row["family"]: row["gate_a0_representative_specification_id"] for row in self.decisions},
            freeze.EXPECTED_GATE_A0,
        )

    def test_09_no_automatic_rank_or_mechanical_inference_threshold_is_used(self) -> None:
        self.assertIn("not an automated rank, score, p-value, or BH-q-value threshold decision", self.report)
        self.assertIn("No mechanical p-value or BH-q-value threshold", self.report)
        self.assertNotIn("rank", freeze.HUMAN_DECISIONS["Quality"]["primary"].lower())

    def test_10_directions_are_preserved_without_a_flip(self) -> None:
        source = {row["specification_id"]: row for row in rows(AUDIT / "gate_a0_within_family_specification_registry.csv")}
        self.assertTrue(all(row["direction"] == source[row["specification_id"]]["direction"] for row in self.registry))
        self.assertEqual({row["direction"] for row in self.registry}, {"Higher is preferred"})

    def test_11_report_has_all_twenty_required_sections_and_exact_final_contract(self) -> None:
        self.assertTrue(all(f"## {number}." in self.report for number in range(1, 21)))
        for value in ("QUALITY_CURRENT", "MOMENTUM_12_1", "LOWRISK_CURRENT", "INVESTMENT_CURRENT", "K = 4"):
            self.assertIn(value, self.report)

    def test_12_later_performance_and_gate_b_are_not_accessed_or_started(self) -> None:
        records = [row for row in self.oos if row["stage"] == "HUMAN_GATE_A_FINAL_FACTOR_SET_FREEZE"]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual((record["performance_2016_2020"], record["performance_2021_2025"], record["post2015_returns_used_for_factor_choice"]), ("NOT_ACCESSED", "NOT_ACCESSED", "FALSE"))
        self.assertIn("2026 performance=NOT_ACCESSED", record["notes"])
        self.assertIn("Gate B=NOT_STARTED", record["formal_gate_a_evidence"])
        self.assertIn("FMOM configuration=NOT_STARTED", record["formal_gate_a_evidence"])
        self.assertFalse(any(row["stage"].startswith("FORMAL_GATE_B") for row in self.oos))

    def test_13_no_fmom_strategy_or_factor_score_construction_is_claimed(self) -> None:
        for check in ("NO_FMOM_CALCULATION", "NO_STRATEGY_BACKTEST", "NO_NEW_FACTOR_SCORE_CONSTRUCTION"):
            self.assertEqual(self.acceptance[check]["status"], "PASS")
        source = (ROOT / "src/validation/freeze_gate_a_final_factor_set.py").read_text(encoding="utf-8")
        self.assertNotIn("sqlite3", source)
        self.assertNotIn("spearman", source)

    def test_14_dropped_factors_are_preserved_for_audit(self) -> None:
        for family in ("Value", "Dividend", "Size"):
            self.assertIn("AUDIT_ATTRIBUTION_ROBUSTNESS_ONLY", self.by_family[family]["future_role"])
        self.assertEqual(self.acceptance["DROPPED_FACTORS_PRESERVED_FOR_AUDIT"]["status"], "PASS")

    def test_15_final_registry_is_a_governance_contract_from_frozen_sources(self) -> None:
        self.assertEqual({row["selected_at_gate"] for row in self.registry}, {"GATE_A"})
        self.assertEqual({row["gate_a0_status"] for row in self.registry}, {"FROZEN_GATE_A0_REPRESENTATIVE"})
        self.assertEqual({row["gate_a_status"] for row in self.registry}, {"KEEP__FROZEN"})
        self.assertEqual({row["freeze_commit_parent"] for row in self.registry}, {freeze.PARENT_COMMIT})
        self.assertTrue(all("no new score construction" in row["notes"].lower() for row in self.registry))

    def test_16_ssot_and_acceptance_are_consistent(self) -> None:
        for value in ("GATE A STATUS: FROZEN", "VALUE_CURRENT", "DIVIDEND_CURRENT", "SIZE_CURRENT", "K=4"):
            self.assertIn(value, self.ssot)
        # The authorized matrix has 32 named checks; the CSV has 33 lines
        # including its mandatory header.
        self.assertEqual(len(self.acceptance), 32)
        self.assertFalse(any(row["status"] in {"FAIL", "BLOCKED"} for row in self.acceptance.values()))
        self.assertEqual([row["check"] for row in self.acceptance.values() if row["status"] == "PASS_WITH_WARNING"], ["REFRESHED_GATE_A_EVIDENCE_PASS"])

    def test_17_protected_artifacts_are_immutable(self) -> None:
        manifest = rows(AUDIT / "gate_a_final_freeze_protected_artifacts.csv")
        self.assertGreaterEqual(len(manifest), 10)
        self.assertEqual({row["before_after_match"] for row in manifest}, {"TRUE"})
        self.assertNotIn("false", {row["value"] for row in manifest})
        self.assertEqual(self.acceptance["PROTECTED_ARTIFACT_IMMUTABILITY"]["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
