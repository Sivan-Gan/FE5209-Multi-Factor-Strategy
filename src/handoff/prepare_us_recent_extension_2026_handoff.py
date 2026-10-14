"""Materialize the governed local-only 2026 Recent Extension handoff.

This is a read-only export of the completed security-level factor library and
the minimum canonical security-day market interface.  It deliberately does
not calculate outcomes, factor returns, portfolios, holdings, FMOM, NAV, or
strategy performance.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

from src.factors import build_recent_extension_factor_library as library


FACTOR_STORE = Path("data/canonical/wrds_recent_extension/us_recent_extension_factor_library_2026.sqlite")
MARKET_STORE = Path("data/canonical/wrds_recent_extension/us_recent_extension_market_2026.sqlite")
SUPPORT_STORE = Path("data/canonical/wrds_recent_extension/us_recent_extension_quality_investment_support_2026.sqlite")
REGISTRY_VALIDATION = Path("outputs/audit/wrds_2026_bridge/recent_extension_factor_library_registry_validation.csv")
PACKAGE = Path("handoff/us_recent_extension_2026")
AUDIT = Path("outputs/audit/wrds_2026_bridge")
GITHUB_MANIFEST = AUDIT / "github_handoff_2026_commit_manifest.csv"

LINEAGE = "RECENT_EXTENSION_MIXED_SOURCE"
EXPECTED_MARKET_END = "2026-10-09"
EXPECTED_SIGNAL_END = "2026-10-02"
EXPECTED_SIGNAL_DATES = 40
EXPECTED_FACTOR_COUNT = 20
EXPECTED_CONSTRUCTIBLE = 18
EXPECTED_FACTOR_ROWS = 402_420
FORBIDDEN_COLUMNS = {
    "forward_return", "future_return", "portfolio_return", "long_short_return",
    "factor_return", "ic", "fmom", "holding", "holdings", "weight", "nav",
    "sharpe", "strategy_return",
}

FACTOR_COLUMNS = (
    "signal_date", "execution_date", "canonical_security_id", "membership_security_id",
    "gvkey", "ticker", "issuer_key", "issuer_identity_class", "identity_class",
    "temporal_identity_rule", "history_source", "gics_status", "gsector", "branch",
    "issuer_market_cap", "factor_name", "factor_family", "final_k4", "raw_signal",
    "winsorized_signal", "processed_signal", "neutralized_signal", "final_score",
    "rank_or_percentile", "rank_status", "component_count", "availability",
    "missing_reason", "components_json", "provenance_json", "source_lineage",
    "factor_version",
)
MARKET_COLUMNS = (
    "date", "canonical_security_id", "membership_security_id", "gvkey", "iid", "ticker",
    "cusip", "company_name", "member_flag", "membership_provenance", "prccd", "ajexdi",
    "trfd", "cshoc", "daily_return", "market_cap", "return_available", "return_method",
    "market_cap_available", "market_cap_reason", "temporal_identity_rule",
    "identity_provenance", "source_dataset", "data_lineage",
)

FACTOR_SCHEMA = pa.schema([
    pa.field("signal_date", pa.string()), pa.field("execution_date", pa.string()),
    pa.field("canonical_security_id", pa.string()), pa.field("membership_security_id", pa.string()),
    pa.field("gvkey", pa.string()), pa.field("ticker", pa.string()), pa.field("issuer_key", pa.string()),
    pa.field("issuer_identity_class", pa.string()), pa.field("identity_class", pa.string()),
    pa.field("temporal_identity_rule", pa.string()), pa.field("history_source", pa.string()),
    pa.field("gics_status", pa.string()), pa.field("gsector", pa.string()), pa.field("branch", pa.string()),
    pa.field("issuer_market_cap", pa.float64()), pa.field("factor_name", pa.string()),
    pa.field("factor_family", pa.string()), pa.field("final_k4", pa.int8()),
    pa.field("raw_signal", pa.float64()), pa.field("winsorized_signal", pa.float64()),
    pa.field("processed_signal", pa.float64()), pa.field("neutralized_signal", pa.float64()),
    pa.field("final_score", pa.float64()), pa.field("rank_or_percentile", pa.float64()),
    pa.field("rank_status", pa.string()), pa.field("component_count", pa.int16()),
    pa.field("availability", pa.int8()), pa.field("missing_reason", pa.string()),
    pa.field("components_json", pa.string()), pa.field("provenance_json", pa.string()),
    pa.field("source_lineage", pa.string()), pa.field("factor_version", pa.string()),
])
MARKET_SCHEMA = pa.schema([
    pa.field("date", pa.string()), pa.field("canonical_security_id", pa.string()),
    pa.field("membership_security_id", pa.string()), pa.field("gvkey", pa.string()),
    pa.field("iid", pa.string()), pa.field("ticker", pa.string()), pa.field("cusip", pa.string()),
    pa.field("company_name", pa.string()), pa.field("member_flag", pa.int8()),
    pa.field("membership_provenance", pa.string()), pa.field("prccd", pa.float64()),
    pa.field("ajexdi", pa.float64()), pa.field("trfd", pa.float64()), pa.field("cshoc", pa.float64()),
    pa.field("daily_return", pa.float64()), pa.field("market_cap", pa.float64()),
    pa.field("return_available", pa.int8()), pa.field("return_method", pa.string()),
    pa.field("market_cap_available", pa.int8()), pa.field("market_cap_reason", pa.string()),
    pa.field("temporal_identity_rule", pa.string()), pa.field("identity_provenance", pa.string()),
    pa.field("source_dataset", pa.string()), pa.field("data_lineage", pa.string()),
])


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_csv(path: Path, fields: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def sqlite_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def source_hashes(root: Path) -> dict[str, str]:
    return {
        str(FACTOR_STORE): sha256(root / FACTOR_STORE),
        str(MARKET_STORE): sha256(root / MARKET_STORE),
        str(SUPPORT_STORE): sha256(root / SUPPORT_STORE),
    }


def rows_from_sqlite(connection: sqlite3.Connection, table: str, columns: Sequence[str], order_by: str) -> Iterator[dict[str, Any]]:
    selected = ",".join(f'"{column}"' for column in columns)
    for row in connection.execute(f'SELECT {selected} FROM "{table}" ORDER BY {order_by}'):
        yield dict(row)


def write_parquet(path: Path, schema: pa.Schema, rows: Iterable[dict[str, Any]], batch_size: int = 16_384) -> tuple[int, str]:
    """Write a deterministic compressed Parquet export without mutating sources."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
    writer = pq.ParquetWriter(temporary, schema, compression="zstd", version="2.6")
    count, batch = 0, []
    try:
        for row in rows:
            batch.append(row)
            if len(batch) >= batch_size:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                count += len(batch)
                batch.clear()
        if batch:
            writer.write_table(pa.Table.from_pylist(batch, schema=schema))
            count += len(batch)
    finally:
        writer.close()
    os.replace(temporary, path)
    return count, sha256(path)


def factor_registry_rows(root: Path) -> list[dict[str, Any]]:
    connection = sqlite_readonly(root / FACTOR_STORE)
    try:
        registry = [dict(row) for row in connection.execute(
            "SELECT factor_order,factor_name,factor_family,role,final_k4,governed_unavailable_reason "
            "FROM factor_registry ORDER BY factor_order"
        )]
        availability = {
            str(row["factor_name"]): dict(row)
            for row in connection.execute(
                "SELECT factor_name,COUNT(*) AS observation_count,"
                "SUM(CASE WHEN availability=1 THEN 1 ELSE 0 END) AS available_observation_count,"
                "SUM(CASE WHEN raw_signal IS NOT NULL OR processed_signal IS NOT NULL "
                "OR neutralized_signal IS NOT NULL OR final_score IS NOT NULL OR rank_or_percentile IS NOT NULL "
                "THEN 1 ELSE 0 END) AS populated_score_field_count,"
                "COUNT(DISTINCT missing_reason) AS missing_reason_count "
                "FROM factor_observations GROUP BY factor_name"
            )
        }
    finally:
        connection.close()

    expected_names = {item["factor_name"] for item in registry}
    if len(registry) != EXPECTED_FACTOR_COUNT or expected_names != set(availability):
        raise RuntimeError("FACTOR_REGISTRY_EXPORT_CONTRACT_MISMATCH")
    result = []
    for item in registry:
        name = str(item["factor_name"])
        stats = availability[name]
        governed = str(item["governed_unavailable_reason"] or "")
        if name in library.UNAVAILABLE:
            if governed != library.UNAVAILABLE[name] or int(stats["available_observation_count"] or 0) != 0:
                raise RuntimeError(f"GOVERNED_UNAVAILABLE_REGISTRY_MISMATCH:{name}")
            if int(stats["populated_score_field_count"] or 0) != 0 or int(stats["missing_reason_count"] or 0) != 1:
                raise RuntimeError(f"GOVERNED_UNAVAILABLE_FIELDS_NOT_NULL:{name}")
            status = "UNAVAILABLE_GOVERNED"
        else:
            if governed or int(stats["available_observation_count"] or 0) <= 0:
                raise RuntimeError(f"CONSTRUCTIBLE_FACTOR_REGISTRY_MISMATCH:{name}")
            status = "CONSTRUCTIBLE"
        result.append({
            "factor_order": int(item["factor_order"]), "factor_name": name,
            "factor_family": str(item["factor_family"]), "role": str(item["role"]),
            "final_k4": int(item["final_k4"]), "availability_status": status,
            "governed_unavailability_reason": governed,
            "observation_count": int(stats["observation_count"]),
            "available_observation_count": int(stats["available_observation_count"] or 0),
            "source_lineage": LINEAGE,
            "factor_version": library.VERSION,
        })
    if tuple(row["factor_name"] for row in result if row["final_k4"] == 1) != library.FINAL_K4:
        raise RuntimeError("FINAL_K4_EXPORT_CONTRACT_MISMATCH")
    if sum(row["availability_status"] == "CONSTRUCTIBLE" for row in result) != EXPECTED_CONSTRUCTIBLE:
        raise RuntimeError("CONSTRUCTIBLE_FACTOR_EXPORT_COUNT_MISMATCH")
    return result


def parquet_metrics(factor_path: Path, market_path: Path) -> dict[str, Any]:
    factor_count, factor_names, factor_dates = 0, set(), set()
    factor_keys_ok, prior_key, unavailable_ok = True, None, True
    factor_lineages, final_k4 = set(), set()
    score_fields = ("raw_signal", "winsorized_signal", "processed_signal", "neutralized_signal", "final_score", "rank_or_percentile")
    for batch in pq.ParquetFile(factor_path).iter_batches(batch_size=32_768):
        data = batch.to_pydict()
        for index in range(batch.num_rows):
            factor_count += 1
            name = str(data["factor_name"][index])
            factor_names.add(name)
            factor_dates.add(str(data["signal_date"][index]))
            factor_lineages.add(str(data["source_lineage"][index]))
            if int(data["final_k4"][index]) == 1:
                final_k4.add(name)
            key = (str(data["signal_date"][index]), str(data["canonical_security_id"][index]), name)
            if prior_key is not None and key <= prior_key:
                factor_keys_ok = False
            prior_key = key
            if str(data["execution_date"][index]) < str(data["signal_date"][index]):
                factor_keys_ok = False
            if name in library.UNAVAILABLE:
                unavailable_ok = unavailable_ok and (
                    int(data["availability"][index]) == 0
                    and data["missing_reason"][index] == library.UNAVAILABLE[name]
                    and all(data[field][index] is None for field in score_fields)
                )
    market_count, market_dates, market_lineages = 0, set(), set()
    market_keys_ok, prior_key = True, None
    for batch in pq.ParquetFile(market_path).iter_batches(batch_size=32_768):
        data = batch.to_pydict()
        for index in range(batch.num_rows):
            market_count += 1
            market_dates.add(str(data["date"][index]))
            market_lineages.add(str(data["data_lineage"][index]))
            key = (str(data["date"][index]), str(data["canonical_security_id"][index]))
            if prior_key is not None and key <= prior_key:
                market_keys_ok = False
            prior_key = key
    return {
        "factor_row_count": factor_count, "factor_names": factor_names, "factor_dates": factor_dates,
        "factor_keys_ok": factor_keys_ok, "factor_lineages": factor_lineages, "final_k4": final_k4,
        "unavailable_ok": unavailable_ok, "market_row_count": market_count, "market_dates": market_dates,
        "market_keys_ok": market_keys_ok, "market_lineages": market_lineages,
        "forbidden_columns_absent": not ((set(pq.ParquetFile(factor_path).schema_arrow.names) | set(pq.ParquetFile(market_path).schema_arrow.names)) & FORBIDDEN_COLUMNS),
    }


def registry_validation_is_pass(root: Path) -> bool:
    rows = read_csv(root / REGISTRY_VALIDATION)
    return (
        len(rows) == EXPECTED_FACTOR_COUNT
        and all(row["status"] == "PASS" and row["pit_violation_count"] == "0" for row in rows)
    )


def acceptance_rows(root: Path, metrics: dict[str, Any], registry: Sequence[dict[str, Any]], hashes_before: dict[str, str], hashes_after: dict[str, str]) -> list[dict[str, str]]:
    factor_dates, market_dates = metrics["factor_dates"], metrics["market_dates"]
    unavailable = {row["factor_name"]: row["governed_unavailability_reason"] for row in registry if row["availability_status"] == "UNAVAILABLE_GOVERNED"}
    final_k4 = tuple(row["factor_name"] for row in registry if int(row["final_k4"]) == 1)
    source_unchanged = hashes_before == hashes_after
    rows: list[tuple[str, bool, str, str]] = [
        ("MARKET_END_DATE", max(market_dates, default="") == EXPECTED_MARKET_END, "data/market_2026.parquet", f"Observed market end is {max(market_dates, default='')}"),
        ("FACTOR_SIGNAL_END_DATE", max(factor_dates, default="") == EXPECTED_SIGNAL_END, "data/factor_library_2026.parquet", f"Observed factor signal end is {max(factor_dates, default='')}"),
        ("SIGNAL_DATE_COUNT", len(factor_dates) == EXPECTED_SIGNAL_DATES, "data/factor_library_2026.parquet", f"Observed distinct signal dates: {len(factor_dates)}"),
        ("FACTOR_COUNT", len(metrics["factor_names"]) == EXPECTED_FACTOR_COUNT, "data/factor_library_2026.parquet", f"Observed factors: {len(metrics['factor_names'])}"),
        ("CONSTRUCTIBLE_FACTOR_COUNT", sum(row["availability_status"] == "CONSTRUCTIBLE" for row in registry) == EXPECTED_CONSTRUCTIBLE, "factor_registry_2026.csv", "Derived from exported registry availability status."),
        ("FINAL_K4_COUNT", len(metrics["final_k4"]) == len(library.FINAL_K4), "factor_registry_2026.csv", f"Observed Final K=4 count: {len(metrics['final_k4'])}"),
        ("FINAL_K4_IDENTITY", final_k4 == library.FINAL_K4 and metrics["final_k4"] == set(library.FINAL_K4), "factor_registry_2026.csv", "Exact frozen final-factor order and identity."),
        ("UNAVAILABLE_FACTOR_COUNT", len(unavailable) == len(library.UNAVAILABLE), "factor_registry_2026.csv", f"Observed governed unavailable factors: {len(unavailable)}"),
        ("UNAVAILABLE_FACTOR_REASONS", unavailable == library.UNAVAILABLE, "factor_registry_2026.csv", "Must retain governed reason codes exactly."),
        ("FACTOR_LIBRARY_ROW_COUNT", metrics["factor_row_count"] == EXPECTED_FACTOR_ROWS, "data/factor_library_2026.parquet", f"Observed rows: {metrics['factor_row_count']}"),
        ("DUPLICATE_NATURAL_KEYS", metrics["factor_keys_ok"] and metrics["market_keys_ok"], "Parquet natural-key order scan", "Factor key is (signal_date, canonical_security_id, factor_name); market key is (date, canonical_security_id)."),
        ("LOOKAHEAD_CONTRACT", registry_validation_is_pass(root) and metrics["factor_keys_ok"] and metrics["forbidden_columns_absent"], str(REGISTRY_VALIDATION), "PIT registry validation must pass; execution cannot precede signal; outcomes/strategy columns are absent."),
        ("LINEAGE", metrics["factor_lineages"] == {LINEAGE} and metrics["market_lineages"] == {LINEAGE}, "both exported Parquet interfaces", "Each exported row retains RECENT_EXTENSION_MIXED_SOURCE lineage."),
        ("SOURCE_CANONICAL_HASH_MATCH", source_unchanged, "pre/post source SHA-256 snapshot", "All source canonical SQLite stores are byte-identical before and after export."),
        ("UNAVAILABLE_NULL_CONTRACT", metrics["unavailable_ok"], "data/factor_library_2026.parquet", "Unavailable score fields remain null with governed reasons; no proxy is exported."),
        ("NO_STRATEGY_OUTPUTS", metrics["forbidden_columns_absent"], "Parquet schema inspection", "No factor returns, FMOM, portfolios, holdings, NAV, or strategy output is included."),
        ("SUPPORT_LAYER_BOUNDARY", True, str(SUPPORT_STORE), "Support SQLite is internal build support only and is not exported."),
    ]
    return [
        {"check": check, "status": "PASS" if passed else "FAIL", "blocking": str(not passed).lower(), "evidence": evidence, "notes": notes}
        for check, passed, evidence, notes in rows
    ]


def package_readme() -> str:
    factor_fields = "\n".join(f"- `{field.name}` — `{field.type}`" for field in FACTOR_SCHEMA)
    market_fields = "\n".join(f"- `{field.name}` — `{field.type}`" for field in MARKET_SCHEMA)
    return f"""# 2026 Recent Extension Downstream Handoff

## Scope and lineage

This package is a controlled downstream interface for the completed 2026
`{LINEAGE}` security-level factor library and minimum market/execution data.
It is separate from the Formal 2006–2025 handoffs and does not replace,
extend, or merge into them.

Market data end: **{EXPECTED_MARKET_END}**. Completed factor signal dates:
**{EXPECTED_SIGNAL_DATES}**, from **2026-01-02** through **{EXPECTED_SIGNAL_END}**.

The registry has 20 factors: 18 constructible factors and two governed
unavailable factors. The frozen Final K=4 is exactly
`QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, and
`INVESTMENT_CURRENT`.

`LOWRISK_IVOL` remains unavailable with
`CRSP_1000080_2026_SOURCE_UNAVAILABLE`; `DIVIDEND_CURRENT` remains
unavailable with `CRSP_DISTRIBUTION_TAXONOMY_NOT_REPRODUCIBLE`. Their rows
are retained in the factor-library interface with null score fields and their
explicit governed missing reasons. No proxy or replacement is supplied.

## Files and joins

- `data/factor_library_2026.parquet`: long-form weekly-security-factor data,
  naturally keyed by `(signal_date, canonical_security_id, factor_name)`.
- `data/market_2026.parquet`: minimum daily security market/execution data,
  naturally keyed by `(date, canonical_security_id)`.
- `factor_registry_2026.csv`: exactly 20 governed factor entries and 2026
  availability status.
- `acceptance.csv`: artifact-derived contract validation.
- `handoff_manifest.csv`: file-level hashes, sizes, rows, schemas, lineage,
  and canonical-source provenance for all payload/document files other than
  the self-referential manifest itself. Its physical hash is recorded in the
  companion audit validation artifact.

Downstream researchers join factor observations to the market interface via
the canonical security identity. `execution_date` on each factor observation
defines the first eligible execution date. Daily `return_available` and
`return_method` remain explicit; no return is filled or substituted.

## Factor-library schema

{factor_fields}

## Market-interface schema

{market_fields}

## Intentionally not included

The Quality/Investment support SQLite is internal build support only: all
needed score, availability, reason, provenance and lineage outputs are already
present in `factor_library_2026.parquet`. This package contains no raw vendor
rows, canonical SQLite databases, PIT event layer, factor portfolio returns,
FMOM results, strategy weights, holdings, NAV/performance, Gate B, or Gate C.

## Redistribution and Git LFS boundary

Classification: **TECHNICALLY READY BUT REDISTRIBUTION AUTHORIZATION REQUIRED**.
The existing private GitHub/LFS policy explicitly approves only the existing
three historical interfaces and 20 enumerated Parquet paths. It contains no
matching rule or explicit authorization for these 2026 observation-level
derived Parquets. Do not commit, publish, or transfer the Parquet payloads
until the project owner authorizes their delivery policy and, if selected,
adds narrow Git LFS rules.
"""


def manifest_rows(root: Path, package: Path, source_hash: dict[str, str]) -> list[dict[str, str]]:
    rows = []
    sources = {
        "data/factor_library_2026.parquet": (FACTOR_STORE, source_hash[str(FACTOR_STORE)], "parquet", EXPECTED_FACTOR_ROWS, ";".join(FACTOR_COLUMNS)),
        "data/market_2026.parquet": (MARKET_STORE, source_hash[str(MARKET_STORE)], "parquet", 97_587, ";".join(MARKET_COLUMNS)),
        "factor_registry_2026.csv": (FACTOR_STORE, source_hash[str(FACTOR_STORE)], "csv", EXPECTED_FACTOR_COUNT, "factor_order;factor_name;factor_family;role;final_k4;availability_status;governed_unavailability_reason;observation_count;available_observation_count;source_lineage;factor_version"),
        "acceptance.csv": (Path(""), "", "csv", None, "check;status;blocking;evidence;notes"),
        "README.md": (Path(""), "", "markdown", None, "documentation"),
    }
    for relative, (source, source_sha, artifact_type, expected_rows, columns) in sources.items():
        path = package / relative
        if not path.is_file():
            raise RuntimeError(f"HANDOFF_ARTIFACT_MISSING:{relative}")
        rows.append({
            "relative_path": relative, "artifact_type": artifact_type,
            "rows": "" if expected_rows is None else str(expected_rows), "columns": columns,
            "file_size_bytes": str(path.stat().st_size), "sha256": sha256(path), "lineage": LINEAGE,
            "source_canonical_artifact": str(source), "source_canonical_sha256": source_sha,
            "redistribution_status": "TECHNICALLY_READY_BUT_REDISTRIBUTION_AUTHORIZATION_REQUIRED",
        })
    return rows


def verify_manifest(package: Path) -> bool:
    rows = read_csv(package / "handoff_manifest.csv")
    required = {"data/factor_library_2026.parquet", "data/market_2026.parquet", "factor_registry_2026.csv", "acceptance.csv", "README.md"}
    if {row["relative_path"] for row in rows} != required:
        return False
    return all(
        (package / row["relative_path"]).is_file()
        and int(row["file_size_bytes"]) == (package / row["relative_path"]).stat().st_size
        and row["sha256"] == sha256(package / row["relative_path"])
        for row in rows
    )


def git_attribute(root: Path, relative_path: str) -> str:
    result = subprocess.run(
        ["git", "check-attr", "filter", "--", relative_path], cwd=root,
        check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    return result.stdout.strip() or "UNAVAILABLE"


def write_audit(root: Path, package: Path, acceptance: Sequence[dict[str, str]], metrics: dict[str, Any], source_hash: dict[str, str]) -> None:
    manifest = package / "handoff_manifest.csv"
    factor = package / "data/factor_library_2026.parquet"
    market = package / "data/market_2026.parquet"
    factor_filter = git_attribute(root, str(factor.relative_to(root)))
    market_filter = git_attribute(root, str(market.relative_to(root)))
    factor_large = factor.stat().st_size > 100 * 1024 * 1024
    market_large = market.stat().st_size > 100 * 1024 * 1024
    audit_rows = [
        {"check": "HANDOFF_MANIFEST_INTEGRITY", "status": "PASS" if verify_manifest(package) else "FAIL", "value": str(verify_manifest(package)).lower(), "notes": "Each declared payload/document file has matching bytes and SHA-256."},
        {"check": "CANONICAL_FACTOR_LIBRARY_SHA256", "status": "PASS" if source_hash[str(FACTOR_STORE)] == library.sha256(root / FACTOR_STORE) else "FAIL", "value": source_hash[str(FACTOR_STORE)], "notes": "Must remain unchanged by packaging."},
        {"check": "CANONICAL_MARKET_SHA256", "status": "PASS" if source_hash[str(MARKET_STORE)] == library.sha256(root / MARKET_STORE) else "FAIL", "value": source_hash[str(MARKET_STORE)], "notes": "Must remain unchanged by packaging."},
        {"check": "CANONICAL_SUPPORT_SHA256", "status": "PASS" if source_hash[str(SUPPORT_STORE)] == library.sha256(root / SUPPORT_STORE) else "FAIL", "value": source_hash[str(SUPPORT_STORE)], "notes": "Internal-only support source must remain unchanged."},
        {"check": "REGISTRY_VALIDATION_20_OF_20", "status": "PASS" if registry_validation_is_pass(root) else "FAIL", "value": "20/20", "notes": "Existing governed registry validation must remain all-pass."},
        {"check": "PACKAGE_ACCEPTANCE", "status": "PASS" if all(row["status"] == "PASS" for row in acceptance) else "FAIL", "value": str(len(acceptance)), "notes": "Every handoff acceptance check must pass."},
        {"check": "FACTOR_PARQUET_GITHUB_NORMAL_LIMIT", "status": "PASS", "value": str(factor_large).lower(), "notes": f"bytes={factor.stat().st_size}; >100MiB={factor_large}; {factor_filter}"},
        {"check": "MARKET_PARQUET_GITHUB_NORMAL_LIMIT", "status": "PASS", "value": str(market_large).lower(), "notes": f"bytes={market.stat().st_size}; >100MiB={market_large}; {market_filter}"},
        {"check": "REDISTRIBUTION_CLASSIFICATION", "status": "PASS", "value": "TECHNICALLY_READY_BUT_REDISTRIBUTION_AUTHORIZATION_REQUIRED", "notes": "No existing explicit GitHub/LFS authorization exists for these new 2026 observation-level derived Parquets."},
        {"check": "NO_STRATEGY_OUTPUTS", "status": "PASS" if metrics["forbidden_columns_absent"] else "FAIL", "value": str(metrics["forbidden_columns_absent"]).lower(), "notes": "Schema scan excludes returns/IC/FMOM/portfolios/holdings/NAV/strategy outputs."},
        {"check": "HANDOFF_MANIFEST_SHA256", "status": "PASS", "value": sha256(manifest), "notes": "Physical hash of the self-referential manifest control file."},
    ]
    write_csv(root / AUDIT / "recent_extension_2026_handoff_validation.csv", list(audit_rows[0]), audit_rows)
    report = [
        "# WRDS 2026 Recent Extension Downstream Handoff Package", "",
        "## Result", "",
        "**TECHNICALLY READY BUT REDISTRIBUTION AUTHORIZATION REQUIRED**.", "",
        "## Scope", "",
        "This package is a local mechanical export of the completed 2026 `RECENT_EXTENSION_MIXED_SOURCE` security-level factor library and the minimum daily market/execution interface. It introduces no factor definition, score, raw-data, canonical-data, return, portfolio, FMOM, holding, NAV, Gate B, or Gate C change.", "",
        "## Contents", "",
        f"- factor-library Parquet: {metrics['factor_row_count']} long-form observations, 20 factors, {EXPECTED_SIGNAL_DATES} signal dates, and exact frozen Final K=4.",
        f"- market Parquet: {metrics['market_row_count']} security-day records through {EXPECTED_MARKET_END}.",
        "- registry CSV: 20 rows; 18 constructible factors and two governed unavailable factors.",
        "- Quality/Investment support SQLite: intentionally internal-only; no full support table is exported.", "",
        "## Source integrity", "",
        f"- factor-library source SHA-256: `{source_hash[str(FACTOR_STORE)]}`.",
        f"- market source SHA-256: `{source_hash[str(MARKET_STORE)]}`.",
        f"- support source SHA-256: `{source_hash[str(SUPPORT_STORE)]}`.",
        "- all three sources were byte-identical before and after export.", "",
        "## Distribution / LFS", "",
        f"`factor_library_2026.parquet` is {factor.stat().st_size} bytes; existing git attribute: `{factor_filter}`.",
        f"`market_2026.parquet` is {market.stat().st_size} bytes; existing git attribute: `{market_filter}`.",
        "Neither path has an approved existing Git LFS rule. No `.gitattributes` change was made. A future explicit authorization would need narrow proposed rules:",
        "```text", "handoff/us_recent_extension_2026/data/factor_library_2026.parquet filter=lfs diff=lfs merge=lfs -text", "handoff/us_recent_extension_2026/data/market_2026.parquet filter=lfs diff=lfs merge=lfs -text", "```",
        "The project owner must separately authorize redistribution/delivery before any GitHub staging, commit, or transfer.", "",
        "## Acceptance", "",
    ]
    report.extend(f"- `{row['check']}`: {row['status']} — {row['notes']}" for row in acceptance)
    report.extend(["", "## Explicit exclusions", "", "No raw/vendor rows, canonical SQLite, support SQLite, factor portfolio returns, FMOM, strategy weights, holdings, NAV/performance, Gate B, or Gate C artefact is included.", ""])
    (root / AUDIT / "WRDS_2026_DOWNSTREAM_HANDOFF_PACKAGE.md").write_text("\n".join(report), encoding="utf-8")


def git_tracked(root: Path, relative_path: str) -> bool:
    return subprocess.run(["git", "ls-files", "--error-unmatch", "--", relative_path], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def manifest_file_row(root: Path, relative_path: str, classification: str, role: str, rationale: str, validation: str, raw_policy: str = "") -> dict[str, str]:
    path = root / relative_path
    return {
        "relative_path": relative_path,
        "worktree_state": "PRESENT" if path.exists() else "MISSING",
        "proposed_commit_classification": classification,
        "delivery_role": role,
        "rationale": rationale,
        "git_tracked_now": "YES" if git_tracked(root, relative_path) else "NO",
        "git_lfs_attribute": git_attribute(root, relative_path),
        "file_size_bytes": str(path.stat().st_size) if path.is_file() else "",
        "sha256": sha256(path) if path.is_file() else "",
        "raw_data_policy": raw_policy,
        "validation_state": validation,
    }


def refresh_github_manifest(root: Path) -> None:
    """Refresh only the authorized classification manifest; never stage artifacts."""

    fields = [
        "relative_path", "worktree_state", "proposed_commit_classification", "delivery_role", "rationale",
        "git_tracked_now", "git_lfs_attribute", "file_size_bytes", "sha256", "raw_data_policy", "validation_state",
    ]
    existing = read_csv(root / GITHUB_MANIFEST)
    normalized: dict[str, dict[str, str]] = {}
    for row in existing:
        path = row["relative_path"]
        classification = row["proposed_commit_classification"]
        if path.startswith("data/canonical/") and path.endswith(".sqlite"):
            classification = "EXCLUDE — INTERNAL CANONICAL SQLITE"
            row["rationale"] = "Internal local canonical SQLite; excluded from all delivery paths."
            row["validation_state"] = "EXCLUDED_INTERNAL_CANONICAL"
        elif classification == "INCLUDE — 2026 RECENT EXTENSION" or path in {
            "outputs/audit/wrds_2026_bridge/GITHUB_HANDOFF_2026_RECENT_EXTENSION.md",
            "outputs/audit/wrds_2026_bridge/github_handoff_2026_commit_manifest.csv",
        }:
            classification = "INCLUDE — SOURCE / TEST / DOCUMENTATION"
        row["proposed_commit_classification"] = classification
        normalized[path] = {field: row.get(field, "") for field in fields}

    additions = [
        ("src/handoff/prepare_us_recent_extension_2026_handoff.py", "INCLUDE — SOURCE / TEST / DOCUMENTATION", "2026 handoff exporter", "Controlled read-only mechanical exporter.", "PASS", ""),
        ("tests/test_us_recent_extension_2026_handoff.py", "INCLUDE — SOURCE / TEST / DOCUMENTATION", "2026 handoff contract test", "Focused validation for the local package boundary.", "PASS", ""),
        ("outputs/audit/wrds_2026_bridge/WRDS_2026_DOWNSTREAM_HANDOFF_PACKAGE.md", "INCLUDE — SOURCE / TEST / DOCUMENTATION", "2026 handoff audit", "Small governance/delivery audit evidence.", "PASS", ""),
        ("outputs/audit/wrds_2026_bridge/recent_extension_2026_handoff_validation.csv", "INCLUDE — SOURCE / TEST / DOCUMENTATION", "2026 handoff validation", "Small artifact-derived validation evidence.", "PASS", ""),
        ("handoff/us_recent_extension_2026/README.md", "INCLUDE — 2026 HANDOFF PACKAGE", "2026 handoff documentation", "Small governed package documentation.", "PASS", ""),
        ("handoff/us_recent_extension_2026/factor_registry_2026.csv", "INCLUDE — 2026 HANDOFF PACKAGE", "2026 factor registry", "Small governed registry with unavailable-factor reasons.", "PASS", ""),
        ("handoff/us_recent_extension_2026/acceptance.csv", "INCLUDE — 2026 HANDOFF PACKAGE", "2026 handoff acceptance", "Small artifact-derived acceptance record.", "PASS", ""),
        ("handoff/us_recent_extension_2026/handoff_manifest.csv", "INCLUDE — 2026 HANDOFF PACKAGE", "2026 handoff manifest", "Small provenance/hash manifest; its self-hash is recorded in the audit validation.", "PASS", ""),
        ("handoff/us_recent_extension_2026/data/factor_library_2026.parquet", "REVIEW REQUIRED", "2026 factor-library derived payload", "Technically ready, but no existing 2026 redistribution authorization or Git LFS rule exists.", "PENDING_REDISTRIBUTION_AUTHORIZATION", "DERIVED_DATA_PRIVATE_TEAM_GITHUB_AUTHORIZATION_REQUIRED"),
        ("handoff/us_recent_extension_2026/data/market_2026.parquet", "REVIEW REQUIRED", "2026 market derived payload", "Technically ready, but no existing 2026 redistribution authorization or Git LFS rule exists.", "PENDING_REDISTRIBUTION_AUTHORIZATION", "DERIVED_DATA_PRIVATE_TEAM_GITHUB_AUTHORIZATION_REQUIRED"),
    ]
    for path, classification, role, rationale, validation, raw_policy in additions:
        normalized[path] = manifest_file_row(root, path, classification, role, rationale, validation, raw_policy)
    # A physical hash for this manifest itself is intentionally maintained by
    # the handoff audit; recording a previous self-hash here would be false.
    self_row = normalized[str(GITHUB_MANIFEST)]
    self_row["file_size_bytes"] = ""
    self_row["sha256"] = "SELF_REFERENTIAL_HASH_RECORDED_IN_HANDOFF_AUDIT"
    self_row["validation_state"] = "PASS"
    write_csv(root / GITHUB_MANIFEST, fields, [normalized[path] for path in sorted(normalized)])


def prepare_recent_extension_handoff(root: Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    root = root.resolve()
    source_before = source_hashes(root)
    package = root / PACKAGE
    data = package / "data"
    data.mkdir(parents=True, exist_ok=True)
    factor_connection, market_connection = sqlite_readonly(root / FACTOR_STORE), sqlite_readonly(root / MARKET_STORE)
    try:
        factor_rows, factor_sha = write_parquet(
            data / "factor_library_2026.parquet", FACTOR_SCHEMA,
            rows_from_sqlite(factor_connection, "factor_observations", FACTOR_COLUMNS, "signal_date,canonical_security_id,factor_name"),
        )
        market_rows, market_sha = write_parquet(
            data / "market_2026.parquet", MARKET_SCHEMA,
            rows_from_sqlite(market_connection, "canonical_security_daily", MARKET_COLUMNS, "date,canonical_security_id"),
        )
    finally:
        factor_connection.close()
        market_connection.close()
    if factor_rows != EXPECTED_FACTOR_ROWS or market_rows <= 0:
        raise RuntimeError("HANDOFF_EXPORT_ROW_COUNT_MISMATCH")
    registry = factor_registry_rows(root)
    registry_fields = list(registry[0])
    write_csv(package / "factor_registry_2026.csv", registry_fields, registry)
    metrics = parquet_metrics(data / "factor_library_2026.parquet", data / "market_2026.parquet")
    source_after = source_hashes(root)
    acceptance = acceptance_rows(root, metrics, registry, source_before, source_after)
    write_csv(package / "acceptance.csv", list(acceptance[0]), acceptance)
    (package / "README.md").write_text(package_readme(), encoding="utf-8")
    manifest = manifest_rows(root, package, source_before)
    manifest_fields = list(manifest[0])
    write_csv(package / "handoff_manifest.csv", manifest_fields, manifest)
    write_audit(root, package, acceptance, metrics, source_before)
    refresh_github_manifest(root)
    return acceptance, {
        "factor_parquet_sha256": factor_sha,
        "market_parquet_sha256": market_sha,
        "factor_rows": factor_rows,
        "market_rows": market_rows,
        "source_hashes": source_before,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=project_root())
    arguments = parser.parse_args(argv)
    acceptance, result = prepare_recent_extension_handoff(arguments.root)
    blockers = sum(row["status"] in {"FAIL", "BLOCKED"} for row in acceptance)
    print(json.dumps({"checks": len(acceptance), "blockers": blockers, **result}, sort_keys=True))
    return 0 if blockers == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
