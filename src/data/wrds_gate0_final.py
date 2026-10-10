"""Formal Gate 0 integrated final sign-off for the WRDS rebuild.

This module is deliberately an evidence-and-contract audit.  It reads the
immutable WRDS package and prior Gate 0 outputs, writes only final audit
contracts, and never creates a canonical panel, market cap, factor, IC,
portfolio, or backtest.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from src.data import wrds_gate0_phase1 as phase1


EVIDENCE_STAGE = "FORMAL_GATE0_INTEGRATED_FINAL_SIGNOFF"
FORMAL_CUTOFF = "2025-12-31"
AUDIT = Path("outputs/audit/wrds_formal")
RAW = Path("wrds_raw/US_WRDS_2026-10-09")

ISSUE_REGISTER = AUDIT / "gate0_final_issue_register.csv"
ISSUER_LIMITATIONS = AUDIT / "gate0_final_issuer_limitation_assessment.csv"
UNITS_CONTRACT = AUDIT / "gate0_final_numeric_units_contract.csv"
CROSS_SOURCE_ALIGNMENT = AUDIT / "gate0_final_cross_source_scale_alignment.csv"
UNITS_EVIDENCE = AUDIT / "gate0_units_authoritative_evidence.csv"
CONFLICT_POLICY = AUDIT / "gate0_final_compustat_conflict_policy.csv"
PIT_CONTRACT = AUDIT / "gate0_final_pit_availability_contract.csv"
CANONICAL_CONTRACT = AUDIT / "gate0_final_canonical_construction_contract.csv"
ACCEPTANCE = AUDIT / "gate0_final_acceptance.csv"
REPORT = AUDIT / "WRDS_GATE0_FINAL_SIGNOFF.md"
OPEN_ISSUES = AUDIT / "gate0_open_issues.csv"

PRIMARY_CATEGORIES = {
    "NON_BLOCKING_DISCLOSURE",
    "CANONICAL_RULE_REQUIRED",
    "GATE0_BLOCKER",
    "RESOLVED",
}

MANDATORY_CHECKS = (
    "RAW_INTEGRITY",
    "UNIVERSE_HISTORY",
    "CCM_IDENTIFIER_MAPPING",
    "HISTORICAL_GICS",
    "CRSP_RETURN_SEMANTICS",
    "DELISTING_HANDLING",
    "CORPORATE_ACTION_HANDLING",
    "SECURITY_MARKET_CAP",
    "ISSUER_SHARE_CLASS_COVERAGE",
    "SHARES_OUTSTANDING",
    "MARKET_INDEX_SEMANTICS",
    "QUARTERLY_RDQ_TIMING",
    "ANNUAL_INFORMATION_AVAILABILITY",
    "COMPUSTAT_REVISION_CLAIM",
    "QUARTERLY_FLOW_SEMANTICS",
    "TTM_CAPABILITY",
    "COMPUSTAT_DUPLICATE_POLICY",
    "NUMERIC_UNIT_ALIGNMENT",
    "FORMAL_SAMPLE_CUTOFF",
    "NO_FUTURE_INFORMATION",
    "CANONICAL_CONSTRUCTION_DETERMINISM",
)

# These are the only Compustat fields whose unit evidence is newly resolved by
# the authorized WRDS Data Dictionary item-detail evidence.  The keys match
# both the immutable raw schemas and the frozen Phase-2C field contract.
FIELD_LEVEL_UNIT_SPECS: dict[tuple[str, str], dict[str, str]] = {
    ("ni", "ANNUAL"): {"evidence_id": "WRDS_COMPUSTAT_NI_ANNUAL_MILLIONS", "item": "NI", "periodicity": "Annual (North America)", "category": "Income Statement"},
    ("niq", "QUARTERLY"): {"evidence_id": "WRDS_COMPUSTAT_NIQ_QUARTERLY_MILLIONS", "item": "NI", "periodicity": "Quarterly (North America)", "category": "Income Statement"},
    ("ceq", "ANNUAL"): {"evidence_id": "WRDS_COMPUSTAT_CEQ_ANNUAL_MILLIONS", "item": "CEQ", "periodicity": "Annual", "category": "Balance Sheet"},
    ("ceqq", "QUARTERLY"): {"evidence_id": "WRDS_COMPUSTAT_CEQQ_QUARTERLY_MILLIONS", "item": "CEQ", "periodicity": "Quarterly", "category": "Balance Sheet"},
    ("oancf", "ANNUAL"): {"evidence_id": "WRDS_COMPUSTAT_OANCF_ANNUAL_MILLIONS", "item": "OANCF", "periodicity": "Annual", "category": "Cash Flow"},
    ("oancfy", "QUARTERLY"): {"evidence_id": "WRDS_COMPUSTAT_OANCFY_YTD_MILLIONS", "item": "OANCF", "periodicity": "Year-to-Date", "category": "Cash Flow"},
    ("capx", "ANNUAL"): {"evidence_id": "WRDS_COMPUSTAT_CAPX_ANNUAL_MILLIONS", "item": "CAPX", "periodicity": "Annual", "category": "Cash Flow"},
    ("capxy", "QUARTERLY"): {"evidence_id": "WRDS_COMPUSTAT_CAPXY_YTD_MILLIONS", "item": "CAPX", "periodicity": "Year-to-Date", "category": "Cash Flow"},
}

REQUIRED_CROSS_SOURCE_UNIT_FIELDS = frozenset(FIELD_LEVEL_UNIT_SPECS)


def project_root() -> Path:
    """Return the repository root without depending on the shell working directory."""

    return Path(__file__).resolve().parents[2]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(root: Path, relative: Path, columns: tuple[str, ...], rows: Iterable[dict[str, Any]]) -> None:
    phase1._write_csv(root / relative, columns, list(rows))


def _clean(value: object) -> str:
    return str(value or "").strip()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _bool(value: bool) -> str:
    return str(value).lower()


def _gzip_rows(path: Path) -> Iterable[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def _required_prior_outputs() -> tuple[Path, ...]:
    return (
        AUDIT / "raw_file_manifest.csv",
        AUDIT / "gate0_phase1_acceptance.csv",
        AUDIT / "gate0_phase2a_acceptance.csv",
        AUDIT / "gate0_phase2b_acceptance.csv",
        AUDIT / "gate0_phase2b2_acceptance.csv",
        AUDIT / "gate0_phase2c_acceptance.csv",
        AUDIT / "gate0_phase2a_ccm_exceptions.csv",
        AUDIT / "gate0_phase2a_historical_gics_exceptions.csv",
        AUDIT / "gate0_phase2b2_issuer_coverage_sufficiency.csv",
        AUDIT / "gate0_phase2c_duplicate_conflict_audit.csv",
        AUDIT / "gate0_phase2c_annual_availability_audit.csv",
        AUDIT / "gate0_phase2c_rdq_coverage.csv",
        AUDIT / "gate0_phase2c_fundamental_field_contract.csv",
        AUDIT / "gate0_open_issues.csv",
    )


def verify_prior_evidence(root: Path) -> dict[str, Any]:
    """Confirm all prior formal Gate 0 evidence remains available."""

    missing = [item.as_posix() for item in _required_prior_outputs() if not (root / item).is_file()]
    return {"prior_outputs_present": not missing, "missing_prior_outputs": missing}


def verify_raw_integrity(root: Path) -> dict[str, Any]:
    """Rehash the 16 original and three approved supplemental raw files."""

    expected: list[tuple[Path, int, str]] = []
    for row in _read_csv(root / AUDIT / "raw_file_manifest.csv"):
        expected.append((RAW / row["relative_path"], int(row["size_bytes"]), row["sha256"]))
    for row in _read_csv(root / AUDIT / "gate0_phase2b2_new_raw_manifest.csv"):
        expected.append((Path(row["relative_path"]), int(row["file_size_bytes"]), row["sha256_post_audit"]))

    failures: list[str] = []
    for relative, expected_size, expected_hash in expected:
        path = root / relative
        if not path.is_file():
            failures.append(f"MISSING:{relative.as_posix()}")
            continue
        actual_size = path.stat().st_size
        actual_hash = phase1.sha256_file(path)
        if actual_size != expected_size or actual_hash != expected_hash:
            failures.append(f"MISMATCH:{relative.as_posix()}")
    return {
        "expected_file_count": len(expected),
        "original_file_count": 16,
        "supplemental_file_count": 3,
        "raw_hashes_match": not failures,
        "raw_failures": failures,
    }


def _parse_json_list(value: str) -> list[str]:
    parsed = json.loads(value) if _clean(value) else []
    return [str(item) for item in parsed]


def _issuer_limitation_rows(root: Path) -> list[dict[str, str]]:
    """Classify each Phase 2B.2 issuer limitation without constructing cap."""

    issuer_rows = _read_csv(root / AUDIT / "gate0_phase2b2_issuer_coverage_sufficiency.csv")
    daily_coverage = {
        row["permno"]: row
        for row in _read_csv(root / AUDIT / "gate0_phase2b2_daily_security_coverage.csv")
    }
    limited_permnos = {
        permno
        for row in issuer_rows
        if row["issuer_coverage_classification"] == "ISSUER_COVERAGE_COMPLETE_WITH_LIMITATION"
        for permno in set(_parse_json_list(row["daily_market_data_gap_permnos"]))
        | set(_parse_json_list(row["share_history_gap_permnos"]))
    }
    missing_by_permno: dict[str, set[str]] = defaultdict(set)
    daily_path = root / RAW / "crsp/CRSP_Supplemental_Issuer_DailySecurityData_2004-01-01_2025-12-31.csv.gz"
    for row in _gzip_rows(daily_path):
        permno = _clean(row.get("PERMNO"))
        if permno in limited_permnos and (not _clean(row.get("DlyCap")) or not _clean(row.get("ShrOut"))):
            missing_by_permno[permno].add(_clean(row.get("DlyCalDt")))

    result: list[dict[str, str]] = []
    for issuer in sorted(issuer_rows, key=lambda item: int(item["permco"])):
        if issuer["issuer_coverage_classification"] != "ISSUER_COVERAGE_COMPLETE_WITH_LIMITATION":
            continue
        permnos = sorted(
            set(_parse_json_list(issuer["daily_market_data_gap_permnos"]))
            | set(_parse_json_list(issuer["share_history_gap_permnos"])),
            key=int,
        )
        active_gap_dates: dict[str, list[str]] = {}
        inactive_only: dict[str, list[str]] = {}
        for permno in permnos:
            end = daily_coverage[permno]["effective_end"]
            dates = sorted(missing_by_permno[permno])
            active = [item for item in dates if item <= end]
            if active:
                active_gap_dates[permno] = active
            else:
                inactive_only[permno] = dates
        if active_gap_dates:
            limitation = "B_PERIOD_SPECIFIC_ACTIVE_CLASS_DLYCAP_MISSING"
            affected = _json(active_gap_dates)
            effect = "Relevant active common-equity class lacks DlyCap on listed date(s); issuer denominator is unavailable on those issuer-date observations."
            action = "Set issuer_market_cap unavailable on the listed active-class dates; do not impute, substitute, or omit the class from the issuer sum."
        else:
            limitation = "A_HARMLESS_POST_EFFECTIVE_END_DIAGNOSTIC"
            affected = _json(inactive_only)
            effect = "Every DlyCap/ShrOut gap occurs after the class effective-end date, when the class is not eligible for the issuer sum."
            action = "Apply active security-information intervals first; exclude only the post-effective-end raw rows as inactive. No active issuer-date is labelled complete by inference."
        result.append(
            {
                "PERMCO": issuer["permco"],
                "GVKEY": issuer["associated_gvkeys"],
                "coverage_class": issuer["issuer_coverage_classification"],
                "limitation": limitation,
                "affected_period": affected,
                "effect_on_issuer_market_cap": effect,
                "canonical_action": action,
                "gate0_blocking": "false",
                "evidence_reference": "gate0_phase2b2_issuer_coverage_sufficiency.csv; gate0_phase2b2_daily_security_coverage.csv; approved supplemental daily raw",
            }
        )
    return result


def _compustat_conflict_profiles(root: Path) -> dict[str, dict[str, Any]]:
    """Profile actual record dimensions and standard-row residual conflicts."""

    definitions = {
        "ANNUAL": (RAW / "compustat/Compustat_NA_Fundamentals_Annual_2004-01_2026-10.csv.gz", "curcd"),
        "QUARTERLY": (RAW / "compustat/Compustat_NA_Fundamentals_Quarterly_2004-01_2026-10.csv.gz", "curcdq"),
    }
    result: dict[str, dict[str, Any]] = {}
    for dataset, (relative, currency) in definitions.items():
        dimensions = ("indfmt", "datafmt", "consol", currency)
        groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        distributions = {field: Counter() for field in dimensions}
        for row in _gzip_rows(root / relative):
            for field in dimensions:
                distributions[field][_clean(row.get(field))] += 1
            groups[(_clean(row.get("gvkey")), _clean(row.get("datadate")))].append(row)
        duplicate_groups = {key: rows for key, rows in groups.items() if len(rows) > 1}
        selected_counts: Counter[str] = Counter()
        rdq_conflicts = 0
        for rows in duplicate_groups.values():
            selected = [
                row
                for row in rows
                if _clean(row.get("indfmt")) == "INDL"
                and _clean(row.get("datafmt")) == "STD"
                and _clean(row.get("consol")) == "C"
                and _clean(row.get(currency)) == "USD"
            ]
            selected_counts[str(len(selected))] += 1
            if dataset == "QUARTERLY" and len({_clean(row.get("rdq")) for row in rows}) > 1:
                rdq_conflicts += 1
        result[dataset] = {
            "dimensions": dimensions,
            "distributions": {field: dict(sorted(values.items())) for field, values in distributions.items()},
            "duplicate_groups": len(duplicate_groups),
            "selected_one": selected_counts["1"],
            "selected_none": selected_counts["0"],
            "selected_multiple": sum(count for count, value in selected_counts.items() if int(count) > 1 for count in [value]),
            "rdq_conflicts": rdq_conflicts,
        }
    return result


def _conflict_policy_rows(profiles: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    annual, quarterly = profiles["ANNUAL"], profiles["QUARTERLY"]
    return [
        {
            "conflict_type": "ANNUAL_GVKEY_DATADATE",
            "record_dimensions": "indfmt,datafmt,consol,curcd; popsrc is not delivered in this raw schema",
            "count": str(annual["duplicate_groups"]),
            "deterministic_resolution_available": "true",
            "resolution_rule": "Use only indfmt=INDL, datafmt=STD, consol=C, curcd=USD. Retain a key only when exactly one row remains after this predicate.",
            "fallback_if_unresolved": "No eligible row or more than one eligible row => fundamental observation unavailable.",
            "gate0_blocking": "false",
            "evidence_reference": "Delivered annual raw dimension profile; WRDS QUARTERIZE identifies datafmt/indfmt/consol as record dimensions.",
            "notes": f"selected_one={annual['selected_one']}; selected_none={annual['selected_none']}; selected_multiple={annual['selected_multiple']}; distributions={_json(annual['distributions'])}",
        },
        {
            "conflict_type": "QUARTERLY_GVKEY_DATADATE",
            "record_dimensions": "indfmt,datafmt,consol,curcdq; popsrc is not delivered in this raw schema",
            "count": str(quarterly["duplicate_groups"]),
            "deterministic_resolution_available": "true",
            "resolution_rule": "Use only indfmt=INDL, datafmt=STD, consol=C, curcdq=USD. Retain a key only when exactly one row remains after this predicate.",
            "fallback_if_unresolved": "No eligible row or more than one eligible row => quarterly observation unavailable.",
            "gate0_blocking": "false",
            "evidence_reference": "Delivered quarterly raw dimension profile; WRDS QUARTERIZE identifies datafmt/indfmt/consol as record dimensions.",
            "notes": f"selected_one={quarterly['selected_one']}; selected_none={quarterly['selected_none']}; selected_multiple={quarterly['selected_multiple']}; distributions={_json(quarterly['distributions'])}",
        },
        {
            "conflict_type": "QUARTERLY_CONFLICTING_RDQ",
            "record_dimensions": "GVKEY,datadate plus the delivered quarterly standard-row predicate",
            "count": str(quarterly["rdq_conflicts"]),
            "deterministic_resolution_available": "true",
            "resolution_rule": "Apply the standard-row predicate; if exactly one eligible row remains, use its RDQ only from the next local trading day.",
            "fallback_if_unresolved": "More than one eligible RDQ or no eligible RDQ => observation unavailable; do not choose an RDQ by dataframe order.",
            "gate0_blocking": "false",
            "evidence_reference": "gate0_phase2c_duplicate_conflict_audit.csv and delivered quarterly raw profile.",
            "notes": "A date-only RDQ does not permit same-day use.",
        },
    ]


def _field_level_unit_evidence_rows() -> list[dict[str, str]]:
    """Return the authorized, exact WRDS Data Dictionary unit evidence.

    The authenticated item-detail pages were supplied as authoritative
    field-level evidence for this re-evaluation.  This code does not access a
    WRDS session or retain protected documentation; it records the precise
    product, schema, table, mnemonic, periodicity, format, and numeric unit.
    """

    rows: list[dict[str, str]] = []
    for (raw_field, frequency), spec in sorted(FIELD_LEVEL_UNIT_SPECS.items()):
        rows.append(
            {
                "evidence_id": spec["evidence_id"],
                "source_organization": "WRDS / Compustat North America",
                "document_title": f"WRDS Compustat North America Data Dictionary — {spec['item']} item detail",
                "document_type": "Official WRDS authenticated Data Dictionary item detail",
                "source_url_or_local_path": f"WRDS authenticated Data Dictionary: comp_na_daily_all.funda / {spec['item']} item-detail page (authoritative field-level evidence received 2026-10-10)",
                "access_date": "2026-10-10",
                "relevant_section": f"Product: Compustat North America daily updates; schema: comp_na_daily_all; table: funda; item: {spec['item']}; periodicity: {spec['periodicity']}; format: Number; units: Millions.",
                "exact_field_or_scope": f"Delivered raw {frequency.lower()} field `{raw_field}`; exact mnemonic identity is confirmed against the immutable raw schema and frozen Phase-2C field contract.",
                "unit_statement": f"{raw_field.upper()}: Category {spec['category']}; Periodicity {spec['periodicity']}; Format Number; Units Millions.",
                "applies_to_annual": "YES" if frequency == "ANNUAL" else "NO",
                "applies_to_quarterly": "YES" if frequency == "QUARTERLY" else "NO",
                "applies_to_currency_fields": "YES_FOR_MONETARY_ITEM",
                "applies_to_share_fields": "NOT_APPLICABLE",
                "interpretation": "Field-specific numeric scale is Millions. The frozen USD record predicate establishes denomination separately; valid cross-source alignment converts USD millions to CRSP USD thousands by multiplying by 1000.",
                "confidence": "RESOLVED_AUTHORITATIVELY",
                "notes": "No value, factor, IC, portfolio, or market-cap object is constructed by this evidence audit.",
            }
        )
    return rows


def _unit_evidence_rows() -> list[dict[str, str]]:
    """Record historical generic evidence plus exact newly authorized evidence."""

    return [
        {
            "evidence_id": "WRDS_COMPUSTAT_GENERIC_MILLIONS",
            "source_organization": "Wharton Research Data Services",
            "document_title": "WRDS conditional-query support (Compustat demo)",
            "document_type": "Official WRDS web help",
            "source_url_or_local_path": "https://wrds-www.wharton.upenn.edu/demo/step_support/conditional/",
            "access_date": "2026-10-10",
            "relevant_section": "Conditional statements; examples AT and SALE",
            "exact_field_or_scope": "The page states only that most data items are represented in units of millions; it does not enumerate this extract's fields or distinguish annual from quarterly.",
            "unit_statement": "Most data items are represented in units of millions.",
            "applies_to_annual": "NOT_ESTABLISHED",
            "applies_to_quarterly": "NOT_ESTABLISHED",
            "applies_to_currency_fields": "NOT_FIELD_SPECIFIC",
            "applies_to_share_fields": "NOT_ESTABLISHED",
            "interpretation": "Official but insufficient: 'most' cannot establish an exact scale for NI/NIQ, CEQ/CEQQ, OANCF/OANCFY, CAPX/CAPXY, dividend fields, or shares.",
            "confidence": "OFFICIAL_BUT_INSUFFICIENT_SCOPE",
            "notes": "This record cannot authorize a million-to-thousand conversion.",
        },
        {
            "evidence_id": "SPG_COMPUSTAT_DICTIONARY_LOCATION",
            "source_organization": "S&P Global Market Intelligence",
            "document_title": "Extract: Getting Started Overview",
            "document_type": "Official S&P Global PDF",
            "source_url_or_local_path": "https://www.support.marketplace.spglobal.com/content/dam/spglobal/mi/en/documents/marketplace/extract/extract-getting-started-202207.pdf",
            "access_date": "2026-10-10",
            "relevant_section": "Pages 3 and 7: Dictionary_Compustat.xlsx and finding item mnemonics/descriptions",
            "exact_field_or_scope": "Provider guidance identifies the Compustat dictionary as the location for file groups, columns, descriptions, and mnemonics; the dictionary itself is not delivered in this repository.",
            "unit_statement": "No item-scale statement is reproduced in the public getting-started guide.",
            "applies_to_annual": "NOT_ESTABLISHED",
            "applies_to_quarterly": "NOT_ESTABLISHED",
            "applies_to_currency_fields": "NOT_ESTABLISHED",
            "applies_to_share_fields": "NOT_ESTABLISHED",
            "interpretation": "Supports the requirement for a provider dictionary but supplies no conversion factor.",
            "confidence": "OFFICIAL_METADATA_LOCATION_ONLY",
            "notes": "No proprietary dictionary was downloaded or committed.",
        },
        {
            "evidence_id": "WRDS_COMPUSTAT_DICTIONARY_ACCESS_BOUNDARY",
            "source_organization": "Wharton Research Data Services",
            "document_title": "Compustat North America data dictionary access page",
            "document_type": "Official WRDS authenticated resource",
            "source_url_or_local_path": "https://wrds-www.wharton.upenn.edu/data-dictionary/comp_na_daily_all/",
            "access_date": "2026-10-10",
            "relevant_section": "Access-control response",
            "exact_field_or_scope": "The official data dictionary endpoint requires a logged-in WRDS session; it was not accessed with credentials in this audit environment.",
            "unit_statement": "Not inspected; protected authoritative content is not inferred.",
            "applies_to_annual": "NOT_INSPECTED",
            "applies_to_quarterly": "NOT_INSPECTED",
            "applies_to_currency_fields": "NOT_INSPECTED",
            "applies_to_share_fields": "NOT_INSPECTED",
            "interpretation": "The authoritative field-level source is identified but no authenticated content was retrieved, stored, or assumed.",
            "confidence": "OFFICIAL_ACCESS_BOUNDARY",
            "notes": "Historical access-boundary record. The later authorized field-level evidence is recorded separately; no WRDS credential, token, password, or protected documentation is present in repository artifacts.",
        },
        *_field_level_unit_evidence_rows(),
        {
            "evidence_id": "CRSP_CIZ_DLYCAP_THOUSANDS",
            "source_organization": "CRSP / Morningstar Indexes",
            "document_title": "CRSP US Stock & Indexes Database Guide File Format 2.0 (CIZ)",
            "document_type": "Official CRSP guide",
            "source_url_or_local_path": "https://indexes.morningstar.com/docs/guide/crsp-us-stock-databases-guide-for-flat-file-format-2-0?isRdp=true",
            "access_date": "2026-10-10",
            "relevant_section": "DlyCap; DlyPrc; ShrOut; PERMNO",
            "exact_field_or_scope": "DlyCap in the delivered CIZ daily security dataset.",
            "unit_statement": "DlyCap is security-level market capitalization using closing price or bid/ask average times shares outstanding, in thousands of currency units; the formal US USD predicate makes the relevant denominator thousands of USD.",
            "applies_to_annual": "NOT_APPLICABLE",
            "applies_to_quarterly": "NOT_APPLICABLE",
            "applies_to_currency_fields": "YES_FOR_CRSP_DLYCAP",
            "applies_to_share_fields": "YES_FOR_CRSP_SHROUT",
            "interpretation": "Frozen CRSP denominator rule: each eligible security DlyCap is in thousand USD; a future issuer denominator is the sum of included active security DlyCap values in the same scale.",
            "confidence": "RESOLVED_AUTHORITATIVELY",
            "notes": "This does not establish a Compustat numerator scale.",
        },
    ]


def _factor_relevance(concept: str) -> tuple[str, bool, bool]:
    """Map delivered Phase-2C concepts to the frozen candidate blueprint only."""

    mapping = {
        "assets": ("ROA; GP/A; Investment / asset growth", False, True),
        "equity": ("B/P; ROE", True, True),
        "liabilities": ("Leverage", False, True),
        "short_term_debt": ("Leverage", False, True),
        "long_term_debt": ("Leverage", False, True),
        "preferred_stock": ("B/P construction diagnostic", False, True),
        "revenue": ("Gross-profitability input diagnostic", False, True),
        "sales": ("Gross-profitability input diagnostic", False, True),
        "cogs": ("Gross-profitability input diagnostic", False, True),
        "gross_profit": ("GP/A", False, True),
        "net_income": ("E/P; ROE; ROA", True, True),
        "operating_cash_flow": ("FCF Yield", True, False),
        "capital_expenditure": ("FCF Yield", True, False),
        "cash_dividends_common": ("Dividend yield candidate", True, False),
        "cash_dividends_total": ("Dividend yield candidate", True, False),
        "shares_outstanding": ("Compustat shares diagnostic", False, False),
    }
    return mapping[concept]


def _stock_or_flow(concept: str, frequency: str) -> str:
    if concept in {"assets", "equity", "liabilities", "short_term_debt", "long_term_debt", "preferred_stock", "shares_outstanding"}:
        return "STOCK"
    if concept in {"operating_cash_flow", "capital_expenditure"}:
        return "ANNUAL_FLOW" if frequency == "ANNUAL" else "YTD_FLOW"
    return "ANNUAL_FLOW" if frequency == "ANNUAL" else "SINGLE_QUARTER_FLOW"


def _numeric_units_rows(root: Path) -> list[dict[str, str]]:
    """Create one unit-contract row for every delivered relevant field.

    Eight exact raw fields are now resolved by field-level WRDS evidence.  All
    other rows retain their prior unresolved status; they do not silently gain
    a unit merely because an adjacent field is measured in millions.
    """

    contract = _read_csv(root / AUDIT / "gate0_phase2c_fundamental_field_contract.csv")
    rows: list[dict[str, str]] = []
    for source_row in contract:
        concept = source_row["concept"]
        variable, cross_source, scale_cancels = _factor_relevance(concept)
        for frequency, field_column, present_column, currency_field in (
            ("ANNUAL", "raw_annual_field", "annual_field_present", "curcd"),
            ("QUARTERLY", "raw_quarterly_field", "quarterly_field_present", "curcdq"),
        ):
            field = _clean(source_row[field_column])
            if source_row[present_column] != "true" or not field:
                continue
            spec = FIELD_LEVEL_UNIT_SPECS.get((field, frequency))
            resolved = spec is not None
            required_for_gate0 = (field, frequency) in REQUIRED_CROSS_SOURCE_UNIT_FIELDS
            if resolved:
                reported_unit = "Millions (official WRDS field-level item detail)"
                evidence_id = spec["evidence_id"]
                conversion = "USD millions × 1000 = USD thousands (frozen CRSP DlyCap scale)."
                status = "RESOLVED_AUTHORITATIVELY"
                notes = "Field-specific source evidence establishes numeric scale only. USD denomination remains governed by the frozen standard USD-record predicate; no value or ratio is calculated in Gate 0."
            else:
                reported_unit = "UNRESOLVED: delivered raw header has no unit metadata; the retained generic WRDS statement is not field-level or frequency-level proof."
                evidence_id = "WRDS_COMPUSTAT_GENERIC_MILLIONS; SPG_COMPUSTAT_DICTIONARY_LOCATION; WRDS_COMPUSTAT_DICTIONARY_ACCESS_BOUNDARY"
                conversion = "UNRESOLVED: no field-specific provider conversion to CRSP thousand USD is available."
                status = "UNRESOLVED"
                notes = "No multiplier, currency conversion, sign normalization, annual/quarterly equivalence, or share-unit substitution is inferred. This field is outside the eight authorized current Gate-0 cross-source numerator fields and needs its own evidence before a later absolute-scale use."
            rows.append(
                {
                    "economic_variable": variable,
                    "raw_source": "Compustat North America Fundamentals",
                    "raw_field": field,
                    "frequency": frequency,
                    "stock_or_flow": _stock_or_flow(concept, frequency),
                    "currency_basis": f"USD only after the frozen unique INDL+STD+C+{currency_field}=USD predicate; currency denomination does not establish numeric scale.",
                    "reported_unit_or_scale": reported_unit,
                    "authoritative_evidence_id": evidence_id,
                    "absolute_scale_required": _bool(cross_source),
                    "scale_cancels_in_same_source_ratio": _bool(scale_cancels),
                    "cross_source_scale_alignment_required": _bool(cross_source),
                    "conversion_to_canonical_usd_scale": conversion,
                    "status": status,
                    "gate0_blocking": _bool(required_for_gate0 and not resolved),
                    "notes": notes,
                }
            )
    return sorted(rows, key=lambda row: (row["raw_field"], row["frequency"]))


def verify_field_level_unit_identity(root: Path) -> dict[str, Any]:
    """Check that each authorized unit claim matches both frozen contracts and raw headers."""

    raw_paths = {
        "ANNUAL": RAW / "compustat/Compustat_NA_Fundamentals_Annual_2004-01_2026-10.csv.gz",
        "QUARTERLY": RAW / "compustat/Compustat_NA_Fundamentals_Quarterly_2004-01_2026-10.csv.gz",
    }
    raw_headers: dict[str, set[str]] = {}
    for frequency, relative in raw_paths.items():
        with gzip.open(root / relative, "rt", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            raw_headers[frequency] = set(next(reader))

    contract = _read_csv(root / AUDIT / "gate0_phase2c_fundamental_field_contract.csv")
    contract_fields = {
        "ANNUAL": {
            _clean(row["raw_annual_field"])
            for row in contract
            if row["annual_field_present"] == "true" and _clean(row["raw_annual_field"])
        },
        "QUARTERLY": {
            _clean(row["raw_quarterly_field"])
            for row in contract
            if row["quarterly_field_present"] == "true" and _clean(row["raw_quarterly_field"])
        },
    }
    missing_contract = sorted(
        f"{field}[{frequency}]"
        for field, frequency in REQUIRED_CROSS_SOURCE_UNIT_FIELDS
        if field not in contract_fields[frequency]
    )
    missing_raw = sorted(
        f"{field}[{frequency}]"
        for field, frequency in REQUIRED_CROSS_SOURCE_UNIT_FIELDS
        if field not in raw_headers[frequency]
    )
    return {
        "matches": not missing_contract and not missing_raw,
        "missing_contract": missing_contract,
        "missing_raw": missing_raw,
        "required_fields": sorted(f"{field}[{frequency}]" for field, frequency in REQUIRED_CROSS_SOURCE_UNIT_FIELDS),
    }


def _cross_source_alignment_rows() -> list[dict[str, str]]:
    """Freeze the documented USD-millions to USD-thousands conversion only."""

    evidence = "CRSP_CIZ_DLYCAP_THOUSANDS; WRDS_COMPUSTAT_NI_ANNUAL_MILLIONS; WRDS_COMPUSTAT_NIQ_QUARTERLY_MILLIONS; WRDS_COMPUSTAT_CEQ_ANNUAL_MILLIONS; WRDS_COMPUSTAT_CEQQ_QUARTERLY_MILLIONS; WRDS_COMPUSTAT_OANCF_ANNUAL_MILLIONS; WRDS_COMPUSTAT_OANCFY_YTD_MILLIONS; WRDS_COMPUSTAT_CAPX_ANNUAL_MILLIONS; WRDS_COMPUSTAT_CAPXY_YTD_MILLIONS"
    return [
        {
            "ratio_name": "E/P",
            "numerator_source": "Compustat North America Fundamentals",
            "numerator_fields": "ni (annual); niq (quarterly)",
            "numerator_native_scale": "Millions after the frozen USD record predicate",
            "denominator_source": "CRSP CIZ daily security data aggregated only over future eligible active issuer classes",
            "denominator_field": "DlyCap",
            "denominator_native_scale": "thousand USD",
            "required_conversion": "Multiply NI or valid PIT NIQ in USD millions by 1000 to obtain USD thousands.",
            "formula_in_common_units": "E/P = ((NI or valid PIT earnings measure) × 1000) / IssuerMarketCap_in_CRSP_thousands.",
            "authoritative_evidence": evidence,
            "status": "RESOLVED_AUTHORITATIVELY",
            "gate0_blocking": "false",
            "notes": "No E/P value is calculated in this audit.",
        },
        {
            "ratio_name": "B/P",
            "numerator_source": "Compustat North America Fundamentals",
            "numerator_fields": "ceq (annual); ceqq (quarterly)",
            "numerator_native_scale": "Millions after the frozen USD record predicate",
            "denominator_source": "CRSP CIZ daily security data aggregated only over future eligible active issuer classes",
            "denominator_field": "DlyCap",
            "denominator_native_scale": "thousand USD",
            "required_conversion": "Multiply CEQ or valid PIT CEQQ in USD millions by 1000 to obtain USD thousands.",
            "formula_in_common_units": "B/P = (CEQ × 1000) / IssuerMarketCap_in_CRSP_thousands.",
            "authoritative_evidence": evidence,
            "status": "RESOLVED_AUTHORITATIVELY",
            "gate0_blocking": "false",
            "notes": "No B/P value is calculated in this audit.",
        },
        {
            "ratio_name": "FCF Yield",
            "numerator_source": "Compustat North America Fundamentals",
            "numerator_fields": "oancf - capx (annual); oancfy and capxy after frozen same-fiscal-year differencing and eligible four-quarter TTM aggregation",
            "numerator_native_scale": "Millions after the frozen USD record predicate; YTD differencing and TTM aggregation preserve Millions.",
            "denominator_source": "CRSP CIZ daily security data aggregated only over future eligible active issuer classes",
            "denominator_field": "DlyCap",
            "denominator_native_scale": "thousand USD",
            "required_conversion": "Compute FCF = OANCF - CAPX in USD millions, then multiply the result by 1000 to obtain USD thousands.",
            "formula_in_common_units": "FCF Yield = ((OANCF - CAPX) × 1000) / IssuerMarketCap_in_CRSP_thousands; quarterly/TTM uses valid YTD-derived OANCFY and CAPXY in Millions before ×1000.",
            "authoritative_evidence": evidence,
            "status": "RESOLVED_AUTHORITATIVELY",
            "gate0_blocking": "false",
            "notes": "Same-fiscal-year YTD differencing and four-quarter TTM summation preserve the Millions scale. No FCF or ratio value is calculated in this audit.",
        },
    ]


def _unit_alignment_assessment(units: list[dict[str, str]], alignment: list[dict[str, str]]) -> dict[str, str]:
    """Fail closed unless every required cross-source numerator is authoritative."""

    required_keys = REQUIRED_CROSS_SOURCE_UNIT_FIELDS
    covered = {(row["raw_field"], row["frequency"]) for row in units}
    unresolved_required = sorted(
        f"{row['raw_field']}[{row['frequency']}]"
        for row in units
        if (row["raw_field"], row["frequency"]) in required_keys and row["status"] != "RESOLVED_AUTHORITATIVELY"
    )
    blocked_ratios = sorted(row["ratio_name"] for row in alignment if row["status"] != "RESOLVED_AUTHORITATIVELY")
    return {
        "status": "BLOCKED" if required_keys - covered or unresolved_required or blocked_ratios else "PASS",
        "gate0_blocking": _bool(bool(required_keys - covered or unresolved_required or blocked_ratios)),
        "unresolved_required_fields": "; ".join(unresolved_required),
        "blocked_ratios": "; ".join(blocked_ratios),
        "evidence": "Official WRDS Compustat North America Data Dictionary item-detail evidence establishes NI/NIQ, CEQ/CEQQ, OANCF/OANCFY, and CAPX/CAPXY as Millions; the frozen CRSP DlyCap contract is thousand USD. The deterministic cross-source conversion is ×1000.",
    }


def _pit_contract_rows() -> list[dict[str, str]]:
    return [
        {
            "observation_type": "QUARTERLY",
            "timing_evidence": "valid RDQ with RDQ >= datadate after standard-row conflict policy",
            "availability_rule": "Available only on the first delivered local trading day strictly after RDQ.",
            "invalid_or_missing_rule": "Missing, invalid, conflicting, or no-next-trading-day RDQ => unavailable.",
            "formal_cutoff_rule": f"availability_date <= signal_date <= {FORMAL_CUTOFF}",
            "2026_rule": "No 2026 observation or publication date may enter a 2025 formal signal.",
            "status": "FROZEN",
            "notes": "No same-day RDQ use and no timing fill.",
        },
        {
            "observation_type": "ANNUAL",
            "timing_evidence": "exact matching unique valid Q4 RDQ for GVKEY, fiscal year, and annual datadate after standard-row conflict policy",
            "availability_rule": "Available only on the first delivered local trading day strictly after the defensible Q4 RDQ.",
            "invalid_or_missing_rule": "No exact valid Q4 timing evidence, ambiguous selected row, or no next local trading day => unavailable.",
            "formal_cutoff_rule": f"availability_date <= signal_date <= {FORMAL_CUTOFF}",
            "2026_rule": "2026 annual publication information is RECENT_EXTENSION only and cannot enter a 2025 formal signal.",
            "status": "FROZEN",
            "notes": "Annual datadate is never substituted as a publication date and no generic reporting lag is invented.",
        },
    ]


def _canonical_contract_rows() -> list[dict[str, str]]:
    base = "2006-01-01 through 2025-12-31"
    rows = [
        ("historical universe membership", "CRSP S&P 500 constituents daily", "PERMNO", "DlyCalDt", "Exact delivered constituent date", "No membership fill", "Unavailable when absent", "Exact raw key only", "FALSE", base, "RULE_FROZEN", "Historical membership, not current index composition."),
        ("security identifier", "CRSP stock/name history", "PERMNO", "DlyCalDt", "Time-valid security record", "Retain PERMNO", "Unavailable if no time-valid record", "No order selection", "FALSE", base, "RULE_FROZEN", "PERMNO is the security key."),
        ("issuer identifier", "CRSP security information history", "PERMCO", "security effective interval", "Time-valid included common-equity class", "Retain PERMCO", "Issuer-date unavailable if class status is unresolved", "No issuer inference", "FALSE", base, "RULE_FROZEN", "PERMCO is the issuer aggregation key."),
        ("accounting identifier", "CCM link history", "GVKEY", "LINKDT/LINKENDDT", "Accepted LC/LU/LS and C/J/N/P time-valid link; E is open ended", "Strict inclusive interval join", "Unavailable for 29 unmatched observations", "Different-GVKEY ambiguity => unavailable", "FALSE", base, "RULE_FROZEN", "No forward/backward CCM fill; DVMT remains unlinked."),
        ("historical GICS", "Compustat Historical GICS", "GVKEY", "INDFROM/INDTHRU", "One time-valid raw historical GICS interval", "Raw blank INDTHRU is non-finite without substituting a date", "Industry-dependent transform unavailable for a boundary gap", "Zero or multiple valid rows => unavailable", "FALSE", base, "RULE_FROZEN", "Never current-GICS backfill."),
        ("daily total return", "CRSP Daily Stock CIZ", "PERMNO", "DlyCalDt", "Delivered CIZ DlyRet on exact date", "USE_CIZ_DLYRET_DIRECTLY__DO_NOT_ADD_SEPARATE_DELRET", "Unavailable if raw return absent", "No manual distribution/delisting add-on", "FALSE", base, "RULE_FROZEN", "CIZ return already governs delivered delisting/distribution semantics."),
        ("market benchmark return", "CRSP Daily Market and S&P 500 Indexes", "INDNO", "DlyCalDt", "Selected formal index and exact date", "Use documented DlyTotRet/DlyPrcRet identity", "Unavailable if raw index field absent", "No source substitution", "FALSE", base, "RULE_FROZEN", "1000080/1000500 total return; 1000502 price return."),
        ("IVOL market proxy", "CRSP Daily Market and S&P 500 Indexes", "INDNO", "DlyCalDt", "Exact-date formal market return", "Use frozen benchmark selection only", "Unavailable if prerequisite return missing", "No fill", "FALSE", base, "RULE_FROZEN", "Proxy construction is deferred beyond Gate 0."),
        ("security market cap", "CRSP Daily Stock CIZ", "PERMNO", "DlyCalDt", "Time-valid security and nonmissing DlyCap", "Use DlyCap as delivered security-level cap", "Security-date unavailable if DlyCap missing", "Exact duplicate raw rows may collapse only when all fields are identical", "FALSE", base, "RULE_FROZEN", "No Yahoo/SEC estimate and no manual split adjustment."),
        ("issuer market cap eligibility", "CRSP security history plus DlyCap", "PERMCO", "DlyCalDt", "All economically relevant included active common-equity classes have DlyCap", "Future sum of eligible security DlyCap only", "Issuer-date unavailable when any relevant active class lacks DlyCap", "No omission of active class; no aggregation in this audit", "FALSE", base, "RULE_FROZEN", "PERMCO 47011 has five active-class unavailable dates."),
        ("shares outstanding", "CRSP CIZ share history", "PERMNO", "ShrStartDt/ShrEndDt", "Time-valid raw share interval", "Use only as source provenance/reconciliation", "Unavailable if no matching interval", "No interval fill", "FALSE", base, "RULE_FROZEN", "DlyCap remains the security-cap source."),
        ("quarterly PIT fundamentals", "Compustat NA quarterly", "GVKEY", "RDQ and datadate", "Final PIT availability contract", "Standard USD row then next trading day after valid RDQ", "Unavailable when timing/conflict prerequisites fail", "No date or value fill", "FALSE", base, "RULE_FROZEN", "Publication-date aligned, not vintage perfect."),
        ("annual PIT fundamentals", "Compustat NA annual plus exact quarterly Q4 evidence", "GVKEY", "fyear, datadate, Q4 RDQ", "Final PIT availability contract", "Standard USD row then next trading day after exact valid Q4 RDQ", "Unavailable without exact defensible evidence", "No generic filing lag", "FALSE", base, "RULE_FROZEN", "Annual datadate is not publication timing."),
        ("YTD cash-flow conversion", "Compustat NA quarterly", "GVKEY", "fyearq/fqtr/RDQ", "Four needed PIT-eligible records", "Q1 YTD; later quarter minus same-fiscal-year predecessor", "Unavailable if predecessor missing/invalid", "No cross-year difference", "FALSE", base, "RULE_FROZEN", "Applies only to oancfy and capxy."),
        ("TTM eligibility", "PIT quarterly fundamentals", "GVKEY", "fyearq/fqtr availability", "Four consecutive valid single-quarter flows", "Sum only four eligible consecutive quarters", "Unavailable without all four", "No partial TTM", "FALSE", base, "RULE_FROZEN", "Capability only; no TTM panel is built here."),
        ("missing-data behavior", "All formal sources", "source key", "source date", "Every stated prerequisite passes", "Retain explicit availability reason", "Leave unavailable", "No proxy substitution", "FALSE", base, "RULE_FROZEN", "Less coverage is not silently converted to an estimate."),
        ("duplicate/conflict handling", "Compustat and supplemental CRSP", "source logical key", "source date", "Final conflict policy", "Exact clone collapse only; standard row only when unique", "Unresolved conflict => unavailable", "Never first/last dataframe row", "FALSE", base, "RULE_FROZEN", "No coverage-based selection."),
        ("formal cutoff", "All formal market/fundamental sources", "source key", "availability/signal date", f"No formal use after {FORMAL_CUTOFF}", "Enforce cutoff before output", "Outside-period data unavailable in formal panel", "No mixed-source extension", "FALSE", base, "RULE_FROZEN", "2004–2005 initialization; 2006–2010 warm-up; 2011–2025 formal research periods."),
        ("2026 extension separation", "WRDS 2026 observations", "source key", "source date", "RECENT_EXTENSION only", "Separate label and output namespace", "Never merge into formal historical panel", "No backfill into 2025", "FALSE", "2026-01-01 onward", "RULE_FROZEN", "2026.10+ is Live/Forward."),
    ]
    columns = ("component", "source", "key", "time_key", "availability_rule", "transformation_rule", "missing_data_rule", "conflict_rule", "allowed_fill", "formal_period", "status", "notes")
    return [dict(zip(columns, row)) for row in rows]


def _issue_rows(unit_alignment: dict[str, str]) -> list[dict[str, str]]:
    units_resolved = unit_alignment["gate0_blocking"] == "false"
    rows = [
        ("CCM_UNMATCHED_OBSERVATIONS", "PHASE_2A", "29 historical constituent observations lack a strict accepted CCM mapping, including DVMT/PERMNO 16267.", "Historical PERMNO to GVKEY", "2006-2025 observation dates", "NON_BLOCKING_DISCLOSURE", "CLOSED_NON_BLOCKING", "Leave the affected observation unavailable; no mapping fill.", "No fundamental/GICS dependent value for the observation.", "false", "gate0_phase2a_acceptance.csv; gate0_phase2a_ccm_exceptions.csv", "0 simultaneous different-GVKEY ambiguity; coverage is reduced only.", "Accepted LINKTYPE LC/LU/LS, LINKPRIM C/J/N/P, and raw LINKENDDT E treatment remain frozen."),
        ("GICS_BOUNDARY_GAP", "PHASE_2A", "2,907 strict linked constituent dates have no time-valid historical GICS record.", "Historical GICS", "2006-2025", "NON_BLOCKING_DISCLOSURE", "CLOSED_NON_BLOCKING", "Do not invent an industry.", "Industry-neutral data may remain; industry-dependent transforms are unavailable.", "false", "gate0_phase2a_historical_gics_exceptions.csv", "No current-GICS backfill; coverage is reduced only.", "No multiple-match GICS ambiguity was found."),
        ("GICS_RAW_INDTHRU_BLANK_ENCODING", "PHASE_2A", "12,118 raw INDTHRU values are blank.", "Historical GICS interval end", "Raw historical GICS interval", "NON_BLOCKING_DISCLOSURE", "CLOSED_NON_BLOCKING", "Retain the raw blank; treat it only as a non-finite comparison end after INDFROM, never as a substituted provider date.", "If no single time-valid interval remains, industry-dependent transforms are unavailable.", "false", "gate0_phase2a_historical_gics_summary.csv; WRDS_GATE0_PHASE2A_IDENTIFIERS_CCM_GICS.md", "Raw provider blank encoding remains disclosed; no current/header GICS is used.", "The rule is deterministic and does not manufacture a historical end date."),
        ("ISSUER_MARKET_CAP_SUPPLEMENTAL_EXTRACTION", "PHASE_2B2", "23 of 45 audited issuers carry a recorded daily/share-history limitation.", "Issuer market-cap eligibility", "Class-specific dates", "NON_BLOCKING_DISCLOSURE", "CLOSED_NON_BLOCKING", "Apply the issuer limitation assessment row by row.", "Unavailable only for affected active issuer-date(s); no active class is omitted.", "false", "gate0_final_issuer_limitation_assessment.csv", "22 diagnostics are post-effective-end; PERMCO 47011 has five active-class dates unavailable.", "No issuer market cap is constructed by this sign-off."),
        ("SUPPLEMENTAL_DAILY_EXACT_DUPLICATES", "PHASE_2B2", "34 extra rows occur in 28 exact duplicate PERMNO-date raw groups; no conflicting duplicate key exists.", "Supplemental CRSP daily security data", "2004-2025 supplemental scope", "CANONICAL_RULE_REQUIRED", "RESOLVED", "Exact full-row clones may be collapsed after equality verification.", "Any non-identical duplicate key is unavailable.", "false", "gate0_phase2b2_new_raw_manifest.csv", "Immutable raw is retained; identical rows do not create semantic ambiguity.", "Never select a non-identical duplicate by row order."),
        ("COMPUSTAT_ANNUAL_AVAILABILITY_Q4_RDQ", "PHASE_2C", "Annual values require exact defensible Q4 RDQ timing evidence.", "Annual PIT fundamentals", "2004-2025 formal period", "NON_BLOCKING_DISCLOSURE", "CLOSED_NON_BLOCKING", "Apply the final annual PIT contract.", "No Q4 evidence means annual observation unavailable.", "false", "gate0_phase2c_annual_availability_audit.csv; gate0_final_pit_availability_contract.csv", "Conservative availability reduces coverage; annual datadate is not used as a proxy.", "No generic reporting lag is authorised."),
        ("COMPUSTAT_HISTORICAL_VINTAGE_RESTATEMENT_BOUNDARY", "PHASE_2C", "Ordinary Compustat history is not a Snapshot vintage history.", "Fundamental revision claim", "All formal periods", "NON_BLOCKING_DISCLOSURE", "CLOSED_NON_BLOCKING", "Label all use publication-date-aligned historical fundamentals.", "Do not claim vintage-perfect PIT.", "false", "gate0_phase2c_source_evidence.csv", "Residual restatement/revision bias remains disclosed.", "Snapshot evidence would be required for a stronger claim."),
        ("COMPUSTAT_RAW_DUPLICATE_CONFLICTS", "PHASE_2C", "25,437 annual and 451 quarterly conflicting raw GVKEY-date groups were identified.", "Compustat record selection", "2004-2025", "CANONICAL_RULE_REQUIRED", "RESOLVED", "Apply the unique standard USD-record policy.", "No/multiple eligible standard records are unavailable.", "false", "gate0_final_compustat_conflict_policy.csv", "No dataframe-order or coverage-based selection remains.", "The policy is deterministic and point-in-time neutral."),
        (
            "COMPUSTAT_RAW_FIELD_UNITS",
            "PHASE_2C",
            "Historical Gate-0 blocker: delivered Compustat headers had no unit metadata and generic evidence could not align accounting numerators to CRSP DlyCap. It is resolved for NI/NIQ, CEQ/CEQQ, OANCF/OANCFY, and CAPX/CAPXY by exact official WRDS item-detail evidence.",
            "Value and cross-source fundamental ratios",
            "All formal periods",
            "RESOLVED" if units_resolved else "GATE0_BLOCKER",
            "RESOLVED" if units_resolved else unit_alignment["status"],
            "Field-specific WRDS Data Dictionary evidence establishes Millions for the eight exact raw fields; apply the frozen USD-millions to CRSP-USD-thousands ×1000 conversion.",
            "E/P, B/P, and FCF Yield have documented scale alignment; no actual ratio is constructed in Gate 0.",
            unit_alignment["gate0_blocking"],
            "gate0_units_authoritative_evidence.csv; gate0_final_numeric_units_contract.csv; gate0_final_cross_source_scale_alignment.csv",
            "USD denomination remains distinct from numeric scale. Other raw fields retain their own evidence requirements before a later absolute-scale use.",
            f"Historical lifecycle: previously BLOCKED. Re-evaluation status={unit_alignment['status']}; unresolved_required_fields={unit_alignment['unresolved_required_fields'] or 'none'}; blocked_ratios={unit_alignment['blocked_ratios'] or 'none'}.",
        ),
        ("COMPUSTAT_RDQ_MISSINGNESS", "PHASE_2C", "Missing or invalid RDQ observations are not PIT eligible.", "Quarterly PIT fundamentals", "2004-2025", "NON_BLOCKING_DISCLOSURE", "CLOSED_NON_BLOCKING", "Apply the final quarterly PIT contract.", "Observation unavailable; no timing fill.", "false", "gate0_phase2c_rdq_coverage.csv; gate0_final_pit_availability_contract.csv", "Coverage is reduced deterministically.", "RDQ date-only values are never same-day usable."),
    ]
    columns = ("issue_id", "source_phase", "issue", "affected_component", "affected_period", "severity", "status", "resolution", "canonical_action", "gate0_blocking", "evidence_reference", "residual_bias_or_limitation", "notes")
    return [dict(zip(columns, row)) for row in rows]


def _acceptance_rows(
    raw: dict[str, Any], profiles: dict[str, dict[str, Any]], issues: list[dict[str, str]], unit_alignment: dict[str, str]
) -> list[dict[str, str]]:
    blocker = any(row["gate0_blocking"] == "true" for row in issues)
    numeric_action = (
        "The eight exact numerator fields have a documented conversion to CRSP thousand USD; preserve the frozen ×1000 rule for a later construction stage."
        if unit_alignment["gate0_blocking"] == "false"
        else "Do not construct a cross-source ratio until every required numerator field has a documented conversion to CRSP thousand USD."
    )
    numeric_notes = (
        "NI/NIQ, CEQ/CEQQ, OANCF/OANCFY, and CAPX/CAPXY are Millions; USD denomination comes from the frozen record predicate; DlyCap is thousand USD; required conversion=×1000."
        if unit_alignment["gate0_blocking"] == "false"
        else f"unresolved_required_fields={unit_alignment['unresolved_required_fields']}; blocked_ratios={unit_alignment['blocked_ratios']}."
    )
    canonical_action = (
        "Formal canonical construction prerequisites are deterministic at Gate 0; apply all frozen contracts in the next authorized stage."
        if not blocker
        else "Do not construct the formal canonical dataset until blocker is resolved."
    )
    canonical_notes = "No canonical data were created by this sign-off." if not blocker else "Blocked only by numeric units."
    items = [
        ("RAW_INTEGRITY", "PASS" if raw["raw_hashes_match"] else "FAIL", not raw["raw_hashes_match"], "Phase 1 manifest plus Phase 2B.2 supplemental manifest rehashed.", "Immutable raw inputs verified.", f"expected_files={raw['expected_file_count']}"),
        ("UNIVERSE_HISTORY", "PASS_WITH_WARNING", False, "Historical daily S&P 500 constituent file.", "Use exact historical constituent membership.", "No current-universe substitution."),
        ("CCM_IDENTIFIER_MAPPING", "PASS_WITH_WARNING", False, "2,527,111/2,527,140 matched; 29 unmatched; zero different-GVKEY ambiguity.", "Strict accepted interval mapping; unmatched unavailable.", "No link fill."),
        ("HISTORICAL_GICS", "PASS_WITH_WARNING", False, "2,907 boundary gaps; 12,118 blank raw INDTHRU; no ambiguous matches.", "Industry-dependent transform unavailable if no one historical interval.", "No current-GICS backfill."),
        ("CRSP_RETURN_SEMANTICS", "PASS", False, "Phase 2B CIZ return audit.", "USE_CIZ_DLYRET_DIRECTLY__DO_NOT_ADD_SEPARATE_DELRET.", "No return-formula reopening."),
        ("DELISTING_HANDLING", "PASS", False, "Phase 2B delisting reconciliation.", "Use CIZ DlyRet semantics.", "No separate delisting return."),
        ("CORPORATE_ACTION_HANDLING", "PASS", False, "Phase 2B distribution and split audit.", "No manual return adjustment.", "Avoids double counting."),
        ("SECURITY_MARKET_CAP", "PASS", False, "Phase 2B DlyCap reconciliation.", "Use DlyCap as security-level cap only.", "No issuer aggregation in this audit."),
        ("ISSUER_SHARE_CLASS_COVERAGE", "PASS_WITH_WARNING", False, "45 issuer audit: 22 complete; 23 with limitation; no unresolved issuer.", "Use final issuer limitation assessment.", "Five active-class dates for PERMCO 47011 unavailable."),
        ("SHARES_OUTSTANDING", "PASS_WITH_WARNING", False, "Phase 2B/2B.2 time-valid share-history audit.", "Use active interval evidence; missing active coverage makes issuer-date unavailable.", "No shares imputation."),
        ("MARKET_INDEX_SEMANTICS", "PASS", False, "Phase 2B index field audit.", "Use frozen documented market-return identities.", "Exact-date source only."),
        ("QUARTERLY_RDQ_TIMING", "PASS", False, "Phase 2C RDQ audit.", "Next local trading day after valid RDQ.", "No same-day use."),
        ("ANNUAL_INFORMATION_AVAILABILITY", "PASS_WITH_WARNING", False, "Phase 2C Q4-RDQ annual audit.", "Exact Q4 evidence only; otherwise unavailable.", "Annual datadate never used as publication date."),
        ("COMPUSTAT_REVISION_CLAIM", "PASS_WITH_WARNING", False, "Phase 2C provider evidence distinguishes ordinary history from Snapshot.", "Publication-date-aligned historical claim only.", "No vintage-perfect claim."),
        ("QUARTERLY_FLOW_SEMANTICS", "PASS", False, "Phase 2C WRDS QUARTERIZE evidence.", "Same-fiscal-year YTD differencing only.", "No missing predecessor fill."),
        ("TTM_CAPABILITY", "PASS", False, "Phase 2C TTM capability audit.", "Require four consecutive eligible single quarters.", "No partial TTM."),
        ("COMPUSTAT_DUPLICATE_POLICY", "PASS", False, f"annual_conflicts={profiles['ANNUAL']['duplicate_groups']}; quarterly_conflicts={profiles['QUARTERLY']['duplicate_groups']}.", "Final unique standard USD-record policy.", "Unresolved selected duplicates unavailable."),
        (
            "NUMERIC_UNIT_ALIGNMENT",
            unit_alignment["status"],
            unit_alignment["gate0_blocking"] == "true",
            unit_alignment["evidence"],
            numeric_action,
            numeric_notes,
        ),
        ("FORMAL_SAMPLE_CUTOFF", "PASS", False, "Frozen formal cutoff 2025-12-31.", "Enforce 2004-2005 initialization, 2006-2010 warm-up, 2011-2025 formal periods.", "2026 separate."),
        ("NO_FUTURE_INFORMATION", "PASS", False, "Final PIT availability contract.", "Availability date must precede/equal signal date and remain within formal cutoff.", "No future date, fill, or annual-lag proxy."),
        ("CANONICAL_CONSTRUCTION_DETERMINISM", "BLOCKED" if blocker else "PASS", blocker, "All non-unit rules remain deterministic; numeric alignment is now resolved only when the acceptance matrix says PASS.", canonical_action, canonical_notes),
    ]
    return [
        {"check": check, "status": status, "blocking": _bool(blocking), "evidence": evidence, "canonical_action": action, "notes": notes}
        for check, status, blocking, evidence, action, notes in items
    ]


def validate_final_outputs(
    issues: list[dict[str, str]],
    acceptance: list[dict[str, str]],
    canonical: list[dict[str, str]],
    units: list[dict[str, str]],
    evidence: list[dict[str, str]],
    alignment: list[dict[str, str]],
    unit_alignment: dict[str, str],
    root: Path | None = None,
) -> None:
    """Fail closed when a final-signoff contract is incomplete or unordered."""

    issue_ids = [row["issue_id"] for row in issues]
    if len(issue_ids) != len(set(issue_ids)):
        raise ValueError("Duplicate final issue IDs")
    if any(row["severity"] not in PRIMARY_CATEGORIES for row in issues):
        raise ValueError("Issue severity is outside the frozen primary categories")
    if any(not _clean(row["gate0_blocking"]) for row in issues if row["status"] in {"OPEN", "BLOCKED"}):
        raise ValueError("Every open/blocked issue requires a blocking classification")
    checks = {row["check"] for row in acceptance}
    if checks != set(MANDATORY_CHECKS):
        raise ValueError("Final acceptance matrix does not contain exactly the mandatory checks")
    root = root or project_root()
    evidence_columns = {
        "evidence_id", "source_organization", "document_title", "document_type", "source_url_or_local_path", "access_date",
        "relevant_section", "exact_field_or_scope", "unit_statement", "applies_to_annual", "applies_to_quarterly",
        "applies_to_currency_fields", "applies_to_share_fields", "interpretation", "confidence", "notes",
    }
    alignment_columns = {
        "ratio_name", "numerator_source", "numerator_fields", "numerator_native_scale", "denominator_source", "denominator_field",
        "denominator_native_scale", "required_conversion", "formula_in_common_units", "authoritative_evidence", "status", "gate0_blocking", "notes",
    }
    if not evidence or any(set(row) != evidence_columns for row in evidence):
        raise ValueError("Authoritative unit evidence schema is incomplete")
    if not alignment or any(set(row) != alignment_columns for row in alignment):
        raise ValueError("Cross-source scale alignment schema is incomplete")
    unit_columns = {
        "economic_variable", "raw_source", "raw_field", "frequency", "stock_or_flow", "currency_basis", "reported_unit_or_scale",
        "authoritative_evidence_id", "absolute_scale_required", "scale_cancels_in_same_source_ratio",
        "cross_source_scale_alignment_required", "conversion_to_canonical_usd_scale", "status", "gate0_blocking", "notes",
    }
    if not units or any(set(row) != unit_columns for row in units):
        raise ValueError("Numeric unit contract schema is incomplete")
    delivered_fields = {
        _clean(row[field_column])
        for row in _read_csv(root / AUDIT / "gate0_phase2c_fundamental_field_contract.csv")
        for field_column, present_column in (("raw_annual_field", "annual_field_present"), ("raw_quarterly_field", "quarterly_field_present"))
        if row[present_column] == "true" and _clean(row[field_column])
    }
    if {row["raw_field"] for row in units} != delivered_fields:
        raise ValueError("Numeric unit contract does not cover exactly the delivered factor-relevant fields")
    if {row["frequency"] for row in units} != {"ANNUAL", "QUARTERLY"}:
        raise ValueError("Annual and quarterly unit scope must remain distinct")
    if not all("currency denomination does not establish numeric scale" in row["currency_basis"] for row in units):
        raise ValueError("Currency and numeric scale must be recorded separately")
    if not any(row["evidence_id"] == "CRSP_CIZ_DLYCAP_THOUSANDS" for row in evidence):
        raise ValueError("Frozen CRSP DlyCap unit evidence is missing")
    evidence_by_id = {row["evidence_id"]: row for row in evidence}
    for (field, frequency), spec in FIELD_LEVEL_UNIT_SPECS.items():
        evidence_row = evidence_by_id.get(spec["evidence_id"])
        if evidence_row is None:
            raise ValueError(f"Missing exact field-level evidence for {field}[{frequency}]")
        if evidence_row["source_organization"] != "WRDS / Compustat North America":
            raise ValueError(f"Unexpected source organization for {field}[{frequency}]")
        if "comp_na_daily_all" not in evidence_row["relevant_section"] or "funda" not in evidence_row["relevant_section"]:
            raise ValueError(f"WRDS schema/table provenance is incomplete for {field}[{frequency}]")
        if "Millions" not in evidence_row["unit_statement"] or evidence_row["confidence"] != "RESOLVED_AUTHORITATIVELY":
            raise ValueError(f"Field-level numeric-scale evidence is incomplete for {field}[{frequency}]")
        if evidence_row["applies_to_annual"] != ("YES" if frequency == "ANNUAL" else "NO"):
            raise ValueError(f"Annual scope is wrong for {field}[{frequency}]")
        if evidence_row["applies_to_quarterly"] != ("YES" if frequency == "QUARTERLY" else "NO"):
            raise ValueError(f"Quarterly scope is wrong for {field}[{frequency}]")
    identity = verify_field_level_unit_identity(root)
    if not identity["matches"]:
        raise ValueError(f"Field-level unit evidence does not match frozen raw/contract fields: {identity}")
    units_by_key = {(row["raw_field"], row["frequency"]): row for row in units}
    for key, spec in FIELD_LEVEL_UNIT_SPECS.items():
        unit_row = units_by_key.get(key)
        if unit_row is None or unit_row["status"] != "RESOLVED_AUTHORITATIVELY":
            raise ValueError(f"Required unit contract is unresolved for {key}")
        if unit_row["authoritative_evidence_id"] != spec["evidence_id"] or "Millions" not in unit_row["reported_unit_or_scale"]:
            raise ValueError(f"Unit contract evidence or scale is wrong for {key}")
        if "× 1000" not in unit_row["conversion_to_canonical_usd_scale"]:
            raise ValueError(f"Missing ×1000 conversion for {key}")
    if {row["ratio_name"] for row in alignment} != {"E/P", "B/P", "FCF Yield"}:
        raise ValueError("Cross-source scale alignment does not cover E/P, B/P, and FCF Yield")
    if any(row["denominator_native_scale"] != "thousand USD" for row in alignment):
        raise ValueError("CRSP DlyCap denominator contract changed")
    numeric = next(row for row in acceptance if row["check"] == "NUMERIC_UNIT_ALIGNMENT")
    if any(units_by_key[key]["status"] != "RESOLVED_AUTHORITATIVELY" for key in REQUIRED_CROSS_SOURCE_UNIT_FIELDS) and numeric["status"] in {"PASS", "PASS_WITH_WARNING"}:
        raise ValueError("An unresolved Gate-0 cross-source Compustat field cannot produce a numeric-unit pass")
    if numeric["status"] in {"PASS", "PASS_WITH_WARNING"} and any(row["status"] != "RESOLVED_AUTHORITATIVELY" or row["gate0_blocking"] != "false" for row in alignment):
        raise ValueError("A numeric-unit pass requires every cross-source ratio alignment to be resolved")
    if unit_alignment["gate0_blocking"] == "true" and numeric["status"] != "BLOCKED":
        raise ValueError("A material unit ambiguity must remain blocked")
    if unit_alignment["gate0_blocking"] == "false" and numeric["status"] == "BLOCKED":
        raise ValueError("Resolved required unit evidence cannot remain blocked")
    required_components = {
        "historical universe membership", "security identifier", "issuer identifier", "accounting identifier", "historical GICS",
        "daily total return", "market benchmark return", "IVOL market proxy", "security market cap", "issuer market cap eligibility",
        "shares outstanding", "quarterly PIT fundamentals", "annual PIT fundamentals", "YTD cash-flow conversion", "TTM eligibility",
        "missing-data behavior", "duplicate/conflict handling", "formal cutoff", "2026 extension separation",
    }
    if {row["component"] for row in canonical} != required_components:
        raise ValueError("Canonical construction contract does not contain exactly the mandatory components")
    if any(row["formal_period"] == "2026-01-01 onward" and "historical" in row["status"].lower() for row in canonical):
        raise ValueError("2026 extension data were incorrectly marked formal historical")


def derive_verdict(acceptance: Iterable[dict[str, str]]) -> str:
    """Return the only verdict consistent with the final acceptance matrix."""

    rows = list(acceptance)
    if any(row["status"] == "FAIL" for row in rows):
        return "FORMAL GATE 0 — FAIL"
    if any(row["blocking"] == "true" or row["status"] == "BLOCKED" for row in rows):
        return "FORMAL GATE 0 — BLOCKED"
    if any(row["status"] == "PASS_WITH_WARNING" for row in rows):
        return "FORMAL GATE 0 — PASS WITH WARNINGS"
    return "FORMAL GATE 0 — PASS"


def _update_open_issues(root: Path, final_issues: list[dict[str, str]]) -> None:
    prior = {row["issue_id"]: row for row in _read_csv(root / OPEN_ISSUES)}
    by_id = {row["issue_id"]: row for row in final_issues}
    columns = ("issue_id", "source_phase", "status", "severity", "evidence", "required_resolution", "final_primary_category", "gate0_blocking", "canonical_action", "evidence_reference", "residual_bias_or_limitation")
    rows: list[dict[str, str]] = []
    for issue_id in sorted(prior):
        old = prior[issue_id]
        final = by_id.get(issue_id)
        if final is None:
            rows.append({key: old.get(key, "") for key in columns})
            continue
        is_unit_lifecycle = issue_id == "COMPUSTAT_RAW_FIELD_UNITS"
        rows.append(
            {
                "issue_id": issue_id,
                "source_phase": old["source_phase"],
                "status": final["status"],
                "severity": old["severity"],
                "evidence": "Historical lifecycle: previously BLOCKED because delivered headers lacked field metadata. Resolved by official WRDS Compustat North America Data Dictionary item-detail evidence for NI/NIQ, CEQ/CEQQ, OANCF/OANCFY, and CAPX/CAPXY, all in Millions." if is_unit_lifecycle else old["evidence"],
                "required_resolution": "Resolved: preserve field-level evidence and apply USD millions × 1000 = CRSP USD thousands for the three documented cross-source ratios; do not infer units for other fields." if is_unit_lifecycle else old["required_resolution"],
                "final_primary_category": final["severity"],
                "gate0_blocking": final["gate0_blocking"],
                "canonical_action": final["canonical_action"],
                "evidence_reference": final["evidence_reference"],
                "residual_bias_or_limitation": final["residual_bias_or_limitation"],
            }
        )
    _write_csv(root, OPEN_ISSUES, columns, rows)


def _write_report(
    root: Path,
    raw: dict[str, Any],
    profiles: dict[str, dict[str, Any]],
    issues: list[dict[str, str]],
    acceptance: list[dict[str, str]],
    unit_alignment: dict[str, str],
    evidence: list[dict[str, str]],
    alignment: list[dict[str, str]],
) -> None:
    """Write a dated re-evaluation without erasing the original blocked history."""

    verdict = derive_verdict(acceptance)
    blocking = [row for row in issues if row["gate0_blocking"] == "true"]
    warnings = [row for row in issues if row["severity"] == "NON_BLOCKING_DISCLOSURE"]
    readiness = "Formal Canonical Dataset Construction is now permitted." if verdict in {"FORMAL GATE 0 — PASS", "FORMAL GATE 0 — PASS WITH WARNINGS"} else "Formal Canonical Dataset Construction is NOT permitted."
    blocker_summary = "0: no outstanding Formal Gate 0 blocker." if not blocking else f"{len(blocking)}: " + "; ".join(row["issue_id"] for row in blocking) + "."
    next_step = (
        "Formal Canonical Dataset Construction is now permitted. The next authorized stage must implement the existing frozen contracts; no canonical data, market cap, factor, IC, portfolio, or backtest was created by this re-signoff."
        if not blocking
        else "Resolve every remaining Gate-0 blocker before constructing canonical data."
    )
    lines = [
        "# Formal WRDS Gate 0 — Integrated Final Sign-off",
        "",
        "## 1. Executive verdict",
        "",
        f"**{verdict}**",
        "",
        f"{readiness}",
        "",
        "## 2. Sign-off history and re-evaluation",
        "",
        "Commit `f1831e3011892c8c7e73e2a97143b28a0ab7b813` first recorded Formal Gate 0 as blocked by `COMPUSTAT_RAW_FIELD_UNITS`. That historical conclusion remains preserved: generic WRDS wording that most items are in millions was correctly insufficient because it did not identify exact fields or annual/quarterly scope.",
        "",
        "This dated re-evaluation adds authorized official WRDS Compustat North America Data Dictionary item-detail evidence for the exact delivered mnemonics `ni`, `niq`, `ceq`, `ceqq`, `oancf`, `oancfy`, `capx`, and `capxy`. It does not alter raw data, the frozen research manual, timing, factor definitions, or market-cap aggregation rules.",
        "",
        "## 3. Frozen scope and evidence lineage",
        "",
        "This sign-off reconciles Phase 1, 2A, 2B, 2B.1, 2B.2, and 2C. It creates audit contracts only. It creates no canonical dataset, issuer market cap, factor, IC, portfolio, or backtest.",
        "",
        "- Phase 1: raw integrity and schemas.",
        "- Phase 2A: historical universe, CCM linkage, and historical GICS.",
        "- Phase 2B: CIZ returns, delisting/distribution semantics, DlyCap, shares, and benchmark identities.",
        "- Phase 2B.1/2B.2: supplemental issuer share-class coverage.",
        "- Phase 2C: Compustat timing, restatement boundary, flow semantics, TTM capability, and raw-field contract.",
        "",
        "## 4. Raw integrity and frozen CRSP denominator",
        "",
        f"All {raw['expected_file_count']} approved raw files rehash to the existing manifests: original={raw['original_file_count']}, supplemental={raw['supplemental_file_count']}, failures={len(raw['raw_failures'])}.",
        "",
        "The frozen CRSP CIZ rule remains: `DlyCap` is security-level market capitalization in thousand USD for the formal USD population. A future issuer denominator may sum only eligible active common-equity security `DlyCap` values in the same thousand-USD scale; this audit constructs no issuer market cap.",
        "",
        "## 5. Previously signed universe, return, market-cap, and PIT boundaries",
        "",
        "The historical constituent-to-CCM rule is strict and deterministic. Twenty-nine observations remain unmapped, including DVMT/PERMNO 16267; they remain unavailable. Historical GICS has 2,907 boundary gaps and 12,118 blank raw INDTHRU endpoints. No current-GICS backfill is permitted; industry-dependent transforms are unavailable where no single time-valid historical GICS interval exists.",
        "",
        "The frozen return rule is `USE_CIZ_DLYRET_DIRECTLY__DO_NOT_ADD_SEPARATE_DELRET`. The Phase 2B delisting, distribution, and split review found no reason for a manual add-on or separate corporate-action return adjustment. The 23 issuer limitations remain deterministically classified: 22 are post-effective-end diagnostics; PERMCO 47011 has five active-class unavailable dates. No active share class may be silently omitted.",
        "",
        "Quarterly fundamental records require valid RDQ and become usable only on the next local trading day. Annual records require exact defensible Q4 RDQ evidence; annual datadate is never a publication date. Ordinary delivered Compustat remains labelled publication-date-aligned historical fundamentals, not vintage-perfect PIT.",
        "",
        "## 6. Compustat field-level unit conclusion",
        "",
        "The new exact item-detail evidence resolves numeric scale for the Gate-0 cross-source numerator scope: `ni` and `niq`, `ceq` and `ceqq`, `oancf` and `oancfy`, and `capx` and `capxy` are each **Millions**. The annual and quarterly/YTD counterparts therefore have authoritatively identical numeric scale for this scope.",
        "",
        "The USD record predicate (`INDL + STD + C + curcd/curcdq = USD`) establishes currency denomination, while the WRDS item pages establish numeric scale. These are distinct facts. The formal quarterly-flow rule remains intact: `oancfy` and `capxy` are differenced only within the same fiscal year, and TTM requires four PIT-eligible single quarters. Both transformations preserve Millions.",
        "",
        "## 7. Cross-source ratios",
        "",
    ]
    for row in alignment:
        lines.extend(
            [
                f"### {row['ratio_name']}",
                "",
                f"- Numerator fields: {row['numerator_fields']}",
                f"- Denominator: `{row['denominator_field']}` in {row['denominator_native_scale']}",
                f"- Required conversion: {row['required_conversion']}",
                f"- Common-unit formula: {row['formula_in_common_units']}",
                f"- Status: **{row['status']}**",
                "",
            ]
        )
    lines.extend(
        [
            "## 8. Annual versus quarterly and currency-versus-scale boundaries",
            "",
            "For the eight exact raw fields in the newly supplied evidence, annual/quarterly/YTD numeric scale is Millions. In a same-source ratio whose numerator and denominator are both these Compustat monetary fields in Millions, the common scale cancels. That property does not replace the explicit cross-source conversion required for E/P, B/P, or FCF Yield: their Compustat numerator must be multiplied by 1000 before division by CRSP `DlyCap` in USD thousands.",
            "",
            "## 9. Other frozen Gate 0 evidence",
            "",
            f"Annual duplicate groups={profiles['ANNUAL']['duplicate_groups']} and quarterly duplicate groups={profiles['QUARTERLY']['duplicate_groups']}. The standard USD predicate, PIT timing contracts, CIZ return semantics, historical membership, CCM, historical GICS, and issuer-share-class coverage decisions remain as recorded in the existing Phase 1–2C artifacts. No canonical dataset, issuer market cap, factor, IC, portfolio, or backtest has been created.",
            "",
            "## 10. Formal sample boundaries",
            "",
            "- 2004–2005: initialization / pre-sample buffer.",
            "- 2006–2010: DEV / warm-up.",
            "- 2011–2015: factor selection.",
            "- 2016–2020: configuration selection and strategy freeze.",
            "- 2021–2025.12: Formal Historical OOS.",
            "- 2026.01–2026.09: RECENT_EXTENSION only.",
            "- 2026.10 onward: Live / Forward.",
            "",
            f"The formal market-data cutoff is **{FORMAL_CUTOFF}**. No 2026 mixed-source data may enter the formal historical panel or any 2025 signal.",
            "",
            "## 11. Remaining warnings",
            "",
            "| Issue | Canonical treatment |",
            "|---|---|",
        ]
    )
    for row in warnings:
        lines.append(f"| {row['issue_id']} | {row['canonical_action']} |")
    lines.extend(
        [
            "",
            "## 12. Blocking-issue count",
            "",
            f"**{blocker_summary}**",
            "",
            "## 13. Exact next permitted step",
            "",
            next_step,
            "",
            "## 14. Authoritative sources examined",
            "",
            "| Organization | Title | URL / local path | Section / field scope |",
            "|---|---|---|---|",
        ]
    )
    for row in evidence:
        lines.append(f"| {row['source_organization']} | {row['document_title']} | {row['source_url_or_local_path']} | {row['relevant_section']} |")
    lines.extend(
        [
            "",
            "## 15. Machine-readable evidence",
            "",
            "The issue register, authoritative-evidence table, field-level numeric-units contract, cross-source scale-alignment table, acceptance matrix, and earlier Gate 0 artifacts in this directory are the controlling evidence for this re-evaluation.",
        ]
    )
    (root / REPORT).write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_final_signoff(root: Path | None = None) -> dict[str, Any]:
    """Generate final Gate 0 evidence without creating any downstream research object."""

    root = root or project_root()
    prior = verify_prior_evidence(root)
    if not prior["prior_outputs_present"]:
        raise FileNotFoundError("Missing prior Gate 0 evidence: " + ", ".join(prior["missing_prior_outputs"]))
    raw = verify_raw_integrity(root)
    profiles = _compustat_conflict_profiles(root)
    issuer_rows = _issuer_limitation_rows(root)
    evidence = _unit_evidence_rows()
    units = _numeric_units_rows(root)
    alignment = _cross_source_alignment_rows()
    unit_alignment = _unit_alignment_assessment(units, alignment)
    pit = _pit_contract_rows()
    canonical = _canonical_contract_rows()
    issues = _issue_rows(unit_alignment)
    acceptance = _acceptance_rows(raw, profiles, issues, unit_alignment)
    validate_final_outputs(issues, acceptance, canonical, units, evidence, alignment, unit_alignment, root)

    _write_csv(root, ISSUE_REGISTER, tuple(issues[0]), sorted(issues, key=lambda row: row["issue_id"]))
    _write_csv(root, ISSUER_LIMITATIONS, tuple(issuer_rows[0]), issuer_rows)
    _write_csv(root, UNITS_EVIDENCE, tuple(evidence[0]), evidence)
    _write_csv(root, UNITS_CONTRACT, tuple(units[0]), units)
    _write_csv(root, CROSS_SOURCE_ALIGNMENT, tuple(alignment[0]), alignment)
    _write_csv(root, CONFLICT_POLICY, tuple(_conflict_policy_rows(profiles)[0]), _conflict_policy_rows(profiles))
    _write_csv(root, PIT_CONTRACT, tuple(pit[0]), pit)
    _write_csv(root, CANONICAL_CONTRACT, tuple(canonical[0]), canonical)
    _write_csv(root, ACCEPTANCE, tuple(acceptance[0]), acceptance)
    _update_open_issues(root, issues)
    _write_report(root, raw, profiles, issues, acceptance, unit_alignment, evidence, alignment)
    return {
        "verdict": derive_verdict(acceptance),
        "blocking_issue_count": sum(row["gate0_blocking"] == "true" for row in issues),
        "warning_count": sum(row["severity"] == "NON_BLOCKING_DISCLOSURE" for row in issues),
        "raw": raw,
        "profiles": profiles,
        "issuer_limitations": issuer_rows,
        "unit_alignment": unit_alignment,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Formal WRDS Gate 0 integrated final sign-off.")
    parser.add_argument("--root", type=Path, default=None, help="Repository root; defaults to this module's root.")
    args = parser.parse_args(argv)
    result = run_final_signoff(args.root)
    print(_json({key: result[key] for key in ("verdict", "blocking_issue_count", "warning_count")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
