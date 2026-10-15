#!/usr/bin/env python3
"""Add the two required SG baselines without changing selected SG parameters.

Baselines:
1. Static multi-factor: equal weight across the factors selected in 2011--2015.
2. Eligible-universe equal weight: all price-eligible SG securities each week.

Both use the same next-trading-day execution dates and provisional 20 bp one-way
cost convention as the frozen SG strategy.  They are descriptive benchmarks,
not additional configuration candidates.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.strategy import run_sg_independent_research as sg


OUT = sg.OUT


def equal_universe_holdings(scores: pd.DataFrame) -> pd.DataFrame:
    s = scores[scores.signal_date.ge(pd.Timestamp(sg.CONFIG_START)) & scores.price_eligible].copy()
    s["weight"] = 1 / s.groupby("signal_date")["ticker"].transform("count")
    return s[["signal_date", "execution_date", "next_execution_date", "ticker", "six_sector", "weight", "forward_return"]]


def compare_rows(name: str, ret: pd.DataFrame) -> dict:
    q = ret[ret.signal_date.ge(pd.Timestamp(sg.OOS_START))]
    return {"benchmark": name, **sg.metrics(q)}


def main() -> None:
    prices, calendar = sg.prepare_prices()
    funds = sg.prepare_fundamentals(set(prices.ticker))
    scores, _ = sg.make_scores(prices, calendar, funds)
    fret, ic = sg.factor_returns(scores)
    _, selected = sg.selection_summary(fret, ic)
    signals = sorted(scores[scores.signal_date.ge(pd.Timestamp(sg.CONFIG_START))].signal_date.unique())

    static_config = {"J": 26, "lambda": 0.0, "risk_adjusted": False, "buffer": False}
    static_w = sg.fmom_weights(fret, selected, static_config, signals)
    static_h, _ = sg.score_and_hold(scores, static_w, selected, False)
    static_r = sg.path(static_h, sg.ONE_WAY_COST_BPS)

    universe_h = equal_universe_holdings(scores)
    universe_r = sg.path(universe_h, sg.ONE_WAY_COST_BPS)

    frozen_r = pd.read_csv(OUT / "03_strategy_weekly_returns_2016_latest.csv", parse_dates=["signal_date"])
    all_returns = pd.concat([
        frozen_r.assign(strategy="FMOM_SELECTED"),
        static_r.assign(strategy="STATIC_EQUAL_FACTOR"),
        universe_r.assign(strategy="EQUAL_WEIGHT_ELIGIBLE_UNIVERSE"),
    ], ignore_index=True)
    all_returns.to_csv(OUT / "06_strategy_baselines_weekly_returns.csv", index=False)
    pd.DataFrame([
        compare_rows("FMOM_SELECTED", frozen_r),
        compare_rows("STATIC_EQUAL_FACTOR", static_r),
        compare_rows("EQUAL_WEIGHT_ELIGIBLE_UNIVERSE", universe_r),
    ]).to_csv(OUT / "06_strategy_baselines_oos_summary.csv", index=False)
    summary = pd.read_csv(OUT / "06_strategy_baselines_oos_summary.csv")
    (OUT / "06_strategy_baselines_notes.json").write_text(json.dumps({
        "selected_factors": selected,
        "cost_bps_one_way": sg.ONE_WAY_COST_BPS,
        "scope": "2021-latest only; no benchmark-total-return claim",
        "interpretation": "FMOM must beat STATIC_EQUAL_FACTOR before attributing improvement to factor timing.",
    }, indent=2), encoding="utf-8")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
