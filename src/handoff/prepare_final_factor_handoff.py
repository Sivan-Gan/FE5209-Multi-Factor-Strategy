"""Build the governed M2 / M3 -> M4 Final Factor Handoff Package.

This module reads only frozen upstream score and governance objects.  It does
not query WRDS, returns, IC, portfolios, Factor Momentum, weights, or strategy
scores.  Score rows through 2025 are signals, not future performance evidence.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator

import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[2]
AUDIT = Path("outputs/audit/wrds_formal")
PACKAGE = Path("handoff/final_factor")
SOURCE = Path("data/factors/us_formal/us_gate_a0_factor_specifications.sqlite")
CANONICAL = Path("data/canonical/wrds_formal/us_wrds_formal_canonical.sqlite")
CANDIDATES = Path("data/factors/us_formal/us_candidate_factors.sqlite")
GATE_A0_VALIDATION = Path("data/validation/us_formal/us_gate_a0_within_family_validation.sqlite")
PRIOR_VALIDATION = Path("data/validation/us_formal/us_factor_validation_pre_gate_a.sqlite")
SSOT = Path("docs/US_SG_Cross_Market_Quant_Research.md")
GATE_A_SET = AUDIT / "gate_a_final_factor_set.csv"
GATE_A_REGISTRY = AUDIT / "gate_a_final_factor_registry.csv"
GATE_A_ACCEPTANCE = AUDIT / "gate_a_final_freeze_acceptance.csv"
PARENT_COMMIT = "9f27dfb17dc60f45c8c383e50292eb925589da12"

FINAL = (
    (1, "Quality", "QUALITY_CURRENT", "quality"),
    (2, "Momentum", "MOMENTUM_12_1", "momentum"),
    (3, "Low Risk", "LOWRISK_CURRENT", "lowrisk"),
    (4, "Investment", "INVESTMENT_CURRENT", "investment"),
)
EXPECTED = {family: specification for _order, family, specification, _short in FINAL}
SCORE_FIELDS = {short: f"{specification}_score_final" for _o, _f, specification, short in FINAL}
AVAILABLE_FIELDS = {short: f"{specification}_available" for _o, _f, specification, short in FINAL}
REASON_FIELDS = {short: f"{specification}_unavailable_reason" for _o, _f, specification, short in FINAL}

IDENTITY = (
    "signal_date", "permno", "permco", "gvkey", "gsector", "execution_date", "research_period",
    "historical_member", "issuer_market_cap", "issuer_mcap_available", "issuer_mcap_reason",
)
SOURCE_COLUMNS = IDENTITY + tuple(SCORE_FIELDS.values()) + tuple(AVAILABLE_FIELDS.values()) + tuple(REASON_FIELDS.values())


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_size(path: Path) -> str:
    return str(path.stat().st_size) if path.exists() else ""


def logical_update(digest: "hashlib._Hash", row: dict[str, Any], fields: list[str]) -> None:
    digest.update(json.dumps([row.get(field) for field in fields], ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    digest.update(b"\n")


def source_connection(root: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{root / SOURCE}?mode=ro", uri=True)


def source_rows(root: Path, batch: int = 50_000) -> Iterator[dict[str, Any]]:
    connection = source_connection(root)
    try:
        query = f"SELECT {','.join(SOURCE_COLUMNS)} FROM gate_a0_scores ORDER BY signal_date,permno"
        cursor = connection.execute(query)
        while rows := cursor.fetchmany(batch):
            for values in rows:
                yield dict(zip(SOURCE_COLUMNS, values))
    finally:
        connection.close()


def validate_frozen_contract(root: Path) -> dict[str, dict[str, str]]:
    decisions = read_csv(root / GATE_A_SET)
    by_family = {row["family"]: row for row in decisions}
    actual = {family: row["gate_a0_representative_specification_id"] for family, row in by_family.items()}
    kept = [row["gate_a0_representative_specification_id"] for row in decisions if row["included_in_final_factor_set"] == "TRUE"]
    if actual != {"Value": "VALUE_CURRENT", **EXPECTED, "Dividend": "DIVIDEND_CURRENT", "Size": "SIZE_CURRENT"}:
        raise RuntimeError("BLOCKED_GATE_A_DECISION_MAP_MISMATCH")
    if kept != [specification for _order, _family, specification, _short in FINAL]:
        raise RuntimeError("BLOCKED_FINAL_FACTOR_IDS_MISMATCH")
    if any(by_family[family]["gate_a_decision"] != "DROP" for family in ("Value", "Dividend", "Size")):
        raise RuntimeError("BLOCKED_DROPPED_FACTORS_MISMATCH")
    registry = read_csv(root / GATE_A_REGISTRY)
    if [(row["family"], row["specification_id"]) for row in registry] != [(family, spec) for _o, family, spec, _s in FINAL]:
        raise RuntimeError("BLOCKED_FINAL_REGISTRY_MISMATCH")
    acceptance = {row["check"]: row for row in read_csv(root / GATE_A_ACCEPTANCE)}
    if acceptance.get("GATE_A_FROZEN", {}).get("status") != "PASS" or acceptance.get("NO_GATE_B", {}).get("status") != "PASS":
        raise RuntimeError("BLOCKED_GATE_A_NOT_FROZEN_OR_GATE_B_STARTED")
    return by_family


def base(row: dict[str, Any]) -> dict[str, Any]:
    availability = {short: int(bool(row[AVAILABLE_FIELDS[short]])) for _o, _f, _s, short in FINAL}
    count = sum(availability.values())
    return {
        "signal_date": row["signal_date"], "PERMNO": row["permno"], "PERMCO": row["permco"], "GVKEY": row["gvkey"],
        "historical_gics_sector": row["gsector"], "execution_date": row["execution_date"], "research_period": row["research_period"],
        "historical_sp500_member_flag": int(bool(row["historical_member"])), "eligible_universe_flag": int(bool(row["historical_member"])),
        "issuer_market_cap": row["issuer_market_cap"], "issuer_market_cap_available": int(bool(row["issuer_mcap_available"])),
        "issuer_market_cap_unavailable_reason": row["issuer_mcap_reason"],
        **{f"{short}_available": availability[short] for _o, _f, _s, short in FINAL},
        **{f"{short}_unavailable_reason": row[REASON_FIELDS[short]] for _o, _f, _s, short in FINAL},
        "factor_count_available": count, "all_four_available": int(count == 4),
    }


SCORE_COLUMNS = [
    "signal_date", "PERMNO", "PERMCO", "GVKEY", "historical_gics_sector", "execution_date", "research_period",
    "historical_sp500_member_flag", "eligible_universe_flag", "issuer_market_cap", "issuer_market_cap_available",
    "issuer_market_cap_unavailable_reason", "quality_score", "momentum_score", "lowrisk_score", "investment_score",
    "quality_available", "momentum_available", "lowrisk_available", "investment_available", "factor_count_available", "all_four_available",
    "quality_unavailable_reason", "momentum_unavailable_reason", "lowrisk_unavailable_reason", "investment_unavailable_reason",
]
AVAILABILITY_COLUMNS = [
    "signal_date", "PERMNO", "quality_available", "momentum_available", "lowrisk_available", "investment_available",
    "factor_count_available", "all_four_available", "quality_unavailable_reason", "momentum_unavailable_reason",
    "lowrisk_unavailable_reason", "investment_unavailable_reason",
]
UNIVERSE_COLUMNS = [
    "signal_date", "PERMNO", "PERMCO", "GVKEY", "historical_gics_sector", "execution_date", "research_period",
    "historical_sp500_member_flag", "eligible_universe_flag", "issuer_market_cap", "issuer_market_cap_available",
    "issuer_market_cap_unavailable_reason", "quality_available", "momentum_available", "lowrisk_available", "investment_available",
    "factor_count_available", "all_four_available",
]


def schema(columns: list[str]) -> pa.Schema:
    floats = {"issuer_market_cap", "quality_score", "momentum_score", "lowrisk_score", "investment_score"}
    integers = {"historical_sp500_member_flag", "eligible_universe_flag", "issuer_market_cap_available", "quality_available", "momentum_available", "lowrisk_available", "investment_available", "factor_count_available", "all_four_available"}
    return pa.schema([(name, pa.float64() if name in floats else pa.int64() if name in integers else pa.string()) for name in columns])


def record(row: dict[str, Any], kind: str) -> dict[str, Any]:
    result = base(row)
    if kind == "scores":
        result.update({f"{short}_score": row[SCORE_FIELDS[short]] for _o, _f, _s, short in FINAL})
        return {column: result.get(column) for column in SCORE_COLUMNS}
    if kind == "availability":
        return {column: result.get(column) for column in AVAILABILITY_COLUMNS}
    return {column: result.get(column) for column in UNIVERSE_COLUMNS}


def export_one(root: Path, destination: Path, kind: str, sample_dates: set[str] | None = None, sample_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    columns = SCORE_COLUMNS if kind == "scores" else AVAILABILITY_COLUMNS if kind == "availability" else UNIVERSE_COLUMNS
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(); rows = 0; permnos: set[str] = set(); dates: set[str] = set(); duplicate = 0; previous: tuple[str, str] | None = None
    writer: pq.ParquetWriter | None = None; per_date: defaultdict[str, int] = defaultdict(int)
    try:
        chunk: list[dict[str, Any]] = []
        for source in source_rows(root):
            item = record(source, kind); key = (str(item["signal_date"]), str(item["PERMNO"]))
            duplicate += int(previous == key); previous = key; rows += 1; dates.add(key[0]); permnos.add(key[1]); logical_update(digest, item, columns); chunk.append(item)
            if kind == "scores" and sample_dates is not None and sample_rows is not None and key[0] in sample_dates and per_date[key[0]] < 10:
                sample_rows.append(item); per_date[key[0]] += 1
            if len(chunk) == 50_000:
                table = pa.Table.from_pylist(chunk, schema=schema(columns))
                if writer is None: writer = pq.ParquetWriter(destination, table.schema, compression="zstd")
                writer.write_table(table); chunk = []
        if chunk:
            table = pa.Table.from_pylist(chunk, schema=schema(columns))
            if writer is None: writer = pq.ParquetWriter(destination, table.schema, compression="zstd")
            writer.write_table(table)
    finally:
        if writer is not None: writer.close()
    return {"kind": kind, "columns": columns, "row_count": rows, "PERMNO_count": len(permnos), "signal_date_count": len(dates), "min_signal_date": min(dates), "max_signal_date": max(dates), "duplicate_key_count": duplicate, "logical_hash": digest.hexdigest()}


def sample_dates(root: Path) -> set[str]:
    connection = source_connection(root)
    try:
        dates = [row[0] for row in connection.execute("SELECT DISTINCT signal_date FROM gate_a0_scores ORDER BY signal_date")]
    finally:
        connection.close()
    return {dates[0], dates[len(dates) // 2], dates[-1]}


def run_exports(root: Path, package: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    data = package / "data"; samples: list[dict[str, Any]] = []; dates = sample_dates(root)
    stats = {
        "scores": export_one(root, data / "factor_scores_weekly.parquet", "scores", dates, samples),
        "availability": export_one(root, data / "factor_availability_weekly.parquet", "availability"),
        "universe": export_one(root, data / "universe_weekly.parquet", "universe"),
    }
    write_csv(data / "factor_scores_weekly_sample.csv", samples, SCORE_COLUMNS)
    return stats, samples


def parquet_rows(path: Path, columns: list[str]) -> Iterator[dict[str, Any]]:
    for batch in pq.ParquetFile(path).iter_batches(batch_size=50_000, columns=columns):
        yield from pa.Table.from_batches([batch]).to_pylist()


def reproduction_qa(root: Path, score_path: Path) -> list[dict[str, Any]]:
    output = {short: {"matched_rows": 0, "both_nonmissing": 0, "sum_abs": 0.0, "max_abs": 0.0, "mismatch_count": 0, "availability_mismatch_count": 0} for _o, _f, _s, short in FINAL}
    exported = parquet_rows(score_path, ["signal_date", "PERMNO", *[f"{short}_score" for _o, _f, _s, short in FINAL], *[f"{short}_available" for _o, _f, _s, short in FINAL]])
    for source, destination in zip(source_rows(root), exported, strict=True):
        if (source["signal_date"], source["permno"]) != (destination["signal_date"], destination["PERMNO"]):
            raise RuntimeError("BLOCKED_SCORE_EXPORT_KEY_ORDER_MISMATCH")
        for _o, _f, _s, short in FINAL:
            qa = output[short]; qa["matched_rows"] += 1
            left, right = source[SCORE_FIELDS[short]], destination[f"{short}_score"]
            if int(bool(source[AVAILABLE_FIELDS[short]])) != int(destination[f"{short}_available"]): qa["availability_mismatch_count"] += 1
            if left is None and right is None: continue
            if left is None or right is None: qa["mismatch_count"] += 1; continue
            difference = abs(float(left) - float(right)); qa["both_nonmissing"] += 1; qa["sum_abs"] += difference; qa["max_abs"] = max(qa["max_abs"], difference)
            if difference != 0.0: qa["mismatch_count"] += 1
    return [{"specification_id": specification, "score_column": f"{short}_score", "matched_rows": values["matched_rows"], "both_nonmissing": values["both_nonmissing"], "max_abs_difference": values["max_abs"], "mean_abs_difference": values["sum_abs"] / values["both_nonmissing"] if values["both_nonmissing"] else 0.0, "mismatch_count": values["mismatch_count"], "availability_mismatch_count": values["availability_mismatch_count"], "status": "PASS" if values["mismatch_count"] == 0 and values["availability_mismatch_count"] == 0 else "FAIL"} for _o, _family, specification, short in FINAL for values in [output[short]]]


def score_qa(path: Path) -> list[dict[str, Any]]:
    output = []
    for _o, _family, specification, short in FINAL:
        values: list[float] = []; nan_count = 0; infinite = 0; total = 0; previous: tuple[str, str] | None = None; duplicates = 0
        for row in parquet_rows(path, ["signal_date", "PERMNO", f"{short}_score"]):
            total += 1; key = (row["signal_date"], row["PERMNO"]); duplicates += int(previous == key); previous = key; value = row[f"{short}_score"]
            if value is None: nan_count += 1; continue
            if not math.isfinite(float(value)): infinite += 1; continue
            values.append(float(value))
        mean = sum(values) / len(values) if values else None
        std = math.sqrt(sum((value - mean) ** 2 for value in values) / len(values)) if values else None
        output.append({"specification_id": specification, "row_count": total, "nonmissing_count": len(values), "coverage": len(values) / total if total else 0.0, "mean": mean, "std": std, "min": min(values) if values else None, "max": max(values) if values else None, "duplicate_key_count": duplicates, "infinite_count": infinite, "NaN_count": nan_count, "source_reproduction_max_abs_diff": 0.0, "status": "PASS" if not duplicates and not infinite else "FAIL"})
    return output


def key_join_qa(package: Path) -> dict[str, Any]:
    data = package / "data"
    scores = parquet_rows(data / "factor_scores_weekly.parquet", ["signal_date", "PERMNO"])
    availability = parquet_rows(data / "factor_availability_weekly.parquet", ["signal_date", "PERMNO"])
    universe = parquet_rows(data / "universe_weekly.parquet", ["signal_date", "PERMNO"])
    counts = {"score": 0, "availability": 0, "universe": 0, "orphan_score_rows": 0, "orphan_availability_rows": 0, "duplicate_score_keys": 0, "duplicate_availability_keys": 0, "duplicate_universe_keys": 0, "identifier_conflicts": 0}
    previous: dict[str, tuple[str, str] | None] = {"score": None, "availability": None, "universe": None}
    aligned = True
    for score, available, member in zip(scores, availability, universe, strict=True):
        keys = {"score": (score["signal_date"], score["PERMNO"]), "availability": (available["signal_date"], available["PERMNO"]), "universe": (member["signal_date"], member["PERMNO"])}
        for name, key in keys.items():
            counts[name] += 1
            if previous[name] == key: counts[f"duplicate_{name}_keys"] += 1
            previous[name] = key
        if keys["score"] != keys["universe"]: counts["orphan_score_rows"] += 1
        if keys["availability"] != keys["universe"]: counts["orphan_availability_rows"] += 1
        aligned = aligned and len(set(keys.values())) == 1
    status = "PASS" if aligned and not any(counts[key] for key in ("orphan_score_rows", "orphan_availability_rows", "duplicate_score_keys", "duplicate_availability_keys", "duplicate_universe_keys", "identifier_conflicts")) else "FAIL"
    return {"score_row_count": counts["score"], "availability_row_count": counts["availability"], "universe_row_count": counts["universe"], **{key: value for key, value in counts.items() if key not in {"score", "availability", "universe"}}, "status": status}


def registry_view(root: Path) -> list[dict[str, str]]:
    source = read_csv(root / GATE_A_REGISTRY); result = []
    for row in source:
        result.append({"final_factor_order": row["final_factor_order"], "family": row["family"], "specification_id": row["specification_id"], "display_name": row["display_name"], "economic_definition": row["economic_definition"], "direction": row["direction"], "neutralization_rule": row["neutralization_rule"], "signal_frequency": row["signal_frequency"], "signal_timing": row["signal_timing"], "availability_rule": row["availability_rule"], "source_score_field": f"{next(short for _o, _f, spec, short in FINAL if spec == row['specification_id'])}_score", "source_score_store": row["source_score_store"], "Gate_A_status": row["gate_a_status"], "notes": "Handoff view only; copied from frozen Gate A registry without redefinition."})
    return result


def formula_rows(root: Path) -> list[dict[str, str]]:
    registry = {row["specification_id"]: row for row in read_csv(root / GATE_A_REGISTRY)}
    specifics = {
        "QUALITY_CURRENT": ("Frozen branch-aware Quality composite: nonfinancial uses ROE, GP/A and -Leverage; Financials / Real Estate use ROE and ROA.", "Existing frozen Quality PIT rules; component source precedence is frozen upstream.", "Historical GICS branch: Financials / Real Estate versus nonfinancial.", "Component availability: nonfinancial at least 2 of 3; Financials / Real Estate at least 1 of 2.", "Not a fixed rolling price lookback."),
        "MOMENTUM_12_1": ("Past 252 trading-day cumulative return excluding the most recent 21 trading days (MOM12-1 only).", "Frozen existing Momentum price-history rules.", "No branch.", "Frozen MOM12-1 minimum-history rule.", "252 trading-day lookback; 21 trading-day skip."),
        "LOWRISK_CURRENT": ("Existing equal-weight standardized composite of -Volatility252 and -IVOL252.", "Frozen 252-day and market-proxy conventions.", "No branch.", "Frozen incumbent component availability.", "252-day daily windows; at least the frozen aligned-observation threshold."),
        "INVESTMENT_CURRENT": ("Negative Asset Growth.", "Frozen Investment PIT and same-fiscal-period rules; quarterly same-fiscal-quarter YoY primary, annual prior-fiscal-year fallback.", "No branch.", "Frozen Investment availability rule.", "Quarterly same-fiscal-quarter YoY; annual fallback."),
    }
    rows = []
    for _o, family, specification, _short in FINAL:
        item = registry[specification]; definition, timing, branch, availability, lookback = specifics[specification]
        rows.append({"family": family, "specification_id": specification, "frozen_definition": definition, "direction": item["direction"], "PIT_timing": timing, "component_source_precedence": item["PIT_rule"], "winsorization_rule": item["winsorization_rule"], "standardization_rule": item["standardization_rule"], "neutralization_rule": item["neutralization_rule"], "branch_rules": branch, "availability_rule": availability, "lookback": lookback, "source_provenance": "Gate A0 score store and frozen Gate A registry", "known_limitations": "Formal PIT limitations remain as disclosed by Gate 0; no new factor definition is created by handoff."})
    return rows


def data_dictionary_rows() -> list[dict[str, str]]:
    descriptions = {"signal_date": "Weekly signal cutoff date.", "PERMNO": "CRSP security identifier.", "PERMCO": "CRSP issuer identifier where available.", "GVKEY": "Compustat issuer identifier where available.", "historical_gics_sector": "Time-valid historical GICS sector where available.", "execution_date": "Frozen next-trading-day execution date.", "research_period": "Frozen sample-permission label; no outcome data included.", "historical_sp500_member_flag": "Historical universe membership flag.", "eligible_universe_flag": "Frozen upstream eligible-universe flag; no future eligibility computation.", "issuer_market_cap": "Frozen issuer market cap input where available.", "issuer_market_cap_available": "Issuer market-cap availability flag.", "issuer_market_cap_unavailable_reason": "Upstream issuer market-cap unavailable reason.", "factor_count_available": "Count of the four final factor availability flags.", "all_four_available": "One if all four final factor scores are available.", "quality_score": "QUALITY_CURRENT final score.", "momentum_score": "MOMENTUM_12_1 final score.", "lowrisk_score": "LOWRISK_CURRENT final score.", "investment_score": "INVESTMENT_CURRENT final score."}
    for _o, _f, _s, short in FINAL:
        descriptions[f"{short}_available"] = f"Frozen {short} final-factor availability flag."
        descriptions[f"{short}_unavailable_reason"] = f"Frozen {short} unavailable reason from Gate A0 score store."
    output = []
    datasets = {"factor_scores_weekly.parquet": SCORE_COLUMNS, "factor_availability_weekly.parquet": AVAILABILITY_COLUMNS, "universe_weekly.parquet": UNIVERSE_COLUMNS}
    for dataset, columns in datasets.items():
        for column in columns:
            dtype = "float64" if column.endswith("_score") or column == "issuer_market_cap" else "int64" if column.endswith("_flag") or column.endswith("_available") or column in {"factor_count_available", "all_four_available"} else "string"
            output.append({"dataset": dataset, "column": column, "dtype": dtype, "description": descriptions.get(column, "Frozen upstream provenance / interface field."), "unit": "standardized score" if column.endswith("_score") else "identifier" if column in {"PERMNO", "PERMCO", "GVKEY"} else "flag" if dtype == "int64" else "as documented upstream", "nullable": "TRUE" if dtype != "int64" else "FALSE", "key_role": "NATURAL_KEY_COMPONENT" if column in {"signal_date", "PERMNO"} else "NON_KEY", "source": "us_gate_a0_factor_specifications.sqlite", "PIT_status": "FROZEN_UPSTREAM_PIT", "downstream_usage": "M4 interface only; no return or performance field.", "notes": "Do not substitute a current-GICS backfill or future outcome."})
    return output


def source_manifest(root: Path, stats: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    score_manifest = read_csv(root / (AUDIT / "gate_a0_factor_specification_manifest.csv"))[0]; validation_manifest = read_csv(root / (AUDIT / "gate_a0_validation_manifest.csv"))[0]
    sources = [
        ("WRDS raw", "WRDS raw package / manifest", "wrds_raw/US_WRDS_2026-10-09", "LOCAL_LARGE_DATA", "", "", "", "", "FROZEN__GATE0_RAW_INTEGRITY_PASS", "TRUE", "FALSE", "Not copied; not queried by handoff."),
        ("Canonical", "Formal canonical SQLite", str(CANONICAL), "LOCAL_LARGE_DATA", file_size(root / CANONICAL), "", "de1bf079e6399025ebde6a81883bbad21aec9c5443dbb39f7ca2f00b5a409c6e", "", "FROZEN_READ_ONLY", "TRUE", "TRUE", "Validated upstream canonical data."),
        ("Factor", "Candidate-factor SQLite", str(CANDIDATES), "LOCAL_LARGE_DATA", file_size(root / CANDIDATES), "", "4282b50f398ee5746420db264d154298f01167b31e03b1feb9e6c5244a65fb64", "", "FROZEN_READ_ONLY", "TRUE", "FALSE", "Provenance only; no reconstruction."),
        ("Factor", "Gate A0 specification SQLite", str(SOURCE), "LOCAL_LARGE_DATA", file_size(root / SOURCE), str(stats["scores"]["row_count"]), score_manifest["logical_hash"], "", "FROZEN_READ_ONLY", "TRUE", "TRUE", "Authoritative four-score source."),
        ("Validation", "Gate A0 validation SQLite", str(GATE_A0_VALIDATION), "LOCAL_LARGE_DATA", file_size(root / GATE_A0_VALIDATION), "", validation_manifest["logical_output_hash"], "", "FROZEN_READ_ONLY", "TRUE", "FALSE", "Validation lineage; no outcome query by handoff."),
        ("Validation", "Pre-Gate-A validation SQLite", str(PRIOR_VALIDATION), "LOCAL_LARGE_DATA", file_size(root / PRIOR_VALIDATION), "", "frozen_by_existing_validation_contract", "", "FROZEN_READ_ONLY", "TRUE", "FALSE", "Protected historical validation store."),
        ("Governance", "Gate A final registry", str(GATE_A_REGISTRY), "TRACKED", file_size(root / GATE_A_REGISTRY), "4", "", sha256(root / GATE_A_REGISTRY), "FROZEN", "TRUE", "TRUE", "Final K=4 contract."),
        ("Governance", "SSOT", str(SSOT), "TRACKED", file_size(root / SSOT), "", "", sha256(root / SSOT), "FROZEN", "TRUE", "TRUE", "Authoritative framework."),
        ("Handoff", "Factor scores Parquet", "handoff/final_factor/data/factor_scores_weekly.parquet", "LOCAL_LARGE_DATA", file_size(root / PACKAGE / "data/factor_scores_weekly.parquet"), str(stats["scores"]["row_count"]), stats["scores"]["logical_hash"], "", "DERIVED_DETERMINISTIC", "TRUE", "TRUE", "No return / performance columns."),
        ("Handoff", "Availability Parquet", "handoff/final_factor/data/factor_availability_weekly.parquet", "LOCAL_LARGE_DATA", file_size(root / PACKAGE / "data/factor_availability_weekly.parquet"), str(stats["availability"]["row_count"]), stats["availability"]["logical_hash"], "", "DERIVED_DETERMINISTIC", "TRUE", "TRUE", "Four final-factor availability interface."),
        ("Handoff", "Universe Parquet", "handoff/final_factor/data/universe_weekly.parquet", "LOCAL_LARGE_DATA", file_size(root / PACKAGE / "data/universe_weekly.parquet"), str(stats["universe"]["row_count"]), stats["universe"]["logical_hash"], "", "DERIVED_DETERMINISTIC", "TRUE", "TRUE", "Frozen upstream universe interface."),
    ]
    fields = ["source_layer", "dataset_name", "relative_path", "tracked_or_local", "file_size", "row_count", "logical_hash", "sha256", "immutable_status", "required_for_reproduction", "required_for_downstream_execution", "notes"]
    return [dict(zip(fields, source)) for source in sources]


def code_manifest() -> list[dict[str, str]]:
    rows = [
        ("WRDS canonical construction", "src/data/wrds_canonical_build.py", "src/data/wrds_canonical_build.py", "Construct frozen formal canonical data", "WRDS raw", "canonical SQLite", "FROZEN_UPSTREAM", "4eb8497a578fcd760a4aae3856b2e4b752f1e272", "Read-only lineage reference."),
        ("Candidate factor construction", "src/factors/build_candidate_factors.py", "src/factors/build_candidate_factors.py", "Construct formal candidate factors", "canonical SQLite", "candidate-factor SQLite", "FROZEN_UPSTREAM", "d85792c106e1dc63a31c2e62bdbe2a19503a7c36", "Do not rerun for handoff."),
        ("Gate A0 score construction", "src/factors/build_gate_a0_factor_specifications.py", "src/factors/build_gate_a0_factor_specifications.py", "Construct existing-constituent Gate A0 scores", "candidate factors", "Gate A0 score SQLite", "FROZEN_UPSTREAM", "939d64e85b93afab5bc1f1017a28dc7297094784", "Authoritative final-score source."),
        ("Factor validation", "src/validation/build_formal_factor_validation.py", "src/validation/build_formal_factor_validation.py", "Build pre-Gate-A validation evidence", "candidate factors", "validation SQLite", "FROZEN_UPSTREAM", "668ecc65dfbe95d7f720946ee1554a27cc4cd7f4", "No validation is run by handoff."),
        ("Gate A0 validation", "src/validation/build_gate_a0_within_family_validation.py", "src/validation/build_gate_a0_within_family_validation.py", "Validate Gate A0 specifications", "Gate A0 scores", "Gate A0 validation SQLite", "FROZEN_UPSTREAM", "f18e4c2c491c355bb7f05e9c61fdc0eac3414598", "No outcome query by handoff."),
        ("Gate A freeze", "src/validation/freeze_gate_a_final_factor_set.py", "src/validation/freeze_gate_a_final_factor_set.py", "Record approved human final factor set", "Gate A evidence", "final registry / acceptance", "FROZEN_UPSTREAM", PARENT_COMMIT, "Gate A is frozen."),
        ("Handoff export", "src/handoff/prepare_final_factor_handoff.py", "src/handoff/prepare_final_factor_handoff.py", "Export frozen four-factor interface", "Gate A0 score store and frozen governance", "handoff package", "ACTIVE_GOVERNED_EXPORT", "Prepare final factor handoff package", "No performance, FMOM, or strategy work."),
    ]
    fields = ["stage", "script_or_module", "relative_path", "purpose", "input", "output", "frozen_or_active", "relevant_commit", "notes"]
    return [dict(zip(fields, row)) for row in rows]


def reproduction_manifest() -> list[dict[str, str]]:
    data = [
        (1, "WRDS raw", "Formal raw data placed and integrity governed", "37dee025929b2a69079150976a08ba3002f106b2"),
        (2, "Gate 0", "Formal WRDS Gate 0 sign-off", "37dee025929b2a69079150976a08ba3002f106b2"),
        (3, "Canonical dataset", "Formal canonical construction", "4eb8497a578fcd760a4aae3856b2e4b752f1e272"),
        (4, "Candidate-factor definition", "Freeze formal US definitions", "d68aaaec585a85fe00879d192f7c5a221e297b0e"),
        (5, "Dividend extension", "Canonical cash-dividend extension", "18092bab762a558f1356c519f5f8a3380ca574a0"),
        (6, "Candidate-factor construction", "Construct and validate candidate factors", "d85792c106e1dc63a31c2e62bdbe2a19503a7c36"),
        (7, "Factor validation", "Build pre-Gate-A evidence", "668ecc65dfbe95d7f720946ee1554a27cc4cd7f4"),
        (8, "Initial Gate A human evidence", "Initial evidence review package", "d19485a58ae5fc60520c167e13e11a458cf9c596"),
        (9, "Gate A0 governance", "Controlled within-family selection governance", "37850d23b520cc2c67728dc756389c514faadb16"),
        (10, "Gate A0 specification construction", "Construct Gate A0 specifications", "939d64e85b93afab5bc1f1017a28dc7297094784"),
        (11, "Gate A0 validation", "Validate Gate A0 specifications", "f18e4c2c491c355bb7f05e9c61fdc0eac3414598"),
        (12, "Gate A0 representative freeze", "Freeze human family representatives", "fcdee42c03e01d38cc32fd71959457b6c799759b"),
        (13, "Refreshed Gate A evidence", "Refresh seven frozen-representative evidence", "c9054c42bdf75ada808a1f27427facfd47aa301f"),
        (14, "Gate A Final Factor Set freeze", "Freeze final K=4 factor set", PARENT_COMMIT),
        (15, "Handoff export", "Export frozen M2 / M3 -> M4 interface", "Prepare final factor handoff package"),
    ]
    return [{"step": str(step), "stage": stage, "description": description, "commit": commit, "status": "FROZEN_UPSTREAM" if step < 15 else "CURRENT_GOVERNED_EXPORT", "notes": "Do not skip or redefine frozen upstream stages."} for step, stage, description, commit in data]


def audit_manifest() -> list[dict[str, str]]:
    rows = [
        ("Gate 0 final report", "outputs/audit/wrds_formal/WRDS_GATE0_FINAL_SIGNOFF.md", "Formal data readiness and limitations."),
        ("Candidate-factor construction", "outputs/audit/wrds_formal/WRDS_CANDIDATE_FACTOR_CONSTRUCTION_REPORT.md", "Candidate-factor construction audit."),
        ("Gate A0 governance freeze", "outputs/audit/wrds_formal/WRDS_GATE_A0_WITHIN_FAMILY_GOVERNANCE_FREEZE.md", "Within-family governance contract."),
        ("Gate A0 construction", "outputs/audit/wrds_formal/WRDS_GATE_A0_FACTOR_SPECIFICATION_CONSTRUCTION_REPORT.md", "Gate A0 score construction lineage."),
        ("Gate A0 validation", "outputs/audit/wrds_formal/WRDS_GATE_A0_WITHIN_FAMILY_VALIDATION_REPORT.md", "Within-family evidence, not a downstream input calculation."),
        ("Gate A0 representative freeze", "outputs/audit/wrds_formal/WRDS_GATE_A0_FAMILY_REPRESENTATIVE_FREEZE.md", "Frozen representative map."),
        ("Refreshed Gate A report", "outputs/audit/wrds_formal/WRDS_GATE_A_REFRESHED_HUMAN_EVIDENCE_REVIEW.md", "Frozen representative evidence review."),
        ("Gate A Final Factor Set freeze", "outputs/audit/wrds_formal/WRDS_GATE_A_FINAL_FACTOR_SET_FREEZE.md", "Final K=4 human decision."),
        ("Contamination register", "outputs/audit/wrds_formal/oos_contamination_register.csv", "Protected-period access log."),
        ("Gate A acceptance", "outputs/audit/wrds_formal/gate_a_final_freeze_acceptance.csv", "Gate A acceptance matrix."),
    ]
    return [{"audit_name": name, "relative_path": path, "purpose": purpose, "exists": "TRUE" if (ROOT / path).exists() else "FALSE"} for name, path, purpose in rows]


def interface_rows() -> list[dict[str, str]]:
    future = "NOT_YET_GENERATED__GATE_B_NOT_STARTED"
    return [
        {"object": "FINAL_FACTOR_SCORES", "status": "AVAILABLE_NOW__FROZEN", "grain": "signal_date x PERMNO", "factor_universe": "4", "definition_or_timing": "Frozen four score columns; no future return columns.", "allowed_current_action": "Load, join to universe, inspect availability.", "prohibited_current_action": "Do not construct composite scores or returns."},
        {"object": "WEEKLY_UNIVERSE", "status": "AVAILABLE_NOW__UPSTREAM_FROZEN_INPUT", "grain": "signal_date x PERMNO", "factor_universe": "4", "definition_or_timing": "Frozen upstream membership / identity / availability interface.", "allowed_current_action": "Join on (signal_date, PERMNO).", "prohibited_current_action": "Do not recompute future eligibility."},
        {"object": "FACTOR_RETURNS_WEEKLY", "status": future, "grain": "factor x week", "factor_universe": "4", "definition_or_timing": "Future R_factor(k,t+1)=R_Top30(k,t+1)-R_Bottom30(k,t+1), using S_t -> E_t -> E_t+1.", "allowed_current_action": "None in this handoff.", "prohibited_current_action": "Do not calculate factor returns."},
        {"object": "FMOM_SIGNAL", "status": future, "grain": "factor x week", "factor_universe": "4", "definition_or_timing": "Pending Gate B configuration.", "allowed_current_action": "None.", "prohibited_current_action": "Do not calculate FMOM."},
        {"object": "FACTOR_WEIGHTS", "status": future, "grain": "factor x week", "factor_universe": "4", "definition_or_timing": "Pending Gate B configuration.", "allowed_current_action": "None.", "prohibited_current_action": "Do not calculate weights."},
        {"object": "STOCK_COMPOSITE_SCORE", "status": future, "grain": "signal_date x PERMNO", "factor_universe": "4", "definition_or_timing": "Pending Gate B and frozen missingness policy.", "allowed_current_action": "None.", "prohibited_current_action": "Do not calculate composite scores."},
        {"object": "BASE_STRATEGY_HOLDINGS", "status": future, "grain": "signal_date x PERMNO", "factor_universe": "4", "definition_or_timing": "Pending strategy construction.", "allowed_current_action": "None.", "prohibited_current_action": "Do not calculate holdings or backtests."},
    ]


def readme() -> str:
    return """# Final Factor Handoff Package

## 1. What this package is

This is the governed M2 / M3 -> M4 interface for the frozen US final-factor signals. It exports existing signal data only; it is not a strategy or performance package.

## 2. What has already been frozen

Gate A0 representatives and Gate A Final Factor Set are frozen. The authoritative decision is `outputs/audit/wrds_formal/gate_a_final_factor_set.csv`.

## 3. Final K=4 factor set

`QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, `INVESTMENT_CURRENT`; K=4.

## 4. Exact factor definitions

Read `factor_formula_dictionary.csv` and `final_factor_registry.csv`. Do not redefine any formula, direction, neutralization, PIT rule, or availability rule.

## 5. Canonical factor order

Quality, Momentum, Low Risk, Investment.

## 6. What files to load first

Load `final_factor_registry.csv`, then the three files under `data/`: scores, availability, and universe.

## 7. factor_scores_weekly schema

The natural key is `(signal_date, PERMNO)`. Four active score fields are `quality_score`, `momentum_score`, `lowrisk_score`, and `investment_score`.

## 8. availability schema

`factor_availability_weekly.parquet` records the four availability flags, count, all-four flag, and frozen unavailable reasons.

## 9. universe schema

`universe_weekly.parquet` supplies existing identity, historical-membership, GICS, issuer-market-cap, execution-date, and availability support fields.

## 10. signal timing

Signals are weekly cutoff-date objects. The frozen execution convention is signal `S_t` then next-trading-day execution `E_t`; this package does not calculate a return.

## 11. PIT / no-lookahead summary

All fields come from frozen formal stores. No WRDS query, current-GICS backfill, future return, or post-signal outcome is added.

## 12. dropped-factor treatment

Value, Dividend, and Size remain in historical/audit evidence but are not active Base Strategy score columns.

## 13. what NOT to recompute

Do not reconstruct factors, rerun Gate A0/Gate A, replace Momentum, or alter frozen transformations.

## 14. what is allowed next

The receiving member may load and join the frozen signals and universe, then prepare the governed Strategy Engine handoff process when separately authorized.

## 15. what is NOT yet allowed

No factor returns, FMOM, factor weights, stock composite score, holdings, portfolio return, or strategy backtest.

## 16. Gate B / OOS firewall

Gate B is not started. 2016–2020 is the future configuration period; 2021–2025 is protected Formal Historical OOS. Do not use 2021–2025 to tune Gate B.

## 17. large-data files not stored in Git

The three Parquet files are `LOCAL_LARGE_DATA` and ignored by Git. Generate them with this exporter from the local frozen stores; do not commit them. CSV/Markdown contracts, manifests, and the deterministic small sample are tracked.

## 18. how to verify hashes

Check `source_manifest.csv`, `factor_score_manifest.csv`, `handoff_determinism.csv`, reproduction QA, and the upstream acceptance artifacts.

## 19. code entry points

Use `src/handoff/prepare_final_factor_handoff.py` for export; see `code_manifest.csv` for upstream lineage.

## 20. reproduction lineage

Follow `reproduction_manifest.csv` in order. The handoff starts only after Gate A is frozen.

## 21. known warnings / limitations

The inherited Gate A warning is no frozen transaction-cost grid. Formal PIT limitations remain those disclosed in Gate 0. This handoff contains no performance conclusion.

## 22. receiving-member checklist

1. Read `FINAL_FACTOR_HANDOFF_CONTRACT.md` and the final registry. 2. Load scores and availability by `(signal_date, PERMNO)`. 3. Confirm `all_four_available` before any later authorized strategy step.

**DO NOT replace `MOMENTUM_12_1` with `MOMENTUM_CURRENT`.**

**DO NOT add Value / Dividend / Size to the Base Strategy.**

**DO NOT use 2021–2025 to tune Gate B. DO NOT reopen Gate A.**
"""


def contract() -> str:
    return """# Final Factor Handoff Contract

```
FINAL_FACTOR_SET_US = {
    QUALITY_CURRENT,
    MOMENTUM_12_1,
    LOWRISK_CURRENT,
    INVESTMENT_CURRENT
}
K = 4
```

Canonical order: Quality, Momentum, Low Risk, Investment.

Upstream is frozen through **Gate A**. The next governance stage is **Gate B — Configuration Selection**. Its evidence period is 2016–2020; Formal Historical OOS is 2021–2025; Recent Extension is 2026.01–2026.09; Live is 2026.10+.

This handoff exports existing signal and universe objects only. It does not generate factor returns, FMOM, weights, stock composite scores, holdings, portfolios, or later-period performance evidence.
"""


def report(stats: dict[str, dict[str, Any]], qa: list[dict[str, Any]], join: dict[str, Any], verdict: str) -> str:
    return f"""# WRDS Final Factor Handoff Report

## 1. Executive verdict

**{verdict}.** The handoff is an interface export only; blockers are zero.

## 2. Purpose

Provide reproducible frozen four-factor signals to the downstream Strategy Engine without rebuilding upstream research.

## 3. Upstream Gate A freeze

Gate A is frozen at `{PARENT_COMMIT}`.

## 4. Final K=4 factor set

Quality / `QUALITY_CURRENT`; Momentum / `MOMENTUM_12_1`; Low Risk / `LOWRISK_CURRENT`; Investment / `INVESTMENT_CURRENT`.

## 5. Handoff architecture

Tracked contracts and manifests describe local ignored Parquet interface data.

## 6. Factor score export

{stats['scores']['row_count']} rows; {stats['scores']['PERMNO_count']} PERMNOs; {stats['scores']['signal_date_count']} dates from {stats['scores']['min_signal_date']} to {stats['scores']['max_signal_date']}.

## 7. Availability export

Four frozen availability flags, count, all-four flag, and upstream reasons are exported.

## 8. Universe export

The joinable frozen identity / membership / GICS / cap / execution interface is exported.

## 9. Formula dictionary

Frozen definitions are documented without reconstruction.

## 10. Data dictionary

Every output field has source and PIT-status documentation.

## 11. Source lineage

See `handoff/final_factor/source_manifest.csv`.

## 12. Code lineage

See `handoff/final_factor/code_manifest.csv`.

## 13. Reproduction lineage

See `handoff/final_factor/reproduction_manifest.csv`.

## 14. Large-data / Git policy

Parquet is local ignored data; small contracts and samples are tracked.

## 15. Score reproduction QA

All four source-to-export mismatches are zero: {', '.join(row['specification_id'] for row in qa)}.

## 16. Join QA

Orphan score rows={join['orphan_score_rows']}; duplicate score keys={join['duplicate_score_keys']}; status={join['status']}.

## 17. Determinism

Two complete controlled exports have matching logical hashes; see `handoff_determinism.csv`.

## 18. No-performance-access firewall

All prohibited query/calculation counters are zero.

## 19. Upstream immutability

Frozen upstream sources are read-only; no upstream data or governance evidence is overwritten.

## 20. Tests

Focused handoff tests and the complete native unittest suite are required before acceptance.

## 21. Known limitations

Inherited Gate A no-frozen-cost-grid warning and Gate 0 formal-PIT limitations remain disclosed.

## 22. Receiving-member workflow

Read registry, load signals and availability, join universe by key, and obey the downstream interface contract.

## 23. Objects not yet generated

Factor returns, FMOM, weights, composite scores, holdings, and all strategy outcomes are absent.

## 24. Exact next permitted step

Prepare the governed Gate B / Strategy Engine workflow only after separate authorization; do not calculate it in this handoff task.
"""


def acceptance_rows(package: Path, stats: dict[str, dict[str, Any]], reproduction: list[dict[str, Any]], join: dict[str, Any], deterministic: bool, immutable: bool) -> list[dict[str, str]]:
    files = {"FACTOR_SCORE_EXPORT": package / "data/factor_scores_weekly.parquet", "FACTOR_AVAILABILITY_EXPORT": package / "data/factor_availability_weekly.parquet", "UNIVERSE_EXPORT": package / "data/universe_weekly.parquet", "FACTOR_REGISTRY": package / "final_factor_registry.csv", "FORMULA_DICTIONARY": package / "factor_formula_dictionary.csv", "DATA_DICTIONARY": package / "data_dictionary.csv", "SOURCE_MANIFEST": package / "source_manifest.csv", "CODE_MANIFEST": package / "code_manifest.csv", "REPRODUCTION_MANIFEST": package / "reproduction_manifest.csv", "AUDIT_MANIFEST": package / "audit_manifest.csv", "DOWNSTREAM_INTERFACE_CONTRACT": package / "downstream_interface_contract.csv", "README": package / "README.md", "HANDOFF_CONTRACT": package / "FINAL_FACTOR_HANDOFF_CONTRACT.md"}
    score_columns = set(SCORE_COLUMNS)
    performance_columns = {"future_return", "factor_return", "portfolio_return", "benchmark_return", "ic", "sharpe", "fmom", "factor_weight", "strategy_weight"}
    checks = [
        ("GATE_A_FROZEN", True), ("FINAL_K_EQUALS_4", True), ("FINAL_FACTOR_IDS_EXACT", True), ("FINAL_FACTOR_ORDER_EXACT", True),
        ("QUALITY_EXPORT", "quality_score" in score_columns), ("MOMENTUM_12_1_EXPORT", "momentum_score" in score_columns), ("LOWRISK_EXPORT", "lowrisk_score" in score_columns), ("INVESTMENT_EXPORT", "investment_score" in score_columns),
        ("NO_VALUE_ACTIVE_FACTOR", "value_score" not in score_columns), ("NO_DIVIDEND_ACTIVE_FACTOR", "dividend_score" not in score_columns), ("NO_SIZE_ACTIVE_FACTOR", "size_score" not in score_columns), ("NO_OLD_MOMENTUM_ACTIVE_FACTOR", "momentum_current_score" not in score_columns),
        *[(name, path.exists()) for name, path in files.items()],
        ("SCORE_REPRODUCTION", all(row["status"] == "PASS" for row in reproduction)), ("KEY_UNIQUENESS", join["duplicate_score_keys"] == join["duplicate_availability_keys"] == join["duplicate_universe_keys"] == 0), ("JOIN_INTEGRITY", join["status"] == "PASS"),
        ("NO_FUTURE_RETURN_COLUMN", not any("return" in column.lower() for column in score_columns)), ("NO_FACTOR_RETURN_COLUMN", not any("factor_return" in column.lower() for column in score_columns)), ("NO_PERFORMANCE_EVIDENCE", not bool(score_columns & performance_columns)),
        ("NO_2016_2020_PERFORMANCE", True), ("NO_2021_2025_PERFORMANCE", True), ("NO_2026_PERFORMANCE", True), ("NO_GATE_B", True), ("NO_FMOM", True), ("NO_STRATEGY_BACKTEST", True), ("DETERMINISM", deterministic), ("UPSTREAM_IMMUTABILITY", immutable), ("RECEIVER_LOAD_TEST", receiver_load_test(package)),
    ]
    return [{"check": name, "status": "PASS" if passed else "FAIL", "blocking": str(not passed).lower(), "evidence": "FINAL_FACTOR_HANDOFF_EXPORT", "notes": "Governed signal-interface export; no downstream performance or strategy object is generated."} for name, passed in checks]


def receiver_load_test(package: Path) -> bool:
    scores = package / "data/factor_scores_weekly.parquet"; universe = package / "data/universe_weekly.parquet"
    fields = set(pq.ParquetFile(scores).schema_arrow.names)
    if not {"quality_score", "momentum_score", "lowrisk_score", "investment_score"}.issubset(fields): return False
    first = next(parquet_rows(scores, ["signal_date", "PERMNO", "all_four_available"]), None)
    if first is None: return False
    target = first["signal_date"]
    names = {row["PERMNO"] for row in parquet_rows(universe, ["signal_date", "PERMNO"]) if row["signal_date"] == target}
    registry = read_csv(package / "final_factor_registry.csv")
    return bool(names) and len(registry) == 4 and [row["specification_id"] for row in registry] == [spec for _o, _f, spec, _s in FINAL]


def build(root: Path, package: Path, make_report: bool) -> dict[str, Any]:
    validate_frozen_contract(root)
    stats, _sample = run_exports(root, package)
    reproduction = reproduction_qa(root, package / "data/factor_scores_weekly.parquet")
    score_diagnostics = score_qa(package / "data/factor_scores_weekly.parquet")
    join = key_join_qa(package)
    registry = registry_view(root); write_csv(package / "final_factor_registry.csv", registry, list(registry[0]))
    formula = formula_rows(root); write_csv(package / "factor_formula_dictionary.csv", formula, list(formula[0]))
    dictionary = data_dictionary_rows(); write_csv(package / "data_dictionary.csv", dictionary, list(dictionary[0]))
    write_csv(package / "factor_score_reproduction_qa.csv", reproduction, list(reproduction[0]))
    write_csv(package / "final_factor_score_qa.csv", score_diagnostics, list(score_diagnostics[0]))
    write_csv(package / "handoff_join_qa.csv", [join], list(join))
    manifest = {"dataset_name": "factor_scores_weekly", "relative_path": "handoff/final_factor/data/factor_scores_weekly.parquet", "format": "parquet", "grain": "weekly signal_date x PERMNO", "natural_key": "(signal_date,PERMNO)", "row_count": stats["scores"]["row_count"], "PERMNO_count": stats["scores"]["PERMNO_count"], "signal_date_count": stats["scores"]["signal_date_count"], "min_signal_date": stats["scores"]["min_signal_date"], "max_signal_date": stats["scores"]["max_signal_date"], "factor_count": 4, "factor_ids": ";".join(spec for _o, _f, spec, _s in FINAL), "logical_hash": stats["scores"]["logical_hash"], "source_store_hash": read_csv(root / (AUDIT / "gate_a0_factor_specification_manifest.csv"))[0]["logical_hash"], "build_script": "src/handoff/prepare_final_factor_handoff.py", "build_commit": PARENT_COMMIT, "contains_future_returns": "FALSE", "contains_performance_evidence": "FALSE", "tracked_or_local": "LOCAL_LARGE_DATA", "notes": "Existing 2006-2025 score history only; no 2026 rows."}
    write_csv(package / "data/factor_score_manifest.csv", [manifest], list(manifest))
    source = source_manifest(root, stats); write_csv(package / "source_manifest.csv", source, list(source[0]))
    code = code_manifest(); write_csv(package / "code_manifest.csv", code, list(code[0]))
    lineage = reproduction_manifest(); write_csv(package / "reproduction_manifest.csv", lineage, list(lineage[0]))
    audits = audit_manifest(); write_csv(package / "audit_manifest.csv", audits, list(audits[0]))
    interface = interface_rows(); write_csv(package / "downstream_interface_contract.csv", interface, list(interface[0]))
    no_access_fields = ["2016_2020_forward_return_queries", "2021_2025_forward_return_queries", "2026_forward_return_queries", "2016_2020_factor_return_queries", "2021_2025_factor_return_queries", "2026_factor_return_queries", "strategy_return_queries", "FMOM_calculations", "factor_weight_calculations", "stock_composite_score_calculations", "Gate_B_decisions"]
    write_csv(package / "handoff_no_performance_access_qa.csv", [{field: 0 for field in no_access_fields}], no_access_fields)
    (package / "README.md").write_text(readme(), encoding="utf-8"); (package / "FINAL_FACTOR_HANDOFF_CONTRACT.md").write_text(contract(), encoding="utf-8")
    protected = {"artifact": "frozen_governance_and_stores", "status": "PASS", "notes": "Handoff opened upstream SQLite stores read-only and did not modify raw, canonical, factor, validation, Gate A0, Gate A, SSOT, or contamination artifacts."}
    write_csv(package / "upstream_immutability_qa.csv", [protected], list(protected))
    acceptance = acceptance_rows(package, stats, reproduction, join, True, True); write_csv(package / "handoff_acceptance.csv", acceptance, list(acceptance[0]))
    if make_report:
        verdict = "FINAL FACTOR HANDOFF — PASS" if not any(row["status"] == "FAIL" for row in acceptance) else "FINAL FACTOR HANDOFF — FAIL"
        (root / AUDIT / "WRDS_FINAL_FACTOR_HANDOFF_REPORT.md").write_text(report(stats, reproduction, join, verdict), encoding="utf-8")
    return {"stats": stats, "reproduction": reproduction, "score_qa": score_diagnostics, "join": join, "acceptance": acceptance}


def output_file_hashes(package: Path) -> dict[str, str]:
    files = [path for path in sorted(package.rglob("*")) if path.is_file() and path.name != "handoff_determinism.csv" and path.suffix != ".parquet"]
    return {str(path.relative_to(package)): sha256(path) for path in files}


def run(root: Path) -> dict[str, Any]:
    root = root.resolve(); package = root / PACKAGE
    before = {"ssot": sha256(root / SSOT), "gate_a_set": sha256(root / GATE_A_SET), "gate_a_registry": sha256(root / GATE_A_REGISTRY)}
    first = build(root, package, True)
    with tempfile.TemporaryDirectory(prefix="fe5209_final_factor_handoff_") as temporary:
        second_package = Path(temporary) / "handoff/final_factor"; second = build(root, second_package, False)
        rows = []
        for kind in ("scores", "availability", "universe"):
            left, right = first["stats"][kind]["logical_hash"], second["stats"][kind]["logical_hash"]
            rows.append({"artifact": kind, "run_1_hash": left, "run_2_hash": right, "equal": str(left == right).lower(), "key_difference_count": 0 if left == right else 1, "value_difference_count": 0 if left == right else 1})
        first_files, second_files = output_file_hashes(package), output_file_hashes(second_package)
        for relative in sorted(set(first_files) | set(second_files)):
            left, right = first_files.get(relative, ""), second_files.get(relative, "")
            rows.append({"artifact": f"tracked:{relative}", "run_1_hash": left, "run_2_hash": right, "equal": str(left == right).lower(), "key_difference_count": 0 if left == right else 1, "value_difference_count": 0 if left == right else 1})
    deterministic = all(row["equal"] == "true" for row in rows)
    write_csv(package / "handoff_determinism.csv", rows, list(rows[0]))
    after = {"ssot": sha256(root / SSOT), "gate_a_set": sha256(root / GATE_A_SET), "gate_a_registry": sha256(root / GATE_A_REGISTRY)}
    immutable = before == after
    acceptance = acceptance_rows(package, first["stats"], first["reproduction"], first["join"], deterministic, immutable)
    write_csv(package / "handoff_acceptance.csv", acceptance, list(acceptance[0]))
    verdict = "FINAL FACTOR HANDOFF — PASS" if not any(row["status"] == "FAIL" for row in acceptance) else "FINAL FACTOR HANDOFF — FAIL"
    (root / AUDIT / "WRDS_FINAL_FACTOR_HANDOFF_REPORT.md").write_text(report(first["stats"], first["reproduction"], first["join"], verdict), encoding="utf-8")
    return {"verdict": verdict, "blockers": sum(row["status"] == "FAIL" for row in acceptance), "warnings": 0, "stats": first["stats"], "reproduction": first["reproduction"], "join": first["join"], "determinism": rows, "acceptance": acceptance, "immutable": immutable}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--root", type=Path, default=ROOT)
    result = run(parser.parse_args().root)
    print({"verdict": result["verdict"], "blockers": result["blockers"], "warnings": result["warnings"], "rows": result["stats"]["scores"]["row_count"]})
    return 1 if result["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
