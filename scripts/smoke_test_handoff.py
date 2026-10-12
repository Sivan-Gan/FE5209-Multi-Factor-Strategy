#!/usr/bin/env python3
"""Read-only receiver smoke test for the frozen Final Factor Handoff.

This script deliberately validates only registry, contracts, deterministic sample,
and (when present) the already-exported local interface. It never computes
returns, portfolios, FMOM, weights, or research scores.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


EXPECTED = (
    "QUALITY_CURRENT",
    "MOMENTUM_12_1",
    "LOWRISK_CURRENT",
    "INVESTMENT_CURRENT",
)
SCORE_COLUMNS = ("quality_score", "momentum_score", "lowrisk_score", "investment_score")


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_registry(root: Path) -> None:
    rows = read_csv(root / "handoff/final_factor/final_factor_registry.csv")
    rows.sort(key=lambda row: int(row["final_factor_order"]))
    ids = tuple(row["specification_id"] for row in rows)
    require(len(rows) == 4, "Final factor registry must have K=4 rows")
    require(ids == EXPECTED, f"Unexpected final factor order: {ids}")
    active = set(ids)
    require(not active & {"VALUE_CURRENT", "DIVIDEND_CURRENT", "SIZE_CURRENT", "MOMENTUM_CURRENT"}, "Inactive factor is active")


def validate_sample(root: Path) -> int:
    sample = root / "handoff/final_factor/data/factor_scores_weekly_sample.csv"
    rows = read_csv(sample)
    require(bool(rows), "Tracked handoff sample is empty")
    fields = set(rows[0])
    require({"signal_date", "PERMNO", *SCORE_COLUMNS}.issubset(fields), "Sample schema is incomplete")
    return len(rows)


def validate_full_interface(root: Path) -> str:
    data = root / "handoff/final_factor/data"
    scores = data / "factor_scores_weekly.parquet"
    availability = data / "factor_availability_weekly.parquet"
    universe = data / "universe_weekly.parquet"
    paths = (scores, availability, universe)
    if not any(path.exists() for path in paths):
        return "FULL_PARQUET=NOT_PRESENT_EXTERNAL_DATA_REQUIRED"
    require(all(path.exists() for path in paths), "Full handoff interface is incomplete")
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - receiver environment message
        raise ValueError("pyarrow is required to validate present full Parquet files") from exc

    score_file = pq.ParquetFile(scores)
    columns = set(score_file.schema.names)
    require({"signal_date", "PERMNO", *SCORE_COLUMNS}.issubset(columns), "Full score schema is incomplete")
    require(score_file.metadata.num_rows == 523_909, "Unexpected score row count")
    availability_columns = set(pq.ParquetFile(availability).schema.names)
    universe_columns = set(pq.ParquetFile(universe).schema.names)
    require({"signal_date", "PERMNO", "all_four_available"}.issubset(availability_columns), "Availability schema is incomplete")
    require({"signal_date", "PERMNO", "execution_date"}.issubset(universe_columns), "Universe schema is incomplete")
    return "FULL_PARQUET=VALIDATED"


def run(root: Path) -> str:
    require((root / "README.md").is_file(), "Repository README is missing")
    require((root / "handoff/final_factor/FINAL_FACTOR_HANDOFF_CONTRACT.md").is_file(), "Handoff contract is missing")
    validate_registry(root)
    sample_rows = validate_sample(root)
    full_status = validate_full_interface(root)
    return f"SMOKE_TEST_HANDOFF PASS K=4 SAMPLE_ROWS={sample_rows} {full_status}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=project_root(), help="Repository root (defaults to this script's parent)")
    args = parser.parse_args()
    try:
        print(run(args.root.resolve()))
    except (OSError, ValueError, csv.Error) as exc:
        print(f"SMOKE_TEST_HANDOFF FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
