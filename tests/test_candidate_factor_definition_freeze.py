"""Contract tests for the user-approved Formal-US definition freeze."""

from __future__ import annotations

import csv
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FREEZE = ROOT / "outputs/audit/wrds_formal/candidate_factor_definition_freeze.csv"
FREEZE_REPORT = ROOT / "outputs/audit/wrds_formal/CANDIDATE_FACTOR_DEFINITION_FREEZE.md"
SSOT = ROOT / "docs/US_SG_Cross_Market_Quant_Research.md"
PREDECESSOR = "cd0151bd1df80b1834ce4cfe04b7e967adc6cbde"


class TestCandidateFactorDefinitionFreeze(unittest.TestCase):
    @staticmethod
    def rows() -> list[dict[str, str]]:
        with FREEZE.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def test_required_schema_and_unique_decision_ids(self) -> None:
        rows = self.rows()
        required = {
            "decision_id", "factor", "component", "previous_status", "frozen_definition",
            "economic_rationale", "data_source", "pit_rule", "fallback_rule",
            "neutralization_rule", "effective_from_commit", "notes",
        }
        self.assertEqual(set(rows[0]), required)
        self.assertEqual(len(rows), len({row["decision_id"] for row in rows}))
        self.assertTrue(all(all(row[column] for column in required) for row in rows))

    def test_all_six_prior_design_blockers_are_explicitly_resolved(self) -> None:
        ids = {row["decision_id"] for row in self.rows()}
        self.assertTrue({
            "SIZE_ORIENTATION", "FLOW_SOURCE_PRECEDENCE", "STOCK_SOURCE_PRECEDENCE",
            "QUALITY_ROE", "QUALITY_LEVERAGE", "INVESTMENT_LAG",
            "NEUTRALIZATION_SECTOR", "NEUTRALIZATION_MCAP", "DIVIDEND_EVENT",
            "DIVIDEND_SPLIT_BASIS", "DIVIDEND_DENOMINATOR",
        }.issubset(ids))

    def test_freeze_is_linked_to_the_historical_blocked_predecessor(self) -> None:
        rows = self.rows()
        self.assertTrue(all(row["previous_status"] == f"BLOCKED_AT_{PREDECESSOR}" for row in rows))
        self.assertTrue(all(row["effective_from_commit"] == PREDECESSOR for row in rows))

    def test_ssot_records_the_dated_user_approved_freeze(self) -> None:
        content = SSOT.read_text(encoding="utf-8")
        self.assertIn("Formal US Candidate-Factor Definition Freeze — 2026-10-11", content)
        self.assertIn(PREDECESSOR, content)
        self.assertIn("No IC, return, Sharpe, alpha, portfolio, or backtest evidence", content)

    def test_freeze_report_disclaims_performance_based_choice(self) -> None:
        content = FREEZE_REPORT.read_text(encoding="utf-8")
        self.assertIn("No performance evidence was consulted", content)
        self.assertIn("Stage-B operational acceptance condition", content)


if __name__ == "__main__":
    unittest.main()
