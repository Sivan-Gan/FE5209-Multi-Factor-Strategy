"""Read-only SEC design-review audits for DEV fundamental inputs.

This module deliberately audits source facts and method alternatives only.  It
does not create a canonical PIT panel, choose a factor input, calculate a
financial ratio/yield, or write any result outside
``outputs/audit/dev_legacy/sec_pit``.
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
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from src.data import sec_fundamental_spec as sec_spec
from src.data import source_audit


EVIDENCE_STAGE = "DEV_CURRENT_UNIVERSE"
REVIEW_VERSION = "fe5209.sec_fundamental_design_review.dev.v1"
USD = "USD"
PRIMARY_FORMS = frozenset({"10-K", "10-Q"})
FOREIGN_FORM_FAMILIES = {
    "20-F": frozenset({"20-F", "20-F/A"}),
    "40-F": frozenset({"40-F", "40-F/A"}),
}


# Only exact, named tags are examined.  The lists are review scopes, never a
# tag-priority chain for construction.
GROSS_PROFIT_TAG = "GrossProfit"
REVENUE_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "Revenues",
)
COST_TAGS = (
    "CostOfRevenue",
    "CostOfGoodsAndServicesSold",
    "CostOfGoodsSold",
    "CostOfServices",
    "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization",
)
FCF_TAGS = (
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
)
CORE_TAGS = (
    "NetIncomeLoss",
    "StockholdersEquity",
    "Assets",
)
DEBT_TAGS = (
    "DebtCurrent",
    "ShortTermBorrowings",
    "LongTermDebtCurrent",
    "LongTermDebtNoncurrent",
    "LongTermDebt",
    "LongTermDebtAndCapitalLeaseObligationsCurrent",
    "LongTermDebtAndCapitalLeaseObligations",
    "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
    "FinanceLeaseLiability",
    "FinanceLeaseLiabilityCurrent",
    "FinanceLeaseLiabilityNoncurrent",
    "OperatingLeaseLiability",
    "OperatingLeaseLiabilityCurrent",
    "OperatingLeaseLiabilityNoncurrent",
)
REVIEW_TAGS = frozenset(
    (GROSS_PROFIT_TAG,) + REVENUE_TAGS + COST_TAGS + FCF_TAGS + CORE_TAGS + DEBT_TAGS
)


@dataclass(frozen=True)
class GrossProfitPair:
    pair_id: str
    revenue_tag: str
    cost_tag: str
    semantic_status: str
    semantic_note: str


GROSS_PROFIT_PAIRS = (
    GrossProfitPair(
        "contract_revenue_ex_tax_less_cost_of_revenue",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "CostOfRevenue",
        "conditional_fallback_candidate",
        "Both labels describe operating revenue/cost at a consolidated level, but numerical overlap and issuer scope must support use.",
    ),
    GrossProfitPair(
        "sales_revenue_net_less_cost_of_revenue",
        "SalesRevenueNet",
        "CostOfRevenue",
        "conditional_fallback_candidate",
        "Net sales and aggregate cost can be compatible, but SalesRevenueNet is deprecated and no universal issuer merge is assumed.",
    ),
    GrossProfitPair(
        "sales_revenue_net_less_cost_of_goods_and_services_sold",
        "SalesRevenueNet",
        "CostOfGoodsAndServicesSold",
        "conditional_fallback_candidate",
        "The cost tag excludes financial-services and other revenue-generating activities; it cannot be a universal cross-industry fallback.",
    ),
    GrossProfitPair(
        "revenues_less_cost_of_revenue",
        "Revenues",
        "CostOfRevenue",
        "not_universal",
        "Revenues can include interest, investment income, and trading gains; this is not a universal Gross Profit identity.",
    ),
    GrossProfitPair(
        "revenues_less_cost_of_goods_and_services_sold",
        "Revenues",
        "CostOfGoodsAndServicesSold",
        "not_universal",
        "The revenue and cost tag descriptions have explicitly different financial-services scope; do not merge automatically.",
    ),
)


@dataclass(frozen=True)
class LeverageDefinition:
    definition_id: str
    numerator_tags: tuple[str, ...]
    economic_interpretation: str
    complexity: str
    double_counting_risk: str
    review_recommendation: str


LEVERAGE_DEFINITIONS = (
    LeverageDefinition(
        "long_term_debt_over_assets_proxy",
        ("LongTermDebt",),
        "Long-term debt intensity; the tag excludes capital leases and does not establish total debt.",
        "low: one debt tag plus Assets",
        "low aggregation risk, but material scope omission risk",
        "Do not label TotalDebt/Assets; acceptable only if the project explicitly chooses a long-term-debt proxy.",
    ),
    LeverageDefinition(
        "current_plus_noncurrent_long_term_debt_ex_leases_over_assets",
        ("LongTermDebtCurrent", "LongTermDebtNoncurrent"),
        "Current and non-current long-term debt, both described as excluding capital leases.",
        "medium: two mutually classified components plus Assets",
        "low conditional risk: only sum the exact current and non-current pair from the same filing; do not add LongTermDebt or lease tags",
        "Most interpretable debt-only TotalDebt/Assets candidate, conditional on approving lease exclusion.",
    ),
    LeverageDefinition(
        "current_plus_noncurrent_long_term_debt_and_capital_leases_over_assets",
        (
            "LongTermDebtAndCapitalLeaseObligationsCurrent",
            "LongTermDebtAndCapitalLeaseObligations",
        ),
        "Long-term debt and capital-lease obligations, split into current and non-current components.",
        "medium: two mutually classified components plus Assets",
        "low conditional risk: exact current/non-current pair only; no FinanceLeaseLiability or OperatingLeaseLiability addition",
        "Economically broader historical candidate, but coverage is lower and capital-lease versus finance-lease treatment needs approval.",
    ),
    LeverageDefinition(
        "long_term_debt_and_capital_leases_including_current_maturities_over_assets",
        ("LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",),
        "Single reported long-term-debt-and-capital-lease amount including current maturities.",
        "low: one reported total plus Assets",
        "lowest aggregation risk: no components are added",
        "Safest reported-total formulation where available, but expected coverage is low; do not supplement with components.",
    ),
    LeverageDefinition(
        "debt_current_plus_noncurrent_long_term_debt_over_assets",
        ("DebtCurrent", "LongTermDebtNoncurrent"),
        "Current debt (including current maturities and capital leases) plus non-current long-term debt excluding capital leases.",
        "medium: two components plus Assets",
        "medium: current/non-current labels are complementary, but lease scope differs across components",
        "Candidate only for human review; it is not a clean all-lease-inclusive or all-lease-exclusive definition.",
    ),
    LeverageDefinition(
        "short_term_borrowings_plus_current_and_noncurrent_long_term_debt_over_assets",
        ("ShortTermBorrowings", "LongTermDebtCurrent", "LongTermDebtNoncurrent"),
        "Short-term borrowings plus current/non-current long-term debt; all three exclude an explicit operating-lease policy.",
        "high: three components plus Assets",
        "medium/high: short-term borrowing and current-debt presentations may not be mutually exclusive in every issuer filing",
        "Do not approve without issuer-level overlap controls and a documented short-term-debt policy.",
    ),
)


SIGNOFF_ROWS = (
    (
        "NetIncomeLoss -> Net Income primary",
        "RECOMMEND_SIGNOFF",
        "494/497 issuer source coverage in prior audit; exact US-GAAP net-income tag remains subject to USD, duration, PIT, and ambiguity controls.",
    ),
    (
        "StockholdersEquity -> Common Equity primary",
        "RECOMMEND_SIGNOFF",
        "490/497 issuer source coverage; exact stockholders-equity tag is an instant fact and avoids automatic NCI/partner/member substitutions.",
    ),
    (
        "Assets -> Total Assets primary",
        "RECOMMEND_SIGNOFF",
        "497/497 issuer source coverage; exact Assets tag has the clearest source definition and is instant-only.",
    ),
    (
        "NetCashProvidedByUsedInOperatingActivities -> OCF primary",
        "RECOMMEND_SIGNOFF",
        "496/497 issuer source coverage; exact duration cash-flow tag is auditable without a continuing-operations fallback.",
    ),
    (
        "PaymentsToAcquirePropertyPlantAndEquipment -> CapEx primary",
        "RECOMMEND_SIGNOFF",
        "394/497 issuer source coverage; exact cash-payment tag is retained without acquisition/intangible fallbacks and negative-sign observations remain exceptions, not inversions.",
    ),
    (
        "filed_date <= signal_date",
        "RECOMMEND_SIGNOFF",
        "Conservative DEV availability boundary supported by the source filed field; RDQ/intraday timing remains unavailable.",
    ),
    (
        "Amendment effective only from its own filing date",
        "RECOMMEND_SIGNOFF",
        "Prevents future amendment information from backfilling an earlier signal date; it does not resolve same-date ambiguity.",
    ),
    (
        "No silent unit conversion",
        "RECOMMEND_SIGNOFF",
        "The raw facts contain non-USD units; a conversion source/rule has not been approved.",
    ),
    (
        "Instant/duration separation",
        "RECOMMEND_SIGNOFF",
        "Balance-sheet and flow facts have different source date structures; mixing them would be a category error.",
    ),
    (
        "Period classification uses dates + form + FY/FP, not FP alone",
        "RECOMMEND_SIGNOFF",
        "The source exhibits irregular and comparative durations; multi-field classification is required to avoid treating FP labels as sufficient evidence.",
    ),
)


@dataclass
class TagEvidence:
    taxonomy: str
    tag: str
    label: str = ""
    description: str = ""
    issuers: set[int] = field(default_factory=set)
    observations: int = 0
    units: Counter[str] = field(default_factory=Counter)
    forms: Counter[str] = field(default_factory=Counter)
    period_classes: Counter[str] = field(default_factory=Counter)
    signs: Counter[str] = field(default_factory=Counter)
    first_end: date | None = None
    last_end: date | None = None

    def observe(self, cik: int, unit: str, observation: dict[str, Any], label: Any, description: Any) -> None:
        self.issuers.add(cik)
        self.observations += 1
        self.units[unit] += 1
        self.forms[str(observation.get("form", ""))] += 1
        if not self.label and label:
            self.label = str(label)
        if not self.description and description:
            self.description = str(description)
        period_class, _, _ = sec_spec.classify_period(observation)
        self.period_classes[period_class] += 1
        end = _parse_date(observation.get("end"))
        if end is not None:
            self.first_end = end if self.first_end is None else min(self.first_end, end)
            self.last_end = end if self.last_end is None else max(self.last_end, end)
        value = _decimal_value(observation.get("val"))
        if value is None:
            self.signs["non_numeric"] += 1
        elif value > 0:
            self.signs["positive"] += 1
        elif value < 0:
            self.signs["negative"] += 1
        else:
            self.signs["zero"] += 1


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def audit_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / "outputs" / "audit" / "dev_legacy" / "sec_pit"


def output_paths(root: Path | None = None) -> dict[str, Path]:
    directory = audit_dir(root)
    return {
        "tag_inventory": directory / "sec_design_tag_inventory_dev.csv",
        "gross_profit": directory / "sec_gross_profit_fallback_audit_dev.csv",
        "leverage": directory / "sec_leverage_definition_audit_dev.csv",
        "fcf": directory / "sec_fcf_input_audit_dev.csv",
        "foreign_forms": directory / "sec_foreign_form_audit_dev.csv",
        "conflicts": directory / "sec_same_filed_conflict_audit_dev.csv",
        "signoff": directory / "sec_design_signoff_table_dev.csv",
        "audit_json": directory / "sec_fundamental_design_review_dev.json",
        "acceptance": directory / "sec_fundamental_design_review_acceptance_report.md",
    }


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _decimal_value(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _value_token(value: Any) -> str:
    numeric = _decimal_value(value)
    return str(numeric.normalize()) if numeric is not None else f"non_numeric:{value!s}"


def _joined(counter: Counter[str]) -> str:
    return ";".join(f"{item}:{count}" for item, count in counter.most_common())


def _form_base(form: str) -> str:
    return form.removesuffix("/A")


def _is_primary_duration(observation: dict[str, Any]) -> bool:
    if str(observation.get("form", "")) not in PRIMARY_FORMS:
        return False
    period_class, _, _ = sec_spec.classify_period(observation)
    return period_class in {"annual_candidate", "quarterly_candidate", "ytd_candidate"}


def _is_primary_instant(observation: dict[str, Any]) -> bool:
    return (
        str(observation.get("form", "")) in PRIMARY_FORMS
        and sec_spec.classify_period(observation)[0] == "instant"
    )


def _flow_key(cik: int, unit: str, observation: dict[str, Any]) -> tuple[Any, ...]:
    return (
        cik,
        observation.get("start", ""),
        observation.get("end", ""),
        unit,
        observation.get("form", ""),
        observation.get("filed", ""),
        observation.get("accn", ""),
    )


def _instant_key(cik: int, unit: str, observation: dict[str, Any]) -> tuple[Any, ...]:
    return (
        cik,
        observation.get("end", ""),
        unit,
        observation.get("form", ""),
        observation.get("filed", ""),
        observation.get("accn", ""),
    )


def _key_end(key: tuple[Any, ...], instant: bool = False) -> str:
    return str(key[1] if instant else key[2])


def _key_form(key: tuple[Any, ...], instant: bool = False) -> str:
    return str(key[3] if instant else key[4])


def _stable_values(records: dict[tuple[Any, ...], set[str]]) -> dict[tuple[Any, ...], Decimal]:
    stable: dict[tuple[Any, ...], Decimal] = {}
    for key, values in records.items():
        if len(values) != 1:
            continue
        value = _decimal_value(next(iter(values)))
        if value is not None:
            stable[key] = value
    return stable


def _percentile(values: list[Decimal], percentile: float) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _display_decimal(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value, "f")


def _write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _iter_payloads(package: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    raw_dir = package / source_audit.SEC_FACTS_DIR
    for path in sorted(raw_dir.glob("CIK*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        cik = source_audit._as_int(payload.get("cik"))
        if cik is None:
            raise ValueError(f"Missing usable CIK in {path.name}")
        yield cik, payload


def _iter_named_facts(
    payload: dict[str, Any],
    names: set[str] | frozenset[str] | None = None,
) -> Iterable[tuple[str, str, str, dict[str, Any], dict[str, Any]]]:
    for taxonomy, tags in payload.get("facts", {}).items():
        if not isinstance(tags, dict):
            continue
        for tag, tag_payload in tags.items():
            if names is not None and tag not in names:
                continue
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
                        yield str(taxonomy), str(tag), str(unit), observation, tag_payload


def _tag_inventory(package: Path) -> list[dict[str, Any]]:
    evidence: dict[tuple[str, str], TagEvidence] = {}
    for cik, payload in _iter_payloads(package):
        for taxonomy, tag, unit, observation, tag_payload in _iter_named_facts(payload, REVIEW_TAGS):
            item = evidence.setdefault((taxonomy, tag), TagEvidence(taxonomy, tag))
            item.observe(cik, unit, observation, tag_payload.get("label"), tag_payload.get("description"))
    rows: list[dict[str, Any]] = []
    for (taxonomy, tag), item in sorted(evidence.items()):
        rows.append(
            {
                "evidence_stage": EVIDENCE_STAGE,
                "taxonomy": taxonomy,
                "xbrl_tag": tag,
                "source_label": item.label,
                "source_description": item.description,
                "issuer_count": len(item.issuers),
                "issuer_coverage": f"{len(item.issuers) / 497:.9f}",
                "observation_count": item.observations,
                "units": _joined(item.units),
                "forms": _joined(item.forms),
                "period_classes": _joined(item.period_classes),
                "value_signs": _joined(item.signs),
                "first_end_date": item.first_end.isoformat() if item.first_end else "",
                "last_end_date": item.last_end.isoformat() if item.last_end else "",
            }
        )
    return rows


def _collect_flow_maps(package: Path) -> dict[str, dict[tuple[Any, ...], Decimal]]:
    tag_values: dict[str, dict[tuple[Any, ...], set[str]]] = defaultdict(lambda: defaultdict(set))
    target = set((GROSS_PROFIT_TAG,) + REVENUE_TAGS + COST_TAGS + FCF_TAGS)
    for cik, payload in _iter_payloads(package):
        for taxonomy, tag, unit, observation, _ in _iter_named_facts(payload, target):
            if taxonomy != "us-gaap" or unit != USD or not _is_primary_duration(observation):
                continue
            tag_values[tag][_flow_key(cik, unit, observation)].add(_value_token(observation.get("val")))
    return {tag: _stable_values(values) for tag, values in tag_values.items()}


def _gross_profit_audit(flow_maps: dict[str, dict[tuple[Any, ...], Decimal]]) -> list[dict[str, Any]]:
    direct = flow_maps.get(GROSS_PROFIT_TAG, {})
    direct_issuers = {int(key[0]) for key in direct}
    rows: list[dict[str, Any]] = []
    for pair in GROSS_PROFIT_PAIRS:
        revenue = flow_maps.get(pair.revenue_tag, {})
        cost = flow_maps.get(pair.cost_tag, {})
        common_keys = set(revenue).intersection(cost)
        reconstructed = {key: revenue[key] - cost[key] for key in common_keys}
        overlap = set(reconstructed).intersection(direct)
        absolute_errors = [abs(reconstructed[key] - direct[key]) for key in overlap]
        relative_errors = [
            abs(reconstructed[key] - direct[key]) / abs(direct[key])
            for key in overlap
            if direct[key] != 0
        ]
        exact = sum(reconstructed[key] == direct[key] for key in overlap)
        fallback_only = set(reconstructed).difference(direct)
        combined_issuers = direct_issuers.union(int(key[0]) for key in reconstructed)
        ends = [_key_end(key) for key in common_keys if _key_end(key)]
        forms = Counter(_key_form(key) for key in common_keys)
        rows.append(
            {
                "evidence_stage": EVIDENCE_STAGE,
                "pair_id": pair.pair_id,
                "revenue_tag": pair.revenue_tag,
                "cost_tag": pair.cost_tag,
                "semantic_status": pair.semantic_status,
                "semantic_note": pair.semantic_note,
                "eligible_reconstructed_period_count": len(reconstructed),
                "eligible_reconstructed_issuer_count": len({int(key[0]) for key in reconstructed}),
                "direct_gross_profit_period_count": len(direct),
                "direct_gross_profit_issuer_count": len(direct_issuers),
                "overlap_period_count": len(overlap),
                "overlap_exact_match_count": exact,
                "overlap_exact_match_rate": f"{exact / len(overlap):.9f}" if overlap else "",
                "overlap_abs_error_median": _display_decimal(_percentile(absolute_errors, 0.5)),
                "overlap_abs_error_p95": _display_decimal(_percentile(absolute_errors, 0.95)),
                "overlap_abs_error_max": _display_decimal(max(absolute_errors) if absolute_errors else None),
                "overlap_relative_abs_error_median": _display_decimal(_percentile(relative_errors, 0.5)),
                "overlap_relative_abs_error_p95": _display_decimal(_percentile(relative_errors, 0.95)),
                "fallback_only_period_count": len(fallback_only),
                "fallback_only_issuer_count": len({int(key[0]) for key in fallback_only}),
                "combined_exact_or_pair_issuer_count": len(combined_issuers),
                "incremental_issuer_count_vs_direct_gross_profit": len(combined_issuers) - len(direct_issuers),
                "first_reconstructed_end_date": min(ends) if ends else "",
                "last_reconstructed_end_date": max(ends) if ends else "",
                "forms": _joined(forms),
                "audit_scope": "USD us-gaap original 10-K/10-Q duration facts matched on issuer,start,end,form,filed,accession; ambiguous same-tag keys excluded",
            }
        )
    return rows


def _fcf_audit(flow_maps: dict[str, dict[tuple[Any, ...], Decimal]]) -> list[dict[str, Any]]:
    ocf = flow_maps.get("NetCashProvidedByUsedInOperatingActivities", {})
    capex = flow_maps.get("PaymentsToAcquirePropertyPlantAndEquipment", {})
    common_keys = set(ocf).intersection(capex)
    capex_signs: Counter[str] = Counter()
    for key in common_keys:
        value = capex[key]
        capex_signs["positive" if value > 0 else "negative" if value < 0 else "zero"] += 1
    ends = [_key_end(key) for key in common_keys if _key_end(key)]
    forms = Counter(_key_form(key) for key in common_keys)
    return [
        {
            "evidence_stage": EVIDENCE_STAGE,
            "definition_under_review": "FCF = OCF - CapEx (input compatibility audit only; no FCF values or yield produced)",
            "ocf_tag": "NetCashProvidedByUsedInOperatingActivities",
            "capex_tag": "PaymentsToAcquirePropertyPlantAndEquipment",
            "ocf_eligible_period_count": len(ocf),
            "ocf_eligible_issuer_count": len({int(key[0]) for key in ocf}),
            "capex_eligible_period_count": len(capex),
            "capex_eligible_issuer_count": len({int(key[0]) for key in capex}),
            "compatible_ocf_capex_period_count": len(common_keys),
            "compatible_ocf_capex_issuer_count": len({int(key[0]) for key in common_keys}),
            "capex_positive_count": capex_signs["positive"],
            "capex_zero_count": capex_signs["zero"],
            "capex_negative_count": capex_signs["negative"],
            "capex_positive_share": f"{capex_signs['positive'] / len(common_keys):.9f}" if common_keys else "",
            "first_compatible_end_date": min(ends) if ends else "",
            "last_compatible_end_date": max(ends) if ends else "",
            "forms": _joined(forms),
            "sign_convention_finding": "Positive PaymentsToAcquirePropertyPlantAndEquipment is a cash payment and can be deducted. Negative values are retained as sign exceptions/missing pending review; never silently absolute-value or invert.",
            "audit_scope": "USD us-gaap original 10-K/10-Q duration facts matched on issuer,start,end,form,filed,accession; no FCF calculation persisted",
        }
    ]


def _collect_instant_maps(package: Path) -> dict[str, dict[tuple[Any, ...], Decimal]]:
    tag_values: dict[str, dict[tuple[Any, ...], set[str]]] = defaultdict(lambda: defaultdict(set))
    target = set(DEBT_TAGS + ("Assets",))
    for cik, payload in _iter_payloads(package):
        for taxonomy, tag, unit, observation, _ in _iter_named_facts(payload, target):
            if taxonomy != "us-gaap" or unit != USD or not _is_primary_instant(observation):
                continue
            tag_values[tag][_instant_key(cik, unit, observation)].add(_value_token(observation.get("val")))
    return {tag: _stable_values(values) for tag, values in tag_values.items()}


def _leverage_audit(instant_maps: dict[str, dict[tuple[Any, ...], Decimal]]) -> list[dict[str, Any]]:
    assets = instant_maps.get("Assets", {})
    rows: list[dict[str, Any]] = []
    reconciliation_keys = (
        set(instant_maps.get("LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities", {}))
        .intersection(instant_maps.get("LongTermDebtAndCapitalLeaseObligationsCurrent", {}))
        .intersection(instant_maps.get("LongTermDebtAndCapitalLeaseObligations", {}))
    )
    direct_total = instant_maps.get("LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities", {})
    current = instant_maps.get("LongTermDebtAndCapitalLeaseObligationsCurrent", {})
    noncurrent = instant_maps.get("LongTermDebtAndCapitalLeaseObligations", {})
    exact_reconciliations = sum(
        direct_total[key] == current[key] + noncurrent[key] for key in reconciliation_keys
    )
    for definition in LEVERAGE_DEFINITIONS:
        compatible = set(assets)
        for tag in definition.numerator_tags:
            compatible.intersection_update(instant_maps.get(tag, {}))
        ends = [_key_end(key, instant=True) for key in compatible if _key_end(key, instant=True)]
        forms = Counter(_key_form(key, instant=True) for key in compatible)
        rows.append(
            {
                "evidence_stage": EVIDENCE_STAGE,
                "definition_id": definition.definition_id,
                "numerator_tags": "+".join(definition.numerator_tags),
                "denominator_tag": "Assets",
                "compatible_same_filing_period_count": len(compatible),
                "compatible_issuer_count": len({int(key[0]) for key in compatible}),
                "first_end_date": min(ends) if ends else "",
                "last_end_date": max(ends) if ends else "",
                "forms": _joined(forms),
                "economic_interpretation": definition.economic_interpretation,
                "complexity": definition.complexity,
                "double_counting_risk": definition.double_counting_risk,
                "review_recommendation": definition.review_recommendation,
                "capital_lease_direct_total_reconciliation_key_count": len(reconciliation_keys),
                "capital_lease_direct_total_exact_reconciliation_count": exact_reconciliations,
                "capital_lease_direct_total_reconciliation_note": "Diagnostic only: direct reported total versus exact current+non-current pair; no debt value or leverage ratio is persisted.",
                "audit_scope": "USD us-gaap original 10-K/10-Q instant facts matched on issuer,end,form,filed,accession; no leverage ratio calculated",
            }
        )
    return rows


def _ticker_map(package: Path) -> dict[int, set[str]]:
    _, rows = source_audit._read_csv_rows(package / source_audit.SEC_CIK_MAP_FILE)
    values: dict[int, set[str]] = defaultdict(set)
    for row in rows:
        cik = source_audit._as_int(row.get("cik"))
        if cik is not None and row.get("ticker"):
            values[cik].add(str(row["ticker"]))
    return values


def _foreign_concept(tag: str) -> str | None:
    if tag in {"GrossProfit"}:
        return "gross_profit"
    if tag in set(REVENUE_TAGS) | {"Revenue", "RevenueFromContractsWithCustomers"}:
        return "revenue"
    if tag in set(COST_TAGS) | {"CostOfSales"}:
        return "cost_of_revenue"
    if tag == "Assets":
        return "total_assets"
    if tag == "NetCashProvidedByUsedInOperatingActivities":
        return "operating_cash_flow"
    if tag == "PaymentsToAcquirePropertyPlantAndEquipment":
        return "capital_expenditure"
    if tag in set(DEBT_TAGS) | {
        "Borrowings",
        "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
        "LongtermBorrowings",
        "FinanceLeaseLiabilities",
        "LeaseLiabilities",
    }:
        return "debt_or_lease_support"
    if tag == "NetIncomeLoss":
        return "net_income"
    if tag == "StockholdersEquity":
        return "common_equity"
    return None


def _foreign_form_audit(package: Path) -> list[dict[str, Any]]:
    tickers = _ticker_map(package)
    foreign: dict[tuple[str, str], set[int]] = defaultdict(set)
    domestic: dict[str, set[int]] = defaultdict(set)
    foreign_periods: dict[tuple[str, str], set[tuple[int, str, str]]] = defaultdict(set)
    domestic_periods: dict[str, set[tuple[int, str, str]]] = defaultdict(set)
    taxa: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    tags: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    observations: Counter[tuple[str, str]] = Counter()
    all_foreign_ciks: dict[str, set[int]] = defaultdict(set)
    for cik, payload in _iter_payloads(package):
        for taxonomy, tag, unit, observation, _ in _iter_named_facts(payload):
            concept = _foreign_concept(tag)
            if concept is None:
                continue
            form = str(observation.get("form", ""))
            for family, forms in FOREIGN_FORM_FAMILIES.items():
                if form in forms:
                    key = (family, concept)
                    foreign[key].add(cik)
                    foreign_periods[key].add((cik, str(observation.get("start", "")), str(observation.get("end", ""))))
                    all_foreign_ciks[family].add(cik)
                    taxa[key][taxonomy] += 1
                    tags[key][f"{taxonomy}:{tag}"] += 1
                    observations[key] += 1
            if _form_base(form) in PRIMARY_FORMS:
                domestic[concept].add(cik)
                domestic_periods[concept].add((cik, str(observation.get("start", "")), str(observation.get("end", ""))))
    concept_order = (
        "gross_profit",
        "revenue",
        "cost_of_revenue",
        "total_assets",
        "operating_cash_flow",
        "capital_expenditure",
        "debt_or_lease_support",
        "net_income",
        "common_equity",
    )
    rows: list[dict[str, Any]] = []
    for family in ("20-F", "40-F"):
        for concept in concept_order:
            key = (family, concept)
            issuer_set = foreign[key]
            all_history_relying = issuer_set.difference(domestic[concept])
            foreign_only_periods = foreign_periods[key].difference(domestic_periods[concept])
            period_relying = {period[0] for period in foreign_only_periods}
            rows.append(
                {
                    "evidence_stage": EVIDENCE_STAGE,
                    "foreign_form_family": family,
                    "economic_concept": concept,
                    "issuer_count_with_foreign_form": len(issuer_set),
                    "issuer_ciks_with_foreign_form": ";".join(str(cik) for cik in sorted(issuer_set)),
                    "issuer_tickers_with_foreign_form": ";".join(
                        ",".join(sorted(tickers.get(cik, set()))) for cik in sorted(issuer_set)
                    ),
                    "issuer_count_no_any_10k_10q_for_concept": len(all_history_relying),
                    "issuer_count_with_foreign_only_periods": len(period_relying),
                    "issuer_ciks_with_foreign_only_periods": ";".join(str(cik) for cik in sorted(period_relying)),
                    "issuer_tickers_with_foreign_only_periods": ";".join(
                        ",".join(sorted(tickers.get(cik, set()))) for cik in sorted(period_relying)
                    ),
                    "foreign_period_identity_count": len(foreign_periods[key]),
                    "foreign_periods_without_same_concept_10k_10q": len(foreign_only_periods),
                    "foreign_observation_count": observations[key],
                    "taxonomy_observations": _joined(taxa[key]),
                    "observed_tags": _joined(tags[key]),
                    "all_issuer_count_any_audited_foreign_concept": len(all_foreign_ciks[family]),
                    "interpretation": "Foreign-only periods have no same-concept 10-K/10-Q fact for the same start/end. All-history reliance is stricter and asks whether the issuer has any 10-K/10-Q fact for the concept. Neither is an inclusion/exclusion decision.",
                }
            )
    return rows


def _conflict_concept(tag: str) -> str | None:
    if tag == "NetIncomeLoss":
        return "net_income"
    if tag == "StockholdersEquity":
        return "common_equity"
    if tag == "Assets":
        return "total_assets"
    if tag == "NetCashProvidedByUsedInOperatingActivities":
        return "operating_cash_flow"
    if tag == "PaymentsToAcquirePropertyPlantAndEquipment":
        return "capital_expenditure"
    if tag == "GrossProfit":
        return "gross_profit"
    if tag in REVENUE_TAGS:
        return "revenue"
    if tag in COST_TAGS:
        return "cost_of_revenue"
    if tag in DEBT_TAGS:
        return "debt_or_lease_support"
    return None


def classify_same_filed_conflict(records: list[dict[str, Any]]) -> dict[str, bool]:
    """Classify one same-tag, same-period latest-filing conflict without selecting it."""

    accessions = {str(record.get("accn", "")) for record in records}
    forms = {str(record.get("form", "")) for record in records}
    fy_fp = {(str(record.get("fy", "")), str(record.get("fp", ""))) for record in records}
    frames = {str(record.get("frame", "")) for record in records}
    amended = any(form.endswith("/A") for form in forms)
    different_accession = len(accessions) > 1
    same_accession = len(accessions) == 1
    context_surrogate = len(fy_fp) > 1 or len(frames) > 1
    other = same_accession and not amended and not context_surrogate
    return {
        "different_accession": different_accession,
        "same_accession": same_accession,
        "amendment": amended,
        "different_context_or_period_surrogate": context_surrogate,
        "other": other,
    }


def _finalize_conflict_groups(
    cik: int,
    same_groups: dict[tuple[Any, ...], list[dict[str, Any]]],
    cross_groups: dict[tuple[Any, ...], list[dict[str, Any]]],
    counters: Counter[str],
    issuers: dict[str, set[int]],
) -> None:
    for records in same_groups.values():
        filed_dates = [_parse_date(record.get("filed")) for record in records]
        valid = [record for record, filed in zip(records, filed_dates) if filed is not None]
        if not valid:
            continue
        latest = max(_parse_date(record.get("filed")) for record in valid)
        same_latest = [record for record in valid if _parse_date(record.get("filed")) == latest]
        counters["same_tag_latest_groups"] += 1
        if len({_value_token(record.get("val")) for record in same_latest}) <= 1:
            continue
        counters["same_tag_conflicting_groups"] += 1
        issuers["same_tag_conflicting_groups"].add(cik)
        for category, present in classify_same_filed_conflict(same_latest).items():
            if present:
                counters[category] += 1
                issuers[category].add(cik)
    for records in cross_groups.values():
        filed_dates = [_parse_date(record.get("filed")) for record in records]
        valid = [record for record, filed in zip(records, filed_dates) if filed is not None]
        if not valid:
            continue
        latest = max(_parse_date(record.get("filed")) for record in valid)
        same_latest = [record for record in valid if _parse_date(record.get("filed")) == latest]
        counters["cross_tag_latest_concept_groups"] += 1
        tags = {(record["taxonomy"], record["tag"]) for record in same_latest}
        values = {_value_token(record.get("val")) for record in same_latest}
        if len(tags) > 1 and len(values) > 1:
            counters["taxonomy_tag_difference_groups"] += 1
            issuers["taxonomy_tag_difference_groups"].add(cik)


def _conflict_audit(package: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    counters: Counter[str] = Counter()
    issuers: dict[str, set[int]] = defaultdict(set)
    for cik, payload in _iter_payloads(package):
        same_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        cross_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for taxonomy, tag, unit, observation, _ in _iter_named_facts(payload, REVIEW_TAGS):
            concept = _conflict_concept(tag)
            if concept is None:
                continue
            source_record = dict(observation)
            source_record["taxonomy"] = taxonomy
            source_record["tag"] = tag
            same_key = (taxonomy, tag, unit, observation.get("start", ""), observation.get("end", ""))
            cross_key = (concept, unit, observation.get("start", ""), observation.get("end", ""))
            same_groups[same_key].append(source_record)
            cross_groups[cross_key].append(source_record)
        _finalize_conflict_groups(cik, same_groups, cross_groups, counters, issuers)
    denominator = counters["same_tag_latest_groups"]
    descriptions = {
        "same_tag_conflicting_groups": "Same taxonomy/tag/unit/start/end group whose maximum filed date contains multiple values.",
        "different_accession": "Non-exclusive source attribute: conflicting latest records have more than one accession.",
        "same_accession": "Non-exclusive source attribute: conflicting latest records have one accession.",
        "amendment": "Non-exclusive source attribute: at least one conflicting latest record is an amended form.",
        "different_context_or_period_surrogate": "Non-exclusive surrogate: FY/FP or frame differs. Company Facts supplies no context identifier, and start/end are already fixed by the group.",
        "other": "Same-accession conflict without amendment or available context surrogate difference; unresolved from Company Facts fields.",
        "taxonomy_tag_difference_groups": "Different tags/taxonomies for the same audited economic concept, period, and latest filing date with differing values. This is semantic competition, not a same-tag tie and must not be auto-merged.",
    }
    order = (
        "same_tag_conflicting_groups",
        "different_accession",
        "same_accession",
        "amendment",
        "different_context_or_period_surrogate",
        "other",
        "taxonomy_tag_difference_groups",
    )
    rows = [
        {
            "evidence_stage": EVIDENCE_STAGE,
            "scope": "reviewed named fundamental tags only",
            "metric": "same_tag_latest_groups",
            "group_count": denominator,
            "issuer_count": "",
            "share_of_same_tag_latest_groups": "1.000000000" if denominator else "",
            "classification_type": "denominator",
            "description": "Eligible issuer/taxonomy/tag/unit/start/end groups with a valid latest filed date.",
            "recommended_handling": "Audit denominator only; do not select a value from it.",
        }
    ]
    for metric in order:
        rows.append(
            {
                "evidence_stage": EVIDENCE_STAGE,
                "scope": "reviewed named fundamental tags only",
                "metric": metric,
                "group_count": counters[metric],
                "issuer_count": len(issuers[metric]),
                "share_of_same_tag_latest_groups": f"{counters[metric] / denominator:.9f}" if denominator else "",
                "classification_type": "nonexclusive_source_attribute" if metric not in {"same_tag_conflicting_groups", "taxonomy_tag_difference_groups"} else "conflict_or_semantic_competition",
                "description": descriptions[metric],
                "recommended_handling": "Mark ambiguous/missing in a future canonical selector; no value/order/size tie-break is supported." if metric != "taxonomy_tag_difference_groups" else "Do not merge tags. Require an approved semantic priority before any construction.",
            }
        )
    summary = {
        "same_tag_latest_group_count": denominator,
        "same_tag_conflicting_group_count": counters["same_tag_conflicting_groups"],
        "same_tag_conflicting_issuer_count": len(issuers["same_tag_conflicting_groups"]),
        "same_tag_conflict_share": counters["same_tag_conflicting_groups"] / denominator if denominator else None,
        "taxonomy_tag_difference_group_count": counters["taxonomy_tag_difference_groups"],
        "taxonomy_tag_difference_issuer_count": len(issuers["taxonomy_tag_difference_groups"]),
    }
    return rows, summary


def _signoff_rows() -> list[dict[str, Any]]:
    return [
        {
            "evidence_stage": EVIDENCE_STAGE,
            "proposed_rule": rule,
            "recommendation": recommendation,
            "reason": reason,
        }
        for rule, recommendation, reason in SIGNOFF_ROWS
    ]


def collect_design_review(root: Path | None = None) -> dict[str, Any]:
    """Perform read-only evidence collection for the remaining design questions."""

    selected_root = root or project_root()
    package = source_audit.package_root(selected_root)
    tag_inventory = _tag_inventory(package)
    flow_maps = _collect_flow_maps(package)
    gross_profit = _gross_profit_audit(flow_maps)
    fcf = _fcf_audit(flow_maps)
    instant_maps = _collect_instant_maps(package)
    leverage = _leverage_audit(instant_maps)
    foreign_forms = _foreign_form_audit(package)
    conflicts, conflict_summary = _conflict_audit(package)
    return {
        "evidence_stage": EVIDENCE_STAGE,
        "schema": REVIEW_VERSION,
        "tag_inventory": tag_inventory,
        "gross_profit": gross_profit,
        "fcf": fcf,
        "leverage": leverage,
        "foreign_forms": foreign_forms,
        "conflicts": conflicts,
        "conflict_summary": conflict_summary,
        "signoff": _signoff_rows(),
    }


def write_audit_outputs(audit: dict[str, Any], root: Path | None = None) -> dict[str, Path]:
    """Write only aggregated audit artefacts and an acceptance report."""

    selected_root = root or project_root()
    paths = output_paths(selected_root)
    _write_csv(
        paths["tag_inventory"],
        [
            "evidence_stage", "taxonomy", "xbrl_tag", "source_label", "source_description", "issuer_count",
            "issuer_coverage", "observation_count", "units", "forms", "period_classes", "value_signs",
            "first_end_date", "last_end_date",
        ],
        audit["tag_inventory"],
    )
    _write_csv(paths["gross_profit"], list(audit["gross_profit"][0].keys()), audit["gross_profit"])
    _write_csv(paths["fcf"], list(audit["fcf"][0].keys()), audit["fcf"])
    _write_csv(paths["leverage"], list(audit["leverage"][0].keys()), audit["leverage"])
    _write_csv(paths["foreign_forms"], list(audit["foreign_forms"][0].keys()), audit["foreign_forms"])
    _write_csv(paths["conflicts"], list(audit["conflicts"][0].keys()), audit["conflicts"])
    _write_csv(paths["signoff"], list(audit["signoff"][0].keys()), audit["signoff"])

    manifest = source_audit.audit_manifest(source_audit.package_root(selected_root))
    summary = {
        "schema": REVIEW_VERSION,
        "evidence_stage": EVIDENCE_STAGE,
        "post_scan_raw_manifest": manifest,
        "gross_profit_pairs": audit["gross_profit"],
        "fcf_input_audit": audit["fcf"],
        "leverage_definitions": audit["leverage"],
        "foreign_form_audit": audit["foreign_forms"],
        "same_filed_conflict_summary": audit["conflict_summary"],
        "signoff_recommendations": audit["signoff"],
        "prohibited_outputs_not_created": [
            "canonical PIT fundamental panel",
            "fundamental factor or ratio values",
            "FCF Yield or historical market capitalization",
            "preprocessing, IC, portfolio, FMOM, backtest, or strategy outputs",
        ],
    }
    with paths["audit_json"].open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")
    _write_acceptance_report(paths["acceptance"], summary)
    return paths


def _write_acceptance_report(path: Path, summary: dict[str, Any]) -> None:
    manifest = summary["post_scan_raw_manifest"]
    conflict = summary["same_filed_conflict_summary"]
    fcf = summary["fcf_input_audit"][0]
    text = f"""# SEC Fundamental Design Review — DEV Acceptance Report

- **Evidence stage:** `{EVIDENCE_STAGE}`
- **Decision:** PASS WITH WARNINGS — the source evidence supports the documented recommendations, but it does not itself freeze a Total Debt definition, Revenue/Cost fallback chain, foreign-form policy, or ambiguity policy.
- **Canonical PIT construction:** NOT YET AUTHORIZED automatically. It requires explicit human sign-off on the documented recommendations and remaining choices.

| Check | Status | Evidence / disposition |
| --- | --- | --- |
| Raw package integrity after design review | {'PASS' if manifest['passed'] else 'FAIL'} | Verified {manifest['verified_payload_files']}/{manifest['declared_payload_files']} payload files; failures={len(manifest['hash_failures']) + len(manifest['missing_files']) + len(manifest['unexpected_files'])}. |
| Gross Profit fallback evidence | WARN | Five named Revenue/Cost pairs were tested against exact GrossProfit only where the raw filing/period identity matched. Semantic approval remains pair-specific. |
| Leverage / Total Debt alternatives | WARN | Coverage and double-counting risks are documented, but the frozen manual does not choose the debt/lease scope. |
| OCF minus CapEx input compatibility | PASS WITH EXCEPTIONS | {fcf['compatible_ocf_capex_period_count']} matched source periods; {fcf['capex_negative_count']} matched negative CapEx observations remain exceptions and are not inverted. |
| 20-F / 40-F source impact | WARN | Measured by audited concept and taxonomy; no automatic exclusion/inclusion rule is inferred. |
| Latest same-filed conflicting values | WARN | {conflict['same_tag_conflicting_group_count']} / {conflict['same_tag_latest_group_count']} same-tag latest groups conflict; future construction must retain them as ambiguous/missing unless a supported deterministic rule is approved. |
| Prohibited outputs | PASS | No canonical panel, financial factors/ratios, FCF Yield, market cap, preprocessing, IC, portfolio, FMOM, backtest, or strategy result was written. |

## Remaining construction conditions

1. Select and approve the leverage numerator/lease scope; do not infer it from coverage.
2. Approve or reject each Gross Profit fallback pair only within its stated semantic scope.
3. Approve FCF treatment of the rare negative CapEx-source observations (the recommendation is exception/missing, never silent absolute-value conversion).
4. Approve 20-F/40-F use on the measured issuer/concept evidence.
5. Adopt the proposed safe rule that unresolved latest-same-filed conflicts are `ambiguous/missing`.
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_audit(root: Path | None = None) -> dict[str, Any]:
    audit = collect_design_review(root)
    paths = write_audit_outputs(audit, root)
    return {"audit": audit, "paths": {name: str(path) for name, path in paths.items()}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=project_root())
    args = parser.parse_args()
    result = run_audit(args.root)
    print(json.dumps(result["paths"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
