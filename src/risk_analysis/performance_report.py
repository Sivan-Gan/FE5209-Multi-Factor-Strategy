
"""Member 5 - US Historical OOS Performance Evaluation.

Uses the frozen Member 4 weekly path without changing strategy rules.
Run from repository root:
    python -m src.risk_analysis.performance_report
"""

from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from src.strategy import metrics as mt


ROOT = Path(__file__).resolve().parents[2]

INPUT_DIR = ROOT / "outputs/strategy/frozen/hist_oos"
RF_FILE = ROOT / "handoff/us_downstream/data/risk_free_weekly.parquet"
OUTPUT_DIR = ROOT / "outputs/risk_analysis/us_hist_oos"

COST_ONEWAY = 10 / 10000
START = "2021-01-01"
END = "2025-12-31"
TOL = 1e-10


def load_data():
    path = pd.read_csv(INPUT_DIR / "weekly_path.csv")
    rf = pd.read_parquet(RF_FILE)

    required = {
        "signal_date", "period_end", "gross_return",
        "net_return", "gtr", "turnover"
    }
    missing = required - set(path.columns)
    if missing:
        raise ValueError(f"Missing weekly_path columns: {missing}")

    if not {"signal_date", "risk_free_return"}.issubset(rf.columns):
        raise ValueError("Risk-free file has unexpected columns")

    path["signal_date"] = pd.to_datetime(path["signal_date"])
    path["period_end"] = pd.to_datetime(path["period_end"])
    rf["signal_date"] = pd.to_datetime(rf["signal_date"])

    path = path.sort_values("signal_date").reset_index(drop=True)

    if path["signal_date"].duplicated().any():
        raise ValueError("Duplicate signal dates")

    if not path["signal_date"].between(START, END).all():
        raise ValueError("Signal date outside OOS")

    if (path["period_end"] > pd.Timestamp(END)).any():
        raise ValueError("Holding period extends beyond OOS cutoff")

    if rf["signal_date"].duplicated().any():
        raise ValueError("Duplicate risk-free dates")

    path = path.merge(
        rf[["signal_date", "risk_free_return"]],
        on="signal_date",
        how="left",
        validate="one_to_one"
    )

    numeric = [
        "gross_return", "net_return", "gtr",
        "turnover", "risk_free_return"
    ]

    if not np.isfinite(path[numeric].to_numpy(dtype=float)).all():
        raise ValueError("Missing or non-finite returns, turnover, or RF")

    return path


def validate_accounting(df):
    expected = (
        (1 - COST_ONEWAY * df["gtr"])
        * (1 + df["gross_return"]) - 1
    )

    error = (expected - df["net_return"]).abs().max()

    if error > TOL:
        raise ValueError(f"Gross-to-net closure failed: {error}")

    turnover_error = (
        df["turnover"] - 0.5 * df["gtr"]
    ).abs().max()

    if turnover_error > TOL:
        raise ValueError("Turnover convention mismatch")

    if (df["gtr"] < 0).any():
        raise ValueError("Negative GTR")

    print(f"Accounting closure PASS: {error:.3e}")
    print(f"Turnover convention PASS: {turnover_error:.3e}")


def build_nav(df):
    result = df[
        ["signal_date", "period_end", "gross_return", "net_return"]
    ].copy()

    result["gross_nav"] = (1 + result["gross_return"]).cumprod()
    result["net_nav"] = (1 + result["net_return"]).cumprod()

    # Include initial NAV=1 when computing drawdown.
    gross_peak = np.maximum(
        1.0, result["gross_nav"].cummax()
    )
    net_peak = np.maximum(
        1.0, result["net_nav"].cummax()
    )

    result["gross_drawdown"] = result["gross_nav"] / gross_peak - 1
    result["net_drawdown"] = result["net_nav"] / net_peak - 1

    return result


def calculate_performance(df):
    gross = df.set_index("signal_date")["gross_return"]
    net = df.set_index("signal_date")["net_return"]
    rf = df.set_index("signal_date")["risk_free_return"]

    gross_perf = mt.performance(gross, rf=rf)
    net_perf = mt.performance(net, rf=rf)

    summary = {
        "strategy": "J26_lam0.5_ra1_min4_buf1",
        "sample_start": str(df["signal_date"].min().date()),
        "sample_end": str(df["period_end"].max().date()),
        "n_weeks": len(df),
        "gross_ann_return": gross_perf["ann_return"],
        "net_ann_return": net_perf["ann_return"],
        "gross_ann_vol": gross_perf["ann_vol"],
        "net_ann_vol": net_perf["ann_vol"],
        "gross_sharpe": gross_perf["sharpe"],
        "net_sharpe": net_perf["sharpe"],
        "net_sortino": net_perf["sortino"],
        "net_max_drawdown": net_perf["max_drawdown"],
        "net_calmar": net_perf["calmar"],
        "net_hit_rate": net_perf["period_hit_rate"],
        "net_worst_week": net_perf["worst_week"],
        "net_best_week": net_perf["best_week"],
        "ann_turnover": df["turnover"].mean() * 52,
        "avg_weekly_gtr": df["gtr"].mean(),
        "cost_drag_ann": (
            gross_perf["ann_return"] - net_perf["ann_return"]
        ),
    }

    return pd.DataFrame([summary])


def calculate_yearly(df):
    yearly = []

    for year, group in df.groupby(df["signal_date"].dt.year):
        yearly.append({
            "year": year,
            "n_weeks": len(group),
            "gross_return": (1 + group["gross_return"]).prod() - 1,
            "net_return": (1 + group["net_return"]).prod() - 1,
            "avg_weekly_turnover": group["turnover"].mean(),
            "total_gtr": group["gtr"].sum(),
        })

    return pd.DataFrame(yearly)


def make_plots(nav):
    plt.figure(figsize=(11, 6))
    plt.plot(nav["period_end"], nav["gross_nav"], label="Gross NAV")
    plt.plot(nav["period_end"], nav["net_nav"], label="Net NAV")
    plt.title("US FMOM - Historical OOS Cumulative NAV")
    plt.xlabel("Holding Period End")
    plt.ylabel("Growth of $1")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "cumulative_returns.png", dpi=200)
    plt.close()

    plt.figure(figsize=(11, 5))
    plt.plot(
        nav["period_end"],
        nav["net_drawdown"] * 100,
        label="Net Drawdown"
    )
    plt.axhline(0, linewidth=0.8)
    plt.title("US FMOM - Historical OOS Drawdown")
    plt.xlabel("Holding Period End")
    plt.ylabel("Drawdown (%)")
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "drawdown.png", dpi=200)
    plt.close()


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    df = load_data()
    validate_accounting(df)

    nav = build_nav(df)
    performance = calculate_performance(df)
    yearly = calculate_yearly(df)

    nav.to_csv(OUTPUT_DIR / "weekly_nav.csv", index=False)
    performance.to_csv(
        OUTPUT_DIR / "performance_summary.csv", index=False
    )
    yearly.to_csv(
        OUTPUT_DIR / "yearly_performance.csv", index=False
    )

    make_plots(nav)

    print("\nUS Historical OOS Performance")
    print(performance.T.to_string(header=False))
    print("\nYearly Performance")
    print(yearly.to_string(index=False))
    print(f"\nResults saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
