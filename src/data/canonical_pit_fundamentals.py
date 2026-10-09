"""Build DEV canonical SEC fundamental inputs and weekly PIT availability snapshots.

This module implements only the signed source-input controls.  It preserves
source facts and their period classes, generates no ratios or factor values,
and never modifies the immutable raw package.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Iterator

from src.data import sec_fundamental_spec as sec_spec
from src.data import source_audit


EVIDENCE_STAGE = "DEV_CURRENT_UNIVERSE"
CANONICAL_VERSION = "fe5209.us_pit_fundamentals.dev.v1"
USD = "USD"
ALLOWED_FORM_BASES = frozenset({"10-K", "10-Q", "20-F"})
INSTANT_CLASSES = ("instant",)
DURATION_CLASSES = (
    "annual_candidate",
    "quarterly_candidate",
    "ytd_candidate",
    "irregular_duration",
)


@dataclass(frozen=True)
class ConceptRule:
    economic_concept: str
    xbrl_tag: str
    source_kind: str
    allowed_period_classes: tuple[str, ...]


CONCEPT_RULES = (
    ConceptRule("net_income", "NetIncomeLoss", "duration", DURATION_CLASSES),
    ConceptRule("common_equity", "StockholdersEquity", "instant", INSTANT_CLASSES),
    ConceptRule("assets", "Assets", "instant", INSTANT_CLASSES),
    ConceptRule("liabilities", "Liabilities", "instant", INSTANT_CLASSES),
    ConceptRule("gross_profit", "GrossProfit", "duration", DURATION_CLASSES),
    ConceptRule(
        "operating_cash_flow",
        "NetCashProvidedByUsedInOperatingActivities",
        "duration",
        DURATION_CLASSES,
    ),
    ConceptRule(
        "capital_expenditure",
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "duration",
        DURATION_CLASSES,
    ),
)
RULE_BY_TAG = {rule.xbrl_tag: rule for rule in CONCEPT_RULES}
CONCEPT_BY_NAME = {rule.economic_concept: rule for rule in CONCEPT_RULES}


@dataclass(slots=True)
class FactCandidate:
    source_observation_id: str
    ticker: str
    yahoo_ticker: str
    cik: int
    issuer: str
    economic_concept: str
    taxonomy: str
    xbrl_tag: str
    source_value: str
    value_token: str
    unit: str
    start_date: str
    end_date: str
    period_length_days: int | None
    period_class: str
    fiscal_year: str
    fiscal_period: str
    form: str
    filed_date: str
    accession: str
    frame: str
    is_amendment: bool
    candidate_period_flag: bool
    availability_flag: bool
    ambiguity_flag: bool
    exclusion_reason: str

    @property
    def end(self) -> date:
        return date.fromisoformat(self.end_date)

    @property
    def filed(self) -> date:
        return date.fromisoformat(self.filed_date)

    @property
    def provenance_key(self) -> tuple[str, ...]:
        return (
            self.taxonomy,
            self.xbrl_tag,
            self.unit,
            self.form,
            self.accession,
            self.start_date,
            self.end_date,
            self.fiscal_year,
            self.fiscal_period,
            self.frame,
        )


@dataclass(slots=True)
class Selection:
    status: str
    candidate: FactCandidate | None = None
    reason: str = ""


@dataclass(slots=True)
class SlotState:
    """As-of state for one ticker/concept/period-class source series."""

    periods: dict[tuple[str, str], list[FactCandidate]]
    filing_dates: list[date] = field(init=False)
    groups_by_filed: dict[date, list[tuple[tuple[str, str], list[FactCandidate]]]] = field(init=False)
    current: dict[tuple[str, str], list[FactCandidate]] = field(default_factory=dict)
    cursor: int = 0

    def __post_init__(self) -> None:
        by_filed: dict[date, list[tuple[tuple[str, str], list[FactCandidate]]]] = defaultdict(list)
        for period_key, candidates in self.periods.items():
            per_filed: dict[date, list[FactCandidate]] = defaultdict(list)
            for candidate in candidates:
                per_filed[candidate.filed].append(candidate)
            for filed, group in per_filed.items():
                by_filed[filed].append((period_key, group))
        self.groups_by_filed = dict(by_filed)
        self.filing_dates = sorted(by_filed)

    def advance(self, signal_date: date) -> Selection:
        while self.cursor < len(self.filing_dates) and self.filing_dates[self.cursor] <= signal_date:
            filed = self.filing_dates[self.cursor]
            for period_key, group in self.groups_by_filed[filed]:
                self.current[period_key] = group
            self.cursor += 1
        if not self.current:
            return Selection("NO_ELIGIBLE_FILING", reason="NO_ELIGIBLE_FILING")
        latest_period = max(self.current, key=lambda item: (item[1], item[0]))
        return _resolve_latest_group(self.current[latest_period])


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def canonical_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / "data" / "canonical"


def audit_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / "outputs" / "audit" / "dev_legacy" / "sec_pit"


def output_paths(root: Path | None = None) -> dict[str, Path]:
    canonical = canonical_dir(root)
    audit = audit_dir(root)
    return {
        "facts_db": canonical / "us_pit_fundamental_facts_dev.sqlite",
        "weekly_db": canonical / "us_weekly_pit_fundamentals_dev.sqlite",
        "coverage": audit / "sec_weekly_fundamental_coverage_dev.csv",
        "coverage_windows": audit / "sec_weekly_fundamental_coverage_by_window_dev.csv",
        "staleness": audit / "sec_fundamental_staleness_dev.csv",
        "ambiguity": audit / "sec_fundamental_ambiguity_dev.csv",
        "period_coverage": audit / "sec_fundamental_period_coverage_dev.csv",
        "amendment_switches": audit / "sec_fundamental_amendment_switches_dev.csv",
        "amendment_switch_summary": audit / "sec_fundamental_amendment_switch_summary_dev.csv",
        "open_issues": audit / "sec_canonical_pit_open_implementation_issues_dev.csv",
        "summary": audit / "sec_canonical_pit_fundamental_summary_dev.json",
        "acceptance": audit / "sec_canonical_pit_acceptance_report.md",
    }


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _value_token(value: Any) -> str | None:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return str(decimal.normalize()) if decimal.is_finite() else None


def _base_form(form: str) -> str:
    return form.removesuffix("/A")


def _expected_period_class(rule: ConceptRule, period_class: str) -> bool:
    return period_class in rule.allowed_period_classes


def _classify_source_observation(
    rule: ConceptRule, taxonomy: str, unit: str, observation: dict[str, Any]
) -> tuple[str, int | None, bool, bool, str]:
    """Classify a raw observation without fallback, conversion, or selection."""

    period_class, period_length_days, _ = sec_spec.classify_period(observation)
    form = str(observation.get("form", ""))
    value_token = _value_token(observation.get("val"))
    end = _parse_date(observation.get("end"))
    filed = _parse_date(observation.get("filed"))
    candidate_period = True
    reason = ""
    if taxonomy != "us-gaap":
        candidate_period = False
        reason = "UNSUPPORTED_TAXONOMY"
    elif unit != USD:
        candidate_period = False
        reason = "UNIT_REJECTED"
    elif _base_form(form) not in ALLOWED_FORM_BASES:
        candidate_period = False
        reason = "FORM_REJECTED"
    elif not _expected_period_class(rule, period_class):
        candidate_period = False
        reason = "PERIOD_CLASS_MISMATCH"
    elif value_token is None or end is None or filed is None:
        candidate_period = False
        reason = "INVALID_REQUIRED_SOURCE_FIELD"
    elif end > filed:
        candidate_period = False
        reason = "OPEN_IMPLEMENTATION_ISSUE_FUTURE_PERIOD_END"
    available = candidate_period
    if candidate_period and rule.economic_concept == "capital_expenditure":
        value = Decimal(value_token)
        if value < 0:
            available = False
            reason = "SIGN_EXCEPTION_NEGATIVE_CAPEX"
    return period_class, period_length_days, candidate_period, available, reason or "AVAILABLE_SOURCE"


def _read_security_mapping(root: Path) -> tuple[list[dict[str, str]], dict[int, list[dict[str, str]]], list[dict[str, str]]]:
    master_path = root / "data" / "canonical" / "us_security_master_dev.csv"
    _, master_rows = source_audit._read_csv_rows(master_path)
    package = source_audit.package_root(root)
    _, source_map_rows = source_audit._read_csv_rows(package / source_audit.SEC_CIK_MAP_FILE)
    master_by_ticker = {row["ticker"]: row for row in master_rows}
    source_tickers = {row["ticker"] for row in source_map_rows}
    source_ciks: dict[str, int] = {}
    for row in source_map_rows:
        cik = source_audit._as_int(row.get("cik"))
        if cik is None or row["ticker"] not in master_by_ticker:
            raise ValueError(f"SEC security mapping mismatch: {row}")
        source_ciks[row["ticker"]] = cik
        master_cik = source_audit._as_int(master_by_ticker[row["ticker"]].get("cik"))
        if master_cik != cik:
            raise ValueError(f"Canonical/master CIK mismatch for {row['ticker']}")
    mapped = [master_by_ticker[ticker] for ticker in sorted(source_tickers)]
    by_cik: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in mapped:
        cik = source_ciks[row["ticker"]]
        by_cik[cik].append(row)
    unmapped = [row for row in master_rows if row["ticker"] not in source_tickers]
    return sorted(master_rows, key=lambda row: row["ticker"]), dict(by_cik), sorted(unmapped, key=lambda row: row["ticker"])


def _read_signal_dates(root: Path) -> list[date]:
    path = root / "data" / "canonical" / "us_weekly_signal_calendar_dev.csv"
    _, rows = source_audit._read_csv_rows(path)
    dates = [date.fromisoformat(row["signal_date"]) for row in rows]
    if any(right <= left for left, right in zip(dates, dates[1:])):
        raise ValueError("Weekly signal calendar is not strictly increasing")
    return dates


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA temp_store=MEMORY")
    return connection


def _initialize_facts_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE canonical_fundamental_facts (
            ticker TEXT NOT NULL,
            yahoo_ticker TEXT NOT NULL,
            cik INTEGER NOT NULL,
            issuer TEXT NOT NULL,
            economic_concept TEXT NOT NULL,
            taxonomy TEXT NOT NULL,
            xbrl_tag TEXT NOT NULL,
            canonical_value TEXT,
            source_value TEXT,
            source_value_token TEXT,
            unit TEXT NOT NULL,
            start_date TEXT,
            end_date TEXT NOT NULL,
            period_length_days INTEGER,
            period_class TEXT NOT NULL,
            fiscal_year TEXT,
            fiscal_period TEXT,
            form TEXT NOT NULL,
            filed_date TEXT NOT NULL,
            accession TEXT NOT NULL,
            frame TEXT,
            amendment_flag INTEGER NOT NULL,
            ambiguity_flag INTEGER NOT NULL,
            candidate_period_flag INTEGER NOT NULL,
            availability_flag INTEGER NOT NULL,
            exclusion_reason TEXT NOT NULL,
            source_observation_id TEXT NOT NULL,
            source_archive_path TEXT NOT NULL,
            canonical_version TEXT NOT NULL,
            evidence_stage TEXT NOT NULL,
            PRIMARY KEY (ticker, source_observation_id)
        );
        CREATE INDEX facts_selection_index ON canonical_fundamental_facts
            (ticker, economic_concept, period_class, filed_date, end_date);
        CREATE INDEX facts_source_index ON canonical_fundamental_facts
            (cik, taxonomy, xbrl_tag, unit, start_date, end_date, filed_date);
        """
    )


def _initialize_weekly_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE weekly_security_universe (
            ticker TEXT NOT NULL,
            yahoo_ticker TEXT NOT NULL,
            cik INTEGER,
            issuer TEXT,
            signal_date TEXT NOT NULL,
            sec_mapping_status TEXT NOT NULL,
            canonical_version TEXT NOT NULL,
            evidence_stage TEXT NOT NULL,
            PRIMARY KEY (ticker, signal_date)
        );
        CREATE TABLE weekly_pit_fundamental_snapshot (
            ticker TEXT NOT NULL,
            yahoo_ticker TEXT NOT NULL,
            cik INTEGER NOT NULL,
            issuer TEXT NOT NULL,
            signal_date TEXT NOT NULL,
            economic_concept TEXT NOT NULL,
            period_class TEXT NOT NULL,
            canonical_value TEXT NOT NULL,
            taxonomy TEXT NOT NULL,
            xbrl_tag TEXT NOT NULL,
            unit TEXT NOT NULL,
            start_date TEXT,
            end_date TEXT NOT NULL,
            period_length_days INTEGER,
            fiscal_year TEXT,
            fiscal_period TEXT,
            form TEXT NOT NULL,
            filed_date TEXT NOT NULL,
            accession TEXT NOT NULL,
            frame TEXT,
            amendment_flag INTEGER NOT NULL,
            source_observation_id TEXT NOT NULL,
            age_since_period_end_days INTEGER NOT NULL,
            age_since_filing_days INTEGER NOT NULL,
            canonical_version TEXT NOT NULL,
            evidence_stage TEXT NOT NULL,
            PRIMARY KEY (ticker, signal_date, economic_concept, period_class)
        );
        CREATE INDEX weekly_snapshot_lookup ON weekly_pit_fundamental_snapshot
            (signal_date, economic_concept, period_class, ticker);
        CREATE TABLE weekly_pit_fundamental_unavailability (
            ticker TEXT NOT NULL,
            signal_date TEXT NOT NULL,
            economic_concept TEXT NOT NULL,
            period_class TEXT NOT NULL,
            status TEXT NOT NULL,
            exclusion_reason TEXT NOT NULL,
            candidate_end_date TEXT,
            candidate_filed_date TEXT,
            canonical_version TEXT NOT NULL,
            evidence_stage TEXT NOT NULL,
            PRIMARY KEY (ticker, signal_date, economic_concept, period_class)
        );
        """
    )


def _iter_target_observations(
    package: Path,
) -> Iterator[tuple[int, str, str, str, dict[str, Any], int, str]]:
    raw_dir = package / source_audit.SEC_FACTS_DIR
    for archive_path in sorted(raw_dir.glob("CIK*.json.gz")):
        with gzip.open(archive_path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        cik = source_audit._as_int(payload.get("cik"))
        if cik is None:
            raise ValueError(f"Missing usable CIK in {archive_path.name}")
        facts = payload.get("facts", {})
        if not isinstance(facts, dict):
            continue
        for taxonomy, tag_map in facts.items():
            if not isinstance(tag_map, dict):
                continue
            for tag, rule in RULE_BY_TAG.items():
                tag_payload = tag_map.get(tag)
                if not isinstance(tag_payload, dict):
                    continue
                units = tag_payload.get("units", {})
                if not isinstance(units, dict):
                    continue
                for unit, observations in units.items():
                    if not isinstance(observations, list):
                        continue
                    for ordinal, observation in enumerate(observations):
                        if isinstance(observation, dict):
                            yield cik, str(taxonomy), tag, str(unit), observation, ordinal, payload.get("entityName", "")


def _fact_insert_row(candidate: FactCandidate, archive_path: str) -> tuple[Any, ...]:
    return (
        candidate.ticker,
        candidate.yahoo_ticker,
        candidate.cik,
        candidate.issuer,
        candidate.economic_concept,
        candidate.taxonomy,
        candidate.xbrl_tag,
        candidate.source_value if candidate.availability_flag else None,
        candidate.source_value,
        candidate.value_token,
        candidate.unit,
        candidate.start_date or None,
        candidate.end_date,
        candidate.period_length_days,
        candidate.period_class,
        candidate.fiscal_year or None,
        candidate.fiscal_period or None,
        candidate.form,
        candidate.filed_date,
        candidate.accession,
        candidate.frame or None,
        int(candidate.is_amendment),
        int(candidate.ambiguity_flag),
        int(candidate.candidate_period_flag),
        int(candidate.availability_flag),
        candidate.exclusion_reason,
        candidate.source_observation_id,
        archive_path,
        CANONICAL_VERSION,
        EVIDENCE_STAGE,
    )


def _build_facts_database(root: Path) -> dict[str, Any]:
    paths = output_paths(root)
    temporary = paths["facts_db"].with_suffix(".sqlite.tmp")
    if temporary.exists():
        temporary.unlink()
    master_rows, securities_by_cik, unmapped_master = _read_security_mapping(root)
    package = source_audit.package_root(root)
    connection = _connect(temporary)
    _initialize_facts_schema(connection)
    insert_sql = (
        "INSERT INTO canonical_fundamental_facts VALUES ("
        + ", ".join("?" for _ in range(30))
        + ")"
    )
    rows: list[tuple[Any, ...]] = []
    source_counts: Counter[str] = Counter()
    archive_ciks: set[int] = set()
    mapped_ciks: set[int] = set()
    for cik, taxonomy, tag, unit, observation, ordinal, issuer_name in _iter_target_observations(package):
        archive_ciks.add(cik)
        rule = RULE_BY_TAG[tag]
        period_class, period_length, candidate_period, available, reason = _classify_source_observation(
            rule, taxonomy, unit, observation
        )
        end = _parse_date(observation.get("end"))
        filed = _parse_date(observation.get("filed"))
        if end is None or filed is None:
            # The raw contract reports all observations have these fields, but preserve a no-value row if not.
            end_text = str(observation.get("end", ""))
            filed_text = str(observation.get("filed", ""))
        else:
            end_text = end.isoformat()
            filed_text = filed.isoformat()
        value_token = _value_token(observation.get("val")) or ""
        source_id = ":".join(
            (
                str(cik),
                taxonomy,
                tag,
                unit,
                str(observation.get("start", "")),
                end_text,
                filed_text,
                str(observation.get("form", "")),
                str(observation.get("accn", "")),
                str(ordinal),
            )
        )
        mapped_securities = securities_by_cik.get(cik, [])
        if not mapped_securities:
            source_counts["UNMAPPED_ARCHIVE_CIK"] += 1
            continue
        mapped_ciks.add(cik)
        for security in mapped_securities:
            candidate = FactCandidate(
                source_observation_id=source_id,
                ticker=security["ticker"],
                yahoo_ticker=security["yahoo_ticker"],
                cik=cik,
                issuer=str(issuer_name),
                economic_concept=rule.economic_concept,
                taxonomy=taxonomy,
                xbrl_tag=tag,
                source_value=str(observation.get("val", "")),
                value_token=value_token,
                unit=unit,
                start_date=str(observation.get("start", "")),
                end_date=end_text,
                period_length_days=period_length,
                period_class=period_class,
                fiscal_year=str(observation.get("fy", "")),
                fiscal_period=str(observation.get("fp", "")),
                form=str(observation.get("form", "")),
                filed_date=filed_text,
                accession=str(observation.get("accn", "")),
                frame=str(observation.get("frame", "")),
                is_amendment=str(observation.get("form", "")).endswith("/A"),
                candidate_period_flag=candidate_period,
                availability_flag=available,
                ambiguity_flag=False,
                exclusion_reason=reason,
            )
            rows.append(_fact_insert_row(candidate, f"{source_audit.SEC_FACTS_DIR.as_posix()}/CIK{cik:010d}.json.gz"))
            source_counts[reason] += 1
        if len(rows) >= 20_000:
            connection.executemany(insert_sql, rows)
            rows.clear()
    if rows:
        connection.executemany(insert_sql, rows)
    connection.commit()

    # Conflicts are evaluated across all same-taxonomy/tag/unit/economic-period
    # facts at a filed date.  A later source filing with divergent values cannot
    # be turned into a canonical value by row-order or accession selection.
    connection.executescript(
        """
        CREATE TEMP TABLE ambiguous_source_groups AS
        SELECT cik, taxonomy, xbrl_tag, unit, COALESCE(start_date, '') AS start_key,
               end_date, filed_date
        FROM canonical_fundamental_facts
        WHERE candidate_period_flag = 1
        GROUP BY cik, taxonomy, xbrl_tag, unit, COALESCE(start_date, ''), end_date, filed_date
        HAVING COUNT(DISTINCT source_value_token) > 1;

        UPDATE canonical_fundamental_facts
        SET ambiguity_flag = 1,
            availability_flag = 0,
            canonical_value = NULL,
            exclusion_reason = 'AMBIGUOUS_SAME_FILED_CONFLICT'
        WHERE candidate_period_flag = 1
          AND EXISTS (
            SELECT 1 FROM ambiguous_source_groups groups
            WHERE groups.cik = canonical_fundamental_facts.cik
              AND groups.taxonomy = canonical_fundamental_facts.taxonomy
              AND groups.xbrl_tag = canonical_fundamental_facts.xbrl_tag
              AND groups.unit = canonical_fundamental_facts.unit
              AND groups.start_key = COALESCE(canonical_fundamental_facts.start_date, '')
              AND groups.end_date = canonical_fundamental_facts.end_date
              AND groups.filed_date = canonical_fundamental_facts.filed_date
          );
        """
    )
    metadata = {
        "canonical_version": CANONICAL_VERSION,
        "evidence_stage": EVIDENCE_STAGE,
        "format": "SQLite; source numeric representation is retained as text to avoid implicit rounding before factor engineering.",
        "raw_package": str(source_audit.package_root(root).relative_to(root)),
        "master_security_count": str(len(master_rows)),
        "sec_mapped_security_count": str(sum(len(items) for items in securities_by_cik.values())),
        "sec_mapped_issuer_count": str(len(securities_by_cik)),
        "unmapped_master_security_count": str(len(unmapped_master)),
    }
    connection.executemany("INSERT INTO metadata VALUES (?, ?)", sorted(metadata.items()))
    counts = {
        "fact_rows": connection.execute("SELECT COUNT(*) FROM canonical_fundamental_facts").fetchone()[0],
        "available_fact_rows": connection.execute(
            "SELECT COUNT(*) FROM canonical_fundamental_facts WHERE availability_flag = 1"
        ).fetchone()[0],
        "ambiguous_fact_rows": connection.execute(
            "SELECT COUNT(*) FROM canonical_fundamental_facts WHERE ambiguity_flag = 1"
        ).fetchone()[0],
        "ambiguous_source_groups": connection.execute("SELECT COUNT(*) FROM ambiguous_source_groups").fetchone()[0],
    }
    # Report final statuses after conflicts have been converted to unavailable
    # facts; the initial scan status is not the canonical availability status.
    source_counts = Counter(
        {
            reason: count
            for reason, count in connection.execute(
                "SELECT exclusion_reason, COUNT(*) FROM canonical_fundamental_facts "
                "GROUP BY exclusion_reason"
            )
        }
    )
    connection.commit()
    connection.close()
    os.replace(temporary, paths["facts_db"])
    return {
        "counts": counts,
        "source_counts": dict(source_counts),
        "master_rows": master_rows,
        "securities_by_cik": securities_by_cik,
        "unmapped_master": unmapped_master,
        "archive_cik_count": len(archive_ciks),
        "mapped_cik_count": len(mapped_ciks),
    }


def _load_facts_for_snapshot(facts_db: Path) -> tuple[
    dict[tuple[str, str, str], dict[tuple[str, str], list[FactCandidate]]],
    dict[tuple[str, str], list[tuple[date, str]]],
]:
    """Load the small signed-tag source surface into as-of selection structures."""

    connection = sqlite3.connect(facts_db)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT ticker, yahoo_ticker, cik, issuer, economic_concept, taxonomy, xbrl_tag,
               source_value, source_value_token, unit, start_date, end_date, period_length_days,
               period_class, fiscal_year, fiscal_period, form, filed_date, accession, frame,
               amendment_flag, ambiguity_flag, candidate_period_flag, availability_flag,
               exclusion_reason, source_observation_id
        FROM canonical_fundamental_facts
        ORDER BY ticker, economic_concept, period_class, end_date, filed_date, source_observation_id
        """
    )
    slots: dict[tuple[str, str, str], dict[tuple[str, str], list[FactCandidate]]] = defaultdict(lambda: defaultdict(list))
    rejections: dict[tuple[str, str], list[tuple[date, str]]] = defaultdict(list)
    for row in rows:
        filed = _parse_date(row["filed_date"])
        if filed is None:
            continue
        if not row["candidate_period_flag"]:
            rejections[(row["ticker"], row["economic_concept"])].append((filed, row["exclusion_reason"]))
            continue
        candidate = FactCandidate(
            source_observation_id=row["source_observation_id"],
            ticker=row["ticker"],
            yahoo_ticker=row["yahoo_ticker"],
            cik=row["cik"],
            issuer=row["issuer"],
            economic_concept=row["economic_concept"],
            taxonomy=row["taxonomy"],
            xbrl_tag=row["xbrl_tag"],
            source_value=row["source_value"],
            value_token=row["source_value_token"],
            unit=row["unit"],
            start_date=row["start_date"] or "",
            end_date=row["end_date"],
            period_length_days=row["period_length_days"],
            period_class=row["period_class"],
            fiscal_year=row["fiscal_year"] or "",
            fiscal_period=row["fiscal_period"] or "",
            form=row["form"],
            filed_date=row["filed_date"],
            accession=row["accession"],
            frame=row["frame"] or "",
            is_amendment=bool(row["amendment_flag"]),
            candidate_period_flag=True,
            availability_flag=bool(row["availability_flag"]),
            ambiguity_flag=bool(row["ambiguity_flag"]),
            exclusion_reason=row["exclusion_reason"],
        )
        slots[(candidate.ticker, candidate.economic_concept, candidate.period_class)][
            (candidate.start_date, candidate.end_date)
        ].append(candidate)
    connection.close()
    for records in rejections.values():
        records.sort()
    return slots, rejections


def _resolve_latest_group(group: list[FactCandidate]) -> Selection:
    """Resolve one latest eligible filing-date group without an arbitrary tie-break."""

    if any(candidate.ambiguity_flag for candidate in group):
        return Selection("AMBIGUOUS", reason="AMBIGUOUS_SAME_FILED_CONFLICT")
    values = {candidate.value_token for candidate in group}
    if len(values) > 1:
        return Selection("AMBIGUOUS", reason="AMBIGUOUS_SAME_FILED_CONFLICT")
    unavailable_reasons = {candidate.exclusion_reason for candidate in group if not candidate.availability_flag}
    if "SIGN_EXCEPTION_NEGATIVE_CAPEX" in unavailable_reasons:
        return Selection("SIGN_EXCEPTION", reason="SIGN_EXCEPTION_NEGATIVE_CAPEX")
    available = [candidate for candidate in group if candidate.availability_flag]
    if not available:
        return Selection("UNAVAILABLE", reason=next(iter(unavailable_reasons), "UNAVAILABLE_SOURCE"))
    provenance = {candidate.provenance_key for candidate in available}
    if len(provenance) > 1:
        return Selection(
            "OPEN_IMPLEMENTATION_ISSUE",
            reason="OPEN_IMPLEMENTATION_ISSUE_NONUNIQUE_PROVENANCE",
        )
    # Exact duplicate source lines are one logical observation: all required
    # provenance and value fields agree. This representative is never used to
    # resolve a value conflict.
    selected = min(available, key=lambda item: item.source_observation_id)
    return Selection("AVAILABLE", candidate=selected, reason="AVAILABLE")


def _reason_from_rejections(
    rejections: list[tuple[date, str]], signal_date: date
) -> str:
    seen = {reason for filed, reason in rejections if filed <= signal_date}
    for reason in (
        "UNIT_REJECTED",
        "PERIOD_CLASS_MISMATCH",
        "FORM_REJECTED",
        "UNSUPPORTED_TAXONOMY",
        "INVALID_REQUIRED_SOURCE_FIELD",
        "OPEN_IMPLEMENTATION_ISSUE_FUTURE_PERIOD_END",
    ):
        if reason in seen:
            return reason
    return "NO_ELIGIBLE_FILING"


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def _age_summary(counter: Counter[int]) -> dict[str, float | int | None]:
    count = sum(counter.values())
    if not count:
        return {"count": 0, "mean": None, "p05": None, "median": None, "p95": None, "max": None}
    total = sum(value * frequency for value, frequency in counter.items())
    values: list[float] = []
    for value, frequency in counter.items():
        values.extend([float(value)] * frequency)
    return {
        "count": count,
        "mean": total / count,
        "p05": _percentile(values, 0.05),
        "median": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "max": max(counter),
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


def _window_name(signal_date: date) -> str | None:
    if date(2011, 1, 1) <= signal_date <= date(2015, 12, 31):
        return "factor_selection_2011_2015"
    if date(2016, 1, 1) <= signal_date <= date(2020, 12, 31):
        return "configuration_selection_2016_2020"
    if date(2021, 1, 1) <= signal_date <= date(2026, 9, 30):
        return "historical_oos_2021_2026_09"
    return None


def _build_weekly_database(root: Path, facts_result: dict[str, Any]) -> dict[str, Any]:
    paths = output_paths(root)
    temporary = paths["weekly_db"].with_suffix(".sqlite.tmp")
    if temporary.exists():
        temporary.unlink()
    master_rows = facts_result["master_rows"]
    securities_by_cik = facts_result["securities_by_cik"]
    unmapped_master = facts_result["unmapped_master"]
    signal_dates = _read_signal_dates(root)
    slots, rejections = _load_facts_for_snapshot(paths["facts_db"])
    mapped_by_ticker = {
        security["ticker"]: security
        for securities in securities_by_cik.values()
        for security in securities
    }
    ticker_to_issuer: dict[str, str] = {}
    for (ticker, _concept, _period_class), period_map in slots.items():
        for candidates in period_map.values():
            if candidates:
                ticker_to_issuer[ticker] = candidates[0].issuer
                break

    connection = _connect(temporary)
    _initialize_weekly_schema(connection)
    universe_rows: list[tuple[Any, ...]] = []
    for security in master_rows:
        cik = source_audit._as_int(security.get("cik"))
        mapped = security["ticker"] in mapped_by_ticker
        for signal_date in signal_dates:
            universe_rows.append(
                (
                    security["ticker"],
                    security["yahoo_ticker"],
                    cik if mapped else None,
                    ticker_to_issuer.get(security["ticker"]),
                    signal_date.isoformat(),
                    "SEC_CIK_MAPPED" if mapped else "SEC_MAPPING_UNAVAILABLE",
                    CANONICAL_VERSION,
                    EVIDENCE_STAGE,
                )
            )
            if len(universe_rows) >= 20_000:
                connection.executemany("INSERT INTO weekly_security_universe VALUES (?, ?, ?, ?, ?, ?, ?, ?)", universe_rows)
                universe_rows.clear()
    if universe_rows:
        connection.executemany("INSERT INTO weekly_security_universe VALUES (?, ?, ?, ?, ?, ?, ?, ?)", universe_rows)

    coverage: dict[tuple[date, str], Counter[str]] = defaultdict(Counter)
    staleness_period: dict[tuple[str, str], Counter[int]] = defaultdict(Counter)
    staleness_filing: dict[tuple[str, str], Counter[int]] = defaultdict(Counter)
    period_counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    amendment_rows: list[dict[str, Any]] = []
    issue_rows: list[dict[str, Any]] = []
    snapshot_rows: list[tuple[Any, ...]] = []
    unavailable_rows: list[tuple[Any, ...]] = []
    previous_selection: dict[tuple[str, str, str], str | None] = {}
    previous_candidate: dict[tuple[str, str, str], FactCandidate | None] = {}
    mapped_tickers = sorted(mapped_by_ticker)
    eligible_count = len(mapped_tickers)
    for ticker in mapped_tickers:
        security = mapped_by_ticker[ticker]
        cik = source_audit._as_int(security.get("cik"))
        if cik is None:
            raise ValueError(f"Mapped security has no CIK: {ticker}")
        slot_states: dict[tuple[str, str], SlotState] = {}
        for rule in CONCEPT_RULES:
            for period_class in rule.allowed_period_classes:
                period_map = slots.get((ticker, rule.economic_concept, period_class), {})
                slot_states[(rule.economic_concept, period_class)] = SlotState(period_map)
        for signal_date in signal_dates:
            concept_results: dict[str, list[Selection]] = defaultdict(list)
            for (concept, period_class), state in slot_states.items():
                selection = state.advance(signal_date)
                concept_results[concept].append(selection)
                selection_key = (ticker, concept, period_class)
                prior_source = previous_selection.get(selection_key)
                prior_candidate = previous_candidate.get(selection_key)
                current_source = selection.candidate.source_observation_id if selection.candidate else None
                if selection.status == "AVAILABLE" and selection.candidate is not None:
                    candidate = selection.candidate
                    period_age = (signal_date - candidate.end).days
                    filing_age = (signal_date - candidate.filed).days
                    if period_age < 0 or filing_age < 0:
                        raise ValueError(f"PIT age became negative for {candidate.source_observation_id}")
                    snapshot_rows.append(
                        (
                            candidate.ticker,
                            candidate.yahoo_ticker,
                            candidate.cik,
                            candidate.issuer,
                            signal_date.isoformat(),
                            candidate.economic_concept,
                            candidate.period_class,
                            candidate.source_value,
                            candidate.taxonomy,
                            candidate.xbrl_tag,
                            candidate.unit,
                            candidate.start_date or None,
                            candidate.end_date,
                            candidate.period_length_days,
                            candidate.fiscal_year or None,
                            candidate.fiscal_period or None,
                            candidate.form,
                            candidate.filed_date,
                            candidate.accession,
                            candidate.frame or None,
                            int(candidate.is_amendment),
                            candidate.source_observation_id,
                            period_age,
                            filing_age,
                            CANONICAL_VERSION,
                            EVIDENCE_STAGE,
                        )
                    )
                    staleness_period[(concept, period_class)][period_age] += 1
                    staleness_filing[(concept, period_class)][filing_age] += 1
                    period_counts[(concept, period_class)]["available_snapshot_rows"] += 1
                    if candidate.is_amendment and current_source != prior_source:
                        if prior_candidate is None:
                            switch_type = "NO_PRIOR_AVAILABLE_VALUE"
                        elif (
                            candidate.start_date == prior_candidate.start_date
                            and candidate.end_date == prior_candidate.end_date
                        ):
                            switch_type = "SAME_PERIOD_AMENDMENT"
                        elif candidate.end > prior_candidate.end:
                            switch_type = "LATER_PERIOD_AMENDMENT"
                        else:
                            switch_type = "OTHER_PERIOD_AMENDMENT"
                        amendment_rows.append(
                            {
                                "evidence_stage": EVIDENCE_STAGE,
                                "ticker": ticker,
                                "signal_date": signal_date.isoformat(),
                                "economic_concept": concept,
                                "period_class": period_class,
                                "prior_source_observation_id": prior_source or "",
                                "prior_source_end_date": prior_candidate.end_date if prior_candidate else "",
                                "amendment_source_observation_id": current_source,
                                "source_filed_date": candidate.filed_date,
                                "source_end_date": candidate.end_date,
                                "source_accession": candidate.accession,
                                "switch_type": switch_type,
                                "note": "Amendment enters only from its own filed date; no historical backfill is performed.",
                            }
                        )
                elif selection.status in {"AMBIGUOUS", "SIGN_EXCEPTION", "OPEN_IMPLEMENTATION_ISSUE"}:
                    current_candidate = None
                    if state.current:
                        latest_period = max(state.current, key=lambda item: (item[1], item[0]))
                        current_group = state.current[latest_period]
                        current_candidate = current_group[0] if current_group else None
                    unavailable_rows.append(
                        (
                            ticker,
                            signal_date.isoformat(),
                            concept,
                            period_class,
                            selection.status,
                            selection.reason,
                            current_candidate.end_date if current_candidate else None,
                            current_candidate.filed_date if current_candidate else None,
                            CANONICAL_VERSION,
                            EVIDENCE_STAGE,
                        )
                    )
                    if selection.status == "OPEN_IMPLEMENTATION_ISSUE":
                        issue_rows.append(
                            {
                                "evidence_stage": EVIDENCE_STAGE,
                                "issue_type": selection.reason,
                                "ticker": ticker,
                                "cik": cik,
                                "economic_concept": concept,
                                "period_class": period_class,
                                "signal_date": signal_date.isoformat(),
                                "disposition": "Unavailable/missing; no deterministic provenance tie-break was invented.",
                            }
                        )
                previous_selection[selection_key] = current_source
                previous_candidate[selection_key] = (
                    selection.candidate if selection.status == "AVAILABLE" else None
                )
                if len(snapshot_rows) >= 20_000:
                    connection.executemany(
                        "INSERT INTO weekly_pit_fundamental_snapshot VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        snapshot_rows,
                    )
                    snapshot_rows.clear()
                if len(unavailable_rows) >= 20_000:
                    connection.executemany(
                        "INSERT INTO weekly_pit_fundamental_unavailability VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        unavailable_rows,
                    )
                    unavailable_rows.clear()
            for rule in CONCEPT_RULES:
                results = concept_results[rule.economic_concept]
                summary = coverage[(signal_date, rule.economic_concept)]
                summary["eligible_count"] = eligible_count
                if any(result.status == "AVAILABLE" for result in results):
                    summary["available_count"] += 1
                elif any(result.status == "AMBIGUOUS" for result in results):
                    summary["ambiguous_count"] += 1
                elif any(result.status == "SIGN_EXCEPTION" for result in results):
                    summary["sign_exception_count"] += 1
                elif any(result.status == "OPEN_IMPLEMENTATION_ISSUE" for result in results):
                    summary["open_implementation_issue_count"] += 1
                else:
                    rejection = _reason_from_rejections(
                        rejections.get((ticker, rule.economic_concept), []), signal_date
                    )
                    if rejection == "UNIT_REJECTED":
                        summary["unit_failure_count"] += 1
                    elif rejection == "PERIOD_CLASS_MISMATCH":
                        summary["period_failure_count"] += 1
                    elif rejection == "FORM_REJECTED":
                        summary["form_failure_count"] += 1
                    elif rejection == "UNSUPPORTED_TAXONOMY":
                        summary["taxonomy_failure_count"] += 1
                    elif rejection == "OPEN_IMPLEMENTATION_ISSUE_FUTURE_PERIOD_END":
                        summary["open_implementation_issue_count"] += 1
                    else:
                        summary["no_eligible_filing_count"] += 1
        connection.commit()
    if snapshot_rows:
        connection.executemany(
            "INSERT INTO weekly_pit_fundamental_snapshot VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            snapshot_rows,
        )
    if unavailable_rows:
        connection.executemany(
            "INSERT INTO weekly_pit_fundamental_unavailability VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            unavailable_rows,
        )
    metadata = {
        "canonical_version": CANONICAL_VERSION,
        "evidence_stage": EVIDENCE_STAGE,
        "snapshot_format": "Sparse long fact rows plus complete weekly_security_universe grid; absent fact row means unavailable and coverage audits retain its reason.",
        "period_hierarchy": "Not frozen. Each instant/annual_candidate/quarterly_candidate/ytd_candidate/irregular_duration series remains separate.",
        "eligible_sec_mapped_security_count": str(eligible_count),
        "unmapped_master_security_count": str(len(unmapped_master)),
        "signal_date_count": str(len(signal_dates)),
    }
    connection.executemany("INSERT INTO metadata VALUES (?, ?)", sorted(metadata.items()))
    unavailability_counts = dict(
        connection.execute(
            "SELECT status, COUNT(*) FROM weekly_pit_fundamental_unavailability GROUP BY status"
        )
    )
    weekly_counts = {
        "weekly_universe_rows": connection.execute("SELECT COUNT(*) FROM weekly_security_universe").fetchone()[0],
        "snapshot_rows": connection.execute("SELECT COUNT(*) FROM weekly_pit_fundamental_snapshot").fetchone()[0],
        "unavailability_rows": connection.execute("SELECT COUNT(*) FROM weekly_pit_fundamental_unavailability").fetchone()[0],
        "snapshot_ticker_count": connection.execute("SELECT COUNT(DISTINCT ticker) FROM weekly_pit_fundamental_snapshot").fetchone()[0],
        "snapshot_signal_date_count": connection.execute("SELECT COUNT(DISTINCT signal_date) FROM weekly_pit_fundamental_snapshot").fetchone()[0],
        "pit_violations": connection.execute(
            "SELECT COUNT(*) FROM weekly_pit_fundamental_snapshot WHERE filed_date > signal_date"
        ).fetchone()[0],
        "provenance_nonunique": connection.execute(
            """
            SELECT COUNT(*) FROM (
              SELECT ticker, signal_date, economic_concept, period_class, COUNT(*) AS count
              FROM weekly_pit_fundamental_snapshot
              GROUP BY ticker, signal_date, economic_concept, period_class HAVING count <> 1
            )
            """
        ).fetchone()[0],
        "ambiguous_unavailability_rows": unavailability_counts.get("AMBIGUOUS", 0),
        "sign_exception_unavailability_rows": unavailability_counts.get("SIGN_EXCEPTION", 0),
        "open_implementation_issue_unavailability_rows": unavailability_counts.get(
            "OPEN_IMPLEMENTATION_ISSUE", 0
        ),
    }
    connection.commit()
    connection.close()
    os.replace(temporary, paths["weekly_db"])
    return {
        "coverage": coverage,
        "staleness_period": staleness_period,
        "staleness_filing": staleness_filing,
        "period_counts": period_counts,
        "amendment_rows": amendment_rows,
        "issue_rows": issue_rows,
        "weekly_counts": weekly_counts,
        "eligible_count": eligible_count,
        "unmapped_master": unmapped_master,
        "signal_dates": signal_dates,
    }


def _write_audits(root: Path, facts_result: dict[str, Any], weekly_result: dict[str, Any]) -> dict[str, Any]:
    paths = output_paths(root)
    coverage_rows: list[dict[str, Any]] = []
    by_concept_rates: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for (signal_date, concept), counts in sorted(weekly_result["coverage"].items()):
        eligible = counts["eligible_count"]
        available = counts["available_count"]
        rate = available / eligible if eligible else 0.0
        coverage_rows.append(
            {
                "evidence_stage": EVIDENCE_STAGE,
                "signal_date": signal_date.isoformat(),
                "economic_concept": concept,
                "eligible_count": eligible,
                "available_count": available,
                "coverage_rate": f"{rate:.9f}",
                "ambiguous_count": counts["ambiguous_count"],
                "unit_failure_count": counts["unit_failure_count"],
                "period_failure_count": counts["period_failure_count"],
                "sign_exception_count": counts["sign_exception_count"],
                "no_eligible_filing_count": counts["no_eligible_filing_count"],
                "form_failure_count": counts["form_failure_count"],
                "taxonomy_failure_count": counts["taxonomy_failure_count"],
                "open_implementation_issue_count": counts["open_implementation_issue_count"],
                "current_gics_note": "Current GICS is not used in coverage eligibility and is not historical PIT evidence.",
                "evidence_stage_note": "SEC-mapped current DEV securities only; 3 current-master securities have no SEC map and are outside this denominator.",
            }
        )
        by_concept_rates[concept].append((signal_date, rate))
    _write_csv(paths["coverage"], coverage_rows)

    window_rows: list[dict[str, Any]] = []
    for concept, values in sorted(by_concept_rates.items()):
        for window in (
            "factor_selection_2011_2015",
            "configuration_selection_2016_2020",
            "historical_oos_2021_2026_09",
        ):
            rates = [rate for signal_date, rate in values if _window_name(signal_date) == window]
            if not rates:
                continue
            window_rows.append(
                {
                    "evidence_stage": EVIDENCE_STAGE,
                    "window": window,
                    "economic_concept": concept,
                    "weekly_signal_count": len(rates),
                    "mean_weekly_coverage": f"{sum(rates) / len(rates):.9f}",
                    "median_weekly_coverage": f"{_percentile(rates, 0.50):.9f}",
                    "min_weekly_coverage": f"{min(rates):.9f}",
                    "p05_weekly_coverage": f"{_percentile(rates, 0.05):.9f}",
                    "first_week_coverage": f"{rates[0]:.9f}",
                    "last_week_coverage": f"{rates[-1]:.9f}",
                    "coverage_change_first_to_last": f"{rates[-1] - rates[0]:.9f}",
                    "interpretation": "Descriptive DEV availability only; not factor selection evidence.",
                }
            )
    _write_csv(paths["coverage_windows"], window_rows)

    staleness_rows: list[dict[str, Any]] = []
    for key in sorted(weekly_result["staleness_period"]):
        concept, period_class = key
        period_summary = _age_summary(weekly_result["staleness_period"][key])
        filed_summary = _age_summary(weekly_result["staleness_filing"][key])
        staleness_rows.append(
            {
                "evidence_stage": EVIDENCE_STAGE,
                "economic_concept": concept,
                "period_class": period_class,
                "available_snapshot_rows": period_summary["count"],
                "age_since_period_end_mean_days": period_summary["mean"],
                "age_since_period_end_p05_days": period_summary["p05"],
                "age_since_period_end_median_days": period_summary["median"],
                "age_since_period_end_p95_days": period_summary["p95"],
                "age_since_period_end_max_days": period_summary["max"],
                "age_since_filing_mean_days": filed_summary["mean"],
                "age_since_filing_p05_days": filed_summary["p05"],
                "age_since_filing_median_days": filed_summary["median"],
                "age_since_filing_p95_days": filed_summary["p95"],
                "age_since_filing_max_days": filed_summary["max"],
                "staleness_cutoff_applied": "false",
                "interpretation": "Reported descriptively; no maximum staleness cutoff is invented in DEV.",
            }
        )
    _write_csv(paths["staleness"], staleness_rows)

    facts_connection = sqlite3.connect(paths["facts_db"])
    facts_connection.row_factory = sqlite3.Row
    ambiguity_rows: list[dict[str, Any]] = []
    for row in facts_connection.execute(
        """
        SELECT economic_concept, substr(filed_date, 1, 4) AS filed_year,
               COUNT(DISTINCT cik) AS issuer_count,
               COUNT(DISTINCT cik || ':' || taxonomy || ':' || xbrl_tag || ':' || unit || ':' ||
                     COALESCE(start_date, '') || ':' || end_date || ':' || filed_date) AS source_group_count,
               COUNT(*) AS fact_row_count
        FROM canonical_fundamental_facts
        WHERE ambiguity_flag = 1
        GROUP BY economic_concept, filed_year
        ORDER BY economic_concept, filed_year
        """
    ):
        weekly_impact = sum(
            counts["ambiguous_count"]
            for (signal_date, concept), counts in weekly_result["coverage"].items()
            if concept == row["economic_concept"] and signal_date.year == int(row["filed_year"])
        )
        ambiguity_rows.append(
            {
                "evidence_stage": EVIDENCE_STAGE,
                "classification": "AMBIGUOUS_SAME_FILED_CONFLICT",
                "economic_concept": row["economic_concept"],
                "filed_year": row["filed_year"],
                "issuer_count": row["issuer_count"],
                "source_group_count": row["source_group_count"],
                "canonical_fact_row_count": row["fact_row_count"],
                "weekly_unavailable_security_count_same_year": weekly_impact,
                "handling": "Unavailable/missing; no tie-break by value, order, or accession is used.",
            }
        )
    facts_connection.close()
    _write_csv(paths["ambiguity"], ambiguity_rows)

    period_rows: list[dict[str, Any]] = []
    for (concept, period_class), counts in sorted(weekly_result["period_counts"].items()):
        period_rows.append(
            {
                "evidence_stage": EVIDENCE_STAGE,
                "economic_concept": concept,
                "period_class": period_class,
                "available_weekly_snapshot_rows": counts["available_snapshot_rows"],
                "period_hierarchy_applied": "false",
                "interpretation": "Class retained separately; annual, quarterly, YTD, irregular, and instant facts are not merged.",
            }
        )
    _write_csv(paths["period_coverage"], period_rows)
    _write_csv(paths["amendment_switches"], weekly_result["amendment_rows"])
    amendment_summary: list[dict[str, Any]] = []
    amendment_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in weekly_result["amendment_rows"]:
        amendment_groups[row["switch_type"]].append(row)
    for switch_type, rows in sorted(amendment_groups.items()):
        amendment_summary.append(
            {
                "evidence_stage": EVIDENCE_STAGE,
                "switch_type": switch_type,
                "weekly_switch_count": len(rows),
                "ticker_count": len({row["ticker"] for row in rows}),
                "concept_count": len({row["economic_concept"] for row in rows}),
                "first_signal_date": min(row["signal_date"] for row in rows),
                "last_signal_date": max(row["signal_date"] for row in rows),
                "interpretation": "A switch records a selected /A source on or after its own filing date; it does not backfill earlier signals.",
            }
        )
    _write_csv(paths["amendment_switch_summary"], amendment_summary)
    source_issue_rows = list(weekly_result["issue_rows"])
    for issue_type, count in sorted(facts_result["source_counts"].items()):
        if issue_type.startswith("OPEN_IMPLEMENTATION_ISSUE"):
            source_issue_rows.append(
                {
                    "evidence_stage": EVIDENCE_STAGE,
                    "issue_type": issue_type,
                    "ticker": "",
                    "cik": "",
                    "economic_concept": "",
                    "period_class": "",
                    "signal_date": "",
                    "disposition": f"{count} canonical-fact source rows were unavailable; no new period or fallback rule was introduced.",
                }
            )
    if source_issue_rows:
        issue_groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in source_issue_rows:
            issue_groups[
                (
                    row["issue_type"],
                    row["ticker"],
                    str(row["cik"]),
                    row["economic_concept"],
                    row["period_class"],
                )
            ].append(row)
        aggregated_issue_rows: list[dict[str, Any]] = []
        for _key, rows in sorted(issue_groups.items()):
            signal_dates = [row["signal_date"] for row in rows if row["signal_date"]]
            aggregated_issue_rows.append(
                {
                    "evidence_stage": EVIDENCE_STAGE,
                    "issue_type": rows[0]["issue_type"],
                    "ticker": rows[0]["ticker"],
                    "cik": rows[0]["cik"],
                    "economic_concept": rows[0]["economic_concept"],
                    "period_class": rows[0]["period_class"],
                    "first_signal_date": min(signal_dates) if signal_dates else "",
                    "last_signal_date": max(signal_dates) if signal_dates else "",
                    "affected_week_count": len(signal_dates),
                    "disposition": rows[0]["disposition"],
                }
            )
        _write_csv(paths["open_issues"], aggregated_issue_rows)
    else:
        _write_csv(
            paths["open_issues"],
            [
                {
                    "evidence_stage": EVIDENCE_STAGE,
                    "issue_type": "NONE_OBSERVED",
                    "ticker": "",
                    "cik": "",
                    "economic_concept": "",
                    "period_class": "",
                    "signal_date": "",
                    "disposition": "No unresolved implementation issue was observed under the signed selector contract.",
                }
            ],
        )
    return {
        "coverage_rows": coverage_rows,
        "coverage_windows": window_rows,
        "staleness_rows": staleness_rows,
        "ambiguity_rows": ambiguity_rows,
        "period_rows": period_rows,
        "amendment_summary": amendment_summary,
    }


def _write_acceptance(root: Path, summary: dict[str, Any]) -> None:
    paths = output_paths(root)
    manifest = summary["raw_manifest"]
    weekly = summary["weekly_counts"]
    status = "PASS WITH WARNINGS"
    if not manifest["passed"] or weekly["pit_violations"] or weekly["provenance_nonunique"]:
        status = "FAIL"
    lines = [
        "# Canonical PIT Fundamental Construction — DEV Acceptance Report",
        "",
        f"- **Evidence stage:** `{EVIDENCE_STAGE}`",
        f"- **Decision:** {status}",
        "- **Scope:** canonical SEC source inputs and weekly PIT availability/provenance only; no ratio, factor, score, IC, portfolio, strategy, or historical market-cap output is created.",
        "",
        "| Check | Status | Evidence / disposition |",
        "| --- | --- | --- |",
        f"| Raw package integrity | {'PASS' if manifest['passed'] else 'FAIL'} | {manifest['verified_payload_files']}/{manifest['declared_payload_files']} payload hashes verified. |",
        f"| SEC archive/mapping coverage | {'PASS' if summary['archive_cik_count'] == 497 and summary['mapped_cik_count'] == 497 else 'FAIL'} | {summary['archive_cik_count']} raw issuer archives; {summary['mapped_cik_count']} mapped CIKs; {summary['unmapped_master_security_count']} current-master securities without SEC mapping are explicitly outside the SEC coverage denominator. |",
        f"| PIT no-look-ahead | {'PASS' if weekly['pit_violations'] == 0 else 'FAIL'} | Snapshot rows with filed_date > signal_date: {weekly['pit_violations']}. |",
        f"| Provenance uniqueness | {'PASS' if weekly['provenance_nonunique'] == 0 else 'FAIL'} | Non-unique snapshot keys: {weekly['provenance_nonunique']}; each available row carries one source observation ID and full source provenance. |",
        "| Unit/taxonomy/form controls | PASS | Only USD `us-gaap` signed tags in 10-K/10-Q/US-GAAP-20-F families are available; no unit conversion or IFRS mapping occurs. |",
        "| Amendment timing | PASS | Amendments can enter only on or after their own filed date; amendment switches are reported separately. |",
        "| Conflict handling | PASS WITH WARNINGS | Same-latest-filed-value conflicts remain `AMBIGUOUS`/missing; no value/order/accession tie-break occurs. |",
        f"| Open implementation issues | PASS WITH WARNINGS | {weekly['open_implementation_issue_unavailability_rows']} slot/date instances lack a signed deterministic provenance rule and remain missing; the issue audit is aggregated by ticker/concept/class. |",
        "| Negative CapEx | PASS WITH WARNINGS | Negative primary CapEx remains `SIGN_EXCEPTION`/unavailable; no sign correction occurs. |",
        "| Period separation | PASS | Instant, annual, quarterly, YTD, and irregular duration facts remain independently stored; no final hierarchy is imposed. |",
        "| Prohibited downstream results | PASS | No ROE, ROA, GP/A, Leverage, Asset Growth, FCF, valuation, factor, IC, portfolio, or strategy output is written. |",
        "",
        "## DEV blockers",
        "",
        "No selector-integrity DEV blocker is present when the PASS checks above hold. A future factor-engineering step must still specify how the separately stored period classes are used; this construction deliberately does not decide that hierarchy.",
        "",
        "## DEV warnings",
        "",
        "Current-S&P-500 survivorship bias, non-PIT Current GICS, missing RDQ/timestamps, direct-Gross-Profit coverage limits, sign/ambiguity/open-provenance missingness, and current-master securities without an SEC mapping remain disclosed DEV warnings.",
        "",
        "## Formal PIT limitations",
        "",
        "Historical S&P 500 membership, historical security/identifier history, historical GICS, CRSP delistings and returns, Compustat/GVKEY/PERMNO links, and historical market-cap basis are absent. They require a later `FORMAL_PIT_UNIVERSE` rerun and do not by themselves cause this DEV construction to fail.",
    ]
    paths["acceptance"].write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_pipeline(root: Path | None = None) -> dict[str, Any]:
    selected_root = (root or project_root()).resolve()
    facts_result = _build_facts_database(selected_root)
    weekly_result = _build_weekly_database(selected_root, facts_result)
    audit_result = _write_audits(selected_root, facts_result, weekly_result)
    manifest = source_audit.audit_manifest(source_audit.package_root(selected_root))
    summary = {
        "schema": CANONICAL_VERSION,
        "evidence_stage": EVIDENCE_STAGE,
        "fact_counts": facts_result["counts"],
        "fact_source_status_counts": facts_result["source_counts"],
        "weekly_counts": weekly_result["weekly_counts"],
        "archive_cik_count": facts_result["archive_cik_count"],
        "mapped_cik_count": facts_result["mapped_cik_count"],
        "unmapped_master_security_count": len(facts_result["unmapped_master"]),
        "signal_date_count": len(weekly_result["signal_dates"]),
        "raw_manifest": manifest,
        "audit_row_counts": {key: len(value) for key, value in audit_result.items()},
        "prohibited_outputs_not_created": [
            "ROE, ROA, GP/A, Leverage, Asset Growth, or FCF",
            "valuation yields or historical market capitalization",
            "fundamental factor scores, preprocessing, IC, portfolios, FMOM, backtests, or strategy outputs",
        ],
    }
    paths = output_paths(selected_root)
    paths["summary"].write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_acceptance(selected_root, summary)
    return {"summary": summary, "paths": {name: str(path) for name, path in paths.items()}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=project_root())
    args = parser.parse_args()
    result = run_pipeline(args.root)
    print(json.dumps(result["paths"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
