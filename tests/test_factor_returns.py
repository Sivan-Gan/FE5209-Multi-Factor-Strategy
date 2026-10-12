"""Synthetic checks for Step 16 (weekly factor long-short returns).

Run from the repository root:  python -m unittest tests.test_factor_returns
No real data is needed.  (The real-data check is: python -m src.strategy.factor_returns --check-seed)
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from src.strategy import factor_returns as fr


def make_week(n=10, signal="2016-01-08", next_exec="2016-01-19"):
    """n stocks; every factor score = stock index, return = stock index / 100."""
    permnos = [f"{10000 + i}" for i in range(n)]
    scores = pd.DataFrame({"signal_date": signal, "PERMNO": permnos, "execution_date": "2016-01-11",
                           "research_period": "TEST", "eligible_universe_flag": 1})
    for _, _, s, a in fr.FINAL_FACTORS:
        scores[s] = np.arange(n, dtype=float)
        scores[a] = 1
    returns = pd.DataFrame({"signal_date": signal, "next_execution_date": next_exec, "PERMNO": permnos,
                            "execution_to_next_execution_total_return": np.arange(n) / 100,
                            "execution_feasible": 1, "unavailable_reason": ""})
    return scores, returns


def quality(out):
    return out[out["family"] == "Quality"].iloc[0]


class FactorReturnTest(unittest.TestCase):
    def test_top_minus_bottom_30_percent(self):
        out = fr.compute_factor_returns(*make_week(10), end="2020-12-31")
        q = quality(out)
        # floor(0.3*10) = 3 -> top = stocks 9,8,7 (mean 0.08), bottom = 0,1,2 (mean 0.01)
        self.assertEqual((q["top_count"], q["bottom_count"]), (3, 3))
        self.assertAlmostEqual(q["factor_return_gross"], 0.08 - 0.01)

    def test_tie_break_by_permno(self):
        scores, returns = make_week(10)
        for _, _, s, _ in fr.FINAL_FACTORS:
            scores[s] = [0, 0, 0, 0, 1, 1, 1, 1, 1, 1]   # ties at both cut-offs
        q = quality(fr.compute_factor_returns(scores, returns, end="2020-12-31"))
        # top 3 of the six tied 1s -> lowest PERMNOs 4,5,6 (mean 0.05)
        # bottom 3 of the four tied 0s -> lowest PERMNOs 0,1,2 (mean 0.01); same rule as the frozen seed
        self.assertAlmostEqual(q["top_return_gross"], 0.05)
        self.assertAlmostEqual(q["bottom_return_gross"], 0.01)

    def test_infeasible_stock_left_out_of_leg_mean(self):
        scores, returns = make_week(10)
        returns.loc[returns["PERMNO"] == "10009", "execution_feasible"] = 0
        q = quality(fr.compute_factor_returns(scores, returns, end="2020-12-31"))
        self.assertEqual(q["top_count"], 2)
        self.assertAlmostEqual(q["top_return_gross"], 0.075)   # mean of 0.08, 0.07

    def test_invalid_return_status_makes_week_unavailable(self):
        scores, returns = make_week(10)
        returns.loc[returns["PERMNO"] == "10000", "unavailable_reason"] = "DELISTING_UNCONFIRMED"
        q = quality(fr.compute_factor_returns(scores, returns, end="2020-12-31"))
        self.assertEqual(q["factor_return_available"], 0)
        self.assertEqual(q["unavailable_reason"], "FORWARD_RETURN_INCOMPLETE_OR_EMPTY")

    def test_holding_period_after_window_is_blank(self):
        scores, returns = make_week(10, signal="2020-12-24", next_exec="2021-01-04")
        q = quality(fr.compute_factor_returns(scores, returns, end="2020-12-31"))
        self.assertEqual(q["factor_return_available"], 0)
        self.assertEqual(q["unavailable_reason"], "ENDPOINT_AFTER_WINDOW")

    def test_ineligible_and_unavailable_scores_excluded(self):
        scores, returns = make_week(10)
        scores.loc[scores["PERMNO"] == "10009", "eligible_universe_flag"] = 0
        scores.loc[scores["PERMNO"] == "10008", "quality_available"] = 0
        q = quality(fr.compute_factor_returns(scores, returns, end="2020-12-31"))
        self.assertEqual(q["eligible_count"], 8)
        self.assertEqual(q["top_count"], 2)                     # floor(0.3*8)

    def test_gate_b_guard(self):
        with self.assertRaises(ValueError):
            fr.load_inputs(fr.project_root(), "2016-01-01", "2021-01-31")


if __name__ == "__main__":
    unittest.main()
