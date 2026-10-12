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


class PreviewTest(unittest.TestCase):
    def setUp(self):
        self.hold = pd.DataFrame({"signal_date": ["2020-12-11"] * 2 + ["2020-12-24"] * 2,
                                  "PERMNO": ["1", "2", "1", "2"], "weight": [0.5, 0.5, 0.5, 0.5]})
        self.ret = pd.DataFrame({
            "signal_date": ["2020-12-11", "2020-12-11", "2020-12-24", "2020-12-24"],
            "next_execution_date": ["2020-12-21", "2020-12-21", "2021-01-04", "2021-01-04"],
            "PERMNO": ["1", "2", "1", "2"],
            "execution_to_next_execution_total_return": [0.02, -0.01, 0.05, 0.05],
            "usable": [1, 1, 1, 1]})

    def test_weighted_return_and_window_cut(self):
        weekly = rg.gross_preview(self.hold, self.ret, end="2020-12-31")
        self.assertEqual(list(weekly.index), ["2020-12-11"])    # 12-24 ends in 2021 -> dropped
        self.assertAlmostEqual(weekly["gross_return"].iloc[0], 0.5 * 0.02 + 0.5 * -0.01)

    def test_unusable_stock_counts_as_cash(self):
        self.ret.loc[1, "usable"] = 0
        weekly = rg.gross_preview(self.hold, self.ret, end="2020-12-31")
        self.assertAlmostEqual(weekly["gross_return"].iloc[0], 0.01)
        self.assertAlmostEqual(weekly["unpriced_weight"].iloc[0], 0.5)

    def test_stats(self):
        weekly = pd.DataFrame({"gross_return": [0.01, -0.01] * 26, "unpriced_weight": 0.0})
        s = rg.preview_stats(weekly)
        self.assertEqual(s["preview_weeks"], 52)
        self.assertAlmostEqual(s["preview_gross_sharpe_rf0"], 0.0)


if __name__ == "__main__":
    unittest.main()
