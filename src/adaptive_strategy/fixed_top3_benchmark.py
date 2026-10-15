
"""Fixed Top-3 IV benchmark.

Factor set is selected once at the first 2021 signal.
Factor IV weights are re-estimated annually.
Stock portfolios are rebalanced weekly.

Historical evaluation: 2021-2025.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from src.adaptive_strategy.factor_selection import (
    SelectionConfig,
    select_factors,
)
from src.adaptive_strategy.factor_weighting import (
    WeightingConfig,
    calculate_selected_weights,
)
from src.adaptive_strategy.pipeline import (
    PipelineConfig,
    load_common_scores,
    load_factor_history,
    score_all_weeks,
)
from src.strategy import portfolio as pf
from src.strategy import metrics as mt
from src.strategy import run_gate_b as rg


FAMILIES = [
    "Quality",
    "Momentum",
    "Low Risk",
    "Investment",
    "Value",
    "Dividend",
    "Size",
]


@dataclass(frozen=True)
class FixedBenchmarkConfig:
    start: str = "2021-01-01"
    end: str = "2025-12-31"
    selection_lookback_years: int = 3
    risk_window: int = 52
    expected_weeks: int = 259
    cost_oneway: float = 0.001
    use_buffer: bool = True


def build_fixed_decisions(
    history: pd.DataFrame,
    dates: list,
    config: FixedBenchmarkConfig,
):
    """Select factors once; re-estimate IV weights annually."""

    calendar = pd.to_datetime(pd.Series(dates))

    first_dates = (
        calendar.groupby(calendar.dt.year)
        .min()
        .sort_index()
    )

    initial_cutoff = first_dates.iloc[0]

    selected, selection_stats = select_factors(
        history=history,
        cutoff=initial_cutoff,
        families=FAMILIES,
        config=SelectionConfig(
            lookback_years=config.selection_lookback_years,
            top_k=3,
            min_coverage=0.65,
        ),
    )

    print("\n=== Frozen Factor Selection ===")
    print("Selection cutoff:", initial_cutoff)
    print("Selected factors:", selected)

    annual_rows = []
    weekly_rows = []

    for year, cutoff in first_dates.items():
        methods, wide = calculate_selected_weights(
            history=history,
            cutoff=cutoff,
            selected_factors=selected,
            config=WeightingConfig(
                window=config.risk_window,
            ),
            covariance_families=FAMILIES,
        )

        weights = methods["IV"]

        full = {
            f: weights.get(f, 0.0)
            for f in FAMILIES
        }

        annual_rows.append({
            "year": int(year),
            "cutoff": cutoff,
            "method": "IV",
            "selected_factors": ", ".join(selected),
            "risk_window_weeks": len(wide),
            **{
                f"w_{f.replace(' ', '')}": full[f]
                for f in FAMILIES
            },
        })

        for date in calendar[calendar.dt.year == year]:
            weekly_rows.append({
                "signal_date": date,
                "method": "IV",
                **{
                    f"w_{f.replace(' ', '')}": full[f]
                    for f in FAMILIES
                },
            })

    return (
        selected,
        selection_stats,
        pd.DataFrame(annual_rows),
        pd.DataFrame(weekly_rows),
    )


def run_fixed_benchmark(
    data_root: Path,
    output_dir: Path,
    config: FixedBenchmarkConfig = FixedBenchmarkConfig(),
):
    data_root = Path(data_root).resolve()
    output_dir = Path(output_dir).resolve()

    pipeline_config = PipelineConfig(
        start=config.start,
        end=config.end,
        cost_oneway=config.cost_oneway,
        use_buffer=config.use_buffer,
        expected_weeks=config.expected_weeks,
    )

    base = load_common_scores(
        data_root,
        pipeline_config,
    )

    history = load_factor_history(data_root)

    returns = rg.load_returns(
        data_root,
        config.start,
        config.end,
    )

    returns["signal_date"] = pd.to_datetime(
        returns["signal_date"]
    )

    returns["next_execution_date"] = pd.to_datetime(
        returns["next_execution_date"],
        errors="coerce",
    )

    completed = set(
        returns.loc[
            returns["next_execution_date"].notna()
            & returns["next_execution_date"].le(
                pd.Timestamp(config.end)
            ),
            "signal_date",
        ]
    )

    base = base[
        base["signal_date"].isin(completed)
    ].copy()

    dates = sorted(base["signal_date"].unique())

    if len(dates) != config.expected_weeks:
        raise ValueError(
            f"Expected {config.expected_weeks} weeks, "
            f"got {len(dates)}"
        )

    selected, stats, annual, weekly_weights = (
        build_fixed_decisions(
            history,
            dates,
            config,
        )
    )

    scored = score_all_weeks(
        base,
        annual,
        weekly_weights,
        method="IV",
    )

    scored["signal_date"] = (
        pd.to_datetime(scored["signal_date"])
        .dt.strftime("%Y-%m-%d")
    )

    returns["signal_date"] = (
        returns["signal_date"].dt.strftime("%Y-%m-%d")
    )

    returns["next_execution_date"] = (
        returns["next_execution_date"]
        .dt.strftime("%Y-%m-%d")
    )

    holdings, construction = pf.build_holdings(
        scored,
        use_buffer=config.use_buffer,
    )

    if holdings.duplicated(
        ["signal_date", "PERMNO"]
    ).any():
        raise ValueError("Duplicate holdings")

    if (
        construction["max_sector_weight"] > 0.30 + 1e-12
    ).any():
        raise ValueError("Sector cap violation")

    path, ledger = mt.weekly_path_and_ledger(
        holdings,
        returns,
        config.end,
        config.cost_oneway,
    )

    path["net_return"] = mt.net_returns(
        path,
        config.cost_oneway,
    )

    if len(path) != config.expected_weeks:
        raise ValueError("Incorrect benchmark length")

    strategy_name = "Fixed_Top3_IV"

    for frame in (
        path, holdings, construction, ledger
    ):
        frame["strategy"] = strategy_name

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    annual.to_csv(
        output_dir / "annual_factor_weights.csv",
        index=False,
    )

    stats.to_csv(
        output_dir / "initial_factor_ranking.csv",
        index=False,
    )

    weekly_weights.to_csv(
        output_dir / "weekly_factor_weights.csv",
        index=False,
    )

    holdings.to_csv(
        output_dir / "holdings.csv",
        index=False,
    )

    construction.to_csv(
        output_dir / "construction_summary.csv",
        index=False,
    )

    ledger.to_csv(
        output_dir / "trade_ledger.csv",
        index=False,
    )

    path.to_csv(
        output_dir / "weekly_paths.csv",
        index=False,
    )

    r = path["net_return"].to_numpy(dtype=float)

    cumulative = float(np.prod(1 + r) - 1)

    cagr = float(
        (1 + cumulative) ** (52 / len(r)) - 1
    )

    vol = float(
        np.std(r, ddof=1) * np.sqrt(52)
    )

    nav = np.r_[1.0, np.cumprod(1 + r)]
    peak = np.maximum.accumulate(nav)
    max_dd = float(1 - np.min(nav / peak))

    print("\n=== Fixed Top-3 IV Performance ===")
    print("Selected factors:", selected)
    print("Weeks:", len(path))
    print("Cumulative return:", round(cumulative, 6))
    print("CAGR:", round(cagr, 6))
    print("Volatility:", round(vol, 6))
    print("Max drawdown:", round(max_dd, 6))
    print("Results saved to:", output_dir)

    return path, annual, selected
