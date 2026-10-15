
"""Adaptive composite score construction.

Supports:
- common_all: all candidate factors must be available
- selected_only: selected factors must be available

No post-hoc reallocation of missing factor weights.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd


DEFAULT_FACTORS = {
    "Quality": "QUALITY_CURRENT",
    "Momentum": "MOMENTUM_12_1",
    "Low Risk": "LOWRISK_CURRENT",
    "Investment": "INVESTMENT_CURRENT",
    "Value": "VALUE_CURRENT",
    "Dividend": "DIVIDEND_CURRENT",
    "Size": "SIZE_CURRENT",
}


@dataclass(frozen=True)
class ScoreConfig:
    availability_mode: str = "common_all"


def calculate_scores(
    scores: pd.DataFrame,
    selected_factors: list[str],
    weights: dict[str, float],
    config: ScoreConfig = ScoreConfig(),
    factor_columns: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Build weighted composite scores for one or more weeks.

    Input must include:
      signal_date, execution_date, PERMNO,
      historical_gics_sector,
      factor scores and availability flags.
    """

    if factor_columns is None:
        factor_columns = DEFAULT_FACTORS

    if config.availability_mode not in {
        "common_all",
        "selected_only",
    }:
        raise ValueError("Invalid availability mode")

    if not selected_factors:
        raise ValueError("No selected factors")

    if not set(selected_factors).issubset(factor_columns):
        raise ValueError("Unknown selected factors")

    if set(weights) != set(selected_factors):
        raise ValueError("Weight/factor mismatch")

    w = np.array(
        [weights[f] for f in selected_factors],
        dtype=float,
    )

    if (
        not np.isfinite(w).all()
        or (w < 0).any()
        or not np.isclose(w.sum(), 1.0, atol=1e-9)
    ):
        raise ValueError("Invalid factor weights")

    data = scores.copy()

    required = {
        "signal_date",
        "execution_date",
        "PERMNO",
        "historical_gics_sector",
    }

    if config.availability_mode == "common_all":
        required_factors = list(factor_columns)
    else:
        required_factors = selected_factors

    for family in required_factors:
        col = factor_columns[family]

        required.add(col)
        required.add(f"{col}__available")

    missing = required - set(data.columns)

    if missing:
        raise ValueError(
            f"Missing score columns: {missing}"
        )

    valid = np.ones(len(data), dtype=bool)

    for family in required_factors:
        col = factor_columns[family]

        score = pd.to_numeric(
            data[col],
            errors="coerce",
        )

        available = (
            data[f"{col}__available"].eq(1)
            & np.isfinite(score)
        )

        valid &= available.to_numpy()

    data = data.loc[valid].copy()

    if data.empty:
        raise ValueError("No valid score observations")

    F = data[
        [factor_columns[f] for f in selected_factors]
    ].to_numpy(dtype=float)

    data["composite_score"] = F @ w

    data["score_available"] = 1

    data["score_rank_pct"] = (
        data.groupby("signal_date")["composite_score"]
        .rank(pct=True, ascending=False)
    )

    return data[
        [
            "signal_date",
            "execution_date",
            "PERMNO",
            "historical_gics_sector",
            "composite_score",
            "score_available",
            "score_rank_pct",
        ]
    ]
