"""Read-only Gate 0 audit for the delivered US DEV source package.

This module intentionally performs no factor construction, return calculation,
portfolio construction, or strategy/backtest logic. It profiles the immutable
source package and writes reproducible audit artifacts only under
``outputs/audit``.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable


EVIDENCE_STAGE = "DEV_CURRENT_UNIVERSE"
PACKAGE_NAME = "US_source_data_2026-10-08"
PACKAGE_RELATIVE_PATH = Path("raw_package") / PACKAGE_NAME
PACKAGE_MANIFEST = "PACKAGE_SHA256.csv"
PRICE_FILE = Path("yahoo_download_stage/sp500_daily_prices.csv")
SECURITY_MASTER_FILE = Path("yahoo_download_stage/sp500_security_master.csv")
PRICE_QUALITY_FILE = Path("yahoo_download_stage/sp500_data_quality.csv")
YAHOO_MANIFEST_FILE = Path("yahoo_download_stage/manifest.json")
SEC_CIK_MAP_FILE = Path("sec_api_json/security_cik_map.csv")
SEC_STATUS_FILE = Path("sec_api_json/download_status.csv")
SEC_MANIFEST_FILE = Path("sec_api_json/manifest.json")
SEC_FACTS_DIR = Path("sec_api_json/raw_companyfacts")
READ_FIRST_FILE = "00_READ_FIRST.md"

PRICE_REQUIRED_COLUMNS = (
    "date",
    "ticker",
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "volume",
    "dividends",
    "stock_splits",
    "simple_return",
    "log_return",
    "dollar_volume",
)
SECURITY_MASTER_REQUIRED_COLUMNS = (
    "security_id",
    "ticker",
    "security_name",
    "gics_sector",
    "gics_sub_industry",
    "cik",
    "yahoo_ticker",
    "security_type",
    "currency",
    "country",
    "universe_definition",
    "exchange",
)
SEC_MAP_REQUIRED_COLUMNS = (
    "cik",
    "ticker",
    "yahoo_ticker",
    "security_name",
    "gics_sector",
    "gics_sub_industry",
)
SEC_OBSERVATION_FIELDS = ("end", "filed", "form", "accn")
CIK_FILENAME = re.compile(r"^CIK(?P<cik>\d{10})\.json\.gz$")


def project_root() -> Path:
    """Return the repository root for this module's checked-in location."""

    return Path(__file__).resolve().parents[2]


def package_root(root: Path | None = None) -> Path:
    """Return the immutable package path without modifying it."""

    return (root or project_root()) / PACKAGE_RELATIVE_PATH


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    with path.open("rt", encoding="utf-8") as handle:
        return json.load(handle)


def _load_gzip_json(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _read_csv_rows(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    with path.open("rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return tuple(reader.fieldnames or ()), list(reader)


def _as_int(value: str | int | None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _as_float(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    try:
        result = float(value)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def _examples(values: Iterable[Any], limit: int = 20) -> list[Any]:
    result: list[Any] = []
    for value in values:
        result.append(value)
        if len(result) >= limit:
            break
    return result


def _file_purpose(relative_path: str) -> str:
    if relative_path == READ_FIRST_FILE:
        return "Package scope, provenance, and usage instructions"
    if relative_path == PACKAGE_MANIFEST:
        return "Delivered-file SHA-256 integrity manifest"
    if relative_path == "QA_SUMMARY.json":
        return "Supplier offline QA summary"
    if relative_path.startswith("scripts/"):
        return "Delivery-stage provenance script; not executed by this audit"
    if relative_path.startswith("sec_api_json/raw_companyfacts/"):
        return "Issuer-level SEC EDGAR Company Facts archive"
    if relative_path == SEC_CIK_MAP_FILE.as_posix():
        return "Current security-to-SEC-CIK mapping"
    if relative_path == SEC_STATUS_FILE.as_posix():
        return "SEC download status by issuer"
    if relative_path == SEC_MANIFEST_FILE.as_posix():
        return "SEC download-stage metadata and limitations"
    if relative_path == PRICE_FILE.as_posix():
        return "Daily Yahoo/yfinance price and corporate-action delivery stage"
    if relative_path == SECURITY_MASTER_FILE.as_posix():
        return "Download-date current S&P 500 security reference"
    if relative_path == PRICE_QUALITY_FILE.as_posix():
        return "Per-ticker delivered price-quality diagnostics"
    if relative_path == YAHOO_MANIFEST_FILE.as_posix():
        return "Yahoo download-stage metadata and limitations"
    return "Package file"


def load_manifest(package: Path) -> list[dict[str, str]]:
    """Load the delivery manifest without trusting path values outside package."""

    manifest_path = package / PACKAGE_MANIFEST
    fields, rows = _read_csv_rows(manifest_path)
    required = ("archive_path", "uncompressed_bytes", "sha256", "original_project_path")
    missing = sorted(set(required) - set(fields))
    if missing:
        raise ValueError(f"Integrity manifest is missing columns: {missing}")
    for row in rows:
        relative = Path(row["archive_path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe package manifest path: {row['archive_path']}")
    return rows


def audit_manifest(package: Path) -> dict[str, Any]:
    """Verify every delivered payload file and report file-set mismatches."""

    manifest_rows = load_manifest(package)
    declared = {row["archive_path"] for row in manifest_rows}
    actual = {
        path.relative_to(package).as_posix()
        for path in package.rglob("*")
        if path.is_file() and path.name != PACKAGE_MANIFEST
    }
    missing_files = sorted(declared - actual)
    unexpected_files = sorted(actual - declared)
    hash_failures: list[dict[str, str]] = []
    byte_size_failures: list[dict[str, Any]] = []
    verified = 0
    for row in manifest_rows:
        path = package / row["archive_path"]
        if not path.is_file():
            continue
        expected_bytes = _as_int(row["uncompressed_bytes"])
        actual_bytes = path.stat().st_size
        if expected_bytes is not None and actual_bytes != expected_bytes:
            byte_size_failures.append(
                {
                    "path": row["archive_path"],
                    "expected_bytes": expected_bytes,
                    "actual_bytes": actual_bytes,
                }
            )
        actual_hash = _sha256(path)
        if actual_hash != row["sha256"]:
            hash_failures.append(
                {
                    "path": row["archive_path"],
                    "expected_sha256": row["sha256"],
                    "actual_sha256": actual_hash,
                }
            )
        verified += 1
    return {
        "manifest_path": PACKAGE_MANIFEST,
        "manifest_sha256": _sha256(package / PACKAGE_MANIFEST),
        "declared_payload_files": len(manifest_rows),
        "actual_payload_files": len(actual),
        "verified_payload_files": verified,
        "missing_files": missing_files,
        "unexpected_files": unexpected_files,
        "byte_size_failures": byte_size_failures,
        "hash_failures": hash_failures,
        "passed": not (missing_files or unexpected_files or byte_size_failures or hash_failures),
    }


def build_file_inventory(package: Path, manifest_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Create a row for every actual package file, including the manifest itself."""

    expected_hash = {row["archive_path"]: row["sha256"] for row in manifest_rows}
    expected_size = {row["archive_path"]: _as_int(row["uncompressed_bytes"]) for row in manifest_rows}
    rows: list[dict[str, Any]] = []
    for path in sorted((item for item in package.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        relative = path.relative_to(package).as_posix()
        rows.append(
            {
                "relative_path": relative,
                "suffix": path.suffix.lower(),
                "bytes": path.stat().st_size,
                "listed_in_payload_manifest": relative in expected_hash,
                "manifest_sha256": expected_hash.get(relative, ""),
                "manifest_bytes": expected_size.get(relative, ""),
                "purpose": _file_purpose(relative),
            }
        )
    return rows


def audit_price_file(path: Path) -> dict[str, Any]:
    """Stream the delivered price CSV to validate schema, keys, and basic fields."""

    with path.open("rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = tuple(reader.fieldnames or ())
        missing_columns = sorted(set(PRICE_REQUIRED_COLUMNS) - set(fields))
        unexpected_columns = sorted(set(fields) - set(PRICE_REQUIRED_COLUMNS))
        row_count = 0
        tickers: set[str] = set()
        min_date: date | None = None
        max_date: date | None = None
        invalid_date_rows = 0
        blank_ticker_rows = 0
        whitespace_ticker_rows = 0
        missing_core_cells: Counter[str] = Counter()
        nonnumeric_cells: Counter[str] = Counter()
        negative_value_rows: Counter[str] = Counter()
        invalid_ohlc_rows = 0
        zero_volume_rows = 0
        positive_dividend_rows = 0
        nonzero_split_rows = 0
        adj_close_differs_rows = 0
        previous_key: tuple[str, date] | None = None
        sort_or_key_violations = 0
        adjacent_duplicate_keys = 0
        key_examples: list[dict[str, str]] = []
        date_examples: list[dict[str, str]] = []
        ohlc_examples: list[dict[str, str]] = []

        numeric_columns = (
            "open",
            "high",
            "low",
            "close",
            "adj_close",
            "volume",
            "dividends",
            "stock_splits",
        )
        for row in reader:
            row_count += 1
            ticker = (row.get("ticker") or "").strip()
            date_text = (row.get("date") or "").strip()
            if not ticker:
                blank_ticker_rows += 1
            elif ticker != row.get("ticker", ""):
                whitespace_ticker_rows += 1
            try:
                observation_date = date.fromisoformat(date_text)
            except ValueError:
                invalid_date_rows += 1
                if len(date_examples) < 20:
                    date_examples.append({"row": str(row_count), "date": date_text, "ticker": ticker})
                observation_date = None
            if observation_date is not None:
                min_date = observation_date if min_date is None else min(min_date, observation_date)
                max_date = observation_date if max_date is None else max(max_date, observation_date)
                key = (ticker, observation_date)
                if previous_key is not None and key <= previous_key:
                    sort_or_key_violations += 1
                    if key == previous_key:
                        adjacent_duplicate_keys += 1
                    if len(key_examples) < 20:
                        key_examples.append(
                            {
                                "row": str(row_count),
                                "previous_key": f"{previous_key[0]}|{previous_key[1].isoformat()}",
                                "key": f"{ticker}|{observation_date.isoformat()}",
                            }
                        )
                previous_key = key
            if ticker:
                tickers.add(ticker)

            numeric: dict[str, float | None] = {}
            for column in numeric_columns:
                raw_value = row.get(column)
                value = _as_float(raw_value)
                numeric[column] = value
                if raw_value is None or raw_value.strip() == "":
                    missing_core_cells[column] += 1
                elif value is None:
                    nonnumeric_cells[column] += 1
            if all(numeric[column] is not None for column in ("open", "high", "low", "close")):
                if numeric["low"] > min(numeric["open"], numeric["close"]) or numeric["high"] < max(numeric["open"], numeric["close"]):
                    invalid_ohlc_rows += 1
                    if len(ohlc_examples) < 20:
                        ohlc_examples.append(
                            {
                                "row": str(row_count),
                                "ticker": ticker,
                                "date": date_text,
                                "open": str(numeric["open"]),
                                "high": str(numeric["high"]),
                                "low": str(numeric["low"]),
                                "close": str(numeric["close"]),
                            }
                        )
            for column in ("open", "high", "low", "close", "adj_close", "volume", "dividends", "stock_splits"):
                if numeric[column] is not None and numeric[column] < 0:
                    negative_value_rows[column] += 1
            if numeric["volume"] == 0:
                zero_volume_rows += 1
            if numeric["dividends"] is not None and numeric["dividends"] > 0:
                positive_dividend_rows += 1
            if numeric["stock_splits"] is not None and numeric["stock_splits"] != 0:
                nonzero_split_rows += 1
            if (
                numeric["close"] is not None
                and numeric["adj_close"] is not None
                and not math.isclose(numeric["close"], numeric["adj_close"], rel_tol=1e-12, abs_tol=1e-12)
            ):
                adj_close_differs_rows += 1

    strict_key_order = sort_or_key_violations == 0
    return {
        "path": PRICE_FILE.as_posix(),
        "grain": "daily observation per delivered ticker",
        "candidate_primary_key": ["ticker", "date"],
        "fields": list(fields),
        "missing_required_columns": missing_columns,
        "unexpected_columns": unexpected_columns,
        "row_count": row_count,
        "ticker_count": len(tickers),
        "ticker_examples": _examples(sorted(tickers)),
        "benchmark_ticker": "SPY",
        "benchmark_present": "SPY" in tickers,
        "equity_ticker_count_excluding_spy": len(tickers - {"SPY"}),
        "min_date": min_date.isoformat() if min_date else None,
        "max_date": max_date.isoformat() if max_date else None,
        "invalid_date_rows": invalid_date_rows,
        "blank_ticker_rows": blank_ticker_rows,
        "whitespace_ticker_rows": whitespace_ticker_rows,
        "missing_core_cells": dict(sorted(missing_core_cells.items())),
        "nonnumeric_cells": dict(sorted(nonnumeric_cells.items())),
        "negative_value_rows": dict(sorted(negative_value_rows.items())),
        "invalid_ohlc_rows": invalid_ohlc_rows,
        "zero_volume_rows": zero_volume_rows,
        "positive_dividend_rows": positive_dividend_rows,
        "nonzero_split_rows": nonzero_split_rows,
        "adj_close_differs_rows": adj_close_differs_rows,
        "strictly_sorted_by_candidate_key": strict_key_order,
        "adjacent_duplicate_keys": adjacent_duplicate_keys,
        "sort_or_key_violations": sort_or_key_violations,
        "key_uniqueness_proven_by_strict_order": strict_key_order,
        "key_violation_examples": key_examples,
        "invalid_date_examples": date_examples,
        "invalid_ohlc_examples": ohlc_examples,
    }


def audit_identity_layer(package: Path, price_tickers: set[str] | None = None) -> dict[str, Any]:
    """Profile current security, CIK, exchange, GICS, and delivered price mappings."""

    master_fields, master_rows = _read_csv_rows(package / SECURITY_MASTER_FILE)
    map_fields, map_rows = _read_csv_rows(package / SEC_CIK_MAP_FILE)
    quality_fields, quality_rows = _read_csv_rows(package / PRICE_QUALITY_FILE)
    status_fields, status_rows = _read_csv_rows(package / SEC_STATUS_FILE)

    master_tickers = {row["ticker"].strip() for row in master_rows if row.get("ticker", "").strip()}
    master_pair_rows = [
        (row["ticker"].strip(), _as_int(row.get("cik")))
        for row in master_rows
        if row.get("ticker", "").strip() and _as_int(row.get("cik")) is not None
    ]
    master_pairs = set(master_pair_rows)
    master_ciks = {cik for _, cik in master_pairs}
    master_ids = [row.get("security_id", "").strip() for row in master_rows]
    master_id_duplicates = sorted(key for key, count in Counter(master_ids).items() if key and count > 1)
    master_pair_duplicates = sorted(
        f"{ticker}|{cik}" for (ticker, cik), count in Counter(master_pair_rows).items() if count > 1
    )
    ticker_to_ciks: dict[str, set[int]] = defaultdict(set)
    cik_to_tickers: dict[int, set[str]] = defaultdict(set)
    for ticker, cik in master_pairs:
        ticker_to_ciks[ticker].add(cik)
        cik_to_tickers[cik].add(ticker)

    map_pair_rows = [
        (row["ticker"].strip(), _as_int(row.get("cik")))
        for row in map_rows
        if row.get("ticker", "").strip() and _as_int(row.get("cik")) is not None
    ]
    map_pairs = set(map_pair_rows)
    map_ciks = {cik for _, cik in map_pairs}
    map_pair_duplicates = sorted(
        f"{ticker}|{cik}" for (ticker, cik), count in Counter(map_pair_rows).items() if count > 1
    )
    quality_tickers = {row["ticker"].strip() for row in quality_rows if row.get("ticker", "").strip()}
    status_ciks = {_as_int(row.get("cik")) for row in status_rows if _as_int(row.get("cik")) is not None}
    price_tickers = price_tickers or set()
    quality_statuses = Counter(row.get("quality_status", "") for row in quality_rows)
    status_values = Counter(row.get("status", "") for row in status_rows)
    review_rows = [row for row in quality_rows if row.get("quality_status") == "review"]
    quality_missing_date_total = sum(_as_int(row.get("internal_missing_trading_dates")) or 0 for row in quality_rows)
    quality_duplicate_date_total = sum(_as_int(row.get("duplicate_dates")) or 0 for row in quality_rows)
    quality_missing_core_total = sum(_as_int(row.get("missing_core_cells")) or 0 for row in quality_rows)
    quality_invalid_ohlc_total = sum(_as_int(row.get("invalid_ohlc_rows")) or 0 for row in quality_rows)
    quality_zero_volume_total = sum(_as_int(row.get("zero_volume_rows")) or 0 for row in quality_rows)

    master_yahoo_tickers = {
        row["yahoo_ticker"].strip() for row in master_rows if row.get("yahoo_ticker", "").strip()
    }
    canonical_to_yahoo = {
        row["ticker"].strip(): row["yahoo_ticker"].strip()
        for row in master_rows
        if row.get("ticker", "").strip() and row.get("yahoo_ticker", "").strip()
    }
    translated_tickers = {
        ticker: yahoo_ticker for ticker, yahoo_ticker in sorted(canonical_to_yahoo.items()) if ticker != yahoo_ticker
    }
    price_not_master = sorted(price_tickers - master_yahoo_tickers)
    master_not_price = sorted(master_yahoo_tickers - price_tickers)
    expected_price_only = {"SPY"}
    return {
        "security_master": {
            "path": SECURITY_MASTER_FILE.as_posix(),
            "grain": "download-date current S&P 500 constituent security",
            "candidate_primary_key": ["security_id"],
            "fields": list(master_fields),
            "missing_required_columns": sorted(set(SECURITY_MASTER_REQUIRED_COLUMNS) - set(master_fields)),
            "row_count": len(master_rows),
            "ticker_count": len(master_tickers),
            "cik_count": len(master_ciks),
            "duplicate_security_ids": master_id_duplicates,
            "duplicate_ticker_cik_pairs": master_pair_duplicates,
            "missing_gics_sector_rows": sum(not row.get("gics_sector", "").strip() for row in master_rows),
            "missing_gics_sub_industry_rows": sum(not row.get("gics_sub_industry", "").strip() for row in master_rows),
            "missing_exchange_rows": sum(not row.get("exchange", "").strip() for row in master_rows),
            "security_type_values": dict(sorted(Counter(row.get("security_type", "") for row in master_rows).items())),
            "share_class_field_present": "share_class" in master_fields,
            "ticker_to_multiple_ciks": {
                ticker: sorted(ciks) for ticker, ciks in sorted(ticker_to_ciks.items()) if len(ciks) > 1
            },
            "cik_to_multiple_tickers": {
                str(cik): sorted(tickers) for cik, tickers in sorted(cik_to_tickers.items()) if len(tickers) > 1
            },
        },
        "security_cik_map": {
            "path": SEC_CIK_MAP_FILE.as_posix(),
            "grain": "eligible current security-to-issuer mapping",
            "candidate_primary_key": ["ticker", "cik"],
            "fields": list(map_fields),
            "missing_required_columns": sorted(set(SEC_MAP_REQUIRED_COLUMNS) - set(map_fields)),
            "row_count": len(map_rows),
            "ticker_count": len({ticker for ticker, _ in map_pairs}),
            "cik_count": len(map_ciks),
            "duplicate_ticker_cik_pairs": map_pair_duplicates,
            "pairs_absent_from_security_master": _examples(sorted(f"{ticker}|{cik}" for ticker, cik in map_pairs - master_pairs)),
            "master_pairs_not_in_sec_map_count": len(master_pairs - map_pairs),
        },
        "price_to_master_coverage": {
            "price_ticker_count": len(price_tickers),
            "master_canonical_ticker_count": len(master_tickers),
            "master_yahoo_ticker_count": len(master_yahoo_tickers),
            "intersection_count": len(price_tickers & master_yahoo_tickers),
            "price_tickers_not_in_master_yahoo_mapping": price_not_master,
            "price_tickers_not_in_master_yahoo_mapping_are_expected_benchmark_only": set(price_not_master) == expected_price_only,
            "master_yahoo_tickers_without_price_rows": master_not_price,
            "canonical_to_yahoo_ticker_translation": translated_tickers,
        },
        "price_quality": {
            "path": PRICE_QUALITY_FILE.as_posix(),
            "grain": "ticker-level delivered quality summary",
            "fields": list(quality_fields),
            "row_count": len(quality_rows),
            "ticker_count": len(quality_tickers),
            "quality_status_counts": dict(sorted(quality_statuses.items())),
            "review_ticker_count": len(review_rows),
            "review_examples": [
                {"ticker": row.get("ticker", ""), "issues": row.get("quality_issues", "")}
                for row in review_rows[:20]
            ],
            "internal_missing_trading_dates_total": quality_missing_date_total,
            "duplicate_dates_total": quality_duplicate_date_total,
            "missing_core_cells_total": quality_missing_core_total,
            "invalid_ohlc_rows_total": quality_invalid_ohlc_total,
            "zero_volume_rows_total": quality_zero_volume_total,
            "quality_tickers_not_in_price": sorted(quality_tickers - price_tickers),
            "price_tickers_not_in_quality": sorted(price_tickers - quality_tickers),
        },
        "sec_download_status": {
            "path": SEC_STATUS_FILE.as_posix(),
            "grain": "issuer download status",
            "fields": list(status_fields),
            "row_count": len(status_rows),
            "cik_count": len(status_ciks),
            "status_counts": dict(sorted(status_values.items())),
        },
        "cross_layer_cik_coverage": {
            "sec_map_ciks_not_in_security_master": sorted(map_ciks - master_ciks),
            "security_master_ciks_not_in_sec_map_count": len(master_ciks - map_ciks),
            "sec_status_ciks_not_in_sec_map": sorted(status_ciks - map_ciks),
            "sec_map_ciks_not_in_sec_status": sorted(map_ciks - status_ciks),
        },
    }


def audit_sec_companyfacts(package: Path, expected_ciks: set[int] | None = None) -> dict[str, Any]:
    """Read every delivered SEC archive and profile factual metadata without selecting tags."""

    facts_directory = package / SEC_FACTS_DIR
    archives = sorted(facts_directory.glob("CIK*.json.gz"))
    archive_ciks: set[int] = set()
    unreadable: list[dict[str, str]] = []
    filename_cik_mismatches: list[dict[str, Any]] = []
    missing_root_metadata: Counter[str] = Counter()
    taxonomies: Counter[str] = Counter()
    tag_count_by_taxonomy: Counter[str] = Counter()
    tag_metadata_field_counts: Counter[str] = Counter()
    observation_field_counts: Counter[str] = Counter()
    form_counts: Counter[str] = Counter()
    observed_date_ranges: dict[str, list[date | None]] = {
        "start": [None, None],
        "end": [None, None],
        "filed": [None, None],
    }
    invalid_observation_dates: Counter[str] = Counter()
    observation_date_cache: dict[str, date | None] = {}
    total_tags = 0
    total_units = 0
    tags_with_multiple_units = 0
    total_observations = 0
    missing_observation_fields: Counter[str] = Counter()
    amendment_observations = 0
    duplicate_period_groups = 0
    duplicate_period_observations = 0
    multi_filed_period_groups = 0
    duplicate_period_examples: list[dict[str, Any]] = []
    amendment_examples: list[dict[str, Any]] = []
    root_key_sets: Counter[str] = Counter()

    for path in archives:
        match = CIK_FILENAME.match(path.name)
        filename_cik = int(match.group("cik")) if match else None
        try:
            payload = _load_gzip_json(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            unreadable.append({"file": path.name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        root_key_sets["|".join(sorted(payload.keys()))] += 1
        payload_cik = _as_int(payload.get("cik"))
        if payload_cik is not None:
            archive_ciks.add(payload_cik)
        if payload_cik != filename_cik:
            filename_cik_mismatches.append(
                {"file": path.name, "filename_cik": filename_cik, "payload_cik": payload_cik}
            )
        for field in ("cik", "entityName", "facts"):
            if field not in payload or payload.get(field) in (None, "", {}):
                missing_root_metadata[field] += 1
        facts = payload.get("facts")
        if not isinstance(facts, dict):
            continue
        for taxonomy, concepts in facts.items():
            taxonomies[taxonomy] += 1
            if not isinstance(concepts, dict):
                continue
            for tag, tag_payload in concepts.items():
                total_tags += 1
                tag_count_by_taxonomy[taxonomy] += 1
                if isinstance(tag_payload, dict):
                    tag_metadata_field_counts.update(tag_payload.keys())
                units = tag_payload.get("units", {}) if isinstance(tag_payload, dict) else {}
                if not isinstance(units, dict):
                    continue
                total_units += len(units)
                if len(units) > 1:
                    tags_with_multiple_units += 1
                for unit, observations in units.items():
                    if not isinstance(observations, list):
                        continue
                    period_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
                    for observation in observations:
                        if not isinstance(observation, dict):
                            continue
                        total_observations += 1
                        observation_field_counts.update(observation.keys())
                        for field in SEC_OBSERVATION_FIELDS:
                            if observation.get(field) in (None, ""):
                                missing_observation_fields[field] += 1
                        form = str(observation.get("form", ""))
                        form_counts[form] += 1
                        for date_field in ("start", "end", "filed"):
                            date_text = observation.get(date_field)
                            if not date_text:
                                continue
                            date_key = str(date_text)
                            if date_key not in observation_date_cache:
                                try:
                                    observation_date_cache[date_key] = date.fromisoformat(date_key)
                                except ValueError:
                                    observation_date_cache[date_key] = None
                            observed_date = observation_date_cache[date_key]
                            if observed_date is None:
                                invalid_observation_dates[date_field] += 1
                                continue
                            minimum, maximum = observed_date_ranges[date_field]
                            observed_date_ranges[date_field] = [
                                observed_date if minimum is None else min(minimum, observed_date),
                                observed_date if maximum is None else max(maximum, observed_date),
                            ]
                        if form.endswith("/A"):
                            amendment_observations += 1
                            if len(amendment_examples) < 20:
                                amendment_examples.append(
                                    {
                                        "cik": payload_cik,
                                        "taxonomy": taxonomy,
                                        "tag": tag,
                                        "unit": unit,
                                        "end": observation.get("end"),
                                        "filed": observation.get("filed"),
                                        "form": form,
                                        "accn": observation.get("accn"),
                                    }
                                )
                        period_key = (
                            observation.get("start"),
                            observation.get("end"),
                            observation.get("fy"),
                            observation.get("fp"),
                        )
                        period_groups[period_key].append(observation)
                    for period_key, records in period_groups.items():
                        if len(records) <= 1:
                            continue
                        duplicate_period_groups += 1
                        duplicate_period_observations += len(records) - 1
                        filed_dates = {record.get("filed") for record in records if record.get("filed")}
                        if len(filed_dates) > 1:
                            multi_filed_period_groups += 1
                        if len(duplicate_period_examples) < 20:
                            duplicate_period_examples.append(
                                {
                                    "cik": payload_cik,
                                    "taxonomy": taxonomy,
                                    "tag": tag,
                                    "unit": unit,
                                    "start": period_key[0],
                                    "end": period_key[1],
                                    "fy": period_key[2],
                                    "fp": period_key[3],
                                    "records": len(records),
                                    "filed_dates": sorted(filed_dates),
                                    "forms": sorted({str(record.get("form", "")) for record in records}),
                                }
                            )

    expected_ciks = expected_ciks or set()
    return {
        "path": SEC_FACTS_DIR.as_posix(),
        "grain": "issuer-level SEC Company Facts response archive",
        "candidate_primary_key": ["cik", "taxonomy", "tag", "unit", "start", "end", "filed", "accn"],
        "archive_file_count": len(archives),
        "readable_archive_count": len(archives) - len(unreadable),
        "unreadable_archives": unreadable,
        "archive_cik_count": len(archive_ciks),
        "archive_ciks_not_in_sec_map": sorted(archive_ciks - expected_ciks),
        "sec_map_ciks_without_archive": sorted(expected_ciks - archive_ciks),
        "filename_cik_mismatches": filename_cik_mismatches,
        "root_key_sets": dict(root_key_sets),
        "missing_root_metadata": dict(sorted(missing_root_metadata.items())),
        "taxonomy_archive_counts": dict(sorted(taxonomies.items())),
        "tag_count_by_taxonomy": dict(sorted(tag_count_by_taxonomy.items())),
        "tag_metadata_fields": dict(sorted(tag_metadata_field_counts.items())),
        "total_tags": total_tags,
        "total_units": total_units,
        "tags_with_multiple_units": tags_with_multiple_units,
        "total_observations": total_observations,
        "observation_fields": dict(sorted(observation_field_counts.items())),
        "form_counts": dict(sorted(form_counts.items())),
        "observed_date_ranges": {
            field: {
                "min": values[0].isoformat() if values[0] is not None else None,
                "max": values[1].isoformat() if values[1] is not None else None,
            }
            for field, values in observed_date_ranges.items()
        },
        "invalid_observation_dates": dict(sorted(invalid_observation_dates.items())),
        "missing_observation_fields": dict(sorted(missing_observation_fields.items())),
        "amendment_observations": amendment_observations,
        "amendment_examples": amendment_examples,
        "duplicate_period_groups": duplicate_period_groups,
        "duplicate_period_observations": duplicate_period_observations,
        "multi_filed_period_groups": multi_filed_period_groups,
        "duplicate_period_examples": duplicate_period_examples,
    }


def _read_price_tickers(path: Path) -> set[str]:
    tickers: set[str] = set()
    with path.open("rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            ticker = (row.get("ticker") or "").strip()
            if ticker:
                tickers.add(ticker)
    return tickers


def collect_audit(root: Path | None = None) -> dict[str, Any]:
    """Run every read-only audit component and return a JSON-serializable result."""

    base = root or project_root()
    package = package_root(base)
    if not package.is_dir():
        raise FileNotFoundError(f"Raw package not found: {package}")
    manifest_rows = load_manifest(package)
    manifest = audit_manifest(package)
    files = build_file_inventory(package, manifest_rows)
    price = audit_price_file(package / PRICE_FILE)
    price_tickers = _read_price_tickers(package / PRICE_FILE)
    identity = audit_identity_layer(package, price_tickers)
    sec_map_ciks = {
        _as_int(row.get("cik"))
        for row in _read_csv_rows(package / SEC_CIK_MAP_FILE)[1]
        if _as_int(row.get("cik")) is not None
    }
    sec = audit_sec_companyfacts(package, {cik for cik in sec_map_ciks if cik is not None})
    return {
        "report_schema": "fe5209.gate0.dev.source_audit.v1",
        "generated_at_utc": _utc_now(),
        "evidence_stage": EVIDENCE_STAGE,
        "package_relative_path": PACKAGE_RELATIVE_PATH.as_posix(),
        "manifest": manifest,
        "files": files,
        "price": price,
        "identity": identity,
        "sec_companyfacts": sec,
    }


def _check(status: str, name: str, evidence: str, disposition: str) -> dict[str, str]:
    return {"status": status, "name": name, "evidence": evidence, "disposition": disposition}


def acceptance_checks(audit: dict[str, Any]) -> list[dict[str, str]]:
    """Translate audit facts into Gate 0 DEV PASS/WARN/FAIL findings."""

    manifest = audit["manifest"]
    price = audit["price"]
    identity = audit["identity"]
    sec = audit["sec_companyfacts"]
    checks: list[dict[str, str]] = []
    checks.append(
        _check(
            "PASS" if manifest["passed"] else "FAIL",
            "Raw package manifest/hash integrity",
            f"Verified {manifest['verified_payload_files']}/{manifest['declared_payload_files']} declared payload files; "
            f"hash failures={len(manifest['hash_failures'])}, missing={len(manifest['missing_files'])}, unexpected={len(manifest['unexpected_files'])}.",
            "FAIL blocks DEV intake until the supplied package is restored exactly.",
        )
    )
    observed_ranges = sec["observed_date_ranges"]
    checks.append(
        _check(
            "WARN",
            "SEC raw form and period-date scope",
            f"raw forms={len(sec['form_counts'])}; start range={observed_ranges['start']['min']} to {observed_ranges['start']['max']}; "
            f"end range={observed_ranges['end']['min']} to {observed_ranges['end']['max']}; invalid parsed dates={dict(sec['invalid_observation_dates'])}.",
            "Raw Company Facts is intentionally unfiltered. Later PIT parsing must freeze included forms, tag/unit/period rules, and treatment of non-economic/outlying period observations.",
        )
    )
    required_files = [PRICE_FILE, SECURITY_MASTER_FILE, PRICE_QUALITY_FILE, SEC_CIK_MAP_FILE, SEC_STATUS_FILE, SEC_MANIFEST_FILE, YAHOO_MANIFEST_FILE]
    available_files = {row["relative_path"] for row in audit["files"]}
    missing_required_files = [item.as_posix() for item in required_files if item.as_posix() not in available_files]
    checks.append(
        _check(
            "PASS" if not missing_required_files else "FAIL",
            "Expected source files readable",
            "All expected delivered tables and metadata files are present." if not missing_required_files else f"Missing: {missing_required_files}",
            "FAIL blocks DEV intake.",
        )
    )
    price_key_pass = (
        not price["missing_required_columns"]
        and price["key_uniqueness_proven_by_strict_order"]
        and price["blank_ticker_rows"] == 0
        and price["invalid_date_rows"] == 0
    )
    checks.append(
        _check(
            "PASS" if price_key_pass else "FAIL",
            "Yahoo daily `(ticker, date)` primary-key and date validity",
            f"rows={price['row_count']}, strict_key_order={price['strictly_sorted_by_candidate_key']}, "
            f"invalid_dates={price['invalid_date_rows']}, blank_tickers={price['blank_ticker_rows']}.",
            "FAIL blocks DEV price intake until keys/dates are repaired or excluded by a documented rule.",
        )
    )
    price_quality_issue_count = (
        price["invalid_ohlc_rows"]
        + sum(price["negative_value_rows"].values())
        + identity["price_quality"]["review_ticker_count"]
    )
    checks.append(
        _check(
            "PASS" if price_quality_issue_count == 0 else "WARN",
            "Yahoo OHLC/action plausibility and delivered quality flags",
            f"stream_invalid_ohlc={price['invalid_ohlc_rows']}, negative_values={sum(price['negative_value_rows'].values())}, "
            f"zero_volume_rows={price['zero_volume_rows']}, quality_review_tickers={identity['price_quality']['review_ticker_count']}.",
            "WARN requires documented ticker/row treatment before a downstream object relies on affected observations; it does not invalidate the full DEV package.",
        )
    )
    coverage = identity["price_to_master_coverage"]
    cross_cik = identity["cross_layer_cik_coverage"]
    identity_pass = (
        not identity["security_master"]["duplicate_security_ids"]
        and not identity["security_cik_map"]["duplicate_ticker_cik_pairs"]
        and coverage["price_tickers_not_in_master_yahoo_mapping_are_expected_benchmark_only"]
        and not coverage["master_yahoo_tickers_without_price_rows"]
        and not cross_cik["sec_map_ciks_not_in_security_master"]
        and not cross_cik["sec_status_ciks_not_in_sec_map"]
        and not cross_cik["sec_map_ciks_not_in_sec_status"]
    )
    checks.append(
        _check(
            "PASS" if identity_pass else "FAIL",
            "Security identity and CIK mapping consistency",
            f"price/master-yahoo intersection={coverage['intersection_count']}; price-only={coverage['price_tickers_not_in_master_yahoo_mapping']}; "
            f"map CIK without status={len(cross_cik['sec_map_ciks_not_in_sec_status'])}; "
            f"SEC-map CIK outside master={len(cross_cik['sec_map_ciks_not_in_security_master'])}.",
            "FAIL blocks DEV identity-dependent intake until reconciled.",
        )
    )
    sec_pass = (
        sec["readable_archive_count"] == sec["archive_file_count"]
        and not sec["filename_cik_mismatches"]
        and not sec["archive_ciks_not_in_sec_map"]
        and not sec["sec_map_ciks_without_archive"]
        and not sec["missing_root_metadata"]
    )
    checks.append(
        _check(
            "PASS" if sec_pass else "FAIL",
            "SEC Company Facts readability and issuer coverage",
            f"readable_archives={sec['readable_archive_count']}/{sec['archive_file_count']}, "
            f"archive/map gaps={len(sec['archive_ciks_not_in_sec_map']) + len(sec['sec_map_ciks_without_archive'])}, "
            f"missing root metadata={dict(sec['missing_root_metadata'])}.",
            "FAIL blocks DEV PIT-fundamental intake until the delivered source/mapping is reconciled.",
        )
    )
    checks.append(
        _check(
            "WARN" if sec["duplicate_period_groups"] or sec["amendment_observations"] else "PASS",
            "SEC multi-unit, duplicate-period, and amendment facts",
            f"tags_with_multiple_units={sec['tags_with_multiple_units']}, duplicate_period_groups={sec['duplicate_period_groups']}, "
            f"amendment_observations={sec['amendment_observations']}.",
            "Expected source complexity. A later data contract must freeze tag/unit/period/amendment precedence; no priority is chosen by this audit.",
        )
    )
    checks.extend(
        [
            _check(
                "WARN",
                "Current S&P 500 survivorship bias",
                "The supplied universe is current-constituent based.",
                "Permitted for `DEV_CURRENT_UNIVERSE`; historical constituents are required before formal Gate A evidence.",
            ),
            _check(
                "WARN",
                "Current GICS is not historical PIT GICS",
                "Security master GICS is download-date reference data.",
                "Permitted for DEV pipeline testing only; historical GICS is required for formal PIT sector-neutralization evidence.",
            ),
            _check(
                "WARN",
                "RDQ/earnings-announcement availability unavailable",
                "SEC facts retain `filed_date`; actual RDQ/timestamp is not supplied.",
                "Use conservative `filed_date <= signal_date` for DEV; RDQ is a timing limitation/future enhancement.",
            ),
            _check(
                "WARN",
                "Yahoo historical adjustment limitation",
                "Delivered `close`, `adj_close`, dividend, and split fields need a frozen future corporate-action basis.",
                "Do not infer historical market cap by directly multiplying price and SEC shares; formal data contract work remains required.",
            ),
            _check(
                "WARN",
                "Historical member and delisting coverage pending",
                "The delivered package is not a historical index-membership or delisting reconstruction.",
                "Required for `FORMAL_PIT_UNIVERSE`, not a blocker for DEV intake.",
            ),
        ]
    )
    return checks


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_file_inventory_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = ["relative_path", "suffix", "bytes", "listed_in_payload_manifest", "manifest_sha256", "manifest_bytes", "purpose"]
    with path.open("wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_acceptance_markdown(path: Path, audit: dict[str, Any], checks: list[dict[str, str]]) -> None:
    failures = [check for check in checks if check["status"] == "FAIL"]
    warnings = [check for check in checks if check["status"] == "WARN"]
    decision = "PASS WITH WARNINGS — DEV Factor Engineering may begin" if not failures else "FAIL — DEV intake has blocking issues"
    lines = [
        "# Gate 0 — Source Data Intake Acceptance Report",
        "",
        f"- Evidence stage: `{EVIDENCE_STAGE}`",
        f"- Generated at (UTC): `{audit['generated_at_utc']}`",
        f"- DEV Gate 0 decision: **{decision}**",
        "- Formal Gate A decision: **not eligible from this evidence layer**; it requires the `FORMAL_PIT_UNIVERSE` rerun.",
        "",
        "| Status | Check | Evidence | Disposition |",
        "| --- | --- | --- | --- |",
    ]
    for check in checks:
        evidence = check["evidence"].replace("|", "\\|")
        disposition = check["disposition"].replace("|", "\\|")
        lines.append(f"| {check['status']} | {check['name']} | {evidence} | {disposition} |")
    lines.extend(["", "## DEV blockers", ""])
    if failures:
        lines.extend(f"- **{check['name']}** — {check['evidence']}" for check in failures)
    else:
        lines.append("- None found by this Gate 0 DEV audit.")
    lines.extend(["", "## DEV limitations that may proceed", ""])
    lines.extend(f"- **{check['name']}** — {check['disposition']}" for check in warnings)
    lines.extend(
        [
            "",
            "## Deferred to Formal PIT / WRDS or equivalent",
            "",
            "- Historical S&P 500 membership and security/identifier history.",
            "- Historical GICS/industry classification.",
            "- Historical-member return, delisting, and corporate-action coverage if CRSP or an equivalent source is adopted.",
            "- Optional PERMNO/GVKEY linking and any approved external fundamental cross-checks.",
            "- RDQ/announcement timestamp enrichment, if acquired; the conservative DEV availability rule remains `filed_date <= signal_date`.",
            "",
            "## Scope guard",
            "",
            "This audit did not calculate factors, IC, portfolio returns, FMOM, or strategy results. It did not write to `data/canonical/` or modify `raw_package/`.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_audit_outputs(audit: dict[str, Any], output_directory: Path) -> list[Path]:
    """Write only audit artifacts; no canonical or research-result datasets are created."""

    output_directory.mkdir(parents=True, exist_ok=True)
    checks = acceptance_checks(audit)
    inventory = {
        "report_schema": audit["report_schema"],
        "generated_at_utc": audit["generated_at_utc"],
        "evidence_stage": audit["evidence_stage"],
        "package_relative_path": audit["package_relative_path"],
        "manifest": audit["manifest"],
        "files": audit["files"],
        "source_objects": {
            "daily_prices": audit["price"],
            "security_identity": audit["identity"],
            "sec_companyfacts": audit["sec_companyfacts"],
        },
    }
    coverage = {
        "report_schema": audit["report_schema"],
        "generated_at_utc": audit["generated_at_utc"],
        "evidence_stage": audit["evidence_stage"],
        "daily_price_coverage": {
            key: audit["price"][key]
            for key in ("row_count", "ticker_count", "benchmark_present", "equity_ticker_count_excluding_spy", "min_date", "max_date")
        },
        "identity_coverage": audit["identity"],
        "sec_companyfacts_coverage": {
            key: audit["sec_companyfacts"][key]
            for key in (
                "archive_file_count",
                "readable_archive_count",
                "archive_cik_count",
                "archive_ciks_not_in_sec_map",
                "sec_map_ciks_without_archive",
                "taxonomy_archive_counts",
                "tag_count_by_taxonomy",
                "tag_metadata_fields",
                "total_tags",
                "total_units",
                "total_observations",
                "observation_fields",
                "form_counts",
                "observed_date_ranges",
                "invalid_observation_dates",
            )
        },
    }
    exceptions = {
        "report_schema": audit["report_schema"],
        "generated_at_utc": audit["generated_at_utc"],
        "evidence_stage": audit["evidence_stage"],
        "manifest_exceptions": {
            key: audit["manifest"][key]
            for key in ("missing_files", "unexpected_files", "byte_size_failures", "hash_failures")
        },
        "price_exceptions": {
            key: audit["price"][key]
            for key in (
                "missing_required_columns",
                "invalid_date_rows",
                "blank_ticker_rows",
                "missing_core_cells",
                "nonnumeric_cells",
                "negative_value_rows",
                "invalid_ohlc_rows",
                "zero_volume_rows",
                "sort_or_key_violations",
                "key_violation_examples",
                "invalid_date_examples",
                "invalid_ohlc_examples",
            )
        },
        "identity_exceptions": audit["identity"],
        "sec_companyfacts_exceptions": {
            key: audit["sec_companyfacts"][key]
            for key in (
                "unreadable_archives",
                "archive_ciks_not_in_sec_map",
                "sec_map_ciks_without_archive",
                "filename_cik_mismatches",
                "missing_root_metadata",
                "missing_observation_fields",
                "tags_with_multiple_units",
                "amendment_observations",
                "amendment_examples",
                "duplicate_period_groups",
                "duplicate_period_observations",
                "multi_filed_period_groups",
                "duplicate_period_examples",
            )
        },
        "acceptance_checks": checks,
    }
    paths = {
        "inventory": output_directory / "source_inventory.json",
        "file_inventory": output_directory / "source_file_inventory.csv",
        "coverage": output_directory / "source_coverage_report.json",
        "exceptions": output_directory / "source_exception_report.json",
        "acceptance": output_directory / "acceptance_test_report.md",
    }
    _write_json(paths["inventory"], inventory)
    _write_file_inventory_csv(paths["file_inventory"], audit["files"])
    _write_json(paths["coverage"], coverage)
    _write_json(paths["exceptions"], exceptions)
    _write_acceptance_markdown(paths["acceptance"], audit, checks)
    return list(paths.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=project_root())
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    root = args.project_root.resolve()
    output = (args.output_dir or root / "outputs" / "audit").resolve()
    audit = collect_audit(root)
    written = write_audit_outputs(audit, output)
    print(f"evidence_stage={EVIDENCE_STAGE}")
    for path in written:
        print(path.relative_to(root) if path.is_relative_to(root) else path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
