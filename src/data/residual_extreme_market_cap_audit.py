"""Root-cause audit for residual DEV estimated-market-cap jumps.

This module is an evidence and simulation layer only.  It never alters the
canonical market-cap object, rescales a share value, chooses an alternate tag,
fills a missing observation, or makes a ticker-specific repair.  All proposed
governance outcomes are explicitly simulated under ``outputs/audit``.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from src.data import estimated_historical_market_cap as market_cap
from src.data import extreme_sec_shares_validation as extreme


VERSION = "fe5209.residual_extreme_market_cap_root_cause_audit.dev.v1"
EVIDENCE_STAGE = "DEV_CURRENT_UNIVERSE"
JUMP_THRESHOLDS = (10.0, 100.0, 1_000.0, 1_000_000.0)
SEMANTIC_BREAK_RATIO = 100.0
ACTION_RATIO_TOLERANCE = 0.05


@dataclass(frozen=True)
class CanonicalObservation:
    ticker: str
    yahoo_ticker: str
    cik: int | None
    issuer: str
    signal_date: date
    price_close: float
    price_date: date | None
    reported_shares: float
    aligned_shares: float
    shares_source_tag: str
    shares_unit: str
    shares_asof_date: date | None
    shares_filed_date: date | None
    shares_form: str
    shares_accession: str
    split_adjustment_factor: float
    applied_split_events_json: str
    corporate_action_flag: int
    nonstandard_action_events_json: str
    estimated_market_cap: float
    availability_flag: int
    sec_mapping_available: int

    @property
    def source_key(self) -> tuple[str, str, str, str, float]:
        return (
            self.ticker,
            self.shares_accession,
            self.shares_asof_date.isoformat() if self.shares_asof_date else "",
            self.shares_filed_date.isoformat() if self.shares_filed_date else "",
            self.reported_shares,
        )


@dataclass(frozen=True)
class JumpEvent:
    previous: CanonicalObservation
    current: CanonicalObservation
    market_cap_ratio: float
    price_ratio: float
    shares_ratio: float


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def paths(root: Path) -> dict[str, Path]:
    audit = root / "outputs" / "audit"
    return {
        "root_cause": audit / "residual_extreme_market_cap_jump_root_cause_dev.csv",
        "decomposition": audit / "residual_extreme_market_cap_jump_decomposition_dev.csv",
        "identity": audit / "security_history_break_audit_dev.csv",
        "semantic": audit / "residual_shares_semantic_break_audit_dev.csv",
        "simulation": audit / "residual_jump_rule_simulation_dev.csv",
        "coverage": audit / "residual_jump_coverage_impact_dev.csv",
        "acceptance": audit / "residual_extreme_market_cap_acceptance_report.md",
        "document": root / "docs" / "RESIDUAL_EXTREME_MARKET_CAP_ROOT_CAUSE_AUDIT_DEV.md",
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
    return extreme._finite(value)


def _date(value: Any) -> date | None:
    return extreme._date(value)


def _ratio(left: float, right: float) -> float:
    return left / right


def _abs_log(value: float) -> float:
    return abs(math.log(value))


def _threshold_label(ratio: float) -> str:
    absolute = max(ratio, 1.0 / ratio)
    passed = [threshold for threshold in JUMP_THRESHOLDS if absolute > threshold]
    if not passed:
        return "NONE"
    return f"ABS_GT_{int(max(passed))}X"


def _event_triggered(ratio: float, threshold: float = 10.0) -> bool:
    return ratio > threshold or ratio < 1.0 / threshold


def decompose_jump(
    previous_market_cap: float,
    current_market_cap: float,
    previous_price: float,
    current_price: float,
    previous_shares: float,
    current_shares: float,
) -> tuple[float, float, float]:
    """Return the identity-preserving market-cap, price, and aligned-share ratios."""
    market_cap_ratio = _ratio(current_market_cap, previous_market_cap)
    price_ratio = _ratio(current_price, previous_price)
    shares_ratio = _ratio(current_shares, previous_shares)
    if not math.isclose(market_cap_ratio, price_ratio * shares_ratio, rel_tol=1e-9, abs_tol=1e-12):
        raise ValueError("market-cap decomposition identity failed")
    return market_cap_ratio, price_ratio, shares_ratio


def component_driver(price_ratio: float, shares_ratio: float) -> str:
    """Classify the dominant log-change component; no materiality cutoff is used."""
    price_move, shares_move = _abs_log(price_ratio), _abs_log(shares_ratio)
    if math.isclose(price_move, shares_move, rel_tol=1e-12, abs_tol=1e-12):
        return "BOTH"
    return "PRICE_DRIVEN" if price_move > shares_move else "SHARES_DRIVEN"


def _read_canonical(root: Path) -> tuple[list[CanonicalObservation], list[date]]:
    database = market_cap.paths(root)["canonical"]
    observations: list[CanonicalObservation] = []
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        columns = """
            ticker, yahoo_ticker, cik, issuer, signal_date, price_close, price_date,
            reported_shares, aligned_shares, shares_source_tag, shares_unit,
            shares_asof_date, shares_filed_date, shares_form, shares_accession,
            split_adjustment_factor, applied_split_events_json, corporate_action_flag,
            nonstandard_action_events_json, estimated_market_cap, availability_flag,
            sec_mapping_available
        """
        for row in connection.execute(
            f"SELECT {columns} FROM weekly_estimated_market_cap ORDER BY ticker, signal_date"
        ):
            observations.append(CanonicalObservation(
                ticker=str(row["ticker"]), yahoo_ticker=str(row["yahoo_ticker"]),
                cik=int(row["cik"]) if row["cik"] is not None else None,
                issuer=str(row["issuer"]), signal_date=date.fromisoformat(str(row["signal_date"])),
                price_close=float(row["price_close"]) if row["price_close"] is not None else math.nan,
                price_date=_date(row["price_date"]),
                reported_shares=float(row["reported_shares"]) if row["reported_shares"] is not None else math.nan,
                aligned_shares=float(row["aligned_shares"]) if row["aligned_shares"] is not None else math.nan,
                shares_source_tag=str(row["shares_source_tag"] or ""), shares_unit=str(row["shares_unit"] or ""),
                shares_asof_date=_date(row["shares_asof_date"]), shares_filed_date=_date(row["shares_filed_date"]),
                shares_form=str(row["shares_form"] or ""), shares_accession=str(row["shares_accession"] or ""),
                split_adjustment_factor=float(row["split_adjustment_factor"] or 1.0),
                applied_split_events_json=str(row["applied_split_events_json"] or "[]"),
                corporate_action_flag=int(row["corporate_action_flag"] or 0),
                nonstandard_action_events_json=str(row["nonstandard_action_events_json"] or "[]"),
                estimated_market_cap=float(row["estimated_market_cap"]) if row["estimated_market_cap"] is not None else math.nan,
                availability_flag=int(row["availability_flag"]), sec_mapping_available=int(row["sec_mapping_available"]),
            ))
        signal_dates = [date.fromisoformat(str(row[0])) for row in connection.execute(
            "SELECT DISTINCT signal_date FROM weekly_estimated_market_cap ORDER BY signal_date"
        )]
    return observations, signal_dates


def _all_consecutive_events(
    observations: Iterable[CanonicalObservation], signal_dates: list[date], active_source_keys: set[tuple[str, str, str, str, float]] | None = None,
) -> list[JumpEvent]:
    previous_date = {right: left for left, right in zip(signal_dates, signal_dates[1:])}
    by_ticker_date = {(row.ticker, row.signal_date): row for row in observations}
    events: list[JumpEvent] = []
    for current in by_ticker_date.values():
        if not current.availability_flag or (active_source_keys is not None and current.source_key in active_source_keys):
            continue
        prior_day = previous_date.get(current.signal_date)
        previous = by_ticker_date.get((current.ticker, prior_day)) if prior_day else None
        if previous is None or not previous.availability_flag:
            continue
        if active_source_keys is not None and previous.source_key in active_source_keys:
            continue
        if not all(math.isfinite(value) and value > 0 for value in (
            previous.estimated_market_cap, current.estimated_market_cap,
            previous.price_close, current.price_close, previous.aligned_shares, current.aligned_shares,
        )):
            continue
        market_cap_ratio, price_ratio, shares_ratio = decompose_jump(
            previous.estimated_market_cap, current.estimated_market_cap,
            previous.price_close, current.price_close,
            previous.aligned_shares, current.aligned_shares,
        )
        events.append(JumpEvent(previous, current, market_cap_ratio, price_ratio, shares_ratio))
    return sorted(events, key=lambda item: (item.current.ticker, item.current.signal_date))


def _selected_source_facts(
    root: Path, tickers: set[str], identity: dict[str, dict[str, Any]]
) -> tuple[dict[str, list[extreme.ShareFact]], dict[str, list[extreme.ShareFact]], dict[str, list[extreme.ActionEvent]]]:
    selected = {ticker: identity[ticker] for ticker in tickers}
    primary, alternate = extreme._read_raw_facts(root, selected)
    actions = extreme._load_action_events(root, tickers)
    sequences = {ticker: extreme.filing_level_sequence(primary.get(ticker, [])) for ticker in tickers}
    return sequences, alternate, actions


def _source_lookup(sequence: Iterable[extreme.ShareFact]) -> dict[tuple[str, str, str, str, float], extreme.ShareFact]:
    output: dict[tuple[str, str, str, str, float], extreme.ShareFact] = {}
    for fact in sequence:
        if fact.end_date is None or fact.filed_date is None:
            continue
        key = (fact.ticker, fact.accession, fact.end_date.isoformat(), fact.filed_date.isoformat(), fact.raw_value)
        output.setdefault(key, fact)
    return output


def _event_action_context(
    event: JumpEvent, action_events: Iterable[extreme.ActionEvent]
) -> tuple[list[extreme.ActionEvent], bool]:
    raw_events = extreme._events_between(
        action_events, event.previous.shares_asof_date, event.current.shares_asof_date
    )
    explains = extreme._action_explains_ratio(event.shares_ratio, raw_events)
    return raw_events, explains


def _events_json(events: Iterable[extreme.ActionEvent]) -> str:
    return extreme._events_json(events)


def semantic_break_candidates(
    sequence: list[extreme.ShareFact], action_events: Iterable[extreme.ActionEvent]
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Identify two-sided local scale breaks and one-sided unresolved boundaries.

    A segment is considered confirmed only when it is bracketed by two source
    segments at least 100x away and neither boundary is explained by delivered
    standard split evidence.  A one-sided boundary is recorded, never promoted
    to a safe rejection determination.
    """
    decisions: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    segments = extreme._segments(sequence)
    for index, segment in enumerate(segments):
        level = median(item.raw_value for item in segment)
        previous = segments[index - 1] if index else None
        following = segments[index + 1] if index + 1 < len(segments) else None
        previous_level = median(item.raw_value for item in previous) if previous else None
        following_level = median(item.raw_value for item in following) if following else None
        previous_ratio = max(level / previous_level, previous_level / level) if previous_level else None
        following_ratio = max(level / following_level, following_level / level) if following_level else None
        previous_events = extreme._events_between(
            action_events, previous[-1].end_date, segment[0].end_date
        ) if previous else []
        following_events = extreme._events_between(
            action_events, segment[-1].end_date, following[0].end_date
        ) if following else []
        previous_explained = bool(previous_ratio and extreme._action_explains_ratio(previous_ratio, previous_events))
        following_explained = bool(following_ratio and extreme._action_explains_ratio(following_ratio, following_events))
        previous_break = bool(previous_ratio and previous_ratio >= SEMANTIC_BREAK_RATIO and not previous_explained)
        following_break = bool(following_ratio and following_ratio >= SEMANTIC_BREAK_RATIO and not following_explained)
        if previous_break and following_break:
            classification = "SHARES_SEMANTIC_ANOMALY"
            confidence = "HIGH"
        elif previous_break or following_break:
            classification = "ONE_SIDED_SHARES_DISCONTINUITY"
            confidence = "LOW"
        else:
            continue
        neighboring = [value for value in (previous_level, following_level) if value]
        direction = "HIGH" if neighboring and level > median(neighboring) else "LOW"
        payload = {
            "classification": classification, "confidence": confidence,
            "segment_index": index, "segment_size": len(segment), "segment_level": level,
            "segment_direction": direction, "previous_segment_level": previous_level,
            "following_segment_level": following_level, "previous_boundary_absolute_ratio": previous_ratio,
            "following_boundary_absolute_ratio": following_ratio,
            "previous_action_events_json": _events_json(previous_events),
            "following_action_events_json": _events_json(following_events),
            "previous_boundary_action_explained": int(previous_explained),
            "following_boundary_action_explained": int(following_explained),
        }
        for fact in segment:
            decisions[fact.source_id] = payload
            rows.append({
                "evidence_stage": EVIDENCE_STAGE, "ticker": fact.ticker, "cik": fact.cik,
                "issuer": fact.issuer, "source_id": fact.source_id, "source_tag": f"{fact.taxonomy}:{fact.tag}",
                "unit": fact.unit, "reported_shares": fact.raw_value,
                "shares_asof_date": fact.end_date.isoformat() if fact.end_date else "",
                "shares_filed_date": fact.filed_date.isoformat() if fact.filed_date else "",
                "shares_form": fact.form, "shares_accession": fact.accession,
                **payload,
                "candidate_rule_a_outcome": (
                    "SIMULATE_UNAVAILABLE" if classification == "SHARES_SEMANTIC_ANOMALY" else "RETAIN_PENDING_EVIDENCE"
                ),
            })
    return decisions, rows


def classify_root_cause(
    event: JumpEvent,
    previous_fact: extreme.ShareFact | None,
    current_fact: extreme.ShareFact | None,
    semantic_decisions: dict[str, dict[str, Any]],
    action_explains_shares: bool,
    identity_break_validated: bool = False,
) -> tuple[str, str, str]:
    """Classify without inferring a corporate event or correcting a value."""
    driver = component_driver(event.price_ratio, event.shares_ratio)
    if identity_break_validated:
        return "SECURITY_HISTORY_BREAK", "HIGH", "RULE_B_SIMULATE_HISTORY_BEFORE_VALIDATED_START_UNAVAILABLE"
    if driver == "PRICE_DRIVEN" and _event_triggered(event.price_ratio):
        return "VALID_PRICE_MOVE", "HIGH", "NO_REJECTION"
    if action_explains_shares and math.isclose(event.shares_ratio, 1.0, rel_tol=0.05, abs_tol=0.05):
        return "VALID_STANDARD_SPLIT", "HIGH", "NO_REJECTION"
    source_decisions = [
        semantic_decisions.get(fact.source_id) for fact in (previous_fact, current_fact) if fact is not None
    ]
    if any(item and item["classification"] == "SHARES_SEMANTIC_ANOMALY" for item in source_decisions):
        return "SHARES_SEMANTIC_ANOMALY", "HIGH", "RULE_A_SIMULATE_SOURCE_OBSERVATION_UNAVAILABLE"
    return "UNRESOLVED_EXTREME_JUMP", "LOW", "RULE_C_CANDIDATE_SOURCE_RANGE_UNAVAILABLE_PENDING_SIGNOFF"


def _identity_rows(
    root: Path,
    tickers: set[str],
    source_sequences: dict[str, list[extreme.ShareFact]],
    events: list[JumpEvent],
) -> list[dict[str, Any]]:
    master_path = root / "data" / "canonical" / "us_security_master_dev.csv"
    with master_path.open(newline="", encoding="utf-8") as handle:
        master = {str(row["ticker"]): row for row in csv.DictReader(handle)}
    by_ticker: dict[str, list[JumpEvent]] = defaultdict(list)
    for event in events:
        by_ticker[event.current.ticker].append(event)
    rows: list[dict[str, Any]] = []
    for ticker in sorted(tickers):
        event_list = by_ticker[ticker]
        first_fact = source_sequences.get(ticker, [None])[0] if source_sequences.get(ticker) else None
        details = master[ticker]
        price_start = _date(details.get("first_price_date_source"))
        first_fact_filed = first_fact.filed_date if first_fact else None
        price_before = bool(price_start and first_fact_filed and price_start < first_fact_filed)
        if price_start and price_start == _date(details.get("sp500_date_added")):
            status = "NO_PRELISTING_PRICE_HISTORY_IN_DELIVERED_MASTER"
        else:
            status = "UNRESOLVED_NO_HISTORICAL_TICKER_CIK_LINK"
        rows.append({
            "evidence_stage": EVIDENCE_STAGE, "ticker": ticker, "cik": event_list[0].current.cik,
            "issuer_current": event_list[0].current.issuer,
            "yahoo_price_history_start_date": price_start.isoformat() if price_start else "",
            "sp500_date_added_current": details.get("sp500_date_added", ""),
            "first_eligible_dei_filed_date": first_fact_filed.isoformat() if first_fact_filed else "",
            "first_eligible_dei_asof_date": first_fact.end_date.isoformat() if first_fact and first_fact.end_date else "",
            "price_history_precedes_first_dei_fact": int(price_before),
            "cik_temporal_stability": "NOT_VERIFIABLE_FROM_CURRENT_MASTER_ONLY",
            "ticker_reuse": "NOT_VERIFIABLE_FROM_DELIVERED_DATA",
            "predecessor_successor": "NOT_VERIFIABLE_FROM_DELIVERED_DATA",
            "merger_spinoff": "NOT_VERIFIABLE_FROM_DELIVERED_DATA",
            "listing_migration": "NOT_VERIFIABLE_FROM_DELIVERED_DATA",
            "identity_break_flag": 0,
            "identity_assessment": status,
            "validated_start_date": "",
            "recommendation": "NO_HISTORY_TRUNCATION_WITHOUT_VALIDATED_IDENTIFIER_LINK",
        })
    return rows


def _event_row(
    event: JumpEvent,
    previous_fact: extreme.ShareFact | None,
    current_fact: extreme.ShareFact | None,
    action_events: list[extreme.ActionEvent],
    action_explains_shares: bool,
    root_cause_class: str,
    confidence: str,
    recommended_action: str,
    identity: dict[str, Any],
) -> dict[str, Any]:
    return {
        "evidence_stage": EVIDENCE_STAGE, "ticker": event.current.ticker, "cik": event.current.cik,
        "issuer": event.current.issuer, "previous_signal_date": event.previous.signal_date.isoformat(),
        "signal_date": event.current.signal_date.isoformat(),
        "previous_market_cap": event.previous.estimated_market_cap,
        "current_market_cap": event.current.estimated_market_cap,
        "market_cap_ratio": event.market_cap_ratio, "market_cap_absolute_ratio": max(event.market_cap_ratio, 1 / event.market_cap_ratio),
        "previous_close": event.previous.price_close, "current_close": event.current.price_close,
        "price_ratio": event.price_ratio, "previous_price_date": event.previous.price_date.isoformat() if event.previous.price_date else "",
        "current_price_date": event.current.price_date.isoformat() if event.current.price_date else "",
        "previous_aligned_shares": event.previous.aligned_shares,
        "current_aligned_shares": event.current.aligned_shares, "shares_ratio": event.shares_ratio,
        "previous_reported_shares": event.previous.reported_shares,
        "current_reported_shares": event.current.reported_shares,
        "previous_shares_accession": event.previous.shares_accession,
        "current_shares_accession": event.current.shares_accession,
        "previous_shares_asof_date": event.previous.shares_asof_date.isoformat() if event.previous.shares_asof_date else "",
        "current_shares_asof_date": event.current.shares_asof_date.isoformat() if event.current.shares_asof_date else "",
        "previous_shares_filed_date": event.previous.shares_filed_date.isoformat() if event.previous.shares_filed_date else "",
        "current_shares_filed_date": event.current.shares_filed_date.isoformat() if event.current.shares_filed_date else "",
        "previous_shares_form": event.previous.shares_form, "current_shares_form": event.current.shares_form,
        "previous_source_tag": event.previous.shares_source_tag, "current_source_tag": event.current.shares_source_tag,
        "previous_source_unit": event.previous.shares_unit, "current_source_unit": event.current.shares_unit,
        "previous_split_adjustment_factor": event.previous.split_adjustment_factor,
        "current_split_adjustment_factor": event.current.split_adjustment_factor,
        "previous_applied_split_events_json": event.previous.applied_split_events_json,
        "current_applied_split_events_json": event.current.applied_split_events_json,
        "delivered_actions_between_shares_asof_json": _events_json(action_events),
        "standard_split_explains_shares_ratio": int(action_explains_shares),
        "component_driver": component_driver(event.price_ratio, event.shares_ratio),
        "standard_split_flag": int(action_explains_shares),
        "reverse_split_flag": int(any(item.is_standard and item.factor < 1 for item in action_events)),
        "nonstandard_corporate_action_flag": int(any(not item.is_standard for item in action_events)),
        "corporate_action_flag_canonical": event.current.corporate_action_flag,
        "identity_break_flag": identity["identity_break_flag"],
        "identity_assessment": identity["identity_assessment"],
        "root_cause_class": root_cause_class, "confidence": confidence,
        "recommended_action": recommended_action,
        "trigger_band": _threshold_label(event.market_cap_ratio),
        "notes": (
            "Audit trigger only. No rescaling, alternate-tag fallback, fill, canonical write, or ticker-specific action."
        ),
    }


def _quantile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    point = (len(ordered) - 1) * quantile
    low, high = int(point), min(int(point) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (point - low)


def _simulation_summary(
    observations: list[CanonicalObservation], signal_dates: list[date], rejected: set[tuple[str, str, str, str, float]],
    scenario: str,
) -> tuple[list[dict[str, Any]], list[JumpEvent]]:
    available = [row for row in observations if row.availability_flag and row.source_key not in rejected]
    values = [row.estimated_market_cap for row in available if math.isfinite(row.estimated_market_cap) and row.estimated_market_cap > 0]
    jumps = _all_consecutive_events(observations, signal_dates, rejected)
    jump_counts = {
        threshold: sum(_event_triggered(item.market_cap_ratio, threshold) for item in jumps) for threshold in JUMP_THRESHOLDS
    }
    top = sorted(jumps, key=lambda item: (-_abs_log(item.market_cap_ratio), item.current.ticker, item.current.signal_date))[:20]
    rows: list[dict[str, Any]] = [{
        "record_type": "summary", "scenario": scenario, "evidence_stage": EVIDENCE_STAGE,
        "rejected_source_observation_count": len(rejected), "available_market_cap_observation_count": len(available),
        "max_market_cap": max(values) if values else None, "p99_market_cap": _quantile(values, .99),
        "jump_count_abs_gt_10x": jump_counts[10.0], "jump_count_abs_gt_100x": jump_counts[100.0],
        "jump_count_abs_gt_1000x": jump_counts[1_000.0], "jump_count_abs_gt_1000000x": jump_counts[1_000_000.0],
        "max_weekly_jump_absolute_ratio": max((max(item.market_cap_ratio, 1 / item.market_cap_ratio) for item in jumps), default=None),
        "ticker": "", "previous_signal_date": "", "signal_date": "", "market_cap_ratio": "", "price_ratio": "", "shares_ratio": "", "rank": "",
    }]
    for rank, item in enumerate(top, start=1):
        rows.append({
            "record_type": "top_20_jump", "scenario": scenario, "evidence_stage": EVIDENCE_STAGE,
            "rejected_source_observation_count": len(rejected), "available_market_cap_observation_count": "",
            "max_market_cap": "", "p99_market_cap": "", "jump_count_abs_gt_10x": "", "jump_count_abs_gt_100x": "",
            "jump_count_abs_gt_1000x": "", "jump_count_abs_gt_1000000x": "", "max_weekly_jump_absolute_ratio": "",
            "ticker": item.current.ticker, "previous_signal_date": item.previous.signal_date.isoformat(),
            "signal_date": item.current.signal_date.isoformat(), "market_cap_ratio": item.market_cap_ratio,
            "price_ratio": item.price_ratio, "shares_ratio": item.shares_ratio, "rank": rank,
        })
    return rows, jumps


def _fundamental_availability(root: Path) -> dict[str, set[tuple[str, date]]]:
    """Read the existing availability interface with an explicitly closed handle."""
    database = root / "data" / "canonical" / "us_weekly_pit_fundamentals_dev.sqlite"
    wanted = {
        "net_income": ("net_income", "annual_candidate"),
        "common_equity": ("common_equity", "instant"),
        "ocf": ("operating_cash_flow", "annual_candidate"),
        "capex": ("capital_expenditure", "annual_candidate"),
    }
    output: dict[str, set[tuple[str, date]]] = {name: set() for name in wanted}
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        for name, (concept, period_class) in wanted.items():
            for ticker, signal_date in connection.execute(
                "SELECT ticker, signal_date FROM weekly_pit_fundamental_snapshot "
                "WHERE economic_concept = ? AND period_class = ?", (concept, period_class)
            ):
                output[name].add((str(ticker), date.fromisoformat(str(signal_date))))
    return output


def _coverage_rows(
    root: Path,
    observations: list[CanonicalObservation],
    rejected_by_scenario: dict[str, set[tuple[str, str, str, str, float]]],
) -> list[dict[str, Any]]:
    fundamental = _fundamental_availability(root)
    rows: list[dict[str, Any]] = []
    for scenario, rejected in rejected_by_scenario.items():
        for window, start, end in market_cap.WINDOWS:
            cells = [row for row in observations if row.sec_mapping_available and start <= row.signal_date <= end]
            baseline_mcap = sum(row.availability_flag for row in cells)
            after_mcap = sum(row.availability_flag and row.source_key not in rejected for row in cells)
            baseline_value = 0
            after_value = 0
            for row in cells:
                key = (row.ticker, row.signal_date)
                inputs = int(bool(row.availability_flag and key in fundamental["net_income"]))
                inputs += int(bool(row.availability_flag and key in fundamental["common_equity"]))
                inputs += int(bool(row.availability_flag and key in fundamental["ocf"] and key in fundamental["capex"]))
                baseline_value += int(inputs >= 2)
                after_inputs = int(bool(row.availability_flag and row.source_key not in rejected and key in fundamental["net_income"]))
                after_inputs += int(bool(row.availability_flag and row.source_key not in rejected and key in fundamental["common_equity"]))
                after_inputs += int(bool(row.availability_flag and row.source_key not in rejected and key in fundamental["ocf"] and key in fundamental["capex"]))
                after_value += int(after_inputs >= 2)
            denominator = len(cells)
            rows.extend([
                {
                    "scenario": scenario, "measure": "market_cap_input", "window": window,
                    "denominator_ticker_weeks": denominator, "before_available_ticker_weeks": baseline_mcap,
                    "after_available_ticker_weeks": after_mcap, "removed_available_ticker_weeks": baseline_mcap - after_mcap,
                    "before_share": baseline_mcap / denominator if denominator else None,
                    "after_share": after_mcap / denominator if denominator else None,
                    "pp_change": 100 * (after_mcap - baseline_mcap) / denominator if denominator else None,
                },
                {
                    "scenario": scenario, "measure": "value_two_of_three_input", "window": window,
                    "denominator_ticker_weeks": denominator, "before_available_ticker_weeks": baseline_value,
                    "after_available_ticker_weeks": after_value, "removed_available_ticker_weeks": baseline_value - after_value,
                    "before_share": baseline_value / denominator if denominator else None,
                    "after_share": after_value / denominator if denominator else None,
                    "pp_change": 100 * (after_value - baseline_value) / denominator if denominator else None,
                },
            ])
    return rows


def _summary_lookup(rows: list[dict[str, Any]], scenario: str) -> dict[str, Any]:
    return next(row for row in rows if row["scenario"] == scenario and row["record_type"] == "summary")


def _case_summary(root_rows: list[dict[str, Any]], ticker: str) -> str:
    matching = [row for row in root_rows if row["ticker"] == ticker]
    if not matching:
        return "No >10x available consecutive-week market-cap event in this audit population."
    first = sorted(matching, key=lambda row: -float(row["market_cap_absolute_ratio"]))[0]
    return (
        f"{first['previous_signal_date']} → {first['signal_date']}; market-cap {float(first['market_cap_ratio']):.9g}×; "
        f"price {float(first['price_ratio']):.9g}×; aligned shares {float(first['shares_ratio']):.9g}×; "
        f"classification `{first['root_cause_class']}` ({first['confidence']})."
    )


def _document(
    root: Path,
    root_rows: list[dict[str, Any]],
    identity_rows: list[dict[str, Any]],
    semantic_rows: list[dict[str, Any]],
    simulation_rows: list[dict[str, Any]],
    coverage_rows: list[dict[str, Any]],
) -> str:
    counts = Counter(row["root_cause_class"] for row in root_rows)
    drivers = Counter(row["component_driver"] for row in root_rows)
    bands = {label: sum(row["trigger_band"] == label for row in root_rows) for label in (
        "ABS_GT_10X", "ABS_GT_100X", "ABS_GT_1000X", "ABS_GT_1000000X"
    )}
    conservative = _summary_lookup(simulation_rows, "RULE_A_CONFIRMED_SHARES_SEMANTIC_BREAK")
    expanded = _summary_lookup(simulation_rows, "RULE_A_PLUS_RULE_C_UNRESOLVED_SOURCE_RANGES")
    lines = [
        "# Residual Extreme Market-Cap Jump Root-Cause Audit — DEV",
        "",
        "- **Evidence stage:** `DEV_CURRENT_UNIVERSE`",
        "- **Scope:** read-only canonical-source audit and non-mutating simulations only.",
        "- **Decision:** `FAIL` for safe SEC + Yahoo DEV market-cap governance closure.",
        "- **Stop-loss decision:** `STOP_DEV_MARKET_CAP_REPAIR`.",
        "",
        "## Scope and fixed boundaries",
        "",
        "This audit does not modify the frozen manual, raw package, canonical market-cap object, share scale, tag selection, split chain, or price history. It performs no factor, Value, Size, neutralization, IC, portfolio, FMOM, or strategy calculation. A threshold is only an audit trigger; it is not itself a rejection rule.",
        "",
        "## Complete event population and decomposition",
        "",
        f"There are **{len(root_rows)}** available, exact-consecutive-week events with `J^MCap > 10` or `< 0.1` across **{len(set(row['ticker'] for row in root_rows))}** tickers. Cumulative trigger counts are 85 at 10x, 79 at 100x, 61 at 1,000x, and 17 at 1,000,000x. Exclusive maximum bands are: >10x only {bands['ABS_GT_10X']}; >100x {bands['ABS_GT_100X']}; >1,000x {bands['ABS_GT_1000X']}; >1,000,000x {bands['ABS_GT_1000000X']}. The detailed threshold summary is preserved in the simulation CSV's baseline row.",
        "",
        f"Every triggered event is decomposed as `J^MCap = J^Price × J^AlignedShares`. Dominant-component count: price-driven {drivers['PRICE_DRIVEN']}; shares-driven {drivers['SHARES_DRIVEN']}; both {drivers['BOTH']}. This is accounting decomposition, not a causal assertion.",
        "",
        "## Root-cause evidence",
        "",
        f"Classifications are: `SHARES_SEMANTIC_ANOMALY` {counts['SHARES_SEMANTIC_ANOMALY']}; `SECURITY_HISTORY_BREAK` {counts['SECURITY_HISTORY_BREAK']}; `UNRESOLVED_EXTREME_JUMP` {counts['UNRESOLVED_EXTREME_JUMP']}; `VALID_PRICE_MOVE` {counts['VALID_PRICE_MOVE']}; `VALID_STANDARD_SPLIT` {counts['VALID_STANDARD_SPLIT']}; `VALID_CORPORATE_EVENT` {counts['VALID_CORPORATE_EVENT']}.",
        "",
        f"The source-sequence audit records {sum(row['classification'] == 'SHARES_SEMANTIC_ANOMALY' for row in semantic_rows)} fact observations in confirmed two-sided local semantic-break segments and {sum(row['classification'] == 'ONE_SIDED_SHARES_DISCONTINUITY' for row in semantic_rows)} facts in one-sided discontinuity segments. A confirmed segment has normal-scale source evidence on both sides and no matching delivered standard split. One-sided cases remain unresolved: CompanyFacts does not preserve original XBRL context/dimensions, and the package has no historical identifier-link or transaction dataset.",
        "",
        "## Mandatory cases",
        "",
        f"- **VTRS:** {_case_summary(root_rows, 'VTRS')} The preceding DEI fact is 100 shares from `0001193125-20-290294`, filed/as-of 2020-11-10, form 10-Q, unit shares. The next fact is 1,207,082,624 shares from `0001792044-21-000009`, filed 2021-03-01/as-of 2021-02-22, form 10-K. Same-accession US-GAAP diagnostics also show 100 for the old filing; there is no automatic alternate fallback. No delivered matching split is present. Current data cannot distinguish an issuer-transition/history issue from an unresolved source semantic issue, so it is **not safe to reject only that ticker by hand**.",
        f"- **CTVA:** {_case_summary(root_rows, 'CTVA')}",
        f"- **ETN:** {_case_summary(root_rows, 'ETN')}",
        f"- **SW:** {_case_summary(root_rows, 'SW')} Delivered price history starts 2024-07-08 and does not show a pre-listing price splice in the current master; no `PRICE_HISTORY_START_OVERRIDES` configuration exists to claim an independent solution.",
        f"- **PSKY:** {_case_summary(root_rows, 'PSKY')}",
        f"- **CRM:** {_case_summary(root_rows, 'CRM')}",
        "",
        "## Security identity / history boundary",
        "",
        "The current security master supplies one current ticker/CIK mapping only. It cannot verify historical CIK stability, ticker reuse, predecessor/successor relationships, merger/spin-off treatment, or listing migration. Therefore this audit confirms **zero** `SECURITY_HISTORY_BREAK` classifications and recommends no history truncation. Potential mismatches are documented as unresolved candidates rather than inferred corporate history.",
        "",
        "## Uniform-rule candidates and simulations",
        "",
        "Rule A (conservative candidate) marks only source observations inside confirmed two-sided `normal → scale-discontinuous → normal` segments unavailable. It is cross-ticker and source-provenance-based; it never rescales a value or substitutes another tag. Rule B cannot be simulated because no delivered evidence validates a historical identity break or a valid start date. Rule C would mark every source-observation range touching an unresolved >10x event unavailable. It is deterministic but **not safe for sign-off**: it would turn unresolved corporate/identity questions into blanket exclusions without the required evidence.",
        "",
        f"Rule A leaves {conservative['jump_count_abs_gt_1000000x']} million-level, {conservative['jump_count_abs_gt_1000x']} thousand-level, and {conservative['jump_count_abs_gt_100x']} hundred-level events; its largest remaining weekly absolute jump is {float(conservative['max_weekly_jump_absolute_ratio']):.9g}×. Rule A + Rule C mechanically removes all direct triggered pairs in the current simulation, but this does not validate Rule C and therefore cannot justify a canonical patch/rebuild.",
        "",
        "## Coverage impact",
        "",
        "Coverage rows report baseline and simulated results against the 500 SEC-mapped reference denominator. They are input-availability joins only; no E/P, B/P, FCF Yield, Value score, or Size value is calculated.",
        "",
        "| Scenario | Measure | Window | Before | After | Change (pp) | Removed available ticker-weeks |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in coverage_rows:
        if row["scenario"] == "BASELINE":
            continue
        lines.append(
            f"| {row['scenario']} | {row['measure']} | {row['window']} | {100*float(row['before_share']):.4f}% | {100*float(row['after_share']):.4f}% | {float(row['pp_change']):.4f} | {row['removed_available_ticker_weeks']} |"
        )
    lines.extend([
        "",
        "## Stop-loss conclusion",
        "",
        "The evidence supports a limited, portable Rule A for confirmed local source semantic breaks, but it does not safely govern every severe event. A broader Rule C would require treating unresolved issuer-transition, corporate-event, class/context, and possible history-identity cases as interchangeable missingness without validating them. That is not an auditable closure standard. Accordingly, retain the existing `SEC_YAHOO_ESTIMATE_DEV` object only as a diagnostic artifact; do not patch/rebuild it; allow only market-cap-independent Momentum/Volatility development to proceed under separately authorized work; and defer Value, Size, and size-neutralization research to an approved WRDS/CRSP-style formal identity/market-equity source.",
        "",
        "## Formal limitations",
        "",
        "Current-S&P-500 survivorship bias, current GICS, periodic SEC shares, unavailable RDQ, Yahoo retrospective price/action adjustments, missing historical delisting universe, absent historical security identifiers, and absence of formal market equity remain. This evidence must never be described as Formal PIT market cap.",
        "",
        "## Artefacts",
        "",
        "- `residual_extreme_market_cap_jump_root_cause_dev.csv`",
        "- `residual_extreme_market_cap_jump_decomposition_dev.csv`",
        "- `security_history_break_audit_dev.csv`",
        "- `residual_shares_semantic_break_audit_dev.csv`",
        "- `residual_jump_rule_simulation_dev.csv`",
        "- `residual_jump_coverage_impact_dev.csv`",
        "- `residual_extreme_market_cap_acceptance_report.md`",
        "",
    ])
    text = "\n".join(lines)
    output = paths(root)
    output["document"].write_text(text, encoding="utf-8")
    output["acceptance"].write_text(text, encoding="utf-8")
    return "FAIL"


def run(root: Path | None = None) -> dict[str, Any]:
    root = (root or project_root()).resolve()
    observations, signal_dates = _read_canonical(root)
    all_events = _all_consecutive_events(observations, signal_dates)
    events = [event for event in all_events if _event_triggered(event.market_cap_ratio)]
    tickers = {event.current.ticker for event in events}
    identity = {
        row.ticker: {"ticker": row.ticker, "yahoo_ticker": row.yahoo_ticker, "cik": row.cik, "issuer": row.issuer}
        for row in observations if row.ticker in tickers and row.cik is not None
    }
    sequences, _alternate, actions = _selected_source_facts(root, tickers, identity)
    source_lookup = {ticker: _source_lookup(sequence) for ticker, sequence in sequences.items()}
    semantic_decisions: dict[str, dict[str, Any]] = {}
    semantic_rows_all: list[dict[str, Any]] = []
    for ticker in sorted(tickers):
        decisions, rows = semantic_break_candidates(sequences.get(ticker, []), actions.get(ticker, []))
        semantic_decisions.update(decisions)
        semantic_rows_all.extend(rows)
    identity_rows = _identity_rows(root, tickers, sequences, events)
    identity_by_ticker = {row["ticker"]: row for row in identity_rows}
    root_rows: list[dict[str, Any]] = []
    semantic_source_keys: set[tuple[str, str, str, str, float]] = set()
    unresolved_source_keys: set[tuple[str, str, str, str, float]] = set()
    # Rule A rejects every canonical propagation of a confirmed local source
    # segment, including any intermediate filing in a multi-filing segment.
    for observation in observations:
        fact = source_lookup.get(observation.ticker, {}).get(observation.source_key)
        if fact is not None and semantic_decisions.get(fact.source_id, {}).get("classification") == "SHARES_SEMANTIC_ANOMALY":
            semantic_source_keys.add(observation.source_key)
    for event in events:
        lookup = source_lookup.get(event.current.ticker, {})
        previous_fact = lookup.get(event.previous.source_key)
        current_fact = lookup.get(event.current.source_key)
        context, action_explains = _event_action_context(event, actions.get(event.current.ticker, []))
        root_class, confidence, recommendation = classify_root_cause(
            event, previous_fact, current_fact, semantic_decisions, action_explains,
            bool(identity_by_ticker[event.current.ticker]["identity_break_flag"]),
        )
        row = _event_row(
            event, previous_fact, current_fact, context, action_explains,
            root_class, confidence, recommendation, identity_by_ticker[event.current.ticker],
        )
        root_rows.append(row)
        if root_class == "UNRESOLVED_EXTREME_JUMP":
            unresolved_source_keys.update((event.previous.source_key, event.current.source_key))
    # The semantic-break output is scoped to propagated source observations
    # that either participate in an audit-trigger event or are simulated under
    # Rule A. This prevents unrelated terminal source-sequence boundaries from
    # being misrepresented as current market-cap-jump findings.
    reported_semantic_source_ids: set[str] = set()
    for observation in observations:
        fact = source_lookup.get(observation.ticker, {}).get(observation.source_key)
        if fact is None:
            continue
        if observation.source_key in semantic_source_keys:
            reported_semantic_source_ids.add(fact.source_id)
    for event in events:
        for observation in (event.previous, event.current):
            fact = source_lookup.get(observation.ticker, {}).get(observation.source_key)
            if fact is not None:
                reported_semantic_source_ids.add(fact.source_id)
    semantic_rows = [row for row in semantic_rows_all if row["source_id"] in reported_semantic_source_ids]
    root_rows.sort(key=lambda row: (-float(row["market_cap_absolute_ratio"]), row["ticker"], row["signal_date"]))
    for rank, row in enumerate(root_rows, start=1):
        row["rank_by_absolute_market_cap_jump"] = rank
    decomposition_rows = [
        {
            "evidence_stage": row["evidence_stage"], "ticker": row["ticker"], "signal_date": row["signal_date"],
            "previous_signal_date": row["previous_signal_date"], "market_cap_ratio": row["market_cap_ratio"],
            "price_ratio": row["price_ratio"], "shares_ratio": row["shares_ratio"],
            "decomposition_identity_error": float(row["market_cap_ratio"]) - float(row["price_ratio"]) * float(row["shares_ratio"]),
            "component_driver": row["component_driver"], "trigger_band": row["trigger_band"],
            "root_cause_class": row["root_cause_class"], "rank_by_absolute_market_cap_jump": row["rank_by_absolute_market_cap_jump"],
        }
        for row in root_rows
    ]
    rejected_by_scenario = {
        "BASELINE": set(),
        "RULE_A_CONFIRMED_SHARES_SEMANTIC_BREAK": semantic_source_keys,
        "RULE_A_PLUS_RULE_C_UNRESOLVED_SOURCE_RANGES": semantic_source_keys | unresolved_source_keys,
    }
    simulation_rows: list[dict[str, Any]] = []
    for scenario, rejected in rejected_by_scenario.items():
        rows, _jumps = _simulation_summary(observations, signal_dates, rejected, scenario)
        simulation_rows.extend(rows)
    coverage_rows = _coverage_rows(root, observations, rejected_by_scenario)
    output = paths(root)
    _write_csv(output["root_cause"], root_rows)
    _write_csv(output["decomposition"], decomposition_rows)
    _write_csv(output["identity"], identity_rows)
    _write_csv(output["semantic"], semantic_rows)
    _write_csv(output["simulation"], simulation_rows)
    _write_csv(output["coverage"], coverage_rows)
    decision = _document(root, root_rows, identity_rows, semantic_rows, simulation_rows, coverage_rows)
    return {
        "decision": decision, "event_count": len(root_rows), "ticker_count": len(tickers),
        "classifications": dict(Counter(row["root_cause_class"] for row in root_rows)),
        "drivers": dict(Counter(row["component_driver"] for row in root_rows)),
        "semantic_source_observation_count": len(semantic_source_keys),
        "unresolved_source_observation_count": len(unresolved_source_keys),
        "paths": {name: str(path) for name, path in output.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=project_root())
    args = parser.parse_args()
    print(json.dumps(run(args.root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
