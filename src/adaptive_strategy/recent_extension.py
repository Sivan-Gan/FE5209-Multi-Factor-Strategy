
"""2026 Recent Extension Reporting.

Uses completed execution weeks only.
2026 is reported separately from historical validation.
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import numpy as np
import pandas as pd


WEEKS_PER_YEAR = 52

STRATEGIES = [
    "2026_Top3_EW",
    "2026_Top3_IV",
    "2026_Top3_MV",
]

MARKET = "S&P500_Compustat_TR"


def calculate_metrics(r):
    r = np.asarray(r, dtype=float)

    if len(r) != 39:
        raise ValueError("Expected 39 completed weeks")

    if not np.isfinite(r).all() or (r <= -1).any():
        raise ValueError("Invalid weekly returns")

    nav = np.r_[1.0, np.cumprod(1.0 + r)]
    peak = np.maximum.accumulate(nav)

    cumulative = float(nav[-1] - 1.0)
    annualized = float(
        nav[-1] ** (WEEKS_PER_YEAR / len(r)) - 1.0
    )
    volatility = float(
        np.std(r, ddof=1) * np.sqrt(WEEKS_PER_YEAR)
    )
    sharpe = float(
        np.mean(r) / np.std(r, ddof=1)
        * np.sqrt(WEEKS_PER_YEAR)
    )
    max_dd = float(
        1.0 - np.min(nav / peak)
    )

    return {
        "n_weeks": len(r),
        "cumulative_return": cumulative,
        "annualized_return_52w": annualized,
        "volatility_52w": volatility,
        "sharpe_zero_rf": sharpe,
        "max_drawdown": max_dd,
    }


def run_recent_extension(
    strategy_file: Path,
    benchmark_file: Path,
    reference_file: Path,
    output_dir: Path,
):
    strategy = pd.read_csv(strategy_file)
    benchmark = pd.read_csv(benchmark_file)
    reference = pd.read_csv(reference_file)

    for frame in (strategy, benchmark):
        frame["signal_date"] = pd.to_datetime(
            frame["signal_date"]
        )

    if strategy.duplicated(
        ["signal_date", "strategy"]
    ).any():
        raise ValueError("Duplicate strategy/date rows")

    if benchmark["signal_date"].duplicated().any():
        raise ValueError("Duplicate benchmark dates")

    if set(strategy["strategy"]) != set(STRATEGIES):
        raise ValueError("Unexpected strategy set")

    wide = strategy.pivot(
        index="signal_date",
        columns="strategy",
        values="net_return",
    ).sort_index()

    market = benchmark.set_index(
        "signal_date"
    )["benchmark_total_return"]

    if set(wide.index) != set(market.index):
        raise ValueError("Benchmark calendar mismatch")

    wide[MARKET] = market.reindex(wide.index)

    if len(wide) != 39:
        raise ValueError("Expected 39 completed weeks")

    if not np.isfinite(wide.to_numpy(dtype=float)).all():
        raise ValueError("Invalid aligned returns")

    rows = []

    for name in STRATEGIES + [MARKET]:
        metrics = calculate_metrics(wide[name])

        rows.append({
            "strategy": name,
            **metrics,
        })

    summary = pd.DataFrame(rows)

    # Audit against previously calculated strategy results.
    audit = summary[
        summary["strategy"].isin(STRATEGIES)
    ].merge(
        reference[
            ["strategy", "cumulative_return"]
        ],
        on="strategy",
        suffixes=("_new", "_reference"),
        validate="one_to_one",
    )

    if len(audit) != len(STRATEGIES):
        raise ValueError("Incomplete reference audit")

    audit["abs_error"] = (
        audit["cumulative_return_new"]
        - audit["cumulative_return_reference"]
    ).abs()

    if (audit["abs_error"] > 1e-10).any():
        raise AssertionError("2026 return reproduction failed")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    nav = (1.0 + wide).cumprod()

    initial = pd.DataFrame(
        [np.ones(len(wide.columns))],
        columns=wide.columns,
        index=[wide.index.min() - pd.Timedelta(days=7)],
    )

    nav = pd.concat([initial, nav])

    drawdown = nav / nav.cummax() - 1.0

    summary.to_csv(
        output_dir / "performance_comparison.csv",
        index=False,
    )

    audit.to_csv(
        output_dir / "reproduction_audit.csv",
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

    for data, title, filename, ylabel in [
        (
            nav,
            "2026 Recent Extension: Cumulative NAV",
            "cumulative_nav.png",
            "Growth of $1",
        ),
        (
            drawdown * 100,
            "2026 Recent Extension: Drawdown",
            "drawdown.png",
            "Drawdown (%)",
        ),
    ]:
        ax = data.plot(
            figsize=(11, 6),
            linewidth=2,
        )

        ax.set_title(title)
        ax.set_xlabel("Signal Date")
        ax.set_ylabel(ylabel)

        plt.grid(alpha=0.25)
        plt.tight_layout()

        plt.savefig(
            output_dir / filename,
            dpi=200,
        )
        plt.close()

    diagnostics = (
        strategy.groupby("strategy")
        .agg(
            mean_recovery_weight=(
                "recovery_weight", "mean"
            ),
            mean_recovery_contribution=(
                "recovery_contribution", "mean"
            ),
            mean_unpriced_weight=(
                "unpriced_weight", "mean"
            ),
            max_unpriced_weight=(
                "unpriced_weight", "max"
            ),
        )
        .reset_index()
    )

    diagnostics.to_csv(
        output_dir / "data_quality_diagnostics.csv",
        index=False,
    )

    print("\n=== 2026 Recent Extension ===")
    print(summary.round(6).to_string(index=False))

    print("\n=== Reproduction Audit ===")
    print(
        audit[
            ["strategy", "abs_error"]
        ].to_string(index=False)
    )

    print("\n=== Data Quality Diagnostics ===")
    print(diagnostics.round(6).to_string(index=False))

    print("\nResults saved to:", output_dir)

    return summary, audit, diagnostics
