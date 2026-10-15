
"""Adaptive Strategy V1: historical end-to-end pipeline.

Annual factor selection, annual factor weighting,
weekly stock selection and execution-aligned accounting.

Reproduce the frozen 2016-2025 historical experiment.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from src.adaptive_strategy.factor_selection import (
    SelectionConfig, select_factors,
)
from src.adaptive_strategy.factor_weighting import (
    WeightingConfig, calculate_selected_weights,
)
from src.adaptive_strategy.composite_score import (
    ScoreConfig, calculate_scores, DEFAULT_FACTORS,
)
from src.strategy import portfolio as pf
from src.strategy import metrics as mt
from src.strategy import run_gate_b as rg


@dataclass(frozen=True)
class PipelineConfig:
    start: str = "2016-01-01"
    end: str = "2025-12-31"
    cost_oneway: float = 0.001
    use_buffer: bool = True
    expected_weeks: int = 520


def load_common_scores(data_root: Path, config: PipelineConfig):
    """Historical seven-factor common-availability universe."""

    archive = data_root / "handoff/factor_research_archive/data"
    keys = ["signal_date", "PERMNO"]
    factor_cols = list(DEFAULT_FACTORS.values())
    avail_cols = [f"{c}__available" for c in factor_cols]

    scores = pd.read_parquet(
        archive / "all_factor_scores_weekly.parquet",
        columns=keys + factor_cols,
    )
    availability = pd.read_parquet(
        archive / "all_factor_availability_weekly.parquet",
        columns=keys + avail_cols,
    )
    metadata = pd.read_parquet(
        data_root / "handoff/final_factor/data/factor_scores_weekly.parquet",
        columns=keys + [
            "execution_date",
            "historical_gics_sector",
            "eligible_universe_flag",
        ],
    )

    for frame in (scores, availability, metadata):
        frame["signal_date"] = pd.to_datetime(frame["signal_date"])
        frame["PERMNO"] = frame["PERMNO"].astype(str)

        if frame.duplicated(keys).any():
            raise ValueError("Duplicate score keys")

    base = (
        scores.merge(
            availability, on=keys,
            how="inner", validate="one_to_one",
        )
        .merge(
            metadata, on=keys,
            how="inner", validate="one_to_one",
        )
    )

    base = base[
        base["signal_date"].between(config.start, config.end)
        & base["eligible_universe_flag"].eq(1)
    ].copy()

    # Historical common-all eligibility.
    valid = np.ones(len(base), dtype=bool)

    for col in factor_cols:
        valid &= (
            base[f"{col}__available"].eq(1).to_numpy()
            & np.isfinite(
                pd.to_numeric(base[col], errors="coerce")
            ).to_numpy()
        )

    return base.loc[valid].copy()


def load_factor_history(data_root: Path):
    path = data_root / (
        "data/adaptive_strategy/"
        "common_factor_returns.csv"
    )

    history = pd.read_csv(path)

    for col in ["signal_date", "next_execution_date"]:
        history[col] = pd.to_datetime(history[col])

    if history.duplicated(["signal_date", "family"]).any():
        raise ValueError("Duplicate factor return keys")

    return history


def build_annual_decisions(
    history: pd.DataFrame,
    dates: list,
):
    """Select factors and freeze weights once per year."""

    families = list(DEFAULT_FACTORS)
    calendar = pd.to_datetime(pd.Series(dates))

    first_dates = (
        calendar.groupby(calendar.dt.year)
        .min()
        .sort_index()
    )

    decisions = []
    weekly_weights = []

    for year, cutoff in first_dates.items():
        selected, stats = select_factors(
            history,
            cutoff,
            families,
            SelectionConfig(
                lookback_years=3,
                top_k=3,
                min_coverage=0.65,
            ),
        )

        methods, risk_window = calculate_selected_weights(
            history,
            cutoff,
            selected,
            WeightingConfig(),
            covariance_families=families,
        )

        year_dates = calendar[calendar.dt.year == year]

        for method, weights in methods.items():
            decisions.append({
                "year": int(year),
                "cutoff": cutoff,
                "method": method,
                "selected_factors": ", ".join(selected),
                "risk_window_weeks": len(risk_window),
                **{
                    f"w_{f.replace(' ', '')}":
                    weights.get(f, 0.0)
                    for f in families
                },
            })

            for date in year_dates:
                weekly_weights.append({
                    "signal_date": date,
                    "method": method,
                    **{
                        f"w_{f.replace(' ', '')}":
                        weights.get(f, 0.0)
                        for f in families
                    },
                })

    return (
        pd.DataFrame(decisions),
        pd.DataFrame(weekly_weights),
    )


def score_all_weeks(base, annual, weekly_weights, method):
    """Apply frozen annual weights to weekly stock scores."""

    families = list(DEFAULT_FACTORS)
    outputs = []

    decisions = annual[annual["method"] == method]

    for decision in decisions.itertuples(index=False):
        year = int(decision.year)

        selected = [
            f.strip()
            for f in decision.selected_factors.split(",")
        ]

        weights = {
            f: float(
                getattr(decision, f"w_{f.replace(' ', '')}")
            )
            for f in selected
        }

        dates = weekly_weights.loc[
            (weekly_weights["method"] == method)
            & (weekly_weights["signal_date"].dt.year == year),
            "signal_date",
        ]

        subset = base[base["signal_date"].isin(dates)]

        scored = calculate_scores(
            subset,
            selected,
            weights,
            ScoreConfig(availability_mode="common_all"),
        )

        outputs.append(scored)

    result = pd.concat(outputs, ignore_index=True)

    if result.duplicated(["signal_date", "PERMNO"]).any():
        raise ValueError("Duplicate scored securities")

    return result


def audit_reference(paths, reference_path):
    """Strict rowwise reproduction of the historical experiment."""

    reference = pd.read_csv(reference_path)
    reference["signal_date"] = pd.to_datetime(
        reference["signal_date"]
    )

    required = [
        "gross_return",
        "gtr",
        "net_return",
    ]

    rows = []

    for method in ["EW", "IV", "MV"]:
        name = f"WF_3Y_Top3_{method}"

        old = reference[reference["strategy"] == name].copy()
        new = paths[paths["strategy"] == name].copy()

        if old.empty or new.empty:
            raise ValueError(f"Missing strategy: {name}")

        if old["signal_date"].duplicated().any():
            raise ValueError("Duplicate reference dates")

        if new["signal_date"].duplicated().any():
            raise ValueError("Duplicate new dates")

        if set(old["signal_date"]) != set(new["signal_date"]):
            raise ValueError(f"Calendar mismatch: {name}")

        merged = old.merge(
            new,
            on="signal_date",
            suffixes=("_old", "_new"),
            validate="one_to_one",
        )

        for col in required:
            error = (
                merged[f"{col}_old"]
                - merged[f"{col}_new"]
            ).abs().max()

            rows.append({
                "strategy": name,
                "metric": col,
                "max_abs_error": float(error),
                "passed": bool(error <= 1e-10),
            })

    audit = pd.DataFrame(rows)

    if not audit["passed"].all():
        print(audit.to_string(index=False))
        raise AssertionError("Historical reproduction failed")

    return audit


def run_pipeline(
    data_root: Path,
    output_dir: Path,
    config: PipelineConfig = PipelineConfig(),
    reference_path: Path | None = None,
):
    """Execute the complete historical adaptive strategy."""

    data_root = Path(data_root).resolve()
    output_dir = Path(output_dir).resolve()

    base = load_common_scores(data_root, config)
    history = load_factor_history(data_root)

    returns = rg.load_returns(
        data_root, config.start, config.end
    )

    returns["next_execution_date"] = pd.to_datetime(
        returns["next_execution_date"],
        errors="coerce",
    )

    completed = set(pd.to_datetime(
        returns.loc[
            returns["next_execution_date"].notna()
            & returns["next_execution_date"].le(
                pd.Timestamp(config.end)
            ),
            "signal_date",
        ]
    ))

    base = base[base["signal_date"].isin(completed)].copy()
    dates = sorted(base["signal_date"].unique())

    if len(dates) != config.expected_weeks:
        raise ValueError(
            f"Expected {config.expected_weeks} weeks, "
            f"got {len(dates)}"
        )

    # Original engine expects string dates.
    returns["next_execution_date"] = (
        returns["next_execution_date"].dt.strftime("%Y-%m-%d")
    )

    annual, weekly_weights = build_annual_decisions(
        history, dates
    )

    all_paths = []
    all_holdings = []
    all_construction = []
    all_ledgers = []

    for method in ["EW", "IV", "MV"]:
        print(f"\n=== Running Adaptive Top-3 {method} ===")

        scored = score_all_weeks(
            base, annual, weekly_weights, method
        )

        scored["signal_date"] = (
            pd.to_datetime(scored["signal_date"])
            .dt.strftime("%Y-%m-%d")
        )

        holdings, construction = pf.build_holdings(
            scored, use_buffer=config.use_buffer
        )

        # Check construction constraints without the
        # original Gate-B-only date restriction.
        if holdings.duplicated(["signal_date", "PERMNO"]).any():
            raise ValueError("Duplicate holdings")

        if (holdings["weight"] < 0).any():
            raise ValueError("Negative stock weight")

        if (construction["max_sector_weight"] > 0.30 + 1e-12).any():
            raise ValueError("Sector cap violation")

        if not np.allclose(
            construction["cash_weight"].to_numpy()
            + holdings.groupby("signal_date")["weight"]
            .sum()
            .reindex(construction["signal_date"])
            .fillna(0).to_numpy(),
            1.0,
            atol=1e-12,
        ):
            raise ValueError("Holdings plus cash mismatch")

        path, ledger = mt.weekly_path_and_ledger(
            holdings,
            returns,
            config.end,
            config.cost_oneway,
        )

        path["net_return"] = mt.net_returns(
            path, config.cost_oneway
        )

        if len(path) != config.expected_weeks:
            raise ValueError(
                f"Incorrect backtest length: {method}"
            )

        name = f"WF_3Y_Top3_{method}"

        for frame in (path, holdings, construction, ledger):
            frame["strategy"] = name

        all_paths.append(path)
        all_holdings.append(holdings)
        all_construction.append(construction)
        all_ledgers.append(ledger)

    paths = pd.concat(all_paths, ignore_index=True)
    holdings = pd.concat(all_holdings, ignore_index=True)
    construction = pd.concat(all_construction, ignore_index=True)
    ledgers = pd.concat(all_ledgers, ignore_index=True)

    paths["signal_date"] = pd.to_datetime(
        paths["signal_date"]
    )

    # Audit before writing any output.
    audit = None
    if reference_path is not None:
        audit = audit_reference(paths, reference_path)

    output_dir.mkdir(parents=True, exist_ok=True)

    annual.to_csv(
        output_dir / "annual_factor_decisions.csv",
        index=False,
    )
    weekly_weights.to_csv(
        output_dir / "weekly_factor_weights.csv",
        index=False,
    )
    holdings.to_csv(
        output_dir / "weekly_holdings.csv",
        index=False,
    )
    construction.to_csv(
        output_dir / "construction_summary.csv",
        index=False,
    )
    ledgers.to_csv(
        output_dir / "trade_ledger.csv",
        index=False,
    )
    paths.to_csv(
        output_dir / "weekly_paths.csv",
        index=False,
    )

    if audit is not None:
        audit.to_csv(
            output_dir / "reproduction_audit.csv",
            index=False,
        )

    print("\n=== Completed Historical Pipeline ===")
    print("Strategies:", paths["strategy"].nunique())
    print("Weeks per strategy:", config.expected_weeks)

    if audit is not None:
        print("\n=== Reproduction Audit ===")
        print(audit.to_string(index=False))
        print("\nFULL HISTORICAL REPRODUCTION PASSED")

    return paths, annual, audit
