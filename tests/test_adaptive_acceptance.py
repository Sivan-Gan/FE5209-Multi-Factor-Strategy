
"""Acceptance tests for Adaptive Strategy V1.

Run:
python -m unittest discover -s tests -p "test_adaptive_acceptance.py" -v
"""

import json
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/adaptive_strategy/final_integration_test"


class TestAdaptiveAcceptance(unittest.TestCase):

    def test_historical_reproduction(self):
        audit = pd.read_csv(
            OUT / "historical_reproduction/reproduction_audit.csv"
        )

        self.assertEqual(len(audit), 9)
        self.assertTrue(audit["passed"].all())
        self.assertLess(audit["max_abs_error"].max(), 1e-10)

    def test_historical_week_count(self):
        paths = pd.read_csv(
            OUT / "historical_reproduction/weekly_paths.csv"
        )

        counts = paths.groupby("strategy")["signal_date"].nunique()

        self.assertEqual(len(counts), 3)
        self.assertTrue(counts.eq(520).all())

    def test_model_selection(self):
        with open(
            OUT / "model_selection/frozen_model.json",
            encoding="utf-8",
        ) as f:
            frozen = json.load(f)

        self.assertEqual(
            frozen["selected_strategy"],
            "WF_3Y_Top3_IV",
        )

        rankings = pd.read_csv(
            OUT / "model_selection/training_model_rankings.csv"
        )

        self.assertEqual(len(rankings), 3)
        self.assertEqual(
            rankings.sort_values("train_rank").iloc[0]["strategy"],
            frozen["selected_strategy"],
        )

    def test_fixed_benchmark(self):
        fixed = pd.read_csv(
            OUT / "fixed_top3_benchmark/weekly_paths.csv"
        )

        self.assertEqual(len(fixed), 259)
        self.assertEqual(fixed["signal_date"].nunique(), 259)

    def test_controlled_benchmark(self):
        adaptive = pd.read_csv(
            OUT / "benchmark_validation/adaptive_weekly_paths.csv"
        )
        fixed = pd.read_csv(
            OUT / "fixed_top3_benchmark/weekly_paths.csv"
        )

        a = adaptive[
            adaptive["signal_date"].str[:4].eq("2021")
        ]

        b = fixed[
            fixed["signal_date"].str[:4].eq("2021")
        ]

        merged = a.merge(
            b,
            on="signal_date",
            suffixes=("_a", "_b"),
            validate="one_to_one",
        )

        self.assertEqual(len(merged), 52)

        for metric in ["gross_return", "gtr", "net_return"]:
            error = (
                merged[f"{metric}_a"]
                - merged[f"{metric}_b"]
            ).abs().max()

            self.assertLess(error, 1e-10)

    def test_2026_extension(self):
        summary = pd.read_csv(
            OUT / "recent_extension/performance_comparison.csv"
        )

        audit = pd.read_csv(
            OUT / "recent_extension/reproduction_audit.csv"
        )

        self.assertEqual(len(summary), 4)
        self.assertTrue(summary["n_weeks"].eq(39).all())

        self.assertEqual(len(audit), 3)
        self.assertLess(audit["abs_error"].max(), 1e-10)

    def test_reporting_outputs(self):
        expected = [
            "reporting/overall_performance.csv",
            "reporting/cumulative_nav.png",
            "reporting/drawdown.png",
            "benchmark_reporting/overall_comparison.csv",
            "benchmark_reporting/adaptive_vs_fixed_nav.png",
            "recent_extension/cumulative_nav.png",
        ]

        for name in expected:
            with self.subTest(file=name):
                p = OUT / name
                self.assertTrue(p.is_file())
                self.assertGreater(p.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
