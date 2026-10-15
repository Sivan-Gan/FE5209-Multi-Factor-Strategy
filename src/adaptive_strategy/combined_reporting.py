
"""Combine historical and 2026 adaptive strategy returns.

Historical: 2021-2025 CRSP-based execution paths.
Extension: 2026 mixed-source execution paths.

The data-source boundary is explicitly retained.
"""

from pathlib import Path
import json

import numpy as np
import pandas as pd


def load_combined_returns(
    historical_file: Path,
    extension_file: Path,
    historical_market_file: Path,
    extension_market_file: Path,
    frozen_file: Path,
):
    with open(frozen_file, encoding="utf-8") as f:
        frozen = json.load(f)

    selected = frozen["selected_strategy"]
    method = selected.rsplit("_", 1)[-1]

    historical = pd.read_csv(historical_file)
    extension = pd.read_csv(extension_file)

    historical = historical[
        historical["strategy"].eq(selected)
    ].copy()

    extension_name = f"2026_Top3_{method}"

    extension = extension[
        extension["strategy"].eq(extension_name)
    ].copy()

    if historical.empty or extension.empty:
        raise ValueError("Missing selected strategy paths")

    historical["signal_date"] = pd.to_datetime(
        historical["signal_date"]
    )

    extension["signal_date"] = pd.to_datetime(
        extension["signal_date"]
    )

    historical = historical[
        historical["signal_date"].between(
            "2021-01-01", "2025-12-31"
        )
    ].copy()

    extension = extension[
        extension["signal_date"].dt.year.eq(2026)
    ].copy()

    historical_market = pd.read_parquet(
        historical_market_file
    )

    extension_market = pd.read_csv(
        extension_market_file
    )

    historical_market["signal_date"] = pd.to_datetime(
        historical_market["signal_date"]
    )

    extension_market["signal_date"] = pd.to_datetime(
        extension_market["signal_date"]
    )

    historical_market = historical_market[
        historical_market["signal_date"].between(
            "2021-01-01", "2025-12-31"
        )
        & historical_market["path_complete"].eq(1)
    ].copy()

    extension_market = extension_market[
        extension_market["signal_date"].dt.year.eq(2026)
    ].copy()

    historical_market = historical_market[
        ["signal_date", "benchmark_total_return"]
    ]

    extension_market = extension_market[
        ["signal_date", "benchmark_total_return"]
    ]

    historical = historical.merge(
        historical_market,
        on="signal_date",
        how="left",
        validate="one_to_one",
    )

    extension = extension.merge(
        extension_market,
        on="signal_date",
        how="left",
        validate="one_to_one",
    )

    historical["data_source"] = "CRSP_HISTORICAL"
    extension["data_source"] = "2026_MIXED_SOURCE"

    keep = [
        "signal_date",
        "period_end",
        "net_return",
        "benchmark_total_return",
        "data_source",
    ]

    combined = pd.concat(
        [
            historical[keep],
            extension[keep],
        ],
        ignore_index=True,
    ).sort_values("signal_date")

    combined["signal_date"] = pd.to_datetime(
        combined["signal_date"]
    )

    combined["period_end"] = pd.to_datetime(
        combined["period_end"]
    )

    if combined["signal_date"].duplicated().any():
        raise ValueError("Duplicate combined signal dates")

    if not np.isfinite(
        combined[
            ["net_return", "benchmark_total_return"]
        ].to_numpy(dtype=float)
    ).all():
        raise ValueError("Invalid combined returns")

    historical_count = len(
        combined[
            combined["data_source"].eq("CRSP_HISTORICAL")
        ]
    )

    extension_count = len(
        combined[
            combined["data_source"].eq("2026_MIXED_SOURCE")
        ]
    )

    if historical_count != 259:
        raise ValueError(
            f"Expected 259 historical weeks, got {historical_count}"
        )

    if extension_count != 39:
        raise ValueError(
            f"Expected 39 extension weeks, got {extension_count}"
        )

    # Ensure holding periods do not overlap.
    combined = combined.reset_index(drop=True)

    gaps = (
        combined["signal_date"].iloc[1:].to_numpy()
        - combined["period_end"].iloc[:-1].to_numpy()
    )

    print("\n=== Combined Calendar Audit ===")
    print("Historical weeks:", historical_count)
    print("2026 weeks:", extension_count)
    print("Total weeks:", len(combined))

    print("\n=== Boundary ===")
    print(
        combined.iloc[
            max(0, historical_count - 2):
            historical_count + 2
        ][
            ["signal_date", "period_end", "data_source"]
        ].to_string(index=False)
    )

    print("\n=== Period Endpoint Check ===")
    print(
        "Signal dates preceding previous endpoint:",
        int((gaps < np.timedelta64(0, "D")).sum())
    )

    return combined


def save_combined(combined, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    combined.to_csv(
        output_dir / "combined_weekly_returns.csv",
        index=False,
    )

    nav = combined[
        ["signal_date", "data_source"]
    ].copy()

    nav["strategy_nav"] = (
        1.0 + combined["net_return"]
    ).cumprod()

    nav["benchmark_nav"] = (
        1.0 + combined["benchmark_total_return"]
    ).cumprod()

    nav.to_csv(
        output_dir / "combined_nav.csv",
        index=False,
    )

    print("\n=== Combined Performance ===")

    print(
        "Strategy cumulative return:",
        round(nav["strategy_nav"].iloc[-1] - 1, 6)
    )

    print(
        "Benchmark cumulative return:",
        round(nav["benchmark_nav"].iloc[-1] - 1, 6)
    )

    print("\nSaved:", output_dir)

    return nav
