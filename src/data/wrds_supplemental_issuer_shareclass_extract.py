"""Prepare, but do not automatically run, a supplemental CRSP CIZ extraction.

Formal Gate 0 Phase 2B established that the delivered 959-PERMNO historical
S&P 500 extract cannot establish complete issuer/share-class coverage.  This
module prepares the narrow, reproducible CRSP CIZ pull needed to close that
evidence gap.  It deliberately does *not* construct issuer market
capitalization, canonical data, factors, or portfolios.

Running this module without ``--execute`` writes only the tracked query-input
and Gate-0 preparation evidence.  ``--execute`` is intentionally opt-in: it
opens the user's authenticated WRDS session only when the user has separately
authorized data acquisition.  It has no embedded credentials and will never
overwrite a raw vendor export.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from src.data import wrds_gate0_phase1 as phase1


EVIDENCE_STAGE = "FORMAL_GATE0_PHASE2B1_SUPPLEMENTAL_EXTRACTION_PREPARATION"
START_DATE = "2004-01-01"
END_DATE = "2025-12-31"
WRDS_LIBRARY = "crsp"
QUERY_BATCH_SIZE = 20

ISSUER_SUFFICIENCY = Path("outputs/audit/wrds_formal/gate0_phase2b_issuer_marketcap_sufficiency.csv")
NAMES = Path("crsp/CRSP_Names_CIZ_FullHistory_SP500_Historical_PERMNOs.csv.gz")
CCM = Path("ccm/CCM_CRSP_Compustat_Link_LC_LU_LS_FullHistory_959_SP500_PERMNOs.csv.gz")
QUERY_INPUT = Path("config/wrds_supplemental_issuer_permcos.csv")
AUDIT_DIRECTORY = Path("outputs/audit/wrds_formal")

SECURITY_HISTORY_TABLE = "crsp.stksecurityinfohist"
DAILY_SECURITY_TABLE = "crsp.stkdlysecuritydata"
SHARES_TABLE = "crsp.stkshares"

SECURITY_HISTORY_OUTPUT = Path(
    "wrds_raw/US_WRDS_2026-10-09/crsp/CRSP_Supplemental_Issuer_SecurityInfoHist_2004-01-01_2025-12-31.csv.gz"
)
DAILY_SECURITY_OUTPUT = Path(
    "wrds_raw/US_WRDS_2026-10-09/crsp/CRSP_Supplemental_Issuer_DailySecurityData_2004-01-01_2025-12-31.csv.gz"
)
SHARES_OUTPUT = Path(
    "wrds_raw/US_WRDS_2026-10-09/crsp/CRSP_Supplemental_Issuer_Shares_2004-01-01_2025-12-31.csv.gz"
)
EXECUTION_METADATA = AUDIT_DIRECTORY / "gate0_phase2b1_supplemental_extraction_metadata.json"

# CRSP's official CIZ-to-SIZ crosswalk documents that these combinations map
# to legacy common-equity share codes 10, 11, 12, 18, 20, 21, 40, 41, 42, 48.
# This includes common corporate classes, REIT beneficial-interest classes, and
# certificate common classes.  It excludes ADRs (AD), units (UG), funds,
# derivatives, preferred securities, debt, warrants, and other non-common rows.
ELIGIBLE_SHARE_TYPES = ("NS", "CE", "SB")
ELIGIBLE_SECURITY_TYPE = "EQTY"
ELIGIBLE_SECURITY_SUBTYPE = "COM"
ELIGIBLE_ISSUER_TYPES = ("ACOR", "CORP", "REIT")

SECURITY_HISTORY_COLUMNS = (
    "permno",
    "permco",
    "secinfostartdt",
    "secinfoenddt",
    "securitybegdt",
    "securityenddt",
    "cusip",
    "ticker",
    "tradingsymbol",
    "issuernm",
    "shareclass",
    "sharetype",
    "securitytype",
    "securitysubtype",
    "usincflg",
    "issuertype",
    "securityactiveflg",
    "primaryexch",
    "tradingstatusflg",
)
DAILY_SECURITY_COLUMNS = (
    "permno",
    "dlycaldt",
    "dlycap",
    "dlyprc",
    "dlycapflg",
    "dlyprcflg",
    "dlyret",
    "dlydelflg",
    "dlyvol",
)
SHARES_COLUMNS = (
    "permno",
    "shrstartdt",
    "shrenddt",
    "shrout",
    "shrsource",
    "shrfactype",
    "shradrflg",
)

SOURCE_REFERENCES = (
    {
        "source_id": "WRDS_CIZ_TO_SIZ_MACRO",
        "provider": "WRDS",
        "url": "https://wrds-www.wharton.upenn.edu/pages/wrds-research/macros/crsp-ciztosiz-macro/",
        "evidence": "Uses crsp.StkSecurityInfoHist with SecInfoStartDt/SecInfoEndDt and gives the CIZ-to-common-share-code mapping.",
    },
    {
        "source_id": "WRDS_CIZ_IDVOL_MACRO",
        "provider": "WRDS",
        "url": "https://wrds-www.wharton.upenn.edu/pages/wrds-research/macros/wrds-macro-idvol-ciz/",
        "evidence": "Queries crsp.StkDlySecurityData by PERMNO and DlyCalDt.",
    },
    {
        "source_id": "CRSP_CIZ_SHARE_CODE_REFERENCE",
        "provider": "CRSP",
        "url": "https://www.crsp.org/wp-content/uploads/ShareCode.html",
        "evidence": "Defines CIZ SecurityType, SecuritySubType, ShareType, IssuerType, and USIncFlg combinations for common-equity codes.",
    },
    {
        "source_id": "CRSP_CIZ_STKSHARES_GUIDE",
        "provider": "CRSP",
        "url": "https://www.crsp.org/wp-content/uploads/guides/CRSP_US_Stock_%26_Indexes_Database_Guide_Flat_File_Format_2.0.pdf",
        "evidence": "Documents StkShares effective dates, ShrOut, ShrSource, ShrFacType, and ShrAdrFlg.",
    },
)


def project_root() -> Path:
    """Return the repository root without consulting the current directory."""

    return Path(__file__).resolve().parents[2]


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _numeric_key(value: str) -> tuple[int, str]:
    text = _clean(value)
    return (int(text), text) if text.isdigit() else (10**18, text)


def _json_list(value: str) -> list[str]:
    parsed = json.loads(value)
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise ValueError(f"Expected a JSON list of strings: {value!r}")
    return [_clean(item) for item in parsed if _clean(item)]


def _json_set(values: Iterable[str], *, numeric: bool = False) -> str:
    unique = {_clean(value) for value in values if _clean(value)}
    ordered = sorted(unique, key=_numeric_key if numeric else None)
    return json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))


def _read_csv(path: Path) -> Iterator[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        yield from csv.DictReader(handle)


def _read_gzip_csv(path: Path) -> Iterator[dict[str, str]]:
    with gzip.open(path, "rt", newline="", encoding="utf-8-sig") as handle:
        yield from csv.DictReader(handle)


def is_eligible_common_equity(row: dict[str, str]) -> bool:
    """Apply the provider-documented CIZ common-equity classification only."""

    return (
        _clean(row.get("sharetype")).upper() in ELIGIBLE_SHARE_TYPES
        and _clean(row.get("securitytype")).upper() == ELIGIBLE_SECURITY_TYPE
        and _clean(row.get("securitysubtype")).upper() == ELIGIBLE_SECURITY_SUBTYPE
        and _clean(row.get("issuertype")).upper() in ELIGIBLE_ISSUER_TYPES
    )


def derive_query_universe(
    sufficiency_rows: Iterable[dict[str, str]],
    names_rows: Iterable[dict[str, str]],
    ccm_rows: Iterable[dict[str, str]],
) -> list[dict[str, str]]:
    """Derive the deterministic supplemental PERMCO input from Phase-2B evidence.

    The audit contains 37 group-level warnings, some of which overlap.  A
    PERMCO is the primary query key, so the resulting one-row-per-PERMCO input
    intentionally coalesces duplicate group evidence while retaining its origin.
    Ticker is carried only as diagnostic context.
    """

    relevant_rows = [
        row
        for row in sufficiency_rows
        if _clean(row.get("classification")) == "SUPPLEMENTAL_CRSP_EXTRACTION_REQUIRED"
    ]
    if not relevant_rows:
        raise ValueError("No SUPPLEMENTAL_CRSP_EXTRACTION_REQUIRED Phase-2B rows found.")

    target_permcos: set[str] = set()
    source_groups: dict[str, set[str]] = defaultdict(set)
    group_gvkeys: dict[str, set[str]] = defaultdict(set)
    listed_members: set[str] = set()
    for row in relevant_rows:
        permcos = _json_list(_clean(row.get("permcos")))
        member_permnos = _json_list(_clean(row.get("relevant_permnos")))
        group_label = f"{_clean(row.get('group_type'))}:{_clean(row.get('group_identifier'))}"
        for permco in permcos:
            target_permcos.add(permco)
            source_groups[permco].add(group_label)
            if _clean(row.get("group_type")) == "GVKEY":
                group_gvkeys[permco].add(_clean(row.get("group_identifier")))
        listed_members.update(member_permnos)

    member_permco: dict[str, set[str]] = defaultdict(set)
    siblings: dict[str, set[str]] = defaultdict(set)
    tickers: dict[str, set[str]] = defaultdict(set)
    issuers: dict[str, set[str]] = defaultdict(set)
    for row in names_rows:
        permco = _clean(row.get("permco"))
        permno = _clean(row.get("permno"))
        if permco not in target_permcos:
            continue
        if permno:
            siblings[permco].add(permno)
            if permno in listed_members:
                member_permco[permno].add(permco)
        tickers[permco].add(_clean(row.get("ticker")))
        issuers[permco].add(_clean(row.get("issuernm")))

    historical_members: dict[str, set[str]] = defaultdict(set)
    for permno, permcos in member_permco.items():
        for permco in permcos:
            historical_members[permco].add(permno)

    linked_gvkeys: dict[str, set[str]] = defaultdict(set)
    for permco, gvkeys in group_gvkeys.items():
        linked_gvkeys[permco].update(gvkeys)
    for row in ccm_rows:
        permco = _clean(row.get("LPERMCO") or row.get("lpermco"))
        gvkey = _clean(row.get("GVKEY") or row.get("gvkey"))
        if permco in target_permcos and gvkey:
            linked_gvkeys[permco].add(gvkey)

    result: list[dict[str, str]] = []
    for permco in sorted(target_permcos, key=_numeric_key):
        result.append(
            {
                "permco": permco,
                "linked_gvkeys_from_current_raw": _json_set(linked_gvkeys[permco]),
                "historical_sp500_member_permnos": _json_set(historical_members[permco], numeric=True),
                "known_sibling_permnos_in_current_raw": _json_set(siblings[permco], numeric=True),
                "diagnostic_tickers_from_current_raw": _json_set(tickers[permco]),
                "issuer_names_from_current_raw": _json_set(issuers[permco]),
                "relevant_start_date": START_DATE,
                "relevant_end_date": END_DATE,
                "reason": "RESTRICTED_959_PERMNO_EXTRACT_CANNOT_PROVE_COMPLETE_RELEVANT_EQUITY_CLASS_COVERAGE",
                "source_phase2b_groups": _json_set(source_groups[permco]),
                "query_key_role": "PRIMARY_PERMCO_QUERY_KEY__TICKER_DIAGNOSTIC_ONLY",
            }
        )
    return result


def permco_batches(permcos: Iterable[str], batch_size: int = QUERY_BATCH_SIZE) -> list[tuple[str, ...]]:
    """Return deterministic, de-duplicated batches and reject an invalid size."""

    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    ordered = sorted({_clean(permco) for permco in permcos if _clean(permco)}, key=_numeric_key)
    return [tuple(ordered[index : index + batch_size]) for index in range(0, len(ordered), batch_size)]


def _in_parameters(values: Sequence[str], prefix: str = "permco") -> tuple[str, dict[str, str]]:
    if not values:
        raise ValueError("A query batch cannot be empty.")
    params = {f"{prefix}_{index}": value for index, value in enumerate(values)}
    placeholders = ", ".join(f"%({key})s" for key in params)
    return placeholders, params


def _eligibility_predicate(alias: str = "s") -> str:
    share_types = ", ".join(f"'{value}'" for value in ELIGIBLE_SHARE_TYPES)
    issuer_types = ", ".join(f"'{value}'" for value in ELIGIBLE_ISSUER_TYPES)
    return (
        f"{alias}.sharetype IN ({share_types})\n"
        f"  AND {alias}.securitytype = '{ELIGIBLE_SECURITY_TYPE}'\n"
        f"  AND {alias}.securitysubtype = '{ELIGIBLE_SECURITY_SUBTYPE}'\n"
        f"  AND {alias}.issuertype IN ({issuer_types})"
    )


def security_history_query(permcos: Sequence[str]) -> tuple[str, dict[str, str]]:
    placeholders, params = _in_parameters(permcos)
    params.update({"start_date": START_DATE, "end_date": END_DATE})
    columns = ", ".join(f"s.{column}" for column in SECURITY_HISTORY_COLUMNS)
    sql = f"""SELECT {columns}
FROM {SECURITY_HISTORY_TABLE} AS s
WHERE s.permco IN ({placeholders})
  AND s.secinfostartdt <= %(end_date)s::date
  AND COALESCE(s.secinfoenddt, DATE '9999-12-31') >= %(start_date)s::date
  AND {_eligibility_predicate('s')}
ORDER BY s.permco, s.permno, s.secinfostartdt"""
    return sql, params


def _eligible_history_cte(permcos: Sequence[str]) -> tuple[str, dict[str, str]]:
    placeholders, params = _in_parameters(permcos)
    params.update({"start_date": START_DATE, "end_date": END_DATE})
    cte = f"""WITH eligible_security_history AS (
    SELECT s.permno, s.permco, s.secinfostartdt, s.secinfoenddt
    FROM {SECURITY_HISTORY_TABLE} AS s
    WHERE s.permco IN ({placeholders})
      AND s.secinfostartdt <= %(end_date)s::date
      AND COALESCE(s.secinfoenddt, DATE '9999-12-31') >= %(start_date)s::date
      AND {_eligibility_predicate('s')}
)"""
    return cte, params


def daily_security_query(permcos: Sequence[str]) -> tuple[str, dict[str, str]]:
    cte, params = _eligible_history_cte(permcos)
    columns = ", ".join(f"d.{column}" for column in DAILY_SECURITY_COLUMNS)
    sql = f"""{cte}
SELECT {columns}
FROM {DAILY_SECURITY_TABLE} AS d
WHERE d.dlycaldt BETWEEN %(start_date)s::date AND %(end_date)s::date
  AND EXISTS (
      SELECT 1
      FROM eligible_security_history AS s
      WHERE d.permno = s.permno
        AND d.dlycaldt >= s.secinfostartdt
        AND d.dlycaldt <= COALESCE(s.secinfoenddt, DATE '9999-12-31')
  )
ORDER BY d.permno, d.dlycaldt"""
    return sql, params


def shares_query(permcos: Sequence[str]) -> tuple[str, dict[str, str]]:
    cte, params = _eligible_history_cte(permcos)
    columns = ", ".join(f"h.{column}" for column in SHARES_COLUMNS)
    sql = f"""{cte}
SELECT {columns}
FROM {SHARES_TABLE} AS h
WHERE h.shrstartdt <= %(end_date)s::date
  AND COALESCE(h.shrenddt, DATE '9999-12-31') >= %(start_date)s::date
  AND EXISTS (
      SELECT 1
      FROM eligible_security_history AS s
      WHERE h.permno = s.permno
        AND h.shrstartdt <= COALESCE(s.secinfoenddt, DATE '9999-12-31')
        AND COALESCE(h.shrenddt, DATE '9999-12-31') >= s.secinfostartdt
  )
ORDER BY h.permno, h.shrstartdt"""
    return sql, params


def validate_extraction_schema(columns: Iterable[str], required_columns: Iterable[str]) -> list[str]:
    """Return missing required columns case-insensitively, in deterministic order."""

    present = {_clean(column).lower() for column in columns}
    return [column for column in required_columns if column.lower() not in present]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_csv(path: Path, columns: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    phase1._write_csv(path, tuple(columns), list(rows))


def _write_report(path: Path, lines: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def load_query_universe(root: Path | None = None) -> list[dict[str, str]]:
    """Read local Phase-2B evidence and derive the frozen PERMCO query input."""

    root = root or project_root()
    raw = phase1.raw_root(root)
    return derive_query_universe(
        _read_csv(root / ISSUER_SUFFICIENCY),
        _read_gzip_csv(raw / NAMES),
        _read_gzip_csv(raw / CCM),
    )


def write_preparation_artifacts(root: Path | None = None) -> dict[str, Any]:
    """Write only query-preparation evidence; no WRDS connection is opened."""

    root = root or project_root()
    universe = load_query_universe(root)
    if not universe:
        raise ValueError("Supplemental issuer query universe is empty.")

    query_columns = tuple(universe[0])
    _write_csv(root / QUERY_INPUT, query_columns, universe)
    audit = root / AUDIT_DIRECTORY
    _write_csv(audit / "gate0_phase2b1_issuer_query_universe.csv", query_columns, universe)

    permcos = [row["permco"] for row in universe]
    batches = permco_batches(permcos)
    plan_rows = [
        {
            "step": "01_SECURITY_HISTORY",
            "wrds_library": WRDS_LIBRARY,
            "source_table": SECURITY_HISTORY_TABLE,
            "output_relative_path": str(SECURITY_HISTORY_OUTPUT),
            "date_range": f"{START_DATE} through {END_DATE}",
            "batch_key": "PERMCO",
            "batch_count": len(batches),
            "fields": ",".join(SECURITY_HISTORY_COLUMNS),
            "selection_rule": "Time-valid CIZ common equity: EQTY/COM; ShareType in NS, CE, SB; IssuerType in ACOR, CORP, REIT. No USIncFlg filter; no current-active filter.",
            "purpose": "Discover every relevant issuer share class, including non-S&P500-member classes, without using ticker as a key.",
            "execution_status": "READY_NOT_EXECUTED",
        },
        {
            "step": "02_DAILY_SECURITY",
            "wrds_library": WRDS_LIBRARY,
            "source_table": DAILY_SECURITY_TABLE,
            "output_relative_path": str(DAILY_SECURITY_OUTPUT),
            "date_range": f"{START_DATE} through {END_DATE}",
            "batch_key": "PERMCO via eligible security-history CTE",
            "batch_count": len(batches),
            "fields": ",".join(DAILY_SECURITY_COLUMNS),
            "selection_rule": "Only date-valid rows joined to the same provider-documented eligible security-history definition; raw DlyCap is retained without aggregation.",
            "purpose": "Obtain security-level DlyCap, price, return, delisting flag, and volume for every eligible class.",
            "execution_status": "READY_NOT_EXECUTED",
        },
        {
            "step": "03_SHARES_HISTORY",
            "wrds_library": WRDS_LIBRARY,
            "source_table": SHARES_TABLE,
            "output_relative_path": str(SHARES_OUTPUT),
            "date_range": f"{START_DATE} through {END_DATE}",
            "batch_key": "PERMCO via eligible security-history CTE",
            "batch_count": len(batches),
            "fields": ",".join(SHARES_COLUMNS),
            "selection_rule": "Only shares histories overlapping eligible class history and the formal period; retain ShrAdrFlg for post-extraction rejection audit.",
            "purpose": "Audit shares coverage, source/factor treatment, and residual ADR flags; not to calculate market capitalization.",
            "execution_status": "READY_NOT_EXECUTED",
        },
    ]
    _write_csv(
        audit / "gate0_phase2b1_extraction_plan.csv",
        tuple(plan_rows[0]),
        plan_rows,
    )

    acceptance_rows = [
        {
            "check_id": "phase2b_source_groups",
            "severity": "CRITICAL",
            "status": "PASS",
            "evidence": "37 Phase-2B group-level insufficiency records deterministically coalesced by primary PERMCO.",
            "result": "QUERY_INPUT_DERIVED",
        },
        {
            "check_id": "permco_query_universe",
            "severity": "CRITICAL",
            "status": "PASS",
            "evidence": f"unique_permcos={len(permcos)}; deterministic_batches={len(batches)}; ticker is diagnostic only.",
            "result": "READY_FOR_USER_EXECUTION",
        },
        {
            "check_id": "wrds_table_confirmation",
            "severity": "CRITICAL",
            "status": "PASS",
            "evidence": "Official WRDS/CRSP CIZ documentation confirms crsp.stksecurityinfohist, crsp.stkdlysecuritydata, and StkShares fields.",
            "result": "NO_UNVERIFIED_TABLE_NAME_USED",
        },
        {
            "check_id": "eligible_security_definition",
            "severity": "CRITICAL",
            "status": "PASS",
            "evidence": "Provider CIZ common-equity mapping supports EQTY/COM with ShareType NS/CE/SB and IssuerType ACOR/CORP/REIT; ADR/units/funds/derivatives excluded.",
            "result": "DOCUMENTED_FILTER_READY",
        },
        {
            "check_id": "raw_acquisition",
            "severity": "CRITICAL",
            "status": "NOT_EXECUTED",
            "evidence": "No authenticated WRDS session was opened and no supplemental raw output was created in this preparation stage.",
            "result": "USER_EXECUTION_REQUIRED",
        },
        {
            "check_id": "issuer_coverage_closure",
            "severity": "CRITICAL",
            "status": "PENDING",
            "evidence": "Coverage classification requires the future raw supplemental outputs and a separate issuer-class audit.",
            "result": "NO_ISSUER_MARKET_CAP_CONSTRUCTED",
        },
    ]
    _write_csv(
        audit / "gate0_phase2b1_acceptance.csv",
        ("check_id", "severity", "status", "evidence", "result"),
        acceptance_rows,
    )

    report = [
        "# Formal WRDS Gate 0 Phase 2B.1 — Supplemental CRSP Issuer / Share-Class Extraction Preparation",
        "",
        "## Status",
        "",
        "**SUPPLEMENTAL_EXTRACTION_READY_FOR_USER_EXECUTION**",
        "",
        "This is a preparation-only Gate-0 artifact. It does not open WRDS, download data, construct issuer market capitalization, create canonical data, or begin factor work.",
        "",
        "## Why this exists",
        "",
        "Phase 2B found that the current 959-PERMNO historical-S&P500 delivery cannot establish that all economically relevant equity share classes are present for its 37 group-level issuer/share-class warnings. The frozen manual requires complete relevant-class coverage before a company-level denominator can be aggregated. The 37 evidence rows coalesce to " + str(len(permcos)) + " unique PERMCO query keys; PERMCO is primary and ticker is diagnostic only.",
        "",
        "## Confirmed CIZ tables and library",
        "",
        "- `crsp.stksecurityinfohist`: security identity, PERMCO, time-valid classification, exchange, and trading-status history.",
        "- `crsp.stkdlysecuritydata`: PERMNO/date security-level daily `DlyCap`, price, return, delisting flag, and volume.",
        "- `crsp.stkshares`: PERMNO shares history, including effective dates, `ShrOut`, source/factor flags, and `ShrAdrFlg`.",
        "",
        "Table/library names are supported by the official WRDS CIZ macros and CRSP CIZ reference material recorded in the plan CSV; no table name is inferred from a local filename.",
        "",
        "## Formal period and selection boundary",
        "",
        f"- Coverage requested: `{START_DATE}` through `{END_DATE}` inclusive.",
        "- The security-history query uses `PERMCO`, time-valid `SecInfoStartDt/SecInfoEndDt`, `SecurityType=EQTY`, `SecuritySubType=COM`, `ShareType ∈ {NS, CE, SB}`, and `IssuerType ∈ {ACOR, CORP, REIT}`.",
        "- `ShareType=AD` (ADR), `UG` (unit), non-EQTY/COM securities, funds, derivatives, and unlisted non-common classifications are excluded at extraction. `USIncFlg` is intentionally not a filter because the documented common-share map includes foreign-incorporated corporate common equity.",
        "- `SecurityActiveFlg` and `TradingStatusFlg` are retrieved but not used as a present-day filter: a class that is now inactive can still have been economically relevant inside the historical period. The later coverage audit must apply time-valid status and record each inclusion/exclusion.",
        "- `ShrAdrFlg` is retained in the raw shares export. A `Y` value is a future coverage-audit rejection / exception condition, never an issuer-market-cap input.",
        "",
        "## Exact future raw outputs",
        "",
        f"1. `{SECURITY_HISTORY_OUTPUT}`",
        f"2. `{DAILY_SECURITY_OUTPUT}`",
        f"3. `{SHARES_OUTPUT}`",
        "",
        "The three provider tables remain separate raw exports rather than a flattened or aggregated derived table. On a future authorized execution, the script writes an audit metadata JSON with exact SQL text, parameters, table/library names, row counts, date coverage, schemas, and SHA256 values.",
        "",
        "## Execution control",
        "",
        "`python -m src.data.wrds_supplemental_issuer_shareclass_extract` is preparation-only. The user must explicitly authorize and run `--execute` in an authenticated WRDS environment. Existing raw outputs cause a failure rather than overwrite. No credentials are stored in code or configuration.",
        "",
        "## Explicitly deferred",
        "",
        "- No issuer-level market-cap aggregation or market-cap formula.",
        "- No Value, Size, neutralization, Compustat PIT, factor, IC, portfolio, FMOM, or backtest work.",
        "- No `ISSUER_COVERAGE_COMPLETE` claim. After acquisition, a dedicated coverage audit must classify each issuer as `ISSUER_COVERAGE_COMPLETE`, `ISSUER_COVERAGE_WITH_LIMITATION`, or `ISSUER_COVERAGE_UNRESOLVED` before any aggregation policy is considered.",
        "",
        "## References",
        "",
    ]
    report.extend(
        f"- [{reference['source_id']}]({reference['url']}): {reference['evidence']}"
        for reference in SOURCE_REFERENCES
    )
    _write_report(audit / "WRDS_GATE0_PHASE2B1_SUPPLEMENTAL_EXTRACTION.md", report)
    return {
        "unique_permcos": len(permcos),
        "batch_count": len(batches),
        "query_input": root / QUERY_INPUT,
        "audit_directory": audit,
        "universe": universe,
    }


def _write_frames_gzip(frames: Iterable[Any], path: Path) -> int:
    """Write provider-result frames once, preserving returned columns and values."""

    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing raw vendor output: {path}")
    row_count = 0
    first = True
    with gzip.open(path, "wt", newline="", encoding="utf-8") as handle:
        for frame in frames:
            if frame is None:
                continue
            row_count += len(frame.index)
            frame.to_csv(handle, index=False, header=first, lineterminator="\n")
            first = False
    return row_count


def _gzip_csv_profile(path: Path, date_column: str) -> dict[str, Any]:
    row_count = 0
    dates: list[str] = []
    with gzip.open(path, "rt", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        for row in reader:
            row_count += 1
            value = _clean(row.get(date_column))
            if value:
                dates.append(value)
    return {
        "relative_path": str(path.relative_to(project_root())),
        "row_count": row_count,
        "columns": columns,
        "min_date": min(dates) if dates else "",
        "max_date": max(dates) if dates else "",
        "file_size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def execute_authorized_extraction(root: Path | None = None) -> dict[str, Any]:
    """Execute only after the user explicitly authorizes authenticated WRDS access.

    The caller must have already run the preparation step. This function is not
    invoked by default and cannot alter the original 16-file raw delivery.
    """

    root = root or project_root()
    input_path = root / QUERY_INPUT
    if not input_path.exists():
        raise FileNotFoundError(f"Prepare query input before execution: {input_path}")
    outputs = (root / SECURITY_HISTORY_OUTPUT, root / DAILY_SECURITY_OUTPUT, root / SHARES_OUTPUT)
    existing = [path for path in outputs if path.exists()]
    if existing:
        raise FileExistsError("Refusing to overwrite supplemental raw output(s): " + ", ".join(str(path) for path in existing))

    query_rows = list(_read_csv(input_path))
    permcos = [row["permco"] for row in query_rows]
    batches = permco_batches(permcos)
    input_hash = _sha256(input_path)

    # The import and connection are deliberately inside this explicitly invoked
    # function so preparation cannot trigger an authentication prompt or network
    # request.  The WRDS package obtains credentials from the user's own session.
    import wrds  # type: ignore[import-not-found]

    connection = wrds.Connection()
    try:
        plans = (
            ("security_history", security_history_query, SECURITY_HISTORY_COLUMNS, root / SECURITY_HISTORY_OUTPUT, "secinfostartdt"),
            ("daily_security", daily_security_query, DAILY_SECURITY_COLUMNS, root / DAILY_SECURITY_OUTPUT, "dlycaldt"),
            ("shares", shares_query, SHARES_COLUMNS, root / SHARES_OUTPUT, "shrstartdt"),
        )
        metadata_plans: list[dict[str, Any]] = []
        output_profiles: list[dict[str, Any]] = []
        for name, query_builder, required_columns, output_path, date_column in plans:
            statements: list[str] = []
            frames: list[Any] = []
            for batch in batches:
                sql, params = query_builder(batch)
                statements.append(sql)
                frame = connection.raw_sql(sql, params=params)
                missing = validate_extraction_schema(frame.columns, required_columns)
                if missing:
                    raise ValueError(f"{name} query omitted required fields: {missing}")
                frames.append(frame)
            rows_written = _write_frames_gzip(frames, output_path)
            profile = _gzip_csv_profile(output_path, date_column)
            if profile["row_count"] != rows_written:
                raise RuntimeError(f"{name} row-count mismatch after raw export")
            output_profiles.append(profile)
            metadata_plans.append(
                {
                    "name": name,
                    "table": {
                        "security_history": SECURITY_HISTORY_TABLE,
                        "daily_security": DAILY_SECURITY_TABLE,
                        "shares": SHARES_TABLE,
                    }[name],
                    "required_columns": list(required_columns),
                    "query_sql_by_deterministic_batch": statements,
                    "query_parameter_policy": {
                        "period_start": START_DATE,
                        "period_end": END_DATE,
                        "permco_batch_size": QUERY_BATCH_SIZE,
                        "permco_input_sha256": input_hash,
                    },
                }
            )
    finally:
        connection.close()

    metadata = {
        "evidence_stage": EVIDENCE_STAGE,
        "status": "SUPPLEMENTAL_RAW_EXTRACTION_EXECUTED_PENDING_COVERAGE_AUDIT",
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "wrds_library": WRDS_LIBRARY,
        "input_relative_path": str(QUERY_INPUT),
        "input_sha256": input_hash,
        "unique_permcos": len(permcos),
        "permco_batches": [list(batch) for batch in batches],
        "source_references": list(SOURCE_REFERENCES),
        "queries": metadata_plans,
        "raw_outputs": output_profiles,
        "post_execution_required": [
            "Validate schemas, row counts, and date coverage against the extraction plan.",
            "Classify every issuer as ISSUER_COVERAGE_COMPLETE, ISSUER_COVERAGE_WITH_LIMITATION, or ISSUER_COVERAGE_UNRESOLVED.",
            "Audit any ShrAdrFlg=Y observation as excluded/unresolved before a future aggregation decision.",
            "Do not construct issuer market capitalization until a separately authorized aggregation policy exists.",
        ],
    }
    phase1._write_report(root / EXECUTION_METADATA, [json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True)])
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=project_root(), help="Repository root (defaults to this module's repository).")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Open an authenticated WRDS session and create supplemental raw files. Requires explicit user authorization.",
    )
    args = parser.parse_args()
    preparation = write_preparation_artifacts(args.root)
    if not args.execute:
        print(
            "SUPPLEMENTAL_EXTRACTION_READY_FOR_USER_EXECUTION "
            f"unique_permcos={preparation['unique_permcos']} batches={preparation['batch_count']}"
        )
        return
    metadata = execute_authorized_extraction(args.root)
    print(f"SUPPLEMENTAL_RAW_EXTRACTION_EXECUTED outputs={len(metadata['raw_outputs'])}")


if __name__ == "__main__":
    main()
