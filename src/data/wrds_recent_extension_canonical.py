"""Build the separate 2026 RECENT_EXTENSION_MIXED_SOURCE canonical market layer.

This module reads immutable WRDS Compustat extracts plus completed bridge
audits.  It never alters the Formal 2006–2025 CRSP canonical SQLite database,
does not build factors, and does not run strategy logic.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import hashlib
import math
import os
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator


DATA_LINEAGE = "RECENT_EXTENSION_MIXED_SOURCE"
YEAR_START = "2026-01-01"
YEAR_END = "2026-12-31"
EXPECTED_MEMBER_DATES = 97_587
EXPECTED_RETURN_AVAILABLE = 96_959
EXPECTED_RETURN_UNAVAILABLE = 628
EXPECTED_MARKET_CAP_AVAILABLE = 97_582
EXPECTED_MARKET_CAP_UNAVAILABLE = 5
EXPECTED_DIRECT_RETURNS = 83_584
EXPECTED_AUTHORIZED_RECOVERIES = 13_375
EXPECTED_UNVALIDATED_NULLS = 625
EXPECTED_NEW_CTVA_NULLS = 3

RETURN_DIRECT = "COMPUSTAT_TRFD_TOTAL_RETURN"
RETURN_RECOVERY = "AUTHORIZED_CONSTANT_TRFD_RECOVERY"
RETURN_UNVALIDATED = "UNAVAILABLE_UNVALIDATED_STRUCTURAL_NULL_TRFD"
RETURN_NEW_CTVA = "UNAVAILABLE_NEW_CTVA_STARTUP_TRFD"
MARKET_CAP_AVAILABLE = "AVAILABLE"
MARKET_CAP_NO_FILL = "UNAVAILABLE_MISSING_CSHOC_NO_FILL"

MAIN_SECURITY_DAILY = Path(
    "wrds_raw/US_WRDS_2026-10-13/compustat_recent_extension/security_daily/"
    "compustat_security_daily_sp500_bridge_20250101_20261011_raw.csv.gz"
)
CTVA_SUPPLEMENT = Path(
    "wrds_raw/US_WRDS_2026-10-13/compustat_recent_extension/security_daily/"
    "compustat_security_daily_ctva_postspin_20261001_20261009_raw.csv.gz"
)
VMRK_SUPPLEMENT = Path(
    "wrds_raw/US_WRDS_2026-10-13/compustat_recent_extension/security_daily/"
    "compustat_security_daily_vmrk_transition_20260818_20261009_raw.csv.gz"
)
VYLR_SUPPLEMENT = Path(
    "wrds_raw/US_WRDS_2026-10-13/compustat_recent_extension/security_daily/"
    "compustat_security_daily_vylr_supplement_20261001_20261009_raw.csv.gz"
)
INDEX_DAILY = Path(
    "wrds_raw/US_WRDS_2026-10-13/compustat_recent_extension/index_daily/"
    "compustat_sp500_index_daily_20250101_20261009_raw.csv.gz"
)
MEMBERSHIP = Path("outputs/audit/wrds_2026_bridge/sp500_membership_daily_2026.csv")
CTVA_PRETRANSITION = Path("outputs/audit/wrds_2026_bridge/ctva_pretransition_member_date_recovery.csv")
CTVA_VYLR_FINAL = Path("outputs/audit/wrds_2026_bridge/ctva_vylr_identity_transition_final_2026.csv")
EQR_VMRK_FINAL = Path("outputs/audit/wrds_2026_bridge/eqr_vmrk_identity_transition_final_2026.csv")
TRFD_CLASSIFICATION = Path("outputs/audit/wrds_2026_bridge/trfd_missing_classification_2025_2026.csv")
RESIDUAL_DECISIONS = Path("outputs/audit/wrds_2026_bridge/residual_structural_null_recovery_decision.csv")
MISSING_CSHOC = Path("outputs/audit/wrds_2026_bridge/missing_cshoc_exceptions_2026.csv")

STORE = Path("data/canonical/wrds_recent_extension/us_recent_extension_market_2026.sqlite")
AUDIT_DIR = Path("outputs/audit/wrds_2026_bridge")
AUDIT_REPORT = AUDIT_DIR / "WRDS_2026_RECENT_EXTENSION_CANONICAL_BUILD.md"
AUDIT_COVERAGE = AUDIT_DIR / "recent_extension_canonical_coverage.csv"
AUDIT_EXCEPTIONS = AUDIT_DIR / "recent_extension_canonical_residual_exceptions.csv"
AUDIT_IDENTITIES = AUDIT_DIR / "recent_extension_canonical_identity_validation.csv"
AUDIT_METHODS = AUDIT_DIR / "recent_extension_canonical_return_method_counts.csv"


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _id(value: Any, width: int) -> str:
    text = _clean(value)
    return text.zfill(width) if text else ""


def _number(value: Any) -> float | None:
    try:
        result = float(_clean(value))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _csv_rows(path: Path) -> Iterator[dict[str, str]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", newline="", encoding="utf-8-sig") as handle:
        yield from csv.DictReader(handle)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (_id(row.get("gvkey"), 6), _id(row.get("iid"), 2), _clean(row.get("cusip")), _clean(row.get("datadate")))


def _identity_key(row: dict[str, str]) -> tuple[str, str, str]:
    return (_id(row.get("gvkey"), 6), _id(row.get("iid"), 2), _clean(row.get("cusip")))


def _level(row: dict[str, str] | None) -> float | None:
    if row is None:
        return None
    price, adjustment, trfd = _number(row.get("prccd")), _number(row.get("ajexdi")), _number(row.get("trfd"))
    if None in (price, adjustment, trfd) or adjustment == 0:
        return None
    return (price / adjustment) * trfd


def adjusted_price_return(current: dict[str, str] | None, previous: dict[str, str] | None) -> float | None:
    """Return under the already authorized constant-TRFD diagnostic rule."""

    if current is None or previous is None:
        return None
    current_price, current_adjustment = _number(current.get("prccd")), _number(current.get("ajexdi"))
    previous_price, previous_adjustment = _number(previous.get("prccd")), _number(previous.get("ajexdi"))
    if None in (current_price, current_adjustment, previous_price, previous_adjustment):
        return None
    if current_adjustment == 0 or previous_adjustment == 0 or previous_price == 0:
        return None
    return (current_price / current_adjustment) / (previous_price / previous_adjustment) - 1.0


def direct_total_return(current: dict[str, str] | None, previous: dict[str, str] | None) -> float | None:
    """Return under the frozen Compustat Security Daily total-return formula."""

    current_level, previous_level = _level(current), _level(previous)
    if current_level is None or previous_level is None or previous_level == 0:
        return None
    return current_level / previous_level - 1.0


def benchmark_return(current: dict[str, str] | None, previous: dict[str, str] | None) -> float | None:
    if current is None or previous is None:
        return None
    current_value, previous_value = _number(current.get("prccddiv")), _number(previous.get("prccddiv"))
    if current_value is None or previous_value in (None, 0):
        return None
    return current_value / previous_value - 1.0


def _write_csv(path: Path, fields: tuple[str, ...], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def _source_rows(path: Path, source_name: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in _csv_rows(path):
        record = dict(row)
        record["_source_dataset"] = source_name
        rows.append(record)
    return rows


def _indexed(rows: Iterable[dict[str, str]]) -> dict[tuple[str, str, str, str], dict[str, str]]:
    indexed: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for row in rows:
        key = _source_key(row)
        if not all(key):
            raise ValueError(f"Source row has an incomplete security/date key: {key}")
        if key in indexed:
            raise ValueError(f"Duplicate raw Security Daily source key: {key}")
        indexed[key] = row
    return indexed


def _merge_supplement_indexes(
    base: dict[tuple[str, str, str, str], dict[str, str]],
    supplements: Iterable[dict[str, str]],
) -> dict[tuple[str, str, str, str], dict[str, str]]:
    """Apply an audited supplement only when an overlapping raw value agrees.

    VYLR's seven delivered rows deliberately overlap the main bridge.  The
    supplement has precedence for provenance, but a disagreement in any field
    used by this canonical layer is a hard error rather than a silent source
    substitution.  The main bridge and the supplement intentionally carry
    different non-market metadata column sets, which are not canonical inputs.
    """

    merged = dict(base)
    for row in supplements:
        key = _source_key(row)
        if not all(key):
            raise ValueError(f"Supplemental source row has an incomplete security/date key: {key}")
        existing = merged.get(key)
        if existing is not None:
            fields = ("prccd", "ajexdi", "trfd", "cshoc")
            differences = {
                field: (existing.get(field), row.get(field))
                for field in fields
                if existing.get(field) != row.get(field)
            }
            if differences:
                raise ValueError(f"Conflicting main/supplemental source values for {key}: {differences}")
        merged[key] = row
    return merged


def _by_gi_date(rows: Iterable[dict[str, str]]) -> dict[tuple[str, str, str], list[dict[str, str]]]:
    result: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        result[(_id(row.get("gvkey"), 6), _id(row.get("iid"), 2), _clean(row.get("datadate")))].append(row)
    return result


def _load_authorized_identity_sets(
    root: Path,
) -> tuple[set[tuple[str, str, str]], set[tuple[str, str, str]], set[tuple[str, str, str]]]:
    authorized: set[tuple[str, str, str]] = set()
    classified: set[tuple[str, str, str]] = set()
    for row in _csv_rows(root / TRFD_CLASSIFICATION):
        identity = _identity_key(row)
        classified.add(identity)
        if row.get("trfd_equals_one_authorized") == "true":
            authorized.add(identity)
    if len(authorized) != 70:
        raise ValueError(f"Expected 70 previously authorized structural-null identities, found {len(authorized)}")
    ciena: set[tuple[str, str, str]] = set()
    for row in _csv_rows(root / RESIDUAL_DECISIONS):
        if row.get("recovery_classification") != "AUTHORIZABLE — RETURN RECOVERY SUPPORTED":
            continue
        parts = row["identity"].split(":")
        if len(parts) != 4 or parts[0] != "GVKEY_IID_CUSIP":
            raise ValueError(f"Unexpected residual decision identity: {row['identity']}")
        ciena.add((_id(parts[1], 6), _id(parts[2], 2), parts[3]))
    expected_ciena = {("064356", "01", "171779309")}
    if ciena != expected_ciena:
        raise ValueError(f"Unexpected Ciena recovery authorization: {ciena}")
    unvalidated = classified - authorized - ciena
    if len(unvalidated) != 9:
        raise ValueError(f"Expected nine unvalidated structural-null identities, found {len(unvalidated)}")
    return authorized, ciena, unvalidated


def _load_membership(root: Path) -> list[dict[str, str]]:
    active = [dict(row) for row in _csv_rows(root / MEMBERSHIP) if row.get("membership_flag") == "1"]
    active = [row for row in active if YEAR_START <= row["date"] <= YEAR_END]
    if len(active) != EXPECTED_MEMBER_DATES:
        raise ValueError(f"Expected {EXPECTED_MEMBER_DATES} active member dates, found {len(active)}")
    keys = [(row["date"], row["stable_security_id"], row["ticker"]) for row in active]
    if len(keys) != len(set(keys)):
        raise ValueError("Membership contains duplicate date/stable-security/ticker rows")
    return sorted(active, key=lambda row: (row["date"], row["stable_security_id"], row["ticker"]))


def _load_ctva_maps(root: Path) -> tuple[dict[tuple[str, str], dict[str, str]], dict[tuple[str, str, str], dict[str, str]]]:
    pre: dict[tuple[str, str], dict[str, str]] = {}
    for row in _csv_rows(root / CTVA_PRETRANSITION):
        if row.get("in_memory_bridge_status") != "RECOVERABLE":
            raise ValueError("CTVA pretransition input contains a non-recoverable row")
        key = (row["member_date"], row["stable_security_id"])
        if key in pre:
            raise ValueError(f"Duplicate CTVA pretransition mapping: {key}")
        pre[key] = dict(row)
    if len(pre) != 187:
        raise ValueError(f"Expected 187 CTVA pretransition recovery dates, found {len(pre)}")

    post: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in _csv_rows(root / CTVA_VYLR_FINAL):
        status = row.get("source_mapping_status")
        if status not in {"POSTSPIN_CTVA_SUPPLEMENT", "VYLR_SUPPLEMENT"}:
            continue
        key = (row["date"], row["stable_security_id"], row["official_ticker"])
        if key in post:
            raise ValueError(f"Duplicate CTVA/VYLR post-transition mapping: {key}")
        post[key] = dict(row)
    if sum(row["source_mapping_status"] == "POSTSPIN_CTVA_SUPPLEMENT" for row in post.values()) != 3:
        raise ValueError("Expected exactly three new-CTVA post-spin member dates")
    if sum(row["source_mapping_status"] == "VYLR_SUPPLEMENT" for row in post.values()) != 7:
        raise ValueError("Expected exactly seven VYLR member dates")
    return pre, post


def _load_eqr_map(root: Path) -> dict[tuple[str, str], dict[str, str]]:
    result: dict[tuple[str, str], dict[str, str]] = {}
    for row in _csv_rows(root / EQR_VMRK_FINAL):
        key = (row["date"], row["stable_security_id"])
        if key in result:
            raise ValueError(f"Duplicate EQR/VMRK mapping: {key}")
        result[key] = dict(row)
    transition = [row for row in result.values() if row["source_mapping_status"] == "EQR_TO_VMRK_TRANSITION_SUPPLEMENT"]
    if len(transition) != 38 or min(row["date"] for row in transition) != "2026-08-18":
        raise ValueError("EQR/VMRK transition audit boundary differs from the frozen 38-date mapping")
    return result


def _load_market_cap_exceptions(root: Path) -> set[tuple[str, str]]:
    rows = list(_csv_rows(root / MISSING_CSHOC))
    result = {(row["date"], row["stable_security_id"]) for row in rows}
    if len(result) != 5:
        raise ValueError(f"Expected five unique CSHOC no-fill exceptions, found {len(result)}")
    return result


def _source_from_key(
    indexed: dict[tuple[str, str, str, str], dict[str, str]],
    gvkey: str,
    iid: str,
    cusip: str,
    observed: str,
) -> dict[str, str]:
    key = (_id(gvkey, 6), _id(iid, 2), _clean(cusip), observed)
    try:
        return indexed[key]
    except KeyError as exc:
        raise ValueError(f"Missing audited Security Daily source row: {key}") from exc


def _resolve_rows(
    membership: list[dict[str, str]],
    main_index: dict[tuple[str, str, str, str], dict[str, str]],
    all_index: dict[tuple[str, str, str, str], dict[str, str]],
    main_by_gi_date: dict[tuple[str, str, str], list[dict[str, str]]],
    ctv_pre: dict[tuple[str, str], dict[str, str]],
    ctv_post: dict[tuple[str, str, str], dict[str, str]],
    eqr_map: dict[tuple[str, str], dict[str, str]],
) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for member in membership:
        observed, stable = member["date"], member["stable_security_id"]
        pre = ctv_pre.get((observed, stable))
        post = ctv_post.get((observed, stable, member["ticker"]))
        eqr = eqr_map.get((observed, stable))
        if pre is not None:
            source = _source_from_key(main_index, pre["bridge_gvkey"], pre["bridge_iid"], pre["delivered_cusip"], observed)
            canonical_id = "ECONOMIC_CTVA_LEGACY_035168_01"
            return_group = "TEMPORAL_035168_01_CTVA_VYLR"
            rule = "CTVA_LEGACY_035168_01_THROUGH_2026_09_30"
            provenance = "AUDITED_CTVA_PRETRANSITION_TEMPORAL_BRIDGE"
        elif post is not None:
            source = _source_from_key(all_index, post["observed_gvkey"], post["observed_iid"], post["observed_cusip"], observed)
            if post["source_mapping_status"] == "POSTSPIN_CTVA_SUPPLEMENT":
                canonical_id = "ECONOMIC_CTVA_NEW_077301_01"
                return_group = "TEMPORAL_077301_01_NEW_CTVA"
                rule = "CTVA_NEW_077301_01_FROM_2026_10_01_NO_CROSS_RETURN"
                provenance = "AUDITED_CTVA_POSTSPIN_SUPPLEMENT"
            else:
                canonical_id = "ECONOMIC_VYLR_035168_01"
                return_group = "TEMPORAL_035168_01_CTVA_VYLR"
                rule = "VYLR_035168_01_FROM_2026_10_01"
                provenance = "AUDITED_VYLR_SUPPLEMENT"
        elif eqr is not None:
            source = _source_from_key(
                all_index, eqr["observed_gvkey"], eqr["observed_iid"], eqr["observed_cusip"], observed
            )
            canonical_id = stable
            return_group = "TEMPORAL_EQR_VMRK_CRSP_PERMNO_79547"
            rule = (
                "EQR_TO_VMRK_AUDITED_CONTINUITY_FROM_2026_08_18"
                if eqr["source_mapping_status"] == "EQR_TO_VMRK_TRANSITION_SUPPLEMENT"
                else "EQR_PRE_TRANSITION_MAIN_BRIDGE"
            )
            provenance = "AUDITED_EQR_VMRK_IDENTITY_TRANSITION"
        else:
            candidates = main_by_gi_date.get((_id(member.get("gvkey"), 6), _id(member.get("iid"), 2), observed), [])
            if len(candidates) != 1:
                raise ValueError(
                    f"Expected exactly one main Security Daily source for membership {observed} {stable}; found {len(candidates)}"
                )
            source = candidates[0]
            canonical_id = stable
            return_group = f"RAW:{_id(source['gvkey'], 6)}:{_id(source['iid'], 2)}:{source['cusip']}"
            rule = "STANDARD_GVKEY_IID_SOURCE_MAPPING"
            provenance = "PIT_MEMBERSHIP_TO_MAIN_COMPUSTAT_SECURITY_DAILY"
        resolved.append(
            {
                "membership": member,
                "source": source,
                "canonical_security_id": canonical_id,
                "return_group": return_group,
                "temporal_identity_rule": rule,
                "identity_provenance": provenance,
            }
        )
    return resolved


def _history_by_group(
    main: list[dict[str, str]],
    ctv: list[dict[str, str]],
    vmrk: list[dict[str, str]],
    vylr: list[dict[str, str]],
) -> dict[str, list[dict[str, str]]]:
    groups: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for row in main:
        identity = _identity_key(row)
        observed = row["datadate"]
        if identity == ("035168", "01", "92892R108"):
            if observed <= "2026-09-30":
                group = "TEMPORAL_035168_01_CTVA_VYLR"
            else:
                continue
        elif identity == ("028733", "01", "29599Q934"):
            if observed <= "2026-08-17":
                group = "TEMPORAL_EQR_VMRK_CRSP_PERMNO_79547"
            else:
                continue
        else:
            group = f"RAW:{identity[0]}:{identity[1]}:{identity[2]}"
        groups[group][observed] = row
    for row in ctv:
        groups["TEMPORAL_077301_01_NEW_CTVA"][row["datadate"]] = row
    for row in vmrk:
        groups["TEMPORAL_EQR_VMRK_CRSP_PERMNO_79547"][row["datadate"]] = row
    for row in vylr:
        groups["TEMPORAL_035168_01_CTVA_VYLR"][row["datadate"]] = row
    return {name: [by_date[day] for day in sorted(by_date)] for name, by_date in groups.items()}


def _previous_by_group(histories: dict[str, list[dict[str, str]]]) -> dict[str, tuple[list[str], list[dict[str, str]]]]:
    return {name: ([row["datadate"] for row in rows], rows) for name, rows in histories.items()}


def _prior_observation(
    indexed_histories: dict[str, tuple[list[str], list[dict[str, str]]]], group: str, observed: str
) -> dict[str, str] | None:
    dates, rows = indexed_histories[group]
    position = bisect.bisect_left(dates, observed) - 1
    return rows[position] if position >= 0 else None


def _return_for(
    current: dict[str, str],
    prior: dict[str, str] | None,
    authorized: set[tuple[str, str, str]],
    ciena: set[tuple[str, str, str]],
    is_new_ctva: bool,
) -> tuple[float | None, bool, str]:
    if is_new_ctva:
        return None, False, RETURN_NEW_CTVA
    direct = direct_total_return(current, prior)
    if _number(current.get("trfd")) is not None and direct is not None:
        return direct, True, RETURN_DIRECT
    identity = _identity_key(current)
    if identity in authorized | ciena:
        recovered = adjusted_price_return(current, prior)
        if recovered is not None:
            return recovered, True, RETURN_RECOVERY
    return None, False, RETURN_UNVALIDATED


def _market_cap_for(current: dict[str, str]) -> tuple[float | None, bool]:
    price, shares = _number(current.get("prccd")), _number(current.get("cshoc"))
    if price is None or shares is None or shares <= 0:
        return None, False
    return abs(price) * shares, True


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE canonical_membership_daily (
          date TEXT NOT NULL, membership_security_id TEXT NOT NULL, canonical_security_id TEXT NOT NULL,
          gvkey TEXT, iid TEXT, ticker TEXT, company_name TEXT, member_flag INTEGER NOT NULL,
          membership_provenance TEXT NOT NULL, data_lineage TEXT NOT NULL,
          PRIMARY KEY(date, membership_security_id, ticker));
        CREATE TABLE canonical_security_daily (
          date TEXT NOT NULL, canonical_security_id TEXT NOT NULL, membership_security_id TEXT NOT NULL,
          gvkey TEXT NOT NULL, iid TEXT NOT NULL, ticker TEXT, cusip TEXT, company_name TEXT,
          member_flag INTEGER NOT NULL, membership_provenance TEXT NOT NULL,
          prccd REAL, ajexdi REAL, trfd REAL, cshoc REAL,
          daily_return REAL, market_cap REAL,
          return_available INTEGER NOT NULL, return_method TEXT NOT NULL,
          market_cap_available INTEGER NOT NULL, market_cap_reason TEXT NOT NULL,
          temporal_identity_rule TEXT NOT NULL, identity_provenance TEXT NOT NULL,
          source_dataset TEXT NOT NULL, data_lineage TEXT NOT NULL,
          PRIMARY KEY(date, canonical_security_id));
        CREATE TABLE canonical_benchmark_daily (
          date TEXT PRIMARY KEY, gvkeyx TEXT NOT NULL, tic TEXT NOT NULL, company_name TEXT,
          prccd REAL, prccddiv REAL, benchmark_return REAL, return_available INTEGER NOT NULL,
          return_method TEXT NOT NULL, source_dataset TEXT NOT NULL, data_lineage TEXT NOT NULL);
        CREATE TABLE source_manifest (
          relative_path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, source_role TEXT NOT NULL, data_lineage TEXT NOT NULL);
        CREATE TABLE build_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )


def _create_store(path: Path, security_rows: list[dict[str, Any]], benchmark_rows: list[dict[str, Any]], manifest: list[tuple[str, str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    connection = sqlite3.connect(temporary)
    try:
        _create_schema(connection)
        membership_rows = [
            (
                row["date"], row["membership_security_id"], row["canonical_security_id"], row["membership_gvkey"],
                row["membership_iid"], row["ticker"], row["company_name"], 1, row["membership_provenance"], DATA_LINEAGE,
            )
            for row in security_rows
        ]
        connection.executemany("INSERT INTO canonical_membership_daily VALUES (?,?,?,?,?,?,?,?,?,?)", membership_rows)
        connection.executemany(
            "INSERT INTO canonical_security_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    row["date"], row["canonical_security_id"], row["membership_security_id"], row["gvkey"], row["iid"],
                    row["ticker"], row["cusip"], row["company_name"], 1, row["membership_provenance"], row["prccd"],
                    row["ajexdi"], row["trfd"], row["cshoc"], row["daily_return"], row["market_cap"],
                    int(row["return_available"]), row["return_method"], int(row["market_cap_available"]), row["market_cap_reason"],
                    row["temporal_identity_rule"], row["identity_provenance"], row["source_dataset"], DATA_LINEAGE,
                )
                for row in security_rows
            ],
        )
        connection.executemany(
            "INSERT INTO canonical_benchmark_daily VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    row["date"], row["gvkeyx"], row["tic"], row["company_name"], row["prccd"], row["prccddiv"],
                    row["benchmark_return"], int(row["return_available"]), row["return_method"], row["source_dataset"], DATA_LINEAGE,
                )
                for row in benchmark_rows
            ],
        )
        connection.executemany("INSERT INTO source_manifest VALUES (?,?,?,?)", [(p, h, role, DATA_LINEAGE) for p, h, role in manifest])
        metadata = {
            "data_lineage": DATA_LINEAGE,
            "security_rows": str(len(security_rows)),
            "benchmark_rows": str(len(benchmark_rows)),
            "formal_history_modified": "false",
        }
        connection.executemany("INSERT INTO build_metadata VALUES (?,?)", sorted(metadata.items()))
        connection.execute("CREATE INDEX security_daily_member_idx ON canonical_security_daily(date, membership_security_id)")
        connection.commit()
    except Exception:
        connection.close()
        if temporary.exists():
            temporary.unlink()
        raise
    connection.close()
    os.replace(temporary, path)


def _benchmark_rows(root: Path) -> list[dict[str, Any]]:
    rows = [dict(row) for row in _csv_rows(root / INDEX_DAILY) if YEAR_START <= row["datadate"] <= YEAR_END]
    if not rows:
        raise ValueError("No 2026 Compustat S&P 500 index rows found")
    keys = [row["datadate"] for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("Compustat S&P 500 index extract contains duplicate 2026 dates")
    if {row["gvkeyx"] for row in rows} != {"000003"} or {row["tic"] for row in rows} != {"I0003"}:
        raise ValueError("Benchmark extract is not exclusively GVKEYX 000003 / I0003")
    all_rows = [dict(row) for row in _csv_rows(root / INDEX_DAILY)]
    by_date = {row["datadate"]: row for row in all_rows}
    ordered = sorted(all_rows, key=lambda row: row["datadate"])
    positions = {row["datadate"]: idx for idx, row in enumerate(ordered)}
    result: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: item["datadate"]):
        index = positions[row["datadate"]]
        prior = ordered[index - 1] if index else None
        value = benchmark_return(row, prior)
        result.append(
            {
                "date": row["datadate"], "gvkeyx": row["gvkeyx"], "tic": row["tic"], "company_name": row["conm"],
                "prccd": _number(row["prccd"]), "prccddiv": _number(row["prccddiv"]), "benchmark_return": value,
                "return_available": value is not None, "return_method": "COMPUSTAT_PRCCDDIV_TOTAL_RETURN" if value is not None else "UNAVAILABLE_NO_PRIOR_PRCCDDIV",
                "source_dataset": INDEX_DAILY.as_posix(),
            }
        )
    return result


def _validation(
    security_rows: list[dict[str, Any]],
    benchmark_rows: list[dict[str, Any]],
    cshoc_exceptions: set[tuple[str, str]],
    authorized: set[tuple[str, str, str]],
    ciena: set[tuple[str, str, str]],
    unvalidated: set[tuple[str, str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if len(security_rows) != EXPECTED_MEMBER_DATES:
        raise ValueError(f"Security-row count mismatch: {len(security_rows)}")
    natural_keys = {(row["date"], row["canonical_security_id"]) for row in security_rows}
    if len(natural_keys) != len(security_rows):
        raise ValueError("Duplicate canonical economic identity/date rows")
    membership_keys = {(row["date"], row["membership_security_id"], row["ticker"]) for row in security_rows}
    if len(membership_keys) != len(security_rows):
        raise ValueError("Duplicate membership identity/date/ticker rows")
    return_counts = Counter(row["return_method"] for row in security_rows)
    market_cap_available = sum(row["market_cap_available"] for row in security_rows)
    return_available = sum(row["return_available"] for row in security_rows)
    if return_counts != Counter({
        RETURN_DIRECT: EXPECTED_DIRECT_RETURNS,
        RETURN_RECOVERY: EXPECTED_AUTHORIZED_RECOVERIES,
        RETURN_UNVALIDATED: EXPECTED_UNVALIDATED_NULLS,
        RETURN_NEW_CTVA: EXPECTED_NEW_CTVA_NULLS,
    }):
        raise ValueError(f"Unexpected return-method counts: {return_counts}")
    if return_available != EXPECTED_RETURN_AVAILABLE or len(security_rows) - return_available != EXPECTED_RETURN_UNAVAILABLE:
        raise ValueError(f"Return coverage mismatch: {return_available}/{len(security_rows)}")
    if market_cap_available != EXPECTED_MARKET_CAP_AVAILABLE or len(security_rows) - market_cap_available != EXPECTED_MARKET_CAP_UNAVAILABLE:
        raise ValueError(f"Market-cap coverage mismatch: {market_cap_available}/{len(security_rows)}")
    actual_cshoc = {(row["date"], row["membership_security_id"]) for row in security_rows if not row["market_cap_available"]}
    if actual_cshoc != cshoc_exceptions:
        raise ValueError(f"Market-cap no-fill set mismatch: actual={actual_cshoc} expected={cshoc_exceptions}")
    if any(row["return_method"] == RETURN_RECOVERY and _number(row["trfd"]) is not None for row in security_rows):
        raise ValueError("Authorized constant-TRFD recovery incorrectly populated a TRFD value")
    ciena_rows = [row for row in security_rows if row["gvkey"] == "064356" and row["iid"] == "01" and row["cusip"] == "171779309"]
    if len(ciena_rows) != 169 or any(row["return_method"] != RETURN_RECOVERY for row in ciena_rows):
        raise ValueError("Ciena must contribute exactly 169 authorized recovered observations")
    recovered_identities = {
        (row["gvkey"], row["iid"], row["cusip"])
        for row in security_rows
        if row["return_method"] == RETURN_RECOVERY
    }
    if recovered_identities != authorized | ciena:
        raise ValueError("Recovered structural-null identities differ from the frozen authorization set")
    unvalidated_rows = [row for row in security_rows if row["return_method"] == RETURN_UNVALIDATED]
    if {(row["gvkey"], row["iid"], row["cusip"]) for row in unvalidated_rows} != unvalidated:
        raise ValueError("Unvalidated structural-null no-fill identities differ from the frozen residual set")
    ctv_new = [row for row in security_rows if row["temporal_identity_rule"] == "CTVA_NEW_077301_01_FROM_2026_10_01_NO_CROSS_RETURN"]
    if len(ctv_new) != 3 or any(row["return_method"] != RETURN_NEW_CTVA for row in ctv_new):
        raise ValueError("New CTVA startup no-fill invariant failed")
    if len(benchmark_rows) != len({row["date"] for row in benchmark_rows}):
        raise ValueError("Benchmark contains duplicate dates")
    if not benchmark_rows or benchmark_rows[0]["date"] != "2026-01-02" or benchmark_rows[-1]["date"] != "2026-10-09":
        raise ValueError("Benchmark delivered 2026 date range differs from the frozen source")

    coverage = [
        {
            "metric": "RETURN_INPUT", "numerator": return_available, "denominator": len(security_rows),
            "coverage_fraction": f"{return_available / len(security_rows):.12f}", "coverage_percent": f"{100 * return_available / len(security_rows):.9f}",
            "residual_unavailable": len(security_rows) - return_available, "status": "PASS",
        },
        {
            "metric": "MARKET_CAP_INPUT", "numerator": market_cap_available, "denominator": len(security_rows),
            "coverage_fraction": f"{market_cap_available / len(security_rows):.12f}", "coverage_percent": f"{100 * market_cap_available / len(security_rows):.9f}",
            "residual_unavailable": len(security_rows) - market_cap_available, "status": "PASS",
        },
    ]
    exceptions = [
        {
            "date": row["date"], "canonical_security_id": row["canonical_security_id"], "membership_security_id": row["membership_security_id"],
            "ticker": row["ticker"], "company_name": row["company_name"],
            "exception_type": row["return_method"], "availability_domain": "RETURN", "reason": row["return_method"],
            "temporal_identity_rule": row["temporal_identity_rule"], "data_lineage": DATA_LINEAGE,
        }
        for row in security_rows if not row["return_available"]
    ]
    exceptions.extend(
        {
            "date": row["date"], "canonical_security_id": row["canonical_security_id"], "membership_security_id": row["membership_security_id"],
            "ticker": row["ticker"], "company_name": row["company_name"],
            "exception_type": row["market_cap_reason"], "availability_domain": "MARKET_CAP", "reason": row["market_cap_reason"],
            "temporal_identity_rule": row["temporal_identity_rule"], "data_lineage": DATA_LINEAGE,
        }
        for row in security_rows if not row["market_cap_available"]
    )
    identity_rows = [
        {"check": "ACTIVE_MEMBER_DATE_COUNT", "expected": EXPECTED_MEMBER_DATES, "actual": len(security_rows), "status": "PASS", "detail": "Official reconstructed point-in-time membership."},
        {"check": "DUPLICATE_CANONICAL_ECONOMIC_IDENTITY_DATE", "expected": 0, "actual": len(security_rows)-len(natural_keys), "status": "PASS", "detail": "Unique (date, canonical_security_id)."},
        {"check": "CTVA_LEGACY_PRETRANSITION", "expected": 187, "actual": sum(row["temporal_identity_rule"] == "CTVA_LEGACY_035168_01_THROUGH_2026_09_30" for row in security_rows), "status": "PASS", "detail": "Audited legacy economic bridge."},
        {"check": "VYLR_POSTTRANSITION", "expected": 7, "actual": sum(row["temporal_identity_rule"] == "VYLR_035168_01_FROM_2026_10_01" for row in security_rows), "status": "PASS", "detail": "Separate VYLR identity from 2026-10-01."},
        {"check": "NEW_CTVA_POSTSPIN", "expected": 3, "actual": len(ctv_new), "status": "PASS", "detail": "Separate new CTVA identity; no cross-identity return."},
        {"check": "EQR_TO_VMRK_TRANSITION", "expected": 38, "actual": sum(row["temporal_identity_rule"] == "EQR_TO_VMRK_AUDITED_CONTINUITY_FROM_2026_08_18" for row in security_rows), "status": "PASS", "detail": "Exact audited 2026-08-18 boundary."},
        {"check": "AUTHORIZED_STRUCTURAL_NULL_IDENTITIES", "expected": 70, "actual": len(recovered_identities - ciena), "status": "PASS", "detail": "Only frozen authorized identity set recovered."},
        {"check": "CIENA_RECOVERED_MEMBER_DATES", "expected": 169, "actual": len(ciena_rows), "status": "PASS", "detail": "Authorized Ciena adjusted-price-ratio recovery."},
        {"check": "UNVALIDATED_STRUCTURAL_NULL_NO_FILL", "expected": 625, "actual": len(unvalidated_rows), "status": "PASS", "detail": "Nine residual structural-null identities retained as unavailable."},
        {"check": "BENCHMARK_DUPLICATE_DATES", "expected": 0, "actual": len(benchmark_rows)-len({row["date"] for row in benchmark_rows}), "status": "PASS", "detail": "Compustat GVKEYX 000003 / I0003."},
    ]
    methods = [
        {"return_method": method, "observation_count": return_counts[method], "availability": "true" if method in {RETURN_DIRECT, RETURN_RECOVERY} else "false", "data_lineage": DATA_LINEAGE}
        for method in (RETURN_DIRECT, RETURN_RECOVERY, RETURN_UNVALIDATED, RETURN_NEW_CTVA)
    ]
    return coverage, exceptions, identity_rows, methods


def _write_audits(root: Path, coverage: list[dict[str, Any]], exceptions: list[dict[str, Any]], identities: list[dict[str, Any]], methods: list[dict[str, Any]], benchmark_rows: list[dict[str, Any]], store: Path) -> None:
    _write_csv(root / AUDIT_COVERAGE, tuple(coverage[0]), coverage)
    _write_csv(root / AUDIT_EXCEPTIONS, tuple(exceptions[0]), sorted(exceptions, key=lambda row: (row["date"], row["availability_domain"], row["canonical_security_id"])))
    _write_csv(root / AUDIT_IDENTITIES, tuple(identities[0]), identities)
    _write_csv(root / AUDIT_METHODS, tuple(methods[0]), methods)
    report = f'''# WRDS 2026 Recent Extension Canonical Build

## Result

**RECENT_EXTENSION_CANONICAL_BUILD PASS WITH EXPECTED NO-FILL EXCEPTIONS**

The canonical artifact is a separate 2026 `RECENT_EXTENSION_MIXED_SOURCE` SQLite layer. It neither modifies nor relabels the Formal 2006–2025 CRSP canonical history.

## Construction

- Security returns with delivered `TRFD` use `(PRCCD / AJEXDI * TRFD)_t / (PRCCD / AJEXDI * TRFD)_{{t-1}} - 1`.
- The 70 previously authorized structural-null identities and Ciena use only the authorized adjusted-price-ratio recovery. Source `TRFD` stays null.
- Nine unvalidated structural-null identities remain unavailable for 625 observations. New CTVA remains unavailable for its three startup observations.
- Market capitalization is `ABS(PRCCD) * CSHOC`; DAY (3) and HOLX (2) remain no-fill.
- Legacy CTVA is bridged through 2026-09-30, VYLR is separate from 2026-10-01, new CTVA is separate from 2026-10-01, and EQR/VMRK uses the audited 2026-08-18 boundary.
- The separate benchmark is Compustat GVKEYX `000003` / I0003 using `PRCCDDIV_t / PRCCDDIV_{{t-1}} - 1`.

## Acceptance metrics

| Metric | Result |
|---|---:|
| Active member-date rows | 97,587 |
| Available return inputs | 96,959 / 97,587 (99.356449527%) |
| Residual unavailable return inputs | 628 = 625 unvalidated structural-null + 3 new CTVA |
| Available market-cap inputs | 97,582 / 97,587 (99.994876367%) |
| Residual unavailable market-cap inputs | 5 = DAY 3 + HOLX 2 |
| Benchmark delivered date range | {benchmark_rows[0]['date']} to {benchmark_rows[-1]['date']} |
| Canonical store | `{store.as_posix()}` |

No factor construction, IC analysis, portfolio construction, strategy, or backtest operation was run.
'''
    with (root / AUDIT_REPORT).open("w", encoding="utf-8", newline="") as handle:
        handle.write(report)


def build(root: Path | None = None) -> dict[str, Any]:
    """Build and validate the isolated 2026 canonical market layer."""

    root = root or project_root()
    main = _source_rows(root / MAIN_SECURITY_DAILY, MAIN_SECURITY_DAILY.as_posix())
    ctv = _source_rows(root / CTVA_SUPPLEMENT, CTVA_SUPPLEMENT.as_posix())
    vmrk = _source_rows(root / VMRK_SUPPLEMENT, VMRK_SUPPLEMENT.as_posix())
    vylr = _source_rows(root / VYLR_SUPPLEMENT, VYLR_SUPPLEMENT.as_posix())
    main_index = _indexed(main)
    all_index = _merge_supplement_indexes(main_index, [*ctv, *vmrk, *vylr])
    membership = _load_membership(root)
    authorized, ciena, unvalidated = _load_authorized_identity_sets(root)
    ctv_pre, ctv_post = _load_ctva_maps(root)
    eqr_map = _load_eqr_map(root)
    cshoc_exceptions = _load_market_cap_exceptions(root)
    resolved = _resolve_rows(membership, main_index, all_index, _by_gi_date(main), ctv_pre, ctv_post, eqr_map)
    histories = _previous_by_group(_history_by_group(main, ctv, vmrk, vylr))
    security_rows: list[dict[str, Any]] = []
    for item in resolved:
        member, source = item["membership"], item["source"]
        prior = _prior_observation(histories, item["return_group"], member["date"])
        is_new_ctva = item["temporal_identity_rule"] == "CTVA_NEW_077301_01_FROM_2026_10_01_NO_CROSS_RETURN"
        daily_return, return_available, return_method = _return_for(source, prior, authorized, ciena, is_new_ctva)
        market_cap, market_cap_available = _market_cap_for(source)
        source_exception_key = (member["date"], member["stable_security_id"])
        if market_cap_available and source_exception_key in cshoc_exceptions:
            raise ValueError(f"Audited CSHOC no-fill unexpectedly has a market cap: {source_exception_key}")
        if not market_cap_available and source_exception_key not in cshoc_exceptions:
            raise ValueError(f"Unexpected market-cap unavailable observation: {source_exception_key}")
        security_rows.append(
            {
                "date": member["date"], "canonical_security_id": item["canonical_security_id"], "membership_security_id": member["stable_security_id"],
                "membership_gvkey": _id(member.get("gvkey"), 6), "membership_iid": _id(member.get("iid"), 2),
                "gvkey": _id(source["gvkey"], 6), "iid": _id(source["iid"], 2), "ticker": member["ticker"], "cusip": source["cusip"],
                "company_name": member["company_name"], "membership_provenance": member["membership_source"],
                "prccd": _number(source.get("prccd")), "ajexdi": _number(source.get("ajexdi")), "trfd": _number(source.get("trfd")),
                "cshoc": _number(source.get("cshoc")), "daily_return": daily_return, "market_cap": market_cap,
                "return_available": return_available, "return_method": return_method,
                "market_cap_available": market_cap_available, "market_cap_reason": MARKET_CAP_AVAILABLE if market_cap_available else MARKET_CAP_NO_FILL,
                "temporal_identity_rule": item["temporal_identity_rule"], "identity_provenance": item["identity_provenance"],
                "source_dataset": source["_source_dataset"],
            }
        )
    security_rows.sort(key=lambda row: (row["date"], row["canonical_security_id"]))
    benchmarks = _benchmark_rows(root)
    coverage, exceptions, identities, methods = _validation(
        security_rows, benchmarks, cshoc_exceptions, authorized, ciena, unvalidated
    )
    manifest_paths = [
        (MAIN_SECURITY_DAILY, "MAIN_SECURITY_DAILY"), (CTVA_SUPPLEMENT, "CTVA_POSTSPIN_SUPPLEMENT"),
        (VMRK_SUPPLEMENT, "VMRK_TRANSITION_SUPPLEMENT"), (VYLR_SUPPLEMENT, "VYLR_SUPPLEMENT"),
        (INDEX_DAILY, "SP500_COMPLTD_BENCHMARK"), (MEMBERSHIP, "AUDITED_MEMBERSHIP"),
        (CTVA_PRETRANSITION, "AUDITED_CTVA_TEMPORAL_BRIDGE"), (CTVA_VYLR_FINAL, "AUDITED_CTVA_VYLR_TRANSITION"),
        (EQR_VMRK_FINAL, "AUDITED_EQR_VMRK_TRANSITION"), (TRFD_CLASSIFICATION, "AUTHORIZED_STRUCTURAL_NULL_SET"),
        (RESIDUAL_DECISIONS, "CIENA_AND_RESIDUAL_DECISIONS"), (MISSING_CSHOC, "MARKET_CAP_NO_FILL_SET"),
    ]
    manifest = [(path.as_posix(), _sha256(root / path), role) for path, role in manifest_paths]
    store = root / STORE
    _create_store(store, security_rows, benchmarks, manifest)
    _write_audits(root, coverage, exceptions, identities, methods, benchmarks, STORE)
    return {
        "status": "RECENT_EXTENSION_CANONICAL_BUILD PASS WITH EXPECTED NO-FILL EXCEPTIONS",
        "store": store, "security_rows": len(security_rows), "benchmark_rows": len(benchmarks),
        "return_available": sum(row["return_available"] for row in security_rows),
        "market_cap_available": sum(row["market_cap_available"] for row in security_rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=project_root(), help="Repository root")
    args = parser.parse_args()
    result = build(args.root.resolve())
    print(result["status"])
    print(f"store={result['store']}")
    print(f"security_rows={result['security_rows']} benchmark_rows={result['benchmark_rows']}")
    print(f"return_available={result['return_available']} market_cap_available={result['market_cap_available']}")


if __name__ == "__main__":
    main()
