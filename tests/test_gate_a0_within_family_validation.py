"""Read-only acceptance contracts for Gate A0 within-family validation."""
from __future__ import annotations

import csv
import sqlite3
import unittest
from pathlib import Path

from src.factors import build_gate_a0_factor_specifications as construction
from src.validation import build_formal_factor_validation as validation
from src.validation import build_gate_a0_within_family_validation as gate_a0


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "outputs/audit/wrds_formal"
STORE = ROOT / "data/validation/us_formal/us_gate_a0_within_family_validation.sqlite"


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class GateA0WithinFamilyValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.connection = sqlite3.connect(f"file:{STORE}?mode=ro", uri=True)
        cls.registry = rows(AUDIT / "gate_a0_within_family_specification_registry.csv")
        cls.acceptance = {row["check"]: row for row in rows(AUDIT / "gate_a0_validation_acceptance.csv")}
        cls.master = rows(AUDIT / "gate_a0_human_review_master.csv")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def test_01_only_formal_2011_2015_observations_are_present(self) -> None:
        minimum, maximum, periods = self.connection.execute(
            "SELECT MIN(signal_date),MAX(signal_date),GROUP_CONCAT(DISTINCT research_period) FROM validation_observations"
        ).fetchone()
        self.assertEqual((minimum, maximum, periods), ("2011-01-07", "2015-12-31", "FACTOR_SELECTION"))
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM validation_observations WHERE signal_date>'2015-12-31'").fetchone()[0], 0)

    def test_02_dev_context_is_explicitly_separated_and_not_used_as_formal_evidence(self) -> None:
        manifest = rows(AUDIT / "gate_a0_validation_manifest.csv")[0]
        self.assertEqual(manifest["dev_context_period"], "NOT_GENERATED__FORMAL_EVIDENCE_ONLY")
        self.assertEqual(self.acceptance["DEV_CONTEXT_SEPARATION"]["status"], "PASS")

    def test_03_post_2015_firewall_is_all_zero(self) -> None:
        firewall = rows(AUDIT / "gate_a0_validation_post2015_firewall_qa.csv")[0]
        self.assertTrue(all(value == "0" for value in firewall.values()))
        register = rows(AUDIT / "oos_contamination_register.csv")
        stage = [row for row in register if row["stage"] == "FORMAL_GATE_A0_WITHIN_FAMILY_VALIDATION"]
        self.assertEqual(len(stage), 1)
        self.assertEqual(stage[0]["performance_2016_2020"], "NOT_ACCESSED")
        self.assertEqual(stage[0]["performance_2021_2025"], "NOT_ACCESSED")

    def test_04_frozen_eligible_ids_are_exact(self) -> None:
        expected = {spec for values in gate_a0.EXPECTED.values() for spec in values}
        self.assertEqual({row["specification_id"] for row in self.master}, expected)
        self.assertFalse(any(row["role"] == "COMPONENT_DIAGNOSTIC_ONLY" for row in self.master))

    def test_05_component_diagnostics_are_outside_the_formal_representative_pool(self) -> None:
        components = {row["specification_id"] for row in self.registry if row["role"] == "COMPONENT_DIAGNOSTIC_ONLY"}
        self.assertEqual(components, {"VALUE_FCF_COMPONENT", "QUALITY_GPA_COMPONENT", "QUALITY_NEG_LEVERAGE_COMPONENT", "QUALITY_ROA_COMPONENT"})
        self.assertFalse(components & {row["specification_id"] for row in self.master})

    def test_06_execution_timing_is_copied_from_validated_observations(self) -> None:
        source = sqlite3.connect(f"file:{ROOT / gate_a0.PRIOR_VALIDATION}?mode=ro", uri=True)
        try:
            values = source.execute("SELECT signal_date,permno,execution_date,next_execution_date,fourth_execution_date FROM validation_observations WHERE signal_date BETWEEN '2011-01-01' AND '2015-12-31' ORDER BY signal_date,permno LIMIT 25").fetchall()
        finally:
            source.close()
        for value in values:
            actual = self.connection.execute("SELECT signal_date,permno,execution_date,next_execution_date,fourth_execution_date FROM validation_observations WHERE signal_date=? AND permno=?", value[:2]).fetchone()
            self.assertEqual(actual, value)
            self.assertGreater(actual[2], actual[0])

    def test_07_1w_and_4w_return_statuses_reuse_the_validated_engine(self) -> None:
        self.assertGreater(self.connection.execute("SELECT COUNT(*) FROM validation_observations WHERE r_1w_status IN ('VALID','VALID_CONFIRMED_DELISTING_CASH')").fetchone()[0], 0)
        self.assertGreater(self.connection.execute("SELECT COUNT(*) FROM validation_observations WHERE r_4w_status IN ('VALID','VALID_CONFIRMED_DELISTING_CASH')").fetchone()[0], 0)
        self.assertEqual(self.acceptance["1W_IC"]["status"], "PASS")
        self.assertEqual(self.acceptance["4W_IC"]["status"], "PASS")

    def test_08_common_samples_require_all_frozen_family_specs(self) -> None:
        for family, specs in gate_a0.EXPECTED.items():
            query = """SELECT COUNT(*) FROM observation_scores
                       WHERE family=? AND eligible_family_representative=1
                         AND common_within_family_available=1 AND score_final IS NULL"""
            self.assertEqual(self.connection.execute(query, (family,)).fetchone()[0], 0, family)
            common_ids = {row[0] for row in self.connection.execute("SELECT DISTINCT specification_id FROM ic_weekly WHERE sample_role=? AND family=?", (gate_a0.COMMON, family))}
            self.assertEqual(common_ids, set(specs))

    def test_09_primary_and_common_views_remain_distinct(self) -> None:
        self.assertEqual({row[0] for row in self.connection.execute("SELECT DISTINCT sample_role FROM ic_weekly")}, {gate_a0.PRIMARY, gate_a0.COMMON, gate_a0.COMPONENT})
        self.assertEqual(self.acceptance["VALUE_PRIMARY_VALIDATION"]["status"], "PASS")
        self.assertEqual(self.acceptance["VALUE_COMMON_SAMPLE_VALIDATION"]["status"], "PASS")

    def test_10_component_natural_domains_are_preserved(self) -> None:
        expected = {"VALUE_FCF_COMPONENT": "NONFINANCIAL", "QUALITY_GPA_COMPONENT": "NONFINANCIAL", "QUALITY_NEG_LEVERAGE_COMPONENT": "NONFINANCIAL", "QUALITY_ROA_COMPONENT": "FINANCIAL_REAL_ESTATE"}
        for spec, branch in expected.items():
            count = self.connection.execute("""SELECT COUNT(*) FROM observation_scores s
                JOIN validation_observations o ON s.signal_date=o.signal_date AND s.permno=o.permno
                WHERE s.specification_id=? AND s.score_final IS NOT NULL AND o.branch<>?""", (spec, branch)).fetchone()[0]
            self.assertEqual(count, 0, spec)

    def test_11_frozen_direction_and_historical_gics_are_preserved(self) -> None:
        self.assertTrue(all(row["direction"] == "Higher is preferred" for row in self.registry))
        sector = rows(AUDIT / "gate_a0_validation_sector_breadth.csv")
        self.assertTrue(sector and all(row["historical_gics_only"] == "TRUE" for row in sector))

    def test_12_yearly_and_rolling_outputs_stay_inside_2011_2015(self) -> None:
        yearly = rows(AUDIT / "gate_a0_validation_yearly_ic.csv")
        self.assertEqual({row["year"] for row in yearly if row["year"] != "SUMMARY"}, {str(year) for year in range(2011, 2016)})
        rolling = rows(AUDIT / "gate_a0_validation_rolling_ic.csv")
        self.assertTrue(rolling and all("2011-01-01" <= row["period_end"] <= "2015-12-31" for row in rolling))

    def test_13_bh_groups_are_within_family_and_horizon(self) -> None:
        summaries = rows(AUDIT / "gate_a0_validation_ic_summary.csv")
        for row in summaries:
            if row["sample_role"] == gate_a0.PRIMARY and row["role"] != "COMPONENT_DIAGNOSTIC_ONLY":
                self.assertEqual(int(row["tested_hypotheses_count"]), len(gate_a0.EXPECTED[row["family"]]))
                self.assertNotEqual(row["bh_fdr_q_value"], "")
            elif row["role"] == "COMPONENT_DIAGNOSTIC_ONLY":
                self.assertEqual(row["tested_hypotheses_count"], "0")

    def test_14_hac_and_block_bootstrap_are_the_frozen_conventions(self) -> None:
        summaries = rows(AUDIT / "gate_a0_validation_ic_summary.csv")
        self.assertTrue(all(row["hac_lag"] == ("4" if row["horizon"] == "1W" else "3") for row in summaries))
        self.assertTrue(all(row["bootstrap_replications"] == "2000" for row in summaries if row["horizon"] == "4W"))

    def test_15_no_automatic_ordering_or_representative_outcome_field_exists(self) -> None:
        forbidden = {"rank", "winner", "selected", "keep", "drop", "replace", "decision"}
        columns = set(self.master[0])
        self.assertFalse(columns & forbidden)
        self.assertEqual(self.acceptance["NO_AUTOMATED_RANKING"]["status"], "PASS")
        self.assertEqual(self.acceptance["NO_WINNER_SELECTION"]["status"], "PASS")
        self.assertEqual(self.acceptance["NO_GATE_A0_DECISION"]["status"], "PASS")
        self.assertEqual(self.acceptance["NO_GATE_A_DECISION"]["status"], "PASS")

    def test_16_review_sheets_use_required_sections_and_avoid_prohibited_terms(self) -> None:
        required = [f"## {index}." for index in range(1, 16)]
        prohibited = ("winner", "best", "select", "keep", "drop", "replace", "final representative")
        for name in ("VALUE.md", "QUALITY.md", "MOMENTUM.md", "LOW_RISK.md"):
            content = (AUDIT / "gate_a0_family_review" / name).read_text(encoding="utf-8").lower()
            self.assertTrue(all(section in content for section in required), name)
            self.assertFalse(any(term in content for term in prohibited), name)

    def test_17_turnover_and_cost_grid_warning_are_disclosed(self) -> None:
        self.assertEqual(self.acceptance["TURNOVER"]["status"], "PASS")
        self.assertEqual(self.acceptance["BREAK_EVEN_COST"]["status"], "PASS")
        self.assertEqual(self.acceptance["NO_FROZEN_COST_GRID_DISCLOSURE"]["status"], "PASS_WITH_WARNING")

    def test_18_determinism_and_protected_input_immutability_hold(self) -> None:
        determinism = rows(AUDIT / "gate_a0_validation_determinism.csv")[0]
        self.assertEqual(determinism["run_1_hash"], determinism["run_2_hash"])
        self.assertEqual((determinism["equal"], determinism["key_difference_count"], determinism["value_difference_count"]), ("true", "0", "0"))
        self.assertEqual(construction.logical_hash(ROOT / gate_a0.SCORE_STORE), construction.logical_hash(ROOT / gate_a0.SCORE_STORE))
        self.assertEqual(validation.validation_hash(ROOT / gate_a0.PRIOR_VALIDATION), gate_a0.PRIOR_VALIDATION_HASH)
        self.assertEqual(canonical_hash := validation.input_freeze(ROOT)["canonical_hash"], validation.CANONICAL_HASH)
        self.assertTrue(validation.gate0.verify_raw_integrity(ROOT)["raw_hashes_match"])

    def test_19_acceptance_matrix_is_pass_with_one_nonblocking_warning(self) -> None:
        self.assertEqual(len(self.acceptance), 42)
        self.assertFalse(any(row["status"] in {"FAIL", "BLOCKED"} for row in self.acceptance.values()))
        self.assertEqual([row["check"] for row in self.acceptance.values() if row["status"] == "PASS_WITH_WARNING"], ["NO_FROZEN_COST_GRID_DISCLOSURE"])


if __name__ == "__main__":
    unittest.main()
