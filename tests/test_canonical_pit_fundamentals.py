"""Safety and generated-artifact checks for the DEV PIT fundamental input layer."""

from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import date
from pathlib import Path

from src.data import canonical_pit_fundamentals as pit
from src.data import source_audit


ROOT = pit.project_root()


def _candidate(
    *,
    source_id: str,
    value: str = "100",
    start: str = "2019-01-01",
    end: str = "2019-12-31",
    filed: str = "2020-02-15",
    form: str = "10-K",
    period_class: str = "annual_candidate",
    available: bool = True,
    ambiguity: bool = False,
    reason: str = "AVAILABLE_SOURCE",
) -> pit.FactCandidate:
    return pit.FactCandidate(
        source_observation_id=source_id,
        ticker="TEST",
        yahoo_ticker="TEST",
        cik=1,
        issuer="Test Issuer",
        economic_concept="net_income",
        taxonomy="us-gaap",
        xbrl_tag="NetIncomeLoss",
        source_value=value,
        value_token=value,
        unit="USD",
        start_date=start,
        end_date=end,
        period_length_days=364,
        period_class=period_class,
        fiscal_year="2019",
        fiscal_period="FY",
        form=form,
        filed_date=filed,
        accession=source_id,
        frame="CY2019",
        is_amendment=form.endswith("/A"),
        candidate_period_flag=True,
        availability_flag=available,
        ambiguity_flag=ambiguity,
        exclusion_reason=reason,
    )


class TestCanonicalPitSelection(unittest.TestCase):
    def test_amendment_is_unavailable_before_its_own_filing_and_available_after(self) -> None:
        original = _candidate(source_id="original")
        amendment = _candidate(source_id="amendment", value="90", filed="2020-03-20", form="10-K/A")
        slot = pit.SlotState({("2019-01-01", "2019-12-31"): [original, amendment]})

        before = slot.advance(date(2020, 3, 1))
        after = slot.advance(date(2020, 3, 27))

        self.assertEqual(before.status, "AVAILABLE")
        self.assertEqual(before.candidate.source_observation_id, "original")
        self.assertEqual(after.status, "AVAILABLE")
        self.assertEqual(after.candidate.source_observation_id, "amendment")

    def test_future_filing_does_not_leak_into_earlier_signal(self) -> None:
        original = _candidate(source_id="original")
        future = _candidate(
            source_id="future",
            value="90",
            filed="2021-02-15",
            end="2020-12-31",
            start="2020-01-01",
            form="10-K",
        )
        slot = pit.SlotState(
            {
                ("2019-01-01", "2019-12-31"): [original],
                ("2020-01-01", "2020-12-31"): [future],
            }
        )

        selection = slot.advance(date(2020, 12, 31))

        self.assertEqual(selection.status, "AVAILABLE")
        self.assertEqual(selection.candidate.source_observation_id, "original")

    def test_same_latest_filed_conflicting_values_are_ambiguous(self) -> None:
        one = _candidate(source_id="one", value="100")
        two = _candidate(source_id="two", value="90")
        selection = pit._resolve_latest_group([one, two])

        self.assertEqual(selection.status, "AMBIGUOUS")
        self.assertIsNone(selection.candidate)

    def test_same_value_nonunique_provenance_is_open_not_silently_selected(self) -> None:
        one = _candidate(source_id="one", value="100")
        two = _candidate(source_id="two", value="100", form="10-K/A")
        selection = pit._resolve_latest_group([one, two])

        self.assertEqual(selection.status, "OPEN_IMPLEMENTATION_ISSUE")
        self.assertIsNone(selection.candidate)

    def test_negative_primary_capex_is_sign_exception_without_transformation(self) -> None:
        rule = pit.CONCEPT_BY_NAME["capital_expenditure"]
        result = pit._classify_source_observation(
            rule,
            "us-gaap",
            "USD",
            {
                "val": "-20",
                "start": "2019-01-01",
                "end": "2019-12-31",
                "filed": "2020-02-15",
                "form": "10-K",
                "fp": "FY",
            },
        )

        self.assertEqual(result[0], "annual_candidate")
        self.assertTrue(result[2])
        self.assertFalse(result[3])
        self.assertEqual(result[4], "SIGN_EXCEPTION_NEGATIVE_CAPEX")

    def test_noncanonical_unit_is_rejected_without_conversion(self) -> None:
        rule = pit.CONCEPT_BY_NAME["net_income"]
        result = pit._classify_source_observation(
            rule,
            "us-gaap",
            "EUR",
            {
                "val": "20",
                "start": "2019-01-01",
                "end": "2019-12-31",
                "filed": "2020-02-15",
                "form": "10-K",
                "fp": "FY",
            },
        )

        self.assertFalse(result[2])
        self.assertFalse(result[3])
        self.assertEqual(result[4], "UNIT_REJECTED")

    def test_instant_and_duration_periods_are_never_interchanged(self) -> None:
        assets = pit.CONCEPT_BY_NAME["assets"]
        net_income = pit.CONCEPT_BY_NAME["net_income"]
        duration_for_assets = pit._classify_source_observation(
            assets,
            "us-gaap",
            "USD",
            {
                "val": "100",
                "start": "2019-01-01",
                "end": "2019-03-31",
                "filed": "2019-05-01",
                "form": "10-Q",
                "fp": "Q1",
            },
        )
        instant_for_net_income = pit._classify_source_observation(
            net_income,
            "us-gaap",
            "USD",
            {
                "val": "100",
                "end": "2019-03-31",
                "filed": "2019-05-01",
                "form": "10-Q",
                "fp": "Q1",
            },
        )

        self.assertEqual(duration_for_assets[0], "quarterly_candidate")
        self.assertEqual(duration_for_assets[4], "PERIOD_CLASS_MISMATCH")
        self.assertEqual(instant_for_net_income[0], "instant")
        self.assertEqual(instant_for_net_income[4], "PERIOD_CLASS_MISMATCH")

    def test_future_period_end_is_an_explicit_open_issue(self) -> None:
        rule = pit.CONCEPT_BY_NAME["assets"]
        result = pit._classify_source_observation(
            rule,
            "us-gaap",
            "USD",
            {"val": "100", "end": "2020-12-31", "filed": "2020-11-01", "form": "10-Q", "fp": "Q3"},
        )

        self.assertFalse(result[2])
        self.assertEqual(result[4], "OPEN_IMPLEMENTATION_ISSUE_FUTURE_PERIOD_END")


class TestCanonicalPitArtifacts(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = pit.output_paths(ROOT)

    def test_raw_manifest_remains_intact(self) -> None:
        manifest = source_audit.audit_manifest(source_audit.package_root(ROOT))
        self.assertTrue(manifest["passed"])
        self.assertEqual(manifest["verified_payload_files"], 508)

    def test_generated_sqlite_contracts_have_pit_safe_unique_provenance(self) -> None:
        facts = sqlite3.connect(self.paths["facts_db"])
        weekly = sqlite3.connect(self.paths["weekly_db"])
        try:
            self.assertEqual(
                facts.execute("SELECT COUNT(*) FROM canonical_fundamental_facts").fetchone()[0],
                469705,
            )
            self.assertEqual(
                weekly.execute("SELECT COUNT(*) FROM weekly_pit_fundamental_snapshot WHERE filed_date > signal_date").fetchone()[0],
                0,
            )
            self.assertEqual(
                weekly.execute(
                    "SELECT COUNT(*) FROM ("
                    "SELECT ticker, signal_date, economic_concept, period_class, COUNT(*) n "
                    "FROM weekly_pit_fundamental_snapshot "
                    "GROUP BY ticker, signal_date, economic_concept, period_class HAVING n <> 1)"
                ).fetchone()[0],
                0,
            )
            fact_sources = {
                row
                for row in facts.execute(
                    "SELECT ticker, source_observation_id FROM canonical_fundamental_facts"
                )
            }
            snapshot_sources = {
                row
                for row in weekly.execute(
                    "SELECT DISTINCT ticker, source_observation_id "
                    "FROM weekly_pit_fundamental_snapshot"
                )
            }
            self.assertTrue(snapshot_sources.issubset(fact_sources))
            self.assertEqual(
                weekly.execute("SELECT COUNT(*) FROM weekly_security_universe").fetchone()[0],
                503 * 1081,
            )
        finally:
            facts.close()
            weekly.close()

    def test_generated_summary_and_reports_stay_in_dev_scope(self) -> None:
        summary = json.loads(self.paths["summary"].read_text(encoding="utf-8"))
        acceptance = self.paths["acceptance"].read_text(encoding="utf-8")
        self.assertEqual(summary["evidence_stage"], pit.EVIDENCE_STAGE)
        self.assertEqual(summary["weekly_counts"]["pit_violations"], 0)
        self.assertEqual(summary["raw_manifest"]["verified_payload_files"], 508)
        self.assertIn("PASS WITH WARNINGS", acceptance)
        self.assertIn("No ROE, ROA, GP/A, Leverage, Asset Growth, FCF", acceptance)
        self.assertTrue(self.paths["coverage"].is_file())
        self.assertTrue(self.paths["staleness"].is_file())
        self.assertTrue(self.paths["ambiguity"].is_file())
        self.assertTrue(self.paths["period_coverage"].is_file())


if __name__ == "__main__":
    unittest.main()
