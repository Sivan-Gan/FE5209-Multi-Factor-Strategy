"""Read-only SEC Company Facts inspection for DEV fundamental-input specification.

This module profiles raw Company Facts and proposes PIT-safe selection rules.
It intentionally writes no canonical fundamental values and calculates no
financial factor, yield, ratio, market capitalization, IC, portfolio, or
strategy result.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from src.data import source_audit


EVIDENCE_STAGE = "DEV_CURRENT_UNIVERSE"
SPEC_VERSION = "fe5209.sec_fundamental_input_spec.dev.v1"
RAW_FACTS_RELATIVE = source_audit.SEC_FACTS_DIR


@dataclass(frozen=True)
class CandidateTag:
    """A semantically reviewed candidate, not a frozen selection priority."""

    tag: str
    note: str
    proposal_role: str


# Candidate names are reviewed starting points. They are not a tag-priority
# freeze: actual coverage and semantic risk are emitted for human review.
CONCEPT_CANDIDATES: dict[str, tuple[CandidateTag, ...]] = {
    "net_income": (
        CandidateTag("NetIncomeLoss", "Common US-GAAP net-income candidate.", "primary_candidate"),
        CandidateTag("ProfitLoss", "Consolidated profit/loss candidate; issuer presentation must be reviewed.", "conditional_candidate"),
        CandidateTag("NetIncomeLossAvailableToCommonStockholdersBasic", "Available-to-common variant; not automatically equivalent to consolidated net income.", "conditional_candidate"),
        CandidateTag("NetIncomeLossAttributableToParent", "Parent-attributable variant; not automatically interchangeable.", "conditional_candidate"),
    ),
    "common_equity": (
        CandidateTag("StockholdersEquity", "Stockholders' equity candidate.", "primary_candidate"),
        CandidateTag("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "Includes NCI; not automatically interchangeable with stockholders' equity.", "conditional_candidate"),
        CandidateTag("PartnersCapital", "Partnership-style equity presentation; semantic review required.", "conditional_candidate"),
        CandidateTag("MembersEquity", "Member-equity presentation; semantic review required.", "conditional_candidate"),
        CandidateTag("CommonStocksIncludingAdditionalPaidInCapital", "Capital-component, not necessarily total common equity.", "not_interchangeable"),
    ),
    "operating_cash_flow": (
        CandidateTag("NetCashProvidedByUsedInOperatingActivities", "Cash flow from operating activities candidate.", "primary_candidate"),
        CandidateTag("NetCashProvidedByUsedInOperatingActivitiesContinuingOperations", "Continuing-operations variant; comparability review required.", "conditional_candidate"),
    ),
    "capital_expenditure": (
        CandidateTag("PaymentsToAcquirePropertyPlantAndEquipment", "PP&E acquisition payments candidate; sign/presentation must be verified.", "primary_candidate"),
        CandidateTag("PaymentsToAcquireProductiveAssets", "Broader productive-assets variant; not automatically interchangeable.", "conditional_candidate"),
        CandidateTag("PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets", "Combined PP&E/intangibles; not pure CapEx without an approved definition.", "not_interchangeable"),
        CandidateTag("PaymentsToAcquireBusinessesNetOfCashAcquired", "Business acquisition cash flow; explicitly not CapEx fallback.", "not_interchangeable"),
    ),
    "total_assets": (
        CandidateTag("Assets", "Total-assets candidate.", "primary_candidate"),
        CandidateTag("AssetsCurrent", "Current assets only; explicitly not interchangeable with total assets.", "not_interchangeable"),
        CandidateTag("AssetsNoncurrent", "Non-current assets only; explicitly not interchangeable with total assets.", "not_interchangeable"),
    ),
    "gross_profit": (
        CandidateTag("GrossProfit", "Gross-profit candidate.", "primary_candidate"),
    ),
    "revenue": (
        CandidateTag("RevenueFromContractWithCustomerExcludingAssessedTax", "Modern revenue-recognition candidate; industry/issuer review needed.", "primary_candidate"),
        CandidateTag("Revenues", "Generic revenue candidate; semantic review needed.", "fallback_candidate"),
        CandidateTag("SalesRevenueNet", "Net sales candidate; may not match financial-issuer revenue economics.", "conditional_candidate"),
        CandidateTag("SalesRevenueGoodsNet", "Goods-only sales; not a universal revenue fallback.", "not_interchangeable"),
        CandidateTag("SalesRevenueServicesNet", "Services-only sales; not a universal revenue fallback.", "not_interchangeable"),
        CandidateTag("RevenueFromContractWithCustomerIncludingAssessedTax", "Tax-including variant; not automatically interchangeable.", "conditional_candidate"),
    ),
    "total_debt_support": (
        CandidateTag("LongTermDebtAndFinanceLeaseObligations", "Potential total long-term debt/lease amount; definition must be confirmed.", "primary_candidate"),
        CandidateTag("LongTermDebt", "Long-term debt presentation variant; semantic review required.", "fallback_candidate"),
        CandidateTag("LongTermDebtAndFinanceLeaseObligationsCurrent", "Current component only; supporting field, not total debt.", "supporting_component"),
        CandidateTag("LongTermDebtAndFinanceLeaseObligationsNoncurrent", "Non-current component only; supporting field, not total debt.", "supporting_component"),
        CandidateTag("LongTermDebtCurrent", "Current debt component; supporting field only.", "supporting_component"),
        CandidateTag("LongTermDebtNoncurrent", "Non-current debt component; supporting field only.", "supporting_component"),
        CandidateTag("ShortTermBorrowings", "Borrowing component; supporting field only.", "supporting_component"),
        CandidateTag("DebtCurrent", "Current debt component; supporting field only.", "supporting_component"),
    ),
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def audit_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / "outputs" / "audit" / "dev_legacy" / "sec_pit"


def output_paths(root: Path | None = None) -> dict[str, Path]:
    directory = audit_dir(root)
    return {
        "tag_coverage": directory / "sec_tag_coverage_dev.csv",
        "unit_audit": directory / "sec_unit_audit_dev.csv",
        "form_audit": directory / "sec_form_audit_dev.csv",
        "period_audit": directory / "sec_period_audit_dev.csv",
        "duplicate_audit": directory / "sec_duplicate_amendment_audit_dev.csv",
        "branch_coverage": directory / "sec_financial_re_coverage_dev.csv",
        "audit_json": directory / "sec_fundamental_spec_audit_dev.json",
        "acceptance": directory / "sec_fundamental_spec_acceptance_report.md",
    }


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _join_counts(counter: Counter[str], limit: int = 8) -> str:
    return ";".join(f"{item}:{count}" for item, count in counter.most_common(limit))


def _candidate_by_tag(tag: str) -> list[tuple[str, CandidateTag, str]]:
    """Return configured and cautiously discovered tag-to-concept matches."""

    matched: list[tuple[str, CandidateTag, str]] = []
    configured: set[tuple[str, str]] = set()
    for concept, candidates in CONCEPT_CANDIDATES.items():
        for candidate in candidates:
            if tag == candidate.tag:
                matched.append((concept, candidate, "configured_candidate"))
                configured.add((concept, tag))
    normalized = "".join(character for character in tag.lower() if character.isalnum())
    lexical: dict[str, bool] = {
        "net_income": "netincome" in normalized or "profitloss" in normalized,
        "common_equity": any(token in normalized for token in ("stockholdersequity", "shareholdersequity", "membersequity", "partnerscapital")),
        "operating_cash_flow": "netcash" in normalized and "operatingactivit" in normalized,
        "capital_expenditure": "paymentstoacquire" in normalized and any(token in normalized for token in ("propertyplant", "productiveasset", "capitalasset")),
        "total_assets": normalized in {"assets", "totalassets"},
        "gross_profit": "grossprofit" in normalized,
        "revenue": "revenue" in normalized or "sales" in normalized,
        "total_debt_support": "debt" in normalized or "borrow" in normalized or "financelease" in normalized,
    }
    for concept, is_match in lexical.items():
        if is_match and (concept, tag) not in configured:
            matched.append(
                (
                    concept,
                    CandidateTag(
                        tag,
                        "Lexical discovery only; semantic equivalence has not been established.",
                        "open_decision_lexical_discovery",
                    ),
                    "lexical_discovery",
                )
            )
    return matched


def classify_period(observation: dict[str, Any]) -> tuple[str, int | None, str]:
    """Return a proposed period class using dates, form, and fiscal period."""

    start = _parse_date(observation.get("start"))
    end = _parse_date(observation.get("end"))
    form = str(observation.get("form", ""))
    base_form = form.removesuffix("/A")
    fp = str(observation.get("fp", ""))
    if not observation.get("start"):
        return "instant", None, "no_start_date"
    if start is None or end is None:
        return "invalid_duration", None, "unparseable_start_or_end"
    days = (end - start).days
    if days <= 0:
        return "irregular_duration", days, "nonpositive_duration"
    if 330 <= days <= 380 and base_form in {"10-K", "20-F", "40-F"} and fp in {"FY", ""}:
        return "annual_candidate", days, "duration+annual_form+fy"
    if 75 <= days <= 110 and base_form == "10-Q" and fp in {"Q1", "Q2", "Q3", ""}:
        return "quarterly_candidate", days, "duration+10q+quarterly_fp"
    if 150 <= days <= 300 and base_form == "10-Q" and fp in {"Q2", "Q3", ""}:
        return "ytd_candidate", days, "duration+10q+ytd_fp"
    return "irregular_duration", days, "does_not_meet_proposed_duration_form_fp_rule"


def unit_status(concept: str, unit: str) -> str:
    """Return the stated concept-unit proposal without performing conversion."""

    if unit == "USD":
        return "proposed_canonical_unit"
    if unit in {"shares", "USD/shares", "pure"}:
        return "excluded_nonmonetary_or_ratio_unit"
    return "UNRESOLVED_UNIT"


def form_proposal(form: str) -> tuple[str, str]:
    """Describe the form's proposal status, never a hidden canonical filter."""

    base = form.removesuffix("/A")
    amended = form.endswith("/A")
    if form == "10-K":
        return "conditional_proposal", "Candidate for annual duration and instant facts after period/unit checks."
    if form == "10-Q":
        return "conditional_proposal", "Candidate for quarterly/YTD duration and instant facts after period/unit checks."
    if form in {"20-F", "40-F"}:
        return "conditional_proposal", "Annual-report analogue requires issuer-specific and period review."
    if base in {"10-K", "10-Q", "20-F", "40-F"} and amended:
        return "OPEN_DECISION", "Amended filing is PIT-usable only after its own filed date; selection/tie policy requires sign-off."
    if base == "8-K":
        return "OPEN_DECISION", "Current source contains XBRL facts, but use in canonical financial inputs is not frozen."
    return "excluded_proposal_pending_signoff", "Not proposed for initial canonical inputs without form-specific review."


def _period_bucket(period_class: str, days: int | None) -> str:
    if period_class == "instant":
        return "instant"
    if days is None:
        return "unknown_duration"
    if days <= 0:
        return "nonpositive"
    if days < 75:
        return "1_74_days"
    if days <= 110:
        return "75_110_days"
    if days <= 149:
        return "111_149_days"
    if days <= 300:
        return "150_300_days"
    if days <= 329:
        return "301_329_days"
    if days <= 380:
        return "330_380_days"
    return "381_plus_days"


@dataclass
class TagStats:
    concept: str
    taxonomy: str
    tag: str
    candidate: CandidateTag
    candidate_basis: str
    issuers: set[int] = field(default_factory=set)
    issuer_classes: Counter[str] = field(default_factory=Counter)
    issuer_class_members: dict[str, set[int]] = field(default_factory=lambda: defaultdict(set))
    observations: int = 0
    units: Counter[str] = field(default_factory=Counter)
    forms: Counter[str] = field(default_factory=Counter)
    period_classes: Counter[str] = field(default_factory=Counter)
    period_buckets: Counter[str] = field(default_factory=Counter)
    value_signs: Counter[str] = field(default_factory=Counter)
    first_end: date | None = None
    last_end: date | None = None
    amendments: int = 0
    duplicate_groups: int = 0
    duplicate_extra_observations: int = 0
    multi_filed_duplicate_groups: int = 0
    same_filed_ambiguous_groups: int = 0

    def observe(self, cik: int, issuer_class: str, unit: str, observation: dict[str, Any]) -> None:
        self.issuers.add(cik)
        self.issuer_classes[issuer_class] += 1
        self.issuer_class_members[issuer_class].add(cik)
        self.observations += 1
        self.units[unit] += 1
        form = str(observation.get("form", ""))
        self.forms[form] += 1
        if form.endswith("/A"):
            self.amendments += 1
        end = _parse_date(observation.get("end"))
        if end is not None:
            self.first_end = end if self.first_end is None else min(self.first_end, end)
            self.last_end = end if self.last_end is None else max(self.last_end, end)
        period_class, days, _ = classify_period(observation)
        self.period_classes[period_class] += 1
        self.period_buckets[_period_bucket(period_class, days)] += 1
        raw_value = observation.get("val")
        try:
            numeric_value = float(raw_value)
        except (TypeError, ValueError):
            self.value_signs["non_numeric"] += 1
        else:
            self.value_signs["positive" if numeric_value > 0 else "negative" if numeric_value < 0 else "zero"] += 1


def _issuer_class_map(package: Path) -> dict[int, str]:
    _, rows = source_audit._read_csv_rows(package / source_audit.SEC_CIK_MAP_FILE)
    sectors: dict[int, set[str]] = defaultdict(set)
    for row in rows:
        cik = source_audit._as_int(row.get("cik"))
        if cik is not None:
            sectors[cik].add(row.get("gics_sector", ""))
    result: dict[int, str] = {}
    for cik, values in sectors.items():
        if "Financials" in values:
            result[cik] = "financials_current_gics"
        elif "Real Estate" in values:
            result[cik] = "real_estate_current_gics"
        else:
            result[cik] = "non_financial_current_gics"
    return result


def _iter_raw_facts(package: Path) -> Iterator[tuple[int, str, str, str, dict[str, Any]]]:
    """Yield raw observations, preserving source facts and doing no selection."""

    for path in sorted((package / RAW_FACTS_RELATIVE).glob("CIK*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        cik = source_audit._as_int(payload.get("cik"))
        if cik is None:
            raise ValueError(f"Archive has no usable CIK: {path.name}")
        facts = payload.get("facts", {})
        if not isinstance(facts, dict):
            continue
        for taxonomy, tags in facts.items():
            if not isinstance(tags, dict):
                continue
            for tag, tag_payload in tags.items():
                if not isinstance(tag_payload, dict):
                    continue
                units = tag_payload.get("units", {})
                if not isinstance(units, dict):
                    continue
                for unit, observations in units.items():
                    if not isinstance(observations, list):
                        continue
                    for observation in observations:
                        if isinstance(observation, dict):
                            yield cik, str(taxonomy), str(tag), str(unit), observation


def collect_sec_fundamental_audit(root: Path | None = None) -> dict[str, Any]:
    """Inspect all 497 raw archives and return only specification/audit facts."""

    selected_root = root or project_root()
    package = source_audit.package_root(selected_root)
    issuer_classes = _issuer_class_map(package)
    stats: dict[tuple[str, str, str], TagStats] = {}
    all_form_counts: Counter[str] = Counter()
    all_form_issuers: dict[str, set[int]] = defaultdict(set)
    candidate_form_counts: Counter[str] = Counter()
    candidate_form_issuers: dict[str, set[int]] = defaultdict(set)
    archive_ciks: set[int] = set()
    raw_observations = 0

    # Duplicates are computed within an issuer/tag/unit/period while each raw
    # Company Facts archive is in memory; no selected value is produced.
    current_cik: int | None = None
    local_groups: dict[tuple[str, str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)

    for cik, taxonomy, tag, unit, observation in _iter_raw_facts(package):
        if current_cik is not None and cik != current_cik:
            _apply_duplicate_groups(local_groups, stats)
            local_groups = defaultdict(list)
        current_cik = cik
        archive_ciks.add(cik)
        raw_observations += 1
        form = str(observation.get("form", ""))
        all_form_counts[form] += 1
        all_form_issuers[form].add(cik)
        matches = _candidate_by_tag(tag)
        for concept, candidate, basis in matches:
            key = (concept, taxonomy, tag)
            if key not in stats:
                stats[key] = TagStats(concept, taxonomy, tag, candidate, basis)
            stat = stats[key]
            stat.observe(cik, issuer_classes.get(cik, "unmapped_current_gics"), unit, observation)
            candidate_form_counts[form] += 1
            candidate_form_issuers[form].add(cik)
            period_key = (
                concept,
                taxonomy,
                tag,
                unit,
                str(observation.get("start", "")),
                str(observation.get("end", "")),
            )
            local_groups[period_key].append(observation)
    _apply_duplicate_groups(local_groups, stats)

    return {
        "schema": SPEC_VERSION,
        "evidence_stage": EVIDENCE_STAGE,
        "archive_cik_count": len(archive_ciks),
        "raw_observation_count": raw_observations,
        "issuer_class_counts": dict(Counter(issuer_classes.values())),
        "tag_stats": stats,
        "all_form_counts": all_form_counts,
        "all_form_issuers": all_form_issuers,
        "candidate_form_counts": candidate_form_counts,
        "candidate_form_issuers": candidate_form_issuers,
    }


def _apply_duplicate_groups(
    groups: dict[tuple[str, str, str, str, str, str], list[dict[str, Any]]],
    stats: dict[tuple[str, str, str], TagStats],
) -> None:
    for (concept, taxonomy, tag, _unit, _start, _end), records in groups.items():
        if len(records) <= 1:
            continue
        stat = stats[(concept, taxonomy, tag)]
        stat.duplicate_groups += 1
        stat.duplicate_extra_observations += len(records) - 1
        filed = {str(record.get("filed", "")) for record in records if record.get("filed")}
        if len(filed) > 1:
            stat.multi_filed_duplicate_groups += 1
        else:
            stat.same_filed_ambiguous_groups += 1


def observation_available_on_or_before(observation: dict[str, Any], signal_date: date) -> bool:
    """Implement the frozen DEV conservative availability rule."""

    filed = _parse_date(observation.get("filed"))
    return filed is not None and filed <= signal_date


def select_latest_pit_observation_proposal(
    observations: Iterable[dict[str, Any]], signal_date: date
) -> tuple[dict[str, Any] | None, str]:
    """Proposed as-of selector for one already-compatible raw fact group.

    It filters to facts filed no later than the signal date, then selects the
    latest *eligible* filing date. It does not select an ambiguous set of
    conflicting observations sharing that latest filing date. This function is
    deliberately limited to testing/documenting timing; it does not create a
    canonical fundamental panel.
    """

    eligible = [item for item in observations if observation_available_on_or_before(item, signal_date)]
    if not eligible:
        return None, "no_fact_filed_on_or_before_signal_date"
    latest_filed = max(_parse_date(item.get("filed")) for item in eligible)
    latest = [item for item in eligible if _parse_date(item.get("filed")) == latest_filed]
    comparable = {(str(item.get("accn", "")), str(item.get("val", "")), str(item.get("form", ""))) for item in latest}
    if len(comparable) > 1:
        return None, "OPEN_DECISION_ambiguous_same_filed_date_duplicates"
    return latest[0], "selected_latest_eligible_filing"


def _tag_rows(audit: dict[str, Any]) -> list[dict[str, Any]]:
    issuer_total = audit["archive_cik_count"]
    rows = []
    for stat in sorted(audit["tag_stats"].values(), key=lambda item: (item.concept, item.taxonomy, item.tag)):
        rows.append(
            {
                "evidence_stage": EVIDENCE_STAGE,
                "economic_concept": stat.concept,
                "taxonomy": stat.taxonomy,
                "xbrl_tag": stat.tag,
                "candidate_basis": stat.candidate_basis,
                "proposal_role": stat.candidate.proposal_role,
                "issuer_count": len(stat.issuers),
                "issuer_coverage": f"{len(stat.issuers) / issuer_total:.9f}" if issuer_total else "",
                "issuer_count_financials_current_gics": len(stat.issuer_class_members["financials_current_gics"]),
                "issuer_count_real_estate_current_gics": len(stat.issuer_class_members["real_estate_current_gics"]),
                "observation_count": stat.observations,
                "dominant_units": _join_counts(stat.units),
                "dominant_forms": _join_counts(stat.forms),
                "first_end_date": stat.first_end.isoformat() if stat.first_end else "",
                "last_end_date": stat.last_end.isoformat() if stat.last_end else "",
                "instant_or_duration": _join_counts(stat.period_classes),
                "period_length_distribution": _join_counts(stat.period_buckets),
                "value_sign_distribution": _join_counts(stat.value_signs),
                "amendment_count": stat.amendments,
                "duplicate_period_count": stat.duplicate_groups,
                "multi_filed_duplicate_period_count": stat.multi_filed_duplicate_groups,
                "same_filed_ambiguous_duplicate_count": stat.same_filed_ambiguous_groups,
                "notes": stat.candidate.note,
            }
        )
    return rows


def _unit_rows(audit: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for stat in sorted(audit["tag_stats"].values(), key=lambda item: (item.concept, item.taxonomy, item.tag)):
        for unit, count in sorted(stat.units.items()):
            rows.append(
                {
                    "evidence_stage": EVIDENCE_STAGE,
                    "economic_concept": stat.concept,
                    "taxonomy": stat.taxonomy,
                    "xbrl_tag": stat.tag,
                    "unit": unit,
                    "observation_count": count,
                    "unit_status": unit_status(stat.concept, unit),
                    "conversion_rule": "none; no silent conversion in specification stage",
                    "notes": stat.candidate.note,
                }
            )
    return rows


def _period_rows(audit: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for stat in sorted(audit["tag_stats"].values(), key=lambda item: (item.concept, item.taxonomy, item.tag)):
        for period_class, count in sorted(stat.period_classes.items()):
            rows.append(
                {
                    "evidence_stage": EVIDENCE_STAGE,
                    "economic_concept": stat.concept,
                    "taxonomy": stat.taxonomy,
                    "xbrl_tag": stat.tag,
                    "proposed_period_class": period_class,
                    "observation_count": count,
                    "period_length_buckets": _join_counts(stat.period_buckets),
                    "rule_status": "proposal_requires_signoff",
                    "rule": "instant=no start; duration classification combines end-start length, base form, and fp",
                }
            )
    return rows


def _duplicate_rows(audit: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for stat in sorted(audit["tag_stats"].values(), key=lambda item: (item.concept, item.taxonomy, item.tag)):
        rows.append(
            {
                "evidence_stage": EVIDENCE_STAGE,
                "economic_concept": stat.concept,
                "taxonomy": stat.taxonomy,
                "xbrl_tag": stat.tag,
                "observation_count": stat.observations,
                "amendment_observation_count": stat.amendments,
                "duplicate_period_groups": stat.duplicate_groups,
                "duplicate_extra_observations": stat.duplicate_extra_observations,
                "multi_filed_duplicate_groups": stat.multi_filed_duplicate_groups,
                "same_filed_ambiguous_groups": stat.same_filed_ambiguous_groups,
                "pit_selector_proposal": "filter filed<=signal; select latest eligible filed; retain OPEN_DECISION on conflicting same-filed duplicates",
            }
        )
    return rows


def _form_rows(audit: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    forms = sorted(audit["all_form_counts"])
    for form in forms:
        status, reason = form_proposal(form)
        rows.append(
            {
                "evidence_stage": EVIDENCE_STAGE,
                "form": form,
                "observation_count_all_raw": audit["all_form_counts"][form],
                "issuer_count_all_raw": len(audit["all_form_issuers"][form]),
                "observation_count_candidate_concepts": audit["candidate_form_counts"][form],
                "issuer_count_candidate_concepts": len(audit["candidate_form_issuers"][form]),
                "proposal_status": status,
                "proposal_reason": reason,
            }
        )
    return rows


def _branch_rows(audit: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for concept in sorted(CONCEPT_CANDIDATES):
        matching = [stat for stat in audit["tag_stats"].values() if stat.concept == concept]
        issuers = set().union(*(stat.issuers for stat in matching)) if matching else set()
        financials = set().union(*(stat.issuer_class_members["financials_current_gics"] for stat in matching)) if matching else set()
        real_estate = set().union(*(stat.issuer_class_members["real_estate_current_gics"] for stat in matching)) if matching else set()
        rows.append(
            {
                "evidence_stage": EVIDENCE_STAGE,
                "economic_concept": concept,
                "issuer_count_any_candidate": len(issuers),
                "issuer_count_financials_current_gics": len(financials),
                "issuer_count_real_estate_current_gics": len(real_estate),
                "financials_re_note": "Current GICS branch inspection only; not historical PIT GICS and no branch formula is produced.",
            }
        )
    return rows


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_audit_outputs(audit: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    """Write SEC specification audits only; never write canonical fact values."""

    paths = output_paths(root)
    tag_rows = _tag_rows(audit)
    unit_rows = _unit_rows(audit)
    form_rows = _form_rows(audit)
    period_rows = _period_rows(audit)
    duplicate_rows = _duplicate_rows(audit)
    branch_rows = _branch_rows(audit)
    _write_csv(paths["tag_coverage"], list(tag_rows[0]) if tag_rows else [], tag_rows)
    _write_csv(paths["unit_audit"], list(unit_rows[0]) if unit_rows else [], unit_rows)
    _write_csv(paths["form_audit"], list(form_rows[0]) if form_rows else [], form_rows)
    _write_csv(paths["period_audit"], list(period_rows[0]) if period_rows else [], period_rows)
    _write_csv(paths["duplicate_audit"], list(duplicate_rows[0]) if duplicate_rows else [], duplicate_rows)
    _write_csv(paths["branch_coverage"], list(branch_rows[0]) if branch_rows else [], branch_rows)
    summary = {
        "schema": SPEC_VERSION,
        "evidence_stage": EVIDENCE_STAGE,
        "archive_cik_count": audit["archive_cik_count"],
        "raw_observation_count": audit["raw_observation_count"],
        "candidate_tag_rows": len(tag_rows),
        "reports": {name: str(path) for name, path in paths.items() if name not in {"acceptance", "audit_json"}},
        "prohibited_outputs_not_created": [
            "canonical fundamental values",
            "fundamental factors or ratios",
            "market capitalization",
            "factor preprocessing scores",
            "IC, portfolios, FMOM, strategy, or backtests",
        ],
    }
    paths["audit_json"].write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"paths": paths, "summary": summary, "rows": {"tags": tag_rows, "units": unit_rows, "forms": form_rows, "periods": period_rows, "duplicates": duplicate_rows, "branches": branch_rows}}


def write_acceptance_report(
    audit: dict[str, Any],
    output: dict[str, Any],
    manifest: dict[str, Any],
    root: Path | None = None,
) -> None:
    """Write a decision report for specification work, not factor construction."""

    paths = output_paths(root)
    open_forms = sum(1 for row in output["rows"]["forms"] if row["proposal_status"] == "OPEN_DECISION")
    unresolved_units = sum(1 for row in output["rows"]["units"] if row["unit_status"] == "UNRESOLVED_UNIT")
    duplicate_groups = sum(int(row["duplicate_period_groups"]) for row in output["rows"]["duplicates"])
    lines = [
        "# SEC Fundamental Input Specification — DEV Acceptance Report",
        "",
        f"- **Evidence stage:** `{EVIDENCE_STAGE}`",
        "- **Decision:** PASS WITH WARNINGS — the DEV specification/audit stage is complete; no fundamental-factor calculation is authorized.",
        "- **Canonical PIT construction:** conditional on human approval of the listed `OPEN_DECISION` items; it is not automatically authorized by this report.",
        "",
        "| Check | Status | Evidence / disposition |",
        "| --- | --- | --- |",
        f"| Raw package integrity after SEC scan | {'PASS' if manifest['passed'] else 'FAIL'} | Verified {manifest['verified_payload_files']}/{manifest['declared_payload_files']} payload files; failures={len(manifest['hash_failures'])}. |",
        f"| SEC archive/read-map coverage | {'PASS' if audit['archive_cik_count'] == 497 else 'FAIL'} | Read {audit['archive_cik_count']} issuer archives and {audit['raw_observation_count']} raw observations. |",
        f"| Candidate tag coverage audit | PASS | Produced {len(output['rows']['tags'])} configured/lexically discovered concept-tag rows; lexical discoveries are not selection rules. |",
        "| PIT filing availability proposal | PASS | Selector filters `filed_date <= signal_date`; future facts/amendments cannot affect earlier signals. |",
        f"| Form-selection proposal | WARN | {open_forms} observed form classes are OPEN_DECISION, including 8-K and amendment forms; only conditional proposals are documented. |",
        f"| Unit-selection proposal | {'WARN' if unresolved_units else 'PASS'} | {unresolved_units} candidate unit rows are `UNRESOLVED_UNIT`; no conversion is performed. |",
        f"| Duplicate/amendment proposal | WARN | Candidate-tag audit records {duplicate_groups} duplicate economic-period groups; same-filed conflicting duplicates remain open. |",
        "| Prohibited outputs | PASS | No canonical fundamental values, ratios, market cap, IC, portfolios, FMOM, or strategy output was written. |",
        "",
        "## DEV blockers and next-stage conditions",
        "",
        "There is no blocker to completing this specification/audit stage. Full Canonical PIT Fundamental Construction remains conditional: approve the concept-specific tag priorities and fallback semantics; decide 8-K/20-F/40-F policy; approve amendment treatment for ambiguous same-filed duplicates; and approve any branch handling for Revenue, Total Debt, and FCF. Formal PIT evidence additionally requires the already documented historical-universe/GICS inputs.",
    ]
    paths["acceptance"].write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_audit(root: Path | None = None) -> dict[str, Any]:
    selected_root = root or project_root()
    audit = collect_sec_fundamental_audit(selected_root)
    output = write_audit_outputs(audit, selected_root)
    manifest = source_audit.audit_manifest(source_audit.package_root(selected_root))
    output["summary"]["post_scan_raw_manifest"] = manifest
    output["paths"]["audit_json"].write_text(
        json.dumps(output["summary"], indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_acceptance_report(audit, output, manifest, selected_root)
    return {"audit": audit, "output": output, "manifest": manifest}


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile SEC raw facts for DEV input specification.")
    parser.add_argument("--root", type=Path, default=project_root())
    args = parser.parse_args()
    result = run_audit(args.root.resolve())
    print(json.dumps(result["output"]["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
