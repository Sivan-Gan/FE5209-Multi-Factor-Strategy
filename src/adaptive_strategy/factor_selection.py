
"""Point-in-time adaptive factor selection."""

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SelectionConfig:
    lookback_years: int = 3
    top_k: int = 3
    min_coverage: float = 0.65
    weeks_per_year: int = 52


def select_factors(
    history: pd.DataFrame,
    cutoff: str | pd.Timestamp,
    families: list[str],
    config: SelectionConfig,
) -> tuple[list[str], pd.DataFrame]:
    """Select Top-K factors using completed historical returns."""

    cutoff = pd.Timestamp(cutoff)

    if config.lookback_years <= 0:
        raise ValueError("lookback_years must be positive")

    if not 1 <= config.top_k <= len(families):
        raise ValueError("Invalid top_k")

    if not 0 < config.min_coverage <= 1:
        raise ValueError("Invalid min_coverage")

    required = {
        "family",
        "next_execution_date",
        "factor_return_gross",
        "factor_return_available",
    }

    missing = required - set(history.columns)

    if missing:
        raise ValueError(f"Missing columns: {missing}")

    data = history.copy()

    data["next_execution_date"] = pd.to_datetime(
        data["next_execution_date"]
    )

    data["factor_return_gross"] = pd.to_numeric(
        data["factor_return_gross"],
        errors="coerce",
    )

    start = cutoff - pd.DateOffset(
        years=config.lookback_years
    )

    train = data[
        data["family"].isin(families)
        & data["next_execution_date"].gt(start)
        & data["next_execution_date"].le(cutoff)
        & data["factor_return_available"].eq(1)
        & np.isfinite(data["factor_return_gross"])
    ].copy()

    stats = (
        train.groupby("family")["factor_return_gross"]
        .agg(["count", "mean", "std"])
        .reindex(families)
        .reset_index()
    )

    min_obs = int(
        np.floor(
            config.lookback_years
            * config.weeks_per_year
            * config.min_coverage
        )
    )

    stats["eligible"] = (
        stats["count"].ge(min_obs)
        & stats["std"].gt(0)
        & np.isfinite(stats["mean"])
    )

    stats["selection_score"] = np.where(
        stats["eligible"],
        stats["mean"] / stats["std"],
        np.nan,
    )

    stats = stats.sort_values(
        ["selection_score", "family"],
        ascending=[False, True],
        na_position="last",
    ).reset_index(drop=True)

    eligible = stats[stats["eligible"]]

    if len(eligible) < config.top_k:
        raise ValueError(
            "Insufficient eligible factors"
        )

    selected = eligible.head(
        config.top_k
    )["family"].tolist()

    stats["selected"] = stats["family"].isin(
        selected
    )

    stats["rank"] = np.arange(
        1, len(stats) + 1
    )

    return selected, stats
