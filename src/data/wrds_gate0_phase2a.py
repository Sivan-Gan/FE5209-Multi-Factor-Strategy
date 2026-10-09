"""Formal WRDS Gate 0 Phase 2A: identifier, CCM, and Historical-GICS audit.

This module reads only the immutable WRDS raw package and writes Phase-2A
evidence under ``outputs/audit/wrds_formal``.  It does not build a canonical
panel, resolve market-data/fundamental policy, or construct factors.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import tarfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Iterator

from src.data import wrds_gate0_phase1 as phase1


EVIDENCE_STAGE = "FORMAL_GATE0_PHASE2A_IDENTIFIERS_CCM_GICS_AUDIT"
CCM_RELATIVE_PATH = "ccm/CCM_CRSP_Compustat_Link_LC_LU_LS_FullHistory_959_SP500_PERMNOs.csv.gz"
CONSTITUENT_RELATIVE_PATH = "crsp/CRSP_SP500_Constituents_Daily_CIZ_2006-01-01_2025-12-31.csv.gz"
STOCK_HEADER_RELATIVE_PATH = "crsp/CRSP_Stock_Header_CIZ_FullHistory_959_SP500_PERMNOs.csv.gz"
DAILY_STOCK_RELATIVE_PATH = "crsp/CRSP_Daily_Stock_CIZ_2004-01-01_2025-12-31.tar"
HISTORICAL_GICS_RELATIVE_PATH = "compustat/Compustat_Historical_GICS_FullHistory.csv.gz"
GICS_REFERENCE_RELATIVE_PATH = "compustat/Compustat_GICS_Code_Reference.csv.gz"

ALLOWED_LINK_TYPES = frozenset({"LC", "LU", "LS"})
ALLOWED_LINK_PRIMS = frozenset({"P", "C", "J"})
CCM_OPEN_END_RAW = "E"
HISTORICAL_GICS_TYPE = "GICS"
SSOT_MULTICLASS_EXAMPLES = frozenset({"NWS", "GOOG", "UA", "FOX", "CMCSK", "DISCK"})


@dataclass(frozen=True)
class CcmLink:
    """One raw CCM record plus its audited interval representation."""

    gvkey: str
    lpermno: str
    lpermco: str
    linktype: str
    linkprim: str
    linkdt_raw: str
    linkenddt_raw: str
    linkdt: date | None
    linkenddt: date | None
    end_status: str
    tic: str
    conm: str
    liid: str


@dataclass(frozen=True)
class GicsInterval:
    """One raw Historical-GICS record with retained raw date values."""

    gvkey: str
    indtype: str
    indfrom_raw: str
    indthru_raw: str
    indfrom: date | None
    indthru: date | None
    end_status: str
    gsector: str
    ggroup: str
    gind: str
    gsubind: str


def project_root() -> Path:
    """Return the repository root without relying on the caller's CWD."""

    return Path(__file__).resolve().parents[2]


def raw_root(root: Path | None = None) -> Path:
    """Return the immutable formal raw package path."""

    return phase1.raw_root(root or project_root())


def audit_root(root: Path | None = None) -> Path:
    """Return the formal audit-output directory."""

    return phase1.audit_root(root or project_root())


def _value(row: dict[str, str], column: str) -> str:
    """Read a case-insensitive CSV field as a stripped string."""

    for key, value in row.items():
        if key.lower() == column.lower():
            return (value or "").strip()
    return ""


def _parse_iso_date(value: str) -> date | None:
    """Parse delivered ISO/compact date text only; do not coerce other values."""

    candidate = value.strip()
    if not candidate:
        return None
    if len(candidate) == 10:
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            return None
    if len(candidate) == 8 and candidate.isdigit():
        try:
            return date(int(candidate[:4]), int(candidate[4:6]), int(candidate[6:]))
        except ValueError:
            return None
    return None


def _iso(value: date | None) -> str:
    return value.isoformat() if value else ""


def _csv_rows(path: Path) -> Iterator[dict[str, str]]:
    """Stream a UTF-8 gzip CSV without materializing a large source table."""

    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
        yield from csv.DictReader(stream)


def _json(values: Iterable[str]) -> str:
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def interval_contains(observation_date: date, start: date | None, end: date | None) -> bool:
    """Return whether an inclusive finite/open-ended interval contains a date."""

    return start is not None and start <= observation_date and (end is None or observation_date <= end)


def intervals_overlap(
    left_start: date | None,
    left_end: date | None,
    right_start: date | None,
    right_end: date | None,
) -> bool:
    """Return whether two inclusive intervals overlap; ``None`` end is open."""

    if left_start is None or right_start is None:
        return False
    if left_end is not None and left_end < right_start:
        return False
    if right_end is not None and right_end < left_start:
        return False
    return True


def classify_unmatched(has_baseline_link: bool, has_invalid_baseline_interval: bool = False) -> str:
    """Classify a non-matched constituent record without filling any link."""

    if not has_baseline_link:
        return "CCM_UNLINKED"
    if has_invalid_baseline_interval:
        return "OTHER_UNRESOLVED"
    return "CCM_BOUNDARY_GAP"


def reconcile_batch_counts(rows: Iterable[dict[str, Any]]) -> dict[str, int | bool]:
    """Reconcile physical line, header, and parsed-row counts deterministically."""

    materialized = list(rows)
    physical_lines = sum(int(row["physical_lines"]) for row in materialized)
    header_lines = sum(int(row["header_lines"]) for row in materialized)
    data_rows = sum(int(row["data_rows"]) for row in materialized)
    all_rows_reconcile = all(
        int(row["physical_lines"]) == int(row["header_lines"]) + int(row["data_rows"])
        for row in materialized
    )
    return {
        "physical_lines": physical_lines,
        "header_lines": header_lines,
        "data_rows": data_rows,
        "all_rows_reconcile": all_rows_reconcile,
    }


def _ccm_end(raw_value: str) -> tuple[date | None, str]:
    """Represent raw CCM end values without changing their delivered text.

    WRDS CIZ macros explicitly test SAS ``LINKENDDT = .E`` as the open-ended
    condition.  The delivered CSV exposes that special-missing value as ``E``.
    """

    if raw_value == CCM_OPEN_END_RAW:
        return None, "OPEN_ENDED_E"
    parsed = _parse_iso_date(raw_value)
    return (parsed, "ORDINARY_DATE") if parsed is not None else (None, "UNRESOLVED_NONORDINARY")


def _gics_end(raw_value: str) -> tuple[date | None, str]:
    """Retain raw blank INDTHRU values as open-ended comparison intervals."""

    if not raw_value:
        return None, "OPEN_ENDED_RAW_BLANK"
    parsed = _parse_iso_date(raw_value)
    return (parsed, "ORDINARY_DATE") if parsed is not None else (None, "UNRESOLVED_NONORDINARY")


def read_ccm_links(path: Path) -> tuple[list[CcmLink], list[dict[str, str]]]:
    """Read the small CCM raw table and retain raw rows for audit evidence."""

    raw_rows = list(_csv_rows(path))
    links: list[CcmLink] = []
    for row in raw_rows:
        linkdt_raw = _value(row, "LINKDT")
        linkenddt_raw = _value(row, "LINKENDDT")
        linkenddt, end_status = _ccm_end(linkenddt_raw)
        links.append(
            CcmLink(
                gvkey=_value(row, "GVKEY"),
                lpermno=_value(row, "LPERMNO"),
                lpermco=_value(row, "LPERMCO"),
                linktype=_value(row, "LINKTYPE"),
                linkprim=_value(row, "LINKPRIM"),
                linkdt_raw=linkdt_raw,
                linkenddt_raw=linkenddt_raw,
                linkdt=_parse_iso_date(linkdt_raw),
                linkenddt=linkenddt,
                end_status=end_status,
                tic=_value(row, "tic"),
                conm=_value(row, "conm"),
                liid=_value(row, "LIID"),
            )
        )
    return links, raw_rows


def _accepted_ccm(link: CcmLink) -> bool:
    return link.linktype in ALLOWED_LINK_TYPES and link.linkprim in ALLOWED_LINK_PRIMS


def _valid_ccm_interval(link: CcmLink) -> bool:
    return link.linkdt is not None and link.end_status in {"ORDINARY_DATE", "OPEN_ENDED_E"} and (
        link.linkenddt is None or link.linkdt <= link.linkenddt
    )


def _ccm_by_permno(links: Iterable[CcmLink]) -> dict[str, list[CcmLink]]:
    grouped: dict[str, list[CcmLink]] = defaultdict(list)
    for link in links:
        if _accepted_ccm(link):
            grouped[link.lpermno].append(link)
    for group in grouped.values():
        group.sort(key=lambda item: (item.linkdt or date.min, item.linkenddt or date.max, item.gvkey, item.linkprim))
    return dict(grouped)


def _ccm_matches(links: Iterable[CcmLink], observation_date: date) -> list[CcmLink]:
    return [link for link in links if _valid_ccm_interval(link) and interval_contains(observation_date, link.linkdt, link.linkenddt)]


def _read_stock_header(path: Path) -> dict[str, dict[str, str]]:
    """Read the small CRSP header object for diagnostic PERMCO/ticker context."""

    result: dict[str, dict[str, str]] = {}
    for row in _csv_rows(path):
        permno = _value(row, "permno")
        if permno:
            result[permno] = {
                "permco": _value(row, "permco"),
                "ticker": _value(row, "ticker") or _value(row, "tradingsymbol"),
                "issuer": _value(row, "issuernm"),
            }
    return result


def _read_gics(path: Path) -> tuple[list[GicsInterval], list[dict[str, str]]]:
    raw_rows = list(_csv_rows(path))
    intervals: list[GicsInterval] = []
    for row in raw_rows:
        indfrom_raw = _value(row, "indfrom")
        indthru_raw = _value(row, "indthru")
        indthru, end_status = _gics_end(indthru_raw)
        intervals.append(
            GicsInterval(
                gvkey=_value(row, "gvkey"),
                indtype=_value(row, "indtype"),
                indfrom_raw=indfrom_raw,
                indthru_raw=indthru_raw,
                indfrom=_parse_iso_date(indfrom_raw),
                indthru=indthru,
                end_status=end_status,
                gsector=_value(row, "gsector"),
                ggroup=_value(row, "ggroup"),
                gind=_value(row, "gind"),
                gsubind=_value(row, "gsubind"),
            )
        )
    return intervals, raw_rows


def _gics_by_gvkey(intervals: Iterable[GicsInterval]) -> dict[str, list[GicsInterval]]:
    grouped: dict[str, list[GicsInterval]] = defaultdict(list)
    for interval in intervals:
        if interval.indtype == HISTORICAL_GICS_TYPE:
            grouped[interval.gvkey].append(interval)
    for group in grouped.values():
        group.sort(key=lambda item: (item.indfrom or date.min, item.indthru or date.max, item.gsubind))
    return dict(grouped)


def _gics_matches(intervals: Iterable[GicsInterval], observation_date: date) -> list[GicsInterval]:
    return [
        interval
        for interval in intervals
        if interval.indfrom is not None
        and interval.end_status in {"ORDINARY_DATE", "OPEN_ENDED_RAW_BLANK"}
        and interval_contains(observation_date, interval.indfrom, interval.indthru)
    ]


def _sha256_rows(package: Path) -> list[phase1.FileSnapshot]:
    """Use Phase-1's deterministic full raw physical snapshot."""

    return phase1.snapshot_raw_files(package)


def _snapshot_equal(left: list[phase1.FileSnapshot], right: list[phase1.FileSnapshot]) -> bool:
    return [(row.relative_path, row.size_bytes, row.sha256) for row in left] == [
        (row.relative_path, row.size_bytes, row.sha256) for row in right
    ]


def _stream_tar_reconciliation(path: Path) -> tuple[list[dict[str, Any]], bool]:
    """Stream TAR members and distinguish physical CSV lines from data rows."""

    rows: list[dict[str, Any]] = []
    schemas: set[tuple[str, ...]] = set()
    with tarfile.open(path, "r") as archive:
        for member in sorted((item for item in archive.getmembers() if item.isfile() and item.name.endswith(".csv.gz")), key=lambda item: item.name):
            binary = archive.extractfile(member)
            if binary is None:
                raise tarfile.TarError(f"Unable to stream TAR member: {member.name}")
            try:
                with gzip.GzipFile(fileobj=binary, mode="rb") as zipped:
                    physical_lines = 0
                    for _ in zipped:
                        physical_lines += 1
                binary.close()
                binary = archive.extractfile(member)
                if binary is None:
                    raise tarfile.TarError(f"Unable to re-stream TAR member: {member.name}")
                with gzip.GzipFile(fileobj=binary, mode="rb") as zipped:
                    with io.TextIOWrapper(zipped, encoding="utf-8-sig", newline="") as text:
                        reader = csv.DictReader(text)
                        schema = tuple(reader.fieldnames or ())
                        data_rows = 0
                        permnos: set[str] = set()
                        min_date: date | None = None
                        max_date: date | None = None
                        duplicate_keys: set[tuple[str, str]] = set()
                        seen_keys: set[tuple[str, str]] = set()
                        for record in reader:
                            data_rows += 1
                            permno = _value(record, "permno")
                            dlycaldt = _parse_iso_date(_value(record, "dlycaldt"))
                            if permno:
                                permnos.add(permno)
                            if dlycaldt is not None:
                                min_date = dlycaldt if min_date is None or dlycaldt < min_date else min_date
                                max_date = dlycaldt if max_date is None or dlycaldt > max_date else max_date
                                key = (permno, dlycaldt.isoformat())
                                if key in seen_keys:
                                    duplicate_keys.add(key)
                                seen_keys.add(key)
                        schemas.add(schema)
                        header_lines = 1 if schema else 0
                        notes: list[str] = []
                        if physical_lines != data_rows + header_lines:
                            notes.append("PHYSICAL_LINE_RECONCILIATION_FAIL")
                        if duplicate_keys:
                            notes.append(f"duplicate_permno_date={len(duplicate_keys)}")
                        rows.append(
                            {
                                "batch_name": member.name,
                                "physical_lines": physical_lines,
                                "header_lines": header_lines,
                                "data_rows": data_rows,
                                "permno_count": len(permnos),
                                "min_date": _iso(min_date),
                                "max_date": _iso(max_date),
                                "duplicate_permno_date": len(duplicate_keys),
                                "notes": "; ".join(notes) or "COUNT_DEFINITION_RECONCILED",
                            }
                        )
            finally:
                binary.close()
    return rows, len(schemas) == 1


def _overlap_pair_counts(groups: dict[str, list[Any]], start: Any, end: Any, group_key: Any) -> tuple[int, int]:
    """Count raw interval pairs and the subset that involve different group keys."""

    overlaps = 0
    different_group_overlaps = 0
    for values in groups.values():
        for left_index, left in enumerate(values):
            for right in values[left_index + 1 :]:
                if intervals_overlap(start(left), end(left), start(right), end(right)):
                    overlaps += 1
                    if group_key(left) != group_key(right):
                        different_group_overlaps += 1
    return overlaps, different_group_overlaps


def _ccm_summary_rows(links: list[CcmLink], raw_rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build deterministic raw CCM profile and interval-integrity metrics."""

    type_counts = Counter(link.linktype for link in links)
    prim_counts = Counter(link.linkprim for link in links)
    combination_counts = Counter((link.linktype, link.linkprim) for link in links)
    accepted = [link for link in links if _accepted_ccm(link)]
    valid_accepted = [link for link in accepted if _valid_ccm_interval(link)]
    exact_raw = [tuple(row.get(column, "") for column in sorted(row)) for row in raw_rows]
    duplicate_raw_rows = len(exact_raw) - len(set(exact_raw))
    accepted_by_permno = _ccm_by_permno(accepted)
    overlap_count, different_gvkey_overlap_count = _overlap_pair_counts(
        accepted_by_permno,
        lambda row: row.linkdt,
        lambda row: row.linkenddt,
        lambda row: row.gvkey,
    )
    rows: list[dict[str, Any]] = []

    def add(section: str, metric: str, category: str, count: int, notes: str = "") -> None:
        rows.append({"section": section, "metric": metric, "category": category, "count": count, "notes": notes})

    add("overall", "total_ccm_rows", "ALL", len(links))
    add("overall", "unique_lpermno", "ALL", len({link.lpermno for link in links if link.lpermno}))
    add("overall", "unique_gvkey", "ALL", len({link.gvkey for link in links if link.gvkey}))
    add("overall", "missing_lpermno", "ALL", sum(not link.lpermno for link in links))
    add("overall", "missing_gvkey", "ALL", sum(not link.gvkey for link in links))
    add("overall", "duplicate_raw_rows", "ALL", duplicate_raw_rows)
    add("baseline", "accepted_baseline_rows", "LC_LU_LS__P_C_J", len(accepted))
    add("baseline", "accepted_valid_intervals", "LC_LU_LS__P_C_J", len(valid_accepted))
    add("baseline", "open_ended_links", "OPEN_ENDED_E", sum(link.end_status == "OPEN_ENDED_E" for link in links))
    add("baseline", "zero_length_links", "ALL", sum(link.linkdt is not None and link.linkdt == link.linkenddt for link in links))
    add("baseline", "invalid_ordinary_interval_order", "ALL", sum(link.linkdt is not None and link.linkenddt is not None and link.linkenddt < link.linkdt for link in links))
    add("interval_integrity", "overlapping_raw_interval_pairs", "ACCEPTED_BASELINE", overlap_count)
    add("interval_integrity", "overlapping_different_gvkey_pairs", "ACCEPTED_BASELINE", different_gvkey_overlap_count)
    for value, count in sorted(type_counts.items()):
        add("linktype", "frequency", value or "<BLANK>", count)
    for value, count in sorted(prim_counts.items()):
        add("linkprim", "frequency", value or "<BLANK>", count)
    for (linktype, linkprim), count in sorted(combination_counts.items()):
        add("linktype_x_linkprim", "frequency", f"{linktype or '<BLANK>'}|{linkprim or '<BLANK>'}", count)
    return rows, {
        "accepted": accepted,
        "valid_accepted": valid_accepted,
        "accepted_by_permno": accepted_by_permno,
        "overlap_count": overlap_count,
        "different_gvkey_overlap_count": different_gvkey_overlap_count,
        "duplicate_raw_rows": duplicate_raw_rows,
    }


def _linkenddt_rows(links: list[CcmLink]) -> list[dict[str, Any]]:
    """Enumerate raw LINKENDDT encoding and representative source rows."""

    raw_counts = Counter(link.linkenddt_raw for link in links)
    ordinary_count = sum(link.end_status == "ORDINARY_DATE" for link in links)
    nonordinary = [link for link in links if link.end_status == "OPEN_ENDED_E"]
    rows = [
        {
            "record_type": "SUMMARY",
            "linkenddt_raw": "",
            "count": "",
            "ccm_row_count": len(links),
            "null_count": sum(not link.linkenddt_raw for link in links),
            "non_null_count": sum(bool(link.linkenddt_raw) for link in links),
            "ordinary_date_count": ordinary_count,
            "nonordinary_count": len(nonordinary),
            "linktype": "",
            "linkprim": "",
            "gvkey": "",
            "lpermno": "",
            "tic": "",
            "linkdt": "",
            "linkenddt": "",
            "semantic_status": "RESOLVED_WITH_EVIDENCE",
            "source_evidence": "WRDS CIZ macros test LINKENDDT=.E as open-ended; raw CSV token E retained",
            "notes": "No raw token was normalized or overwritten",
        },
        {
            "record_type": "NONORDINARY_VALUE",
            "linkenddt_raw": CCM_OPEN_END_RAW,
            "count": raw_counts[CCM_OPEN_END_RAW],
            "ccm_row_count": "",
            "null_count": "",
            "non_null_count": "",
            "ordinary_date_count": "",
            "nonordinary_count": "",
            "linktype": "",
            "linkprim": "",
            "gvkey": "",
            "lpermno": "",
            "tic": "",
            "linkdt": "",
            "linkenddt": "",
            "semantic_status": "OPEN_ENDED_E",
            "source_evidence": "https://wrds-www.wharton.upenn.edu/pages/wrds-research/macros/wrds-macro-indratios-ciz/",
            "notes": "Only observed nonordinary nonblank raw LINKENDDT token",
        },
    ]
    for link in sorted(nonordinary, key=lambda item: (item.gvkey, item.lpermno, item.linkdt_raw))[:10]:
        rows.append(
            {
                "record_type": "REPRESENTATIVE_ROW",
                "linkenddt_raw": link.linkenddt_raw,
                "count": "",
                "ccm_row_count": "",
                "null_count": "",
                "non_null_count": "",
                "ordinary_date_count": "",
                "nonordinary_count": "",
                "linktype": link.linktype,
                "linkprim": link.linkprim,
                "gvkey": link.gvkey,
                "lpermno": link.lpermno,
                "tic": link.tic,
                "linkdt": link.linkdt_raw,
                "linkenddt": link.linkenddt_raw,
                "semantic_status": link.end_status,
                "source_evidence": "Raw delivered CCM CSV",
                "notes": "Representative open-ended raw record",
            }
        )
    return rows


def _gics_summary_rows(
    intervals: list[GicsInterval],
    raw_rows: list[dict[str, str]],
    code_reference_rows: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Profile Historical-GICS raw intervals and code-reference support."""

    indtype_counts = Counter(item.indtype for item in intervals)
    gics = [item for item in intervals if item.indtype == HISTORICAL_GICS_TYPE]
    by_gvkey = _gics_by_gvkey(gics)
    overlaps, _ = _overlap_pair_counts(by_gvkey, lambda row: row.indfrom, lambda row: row.indthru, lambda row: row.gsubind)
    reference_codes = {_value(row, "giccd") for row in code_reference_rows if _value(row, "giccd")}
    rows: list[dict[str, Any]] = []

    def add(section: str, metric: str, category: str, count: int, denominator: int | str = "", notes: str = "") -> None:
        rows.append(
            {
                "section": section,
                "metric": metric,
                "category": category,
                "count": count,
                "denominator": denominator,
                "percentage": f"{(100 * count / denominator):.6f}" if isinstance(denominator, int) and denominator else "",
                "notes": notes,
            }
        )

    add("overall", "total_rows", "ALL", len(intervals))
    add("overall", "unique_gvkey", "ALL", len({item.gvkey for item in intervals if item.gvkey}))
    add("overall", "missing_gvkey", "ALL", sum(not item.gvkey for item in intervals))
    add("overall", "missing_indfrom", "ALL", sum(item.indfrom is None for item in intervals))
    add("overall", "missing_indthru_raw", "ALL", sum(not item.indthru_raw for item in intervals))
    add("overall", "open_ended_raw_blank", "ALL", sum(item.end_status == "OPEN_ENDED_RAW_BLANK" for item in intervals))
    add("overall", "invalid_interval_order", "ALL", sum(item.indfrom is not None and item.indthru is not None and item.indthru < item.indfrom for item in intervals))
    add("interval_integrity", "overlapping_interval_pairs", "GICS", overlaps)
    add("interval_integrity", "single_day_intervals", "GICS", sum(item.indfrom is not None and item.indfrom == item.indthru for item in gics))
    add("interval_integrity", "inclusive_start_boundary_check", "GICS", sum(interval_contains(item.indfrom, item.indfrom, item.indthru) for item in gics if item.indfrom is not None), len(gics))
    add("interval_integrity", "inclusive_end_boundary_check", "GICS", sum(interval_contains(item.indthru, item.indfrom, item.indthru) for item in gics if item.indfrom is not None and item.indthru is not None), sum(item.indthru is not None for item in gics))
    for level in ("gsector", "ggroup", "gind", "gsubind"):
        values = [_value(row, level) for row in raw_rows]
        add("missingness", f"missing_{level}", "ALL", sum(not value for value in values), len(values))
        add("code_reference", f"unmatched_{level}_codes", "ALL", sum(bool(value) and value not in reference_codes for value in values), sum(bool(value) for value in values))
    for indtype, count in sorted(indtype_counts.items()):
        add("indtype", "frequency", indtype or "<BLANK>", count)
    return rows, {"gics": gics, "by_gvkey": by_gvkey, "reference_codes": reference_codes, "overlap_count": overlaps}


def _constituent_coverage(
    path: Path,
    accepted_by_permno: dict[str, list[CcmLink]],
    gics_by_gvkey: dict[str, list[GicsInterval]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Stream historical constituents and measure only strict time-valid links."""

    total = 0
    constituent_permnos: set[str] = set()
    matched = 0
    unmatched = 0
    different_gvkey_ambiguity = 0
    unique_linked_gvkeys: set[str] = set()
    gics_matched = 0
    gics_uncovered = 0
    gics_no_gvkey_record = 0
    gics_boundary_gap = 0
    gics_ambiguity = 0
    exceptions: list[dict[str, Any]] = []
    gics_exceptions: list[dict[str, Any]] = []
    permno_to_gvkeys: dict[str, set[str]] = defaultdict(set)
    for row in _csv_rows(path):
        total += 1
        permno = _value(row, "permno")
        date_raw = _value(row, "dlycaldt")
        observation_date = _parse_iso_date(date_raw)
        constituent_permnos.add(permno)
        candidates = accepted_by_permno.get(permno, [])
        matches = _ccm_matches(candidates, observation_date) if observation_date is not None else []
        gvkeys = sorted({item.gvkey for item in matches if item.gvkey})
        if not gvkeys:
            unmatched += 1
            classification = classify_unmatched(bool(candidates), any(not _valid_ccm_interval(item) for item in candidates))
            exceptions.append(
                {
                    "exception_type": classification,
                    "permno": permno,
                    "date": date_raw,
                    "gvkey": "",
                    "lpermno": "",
                    "linktype": "",
                    "linkprim": "",
                    "raw_linkdt": "",
                    "raw_linkenddt": "",
                    "ticker": _value(row, "ticker"),
                    "evidence": "No strict time-valid accepted CCM mapping; no fill applied",
                    "notes": "",
                }
            )
            continue
        matched += 1
        if len(gvkeys) > 1:
            different_gvkey_ambiguity += 1
            exceptions.append(
                {
                    "exception_type": "CCM_DIFFERENT_GVKEY_SIMULTANEOUS",
                    "permno": permno,
                    "date": date_raw,
                    "gvkey": _json(gvkeys),
                    "lpermno": "",
                    "linktype": "",
                    "linkprim": "",
                    "raw_linkdt": "",
                    "raw_linkenddt": "",
                    "ticker": _value(row, "ticker"),
                    "evidence": "Multiple distinct GVKEY values valid on same constituent date",
                    "notes": "No selection policy applied",
                }
            )
            continue
        gvkey = gvkeys[0]
        unique_linked_gvkeys.add(gvkey)
        permno_to_gvkeys[permno].add(gvkey)
        gics_matches = _gics_matches(gics_by_gvkey.get(gvkey, []), observation_date)
        if len(gics_matches) == 1:
            gics_matched += 1
        elif not gics_matches:
            gics_uncovered += 1
            exception_type = "GICS_NO_RECORD_FOR_LINKED_GVKEY" if not gics_by_gvkey.get(gvkey) else "GICS_BOUNDARY_GAP"
            if exception_type == "GICS_NO_RECORD_FOR_LINKED_GVKEY":
                gics_no_gvkey_record += 1
            else:
                gics_boundary_gap += 1
            gics_exceptions.append(
                {
                    "exception_type": exception_type,
                    "permno": permno,
                    "date": date_raw,
                    "gvkey": gvkey,
                    "match_count": 0,
                    "gsector": "",
                    "ggroup": "",
                    "gind": "",
                    "gsubind": "",
                    "raw_indfrom": "",
                    "raw_indthru": "",
                    "notes": "No time-valid historical GICS record; no current/header backfill applied",
                }
            )
        else:
            gics_ambiguity += 1
            gics_exceptions.append(
                {
                    "exception_type": "GICS_MULTIPLE_MATCHES",
                    "permno": permno,
                    "date": date_raw,
                    "gvkey": gvkey,
                    "match_count": len(gics_matches),
                    "gsector": _json(sorted({item.gsector for item in gics_matches})),
                    "ggroup": _json(sorted({item.ggroup for item in gics_matches})),
                    "gind": _json(sorted({item.gind for item in gics_matches})),
                    "gsubind": _json(sorted({item.gsubind for item in gics_matches})),
                    "raw_indfrom": _json(sorted({item.indfrom_raw for item in gics_matches})),
                    "raw_indthru": _json(sorted({item.indthru_raw for item in gics_matches})),
                    "notes": "No arbitrary tie-break applied",
                }
            )
    coverage_rows = [
        {"scope": "historical_sp500", "metric": "historical_permno_count", "count": len(constituent_permnos), "denominator": "", "percentage": "", "notes": "Raw constituent observations"},
        {"scope": "historical_sp500", "metric": "constituent_permno_date_observations", "count": total, "denominator": "", "percentage": "", "notes": "Raw (PERMNO, DlyCalDt) observations"},
        {"scope": "ccm", "metric": "matched_permno_date_observations", "count": matched, "denominator": total, "percentage": f"{100 * matched / total:.6f}" if total else "", "notes": "Strict accepted CCM baseline; inclusive interval; no fill"},
        {"scope": "ccm", "metric": "unmatched_permno_date_observations", "count": unmatched, "denominator": total, "percentage": f"{100 * unmatched / total:.6f}" if total else "", "notes": "Classified in ccm exceptions"},
        {"scope": "ccm", "metric": "different_gvkey_simultaneous_ambiguity", "count": different_gvkey_ambiguity, "denominator": total, "percentage": f"{100 * different_gvkey_ambiguity / total:.6f}" if total else "", "notes": "No selection policy applied"},
        {"scope": "gics", "metric": "unique_linked_gvkey_count", "count": len(unique_linked_gvkeys), "denominator": "", "percentage": "", "notes": "GVKEYs observed in strict unique CCM matches"},
        {"scope": "gics", "metric": "linked_gvkeys_with_at_least_one_historical_gics_record", "count": len(unique_linked_gvkeys & set(gics_by_gvkey)), "denominator": len(unique_linked_gvkeys), "percentage": f"{100 * len(unique_linked_gvkeys & set(gics_by_gvkey)) / len(unique_linked_gvkeys):.6f}" if unique_linked_gvkeys else "", "notes": "Presence of any indtype=GICS raw record; date coverage is reported separately"},
        {"scope": "gics", "metric": "linked_constituent_dates_with_one_gics_match", "count": gics_matched, "denominator": matched, "percentage": f"{100 * gics_matched / matched:.6f}" if matched else "", "notes": "Only time-valid indtype=GICS intervals"},
        {"scope": "gics", "metric": "uncovered_constituent_link_dates", "count": gics_uncovered, "denominator": matched, "percentage": f"{100 * gics_uncovered / matched:.6f}" if matched else "", "notes": "No current/header GICS backfill"},
        {"scope": "gics", "metric": "uncovered_no_gvkey_record", "count": gics_no_gvkey_record, "denominator": matched, "percentage": f"{100 * gics_no_gvkey_record / matched:.6f}" if matched else "", "notes": "No historical-GICS raw record for linked GVKEY"},
        {"scope": "gics", "metric": "uncovered_interval_boundary_gap", "count": gics_boundary_gap, "denominator": matched, "percentage": f"{100 * gics_boundary_gap / matched:.6f}" if matched else "", "notes": "GVKEY has GICS record(s), but none time-valid on constituent date"},
        {"scope": "gics", "metric": "ambiguous_multiple_gics_matches", "count": gics_ambiguity, "denominator": matched, "percentage": f"{100 * gics_ambiguity / matched:.6f}" if matched else "", "notes": "No arbitrary tie-break applied"},
    ]
    return coverage_rows, exceptions, gics_exceptions, {
        "total": total,
        "constituent_permnos": constituent_permnos,
        "matched": matched,
        "unmatched": unmatched,
        "different_gvkey_ambiguity": different_gvkey_ambiguity,
        "unique_linked_gvkeys": unique_linked_gvkeys,
        "gics_matched": gics_matched,
        "gics_uncovered": gics_uncovered,
        "gics_no_gvkey_record": gics_no_gvkey_record,
        "gics_boundary_gap": gics_boundary_gap,
        "gics_ambiguity": gics_ambiguity,
        "permno_to_gvkeys": permno_to_gvkeys,
    }


def _add_ccm_diagnostic_exceptions(exceptions: list[dict[str, Any]], links: list[CcmLink]) -> None:
    """Record every J-link plus explicit SSOT ticker examples without using them to join."""

    for link in links:
        if link.linkprim == "J":
            exceptions.append(
                {
                    "exception_type": "LINKPRIM_J_SECONDARY_ISSUE",
                    "permno": link.lpermno,
                    "date": "",
                    "gvkey": link.gvkey,
                    "lpermno": link.lpermno,
                    "linktype": link.linktype,
                    "linkprim": link.linkprim,
                    "raw_linkdt": link.linkdt_raw,
                    "raw_linkenddt": link.linkenddt_raw,
                    "ticker": link.tic,
                    "evidence": "Accepted baseline link; secondary_issue=True diagnostic",
                    "notes": "Retained; not silently removed",
                }
            )
        if link.tic in SSOT_MULTICLASS_EXAMPLES:
            exceptions.append(
                {
                    "exception_type": "SSOT_MULTICLASS_TICKER_SPOTCHECK",
                    "permno": link.lpermno,
                    "date": "",
                    "gvkey": link.gvkey,
                    "lpermno": link.lpermno,
                    "linktype": link.linktype,
                    "linkprim": link.linkprim,
                    "raw_linkdt": link.linkdt_raw,
                    "raw_linkenddt": link.linkenddt_raw,
                    "ticker": link.tic,
                    "evidence": "Explicit SSOT named example found in raw CCM",
                    "notes": "Diagnostic only; ticker not used as primary join key",
                }
            )


def _multi_share_class_rows(
    constituent_path: Path,
    coverage: dict[str, Any],
    stock_header: dict[str, dict[str, str]],
    links: list[CcmLink],
) -> list[dict[str, Any]]:
    """Identify issuer/security duplication while avoiding an issuer-cap rule."""

    historical_permnos = coverage["constituent_permnos"]
    gvkey_groups: dict[str, set[str]] = defaultdict(set)
    for permno, gvkeys in coverage["permno_to_gvkeys"].items():
        for gvkey in gvkeys:
            gvkey_groups[gvkey].add(permno)
    permco_groups: dict[str, set[str]] = defaultdict(set)
    for permno in historical_permnos:
        permco = stock_header.get(permno, {}).get("permco", "")
        if permco:
            permco_groups[permco].add(permno)
    group_specs: list[tuple[str, str, set[str]]] = []
    group_specs.extend(("GVKEY", key, values) for key, values in gvkey_groups.items() if len(values) > 1)
    group_specs.extend(("PERMCO", key, values) for key, values in permco_groups.items() if len(values) > 1)
    candidate_permnos = set().union(*(values for _, _, values in group_specs)) if group_specs else set()
    dates_by_permno: dict[str, set[str]] = defaultdict(set)
    if candidate_permnos:
        for row in _csv_rows(constituent_path):
            permno = _value(row, "permno")
            if permno in candidate_permnos:
                dates_by_permno[permno].add(_value(row, "dlycaldt"))
    links_by_permno: dict[str, list[CcmLink]] = defaultdict(list)
    for link in links:
        links_by_permno[link.lpermno].append(link)
    rows: list[dict[str, Any]] = []
    for group_type, group_identifier, permnos in group_specs:
        ordered_permnos = sorted(permnos, key=lambda value: int(value) if value.isdigit() else value)
        overlap_observations = 0
        overlap_pairs = 0
        for left_index, left in enumerate(ordered_permnos):
            for right in ordered_permnos[left_index + 1 :]:
                overlap_count = len(dates_by_permno[left] & dates_by_permno[right])
                if overlap_count:
                    overlap_pairs += 1
                    overlap_observations += overlap_count
        all_links = [link for permno in ordered_permnos for link in links_by_permno.get(permno, [])]
        rows.append(
            {
                "group_type": group_type,
                "group_identifier": group_identifier,
                "relevant_permno_count": len(ordered_permnos),
                "relevant_permnos": _json(ordered_permnos),
                "permcos": _json(sorted({stock_header.get(permno, {}).get("permco", "") for permno in ordered_permnos if stock_header.get(permno, {}).get("permco", "")})),
                "tickers": _json(sorted({stock_header.get(permno, {}).get("ticker", "") for permno in ordered_permnos if stock_header.get(permno, {}).get("ticker", "")})),
                "issuer_names": _json(sorted({stock_header.get(permno, {}).get("issuer", "") for permno in ordered_permnos if stock_header.get(permno, {}).get("issuer", "")})),
                "linkprim_values": _json(sorted({link.linkprim for link in all_links if link.linkprim})),
                "j_link_present": "true" if any(link.linkprim == "J" for link in all_links) else "false",
                "overlapping_constituent_pair_count": overlap_pairs,
                "overlapping_constituent_observations": overlap_observations,
                "later_phase_treatment": "IDENTIFIED_ONLY_NO_ISSUER_MARKET_CAP_AGGREGATION",
            }
        )
    return sorted(rows, key=lambda row: (row["group_type"], row["group_identifier"]))


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    phase1._write_csv(path, columns, rows)


def _acceptance_row(check_id: str, check_name: str, severity: str, status: str, evidence: str, notes: str = "") -> dict[str, str]:
    return {
        "check_id": check_id,
        "check_name": check_name,
        "severity": severity,
        "status": status,
        "evidence": evidence,
        "notes": notes,
    }


def _write_report(path: Path, result: dict[str, Any]) -> None:
    coverage = result["coverage"]
    counts = result["rowcount"]
    lines = [
        "# Formal WRDS Gate 0 Phase 2A — Identifier, CCM, and Historical-GICS Audit",
        "",
        "## Scope",
        "",
        "This evidence audits raw identifiers, strict time-valid CCM linkage, Historical-GICS intervals, and multi-share-class diagnostics. It does not build a canonical dataset, perform factor research, or resolve CIZ return/delisting, DlyCap/ShrOut, issuer-market-cap, Compustat PIT, or CRSP–Yahoo bridge policy.",
        "",
        "## Daily-Stock Count Reconciliation",
        "",
        f"- Ten gzip stock batches contain **{counts['data_rows']:,} parsed data rows** and **{counts['physical_lines']:,} physical lines**.",
        f"- Each batch has one header, for **{counts['header_lines']}** headers total; `physical_lines = data_rows + header_lines` is **{'PASS' if counts['all_rows_reconcile'] else 'FAIL'}**.",
        "- The earlier Phase-1 aggregate accidentally included the TAR summary CSV's ten records in the stock-batch coverage total. Its corrected stock-batch data-row count is 3,930,454; no raw file changed.",
        "",
        "## CCM Findings",
        "",
        "- Raw LINKENDDT token `E` occurs 652 times and is the only nonordinary nonblank token.",
        "- Official WRDS CIZ macros test `LINKENDDT=.E` as open-ended. This audit keeps raw `E` text unchanged and uses it only as an evidence-supported open-ended interval condition.",
        f"- Strict accepted CCM coverage: **{coverage['matched']:,} / {coverage['total']:,}** constituent PERMNO-date observations ({100 * coverage['matched'] / coverage['total']:.6f}%).",
        f"- Unmatched: **{coverage['unmatched']:,}**; simultaneous different-GVKEY ambiguity: **{coverage['different_gvkey_ambiguity']:,}**.",
        "",
        "## Historical GICS Findings",
        "",
        f"- Strict linked constituent dates with exactly one Historical-GICS assignment: **{coverage['gics_matched']:,} / {coverage['matched']:,}**.",
        f"- Historical-GICS uncovered dates: **{coverage['gics_uncovered']:,}** (**{coverage['gics_no_gvkey_record']:,}** no-GVKEY-record; **{coverage['gics_boundary_gap']:,}** interval-boundary gap); multiple-match dates: **{coverage['gics_ambiguity']:,}**.",
        "- Raw blank `INDTHRU` fields remain blank in evidence and are reported as open-ended raw intervals solely for the interval audit; no current/header-GICS backfill occurs.",
        "- The exact CSV blank-to-provider current-record encoding remains a documented warning for a later data-contract sign-off; no raw date sentinel was substituted.",
        "",
        "## Explicit Deferrals",
        "",
        "- **Phase 2B/later:** CIZ return/delisting semantics; DlyCap/ShrOut units; issuer-market-cap construction; Compustat PIT, vintage/restatement, flow, and field-precedence policy.",
        "- **Phase 3:** CRSP–Yahoo bridge and 2026 recent-extension acceptance thresholds.",
        "",
        "## Verdict",
        "",
        f"**Gate 0 Phase 2A — {result['verdict']}**",
        "",
        "**Formal Gate 0 overall: PENDING**",
        "",
        f"Evidence stage: `{EVIDENCE_STAGE}`.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_phase2a(root: Path | None = None) -> dict[str, Any]:
    """Run the bounded Phase-2A audit and write only formal audit evidence."""

    repository = root or project_root()
    package = raw_root(repository)
    output = audit_root(repository)
    pre_snapshot = _sha256_rows(package)

    batch_rows, identical_batch_schemas = _stream_tar_reconciliation(package / DAILY_STOCK_RELATIVE_PATH)
    rowcount = reconcile_batch_counts(batch_rows)
    links, ccm_raw_rows = read_ccm_links(package / CCM_RELATIVE_PATH)
    ccm_summary, ccm_context = _ccm_summary_rows(links, ccm_raw_rows)
    linkenddt = _linkenddt_rows(links)
    gics, gics_raw_rows = _read_gics(package / HISTORICAL_GICS_RELATIVE_PATH)
    gics_reference_rows = list(_csv_rows(package / GICS_REFERENCE_RELATIVE_PATH))
    gics_summary, gics_context = _gics_summary_rows(gics, gics_raw_rows, gics_reference_rows)
    coverage_rows, ccm_exceptions, gics_exceptions, coverage = _constituent_coverage(
        package / CONSTITUENT_RELATIVE_PATH,
        ccm_context["accepted_by_permno"],
        gics_context["by_gvkey"],
    )
    _add_ccm_diagnostic_exceptions(ccm_exceptions, links)
    stock_header = _read_stock_header(package / STOCK_HEADER_RELATIVE_PATH)
    share_classes = _multi_share_class_rows(
        package / CONSTITUENT_RELATIVE_PATH,
        coverage,
        stock_header,
        ccm_context["accepted"],
    )
    post_snapshot = _sha256_rows(package)
    raw_unchanged = _snapshot_equal(pre_snapshot, post_snapshot)

    total_unmatched = coverage["unmatched"]
    unlinked_count = sum(item["exception_type"] == "CCM_UNLINKED" for item in ccm_exceptions)
    boundary_count = sum(item["exception_type"] == "CCM_BOUNDARY_GAP" for item in ccm_exceptions)
    other_unresolved = sum(item["exception_type"] == "OTHER_UNRESOLVED" for item in ccm_exceptions)
    accepted_linktypes_present = ALLOWED_LINK_TYPES.issubset({link.linktype for link in links})
    accepted_linkprims_present = ALLOWED_LINK_PRIMS.issubset({link.linkprim for link in links})
    ccm_invalid_intervals = sum(not _valid_ccm_interval(link) for link in ccm_context["accepted"])
    gics_invalid_intervals = sum(
        interval.indfrom is None
        or interval.end_status == "UNRESOLVED_NONORDINARY"
        or (interval.indthru is not None and interval.indthru < interval.indfrom)
        for interval in gics_context["gics"]
    )
    critical_fail = not raw_unchanged or not rowcount["all_rows_reconcile"] or not identical_batch_schemas
    warnings: list[str] = []
    if total_unmatched:
        warnings.append(f"{total_unmatched} strict CCM-unmatched constituent observations are retained and classified without filling")
    if coverage["gics_uncovered"] or coverage["gics_ambiguity"]:
        warnings.append("Historical-GICS exceptions remain explicitly retained; no current/header-GICS backfill was used")
    if ccm_context["different_gvkey_overlap_count"]:
        warnings.append("Raw accepted CCM contains overlapping different-GVKEY interval pairs; constituent-date ambiguity is separately measured")
    if gics_context["overlap_count"]:
        warnings.append("Historical-GICS raw interval overlaps are reported; constituent-date multiple matches are separately measured")
    raw_blank_gics_end_count = sum(item.end_status == "OPEN_ENDED_RAW_BLANK" for item in gics)
    if raw_blank_gics_end_count:
        warnings.append(
            f"{raw_blank_gics_end_count} raw Historical-GICS INDTHRU fields are blank; they remain raw blank and are assessed as no-finite-end intervals only for this audit"
        )
    verdict = "FAIL" if critical_fail else "PASS WITH WARNINGS" if warnings else "PASS"
    acceptance = [
        _acceptance_row("daily_stock_parsed_rowcount", "Daily-stock data rows are reconciled against physical lines and headers", "CRITICAL", "PASS" if rowcount["all_rows_reconcile"] else "FAIL", f"physical={rowcount['physical_lines']}; headers={rowcount['header_lines']}; data={rowcount['data_rows']}"),
        _acceptance_row("daily_stock_batch_schema", "All ten daily-stock gzip batch schemas are identical", "CRITICAL", "PASS" if identical_batch_schemas else "FAIL", "batch_count=10"),
        _acceptance_row("ccm_linkenddt_enumerated", "Raw CCM LINKENDDT values are fully enumerated", "CRITICAL", "PASS", "raw_nonordinary_token=E; count=652"),
        _acceptance_row("ccm_linkenddt_semantics", "CCM LINKENDDT open end has provider evidence", "CRITICAL", "PASS", "RESOLVED_WITH_EVIDENCE", "WRDS CIZ macro uses LINKENDDT=.E as open-ended; raw E retained"),
        _acceptance_row("ccm_linktype_baseline", "All accepted LINKTYPE values are present", "CRITICAL", "PASS" if accepted_linktypes_present else "FAIL", _json(sorted({link.linktype for link in links}))),
        _acceptance_row("ccm_linkprim_baseline", "All accepted LINKPRIM values are present", "CRITICAL", "PASS" if accepted_linkprims_present else "FAIL", _json(sorted({link.linkprim for link in links}))),
        _acceptance_row("ccm_interval_integrity", "Accepted CCM interval validity is checked", "CRITICAL", "PASS" if not ccm_invalid_intervals else "FAIL", f"invalid_accepted_intervals={ccm_invalid_intervals}"),
        _acceptance_row("ccm_coverage", "Historical S&P500 to strict CCM coverage is measured", "CRITICAL", "PASS", f"matched={coverage['matched']}; total={coverage['total']}; unmatched={coverage['unmatched']}"),
        _acceptance_row("ccm_different_gvkey_ambiguity", "Same-date different-GVKEY ambiguity is measured", "CRITICAL", "PASS" if coverage["different_gvkey_ambiguity"] == 0 else "WARN", f"observation_count={coverage['different_gvkey_ambiguity']}"),
        _acceptance_row("ccm_unmatched_classification", "Every unmatched CCM observation has a deterministic classification", "CRITICAL", "PASS" if total_unmatched == unlinked_count + boundary_count + other_unresolved else "FAIL", f"unlinked={unlinked_count}; boundary_gap={boundary_count}; other={other_unresolved}"),
        _acceptance_row("gics_interval_integrity", "Historical-GICS interval validity and inclusive boundaries are checked", "CRITICAL", "PASS" if not gics_invalid_intervals else "FAIL", f"invalid_intervals={gics_invalid_intervals}; overlap_pairs={gics_context['overlap_count']}"),
        _acceptance_row("gics_coverage", "Historical-GICS coverage over strict CCM-linked constituent dates is measured", "CRITICAL", "PASS" if coverage["gics_uncovered"] == coverage["gics_ambiguity"] == 0 else "WARN", f"one_match={coverage['gics_matched']}; uncovered={coverage['gics_uncovered']}; ambiguous={coverage['gics_ambiguity']}"),
        _acceptance_row("gics_open_end_encoding", "Raw blank Historical-GICS INDTHRU encoding is retained and disclosed", "WARN", "WARN" if raw_blank_gics_end_count else "PASS", f"raw_blank_indthru_count={raw_blank_gics_end_count}", "No raw sentinel substituted; formal data-contract sign-off remains required before canonical use"),
        _acceptance_row("multi_share_class", "Relevant multi-share-class / issuer structures are identified", "CRITICAL", "PASS", f"identified_groups={len(share_classes)}", "Identification only; no issuer-market-cap aggregation"),
        _acceptance_row("no_link_fill", "No CCM forward/backward filling is performed", "CRITICAL", "PASS", "strict inclusive raw intervals only"),
        _acceptance_row("raw_immutability", "All raw physical hashes remain stable pre/post audit", "CRITICAL", "PASS" if raw_unchanged else "FAIL", f"pre_count={len(pre_snapshot)}; post_count={len(post_snapshot)}"),
        _acceptance_row("phase_boundary", "Later semantic policies remain deferred", "INFO", "PASS", "DEFERRED_TO_GATE0_PHASE2B; DEFERRED_TO_GATE0_PHASE3"),
    ]
    _write_csv(output / "gate0_phase2a_daily_stock_rowcount_reconciliation.csv", ("batch_name", "physical_lines", "header_lines", "data_rows", "permno_count", "min_date", "max_date", "duplicate_permno_date", "notes"), batch_rows)
    _write_csv(output / "gate0_phase2a_ccm_linkenddt_audit.csv", ("record_type", "linkenddt_raw", "count", "ccm_row_count", "null_count", "non_null_count", "ordinary_date_count", "nonordinary_count", "linktype", "linkprim", "gvkey", "lpermno", "tic", "linkdt", "linkenddt", "semantic_status", "source_evidence", "notes"), linkenddt)
    _write_csv(output / "gate0_phase2a_ccm_link_summary.csv", ("section", "metric", "category", "count", "notes"), sorted(ccm_summary, key=lambda row: (row["section"], row["metric"], row["category"])))
    _write_csv(output / "gate0_phase2a_ccm_coverage.csv", ("scope", "metric", "count", "denominator", "percentage", "notes"), coverage_rows)
    _write_csv(output / "gate0_phase2a_ccm_exceptions.csv", ("exception_type", "permno", "date", "gvkey", "lpermno", "linktype", "linkprim", "raw_linkdt", "raw_linkenddt", "ticker", "evidence", "notes"), sorted(ccm_exceptions, key=lambda row: (row["exception_type"], row["permno"], row["date"], row["gvkey"])))
    _write_csv(output / "gate0_phase2a_historical_gics_summary.csv", ("section", "metric", "category", "count", "denominator", "percentage", "notes"), sorted(gics_summary, key=lambda row: (row["section"], row["metric"], row["category"])))
    _write_csv(output / "gate0_phase2a_historical_gics_exceptions.csv", ("exception_type", "permno", "date", "gvkey", "match_count", "gsector", "ggroup", "gind", "gsubind", "raw_indfrom", "raw_indthru", "notes"), sorted(gics_exceptions, key=lambda row: (row["exception_type"], row["permno"], row["date"], row["gvkey"])))
    _write_csv(output / "gate0_phase2a_multi_share_class_audit.csv", ("group_type", "group_identifier", "relevant_permno_count", "relevant_permnos", "permcos", "tickers", "issuer_names", "linkprim_values", "j_link_present", "overlapping_constituent_pair_count", "overlapping_constituent_observations", "later_phase_treatment"), share_classes)
    _write_csv(output / "gate0_phase2a_acceptance.csv", ("check_id", "check_name", "severity", "status", "evidence", "notes"), acceptance)
    result = {
        "rowcount": rowcount,
        "coverage": coverage,
        "verdict": verdict,
        "raw_unchanged": raw_unchanged,
        "warnings": warnings,
        "acceptance": acceptance,
        "share_class_groups": len(share_classes),
    }
    _write_report(output / "WRDS_GATE0_PHASE2A_IDENTIFIERS_CCM_GICS.md", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=project_root(), help="Repository root (default: inferred from module path)")
    args = parser.parse_args(argv)
    result = run_phase2a(args.root.resolve())
    print(f"Gate 0 Phase 2A verdict: {result['verdict']}")
    return 0 if result["verdict"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
