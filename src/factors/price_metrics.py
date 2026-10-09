"""Pure calculations for the approved US DEV price-based raw metrics.

The functions operate only on canonical daily rows available on or before the
requested signal index.  They contain no file I/O, portfolio logic, IC, or
strategy calculations.
"""

from __future__ import annotations

import math
import statistics
from typing import Any


MOMENTUM_SKIP_TRADING_DAYS = 21
MOM12_LOOKBACK_TRADING_DAYS = 252
MOM6_LOOKBACK_TRADING_DAYS = 126
VOLATILITY_RETURN_WINDOW = 252
VOLATILITY_MIN_VALID_RETURNS = 200
TRADING_DAYS_PER_YEAR = 252


def parse_positive_price(row: dict[str, str] | None) -> float | None:
    """Return a finite positive adjusted close, otherwise ``None``."""

    if not row:
        return None
    value = row.get("adj_close", "")
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price > 0 else None


def _base_result(
    metric: str,
    signal_index: int,
    start_index: int | None,
    end_index: int | None,
) -> dict[str, Any]:
    return {
        "metric": metric,
        "raw_value": None,
        "raw_metric_available": False,
        "raw_availability_reason": "",
        "signal_market_trading_day_index": signal_index,
        "window_start_market_trading_day_index": start_index,
        "window_end_market_trading_day_index": end_index,
        "valid_observation_count": 0,
        "expected_observation_count": 0,
        "zero_return_count": "",
    }


def compute_momentum(
    rows_by_market_index: dict[int, dict[str, str]],
    signal_index: int,
    lookback_trading_days: int,
    metric: str,
    skip_trading_days: int = MOMENTUM_SKIP_TRADING_DAYS,
) -> dict[str, Any]:
    """Compute one daily-lookback momentum measure without using future rows.

    At signal index ``t``, the numerator price is at ``t-skip`` and the
    denominator is at ``t-lookback``.  The complete delivered market-date
    interval from ``t-lookback`` through ``t`` must be present and have a
    positive adjusted close; no values are forward-filled.
    """

    start_index = signal_index - lookback_trading_days
    end_index = signal_index - skip_trading_days
    result = _base_result(metric, signal_index, start_index, end_index)
    result["expected_observation_count"] = lookback_trading_days + 1
    if start_index < 0 or end_index < start_index:
        result["raw_availability_reason"] = "insufficient_momentum_history"
        return result
    if signal_index not in rows_by_market_index:
        result["raw_availability_reason"] = "no_price_on_signal_date"
        return result
    window = range(start_index, signal_index + 1)
    missing = [index for index in window if index not in rows_by_market_index]
    if missing:
        result["raw_availability_reason"] = "incomplete_market_price_window"
        result["valid_observation_count"] = len(window) - len(missing)
        return result
    prices = [parse_positive_price(rows_by_market_index[index]) for index in window]
    if any(price is None for price in prices):
        result["raw_availability_reason"] = "missing_or_nonpositive_adjusted_close"
        result["valid_observation_count"] = sum(price is not None for price in prices)
        return result
    denominator = parse_positive_price(rows_by_market_index[start_index])
    numerator = parse_positive_price(rows_by_market_index[end_index])
    if denominator is None or numerator is None:
        result["raw_availability_reason"] = "missing_or_nonpositive_adjusted_close"
        return result
    result["raw_value"] = numerator / denominator - 1.0
    result["raw_metric_available"] = True
    result["valid_observation_count"] = len(window)
    return result


def compute_realized_volatility(
    rows_by_market_index: dict[int, dict[str, str]],
    signal_index: int,
    metric: str = "VOL252",
    return_window: int = VOLATILITY_RETURN_WINDOW,
    minimum_valid_returns: int = VOLATILITY_MIN_VALID_RETURNS,
) -> dict[str, Any]:
    """Compute annualised sample realized volatility from canonical prices.

    The 252 return dates end at the signal date. A return is valid only when
    both adjacent market-date records exist and have positive adjusted closes;
    zero returns are valid observations.  The calculation uses sample standard
    deviation (``ddof=1``) as an explicit DEV engineering convention.
    """

    start_index = signal_index - return_window
    result = _base_result(metric, signal_index, start_index, signal_index)
    result["expected_observation_count"] = return_window
    if start_index < 0:
        result["raw_availability_reason"] = "insufficient_volatility_window"
        return result
    returns: list[float] = []
    zero_returns = 0
    for current_index in range(signal_index - return_window + 1, signal_index + 1):
        prior_price = parse_positive_price(rows_by_market_index.get(current_index - 1))
        current_price = parse_positive_price(rows_by_market_index.get(current_index))
        if prior_price is None or current_price is None:
            continue
        daily_return = current_price / prior_price - 1.0
        if daily_return == 0.0:
            zero_returns += 1
        returns.append(daily_return)
    result["valid_observation_count"] = len(returns)
    result["zero_return_count"] = zero_returns
    if len(returns) < minimum_valid_returns:
        result["raw_availability_reason"] = "insufficient_valid_daily_returns"
        return result
    volatility = statistics.stdev(returns) * math.sqrt(TRADING_DAYS_PER_YEAR)
    if not math.isfinite(volatility) or volatility <= 0:
        result["raw_availability_reason"] = "nonpositive_realized_volatility"
        return result
    result["raw_value"] = volatility
    result["raw_metric_available"] = True
    return result


def metric_direction(metric: str) -> int:
    """Return the frozen economic direction of a raw price metric."""

    if metric in {"MOM12_1", "MOM6_1"}:
        return 1
    if metric == "VOL252":
        return -1
    raise ValueError(f"Unrecognized price metric: {metric}")


def metric_factor_label(metric: str) -> str:
    """Return an explanatory factor label without combining factor metrics."""

    if metric in {"MOM12_1", "MOM6_1"}:
        return "Momentum candidate metric"
    if metric == "VOL252":
        return "Low Risk candidate metric (-Volatility)"
    raise ValueError(f"Unrecognized price metric: {metric}")

