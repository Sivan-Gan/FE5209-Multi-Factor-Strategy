#!/usr/bin/env python3
"""Compare the fixed SG strategy with a supplied STI *price-index* export.

The comparison is intentionally labeled price-only: it is useful as a temporary
market reference but cannot be treated as a total-return alpha calculation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/strategy/sg_independent"


def stats(r: pd.Series) -> dict:
    r = r.dropna()
    nav = (1 + r).cumprod()
    ann = nav.iloc[-1] ** (52 / len(r)) - 1
    vol = r.std(ddof=1) * np.sqrt(52)
    mdd = (1 - nav / nav.cummax()).max()
    return {"weeks": int(len(r)), "cumulative_return": float(nav.iloc[-1] - 1),
            "annualized_return": float(ann), "annualized_volatility": float(vol),
            "weekly_sharpe_zero_rf": float(r.mean() / r.std(ddof=1) * np.sqrt(52)),
            "max_drawdown": float(mdd)}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("sti_file", type=Path)
    args = p.parse_args()
    strategy = pd.read_csv(OUT / "03_strategy_weekly_returns_2016_latest.csv", parse_dates=["signal_date"])
    dates = pd.read_csv(OUT / "03_frozen_sg_holdings_2016_latest.csv",
                        usecols=["signal_date", "execution_date", "next_execution_date"],
                        parse_dates=["signal_date", "execution_date", "next_execution_date"]).drop_duplicates("signal_date")
    sti = pd.read_excel(args.sti_file, sheet_name="Sheet 1", header=31, usecols="A:H")
    sti = sti.rename(columns={"Exchange Date": "date", "Close": "sti_price_close"})[["date", "sti_price_close"]]
    sti["date"] = pd.to_datetime(sti["date"])
    sti = sti.dropna().drop_duplicates("date").sort_values("date")
    execution = sti.rename(columns={"date": "execution_date", "sti_price_close": "sti_execution_close"})
    next_execution = sti.rename(columns={"date": "next_execution_date", "sti_price_close": "sti_next_execution_close"})
    compare = (strategy.merge(dates, on="signal_date", how="inner")
               .merge(execution, on="execution_date", how="left")
               .merge(next_execution, on="next_execution_date", how="left"))
    compare["sti_price_return"] = compare["sti_next_execution_close"] / compare["sti_execution_close"] - 1
    compare = compare[compare["signal_date"].ge("2021-01-01")].dropna(subset=["sti_price_return", "net_return"]).copy()
    compare["active_return_vs_sti_price"] = compare["net_return"] - compare["sti_price_return"]
    beta = compare.net_return.cov(compare.sti_price_return) / compare.sti_price_return.var(ddof=1)
    weekly_alpha = compare.net_return.mean() - beta * compare.sti_price_return.mean()
    active = compare.active_return_vs_sti_price
    summary = {
        "comparison_label": "Strategy net return versus STI PRICE INDEX return; not a total-return comparison",
        "period_start_signal": str(compare.signal_date.min().date()),
        "period_end_signal": str(compare.signal_date.max().date()),
        "strategy_net": stats(compare.net_return),
        "sti_price_index": stats(compare.sti_price_return),
        "active": {"cumulative_return_difference": float((1+compare.net_return).prod()/(1+compare.sti_price_return).prod()-1),
                   "mean_weekly_active_return": float(active.mean()),
                   "annualized_tracking_error": float(active.std(ddof=1)*np.sqrt(52)),
                   "information_ratio_zero_rf": float(active.mean()/active.std(ddof=1)*np.sqrt(52)),
                   "correlation": float(compare.net_return.corr(compare.sti_price_return)),
                   "beta_to_sti_price": float(beta),
                   "annualized_CAPM_alpha_approx": float(weekly_alpha*52)},
        "limitation": "STI input is a price index. It excludes index dividends; do not present this as final total-return alpha or final outperformance evidence."
    }
    compare.to_csv(OUT / "05_sti_price_benchmark_weekly_comparison_2021_latest.csv", index=False)
    (OUT / "05_sti_price_benchmark_summary_2021_latest.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
