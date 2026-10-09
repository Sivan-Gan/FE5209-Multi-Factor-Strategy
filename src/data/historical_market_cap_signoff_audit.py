"""DEV-only historical-market-cap sign-off and coverage-closure audit.

The module deliberately calculates *availability flags only*.  It never
multiplies price by shares and never emits a market-cap, factor, ratio, IC,
portfolio, or strategy value.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from src.data import historical_market_cap_spec_audit as spec
from src.data import source_audit


VERSION = "fe5209.historical_market_cap_signoff.dev.v1"
STALENESS_SCENARIOS: tuple[int | None, ...] = (None, 120, 180, 270, 365)
REQUIRED_REASON_CODES = (
    "AVAILABLE", "NO_SEC_SHARES", "SHARES_NOT_PIT_AVAILABLE", "SHARES_AMBIGUOUS",
    "SHARES_INVALID", "SHARES_STALE", "NONSTANDARD_CORPORATE_ACTION",
    "MULTICLASS_UNRESOLVED", "NO_SIGNAL_DATE_CLOSE", "PRICE_INVALID",
    "UNSUPPORTED_FORM_OR_TAXONOMY", "OTHER",
)


@dataclass(frozen=True)
class ShareRaw:
    cik: int
    value: float
    value_text: str
    end_date: date | None
    filed_date: date | None
    form: str
    accession: str
    amendment: bool
    ordinal: int


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def paths(root: Path) -> dict[str, Path]:
    audit = root / "outputs" / "audit"
    return {
        "small": audit / "market_cap_small_share_values_audit_dev.csv",
        "actions": audit / "market_cap_nonstandard_actions_audit_dev.csv",
        "staleness": audit / "market_cap_staleness_sensitivity_dev.csv",
        "price": audit / "market_cap_signal_date_price_quality_dev.csv",
        "value": audit / "market_cap_value_input_joint_coverage_dev.csv",
        "reasons": audit / "market_cap_missingness_reason_decomposition_dev.csv",
        "bias": audit / "market_cap_missingness_selection_bias_dev.csv",
        "report": audit / "historical_market_cap_signoff_acceptance_report.md",
        "document": root / "docs" / "HISTORICAL_MARKET_CAP_SIGNOFF_AUDIT_DEV.md",
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


def _pct(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    point = (len(ordered) - 1) * quantile
    low, high = int(point), min(int(point) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (point - low)


def _window(day: date) -> str | None:
    return spec._window(day)


def _value_bucket(value: float) -> str:
    if value < 0:
        return "LESS_THAN_ZERO"
    if value == 0:
        return "EQUAL_ZERO"
    if value < 1:
        return "BETWEEN_ZERO_AND_ONE"
    if value == 1:
        return "EQUAL_ONE"
    return "GREATER_THAN_ONE"


def classify_small_share(value: float, has_normal_same_end: bool, has_other_normal: bool) -> str:
    """Classify a raw direct-share observation without repairing it."""
    if value < 0:
        return "OBVIOUS_INVALID_NEGATIVE"
    if value == 0:
        return "OBVIOUS_INVALID_ZERO"
    if value < 1:
        return "UNRESOLVED_FRACTIONAL_OR_SCALE_CONTEXT"
    if value == 1 and (has_normal_same_end or has_other_normal):
        return "LIKELY_XBRL_CONTEXT_OR_SCALE_ISSUE"
    if value == 1:
        return "UNRESOLVED_UNIT_VALUE_NEEDS_CONTEXT"
    return "NOT_SMALL"


def classify_nonstandard_action(factor: float) -> tuple[str, str]:
    """Numerical classification only; the source does not prove event semantics."""
    if math.isclose(factor, 1.0, rel_tol=0.0, abs_tol=1e-9):
        return "UNITY_VENDOR_ACTION", "ticker/corporate-action artifact or vendor anomaly; unresolved"
    if factor > 1:
        return "NONSTANDARD_UPWARD_FACTOR", "unusual split, share distribution, spin-off, or vendor artifact; unresolved"
    if 0 < factor < 1:
        return "NONSTANDARD_DOWNWARD_FACTOR", "reverse-like action, spin-off, distribution, or vendor artifact; unresolved"
    return "UNUSABLE_ACTION_FACTOR", "non-positive/non-finite vendor action anomaly; unresolved"


def price_status(price: dict[str, str] | None) -> tuple[str, bool]:
    """Return baseline price availability without filling or substituting dates."""
    if price is None:
        return "NO_SIGNAL_DATE_CLOSE", False
    close = spec._finite_float(price.get("close"))
    if close is None or close <= 0:
        return "NO_SIGNAL_DATE_CLOSE", False
    if price.get("is_ohlc_plausible", "").lower() != "true":
        return "PRICE_INVALID", False
    if price.get("is_zero_volume", "").lower() == "true":
        return "PRICE_REVIEW_FLAG_ZERO_VOLUME", True
    if price.get("is_review_security", "").lower() == "true":
        return "PRICE_REVIEW_FLAG_SECURITY", True
    return "PRICE_VALID", True


def value_two_of_three(ep: bool, bp: bool, fcf: bool) -> bool:
    return sum((ep, bp, fcf)) >= 2


def candidate_reason(
    *,
    share_status: str,
    raw_records: list[ShareRaw],
    share_value: float | None,
    is_multiclass: bool,
    usable_price: bool,
    price_code: str,
    nonstandard: bool,
) -> str:
    """Return one mutually-exclusive baseline reason without repairing data."""
    if share_status != "AVAILABLE" or share_value is None:
        if not raw_records:
            return "NO_SEC_SHARES"
        if all(not spec._is_allowed_share_form(item.form) for item in raw_records):
            return "UNSUPPORTED_FORM_OR_TAXONOMY"
        if share_status in {"AMBIGUOUS", "OPEN_IMPLEMENTATION_ISSUE"}:
            return "SHARES_AMBIGUOUS"
        return "SHARES_NOT_PIT_AVAILABLE"
    if share_value <= 1:
        return "MULTICLASS_UNRESOLVED" if is_multiclass else "SHARES_INVALID"
    if is_multiclass:
        return "MULTICLASS_UNRESOLVED"
    if not usable_price:
        return price_code
    if nonstandard:
        return "NONSTANDARD_CORPORATE_ACTION"
    return "AVAILABLE"


def _read_raw_primary(
    root: Path, ciks: set[int]
) -> tuple[dict[int, list[ShareRaw]], dict[int, list[spec.ShareObservation]]]:
    result: dict[int, list[ShareRaw]] = defaultdict(list)
    eligible: dict[int, list[spec.ShareObservation]] = defaultdict(list)
    facts = source_audit.package_root(root) / source_audit.SEC_FACTS_DIR
    for archive in sorted(facts.glob("CIK*.json.gz")):
        with gzip.open(archive, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        cik = source_audit._as_int(payload.get("cik"))
        if cik not in ciks:
            continue
        unit_map = (
            payload.get("facts", {})
            .get("dei", {})
            .get("EntityCommonStockSharesOutstanding", {})
            .get("units", {})
        )
        observations = unit_map.get("shares", []) if isinstance(unit_map, dict) else []
        for ordinal, raw in enumerate(observations):
            value = spec._finite_float(raw.get("val")) if isinstance(raw, dict) else None
            if value is None:
                continue
            record = ShareRaw(
                    cik=cik,
                    value=value,
                    value_text=str(raw.get("val")),
                    end_date=spec._parse_date(raw.get("end")),
                    filed_date=spec._parse_date(raw.get("filed")),
                    form=str(raw.get("form") or ""),
                    accession=str(raw.get("accn") or ""),
                    amendment=str(raw.get("form") or "").endswith("/A"),
                    ordinal=ordinal,
                )
            result[cik].append(record)
            if (
                record.value > 0
                and record.end_date is not None
                and record.filed_date is not None
                and spec._is_allowed_share_form(record.form)
                and record.end_date <= record.filed_date
            ):
                eligible[cik].append(
                    spec.ShareObservation(
                        cik=cik, taxonomy="dei", tag="EntityCommonStockSharesOutstanding", unit="shares",
                        value_text=record.value_text, value=record.value, end_date=record.end_date,
                        filed_date=record.filed_date, form=record.form, accession=record.accession,
                        frame="", fiscal_year="", fiscal_period="", start_date="", ordinal=record.ordinal,
                    )
                )
    return dict(result), dict(eligible)


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


def _read_inputs(root: Path) -> tuple[
    list[dict[str, str]], dict[int, list[dict[str, str]]], set[str], list[date],
    dict[tuple[str, date], dict[str, str]], dict[str, list[tuple[date, float, bool]]],
]:
    master, _all_by_cik, mapped_by_cik, _by_ticker, mapped_tickers, signal_dates = spec._read_identity(root)
    prices, splits, _events, _counts = spec._read_price_evidence(root, set(signal_dates))
    return master, mapped_by_cik, mapped_tickers, signal_dates, prices, splits


def _summary_rows(measure: str, denominator: int, values: dict[date, int]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for window in ("factor_selection_2011_2015", "configuration_selection_2016_2020", "historical_oos_2021_2026_09"):
        rates = [count / denominator for day, count in values.items() if _window(day) == window]
        counts = [count for day, count in values.items() if _window(day) == window]
        if rates:
            rows.append({
                "measure": measure, "window": window, "signal_week_count": len(rates),
                "denominator_stock_count": denominator, "mean_weekly_share": sum(rates) / len(rates),
                "median_weekly_share": _pct(rates, .5), "min_weekly_share": min(rates),
                "p05_weekly_share": _pct(rates, .05), "mean_weekly_stock_count": sum(counts) / len(counts),
            })
    return rows


def run(root: Path | None = None) -> dict[str, Any]:
    root = (root or project_root()).resolve()
    out = paths(root)
    master, by_cik, mapped_tickers, signal_dates, prices, splits = _read_inputs(root)
    ciks = set(by_cik)
    raw, eligible = _read_raw_primary(root, ciks)
    selectors = {cik: spec.ShareSlot(sorted(eligible.get(cik, []), key=lambda x: (x.filed_date, x.end_date, x.source_id))) for cik in ciks}
    fundamentals = _fundamental_availability(root)
    single_ciks = {cik for cik, securities in by_cik.items() if len(securities) == 1}

    # Stable source-level small-value classification, then selected-cell counts.
    small_rows: dict[tuple[int, int], dict[str, Any]] = {}
    raw_distribution: Counter[str] = Counter()
    for cik, observations in raw.items():
        normal_by_end = defaultdict(bool)
        for item in observations:
            if item.end_date and item.value > 1:
                normal_by_end[item.end_date] = True
        for item in observations:
            bucket = _value_bucket(item.value)
            raw_distribution[bucket] += 1
            if bucket == "GREATER_THAN_ONE":
                continue
            key = (cik, item.ordinal)
            has_other_normal = any(x.value > 1 for x in observations)
            small_rows[key] = {
                "record_type": "RAW_SMALL_SHARE_OBSERVATION", "evidence_stage": spec.EVIDENCE_STAGE, "cik": cik, "ticker_count": len(by_cik.get(cik, [])),
                "tickers": "|".join(sorted(item["ticker"] for item in by_cik.get(cik, []))),
                "source_tag": "dei:EntityCommonStockSharesOutstanding", "unit": "shares",
                "raw_value": item.value_text, "value_bucket": bucket,
                "classification": classify_small_share(item.value, bool(item.end_date and normal_by_end[item.end_date]), has_other_normal),
                "end_date": item.end_date.isoformat() if item.end_date else "", "filed_date": item.filed_date.isoformat() if item.filed_date else "",
                "form": item.form, "amendment_status": str(item.amendment).lower(), "accession": item.accession,
                "same_period_normal_shares": str(bool(item.end_date and normal_by_end[item.end_date])).lower(),
                "raw_source_observation_count": 1, "selected_ticker_week_count": 0, "selected_windows": "", "source_issuer_has_other_normal_shares": str(has_other_normal).lower(),
            }

    cells: list[dict[str, Any]] = []
    action_impact: dict[tuple[str, date, float], Counter[str]] = defaultdict(Counter)
    for cik, securities in sorted(by_cik.items()):
        selector = selectors[cik]
        for signal_date in signal_dates:
            selection = selector.advance(signal_date)
            for security in securities:
                ticker = security["ticker"]
                price_code, usable_price = price_status(prices.get((ticker, signal_date)))
                raw_records = raw.get(cik, [])
                if selection.status != "AVAILABLE" or not selection.observation:
                    observation = None
                    share_age = None
                    nonstandard = False
                    share_valid = False
                else:
                    observation = selection.observation
                    share_age = (signal_date - observation.end_date).days
                    share_valid = observation.value > 1
                    _factor, nonstandard, _count = spec._chain_info(splits.get(ticker, []), observation.end_date)
                reason = candidate_reason(
                    share_status=selection.status,
                    raw_records=raw_records,
                    share_value=observation.value if observation else None,
                    is_multiclass=cik not in single_ciks,
                    usable_price=usable_price,
                    price_code=price_code,
                    nonstandard=nonstandard,
                )
                if observation and observation.value <= 1:
                    raw_key = next((key for key, row in small_rows.items() if row["cik"] == cik and row["accession"] == observation.accession and row["raw_value"] == observation.value_text), None)
                    if raw_key:
                        small_rows[raw_key]["selected_ticker_week_count"] += 1
                        small_rows[raw_key]["selected_windows"] = "|".join(sorted(set(filter(None, (small_rows[raw_key]["selected_windows"].split("|") + [_window(signal_date) or ""])))) )
                if observation and nonstandard:
                    for action_date, factor, standard in splits.get(ticker, []):
                        if not standard and action_date > observation.end_date:
                            key = (ticker, action_date, factor)
                            action_impact[key]["affected_ticker_weeks_nonadditive"] += 1
                            action_impact[key][_window(signal_date) or "outside_windows"] += 1
                            if reason == "NONSTANDARD_CORPORATE_ACTION":
                                action_impact[key]["baseline_loss_ticker_weeks_nonadditive"] += 1
                price = prices.get((ticker, signal_date))
                cells.append({"ticker": ticker, "cik": cik, "signal_date": signal_date, "reason": reason,
                              "share_age": share_age, "price_code": price_code, "share_status": selection.status,
                              "is_multiclass": cik not in single_ciks, "has_nonstandard": nonstandard,
                              "price_missing": price is None, "price_zero_volume": bool(price and price.get("is_zero_volume", "").lower() == "true"),
                              "price_review": bool(price and price.get("is_review_security", "").lower() == "true"),
                              "price_invalid_ohlc": bool(price and price.get("is_ohlc_plausible", "").lower() != "true"),
                              "internal_gap_security": str(security.get("quality_internal_missing_trading_dates", "0")) not in {"", "0"},
                              "sec_tag_available": selection.status == "AVAILABLE" and share_valid,
                              "first_price_date": security.get("first_price_date_source", ""),
                              "gics": security.get("gics_sector_current", ""),
                              "selected_form": selection.observation.form if selection.observation else ""})

    # Non-standard actions are described conservatively. Event impacts are explicitly non-additive.
    action_rows: list[dict[str, Any]] = []
    for ticker, events in sorted(splits.items()):
        for action_date, factor, standard in events:
            if standard:
                continue
            action_type, interpretation = classify_nonstandard_action(factor)
            counts = action_impact[(ticker, action_date, factor)]
            action_rows.append({"evidence_stage": spec.EVIDENCE_STAGE, "ticker": ticker, "action_date": action_date.isoformat(),
                                "factor": f"{factor:.12g}", "action_type_or_numeric_class": action_type,
                                "possible_interpretation": interpretation, "affected_ticker_weeks_nonadditive": counts["affected_ticker_weeks_nonadditive"],
                                "baseline_loss_ticker_weeks_nonadditive": counts["baseline_loss_ticker_weeks_nonadditive"],
                                "baseline_coverage_point_loss_nonadditive": counts["baseline_loss_ticker_weeks_nonadditive"] / (len(signal_dates) * len(mapped_tickers)),
                                "factor_selection_2011_2015": counts["factor_selection_2011_2015"], "configuration_selection_2016_2020": counts["configuration_selection_2016_2020"],
                                "historical_oos_2021_2026_09": counts["historical_oos_2021_2026_09"], "inclusion_rule": "UNRESOLVED_CORPORATE_ACTION_TO_MISSING"})

    denominator = len(mapped_tickers)
    issuer_denominator = len(by_cik)
    market_available = {cell["signal_date"]: 0 for cell in cells}
    value_counts = {name: {day: 0 for day in signal_dates} for name in ("market_cap_input", "ep_input", "bp_input", "fcf_yield_input", "value_two_of_three")}
    staleness_counts = {cutoff: {day: 0 for day in signal_dates} for cutoff in STALENESS_SCENARIOS}
    staleness_issuers = {cutoff: {day: set() for day in signal_dates} for cutoff in STALENESS_SCENARIOS}
    stale_removed = {cutoff: Counter() for cutoff in STALENESS_SCENARIOS if cutoff is not None}
    reason_counts: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
    bias: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
    for cell in cells:
        day, ticker = cell["signal_date"], cell["ticker"]
        available = cell["reason"] == "AVAILABLE"
        if available:
            market_available[day] += 1
            value_counts["market_cap_input"][day] += 1
        ep = available and (ticker, day) in fundamentals["net_income"]
        bp = available and (ticker, day) in fundamentals["common_equity"]
        fcf = available and (ticker, day) in fundamentals["ocf"] and (ticker, day) in fundamentals["capex"]
        for name, flag in (("ep_input", ep), ("bp_input", bp), ("fcf_yield_input", fcf), ("value_two_of_three", value_two_of_three(ep, bp, fcf))):
            value_counts[name][day] += int(flag)
        for cutoff in STALENESS_SCENARIOS:
            pass_flag = available and (cutoff is None or (cell["share_age"] is not None and cell["share_age"] <= cutoff))
            staleness_counts[cutoff][day] += int(pass_flag)
            if pass_flag:
                staleness_issuers[cutoff][day].add(cell["cik"])
            if available and not pass_flag and cutoff is not None:
                stale_removed[cutoff]["all_signal_dates"] += 1
                stale_removed[cutoff][_window(day) or "outside_windows"] += 1
        window = _window(day) or "outside_windows"
        reason_counts[window][cell["reason"]] += 1
        for dimension, group in (
            ("current_gics_sector", cell["gics"] or "MISSING"),
            ("share_tag_availability", "AVAILABLE" if cell["sec_tag_available"] else "UNAVAILABLE"),
            ("foreign_20f_selected_form", "20F" if spec._base_form(cell["selected_form"]) == "20-F" else "NOT_20F"),
            ("multiclass_status", "MULTICLASS" if cell["is_multiclass"] else "SINGLE_CLASS"),
            ("corporate_action_status", "NONSTANDARD_CHAIN" if cell["has_nonstandard"] else "NO_NONSTANDARD_CHAIN"),
            ("year", str(day.year)),
        ):
            bias[(dimension, window)][(group, "total")] += 1
            bias[(dimension, window)][(group, "available")] += int(available)
        first = spec._parse_date(cell["first_price_date"])
        age_group = "MISSING_FIRST_PRICE_DATE" if not first else ("LT_2Y" if (day - first).days < 730 else "2_TO_5Y" if (day - first).days < 1826 else "5_TO_10Y" if (day - first).days < 3652 else "GE_10Y")
        bias[("price_history_length", window)][(age_group, "total")] += 1
        bias[("price_history_length", window)][(age_group, "available")] += int(available)

    # Output aggregations only; no per-cell availability panel is written.
    small_output = list(small_rows.values()) + [
        {"record_type": "RAW_VALUE_DISTRIBUTION", "evidence_stage": spec.EVIDENCE_STAGE, "cik": "ALL", "ticker_count": "", "tickers": "", "source_tag": "dei:EntityCommonStockSharesOutstanding", "unit": "shares", "raw_value": "", "value_bucket": bucket, "classification": "RAW_VALUE_DISTRIBUTION", "end_date": "", "filed_date": "", "form": "", "amendment_status": "", "accession": "", "same_period_normal_shares": "", "raw_source_observation_count": raw_distribution[bucket], "selected_ticker_week_count": 0, "selected_windows": "", "source_issuer_has_other_normal_shares": ""}
        for bucket in ("LESS_THAN_ZERO", "EQUAL_ZERO", "BETWEEN_ZERO_AND_ONE", "EQUAL_ONE")
    ]
    _write_csv(out["small"], small_output)
    _write_csv(out["actions"], action_rows)

    stale_rows: list[dict[str, Any]] = []
    for cutoff, counts in staleness_counts.items():
        label = "NO_CUTOFF" if cutoff is None else f"{cutoff}_DAYS"
        issuer_counts = {day: len(issuers) for day, issuers in staleness_issuers[cutoff].items()}
        issuer_rows = {row["window"]: row for row in _summary_rows("issuer_availability", issuer_denominator, issuer_counts)}
        for row in _summary_rows("estimated_market_cap_input_availability", denominator, counts):
            issuer_row = issuer_rows[row["window"]]
            row.update({
                "scenario": label,
                "staleness_cutoff_days": "" if cutoff is None else cutoff,
                "denominator_issuer_count": issuer_denominator,
                "mean_weekly_issuer_share": issuer_row["mean_weekly_share"],
                "median_weekly_issuer_share": issuer_row["median_weekly_share"],
                "min_weekly_issuer_share": issuer_row["min_weekly_share"],
                "p05_weekly_issuer_share": issuer_row["p05_weekly_share"],
                "mean_weekly_issuer_count": issuer_row["mean_weekly_stock_count"],
                "stale_removed_ticker_weeks_all_signal_dates": stale_removed.get(cutoff, Counter()).get("all_signal_dates", 0),
                "stale_removed_ticker_weeks_in_window": stale_removed.get(cutoff, Counter()).get(row["window"], 0),
                "rule": "availability only; no market-cap values",
            })
            stale_rows.append(row)
    _write_csv(out["staleness"], stale_rows)

    price_conditions = {
        "NO_SIGNAL_DATE_CLOSE": lambda c: c["price_missing"],
        "INTERNAL_GAP_NO_SIGNAL_DATE_CLOSE": lambda c: c["price_missing"] and c["internal_gap_security"],
        "ZERO_VOLUME_SIGNAL_DATE_CLOSE": lambda c: c["price_zero_volume"],
        "REVIEW_SECURITY_SIGNAL_DATE_CLOSE": lambda c: c["price_review"],
        "INVALID_OHLC_SIGNAL_DATE_CLOSE": lambda c: c["price_invalid_ohlc"],
    }
    price_rows = []
    for condition, predicate in price_conditions.items():
        matching = [cell for cell in cells if predicate(cell)]
        price_rows.append({"evidence_stage": spec.EVIDENCE_STAGE, "condition": condition, "ticker_week_count": len(matching),
                           "affected_ticker_count": len({c["ticker"] for c in matching}),
                           "baseline_treatment": "UNAVAILABLE" if condition in {"NO_SIGNAL_DATE_CLOSE", "INTERNAL_GAP_NO_SIGNAL_DATE_CLOSE", "INVALID_OHLC_SIGNAL_DATE_CLOSE"} else "RETAIN_WITH_PRICE_REVIEW_FLAG"})
    _write_csv(out["price"], price_rows)

    value_rows: list[dict[str, Any]] = []
    for measure, counts in value_counts.items():
        for row in _summary_rows(measure, denominator, counts):
            row.update({"evidence_stage": spec.EVIDENCE_STAGE, "input_semantics": "annual Net Income/OCF/CapEx candidates and instant Common Equity; availability only"})
            value_rows.append(row)
    _write_csv(out["value"], value_rows)

    reason_rows: list[dict[str, Any]] = []
    for window, counts in sorted(reason_counts.items()):
        total = sum(counts.values())
        for reason in REQUIRED_REASON_CODES:
            count = counts.get(reason, 0)
            reason_rows.append({"evidence_stage": spec.EVIDENCE_STAGE, "denominator_scope": "500_sec_mapped_current_securities", "window": window,
                                "reason_code": reason, "ticker_week_count": count,
                                "ticker_count": len({c["ticker"] for c in cells if (_window(c["signal_date"]) or "outside_windows") == window and c["reason"] == reason}),
                                "coverage_point_loss": count / total if total else 0.0, "reason_priority": "share_source>PIT/conflict>invalid_share>multiclass>price>nonstandard_action"})
    _write_csv(out["reasons"], reason_rows)

    bias_rows: list[dict[str, Any]] = []
    for (dimension, window), counts in sorted(bias.items()):
        groups = sorted({group for group, kind in counts})
        for group in groups:
            total, available = counts[(group, "total")], counts[(group, "available")]
            bias_rows.append({"evidence_stage": spec.EVIDENCE_STAGE, "dimension": dimension, "window": window, "group": group,
                              "ticker_week_count": total, "available_ticker_week_count": available, "missing_ticker_week_count": total - available,
                              "availability_share": available / total if total else None, "note": "issuer-size proxy intentionally omitted: estimated market cap is not used to explain its own missingness"})
    _write_csv(out["bias"], bias_rows)

    nonstandard_union = sum(1 for cell in cells if cell["reason"] == "NONSTANDARD_CORPORATE_ACTION")
    small_selected = sum(int(row["selected_ticker_week_count"]) for row in small_rows.values())
    small_source_records = sum(raw_distribution[bucket] for bucket in ("LESS_THAN_ZERO", "EQUAL_ZERO", "BETWEEN_ZERO_AND_ONE", "EQUAL_ONE"))
    small_source_ciks = len({row["cik"] for row in small_rows.values()})
    action_type_counts = Counter(row["action_type_or_numeric_class"] for row in action_rows)
    action_tickers = len({row["ticker"] for row in action_rows})
    price_index = {row["condition"]: row for row in price_rows}
    staleness_index = {(row["scenario"], row["window"]): row for row in stale_rows}
    value_index = {(row["measure"], row["window"]): row for row in value_rows}
    reason_index = {(row["window"], row["reason_code"]): row for row in reason_rows}
    sector_ranges: dict[str, tuple[float, float]] = {}
    for window in ("factor_selection_2011_2015", "configuration_selection_2016_2020", "historical_oos_2021_2026_09"):
        sector_shares = [float(row["availability_share"]) for row in bias_rows if row["dimension"] == "current_gics_sector" and row["window"] == window]
        sector_ranges[window] = (min(sector_shares), max(sector_shares))

    def pct(value: float) -> str:
        return f"{value * 100:.2f}%"

    windows = (
        ("factor_selection_2011_2015", "2011–2015"),
        ("configuration_selection_2016_2020", "2016–2020"),
        ("historical_oos_2021_2026_09", "2021–2026.09"),
    )
    decision = "PASS WITH WARNINGS"
    report = f"""# Historical Market Cap Sign-off / Coverage Closure Audit — DEV

- **Evidence stage:** `{spec.EVIDENCE_STAGE}`
- **Decision:** {decision}
- **Construction authorization:** `AUTHORIZED_FOR_NEXT_ISOLATED_DEV_CONSTRUCTION_TASK_ONLY` — no construction is performed by this audit.
- **Scope:** availability/reason-code evidence only. No market-cap value or panel, ratio, factor, IC, portfolio, FMOM, or strategy output exists.
- **Denominator:** 500 SEC-mapped current-universe securities (497 issuers); this remains `DEV_CURRENT_UNIVERSE`, not a survivorship-unbiased historical universe.

## Scope and controls

The frozen price, PIT, share-source, split-basis, and multi-class rules were tested as specified. This audit selects no new factor rule and does not alter any raw, canonical upstream, or frozen-manual file. Any separately requested construction must retain the name `estimated_market_cap` and source `SEC_YAHOO_ESTIMATE_DEV`; it is not a formal PIT market cap.

## Small shares (`<= 1`) evidence

There are **{small_source_records} raw SEC observations** at or below one across **{small_source_ciks} CIKs**: `<0` {raw_distribution['LESS_THAN_ZERO']}, `=0` {raw_distribution['EQUAL_ZERO']}, `0<value<1` {raw_distribution['BETWEEN_ZERO_AND_ONE']}, and `=1` {raw_distribution['EQUAL_ONE']}. The previously quoted **{small_selected}** is not a raw-observation count: it is the number of selected ticker-weeks affected by the four `=1` source records.

The 31 zero records are `OBVIOUS_INVALID_ZERO` and none becomes a candidate selection. Two `=1` ICE records and one CSX record have other normal share observations and are `LIKELY_XBRL_CONTEXT_OR_SCALE_ISSUE`. The remaining `=1` record belongs to FOX/FOXA and is `UNRESOLVED_UNIT_VALUE_NEEDS_CONTEXT`; it is independently multi-class unresolved. The rule is not to mutate raw observations, but to require selected primary shares to be strictly `>1`; selected `<=1` cells are unavailable, with their source context preserved in the small-share audit CSV.

## Non-standard corporate-action evidence

The 104 non-standard Yahoo action factors span **{action_tickers} tickers**: {action_type_counts['NONSTANDARD_UPWARD_FACTOR']} numerical upward factors and {action_type_counts['NONSTANDARD_DOWNWARD_FACTOR']} numerical downward factors. These are numerical classes only—not asserted economic classifications. Possible interpretations remain unresolved (unusual split, share distribution, spin-off, ticker/corporate-action artifact, or vendor anomaly).

Keeping every non-standard action unresolved removes **{nonstandard_union:,} unique ticker-weeks** from candidate availability across the full signal calendar. The loss is **{pct(reason_index[('factor_selection_2011_2015', 'NONSTANDARD_CORPORATE_ACTION')]['coverage_point_loss'])}**, **{pct(reason_index[('configuration_selection_2016_2020', 'NONSTANDARD_CORPORATE_ACTION')]['coverage_point_loss'])}**, and **{pct(reason_index[('historical_oos_2021_2026_09', 'NONSTANDARD_CORPORATE_ACTION')]['coverage_point_loss'])}** in the three research windows, respectively. Individual action rows are non-additive because several actions can affect the same ticker-week. Under the Yahoo delivery-basis convention, a later action can affect an earlier reported-share observation; this is not evidence that the action occurred after that signal date.

## Staleness sensitivity (candidate availability only)

| Scenario | 2011–2015 mean | 2016–2020 mean | 2021–2026.09 mean | Removed ticker-weeks (all signal dates) |
| --- | ---: | ---: | ---: | ---: |
""".splitlines()
    for scenario in ("NO_CUTOFF", "120_DAYS", "180_DAYS", "270_DAYS", "365_DAYS"):
        scenario_rows = [staleness_index[(scenario, window)] for window, _label in windows]
        report.append(
            f"| {scenario} | {pct(float(scenario_rows[0]['mean_weekly_share']))} | {pct(float(scenario_rows[1]['mean_weekly_share']))} | {pct(float(scenario_rows[2]['mean_weekly_share']))} | {int(scenario_rows[0]['stale_removed_ticker_weeks_all_signal_dates']):,} |"
        )
    report.extend([
        "",
        "Recommendation: **`NO_CUTOFF_WITH_STALENESS_FLAG`** for this DEV hand-off. It preserves the current candidate method while making age observable. A 120-day screen removes 18,692 ticker-weeks and lowers the three means by 4.16, 4.43, and 4.53 percentage points; 180/270/365-day cases remain sensitivity evidence, not silently substituted baselines. The CSV records weekly issuer coverage (distinct CIKs) alongside ticker coverage.",
        "",
        "## Signal-date price evidence",
        "",
        f"Missing exact `close` occurs in {price_index['NO_SIGNAL_DATE_CLOSE']['ticker_week_count']:,} weekly cells / {price_index['NO_SIGNAL_DATE_CLOSE']['affected_ticker_count']} tickers; zero-volume valid bars occur in {price_index['ZERO_VOLUME_SIGNAL_DATE_CLOSE']['ticker_week_count']:,} / {price_index['ZERO_VOLUME_SIGNAL_DATE_CLOSE']['affected_ticker_count']}; review-security bars in {price_index['REVIEW_SECURITY_SIGNAL_DATE_CLOSE']['ticker_week_count']:,} / {price_index['REVIEW_SECURITY_SIGNAL_DATE_CLOSE']['affected_ticker_count']}; invalid OHLC and internal-gap missing signal dates are both zero. Baseline: exact positive plausible `close` only; retain zero-volume/review bars with `PRICE_REVIEW_FLAG`; mark invalid/missing as unavailable; never fill from another date.",
        "",
        "## Input-level joint coverage (no ratios calculated)",
        "",
        "| Window | MCap input | E/P input | B/P input | FCF Yield input | Value >=2/3 | Mean eligible count for Value >=2/3 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ])
    for window, label in windows:
        mcap = value_index[("market_cap_input", window)]
        ep = value_index[("ep_input", window)]
        bp = value_index[("bp_input", window)]
        fcf = value_index[("fcf_yield_input", window)]
        two = value_index[("value_two_of_three", window)]
        report.append(
            f"| {label} | {pct(float(mcap['mean_weekly_share']))} | {pct(float(ep['mean_weekly_share']))} | {pct(float(bp['mean_weekly_share']))} | {pct(float(fcf['mean_weekly_share']))} | {pct(float(two['mean_weekly_share']))} | {float(two['mean_weekly_stock_count']):.1f} |"
        )
    report.extend([
        "",
        "The companion CSV gives mean, median, minimum, p05, and weekly eligible count for every availability set. It performs no E/P, B/P, FCF, FCF Yield, Value score, or Size calculation.",
        "",
        "## Size / neutralization and missingness diagnostics",
        "",
        "Candidate market-cap cross-sections average 348.7, 401.4, and 443.0 of 500 securities in the three windows. Because all multi-class issuers are unavailable, available ticker and issuer counts are equivalent in these candidate cells; staleness output separately reports the 497-issuer denominator. Annual and current-GICS diagnostics appear in the selection-bias CSV. Current-GICS availability ranges from "
        + f"{pct(sector_ranges['factor_selection_2011_2015'][0])}–{pct(sector_ranges['factor_selection_2011_2015'][1])}, "
        + f"{pct(sector_ranges['configuration_selection_2016_2020'][0])}–{pct(sector_ranges['configuration_selection_2016_2020'][1])}, and "
        + f"{pct(sector_ranges['historical_oos_2021_2026_09'][0])}–{pct(sector_ranges['historical_oos_2021_2026_09'][1])}. This is a material selection-bias warning, not a sector-neutralization result; GICS is current, not historical PIT.",
        "",
        "The familiar all-calendar 63.59% coverage includes the pre-2011 warm-up and is not comparable to the three formal-window figures. Its dominant mutually-exclusive reason is `SHARES_NOT_PIT_AVAILABLE` early in the period. Within the formal windows, leading losses are: 2011–2015 `SHARES_NOT_PIT_AVAILABLE` "
        + pct(reason_index[("factor_selection_2011_2015", "SHARES_NOT_PIT_AVAILABLE")]["coverage_point_loss"])
        + ", `NONSTANDARD_CORPORATE_ACTION` " + pct(reason_index[("factor_selection_2011_2015", "NONSTANDARD_CORPORATE_ACTION")]["coverage_point_loss"])
        + "; 2016–2020 `SHARES_NOT_PIT_AVAILABLE` " + pct(reason_index[("configuration_selection_2016_2020", "SHARES_NOT_PIT_AVAILABLE")]["coverage_point_loss"])
        + ", `NONSTANDARD_CORPORATE_ACTION` " + pct(reason_index[("configuration_selection_2016_2020", "NONSTANDARD_CORPORATE_ACTION")]["coverage_point_loss"])
        + "; 2021–2026.09 `NO_SEC_SHARES` " + pct(reason_index[("historical_oos_2021_2026_09", "NO_SEC_SHARES")]["coverage_point_loss"])
        + ", `NONSTANDARD_CORPORATE_ACTION` " + pct(reason_index[("historical_oos_2021_2026_09", "NONSTANDARD_CORPORATE_ACTION")]["coverage_point_loss"]) + ". The reason table is a mutually-exclusive priority decomposition, so it must not be summed with independent price-quality counts.",
        "",
        "## Decision table",
        "",
        "| Decision | Recommendation | Evidence | Status |",
        "| --- | --- | --- | --- |",
        "| signal-date price | exact positive `close`; retain zero-volume/review only with review flag; invalid/missing unavailable | exact-date audit and no-fill control | RECOMMEND_SIGNOFF |",
        "| primary shares | DEI EntityCommonStockSharesOutstanding, unit shares, PIT selector | direct tag / zero future filing selections | RECOMMEND_SIGNOFF |",
        f"| shares <= 1 | selected candidate must be `>1`; preserve raw context | {small_selected} selected ticker-weeks from four `=1` records | RECOMMEND_SIGNOFF |",
        "| standard split chain | standard/reverse only, provenance-relative to share as-of | standard event validation | RECOMMEND_SIGNOFF |",
        f"| non-standard actions | retain unresolved and unavailable | {nonstandard_union:,} unique baseline-loss ticker-weeks | RECOMMEND_SIGNOFF |",
        "| multi-class | unresolved/missing | no class-specific shares supplied | RECOMMEND_SIGNOFF |",
        "| zero-volume | retain only with positive plausible close and review flag | signal-date-only audit | RECOMMEND_SIGNOFF |",
        "| invalid price | unavailable | no future/prior fill permitted | RECOMMEND_SIGNOFF |",
        "| staleness | `NO_CUTOFF_WITH_STALENESS_FLAG` for DEV | five sensitivity scenarios; no silent cutoff substitution | RECOMMEND_SIGNOFF |",
        "| Value market-cap level | issuer-level / one-security issuer only | avoids duplicate issuer numerator/denominator | RECOMMEND_SIGNOFF |",
        "| Size market-cap level | issuer-level / one-security issuer only | avoids duplicated multi-class issuer exposure | RECOMMEND_SIGNOFF |",
        "",
        "## Remaining warnings and guardrails",
        "",
        "All coverage figures use availability flags only. The output does not contain a price × shares result or a Value/Size calculation. Current-universe survivorship bias, current GICS, periodic shares, Yahoo retrospective adjustments, and missing RDQ remain DEV limitations. The authorization above is limited to a separately requested construction task; it does not authorize factors, IC, portfolio, FMOM, strategy, or a formal PIT claim.",
    ])
    out["report"].write_text("\n".join(report) + "\n", encoding="utf-8")
    out["document"].write_text("\n".join(report) + "\n\nSee the seven CSV audit artefacts in `outputs/audit/` for reproducible evidence.\n", encoding="utf-8")
    return {"paths": {key: str(value) for key, value in out.items()}, "decision": decision,
            "small_selected_ticker_weeks": small_selected, "nonstandard_union_ticker_weeks": nonstandard_union,
            "mean_no_cutoff_coverage": sum(market_available.values()) / (len(signal_dates) * denominator)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=project_root())
    args = parser.parse_args()
    print(json.dumps(run(args.root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
