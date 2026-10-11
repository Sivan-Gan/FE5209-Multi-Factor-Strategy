"""Governance-only contract tests for the Gate A0 specification freeze."""
from __future__ import annotations

import csv
import unittest
from pathlib import Path

from src.validation import build_gate_a_human_review as review
from src.validation import build_formal_factor_validation as validation


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "outputs/audit/wrds_formal"
SSOT = ROOT / "docs/US_SG_Cross_Market_Quant_Research.md"
REGISTRY = AUDIT / "gate_a0_within_family_specification_registry.csv"
ACCEPTANCE = AUDIT / "gate_a0_governance_acceptance.csv"
REPORT = AUDIT / "WRDS_GATE_A0_WITHIN_FAMILY_GOVERNANCE_FREEZE.md"


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class GateA0GovernanceTests(unittest.TestCase):
    def test_01_gate_a0_exists_and_gate_a_remains_distinct(self) -> None:
        content = SSOT.read_text(encoding="utf-8")
        self.assertIn("## Gate A0 — Within-Family Specification Freeze", content)
        self.assertIn("## Gate A — Final Factor Set Freeze", content)
        self.assertIn("Gate A0 asks **“How should this economic style be measured?”**", content)
        self.assertIn("Gate A asks **“Should this economic style enter the final multi-factor strategy?”**", content)

    def test_02_registry_schema_and_ids_are_complete(self) -> None:
        registry = rows(REGISTRY)
        required = {
            "family", "specification_id", "specification_name", "role",
            "eligible_family_representative", "incumbent", "economic_definition",
            "components", "sector_branch", "natural_domain", "direction",
            "winsorization_rule", "standardization_rule", "neutralization_rule",
            "availability_rule", "pit_rule", "source_definition_reference", "round",
            "performance_seen_before_spec_freeze", "notes",
        }
        self.assertEqual(set(registry[0]), required)
        self.assertEqual(len(registry), 20)
        self.assertEqual(len({row["specification_id"] for row in registry}), 20)
        self.assertTrue(all(row["round"] == "EXISTING_CONSTITUENT_ROUND" for row in registry))
        self.assertTrue(all(row["performance_seen_before_spec_freeze"].startswith("TRUE__") for row in registry))

    def test_03_exactly_four_multi_specification_families(self) -> None:
        registry = rows(REGISTRY)
        families = {row["family"] for row in registry if row["role"] in {"INCUMBENT", "CHALLENGER"}}
        self.assertEqual(families, {"Value", "Quality", "Momentum", "Low Risk"})

    def test_04_value_eligible_pool_is_frozen(self) -> None:
        registry = rows(REGISTRY)
        actual = {row["specification_id"] for row in registry if row["family"] == "Value" and row["eligible_family_representative"] == "TRUE"}
        self.assertEqual(actual, {"VALUE_CURRENT", "VALUE_EP", "VALUE_BP", "VALUE_EP_BP"})

    def test_05_quality_eligible_pool_is_frozen(self) -> None:
        registry = rows(REGISTRY)
        actual = {row["specification_id"] for row in registry if row["family"] == "Quality" and row["eligible_family_representative"] == "TRUE"}
        self.assertEqual(actual, {"QUALITY_CURRENT", "QUALITY_ROE", "QUALITY_PROFITABILITY"})

    def test_06_momentum_eligible_pool_is_frozen(self) -> None:
        registry = rows(REGISTRY)
        actual = {row["specification_id"] for row in registry if row["family"] == "Momentum" and row["eligible_family_representative"] == "TRUE"}
        self.assertEqual(actual, {"MOMENTUM_CURRENT", "MOMENTUM_12_1", "MOMENTUM_6_1"})

    def test_07_low_risk_eligible_pool_is_frozen(self) -> None:
        registry = rows(REGISTRY)
        actual = {row["specification_id"] for row in registry if row["family"] == "Low Risk" and row["eligible_family_representative"] == "TRUE"}
        self.assertEqual(actual, {"LOWRISK_CURRENT", "LOWRISK_VOL", "LOWRISK_IVOL"})

    def test_08_single_specification_families_pass_through(self) -> None:
        registry = rows(REGISTRY)
        actual = {row["family"]: row["specification_id"] for row in registry if row["role"] == "SINGLE_SPECIFICATION"}
        self.assertEqual(actual, {"Investment": "INVESTMENT_CURRENT", "Dividend": "DIVIDEND_CURRENT", "Size": "SIZE_CURRENT"})
        self.assertTrue(all(row["eligible_family_representative"] == "TRUE" for row in registry if row["role"] == "SINGLE_SPECIFICATION"))

    def test_09_component_diagnostics_cannot_be_representatives(self) -> None:
        registry = rows(REGISTRY)
        diagnostics = [row for row in registry if row["role"] == "COMPONENT_DIAGNOSTIC_ONLY"]
        self.assertEqual({row["specification_id"] for row in diagnostics}, {"VALUE_FCF_COMPONENT", "QUALITY_GPA_COMPONENT", "QUALITY_NEG_LEVERAGE_COMPONENT", "QUALITY_ROA_COMPONENT"})
        self.assertTrue(all(row["eligible_family_representative"] == "FALSE" for row in diagnostics))

    def test_10_incumbent_equal_weight_is_preserved(self) -> None:
        content = SSOT.read_text(encoding="utf-8")
        self.assertIn("INCUMBENT BASELINE SPECIFICATIONS", content)
        self.assertIn("既有 validation 不被重写", content)
        incumbents = {row["specification_id"] for row in rows(REGISTRY) if row["incumbent"] == "TRUE"}
        self.assertTrue({"VALUE_CURRENT", "QUALITY_CURRENT", "MOMENTUM_CURRENT", "LOWRISK_CURRENT"}.issubset(incumbents))

    def test_11_continuous_internal_weight_optimization_is_prohibited(self) -> None:
        content = SSOT.read_text(encoding="utf-8")
        self.assertIn("连续内部权重优化", content)
        self.assertIn("machine-learning combination", content)
        self.assertIn("以 Sharpe 最大化内部权重", content)

    def test_12_literature_challenger_round_is_not_authorized(self) -> None:
        content = SSOT.read_text(encoding="utf-8")
        self.assertIn("A future Round 2 requires a separate governance decision", content)
        self.assertIn("Trend Momentum", content)
        self.assertIn("Low Beta / BAB", content)

    def test_13_one_representative_rule_and_human_decision_exist(self) -> None:
        content = SSOT.read_text(encoding="utf-8")
        self.assertIn("at most one", content)
        self.assertIn("machine-selected winner", content)
        self.assertIn("retain the INCUMBENT specification", content)

    def test_14_formal_gate_a0_evidence_period_is_2011_2015(self) -> None:
        content = SSOT.read_text(encoding="utf-8")
        self.assertIn("Gate A0 formal performance evidence is 2011–2015 only", content)

    def test_15_2016_2020_and_2021_2025_access_are_prohibited(self) -> None:
        content = SSOT.read_text(encoding="utf-8")
        self.assertIn("Do not access 2016–2020, 2021–2025, or 2026 outcome evidence", content)
        register = rows(AUDIT / "oos_contamination_register.csv")
        entry = next(row for row in register if row["stage"] == "FORMAL_GATE_A0_WITHIN_FAMILY_SPECIFICATION_GOVERNANCE_PRE_CONSTRUCTION")
        self.assertEqual(entry["performance_2016_2020"], "NOT_ACCESSED")
        self.assertEqual(entry["performance_2021_2025"], "NOT_ACCESSED")
        self.assertEqual(entry["post2015_returns_used_for_factor_choice"], "FALSE")

    def test_16_authorized_validation_artifacts_exist_without_automated_choice_artifacts(self) -> None:
        gate_a0_csvs = {path.name for path in AUDIT.glob("gate_a0_*.csv")}
        required = {
            "gate_a0_within_family_specification_registry.csv",
            "gate_a0_governance_acceptance.csv",
            "gate_a0_factor_construction_acceptance.csv",
            "gate_a0_validation_acceptance.csv",
            "gate_a0_validation_manifest.csv",
            "gate_a0_validation_determinism.csv",
            "gate_a0_validation_post2015_firewall_qa.csv",
            "gate_a0_human_review_master.csv",
            "gate_a0_value_comparison.csv",
            "gate_a0_quality_comparison.csv",
            "gate_a0_momentum_comparison.csv",
            "gate_a0_lowrisk_comparison.csv",
        }
        self.assertTrue(required.issubset(gate_a0_csvs))
        forbidden_artifacts = (
            "gate_a0_representative_selection_",
            "gate_a0_automated_ranking_",
            "gate_a0_winner_",
        )
        self.assertFalse(any(name.startswith(forbidden_artifacts) for name in gate_a0_csvs))

    def test_17_governance_report_has_all_required_sections(self) -> None:
        content = REPORT.read_text(encoding="utf-8")
        self.assertIn("## 1. Why Gate A0 was introduced", content)
        self.assertIn("## 18. Explicit prohibited actions", content)
        self.assertIn("Exact next permitted step", content)
        self.assertTrue(
            content.rstrip().endswith(
                "Gate A0 existing-constituent factor construction is now permitted."
            )
        )

    def test_18_acceptance_matrix_is_pass_with_one_disclosed_warning(self) -> None:
        acceptance = rows(ACCEPTANCE)
        expected = {
            "HISTORICAL_INCUMBENT_EVIDENCE_PRESERVED", "ADAPTIVE_RESEARCH_DISCLOSURE", "GATE_A0_DEFINED",
            "GATE_A_REDEFINED_AS_CROSS_FAMILY", "VALUE_POOL_FROZEN", "QUALITY_POOL_FROZEN",
            "MOMENTUM_POOL_FROZEN", "LOWRISK_POOL_FROZEN", "SINGLE_SPEC_FAMILIES_IDENTIFIED",
            "COMPONENT_DIAGNOSTICS_SEPARATED", "NO_CONTINUOUS_WEIGHT_OPTIMIZATION", "NO_LITERATURE_CHALLENGERS",
            "COMMON_WITHIN_FAMILY_RULE", "INCUMBENT_DEFAULT_RULE", "ONE_REPRESENTATIVE_PER_FAMILY",
            "MULTIPLE_TESTING_GOVERNANCE", "2011_2015_SELECTION_BOUNDARY", "NO_2016_2020_PERFORMANCE_ACCESS",
            "NO_2021_2025_PERFORMANCE_ACCESS", "NO_NEW_PERFORMANCE_CALCULATION", "INPUT_IMMUTABILITY", "SSOT_CONSISTENCY",
        }
        self.assertEqual({row["check"] for row in acceptance}, expected)
        self.assertFalse(any(row["blocking"] == "TRUE" for row in acceptance))
        self.assertEqual([row["check"] for row in acceptance if row["status"] == "PASS_WITH_WARNING"], ["ADAPTIVE_RESEARCH_DISCLOSURE"])

    def test_19_factor_store_and_validation_database_are_unchanged(self) -> None:
        self.assertEqual(validation.input_freeze(ROOT)["factor_hash"], validation.FACTOR_HASH)
        self.assertEqual(validation.validation_hash(ROOT / review.VALIDATION), review.VALIDATION_HASH)

    def test_20_canonical_and_raw_data_are_unchanged(self) -> None:
        self.assertEqual(validation.input_freeze(ROOT)["canonical_hash"], validation.CANONICAL_HASH)
        self.assertTrue(validation.gate0.verify_raw_integrity(ROOT)["raw_hashes_match"])


if __name__ == "__main__":
    unittest.main()
