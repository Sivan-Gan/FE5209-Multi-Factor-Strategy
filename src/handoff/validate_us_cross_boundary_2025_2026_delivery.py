"""Validate and prepare the existing 2025--2026 bridge for private delivery.

This module is intentionally read-only with respect to all bridge Parquet
payloads and all upstream inputs.  Its optional writer updates only bridge
delivery metadata: README, acceptance, and manifest.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence

import pyarrow.parquet as pq


PACKAGE = Path("handoff/us_cross_boundary_2025_2026")
LINEAGE = "CROSS_BOUNDARY_2025_2026_BRIDGE"
REDISTRIBUTION = "DERIVED_DATA_PRIVATE_TEAM_GITHUB_APPROVED"

CALENDAR = PACKAGE / "data/weekly_execution_calendar_bridge.parquet"
SECURITY = PACKAGE / "data/security_weekly_execution_returns_bridge.parquet"
BENCHMARK = PACKAGE / "data/benchmark_weekly_execution_return_bridge.parquet"
RISK_FREE = PACKAGE / "data/risk_free_weekly_return_bridge.parquet"
IDENTITY_AUDIT = PACKAGE / "identity_mapping_audit.csv"
ACCEPTANCE = PACKAGE / "acceptance.csv"
README = PACKAGE / "README.md"
MANIFEST = PACKAGE / "handoff_manifest.csv"

EXPECTED_HASHES = {
    CALENDAR: "58845e42a461ad4c279af20a6bc1c14fd6ac139c000db63d8f314338bac9ba3b",
    SECURITY: "7255f2b69938f1c725e7c14e916378881cf73584f03703cc9379faa44953d0b7",
    BENCHMARK: "256ccf221d1caf64b903e7518459824699525644e6bf59e06e847304beb56289",
    RISK_FREE: "582833b7c398525eb659ab2b2ac944a4fcb877bc6cfac4bb1366099b070eafb0",
}
FORMAL_INPUT_HASHES = {
    Path("handoff/us_downstream/data/weekly_execution_calendar.parquet"): "689fb5faa018727b294634e5f54f416124c4f1b99bd7d32bf9c29201adab4eea",
    Path("handoff/us_downstream/data/security_weekly_execution_returns.parquet"): "43bdc9bd3e70f1de72f89d9d7c5dd9a55acab0dc820e50d9cfac5bd1fcc16fae",
    Path("handoff/us_downstream/data/benchmark_weekly_execution_returns.parquet"): "6eac17bbd4d24c42f73d59fc4da0a20b9730adc4c2d6fe360a7ed7984a8a0552",
    Path("handoff/us_downstream/data/risk_free_weekly.parquet"): "c0ccd51d4ed15b536e798316f576abf67fbc1540b6b35bb7edf51da9ea53e38f",
    Path("handoff/final_factor/data/universe_weekly.parquet"): "a8c7a0ceb7422a6425d0163684a6abd4ee0251f15a54ba411c67afefa591c157",
}
RECENT_INPUT_HASHES = {
    Path("handoff/us_recent_extension_2026/data/factor_library_2026.parquet"): "d208dba173994ee4f1d26272c5dcf0ac9a731b070f6e61160049000e55f57ca2",
    Path("handoff/us_recent_extension_2026/data/market_2026.parquet"): "3a8b4a90970a42b12edf8d42e90afaa9d4bf30c49605219344b4f361fb2f80c4",
    Path("handoff/us_recent_extension_2026/data/benchmark_daily_returns_2026.parquet"): "a302378ba66371e709b3c963c22853888f138e9a532e00d7dc9be4654a4ad0ee",
}
SOURCE_FILE_HASHES = {
    Path("原版/outputs/audit/wrds_2026_bridge/residual_structural_null_recovery_decision.csv"): "a73e2bc4468bca67e140f9356bc3e7fb19052876af46d28854d18f4647b75962",
    Path("原版/outputs/audit/wrds_2026_bridge/security_identity_bridge_2025.csv"): "cda640312c0682a15c0044aa0c6b2c48863395454d380423a9317de2691b4060",
    Path("原版/outputs/audit/wrds_2026_bridge/trfd_missing_classification_2025_2026.csv"): "d709bf42d7e232bd380d623a1c21a1d9ff5ee8be708619e9bb077cedb3b8d631",
    Path("原版/wrds_raw/US_WRDS_2026-10-09/fama_french/Fama_French_5Factors_Momentum_RF_Daily_FullHistory_to_2026-08-31.csv.gz"): "deb7c018aae00c71dd6ce01d1a0ee67ae10de383ff43ba7ce0ab006de6849cd4",
    Path("原版/wrds_raw/US_WRDS_2026-10-13/compustat_recent_extension/index_daily/compustat_sp500_index_daily_20250101_20261009_raw.csv.gz"): "9a3f1696de7ade505f013a664b60090cf6224cda6f771247364f5493da646d59",
    Path("原版/wrds_raw/US_WRDS_2026-10-13/compustat_recent_extension/security_daily/compustat_security_daily_sp500_bridge_20250101_20261011_raw.csv.gz"): "6f3482a72bcf985c1ccdde92eeb8e00d65ec668d102ae012aef6c71a98dffb1b",
}

FORBIDDEN_COLUMNS = frozenset({
    "weight", "weights", "holding", "holdings", "portfolio_return", "strategy_return",
    "nav", "cagr", "sharpe", "sortino", "drawdown", "alpha", "tracking_error",
    "fmom", "ic", "factor_return", "transaction_cost",
})
ACCEPTANCE_FIELDS = ("check", "status", "blocking", "evidence", "notes")
MANIFEST_FIELDS = (
    "relative_path", "artifact_type", "rows", "columns_schema", "file_size_bytes", "sha256",
    "lineage", "source_provenance", "redistribution_status",
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="raise", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def hashes_match(root: Path, expected: dict[Path, str]) -> bool:
    return all((root / path).is_file() and sha256(root / path) == digest for path, digest in expected.items())


def _finite_equal(actual: Any, expected: float) -> bool:
    return isinstance(actual, (int, float)) and math.isfinite(float(actual)) and float(actual) == expected


def validate(root: Path) -> dict[str, Any]:
    root = root.resolve()
    paths = [CALENDAR, SECURITY, BENCHMARK, RISK_FREE, IDENTITY_AUDIT]
    for relative in paths:
        if not (root / relative).is_file():
            raise FileNotFoundError(f"BRIDGE_DELIVERY_ARTIFACT_MISSING:{relative}")
    calendar = pq.read_table(root / CALENDAR).to_pylist()
    security = pq.read_table(root / SECURITY).to_pylist()
    benchmark = pq.read_table(root / BENCHMARK).to_pylist()
    risk_free = pq.read_table(root / RISK_FREE).to_pylist()
    identity = csv_rows(root / IDENTITY_AUDIT)
    expected_calendar = {
        "signal_date": "2025-12-26", "execution_date": "2025-12-29",
        "next_signal_date": "2026-01-02", "next_execution_date": "2026-01-05",
        "return_period_complete": 1, "calendar_status": "CROSS_BOUNDARY_BRIDGE_COMPLETE",
        "data_lineage": LINEAGE,
    }
    calendar_exact = len(calendar) == 1 and all(calendar[0].get(key) == value for key, value in expected_calendar.items())
    interval_exact = calendar_exact and calendar[0]["execution_date"] == "2025-12-29" and calendar[0]["next_execution_date"] == "2026-01-05"
    identity_counts = Counter(row.get("mapping_status", "") for row in identity)
    all_security_dates = all(
        row["signal_date"] == "2025-12-26" and row["execution_date"] == "2025-12-29" and row["next_execution_date"] == "2026-01-05"
        for row in security
    )
    security_available = all(
        int(row["return_available"]) == 1 and int(row["path_complete"]) == 1 and int(row["trading_day_count"]) == 4
        and row["weekly_total_return"] is not None and row["unavailable_reason"] == "AVAILABLE"
        for row in security
    )
    security_unique = len(security) == len({(row["signal_date"], row["PERMNO"]) for row in security})
    identity_exact = (
        len(identity) == 503 and identity_counts["EXACT_APPROVED_IDENTITY"] == 503
        and not (set(identity_counts) - {"EXACT_APPROVED_IDENTITY"})
    )
    benchmark_exact = (
        len(benchmark) == 1 and benchmark[0]["signal_date"] == "2025-12-26"
        and benchmark[0]["execution_date"] == "2025-12-29" and benchmark[0]["next_execution_date"] == "2026-01-05"
        and benchmark[0]["benchmark_id"] == "COMPUSTAT_SP500_COMP_LTD_GVKEYX_000003_I0003"
        and benchmark[0]["benchmark_name"] == "S&P 500 Comp-Ltd" and benchmark[0]["return_method"] == "COMPUSTAT_PRCCDDIV_TOTAL_RETURN"
        and benchmark[0]["data_lineage"] == LINEAGE and int(benchmark[0]["trading_day_count"]) == 4
    )
    benchmark_return = len(benchmark) == 1 and _finite_equal(benchmark[0]["benchmark_total_return"], -0.0002199650711303125)
    rf_return = (
        len(risk_free) == 1 and risk_free[0]["signal_date"] == "2025-12-26"
        and risk_free[0]["execution_date"] == "2025-12-29" and risk_free[0]["next_execution_date"] == "2026-01-05"
        and _finite_equal(risk_free[0]["risk_free_weekly_return"], 0.0008002400320015202)
        and risk_free[0]["return_method"] == "FAMA_FRENCH_DAILY_RF_DECIMAL_COMPOUND"
        and risk_free[0]["unit"] == "DECIMAL_RETURN" and int(risk_free[0]["trading_day_count"]) == 4
    )
    schemas = set(pq.ParquetFile(root / SECURITY).schema_arrow.names)
    schemas |= set(pq.ParquetFile(root / BENCHMARK).schema_arrow.names)
    schemas |= set(pq.ParquetFile(root / RISK_FREE).schema_arrow.names)
    checks: list[tuple[str, bool, str, str]] = [
        ("BRIDGE_CALENDAR_EXACT", calendar_exact, str(CALENDAR), "One exact completed bridge calendar row."),
        ("CALENDAR_HASH_MATCH", sha256(root / CALENDAR) == EXPECTED_HASHES[CALENDAR], str(CALENDAR), EXPECTED_HASHES[CALENDAR]),
        ("BRIDGE_INTERVAL_EXACT", interval_exact, "(2025-12-29, 2026-01-05]", "The bridge holds only the frozen cross-boundary interval."),
        ("EXECUTION_DAY_EXCLUDED", interval_exact, "bridge calendar contract", "2025-12-29 is the execution boundary, not a holding-date return."),
        ("NEXT_EXECUTION_DAY_INCLUDED", interval_exact, "bridge calendar contract", "2026-01-05 is the included endpoint."),
        ("SECURITY_ROWS_503", len(security) == 503 and all_security_dates, str(SECURITY), f"rows={len(security)}"),
        ("IDENTITY_EXACT_503", identity_exact, str(IDENTITY_AUDIT), f"exact={identity_counts['EXACT_APPROVED_IDENTITY']}"),
        ("IDENTITY_TEMPORAL_ZERO", identity_counts["APPROVED_TEMPORAL_IDENTITY"] == 0, str(IDENTITY_AUDIT), f"temporal={identity_counts['APPROVED_TEMPORAL_IDENTITY']}"),
        ("IDENTITY_AMBIGUOUS_ZERO", identity_counts["AMBIGUOUS"] == 0, str(IDENTITY_AUDIT), f"ambiguous={identity_counts['AMBIGUOUS']}"),
        ("IDENTITY_UNMAPPED_ZERO", identity_counts["UNMAPPED"] == 0, str(IDENTITY_AUDIT), f"unmapped={identity_counts['UNMAPPED']}"),
        ("SECURITY_RETURNS_AVAILABLE_503", len(security) == 503 and security_available, str(SECURITY), f"available={sum(int(row['return_available']) for row in security)}"),
        ("SECURITY_NATURAL_KEY_UNIQUE", security_unique, "(signal_date, PERMNO)", f"unique={len({(row['signal_date'], row['PERMNO']) for row in security})}"),
        ("SECURITY_HASH_MATCH", sha256(root / SECURITY) == EXPECTED_HASHES[SECURITY], str(SECURITY), EXPECTED_HASHES[SECURITY]),
        ("BENCHMARK_IDENTITY", benchmark_exact, str(BENCHMARK), "Compustat S&P 500 Comp-Ltd only; no proxy or aggregation."),
        ("BENCHMARK_RETURN_MATCH", benchmark_return, str(BENCHMARK), "Frozen bridge total return."),
        ("BENCHMARK_HASH_MATCH", sha256(root / BENCHMARK) == EXPECTED_HASHES[BENCHMARK], str(BENCHMARK), EXPECTED_HASHES[BENCHMARK]),
        ("RF_RETURN_MATCH", rf_return, str(RISK_FREE), "Frozen decimal bridge risk-free return."),
        ("RF_HASH_MATCH", sha256(root / RISK_FREE) == EXPECTED_HASHES[RISK_FREE], str(RISK_FREE), EXPECTED_HASHES[RISK_FREE]),
        ("FORMAL_HISTORICAL_INPUTS_UNCHANGED", hashes_match(root, FORMAL_INPUT_HASHES), "frozen Formal input SHA-256", f"files={len(FORMAL_INPUT_HASHES)}"),
        ("RECENT_EXTENSION_INPUTS_UNCHANGED", hashes_match(root, RECENT_INPUT_HASHES), "2026 handoff SHA-256", f"files={len(RECENT_INPUT_HASHES)}"),
        ("SOURCE_FILES_UNCHANGED", hashes_match(root, SOURCE_FILE_HASHES), "raw/audit source SHA-256", f"files={len(SOURCE_FILE_HASHES)}"),
        ("NO_STRATEGY_OUTPUTS", not bool(schemas & FORBIDDEN_COLUMNS), "bridge output schemas", "No allocation, performance, or model-output fields."),
    ]
    acceptance = [
        {"check": name, "status": "PASS" if passed else "FAIL", "blocking": str(not passed).lower(), "evidence": evidence, "notes": notes}
        for name, passed, evidence, notes in checks
    ]
    return {
        "acceptance": acceptance, "blockers": sum(row["status"] != "PASS" for row in acceptance),
        "calendar": calendar, "security": security, "benchmark": benchmark, "risk_free": risk_free,
        "identity_counts": dict(identity_counts),
    }


def readme(result: dict[str, Any]) -> str:
    return f"""# 2025–2026 Cross-Boundary Execution Bridge

## Sole purpose

This package supplies the sole completed weekly execution interval between the
frozen Formal 2006–2025 handoff and the governed 2026 Recent Extension. It
does not redefine or replace Formal history.

- Historical Formal last signal: `2025-12-26`
- Bridge execution interval: `(2025-12-29, 2026-01-05]`
- 2026 successor signal: `2026-01-02`

The holding-date return path contains exactly `2025-12-30`, `2025-12-31`,
`2026-01-02`, and `2026-01-05`. The 2025-12-29 execution-date return is
excluded. The 2026-01-01 market holiday and 2026-01-03/04 weekend are not
missing observations.

## Downstream concatenation order

1. `handoff/us_downstream`
2. `handoff/us_cross_boundary_2025_2026`
3. `handoff/us_recent_extension_2026`

## Delivered artifacts

- `data/weekly_execution_calendar_bridge.parquet`: one completed bridge calendar row.
- `data/security_weekly_execution_returns_bridge.parquet`: 503 approved
  historical-identity security rows.
- `data/benchmark_weekly_execution_return_bridge.parquet`: Compustat S&P 500
  Comp-Ltd (`GVKEYX=000003`, `I0003`) total return from `PRCCDDIV`.
- `data/risk_free_weekly_return_bridge.parquet`: Fama–French decimal RF.
- `identity_mapping_audit.csv`: approved historical CCM identity evidence.
- `acceptance.csv`: delivery validation checks.
- `handoff_manifest.csv`: hashes, schemas, provenance, and redistribution boundary.

## Data rules

Historical membership is taken only from the Formal 2025-12-26 eligible
universe. Identity uses approved historical CCM evidence, never ticker-only
inference, current-identifier backfill, arbitrary IID selection, or
name-based matching. Security total returns preserve the frozen
`PRCCD / AJEXDI * TRFD` method and its pre-authorized structural-null
recovery only. The benchmark is not a proxy or constituent aggregation. RF
is a decimal daily series compounded over the same four holding dates with no
`/100` conversion.

## Delivery boundary

Classification: **DERIVED_DATA_PRIVATE_TEAM_GITHUB_APPROVED**. These derived
artifacts are authorized for private team GitHub/LFS delivery only; this is
not authorization for public redistribution. No raw/vendor file, SQLite
database, or unrelated 2006–2026 duplicate interface is included.

## Validation summary

- Requested and exact approved identities: {len(result['security'])}
- Ambiguous identities: {result['identity_counts'].get('AMBIGUOUS', 0)}
- Unmapped identities: {result['identity_counts'].get('UNMAPPED', 0)}
- Available security returns: {sum(int(row['return_available']) for row in result['security'])}
- Benchmark bridge return: `{result['benchmark'][0]['benchmark_total_return']}`
- Risk-free bridge return: `{result['risk_free'][0]['risk_free_weekly_return']}`
- Acceptance: `{len(result['acceptance']) - result['blockers']}/{len(result['acceptance'])}` PASS
"""


def manifest_rows(root: Path) -> list[dict[str, str]]:
    entries = [
        (CALENDAR, "parquet", "bridge calendar", "Frozen Formal cutoff calendar plus the governed 2026 successor signal."),
        (SECURITY, "parquet", "bridge security returns", "Formal eligible universe, approved historical CCM evidence, and governed Compustat return path."),
        (BENCHMARK, "parquet", "bridge benchmark return", "Compustat S&P 500 Comp-Ltd GVKEYX=000003/I0003 PRCCDDIV path."),
        (RISK_FREE, "parquet", "bridge risk-free return", "Frozen Fama–French decimal RF daily path."),
        (IDENTITY_AUDIT, "csv", "identity mapping audit", "Historical CCM LIID/CUSIP mapping evidence as of 2025-12-29."),
        (ACCEPTANCE, "csv", "delivery acceptance", "Read-only delivery validation of bridge and upstream hashes."),
        (README, "markdown", "delivery documentation", "Scope, timing, concatenation order, and redistribution boundary."),
    ]
    output = []
    for relative, kind, _artifact_role, provenance in entries:
        path = root / relative
        rows, columns = "", ""
        if kind == "parquet":
            parquet = pq.ParquetFile(path)
            rows, columns = str(parquet.metadata.num_rows), ";".join(parquet.schema_arrow.names)
        elif kind == "csv":
            contents = csv_rows(path)
            rows = str(len(contents))
            columns = ";".join(contents[0].keys()) if contents else ""
        else:
            columns = "markdown"
        output.append({
            "relative_path": str(relative.relative_to(PACKAGE)), "artifact_type": kind, "rows": rows,
            "columns_schema": columns, "file_size_bytes": str(path.stat().st_size), "sha256": sha256(path),
            "lineage": LINEAGE, "source_provenance": provenance, "redistribution_status": REDISTRIBUTION,
        })
    return output


def write_delivery_metadata(root: Path) -> dict[str, Any]:
    root = root.resolve()
    result = validate(root)
    if result["blockers"]:
        raise RuntimeError("BRIDGE_DELIVERY_VALIDATION_FAILED")
    (root / README).write_text(readme(result), encoding="utf-8")
    write_csv(root / ACCEPTANCE, ACCEPTANCE_FIELDS, result["acceptance"])
    write_csv(root / MANIFEST, MANIFEST_FIELDS, manifest_rows(root))
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=project_root())
    parser.add_argument("--write-metadata", action="store_true", help="Write only README, acceptance, and manifest after validation.")
    args = parser.parse_args(argv)
    result = write_delivery_metadata(args.root) if args.write_metadata else validate(args.root)
    print({"blockers": result["blockers"], "acceptance_checks": len(result["acceptance"])})
    return 0 if result["blockers"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
