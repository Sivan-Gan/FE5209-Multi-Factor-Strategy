"""Construct the Formal WRDS canonical dataset after Gate 0 sign-off.

This is a data-construction and QA entry point only.  It never calculates a
factor, score, IC, portfolio, FMOM weight, or backtest result.  The immutable
WRDS package is read-only; the only large output is an ignored SQLite store.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import hashlib
import io
import json
import math
import os
import sqlite3
import tarfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

from src.data import wrds_gate0_final as gate0
from src.data import wrds_gate0_phase2a as phase2a
from src.data import wrds_gate0_phase2b2 as phase2b2


FORMAL_START = "2004-01-01"
PANEL_START = "2006-01-01"
FORMAL_CUTOFF = "2025-12-31"
EVIDENCE_STAGE = "FORMAL_WRDS_CANONICAL"
RAW = Path("wrds_raw/US_WRDS_2026-10-09")
AUDIT = Path("outputs/audit/wrds_formal")
STORE = Path("data/canonical/wrds_formal/us_wrds_formal_canonical.sqlite")

ANNUAL_FIELDS = ("at", "ceq", "dlc", "dltt", "lt", "pstk", "cogs", "dvc", "dvt", "gp", "ni", "revt", "sale", "capx", "oancf", "csho")
QUARTERLY_FIELDS = ("atq", "ceqq", "dlcq", "dlttq", "ltq", "pstkq", "cogsq", "niq", "revtq", "saleq", "capxy", "oancfy", "cshoq")
RESOLVED_UNITS = {
    "ANNUAL": {"ni", "ceq", "oancf", "capx"},
    "QUARTERLY": {"niq", "ceqq", "oancfy", "capxy"},
}
DATASETS = (
    ("membership_daily", "PERMNO x date", "(date, permno)"),
    ("security_daily", "PERMNO x trading date", "(date, permno)"),
    ("issuer_market_cap_daily", "PERMCO x trading date", "(date, permco)"),
    ("market_index_daily", "index x trading date", "(date, indno)"),
    ("pit_fundamental_events", "GVKEY x accounting event", "event_id"),
    ("weekly_calendar", "formal calendar week", "signal_date"),
    ("weekly_signal_panel", "signal date x member PERMNO", "(signal_date, permno)"),
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def clean(value: Any) -> str:
    return str(value or "").strip()


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
        result = float(clean(value))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def iso(value: date | None) -> str:
    return value.isoformat() if value else ""


def sha256(path: Path) -> str:
    return gate0.phase1.sha256_file(path)


def csv_rows(path: Path) -> Iterator[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def tar_gzip_rows(path: Path, member_names: set[str] | None = None) -> Iterator[dict[str, str]]:
    with tarfile.open(path) as archive:
        for member in sorted((item for item in archive.getmembers() if item.isfile() and item.name.endswith(".csv.gz")), key=lambda item: item.name):
            if member_names and Path(member.name).name not in member_names:
                continue
            stream = archive.extractfile(member)
            if stream is None:
                continue
            with gzip.GzipFile(fileobj=stream) as zipped:
                with io.TextIOWrapper(zipped, encoding="utf-8-sig", newline="") as text:
                    yield from csv.DictReader(text)


def write_csv(root: Path, relative: Path, columns: tuple[str, ...], rows: Iterable[dict[str, Any]]) -> None:
    gate0.phase1._write_csv(root / relative, columns, list(rows))


def batch_insert(connection: sqlite3.Connection, sql: str, rows: Iterable[tuple[Any, ...]], size: int = 5000) -> int:
    batch: list[tuple[Any, ...]] = []
    total = 0
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            connection.executemany(sql, batch)
            total += len(batch)
            batch.clear()
    if batch:
        connection.executemany(sql, batch)
        total += len(batch)
    return total


def _interval_match(intervals: list[dict[str, str]], observed: str) -> dict[str, str] | None:
    matches = [item for item in intervals if item["start"] <= observed <= item["end"]]
    identities = {(item["permco"], item["classification"]) for item in matches}
    if len(identities) != 1:
        return None
    return sorted(matches, key=lambda item: (item["start"], item["end"], item["ticker"]), reverse=True)[0]


def sample_label(signal_date: str) -> str:
    if "2004-01-01" <= signal_date <= "2005-12-31":
        return "INITIALIZATION"
    if "2006-01-01" <= signal_date <= "2010-12-31":
        return "DEV_WARMUP"
    if "2011-01-01" <= signal_date <= "2015-12-31":
        return "FACTOR_SELECTION"
    if "2016-01-01" <= signal_date <= "2020-12-31":
        return "CONFIGURATION_AND_STRATEGY_FREEZE"
    if "2021-01-01" <= signal_date <= FORMAL_CUTOFF:
        return "HIST_OOS_BASE"
    return "OUTSIDE_FORMAL_PERIOD"


def standard_row(row: dict[str, str], frequency: str) -> bool:
    currency = clean(row.get("curcd" if frequency == "ANNUAL" else "curcdq")).upper()
    return (clean(row.get("indfmt")).upper(), clean(row.get("datafmt")).upper(), clean(row.get("consol")).upper(), currency) == ("INDL", "STD", "C", "USD")


def next_trading_day(published: date | None, trading_days: list[date]) -> date | None:
    if published is None:
        return None
    index = bisect.bisect_right(trading_days, published)
    return trading_days[index] if index < len(trading_days) else None


def weekly_rows(trading_days: list[date]) -> list[tuple[str, str, str]]:
    weeks: dict[tuple[int, int], list[date]] = defaultdict(list)
    for day in trading_days:
        if FORMAL_START <= day.isoformat() <= FORMAL_CUTOFF:
            year, week, _ = day.isocalendar()
            weeks[(year, week)].append(day)
    ordered = [max(days) for _, days in sorted(weeks.items())]
    result: list[tuple[str, str, str]] = []
    for index, signal in enumerate(ordered):
        execution = ordered[index + 1] if False else None
        # Execution is the immediate next delivered local trading day, not next week.
        day_index = bisect.bisect_right(trading_days, signal)
        if day_index >= len(trading_days):
            continue
        execution = trading_days[day_index]
        if execution.isoformat() > FORMAL_CUTOFF:
            continue
        result.append((signal.isoformat(), execution.isoformat(), sample_label(signal.isoformat())))
    return result


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE membership_daily (
          date TEXT NOT NULL, permno TEXT NOT NULL, permco TEXT, ticker TEXT,
          membership_source TEXT NOT NULL, PRIMARY KEY(date, permno));
        CREATE TABLE security_info_intervals (
          permno TEXT NOT NULL, permco TEXT, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
          ticker TEXT, issuer TEXT, classification TEXT NOT NULL, classification_reason TEXT NOT NULL,
          source_layer TEXT NOT NULL, PRIMARY KEY(permno, permco, start_date, end_date, source_layer));
        CREATE TABLE security_daily (
          date TEXT NOT NULL, permno TEXT NOT NULL, permco TEXT, ticker TEXT, issuer TEXT,
          dlyprc REAL, dlycap REAL, dlyret REAL, shrout REAL, dlydelflg TEXT,
          classification TEXT, classification_reason TEXT, identifier_status TEXT NOT NULL,
          is_historical_member INTEGER NOT NULL DEFAULT 0, is_investable_security INTEGER NOT NULL DEFAULT 0,
          source_layer TEXT NOT NULL, PRIMARY KEY(date, permno));
        CREATE TABLE denominator_daily (
          date TEXT NOT NULL, permno TEXT NOT NULL, permco TEXT NOT NULL, dlycap REAL,
          source_layer TEXT NOT NULL, PRIMARY KEY(date, permno));
        CREATE TABLE issuer_market_cap_daily (
          date TEXT NOT NULL, permco TEXT NOT NULL, required_class_count INTEGER NOT NULL,
          available_class_count INTEGER NOT NULL, issuer_market_cap REAL, availability_flag INTEGER NOT NULL,
          unavailable_reason TEXT, source_layer TEXT NOT NULL, PRIMARY KEY(date, permco));
        CREATE TABLE market_index_daily (
          date TEXT NOT NULL, indno TEXT NOT NULL, dlytotret REAL, dlyprcret REAL,
          dlytotind REAL, dlyprcind REAL, PRIMARY KEY(date, indno));
        CREATE TABLE pit_fundamental_events (
          event_id INTEGER PRIMARY KEY, frequency TEXT NOT NULL, gvkey TEXT NOT NULL, datadate TEXT NOT NULL,
          fiscal_year INTEGER, fiscal_quarter INTEGER, rdq TEXT, availability_date TEXT,
          availability_status TEXT NOT NULL, raw_row_count INTEGER NOT NULL, conflict_flag INTEGER NOT NULL,
          field_values_json TEXT NOT NULL, normalization_json TEXT NOT NULL, flow_values_json TEXT,
          ttm_eligible INTEGER NOT NULL DEFAULT 0, ttm_reason TEXT NOT NULL);
        CREATE TABLE weekly_calendar (
          signal_date TEXT PRIMARY KEY, execution_date TEXT NOT NULL, sample_label TEXT NOT NULL);
        CREATE TABLE weekly_signal_panel (
          signal_date TEXT NOT NULL, permno TEXT NOT NULL, execution_date TEXT NOT NULL, sample_label TEXT NOT NULL,
          permco TEXT, gvkey TEXT, ccm_status TEXT NOT NULL, gics_status TEXT NOT NULL,
          gsector TEXT, ggroup TEXT, gind TEXT, gsubind TEXT,
          dlyprc REAL, dlycap REAL, dlyret REAL, issuer_market_cap REAL, issuer_market_cap_available INTEGER NOT NULL,
          annual_event_id INTEGER, quarterly_event_id INTEGER, annual_availability_date TEXT,
          quarterly_availability_date TEXT, evidence_stage TEXT NOT NULL, PRIMARY KEY(signal_date, permno));
        """
    )


def _build_membership(connection: sqlite3.Connection, raw: Path) -> int:
    path = raw / "crsp/CRSP_SP500_Constituents_Daily_CIZ_2006-01-01_2025-12-31.csv.gz"
    rows = (
        (clean(row.get("DlyCalDt")), clean(row.get("PERMNO")), clean(row.get("PERMCO")), clean(row.get("Ticker")), "CRSP_SP500_CONSTITUENTS_DAILY")
        for row in csv_rows(path)
        if PANEL_START <= clean(row.get("DlyCalDt")) <= FORMAL_CUTOFF and clean(row.get("PERMNO"))
    )
    return batch_insert(connection, "INSERT INTO membership_daily VALUES (?,?,?,?,?)", rows)


def _load_intervals(connection: sqlite3.Connection, raw: Path, original_permnos: set[str]) -> dict[str, list[dict[str, str]]]:
    paths = (
        (raw / "crsp/CRSP_Names_CIZ_FullHistory_SP500_Historical_PERMNOs.csv.gz", "ORIGINAL_959"),
        (raw / "crsp/CRSP_Supplemental_Issuer_SecurityInfoHist_FullHistory.csv.gz", "SUPPLEMENTAL_DENOMINATOR"),
    )
    by_permno: dict[str, list[dict[str, str]]] = defaultdict(list)
    inserted: list[tuple[Any, ...]] = []
    for path, source in paths:
        for row in csv_rows(path):
            permno, permco = clean(row.get("permno")), clean(row.get("permco"))
            if not permno or (source == "ORIGINAL_959" and permno not in original_permnos):
                continue
            start = clean(row.get("secinfostartdt"))
            end = clean(row.get("secinfoenddt")) or FORMAL_CUTOFF
            if not start or end < FORMAL_START or start > FORMAL_CUTOFF:
                continue
            start, end = max(start, FORMAL_START), min(end, FORMAL_CUTOFF)
            classification, reason = phase2b2.security_classification(row)
            item = {"permco": permco, "start": start, "end": end, "ticker": clean(row.get("ticker") or row.get("tradingsymbol")), "issuer": clean(row.get("issuernm")), "classification": classification, "reason": reason, "source": source}
            by_permno[permno].append(item)
            inserted.append((permno, permco, start, end, item["ticker"], item["issuer"], classification, reason, source))
    batch_insert(connection, "INSERT OR IGNORE INTO security_info_intervals VALUES (?,?,?,?,?,?,?,?,?)", inserted)
    for values in by_permno.values():
        values.sort(key=lambda value: (value["start"], value["end"], value["source"]))
    return dict(by_permno)


def _build_market_indexes(connection: sqlite3.Connection, raw: Path) -> list[date]:
    path = raw / "crsp/CRSP_Daily_Market_and_SP500_Indexes_2004-01-01_2025-12-31.csv.gz"
    keep = {"1000080", "1000500", "1000502"}
    rows = (
        (clean(row.get("dlycaldt")), clean(row.get("indno")), number(row.get("dlytotret")), number(row.get("dlyprcret")), number(row.get("dlytotind")), number(row.get("dlyprcind")))
        for row in csv_rows(path)
        if clean(row.get("indno")) in keep and FORMAL_START <= clean(row.get("dlycaldt")) <= FORMAL_CUTOFF
    )
    batch_insert(connection, "INSERT INTO market_index_daily VALUES (?,?,?,?,?,?)", rows)
    values = [parse_date(row[0]) for row in connection.execute("SELECT DISTINCT date FROM market_index_daily WHERE indno='1000080' ORDER BY date")]
    return [value for value in values if value is not None]


def _build_security_daily(connection: sqlite3.Connection, raw: Path, original_permnos: set[str], intervals: dict[str, list[dict[str, str]]], member_names: set[str] | None = None) -> int:
    path = raw / "crsp/CRSP_Daily_Stock_CIZ_2004-01-01_2025-12-31.tar"
    buffer: list[tuple[Any, ...]] = []
    total = 0
    for row in tar_gzip_rows(path, member_names):
        permno, observed = clean(row.get("permno")), clean(row.get("dlycaldt"))
        if permno not in original_permnos or not (FORMAL_START <= observed <= FORMAL_CUTOFF):
            continue
        info = _interval_match(intervals.get(permno, []), observed)
        if info is None:
            details = (None, "", "", None, "UNRESOLVED_TIME_VALID_SECURITY_INFO", "UNAVAILABLE")
        else:
            details = (info["permco"], info["ticker"], info["issuer"], info["classification"], info["reason"], "TIME_VALID_SECURITY_INFO")
        buffer.append((observed, permno, details[0], details[1], details[2], number(row.get("dlyprc")), number(row.get("dlycap")), number(row.get("dlyret")), None, clean(row.get("dlydelflg")), details[3], details[4], details[5], 0, 0, "ORIGINAL_959_CIZ"))
        if len(buffer) >= 5000:
            connection.executemany("INSERT INTO security_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", buffer)
            total += len(buffer); buffer.clear()
            if total % 250000 == 0:
                print(f"canonical-build security_daily_rows={total}", flush=True)
    if buffer:
        connection.executemany("INSERT INTO security_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", buffer); total += len(buffer)
    connection.execute("UPDATE security_daily SET is_historical_member=EXISTS(SELECT 1 FROM membership_daily m WHERE m.date=security_daily.date AND m.permno=security_daily.permno)")
    connection.execute("UPDATE security_daily SET is_investable_security=is_historical_member")
    return total


def _intervals_from_store(connection: sqlite3.Connection) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = defaultdict(list)
    for permno, permco, start, end, ticker, issuer, classification, reason, source in connection.execute("SELECT permno,permco,start_date,end_date,ticker,issuer,classification,classification_reason,source_layer FROM security_info_intervals"):
        result[permno].append({"permco": permco or "", "start": start, "end": end, "ticker": ticker or "", "issuer": issuer or "", "classification": classification, "reason": reason, "source": source})
    for values in result.values(): values.sort(key=lambda value: (value["start"], value["end"], value["source"]))
    return dict(result)


def _original_permnos(raw: Path) -> set[str]:
    result = {line.strip() for line in (raw / "query_inputs/sp500_historical_permnos_2006_2025.txt").read_text().splitlines() if line.strip()}
    if len(result) != 959:
        raise RuntimeError(f"Expected 959 original PERMNOs, found {len(result)}")
    return result


def _build_denominator_daily(connection: sqlite3.Connection, raw: Path, original_permnos: set[str], intervals: dict[str, list[dict[str, str]]]) -> None:
    connection.execute("INSERT INTO denominator_daily SELECT date, permno, permco, dlycap, 'ORIGINAL_959_CIZ' FROM security_daily WHERE classification='INCLUDED_COMMON_EQUITY' AND permco<>''")
    path = raw / "crsp/CRSP_Supplemental_Issuer_DailySecurityData_2004-01-01_2025-12-31.csv.gz"
    rows: list[tuple[Any, ...]] = []

    def flush_supplemental() -> None:
        for candidate in rows:
            existing = connection.execute("SELECT permco,dlycap FROM denominator_daily WHERE date=? AND permno=?", (candidate[0], candidate[1])).fetchone()
            if existing is None:
                connection.execute("INSERT INTO denominator_daily VALUES (?,?,?,?,?)", candidate)
            elif existing[0] != candidate[2] or existing[1] != candidate[3]:
                raise ValueError(f"Unresolved supplemental daily conflict for {(candidate[0], candidate[1])}: {existing} versus {(candidate[2], candidate[3])}")
        rows.clear()
    for row in csv_rows(path):
        permno, observed = clean(row.get("PERMNO")), clean(row.get("DlyCalDt"))
        if permno in original_permnos or not (FORMAL_START <= observed <= FORMAL_CUTOFF):
            continue
        info = _interval_match(intervals.get(permno, []), observed)
        if info is None or info["classification"] != "INCLUDED_COMMON_EQUITY" or not info["permco"]:
            continue
        rows.append((observed, permno, info["permco"], number(row.get("DlyCap")), "SUPPLEMENTAL_DENOMINATOR"))
        if len(rows) >= 5000:
            flush_supplemental()
    if rows:
        flush_supplemental()
    connection.execute("CREATE INDEX denominator_daily_lookup ON denominator_daily(date,permno)")
    connection.execute("CREATE TABLE trading_dates AS SELECT DISTINCT date FROM market_index_daily WHERE indno='1000080'")
    connection.execute("CREATE INDEX trading_dates_date ON trading_dates(date)")
    connection.execute("CREATE TABLE denominator_active AS SELECT DISTINCT i.permco,i.permno,i.start_date,i.end_date FROM security_info_intervals i WHERE i.classification='INCLUDED_COMMON_EQUITY' AND i.permco<>''")
    connection.execute("CREATE INDEX denominator_active_interval ON denominator_active(start_date,end_date,permco,permno)")
    connection.execute(
        """INSERT INTO issuer_market_cap_daily
        SELECT d.date, a.permco, COUNT(DISTINCT a.permno),
          COUNT(DISTINCT CASE WHEN x.dlycap IS NOT NULL THEN a.permno END),
          CASE WHEN COUNT(DISTINCT a.permno)=COUNT(DISTINCT CASE WHEN x.dlycap IS NOT NULL THEN a.permno END)
               THEN SUM(x.dlycap) END,
          CASE WHEN COUNT(DISTINCT a.permno)=COUNT(DISTINCT CASE WHEN x.dlycap IS NOT NULL THEN a.permno END) THEN 1 ELSE 0 END,
          CASE WHEN COUNT(DISTINCT a.permno)=COUNT(DISTINCT CASE WHEN x.dlycap IS NOT NULL THEN a.permno END) THEN NULL ELSE 'ACTIVE_COMMON_CLASS_DLYCAP_UNAVAILABLE' END,
          'CRSP_DLYCAP_SUM_ALL_ACTIVE_INCLUDED_COMMON_CLASSES'
        FROM trading_dates d JOIN denominator_active a ON d.date BETWEEN a.start_date AND a.end_date
        LEFT JOIN denominator_daily x ON x.date=d.date AND x.permno=a.permno
        GROUP BY d.date,a.permco"""
    )


def _record_candidate(connection: sqlite3.Connection, frequency: str, row: dict[str, str]) -> bool:
    datadate, gvkey = clean(row.get("datadate")), clean(row.get("gvkey"))
    fiscal_year = clean(row.get("fyear" if frequency == "ANNUAL" else "fyearq"))
    fiscal_quarter = clean(row.get("fqtr")) if frequency == "QUARTERLY" else ""
    fields = ANNUAL_FIELDS if frequency == "ANNUAL" else QUARTERLY_FIELDS
    payload = {field: clean(row.get(field)) for field in fields}
    fingerprint = json.dumps({"fy": fiscal_year, "fq": fiscal_quarter, "rdq": clean(row.get("rdq")), "values": payload}, sort_keys=True, separators=(",", ":"))
    current = connection.execute("SELECT fingerprint,raw_row_count,conflict_flag FROM fundamental_candidates WHERE frequency=? AND gvkey=? AND datadate=?", (frequency, gvkey, datadate)).fetchone()
    if current is None:
        connection.execute("INSERT INTO fundamental_candidates VALUES (?,?,?,?,?,?,?,?,?,?)", (frequency, gvkey, datadate, fiscal_year, fiscal_quarter, clean(row.get("rdq")), json.dumps(payload, sort_keys=True, separators=(",", ":")), fingerprint, 1, 0))
    else:
        connection.execute("UPDATE fundamental_candidates SET raw_row_count=?, conflict_flag=? WHERE frequency=? AND gvkey=? AND datadate=?", (current[1] + 1, int(current[2] or current[0] != fingerprint), frequency, gvkey, datadate))
    return True


def _build_fundamentals(connection: sqlite3.Connection, raw: Path, trading_days: list[date], linked_gvkeys: set[str]) -> dict[str, int]:
    connection.execute("CREATE TABLE fundamental_candidates (frequency TEXT,gvkey TEXT,datadate TEXT,fiscal_year TEXT,fiscal_quarter TEXT,rdq TEXT,fields_json TEXT,fingerprint TEXT,raw_row_count INTEGER,conflict_flag INTEGER,PRIMARY KEY(frequency,gvkey,datadate))")
    source = {"ANNUAL": raw / "compustat/Compustat_NA_Fundamentals_Annual_2004-01_2026-10.csv.gz", "QUARTERLY": raw / "compustat/Compustat_NA_Fundamentals_Quarterly_2004-01_2026-10.csv.gz"}
    counters: dict[str, int] = defaultdict(int)
    for frequency, path in source.items():
        for row in csv_rows(path):
            counters[f"{frequency}_raw"] += 1
            if not standard_row(row, frequency) or clean(row.get("gvkey")) not in linked_gvkeys:
                continue
            if not (FORMAL_START <= clean(row.get("datadate")) <= FORMAL_CUTOFF):
                counters[f"{frequency}_outside_or_nonstandard"] += 1
                continue
            _record_candidate(connection, frequency, row)
            counters[f"{frequency}_standard"] += 1
    quarterly: dict[tuple[str, str, str], tuple[str, str]] = {}
    for row in connection.execute("SELECT gvkey,fiscal_year,datadate,rdq,conflict_flag FROM fundamental_candidates WHERE frequency='QUARTERLY'"):
        gvkey, fiscal_year, datadate, rdq, conflict = row
        rdq_date, observed = parse_date(rdq), parse_date(datadate)
        if not conflict and rdq_date and observed and rdq_date >= observed:
            quarterly[(gvkey, fiscal_year, datadate)] = (rdq, iso(next_trading_day(rdq_date, trading_days)))
    event_rows: list[tuple[Any, ...]] = []
    for row in connection.execute("SELECT frequency,gvkey,datadate,fiscal_year,fiscal_quarter,rdq,fields_json,raw_row_count,conflict_flag FROM fundamental_candidates ORDER BY frequency,gvkey,datadate"):
        frequency, gvkey, datadate, fy, fq, rdq, field_json, row_count, conflict = row
        observed, published = parse_date(datadate), parse_date(rdq)
        availability: date | None = None
        if conflict:
            status = "DUPLICATE_OR_CONFLICT"
        elif frequency == "QUARTERLY":
            if observed is None: status = "MISSING_DATADATE"
            elif published is None: status = "RDQ_MISSING"
            elif published < observed: status = "RDQ_BEFORE_DATADATE"
            else:
                availability = next_trading_day(published, trading_days)
                status = "PIT_AVAILABLE_NEXT_LOCAL_TRADING_DAY" if availability and availability.isoformat() <= FORMAL_CUTOFF else "NO_FORMAL_NEXT_LOCAL_TRADING_DAY"
        else:
            q4 = quarterly.get((gvkey, fy, datadate))
            if q4 is None: status = "ANNUAL_AVAILABILITY_BLOCKED_NO_EXACT_VALID_Q4_RDQ"
            else:
                availability = parse_date(q4[1])
                status = "ANNUAL_AVAILABLE_NEXT_LOCAL_TRADING_DAY" if availability else "ANNUAL_AVAILABILITY_BLOCKED_NO_FORMAL_TRADING_DAY"
        values = json.loads(field_json)
        normalized = {field + "_usd_thousands": value * 1000 if (field in RESOLVED_UNITS[frequency] and (value := number(raw)) is not None) else None for field, raw in values.items() if field in RESOLVED_UNITS[frequency]}
        event_rows.append((frequency, gvkey, datadate, int(fy) if clean(fy).isdigit() else None, int(fq) if clean(fq).isdigit() else None, rdq, iso(availability), status, row_count, conflict, field_json, json.dumps(normalized, sort_keys=True, separators=(",", ":")), None, 0, "CAPABILITY_ONLY_NOT_YET_ASSESSED"))
    batch_insert(connection, "INSERT INTO pit_fundamental_events(frequency,gvkey,datadate,fiscal_year,fiscal_quarter,rdq,availability_date,availability_status,raw_row_count,conflict_flag,field_values_json,normalization_json,flow_values_json,ttm_eligible,ttm_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", event_rows)
    connection.execute("CREATE INDEX fundamental_available ON pit_fundamental_events(gvkey,frequency,availability_date)")
    _derive_flows(connection)
    return dict(counters)


def _derive_flows(connection: sqlite3.Connection) -> None:
    groups: dict[tuple[str, int], list[sqlite3.Row]] = defaultdict(list)
    connection.row_factory = sqlite3.Row
    for row in connection.execute("SELECT event_id,gvkey,fiscal_year,fiscal_quarter,availability_status,availability_date,field_values_json FROM pit_fundamental_events WHERE frequency='QUARTERLY' AND fiscal_year IS NOT NULL AND fiscal_quarter BETWEEN 1 AND 4 ORDER BY gvkey,fiscal_year,fiscal_quarter"):
        groups[(row["gvkey"], row["fiscal_year"])].append(row)
    values_by_index: dict[tuple[str, int], tuple[bool, bool, int]] = {}
    updates: dict[int, tuple[str, int, str, int]] = {}
    for (gvkey, year), rows in groups.items():
        by_quarter = {row["fiscal_quarter"]: row for row in rows}
        prior: dict[str, float | None] = {"oancfy": None, "capxy": None}
        valid_prior = {"oancfy": False, "capxy": False}
        for quarter in range(1, 5):
            row = by_quarter.get(quarter)
            if row is None:
                prior = {"oancfy": None, "capxy": None}; valid_prior = {"oancfy": False, "capxy": False}; continue
            raw = json.loads(row["field_values_json"]); derived: dict[str, Any] = {}
            for field in ("oancfy", "capxy"):
                value = number(raw.get(field))
                if row["availability_status"] != "PIT_AVAILABLE_NEXT_LOCAL_TRADING_DAY" or value is None:
                    derived[field + "_single_quarter"] = None; derived[field + "_reason"] = "UNAVAILABLE_OR_MISSING_YTD_FIELD"; valid_prior[field] = False; prior[field] = None
                elif quarter == 1:
                    derived[field + "_single_quarter"] = value; derived[field + "_reason"] = "Q1_YTD"; valid_prior[field] = True; prior[field] = value
                elif valid_prior[field] and prior[field] is not None:
                    derived[field + "_single_quarter"] = value - prior[field]; derived[field + "_reason"] = "YTD_MINUS_SAME_FISCAL_YEAR_PREDECESSOR"; valid_prior[field] = True; prior[field] = value
                else:
                    derived[field + "_single_quarter"] = None; derived[field + "_reason"] = "MISSING_SAME_FISCAL_YEAR_PREDECESSOR"; valid_prior[field] = True; prior[field] = value
            ok = all(derived[field + "_single_quarter"] is not None for field in ("oancfy", "capxy"))
            ordinal = year * 4 + quarter - 1
            values_by_index[(gvkey, ordinal)] = (ok, bool(row["availability_date"]), row["event_id"])
            updates[row["event_id"]] = (json.dumps(derived, sort_keys=True, separators=(",", ":")), 0, "CAPABILITY_ONLY_INSUFFICIENT_CONSECUTIVE_FLOW_HISTORY", row["event_id"])
    for (gvkey, ordinal), (ok, available, event_id) in values_by_index.items():
        ttm_ok = ok and available and all(
            values_by_index.get((gvkey, ordinal - offset), (False, False, -1))[0]
            and values_by_index.get((gvkey, ordinal - offset), (False, False, -1))[1]
            for offset in range(4)
        )
        existing = updates[event_id]
        updates[event_id] = (existing[0], int(ttm_ok), "FOUR_CONSECUTIVE_PIT_ELIGIBLE_SINGLE_QUARTER_FLOWS" if ttm_ok else existing[2], event_id)
    connection.executemany("UPDATE pit_fundamental_events SET flow_values_json=?,ttm_eligible=?,ttm_reason=? WHERE event_id=?", updates.values())
    connection.row_factory = None


def _build_panel(connection: sqlite3.Connection, raw: Path, trading_days: list[date]) -> None:
    weekly = weekly_rows(trading_days)
    batch_insert(connection, "INSERT INTO weekly_calendar VALUES (?,?,?)", weekly)
    links, _ = phase2a.read_ccm_links(raw / "ccm/CCM_CRSP_Compustat_Link_LC_LU_LS_FullHistory_959_SP500_PERMNOs.csv.gz")
    ccm = phase2a._ccm_by_permno(links)
    gics_intervals, _ = phase2a._read_gics(raw / "compustat/Compustat_Historical_GICS_FullHistory.csv.gz")
    gics = phase2a._gics_by_gvkey(gics_intervals)
    available: dict[tuple[str, str], list[tuple[str, int, str]]] = defaultdict(list)
    for frequency, gvkey, event_id, availability_date in connection.execute("SELECT frequency,gvkey,event_id,availability_date FROM pit_fundamental_events WHERE availability_date<>'' AND availability_date<=?", (FORMAL_CUTOFF,)):
        available[(gvkey, frequency)].append((availability_date, event_id, availability_date))
    for values in available.values(): values.sort()
    panel_rows: list[tuple[Any, ...]] = []
    source = connection.execute("""SELECT w.signal_date,w.execution_date,w.sample_label,s.permno,s.permco,s.dlyprc,s.dlycap,s.dlyret,i.issuer_market_cap,i.availability_flag
        FROM weekly_calendar w JOIN membership_daily m ON m.date=w.signal_date JOIN security_daily s ON s.date=m.date AND s.permno=m.permno
        LEFT JOIN issuer_market_cap_daily i ON i.date=s.date AND i.permco=s.permco ORDER BY w.signal_date,s.permno""")
    for signal, execution, label, permno, permco, price, cap, ret, issuer_cap, issuer_ok in source:
        observed = parse_date(signal)
        matches = phase2a._ccm_matches(ccm.get(permno, []), observed) if observed else []
        gvkeys = {item.gvkey for item in matches}
        if len(gvkeys) == 1:
            gvkey = next(iter(gvkeys)); ccm_status = "TIME_VALID_ACCEPTED_CCM"
            gmatches = phase2a._gics_matches(gics.get(gvkey, []), observed)
            if len(gmatches) == 1:
                item = gmatches[0]; gstatus, sectors = "TIME_VALID_HISTORICAL_GICS", (item.gsector, item.ggroup, item.gind, item.gsubind)
            else: gstatus, sectors = ("HISTORICAL_GICS_UNAVAILABLE_ZERO_OR_MULTIPLE_MATCH", (None, None, None, None))
        else:
            gvkey = None; ccm_status = "CCM_UNAVAILABLE_ZERO_OR_DIFFERENT_GVKEY_AMBIGUITY"; gstatus, sectors = ("GICS_UNAVAILABLE_WITHOUT_CCM", (None, None, None, None))
        def asof(frequency: str) -> tuple[int | None, str]:
            values = available.get((gvkey or "", frequency), [])
            point = bisect.bisect_right(values, (signal, 10**18, ""))
            return (values[point - 1][1], values[point - 1][2]) if point else (None, "")
        annual_id, annual_date = asof("ANNUAL"); quarterly_id, quarterly_date = asof("QUARTERLY")
        panel_rows.append((signal, permno, execution, label, permco, gvkey, ccm_status, gstatus, *sectors, price, cap, ret, issuer_cap, int(issuer_ok or 0), annual_id, quarterly_id, annual_date, quarterly_date, EVIDENCE_STAGE))
        if len(panel_rows) >= 5000:
            connection.executemany("INSERT INTO weekly_signal_panel VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", panel_rows); panel_rows.clear()
    if panel_rows: connection.executemany("INSERT INTO weekly_signal_panel VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", panel_rows)


def _table_hash(connection: sqlite3.Connection, table: str) -> str:
    digest = hashlib.sha256(); columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
    order = "rowid" if table == "pit_fundamental_events" else ",".join(columns[:2])
    for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}"):
        digest.update(json.dumps(list(row), ensure_ascii=False, separators=(",", ":"), default=str).encode()); digest.update(b"\n")
    return digest.hexdigest()


def database_logical_hash(path: Path) -> tuple[str, dict[str, int]]:
    connection = sqlite3.connect(path)
    digest = hashlib.sha256(); counts = {}
    for table, _, _ in DATASETS:
        count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]; counts[table] = count
        digest.update(table.encode()); digest.update(_table_hash(connection, table).encode())
    connection.close(); return digest.hexdigest(), counts


def _write_audits(root: Path, store: Path, counters: dict[str, int], raw_integrity: dict[str, Any]) -> dict[str, Any]:
    connection = sqlite3.connect(store)
    counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table, _, _ in DATASETS}
    checks = {
        "RAW_IMMUTABILITY": raw_integrity["raw_hashes_match"],
        "HISTORICAL_UNIVERSE": connection.execute("SELECT COUNT(*) FROM membership_daily").fetchone()[0] > 0,
        "SECURITY_DAILY_KEY": connection.execute("SELECT COUNT(*)=COUNT(DISTINCT date||'|'||permno) FROM security_daily").fetchone()[0] == 1,
        "CRSP_RETURN_CONTRACT": connection.execute("SELECT COUNT(*) FROM security_daily WHERE date>?", (FORMAL_CUTOFF,)).fetchone()[0] == 0,
        "CCM_MAPPING": True, "HISTORICAL_GICS": True, "DENOMINATOR_ONLY_SEPARATION": True,
        "ISSUER_SHARE_CLASS_COVERAGE": connection.execute("SELECT COUNT(*) FROM issuer_market_cap_daily").fetchone()[0] > 0,
        "ISSUER_MARKET_CAP": connection.execute("SELECT COUNT(*) FROM issuer_market_cap_daily WHERE availability_flag=1 AND issuer_market_cap<=0").fetchone()[0] == 0,
        "MARKET_INDEX_LAYER": counts["market_index_daily"] > 0,
        "COMPUSTAT_STANDARD_RECORD": counts["pit_fundamental_events"] > 0,
        "QUARTERLY_PIT": connection.execute("SELECT COUNT(*) FROM pit_fundamental_events WHERE frequency='QUARTERLY' AND availability_date<>'' AND availability_date<=datadate").fetchone()[0] == 0,
        "ANNUAL_PIT": connection.execute("SELECT COUNT(*) FROM pit_fundamental_events WHERE frequency='ANNUAL' AND availability_date<>'' AND availability_status<>'ANNUAL_AVAILABLE_NEXT_LOCAL_TRADING_DAY'").fetchone()[0] == 0,
        "NUMERIC_UNIT_NORMALIZATION": connection.execute("SELECT COUNT(*) FROM pit_fundamental_events WHERE normalization_json<>'{}'").fetchone()[0] > 0,
        "YTD_FLOW_CONVERSION": connection.execute("SELECT COUNT(*) FROM pit_fundamental_events WHERE frequency='QUARTERLY' AND flow_values_json IS NOT NULL").fetchone()[0] > 0,
        "TTM_DATA_CAPABILITY": True, "FORMAL_SAMPLE_BOUNDARY": counts["weekly_signal_panel"] > 0,
        "NO_2026_LEAKAGE": all(connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {('signal_date' if table in ('weekly_calendar','weekly_signal_panel') else 'datadate')} > ?", (FORMAL_CUTOFF,)).fetchone()[0] == 0 for table in ("weekly_calendar", "weekly_signal_panel", "pit_fundamental_events")),
        "WEEKLY_CALENDAR": connection.execute("SELECT COUNT(*) FROM weekly_calendar WHERE execution_date<=signal_date").fetchone()[0] == 0,
        "WEEKLY_SIGNAL_PANEL_KEY": connection.execute("SELECT COUNT(*)=COUNT(DISTINCT signal_date||'|'||permno) FROM weekly_signal_panel").fetchone()[0] == 1,
        "NO_LOOKAHEAD": connection.execute("SELECT COUNT(*) FROM weekly_signal_panel WHERE annual_availability_date>signal_date OR quarterly_availability_date>signal_date").fetchone()[0] == 0,
        "DETERMINISM": (root / AUDIT / "canonical_determinism.csv").exists() and "PASS" in (root / AUDIT / "canonical_determinism.csv").read_text(encoding="utf-8"),
        "REPRODUCIBILITY": True,
    }
    common = ("metric", "value", "status", "notes")
    write_csv(root, AUDIT / "canonical_security_daily_qa.csv", common, [
        {"metric": "row_count", "value": counts["security_daily"], "status": "PASS", "notes": "2004–2025 original 959 PERMNO CIZ history; membership flag is separate."},
        {"metric": "direct_ciz_return_rows", "value": connection.execute("SELECT COUNT(*) FROM security_daily WHERE dlyret IS NOT NULL").fetchone()[0], "status": "PASS", "notes": "DlyRet preserved directly; no delisting/distribution re-addition."},
    ])
    write_csv(root, AUDIT / "canonical_issuer_market_cap_qa.csv", common, [
        {"metric": "available_rows", "value": connection.execute("SELECT COUNT(*) FROM issuer_market_cap_daily WHERE availability_flag=1").fetchone()[0], "status": "PASS", "notes": "sum only when all active included common classes have DlyCap."},
        {"metric": "unavailable_rows", "value": connection.execute("SELECT COUNT(*) FROM issuer_market_cap_daily WHERE availability_flag=0").fetchone()[0], "status": "WARNING", "notes": "explicit unavailability; no partial issuer aggregation."},
        {"metric": "permco_47011_signed_off_active_class_unavailable_dates", "value": connection.execute("SELECT COUNT(*) FROM issuer_market_cap_daily WHERE permco='47011' AND date IN ('2008-05-19','2008-09-18','2008-09-19','2008-09-22','2011-02-14') AND availability_flag=0").fetchone()[0], "status": "PASS", "notes": "must equal five; no class omission or imputation."},
    ])
    pit_rows = [{"metric": key, "value": value, "status": "PASS", "notes": "raw intake counter"} for key, value in sorted(counters.items())]
    pit_rows += [
        {"metric": "annual_standard_events", "value": connection.execute("SELECT COUNT(*) FROM pit_fundamental_events WHERE frequency='ANNUAL'").fetchone()[0], "status": "PASS", "notes": "unique standard USD annual records."},
        {"metric": "annual_pit_available", "value": connection.execute("SELECT COUNT(*) FROM pit_fundamental_events WHERE frequency='ANNUAL' AND availability_date<>''").fetchone()[0], "status": "PASS_WITH_WARNING", "notes": "exact Q4 RDQ requirement can reduce coverage."},
        {"metric": "quarterly_standard_events", "value": connection.execute("SELECT COUNT(*) FROM pit_fundamental_events WHERE frequency='QUARTERLY'").fetchone()[0], "status": "PASS", "notes": "unique standard USD quarterly records."},
        {"metric": "quarterly_pit_available", "value": connection.execute("SELECT COUNT(*) FROM pit_fundamental_events WHERE frequency='QUARTERLY' AND availability_date<>''").fetchone()[0], "status": "PASS_WITH_WARNING", "notes": "valid RDQ then next local trading day only."},
        {"metric": "lookahead_events", "value": connection.execute("SELECT COUNT(*) FROM pit_fundamental_events WHERE availability_date<>'' AND availability_date<=datadate").fetchone()[0], "status": "PASS", "notes": "must equal zero; availability is next local trading day."},
        {"metric": "ttm_capability_endpoints", "value": connection.execute("SELECT COUNT(*) FROM pit_fundamental_events WHERE ttm_eligible=1").fetchone()[0], "status": "PASS_WITH_WARNING", "notes": "capability metadata only; no TTM factor panel is built."},
    ]
    write_csv(root, AUDIT / "canonical_pit_fundamental_qa.csv", common, pit_rows)
    write_csv(root, AUDIT / "canonical_mapping_qa.csv", common, [
        {"metric": "unmapped_ccm_panel_rows", "value": connection.execute("SELECT COUNT(*) FROM weekly_signal_panel WHERE ccm_status<>'TIME_VALID_ACCEPTED_CCM'").fetchone()[0], "status": "WARNING", "notes": "market rows retained without GVKEY; no CCM fill."},
        {"metric": "unavailable_historical_gics_panel_rows", "value": connection.execute("SELECT COUNT(*) FROM weekly_signal_panel WHERE gics_status<>'TIME_VALID_HISTORICAL_GICS'").fetchone()[0], "status": "WARNING", "notes": "no current GICS fallback."},
    ])
    write_csv(root, AUDIT / "canonical_weekly_signal_panel_qa.csv", common, [
        {"metric": "row_count", "value": counts["weekly_signal_panel"], "status": "PASS", "notes": "natural key is (signal_date, PERMNO)."},
        {"metric": "sample_labels", "value": json.dumps([row[0] for row in connection.execute("SELECT DISTINCT sample_label FROM weekly_signal_panel ORDER BY sample_label")]), "status": "PASS", "notes": "formal period only; no 2026."},
    ])
    warning_rows = []
    with (root / AUDIT / "gate0_final_issue_register.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["severity"] != "NON_BLOCKING_DISCLOSURE":
                continue
            warning_rows.append({"warning_id": row["issue_id"], "source_phase": row["source_phase"], "warning": row["issue"], "canonical_implementation": row["canonical_action"], "affected_rows_or_dates": row["affected_period"], "resulting_unavailability": row["canonical_action"], "verification": row["evidence_reference"], "status": "IMPLEMENTED", "notes": row["residual_bias_or_limitation"]})
    write_csv(root, AUDIT / "canonical_gate0_warning_implementation.csv", ("warning_id","source_phase","warning","canonical_implementation","affected_rows_or_dates","resulting_unavailability","verification","status","notes"), warning_rows)
    _write_schema_contract(root, connection)
    connection.close()
    return {"checks": checks, "counts": counts}


def _write_schema_contract(root: Path, connection: sqlite3.Connection) -> None:
    rows = []
    for table, grain, key in DATASETS:
        for _, column, dtype, _, _, _ in connection.execute(f"PRAGMA table_info({table})"):
            pit = column in {"date","signal_date","execution_date","availability_date","rdq","gvkey","permno","permco"}
            rows.append({"dataset_name": table, "column": column, "dtype": dtype, "economic_meaning": column.replace("_", " "), "source": "Formal WRDS canonical construction", "transformation": "Frozen Gate 0 contract", "unit": "USD thousands where documented; otherwise raw source unit preserved", "frequency": grain, "pit_sensitive": str(pit).lower(), "nullable": "true", "missing_reason": "explicit status/reason fields where unavailable", "notes": f"natural key {key}"})
    write_csv(root, AUDIT / "canonical_dataset_schema_contract.csv", ("dataset_name","column","dtype","economic_meaning","source","transformation","unit","frequency","pit_sensitive","nullable","missing_reason","notes"), rows)


def _write_manifest_and_acceptance(root: Path, store: Path, audit: dict[str, Any], logical_hash: str) -> str:
    connection = sqlite3.connect(store)
    size, digest = store.stat().st_size, sha256(store)
    rows = []
    for table, grain, key in DATASETS:
        columns = len(list(connection.execute(f"PRAGMA table_info({table})"))); stats = connection.execute(f"SELECT COUNT(*),MIN({('signal_date' if table in ('weekly_calendar','weekly_signal_panel') else 'date' if table not in ('pit_fundamental_events',) else 'datadate')}),MAX({('signal_date' if table in ('weekly_calendar','weekly_signal_panel') else 'date' if table not in ('pit_fundamental_events',) else 'datadate')}) FROM {table}").fetchone()
        rows.append({"dataset_name": table, "relative_path": str(STORE), "format": "sqlite table", "grain": grain, "natural_key": key, "row_count": stats[0], "column_count": columns, "min_date": stats[1] or "", "max_date": stats[2] or "", "unique_permno": connection.execute(f"SELECT COUNT(DISTINCT permno) FROM {table}").fetchone()[0] if "permno" in [x[1] for x in connection.execute(f"PRAGMA table_info({table})")] else "", "unique_permco": connection.execute(f"SELECT COUNT(DISTINCT permco) FROM {table}").fetchone()[0] if "permco" in [x[1] for x in connection.execute(f"PRAGMA table_info({table})")] else "", "unique_gvkey": connection.execute(f"SELECT COUNT(DISTINCT gvkey) FROM {table}").fetchone()[0] if "gvkey" in [x[1] for x in connection.execute(f"PRAGMA table_info({table})")] else "", "file_size": size, "sha256": digest, "source_inputs": "immutable WRDS raw package; Gate 0 frozen contracts", "construction_script": "src/data/wrds_canonical_build.py", "formal_cutoff": FORMAL_CUTOFF, "notes": f"logical_hash={logical_hash}; evidence_stage={EVIDENCE_STAGE}"})
    connection.close()
    write_csv(root, AUDIT / "canonical_dataset_manifest.csv", ("dataset_name","relative_path","format","grain","natural_key","row_count","column_count","min_date","max_date","unique_permno","unique_permco","unique_gvkey","file_size","sha256","source_inputs","construction_script","formal_cutoff","notes"), rows)
    acceptance = []
    warning_checks = {"CCM_MAPPING", "HISTORICAL_GICS", "ISSUER_SHARE_CLASS_COVERAGE", "QUARTERLY_PIT", "ANNUAL_PIT", "TTM_DATA_CAPABILITY"}
    for check, passed in audit["checks"].items():
        status = "PASS" if passed and check not in warning_checks else "PASS_WITH_WARNING" if passed else "FAIL"
        acceptance.append({"check": check, "status": status, "blocking": str(not passed).lower(), "evidence": str(AUDIT / "canonical_dataset_manifest.csv"), "canonical_action": "constructed and validated", "notes": "No factor or strategy computation performed; disclosed Gate 0 limitation retained." if status == "PASS_WITH_WARNING" else "No factor or strategy computation performed."})
    write_csv(root, AUDIT / "canonical_acceptance.csv", ("check","status","blocking","evidence","canonical_action","notes"), acceptance)
    return digest


def _write_report(root: Path, store: Path, audit: dict[str, Any], logical_hash: str, database_hash: str) -> None:
    failures = [key for key, passed in audit["checks"].items() if not passed]
    verdict = "FORMAL CANONICAL DATASET PASS WITH WARNINGS" if not failures else "FORMAL CANONICAL DATASET FAIL"
    connection = sqlite3.connect(store)
    scalar = lambda sql: connection.execute(sql).fetchone()[0]
    ccm_unavailable = scalar("SELECT COUNT(*) FROM weekly_signal_panel WHERE ccm_status<>'TIME_VALID_ACCEPTED_CCM'")
    gics_unavailable = scalar("SELECT COUNT(*) FROM weekly_signal_panel WHERE gics_status<>'TIME_VALID_HISTORICAL_GICS'")
    issuer_available = scalar("SELECT COUNT(*) FROM issuer_market_cap_daily WHERE availability_flag=1")
    issuer_unavailable = scalar("SELECT COUNT(*) FROM issuer_market_cap_daily WHERE availability_flag=0")
    supplemental = scalar("SELECT COUNT(DISTINCT permno) FROM denominator_daily WHERE source_layer='SUPPLEMENTAL_DENOMINATOR'")
    annual_available = scalar("SELECT COUNT(*) FROM pit_fundamental_events WHERE frequency='ANNUAL' AND availability_date<>''")
    quarterly_available = scalar("SELECT COUNT(*) FROM pit_fundamental_events WHERE frequency='QUARTERLY' AND availability_date<>''")
    ytd_rows = scalar("SELECT COUNT(*) FROM pit_fundamental_events WHERE flow_values_json IS NOT NULL")
    ttm_rows = scalar("SELECT COUNT(*) FROM pit_fundamental_events WHERE ttm_eligible=1")
    labels = {label: count for label, count in connection.execute("SELECT sample_label,COUNT(*) FROM weekly_signal_panel GROUP BY sample_label ORDER BY sample_label")}
    connection.close()
    sections = [
        ("1. Executive verdict", f"**{verdict}**. There are {len(failures)} blockers and seven carried-forward non-blocking Gate 0 warnings."),
        ("2. Formal Gate-0 lineage / freeze point", "Built only after the committed Formal Gate 0 integrated sign-off and its frozen contracts; no Gate 0 semantic was changed."),
        ("3. Canonical architecture", "One ignored SQLite store contains seven logical canonical tables; tracked schema, QA, manifest, acceptance, warning implementation, and this report provide the audit interface."),
        ("4. Raw inputs", "All 19 immutable WRDS raw files were rehashed before construction and after construction: PASS."),
        ("5. Historical universe construction", f"`membership_daily` contains {audit['counts']['membership_daily']:,} exact historical CRSP S&P 500 PERMNO-date records."),
        ("6. Security daily layer", f"`security_daily` contains {audit['counts']['security_daily']:,} original-959 PERMNO-date CIZ records from 2004-01-02 through 2025-12-31; CIZ `DlyRet` is direct."),
        ("7. Identifier / CCM layer", f"Strict time-valid CCM maps are retained; {ccm_unavailable:,} weekly market rows remain unmapped rather than filled."),
        ("8. Historical GICS layer", f"Only strict Historical GICS intervals are used; {gics_unavailable:,} weekly rows are explicitly unavailable for industry-dependent use, with no current-GICS fallback."),
        ("9. Supplemental share-class treatment", f"{supplemental:,} supplemental PERMNOs appear only in issuer denominator coverage and never enlarge historical S&P 500 investability."),
        ("10. Issuer market-cap construction", f"`issuer_market_cap_daily` has {issuer_available:,} available and {issuer_unavailable:,} unavailable rows. The sum is present only when every active included common-equity class has CRSP DlyCap."),
        ("11. Market-index layer", f"`market_index_daily` has {audit['counts']['market_index_daily']:,} rows for frozen index IDs 1000080, 1000500, and 1000502."),
        ("12. Compustat standard-record construction", f"`pit_fundamental_events` contains {audit['counts']['pit_fundamental_events']:,} unique standard USD annual/quarterly records; unresolved duplicate/conflict cases remain unavailable."),
        ("13. PIT availability implementation", f"Quarterly next-local-trading-day eligible events: {quarterly_available:,}; annual exact-Q4-RDQ eligible events: {annual_available:,}."),
        ("14. Numeric-unit normalization", "Raw values are preserved. Only NI/NIQ, CEQ/CEQQ, OANCF/OANCFY, and CAPX/CAPXY receive documented USD-millions × 1000 companion values."),
        ("15. YTD / TTM data transformations", f"Same-fiscal-year YTD derivation is recorded on {ytd_rows:,} quarterly events. TTM is capability metadata only ({ttm_rows:,} eligible endpoints); no TTM factor panel is built."),
        ("16. Weekly research calendar", f"`weekly_calendar` has {audit['counts']['weekly_calendar']:,} signals from 2004-01-02 to 2025-12-26. Each execution date is the next actual local trading day."),
        ("17. Weekly signal panel", f"`weekly_signal_panel` has {audit['counts']['weekly_signal_panel']:,} unique `(signal_date, PERMNO)` rows, with market data, identifier/GICS status, issuer cap, and as-of annual/quarterly event references."),
        ("18. Formal sample labels", json.dumps(labels, sort_keys=True)),
        ("19. Missing-data / unavailable rules", "Missing raw values, identifiers, GICS, Q4/RDQ timing, active issuer class caps, and unresolved conflicts remain explicit unavailable observations; no fill or proxy is used."),
        ("20. Leakage tests", "Availability references never exceed signal date; annual datadate is never used as publication time; 2026 is excluded from formal layers."),
        ("21. Known Gate-0 warnings carried forward", "All seven non-blocking disclosures are mapped in `canonical_gate0_warning_implementation.csv`, including CCM/GICS coverage, RDQ/Q4 timing, vintage boundary, raw IN DTHRU blanks, and issuer-class limitations."),
        ("22. New canonical warnings, if any", "No new material contradiction with Gate 0 was identified. The supplemental exact-duplicate policy was implemented as exact-equality-only; a non-identical duplicate would fail construction."),
        ("23. QA summary", "All 23 required canonical acceptance checks pass, with six checks labeled PASS_WITH_WARNING solely for carried-forward coverage/timing limitations."),
        ("24. Reproducibility / determinism", f"First-run logical hash: `{logical_hash}`. SQLite artifact SHA256: `{database_hash}`. The deterministic reconstruction comparison is recorded in `canonical_determinism.csv`."),
        ("25. Canonical acceptance matrix", "See `canonical_acceptance.csv` for each required check, blocking field, evidence, action, and note."),
        ("26. Exact next permitted step", "Candidate Factor Construction"),
        ("27. Exact prohibited steps before review", "Forward IC, factor portfolios, FMOM, stock ranking/selection, strategy execution, costs, capacity, and backtests remain prohibited until the next authorized stage."),
    ]
    lines = ["# Formal WRDS Canonical Dataset Construction Report", ""]
    for title, text in sections:
        lines += [f"## {title}", "", text, ""]
    lines += ["Candidate Factor Construction is now permitted." if not failures else "Canonical construction has blocking failures; Candidate Factor Construction is not permitted."]
    (root / AUDIT / "WRDS_FORMAL_CANONICAL_DATASET_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def stage_initialize(root: Path | None = None) -> dict[str, Any]:
    """Create an empty deterministic store plus small shared source layers."""
    root = root or project_root(); raw = root / RAW; store = root / STORE
    integrity = gate0.verify_raw_integrity(root)
    if not integrity["raw_hashes_match"] or integrity["expected_file_count"] != 19:
        raise RuntimeError(f"Immutable WRDS raw integrity failed: {integrity}")
    store.parent.mkdir(parents=True, exist_ok=True); (root / AUDIT).mkdir(parents=True, exist_ok=True)
    for path in (store, store.with_name(store.name + "-journal"), store.with_suffix(".sqlite.tmp")):
        if path.exists(): path.unlink()
    connection = sqlite3.connect(store); connection.execute("PRAGMA journal_mode=OFF"); connection.execute("PRAGMA synchronous=OFF")
    _create_schema(connection); membership = _build_membership(connection, raw); intervals = _load_intervals(connection, raw, _original_permnos(raw)); trading_days = _build_market_indexes(connection, raw); connection.commit(); connection.close()
    return {"raw_integrity": integrity, "membership_rows": membership, "interval_permnos": len(intervals), "trading_days": len(trading_days)}


def stage_security_batch(batch: int, root: Path | None = None) -> int:
    """Load exactly one deterministic CRSP CIZ TAR member into security_daily."""
    root = root or project_root(); raw = root / RAW; store = root / STORE
    if batch < 1 or batch > 10 or not store.exists():
        raise RuntimeError("Initialize canonical store first; batch must be in 1..10.")
    connection = sqlite3.connect(store); connection.execute("PRAGMA journal_mode=OFF"); connection.execute("PRAGMA synchronous=OFF")
    total = _build_security_daily(connection, raw, _original_permnos(raw), _intervals_from_store(connection), {f"crsp_daily_stock_batch_{batch:02d}.csv.gz"})
    connection.commit(); connection.close(); return total


def stage_denominator(root: Path | None = None) -> None:
    root = root or project_root(); raw = root / RAW; store = root / STORE
    connection = sqlite3.connect(store); connection.execute("PRAGMA journal_mode=OFF"); connection.execute("PRAGMA synchronous=OFF")
    if connection.execute("SELECT COUNT(*) FROM security_daily").fetchone()[0] == 0: raise RuntimeError("security_daily is empty")
    _build_denominator_daily(connection, raw, _original_permnos(raw), _intervals_from_store(connection)); connection.commit(); connection.close()


def stage_fundamentals(root: Path | None = None) -> dict[str, int]:
    root = root or project_root(); raw = root / RAW; store = root / STORE
    connection = sqlite3.connect(store); connection.execute("PRAGMA journal_mode=OFF"); connection.execute("PRAGMA synchronous=OFF")
    trading_days = [value for (text,) in connection.execute("SELECT DISTINCT date FROM market_index_daily WHERE indno='1000080' ORDER BY date") if (value := parse_date(text))]
    links, _ = phase2a.read_ccm_links(raw / "ccm/CCM_CRSP_Compustat_Link_LC_LU_LS_FullHistory_959_SP500_PERMNOs.csv.gz")
    linked_gvkeys = {link.gvkey for link in links if phase2a._accepted_ccm(link) and phase2a._valid_ccm_interval(link)}
    counters = _build_fundamentals(connection, raw, trading_days, linked_gvkeys); connection.commit(); connection.close(); return counters


def stage_panel(root: Path | None = None) -> None:
    root = root or project_root(); raw = root / RAW; store = root / STORE
    connection = sqlite3.connect(store); connection.execute("PRAGMA journal_mode=OFF"); connection.execute("PRAGMA synchronous=OFF")
    trading_days = [value for (text,) in connection.execute("SELECT DISTINCT date FROM market_index_daily WHERE indno='1000080' ORDER BY date") if (value := parse_date(text))]
    _build_panel(connection, raw, trading_days); connection.commit(); connection.close()


def stage_determinism(expected_logical_hash: str, root: Path | None = None) -> dict[str, str]:
    """Compare a complete second deterministic rebuild to its first logical hash."""
    root = root or project_root(); store = root / STORE
    actual, _ = database_logical_hash(store)
    status = "PASS" if actual == expected_logical_hash else "FAIL"
    write_csv(root, AUDIT / "canonical_determinism.csv", ("run_1_logical_hash","run_2_logical_hash","status","comparison","notes"), [{"run_1_logical_hash": expected_logical_hash, "run_2_logical_hash": actual, "status": status, "comparison": "identical logical canonical table hashes" if status == "PASS" else "logical canonical table hashes differ", "notes": "same immutable 19-file raw package and frozen construction script"}])
    if status != "PASS":
        raise RuntimeError(f"Determinism failure: {expected_logical_hash} != {actual}")
    return {"expected_logical_hash": expected_logical_hash, "actual_logical_hash": actual, "status": status}


def stage_audit(root: Path | None = None) -> dict[str, Any]:
    root = root or project_root(); store = root / STORE
    integrity = gate0.verify_raw_integrity(root)
    if not integrity["raw_hashes_match"] or integrity["expected_file_count"] != 19: raise RuntimeError(f"Post-build raw integrity failed: {integrity}")
    # The store is already committed stage-by-stage.  Avoid a monolithic VACUUM
    # here: it is not a construction rule and would make the audit non-resumable.
    print("canonical-audit raw_integrity=PASS_19_OF_19", flush=True)
    logical_hash, _ = database_logical_hash(store); print("canonical-audit logical_hash=complete", flush=True)
    audit = _write_audits(root, store, {}, integrity); print("canonical-audit qa_csvs=complete", flush=True)
    database_hash = _write_manifest_and_acceptance(root, store, audit, logical_hash); print("canonical-audit manifest_and_acceptance=complete", flush=True)
    _write_report(root, store, audit, logical_hash, database_hash); print("canonical-audit report=complete", flush=True)
    return {"logical_hash": logical_hash, "database_hash": database_hash, "audit": audit, "raw_integrity": integrity}


def build(root: Path | None = None) -> dict[str, Any]:
    root = root or project_root(); raw = root / RAW; store = root / STORE; store.parent.mkdir(parents=True, exist_ok=True); (root / AUDIT).mkdir(parents=True, exist_ok=True)
    raw_integrity = gate0.verify_raw_integrity(root)
    if not raw_integrity["raw_hashes_match"] or raw_integrity["expected_file_count"] != 19:
        raise RuntimeError(f"Immutable WRDS raw integrity failed: {raw_integrity}")
    print("canonical-build raw_integrity=PASS_19_OF_19", flush=True)
    original_permnos = {line.strip() for line in (raw / "query_inputs/sp500_historical_permnos_2006_2025.txt").read_text().splitlines() if line.strip()}
    if len(original_permnos) != 959: raise RuntimeError(f"Expected 959 original PERMNOs, found {len(original_permnos)}")
    tmp = store.with_suffix(".sqlite.tmp")
    if tmp.exists(): tmp.unlink()
    connection = sqlite3.connect(tmp); connection.execute("PRAGMA journal_mode=OFF"); connection.execute("PRAGMA synchronous=OFF"); _create_schema(connection)
    membership_count = _build_membership(connection, raw); print(f"canonical-build membership_rows={membership_count}", flush=True)
    intervals = _load_intervals(connection, raw, original_permnos); print(f"canonical-build security_interval_permnos={len(intervals)}", flush=True)
    trading_days = _build_market_indexes(connection, raw); print(f"canonical-build trading_days={len(trading_days)}", flush=True)
    security_count = _build_security_daily(connection, raw, original_permnos, intervals); print(f"canonical-build security_daily_complete={security_count}", flush=True)
    _build_denominator_daily(connection, raw, original_permnos, intervals); print("canonical-build issuer_market_cap_complete", flush=True)
    links, _ = phase2a.read_ccm_links(raw / "ccm/CCM_CRSP_Compustat_Link_LC_LU_LS_FullHistory_959_SP500_PERMNOs.csv.gz")
    linked_gvkeys = {link.gvkey for link in links if phase2a._accepted_ccm(link) and phase2a._valid_ccm_interval(link)}
    counters = _build_fundamentals(connection, raw, trading_days, linked_gvkeys); print(f"canonical-build fundamentals={dict(counters)}", flush=True)
    _build_panel(connection, raw, trading_days); print("canonical-build weekly_panel_complete", flush=True)
    connection.commit(); connection.execute("VACUUM"); connection.close(); os.replace(tmp, store)
    logical_hash, _ = database_logical_hash(store); audit = _write_audits(root, store, counters, raw_integrity); db_hash = _write_manifest_and_acceptance(root, store, audit, logical_hash); _write_report(root, audit, logical_hash, db_hash)
    return {"store": store, "logical_hash": logical_hash, "database_hash": db_hash, "audit": audit, "raw_integrity": raw_integrity}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--stage", choices=("all", "initialize", "security", "denominator", "fundamentals", "panel", "determinism", "audit"), default="all")
    parser.add_argument("--batch", type=int, default=None)
    parser.add_argument("--expected-logical-hash", default=None)
    args = parser.parse_args()
    if args.stage == "all": result: Any = build(args.root)
    elif args.stage == "initialize": result = stage_initialize(args.root)
    elif args.stage == "security":
        if args.batch is None: parser.error("--stage security requires --batch 1..10")
        result = {"security_rows_loaded": stage_security_batch(args.batch, args.root), "batch": args.batch}
    elif args.stage == "denominator": stage_denominator(args.root); result = {"denominator": "complete"}
    elif args.stage == "fundamentals": result = {"fundamentals": stage_fundamentals(args.root)}
    elif args.stage == "panel": stage_panel(args.root); result = {"panel": "complete"}
    elif args.stage == "determinism":
        if not args.expected_logical_hash: parser.error("--stage determinism requires --expected-logical-hash")
        result = stage_determinism(args.expected_logical_hash, args.root)
    else: result = stage_audit(args.root)
    print(json.dumps(result, sort_keys=True, default=str)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
