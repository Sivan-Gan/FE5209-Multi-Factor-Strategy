
"""Adaptive Strategy V1 - Model Selection Trainer.

Select the weighting method using training-period
net returns only. Evaluate the frozen choice on
a separate validation period.

Reuses the original strategy metrics conventions.
"""

from dataclasses import dataclass, asdict
from pathlib import Path
import hashlib
import json

import numpy as np
import pandas as pd

from src.strategy import metrics as mt


CANDIDATES = (
    "WF_3Y_Top3_EW",
    "WF_3Y_Top3_IV",
    "WF_3Y_Top3_MV",
)


@dataclass(frozen=True)
class TrainerConfig:
    train_start: str = "2016-01-01"
    train_end: str = "2020-12-31"
    validation_start: str = "2021-01-01"
    validation_end: str = "2025-12-31"
    selection_metric: str = "sharpe"
    expected_train_weeks: int = 261
    expected_validation_weeks: int = 259


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)

    return h.hexdigest()


def load_inputs(
    paths_file: Path,
    rf_file: Path,
) -> pd.DataFrame:
    paths = pd.read_csv(paths_file)
    rf = pd.read_parquet(
        rf_file,
        columns=["signal_date", "risk_free_return"],
    )

    required = {
        "signal_date",
        "strategy",
        "net_return",
        "gross_return",
        "gtr",
        "turnover",
    }

    if not required.issubset(paths.columns):
        raise ValueError(
            f"Missing path columns: {required - set(paths.columns)}"
        )

    paths["signal_date"] = pd.to_datetime(
        paths["signal_date"]
    )
    rf["signal_date"] = pd.to_datetime(
        rf["signal_date"]
    )

    if paths.duplicated(["strategy", "signal_date"]).any():
        raise ValueError("Duplicate strategy/date rows")

    if rf["signal_date"].duplicated().any():
        raise ValueError("Duplicate risk-free dates")

    paths = paths[
        paths["strategy"].isin(CANDIDATES)
    ].copy()

    if set(paths["strategy"]) != set(CANDIDATES):
        raise ValueError("Missing candidate strategies")

    merged = paths.merge(
        rf,
        on="signal_date",
        how="left",
        validate="many_to_one",
    )

    numeric = [
        "net_return",
        "gross_return",
        "gtr",
        "turnover",
        "risk_free_return",
    ]

    for col in numeric:
        merged[col] = pd.to_numeric(
            merged[col], errors="coerce"
        )

    if not np.isfinite(merged[numeric].to_numpy()).all():
        raise ValueError("Missing or non-finite return data")

    if (merged["net_return"] <= -1).any():
        raise ValueError("Invalid net return <= -100%")

    return merged


def period_data(
    data: pd.DataFrame,
    start: str,
    end: str,
) -> pd.DataFrame:
    return data[
        data["signal_date"].between(start, end)
    ].copy()


def evaluate(
    data: pd.DataFrame,
    period_name: str,
    expected_weeks: int,
) -> pd.DataFrame:
    rows = []
    calendar = None

    for strategy in CANDIDATES:
        group = data[
            data["strategy"].eq(strategy)
        ].sort_values("signal_date")

        if len(group) != expected_weeks:
            raise ValueError(
                f"{period_name}: {strategy}: "
                f"expected {expected_weeks}, got {len(group)}"
            )

        dates = pd.DatetimeIndex(group["signal_date"])

        if calendar is None:
            calendar = dates
        elif not dates.equals(calendar):
            raise ValueError(
                f"Calendar mismatch: {strategy}"
            )

        returns = pd.Series(
            group["net_return"].to_numpy(dtype=float),
            index=dates,
        )

        rf = pd.Series(
            group["risk_free_return"].to_numpy(dtype=float),
            index=dates,
        )

        perf = mt.performance(returns, rf)

        rows.append({
            "strategy": strategy,
            "period": period_name,
            "n_weeks": len(group),
            "cumulative_return": float(
                np.prod(1.0 + returns.to_numpy()) - 1.0
            ),
            "cagr": perf["ann_return"],
            "volatility": perf["ann_vol"],
            "sharpe": perf["sharpe"],
            "max_drawdown": perf["max_drawdown"],
            "calmar": perf["calmar"],
            "sortino": perf["sortino"],
            "annual_turnover": float(
                group["turnover"].mean()
                * mt.WEEKS_PER_YEAR
            ),
        })

    return pd.DataFrame(rows)


def run_trainer(
    paths_file: Path,
    rf_file: Path,
    output_dir: Path,
    config: TrainerConfig = TrainerConfig(),
):
    paths_file = Path(paths_file).resolve()
    rf_file = Path(rf_file).resolve()
    output_dir = Path(output_dir).resolve()

    if not (
        config.train_start <= config.train_end
        < config.validation_start <= config.validation_end
    ):
        raise ValueError("Training/validation periods overlap")

    if config.selection_metric not in {
        "sharpe", "cagr", "calmar",
    }:
        raise ValueError("Unsupported selection metric")

    data = load_inputs(paths_file, rf_file)

    train = period_data(
        data, config.train_start, config.train_end
    )

    validation = period_data(
        data,
        config.validation_start,
        config.validation_end,
    )

    train_results = evaluate(
        train,
        "Training",
        config.expected_train_weeks,
    )

    # Selection depends exclusively on training results.
    ranked = train_results.sort_values(
        [config.selection_metric, "strategy"],
        ascending=[False, True],
        na_position="last",
    ).reset_index(drop=True)

    if not np.isfinite(
        ranked[config.selection_metric].to_numpy()
    ).all():
        raise ValueError("Non-finite training selection metric")

    ranked["train_rank"] = np.arange(1, len(ranked) + 1)

    selected = str(ranked.iloc[0]["strategy"])

    # Validation is evaluated only after selection is frozen.
    validation_results = evaluate(
        validation,
        "Validation",
        config.expected_validation_weeks,
    )

    chosen_validation = validation_results.loc[
        validation_results["strategy"].eq(selected)
    ].copy()

    if len(chosen_validation) != 1:
        raise ValueError("Selected model missing in validation")

    frozen = {
        "selected_strategy": selected,
        "factor_selection": {
            "lookback_years": 3,
            "top_k": 3,
            "min_coverage": 0.65,
        },
        "factor_weighting": {
            "method": selected.rsplit("_", 1)[-1],
            "risk_window_weeks": 52,
            "min_weight": 0.05,
            "max_weight": 0.80,
            "covariance_shrinkage": 0.10,
            "rebalance_frequency": "annual",
        },
        "portfolio": {
            "select_fraction": 0.20,
            "exit_fraction": 0.30,
            "sector_cap": 0.30,
            "stock_weighting": "equal",
            "rebalance_frequency": "weekly",
            "cost_oneway": 0.001,
            "availability_mode": "common_all",
        },
        "model_selection": asdict(config),
        "training_selection_score": float(
            ranked.iloc[0][config.selection_metric]
        ),
        "source_files": {
            "weekly_paths_sha256": file_sha256(paths_file),
            "risk_free_sha256": file_sha256(rf_file),
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)

    ranked.to_csv(
        output_dir / "training_model_rankings.csv",
        index=False,
    )

    validation_results.to_csv(
        output_dir / "validation_all_candidates.csv",
        index=False,
    )

    chosen_validation.to_csv(
        output_dir / "selected_model_validation.csv",
        index=False,
    )

    with (output_dir / "frozen_model.json").open(
        "w", encoding="utf-8"
    ) as f:
        json.dump(frozen, f, indent=2)

    print("\n=== Training Model Rankings ===")
    print(
        ranked[
            [
                "train_rank",
                "strategy",
                "cagr",
                "sharpe",
                "max_drawdown",
                "calmar",
            ]
        ].round(6).to_string(index=False)
    )

    print("\n=== Selected Model ===")
    print(selected)

    print("\n=== Validation Performance ===")
    print(
        chosen_validation.round(6).to_string(index=False)
    )

    print("\n=== Frozen Model ===")
    print(json.dumps(frozen, indent=2))

    print("\nResults saved to:", output_dir)

    return ranked, chosen_validation, frozen
