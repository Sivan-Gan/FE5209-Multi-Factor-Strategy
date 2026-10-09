"""Focused safety tests for availability-only market-cap sign-off outputs."""

from __future__ import annotations

import csv
import unittest
from datetime import date
from pathlib import Path

from src.data import historical_market_cap_signoff_audit as signoff
from src.data import historical_market_cap_spec_audit as spec
from src.data import source_audit


ROOT = signoff.project_root()


def _share(value: str, filed: date) -> spec.ShareObservation:
    return spec.ShareObservation(
        cik=1, taxonomy="dei", tag="EntityCommonStockSharesOutstanding", unit="shares",
        value_text=value, value=float(value), end_date=date(2020, 3, 31), filed_date=filed,
        form="10-Q", accession=f"acc-{value}", frame="", fiscal_year="2020", fiscal_period="Q1",
        start_date="", ordinal=0,
    )


class TestSignoffRules(unittest.TestCase):
    def test_small_share_classification_is_context_aware(self) -> None:
        self.assertEqual(signoff.classify_small_share(0, False, True), "OBVIOUS_INVALID_ZERO")
        self.assertEqual(signoff.classify_small_share(0.5, False, False), "UNRESOLVED_FRACTIONAL_OR_SCALE_CONTEXT")
        self.assertEqual(signoff.classify_small_share(1, False, True), "LIKELY_XBRL_CONTEXT_OR_SCALE_ISSUE")

    def test_standard_and_nonstandard_action_separation(self) -> None:
        self.assertTrue(spec._is_standard_split_factor(7.0))
        self.assertTrue(spec._is_standard_split_factor(0.1))
        self.assertFalse(spec._is_standard_split_factor(1.398))
        self.assertEqual(signoff.classify_nonstandard_action(1.398)[0], "NONSTANDARD_UPWARD_FACTOR")

    def test_staleness_cutoff_is_an_explicit_sensitivity(self) -> None:
        self.assertEqual(signoff.STALENESS_SCENARIOS, (None, 120, 180, 270, 365))
        self.assertTrue(133 <= 180)
        self.assertFalse(133 <= 120)

    def test_no_future_share_selection(self) -> None:
        old, future = _share("100", date(2020, 5, 1)), _share("200", date(2020, 8, 1))
        selected = spec.ShareSlot([old, future]).advance(date(2020, 7, 31))
        self.assertEqual(selected.status, "AVAILABLE")
        self.assertEqual(selected.observation.value, 100)

    def test_no_future_price_or_fill(self) -> None:
        self.assertEqual(signoff.price_status(None), ("NO_SIGNAL_DATE_CLOSE", False))

    def test_zero_volume_and_invalid_price_rules_are_separate(self) -> None:
        zero_volume = {"close": "10", "is_ohlc_plausible": "true", "is_zero_volume": "true", "is_review_security": "false"}
        invalid = {"close": "10", "is_ohlc_plausible": "false", "is_zero_volume": "false", "is_review_security": "false"}
        self.assertEqual(signoff.price_status(zero_volume), ("PRICE_REVIEW_FLAG_ZERO_VOLUME", True))
        self.assertEqual(signoff.price_status(invalid), ("PRICE_INVALID", False))

    def test_multiclass_and_missing_reason_assignment_are_unavailable(self) -> None:
        self.assertEqual(
            signoff.candidate_reason(
                share_status="AVAILABLE", raw_records=[], share_value=100.0,
                is_multiclass=True, usable_price=True, price_code="PRICE_VALID", nonstandard=False,
            ),
            "MULTICLASS_UNRESOLVED",
        )
        self.assertEqual(
            signoff.candidate_reason(
                share_status="NO_CANDIDATE", raw_records=[], share_value=None,
                is_multiclass=False, usable_price=True, price_code="PRICE_VALID", nonstandard=False,
            ),
            "NO_SEC_SHARES",
        )

    def test_value_two_of_three_is_availability_not_a_ratio(self) -> None:
        self.assertTrue(signoff.value_two_of_three(True, True, False))
        self.assertFalse(signoff.value_two_of_three(True, False, False))


class TestSignoffArtifacts(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = signoff.paths(ROOT)

    def test_required_reason_codes_and_multiclass_safety_are_present(self) -> None:
        with self.paths["reasons"].open(newline="", encoding="utf-8") as handle:
            codes = {row["reason_code"] for row in csv.DictReader(handle)}
        self.assertTrue(set(signoff.REQUIRED_REASON_CODES).issubset(codes))
        self.assertIn("MULTICLASS_UNRESOLVED", codes)

    def test_small_share_and_value_availability_outputs_are_not_market_cap_values(self) -> None:
        with self.paths["small"].open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        distribution = {row["value_bucket"]: row["raw_source_observation_count"] for row in rows if row["record_type"] == "RAW_VALUE_DISTRIBUTION"}
        self.assertEqual(distribution, {"LESS_THAN_ZERO": "0", "EQUAL_ZERO": "31", "BETWEEN_ZERO_AND_ONE": "0", "EQUAL_ONE": "4"})
        self.assertFalse((ROOT / "data" / "canonical" / "us_weekly_market_cap_dev.sqlite").exists())
        self.assertFalse((ROOT / "data" / "canonical" / "us_weekly_market_cap_dev.csv").exists())

    def test_value_component_and_two_of_three_outputs_are_availability_only(self) -> None:
        with self.paths["value"].open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        values = {(row["measure"], row["window"]): float(row["mean_weekly_share"]) for row in rows}
        for window in ("factor_selection_2011_2015", "configuration_selection_2016_2020", "historical_oos_2021_2026_09"):
            self.assertLessEqual(values[("value_two_of_three", window)], values[("market_cap_input", window)])
            self.assertIn(("ep_input", window), values)
            self.assertIn(("bp_input", window), values)
            self.assertIn(("fcf_yield_input", window), values)

    def test_raw_manifest_and_acceptance_scope(self) -> None:
        manifest = source_audit.audit_manifest(source_audit.package_root(ROOT))
        report = self.paths["report"].read_text(encoding="utf-8")
        self.assertTrue(manifest["passed"])
        self.assertEqual(manifest["verified_payload_files"], 508)
        self.assertIn("PASS WITH WARNINGS", report)
        self.assertIn("No market-cap value or panel", report)


if __name__ == "__main__":
    unittest.main()
