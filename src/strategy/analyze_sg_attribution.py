#!/usr/bin/env python3
"""Descriptive, post-OOS attribution for the separately frozen SG strategy.

This script deliberately does not alter the 2016--2020 selected configuration.
It breaks down the already-frozen 2021+ results by calendar year, factor signal,
factor weight, turnover/cost, and sector exposure.  Results are diagnostics,
not a source of new parameter selection.
"""
from __future__ import annotations

from pathlib import Path
import json

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs" / "strategy" / "sg_independent"
ATTR = OUT / "07_attribution_oos"
OOS_START = pd.Timestamp("2021-01-01")
ANNUAL = 52


def metrics(x: pd.Series) -> dict[str, float]:
    x = x.dropna()
    if not len(x):
        return {"weeks": 0, "cumulative_return": np.nan, "annualized_return": np.nan,
                "annualized_volatility": np.nan, "sharpe_zero_rf": np.nan, "max_drawdown": np.nan}
    wealth = (1 + x).cumprod()
    years = len(x) / ANNUAL
    ann = wealth.iloc[-1] ** (1 / years) - 1 if years else np.nan
    vol = x.std(ddof=1) * np.sqrt(ANNUAL)
    sharpe = x.mean() / x.std(ddof=1) * np.sqrt(ANNUAL) if x.std(ddof=1) else np.nan
    dd = 1 - wealth / wealth.cummax()
    return {"weeks": len(x), "cumulative_return": wealth.iloc[-1] - 1,
            "annualized_return": ann, "annualized_volatility": vol,
            "sharpe_zero_rf": sharpe, "max_drawdown": dd.max()}


def main() -> None:
    ATTR.mkdir(exist_ok=True)
    ret = pd.read_csv(OUT / "06_strategy_baselines_weekly_returns.csv", parse_dates=["signal_date"])
    ret = ret[ret.signal_date.ge(OOS_START)].copy()
    ret["year"] = ret.signal_date.dt.year

    # Year-level comparison: the clearest diagnostic of when timing helped/hurt.
    yearly = (ret.groupby(["year", "strategy"])
              .agg(weeks=("net_return", "size"), cumulative_return=("net_return", lambda x: (1 + x).prod() - 1),
                   annualized_volatility=("net_return", lambda x: x.std(ddof=1) * np.sqrt(ANNUAL)),
                   sharpe_zero_rf=("net_return", lambda x: x.mean() / x.std(ddof=1) * np.sqrt(ANNUAL) if x.std(ddof=1) else np.nan),
                   mean_one_way_turnover=("turnover", "mean"))
              .reset_index())
    yearly.to_csv(ATTR / "01_oos_yearly_strategy_comparison.csv", index=False)

    overall = pd.DataFrame([
        {"strategy": name, **metrics(g.net_return), "mean_one_way_turnover": g.turnover.mean(),
         "mean_weekly_cost": (g.gross_return - g.net_return).mean()}
        for name, g in ret.groupby("strategy")
    ])
    overall.to_csv(ATTR / "02_oos_overall_strategy_comparison.csv", index=False)

    # Actual dynamic factor allocation and each factor's standalone L/S evidence.
    weights = pd.read_csv(OUT / "03_frozen_sg_factor_weights_2016_latest.csv", parse_dates=["signal_date"])
    weights = weights[weights.signal_date.ge(OOS_START)].copy()
    weights["year"] = weights.signal_date.dt.year
    (weights.groupby(["year", "family"], as_index=False)
            .agg(mean_weight=("weight", "mean"), median_weight=("weight", "median"),
                 zero_weight_share=("weight", lambda x: (x == 0).mean()))
            .to_csv(ATTR / "03_oos_factor_weight_attribution.csv", index=False))

    ic = pd.read_csv(OUT / "01_factor_ic_weekly.csv", parse_dates=["signal_date"])
    ls = pd.read_csv(OUT / "01_factor_long_short_returns_weekly.csv", parse_dates=["signal_date"])
    factor = ic.merge(ls[["signal_date", "family", "factor_return", "available"]], on=["signal_date", "family"], how="left")
    factor = factor[(factor.signal_date.ge(OOS_START)) & factor.available.eq(1)].copy()
    factor["year"] = factor.signal_date.dt.year
    fac = (factor.groupby(["year", "family"], as_index=False)
           .agg(weeks=("ic", "count"), mean_ic=("ic", "mean"),
                long_short_cumulative=("factor_return", lambda x: (1 + x).prod() - 1),
                long_short_sharpe=("factor_return", lambda x: x.mean() / x.std(ddof=1) * np.sqrt(ANNUAL) if x.std(ddof=1) else np.nan)))
    fac.to_csv(ATTR / "04_oos_single_factor_attribution.csv", index=False)

    # Sector exposure of the realized FMOM holdings.  This is exposure, not causal return attribution.
    hold = pd.read_csv(OUT / "03_frozen_sg_holdings_2016_latest.csv", parse_dates=["signal_date"])
    hold = hold[hold.signal_date.ge(OOS_START)].copy()
    hold["year"] = hold.signal_date.dt.year
    # First aggregate all names inside a sector at each rebalance, then average
    # those portfolio-level exposures.  Averaging individual stock weights here
    # would understate sector exposure by the number of names in the sector.
    sector_weekly = (hold.groupby(["signal_date", "year", "six_sector"], as_index=False)
                     .agg(portfolio_weight=("weight", "sum"), name_count=("ticker", "size")))
    sector = (sector_weekly.groupby(["year", "six_sector"], as_index=False)
              .agg(mean_portfolio_weight=("portfolio_weight", "mean"),
                   mean_name_count=("name_count", "mean")))
    sector.to_csv(ATTR / "05_oos_fmom_sector_exposure.csv", index=False)

    # Training selection context, preserved separately from OOS diagnostics.
    grid = pd.read_csv(OUT / "02_configuration_grid_2016_2020.csv")
    grid.head(10).to_csv(ATTR / "06_training_top10_configuration_grid.csv", index=False)
    static = grid[grid["lambda"].eq(0)].sort_values("weekly_sharpe_zero_rf", ascending=False).head(1)
    selected = grid.iloc[[0]]
    selection = pd.concat([selected.assign(comparison="selected_dynamic"), static.assign(comparison="best_static_in_grid")])
    selection.to_csv(ATTR / "07_training_dynamic_vs_static.csv", index=False)

    selected_row = overall.loc[overall.strategy.eq("FMOM_SELECTED")].iloc[0]
    static_row = overall.loc[overall.strategy.eq("STATIC_EQUAL_FACTOR")].iloc[0]
    notes = {
        "scope": "Descriptive OOS attribution only; no parameter has been re-selected using 2021+ data.",
        "core_finding": "The dynamic model won the current 2016-2020 grid, but the static equal-factor benchmark is superior in OOS on annualized return, Sharpe, drawdown and turnover.",
        "oos_fmom_annualized_return": float(selected_row.annualized_return),
        "oos_static_annualized_return": float(static_row.annualized_return),
        "oos_fmom_sharpe": float(selected_row.sharpe_zero_rf),
        "oos_static_sharpe": float(static_row.sharpe_zero_rf),
        "limitation": "Factor-level files support standalone signal diagnostics. They do not identify a causal return contribution of each factor to the nonlinear top-20% stock portfolio.",
    }
    (ATTR / "README.json").write_text(json.dumps(notes, indent=2), encoding="utf-8")
    print(pd.read_csv(ATTR / "07_training_dynamic_vs_static.csv").to_string(index=False))
    print("\nOOS overall")
    print(overall.to_string(index=False))


if __name__ == "__main__":
    main()
