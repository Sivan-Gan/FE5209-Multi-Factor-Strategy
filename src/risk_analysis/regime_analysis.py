
"""Member 5: US OOS Market Regime Analysis.

Uses lagged market information to classify regimes.
Run:
    python -m src.risk_analysis.regime_analysis
"""

from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/risk_analysis/us_hist_oos"

DYNAMIC = ROOT / "outputs/strategy/frozen/hist_oos/weekly_path.csv"
STATIC = ROOT / "outputs/strategy/benchmarks/hist_oos/static_weekly_path.csv"
EW = OUT / "universe_ew_weekly_path.csv"
BENCH = ROOT / "handoff/us_downstream/data/benchmark_weekly_execution_returns.parquet"

WINDOW = 26
ANNUAL = 52


def load_data():
    paths = {
        "Dynamic": pd.read_csv(DYNAMIC),
        "Static": pd.read_csv(STATIC),
        "Universe_EW": pd.read_csv(EW),
    }

    market = pd.read_parquet(BENCH)[
        ["signal_date", "benchmark_total_return"]
    ].rename(columns={"benchmark_total_return": "Market"})

    if market["signal_date"].duplicated().any():
        raise ValueError("Duplicate benchmark dates")

    df = market.sort_values("signal_date").reset_index(drop=True)

    for name, p in paths.items():
        if p["signal_date"].duplicated().any():
            raise ValueError(f"Duplicate dates: {name}")

        p = p[["signal_date", "period_end", "net_return"]]
        p = p.rename(columns={
            "period_end": f"end_{name}",
            "net_return": name,
        })

        df = df.merge(
            p, on="signal_date",
            how="left", validate="one_to_one"
        )

    # Build regime signals on the full benchmark history,
    # before extracting the 259 OOS strategy periods.
    past_market = df["Market"].shift(1)

    df["past_26w_return"] = (
        (1 + past_market)
        .rolling(WINDOW, min_periods=WINDOW)
        .apply(np.prod, raw=True) - 1
    )

    df["past_26w_vol"] = (
        past_market.rolling(WINDOW, min_periods=WINDOW)
        .std(ddof=1) * np.sqrt(ANNUAL)
    )

    # Expanding historical median, also excluding the
    # current signal's most recent market observation.
    df["historical_vol_median"] = (
        df["past_26w_vol"]
        .shift(1)
        .expanding(min_periods=52)
        .median()
    )

    df["trend_regime"] = np.where(
        df["past_26w_return"] >= 0,
        "Bull", "Bear"
    )

    df["vol_regime"] = np.where(
        df["past_26w_vol"] > df["historical_vol_median"],
        "High Vol", "Low Vol"
    )

    df = df[df["Dynamic"].notna()].copy()

    if len(df) != 259:
        raise ValueError(f"Expected 259 weeks, got {len(df)}")

    for name in ["Static", "Universe_EW", "Market"]:
        if df[name].isna().any():
            raise ValueError(f"Missing returns: {name}")

    if not (
        df["end_Dynamic"].eq(df["end_Static"]).all()
        and df["end_Dynamic"].eq(df["end_Universe_EW"]).all()
    ):
        raise ValueError("Holding-period mismatch")

    if df[
        ["past_26w_return", "past_26w_vol",
         "historical_vol_median"]
    ].isna().any().any():
        raise ValueError("Insufficient historical regime data")

    df["increment"] = df["Dynamic"] - df["Static"]

    return df


def regime_summary(df, regime_col):
    rows = []

    for regime, g in df.groupby(regime_col):
        for name in [
            "Dynamic", "Static", "Universe_EW", "Market",
            "increment"
        ]:
            r = g[name]

            rows.append({
                "regime_type": regime_col,
                "regime": regime,
                "strategy": name,
                "n_weeks": len(g),
                "mean_weekly_return": r.mean(),
                "annualized_arithmetic_mean": r.mean() * ANNUAL,
                "annualized_vol": r.std(ddof=1) * np.sqrt(ANNUAL),
                "positive_week_rate": (r > 0).mean(),
            })

    return pd.DataFrame(rows)


def make_plot(summary):
    data = summary[
        summary["strategy"].isin(
            ["Dynamic", "Static", "Universe_EW", "Market"]
        )
    ].copy()

    for regime_type in ["trend_regime", "vol_regime"]:
        g = data[data["regime_type"] == regime_type]

        pivot = g.pivot(
            index="regime",
            columns="strategy",
            values="annualized_arithmetic_mean"
        )

        ax = (pivot * 100).plot(
            kind="bar", figsize=(10, 6)
        )

        ax.set_title(f"US OOS: {regime_type}")
        ax.set_ylabel("Annualized Arithmetic Mean (%)")
        ax.set_xlabel("")
        ax.axhline(0, linewidth=0.8)
        ax.grid(axis="y", alpha=0.3)

        plt.tight_layout()
        plt.savefig(
            OUT / f"{regime_type}_performance.png",
            dpi=200
        )
        plt.close()


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    df = load_data()

    summary = pd.concat([
        regime_summary(df, "trend_regime"),
        regime_summary(df, "vol_regime"),
    ], ignore_index=True)

    df.to_csv(
        OUT / "regime_weekly.csv", index=False
    )

    summary.to_csv(
        OUT / "regime_performance_summary.csv",
        index=False
    )

    make_plot(summary)

    print("\n=== Regime Counts ===")
    print(df["trend_regime"].value_counts())
    print(df["vol_regime"].value_counts())

    print("\n=== Regime Performance ===")
    print(
        summary.to_string(
            index=False,
            float_format=lambda x: f"{x:.5f}"
        )
    )

    print("\nResults saved to:", OUT)


if __name__ == "__main__":
    main()
