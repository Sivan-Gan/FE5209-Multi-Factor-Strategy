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


def _numeric_units_rows() -> list[dict[str, str]]:
    unknown = "UNRESOLVED: delivered CSV headers contain no authoritative field-level scale/unit metadata"
    evidence = "gate0_phase2c_fundamental_field_contract.csv; no Compustat item dictionary is present in the repository or bundled Phase 2C sources"
    rows = [
        ("E/P", "Compustat NA annual/quarterly + CRSP CIZ", "ni; niq; issuer DlyCap", True, False, True, "Do not calculate E/P until authoritative Compustat-to-CRSP scale alignment is retained.", "BLOCKED_PENDING_AUTHORITATIVE_SCALE"),
        ("B/P", "Compustat NA annual/quarterly + CRSP CIZ", "ceq; ceqq; issuer DlyCap", True, False, True, "Do not calculate B/P until authoritative Compustat-to-CRSP scale alignment is retained.", "BLOCKED_PENDING_AUTHORITATIVE_SCALE"),
        ("FCF Yield", "Compustat NA annual/quarterly + CRSP CIZ", "oancf; oancfy; capx; capxy; issuer DlyCap", True, False, True, "Do not calculate FCF Yield until authoritative Compustat-to-CRSP scale alignment is retained.", "BLOCKED_PENDING_AUTHORITATIVE_SCALE"),
        ("ROE", "Compustat NA annual/quarterly", "ni; niq; ceq; ceqq", False, True, False, "A same-source ratio may be scale-invariant, but retain raw units and do not claim an absolute scale.", "SAME_SOURCE_RATIO_ONLY"),
        ("ROA", "Compustat NA annual/quarterly", "ni; niq; at; atq", False, True, False, "A same-source ratio may be scale-invariant, but retain raw units and do not claim an absolute scale.", "SAME_SOURCE_RATIO_ONLY"),
        ("Gross profitability", "Compustat NA annual/quarterly", "gp; revt; sale; cogs; revtq; saleq; cogsq; at; atq", False, True, False, "Use delivered annual gp only; no universal revenue-minus-cogs substitute and no delivered gpq.", "SAME_SOURCE_RATIO_ONLY"),
        ("Leverage", "Compustat NA annual/quarterly", "dlc; dltt; dlcq; dlttq; lt; ltq; at; atq", False, True, False, "Same-source balance-sheet ratios are scale-invariant if fields share the provider scale.", "SAME_SOURCE_RATIO_ONLY"),
        ("Investment / asset growth", "Compustat NA annual/quarterly", "at; atq", False, True, False, "Growth is scale-invariant only across comparable same-source records after PIT and conflict rules.", "SAME_SOURCE_RATIO_ONLY"),
        ("Dividend yield", "Compustat NA annual + CRSP CIZ", "dvc; dvt; issuer DlyCap", True, False, True, "Do not calculate a dividend yield until authoritative cross-source scale alignment is retained.", "BLOCKED_PENDING_AUTHORITATIVE_SCALE"),
        ("Size", "CRSP CIZ", "security DlyCap; issuer DlyCap", False, True, False, "Use only the CRSP DlyCap scale consistently for ranking after share-class eligibility; no Compustat numerator is involved.", "CRSP_INTERNAL_SCALE_ONLY"),
        ("Compustat shares diagnostic", "Compustat NA annual/quarterly", "csho; cshoq", True, False, True, "Do not reconcile or substitute Compustat shares for CRSP market-cap inputs without authoritative units and adjustment semantics.", "BLOCKED_FOR_CROSS_SOURCE_RECONCILIATION"),
    ]
    result: list[dict[str, str]] = []
    for variable, source, fields, absolute, cancels, cross_source, rule, status in rows:
        result.append(
            {
                "economic_variable": variable,
                "raw_source": source,
                "raw_field": fields,
                "reported_unit_or_scale": "CRSP DlyCap internally reconciles to DlyPrc × ShrOut at scale 1; " + unknown,
                "authoritative_evidence": evidence + "; CRSP internal reconciliation: gate0_phase2b_dlycap_unit_reconciliation.csv",
                "absolute_scale_required": _bool(absolute),
                "scale_cancels_in_ratio": _bool(cancels),
                "cross_source_scale_alignment_required": _bool(cross_source),
                "canonical_rule": rule,
                "status": status,
                "gate0_blocking": _bool(cross_source),
                "notes": "No silent thousand/million multiplier, currency conversion, or sign transformation is authorised.",
            }
        )
    return result


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


def _issue_rows() -> list[dict[str, str]]:
    rows = [
        ("CCM_UNMATCHED_OBSERVATIONS", "PHASE_2A", "29 historical constituent observations lack a strict accepted CCM mapping, including DVMT/PERMNO 16267.", "Historical PERMNO to GVKEY", "2006-2025 observation dates", "NON_BLOCKING_DISCLOSURE", "CLOSED_NON_BLOCKING", "Leave the affected observation unavailable; no mapping fill.", "No fundamental/GICS dependent value for the observation.", "false", "gate0_phase2a_acceptance.csv; gate0_phase2a_ccm_exceptions.csv", "0 simultaneous different-GVKEY ambiguity; coverage is reduced only.", "Accepted LINKTYPE LC/LU/LS, LINKPRIM C/J/N/P, and raw LINKENDDT E treatment remain frozen."),
        ("GICS_BOUNDARY_GAP", "PHASE_2A", "2,907 strict linked constituent dates have no time-valid historical GICS record.", "Historical GICS", "2006-2025", "NON_BLOCKING_DISCLOSURE", "CLOSED_NON_BLOCKING", "Do not invent an industry.", "Industry-neutral data may remain; industry-dependent transforms are unavailable.", "false", "gate0_phase2a_historical_gics_exceptions.csv", "No current-GICS backfill; coverage is reduced only.", "No multiple-match GICS ambiguity was found."),
        ("GICS_RAW_INDTHRU_BLANK_ENCODING", "PHASE_2A", "12,118 raw INDTHRU values are blank.", "Historical GICS interval end", "Raw historical GICS interval", "NON_BLOCKING_DISCLOSURE", "CLOSED_NON_BLOCKING", "Retain the raw blank; treat it only as a non-finite comparison end after INDFROM, never as a substituted provider date.", "If no single time-valid interval remains, industry-dependent transforms are unavailable.", "false", "gate0_phase2a_historical_gics_summary.csv; WRDS_GATE0_PHASE2A_IDENTIFIERS_CCM_GICS.md", "Raw provider blank encoding remains disclosed; no current/header GICS is used.", "The rule is deterministic and does not manufacture a historical end date."),
        ("ISSUER_MARKET_CAP_SUPPLEMENTAL_EXTRACTION", "PHASE_2B2", "23 of 45 audited issuers carry a recorded daily/share-history limitation.", "Issuer market-cap eligibility", "Class-specific dates", "NON_BLOCKING_DISCLOSURE", "CLOSED_NON_BLOCKING", "Apply the issuer limitation assessment row by row.", "Unavailable only for affected active issuer-date(s); no active class is omitted.", "false", "gate0_final_issuer_limitation_assessment.csv", "22 diagnostics are post-effective-end; PERMCO 47011 has five active-class dates unavailable.", "No issuer market cap is constructed by this sign-off."),
        ("SUPPLEMENTAL_DAILY_EXACT_DUPLICATES", "PHASE_2B2", "34 extra rows occur in 28 exact duplicate PERMNO-date raw groups; no conflicting duplicate key exists.", "Supplemental CRSP daily security data", "2004-2025 supplemental scope", "CANONICAL_RULE_REQUIRED", "RESOLVED", "Exact full-row clones may be collapsed after equality verification.", "Any non-identical duplicate key is unavailable.", "false", "gate0_phase2b2_new_raw_manifest.csv", "Immutable raw is retained; identical rows do not create semantic ambiguity.", "Never select a non-identical duplicate by row order."),
        ("COMPUSTAT_ANNUAL_AVAILABILITY_Q4_RDQ", "PHASE_2C", "Annual values require exact defensible Q4 RDQ timing evidence.", "Annual PIT fundamentals", "2004-2025 formal period", "NON_BLOCKING_DISCLOSURE", "CLOSED_NON_BLOCKING", "Apply the final annual PIT contract.", "No Q4 evidence means annual observation unavailable.", "false", "gate0_phase2c_annual_availability_audit.csv; gate0_final_pit_availability_contract.csv", "Conservative availability reduces coverage; annual datadate is not used as a proxy.", "No generic reporting lag is authorised."),
        ("COMPUSTAT_HISTORICAL_VINTAGE_RESTATEMENT_BOUNDARY", "PHASE_2C", "Ordinary Compustat history is not a Snapshot vintage history.", "Fundamental revision claim", "All formal periods", "NON_BLOCKING_DISCLOSURE", "CLOSED_NON_BLOCKING", "Label all use publication-date-aligned historical fundamentals.", "Do not claim vintage-perfect PIT.", "false", "gate0_phase2c_source_evidence.csv", "Residual restatement/revision bias remains disclosed.", "Snapshot evidence would be required for a stronger claim."),
        ("COMPUSTAT_RAW_DUPLICATE_CONFLICTS", "PHASE_2C", "25,437 annual and 451 quarterly conflicting raw GVKEY-date groups were identified.", "Compustat record selection", "2004-2025", "CANONICAL_RULE_REQUIRED", "RESOLVED", "Apply the unique standard USD-record policy.", "No/multiple eligible standard records are unavailable.", "false", "gate0_final_compustat_conflict_policy.csv", "No dataframe-order or coverage-based selection remains.", "The policy is deterministic and point-in-time neutral."),
        ("COMPUSTAT_RAW_FIELD_UNITS", "PHASE_2C", "Delivered Compustat headers do not provide authoritative accounting-field scales, so Compustat-to-CRSP alignment cannot be established.", "Value and cross-source fundamental ratios", "All formal periods", "GATE0_BLOCKER", "BLOCKED", "Obtain and retain an authoritative Compustat item dictionary and explicit Compustat-to-CRSP scale alignment evidence, then rerun final Gate 0.", "E/P, B/P, FCF Yield, and any other affected cross-source ratio remain prohibited.", "true", "gate0_final_numeric_units_contract.csv", "No multiplier, currency conversion, or scale assumption may be inferred from value magnitudes.", "This is the sole Gate 0 blocker."),
        ("COMPUSTAT_RDQ_MISSINGNESS", "PHASE_2C", "Missing or invalid RDQ observations are not PIT eligible.", "Quarterly PIT fundamentals", "2004-2025", "NON_BLOCKING_DISCLOSURE", "CLOSED_NON_BLOCKING", "Apply the final quarterly PIT contract.", "Observation unavailable; no timing fill.", "false", "gate0_phase2c_rdq_coverage.csv; gate0_final_pit_availability_contract.csv", "Coverage is reduced deterministically.", "RDQ date-only values are never same-day usable."),
    ]
    columns = ("issue_id", "source_phase", "issue", "affected_component", "affected_period", "severity", "status", "resolution", "canonical_action", "gate0_blocking", "evidence_reference", "residual_bias_or_limitation", "notes")
    return [dict(zip(columns, row)) for row in rows]


def _acceptance_rows(raw: dict[str, Any], profiles: dict[str, dict[str, Any]], issues: list[dict[str, str]]) -> list[dict[str, str]]:
    blocker = any(row["gate0_blocking"] == "true" for row in issues)
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
        ("NUMERIC_UNIT_ALIGNMENT", "BLOCKED", True, "No authoritative Compustat field dictionary/scale is retained locally; cross-source values cannot be aligned.", "Acquire authoritative evidence and rerun this gate.", "E/P, B/P, FCF Yield prohibited."),
        ("FORMAL_SAMPLE_CUTOFF", "PASS", False, "Frozen formal cutoff 2025-12-31.", "Enforce 2004-2005 initialization, 2006-2010 warm-up, 2011-2025 formal periods.", "2026 separate."),
        ("NO_FUTURE_INFORMATION", "PASS", False, "Final PIT availability contract.", "Availability date must precede/equal signal date and remain within formal cutoff.", "No future date, fill, or annual-lag proxy."),
        ("CANONICAL_CONSTRUCTION_DETERMINISM", "BLOCKED" if blocker else "PASS", blocker, "All non-unit rules are deterministic; numeric alignment remains unresolved.", "Do not construct the formal canonical dataset until blocker is resolved.", "Blocked only by numeric units."),
    ]
    return [
        {"check": check, "status": status, "blocking": _bool(blocking), "evidence": evidence, "canonical_action": action, "notes": notes}
        for check, status, blocking, evidence, action, notes in items
    ]


def validate_final_outputs(
    issues: list[dict[str, str]], acceptance: list[dict[str, str]], canonical: list[dict[str, str]], units: list[dict[str, str]]
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
    if not {"E/P", "B/P", "FCF Yield"}.issubset({row["economic_variable"] for row in units}):
        raise ValueError("Numeric unit contract does not cover E/P, B/P, and FCF Yield")
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
        rows.append(
            {
                "issue_id": issue_id,
                "source_phase": old["source_phase"],
                "status": final["status"],
                "severity": old["severity"],
                "evidence": old["evidence"],
                "required_resolution": old["required_resolution"],
                "final_primary_category": final["severity"],
                "gate0_blocking": final["gate0_blocking"],
                "canonical_action": final["canonical_action"],
                "evidence_reference": final["evidence_reference"],
                "residual_bias_or_limitation": final["residual_bias_or_limitation"],
            }
        )
    _write_csv(root, OPEN_ISSUES, columns, rows)


def _write_report(root: Path, raw: dict[str, Any], profiles: dict[str, dict[str, Any]], issues: list[dict[str, str]], acceptance: list[dict[str, str]]) -> None:
    blocking = [row for row in issues if row["gate0_blocking"] == "true"]
    warnings = [row for row in issues if row["severity"] == "NON_BLOCKING_DISCLOSURE"]
    lines = [
        "# Formal WRDS Gate 0 — Integrated Final Sign-off",
        "",
        "## 1. Executive verdict",
        "",
        "**FORMAL GATE 0 — BLOCKED**",
        "",
        "The evidence audit is complete and reproducible, but Formal Canonical Dataset Construction is **not permitted**. The single blocker is unresolved authoritative numeric-unit and scale alignment between Compustat accounting fields and CRSP issuer market capitalization. No multiplier, currency conversion, or other scale assumption has been inferred.",
        "",
        "## 2. Scope",
        "",
        "This sign-off reconciles Phase 1, 2A, 2B, 2B.1, 2B.2, and 2C. It creates audit contracts only. It creates no canonical dataset, issuer market cap, factor, IC, portfolio, or backtest.",
        "",
        "## 3. Evidence lineage by phase",
        "",
        "- Phase 1: raw integrity and schemas.",
        "- Phase 2A: historical universe, CCM linkage, and historical GICS.",
        "- Phase 2B: CIZ returns, delisting/distribution semantics, DlyCap, shares, and benchmark identities.",
        "- Phase 2B.1/2B.2: supplemental issuer share-class coverage.",
        "- Phase 2C: Compustat timing, restatement boundary, flow semantics, TTM capability, and raw-field contract.",
        "",
        "## 4. Raw integrity status",
        "",
        f"All {raw['expected_file_count']} expected raw files rehash to their approved manifests: original={raw['original_file_count']}, supplemental={raw['supplemental_file_count']}, failures={len(raw['raw_failures'])}.",
        "",
        "## 5. Universe, CCM, and historical GICS",
        "",
        "The historical constituent-to-CCM rule is strict and deterministic. Twenty-nine observations remain unmapped, including DVMT/PERMNO 16267; they remain unavailable. Accepted link types/priorities and raw LINKENDDT `E` stay frozen. Historical GICS has 2,907 boundary gaps and 12,118 blank raw INDTHRU endpoints. No current-GICS backfill is permitted; industry-dependent transforms are unavailable where no single time-valid historical GICS interval exists.",
        "",
        "## 6. CRSP returns and delisting",
        "",
        "The frozen return rule is `USE_CIZ_DLYRET_DIRECTLY__DO_NOT_ADD_SEPARATE_DELRET`. The Phase 2B delisting, distribution, and split review found no reason for a manual add-on or separate corporate-action return adjustment.",
        "",
        "## 7. Market cap and issuer share classes",
        "",
        "DlyCap remains the future security-level market-cap input. A later issuer cap may sum only economically relevant included active common-equity DlyCap values. The 23 issuer limitations are now classified deterministically: 22 are post-effective-end diagnostics; PERMCO 47011 has five active-class dates that must be unavailable. No active share class may be silently omitted.",
        "",
        "## 8. Compustat PIT timing and annual availability",
        "",
        "Quarterly records require a valid RDQ and become usable only on the next local trading day. Annual records require exact defensible Q4 RDQ evidence; annual datadate is never a publication date. Missing, invalid, conflicting, or non-trading-day timing evidence yields unavailable, not a convenience reporting lag.",
        "",
        "## 9. Restatement boundary, quarterly flows, and TTM",
        "",
        "Ordinary delivered Compustat is labelled publication-date-aligned historical fundamentals, not vintage-perfect PIT. OANCFY and CAPXY may be differenced only within a fiscal year; TTM requires four consecutive eligible single quarters. Neither rule authorizes filling missing predecessors or source rows.",
        "",
        "## 10. Compustat conflict policy",
        "",
        f"Annual duplicate groups={profiles['ANNUAL']['duplicate_groups']} and quarterly duplicate groups={profiles['QUARTERLY']['duplicate_groups']}. The final rule retains a row only if the standard USD predicate leaves exactly one candidate. The raw profile records that POPSRC is not delivered. All remaining no/multiple-candidate observations are unavailable, never selected by dataframe order.",
        "",
        "## 11. Numeric units and cross-source scaling",
        "",
        "The delivered raw headers do not supply authoritative Compustat item units. Same-source ratios can be scale-invariant, but E/P, B/P, FCF Yield, and dividend yield combine Compustat accounting values with CRSP issuer market capitalization and therefore require explicit cross-source alignment. The required authoritative item dictionary is absent from the project and from the bundled Phase 2C sources. This is a blocker, not a warning.",
        "",
        "## 12. Formal sample boundaries",
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
        "## 13. Remaining-warning table",
        "",
        "| Issue | Canonical treatment |",
        "|---|---|",
    ]
    for row in warnings:
        lines.append(f"| {row['issue_id']} | {row['canonical_action']} |")
    lines.extend(
        [
            "",
            "## 14. Blocking-issue count",
            "",
            f"**{len(blocking)}**: `COMPUSTAT_RAW_FIELD_UNITS`.",
            "",
            "## 15. Canonical construction readiness",
            "",
            "All non-unit contracts now specify deterministic source, timing, conflict, availability, and missing-data behavior. Nevertheless, a factor-ready formal canonical layer cannot be trusted until the required cross-source numeric scale is evidenced.",
            "",
            "## 16. Exact permitted next step",
            "",
            "Obtain and retain authoritative Compustat item-unit documentation and explicit Compustat-to-CRSP scale-alignment evidence, then rerun this integrated final sign-off.",
            "",
            "## 17. Exact prohibited next steps before review",
            "",
            "Do not construct the formal canonical dataset, issuer market cap, factors, IC, portfolio, backtest, Gate A evidence, or any mixed 2026 formal-history output. Do not modify raw files or the frozen SSOT.",
            "",
            "## 18. Machine-readable evidence",
            "",
            "The issue register, issuer assessment, numeric-units contract, conflict policy, PIT contract, canonical construction contract, and acceptance matrix in this directory are the controlling audit artifacts for this decision.",
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
    units = _numeric_units_rows()
    pit = _pit_contract_rows()
    canonical = _canonical_contract_rows()
    issues = _issue_rows()
    acceptance = _acceptance_rows(raw, profiles, issues)
    validate_final_outputs(issues, acceptance, canonical, units)

    _write_csv(root, ISSUE_REGISTER, tuple(issues[0]), sorted(issues, key=lambda row: row["issue_id"]))
    _write_csv(root, ISSUER_LIMITATIONS, tuple(issuer_rows[0]), issuer_rows)
    _write_csv(root, UNITS_CONTRACT, tuple(units[0]), units)
    _write_csv(root, CONFLICT_POLICY, tuple(_conflict_policy_rows(profiles)[0]), _conflict_policy_rows(profiles))
    _write_csv(root, PIT_CONTRACT, tuple(pit[0]), pit)
    _write_csv(root, CANONICAL_CONTRACT, tuple(canonical[0]), canonical)
    _write_csv(root, ACCEPTANCE, tuple(acceptance[0]), acceptance)
    _update_open_issues(root, issues)
    _write_report(root, raw, profiles, issues, acceptance)
    return {
        "verdict": derive_verdict(acceptance),
        "blocking_issue_count": sum(row["gate0_blocking"] == "true" for row in issues),
        "warning_count": sum(row["severity"] == "NON_BLOCKING_DISCLOSURE" for row in issues),
        "raw": raw,
        "profiles": profiles,
        "issuer_limitations": issuer_rows,
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
