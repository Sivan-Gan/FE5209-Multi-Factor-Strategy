"""Isolation and direction checks for cross-sectional price preprocessing."""

from __future__ import annotations

import unittest

from src.factors import preprocessing


def _rows(values: list[float], date: str = "2020-01-03") -> list[dict[str, object]]:
    return [
        {"ticker": f"T{index:03d}", "signal_date": date, "metric": "VOL252", "raw_value": value}
        for index, value in enumerate(values)
    ]


class TestPreprocessing(unittest.TestCase):
    def test_winsor_zscore_and_low_risk_direction(self) -> None:
        result = preprocessing.preprocess_cross_section(_rows([float(index) for index in range(50)]), -1)
        self.assertTrue(all(row["preprocessing_available"] for row in result))
        self.assertEqual({row["cross_section_count"] for row in result}, {50})
        self.assertAlmostEqual(sum(float(row["z_score"]) for row in result), 0.0, places=12)
        self.assertAlmostEqual(
            float(result[-1]["direction_normalized_score"]), -float(result[-1]["z_score"])
        )
        self.assertNotEqual(result[0]["winsorized_value"], result[0]["raw_value"])

    def test_cross_date_isolation_and_no_minimum_cross_section_substitution(self) -> None:
        baseline = preprocessing.preprocess_cross_section(_rows([float(index) for index in range(50)]), 1)
        # A separate date with an extreme value cannot alter the first date.
        other_date = preprocessing.preprocess_cross_section(
            _rows([float(index) for index in range(49)] + [1_000_000.0], "2020-01-10"), 1
        )
        rerun = preprocessing.preprocess_cross_section(_rows([float(index) for index in range(50)]), 1)
        self.assertNotEqual(other_date[-1]["winsorized_value"], baseline[-1]["winsorized_value"])
        self.assertEqual(
            [row["direction_normalized_score"] for row in baseline],
            [row["direction_normalized_score"] for row in rerun],
        )
        too_small = preprocessing.preprocess_cross_section(_rows([1.0] * 49), 1)
        self.assertTrue(all(not row["preprocessing_available"] for row in too_small))
        self.assertTrue(all(row["winsorized_value"] is None for row in too_small))
        self.assertTrue(all(row["preprocessing_reason"] == "insufficient_cross_section_lt_50" for row in too_small))

    def test_linear_quantile_is_deterministic(self) -> None:
        self.assertEqual(preprocessing.linear_quantile([0.0, 10.0], 0.5), 5.0)


if __name__ == "__main__":
    unittest.main()
