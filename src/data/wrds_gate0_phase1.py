"""Formal WRDS Gate 0 Phase 1: immutable raw-data integrity audit.

This module inventories and streams the delivered WRDS raw layer.  It does not
construct a canonical dataset, choose a point-in-time policy, or interpret
financial, return, or market-cap semantics.  All outputs are limited to the
formal Phase-1 audit directory.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import sqlite3
import tarfile
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, TextIO


PACKAGE_RELATIVE_PATH = Path("wrds_raw") / "US_WRDS_2026-10-09"
AUDIT_RELATIVE_PATH = Path("outputs") / "audit" / "wrds_formal"
EVIDENCE_STAGE = "FORMAL_GATE0_PHASE1_RAW_AUDIT"

REQUIRED_FILES = (
    "ccm/CCM_CRSP_Compustat_Link_LC_LU_LS_FullHistory_959_SP500_PERMNOs.csv.gz",
    "compustat/Compustat_GICS_Code_Reference.csv.gz",
    "compustat/Compustat_Historical_GICS_FullHistory.csv.gz",
    "compustat/Compustat_NA_Fundamentals_Annual_2004-01_2026-10.csv.gz",
    "compustat/Compustat_NA_Fundamentals_Quarterly_2004-01_2026-10.csv.gz",
    "crsp/CRSP_Daily_Market_and_SP500_Indexes_2004-01-01_2025-12-31.csv.gz",
    "crsp/CRSP_Daily_Stock_CIZ_2004-01-01_2025-12-31.tar",
    "crsp/CRSP_Delisting_Information_CIZ_2004-01-01_2025-12-30_959_SP500_PERMNOs.csv.gz",
    "crsp/CRSP_Distribution_CIZ_2004-01-01_2025-12-31_959_SP500_PERMNOs.csv.gz",
    "crsp/CRSP_Names_CIZ_FullHistory_SP500_Historical_PERMNOs.csv.gz",
    "crsp/CRSP_SP500_Constituents_Daily_CIZ_2006-01-01_2025-12-31.csv.gz",
    "crsp/CRSP_Share_Outstanding_CIZ_FullHistory_2025-12.csv.gz",
    "crsp/CRSP_Stock_Header_CIZ_FullHistory_959_SP500_PERMNOs.csv.gz",
    "fama_french/Fama_French_5Factors_Momentum_RF_Daily_FullHistory_to_2026-08-31.csv.gz",
    "fama_french/Fama_French_5Factors_Momentum_RF_Monthly_FullHistory_to_2026-08.csv.gz",
    "query_inputs/sp500_historical_permnos_2006_2025.txt",
)
OPTIONAL_FILES = ("metadata/CRSP_Selected_Index_Metadata.csv.gz",)

DATE_FIELD_NAMES = {
    "date",
    "dateff",
    "datadate",
    "rdq",
    "linkdt",
    "linkenddt",
    "indfrom",
    "indthru",
    "mbrstartdt",
    "mbrenddt",
    "dlycaldt",
    "shrstartdt",
    "shrenddt",
    "secinfostartdt",
    "secinfoenddt",
    "securitybegdt",
    "securityenddt",
    "delistingdt",
    "deldlydt",
    "delnextdt",
    "disexdt",
    "disdeclaredt",
    "disrecorddt",
    "dispaydt",
    "ipodate",
    "dldte",
}
IDENTIFIER_FIELD_NAMES = {
    "permno",
    "lpermno",
    "permco",
    "lpermco",
    "gvkey",
    "indno",
    "cik",
    "giccd",
    "ticker",
    "tic",
    "cusip",
}
PRIMARY_IDENTIFIER_PRIORITY = ("permno", "gvkey", "indno", "giccd", "cik", "ticker", "tic")

NATURAL_KEYS = {
    "crsp/CRSP_SP500_Constituents_Daily_CIZ_2006-01-01_2025-12-31.csv.gz": ("permno", "dlycaldt"),
    "crsp/CRSP_Daily_Market_and_SP500_Indexes_2004-01-01_2025-12-31.csv.gz": ("indno", "dlycaldt"),
    "query_inputs/sp500_historical_permnos_2006_2025.txt": ("permno",),
}
TAR_BATCH_KEY = ("permno", "dlycaldt")


@dataclass(frozen=True)
class FileSnapshot:
    """Physical evidence for one immutable raw file."""

    relative_path: str
    size_bytes: int
    sha256: str


class KeyTracker:
    """Disk-backed exact duplicate checker for an unambiguous raw key."""

    def __init__(self, key_fields: tuple[str, ...]) -> None:
        self.key_fields = key_fields
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="wrds_gate0_phase1_keys_")
        self._connection = sqlite3.connect(Path(self._temporary_directory.name) / "keys.sqlite")
        self._connection.execute("CREATE TABLE seen_keys (key_value TEXT PRIMARY KEY)")
        self._pending: list[tuple[str]] = []
        self.rows_seen = 0
        self.missing_key_count = 0
        self.duplicate_count = 0

    def add(self, values: tuple[str, ...]) -> None:
        self.rows_seen += 1
        if any(not value.strip() for value in values):
            self.missing_key_count += 1
            return
        self._pending.append((json.dumps(values, separators=(",", ":")),))
        if len(self._pending) >= 5_000:
            self.flush()

    def flush(self) -> None:
        if not self._pending:
            return
        before = self._connection.total_changes
        self._connection.executemany("INSERT OR IGNORE INTO seen_keys(key_value) VALUES (?)", self._pending)
        inserted = self._connection.total_changes - before
        self.duplicate_count += len(self._pending) - inserted
        self._connection.commit()
        self._pending.clear()

    def close(self) -> None:
        self.flush()
        self._connection.close()
        self._temporary_directory.cleanup()


def project_root() -> Path:
    """Return the repository root based on this checked-in module."""

    return Path(__file__).resolve().parents[2]


def raw_root(root: Path | None = None) -> Path:
    """Return the immutable raw WRDS package directory."""

    return (root or project_root()) / PACKAGE_RELATIVE_PATH


def audit_root(root: Path | None = None) -> Path:
    """Return the formal Phase-1 audit-output directory."""

    return (root or project_root()) / AUDIT_RELATIVE_PATH


def sha256_file(path: Path) -> str:
    """Hash a physical file deterministically without decoding it."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot_raw_files(package: Path) -> list[FileSnapshot]:
    """Return a sorted complete physical snapshot of the raw package."""

    return [
        FileSnapshot(
            relative_path=path.relative_to(package).as_posix(),
            size_bytes=path.stat().st_size,
            sha256=sha256_file(path),
        )
        for path in sorted((item for item in package.rglob("*") if item.is_file()), key=lambda item: item.as_posix())
    ]


def _file_type(path: Path) -> str:
    if path.name.endswith(".csv.gz"):
        return "CSV_GZIP"
    if path.suffix.lower() == ".tar":
        return "TAR"
    if path.suffix.lower() == ".txt":
        return "TEXT"
    return path.suffix.lower().lstrip(".").upper() or "UNKNOWN"


def _source_family(relative_path: str) -> str:
    return relative_path.split("/", 1)[0]


def _parse_date(value: str) -> date | None:
    candidate = value.strip()
    if not candidate:
        return None
    for pattern in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(candidate, pattern).date()
        except ValueError:
            continue
    return None


def _date_columns(columns: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(column for column in columns if column.lower() in DATE_FIELD_NAMES)


def _identifier_columns(columns: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(column for column in columns if column.lower() in IDENTIFIER_FIELD_NAMES)


def _primary_identifier(columns: tuple[str, ...]) -> str | None:
    by_lower = {column.lower(): column for column in columns}
    for candidate in PRIMARY_IDENTIFIER_PRIORITY:
        if candidate in by_lower:
            return by_lower[candidate]
    return None


def _serialize(values: Iterable[str]) -> str:
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def _inspect_csv_stream(
    stream: TextIO,
    *,
    relative_path: str,
    object_name: str,
    natural_key: tuple[str, ...] | None = None,
    shared_tracker: KeyTracker | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Stream a CSV object and return schema evidence plus optional coverage."""

    reader = csv.DictReader(stream)
    columns = tuple(reader.fieldnames or ())
    if not columns:
        raise ValueError(f"CSV object has no header: {object_name}")
    lower_columns = {column.lower(): column for column in columns}
    date_columns = _date_columns(columns)
    identifier_columns = _identifier_columns(columns)
    primary_identifier = _primary_identifier(columns)
    missing_natural_key = tuple(key for key in (natural_key or ()) if key not in lower_columns)
    tracker = shared_tracker
    owns_tracker = False
    if natural_key and not missing_natural_key and tracker is None:
        tracker = KeyTracker(natural_key)
        owns_tracker = True

    row_count = 0
    primary_identifiers: set[str] = set()
    missing_primary_identifier_count = 0
    date_ranges: dict[str, list[date | None]] = {column: [None, None] for column in date_columns}
    invalid_date_counts: Counter[str] = Counter()
    for row in reader:
        row_count += 1
        if primary_identifier:
            primary_value = (row.get(primary_identifier) or "").strip()
            if primary_value:
                primary_identifiers.add(primary_value)
            else:
                missing_primary_identifier_count += 1
        for column in date_columns:
            raw_value = row.get(column) or ""
            parsed = _parse_date(raw_value)
            if raw_value.strip() and parsed is None:
                invalid_date_counts[column] += 1
                continue
            if parsed is not None:
                lower, upper = date_ranges[column]
                date_ranges[column][0] = parsed if lower is None or parsed < lower else lower
                date_ranges[column][1] = parsed if upper is None or parsed > upper else upper
        if tracker is not None and natural_key and not missing_natural_key:
            tracker.add(tuple(row.get(lower_columns[key], "") or "" for key in natural_key))

    if owns_tracker and tracker is not None:
        tracker.close()
    flattened_dates = [bound for bounds in date_ranges.values() for bound in bounds if bound is not None]
    notes: list[str] = []
    if invalid_date_counts:
        notes.append("invalid_nonblank_date_cells=" + json.dumps(dict(sorted(invalid_date_counts.items()))))
    if missing_natural_key:
        notes.append("natural_key_not_available=" + _serialize(missing_natural_key))
    schema = {
        "relative_path": relative_path,
        "object_name": object_name,
        "row_count": row_count,
        "column_count": len(columns),
        "columns": _serialize(columns),
        "identifier_fields": _serialize(identifier_columns),
        "primary_identifier_count": len(primary_identifiers) if primary_identifier else "",
        "primary_identifier_values": primary_identifiers,
        "date_fields": _serialize(date_columns),
        "observed_min_date": min(flattened_dates).isoformat() if flattened_dates else "",
        "observed_max_date": max(flattened_dates).isoformat() if flattened_dates else "",
        "date_ranges": json.dumps(
            {
                column: [bound.isoformat() if bound else "" for bound in bounds]
                for column, bounds in sorted(date_ranges.items())
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "schema_status": "PASS",
        "invalid_nonblank_date_cell_count": sum(invalid_date_counts.values()),
        "notes": "; ".join(notes),
    }
    coverage: dict[str, Any] | None = None
    if natural_key and not missing_natural_key:
        natural_key_date_columns = [lower_columns[key] for key in natural_key if key in DATE_FIELD_NAMES]
        coverage_date_bounds = (
            [bound for column in natural_key_date_columns for bound in date_ranges[column] if bound is not None]
            or flattened_dates
        )
        duplicate_count = "" if shared_tracker is not None else tracker.duplicate_count if tracker else ""
        missing_key_count = "" if shared_tracker is not None else tracker.missing_key_count if tracker else ""
        coverage = {
            "source_object": object_name,
            "row_count": row_count,
            "unique_primary_identifier_count": len(primary_identifiers) if primary_identifier else "",
            "min_date": min(coverage_date_bounds).isoformat() if coverage_date_bounds else "",
            "max_date": max(coverage_date_bounds).isoformat() if coverage_date_bounds else "",
            "basic_duplicate_count_if_unambiguous": duplicate_count,
            "missing_key_count_if_unambiguous": missing_key_count,
            "notes": "natural_key=" + _serialize(natural_key),
        }
    return schema, coverage


def inspect_gzip_csv(path: Path, relative_path: str, natural_key: tuple[str, ...] | None = None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Read a gzip CSV to EOF, validating gzip CRC while collecting schema facts."""

    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
        return _inspect_csv_stream(
            stream,
            relative_path=relative_path,
            object_name=Path(relative_path).name,
            natural_key=natural_key,
        )


def inspect_query_input(path: Path, relative_path: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Inspect the newline-delimited PERMNO query input without assuming vendor schema."""

    tracker = KeyTracker(("permno",))
    identifiers: set[str] = set()
    row_count = 0
    blank_count = 0
    non_numeric_count = 0
    with path.open("rt", encoding="utf-8-sig", newline="") as stream:
        for line in stream:
            value = line.strip()
            if not value:
                blank_count += 1
                continue
            row_count += 1
            identifiers.add(value)
            if not value.isdigit():
                non_numeric_count += 1
            tracker.add((value,))
    tracker.close()
    notes = f"blank_line_count={blank_count}; non_numeric_identifier_count={non_numeric_count}"
    schema = {
        "relative_path": relative_path,
        "object_name": path.name,
        "row_count": row_count,
        "column_count": 1,
        "columns": _serialize(("PERMNO",)),
        "identifier_fields": _serialize(("PERMNO",)),
        "date_fields": _serialize(()),
        "observed_min_date": "",
        "observed_max_date": "",
        "date_ranges": "{}",
        "schema_status": "PASS" if not non_numeric_count else "WARN",
        "notes": notes,
    }
    coverage = {
        "source_object": path.name,
        "row_count": row_count,
        "unique_primary_identifier_count": len(identifiers),
        "min_date": "",
        "max_date": "",
        "basic_duplicate_count_if_unambiguous": tracker.duplicate_count,
        "missing_key_count_if_unambiguous": tracker.missing_key_count,
        "notes": "natural_key=[\"permno\"]; " + notes,
    }
    return schema, coverage


def inspect_tar(path: Path, relative_path: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], str]:
    """Safely inspect all TAR members, including each compressed daily-stock batch."""

    schema_rows: list[dict[str, Any]] = []
    tar_member_rows: list[dict[str, Any]] = []
    batch_tracker = KeyTracker(TAR_BATCH_KEY)
    total_rows = 0
    identifiers: set[str] = set()
    overall_min: date | None = None
    overall_max: date | None = None
    gzip_batch_count = 0
    try:
        with tarfile.open(path, "r") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                object_name = member.name
                member_stream = archive.extractfile(member)
                if member_stream is None:
                    raise tarfile.TarError(f"Unable to stream TAR member: {member.name}")
                try:
                    if member.name.endswith(".csv.gz"):
                        gzip_batch_count += 1
                        with gzip.GzipFile(fileobj=member_stream, mode="rb") as binary_stream:
                            with io.TextIOWrapper(binary_stream, encoding="utf-8-sig", newline="") as text_stream:
                                schema, _ = _inspect_csv_stream(
                                    text_stream,
                                    relative_path=relative_path,
                                    object_name=object_name,
                                    natural_key=TAR_BATCH_KEY,
                                    shared_tracker=batch_tracker,
                                )
                    elif member.name.endswith(".csv"):
                        with io.TextIOWrapper(member_stream, encoding="utf-8-sig", newline="") as text_stream:
                            schema, _ = _inspect_csv_stream(
                                text_stream,
                                relative_path=relative_path,
                                object_name=object_name,
                            )
                    else:
                        tar_member_rows.append(
                            {
                                "member_name": member.name,
                                "member_size": member.size,
                                "row_count": "",
                                "column_count": "",
                                "columns": "",
                                "min_date": "",
                                "max_date": "",
                                "permno_count": "",
                                "readable": "PASS",
                                "notes": "Non-CSV TAR member inspected as readable binary content",
                            }
                        )
                        continue
                finally:
                    member_stream.close()
                schema_rows.append(schema)
                if member.name.endswith(".csv.gz"):
                    total_rows += int(schema["row_count"])
                    identifiers.update(schema["primary_identifier_values"])
                    member_min = _parse_date(schema["observed_min_date"])
                    member_max = _parse_date(schema["observed_max_date"])
                    overall_min = member_min if overall_min is None or (member_min and member_min < overall_min) else overall_min
                    overall_max = member_max if overall_max is None or (member_max and member_max > overall_max) else overall_max
                tar_member_rows.append(
                    {
                        "member_name": member.name,
                        "member_size": member.size,
                        "row_count": schema["row_count"],
                        "column_count": schema["column_count"],
                        "columns": schema["columns"],
                        "min_date": schema["observed_min_date"],
                        "max_date": schema["observed_max_date"],
                        "permno_count": schema["primary_identifier_count"],
                        "readable": "PASS",
                        "notes": schema["notes"],
                    }
                )
    except (OSError, EOFError, csv.Error, tarfile.TarError) as exc:
        batch_tracker.close()
        return schema_rows, tar_member_rows, {}, f"FAIL: {type(exc).__name__}: {exc}"
    batch_tracker.close()
    coverage = {
        "source_object": path.name + "::all_daily_stock_batches",
        "row_count": total_rows,
        "unique_primary_identifier_count": len(identifiers),
        "min_date": overall_min.isoformat() if overall_min else "",
        "max_date": overall_max.isoformat() if overall_max else "",
        "basic_duplicate_count_if_unambiguous": batch_tracker.duplicate_count,
        "missing_key_count_if_unambiguous": batch_tracker.missing_key_count,
        "notes": f"natural_key={_serialize(TAR_BATCH_KEY)}; gzip_batch_count={gzip_batch_count}",
    }
    return schema_rows, tar_member_rows, coverage, "PASS"


def _write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _acceptance_row(check_id: str, check_name: str, severity: str, status: str, evidence: str, notes: str = "") -> dict[str, str]:
    return {
        "check_id": check_id,
        "check_name": check_name,
        "severity": severity,
        "status": status,
        "evidence": evidence,
        "notes": notes,
    }


def _snapshot_map(snapshot: list[FileSnapshot]) -> dict[str, tuple[int, str]]:
    return {item.relative_path: (item.size_bytes, item.sha256) for item in snapshot}


def classify_expected_paths(actual_paths: set[str]) -> dict[str, list[str]]:
    """Classify physical raw paths without making any semantic data judgement."""

    required = set(REQUIRED_FILES)
    optional = set(OPTIONAL_FILES)
    return {
        "present_required": sorted(required & actual_paths),
        "missing_required": sorted(required - actual_paths),
        "unexpected_files": sorted(actual_paths - required - optional),
        "optional_present": sorted(optional & actual_paths),
    }


def gzip_distinct_values(path: Path, field_name: str) -> set[str]:
    """Return nonblank raw values for one named field, without semantic interpretation."""

    values: set[str] = set()
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        actual_names = {name.lower(): name for name in reader.fieldnames or ()}
        if field_name.lower() not in actual_names:
            return values
        actual_name = actual_names[field_name.lower()]
        for row in reader:
            value = (row.get(actual_name) or "").strip()
            if value:
                values.add(value)
    return values


def _write_report(path: Path, result: dict[str, Any]) -> None:
    required = result["expected"]
    lines = [
        "# Formal WRDS Gate 0 Phase 1 — Raw Integrity, Inventory, Schema, and Basic Coverage",
        "",
        "## Scope",
        "",
        "This report covers physical raw-source integrity, compressed-container readability, schema, and basic raw coverage for `wrds_raw/US_WRDS_2026-10-09/`. It does not construct formal canonical data or settle any Phase-2/3 data-contract semantics.",
        "",
        "## Methodology",
        "",
        "- Captured SHA256 and byte-size evidence for every physical raw file before the audit.",
        "- Streamed gzip CSVs to EOF and inspected TAR members without permanent extraction.",
        "- Recorded headers, row counts, identifiable date ranges, raw identifiers, and only unambiguous duplicate checks.",
        "- Recomputed the physical raw snapshot after audit-output generation and compared every path, size, and SHA256.",
        "",
        "## Raw Inventory Summary",
        "",
        f"- Physical raw files audited: **{result['physical_file_count']}**.",
        f"- Required files: **{len(REQUIRED_FILES)}**; present: **{len(required['present_required'])}**; missing: **{len(required['missing_required'])}**.",
        f"- Unexpected physical files: **{len(required['unexpected_files'])}**.",
        f"- Optional metadata file `CRSP_Selected_Index_Metadata.csv.gz`: **{'present' if required['optional_present'] else 'absent (allowed)'}**.",
        f"- TAR gzip batch members: **{result['tar_batch_count']}** (expected 10).",
        "",
        "## Integrity and Schema Findings",
        "",
        f"- Raw pre/post SHA256 and size equality: **{'PASS' if result['raw_unchanged'] else 'FAIL'}** for all physical raw files.",
        f"- Gzip / TAR readability: **{'PASS' if result['all_readable'] else 'FAIL'}**.",
        f"- Schema rows recorded: **{len(result['schema_rows'])}**; basic-coverage rows recorded: **{len(result['coverage_rows'])}**.",
        f"- Query-input PERMNO lines / unique values: **{result['query_row_count']} / {result['query_unique_permno_count']}**.",
        f"- Observed formal raw facts are reported in the CSV inventories; no contrary result was normalized to an expected value.",
        "",
        "## Key Raw Coverage",
        "",
    ]
    for label, coverage in result["key_coverage"].items():
        lines.append(
            f"- {label}: rows **{coverage.get('row_count', '')}**; primary identifiers **{coverage.get('unique_primary_identifier_count', '')}**; dates **{coverage.get('min_date', '')}** to **{coverage.get('max_date', '')}**; unambiguous-key duplicates **{coverage.get('basic_duplicate_count_if_unambiguous', '')}**."
        )
    lines.extend(
        [
            f"- CCM raw `LINKTYPE` values observed: **{', '.join(result['ccm_linktype_values']) or '(none)'}**.",
            "- Historical-GICS and Fama–French headers/date coverage are recorded in `raw_schema_inventory.csv`; this phase records field presence only and does not freeze their downstream semantics.",
            "",
            "## Warnings",
            "",
        ]
    )
    warnings = result["warnings"]
    lines.extend([f"- {warning}" for warning in warnings] if warnings else ["- None for raw integrity. The optional metadata file is absent as allowed."])
    lines.extend(
        [
            "",
            "## Explicitly Deferred Beyond Phase 1",
            "",
            "- CRSP CIZ return, corporate-action, and delisting semantics.",
            "- DlyCap / ShrOut economic unit interpretation and issuer-level market-cap construction.",
            "- CCM mapping-policy verification and Historical-GICS effective-date merge validation.",
            "- Compustat annual PIT availability, vintage/restatement semantics, quarterly YTD flow differencing, and field precedence.",
            "- CRSP–Yahoo bridge acceptance thresholds.",
            "",
            "## Verdict",
            "",
            f"**Gate 0 Phase 1 — Raw Integrity & Inventory: {result['verdict']}**",
            "**Formal Gate 0 overall: PENDING**",
            "",
            f"Evidence stage for this output: `{EVIDENCE_STAGE}`.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_phase1(root: Path | None = None) -> dict[str, Any]:
    """Run the narrow Phase-1 audit and write only formal audit artefacts."""

    repository = root or project_root()
    package = raw_root(repository)
    output_directory = audit_root(repository)
    if not package.is_dir():
        raise FileNotFoundError(f"WRDS raw package not found: {package}")

    pre_snapshot = snapshot_raw_files(package)
    actual_paths = {item.relative_path for item in pre_snapshot}
    path_classification = classify_expected_paths(actual_paths)
    expected_required = set(REQUIRED_FILES)
    expected_optional = set(OPTIONAL_FILES)
    missing_required = path_classification["missing_required"]
    unexpected_files = path_classification["unexpected_files"]
    optional_present = path_classification["optional_present"]
    manifest_by_path = {item.relative_path: item for item in pre_snapshot}
    duplicate_hashes: dict[str, list[str]] = {}
    for snapshot in pre_snapshot:
        duplicate_hashes.setdefault(snapshot.sha256, []).append(snapshot.relative_path)
    duplicate_hashes = {digest: paths for digest, paths in duplicate_hashes.items() if len(paths) > 1}

    schema_rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    tar_member_rows: list[dict[str, Any]] = []
    inspection_status: dict[str, tuple[str, str]] = {}
    query_row_count = 0
    query_unique_permno_count = 0
    tar_batch_count = 0
    for snapshot in pre_snapshot:
        relative_path = snapshot.relative_path
        path = package / relative_path
        try:
            if relative_path.endswith(".csv.gz"):
                schema, coverage = inspect_gzip_csv(path, relative_path, NATURAL_KEYS.get(relative_path))
                schema_rows.append(schema)
                if coverage:
                    coverage_rows.append(coverage)
                inspection_status[relative_path] = ("PASS", "PASS")
            elif relative_path.endswith(".tar"):
                tar_schemas, members, coverage, tar_status = inspect_tar(path, relative_path)
                schema_rows.extend(tar_schemas)
                tar_member_rows.extend(members)
                if coverage:
                    coverage_rows.append(coverage)
                    tar_batch_count = int(coverage["notes"].split("gzip_batch_count=")[-1])
                inspection_status[relative_path] = ("PASS" if tar_status == "PASS" else "FAIL", tar_status)
            elif relative_path.endswith(".txt"):
                schema, coverage = inspect_query_input(path, relative_path)
                schema_rows.append(schema)
                coverage_rows.append(coverage)
                query_row_count = int(schema["row_count"])
                query_unique_permno_count = int(coverage["unique_primary_identifier_count"])
                inspection_status[relative_path] = ("PASS", "NOT_COMPRESSED")
            else:
                inspection_status[relative_path] = ("WARN", "NOT_INSPECTED_UNKNOWN_TYPE")
        except (OSError, EOFError, UnicodeError, csv.Error, tarfile.TarError, ValueError) as exc:
            inspection_status[relative_path] = ("FAIL", f"FAIL: {type(exc).__name__}: {exc}")

    post_snapshot = snapshot_raw_files(package)
    raw_unchanged = _snapshot_map(pre_snapshot) == _snapshot_map(post_snapshot)
    all_readable = all(status == "PASS" for status, _ in inspection_status.values())
    physical_manifest_rows: list[dict[str, Any]] = []
    for snapshot in pre_snapshot:
        status, integrity = inspection_status.get(snapshot.relative_path, ("FAIL", "NOT_INSPECTED"))
        note_parts = []
        if snapshot.relative_path in unexpected_files:
            note_parts.append("Unexpected physical raw file")
        for digest, paths in duplicate_hashes.items():
            if snapshot.relative_path in paths:
                note_parts.append("duplicate_physical_sha256_with=" + _serialize(path for path in paths if path != snapshot.relative_path))
        physical_manifest_rows.append(
            {
                "relative_path": snapshot.relative_path,
                "source_family": _source_family(snapshot.relative_path),
                "filename": Path(snapshot.relative_path).name,
                "file_type": _file_type(package / snapshot.relative_path),
                "size_bytes": snapshot.size_bytes,
                "sha256": snapshot.sha256,
                "readable": status,
                "compression_integrity": integrity,
                "expected_status": "REQUIRED" if snapshot.relative_path in expected_required else "OPTIONAL" if snapshot.relative_path in expected_optional else "UNEXPECTED",
                "notes": "; ".join(note_parts),
            }
        )

    index_schema = next((row for row in schema_rows if row["object_name"].startswith("CRSP_Daily_Market_and_SP500")), None)
    index_values: set[str] = set()
    index_path = package / "crsp/CRSP_Daily_Market_and_SP500_Indexes_2004-01-01_2025-12-31.csv.gz"
    if index_path.is_file():
        with gzip.open(index_path, "rt", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                value = (row.get("indno") or "").strip()
                if value:
                    index_values.add(value)
    expected_index_values = {"1000080", "1000500", "1000502"}
    ccm_linktype_values = sorted(
        gzip_distinct_values(
            package / "ccm/CCM_CRSP_Compustat_Link_LC_LU_LS_FullHistory_959_SP500_PERMNOs.csv.gz",
            "LINKTYPE",
        )
    )
    coverage_by_object = {row["source_object"]: row for row in coverage_rows}
    key_coverage = {
        "CRSP daily stock TAR": coverage_by_object.get(
            "CRSP_Daily_Stock_CIZ_2004-01-01_2025-12-31.tar::all_daily_stock_batches", {}
        ),
        "CRSP S&P 500 constituents": coverage_by_object.get(
            "CRSP_SP500_Constituents_Daily_CIZ_2006-01-01_2025-12-31.csv.gz", {}
        ),
        "CRSP market / S&P 500 indexes": coverage_by_object.get(
            "CRSP_Daily_Market_and_SP500_Indexes_2004-01-01_2025-12-31.csv.gz", {}
        ),
        "Historical PERMNO query input": coverage_by_object.get("sp500_historical_permnos_2006_2025.txt", {}),
    }
    historical_gics_schema = next((row for row in schema_rows if row["object_name"] == "Compustat_Historical_GICS_FullHistory.csv.gz"), {})
    historical_gics_fields = set(json.loads(historical_gics_schema.get("columns", "[]")))
    ff_schemas = [row for row in schema_rows if row["object_name"].startswith("Fama_French_")]
    ff_headers_recorded = len(ff_schemas) == 2 and all("date" in set(json.loads(row["columns"])) for row in ff_schemas)
    constituent_coverage = key_coverage["CRSP S&P 500 constituents"]
    tar_coverage = key_coverage["CRSP daily stock TAR"]
    date_parse_anomaly_rows = [
        row for row in schema_rows if int(row.get("invalid_nonblank_date_cell_count", 0) or 0) > 0
    ]
    date_parse_anomaly_cells = sum(
        int(row.get("invalid_nonblank_date_cell_count", 0) or 0)
        for row in date_parse_anomaly_rows
    )
    warnings: list[str] = []
    if missing_required:
        warnings.append("Missing required raw file(s): " + ", ".join(missing_required))
    if unexpected_files:
        warnings.append("Unexpected physical raw file(s): " + ", ".join(unexpected_files))
    if not raw_unchanged:
        warnings.append("Critical: pre/post raw snapshot mismatch")
    if not all_readable:
        warnings.append("At least one raw object failed readability or container-integrity inspection")
    if tar_batch_count != 10:
        warnings.append(f"Expected 10 TAR gzip batches; observed {tar_batch_count}")
    if query_row_count != 959 or query_unique_permno_count != 959:
        warnings.append(f"Expected 959 query-input PERMNO lines/unique values; observed {query_row_count}/{query_unique_permno_count}")
    if index_values != expected_index_values:
        warnings.append("Expected index INDNO set differs: observed=" + _serialize(sorted(index_values)))
    if date_parse_anomaly_cells:
        warnings.append(
            "Nonblank raw date cells outside the plain YYYY-MM-DD/YYYYMMDD parser: "
            f"{date_parse_anomaly_cells} cells across {len(date_parse_anomaly_rows)} object(s); "
            "no coercion or semantic interpretation was applied (deferred to a later Gate 0 phase)"
        )

    critical_fail = bool(missing_required or unexpected_files or not raw_unchanged or not all_readable or tar_batch_count != 10)
    verdict = "FAIL" if critical_fail else "PASS WITH WARNINGS" if warnings else "PASS"
    acceptance_rows = [
        _acceptance_row("required_files_present", "All expected required raw files are present", "CRITICAL", "PASS" if not missing_required else "FAIL", f"present={len(expected_required) - len(missing_required)}; expected={len(expected_required)}", "; ".join(missing_required)),
        _acceptance_row("unexpected_raw_files", "No unexpected physical raw files", "CRITICAL", "PASS" if not unexpected_files else "FAIL", f"unexpected_count={len(unexpected_files)}", "; ".join(unexpected_files)),
        _acceptance_row("optional_metadata", "Optional CRSP index metadata status", "INFO", "PASS", "present" if optional_present else "absent_allowed", "Known optional file; absence is not a failure"),
        _acceptance_row("sha256_pre_post_stability", "Every physical raw file has stable size and SHA256 pre/post audit", "CRITICAL", "PASS" if raw_unchanged else "FAIL", f"pre_count={len(pre_snapshot)}; post_count={len(post_snapshot)}"),
        _acceptance_row("raw_file_readability", "Every physical raw file is readable by its supported container inspector", "CRITICAL", "PASS" if all_readable else "FAIL", f"readable_count={sum(status == 'PASS' for status, _ in inspection_status.values())}; total={len(inspection_status)}"),
        _acceptance_row("tar_integrity", "Daily-stock TAR and all readable members pass inspection", "CRITICAL", "PASS" if inspection_status.get("crsp/CRSP_Daily_Stock_CIZ_2004-01-01_2025-12-31.tar", ("FAIL",))[0] == "PASS" else "FAIL", inspection_status.get("crsp/CRSP_Daily_Stock_CIZ_2004-01-01_2025-12-31.tar", ("FAIL", "missing"))[1]),
        _acceptance_row("tar_expected_batch_count", "Daily-stock TAR has ten gzip batch members", "CRITICAL", "PASS" if tar_batch_count == 10 else "FAIL", f"observed_batch_count={tar_batch_count}"),
        _acceptance_row("schema_inventory", "Schema inspection completed for CSV/text raw objects", "CRITICAL", "PASS" if all(row["schema_status"] == "PASS" for row in schema_rows) else "FAIL", f"schema_object_count={len(schema_rows)}"),
        _acceptance_row("raw_date_format_exceptions", "Raw date-format exceptions are recorded without coercion", "WARN", "WARN" if date_parse_anomaly_cells else "PASS", f"nonblank_unparsed_date_cells={date_parse_anomaly_cells}; object_count={len(date_parse_anomaly_rows)}", "No coercion or semantic interpretation applied; deferred to a later Gate 0 phase"),
        _acceptance_row("query_input_permno", "Query input has 959 unique numeric PERMNO values", "CRITICAL", "PASS" if query_row_count == query_unique_permno_count == 959 else "FAIL", f"row_count={query_row_count}; unique_permno_count={query_unique_permno_count}"),
        _acceptance_row("constituent_historical_permno_count", "Constituent raw file exposes 959 distinct PERMNO values", "CRITICAL", "PASS" if constituent_coverage.get("unique_primary_identifier_count") == 959 else "WARN", f"observed={constituent_coverage.get('unique_primary_identifier_count', '')}", "Raw coverage fact only; membership-policy validation is deferred"),
        _acceptance_row("daily_stock_raw_key", "Daily-stock TAR raw (PERMNO, DlyCalDt) duplicate check", "CRITICAL", "PASS" if tar_coverage.get("basic_duplicate_count_if_unambiguous") == 0 and tar_coverage.get("missing_key_count_if_unambiguous") == 0 else "FAIL", f"duplicates={tar_coverage.get('basic_duplicate_count_if_unambiguous', '')}; missing_keys={tar_coverage.get('missing_key_count_if_unambiguous', '')}"),
        _acceptance_row("index_indno_set", "CRSP index extraction contains expected INDNO values", "CRITICAL", "PASS" if index_values == expected_index_values else "WARN", _serialize(sorted(index_values)), "Raw field presence only; index semantics deferred"),
        _acceptance_row("ccm_linktype_raw_values", "CCM raw file contains expected LC/LU/LS link-type extraction", "CRITICAL", "PASS" if set(ccm_linktype_values) == {"LC", "LU", "LS"} else "WARN", _serialize(ccm_linktype_values), "Raw field values only; full CCM policy verification is deferred"),
        _acceptance_row("historical_gics_schema", "Historical-GICS raw schema records effective-date boundary fields", "CRITICAL", "PASS" if {"indfrom", "indthru"}.issubset(historical_gics_fields) else "FAIL", _serialize(sorted(historical_gics_fields & {"indfrom", "indthru"})), "Effective-date merge validation is deferred"),
        _acceptance_row("fama_french_schema", "Fama–French daily/monthly raw headers and date coverage are recorded", "CRITICAL", "PASS" if ff_headers_recorded else "FAIL", f"schema_rows={len(ff_schemas)}", "Return-unit and frequency semantics are deferred"),
        _acceptance_row("phase_boundary", "Phase-2/3 semantic matters remain unresolved", "INFO", "PASS", "DEFERRED_TO_GATE0_PHASE2; DEFERRED_TO_GATE0_PHASE3", "No semantic policy was frozen by this audit"),
    ]
    result = {
        "expected": {
            "present_required": path_classification["present_required"],
            "missing_required": missing_required,
            "unexpected_files": unexpected_files,
            "optional_present": optional_present,
        },
        "physical_file_count": len(pre_snapshot),
        "schema_rows": schema_rows,
        "coverage_rows": coverage_rows,
        "tar_member_rows": tar_member_rows,
        "tar_batch_count": tar_batch_count,
        "ccm_linktype_values": ccm_linktype_values,
        "key_coverage": key_coverage,
        "query_row_count": query_row_count,
        "query_unique_permno_count": query_unique_permno_count,
        "raw_unchanged": raw_unchanged,
        "all_readable": all_readable,
        "date_parse_anomaly_cells": date_parse_anomaly_cells,
        "warnings": warnings,
        "verdict": verdict,
    }
    _write_csv(
        output_directory / "raw_file_manifest.csv",
        ("relative_path", "source_family", "filename", "file_type", "size_bytes", "sha256", "readable", "compression_integrity", "expected_status", "notes"),
        physical_manifest_rows,
    )
    _write_csv(
        output_directory / "raw_schema_inventory.csv",
        ("relative_path", "object_name", "row_count", "column_count", "columns", "identifier_fields", "date_fields", "observed_min_date", "observed_max_date", "date_ranges", "schema_status", "notes"),
        sorted(schema_rows, key=lambda row: (str(row["relative_path"]), str(row["object_name"]))),
    )
    _write_csv(
        output_directory / "crsp_daily_stock_tar_members.csv",
        ("member_name", "member_size", "row_count", "column_count", "columns", "min_date", "max_date", "permno_count", "readable", "notes"),
        sorted(tar_member_rows, key=lambda row: str(row["member_name"])),
    )
    _write_csv(
        output_directory / "raw_basic_coverage.csv",
        ("source_object", "row_count", "unique_primary_identifier_count", "min_date", "max_date", "basic_duplicate_count_if_unambiguous", "missing_key_count_if_unambiguous", "notes"),
        sorted(coverage_rows, key=lambda row: str(row["source_object"])),
    )
    _write_csv(
        output_directory / "gate0_phase1_acceptance.csv",
        ("check_id", "check_name", "severity", "status", "evidence", "notes"),
        acceptance_rows,
    )
    _write_report(output_directory / "WRDS_GATE0_PHASE1_RAW_INTEGRITY.md", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=project_root(), help="Repository root (default: inferred from module path)")
    args = parser.parse_args(argv)
    result = run_phase1(args.root.resolve())
    print(f"Gate 0 Phase 1 verdict: {result['verdict']}")
    print(f"Physical raw files: {result['physical_file_count']}")
    return 0 if result["verdict"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
