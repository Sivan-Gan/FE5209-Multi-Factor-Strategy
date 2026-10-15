
"""Build execution-aligned 2026 S&P 500 benchmark.

Source:
Compustat S&P 500 Comp-Ltd Total Return

Convention:
(execution_date, next_execution_date]
"""

from pathlib import Path

import numpy as np
import pandas as pd


def build_weekly_benchmark(
    daily_file: Path,
    weekly_returns_file: Path,
    output_file: Path,
):
    daily = pd.read_parquet(daily_file)
    weekly = pd.read_csv(weekly_returns_file)

    required_daily = {
        "date",
        "benchmark_return",
        "return_available",
    }

    required_weekly = {
        "signal_date",
        "execution_date",
        "next_execution_date",
        "path_complete",
    }

    if not required_daily.issubset(daily.columns):
        raise ValueError("Missing daily benchmark columns")

    if not required_weekly.issubset(weekly.columns):
        raise ValueError("Missing weekly calendar columns")

    daily["date"] = pd.to_datetime(daily["date"])

    for col in [
        "signal_date",
        "execution_date",
        "next_execution_date",
    ]:
        weekly[col] = pd.to_datetime(
            weekly[col]
        )

    daily["benchmark_return"] = pd.to_numeric(
        daily["benchmark_return"],
        errors="coerce",
    )

    if daily["date"].duplicated().any():
        raise ValueError("Duplicate benchmark dates")

    if not daily["date"].is_monotonic_increasing:
        daily = daily.sort_values("date")

    if (
        not daily["return_available"].eq(1).all()
        or not np.isfinite(
            daily["benchmark_return"]
        ).all()
    ):
        raise ValueError("Invalid daily benchmark returns")

    if (daily["benchmark_return"] < -1).any():
        raise ValueError("Benchmark return below -100%")

    calendar = weekly[
        [
            "signal_date",
            "execution_date",
            "next_execution_date",
            "path_complete",
        ]
    ].drop_duplicates()

    calendar = calendar[
        calendar["path_complete"].eq(1)
    ].sort_values("signal_date")

    if calendar["signal_date"].duplicated().any():
        raise ValueError("Duplicate signal calendar dates")

    rows = []

    for row in calendar.itertuples(index=False):
        sub = daily[
            daily["date"].gt(row.execution_date)
            & daily["date"].le(row.next_execution_date)
        ]

        if sub.empty:
            raise ValueError(
                f"No benchmark observations: {row.signal_date}"
            )

        if sub["date"].max() != row.next_execution_date:
            raise ValueError(
                f"Missing endpoint: {row.next_execution_date}"
            )

        benchmark_return = float(
            np.prod(
                1.0 + sub["benchmark_return"].to_numpy()
            ) - 1.0
        )

        rows.append({
            "signal_date": row.signal_date,
            "execution_date": row.execution_date,
            "next_execution_date": row.next_execution_date,
            "benchmark_total_return": benchmark_return,
            "n_daily_observations": len(sub),
        })

    result = pd.DataFrame(rows)

    if len(result) != 39:
        raise ValueError(
            f"Expected 39 completed weeks, got {len(result)}"
        )

    if not np.isfinite(
        result["benchmark_total_return"]
    ).all():
        raise ValueError("Invalid weekly benchmark")

    output_file.parent.mkdir(
        parents=True, exist_ok=True
    )

    result.to_csv(output_file, index=False)

    print("\n=== 2026 Weekly Benchmark ===")
    print("Weeks:", len(result))
    print("First signal:", result["signal_date"].min())
    print("Last signal:", result["signal_date"].max())

    cumulative = float(
        np.prod(
            1.0 + result["benchmark_total_return"]
        ) - 1.0
    )

    print("Cumulative return:", round(cumulative, 6))
    print("\nLast 5 weeks:")
    print(result.tail().to_string(index=False))
    print("\nSaved:", output_file)

    return result
