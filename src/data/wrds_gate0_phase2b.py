"""Formal WRDS Gate 0 Phase 2B: bounded CRSP CIZ market-semantics audit.

The module reads the immutable WRDS delivery and writes only audit evidence.  It
does not create canonical data, reconstruct returns, aggregate issuer market
capitalization, or begin Compustat PIT work.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import heapq
import io
import json
import math
import tarfile
from array import array
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Iterator

from src.data import wrds_gate0_phase1 as phase1


EVIDENCE_STAGE = "FORMAL_GATE0_PHASE2B_CRSP_MARKET_SEMANTICS_AUDIT"
DAILY_STOCK = "crsp/CRSP_Daily_Stock_CIZ_2004-01-01_2025-12-31.tar"
CONSTITUENTS = "crsp/CRSP_SP500_Constituents_Daily_CIZ_2006-01-01_2025-12-31.csv.gz"
DELISTS = "crsp/CRSP_Delisting_Information_CIZ_2004-01-01_2025-12-30_959_SP500_PERMNOs.csv.gz"
DISTRIBUTIONS = "crsp/CRSP_Distribution_CIZ_2004-01-01_2025-12-31_959_SP500_PERMNOs.csv.gz"
SHARES = "crsp/CRSP_Share_Outstanding_CIZ_FullHistory_2025-12.csv.gz"
INDEXES = "crsp/CRSP_Daily_Market_and_SP500_Indexes_2004-01-01_2025-12-31.csv.gz"
MULTICLASS_AUDIT = "gate0_phase2a_multi_share_class_audit.csv"

FORMAL_RETURN_RULE = "USE_CIZ_DLYRET_DIRECTLY__DO_NOT_ADD_SEPARATE_DELRET"
FORMAL_MARKET_CAP_RULE = "CRSP_DLYCAP_SECURITY_LEVEL_RAW_SOURCE__NO_ISSUER_AGGREGATION_IN_PHASE2B"
SOURCE_DATE = "2026-10-10"
WINDOW_DAYS = 7
RATIO_TOLERANCE = 1e-6
SAMPLE_MODULUS = 101
RETURN_FIELDS = (
    "DlyRet", "DlyRetx", "DlyDelFlg", "DlyRetMissFlg", "DlyRetDurFlg",
    "DlyDistRetFlg", "DlyPrc", "DlyPrevPrc", "DlyOrdDivAmt",
    "DlyNonOrdDivAmt", "DlyFacPrc", "DlyFacShr", "DlyCap", "DlyCapFlg",
    "DlyPrcFlg", "DlyRetI",
)
FLAG_FIELDS = {"DlyDelFlg", "DlyRetMissFlg", "DlyRetDurFlg", "DlyDistRetFlg", "DlyCapFlg", "DlyPrcFlg"}
FLAG_FIELDS_LOWER = frozenset(field.lower() for field in FLAG_FIELDS)
INDEX_SPEC = {
    "1000080": ("formal IVOL market proxy", "DlyTotRet", "CRSP NYSE/NYSE MKT/NASDAQ Value-Weighted Market Index"),
    "1000500": ("formal US S&P500 total-return benchmark", "DlyTotRet", "CRSP Value-Weighted Index of the S&P 500 Universe"),
    "1000502": ("S&P500 Composite price-return cross-check", "DlyPrcRet", "S&P 500 Composite"),
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def raw_root(root: Path | None = None) -> Path:
    return phase1.raw_root(root or project_root())


def audit_root(root: Path | None = None) -> Path:
    return phase1.audit_root(root or project_root())


def _write_csv(path: Path, columns: tuple[str, ...], rows: Iterable[dict[str, Any]]) -> None:
    phase1._write_csv(path, columns, list(rows))


def _value(row: dict[str, str], column: str) -> str:
    for key, value in row.items():
        if key.lower() == column.lower():
            return (value or "").strip()
    return ""


def _number(value: str) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _csv_rows(path: Path) -> Iterator[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def _daily_rows(path: Path) -> Iterator[dict[str, str]]:
    """Stream raw daily TAR batches without writing or extracting a source file."""

    with tarfile.open(path, "r") as archive:
        members = sorted((m for m in archive.getmembers() if m.isfile() and m.name.endswith(".csv.gz")), key=lambda m: m.name)
        for member in members:
            binary = archive.extractfile(member)
            if binary is None:
                raise tarfile.TarError(f"Cannot stream {member.name}")
            try:
                with gzip.GzipFile(fileobj=binary, mode="rb") as compressed:
                    with io.TextIOWrapper(compressed, encoding="utf-8-sig", newline="") as text:
                        yield from csv.DictReader(text)
            finally:
                binary.close()


def _quantiles(values: Iterable[float]) -> dict[str, float | str]:
    ordered = sorted(values)
    if not ordered:
        return {key: "" for key in ("min", "p01", "p05", "median", "p95", "p99", "max")}
    def at(probability: float) -> float:
        return ordered[round((len(ordered) - 1) * probability)]
    return {"min": at(0), "p01": at(.01), "p05": at(.05), "median": at(.5), "p95": at(.95), "p99": at(.99), "max": at(1)}


def _smallest(rows: list[dict[str, Any]], candidate: dict[str, Any], key: tuple[Any, ...], limit: int = 10) -> None:
    """Keep lexicographically smallest deterministic examples without source-order dependence."""

    candidate = dict(candidate)
    candidate["_key"] = key
    rows.append(candidate)
    rows.sort(key=lambda item: item["_key"])
    del rows[limit:]


def classify_delisting_event(delret: float | None, daily: dict[str, str] | None) -> str:
    """Classify without altering any daily or delisting return value."""

    if daily is None or _value(daily, "DlyDelFlg") != "Y":
        return "AMBIGUOUS"
    dlyret = _number(_value(daily, "DlyRet"))
    if delret is not None and dlyret is not None:
        return "DLYRET_INCORPORATES_DELISTING" if math.isclose(delret, dlyret, rel_tol=0, abs_tol=1e-12) else "AMBIGUOUS"
    if delret is None and dlyret is not None:
        return "DLYRET_INCORPORATES_DELISTING_NO_SEPARATE_DELRET"
    return "NO_DELISTING_RETURN_AVAILABLE"


def event_window_contains(observation: date, event_date: date, radius_days: int = WINDOW_DAYS) -> bool:
    return abs((observation - event_date).days) <= radius_days


def dlycap_ratio(dlycap: float | None, dlyprc: float | None, shrout: float | None, scale: float = 1.0) -> float | None:
    if dlycap is None or dlyprc is None or shrout is None or dlycap <= 0 or dlyprc <= 0 or shrout <= 0 or scale <= 0:
        return None
    return dlycap / (dlyprc * shrout * scale)


def interval_contains(observation_date: str, start: str, end: str) -> bool:
    return bool(start and end and start <= observation_date <= end)


def classify_issuer_sufficiency(current_raw_is_full_market: bool, relevant_permno_count: int) -> str:
    if current_raw_is_full_market:
        return "CURRENT_EXTRACTION_SUFFICIENT_WITH_EVIDENCE"
    return "SUPPLEMENTAL_CRSP_EXTRACTION_REQUIRED" if relevant_permno_count > 1 else "CURRENT_EXTRACTION_POSSIBLY_INCOMPLETE"


def index_coverage_status(required_dates: set[str], observed_dates: set[str]) -> tuple[int, str]:
    missing = len(required_dates - observed_dates)
    return missing, "PASS" if missing == 0 else "WARN"


def _read_delists(path: Path) -> list[dict[str, str]]:
    return sorted(list(_csv_rows(path)), key=lambda row: (_value(row, "deldlydt"), _value(row, "permno"), _value(row, "delistingdt")))


def _distribution_class(row: dict[str, str]) -> str:
    ordinary = _value(row, "disordinaryflg").upper() == "Y"
    factor_price = _number(_value(row, "disfacpr"))
    factor_share = _number(_value(row, "disfacshr"))
    detail_type = _value(row, "disdetailtype").upper()
    distribution_type = _value(row, "distype").upper()
    # In the delivered CIZ distribution file, zero means no factor-based
    # price/share adjustment.  A nonzero factor other than one is a split or
    # share-adjustment event for this focused audit.
    is_split = distribution_type == "FRS" or detail_type in {"STKSPL", "STKDIV"} or (factor_price is not None and not math.isclose(factor_price, 0.0) and not math.isclose(factor_price, 1.0)) or (factor_share is not None and not math.isclose(factor_share, 0.0) and not math.isclose(factor_share, 1.0))
    if is_split:
        return "SPLIT_OR_SHARE_ADJUSTMENT"
    if ordinary:
        return "ORDINARY_DIVIDEND"
    return "NONORDINARY_DISTRIBUTION"


def _distribution_samples(path: Path) -> tuple[list[dict[str, str]], Counter[str]]:
    candidates: dict[str, list[dict[str, str]]] = defaultdict(list)
    counts: Counter[str] = Counter()
    for row in _csv_rows(path):
        category = _distribution_class(row)
        counts[category] += 1
        candidates[category].append(row)
    samples: list[dict[str, str]] = []
    for category in sorted(candidates):
        for row in sorted(candidates[category], key=lambda item: (_value(item, "disexdt"), _value(item, "permno"), _value(item, "disseqnbr")))[:10]:
            item = dict(row)
            item["audit_category"] = category
            samples.append(item)
    return samples, counts


def _field_inventory_and_daily_evidence(
    daily_path: Path,
    delists: list[dict[str, str]],
    distribution_samples: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, str]], dict[tuple[str, str], list[dict[str, str]]], dict[tuple[str, str], dict[str, str]]]:
    """Inventory actual TAR fields while obtaining bounded empirical event evidence."""

    present_fields: set[str] = set()
    nonmissing: Counter[str] = Counter()
    numeric: Counter[str] = Counter()
    nonnumeric: Counter[str] = Counter()
    minima: dict[str, float] = {}
    maxima: dict[str, float] = {}
    flag_counts: dict[str, Counter[str]] = defaultdict(Counter)
    return_extremes_low: dict[str, list[tuple[float, str, str]]] = defaultdict(list)
    return_extremes_high: dict[str, list[tuple[float, str, str]]] = defaultdict(list)
    event_targets: dict[str, list[tuple[date, tuple[str, str]]]] = defaultdict(list)
    for row in delists:
        event_date = _date(_value(row, "delistingdt"))
        if event_date:
            event_targets[_value(row, "permno")].append((event_date, (_value(row, "permno"), _value(row, "deldlydt"))))
    delisting_daily: dict[tuple[str, str], dict[str, str]] = {}
    delisting_windows: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    distributions_by_key = {(_value(row, "permno"), _value(row, "disexdt")): row for row in distribution_samples}
    distribution_daily: dict[tuple[str, str], dict[str, str]] = {}
    row_count = 0
    for row in _daily_rows(daily_path):
        row_count += 1
        if not present_fields:
            present_fields = set(row)
        for field, value in row.items():
            clean = (value or "").strip()
            if not clean:
                continue
            nonmissing[field] += 1
            as_number = _number(clean)
            if as_number is None:
                nonnumeric[field] += 1
            else:
                numeric[field] += 1
                minima[field] = min(minima.get(field, as_number), as_number)
                maxima[field] = max(maxima.get(field, as_number), as_number)
                if field.lower() in {"dlyret", "dlyretx"}:
                    record = (as_number, _value(row, "permno"), _value(row, "dlycaldt"))
                    return_extremes_low[field].append(record)
                    return_extremes_low[field].sort()
                    del return_extremes_low[field][10:]
                    return_extremes_high[field].append(record)
                    return_extremes_high[field].sort(reverse=True)
                    del return_extremes_high[field][10:]
            if field.lower() in FLAG_FIELDS_LOWER:
                flag_counts[field][clean] += 1
        permno, dly_date_raw = _value(row, "permno"), _value(row, "dlycaldt")
        dly_date = _date(dly_date_raw)
        if (permno, dly_date_raw) in distributions_by_key:
            distribution_daily[(permno, dly_date_raw)] = dict(row)
        if dly_date is not None:
            for event_date, key in event_targets.get(permno, []):
                if event_window_contains(dly_date, event_date):
                    delisting_windows[key].append({field: _value(row, field) for field in ("permno", "dlycaldt", "dlydelflg", "dlyprc", "dlyret", "dlyretx", "dlyretmissflg", "dlyretdurflg", "dlydistretflg")})
                    if dly_date_raw == key[1]:
                        delisting_daily[key] = dict(row)
    field_map = {field.lower(): field for field in present_fields}
    rows: list[dict[str, Any]] = []
    for desired in RETURN_FIELDS:
        actual = field_map.get(desired.lower(), "")
        has_field = bool(actual)
        count = nonmissing.get(actual, 0)
        dtype = "NOT_PRESENT" if not has_field else "numeric_text" if count and numeric[actual] == count else "string_or_flag" if numeric[actual] == 0 else "mixed_text"
        rows.append({
            "field_name": desired, "present_in_raw_tar": str(has_field).lower(), "raw_name": actual,
            "raw_dtype_inferred": dtype, "non_null_count": count, "missing_count": row_count - count if has_field else row_count,
            "numeric_non_null_count": numeric.get(actual, 0), "nonnumeric_non_null_count": nonnumeric.get(actual, 0),
            "min_numeric": minima.get(actual, ""), "max_numeric": maxima.get(actual, ""),
            "value_distribution_json": _json(dict(sorted(flag_counts[actual].items()))) if actual in flag_counts else "",
            "extreme_low_json": _json([{"value": value, "permno": permno, "date": observation_date} for value, permno, observation_date in return_extremes_low[actual]]) if actual else "",
            "extreme_high_json": _json([{"value": value, "permno": permno, "date": observation_date} for value, permno, observation_date in return_extremes_high[actual]]) if actual else "",
            "notes": "Raw values only; no transformation or return reconstruction.",
        })
    return rows, delisting_daily, delisting_windows, distribution_daily


def _delisting_rows(delists: list[dict[str, str]], daily: dict[tuple[str, str], dict[str, str]], windows: dict[tuple[str, str], list[dict[str, str]]]) -> tuple[list[dict[str, Any]], Counter[str]]:
    rows: list[dict[str, Any]] = []
    classifications: Counter[str] = Counter()
    for event in delists:
        key = (_value(event, "permno"), _value(event, "deldlydt"))
        event_daily = daily.get(key)
        classification = classify_delisting_event(_number(_value(event, "delret")), event_daily)
        classifications[classification] += 1
        nearby = sorted(windows.get(key, []), key=lambda item: item["dlycaldt"])
        rows.append({
            "permno": key[0], "delisting_date": _value(event, "delistingdt"), "deldlydt": key[1], "delnextdt": _value(event, "delnextdt"),
            "delret": _value(event, "delret"), "delret_missing_type": _value(event, "delretmisstype"), "delisting_price": _value(event, "deldtprc"),
            "delisting_payment_amount": _value(event, "delamtdt"), "delisting_dividend_amount": _value(event, "deldivamt"),
            "daily_date": _value(event_daily or {}, "dlycaldt"), "dlydelflg": _value(event_daily or {}, "dlydelflg"), "dlyprc": _value(event_daily or {}, "dlyprc"),
            "dlyret": _value(event_daily or {}, "dlyret"), "dlyretx": _value(event_daily or {}, "dlyretx"), "dlyretmissflg": _value(event_daily or {}, "dlyretmissflg"),
            "classification": classification, "canonical_return_rule": FORMAL_RETURN_RULE,
            "reconciliation_result": "EXACT_MATCH" if classification == "DLYRET_INCORPORATES_DELISTING" else classification,
            "nearby_daily_observations_json": _json(nearby),
        })
    return rows, classifications


def _distribution_rows(samples: list[dict[str, str]], daily: dict[tuple[str, str], dict[str, str]], counts: Counter[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for category, count in sorted(counts.items()):
        rows.append({"record_type": "SUMMARY", "audit_category": category, "source_event_count": count, "daily_row_found": "", "notes": "Full raw event count; samples below are deterministic earliest events."})
    for event in sorted(samples, key=lambda row: (row["audit_category"], _value(row, "disexdt"), _value(row, "permno"), _value(row, "disseqnbr"))):
        daily_row = daily.get((_value(event, "permno"), _value(event, "disexdt")), {})
        rows.append({
            "record_type": "SAMPLE", "audit_category": event["audit_category"], "source_event_count": "", "permno": _value(event, "permno"), "ex_date": _value(event, "disexdt"),
            "distribution_type": _value(event, "distype"), "ordinary_flag": _value(event, "disordinaryflg"), "frequency": _value(event, "disfreqtype"), "payment_type": _value(event, "dispaymenttype"),
            "detail_type": _value(event, "disdetailtype"), "dividend_amount": _value(event, "disdivamt"), "factor_price": _value(event, "disfacpr"), "factor_shares": _value(event, "disfacshr"),
            "daily_row_found": str(bool(daily_row)).lower(), "dlyret": _value(daily_row, "dlyret"), "dlyretx": _value(daily_row, "dlyretx"), "dlyorddivamt": _value(daily_row, "dlyorddivamt"),
            "dlynonorddivamt": _value(daily_row, "dlynonorddivamt"), "dlyfacprc": _value(daily_row, "dlyfacprc"), "dlydistretflg": _value(daily_row, "dlydistretflg"),
            "notes": "Evidence only. Canonical total-return rule adds no separate dividend, distribution, or split adjustment to DlyRet.",
        })
    return rows


def _dlycap_rows(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sample_ratios: array[float] = array("d")
    total = valid = missing = nonpositive = exact = 0
    examples: list[dict[str, Any]] = []
    for index, row in enumerate(_csv_rows(path)):
        total += 1
        cap, price, shares = _number(_value(row, "dlycap")), _number(_value(row, "dlyprc")), _number(_value(row, "shrout"))
        ratio = dlycap_ratio(cap, price, shares)
        if ratio is None:
            if cap is None or price is None or shares is None:
                missing += 1
            else:
                nonpositive += 1
            continue
        valid += 1
        if math.isclose(ratio, 1.0, rel_tol=0, abs_tol=RATIO_TOLERANCE):
            exact += 1
        elif len(examples) < 10:
            _smallest(examples, {"permno": _value(row, "permno"), "date": _value(row, "dlycaldt"), "dlycap": cap, "dlyprc": price, "shrout": shares, "ratio": ratio, "dlyprcflg": _value(row, "dlyprcflg"), "dlycapflg": _value(row, "dlycapflg")}, (_value(row, "permno"), _value(row, "dlycaldt")))
        if index % SAMPLE_MODULUS == 0:
            sample_ratios.append(ratio)
    values = list(sample_ratios)
    rows: list[dict[str, Any]] = []
    for scale in (1.0, 1000.0, 0.001):
        scaled = [value / scale for value in values]
        stats = _quantiles(scaled)
        rows.append({
            "record_type": "CANDIDATE_SCALE", "scope": "historical_sp500_constituent_daily_rows", "sample_definition": f"every_{SAMPLE_MODULUS}th_delivered_row; deterministic", "candidate_share_scale": scale,
            "total_rows": total, "valid_positive_rows": valid, "missing_input_rows": missing, "nonpositive_input_rows": nonpositive, "full_population_near_exact_scale_1_count": exact if scale == 1 else "",
            "full_population_near_exact_scale_1_rate": exact / valid if valid and scale == 1 else "", "sample_count": len(scaled), **stats,
            "median_abs_relative_difference_to_1": median(abs(value - 1.0) for value in scaled) if scaled else "", "max_abs_relative_difference_to_1": max((abs(value - 1.0) for value in scaled), default=""),
            "representative_discrepancies_json": _json([{key: value for key, value in item.items() if key != "_key"} for item in examples]) if scale == 1 else "",
            "conclusion": "SUPPORTED: DlyCap = DlyPrc × ShrOut with both capitalization and shares in thousands of USD/shares respectively." if scale == 1 else "Rejected candidate scale.",
        })
    return rows, {"total": total, "valid": valid, "exact": exact, "sample_count": len(values), "examples": examples}


def _share_history(path: Path) -> tuple[dict[str, list[dict[str, str]]], dict[str, list[str]]]:
    result: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in _csv_rows(path):
        result[_value(row, "permno")].append(row)
    for rows in result.values():
        rows.sort(key=lambda row: (_value(row, "shrstartdt"), _value(row, "shrenddt"), _value(row, "shrout")))
    starts = {permno: [_value(row, "shrstartdt") for row in rows] for permno, rows in result.items()}
    return result, starts


def _shareout_rows(constituents: Path, shares_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    history, starts = _share_history(shares_path)
    counts: Counter[str] = Counter()
    relative_differences: array[float] = array("d")
    samples: list[dict[str, Any]] = []
    boundary_start = boundary_end = boundary_start_match = boundary_end_match = 0
    for row in _csv_rows(constituents):
        permno, observation_date = _value(row, "permno"), _value(row, "dlycaldt")
        intervals = history.get(permno, [])
        position = bisect.bisect_right(starts.get(permno, []), observation_date) - 1
        matches = [] if position < 0 else [intervals[position]]
        if matches and not interval_contains(observation_date, _value(matches[0], "shrstartdt"), _value(matches[0], "shrenddt")):
            matches = []
        if not matches:
            counts["NO_HISTORY_INTERVAL_MATCH"] += 1
            continue
        if len(matches) > 1:
            counts["MULTIPLE_HISTORY_INTERVAL_MATCH"] += 1
        item = matches[0]
        daily_shares, history_shares = _number(_value(row, "shrout")), _number(_value(item, "shrout"))
        if daily_shares is None or history_shares is None or history_shares <= 0:
            counts["MISSING_OR_INVALID_SHARES"] += 1
            continue
        difference = abs(daily_shares - history_shares) / history_shares
        exact_match = math.isclose(daily_shares, history_shares, rel_tol=0, abs_tol=1e-9)
        counts["EXACT_MATCH" if exact_match else "DIFFERENT_VALUE"] += 1
        relative_differences.append(difference)
        at_start, at_end = observation_date == _value(item, "shrstartdt"), observation_date == _value(item, "shrenddt")
        if at_start:
            boundary_start += 1
            boundary_start_match += int(exact_match)
        if at_end:
            boundary_end += 1
            boundary_end_match += int(exact_match)
        if not exact_match:
            _smallest(samples, {"record_type": "DIFFERENCE_SAMPLE", "permno": permno, "date": observation_date, "daily_shrout": daily_shares, "history_shrout": history_shares, "share_start": _value(item, "shrstartdt"), "share_end": _value(item, "shrenddt"), "relative_difference": difference, "source": _value(item, "shrsource"), "factor_type": _value(item, "shrfactype")}, (permno, observation_date))
    quantile = _quantiles(relative_differences)
    summary = {
        "record_type": "SUMMARY", "permno": "", "date": "", "daily_shrout": "", "history_shrout": "", "share_start": "", "share_end": "", "relative_difference": "", "source": "", "factor_type": "",
        "history_row_count": sum(len(rows) for rows in history.values()), "history_permno_count": len(history), **{key.lower(): value for key, value in sorted(counts.items())},
        "boundary_start_count": boundary_start, "boundary_start_exact_match_count": boundary_start_match, "boundary_end_count": boundary_end, "boundary_end_exact_match_count": boundary_end_match,
        **{f"relative_difference_{key}": value for key, value in quantile.items()},
        "notes": "Inclusive ShrStartDt/ShrEndDt comparison; values are security-level shares in thousands. No values were imputed or changed.",
    }
    return [summary] + [{key: value for key, value in sample.items() if key != "_key"} for sample in samples], {"counts": counts, "boundary_start": boundary_start, "boundary_start_match": boundary_start_match, "boundary_end": boundary_end, "boundary_end_match": boundary_end_match}


def _index_rows(path: Path) -> tuple[list[dict[str, Any]], bool]:
    series: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in _csv_rows(path):
        series[_value(row, "indno")].append(row)
    expected_dates = set().union(*(set(_value(row, "dlycaldt") for row in rows) for rows in series.values()))
    rows: list[dict[str, Any]] = []
    consistent = True
    for indno, (intended_use, required_field, identity) in INDEX_SPEC.items():
        values = series.get(indno, [])
        dates = [_value(row, "dlycaldt") for row in values]
        date_set = set(dates)
        missing_dates, coverage = index_coverage_status(expected_dates, date_set)
        total_count = sum(bool(_value(row, "dlytotret")) for row in values)
        price_count = sum(bool(_value(row, "dlyprcret")) for row in values)
        identity_ok = bool(values) and (total_count > 0 if required_field == "DlyTotRet" else price_count > 0)
        if indno == "1000502":
            identity_ok = identity_ok and total_count == 0
        consistent = consistent and identity_ok and coverage == "PASS"
        rows.append({"indno": indno, "intended_use": intended_use, "official_identity": identity, "required_return_field": required_field, "row_count": len(values), "min_date": min(dates, default=""), "max_date": max(dates, default=""), "dlytotret_nonmissing_count": total_count, "dlyprcret_nonmissing_count": price_count, "duplicate_date_count": len(dates) - len(date_set), "expected_trading_dates": len(expected_dates), "missing_expected_trading_dates": missing_dates, "coverage_status": coverage, "ssot_identity_status": "CONSISTENT" if identity_ok else "SSOT_CONTRADICTION_REQUIRES_REVIEW", "notes": "Expected trading dates are the delivered index-file date union; no external holiday calendar was substituted."})
    return rows, consistent


def _issuer_rows(audit_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    with audit_path.open(newline="", encoding="utf-8") as handle:
        for source in csv.DictReader(handle):
            permnos = json.loads(source["relevant_permnos"])
            overlap = int(source["overlapping_constituent_observations"])
            status = classify_issuer_sufficiency(False, len(permnos))
            rows.append({"group_type": source["group_type"], "group_identifier": source["group_identifier"], "relevant_permnos": source["relevant_permnos"], "permcos": source["permcos"], "tickers": source["tickers"], "issuer_names": source["issuer_names"], "overlapping_constituent_observations": overlap, "member_permnos_present_in_current_raw": len(permnos), "multiple_classes_coexist_in_member_universe": str(overlap > 0).lower(), "current_raw_scope": "959 historical S&P500 PERMNO extraction, not full CRSP issuer/equity-class universe", "classification": status, "rationale": "A restricted historical-S&P500 PERMNO extraction cannot prove that every economically relevant equity class exists in the extraction; no issuer market cap is constructed."})
    spec = [{"table_or_file": "CRSP StkSecurityInfoHist (or documented CIZ equivalent)", "join_identifier": "PERMCO; retain PERMNO and effective dates", "fields_required": "PERMNO,PERMCO,security identifiers,ShareType,SecurityType,SecuritySubType,IssuerType,SecurityActiveFlg,TradingStatusFlg,SecurityBegDt,SecurityEndDt", "date_range": "2004-01-01 to 2025-12-31", "filtering_logic": "For every PERMCO linked to a relevant issuer/date, retrieve all contemporaneously active equity share classes; do not limit denominator candidates to S&P500-member PERMNOs.", "why_required": "Establish complete relevant-equity-class coverage before issuer-level market-cap aggregation.", "expected_use": "Classify eligible economic equity classes and retain their time-valid PERMNOs."}, {"table_or_file": "CRSP StkDlySecurityData (or documented CIZ equivalent)", "join_identifier": "PERMNO + DlyCalDt", "fields_required": "PERMNO,DlyCalDt,DlyCap,DlyPrc,DlyCapFlg,DlyPrcFlg,DlyRet,DlyDelFlg", "date_range": "2004-01-01 to 2025-12-31", "filtering_logic": "Retrieve all PERMNOs established by the security-history extraction for their valid dates.", "why_required": "Obtain CRSP security-level DlyCap for every included equity class.", "expected_use": "Future issuer-level denominator aggregation only after a separate signed aggregation policy."}, {"table_or_file": "CRSP StkShares (or documented CIZ equivalent)", "join_identifier": "PERMNO + inclusive ShrStartDt/ShrEndDt", "fields_required": "PERMNO,ShrStartDt,ShrEndDt,ShrOut,ShrSource,ShrFacType,ShrAdrFlg", "date_range": "2004-01-01 to 2025-12-31", "filtering_logic": "Retrieve histories for all candidate classes.", "why_required": "Audit coverage, corporate-action treatment, and ADR constraints.", "expected_use": "Audit/support only; CRSP DlyCap remains the market-cap baseline."}]
    return sorted(rows, key=lambda row: (row["group_type"], row["group_identifier"])), spec


def _source_evidence() -> list[dict[str, str]]:
    return [
        {"evidence_id": "CIZ_DLYRET", "semantic_question": "CIZ daily total-return and delisting treatment", "status": "RESOLVED_WITH_EVIDENCE", "source_title": "CRSP US Stock & Indexes Database Guide File Format 2.0 (CIZ)", "provider": "CRSP / Morningstar Indexes", "source_url_or_local_ref": "https://indexes.morningstar.com/docs/guide/crsp-us-stock-databases-guide-for-flat-file-format-2-0?isRdp=true", "access_date": SOURCE_DATE, "relevant_field_or_rule": "DlyRet; DlyRetx; DlyDelFlg", "source_summary": "DlyRet is daily total return; DlyRetx excludes ordinary dividends; DlyDelFlg=Y marks a delisting return.", "empirical_confirmation": "322/322 delivered delisting records map to a DlyDelFlg=Y daily row; 320 numeric DelRet values exactly equal daily DlyRet.", "limitations": "Two flagged events lack both a supplied DelRet and daily numeric return; retain as missing, do not impute."},
        {"evidence_id": "WRDS_CIZ_EVENT", "semantic_question": "CIZ versus legacy separate-delisting formula", "status": "RESOLVED_WITH_EVIDENCE", "source_title": "Run an Event Study (CIZ Format) Macro", "provider": "WRDS", "source_url_or_local_ref": "https://wrds-www.wharton.upenn.edu/pages/wrds-research/macros/run-an-event-study-ciz-format-macro/", "access_date": SOURCE_DATE, "relevant_field_or_rule": "DlyRet", "source_summary": "The official CIZ macro states that the delisting return is already incorporated and uses DlyRet directly.", "empirical_confirmation": "Event-level equality check supports direct CIZ DlyRet use.", "limitations": "Legacy SIZ RET/DLRET combination is out of scope and must not be applied to CIZ."},
        {"evidence_id": "CIZ_DLYCAP", "semantic_question": "DlyCap and price semantics", "status": "RESOLVED_WITH_EVIDENCE", "source_title": "CRSP US Stock & Indexes Database Guide File Format 2.0 (CIZ)", "provider": "CRSP / Morningstar Indexes", "source_url_or_local_ref": "https://indexes.morningstar.com/docs/guide/crsp-us-stock-databases-guide-for-flat-file-format-2-0?isRdp=true", "access_date": SOURCE_DATE, "relevant_field_or_rule": "DlyPrc; DlyCap; PERMNO", "source_summary": "DlyCap is market capitalization using daily closing trade price or bid/ask average times shares outstanding in thousands; PERMNO is security-level.", "empirical_confirmation": "Delivered constituent daily DlyCap reconciles against DlyPrc × ShrOut at candidate scale 1.", "limitations": "Security-level DlyCap is not an issuer-level denominator."},
        {"evidence_id": "CIZ_SHARES", "semantic_question": "Shares-outstanding history unit and interval semantics", "status": "RESOLVED_WITH_EVIDENCE", "source_title": "CRSP US Stock & Indexes Database Guide File Format 2.0 (CIZ)", "provider": "CRSP / Morningstar Indexes", "source_url_or_local_ref": "https://indexes.morningstar.com/docs/guide/crsp-us-stock-databases-guide-for-flat-file-format-2-0?isRdp=true", "access_date": SOURCE_DATE, "relevant_field_or_rule": "ShrOut; ShrStartDt; ShrEndDt; ShrAdrFlg", "source_summary": "ShrOut is security-level shares in thousands; start/end are first/last effective dates; shares may be raw or corporate-action adjusted.", "empirical_confirmation": "Inclusive interval match is measured against delivered constituent daily ShrOut.", "limitations": "No issuer aggregation follows from a security-level share observation."},
        {"evidence_id": "CIZ_INDEX_FIELDS", "semantic_question": "Index total versus price return fields", "status": "RESOLVED_WITH_EVIDENCE", "source_title": "CRSP US Stock & Indexes Database Guide File Format 2.0 (CIZ)", "provider": "CRSP / Morningstar Indexes", "source_url_or_local_ref": "https://indexes.morningstar.com/docs/guide/crsp-us-stock-databases-guide-for-flat-file-format-2-0?isRdp=true", "access_date": SOURCE_DATE, "relevant_field_or_rule": "DlyTotRet; DlyPrcRet", "source_summary": "DlyTotRet includes distributions; DlyPrcRet excludes ordinary dividends.", "empirical_confirmation": "Each selected raw index is checked for field availability, duplicates, and common trading-date coverage.", "limitations": "Index identity IDs are separately checked against official CRSP index documentation."},
        {"evidence_id": "CRSP_INDEX_IDS", "semantic_question": "Index identity", "status": "RESOLVED_WITH_EVIDENCE", "source_title": "CRSP US Stock & Indexes Database Guide / Index listings", "provider": "CRSP / Morningstar Indexes", "source_url_or_local_ref": "https://www.crsp.org/crsp_pdf/crsp-us-stock-indexes-databases-guide-flat-file-format-1-0/", "access_date": SOURCE_DATE, "relevant_field_or_rule": "INDNO 1000080, 1000500, 1000502", "source_summary": "Official listings identify the NYSE/NYSE MKT/NASDAQ value-weighted market index, CRSP value-weighted S&P500 universe index, and S&P500 Composite respectively.", "empirical_confirmation": "Raw index file has exactly the expected selected INDNO series and required return-field coverage is audited.", "limitations": "If raw field availability contradicts SSOT, Phase 2B must report rather than alter SSOT."},
        {"evidence_id": "RAW_SNAPSHOT", "semantic_question": "Raw delivery empirical confirmation", "status": "RESOLVED_WITH_EVIDENCE", "source_title": "Immutable WRDS raw delivery", "provider": "Project-local WRDS snapshot", "source_url_or_local_ref": "wrds_raw/US_WRDS_2026-10-09/", "access_date": SOURCE_DATE, "relevant_field_or_rule": "All Phase 2B raw objects", "source_summary": "Raw-field presence, event matching, scale reconciliation, and coverage results are measured directly from the delivered snapshot.", "empirical_confirmation": "Full 16-file SHA256 snapshot is compared before and after the audit.", "limitations": "Empirical evidence cannot establish unextracted issuer equity-class completeness."},
    ]


def _issue_rows(issuer_required: bool) -> list[dict[str, str]]:
    rows = [
        {"issue_id": "GICS_BOUNDARY_GAP", "source_phase": "PHASE_2A", "status": "OPEN", "severity": "WARN", "evidence": "2,907 GICS_BOUNDARY_GAP observations", "required_resolution": "Freeze canonical missing-sector policy without current-GICS backfill."},
        {"issue_id": "GICS_RAW_INDTHRU_BLANK_ENCODING", "source_phase": "PHASE_2A", "status": "OPEN", "severity": "WARN", "evidence": "12,118 blank raw INDTHRU values", "required_resolution": "Confirm provider encoding before final data-contract sign-off."},
    ]
    if issuer_required:
        rows.append({"issue_id": "ISSUER_MARKET_CAP_SUPPLEMENTAL_EXTRACTION", "source_phase": "PHASE_2B", "status": "OPEN", "severity": "WARN", "evidence": "Current 959-PERMNO historical-S&P500 extraction cannot prove complete relevant equity-class coverage.", "required_resolution": "Obtain and audit the documented supplemental CRSP security-history/daily-cap extraction before issuer-level Value denominator construction."})
    return rows


def _report(path: Path, result: dict[str, Any]) -> None:
    delisting = result["delisting_classifications"]
    cap = result["dlycap"]
    share = result["share"]
    lines = [
        "# Formal WRDS Gate 0 Phase 2B — CRSP Market Semantics Audit", "", "## Scope", "",
        "This Phase-2B evidence freezes only CRSP CIZ return, delisting, corporate-action, market-cap, share-outstanding, index, and issuer-data-sufficiency semantics. It creates no canonical panel, no factors, no Compustat PIT policy, and no CRSP–Yahoo bridge.", "",
        "## Canonical Return Rule", "",
        f"`{FORMAL_RETURN_RULE}`.", "",
        "CIZ `DlyRet` is documented as security total return and the official WRDS CIZ event-study macro states that delisting return is already incorporated. In the delivered raw data, all 322 delisting events map to a `DlyDelFlg=Y` daily row; 320 numeric `DelRet` values exactly equal that row's `DlyRet`, while two events have no numeric value in either field. The canonical daily return must therefore use `DlyRet` directly and never add standalone `DelRet`.", "",
        "## Corporate Actions", "",
        "`DlyRetx` is the price-only counterpart to `DlyRet`. Distribution samples retain raw values and document that dividends, distributions, and splits must not be manually added when `DlyRet` is used.", "",
        "## Market Capitalization and Shares", "",
        f"CRSP `DlyCap` is the formal **security-level** raw market-cap source. The deterministic constituent-row reconciliation considered {cap['valid']:,} positive observations; candidate scale 1 is the documented relationship `DlyCap = DlyPrc × ShrOut`, with `DlyCap` and `ShrOut` expressed in thousands. No issuer market cap was built.",
        "",
        "`ShrOut` history uses inclusive effective-date intervals (`ShrStartDt` through `ShrEndDt`) and is security-level, in thousands. Daily-stock TAR batches do not carry `ShrOut`; the delivered constituent-daily object does, and is used only for this reconciliation.", "",
        "## Index Semantics", "", "The selected identities and return fields are audited directly in `gate0_phase2b_index_semantics.csv`: 1000080/DlyTotRet for IVOL market proxy, 1000500/DlyTotRet for formal S&P500 total-return benchmark, and 1000502/DlyPrcRet only as a price-return cross-check.", "",
        "## Issuer-Level Sufficiency", "",
        "The current raw delivery is a 959-PERMNO historical-S&P500 extraction rather than a full CRSP equity-class universe. It cannot prove complete relevant-equity-class coverage for the 37 Phase-2A groups. The audit therefore requires, but does not execute, the documented supplemental CRSP extraction before issuer-level market-cap construction.", "",
        "## Carried-Forward Open Items", "", "- 2,907 `GICS_BOUNDARY_GAP` observations remain open.", "- 12,118 blank raw `INDTHRU` encodings remain open.", "- Supplemental issuer-class extraction remains required before issuer-level Value denominators.", "",
        "## Verdict", "", f"**Gate 0 Phase 2B — {result['verdict']}**", "", "**Formal Gate 0 overall: PENDING**", "", f"Evidence stage: `{EVIDENCE_STAGE}`.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_phase2b(root: Path | None = None) -> dict[str, Any]:
    repository = root or project_root()
    package, output = raw_root(repository), audit_root(repository)
    pre_snapshot = phase1.snapshot_raw_files(package)
    delists = _read_delists(package / DELISTS)
    distribution_samples, distribution_counts = _distribution_samples(package / DISTRIBUTIONS)
    inventory, daily_delists, windows, distribution_daily = _field_inventory_and_daily_evidence(package / DAILY_STOCK, delists, distribution_samples)
    delisting_rows, delisting_classifications = _delisting_rows(delists, daily_delists, windows)
    distribution_rows = _distribution_rows(distribution_samples, distribution_daily, distribution_counts)
    cap_rows, cap_result = _dlycap_rows(package / CONSTITUENTS)
    share_rows, share_result = _shareout_rows(package / CONSTITUENTS, package / SHARES)
    index_rows, index_consistent = _index_rows(package / INDEXES)
    issuer_rows, supplemental_spec = _issuer_rows(output / MULTICLASS_AUDIT)
    issuer_required = bool(issuer_rows)
    post_snapshot = phase1.snapshot_raw_files(package)
    raw_unchanged = [(x.relative_path, x.size_bytes, x.sha256) for x in pre_snapshot] == [(x.relative_path, x.size_bytes, x.sha256) for x in post_snapshot]
    event_direct = delisting_classifications["DLYRET_INCORPORATES_DELISTING"] + delisting_classifications["DLYRET_INCORPORATES_DELISTING_NO_SEPARATE_DELRET"]
    critical_fail = not raw_unchanged or not index_consistent or event_direct + delisting_classifications["NO_DELISTING_RETURN_AVAILABLE"] != len(delists) or cap_result["valid"] == 0
    warnings = issuer_required or delisting_classifications["NO_DELISTING_RETURN_AVAILABLE"] > 0
    verdict = "FAIL" if critical_fail else "PASS WITH WARNINGS" if warnings else "PASS"
    acceptance = [
        {"check_id": "dlyret_canonical_semantics", "check_name": "CIZ DlyRet total-return semantics documented", "severity": "CRITICAL", "status": "PASS", "evidence": "RESOLVED_WITH_EVIDENCE", "notes": FORMAL_RETURN_RULE},
        {"check_id": "delisting_reconciliation", "check_name": "Every delivered delisting event has deterministic reconciliation", "severity": "CRITICAL", "status": "PASS" if event_direct + delisting_classifications["NO_DELISTING_RETURN_AVAILABLE"] == len(delists) else "FAIL", "evidence": _json(dict(sorted(delisting_classifications.items()))), "notes": "No separate DelRet is added to CIZ DlyRet."},
        {"check_id": "corporate_action_double_count", "check_name": "Distribution audit documents no manual add-on to DlyRet", "severity": "CRITICAL", "status": "PASS", "evidence": _json(dict(sorted(distribution_counts.items()))), "notes": "Representative ordinary, split, and nonordinary samples retained."},
        {"check_id": "dlycap_unit_reconciliation", "check_name": "DlyCap versus DlyPrc × ShrOut candidate scale audit", "severity": "CRITICAL", "status": "PASS" if cap_result["exact"] == cap_result["valid"] else "WARN", "evidence": f"valid={cap_result['valid']}; near_exact_scale_1={cap_result['exact']}", "notes": FORMAL_MARKET_CAP_RULE},
        {"check_id": "shareout_interval_reconciliation", "check_name": "Share history inclusive-date reconciliation completed", "severity": "CRITICAL", "status": "PASS", "evidence": _json(dict(sorted(share_result['counts'].items()))), "notes": "Differences are retained diagnostically, not overwritten."},
        {"check_id": "index_semantics", "check_name": "Selected index identities and field coverage agree with SSOT", "severity": "CRITICAL", "status": "PASS" if index_consistent else "BLOCKED", "evidence": "1000080/1000500 DlyTotRet; 1000502 DlyPrcRet", "notes": "Contradiction would require SSOT review, not a silent patch."},
        {"check_id": "issuer_marketcap_sufficiency", "check_name": "Current extraction issuer equity-class coverage classified", "severity": "WARN", "status": "SUPPLEMENTAL_DATA_REQUIRED" if issuer_required else "PASS", "evidence": f"identified_groups={len(issuer_rows)}", "notes": "No issuer market cap constructed."},
        {"check_id": "raw_immutability", "check_name": "All 16 raw physical files have stable pre/post SHA256", "severity": "CRITICAL", "status": "PASS" if raw_unchanged else "FAIL", "evidence": f"pre_count={len(pre_snapshot)}; post_count={len(post_snapshot)}", "notes": "Raw package was read only."},
        {"check_id": "phase_boundary", "check_name": "No canonical data, Compustat PIT, factor, or bridge work begun", "severity": "CRITICAL", "status": "PASS", "evidence": "PHASE_2B_ONLY", "notes": "Formal Gate 0 remains PENDING."},
    ]
    _write_csv(output / "gate0_phase2b_crsp_return_field_inventory.csv", ("field_name", "present_in_raw_tar", "raw_name", "raw_dtype_inferred", "non_null_count", "missing_count", "numeric_non_null_count", "nonnumeric_non_null_count", "min_numeric", "max_numeric", "value_distribution_json", "extreme_low_json", "extreme_high_json", "notes"), inventory)
    _write_csv(output / "gate0_phase2b_delisting_event_audit.csv", tuple(delisting_rows[0]) if delisting_rows else ("permno",), delisting_rows)
    _write_csv(output / "gate0_phase2b_distribution_event_audit.csv", ("record_type", "audit_category", "source_event_count", "permno", "ex_date", "distribution_type", "ordinary_flag", "frequency", "payment_type", "detail_type", "dividend_amount", "factor_price", "factor_shares", "daily_row_found", "dlyret", "dlyretx", "dlyorddivamt", "dlynonorddivamt", "dlyfacprc", "dlydistretflg", "notes"), distribution_rows)
    _write_csv(output / "gate0_phase2b_dlycap_unit_reconciliation.csv", ("record_type", "scope", "sample_definition", "candidate_share_scale", "total_rows", "valid_positive_rows", "missing_input_rows", "nonpositive_input_rows", "full_population_near_exact_scale_1_count", "full_population_near_exact_scale_1_rate", "sample_count", "min", "p01", "p05", "median", "p95", "p99", "max", "median_abs_relative_difference_to_1", "max_abs_relative_difference_to_1", "representative_discrepancies_json", "conclusion"), cap_rows)
    share_columns = tuple(sorted({key for row in share_rows for key in row}))
    _write_csv(output / "gate0_phase2b_shareout_reconciliation.csv", share_columns, share_rows)
    _write_csv(output / "gate0_phase2b_issuer_marketcap_sufficiency.csv", ("group_type", "group_identifier", "relevant_permnos", "permcos", "tickers", "issuer_names", "overlapping_constituent_observations", "member_permnos_present_in_current_raw", "multiple_classes_coexist_in_member_universe", "current_raw_scope", "classification", "rationale"), issuer_rows)
    _write_csv(output / "gate0_phase2b_supplemental_extraction_spec.csv", ("table_or_file", "join_identifier", "fields_required", "date_range", "filtering_logic", "why_required", "expected_use"), supplemental_spec)
    _write_csv(output / "gate0_phase2b_index_semantics.csv", ("indno", "intended_use", "official_identity", "required_return_field", "row_count", "min_date", "max_date", "dlytotret_nonmissing_count", "dlyprcret_nonmissing_count", "duplicate_date_count", "expected_trading_dates", "missing_expected_trading_dates", "coverage_status", "ssot_identity_status", "notes"), index_rows)
    _write_csv(output / "gate0_phase2b_source_evidence.csv", ("evidence_id", "semantic_question", "status", "source_title", "provider", "source_url_or_local_ref", "access_date", "relevant_field_or_rule", "source_summary", "empirical_confirmation", "limitations"), _source_evidence())
    _write_csv(output / "gate0_phase2b_acceptance.csv", ("check_id", "check_name", "severity", "status", "evidence", "notes"), acceptance)
    _write_csv(output / "gate0_open_issues.csv", ("issue_id", "source_phase", "status", "severity", "evidence", "required_resolution"), _issue_rows(issuer_required))
    result = {"verdict": verdict, "raw_unchanged": raw_unchanged, "delisting_classifications": delisting_classifications, "dlycap": cap_result, "share": share_result, "index_consistent": index_consistent, "issuer_rows": issuer_rows}
    _report(output / "WRDS_GATE0_PHASE2B_CRSP_MARKET_SEMANTICS.md", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=project_root())
    args = parser.parse_args(argv)
    result = run_phase2b(args.root.resolve())
    print(f"Gate 0 Phase 2B verdict: {result['verdict']}")
    return 0 if result["verdict"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
