#!/usr/bin/env python3
"""Export the complete weekly SG stock-factor panel for private team research.

This creates the stock-level inputs required to test alternative portfolio
construction rules.  It includes every covered ticker, including rows that
fail the current eligibility screen, so a researcher can transparently change
the liquidity or history rule.  It does not change, select, or backtest any
strategy configuration.

All four factor scores are residualized weekly against log market capitalization
and six-sector dummy variables.  Market capitalization is a documented lagged
shares-outstanding × weekly-close proxy; it is not vendor-confirmed strict PIT.

The output is deliberately ignored by Git: it is derived from LSEG-licensed
inputs and should be shared only through an authorised private channel.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.strategy.run_sg_independent_research import (
    make_scores,
    prepare_fundamentals,
    prepare_prices,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "private_exports" / "sg_stock_factor_panel_2006_latest_mcap_sector_neutralized.csv.gz"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2006-01-01")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    prices, calendar = prepare_prices()
    funds = prepare_fundamentals(set(prices["ticker"].unique()))
    scores, _ = make_scores(prices, calendar, funds)

    columns = [
        "signal_date", "execution_date", "next_execution_date", "ticker", "six_sector",
        "adj_close", "volume", "trading_value", "vol_252d", "obs_252d", "nonzero_60d",
        "price_eligible", "market_cap", "market_cap_eligible", "currency", "source_date",
        "source_field", "PIT_status", "market_cap_status", "Quality_score", "Momentum_score", "LowRisk_score",
        "Investment_score", "Quality_available", "Momentum_available", "LowRisk_available",
        "Investment_available", "forward_return",
    ]
    result = scores.loc[scores["signal_date"].ge(args.start), columns].copy()
    if result.duplicated(["signal_date", "ticker"]).any():
        raise ValueError("Duplicate signal_date/ticker rows in factor panel")
    if not (result["execution_date"] > result["signal_date"]).all():
        raise ValueError("Execution date must be after signal date")
    if not (result["next_execution_date"] > result["execution_date"]).all():
        raise ValueError("Holding period must be positive")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output, index=False, compression="gzip")
    coverage = pd.DataFrame({
        "field": ["Quality_score", "Momentum_score", "LowRisk_score", "Investment_score", "forward_return"],
        "nonmissing_share": [float(result[c].notna().mean()) for c in ["Quality_score", "Momentum_score", "LowRisk_score", "Investment_score", "forward_return"]],
    })
    coverage.to_csv(args.output.with_name("sg_stock_factor_panel_coverage.csv"), index=False)
    print({
        "file": str(args.output.resolve()),
        "rows": len(result),
        "weeks": int(result["signal_date"].nunique()),
        "tickers": int(result["ticker"].nunique()),
        "size_mb": round(args.output.stat().st_size / 1e6, 2),
    })


if __name__ == "__main__":
    main()
