"""Formal Gate 0 Phase 2C: Compustat PIT and fundamental-semantics audit.

This module is intentionally an audit/data-contract step.  It reads immutable
WRDS raw files and writes only Gate-0 evidence.  It does not build a canonical
fundamental panel, choose factor definitions, calculate signals, or run a
portfolio/backtest.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import sqlite3
import tempfile
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from src.data import wrds_gate0_phase1 as phase1
from src.data import wrds_gate0_phase2a as phase2a
from src.data import wrds_gate0_phase2b2 as phase2b2


EVIDENCE_STAGE = "FORMAL_GATE0_PHASE2C_COMPUSTAT_PIT_SEMANTICS"
FORMAL_CUTOFF = date(2025, 12, 31)

AUDIT_RELATIVE = Path("outputs/audit/wrds_formal")
ANNUAL_RELATIVE = Path("compustat/Compustat_NA_Fundamentals_Annual_2004-01_2026-10.csv.gz")
QUARTERLY_RELATIVE = Path("compustat/Compustat_NA_Fundamentals_Quarterly_2004-01_2026-10.csv.gz")
TRADING_CALENDAR_RELATIVE = Path("crsp/CRSP_Daily_Market_and_SP500_Indexes_2004-01-01_2025-12-31.csv.gz")
CCM_RELATIVE = phase2a.CCM_RELATIVE_PATH

SCHEMA_INVENTORY = AUDIT_RELATIVE / "gate0_phase2c_compustat_schema_inventory.csv"
RDQ_COVERAGE = AUDIT_RELATIVE / "gate0_phase2c_rdq_coverage.csv"
ANNUAL_AVAILABILITY = AUDIT_RELATIVE / "gate0_phase2c_annual_availability_audit.csv"
FLOW_SEMANTICS = AUDIT_RELATIVE / "gate0_phase2c_quarterly_flow_semantics.csv"
TTM_CAPABILITY = AUDIT_RELATIVE / "gate0_phase2c_ttm_capability.csv"
DUPLICATE_CONFLICT = AUDIT_RELATIVE / "gate0_phase2c_duplicate_conflict_audit.csv"
FIELD_CONTRACT = AUDIT_RELATIVE / "gate0_phase2c_fundamental_field_contract.csv"
SOURCE_EVIDENCE = AUDIT_RELATIVE / "gate0_phase2c_source_evidence.csv"
ACCEPTANCE = AUDIT_RELATIVE / "gate0_phase2c_acceptance.csv"
REPORT = AUDIT_RELATIVE / "WRDS_GATE0_PHASE2C_COMPUSTAT_PIT.md"
OPEN_ISSUES = AUDIT_RELATIVE / "gate0_open_issues.csv"

PRIMARY_FIELDS: tuple[tuple[str, str, str, str, str], ...] = (
    ("assets", "at", "atq", "STOCK", "Total assets"),
    ("equity", "ceq", "ceqq", "STOCK", "Common equity"),
    ("liabilities", "lt", "ltq", "STOCK", "Total liabilities"),
    ("short_term_debt", "dlc", "dlcq", "STOCK", "Debt in current liabilities"),
    ("long_term_debt", "dltt", "dlttq", "STOCK", "Long-term debt"),
    ("preferred_stock", "pstk", "pstkq", "STOCK", "Preferred stock"),
    ("revenue", "revt", "revtq", "SINGLE_QUARTER_FLOW", "Revenue"),
    ("sales", "sale", "saleq", "SINGLE_QUARTER_FLOW", "Net sales"),
    ("cogs", "cogs", "cogsq", "SINGLE_QUARTER_FLOW", "Cost of goods sold"),
    ("gross_profit", "gp", "", "ANNUAL_FLOW_ONLY", "Gross profit; no delivered quarterly gpq"),
    ("net_income", "ni", "niq", "SINGLE_QUARTER_FLOW", "Net income"),
    ("operating_cash_flow", "oancf", "oancfy", "YTD_FLOW", "Operating cash flow"),
    ("capital_expenditure", "capx", "capxy", "YTD_FLOW", "Capital expenditure"),
    ("cash_dividends_common", "dvc", "", "ANNUAL_FLOW_ONLY", "Common dividends"),
    ("cash_dividends_total", "dvt", "", "ANNUAL_FLOW_ONLY", "Total dividends"),
    ("shares_outstanding", "csho", "cshoq", "STOCK", "Common shares outstanding"),
)
YTD_FIELDS = ("oancfy", "capxy")


@dataclass(frozen=True)
class QuarterObservation:
    """One raw logical quarterly observation retained only for audit logic."""

    datadate: date | None
    rdq: date | None
    values: dict[str, float | None]
    raw_count: int = 1
    conflict: bool = False


@dataclass(frozen=True)
class FlowResult:
    """Result of one bounded single-quarter YTD-flow audit calculation."""

    status: str
    value: float | None
    availability_date: date | None
    method: str


def project_root() -> Path:
    """Return the checked-in repository root independent of working directory."""

    return Path(__file__).resolve().parents[2]


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _parse_date(value: Any) -> date | None:
    text = _clean(value)
    if not text:
        return None
    for pattern in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _number(value: Any) -> float | None:
    try:
        numeric = float(_clean(value))
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _percent(numerator: int, denominator: int) -> str:
    return f"{100 * numerator / denominator:.6f}" if denominator else ""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _quantile(values: Sequence[int], probability: float) -> str:
    if not values:
        return ""
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return str(ordered[lower])
    return f"{ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower):.6f}"


def _gzip_rows(path: Path) -> Iterator[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def _gzip_columns(path: Path) -> tuple[str, ...]:
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        return tuple(csv.DictReader(handle).fieldnames or ())


def _sha256(path: Path) -> str:
    return phase1.sha256_file(path)


def _write_csv(root: Path, relative: Path, columns: tuple[str, ...], rows: Iterable[dict[str, Any]]) -> None:
    phase1._write_csv(root / relative, columns, list(rows))


def _date_iso(value: date | None) -> str:
    return value.isoformat() if value is not None else ""


def next_local_trading_day(publication_date: date | None, trading_dates: Sequence[date]) -> date | None:
    """Return the first delivered local trading day strictly after publication.

    A date without a time stamp is deliberately not usable on its own date.
    No forward-fill from a previous observation and no future-date substitution is
    allowed: a missing following local trading date leaves the item unavailable.
    """

    if publication_date is None:
        return None
    index = bisect_right(trading_dates, publication_date)
    return trading_dates[index] if index < len(trading_dates) else None


def usable_at_signal(availability_date: date | None, signal_date: date | None, *, formal_cutoff: date = FORMAL_CUTOFF) -> bool:
    """Enforce publication availability and the frozen formal market-data cutoff."""

    return bool(
        availability_date is not None
        and signal_date is not None
        and availability_date <= signal_date <= formal_cutoff
    )


def quarterly_availability_status(observation: QuarterObservation, trading_dates: Sequence[date]) -> tuple[str, date | None]:
    """Bound one quarterly raw row without choosing any duplicate/conflict row."""

    if observation.raw_count != 1 or observation.conflict:
        return "DUPLICATE_OR_CONFLICT", None
    if observation.datadate is None:
        return "MISSING_DATADATE", None
    if observation.rdq is None:
        return "RDQ_MISSING", None
    if observation.rdq < observation.datadate:
        return "RDQ_BEFORE_DATADATE", None
    availability = next_local_trading_day(observation.rdq, trading_dates)
    if availability is None:
        return "NO_NEXT_LOCAL_TRADING_DAY", None
    if availability > FORMAL_CUTOFF:
        return "OUTSIDE_FORMAL_CUTOFF", availability
    return "PIT_AVAILABLE_NEXT_LOCAL_TRADING_DAY", availability


def derive_ytd_single_quarter(
    observations: dict[int, QuarterObservation],
    field_name: str,
    trading_dates: Sequence[date],
) -> dict[int, FlowResult]:
    """Audit the only permitted same-fiscal-year YTD differencing rule.

    The result is intentionally a small audit object.  It does not persist a
    panel and never bridges missing predecessors or fiscal-year boundaries.
    """

    result: dict[int, FlowResult] = {}
    prior_value: float | None = None
    prior_available = False
    for quarter in (1, 2, 3, 4):
        observation = observations.get(quarter)
        if observation is None:
            result[quarter] = FlowResult("MISSING_QUARTER", None, None, "NO_FILL")
            prior_value, prior_available = None, False
            continue
        availability_status, availability_date = quarterly_availability_status(observation, trading_dates)
        value = observation.values.get(field_name)
        if availability_status != "PIT_AVAILABLE_NEXT_LOCAL_TRADING_DAY":
            result[quarter] = FlowResult(availability_status, None, availability_date, "NO_DERIVATION")
            prior_value, prior_available = None, False
            continue
        if value is None:
            result[quarter] = FlowResult("MISSING_YTD_FIELD", None, availability_date, "NO_FILL")
            prior_value, prior_available = None, False
            continue
        if quarter == 1:
            result[quarter] = FlowResult("VALID", value, availability_date, "Q1_YTD")
        elif not prior_available or prior_value is None:
            result[quarter] = FlowResult("MISSING_PREDECESSOR", None, availability_date, "NO_CROSS_YEAR_DIFFERENCE")
        else:
            method = "Q4_YTD_MINUS_Q3" if quarter == 4 else "YTD_MINUS_PRIOR_QUARTER"
            result[quarter] = FlowResult("VALID", value - prior_value, availability_date, method)
        prior_value, prior_available = value, True
    return result


def ttm_four_quarter_windows(
    values: dict[tuple[int, int], FlowResult], *, formal_cutoff: date = FORMAL_CUTOFF
) -> tuple[int, int]:
    """Return (valid TTM windows, candidate endpoints without four valid quarters)."""

    valid_indexes = {
        fiscal_year * 4 + fiscal_quarter
        for (fiscal_year, fiscal_quarter), flow in values.items()
        if flow.status == "VALID"
        and flow.availability_date is not None
        and flow.availability_date <= formal_cutoff
    }
    endpoints = sorted(valid_indexes)
    valid = sum(all(index - offset in valid_indexes for offset in range(4)) for index in endpoints)
    return valid, len(endpoints) - valid


def duplicate_classification(fingerprints: Sequence[str]) -> str:
    """Classify a complete raw-key group without order-based selection."""

    if len(fingerprints) <= 1:
        return "UNIQUE"
    return "EXACT_DUPLICATE" if len(set(fingerprints)) == 1 else "CONFLICT"


class DuplicateTracker:
    """Disk-backed duplicate/conflict profiler for a raw logical key."""

    def __init__(self, name: str) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix=f"wrds_gate0_phase2c_{name}_")
        self._connection = sqlite3.connect(Path(self._temporary.name) / "duplicate_keys.sqlite")
        self._connection.execute(
            "CREATE TABLE seen (key_value TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, secondary_value TEXT NOT NULL, row_count INTEGER NOT NULL, conflict INTEGER NOT NULL, secondary_conflict INTEGER NOT NULL)"
        )
        self._connection.execute("CREATE INDEX seen_conflict ON seen(conflict)")
        self.total_rows = 0
        self.missing_key_rows = 0

    def add(self, key: tuple[str, str], fingerprint: str, *, secondary_value: str = "") -> None:
        self.total_rows += 1
        if not all(key):
            self.missing_key_rows += 1
            return
        encoded = _json(key)
        cursor = self._connection.execute("SELECT fingerprint, secondary_value, row_count, conflict, secondary_conflict FROM seen WHERE key_value = ?", (encoded,))
        existing = cursor.fetchone()
        if existing is None:
            self._connection.execute(
                "INSERT INTO seen(key_value, fingerprint, secondary_value, row_count, conflict, secondary_conflict) VALUES (?, ?, ?, 1, 0, 0)",
                (encoded, fingerprint, secondary_value),
            )
            return
        old_fingerprint, old_secondary, old_count, old_conflict, old_secondary_conflict = existing
        self._connection.execute(
            "UPDATE seen SET row_count = ?, conflict = ?, secondary_conflict = ? WHERE key_value = ?",
            (
                int(old_count) + 1,
                1 if old_conflict or old_fingerprint != fingerprint else 0,
                1 if old_secondary_conflict or old_secondary != secondary_value else 0,
                encoded,
            ),
        )

    def finalize(self) -> dict[str, Any]:
        self._connection.commit()
        duplicate_groups, duplicate_extra, exact_groups, conflict_groups, secondary_conflict_groups = self._connection.execute(
            "SELECT "
            "SUM(CASE WHEN row_count > 1 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN row_count > 1 THEN row_count - 1 ELSE 0 END), "
            "SUM(CASE WHEN row_count > 1 AND conflict = 0 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN row_count > 1 AND conflict = 1 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN row_count > 1 AND secondary_conflict = 1 THEN 1 ELSE 0 END) "
            "FROM seen"
        ).fetchone()
        samples = [
            _json(json.loads(row[0]))
            for row in self._connection.execute(
                "SELECT key_value FROM seen WHERE row_count > 1 AND conflict = 1 ORDER BY key_value LIMIT 12"
            )
        ]
        conflict_keys = {
            tuple(json.loads(row[0]))
            for row in self._connection.execute("SELECT key_value FROM seen WHERE row_count > 1 AND conflict = 1")
        }
        return {
            "raw_rows": self.total_rows,
            "missing_key_rows": self.missing_key_rows,
            "duplicate_key_groups": int(duplicate_groups or 0),
            "duplicate_extra_rows": int(duplicate_extra or 0),
            "exact_duplicate_key_groups": int(exact_groups or 0),
            "conflicting_key_groups": int(conflict_groups or 0),
            "secondary_conflicting_key_groups": int(secondary_conflict_groups or 0),
            "sample_conflicting_keys_json": _json(samples),
            "conflict_keys": conflict_keys,
        }

    def close(self) -> None:
        self._connection.close()
        self._temporary.cleanup()


def _row_fingerprint(row: dict[str, str]) -> str:
    encoded = _json({column: _clean(value) for column, value in sorted(row.items())})
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _load_trading_dates(root: Path) -> list[date]:
    observed: set[date] = set()
    for row in _gzip_rows(phase1.raw_root(root) / TRADING_CALENDAR_RELATIVE):
        candidate = _parse_date(row.get("DlyCalDt") or row.get("dlycaldt"))
        if candidate is not None:
            observed.add(candidate)
    dates = sorted(observed)
    if not dates:
        raise ValueError("Delivered CRSP market/index file has no trading-calendar dates.")
    return dates


def _linked_ccm_gvkeys(root: Path) -> set[str]:
    links, _ = phase2a.read_ccm_links(phase1.raw_root(root) / CCM_RELATIVE)
    return {
        link.gvkey
        for link in links
        if link.gvkey and phase2a._accepted_ccm(link) and phase2a._valid_ccm_interval(link)
    }


def _init_inventory(columns: tuple[str, ...]) -> dict[str, Any]:
    return {
        "columns": columns,
        "row_count": 0,
        "gvkeys": set(),
        "missing_gvkey": 0,
        "missing_datadate": 0,
        "min_date": None,
        "max_date": None,
        "fiscal_years": set(),
        "fiscal_quarters": set(),
        "field_nonmissing": Counter(),
        "format_distributions": {field: Counter() for field in ("indfmt", "datafmt", "consol", "curcd", "curcdq", "costat", "popsrc") if field in {item.lower() for item in columns}},
    }


def _update_inventory(inventory: dict[str, Any], row: dict[str, str], *, quarterly: bool) -> None:
    inventory["row_count"] += 1
    gvkey = _clean(row.get("gvkey"))
    datadate = _parse_date(row.get("datadate"))
    if gvkey:
        inventory["gvkeys"].add(gvkey)
    else:
        inventory["missing_gvkey"] += 1
    if datadate is None:
        inventory["missing_datadate"] += 1
    else:
        inventory["min_date"] = datadate if inventory["min_date"] is None else min(inventory["min_date"], datadate)
        inventory["max_date"] = datadate if inventory["max_date"] is None else max(inventory["max_date"], datadate)
    fiscal_year = _clean(row.get("fyearq" if quarterly else "fyear"))
    fiscal_quarter = _clean(row.get("fqtr")) if quarterly else ""
    if fiscal_year:
        inventory["fiscal_years"].add(fiscal_year)
    if fiscal_quarter:
        inventory["fiscal_quarters"].add(fiscal_quarter)
    for field in inventory["columns"]:
        if _clean(row.get(field)):
            inventory["field_nonmissing"][field] += 1
    for field, counter in inventory["format_distributions"].items():
        counter[_clean(row.get(field)) or "<BLANK>"] += 1


def _inventory_rows(dataset: str, inventory: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    summary = {
        "raw_row_count": inventory["row_count"],
        "column_count": len(inventory["columns"]),
        "unique_gvkey_count": len(inventory["gvkeys"]),
        "missing_gvkey_count": inventory["missing_gvkey"],
        "missing_datadate_count": inventory["missing_datadate"],
        "min_datadate": _date_iso(inventory["min_date"]),
        "max_datadate": _date_iso(inventory["max_date"]),
        "fiscal_year_values_json": _json(sorted(inventory["fiscal_years"])),
        "fiscal_quarter_values_json": _json(sorted(inventory["fiscal_quarters"])),
    }
    for metric, value in summary.items():
        rows.append({"dataset": dataset, "record_type": "DATASET_SUMMARY", "field_name": "", "metric": metric, "category": "ALL", "value": value, "denominator": "", "percentage": "", "notes": "Raw delivered-file inventory"})
    for field in inventory["columns"]:
        nonmissing = inventory["field_nonmissing"][field]
        rows.append({"dataset": dataset, "record_type": "FIELD", "field_name": field, "metric": "nonmissing", "category": "RAW", "value": nonmissing, "denominator": inventory["row_count"], "percentage": _percent(nonmissing, inventory["row_count"]), "notes": "Raw-delivery field presence; no unit inference"})
    for field, counter in sorted(inventory["format_distributions"].items()):
        for category, count in sorted(counter.items()):
            rows.append({"dataset": dataset, "record_type": "FORMAT_DISTRIBUTION", "field_name": field, "metric": "frequency", "category": category, "value": count, "denominator": inventory["row_count"], "percentage": _percent(count, inventory["row_count"]), "notes": "Observed delivered raw values"})
    return rows


def _update_q4_map(
    q4_map: dict[tuple[str, str, str], dict[str, Any]], row: dict[str, str]
) -> None:
    if _clean(row.get("fqtr")) != "4":
        return
    gvkey, fiscal_year = _clean(row.get("gvkey")), _clean(row.get("fyearq"))
    datadate = _parse_date(row.get("datadate"))
    if not gvkey or not fiscal_year or datadate is None:
        return
    item = q4_map.setdefault((gvkey, fiscal_year, datadate.isoformat()), {"raw_rows": 0, "rdq_values": set(), "missing_rdq": 0, "invalid_rdq": 0})
    item["raw_rows"] += 1
    rdq = _parse_date(row.get("rdq"))
    if rdq is None:
        item["missing_rdq"] += 1
    elif datadate is None or rdq < datadate:
        item["invalid_rdq"] += 1
    else:
        item["rdq_values"].add(rdq)


def _quarter_observation_from_row(row: dict[str, str]) -> QuarterObservation:
    return QuarterObservation(
        datadate=_parse_date(row.get("datadate")),
        rdq=_parse_date(row.get("rdq")),
        values={field: _number(row.get(field)) for field in YTD_FIELDS},
    )


def _collect_linked_flow_observations(
    quarterly_path: Path, linked_gvkeys: set[str]
) -> tuple[dict[tuple[str, int], dict[int, QuarterObservation]], dict[str, Any]]:
    """Collect only historical-CCM-linked YTD rows for capability diagnostics."""

    raw: dict[tuple[str, int, int], list[QuarterObservation]] = defaultdict(list)
    linked_rows = 0
    for row in _gzip_rows(quarterly_path):
        gvkey = _clean(row.get("gvkey"))
        fiscal_year_text, quarter_text = _clean(row.get("fyearq")), _clean(row.get("fqtr"))
        if gvkey not in linked_gvkeys or not fiscal_year_text.isdigit() or quarter_text not in {"1", "2", "3", "4"}:
            continue
        linked_rows += 1
        raw[(gvkey, int(fiscal_year_text), int(quarter_text))].append(_quarter_observation_from_row(row))
    groups: dict[tuple[str, int], dict[int, QuarterObservation]] = defaultdict(dict)
    duplicate_or_conflict = 0
    for (gvkey, fiscal_year, quarter), observations in raw.items():
        first = observations[0]
        fingerprints = [
            _json({"datadate": _date_iso(item.datadate), "rdq": _date_iso(item.rdq), **{field: item.values[field] for field in YTD_FIELDS}})
            for item in observations
        ]
        conflict = duplicate_classification(fingerprints) == "CONFLICT"
        if len(observations) > 1:
            duplicate_or_conflict += 1
        groups[(gvkey, fiscal_year)][quarter] = QuarterObservation(
            datadate=first.datadate,
            rdq=first.rdq,
            values=first.values,
            raw_count=len(observations),
            conflict=conflict,
        )
    return dict(groups), {"linked_raw_rows": linked_rows, "logical_fiscal_groups": len(groups), "duplicate_or_conflict_quarter_keys": duplicate_or_conflict}


def _annual_status(
    row: dict[str, str],
    q4_map: dict[tuple[str, str, str], dict[str, Any]],
    annual_conflict_keys: set[tuple[str, str]],
    trading_dates: Sequence[date],
) -> tuple[str, date | None, int | None]:
    datadate = _parse_date(row.get("datadate"))
    gvkey, fiscal_year = _clean(row.get("gvkey")), _clean(row.get("fyear"))
    if datadate is None or not gvkey or not fiscal_year:
        return "ANNUAL_AVAILABILITY_BLOCKED_MISSING_KEY", None, None
    if datadate > FORMAL_CUTOFF:
        return "ANNUAL_OBSERVATION_OUTSIDE_FORMAL_CUTOFF", None, None
    info = q4_map.get((gvkey, fiscal_year, _date_iso(datadate)))
    if info is None:
        return "ANNUAL_AVAILABILITY_BLOCKED_NO_CORRESPONDING_Q4_RECORD", None, None
    rdqs = sorted(info["rdq_values"])
    if len(rdqs) == 0:
        return "ANNUAL_AVAILABILITY_BLOCKED_NO_VALID_Q4_RDQ", None, None
    if len(rdqs) > 1:
        return "ANNUAL_AVAILABILITY_BLOCKED_AMBIGUOUS_Q4_RDQ", None, None
    availability = next_local_trading_day(rdqs[0], trading_dates)
    if availability is None or availability > FORMAL_CUTOFF:
        return "ANNUAL_AVAILABILITY_BLOCKED_NO_FORMAL_TRADING_DAY", availability, (rdqs[0] - datadate).days
    if (gvkey, _date_iso(datadate)) in annual_conflict_keys:
        return "ANNUAL_AVAILABILITY_RESOLVED_WITH_Q4_RDQ_AND_RAW_CONFLICT_LIMITATION", availability, (rdqs[0] - datadate).days
    if info["missing_rdq"] or info["invalid_rdq"] or info["raw_rows"] > 1:
        return "ANNUAL_AVAILABILITY_RESOLVED_WITH_Q4_RDQ_LIMITATION", availability, (rdqs[0] - datadate).days
    return "ANNUAL_AVAILABILITY_RESOLVED_WITH_Q4_RDQ", availability, (rdqs[0] - datadate).days


def _annual_availability_rows(
    annual_path: Path,
    q4_map: dict[tuple[str, str, str], dict[str, Any]],
    annual_conflict_keys: set[tuple[str, str]],
    trading_dates: Sequence[date],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
    all_counts: Counter[str] = Counter()
    for row in _gzip_rows(annual_path):
        status, _, lag = _annual_status(row, q4_map, annual_conflict_keys, trading_dates)
        year = _parse_date(row.get("datadate"))
        grouped[((str(year.year) if year else "<MISSING>"), status)].append(lag if lag is not None else -999999)
        all_counts[status] += 1
    total = sum(all_counts.values())
    rows: list[dict[str, Any]] = []
    for (calendar_year, status), values in sorted(grouped.items()):
        actual_lags = [value for value in values if value != -999999]
        rows.append(
            {
                "scope": "ANNUAL_RAW_RECORDS",
                "calendar_datadate_year": calendar_year,
                "availability_status": status,
                "record_count": len(values),
                "denominator": total,
                "percentage": _percent(len(values), total),
                "q4_rdq_lag_days_min": min(actual_lags) if actual_lags else "",
                "q4_rdq_lag_days_median": _quantile(actual_lags, 0.5),
                "q4_rdq_lag_days_p95": _quantile(actual_lags, 0.95),
                "q4_rdq_lag_days_max": max(actual_lags) if actual_lags else "",
                "notes": "Annual datadate is not used as publication date; a resolved row remains subject to raw-value conflict and revision limitations where labelled.",
            }
        )
    return rows, dict(all_counts)


def _flow_rows(
    groups: dict[tuple[str, int], dict[int, QuarterObservation]], trading_dates: Sequence[date]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[tuple[str, int], dict[int, FlowResult]]]]:
    counters: dict[str, Counter[str]] = {field: Counter() for field in YTD_FIELDS}
    methods: dict[str, Counter[str]] = {field: Counter() for field in YTD_FIELDS}
    negative: Counter[str] = Counter()
    all_results: dict[str, dict[tuple[str, int], dict[int, FlowResult]]] = {field: {} for field in YTD_FIELDS}
    within_fiscal_period_gaps: list[int] = []
    nonpositive_period_gaps = 0
    for group_key, observations in sorted(groups.items()):
        for quarter in (2, 3, 4):
            current, prior = observations.get(quarter), observations.get(quarter - 1)
            if current is not None and prior is not None and current.datadate is not None and prior.datadate is not None:
                days = (current.datadate - prior.datadate).days
                within_fiscal_period_gaps.append(days)
                nonpositive_period_gaps += days <= 0
        for field in YTD_FIELDS:
            results = derive_ytd_single_quarter(observations, field, trading_dates)
            all_results[field][group_key] = results
            for flow in results.values():
                counters[field][flow.status] += 1
                methods[field][flow.method] += 1
                if flow.status == "VALID" and flow.value is not None and flow.value < 0:
                    negative[field] += 1
    rows: list[dict[str, Any]] = []
    summary: dict[str, dict[str, Any]] = {}
    total_slots = len(groups) * 4
    if within_fiscal_period_gaps:
        period_notes = _json(
            {
                "min": min(within_fiscal_period_gaps),
                "median": _quantile(within_fiscal_period_gaps, 0.5),
                "p05": _quantile(within_fiscal_period_gaps, 0.05),
                "p95": _quantile(within_fiscal_period_gaps, 0.95),
                "max": max(within_fiscal_period_gaps),
                "nonpositive_gap_count": nonpositive_period_gaps,
            }
        )
    else:
        period_notes = _json({"nonpositive_gap_count": 0})
    rows.append({"field_name": "ALL_YTD_FIELDS", "scope": "CCM_LINKED_GVKEY_FISCAL_QUARTER", "metric": "within_fiscal_year_consecutive_datadate_gap_days", "status": "DESCRIPTIVE_NO_INVENTED_PERIOD_LENGTH_THRESHOLD", "count": len(within_fiscal_period_gaps), "denominator": "", "percentage": "", "notes": period_notes})
    for field in YTD_FIELDS:
        valid = counters[field]["VALID"]
        summary[field] = {"valid": valid, "total_slots": total_slots, "negative": negative[field]}
        for status, count in sorted(counters[field].items()):
            rows.append({"field_name": field, "scope": "CCM_LINKED_GVKEY_FISCAL_QUARTER", "metric": "single_quarter_derivation_status", "status": status, "count": count, "denominator": total_slots, "percentage": _percent(count, total_slots), "notes": "No cross-year differencing, fill, or value selection from a conflicting raw key."})
        for method, count in sorted(methods[field].items()):
            rows.append({"field_name": field, "scope": "CCM_LINKED_GVKEY_FISCAL_QUARTER", "metric": "derivation_method", "status": method, "count": count, "denominator": valid, "percentage": _percent(count, valid), "notes": "Negative derived flows are retained as economically possible values."})
        rows.append({"field_name": field, "scope": "CCM_LINKED_GVKEY_FISCAL_QUARTER", "metric": "negative_valid_single_quarter_values", "status": "RETAINED_NOT_CLIPPED", "count": negative[field], "denominator": valid, "percentage": _percent(negative[field], valid), "notes": "Diagnostic only; no winsorization or clipping."})
    return rows, summary, all_results


def _ttm_rows(all_results: dict[str, dict[tuple[str, int], dict[int, FlowResult]]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    rows: list[dict[str, Any]] = []
    result: dict[str, dict[str, int]] = {}
    for field, group_results in sorted(all_results.items()):
        by_gvkey: dict[str, dict[tuple[int, int], FlowResult]] = defaultdict(dict)
        for (gvkey, fiscal_year), quarter_results in group_results.items():
            for fiscal_quarter, flow in quarter_results.items():
                by_gvkey[gvkey][(fiscal_year, fiscal_quarter)] = flow
        windows = 0
        incomplete = 0
        valid_quarters = 0
        for values in by_gvkey.values():
            valid, not_four = ttm_four_quarter_windows(values)
            windows += valid
            incomplete += not_four
            valid_quarters += sum(
                flow.status == "VALID" and flow.availability_date is not None and flow.availability_date <= FORMAL_CUTOFF
                for flow in values.values()
            )
        result[field] = {"valid_quarters": valid_quarters, "ttm_windows": windows, "incomplete_endpoints": incomplete}
        rows.extend(
            [
                {"field_name": field, "scope": "CCM_LINKED_GVKEYS_ONLY", "metric": "PIT_eligible_single_quarter_flows", "count": valid_quarters, "denominator": "", "percentage": "", "notes": "Only values whose availability date is on or before the formal cutoff."},
                {"field_name": field, "scope": "CCM_LINKED_GVKEYS_ONLY", "metric": "four_consecutive_quarter_ttm_windows", "count": windows, "denominator": valid_quarters, "percentage": _percent(windows, valid_quarters), "notes": "Capability only; no factor choice or TTM series is constructed."},
                {"field_name": field, "scope": "CCM_LINKED_GVKEYS_ONLY", "metric": "valid_endpoint_without_four_consecutive_quarters", "count": incomplete, "denominator": valid_quarters, "percentage": _percent(incomplete, valid_quarters), "notes": "Failure reflects missing/non-PIT-eligible predecessor(s), not imputation."},
            ]
        )
    return rows, result


def _field_contract_rows(annual_columns: tuple[str, ...], quarterly_columns: tuple[str, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    annual_set, quarterly_set = set(annual_columns), set(quarterly_columns)
    for concept, annual, quarterly, semantic_type, description in PRIMARY_FIELDS:
        annual_present = annual in annual_set if annual else False
        quarterly_present = quarterly in quarterly_set if quarterly else False
        if semantic_type == "YTD_FLOW":
            derivation = "Q1=YTD; Q2=Q2YTD-Q1YTD; Q3=Q3YTD-Q2YTD; Q4=Q4YTD-Q3YTD only within same GVKEY/fiscal year after PIT checks"
            quarterly_rule = "RDQ then next local trading day; no missing predecessor fill"
        elif semantic_type == "SINGLE_QUARTER_FLOW":
            derivation = "Raw quarterly income-statement field; no YTD differencing in this audit"
            quarterly_rule = "RDQ then next local trading day"
        elif semantic_type == "STOCK":
            derivation = "Raw period-end stock; no interpolation"
            quarterly_rule = "RDQ then next local trading day"
        else:
            derivation = "No delivered quarterly raw counterpart; do not derive a universal substitute"
            quarterly_rule = "UNAVAILABLE_IN_DELIVERED_QUARTERLY_SCHEMA"
        rows.append(
            {
                "concept": concept,
                "description": description,
                "raw_annual_field": annual,
                "annual_field_present": str(annual_present).lower(),
                "raw_quarterly_field": quarterly,
                "quarterly_field_present": str(quarterly_present).lower(),
                "stock_or_flow_semantics": semantic_type,
                "raw_unit_status": "RAW_CSV_HAS_NO_UNIT_METADATA__PROVIDER_DATA_DICTIONARY_REQUIRED_BEFORE_NUMERIC_SCALING",
                "sign_convention_status": "RETAIN_RAW_SIGN__NO_SILENT_SIGN_STANDARDIZATION",
                "annual_pit_availability_rule": "Corresponding unique valid Q4 RDQ, usable from next local trading day; annual datadate is never a publication date.",
                "quarterly_pit_availability_rule": quarterly_rule,
                "derivation_rule": derivation,
                "duplicate_conflict_handling": "CONFLICTING_GVKEY_DATADATE_RAW_ROWS_ARE_UNRESOLVED__NO_DATAFRAME_ORDER_DEDUPLICATION",
                "known_limitation": "Ordinary delivered Compustat history is publication-date aligned only; residual revision/restatement bias is disclosed. Exact raw units are not encoded in the delivered CSV.",
            }
        )
    return rows


def _source_evidence_rows() -> list[dict[str, str]]:
    return [
        {
            "evidence_id": "WRDS_QUARTERIZE_YTD",
            "semantic_question": "Quarterly cash-flow YTD versus income-statement quarterly semantics",
            "status": "RESOLVED_WITH_EVIDENCE",
            "source_title": "WRDS QUARTERIZE macro",
            "provider": "Wharton Research Data Services",
            "source_url_or_local_ref": "https://wrds-www.wharton.upenn.edu/pages/wrds-research/macros/wrds-macro-quarterize/",
            "access_date": "2026-10-10",
            "relevant_field_or_rule": "oancfy; capxy; fyearq; fqtr",
            "source_summary": "WRDS identifies cash-flow items ending in y as YTD variables, distinguishes income-statement quarterly values, and resets the difference at the first fiscal quarter.",
            "empirical_confirmation": "Delivered quarterly schema contains oancfy and capxy plus fyearq/fqtr; the audit applies only same-fiscal-year differencing.",
            "limitations": "The macro is documentation of semantics, not permission to fill missing quarters or select conflicting raw observations.",
        },
        {
            "evidence_id": "COMPUSSTAT_RESTATEMENT_BOUNDARY",
            "semantic_question": "Ordinary historical extract versus vintage-perfect accounting snapshots",
            "status": "RESOLVED_WITH_LIMITATION",
            "source_title": "WRDS / S&P Global Compustat February 2026 webinar",
            "provider": "WRDS and S&P Global Market Intelligence",
            "source_url_or_local_ref": "https://wrds-www.wharton.upenn.edu/documents/2180/WRDS_SP_Webinar_Feb_002.pdf",
            "access_date": "2026-10-10",
            "relevant_field_or_rule": "North America STD historical values; Compustat Snapshot",
            "source_summary": "The provider describes overwritten North American values in ordinary Compustat and identifies Snapshot as the product retaining iterations with effective/thru dates.",
            "empirical_confirmation": "Delivered raw schema is ordinary current/historical Compustat and contains no Snapshot effective/thru version fields.",
            "limitations": "This project therefore claims publication-date-aligned historical fundamentals, not vintage-perfect PIT accounting values; residual revision/restatement bias remains.",
        },
        {
            "evidence_id": "SSOT_PIT_RULES",
            "semantic_question": "Project-specific timing and claim boundaries",
            "status": "FROZEN_REQUIREMENT",
            "source_title": "US_SG_Cross_Market_Quant_Research",
            "provider": "FE5209 Frozen Research Manual",
            "source_url_or_local_ref": "docs/US_SG_Cross_Market_Quant_Research.md sections 4.1 and formal PIT rules",
            "access_date": "2026-10-10",
            "relevant_field_or_rule": "RDQ, annual datadate, formal cutoff 2025-12-31",
            "source_summary": "RDQ must be aligned to signal timing; date-only publication becomes usable from the next local trading day; annual datadate is not a publication date; no vintage-perfect claim without evidence.",
            "empirical_confirmation": "Phase 2C encodes these as audit/data-contract constraints and does not create a canonical panel.",
            "limitations": "The SSOT is a project rule, not a provider field dictionary for raw numeric units.",
        },
        {
            "evidence_id": "WRDS_COMPUSTAT_PRODUCT_SCOPE",
            "semantic_question": "Availability of distinct ordinary and Snapshot Compustat products",
            "status": "RESOLVED_WITH_EVIDENCE",
            "source_title": "S&P Global Market Intelligence through WRDS",
            "provider": "Wharton Research Data Services",
            "source_url_or_local_ref": "https://wrds-www.wharton.upenn.edu/pages/about/data-vendors/sp-global-market-intelligence/",
            "access_date": "2026-10-10",
            "relevant_field_or_rule": "comp_na_daily_all; comp_snapshot",
            "source_summary": "WRDS lists Compustat North America current/historical data and Compustat Snapshot as distinct products.",
            "empirical_confirmation": "The delivered package contains ordinary annual/quarterly fundamental extracts, not a Snapshot history table.",
            "limitations": "Product listing alone does not supply a raw-item unit definition.",
        },
    ]


def _write_open_issues(root: Path, *, annual_counts: dict[str, int], duplicate_profiles: dict[str, dict[str, Any]]) -> None:
    columns = ("issue_id", "source_phase", "status", "severity", "evidence", "required_resolution")
    existing: list[dict[str, str]] = []
    path = root / OPEN_ISSUES
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            existing = list(csv.DictReader(handle))
    replaced = {
        "COMPUSTAT_ANNUAL_AVAILABILITY_Q4_RDQ",
        "COMPUSTAT_HISTORICAL_VINTAGE_RESTATEMENT_BOUNDARY",
        "COMPUSTAT_RAW_DUPLICATE_CONFLICTS",
        "COMPUSTAT_RAW_FIELD_UNITS",
        "COMPUSTAT_RDQ_MISSINGNESS",
    }
    retained = [row for row in existing if _clean(row.get("issue_id")) not in replaced]
    resolved = annual_counts.get("ANNUAL_AVAILABILITY_RESOLVED_WITH_Q4_RDQ", 0) + annual_counts.get("ANNUAL_AVAILABILITY_RESOLVED_WITH_Q4_RDQ_LIMITATION", 0) + annual_counts.get("ANNUAL_AVAILABILITY_RESOLVED_WITH_Q4_RDQ_AND_RAW_CONFLICT_LIMITATION", 0)
    blocked = sum(value for key, value in annual_counts.items() if key.startswith("ANNUAL_AVAILABILITY_BLOCKED"))
    raw_conflicts = sum(profile["conflicting_key_groups"] for profile in duplicate_profiles.values())
    retained.extend(
        [
            {"issue_id": "COMPUSTAT_ANNUAL_AVAILABILITY_Q4_RDQ", "source_phase": "PHASE_2C", "status": "OPEN", "severity": "WARN", "evidence": f"annual_q4_rdq_resolved_records={resolved}; blocked_records={blocked}; annual datadate was not used as a publication date.", "required_resolution": "Freeze the canonical annual-observation selection rule from the documented Q4-RDQ evidence; do not substitute a convenience lag."},
            {"issue_id": "COMPUSTAT_HISTORICAL_VINTAGE_RESTATEMENT_BOUNDARY", "source_phase": "PHASE_2C", "status": "CLOSED_WITH_LIMITATION", "severity": "WARN", "evidence": "Provider evidence distinguishes ordinary Compustat from Snapshot and documents overwritten North America values.", "required_resolution": "All later formal outputs must retain the label publication-date-aligned historical fundamentals and disclose residual revision/restatement bias unless Snapshot evidence is separately acquired."},
            {"issue_id": "COMPUSTAT_RAW_DUPLICATE_CONFLICTS", "source_phase": "PHASE_2C", "status": "OPEN", "severity": "WARN", "evidence": f"conflicting_raw_gvkey_datadate_groups={raw_conflicts}; no order-based raw-row selection was applied.", "required_resolution": "Freeze a field-level canonical conflict policy before constructing any fundamental panel; conflicting raw observations remain unavailable until then."},
            {"issue_id": "COMPUSTAT_RAW_FIELD_UNITS", "source_phase": "PHASE_2C", "status": "OPEN", "severity": "WARN", "evidence": "Delivered CSV headers carry no unit metadata for accounting fields or shares.", "required_resolution": "Obtain/retain an authoritative Compustat item dictionary before numeric factor scaling; do not assume a multiplier from raw values."},
            {"issue_id": "COMPUSTAT_RDQ_MISSINGNESS", "source_phase": "PHASE_2C", "status": "OPEN", "severity": "WARN", "evidence": "Quarterly raw records with missing or invalid RDQ are explicitly classified as not PIT eligible.", "required_resolution": "Canonical PIT construction must exclude or separately label non-PIT-available quarterly observations; no date fill is permitted."},
        ]
    )
    _write_csv(root, OPEN_ISSUES, columns, retained)


def _acceptance_rows(
    *,
    original_integrity: dict[str, Any],
    raw_hashes_match: bool,
    annual_inventory: dict[str, Any],
    quarterly_inventory: dict[str, Any],
    annual_counts: dict[str, int],
    duplicate_profiles: dict[str, dict[str, Any]],
    flow_summary: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, str]], str]:
    annual_resolved = sum(value for key, value in annual_counts.items() if key.startswith("ANNUAL_AVAILABILITY_RESOLVED"))
    annual_blocked = sum(value for key, value in annual_counts.items() if key.startswith("ANNUAL_AVAILABILITY_BLOCKED"))
    conflicts = sum(profile["conflicting_key_groups"] for profile in duplicate_profiles.values())
    schema_ok = bool(annual_inventory["columns"] and quarterly_inventory["columns"] and "rdq" in quarterly_inventory["columns"])
    flow_ok = all(flow_summary[field]["total_slots"] > 0 for field in YTD_FIELDS)
    critical_failure = original_integrity["original_raw_integrity"] != "PASS" or not raw_hashes_match or not schema_ok or not flow_ok
    verdict = "FAIL" if critical_failure else "PASS WITH WARNINGS"
    rows = [
        {"check_id": "phase1_original_raw_immutability", "severity": "CRITICAL", "status": original_integrity["original_raw_integrity"], "evidence": f"original_files={original_integrity['original_file_count']}; changed={original_integrity['original_changed_count']}", "result": "PHASE1_MANIFEST_REMAINS_HISTORICAL_EVIDENCE"},
        {"check_id": "compustat_pre_post_hash", "severity": "CRITICAL", "status": "PASS" if raw_hashes_match else "FAIL", "evidence": "Annual and quarterly SHA256 match before and after Phase 2C read-only audit.", "result": "RAW_INPUTS_NOT_MODIFIED"},
        {"check_id": "compustat_schema_inventory", "severity": "CRITICAL", "status": "PASS" if schema_ok else "FAIL", "evidence": f"annual_columns={len(annual_inventory['columns'])}; quarterly_columns={len(quarterly_inventory['columns'])}; rdq_present={'rdq' in quarterly_inventory['columns']}", "result": "SCHEMA_AUDITED_FROM_DELIVERED_RAW"},
        {"check_id": "quarterly_rdq_timing_rule", "severity": "CRITICAL", "status": "PASS", "evidence": "RDQ date-only observations are usable only from the next local trading day; missing/invalid RDQ is not PIT eligible.", "result": "NO_SAME_DAY_OR_FUTURE_USE"},
        {"check_id": "annual_availability_rule", "severity": "CRITICAL", "status": "PASS" if annual_resolved else "BLOCKED", "evidence": f"q4_rdq_resolved={annual_resolved}; blocked={annual_blocked}; no fallback lag used.", "result": "ANNUAL_DATADATE_NOT_USED_AS_PUBLICATION_DATE"},
        {"check_id": "quarterly_ytd_semantics", "severity": "CRITICAL", "status": "PASS" if flow_ok else "FAIL", "evidence": _json({field: {"valid": values["valid"], "total": values["total_slots"]} for field, values in sorted(flow_summary.items())}), "result": "SAME_FISCAL_YEAR_DIFFERENCING_ONLY"},
        {"check_id": "ordinary_compustat_vintage_boundary", "severity": "WARN", "status": "PASS_WITH_LIMITATION", "evidence": "Ordinary delivered extract is bounded as publication-date-aligned history; Snapshot-style vintage retention was not delivered.", "result": "NO_VINTAGE_PERFECT_CLAIM"},
        {"check_id": "duplicate_conflict_policy", "severity": "WARN", "status": "OPEN" if conflicts else "PASS", "evidence": f"conflicting_gvkey_datadate_groups={conflicts}", "result": "NO_ORDER_BASED_DEDUPLICATION"},
        {"check_id": "raw_field_units", "severity": "WARN", "status": "OPEN", "evidence": "Raw CSV has no field-level unit metadata.", "result": "NO_SILENT_UNIT_MULTIPLIER"},
        {"check_id": "phase_boundary", "severity": "CRITICAL", "status": "PASS", "evidence": "PHASE_2C_AUDIT_ONLY", "result": "NO_CANONICAL_DATA_FACTORS_IC_PORTFOLIO_OR_BACKTEST"},
        {"check_id": "phase2c_verdict", "severity": "CRITICAL", "status": verdict, "evidence": "See Phase 2C coverage, contract, and open-issue evidence.", "result": "FORMAL_GATE0_REMAINS_PENDING"},
    ]
    return rows, verdict


def _write_report(root: Path, result: dict[str, Any]) -> None:
    annual, quarterly = result["annual_inventory"], result["quarterly_inventory"]
    annual_resolved = sum(value for key, value in result["annual_counts"].items() if key.startswith("ANNUAL_AVAILABILITY_RESOLVED"))
    annual_blocked = sum(value for key, value in result["annual_counts"].items() if key.startswith("ANNUAL_AVAILABILITY_BLOCKED"))
    duplicate = result["duplicate_profiles"]
    ttm = result["ttm"]
    lines = [
        "# Formal WRDS Gate 0 Phase 2C — Compustat PIT and Fundamental-Semantics Audit",
        "",
        "## Verdict",
        "",
        f"**Gate 0 Phase 2C — {result['verdict']}**",
        "",
        "Formal Gate 0 overall remains **PENDING**. This evidence set audits timing, raw semantics, conflicts, and a future fundamental-field contract. It creates no canonical dataset, factor, IC, portfolio, or backtest.",
        "",
        "## Delivered raw coverage",
        "",
        f"- Annual: {annual['row_count']:,} raw rows, {len(annual['columns'])} columns, {len(annual['gvkeys']):,} GVKEYs, datadate {_date_iso(annual['min_date'])} to {_date_iso(annual['max_date'])}.",
        f"- Quarterly: {quarterly['row_count']:,} raw rows, {len(quarterly['columns'])} columns, {len(quarterly['gvkeys']):,} GVKEYs, datadate {_date_iso(quarterly['min_date'])} to {_date_iso(quarterly['max_date'])}.",
        "- Both inputs were hashed before and after the audit; Phase-1 original raw integrity was rechecked rather than rewriting its historical manifest.",
        "",
        "## PIT timing conclusion",
        "",
        "Quarterly `rdq` is treated as date-only publication evidence. A quarterly observation becomes usable only on the **next delivered local trading day** after `rdq`; blank, invalid (`rdq < datadate`), duplicate/conflicting, or cutoff-exceeding observations are not PIT eligible. No same-day assumption, date fill, or future observation use is permitted.",
        "",
        "Annual `datadate` is never treated as publication timing. The annual availability audit uses a matching unique, valid Q4 `rdq` and the same next-local-trading-day convention. It identifies evidence-supported records and separately labels no-Q4, invalid, ambiguous, calendar-unavailable, raw-conflict, and 2026-outside-formal-cutoff cases. No convenience lag was invented.",
        f"- Annual rows resolved through Q4-RDQ evidence: {annual_resolved:,}.",
        f"- Annual rows blocked by absent/invalid/ambiguous timing evidence: {annual_blocked:,}.",
        "",
        "## Revision/restatement claim boundary",
        "",
        "The delivered inputs are ordinary Compustat history, not Compustat Snapshot. Provider evidence states that North America values that are overwritten do not remain in the ordinary product, while Snapshot retains iterations with effective/thru dates. Results may therefore be called **publication-date-aligned historical fundamentals** only; they must not be called vintage-perfect PIT accounting data. Residual revision/restatement bias remains an explicit limitation.",
        "",
        "## Quarterly flow and TTM capability",
        "",
        "`oancfy` and `capxy` are audited as fiscal-YTD cash-flow variables. Single-quarter derivation is constrained to Q1=YTD, Q2/Q3=current YTD minus the immediate prior fiscal-quarter YTD, and Q4=current Q4 YTD minus Q3 YTD. The audit never differences across fiscal years, fills missing quarters, clips negative flows, or resolves conflicts by row order.",
    ]
    for field in YTD_FIELDS:
        lines.append(f"- `{field}`: {result['flow_summary'][field]['valid']:,} valid linked-GVKEY single-quarter audit observations; {ttm[field]['ttm_windows']:,} four-consecutive-quarter PIT-capable windows.")
    lines.extend(
        [
            "",
            "## Duplicate/conflict evidence",
            "",
            f"- Annual `(GVKEY, datadate)` conflict groups: {duplicate['ANNUAL']['conflicting_key_groups']:,}; exact-duplicate groups: {duplicate['ANNUAL']['exact_duplicate_key_groups']:,}.",
            f"- Quarterly `(GVKEY, datadate)` conflict groups: {duplicate['QUARTERLY']['conflicting_key_groups']:,}; keys with conflicting `rdq` values: {duplicate['QUARTERLY']['secondary_conflicting_key_groups']:,}; exact-duplicate groups: {duplicate['QUARTERLY']['exact_duplicate_key_groups']:,}.",
            "- Conflicting raw logical observations remain unresolved. This audit defines no dataframe-order, first-row, or latest-row selection rule.",
            "",
            "## Data-contract limitations carried forward",
            "",
            "- The delivered CSV headers do not encode authoritative raw-field units. The field contract preserves no silent unit multiplier; an authoritative Compustat item dictionary is required before numeric factor scaling.",
            "- The delivered quarterly schema has no `gpq`. Revenue minus COGS is not adopted as a universal quarterly gross-profit substitute.",
            "- Raw observations extending into 2026 are retained in the immutable delivery but are labelled outside the frozen 2025-12-31 formal market-data cutoff.",
            "",
            "## Explicit phase boundary",
            "",
            "No canonical data, issuer market-cap construction, factor score, Forward IC, portfolio, strategy, or Phase 3 work is performed by this audit.",
        ]
    )
    (root / REPORT).write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_phase2c(root: Path | None = None) -> dict[str, Any]:
    """Run the bounded, reproducible Compustat Phase-2C audit."""

    root = root or project_root()
    raw = phase1.raw_root(root)
    annual_path, quarterly_path = raw / ANNUAL_RELATIVE, raw / QUARTERLY_RELATIVE
    if not annual_path.exists() or not quarterly_path.exists():
        raise FileNotFoundError("Expected delivered Compustat annual and quarterly raw files are required for Phase 2C.")
    original_integrity_before = phase2b2._original_raw_integrity(root)
    if original_integrity_before["original_raw_integrity"] != "PASS":
        raise RuntimeError(f"Phase-1 original raw integrity failed before Phase 2C: {original_integrity_before}")
    before_hashes = {"ANNUAL": _sha256(annual_path), "QUARTERLY": _sha256(quarterly_path)}
    annual_columns, quarterly_columns = _gzip_columns(annual_path), _gzip_columns(quarterly_path)
    linked_gvkeys = _linked_ccm_gvkeys(root)
    annual_inventory, quarterly_inventory = _init_inventory(annual_columns), _init_inventory(quarterly_columns)
    annual_duplicates, quarterly_duplicates = DuplicateTracker("annual"), DuplicateTracker("quarterly")
    try:
        for row in _gzip_rows(annual_path):
            _update_inventory(annual_inventory, row, quarterly=False)
            annual_duplicates.add((_clean(row.get("gvkey")), _clean(row.get("datadate"))), _row_fingerprint(row))
        q4_map: dict[tuple[str, str, str], dict[str, Any]] = {}
        rdq_by_year: Counter[str] = Counter()
        row_by_year: Counter[str] = Counter()
        rdq_field_numerators: Counter[str] = Counter()
        field_nonmissing: Counter[str] = Counter()
        linked_gvkeys_with_rdq: set[str] = set()
        rdq_negative, rdq_equal = 0, 0
        rdq_lags: list[int] = []
        for row in _gzip_rows(quarterly_path):
            _update_inventory(quarterly_inventory, row, quarterly=True)
            quarterly_duplicates.add(
                (_clean(row.get("gvkey")), _clean(row.get("datadate"))),
                _row_fingerprint(row),
                secondary_value=_clean(row.get("rdq")),
            )
            datadate, rdq = _parse_date(row.get("datadate")), _parse_date(row.get("rdq"))
            year = str(datadate.year) if datadate is not None else "<MISSING>"
            row_by_year[year] += 1
            if rdq is not None:
                rdq_by_year[year] += 1
                if _clean(row.get("gvkey")) in linked_gvkeys:
                    linked_gvkeys_with_rdq.add(_clean(row.get("gvkey")))
            if datadate is not None and rdq is not None:
                lag = (rdq - datadate).days
                rdq_lags.append(lag)
                rdq_negative += lag < 0
                rdq_equal += lag == 0
            for _, _, quarterly_field, _, _ in PRIMARY_FIELDS:
                if quarterly_field and _clean(row.get(quarterly_field)):
                    field_nonmissing[quarterly_field] += 1
                    if rdq is not None:
                        rdq_field_numerators[quarterly_field] += 1
            _update_q4_map(q4_map, row)
        annual_duplicate_profile, quarterly_duplicate_profile = annual_duplicates.finalize(), quarterly_duplicates.finalize()
    finally:
        annual_duplicates.close()
        quarterly_duplicates.close()
    duplicate_profiles = {"ANNUAL": annual_duplicate_profile, "QUARTERLY": quarterly_duplicate_profile}
    trading_dates = _load_trading_dates(root)
    linked_flows, linked_flow_profile = _collect_linked_flow_observations(quarterly_path, linked_gvkeys)
    flow_rows, flow_summary, all_flow_results = _flow_rows(linked_flows, trading_dates)
    ttm_rows, ttm_summary = _ttm_rows(all_flow_results)
    annual_rows, annual_counts = _annual_availability_rows(annual_path, q4_map, annual_duplicate_profile["conflict_keys"], trading_dates)
    inventory_rows = _inventory_rows("COMPUSTAT_ANNUAL", annual_inventory) + _inventory_rows("COMPUSTAT_QUARTERLY", quarterly_inventory)
    rdq_rows: list[dict[str, Any]] = []
    total_quarterly = quarterly_inventory["row_count"]
    linked_present = linked_gvkeys & quarterly_inventory["gvkeys"]
    rdq_rows.extend(
        [
            {"scope": "CCM_HISTORICAL_SP500_GVKEYS", "calendar_datadate_year": "ALL", "metric": "linked_gvkeys_present_in_quarterly_raw", "field_name": "gvkey", "numerator": len(linked_present), "denominator": len(linked_gvkeys), "percentage": _percent(len(linked_present), len(linked_gvkeys)), "min": "", "median": "", "p95": "", "p99": "", "max": "", "notes": "CCM baseline GVKEY scope from the historical S&P 500 raw linkage; this is a raw-presence audit, not a canonical join."},
            {"scope": "CCM_HISTORICAL_SP500_GVKEYS", "calendar_datadate_year": "ALL", "metric": "linked_gvkeys_with_at_least_one_nonmissing_rdq", "field_name": "rdq", "numerator": len(linked_gvkeys_with_rdq), "denominator": len(linked_gvkeys), "percentage": _percent(len(linked_gvkeys_with_rdq), len(linked_gvkeys)), "min": "", "median": "", "p95": "", "p99": "", "max": "", "notes": "Presence of an RDQ is not alone PIT eligibility; date, conflict, and cutoff checks still apply."},
        ]
    )
    for year in sorted(row_by_year):
        count, available = row_by_year[year], rdq_by_year[year]
        rdq_rows.append({"scope": "QUARTERLY_RAW", "calendar_datadate_year": year, "metric": "rdq_nonmissing", "field_name": "rdq", "numerator": available, "denominator": count, "percentage": _percent(available, count), "min": "", "median": "", "p95": "", "p99": "", "max": "", "notes": "RDQ is date-only public-information evidence; usability begins next local trading day."})
    for field in sorted(field_nonmissing):
        count, available = field_nonmissing[field], rdq_field_numerators[field]
        rdq_rows.append({"scope": "QUARTERLY_RAW", "calendar_datadate_year": "ALL", "metric": "rdq_nonmissing_given_field_nonmissing", "field_name": field, "numerator": available, "denominator": count, "percentage": _percent(available, count), "min": "", "median": "", "p95": "", "p99": "", "max": "", "notes": "Field-level RDQ coverage; it does not override conflict or invalid-date checks."})
    valid_lags = [lag for lag in rdq_lags if lag >= 0]
    for metric, count in (("rdq_before_datadate", rdq_negative), ("rdq_equal_datadate", rdq_equal), ("rdq_nonmissing_all", sum(rdq_by_year.values()))):
        rdq_rows.append({"scope": "QUARTERLY_RAW", "calendar_datadate_year": "ALL", "metric": metric, "field_name": "rdq", "numerator": count, "denominator": total_quarterly, "percentage": _percent(count, total_quarterly), "min": min(valid_lags) if valid_lags else "", "median": _quantile(valid_lags, 0.5), "p95": _quantile(valid_lags, 0.95), "p99": _quantile(valid_lags, 0.99), "max": max(valid_lags) if valid_lags else "", "notes": "Lag statistics are computed only for nonnegative RDQ minus datadate observations."})
    duplicate_rows = [
        {"dataset": dataset, "logical_key": "(GVKEY,datadate)", "raw_row_count": profile["raw_rows"], "missing_key_rows": profile["missing_key_rows"], "duplicate_key_groups": profile["duplicate_key_groups"], "duplicate_extra_rows": profile["duplicate_extra_rows"], "exact_duplicate_key_groups": profile["exact_duplicate_key_groups"], "conflicting_key_groups": profile["conflicting_key_groups"], "rdq_conflicting_duplicate_key_groups": profile["secondary_conflicting_key_groups"] if dataset == "QUARTERLY" else "", "sample_conflicting_keys_json": profile["sample_conflicting_keys_json"], "policy": "CONFLICT_UNRESOLVED__NO_ORDER_BASED_DEDUPLICATION", "notes": "Exact duplicates are preserved in immutable raw; conflicting values receive no selection in Phase 2C."}
        for dataset, profile in sorted(duplicate_profiles.items())
    ]
    after_hashes = {"ANNUAL": _sha256(annual_path), "QUARTERLY": _sha256(quarterly_path)}
    original_integrity_after = phase2b2._original_raw_integrity(root)
    raw_hashes_match = before_hashes == after_hashes
    if original_integrity_after != original_integrity_before or not raw_hashes_match:
        raise RuntimeError("Immutable WRDS raw evidence changed during the Phase-2C audit.")
    acceptance_rows, verdict = _acceptance_rows(
        original_integrity=original_integrity_after,
        raw_hashes_match=raw_hashes_match,
        annual_inventory=annual_inventory,
        quarterly_inventory=quarterly_inventory,
        annual_counts=annual_counts,
        duplicate_profiles=duplicate_profiles,
        flow_summary=flow_summary,
    )
    _write_csv(root, SCHEMA_INVENTORY, ("dataset", "record_type", "field_name", "metric", "category", "value", "denominator", "percentage", "notes"), inventory_rows)
    _write_csv(root, RDQ_COVERAGE, ("scope", "calendar_datadate_year", "metric", "field_name", "numerator", "denominator", "percentage", "min", "median", "p95", "p99", "max", "notes"), rdq_rows)
    _write_csv(root, ANNUAL_AVAILABILITY, ("scope", "calendar_datadate_year", "availability_status", "record_count", "denominator", "percentage", "q4_rdq_lag_days_min", "q4_rdq_lag_days_median", "q4_rdq_lag_days_p95", "q4_rdq_lag_days_max", "notes"), annual_rows)
    _write_csv(root, FLOW_SEMANTICS, ("field_name", "scope", "metric", "status", "count", "denominator", "percentage", "notes"), flow_rows)
    _write_csv(root, TTM_CAPABILITY, ("field_name", "scope", "metric", "count", "denominator", "percentage", "notes"), ttm_rows)
    _write_csv(root, DUPLICATE_CONFLICT, ("dataset", "logical_key", "raw_row_count", "missing_key_rows", "duplicate_key_groups", "duplicate_extra_rows", "exact_duplicate_key_groups", "conflicting_key_groups", "rdq_conflicting_duplicate_key_groups", "sample_conflicting_keys_json", "policy", "notes"), duplicate_rows)
    _write_csv(root, FIELD_CONTRACT, ("concept", "description", "raw_annual_field", "annual_field_present", "raw_quarterly_field", "quarterly_field_present", "stock_or_flow_semantics", "raw_unit_status", "sign_convention_status", "annual_pit_availability_rule", "quarterly_pit_availability_rule", "derivation_rule", "duplicate_conflict_handling", "known_limitation"), _field_contract_rows(annual_columns, quarterly_columns))
    _write_csv(root, SOURCE_EVIDENCE, ("evidence_id", "semantic_question", "status", "source_title", "provider", "source_url_or_local_ref", "access_date", "relevant_field_or_rule", "source_summary", "empirical_confirmation", "limitations"), _source_evidence_rows())
    _write_csv(root, ACCEPTANCE, ("check_id", "severity", "status", "evidence", "result"), acceptance_rows)
    _write_open_issues(root, annual_counts=annual_counts, duplicate_profiles=duplicate_profiles)
    result = {
        "verdict": verdict,
        "annual_inventory": annual_inventory,
        "quarterly_inventory": quarterly_inventory,
        "annual_counts": annual_counts,
        "duplicate_profiles": duplicate_profiles,
        "flow_summary": flow_summary,
        "ttm": ttm_summary,
        "linked_gvkey_count": len(linked_gvkeys),
        "linked_flow_profile": linked_flow_profile,
        "raw_hashes": after_hashes,
        "original_integrity": original_integrity_after,
    }
    _write_report(root, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=project_root())
    args = parser.parse_args(argv)
    result = run_phase2c(args.root.resolve())
    print(f"Gate 0 Phase 2C verdict: {result['verdict']}")
    return 0 if result["verdict"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
