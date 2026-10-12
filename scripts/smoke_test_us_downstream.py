#!/usr/bin/env python3
"""Verify the tracked US downstream handoff contract without local Parquet data.

This intentionally performs no factor, return, strategy, or OOS computation.
"""
from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "handoff/us_downstream"


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    required = (
        "README.md", "US_DOWNSTREAM_DATA_CONTRACT.md", "US_DOWNSTREAM_DATA_REQUIREMENT_MATRIX.csv",
        "downstream_data_dictionary.csv", "downstream_source_manifest.csv", "downstream_partition_manifest.csv",
        "downstream_access_contract.csv", "downstream_non_data_decisions.csv", "US_DOWNSTREAM_CAPABILITY_MATRIX.csv",
        "EXTERNAL_DOWNSTREAM_DATA_TRANSFER_MANIFEST.csv", "downstream_acceptance.csv",
    )
    missing = [name for name in required if not (PACKAGE / name).is_file()]
    if missing:
        print(f"DOWNSTREAM_CONTRACT=FAIL missing={','.join(missing)}")
        return 1
    acceptance = rows(PACKAGE / "downstream_acceptance.csv")
    if any(row["status"] in {"FAIL", "BLOCKED"} for row in acceptance):
        print("DOWNSTREAM_CONTRACT=FAIL acceptance_blocker")
        return 1
    samples = sorted((PACKAGE / "data/samples").glob("*_sample_pre2016.csv"))
    if not samples:
        print("DOWNSTREAM_CONTRACT=FAIL missing_pre2016_samples")
        return 1
    full_daily = PACKAGE / "data/security_daily_market.parquet"
    print("DOWNSTREAM_CONTRACT=PASS")
    print(f"SAMPLES={len(samples)}")
    print("FULL_PARQUET=LOCAL_AVAILABLE" if full_daily.is_file() else "FULL_PARQUET=NOT_PRESENT_EXTERNAL_DATA_REQUIRED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
