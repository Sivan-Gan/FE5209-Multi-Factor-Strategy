
"""Controlled benchmark reporting for Adaptive Top-3 IV.

Both strategies start from cash in 2021.

Outputs:
- overall performance
- annual performance
- cumulative NAV
- drawdown
- annual return comparison
- weekly active returns
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import numpy as np
import pandas as pd

from src.strategy import metrics as mt


START = "2021-01-01"
END = "2025-12-31"
EXPECTED_WEEKS = 259

ADAPTIVE = "Adaptive Top-3 IV"
FIXED = "Fixed Top-3 IV"


def load_paths(
    adaptive_file: Path,
    fixed_file: Path,
    rf_file: Path,
):
    adaptive = pd.read_csv(adaptive_file)
    fixed = pd.read_csv(fixed_file)

    rf = pd.read_parquet(
        rf_file,
        columns=["signal_date", "risk_free_return"],
    )

    for df in (adaptive, fixed, rf):
        df["signal_date"] = pd.to_datetime(
            df["signal_date"]
        )

    adaptive = adaptive[
        adaptive["signal_date"].between(START, END)
    ].copy()

    fixed = fixed[
        fixed["signal_date"].between(START, END)
    ].copy()

    for name, df in [
        (ADAPTIVE, adaptive),
        (FIXED, fixed),
    ]:
        if len(df) != EXPECTED_WEEKS:
            raise ValueError(
                f"{name}: expected {EXPECTED_WEEKS} weeks"
            )

        if df["signal_date"].duplicated().any():
            raise ValueError(
                f"Duplicate dates: {name}"
            )

        df["net_return"] = pd.to_numeric(
            df["net_return"], errors="coerce"
        )

        if not np.isfinite(df["net_return"]).all():
            raise ValueError(
                f"Invalid returns: {name}"
            )

    if set(adaptive["signal_date"]) != set(fixed["signal_date"]):
        raise ValueError("Strategy calendars differ")

    if rf["signal_date"].duplicated().any():
        raise ValueError("Duplicate RF dates")

    rf["risk_free_return"] = pd.to_numeric(
        rf["risk_free_return"], errors="coerce"
    )

    wide = pd.DataFrame({
        ADAPTIVE: adaptive.set_index(
            "signal_date"
        )["net_return"],
        FIXED: fixed.set_index(
            "signal_date"
        )["net_return"],
    }).sort_index()

    risk_free = (
        rf.set_index("signal_date")["risk_free_return"]
        .reindex(wide.index)
    )

    if not np.isfinite(risk_free.to_numpy()).all():
        raise ValueError("Invalid aligned RF")

    # Both strategies should be identical during 2021.
    first_year = wide[wide.index.year == 2021]

    if len(first_year) != 52:
        raise ValueError("Unexpected 2021 week count")

    error = (
        first_year[ADAPTIVE] - first_year[FIXED]
    ).abs().max()

    if error > 1e-10:
        raise AssertionError(
            f"2021 reproduction mismatch: {error}"
        )

    return wide, risk_free


def calculate_metrics(r, rf):
    perf = mt.performance(r, rf)

    nav = np.r_[
        1.0,
        np.cumprod(1.0 + r.to_numpy(dtype=float)),
    ]

    peak = np.maximum.accumulate(nav)
    max_dd = float(1.0 - np.min(nav / peak))

    return {
        "cumulative_return": float(nav[-1] - 1.0),
        "cagr": float(perf["ann_return"]),
        "volatility": float(perf["ann_vol"]),
        "sharpe": float(perf["sharpe"]),
        "sortino": float(perf["sortino"]),
        "max_drawdown": max_dd,
        "calmar": (
            float(perf["ann_return"] / max_dd)
            if max_dd > 0 else np.nan
        ),
    }


def build_tables(wide, rf):
    overall_rows = []
    annual_rows = []

    for strategy in wide.columns:
        r = wide[strategy]

        perf = calculate_metrics(
            r, rf.reindex(r.index)
        )

        overall_rows.append({
            "strategy": strategy,
            "n_weeks": len(r),
            **perf,
        })

        for year, group in r.groupby(r.index.year):
            p = calculate_metrics(
                group,
                rf.reindex(group.index),
            )

            annual_rows.append({
                "year": int(year),
                "strategy": strategy,
                "n_weeks": len(group),
                **p,
            })

    return (
        pd.DataFrame(overall_rows),
        pd.DataFrame(annual_rows),
    )


def create_charts(wide, annual, output_dir):
    nav = (1.0 + wide).cumprod()

    # Include initial NAV=1.
    initial = pd.DataFrame(
        [{ADAPTIVE: 1.0, FIXED: 1.0}],
        index=[wide.index.min() - pd.Timedelta(days=7)],
    )

    nav = pd.concat([initial, nav])

    plt.figure(figsize=(11, 6))
    for col in nav.columns:
        plt.plot(nav.index, nav[col], label=col, linewidth=2)

    plt.title("Adaptive vs Fixed Top-3 IV: 2021-2025")
    plt.ylabel("Cumulative NAV")
    plt.xlabel("Signal Date")
    plt.legend()
    plt.grid(alpha=0.25)
    plt.tight_layout()
    plt.savefig(
        output_dir / "adaptive_vs_fixed_nav.png",
        dpi=200,
    )
    plt.close()

    drawdown = nav / nav.cummax() - 1.0

    plt.figure(figsize=(11, 6))
    for col in drawdown.columns:
        plt.plot(
            drawdown.index,
            drawdown[col] * 100,
            label=col,
            linewidth=2,
        )

    plt.title("Adaptive vs Fixed Top-3 IV: Drawdown")
    plt.ylabel("Drawdown (%)")
    plt.xlabel("Signal Date")
    plt.legend()
    plt.grid(alpha=0.25)
    plt.tight_layout()
    plt.savefig(
        output_dir / "adaptive_vs_fixed_drawdown.png",
        dpi=200,
    )
    plt.close()

    annual_wide = annual.pivot(
        index="year",
        columns="strategy",
        values="cumulative_return",
    )

    ax = (annual_wide * 100).plot.bar(
        figsize=(10, 6),
        width=0.7,
    )

    ax.set_title("Annual Returns: Adaptive vs Fixed Top-3")
    ax.set_ylabel("Annual Return (%)")
    ax.set_xlabel("Year")
    plt.xticks(rotation=0)
    plt.tight_layout()
    plt.savefig(
        output_dir / "adaptive_vs_fixed_annual.png",
        dpi=200,
    )
    plt.close()

    return nav, drawdown


def run_benchmark_reporting(
    adaptive_file: Path,
    fixed_file: Path,
    rf_file: Path,
    output_dir: Path,
):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    wide, rf = load_paths(
        adaptive_file,
        fixed_file,
        rf_file,
    )

    overall, annual = build_tables(wide, rf)

    nav, drawdown = create_charts(
        wide, annual, output_dir
    )

    active = wide[ADAPTIVE] - wide[FIXED]

    tracking_error = float(
        active.std(ddof=1)
        * np.sqrt(mt.WEEKS_PER_YEAR)
    )

    information_ratio = (
        float(
            active.mean()
            * mt.WEEKS_PER_YEAR
            / tracking_error
        )
        if tracking_error > 0 else np.nan
    )

    relative = pd.DataFrame([{
        "adaptive": ADAPTIVE,
        "benchmark": FIXED,
        "tracking_error": tracking_error,
        "information_ratio": information_ratio,
        "annualized_active_return_arithmetic": float(
            active.mean() * mt.WEEKS_PER_YEAR
        ),
    }])

    overall.to_csv(
        output_dir / "overall_comparison.csv",
        index=False,
    )

    annual.to_csv(
        output_dir / "annual_comparison.csv",
        index=False,
    )

    wide.to_csv(
        output_dir / "weekly_returns.csv"
    )

    nav.to_csv(
        output_dir / "cumulative_nav.csv"
    )

    drawdown.to_csv(
        output_dir / "drawdown.csv"
    )

    relative.to_csv(
        output_dir / "relative_risk.csv",
        index=False,
    )

    print("\n=== Controlled Benchmark Performance ===")
    print(overall.round(6).to_string(index=False))

    print("\n=== Annual Performance ===")
    print(
        annual[
            [
                "year",
                "strategy",
                "cumulative_return",
                "sharpe",
                "max_drawdown",
            ]
        ].round(6).to_string(index=False)
    )

    print("\n=== Relative Risk ===")
    print(relative.round(6).to_string(index=False))

    print("\nResults saved to:", output_dir)

    return overall, annual, relative
