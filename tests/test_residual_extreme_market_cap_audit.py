"""Focused tests for the residual market-cap root-cause audit only."""

from __future__ import annotations

import csv
import math
import sqlite3
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

from src.data import extreme_sec_shares_validation as extreme
from src.data import residual_extreme_market_cap_audit as audit
from src.data import source_audit


ROOT = audit.project_root()


def _observation(day: int, market_cap: float, price: float, shares: float) -> audit.CanonicalObservation:
    return audit.CanonicalObservation(
        ticker="TEST", yahoo_ticker="TEST", cik=1, issuer="Test", signal_date=date(2020, 1, day),
        price_close=price, price_date=date(2020, 1, day), reported_shares=shares,
        aligned_shares=shares, shares_source_tag="dei:EntityCommonStockSharesOutstanding",
        shares_unit="shares", shares_asof_date=date(2020, 1, day), shares_filed_date=date(2020, 1, day),
        shares_form="10-Q", shares_accession=f"acc-{day}", split_adjustment_factor=1.0,
        applied_split_events_json="[]", corporate_action_flag=0, nonstandard_action_events_json="[]",
        estimated_market_cap=market_cap, availability_flag=1, sec_mapping_available=1,
    )


def _fact(day: int, value: float) -> extreme.ShareFact:
    return extreme.ShareFact(
        ticker="TEST", yahoo_ticker="TEST", cik=1, issuer="Test", taxonomy="dei",
        tag="EntityCommonStockSharesOutstanding", unit="shares", raw_value=value,
        value_text=str(value), end_date=date(2020, 1, day), filed_date=date(2020, 1, day),
        form="10-Q", accession=f"acc-{day}", amendment_flag=False, frame="", fiscal_year="2020",
        fiscal_period="Q1", ordinal=day, tag_label="", tag_description="",
    )


class TestResidualJumpUnits(unittest.TestCase):
    def test_market_cap_decomposition_is_exact(self) -> None:
        result = audit.decompose_jump(100.0, 1_200.0, 10.0, 12.0, 10.0, 100.0)
        self.assertEqual(result, (12.0, 1.2, 10.0))

    def test_price_and_shares_driver_classification(self) -> None:
        self.assertEqual(audit.component_driver(20.0, 1.01), "PRICE_DRIVEN")
        self.assertEqual(audit.component_driver(1.01, 20.0), "SHARES_DRIVEN")
        self.assertEqual(audit.component_driver(10.0, 10.0), "BOTH")

    def test_confirmed_local_semantic_break_is_detected_without_repair(self) -> None:
        facts = [_fact(1, 100_000_000), _fact(2, 100_000_000_000), _fact(3, 100_000_000)]
        decisions, rows = audit.semantic_break_candidates(facts, [])
        self.assertEqual(decisions[facts[1].source_id]["classification"], "SHARES_SEMANTIC_ANOMALY")
        anomaly = next(row for row in rows if row["source_id"] == facts[1].source_id)
        self.assertEqual(anomaly["candidate_rule_a_outcome"], "SIMULATE_UNAVAILABLE")
        self.assertEqual(facts[1].raw_value, 100_000_000_000)

    def test_one_sided_break_remains_unresolved(self) -> None:
        facts = [_fact(1, 100), _fact(2, 100_000_000)]
        decisions, rows = audit.semantic_break_candidates(facts, [])
        self.assertEqual(decisions[facts[0].source_id]["classification"], "ONE_SIDED_SHARES_DISCONTINUITY")
        self.assertEqual(rows[0]["candidate_rule_a_outcome"], "RETAIN_PENDING_EVIDENCE")

    def test_standard_split_exclusion_and_unresolved_handling(self) -> None:
        previous = _observation(1, 100.0, 10.0, 10.0)
        current = _observation(2, 100.0, 10.0, 10.0)
        event = audit.JumpEvent(previous, current, 1.0, 1.0, 1.0)
        root, confidence, action = audit.classify_root_cause(event, None, None, {}, True)
        self.assertEqual((root, confidence, action), ("VALID_STANDARD_SPLIT", "HIGH", "NO_REJECTION"))
        unresolved = audit.JumpEvent(previous, _observation(2, 1_000_000.0, 10.0, 100_000.0), 10_000.0, 1.0, 10_000.0)
        root, confidence, action = audit.classify_root_cause(unresolved, None, None, {}, False)
        self.assertEqual(root, "UNRESOLVED_EXTREME_JUMP")
        self.assertEqual(confidence, "LOW")
        self.assertIn("RULE_C", action)

    def test_security_history_break_requires_explicit_validation(self) -> None:
        previous = _observation(1, 100.0, 10.0, 10.0)
        current = _observation(2, 1_000_000.0, 10.0, 100_000.0)
        event = audit.JumpEvent(previous, current, 10_000.0, 1.0, 10_000.0)
        root, confidence, _action = audit.classify_root_cause(event, None, None, {}, False, True)
        self.assertEqual((root, confidence), ("SECURITY_HISTORY_BREAK", "HIGH"))

    def test_no_ticker_specific_governance_branch(self) -> None:
        source = Path(audit.__file__).read_text(encoding="utf-8")
        self.assertNotIn("if ticker ==", source)
        self.assertNotIn("/1e6", source)


class TestResidualJumpArtifacts(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = audit.paths(ROOT)

    def test_root_cause_population_and_identity(self) -> None:
        with self.paths["root_cause"].open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 85)
        self.assertEqual(sum(row["ticker"] == "VTRS" for row in rows), 1)
        self.assertTrue(all(math.isclose(
            float(row["market_cap_ratio"]), float(row["price_ratio"]) * float(row["shares_ratio"]), rel_tol=1e-9
        ) for row in rows))
        with self.paths["identity"].open(newline="", encoding="utf-8") as handle:
            identity = list(csv.DictReader(handle))
        self.assertEqual(sum(row["ticker"] == "SW" for row in identity), 1)
        self.assertTrue(all(row["identity_break_flag"] == "0" for row in identity))

    def test_simulation_is_nonmutating_and_reports_stop_loss_evidence(self) -> None:
        with closing(sqlite3.connect(f"file:{ROOT / 'data/canonical/us_weekly_estimated_market_cap_dev.sqlite'}?mode=ro", uri=True)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM weekly_estimated_market_cap").fetchone()[0], 503 * 1081)
        with self.paths["simulation"].open(newline="", encoding="utf-8") as handle:
            summaries = {
                row["scenario"]: row for row in csv.DictReader(handle) if row["record_type"] == "summary"
            }
        self.assertEqual(int(summaries["BASELINE"]["jump_count_abs_gt_10x"]), 85)
        self.assertGreater(int(summaries["RULE_A_CONFIRMED_SHARES_SEMANTIC_BREAK"]["jump_count_abs_gt_1000000x"]), 0)
        self.assertEqual(int(summaries["RULE_A_PLUS_RULE_C_UNRESOLVED_SOURCE_RANGES"]["jump_count_abs_gt_10x"]), 0)

    def test_coverage_and_raw_hash_integrity(self) -> None:
        with self.paths["coverage"].open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 18)
        self.assertTrue(all(float(row["after_share"]) <= float(row["before_share"]) for row in rows))
        manifest = source_audit.audit_manifest(source_audit.package_root(ROOT))
        self.assertTrue(manifest["passed"])
        self.assertEqual(manifest["verified_payload_files"], 508)

    def test_acceptance_is_fail_and_no_factor_claim(self) -> None:
        text = self.paths["acceptance"].read_text(encoding="utf-8")
        self.assertIn("**Decision:** `FAIL`", text)
        self.assertIn("`STOP_DEV_MARKET_CAP_REPAIR`", text)
        self.assertIn("no factor, Value, Size, neutralization, IC, portfolio, FMOM, or strategy", text)


if __name__ == "__main__":
    unittest.main()
