
"""Fair 2021-2025 benchmark comparison.

Both Adaptive Top-3 IV and Fixed Top-3 IV start
from cash at the beginning of the validation period.

Historical factor information is retained.
Historical stock positions are not inherited.
"""

from pathlib import Path

import numpy as np
import pandas as pd

from src.adaptive_strategy.pipeline import (
    PipelineConfig,
    load_common_scores,
    score_all_weeks,
)
from src.strategy import portfolio as pf
from src.strategy import metrics as mt
from src.strategy import run_gate_b as rg


START = "2021-01-01"
END = "2025-12-31"
EXPECTED_WEEKS = 259
COST_ONEWAY = 0.001


def run_adaptive_validation(
    data_root: Path,
    annual_file: Path,
    weekly_weights_file: Path,
    output_dir: Path,
):
    """Rebuild Adaptive Top-3 IV from cash in 2021."""

    data_root = Path(data_root).resolve()
    output_dir = Path(output_dir).resolve()

    config = PipelineConfig(
        start=START,
        end=END,
        cost_oneway=COST_ONEWAY,
        use_buffer=True,
        expected_weeks=EXPECTED_WEEKS,
    )

    base = load_common_scores(
        data_root,
        config,
    )

    annual = pd.read_csv(annual_file)
    weekly_weights = pd.read_csv(
        weekly_weights_file
    )

    weekly_weights["signal_date"] = pd.to_datetime(
        weekly_weights["signal_date"]
    )

    annual = annual[
        annual["year"].between(2021, 2025)
        & annual["method"].eq("IV")
    ].copy()

    weekly_weights = weekly_weights[
        weekly_weights["signal_date"].between(
            START, END
        )
        & weekly_weights["method"].eq("IV")
    ].copy()

    if len(annual) != 5:
        raise ValueError("Expected 5 annual IV decisions")

    if len(weekly_weights) != EXPECTED_WEEKS:
        raise ValueError("Expected 259 weekly weights")

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

    # New call starts portfolio buffer from empty holdings.
    holdings, construction = pf.build_holdings(
        scored,
        use_buffer=True,
    )

    returns = rg.load_returns(
        data_root,
        START,
        END,
    )

    returns["next_execution_date"] = (
        pd.to_datetime(
            returns["next_execution_date"]
        ).dt.strftime("%Y-%m-%d")
    )

    # New call starts return accounting from 100% cash.
    path, ledger = mt.weekly_path_and_ledger(
        holdings,
        returns,
        END,
        COST_ONEWAY,
    )

    path["net_return"] = mt.net_returns(
        path,
        COST_ONEWAY,
    )

    if len(path) != EXPECTED_WEEKS:
        raise ValueError(
            f"Expected {EXPECTED_WEEKS} weeks, got {len(path)}"
        )

    for frame in (
        path, holdings, construction, ledger
    ):
        frame["strategy"] = "Adaptive_Top3_IV_Reset2021"

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.to_csv(
        output_dir / "adaptive_weekly_paths.csv",
        index=False,
    )

    holdings.to_csv(
        output_dir / "adaptive_holdings.csv",
        index=False,
    )

    construction.to_csv(
        output_dir / "adaptive_construction.csv",
        index=False,
    )

    ledger.to_csv(
        output_dir / "adaptive_trade_ledger.csv",
        index=False,
    )

    print("\n=== Adaptive Validation Reset ===")
    print("Weeks:", len(path))
    print("First signal:", path["signal_date"].iloc[0])
    print("Initial GTR:", path["gtr"].iloc[0])

    return path


def compare_with_fixed(
    adaptive_path: pd.DataFrame,
    fixed_file: Path,
):
    fixed = pd.read_csv(fixed_file)

    adaptive = adaptive_path.copy()

    adaptive["signal_date"] = pd.to_datetime(
        adaptive["signal_date"]
    )

    fixed["signal_date"] = pd.to_datetime(
        fixed["signal_date"]
    )

    merged = adaptive.merge(
        fixed,
        on="signal_date",
        suffixes=("_adaptive", "_fixed"),
        validate="one_to_one",
    )

    if len(merged) != EXPECTED_WEEKS:
        raise ValueError("Benchmark calendar mismatch")

    first_year = merged[
        merged["signal_date"].dt.year.eq(2021)
    ]

    print("\n=== 2021 Initial-Year Reproduction ===")

    for metric in [
        "gross_return",
        "gtr",
        "net_return",
    ]:
        error = float(
            (
                first_year[f"{metric}_adaptive"]
                - first_year[f"{metric}_fixed"]
            ).abs().max()
        )

        print(metric, "max error:", error)

        if error > 1e-10:
            raise AssertionError(
                f"2021 mismatch: {metric}"
            )

    print("\n2021 REPRODUCTION PASSED")

    rows = []

    for name, col in [
        ("Adaptive_Top3_IV", "net_return_adaptive"),
        ("Fixed_Top3_IV", "net_return_fixed"),
    ]:
        r = merged[col].to_numpy(dtype=float)

        nav = np.r_[1.0, np.cumprod(1 + r)]

        cumulative = float(nav[-1] - 1)
        cagr = float(nav[-1] ** (52 / len(r)) - 1)
        vol = float(np.std(r, ddof=1) * np.sqrt(52))
        mdd = float(
            1 - np.min(nav / np.maximum.accumulate(nav))
        )

        rows.append({
            "strategy": name,
            "n_weeks": len(r),
            "cumulative_return": cumulative,
            "cagr": cagr,
            "volatility": vol,
            "max_drawdown": mdd,
        })

    summary = pd.DataFrame(rows)

    print("\n=== Fair Benchmark Comparison ===")
    print(summary.round(6).to_string(index=False))

    return summary
