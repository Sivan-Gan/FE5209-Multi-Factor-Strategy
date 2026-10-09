"""Focused tests for the extreme-SEC-shares audit and simulation only."""

from __future__ import annotations

import csv
import sqlite3
import unittest
from contextlib import closing
from datetime import date

from src.data import extreme_sec_shares_validation as extreme
from src.data import source_audit


ROOT = extreme.project_root()


def _fact(day: int, value: float) -> extreme.ShareFact:
    return extreme.ShareFact(
        ticker="TEST", yahoo_ticker="TEST", cik=1, issuer="Test", taxonomy="dei",
        tag="EntityCommonStockSharesOutstanding", unit="shares", raw_value=value,
        value_text=str(value), end_date=date(2020, day, 1), filed_date=date(2020, day, 2),
        form="10-Q", accession=f"acc-{day}", amendment_flag=False, frame="", fiscal_year="2020",
        fiscal_period="Q1", ordinal=day, tag_label="", tag_description="",
    )


class TestExtremeSharesRule(unittest.TestCase):
    def test_relative_transient_high_run_is_detected_without_rescaling(self) -> None:
        sequence = [_fact(1, 500_000_000), _fact(2, 500_000_000_000), _fact(3, 501_000_000)]
        result = extreme.detect_transient_high_runs(sequence, [])
        decision = result[sequence[1].source_id]
        self.assertEqual(decision["classification"], "CONFIRMED_SCALE_ANOMALY")
        self.assertEqual(decision["recommended_exclusion_reason"], "EXTREME_SHARES_UNRESOLVED")
        self.assertEqual(sequence[1].raw_value, 500_000_000_000)

    def test_normal_large_issuer_is_not_removed_by_absolute_size(self) -> None:
        sequence = [_fact(1, 2_000_000_000_000), _fact(2, 2_100_000_000_000), _fact(3, 1_950_000_000_000)]
        self.assertEqual(extreme.detect_transient_high_runs(sequence, []), {})

    def test_standard_and_reverse_split_explain_matching_relative_boundary(self) -> None:
        forward = [_fact(1, 100_000_000), _fact(2, 10_000_000_000), _fact(3, 100_000_000)]
        reverse = [_fact(1, 10_000_000_000), _fact(2, 100_000_000), _fact(3, 10_000_000_000)]
        forward_events = [extreme.ActionEvent(date(2020, 2, 1), 100.0, True)]
        reverse_events = [extreme.ActionEvent(date(2020, 2, 1), 0.01, True)]
        self.assertEqual(extreme.detect_transient_high_runs(forward, forward_events), {})
        self.assertEqual(extreme.detect_transient_high_runs(reverse, reverse_events), {})

    def test_one_sided_reversion_is_unresolved_not_repaired(self) -> None:
        sequence = [_fact(1, 500_000_000_000), _fact(2, 500_000_000)]
        result = extreme.detect_transient_high_runs(sequence, [])
        self.assertEqual(result[sequence[0].source_id]["classification"], "UNRESOLVED_EXTREME_SHARES")
        self.assertEqual(result[sequence[0].source_id]["rejection_recommended"], 1)


class TestExtremeSharesArtifacts(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = extreme.paths(ROOT)

    def test_source_population_and_preserved_provenance(self) -> None:
        with self.paths["source"].open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 20)
        self.assertEqual(len({row["ticker"] for row in rows}), 13)
        self.assertTrue(all(row["rejection_recommended"] == "1" for row in rows))
        self.assertTrue(all(row["recommended_exclusion_reason"] == "EXTREME_SHARES_UNRESOLVED" for row in rows))
        self.assertTrue(all(row["source_context_metadata_available"] == "0" for row in rows))

    def test_simulation_does_not_modify_canonical_object(self) -> None:
        with closing(sqlite3.connect(f"file:{ROOT / 'data/canonical/us_weekly_estimated_market_cap_dev.sqlite'}?mode=ro", uri=True)) as connection:
            count = connection.execute("SELECT COUNT(*) FROM weekly_estimated_market_cap").fetchone()[0]
            extreme_count = connection.execute(
                "SELECT COUNT(*) FROM weekly_estimated_market_cap WHERE availability_flag = 1 AND reported_shares > ?",
                (extreme.DIAGNOSTIC_ABSOLUTE_THRESHOLD,),
            ).fetchone()[0]
        self.assertEqual(count, 503 * 1081)
        self.assertEqual(extreme_count, 274)

    def test_rejection_impact_and_remaining_jump_are_explicit(self) -> None:
        with self.paths["impact"].open(newline="", encoding="utf-8") as handle:
            impacts = {(row["measure"], row["window"]): row for row in csv.DictReader(handle)}
        first = impacts[("market_cap_input", "factor_selection_2011_2015")]
        self.assertEqual(first["affected_available_ticker_week_count"], "149")
        self.assertLess(float(first["after_mean_weekly_share"]), float(first["before_mean_weekly_share"]))
        with self.paths["jumps"].open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        after_top = next(row for row in rows if row["scenario"] == "after_simulated_rejection" and row["rank"] == "1")
        self.assertEqual(after_top["ticker"], "VTRS")
        self.assertGreater(float(after_top["market_cap_ratio"]), 1_000_000)

    def test_signed_standard_split_regression_and_raw_hash(self) -> None:
        with (ROOT / "outputs/audit/estimated_market_cap_split_validation_dev.csv").open(encoding="utf-8") as handle:
            split_text = handle.read()
        for case in ("AAPL_2014_7_FOR_1", "AAPL_2020_4_FOR_1", "AMZN_2022_20_FOR_1", "C_2011_1_FOR_10_REVERSE"):
            self.assertIn(case, split_text)
        self.assertNotIn(",FAIL\n", split_text)
        manifest = source_audit.audit_manifest(source_audit.package_root(ROOT))
        self.assertTrue(manifest["passed"])
        self.assertEqual(manifest["verified_payload_files"], 508)

    def test_acceptance_reports_nonclosure_not_a_factor_result(self) -> None:
        text = self.paths["acceptance"].read_text(encoding="utf-8")
        self.assertIn("**Decision:** `FAIL`", text)
        self.assertIn("not ready for formal sign-off", text)
        self.assertIn("no factor, ratio, IC, portfolio, FMOM, or strategy", text)


if __name__ == "__main__":
    unittest.main()
