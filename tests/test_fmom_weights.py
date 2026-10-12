"""Synthetic checks for Steps 17-19 (FMOM timing and weight arithmetic).

Run from the repository root:  python -m unittest tests.test_fmom_weights
No real data is needed.
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from src.strategy import fmom_weights as fw


def synthetic_history(n_weeks: int = 140, value: float = 0.01) -> pd.DataFrame:
    """Fridays as S_t, next Monday as E_t, so E_{t+1} is the Monday after next Friday."""
    fridays = pd.date_range("2014-01-03", periods=n_weeks, freq="W-FRI")
    rows = []
    for i, s in enumerate(fridays):
        next_exec = (s + pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        for family in fw.FAMILIES:
            rows.append({"signal_date": s.strftime("%Y-%m-%d"), "next_execution_date": next_exec,
                         "family": family, "factor_return_gross": value,
                         "factor_return_available": 1, "source": "test"})
    return pd.DataFrame(rows)


def run(hist: pd.DataFrame, J: int, lam: float, ra: bool = False, last_n: int = 1) -> pd.DataFrame:
    wide, next_exec = fw.to_wide(hist)
    return fw.compute_weights(wide, next_exec, list(wide.index[-last_n:]), J, lam, ra)


class FmomTimingTest(unittest.TestCase):
    def test_window_ends_two_weeks_back(self):
        hist = synthetic_history()
        dates = sorted(hist["signal_date"].unique())
        out = run(hist, J=26, lam=0.5)
        self.assertEqual(out["window_last_signal_date"].iloc[0], dates[-3])
        self.assertLessEqual(out["window_last_next_execution_date"].iloc[0], dates[-1])

    def test_in_flight_returns_are_ignored(self):
        hist = synthetic_history()
        dates = sorted(hist["signal_date"].unique())
        base = run(hist, J=26, lam=1.0)
        # make the two not-yet-completed weeks absurd: weights must not move
        hist.loc[hist["signal_date"].isin(dates[-2:]) & (hist["family"] == "Quality"),
                 "factor_return_gross"] = 100.0
        moved = run(hist, J=26, lam=1.0)
        np.testing.assert_allclose(base["weight"], moved["weight"])

    def test_fmom_is_sum_of_last_J(self):
        hist = synthetic_history(value=0.01)
        out = run(hist, J=13, lam=0.0)
        np.testing.assert_allclose(out["fmom"], 0.13)


class FmomWeightTest(unittest.TestCase):
    def test_missing_return_falls_back_to_equal(self):
        hist = synthetic_history()
        dates = sorted(hist["signal_date"].unique())
        hist.loc[(hist["signal_date"] == dates[-5]) & (hist["family"] == "Momentum"),
                 "factor_return_available"] = 0
        out = run(hist, J=26, lam=1.0)
        self.assertEqual(out["dynamic_status"].iloc[0], "FALLBACK_MISSING_FACTOR_RETURN")
        np.testing.assert_allclose(out["weight"], 0.25)

    def test_short_history_falls_back(self):
        out = run(synthetic_history(n_weeks=20), J=26, lam=1.0)
        self.assertEqual(out["dynamic_status"].iloc[0], "FALLBACK_INSUFFICIENT_HISTORY")
        np.testing.assert_allclose(out["weight"], 0.25)

    def test_all_negative_is_equal_weight(self):
        out = run(synthetic_history(value=-0.01), J=26, lam=1.0)
        self.assertEqual(out["dynamic_status"].iloc[0], "FALLBACK_ALL_NONPOSITIVE")
        np.testing.assert_allclose(out["weight"], 0.25)

    def test_blend_arithmetic(self):
        hist = synthetic_history(value=0.0)
        hist.loc[hist["family"] == "Quality", "factor_return_gross"] = 0.01   # only Quality positive
        out = run(hist, J=26, lam=0.5).set_index("family")
        self.assertAlmostEqual(out.loc["Quality", "weight"], 0.5 / 4 + 0.5 * 1.0)
        self.assertAlmostEqual(out.loc["Momentum", "weight"], 0.5 / 4)
        self.assertAlmostEqual(out["weight"].sum(), 1.0)

    def test_risk_adjusted_needs_104_weeks(self):
        out = run(synthetic_history(n_weeks=100), J=26, lam=1.0, ra=True)
        self.assertEqual(out["dynamic_status"].iloc[0], "FALLBACK_INSUFFICIENT_HISTORY")

    def test_checks_pass_on_full_grid(self):
        hist = synthetic_history()
        rng = np.random.default_rng(0)
        hist["factor_return_gross"] = rng.normal(0, 0.01, len(hist))
        wide, next_exec = fw.to_wide(hist)
        dates = list(wide.index[-30:])
        out = pd.concat([fw.compute_weights(wide, next_exec, dates, J, lam, ra)
                         for J in fw.J_GRID for lam in fw.LAMBDA_GRID for ra in fw.RISK_ADJ_GRID])
        self.assertEqual(fw.check_weights(out), [])


if __name__ == "__main__":
    unittest.main()
