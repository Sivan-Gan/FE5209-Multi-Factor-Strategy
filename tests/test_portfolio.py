"""Synthetic checks for Step 21 (target holdings).

Run from the repository root:  python -m unittest tests.test_portfolio
No real data is needed.
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from src.strategy import portfolio as pf


def week(scores, sectors=None, date="2016-01-08"):
    n = len(scores)
    sectors = sectors or ["45"] * n
    return pd.DataFrame({
        "signal_date": date, "execution_date": "2016-01-11",
        "PERMNO": [f"{10000 + i}" for i in range(n)], "historical_gics_sector": sectors,
        "composite_score": scores, "score_available": 1,
    })


def spread_sectors(n, k=10):
    return [str(10 + i % k) for i in range(n)]


class PortfolioTest(unittest.TestCase):
    def test_top_20_percent_equal_weight(self):
        df = week(list(np.linspace(1, 0, 50)), spread_sectors(50))
        hold, weekly = pf.build_holdings(df, use_buffer=False)
        self.assertEqual(len(hold), 10)                      # floor(0.2 * 50)
        self.assertEqual(set(hold["rank"]), set(range(1, 11)))
        np.testing.assert_allclose(hold["weight"], 0.1)
        self.assertEqual(pf.check_holdings(hold, weekly), [])

    def test_tie_break_by_permno(self):
        df = week([1.0] * 50, spread_sectors(50)).sample(frac=1, random_state=3)  # shuffled rows
        hold, _ = pf.build_holdings(df, use_buffer=False)   # floor(0.2*50) = 10, all tied
        self.assertEqual(sorted(hold["PERMNO"]), [f"{10000 + i}" for i in range(10)])

    def test_sector_cap_skips_and_fills(self):
        # best 10 names all in sector A; cap = floor(0.3 * 10) = 3 names
        sectors = ["A"] * 10 + spread_sectors(40)
        df = week(list(np.linspace(1, 0, 50)), sectors)
        hold, weekly = pf.build_holdings(df, use_buffer=False)
        self.assertEqual((hold["sector"] == "A").sum(), 3)
        self.assertEqual(len(hold), 10)
        self.assertLessEqual(weekly["max_sector_weight"].iloc[0], 0.30 + 1e-12)

    def test_infeasible_cap_leaves_cash(self):
        # only two sectors, cap 3 names each -> at most 6 of 10 names
        sectors = ["A"] * 25 + ["B"] * 25
        hold, weekly = pf.build_holdings(week(list(np.linspace(1, 0, 50)), sectors), use_buffer=False)
        self.assertEqual(len(hold), 6)
        self.assertEqual(weekly["constraint_infeasible"].iloc[0], 1)
        self.assertAlmostEqual(weekly["cash_weight"].iloc[0], 0.4)
        np.testing.assert_allclose(hold["weight"], 0.1)     # no silent re-scaling above 1/N

    def test_buffer_keeps_name_inside_top_30(self):
        sectors = spread_sectors(50)
        w1 = week(list(np.linspace(1, 0, 50)), sectors, "2016-01-08")
        s2 = list(np.linspace(1, 0, 50))
        s2[0], s2[12] = s2[12], s2[0]                        # old #1 drops to rank 13 (inside top 30% = 15)
        w2 = week(s2, sectors, "2016-01-15")
        hold_nb, _ = pf.build_holdings(pd.concat([w1, w2]), use_buffer=False)
        hold_b, weekly_b = pf.build_holdings(pd.concat([w1, w2]), use_buffer=True)
        wk2_nb = set(hold_nb.loc[hold_nb["signal_date"] == "2016-01-15", "PERMNO"])
        wk2_b = hold_b[hold_b["signal_date"] == "2016-01-15"]
        self.assertNotIn("10000", wk2_nb)
        self.assertIn("10000", set(wk2_b["PERMNO"]))
        self.assertEqual(wk2_b.set_index("PERMNO").loc["10000", "entry"], "KEPT_BY_BUFFER")
        self.assertEqual(len(wk2_b), 10)
        self.assertLess(weekly_b["one_way_turnover"].iloc[1], 1e-12)

    def test_buffer_sells_below_top_30(self):
        sectors = spread_sectors(50)
        w1 = week(list(np.linspace(1, 0, 50)), sectors, "2016-01-08")
        s2 = list(np.linspace(1, 0, 50))
        s2[0], s2[20] = s2[20], s2[0]                        # old #1 drops to rank 21 (outside 15)
        w2 = week(s2, sectors, "2016-01-15")
        hold_b, _ = pf.build_holdings(pd.concat([w1, w2]), use_buffer=True)
        self.assertNotIn("10000", set(hold_b.loc[hold_b["signal_date"] == "2016-01-15", "PERMNO"]))

    def test_unscored_stock_never_held(self):
        df = week(list(np.linspace(1, 0, 50)), spread_sectors(50))
        df.loc[0, "score_available"] = 0
        hold, _ = pf.build_holdings(df, use_buffer=False)
        self.assertNotIn("10000", set(hold["PERMNO"]))
        self.assertEqual(len(hold), 9)                       # floor(0.2 * 49)

    def test_missing_sector_is_its_own_bucket(self):
        sectors = [None] * 10 + spread_sectors(40)
        hold, _ = pf.build_holdings(week(list(np.linspace(1, 0, 50)), sectors), use_buffer=False)
        self.assertEqual((hold["sector"] == pf.UNKNOWN_SECTOR).sum(), 3)


if __name__ == "__main__":
    unittest.main()
