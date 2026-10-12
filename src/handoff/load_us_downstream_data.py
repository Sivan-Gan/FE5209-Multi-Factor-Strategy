"""Stage-aware access to the governed local US downstream data package.

The public loader returns only tables restricted to the approved stage window.
It does not calculate returns, construct scores, or supply a bypass for
protected Historical-OOS access.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Mapping

import pyarrow as pa
import pyarrow.parquet as pq


PACKAGE = Path("handoff/us_downstream")
AUDIT = Path("outputs/audit/wrds_formal")


class GovernanceError(RuntimeError):
    """Raised when an unapproved downstream data stage is requested."""


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _has_pass(path: Path, required_check: str) -> bool:
    if not path.is_file():
        return False
    with path.open(newline="", encoding="utf-8") as handle:
        return any(
            row.get("check") == required_check and row.get("status") == "PASS"
            for row in csv.DictReader(handle)
        )


def _require_hist_oos_freezes(root: Path) -> None:
    gate_b = root / AUDIT / "gate_b_fmom_configuration_freeze_acceptance.csv"
    gate_c = root / AUDIT / "gate_c_us_base_strategy_freeze_acceptance.csv"
    if not _has_pass(gate_b, "GATE_B_FROZEN"):
        raise GovernanceError(
            "HIST_OOS_BLOCKED__MISSING_OR_INVALID_GATE_B_FREEZE_EVIDENCE"
        )
    if not _has_pass(gate_c, "GATE_C_FROZEN"):
        raise GovernanceError(
            "HIST_OOS_BLOCKED__MISSING_OR_INVALID_GATE_C_FREEZE_EVIDENCE"
        )


def _permitted_paths(stage: str, root: Path | None = None) -> Mapping[str, Path]:
    """Resolve physical local files for an explicit governance stage.

    This internal helper is intentionally not the public loader: callers must
    receive stage-filtered tables, rather than file paths to broader datasets.
    """

    repository = (root or project_root()).resolve()
    data = repository / PACKAGE / "data"
    common = {
        "weekly_execution_calendar": data / "weekly_execution_calendar.parquet",
        "security_weekly_execution_returns": data / "security_weekly_execution_returns.parquet",
        "execution_liquidity_weekly": data / "execution_liquidity_weekly.parquet",
        "benchmark_daily_returns": data / "benchmark_daily_returns.parquet",
        "benchmark_weekly_execution_returns": data / "benchmark_weekly_execution_returns.parquet",
        "risk_free_daily": data / "risk_free_daily.parquet",
        "risk_free_weekly": data / "risk_free_weekly.parquet",
        "ff_factors_daily": data / "ff_factors_daily.parquet",
        "ff_factors_monthly": data / "ff_factors_monthly.parquet",
        "security_reference_history": data / "security_reference_history.parquet",
    }
    normalized = stage.upper().strip()
    if normalized == "GATE_B":
        permitted = {
            **common,
            "security_daily_market_pre_gate_b": data / "security_daily_market_pre_gate_b.parquet",
            "security_daily_market_gate_b_config": data / "security_daily_market_gate_b_config.parquet",
            "final_factor_returns_seed_weekly": data / "final_factor_returns_seed_weekly.parquet",
        }
    elif normalized == "HIST_OOS":
        _require_hist_oos_freezes(repository)
        permitted = {
            **common,
            "security_daily_market_hist_oos": data / "security_daily_market_hist_oos.parquet",
        }
    else:
        raise GovernanceError(f"UNKNOWN_OR_UNAUTHORIZED_DOWNSTREAM_STAGE:{stage}")
    missing = sorted(name for name, path in permitted.items() if not path.is_file())
    if missing:
        raise GovernanceError(f"DOWNSTREAM_DATA_MISSING:{','.join(missing)}")
    return permitted


def _date_key(dataset: str) -> str:
    if dataset in {
        "weekly_execution_calendar", "security_weekly_execution_returns",
        "execution_liquidity_weekly", "benchmark_weekly_execution_returns",
        "risk_free_weekly",
    }:
        return "signal_date"
    if dataset == "security_reference_history":
        return "effective_start_date"
    return "date"


def _read_stage_table(path: Path, dataset: str, start: str | None, end: str | None) -> pa.Table:
    filters: list[tuple[str, str, str]] = []
    if dataset != "final_factor_returns_seed_weekly":
        key = _date_key(dataset)
        if start:
            filters.append((key, ">=", start))
        if end:
            filters.append((key, "<=", end))
    return pq.read_table(path, filters=filters or None)


def load_us_downstream_data(stage: str, root: Path | None = None) -> Mapping[str, pa.Table]:
    """Load only the values permitted for a governance stage.

    ``GATE_B`` yields pre-2016 and 2016--2020 data only. ``HIST_OOS`` yields
    2021--2025 data only, after both named freeze artifacts pass. There is no
    normal ``force`` or file-path return that could silently bypass the boundary.
    """

    normalized = stage.upper().strip()
    permitted = _permitted_paths(normalized, root)
    if normalized == "GATE_B":
        start, end = None, "2020-12-31"
    elif normalized == "HIST_OOS":
        start, end = "2021-01-01", "2025-12-31"
    else:  # `_permitted_paths` raises first; retained for static clarity.
        raise GovernanceError(f"UNKNOWN_OR_UNAUTHORIZED_DOWNSTREAM_STAGE:{stage}")
    return {
        name: _read_stage_table(path, name, start, end)
        for name, path in permitted.items()
    }
