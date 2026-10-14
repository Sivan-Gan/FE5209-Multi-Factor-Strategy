
"""Member 5: US historical stress and risk map.

Run:
    python -m src.risk_analysis.stress_analysis
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

ANNUAL = 52


def load_data():
    paths = {
        "Dynamic": DYNAMIC,
        "Static": STATIC,
        "Universe_EW": EW,
    }

    frames = []

    for name, path in paths.items():
        df = pd.read_csv(path)
        df = df[
            ["signal_date", "period_end", "net_return"]
        ].rename(columns={
            "period_end": f"end_{name}",
            "net_return": name
        })

        if df["signal_date"].duplicated().any():
            raise ValueError(f"Duplicate dates: {name}")

        frames.append(df)

    data = frames[0]

    for frame in frames[1:]:
        data = data.merge(
            frame, on="signal_date",
            how="inner", validate="one_to_one"
        )

    market = pd.read_parquet(BENCH)[
        ["signal_date", "next_execution_date",
         "benchmark_total_return"]
    ].rename(columns={
        "benchmark_total_return": "Market"
    })

    data = data.merge(
        market, on="signal_date",
        how="left", validate="one_to_one"
    )

    if len(data) != 259:
        raise ValueError("Expected 259 completed OOS weeks")

    ends = [
        "end_Dynamic", "end_Static",
        "end_Universe_EW", "next_execution_date"
    ]

    for col in ends[1:]:
        if not data[col].eq(data[ends[0]]).all():
            raise ValueError("Holding-period mismatch")

    cols = ["Dynamic", "Static", "Universe_EW", "Market"]

    if not np.isfinite(data[cols].to_numpy()).all():
        raise ValueError("Invalid returns")

    data["increment"] = data["Dynamic"] - data["Static"]

    return data.sort_values("signal_date").reset_index(drop=True)


def classify_stress(df):
    result = df.copy()

    # Realized market stress: descriptive, not a
    # pre-trade forecasting signal.
    q10 = result["Market"].quantile(0.10)
    q90 = result["Market"].quantile(0.90)

    result["stress_regime"] = np.select(
        [
            result["Market"] <= q10,
            result["Market"] >= q90,
        ],
        [
            "Market Crash Weeks",
            "Market Rally Weeks",
        ],
        default="Normal Weeks"
    )

    # Ex-post strategy-specific failure indicator.
    threshold = result["increment"].quantile(0.10)

    result["severe_fmom_underperformance"] = (
        result["increment"] <= threshold
    )

    return result, q10, q90, threshold


def summarize(df):
    rows = []

    for regime, g in df.groupby("stress_regime"):
        for strategy in [
            "Dynamic", "Static", "Universe_EW",
            "Market", "increment"
        ]:
            r = g[strategy]

            rows.append({
                "regime": regime,
                "strategy": strategy,
                "n_weeks": len(g),
                "mean_return": r.mean(),
                "annualized_arithmetic_mean": r.mean() * ANNUAL,
                "volatility": r.std(ddof=1) * np.sqrt(ANNUAL),
                "positive_week_rate": (r > 0).mean(),
            })

    return pd.DataFrame(rows)


def make_plot(summary):
    g = summary[
        summary["strategy"].isin(
            ["Dynamic", "Static", "Universe_EW", "Market"]
        )
    ]

    pivot = g.pivot(
        index="regime",
        columns="strategy",
        values="mean_return"
    )

    ax = (pivot * 100).plot(
        kind="bar", figsize=(11, 6)
    )

    ax.set_title("US OOS: Realized Market Stress")
    ax.set_ylabel("Mean Holding-Period Return (%)")
    ax.set_xlabel("")
    ax.axhline(0, linewidth=0.8)
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    plt.savefig(
        OUT / "historical_stress_performance.png",
        dpi=200
    )
    plt.close()


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    df = load_data()
    classified, q10, q90, threshold = classify_stress(df)
    summary = summarize(classified)

    worst = classified.nsmallest(
        15, "increment"
    )[
        ["signal_date", "end_Dynamic", "Dynamic",
         "Static", "Market", "increment",
         "stress_regime"]
    ]

    classified.to_csv(
        OUT / "historical_stress_weekly.csv",
        index=False
    )

    summary.to_csv(
        OUT / "historical_stress_summary.csv",
        index=False
    )

    worst.to_csv(
        OUT / "worst_fmom_weeks.csv",
        index=False
    )

    make_plot(summary)

    print("\n=== Stress Thresholds ===")
    print("Market 10th percentile:", f"{q10:.4%}")
    print("Market 90th percentile:", f"{q90:.4%}")
    print("FMOM increment 10th percentile:",
          f"{threshold:.4%}")

    print("\n=== Historical Stress Performance ===")
    print(
        summary.to_string(
            index=False,
            float_format=lambda x: f"{x:.5f}"
        )
    )

    print("\n=== Worst FMOM Weeks ===")
    print(worst.to_string(index=False))

    print("\nResults saved to:", OUT)


if __name__ == "__main__":
    main()
