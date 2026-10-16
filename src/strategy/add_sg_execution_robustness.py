#!/usr/bin/env python3
"""Execution robustness diagnostics for the frozen SG results.

Writes descriptive OOS cost sensitivity, a HAC test of FMOM-minus-static active
returns, and a 10%-ADV capacity proxy.  No result here is used to retune the
already frozen 2021+ configuration.
"""
from __future__ import annotations

from pathlib import Path
import json

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
OUT = ROOT / "outputs/strategy/sg_independent/08_execution_robustness"
PRICE_FILE = WORKSPACE / "SGX_factor_research/output_lseg_rebuilt_for_sg_strategy/data/prices_daily_2006.csv"
OOS_START = pd.Timestamp("2021-01-01")
WEEKS = 52
COST_SCENARIOS_BPS = [0, 10, 20, 40, 60]


def metrics(r: pd.Series) -> dict[str, float]:
    r = r.dropna()
    nav = (1 + r).cumprod()
    ann = nav.iloc[-1] ** (WEEKS / len(r)) - 1
    vol = r.std(ddof=1) * np.sqrt(WEEKS)
    return {"weeks": int(len(r)), "cumulative_return": float(nav.iloc[-1] - 1),
            "annualized_return": float(ann), "annualized_volatility": float(vol),
            "sharpe_zero_rf": float(r.mean() / r.std(ddof=1) * np.sqrt(WEEKS)),
            "max_drawdown": float((1 - nav / nav.cummax()).max())}


def hac_mean_test(x: pd.Series, lag: int = 4) -> dict[str, float]:
    """Newey-West/Bartlett t-test of a weekly mean active return."""
    x = x.dropna().to_numpy(dtype=float)
    n = len(x)
    centered = x - x.mean()
    long_run_var = np.dot(centered, centered) / n
    for j in range(1, min(lag, n - 1) + 1):
        gamma = np.dot(centered[j:], centered[:-j]) / n
        long_run_var += 2 * (1 - j / (lag + 1)) * gamma
    se = np.sqrt(long_run_var / n)
    return {"weeks": n, "newey_west_lag": lag, "mean_weekly_active_return": float(x.mean()),
            "annualized_mean_active_return_simple": float(x.mean() * WEEKS),
            "hac_standard_error": float(se), "hac_t_stat": float(x.mean() / se) if se else np.nan}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    returns = pd.read_csv(ROOT / "outputs/strategy/sg_independent/06_strategy_baselines_weekly_returns.csv",
                          parse_dates=["signal_date"])
    returns = returns[returns.signal_date.ge(OOS_START)].copy()

    # Recalculate each strategy under identical per-side cost scenarios from
    # saved gross return and realized GTR. Turnover is retained as the
    # conventional one-way reporting statistic (GTR / 2).
    cost_rows, paths = [], []
    for cost in COST_SCENARIOS_BPS:
        q = returns.copy()
        q["scenario_cost_bps"] = cost
        q["scenario_net_return"] = (1 - q.gtr * cost / 1e4) * (1 + q.gross_return) - 1
        paths.append(q[["signal_date", "strategy", "scenario_cost_bps", "scenario_net_return", "gtr", "turnover"]])
        for strategy, g in q.groupby("strategy"):
            cost_rows.append({"strategy": strategy, "one_way_cost_bps": cost,
                              "mean_one_way_turnover": float(g.turnover.mean()), **metrics(g.scenario_net_return)})
    pd.DataFrame(cost_rows).to_csv(OUT / "01_cost_sensitivity_oos.csv", index=False)
    pd.concat(paths, ignore_index=True).to_csv(OUT / "02_cost_sensitivity_weekly_paths.csv", index=False)

    # Paired FMOM/static inference under each equal-cost scenario.
    tests = []
    for cost in COST_SCENARIOS_BPS:
        q = pd.concat(paths, ignore_index=True)
        wide = q[q.scenario_cost_bps.eq(cost)].pivot(index="signal_date", columns="strategy", values="scenario_net_return")
        active = wide["FMOM_SELECTED"] - wide["STATIC_EQUAL_FACTOR"]
        tests.append({"comparison": "FMOM_SELECTED minus STATIC_EQUAL_FACTOR", "one_way_cost_bps": cost,
                      **hac_mean_test(active, lag=4)})
    pd.DataFrame(tests).to_csv(OUT / "03_fmom_minus_static_hac_test.csv", index=False)

    # Capacity proxy: prior 60 trading-day median trading value.  For each
    # equal-weight holding, max AUM is 10% ADV divided by target weight.
    prices = pd.read_csv(PRICE_FILE, usecols=["date", "ticker", "trading_value"], parse_dates=["date"])
    prices = prices.sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"])
    prices["trading_value"] = pd.to_numeric(prices.trading_value, errors="coerce")
    prices["adv60_prior_sgd"] = prices.groupby("ticker").trading_value.transform(
        lambda x: x.shift(1).rolling(60, min_periods=40).median())
    hold = pd.read_csv(ROOT / "outputs/strategy/sg_independent/03_frozen_sg_holdings_2016_latest.csv",
                       parse_dates=["signal_date", "execution_date"])
    hold = hold[hold.signal_date.ge(OOS_START)].copy()
    hold = hold.merge(prices[["date", "ticker", "adv60_prior_sgd"]],
                      left_on=["execution_date", "ticker"], right_on=["date", "ticker"], how="left")
    hold["max_aum_sgd_at_10pct_adv"] = 0.10 * hold.adv60_prior_sgd / hold.weight
    per_rebalance = (hold.groupby(["signal_date", "execution_date"], as_index=False)
                     .agg(held_names=("ticker", "size"), matched_adv_share=("adv60_prior_sgd", lambda x: x.notna().mean()),
                          limiting_capacity_sgd=("max_aum_sgd_at_10pct_adv", "min"),
                          p25_capacity_sgd=("max_aum_sgd_at_10pct_adv", lambda x: x.quantile(.25)),
                          median_capacity_sgd=("max_aum_sgd_at_10pct_adv", "median")))
    per_rebalance.to_csv(OUT / "04_fmom_capacity_proxy_by_rebalance.csv", index=False)
    summary = {"method": "Capacity proxy only: 10% of each holding's prior 60-trading-day median SGD trading value divided by target weight; ignores netting, market impact and order scheduling.",
               "period": "2021-latest", "rebalances": int(len(per_rebalance)),
               "median_limiting_capacity_sgd": float(per_rebalance.limiting_capacity_sgd.median()),
               "p25_limiting_capacity_sgd": float(per_rebalance.limiting_capacity_sgd.quantile(.25)),
               "median_adv_match_share": float(per_rebalance.matched_adv_share.median())}
    (OUT / "05_capacity_proxy_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(pd.DataFrame(cost_rows).query("one_way_cost_bps == 20").to_string(index=False))
    print(pd.DataFrame(tests).to_string(index=False))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
