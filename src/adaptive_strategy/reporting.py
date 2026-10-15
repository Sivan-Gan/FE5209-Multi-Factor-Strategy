
"""Adaptive Strategy V1 - Performance & Risk Reporting.

Reports execution-aligned weekly historical performance.
Uses frozen model selection, historical benchmarks and FF RF.

The market benchmark is CRSP INDNO 1000500, not an
independently downloaded official S&P 500 TR series.
"""

from pathlib import Path
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import numpy as np
import pandas as pd

from src.strategy import metrics as mt


START = "2021-01-01"
END = "2025-12-31"

BENCHMARK_LABEL = "CRSP S&P 500 Universe VW"

FIXED_BENCHMARKS = [
    "4F_Rolling_IV",
    "4F_Rolling_MV",
]


def read_inputs(
    pipeline_file: Path,
    frozen_file: Path,
    fixed_file: Path,
    market_file: Path,
    rf_file: Path,
):
    with open(frozen_file, encoding="utf-8") as f:
        frozen = json.load(f)

    selected = frozen["selected_strategy"]

    paths = pd.read_csv(pipeline_file)
    fixed = pd.read_csv(fixed_file)
    market = pd.read_parquet(market_file)
    rf = pd.read_parquet(rf_file)

    paths = paths[paths["strategy"].eq(selected)].copy()
    fixed = fixed[
        fixed["strategy"].isin(FIXED_BENCHMARKS)
    ].copy()

    required_path = {
        "signal_date", "strategy", "net_return",
    }

    for name, df in [
        ("adaptive", paths),
        ("fixed", fixed),
    ]:
        if not required_path.issubset(df.columns):
            raise ValueError(f"Missing {name} path columns")

    market_required = {
        "signal_date",
        "benchmark_total_return",
        "path_complete",
    }

    if not market_required.issubset(market.columns):
        raise ValueError("Missing market columns")

    if not {
        "signal_date", "risk_free_return"
    }.issubset(rf.columns):
        raise ValueError("Missing RF columns")

    strategy_data = pd.concat(
        [paths, fixed],
        ignore_index=True,
    )

    strategy_data["signal_date"] = pd.to_datetime(
        strategy_data["signal_date"]
    )

    strategy_data = strategy_data[
        strategy_data["signal_date"].between(START, END)
    ].copy()

    strategy_data["net_return"] = pd.to_numeric(
        strategy_data["net_return"],
        errors="coerce",
    )

    market["signal_date"] = pd.to_datetime(
        market["signal_date"]
    )

    market = market[
        market["signal_date"].between(START, END)
        & market["path_complete"].eq(1)
    ].copy()

    market["benchmark_total_return"] = pd.to_numeric(
        market["benchmark_total_return"],
        errors="coerce",
    )

    rf["signal_date"] = pd.to_datetime(
        rf["signal_date"]
    )

    rf["risk_free_return"] = pd.to_numeric(
        rf["risk_free_return"],
        errors="coerce",
    )

    if strategy_data.duplicated(
        ["strategy", "signal_date"]
    ).any():
        raise ValueError("Duplicate strategy dates")

    if market["signal_date"].duplicated().any():
        raise ValueError("Duplicate market dates")

    if rf["signal_date"].duplicated().any():
        raise ValueError("Duplicate RF dates")

    if strategy_data.empty:
        raise ValueError("No strategy data")

    if market.empty:
        raise ValueError("No market data")

    if rf.empty:
        raise ValueError("No risk-free data")

    if not np.isfinite(
        strategy_data["net_return"]
    ).all():
        raise ValueError("Invalid strategy returns")

    if not np.isfinite(
        market["benchmark_total_return"]
    ).all():
        raise ValueError("Invalid market returns")

    # All strategy and market returns must use
    # exactly the same signal-date calendar.
    expected = pd.DatetimeIndex(
        sorted(
            strategy_data.loc[
                strategy_data["strategy"].eq(selected),
                "signal_date",
            ].unique()
        )
    )

    if len(expected) != 259:
        raise ValueError(
            f"Expected 259 validation weeks, got {len(expected)}"
        )

    expected_set = set(expected)

    for name, group in strategy_data.groupby("strategy"):
        if set(group["signal_date"]) != expected_set:
            raise ValueError(
                f"Strategy calendar mismatch: {name}"
            )

    if set(market["signal_date"]) != expected_set:
        raise ValueError("Market calendar mismatch")

    if not expected_set.issubset(set(rf["signal_date"])):
        raise ValueError("RF calendar mismatch")

    market_series = (
        market.set_index("signal_date")[
            "benchmark_total_return"
        ].sort_index()
    )

    wide = strategy_data.pivot(
        index="signal_date",
        columns="strategy",
        values="net_return",
    ).sort_index()

    wide[BENCHMARK_LABEL] = market_series.reindex(
        wide.index
    )

    if wide.isna().any().any():
        raise ValueError("Missing aligned strategy or market returns")

    rf_series = (
        rf.set_index("signal_date")["risk_free_return"]
        .reindex(wide.index)
    )

    if not np.isfinite(rf_series.to_numpy()).all():
        invalid_dates = rf_series.index[
            ~np.isfinite(rf_series.to_numpy())
        ]
        raise ValueError(
            f"Invalid RF on strategy dates: {invalid_dates.tolist()}"
        )

    return wide, rf_series, selected, frozen


def metrics_for_period(
    returns: pd.Series,
    rf: pd.Series,
):
    perf = mt.performance(returns, rf)

    # Include initial NAV=1 to capture first-week drawdown.
    nav = np.r_[1.0, np.cumprod(
        1.0 + returns.to_numpy(dtype=float)
    )]

    peak = np.maximum.accumulate(nav)
    max_dd = float(1.0 - np.min(nav / peak))

    perf["max_drawdown"] = max_dd
    perf["calmar"] = (
        perf["ann_return"] / max_dd
        if max_dd > 0 else np.nan
    )

    perf["cumulative_return"] = float(nav[-1] - 1.0)

    return perf


def performance_tables(wide, rf):
    overall_rows = []
    annual_rows = []

    for strategy in wide.columns:
        r = wide[strategy]

        perf = metrics_for_period(r, rf.reindex(r.index))

        overall_rows.append({
            "strategy": strategy,
            "n_weeks": len(r),
            "cumulative_return": perf["cumulative_return"],
            "cagr": perf["ann_return"],
            "volatility": perf["ann_vol"],
            "sharpe": perf["sharpe"],
            "sortino": perf["sortino"],
            "max_drawdown": perf["max_drawdown"],
            "calmar": perf["calmar"],
        })

        for year, group in r.groupby(r.index.year):
            p = metrics_for_period(
                group,
                rf.reindex(group.index),
            )

            annual_rows.append({
                "year": int(year),
                "strategy": strategy,
                "n_weeks": len(group),
                "return": p["cumulative_return"],
                "volatility": p["ann_vol"],
                "sharpe": p["sharpe"],
                "max_drawdown": p["max_drawdown"],
                "calmar": p["calmar"],
                "sortino": p["sortino"],
            })

    return (
        pd.DataFrame(overall_rows),
        pd.DataFrame(annual_rows),
    )


def risk_attribution(wide, rf):
    market = wide[BENCHMARK_LABEL]
    rows = []

    for strategy in wide.columns:
        if strategy == BENCHMARK_LABEL:
            continue

        r = wide[strategy]
        active = r - market

        # Excess-return regression:
        # (Rp - Rf) = alpha + beta * (Rm - Rf) + error
        y = (r - rf.reindex(r.index)).to_numpy()
        x = (
            market - rf.reindex(market.index)
        ).to_numpy()

        beta = float(
            np.cov(y, x, ddof=1)[0, 1]
            / np.var(x, ddof=1)
        )

        alpha_weekly = float(
            np.mean(y) - beta * np.mean(x)
        )

        tracking_error = float(
            active.std(ddof=1)
            * np.sqrt(mt.WEEKS_PER_YEAR)
        )

        ir = (
            float(
                active.mean()
                * mt.WEEKS_PER_YEAR
                / tracking_error
            )
            if tracking_error > 0 else np.nan
        )

        rows.append({
            "strategy": strategy,
            "beta": beta,
            "alpha_annualized_arithmetic": (
                alpha_weekly * mt.WEEKS_PER_YEAR
            ),
            "tracking_error": tracking_error,
            "information_ratio": ir,
            "active_return_annualized_arithmetic": (
                float(active.mean() * mt.WEEKS_PER_YEAR)
            ),
        })

    return pd.DataFrame(rows)


def annual_relative_attribution(wide, rf, selected):
    rows = []

    for year in [2023, 2024]:
        subset = wide[wide.index.year == year]

        r = subset[selected]
        market = subset[BENCHMARK_LABEL]
        risk_free = rf.reindex(subset.index)

        x = (market - risk_free).to_numpy()
        y = (r - risk_free).to_numpy()

        beta = float(
            np.cov(y, x, ddof=1)[0, 1]
            / np.var(x, ddof=1)
        )

        alpha_weekly = float(
            np.mean(y) - beta * np.mean(x)
        )

        strategy_return = float(
            np.prod(1.0 + r) - 1.0
        )

        market_return = float(
            np.prod(1.0 + market) - 1.0
        )

        rows.append({
            "year": year,
            "strategy": selected,
            "strategy_return": strategy_return,
            "market_return": market_return,
            "active_compounded_return_pp": (
                (strategy_return - market_return) * 100
            ),
            "beta": beta,
            "alpha_annualized_arithmetic": (
                alpha_weekly * mt.WEEKS_PER_YEAR
            ),
        })

    return pd.DataFrame(rows)


def create_figures(wide, annual, overall, selected, out):
    plt.rcParams.update({
        "figure.figsize": (11, 6),
        "font.size": 10,
        "axes.grid": True,
        "grid.alpha": 0.25,
    })

    # 1. Cumulative NAV
    nav = (1.0 + wide).cumprod()
    nav = pd.concat([
        pd.DataFrame(
            [np.ones(len(wide.columns))],
            index=[wide.index.min() - pd.Timedelta(days=7)],
            columns=wide.columns,
        ),
        nav,
    ])

    ax = nav.plot(linewidth=2)
    ax.set_title("Validation Period: Cumulative NAV (2021-2025)")
    ax.set_ylabel("Growth of $1")
    ax.set_xlabel("Signal Date")
    plt.tight_layout()
    plt.savefig(out / "cumulative_nav.png", dpi=200)
    plt.close()

    # 2. Annual returns
    pivot = annual.pivot(
        index="year",
        columns="strategy",
        values="return",
    )

    ax = (pivot * 100).plot.bar(width=0.8)
    ax.set_title("Annual Returns: Execution-Aligned Weeks")
    ax.set_ylabel("Return (%)")
    ax.set_xlabel("Signal Year")
    ax.legend(fontsize=8)
    plt.xticks(rotation=0)
    plt.tight_layout()
    plt.savefig(out / "annual_returns.png", dpi=200)
    plt.close()

    # 3. Drawdown
    peak = nav.cummax()
    dd = nav / peak - 1.0

    ax = (dd * 100).plot(linewidth=1.8)
    ax.set_title("Validation Period Drawdown")
    ax.set_ylabel("Drawdown (%)")
    ax.set_xlabel("Signal Date")
    plt.tight_layout()
    plt.savefig(out / "drawdown.png", dpi=200)
    plt.close()

    # 4. Risk-return scatter
    fig, ax = plt.subplots()

    for row in overall.itertuples(index=False):
        ax.scatter(
            row.volatility * 100,
            row.cagr * 100,
            s=90,
        )
        ax.annotate(
            row.strategy,
            (row.volatility * 100, row.cagr * 100),
            xytext=(5, 5),
            textcoords="offset points",
            fontsize=8,
        )

    ax.set_title("Annualized Risk vs Return (2021-2025)")
    ax.set_xlabel("Annualized Volatility (%)")
    ax.set_ylabel("CAGR (%)")
    plt.tight_layout()
    plt.savefig(out / "risk_return.png", dpi=200)
    plt.close()


def run_reporting(
    pipeline_file: Path,
    frozen_file: Path,
    fixed_file: Path,
    market_file: Path,
    rf_file: Path,
    output_dir: Path,
):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    wide, rf, selected, frozen = read_inputs(
        pipeline_file,
        frozen_file,
        fixed_file,
        market_file,
        rf_file,
    )

    overall, annual = performance_tables(wide, rf)
    attribution = risk_attribution(wide, rf)
    relative = annual_relative_attribution(
        wide, rf, selected
    )

    overall.to_csv(
        output_dir / "overall_performance.csv",
        index=False,
    )

    annual.to_csv(
        output_dir / "annual_performance.csv",
        index=False,
    )

    annual.pivot(
        index="year",
        columns="strategy",
        values="return",
    ).to_csv(
        output_dir / "annual_returns_wide.csv"
    )

    attribution.to_csv(
        output_dir / "risk_attribution.csv",
        index=False,
    )

    relative.to_csv(
        output_dir / "relative_performance_2023_2024.csv",
        index=False,
    )

    (1.0 + wide).cumprod().to_csv(
        output_dir / "weekly_nav.csv"
    )

    create_figures(
        wide, annual, overall, selected, output_dir
    )

    print("\n=== Selected Model ===")
    print(selected)

    print("\n=== Validation Performance ===")
    print(
        overall.round(5).to_string(index=False)
    )

    print("\n=== 2023-2024 Relative Performance ===")
    print(
        relative.round(5).to_string(index=False)
    )

    print("\n=== Risk Attribution ===")
    print(
        attribution.round(5).to_string(index=False)
    )

    print("\nReporting outputs:", output_dir)

    return overall, annual, attribution
