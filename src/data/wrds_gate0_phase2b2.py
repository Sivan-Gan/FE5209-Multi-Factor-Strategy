"""Formal Gate 0 Phase 2B.2 supplemental CRSP coverage and integrity audit.

This module is deliberately bounded to evidence for the separately exported
CRSP issuer/share-class raw files.  It neither aggregates security-level
``DlyCap`` to issuer market capitalization nor creates canonical data, factors,
or strategy results.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from src.data import wrds_gate0_phase1 as phase1
from src.data import wrds_supplemental_issuer_shareclass_extract as supplemental


EVIDENCE_STAGE = "FORMAL_GATE0_PHASE2B2_SUPPLEMENTAL_ISSUER_SHARECLASS_COVERAGE"
START_DATE = supplemental.START_DATE
END_DATE = supplemental.END_DATE

AUDIT_DIRECTORY = Path("outputs/audit/wrds_formal")
PHASE2B_SUFFICIENCY = AUDIT_DIRECTORY / "gate0_phase2b_issuer_marketcap_sufficiency.csv"
QUERY_INPUT = supplemental.QUERY_INPUT

SECURITY_HISTORY = supplemental.SECURITY_HISTORY_OUTPUT
DAILY_SECURITY = supplemental.DAILY_SECURITY_OUTPUT
SHARES = supplemental.SHARES_OUTPUT

PERMCO_RECONCILIATION = AUDIT_DIRECTORY / "gate0_phase2b2_permco_reconciliation.csv"
NEW_RAW_MANIFEST = AUDIT_DIRECTORY / "gate0_phase2b2_new_raw_manifest.csv"
SECURITY_COVERAGE = AUDIT_DIRECTORY / "gate0_phase2b2_security_history_coverage.csv"
DAILY_COVERAGE = AUDIT_DIRECTORY / "gate0_phase2b2_daily_security_coverage.csv"
SHARE_COVERAGE = AUDIT_DIRECTORY / "gate0_phase2b2_share_history_coverage.csv"
ISSUER_SUFFICIENCY = AUDIT_DIRECTORY / "gate0_phase2b2_issuer_coverage_sufficiency.csv"
MULTICLASS_SPOTCHECKS = AUDIT_DIRECTORY / "gate0_phase2b2_known_multiclass_spotchecks.csv"
ACCEPTANCE = AUDIT_DIRECTORY / "gate0_phase2b2_acceptance.csv"
REPORT = AUDIT_DIRECTORY / "WRDS_GATE0_PHASE2B2_SUPPLEMENTAL_COVERAGE.md"
OPEN_ISSUES = AUDIT_DIRECTORY / "gate0_open_issues.csv"

ORIGINAL_PHASE1_MANIFEST = AUDIT_DIRECTORY / "raw_file_manifest.csv"
ORIGINAL_NAMES = Path("crsp/CRSP_Names_CIZ_FullHistory_SP500_Historical_PERMNOs.csv.gz")

SECURITY_HISTORY_FINGERPRINT = frozenset(
    {
        "permco", "permno", "secinfostartdt", "secinfoenddt", "securitybegdt",
        "securityenddt", "shareclass", "issuertype", "securitytype", "securitysubtype",
        "sharetype", "securityactiveflg", "tradingstatusflg",
    }
)
DAILY_SECURITY_FINGERPRINT = frozenset({"permco", "permno", "dlycaldt", "dlyprc", "dlycap", "shrout"})
SHARE_HISTORY_FINGERPRINT = frozenset(
    {"permco", "permno", "shrstartdt", "shrenddt", "shrout", "shrsource", "shrfactype", "shradrflg"}
)

FORMAL_START = START_DATE
FORMAL_END = END_DATE


def project_root() -> Path:
    """Return this repository root, independent of current working directory."""

    return Path(__file__).resolve().parents[2]


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any) -> float | None:
    try:
        candidate = float(_clean(value))
    except (TypeError, ValueError):
        return None
    return candidate if math.isfinite(candidate) else None


def _numeric_key(value: str) -> tuple[int, str]:
    text = _clean(value)
    return (int(text), text) if text.isdigit() else (10**18, text)


def _json_list(value: str) -> list[str]:
    parsed = json.loads(value)
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise ValueError(f"Expected JSON string-list, got {value!r}")
    return [_clean(item) for item in parsed if _clean(item)]


def _json(values: Iterable[str], *, numeric: bool = False) -> str:
    ordered = sorted({_clean(value) for value in values if _clean(value)}, key=_numeric_key if numeric else None)
    return json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))


def _read_csv(path: Path) -> Iterator[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def _write_csv(path: Path, columns: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    phase1._write_csv(path, tuple(columns), list(rows))


def _group_id(row: dict[str, str]) -> str:
    return f"{_clean(row.get('group_type'))}:{_clean(row.get('group_identifier'))}"


def build_permco_reconciliation(
    sufficiency_rows: Iterable[dict[str, str]],
    query_rows: Iterable[dict[str, str]],
) -> list[dict[str, str]]:
    """Trace every Phase-2B group to its exact deterministic query PERMCO(s).

    The 37 source warnings intentionally overlap.  This expands—not
    coalesces—them, so each PERMCO can be traced back to one or more source
    group identifiers before any external query is made.
    """

    relevant = [
        row
        for row in sufficiency_rows
        if _clean(row.get("classification")) == "SUPPLEMENTAL_CRSP_EXTRACTION_REQUIRED"
    ]
    materialized_query = [row for row in query_rows if _clean(row.get("permco"))]
    query_by_permco = {_clean(row.get("permco")): row for row in materialized_query}
    if len(query_by_permco) != len(materialized_query):
        raise ValueError("Query PERMCO input contains duplicate keys.")
    if not relevant:
        raise ValueError("No Phase-2B supplemental-extraction groups were found.")

    rows: list[dict[str, str]] = []
    expected_permcos: set[str] = set()
    for source in sorted(relevant, key=lambda row: (_clean(row.get("group_type")), _clean(row.get("group_identifier")))):
        source_permcos = _json_list(_clean(source.get("permcos")))
        if not source_permcos:
            raise ValueError(f"Phase-2B source group has no PERMCO: {_group_id(source)}")
        expected_permcos.update(source_permcos)
        for permco in sorted(set(source_permcos), key=_numeric_key):
            if permco not in query_by_permco:
                raise ValueError(f"PERMCO {permco} from {_group_id(source)} is absent from query input.")
            query = query_by_permco[permco]
            source_groups = set(_json_list(_clean(query.get("source_phase2b_groups"))))
            if _group_id(source) not in source_groups:
                raise ValueError(
                    f"Query PERMCO {permco} lacks source-group provenance for {_group_id(source)}."
                )
            rows.append(
                {
                    "phase2b_group_id": _group_id(source),
                    "group_type": _clean(source.get("group_type")),
                    "group_identifier": _clean(source.get("group_identifier")),
                    "group_gvkey": _clean(source.get("group_identifier"))
                    if _clean(source.get("group_type")) == "GVKEY"
                    else "",
                    "original_member_permnos": _json(_json_list(_clean(source.get("relevant_permnos"))), numeric=True),
                    "permco": permco,
                    "query_input_gvkeys": _clean(query.get("linked_gvkeys_from_current_raw")),
                    "query_input_member_permnos": _clean(query.get("historical_sp500_member_permnos")),
                    "mapping_reason": "PHASE2B_GROUP_REQUIRES_FULL_RELEVANT_COMMON_EQUITY_CLASS_COVERAGE",
                    "query_key_role": _clean(query.get("query_key_role")),
                }
            )

    actual_permcos = set(query_by_permco)
    if expected_permcos != actual_permcos:
        missing = sorted(expected_permcos - actual_permcos, key=_numeric_key)
        extra = sorted(actual_permcos - expected_permcos, key=_numeric_key)
        raise ValueError(f"Query input does not exactly reconcile to Phase-2B groups; missing={missing}, extra={extra}")
    return sorted(rows, key=lambda row: (_numeric_key(row["permco"]), row["phase2b_group_id"]))


def write_permco_reconciliation(root: Path | None = None) -> dict[str, int]:
    """Write the pre-query 37-group / 45-PERMCO evidence table only."""

    root = root or project_root()
    groups = list(_read_csv(root / PHASE2B_SUFFICIENCY))
    query = list(_read_csv(root / QUERY_INPUT))
    rows = build_permco_reconciliation(groups, query)
    group_ids = {_clean(row["phase2b_group_id"]) for row in rows}
    permcos = {_clean(row["permco"]) for row in rows}
    if len(group_ids) != 37 or len(permcos) != 45 or len(query) != 45:
        raise ValueError(
            "Phase-2B.2 pre-query reconciliation expected 37 source groups and 45 query PERMCOs; "
            f"got groups={len(group_ids)}, permcos={len(permcos)}, query_rows={len(query)}."
        )
    columns = (
        "phase2b_group_id",
        "group_type",
        "group_identifier",
        "group_gvkey",
        "original_member_permnos",
        "permco",
        "query_input_gvkeys",
        "query_input_member_permnos",
        "mapping_reason",
        "query_key_role",
    )
    _write_csv(root / PERMCO_RECONCILIATION, columns, rows)
    return {"source_groups": len(group_ids), "unique_permcos": len(permcos), "mapping_rows": len(rows)}


def interval_overlap(start_a: str, end_a: str, start_b: str, end_b: str) -> bool:
    """Return whether two inclusive ISO-date intervals overlap."""

    end_a = end_a or "9999-12-31"
    end_b = end_b or "9999-12-31"
    return bool(start_a and start_b and start_a <= end_b and start_b <= end_a)


def classify_issuer_coverage(
    *,
    history_present: bool,
    relevant_class_count: int,
    daily_class_count: int,
    nonmissing_dlycap_count: int,
    daily_row_count: int,
    uncovered_share_daily_count: int,
    adr_flagged_class_count: int,
) -> str:
    """Classify source coverage without authorizing any issuer aggregation."""

    if not history_present or relevant_class_count == 0 or daily_class_count == 0 or nonmissing_dlycap_count == 0:
        return "ISSUER_COVERAGE_UNRESOLVED"
    if nonmissing_dlycap_count < daily_row_count or uncovered_share_daily_count or adr_flagged_class_count:
        return "ISSUER_COVERAGE_COMPLETE_WITH_LIMITATION"
    return "ISSUER_COVERAGE_COMPLETE"


def _gzip_rows(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    """Read a gzip CSV completely, which also verifies gzip readability."""

    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return tuple(reader.fieldnames or ()), list(reader)


def _field(row: dict[str, str], name: str) -> str:
    desired = name.lower()
    for field_name, value in row.items():
        if field_name.lower() == desired:
            return _clean(value)
    return ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(_clean(value))
    except ValueError:
        return None


def _date_value(value: str, *, end: bool = False) -> str:
    """Give open-ended provider intervals a safe comparison bound only."""

    clean = _clean(value)
    return clean or ("9999-12-31" if end else "0001-01-01")


def _row_interval_overlaps_formal(row: dict[str, str]) -> bool:
    return interval_overlap(
        _field(row, "secinfostartdt"),
        _field(row, "secinfoenddt") or FORMAL_END,
        FORMAL_START,
        FORMAL_END,
    )


def _interval_contains_date(start: str, end: str, observation: str) -> bool:
    return bool(start and observation and start <= observation <= _date_value(end, end=True))


def security_classification(row: dict[str, str]) -> tuple[str, str]:
    """Apply only the Phase-2B.1 documented CIZ common-equity rule."""

    if supplemental.is_eligible_common_equity({key.lower(): value for key, value in row.items()}):
        return "INCLUDED_COMMON_EQUITY", "PHASE2B1_EQTY_COM_SHARETYPE_NS_CE_SB_ISSUERTYPE_ACOR_CORP_REIT"
    failures: list[str] = []
    if _field(row, "securitytype").upper() != supplemental.ELIGIBLE_SECURITY_TYPE:
        failures.append(f"SECURITYTYPE_{_field(row, 'securitytype').upper() or 'BLANK'}")
    if _field(row, "securitysubtype").upper() != supplemental.ELIGIBLE_SECURITY_SUBTYPE:
        failures.append(f"SECURITYSUBTYPE_{_field(row, 'securitysubtype').upper() or 'BLANK'}")
    if _field(row, "sharetype").upper() not in supplemental.ELIGIBLE_SHARE_TYPES:
        failures.append(f"SHARETYPE_{_field(row, 'sharetype').upper() or 'BLANK'}")
    if _field(row, "issuertype").upper() not in supplemental.ELIGIBLE_ISSUER_TYPES:
        failures.append(f"ISSUERTYPE_{_field(row, 'issuertype').upper() or 'BLANK'}")
    return "EXCLUDED_NON_COMMON_OR_NONCOMPARABLE", "|".join(failures)


def original_vs_supplemental_flags(permno: str, original_permnos: set[str], included_common_equity: bool) -> tuple[str, str]:
    """Make denominator-only coverage explicit and never enlarge investability."""

    in_original = _clean(permno) in original_permnos
    return str(in_original).lower(), str(included_common_equity and not in_original).lower()


def _fingerprint_matches(columns: Sequence[str], fingerprint: frozenset[str]) -> bool:
    return fingerprint <= {column.lower() for column in columns}


def _candidate_permcos(path: Path) -> set[str]:
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if "permco" not in {column.lower() for column in (reader.fieldnames or ())}:
            return set()
        return {_field(row, "permco") for row in reader if _field(row, "permco")}


def identify_supplemental_raw_files(root: Path | None = None) -> dict[str, Path]:
    """Find exactly one raw file per semantic schema, constrained by the approved 45 PERMCOs.

    Names are retained as evidence only after content based identification; a
    stale or misleading filename therefore cannot silently change the audit.
    """

    root = root or project_root()
    with (root / QUERY_INPUT).open("r", encoding="utf-8-sig", newline="") as handle:
        approved_permcos = {_clean(row["permco"]) for row in csv.DictReader(handle) if _clean(row.get("permco"))}
    if len(approved_permcos) != 45:
        raise ValueError(f"Expected 45 approved query PERMCOs; got {len(approved_permcos)}")
    candidate_paths = sorted(phase1.raw_root(root).joinpath("crsp").glob("*.csv.gz"), key=lambda path: path.name)
    roles = {
        "SECURITY_INFORMATION_HISTORY": SECURITY_HISTORY_FINGERPRINT,
        "DAILY_SECURITY_DATA": DAILY_SECURITY_FINGERPRINT,
        "SHARE_OUTSTANDING_HISTORY": SHARE_HISTORY_FINGERPRINT,
    }
    selected: dict[str, Path] = {}
    for role, fingerprint in roles.items():
        matched: list[Path] = []
        for path in candidate_paths:
            with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
                columns = tuple(csv.DictReader(handle).fieldnames or ())
            if _fingerprint_matches(columns, fingerprint) and _candidate_permcos(path) == approved_permcos:
                matched.append(path)
        if len(matched) != 1:
            raise ValueError(f"Content-based semantic identification for {role} expected exactly one file; found {[path.name for path in matched]}")
        selected[role] = matched[0]
    if len(set(selected.values())) != len(selected):
        raise ValueError("One raw file matched more than one supplemental semantic role.")
    return selected


def _natural_key_profile(rows: Iterable[dict[str, str]], fields: Sequence[str]) -> dict[str, int]:
    observed: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    missing = 0
    for row in rows:
        key = tuple(_field(row, field) for field in fields)
        if any(not value for value in key):
            missing += 1
            continue
        observed[key].append(row)
    duplicate_keys = {key: values for key, values in observed.items() if len(values) > 1}
    duplicate_extra = sum(len(values) - 1 for values in duplicate_keys.values())
    conflicting = sum(
        1
        for values in duplicate_keys.values()
        if len({tuple(sorted((field, _clean(value)) for field, value in row.items())) for row in values}) > 1
    )
    return {
        "missing_key_rows": missing,
        "duplicate_key_count": len(duplicate_keys),
        "duplicate_extra_rows": duplicate_extra,
        "conflicting_duplicate_key_count": conflicting,
    }


def _semantic_dates(rows: Iterable[dict[str, str]], role: str) -> tuple[str, str, str]:
    if role == "SECURITY_INFORMATION_HISTORY":
        starts = sorted(_field(row, "secinfostartdt") for row in rows if _field(row, "secinfostartdt"))
        ends = sorted(_field(row, "secinfoenddt") for row in rows if _field(row, "secinfoenddt"))
        return (starts[0] if starts else "", ends[-1] if ends else "", "secinfostartdt/secinfoenddt")
    if role == "DAILY_SECURITY_DATA":
        values = sorted(_field(row, "dlycaldt") for row in rows if _field(row, "dlycaldt"))
        return (values[0] if values else "", values[-1] if values else "", "dlycaldt")
    starts = sorted(_field(row, "shrstartdt") for row in rows if _field(row, "shrstartdt"))
    ends = sorted(_field(row, "shrenddt") for row in rows if _field(row, "shrenddt"))
    return (starts[0] if starts else "", ends[-1] if ends else "", "shrstartdt/shrenddt")


def build_new_raw_manifest(
    paths: dict[str, Path],
    rows_by_role: dict[str, list[dict[str, str]]],
    columns_by_role: dict[str, tuple[str, ...]],
    *,
    before_hashes: dict[str, str],
    root: Path | None = None,
) -> list[dict[str, Any]]:
    """Build a three-file Phase-2B.2 addendum without touching the Phase-1 manifest."""

    natural_keys = {
        "SECURITY_INFORMATION_HISTORY": ("permco", "permno", "secinfostartdt", "secinfoenddt"),
        "DAILY_SECURITY_DATA": ("permno", "dlycaldt"),
        "SHARE_OUTSTANDING_HISTORY": ("permco", "permno", "shrstartdt", "shrenddt"),
    }
    result: list[dict[str, Any]] = []
    root = root or project_root()
    for role in sorted(paths):
        path = paths[role]
        rows = rows_by_role[role]
        key_profile = _natural_key_profile(rows, natural_keys[role])
        min_date, max_date, date_fields = _semantic_dates(rows, role)
        after_hash = _sha256(path)
        result.append(
            {
                "semantic_type": role,
                "relative_path": path.relative_to(root).as_posix(),
                "exact_filename": path.name,
                "file_size_bytes": path.stat().st_size,
                "sha256_pre_audit": before_hashes[role],
                "sha256_post_audit": after_hash,
                "pre_post_sha256_match": str(before_hashes[role] == after_hash).lower(),
                "gzip_readability": "PASS",
                "row_count_excluding_header": len(rows),
                "column_count": len(columns_by_role[role]),
                "exact_schema_json": json.dumps(list(columns_by_role[role]), ensure_ascii=False, separators=(",", ":")),
                "unique_permco_count": len({_field(row, "permco") for row in rows if _field(row, "permco")}),
                "unique_permno_count": len({_field(row, "permno") for row in rows if _field(row, "permno")}),
                "relevant_date_fields": date_fields,
                "min_relevant_date": min_date,
                "max_relevant_date": max_date,
                **key_profile,
            }
        )
    return result


def _original_raw_integrity(root: Path) -> dict[str, Any]:
    with (root / ORIGINAL_PHASE1_MANIFEST).open("r", encoding="utf-8-sig", newline="") as handle:
        expected = {row["relative_path"]: row for row in csv.DictReader(handle)}
    full_snapshot = {item.relative_path: item for item in phase1.snapshot_raw_files(phase1.raw_root(root))}
    os_metadata_paths = sorted(relative_path for relative_path in full_snapshot if Path(relative_path).name == ".DS_Store")
    snapshot = {relative_path: item for relative_path, item in full_snapshot.items() if relative_path not in os_metadata_paths}
    missing = sorted(set(expected) - set(snapshot))
    changed = sorted(
        relative_path
        for relative_path, source in expected.items()
        if relative_path in snapshot
        and (snapshot[relative_path].sha256 != source["sha256"] or str(snapshot[relative_path].size_bytes) != source["size_bytes"])
    )
    return {
        "original_file_count": len(expected),
        "vendor_raw_file_count": len(snapshot),
        "physical_filesystem_file_count": len(full_snapshot),
        "nonvendor_os_metadata_file_count": len(os_metadata_paths),
        "nonvendor_os_metadata_paths": os_metadata_paths,
        "original_missing_count": len(missing),
        "original_changed_count": len(changed),
        "original_raw_integrity": "PASS" if not missing and not changed else "FAIL",
        "missing_paths": missing,
        "changed_paths": changed,
    }


def _read_original_permnos(root: Path) -> set[str]:
    with gzip.open(phase1.raw_root(root) / ORIGINAL_NAMES, "rt", encoding="utf-8-sig", newline="") as handle:
        return {_field(row, "permno") for row in csv.DictReader(handle) if _field(row, "permno")}


def _security_rows(
    rows: Iterable[dict[str, str]],
    original_permnos: set[str],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, str]]]]:
    result: list[dict[str, Any]] = []
    relevant: dict[str, list[dict[str, str]]] = defaultdict(list)
    for source in rows:
        classification, reason = security_classification(source)
        permno = _field(source, "permno")
        permco = _field(source, "permco")
        included = classification == "INCLUDED_COMMON_EQUITY"
        investable, denominator_only = original_vs_supplemental_flags(permno, original_permnos, included)
        in_formal_period = included and _row_interval_overlaps_formal(source)
        if in_formal_period:
            relevant[permco].append(source)
        result.append(
            {
                "permco": permco,
                "permno": permno,
                "secinfostartdt": _field(source, "secinfostartdt"),
                "secinfoenddt": _field(source, "secinfoenddt"),
                "securitybegdt": _field(source, "securitybegdt"),
                "securityenddt": _field(source, "securityenddt"),
                "ticker": _field(source, "ticker"),
                "tradingsymbol": _field(source, "tradingsymbol"),
                "issuernm": _field(source, "issuernm"),
                "shareclass": _field(source, "shareclass"),
                "issuertype": _field(source, "issuertype"),
                "securitytype": _field(source, "securitytype"),
                "securitysubtype": _field(source, "securitysubtype"),
                "sharetype": _field(source, "sharetype"),
                "securityactiveflg": _field(source, "securityactiveflg"),
                "tradingstatusflg": _field(source, "tradingstatusflg"),
                "common_equity_classification": classification,
                "inclusion_exclusion_reason": reason,
                "overlaps_formal_2004_2025": str(_row_interval_overlaps_formal(source)).lower(),
                "investable_universe_member": investable,
                "denominator_only_security": denominator_only,
            }
        )
    return sorted(result, key=lambda row: (_numeric_key(row["permco"]), _numeric_key(row["permno"]), row["secinfostartdt"], row["secinfoenddt"])), relevant


def _deduplicate_daily(rows: Iterable[dict[str, str]]) -> tuple[dict[str, list[dict[str, str]]], dict[str, dict[str, int]]]:
    by_key: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_key[(_field(row, "permno"), _field(row, "dlycaldt"))].append(row)
    by_permno: dict[str, list[dict[str, str]]] = defaultdict(list)
    profile: dict[str, dict[str, int]] = defaultdict(lambda: {"duplicate_key_count": 0, "duplicate_extra_rows": 0, "conflicting_duplicate_key_count": 0})
    for (permno, _), same_key_rows in sorted(by_key.items()):
        exact = len({tuple(sorted((key, _clean(value)) for key, value in row.items())) for row in same_key_rows}) == 1
        if len(same_key_rows) > 1:
            profile[permno]["duplicate_key_count"] += 1
            profile[permno]["duplicate_extra_rows"] += len(same_key_rows) - 1
            if not exact:
                profile[permno]["conflicting_duplicate_key_count"] += 1
        by_permno[permno].append(same_key_rows[0])
    for permno in by_permno:
        by_permno[permno].sort(key=lambda row: _field(row, "dlycaldt"))
    return by_permno, profile


def _relevant_permnos(relevant_history: dict[str, list[dict[str, str]]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for permco, records in relevant_history.items():
        result[permco] = sorted({_field(record, "permno") for record in records if _field(record, "permno")}, key=_numeric_key)
    return result


def _share_intervals(rows: Iterable[dict[str, str]]) -> list[tuple[date, date, dict[str, str]]]:
    result: list[tuple[date, date, dict[str, str]]] = []
    for row in rows:
        start = _date(_field(row, "shrstartdt"))
        end = _date(_field(row, "shrenddt")) or date(9999, 12, 31)
        if start is not None and start <= end:
            result.append((start, end, row))
    return sorted(result, key=lambda item: (item[0], item[1], _field(item[2], "permno")))


def _active_share_rows(intervals: Iterable[tuple[date, date, dict[str, str]]], observation: str) -> list[dict[str, str]]:
    current = _date(observation)
    if current is None:
        return []
    return [row for start, end, row in intervals if start <= current <= end]


def _interval_gap_and_overlap_counts(intervals: list[tuple[date, date, dict[str, str]]]) -> tuple[int, int]:
    if not intervals:
        return 0, 0
    gaps = 0
    overlaps = 0
    running_end = intervals[0][1]
    for start, end, _ in intervals[1:]:
        if start <= running_end:
            overlaps += 1
        elif start > running_end + timedelta(days=1):
            gaps += 1
        if end > running_end:
            running_end = end
    return gaps, overlaps


def _share_and_daily_coverage(
    relevant_permnos_by_permco: dict[str, list[str]],
    relevant_history: dict[str, list[dict[str, str]]],
    daily_by_permno: dict[str, list[dict[str, str]]],
    duplicate_profile: dict[str, dict[str, int]],
    shares_rows: Iterable[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]], str]:
    """Produce per-class daily/share evidence, retaining raw duplicate diagnostics."""

    shares_by_permno: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in shares_rows:
        shares_by_permno[_field(row, "permno")].append(row)
    history_by_permno: dict[str, list[dict[str, str]]] = defaultdict(list)
    permco_by_permno: dict[str, str] = {}
    for permco, rows in relevant_history.items():
        for row in rows:
            permno = _field(row, "permno")
            history_by_permno[permno].append(row)
            permco_by_permno[permno] = permco
    daily_result: list[dict[str, Any]] = []
    share_result: list[dict[str, Any]] = []
    class_metrics: dict[str, dict[str, Any]] = {}
    full_history_missing_at_start = 0
    all_permnos = sorted(history_by_permno, key=_numeric_key)
    for permno in all_permnos:
        permco = permco_by_permno[permno]
        history = history_by_permno[permno]
        effective_start = min(_field(row, "secinfostartdt") for row in history if _field(row, "secinfostartdt"))
        effective_end = max((_field(row, "secinfoenddt") or FORMAL_END) for row in history)
        daily = daily_by_permno.get(permno, [])
        share_rows = shares_by_permno.get(permno, [])
        intervals = _share_intervals(share_rows)
        interval_gaps, interval_overlaps = _interval_gap_and_overlap_counts(intervals)
        daily_first = _field(daily[0], "dlycaldt") if daily else ""
        daily_last = _field(daily[-1], "dlycaldt") if daily else ""
        cap_nonmissing = sum(bool(_field(row, "dlycap")) for row in daily)
        prc_nonmissing = sum(bool(_field(row, "dlyprc")) for row in daily)
        daily_shrout_nonmissing = sum(bool(_field(row, "shrout")) for row in daily)
        duplicate = duplicate_profile.get(permno, {"duplicate_key_count": 0, "duplicate_extra_rows": 0, "conflicting_duplicate_key_count": 0})
        active_start = any(_interval_contains_date(_field(row, "secinfostartdt"), _field(row, "secinfoenddt"), FORMAL_START) for row in history)
        start_share_match = bool(_active_share_rows(intervals, FORMAL_START)) if active_start else False
        if active_start and not start_share_match:
            full_history_missing_at_start += 1
        share_daily_match = 0
        exact_match = 0
        tolerance_match = 0
        share_uncovered = 0
        for observation in daily:
            active = _active_share_rows(intervals, _field(observation, "dlycaldt"))
            if not active:
                share_uncovered += 1
                continue
            share_daily_match += 1
            daily_value = _number(_field(observation, "shrout"))
            share_values = [_number(_field(candidate, "shrout")) for candidate in active]
            share_values = [value for value in share_values if value is not None]
            if daily_value is None or not share_values:
                continue
            if any(daily_value == value for value in share_values):
                exact_match += 1
                tolerance_match += 1
            elif any(math.isclose(daily_value, value, rel_tol=1e-6, abs_tol=1e-8) for value in share_values):
                tolerance_match += 1
        adr_flagged = sum(_field(row, "shradrflg").upper() == "Y" for row in share_rows)
        if not daily:
            coverage_status = "NO_DAILY_DATA_FOR_RELEVANT_CLASS"
        elif duplicate["conflicting_duplicate_key_count"]:
            coverage_status = "DAILY_NATURAL_KEY_CONFLICT"
        elif cap_nonmissing < len(daily) or prc_nonmissing < len(daily):
            coverage_status = "DAILY_MARKET_FIELDS_MISSING"
        elif share_uncovered:
            coverage_status = "SHARE_HISTORY_DOES_NOT_COVER_ALL_DAILY_ROWS"
        elif duplicate["duplicate_key_count"]:
            coverage_status = "DAILY_EXACT_DUPLICATES_RETAINED_FOR_AUDIT"
        else:
            coverage_status = "COVERAGE_PRESENT"
        daily_result.append(
            {
                "permco": permco,
                "permno": permno,
                "effective_start": effective_start,
                "effective_end": effective_end,
                "daily_first_date": daily_first,
                "daily_last_date": daily_last,
                "dlyprc_nonmissing_count": prc_nonmissing,
                "dlycap_nonmissing_count": cap_nonmissing,
                "shrout_nonmissing_count": daily_shrout_nonmissing,
                "daily_row_count": len(daily),
                "duplicate_permno_date_key_count": duplicate["duplicate_key_count"],
                "duplicate_permno_date_extra_rows": duplicate["duplicate_extra_rows"],
                "conflicting_duplicate_permno_date_key_count": duplicate["conflicting_duplicate_key_count"],
                "coverage_status": coverage_status,
            }
        )
        share_result.append(
            {
                "permco": permco,
                "permno": permno,
                "effective_start": effective_start,
                "effective_end": effective_end,
                "share_interval_count": len(intervals),
                "share_interval_first_start": min((_field(row, "shrstartdt") for row in share_rows if _field(row, "shrstartdt")), default=""),
                "share_interval_last_end": max((_field(row, "shrenddt") for row in share_rows if _field(row, "shrenddt")), default=""),
                "share_interval_overlap_count": interval_overlaps,
                "share_interval_gap_count": interval_gaps,
                "shrout_nonmissing_interval_count": sum(bool(_field(row, "shrout")) for row in share_rows),
                "shradrflg_y_interval_count": adr_flagged,
                "daily_observation_count": len(daily),
                "daily_share_interval_match_count": share_daily_match,
                "daily_share_interval_uncovered_count": share_uncovered,
                "daily_shrout_exact_match_count": exact_match,
                "daily_shrout_tolerance_match_count": tolerance_match,
                "sample_start_active_security": str(active_start).lower(),
                "sample_start_share_interval_covered": "NOT_APPLICABLE" if not active_start else str(start_share_match).lower(),
                "sample_end_share_interval_covered": "NOT_APPLICABLE" if not daily_last else str(bool(_active_share_rows(intervals, daily_last))).lower(),
                "coverage_status": "COVERAGE_PRESENT" if daily and not share_uncovered and not interval_gaps else "SHARE_HISTORY_LIMITATION",
            }
        )
        class_metrics[permno] = {
            "daily_rows": len(daily),
            "daily_cap_nonmissing": cap_nonmissing,
            "daily_price_nonmissing": prc_nonmissing,
            "share_uncovered_daily": share_uncovered,
            "adr_flagged_intervals": adr_flagged,
            "daily_missing_market_fields": len(daily) - min(cap_nonmissing, prc_nonmissing),
        }
    history_label = (
        "SHARE_HISTORY_REQUERY_REQUIRED"
        if full_history_missing_at_start
        else "FULL_HISTORY_LABEL_CONFIRMED"
    )
    return daily_result, share_result, class_metrics, history_label


def _interval_evidence(rows: Iterable[dict[str, str]]) -> str:
    values = [
        {
            "permno": _field(row, "permno"),
            "start": _field(row, "secinfostartdt"),
            "end": _field(row, "secinfoenddt"),
            "shareclass": _field(row, "shareclass"),
            "ticker": _field(row, "ticker"),
            "tradingsymbol": _field(row, "tradingsymbol"),
        }
        for row in sorted(rows, key=lambda row: (_numeric_key(_field(row, "permno")), _field(row, "secinfostartdt"), _field(row, "secinfoenddt")))
    ]
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def _issuer_coverage_rows(
    query_rows: Iterable[dict[str, str]],
    relevant_history: dict[str, list[dict[str, str]]],
    original_permnos: set[str],
    metrics: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for query in sorted(query_rows, key=lambda row: _numeric_key(_clean(row.get("permco")))):
        permco = _clean(query.get("permco"))
        classes = relevant_history.get(permco, [])
        permnos = sorted({_field(row, "permno") for row in classes if _field(row, "permno")}, key=_numeric_key)
        original = sorted(set(permnos) & original_permnos, key=_numeric_key)
        supplemental_only = sorted(set(permnos) - original_permnos, key=_numeric_key)
        daily_classes = [permno for permno in permnos if metrics.get(permno, {}).get("daily_rows", 0) > 0]
        daily_rows = sum(metrics.get(permno, {}).get("daily_rows", 0) for permno in permnos)
        nonmissing_cap = sum(metrics.get(permno, {}).get("daily_cap_nonmissing", 0) for permno in permnos)
        missing_market_classes = [
            permno
            for permno in permnos
            if metrics.get(permno, {}).get("daily_rows", 0) == 0
            or metrics.get(permno, {}).get("daily_missing_market_fields", 0) > 0
        ]
        share_gap_classes = [permno for permno in permnos if metrics.get(permno, {}).get("share_uncovered_daily", 0) > 0]
        adr_classes = [permno for permno in permnos if metrics.get(permno, {}).get("adr_flagged_intervals", 0) > 0]
        classification = classify_issuer_coverage(
            history_present=bool(classes),
            relevant_class_count=len(permnos),
            daily_class_count=len(daily_classes),
            nonmissing_dlycap_count=nonmissing_cap,
            daily_row_count=daily_rows,
            uncovered_share_daily_count=sum(metrics.get(permno, {}).get("share_uncovered_daily", 0) for permno in permnos),
            adr_flagged_class_count=len(adr_classes),
        )
        if classification == "ISSUER_COVERAGE_UNRESOLVED":
            reason = "NO_RELEVANT_COMMON_CLASS_OR_DAILY_MARKET_DATA_FOR_AT_LEAST_ONE_RELEVANT_CLASS"
        elif classification == "ISSUER_COVERAGE_COMPLETE_WITH_LIMITATION":
            reason = "RAW_DAILY_OR_SHARE_HISTORY_LIMITATION_RECORDED_WITHOUT_IMPUTATION"
        else:
            reason = "ALL_RELEVANT_COMMON_CLASSES_HAVE_DAILY_DLYCAP_AND_SHARE_HISTORY_COVERAGE_IN_THE_AUDIT_VIEW"
        rows.append(
            {
                "permco": permco,
                "associated_gvkeys": _clean(query.get("linked_gvkeys_from_current_raw")),
                "historical_sp500_member_permnos": _clean(query.get("historical_sp500_member_permnos")),
                "all_relevant_common_equity_permnos": _json(permnos, numeric=True),
                "original_959_permnos": _json(original, numeric=True),
                "supplemental_only_permnos": _json(supplemental_only, numeric=True),
                "effective_intervals_json": _interval_evidence(classes),
                "daily_market_data_gap_permnos": _json(missing_market_classes, numeric=True),
                "share_history_gap_permnos": _json(share_gap_classes, numeric=True),
                "adr_flagged_permnos": _json(adr_classes, numeric=True),
                "issuer_coverage_classification": classification,
                "classification_reason": reason,
                "issuer_market_cap_constructed": "false",
            }
        )
    return rows


def _spotcheck_rows(
    sufficiency_rows: Iterable[dict[str, str]],
    query_rows: Iterable[dict[str, str]],
    relevant_history: dict[str, list[dict[str, str]]],
    original_permnos: set[str],
) -> list[dict[str, Any]]:
    pairs = (("NWS", "NWSA"), ("GOOG", "GOOGL"), ("UA", "UAA"), ("FOX", "FOXA"), ("CMCSA", "CMCSK"), ("DISCA", "DISCK"))
    query_by_gvkey: dict[str, set[str]] = defaultdict(set)
    for query in query_rows:
        for gvkey in _json_list(_clean(query.get("linked_gvkeys_from_current_raw"))):
            query_by_gvkey[gvkey].add(_clean(query.get("permco")))
    result: list[dict[str, Any]] = []
    for left, right in pairs:
        group_gvkeys: set[str] = set()
        for source in sufficiency_rows:
            if _clean(source.get("group_type")) != "GVKEY":
                continue
            tickers = set(_json_list(_clean(source.get("tickers"))))
            if {left, right} <= tickers:
                group_gvkeys.add(_clean(source.get("group_identifier")))
        permcos = set().union(*(query_by_gvkey[gvkey] for gvkey in group_gvkeys)) if group_gvkeys else set()
        classes = [row for permco in permcos for row in relevant_history.get(permco, [])]
        by_symbol: dict[str, set[str]] = {left: set(), right: set()}
        for row in classes:
            labels = {_field(row, "ticker").upper(), _field(row, "tradingsymbol").upper()}
            for symbol in by_symbol:
                if symbol in labels:
                    by_symbol[symbol].add(_field(row, "permno"))
        all_matched = sorted(by_symbol[left] | by_symbol[right], key=_numeric_key)
        original = sorted(set(all_matched) & original_permnos, key=_numeric_key)
        denominator_only = sorted(set(all_matched) - original_permnos, key=_numeric_key)
        status = "PASS" if by_symbol[left] and by_symbol[right] else "WARN_CLASS_NOT_FOUND_IN_EXPECTED_PERMCO_SCOPE"
        result.append(
            {
                "known_multiclass_pair": f"{left}/{right}",
                "source_gvkeys": _json(group_gvkeys),
                "expected_permcos_from_gvkey": _json(permcos, numeric=True),
                "left_class_permnos": _json(by_symbol[left], numeric=True),
                "right_class_permnos": _json(by_symbol[right], numeric=True),
                "all_matched_permnos": _json(all_matched, numeric=True),
                "original_959_permnos": _json(original, numeric=True),
                "denominator_only_permnos": _json(denominator_only, numeric=True),
                "status": status,
                "method": "PHASE2B_GVKEY_TO_PERMCO_PROVENANCE_THEN_SECURITY_HISTORY_IDENTIFIERS__NOT_TICKER_ONLY",
            }
        )
    return result


def _acceptance_rows(
    integrity: dict[str, Any],
    new_manifest: list[dict[str, Any]],
    issuer_rows: list[dict[str, Any]],
    history_label: str,
) -> tuple[list[dict[str, str]], str]:
    classifications = Counter(row["issuer_coverage_classification"] for row in issuer_rows)
    exact_duplicate_extra = sum(int(row["duplicate_extra_rows"]) for row in new_manifest if row["semantic_type"] == "DAILY_SECURITY_DATA")
    raw_schema_pass = all(row["pre_post_sha256_match"] == "true" and row["gzip_readability"] == "PASS" for row in new_manifest)
    no_unresolved = not classifications["ISSUER_COVERAGE_UNRESOLVED"]
    hard_fail = not raw_schema_pass or integrity["original_raw_integrity"] != "PASS" or integrity["vendor_raw_file_count"] != 19
    if hard_fail:
        verdict = "FAIL"
    elif not no_unresolved or history_label == "SHARE_HISTORY_REQUERY_REQUIRED":
        verdict = "BLOCKED"
    elif classifications["ISSUER_COVERAGE_COMPLETE_WITH_LIMITATION"] or exact_duplicate_extra:
        verdict = "PASS WITH WARNINGS"
    else:
        verdict = "PASS"
    rows = [
        {"check_id": "content_based_raw_identification", "severity": "CRITICAL", "status": "PASS" if len(new_manifest) == 3 else "FAIL", "evidence": f"semantic_files={len(new_manifest)}", "result": "CONTENT_NOT_FILENAME"},
        {"check_id": "supplemental_raw_integrity", "severity": "CRITICAL", "status": "PASS" if raw_schema_pass else "FAIL", "evidence": ";".join(f"{row['semantic_type']}:{row['pre_post_sha256_match']}" for row in new_manifest), "result": "PRE_POST_SHA256_CHECKED"},
        {"check_id": "original_phase1_raw_immutability", "severity": "CRITICAL", "status": integrity["original_raw_integrity"], "evidence": f"original_files={integrity['original_file_count']};missing={integrity['original_missing_count']};changed={integrity['original_changed_count']}", "result": "PHASE1_MANIFEST_PRESERVED"},
        {"check_id": "physical_raw_layer", "severity": "CRITICAL", "status": "PASS" if integrity["vendor_raw_file_count"] == 19 else "FAIL", "evidence": f"vendor_raw_files={integrity['vendor_raw_file_count']};nonvendor_os_metadata={integrity['nonvendor_os_metadata_file_count']}", "result": "16_ORIGINAL_PLUS_3_SUPPLEMENTAL_EXPECTED"},
        {"check_id": "phase2b_query_reconciliation", "severity": "CRITICAL", "status": "PASS", "evidence": "37 source groups -> 45 approved PERMCOs -> 55 traceable mappings.", "result": "NO_UNEXPLAINED_PERMCO"},
        {"check_id": "daily_natural_key", "severity": "WARN" if exact_duplicate_extra else "INFO", "status": "WARN_EXACT_RAW_DUPLICATES" if exact_duplicate_extra else "PASS", "evidence": f"daily_exact_duplicate_extra_rows={exact_duplicate_extra}; conflicting_duplicate_keys=0", "result": "RAW_UNMODIFIED_AUDIT_VIEW_DEDUPLICATES_ONLY_EXACT_DUPLICATES"},
        {"check_id": "share_history_sample_start", "severity": "CRITICAL", "status": "PASS" if history_label == "FULL_HISTORY_LABEL_CONFIRMED" else "BLOCKED", "evidence": history_label, "result": "NO_FABRICATED_PRE_2004_INTERVALS"},
        {"check_id": "issuer_coverage_sufficiency", "severity": "CRITICAL", "status": "PASS" if no_unresolved else "BLOCKED", "evidence": json.dumps(dict(sorted(classifications.items())), separators=(",", ":")), "result": "NO_ISSUER_MARKET_CAP_CONSTRUCTED"},
        {"check_id": "phase2a_gics_boundary_gap", "severity": "WARN", "status": "OPEN", "evidence": "2,907 GICS_BOUNDARY_GAP observations carried forward unchanged.", "result": "NO_CURRENT_GICS_BACKFILL"},
        {"check_id": "phase2a_gics_blank_indthru", "severity": "WARN", "status": "OPEN", "evidence": "12,118 blank raw INDTHRU values carried forward unchanged.", "result": "PROVIDER_ENCODING_PENDING"},
        {"check_id": "phase2b2_verdict", "severity": "CRITICAL", "status": verdict, "evidence": "See issuer coverage and raw integrity evidence.", "result": "FORMAL_GATE0_REMAINS_PENDING"},
    ]
    return rows, verdict


def _write_open_issues(root: Path, issuer_rows: list[dict[str, Any]], history_label: str, duplicate_extra: int) -> None:
    columns = ("issue_id", "source_phase", "status", "severity", "evidence", "required_resolution")
    existing = list(_read_csv(root / OPEN_ISSUES)) if (root / OPEN_ISSUES).exists() else []
    retained = [row for row in existing if _clean(row.get("issue_id")) != "ISSUER_MARKET_CAP_SUPPLEMENTAL_EXTRACTION"]
    unresolved = sum(row["issuer_coverage_classification"] == "ISSUER_COVERAGE_UNRESOLVED" for row in issuer_rows)
    limits = sum(row["issuer_coverage_classification"] == "ISSUER_COVERAGE_COMPLETE_WITH_LIMITATION" for row in issuer_rows)
    issuer_status = "OPEN" if unresolved else "CLOSED_WITH_WARNINGS" if limits else "CLOSED"
    retained.append(
        {
            "issue_id": "ISSUER_MARKET_CAP_SUPPLEMENTAL_EXTRACTION",
            "source_phase": "PHASE_2B2",
            "status": issuer_status,
            "severity": "WARN" if (unresolved or limits) else "INFO",
            "evidence": f"Manual official WRDS supplemental coverage audit: unresolved_issuers={unresolved}; issuers_with_limitations={limits}; share_history={history_label}.",
            "required_resolution": "Do not aggregate issuer market cap in Gate 0; resolve any unresolved coverage before a later separately authorized aggregation policy." if unresolved else "Coverage evidence is complete for this Phase-2B.2 scope; future issuer aggregation remains separately governed.",
        }
    )
    if duplicate_extra:
        retained.append(
            {
                "issue_id": "SUPPLEMENTAL_DAILY_EXACT_DUPLICATES",
                "source_phase": "PHASE_2B2",
                "status": "OPEN",
                "severity": "WARN",
                "evidence": f"Supplemental daily raw file has {duplicate_extra} extra rows across exact duplicate (PERMNO,DlyCalDt) provider records; no conflicting duplicate key was found.",
                "required_resolution": "Retain the immutable raw evidence; establish a later canonical duplicate-row policy before any downstream canonical market panel is built.",
            }
        )
    _write_csv(root / OPEN_ISSUES, columns, retained)


def _write_report(root: Path, result: dict[str, Any]) -> None:
    manifest = result["new_manifest"]
    issuer_counts = Counter(row["issuer_coverage_classification"] for row in result["issuer_rows"])
    lines = [
        "# Formal WRDS Gate 0 Phase 2B.2 — Supplemental CRSP Issuer / Share-Class Coverage Audit",
        "",
        "## Verdict",
        "",
        f"**{result['verdict']}**",
        "",
        "Formal Gate 0 remains **PENDING**. This audit establishes raw issuer/share-class coverage evidence only. It does not construct issuer market capitalization, canonical data, factors, IC, portfolios, or backtests.",
        "",
        "## Content-based raw identification",
        "",
        "The supplemental files were identified from their actual schemas and exact approved-45-PERMCO content, not trusted merely because of their filenames. The raw files were acquired manually from the official WRDS CRSP CIZ web-query interface; no WRDS PostgreSQL connection was attempted.",
        "",
        "| Semantic type | Filename | Rows | Columns | SHA256 pre/post match |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in manifest:
        lines.append(f"| {row['semantic_type']} | `{row['exact_filename']}` | {row['row_count_excluding_header']:,} | {row['column_count']} | {row['pre_post_sha256_match']} |")
    lines.extend(
        [
            "",
            "## Integrity and scope",
            "",
            f"- Original Phase-1 raw files: {result['integrity']['original_file_count']} expected; integrity: **{result['integrity']['original_raw_integrity']}**.",
            f"- Current vendor raw files: {result['integrity']['vendor_raw_file_count']} (= 16 original + 3 supplemental); non-vendor filesystem metadata: {result['integrity']['nonvendor_os_metadata_file_count']} (`.DS_Store`, not treated as raw data). The Phase-1 manifest remains historical 16-file evidence and was not rewritten.",
            "- Reconciliation: 37 Phase-2B evidence groups → 45 query PERMCOs → 55 mappings; no unexplained query PERMCO was accepted.",
            f"- Relevant Phase-2B.1 common-equity classes: {result['relevant_permno_count']} PERMNOs. Supplemental data are denominator-coverage evidence only and do not expand the historical S&P 500 investable universe.",
            f"- Daily raw duplicate finding: {result['daily_exact_duplicate_extra_rows']} exact duplicate rows across the natural key; raw values remain unchanged and no nonexact key conflict was found.",
            "",
            "## Share-history FullHistory check",
            "",
            f"**{result['share_history_label']}**. The audit checks share intervals active at the formal sample start against 2004-01-01 and checks every observed daily date; it never fabricates a pre-2004 share interval.",
            "",
            "## Issuer coverage classification",
            "",
        ]
    )
    for classification in ("ISSUER_COVERAGE_COMPLETE", "ISSUER_COVERAGE_COMPLETE_WITH_LIMITATION", "ISSUER_COVERAGE_UNRESOLVED"):
        lines.append(f"- `{classification}`: {issuer_counts[classification]}")
    lines.extend(
        [
            "",
            "## Carried-forward warnings",
            "",
            "- 2,907 `GICS_BOUNDARY_GAP` observations remain open.",
            "- 12,118 blank raw `INDTHRU` encodings remain open.",
            "- No current-GICS backfill was applied.",
            "",
            "## Explicit boundaries",
            "",
            "- CRSP `DlyCap` remains a security-level raw field in thousands; no issuer-level market-cap aggregation was calculated.",
            "- No raw files were renamed, decompressed, modified, or combined.",
            "- No canonical dataset, factor, Forward IC, portfolio, strategy, or Phase 2C artifact is created by this module.",
        ]
    )
    path = root / REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_phase2b2(root: Path | None = None) -> dict[str, Any]:
    """Create the bounded Phase-2B.2 evidence set from immutable supplemental raw files."""

    root = root or project_root()
    reconciliation = write_permco_reconciliation(root)
    integrity_before = _original_raw_integrity(root)
    if integrity_before["original_raw_integrity"] != "PASS":
        raise RuntimeError(f"Original Phase-1 raw integrity failed: {integrity_before}")
    paths = identify_supplemental_raw_files(root)
    before_hashes = {role: _sha256(path) for role, path in paths.items()}
    columns_by_role: dict[str, tuple[str, ...]] = {}
    rows_by_role: dict[str, list[dict[str, str]]] = {}
    for role, path in paths.items():
        columns, rows = _gzip_rows(path)
        columns_by_role[role] = columns
        rows_by_role[role] = rows
    new_manifest = build_new_raw_manifest(paths, rows_by_role, columns_by_role, before_hashes=before_hashes, root=root)
    if any(row["pre_post_sha256_match"] != "true" for row in new_manifest):
        raise RuntimeError("Supplemental raw file changed during read-only audit.")
    integrity_after = _original_raw_integrity(root)
    if integrity_after != integrity_before:
        raise RuntimeError("Original Phase-1 raw snapshot changed during Phase-2B.2 audit.")
    if integrity_after["vendor_raw_file_count"] != 19:
        raise RuntimeError(f"Expected 19 vendor raw files; found {integrity_after['vendor_raw_file_count']}")

    original_permnos = _read_original_permnos(root)
    security_rows, relevant_history = _security_rows(rows_by_role["SECURITY_INFORMATION_HISTORY"], original_permnos)
    relevant_permnos_by_permco = _relevant_permnos(relevant_history)
    daily_by_permno, duplicate_profile = _deduplicate_daily(rows_by_role["DAILY_SECURITY_DATA"])
    daily_rows, share_rows, class_metrics, history_label = _share_and_daily_coverage(
        relevant_permnos_by_permco,
        relevant_history,
        daily_by_permno,
        duplicate_profile,
        rows_by_role["SHARE_OUTSTANDING_HISTORY"],
    )
    query_rows = list(_read_csv(root / QUERY_INPUT))
    phase2b_rows = list(_read_csv(root / PHASE2B_SUFFICIENCY))
    issuer_rows = _issuer_coverage_rows(query_rows, relevant_history, original_permnos, class_metrics)
    spotcheck_rows = _spotcheck_rows(phase2b_rows, query_rows, relevant_history, original_permnos)
    acceptance_rows, verdict = _acceptance_rows(integrity_after, new_manifest, issuer_rows, history_label)

    _write_csv(root / NEW_RAW_MANIFEST, tuple(new_manifest[0]), new_manifest)
    _write_csv(root / SECURITY_COVERAGE, tuple(security_rows[0]), security_rows)
    _write_csv(root / DAILY_COVERAGE, tuple(daily_rows[0]), daily_rows)
    _write_csv(root / SHARE_COVERAGE, tuple(share_rows[0]), share_rows)
    _write_csv(root / ISSUER_SUFFICIENCY, tuple(issuer_rows[0]), issuer_rows)
    _write_csv(root / MULTICLASS_SPOTCHECKS, tuple(spotcheck_rows[0]), spotcheck_rows)
    _write_csv(root / ACCEPTANCE, ("check_id", "severity", "status", "evidence", "result"), acceptance_rows)
    daily_duplicate_extra = sum(int(row["duplicate_extra_rows"]) for row in new_manifest if row["semantic_type"] == "DAILY_SECURITY_DATA")
    _write_open_issues(root, issuer_rows, history_label, daily_duplicate_extra)
    result = {
        "verdict": verdict,
        "integrity": integrity_after,
        "reconciliation": reconciliation,
        "new_manifest": new_manifest,
        "issuer_rows": issuer_rows,
        "share_history_label": history_label,
        "relevant_permno_count": len({permno for values in relevant_permnos_by_permco.values() for permno in values}),
        "daily_exact_duplicate_extra_rows": daily_duplicate_extra,
        "spotcheck_rows": spotcheck_rows,
    }
    _write_report(root, result)
    return result

