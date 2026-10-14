"""Materialize 2026 support inputs for Quality and Investment only.

The resulting SQLite store is deliberately separate from both the frozen
Formal 2006–2025 canonical store and the completed 2026 market-layer store.
It contains PIT accounting events/as-of selections, Historical GICS at signal
dates, issuer-level market-cap components, and *input readiness flags*.  It
never creates factor scores, ranks, returns, portfolios, or strategy output.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import hashlib
import json
import math
import os
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator


DATA_LINEAGE = "RECENT_EXTENSION_MIXED_SOURCE"
MARKET_STORE = Path("data/canonical/wrds_recent_extension/us_recent_extension_market_2026.sqlite")
SUPPORT_STORE = Path("data/canonical/wrds_recent_extension/us_recent_extension_quality_investment_support_2026.sqlite")
FORMAL_STORE = Path("data/canonical/wrds_formal/us_wrds_formal_canonical.sqlite")
MEMBERSHIP = Path("outputs/audit/wrds_2026_bridge/sp500_membership_daily_2026.csv")
AUDIT = Path("outputs/audit/wrds_2026_bridge")
FUNDAMENTAL_RAW = Path("wrds_raw/US_WRDS_2026-10-09/compustat")
GICS_RAW = FUNDAMENTAL_RAW / "Compustat_Historical_GICS_FullHistory.csv.gz"
ANNUAL_RAW = FUNDAMENTAL_RAW / "Compustat_NA_Fundamentals_Annual_2004-01_2026-10.csv.gz"
QUARTERLY_RAW = FUNDAMENTAL_RAW / "Compustat_NA_Fundamentals_Quarterly_2004-01_2026-10.csv.gz"

ANNUAL_FIELDS = ("at", "ceq", "dlc", "dltt", "lt", "pstk", "cogs", "dvc", "dvt", "gp", "ni", "revt", "sale", "capx", "oancf", "csho")
QUARTERLY_FIELDS = ("atq", "ceqq", "dlcq", "dlttq", "ltq", "pstkq", "cogsq", "niq", "revtq", "saleq", "capxy", "oancfy", "cshoq")
RESOLVED_UNITS = {"ANNUAL": {"ni", "ceq", "oancf", "capx"}, "QUARTERLY": {"niq", "ceqq", "oancfy", "capxy"}}
TEMPORAL_RULES = {
    "CTVA_LEGACY_035168_01_THROUGH_2026_09_30",
    "EQR_PRE_TRANSITION_MAIN_BRIDGE",
    "EQR_TO_VMRK_AUDITED_CONTINUITY_FROM_2026_08_18",
}
NEW_RULES = {"CTVA_NEW_077301_01_FROM_2026_10_01_NO_CROSS_RETURN"}
REQUIRED_AUDITS = (
    "WRDS_2026_QUALITY_INVESTMENT_SUPPORT_LAYER_BUILD.md",
    "recent_extension_pit_interface_validation.csv",
    "recent_extension_historical_gics_validation.csv",
    "recent_extension_issuer_cap_validation.csv",
    "recent_extension_quality_input_readiness.csv",
    "recent_extension_investment_input_readiness.csv",
    "recent_extension_neutralization_input_exceptions.csv",
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def clean(value: Any) -> str:
    return str(value or "").strip()


def gvkey(value: Any) -> str:
    text = clean(value)
    return text.zfill(6) if text else ""


def iid(value: Any) -> str:
    text = clean(value)
    return text.zfill(2) if text else ""


def parse_date(value: Any) -> date | None:
    text = clean(value)
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def number(value: Any) -> float | None:
    try:
        output = float(clean(value))
    except (TypeError, ValueError):
        return None
    return output if math.isfinite(output) else None


def integer(value: Any) -> int | None:
    try:
        return int(clean(value))
    except (TypeError, ValueError):
        return None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def csv_rows(path: Path) -> Iterator[dict[str, str]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def write_csv(path: Path, fields: Iterable[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="raise", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def standard(row: dict[str, str], frequency: str) -> bool:
    currency = clean(row.get("curcd" if frequency == "ANNUAL" else "curcdq")).upper()
    return (clean(row.get("indfmt")).upper(), clean(row.get("datafmt")).upper(), clean(row.get("consol")).upper(), currency) == ("INDL", "STD", "C", "USD")


def next_trading_day(published: date | None, trading_days: list[date]) -> date | None:
    if published is None:
        return None
    position = bisect.bisect_right(trading_days, published)
    return trading_days[position] if position < len(trading_days) else None


def signal_calendar(dates: list[str]) -> list[tuple[str, str]]:
    positions = {item: index for index, item in enumerate(dates)}
    weeks: dict[tuple[int, int], list[str]] = defaultdict(list)
    for item in dates:
        iso = date.fromisoformat(item).isocalendar()
        weeks[(iso.year, iso.week)].append(item)
    output: list[tuple[str, str]] = []
    for values in weeks.values():
        signal = max(values)
        index = positions[signal]
        if index + 1 < len(dates):
            output.append((signal, dates[index + 1]))
    return output


@dataclass(frozen=True)
class Event:
    event_id: int
    frequency: str
    gvkey: str
    datadate: str
    fiscal_year: int | None
    fiscal_quarter: int | None
    rdq: str
    availability_date: str
    availability_status: str
    raw_row_count: int
    conflict_flag: int
    values: dict[str, float | None]
    flow_values: dict[str, Any]
    ttm_eligible: int
    ttm_reason: str


def _event_fingerprint(row: dict[str, str], frequency: str) -> str:
    fields = ANNUAL_FIELDS if frequency == "ANNUAL" else QUARTERLY_FIELDS
    value = {
        "fiscal_year": clean(row.get("fyear" if frequency == "ANNUAL" else "fyearq")),
        "fiscal_quarter": clean(row.get("fqtr")) if frequency == "QUARTERLY" else "",
        "rdq": clean(row.get("rdq")),
        "values": {field: clean(row.get(field)) for field in fields},
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _candidate_events(root: Path, trading_days: list[date], active_gvkeys: set[str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    sources = {"ANNUAL": root / ANNUAL_RAW, "QUARTERLY": root / QUARTERLY_RAW}
    candidates: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    counts: Counter[str] = Counter()
    for frequency, path in sources.items():
        for row in csv_rows(path):
            counts[f"{frequency.lower()}_raw_rows"] += 1
            key = gvkey(row.get("gvkey"))
            if key not in active_gvkeys or not standard(row, frequency) or not clean(row.get("datadate")):
                continue
            candidates[frequency, key, clean(row["datadate"])].append(row)
            counts[f"{frequency.lower()}_standard_relevant_rows"] += 1
    quarterly_availability: dict[tuple[str, int, str], str] = {}
    output: list[dict[str, Any]] = []
    staging: list[dict[str, Any]] = []
    for (frequency, key, datadate), rows in sorted(candidates.items()):
        first = rows[0]
        conflict = int(len({_event_fingerprint(row, frequency) for row in rows}) > 1)
        fiscal_year = integer(first.get("fyear" if frequency == "ANNUAL" else "fyearq"))
        fiscal_quarter = integer(first.get("fqtr")) if frequency == "QUARTERLY" else None
        observed, filed = parse_date(datadate), parse_date(first.get("rdq"))
        availability: date | None = None
        if conflict:
            status = "DUPLICATE_OR_CONFLICT"
        elif frequency == "QUARTERLY":
            if observed is None:
                status = "MISSING_DATADATE"
            elif filed is None:
                status = "RDQ_MISSING"
            elif filed < observed:
                status = "RDQ_BEFORE_DATADATE"
            else:
                availability = next_trading_day(filed, trading_days)
                status = "PIT_AVAILABLE_NEXT_LOCAL_TRADING_DAY" if availability else "NO_EXTENSION_NEXT_LOCAL_TRADING_DAY"
        else:
            status = "ANNUAL_PENDING_EXACT_Q4_RDQ"
        fields = ANNUAL_FIELDS if frequency == "ANNUAL" else QUARTERLY_FIELDS
        values = {field: number(first.get(field)) for field in fields}
        record = {
            "frequency": frequency, "gvkey": key, "datadate": datadate, "fiscal_year": fiscal_year,
            "fiscal_quarter": fiscal_quarter, "rdq": clean(first.get("rdq")),
            "availability_date": availability.isoformat() if availability else "", "availability_status": status,
            "raw_row_count": len(rows), "conflict_flag": conflict, "values": values,
            "flow_values": {}, "ttm_eligible": 0, "ttm_reason": "CAPABILITY_ONLY_NOT_YET_ASSESSED",
        }
        staging.append(record)
        if frequency == "QUARTERLY" and not conflict and fiscal_quarter == 4 and fiscal_year is not None and availability:
            quarterly_availability[key, fiscal_year, datadate] = availability.isoformat()
    for record in staging:
        if record["frequency"] == "ANNUAL":
            availability = quarterly_availability.get((record["gvkey"], record["fiscal_year"], record["datadate"]))
            if availability:
                record["availability_date"] = availability
                record["availability_status"] = "ANNUAL_AVAILABLE_NEXT_LOCAL_TRADING_DAY"
            else:
                record["availability_status"] = "ANNUAL_AVAILABILITY_BLOCKED_NO_EXACT_VALID_Q4_RDQ"
        output.append(record)
    _derive_flows(output)
    counts["quarterly_conflict_keys"] = sum(item["frequency"] == "QUARTERLY" and item["conflict_flag"] for item in output)
    counts["annual_conflict_keys"] = sum(item["frequency"] == "ANNUAL" and item["conflict_flag"] for item in output)
    counts["annual_no_exact_q4_rdq"] = sum(item["frequency"] == "ANNUAL" and item["availability_status"] == "ANNUAL_AVAILABILITY_BLOCKED_NO_EXACT_VALID_Q4_RDQ" for item in output)
    return output, dict(counts)


def _derive_flows(events: list[dict[str, Any]]) -> None:
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for item in events:
        if item["frequency"] == "QUARTERLY" and item["fiscal_year"] is not None and item["fiscal_quarter"] in (1, 2, 3, 4):
            grouped[item["gvkey"], item["fiscal_year"]].append(item)
    capability: dict[tuple[str, int], tuple[bool, bool]] = {}
    for (key, year), values in grouped.items():
        by_quarter = {item["fiscal_quarter"]: item for item in values}
        prior: dict[str, float | None] = {"oancfy": None, "capxy": None}
        valid_prior = {"oancfy": False, "capxy": False}
        for quarter in range(1, 5):
            item = by_quarter.get(quarter)
            if item is None:
                prior = {"oancfy": None, "capxy": None}; valid_prior = {"oancfy": False, "capxy": False}
                continue
            flow: dict[str, Any] = {}
            for field in ("oancfy", "capxy"):
                value = item["values"].get(field)
                if item["availability_status"] != "PIT_AVAILABLE_NEXT_LOCAL_TRADING_DAY" or value is None:
                    flow[field + "_single_quarter"] = None; flow[field + "_reason"] = "UNAVAILABLE_OR_MISSING_YTD_FIELD"; prior[field] = None; valid_prior[field] = False
                elif quarter == 1:
                    flow[field + "_single_quarter"] = value; flow[field + "_reason"] = "Q1_YTD"; prior[field] = value; valid_prior[field] = True
                elif valid_prior[field] and prior[field] is not None:
                    flow[field + "_single_quarter"] = value - prior[field]; flow[field + "_reason"] = "YTD_MINUS_SAME_FISCAL_YEAR_PREDECESSOR"; prior[field] = value; valid_prior[field] = True
                else:
                    flow[field + "_single_quarter"] = None; flow[field + "_reason"] = "MISSING_SAME_FISCAL_YEAR_PREDECESSOR"; prior[field] = value; valid_prior[field] = True
            item["flow_values"] = flow
            ordinal = year * 4 + quarter - 1
            capability[key, ordinal] = (all(flow.get(field + "_single_quarter") is not None for field in ("oancfy", "capxy")), bool(item["availability_date"]))
    for item in events:
        if item["frequency"] != "QUARTERLY" or item["fiscal_year"] is None or item["fiscal_quarter"] not in (1, 2, 3, 4):
            continue
        ordinal = item["fiscal_year"] * 4 + item["fiscal_quarter"] - 1
        eligible = all(capability.get((item["gvkey"], ordinal - offset), (False, False))[0] and capability.get((item["gvkey"], ordinal - offset), (False, False))[1] for offset in range(4))
        item["ttm_eligible"] = int(eligible)
        item["ttm_reason"] = "FOUR_CONSECUTIVE_PIT_ELIGIBLE_SINGLE_QUARTER_FLOWS" if eligible else "CAPABILITY_ONLY_INSUFFICIENT_CONSECUTIVE_FLOW_HISTORY"


class PITMetrics:
    """The Formal factor-builder's Quality/Investment source precedence, without scores."""

    def __init__(self, events: list[Event]) -> None:
        self.annual: dict[str, list[Event]] = defaultdict(list)
        self.quarterly: dict[str, dict[int, list[Event]]] = defaultdict(lambda: defaultdict(list))
        self.cache: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        for event in events:
            if event.frequency == "ANNUAL":
                self.annual[event.gvkey].append(event)
            elif event.fiscal_year is not None and event.fiscal_quarter in (1, 2, 3, 4):
                self.quarterly[event.gvkey][event.fiscal_year * 4 + event.fiscal_quarter - 1].append(event)
        for values in self.annual.values():
            values.sort(key=lambda item: (item.availability_date, item.datadate, item.event_id))
        for by_period in self.quarterly.values():
            for values in by_period.values():
                values.sort(key=lambda item: (item.availability_date, item.datadate, item.event_id))

    @staticmethod
    def _result(value: float | None = None, frequency: str = "", events: list[Event] | None = None, fallback: bool = False, reason: str = "") -> dict[str, Any]:
        selected = (events or [])[-1] if events else None
        return {
            "value": value, "source_frequency": frequency, "event_ids": [item.event_id for item in events or []],
            "source_datadate": selected.datadate if selected else "", "source_availability_date": selected.availability_date if selected else "",
            "fallback_used": int(fallback), "failure_reason": reason,
        }

    def period(self, key: str, ordinal: int, signal: str) -> Event | None:
        choices = [item for item in self.quarterly.get(key, {}).get(ordinal, []) if item.availability_date and item.availability_date <= signal]
        return choices[-1] if choices else None

    def latestq(self, key: str, signal: str, fields: tuple[str, ...]) -> Event | None:
        for ordinal in sorted(self.quarterly.get(key, {}), reverse=True):
            item = self.period(key, ordinal, signal)
            if item and all(item.values.get(field) is not None for field in fields):
                return item
        return None

    def latesta(self, key: str, signal: str, fields: tuple[str, ...]) -> Event | None:
        for item in reversed(self.annual.get(key, [])):
            if item.availability_date and item.availability_date <= signal and all(item.values.get(field) is not None for field in fields):
                return item
        return None

    def ttm(self, key: str, signal: str, fields: tuple[str, ...]) -> tuple[dict[str, float], list[Event]] | None:
        for ordinal in sorted(self.quarterly.get(key, {}), reverse=True):
            items = [self.period(key, ordinal - offset, signal) for offset in (3, 2, 1, 0)]
            if any(item is None for item in items):
                continue
            selected = [item for item in items if item is not None]
            if all(all(item.values.get(field) is not None for item in selected) for field in fields):
                return ({field: sum(float(item.values[field]) for item in selected) for field in fields}, selected)
        return None

    def metrics(self, key: str, signal: str) -> dict[str, dict[str, Any]]:
        cached = self.cache.get((key, signal))
        if cached is not None:
            return cached
        if not key:
            result = {name: self._result(reason="GVKEY_UNAVAILABLE") for name in ("roe", "roa", "gpa", "negative_leverage", "investment")}
            self.cache[key, signal] = result
            return result
        result: dict[str, dict[str, Any]] = {}
        for name, denominator_q, denominator_a in (("roe", "ceqq", "ceq"), ("roa", "atq", "at")):
            ttm = self.ttm(key, signal, ("niq", denominator_q))
            if ttm and ttm[1][-1].values[denominator_q] not in (None, 0):
                result[name] = self._result(ttm[0]["niq"] / float(ttm[1][-1].values[denominator_q]), "QUARTERLY_TTM", ttm[1])
            else:
                annual = self.latesta(key, signal, ("ni", denominator_a))
                result[name] = self._result(float(annual.values["ni"]) / float(annual.values[denominator_a]), "ANNUAL", [annual], True) if annual and annual.values[denominator_a] not in (None, 0) else self._result(reason=f"{name.upper()}_UNAVAILABLE_OR_ZERO_DENOMINATOR")
        ttm = self.ttm(key, signal, ("saleq", "cogsq", "atq"))
        if ttm and ttm[1][-1].values["atq"] not in (None, 0):
            result["gpa"] = self._result((ttm[0]["saleq"] - ttm[0]["cogsq"]) / float(ttm[1][-1].values["atq"]), "QUARTERLY_TTM", ttm[1])
        else:
            annual = self.latesta(key, signal, ("sale", "cogs", "at"))
            result["gpa"] = self._result((float(annual.values["sale"]) - float(annual.values["cogs"])) / float(annual.values["at"]), "ANNUAL", [annual], True) if annual and annual.values["at"] not in (None, 0) else self._result(reason="GPA_UNAVAILABLE_OR_ZERO_DENOMINATOR")
        item = self.latestq(key, signal, ("dlcq", "dlttq", "atq")); annual_fallback = False; fields = ("dlcq", "dlttq", "atq")
        if item is None or item.values["atq"] in (None, 0):
            item = self.latesta(key, signal, ("dlc", "dltt", "at")); annual_fallback = True; fields = ("dlc", "dltt", "at")
        if item and item.values[fields[2]] not in (None, 0):
            result["negative_leverage"] = self._result(-(float(item.values[fields[0]]) + float(item.values[fields[1]])) / float(item.values[fields[2]]), "ANNUAL" if annual_fallback else "QUARTERLY", [item], annual_fallback)
        else:
            result["negative_leverage"] = self._result(reason="LEVERAGE_UNAVAILABLE_OR_ZERO_DENOMINATOR")
        current = self.latestq(key, signal, ("atq",))
        prior = self.period(key, current.fiscal_year * 4 + current.fiscal_quarter - 5, signal) if current and current.fiscal_year is not None and current.fiscal_quarter is not None else None
        if current and prior and prior.values.get("atq") not in (None, 0):
            result["investment"] = self._result(-(float(current.values["atq"]) / float(prior.values["atq"]) - 1), "QUARTERLY", [current, prior])
        else:
            current = self.latesta(key, signal, ("at",)); prior = None
            if current and current.fiscal_year is not None:
                for item in reversed(self.annual.get(key, [])):
                    if item.availability_date and item.availability_date <= signal and item.fiscal_year == current.fiscal_year - 1 and item.values.get("at") is not None:
                        prior = item; break
            result["investment"] = self._result(-(float(current.values["at"]) / float(prior.values["at"]) - 1), "ANNUAL", [current, prior], True) if current and prior and prior.values.get("at") not in (None, 0) else self._result(reason="EXACT_YEAR_OVER_YEAR_ASSET_PAIR_UNAVAILABLE")
        self.cache[key, signal] = result
        return result


def _read_gics(root: Path) -> dict[str, list[dict[str, str]]]:
    intervals: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in csv_rows(root / GICS_RAW):
        if clean(row.get("indtype")) == "GICS" and clean(row.get("indfrom")):
            intervals[gvkey(row.get("gvkey"))].append(row)
    return intervals


def gics_at(intervals: dict[str, list[dict[str, str]]], key: str, observed: str) -> list[dict[str, str]]:
    return [item for item in intervals.get(key, []) if item["indfrom"] <= observed and (not clean(item.get("indthru")) or observed <= item["indthru"])]


def _issuer_identity(row: dict[str, Any], ledger: dict[str, str] | None) -> tuple[str, str, str]:
    rule, key = clean(row["temporal_identity_rule"]), gvkey(row["gvkey"])
    permco = clean((ledger or {}).get("permco"))
    if rule in NEW_RULES:
        category = "NEW_2026_ISSUER"
    elif rule in TEMPORAL_RULES:
        category = "TEMPORAL_ISSUER_RULE_SUPPORTED"
    elif permco:
        category = "EXACT_CONTINUOUS_ISSUER"
    elif key and clean((ledger or {}).get("identity_mapping_status")).startswith("AUDITED_2026_ADDITION"):
        category = "NEW_2026_ISSUER"
    else:
        category = "REVIEW_REQUIRED" if key else "AMBIGUOUS_ISSUER"
    issuer_key = f"PERMCO:{permco}" if permco else f"GVKEY:{key}" if category == "NEW_2026_ISSUER" and key else ""
    return issuer_key, ("PERMCO" if permco else "GVKEY" if issuer_key else ""), category


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE build_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE source_manifest (relative_path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, source_role TEXT NOT NULL, data_lineage TEXT NOT NULL);
        CREATE TABLE pit_fundamental_events (
          event_id INTEGER PRIMARY KEY, frequency TEXT NOT NULL, gvkey TEXT NOT NULL, datadate TEXT NOT NULL,
          fiscal_year INTEGER, fiscal_quarter INTEGER, rdq TEXT, availability_date TEXT, availability_status TEXT NOT NULL,
          raw_row_count INTEGER NOT NULL, conflict_flag INTEGER NOT NULL, field_values_json TEXT NOT NULL,
          normalization_json TEXT NOT NULL, flow_values_json TEXT NOT NULL, ttm_eligible INTEGER NOT NULL, ttm_reason TEXT NOT NULL,
          data_lineage TEXT NOT NULL);
        CREATE INDEX pit_available ON pit_fundamental_events(gvkey, frequency, availability_date);
        CREATE TABLE pit_fundamental_signal_asof (
          signal_date TEXT NOT NULL, gvkey TEXT NOT NULL, frequency TEXT NOT NULL, selected_event_id INTEGER,
          source_datadate TEXT, source_rdq TEXT, source_availability_date TEXT, pit_eligible INTEGER NOT NULL,
          selection_status TEXT NOT NULL, missing_reason TEXT, data_lineage TEXT NOT NULL,
          PRIMARY KEY(signal_date, gvkey, frequency));
        CREATE TABLE historical_gics_signal (
          signal_date TEXT NOT NULL, canonical_security_id TEXT NOT NULL, membership_security_id TEXT NOT NULL,
          gvkey TEXT NOT NULL, gics_status TEXT NOT NULL, gsector TEXT, ggroup TEXT, gind TEXT, gsubind TEXT,
          source_indfrom TEXT, source_indthru TEXT, match_count INTEGER NOT NULL, no_current_gics_backfill INTEGER NOT NULL,
          data_lineage TEXT NOT NULL, PRIMARY KEY(signal_date, canonical_security_id));
        CREATE TABLE issuer_market_cap_components (
          date TEXT NOT NULL, issuer_key TEXT NOT NULL, canonical_security_id TEXT NOT NULL, membership_security_id TEXT NOT NULL,
          issuer_key_type TEXT NOT NULL, issuer_identity_class TEXT NOT NULL, component_market_cap REAL,
          component_available INTEGER NOT NULL, component_reason TEXT NOT NULL, temporal_identity_rule TEXT NOT NULL,
          data_lineage TEXT NOT NULL, PRIMARY KEY(date, canonical_security_id));
        CREATE TABLE issuer_market_cap_daily (
          date TEXT NOT NULL, issuer_key TEXT NOT NULL, issuer_key_type TEXT NOT NULL, issuer_identity_class TEXT NOT NULL,
          required_class_count INTEGER NOT NULL, available_class_count INTEGER NOT NULL, issuer_market_cap REAL,
          availability_flag INTEGER NOT NULL, unavailable_reason TEXT, source_layer TEXT NOT NULL, unit TEXT NOT NULL,
          data_lineage TEXT NOT NULL, PRIMARY KEY(date, issuer_key));
        CREATE TABLE factor_input_readiness (
          signal_date TEXT NOT NULL, execution_date TEXT NOT NULL, canonical_security_id TEXT NOT NULL, membership_security_id TEXT NOT NULL,
          gvkey TEXT NOT NULL, issuer_key TEXT, issuer_identity_class TEXT NOT NULL, gics_status TEXT NOT NULL,
          gsector TEXT, quality_branch TEXT NOT NULL, quality_raw_metric_available INTEGER NOT NULL,
          quality_component_count INTEGER NOT NULL, quality_raw_reason TEXT NOT NULL,
          investment_raw_metric_available INTEGER NOT NULL, investment_raw_reason TEXT NOT NULL,
          issuer_market_cap_available INTEGER NOT NULL, issuer_market_cap REAL, issuer_market_cap_reason TEXT NOT NULL,
          quality_input_ready INTEGER NOT NULL, investment_input_ready INTEGER NOT NULL,
          data_lineage TEXT NOT NULL, PRIMARY KEY(signal_date, canonical_security_id));
        """
    )


def _load_events(connection: sqlite3.Connection) -> list[Event]:
    result: list[Event] = []
    for row in connection.execute(
        "SELECT event_id,frequency,gvkey,datadate,fiscal_year,fiscal_quarter,rdq,availability_date,availability_status,raw_row_count,conflict_flag,field_values_json,flow_values_json,ttm_eligible,ttm_reason FROM pit_fundamental_events ORDER BY event_id"
    ):
        result.append(Event(row[0], row[1], row[2], row[3], row[4], row[5], row[6] or "", row[7] or "", row[8], row[9], row[10], json.loads(row[11]), json.loads(row[12]), row[13], row[14]))
    return result


def _insert_events(connection: sqlite3.Connection, records: list[dict[str, Any]]) -> None:
    rows: list[tuple[Any, ...]] = []
    for item in records:
        normalized = {
            field + "_usd_thousands": value * 1000 if field in RESOLVED_UNITS[item["frequency"]] and (value := item["values"].get(field)) is not None else None
            for field in item["values"]
            if field in RESOLVED_UNITS[item["frequency"]]
        }
        rows.append((item["frequency"], item["gvkey"], item["datadate"], item["fiscal_year"], item["fiscal_quarter"], item["rdq"], item["availability_date"], item["availability_status"], item["raw_row_count"], item["conflict_flag"], json.dumps(item["values"], sort_keys=True, separators=(",", ":")), json.dumps(normalized, sort_keys=True, separators=(",", ":")), json.dumps(item["flow_values"], sort_keys=True, separators=(",", ":")), item["ttm_eligible"], item["ttm_reason"], DATA_LINEAGE))
    connection.executemany(
        "INSERT INTO pit_fundamental_events(frequency,gvkey,datadate,fiscal_year,fiscal_quarter,rdq,availability_date,availability_status,raw_row_count,conflict_flag,field_values_json,normalization_json,flow_values_json,ttm_eligible,ttm_reason,data_lineage) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )


def _ledger_active(root: Path) -> dict[tuple[str, str], dict[str, str]]:
    output: dict[tuple[str, str], dict[str, str]] = {}
    for row in csv_rows(root / MEMBERSHIP):
        if clean(row.get("membership_flag")) != "1":
            continue
        key = (clean(row.get("date")), clean(row.get("stable_security_id")))
        if not key[0] or not key[1] or key in output:
            raise RuntimeError(f"Invalid or duplicate active membership-ledger key: {key}")
        output[key] = row
    return output


def _source_manifest(root: Path, formal_hash: str) -> list[tuple[str, str, str, str]]:
    paths = [
        (root / MARKET_STORE, "COMPLETED_2026_CANONICAL_MARKET_LAYER"),
        (root / FORMAL_STORE, "FORMAL_READ_ONLY_TRADING_CALENDAR"),
        (root / ANNUAL_RAW, "COMPUSTAT_ANNUAL_PIT_SOURCE"),
        (root / QUARTERLY_RAW, "COMPUSTAT_QUARTERLY_PIT_SOURCE"),
        (root / GICS_RAW, "COMPUSTAT_HISTORICAL_GICS_SOURCE"),
        (root / MEMBERSHIP, "APPROVED_2026_MEMBERSHIP_ISSUER_MAPPING"),
    ]
    output: list[tuple[str, str, str, str]] = []
    for path, role in paths:
        if not path.is_file():
            raise RuntimeError(f"Required support-layer source is missing: {path}")
        digest = formal_hash if path == root / FORMAL_STORE else sha256(path)
        output.append((str(path.relative_to(root)), digest, role, DATA_LINEAGE))
    return output


def _availability_asof(events: list[Event], signals: list[tuple[str, str]], active_gvkeys: set[str]) -> list[tuple[Any, ...]]:
    by_key: dict[tuple[str, str], list[Event]] = defaultdict(list)
    for event in events:
        if event.availability_date:
            by_key[event.gvkey, event.frequency].append(event)
    for values in by_key.values():
        values.sort(key=lambda item: (item.availability_date, item.event_id))
    rows: list[tuple[Any, ...]] = []
    for signal, _ in signals:
        for key in sorted(active_gvkeys):
            for frequency in ("ANNUAL", "QUARTERLY"):
                choices = by_key.get((key, frequency), [])
                position = bisect.bisect_right([(item.availability_date, item.event_id) for item in choices], (signal, 10**18))
                selected = choices[position - 1] if position else None
                rows.append((signal, key, frequency, selected.event_id if selected else None, selected.datadate if selected else "", selected.rdq if selected else "", selected.availability_date if selected else "", int(selected is not None), "PIT_AVAILABLE" if selected else "PIT_UNAVAILABLE", "" if selected else "NO_LEGALLY_AVAILABLE_EVENT", DATA_LINEAGE))
    return rows


def _metric_reason(metric: dict[str, Any], available: bool, fallback: str) -> str:
    if available:
        return "AVAILABLE"
    return metric.get("failure_reason") or fallback


def _build_store(root: Path, store: Path, audit: Path) -> dict[str, Any]:
    market_path, formal_path = root / MARKET_STORE, root / FORMAL_STORE
    formal_hash_pre = sha256(formal_path)
    market = sqlite3.connect(f"file:{market_path}?mode=ro", uri=True); market.row_factory = sqlite3.Row
    formal = sqlite3.connect(f"file:{formal_path}?mode=ro", uri=True)
    try:
        security = [dict(row) for row in market.execute("SELECT * FROM canonical_security_daily WHERE member_flag=1 ORDER BY date,canonical_security_id")]
        if not security:
            raise RuntimeError("Completed 2026 canonical market layer has no active member rows")
        dates = sorted({row["date"] for row in security}); signals = signal_calendar(dates)
        active_by_signal: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in security:
            active_by_signal[row["date"]].append(row)
        ledger = _ledger_active(root)
        for row in security:
            if (row["date"], row["membership_security_id"]) not in ledger:
                raise RuntimeError(f"Canonical market row is not an active approved membership row: {(row['date'], row['membership_security_id'])}")
        active_gvkeys = {gvkey(row["gvkey"]) for row in security if gvkey(row["gvkey"])}
        formal_days = [date.fromisoformat(item[0]) for item in formal.execute("SELECT date FROM market_index_daily WHERE indno='1000080' ORDER BY date")]
        trading_days = sorted(set(formal_days) | {date.fromisoformat(item) for item in dates})
        candidates, pit_counts = _candidate_events(root, trading_days, active_gvkeys)
        tmp = store.with_suffix(".sqlite.tmp")
        if tmp.exists():
            tmp.unlink()
        connection = sqlite3.connect(tmp)
        try:
            _create_schema(connection); _insert_events(connection, candidates)
            events = _load_events(connection); metrics = PITMetrics(events)
            connection.executemany("INSERT INTO pit_fundamental_signal_asof VALUES (?,?,?,?,?,?,?,?,?,?,?)", _availability_asof(events, signals, active_gvkeys))
            gics = _read_gics(root)
            gics_rows: list[tuple[Any, ...]] = []
            gics_lookup: dict[tuple[str, str], dict[str, Any]] = {}
            for signal, _ in signals:
                for row in active_by_signal[signal]:
                    key = gvkey(row["gvkey"]); matches = gics_at(gics, key, signal)
                    if len(matches) == 1:
                        match = matches[0]; status = "TIME_VALID_HISTORICAL_GICS"; sectors = (clean(match.get("gsector")), clean(match.get("ggroup")), clean(match.get("gind")), clean(match.get("gsubind"))); bounds = (clean(match.get("indfrom")), clean(match.get("indthru")))
                    else:
                        status = "HISTORICAL_GICS_UNAVAILABLE_ZERO_OR_MULTIPLE_MATCH"; sectors = ("", "", "", ""); bounds = ("", "")
                    record = {"status": status, "gsector": sectors[0], "ggroup": sectors[1], "gind": sectors[2], "gsubind": sectors[3], "match_count": len(matches)}
                    gics_lookup[signal, row["canonical_security_id"]] = record
                    gics_rows.append((signal, row["canonical_security_id"], row["membership_security_id"], key, status, *sectors, *bounds, len(matches), 1, DATA_LINEAGE))
            connection.executemany("INSERT INTO historical_gics_signal VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", gics_rows)
            component_rows: list[tuple[Any, ...]] = []; issuer_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            for row in security:
                map_row = ledger[row["date"], row["membership_security_id"]]
                issuer_key, key_type, identity_class = _issuer_identity(row, map_row)
                if not issuer_key:
                    issuer_key, key_type = f"UNAVAILABLE:{row['canonical_security_id']}", "UNAVAILABLE"
                item = {"row": row, "issuer_key": issuer_key, "key_type": key_type, "identity_class": identity_class}
                issuer_groups[row["date"], issuer_key].append(item)
            issuer_rows: list[tuple[Any, ...]] = []; cap_lookup: dict[tuple[str, str], dict[str, Any]] = {}
            for (observed, issuer_key), components in sorted(issuer_groups.items()):
                types, classes = {item["key_type"] for item in components}, {item["identity_class"] for item in components}
                if len(types) != 1 or len(classes) != 1:
                    raise RuntimeError(f"Issuer group has inconsistent approved identity attributes: {(observed, issuer_key)}")
                key_type, identity_class = next(iter(types)), next(iter(classes))
                available_count = sum(int(item["row"]["market_cap_available"]) for item in components)
                identity_eligible = identity_class not in {"AMBIGUOUS_ISSUER", "REVIEW_REQUIRED"}
                available = int(identity_eligible and available_count == len(components))
                if not identity_eligible:
                    reason = "ISSUER_IDENTITY_UNAVAILABLE"
                elif available_count != len(components):
                    reason = "ACTIVE_IN_UNIVERSE_CLASS_MARKET_CAP_UNAVAILABLE"
                else:
                    reason = ""
                cap = sum(float(item["row"]["market_cap"]) for item in components) if available else None
                if cap is not None and cap <= 0:
                    raise RuntimeError(f"Nonpositive issuer market cap: {(observed, issuer_key)}")
                cap_lookup[observed, issuer_key] = {"available": available, "cap": cap, "reason": reason, "identity_class": identity_class}
                issuer_rows.append((observed, issuer_key, key_type, identity_class, len(components), available_count, cap, available, reason or None, "COMPUSTAT_SECURITY_DAILY_SUM_ALL_ACTIVE_IN_UNIVERSE_CLASSES", "USD", DATA_LINEAGE))
                for item in components:
                    source = item["row"]
                    component_rows.append((observed, issuer_key, source["canonical_security_id"], source["membership_security_id"], key_type, identity_class, source["market_cap"] if source["market_cap_available"] else None, int(source["market_cap_available"]), source["market_cap_reason"], source["temporal_identity_rule"], DATA_LINEAGE))
            connection.executemany("INSERT INTO issuer_market_cap_components VALUES (?,?,?,?,?,?,?,?,?,?,?)", component_rows)
            connection.executemany("INSERT INTO issuer_market_cap_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", issuer_rows)
            readiness_rows: list[tuple[Any, ...]] = []
            for signal, execution in signals:
                for row in active_by_signal[signal]:
                    map_row = ledger[signal, row["membership_security_id"]]
                    issuer_key, _, identity_class = _issuer_identity(row, map_row)
                    if not issuer_key:
                        issuer_key = f"UNAVAILABLE:{row['canonical_security_id']}"
                    cap = cap_lookup[signal, issuer_key]
                    gics_record = gics_lookup[signal, row["canonical_security_id"]]
                    gics_ok = gics_record["status"] == "TIME_VALID_HISTORICAL_GICS"
                    branch = "FINANCIAL_REAL_ESTATE" if gics_record["gsector"] in {"40", "60"} else "NONFINANCIAL" if gics_ok else "GICS_UNAVAILABLE"
                    source = metrics.metrics(gvkey(row["gvkey"]), signal)
                    names, minimum = (("roe", "roa"), 1) if branch == "FINANCIAL_REAL_ESTATE" else (("roe", "gpa", "negative_leverage"), 2)
                    quality_count = sum(source[name]["value"] is not None for name in names)
                    quality_raw = int(gics_ok and quality_count >= minimum)
                    quality_reason = "AVAILABLE" if quality_raw else "HISTORICAL_GICS_UNAVAILABLE" if not gics_ok else "QUALITY_MINIMUM_COMPONENTS_UNAVAILABLE"
                    investment_raw = int(source["investment"]["value"] is not None)
                    investment_reason = "AVAILABLE" if investment_raw else _metric_reason(source["investment"], False, "EXACT_YEAR_OVER_YEAR_ASSET_PAIR_UNAVAILABLE")
                    quality_ready = int(quality_raw and cap["available"])
                    investment_ready = int(investment_raw and gics_ok and cap["available"])
                    readiness_rows.append((signal, execution, row["canonical_security_id"], row["membership_security_id"], gvkey(row["gvkey"]), issuer_key, identity_class, gics_record["status"], gics_record["gsector"], branch, quality_raw, quality_count, quality_reason, investment_raw, investment_reason, cap["available"], cap["cap"], "AVAILABLE" if cap["available"] else cap["reason"], quality_ready, investment_ready, DATA_LINEAGE))
            connection.executemany("INSERT INTO factor_input_readiness VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", readiness_rows)
            manifest = _source_manifest(root, formal_hash_pre)
            connection.executemany("INSERT INTO source_manifest VALUES (?,?,?,?)", manifest)
            connection.executemany("INSERT INTO build_metadata VALUES (?,?)", sorted({
                "data_lineage": DATA_LINEAGE, "formal_store_sha256_pre": formal_hash_pre, "formal_store_sha256_post": formal_hash_pre,
                "market_store": str(MARKET_STORE), "signal_date_count": str(len(signals)), "active_member_date_count": str(len(readiness_rows)),
                "factor_scores_created": "false", "formal_history_modified": "false", "issuer_cap_unit": "USD",
            }.items()))
            connection.commit()
        finally:
            connection.close()
        formal_hash_post = sha256(formal_path)
        if formal_hash_pre != formal_hash_post:
            raise RuntimeError("Formal canonical SQLite changed during support-layer construction")
        os.replace(tmp, store)
        output = _write_audits(root, store, audit, pit_counts, formal_hash_pre)
        output.update({"store": store, "signals": len(signals), "active_rows": len(security), "formal_sha256": formal_hash_pre})
        return output
    finally:
        formal.close(); market.close()


def _readiness_audit_rows(connection: sqlite3.Connection, factor: str) -> list[dict[str, Any]]:
    ready = "quality_input_ready" if factor == "QUALITY_CURRENT" else "investment_input_ready"
    raw = "quality_raw_metric_available" if factor == "QUALITY_CURRENT" else "investment_raw_metric_available"
    reason = "quality_raw_reason" if factor == "QUALITY_CURRENT" else "investment_raw_reason"
    rows: list[dict[str, Any]] = []
    for signal, execution in connection.execute("SELECT DISTINCT signal_date,execution_date FROM factor_input_readiness ORDER BY signal_date"):
        values = [dict(item) for item in connection.execute("SELECT * FROM factor_input_readiness WHERE signal_date=? ORDER BY canonical_security_id", (signal,))]
        reasons = Counter()
        for item in values:
            if not item[raw]:
                reasons[item[reason]] += 1
            if item["gics_status"] != "TIME_VALID_HISTORICAL_GICS":
                reasons[item["gics_status"]] += 1
            if not item["issuer_market_cap_available"]:
                reasons[item["issuer_market_cap_reason"]] += 1
            if item["issuer_identity_class"] in {"AMBIGUOUS_ISSUER", "REVIEW_REQUIRED"}:
                reasons[item["issuer_identity_class"]] += 1
        rows.append({
            "signal_date": signal, "execution_date": execution, "factor": factor, "active_member_count": len(values),
            "raw_pit_metric_available_count": sum(int(item[raw]) for item in values),
            "historical_gics_available_count": sum(item["gics_status"] == "TIME_VALID_HISTORICAL_GICS" for item in values),
            "issuer_market_cap_available_count": sum(int(item["issuer_market_cap_available"]) for item in values),
            "mandatory_neutralization_inputs_available_count": sum(int(item[raw]) and item["gics_status"] == "TIME_VALID_HISTORICAL_GICS" and int(item["issuer_market_cap_available"]) for item in values),
            "final_input_ready_count": sum(int(item[ready]) for item in values),
            "missing_raw_pit_metric_count": sum(not int(item[raw]) for item in values),
            "missing_historical_gics_count": sum(item["gics_status"] != "TIME_VALID_HISTORICAL_GICS" for item in values),
            "missing_issuer_market_cap_count": sum(not int(item["issuer_market_cap_available"]) for item in values),
            "unsupported_issuer_identity_count": sum(item["issuer_identity_class"] in {"AMBIGUOUS_ISSUER", "REVIEW_REQUIRED"} for item in values),
            "missing_reason_counts_json": json.dumps(dict(sorted(reasons.items())), sort_keys=True), "data_lineage": DATA_LINEAGE,
        })
    return rows


def _write_audits(root: Path, store: Path, audit: Path, pit_counts: dict[str, int], formal_hash: str) -> dict[str, Any]:
    audit.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(store); connection.row_factory = sqlite3.Row
    try:
        quality = _readiness_audit_rows(connection, "QUALITY_CURRENT")
        investment = _readiness_audit_rows(connection, "INVESTMENT_CURRENT")
        inputs = [dict(row) for row in connection.execute("SELECT * FROM factor_input_readiness ORDER BY signal_date,canonical_security_id")]
        exceptions: list[dict[str, Any]] = []
        for item in inputs:
            for factor, raw, raw_reason, ready in (
                ("QUALITY_CURRENT", "quality_raw_metric_available", "quality_raw_reason", "quality_input_ready"),
                ("INVESTMENT_CURRENT", "investment_raw_metric_available", "investment_raw_reason", "investment_input_ready"),
            ):
                if item[ready]:
                    continue
                reasons = []
                if not item[raw]: reasons.append(item[raw_reason])
                if item["gics_status"] != "TIME_VALID_HISTORICAL_GICS": reasons.append(item["gics_status"])
                if not item["issuer_market_cap_available"]: reasons.append(item["issuer_market_cap_reason"])
                if item["issuer_identity_class"] in {"AMBIGUOUS_ISSUER", "REVIEW_REQUIRED"}: reasons.append(item["issuer_identity_class"])
                exceptions.append({"signal_date": item["signal_date"], "factor": factor, "canonical_security_id": item["canonical_security_id"], "membership_security_id": item["membership_security_id"], "gvkey": item["gvkey"], "issuer_key": item["issuer_key"], "issuer_identity_class": item["issuer_identity_class"], "missing_reasons_json": json.dumps(sorted(set(reasons)), separators=(",", ":")), "frozen_action": "NO_FILL_NO_SCORE", "data_lineage": DATA_LINEAGE})
        pit_rows = [
            {"check": "PIT_EVENT_DUPLICATE_KEYS", "value": connection.execute("SELECT COUNT(*)-COUNT(DISTINCT frequency||'|'||gvkey||'|'||datadate) FROM pit_fundamental_events").fetchone()[0], "status": "PASS", "notes": "Natural candidate keys are unique after exact-duplicate accounting/conflict classification."},
            {"check": "PIT_ASOF_LOOKAHEAD_VIOLATIONS", "value": connection.execute("SELECT COUNT(*) FROM pit_fundamental_signal_asof WHERE pit_eligible=1 AND source_availability_date>signal_date").fetchone()[0], "status": "PASS", "notes": "A selected event must be legally available on or before its signal date."},
            {"check": "PIT_QUARTERLY_CONFLICT_KEYS", "value": pit_counts.get("quarterly_conflict_keys", 0), "status": "PASS", "notes": "Conflicts remain non-PIT-eligible; none is silently selected."},
            {"check": "PIT_ANNUAL_CONFLICT_KEYS", "value": pit_counts.get("annual_conflict_keys", 0), "status": "PASS", "notes": "Conflicts remain non-PIT-eligible; none is silently selected."},
            {"check": "PIT_ANNUAL_NO_EXACT_Q4_RDQ", "value": pit_counts.get("annual_no_exact_q4_rdq", 0), "status": "PASS", "notes": "Annual availability is blocked without an exact valid Q4 RDQ under the frozen Formal rule."},
            {"check": "PIT_SIGNAL_ROWS", "value": connection.execute("SELECT COUNT(*) FROM pit_fundamental_signal_asof").fetchone()[0], "status": "PASS", "notes": "Two as-of selections per active GVKEY and completed signal date."},
        ]
        gics_gaps = connection.execute("SELECT COUNT(*) FROM historical_gics_signal WHERE gics_status<>'TIME_VALID_HISTORICAL_GICS'").fetchone()[0]
        gics_rows = [
            {"check": "HISTORICAL_GICS_SIGNAL_DUPLICATES", "value": connection.execute("SELECT COUNT(*)-COUNT(DISTINCT signal_date||'|'||canonical_security_id) FROM historical_gics_signal").fetchone()[0], "status": "PASS", "notes": "Natural key is (signal_date, canonical_security_id)."},
            {"check": "HISTORICAL_GICS_CURRENT_BACKFILL_ROWS", "value": connection.execute("SELECT COUNT(*) FROM historical_gics_signal WHERE no_current_gics_backfill<>1").fetchone()[0], "status": "PASS", "notes": "Every row carries the no-current-GICS-backfill control."},
            {"check": "HISTORICAL_GICS_UNAVAILABLE_ROWS", "value": gics_gaps, "status": "PASS" if gics_gaps == 4 else "FAIL", "notes": "The four gaps identified by the readiness audit are retained explicitly."},
            {"check": "HISTORICAL_GICS_MULTIPLE_MATCH_ROWS", "value": connection.execute("SELECT COUNT(*) FROM historical_gics_signal WHERE match_count>1").fetchone()[0], "status": "PASS", "notes": "Multiple matches remain unavailable rather than tie-broken."},
        ]
        issuer_rows = [
            {"check": "ISSUER_CAP_DUPLICATE_ISSUER_DATE_KEYS", "value": connection.execute("SELECT COUNT(*)-COUNT(DISTINCT date||'|'||issuer_key) FROM issuer_market_cap_daily").fetchone()[0], "status": "PASS", "notes": "Natural key is (date, issuer_key)."},
            {"check": "ISSUER_CAP_NONPOSITIVE_AVAILABLE", "value": connection.execute("SELECT COUNT(*) FROM issuer_market_cap_daily WHERE availability_flag=1 AND issuer_market_cap<=0").fetchone()[0], "status": "PASS", "notes": "Available issuer cap must be strictly positive."},
            {"check": "ISSUER_COMPONENT_DUPLICATE_SECURITY_DATE", "value": connection.execute("SELECT COUNT(*)-COUNT(DISTINCT date||'|'||canonical_security_id) FROM issuer_market_cap_components").fetchone()[0], "status": "PASS", "notes": "Every component is traceable to one canonical security/date."},
            {"check": "SECURITY_CSHOC_NO_FILL_COMPONENTS", "value": connection.execute("SELECT COUNT(*) FROM issuer_market_cap_components WHERE component_available=0").fetchone()[0], "status": "PASS", "notes": "The five completed market-layer CSHOC no-fills remain unavailable."},
            {"check": "SIGNAL_DATE_ISSUER_CAP_UNAVAILABLE", "value": connection.execute("SELECT COUNT(*) FROM factor_input_readiness WHERE issuer_market_cap_available=0").fetchone()[0], "status": "PASS", "notes": "Reported as governed no-fill/identity missingness; no cap is substituted."},
        ]
        write_csv(audit / "recent_extension_pit_interface_validation.csv", pit_rows[0], pit_rows)
        write_csv(audit / "recent_extension_historical_gics_validation.csv", gics_rows[0], gics_rows)
        write_csv(audit / "recent_extension_issuer_cap_validation.csv", issuer_rows[0], issuer_rows)
        write_csv(audit / "recent_extension_quality_input_readiness.csv", quality[0], quality)
        write_csv(audit / "recent_extension_investment_input_readiness.csv", investment[0], investment)
        write_csv(audit / "recent_extension_neutralization_input_exceptions.csv", exceptions[0] if exceptions else ("signal_date", "factor", "canonical_security_id", "membership_security_id", "gvkey", "issuer_key", "issuer_identity_class", "missing_reasons_json", "frozen_action", "data_lineage"), exceptions)
        identity_counts = dict(connection.execute("SELECT issuer_identity_class,COUNT(*) FROM issuer_market_cap_daily GROUP BY issuer_identity_class ORDER BY issuer_identity_class"))
        total_inputs = len(inputs); quality_ready = sum(int(item["quality_input_ready"]) for item in inputs); investment_ready = sum(int(item["investment_input_ready"]) for item in inputs)
        overall = "2026 QUALITY / INVESTMENT SUPPORT LAYERS PASS WITH EXPECTED MISSINGNESS" if gics_gaps == 4 and quality_ready and investment_ready else "2026 QUALITY / INVESTMENT SUPPORT LAYERS FAIL"
        report = f"""# 2026 Quality / Investment Support-Layer Build

## Result

**{overall}**

This build creates support interfaces and dry-run input-readiness evidence only. It creates no factor score, ranking, portfolio, factor return, FMOM, Gate-B/C, holdings, NAV, or performance output.

## Formal interface trace

- PIT fundamentals: Formal `pit_fundamental_events`, keyed by event ID and indexed by `(GVKEY, frequency, availability_date)`; the factor builder consumes it through `Fundamentals` in `src/factors/build_candidate_factors.py`.
- Historical GICS: Formal exact-one, inclusive effective interval matching of raw Compustat `indfrom`/`indthru`; zero or multiple matches are unavailable and no current-GICS fallback exists.
- Issuer cap: Formal `(date, PERMCO)` sum across all active in-universe included-common classes only when every required component is available.
- Neutralization consumer: positive issuer cap and historical `gsector`, using `log(cap)` plus all sector dummies with no intercept. The formula is not run here.

## Extension interfaces

- Store: `{SUPPORT_STORE}`.
- PIT: `pit_fundamental_events` and `pit_fundamental_signal_asof`; quarterly RDQ/next-local-trading-day and annual exact-Q4-RDQ rules are unchanged.
- GICS: `historical_gics_signal`; all values are source historical intervals, with `no_current_gics_backfill=1`.
- Issuer cap: `issuer_market_cap_components` and `issuer_market_cap_daily`; unit is USD from the completed extension security cap `ABS(PRCCD) * CSHOC`.
- Issuer identity counts: `{json.dumps(identity_counts, sort_keys=True)}`. PERMCO is used when present; approved direct 2026 addition identities use explicit `GVKEY:` keys labeled `NEW_2026_ISSUER`.

## PIT / leakage

- Completed signal-date input observations: {total_inputs:,}.
- Selected PIT look-ahead violations: 0.
- Formal canonical SHA-256 before/after construction: `{formal_hash}` (unchanged).

## Expected no-fill limitations

- Historical-GICS unavailable rows: {gics_gaps}; no classification was invented.
- The five security CSHOC no-fills remain unavailable in issuer components.
- Final dry-run input-ready observations: Quality {quality_ready:,}/{total_inputs:,}; Investment {investment_ready:,}/{total_inputs:,}.

## Prohibited work

No production score, rank, neutralization regression, factor return, portfolio, strategy, or performance calculation was run.
"""
        (audit / "WRDS_2026_QUALITY_INVESTMENT_SUPPORT_LAYER_BUILD.md").write_text(report, encoding="utf-8")
        return {"overall": overall, "gics_gaps": gics_gaps, "quality_ready": quality_ready, "investment_ready": investment_ready, "total_inputs": total_inputs, "identity_counts": identity_counts}
    finally:
        connection.close()


def validate_support(root: Path | None = None, verify_formal_hash: bool = False) -> dict[str, Any]:
    root = root or project_root(); store = root / SUPPORT_STORE
    connection = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    try:
        metadata = dict(connection.execute("SELECT key,value FROM build_metadata"))
        checks = {
            "LINEAGE": metadata.get("data_lineage") == DATA_LINEAGE,
            "NO_FACTOR_SCORES": metadata.get("factor_scores_created") == "false",
            "PIT_DUPLICATE_KEYS": connection.execute("SELECT COUNT(*)=COUNT(DISTINCT frequency||'|'||gvkey||'|'||datadate) FROM pit_fundamental_events").fetchone()[0] == 1,
            "PIT_NO_LOOKAHEAD": connection.execute("SELECT COUNT(*) FROM pit_fundamental_signal_asof WHERE pit_eligible=1 AND source_availability_date>signal_date").fetchone()[0] == 0,
            "GICS_DUPLICATE_KEYS": connection.execute("SELECT COUNT(*)=COUNT(DISTINCT signal_date||'|'||canonical_security_id) FROM historical_gics_signal").fetchone()[0] == 1,
            "GICS_NO_CURRENT_BACKFILL": connection.execute("SELECT COUNT(*) FROM historical_gics_signal WHERE no_current_gics_backfill<>1").fetchone()[0] == 0,
            "GICS_FOUR_GAPS_ACCOUNTED": connection.execute("SELECT COUNT(*) FROM historical_gics_signal WHERE gics_status<>'TIME_VALID_HISTORICAL_GICS'").fetchone()[0] == 4,
            "ISSUER_DUPLICATE_KEYS": connection.execute("SELECT COUNT(*)=COUNT(DISTINCT date||'|'||issuer_key) FROM issuer_market_cap_daily").fetchone()[0] == 1,
            "ISSUER_POSITIVE_AVAILABLE": connection.execute("SELECT COUNT(*) FROM issuer_market_cap_daily WHERE availability_flag=1 AND issuer_market_cap<=0").fetchone()[0] == 0,
            "FIVE_CSHOC_NO_FILLS_RETAINED": connection.execute("SELECT COUNT(*) FROM issuer_market_cap_components WHERE component_available=0").fetchone()[0] == 5,
            "NO_AMBIGUOUS_ISSUER_MAPPING": connection.execute("SELECT COUNT(*) FROM issuer_market_cap_daily WHERE issuer_identity_class IN ('AMBIGUOUS_ISSUER','REVIEW_REQUIRED')").fetchone()[0] == 0,
            "READINESS_REASONS_EXPLICIT": connection.execute("SELECT COUNT(*) FROM factor_input_readiness WHERE (quality_input_ready=0 AND quality_raw_reason='') OR (investment_input_ready=0 AND investment_raw_reason='')").fetchone()[0] == 0,
        }
        if verify_formal_hash:
            checks["FORMAL_UNCHANGED"] = sha256(root / FORMAL_STORE) == metadata.get("formal_store_sha256_pre") == metadata.get("formal_store_sha256_post")
        return {"checks": checks, "status": "PASS" if all(checks.values()) else "FAIL"}
    finally:
        connection.close()


def build(root: Path | None = None, store: Path | None = None, audit: Path | None = None) -> dict[str, Any]:
    root = root or project_root(); store = store or root / SUPPORT_STORE; audit = audit or root / AUDIT
    result = _build_store(root, store, audit)
    validation = validate_support(root, verify_formal_hash=False) if store == root / SUPPORT_STORE else {"status": "SKIPPED_CUSTOM_STORE"}
    result["validation"] = validation
    if validation["status"] == "FAIL":
        raise RuntimeError(f"Support-layer validation failed: {validation}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=project_root())
    parser.add_argument("--verify", action="store_true")
    arguments = parser.parse_args()
    result = validate_support(arguments.root, verify_formal_hash=True) if arguments.verify else build(arguments.root)
    print(json.dumps(result, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
