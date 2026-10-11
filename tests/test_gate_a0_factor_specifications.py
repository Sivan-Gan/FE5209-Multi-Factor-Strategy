"""Read-only contracts for formal Gate A0 existing-constituent score construction.

These tests deliberately inspect only frozen signal inputs, score construction
artifacts, and governance evidence.  They never open outcome, return, IC,
portfolio, or strategy data.
"""
from __future__ import annotations

import csv
import json
import sqlite3
import unittest
from pathlib import Path

from src.factors import build_gate_a0_factor_specifications as construction


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "outputs/audit/wrds_formal"
REGISTRY = AUDIT / "gate_a0_within_family_specification_registry.csv"
STORE = ROOT / "data/factors/us_formal/us_gate_a0_factor_specifications.sqlite"
SOURCE = ROOT / "data/factors/us_formal/us_candidate_factors.sqlite"

EXPECTED_SPECS = {
    "VALUE_CURRENT", "VALUE_EP", "VALUE_BP", "VALUE_EP_BP", "VALUE_FCF_COMPONENT",
    "QUALITY_CURRENT", "QUALITY_ROE", "QUALITY_PROFITABILITY", "QUALITY_GPA_COMPONENT",
    "QUALITY_NEG_LEVERAGE_COMPONENT", "QUALITY_ROA_COMPONENT", "MOMENTUM_CURRENT",
    "MOMENTUM_12_1", "MOMENTUM_6_1", "LOWRISK_CURRENT", "LOWRISK_VOL", "LOWRISK_IVOL",
    "INVESTMENT_CURRENT", "DIVIDEND_CURRENT", "SIZE_CURRENT",
}
INCUMBENTS = {
    "VALUE_CURRENT", "QUALITY_CURRENT", "MOMENTUM_CURRENT", "LOWRISK_CURRENT",
    "INVESTMENT_CURRENT", "DIVIDEND_CURRENT", "SIZE_CURRENT",
}


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class GateA0FactorSpecificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = rows(REGISTRY)
        cls.acceptance = {row["check"]: row for row in rows(AUDIT / "gate_a0_factor_construction_acceptance.csv")}
        cls.connection = sqlite3.connect(f"file:{STORE}?mode=ro", uri=True)
        cls.columns = {row[1] for row in cls.connection.execute("PRAGMA table_info(gate_a0_scores)")}

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def test_01_exact_frozen_specification_ids(self) -> None:
        self.assertEqual({row["specification_id"] for row in self.registry}, EXPECTED_SPECS)
        catalog = {row[0] for row in self.connection.execute("SELECT specification_id FROM specification_catalog")}
        self.assertEqual(catalog, EXPECTED_SPECS)

    def test_02_catalog_matches_registry_metadata(self) -> None:
        actual = {
            row[0]: json.loads(row[7])
            for row in self.connection.execute("SELECT * FROM specification_catalog")
        }
        self.assertEqual(set(actual), EXPECTED_SPECS)
        for item in self.registry:
            self.assertEqual(actual[item["specification_id"]], item)

    def test_03_all_scores_have_explicit_availability_reason_and_provenance_fields(self) -> None:
        required = {
            "signal_date", "permno", "execution_date", "permco", "gvkey", "branch", "gsector",
            "issuer_market_cap", "component_availability_json", "source_frequency_json",
        }
        for spec in EXPECTED_SPECS:
            required.update({
                construction.score(spec), construction.pre(spec), construction.resid(spec),
                construction.count(spec), construction.avail(spec), construction.reason(spec),
            })
        self.assertTrue(required.issubset(self.columns))

    def test_04_natural_key_is_unique_and_matches_frozen_candidate_row_count(self) -> None:
        count, distinct_keys = self.connection.execute(
            "SELECT COUNT(*), COUNT(DISTINCT signal_date || '|' || permno) FROM gate_a0_scores"
        ).fetchone()
        source_count = sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True).execute("SELECT COUNT(*) FROM factors").fetchone()[0]
        self.assertEqual(count, distinct_keys)
        self.assertEqual(count, source_count)

    def test_05_incumbent_reproduction_is_exact(self) -> None:
        audit = rows(AUDIT / "gate_a0_incumbent_reproduction_qa.csv")
        self.assertEqual({row["specification_id"] for row in audit}, INCUMBENTS)
        self.assertTrue(all(row["status"] == "PASS" for row in audit))
        self.assertTrue(all(row["mismatch_count"] == "0" for row in audit))
        self.assertTrue(all(row["coverage_difference"] == "0" for row in audit))

    def test_06_value_neutralized_challengers_have_audit_evidence(self) -> None:
        records = rows(AUDIT / "gate_a0_neutralization_qa.csv")
        specs = {row["specification_id"] for row in records}
        self.assertTrue({"VALUE_EP", "VALUE_BP", "VALUE_EP_BP"}.issubset(specs))
        self.assertTrue(all(row["regression_status"] == "PASS" for row in records if row["specification_id"].startswith("VALUE_")))

    def test_07_quality_neutralized_challengers_have_audit_evidence(self) -> None:
        records = rows(AUDIT / "gate_a0_neutralization_qa.csv")
        specs = {row["specification_id"] for row in records}
        self.assertTrue({"QUALITY_ROE", "QUALITY_PROFITABILITY"}.issubset(specs))
        self.assertTrue(all(row["regression_status"] == "PASS" for row in records if row["specification_id"].startswith("QUALITY_")))

    def test_08_diagnostics_respect_frozen_natural_domains(self) -> None:
        audit = {row["check"]: row for row in rows(AUDIT / "gate_a0_branch_qa.csv")}
        self.assertEqual(set(audit), {
            "VALUE_FCF_NONFINANCIAL_ONLY", "QUALITY_GPA_NONFINANCIAL_ONLY",
            "QUALITY_NEG_LEVERAGE_NONFINANCIAL_ONLY", "QUALITY_ROA_FINANCIAL_RE_ONLY",
        })
        self.assertTrue(all(row["status"] == "PASS" and row["violation_count"] == "0" for row in audit.values()))

    def test_09_value_fcf_is_never_materialized_outside_nonfinancials(self) -> None:
        count = self.connection.execute(
            "SELECT COUNT(*) FROM gate_a0_scores WHERE VALUE_FCF_COMPONENT_score_final IS NOT NULL AND branch <> 'NONFINANCIAL'"
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_10_quality_gpa_and_leverage_are_never_materialized_outside_nonfinancials(self) -> None:
        count = self.connection.execute(
            "SELECT COUNT(*) FROM gate_a0_scores WHERE (QUALITY_GPA_COMPONENT_score_final IS NOT NULL OR QUALITY_NEG_LEVERAGE_COMPONENT_score_final IS NOT NULL) AND branch <> 'NONFINANCIAL'"
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_11_quality_roa_is_never_materialized_outside_financial_real_estate(self) -> None:
        count = self.connection.execute(
            "SELECT COUNT(*) FROM gate_a0_scores WHERE QUALITY_ROA_COMPONENT_score_final IS NOT NULL AND branch <> 'FINANCIAL_REAL_ESTATE'"
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_12_component_diagnostics_are_ineligible_representatives(self) -> None:
        diagnostics = {row["specification_id"] for row in self.registry if row["role"] == "COMPONENT_DIAGNOSTIC_ONLY"}
        self.assertEqual(diagnostics, {
            "VALUE_FCF_COMPONENT", "QUALITY_GPA_COMPONENT",
            "QUALITY_NEG_LEVERAGE_COMPONENT", "QUALITY_ROA_COMPONENT",
        })
        self.assertTrue(all(row["eligible_family_representative"] == "FALSE" for row in self.registry if row["specification_id"] in diagnostics))

    def test_13_all_common_within_family_flags_are_boolean(self) -> None:
        fields = (
            "value_all_eligible_family_representatives_available",
            "quality_all_eligible_family_representatives_available",
            "momentum_all_eligible_family_representatives_available",
            "lowrisk_all_eligible_family_representatives_available",
        )
        for field in fields:
            values = {row[0] for row in self.connection.execute(f"SELECT DISTINCT {field} FROM gate_a0_scores")}
            self.assertTrue(values.issubset({0, 1}))

    def test_14_every_unavailable_score_has_a_reason(self) -> None:
        for spec in EXPECTED_SPECS:
            missing_reason = self.connection.execute(
                f"SELECT COUNT(*) FROM gate_a0_scores WHERE {construction.score(spec)} IS NULL AND ({construction.reason(spec)} IS NULL OR {construction.reason(spec)} = '')"
            ).fetchone()[0]
            self.assertEqual(missing_reason, 0, spec)

    def test_15_available_scores_are_finite(self) -> None:
        for spec in EXPECTED_SPECS:
            invalid = self.connection.execute(
                f"SELECT COUNT(*) FROM gate_a0_scores WHERE {construction.score(spec)} IS NOT NULL AND ({construction.score(spec)} <> {construction.score(spec)})"
            ).fetchone()[0]
            self.assertEqual(invalid, 0, spec)

    def test_16_no_outcome_or_performance_columns_exist(self) -> None:
        prohibited = {"forward_return", "future_return", "portfolio_return", "long_short_return", "ic", "sharpe", "fmom", "decision", "selection"}
        self.assertFalse(self.columns & prohibited)
        audit = rows(AUDIT / "gate_a0_no_performance_access_qa.csv")[0]
        self.assertEqual(audit["status"], "PASS")
        self.assertEqual(audit["post2015_performance_queries"], "0")

    def test_17_no_gate_a0_representative_or_gate_a_decision_exists(self) -> None:
        self.assertEqual(self.acceptance["NO_GATE_A0_SELECTION"]["status"], "PASS")
        self.assertEqual(self.acceptance["NO_GATE_A_SELECTION"]["status"], "PASS")
        decision_like = {"representative_selection", "selected_representative", "gate_a_decision"}
        self.assertFalse(self.columns & decision_like)

    def test_18_direction_audit_is_fixed_and_complete(self) -> None:
        direction = rows(AUDIT / "gate_a0_direction_qa.csv")
        self.assertEqual({row["specification_id"] for row in direction}, {
            "VALUE_EP", "VALUE_BP", "VALUE_EP_BP", "QUALITY_ROE", "QUALITY_PROFITABILITY",
            "QUALITY_GPA_COMPONENT", "QUALITY_NEG_LEVERAGE_COMPONENT", "QUALITY_ROA_COMPONENT",
            "MOMENTUM_12_1", "MOMENTUM_6_1", "LOWRISK_VOL", "LOWRISK_IVOL",
        })
        self.assertTrue(all(row["status"] == "PASS" for row in direction))

    def test_19_determinism_audit_matches(self) -> None:
        row = rows(AUDIT / "gate_a0_factor_construction_determinism.csv")[0]
        self.assertEqual(row["equal"], "true")
        self.assertEqual(row["key_difference_count"], "0")
        self.assertEqual(row["value_difference_count"], "0")

    def test_20_manifest_declares_exact_schema_and_source_hashes(self) -> None:
        manifest = rows(AUDIT / "gate_a0_factor_specification_manifest.csv")[0]
        self.assertEqual(manifest["natural_key"], "(signal_date,permno)")
        self.assertEqual(manifest["specification_count"], "20")
        self.assertEqual(manifest["source_factor_hash"], construction.FACTOR_HASH)
        self.assertEqual(manifest["source_canonical_hash"], construction.CANONICAL_HASH)

    def test_21_definition_contract_is_complete_and_derived_only_from_registry(self) -> None:
        contract = rows(AUDIT / "gate_a0_factor_specification_definition_contract.csv")
        self.assertEqual({row["specification_id"] for row in contract}, EXPECTED_SPECS)
        self.assertTrue(all(row["source_columns"] == "frozen us_candidate_factors.sqlite" for row in contract))
        self.assertTrue(all(row["output_score"] == construction.score(row["specification_id"]) for row in contract))

    def test_22_coverage_audit_is_descriptive_and_complete(self) -> None:
        coverage = rows(AUDIT / "gate_a0_specification_coverage.csv")
        self.assertEqual({row["specification_id"] for row in coverage}, EXPECTED_SPECS)
        self.assertTrue(all(row["branch_counts"] == "DESCRIPTIVE_IN_STORE" for row in coverage))
        self.assertTrue(all(0.0 <= float(row["coverage"]) <= 1.0 for row in coverage))

    def test_23_all_construction_acceptance_checks_pass(self) -> None:
        self.assertTrue(self.acceptance)
        self.assertTrue(all(row["status"] == "PASS" for row in self.acceptance.values()))
        self.assertFalse(any(row["blocking"] == "TRUE" for row in self.acceptance.values()))

    def test_24_contamination_register_records_score_construction_as_firewall_compliant(self) -> None:
        entry = next(
            row for row in rows(AUDIT / "oos_contamination_register.csv")
            if row["stage"] == "FORMAL_GATE_A0_EXISTING_CONSTITUENT_SCORE_CONSTRUCTION"
        )
        self.assertEqual(entry["performance_2016_2020"], "NOT_ACCESSED")
        self.assertEqual(entry["performance_2021_2025"], "NOT_ACCESSED")
        self.assertEqual(entry["post2015_returns_used_for_factor_choice"], "FALSE")
        self.assertEqual(entry["status"], "FIREWALL_COMPLIANT")

    def test_25_construction_report_stops_before_validation_or_selection(self) -> None:
        content = (AUDIT / "WRDS_GATE_A0_FACTOR_SPECIFICATION_CONSTRUCTION_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("No outcome, IC, portfolio, Sharpe, decision, or selection column exists.", content)
        self.assertIn("No representative or Gate A selection is authorized.", content)


if __name__ == "__main__":
    unittest.main()
