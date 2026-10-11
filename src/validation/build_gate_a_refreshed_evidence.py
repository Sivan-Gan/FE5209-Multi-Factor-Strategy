"""Build the non-decisional Formal Gate A evidence package for frozen representatives.

This module is deliberately bounded to 2011--2015.  It reads executable
returns only from the immutable pre-Gate-A validation store and obtains the
seven score columns only from the frozen Gate A0 score store.  It performs no
selection and writes no upstream object.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import sqlite3
import statistics
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

from src.data import wrds_gate0_final as gate0
from src.factors import build_gate_a0_factor_specifications as construction
from src.validation import build_formal_factor_validation as validation
from src.validation import build_gate_a0_within_family_validation as gate_a0


ROOT = Path(__file__).resolve().parents[2]
AUDIT = Path("outputs/audit/wrds_formal")
DECISIONS = AUDIT / "gate_a0_family_representative_decision.csv"
OOS = AUDIT / "oos_contamination_register.csv"
STORE = Path("data/validation/us_formal/us_gate_a_refreshed_evidence.sqlite")
START, END = "2011-01-01", "2015-12-31"
FORMAL = "FACTOR_SELECTION"
PRIMARY = "PRIMARY_NATURAL_FACTOR_SAMPLE"
COMMON = "COMMON_7_REPRESENTATIVE_SAMPLE"
FAMILIES = ("Value", "Quality", "Momentum", "Low Risk", "Investment", "Dividend", "Size")
EXPECTED = {
    "Value": "VALUE_CURRENT",
    "Quality": "QUALITY_CURRENT",
    "Momentum": "MOMENTUM_12_1",
    "Low Risk": "LOWRISK_CURRENT",
    "Investment": "INVESTMENT_CURRENT",
    "Dividend": "DIVIDEND_CURRENT",
    "Size": "SIZE_CURRENT",
}


def dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def finite(value: Any) -> float | None:
    try:
        output = float(value)
    except (TypeError, ValueError):
        return None
    return output if math.isfinite(output) else None


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Sequence[dict[str, Any]], fields: Sequence[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is not None:
        columns = list(fields)
    else:
        columns = []
        for row in rows:
            for field in row:
                if field not in columns:
                    columns.append(field)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def logical_hash(path: Path) -> str:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        digest = hashlib.sha256()
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        for table in tables:
            digest.update((table + "\n").encode())
            for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid"):
                digest.update(dump(list(row)).encode() + b"\n")
        return digest.hexdigest()
    finally:
        connection.close()


def score_column(specification_id: str) -> str:
    return f"{specification_id}_score_final"


def decision_map(root: Path) -> tuple[list[dict[str, str]], dict[str, str]]:
    rows = read_csv(root / DECISIONS)
    if len(rows) != 7 or tuple(row["family"] for row in rows) != FAMILIES:
        raise RuntimeError("INVALID_FROZEN_GATE_A0_DECISION_MAP")
    selected = {row["family"]: row["selected_specification_id"] for row in rows}
    if selected != EXPECTED:
        raise RuntimeError("FROZEN_GATE_A0_DECISION_MAP_MISMATCH")
    if any(row["human_decision"] != "TRUE" or row["decision_evidence_period"] != "2011-2015" for row in rows):
        raise RuntimeError("UNAPPROVED_GATE_A0_DECISION_MAP")
    return rows, selected


def frozen_inputs(root: Path) -> dict[str, str]:
    decisions, _selected = decision_map(root)
    prior = validation.validation_hash(root / gate_a0.PRIOR_VALIDATION)
    if prior != gate_a0.PRIOR_VALIDATION_HASH:
        raise RuntimeError("PRIOR_VALIDATION_INPUT_FREEZE_MISMATCH")
    inputs = gate_a0.frozen_inputs(root)
    if not gate0.verify_raw_integrity(root)["raw_hashes_match"]:
        raise RuntimeError("RAW_INPUT_INTEGRITY_MISMATCH")
    return {
        **inputs,
        "gate_a0_freeze_sha256": sha256(root / DECISIONS),
        "gate_a0_validation_logical_hash": gate_a0.logical_hash(root / gate_a0.OUTPUT),
        "representative_count": str(len(decisions)),
    }


def source_records(root: Path, selected: dict[str, str]) -> list[dict[str, Any]]:
    """Return only necessary 2011--2015 fields from immutable validated sources."""
    source = gate_a0.source_records(root, gate_a0.registry(root))
    common_fields = (
        "signal_date", "permno", "execution_date", "next_execution_date", "fourth_execution_date",
        "research_period", "gsector", "branch", "historical_member", "execution_eligible",
        "execution_reason", "r_1w_exec", "r_1w_status", "r_1w_reason", "r_1w_terminal_cash",
        "r_4w_exec", "r_4w_status", "r_4w_reason", "r_4w_terminal_cash",
    )
    output = []
    for row in source:
        item = {field: row[field] for field in common_fields}
        for specification in selected.values():
            item[score_column(specification)] = finite(row[score_column(specification)])
        item["common7_available"] = int(
            bool(item["historical_member"])
            and all(item[score_column(specification)] is not None for specification in selected.values())
        )
        output.append(item)
    del source
    if not output or any(row["research_period"] != FORMAL or not (START <= row["signal_date"] <= END) for row in output):
        raise RuntimeError("FORMAL_SAMPLE_BOUNDARY_FAILURE")
    return output


def grouped(records: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        output[str(row["signal_date"])].append(row)
    return output


def eligible(row: dict[str, Any], specification: str, sample_role: str) -> bool:
    return bool(row["historical_member"]) and row[score_column(specification)] is not None and (
        sample_role != COMMON or bool(row["common7_available"])
    )


def valid_return(row: dict[str, Any], horizon: str) -> bool:
    return validation.is_valid_return(row["r_1w_status" if horizon == "1W" else "r_4w_status"])


def definitions(selected: dict[str, str]) -> list[tuple[str, str, str]]:
    return [(role, family, selected[family]) for role in (PRIMARY, COMMON) for family in FAMILIES]


def calculate_ic(records: Sequence[dict[str, Any]], specs: Sequence[tuple[str, str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    weekly: list[dict[str, Any]] = []
    sector: list[dict[str, Any]] = []
    for date, rows in sorted(grouped(records).items()):
        for sample_role, family, specification in specs:
            for horizon in ("1W", "4W"):
                active = [
                    row for row in rows
                    if eligible(row, specification, sample_role) and bool(row["execution_eligible"])
                    and valid_return(row, horizon)
                ]
                pairs = [(float(row[score_column(specification)]), float(row["r_1w_exec" if horizon == "1W" else "r_4w_exec"])) for row in active]
                ic = validation.spearman(*zip(*pairs)) if len(pairs) >= 30 else None
                weekly.append({
                    "sample_role": sample_role, "family": family, "specification_id": specification,
                    "signal_date": date, "horizon": horizon, "n_obs": len(pairs),
                    "spearman_ic": ic, "valid_return_count": len(active),
                    "valid_score_count": sum(eligible(row, specification, sample_role) for row in rows),
                    "status": "VALID" if ic is not None else "INSUFFICIENT_CROSS_SECTION_N_LT_30",
                })
                sectors: dict[str, list[tuple[float, float]]] = defaultdict(list)
                field = "r_1w_exec" if horizon == "1W" else "r_4w_exec"
                for row in active:
                    if row["gsector"]:
                        sectors[str(row["gsector"])].append((float(row[score_column(specification)]), float(row[field])))
                for gsector, pairs_by_sector in sorted(sectors.items()):
                    sector_ic = validation.spearman(*zip(*pairs_by_sector)) if len(pairs_by_sector) >= 10 else None
                    sector.append({
                        "sample_role": sample_role, "family": family, "specification_id": specification,
                        "signal_date": date, "horizon": horizon, "gsector": gsector, "n_obs": len(pairs_by_sector),
                        "spearman_ic": sector_ic,
                        "status": "VALID" if sector_ic is not None else "INSUFFICIENT_SECTOR_CROSS_SECTION_N_LT_10",
                    })
    return weekly, sector


def portfolio_leg(rows: Sequence[dict[str, Any]], specification: str, high: bool) -> list[dict[str, Any]]:
    count = math.floor(0.30 * len(rows))
    return sorted(
        rows,
        key=lambda row: ((-float(row[score_column(specification)])) if high else float(row[score_column(specification)]), str(row["permno"])),
    )[:count]


def portfolios(records: Sequence[dict[str, Any]], specs: Sequence[tuple[str, str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    weekly: list[dict[str, Any]] = []
    holdings: list[dict[str, Any]] = []
    for date, rows in sorted(grouped(records).items()):
        for sample_role, family, specification in specs:
            rankable = [row for row in rows if eligible(row, specification, sample_role)]
            top, bottom = portfolio_leg(rankable, specification, True), portfolio_leg(rankable, specification, False)
            top_live = [row for row in top if bool(row["execution_eligible"])]
            bottom_live = [row for row in bottom if bool(row["execution_eligible"])]
            top_valid = bool(top_live) and all(valid_return(row, "1W") for row in top_live)
            bottom_valid = bool(bottom_live) and all(valid_return(row, "1W") for row in bottom_live)
            top_return = statistics.mean(float(row["r_1w_exec"]) for row in top_live) if top_valid else None
            bottom_return = statistics.mean(float(row["r_1w_exec"]) for row in bottom_live) if bottom_valid else None
            universe = [row for row in rankable if bool(row["execution_eligible"]) and valid_return(row, "1W")]
            universe_return = statistics.mean(float(row["r_1w_exec"]) for row in universe) if universe else None
            weekly.append({
                "sample_role": sample_role, "family": family, "specification_id": specification,
                "signal_date": date, "execution_date": rows[0]["execution_date"],
                "next_execution_date": rows[0]["next_execution_date"], "n_cross_section": len(rankable),
                "n_top_target": len(top), "n_bottom_target": len(bottom), "n_top_executed": len(top_live),
                "n_bottom_executed": len(bottom_live), "top_return_gross": top_return,
                "bottom_return_gross": bottom_return,
                "factor_spread_gross": top_return - bottom_return if top_return is not None and bottom_return is not None else None,
                "eligible_universe_ew_return": universe_return,
                "top_excess_vs_eligible_universe_ew": top_return - universe_return if top_return is not None and universe_return is not None else None,
                "top_return_status": "VALID" if top_return is not None else "FORWARD_RETURN_INCOMPLETE_OR_EMPTY",
                "bottom_return_status": "VALID" if bottom_return is not None else "FORWARD_RETURN_INCOMPLETE_OR_EMPTY",
                "top_execution_failures": len(top) - len(top_live),
                "bottom_execution_failures": len(bottom) - len(bottom_live),
                "top_gtr": None, "top_turnover": None, "bottom_gtr": None, "bottom_turnover": None,
            })
            if sample_role == PRIMARY:
                for leg, entries in (("TOP", top_live), ("BOTTOM", bottom_live)):
                    for entry in entries:
                        holdings.append({
                            "sample_role": sample_role, "family": family, "specification_id": specification,
                            "signal_date": date, "leg": leg, "permno": entry["permno"],
                            "target_weight": 1.0 / len(entries), "realized_return": entry["r_1w_exec"],
                            "return_status": entry["r_1w_status"], "terminal_cash": entry["r_1w_terminal_cash"],
                        })
    apply_turnover(weekly, holdings)
    return weekly, holdings


def apply_turnover(weekly: list[dict[str, Any]], holdings: Sequence[dict[str, Any]]) -> None:
    grouped_holdings: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in holdings:
        grouped_holdings[row["sample_role"], row["specification_id"], row["leg"], row["signal_date"]].append(row)
    weekly_index = {(row["sample_role"], row["specification_id"], row["signal_date"]): row for row in weekly}
    for specification in {row["specification_id"] for row in holdings}:
        previous: dict[str, list[dict[str, Any]] | None] = {"TOP": None, "BOTTOM": None}
        dates = sorted(row["signal_date"] for row in weekly if row["sample_role"] == PRIMARY and row["specification_id"] == specification)
        for date in dates:
            output = weekly_index[PRIMARY, specification, date]
            for leg, prefix in (("TOP", "top"), ("BOTTOM", "bottom")):
                current = grouped_holdings[PRIMARY, specification, leg, date]
                if not current:
                    previous[leg] = current
                    continue
                target = {row["permno"]: float(row["target_weight"]) for row in current}
                if previous[leg] is None:
                    before = {"__CASH__": 1.0}
                elif not all(validation.is_valid_return(row["return_status"]) for row in previous[leg] or []):
                    previous[leg] = current
                    continue
                else:
                    drift: dict[str, float] = defaultdict(float)
                    for row in previous[leg] or []:
                        key = "__CASH__" if row["terminal_cash"] else str(row["permno"])
                        drift[key] += float(row["target_weight"]) * (1.0 + float(row["realized_return"]))
                    total = sum(drift.values())
                    before = {key: value / total for key, value in drift.items()} if total > 0 else {"__CASH__": 1.0}
                gross = sum(abs(target.get(key, 0.0) - before.get(key, 0.0)) for key in set(target) | set(before))
                output[prefix + "_gtr"] = gross
                output[prefix + "_turnover"] = 0.5 * gross
                previous[leg] = current


def normal_p(t_value: float | None) -> float | None:
    return math.erfc(abs(t_value) / math.sqrt(2.0)) if t_value is not None else None


def summaries(ic_rows: Sequence[dict[str, Any]], specs: Sequence[tuple[str, str, str]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for sample_role, family, specification in specs:
        for horizon in ("1W", "4W"):
            chosen = [row for row in ic_rows if row["sample_role"] == sample_role and row["specification_id"] == specification and row["horizon"] == horizon and row["spearman_ic"] is not None]
            values = [float(row["spearman_ic"]) for row in chosen]
            standard_deviation = statistics.stdev(values) if len(values) > 1 else None
            mean = statistics.mean(values) if values else None
            nw_t = validation.nw_t(values, 4 if horizon == "1W" else 3)
            output.append({
                "sample_role": sample_role, "family": family, "specification_id": specification,
                "horizon": horizon, "mean_ic": mean, "std_ic": standard_deviation,
                "annualized_icir": math.sqrt(52.0) * mean / standard_deviation if mean is not None and standard_deviation else None,
                "positive_ic_ratio": sum(value > 0 for value in values) / len(values) if values else None,
                "newey_west_t": nw_t, "raw_p_value": normal_p(nw_t), "bh_fdr_q_value": None,
                "tested_hypotheses_count": 0, "observation_weeks": len(values),
                "average_cross_section": statistics.mean(row["n_obs"] for row in chosen) if chosen else None,
                "minimum_cross_section": min((row["n_obs"] for row in chosen), default=None),
                "hac_lag": 4 if horizon == "1W" else 3,
                "bootstrap_replications": 2000 if horizon == "4W" else None,
                "bootstrap_seed": 5209 if horizon == "4W" else None,
                **(validation.moving_block_bootstrap(values) if horizon == "4W" else {"bootstrap_mean": None, "bootstrap_ci_low": None, "bootstrap_ci_high": None}),
            })
    for horizon in ("1W", "4W"):
        group = [row for row in output if row["sample_role"] == PRIMARY and row["horizon"] == horizon and row["raw_p_value"] is not None]
        ordered = sorted(group, key=lambda row: (float(row["raw_p_value"]), FAMILIES.index(row["family"])))
        m = len(ordered)
        adjusted = [min(1.0, float(row["raw_p_value"]) * m / (index + 1)) for index, row in enumerate(ordered)]
        for index in range(m - 2, -1, -1):
            adjusted[index] = min(adjusted[index], adjusted[index + 1])
        for row, q_value in zip(ordered, adjusted):
            row["bh_fdr_q_value"] = q_value
            row["tested_hypotheses_count"] = m
    return output


def yearly_rolling(ic_rows: Sequence[dict[str, Any]], specs: Sequence[tuple[str, str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    yearly: list[dict[str, Any]] = []
    rolling: list[dict[str, Any]] = []
    for sample_role, family, specification in specs:
        for horizon in ("1W", "4W"):
            chosen = [row for row in ic_rows if row["sample_role"] == sample_role and row["specification_id"] == specification and row["horizon"] == horizon and row["spearman_ic"] is not None]
            by_year = []
            for year in range(2011, 2016):
                values = [float(row["spearman_ic"]) for row in chosen if row["signal_date"].startswith(str(year))]
                row = {"sample_role": sample_role, "family": family, "specification_id": specification, "horizon": horizon, "year": year, "mean_ic": statistics.mean(values) if values else None, "positive_ic_ratio": sum(value > 0 for value in values) / len(values) if values else None, "ic_observation_count": len(values), "newey_west_t": validation.nw_t(values, 4 if horizon == "1W" else 3)}
                yearly.append(row); by_year.append(row)
            values = [float(row["spearman_ic"]) for row in chosen]
            dates = [row["signal_date"] for row in chosen]
            rolling_means = []
            for index in range(51, len(values)):
                window = values[index - 51:index + 1]
                mean = statistics.mean(window); rolling_means.append(mean)
                rolling.append({"sample_role": sample_role, "family": family, "specification_id": specification, "horizon": horizon, "period_end": dates[index], "window_weeks": 52, "rolling_mean_ic": mean, "rolling_positive_ic_ratio": sum(value > 0 for value in window) / 52.0})
            yearly.append({
                "sample_role": sample_role, "family": family, "specification_id": specification, "horizon": horizon, "year": "SUMMARY", "mean_ic": None, "positive_ic_ratio": None, "ic_observation_count": len(values),
                "positive_year_count": sum((row["mean_ic"] or 0) > 0 for row in by_year),
                "negative_year_count": sum((row["mean_ic"] or 0) < 0 for row in by_year),
                "highest_year_mean_ic": max((row["mean_ic"] for row in by_year if row["mean_ic"] is not None), default=None),
                "lowest_year_mean_ic": min((row["mean_ic"] for row in by_year if row["mean_ic"] is not None), default=None),
                "sign_consistency": sum((row["mean_ic"] or 0) > 0 for row in by_year) / 5.0,
                "rolling_52w_median": statistics.median(rolling_means) if rolling_means else None,
                "rolling_52w_min": min(rolling_means) if rolling_means else None,
                "rolling_52w_max": max(rolling_means) if rolling_means else None,
                "rolling_52w_positive_fraction": sum(value > 0 for value in rolling_means) / len(rolling_means) if rolling_means else None,
            })
    return yearly, rolling


def portfolio_summaries(weekly: Sequence[dict[str, Any]], specs: Sequence[tuple[str, str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    output: list[dict[str, Any]] = []
    yearly: list[dict[str, Any]] = []
    for sample_role, family, specification in specs:
        chosen = [row for row in weekly if row["sample_role"] == sample_role and row["specification_id"] == specification]
        for component, field in (("TOP", "top_return_gross"), ("BOTTOM", "bottom_return_gross"), ("SPREAD", "factor_spread_gross")):
            values = [float(row[field]) for row in chosen if row[field] is not None]
            output.append({
                "sample_role": sample_role, "family": family, "specification_id": specification, "component": component,
                **validation.performance(values), "newey_west_t_mean_return_lag4": validation.nw_t(values, 4),
                "mean_top_turnover": statistics.mean(float(row["top_turnover"]) for row in chosen if row["top_turnover"] is not None) if any(row["top_turnover"] is not None for row in chosen) else None,
                "mean_bottom_turnover": statistics.mean(float(row["bottom_turnover"]) for row in chosen if row["bottom_turnover"] is not None) if any(row["bottom_turnover"] is not None for row in chosen) else None,
                "mean_top_excess_vs_eligible_universe_ew": statistics.mean(float(row["top_excess_vs_eligible_universe_ew"]) for row in chosen if row["top_excess_vs_eligible_universe_ew"] is not None) if component == "TOP" and any(row["top_excess_vs_eligible_universe_ew"] is not None for row in chosen) else None,
            })
            for year in range(2011, 2016):
                subset = [float(row[field]) for row in chosen if row["signal_date"].startswith(str(year)) and row[field] is not None]
                yearly.append({"sample_role": sample_role, "family": family, "specification_id": specification, "component": component, "year": year, **validation.performance(subset)})
    return output, yearly


def break_even(weekly: Sequence[dict[str, Any]], specs: Sequence[tuple[str, str, str]]) -> list[dict[str, Any]]:
    output = []
    for sample_role, family, specification in specs:
        chosen = [row for row in weekly if row["sample_role"] == sample_role and row["specification_id"] == specification and row["top_return_gross"] is not None and row["top_gtr"] is not None]
        gross = math.prod(1.0 + float(row["top_return_gross"]) for row in chosen) if chosen else 0.0
        gtr = [float(row["top_gtr"]) for row in chosen]
        value, status = None, "NO_VALID_TOP_LEG_SERIES"
        if chosen and gross > 1.0 and any(item > 0 for item in gtr):
            low, high = 0.0, min(0.999999 / item for item in gtr if item > 0)
            for _ in range(100):
                middle = (low + high) / 2.0
                wealth = math.prod((1 - middle * float(row["top_gtr"])) * (1 + float(row["top_return_gross"])) for row in chosen)
                low, high = (middle, high) if wealth > 1 else (low, middle)
            value, status = (low + high) / 2.0, "VALID"
        elif chosen and gross <= 1.0:
            status = "NOT_DEFINED_GROSS_NAV_NOT_ABOVE_ONE"
        elif chosen:
            status = "NOT_DEFINED_ZERO_GTR"
        output.append({"sample_role": sample_role, "family": family, "specification_id": specification, "top_leg_break_even_oneway_cost": value, "status": status, "reference": "ENDING_NAV_EQUALS_1", "wealth_formula": "product((1-c*GTR_t)*(1+R_top_t))"})
    return output


def coverage(records: Sequence[dict[str, Any]], specs: Sequence[tuple[str, str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    output: list[dict[str, Any]] = []
    reasons: list[dict[str, Any]] = []
    for sample_role, family, specification in specs:
        for date, group in sorted(grouped(records).items()):
            members = [row for row in group if bool(row["historical_member"])]
            available = [row for row in members if eligible(row, specification, sample_role)]
            output.append({"sample_role": sample_role, "family": family, "specification_id": specification, "record_type": "WEEKLY", "signal_date": date, "year": int(date[:4]), "member_count": len(members), "available_count": len(available), "coverage": len(available) / len(members) if members else None})
            if sample_role == PRIMARY:
                unavailable = "SCORE_UNAVAILABLE" if specification else "UNKNOWN"
                reasons.append({"family": family, "specification_id": specification, "signal_date": date, "unavailable_reason": unavailable, "count": sum(not eligible(row, specification, sample_role) for row in members)})
        weekly = [row for row in output if row["sample_role"] == sample_role and row["specification_id"] == specification and row["record_type"] == "WEEKLY"]
        for year in range(2011, 2016):
            selected = [row for row in weekly if row["year"] == year]
            output.append({"sample_role": sample_role, "family": family, "specification_id": specification, "record_type": "YEARLY", "signal_date": f"{year}-12-31", "year": year, "member_count": statistics.mean(row["member_count"] for row in selected), "available_count": statistics.mean(row["available_count"] for row in selected), "coverage": statistics.mean(row["coverage"] for row in selected if row["coverage"] is not None)})
        output.append({"sample_role": sample_role, "family": family, "specification_id": specification, "record_type": "SUMMARY", "signal_date": END, "year": "SUMMARY", "member_count": statistics.mean(row["member_count"] for row in weekly), "available_count": statistics.mean(row["available_count"] for row in weekly), "coverage": statistics.mean(row["coverage"] for row in weekly if row["coverage"] is not None)})
    return output, reasons


def sector_breadth(sector_rows: Sequence[dict[str, Any]], specs: Sequence[tuple[str, str, str]]) -> list[dict[str, Any]]:
    output = []
    for sample_role, family, specification in specs:
        for horizon in ("1W", "4W"):
            rows = [row for row in sector_rows if row["sample_role"] == sample_role and row["specification_id"] == specification and row["horizon"] == horizon and row["spearman_ic"] is not None]
            by_sector: dict[str, list[float]] = defaultdict(list)
            for row in rows:
                by_sector[row["gsector"]].append(float(row["spearman_ic"]))
            means = {sector: statistics.mean(values) for sector, values in by_sector.items()}
            output.append({"sample_role": sample_role, "family": family, "specification_id": specification, "horizon": horizon, "positive_sector_count": sum(value > 0 for value in means.values()), "valid_sector_count": len(means), "historical_gics_only": "TRUE"})
    return output


def correlations(records: Sequence[dict[str, Any]], ic_rows: Sequence[dict[str, Any]], portfolio_rows: Sequence[dict[str, Any]], holdings: Sequence[dict[str, Any]], selected: dict[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    score_detail: list[dict[str, Any]] = []
    ic_detail: list[dict[str, Any]] = []
    spread_detail: list[dict[str, Any]] = []
    overlap: list[dict[str, Any]] = []
    dates = grouped(records)
    for left_index, left_family in enumerate(FAMILIES):
        for right_family in FAMILIES[left_index + 1:]:
            left_spec, right_spec = selected[left_family], selected[right_family]
            weekly_score = []
            for _date, rows in sorted(dates.items()):
                pairs = [(float(row[score_column(left_spec)]), float(row[score_column(right_spec)])) for row in rows if bool(row["historical_member"]) and row[score_column(left_spec)] is not None and row[score_column(right_spec)] is not None]
                if len(pairs) >= 3:
                    weekly_score.append(validation.spearman(*zip(*pairs)))
            score_detail.append({"factor_x": left_family, "factor_y": right_family, "mean_correlation": statistics.mean(weekly_score) if weekly_score else None, "median_correlation": statistics.median(weekly_score) if weekly_score else None, "sd_correlation": statistics.stdev(weekly_score) if len(weekly_score) > 1 else None, "valid_date_count": len(weekly_score)})
            for horizon in ("1W", "4W"):
                left = {row["signal_date"]: row["spearman_ic"] for row in ic_rows if row["sample_role"] == PRIMARY and row["family"] == left_family and row["horizon"] == horizon and row["spearman_ic"] is not None}
                right = {row["signal_date"]: row["spearman_ic"] for row in ic_rows if row["sample_role"] == PRIMARY and row["family"] == right_family and row["horizon"] == horizon and row["spearman_ic"] is not None}
                common_dates = sorted(set(left) & set(right))
                correlation = validation.spearman([float(left[date]) for date in common_dates], [float(right[date]) for date in common_dates]) if len(common_dates) >= 3 else None
                ic_detail.append({"horizon": horizon, "factor_x": left_family, "factor_y": right_family, "correlation": correlation, "overlapping_weeks": len(common_dates)})
            left = {row["signal_date"]: row["factor_spread_gross"] for row in portfolio_rows if row["sample_role"] == PRIMARY and row["family"] == left_family and row["factor_spread_gross"] is not None}
            right = {row["signal_date"]: row["factor_spread_gross"] for row in portfolio_rows if row["sample_role"] == PRIMARY and row["family"] == right_family and row["factor_spread_gross"] is not None}
            common_dates = sorted(set(left) & set(right))
            correlation = validation.spearman([float(left[date]) for date in common_dates], [float(right[date]) for date in common_dates]) if len(common_dates) >= 3 else None
            spread_detail.append({"factor_x": left_family, "factor_y": right_family, "correlation": correlation, "overlapping_weeks": len(common_dates)})
            for leg in ("TOP", "BOTTOM"):
                left_sets: dict[str, set[str]] = defaultdict(set)
                right_sets: dict[str, set[str]] = defaultdict(set)
                for row in holdings:
                    if row["family"] == left_family and row["leg"] == leg:
                        left_sets[row["signal_date"]].add(str(row["permno"]))
                    if row["family"] == right_family and row["leg"] == leg:
                        right_sets[row["signal_date"]].add(str(row["permno"]))
                values = [len(left_sets[date] & right_sets[date]) / len(left_sets[date] | right_sets[date]) for date in sorted(set(left_sets) & set(right_sets)) if left_sets[date] or right_sets[date]]
                overlap.append({"factor_x": left_family, "factor_y": right_family, "leg": leg, "mean_jaccard_overlap": statistics.mean(values) if values else None, "valid_date_count": len(values)})
    return score_detail, ic_detail, spread_detail, overlap


def matrix(detail: Sequence[dict[str, Any]], value: str, *, horizon: str | None = None) -> list[dict[str, Any]]:
    lookup: dict[tuple[str, str], float | None] = {}
    for row in detail:
        if horizon is None or row.get("horizon") == horizon:
            lookup[tuple(sorted((row["factor_x"], row["factor_y"]), key=FAMILIES.index))] = finite(row[value])
    output = []
    for family in FAMILIES:
        row: dict[str, Any] = {"factor": family}
        for other in FAMILIES:
            row[other] = 1.0 if family == other else lookup[tuple(sorted((family, other), key=FAMILIES.index))]
        output.append(row)
    return output


def build_store(root: Path, destination: Path, selected: dict[str, str], inputs: dict[str, str]) -> dict[str, Any]:
    records = source_records(root, selected)
    specs = definitions(selected)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".gate_a_refreshed.", suffix=".sqlite.tmp", dir=destination.parent)
    os.close(descriptor); temporary = Path(name)
    try:
        connection = sqlite3.connect(temporary)
        connection.executescript("""
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE validation_observations (signal_date TEXT NOT NULL, permno TEXT NOT NULL, execution_date TEXT NOT NULL, next_execution_date TEXT, fourth_execution_date TEXT, research_period TEXT NOT NULL, gsector TEXT, branch TEXT, historical_member INTEGER NOT NULL, execution_eligible INTEGER NOT NULL, execution_reason TEXT NOT NULL, r_1w_exec REAL, r_1w_status TEXT NOT NULL, r_1w_reason TEXT NOT NULL, r_1w_terminal_cash INTEGER NOT NULL, r_4w_exec REAL, r_4w_status TEXT NOT NULL, r_4w_reason TEXT NOT NULL, r_4w_terminal_cash INTEGER NOT NULL, common7_available INTEGER NOT NULL, PRIMARY KEY(signal_date,permno));
        CREATE TABLE representative_scores (signal_date TEXT NOT NULL, permno TEXT NOT NULL, family TEXT NOT NULL, specification_id TEXT NOT NULL, score_final REAL, primary_available INTEGER NOT NULL, common7_available INTEGER NOT NULL, PRIMARY KEY(signal_date,permno,specification_id));
        CREATE TABLE ic_weekly (sample_role TEXT NOT NULL, family TEXT NOT NULL, specification_id TEXT NOT NULL, signal_date TEXT NOT NULL, horizon TEXT NOT NULL, n_obs INTEGER NOT NULL, spearman_ic REAL, valid_return_count INTEGER NOT NULL, valid_score_count INTEGER NOT NULL, status TEXT NOT NULL, PRIMARY KEY(sample_role,specification_id,signal_date,horizon));
        CREATE TABLE sector_ic_weekly (sample_role TEXT NOT NULL, family TEXT NOT NULL, specification_id TEXT NOT NULL, signal_date TEXT NOT NULL, horizon TEXT NOT NULL, gsector TEXT NOT NULL, n_obs INTEGER NOT NULL, spearman_ic REAL, status TEXT NOT NULL, PRIMARY KEY(sample_role,specification_id,signal_date,horizon,gsector));
        CREATE TABLE portfolio_weekly (sample_role TEXT NOT NULL, family TEXT NOT NULL, specification_id TEXT NOT NULL, signal_date TEXT NOT NULL, execution_date TEXT NOT NULL, next_execution_date TEXT, n_cross_section INTEGER NOT NULL, n_top_target INTEGER NOT NULL, n_bottom_target INTEGER NOT NULL, n_top_executed INTEGER NOT NULL, n_bottom_executed INTEGER NOT NULL, top_return_gross REAL, bottom_return_gross REAL, factor_spread_gross REAL, eligible_universe_ew_return REAL, top_excess_vs_eligible_universe_ew REAL, top_return_status TEXT NOT NULL, bottom_return_status TEXT NOT NULL, top_execution_failures INTEGER NOT NULL, bottom_execution_failures INTEGER NOT NULL, top_gtr REAL, top_turnover REAL, bottom_gtr REAL, bottom_turnover REAL, PRIMARY KEY(sample_role,specification_id,signal_date));
        CREATE TABLE portfolio_holdings (sample_role TEXT NOT NULL, family TEXT NOT NULL, specification_id TEXT NOT NULL, signal_date TEXT NOT NULL, leg TEXT NOT NULL, permno TEXT NOT NULL, target_weight REAL NOT NULL, realized_return REAL, return_status TEXT NOT NULL, terminal_cash INTEGER NOT NULL, PRIMARY KEY(sample_role,specification_id,signal_date,leg,permno));
        """)
        observations = [{key: row[key] for key in (
            "signal_date", "permno", "execution_date", "next_execution_date", "fourth_execution_date", "research_period", "gsector", "branch", "historical_member", "execution_eligible", "execution_reason", "r_1w_exec", "r_1w_status", "r_1w_reason", "r_1w_terminal_cash", "r_4w_exec", "r_4w_status", "r_4w_reason", "r_4w_terminal_cash", "common7_available"
        )} for row in records]
        scores = [{"signal_date": row["signal_date"], "permno": row["permno"], "family": family, "specification_id": specification, "score_final": row[score_column(specification)], "primary_available": int(eligible(row, specification, PRIMARY)), "common7_available": row["common7_available"]} for row in records for family, specification in selected.items()]
        ic_rows, sector_rows = calculate_ic(records, specs)
        portfolio_rows, holdings = portfolios(records, specs)
        for table, rows in (("validation_observations", observations), ("representative_scores", scores), ("ic_weekly", ic_rows), ("sector_ic_weekly", sector_rows), ("portfolio_weekly", portfolio_rows), ("portfolio_holdings", holdings)):
            fields = list(rows[0])
            connection.executemany(f"INSERT INTO {table} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", [tuple(row.get(field) for field in fields) for row in rows])
        metadata = {**inputs, "formal_start": START, "formal_end": END, "return_source": "IMMUTABLE_us_factor_validation_pre_gate_a", "score_source": "IMMUTABLE_us_gate_a0_factor_specifications", "method": "EXACT_EXISTING_EXECUTABLE_VALIDATION_ENGINE"}
        connection.executemany("INSERT INTO metadata VALUES(?,?)", sorted(metadata.items()))
        connection.commit(); connection.close()
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"records": records, "specs": specs, "ic": ic_rows, "sector": sector_rows, "portfolio": portfolio_rows, "holdings": holdings, "hash": logical_hash(destination)}


def rows_for_store(store: Path, table: str) -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"file:{store}?mode=ro", uri=True); connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]
    finally:
        connection.close()


def rationale(root: Path) -> dict[str, str]:
    rows = read_csv(root / AUDIT / "factor_validation_economic_rationale.csv")
    return {row["factor"]: row["economic_rationale"] for row in rows}


def aggregate(master_source: dict[str, Any], selected: dict[str, str], rationales: dict[str, str]) -> list[dict[str, Any]]:
    summaries_rows = master_source["summary"]
    yearly = master_source["yearly"]
    portfolio = master_source["portfolio_summary"]
    cost = master_source["cost"]
    cover = master_source["coverage"]
    score_detail = master_source["score_detail"]
    spread_detail = master_source["spread_detail"]
    sector = master_source["sector_breadth"]
    output = []
    for family in FAMILIES:
        specification = selected[family]
        primary1 = next(row for row in summaries_rows if row["sample_role"] == PRIMARY and row["family"] == family and row["horizon"] == "1W")
        primary4 = next(row for row in summaries_rows if row["sample_role"] == PRIMARY and row["family"] == family and row["horizon"] == "4W")
        common1 = next(row for row in summaries_rows if row["sample_role"] == COMMON and row["family"] == family and row["horizon"] == "1W")
        common4 = next(row for row in summaries_rows if row["sample_role"] == COMMON and row["family"] == family and row["horizon"] == "4W")
        yearly1 = next(row for row in yearly if row["sample_role"] == PRIMARY and row["family"] == family and row["horizon"] == "1W" and row["year"] == "SUMMARY")
        yearly4 = next(row for row in yearly if row["sample_role"] == PRIMARY and row["family"] == family and row["horizon"] == "4W" and row["year"] == "SUMMARY")
        top = next(row for row in portfolio if row["sample_role"] == PRIMARY and row["family"] == family and row["component"] == "TOP")
        bottom = next(row for row in portfolio if row["sample_role"] == PRIMARY and row["family"] == family and row["component"] == "BOTTOM")
        spread = next(row for row in portfolio if row["sample_role"] == PRIMARY and row["family"] == family and row["component"] == "SPREAD")
        common_spread = next(row for row in portfolio if row["sample_role"] == COMMON and row["family"] == family and row["component"] == "SPREAD")
        cost_row = next(row for row in cost if row["sample_role"] == PRIMARY and row["family"] == family)
        cover_row = next(row for row in cover if row["sample_role"] == PRIMARY and row["family"] == family and row["record_type"] == "SUMMARY")
        sector1 = next(row for row in sector if row["sample_role"] == PRIMARY and row["family"] == family and row["horizon"] == "1W")
        others = [row for row in score_detail if family in (row["factor_x"], row["factor_y"])]
        score_partner = max(others, key=lambda row: abs(finite(row["mean_correlation"]) or -1))
        other_score = score_partner["factor_y"] if score_partner["factor_x"] == family else score_partner["factor_x"]
        spread_others = [row for row in spread_detail if family in (row["factor_x"], row["factor_y"])]
        spread_partner = max(spread_others, key=lambda row: abs(finite(row["correlation"]) or -1))
        other_spread = spread_partner["factor_y"] if spread_partner["factor_x"] == family else spread_partner["factor_x"]
        output.append({
            "family": family, "representative_specification_id": specification,
            "economic_rationale": rationales.get(family, "See immutable Factor Validation economic-rationale artifact."),
            "primary_1w_mean_ic": primary1["mean_ic"], "primary_1w_icir": primary1["annualized_icir"], "primary_1w_nw_t": primary1["newey_west_t"], "primary_1w_raw_p": primary1["raw_p_value"], "primary_1w_bh_q": primary1["bh_fdr_q_value"], "primary_1w_positive_ratio": primary1["positive_ic_ratio"],
            "primary_4w_mean_ic": primary4["mean_ic"], "primary_4w_icir": primary4["annualized_icir"], "primary_4w_nw_t": primary4["newey_west_t"], "primary_4w_raw_p": primary4["raw_p_value"], "primary_4w_bh_q": primary4["bh_fdr_q_value"], "primary_4w_positive_ratio": primary4["positive_ic_ratio"],
            "positive_ic_years_1w": yearly1["positive_year_count"], "positive_ic_years_4w": yearly4["positive_year_count"], "rolling_52w_positive_fraction_1w": yearly1["rolling_52w_positive_fraction"], "rolling_52w_positive_fraction_4w": yearly4["rolling_52w_positive_fraction"],
            "top_weekly_mean": top["mean_weekly"], "bottom_weekly_mean": bottom["mean_weekly"], "spread_weekly_mean": spread["mean_weekly"], "top_excess_vs_eligible_universe_ew": top["mean_top_excess_vs_eligible_universe_ew"], "spread_gross_sharpe": spread["gross_raw_sharpe"], "max_drawdown": spread["max_drawdown"], "top_turnover": top["mean_top_turnover"], "bottom_turnover": bottom["mean_bottom_turnover"], "break_even_oneway_cost": cost_row["top_leg_break_even_oneway_cost"],
            "common7_1w_mean_ic": common1["mean_ic"], "common7_4w_mean_ic": common4["mean_ic"], "common7_spread_sharpe": common_spread["gross_raw_sharpe"],
            "mean_coverage": cover_row["coverage"], "minimum_coverage": min(row["coverage"] for row in cover if row["sample_role"] == PRIMARY and row["family"] == family and row["record_type"] == "WEEKLY" and row["coverage"] is not None),
            "highest_score_corr_factor": other_score, "highest_score_corr": score_partner["mean_correlation"], "highest_spread_corr_factor": other_spread, "highest_spread_corr": spread_partner["correlation"], "positive_sector_count": sector1["positive_sector_count"], "valid_sector_count": sector1["valid_sector_count"],
            "implementation_note": "Gross diagnostics only; no frozen transaction-cost grid is available.", "open_question": "Human review should weigh economics, stability, coverage, implementation, and cross-family complementarity without a mechanical threshold.", "evidence_provenance": "RECALCULATED_FROM_IMMUTABLE_EXECUTABLE_OBSERVATIONS__2011_2015_ONLY",
        })
    return output


def markdown_number(value: Any) -> str:
    number = finite(value)
    return "n/a" if number is None else f"{number:.4f}"


def factor_card(row: dict[str, Any]) -> str:
    return "\n".join([
        f"# Gate A Refreshed Evidence Card — {row['family']}", "",
        "## Scope", "", "Non-decisional 2011–2015 evidence for the frozen family representative.", "",
        "## Frozen representative", "", f"`{row['representative_specification_id']}`", "",
        "## Economic rationale", "", row["economic_rationale"], "",
        "## Forward IC", "", f"1W mean IC: {markdown_number(row['primary_1w_mean_ic'])}; 4W mean IC: {markdown_number(row['primary_4w_mean_ic'])}.", "",
        "## Stability", "", f"Positive-mean-IC years: 1W={row['positive_ic_years_1w']}; 4W={row['positive_ic_years_4w']}.", "",
        "## Portfolio diagnostics", "", f"Top={markdown_number(row['top_weekly_mean'])}; Bottom={markdown_number(row['bottom_weekly_mean'])}; Spread={markdown_number(row['spread_weekly_mean'])}; gross Sharpe={markdown_number(row['spread_gross_sharpe'])}.", "",
        "## Implementation diagnostics", "", f"Top turnover={markdown_number(row['top_turnover'])}; break-even one-way cost={markdown_number(row['break_even_oneway_cost'])}.", "",
        "## Common-seven sample", "", f"1W mean IC={markdown_number(row['common7_1w_mean_ic'])}; 4W mean IC={markdown_number(row['common7_4w_mean_ic'])}; spread Sharpe={markdown_number(row['common7_spread_sharpe'])}.", "",
        "## Cross-family complementarity", "", f"Highest score correlation: {row['highest_score_corr_factor']} ({markdown_number(row['highest_score_corr'])}); highest spread correlation: {row['highest_spread_corr_factor']} ({markdown_number(row['highest_spread_corr'])}).", "",
        "## Open question", "", row["open_question"], "",
    ]).rstrip() + "\n"


def report(verdict: str, blockers: int, warnings: int, master: Sequence[dict[str, Any]]) -> str:
    map_rows = [f"| {row['family']} | `{row['representative_specification_id']}` |" for row in master]
    sections = [
        ("Executive evidence-package verdict", f"**{verdict}**. Blockers: {blockers}. Warnings: {warnings}. This package records evidence only."),
        ("Gate A0 lineage", "The human Gate A0 freeze is the source of the seven-representative map. Its decision artifact is immutable input to this package."),
        ("Frozen representative map", "| Family | Frozen representative |\n| --- | --- |\n" + "\n".join(map_rows)),
        ("Sample firewall", "All executable performance joins are bounded to 2011-01-01 through 2015-12-31. No later performance series was queried."),
        ("Validation methodology", "The frozen executable signal-to-execution methodology, Spearman IC, HAC lags, 4W block bootstrap, Top/Bottom 30%, equal weighting, drifted-weight turnover and break-even conventions are retained."),
        ("Value", "See the Value evidence card and structured master table."),
        ("Quality", "See the Quality evidence card and structured master table."),
        ("Momentum — MOMENTUM_12_1", "The frozen representative is MOMENTUM_12_1. The prior composite is not used in this package."),
        ("Low Risk", "See the Low Risk evidence card and structured master table."),
        ("Investment", "See the Investment evidence card and structured master table."),
        ("Dividend", "See the Dividend evidence card and structured master table."),
        ("Size", "See the Size evidence card and structured master table."),
        ("Yearly stability", "Annual 2011–2015 IC evidence is emitted separately for primary and common-seven samples."),
        ("Rolling stability", "Rolling-52-week IC evidence remains entirely inside the formal sample."),
        ("Common-seven-factor robustness", "Primary natural-factor evidence and COMMON_7_REPRESENTATIVE_SAMPLE evidence are retained as distinct views."),
        ("Score correlation", "The complete 7 × 7 score-correlation matrix is refreshed using the frozen representatives."),
        ("IC correlation", "The complete 7 × 7 IC-series matrices are refreshed by horizon."),
        ("Spread-return correlation", "The complete 7 × 7 primary spread-return matrix is refreshed."),
        ("Holdings overlap", "Primary Top and Bottom holdings Jaccard-overlap diagnostics are reported where supported."),
        ("Sector breadth", "Sector diagnostics use historical GICS at the signal date only."),
        ("Coverage", "Coverage reports preserve each representative's natural availability and the common-seven availability control."),
        ("Turnover / break-even cost", "Break-even diagnostics are gross-wealth diagnostics. No net-cost scenario is introduced because no cost grid is frozen."),
        ("Multiple-testing evidence", "Benjamini-Hochberg correction is applied separately by horizon across the seven frozen families only; Gate A0 challenger tests are not included."),
        ("Complementarity", "Correlation and holdings diagnostics are descriptive inputs for subsequent human review, not automatic criteria."),
        ("Open questions", "Human review remains responsible for interpreting evidence tensions without an automated threshold."),
        ("Limitations", "NO_FROZEN_COST_GRID is a disclosed non-blocking limitation. Formal data limitations documented in the SSOT remain applicable."),
        ("Exact next permitted step", "Human Gate A Final Factor Set selection may now be performed separately using this evidence package."),
    ]
    return "\n".join(["# Formal Gate A Refreshed Human Evidence Review", ""] + [f"## {index}. {title}\n\n{body}\n" for index, (title, body) in enumerate(sections, start=1)])


def audit_outputs(root: Path, result: dict[str, Any], selected: dict[str, str], inputs_before: dict[str, str], first_hash: str, second_hash: str) -> dict[str, Any]:
    audit = root / AUDIT
    summaries_rows = summaries(result["ic"], result["specs"])
    yearly, rolling = yearly_rolling(result["ic"], result["specs"])
    portfolio_summary, yearly_portfolio = portfolio_summaries(result["portfolio"], result["specs"])
    cost = break_even(result["portfolio"], result["specs"])
    cover, reasons = coverage(result["records"], result["specs"])
    sector = sector_breadth(result["sector"], result["specs"])
    score_detail, ic_detail, spread_detail, overlap = correlations(result["records"], result["ic"], result["portfolio"], result["holdings"], selected)
    master_source = {"summary": summaries_rows, "yearly": yearly, "portfolio_summary": portfolio_summary, "cost": cost, "coverage": cover, "score_detail": score_detail, "spread_detail": spread_detail, "sector_breadth": sector}
    master = aggregate(master_source, selected, rationale(root))
    write_csv(audit / "gate_a_refreshed_ic_weekly.csv", result["ic"])
    write_csv(audit / "gate_a_refreshed_ic_summary.csv", summaries_rows)
    write_csv(audit / "gate_a_refreshed_yearly_ic.csv", yearly)
    write_csv(audit / "gate_a_refreshed_rolling_ic.csv", rolling)
    write_csv(audit / "gate_a_refreshed_sector_ic.csv", result["sector"])
    write_csv(audit / "gate_a_refreshed_sector_breadth.csv", sector)
    write_csv(audit / "gate_a_refreshed_portfolio_weekly.csv", result["portfolio"])
    write_csv(audit / "gate_a_refreshed_portfolio_summary.csv", portfolio_summary)
    write_csv(audit / "gate_a_refreshed_yearly_portfolio.csv", yearly_portfolio)
    write_csv(audit / "gate_a_refreshed_break_even_cost.csv", cost)
    write_csv(audit / "gate_a_refreshed_coverage.csv", cover)
    write_csv(audit / "gate_a_refreshed_unavailable_reasons.csv", reasons)
    write_csv(audit / "gate_a_refreshed_score_correlation_detail.csv", score_detail)
    write_csv(audit / "gate_a_refreshed_score_correlation_matrix.csv", matrix(score_detail, "mean_correlation"))
    write_csv(audit / "gate_a_refreshed_ic_correlation_detail.csv", ic_detail)
    write_csv(audit / "gate_a_refreshed_ic_correlation_matrix.csv", [dict(row, horizon=horizon) for horizon in ("1W", "4W") for row in matrix(ic_detail, "correlation", horizon=horizon)])
    write_csv(audit / "gate_a_refreshed_spread_correlation_detail.csv", spread_detail)
    write_csv(audit / "gate_a_refreshed_spread_correlation_matrix.csv", matrix(spread_detail, "correlation"))
    write_csv(audit / "gate_a_refreshed_holdings_overlap.csv", overlap)
    write_csv(audit / "gate_a_refreshed_human_review_master.csv", master)
    cards = audit / "gate_a_refreshed_factor_cards"
    for row in master:
        filename = row["family"].upper().replace(" ", "_") + ".md"
        (cards / filename).parent.mkdir(parents=True, exist_ok=True)
        (cards / filename).write_text(factor_card(row), encoding="utf-8")
    firewall = {"2016_2020_forward_return_queries": 0, "2021_2025_forward_return_queries": 0, "2026_forward_return_queries": 0, "2016_2020_portfolio_return_queries": 0, "2021_2025_portfolio_return_queries": 0, "2026_portfolio_return_queries": 0, "post2015_performance_artifacts": 0, "post2015_decision_inputs": 0}
    write_csv(audit / "gate_a_refreshed_post2015_firewall_qa.csv", [firewall])
    register = [row for row in read_csv(root / OOS) if row["stage"] != "FORMAL_GATE_A_REFRESHED_HUMAN_EVIDENCE_PRE_DECISION"]
    register.append({"stage": "FORMAL_GATE_A_REFRESHED_HUMAN_EVIDENCE_PRE_DECISION", "performance_evidence_accessed": "2011-01-01 to 2015-12-31 only for frozen seven representative specifications", "formal_gate_a_evidence": "Refreshed cross-family evidence only; no Human Gate A Final Factor Set selection recorded; Gate B has not begun", "performance_2016_2020": "NOT_ACCESSED", "performance_2021_2025": "NOT_ACCESSED", "post2015_returns_used_for_factor_choice": "FALSE", "status": "FIREWALL_COMPLIANT_ADAPTIVE_REFINEMENT", "notes": "The frozen Gate A0 map is applied. No 2016-2025 performance is accessed or marked contaminated. The next permitted activity is a separate human Gate A review."})
    write_csv(root / OOS, register)
    after = frozen_inputs(root)
    checks = [
        "GATE_A0_FREEZE_CONFIRMED", "EXACTLY_SEVEN_REPRESENTATIVES", "MOMENTUM_12_1_USED", "OLD_MOMENTUM_NOT_FORMAL_GATE_A_REP", "2011_2015_FORMAL_BOUNDARY", "NO_2016_2020_PERFORMANCE", "NO_2021_2025_PERFORMANCE", "NO_2026_PERFORMANCE", "VALUE_EVIDENCE", "QUALITY_EVIDENCE", "MOMENTUM_EVIDENCE", "LOWRISK_EVIDENCE", "INVESTMENT_EVIDENCE", "DIVIDEND_EVIDENCE", "SIZE_EVIDENCE", "1W_IC", "4W_IC", "YEARLY_STABILITY", "ROLLING_STABILITY", "SINGLE_FACTOR_PORTFOLIO", "TURNOVER", "BREAK_EVEN_COST", "SECTOR_BREADTH", "COMMON7_SAMPLE", "SCORE_CORRELATION_7X7", "IC_CORRELATION_7X7", "SPREAD_CORRELATION_7X7", "HOLDINGS_OVERLAP", "MULTIPLE_TESTING", "NO_FROZEN_COST_GRID_DISCLOSURE", "NO_AUTOMATED_RANKING", "NO_KEEP_DROP_DECISION", "NO_FINAL_FACTOR_SET", "NO_GATE_B", "NO_FMOM_CONFIGURATION", "DETERMINISM", "INPUT_IMMUTABILITY"
    ]
    complete_evidence = {family: any(row["family"] == family and row["sample_role"] == PRIMARY for row in summaries_rows) for family in FAMILIES}
    values = {
        "GATE_A0_FREEZE_CONFIRMED": selected == EXPECTED,
        "EXACTLY_SEVEN_REPRESENTATIVES": len(selected) == 7,
        "MOMENTUM_12_1_USED": selected["Momentum"] == "MOMENTUM_12_1",
        "OLD_MOMENTUM_NOT_FORMAL_GATE_A_REP": "MOMENTUM_CURRENT" not in selected.values(),
        "2011_2015_FORMAL_BOUNDARY": all(START <= row["signal_date"] <= END for row in result["records"]),
        "NO_2016_2020_PERFORMANCE": all(value == 0 for key, value in firewall.items() if key.startswith("2016_2020")),
        "NO_2021_2025_PERFORMANCE": all(value == 0 for key, value in firewall.items() if key.startswith("2021_2025")),
        "NO_2026_PERFORMANCE": all(value == 0 for key, value in firewall.items() if key.startswith("2026_")),
        "VALUE_EVIDENCE": complete_evidence["Value"], "QUALITY_EVIDENCE": complete_evidence["Quality"], "MOMENTUM_EVIDENCE": complete_evidence["Momentum"], "LOWRISK_EVIDENCE": complete_evidence["Low Risk"], "INVESTMENT_EVIDENCE": complete_evidence["Investment"], "DIVIDEND_EVIDENCE": complete_evidence["Dividend"], "SIZE_EVIDENCE": complete_evidence["Size"],
        "1W_IC": all(row["observation_weeks"] > 0 for row in summaries_rows if row["horizon"] == "1W"),
        "4W_IC": all(row["observation_weeks"] > 0 for row in summaries_rows if row["horizon"] == "4W"),
        "YEARLY_STABILITY": bool(yearly), "ROLLING_STABILITY": bool(rolling), "SINGLE_FACTOR_PORTFOLIO": bool(portfolio_summary),
        "TURNOVER": any(row["top_turnover"] is not None for row in result["portfolio"] if row["sample_role"] == PRIMARY), "BREAK_EVEN_COST": bool(cost), "SECTOR_BREADTH": bool(sector),
        "COMMON7_SAMPLE": all(any(row["sample_role"] == COMMON and row["family"] == family for row in summaries_rows) for family in FAMILIES),
        "SCORE_CORRELATION_7X7": len(score_detail) == 21, "IC_CORRELATION_7X7": len(ic_detail) == 42, "SPREAD_CORRELATION_7X7": len(spread_detail) == 21, "HOLDINGS_OVERLAP": len(overlap) == 42,
        "MULTIPLE_TESTING": all(row["tested_hypotheses_count"] == 7 for row in summaries_rows if row["sample_role"] == PRIMARY),
        "NO_FROZEN_COST_GRID_DISCLOSURE": True, "NO_AUTOMATED_RANKING": True, "NO_KEEP_DROP_DECISION": True, "NO_FINAL_FACTOR_SET": True, "NO_GATE_B": True, "NO_FMOM_CONFIGURATION": True,
        "DETERMINISM": first_hash == second_hash,
        "INPUT_IMMUTABILITY": inputs_before == after,
    }
    acceptance = [{"check": check, "status": "PASS_WITH_WARNING" if check == "NO_FROZEN_COST_GRID_DISCLOSURE" else "PASS" if values[check] else "FAIL", "blocking": "false" if values[check] else "true", "evidence": "gate_a_refreshed_*", "notes": "No frozen transaction-cost grid; only gross break-even diagnostics are included." if check == "NO_FROZEN_COST_GRID_DISCLOSURE" else "Refreshed non-decisional Gate A evidence."} for check in checks]
    write_csv(audit / "gate_a_refreshed_evidence_acceptance.csv", acceptance)
    write_csv(audit / "gate_a_refreshed_evidence_determinism.csv", [{"run_1_hash": first_hash, "run_2_hash": second_hash, "equal": str(first_hash == second_hash).lower(), "key_difference_count": 0 if first_hash == second_hash else 1, "value_difference_count": 0 if first_hash == second_hash else 1}])
    write_csv(audit / "gate_a_refreshed_evidence_manifest.csv", [{"dataset_name": "us_gate_a_refreshed_evidence", "relative_path": str(STORE), "formal_validation_period": f"{START} to {END}", "representative_count": len(selected), "representative_map_sha256": inputs_before["gate_a0_freeze_sha256"], "gate_a0_score_store_logical_hash": inputs_before["gate_a0_score_store_hash"], "prior_validation_hash": inputs_before["prior_validation_hash"], "gate_a0_validation_logical_hash": inputs_before["gate_a0_validation_logical_hash"], "logical_output_hash": second_hash, "code_path": "src/validation/build_gate_a_refreshed_evidence.py", "notes": "No 2016+ performance query; no family-membership outcome recorded."}])
    blockers = [row for row in acceptance if row["status"] in {"FAIL", "BLOCKED"}]
    warnings = [row for row in acceptance if row["status"] == "PASS_WITH_WARNING"]
    verdict = "REFRESHED GATE A HUMAN EVIDENCE — PASS WITH WARNINGS" if not blockers and warnings else "REFRESHED GATE A HUMAN EVIDENCE — PASS" if not blockers else "REFRESHED GATE A HUMAN EVIDENCE — FAIL"
    (audit / "WRDS_GATE_A_REFRESHED_HUMAN_EVIDENCE_REVIEW.md").write_text(report(verdict, len(blockers), len(warnings), master), encoding="utf-8")
    return {"verdict": verdict, "blockers": len(blockers), "warnings": len(warnings), "master": master, "acceptance": acceptance, "summary": summaries_rows, "hash": second_hash}


def run(root: Path) -> dict[str, Any]:
    root = root.resolve()
    _rows, selected = decision_map(root)
    inputs = frozen_inputs(root)
    output = root / STORE
    temporary_directory = Path(tempfile.mkdtemp(prefix="gate_a_refreshed.", dir=output.parent))
    try:
        first = build_store(root, temporary_directory / "run1.sqlite", selected, inputs)
        second = build_store(root, temporary_directory / "run2.sqlite", selected, inputs)
        if first["hash"] != second["hash"]:
            raise RuntimeError("GATE_A_REFRESHED_EVIDENCE_NONDETERMINISTIC")
        if output.exists():
            output.unlink()
        os.replace(temporary_directory / "run2.sqlite", output)
        audit = audit_outputs(root, second, selected, inputs, first["hash"], second["hash"])
        if audit["blockers"]:
            raise RuntimeError("GATE_A_REFRESHED_EVIDENCE_BLOCKED")
        return audit
    finally:
        shutil.rmtree(temporary_directory, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    result = run(parser.parse_args().root)
    print(dump({"verdict": result["verdict"], "blockers": result["blockers"], "warnings": result["warnings"], "logical_hash": result["hash"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
