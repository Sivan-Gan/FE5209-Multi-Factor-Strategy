"""Construct and validate the DEV weekly estimated historical market-cap input.

The canonical object is intentionally limited to the signed DEV convention:
exact signal-date Yahoo ``close`` multiplied by PIT-available primary SEC
shares aligned only through audited standard split/reverse-split events.
It is not a Formal PIT/CRSP market-cap claim and it does not construct any
factor, ratio, score, IC, portfolio, FMOM, or strategy output.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import heapq
import json
import math
import os
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from src.data import historical_market_cap_signoff_audit as signoff
from src.data import historical_market_cap_spec_audit as spec
from src.data import source_audit


CANONICAL_VERSION = "fe5209.us_estimated_market_cap.dev.v1"
EVIDENCE_STAGE = "DEV_CURRENT_UNIVERSE"
MARKET_CAP_SOURCE = "SEC_YAHOO_ESTIMATE_DEV"
PRIMARY_SOURCE_TAG = "dei:EntityCommonStockSharesOutstanding"
REASON_PRIORITY = "share_source>PIT/conflict>invalid_share>multiclass>price>nonstandard_action>other"
WINDOWS = (
    ("factor_selection_2011_2015", date(2011, 1, 1), date(2015, 12, 31)),
    ("configuration_selection_2016_2020", date(2016, 1, 1), date(2020, 12, 31)),
    ("historical_oos_2021_2026_09", date(2021, 1, 1), date(2026, 9, 30)),
)
PANEL_COLUMNS = (
    "signal_date", "ticker", "yahoo_ticker", "cik", "issuer", "gics_sector_current", "price_close", "price_date",
    "price_review_flag", "price_zero_volume_flag", "price_review_security_flag", "price_status",
    "reported_shares", "shares_source_tag", "shares_unit", "shares_asof_date", "shares_filed_date",
    "shares_form", "shares_accession", "shares_amendment_flag", "shares_selection_status",
    "shares_selection_reason", "share_asof_age_days", "share_filing_age_days", "staleness_bucket",
    "staleness_flag", "split_adjustment_factor", "applied_split_events_json",
    "standard_split_event_count", "nonstandard_action_events_json", "corporate_action_flag",
    "aligned_shares", "estimated_market_cap", "market_cap_source", "availability_flag",
    "exclusion_reason", "all_exclusion_flags_json", "multi_class_flag", "sec_mapping_available",
    "evidence_stage", "canonical_version",
)


@dataclass(frozen=True)
class SplitChain:
    """All post-as-of Yahoo action evidence for one reported-share observation."""

    factor: float
    standard_events: tuple[tuple[date, float], ...]
    nonstandard_events: tuple[tuple[date, float], ...]


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def paths(root: Path) -> dict[str, Path]:
    audit = root / "outputs" / "audit"
    return {
        "canonical": root / "data" / "canonical" / "us_weekly_estimated_market_cap_dev.sqlite",
        "coverage": audit / "estimated_market_cap_weekly_coverage_dev.csv",
        "reasons": audit / "estimated_market_cap_exclusion_reasons_dev.csv",
        "splits": audit / "estimated_market_cap_split_validation_dev.csv",
        "jumps": audit / "estimated_market_cap_jump_audit_dev.csv",
        "updates": audit / "estimated_market_cap_shares_update_audit_dev.csv",
        "distribution": audit / "estimated_market_cap_distribution_dev.csv",
        "value": audit / "estimated_market_cap_value_input_coverage_dev.csv",
        "acceptance": audit / "estimated_market_cap_construction_acceptance_report.md",
        "document": root / "docs" / "ESTIMATED_HISTORICAL_MARKET_CAP_DEV.md",
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def _finite(value: Any) -> float | None:
    return spec._finite_float(value)


def _window(day: date) -> str | None:
    return spec._window(day)


def _percentile(values: list[float], quantile: float) -> float | None:
    return spec._percentile(values, quantile)


def staleness_bucket(age_days: int | None) -> tuple[str, bool]:
    """Return a diagnostic-only bucket; it is never an eligibility cutoff."""
    if age_days is None:
        return "NO_SELECTED_SHARES", False
    if age_days <= 120:
        return "LE_120_DAYS", False
    if age_days <= 180:
        return "DAYS_121_TO_180", False
    if age_days <= 270:
        return "DAYS_181_TO_270", False
    if age_days <= 365:
        return "DAYS_271_TO_365", False
    return "GT_365_DAYS", True


def split_chain(events: Iterable[tuple[date, float, bool]], share_asof_date: date) -> SplitChain:
    """Build the signed delivery-basis chain strictly after the share as-of date."""
    standard: list[tuple[date, float]] = []
    nonstandard: list[tuple[date, float]] = []
    factor = 1.0
    for event_date, event_factor, is_standard in sorted(events):
        if event_date <= share_asof_date:
            continue
        if is_standard:
            standard.append((event_date, event_factor))
            factor *= event_factor
        else:
            nonstandard.append((event_date, event_factor))
    return SplitChain(factor, tuple(standard), tuple(nonstandard))


def estimated_market_cap(close: float | None, aligned_shares: float | None) -> float | None:
    """Calculate only a finite positive USD market-cap estimate."""
    if close is None or aligned_shares is None or close <= 0 or aligned_shares <= 0:
        return None
    result = close * aligned_shares
    return result if math.isfinite(result) and result > 0 else None


def _json_events(events: Iterable[tuple[date, float]]) -> str:
    return json.dumps(
        [{"event_date": event_date.isoformat(), "factor": factor} for event_date, factor in events],
        sort_keys=True,
        separators=(",", ":"),
    )


def _primary_reason(
    *,
    sec_mapping_available: bool,
    share_status: str,
    raw_records: list[signoff.ShareRaw],
    share_value: float | None,
    is_multiclass: bool,
    usable_price: bool,
    price_code: str,
    has_nonstandard_action: bool,
) -> str:
    if not sec_mapping_available:
        return "NO_SEC_SHARES"
    return signoff.candidate_reason(
        share_status=share_status,
        raw_records=raw_records,
        share_value=share_value,
        is_multiclass=is_multiclass,
        usable_price=usable_price,
        price_code=price_code,
        nonstandard=has_nonstandard_action,
    )


def _read_inputs(root: Path) -> tuple[
    list[dict[str, str]], set[str], list[date], dict[tuple[str, date], dict[str, str]],
    dict[str, list[tuple[date, float, bool]]],
]:
    master, _all_by_cik, _mapped_by_cik, _by_ticker, mapped_tickers, signal_dates = spec._read_identity(root)
    prices, splits, _event_rows, _counts = spec._read_price_evidence(root, set(signal_dates))
    return master, mapped_tickers, signal_dates, prices, splits


def _fundamental_availability(root: Path) -> dict[str, set[tuple[str, date]]]:
    db = root / "data" / "canonical" / "us_weekly_pit_fundamentals_dev.sqlite"
    wanted = {
        "net_income": ("net_income", "annual_candidate"),
        "common_equity": ("common_equity", "instant"),
        "ocf": ("operating_cash_flow", "annual_candidate"),
        "capex": ("capital_expenditure", "annual_candidate"),
    }
    result: dict[str, set[tuple[str, date]]] = {name: set() for name in wanted}
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as connection:
        for name, (concept, period_class) in wanted.items():
            for ticker, signal_date in connection.execute(
                "SELECT ticker, signal_date FROM weekly_pit_fundamental_snapshot "
                "WHERE economic_concept = ? AND period_class = ?",
                (concept, period_class),
            ):
                result[name].add((str(ticker), date.fromisoformat(str(signal_date))))
    return result


def _read_daily_event_rows(root: Path, event_tickers: set[str]) -> dict[str, list[dict[str, str]]]:
    """Read only representative-event tickers for daily construction validation."""
    result: dict[str, list[dict[str, str]]] = defaultdict(list)
    path = root / "data" / "canonical" / "us_daily_market_dev.csv.gz"
    with gzip.open(path, "rt", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["ticker"] in event_tickers:
                result[row["ticker"]].append(row)
    return dict(result)


def _read_primary_for_ciks(
    root: Path, ciks: set[int]
) -> tuple[dict[int, list[signoff.ShareRaw]], dict[int, list[spec.ShareObservation]]]:
    """Read named CIK archives only, for fast real-event validation."""
    raw: dict[int, list[signoff.ShareRaw]] = defaultdict(list)
    eligible: dict[int, list[spec.ShareObservation]] = defaultdict(list)
    facts = source_audit.package_root(root) / source_audit.SEC_FACTS_DIR
    for cik in sorted(ciks):
        archive = facts / f"CIK{cik:010d}.json.gz"
        if not archive.exists():
            continue
        with gzip.open(archive, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        observations = (
            payload.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding", {})
            .get("units", {}).get("shares", [])
        )
        for ordinal, item in enumerate(observations if isinstance(observations, list) else []):
            if not isinstance(item, dict):
                continue
            value = _finite(item.get("val"))
            if value is None:
                continue
            record = signoff.ShareRaw(
                cik=cik, value=value, value_text=str(item.get("val")), end_date=spec._parse_date(item.get("end")),
                filed_date=spec._parse_date(item.get("filed")), form=str(item.get("form") or ""),
                accession=str(item.get("accn") or ""), amendment=str(item.get("form") or "").endswith("/A"), ordinal=ordinal,
            )
            raw[cik].append(record)
            if (
                record.value > 0 and record.end_date is not None and record.filed_date is not None
                and spec._is_allowed_share_form(record.form) and record.end_date <= record.filed_date
            ):
                eligible[cik].append(spec.ShareObservation(
                    cik=cik, taxonomy="dei", tag="EntityCommonStockSharesOutstanding", unit="shares",
                    value_text=record.value_text, value=record.value, end_date=record.end_date,
                    filed_date=record.filed_date, form=record.form, accession=record.accession,
                    frame="", fiscal_year="", fiscal_period="", start_date="", ordinal=record.ordinal,
                ))
    return dict(raw), dict(eligible)


def _create_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        CREATE TABLE weekly_estimated_market_cap (
            signal_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            yahoo_ticker TEXT NOT NULL,
            cik INTEGER,
            issuer TEXT NOT NULL,
            gics_sector_current TEXT,
            price_close REAL,
            price_date TEXT,
            price_review_flag INTEGER NOT NULL,
            price_zero_volume_flag INTEGER NOT NULL,
            price_review_security_flag INTEGER NOT NULL,
            price_status TEXT NOT NULL,
            reported_shares REAL,
            shares_source_tag TEXT,
            shares_unit TEXT,
            shares_asof_date TEXT,
            shares_filed_date TEXT,
            shares_form TEXT,
            shares_accession TEXT,
            shares_amendment_flag INTEGER,
            shares_selection_status TEXT NOT NULL,
            shares_selection_reason TEXT NOT NULL,
            share_asof_age_days INTEGER,
            share_filing_age_days INTEGER,
            staleness_bucket TEXT NOT NULL,
            staleness_flag INTEGER NOT NULL,
            split_adjustment_factor REAL,
            applied_split_events_json TEXT NOT NULL,
            standard_split_event_count INTEGER NOT NULL,
            nonstandard_action_events_json TEXT NOT NULL,
            corporate_action_flag INTEGER NOT NULL,
            aligned_shares REAL,
            estimated_market_cap REAL,
            market_cap_source TEXT NOT NULL,
            availability_flag INTEGER NOT NULL,
            exclusion_reason TEXT NOT NULL,
            all_exclusion_flags_json TEXT NOT NULL,
            multi_class_flag INTEGER NOT NULL,
            sec_mapping_available INTEGER NOT NULL,
            evidence_stage TEXT NOT NULL,
            canonical_version TEXT NOT NULL,
            PRIMARY KEY (ticker, signal_date),
            CHECK (availability_flag IN (0, 1)),
            CHECK (estimated_market_cap IS NULL OR estimated_market_cap > 0)
        );
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE INDEX idx_estimated_market_cap_signal_date
            ON weekly_estimated_market_cap(signal_date);
        CREATE INDEX idx_estimated_market_cap_availability
            ON weekly_estimated_market_cap(availability_flag, signal_date);
        CREATE INDEX idx_estimated_market_cap_cik
            ON weekly_estimated_market_cap(cik, signal_date);
        """
    )
    return connection


def _as_int(value: Any) -> int | None:
    return source_audit._as_int(value)


def _row_tuple(
    *,
    day: date,
    security: dict[str, str],
    cik: int | None,
    sec_mapping_available: bool,
    selection: spec.ShareSelection,
    raw_records: list[signoff.ShareRaw],
    price: dict[str, str] | None,
    chain: SplitChain | None,
    is_multiclass: bool,
) -> tuple[Any, ...]:
    observation = selection.observation
    price_status, usable_price = signoff.price_status(price)
    price_close = _finite(price.get("close")) if price else None
    zero_volume = bool(price and price.get("is_zero_volume", "").lower() == "true")
    review_security = bool(price and price.get("is_review_security", "").lower() == "true")
    share_value = observation.value if observation else None
    nonstandard = bool(chain and chain.nonstandard_events)
    reason = _primary_reason(
        sec_mapping_available=sec_mapping_available,
        share_status=selection.status,
        raw_records=raw_records,
        share_value=share_value,
        is_multiclass=is_multiclass,
        usable_price=usable_price,
        price_code=price_status,
        has_nonstandard_action=nonstandard,
    )
    asof_age = (day - observation.end_date).days if observation else None
    filing_age = (day - observation.filed_date).days if observation else None
    bucket, stale_flag = staleness_bucket(asof_age)
    standard_events = chain.standard_events if chain else ()
    nonstandard_events = chain.nonstandard_events if chain else ()
    split_factor = chain.factor if chain else None
    aligned = share_value * split_factor if reason == "AVAILABLE" and share_value is not None and split_factor is not None else None
    value = estimated_market_cap(price_close, aligned) if reason == "AVAILABLE" else None
    if reason == "AVAILABLE" and value is None:
        reason = "OTHER"
        aligned = None
    available = reason == "AVAILABLE"
    flags = {
        "sec_mapping_available": sec_mapping_available,
        "share_selection_status": selection.status,
        "share_selection_reason": selection.reason,
        "share_value_le_one": share_value is not None and share_value <= 1,
        "shares_pit_violation": bool(observation and observation.filed_date > day),
        "multi_class_flag": is_multiclass,
        "price_row_exists": price is not None,
        "price_status": price_status,
        "price_zero_volume": zero_volume,
        "price_review_security": review_security,
        "price_ohlc_plausible": bool(price and price.get("is_ohlc_plausible", "").lower() == "true"),
        "nonstandard_corporate_action_after_asof": nonstandard,
        "standard_split_event_count": len(standard_events),
        "staleness_flag_over_365_days": stale_flag,
    }
    issuer = security.get("sec_name") or security.get("security_name") or security["ticker"]
    values = {
        "signal_date": day.isoformat(),
        "ticker": security["ticker"],
        "yahoo_ticker": security["yahoo_ticker"],
        "cik": cik,
        "issuer": issuer,
        "gics_sector_current": security.get("gics_sector_current") or None,
        "price_close": price_close,
        "price_date": day.isoformat() if price else None,
        "price_review_flag": int(usable_price and (zero_volume or review_security)),
        "price_zero_volume_flag": int(zero_volume),
        "price_review_security_flag": int(review_security),
        "price_status": price_status,
        "reported_shares": share_value,
        "shares_source_tag": PRIMARY_SOURCE_TAG if observation else None,
        "shares_unit": observation.unit if observation else None,
        "shares_asof_date": observation.end_date.isoformat() if observation else None,
        "shares_filed_date": observation.filed_date.isoformat() if observation else None,
        "shares_form": observation.form if observation else None,
        "shares_accession": observation.accession if observation else None,
        "shares_amendment_flag": int(observation.is_amendment) if observation else None,
        "shares_selection_status": selection.status,
        "shares_selection_reason": selection.reason,
        "share_asof_age_days": asof_age,
        "share_filing_age_days": filing_age,
        "staleness_bucket": bucket,
        "staleness_flag": int(stale_flag),
        "split_adjustment_factor": split_factor,
        "applied_split_events_json": _json_events(standard_events) if available else "[]",
        "standard_split_event_count": len(standard_events),
        "nonstandard_action_events_json": _json_events(nonstandard_events),
        "corporate_action_flag": int(nonstandard),
        "aligned_shares": aligned,
        "estimated_market_cap": value,
        "market_cap_source": MARKET_CAP_SOURCE,
        "availability_flag": int(available),
        "exclusion_reason": reason,
        "all_exclusion_flags_json": json.dumps(flags, sort_keys=True, separators=(",", ":")),
        "multi_class_flag": int(is_multiclass),
        "sec_mapping_available": int(sec_mapping_available),
        "evidence_stage": EVIDENCE_STAGE,
        "canonical_version": CANONICAL_VERSION,
    }
    return tuple(values[column] for column in PANEL_COLUMNS)


def construct_panel(root: Path | None = None) -> dict[str, Any]:
    """Build only the complete canonical estimated-market-cap grid."""
    root = (root or project_root()).resolve()
    out = paths(root)
    master, mapped_tickers, signal_dates, prices, splits = _read_inputs(root)
    securities_by_cik: dict[int | None, list[dict[str, str]]] = defaultdict(list)
    for security in master:
        securities_by_cik[_as_int(security.get("cik"))].append(security)
    all_ciks = {cik for cik in securities_by_cik if cik is not None}
    raw, eligible = signoff._read_raw_primary(root, all_ciks)
    selectors = {
        cik: spec.ShareSlot(sorted(eligible.get(cik, []), key=lambda item: (item.filed_date, item.end_date, item.source_id)))
        for cik in all_ciks
    }
    single_class_ciks = {cik for cik, securities in securities_by_cik.items() if cik is not None and len(securities) == 1}
    temporary = out["canonical"].with_suffix(".sqlite.tmp")
    connection = _create_database(temporary)
    placeholders = ",".join("?" for _ in PANEL_COLUMNS)
    insert_sql = f"INSERT INTO weekly_estimated_market_cap ({','.join(PANEL_COLUMNS)}) VALUES ({placeholders})"
    batch: list[tuple[Any, ...]] = []
    total_rows = 0
    availability_count = 0
    try:
        for day in signal_dates:
            for cik, securities in sorted(securities_by_cik.items(), key=lambda item: (-1 if item[0] is None else item[0])):
                selection = selectors[cik].advance(day) if cik is not None else spec.ShareSelection("NO_ELIGIBLE_FILING", reason="NO_CIK")
                source_records = raw.get(cik, []) if cik is not None else []
                for security in securities:
                    ticker = security["ticker"]
                    mapping = ticker in mapped_tickers
                    observation = selection.observation if mapping else None
                    effective_selection = selection if mapping else spec.ShareSelection("NO_ELIGIBLE_FILING", reason="NO_SEC_MAPPING")
                    chain = split_chain(splits.get(ticker, []), observation.end_date) if observation else None
                    row = _row_tuple(
                        day=day,
                        security=security,
                        cik=cik,
                        sec_mapping_available=mapping,
                        selection=effective_selection,
                        raw_records=source_records if mapping else [],
                        price=prices.get((ticker, day)),
                        chain=chain,
                        is_multiclass=cik is not None and cik not in single_class_ciks,
                    )
                    batch.append(row)
                    total_rows += 1
                    availability_count += int(row[PANEL_COLUMNS.index("availability_flag")])
                    if len(batch) >= 5_000:
                        connection.executemany(insert_sql, batch)
                        batch.clear()
        if batch:
            connection.executemany(insert_sql, batch)
        metadata = {
            "canonical_version": CANONICAL_VERSION,
            "evidence_stage": EVIDENCE_STAGE,
            "market_cap_source": MARKET_CAP_SOURCE,
            "price_definition": "Exact weekly signal-date canonical Yahoo close; USD price per share; adj_close/fills excluded.",
            "shares_definition": "PIT DEI EntityCommonStockSharesOutstanding, unit shares, filed_date <= signal_date, selected value > 1.",
            "split_definition": "Product of audited standard/reverse Yahoo split factors with event_date strictly after SEC shares as-of date; any later non-standard action excludes the observation.",
            "staleness_definition": "No cutoff. staleness_flag is diagnostic only for share_asof_age_days > 365; 120/180/270/365 buckets are retained without changing availability.",
            "unit_definition": "price_close is USD/share; reported_shares and aligned_shares are shares; estimated_market_cap is USD at scale 1.",
            "grid_shape": f"{len(master)} securities x {len(signal_dates)} signal dates",
        }
        connection.executemany("INSERT INTO metadata(key, value) VALUES (?, ?)", sorted(metadata.items()))
        connection.commit()
    finally:
        connection.close()
    os.replace(temporary, out["canonical"])
    return {
        "canonical": str(out["canonical"]),
        "row_count": total_rows,
        "ticker_count": len(master),
        "signal_date_count": len(signal_dates),
        "available_count": availability_count,
    }


def _coverage_stats(counts: dict[date, tuple[int, int]]) -> dict[str, float | int | None]:
    rates = [available / denominator for available, denominator in counts.values() if denominator]
    values = [available for available, _denominator in counts.values()]
    if not rates:
        return {
            "signal_week_count": 0, "mean_weekly_share": None, "median_weekly_share": None,
            "min_weekly_share": None, "p05_weekly_share": None, "mean_weekly_stock_count": None,
        }
    return {
        "signal_week_count": len(rates),
        "mean_weekly_share": sum(rates) / len(rates),
        "median_weekly_share": _percentile(rates, 0.5),
        "min_weekly_share": min(rates),
        "p05_weekly_share": _percentile(rates, 0.05),
        "mean_weekly_stock_count": sum(values) / len(values),
    }


def _window_for_text(value: str) -> str:
    parsed = date.fromisoformat(value)
    return _window(parsed) or "outside_windows"


def _coverage_audit(root: Path) -> list[dict[str, Any]]:
    path = paths(root)["canonical"]
    rows: list[dict[str, Any]] = []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        for scope, where in (
            ("COMPLETE_GRID_503", "1=1"),
            ("SEC_MAPPED_REFERENCE_500", "sec_mapping_available = 1"),
        ):
            weekly: dict[date, tuple[int, int]] = {}
            query = (
                "SELECT signal_date, COUNT(*) AS denominator_count, SUM(availability_flag) AS available_count "
                f"FROM weekly_estimated_market_cap WHERE {where} GROUP BY signal_date ORDER BY signal_date"
            )
            for item in connection.execute(query):
                day = date.fromisoformat(item["signal_date"])
                available, denominator = int(item["available_count"]), int(item["denominator_count"])
                weekly[day] = (available, denominator)
                rows.append({
                    "row_type": "WEEKLY", "coverage_scope": scope, "dimension": "all", "group": "all",
                    "signal_date": day.isoformat(), "denominator_count": denominator, "available_count": available,
                    "coverage_share": available / denominator if denominator else None,
                    "signal_week_count": "", "mean_weekly_share": "", "median_weekly_share": "",
                    "min_weekly_share": "", "p05_weekly_share": "", "mean_weekly_stock_count": "",
                    "note": "complete-grid availability; no factor values",
                })
            for label, start, end in WINDOWS:
                stats = _coverage_stats({day: value for day, value in weekly.items() if start <= day <= end})
                rows.append({
                    "row_type": "SUMMARY", "coverage_scope": scope, "dimension": "research_window", "group": label,
                    "signal_date": "", "denominator_count": "", "available_count": "", "coverage_share": "",
                    **stats, "note": "weekly coverage summary; no factor values",
                })
            years = sorted({day.year for day in weekly})
            for year in years:
                stats = _coverage_stats({day: value for day, value in weekly.items() if day.year == year})
                rows.append({
                    "row_type": "SUMMARY", "coverage_scope": scope, "dimension": "calendar_year", "group": str(year),
                    "signal_date": "", "denominator_count": "", "available_count": "", "coverage_share": "",
                    **stats, "note": "weekly coverage summary; no factor values",
                })

        for scope, where in (
            ("COMPLETE_GRID_503", "1=1"),
            ("SEC_MAPPED_REFERENCE_500", "sec_mapping_available = 1"),
        ):
            gics_weekly: dict[tuple[str, str], dict[date, tuple[int, int]]] = defaultdict(dict)
            query = (
                "SELECT signal_date, COALESCE(gics_sector_current, 'MISSING') AS gics, "
                "COUNT(*) AS denominator_count, SUM(availability_flag) AS available_count "
                f"FROM weekly_estimated_market_cap WHERE {where} "
                "GROUP BY signal_date, COALESCE(gics_sector_current, 'MISSING')"
            )
            for item in connection.execute(query):
                day = date.fromisoformat(item["signal_date"])
                window = _window(day)
                if window:
                    gics_weekly[(window, item["gics"])][day] = (int(item["available_count"]), int(item["denominator_count"]))
            for (window, gics), counts in sorted(gics_weekly.items()):
                stats = _coverage_stats(counts)
                rows.append({
                    "row_type": "SUMMARY", "coverage_scope": scope, "dimension": "current_gics_sector_dev_only",
                    "group": f"{window}|{gics}", "signal_date": "", "denominator_count": "", "available_count": "",
                    "coverage_share": "", **stats,
                    "note": "Current GICS only; not a historical PIT sector result.",
                })
    return rows


def _reason_audit(root: Path) -> list[dict[str, Any]]:
    path = paths(root)["canonical"]
    result: list[dict[str, Any]] = []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        for scope, where in (
            ("COMPLETE_GRID_503", "1=1"),
            ("SEC_MAPPED_REFERENCE_500", "sec_mapping_available = 1"),
        ):
            grouped: dict[tuple[str, str], int] = Counter()
            totals: Counter[str] = Counter()
            tickers: dict[tuple[str, str], set[str]] = defaultdict(set)
            query = f"SELECT signal_date, ticker, exclusion_reason FROM weekly_estimated_market_cap WHERE {where}"
            for day_text, ticker, reason in connection.execute(query):
                window = _window_for_text(str(day_text))
                grouped[(window, str(reason))] += 1
                totals[window] += 1
                tickers[(window, str(reason))].add(str(ticker))
            all_reasons = tuple(signoff.REQUIRED_REASON_CODES)
            for window in sorted(totals):
                for reason in all_reasons:
                    count = grouped[(window, reason)]
                    result.append({
                        "evidence_stage": EVIDENCE_STAGE, "coverage_scope": scope, "window": window,
                        "exclusion_reason": reason, "ticker_week_count": count,
                        "ticker_count": len(tickers[(window, reason)]),
                        "coverage_share": count / totals[window] if totals[window] else None,
                        "reason_priority": REASON_PRIORITY,
                    })
    return result


def _distribution_stats(values: list[float]) -> dict[str, float | int | None]:
    finite = [value for value in values if math.isfinite(value)]
    return {
        "available_observation_count": len(values),
        "finite_observation_count": len(finite),
        "nonpositive_count": sum(value <= 0 for value in finite),
        "min": min(finite) if finite else None,
        "p01": _percentile(finite, .01),
        "p05": _percentile(finite, .05),
        "median": _percentile(finite, .50),
        "p95": _percentile(finite, .95),
        "p99": _percentile(finite, .99),
        "max": max(finite) if finite else None,
    }


def _distribution_audit(root: Path) -> list[dict[str, Any]]:
    path = paths(root)["canonical"]
    values: dict[str, list[float]] = defaultdict(list)
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        for day_text, value in connection.execute(
            "SELECT signal_date, estimated_market_cap FROM weekly_estimated_market_cap WHERE availability_flag = 1"
        ):
            market_cap = float(value)
            day = date.fromisoformat(str(day_text))
            values["all_signal_dates"].append(market_cap)
            window = _window(day)
            if window:
                values[window].append(market_cap)
            values[f"calendar_year_{day.year}"].append(market_cap)
    return [
        {
            "evidence_stage": EVIDENCE_STAGE, "scope": scope, "currency": "USD", "scale": "1",
            **_distribution_stats(scope_values),
            "note": "Diagnostic only; no clipping or winsorization applied.",
        }
        for scope, scope_values in sorted(values.items())
    ]


def _value_input_coverage(root: Path) -> list[dict[str, Any]]:
    fundamentals = _fundamental_availability(root)
    path = paths(root)["canonical"]
    signal_dates: set[date] = set()
    available: dict[date, set[str]] = defaultdict(set)
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        for day_text, ticker, flag in connection.execute(
            "SELECT signal_date, ticker, availability_flag FROM weekly_estimated_market_cap WHERE sec_mapping_available = 1"
        ):
            day = date.fromisoformat(str(day_text))
            signal_dates.add(day)
            if int(flag):
                available[day].add(str(ticker))
    measures: dict[str, dict[date, int]] = {name: {day: 0 for day in signal_dates} for name in (
        "market_cap_input", "ep_input", "bp_input", "fcf_yield_input", "value_two_of_three",
    )}
    for day, tickers in available.items():
        for ticker in tickers:
            ep = (ticker, day) in fundamentals["net_income"]
            bp = (ticker, day) in fundamentals["common_equity"]
            fcf = (ticker, day) in fundamentals["ocf"] and (ticker, day) in fundamentals["capex"]
            measures["market_cap_input"][day] += 1
            measures["ep_input"][day] += int(ep)
            measures["bp_input"][day] += int(bp)
            measures["fcf_yield_input"][day] += int(fcf)
            measures["value_two_of_three"][day] += int(signoff.value_two_of_three(ep, bp, fcf))
    rows: list[dict[str, Any]] = []
    for measure, counts in measures.items():
        for window, start, end in WINDOWS:
            stats = _coverage_stats({day: (count, 500) for day, count in counts.items() if start <= day <= end})
            rows.append({
                "evidence_stage": EVIDENCE_STAGE, "coverage_scope": "SEC_MAPPED_REFERENCE_500", "measure": measure,
                "window": window, "input_semantics": "availability-only MCap/fundamental join; no ratio or factor value",
                **stats,
            })
    return rows


def _shares_update_audit(root: Path) -> list[dict[str, Any]]:
    path = paths(root)["canonical"]
    rows: list[dict[str, Any]] = []
    previous: dict[str, sqlite3.Row] = {}
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        query = """
            SELECT ticker, issuer, signal_date, reported_shares, shares_accession, shares_filed_date,
                   shares_asof_date, shares_form, shares_amendment_flag, split_adjustment_factor,
                   standard_split_event_count, share_asof_age_days, staleness_flag
            FROM weekly_estimated_market_cap
            WHERE reported_shares IS NOT NULL
            ORDER BY ticker, signal_date
        """
        for item in connection.execute(query):
            ticker = str(item["ticker"])
            prior = previous.get(ticker)
            if prior is not None and item["shares_accession"] != prior["shares_accession"]:
                old = float(prior["reported_shares"])
                new = float(item["reported_shares"])
                split_related = not math.isclose(
                    float(prior["split_adjustment_factor"] or 1.0), float(item["split_adjustment_factor"] or 1.0),
                    rel_tol=0.0, abs_tol=1e-12,
                ) or int(item["standard_split_event_count"]) != int(prior["standard_split_event_count"])
                if int(prior["staleness_flag"]) or int(item["staleness_flag"]):
                    classification = "STALE_SHARES_REPLACEMENT"
                elif split_related:
                    classification = "SPLIT_RELATED_SHARES_UPDATE"
                elif int(item["shares_amendment_flag"] or 0):
                    classification = "AMENDMENT_SHARES_UPDATE"
                else:
                    classification = "SHARES_DISCLOSURE_UPDATE"
                rows.append({
                    "evidence_stage": EVIDENCE_STAGE, "ticker": ticker, "issuer": item["issuer"],
                    "signal_date": item["signal_date"], "previous_reported_shares": old,
                    "new_reported_shares": new, "reported_shares_pct_change": (new / old - 1.0) if old else None,
                    "previous_accession": prior["shares_accession"], "new_accession": item["shares_accession"],
                    "previous_filed_date": prior["shares_filed_date"], "new_filed_date": item["shares_filed_date"],
                    "previous_asof_date": prior["shares_asof_date"], "new_asof_date": item["shares_asof_date"],
                    "new_form": item["shares_form"], "new_amendment_flag": item["shares_amendment_flag"],
                    "previous_split_adjustment_factor": prior["split_adjustment_factor"],
                    "new_split_adjustment_factor": item["split_adjustment_factor"],
                    "split_related": int(split_related), "update_classification": classification,
                })
            previous[ticker] = item
    return rows


def _jump_audit(root: Path, limit: int = 250) -> list[dict[str, Any]]:
    """Return the deterministic largest consecutive-week absolute log changes only."""
    path = paths(root)["canonical"]
    previous_signal: dict[str, str] = {}
    heap: list[tuple[float, int, dict[str, Any]]] = []
    serial = 0
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        dates = [row[0] for row in connection.execute("SELECT DISTINCT signal_date FROM weekly_estimated_market_cap ORDER BY signal_date")]
        previous_signal = {right: left for left, right in zip(dates, dates[1:])}
        query = """
            SELECT ticker, issuer, signal_date, estimated_market_cap, price_close, reported_shares,
                   shares_accession, split_adjustment_factor, standard_split_event_count,
                   share_asof_age_days, staleness_flag, applied_split_events_json
            FROM weekly_estimated_market_cap
            WHERE availability_flag = 1
            ORDER BY ticker, signal_date
        """
        previous_by_ticker: dict[str, sqlite3.Row] = {}
        for item in connection.execute(query):
            ticker = str(item["ticker"])
            prior = previous_by_ticker.get(ticker)
            previous_by_ticker[ticker] = item
            if prior is None or previous_signal.get(item["signal_date"]) != prior["signal_date"]:
                continue
            current_value = float(item["estimated_market_cap"])
            prior_value = float(prior["estimated_market_cap"])
            ratio = current_value / prior_value
            log_change = math.log(ratio)
            price_ratio = float(item["price_close"]) / float(prior["price_close"])
            factor_changed = not math.isclose(
                float(item["split_adjustment_factor"]), float(prior["split_adjustment_factor"]),
                rel_tol=0.0, abs_tol=1e-12,
            )
            shares_changed = item["shares_accession"] != prior["shares_accession"]
            if shares_changed and (int(item["staleness_flag"]) or int(prior["staleness_flag"])):
                classification = "STALE_SHARES_REPLACEMENT"
            elif shares_changed and factor_changed:
                classification = "SPLIT_RELATED_SHARES_UPDATE"
            elif shares_changed:
                classification = "SHARES_UPDATE"
            elif factor_changed:
                classification = "STANDARD_SPLIT_CHAIN_CHANGE"
            else:
                classification = "PRICE_MOVE_OR_UNCLASSIFIED"
            payload = {
                "evidence_stage": EVIDENCE_STAGE, "ticker": ticker, "issuer": item["issuer"],
                "previous_signal_date": prior["signal_date"], "signal_date": item["signal_date"],
                "previous_estimated_market_cap": prior_value, "estimated_market_cap": current_value,
                "market_cap_ratio": ratio, "log_market_cap_change": log_change,
                "absolute_log_market_cap_change": abs(log_change), "price_ratio": price_ratio,
                "previous_reported_shares": prior["reported_shares"], "reported_shares": item["reported_shares"],
                "previous_accession": prior["shares_accession"], "accession": item["shares_accession"],
                "previous_split_adjustment_factor": prior["split_adjustment_factor"],
                "split_adjustment_factor": item["split_adjustment_factor"],
                "applied_split_events_json": item["applied_split_events_json"],
                "jump_classification": classification,
                "note": "Diagnostic ranking only; no jump is auto-corrected.",
            }
            serial += 1
            item_key = (abs(log_change), serial, payload)
            if len(heap) < limit:
                heapq.heappush(heap, item_key)
            elif item_key[0] > heap[0][0]:
                heapq.heapreplace(heap, item_key)
    ordered = sorted(heap, key=lambda item: (-item[0], item[2]["ticker"], item[2]["signal_date"]))
    return [{"rank": rank, **payload} for rank, (_value, _serial, payload) in enumerate(ordered, start=1)]


def _split_validation(root: Path) -> list[dict[str, Any]]:
    """Validate selected real standard split/reverse-split cases over t-5,...,t+5."""
    cases = (
        ("AAPL_2014_7_FOR_1", "AAPL", date(2014, 6, 9), 7.0),
        ("AAPL_2020_4_FOR_1", "AAPL", date(2020, 8, 31), 4.0),
        ("AMZN_2022_20_FOR_1", "AMZN", date(2022, 6, 6), 20.0),
        ("C_2011_1_FOR_10_REVERSE", "C", date(2011, 5, 9), 0.1),
    )
    master, mapped_tickers, _signal_dates, _signal_prices, splits = _read_inputs(root)
    by_ticker = {row["ticker"]: row for row in master}
    event_tickers = {ticker for _name, ticker, _day, _factor in cases}
    daily = _read_daily_event_rows(root, event_tickers)
    ciks = {_as_int(by_ticker[ticker].get("cik")) for ticker in event_tickers}
    raw, eligible = _read_primary_for_ciks(root, {cik for cik in ciks if cik is not None})
    selectors = {
        cik: spec.ShareSlot(sorted(eligible.get(cik, []), key=lambda item: (item.filed_date, item.end_date, item.source_id)))
        for cik in ciks if cik is not None
    }
    results_by_ticker: dict[str, dict[date, dict[str, Any]]] = defaultdict(dict)
    for ticker, price_rows in daily.items():
        security = by_ticker[ticker]
        cik = _as_int(security.get("cik"))
        selector = selectors[cik]
        for price in price_rows:
            day = date.fromisoformat(price["date"])
            selection = selector.advance(day)
            observation = selection.observation
            chain = split_chain(splits.get(ticker, []), observation.end_date) if observation else None
            reason = _primary_reason(
                sec_mapping_available=ticker in mapped_tickers,
                share_status=selection.status,
                raw_records=raw.get(cik, []),
                share_value=observation.value if observation else None,
                is_multiclass=False,
                usable_price=signoff.price_status(price)[1],
                price_code=signoff.price_status(price)[0],
                has_nonstandard_action=bool(chain and chain.nonstandard_events),
            )
            close = _finite(price.get("close"))
            aligned = observation.value * chain.factor if reason == "AVAILABLE" and observation and chain else None
            value = estimated_market_cap(close, aligned) if reason == "AVAILABLE" else None
            results_by_ticker[ticker][day] = {
                "price_close": close, "reported_shares": observation.value if observation else None,
                "split_adjustment_factor": chain.factor if chain else None, "aligned_shares": aligned,
                "estimated_market_cap": value, "availability_flag": int(value is not None),
                "exclusion_reason": "AVAILABLE" if value is not None else reason,
            }
    rows: list[dict[str, Any]] = []
    for case_id, ticker, event_day, split_factor in cases:
        prices = daily.get(ticker, [])
        index = next((idx for idx, row in enumerate(prices) if row["date"] == event_day.isoformat()), None)
        if index is None:
            rows.append({
                "evidence_stage": EVIDENCE_STAGE, "case_id": case_id, "ticker": ticker,
                "split_date": event_day.isoformat(), "split_factor": split_factor, "relative_trading_day": "",
                "date": "", "price_close": "", "reported_shares": "", "split_adjustment_factor": "",
                "aligned_shares": "", "estimated_market_cap": "", "availability_flag": 0,
                "exclusion_reason": "NO_EVENT_DATE_ROW", "market_cap_to_prior_ratio": "", "price_to_prior_ratio": "",
                "nonprice_ratio": "", "mechanical_split_discontinuity": "NOT_EVALUABLE", "case_status": "FAIL",
            })
            continue
        event_rows: list[dict[str, Any]] = []
        for idx in range(max(0, index - 5), min(len(prices), index + 6)):
            day = date.fromisoformat(prices[idx]["date"])
            result = results_by_ticker[ticker][day]
            previous = results_by_ticker[ticker].get(date.fromisoformat(prices[idx - 1]["date"])) if idx else None
            mcap_ratio = (
                result["estimated_market_cap"] / previous["estimated_market_cap"]
                if result["estimated_market_cap"] and previous and previous["estimated_market_cap"] else None
            )
            price_ratio = (
                result["price_close"] / previous["price_close"]
                if result["price_close"] and previous and previous["price_close"] else None
            )
            nonprice_ratio = mcap_ratio / price_ratio if mcap_ratio and price_ratio else None
            mechanical = "NOT_APPLICABLE"
            if idx == index and nonprice_ratio is not None:
                mechanical = "FAIL" if any(math.isclose(nonprice_ratio, candidate, rel_tol=.05) for candidate in (split_factor, 1 / split_factor)) else "PASS"
            event_rows.append({
                "evidence_stage": EVIDENCE_STAGE, "case_id": case_id, "ticker": ticker,
                "split_date": event_day.isoformat(), "split_factor": split_factor, "relative_trading_day": idx - index,
                "date": day.isoformat(), "price_close": result["price_close"], "reported_shares": result["reported_shares"],
                "split_adjustment_factor": result["split_adjustment_factor"], "aligned_shares": result["aligned_shares"],
                "estimated_market_cap": result["estimated_market_cap"], "availability_flag": result["availability_flag"],
                "exclusion_reason": result["exclusion_reason"], "market_cap_to_prior_ratio": mcap_ratio,
                "price_to_prior_ratio": price_ratio, "nonprice_ratio": nonprice_ratio,
                "mechanical_split_discontinuity": mechanical, "case_status": "",
            })
        case_status = "FAIL" if any(row["mechanical_split_discontinuity"] == "FAIL" for row in event_rows) else "PASS"
        rows.extend([{**row, "case_status": case_status} for row in event_rows])
    return rows


def _invariant_checks(root: Path, expected_rows: int) -> dict[str, Any]:
    path = paths(root)["canonical"]
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        scalar = lambda sql: int(connection.execute(sql).fetchone()[0])
        values = {
            "grid_row_count": scalar("SELECT COUNT(*) FROM weekly_estimated_market_cap"),
            "duplicate_key_count": scalar("SELECT COUNT(*) - COUNT(DISTINCT ticker || '|' || signal_date) FROM weekly_estimated_market_cap"),
            "available_pit_violations": scalar("SELECT COUNT(*) FROM weekly_estimated_market_cap WHERE availability_flag = 1 AND shares_filed_date > signal_date"),
            "available_price_date_violations": scalar("SELECT COUNT(*) FROM weekly_estimated_market_cap WHERE availability_flag = 1 AND price_date <> signal_date"),
            "available_invalid_shares": scalar("SELECT COUNT(*) FROM weekly_estimated_market_cap WHERE availability_flag = 1 AND reported_shares <= 1"),
            "available_nonpositive_or_nonfinite_market_cap": scalar("SELECT COUNT(*) FROM weekly_estimated_market_cap WHERE availability_flag = 1 AND (estimated_market_cap <= 0 OR estimated_market_cap != estimated_market_cap)"),
            "available_multiclass": scalar("SELECT COUNT(*) FROM weekly_estimated_market_cap WHERE availability_flag = 1 AND multi_class_flag = 1"),
            "available_nonstandard_action": scalar("SELECT COUNT(*) FROM weekly_estimated_market_cap WHERE availability_flag = 1 AND corporate_action_flag = 1"),
            "available_missing_provenance": scalar("""
                SELECT COUNT(*) FROM weekly_estimated_market_cap
                WHERE availability_flag = 1 AND (
                    price_close IS NULL OR reported_shares IS NULL OR aligned_shares IS NULL OR
                    shares_asof_date IS NULL OR shares_filed_date IS NULL OR shares_accession IS NULL OR
                    shares_source_tag <> 'dei:EntityCommonStockSharesOutstanding' OR shares_unit <> 'shares' OR
                    split_adjustment_factor IS NULL OR market_cap_source <> 'SEC_YAHOO_ESTIMATE_DEV'
                )
            """),
            "unavailable_missing_reason": scalar("SELECT COUNT(*) FROM weekly_estimated_market_cap WHERE availability_flag = 0 AND exclusion_reason IN ('', 'AVAILABLE')"),
            "available_wrong_evidence_stage": scalar("SELECT COUNT(*) FROM weekly_estimated_market_cap WHERE availability_flag = 1 AND evidence_stage <> 'DEV_CURRENT_UNIVERSE'"),
        }
    values["expected_grid_rows"] = expected_rows
    values["grid_row_count_matches"] = values["grid_row_count"] == expected_rows
    values["critical_pass"] = (
        values["grid_row_count_matches"]
        and all(value == 0 for key, value in values.items() if key not in {"grid_row_count", "expected_grid_rows", "grid_row_count_matches", "critical_pass"})
    )
    return values


def _share_scale_diagnostics(root: Path) -> dict[str, Any]:
    """Summarize conspicuously large raw-share observations without altering them.

    ``1e12`` is a report-only inspection threshold, not an eligibility rule,
    conversion factor, or data transformation. The signed construction rule is
    still solely ``reported_shares > 1``. This diagnostic makes it impossible
    to describe an extreme source value as an undetected numerical issue.
    """
    path = paths(root)["canonical"]
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        row = connection.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT ticker), MAX(reported_shares), MAX(estimated_market_cap)
            FROM weekly_estimated_market_cap
            WHERE availability_flag = 1 AND reported_shares > 1000000000000
            """
        ).fetchone()
    return {
        "diagnostic_threshold_reported_shares": 1_000_000_000_000,
        "observation_count": int(row[0]),
        "ticker_count": int(row[1]),
        "max_reported_shares": float(row[2]) if row[2] is not None else None,
        "max_estimated_market_cap": float(row[3]) if row[3] is not None else None,
    }


def _doc_and_report(
    root: Path,
    construction: dict[str, Any],
    coverage: list[dict[str, Any]],
    reasons: list[dict[str, Any]],
    split_rows: list[dict[str, Any]],
    jumps: list[dict[str, Any]],
    updates: list[dict[str, Any]],
    distribution: list[dict[str, Any]],
    value_rows: list[dict[str, Any]],
    checks: dict[str, Any],
    share_scale: dict[str, Any],
) -> str:
    out = paths(root)
    coverage_index = {
        (row["coverage_scope"], row["dimension"], row["group"]): row
        for row in coverage if row["row_type"] == "SUMMARY"
    }
    reason_index = {(row["coverage_scope"], row["window"], row["exclusion_reason"]): row for row in reasons}
    value_index = {(row["measure"], row["window"]): row for row in value_rows}
    distribution_all = next(row for row in distribution if row["scope"] == "all_signal_dates")
    split_cases = {row["case_id"]: row["case_status"] for row in split_rows}
    critical_status = "PASS" if checks["critical_pass"] and all(status == "PASS" for status in split_cases.values()) else "FAIL"
    decision = "PASS WITH WARNINGS" if critical_status == "PASS" else "FAIL"

    def pct(value: Any) -> str:
        return f"{float(value) * 100:.2f}%"

    windows = (
        ("factor_selection_2011_2015", "2011–2015"),
        ("configuration_selection_2016_2020", "2016–2020"),
        ("historical_oos_2021_2026_09", "2021–2026.09"),
    )
    report = [
        "# Estimated Historical Market Cap Construction — DEV Acceptance Report",
        "",
        f"- **Evidence stage:** `{EVIDENCE_STAGE}`",
        f"- **Decision:** {decision}",
        "- **Canonical object:** `data/canonical/us_weekly_estimated_market_cap_dev.sqlite`",
        "- **Scope:** DEV estimated market-cap construction and validation only. No valuation ratio, factor, IC, portfolio, FMOM, or strategy calculation is included.",
        "",
        "## Object contract",
        "",
        "The panel is a complete `ticker × signal_date` grid. `price_close` is exact signal-date Yahoo `close` in USD/share; `reported_shares` and `aligned_shares` are source-unit shares; `estimated_market_cap = price_close × aligned_shares` is USD at scale 1. No thousand-share multiplier, adjusted close, price fill, or staleness exclusion is applied.",
        "",
        f"Grid size is **{construction['row_count']:,}** rows = {construction['ticker_count']} securities × {construction['signal_date_count']} signal dates. Available observations: **{construction['available_count']:,}**. Every row labels source `{MARKET_CAP_SOURCE}` and evidence stage `{EVIDENCE_STAGE}`.",
        "",
        "## Construction invariants",
        "",
        "| Check | Violations | Status |",
        "| --- | ---: | --- |",
    ]
    for label, key in (
        ("complete-grid row count", "grid_row_count"),
        ("duplicate ticker/date key", "duplicate_key_count"),
        ("available shares filed after signal date", "available_pit_violations"),
        ("available price date differs from signal date", "available_price_date_violations"),
        ("available reported shares <= 1", "available_invalid_shares"),
        ("available nonpositive/NaN market cap", "available_nonpositive_or_nonfinite_market_cap"),
        ("available unresolved multi-class", "available_multiclass"),
        ("available non-standard action", "available_nonstandard_action"),
        ("available missing provenance", "available_missing_provenance"),
        ("unavailable missing primary reason", "unavailable_missing_reason"),
    ):
        expected = construction["row_count"] if key == "grid_row_count" else 0
        status = "PASS" if checks[key] == expected else "FAIL"
        report.append(f"| {label} | {checks[key]:,} | {status} |")
    report.extend([
        "",
        "## Weekly coverage",
        "",
        "| Reference denominator | 2011–2015 mean / median / min / p05 | 2016–2020 mean / median / min / p05 | 2021–2026.09 mean / median / min / p05 |",
        "| --- | --- | --- | --- |",
    ])
    for scope, label in (("SEC_MAPPED_REFERENCE_500", "500 SEC-mapped reference"), ("COMPLETE_GRID_503", "503 complete grid")):
        formatted = []
        for window, _window_label in windows:
            item = coverage_index[(scope, "research_window", window)]
            formatted.append(" / ".join(pct(item[key]) for key in ("mean_weekly_share", "median_weekly_share", "min_weekly_share", "p05_weekly_share")))
        report.append(f"| {label} | {formatted[0]} | {formatted[1]} | {formatted[2]} |")
    report.extend([
        "",
        "The SEC-mapped reference should agree with the signed availability audit (approximately 69.74%, 80.28%, and 88.60% mean coverage); the complete-grid denominator also retains the three non-SEC-mapped securities as explicit unavailable rows.",
        "",
        "## Primary missingness",
        "",
        "| Window (500 SEC-mapped reference) | Leading primary reasons |",
        "| --- | --- |",
    ])
    for window, label in windows:
        excluded = [
            reason_index[("SEC_MAPPED_REFERENCE_500", window, reason)]
            for reason in signoff.REQUIRED_REASON_CODES
            if reason != "AVAILABLE"
        ]
        leaders = sorted(excluded, key=lambda item: int(item["ticker_week_count"]), reverse=True)[:3]
        report.append("| %s | %s |" % (label, "; ".join(f"{item['exclusion_reason']} {pct(item['coverage_share'])}" for item in leaders)))
    report.extend([
        "",
        "## Split and numerical diagnostics",
        "",
        "| Representative event | Construction-level status |",
        "| --- | --- |",
    ])
    for case_id in ("AAPL_2014_7_FOR_1", "AAPL_2020_4_FOR_1", "AMZN_2022_20_FOR_1", "C_2011_1_FOR_10_REVERSE"):
        report.append(f"| {case_id} | {split_cases.get(case_id, 'FAIL')} |")
    report.extend([
        "",
        f"Distribution: {distribution_all['available_observation_count']:,} available observations; min={distribution_all['min']:.6g}, p01={distribution_all['p01']:.6g}, median={distribution_all['median']:.6g}, p99={distribution_all['p99']:.6g}, max={distribution_all['max']:.6g}; nonpositive/NaN count={distribution_all['nonpositive_count']}. No clipping or winsorization occurred.",
        f"The jump audit retains the deterministic largest {len(jumps)} consecutive-week absolute log changes; the shares-update audit records {len(updates):,} SEC-observation switches. Both are diagnostic only and no row is silently corrected.",
        f"**DEV warning / downstream blocker:** the report-only extreme-share inspection finds {share_scale['observation_count']:,} available observations across {share_scale['ticker_count']} tickers with raw reported shares above {share_scale['diagnostic_threshold_reported_shares']:.0f}. The largest raw value is {share_scale['max_reported_shares']:.6g}; examples in the audit include raw SEC values approximately one-million times adjacent disclosures. These observations satisfy the signed `> 1` predicate and therefore remain in this canonical object unchanged. They require a separately authorized semantic share-scale rule before this object can support factor construction; they are neither re-scaled nor silently excluded here.",
        "",
        "## Value-input availability recheck (availability only)",
        "",
        "| Window | E/P input | B/P input | FCF Yield input | Value >=2/3 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    for window, label in windows:
        report.append(
            f"| {label} | {pct(value_index[('ep_input', window)]['mean_weekly_share'])} | "
            f"{pct(value_index[('bp_input', window)]['mean_weekly_share'])} | "
            f"{pct(value_index[('fcf_yield_input', window)]['mean_weekly_share'])} | "
            f"{pct(value_index[('value_two_of_three', window)]['mean_weekly_share'])} |"
        )
    report.extend([
        "",
        "This is a post-construction availability cross-check only. The report contains no E/P, B/P, FCF, FCF Yield, Value score, Size factor, neutralization, IC, portfolio, FMOM, or strategy value.",
        "",
        "## Decision labels",
        "",
        "- **DEV blocker (downstream use):** the retained raw SEC share-scale anomaly requires a separately authorized semantic validation/rejection rule before any Value, Size, or size-neutralization consumer may use this object. The blocker does not license an automatic re-scale, deletion, fallback, or threshold rule.",
        "- **DEV warnings (construction scope):** periodic shares, staleness retained without cutoff, price-review rows retained, unresolved non-standard corporate actions, and unresolved multi-class issuers reduce or qualify DEV coverage but did not violate the signed construction invariants.",
        "- **Formal PIT limitations:** current-S&P-500 survivorship bias, current GICS, Yahoo retrospective price/action adjustments, absent historical delisting universe, and no RDQ/formal market-cap vendor source mean this output is not Formal PIT evidence.",
        "",
        "## Downstream interface and boundaries",
        "",
        "Downstream code can read `estimated_market_cap` plus `market_cap_source`, `availability_flag`, and `exclusion_reason` without knowing the SEC/Yahoo implementation. A Formal replacement can preserve this interface while setting `market_cap_source = CRSP_FORMAL` under a separately approved Formal contract.",
        "",
        "DEV warnings remain: current-S&P-500 survivorship bias, current GICS, periodic SEC shares, Yahoo retrospective price/action adjustments, unresolved non-standard corporate actions, unresolved multi-class issuers, missing historical delisting universe, and absent RDQ. This object must be called **DEV estimated market cap**, never Formal PIT market cap.",
    ])
    text = "\n".join(report) + "\n"
    out["acceptance"].write_text(text, encoding="utf-8")
    document = "# Estimated Historical Market Cap — DEV Canonical Contract\n\n" + text + "\n## Audit artefacts\n\n" + "\n".join(
        f"- `{path.name}`" for key, path in out.items() if key not in {"canonical", "document"}
    ) + "\n"
    out["document"].write_text(document, encoding="utf-8")
    return decision


def run(root: Path | None = None) -> dict[str, Any]:
    root = (root or project_root()).resolve()
    construction = construct_panel(root)
    out = paths(root)
    coverage = _coverage_audit(root)
    reasons = _reason_audit(root)
    split_rows = _split_validation(root)
    jumps = _jump_audit(root)
    updates = _shares_update_audit(root)
    distribution = _distribution_audit(root)
    value_rows = _value_input_coverage(root)
    checks = _invariant_checks(root, construction["row_count"])
    share_scale = _share_scale_diagnostics(root)
    _write_csv(out["coverage"], coverage)
    _write_csv(out["reasons"], reasons)
    _write_csv(out["splits"], split_rows)
    _write_csv(out["jumps"], jumps)
    _write_csv(out["updates"], updates)
    _write_csv(out["distribution"], distribution)
    _write_csv(out["value"], value_rows)
    decision = _doc_and_report(root, construction, coverage, reasons, split_rows, jumps, updates, distribution, value_rows, checks, share_scale)
    return {"decision": decision, "construction": construction, "checks": checks, "share_scale_diagnostics": share_scale, "paths": {key: str(value) for key, value in out.items()}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=project_root())
    args = parser.parse_args()
    print(json.dumps(run(args.root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
