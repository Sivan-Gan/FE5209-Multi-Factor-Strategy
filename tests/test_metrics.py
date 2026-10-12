"""Synthetic checks for the shared metric functions (metrics.py).

Run from the repository root:  python -m unittest tests.test_metrics
No real data is needed.
"""
from __future__ import annotations

import math
import unittest

import numpy as np
import pandas as pd

from src.strategy import metrics as mt

DATES = ["2016-01-08", "2016-01-15", "2016-01-22"]
ENDS = ["2016-01-19", "2016-01-25", "2016-02-01"]


def hold(weeks):
    """weeks: list of {PERMNO: weight} per DATES entry."""
    return pd.DataFrame([{"signal_date": DATES[i], "PERMNO": p, "weight": w}
                         for i, wk in enumerate(weeks) for p, w in wk.items()])


def rets(table, usable=None):
    """table: list of {PERMNO: return} per DATES entry."""
    rows = []
    for i, wk in enumerate(table):
        for p, r in wk.items():
            rows.append({"signal_date": DATES[i], "next_execution_date": ENDS[i], "PERMNO": p,
                         "execution_to_next_execution_total_return": r,
                         "usable": 1 if usable is None else usable.get((i, p), 1)})
    return pd.DataFrame(rows)


class PathTest(unittest.TestCase):
    def test_initial_build_gtr_is_one_and_gross_return(self):
        path = mt.weekly_path(hold([{"A": 0.5, "B": 0.5}]), rets([{"A": 0.10, "B": 0.0}]), "2020-12-31")
        self.assertAlmostEqual(path["gtr"].iloc[0], 1.0)
        self.assertAlmostEqual(path["gross_return"].iloc[0], 0.05)

    def test_drift_rebalance_gtr(self):
        # week 1: A +10%, B 0% -> drifted A = 0.55/1.05, B = 0.5/1.05; week 2 back to 50/50
        path = mt.weekly_path(hold([{"A": 0.5, "B": 0.5}, {"A": 0.5, "B": 0.5}]),
                              rets([{"A": 0.10, "B": 0.0}, {"A": 0.0, "B": 0.0}]), "2020-12-31")
        expected = abs(0.5 - 0.55 / 1.05) + abs(0.5 - 0.5 / 1.05)
        self.assertAlmostEqual(path["gtr"].iloc[1], expected)

    def test_full_switch_gtr_is_two(self):
        path = mt.weekly_path(hold([{"A": 1.0}, {"B": 1.0}]), rets([{"A": 0.0}, {"B": 0.0}]), "2020-12-31")
        self.assertAlmostEqual(path["gtr"].iloc[1], 2.0)              # sell 100% + buy 100%

    def test_exact_net_formula(self):
        path = pd.DataFrame({"gross_return": [0.02], "gtr": [0.5]})
        self.assertAlmostEqual(mt.net_returns(path, 0.001).iloc[0], (1 - 0.0005) * 1.02 - 1)

    def test_unusable_return_earns_zero(self):
        path = mt.weekly_path(hold([{"A": 0.5, "B": 0.5}]), rets([{"A": 0.10, "B": 0.50}], {(0, "B"): 0}),
                              "2020-12-31")
        self.assertAlmostEqual(path["gross_return"].iloc[0], 0.05)
        self.assertAlmostEqual(path["unpriced_weight"].iloc[0], 0.5)

    def test_window_cut(self):
        path = mt.weekly_path(hold([{"A": 1.0}, {"A": 1.0}]), rets([{"A": 0.0}, {"A": 0.0}]), "2016-01-20")
        self.assertEqual(len(path), 1)

    def test_break_even_cost(self):
        path = pd.DataFrame({"gross_return": [0.01] * 10, "gtr": [0.2] * 10})
        c = mt.break_even_cost(path)
        self.assertAlmostEqual(float(np.prod((1 - c * path["gtr"]) * (1 + path["gross_return"]))), 1.0, places=9)


class PerformanceTest(unittest.TestCase):
    def test_drawdown_positive_and_episodes(self):
        r = pd.Series([0.1, -0.5, 0.2, 1.0, -0.1, 0.0])
        p = mt.performance(r)
        self.assertAlmostEqual(p["max_drawdown"], 0.5)                 # positive per SSOT
        self.assertEqual(p["drawdown_episodes"], 2)

    def test_sharpe_uses_excess_return(self):
        r = pd.Series([0.02, 0.0, 0.02, 0.0])
        rf = pd.Series([0.01] * 4)
        p = mt.performance(r, rf)
        ex = r - rf
        self.assertAlmostEqual(p["sharpe"], ex.mean() / ex.std(ddof=1) * math.sqrt(52))
        self.assertAlmostEqual(p["period_hit_rate"], 0.5)

    def test_benchmark_beta_one_for_same_series(self):
        r = pd.Series([0.01, -0.02, 0.03, 0.0])
        p = mt.performance(r, bench=r)
        self.assertAlmostEqual(p["beta"], 1.0)
        self.assertAlmostEqual(p["active_ann_return"], 0.0)


class TradeTest(unittest.TestCase):
    def test_round_trips_and_holding(self):
        # A held weeks 0-1 then dropped; B held week 2 only (open at end)
        h = hold([{"A": 1.0}, {"A": 1.0}, {"B": 1.0}])
        r = rets([{"A": 0.10}, {"A": 0.10}, {"B": -0.05}])
        t = mt.trade_stats(h, r, "2020-12-31", cost_oneway=0.0)
        self.assertEqual(t["n_round_trips"], 2)
        self.assertEqual(t["n_trades_buy_plus_sell"], 3)              # buy A, sell A, buy B
        self.assertAlmostEqual(t["avg_holding_weeks"], 1.5)
        self.assertAlmostEqual(t["trade_win_rate"], 0.5)
        self.assertAlmostEqual(t["avg_profit_per_trade"], ((1.1 * 1.1 - 1) + (-0.05)) / 2)


if __name__ == "__main__":
    unittest.main()
