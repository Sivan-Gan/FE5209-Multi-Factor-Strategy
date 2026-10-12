"""Separated readers for final K=4 strategy data and the research-only archive."""
from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[2]
FINAL_IDS = ("QUALITY_CURRENT", "MOMENTUM_12_1", "LOWRISK_CURRENT", "INVESTMENT_CURRENT")
ARCHIVE_IDS = (
    "VALUE_CURRENT", "VALUE_EP", "VALUE_BP", "VALUE_EP_BP", "VALUE_FCF_COMPONENT",
    "QUALITY_CURRENT", "QUALITY_ROE", "QUALITY_PROFITABILITY", "QUALITY_GPA_COMPONENT",
    "QUALITY_NEG_LEVERAGE_COMPONENT", "QUALITY_ROA_COMPONENT", "MOMENTUM_CURRENT", "MOMENTUM_12_1",
    "MOMENTUM_6_1", "LOWRISK_CURRENT", "LOWRISK_VOL", "LOWRISK_IVOL", "INVESTMENT_CURRENT",
    "DIVIDEND_CURRENT", "SIZE_CURRENT",
)


class ArchiveGovernanceError(ValueError):
    """Raised when a caller tries to treat the research archive as strategy input."""


def load_final_factor_scores(root: Path | None = None) -> pa.Table:
    """Load the only factor-score table permitted for Strategy Engine use: frozen K=4."""
    base = (root or ROOT).resolve()
    path = base / "handoff/final_factor/data/factor_scores_weekly.parquet"
    table = pq.read_table(path)
    required = {"quality_score", "momentum_score", "lowrisk_score", "investment_score"}
    if not required.issubset(table.column_names):
        raise ArchiveGovernanceError("FINAL_FACTOR_INTERFACE_SCHEMA_INVALID")
    forbidden = set(ARCHIVE_IDS) & set(table.column_names)
    if forbidden:
        raise ArchiveGovernanceError(f"FINAL_FACTOR_INTERFACE_HAS_ARCHIVE_COLUMNS:{sorted(forbidden)}")
    return table


def load_factor_research_archive(root: Path | None = None) -> pa.Table:
    """Load all 20 frozen specifications for audit/reproducibility only.

    Callers receive a table but no strategy-facing conversion API.  A Strategy
    Engine must use :func:`load_final_factor_scores` and its K=4 contract.
    """
    base = (root or ROOT).resolve()
    path = base / "handoff/factor_research_archive/data/all_factor_scores_weekly.parquet"
    table = pq.read_table(path)
    if not {"signal_date", "PERMNO", *ARCHIVE_IDS}.issubset(table.column_names):
        raise ArchiveGovernanceError("FACTOR_RESEARCH_ARCHIVE_SCHEMA_INVALID")
    if any("return" in name.lower() or "fmom" in name.lower() or "strategy" in name.lower() for name in table.column_names):
        raise ArchiveGovernanceError("FACTOR_RESEARCH_ARCHIVE_CONTAINS_PROHIBITED_OUTCOME_FIELD")
    return table
