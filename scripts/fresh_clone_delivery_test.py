#!/usr/bin/env python3
"""Create an external clean clone and validate the Git-delivery surface only."""
from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


EXPECTED = ("QUALITY_CURRENT", "MOMENTUM_12_1", "LOWRISK_CURRENT", "INVESTMENT_CURRENT")
MACHINE_PATH_PATTERNS = (
    "/Users/" + "gan" + "junhui" + "/",
    "/" + "Desktop" + "/",
    "/" + "课堂" + "/",
    "/" + "FE" + "5209" + "/FE" + "5209",
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def command(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=True)


def verify_clone(clone: Path, python: str) -> str:
    required = (
        "README.md", "ENVIRONMENT.md", "LICENSE_OR_DATA_NOTICE.md", "requirements.txt",
        "docs/PROJECT_STATUS.md", "docs/GITHUB_TEAM_HANDOFF.md", "docs/FINAL_RESEARCH_AUDIT_INDEX.md",
        "handoff/final_factor/FINAL_FACTOR_HANDOFF_CONTRACT.md",
        "handoff/final_factor/final_factor_registry.csv",
        "handoff/final_factor/data/factor_scores_weekly_sample.csv",
        "scripts/smoke_test_handoff.py", "GITHUB_DELIVERY_MANIFEST.csv",
    )
    missing = [path for path in required if not (clone / path).is_file()]
    if missing:
        raise ValueError(f"Required tracked files missing: {missing}")
    with (clone / "handoff/final_factor/final_factor_registry.csv").open(newline="", encoding="utf-8") as handle:
        rows = sorted(csv.DictReader(handle), key=lambda row: int(row["final_factor_order"]))
    ids = tuple(row["specification_id"] for row in rows)
    if ids != EXPECTED:
        raise ValueError(f"Unexpected final factors: {ids}")
    if any(path.exists() for path in (clone / "handoff/final_factor/data").glob("*.parquet")):
        raise ValueError("External full Parquet files unexpectedly entered fresh clone")
    absolute = subprocess.run(
        ["rg", "-n", "|".join(MACHINE_PATH_PATTERNS), "src", "tests", "config", "scripts"],
        cwd=clone, text=True, capture_output=True,
    )
    if absolute.returncode not in (0, 1):
        raise ValueError("Absolute-path audit command failed")
    if absolute.stdout:
        raise ValueError("Runtime absolute path found in fresh clone")
    imports = command([python, "-B", "-c", "from src.handoff import prepare_final_factor_handoff; print('IMPORTS PASS')"], clone)
    smoke = command([python, "-B", "scripts/smoke_test_handoff.py"], clone)
    if "FULL_PARQUET=NOT_PRESENT_EXTERNAL_DATA_REQUIRED" not in smoke.stdout:
        raise ValueError("Fresh clone did not give the documented external-data message")
    if "IMPORTS PASS" not in imports.stdout or "SMOKE_TEST_HANDOFF PASS" not in smoke.stdout:
        raise ValueError("Fresh-clone import or smoke check failed")
    return smoke.stdout.strip()


def run(repo: Path, python: str) -> str:
    with tempfile.TemporaryDirectory(prefix="fe5209-fresh-clone-") as temporary:
        destination = Path(temporary) / "FE5209"
        command(["git", "clone", "--no-local", "--quiet", str(repo), str(destination)])
        return verify_clone(destination, python)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=project_root())
    parser.add_argument("--python", default=sys.executable, help="Interpreter with project dependencies")
    args = parser.parse_args()
    try:
        summary = run(args.repo.resolve(), args.python)
    except (OSError, ValueError, subprocess.CalledProcessError, csv.Error) as exc:
        print(f"FRESH_CLONE_DELIVERY_TEST FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"FRESH_CLONE_DELIVERY_TEST PASS {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
