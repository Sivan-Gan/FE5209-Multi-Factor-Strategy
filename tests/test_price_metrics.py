"""Synthetic timing and availability checks for approved price metrics."""

from __future__ import annotations

import math
import statistics
import unittest

from src.factors import price_metrics


def _price_rows(prices: list[float]) -> dict[int, dict[str, str]]:
    return {index: {"adj_close": str(price)} for index, price in enumerate(prices)}


class TestPriceMetrics(unittest.TestCase):
    def test_momentum_uses_fixed_past_offsets_and_no_future_price(self) -> None:
        rows = _price_rows([100.0 + index for index in range(254)])
        result = price_metrics.compute_momentum(rows, 252, 252, "MOM12_1")
        self.assertTrue(result["raw_metric_available"])
        self.assertAlmostEqual(result["raw_value"], 331.0 / 100.0 - 1.0)
        self.assertEqual(result["window_start_market_trading_day_index"], 0)
        self.assertEqual(result["window_end_market_trading_day_index"], 231)
        # This is after the signal index and must not affect the signal result.
        rows[253]["adj_close"] = "999999"
        unchanged = price_metrics.compute_momentum(rows, 252, 252, "MOM12_1")
        self.assertAlmostEqual(unchanged["raw_value"], result["raw_value"])

    def test_momentum_skip_and_history_rules(self) -> None:
        rows = _price_rows([200.0 + index for index in range(127)])
        result = price_metrics.compute_momentum(rows, 126, 126, "MOM6_1")
        self.assertTrue(result["raw_metric_available"])
        self.assertAlmostEqual(result["raw_value"], 305.0 / 200.0 - 1.0)
        self.assertEqual(result["window_end_market_trading_day_index"], 105)
        early = price_metrics.compute_momentum(rows, 125, 126, "MOM6_1")
        self.assertFalse(early["raw_metric_available"])
        self.assertEqual(early["raw_availability_reason"], "insufficient_momentum_history")
        rows.pop(50)
        incomplete = price_metrics.compute_momentum(rows, 126, 126, "MOM6_1")
        self.assertFalse(incomplete["raw_metric_available"])
        self.assertEqual(incomplete["raw_availability_reason"], "incomplete_market_price_window")

    def test_volatility_window_annualization_and_zero_return_treatment(self) -> None:
        returns = [0.0 if index % 17 == 0 else (0.01 if index % 2 else -0.006) for index in range(252)]
        prices = [100.0]
        for daily_return in returns:
            prices.append(prices[-1] * (1 + daily_return))
        result = price_metrics.compute_realized_volatility(_price_rows(prices), 252)
        expected = statistics.stdev(returns) * math.sqrt(252)
        self.assertTrue(result["raw_metric_available"])
        self.assertEqual(result["valid_observation_count"], 252)
        self.assertEqual(result["zero_return_count"], sum(value == 0 for value in returns))
        self.assertAlmostEqual(result["raw_value"], expected)
        rows = _price_rows(prices)
        for index in range(1, 61):
            rows.pop(index)
        insufficient = price_metrics.compute_realized_volatility(rows, 252)
        self.assertFalse(insufficient["raw_metric_available"])
        self.assertEqual(insufficient["raw_availability_reason"], "insufficient_valid_daily_returns")

    def test_volatility_requires_full_252_return_window_start(self) -> None:
        result = price_metrics.compute_realized_volatility(_price_rows([100.0] * 252), 251)
        self.assertFalse(result["raw_metric_available"])
        self.assertEqual(result["raw_availability_reason"], "insufficient_volatility_window")


if __name__ == "__main__":
    unittest.main()
