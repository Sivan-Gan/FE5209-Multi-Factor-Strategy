"""Sample-window governance for the frozen US Base Strategy (SSOT 0.6 / 0.7 / 4.2).

- Gate B tools (run_gate_b.py etc.) stay locked to <= 2020-12-31.
- The frozen runner may read 2021-2025 (HIST_OOS_BASE) only when
  config/strategy/us_base_strategy.json has status FROZEN, a freeze date, at least one
  approver, and a frozen transaction cost.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

GATE_B_LAST_DATE = "2020-12-31"
CONFIG_FILE = "config/strategy/us_base_strategy.json"

PERIODS = {
    "gate_b": ("2016-01-01", "2020-12-31", "CONFIGURATION_AND_STRATEGY_FREEZE"),
    "hist_oos": ("2021-01-01", "2025-12-31", "HIST_OOS_BASE"),
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_config(root: Path | None = None) -> dict:
    root = root or project_root()
    return json.loads((root / CONFIG_FILE).read_text(encoding="utf-8"))


def config_hash(root: Path | None = None) -> str:
    root = root or project_root()
    return hashlib.sha256((root / CONFIG_FILE).read_bytes()).hexdigest()


def freeze_problems(cfg: dict) -> list[str]:
    problems = []
    if cfg.get("status") != "FROZEN":
        problems.append(f"status is {cfg.get('status')!r}, not 'FROZEN'")
    if not cfg.get("freeze_date"):
        problems.append("freeze_date is empty")
    if not cfg.get("approvers"):
        problems.append("approvers list is empty")
    if cfg.get("transaction_cost", {}).get("status") != "FROZEN":
        problems.append("transaction_cost.status is not 'FROZEN'")
    return problems


def check_window(end: str, allow_oos: bool = False, root: Path | None = None) -> None:
    """Raise unless reading data up to `end` is permitted."""
    if end <= GATE_B_LAST_DATE:
        return
    if not allow_oos:
        raise ValueError(f"Gate B may not read data after {GATE_B_LAST_DATE}; got end={end}")
    problems = freeze_problems(load_config(root))
    if problems:
        raise PermissionError("Historical OOS is locked until the strategy is FROZEN: " + "; ".join(problems))
    if end > PERIODS["hist_oos"][1]:
        raise ValueError(f"end={end} is beyond HIST_OOS_BASE ({PERIODS['hist_oos'][1]}); "
                         "RECENT_EXTENSION / LIVE_OPS are separate runs")
