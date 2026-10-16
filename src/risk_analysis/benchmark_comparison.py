
"""Member 5: US OOS Dynamic FMOM vs S&P 500 benchmark."""

from pathlib import Path
import numpy as np
import pandas as pd

from src.strategy import metrics as mt


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/risk_analysis/us_hist_oos"


def main():
    dynamic = pd.read_csv(
        ROOT / "outputs/strategy/frozen/hist_oos/weekly_path.csv"
    )

    benchmark = pd.read_parquet(
        ROOT / "handoff/us_downstream/data/"
        "benchmark_weekly_execution_returns.parquet"
    )

    rf = pd.read_parquet(
        ROOT / "handoff/us_downstream/data/risk_free_weekly.parquet"
    )

    df = dynamic.merge(
        benchmark[
            ["signal_date", "next_execution_date",
             "benchmark_total_return"]
        ],
        on="signal_date",
        how="left",
        validate="one_to_one",
    ).merge(
        rf[["signal_date", "risk_free_return"]],
        on="signal_date",
        how="left",
        validate="one_to_one",
    )

    if len(df) != len(dynamic):
        raise ValueError("Unexpected number of merged rows")

    if not (
        pd.to_datetime(df["period_end"])
        == pd.to_datetime(df["next_execution_date"])
    ).all():
        raise ValueError("Holding periods do not align")

    cols = [
        "net_return",
        "benchmark_total_return",
        "risk_free_return",
    ]
    if not np.isfinite(df[cols].to_numpy(dtype=float)).all():
        raise ValueError("Missing or invalid returns")

    df = df.sort_values("signal_date").set_index("signal_date")

    strategy = df["net_return"]
    market = df["benchmark_total_return"]
    risk_free = df["risk_free_return"]

    # Reuse the shared M4 metrics implementation.
    strategy_perf = mt.performance(strategy, risk_free, market)
    market_perf = mt.performance(market, risk_free)

    rows = []
    for name, p in [
        ("Dynamic FMOM", strategy_perf),
        ("S&P 500 Total Return", market_perf),
    ]:
        rows.append({
            "strategy": name,
            "CAGR": p["ann_return"],
            "Annual Volatility": p["ann_vol"],
            "Sharpe": p["sharpe"],
            "Sortino": p["sortino"],
            "Max Drawdown": p["max_drawdown"],
            "Calmar": p["calmar"],
        })

    summary = pd.DataFrame(rows)

    df["active_return"] = strategy - market
    df["dynamic_nav"] = (1 + strategy).cumprod()
    df["market_nav"] = (1 + market).cumprod()

    # Active return is arithmetic difference per aligned week.
    tracking_error = df["active_return"].std(ddof=1) * np.sqrt(52)
    information_ratio = (
        df["active_return"].mean() * 52 / tracking_error
        if tracking_error > 0 else np.nan
    )

    beta = strategy.cov(market) / market.var()

    print("\n=== OOS Performance Comparison ===")
    print(summary.to_string(index=False))

    print("\n=== Benchmark-relative Metrics ===")
    print(f"Annualized Active Return: {df['active_return'].mean()*52:.4%}")
    print(f"Tracking Error:           {tracking_error:.4%}")
    print(f"Information Ratio:        {information_ratio:.4f}")
    print(f"Market Beta:              {beta:.4f}")

    OUT.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT / "benchmark_summary.csv", index=False)
    df.reset_index().to_csv(
        OUT / "benchmark_aligned_weekly.csv", index=False
    )


if __name__ == "__main__":
    main()
