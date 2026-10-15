"""Build the isolated 2025--2026 cross-boundary execution bridge.

This is a mechanical data bridge only.  It leaves every frozen Formal
2006--2025 artifact untouched and does not calculate factor, portfolio, or
strategy results.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import math
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

from src.data import wrds_recent_extension_canonical as extension
from src.handoff import validate_us_cross_boundary_2025_2026_delivery as delivery


FORMAL_CALENDAR = Path("handoff/us_downstream/data/weekly_execution_calendar.parquet")
FORMAL_SECURITY_RETURNS = Path("handoff/us_downstream/data/security_weekly_execution_returns.parquet")
FORMAL_BENCHMARK_RETURNS = Path("handoff/us_downstream/data/benchmark_weekly_execution_returns.parquet")
FORMAL_UNIVERSE = Path("handoff/final_factor/data/universe_weekly.parquet")
RECENT_FACTOR_LIBRARY = Path("handoff/us_recent_extension_2026/data/factor_library_2026.parquet")

SECURITY_SOURCE = Path(
    "wrds_raw/US_WRDS_2026-10-13/compustat_recent_extension/security_daily/"
    "compustat_security_daily_sp500_bridge_20250101_20261011_raw.csv.gz"
)
BENCHMARK_SOURCE = Path(
    "wrds_raw/US_WRDS_2026-10-13/compustat_recent_extension/index_daily/"
    "compustat_sp500_index_daily_20250101_20261009_raw.csv.gz"
)
RF_SOURCE = Path(
    "wrds_raw/US_WRDS_2026-10-09/fama_french/"
    "Fama_French_5Factors_Momentum_RF_Daily_FullHistory_to_2026-08-31.csv.gz"
)
IDENTITY_AUDIT = Path("outputs/audit/wrds_2026_bridge/security_identity_bridge_2025.csv")

PACKAGE = Path("handoff/us_cross_boundary_2025_2026")
LINEAGE = "CROSS_BOUNDARY_2025_2026_BRIDGE"
SIGNAL_DATE = "2025-12-26"
EXECUTION_DATE = "2025-12-29"
NEXT_SIGNAL_DATE = "2026-01-02"
NEXT_EXECUTION_DATE = "2026-01-05"
HOLDING_DATES = ("2025-12-30", "2025-12-31", "2026-01-02", "2026-01-05")
PATH_DATES = (EXECUTION_DATE, *HOLDING_DATES)
BENCHMARK_ID = "COMPUSTAT_SP500_COMP_LTD_GVKEYX_000003_I0003"
BENCHMARK_GVKEYX = "000003"
BENCHMARK_TIC = "I0003"
BENCHMARK_NAME = "S&P 500 Comp-Ltd"
FORBIDDEN_COLUMNS = frozenset({
    "weight", "holding", "holdings", "portfolio_return", "strategy_return",
    "nav", "sharpe", "fmom", "ic", "factor_return",
})

CALENDAR_SCHEMA = pa.schema([
    ("signal_date", pa.string()), ("execution_date", pa.string()),
    ("next_signal_date", pa.string()), ("next_execution_date", pa.string()),
    ("return_period_complete", pa.int8()), ("calendar_status", pa.string()),
    ("data_lineage", pa.string()),
])
SECURITY_SCHEMA = pa.schema([
    ("signal_date", pa.string()), ("execution_date", pa.string()),
    ("next_execution_date", pa.string()), ("PERMNO", pa.string()),
    ("PERMCO", pa.string()), ("historical_GVKEY", pa.string()),
    ("historical_gics_sector", pa.string()), ("eligible_universe_flag", pa.int8()),
    ("canonical_security_id", pa.string()), ("compustat_gvkey", pa.string()),
    ("iid", pa.string()), ("cusip", pa.string()), ("weekly_total_return", pa.float64()),
    ("path_complete", pa.int8()), ("return_available", pa.int8()),
    ("trading_day_count", pa.int16()), ("return_source", pa.string()),
    ("mapping_status", pa.string()), ("unavailable_reason", pa.string()),
    ("data_lineage", pa.string()),
])
BENCHMARK_SCHEMA = pa.schema([
    ("signal_date", pa.string()), ("execution_date", pa.string()),
    ("next_execution_date", pa.string()), ("benchmark_id", pa.string()),
    ("benchmark_name", pa.string()), ("benchmark_total_return", pa.float64()),
    ("path_complete", pa.int8()), ("return_available", pa.int8()),
    ("trading_day_count", pa.int16()), ("return_method", pa.string()),
    ("unavailable_reason", pa.string()), ("data_lineage", pa.string()),
])
RF_SCHEMA = pa.schema([
    ("signal_date", pa.string()), ("execution_date", pa.string()),
    ("next_execution_date", pa.string()), ("risk_free_weekly_return", pa.float64()),
    ("path_complete", pa.int8()), ("return_available", pa.int8()),
    ("trading_day_count", pa.int16()), ("return_method", pa.string()),
    ("source_field", pa.string()), ("unit", pa.string()),
    ("unavailable_reason", pa.string()), ("data_lineage", pa.string()),
])
IDENTITY_FIELDS = (
    "signal_date", "PERMNO", "PERMCO", "historical_GVKEY", "historical_gics_sector",
    "eligible_universe_flag", "Compustat_GVKEY", "IID", "CUSIP", "mapping_method",
    "mapping_status", "evidence_source",
)
ACCEPTANCE_FIELDS = ("check", "status", "blocking", "evidence", "notes")


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def csv_rows(path: Path) -> Iterator[dict[str, str]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def write_csv(path: Path, fields: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="raise", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_parquet(path: Path, schema: pa.Schema, rows: Iterable[dict[str, Any]]) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
    count, batch = 0, []
    writer = pq.ParquetWriter(temporary, schema, compression="zstd", version="2.6")
    try:
        for row in rows:
            batch.append(row)
            if len(batch) == 4096:
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


def source_root_path(root: Path, source_root: Path | str) -> Path:
    configured = Path(source_root)
    return configured if configured.is_absolute() else root / configured


def source_path(root: Path, source_root: Path | str, relative: Path) -> Path:
    path = source_root_path(root, source_root) / relative
    if not path.is_file():
        raise FileNotFoundError(f"CROSS_BOUNDARY_SOURCE_NOT_FOUND:{relative}")
    return path


def portable_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return f"EXTERNAL_SOURCE_ROOT/{path.name}"


def number(value: Any) -> float | None:
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def compound(values: Sequence[float]) -> float | None:
    wealth = 1.0
    for value in values:
        if not math.isfinite(value) or value <= -1.0:
            return None
        wealth *= 1.0 + value
    return wealth - 1.0


def frozen_gap(root: Path) -> tuple[dict[str, Any], set[tuple[str, str]]]:
    calendar = pq.read_table(root / FORMAL_CALENDAR, filters=[("signal_date", "=", SIGNAL_DATE)]).to_pylist()
    if len(calendar) != 1:
        raise RuntimeError("FORMAL_CUTOFF_CALENDAR_ROW_NOT_UNIQUE")
    row = calendar[0]
    expected = {
        "execution_date": EXECUTION_DATE, "next_execution_date": "",
        "return_period_complete": 0, "calendar_status": "NO_NEXT_SIGNAL_WITHIN_FORMAL_CUTOFF",
    }
    if any(row.get(key) != value for key, value in expected.items()):
        raise RuntimeError(f"FORMAL_CUTOFF_GAP_CONTRACT_MISMATCH:{row}")
    recent = pq.read_table(
        root / RECENT_FACTOR_LIBRARY,
        columns=["signal_date", "execution_date"],
        filters=[("signal_date", "=", NEXT_SIGNAL_DATE)],
    ).to_pylist()
    pairs = {(str(item["signal_date"]), str(item["execution_date"])) for item in recent}
    if pairs != {(NEXT_SIGNAL_DATE, NEXT_EXECUTION_DATE)}:
        raise RuntimeError(f"RECENT_EXTENSION_FIRST_SIGNAL_MISMATCH:{pairs}")
    return row, pairs


def requested_universe(root: Path) -> list[dict[str, Any]]:
    rows = pq.read_table(root / FORMAL_UNIVERSE, filters=[("signal_date", "=", SIGNAL_DATE)]).to_pylist()
    eligible = [row for row in rows if int(row["historical_sp500_member_flag"]) == 1 and int(row["eligible_universe_flag"]) == 1]
    keys = [str(row["PERMNO"]) for row in eligible]
    if not eligible or len(keys) != len(set(keys)):
        raise RuntimeError("FORMAL_UNIVERSE_REQUEST_SET_INVALID")
    if any(str(row["execution_date"]) != EXECUTION_DATE for row in eligible):
        raise RuntimeError("FORMAL_UNIVERSE_EXECUTION_DATE_MISMATCH")
    return sorted(eligible, key=lambda row: int(str(row["PERMNO"])))


def mapping_audit(source_root: Path) -> dict[str, list[dict[str, str]]]:
    output: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in csv_rows(source_root / IDENTITY_AUDIT):
        if row.get("date") == EXECUTION_DATE and row.get("comparison_eligible") == "true":
            output[str(row["permno"])].append(row)
    return output


def build_identity_rows(universe: Sequence[dict[str, Any]], audit: dict[str, list[dict[str, str]]]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    accepted = {"MATCHED_CCM_LIID", "MATCHED_CCM_LIID_CUSIP_MISMATCH"}
    for member in universe:
        permno, historical_gvkey = str(member["PERMNO"]), str(member["GVKEY"])
        candidates = [row for row in audit.get(permno, []) if row.get("ccm_gvkey") == historical_gvkey]
        base = {
            "signal_date": SIGNAL_DATE, "PERMNO": permno, "PERMCO": str(member["PERMCO"]),
            "historical_GVKEY": historical_gvkey, "historical_gics_sector": str(member["historical_gics_sector"]),
            "eligible_universe_flag": str(int(member["eligible_universe_flag"])),
            "Compustat_GVKEY": "", "IID": "", "CUSIP": "", "mapping_method": "",
            "mapping_status": "", "evidence_source": "security_identity_bridge_2025.csv",
        }
        if len(candidates) != 1:
            base["mapping_status"] = "UNMAPPED" if not candidates else "AMBIGUOUS"
            base["mapping_method"] = "NO_UNIQUE_HISTORICAL_CCM_EVIDENCE"
        else:
            candidate = candidates[0]
            base.update({
                "Compustat_GVKEY": candidate["ccm_gvkey"], "IID": candidate["compustat_iid"],
                "CUSIP": candidate["compustat_cusip"],
            })
            if candidate["mapping_status"] in accepted:
                base["mapping_status"] = "EXACT_APPROVED_IDENTITY"
                base["mapping_method"] = "HISTORICAL_CCM_LIID_EXACT"
                if candidate["mapping_status"] != "MATCHED_CCM_LIID":
                    base["mapping_method"] += "__CUSIP_DIAGNOSTIC_MISMATCH"
            else:
                base["mapping_status"] = "AMBIGUOUS"
                base["mapping_method"] = "HISTORICAL_CCM_EVIDENCE_NOT_APPROVED"
        output.append(base)
    return output


def security_index(path: Path) -> dict[tuple[str, str, str], dict[str, list[dict[str, str]]]]:
    output: dict[tuple[str, str, str], dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    for row in csv_rows(path):
        if row.get("datadate") in PATH_DATES:
            key = (str(row.get("gvkey", "")).zfill(6), str(row.get("iid", "")).zfill(2), str(row.get("cusip", "")).strip())
            output[key][str(row["datadate"])].append(row)
    return output


def security_return_rows(
    universe: Sequence[dict[str, Any]], identities: Sequence[dict[str, str]], source_rows: dict[tuple[str, str, str], dict[str, list[dict[str, str]]]],
    authorized: set[tuple[str, str, str]], ciena: set[tuple[str, str, str]],
) -> tuple[list[dict[str, Any]], set[tuple[str, str, str]]]:
    member_by_permno = {str(row["PERMNO"]): row for row in universe}
    output: list[dict[str, Any]] = []
    recovery_used: set[tuple[str, str, str]] = set()
    for identity in identities:
        member = member_by_permno[identity["PERMNO"]]
        key = (identity["Compustat_GVKEY"], identity["IID"], identity["CUSIP"])
        base = {
            "signal_date": SIGNAL_DATE, "execution_date": EXECUTION_DATE,
            "next_execution_date": NEXT_EXECUTION_DATE, "PERMNO": identity["PERMNO"],
            "PERMCO": identity["PERMCO"], "historical_GVKEY": identity["historical_GVKEY"],
            "historical_gics_sector": identity["historical_gics_sector"],
            "eligible_universe_flag": int(identity["eligible_universe_flag"]),
            "canonical_security_id": f"CRSP_PERMNO:{identity['PERMNO']}",
            "compustat_gvkey": identity["Compustat_GVKEY"], "iid": identity["IID"],
            "cusip": identity["CUSIP"], "weekly_total_return": None,
            "path_complete": 0, "return_available": 0, "trading_day_count": 0,
            "return_source": "", "mapping_status": identity["mapping_status"],
            "unavailable_reason": "", "data_lineage": LINEAGE,
        }
        if identity["mapping_status"] not in {"EXACT_APPROVED_IDENTITY", "APPROVED_TEMPORAL_IDENTITY"}:
            base.update({"return_source": "UNAVAILABLE", "unavailable_reason": f"IDENTITY_{identity['mapping_status']}"})
            output.append(base)
            continue
        by_date = source_rows.get(key, {})
        if any(len(by_date.get(day, [])) != 1 for day in PATH_DATES):
            missing = [day for day in PATH_DATES if len(by_date.get(day, [])) == 0]
            duplicate = [day for day in PATH_DATES if len(by_date.get(day, [])) > 1]
            reason = "MISSING_REQUIRED_TRADING_DATE" if missing else "DUPLICATE_SOURCE_SECURITY_DATE"
            base.update({"return_source": "UNAVAILABLE", "unavailable_reason": f"{reason}:{'|'.join(missing or duplicate)}"})
            output.append(base)
            continue
        path = [by_date[day][0] for day in PATH_DATES]
        base.update({"path_complete": 1, "trading_day_count": len(HOLDING_DATES)})
        daily, methods = [], []
        for prior, current in zip(path, path[1:]):
            result, available, method = extension._return_for(current, prior, authorized, ciena, False)
            if not available or result is None:
                base.update({"return_source": method, "unavailable_reason": method})
                break
            daily.append(result)
            methods.append(method)
            if method == extension.RETURN_RECOVERY:
                recovery_used.add(key)
        else:
            weekly = compound(daily)
            if weekly is None:
                base.update({"return_source": "UNAVAILABLE_INVALID_DAILY_RETURN", "unavailable_reason": "NONFINITE_OR_INVALID_DAILY_RETURN"})
            else:
                base.update({
                    "weekly_total_return": weekly, "return_available": 1,
                    "return_source": extension.RETURN_RECOVERY if extension.RETURN_RECOVERY in methods else extension.RETURN_DIRECT,
                    "unavailable_reason": "AVAILABLE",
                })
        output.append(base)
    return sorted(output, key=lambda row: int(row["PERMNO"])), recovery_used


def benchmark_row(path: Path) -> tuple[dict[str, Any], bool]:
    values: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in csv_rows(path):
        if row.get("datadate") in PATH_DATES and row.get("gvkeyx") == BENCHMARK_GVKEYX and row.get("tic") == BENCHMARK_TIC:
            values[str(row["datadate"])].append(row)
    base = {
        "signal_date": SIGNAL_DATE, "execution_date": EXECUTION_DATE, "next_execution_date": NEXT_EXECUTION_DATE,
        "benchmark_id": BENCHMARK_ID, "benchmark_name": BENCHMARK_NAME, "benchmark_total_return": None,
        "path_complete": 0, "return_available": 0, "trading_day_count": 0,
        "return_method": "COMPUSTAT_PRCCDDIV_TOTAL_RETURN", "unavailable_reason": "", "data_lineage": LINEAGE,
    }
    if any(len(values.get(day, [])) != 1 for day in PATH_DATES):
        base["unavailable_reason"] = "MISSING_OR_DUPLICATE_BENCHMARK_TRADING_DATE"
        return base, False
    path_rows = [values[day][0] for day in PATH_DATES]
    if any(row.get("conm") != BENCHMARK_NAME for row in path_rows):
        raise RuntimeError("BENCHMARK_NAME_IDENTITY_MISMATCH")
    daily = [extension.benchmark_return(current, prior) for prior, current in zip(path_rows, path_rows[1:])]
    if any(value is None for value in daily):
        base.update({"path_complete": 1, "trading_day_count": len(HOLDING_DATES), "unavailable_reason": "INVALID_PRCCDDIV_PATH"})
        return base, False
    total = compound([float(value) for value in daily if value is not None])
    if total is None:
        base.update({"path_complete": 1, "trading_day_count": len(HOLDING_DATES), "unavailable_reason": "INVALID_PRCCDDIV_RETURN"})
        return base, False
    base.update({"benchmark_total_return": total, "path_complete": 1, "return_available": 1, "trading_day_count": len(HOLDING_DATES), "unavailable_reason": "AVAILABLE"})
    return base, True


def risk_free_row(path: Path) -> tuple[dict[str, Any], bool]:
    values = {str(row.get("date")): row for row in csv_rows(path) if row.get("date") in HOLDING_DATES}
    base = {
        "signal_date": SIGNAL_DATE, "execution_date": EXECUTION_DATE, "next_execution_date": NEXT_EXECUTION_DATE,
        "risk_free_weekly_return": None, "path_complete": 0, "return_available": 0,
        "trading_day_count": 0, "return_method": "FAMA_FRENCH_DAILY_RF_DECIMAL_COMPOUND",
        "source_field": "rf", "unit": "DECIMAL_RETURN", "unavailable_reason": "", "data_lineage": LINEAGE,
    }
    if set(values) != set(HOLDING_DATES):
        base["unavailable_reason"] = "MISSING_REQUIRED_RISK_FREE_TRADING_DATE"
        return base, False
    daily = [number(values[day].get("rf")) for day in HOLDING_DATES]
    if any(value is None for value in daily):
        base.update({"path_complete": 1, "trading_day_count": len(HOLDING_DATES), "unavailable_reason": "INVALID_RISK_FREE_VALUE"})
        return base, False
    total = compound([float(value) for value in daily if value is not None])
    if total is None:
        base.update({"path_complete": 1, "trading_day_count": len(HOLDING_DATES), "unavailable_reason": "INVALID_RISK_FREE_RETURN"})
        return base, False
    base.update({"risk_free_weekly_return": total, "path_complete": 1, "return_available": 1, "trading_day_count": len(HOLDING_DATES), "unavailable_reason": "AVAILABLE"})
    return base, True


def acceptance_rows(
    formal_gap: dict[str, Any], universe: Sequence[dict[str, Any]], identities: Sequence[dict[str, str]], security: Sequence[dict[str, Any]],
    benchmark_ok: bool, rf_ok: bool, recovery_used: set[tuple[str, str, str]], authorized: set[tuple[str, str, str]], ciena: set[tuple[str, str, str]],
    sources_unchanged: bool,
) -> list[dict[str, str]]:
    counts = Counter(row["mapping_status"] for row in identities)
    available = sum(int(row["return_available"]) for row in security)
    unavailable = len(security) - available
    required = set(HOLDING_DATES)
    statuses = {"EXACT_APPROVED_IDENTITY", "APPROVED_TEMPORAL_IDENTITY", "AMBIGUOUS", "UNMAPPED"}
    no_silent_fill = all(
        (int(row["return_available"]) == 1 and row["weekly_total_return"] is not None and row["unavailable_reason"] == "AVAILABLE")
        or (int(row["return_available"]) == 0 and row["weekly_total_return"] is None and bool(row["unavailable_reason"]))
        for row in security
    )
    no_strategy = not ((set(SECURITY_SCHEMA.names) | set(BENCHMARK_SCHEMA.names) | set(RF_SCHEMA.names)) & FORBIDDEN_COLUMNS)
    rows: list[tuple[str, bool, str, str]] = [
        ("CROSS_BOUNDARY_CALENDAR_EXACT", formal_gap["execution_date"] == EXECUTION_DATE and formal_gap["next_execution_date"] == "", "frozen formal calendar + bridge calendar", "Formal cutoff gap remains frozen; independent bridge completes the exact next execution."),
        ("SECURITY_REQUEST_COUNT", len(universe) == len(security) and len(universe) > 0, "historical eligible universe", f"requested={len(universe)}"),
        ("IDENTITY_MAPPING_COVERAGE", sum(counts.values()) == len(universe) and set(counts) <= statuses, "identity_mapping_audit.csv", f"exact={counts['EXACT_APPROVED_IDENTITY']}; temporal={counts['APPROVED_TEMPORAL_IDENTITY']}; ambiguous={counts['AMBIGUOUS']}; unmapped={counts['UNMAPPED']}"),
        ("NO_TICKER_ONLY_MAPPING", all("TICKER" not in row["mapping_method"] for row in identities), "historical CCM LIID audit", "No mapping was inferred from ticker."),
        ("NO_CURRENT_ID_BACKFILL", all(row["mapping_method"].startswith("HISTORICAL_CCM") or row["mapping_status"] in {"AMBIGUOUS", "UNMAPPED"} for row in identities), "security_identity_bridge_2025.csv dated 2025-12-29", "Only historical CCM evidence is used."),
        ("HOLDING_DATES_EXACT", required == set(HOLDING_DATES), "bridge constant", "2026-01-01 holiday and 2026-01-03/04 weekend are intentionally absent."),
        ("EXECUTION_DAY_EXCLUDED", EXECUTION_DATE not in required, "bridge return construction", "Execution-day return is excluded; its level is the opening denominator."),
        ("NEXT_EXECUTION_DAY_INCLUDED", NEXT_EXECUTION_DATE in required, "bridge return construction", "2026-01-05 is included."),
        ("SECURITY_RETURN_FORMULA", all(row["return_source"] in {extension.RETURN_DIRECT, extension.RETURN_RECOVERY, extension.RETURN_UNVALIDATED, "UNAVAILABLE"} for row in security), "Compustat PRCCD/AJEXDI/TRFD path", f"available={available}; unavailable={unavailable}"),
        ("NO_SILENT_RETURN_FILL", no_silent_fill, "security bridge records", "Every requested security has either a valid return or an explicit unavailable reason."),
        ("TRFD_RECOVERY_POLICY_UNCHANGED", recovery_used <= (authorized | ciena), "frozen authorized structural-null sets", f"recovery identities used={len(recovery_used)}; authorized universe={len(authorized | ciena)}"),
        ("BENCHMARK_IDENTITY", benchmark_ok, "Compustat GVKEYX 000003 / I0003", "Compustat S&P 500 Comp-Ltd only; no SPY or constituent aggregation."),
        ("BENCHMARK_FORMULA", benchmark_ok, "PRCCDDIV path", "Compounded PRCCDDIV_t / PRCCDDIV_(t-1) - 1."),
        ("RISK_FREE_SEMANTICS", rf_ok, "Fama-French daily rf", "RF is already decimal and is compounded without /100."),
        ("NO_DUPLICATE_KEYS", len({(row["signal_date"], row["PERMNO"]) for row in security}) == len(security), "security bridge natural key", "Natural key is (signal_date, PERMNO)."),
        ("NO_STRATEGY_OUTPUTS", no_strategy, "bridge schemas", "No weights, holdings, portfolio return, NAV, FMOM, IC, or strategy result."),
        ("SOURCE_IMMUTABILITY", sources_unchanged, "pre/post SHA-256 snapshot", "Frozen historical and raw source inputs are byte-identical before and after build."),
    ]
    return [{"check": check, "status": "PASS" if passed else "FAIL", "blocking": str(not passed).lower(), "evidence": evidence, "notes": notes} for check, passed, evidence, notes in rows]


def source_snapshot(root: Path, source_root: Path) -> dict[str, str]:
    files = [root / relative for relative in (FORMAL_CALENDAR, FORMAL_SECURITY_RETURNS, FORMAL_BENCHMARK_RETURNS, FORMAL_UNIVERSE, RECENT_FACTOR_LIBRARY)]
    files += [source_root / relative for relative in (SECURITY_SOURCE, BENCHMARK_SOURCE, RF_SOURCE, IDENTITY_AUDIT, extension.TRFD_CLASSIFICATION, extension.RESIDUAL_DECISIONS)]
    return {portable_path(root, path): sha256(path) for path in files}


def package_readme(source_paths: dict[str, str], counts: Counter[str], security: Sequence[dict[str, Any]], benchmark: dict[str, Any], risk_free: dict[str, Any]) -> str:
    return f"""# 2025–2026 Cross-Boundary Execution Bridge

## Scope

This is a standalone controlled data bridge for the sole frozen Formal cutoff
gap: signal date `{SIGNAL_DATE}`, execution date `{EXECUTION_DATE}`, next
signal date `{NEXT_SIGNAL_DATE}`, and next execution date
`{NEXT_EXECUTION_DATE}`. It does not alter Formal 2006–2025 handoff files,
factor definitions, Gate B/C, model selection, holdings, portfolio returns,
or strategy performance.

The holding period is `({EXECUTION_DATE}, {NEXT_EXECUTION_DATE}]`: exactly
`{', '.join(HOLDING_DATES)}`. The 2026-01-01 market holiday and 2026-01-03/04
weekend are not missing observations.

## Contents

- `data/weekly_execution_calendar_bridge.parquet`: one completed bridge calendar record.
- `data/security_weekly_execution_returns_bridge.parquet`: one record per
  actual 2025-12-26 eligible historical security, including explicit
  unavailable reasons where necessary.
- `data/benchmark_weekly_execution_return_bridge.parquet`: Compustat S&P 500
  Comp-Ltd (`GVKEYX=000003`, `I0003`) bridge return from `PRCCDDIV`.
- `data/risk_free_weekly_return_bridge.parquet`: Fama–French decimal RF bridge return.
- `identity_mapping_audit.csv`: historical CCM LIID/CUSIP mapping evidence.
- `acceptance.csv`: artifact-derived bridge acceptance checks.

## Identity and return rules

Historical universe membership is sourced exclusively from the Formal
2025-12-26 eligible universe. Mapping uses only the historical CCM identity
audit dated 2025-12-29, never ticker-only inference, current-identifier
backfill, arbitrary IID selection, or fuzzy/company-name matching.

Security returns use `(PRCCD / AJEXDI * TRFD)_t / (PRCCD / AJEXDI * TRFD)_(t-1) - 1`.
Only the frozen `AUTHORIZED_CONSTANT_TRFD_RECOVERY` set can use its existing
adjusted-price recovery; all other structural-null cases remain unavailable.

## Result

- Requested securities: {len(security)}
- Exact approved mappings: {counts['EXACT_APPROVED_IDENTITY']}
- Approved temporal mappings: {counts['APPROVED_TEMPORAL_IDENTITY']}
- Ambiguous mappings: {counts['AMBIGUOUS']}
- Unmapped securities: {counts['UNMAPPED']}
- Available security returns: {sum(int(row['return_available']) for row in security)}
- Benchmark available: {benchmark['return_available']}
- Risk-free available: {risk_free['return_available']}

## Source provenance

All inputs are read-only. Source paths are recorded project-relatively when
possible; no local absolute user path is stored in this package.

{chr(10).join(f'- `{path}`: `{digest}`' for path, digest in sorted(source_paths.items()))}
"""


def build(root: Path, source_root: Path | str) -> dict[str, Any]:
    root = root.resolve()
    sources = source_root_path(root, source_root)
    required = (SECURITY_SOURCE, BENCHMARK_SOURCE, RF_SOURCE, IDENTITY_AUDIT, extension.TRFD_CLASSIFICATION, extension.RESIDUAL_DECISIONS)
    for relative in required:
        source_path(root, source_root, relative)
    before = source_snapshot(root, sources)
    formal_gap, _recent_pair = frozen_gap(root)
    universe = requested_universe(root)
    identities = build_identity_rows(universe, mapping_audit(sources))
    security_source = security_index(sources / SECURITY_SOURCE)
    authorized, ciena, _unvalidated = extension._load_authorized_identity_sets(sources)
    security, recovery_used = security_return_rows(universe, identities, security_source, authorized, ciena)
    benchmark, benchmark_ok = benchmark_row(sources / BENCHMARK_SOURCE)
    risk_free, rf_ok = risk_free_row(sources / RF_SOURCE)
    after = source_snapshot(root, sources)
    package = root / PACKAGE
    calendar = [{
        "signal_date": SIGNAL_DATE, "execution_date": EXECUTION_DATE,
        "next_signal_date": NEXT_SIGNAL_DATE, "next_execution_date": NEXT_EXECUTION_DATE,
        "return_period_complete": 1, "calendar_status": "CROSS_BOUNDARY_BRIDGE_COMPLETE", "data_lineage": LINEAGE,
    }]
    calendar_count, calendar_hash = write_parquet(package / "data/weekly_execution_calendar_bridge.parquet", CALENDAR_SCHEMA, calendar)
    security_count, security_hash = write_parquet(package / "data/security_weekly_execution_returns_bridge.parquet", SECURITY_SCHEMA, security)
    benchmark_count, benchmark_hash = write_parquet(package / "data/benchmark_weekly_execution_return_bridge.parquet", BENCHMARK_SCHEMA, [benchmark])
    rf_count, rf_hash = write_parquet(package / "data/risk_free_weekly_return_bridge.parquet", RF_SCHEMA, [risk_free])
    write_csv(package / "identity_mapping_audit.csv", IDENTITY_FIELDS, identities)
    # The delivery validator is the sole owner of delivery metadata.  Calling
    # it after mechanically materializing the four bridge payloads prevents a
    # rebuild from restoring the superseded 17-check acceptance schema.
    delivery_result = delivery.write_delivery_metadata(root)
    acceptance = delivery_result["acceptance"]
    blockers = delivery_result["blockers"]
    return {
        "blockers": blockers, "acceptance_checks": len(acceptance), "calendar_rows": calendar_count,
        "security_rows": security_count, "security_available": sum(int(row["return_available"]) for row in security),
        "security_unavailable": len(security) - sum(int(row["return_available"]) for row in security),
        "mapping_counts": dict(Counter(row["mapping_status"] for row in identities)),
        "calendar_sha256": calendar_hash, "security_sha256": security_hash,
        "benchmark_sha256": benchmark_hash, "risk_free_sha256": rf_hash,
        "benchmark_return": benchmark["benchmark_total_return"], "risk_free_return": risk_free["risk_free_weekly_return"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=project_root())
    parser.add_argument("--source-root", type=Path, default=Path("."), help="Root containing WRDS extracts and bridge audits; relative paths resolve from --root.")
    args = parser.parse_args(argv)
    result = build(args.root, args.source_root)
    print(result)
    return 0 if result["blockers"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
