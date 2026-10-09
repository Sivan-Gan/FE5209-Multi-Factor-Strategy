"""Build the immutable-source-derived US DEV canonical market-input layer.

This module only transforms the supplied Yahoo delivery stage into reproducible
canonical inputs.  It does not touch ``raw_package`` and does not calculate
factor scores, portfolios, returns, IC, or strategy results.
"""

from __future__ import annotations

import csv
import gzip
import io
import math
from collections import deque
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from src.data import source_audit


EVIDENCE_STAGE = "DEV_CURRENT_UNIVERSE"
MARKET = "US"
BENCHMARK_TICKER = "SPY"
CANONICAL_VERSION = "fe5209.us_market_dev.v1"


MASTER_FIELDS = (
    "security_id",
    "ticker",
    "yahoo_ticker",
    "security_name",
    "sec_name",
    "cik",
    "exchange",
    "security_type",
    "currency",
    "country",
    "universe_definition",
    "sp500_date_added",
    "gics_sector_current",
    "gics_sub_industry_current",
    "first_price_date_source",
    "last_price_date_source",
    "eligible_one_year_by_2026_08_01_source",
    "quality_status",
    "quality_issues",
    "quality_valid_price_rows",
    "quality_internal_missing_trading_dates",
    "quality_missing_core_cells",
    "quality_zero_volume_rows",
    "quality_duplicate_dates",
    "quality_invalid_ohlc_rows",
    "quality_absolute_returns_over_20pct",
    "quality_absolute_returns_over_50pct",
    "source_security_master_path",
    "source_price_quality_path",
    "canonical_version",
    "evidence_stage",
)

DAILY_FIELDS = (
    "market",
    "ticker",
    "yahoo_ticker",
    "date",
    "market_trading_day_index",
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "volume",
    "dividends",
    "stock_splits",
    "source_simple_return",
    "source_log_return",
    "dollar_volume",
    "security_id",
    "cik",
    "exchange",
    "security_type",
    "currency",
    "country",
    "gics_sector_current",
    "gics_sub_industry_current",
    "quality_status",
    "quality_issues",
    "is_benchmark",
    "is_stock_cross_section",
    "is_review_security",
    "is_zero_volume",
    "is_ohlc_plausible",
    "price_history_start_date",
    "has_24m_price_history",
    "trailing_60_expected_market_days",
    "trailing_60_observed_price_days",
    "trailing_60_positive_volume_days",
    "trailing_60_trading_coverage",
    "has_80pct_trading_coverage",
    "base_price_eligibility",
    "base_price_eligibility_reason",
    "raw_source_price_path",
    "canonical_version",
    "evidence_stage",
)

CALENDAR_FIELDS = (
    "market",
    "week_identifier",
    "signal_date",
    "signal_market_trading_day_index",
    "previous_signal_date",
    "next_trading_date",
    "days_since_previous_signal",
    "days_to_next_trading_date",
    "calendar_gap_status",
    "benchmark_ticker",
    "benchmark_available_on_signal_date",
    "next_trading_day_available",
    "calendar_source",
    "canonical_version",
    "evidence_stage",
)


def project_root() -> Path:
    """Return the repository root without relying on the process CWD."""

    return Path(__file__).resolve().parents[2]


def canonical_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / "data" / "canonical"


def canonical_paths(root: Path | None = None) -> dict[str, Path]:
    directory = canonical_dir(root)
    return {
        "master": directory / "us_security_master_dev.csv",
        "daily": directory / "us_daily_market_dev.csv.gz",
        "calendar": directory / "us_weekly_signal_calendar_dev.csv",
    }


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _float(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    try:
        result = float(value)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def _as_bool(value: str | bool) -> bool:
    return str(value).strip().lower() == "true"


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _add_months(value: date, months: int) -> date:
    """Add calendar months with month-end clamping for the 24-month rule."""

    target = value.month - 1 + months
    year = value.year + target // 12
    month = target % 12 + 1
    month_lengths = (31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                     31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    return date(year, month, min(value.day, month_lengths[month - 1]))


def _write_csv(path: Path, fields: Iterable[str], rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(fields), extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            count += 1
    return count


def _gzip_text_writer(path: Path) -> tuple[gzip.GzipFile, io.TextIOWrapper]:
    """Open a deterministic gzip CSV writer (mtime fixed at zero)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    binary = gzip.GzipFile(filename="", mode="wb", fileobj=path.open("wb"), mtime=0)
    return binary, io.TextIOWrapper(binary, encoding="utf-8", newline="")


def read_security_master(package: Path) -> tuple[list[dict[str, str]], dict[str, dict[str, str]]]:
    """Read the delivered master and QA table into one canonical identity map."""

    _, master_rows = source_audit._read_csv_rows(package / source_audit.SECURITY_MASTER_FILE)
    _, quality_rows = source_audit._read_csv_rows(package / source_audit.PRICE_QUALITY_FILE)
    quality_by_yahoo = {row["ticker"]: row for row in quality_rows}
    canonical_rows: list[dict[str, str]] = []
    by_yahoo: dict[str, dict[str, str]] = {}
    for source in master_rows:
        yahoo_ticker = source["yahoo_ticker"]
        quality = quality_by_yahoo.get(yahoo_ticker, {})
        row = {
            "security_id": source["security_id"],
            "ticker": source["ticker"],
            "yahoo_ticker": yahoo_ticker,
            "security_name": source["security_name"],
            "sec_name": source["sec_name"],
            "cik": source["cik"],
            "exchange": source["exchange"],
            "security_type": source["security_type"],
            "currency": source["currency"],
            "country": source["country"],
            "universe_definition": source["universe_definition"],
            "sp500_date_added": source["sp500_date_added"],
            "gics_sector_current": source["gics_sector"],
            "gics_sub_industry_current": source["gics_sub_industry"],
            "first_price_date_source": source["first_price_date"],
            "last_price_date_source": source["last_price_date"],
            "eligible_one_year_by_2026_08_01_source": source["eligible_one_year_by_2026_08_01"],
            "quality_status": source["quality_status"],
            "quality_issues": source["quality_issues"],
            "quality_valid_price_rows": quality.get("valid_price_rows", ""),
            "quality_internal_missing_trading_dates": quality.get("internal_missing_trading_dates", ""),
            "quality_missing_core_cells": quality.get("missing_core_cells", ""),
            "quality_zero_volume_rows": quality.get("zero_volume_rows", ""),
            "quality_duplicate_dates": quality.get("duplicate_dates", ""),
            "quality_invalid_ohlc_rows": quality.get("invalid_ohlc_rows", ""),
            "quality_absolute_returns_over_20pct": quality.get("absolute_returns_over_20pct", ""),
            "quality_absolute_returns_over_50pct": quality.get("absolute_returns_over_50pct", ""),
            "source_security_master_path": source_audit.SECURITY_MASTER_FILE.as_posix(),
            "source_price_quality_path": source_audit.PRICE_QUALITY_FILE.as_posix(),
            "canonical_version": CANONICAL_VERSION,
            "evidence_stage": EVIDENCE_STAGE,
        }
        if yahoo_ticker in by_yahoo:
            raise ValueError(f"Duplicate Yahoo ticker in security master: {yahoo_ticker}")
        by_yahoo[yahoo_ticker] = row
        canonical_rows.append(row)
    if len(quality_by_yahoo) != len(canonical_rows):
        missing = sorted(set(by_yahoo) - set(quality_by_yahoo))
        extra = sorted(set(quality_by_yahoo) - set(by_yahoo))
        if missing or extra:
            raise ValueError(f"Master/quality ticker mismatch: missing={missing}, extra={extra}")
    return sorted(canonical_rows, key=lambda row: row["ticker"]), by_yahoo


def load_benchmark_dates(package: Path) -> list[date]:
    """Read actual SPY dates from the source table for the US market calendar."""

    dates: list[date] = []
    with (package / source_audit.PRICE_FILE).open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row["ticker"] == BENCHMARK_TICKER:
                dates.append(_parse_date(row["date"]))
    if not dates:
        raise ValueError("SPY benchmark rows are absent from delivered price data")
    if any(right <= left for left, right in zip(dates, dates[1:])):
        raise ValueError("SPY benchmark dates are not strictly increasing")
    return dates


def build_weekly_calendar_rows(benchmark_dates: list[date]) -> list[dict[str, str]]:
    """Use the final actual SPY trading date in each ISO calendar week as signal date."""

    last_by_week: dict[tuple[int, int], tuple[int, date]] = {}
    for index, trading_date in enumerate(benchmark_dates):
        iso = trading_date.isocalendar()
        last_by_week[(iso.year, iso.week)] = (index, trading_date)
    weekly = [last_by_week[key] for key in sorted(last_by_week)]
    rows: list[dict[str, str]] = []
    for position, (market_index, signal_date) in enumerate(weekly):
        previous = weekly[position - 1][1] if position else None
        next_date = benchmark_dates[market_index + 1] if market_index + 1 < len(benchmark_dates) else None
        gap = (signal_date - previous).days if previous else None
        to_next = (next_date - signal_date).days if next_date else None
        rows.append(
            {
                "market": MARKET,
                "week_identifier": f"{signal_date.isocalendar().year}-W{signal_date.isocalendar().week:02d}",
                "signal_date": signal_date.isoformat(),
                "signal_market_trading_day_index": str(market_index),
                "previous_signal_date": previous.isoformat() if previous else "",
                "next_trading_date": next_date.isoformat() if next_date else "",
                "days_since_previous_signal": str(gap) if gap is not None else "",
                "days_to_next_trading_date": str(to_next) if to_next is not None else "",
                "calendar_gap_status": (
                    "first_signal" if previous is None else "extended_gap" if gap > 7 else "normal"
                ),
                "benchmark_ticker": BENCHMARK_TICKER,
                "benchmark_available_on_signal_date": "true",
                "next_trading_day_available": _bool_text(next_date is not None),
                "calendar_source": "SPY actual delivered trading dates; ISO Monday-Sunday natural week",
                "canonical_version": CANONICAL_VERSION,
                "evidence_stage": EVIDENCE_STAGE,
            }
        )
    return rows


def _ohlc_plausible(row: dict[str, str]) -> bool:
    open_price = _float(row["open"])
    high = _float(row["high"])
    low = _float(row["low"])
    close = _float(row["close"])
    if None in (open_price, high, low, close):
        return False
    return low <= min(open_price, close) and high >= max(open_price, close) and low <= high


def _base_eligibility_reason(has_history: bool, has_coverage: bool) -> str:
    reasons: list[str] = []
    if not has_history:
        reasons.append("insufficient_24m_price_history")
    if not has_coverage:
        reasons.append("insufficient_60d_trading_coverage")
    return ";".join(reasons)


def write_canonical_daily(
    package: Path,
    master_by_yahoo: dict[str, dict[str, str]],
    benchmark_dates: list[date],
    output_path: Path,
) -> dict[str, Any]:
    """Stream source prices into canonical daily data, retaining all source rows."""

    market_index = {trading_date: index for index, trading_date in enumerate(benchmark_dates)}
    row_count = 0
    benchmark_rows = 0
    stock_rows = 0
    unmapped_tickers: set[str] = set()
    duplicate_keys = 0
    last_key: tuple[str, str] | None = None
    current_yahoo: str | None = None
    ticker_first_date: date | None = None
    trailing: deque[tuple[int, bool]] = deque()
    positive_volume_count = 0
    text_binary, text_handle = _gzip_text_writer(output_path)
    try:
        writer = csv.DictWriter(text_handle, fieldnames=DAILY_FIELDS, extrasaction="raise")
        writer.writeheader()
        with (package / source_audit.PRICE_FILE).open("r", newline="", encoding="utf-8-sig") as handle:
            for source in csv.DictReader(handle):
                yahoo_ticker = source["ticker"]
                trading_date = _parse_date(source["date"])
                is_benchmark = yahoo_ticker == BENCHMARK_TICKER
                master = master_by_yahoo.get(yahoo_ticker)
                if not is_benchmark and master is None:
                    unmapped_tickers.add(yahoo_ticker)
                    continue
                if current_yahoo != yahoo_ticker:
                    current_yahoo = yahoo_ticker
                    ticker_first_date = trading_date
                    trailing.clear()
                    positive_volume_count = 0
                if ticker_first_date is None:
                    raise AssertionError("Ticker start date is unexpectedly absent")
                day_index = market_index.get(trading_date)
                if day_index is None:
                    raise ValueError(f"Source price date absent from benchmark calendar: {yahoo_ticker} {trading_date}")
                while trailing and trailing[0][0] < day_index - 59:
                    _, old_positive = trailing.popleft()
                    if old_positive:
                        positive_volume_count -= 1
                volume = _float(source["volume"])
                positive_volume = volume is not None and volume > 0
                trailing.append((day_index, positive_volume))
                if positive_volume:
                    positive_volume_count += 1
                expected_days = min(60, day_index + 1)
                observed_days = len(trailing)
                coverage = positive_volume_count / expected_days if expected_days else 0.0
                has_history = trading_date >= _add_months(ticker_first_date, 24)
                has_coverage = expected_days == 60 and positive_volume_count >= 48
                base_reason = _base_eligibility_reason(has_history, has_coverage)
                if is_benchmark:
                    identity = {
                        "security_id": "BENCHMARK:SPY",
                        "ticker": BENCHMARK_TICKER,
                        "cik": "",
                        "exchange": "",
                        "security_type": "Benchmark ETF",
                        "currency": "USD",
                        "country": "United States",
                        "gics_sector_current": "",
                        "gics_sub_industry_current": "",
                        "quality_status": "benchmark_not_in_security_master",
                        "quality_issues": "",
                    }
                    benchmark_rows += 1
                else:
                    identity = master
                    stock_rows += 1
                key = (identity["ticker"], source["date"])
                if key == last_key:
                    duplicate_keys += 1
                last_key = key
                row = {
                    "market": MARKET,
                    "ticker": identity["ticker"],
                    "yahoo_ticker": yahoo_ticker,
                    "date": source["date"],
                    "market_trading_day_index": str(day_index),
                    "open": source["open"],
                    "high": source["high"],
                    "low": source["low"],
                    "close": source["close"],
                    "adj_close": source["adj_close"],
                    "volume": source["volume"],
                    "dividends": source["dividends"],
                    "stock_splits": source["stock_splits"],
                    "source_simple_return": source["simple_return"],
                    "source_log_return": source["log_return"],
                    "dollar_volume": source["dollar_volume"],
                    "security_id": identity["security_id"],
                    "cik": identity["cik"],
                    "exchange": identity["exchange"],
                    "security_type": identity["security_type"],
                    "currency": identity["currency"],
                    "country": identity["country"],
                    "gics_sector_current": identity["gics_sector_current"],
                    "gics_sub_industry_current": identity["gics_sub_industry_current"],
                    "quality_status": identity["quality_status"],
                    "quality_issues": identity["quality_issues"],
                    "is_benchmark": _bool_text(is_benchmark),
                    "is_stock_cross_section": _bool_text(not is_benchmark),
                    "is_review_security": _bool_text(identity["quality_status"] == "review"),
                    "is_zero_volume": _bool_text(not positive_volume),
                    "is_ohlc_plausible": _bool_text(_ohlc_plausible(source)),
                    "price_history_start_date": ticker_first_date.isoformat(),
                    "has_24m_price_history": _bool_text(has_history),
                    "trailing_60_expected_market_days": str(expected_days),
                    "trailing_60_observed_price_days": str(observed_days),
                    "trailing_60_positive_volume_days": str(positive_volume_count),
                    "trailing_60_trading_coverage": f"{coverage:.10f}",
                    "has_80pct_trading_coverage": _bool_text(has_coverage),
                    "base_price_eligibility": _bool_text(has_history and has_coverage),
                    "base_price_eligibility_reason": base_reason,
                    "raw_source_price_path": source_audit.PRICE_FILE.as_posix(),
                    "canonical_version": CANONICAL_VERSION,
                    "evidence_stage": EVIDENCE_STAGE,
                }
                writer.writerow(row)
                row_count += 1
    finally:
        text_handle.flush()
        text_handle.detach()
        text_binary.close()
    if unmapped_tickers:
        raise ValueError(f"Unexpected price tickers not in security master: {sorted(unmapped_tickers)}")
    return {
        "row_count": row_count,
        "stock_row_count": stock_rows,
        "benchmark_row_count": benchmark_rows,
        "duplicate_canonical_ticker_date_rows": duplicate_keys,
        "unmapped_price_tickers": sorted(unmapped_tickers),
        "path": str(output_path),
    }


def build_canonical_market_inputs(root: Path | None = None) -> dict[str, Any]:
    """Write the three DEV canonical market objects from immutable raw inputs."""

    selected_root = root or project_root()
    package = source_audit.package_root(selected_root)
    paths = canonical_paths(selected_root)
    master_rows, master_by_yahoo = read_security_master(package)
    master_count = _write_csv(paths["master"], MASTER_FIELDS, master_rows)
    benchmark_dates = load_benchmark_dates(package)
    calendar_rows = build_weekly_calendar_rows(benchmark_dates)
    calendar_count = _write_csv(paths["calendar"], CALENDAR_FIELDS, calendar_rows)
    daily = write_canonical_daily(package, master_by_yahoo, benchmark_dates, paths["daily"])
    return {
        "canonical_version": CANONICAL_VERSION,
        "evidence_stage": EVIDENCE_STAGE,
        "master_rows": master_count,
        "calendar_rows": calendar_count,
        "benchmark_first_date": benchmark_dates[0].isoformat(),
        "benchmark_last_date": benchmark_dates[-1].isoformat(),
        "benchmark_trading_day_count": len(benchmark_dates),
        "daily": daily,
        "paths": {name: str(path) for name, path in paths.items()},
    }

