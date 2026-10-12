#!/usr/bin/env python3
"""Read-only receiver smoke test for the three governed derived-data interfaces.

It validates only schemas, frozen counts, and required files. It deliberately
does not calculate IC, returns, factor returns, FMOM, portfolios, or strategy
performance.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.handoff.load_factor_research_archive import ARCHIVE_IDS, FINAL_IDS


DOWNSTREAM_REQUIRED = (
    "weekly_execution_calendar.parquet", "security_weekly_execution_returns.parquet",
    "execution_liquidity_weekly.parquet", "security_daily_market.parquet",
    "benchmark_daily_returns.parquet", "benchmark_weekly_execution_returns.parquet",
    "risk_free_daily.parquet", "risk_free_weekly.parquet", "ff_factors_daily.parquet",
    "ff_factors_monthly.parquet", "security_reference_history.parquet",
)


def root_path() -> Path:
    return ROOT


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise ValueError(detail)


def run(root: Path) -> str:
    final_registry = rows(root / "handoff/final_factor/final_factor_registry.csv")
    final_registry.sort(key=lambda row: int(row["final_factor_order"]))
    require(tuple(row["specification_id"] for row in final_registry) == FINAL_IDS, "Final Factor registry is not exact K=4")
    final_scores = pq.ParquetFile(root / "handoff/final_factor/data/factor_scores_weekly.parquet")
    require({"quality_score", "momentum_score", "lowrisk_score", "investment_score"}.issubset(final_scores.schema_arrow.names), "Final Factor schema is incomplete")
    require(not (set(ARCHIVE_IDS) & set(final_scores.schema_arrow.names)), "Final Factor schema contains research-archive specification fields")

    archive_registry = rows(root / "handoff/factor_research_archive/all_factor_spec_registry.csv")
    require(len(archive_registry) == 20, "Formal archive registry count is not 20")
    require(tuple(row["specification_id"] for row in archive_registry) == ARCHIVE_IDS, "Formal archive IDs are not exact")
    archive_scores = pq.ParquetFile(root / "handoff/factor_research_archive/data/all_factor_scores_weekly.parquet")
    require({"signal_date", "PERMNO", *ARCHIVE_IDS}.issubset(archive_scores.schema_arrow.names), "Archive score schema is incomplete")
    require(not any(token in name.lower() for name in archive_scores.schema_arrow.names for token in ("future_return", "factor_return", "fmom", "strategy_weight", "portfolio")), "Archive score schema contains an outcome/strategy field")

    downstream = root / "handoff/us_downstream/data"
    missing = [name for name in DOWNSTREAM_REQUIRED if not (downstream / name).is_file()]
    require(not missing, f"US downstream interface is missing: {','.join(missing)}")
    for name in DOWNSTREAM_REQUIRED:
        pq.ParquetFile(downstream / name)
    return "\n".join((
        "STRATEGY_ACTIVE_FACTORS=4",
        "FORMAL_ARCHIVED_FACTOR_SPECS=20",
        "DOWNSTREAM_US_DATA=READY",
        "SMOKE_TEST_COMPLETE_DELIVERY PASS",
    ))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=root_path())
    args = parser.parse_args()
    try:
        print(run(args.root.resolve()))
    except (OSError, ValueError, csv.Error) as exc:
        print(f"SMOKE_TEST_COMPLETE_DELIVERY FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
