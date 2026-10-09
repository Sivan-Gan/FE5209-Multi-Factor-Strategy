"""Safety checks for the DEV historical-market-cap specification audit only."""

from __future__ import annotations

import csv
import json
import unittest
from datetime import date
from pathlib import Path

from src.data import historical_market_cap_spec_audit as market_cap
from src.data import source_audit


ROOT = market_cap.project_root()


def _share(
    *,
    source_id: str,
    value: str = "100",
    end: date = date(2020, 3, 31),
    filed: date = date(2020, 5, 1),
    form: str = "10-Q",
) -> market_cap.ShareObservation:
    return market_cap.ShareObservation(
        cik=1,
        taxonomy="dei",
        tag="EntityCommonStockSharesOutstanding",
        unit="shares",
        value_text=value,
        value=float(value),
        end_date=end,
        filed_date=filed,
        form=form,
        accession=source_id,
        frame="",
        fiscal_year="2020",
        fiscal_period="Q1",
        start_date="",
        ordinal=0,
    )


class TestSharePITSelection(unittest.TestCase):
    def test_future_filing_cannot_backfill_earlier_signal(self) -> None:
        old = _share(source_id="old")
        future = _share(
            source_id="future",
            value="200",
            end=date(2020, 6, 30),
            filed=date(2020, 8, 1),
        )
        slot = market_cap.ShareSlot([old, future])

        selection = slot.advance(date(2020, 7, 31))

        self.assertEqual(selection.status, "AVAILABLE")
        self.assertEqual(selection.observation.accession, "old")

    def test_same_latest_filed_conflicting_share_values_are_ambiguous(self) -> None:
        one = _share(source_id="one")
        two = _share(source_id="two", value="90")
        selection = market_cap.ShareSlot([one, two]).advance(date(2020, 5, 8))

        self.assertEqual(selection.status, "AMBIGUOUS")
        self.assertIsNone(selection.observation)

    def test_amendment_applies_only_on_its_own_filing_date(self) -> None:
        original = _share(source_id="original")
        amendment = _share(source_id="amendment", value="90", filed=date(2020, 6, 1), form="10-Q/A")
        slot = market_cap.ShareSlot([original, amendment])

        before = slot.advance(date(2020, 5, 29))
        after = slot.advance(date(2020, 6, 5))

        self.assertEqual(before.observation.accession, "original")
        self.assertEqual(after.observation.accession, "amendment")


class TestSplitChainHelpers(unittest.TestCase):
    def test_split_direction_and_multiple_chain_are_explicit(self) -> None:
        events = [
            (date(2020, 8, 31), 4.0, True),
            (date(2021, 8, 2), 0.25, True),
        ]
        before_all, before_nonstandard, before_count = market_cap._chain_info(events, date(2020, 1, 1))
        between, between_nonstandard, between_count = market_cap._chain_info(events, date(2021, 1, 1))
        after_all, after_nonstandard, after_count = market_cap._chain_info(events, date(2022, 1, 1))

        self.assertEqual((before_all, before_nonstandard, before_count), (1.0, False, 2))
        self.assertEqual((between, between_nonstandard, between_count), (0.25, False, 1))
        self.assertEqual((after_all, after_nonstandard, after_count), (1.0, False, 0))

    def test_nonstandard_action_is_detected_not_silently_chained(self) -> None:
        factor, has_nonstandard, count = market_cap._chain_info(
            [(date(2021, 1, 1), 2.0, True), (date(2022, 1, 1), 1.398, False)],
            date(2020, 1, 1),
        )

        self.assertEqual(factor, 2.0)
        self.assertTrue(has_nonstandard)
        self.assertEqual(count, 2)

    def test_known_forward_and_reverse_factors_are_standard(self) -> None:
        self.assertTrue(market_cap._is_standard_split_factor(20.0))
        self.assertTrue(market_cap._is_standard_split_factor(0.1))
        self.assertFalse(market_cap._is_standard_split_factor(1.398))


class TestMarketCapSpecificationArtifacts(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = market_cap.output_paths(ROOT)

    def test_raw_package_manifest_is_intact(self) -> None:
        manifest = source_audit.audit_manifest(source_audit.package_root(ROOT))
        self.assertTrue(manifest["passed"])
        self.assertEqual(manifest["verified_payload_files"], 508)

    def test_generated_outputs_remain_specification_only(self) -> None:
        summary = json.loads(self.paths["summary"].read_text(encoding="utf-8"))
        acceptance = self.paths["acceptance"].read_text(encoding="utf-8")

        self.assertEqual(summary["evidence_stage"], market_cap.EVIDENCE_STAGE)
        self.assertEqual(summary["primary_share_pit_violations"], 0)
        self.assertEqual(summary["raw_manifest"]["verified_payload_files"], 508)
        self.assertEqual(summary["standard_split_event_count"], 227)
        self.assertEqual(summary["reverse_standard_split_event_count"], 10)
        self.assertEqual(summary["multiclass_security_count"], 6)
        self.assertIn("PASS WITH WARNINGS", acceptance)
        self.assertIn("No `us_weekly_market_cap_dev`", acceptance)
        self.assertFalse((ROOT / "data" / "canonical" / "us_weekly_market_cap_dev.csv").exists())
        self.assertFalse((ROOT / "data" / "canonical" / "us_weekly_market_cap_dev.sqlite").exists())

    def test_actual_split_events_reject_dynamic_multiplier_and_include_reverse_case(self) -> None:
        with self.paths["split_events"].open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        aapl_2014 = next(row for row in rows if row["ticker"] == "AAPL" and row["split_date"] == "2014-06-09")
        reverse_c = next(row for row in rows if row["ticker"] == "C" and row["split_date"] == "2011-05-09")

        self.assertEqual(aapl_2014["split_factor"], "7")
        self.assertEqual(aapl_2014["dynamic_chain_mechanical_discontinuity"], "FAIL")
        self.assertEqual(aapl_2014["delivery_basis_chain_discontinuity"], "PASS_NO_MECHANICAL_SPLIT_MULTIPLIER")
        self.assertEqual(reverse_c["split_factor"], "0.1")
        self.assertEqual(reverse_c["split_event_class"], "STANDARD_SPLIT_FACTOR")

    def test_multiclass_identity_is_never_silently_allocated(self) -> None:
        with self.paths["multiclass"].open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 6)
        self.assertEqual({row["cik"] for row in rows}, {"1564708", "1652044", "1754301"})
        self.assertTrue(all(row["market_cap_status"] == "MULTICLASS_UNRESOLVED" for row in rows))
        self.assertTrue(all(row["class_specific_shares_status"] == "NOT_DEMONSTRATED_BY_COMPANY_FACTS" for row in rows))

    def test_exact_price_audit_and_candidate_method_retain_missingness(self) -> None:
        with self.paths["price_basis"].open(newline="", encoding="utf-8") as handle:
            price_rows = {row["metric"]: row for row in csv.DictReader(handle)}
        with self.paths["methods"].open(newline="", encoding="utf-8") as handle:
            methods = {row["method_id"]: row for row in csv.DictReader(handle)}

        self.assertEqual(price_rows["missing_signal_date_bar"]["value"], "45090")
        self.assertEqual(price_rows["positive_exact_signal_close"]["value"], "495410")
        self.assertIn("no next-day or forward-filled", price_rows["missing_signal_date_bar"]["interpretation"])
        self.assertEqual(methods["SEC_YAHOO_STANDARD_SPLIT_CHAIN_ESTIMATE"]["market_cap_source"], "SEC_YAHOO_ESTIMATE_DEV")
        self.assertEqual(methods["SEC_YAHOO_STANDARD_SPLIT_CHAIN_ESTIMATE"]["recommendation"], "RECOMMENDED_BASELINE_SUBJECT_TO_SIGNOFF")
        self.assertEqual(methods["CRSP_FORMAL"]["recommendation"], "NOT_IMPLEMENTABLE_WITH_CURRENT_SOURCE")


if __name__ == "__main__":
    unittest.main()
