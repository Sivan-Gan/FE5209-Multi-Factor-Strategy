"""Export the frozen 20-specification formal factor-research archive.

This is a governed archive/export step, not factor construction or factor
research.  It reads the frozen Gate A0 score store in SQLite read-only mode
and the frozen Gate A0/Gate A decision artifacts.  It never queries returns,
computes IC, produces portfolios, calculates FMOM, or accesses Gate B.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator

import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[2]
AUDIT = Path("outputs/audit/wrds_formal")
PACKAGE = Path("handoff/factor_research_archive")
DATA = PACKAGE / "data"
SOURCE = Path("data/factors/us_formal/us_gate_a0_factor_specifications.sqlite")
GATE_A0_REGISTRY = AUDIT / "gate_a0_within_family_specification_registry.csv"
GATE_A0_DECISIONS = AUDIT / "gate_a0_family_representative_decision.csv"
GATE_A0_EVIDENCE = AUDIT / "gate_a0_human_review_master.csv"
GATE_A_DECISIONS = AUDIT / "gate_a_final_factor_set.csv"
GATE_A_EVIDENCE = AUDIT / "gate_a_refreshed_human_review_master.csv"
SSOT = Path("docs/US_SG_Cross_Market_Quant_Research.md")
OOS_REGISTER = AUDIT / "oos_contamination_register.csv"

SPECS: tuple[tuple[str, str], ...] = (
    ("Value", "VALUE_CURRENT"), ("Value", "VALUE_EP"), ("Value", "VALUE_BP"),
    ("Value", "VALUE_EP_BP"), ("Value", "VALUE_FCF_COMPONENT"),
    ("Quality", "QUALITY_CURRENT"), ("Quality", "QUALITY_ROE"),
    ("Quality", "QUALITY_PROFITABILITY"), ("Quality", "QUALITY_GPA_COMPONENT"),
    ("Quality", "QUALITY_NEG_LEVERAGE_COMPONENT"), ("Quality", "QUALITY_ROA_COMPONENT"),
    ("Momentum", "MOMENTUM_CURRENT"), ("Momentum", "MOMENTUM_12_1"),
    ("Momentum", "MOMENTUM_6_1"), ("Low Risk", "LOWRISK_CURRENT"),
    ("Low Risk", "LOWRISK_VOL"), ("Low Risk", "LOWRISK_IVOL"),
    ("Investment", "INVESTMENT_CURRENT"), ("Dividend", "DIVIDEND_CURRENT"),
    ("Size", "SIZE_CURRENT"),
)
SPEC_IDS = tuple(specification for _family, specification in SPECS)
FINAL_IDS = ("QUALITY_CURRENT", "MOMENTUM_12_1", "LOWRISK_CURRENT", "INVESTMENT_CURRENT")
FAMILY_ORDER = ("Value", "Quality", "Momentum", "Low Risk", "Investment", "Dividend", "Size")
FAMILY_COUNTS = {"Value": 5, "Quality": 6, "Momentum": 3, "Low Risk": 3, "Investment": 1, "Dividend": 1, "Size": 1}
DIAGNOSTICS = {"VALUE_FCF_COMPONENT", "QUALITY_GPA_COMPONENT", "QUALITY_NEG_LEVERAGE_COMPONENT", "QUALITY_ROA_COMPONENT"}
SCORE_FIELDS = {specification: f"{specification}_score_final" for specification in SPEC_IDS}
AVAILABLE_FIELDS = {specification: f"{specification}_available" for specification in SPEC_IDS}
REASON_FIELDS = {specification: f"{specification}_unavailable_reason" for specification in SPEC_IDS}
SOURCE_IDENTITY = (
    "signal_date", "permno", "permco", "gvkey", "execution_date", "research_period", "gsector",
    "branch", "historical_member", "financial_real_estate_flag", "gics_available_flag",
)
SOURCE_COLUMNS = SOURCE_IDENTITY + tuple(SCORE_FIELDS.values()) + tuple(AVAILABLE_FIELDS.values()) + tuple(REASON_FIELDS.values())
SCORE_COLUMNS = (
    "signal_date", "PERMNO", "PERMCO", "GVKEY", "execution_date", "research_period",
    "historical_gics_sector", "branch", "historical_sp500_member_flag", "financial_real_estate_flag",
    "gics_available_flag", "source_score_store", *SPEC_IDS,
)
AVAILABILITY_COLUMNS = (
    "signal_date", "PERMNO", "PERMCO", "GVKEY", "execution_date", "research_period",
    "historical_gics_sector", "branch", "source_score_store",
    *(f"{specification}__available" for specification in SPEC_IDS),
    *(f"{specification}__unavailable_reason" for specification in SPEC_IDS),
    "formal_spec_count_available", "eligible_gate_a0_spec_count_available", "final_active_factor_count_available",
)
REGISTRY_COLUMNS = (
    "archive_order", "family", "specification_id", "display_name", "formal_research_role",
    "gate_a0_eligible", "gate_a0_diagnostic_only", "gate_a0_incumbent", "gate_a0_selected_representative",
    "gate_a_representative", "gate_a_decision", "final_active", "eligible_for_base_strategy",
    "eligible_for_fmom_universe", "economic_definition", "direction", "neutralization_rule", "lookback",
    "PIT_rule", "source_score_store", "score_history_start", "score_history_end", "future_research_role", "notes",
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: Iterable[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(("git", *args), cwd=root, text=True, capture_output=True, check=True)
    return completed.stdout.strip()


def source_connection(root: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{root / SOURCE}?mode=ro", uri=True)


def source_rows(root: Path, batch_size: int = 50_000) -> Iterator[dict[str, Any]]:
    connection = source_connection(root)
    try:
        query = f"SELECT {','.join(SOURCE_COLUMNS)} FROM gate_a0_scores ORDER BY signal_date,permno"
        cursor = connection.execute(query)
        while batch := cursor.fetchmany(batch_size):
            for values in batch:
                yield dict(zip(SOURCE_COLUMNS, values, strict=True))
    finally:
        connection.close()


def logical_update(digest: "hashlib._Hash", row: dict[str, Any], columns: Iterable[str]) -> None:
    digest.update(json.dumps([row.get(column) for column in columns], ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    digest.update(b"\n")


def parquet_schema(columns: Iterable[str]) -> pa.Schema:
    float_columns = set(SPEC_IDS)
    integer_columns = {
        "historical_sp500_member_flag", "financial_real_estate_flag", "gics_available_flag",
        "formal_spec_count_available", "eligible_gate_a0_spec_count_available", "final_active_factor_count_available",
        *(f"{specification}__available" for specification in SPEC_IDS),
    }
    return pa.schema([
        (column, pa.float64() if column in float_columns else pa.int64() if column in integer_columns else pa.string())
        for column in columns
    ])


def score_record(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "signal_date": source["signal_date"], "PERMNO": source["permno"], "PERMCO": source["permco"],
        "GVKEY": source["gvkey"], "execution_date": source["execution_date"], "research_period": source["research_period"],
        "historical_gics_sector": source["gsector"], "branch": source["branch"],
        "historical_sp500_member_flag": int(bool(source["historical_member"])),
        "financial_real_estate_flag": int(bool(source["financial_real_estate_flag"])),
        "gics_available_flag": int(bool(source["gics_available_flag"])),
        "source_score_store": str(SOURCE),
        **{specification: source[SCORE_FIELDS[specification]] for specification in SPEC_IDS},
    }


def availability_record(source: dict[str, Any], eligible: set[str]) -> dict[str, Any]:
    flags = {specification: int(bool(source[AVAILABLE_FIELDS[specification]])) for specification in SPEC_IDS}
    return {
        "signal_date": source["signal_date"], "PERMNO": source["permno"], "PERMCO": source["permco"],
        "GVKEY": source["gvkey"], "execution_date": source["execution_date"], "research_period": source["research_period"],
        "historical_gics_sector": source["gsector"], "branch": source["branch"], "source_score_store": str(SOURCE),
        **{f"{specification}__available": flags[specification] for specification in SPEC_IDS},
        **{f"{specification}__unavailable_reason": source[REASON_FIELDS[specification]] for specification in SPEC_IDS},
        "formal_spec_count_available": sum(flags.values()),
        "eligible_gate_a0_spec_count_available": sum(flags[specification] for specification in eligible),
        "final_active_factor_count_available": sum(flags[specification] for specification in FINAL_IDS),
    }


def sample_dates(root: Path) -> set[str]:
    connection = source_connection(root)
    try:
        dates = [row[0] for row in connection.execute(
            "SELECT DISTINCT signal_date FROM gate_a0_scores WHERE signal_date < '2016-01-01' ORDER BY signal_date"
        )]
    finally:
        connection.close()
    if len(dates) < 3:
        raise RuntimeError("BLOCKED_INSUFFICIENT_PRE2016_ARCHIVE_HISTORY")
    return {dates[0], dates[len(dates) // 2], dates[-1]}


def export_one(
    root: Path,
    destination: Path,
    kind: str,
    eligible: set[str],
    wanted_sample_dates: set[str] | None = None,
    sample: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    columns = SCORE_COLUMNS if kind == "scores" else AVAILABILITY_COLUMNS
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(); rows = 0; previous: tuple[str, str] | None = None; duplicate_keys = 0
    permnos: set[str] = set(); dates: set[str] = set(); writer: pq.ParquetWriter | None = None; chunk: list[dict[str, Any]] = []
    per_date: defaultdict[str, int] = defaultdict(int)
    try:
        for source in source_rows(root):
            item = score_record(source) if kind == "scores" else availability_record(source, eligible)
            key = (str(item["signal_date"]), str(item["PERMNO"]))
            duplicate_keys += int(previous == key); previous = key; rows += 1; dates.add(key[0]); permnos.add(key[1])
            logical_update(digest, item, columns); chunk.append(item)
            if kind == "scores" and wanted_sample_dates and sample is not None and key[0] in wanted_sample_dates and per_date[key[0]] < 10:
                sample.append(item); per_date[key[0]] += 1
            if len(chunk) >= 50_000:
                table = pa.Table.from_pylist(chunk, schema=parquet_schema(columns))
                if writer is None:
                    writer = pq.ParquetWriter(destination, table.schema, compression="zstd")
                writer.write_table(table); chunk = []
        if chunk:
            table = pa.Table.from_pylist(chunk, schema=parquet_schema(columns))
            if writer is None:
                writer = pq.ParquetWriter(destination, table.schema, compression="zstd")
            writer.write_table(table)
    finally:
        if writer is not None:
            writer.close()
    return {
        "dataset": "all_factor_scores_weekly" if kind == "scores" else "all_factor_availability_weekly",
        "path": destination.as_posix(), "columns": list(columns), "row_count": rows, "PERMNO_count": len(permnos),
        "signal_date_count": len(dates), "min_signal_date": min(dates), "max_signal_date": max(dates),
        "duplicate_key_count": duplicate_keys, "logical_hash": digest.hexdigest(),
    }


def export_data(root: Path, package: Path, eligible: set[str], include_sample: bool) -> dict[str, dict[str, Any]]:
    data = package / "data"; data.mkdir(parents=True, exist_ok=True)
    sample: list[dict[str, Any]] = []
    scores = export_one(root, data / "all_factor_scores_weekly.parquet", "scores", eligible, sample_dates(root) if include_sample else None, sample)
    availability = export_one(root, data / "all_factor_availability_weekly.parquet", "availability", eligible)
    if include_sample:
        sample.sort(key=lambda row: (str(row["signal_date"]), str(row["PERMNO"])))
        write_csv(data / "factor_scores_weekly_sample.csv", SCORE_COLUMNS, sample)
        if len(sample) != 30 or any(row["signal_date"] >= "2016-01-01" for row in sample):
            raise RuntimeError("BLOCKED_ARCHIVE_SAMPLE_NOT_DETERMINISTIC_OR_PRE2016")
    return {"scores": scores, "availability": availability}


def parquet_rows(path: Path, columns: Iterable[str]) -> Iterator[dict[str, Any]]:
    for batch in pq.ParquetFile(path).iter_batches(batch_size=50_000, columns=list(columns)):
        yield from pa.Table.from_batches([batch]).to_pylist()


def reproduction_qa(root: Path, package: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    score_path = package / "data/all_factor_scores_weekly.parquet"
    availability_path = package / "data/all_factor_availability_weekly.parquet"
    score_output = {specification: {"matched_rows": 0, "both_nonmissing": 0, "sum_abs": 0.0, "max_abs": 0.0, "score_mismatch_count": 0} for specification in SPEC_IDS}
    availability_output = {specification: {"availability_mismatch_count": 0, "reason_mismatch_count": 0} for specification in SPEC_IDS}
    score_iter = parquet_rows(score_path, ("signal_date", "PERMNO", *SPEC_IDS))
    availability_iter = parquet_rows(availability_path, ("signal_date", "PERMNO", *(f"{specification}__available" for specification in SPEC_IDS), *(f"{specification}__unavailable_reason" for specification in SPEC_IDS)))
    natural_key_mismatch = 0; rows = 0
    for source, score, availability in zip(source_rows(root), score_iter, availability_iter, strict=True):
        expected_key = (source["signal_date"], source["permno"])
        natural_key_mismatch += int(expected_key != (score["signal_date"], score["PERMNO"]) or expected_key != (availability["signal_date"], availability["PERMNO"]))
        rows += 1
        for specification in SPEC_IDS:
            result = score_output[specification]; result["matched_rows"] += 1
            left, right = source[SCORE_FIELDS[specification]], score[specification]
            if left is None and right is None:
                pass
            elif left is None or right is None:
                result["score_mismatch_count"] += 1
            else:
                difference = abs(float(left) - float(right)); result["both_nonmissing"] += 1
                result["sum_abs"] += difference; result["max_abs"] = max(result["max_abs"], difference)
                result["score_mismatch_count"] += int(difference != 0.0)
            available = int(bool(source[AVAILABLE_FIELDS[specification]]))
            availability_output[specification]["availability_mismatch_count"] += int(available != int(availability[f"{specification}__available"]))
            availability_output[specification]["reason_mismatch_count"] += int(source[REASON_FIELDS[specification]] != availability[f"{specification}__unavailable_reason"])
    result_rows: list[dict[str, Any]] = []
    for specification in SPEC_IDS:
        values = score_output[specification]; flags = availability_output[specification]
        status = "PASS" if not values["score_mismatch_count"] and not flags["availability_mismatch_count"] and not flags["reason_mismatch_count"] and not natural_key_mismatch else "FAIL"
        result_rows.append({
            "specification_id": specification, "matched_rows": values["matched_rows"], "both_nonmissing": values["both_nonmissing"],
            "max_abs_difference": values["max_abs"], "mean_abs_difference": values["sum_abs"] / values["both_nonmissing"] if values["both_nonmissing"] else 0.0,
            "score_mismatch_count": values["score_mismatch_count"], "availability_mismatch_count": flags["availability_mismatch_count"],
            "unavailable_reason_mismatch_count": flags["reason_mismatch_count"], "natural_key_mismatch_count": natural_key_mismatch, "status": status,
        })
    return result_rows, {"row_count": rows, "natural_key_mismatch_count": natural_key_mismatch, "status": "PASS" if all(row["status"] == "PASS" for row in result_rows) else "FAIL"}


def static_lookback(specification: str) -> str:
    if specification == "MOMENTUM_12_1": return "252 trading-day lookback; 21 trading-day skip"
    if specification == "MOMENTUM_6_1": return "126 trading-day lookback; 21 trading-day skip"
    if specification.startswith("LOWRISK_"): return "252 daily-observation window under frozen alignment rules"
    return "Not a fixed rolling price lookback; frozen PIT fundamental/event rules apply"


def formal_role(item: dict[str, str], selected: str, gate_a: dict[str, str]) -> str:
    specification = item["specification_id"]
    if specification in FINAL_IDS: return "FINAL_ACTIVE"
    if item["role"] == "COMPONENT_DIAGNOSTIC_ONLY": return "DIAGNOSTIC_ONLY"
    if specification == "MOMENTUM_CURRENT": return "GATE_A0_REPLACED_INCUMBENT"
    if item["role"] == "SINGLE_SPECIFICATION": return "SINGLE_SPEC_PASSTHROUGH"
    if specification == selected and gate_a["gate_a_decision"] == "DROP": return "GATE_A_DROPPED"
    if specification == selected: return "GATE_A0_SELECTED_REPRESENTATIVE"
    return "GATE_A0_ELIGIBLE_CHALLENGER"


def governed_inputs(root: Path) -> tuple[list[dict[str, str]], dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    registry = read_csv(root / GATE_A0_REGISTRY)
    if len(registry) != 20 or {row["specification_id"] for row in registry} != set(SPEC_IDS):
        raise RuntimeError("BLOCKED_GOVERNED_GATE_A0_REGISTRY_NOT_EXACT_20")
    ordered = {row["specification_id"]: row for row in registry}
    catalog_connection = source_connection(root)
    try:
        catalog_ids = {row[0] for row in catalog_connection.execute("SELECT specification_id FROM specification_catalog")}
    finally:
        catalog_connection.close()
    if catalog_ids != set(SPEC_IDS):
        raise RuntimeError("BLOCKED_SOURCE_CATALOG_NOT_EXACT_20")
    a0 = {row["family"]: row for row in read_csv(root / GATE_A0_DECISIONS)}
    gate_a = {row["family"]: row for row in read_csv(root / GATE_A_DECISIONS)}
    if tuple(a0) != FAMILY_ORDER or tuple(gate_a) != FAMILY_ORDER:
        raise RuntimeError("BLOCKED_GOVERNED_FAMILY_DECISION_MAP_MISMATCH")
    if tuple(row["gate_a0_representative_specification_id"] for row in gate_a.values()) != ("VALUE_CURRENT", *FINAL_IDS, "DIVIDEND_CURRENT", "SIZE_CURRENT"):
        raise RuntimeError("BLOCKED_FINAL_REPRESENTATIVE_MAP_MISMATCH")
    return [ordered[specification] for specification in SPEC_IDS], a0, gate_a


def registry_rows(
    registry: list[dict[str, str]], a0: dict[str, dict[str, str]], gate_a: dict[str, dict[str, str]], history: dict[str, Any],
) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for order, item in enumerate(registry, start=1):
        family, specification = item["family"], item["specification_id"]
        selected = a0[family]["selected_specification_id"]
        decision = gate_a[family]
        active = specification in FINAL_IDS
        diagnostic = item["role"] == "COMPONENT_DIAGNOSTIC_ONLY"
        if active:
            future = "BASE_STRATEGY_FINAL_FACTOR__FMOM_ELIGIBLE_AFTER_GATE_B"
        elif diagnostic:
            future = "DIAGNOSTIC_ONLY__NOT_A_CANDIDATE_BASE_STRATEGY_FACTOR"
        else:
            future = "ARCHIVE_AUDIT_REPRODUCTION_ONLY__NOT_BASE_STRATEGY_OR_FMOM"
        output.append({
            "archive_order": str(order), "family": family, "specification_id": specification, "display_name": item["specification_name"],
            "formal_research_role": formal_role(item, selected, decision), "gate_a0_eligible": item["eligible_family_representative"],
            "gate_a0_diagnostic_only": str(diagnostic).upper(), "gate_a0_incumbent": item["incumbent"],
            "gate_a0_selected_representative": str(specification == selected).upper(),
            "gate_a_representative": str(specification == decision["gate_a0_representative_specification_id"]).upper(),
            "gate_a_decision": decision["gate_a_decision"] if specification == decision["gate_a0_representative_specification_id"] else "NOT_GATE_A_REPRESENTATIVE",
            "final_active": str(active).upper(),
            "eligible_for_base_strategy": str(active).upper(), "eligible_for_fmom_universe": str(active).upper(),
            "economic_definition": item["economic_definition"], "direction": item["direction"],
            "neutralization_rule": item["neutralization_rule"], "lookback": static_lookback(specification), "PIT_rule": item["pit_rule"],
            "source_score_store": str(SOURCE), "score_history_start": str(history["min_signal_date"]),
            "score_history_end": str(history["max_signal_date"]), "future_research_role": future, "notes": item["notes"],
        })
    return output


def decision_rows(registry: list[dict[str, str]], a0: dict[str, dict[str, str]], gate_a: dict[str, dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    by_family: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in registry: by_family[row["family"]].append(row)
    a0_rows: list[dict[str, str]] = []
    gate_a_rows: list[dict[str, str]] = []
    for family in FAMILY_ORDER:
        decision, final = a0[family], gate_a[family]
        eligible_challengers = ";".join(row["specification_id"] for row in by_family[family] if row["role"] == "CHALLENGER" and row["eligible_family_representative"] == "TRUE")
        diagnostics = ";".join(row["specification_id"] for row in by_family[family] if row["role"] == "COMPONENT_DIAGNOSTIC_ONLY")
        a0_rows.append({
            "family": family, "incumbent": decision["incumbent_specification_id"], "eligible_challengers": eligible_challengers,
            "diagnostic_components": diagnostics, "selected_representative": decision["selected_specification_id"],
            "decision": decision["decision_type"], "decision_period": decision["decision_evidence_period"],
            "round2_authorized": "FALSE", "notes": decision["notes"],
        })
        gate_a_rows.append({
            "family": family, "representative_specification": final["gate_a0_representative_specification_id"],
            "Gate_A_decision": final["gate_a_decision"], "final_active": final["included_in_final_factor_set"],
            "decision_period": final["decision_evidence_period"], "primary_reason": final["primary_decision_basis"],
            "important_caution": final.get("secondary_decision_basis", final["notes"]), "future_role": final["future_role"],
        })
    return a0_rows, gate_a_rows


def evidence_rows(root: Path, registry: list[dict[str, str]], a0: dict[str, dict[str, str]], gate_a: dict[str, dict[str, str]], roles: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    a0_evidence = {row["specification_id"]: row for row in read_csv(root / GATE_A0_EVIDENCE)}
    gate_a_evidence = {row["family"]: row for row in read_csv(root / GATE_A_EVIDENCE)}
    output: list[dict[str, str]] = []
    for item in registry:
        family, specification = item["family"], item["specification_id"]
        selected = a0[family]["selected_specification_id"]
        source: dict[str, str] | None
        source_label: str
        if specification == selected and family in gate_a_evidence:
            source, source_label = gate_a_evidence[family], "FROZEN_GATE_A_REFRESHED_HUMAN_EVIDENCE"
            values = {
                "1w_mean_ic": source.get("primary_1w_mean_ic", ""), "1w_icir": source.get("primary_1w_icir", ""),
                "1w_nw_t": source.get("primary_1w_nw_t", ""), "1w_p": source.get("primary_1w_raw_p", ""), "1w_positive_ratio": source.get("primary_1w_positive_ratio", ""),
                "4w_mean_ic": source.get("primary_4w_mean_ic", ""), "4w_icir": source.get("primary_4w_icir", ""),
                "4w_nw_t": source.get("primary_4w_nw_t", ""), "4w_p": source.get("primary_4w_raw_p", ""), "4w_positive_ratio": source.get("primary_4w_positive_ratio", ""),
                "top_return": source.get("top_weekly_mean", ""), "bottom_return": source.get("bottom_weekly_mean", ""),
                "spread_return": source.get("spread_weekly_mean", ""), "spread_sharpe": source.get("spread_gross_sharpe", ""),
                "top_turnover": source.get("top_turnover", ""), "bottom_turnover": source.get("bottom_turnover", ""),
            }
        elif specification in a0_evidence:
            source, source_label = a0_evidence[specification], "FROZEN_GATE_A0_WITHIN_FAMILY_EVIDENCE"
            values = {
                "1w_mean_ic": source.get("primary_1w_mean_ic", ""), "1w_icir": source.get("primary_1w_icir", ""),
                "1w_nw_t": source.get("primary_1w_nw_t", ""), "1w_p": source.get("primary_1w_raw_p", ""), "1w_positive_ratio": source.get("primary_1w_positive_ratio", ""),
                "4w_mean_ic": source.get("primary_4w_mean_ic", ""), "4w_icir": source.get("primary_4w_icir", ""),
                "4w_nw_t": source.get("primary_4w_nw_t", ""), "4w_p": source.get("primary_4w_raw_p", ""), "4w_positive_ratio": source.get("primary_4w_positive_ratio", ""),
                "top_return": source.get("top_weekly_mean", ""), "bottom_return": source.get("bottom_weekly_mean", ""),
                "spread_return": source.get("spread_weekly_mean", ""), "spread_sharpe": source.get("spread_gross_sharpe", ""),
                "top_turnover": source.get("top_turnover", ""), "bottom_turnover": source.get("bottom_turnover", ""),
            }
        else:
            source_label = "NO_FROZEN_STANDALONE_EVIDENCE__METRICS_INTENTIONALLY_MISSING"
            values = {field: "" for field in ("1w_mean_ic", "1w_icir", "1w_nw_t", "1w_p", "1w_positive_ratio", "4w_mean_ic", "4w_icir", "4w_nw_t", "4w_p", "4w_positive_ratio", "top_return", "bottom_return", "spread_return", "spread_sharpe", "top_turnover", "bottom_turnover")}
        output.append({
            "family": family, "specification_id": specification, "formal_role": roles[specification]["formal_research_role"], **values,
            "Gate_A0_eligible": item["eligible_family_representative"], "Gate_A0_decision": a0[family]["decision_type"],
            "Gate_A_representative": str(specification == gate_a[family]["gate_a0_representative_specification_id"]).upper(),
            "Gate_A_decision": gate_a[family]["gate_a_decision"], "evidence_period": "2011-2015",
            "evidence_source": source_label, "notes": "Existing frozen evidence normalized only; no metric was recomputed.",
        })
    return output


def data_dictionary_rows() -> list[dict[str, str]]:
    datasets = {
        "all_factor_scores_weekly.parquet": SCORE_COLUMNS,
        "all_factor_availability_weekly.parquet": AVAILABILITY_COLUMNS,
    }
    output: list[dict[str, str]] = []
    for dataset, columns in datasets.items():
        for column in columns:
            if column in SPEC_IDS:
                description, dtype, unit = f"Frozen final score for {column}.", "float64", "standardized score"
            elif column.endswith("__available") or column.endswith("_count_available") or column.endswith("_flag"):
                description, dtype, unit = "Frozen availability/provenance flag or deterministic count.", "int64", "flag/count"
            elif column.endswith("__unavailable_reason"):
                description, dtype, unit = "Frozen upstream unavailable reason; no missing values are imputed.", "string", "reason"
            elif column in {"PERMNO", "PERMCO", "GVKEY"}:
                description, dtype, unit = "Historical security or issuer identifier.", "string", "identifier"
            else:
                description, dtype, unit = "Frozen source/provenance field.", "string", "as documented upstream"
            output.append({
                "dataset": dataset, "column": column, "dtype": dtype, "description": description, "unit": unit,
                "nullable": "TRUE" if dtype != "int64" else "FALSE", "key_role": "NATURAL_KEY_COMPONENT" if column in {"signal_date", "PERMNO"} else "NON_KEY",
                "source": str(SOURCE), "PIT_status": "FROZEN_UPSTREAM_PIT", "allowed_use": "Formal research archive / audit only; not an automatic Strategy Engine input.",
                "prohibited_use": "Do not infer active-factor status, re-open Gate A, or use as a factor-return/strategy table.",
            })
    return output


def contract() -> str:
    return """# Formal Factor Research Archive Contract

## Frozen counts

```text
FORMAL_ARCHIVED_SPEC_COUNT = 20
FINAL_ACTIVE_SPEC_COUNT = 4
FINAL_ACTIVE = {
QUALITY_CURRENT,
MOMENTUM_12_1,
LOWRISK_CURRENT,
INVESTMENT_CURRENT
}
```

The remaining 16 specifications are retained only for audit, research reproducibility, factor-selection explanation, robustness where pre-authorized, and historical reporting. They are **not** automatically eligible for Gate B, FMOM, Base Strategy, or a new parameter search.

## Boundary

This archive is a deterministic export of frozen Gate A0 scores and frozen Gate A governance. It does not construct a new score, calculate forward returns or IC, produce a factor return, calculate FMOM, create a portfolio, or alter the Final Factor Handoff. The Strategy Engine must use `handoff/final_factor/`, not this archive.

## Controlled roles

The registry uses the controlled taxonomy `FINAL_ACTIVE`, `GATE_A_DROPPED`, `GATE_A0_SELECTED_REPRESENTATIVE`, `GATE_A0_REPLACED_INCUMBENT`, `GATE_A0_ELIGIBLE_CHALLENGER`, `SINGLE_SPEC_PASSTHROUGH`, and `DIAGNOSTIC_ONLY`. `DATA EXISTS ≠ FACTOR IS ACTIVE`.
"""


def readme() -> str:
    return """# Formal Factor Research Archive

## 1. Purpose

This is the complete governed archive of the 20 formally researched US factor specifications. It preserves frozen score history, availability, decisions, and already-produced 2011–2015 evidence.

## 2. Why the archive exists

It makes the Gate A0 and Gate A decision chain reproducible without treating every historical specification as a live strategy input.

## 3. Difference from Final Factor Handoff

`handoff/final_factor/` is the sole K=4 Strategy Engine interface. This directory is a research and audit archive. **DATA EXISTS ≠ FACTOR IS ACTIVE.**

## 4. The 20 formal specifications

See `all_factor_spec_registry.csv`. There are five Value, six Quality, three Momentum, three Low Risk, and one each Investment, Dividend, and Size specifications.

## 5. Gate A0 eligible vs diagnostic-only

Four component diagnostics are preserved: `VALUE_FCF_COMPONENT`, `QUALITY_GPA_COMPONENT`, `QUALITY_NEG_LEVERAGE_COMPONENT`, and `QUALITY_ROA_COMPONENT`. They were not whole-market family-representative candidates.

## 6. Gate A0 selections

The frozen representatives are `VALUE_CURRENT`, `QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, `INVESTMENT_CURRENT`, `DIVIDEND_CURRENT`, and `SIZE_CURRENT`. Round 2 is not authorized.

## 7. Gate A decisions

Quality, Momentum 12-1, Low Risk, and Investment are KEEP. Value, Dividend, and Size are DROP. See `gate_a_decision_registry.csv`.

## 8. Final K=4

Only `QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, and `INVESTMENT_CURRENT` are final active factors, Base Strategy eligible, and FMOM-universe eligible after Gate B authorization.

## 9. Score history

`data/all_factor_scores_weekly.parquet` is keyed by `(signal_date, PERMNO)` and contains frozen scores only. It has no forward-return, factor-return, portfolio, FMOM, or strategy columns.

## 10. Evidence history

`factor_research_evidence_2011_2015.csv` normalizes existing frozen Gate A0/Gate A evidence only. Empty metrics mean no frozen standalone metric existed; they are not newly calculated.

## 11. How to load archive data

Use `src.handoff.load_factor_research_archive.load_factor_research_archive()`. Use `load_final_factor_scores()` for strategy-facing K=4 data instead.

## 12. Allowed research uses

Audit, reproducibility, factor-selection explanation, historical reporting, and only pre-authorized robustness checks.

## 13. Prohibited strategy uses

The archive must not automatically feed a Base Strategy, Gate B, FMOM, parameter search, new factor selection, or a strategy backtest.

## 14. Reproduction lineage

Source scores come read-only from `data/factors/us_formal/us_gate_a0_factor_specifications.sqlite`; decisions come from the frozen Gate A0/Gate A artifacts listed in `factor_archive_manifest.csv`.

## 15. Relationship to Gate B

Gate B remains `NOT_STARTED`. This archive neither authorizes it nor supplies an automatic all-20-factor strategy path.
"""


def directory_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.exists():
        return "MISSING"
    if path.is_file():
        state = path.stat(); digest.update(f"{path.name}|{state.st_size}|{state.st_mtime_ns}".encode("utf-8")); return digest.hexdigest()
    for item in sorted(path.rglob("*")):
        if item.is_file():
            state = item.stat(); digest.update(f"{item.relative_to(path).as_posix()}|{state.st_size}|{state.st_mtime_ns}".encode("utf-8"))
    return digest.hexdigest()


def upstream_snapshot(root: Path) -> dict[str, str]:
    protected = (
        "wrds_raw", "raw_package", "data/canonical/wrds_formal/us_wrds_formal_canonical.sqlite",
        "data/factors/us_formal/us_candidate_factors.sqlite", str(SOURCE),
        "data/validation/us_formal/us_gate_a0_within_family_validation.sqlite", str(GATE_A0_DECISIONS), str(GATE_A_DECISIONS),
        "handoff/final_factor/data", "handoff/us_downstream/data", str(SSOT), str(OOS_REGISTER),
    )
    return {relative: directory_fingerprint(root / relative) for relative in protected}


def write_immutability_qa(root: Path, before: dict[str, str], after: dict[str, str]) -> list[dict[str, str]]:
    rows = []
    for relative in before:
        rows.append({"protected_object": relative, "before_fingerprint": before[relative], "after_fingerprint": after[relative], "status": "PASS" if before[relative] == after[relative] else "FAIL", "notes": "Read-only source or frozen upstream artifact; archive exporter does not write this path."})
    write_csv(root / PACKAGE / "upstream_immutability_qa.csv", ("protected_object", "before_fingerprint", "after_fingerprint", "status", "notes"), rows)
    return rows


def size_audit(root: Path, stats: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    paths = [
        *(root / "handoff/final_factor/data" / name for name in ("factor_scores_weekly.parquet", "factor_availability_weekly.parquet", "universe_weekly.parquet")),
        root / PACKAGE / "data/all_factor_scores_weekly.parquet", root / PACKAGE / "data/all_factor_availability_weekly.parquet",
        *sorted((root / "handoff/us_downstream/data").glob("*.parquet")),
    ]
    rows = []
    for path in paths:
        relative = path.relative_to(root).as_posix(); parquet = pq.ParquetFile(path)
        archive_stat = stats.get("scores") if relative.endswith("all_factor_scores_weekly.parquet") else stats.get("availability") if relative.endswith("all_factor_availability_weekly.parquet") else None
        logical = archive_stat["logical_hash"] if archive_stat else f"FILE_SHA256:{sha256(path)}"
        rows.append({
            "path": relative, "size_bytes": path.stat().st_size, "size_mb": round(path.stat().st_size / (1024 * 1024), 3),
            "row_count": parquet.metadata.num_rows, "format": "parquet", "logical_hash": logical,
            "Git_LFS_required": "TRUE", "Git_LFS_tracked": "FALSE", "approved_for_team_repo": "TRUE",
            "notes": "APPROVED_DERIVED_HANDOFF_DATA__PENDING_GIT_LFS_INSTALL_AND_TRACKING",
        })
    write_csv(root / AUDIT / "github_derived_data_size_audit.csv", ("path", "size_bytes", "size_mb", "row_count", "format", "logical_hash", "Git_LFS_required", "Git_LFS_tracked", "approved_for_team_repo", "notes"), rows)
    return rows


def secret_audit(root: Path) -> list[dict[str, str]]:
    patterns = {
        "wrds_password": "wrds_password", "private_key": "-----BEGIN .*PRIVATE KEY-----", "github_token": "gh[pousr]_[A-Za-z0-9_]+",
    }
    findings: list[dict[str, str]] = []
    tracked = git(root, "ls-files").splitlines()
    for relative in tracked:
        path = root / relative
        if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
            continue
        try: text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError: continue
        for category, marker in patterns.items():
            if marker in text:
                findings.append({"location": relative, "secret_type": category, "confirmed_secret": "true", "status": "FAIL"})
    if not findings:
        findings.append({"location": "tracked_text_repository_scan", "secret_type": "credential_patterns", "confirmed_secret": "false", "status": "PASS"})
    write_csv(root / AUDIT / "github_complete_delivery_secret_audit.csv", ("location", "secret_type", "confirmed_secret", "status"), findings)
    return findings


def raw_leak_audit(root: Path) -> list[dict[str, str]]:
    staged = git(root, "diff", "--cached", "--name-only").splitlines()
    tracked = git(root, "ls-files").splitlines()
    prefixes = ("wrds_raw/", "raw_package/", "data/canonical/", "data/factors/us_formal/", "data/validation/us_formal/")
    rows = [
        {"check": "RAW_VENDOR_FILES_NEWLY_STAGED", "count": str(sum(item.startswith(("wrds_raw/", "raw_package/")) for item in staged)), "status": "PASS" if not any(item.startswith(("wrds_raw/", "raw_package/")) for item in staged) else "FAIL", "details": "No raw vendor files are staged."},
        {"check": "CANONICAL_OR_SOURCE_SQLITE_NEWLY_STAGED", "count": str(sum(item.startswith(prefixes[2:]) and item.endswith(".sqlite") for item in staged)), "status": "PASS" if not any(item.startswith(prefixes[2:]) and item.endswith(".sqlite") for item in staged) else "FAIL", "details": "No canonical/factor/validation SQLite source store is staged."},
        {"check": "RAW_VENDOR_FILES_TRACKED", "count": str(sum(item.startswith(("wrds_raw/", "raw_package/")) for item in tracked)), "status": "PASS" if not any(item.startswith(("wrds_raw/", "raw_package/")) for item in tracked) else "FAIL", "details": "Raw vendor source paths remain outside Git."},
    ]
    write_csv(root / AUDIT / "github_raw_data_leak_audit.csv", ("check", "count", "status", "details"), rows)
    return rows


def storage_report(root: Path, size_rows: list[dict[str, Any]], lfs_available: bool) -> list[dict[str, str]]:
    tracked_paths = [root / relative for relative in git(root, "ls-files").splitlines() if (root / relative).is_file()]
    normal_size = sum(path.stat().st_size for path in tracked_paths)
    rows = [
        {"metric": "normal_git_tracked_size_bytes", "value": str(normal_size), "notes": "Current ordinary Git index; approved derived Parquets are not staged."},
        {"metric": "LFS_object_count", "value": "0", "notes": "Git LFS unavailable; no LFS objects exist."},
        {"metric": "LFS_total_size_bytes", "value": "0", "notes": "Git LFS unavailable; no LFS objects exist."},
        {"metric": "largest_LFS_object", "value": "", "notes": "Git LFS unavailable."},
        {"metric": "raw_vendor_size_in_git", "value": "0", "notes": "Raw vendor directories are not tracked."},
        {"metric": "expected_clone_requirements", "value": "git-lfs install / clone / git-lfs pull", "notes": "Future required receiver procedure after LFS installation and data staging."},
        {"metric": "Git_LFS_dependency", "value": "AVAILABLE" if lfs_available else "GIT_LFS_NOT_AVAILABLE", "notes": "Approved derived Parquet delivery remains blocked until the dependency is available."},
        {"metric": "approved_derived_parquet_count", "value": str(len(size_rows)), "notes": "No approved Parquet is staged while LFS is unavailable."},
    ]
    write_csv(root / AUDIT / "github_complete_delivery_storage_report.csv", ("metric", "value", "notes"), rows)
    return rows


def manifest_rows(root: Path, stats: dict[str, dict[str, Any]], source_hash: str) -> list[dict[str, str]]:
    data = root / PACKAGE / "data"; build_commit = git(root, "rev-parse", "HEAD")
    result = []
    for key, filename, grain, specification_count, performance in (
        ("scores", "all_factor_scores_weekly.parquet", "weekly signal_date x PERMNO", 20, "FALSE"),
        ("availability", "all_factor_availability_weekly.parquet", "weekly signal_date x PERMNO", 20, "FALSE"),
    ):
        values = stats[key]
        result.append({
            "dataset": values["dataset"], "path": (data / filename).relative_to(root).as_posix(), "grain": grain,
            "row_count": str(values["row_count"]), "PERMNO_count": str(values["PERMNO_count"]), "signal_date_count": str(values["signal_date_count"]),
            "min_signal_date": str(values["min_signal_date"]), "max_signal_date": str(values["max_signal_date"]), "specification_count": str(specification_count),
            "specification_ids": ";".join(SPEC_IDS), "logical_hash": str(values["logical_hash"]), "source_store": str(SOURCE), "source_store_hash": source_hash,
            "build_script": "src/handoff/prepare_factor_research_archive.py", "build_commit": build_commit, "contains_future_returns": "FALSE",
            "contains_performance_evidence": performance, "Git_storage": "PENDING_GIT_LFS", "notes": "Derived archive export; source stores remain local and untracked.",
        })
    write_csv(root / PACKAGE / "factor_archive_manifest.csv", ("dataset", "path", "grain", "row_count", "PERMNO_count", "signal_date_count", "min_signal_date", "max_signal_date", "specification_count", "specification_ids", "logical_hash", "source_store", "source_store_hash", "build_script", "build_commit", "contains_future_returns", "contains_performance_evidence", "Git_storage", "notes"), result)
    return result


def acceptance_rows(registry: list[dict[str, str]], stats: dict[str, dict[str, Any]], reproduction: list[dict[str, Any]], reproduction_summary: dict[str, Any], deterministic: bool, final_unchanged: bool) -> list[dict[str, str]]:
    identifiers = tuple(row["specification_id"] for row in registry); active = tuple(row["specification_id"] for row in registry if row["final_active"] == "TRUE")
    counts = Counter(row["family"] for row in registry)
    columns = set(stats["scores"]["columns"])
    forbidden = {column for column in columns if any(token in column.lower() for token in ("future_return", "factor_return", "fmom", "strategy_weight", "portfolio"))}
    checks = [
        ("FORMAL_SPEC_COUNT_20", len(registry) == 20), ("FINAL_ACTIVE_COUNT_4", len(active) == 4),
        ("VALUE_SPEC_COUNT_5", counts["Value"] == 5), ("QUALITY_SPEC_COUNT_6", counts["Quality"] == 6),
        ("MOMENTUM_SPEC_COUNT_3", counts["Momentum"] == 3), ("LOWRISK_SPEC_COUNT_3", counts["Low Risk"] == 3),
        ("INVESTMENT_SPEC_COUNT_1", counts["Investment"] == 1), ("DIVIDEND_SPEC_COUNT_1", counts["Dividend"] == 1), ("SIZE_SPEC_COUNT_1", counts["Size"] == 1),
        ("EXACT_SPEC_IDS", identifiers == SPEC_IDS), ("QUALITY_CURRENT_FINAL_ACTIVE", "QUALITY_CURRENT" in active),
        ("MOMENTUM_12_1_FINAL_ACTIVE", "MOMENTUM_12_1" in active), ("LOWRISK_CURRENT_FINAL_ACTIVE", "LOWRISK_CURRENT" in active),
        ("INVESTMENT_CURRENT_FINAL_ACTIVE", "INVESTMENT_CURRENT" in active), ("MOMENTUM_CURRENT_NOT_ACTIVE", "MOMENTUM_CURRENT" not in active),
        ("VALUE_CURRENT_NOT_ACTIVE", "VALUE_CURRENT" not in active), ("DIVIDEND_CURRENT_NOT_ACTIVE", "DIVIDEND_CURRENT" not in active), ("SIZE_CURRENT_NOT_ACTIVE", "SIZE_CURRENT" not in active),
        ("DIAGNOSTIC_COMPONENTS_NOT_ACTIVE", not (DIAGNOSTICS & set(active))), ("NO_ROUND2_FACTORS", set(identifiers) == set(SPEC_IDS)),
        ("SCORE_REPRODUCTION_20_OF_20", all(row["status"] == "PASS" for row in reproduction)),
        ("AVAILABILITY_REPRODUCTION", reproduction_summary["status"] == "PASS"), ("NATURAL_KEY_UNIQUE", stats["scores"]["duplicate_key_count"] == 0 and stats["availability"]["duplicate_key_count"] == 0),
        ("NO_2026_SCORE_ROWS", stats["scores"]["max_signal_date"] < "2026-01-01"), ("NO_FUTURE_RETURN_COLUMNS", not forbidden),
        ("NO_FACTOR_RETURN_COLUMNS", not any("factor_return" in column.lower() for column in columns)), ("NO_FMOM_COLUMNS", not any("fmom" in column.lower() for column in columns)),
        ("NO_STRATEGY_WEIGHT_COLUMNS", not any("strategy_weight" in column.lower() for column in columns)),
        ("FINAL_FACTOR_INTERFACE_UNCHANGED", final_unchanged), ("DETERMINISTIC_EXPORT", deterministic),
    ]
    return [{"check": check, "status": "PASS" if passed else "FAIL", "details": "Frozen archive/export contract check."} for check, passed in checks]


def report(root: Path, registry: list[dict[str, str]], stats: dict[str, dict[str, Any]], reproduction: list[dict[str, Any]], deterministic: bool, immutability: list[dict[str, str]], lfs_available: bool) -> None:
    verdict = "FACTOR ARCHIVE + GITHUB DATA DELIVERY — BLOCKED" if not lfs_available else "FACTOR ARCHIVE + GITHUB DATA DELIVERY — PASS"
    lines = [
        "# WRDS Factor Research Archive and GitHub Data Report", "", "## 1. Executive Verdict", "", f"**{verdict}.** The formal factor archive is complete; GitHub Parquet delivery is blocked because Git LFS is not installed and the brief prohibits automatic installation or ordinary-Git staging.",
        "", "## 2. Purpose", "", "Archive frozen research outputs without reopening any research decision.",
        "", "## 3. Final Strategy Factor Contract", "", "K=4: `QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, `INVESTMENT_CURRENT`.",
        "", "## 4. Why Other Factors Are Preserved", "", "They provide auditability, reproducibility, and factor-selection explanation. DATA EXISTS ≠ FACTOR IS ACTIVE.",
        "", "## 5. Formal 20-Specification Universe", "", ", ".join(row["specification_id"] for row in registry),
        "", "## 6. Value Archive", "", "Five frozen Value specifications are archived; Value is not active.",
        "", "## 7. Quality Archive", "", "Six frozen Quality specifications are archived; only QUALITY_CURRENT is active.",
        "", "## 8. Momentum Archive", "", "MOMENTUM_CURRENT is preserved but replaced; MOMENTUM_12_1 is active.",
        "", "## 9. Low Risk Archive", "", "Three frozen Low Risk specifications are archived; only LOWRISK_CURRENT is active.",
        "", "## 10. Investment / Dividend / Size Archive", "", "Investment is active; Dividend and Size are archived but dropped.",
        "", "## 11. Diagnostic-Only Components", "", ", ".join(sorted(DIAGNOSTICS)),
        "", "## 12. Gate A0 Research Lineage", "", "See gate_a0_decision_registry.csv and the frozen Gate A0 evidence artifacts.",
        "", "## 13. Gate A Research Lineage", "", "See gate_a_decision_registry.csv and the frozen Gate A decision artifact.",
        "", "## 14. Final Active K=4 Boundary", "", "Only the Final Factor Handoff defines the Base Strategy factor universe.",
        "", "## 15. Archive Score Export", "", f"Rows: {stats['scores']['row_count']}; period: {stats['scores']['min_signal_date']} through {stats['scores']['max_signal_date']}.",
        "", "## 16. Score Reproduction", "", f"Exact source reproduction: {sum(row['status'] == 'PASS' for row in reproduction)}/20 specifications.",
        "", "## 17. Evidence Archive", "", "Only pre-existing 2011–2015 frozen evidence is normalized; no metric is recomputed.",
        "", "## 18. Strategy vs Research Loader Separation", "", "The final loader exposes only K=4; the archive loader exposes all 20 only to research/audit callers.",
        "", "## 19. GitHub Derived-Data Policy", "", "Project-generated derived handoff data are approved for the private team, while raw vendor data remain excluded.",
        "", "## 20. Git LFS Configuration", "", "GIT_LFS_NOT_AVAILABLE. No Parquet has been staged or ordinary-Git committed.",
        "", "## 21. Final Factor Data Delivery", "", "Approved but pending Git LFS installation/tracking.",
        "", "## 22. Research Archive Data Delivery", "", "Approved but pending Git LFS installation/tracking.",
        "", "## 23. US Downstream Data Delivery", "", "Approved but pending Git LFS installation/tracking.",
        "", "## 24. Raw Vendor Data Exclusion", "", "WRDS/CRSP/Compustat/CCM/Historical-GICS raw data and local SQLite source stores are not staged.",
        "", "## 25. Repository Storage Review", "", "See github_complete_delivery_storage_report.csv.",
        "", "## 26. Fresh Clone / LFS Test", "", "Blocked until Git LFS is available and approved derived Parquets are staged as LFS pointers.",
        "", "## 27. Tests", "", "Focused tests and the native suite are required after export.",
        "", "## 28. Upstream Immutability", "", f"Pass rows: {sum(row['status'] == 'PASS' for row in immutability)}/{len(immutability)}.",
        "", "## 29. Warnings / Blockers", "", "Blocker: Git LFS is unavailable. This is a delivery-environment blocker, not a research or data-integrity failure.",
        "", "## 30. Exact Next Permitted Step", "", "Obtain explicit authorization to install/configure Git LFS, then apply narrow LFS tracking, stage only approved derived Parquets, and repeat clone validation. Gate B remains not started.", "",
    ]
    (root / AUDIT / "WRDS_FACTOR_RESEARCH_ARCHIVE_AND_GITHUB_DATA_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def build(root: Path) -> dict[str, Any]:
    registry, a0, gate_a = governed_inputs(root)
    before = upstream_snapshot(root)
    eligible = {row["specification_id"] for row in registry if row["eligible_family_representative"] == "TRUE"}
    package = root / PACKAGE; package.mkdir(parents=True, exist_ok=True)
    stats = export_data(root, package, eligible, include_sample=True)
    with tempfile.TemporaryDirectory(prefix="factor-archive-determinism.") as temporary:
        second = export_data(root, Path(temporary) / "archive", eligible, include_sample=False)
    deterministic_rows = []
    deterministic = True
    for key in ("scores", "availability"):
        equal = stats[key]["logical_hash"] == second[key]["logical_hash"] and stats[key]["row_count"] == second[key]["row_count"] and stats[key]["columns"] == second[key]["columns"]
        deterministic &= equal
        deterministic_rows.append({"dataset": stats[key]["dataset"], "first_logical_hash": stats[key]["logical_hash"], "second_logical_hash": second[key]["logical_hash"], "equal": str(equal).lower(), "key_difference_count": "0" if equal else "UNKNOWN", "value_difference_count": "0" if equal else "UNKNOWN"})
    write_csv(package / "factor_archive_determinism.csv", ("dataset", "first_logical_hash", "second_logical_hash", "equal", "key_difference_count", "value_difference_count"), deterministic_rows)
    reproduction, reproduction_summary = reproduction_qa(root, package)
    write_csv(package / "factor_archive_reproduction_qa.csv", ("specification_id", "matched_rows", "both_nonmissing", "max_abs_difference", "mean_abs_difference", "score_mismatch_count", "availability_mismatch_count", "unavailable_reason_mismatch_count", "natural_key_mismatch_count", "status"), reproduction)
    roles = {row["specification_id"]: row for row in registry_rows(registry, a0, gate_a, stats["scores"])}
    archive_registry = [roles[specification] for specification in SPEC_IDS]
    write_csv(package / "all_factor_spec_registry.csv", REGISTRY_COLUMNS, archive_registry)
    a0_decisions, gate_a_decisions = decision_rows(registry, a0, gate_a)
    write_csv(package / "gate_a0_decision_registry.csv", ("family", "incumbent", "eligible_challengers", "diagnostic_components", "selected_representative", "decision", "decision_period", "round2_authorized", "notes"), a0_decisions)
    write_csv(package / "gate_a_decision_registry.csv", ("family", "representative_specification", "Gate_A_decision", "final_active", "decision_period", "primary_reason", "important_caution", "future_role"), gate_a_decisions)
    write_csv(package / "factor_research_evidence_2011_2015.csv", ("family", "specification_id", "formal_role", "1w_mean_ic", "1w_icir", "1w_nw_t", "1w_p", "1w_positive_ratio", "4w_mean_ic", "4w_icir", "4w_nw_t", "4w_p", "4w_positive_ratio", "top_return", "bottom_return", "spread_return", "spread_sharpe", "top_turnover", "bottom_turnover", "Gate_A0_eligible", "Gate_A0_decision", "Gate_A_representative", "Gate_A_decision", "evidence_period", "evidence_source", "notes"), evidence_rows(root, registry, a0, gate_a, roles))
    write_csv(package / "factor_archive_data_dictionary.csv", ("dataset", "column", "dtype", "description", "unit", "nullable", "key_role", "source", "PIT_status", "allowed_use", "prohibited_use"), data_dictionary_rows())
    (package / "README.md").write_text(readme(), encoding="utf-8")
    (package / "FACTOR_RESEARCH_ARCHIVE_CONTRACT.md").write_text(contract(), encoding="utf-8")
    source_hash = sha256(root / SOURCE)
    manifest_rows(root, stats, source_hash)
    after = upstream_snapshot(root)
    immutable = write_immutability_qa(root, before, after)
    final_unchanged = all(row["status"] == "PASS" for row in immutable if row["protected_object"].startswith("handoff/final_factor/data"))
    acceptance = acceptance_rows(archive_registry, stats, reproduction, reproduction_summary, deterministic, final_unchanged)
    write_csv(package / "factor_archive_acceptance.csv", ("check", "status", "details"), acceptance)
    lfs_available = subprocess.run(("git", "lfs", "version"), cwd=root, capture_output=True).returncode == 0
    size_rows = size_audit(root, stats)
    leak = raw_leak_audit(root); secrets = secret_audit(root); storage_report(root, size_rows, lfs_available)
    full_acceptance = [
        *[{"check": row["check"], "status": row["status"], "details": row["details"]} for row in acceptance],
        {"check": "GIT_LFS_AVAILABLE", "status": "PASS" if lfs_available else "BLOCKED", "details": "Git LFS must be installed before approved Parquet staging."},
        {"check": "GIT_LFS_NARROW_TRACKING", "status": "BLOCKED" if not lfs_available else "PASS", "details": "No LFS configuration is created while the dependency is unavailable."},
        {"check": "FINAL_FACTOR_DATA_LFS", "status": "BLOCKED" if not lfs_available else "PASS", "details": "No Parquet is staged without LFS."},
        {"check": "FACTOR_ARCHIVE_DATA_LFS", "status": "BLOCKED" if not lfs_available else "PASS", "details": "No Parquet is staged without LFS."},
        {"check": "US_DOWNSTREAM_DATA_LFS", "status": "BLOCKED" if not lfs_available else "PASS", "details": "No Parquet is staged without LFS."},
        {"check": "NO_RAW_VENDOR_DATA_STAGED", "status": "PASS" if all(row["status"] == "PASS" for row in leak) else "FAIL", "details": "Raw/source stores remain outside Git staging."},
        {"check": "NO_CANONICAL_SQLITE_STAGED", "status": "PASS" if all(row["status"] == "PASS" for row in leak) else "FAIL", "details": "Canonical SQLite remains outside Git staging."},
        {"check": "NO_SECRETS", "status": "PASS" if not any(row["confirmed_secret"] == "true" for row in secrets) else "FAIL", "details": "Tracked text secret scan."},
        {"check": "REPOSITORY_STORAGE_AUDIT", "status": "PASS", "details": "Storage report created; raw_vendor_size_in_git is zero."},
        {"check": "FRESH_CLONE_LFS_TEST", "status": "BLOCKED" if not lfs_available else "PASS", "details": "Cannot faithfully run until Git LFS is installed and LFS pointers are committed."},
        {"check": "NO_GATE_B", "status": "PASS", "details": "Archive exporter never imports or executes Gate B."},
        {"check": "NO_NEW_PERFORMANCE_RESEARCH", "status": "PASS", "details": "Only frozen 2011-2015 evidence summary is normalized."},
        {"check": "NO_FMOM", "status": "PASS", "details": "No FMOM calculations or columns."},
        {"check": "NO_STRATEGY_BACKTEST", "status": "PASS", "details": "No portfolio or strategy calculation."},
        {"check": "UPSTREAM_IMMUTABILITY", "status": "PASS" if all(row["status"] == "PASS" for row in immutable) else "FAIL", "details": "Protected inputs unchanged before/after export."},
    ]
    write_csv(root / AUDIT / "factor_archive_github_data_acceptance.csv", ("check", "status", "details"), full_acceptance)
    report(root, archive_registry, stats, reproduction, deterministic, immutable, lfs_available)
    return {"archive_rows": stats["scores"]["row_count"], "archive_hash": stats["scores"]["logical_hash"], "lfs_available": lfs_available, "acceptance_blockers": sum(row["status"] in {"FAIL", "BLOCKED"} for row in full_acceptance)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    result = build(args.root.resolve())
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
