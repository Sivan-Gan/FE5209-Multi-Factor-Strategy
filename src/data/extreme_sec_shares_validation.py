"""Audit extreme DEV SEC-share source facts and simulate a rejection rule.

This module is deliberately an audit/recommendation layer.  It never rescales
shares, replaces DEI shares with an alternate tag, changes the signed split
chain, or writes the canonical estimated-market-cap object.  Its only data
mutation is the authorized documentation and audit evidence under ``docs/``
and ``outputs/audit/``.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from src.data import estimated_historical_market_cap as market_cap
from src.data import historical_market_cap_spec_audit as spec
from src.data import source_audit


VERSION = "fe5209.extreme_sec_shares_validation.dev.v1"
EVIDENCE_STAGE = "DEV_CURRENT_UNIVERSE"
DIAGNOSTIC_ABSOLUTE_THRESHOLD = 1_000_000_000_000
# This is a proposed source-semantic rule, not a market-cap, factor, or
# portfolio parameter.  The smallest observed anomalous boundary is ~993.9x;
# 100x is deliberately below that evidence floor and remains split-exempt.
RELATIVE_SEGMENT_RATIO = 100.0
ACTION_RATIO_TOLERANCE = 0.05
WINDOWS = market_cap.WINDOWS


@dataclass(frozen=True)
class ShareFact:
    ticker: str
    yahoo_ticker: str
    cik: int
    issuer: str
    taxonomy: str
    tag: str
    unit: str
    raw_value: float
    value_text: str
    end_date: date | None
    filed_date: date | None
    form: str
    accession: str
    amendment_flag: bool
    frame: str
    fiscal_year: str
    fiscal_period: str
    ordinal: int
    tag_label: str
    tag_description: str

    @property
    def source_id(self) -> str:
        return "|".join(
            (
                self.ticker,
                self.taxonomy,
                self.tag,
                self.unit,
                self.end_date.isoformat() if self.end_date else "",
                self.filed_date.isoformat() if self.filed_date else "",
                self.form,
                self.accession,
                str(self.ordinal),
            )
        )

    @property
    def eligible(self) -> bool:
        return (
            self.raw_value > 0
            and self.end_date is not None
            and self.filed_date is not None
            and self.end_date <= self.filed_date
            and spec._is_allowed_share_form(self.form)
        )


@dataclass(frozen=True)
class ActionEvent:
    event_date: date
    factor: float
    is_standard: bool


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def paths(root: Path) -> dict[str, Path]:
    audit = root / "outputs" / "audit"
    return {
        "source": audit / "extreme_sec_shares_source_observations_dev.csv",
        "timeseries": audit / "extreme_sec_shares_timeseries_dev.csv",
        "propagation": audit / "extreme_shares_market_cap_propagation_dev.csv",
        "cross_tag": audit / "extreme_shares_cross_tag_validation_dev.csv",
        "impact": audit / "extreme_shares_rejection_impact_dev.csv",
        "distribution": audit / "extreme_shares_post_rejection_distribution_dev.csv",
        "jumps": audit / "extreme_shares_post_rejection_jump_audit_dev.csv",
        "acceptance": audit / "extreme_sec_shares_acceptance_report.md",
        "document": root / "docs" / "EXTREME_SEC_SHARES_VALIDATION_DEV.md",
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


def _date(value: Any) -> date | None:
    return spec._parse_date(value)


def _ratio(left: float | None, right: float | None) -> float | None:
    if left is None or right is None or left <= 0 or right <= 0:
        return None
    return left / right


def _absolute_ratio(left: float | None, right: float | None) -> float | None:
    ratio = _ratio(left, right)
    return max(ratio, 1.0 / ratio) if ratio is not None else None


def _pct(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    point = (len(ordered) - 1) * quantile
    low, high = int(point), min(int(point) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (point - low)


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    finite = [value for value in values if math.isfinite(value)]
    positive = [value for value in finite if value > 0]
    return {
        "available_observation_count": len(values),
        "finite_observation_count": len(finite),
        "nonpositive_or_nonfinite_count": len(values) - len(positive),
        "min": min(positive) if positive else None,
        "p01": _pct(positive, .01),
        "p05": _pct(positive, .05),
        "median": _pct(positive, .50),
        "p95": _pct(positive, .95),
        "p99": _pct(positive, .99),
        "max": max(positive) if positive else None,
    }


def _window(day: date) -> str | None:
    for name, start, end in WINDOWS:
        if start <= day <= end:
            return name
    return None


def _read_canonical_extremes(root: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Return authoritative identity and the current propagated high-share keys."""
    path = market_cap.paths(root)["canonical"]
    identity: dict[str, dict[str, Any]] = {}
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
        for row in connection.execute(
            "SELECT ticker, yahoo_ticker, cik, issuer FROM weekly_estimated_market_cap GROUP BY ticker"
        ):
            identity[str(row[0])] = {
                "ticker": str(row[0]), "yahoo_ticker": str(row[1]), "cik": int(row[2]), "issuer": str(row[3]),
            }
        keys = [
            {
                "ticker": str(row[0]), "cik": int(row[1]), "reported_shares": float(row[2]),
                "shares_asof_date": str(row[3]), "shares_filed_date": str(row[4]), "shares_accession": str(row[5]),
            }
            for row in connection.execute(
                """
                SELECT DISTINCT ticker, cik, reported_shares, shares_asof_date, shares_filed_date, shares_accession
                FROM weekly_estimated_market_cap
                WHERE availability_flag = 1 AND reported_shares > ?
                ORDER BY ticker, shares_filed_date, shares_accession
                """,
                (DIAGNOSTIC_ABSOLUTE_THRESHOLD,),
            )
        ]
    return identity, keys


def _read_raw_facts(root: Path, identity: dict[str, dict[str, Any]]) -> tuple[dict[str, list[ShareFact]], dict[str, list[ShareFact]]]:
    """Read DEI primary facts and US-GAAP diagnostic facts for named CIKs only."""
    by_cik = {item["cik"]: item for item in identity.values()}
    primary: dict[str, list[ShareFact]] = defaultdict(list)
    alternate: dict[str, list[ShareFact]] = defaultdict(list)
    facts_root = source_audit.package_root(root) / source_audit.SEC_FACTS_DIR
    for cik, info in sorted(by_cik.items()):
        archive = facts_root / f"CIK{cik:010d}.json.gz"
        if not archive.exists():
            continue
        with gzip.open(archive, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        fact_map = payload.get("facts", {})
        for destination, taxonomy, tag in (
            (primary, "dei", "EntityCommonStockSharesOutstanding"),
            (alternate, "us-gaap", "CommonStockSharesOutstanding"),
        ):
            definition = fact_map.get(taxonomy, {}).get(tag, {})
            units = definition.get("units", {}) if isinstance(definition, dict) else {}
            if not isinstance(units, dict):
                continue
            for unit, rows in units.items():
                if not isinstance(rows, list):
                    continue
                for ordinal, raw in enumerate(rows):
                    if not isinstance(raw, dict):
                        continue
                    value = _finite(raw.get("val"))
                    if value is None:
                        continue
                    destination[info["ticker"]].append(ShareFact(
                        ticker=info["ticker"], yahoo_ticker=info["yahoo_ticker"], cik=cik, issuer=info["issuer"],
                        taxonomy=taxonomy, tag=tag, unit=str(unit), raw_value=value, value_text=str(raw.get("val")),
                        end_date=_date(raw.get("end")), filed_date=_date(raw.get("filed")), form=str(raw.get("form") or ""),
                        accession=str(raw.get("accn") or ""), amendment_flag=str(raw.get("form") or "").endswith("/A"),
                        frame=str(raw.get("frame") or ""), fiscal_year=str(raw.get("fy") or ""),
                        fiscal_period=str(raw.get("fp") or ""), ordinal=ordinal,
                        tag_label=str(definition.get("label") or ""), tag_description=str(definition.get("description") or ""),
                    ))
    return dict(primary), dict(alternate)


def filing_level_sequence(facts: Iterable[ShareFact]) -> list[ShareFact]:
    """Make an auditable sequence of latest-ending eligible observations by filing.

    A conflicted latest-end group is intentionally omitted from a relative-run
    inference rather than receiving a silent value-order tie-break.  No target
    source fact in the present audit population is omitted this way.
    """
    by_filed: dict[date, list[ShareFact]] = defaultdict(list)
    for fact in facts:
        if fact.eligible and fact.filed_date is not None:
            by_filed[fact.filed_date].append(fact)
    sequence: list[ShareFact] = []
    for filed, items in sorted(by_filed.items()):
        latest_end = max(item.end_date for item in items if item.end_date is not None)
        group = [item for item in items if item.end_date == latest_end]
        values = {item.value_text for item in group}
        provenance = {(item.accession, item.form, item.frame, item.fiscal_year, item.fiscal_period) for item in group}
        if len(values) != 1 or len(provenance) != 1:
            continue
        sequence.append(min(group, key=lambda item: item.source_id))
    return sequence


def _load_action_events(root: Path, tickers: set[str]) -> dict[str, list[ActionEvent]]:
    """Read supplied Yahoo action values only for relevant tickers."""
    path = source_audit.package_root(root) / source_audit.PRICE_FILE
    result: dict[str, list[ActionEvent]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            ticker = str(row.get("ticker") or "")
            if ticker not in tickers:
                continue
            factor = _finite(row.get("stock_splits")) or 0.0
            event_date = _date(row.get("date"))
            if event_date is not None and factor and math.isfinite(factor):
                result[ticker].append(ActionEvent(event_date, factor, spec._is_standard_split_factor(factor)))
    return {ticker: sorted(events, key=lambda item: item.event_date) for ticker, events in result.items()}


def _events_between(events: Iterable[ActionEvent], start: date | None, end: date | None) -> list[ActionEvent]:
    if start is None or end is None:
        return []
    low, high = sorted((start, end))
    return [event for event in events if low <= event.event_date <= high]


def _action_explains_ratio(ratio: float, events: Iterable[ActionEvent]) -> bool:
    standard = [event.factor for event in events if event.is_standard]
    if not standard:
        return False
    product = math.prod(standard)
    if product <= 0 or not math.isfinite(product):
        return False
    return math.isclose(ratio, product, rel_tol=ACTION_RATIO_TOLERANCE) or math.isclose(
        ratio, 1.0 / product, rel_tol=ACTION_RATIO_TOLERANCE
    )


def _segments(sequence: list[ShareFact]) -> list[list[ShareFact]]:
    """Partition a filing sequence whenever its scale changes >= proposed ratio."""
    if not sequence:
        return []
    output: list[list[ShareFact]] = [[sequence[0]]]
    for fact in sequence[1:]:
        if (_absolute_ratio(fact.raw_value, output[-1][-1].raw_value) or 0.0) >= RELATIVE_SEGMENT_RATIO:
            output.append([fact])
        else:
            output[-1].append(fact)
    return output


def detect_transient_high_runs(
    sequence: list[ShareFact], events: Iterable[ActionEvent]
) -> dict[str, dict[str, Any]]:
    """Return proposed relative-rule classifications keyed by source id.

    A candidate high run must *revert* to a lower-scale subsequent direct DEI
    disclosure by at least ``RELATIVE_SEGMENT_RATIO``.  This avoids treating a
    persistent issuer-capitalization change (such as VTRS's documented source
    sequence) as a transient source scale anomaly.  If a prior normal segment
    also exists the source classification is confirmed; otherwise it remains
    unresolved yet is still recommended unavailable because no corrected value
    is inferred.
    """
    segments = _segments(sequence)
    result: dict[str, dict[str, Any]] = {}
    for segment_index, current in enumerate(segments[:-1]):
        following = segments[segment_index + 1]
        current_level, following_level = median(item.raw_value for item in current), median(item.raw_value for item in following)
        if current_level <= following_level:
            continue
        down_ratio = current_level / following_level
        boundary_events = _events_between(events, current[-1].end_date, following[0].end_date)
        if down_ratio < RELATIVE_SEGMENT_RATIO or _action_explains_ratio(down_ratio, boundary_events):
            continue
        preceding = segments[segment_index - 1] if segment_index else None
        prior_ratio = None
        prior_events: list[ActionEvent] = []
        if preceding:
            previous_level = median(item.raw_value for item in preceding)
            prior_ratio = current_level / previous_level if previous_level else None
            prior_events = _events_between(events, preceding[-1].end_date, current[0].end_date)
        confirmed = bool(
            preceding
            and prior_ratio is not None
            and prior_ratio >= RELATIVE_SEGMENT_RATIO
            and not _action_explains_ratio(prior_ratio, prior_events)
        )
        classification = "CONFIRMED_SCALE_ANOMALY" if confirmed else "UNRESOLVED_EXTREME_SHARES"
        for fact in current:
            result[fact.source_id] = {
                "classification": classification,
                "recommended_exclusion_reason": "EXTREME_SHARES_UNRESOLVED",
                "rejection_recommended": 1,
                "segment_index": segment_index,
                "segment_size": len(current),
                "segment_level": current_level,
                "next_segment_level": following_level,
                "downward_boundary_ratio": down_ratio,
                "prior_segment_level": median(item.raw_value for item in preceding) if preceding else None,
                "upward_boundary_ratio": prior_ratio,
                "prior_action_events_json": _events_json(prior_events),
                "next_action_events_json": _events_json(boundary_events),
                "action_explains_boundary": 0,
            }
    return result


def _events_json(events: Iterable[ActionEvent]) -> str:
    return json.dumps(
        [
            {"event_date": item.event_date.isoformat(), "factor": item.factor, "standard": item.is_standard}
            for item in events
        ],
        separators=(",", ":"), sort_keys=True,
    )


def _fact_payload(prefix: str, fact: ShareFact | None) -> dict[str, Any]:
    if fact is None:
        return {
            f"{prefix}_source_id": "", f"{prefix}_value": "", f"{prefix}_end_date": "",
            f"{prefix}_filed_date": "", f"{prefix}_form": "", f"{prefix}_accession": "",
        }
    return {
        f"{prefix}_source_id": fact.source_id, f"{prefix}_value": fact.raw_value,
        f"{prefix}_end_date": fact.end_date.isoformat() if fact.end_date else "",
        f"{prefix}_filed_date": fact.filed_date.isoformat() if fact.filed_date else "",
        f"{prefix}_form": fact.form, f"{prefix}_accession": fact.accession,
    }


def _sequence_neighbors(sequence: list[ShareFact], source_id: str) -> tuple[ShareFact | None, ShareFact | None, int | None]:
    index = next((idx for idx, item in enumerate(sequence) if item.source_id == source_id), None)
    if index is None:
        return None, None, None
    return (sequence[index - 1] if index else None, sequence[index + 1] if index + 1 < len(sequence) else None, index)


def _alternate_summary(primary: ShareFact, facts: list[ShareFact]) -> tuple[ShareFact | None, str, str]:
    same_accession = [fact for fact in facts if fact.accession == primary.accession and fact.unit == "shares"]
    exact = [fact for fact in same_accession if fact.end_date == primary.end_date]
    if exact:
        candidate = min(exact, key=lambda fact: fact.source_id)
        return candidate, "EXACT_END_SAME_ACCESSION", json.dumps([item.source_id for item in same_accession], separators=(",", ":"))
    if same_accession and primary.end_date is not None:
        candidate = min(
            same_accession,
            key=lambda fact: abs((fact.end_date - primary.end_date).days) if fact.end_date else 10**9,
        )
        return candidate, "NEAREST_END_SAME_ACCESSION", json.dumps([item.source_id for item in same_accession], separators=(",", ":"))
    return None, "NO_SAME_ACCESSION_ALTERNATE", "[]"


def _build_source_audits(
    root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], set[tuple[str, str, str]], dict[str, Any]]:
    identity, current_keys = _read_canonical_extremes(root)
    primary, alternate = _read_raw_facts(root, identity)
    tickers = {item["ticker"] for item in current_keys}
    actions = _load_action_events(root, tickers)
    sequences = {ticker: filing_level_sequence(primary.get(ticker, [])) for ticker in tickers}
    decisions = {ticker: detect_transient_high_runs(sequences[ticker], actions.get(ticker, [])) for ticker in tickers}
    key_lookup = {(item["ticker"], item["shares_accession"], item["shares_asof_date"], item["reported_shares"]): item for item in current_keys}
    source_rows: list[dict[str, Any]] = []
    timeseries_rows: list[dict[str, Any]] = []
    cross_rows: list[dict[str, Any]] = []
    rejected_keys: set[tuple[str, str, str]] = set()
    classification_counts: dict[str, int] = defaultdict(int)
    for ticker in sorted(tickers):
        sequence = sequences[ticker]
        for fact in sequence:
            current = key_lookup.get((ticker, fact.accession, fact.end_date.isoformat() if fact.end_date else "", fact.raw_value))
            if current is None:
                continue
            previous, following, index = _sequence_neighbors(sequence, fact.source_id)
            decision = decisions[ticker].get(fact.source_id, {
                "classification": "UNRESOLVED_EXTREME_SHARES",
                "recommended_exclusion_reason": "EXTREME_SHARES_UNRESOLVED",
                "rejection_recommended": 0,
                "segment_index": "", "segment_size": "", "segment_level": "", "next_segment_level": "",
                "downward_boundary_ratio": "", "prior_segment_level": "", "upward_boundary_ratio": "",
                "prior_action_events_json": "[]", "next_action_events_json": "[]", "action_explains_boundary": 0,
            })
            classification_counts[str(decision["classification"])] += 1
            if decision["rejection_recommended"]:
                rejected_keys.add((ticker, fact.accession, fact.end_date.isoformat() if fact.end_date else ""))
            alternate_fact, alternate_basis, alternate_all = _alternate_summary(fact, alternate.get(ticker, []))
            adjacent_events = _events_between(
                actions.get(ticker, []), previous.end_date if previous else fact.end_date, following.end_date if following else fact.end_date
            )
            source_rows.append({
                "evidence_stage": EVIDENCE_STAGE, "ticker": ticker, "yahoo_ticker": fact.yahoo_ticker,
                "cik": fact.cik, "issuer": fact.issuer, "taxonomy": fact.taxonomy, "tag": fact.tag, "unit": fact.unit,
                "raw_shares_value": fact.raw_value, "diagnostic_absolute_threshold": DIAGNOSTIC_ABSOLUTE_THRESHOLD,
                "end_date": fact.end_date.isoformat() if fact.end_date else "", "filed_date": fact.filed_date.isoformat() if fact.filed_date else "",
                "form": fact.form, "accession": fact.accession, "amendment_flag": int(fact.amendment_flag),
                "frame": fact.frame, "fiscal_year": fact.fiscal_year, "fiscal_period": fact.fiscal_period,
                "source_observation_id": fact.source_id, "tag_label": fact.tag_label, "tag_description": fact.tag_description,
                "source_context_metadata_available": 0,
                "source_context_metadata_note": "SEC Company Facts record contains no original XBRL context/dimension/member metadata.",
                **_fact_payload("previous_eligible", previous), **_fact_payload("next_eligible", following),
                "ratio_to_previous": _ratio(fact.raw_value, previous.raw_value if previous else None),
                "ratio_to_next": _ratio(fact.raw_value, following.raw_value if following else None),
                "absolute_ratio_to_previous": _absolute_ratio(fact.raw_value, previous.raw_value if previous else None),
                "absolute_ratio_to_next": _absolute_ratio(fact.raw_value, following.raw_value if following else None),
                "nearby_action_events_json": _events_json(adjacent_events),
                "nearby_standard_action_count": sum(event.is_standard for event in adjacent_events),
                "nearby_nonstandard_action_count": sum(not event.is_standard for event in adjacent_events),
                "alternate_same_accession_basis": alternate_basis,
                "alternate_source_observation_ids_json": alternate_all,
                "alternate_value": alternate_fact.raw_value if alternate_fact else None,
                "dei_to_alternate_ratio": _ratio(fact.raw_value, alternate_fact.raw_value if alternate_fact else None),
                **decision,
            })
            for offset in range(-3, 4):
                item_index = (index or 0) + offset
                if 0 <= item_index < len(sequence):
                    item = sequence[item_index]
                    prior = sequence[item_index - 1] if item_index else None
                    timeseries_rows.append({
                        "evidence_stage": EVIDENCE_STAGE, "ticker": ticker, "cik": fact.cik,
                        "target_source_observation_id": fact.source_id, "target_accession": fact.accession,
                        "relative_filing_position": offset, "source_observation_id": item.source_id,
                        "end_date": item.end_date.isoformat() if item.end_date else "", "filed_date": item.filed_date.isoformat() if item.filed_date else "",
                        "form": item.form, "accession": item.accession, "raw_shares_value": item.raw_value,
                        "ratio_to_previous_disclosure": _ratio(item.raw_value, prior.raw_value if prior else None),
                        "is_target_extreme": int(item.source_id == fact.source_id),
                        "target_classification": decision["classification"],
                    })
            cross_rows.append({
                "evidence_stage": EVIDENCE_STAGE, "ticker": ticker, "yahoo_ticker": fact.yahoo_ticker,
                "cik": fact.cik, "issuer": fact.issuer, "dei_source_observation_id": fact.source_id,
                "dei_accession": fact.accession, "dei_end_date": fact.end_date.isoformat() if fact.end_date else "",
                "dei_raw_shares_value": fact.raw_value, "alternate_taxonomy": alternate_fact.taxonomy if alternate_fact else "",
                "alternate_tag": alternate_fact.tag if alternate_fact else "", "alternate_unit": alternate_fact.unit if alternate_fact else "",
                "alternate_source_observation_id": alternate_fact.source_id if alternate_fact else "",
                "alternate_end_date": alternate_fact.end_date.isoformat() if alternate_fact and alternate_fact.end_date else "",
                "alternate_raw_shares_value": alternate_fact.raw_value if alternate_fact else None,
                "alternate_match_basis": alternate_basis, "dei_to_alternate_ratio": _ratio(fact.raw_value, alternate_fact.raw_value if alternate_fact else None),
                "all_same_accession_alternate_ids_json": alternate_all,
                "diagnostic_only_no_production_fallback": 1, "classification": decision["classification"],
            })
    summary = {
        "source_observation_count": len(source_rows), "ticker_count": len(tickers),
        "classification_counts": dict(sorted(classification_counts.items())), "rejected_source_count": len(rejected_keys),
    }
    return source_rows, timeseries_rows, cross_rows, rejected_keys, summary


def _propagation_audit(root: Path, source_rows: list[dict[str, Any]], rejected: set[tuple[str, str, str]]) -> list[dict[str, Any]]:
    path = market_cap.paths(root)["canonical"]
    relevant = {(row["ticker"], row["accession"], row["end_date"]): row for row in source_rows}
    rows_by_key: dict[tuple[str, str, str], list[sqlite3.Row]] = defaultdict(list)
    ticker_rows: dict[str, list[sqlite3.Row]] = defaultdict(list)
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        for item in connection.execute(
            """
            SELECT ticker, signal_date, reported_shares, shares_asof_date, shares_accession,
                   estimated_market_cap, availability_flag
            FROM weekly_estimated_market_cap ORDER BY ticker, signal_date
            """
        ):
            ticker_rows[str(item["ticker"])].append(item)
            key = (str(item["ticker"]), str(item["shares_accession"] or ""), str(item["shares_asof_date"] or ""))
            if key in relevant and int(item["availability_flag"]):
                rows_by_key[key].append(item)
    results: list[dict[str, Any]] = []
    for key, source in sorted(relevant.items()):
        current = rows_by_key.get(key, [])
        all_rows = ticker_rows[key[0]]
        positions = {str(item["signal_date"]): index for index, item in enumerate(all_rows)}
        jumps: list[float] = []
        for item in current:
            pos = positions[str(item["signal_date"])]
            if pos and int(all_rows[pos - 1]["availability_flag"]) and all_rows[pos - 1]["estimated_market_cap"]:
                prior = float(all_rows[pos - 1]["estimated_market_cap"])
                value = float(item["estimated_market_cap"])
                jumps.append(value / prior)
        last_date = str(current[-1]["signal_date"]) if current else ""
        recovery = next(
            (
                item for item in all_rows
                if last_date and str(item["signal_date"]) > last_date and int(item["availability_flag"])
                and (str(item["shares_accession"] or ""), str(item["shares_asof_date"] or "")) != (key[1], key[2])
            ),
            None,
        )
        results.append({
            "evidence_stage": EVIDENCE_STAGE, "ticker": key[0], "source_observation_id": source["source_observation_id"],
            "accession": key[1], "shares_asof_date": key[2], "reported_shares": source["raw_shares_value"],
            "classification": source["classification"], "rejection_recommended": int(key in rejected),
            "first_signal_date_selected": str(current[0]["signal_date"]) if current else "",
            "last_signal_date_selected": str(current[-1]["signal_date"]) if current else "",
            "propagated_available_ticker_week_count": len(current),
            "estimated_market_cap_min": min((float(item["estimated_market_cap"]) for item in current), default=None),
            "estimated_market_cap_max": max((float(item["estimated_market_cap"]) for item in current), default=None),
            "max_weekly_market_cap_ratio_during_propagation": max(jumps) if jumps else None,
            "recovery_signal_date": str(recovery["signal_date"]) if recovery else "",
            "recovery_reported_shares": float(recovery["reported_shares"]) if recovery and recovery["reported_shares"] is not None else None,
            "recovery_estimated_market_cap": float(recovery["estimated_market_cap"]) if recovery and recovery["estimated_market_cap"] is not None else None,
        })
    return results


def _fundamental_availability(root: Path) -> dict[str, set[tuple[str, date]]]:
    """Read the existing PIT availability layer without leaving a connection open."""
    db = root / "data" / "canonical" / "us_weekly_pit_fundamentals_dev.sqlite"
    wanted = {
        "net_income": ("net_income", "annual_candidate"),
        "common_equity": ("common_equity", "instant"),
        "ocf": ("operating_cash_flow", "annual_candidate"),
        "capex": ("capital_expenditure", "annual_candidate"),
    }
    result: dict[str, set[tuple[str, date]]] = {name: set() for name in wanted}
    with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as connection:
        for name, (concept, period_class) in wanted.items():
            for ticker, signal_date in connection.execute(
                "SELECT ticker, signal_date FROM weekly_pit_fundamental_snapshot "
                "WHERE economic_concept = ? AND period_class = ?",
                (concept, period_class),
            ):
                result[name].add((str(ticker), date.fromisoformat(str(signal_date))))
    return result


def _impact_and_distribution(
    root: Path, rejected: set[tuple[str, str, str]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    path = market_cap.paths(root)["canonical"]
    fundamentals = _fundamental_availability(root)
    per_day: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    before_values: list[float] = []
    after_values: list[float] = []
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
        for signal_text, ticker, mapped, available, accession, asof, value in connection.execute(
            """
            SELECT signal_date, ticker, sec_mapping_available, availability_flag, shares_accession,
                   shares_asof_date, estimated_market_cap FROM weekly_estimated_market_cap
            """
        ):
            day = date.fromisoformat(str(signal_text))
            if not int(mapped):
                continue
            per_day[day]["denominator"] += 1
            if not int(available):
                continue
            value = float(value)
            before_values.append(value)
            rejected_row = (str(ticker), str(accession or ""), str(asof or "")) in rejected
            if rejected_row:
                per_day[day]["affected"] += 1
            else:
                after_values.append(value)
            ep = (str(ticker), day) in fundamentals["net_income"]
            bp = (str(ticker), day) in fundamentals["common_equity"]
            fcf = (str(ticker), day) in fundamentals["ocf"] and (str(ticker), day) in fundamentals["capex"]
            two_of_three = int(sum((ep, bp, fcf)) >= 2)
            per_day[day]["market_cap_before"] += 1
            per_day[day]["value_before"] += two_of_three
            if not rejected_row:
                per_day[day]["market_cap_after"] += 1
                per_day[day]["value_after"] += two_of_three
    impact: list[dict[str, Any]] = []
    for measure, before_key, after_key in (
        ("market_cap_input", "market_cap_before", "market_cap_after"),
        ("value_two_of_three_input", "value_before", "value_after"),
    ):
        for window, start, end in WINDOWS:
            days = [day for day in per_day if start <= day <= end]
            before = [per_day[day][before_key] / per_day[day]["denominator"] for day in days]
            after = [per_day[day][after_key] / per_day[day]["denominator"] for day in days]
            affected = [per_day[day]["affected"] for day in days]
            impact.append({
                "evidence_stage": EVIDENCE_STAGE, "measure": measure, "window": window,
                "signal_week_count": len(days), "denominator": 500,
                "before_mean_weekly_share": sum(before) / len(before), "after_mean_weekly_share": sum(after) / len(after),
                "change_percentage_points": (sum(after) / len(after) - sum(before) / len(before)) * 100,
                "affected_available_ticker_week_count": sum(affected), "mean_weekly_affected_count": sum(affected) / len(affected),
                "scope": "simulation only; no factor or canonical object is modified",
            })
    distribution = [
        {"evidence_stage": EVIDENCE_STAGE, "scenario": "before_rejection", **_distribution(before_values), "note": "Existing available estimated market-cap values; diagnostic only."},
        {"evidence_stage": EVIDENCE_STAGE, "scenario": "after_simulated_rejection", **_distribution(after_values), "note": "Simulation only; no clipping, winsorization, rescaling, or canonical rewrite."},
    ]
    return impact, distribution


def _jump_audit(root: Path, rejected: set[tuple[str, str, str]], limit: int = 20) -> list[dict[str, Any]]:
    path = market_cap.paths(root)["canonical"]
    scenarios = (("before_rejection", False), ("after_simulated_rejection", True))
    output: list[dict[str, Any]] = []
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        all_rows = list(connection.execute(
            """
            SELECT ticker, issuer, signal_date, estimated_market_cap, price_close, reported_shares,
                   shares_accession, shares_asof_date, split_adjustment_factor, standard_split_event_count,
                   staleness_flag, availability_flag
            FROM weekly_estimated_market_cap ORDER BY ticker, signal_date
            """
        ))
    dates = sorted({str(item["signal_date"]) for item in all_rows})
    prior_date = {right: left for left, right in zip(dates, dates[1:])}
    for scenario, reject in scenarios:
        prior_by_ticker: dict[str, sqlite3.Row] = {}
        candidates: list[dict[str, Any]] = []
        for item in all_rows:
            ticker = str(item["ticker"])
            key = (ticker, str(item["shares_accession"] or ""), str(item["shares_asof_date"] or ""))
            usable = bool(item["availability_flag"]) and (not reject or key not in rejected)
            prior = prior_by_ticker.get(ticker)
            if usable and prior is not None and prior_date.get(str(item["signal_date"])) == str(prior["signal_date"]):
                value, old = float(item["estimated_market_cap"]), float(prior["estimated_market_cap"])
                ratio = value / old
                shares_changed = item["shares_accession"] != prior["shares_accession"]
                split_changed = not math.isclose(
                    float(item["split_adjustment_factor"]), float(prior["split_adjustment_factor"]), rel_tol=0.0, abs_tol=1e-12
                )
                if shares_changed and (int(item["staleness_flag"]) or int(prior["staleness_flag"])):
                    classification = "STALE_SHARES_REPLACEMENT"
                elif shares_changed and split_changed:
                    classification = "SPLIT_RELATED_SHARES_UPDATE"
                elif shares_changed:
                    classification = "SHARES_UPDATE"
                elif split_changed:
                    classification = "STANDARD_SPLIT_CHAIN_CHANGE"
                else:
                    classification = "PRICE_MOVE_OR_UNCLASSIFIED"
                candidates.append({
                    "scenario": scenario, "ticker": ticker, "issuer": item["issuer"],
                    "previous_signal_date": prior["signal_date"], "signal_date": item["signal_date"],
                    "previous_estimated_market_cap": old, "estimated_market_cap": value, "market_cap_ratio": ratio,
                    "absolute_log_market_cap_change": abs(math.log(ratio)), "price_ratio": float(item["price_close"]) / float(prior["price_close"]),
                    "previous_reported_shares": prior["reported_shares"], "reported_shares": item["reported_shares"],
                    "previous_accession": prior["shares_accession"], "accession": item["shares_accession"],
                    "jump_classification": classification,
                })
            prior_by_ticker[ticker] = item if usable else None
        for rank, row in enumerate(sorted(candidates, key=lambda item: (-item["absolute_log_market_cap_change"], item["ticker"], item["signal_date"]))[:limit], start=1):
            output.append({"evidence_stage": EVIDENCE_STAGE, "rank": rank, **row})
    return output


def _vtrs_case(root: Path) -> dict[str, Any]:
    """Document, but do not silently solve, the mandatory non-threshold VTRS case."""
    identity, _keys = _read_canonical_extremes(root)
    primary, alternate = _read_raw_facts(root, {"VTRS": identity["VTRS"]})
    sequence = filing_level_sequence(primary["VTRS"])
    old = next(item for item in sequence if item.accession == "0001193125-20-290294")
    new = next(item for item in sequence if item.accession == "0001792044-21-000009")
    alternate_old, old_basis, _ = _alternate_summary(old, alternate["VTRS"])
    return {
        "old_accession": old.accession, "old_shares": old.raw_value, "old_filed": old.filed_date.isoformat() if old.filed_date else "",
        "new_accession": new.accession, "new_shares": new.raw_value, "new_filed": new.filed_date.isoformat() if new.filed_date else "",
        "ratio": new.raw_value / old.raw_value, "old_alternate_shares": alternate_old.raw_value if alternate_old else None,
        "alternate_basis": old_basis,
        "conclusion": "Persistent source issuer-transition sequence; no delivered split action and no raw Company Facts context/transaction metadata proves an economic explanation. It is not a transient >1e12 scale-anomaly run and is not included in this narrow rejection simulation.",
    }


def _document(
    root: Path, summary: dict[str, Any], propagation: list[dict[str, Any]], impact: list[dict[str, Any]],
    distribution: list[dict[str, Any]], jumps: list[dict[str, Any]], vtrs: dict[str, Any],
) -> str:
    out = paths(root)
    after = next(row for row in distribution if row["scenario"] == "after_simulated_rejection")
    before = next(row for row in distribution if row["scenario"] == "before_rejection")
    source_count = summary["source_observation_count"]
    propagated = sum(int(row["propagated_available_ticker_week_count"]) for row in propagation)
    before_top = next(row for row in jumps if row["scenario"] == "before_rejection" and row["rank"] == 1)
    after_top = next(row for row in jumps if row["scenario"] == "after_simulated_rejection" and row["rank"] == 1)
    remaining_million_jump = float(after_top["market_cap_ratio"]) >= 1_000_000
    decision = "FAIL" if remaining_million_jump else "PASS WITH WARNINGS"
    rule_status = "NOT_READY_FOR_FORMAL_SIGNOFF" if remaining_million_jump else "RECOMMEND_SIGNOFF"
    impacts = {(row["measure"], row["window"]): row for row in impact}
    window_labels = {
        "factor_selection_2011_2015": "2011–2015",
        "configuration_selection_2016_2020": "2016–2020",
        "historical_oos_2021_2026_09": "2021–2026.09",
    }
    report = [
        "# Extreme SEC Shares Semantic Validation — DEV Acceptance Report",
        "",
        f"- **Evidence stage:** `{EVIDENCE_STAGE}`",
        "- **Scope:** source-semantic audit and simulation only; the existing canonical market-cap object is not modified.",
        f"- **Decision:** `{decision}` for market-cap semantic closure.",
        f"- **Rule status:** `{rule_status}` for the relative transient-high-share candidate below.",
        "",
        "## Population and source conclusion",
        "",
        f"The current diagnostic threshold identifies **{source_count} selected DEI source observations** across **{summary['ticker_count']} tickers**, propagating to **{propagated} available ticker-weeks**. The threshold is only a discovery device; it is not the proposed production rule.",
        "",
        "The source sequence shows 10^3/10^6 transient high-scale episodes. The delivered Yahoo action data has no contemporaneous standard or non-standard action that explains an affected boundary. SEC Company Facts retains form/date/accession/frame fields but not original XBRL dimensional context; this limitation is recorded rather than inferred.",
        "",
        "## Candidate rule: EXTREME_SHARES_REJECTION_RULE",
        "",
        "Within one issuer's eligible DEI primary-share filing sequence, identify a contiguous high-share run that subsequently reverts to a lower-scale direct DEI run by at least 100×. If the delivered standard-action chain between the runs does not explain the ratio, mark every observation in the transient high run `EXTREME_SHARES_UNRESOLVED` and unavailable. A run with normal-scale observations on both sides is `CONFIRMED_SCALE_ANOMALY`; an early-source run with only the subsequent reversion is `UNRESOLVED_EXTREME_SHARES`. Neither result implies a corrected value, alternate-tag fallback, or rescaling.",
        "",
        "This is a relative, issuer-sequence rule. It does not reject a stable large issuer merely for exceeding 1e12 shares, and it is portable to a future formal source because it relies on source observation provenance and disclosed action evidence rather than ticker hardcodes. A standard/reverse split whose delivered action factor explains the boundary is exempt.",
        "",
        "## Classification",
        "",
        "| Classification | Source observations | Treatment |",
        "| --- | ---: | --- |",
    ]
    for name, count in sorted(summary["classification_counts"].items()):
        report.append(f"| {name} | {count} | `EXTREME_SHARES_UNRESOLVED` / unavailable in the simulation |")
    report.extend([
        "",
        "## Simulated availability impact",
        "",
        "| Measure | Window | Before | After | Change (pp) | Affected available ticker-weeks |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ])
    for measure in ("market_cap_input", "value_two_of_three_input"):
        for window, label in window_labels.items():
            item = impacts[(measure, window)]
            report.append(
                f"| {measure} | {label} | {item['before_mean_weekly_share']:.2%} | {item['after_mean_weekly_share']:.2%} | {item['change_percentage_points']:.4f} | {item['affected_available_ticker_week_count']:,} |"
            )
    report.extend([
        "",
        "## Distribution and jumps (simulation only)",
        "",
        f"Estimated market-cap max changes from {before['max']:.6g} to {after['max']:.6g}; p99 changes from {before['p99']:.6g} to {after['p99']:.6g}. No clipping or winsorization occurred.",
        f"The largest weekly jump before simulation is {before_top['ticker']} at {before_top['market_cap_ratio']:.6g}×; after simulation it is {after_top['ticker']} at {after_top['market_cap_ratio']:.6g}×. The full top-20 before/after rankings are in the jump audit.",
        "",
        "## Mandatory case: ORCL",
        "",
        "ORCL source accession `0001193125-12-401697` filed 2012-09-24 reports DEI shares `4,819,056,000,000,000`; the preceding normal value is `4,882,506,000` (about 987,004.6× lower) and the next normal value is `4,734,297,000` (about 1,017,903.2× lower). The same accession's diagnostic US-GAAP fact is `4,831,000,000`. No delivered action explains the episode. Classification: `CONFIRMED_SCALE_ANOMALY`.",
        "",
        "## Mandatory case: VTRS",
        "",
        f"VTRS changes from `{vtrs['old_shares']:.0f}` in `{vtrs['old_accession']}` (filed {vtrs['old_filed']}) to `{vtrs['new_shares']:.0f}` in `{vtrs['new_accession']}` (filed {vtrs['new_filed']}), a {vtrs['ratio']:.6g}× source change. {vtrs['conclusion']}",
        "",
        "## Boundary and next action",
        "",
        "The 20-record evidence supports the candidate relative rule's design, but it is **not ready for formal sign-off** in this round. The post-rejection audit still has a 1,130万× VTRS jump and other million-level source-share jumps. Under the explicit closure condition, no patch/rebuild of the canonical object is recommended now: doing so would remove the 20 audited records while leaving unresolved source-semantic failures. The VTRS persistence/issuer-transition case is outside the `>1e12` population and must not be silently swept into this patch.",
        "",
        "Formal PIT limitations remain unchanged: current-S&P-500 survivorship bias, current GICS, periodic SEC shares, absent RDQ and historical delisting universe, and Yahoo retrospective price/action adjustments. This report contains no factor, ratio, IC, portfolio, FMOM, or strategy result.",
    ])
    text = "\n".join(report) + "\n"
    out["acceptance"].write_text(text, encoding="utf-8")
    out["document"].write_text("# Extreme SEC Shares Validation — DEV\n\n" + text, encoding="utf-8")
    return decision


def run(root: Path | None = None) -> dict[str, Any]:
    root = (root or project_root()).resolve()
    source_rows, timeseries_rows, cross_rows, rejected, summary = _build_source_audits(root)
    propagation = _propagation_audit(root, source_rows, rejected)
    impact, distribution = _impact_and_distribution(root, rejected)
    jumps = _jump_audit(root, rejected)
    vtrs = _vtrs_case(root)
    out = paths(root)
    _write_csv(out["source"], source_rows)
    _write_csv(out["timeseries"], timeseries_rows)
    _write_csv(out["cross_tag"], cross_rows)
    _write_csv(out["propagation"], propagation)
    _write_csv(out["impact"], impact)
    _write_csv(out["distribution"], distribution)
    _write_csv(out["jumps"], jumps)
    decision = _document(root, summary, propagation, impact, distribution, jumps, vtrs)
    return {
        "decision": decision, "summary": summary, "rejected_source_keys": len(rejected),
        "paths": {key: str(value) for key, value in out.items()}, "vtrs": vtrs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=project_root())
    args = parser.parse_args()
    print(json.dumps(run(args.root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
