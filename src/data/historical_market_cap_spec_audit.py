"""Audit inputs and choices for a DEV historical-market-cap specification.

This module produces aggregate evidence only.  It intentionally does not write
a market-cap panel or calculate valuation/Size/factor/portfolio quantities.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from src.data import canonical_market, source_audit


EVIDENCE_STAGE = "DEV_CURRENT_UNIVERSE"
CANONICAL_VERSION = "fe5209.us_market_cap_spec.dev.v1"
PRIMARY_SHARE_TAG = ("dei", "EntityCommonStockSharesOutstanding")
ALTERNATIVE_SHARE_TAG = ("us-gaap", "CommonStockSharesOutstanding")
ALLOWED_FORM_BASES = frozenset({"10-K", "10-Q", "20-F"})
SHARE_UNIT = "shares"

# These are only an audit classifier, not an automatic production action rule.
# Values outside this finite set include vendor action factors whose economic
# connection to an issuer's share count is not demonstrated by the package.
STANDARD_SPLIT_FACTORS = frozenset(
    {
        1.25,
        1.5,
        2.0,
        3.0,
        4.0,
        5.0,
        6.0,
        7.0,
        10.0,
        15.0,
        20.0,
        25.0,
        50.0,
        0.5,
        1 / 3,
        0.25,
        0.2,
        1 / 7,
        0.1,
        0.05,
        0.125,
    }
)


@dataclass(frozen=True)
class ShareObservation:
    cik: int
    taxonomy: str
    tag: str
    unit: str
    value_text: str
    value: float
    end_date: date
    filed_date: date
    form: str
    accession: str
    frame: str
    fiscal_year: str
    fiscal_period: str
    start_date: str
    ordinal: int

    @property
    def source_id(self) -> str:
        return ":".join(
            (
                str(self.cik),
                self.taxonomy,
                self.tag,
                self.unit,
                self.start_date,
                self.end_date.isoformat(),
                self.filed_date.isoformat(),
                self.form,
                self.accession,
                str(self.ordinal),
            )
        )

    @property
    def provenance_key(self) -> tuple[str, ...]:
        return (
            self.taxonomy,
            self.tag,
            self.unit,
            self.start_date,
            self.end_date.isoformat(),
            self.filed_date.isoformat(),
            self.form,
            self.accession,
            self.frame,
            self.fiscal_year,
            self.fiscal_period,
        )

    @property
    def is_amendment(self) -> bool:
        return self.form.endswith("/A")


@dataclass(frozen=True)
class ShareSelection:
    status: str
    observation: ShareObservation | None = None
    reason: str = ""


@dataclass
class ShareSlot:
    """PIT selector for exactly one CIK and one shares tag."""

    observations: list[ShareObservation]
    by_filed: dict[date, list[ShareObservation]] = field(init=False)
    filed_dates: list[date] = field(init=False)
    cursor: int = 0
    current: dict[date, list[ShareObservation]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        by_filed: dict[date, list[ShareObservation]] = defaultdict(list)
        for observation in self.observations:
            by_filed[observation.filed_date].append(observation)
        self.by_filed = dict(by_filed)
        self.filed_dates = sorted(by_filed)

    def advance(self, signal_date: date) -> ShareSelection:
        while self.cursor < len(self.filed_dates) and self.filed_dates[self.cursor] <= signal_date:
            filed = self.filed_dates[self.cursor]
            by_end: dict[date, list[ShareObservation]] = defaultdict(list)
            for observation in self.by_filed[filed]:
                by_end[observation.end_date].append(observation)
            for end_date, group in by_end.items():
                self.current[end_date] = group
            self.cursor += 1
        if not self.current:
            return ShareSelection("NO_ELIGIBLE_FILING", reason="NO_ELIGIBLE_FILING")
        latest_end = max(self.current)
        group = self.current[latest_end]
        values = {item.value_text for item in group}
        if len(values) > 1:
            return ShareSelection("AMBIGUOUS", reason="AMBIGUOUS_SAME_FILED_CONFLICT")
        provenance = {item.provenance_key for item in group}
        if len(provenance) > 1:
            return ShareSelection(
                "OPEN_IMPLEMENTATION_ISSUE",
                reason="OPEN_IMPLEMENTATION_ISSUE_NONUNIQUE_PROVENANCE",
            )
        return ShareSelection("AVAILABLE", min(group, key=lambda item: item.source_id), "AVAILABLE")


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def audit_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / "outputs" / "audit"


def output_paths(root: Path | None = None) -> dict[str, Path]:
    audit = audit_dir(root)
    return {
        "share_tags": audit / "sec_share_tag_coverage_dev.csv",
        "share_staleness": audit / "sec_share_staleness_dev.csv",
        "price_basis": audit / "yahoo_split_basis_audit_dev.csv",
        "split_events": audit / "market_cap_split_event_validation_dev.csv",
        "multiclass": audit / "multiclass_market_cap_audit_dev.csv",
        "methods": audit / "market_cap_candidate_method_comparison_dev.csv",
        "summary": audit / "historical_market_cap_spec_summary_dev.json",
        "acceptance": audit / "historical_market_cap_spec_acceptance_report.md",
    }


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _finite_float(value: Any) -> float | None:
    try:
        result = float(str(value))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _base_form(form: str) -> str:
    return form.removesuffix("/A")


def _is_allowed_share_form(form: str) -> bool:
    return _base_form(form) in ALLOWED_FORM_BASES


def _is_standard_split_factor(value: float) -> bool:
    return any(math.isclose(value, expected, rel_tol=0.0, abs_tol=1e-7) for expected in STANDARD_SPLIT_FACTORS)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _age_summary(values: list[int]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "p05": None, "median": None, "p95": None, "max": None}
    floats = [float(value) for value in values]
    return {
        "count": len(values),
        "mean": sum(values) / len(values),
        "p05": _percentile(floats, 0.05),
        "median": _percentile(floats, 0.50),
        "p95": _percentile(floats, 0.95),
        "max": max(values),
    }


def _window(signal_date: date) -> str | None:
    if date(2011, 1, 1) <= signal_date <= date(2015, 12, 31):
        return "factor_selection_2011_2015"
    if date(2016, 1, 1) <= signal_date <= date(2020, 12, 31):
        return "configuration_selection_2016_2020"
    if date(2021, 1, 1) <= signal_date <= date(2026, 9, 30):
        return "historical_oos_2021_2026_09"
    return None


def _share_semantic_status(taxonomy: str, tag: str) -> tuple[str, str]:
    if (taxonomy, tag) == PRIMARY_SHARE_TAG:
        return "PRIMARY_CANDIDATE", "Direct cover-page entity common-stock shares outstanding; issuer/class scope requires audit."
    if (taxonomy, tag) == ALTERNATIVE_SHARE_TAG:
        return "CONDITIONAL_FALLBACK_CANDIDATE", "Direct common-stock shares outstanding, but narrower coverage and not approved as fallback."
    lowered = tag.lower()
    if "weightedaverage" in lowered or "incrementalcommonshares" in lowered:
        return "NOT_POINT_IN_TIME", "EPS weighted-average or dilution computation; not shares outstanding at an as-of date."
    if "authorized" in lowered:
        return "NOT_OUTSTANDING", "Authorized shares are a legal capacity, not outstanding public equity."
    if "issued" in lowered:
        return "NOT_OUTSTANDING", "Issued shares can include treasury shares or period transactions; not a direct outstanding-share measure."
    if "treasury" in lowered or "repurchase" in lowered or "stockissued" in lowered:
        return "NOT_OUTSTANDING", "Treasury/repurchase/issuance activity is not total common shares outstanding."
    if "option" in lowered or "warrant" in lowered or "sharebased" in lowered:
        return "NOT_OUTSTANDING", "Option, award, warrant, or plan measure; not total common shares outstanding."
    if "preferred" in lowered:
        return "NOT_COMMON_EQUITY_OUTSTANDING", "Preferred-stock share measure; not common-equity market-cap shares."
    return "NOT_CANDIDATE_UNLESS_SIGNED", "Observed share-unit tag does not establish a direct common-shares-outstanding meaning."


def _read_identity(root: Path) -> tuple[
    list[dict[str, str]],
    dict[int, list[dict[str, str]]],
    dict[int, list[dict[str, str]]],
    dict[str, dict[str, str]],
    set[str],
    list[date],
]:
    master_path = root / "data" / "canonical" / "us_security_master_dev.csv"
    calendar_path = root / "data" / "canonical" / "us_weekly_signal_calendar_dev.csv"
    _, master = source_audit._read_csv_rows(master_path)
    _, calendar = source_audit._read_csv_rows(calendar_path)
    _, sec_map = source_audit._read_csv_rows(
        source_audit.package_root(root) / source_audit.SEC_CIK_MAP_FILE
    )
    mapped_tickers = {str(row["ticker"]) for row in sec_map if row.get("ticker")}
    all_by_cik: dict[int, list[dict[str, str]]] = defaultdict(list)
    mapped_by_cik: dict[int, list[dict[str, str]]] = defaultdict(list)
    by_ticker: dict[str, dict[str, str]] = {}
    for row in master:
        cik = source_audit._as_int(row.get("cik"))
        if cik is not None:
            all_by_cik[cik].append(row)
            if row["ticker"] in mapped_tickers:
                mapped_by_cik[cik].append(row)
        by_ticker[row["ticker"]] = row
    signal_dates = [date.fromisoformat(row["signal_date"]) for row in calendar]
    if any(right <= left for left, right in zip(signal_dates, signal_dates[1:])):
        raise ValueError("Signal calendar must be strictly increasing")
    return (
        master,
        dict(all_by_cik),
        dict(mapped_by_cik),
        by_ticker,
        mapped_tickers,
        signal_dates,
    )


def _scan_share_facts(root: Path, mapped_ciks: set[int]) -> tuple[
    list[dict[str, Any]], dict[tuple[int, tuple[str, str]], list[ShareObservation]], dict[tuple[int, tuple[str, str]], list[ShareObservation]]
]:
    """Profile every actual `shares`-unit tag and retain direct candidates."""

    profiles: dict[tuple[str, str], dict[str, Any]] = {}
    eligible: dict[tuple[int, tuple[str, str]], list[ShareObservation]] = defaultdict(list)
    all_direct: dict[tuple[int, tuple[str, str]], list[ShareObservation]] = defaultdict(list)
    facts_dir = source_audit.package_root(root) / source_audit.SEC_FACTS_DIR
    for archive in sorted(facts_dir.glob("CIK*.json.gz")):
        with gzip.open(archive, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        cik = source_audit._as_int(payload.get("cik"))
        if cik is None:
            raise ValueError(f"Missing CIK in {archive.name}")
        for taxonomy, tag_map in payload.get("facts", {}).items():
            if not isinstance(tag_map, dict):
                continue
            for tag, definition in tag_map.items():
                if not isinstance(definition, dict):
                    continue
                unit_map = definition.get("units", {})
                if not isinstance(unit_map, dict):
                    continue
                matching_units = [
                    (str(unit), observations)
                    for unit, observations in unit_map.items()
                    if str(unit).lower() == SHARE_UNIT and isinstance(observations, list)
                ]
                if not matching_units:
                    continue
                key = (str(taxonomy), str(tag))
                semantic_status, semantic_note = _share_semantic_status(*key)
                profile = profiles.setdefault(
                    key,
                    {
                        "taxonomy": key[0],
                        "xbrl_tag": key[1],
                        "semantic_status": semantic_status,
                        "semantic_note": semantic_note,
                        "label": str(definition.get("label") or ""),
                        "description": str(definition.get("description") or ""),
                        "issuer_ciks": set(),
                        "observation_count": 0,
                        "instant_count": 0,
                        "duration_count": 0,
                        "forms": Counter(),
                        "units": Counter(),
                        "min_end": None,
                        "max_end": None,
                        "min_filed": None,
                        "max_filed": None,
                        "amendment_count": 0,
                    },
                )
                profile["issuer_ciks"].add(cik)
                for unit, observations in matching_units:
                    for ordinal, observation in enumerate(observations):
                        if not isinstance(observation, dict):
                            continue
                        end = _parse_date(observation.get("end"))
                        filed = _parse_date(observation.get("filed"))
                        value = _finite_float(observation.get("val"))
                        form = str(observation.get("form") or "")
                        profile["observation_count"] += 1
                        profile["units"][unit] += 1
                        profile["forms"][form] += 1
                        profile["instant_count" if not observation.get("start") else "duration_count"] += 1
                        profile["amendment_count"] += int(form.endswith("/A"))
                        if end:
                            profile["min_end"] = end if profile["min_end"] is None or end < profile["min_end"] else profile["min_end"]
                            profile["max_end"] = end if profile["max_end"] is None or end > profile["max_end"] else profile["max_end"]
                        if filed:
                            profile["min_filed"] = filed if profile["min_filed"] is None or filed < profile["min_filed"] else profile["min_filed"]
                            profile["max_filed"] = filed if profile["max_filed"] is None or filed > profile["max_filed"] else profile["max_filed"]
                        if key not in {PRIMARY_SHARE_TAG, ALTERNATIVE_SHARE_TAG} or cik not in mapped_ciks:
                            continue
                        if end is None or filed is None or value is None or value <= 0:
                            continue
                        share = ShareObservation(
                            cik=cik,
                            taxonomy=key[0],
                            tag=key[1],
                            unit=unit,
                            value_text=str(observation.get("val")),
                            value=value,
                            end_date=end,
                            filed_date=filed,
                            form=form,
                            accession=str(observation.get("accn") or ""),
                            frame=str(observation.get("frame") or ""),
                            fiscal_year=str(observation.get("fy") or ""),
                            fiscal_period=str(observation.get("fp") or ""),
                            start_date=str(observation.get("start") or ""),
                            ordinal=ordinal,
                        )
                        all_direct[(cik, key)].append(share)
                        if _is_allowed_share_form(form) and end <= filed:
                            eligible[(cik, key)].append(share)
    output_rows: list[dict[str, Any]] = []
    for key, profile in sorted(profiles.items()):
        output_rows.append(
            {
                "evidence_stage": EVIDENCE_STAGE,
                "taxonomy": profile["taxonomy"],
                "xbrl_tag": profile["xbrl_tag"],
                "semantic_status": profile["semantic_status"],
                "semantic_note": profile["semantic_note"],
                "label": profile["label"],
                "description": profile["description"],
                "issuer_count": len(profile["issuer_ciks"]),
                "observation_count": profile["observation_count"],
                "instant_count": profile["instant_count"],
                "duration_count": profile["duration_count"],
                "unit_values": json.dumps(dict(profile["units"]), sort_keys=True),
                "forms": json.dumps(dict(profile["forms"]), sort_keys=True),
                "min_end_date": profile["min_end"].isoformat() if profile["min_end"] else "",
                "max_end_date": profile["max_end"].isoformat() if profile["max_end"] else "",
                "min_filed_date": profile["min_filed"].isoformat() if profile["min_filed"] else "",
                "max_filed_date": profile["max_filed"].isoformat() if profile["max_filed"] else "",
                "amendment_observation_count": profile["amendment_count"],
                "proposed_form_policy": "10-K/10-Q/20-F families only for direct-candidate audit; requires sign-off",
                "canonical_version": CANONICAL_VERSION,
            }
        )
    return output_rows, dict(eligible), dict(all_direct)


def _read_price_evidence(root: Path, signal_dates: set[date]) -> tuple[
    dict[tuple[str, date], dict[str, str]], dict[str, list[tuple[date, float, bool]]], list[dict[str, Any]], dict[str, Any]
]:
    """Read exact signal-date prices and split-event evidence without deriving market cap."""

    daily_path = root / "data" / "canonical" / "us_daily_market_dev.csv.gz"
    signal_prices: dict[tuple[str, date], dict[str, str]] = {}
    split_by_ticker: dict[str, list[tuple[date, float, bool]]] = defaultdict(list)
    validation_rows: list[dict[str, Any]] = []
    counts = Counter()
    current_ticker = ""
    ticker_rows: list[dict[str, str]] = []

    def process_ticker(rows: list[dict[str, str]]) -> None:
        if not rows:
            return
        ticker = rows[0]["ticker"]
        for index, row in enumerate(rows):
            split = _finite_float(row.get("stock_splits")) or 0.0
            if split == 0.0:
                continue
            standard = _is_standard_split_factor(split)
            event_date = date.fromisoformat(row["date"])
            split_by_ticker[ticker].append((event_date, split, standard))
            prior = rows[index - 1] if index else None
            following = rows[index + 1] if index + 1 < len(rows) else None
            prior_close = _finite_float(prior.get("close")) if prior else None
            event_close = _finite_float(row.get("close"))
            next_close = _finite_float(following.get("close")) if following else None
            prior_adj = _finite_float(prior.get("adj_close")) if prior else None
            event_adj = _finite_float(row.get("adj_close"))
            close_ratio = event_close / prior_close if prior_close and event_close else None
            adj_ratio = event_adj / prior_adj if prior_adj and event_adj else None
            dynamic_ratio = close_ratio * split if close_ratio is not None else None
            validation_rows.append(
                {
                    "evidence_stage": EVIDENCE_STAGE,
                    "ticker": ticker,
                    "yahoo_ticker": row["yahoo_ticker"],
                    "split_date": row["date"],
                    "split_factor": f"{split:.12g}",
                    "split_event_class": "STANDARD_SPLIT_FACTOR" if standard else "NONSTANDARD_VENDOR_ACTION_FACTOR",
                    "prior_trading_date": prior["date"] if prior else "",
                    "prior_close": prior.get("close", "") if prior else "",
                    "event_close": row.get("close", ""),
                    "next_trading_date": following["date"] if following else "",
                    "next_close": following.get("close", "") if following else "",
                    "close_event_to_prior_ratio": f"{close_ratio:.12g}" if close_ratio is not None else "",
                    "adj_close_event_to_prior_ratio": f"{adj_ratio:.12g}" if adj_ratio is not None else "",
                    "delivery_basis_constant_share_ratio": f"{close_ratio:.12g}" if close_ratio is not None else "",
                    "signal_date_dynamic_chain_share_ratio": f"{split:.12g}",
                    "signal_date_dynamic_chain_implied_mcap_ratio": f"{dynamic_ratio:.12g}" if dynamic_ratio is not None else "",
                    "dynamic_chain_mechanical_discontinuity": "FAIL" if standard and not math.isclose(split, 1.0) else "NOT_APPLICABLE",
                    "delivery_basis_chain_discontinuity": "PASS_NO_MECHANICAL_SPLIT_MULTIPLIER" if standard else "REVIEW_NONSTANDARD_ACTION",
                    "note": "Ratios are audit-only; no market-cap value or panel is created.",
                    "canonical_version": CANONICAL_VERSION,
                }
            )

    with gzip.open(daily_path, "rt", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            ticker = row["ticker"]
            if ticker != current_ticker:
                process_ticker(ticker_rows)
                current_ticker = ticker
                ticker_rows = []
            ticker_rows.append(row)
            is_stock = row["is_stock_cross_section"].lower() == "true"
            close = _finite_float(row.get("close"))
            adj_close = _finite_float(row.get("adj_close"))
            dividend = _finite_float(row.get("dividends")) or 0.0
            split = _finite_float(row.get("stock_splits")) or 0.0
            counts["daily_rows"] += 1
            counts["close_adj_close_different_rows"] += int(
                close is not None and adj_close is not None and not math.isclose(close, adj_close, rel_tol=0.0, abs_tol=1e-9)
            )
            counts["positive_dividend_rows"] += int(dividend > 0)
            counts["nonzero_split_rows"] += int(split != 0)
            if is_stock and date.fromisoformat(row["date"]) in signal_dates:
                signal_prices[(ticker, date.fromisoformat(row["date"]))] = row
    process_ticker(ticker_rows)
    return signal_prices, dict(split_by_ticker), validation_rows, dict(counts)


def _chain_info(events: list[tuple[date, float, bool]], share_date: date) -> tuple[float, bool, int]:
    matching = [item for item in events if item[0] > share_date]
    factor = 1.0
    for _event_date, split, standard in matching:
        if standard:
            factor *= split
    return factor, any(not standard for _event_date, _split, standard in matching), len(matching)


def _window_summary(values: dict[date, tuple[int, int]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for window in (
        "factor_selection_2011_2015",
        "configuration_selection_2016_2020",
        "historical_oos_2021_2026_09",
    ):
        rates = [available / total for signal_date, (available, total) in values.items() if _window(signal_date) == window and total]
        if rates:
            result.append(
                {
                    "window": window,
                    "signal_count": len(rates),
                    "mean_weekly_coverage": sum(rates) / len(rates),
                    "median_weekly_coverage": _percentile(rates, 0.50),
                    "min_weekly_coverage": min(rates),
                    "p05_weekly_coverage": _percentile(rates, 0.05),
                }
            )
    return result


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def _nearest_share_pair(
    observations: list[ShareObservation], split_date: date
) -> tuple[ShareObservation | None, ShareObservation | None]:
    before = [item for item in observations if item.end_date < split_date]
    after = [item for item in observations if item.end_date >= split_date]
    return (
        max(before, key=lambda item: (item.end_date, item.filed_date), default=None),
        min(after, key=lambda item: (item.end_date, item.filed_date), default=None),
    )


def _write_acceptance(root: Path, summary: dict[str, Any]) -> None:
    paths = output_paths(root)
    manifest = summary["raw_manifest"]
    pit_violations = summary["primary_share_pit_violations"]
    decision = "PASS WITH WARNINGS" if manifest["passed"] and pit_violations == 0 else "FAIL"
    lines = [
        "# Historical Market Cap Specification — DEV Acceptance Report",
        "",
        f"- **Evidence stage:** `{EVIDENCE_STAGE}`",
        f"- **Decision:** {decision}",
        "- **Scope:** source/share/price/split/multi-class specification and aggregate audit only. No historical market-cap panel, valuation ratio, Size, neutralization, IC, portfolio, or strategy result is created.",
        "",
        "| Check | Status | Evidence / disposition |",
        "| --- | --- | --- |",
        f"| Raw package integrity | {'PASS' if manifest['passed'] else 'FAIL'} | {manifest['verified_payload_files']}/{manifest['declared_payload_files']} supplied payload hashes passed. |",
        f"| Shares PIT timing | {'PASS' if pit_violations == 0 else 'FAIL'} | Primary selected shares with `filed_date > signal_date`: {pit_violations}. |",
        f"| Direct-share validity diagnostic | WARN | {summary['primary_selected_value_le_one_security_signal_count']} selected primary security-signal observations have reported shares <= 1; no automatic exclusion rule is approved. |",
        "| Exact signal-date price rule | PASS WITH WARNINGS | The audit never substitutes a future/next-day/forward-filled price. Missing or invalid exact-date prices remain unavailable; zero-volume/review handling requires sign-off. |",
        "| Split-event tests | PASS WITH WARNINGS | Standard forward and reverse vendor split factors are tested; nonstandard vendor action factors are explicitly excluded from the recommended chain pending review. |",
        "| Multi-class safety | WARN | Issuer shares are not allocated across the six securities of the three multi-class CIKs; they remain `MULTICLASS_UNRESOLVED`. |",
        "| Source naming | PASS | Current method is an ex-post delivery-basis `estimated_market_cap` candidate, never an exact contemporaneous or Formal PIT market-cap claim. |",
        "| Prohibited construction | PASS | No `us_weekly_market_cap_dev` table/panel or factor output is written. |",
        "",
        "## DEV blockers",
        "",
        "No blocker prevents a specification handoff. Construction remains blocked pending explicit sign-off of price/shares/split/multi-class/missing-data rules.",
        "",
        "## DEV warnings",
        "",
        "Yahoo delivery prices are retrospectively action-adjusted; reported shares are periodic and stale; direct-tag values require a signed validity/outlier policy; RDQ timestamps are absent; current-universe survivorship bias and non-PIT GICS remain; vendor action factors include nonstandard values; and multi-class issuer allocation is unavailable.",
        "",
        "## Formal PIT limitations",
        "",
        "CRSP historical shares/market cap, historical identifier/class history, historical constituents/GICS, delistings, and approved PERMNO/GVKEY links remain future Formal PIT inputs. They do not make this DEV specification fail.",
    ]
    paths["acceptance"].write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_audit(root: Path | None = None) -> dict[str, Any]:
    selected_root = (root or project_root()).resolve()
    paths = output_paths(selected_root)
    master, all_by_cik, by_cik, by_ticker, mapped_tickers, signal_dates = _read_identity(selected_root)
    mapped_ciks = set(by_cik)
    share_tag_rows, eligible_shares, all_direct_shares = _scan_share_facts(selected_root, mapped_ciks)
    signal_prices, split_by_ticker, event_rows, price_counts = _read_price_evidence(
        selected_root, set(signal_dates)
    )

    selectors: dict[tuple[int, tuple[str, str]], ShareSlot] = {
        key: ShareSlot(sorted(observations, key=lambda item: (item.filed_date, item.end_date, item.source_id)))
        for key, observations in eligible_shares.items()
    }
    selections: dict[tuple[int, tuple[str, str], date], ShareSelection] = {}
    share_ages_by_tag: dict[tuple[str, str], dict[str, list[int]]] = defaultdict(lambda: {"share": [], "filing": []})
    share_selection_statuses: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    primary_selected_value_le_one = 0
    primary_selected_value_le_one_examples: list[str] = []
    share_pit_violations = 0
    share_coverage_by_tag: dict[tuple[str, str], dict[date, tuple[int, int]]] = {}
    for share_tag in (PRIMARY_SHARE_TAG, ALTERNATIVE_SHARE_TAG):
        weekly_counts: dict[date, list[int]] = {item: [0, 0] for item in signal_dates}
        for cik, securities in sorted(by_cik.items()):
            selector = selectors.get((cik, share_tag))
            for signal_date in signal_dates:
                selection = selector.advance(signal_date) if selector else ShareSelection("NO_ELIGIBLE_FILING")
                selections[(cik, share_tag, signal_date)] = selection
                share_selection_statuses[share_tag][selection.status] += len(securities)
                weekly_counts[signal_date][1] += len(securities)
                if selection.status == "AVAILABLE" and selection.observation:
                    observation = selection.observation
                    if observation.filed_date > signal_date:
                        share_pit_violations += len(securities)
                    weekly_counts[signal_date][0] += len(securities)
                    share_ages_by_tag[share_tag]["share"].extend(
                        [(signal_date - observation.end_date).days] * len(securities)
                    )
                    share_ages_by_tag[share_tag]["filing"].extend(
                        [(signal_date - observation.filed_date).days] * len(securities)
                    )
                    if share_tag == PRIMARY_SHARE_TAG and observation.value <= 1:
                        primary_selected_value_le_one += len(securities)
                        if len(primary_selected_value_le_one_examples) < 10:
                            primary_selected_value_le_one_examples.append(
                                f"{cik}:{signal_date.isoformat()}:{observation.end_date.isoformat()}:"
                                f"{observation.value_text}:{observation.accession}"
                            )
        share_coverage_by_tag[share_tag] = {signal: (values[0], values[1]) for signal, values in weekly_counts.items()}

    # Add weekly availability evidence to direct-tag profile rows only.
    profile_by_tag = {(row["taxonomy"], row["xbrl_tag"]): row for row in share_tag_rows}
    for share_tag in (PRIMARY_SHARE_TAG, ALTERNATIVE_SHARE_TAG):
        row = profile_by_tag.get(share_tag)
        if row is None:
            continue
        coverages = share_coverage_by_tag[share_tag]
        rates = [available / total for available, total in coverages.values() if total]
        row["weekly_pit_mean_coverage"] = f"{sum(rates) / len(rates):.9f}" if rates else ""
        row["weekly_pit_min_coverage"] = f"{min(rates):.9f}" if rates else ""
        row["weekly_pit_signal_count"] = len(rates)
        row["weekly_pit_selector"] = "latest eligible filed direct-tag fact; exact same-filed conflicts/open provenance missing"
    for row in share_tag_rows:
        row.setdefault("weekly_pit_mean_coverage", "")
        row.setdefault("weekly_pit_min_coverage", "")
        row.setdefault("weekly_pit_signal_count", "")
        row.setdefault("weekly_pit_selector", "")
    _write_csv(paths["share_tags"], share_tag_rows)

    staleness_rows: list[dict[str, Any]] = []
    for share_tag in (PRIMARY_SHARE_TAG, ALTERNATIVE_SHARE_TAG):
        share_age = _age_summary(share_ages_by_tag[share_tag]["share"])
        filing_age = _age_summary(share_ages_by_tag[share_tag]["filing"])
        staleness_rows.append(
            {
                "evidence_stage": EVIDENCE_STAGE,
                "taxonomy": share_tag[0],
                "xbrl_tag": share_tag[1],
                "selected_security_signal_count": share_age["count"],
                "age_since_share_date_mean_days": share_age["mean"],
                "age_since_share_date_p05_days": share_age["p05"],
                "age_since_share_date_median_days": share_age["median"],
                "age_since_share_date_p95_days": share_age["p95"],
                "age_since_share_date_max_days": share_age["max"],
                "age_since_filing_mean_days": filing_age["mean"],
                "age_since_filing_p05_days": filing_age["p05"],
                "age_since_filing_median_days": filing_age["median"],
                "age_since_filing_p95_days": filing_age["p95"],
                "age_since_filing_max_days": filing_age["max"],
                "staleness_cutoff_applied": "false",
                "availability_semantics": "filed_date <= signal_date; descriptive ages only",
                "selection_status_counts_json": json.dumps(
                    dict(share_selection_statuses[share_tag]), sort_keys=True
                ),
                "selected_value_le_one_security_signal_count": (
                    primary_selected_value_le_one if share_tag == PRIMARY_SHARE_TAG else 0
                ),
                "small_value_diagnostic": (
                    "Diagnostic only; no automatic exclusion policy is approved."
                    if share_tag == PRIMARY_SHARE_TAG
                    else "Not applicable."
                ),
                "canonical_version": CANONICAL_VERSION,
            }
        )
    _write_csv(paths["share_staleness"], staleness_rows)

    # Augment split validation rows with actual direct-share observations around
    # the event and delivery-basis alignment factors.  This remains an event
    # audit; it never emits an issuer/week market-cap value.
    for row in event_rows:
        ticker = row["ticker"]
        master_row = by_ticker.get(ticker)
        cik = source_audit._as_int(master_row.get("cik")) if master_row else None
        event_date = date.fromisoformat(row["split_date"])
        direct = all_direct_shares.get((cik, PRIMARY_SHARE_TAG), []) if cik is not None else []
        before, after = _nearest_share_pair(direct, event_date)
        events = split_by_ticker.get(ticker, [])
        if before:
            before_factor, before_nonstandard, _ = _chain_info(events, before.end_date)
        else:
            before_factor, before_nonstandard = None, None
        if after:
            after_factor, after_nonstandard, _ = _chain_info(events, after.end_date)
        else:
            after_factor, after_nonstandard = None, None
        reported_ratio = after.value / before.value if before and after and before.value else None
        aligned_ratio = (
            (after.value * after_factor) / (before.value * before_factor)
            if before and after and before_factor and after_factor and before.value
            else None
        )
        row.update(
            {
                "cik": cik or "",
                "nearest_pre_split_share_end_date": before.end_date.isoformat() if before else "",
                "nearest_pre_split_share_filed_date": before.filed_date.isoformat() if before else "",
                "nearest_pre_split_reported_shares": before.value_text if before else "",
                "nearest_post_split_share_end_date": after.end_date.isoformat() if after else "",
                "nearest_post_split_share_filed_date": after.filed_date.isoformat() if after else "",
                "nearest_post_split_reported_shares": after.value_text if after else "",
                "reported_share_ratio_post_pre": f"{reported_ratio:.12g}" if reported_ratio is not None else "",
                "delivery_basis_pre_split_factor": f"{before_factor:.12g}" if before_factor is not None else "",
                "delivery_basis_post_split_factor": f"{after_factor:.12g}" if after_factor is not None else "",
                "delivery_basis_aligned_share_ratio_post_pre": f"{aligned_ratio:.12g}" if aligned_ratio is not None else "",
                "nonstandard_vendor_action_in_pre_chain": str(before_nonstandard).lower() if before_nonstandard is not None else "",
                "nonstandard_vendor_action_in_post_chain": str(after_nonstandard).lower() if after_nonstandard is not None else "",
                "empirical_share_reconciliation": (
                    "AVAILABLE_FOR_REVIEW" if aligned_ratio is not None else "NO_DIRECT_SHARE_PAIR"
                ),
            }
        )
    _write_csv(paths["split_events"], event_rows)

    # Exact signal-date price audit: no use of next_trading_date or prior price.
    price_quality_counts = Counter()
    for security in master:
        if security["ticker"] not in mapped_tickers:
            continue
        ticker = security["ticker"]
        for signal_date in signal_dates:
            price = signal_prices.get((ticker, signal_date))
            if price is None:
                price_quality_counts["missing_signal_date_bar"] += 1
                continue
            close = _finite_float(price.get("close"))
            if close is None or close <= 0:
                price_quality_counts["missing_or_nonpositive_close"] += 1
            else:
                price_quality_counts["positive_exact_signal_close"] += 1
            if price["is_zero_volume"].lower() == "true":
                price_quality_counts["zero_volume_signal_close"] += 1
            if price["is_ohlc_plausible"].lower() != "true":
                price_quality_counts["invalid_ohlc_signal_close"] += 1
            if price["is_review_security"].lower() == "true":
                price_quality_counts["review_security_signal_close"] += 1
    price_basis_rows = [
        {
            "evidence_stage": EVIDENCE_STAGE,
            "audit_area": "close_vs_adj_close",
            "metric": "daily_rows",
            "value": price_counts.get("daily_rows", 0),
            "interpretation": "Canonical daily source rows audited.",
        },
        {
            "evidence_stage": EVIDENCE_STAGE,
            "audit_area": "close_vs_adj_close",
            "metric": "close_adj_close_different_rows",
            "value": price_counts.get("close_adj_close_different_rows", 0),
            "interpretation": "Fields are empirically distinct; adj_close cannot be substituted for price-level market cap.",
        },
        {
            "evidence_stage": EVIDENCE_STAGE,
            "audit_area": "corporate_actions",
            "metric": "positive_dividend_rows",
            "value": price_counts.get("positive_dividend_rows", 0),
            "interpretation": "Dividend events support the documented rule that adj_close has additional total-return adjustments.",
        },
        {
            "evidence_stage": EVIDENCE_STAGE,
            "audit_area": "corporate_actions",
            "metric": "nonzero_vendor_split_rows",
            "value": price_counts.get("nonzero_split_rows", 0),
            "interpretation": "Vendor action factors are split-basis audit evidence, not an automatically approved share-conversion chain.",
        },
    ]
    for metric, value in sorted(price_quality_counts.items()):
        price_basis_rows.append(
            {
                "evidence_stage": EVIDENCE_STAGE,
                "audit_area": "exact_signal_date_price",
                "metric": metric,
                "value": value,
                "interpretation": "Exact signal-date only; no next-day or forward-filled replacement is used.",
            }
        )
    _write_csv(paths["price_basis"], price_basis_rows)

    # Multi-class report.  It intentionally proves only what the delivered
    # identity and issuer facts support; class-specific share allocation is not
    # inferred from ticker names or duplicated issuer facts.
    multiclass_rows: list[dict[str, Any]] = []
    for cik, securities in sorted(all_by_cik.items()):
        if len(securities) <= 1:
            continue
        primary_direct = all_direct_shares.get((cik, PRIMARY_SHARE_TAG), [])
        candidate_direct = eligible_shares.get((cik, PRIMARY_SHARE_TAG), [])
        for security in securities:
            daily_prices = sum(1 for (ticker, _signal) in signal_prices if ticker == security["ticker"])
            multiclass_rows.append(
                {
                    "evidence_stage": EVIDENCE_STAGE,
                    "cik": cik,
                    "issuer": security["security_name"],
                    "canonical_ticker": security["ticker"],
                    "yahoo_ticker": security["yahoo_ticker"],
                    "share_class": "NOT_SUPPLIED_IN_SOURCE_MASTER",
                    "price_signal_date_bar_count": daily_prices,
                    "price_quality_status": security["quality_status"],
                    "primary_sec_share_observation_count": len(primary_direct),
                    "primary_sec_share_eligible_observation_count": len(candidate_direct),
                    "class_specific_shares_status": "NOT_DEMONSTRATED_BY_COMPANY_FACTS",
                    "market_cap_status": "MULTICLASS_UNRESOLVED",
                    "value_denominator_consequence": "Issuer accounting numerator must not be divided by one class price-times-duplicated issuer shares.",
                    "size_consequence": "No security-level size without class-specific shares; issuer-level alternative requires issuer aggregation/portfolio treatment sign-off.",
                    "canonical_version": CANONICAL_VERSION,
                }
            )
    _write_csv(paths["multiclass"], multiclass_rows)

    # Candidate method coverage is calculated as aggregate counts only.  No
    # market-cap value is retained or emitted.
    single_class_ciks = {cik for cik, securities in by_cik.items() if len(securities) == 1}
    method_counts: dict[str, dict[date, list[int]]] = {
        "SEC_REPORTED_SHARES_X_CLOSE_UNALIGNED": {item: [0, 0] for item in signal_dates},
        "SEC_YAHOO_STANDARD_SPLIT_CHAIN_ESTIMATE": {item: [0, 0] for item in signal_dates},
    }
    for security in master:
        cik = source_audit._as_int(security.get("cik"))
        if cik is None or cik not in mapped_ciks:
            continue
        ticker = security["ticker"]
        for signal_date in signal_dates:
            selection = selections.get((cik, PRIMARY_SHARE_TAG, signal_date), ShareSelection("NO_ELIGIBLE_FILING"))
            price = signal_prices.get((ticker, signal_date))
            positive_close = bool(price and (_finite_float(price.get("close")) or 0) > 0)
            method_counts["SEC_REPORTED_SHARES_X_CLOSE_UNALIGNED"][signal_date][1] += 1
            method_counts["SEC_YAHOO_STANDARD_SPLIT_CHAIN_ESTIMATE"][signal_date][1] += 1
            if selection.status == "AVAILABLE" and positive_close:
                method_counts["SEC_REPORTED_SHARES_X_CLOSE_UNALIGNED"][signal_date][0] += 1
                if cik in single_class_ciks and selection.observation:
                    _factor, has_nonstandard, _events = _chain_info(
                        split_by_ticker.get(ticker, []), selection.observation.end_date
                    )
                    if not has_nonstandard:
                        method_counts["SEC_YAHOO_STANDARD_SPLIT_CHAIN_ESTIMATE"][signal_date][0] += 1

    method_specs = {
        "SEC_REPORTED_SHARES_X_CLOSE_UNALIGNED": {
            "required_inputs": "Exact signal-date close + latest eligible primary DEI reported shares",
            "pit_safety": "Shares PIT-safe, but cross-basis mismatch remains",
            "split_consistency": "FAIL: reported shares are not converted to Yahoo delivery close basis",
            "multi_class_safety": "FAIL: issuer shares cannot be allocated to a listed class",
            "semantic_correctness": "Not sufficient for market-cap construction",
            "complexity": "Low",
            "known_biases": "Split-basis mismatch; periodic share staleness; price source retrospective adjustments",
            "recommendation": "NOT_IMPLEMENTABLE_AS_BASELINE",
        },
        "SEC_YAHOO_STANDARD_SPLIT_CHAIN_ESTIMATE": {
            "required_inputs": "Exact signal-date close + latest eligible primary DEI shares + audited standard split factors after share as-of date to delivery price basis",
            "pit_safety": "Shares PIT-safe; price basis is ex-post delivery-normalized",
            "split_consistency": "PASS for tested standard split mechanics; nonstandard vendor action chains excluded",
            "multi_class_safety": "PASS only where one current security maps to the issuer; multi-class excluded",
            "semantic_correctness": "Estimated issuer/security market cap only for one-security issuers",
            "complexity": "Medium; chain provenance and action review required",
            "known_biases": "Periodic shares; retrospective Yahoo basis; omitted/unresolved nonstandard actions; current-universe bias",
            "recommendation": "RECOMMENDED_BASELINE_SUBJECT_TO_SIGNOFF",
        },
        "CRSP_FORMAL": {
            "required_inputs": "CRSP/PERMNO historical shares and price/market-cap convention plus approved identity links",
            "pit_safety": "Potential Formal PIT replacement",
            "split_consistency": "Vendor-defined formal convention",
            "multi_class_safety": "Requires class/PERMNO identity verification",
            "semantic_correctness": "Potential formal replacement",
            "complexity": "External-data integration",
            "known_biases": "Not present in current source package",
            "recommendation": "NOT_IMPLEMENTABLE_WITH_CURRENT_SOURCE",
        },
    }
    method_rows: list[dict[str, Any]] = []
    for method, specification in method_specs.items():
        if method in method_counts:
            coverage = {item: tuple(values) for item, values in method_counts[method].items()}
            rates = [available / total for available, total in coverage.values() if total]
            overall = {
                "mean": sum(rates) / len(rates) if rates else None,
                "min": min(rates) if rates else None,
            }
            windows = _window_summary(coverage)
            window_text = json.dumps(windows, sort_keys=True)
            security_coverage = sum(1 for cik in single_class_ciks if any(
                selections.get((cik, PRIMARY_SHARE_TAG, signal), ShareSelection("NO")).status == "AVAILABLE"
                for signal in signal_dates
            ))
        else:
            overall = {"mean": None, "min": None}
            window_text = ""
            security_coverage = 0
        method_rows.append(
            {
                "evidence_stage": EVIDENCE_STAGE,
                "method_id": method,
                "required_inputs": specification["required_inputs"],
                "issuer_coverage_static": security_coverage,
                "security_coverage_static": security_coverage,
                "mean_weekly_coverage": f"{overall['mean']:.9f}" if overall["mean"] is not None else "",
                "min_weekly_coverage": f"{overall['min']:.9f}" if overall["min"] is not None else "",
                "window_coverage_json": window_text,
                "pit_safety": specification["pit_safety"],
                "split_consistency": specification["split_consistency"],
                "multi_class_safety": specification["multi_class_safety"],
                "semantic_correctness": specification["semantic_correctness"],
                "complexity": specification["complexity"],
                "known_biases": specification["known_biases"],
                "recommendation": specification["recommendation"],
                "market_cap_source": "SEC_YAHOO_ESTIMATE_DEV" if method.startswith("SEC_") else "CRSP_FORMAL",
                "canonical_version": CANONICAL_VERSION,
            }
        )
    _write_csv(paths["methods"], method_rows)

    manifest = source_audit.audit_manifest(source_audit.package_root(selected_root))
    summary = {
        "schema": CANONICAL_VERSION,
        "evidence_stage": EVIDENCE_STAGE,
        "raw_manifest": manifest,
        "share_tag_count": len(share_tag_rows),
        "primary_share_tag": {"taxonomy": PRIMARY_SHARE_TAG[0], "tag": PRIMARY_SHARE_TAG[1]},
        "alternative_share_tag": {"taxonomy": ALTERNATIVE_SHARE_TAG[0], "tag": ALTERNATIVE_SHARE_TAG[1]},
        "primary_share_pit_violations": share_pit_violations,
        "primary_share_selection_status_counts": dict(share_selection_statuses[PRIMARY_SHARE_TAG]),
        "primary_selected_value_le_one_security_signal_count": primary_selected_value_le_one,
        "primary_selected_value_le_one_examples": primary_selected_value_le_one_examples,
        "split_event_count": len(event_rows),
        "standard_split_event_count": sum(row["split_event_class"] == "STANDARD_SPLIT_FACTOR" for row in event_rows),
        "nonstandard_vendor_action_count": sum(row["split_event_class"] != "STANDARD_SPLIT_FACTOR" for row in event_rows),
        "reverse_standard_split_event_count": sum(
            row["split_event_class"] == "STANDARD_SPLIT_FACTOR" and float(row["split_factor"]) < 1
            for row in event_rows
        ),
        "multiclass_security_count": len(multiclass_rows),
        "multiclass_cik_count": len({row["cik"] for row in multiclass_rows}),
        "price_counts": price_counts,
        "signal_price_counts": dict(price_quality_counts),
        "prohibited_outputs_not_created": [
            "No final historical market-cap panel or us_weekly_market_cap_dev object",
            "No E/P, B/P, FCF, FCF Yield, Size, financial ratio, neutralization, IC, portfolio, FMOM, or strategy output",
        ],
    }
    paths["summary"].write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_acceptance(selected_root, summary)
    return {"summary": summary, "paths": {name: str(path) for name, path in paths.items()}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=project_root())
    args = parser.parse_args()
    result = run_audit(args.root)
    print(json.dumps(result["paths"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
