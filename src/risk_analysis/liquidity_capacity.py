
"""Member 5: US OOS Liquidity and Capacity Diagnostics.

Uses actual drift-adjusted trade ledgers and historical ADV.
No strategy parameters or frozen outputs are modified.

Run:
    python -m src.risk_analysis.liquidity_capacity
"""

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/risk_analysis/us_hist_oos"

LIQUIDITY = (
    ROOT / "handoff/us_downstream/data/"
    "execution_liquidity_weekly.parquet"
)

LEDGERS = {
    "Dynamic FMOM":
        ROOT / "outputs/strategy/frozen/hist_oos/trade_ledger.csv",
    "Static MF":
        ROOT / "outputs/strategy/benchmarks/hist_oos/"
        "static_trade_ledger.csv",
    "Universe EW":
        OUT / "universe_ew_trade_ledger.csv",
}

OOS_PATH = (
    ROOT / "outputs/strategy/frozen/hist_oos/weekly_path.csv"
)

AUM_SCENARIOS = [10_000_000, 50_000_000, 100_000_000]
PARTICIPATION_LIMITS = [0.01, 0.05, 0.10]
ADV_STRESS_MULTIPLIER = 0.5


def load_data():
    valid_weeks = pd.read_csv(OOS_PATH)["signal_date"]

    if valid_weeks.duplicated().any():
        raise ValueError("Duplicate OOS signal dates")

    valid_weeks = set(valid_weeks)

    liq = pd.read_parquet(
        LIQUIDITY,
        columns=[
            "signal_date", "execution_date", "PERMNO",
            "ADV20", "ADV60", "liquidity_available",
            "valid_trading_days_20",
            "valid_trading_days_60",
            "historical_gics_sector",
        ],
    )

    liq["PERMNO"] = liq["PERMNO"].astype(str)
    liq = liq[liq["signal_date"].isin(valid_weeks)].copy()

    keys = ["signal_date", "PERMNO"]

    if liq.duplicated(keys).any():
        raise ValueError("Duplicate liquidity stock-weeks")

    frames = []

    for name, path in LEDGERS.items():
        if not path.exists():
            raise FileNotFoundError(path)

        ledger = pd.read_csv(path, dtype={"PERMNO": str})

        ledger = ledger[
            ledger["signal_date"].isin(valid_weeks)
        ].copy()

        if ledger.duplicated(keys).any():
            raise ValueError(f"{name}: duplicate trades")

        if not np.isfinite(
            ledger["trade_weight"].to_numpy(dtype=float)
        ).all():
            raise ValueError(f"{name}: invalid trade weights")

        ledger["strategy"] = name
        frames.append(ledger)

    trades = pd.concat(frames, ignore_index=True)

    trades["abs_trade_weight"] = trades["trade_weight"].abs()

    trades = trades[
        trades["abs_trade_weight"] > 1e-15
    ].copy()

    trades = trades.merge(
        liq, on=keys, how="left",
        validate="many_to_one", indicator=True
    )

    trades["liquidity_valid"] = (
        trades["_merge"].eq("both")
        & trades["liquidity_available"].eq(1)
        & np.isfinite(trades["ADV20"])
        & (trades["ADV20"] > 0)
        & (trades["valid_trading_days_20"] >= 20)
    )

    trades = trades.drop(columns="_merge")

    return trades


def coverage_report(trades):
    rows = []

    for name, g in trades.groupby("strategy"):
        valid = g["liquidity_valid"]

        total_notional_weight = g["abs_trade_weight"].sum()

        valid_notional_weight = (
            g.loc[valid, "abs_trade_weight"].sum()
        )

        rows.append({
            "strategy": name,
            "n_trades": len(g),
            "n_valid": int(valid.sum()),
            "n_missing": int((~valid).sum()),
            "trade_coverage": valid.mean(),
            "notional_weight_coverage": (
                valid_notional_weight / total_notional_weight
                if total_notional_weight > 0 else np.nan
            ),
        })

    return pd.DataFrame(rows)


def calculate_scenarios(trades):
    valid = trades[trades["liquidity_valid"]].copy()

    rows = []
    details = []

    for (name, date), g in valid.groupby(
        ["strategy", "signal_date"]
    ):
        for aum in AUM_SCENARIOS:
            for stress_name, multiplier in [
                ("Base", 1.0),
                ("ADV50pct", ADV_STRESS_MULTIPLIER),
            ]:
                participation = (
                    aum * g["abs_trade_weight"]
                    / (g["ADV20"] * multiplier)
                )

                rows.append({
                    "strategy": name,
                    "signal_date": date,
                    "AUM": aum,
                    "scenario": stress_name,
                    "n_valid_trades": len(g),
                    "mean_participation": participation.mean(),
                    "p95_participation": participation.quantile(0.95),
                    "max_participation": participation.max(),
                    "share_above_5pct": (participation > 0.05).mean(),
                    "share_above_10pct": (participation > 0.10).mean(),
                })

                if stress_name == "Base":
                    for idx, p in zip(g.index, participation):
                        details.append({
                            "strategy": name,
                            "signal_date": date,
                            "PERMNO": g.loc[idx, "PERMNO"],
                            "AUM": aum,
                            "participation": p,
                        })

    return pd.DataFrame(rows), pd.DataFrame(details)


def capacity_analysis(trades):
    valid = trades[trades["liquidity_valid"]].copy()

    rows = []

    for name, g in valid.groupby("strategy"):
        for limit in PARTICIPATION_LIMITS:
            capacity = (
                limit * g["ADV20"] / g["abs_trade_weight"]
            )

            bottleneck_idx = capacity.idxmin()

            rows.append({
                "strategy": name,
                "participation_limit": limit,
                "capacity_usd": capacity.min(),
                "bottleneck_signal_date":
                    g.loc[bottleneck_idx, "signal_date"],
                "bottleneck_PERMNO":
                    g.loc[bottleneck_idx, "PERMNO"],
                "bottleneck_ADV20":
                    g.loc[bottleneck_idx, "ADV20"],
                "bottleneck_trade_weight":
                    g.loc[bottleneck_idx, "abs_trade_weight"],
            })

    return pd.DataFrame(rows)


def summarize(scenarios):
    return scenarios.groupby(
        ["strategy", "AUM", "scenario"], as_index=False
    ).agg(
        avg_mean_participation=("mean_participation", "mean"),
        avg_p95_participation=("p95_participation", "mean"),
        worst_participation=("max_participation", "max"),
        avg_share_above_5pct=("share_above_5pct", "mean"),
        avg_share_above_10pct=("share_above_10pct", "mean"),
    )


def make_plot(summary):
    base = summary[
        summary["scenario"] == "Base"
    ]

    fig, ax = plt.subplots(figsize=(10, 6))

    for name, g in base.groupby("strategy"):
        g = g.sort_values("AUM")

        ax.plot(
            g["AUM"] / 1e6,
            g["avg_p95_participation"] * 100,
            marker="o",
            label=name,
        )

    ax.axhline(
        5, linestyle="--", linewidth=1,
        label="5% participation"
    )

    ax.set_title("US OOS: Trade Participation vs AUM")
    ax.set_xlabel("AUM (USD millions)")
    ax.set_ylabel("Average Weekly P95 Participation (%)")
    ax.grid(alpha=0.3)
    ax.legend()

    fig.tight_layout()
    fig.savefig(
        OUT / "capacity_participation.png", dpi=200
    )
    plt.close(fig)


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    trades = load_data()
    coverage = coverage_report(trades)

    scenarios, details = calculate_scenarios(trades)
    capacity = capacity_analysis(trades)
    summary = summarize(scenarios)

    coverage.to_csv(
        OUT / "liquidity_coverage.csv", index=False
    )
    scenarios.to_csv(
        OUT / "liquidity_weekly_scenarios.csv", index=False
    )
    details.to_csv(
        OUT / "liquidity_trade_participation.csv", index=False
    )
    capacity.to_csv(
        OUT / "capacity_limits.csv", index=False
    )
    summary.to_csv(
        OUT / "liquidity_capacity_summary.csv", index=False
    )

    make_plot(summary)

    print("\n=== Liquidity Coverage ===")
    print(coverage.to_string(index=False))

    print("\n=== Capacity Limits ===")
    print(
        capacity.to_string(
            index=False,
            float_format=lambda x: f"{x:.5f}"
        )
    )

    print("\n=== Liquidity Capacity Summary ===")
    print(
        summary.to_string(
            index=False,
            float_format=lambda x: f"{x:.5f}"
        )
    )

    print("\nResults saved to:", OUT)


if __name__ == "__main__":
    main()
