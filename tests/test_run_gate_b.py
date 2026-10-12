"""Synthetic checks for the Gate B driver (config grid and gross preview).

Run from the repository root:  python -m unittest tests.test_run_gate_b
No real data is needed.
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from src.strategy import run_gate_b as rg


class GridTest(unittest.TestCase):
    def test_grid_size_and_no_duplicates(self):
        grid = rg.config_grid()
        tags = [rg.tag_of(c) for c in grid]
        self.assertEqual(len(grid), 28)
        self.assertEqual(len(set(tags)), 28)

    def test_lambda_zero_run_once_per_buffer(self):
        lam0 = [c for c in rg.config_grid() if c["lam"] == 0]
        self.assertEqual(len(lam0), 2)
        self.assertEqual({c["buffer"] for c in lam0}, {False, True})

    def test_baseline_present_with_both_missing_policies(self):
        base = [c for c in rg.config_grid()
                if (c["J"], c["lam"], c["risk_adjusted"]) == (26, 0.5, False)]
        self.assertEqual({(c["min_factors"], c["buffer"]) for c in base},
                         {(4, False), (4, True), (3, False), (3, True)})


class EvaluateTest(unittest.TestCase):
    def test_evaluate_columns_and_cost_order(self):
        dates = ["2016-01-08", "2016-01-15", "2016-01-22", "2016-01-29"]
        ends = ["2016-01-19", "2016-01-25", "2016-02-01", "2016-02-08"]
        rng = np.random.default_rng(0)
        hold = pd.DataFrame([{"signal_date": d, "PERMNO": p, "weight": 0.5}
                             for i, d in enumerate(dates) for p in (("A", "B") if i % 2 == 0 else ("A", "C"))])
        ret = pd.DataFrame([{"signal_date": d, "next_execution_date": e, "PERMNO": p,
                             "execution_to_next_execution_total_return": rng.normal(0.01, 0.02), "usable": 1}
                            for d, e in zip(dates, ends) for p in ("A", "B", "C")])
        rf = pd.Series(0.0, index=dates)
        row, weekly = rg.evaluate(hold, ret, rf, rf, "2020-12-31", 0.001)
        for col in ("gross_sharpe", "net_sharpe", "stress_sharpe", "net_max_drawdown", "break_even_oneway_cost",
                    "avg_holding_weeks", "trade_win_rate", "cost_drag_ann", "net_information_ratio"):
            self.assertIn(col, row)
        self.assertGreater(row["gross_ann_return"], row["net_ann_return"])
        self.assertGreater(row["net_ann_return"], row["stress_ann_return"])
        self.assertEqual(len(weekly), 4)


if __name__ == "__main__":
    unittest.main()
