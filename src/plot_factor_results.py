#!/usr/bin/env python3
"""Generate reproducible figures from frozen FE5209 single-factor results.

Examples:
    python3 src/plot_factor_results.py
    python3 src/plot_factor_results.py --module performance
    python3 src/plot_factor_results.py --sample PRICE_ELIGIBLE_TRUE

This module reads only the formal CSV outputs. It never reruns factor research,
rewrites result files, or reads the source panel.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
from matplotlib.ticker import PercentFormatter
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = PROJECT_ROOT / "results"
DEFAULT_FIGURES_DIR = PROJECT_ROOT / "figures"
PERIOD = "2011-2015"
FACTORS = ["Quality", "Momentum", "LowRisk", "Investment", "Size", "Value"]
SAMPLES = ["PRICE_ELIGIBLE_TRUE", "ALL_PAIRED"]
SAMPLE_LABELS = {
    "PRICE_ELIGIBLE_TRUE": "Price-eligible (primary)",
    "ALL_PAIRED": "All paired (control)",
}
SAMPLE_LINESTYLES = {"PRICE_ELIGIBLE_TRUE": "-", "ALL_PAIRED": "--"}
SAMPLE_HATCHES = {"PRICE_ELIGIBLE_TRUE": "", "ALL_PAIRED": "//"}
FACTOR_COLORS = {
    "Quality": "#1f77b4",
    "Momentum": "#ff7f0e",
    "LowRisk": "#2ca02c",
    "Investment": "#9467bd",
    "Size": "#d62728",
    "Value": "#8c564b",
}
COMPOSITE_LABELS = {
    "UNIVERSE_EW": "Universe EW",
    "ADD_1": "Momentum",
    "ADD_2": "Momentum + Quality",
    "ADD_3": "+ LowRisk",
    "ADD_4": "+ Investment",
    "ADD_5": "+ Size",
    "ADD_6": "+ Value",
}
COMPOSITE_COLORS = {
    "UNIVERSE_EW": "#4d4d4d",
    "ADD_1": FACTOR_COLORS["Momentum"],
    "ADD_2": FACTOR_COLORS["Quality"],
    "ADD_3": FACTOR_COLORS["LowRisk"],
    "ADD_4": FACTOR_COLORS["Investment"],
    "ADD_5": FACTOR_COLORS["Size"],
    "ADD_6": FACTOR_COLORS["Value"],
}


@dataclass
class FigureRecord:
    figure_id: str
    module: str
    filename_png: str
    filename_pdf: str
    source_csv: str
    source_columns: str
    sample: str
    metric_definition: str
    period: str
    status: str


def configure_style() -> None:
    plt.rcParams.update({
        "figure.dpi": 120,
        "savefig.dpi": 300,
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.labelsize": 10,
        "legend.fontsize": 9,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    })


def sample_key(samples: list[str]) -> str:
    return "both" if len(samples) == 2 else samples[0].lower()


def sample_title(samples: list[str]) -> str:
    return "Both samples" if len(samples) == 2 else SAMPLE_LABELS[samples[0]]


def add_period_note(fig: plt.Figure, note: str = "Diagnostic research output; gross returns; no transaction costs.") -> None:
    fig.set_constrained_layout_pads(w_pad=0.12, h_pad=0.34, wspace=0.08, hspace=0.10)
    fig.text(0.01, 0.012, f"{PERIOD} | {note}", fontsize=8, color="#444444")


def validate_dates(df: pd.DataFrame, path: Path) -> pd.DataFrame:
    if "signal_date" in df.columns:
        df = df.copy()
        df["signal_date"] = pd.to_datetime(df["signal_date"], errors="raise")
        valid = df["signal_date"].between(pd.Timestamp("2011-01-01"), pd.Timestamp("2015-12-31"))
        if not valid.all():
            raise ValueError(f"Out-of-period signal_date in {path}")
    if "year" in df.columns:
        years = pd.to_numeric(df["year"], errors="raise")
        if not years.between(2011, 2015).all():
            raise ValueError(f"Out-of-period year in {path}")
    return df


def read_result(relative_path: str) -> pd.DataFrame:
    path = RESULTS_DIR / relative_path
    if not path.is_file():
        raise FileNotFoundError(f"Missing formal result file: {path}")
    return validate_dates(pd.read_csv(path), path)


def source_path(*paths: str) -> str:
    return "; ".join(paths)


def save_figure(
    fig: plt.Figure,
    records: list[FigureRecord],
    output_root: Path,
    module: str,
    figure_id: str,
    short_name: str,
    samples: list[str],
    sources: str,
    columns: str,
    definition: str,
) -> None:
    target_dir = output_root / module
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{figure_id.lower()}_{short_name}_{sample_key(samples)}"
    png = target_dir / f"{stem}.png"
    pdf = target_dir / f"{stem}.pdf"
    fig.savefig(png, dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(pdf, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    records.append(FigureRecord(
        figure_id=figure_id,
        module=module,
        filename_png=str(png.relative_to(output_root)),
        filename_pdf=str(pdf.relative_to(output_root)),
        source_csv=sources,
        source_columns=columns,
        sample="; ".join(samples),
        metric_definition=definition,
        period=PERIOD,
        status="GENERATED",
    ))


def zero_line(ax: plt.Axes) -> None:
    ax.axhline(0, color="#333333", linewidth=0.8, zorder=1)


def factor_bar(
    ax: plt.Axes,
    data: dict[str, pd.Series],
    ylabel: str,
    title: str,
    percent: bool = False,
    sort_factors: bool = False,
) -> None:
    factors = sorted(FACTORS, key=lambda factor: float(np.nanmean([series.get(factor, np.nan) for series in data.values()]))) if sort_factors else FACTORS
    x = np.arange(len(factors))
    width = 0.36 if len(data) == 2 else 0.58
    offsets = np.linspace(-(len(data) - 1) * width / 2, (len(data) - 1) * width / 2, len(data))
    for offset, (sample, series) in zip(offsets, data.items()):
        values = series.reindex(factors).to_numpy(dtype=float)
        if percent:
            values *= 100
        bars = ax.bar(x + offset, values, width=width, label=SAMPLE_LABELS[sample],
                      edgecolor="#333333", linewidth=0.4, alpha=0.9,
                      hatch=SAMPLE_HATCHES[sample])
        for bar, factor in zip(bars, factors):
            bar.set_facecolor(FACTOR_COLORS[factor])
    ax.set_xticks(x, factors, rotation=30, ha="right")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    zero_line(ax)
    ax.legend(frameon=False)


def annotate_heatmap(ax: plt.Axes, matrix: pd.DataFrame, fmt: str = ".3f") -> None:
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            value = matrix.iloc[i, j]
            if pd.notna(value):
                color = "white" if abs(value) > max(abs(matrix.min().min()), abs(matrix.max().max())) * 0.55 else "black"
                ax.text(j, i, format(value, fmt), ha="center", va="center", fontsize=8, color=color)


def heatmap(
    ax: plt.Axes,
    matrix: pd.DataFrame,
    title: str,
    label: str,
    vmin: float | None = None,
    vmax: float | None = None,
    center: float | None = None,
    fmt: str = ".3f",
) -> None:
    if center is not None:
        lim = max(abs(np.nanmin(matrix.to_numpy(dtype=float))), abs(np.nanmax(matrix.to_numpy(dtype=float))), 1e-12)
        norm = TwoSlopeNorm(vmin=-lim if vmin is None else vmin, vcenter=center, vmax=lim if vmax is None else vmax)
        image = ax.imshow(matrix, cmap="coolwarm", norm=norm, aspect="auto")
    else:
        image = ax.imshow(matrix, cmap="viridis", vmin=vmin, vmax=vmax, aspect="auto")
    ax.set_xticks(range(matrix.shape[1]), matrix.columns)
    ax.set_yticks(range(matrix.shape[0]), matrix.index)
    ax.set_title(title)
    annotate_heatmap(ax, matrix, fmt=fmt)
    cbar = ax.figure.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label(label)


def performance_figures(samples: list[str], output_root: Path, records: list[FigureRecord]) -> None:
    summaries = {
        sample: read_result(f"performance/{sample}_factor_performance_summary.csv").set_index("factor")
        for sample in samples
    }
    weekly = {
        sample: read_result(f"performance/{sample}_factor_weekly_metrics.csv")
        for sample in samples
    }
    quintiles = {
        sample: read_result(f"performance/{sample}_factor_quintile_return_summary.csv")
        for sample in samples
    }
    coverage = {
        sample: read_result(f"performance/{sample}_factor_coverage.csv").set_index("factor")
        for sample in samples
    }

    fig, ax = plt.subplots(figsize=(9, 5.3), constrained_layout=True)
    factor_bar(ax, {s: d["mean_rank_ic"] for s, d in summaries.items()}, "Mean Rank IC", f"P01 - Mean Rank IC ({PERIOD})", sort_factors=True)
    add_period_note(fig)
    save_figure(fig, records, output_root, "performance", "P01", "mean_rank_ic", samples,
                source_path(*[f"results/performance/{s}_factor_performance_summary.csv" for s in samples]),
                "factor; mean_rank_ic", "Mean weekly Spearman Rank IC across evaluated weeks.")

    fig, ax = plt.subplots(figsize=(9, 5.3), constrained_layout=True)
    factor_bar(ax, {s: d["rank_ic_ir"] for s, d in summaries.items()}, "ICIR (not annualized)", f"P02 - Rank ICIR ({PERIOD})")
    add_period_note(fig, "ICIR = mean weekly Rank IC / weekly Rank IC standard deviation; not annualized.")
    save_figure(fig, records, output_root, "performance", "P02", "rank_icir", samples,
                source_path(*[f"results/performance/{s}_factor_performance_summary.csv" for s in samples]),
                "factor; rank_ic_ir", "Original non-annualized ICIR from factor performance summary.")

    fig, axes = plt.subplots(len(samples), 1, figsize=(11, 4.2 * len(samples)), sharex=True, constrained_layout=True)
    axes = np.atleast_1d(axes)
    for ax, sample in zip(axes, samples):
        for factor in FACTORS:
            d = weekly[sample].query("factor == @factor").sort_values("signal_date")
            nav = (1 + d["long_short_return"]).cumprod()
            ax.plot(d["signal_date"], nav, label=factor, color=FACTOR_COLORS[factor], linewidth=1.4)
        ax.set_title(SAMPLE_LABELS[sample])
        ax.set_ylabel("Cumulative net value")
        ax.set_xlim(d["signal_date"].min(), d["signal_date"].max())
        ax.legend(ncol=3, frameon=False)
    axes[-1].set_xlabel("Signal date")
    fig.suptitle(f"P03 - Cumulative Top 30% Minus Bottom 30% Long-Short Return ({PERIOD})")
    add_period_note(fig, "Net value compounds weekly: cumulative product of (1 + weekly Top 30% − Bottom 30% return).")
    save_figure(fig, records, output_root, "performance", "P03", "cumulative_long_short", samples,
                source_path(*[f"results/performance/{s}_factor_weekly_metrics.csv" for s in samples]),
                "factor; signal_date; long_short_return", "Weekly-compounded Top 30% minus Bottom 30% long-short net value.")

    fig, axes = plt.subplots(2, 3, figsize=(12, 7.5), sharex=True, constrained_layout=True)
    for ax, factor in zip(axes.flat, FACTORS):
        for sample in samples:
            d = quintiles[sample].query("factor == @factor").sort_values("quantile")
            ax.plot(d["quantile"], d["mean_weekly_return"] * 100, marker="o", color=FACTOR_COLORS[factor],
                    linestyle=SAMPLE_LINESTYLES[sample], label=SAMPLE_LABELS[sample])
        ax.set_title(factor, color=FACTOR_COLORS[factor])
        ax.set_xticks(range(1, 6), ["Q1", "Q2", "Q3", "Q4", "Q5"])
        ax.set_ylabel("Mean weekly return (%)")
        zero_line(ax)
    axes[0, 0].legend(frameon=False, loc="best")
    fig.suptitle(f"P04 - Quintile Return Profiles ({PERIOD})")
    add_period_note(fig, "Q1 is the highest-score group; Q5 is the lowest-score group.")
    save_figure(fig, records, output_root, "performance", "P04", "quintile_return_profiles", samples,
                source_path(*[f"results/performance/{s}_factor_quintile_return_summary.csv" for s in samples]),
                "factor; quantile; mean_weekly_return", "Factor-specific mean weekly return by deterministic score quintile.")

    fig, ax = plt.subplots(figsize=(9, 5.3), constrained_layout=True)
    factor_bar(ax, {s: d["top20_excess_mean_weekly"] for s, d in summaries.items()}, "Mean weekly excess return (%)", f"P05 - Top-20 Long-only Excess Return ({PERIOD})", percent=True)
    add_period_note(fig, "Excess return versus contemporaneous equal-weight factor-available universe; not STI.")
    save_figure(fig, records, output_root, "performance", "P05", "top20_excess_return", samples,
                source_path(*[f"results/performance/{s}_factor_performance_summary.csv" for s in samples]),
                "factor; top20_excess_mean_weekly", "Mean weekly Top-20 return minus factor-available equal-weight universe return.")

    fig, ax = plt.subplots(figsize=(9, 5.3), constrained_layout=True)
    factor_bar(ax, {s: d["score_nonnull"] / d["total_rows"] for s, d in coverage.items()}, "Score coverage (%)", f"P06 - Factor Score Coverage ({PERIOD})", percent=True)
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=100))
    add_period_note(fig, "Coverage = non-null factor scores / rows in the respective sample.")
    save_figure(fig, records, output_root, "performance", "P06", "factor_coverage", samples,
                source_path(*[f"results/performance/{s}_factor_coverage.csv" for s in samples]),
                "factor; score_nonnull; total_rows", "Valid score coverage within each formal sample.")


def robustness_figures(samples: list[str], output_root: Path, records: list[FigureRecord]) -> None:
    annual = read_result("robustness/annual_factor_robustness.csv")
    summary = read_result("robustness/factor_robustness_summary.csv")

    fig, axes = plt.subplots(1, len(samples), figsize=(6.4 * len(samples), 5.5), constrained_layout=True)
    axes = np.atleast_1d(axes)
    for ax, sample in zip(axes, samples):
        matrix = annual.query("sample == @sample").pivot(index="factor", columns="year", values="mean_rank_ic").reindex(FACTORS)
        heatmap(ax, matrix, SAMPLE_LABELS[sample], "Mean annual Rank IC", center=0, fmt=".3f")
    fig.suptitle(f"R01 - Annual Rank IC Heatmap ({PERIOD})")
    add_period_note(fig, "Annual values are mean weekly cross-sectional Rank IC.")
    save_figure(fig, records, output_root, "robustness", "R01", "annual_rank_ic_heatmap", samples,
                "results/robustness/annual_factor_robustness.csv", "sample; factor; year; mean_rank_ic",
                "Annual mean weekly Rank IC, shown separately by sample.")

    fig, axes = plt.subplots(1, len(samples), figsize=(6.4 * len(samples), 5.5), constrained_layout=True)
    axes = np.atleast_1d(axes)
    for ax, sample in zip(axes, samples):
        matrix = annual.query("sample == @sample").pivot(index="factor", columns="year", values="mean_ls_weekly").reindex(FACTORS) * 100
        heatmap(ax, matrix, SAMPLE_LABELS[sample], "Annual mean weekly long-short return (%)", center=0, fmt=".2f")
    fig.suptitle(f"R02 - Annual Long-Short Return Heatmap ({PERIOD})")
    add_period_note(fig, "Values are annual mean weekly long-short returns, not annual compounded returns.")
    save_figure(fig, records, output_root, "robustness", "R02", "annual_long_short_heatmap", samples,
                "results/robustness/annual_factor_robustness.csv", "sample; factor; year; mean_ls_weekly",
                "Annual mean of weekly Top 30% minus Bottom 30% long-short return.")

    data = {sample: summary.query("sample == @sample").set_index("factor")["ic_positive_week_rate"] for sample in samples}
    fig, ax = plt.subplots(figsize=(9, 5.3), constrained_layout=True)
    factor_bar(ax, data, "Positive Rank IC weeks (%)", f"R03 - Positive Rank IC Frequency ({PERIOD})", percent=True)
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=100))
    add_period_note(fig, "Frequency of weeks with Rank IC above zero.")
    save_figure(fig, records, output_root, "robustness", "R03", "positive_rank_ic_frequency", samples,
                "results/robustness/factor_robustness_summary.csv", "sample; factor; ic_positive_week_rate",
                "Share of evaluated weeks with positive Rank IC.")

    metrics = [
        ("mean_rank_ic", "Mean Rank IC", False),
        ("avg_long_short_weekly", "Mean weekly long-short return (%)", True),
        ("avg_top20_excess_weekly", "Mean weekly Top-20 excess return (%)", True),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.1), constrained_layout=True)
    for ax, (column, ylabel, percent) in zip(axes, metrics):
        data = {sample: summary.query("sample == @sample").set_index("factor")[column] for sample in samples}
        factor_bar(ax, data, ylabel, ylabel.replace("Mean ", ""), percent=percent)
    fig.suptitle(f"R04 - Universe Filter Sensitivity ({PERIOD})")
    add_period_note(fig, "Panels use separate axes because metrics have different units.")
    save_figure(fig, records, output_root, "robustness", "R04", "universe_filter_sensitivity", samples,
                "results/robustness/factor_robustness_summary.csv",
                "sample; factor; mean_rank_ic; avg_long_short_weekly; avg_top20_excess_weekly",
                "Direct comparison of each summary metric across the two formal sample definitions.")

    fig, axes = plt.subplots(2, 3, figsize=(12, 7.3), sharex=True, constrained_layout=True)
    for ax, factor in zip(axes.flat, FACTORS):
        for sample in samples:
            d = annual.query("sample == @sample and factor == @factor").sort_values("year")
            ax.plot(d["year"], d["mean_rank_ic"], marker="o", linestyle=SAMPLE_LINESTYLES[sample],
                    color=FACTOR_COLORS[factor], label=SAMPLE_LABELS[sample])
        zero_line(ax)
        ax.set_title(factor, color=FACTOR_COLORS[factor])
        ax.set_xticks(range(2011, 2016))
        ax.set_ylabel("Annual mean Rank IC")
    axes[0, 0].legend(frameon=False)
    fig.suptitle(f"R05 - Annual Factor Stability ({PERIOD})")
    add_period_note(fig, "Line charts highlight interannual variation in annual mean weekly Rank IC.")
    save_figure(fig, records, output_root, "robustness", "R05", "annual_factor_stability", samples,
                "results/robustness/annual_factor_robustness.csv", "sample; factor; year; mean_rank_ic",
                "Interannual stability of annual mean weekly Rank IC.")


def correlation_matrix(pairs: pd.DataFrame, sample: str) -> pd.DataFrame:
    matrix = pd.DataFrame(np.eye(len(FACTORS)), index=FACTORS, columns=FACTORS)
    sub = pairs.query("mode == @sample and universe == 'six_complete'")
    for row in sub.itertuples(index=False):
        matrix.loc[row.factor_a, row.factor_b] = row.mean_weekly_spearman
        matrix.loc[row.factor_b, row.factor_a] = row.mean_weekly_spearman
    return matrix


def correlation_figures(samples: list[str], output_root: Path, records: list[FigureRecord]) -> None:
    pairs = read_result("correlation/factor_pairwise_weekly_spearman.csv")
    coverage = read_result("correlation/complete_case_coverage.csv")
    weekly = read_result("correlation/composite_portfolio_weekly_returns.csv")
    summary = read_result("correlation/composite_portfolio_performance_summary.csv")
    increments = read_result("correlation/factor_incremental_contributions.csv")

    fig, axes = plt.subplots(1, len(samples), figsize=(6.4 * len(samples), 5.5), constrained_layout=True)
    axes = np.atleast_1d(axes)
    for ax, sample in zip(axes, samples):
        matrix = correlation_matrix(pairs, sample)
        heatmap(ax, matrix, SAMPLE_LABELS[sample], "Mean weekly Spearman correlation", vmin=-1, vmax=1, center=0, fmt=".2f")
    fig.suptitle(f"C01 - Factor Spearman Correlation Heatmap ({PERIOD})")
    add_period_note(fig, "Mean weekly cross-sectional score correlation in the six-complete universe; not factor-return correlation.")
    save_figure(fig, records, output_root, "correlation", "C01", "spearman_correlation_heatmap", samples,
                "results/correlation/factor_pairwise_weekly_spearman.csv",
                "mode; universe; factor_a; factor_b; mean_weekly_spearman",
                "Symmetric matrix of mean weekly cross-sectional Spearman score correlations.")

    fig, axes = plt.subplots(2, 1, figsize=(11, 7.2), sharex=True, constrained_layout=True)
    for sample in samples:
        d = coverage.query("mode == @sample").sort_values("signal_date")
        axes[0].plot(d["signal_date"], d["base_n"], label=f"{SAMPLE_LABELS[sample]} base", linestyle=SAMPLE_LINESTYLES[sample])
        axes[0].plot(d["signal_date"], d["six_complete_n"], label=f"{SAMPLE_LABELS[sample]} six-complete", linestyle=SAMPLE_LINESTYLES[sample], alpha=0.7)
        axes[1].plot(d["signal_date"], d["six_complete_n"] / d["base_n"] * 100, label=SAMPLE_LABELS[sample], linestyle=SAMPLE_LINESTYLES[sample])
    axes[0].set_xlim(coverage["signal_date"].min(), coverage["signal_date"].max())
    axes[0].set_ylabel("Stocks")
    axes[0].set_title("Weekly base and six-factor complete-case universe")
    axes[0].legend(ncol=2, frameon=False)
    axes[1].set_ylabel("Six-complete coverage (%)")
    axes[1].set_xlabel("Signal date")
    axes[1].yaxis.set_major_formatter(PercentFormatter(xmax=100))
    axes[1].legend(frameon=False)
    fig.suptitle(f"C02 - Complete-case Coverage ({PERIOD})")
    add_period_note(fig, "Coverage ratio = six-factor complete-case count / base universe count.")
    save_figure(fig, records, output_root, "correlation", "C02", "complete_case_coverage", samples,
                "results/correlation/complete_case_coverage.csv", "mode; signal_date; base_n; six_complete_n",
                "Weekly base-universe, six-complete universe, and their ratio.")

    strategies = ["UNIVERSE_EW", "ADD_1", "ADD_2", "ADD_3", "ADD_4", "ADD_5", "ADD_6"]
    fig, axes = plt.subplots(len(samples), 1, figsize=(11, 4.2 * len(samples)), sharex=True, constrained_layout=True)
    axes = np.atleast_1d(axes)
    for ax, sample in zip(axes, samples):
        d = weekly.query("mode == @sample").sort_values("signal_date")
        for strategy in strategies:
            ax.plot(d["signal_date"], (1 + d[strategy]).cumprod(), label=COMPOSITE_LABELS[strategy],
                    color=COMPOSITE_COLORS[strategy], linewidth=1.35)
        ax.set_title(SAMPLE_LABELS[sample])
        ax.set_ylabel("Cumulative net value")
        ax.set_xlim(d["signal_date"].min(), d["signal_date"].max())
        ax.legend(ncol=3, frameon=False)
    axes[-1].set_xlabel("Signal date")
    fig.suptitle(f"C03 - Composite Portfolio Cumulative Return ({PERIOD})")
    add_period_note(fig, "Each curve compounds weekly returns from the same weekly complete-case universe within its sample.")
    save_figure(fig, records, output_root, "correlation", "C03", "composite_cumulative_return", samples,
                "results/correlation/composite_portfolio_weekly_returns.csv",
                "mode; signal_date; UNIVERSE_EW; ADD_1; ADD_2; ADD_3; ADD_4; ADD_5; ADD_6",
                "Weekly-compounded net values for the fixed complete-case composite sequence.")

    fig, axes = plt.subplots(1, len(samples), figsize=(6.8 * len(samples), 5.5), constrained_layout=True)
    axes = np.atleast_1d(axes)
    for ax, sample in zip(axes, samples):
        d = summary.query("mode == @sample and strategy in @strategies")
        ax.scatter(d["annualized_vol"] * 100, d["annualized_compound_return"] * 100, s=45, color="#4d4d4d")
        for row in d.itertuples(index=False):
            ax.annotate(COMPOSITE_LABELS[row.strategy], (row.annualized_vol * 100, row.annualized_compound_return * 100), fontsize=8, xytext=(4, 3), textcoords="offset points")
        zero_line(ax)
        ax.axvline(0, color="#333333", linewidth=0.8)
        ax.set_title(SAMPLE_LABELS[sample])
        ax.set_xlabel("Annualized volatility (%)")
        ax.set_ylabel("Annualized compound return (%)")
    fig.suptitle(f"C04 - Composite Portfolio Risk-Return Comparison ({PERIOD})")
    add_period_note(fig, "Annualized quantities and Sharpe proxy are descriptive diagnostics, not verified live strategy performance.")
    save_figure(fig, records, output_root, "correlation", "C04", "composite_risk_return", samples,
                "results/correlation/composite_portfolio_performance_summary.csv",
                "mode; strategy; annualized_compound_return; annualized_vol; sharpe_proxy",
                "Descriptive annualized compound return versus annualized volatility for fixed composite portfolios.")

    fig, ax = plt.subplots(figsize=(9, 5.4), constrained_layout=True)
    data = {}
    for sample in samples:
        d = increments.query("mode == @sample and comparison.str.startswith('WITHOUT_')", engine="python").set_index("added_factor")
        data[sample] = d["mean_weekly_increment"]
    factor_bar(ax, data, "Mean weekly increment (%)", f"C05 - Leave-One-Out Incremental Contributions ({PERIOD})", percent=True)
    add_period_note(fig, "Fixed-setting diagnostic: ADD_6 minus the corresponding leave-one-out portfolio; not a causal effect or dynamic Top-K performance.")
    save_figure(fig, records, output_root, "correlation", "C05", "leave_one_out_increment", samples,
                "results/correlation/factor_incremental_contributions.csv",
                "mode; comparison; added_factor; mean_weekly_increment; annualized_tracking_error",
                "Weekly return difference between complete six-factor composite and each leave-one-out counterpart.")


def validation_figures(samples: list[str], output_root: Path, records: list[FigureRecord]) -> None:
    summary = {
        sample: read_result(f"validation/{sample}_factor_validation_summary.csv").set_index("factor")
        for sample in samples
    }
    annual = {
        sample: read_result(f"validation/{sample}_annual_factor_validation.csv")
        for sample in samples
    }

    metrics = [
        ("ic_hac_t", "Rank IC HAC t-statistic"),
        ("ls_30pct_hac_t", "Long-short HAC t-statistic"),
        ("top20pct_excess_hac_t", "Top 20% excess HAC t-statistic"),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.2), constrained_layout=True)
    for ax, (column, title) in zip(axes, metrics):
        factor_bar(ax, {s: d[column] for s, d in summary.items()}, "HAC t-statistic", title)
        ax.axhline(1.96, color="#666666", linewidth=0.8, linestyle=":")
        ax.axhline(-1.96, color="#666666", linewidth=0.8, linestyle=":")
    fig.suptitle(f"V01 - HAC t-statistics ({PERIOD})")
    add_period_note(fig, "Newey-West/Bartlett lag = 4; two-sided normal-asymptotic reference lines at ±1.96.")
    save_figure(fig, records, output_root, "validation", "V01", "hac_t_statistics", samples,
                source_path(*[f"results/validation/{s}_factor_validation_summary.csv" for s in samples]),
                "factor; ic_hac_t; ls_30pct_hac_t; top20pct_excess_hac_t",
                "Existing Newey-West/Bartlett t-statistics for three diagnostic metrics.")

    fig, axes = plt.subplots(len(samples), 1, figsize=(10, 3.8 * len(samples)), sharex=True, constrained_layout=True)
    axes = np.atleast_1d(axes)
    for ax, sample in zip(axes, samples):
        d = summary[sample].reindex(FACTORS)
        mean = d["top20pct_excess_hac_mean"] * 100
        ci = 1.96 * d["top20pct_excess_hac_se"] * 100
        y = np.arange(len(FACTORS))
        bars = ax.barh(y, mean, xerr=ci, color=[FACTOR_COLORS[f] for f in FACTORS], ecolor="#333333", capsize=3)
        ax.set_yticks(y, FACTORS)
        ax.invert_yaxis()
        ax.set_xlabel("Mean weekly Top 20% excess return (%)")
        ax.set_title(SAMPLE_LABELS[sample])
        ax.axvline(0, color="#333333", linewidth=0.8, zorder=1)
    fig.suptitle(f"V02 - Top 20% Excess Return with HAC Confidence Intervals ({PERIOD})")
    add_period_note(fig, "95% normal-approximation intervals: HAC mean ± 1.96 × HAC standard error; lag = 4.")
    save_figure(fig, records, output_root, "validation", "V02", "top20_excess_hac_ci", samples,
                source_path(*[f"results/validation/{s}_factor_validation_summary.csv" for s in samples]),
                "factor; top20pct_excess_hac_mean; top20pct_excess_hac_se",
                "Existing HAC mean and standard error for weekly Top 20% excess return.")

    fig, axes = plt.subplots(1, len(samples), figsize=(6.4 * len(samples), 5.5), constrained_layout=True)
    axes = np.atleast_1d(axes)
    for ax, sample in zip(axes, samples):
        matrix = annual[sample].pivot(index="factor", columns="year", values="long_short_30pct_compound_return").reindex(FACTORS) * 100
        heatmap(ax, matrix, SAMPLE_LABELS[sample], "Annual compounded long-short return (%)", center=0, fmt=".2f")
    fig.suptitle(f"V03 - Annual Compounded Long-Short Returns ({PERIOD})")
    add_period_note(fig, "Each annual value compounds the actual weekly Top 30% minus Bottom 30% long-short series.")
    save_figure(fig, records, output_root, "validation", "V03", "annual_compounded_long_short", samples,
                source_path(*[f"results/validation/{s}_annual_factor_validation.csv" for s in samples]),
                "factor; year; long_short_30pct_compound_return",
                "Calendar-year compounded Top 30% minus Bottom 30% long-short return.")

    screen_columns = [
        ("screen_weeks_ge_150", "Weeks ≥ 150"),
        ("screen_mean_ic_positive", "Mean IC > 0"),
        ("screen_mean_ls_weekly_positive", "Mean weekly LS > 0"),
        ("screen_ls_compound_positive_years_ge_3", "≥3 positive LS years"),
    ]
    fig, axes = plt.subplots(1, len(samples), figsize=(6.8 * len(samples), 5.5), constrained_layout=True)
    axes = np.atleast_1d(axes)
    for ax, sample in zip(axes, samples):
        d = summary[sample].reindex(FACTORS)
        matrix = pd.DataFrame({label: d[column].astype(int) for column, label in screen_columns}, index=FACTORS)
        image = ax.imshow(matrix, cmap="Greens", vmin=0, vmax=1, aspect="auto")
        ax.set_xticks(range(matrix.shape[1]), matrix.columns, rotation=30, ha="right")
        ax.set_yticks(range(matrix.shape[0]), matrix.index)
        for i in range(matrix.shape[0]):
            for j in range(matrix.shape[1]):
                ax.text(j, i, "Pass" if matrix.iloc[i, j] else "Fail", ha="center", va="center", fontsize=8)
        ax.set_title(SAMPLE_LABELS[sample])
    fig.suptitle(f"V04 - Factor Screening Criteria ({PERIOD})")
    add_period_note(fig, "Descriptive operational screens only; they are not statistical-significance claims.")
    save_figure(fig, records, output_root, "validation", "V04", "factor_screening_criteria", samples,
                source_path(*[f"results/validation/{s}_factor_validation_summary.csv" for s in samples]),
                "; ".join(column for column, _ in screen_columns),
                "Current descriptive screening outcomes recalculated by the frozen validation results.")

    metrics = [
        ("mean_top20pct_excess_weekly", "Mean weekly Top 20% excess return (%)", True),
        ("top20pct_excess_hac_t", "Top 20% excess HAC t-statistic", False),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(11, 5.2), constrained_layout=True)
    for ax, (column, title, percent) in zip(axes, metrics):
        factor_bar(ax, {s: d[column] for s, d in summary.items()}, title, title, percent=percent)
        if column.endswith("_t"):
            ax.axhline(1.96, color="#666666", linewidth=0.8, linestyle=":")
            ax.axhline(-1.96, color="#666666", linewidth=0.8, linestyle=":")
    fig.suptitle(f"V05 - Full Universe vs Price-eligible Validation ({PERIOD})")
    add_period_note(fig, "Top 20% excess is relative to the contemporaneous factor-available equal-weight universe; HAC lag = 4.")
    save_figure(fig, records, output_root, "validation", "V05", "sample_validation_comparison", samples,
                source_path(*[f"results/validation/{s}_factor_validation_summary.csv" for s in samples]),
                "factor; mean_top20pct_excess_weekly; top20pct_excess_hac_t",
                "Comparison of Top 20% excess-return level and HAC t-statistic across formal samples.")


def write_index(records: list[FigureRecord], output_root: Path) -> Path:
    path = output_root / "figure_index.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(FigureRecord.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(record.__dict__ for record in records)
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", choices=["all", "performance", "robustness", "correlation", "validation"], default="all")
    parser.add_argument("--sample", choices=["both", "PRICE_ELIGIBLE_TRUE", "ALL_PAIRED"], default="both")
    parser.add_argument("--outdir", type=Path, default=DEFAULT_FIGURES_DIR, help="Base output directory; defaults to project-root figures/")
    return parser.parse_args()


def main() -> None:
    configure_style()
    args = parse_args()
    samples = SAMPLES if args.sample == "both" else [args.sample]
    selected = ["performance", "robustness", "correlation", "validation"] if args.module == "all" else [args.module]
    generators: dict[str, Callable[[list[str], Path, list[FigureRecord]], None]] = {
        "performance": performance_figures,
        "robustness": robustness_figures,
        "correlation": correlation_figures,
        "validation": validation_figures,
    }
    records: list[FigureRecord] = []
    for module in selected:
        generators[module](samples, args.outdir, records)
    index_path = write_index(records, args.outdir)
    print(f"Generated {len(records)} figure records for {sample_title(samples)}.")
    print(f"Figure index: {index_path.resolve()}")


if __name__ == "__main__":
    main()
