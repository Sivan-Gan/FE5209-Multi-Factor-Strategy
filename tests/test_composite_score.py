"""Synthetic checks for Step 20 (composite stock score).

Run from the repository root:  python -m unittest tests.test_composite_score
No real data is needed.
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from src.strategy import composite_score as cs

W = {"w_Quality": 0.4, "w_Momentum": 0.3, "w_LowRisk": 0.2, "w_Investment": 0.1}


def make_scores(rows):
    """rows: list of (PERMNO, [q, m, l, i]) with np.nan meaning unavailable."""
    out = []
    for permno, vals in rows:
        r = {"signal_date": "2016-01-08", "execution_date": "2016-01-11", "PERMNO": permno,
             "historical_gics_sector": "45", "eligible_universe_flag": 1}
        for (s, a), v in zip(zip(cs.SCORE_COLS, cs.AVAIL_COLS), vals):
            r[s], r[a] = v, int(np.isfinite(v))
        out.append(r)
    return pd.DataFrame(out)


WEEK_W = pd.DataFrame([{"signal_date": "2016-01-08", **W}])


class CompositeScoreTest(unittest.TestCase):
    def test_complete_stock(self):
        out = cs.compute_scores(make_scores([("1", [1.0, 2.0, 3.0, 4.0])]), WEEK_W, min_factors=4)
        self.assertAlmostEqual(out["composite_score"].iloc[0], 0.4 * 1 + 0.3 * 2 + 0.2 * 3 + 0.1 * 4)
        self.assertAlmostEqual(out["reallocated_weight"].iloc[0], 0.0)

    def test_missing_factor_dropped_under_min4(self):
        out = cs.compute_scores(make_scores([("1", [1.0, np.nan, 3.0, 4.0])]), WEEK_W, min_factors=4)
        self.assertEqual(out["score_available"].iloc[0], 0)
        self.assertTrue(np.isnan(out["composite_score"].iloc[0]))

    def test_missing_factor_renormalised_under_min3(self):
        out = cs.compute_scores(make_scores([("1", [1.0, np.nan, 3.0, 4.0])]), WEEK_W, min_factors=3)
        expected = (0.4 * 1 + 0.2 * 3 + 0.1 * 4) / (0.4 + 0.2 + 0.1)
        self.assertAlmostEqual(out["composite_score"].iloc[0], expected)
        self.assertAlmostEqual(out["reallocated_weight"].iloc[0], 0.3)

    def test_two_missing_dropped_under_min3(self):
        out = cs.compute_scores(make_scores([("1", [1.0, np.nan, np.nan, 4.0])]), WEEK_W, min_factors=3)
        self.assertEqual(out["score_available"].iloc[0], 0)

    def test_ineligible_stock_excluded(self):
        df = make_scores([("1", [1.0, 1.0, 1.0, 1.0]), ("2", [2.0, 2.0, 2.0, 2.0])])
        df.loc[1, "eligible_universe_flag"] = 0
        out = cs.compute_scores(df, WEEK_W, min_factors=4)
        self.assertEqual(list(out["PERMNO"]), ["1"])

    def test_rank_best_is_smallest_pct(self):
        df = make_scores([("1", [0.0] * 4), ("2", [1.0] * 4), ("3", [-1.0] * 4), ("4", [2.0] * 4)])
        out = cs.compute_scores(df, WEEK_W, min_factors=4).set_index("PERMNO")
        self.assertEqual(out["score_rank_pct"].idxmin(), "4")
        self.assertAlmostEqual(out.loc["4", "score_rank_pct"], 0.25)

    def test_missing_weights_raise(self):
        df = make_scores([("1", [1.0] * 4)])
        df["signal_date"] = "2016-01-15"
        with self.assertRaises(ValueError):
            cs.compute_scores(df, WEEK_W, min_factors=4)

    def test_checks_pass(self):
        rng = np.random.default_rng(1)
        rows = [(str(i), list(rng.normal(size=4))) for i in range(50)]
        out = cs.compute_scores(make_scores(rows), WEEK_W, min_factors=4)
        self.assertEqual(cs.check_scores(out), [])


if __name__ == "__main__":
    unittest.main()
