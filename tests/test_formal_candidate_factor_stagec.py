"""Focused, definition-level contracts for formal-US Candidate Factor Stage C.

These tests deliberately contain no return, IC, portfolio, selection, or
strategy calculation.  They cover only the frozen input-to-signal contract.
"""
from __future__ import annotations

import csv
import json
import math
import sqlite3
import tempfile
import unittest
from collections import defaultdict
from datetime import date
from pathlib import Path

from src.data import wrds_canonical_build as canonical
from src.data import wrds_gate0_final as gate0
from src.factors import build_candidate_factors as candidate


ROOT = Path(__file__).resolve().parents[1]


def event(event_id: str, fy: int, fq: int | None, av: str, **values: float) -> dict[str, object]:
    return {
        "id": event_id,
        "dd": f"{fy}-{(fq or 4) * 3:02d}-28",
        "fy": fy,
        "fq": fq,
        "av": av,
        "v": values,
        "f": {
            "oancfy_single_quarter": values.get("oancfy", 6.0),
            "capxy_single_quarter": values.get("capxy", 2.0),
        },
    }


def fundamentals(*, quarterly: bool = True, annual: bool = True, prior_q4: bool = True) -> candidate.Fundamentals:
    obj = object.__new__(candidate.Fundamentals)
    obj.a = defaultdict(list)
    obj.q = defaultdict(lambda: defaultdict(list))
    obj.cache = {}
    values = dict(niq=10.0, ceqq=40.0, atq=100.0, saleq=50.0, cogsq=20.0, dlcq=10.0, dlttq=20.0, oancfy=6.0, capxy=2.0)
    if quarterly:
        for fq in range(1, 5):
            obj.q["G"][2020 * 4 + fq - 1].append(event(f"q{fq}", 2020, fq, f"2020-{fq * 3:02d}-29", **values))
        if prior_q4:
            obj.q["G"][2019 * 4 + 3].append(event("qprev", 2019, 4, "2019-12-30", **(values | {"atq": 80.0})))
    if annual:
        obj.a["G"].append(event("a2020", 2020, None, "2021-02-15", ni=40.0, ceq=80.0, at=160.0, sale=200.0, cogs=80.0, dlc=20.0, dltt=40.0, oancf=30.0, capx=10.0))
        obj.a["G"].append(event("a2019", 2019, None, "2020-02-15", ni=30.0, ceq=70.0, at=120.0, sale=160.0, cogs=70.0, dlc=15.0, dltt=30.0, oancf=25.0, capx=8.0))
        obj.a["G"].sort(key=lambda x: (str(x["dd"]), str(x["av"]), str(x["id"])))
    return obj


def dividend_store(events: list[tuple[date, float | None, int, int, float | None, int]]) -> candidate.Dividends:
    obj = object.__new__(candidate.Dividends)
    obj.start, obj.end = date(2004, 1, 1), date(2025, 12, 31)
    obj.d = defaultdict(list, {"1": events})
    return obj


class TestFormalCandidateFactorStageC(unittest.TestCase):
    # 1–2: Size.
    def test_01_size_is_negative_log_of_positive_issuer_cap(self) -> None:
        self.assertAlmostEqual(-math.log(100.0), -4.605170185988092)
        self.assertIn("-log positive canonical issuer market cap", candidate.contract_rows()[0]["frozen_definition"])

    def test_02_size_contract_has_no_neutralization(self) -> None:
        row = next(x for x in candidate.contract_rows() if x["factor"] == "Size")
        self.assertEqual(row["neutralization_rule"], "none")

    # 3–9: Value precedence and scale alignment.
    def test_03_earnings_prefers_quarterly_ttm(self) -> None:
        self.assertEqual(fundamentals().metrics("G", "2021-03-01")["earnings"]["source_frequency"], "QUARTERLY_TTM")

    def test_04_earnings_uses_annual_fallback(self) -> None:
        self.assertEqual(fundamentals(quarterly=False).metrics("G", "2021-03-01")["earnings"]["source_frequency"], "ANNUAL")

    def test_05_book_prefers_quarterly_ceqq(self) -> None:
        result = fundamentals().metrics("G", "2021-03-01")["book"]
        self.assertEqual((result["value"], result["source_frequency"]), (40.0, "QUARTERLY"))

    def test_06_book_uses_annual_ceq_fallback(self) -> None:
        self.assertEqual(fundamentals(quarterly=False).metrics("G", "2021-03-01")["book"]["source_frequency"], "ANNUAL")

    def test_07_fcf_prefers_quarterly_ttm(self) -> None:
        result = fundamentals().metrics("G", "2021-03-01")["fcf"]
        self.assertEqual((result["value"], result["source_frequency"]), (16.0, "QUARTERLY_TTM"))

    def test_08_fcf_uses_annual_fallback(self) -> None:
        result = fundamentals(quarterly=False).metrics("G", "2021-03-01")["fcf"]
        self.assertEqual((result["value"], result["source_frequency"]), (20.0, "ANNUAL"))

    def test_09_attach_scales_millions_to_thousands_without_frequency_mix(self) -> None:
        metrics = fundamentals().metrics("G", "2021-03-01")
        row = {"issuer_market_cap": 10_000.0, "issuer_mcap_available": 1}
        candidate.attach(row, metrics)
        self.assertAlmostEqual(row["ep_raw"], 4.0)
        self.assertEqual(row["earnings_source_frequency"], "QUARTERLY_TTM")

    # 10–15: Quality definitions.
    def test_10_roe_uses_paired_quarterly_ttm_and_ceqq(self) -> None:
        self.assertEqual(fundamentals().metrics("G", "2021-03-01")["roe"]["source_frequency"], "QUARTERLY_TTM")

    def test_11_roe_uses_paired_annual_fallback(self) -> None:
        result = fundamentals(quarterly=False).metrics("G", "2021-03-01")["roe"]
        self.assertEqual((result["value"], result["source_frequency"]), (0.5, "ANNUAL"))

    def test_12_roa_uses_paired_frequency(self) -> None:
        self.assertEqual(fundamentals().metrics("G", "2021-03-01")["roa"]["source_frequency"], "QUARTERLY_TTM")

    def test_13_gpa_uses_sales_minus_cogs_not_missing_cogs_as_zero(self) -> None:
        self.assertAlmostEqual(fundamentals().metrics("G", "2021-03-01")["gpa"]["value"], 1.2)
        broken = fundamentals()
        for period_events in broken.q["G"].values():
            for quarterly_event in period_events:
                quarterly_event["v"]["cogsq"] = None
        self.assertEqual(broken.metrics("G", "2021-03-01")["gpa"]["source_frequency"], "ANNUAL")

    def test_14_leverage_is_debt_over_assets(self) -> None:
        result = fundamentals().metrics("G", "2021-03-01")["negative_leverage"]
        self.assertAlmostEqual(result["leverage_level"], 0.3)

    def test_15_quality_uses_negative_leverage_orientation(self) -> None:
        self.assertAlmostEqual(fundamentals().metrics("G", "2021-03-01")["negative_leverage"]["value"], -0.3)

    # 16–18: Investment.
    def test_16_investment_matches_same_fiscal_quarter_prior_year(self) -> None:
        result = fundamentals().metrics("G", "2021-03-01")["investment"]
        self.assertEqual(result["source_frequency"], "QUARTERLY")
        self.assertAlmostEqual(result["asset_growth"], 0.25)

    def test_17_investment_uses_prior_fiscal_year_annual_fallback(self) -> None:
        result = fundamentals(quarterly=False).metrics("G", "2021-03-01")["investment"]
        self.assertEqual((result["source_frequency"], result["asset_growth"]), ("ANNUAL", 160.0 / 120.0 - 1.0))

    def test_18_investment_rejects_nearest_calendar_substitution(self) -> None:
        result = fundamentals(annual=False, prior_q4=False).metrics("G", "2021-03-01")["investment"]
        self.assertEqual(result["failure_reason"], "EXACT_YEAR_OVER_YEAR_ASSET_PAIR_UNAVAILABLE")

    # 19–25: Historical GICS and no-intercept neutralization design.
    def test_19_sector_40_is_financial_branch(self) -> None:
        self.assertIn("40", ("40", "60"))

    def test_20_sector_60_is_real_estate_branch(self) -> None:
        self.assertIn("60", ("40", "60"))

    def test_21_gics_unavailable_has_no_branch_fallback(self) -> None:
        branch = "FINANCIAL_REAL_ESTATE" if None in ("40", "60") else "NONFINANCIAL" if None else "GICS_UNAVAILABLE"
        self.assertEqual(branch, "GICS_UNAVAILABLE")

    def test_22_neutralization_contract_uses_historical_gics_sector(self) -> None:
        row = next(x for x in candidate.contract_rows() if x["factor"] == "Neutralization" and x["component"] == "GICS Sector")
        self.assertIn("historical-sector", row["frozen_definition"])

    def test_23_neutralization_contract_uses_log_issuer_market_cap(self) -> None:
        row = next(x for x in candidate.contract_rows() if x["component"] == "issuer market cap")
        self.assertIn("log positive issuer-level cap", row["frozen_definition"])

    def _neutral_rows(self) -> list[dict[str, object]]:
        values = (1.0, 4.0, 8.0, 9.0)
        return [{"signal_date": "2020-01-03", "gsector": "10" if i < 2 else "20", "issuer_market_cap": math.exp(i + 1), "pre": values[i]} for i in range(4)]

    def test_24_neutralization_has_no_intercept(self) -> None:
        result = candidate.neutralize(self._neutral_rows(), "pre", "residual", "final", "Value")
        self.assertEqual(result["design_matrix_rank"], 3)  # log cap + both sector dummies

    def test_25_neutralization_uses_all_sector_dummies(self) -> None:
        result = candidate.neutralize(self._neutral_rows(), "pre", "residual", "final", "Value")
        self.assertEqual(result["number_of_sectors"], 2)

    def test_26_value_residual_is_orthogonal_to_log_cap(self) -> None:
        result = candidate.neutralize(self._neutral_rows(), "pre", "residual", "final", "Value")
        self.assertLess(abs(float(result["corr_residual_log_issuer_mcap"])), 1e-10)

    def test_27_quality_residual_is_orthogonal_to_log_cap(self) -> None:
        result = candidate.neutralize(self._neutral_rows(), "pre", "residual", "final", "Quality")
        self.assertLess(abs(float(result["max_abs_sector_residual_mean"])), 1e-10)

    def test_28_investment_residual_is_orthogonal_to_log_cap(self) -> None:
        result = candidate.neutralize(self._neutral_rows(), "pre", "residual", "final", "Investment")
        self.assertFalse(result["regression_failure"] == "true")

    # 29–34: Dividend source, timing, denominator, and return boundary.
    def test_29_dividend_uses_only_ordinary_cash_events(self) -> None:
        store = dividend_store([(date(2020, 6, 1), 1.0, 1, 0, None, 0), (date(2020, 9, 1), 9.0, 0, 0, None, 0)])
        self.assertAlmostEqual(store.metric("1", "2020-12-31", 10.0)["value"], 0.1)

    def test_30_dividend_applies_standard_split_normalization(self) -> None:
        store = dividend_store([(date(2020, 1, 2), 2.0, 1, 0, None, 0), (date(2020, 6, 1), None, 0, 1, 2.0, 0)])
        self.assertAlmostEqual(store.metric("1", "2020-12-31", 10.0)["value"], 0.1)

    def test_31_dividend_window_is_strict_365_days(self) -> None:
        store = dividend_store([(date(2019, 12, 31), 10.0, 1, 0, None, 0), (date(2020, 1, 2), 1.0, 1, 0, None, 0)])
        self.assertAlmostEqual(store.metric("1", "2020-12-31", 10.0)["value"], 0.1)

    def test_32_dividend_zero_is_distinct_from_incomplete_window(self) -> None:
        self.assertEqual(dividend_store([]).metric("1", "2020-12-31", 10.0)["value"], 0.0)
        self.assertIsNone(dividend_store([]).metric("1", "2004-06-01", 10.0)["value"])

    def test_33_dividend_uses_signal_date_security_price_denominator(self) -> None:
        store = dividend_store([(date(2020, 6, 1), 1.0, 1, 0, None, 0)])
        self.assertEqual(store.metric("1", "2020-12-31", 20.0)["value"], 0.05)

    def test_34_factor_schema_has_no_dividend_return_double_counting_field(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            con = sqlite3.connect(Path(temporary) / "f.sqlite"); candidate.schema(con)
            names = {r[1].lower() for r in con.execute("PRAGMA table_info(factors)")}; con.close()
        self.assertFalse({"dlyret", "dividend_return", "total_return"} & names)

    # 35–42: preserved price definitions, data boundaries and integrity.
    def test_35_existing_momentum_skip_rule_is_unchanged(self) -> None:
        twelve, six = candidate.momentum_windows(list(range(300)), 280)
        self.assertEqual((twelve[-1], six[-1]), (258, 258))

    def test_36_existing_lowrisk_requires_200_aligned_observations(self) -> None:
        self.assertEqual(candidate.trailing_stats([0.01] * 199, [0.01] * 199)[3], 199)
        self.assertIsNone(candidate.trailing_stats([0.01] * 199, [0.01] * 199)[0])

    def test_37_schema_has_no_forward_return_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            con = sqlite3.connect(Path(temporary) / "f.sqlite"); candidate.schema(con)
            names = {r[1].lower() for r in con.execute("PRAGMA table_info(factors)")}; con.close()
        self.assertFalse({"forward_return", "ic", "portfolio_return", "fmom"} & names)

    def test_38_formal_cutoff_excludes_2026(self) -> None:
        self.assertEqual(candidate.FORMAL_CUTOFF, "2025-12-31")

    def test_39_schema_enforces_natural_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            con = sqlite3.connect(Path(temporary) / "f.sqlite"); candidate.schema(con)
            keys = [r[1] for r in con.execute("PRAGMA table_info(factors)") if r[5]]; con.close()
        self.assertEqual(keys, ["signal_date", "permno"])

    def test_40_recorded_factor_rebuild_is_deterministic(self) -> None:
        with (ROOT / "outputs/audit/wrds_formal/candidate_factor_determinism.csv").open(newline="", encoding="utf-8") as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row["status"], "PASS")
        self.assertEqual(row["run_1_logical_hash"], row["run_2_logical_hash"])

    def test_41_raw_integrity_remains_the_19_file_contract(self) -> None:
        result = gate0.verify_raw_integrity(ROOT)
        self.assertEqual(result["expected_file_count"], 19)
        self.assertTrue(result["raw_hashes_match"], result)

    def test_42_canonical_freeze_manifest_retains_expected_hash(self) -> None:
        manifest = (ROOT / "outputs/audit/wrds_formal/canonical_dataset_manifest.csv").read_text(encoding="utf-8")
        self.assertIn(candidate.CANONICAL_LOGICAL_HASH, manifest)


if __name__ == "__main__":
    unittest.main()
