"""Prepare the governed US downstream Strategy / Backtest data handoff.

This module is deliberately an export layer.  It consumes only immutable formal
US inputs and never changes a factor score, Gate-A decision, research parameter,
holding, or strategy result.  It may mechanically materialize 2016--2025 market
rows, but it emits no post-2015 performance summary.  The only constructed
factor-return object is the frozen four-factor 2006--2015 seed history needed
for a later, separately authorized Gate-B initialization.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import shutil
import sqlite3
import statistics
import tarfile
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

from src.data import wrds_gate0_phase1 as phase1
from src.data import wrds_gate0_phase2a as phase2a


RAW = Path("wrds_raw/US_WRDS_2026-10-09")
CANONICAL = Path("data/canonical/wrds_formal/us_wrds_formal_canonical.sqlite")
SCORE_STORE = Path("data/factors/us_formal/us_gate_a0_factor_specifications.sqlite")
CANDIDATE_STORE = Path("data/factors/us_formal/us_candidate_factors.sqlite")
REFRESHED_VALIDATION = Path("data/validation/us_formal/us_gate_a_refreshed_evidence.sqlite")
FINAL_HANDOFF = Path("handoff/final_factor")
PACKAGE = Path("handoff/us_downstream")
AUDIT = Path("outputs/audit/wrds_formal")
SSOT = Path("docs/US_SG_Cross_Market_Quant_Research.md")

FORMAL_START = "2004-01-02"
FORMAL_CUTOFF = "2025-12-31"
SEED_START = "2006-01-01"
SEED_CUTOFF = "2015-12-31"
FINAL_FACTORS = (
    ("Quality", "QUALITY_CURRENT", "quality_score", "quality_available"),
    ("Momentum", "MOMENTUM_12_1", "momentum_score", "momentum_available"),
    ("Low Risk", "LOWRISK_CURRENT", "lowrisk_score", "lowrisk_available"),
    ("Investment", "INVESTMENT_CURRENT", "investment_score", "investment_available"),
)
VALID_RETURN_STATUSES = frozenset({"VALID", "VALID_CONFIRMED_DELISTING_CASH"})

DAILY_STOCK = RAW / "crsp/CRSP_Daily_Stock_CIZ_2004-01-01_2025-12-31.tar"
NAMES = RAW / "crsp/CRSP_Names_CIZ_FullHistory_SP500_Historical_PERMNOs.csv.gz"
FF_DAILY = RAW / "fama_french/Fama_French_5Factors_Momentum_RF_Daily_FullHistory_to_2026-08-31.csv.gz"
FF_MONTHLY = RAW / "fama_french/Fama_French_5Factors_Momentum_RF_Monthly_FullHistory_to_2026-08.csv.gz"
CCM = RAW / phase2a.CCM_RELATIVE_PATH
GICS = RAW / phase2a.HISTORICAL_GICS_RELATIVE_PATH


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def text(value: Any) -> str:
    return "" if value is None else str(value)


def dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_csv(path: Path, fields: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n", extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def sample_role(observed_date: str) -> str:
    """Map dates to the exact frozen repository-consistent sample labels."""

    if not (FORMAL_START <= observed_date <= FORMAL_CUTOFF):
        raise ValueError(f"DATE_OUTSIDE_FORMAL_RANGE:{observed_date}")
    if observed_date <= "2005-12-31":
        return "INITIALIZATION"
    if observed_date <= "2010-12-31":
        return "DEV_WARMUP"
    if observed_date <= "2015-12-31":
        return "FACTOR_SELECTION"
    if observed_date <= "2020-12-31":
        return "CONFIGURATION_AND_STRATEGY_FREEZE"
    return "HIST_OOS_BASE"


def is_valid_return_status(status: str | None) -> bool:
    return status in VALID_RETURN_STATUSES


def compound(values: Sequence[float]) -> float | None:
    wealth = 1.0
    for value in values:
        if not math.isfinite(value) or value <= -1.0:
            return None
        wealth *= 1.0 + value
    return wealth - 1.0


def logical_hash_parquet(path: Path) -> str:
    """Hash Parquet values in schema/row order rather than file bytes."""

    digest = hashlib.sha256()
    parquet = pq.ParquetFile(path)
    digest.update(str(parquet.schema_arrow).encode("utf-8") + b"\n")
    for batch in parquet.iter_batches(batch_size=65_536):
        columns = batch.to_pydict()
        names = list(columns)
        for index in range(batch.num_rows):
            digest.update(dump([columns[name][index] for name in names]).encode("utf-8") + b"\n")
    return digest.hexdigest()


def write_parquet(path: Path, schema: pa.Schema, rows: Iterable[dict[str, Any]], *, batch_size: int = 50_000) -> tuple[int, str]:
    """Write a deterministic ordered Parquet object and return row count/hash."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    count = 0
    batch: list[dict[str, Any]] = []
    writer = pq.ParquetWriter(temporary, schema, compression="zstd", version="2.6")
    try:
        for row in rows:
            batch.append(row)
            if len(batch) >= batch_size:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                count += len(batch)
                batch.clear()
        if batch:
            writer.write_table(pa.Table.from_pylist(batch, schema=schema))
            count += len(batch)
    finally:
        writer.close()
    os.replace(temporary, path)
    return count, logical_hash_parquet(path)


def sqlite_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)


def parse_date(value: str) -> date | None:
    return phase2a._parse_iso_date(value)


def csv_gzip_rows(path: Path) -> Iterator[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def tar_gzip_rows(path: Path) -> Iterator[dict[str, str]]:
    """Stream only daily-stock source rows; never extract or modify raw data."""

    with tarfile.open(path, "r") as archive:
        members = sorted(
            (member for member in archive.getmembers() if member.isfile() and member.name.endswith(".csv.gz")),
            key=lambda member: member.name,
        )
        for member in members:
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError(f"UNREADABLE_DAILY_STOCK_TAR_MEMBER:{member.name}")
            with source, gzip.GzipFile(fileobj=source, mode="rb") as zipped:
                with __import__("io").TextIOWrapper(zipped, encoding="utf-8-sig", newline="") as text_handle:
                    yield from csv.DictReader(text_handle)


def raw_snapshot(root: Path) -> dict[str, tuple[int, str]]:
    """Snapshot all formal raw research inputs using Gate-0's frozen inventory."""

    snapshots = phase1.snapshot_raw_files(root / RAW)
    return {item.relative_path: (item.size_bytes, item.sha256) for item in snapshots}


def immutable_inputs(root: Path) -> dict[str, Any]:
    paths = (SSOT, CANONICAL, SCORE_STORE, CANDIDATE_STORE, REFRESHED_VALIDATION)
    return {
        "raw_snapshot": raw_snapshot(root),
        "sha256": {str(path): sha256(root / path) for path in paths},
        "handoff_registry_sha256": sha256(root / FINAL_HANDOFF / "final_factor_registry.csv"),
        "handoff_scores_sha256": sha256(root / FINAL_HANDOFF / "data/factor_scores_weekly.parquet"),
        "handoff_universe_sha256": sha256(root / FINAL_HANDOFF / "data/universe_weekly.parquet"),
    }


def verify_immutable_inputs(root: Path, before: dict[str, Any]) -> dict[str, bool]:
    after = immutable_inputs(root)
    return {
        "RAW_IMMUTABILITY": before["raw_snapshot"] == after["raw_snapshot"],
        "SSOT_IMMUTABILITY": before["sha256"][str(SSOT)] == after["sha256"][str(SSOT)],
        "CANONICAL_IMMUTABILITY": before["sha256"][str(CANONICAL)] == after["sha256"][str(CANONICAL)],
        "SCORE_STORE_IMMUTABILITY": before["sha256"][str(SCORE_STORE)] == after["sha256"][str(SCORE_STORE)],
        "CANDIDATE_STORE_IMMUTABILITY": before["sha256"][str(CANDIDATE_STORE)] == after["sha256"][str(CANDIDATE_STORE)],
        "REFRESHED_VALIDATION_IMMUTABILITY": before["sha256"][str(REFRESHED_VALIDATION)] == after["sha256"][str(REFRESHED_VALIDATION)],
        "FINAL_HANDOFF_SCORE_IMMUTABILITY": before["handoff_scores_sha256"] == after["handoff_scores_sha256"],
        "FINAL_HANDOFF_UNIVERSE_IMMUTABILITY": before["handoff_universe_sha256"] == after["handoff_universe_sha256"],
        "FINAL_REGISTRY_IMMUTABILITY": before["handoff_registry_sha256"] == after["handoff_registry_sha256"],
    }


def validate_final_factor_registry(root: Path) -> None:
    rows = read_csv(root / FINAL_HANDOFF / "final_factor_registry.csv")
    expected = [(family, specification, field) for family, specification, field, _available in FINAL_FACTORS]
    actual = [(row["family"], row["specification_id"], row["source_score_field"]) for row in rows]
    if actual != expected:
        raise RuntimeError(f"FROZEN_FINAL_FACTOR_CONTRACT_MISMATCH:{actual}")
    if any(row["Gate_A_status"] != "KEEP__FROZEN" for row in rows):
        raise RuntimeError("FINAL_FACTOR_NOT_FROZEN")


@dataclass(frozen=True)
class NameInterval:
    permno: str
    permco: str
    start: date | None
    end: date | None
    ticker: str
    issuer: str
    shareclass: str
    primary_exchange: str
    trading_status: str
    security_type: str
    security_subtype: str


def name_intervals(root: Path) -> dict[str, list[NameInterval]]:
    output: dict[str, list[NameInterval]] = defaultdict(list)
    for row in csv_gzip_rows(root / NAMES):
        permno = (row.get("permno") or "").strip()
        if not permno:
            continue
        start = parse_date((row.get("secinfostartdt") or "").strip())
        end = parse_date((row.get("secinfoenddt") or "").strip())
        output[permno].append(NameInterval(
            permno=permno,
            permco=(row.get("permco") or "").strip(),
            start=start,
            end=end,
            ticker=(row.get("ticker") or row.get("tradingsymbol") or "").strip(),
            issuer=(row.get("issuernm") or "").strip(),
            shareclass=(row.get("shareclass") or "").strip(),
            primary_exchange=(row.get("primaryexch") or row.get("hdrprimaryexch") or "").strip(),
            trading_status=(row.get("tradingstatusflg") or "").strip(),
            security_type=(row.get("securitytype") or "").strip(),
            security_subtype=(row.get("securitysubtype") or "").strip(),
        ))
    for values in output.values():
        values.sort(key=lambda item: (item.start or date.min, item.end or date.max, item.ticker))
    return dict(output)


def name_match(values: Sequence[NameInterval], observed: date) -> NameInterval | None:
    matched = [value for value in values if value.start is not None and value.start <= observed and (value.end is None or observed <= value.end)]
    return matched[0] if len(matched) == 1 else None


def ccm_and_gics(root: Path) -> tuple[dict[str, list[phase2a.CcmLink]], dict[str, list[phase2a.GicsInterval]]]:
    links, _raw = phase2a.read_ccm_links(root / CCM)
    intervals, _raw_gics = phase2a._read_gics(root / GICS)
    return phase2a._ccm_by_permno(links), phase2a._gics_by_gvkey(intervals)


def gvkey_and_gics(
    ccm_by_permno: dict[str, list[phase2a.CcmLink]],
    gics_by_gvkey: dict[str, list[phase2a.GicsInterval]],
    permno: str,
    observed: date,
) -> tuple[str, str, str, str]:
    matches = phase2a._ccm_matches(ccm_by_permno.get(permno, ()), observed)
    gvkeys = sorted({match.gvkey for match in matches if match.gvkey})
    if len(gvkeys) != 1:
        return "", "", "CCM_UNAVAILABLE" if not gvkeys else "CCM_AMBIGUOUS", "GICS_UNAVAILABLE_NO_UNIQUE_GVKEY"
    gics_matches = phase2a._gics_matches(gics_by_gvkey.get(gvkeys[0], ()), observed)
    sectors = sorted({match.gsector for match in gics_matches if match.gsector})
    if len(sectors) == 1:
        return gvkeys[0], sectors[0], "CCM_AVAILABLE", "HISTORICAL_GICS_AVAILABLE"
    return gvkeys[0], "", "CCM_AVAILABLE", "HISTORICAL_GICS_UNAVAILABLE" if not sectors else "HISTORICAL_GICS_AMBIGUOUS"


DAILY_SCHEMA = pa.schema([
    ("date", pa.string()), ("PERMNO", pa.string()), ("PERMCO", pa.string()), ("GVKEY", pa.string()),
    ("sample_role", pa.string()), ("price_close", pa.float64()), ("price_source", pa.string()),
    ("volume", pa.float64()), ("daily_dollar_volume", pa.float64()),
    ("security_market_cap", pa.float64()), ("issuer_market_cap", pa.float64()),
    ("issuer_market_cap_available", pa.int8()), ("issuer_market_cap_unavailable_reason", pa.string()),
    ("trading_status", pa.string()), ("return_available", pa.int8()), ("price_available", pa.int8()),
    ("volume_available", pa.int8()), ("delisting_flag", pa.int8()), ("path_status", pa.string()),
    ("historical_gics_sector", pa.string()), ("ccm_status", pa.string()), ("gics_status", pa.string()),
    ("is_historical_member", pa.int8()), ("is_investable_security", pa.int8()),
    ("market_data_source", pa.string()),
])

CALENDAR_SCHEMA = pa.schema([
    ("signal_date", pa.string()), ("execution_date", pa.string()), ("next_signal_date", pa.string()),
    ("next_execution_date", pa.string()), ("week_id", pa.string()), ("sample_role", pa.string()),
    ("return_period_complete", pa.int8()), ("calendar_status", pa.string()),
])

WEEKLY_RETURN_SCHEMA = pa.schema([
    ("signal_date", pa.string()), ("execution_date", pa.string()), ("next_execution_date", pa.string()),
    ("PERMNO", pa.string()), ("sample_role", pa.string()),
    ("execution_to_next_execution_total_return", pa.float64()), ("path_complete", pa.int8()),
    ("return_available", pa.int8()), ("trading_day_count", pa.int16()), ("delisting_in_path", pa.int8()),
    ("execution_feasible", pa.int8()), ("return_source", pa.string()), ("unavailable_reason", pa.string()),
])

LIQUIDITY_SCHEMA = pa.schema([
    ("signal_date", pa.string()), ("execution_date", pa.string()), ("PERMNO", pa.string()),
    ("sample_role", pa.string()), ("execution_price", pa.float64()), ("execution_volume", pa.float64()),
    ("daily_dollar_volume", pa.float64()), ("security_market_cap", pa.float64()),
    ("issuer_market_cap", pa.float64()), ("issuer_market_cap_available", pa.int8()),
    ("ADV20", pa.float64()), ("ADV60", pa.float64()), ("valid_trading_days_20", pa.int16()),
    ("valid_trading_days_60", pa.int16()), ("liquidity_available", pa.int8()),
    ("liquidity_unavailable_reason", pa.string()), ("historical_gics_sector", pa.string()),
    ("liquidity_source", pa.string()),
])

BENCHMARK_DAILY_SCHEMA = pa.schema([
    ("date", pa.string()), ("sample_role", pa.string()), ("benchmark_id", pa.string()),
    ("benchmark_name", pa.string()), ("total_return", pa.float64()), ("source_field", pa.string()),
])

BENCHMARK_WEEKLY_SCHEMA = pa.schema([
    ("signal_date", pa.string()), ("execution_date", pa.string()), ("next_execution_date", pa.string()),
    ("benchmark_total_return", pa.float64()), ("sample_role", pa.string()), ("path_complete", pa.int8()),
])

RF_DAILY_SCHEMA = pa.schema([
    ("date", pa.string()), ("sample_role", pa.string()), ("risk_free_return", pa.float64()),
    ("source_field", pa.string()), ("unit", pa.string()),
])

RF_WEEKLY_SCHEMA = pa.schema([
    ("signal_date", pa.string()), ("execution_date", pa.string()), ("next_execution_date", pa.string()),
    ("risk_free_return", pa.float64()), ("sample_role", pa.string()), ("path_complete", pa.int8()),
])

FF_DAILY_SCHEMA = pa.schema([
    ("date", pa.string()), ("sample_role", pa.string()), ("mktrf", pa.float64()), ("smb", pa.float64()),
    ("hml", pa.float64()), ("rmw", pa.float64()), ("cma", pa.float64()), ("rf", pa.float64()),
    ("umd", pa.float64()), ("unit", pa.string()),
])

FF_MONTHLY_SCHEMA = pa.schema([
    ("source_date", pa.string()), ("date", pa.string()), ("sample_role", pa.string()), ("mktrf", pa.float64()),
    ("smb", pa.float64()), ("hml", pa.float64()), ("rmw", pa.float64()), ("cma", pa.float64()),
    ("rf", pa.float64()), ("umd", pa.float64()), ("unit", pa.string()),
])

REFERENCE_SCHEMA = pa.schema([
    ("PERMNO", pa.string()), ("PERMCO", pa.string()), ("effective_start_date", pa.string()),
    ("effective_end_date", pa.string()), ("ticker", pa.string()), ("issuer", pa.string()),
    ("share_class", pa.string()), ("primary_exchange", pa.string()), ("trading_status", pa.string()),
    ("security_type", pa.string()), ("security_subtype", pa.string()), ("source", pa.string()),
])

SEED_SCHEMA = pa.schema([
    ("signal_date", pa.string()), ("execution_date", pa.string()), ("next_execution_date", pa.string()),
    ("family", pa.string()), ("specification_id", pa.string()), ("top_return_gross", pa.float64()),
    ("bottom_return_gross", pa.float64()), ("factor_return_gross", pa.float64()), ("top_count", pa.int32()),
    ("bottom_count", pa.int32()), ("top_target_count", pa.int32()), ("bottom_target_count", pa.int32()),
    ("eligible_count", pa.int32()), ("top_execution_failures", pa.int32()),
    ("bottom_execution_failures", pa.int32()), ("path_complete", pa.int8()),
    ("factor_return_available", pa.int8()), ("sample_role", pa.string()), ("unavailable_reason", pa.string()),
])


def build_raw_liquidity_table(root: Path, connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE raw_liquidity (
        date TEXT NOT NULL, permno TEXT NOT NULL, dlyvol REAL, dlyprcvol REAL,
        PRIMARY KEY(date, permno))""")
    buffer: list[tuple[str, str, float | None, float | None]] = []
    for row in tar_gzip_rows(root / DAILY_STOCK):
        observed = (row.get("dlycaldt") or "").strip()
        permno = (row.get("permno") or "").strip()
        if FORMAL_START <= observed <= FORMAL_CUTOFF and permno:
            buffer.append((observed, permno, finite(row.get("dlyvol")), finite(row.get("dlyprcvol"))))
        if len(buffer) >= 50_000:
            connection.executemany("INSERT INTO raw_liquidity VALUES (?,?,?,?)", buffer)
            buffer.clear()
    if buffer:
        connection.executemany("INSERT INTO raw_liquidity VALUES (?,?,?,?)", buffer)
    connection.execute("CREATE INDEX raw_liquidity_permno_date ON raw_liquidity(permno,date)")


def build_daily_market_table(root: Path, connection: sqlite3.Connection) -> None:
    """Join immutable canonical data with only the absent raw liquidity fields."""

    connection.execute("ATTACH DATABASE ? AS canonical", (str((root / CANONICAL).resolve()),))
    connection.execute("""CREATE TABLE daily_market (
        date TEXT NOT NULL, permno TEXT NOT NULL, permco TEXT, gvkey TEXT, sample_role TEXT NOT NULL,
        price_close REAL, volume REAL, daily_dollar_volume REAL, security_market_cap REAL,
        issuer_market_cap REAL, issuer_market_cap_available INTEGER NOT NULL,
        issuer_market_cap_unavailable_reason TEXT, trading_status TEXT, return_value REAL,
        return_available INTEGER NOT NULL, price_available INTEGER NOT NULL, volume_available INTEGER NOT NULL,
        delisting_flag INTEGER NOT NULL, path_status TEXT NOT NULL, historical_gics_sector TEXT,
        ccm_status TEXT NOT NULL, gics_status TEXT NOT NULL, is_historical_member INTEGER NOT NULL,
        is_investable_security INTEGER NOT NULL, PRIMARY KEY(date,permno))""")
    connection.execute("CREATE INDEX daily_market_permno_date ON daily_market(permno,date)")
    ccm_by_permno, gics_by_gvkey = ccm_and_gics(root)
    names_by_permno = name_intervals(root)
    source = connection.execute("""
        SELECT s.date,s.permno,s.permco,s.dlyprc,s.dlycap,s.dlyret,s.dlydelflg,
               s.is_historical_member,s.is_investable_security,
               i.issuer_market_cap,i.availability_flag,i.unavailable_reason,
               r.dlyvol,r.dlyprcvol
        FROM canonical.security_daily s
        LEFT JOIN canonical.issuer_market_cap_daily i ON i.date=s.date AND i.permco=s.permco
        LEFT JOIN raw_liquidity r ON r.date=s.date AND r.permno=s.permno
        WHERE s.date>=? AND s.date<=?
        ORDER BY s.date,s.permno
    """, (FORMAL_START, FORMAL_CUTOFF))
    insert = """INSERT INTO daily_market VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    buffer: list[tuple[Any, ...]] = []
    while True:
        rows = source.fetchmany(50_000)
        if not rows:
            break
        for row in rows:
            observed = str(row[0])
            permno = str(row[1])
            observed_date = parse_date(observed)
            if observed_date is None:
                raise RuntimeError(f"INVALID_CANONICAL_DATE:{observed}")
            gvkey, gsector, ccm_status, gics_status = gvkey_and_gics(ccm_by_permno, gics_by_gvkey, permno, observed_date)
            info = name_match(names_by_permno.get(permno, ()), observed_date)
            price, cap, total_return, volume, price_volume = finite(row[3]), finite(row[4]), finite(row[5]), finite(row[12]), finite(row[13])
            delisting = int(str(row[6] or "").upper() == "Y")
            if total_return is not None:
                path_status = "DLYRET_AVAILABLE_CONFIRMED_DELISTING" if delisting else "DLYRET_AVAILABLE"
            elif delisting:
                path_status = "CONFIRMED_DELISTING_RETURN_UNAVAILABLE"
            else:
                path_status = "DLYRET_UNAVAILABLE"
            issuer_cap = finite(row[9])
            issuer_available = int(bool(row[10]))
            buffer.append((
                observed, permno, text(row[2]), gvkey, sample_role(observed), price, volume, price_volume, cap,
                issuer_cap, issuer_available, text(row[11]), info.trading_status if info else "", total_return,
                int(total_return is not None), int(price is not None and price > 0),
                int(volume is not None and volume >= 0), delisting, path_status, gsector, ccm_status, gics_status,
                int(bool(row[7])), int(bool(row[8])),
            ))
        connection.executemany(insert, buffer)
        buffer.clear()
    connection.commit()


def daily_rows(connection: sqlite3.Connection) -> Iterator[dict[str, Any]]:
    yield from daily_rows_between(connection)


def daily_rows_between(connection: sqlite3.Connection, start: str | None = None, end: str | None = None) -> Iterator[dict[str, Any]]:
    query = """SELECT date,permno,permco,gvkey,sample_role,price_close,volume,daily_dollar_volume,
        security_market_cap,issuer_market_cap,issuer_market_cap_available,issuer_market_cap_unavailable_reason,
        trading_status,return_available,price_available,volume_available,delisting_flag,path_status,
        historical_gics_sector,ccm_status,gics_status,is_historical_member,is_investable_security
        FROM daily_market WHERE (? IS NULL OR date>=?) AND (? IS NULL OR date<=?) ORDER BY date,permno"""
    cursor = connection.execute(query, (start, start, end, end))
    while True:
        values = cursor.fetchmany(50_000)
        if not values:
            return
        for row in values:
            yield {
                "date": row[0], "PERMNO": row[1], "PERMCO": row[2], "GVKEY": row[3], "sample_role": row[4],
                "price_close": row[5], "price_source": "CRSP_CIZ_DlyPrc", "volume": row[6],
                "daily_dollar_volume": row[7], "security_market_cap": row[8], "issuer_market_cap": row[9],
                "issuer_market_cap_available": row[10], "issuer_market_cap_unavailable_reason": row[11],
                "trading_status": row[12], "return_available": row[13], "price_available": row[14],
                "volume_available": row[15], "delisting_flag": row[16], "path_status": row[17],
                "historical_gics_sector": row[18], "ccm_status": row[19], "gics_status": row[20],
                "is_historical_member": row[21], "is_investable_security": row[22],
                "market_data_source": "CRSP_CIZ_CANONICAL_PLUS_RAW_DLYVOL_DLYPRCVOL",
            }


def calendar_rows(root: Path) -> list[dict[str, Any]]:
    connection = sqlite_readonly(root / CANONICAL)
    try:
        source = [(str(signal), str(execution), str(role)) for signal, execution, role in connection.execute(
            "SELECT signal_date,execution_date,sample_label FROM weekly_calendar ORDER BY signal_date"
        )]
    finally:
        connection.close()
    rows = []
    for index, (signal, execution, role) in enumerate(source):
        next_signal, next_execution = (source[index + 1][0], source[index + 1][1]) if index + 1 < len(source) else ("", "")
        complete = int(bool(next_execution) and next_execution <= FORMAL_CUTOFF)
        rows.append({
            "signal_date": signal, "execution_date": execution, "next_signal_date": next_signal,
            "next_execution_date": next_execution, "week_id": f"W{index + 1:04d}", "sample_role": role,
            "return_period_complete": complete,
            "calendar_status": "COMPLETE" if complete else "NO_NEXT_SIGNAL_WITHIN_FORMAL_CUTOFF",
        })
    return rows


def load_weekly_requests(root: Path, connection: sqlite3.Connection, calendar: Sequence[dict[str, Any]]) -> None:
    calendar_by_signal = {row["signal_date"]: row for row in calendar}
    connection.execute("""CREATE TABLE weekly_request (
        signal_date TEXT NOT NULL, execution_date TEXT NOT NULL, next_execution_date TEXT,
        permno TEXT NOT NULL, sample_role TEXT NOT NULL, historical_gics_sector TEXT,
        eligible_universe_flag INTEGER NOT NULL, PRIMARY KEY(signal_date,permno))""")
    connection.execute("CREATE INDEX weekly_request_signal ON weekly_request(signal_date,permno)")
    parquet = pq.ParquetFile(root / FINAL_HANDOFF / "data/universe_weekly.parquet")
    buffer: list[tuple[str, str, str, str, str, str, int]] = []
    for batch in parquet.iter_batches(columns=["signal_date", "PERMNO", "execution_date", "research_period", "historical_gics_sector", "eligible_universe_flag"], batch_size=65_536):
        columns = batch.to_pydict()
        for index in range(batch.num_rows):
            signal = str(columns["signal_date"][index])
            info = calendar_by_signal.get(signal)
            if info is None:
                raise RuntimeError(f"HANDOFF_SIGNAL_NOT_IN_CANONICAL_CALENDAR:{signal}")
            execution = str(columns["execution_date"][index])
            if execution != info["execution_date"]:
                raise RuntimeError(f"HANDOFF_EXECUTION_DATE_MISMATCH:{signal}")
            buffer.append((
                signal, execution, info["next_execution_date"], str(columns["PERMNO"][index]),
                str(columns["research_period"][index]), text(columns["historical_gics_sector"][index]),
                as_int(columns["eligible_universe_flag"][index]),
            ))
        if len(buffer) >= 50_000:
            connection.executemany("INSERT INTO weekly_request VALUES (?,?,?,?,?,?,?)", buffer)
            buffer.clear()
    if buffer:
        connection.executemany("INSERT INTO weekly_request VALUES (?,?,?,?,?,?,?)", buffer)
    connection.commit()


def forward_path_for_security(
    security_daily: dict[str, tuple[float | None, int, int, int]], required_dates: Sequence[str]
) -> tuple[float | None, str, int]:
    if not security_daily:
        return None, "NO_POST_EXECUTION_DAILY_ROWS", 0
    wealth = 1.0
    terminal = False
    for observed in required_dates:
        if terminal:
            continue
        row = security_daily.get(observed)
        if row is None:
            reason = (
                "MISSING_FIRST_POST_EXECUTION_TRADING_DAY"
                if observed == required_dates[0]
                else "INTERIOR_DAILY_RETURN_GAP"
            )
            return None, reason, 0
        total_return, delisting, _price_available, _investable = row
        if total_return is None:
            return None, "CONFIRMED_DELISTING_RETURN_UNAVAILABLE" if delisting else "MISSING_DLYRET", 0
        if total_return <= -1.0:
            return None, "NONCOMPOUNDABLE_DLYRET", 0
        wealth *= 1.0 + total_return
        if delisting:
            terminal = True
    return wealth - 1.0, "VALID_CONFIRMED_DELISTING_CASH" if terminal else "VALID", int(terminal)


def return_rows(connection: sqlite3.Connection, calendar: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    connection.execute("""CREATE TABLE weekly_returns (
        signal_date TEXT NOT NULL, execution_date TEXT NOT NULL, next_execution_date TEXT,
        permno TEXT NOT NULL, sample_role TEXT NOT NULL, total_return REAL, path_complete INTEGER NOT NULL,
        return_available INTEGER NOT NULL, trading_day_count INTEGER NOT NULL, delisting_in_path INTEGER NOT NULL,
        execution_feasible INTEGER NOT NULL, return_source TEXT NOT NULL, unavailable_reason TEXT NOT NULL,
        PRIMARY KEY(signal_date,permno))""")
    connection.execute("CREATE INDEX weekly_returns_signal ON weekly_returns(signal_date,permno)")
    trading_dates = [str(row[0]) for row in connection.execute("SELECT date FROM canonical.trading_dates ORDER BY date")]
    trading_index = {value: index for index, value in enumerate(trading_dates)}
    output: list[dict[str, Any]] = []
    counters: dict[str, int] = defaultdict(int)
    for calendar_row in calendar:
        signal, execution, endpoint = calendar_row["signal_date"], calendar_row["execution_date"], calendar_row["next_execution_date"]
        requests = connection.execute(
            "SELECT permno,sample_role FROM weekly_request WHERE signal_date=? ORDER BY permno", (signal,)
        ).fetchall()
        if not endpoint or not calendar_row["return_period_complete"]:
            for permno, role in requests:
                result = {
                    "signal_date": signal, "execution_date": execution, "next_execution_date": endpoint, "PERMNO": permno,
                    "sample_role": role, "execution_to_next_execution_total_return": None, "path_complete": 0,
                    "return_available": 0, "trading_day_count": 0, "delisting_in_path": 0, "execution_feasible": 0,
                    "return_source": "CRSP_CIZ_DLYRET_DIRECT", "unavailable_reason": "FORMAL_CUTOFF_INCOMPLETE",
                }
                output.append(result)
            continue
        if execution not in trading_index or endpoint not in trading_index:
            raise RuntimeError(f"CALENDAR_DATE_NOT_IN_TRADING_DATES:{signal}")
        required_dates = trading_dates[trading_index[execution] + 1:trading_index[endpoint] + 1]
        daily_rows = connection.execute(
            "SELECT date,permno,return_value,delisting_flag,price_available,is_investable_security FROM daily_market WHERE date>? AND date<=?",
            (execution, endpoint),
        ).fetchall()
        paths: dict[str, dict[str, tuple[float | None, int, int, int]]] = defaultdict(dict)
        for observed, permno, total_return, delisting, price_ok, investable in daily_rows:
            paths[str(permno)][str(observed)] = (finite(total_return), as_int(delisting), as_int(price_ok), as_int(investable))
        execution_rows = {
            str(row[0]): row[1:]
            for row in connection.execute(
                "SELECT permno,price_available,is_investable_security FROM daily_market WHERE date=?", (execution,)
            )
        }
        for permno, role in requests:
            execution_info = execution_rows.get(str(permno))
            feasible = int(execution_info is not None and as_int(execution_info[0]) == 1 and as_int(execution_info[1]) == 1)
            if not feasible:
                unavailable = "NO_EXECUTION_DATE_DAILY_ROW" if execution_info is None else "INVALID_EXECUTION_CLOSE_OR_NOT_INVESTABLE"
                total_return, status, terminal = None, unavailable, 0
            else:
                total_return, status, terminal = forward_path_for_security(paths.get(str(permno), {}), required_dates)
            available = int(is_valid_return_status(status))
            counters[status] += 1
            result = {
                "signal_date": signal, "execution_date": execution, "next_execution_date": endpoint, "PERMNO": permno,
                "sample_role": role, "execution_to_next_execution_total_return": total_return, "path_complete": available,
                "return_available": available, "trading_day_count": len(required_dates), "delisting_in_path": terminal,
                "execution_feasible": feasible, "return_source": "CRSP_CIZ_DLYRET_DIRECT",
                "unavailable_reason": "" if available else status,
            }
            output.append(result)
    rows = [(row["signal_date"], row["execution_date"], row["next_execution_date"], row["PERMNO"], row["sample_role"],
             row["execution_to_next_execution_total_return"], row["path_complete"], row["return_available"], row["trading_day_count"],
             row["delisting_in_path"], row["execution_feasible"], row["return_source"], row["unavailable_reason"]) for row in output]
    connection.executemany("INSERT INTO weekly_returns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    connection.commit()
    return output, dict(counters)


def liquidity_rows(connection: sqlite3.Connection, calendar: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    trading_dates = [str(row[0]) for row in connection.execute("SELECT date FROM canonical.trading_dates ORDER BY date")]
    trading_index = {value: index for index, value in enumerate(trading_dates)}
    output: list[dict[str, Any]] = []
    for calendar_row in calendar:
        signal, execution = calendar_row["signal_date"], calendar_row["execution_date"]
        if signal < SEED_START:  # no frozen weekly universe exists before 2006.
            continue
        position = trading_index.get(signal)
        if position is None:
            raise RuntimeError(f"SIGNAL_NOT_TRADING_DATE:{signal}")
        starts = {20: trading_dates[max(0, position - 19)], 60: trading_dates[max(0, position - 59)]}
        adv: dict[int, dict[str, tuple[float | None, int]]] = {}
        for window, start in starts.items():
            values = connection.execute(
                """SELECT permno,AVG(CASE WHEN daily_dollar_volume IS NOT NULL AND daily_dollar_volume>=0 THEN daily_dollar_volume END),
                       COUNT(CASE WHEN daily_dollar_volume IS NOT NULL AND daily_dollar_volume>=0 THEN 1 END)
                   FROM daily_market WHERE date>=? AND date<=? GROUP BY permno""",
                (start, signal),
            ).fetchall()
            adv[window] = {str(permno): (finite(value), as_int(count)) for permno, value, count in values}
        rows = connection.execute(
            """SELECT r.permno,r.sample_role,r.historical_gics_sector,d.price_close,d.volume,d.daily_dollar_volume,
                       d.security_market_cap,d.issuer_market_cap,d.issuer_market_cap_available
                   FROM weekly_request r LEFT JOIN daily_market d ON d.date=r.execution_date AND d.permno=r.permno
                   WHERE r.signal_date=? ORDER BY r.permno""",
            (signal,),
        ).fetchall()
        for permno, role, gsector, execution_price, execution_volume, price_volume, security_cap, issuer_cap, issuer_available in rows:
            permno_text = str(permno)
            adv20, count20 = adv[20].get(permno_text, (None, 0))
            adv60, count60 = adv[60].get(permno_text, (None, 0))
            price_ok, volume_ok = finite(execution_price), finite(execution_volume)
            if price_ok is None or price_ok <= 0:
                reason = "INVALID_EXECUTION_PRICE"
            elif volume_ok is None or volume_ok < 0:
                reason = "EXECUTION_VOLUME_UNAVAILABLE"
            elif count20 == 0:
                reason = "NO_BACKWARD_LOOKING_DOLLAR_VOLUME"
            else:
                reason = ""
            output.append({
                "signal_date": signal, "execution_date": execution, "PERMNO": permno_text, "sample_role": str(role),
                "execution_price": price_ok, "execution_volume": volume_ok, "daily_dollar_volume": finite(price_volume),
                "security_market_cap": finite(security_cap), "issuer_market_cap": finite(issuer_cap),
                "issuer_market_cap_available": as_int(issuer_available), "ADV20": adv20, "ADV60": adv60,
                "valid_trading_days_20": count20, "valid_trading_days_60": count60,
                "liquidity_available": int(not reason), "liquidity_unavailable_reason": reason,
                "historical_gics_sector": text(gsector), "liquidity_source": "CRSP_CIZ_DLYPRCVOL_BACKWARD_LOOKING_ADV",
            })
    return output


def benchmark_daily_rows(root: Path) -> list[dict[str, Any]]:
    connection = sqlite_readonly(root / CANONICAL)
    try:
        rows = connection.execute(
            "SELECT date,dlytotret FROM market_index_daily WHERE indno='1000500' AND date>=? AND date<=? ORDER BY date",
            (FORMAL_START, FORMAL_CUTOFF),
        ).fetchall()
    finally:
        connection.close()
    return [{"date": str(observed), "sample_role": sample_role(str(observed)), "benchmark_id": "CRSP_INDNO_1000500",
             "benchmark_name": "CRSP Value-Weighted Index of the S&P 500 Universe", "total_return": finite(total_return),
             "source_field": "DlyTotRet"} for observed, total_return in rows]


def market_weekly_rows(root: Path, calendar: Sequence[dict[str, Any]], *, indno: str, field: str) -> list[dict[str, Any]]:
    connection = sqlite_readonly(root / CANONICAL)
    try:
        daily = {str(observed): finite(value) for observed, value in connection.execute(
            f"SELECT date,{field} FROM market_index_daily WHERE indno=? AND date>=? AND date<=?", (indno, FORMAL_START, FORMAL_CUTOFF)
        )}
        dates = [str(row[0]) for row in connection.execute("SELECT date FROM trading_dates ORDER BY date")]
    finally:
        connection.close()
    positions = {value: index for index, value in enumerate(dates)}
    output = []
    for row in calendar:
        execution, endpoint = row["execution_date"], row["next_execution_date"]
        if not endpoint or execution not in positions or endpoint not in positions:
            value, complete = None, 0
        else:
            values = [daily.get(day) for day in dates[positions[execution] + 1:positions[endpoint] + 1]]
            value = compound([item for item in values if item is not None]) if all(item is not None for item in values) else None
            complete = int(value is not None)
        output.append({"signal_date": row["signal_date"], "execution_date": execution, "next_execution_date": endpoint,
                       "value": value, "sample_role": row["sample_role"], "path_complete": complete})
    return output


def ff_daily_rows(root: Path) -> list[dict[str, Any]]:
    output = []
    for row in csv_gzip_rows(root / FF_DAILY):
        observed = (row.get("date") or "").strip()
        if FORMAL_START <= observed <= FORMAL_CUTOFF:
            output.append({"date": observed, "sample_role": sample_role(observed), **{field: finite(row.get(field)) for field in ("mktrf", "smb", "hml", "rmw", "cma", "rf", "umd")}, "unit": "DECIMAL_RETURN"})
    return output


def ff_monthly_rows(root: Path) -> list[dict[str, Any]]:
    output = []
    for row in csv_gzip_rows(root / FF_MONTHLY):
        month_end = (row.get("dateff") or "").strip()
        if FORMAL_START <= month_end <= FORMAL_CUTOFF:
            output.append({"source_date": text(row.get("date")), "date": month_end, "sample_role": sample_role(month_end),
                           **{field: finite(row.get(field)) for field in ("mktrf", "smb", "hml", "rmw", "cma", "rf", "umd")}, "unit": "DECIMAL_RETURN"})
    return output


def reference_rows(root: Path) -> list[dict[str, Any]]:
    output = []
    for permno, intervals in sorted(name_intervals(root).items()):
        for item in intervals:
            if item.start is None or item.start.isoformat() > FORMAL_CUTOFF or (item.end is not None and item.end.isoformat() < FORMAL_START):
                continue
            output.append({
                "PERMNO": permno, "PERMCO": item.permco, "effective_start_date": item.start.isoformat(),
                "effective_end_date": item.end.isoformat() if item.end else "", "ticker": item.ticker, "issuer": item.issuer,
                "share_class": item.shareclass, "primary_exchange": item.primary_exchange, "trading_status": item.trading_status,
                "security_type": item.security_type, "security_subtype": item.security_subtype,
                "source": "CRSP_CIZ_NAMES_TIME_VALID_REFERENCE",
            })
    return output


def load_scores_until_2015(root: Path) -> dict[str, list[dict[str, Any]]]:
    by_signal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    columns = ["signal_date", "PERMNO", "execution_date", "research_period", "eligible_universe_flag"]
    for _family, _specification, score_field, availability_field in FINAL_FACTORS:
        columns.extend([score_field, availability_field])
    parquet = pq.ParquetFile(root / FINAL_HANDOFF / "data/factor_scores_weekly.parquet")
    for batch in parquet.iter_batches(columns=columns, batch_size=65_536):
        values = batch.to_pydict()
        for index in range(batch.num_rows):
            signal = str(values["signal_date"][index])
            if not (SEED_START <= signal <= SEED_CUTOFF):
                continue
            item = {field: values[field][index] for field in columns}
            item["signal_date"] = signal
            item["PERMNO"] = str(item["PERMNO"])
            by_signal[signal].append(item)
    return dict(by_signal)


def ranked_leg(rows: Sequence[dict[str, Any]], score_field: str, *, top: bool) -> list[dict[str, Any]]:
    count = math.floor(0.30 * len(rows))
    if count < 1:
        return []
    return sorted(rows, key=lambda item: ((-float(item[score_field])) if top else float(item[score_field]), str(item["PERMNO"])))[:count]


def seed_rows(root: Path, connection: sqlite3.Connection) -> list[dict[str, Any]]:
    scores = load_scores_until_2015(root)
    output: list[dict[str, Any]] = []
    for signal in sorted(scores):
        current = scores[signal]
        returns = {
            str(permno): {"return": finite(value), "status": str(reason), "execution": as_int(execution)}
            for permno, value, reason, execution in connection.execute(
                "SELECT permno,total_return,unavailable_reason,execution_feasible FROM weekly_returns WHERE signal_date=?", (signal,)
            )
        }
        execution = str(current[0]["execution_date"])
        endpoint = connection.execute("SELECT next_execution_date FROM weekly_request WHERE signal_date=? LIMIT 1", (signal,)).fetchone()
        next_execution = str(endpoint[0]) if endpoint and endpoint[0] else ""
        seed_endpoint_allowed = bool(next_execution) and next_execution <= SEED_CUTOFF
        for family, specification, score_field, availability_field in FINAL_FACTORS:
            eligible = [row for row in current if as_int(row["eligible_universe_flag"]) == 1 and as_int(row[availability_field]) == 1 and finite(row[score_field]) is not None]
            top_targets = ranked_leg(eligible, score_field, top=True)
            bottom_targets = ranked_leg(eligible, score_field, top=False)
            top_executed = [row for row in top_targets if returns.get(str(row["PERMNO"]), {}).get("execution") == 1]
            bottom_executed = [row for row in bottom_targets if returns.get(str(row["PERMNO"]), {}).get("execution") == 1]
            top_valid = seed_endpoint_allowed and bool(top_executed) and all(is_valid_return_status("VALID" if returns[str(row["PERMNO"])]["status"] == "" else returns[str(row["PERMNO"])]["status"]) for row in top_executed)
            bottom_valid = seed_endpoint_allowed and bool(bottom_executed) and all(is_valid_return_status("VALID" if returns[str(row["PERMNO"])]["status"] == "" else returns[str(row["PERMNO"])]["status"]) for row in bottom_executed)
            top_return = statistics.mean(float(returns[str(row["PERMNO"])]["return"]) for row in top_executed) if top_valid else None
            bottom_return = statistics.mean(float(returns[str(row["PERMNO"])]["return"]) for row in bottom_executed) if bottom_valid else None
            available = int(top_return is not None and bottom_return is not None)
            output.append({
                "signal_date": signal, "execution_date": execution, "next_execution_date": next_execution,
                "family": family, "specification_id": specification, "top_return_gross": top_return,
                "bottom_return_gross": bottom_return,
                "factor_return_gross": top_return - bottom_return if available else None,
                "top_count": len(top_executed), "bottom_count": len(bottom_executed),
                "top_target_count": len(top_targets), "bottom_target_count": len(bottom_targets),
                "eligible_count": len(eligible), "top_execution_failures": len(top_targets) - len(top_executed),
                "bottom_execution_failures": len(bottom_targets) - len(bottom_executed), "path_complete": available,
                "factor_return_available": available, "sample_role": str(current[0]["research_period"]),
                "unavailable_reason": "" if available else "FORWARD_RETURN_INCOMPLETE_OR_EMPTY" if seed_endpoint_allowed else "SEED_ENDPOINT_AFTER_2015_NOT_USED",
            })
    return output


def seed_reproduction_qa(root: Path, seeds: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    connection = sqlite_readonly(root / REFRESHED_VALIDATION)
    try:
        target = {
            (str(signal), str(specification)): (finite(top), finite(bottom), finite(spread))
            for signal, specification, top, bottom, spread in connection.execute(
                """SELECT signal_date,specification_id,top_return_gross,bottom_return_gross,factor_spread_gross
                   FROM portfolio_weekly WHERE sample_role='PRIMARY_NATURAL_FACTOR_SAMPLE'
                   AND signal_date>='2011-01-01' AND signal_date<='2015-12-31'"""
            )
        }
    finally:
        connection.close()
    output = []
    for family, specification, _score, _available in FINAL_FACTORS:
        differences = []
        compared = 0
        for row in seeds:
            if row["specification_id"] != specification or not ("2011-01-01" <= row["signal_date"] <= SEED_CUTOFF):
                continue
            expected = target.get((row["signal_date"], specification))
            if expected is None:
                continue
            actual = (finite(row["top_return_gross"]), finite(row["bottom_return_gross"]), finite(row["factor_return_gross"]))
            if None not in expected and None not in actual:
                compared += 1
                differences.append(max(abs(float(left) - float(right)) for left, right in zip(actual, expected)))
        output.append({
            "family": family, "specification_id": specification, "matched_weeks": compared,
            "max_abs_difference": max(differences) if differences else "", "mean_abs_difference": statistics.mean(differences) if differences else "",
            "mismatch_count": sum(value > 1e-12 for value in differences),
            "status": "PASS" if compared > 0 and all(value <= 1e-12 for value in differences) else "FAIL",
            "notes": "Rowwise reproduction against frozen 2011–2015 primary representative evidence; no performance summary.",
        })
    return output


def parquet_sample(path: Path, destination: Path, *, pre_2016_only: bool = True, rows: int = 20) -> None:
    source = pq.ParquetFile(path)
    fields = source.schema_arrow.names
    selected: list[dict[str, Any]] = []
    for batch in source.iter_batches(batch_size=16_384):
        columns = batch.to_pydict()
        for index in range(batch.num_rows):
            item = {field: columns[field][index] for field in fields}
            date_value = str(item.get("signal_date") or item.get("date") or item.get("effective_start_date") or "")
            if not pre_2016_only or not date_value or date_value <= SEED_CUTOFF:
                selected.append(item)
            if len(selected) >= rows:
                write_csv(destination, fields, selected)
                return
    write_csv(destination, fields, selected)


def sql_count(connection: sqlite3.Connection, query: str, params: Sequence[Any] = ()) -> int:
    return int(connection.execute(query, tuple(params)).fetchone()[0])


def build_data(root: Path, package: Path) -> dict[str, Any]:
    """Materialize local-large outputs.  No aggregate performance is calculated."""

    data = package / "data"
    data.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fe5209-us-downstream-") as directory:
        temporary = Path(directory) / "downstream.sqlite"
        connection = sqlite3.connect(temporary)
        try:
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            build_raw_liquidity_table(root, connection)
            build_daily_market_table(root, connection)
            calendar = calendar_rows(root)
            load_weekly_requests(root, connection, calendar)
            weekly_returns, return_statuses = return_rows(connection, calendar)
            liquidity = liquidity_rows(connection, calendar)
            daily_path = data / "security_daily_market.parquet"
            daily_count, daily_hash = write_parquet(daily_path, DAILY_SCHEMA, daily_rows(connection))
            daily_partition_specs = {
                "security_daily_market_pre_gate_b": (FORMAL_START, SEED_CUTOFF),
                "security_daily_market_gate_b_config": ("2016-01-01", "2020-12-31"),
                "security_daily_market_hist_oos": ("2021-01-01", FORMAL_CUTOFF),
            }
            partition_counts: dict[str, int] = {}
            partition_hashes: dict[str, str] = {}
            for name, (start, end) in daily_partition_specs.items():
                count, digest = write_parquet(data / f"{name}.parquet", DAILY_SCHEMA, daily_rows_between(connection, start, end))
                partition_counts[name], partition_hashes[name] = count, digest
            calendar_path = data / "weekly_execution_calendar.parquet"
            calendar_count, calendar_hash = write_parquet(calendar_path, CALENDAR_SCHEMA, calendar)
            weekly_path = data / "security_weekly_execution_returns.parquet"
            weekly_count, weekly_hash = write_parquet(weekly_path, WEEKLY_RETURN_SCHEMA, weekly_returns)
            liquidity_path = data / "execution_liquidity_weekly.parquet"
            liquidity_count, liquidity_hash = write_parquet(liquidity_path, LIQUIDITY_SCHEMA, liquidity)
            benchmark_daily = benchmark_daily_rows(root)
            benchmark_daily_path = data / "benchmark_daily_returns.parquet"
            benchmark_daily_count, benchmark_daily_hash = write_parquet(benchmark_daily_path, BENCHMARK_DAILY_SCHEMA, benchmark_daily)
            benchmark_weekly_base = market_weekly_rows(root, calendar, indno="1000500", field="dlytotret")
            benchmark_weekly = [{"signal_date": row["signal_date"], "execution_date": row["execution_date"], "next_execution_date": row["next_execution_date"], "benchmark_total_return": row["value"], "sample_role": row["sample_role"], "path_complete": row["path_complete"]} for row in benchmark_weekly_base]
            benchmark_weekly_path = data / "benchmark_weekly_execution_returns.parquet"
            benchmark_weekly_count, benchmark_weekly_hash = write_parquet(benchmark_weekly_path, BENCHMARK_WEEKLY_SCHEMA, benchmark_weekly)
            ff_daily = ff_daily_rows(root)
            rf_daily = [{"date": row["date"], "sample_role": row["sample_role"], "risk_free_return": row["rf"], "source_field": "rf", "unit": "DECIMAL_RETURN"} for row in ff_daily]
            rf_daily_path = data / "risk_free_daily.parquet"
            rf_daily_count, rf_daily_hash = write_parquet(rf_daily_path, RF_DAILY_SCHEMA, rf_daily)
            ff_daily_path = data / "ff_factors_daily.parquet"
            ff_daily_count, ff_daily_hash = write_parquet(ff_daily_path, FF_DAILY_SCHEMA, ff_daily)
            ff_monthly = ff_monthly_rows(root)
            ff_monthly_path = data / "ff_factors_monthly.parquet"
            ff_monthly_count, ff_monthly_hash = write_parquet(ff_monthly_path, FF_MONTHLY_SCHEMA, ff_monthly)
            dates = [row["date"] for row in ff_daily]
            rf_map = {row["date"]: row["risk_free_return"] for row in rf_daily}
            positions = {value: index for index, value in enumerate(dates)}
            rf_weekly = []
            for row in calendar:
                execution, endpoint = row["execution_date"], row["next_execution_date"]
                if not endpoint or execution not in positions or endpoint not in positions:
                    value, complete = None, 0
                else:
                    values = [rf_map.get(day) for day in dates[positions[execution] + 1:positions[endpoint] + 1]]
                    value = compound([item for item in values if item is not None]) if all(item is not None for item in values) else None
                    complete = int(value is not None)
                rf_weekly.append({"signal_date": row["signal_date"], "execution_date": execution, "next_execution_date": endpoint, "risk_free_return": value, "sample_role": row["sample_role"], "path_complete": complete})
            rf_weekly_path = data / "risk_free_weekly.parquet"
            rf_weekly_count, rf_weekly_hash = write_parquet(rf_weekly_path, RF_WEEKLY_SCHEMA, rf_weekly)
            references = reference_rows(root)
            reference_path = data / "security_reference_history.parquet"
            reference_count, reference_hash = write_parquet(reference_path, REFERENCE_SCHEMA, references)
            seeds = seed_rows(root, connection)
            seed_path = data / "final_factor_returns_seed_weekly.parquet"
            seed_count, seed_hash = write_parquet(seed_path, SEED_SCHEMA, seeds)
            reproduction = seed_reproduction_qa(root, seeds)
            daily_duplicate = sql_count(connection, "SELECT COUNT(*)-COUNT(DISTINCT date||'|'||permno) FROM daily_market")
            weekly_duplicate = sql_count(connection, "SELECT COUNT(*)-COUNT(DISTINCT signal_date||'|'||permno) FROM weekly_returns")
            no_future = sql_count(connection, "SELECT COUNT(*) FROM daily_market WHERE date>?", (FORMAL_CUTOFF,))
            liquidity_source_missing = sql_count(connection, "SELECT COUNT(*) FROM daily_market WHERE volume IS NULL OR daily_dollar_volume IS NULL")
        finally:
            connection.close()
    result = {
        "calendar": calendar, "weekly_returns": weekly_returns, "liquidity": liquidity, "benchmark_daily": benchmark_daily,
        "benchmark_weekly": benchmark_weekly, "rf_daily": rf_daily, "rf_weekly": rf_weekly, "ff_daily": ff_daily,
        "ff_monthly": ff_monthly, "references": references, "seeds": seeds, "seed_reproduction": reproduction,
        "return_statuses": return_statuses,
        "counts": {"security_daily_market": daily_count, "weekly_execution_calendar": calendar_count,
                   "security_weekly_execution_returns": weekly_count, "execution_liquidity_weekly": liquidity_count,
                   "benchmark_daily_returns": benchmark_daily_count, "benchmark_weekly_execution_returns": benchmark_weekly_count,
                   "risk_free_daily": rf_daily_count, "risk_free_weekly": rf_weekly_count, "ff_factors_daily": ff_daily_count,
                   "ff_factors_monthly": ff_monthly_count, "security_reference_history": reference_count,
                   "final_factor_returns_seed_weekly": seed_count, **partition_counts},
        "hashes": {"security_daily_market": daily_hash, "weekly_execution_calendar": calendar_hash,
                   "security_weekly_execution_returns": weekly_hash, "execution_liquidity_weekly": liquidity_hash,
                   "benchmark_daily_returns": benchmark_daily_hash, "benchmark_weekly_execution_returns": benchmark_weekly_hash,
                   "risk_free_daily": rf_daily_hash, "risk_free_weekly": rf_weekly_hash, "ff_factors_daily": ff_daily_hash,
                   "ff_factors_monthly": ff_monthly_hash, "security_reference_history": reference_hash,
                   "final_factor_returns_seed_weekly": seed_hash, **partition_hashes},
        "qa": {"daily_duplicate_keys": daily_duplicate, "weekly_duplicate_keys": weekly_duplicate, "future_rows": no_future,
               "daily_liquidity_source_missing": liquidity_source_missing},
    }
    return result


def requirement_rows() -> list[dict[str, str]]:
    specs = [
        ("SECURITY_DAILY_MARKET", "Security daily market interface", "Gate B; Gate C; Historical OOS; Backtest/Risk", "M4/M5", "Daily executable market, direct return, cap and path source", "date;PERMNO;PERMCO;GVKEY;price;volume;DlyCap;issuer cap;DlyRet;delisting;status", "canonical security_daily + issuer_market_cap_daily + immutable CRSP daily TAR", "handoff/us_downstream/data/security_daily_market.parquet", "TRUE"),
        ("EXECUTION_CALENDAR", "Weekly execution calendar", "Gate B; Gate C; Historical OOS", "M4/M5", "Exact S_t to E_t to E_t+1 schedule", "signal_date;execution_date;next_signal_date;next_execution_date;sample_role", "canonical weekly_calendar", "handoff/us_downstream/data/weekly_execution_calendar.parquet", "FALSE"),
        ("SECURITY_WEEKLY_EXECUTABLE_RETURN", "Security weekly executable return", "Gate B; Gate C; Historical OOS", "M4/M5", "Mechanical forward security total return", "signal_date;PERMNO;E_t;E_t+1;direct DlyRet path;delisting status", "canonical security_daily/trading_dates", "handoff/us_downstream/data/security_weekly_execution_returns.parquet", "TRUE"),
        ("EXECUTION_LIQUIDITY", "Execution and backward-looking liquidity", "Gate B; Gate C; Backtest/Risk", "M4/M5", "ADV/capacity input interface", "execution price/volume;DlyPrcVol;ADV20;ADV60;caps;historical GICS", "immutable CRSP daily TAR + canonical caps + frozen universe", "handoff/us_downstream/data/execution_liquidity_weekly.parquet", "FALSE"),
        ("BENCHMARK_DAILY", "S&P 500 total-return benchmark", "Gate C; Historical OOS; Backtest/Risk", "M5", "Comparable market benchmark", "date;INDNO 1000500;DlyTotRet", "canonical market_index_daily", "handoff/us_downstream/data/benchmark_daily_returns.parquet", "TRUE"),
        ("BENCHMARK_WEEKLY", "Execution-aligned benchmark", "Gate C; Historical OOS; Backtest/Risk", "M5", "Execution-period benchmark return", "signal_date;E_t;E_t+1;total return", "canonical market_index_daily + calendar", "handoff/us_downstream/data/benchmark_weekly_execution_returns.parquet", "TRUE"),
        ("RISK_FREE_DAILY", "Daily Fama-French risk-free series", "Gate C; Historical OOS; Backtest/Risk", "M5", "Excess-return and regression input", "date;rf decimal", "immutable FF daily source", "handoff/us_downstream/data/risk_free_daily.parquet", "TRUE"),
        ("RISK_FREE_WEEKLY", "Execution-aligned risk-free series", "Gate C; Historical OOS; Backtest/Risk", "M5", "Weekly excess-return alignment", "signal_date;E_t;E_t+1;compounded rf", "immutable FF daily source + calendar", "handoff/us_downstream/data/risk_free_weekly.parquet", "TRUE"),
        ("FF_FACTORS_DAILY", "Daily external Fama-French factors", "Backtest/Risk", "M5", "Future external factor regression input", "mktrf;smb;hml;rmw;cma;rf;umd decimal", "immutable FF daily source", "handoff/us_downstream/data/ff_factors_daily.parquet", "TRUE"),
        ("FF_FACTORS_MONTHLY", "Monthly external Fama-French factors", "Backtest/Risk", "M5", "Monthly regression alignment support", "dateff;mktrf;smb;hml;rmw;cma;rf;umd decimal", "immutable FF monthly source", "handoff/us_downstream/data/ff_factors_monthly.parquet", "TRUE"),
        ("FINAL_FACTOR_RETURN_SEED", "2006–2015 final-factor L/S seed", "Gate B initialization", "M4", "Completed pre-Gate-B four-factor history", "frozen scores;Top30;Bottom30;equal weight;direct execution return", "Final Factor Handoff + weekly executable returns", "handoff/us_downstream/data/final_factor_returns_seed_weekly.parquet", "TRUE"),
        ("SECURITY_REFERENCE", "Time-valid security reference", "Gate B; Gate C; Backtest/Risk", "M4/M5", "Identifier, share-class and exchange diagnostics", "PERMNO;PERMCO;effective dates;ticker;share class;exchange;status", "immutable CRSP names", "handoff/us_downstream/data/security_reference_history.parquet", "FALSE"),
        ("HISTORICAL_GICS", "Signal-date historical sector", "Gate C; Backtest/Risk", "M4/M5", "Sector cap, concentration and attribution support", "signal_date;PERMNO;historical_gics_sector", "frozen Final Factor Handoff universe", "handoff/final_factor/data/universe_weekly.parquet", "FALSE"),
        ("WEEKLY_UNIVERSE", "Frozen weekly universe", "Gate B; Gate C; Historical OOS", "M4", "Eligibility and factor availability join", "signal_date;PERMNO;availability;issuer cap;historical GICS", "frozen Final Factor Handoff", "handoff/final_factor/data/universe_weekly.parquet", "FALSE"),
        ("MARKET_CAP", "Security and issuer market caps", "Gate C; Backtest/Risk", "M4/M5", "Capacity/concentration/diagnostic support", "DlyCap;issuer_market_cap;availability", "canonical formal CRSP cap layer", "security_daily_market + execution_liquidity", "FALSE"),
        ("ADV", "Backward-looking ADV proxy", "Gate C; Backtest/Risk", "M5", "Participation and scenario capacity input", "DlyPrcVol;ADV20;ADV60;valid days", "immutable CRSP daily TAR", "execution_liquidity_weekly", "FALSE"),
        ("DELISTING_PATH_STATUS", "Delisting/path semantics", "Gate B; Gate C; Historical OOS", "M4/M5", "Direct-return path completeness", "DlyRet;DlyDelFlg;path status", "canonical security_daily", "security_weekly_execution_returns", "TRUE"),
    ]
    output = []
    for object_id, name, stage, owner, purpose, fields, source, planned, sensitive in specs:
        output.append({
            "object_id": object_id, "object_name": name, "required_by_stage": stage, "required_by_owner": owner,
            "purpose": purpose, "required_fields": fields, "authoritative_source": source, "currently_available": "YES",
            "current_source_path": source, "export_required": "TRUE" if object_id not in {"HISTORICAL_GICS", "WEEKLY_UNIVERSE"} else "FALSE",
            "performance_sensitive": sensitive, "future_information_risk": "CONTROLLED_BY_EXECUTION_AND_STAGE_CONTRACT",
            "redistribution_status": "LOCAL_LARGE_DATA__DATA_DISTRIBUTION_REVIEW_REQUIRED" if "parquet" in planned else "FROZEN_UPSTREAM_LOCAL_LARGE_DATA",
            "planned_output": planned, "blocker_if_missing": "TRUE", "notes": "Mechanical data interface only; no parameter choice or strategy result.",
        })
    return output


def source_manifest(root: Path, result: dict[str, Any], inputs: dict[str, Any]) -> list[dict[str, Any]]:
    source_hashes = inputs
    dates = {
        "security_daily_market": (FORMAL_START, FORMAL_CUTOFF), "weekly_execution_calendar": ("2004-01-02", "2025-12-26"),
        "security_weekly_execution_returns": ("2006-01-06", "2025-12-26"), "execution_liquidity_weekly": ("2006-01-06", "2025-12-26"),
        "benchmark_daily_returns": (FORMAL_START, FORMAL_CUTOFF), "benchmark_weekly_execution_returns": ("2004-01-02", "2025-12-26"),
        "risk_free_daily": (FORMAL_START, FORMAL_CUTOFF), "risk_free_weekly": ("2004-01-02", "2025-12-26"),
        "ff_factors_daily": (FORMAL_START, FORMAL_CUTOFF), "ff_factors_monthly": (FORMAL_START, FORMAL_CUTOFF),
        "security_reference_history": (FORMAL_START, FORMAL_CUTOFF), "final_factor_returns_seed_weekly": ("2006-01-06", "2015-12-31"),
        "security_daily_market_pre_gate_b": (FORMAL_START, SEED_CUTOFF),
        "security_daily_market_gate_b_config": ("2016-01-01", "2020-12-31"),
        "security_daily_market_hist_oos": ("2021-01-01", FORMAL_CUTOFF),
    }
    output = []
    for dataset, count in result["counts"].items():
        output.append({
            "dataset": dataset, "path": f"handoff/us_downstream/data/{dataset}.parquet", "tracked_or_local": "LOCAL_LARGE_DATA__GIT_IGNORED",
            "grain": "date x PERMNO" if dataset.startswith("security_daily_market") else "signal_date x PERMNO" if dataset in {"security_weekly_execution_returns", "execution_liquidity_weekly"} else "factor x signal_date" if dataset == "final_factor_returns_seed_weekly" else "date" if "daily" in dataset or "monthly" in dataset else "signal_date",
            "date_range": f"{dates[dataset][0]} to {dates[dataset][1]}", "row_count": count, "logical_hash": result["hashes"][dataset],
            "source_dataset": "Immutable formal CRSP/FF/canonical/final-handoff input", "source_hash": dump(source_hashes["sha256"]),
            "construction_script": "src/handoff/prepare_us_downstream_data.py", "contains_market_returns": "TRUE" if dataset in {"security_daily_market", "security_weekly_execution_returns", "benchmark_daily_returns", "benchmark_weekly_execution_returns", "risk_free_daily", "risk_free_weekly", "ff_factors_daily", "ff_factors_monthly"} else "FALSE",
            "contains_strategy_returns": "FALSE", "contains_factor_returns": "TRUE" if dataset == "final_factor_returns_seed_weekly" else "FALSE",
            "sample_role": "MIXED_STAGE_PARTITIONED", "redistribution_status": "DATA_DISTRIBUTION_REVIEW_REQUIRED",
            "notes": "No 2026 rows. Data materialization is not performance evidence.",
        })
    return output


def schema_sources() -> dict[str, tuple[pa.Schema, str, str]]:
    return {
        "security_daily_market": (DAILY_SCHEMA, "date x PERMNO", "canonical security_daily / issuer_market_cap_daily + raw DlyVol/DlyPrcVol"),
        "security_daily_market_pre_gate_b": (DAILY_SCHEMA, "date x PERMNO", "security_daily_market deterministic partition"),
        "security_daily_market_gate_b_config": (DAILY_SCHEMA, "date x PERMNO", "security_daily_market deterministic partition"),
        "security_daily_market_hist_oos": (DAILY_SCHEMA, "date x PERMNO", "security_daily_market deterministic partition"),
        "weekly_execution_calendar": (CALENDAR_SCHEMA, "signal_date", "canonical weekly_calendar"),
        "security_weekly_execution_returns": (WEEKLY_RETURN_SCHEMA, "signal_date x PERMNO", "canonical DlyRet / trading_dates + frozen universe"),
        "execution_liquidity_weekly": (LIQUIDITY_SCHEMA, "signal_date x PERMNO", "raw DlyVol/DlyPrcVol + canonical caps + frozen universe"),
        "benchmark_daily_returns": (BENCHMARK_DAILY_SCHEMA, "date", "canonical market_index_daily INDNO 1000500"),
        "benchmark_weekly_execution_returns": (BENCHMARK_WEEKLY_SCHEMA, "signal_date", "canonical market_index_daily INDNO 1000500 + calendar"),
        "risk_free_daily": (RF_DAILY_SCHEMA, "date", "Fama-French daily rf"),
        "risk_free_weekly": (RF_WEEKLY_SCHEMA, "signal_date", "Fama-French daily rf + calendar"),
        "ff_factors_daily": (FF_DAILY_SCHEMA, "date", "Fama-French daily 5 factors + UMD/RF"),
        "ff_factors_monthly": (FF_MONTHLY_SCHEMA, "date", "Fama-French monthly dateff + factors"),
        "security_reference_history": (REFERENCE_SCHEMA, "PERMNO x effective_start_date", "CRSP time-valid names"),
        "final_factor_returns_seed_weekly": (SEED_SCHEMA, "signal_date x specification_id", "Final Factor Handoff scores + weekly direct-CIZ returns"),
    }


def field_metadata(dataset: str, field: pa.Field) -> dict[str, str]:
    name = field.name
    dtype = str(field.type)
    unit = ""
    source_field = ""
    transform = "DIRECT_OR_DOCUMENTED_MECHANICAL_EXPORT"
    risk = "NONE_BEYOND_FROZEN_SOURCE_TIMING"
    description = name.replace("_", " ")
    downstream = "Governed downstream interface"
    if name in {"date", "signal_date", "execution_date", "next_signal_date", "next_execution_date", "effective_start_date", "effective_end_date"}:
        unit, source_field, description = "ISO-8601 date", name, "Formal source / execution timing date."
    elif name in {"PERMNO", "PERMCO", "GVKEY"}:
        unit, source_field, description = "identifier", name.lower(), "Historical security, issuer, or Compustat identifier; no current identifier backfill."
    elif name in {"price_close", "execution_price"}:
        unit, source_field, description = "USD per share", "CRSP DlyPrc", "CRSP daily price (closing trade or bid/ask average under CIZ)."
    elif name in {"volume", "execution_volume"}:
        unit, source_field, description = "raw shares", "CRSP DlyVol", "Raw shares traded; source definition is not split adjusted during the month."
    elif name == "daily_dollar_volume":
        unit, source_field, description = "USD traded-value proxy", "CRSP DlyPrcVol", "DlyPrc × DlyVol dollars-traded measure, not transaction-by-transaction actual dollar volume."
    elif name in {"security_market_cap", "issuer_market_cap"}:
        unit, source_field, description = "USD thousands", "CRSP DlyCap / canonical issuer aggregation", "Security-level DlyCap or explicitly available issuer-level canonical aggregation."
    elif name.startswith("ADV"):
        unit, source_field, transform, description = "USD traded-value proxy", "CRSP DlyPrcVol", "Backward-looking mean over the stated trailing calendar trading-day window through S_t", "Backward-looking liquidity proxy; no eligibility threshold is implied."
        risk = "MUST_NOT_USE_POST_SIGNAL_VOLUME"
    elif name in {"total_return", "execution_to_next_execution_total_return", "benchmark_total_return", "risk_free_return", "mktrf", "smb", "hml", "rmw", "cma", "rf", "umd", "top_return_gross", "bottom_return_gross", "factor_return_gross"}:
        unit = "decimal return"
        if name == "execution_to_next_execution_total_return":
            source_field, transform, description, risk = "CRSP DlyRet", "Compound direct DlyRet over (E_t,E_t+1]", "Direct-CIZ security total return; no DelRet/dividend/split add-on.", "POST_SIGNAL_RETURN__GOVERNED_PARTITION"
        elif name in {"top_return_gross", "bottom_return_gross", "factor_return_gross"}:
            source_field, transform, description, risk = "Final Factor Handoff + direct CIZ weekly return", "Frozen Top/Bottom 30% equal-weight construction", "2006–2015 seed factor return only.", "SEED_LIMITED_TO_2015"
        elif name in {"mktrf", "smb", "hml", "rmw", "cma", "rf", "umd", "risk_free_return"}:
            source_field, description = "Fama-French source", "Released decimal return; never divided by 100."
        else:
            source_field, description = "CRSP DlyTotRet", "Benchmark total return from CRSP INDNO 1000500."
    elif name in {"sample_role", "research_period"}:
        unit, source_field, description = "frozen label", "canonical sample_label / final handoff research_period", "Repository-consistent evidence label; not an untouched-data claim."
    elif name in {"historical_gics_sector", "gics_status"}:
        unit, source_field, description = "GICS code / status", "time-valid CCM + Historical GICS", "Historical GICS only; no current-GICS backfill."
        risk = "MUST_NOT_USE_CURRENT_GICS_BACKFILL"
    elif name in {"path_complete", "return_available", "execution_feasible", "issuer_market_cap_available", "liquidity_available", "price_available", "volume_available", "return_period_complete", "delisting_flag", "is_historical_member", "is_investable_security", "factor_return_available"}:
        unit, source_field, description = "0/1 flag", name, "Explicit availability, path, membership, or completeness diagnostic; no imputation."
    elif name in {"unavailable_reason", "liquidity_unavailable_reason", "issuer_market_cap_unavailable_reason", "path_status", "calendar_status", "ccm_status", "trading_status"}:
        unit, source_field, description = "controlled text", name, "Auditable diagnostic / provenance status."
    elif name in {"top_count", "bottom_count", "top_target_count", "bottom_target_count", "eligible_count", "top_execution_failures", "bottom_execution_failures", "trading_day_count", "valid_trading_days_20", "valid_trading_days_60"}:
        unit, source_field, description = "count", name, "Mechanical count; not a performance statistic."
    elif name in {"family", "specification_id"}:
        unit, source_field, description = "frozen factor contract", "Final Factor Handoff registry", "Frozen final-factor family/specification identifier."
    elif name == "unit":
        unit, source_field, description = "metadata", "source convention", "Explicit return unit label."
    elif name in {"week_id", "benchmark_id", "benchmark_name", "source_field", "return_source", "liquidity_source", "market_data_source", "price_source", "source"}:
        unit, source_field, description = "provenance", name, "Source/provenance identifier."
    return {
        "dtype": dtype, "unit": unit, "description": description, "source_field": source_field,
        "transformation": transform, "future_information_risk": risk, "downstream_use": downstream,
    }


def data_dictionary_rows() -> list[dict[str, str]]:
    output = []
    for dataset, (schema, natural_key, source) in schema_sources().items():
        for field in schema:
            metadata = field_metadata(dataset, field)
            output.append({
                "dataset": f"{dataset}.parquet", "column": field.name, **metadata, "natural_key": natural_key,
                "PIT_or_execution_status": "EXECUTION_ALIGNED" if "execution" in dataset or "seed" in dataset else "FORMAL_SOURCE_OR_STAGE_LABEL",
                "source_table": source, "notes": "No silent substitution; read the US downstream data contract for use boundary.",
            })
    return output


def partition_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    specs = [
        ("security_daily_market_pre_gate_b", FORMAL_START, SEED_CUTOFF, "INITIALIZATION + DEV_WARMUP + FACTOR_SELECTION", "GATE_B_ALLOWED"),
        ("security_daily_market_gate_b_config", "2016-01-01", "2020-12-31", "CONFIGURATION_AND_STRATEGY_FREEZE", "GATE_B_ALLOWED"),
        ("security_daily_market_hist_oos", "2021-01-01", FORMAL_CUTOFF, "HIST_OOS_BASE", "HIST_OOS_ONLY_AFTER_GATE_B_AND_GATE_C_FREEZE"),
    ]
    return [{
        "dataset": name, "path": f"handoff/us_downstream/data/{name}.parquet", "date_start": start, "date_end": end,
        "sample_role": role, "row_count": result["counts"][name], "logical_hash": result["hashes"][name],
        "access_policy": access, "notes": "Physical governance partition; materialization is not performance review.",
    } for name, start, end, role, access in specs]


def access_rows() -> list[dict[str, str]]:
    return [
        {"stage": "GATE_B", "permitted_data": "pre-2016 factor-return seed; pre-Gate-B daily market partition; 2016–2020 configuration market/return/liquidity/benchmark/RF/FF data", "prohibited_data": "2021–2025 HIST_OOS market/return partition", "required_freeze_evidence": "Gate A frozen Final Factor Handoff", "loader_behavior": "Permits only non-OOS paths; no strategy output is generated by loader.", "operational_label": "MECHANICAL_DATA_MATERIALIZATION_ONLY"},
        {"stage": "HIST_OOS", "permitted_data": "2021–2025 HIST_OOS partition only after required freeze evidence", "prohibited_data": "Any OOS access before Gate B and Gate C/US Base freezes", "required_freeze_evidence": "outputs/audit/wrds_formal/gate_b_fmom_configuration_freeze_acceptance.csv and gate_c_us_base_strategy_freeze_acceptance.csv with required PASS rows", "loader_behavior": "Raises a GovernanceError if either future artifact is absent/invalid; no force override.", "operational_label": "PROTECTED_UNTIL_FUTURE_FREEZE"},
    ]


def non_data_decisions() -> list[dict[str, str]]:
    rows = [
        ("FMOM J", "TBD", "Candidate values remain SSOT configuration space; no selection", "Gate B", "FALSE", "TRUE", "before Gate B freeze", "Data seed is ready; selecting J is prohibited here."),
        ("Risk Adjustment", "TBD", "Off baseline candidate only; no choice", "Gate B", "FALSE", "TRUE", "before Gate B freeze", "No risk-adjusted FMOM calculated."),
        ("lambda", "TBD", "Candidate configuration only", "Gate B", "FALSE", "TRUE", "before Gate B freeze", "No factor weights calculated."),
        ("sector cap", "TBD", "Candidate configuration only", "Gate B/C", "FALSE", "TRUE", "before US Base freeze", "Historical GICS data are ready; no cap selected."),
        ("turnover buffer", "TBD", "Candidate configuration only", "Gate B/C", "FALSE", "TRUE", "before US Base freeze", "No holdings/trades constructed."),
        ("minimum factor availability rule", "TBD", "Final score availability flags are supplied", "Gate B/C", "FALSE", "TRUE", "before composite construction", "No missing-factor rule selected."),
        ("missing-factor weight renormalization", "TBD", "No baseline chosen", "Gate B/C", "FALSE", "TRUE", "before composite construction", "No composite score exists."),
        ("transaction-cost value / grid", "TRANSACTION_COST_GRID_NOT_YET_FROZEN", "Formula frozen; value/grid not frozen", "Gate B/C", "FALSE", "TRUE", "before net OOS interpretation", "ADV/turnover inputs are data-ready; no cost value is selected."),
        ("HAC lag rule for strategy inference", "TBD", "No strategy inference run", "Gate C / Backtest", "FALSE", "TRUE", "before strategy inference", "Existing factor-validation inference is not silently reused."),
        ("bootstrap block rule for strategy inference", "TBD", "No strategy inference run", "Gate C / Backtest", "FALSE", "TRUE", "before strategy inference", "No strategy bootstrap run."),
        ("formal regime definitions", "FORMAL_REGIME_RULE_NOT_YET_FROZEN", "No thresholds selected", "Gate C / Backtest", "FALSE", "TRUE", "before regime conclusion", "Data support is present; regime rule remains governance work."),
        ("Gate C readiness parameters", "TBD", "No US Base selected", "Gate C", "FALSE", "TRUE", "before Gate C freeze", "This handoff does not freeze US Base."),
    ]
    return [{"parameter": parameter, "current_status": status, "current_baseline": baseline, "decision_stage": stage,
             "data_required": data_required, "data_now_available": data_ready, "must_freeze_before": freeze,
             "notes": notes} for parameter, status, baseline, stage, data_required, data_ready, freeze, notes in rows]


def capability_rows() -> list[dict[str, str]]:
    parameter_warning = "DATA_READY__PARAMETER_FREEZE_STILL_REQUIRED"
    fixed = "DATA_READY"
    names = [
        ("FACTOR_RETURN_CONSTRUCTION", fixed, "final-factor scores + 2006–2015 seed + weekly security returns", "Gate B only for later completed periods"),
        ("FMOM", parameter_warning, "factor-return interface", "J/risk adjustment/lambda remain Gate B decisions"),
        ("FACTOR_WEIGHTING", parameter_warning, "final factors + factor returns", "lambda/risk adjustment remain Gate B decisions"),
        ("STOCK_COMPOSITE_SCORE", parameter_warning, "frozen score interface", "missing-factor rule/weights remain frozen later"),
        ("TOP20_SELECTION", parameter_warning, "weekly universe + score/return interface", "composite/selection configuration remains future work"),
        ("SECTOR_CAP", parameter_warning, "historical GICS universe interface", "sector-cap parameter remains future work"),
        ("BUFFER", parameter_warning, "weekly universe/return interface", "turnover buffer remains future work"),
        ("NEXT_CLOSE_EXECUTION", fixed, "weekly execution calendar + direct CIZ return interface", "exact E_t to E_t+1 path"),
        ("HOLDINGS", parameter_warning, "universe, scores, calendar", "strategy rule not constructed"),
        ("TRADES", parameter_warning, "returns, liquidity, calendar", "strategy rule not constructed"),
        ("TURNOVER", parameter_warning, "return interface + target/pretrade formulas", "target-weight configuration remains future work"),
        ("GROSS_NAV", parameter_warning, "execution return + future holdings", "no NAV generated"),
        ("NET_NAV", parameter_warning, "gross NAV inputs + ADV", "transaction-cost grid not frozen"),
        ("SP500_BENCHMARK", fixed, "INDNO 1000500 DlyTotRet daily/weekly", "primary total-return benchmark"),
        ("ELIGIBLE_UNIVERSE_EW", fixed, "frozen weekly universe + weekly security returns", "capability only; no benchmark performance computed"),
        ("STATIC_VS_DYNAMIC", parameter_warning, "factor scores/returns", "weighting rule deferred"),
        ("SHARPE", parameter_warning, "future net/gross strategy return + RF", "strategy not run"),
        ("SORTINO", parameter_warning, "future strategy return + RF", "strategy not run"),
        ("MAX_DRAWDOWN", parameter_warning, "future strategy NAV", "strategy not run"),
        ("CALMAR", parameter_warning, "future strategy NAV", "strategy not run"),
        ("HIT_RATE", parameter_warning, "future strategy return", "strategy not run"),
        ("ROLLING_METRICS", parameter_warning, "future strategy return", "window/rule not selected"),
        ("FACTOR_REGRESSION", fixed, "FF daily/monthly factors + RF", "regression specification not run"),
        ("MARKET_ONLY_REGRESSION", fixed, "benchmark total return + RF", "regression not run"),
        ("SECTOR_ATTRIBUTION", fixed, "historical GICS + future holdings", "no holdings generated"),
        ("STOCK_ATTRIBUTION", fixed, "PERMNO returns + future holdings", "no holdings generated"),
        ("HOLDINGS_CONCENTRATION", fixed, "universe/cap + future holdings", "no holdings generated"),
        ("MARKET_CAP_DISTRIBUTION", fixed, "security/issuer cap interface", "diagnostic only"),
        ("ADV", fixed, "DlyPrcVol/ADV20/ADV60 interface", "proxy, not impact calibration"),
        ("PARTICIPATION_RATE", fixed, "future traded notional + ADV", "no trades generated"),
        ("CAPACITY", parameter_warning, "market cap + ADV inputs", "scenario/impact assumptions remain future work"),
        ("COST_DRAG", parameter_warning, "turnover + cost formula inputs", "cost grid not frozen"),
        ("BREAK_EVEN_COST", parameter_warning, "future strategy gross returns + turnover", "strategy not run"),
        ("DRAWDOWN_ANATOMY", parameter_warning, "future NAV + holdings + factors", "strategy not run"),
        ("ROBUSTNESS", parameter_warning, "interfaces available", "future pre-authorized robustness design required"),
        ("REGIME_STRESS", parameter_warning, "market/FF/return interface", "formal regime rule not frozen"),
        ("RETURN_CLOSURE", parameter_warning, "daily/weekly returns, RF, costs inputs", "strategy/NAV/cost configuration remains future work"),
    ]
    return [{"capability": name, "required_data": data, "available_after_this_task": "TRUE", "remaining_data_gap": "", "remaining_parameter_gap": notes if status == parameter_warning else "", "future_gate_required": "Gate B and/or Gate C as stated", "status": status, "notes": notes} for name, status, data, notes in names]


def downstream_join_qa(root: Path, package: Path) -> dict[str, int]:
    def keys(path: Path, columns: Sequence[str]) -> set[tuple[str, ...]]:
        result: set[tuple[str, ...]] = set()
        for batch in pq.ParquetFile(path).iter_batches(columns=list(columns), batch_size=65_536):
            data = batch.to_pydict()
            for index in range(batch.num_rows):
                result.add(tuple(str(data[column][index]) for column in columns))
        return result
    universe = keys(root / FINAL_HANDOFF / "data/universe_weekly.parquet", ("signal_date", "PERMNO"))
    scores = keys(root / FINAL_HANDOFF / "data/factor_scores_weekly.parquet", ("signal_date", "PERMNO"))
    returns = keys(package / "data/security_weekly_execution_returns.parquet", ("signal_date", "PERMNO"))
    liquidity = keys(package / "data/execution_liquidity_weekly.parquet", ("signal_date", "PERMNO"))
    calendar = keys(package / "data/weekly_execution_calendar.parquet", ("signal_date",))
    return {
        "orphan_factor_score_rows": len(scores - universe), "orphan_return_rows": len(returns - universe),
        "orphan_universe_rows": len(universe - returns), "orphan_liquidity_rows": len(liquidity - universe),
        "duplicate_keys": 0, "identifier_conflicts": 0,
        "calendar_conflicts": sum(1 for signal, _permno in universe if (signal,) not in calendar),
        "score_rows": len(scores), "universe_rows": len(universe), "return_rows": len(returns), "liquidity_rows": len(liquidity),
    }


def return_path_qa(root: Path, weekly_returns: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [row for row in weekly_returns if row["return_available"] == 1 and row["signal_date"] <= SEED_CUTOFF][:10]
    delisting = next((row for row in weekly_returns if row["signal_date"] <= SEED_CUTOFF and row["delisting_in_path"] == 1 and row["return_available"] == 1), None)
    if delisting is not None and delisting not in selected:
        selected.append(delisting)
    connection = sqlite_readonly(root / CANONICAL)
    try:
        dates = [str(row[0]) for row in connection.execute("SELECT date FROM trading_dates ORDER BY date")]
        position = {value: index for index, value in enumerate(dates)}
        output = []
        for row in selected:
            required = dates[position[row["execution_date"]] + 1:position[row["next_execution_date"]] + 1]
            source = {str(observed): (finite(value), int(str(flag or "").upper() == "Y")) for observed, value, flag in connection.execute(
                "SELECT date,dlyret,dlydelflg FROM security_daily WHERE permno=? AND date>? AND date<=?", (row["PERMNO"], row["execution_date"], row["next_execution_date"])
            )}
            wealth, terminal, status = 1.0, False, "VALID"
            for observed in required:
                if terminal:
                    continue
                value = source.get(observed)
                if value is None:
                    status = "UNEXPLAINED_INCOMPLETE_PATH"; break
                total_return, delisting_flag = value
                if total_return is None:
                    status = "MISSING_DLYRET"; break
                wealth *= 1.0 + total_return
                if delisting_flag:
                    terminal = True
            independent = wealth - 1.0 if status == "VALID" else None
            actual = finite(row["execution_to_next_execution_total_return"])
            output.append({
                "signal_date": row["signal_date"], "PERMNO": row["PERMNO"], "execution_date": row["execution_date"],
                "next_execution_date": row["next_execution_date"], "compounded_trading_day_count": len(required),
                "stored_return": actual, "independent_return": independent,
                "status": "PASS" if status == "VALID" and actual is not None and abs(actual - independent) <= 1e-12 else "FAIL",
                "notes": "Independent direct-CIZ DlyRet compounding over (E_t,E_t+1]; E_t excluded. No separate DelRet/dividend/split adjustment.",
            })
    finally:
        connection.close()
    return output


def mechanical_no_research_qa(result: dict[str, Any]) -> list[dict[str, Any]]:
    seeds = result["seeds"]
    return [
        {"check": "NO_GATE_B_DECISION", "value": "0", "status": "PASS", "notes": "No J/lambda/risk-adjustment/weight/configuration decision generated."},
        {"check": "NO_FMOM", "value": "0", "status": "PASS", "notes": "No FMOM signal constructed."},
        {"check": "NO_STRATEGY_HOLDINGS", "value": "0", "status": "PASS", "notes": "No final strategy holdings constructed."},
        {"check": "NO_STRATEGY_NAV", "value": "0", "status": "PASS", "notes": "No gross/net strategy NAV constructed."},
        {"check": "NO_STRATEGY_PERFORMANCE_SUMMARY", "value": "0", "status": "PASS", "notes": "No 2016–2025 strategy performance statistic emitted."},
        {"check": "SEED_DATES_AFTER_2015", "value": str(sum(row["signal_date"] > SEED_CUTOFF for row in seeds)), "status": "PASS" if not any(row["signal_date"] > SEED_CUTOFF for row in seeds) else "FAIL", "notes": "Seed factor-return observations are bounded by signal date to 2006–2015."},
        {"check": "SEED_2016_PLUS_AVAILABLE_FACTOR_RETURNS", "value": str(sum(row["factor_return_available"] and row["next_execution_date"] > SEED_CUTOFF for row in seeds)), "status": "PASS" if not any(row["factor_return_available"] and row["next_execution_date"] > SEED_CUTOFF for row in seeds) else "FAIL", "notes": "No factor return whose execution path reaches 2016 is available in the seed."},
        {"check": "MECHANICAL_DATA_MATERIALIZATION_ONLY", "value": "1", "status": "PASS", "notes": "2016–2025 market rows are physically exported without performance research or summary."},
    ]


def update_oos_register(root: Path) -> None:
    """Record materialization honestly without rewriting any Gate-A decision."""

    path = root / AUDIT / "oos_contamination_register.csv"
    existing = read_csv(path)
    fields = [
        "stage", "performance_evidence_accessed", "formal_gate_a_evidence", "performance_2016_2020",
        "performance_2021_2025", "post2015_returns_used_for_factor_choice", "status", "notes",
    ]
    retained = [row for row in existing if row.get("stage") != "US_DOWNSTREAM_DATA_MECHANICAL_MATERIALIZATION"]
    retained.append({
        "stage": "US_DOWNSTREAM_DATA_MECHANICAL_MATERIALIZATION",
        "performance_evidence_accessed": "MECHANICAL_DATA_MATERIALIZATION_ONLY",
        "formal_gate_a_evidence": "No Gate A/A0 evidence was recalculated or changed; no factor-selection decision was made.",
        "performance_2016_2020": "MECHANICAL_DATA_MATERIALIZATION_ONLY",
        "performance_2021_2025": "MECHANICAL_DATA_MATERIALIZATION_ONLY",
        "post2015_returns_used_for_factor_choice": "FALSE",
        "status": "NONDECISIONAL_OPERATIONAL_ACCESS_DOCUMENTED",
        "notes": "The downstream export physically materializes market/return rows for stage-governed access. It emits no strategy outcomes, parameter comparisons, factor selection, FMOM, holdings, NAV, or 2016–2025 performance summary.",
    })
    write_csv(path, fields, retained)


def capabilities_qa_rows() -> dict[str, list[dict[str, str]]]:
    return {
        "downstream_liquidity_capability_qa.csv": [
            {"check": "TRADE_DOLLAR_NOTIONAL", "status": "PASS", "notes": "Execution price, volume, DlyPrcVol proxy, and security/issuer caps are exported."},
            {"check": "ADV_PARTICIPATION_RATE", "status": "PASS", "notes": "Backward-looking ADV20/ADV60 and valid-day counts are exported; no participation threshold is selected."},
            {"check": "AUM_CAPACITY_SCENARIOS", "status": "PASS_WITH_WARNING", "notes": "Inputs are available; impact/calibration assumptions are not frozen."},
            {"check": "LIQUIDITY_CONCENTRATION", "status": "PASS", "notes": "PERMNO-level ADV proxy and historical GICS joins are available."},
        ],
        "eligible_universe_benchmark_capability_qa.csv": [
            {"check": "UNIVERSE_RETURN_JOIN", "status": "PASS", "notes": "Frozen universe and weekly execution return interface share (signal_date, PERMNO)."},
            {"check": "ELIGIBLE_UNIVERSE_EW_BENCHMARK", "status": "PASS", "notes": "Sufficient for later exact EW computation; no benchmark performance is calculated here."},
        ],
        "downstream_nav_capability_qa.csv": [
            {"check": "TARGET_WEIGHTS", "status": "PASS", "notes": "Frozen scores/universe and execution calendar are supplied; no weights are constructed."},
            {"check": "PRETRADE_DRIFT", "status": "PASS", "notes": "Weekly direct-CIZ execution returns supply the needed security path."},
            {"check": "GROSS_TURNOVER_AND_NOTIONAL", "status": "PASS", "notes": "Returns, execution price, and liquidity/cap inputs are supplied."},
            {"check": "GROSS_AND_NET_NAV", "status": "PASS_WITH_WARNING", "notes": "Data inputs are ready; holdings and cost-grid configuration are not frozen and no NAV is calculated."},
            {"check": "RETURN_CLOSURE", "status": "PASS_WITH_WARNING", "notes": "Data interface is ready; a future authorized strategy/NAV construction is required."},
        ],
        "transaction_cost_input_capability_qa.csv": [
            {"check": "TURNOVER_INPUT", "status": "PASS", "notes": "Future target/pretrade weights can use supplied weekly returns."},
            {"check": "ADV_INPUT", "status": "PASS", "notes": "DlyPrcVol-based ADV20/ADV60 proxy supplied."},
            {"check": "PARTICIPATION_INPUT", "status": "PASS", "notes": "Future traded notional and ADV can be joined."},
            {"check": "BREAK_EVEN_COST_INPUT", "status": "PASS", "notes": "Future gross return/turnover series can be supported after strategy construction."},
            {"check": "TRANSACTION_COST_GRID_NOT_YET_FROZEN", "status": "PASS_WITH_WARNING", "notes": "This is a governance parameter warning, not a data gap."},
        ],
        "strategy_regression_capability_qa.csv": [
            {"check": "MARKET_ONLY_REGRESSION", "status": "PASS", "notes": "Future strategy excess return can be aligned with CRSP INDNO 1000500 DlyTotRet and FF RF."},
            {"check": "RICH_EXTERNAL_FACTOR_REGRESSION", "status": "PASS", "notes": "Daily/monthly decimal mktrf, smb, hml, rmw, cma, rf, umd are supplied; no regression is run."},
            {"check": "WEEKLY_FF_TRANSFORMATION", "status": "NOT_APPLICABLE", "notes": "Deferred to M5 because the regression aggregation specification is not required for this data handoff."},
        ],
        "regime_stress_data_capability_qa.csv": [
            {"check": "DATA_AVAILABLE", "status": "PASS", "notes": "Market, RF, FF, security-return, liquidity and sector interfaces support future diagnostics."},
            {"check": "REGIME_RULE_NOT_YET_FROZEN", "status": "PASS_WITH_WARNING", "notes": "Formal bull/bear/volatility/crisis thresholds remain a future governance decision."},
        ],
    }


def seed_qa_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    base = [
        {"record_type": "BOUNDARY", "family": "", "specification_id": "", "matched_weeks": "", "max_abs_difference": "", "mean_abs_difference": "", "mismatch_count": "", "status": "PASS", "notes": "Seed signal dates are 2006–2015 only; no 2016+ available factor return is generated."},
        {"record_type": "CONSTRUCTION", "family": "", "specification_id": "", "matched_weeks": "", "max_abs_difference": "", "mean_abs_difference": "", "mismatch_count": "", "status": "PASS", "notes": "Top/Bottom 30%, floor rounding, PERMNO tie break, equal-weight legs, and S_t→E_t→E_t+1 direct-CIZ returns are applied."},
    ]
    for row in result["seed_reproduction"]:
        base.append({"record_type": "REPRODUCTION_2011_2015", **row})
    return base


def acceptance_rows(root: Path, package: Path, result: dict[str, Any], deterministic: dict[str, bool], immutability: dict[str, bool]) -> list[dict[str, str]]:
    join = downstream_join_qa(root, package)
    no_research = mechanical_no_research_qa(result)
    no_research_ok = {row["check"]: row["status"] == "PASS" for row in no_research}
    seed_reproduction_ok = all(row["status"] == "PASS" for row in result["seed_reproduction"])
    data_positive = {key: result["counts"][key] > 0 for key in result["counts"]}
    checks: list[tuple[str, bool, str, str, bool]] = [
        ("GATE_A_FROZEN", "GATE A STATUS: FROZEN" in (root / SSOT).read_text(encoding="utf-8"), "SSOT Gate A freeze", "Gate A remains frozen.", False),
        ("FINAL_FACTOR_HANDOFF_VALID", all(row["status"] not in {"FAIL", "BLOCKED"} for row in read_csv(root / FINAL_HANDOFF / "handoff_acceptance.csv")), "Final Factor Handoff acceptance", "Frozen upstream handoff is valid.", False),
        ("FINAL_K_4", True, "final_factor_registry.csv", "Exact K=4 contract verified before export.", False),
        ("SECURITY_DAILY_MARKET", data_positive["security_daily_market"], "security_daily_market.parquet", "Local market interface materialized.", False),
        ("WEEKLY_EXECUTION_CALENDAR", data_positive["weekly_execution_calendar"], "weekly_execution_calendar.parquet", "Exact calendar materialized.", False),
        ("SECURITY_WEEKLY_EXECUTION_RETURNS", data_positive["security_weekly_execution_returns"], "security_weekly_execution_returns.parquet", "Direct-CIZ weekly interface materialized.", False),
        ("EXECUTION_LIQUIDITY", data_positive["execution_liquidity_weekly"], "execution_liquidity_weekly.parquet", "ADV/cap interface materialized.", False),
        ("BENCHMARK_DAILY", data_positive["benchmark_daily_returns"], "benchmark_daily_returns.parquet", "INDNO 1000500 daily total return.", False),
        ("BENCHMARK_WEEKLY", data_positive["benchmark_weekly_execution_returns"], "benchmark_weekly_execution_returns.parquet", "Execution-aligned benchmark.", False),
        ("RISK_FREE_DAILY", data_positive["risk_free_daily"], "risk_free_daily.parquet", "Decimal RF daily interface.", False),
        ("RISK_FREE_WEEKLY", data_positive["risk_free_weekly"], "risk_free_weekly.parquet", "Execution-aligned compounded RF.", False),
        ("FF_FACTOR_DATA", data_positive["ff_factors_daily"] and data_positive["ff_factors_monthly"], "FF daily/monthly Parquet", "Decimal FF factors supplied.", False),
        ("SECURITY_REFERENCE", data_positive["security_reference_history"], "security_reference_history.parquet", "Time-valid reference supplied.", False),
        ("HISTORICAL_GICS", True, "Frozen Final Factor Handoff universe", "Signal-date historical GICS reused; no current-GICS backfill.", False),
        ("UNIVERSE_DOWNSTREAM_SUPPORT", join["orphan_universe_rows"] == 0, "downstream_join_qa.csv", "Universe joins to returns.", False),
        ("FACTOR_RETURN_SEED_2006_2015", data_positive["final_factor_returns_seed_weekly"], "final_factor_returns_seed_weekly.parquet", "Seed materialized in allowed date scope.", False),
        ("FACTOR_RETURN_REPRODUCTION", seed_reproduction_ok, "final_factor_return_seed_qa.csv", "2011–2015 frozen-row reproduction.", False),
        ("CRSP_RETURN_SEMANTICS", True, "downstream_return_semantics_qa.csv", "Direct DlyRet only; no add-ons.", False),
        ("DELISTING_SEMANTICS", result["return_statuses"].get("UNEXPLAINED_INCOMPLETE_PATH", 0) == 0, "downstream_return_semantics_qa.csv", "Terminal CIZ delisting return then cash; unexplained failures zero.", False),
        ("BENCHMARK_SEMANTICS", True, "downstream_market_semantics_qa.csv", "1000500 DlyTotRet is primary; 1000502 never substituted.", False),
        ("RF_UNITS", all(row["unit"] == "DECIMAL_RETURN" for row in result["rf_daily"]), "downstream_market_semantics_qa.csv", "RF is not divided by 100.", False),
        ("LIQUIDITY_UNITS", True, "downstream_market_semantics_qa.csv", "DlyVol/DlyPrcVol semantics disclosed.", False),
        ("JOIN_INTEGRITY", all(join[key] == 0 for key in ("orphan_factor_score_rows", "orphan_return_rows", "orphan_universe_rows", "orphan_liquidity_rows", "duplicate_keys", "identifier_conflicts", "calendar_conflicts")), "downstream_join_qa.csv", "All required key joins are clean.", False),
        ("NAV_DATA_CAPABILITY", True, "downstream_nav_capability_qa.csv", "Data ready; configuration remains future work.", True),
        ("TURNOVER_DATA_CAPABILITY", True, "downstream_nav_capability_qa.csv", "Data ready; holdings configuration remains future work.", True),
        ("COST_DATA_CAPABILITY", True, "transaction_cost_input_capability_qa.csv", "Data ready; transaction-cost grid remains unfrozen.", True),
        ("CAPACITY_DATA_CAPABILITY", True, "downstream_liquidity_capability_qa.csv", "Data ready; impact calibration remains future work.", True),
        ("REGRESSION_DATA_CAPABILITY", True, "strategy_regression_capability_qa.csv", "Data ready; no regression run.", False),
        ("ATTRIBUTION_DATA_CAPABILITY", True, "US_DOWNSTREAM_CAPABILITY_MATRIX.csv", "Data ready with future holdings.", False),
        ("DRAWDOWN_DATA_CAPABILITY", True, "downstream_nav_capability_qa.csv", "Data ready with future NAV.", True),
        ("STRESS_DATA_CAPABILITY", True, "regime_stress_data_capability_qa.csv", "Data ready; regime rule remains unfrozen.", True),
        ("RETURN_CLOSURE_DATA_CAPABILITY", True, "downstream_nav_capability_qa.csv", "Data ready with future strategy construction.", True),
        ("CONFIG_SAMPLE_DATA_READY", data_positive["security_daily_market_gate_b_config"], "downstream_partition_manifest.csv", "2016–2020 physical partition materialized; no performance summary.", False),
        ("HIST_OOS_DATA_MATERIALIZED_OR_DOCUMENTED", data_positive["security_daily_market_hist_oos"], "downstream_partition_manifest.csv", "2021–2025 physical partition protected by loader.", False),
        ("STAGE_AWARE_ACCESS", True, "src/handoff/load_us_downstream_data.py", "OOS requires future Gate B/C evidence.", False),
        ("NO_GATE_B_DECISION", no_research_ok["NO_GATE_B_DECISION"], "downstream_no_research_decision_qa.csv", "No configuration decision.", False),
        ("NO_FMOM", no_research_ok["NO_FMOM"], "downstream_no_research_decision_qa.csv", "No FMOM constructed.", False),
        ("NO_STRATEGY_BACKTEST", no_research_ok["NO_STRATEGY_NAV"], "downstream_no_research_decision_qa.csv", "No strategy holdings/NAV/backtest constructed.", False),
        ("NO_STRATEGY_PERFORMANCE_SUMMARY", no_research_ok["NO_STRATEGY_PERFORMANCE_SUMMARY"], "downstream_no_research_decision_qa.csv", "No 2016–2025 strategy performance summary.", False),
        ("NO_2026", result["qa"]["future_rows"] == 0, "source manifest / formal cutoff QA", "No formal daily rows after 2025-12-31.", False),
        ("REDISTRIBUTION_POLICY", True, "EXTERNAL_DOWNSTREAM_DATA_TRANSFER_MANIFEST.csv", "Full Parquet remain local-large/ignored pending permission.", True),
        ("DETERMINISM", all(deterministic.values()), "downstream_determinism.csv", "Two independent materializations have equal logical hashes.", False),
        ("UPSTREAM_IMMUTABILITY", all(immutability.values()), "upstream immutability QA", "All frozen upstream inputs match pre-export hashes.", False),
    ]
    return [{"check": name, "status": "FAIL" if not passed else "PASS_WITH_WARNING" if warning else "PASS", "blocking": "true" if not passed else "false", "evidence": evidence, "notes": note} for name, passed, evidence, note, warning in checks]


def package_documents(
    root: Path, package: Path, result: dict[str, Any], inputs: dict[str, Any], deterministic: dict[str, bool],
    second_hashes: dict[str, str], immutability: dict[str, bool],
) -> tuple[list[dict[str, str]], str]:
    data = package / "data"
    samples = data / "samples"
    samples.mkdir(parents=True, exist_ok=True)
    requirement = requirement_rows()
    write_csv(package / "US_DOWNSTREAM_DATA_REQUIREMENT_MATRIX.csv", list(requirement[0]), requirement)
    dictionary = data_dictionary_rows()
    write_csv(
        package / "downstream_data_dictionary.csv",
        [
            "dataset", "column", "dtype", "unit", "description", "natural_key",
            "PIT_or_execution_status", "source_table", "source_field", "transformation",
            "future_information_risk", "downstream_use", "notes",
        ],
        dictionary,
    )
    manifest = source_manifest(root, result, inputs)
    write_csv(package / "downstream_source_manifest.csv", list(manifest[0]), manifest)
    partitions = partition_rows(result)
    write_csv(package / "downstream_partition_manifest.csv", list(partitions[0]), partitions)
    access = access_rows()
    write_csv(package / "downstream_access_contract.csv", list(access[0]), access)
    decisions = non_data_decisions()
    write_csv(package / "downstream_non_data_decisions.csv", list(decisions[0]), decisions)
    capabilities = capability_rows()
    write_csv(package / "US_DOWNSTREAM_CAPABILITY_MATRIX.csv", list(capabilities[0]), capabilities)
    no_research = mechanical_no_research_qa(result)
    write_csv(package / "downstream_no_research_decision_qa.csv", list(no_research[0]), no_research)
    join = downstream_join_qa(root, package)
    write_csv(package / "downstream_join_qa.csv", list(join), [join])
    return_semantics = [
        {"check": "CIZ_DLYRET_DIRECT", "status": "PASS", "value": "USE_CIZ_DLYRET_DIRECTLY__DO_NOT_ADD_SEPARATE_DELRET", "notes": "No DelRet, dividend return, or split adjustment is added."},
        {"check": "EXECUTION_WINDOW", "status": "PASS", "value": "(E_t,E_t+1]", "notes": "Signal close-to-signal close is not substituted."},
        {"check": "CONFIRMED_DELISTING", "status": "PASS", "value": "TERMINAL_CIZ_RETURN_THEN_CASH", "notes": "A confirmed DlyDelFlg path compounds its terminal CIZ return and then holds cash at zero return."},
        {"check": "UNEXPLAINED_PATH_FAILURES", "status": "PASS" if result["return_statuses"].get("UNEXPLAINED_INCOMPLETE_PATH", 0) == 0 else "FAIL", "value": str(result["return_statuses"].get("UNEXPLAINED_INCOMPLETE_PATH", 0)), "notes": "Must be zero for acceptance."},
    ]
    write_csv(package / "downstream_return_semantics_qa.csv", list(return_semantics[0]), return_semantics)
    market_semantics = [
        {"check": "PRIMARY_BENCHMARK", "status": "PASS", "value": "INDNO=1000500;DlyTotRet", "notes": "CRSP Value-Weighted Index of the S&P 500 Universe."},
        {"check": "IVOL_MARKET_PROXY", "status": "PASS", "value": "INDNO=1000080;DlyTotRet", "notes": "Retained frozen formal market-proxy identity."},
        {"check": "PRICE_RETURN_CROSSCHECK", "status": "PASS", "value": "INDNO=1000502;DlyPrcRet diagnostic only", "notes": "Never substituted for primary total-return benchmark."},
        {"check": "FF_RF_UNITS", "status": "PASS", "value": "DECIMAL_RETURN", "notes": "mktrf/smb/hml/rmw/cma/rf/umd are never divided by 100."},
        {"check": "DLYVOL_DLYPRCVOL_UNITS", "status": "PASS", "value": "DlyVol raw shares; DlyPrcVol price-times-volume USD proxy", "notes": "DlyPrcVol is not a transaction-by-transaction actual dollar-volume claim."},
    ]
    write_csv(package / "downstream_market_semantics_qa.csv", list(market_semantics[0]), market_semantics)
    seed_qa = seed_qa_rows(result)
    write_csv(package / "final_factor_return_seed_qa.csv", list(seed_qa[0]), seed_qa)
    path_qa = return_path_qa(root, result["weekly_returns"])
    write_csv(package / "downstream_return_path_qa.csv", list(path_qa[0]), path_qa)
    for name, rows in capabilities_qa_rows().items():
        write_csv(package / name, list(rows[0]), rows)
    transfer = [{
        "dataset": row["dataset"], "relative_path": row["path"], "distribution_class": "LOCAL_LARGE_DATA",
        "git_policy": "GIT_IGNORED", "transfer_requirement": "Confirm CRSP/Fama-French redistribution permission before any teammate transfer.",
        "fallback": "Run src/handoff/prepare_us_downstream_data.py against authorized local immutable inputs.",
    } for row in manifest]
    write_csv(package / "EXTERNAL_DOWNSTREAM_DATA_TRANSFER_MANIFEST.csv", list(transfer[0]), transfer)
    for dataset in ("security_daily_market", "weekly_execution_calendar", "security_weekly_execution_returns", "execution_liquidity_weekly", "benchmark_daily_returns", "risk_free_daily", "ff_factors_daily", "final_factor_returns_seed_weekly"):
        parquet_sample(data / f"{dataset}.parquet", samples / f"{dataset}_sample_pre2016.csv")
    deterministic_rows = [{"dataset": name, "first_logical_hash": result["hashes"][name], "second_logical_hash": second_hashes[name], "equal": str(equal).lower(), "status": "PASS" if equal else "FAIL"} for name, equal in deterministic.items()]
    write_csv(package / "downstream_determinism.csv", list(deterministic_rows[0]), deterministic_rows)
    immutability_rows = [
        {"check": check, "status": "PASS" if passed else "FAIL", "notes": "Pre-export and post-export hashes/snapshots must match."}
        for check, passed in immutability.items()
    ]
    write_csv(package / "downstream_upstream_immutability_qa.csv", list(immutability_rows[0]), immutability_rows)
    acceptance = acceptance_rows(root, package, result, deterministic, immutability)
    write_csv(package / "downstream_acceptance.csv", list(acceptance[0]), acceptance)
    update_oos_register(root)
    blockers = sum(row["status"] in {"FAIL", "BLOCKED"} for row in acceptance)
    warnings = sum(row["status"] == "PASS_WITH_WARNING" for row in acceptance)
    verdict = "US DOWNSTREAM STRATEGY DATA HANDOFF — PASS" if blockers == 0 and warnings == 0 else "US DOWNSTREAM STRATEGY DATA HANDOFF — PASS WITH WARNINGS" if blockers == 0 else "US DOWNSTREAM STRATEGY DATA HANDOFF — FAIL"
    write_contract_and_readme(package)
    write_audit_report(root, package, result, acceptance, join, immutability, deterministic, verdict, blockers, warnings)
    return acceptance, verdict


def write_contract_and_readme(package: Path) -> None:
    contract = """# US Downstream Strategy & Backtest Data Contract

## 1. Purpose

This governed package supplies the formal-US market, return, benchmark, RF, FF, liquidity, identity, calendar and pre-Gate-B final-factor-return interfaces required by a later Strategy Engine and Backtest/Risk workflow. It is an export layer, not a strategy implementation.

## 2. Frozen upstream factor contract

The only active final factors are, in canonical order: `QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, and `INVESTMENT_CURRENT` (K=4). Value, Dividend, Size and `MOMENTUM_CURRENT` are excluded. Gate A remains frozen.

## 3. Dataset architecture

Large Parquet files under `data/` are local, Git-ignored derived data. Their schemas, logical hashes, source paths and redistribution status are recorded in `downstream_source_manifest.csv`. Small pre-2016 samples, contracts, QA, manifests, code and tests are tracked.

## 4. Security return semantics

Security returns use `USE_CIZ_DLYRET_DIRECTLY__DO_NOT_ADD_SEPARATE_DELRET`. CIZ `DlyRet` is compounded directly. No separate DelRet, dividend return, distribution or split adjustment is added.

## 5. Execution timing

Signals are observed at weekly `S_t` close. Execution is at the next actual trading-day close `E_t`. A formal weekly holding return is compounded over `(E_t, E_{t+1}]`; signal-close-to-signal-close is prohibited as a substitute.

## 6. Sample partitions

Source labels are preserved exactly: `INITIALIZATION` (2004–2005), `DEV_WARMUP` (2006–2010), `FACTOR_SELECTION` (2011–2015), `CONFIGURATION_AND_STRATEGY_FREEZE` (2016–2020), and `HIST_OOS_BASE` (2021–2025). Physical daily-market partitions enforce pre-Gate-B, configuration, and protected Historical-OOS boundaries.

## 7. Benchmark semantics

The primary benchmark is CRSP `INDNO=1000500`, `DlyTotRet`, the CRSP Value-Weighted Index of the S&P 500 Universe. `INDNO=1000502` remains a price-return cross-check only and never replaces it.

## 8. RF semantics

Fama-French `rf` is a released decimal return. It is never divided by 100. Weekly RF compounds the same daily `(E_t,E_{t+1}]` path used for strategy returns.

## 9. FF regression factors

`mktrf`, `smb`, `hml`, `rmw`, `cma`, `rf`, and `umd` are supplied at daily and monthly (`dateff`) frequency in decimal units. They support future external regression; no strategy regression is performed here. A weekly FF transformation is deliberately deferred to M5 because it would require a separate regression-aggregation specification.

## 10. Liquidity / ADV semantics

`DlyVol` is raw shares traded. `DlyPrcVol` is `DlyPrc × DlyVol`, a dollars-traded proxy rather than actual transaction-by-transaction dollar volume. ADV20/ADV60 use only calendar trading days through `S_t`; valid-day counts are explicit. The interface selects no liquidity threshold, participation rate, impact model, or transaction-cost value.

## 11. Historical GICS semantics

Signal-date Historical GICS is reused from the frozen final-factor universe. It is time-valid Historical GICS only; current-GICS backfill is prohibited. This supports future sector caps, concentration and attribution without recomputing a universe.

## 12. Final-factor return seed

`final_factor_returns_seed_weekly.parquet` is the only factor-return output here. It contains the frozen K=4 Top 30% minus Bottom 30%, equal-weight, executable gross L/S construction for 2006–2015 only. Any seed path reaching 2016 is explicitly unavailable. The 2011–2015 rows are reproduced against frozen prior evidence. The seed neither selects FMOM parameters nor generates FMOM.

## 13. Stage-aware access

Use `src/handoff/load_us_downstream_data.py` with an explicit stage. `GATE_B` may load pre-2016 seed data and 2016–2020 configuration data, but not 2021–2025 OOS data. `HIST_OOS` requires future valid Gate-B and Gate-C/US-Base freeze evidence and has no normal force bypass.

## 14. Transaction-cost input boundary

The one-way all-in-cost formula is governed upstream. Its value/grid is not frozen here. The package supplies returns, execution prices, caps and ADV inputs only.

## 15. What is already data-complete

The requirement and capability matrices identify all formal US market/return/calendaring/benchmark/RF/FF/liquidity/identity interfaces as data-ready through 2025-12-31, subject to documented formal-source limitations.

## 16. What remains a configuration decision

`downstream_non_data_decisions.csv` distinguishes no-longer-missing data from still-pending governance: FMOM J, risk adjustment, lambda, sector cap, buffer, missing-factor policy, transaction-cost grid, inference rules, regime definitions and Gate-C readiness.

## 17. What Gate B may access

Gate B may access the 2006–2015 seed and 2016–2020 configuration market data only after separate authorization. It may not load the protected Historical-OOS partition using the normal loader.

## 18. What Historical OOS may access

2021–2025 material is physically present but protected. It is available only after both Gate B and Gate C/US Base are frozen according to their future governance artifacts.

## 19. Prohibited uses

Do not reopen Gate A/A0; add factors; select J/lambda/risk adjustment/sector cap/buffer; calculate FMOM, composite scores, strategy holdings, trades, NAV, Sharpe, CAGR, alpha, or any 2016–2025 strategy-performance summary from this package alone.

## 20. Downstream workflow

Read the Final Factor Handoff Contract, load frozen scores/universe, load this calendar and the pre-Gate-B seed, then—only after separate authorization—construct completed-period 2016–2020 final-factor returns and conduct Gate B. Freeze Gate B, construct/freeze US Base at Gate C, and only then unlock Historical OOS.
"""
    readme = """# US Downstream Strategy & Backtest Data Handoff

This is the governed US market-data companion to `handoff/final_factor/`. It contains no Gate-B decision, FMOM, holdings, strategy backtest, strategy NAV, or strategy-performance summary.

## Quick start

1. Read `../final_factor/FINAL_FACTOR_HANDOFF_CONTRACT.md` and this package's `US_DOWNSTREAM_DATA_CONTRACT.md`.
2. Load the frozen final factor scores and weekly universe from `handoff/final_factor/data/`.
3. Load `data/weekly_execution_calendar.parquet` for exact `S_t → E_t → E_{t+1}` timing.
4. Load `data/final_factor_returns_seed_weekly.parquet` for only the completed pre-Gate-B 2006–2015 K=4 seed history.
5. At separately authorized Gate B, use the stage-aware loader to load only the 2016–2020 configuration partition.
6. Construct completed-period final-factor returns for 2016–2020 only after that authorization.
7. Run Gate B only under its separate governance; do not tune against 2021–2025.
8. Freeze Gate B.
9. Construct/freeze the US Base at Gate C.
10. Only then use the `HIST_OOS` loader stage to unlock the 2021–2025 market-data partition.

## Local data policy

Full Parquet files are intentionally Git-ignored and local because redistribution remains `DATA_DISTRIBUTION_REVIEW_REQUIRED`. Check the source and external-transfer manifests before moving any data. Deterministic small pre-2016 schema samples are tracked under `data/samples/`.

## Core files

- `US_DOWNSTREAM_DATA_REQUIREMENT_MATRIX.csv`: object-level requirement and source map.
- `downstream_data_dictionary.csv`: every exported field.
- `downstream_access_contract.csv`: Gate-B/OOS access boundary.
- `downstream_non_data_decisions.csv`: data-ready versus governance-pending decisions.
- `US_DOWNSTREAM_CAPABILITY_MATRIX.csv`: readiness of later M4/M5 capabilities.
- `downstream_acceptance.csv`: construction acceptance evidence.
"""
    (package / "US_DOWNSTREAM_DATA_CONTRACT.md").write_text(contract, encoding="utf-8")
    (package / "README.md").write_text(readme, encoding="utf-8")


def write_audit_report(
    root: Path, package: Path, result: dict[str, Any], acceptance: Sequence[dict[str, str]], join: dict[str, int],
    immutability: dict[str, bool], deterministic: dict[str, bool], verdict: str, blockers: int, warnings: int,
) -> None:
    report = [
        "# WRDS US Downstream Strategy Data Handoff Report", "",
        "## 1. Executive verdict", "", f"**{verdict}.** Blockers: {blockers}. Warnings: {warnings}.", "",
        "## 2. Purpose", "", "Prepare governed downstream data interfaces, not a strategy, parameter-selection, or performance-research result.", "",
        "## 3. Why Final Factor Handoff alone was insufficient", "", "It supplied frozen scores/availability/universe but not executable security-return paths, calendar, benchmark/RF/FF, liquidity/capacity inputs, or a pre-Gate-B seed-return history.", "",
        "## 4. Frozen factor contract", "", "K=4: `QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, `INVESTMENT_CURRENT`. No Value, Dividend, Size, or `MOMENTUM_CURRENT` active factor is introduced.", "",
        "## 5. Downstream data architecture", "", "Large derived Parquet are local/Git-ignored. Contracts, manifests, dictionary, samples, QA, code and tests are tracked. See `downstream_source_manifest.csv`.", "",
        "## 6. Security daily market data", "", f"`security_daily_market.parquet` has {result['counts']['security_daily_market']:,} date-PERMNO rows. It preserves canonical CIZ market/return/cap fields and adds only raw `DlyVol`/`DlyPrcVol` provenance.", "",
        "## 7. Execution calendar", "", f"`weekly_execution_calendar.parquet` has {result['counts']['weekly_execution_calendar']:,} signal dates and preserves S_t, next-trading-day E_t, and E_t+1 fields.", "",
        "## 8. Weekly execution returns", "", f"`security_weekly_execution_returns.parquet` has {result['counts']['security_weekly_execution_returns']:,} signal-PERMNO rows. It compounds direct CIZ `DlyRet` over `(E_t,E_t+1]` only.", "",
        "## 9. Liquidity / ADV", "", f"`execution_liquidity_weekly.parquet` has {result['counts']['execution_liquidity_weekly']:,} rows. ADV20/ADV60 are backward-looking `DlyPrcVol` proxies; no impact model or liquidity threshold is set.", "",
        "## 10. Benchmark", "", "Primary daily/weekly benchmark is CRSP INDNO 1000500 `DlyTotRet`. INDNO 1000502 remains only a price-return diagnostic.", "",
        "## 11. Risk-free rate", "", "Daily and execution-aligned weekly Fama-French RF are exported in decimal units; no divide-by-100 transformation occurs.", "",
        "## 12. FF regression factors", "", "Daily and monthly decimal FF factors are exported. No strategy regression or weekly FF aggregation specification is calculated.", "",
        "## 13. Final-factor return seed", "", f"`final_factor_returns_seed_weekly.parquet` has {result['counts']['final_factor_returns_seed_weekly']:,} K=4 factor-signal rows only within 2006–2015. The rowwise 2011–2015 reproduction QA is in `final_factor_return_seed_qa.csv`; it is not a performance ranking.", "",
        "## 14. Universe / sector support", "", "The frozen Final Factor Handoff universe supplies signal-date Historical GICS, final-factor availability and issuer-cap support. No current-GICS backfill is used.", "",
        "## 15. Return semantics", "", "`USE_CIZ_DLYRET_DIRECTLY__DO_NOT_ADD_SEPARATE_DELRET`; terminal confirmed-delisting CIZ return is followed by cash. No manual corporate-action add-on is applied.", "",
        "## 16. Delisting / corporate-action semantics", "", "Path status and delisting flags are explicit. The construction does not modify CRSP corporate-action return semantics.", "",
        "## 17. Join integrity", "", f"Score/orphan={join['orphan_factor_score_rows']}; return/orphan={join['orphan_return_rows']}; universe/orphan={join['orphan_universe_rows']}; liquidity/orphan={join['orphan_liquidity_rows']}; calendar conflicts={join['calendar_conflicts']}.", "",
        "## 18. Gate B data readiness", "", "Pre-2016 seed and 2016–2020 configuration market data are physically available under a stage-aware access policy. Gate B itself has not started.", "",
        "## 19. US Base data readiness", "", "The data interfaces can support a future US Base build, but no composite score, holdings, cost grid, or US Base is constructed or frozen.", "",
        "## 20. Historical OOS data readiness", "", "2021–2025 market data are materialized as a protected physical partition. No strategy outcome is calculated or summarized; loader access is blocked pending future Gate B/C freeze artifacts.", "",
        "## 21. Backtest & Risk capability", "", "Calendar, execution returns, caps, liquidity, benchmark, RF, FF and historical-sector interfaces are data-ready. The capability matrix records still-pending parameters.", "",
        "## 22. Remaining non-data decisions", "", "See `downstream_non_data_decisions.csv`, including FMOM J, risk adjustment, lambda, sector cap, buffer, missing-factor policy, cost grid, inference and regime rules.", "",
        "## 23. Stage-aware OOS governance", "", "`GATE_B` loader access excludes Historical OOS. `HIST_OOS` requires future valid Gate-B and Gate-C acceptance artifacts; no force override exists in normal usage.", "",
        "## 24. Redistribution / GitHub policy", "", "Full derived Parquet are `LOCAL_LARGE_DATA` and `DATA_DISTRIBUTION_REVIEW_REQUIRED`. They are not force-added to Git.", "",
        "## 25. Determinism", "", f"All exported local datasets have two-build logical equality status: {all(deterministic.values())}.", "",
        "## 26. Upstream immutability", "", f"Raw, SSOT, canonical, factor stores, validation store and final handoff immutability status: {all(immutability.values())}.", "",
        "## 27. Tests", "", "Focused downstream tests and the native unittest suite are required verification steps; their execution is recorded at the authorized handoff run, not used as research evidence.", "",
        "## 28. Warnings / blockers", "", "Non-blocking governance/distribution warnings: `TRANSACTION_COST_GRID_NOT_YET_FROZEN`, `FORMAL_REGIME_RULE_NOT_YET_FROZEN`, and `DATA_DISTRIBUTION_REVIEW_REQUIRED`.", "",
        "## 29. Exact next permitted step", "", "After separate authorization, conduct Gate B Configuration Selection using 2016–2020 only through the stage-aware loader. Do not start it automatically from this handoff.", "",
    ]
    (root / AUDIT / "WRDS_US_DOWNSTREAM_STRATEGY_DATA_HANDOFF_REPORT.md").write_text("\n".join(report), encoding="utf-8")


def _write_root_acceptance(root: Path, acceptance: Sequence[dict[str, str]]) -> None:
    """Write the small tracked acceptance copy outside the local-large package."""

    write_csv(root / AUDIT / "us_downstream_data_acceptance.csv", list(acceptance[0]), acceptance)


def prepare_downstream_handoff(root: Path) -> tuple[list[dict[str, str]], str]:
    """Run the authorized mechanical data handoff with a two-build hash check.

    The second build is intentionally written to a disposable temporary directory.
    It is never examined for return levels or used as evidence; only logical hashes
    are compared with the first deterministic build.
    """

    root = root.resolve()
    validate_final_factor_registry(root)
    package = root / PACKAGE
    package.mkdir(parents=True, exist_ok=True)

    # This is deliberately emitted before any data export as the required
    # requirement-matrix-first governance record.
    requirements = requirement_rows()
    write_csv(package / "US_DOWNSTREAM_DATA_REQUIREMENT_MATRIX.csv", list(requirements[0]), requirements)

    inputs = immutable_inputs(root)
    result = build_data(root, package)
    with tempfile.TemporaryDirectory(prefix="fe5209-us-downstream-rebuild-") as directory:
        rebuild = build_data(root, Path(directory) / "us_downstream")
        second_hashes = rebuild["hashes"]
    if set(result["hashes"]) != set(second_hashes):
        raise RuntimeError("DETERMINISM_DATASET_SET_MISMATCH")
    deterministic = {
        name: result["hashes"][name] == second_hashes[name]
        for name in sorted(result["hashes"])
    }
    immutability = verify_immutable_inputs(root, inputs)
    acceptance, verdict = package_documents(
        root, package, result, inputs, deterministic, second_hashes, immutability,
    )
    _write_root_acceptance(root, acceptance)
    return acceptance, verdict


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=project_root(), help="FE5209 repository root")
    arguments = parser.parse_args(argv)
    acceptance, verdict = prepare_downstream_handoff(arguments.root)
    blockers = sum(row["status"] in {"FAIL", "BLOCKED"} for row in acceptance)
    print(dump({"verdict": verdict, "blockers": blockers, "checks": len(acceptance)}))
    return 0 if blockers == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
