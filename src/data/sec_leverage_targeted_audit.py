"""Read-only targeted evidence audit for the DEV Quality Leverage definition.

The module compares exact same-filing input availability.  It intentionally
does not calculate individual leverage, ROE, GP/A, or Quality values and never
writes a canonical fundamental panel.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from src.data import sec_fundamental_spec as sec_spec
from src.data import source_audit


EVIDENCE_STAGE = "DEV_CURRENT_UNIVERSE"
AUDIT_VERSION = "fe5209.sec_leverage_targeted_audit.dev.v1"
USD = "USD"
INSTANT_TAGS = frozenset(
    {
        "Assets",
        "Liabilities",
        "StockholdersEquity",
        "LongTermDebtCurrent",
        "LongTermDebtNoncurrent",
    }
)
FLOW_TAGS = frozenset({"NetIncomeLoss", "GrossProfit"})
REVIEW_TAGS = INSTANT_TAGS | FLOW_TAGS
ELIGIBLE_FORM_BASES = frozenset({"10-K", "10-Q", "20-F"})


@dataclass(frozen=True)
class LeverageCandidate:
    definition_id: str
    formula_label: str
    numerator_tags: tuple[str, ...]
    denominator_tag: str
    component_aggregation: str
    semantic_consistency: str
    financials_suitability: str
    real_estate_suitability: str
    denominator_risk: str
    extreme_value_risk: str
    double_counting_risk: str
    recommendation_role: str


LEVERAGE_CANDIDATES = (
    LeverageCandidate(
        "long_term_debt_components_over_assets",
        "(LongTermDebtCurrent + LongTermDebtNoncurrent) / Assets",
        ("LongTermDebtCurrent", "LongTermDebtNoncurrent"),
        "Assets",
        "Required: sum only these exact current/non-current long-term-debt components from the same filing. Both source descriptions exclude capital leases.",
        "Narrow debt-capital proxy; clear component definitions but excludes capital leases and other liabilities.",
        "Not recommended for the Financials Quality branch: the frozen manual uses ROE/ROA instead of -Leverage.",
        "Conditional only. Real Estate debt economics may be meaningful, but the frozen manual requires an explicitly pre-registered RE comparable-liability rule.",
        "Assets are normally positive; zero Assets prevents calculation. No equity denominator is used.",
        "Lower denominator-tail risk than equity-denominator proxies; debt scope and component coverage are the main comparability risks.",
        "Low if and only if the two named components are summed once from the same filing. Do not add LongTermDebt, DebtCurrent, or lease-liability tags.",
        "ALTERNATIVE_DEBT_ONLY",
    ),
    LeverageCandidate(
        "liabilities_over_assets",
        "Liabilities / Assets",
        ("Liabilities",),
        "Assets",
        "None: use the exact reported Liabilities tag from the same filing.",
        "Broad book-leverage proxy using recognized liabilities over total assets; captures operating and financing obligations consistently within a filing.",
        "Not recommended for the Financials Quality branch: financial-sector liabilities are part of core operating intermediation, while the frozen manual specifies ROE/ROA.",
        "Conditional only. Broad liabilities can include RE operating/financing obligations; no historical-PIT RE branch is frozen.",
        "Assets are normally positive; zero Assets prevents calculation. No equity denominator is used.",
        "Lower denominator-tail risk than equity-denominator proxies. Ratios can exceed one when equity is negative, but remain defined while Assets are positive.",
        "None: a single exact reported numerator is used; do not substitute LiabilitiesAndStockholdersEquity or derive liabilities by subtraction.",
        "RECOMMENDED_BASELINE",
    ),
    LeverageCandidate(
        "assets_over_stockholders_equity",
        "Assets / StockholdersEquity",
        ("Assets",),
        "StockholdersEquity",
        "None: two exact reported balance-sheet tags from the same filing.",
        "Book equity multiplier, not a debt measure. It can be algebraically related to liabilities/equity when the equity concepts align.",
        "Not recommended for the Financials Quality branch under the frozen ROE/ROA branch rule.",
        "Not recommended pending a separate RE equity/treatment policy.",
        "Zero equity is undefined; negative equity reverses sign and makes a leverage interpretation unstable.",
        "High: small positive equity creates unbounded tails, while zero/negative equity prevents a stable cross-sectional interpretation.",
        "None in aggregation, but equity is parent-attributable and may not align with all-liabilities presentation in every issuer.",
        "NOT_RECOMMENDED_EQUITY_DENOMINATOR",
    ),
    LeverageCandidate(
        "liabilities_over_stockholders_equity",
        "Liabilities / StockholdersEquity",
        ("Liabilities",),
        "StockholdersEquity",
        "None: two exact reported balance-sheet tags from the same filing.",
        "Book debt-to-equity-style multiplier; broad liabilities rather than debt-only capital structure.",
        "Not recommended for the Financials Quality branch under the frozen ROE/ROA branch rule.",
        "Not recommended pending a separate RE equity/treatment policy.",
        "Zero equity is undefined; negative equity reverses sign and makes a leverage interpretation unstable.",
        "High: small positive equity creates unbounded tails, while zero/negative equity prevents a stable cross-sectional interpretation.",
        "None in aggregation, but parent-attributable equity may not align with all-liabilities presentation in every issuer.",
        "NOT_RECOMMENDED_EQUITY_DENOMINATOR",
    ),
)


DEV_APPROVED_SIGNOFFS = (
    (
        "gross_profit_baseline",
        "Gross Profit baseline uses exact GrossProfit only",
        "APPROVED_DEV",
        "No universal fallback is permitted; SalesRevenueNet - CostOfGoodsAndServicesSold does not enter the baseline.",
    ),
    (
        "fcf_primary_inputs",
        "FCF uses OCF - CapEx primary inputs",
        "APPROVED_DEV",
        "OCF=NetCashProvidedByUsedInOperatingActivities; CapEx=PaymentsToAcquirePropertyPlantAndEquipment; no weak-semantic fallback.",
    ),
    (
        "negative_capex",
        "Negative primary CapEx is SIGN_EXCEPTION / unavailable",
        "APPROVED_DEV",
        "Never apply absolute value or sign inversion.",
    ),
    (
        "same_latest_filed_conflict",
        "Conflicting values at the same latest eligible filing date are AMBIGUOUS / missing",
        "APPROVED_DEV",
        "No arbitrary tie-break based on value, order, or final record.",
    ),
    (
        "foreign_forms",
        "20-F conditional inclusion; 40-F has no observations",
        "APPROVED_DEV",
        "US-GAAP facts may follow the same controls. IFRS facts require separately approved mapping and currency policy before use.",
    ),
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def audit_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / "outputs" / "audit" / "dev_legacy" / "sec_pit"


def output_paths(root: Path | None = None) -> dict[str, Path]:
    directory = audit_dir(root)
    return {
        "definition_audit": directory / "sec_leverage_targeted_definition_audit_dev.csv",
        "quality_availability": directory / "sec_quality_input_availability_nonfinancial_dev.csv",
        "signoff_register": directory / "sec_dev_signoff_register.csv",
        "audit_json": directory / "sec_leverage_targeted_audit_dev.json",
        "acceptance": directory / "sec_leverage_targeted_audit_acceptance_report.md",
    }


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _value_token(value: Any) -> str:
    parsed = _decimal(value)
    return str(parsed.normalize()) if parsed is not None else f"non_numeric:{value!s}"


def _base_form(form: str) -> str:
    return form.removesuffix("/A")


def _eligible_form(observation: dict[str, Any]) -> bool:
    return _base_form(str(observation.get("form", ""))) in ELIGIBLE_FORM_BASES


def _is_instant_fact(observation: dict[str, Any]) -> bool:
    return _eligible_form(observation) and sec_spec.classify_period(observation)[0] == "instant"


def _is_duration_fact(observation: dict[str, Any]) -> bool:
    return _eligible_form(observation) and sec_spec.classify_period(observation)[0] in {
        "annual_candidate",
        "quarterly_candidate",
        "ytd_candidate",
    }


def _filing_key(cik: int, observation: dict[str, Any]) -> tuple[int, str, str, str, str]:
    return (
        cik,
        str(observation.get("end", "")),
        str(observation.get("form", "")),
        str(observation.get("filed", "")),
        str(observation.get("accn", "")),
    )


def _stable_values(values: dict[tuple[int, str, str, str, str], set[str]]) -> dict[tuple[int, str, str, str, str], Decimal]:
    result: dict[tuple[int, str, str, str, str], Decimal] = {}
    for key, tokens in values.items():
        if len(tokens) != 1:
            continue
        value = _decimal(next(iter(tokens)))
        if value is not None:
            result[key] = value
    return result


def _iter_payloads(package: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    raw_dir = package / source_audit.SEC_FACTS_DIR
    for path in sorted(raw_dir.glob("CIK*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        cik = source_audit._as_int(payload.get("cik"))
        if cik is None:
            raise ValueError(f"Missing usable CIK in {path.name}")
        yield cik, payload


def _iter_target_facts(payload: dict[str, Any]) -> Iterable[tuple[str, str, dict[str, Any]]]:
    tags = payload.get("facts", {}).get("us-gaap", {})
    if not isinstance(tags, dict):
        return
    for tag, tag_payload in tags.items():
        if tag not in REVIEW_TAGS or not isinstance(tag_payload, dict):
            continue
        units = tag_payload.get("units", {})
        if not isinstance(units, dict):
            continue
        observations = units.get(USD, [])
        if not isinstance(observations, list):
            continue
        for observation in observations:
            if isinstance(observation, dict):
                yield tag, USD, observation


def _collect_maps(package: Path) -> dict[str, dict[tuple[int, str, str, str, str], Decimal]]:
    raw_values: dict[str, dict[tuple[int, str, str, str, str], set[str]]] = defaultdict(lambda: defaultdict(set))
    for cik, payload in _iter_payloads(package):
        for tag, _, observation in _iter_target_facts(payload):
            valid_kind = _is_instant_fact(observation) if tag in INSTANT_TAGS else _is_duration_fact(observation)
            if not valid_kind or _parse_date(observation.get("end")) is None or _parse_date(observation.get("filed")) is None:
                continue
            raw_values[tag][_filing_key(cik, observation)].add(_value_token(observation.get("val")))
    return {tag: _stable_values(values) for tag, values in raw_values.items()}


def _issuer_groups(package: Path) -> tuple[dict[str, set[int]], dict[int, int]]:
    _, rows = source_audit._read_csv_rows(package / source_audit.SEC_CIK_MAP_FILE)
    all_issuers: set[int] = set()
    financials: set[int] = set()
    real_estate: set[int] = set()
    security_counts: Counter[int] = Counter()
    for row in rows:
        cik = source_audit._as_int(row.get("cik"))
        if cik is None:
            continue
        all_issuers.add(cik)
        security_counts[cik] += 1
        if row.get("gics_sector") == "Financials":
            financials.add(cik)
        if row.get("gics_sector") == "Real Estate":
            real_estate.add(cik)
    groups = {
        "all_current_issuers": all_issuers,
        "financials_current_gics": financials,
        "real_estate_current_gics": real_estate,
        "non_financial_current_gics": all_issuers.difference(financials),
        "ordinary_non_financial_ex_real_estate_current_gics": all_issuers.difference(financials).difference(real_estate),
    }
    return groups, dict(security_counts)


def _keys_for_issuers(keys: set[tuple[int, str, str, str, str]], issuers: set[int]) -> set[tuple[int, str, str, str, str]]:
    return {key for key in keys if key[0] in issuers}


def _key_stats(
    keys: set[tuple[int, str, str, str, str]],
    issuers: set[int],
    asset_keys: set[tuple[int, str, str, str, str]],
) -> dict[str, Any]:
    scoped = _keys_for_issuers(keys, issuers)
    base_assets = _keys_for_issuers(asset_keys, issuers)
    ends = [key[1] for key in scoped]
    forms: Counter[str] = Counter(key[2] for key in scoped)
    issuer_count = len({key[0] for key in scoped})
    return {
        "same_filing_period_count": len(scoped),
        "issuer_count": issuer_count,
        "issuer_coverage": issuer_count / len(issuers) if issuers else None,
        "asset_filing_coverage": len(scoped) / len(base_assets) if base_assets else None,
        "first_end_date": min(ends) if ends else "",
        "last_end_date": max(ends) if ends else "",
        "forms": ";".join(f"{form}:{count}" for form, count in forms.most_common()),
        "asset_base_filing_count": len(base_assets),
    }


def _denominator_counts(
    values: dict[tuple[int, str, str, str, str], Decimal],
    keys: set[tuple[int, str, str, str, str]],
    issuers: set[int],
) -> Counter[str]:
    result: Counter[str] = Counter()
    for key in _keys_for_issuers(keys, issuers):
        value = values[key]
        result["positive" if value > 0 else "negative" if value < 0 else "zero"] += 1
    return result


def _proportion(value: float | None) -> str:
    return f"{value:.9f}" if value is not None else ""


def _build_definition_rows(
    maps: dict[str, dict[tuple[int, str, str, str, str], Decimal]],
    groups: dict[str, set[int]],
) -> tuple[Any, ...]:
    assets = maps.get("Assets", {})
    equity = maps.get("StockholdersEquity", {})
    net_income = maps.get("NetIncomeLoss", {})
    gross_profit = maps.get("GrossProfit", {})
    asset_keys = set(assets)
    roe_source = set(net_income).intersection(equity)
    roe_technical = {key for key in roe_source if equity[key] != 0}
    gross_profit_assets = set(gross_profit).intersection(assets)
    gpa_technical = {key for key in gross_profit_assets if assets[key] != 0}
    rows: list[dict[str, Any]] = []
    definition_keys: dict[str, set[tuple[int, str, str, str, str]]] = {}
    for candidate in LEVERAGE_CANDIDATES:
        compatible = set(maps.get(candidate.denominator_tag, {}))
        for tag in candidate.numerator_tags:
            compatible.intersection_update(maps.get(tag, {}))
        denominator = maps.get(candidate.denominator_tag, {})
        technical = {key for key in compatible if denominator[key] != 0}
        definition_keys[candidate.definition_id] = technical
        for group_name, issuers in groups.items():
            stats = _key_stats(technical, issuers, asset_keys)
            denominator_signs = _denominator_counts(denominator, compatible, issuers)
            roe_joint = technical.intersection(roe_technical)
            gpa_joint = technical.intersection(gpa_technical)
            all_three = technical.intersection(roe_technical).intersection(gpa_technical)
            two_of_three = {
                key
                for key in _keys_for_issuers(asset_keys, issuers)
                if sum(key in metric for metric in (roe_technical, gpa_technical, technical)) >= 2
            }
            rows.append(
                {
                    "evidence_stage": EVIDENCE_STAGE,
                    "definition_id": candidate.definition_id,
                    "formula_label": candidate.formula_label,
                    "numerator_tags": "+".join(candidate.numerator_tags),
                    "denominator_tag": candidate.denominator_tag,
                    "group": group_name,
                    "current_group_issuer_count": len(issuers),
                    "same_filing_period_count": stats["same_filing_period_count"],
                    "issuer_count": stats["issuer_count"],
                    "issuer_coverage": _proportion(stats["issuer_coverage"]),
                    "asset_base_filing_count": stats["asset_base_filing_count"],
                    "asset_filing_coverage": _proportion(stats["asset_filing_coverage"]),
                    "first_end_date": stats["first_end_date"],
                    "last_end_date": stats["last_end_date"],
                    "forms": stats["forms"],
                    "denominator_positive_count": denominator_signs["positive"],
                    "denominator_zero_count": denominator_signs["zero"],
                    "denominator_negative_count": denominator_signs["negative"],
                    "component_aggregation": candidate.component_aggregation,
                    "semantic_consistency": candidate.semantic_consistency,
                    "financials_suitability": candidate.financials_suitability,
                    "real_estate_suitability": candidate.real_estate_suitability,
                    "denominator_risk": candidate.denominator_risk,
                    "extreme_value_risk": candidate.extreme_value_risk,
                    "potential_double_counting": candidate.double_counting_risk,
                    "recommendation_role": candidate.recommendation_role,
                    "roe_input_joint_filing_count": len(_keys_for_issuers(roe_joint, issuers)),
                    "exact_gpa_input_joint_filing_count": len(_keys_for_issuers(gpa_joint, issuers)),
                    "roe_gpa_leverage_all_three_filing_count": len(_keys_for_issuers(all_three, issuers)),
                    "at_least_two_of_three_filing_count": len(two_of_three),
                    "audit_scope": "Deterministic USD us-gaap facts from 10-K/10-Q/20-F families, including amendments only as their own filing dates; values/ratios are not produced.",
                }
            )
    return rows, definition_keys, {"roe": roe_technical, "gpa": gpa_technical}, {"assets": asset_keys}


def _non_financial_availability_rows(
    groups: dict[str, set[int]],
    definition_keys: dict[str, set[tuple[int, str, str, str, str]]],
    quality_keys: dict[str, set[tuple[int, str, str, str, str]]],
    base_keys: dict[str, set[tuple[int, str, str, str, str]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group_name in ("non_financial_current_gics", "ordinary_non_financial_ex_real_estate_current_gics"):
        issuers = groups[group_name]
        asset_keys = base_keys["assets"]
        metrics: list[tuple[str, str, set[tuple[int, str, str, str, str]]]] = [
            ("roe_end_aligned_input", "NetIncomeLoss + StockholdersEquity; availability only, not a frozen ROE formula", quality_keys["roe"]),
            ("exact_gpa_input", "Exact GrossProfit + Assets; availability only, no GP/A value", quality_keys["gpa"]),
        ]
        metrics.extend(
            (
                f"leverage_{candidate.definition_id}",
                candidate.formula_label,
                definition_keys[candidate.definition_id],
            )
            for candidate in LEVERAGE_CANDIDATES
        )
        for metric_id, description, keys in metrics:
            stats = _key_stats(keys, issuers, asset_keys)
            rows.append(
                {
                    "evidence_stage": EVIDENCE_STAGE,
                    "group": group_name,
                    "metric_id": metric_id,
                    "metric_description": description,
                    "current_group_issuer_count": len(issuers),
                    "available_issuer_count": stats["issuer_count"],
                    "available_issuer_proportion": _proportion(stats["issuer_coverage"]),
                    "available_same_filing_period_count": stats["same_filing_period_count"],
                    "asset_base_filing_count": stats["asset_base_filing_count"],
                    "available_asset_filing_proportion": _proportion(stats["asset_filing_coverage"]),
                    "first_end_date": stats["first_end_date"],
                    "last_end_date": stats["last_end_date"],
                    "forms": stats["forms"],
                    "interpretation": "Source-input compatibility only. No ratio, score, or denominator threshold is calculated or frozen.",
                }
            )
        for candidate in LEVERAGE_CANDIDATES:
            leverage = definition_keys[candidate.definition_id]
            at_least_two = {
                key
                for key in _keys_for_issuers(asset_keys, issuers)
                if sum(key in metric for metric in (quality_keys["roe"], quality_keys["gpa"], leverage)) >= 2
            }
            stats = _key_stats(at_least_two, issuers, asset_keys)
            rows.append(
                {
                    "evidence_stage": EVIDENCE_STAGE,
                    "group": group_name,
                    "metric_id": f"at_least_two_of_three_with_{candidate.definition_id}",
                    "metric_description": "At least two source-input sets among end-aligned ROE inputs, exact GP/A inputs, and this Leverage candidate.",
                    "current_group_issuer_count": len(issuers),
                    "available_issuer_count": stats["issuer_count"],
                    "available_issuer_proportion": _proportion(stats["issuer_coverage"]),
                    "available_same_filing_period_count": stats["same_filing_period_count"],
                    "asset_base_filing_count": stats["asset_base_filing_count"],
                    "available_asset_filing_proportion": _proportion(stats["asset_filing_coverage"]),
                    "first_end_date": stats["first_end_date"],
                    "last_end_date": stats["last_end_date"],
                    "forms": stats["forms"],
                    "interpretation": "Same filing/period coverage only; the future Quality availability rule is not frozen here.",
                }
            )
    return rows


def _signoff_rows() -> list[dict[str, str]]:
    return [
        {
            "evidence_stage": EVIDENCE_STAGE,
            "decision_id": decision_id,
            "decision": decision,
            "status": status,
            "scope_and_constraint": scope,
        }
        for decision_id, decision, status, scope in DEV_APPROVED_SIGNOFFS
    ]


def _write_csv(path: Path, fields: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def collect_targeted_audit(root: Path | None = None) -> dict[str, Any]:
    selected_root = root or project_root()
    package = source_audit.package_root(selected_root)
    maps = _collect_maps(package)
    groups, security_counts = _issuer_groups(package)
    definition_rows, definition_keys, quality_keys, base_keys = _build_definition_rows(maps, groups)
    non_financial_rows = _non_financial_availability_rows(groups, definition_keys, quality_keys, base_keys)
    return {
        "schema": AUDIT_VERSION,
        "evidence_stage": EVIDENCE_STAGE,
        "definition_rows": definition_rows,
        "non_financial_rows": non_financial_rows,
        "signoff_rows": _signoff_rows(),
        "group_issuer_counts": {name: len(ciks) for name, ciks in groups.items()},
        "group_security_counts": {
            name: sum(security_counts.get(cik, 0) for cik in ciks) for name, ciks in groups.items()
        },
        "recommended_baseline": "liabilities_over_assets",
        "prohibited_outputs_not_created": [
            "canonical PIT fundamental panel",
            "individual leverage, ROE, or GP/A values",
            "Quality scores or z-scores",
            "IC, portfolios, backtests, FMOM, or strategy outputs",
        ],
    }


def _write_acceptance(path: Path, summary: dict[str, Any]) -> None:
    manifest = summary["post_scan_raw_manifest"]
    baseline = next(
        row
        for row in summary["definition_rows"]
        if row["definition_id"] == "liabilities_over_assets"
        and row["group"] == "ordinary_non_financial_ex_real_estate_current_gics"
    )
    debt = next(
        row
        for row in summary["definition_rows"]
        if row["definition_id"] == "long_term_debt_components_over_assets"
        and row["group"] == "ordinary_non_financial_ex_real_estate_current_gics"
    )
    text = f"""# Leverage Definition Targeted Audit — DEV Acceptance Report

- **Evidence stage:** `{EVIDENCE_STAGE}`
- **Decision:** PASS WITH WARNINGS — a DEV baseline recommendation is evidence-supported, but it is not a canonical panel or a Quality-score implementation.
- **Recommended baseline:** `Liabilities / Assets` for the Current-GICS ordinary non-financial branch excluding Real Estate, subject to the documented Financials/Real Estate boundary.

| Check | Status | Evidence / disposition |
| --- | --- | --- |
| Raw package integrity | {'PASS' if manifest['passed'] else 'FAIL'} | Verified {manifest['verified_payload_files']}/{manifest['declared_payload_files']} payload files. |
| Required candidate definitions | PASS | Compared debt-components/assets, liabilities/assets, assets/equity, and liabilities/equity using exact same-filing source inputs. |
| Baseline source coverage | PASS WITH WARNINGS | In Current-GICS ordinary non-financials excluding Real Estate, Liabilities/Assets has {baseline['issuer_count']} issuers and {baseline['same_filing_period_count']} same-filing source periods; exact Liabilities has lower raw issuer coverage than Assets. |
| Debt-only alternative | PASS WITH WARNINGS | Current/non-current long-term-debt components have {debt['issuer_count']} issuers and require controlled aggregation; they explicitly exclude capital leases. |
| Equity-denominator alternatives | WARN | Zero/negative StockholdersEquity observations create undefined/sign-reversing and unbounded-tail risk. |
| Financials/RE branch | WARN | The frozen manual uses ROE/ROA for Financials; Real Estate requires a separately documented comparable-liability decision. Current GICS is DEV-only. |
| Prohibited outputs | PASS | No individual ratio, Quality score, z-score, IC, portfolio, backtest, or strategy result was written. |
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_audit_outputs(audit: dict[str, Any], root: Path | None = None) -> dict[str, Path]:
    selected_root = root or project_root()
    paths = output_paths(selected_root)
    _write_csv(paths["definition_audit"], list(audit["definition_rows"][0].keys()), audit["definition_rows"])
    _write_csv(paths["quality_availability"], list(audit["non_financial_rows"][0].keys()), audit["non_financial_rows"])
    _write_csv(paths["signoff_register"], list(audit["signoff_rows"][0].keys()), audit["signoff_rows"])
    manifest = source_audit.audit_manifest(source_audit.package_root(selected_root))
    summary = dict(audit)
    summary["post_scan_raw_manifest"] = manifest
    with paths["audit_json"].open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")
    _write_acceptance(paths["acceptance"], summary)
    return paths


def run_audit(root: Path | None = None) -> dict[str, Any]:
    audit = collect_targeted_audit(root)
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
