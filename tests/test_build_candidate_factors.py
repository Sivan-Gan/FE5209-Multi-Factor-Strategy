"""Focused unit tests for the bounded Formal-US candidate-factor builder."""

from __future__ import annotations

import sqlite3
import statistics
import tempfile
import unittest
from pathlib import Path

from src.factors import build_candidate_factors as candidate


class TestCandidateFactorHelpers(unittest.TestCase):
    def test_finite_rejects_non_finite_and_text(self) -> None:
        self.assertEqual(candidate.finite("1.25"), 1.25)
        self.assertIsNone(candidate.finite("not-a-number"))
        self.assertIsNone(candidate.finite(float("nan")))

    def test_quantile_interpolates_deterministically(self) -> None:
        self.assertEqual(candidate.q([4.0, 1.0, 3.0, 2.0], 0.5), 2.5)
        self.assertAlmostEqual(candidate.q(list(range(100)), 0.01), 0.99)

    def test_compounded_return(self) -> None:
        self.assertAlmostEqual(candidate.compounded([0.10, -0.05]), 0.045)

    def test_compounded_rejects_missing_return(self) -> None:
        self.assertIsNone(candidate.compounded([0.01, None]))

    def test_compounded_rejects_negative_one_or_worse(self) -> None:
        self.assertIsNone(candidate.compounded([0.01, -1.0]))
        self.assertIsNone(candidate.compounded([0.01, -1.01]))

    def test_compounded_rejects_empty_window(self) -> None:
        self.assertIsNone(candidate.compounded([]))

    def test_momentum_windows_have_fixed_lengths_and_skip(self) -> None:
        returns = list(range(300))
        twelve, six = candidate.momentum_windows(returns, 280)
        self.assertEqual((len(twelve), len(six)), (231, 105))
        self.assertEqual((twelve[0], twelve[-1]), (28, 258))
        self.assertEqual((six[0], six[-1]), (154, 258))
        self.assertEqual(returns[259:280], list(range(259, 280)))

    def test_momentum_requires_full_history(self) -> None:
        twelve, six = candidate.momentum_windows([0.01] * 251, 251)
        self.assertEqual(twelve, [])
        self.assertEqual(len(six), 105)

    def test_equal_available_uses_only_available_metrics(self) -> None:
        self.assertEqual(candidate.equal_available([2.0, 4.0], 1), 3.0)
        self.assertEqual(candidate.equal_available([2.0], 2), None)

    def test_preprocess_winsorizes_and_standardizes_same_date(self) -> None:
        rows = [{"signal_date": "2020-01-03", "m_raw": float(i)} for i in range(100)]
        qa: list[dict[str, object]] = []
        candidate.preprocess(rows, "m", qa)
        values = [row["m_z"] for row in rows]
        self.assertAlmostEqual(rows[0]["m_winsor"], 0.99)
        self.assertAlmostEqual(rows[-1]["m_winsor"], 98.01)
        self.assertAlmostEqual(statistics.mean(values), 0.0, places=12)
        self.assertAlmostEqual(statistics.stdev(values), 1.0, places=12)
        self.assertEqual(qa[0]["clipped_low"], 1)
        self.assertEqual(qa[0]["clipped_high"], 1)

    def test_preprocess_preserves_missing(self) -> None:
        rows = [
            {"signal_date": "2020-01-03", "m_raw": 1.0},
            {"signal_date": "2020-01-03", "m_raw": None},
            {"signal_date": "2020-01-03", "m_raw": 2.0},
        ]
        candidate.preprocess(rows, "m", [])
        self.assertIsNone(rows[1]["m_winsor"])
        self.assertIsNone(rows[1]["m_z"])

    def test_preprocess_zero_standard_deviation_is_unavailable(self) -> None:
        rows = [{"signal_date": "2020-01-03", "m_raw": 2.0} for _ in range(3)]
        qa: list[dict[str, object]] = []
        candidate.preprocess(rows, "m", qa)
        self.assertEqual([row["m_z"] for row in rows], [None, None, None])
        self.assertEqual(qa[0]["zero_std_flag"], "true")

    def test_audit_csv_writer_emits_lf_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.csv"
            candidate.write_csv(path, ("value",), [{"value": "x"}])
            content = path.read_bytes()
        self.assertEqual(content, b"value\nx\n")

    def test_trailing_volatility_requires_200_returns(self) -> None:
        vol, ivol, beta, n = candidate.trailing_stats([0.01] * 199, [0.01] * 199)
        self.assertEqual((vol, ivol, beta, n), (None, None, None, 199))

    def test_trailing_stats_uses_residual_not_total_volatility(self) -> None:
        market = [0.001 * i for i in range(200)]
        security = [0.002 + 1.5 * value for value in market]
        vol, ivol, beta, n = candidate.trailing_stats(security, market)
        self.assertEqual(n, 200)
        self.assertGreater(vol or 0, 0)
        self.assertAlmostEqual(beta or 0, 1.5, places=12)
        self.assertAlmostEqual(ivol or 0, 0.0, places=12)

    def test_trailing_stats_rejects_zero_market_variance_for_ivol(self) -> None:
        vol, ivol, beta, n = candidate.trailing_stats([0.001 * i for i in range(200)], [0.0] * 200)
        self.assertGreater(vol or 0, 0)
        self.assertEqual((ivol, beta, n), (None, None, 200))

    def test_sample_permission_labels_are_not_outcomes(self) -> None:
        self.assertEqual(candidate.sample_label("2005-12-31"), "INITIALIZATION")
        self.assertEqual(candidate.sample_label("2011-01-01"), "FACTOR_SELECTION")
        self.assertEqual(candidate.sample_label("2021-01-01"), "HIST_OOS_BASE")


class TestCandidateFactorSchema(unittest.TestCase):
    def test_schema_has_required_identity_availability_branch_and_provenance_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            con = sqlite3.connect(Path(directory) / "factors.sqlite")
            candidate.schema(con)
            columns = {row[1] for row in con.execute("PRAGMA table_info(factors)")}
            con.close()
        required = {
            "signal_date", "permno", "size_mcap_raw", "mom12_1_raw", "mom6_1_raw",
            "vol_252_raw", "vol_obs", "vol_start", "vol_end", "ivol_252_raw", "ivol_obs", "ivol_start", "ivol_end", "ep_raw", "bp_raw", "fcf_yield_raw",
            "roe_raw", "gpa_raw", "leverage_raw", "asset_growth_raw", "dividend_yield_raw",
            "branch", "financial_real_estate_flag", "gics_available_flag", "availability_json",
            "provenance_json", "momentum_available", "lowrisk_available",
        }
        self.assertTrue(required.issubset(columns))

    def test_schema_has_no_forward_return_or_portfolio_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            con = sqlite3.connect(Path(directory) / "factors.sqlite")
            candidate.schema(con)
            columns = {row[1] for row in con.execute("PRAGMA table_info(factors)")}
            con.close()
        forbidden = {"forward_return", "portfolio_return", "long_short_return", "ic", "fmom"}
        self.assertFalse(columns & forbidden)

    def test_constants_lock_formal_cutoff_and_canonical_source(self) -> None:
        self.assertEqual(candidate.FORMAL_CUTOFF, "2025-12-31")
        self.assertEqual(str(candidate.CANONICAL), "data/canonical/wrds_formal/us_wrds_formal_canonical.sqlite")

    def test_blockers_are_explicit_not_silent_substitutions(self) -> None:
        self.assertIn("CONFIRMATION_REQUIRED", candidate.BLOCKED["size_final"])
        self.assertIn("CANONICAL_DEPENDENCY", candidate.BLOCKED["dividend_final"])


if __name__ == "__main__":
    unittest.main()
