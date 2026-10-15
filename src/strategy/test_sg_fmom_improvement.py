#!/usr/bin/env python3
"""Pre-OOS internal holdout test for the SG FMOM configuration grid.

This is intentionally confined to 2016--2020.  It answers whether the dynamic
selection rule is stable before looking at 2021+ and is not allowed to change
the already reported frozen OOS result.
"""
from __future__ import annotations

from pathlib import Path
import pandas as pd

from src.strategy import run_sg_independent_research as sg


OUT = sg.OUT / "07_attribution_oos"
TRAIN_END = pd.Timestamp("2018-12-31")
VALID_START = pd.Timestamp("2019-01-01")
VALID_END = pd.Timestamp("2020-12-31")


def tag(c: dict) -> str:
    return f"J{c['J']}_lam{c['lambda']:g}_ra{int(c['risk_adjusted'])}_buf{int(c['buffer'])}"


def main() -> None:
    prices, calendar = sg.prepare_prices()
    funds = sg.prepare_fundamentals(set(prices.ticker))
    scores, _ = sg.make_scores(prices, calendar, funds)
    fret, ic = sg.factor_returns(scores)
    _, selected = sg.selection_summary(fret, ic)
    signals = sorted(scores[scores.signal_date.ge(pd.Timestamp(sg.CONFIG_START))].signal_date.unique())
    rows = []
    # A transparent stability check: compare the full-window FMOM winner with
    # the static control.  It is intentionally *not* a second exhaustive search
    # that could manufacture another preferred setting after seeing results.
    candidates = [
        {"J": 13, "lambda": 1.0, "risk_adjusted": False, "buffer": False},
        {"J": 26, "lambda": 0.0, "risk_adjusted": False, "buffer": False},
    ]
    for c in candidates:
        weights = sg.fmom_weights(fret, selected, c, signals)
        holdings, _ = sg.score_and_hold(scores, weights, selected, c["buffer"], pd.Timestamp(VALID_END))
        returns = sg.path(holdings, sg.ONE_WAY_COST_BPS)
        tr = returns[returns.signal_date.le(TRAIN_END)]
        va = returns[(returns.signal_date.ge(VALID_START)) & (returns.signal_date.le(VALID_END))]
        rows.append({"tag": tag(c), **c,
                     **{f"train_{k}": v for k, v in sg.metrics(tr).items()},
                     **{f"validation_{k}": v for k, v in sg.metrics(va).items()}})
    result = pd.DataFrame(rows).sort_values(["train_weekly_sharpe_zero_rf", "train_annualized_net_return"], ascending=False)
    result.to_csv(OUT / "08_internal_walkforward_model_test.csv", index=False)
    winner = result.iloc[0]
    best_static = result[result["lambda"].eq(0)].iloc[0]
    comparison = pd.DataFrame([winner, best_static])
    comparison.insert(0, "comparison", ["higher_sharpe_2016_2018", "static_control"])
    comparison.to_csv(OUT / "09_internal_holdout_dynamic_vs_static.csv", index=False)
    print(comparison[["comparison", "tag", "train_weekly_sharpe_zero_rf", "validation_annualized_net_return", "validation_weekly_sharpe_zero_rf", "validation_max_drawdown", "validation_average_one_way_turnover"]].to_string(index=False))


if __name__ == "__main__":
    main()
