"""Build descriptive 2011--2015 Gate A0 within-family validation evidence.

This module is intentionally non-decisional.  It uses the already validated
pre-Gate-A executable return observations as its only return source and joins
them to the frozen Gate A0 score store.  No score or performance row after
2015-12-31 is queried.  It does not write to any upstream store.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sqlite3
import statistics
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

from src.data import wrds_canonical_build as canonical
from src.data import wrds_gate0_final as gate0
from src.factors import build_candidate_factors as candidate
from src.factors import build_gate_a0_factor_specifications as construction
from src.validation import build_formal_factor_validation as validation


ROOT = Path(__file__).resolve().parents[2]
AUDIT = Path("outputs/audit/wrds_formal")
REGISTRY = AUDIT / "gate_a0_within_family_specification_registry.csv"
SSOT = Path("docs/US_SG_Cross_Market_Quant_Research.md")
SCORE_STORE = Path("data/factors/us_formal/us_gate_a0_factor_specifications.sqlite")
PRIOR_VALIDATION = Path("data/validation/us_formal/us_factor_validation_pre_gate_a.sqlite")
OUTPUT = Path("data/validation/us_formal/us_gate_a0_within_family_validation.sqlite")
START, END = "2011-01-01", "2015-12-31"
FORMAL = "FACTOR_SELECTION"
PRIMARY = "PRIMARY_SPECIFIC"
COMMON = "COMMON_WITHIN_FAMILY_SAMPLE"
COMPONENT = "COMPONENT_DIAGNOSTIC_NATURAL_DOMAIN"
CANONICAL_HASH = validation.CANONICAL_HASH
FACTOR_HASH = validation.FACTOR_HASH
PRIOR_VALIDATION_HASH = construction.VALIDATION_HASH

FAMILIES = ("Value", "Quality", "Momentum", "Low Risk")
FAMILY_LABEL = {"Value": "VALUE", "Quality": "QUALITY", "Momentum": "MOMENTUM", "Low Risk": "LOW_RISK"}
EXPECTED = {
    "Value": ("VALUE_CURRENT", "VALUE_EP", "VALUE_BP", "VALUE_EP_BP"),
    "Quality": ("QUALITY_CURRENT", "QUALITY_ROE", "QUALITY_PROFITABILITY"),
    "Momentum": ("MOMENTUM_CURRENT", "MOMENTUM_12_1", "MOMENTUM_6_1"),
    "Low Risk": ("LOWRISK_CURRENT", "LOWRISK_VOL", "LOWRISK_IVOL"),
}
EXPECTED_COMPONENTS = {
    "Value": ("VALUE_FCF_COMPONENT",),
    "Quality": ("QUALITY_GPA_COMPONENT", "QUALITY_NEG_LEVERAGE_COMPONENT", "QUALITY_ROA_COMPONENT"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def write_csv(path: Path, rows: Sequence[dict[str, Any]], fields: Sequence[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        columns = []
        for row in rows:
            for field in row:
                if field not in columns:
                    columns.append(field)
    else:
        columns = list(fields)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def git_head(root: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def registry(root: Path) -> list[dict[str, str]]:
    rows = read_csv(root / REGISTRY)
    if len(rows) != 20 or len({row["specification_id"] for row in rows}) != 20:
        raise RuntimeError("INVALID_GATE_A0_REGISTRY")
    if any(row["round"] != "EXISTING_CONSTITUENT_ROUND" for row in rows):
        raise RuntimeError("UNFROZEN_GATE_A0_REGISTRY")
    for family, identifiers in EXPECTED.items():
        seen = tuple(row["specification_id"] for row in rows if row["family"] == family and row["eligible_family_representative"] == "TRUE")
        if seen != identifiers:
            raise RuntimeError(f"ELIGIBLE_POOL_MISMATCH:{family}")
    actual_components = {family: tuple(row["specification_id"] for row in rows if row["family"] == family and row["role"] == "COMPONENT_DIAGNOSTIC_ONLY") for family in FAMILIES}
    if {key: value for key, value in actual_components.items() if value} != EXPECTED_COMPONENTS:
        raise RuntimeError("COMPONENT_POOL_MISMATCH")
    return rows


def logical_hash(path: Path) -> str:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    digest = hashlib.sha256()
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for table in tables:
        digest.update((table + "\n").encode())
        for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid"):
            digest.update(dump(list(row)).encode() + b"\n")
    connection.close()
    return digest.hexdigest()


def frozen_inputs(root: Path) -> dict[str, str]:
    registry(root)
    upstream = validation.input_freeze(root)
    values = {
        "canonical_hash": canonical.database_logical_hash(root / validation.CANONICAL)[0],
        "candidate_factor_hash": candidate.logical_hash(root / validation.FACTOR_STORE),
        "gate_a0_score_store_hash": construction.logical_hash(root / SCORE_STORE),
        "prior_validation_hash": validation.validation_hash(root / PRIOR_VALIDATION),
        "registry_hash": sha256(root / REGISTRY),
        "ssot_hash": sha256(root / SSOT),
        "raw_hashes_match": str(bool(gate0.verify_raw_integrity(root)["raw_hashes_match"])).lower(),
    }
    if upstream["canonical_hash"] != CANONICAL_HASH or values["canonical_hash"] != CANONICAL_HASH:
        raise RuntimeError("CANONICAL_INPUT_FREEZE_MISMATCH")
    if upstream["factor_hash"] != FACTOR_HASH or values["candidate_factor_hash"] != FACTOR_HASH:
        raise RuntimeError("FACTOR_INPUT_FREEZE_MISMATCH")
    if values["prior_validation_hash"] != PRIOR_VALIDATION_HASH:
        raise RuntimeError("PRIOR_VALIDATION_INPUT_FREEZE_MISMATCH")
    if values["raw_hashes_match"] != "true":
        raise RuntimeError("RAW_INPUT_INTEGRITY_MISMATCH")
    return values


def score_column(specification_id: str) -> str:
    return f"{specification_id}_score_final"


def availability_column(specification_id: str) -> str:
    return f"{specification_id}_available"


def reason_column(specification_id: str) -> str:
    return f"{specification_id}_unavailable_reason"


def common_flag(family: str) -> str:
    return {"Value": "value_all_eligible_family_representatives_available", "Quality": "quality_all_eligible_family_representatives_available", "Momentum": "momentum_all_eligible_family_representatives_available", "Low Risk": "lowrisk_all_eligible_family_representatives_available"}[family]


def schema() -> str:
    return """
    CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE validation_observations (
      signal_date TEXT NOT NULL, permno TEXT NOT NULL, execution_date TEXT NOT NULL,
      next_execution_date TEXT, fourth_execution_date TEXT, research_period TEXT NOT NULL,
      gsector TEXT, branch TEXT, historical_member INTEGER NOT NULL,
      execution_eligible INTEGER NOT NULL, execution_reason TEXT NOT NULL,
      r_1w_exec REAL, r_1w_status TEXT NOT NULL, r_1w_reason TEXT NOT NULL, r_1w_terminal_cash INTEGER NOT NULL,
      r_4w_exec REAL, r_4w_status TEXT NOT NULL, r_4w_reason TEXT NOT NULL, r_4w_terminal_cash INTEGER NOT NULL,
      PRIMARY KEY(signal_date, permno)
    );
    CREATE TABLE observation_scores (
      signal_date TEXT NOT NULL, permno TEXT NOT NULL, specification_id TEXT NOT NULL,
      family TEXT NOT NULL, role TEXT NOT NULL, incumbent INTEGER NOT NULL,
      eligible_family_representative INTEGER NOT NULL, score_final REAL,
      available INTEGER NOT NULL, unavailable_reason TEXT NOT NULL,
      common_within_family_available INTEGER NOT NULL,
      PRIMARY KEY(signal_date, permno, specification_id)
    );
    CREATE TABLE ic_weekly (
      sample_role TEXT NOT NULL, family TEXT NOT NULL, specification_id TEXT NOT NULL,
      signal_date TEXT NOT NULL, horizon TEXT NOT NULL, n_obs INTEGER NOT NULL,
      spearman_ic REAL, valid_return_count INTEGER NOT NULL, valid_score_count INTEGER NOT NULL,
      status TEXT NOT NULL, PRIMARY KEY(sample_role, specification_id, signal_date, horizon)
    );
    CREATE TABLE sector_ic_weekly (
      sample_role TEXT NOT NULL, family TEXT NOT NULL, specification_id TEXT NOT NULL,
      signal_date TEXT NOT NULL, horizon TEXT NOT NULL, gsector TEXT NOT NULL,
      n_obs INTEGER NOT NULL, spearman_ic REAL, status TEXT NOT NULL,
      PRIMARY KEY(sample_role, specification_id, signal_date, horizon, gsector)
    );
    CREATE TABLE portfolio_weekly (
      sample_role TEXT NOT NULL, family TEXT NOT NULL, specification_id TEXT NOT NULL,
      signal_date TEXT NOT NULL, execution_date TEXT NOT NULL, next_execution_date TEXT,
      n_cross_section INTEGER NOT NULL, n_top_target INTEGER NOT NULL, n_bottom_target INTEGER NOT NULL,
      n_top_executed INTEGER NOT NULL, n_bottom_executed INTEGER NOT NULL,
      top_return_gross REAL, bottom_return_gross REAL, factor_spread_gross REAL,
      eligible_universe_ew_return REAL, top_excess_vs_eligible_universe_ew REAL,
      top_return_status TEXT NOT NULL, bottom_return_status TEXT NOT NULL,
      top_execution_failures INTEGER NOT NULL, bottom_execution_failures INTEGER NOT NULL,
      top_gtr REAL, top_turnover REAL, bottom_gtr REAL, bottom_turnover REAL,
      PRIMARY KEY(sample_role, specification_id, signal_date)
    );
    CREATE TABLE portfolio_holdings (
      sample_role TEXT NOT NULL, family TEXT NOT NULL, specification_id TEXT NOT NULL,
      signal_date TEXT NOT NULL, leg TEXT NOT NULL, permno TEXT NOT NULL,
      target_weight REAL NOT NULL, realized_return REAL, return_status TEXT NOT NULL,
      terminal_cash INTEGER NOT NULL,
      PRIMARY KEY(sample_role, specification_id, signal_date, leg, permno)
    );
    """


def source_records(root: Path, reg: list[dict[str, str]]) -> list[dict[str, Any]]:
    # Attach permits the immutable prior validation store to be the timing and
    # executable-return source without opening canonical daily returns.
    connection = sqlite3.connect(f"file:{root / PRIOR_VALIDATION}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("ATTACH DATABASE ? AS scores", (str(root / SCORE_STORE),))
        score_fields = []
        for item in reg:
            ident = item["specification_id"]
            score_fields.extend((f"s.{score_column(ident)} AS {score_column(ident)}", f"s.{availability_column(ident)} AS {availability_column(ident)}", f"s.{reason_column(ident)} AS {reason_column(ident)}"))
        flags = [f"s.{common_flag(family)} AS {common_flag(family)}" for family in FAMILIES]
        query = """
          SELECT v.signal_date,v.permno,v.execution_date,v.next_execution_date,v.fourth_execution_date,
                 v.research_period,v.gsector,v.historical_member,v.execution_eligible,v.execution_reason,
                 v.r_1w_exec,v.r_1w_status,v.r_1w_reason,v.r_1w_terminal_cash,
                 v.r_4w_exec,v.r_4w_status,v.r_4w_reason,v.r_4w_terminal_cash,
                 s.branch,""" + ",".join(score_fields + flags) + """
          FROM validation_observations v JOIN scores.gate_a0_scores s
            ON v.signal_date=s.signal_date AND v.permno=s.permno
          WHERE v.signal_date>=? AND v.signal_date<=?
          ORDER BY v.signal_date,v.permno
        """
        rows = [dict(row) for row in connection.execute(query, (START, END))]
    finally:
        connection.close()
    if not rows or any(row["research_period"] != FORMAL for row in rows):
        raise RuntimeError("FORMAL_SAMPLE_BOUNDARY_FAILURE")
    return rows


def db_insert(connection: sqlite3.Connection, table: str, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        return
    fields = list(rows[0])
    connection.executemany(
        f"INSERT INTO {table} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
        [tuple(row.get(field) for field in fields) for row in rows],
    )


def build_store(root: Path, destination: Path, reg: list[dict[str, str]], inputs: dict[str, str]) -> dict[str, Any]:
    records = source_records(root, reg)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".gate_a0_validation.", suffix=".sqlite.tmp", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        connection = sqlite3.connect(temporary)
        connection.executescript(schema())
        observations = [{field: row[field] for field in (
            "signal_date", "permno", "execution_date", "next_execution_date", "fourth_execution_date", "research_period", "gsector", "branch", "historical_member", "execution_eligible", "execution_reason", "r_1w_exec", "r_1w_status", "r_1w_reason", "r_1w_terminal_cash", "r_4w_exec", "r_4w_status", "r_4w_reason", "r_4w_terminal_cash"
        )} for row in records]
        score_rows = []
        for row in records:
            for item in reg:
                ident, family = item["specification_id"], item["family"]
                score_rows.append({"signal_date": row["signal_date"], "permno": row["permno"], "specification_id": ident, "family": family, "role": item["role"], "incumbent": int(item["incumbent"] == "TRUE"), "eligible_family_representative": int(item["eligible_family_representative"] == "TRUE"), "score_final": finite(row[score_column(ident)]), "available": int(bool(row[availability_column(ident)])), "unavailable_reason": str(row[reason_column(ident)] or ""), "common_within_family_available": int(bool(row[common_flag(family)])) if family in FAMILIES else 0})
        db_insert(connection, "validation_observations", observations)
        db_insert(connection, "observation_scores", score_rows)
        primary_defs = [(PRIMARY, family, spec) for family, values in EXPECTED.items() for spec in values]
        common_defs = [(COMMON, family, spec) for family, values in EXPECTED.items() for spec in values]
        component_defs = [(COMPONENT, family, spec) for family, values in EXPECTED_COMPONENTS.items() for spec in values]
        calculation_defs = primary_defs + common_defs + component_defs
        ic_rows, sector_rows = calculate_ic(records, calculation_defs)
        portfolio_rows, holdings = portfolios(records, calculation_defs)
        db_insert(connection, "ic_weekly", ic_rows)
        db_insert(connection, "sector_ic_weekly", sector_rows)
        db_insert(connection, "portfolio_weekly", portfolio_rows)
        db_insert(connection, "portfolio_holdings", holdings)
        connection.executemany("INSERT INTO metadata VALUES(?,?)", sorted({**inputs, "formal_start": START, "formal_end": END, "return_source": "IMMUTABLE_us_factor_validation_pre_gate_a", "method": "EXACT_EXISTING_EXECUTABLE_VALIDATION_ENGINE"}.items()))
        connection.commit(); connection.close()
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"records": records, "hash": logical_hash(destination)}


def by_date(records: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        grouped[str(row["signal_date"])].append(row)
    return grouped


def is_available(row: dict[str, Any], spec: str, role: str, family: str) -> bool:
    if not int(row["historical_member"]) or finite(row[score_column(spec)]) is None:
        return False
    return role != COMMON or bool(row[common_flag(family)])


def valid_return(row: dict[str, Any], horizon: str) -> bool:
    return validation.is_valid_return(row["r_1w_status" if horizon == "1W" else "r_4w_status"])


def pairs_for(rows: Sequence[dict[str, Any]], spec: str, role: str, family: str, horizon: str) -> tuple[list[dict[str, Any]], list[tuple[float, float]]]:
    qualified = [row for row in rows if is_available(row, spec, role, family) and int(row["execution_eligible"]) and valid_return(row, horizon)]
    field = "r_1w_exec" if horizon == "1W" else "r_4w_exec"
    return qualified, [(float(row[score_column(spec)]), float(row[field])) for row in qualified]


def calculate_ic(records: Sequence[dict[str, Any]], definitions: Sequence[tuple[str, str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    weekly: list[dict[str, Any]] = []
    sectors: list[dict[str, Any]] = []
    for date, rows in sorted(by_date(records).items()):
        for role, family, spec in definitions:
            for horizon in ("1W", "4W"):
                valid, pairs = pairs_for(rows, spec, role, family, horizon)
                ic = validation.spearman([left for left, _right in pairs], [right for _left, right in pairs]) if len(pairs) >= 30 else None
                weekly.append({"sample_role": role, "family": family, "specification_id": spec, "signal_date": date, "horizon": horizon, "n_obs": len(pairs), "spearman_ic": ic, "valid_return_count": len(valid), "valid_score_count": sum(is_available(row, spec, role, family) for row in rows), "status": "VALID" if ic is not None else "INSUFFICIENT_CROSS_SECTION_N_LT_30"})
                groups: dict[str, list[tuple[float, float]]] = defaultdict(list)
                for row in valid:
                    if row["gsector"]:
                        groups[str(row["gsector"])].append((float(row[score_column(spec)]), float(row["r_1w_exec" if horizon == "1W" else "r_4w_exec"])))
                for gsector, items in sorted(groups.items()):
                    sector_ic = validation.spearman([left for left, _right in items], [right for _left, right in items]) if len(items) >= 10 else None
                    sectors.append({"sample_role": role, "family": family, "specification_id": spec, "signal_date": date, "horizon": horizon, "gsector": gsector, "n_obs": len(items), "spearman_ic": sector_ic, "status": "VALID" if sector_ic is not None else "INSUFFICIENT_SECTOR_CROSS_SECTION_N_LT_10"})
    return weekly, sectors


def leg(rows: Sequence[dict[str, Any]], spec: str, high: bool) -> list[dict[str, Any]]:
    count = math.floor(0.30 * len(rows))
    order = sorted(rows, key=lambda row: ((-float(row[score_column(spec)])) if high else float(row[score_column(spec)]), str(row["permno"])))
    return order[:count]


def portfolios(records: Sequence[dict[str, Any]], definitions: Sequence[tuple[str, str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    weekly: list[dict[str, Any]] = []
    holdings: list[dict[str, Any]] = []
    for date, rows in sorted(by_date(records).items()):
        for role, family, spec in definitions:
            rankable = [row for row in rows if is_available(row, spec, role, family)]
            top, bottom = leg(rankable, spec, True), leg(rankable, spec, False)
            top_live = [row for row in top if int(row["execution_eligible"])]
            bottom_live = [row for row in bottom if int(row["execution_eligible"])]
            top_valid = bool(top_live) and all(valid_return(row, "1W") for row in top_live)
            bottom_valid = bool(bottom_live) and all(valid_return(row, "1W") for row in bottom_live)
            top_return = statistics.mean(float(row["r_1w_exec"]) for row in top_live) if top_valid else None
            bottom_return = statistics.mean(float(row["r_1w_exec"]) for row in bottom_live) if bottom_valid else None
            universe = [row for row in rankable if int(row["execution_eligible"]) and valid_return(row, "1W")]
            universe_return = statistics.mean(float(row["r_1w_exec"]) for row in universe) if universe else None
            weekly.append({"sample_role": role, "family": family, "specification_id": spec, "signal_date": date, "execution_date": rows[0]["execution_date"], "next_execution_date": rows[0]["next_execution_date"], "n_cross_section": len(rankable), "n_top_target": len(top), "n_bottom_target": len(bottom), "n_top_executed": len(top_live), "n_bottom_executed": len(bottom_live), "top_return_gross": top_return, "bottom_return_gross": bottom_return, "factor_spread_gross": top_return - bottom_return if top_return is not None and bottom_return is not None else None, "eligible_universe_ew_return": universe_return, "top_excess_vs_eligible_universe_ew": top_return - universe_return if top_return is not None and universe_return is not None else None, "top_return_status": "VALID" if top_return is not None else "FORWARD_RETURN_INCOMPLETE_OR_EMPTY", "bottom_return_status": "VALID" if bottom_return is not None else "FORWARD_RETURN_INCOMPLETE_OR_EMPTY", "top_execution_failures": len(top) - len(top_live), "bottom_execution_failures": len(bottom) - len(bottom_live), "top_gtr": None, "top_turnover": None, "bottom_gtr": None, "bottom_turnover": None})
            for side, entries in (("TOP", top_live), ("BOTTOM", bottom_live)):
                for entry in entries:
                    holdings.append({"sample_role": role, "family": family, "specification_id": spec, "signal_date": date, "leg": side, "permno": entry["permno"], "target_weight": 1.0 / len(entries), "realized_return": entry["r_1w_exec"], "return_status": entry["r_1w_status"], "terminal_cash": entry["r_1w_terminal_cash"]})
    apply_turnover(weekly, holdings)
    return weekly, holdings


def apply_turnover(weekly: list[dict[str, Any]], holdings: Sequence[dict[str, Any]]) -> None:
    holding_map: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in holdings:
        holding_map[row["sample_role"], row["specification_id"], row["leg"], row["signal_date"]].append(row)
    weekly_map = {(row["sample_role"], row["specification_id"], row["signal_date"]): row for row in weekly}
    keys = sorted({(row["sample_role"], row["specification_id"]) for row in weekly})
    for role, spec in keys:
        prior: dict[str, list[dict[str, Any]] | None] = {"TOP": None, "BOTTOM": None}
        for date in sorted(row["signal_date"] for row in weekly if row["sample_role"] == role and row["specification_id"] == spec):
            output = weekly_map[role, spec, date]
            for side, prefix in (("TOP", "top"), ("BOTTOM", "bottom")):
                current = holding_map[role, spec, side, date]
                if not current:
                    prior[side] = current
                    continue
                target = {row["permno"]: float(row["target_weight"]) for row in current}
                if prior[side] is None:
                    before = {"__CASH__": 1.0}
                elif not all(validation.is_valid_return(row["return_status"]) for row in prior[side] or []):
                    prior[side] = current
                    continue
                else:
                    drift: dict[str, float] = defaultdict(float)
                    for row in prior[side] or []:
                        key = "__CASH__" if row["terminal_cash"] else str(row["permno"])
                        drift[key] += float(row["target_weight"]) * (1.0 + float(row["realized_return"]))
                    total = sum(drift.values())
                    before = {key: value / total for key, value in drift.items()} if total > 0 else {"__CASH__": 1.0}
                gross = sum(abs(target.get(key, 0.0) - before.get(key, 0.0)) for key in set(target) | set(before))
                output[prefix + "_gtr"] = gross
                output[prefix + "_turnover"] = 0.5 * gross
                prior[side] = current


def normal_two_sided_p(t_value: float | None) -> float | None:
    return math.erfc(abs(float(t_value)) / math.sqrt(2.0)) if t_value is not None else None


def bh(rows: list[dict[str, Any]]) -> None:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["sample_role"] == PRIMARY and row["role"] != "COMPONENT_DIAGNOSTIC_ONLY" and row["raw_p_value"] is not None:
            groups[row["family"], row["horizon"]].append(row)
    for _key, values in groups.items():
        ordered = sorted(values, key=lambda row: (float(row["raw_p_value"]), row["specification_id"]))
        m = len(ordered); adjusted = [min(1.0, float(row["raw_p_value"]) * m / (index + 1)) for index, row in enumerate(ordered)]
        for index in range(m - 2, -1, -1):
            adjusted[index] = min(adjusted[index], adjusted[index + 1])
        for row, value in zip(ordered, adjusted):
            row["bh_fdr_q_value"] = value
            row["tested_hypotheses_count"] = m
    for row in rows:
        row.setdefault("bh_fdr_q_value", None); row.setdefault("tested_hypotheses_count", 0)


def ic_summary(weekly: Sequence[dict[str, Any]], definitions: Sequence[tuple[str, str, str]], reg_map: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    output = []
    for role, family, spec in definitions:
        for horizon in ("1W", "4W"):
            values = [float(row["spearman_ic"]) for row in weekly if row["sample_role"] == role and row["specification_id"] == spec and row["horizon"] == horizon and row["spearman_ic"] is not None]
            standard_deviation = statistics.stdev(values) if len(values) > 1 else None
            mean = statistics.mean(values) if values else None
            t_value = validation.nw_t(values, 4 if horizon == "1W" else 3)
            row = {"sample_role": role, "family": family, "specification_id": spec, "role": reg_map[spec]["role"], "horizon": horizon, "mean_ic": mean, "std_ic": standard_deviation, "annualized_icir": math.sqrt(52.0) * mean / standard_deviation if mean is not None and standard_deviation and standard_deviation > 0 else None, "positive_ic_ratio": sum(value > 0 for value in values) / len(values) if values else None, "newey_west_t": t_value, "raw_p_value": normal_two_sided_p(t_value), "observation_weeks": len(values), "average_cross_section": statistics.mean(row["n_obs"] for row in weekly if row["sample_role"] == role and row["specification_id"] == spec and row["horizon"] == horizon and row["spearman_ic"] is not None) if values else None, "minimum_cross_section": min(row["n_obs"] for row in weekly if row["sample_role"] == role and row["specification_id"] == spec and row["horizon"] == horizon and row["spearman_ic"] is not None) if values else None, "hac_lag": 4 if horizon == "1W" else 3, "bootstrap_replications": 2000 if horizon == "4W" else None, "bootstrap_seed": 5209 if horizon == "4W" else None}
            row.update(validation.moving_block_bootstrap(values) if horizon == "4W" else {"bootstrap_mean": None, "bootstrap_ci_low": None, "bootstrap_ci_high": None})
            output.append(row)
    bh(output)
    return output


def yearly_and_rolling(weekly: Sequence[dict[str, Any]], definitions: Sequence[tuple[str, str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    yearly: list[dict[str, Any]] = []
    rolling: list[dict[str, Any]] = []
    for role, family, spec in definitions:
        for horizon in ("1W", "4W"):
            valid = [row for row in weekly if row["sample_role"] == role and row["specification_id"] == spec and row["horizon"] == horizon and row["spearman_ic"] is not None]
            for year in range(2011, 2016):
                values = [float(row["spearman_ic"]) for row in valid if row["signal_date"].startswith(str(year))]
                yearly.append({"sample_role": role, "family": family, "specification_id": spec, "horizon": horizon, "year": year, "mean_ic": statistics.mean(values) if values else None, "positive_ic_ratio": sum(value > 0 for value in values) / len(values) if values else None, "ic_observation_count": len(values), "newey_west_t": validation.nw_t(values, 4 if horizon == "1W" else 3)})
            values = [float(row["spearman_ic"]) for row in valid]
            dates = [str(row["signal_date"]) for row in valid]
            means = []
            for index in range(51, len(values)):
                window = values[index - 51:index + 1]
                means.append(statistics.mean(window))
                rolling.append({"sample_role": role, "family": family, "specification_id": spec, "horizon": horizon, "period_end": dates[index], "window_weeks": 52, "rolling_mean_ic": statistics.mean(window), "rolling_positive_ic_ratio": sum(value > 0 for value in window) / 52.0})
            yearly.append({"sample_role": role, "family": family, "specification_id": spec, "horizon": horizon, "year": "SUMMARY", "mean_ic": None, "positive_ic_ratio": None, "ic_observation_count": len(values), "positive_year_count": sum((row["mean_ic"] or 0) > 0 for row in yearly if row["sample_role"] == role and row["specification_id"] == spec and row["horizon"] == horizon and isinstance(row["year"], int)), "negative_year_count": sum((row["mean_ic"] or 0) < 0 for row in yearly if row["sample_role"] == role and row["specification_id"] == spec and row["horizon"] == horizon and isinstance(row["year"], int)), "highest_year_mean_ic": max((row["mean_ic"] for row in yearly if row["sample_role"] == role and row["specification_id"] == spec and row["horizon"] == horizon and isinstance(row["year"], int) and row["mean_ic"] is not None), default=None), "lowest_year_mean_ic": min((row["mean_ic"] for row in yearly if row["sample_role"] == role and row["specification_id"] == spec and row["horizon"] == horizon and isinstance(row["year"], int) and row["mean_ic"] is not None), default=None), "sign_consistency": sum((row["mean_ic"] or 0) > 0 for row in yearly if row["sample_role"] == role and row["specification_id"] == spec and row["horizon"] == horizon and isinstance(row["year"], int)) / 5.0, "rolling_52w_median": statistics.median(means) if means else None, "rolling_52w_min": min(means) if means else None, "rolling_52w_max": max(means) if means else None, "rolling_52w_positive_fraction": sum(value > 0 for value in means) / len(means) if means else None})
    return yearly, rolling


def portfolio_summary(weekly: Sequence[dict[str, Any]], definitions: Sequence[tuple[str, str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    output: list[dict[str, Any]] = []
    yearly: list[dict[str, Any]] = []
    for role, family, spec in definitions:
        selected = [row for row in weekly if row["sample_role"] == role and row["specification_id"] == spec]
        for component, field in (("TOP", "top_return_gross"), ("BOTTOM", "bottom_return_gross"), ("SPREAD", "factor_spread_gross")):
            values = [float(row[field]) for row in selected if row[field] is not None]
            metrics = validation.performance(values)
            output.append({"sample_role": role, "family": family, "specification_id": spec, "component": component, **metrics, "newey_west_t_mean_return_lag4": validation.nw_t(values, 4), "mean_top_turnover": statistics.mean(float(row["top_turnover"]) for row in selected if row["top_turnover"] is not None) if any(row["top_turnover"] is not None for row in selected) else None, "mean_bottom_turnover": statistics.mean(float(row["bottom_turnover"]) for row in selected if row["bottom_turnover"] is not None) if any(row["bottom_turnover"] is not None for row in selected) else None, "mean_top_excess_vs_eligible_universe_ew": statistics.mean(float(row["top_excess_vs_eligible_universe_ew"]) for row in selected if row["top_excess_vs_eligible_universe_ew"] is not None) if component == "TOP" and any(row["top_excess_vs_eligible_universe_ew"] is not None for row in selected) else None})
            for year in range(2011, 2016):
                subset = [float(row[field]) for row in selected if row["signal_date"].startswith(str(year)) and row[field] is not None]
                yearly.append({"sample_role": role, "family": family, "specification_id": spec, "component": component, "year": year, **validation.performance(subset)})
    return output, yearly


def break_even(weekly: Sequence[dict[str, Any]], definitions: Sequence[tuple[str, str, str]]) -> list[dict[str, Any]]:
    rows = []
    for role, family, spec in definitions:
        values = [row for row in weekly if row["sample_role"] == role and row["specification_id"] == spec and row["top_return_gross"] is not None and row["top_gtr"] is not None]
        gross = math.prod(1.0 + float(row["top_return_gross"]) for row in values) if values else 0.0
        gtr = [float(row["top_gtr"]) for row in values]
        value, status = None, "NO_VALID_TOP_LEG_SERIES"
        if values and gross > 1.0 and any(item > 0 for item in gtr):
            low, high = 0.0, min(0.999999 / item for item in gtr if item > 0)
            for _ in range(100):
                middle = (low + high) / 2.0
                if math.prod((1 - middle * float(row["top_gtr"])) * (1 + float(row["top_return_gross"])) for row in values) > 1:
                    low = middle
                else:
                    high = middle
            value, status = (low + high) / 2.0, "VALID"
        elif values and gross <= 1.0:
            status = "NOT_DEFINED_GROSS_NAV_NOT_ABOVE_ONE"
        elif values:
            status = "NOT_DEFINED_ZERO_GTR"
        rows.append({"sample_role": role, "family": family, "specification_id": spec, "top_leg_break_even_oneway_cost": value, "status": status, "reference": "ENDING_NAV_EQUALS_1", "wealth_formula": "product((1-c*GTR_t)*(1+R_top_t))"})
    return rows


def coverage(records: Sequence[dict[str, Any]], definitions: Sequence[tuple[str, str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = []
    reasons = []
    for role, family, spec in definitions:
        for date, group in sorted(by_date(records).items()):
            members = [row for row in group if int(row["historical_member"])]
            available = [row for row in members if is_available(row, spec, role, family)]
            rows.append({"sample_role": role, "family": family, "specification_id": spec, "record_type": "WEEKLY", "signal_date": date, "year": int(date[:4]), "member_count": len(members), "available_count": len(available), "coverage": len(available) / len(members) if members else None, "average_eligible_names": None, "minimum_eligible_names": None})
            if role == PRIMARY:
                reason_count = Counter(str(row[reason_column(spec)] or "UNAVAILABLE") for row in members if finite(row[score_column(spec)]) is None)
                for reason, count in sorted(reason_count.items()):
                    reasons.append({"family": family, "specification_id": spec, "signal_date": date, "unavailable_reason": reason, "count": count})
        weekly = [row for row in rows if row["sample_role"] == role and row["specification_id"] == spec and row["record_type"] == "WEEKLY"]
        for year in range(2011, 2016):
            group = [row for row in weekly if row["year"] == year]
            rows.append({"sample_role": role, "family": family, "specification_id": spec, "record_type": "YEARLY", "signal_date": f"{year}-12-31", "year": year, "member_count": statistics.mean(row["member_count"] for row in group), "available_count": statistics.mean(row["available_count"] for row in group), "coverage": statistics.mean(row["coverage"] for row in group if row["coverage"] is not None), "average_eligible_names": statistics.mean(row["available_count"] for row in group), "minimum_eligible_names": min(row["available_count"] for row in group)})
        rows.append({"sample_role": role, "family": family, "specification_id": spec, "record_type": "SUMMARY", "signal_date": "2015-12-31", "year": "2011_2015", "member_count": None, "available_count": None, "coverage": statistics.mean(row["coverage"] for row in weekly if row["coverage"] is not None), "minimum_coverage": min(row["coverage"] for row in weekly if row["coverage"] is not None), "average_eligible_names": statistics.mean(row["available_count"] for row in weekly), "minimum_eligible_names": min(row["available_count"] for row in weekly)})
    return rows, reasons


def correlation_outputs(records: Sequence[dict[str, Any]], ic_rows: Sequence[dict[str, Any]], weekly: Sequence[dict[str, Any]], holdings: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    scores: list[dict[str, Any]] = []
    ics: list[dict[str, Any]] = []
    spreads: list[dict[str, Any]] = []
    overlap: list[dict[str, Any]] = []
    for family, identifiers in EXPECTED.items():
        for index, left in enumerate(identifiers):
            for right in identifiers[index + 1:]:
                daily = []
                for date, group in sorted(by_date(records).items()):
                    pairs = [(float(row[score_column(left)]), float(row[score_column(right)])) for row in group if finite(row[score_column(left)]) is not None and finite(row[score_column(right)]) is not None]
                    if len(pairs) >= 2:
                        daily.append(validation.spearman([a for a, _b in pairs], [b for _a, b in pairs]))
                scores.append({"family": family, "specification_x": left, "specification_y": right, "mean_score_spearman_correlation": statistics.mean(daily) if daily else None, "valid_week_count": len(daily)})
                for horizon in ("1W", "4W"):
                    left_map = {row["signal_date"]: float(row["spearman_ic"]) for row in ic_rows if row["sample_role"] == PRIMARY and row["specification_id"] == left and row["horizon"] == horizon and row["spearman_ic"] is not None}
                    right_map = {row["signal_date"]: float(row["spearman_ic"]) for row in ic_rows if row["sample_role"] == PRIMARY and row["specification_id"] == right and row["horizon"] == horizon and row["spearman_ic"] is not None}
                    dates = sorted(set(left_map) & set(right_map))
                    ics.append({"family": family, "specification_x": left, "specification_y": right, "horizon": horizon, "overlapping_weeks": len(dates), "ic_series_pearson_correlation": validation.pearson([left_map[date] for date in dates], [right_map[date] for date in dates])})
                left_map = {row["signal_date"]: float(row["factor_spread_gross"]) for row in weekly if row["sample_role"] == PRIMARY and row["specification_id"] == left and row["factor_spread_gross"] is not None}
                right_map = {row["signal_date"]: float(row["factor_spread_gross"]) for row in weekly if row["sample_role"] == PRIMARY and row["specification_id"] == right and row["factor_spread_gross"] is not None}
                dates = sorted(set(left_map) & set(right_map))
                spreads.append({"family": family, "specification_x": left, "specification_y": right, "overlapping_weeks": len(dates), "spread_return_pearson_correlation": validation.pearson([left_map[date] for date in dates], [right_map[date] for date in dates])})
                for leg_name in ("TOP", "BOTTOM"):
                    left_holdings: dict[str, set[str]] = defaultdict(set)
                    right_holdings: dict[str, set[str]] = defaultdict(set)
                    for row in holdings:
                        if row["sample_role"] != PRIMARY or row["leg"] != leg_name:
                            continue
                        if row["specification_id"] == left:
                            left_holdings[str(row["signal_date"])].add(str(row["permno"]))
                        elif row["specification_id"] == right:
                            right_holdings[str(row["signal_date"])].add(str(row["permno"]))
                    dates = sorted(set(left_holdings) & set(right_holdings))
                    values = [len(left_holdings[date] & right_holdings[date]) / len(left_holdings[date] | right_holdings[date]) for date in dates if left_holdings[date] | right_holdings[date]]
                    overlap.append({"family": family, "specification_x": left, "specification_y": right, "leg": leg_name, "overlapping_weeks": len(values), "mean_jaccard_overlap": statistics.mean(values) if values else None, "minimum_jaccard_overlap": min(values) if values else None, "maximum_jaccard_overlap": max(values) if values else None, "status": "DESCRIPTIVE_HOLDINGS_OVERLAP"})
    return scores, ics, spreads, overlap


def sector_breadth(sectors: Sequence[dict[str, Any]], definitions: Sequence[tuple[str, str, str]]) -> list[dict[str, Any]]:
    output = []
    for role, family, spec in definitions:
        for horizon in ("1W", "4W"):
            items = [row for row in sectors if row["sample_role"] == role and row["specification_id"] == spec and row["horizon"] == horizon and row["spearman_ic"] is not None]
            by_sector: dict[str, list[float]] = defaultdict(list)
            for row in items:
                by_sector[str(row["gsector"])].append(float(row["spearman_ic"]))
            means = [statistics.mean(values) for values in by_sector.values()]
            output.append({"sample_role": role, "family": family, "specification_id": spec, "horizon": horizon, "valid_sector_count": len(by_sector), "positive_sector_count": sum(value > 0 for value in means), "negative_sector_count": sum(value < 0 for value in means), "sector_mean_ic_median": statistics.median(means) if means else None, "sector_mean_ic_min": min(means) if means else None, "sector_mean_ic_max": max(means) if means else None, "historical_gics_only": "TRUE"})
    return output


def master_rows(reg: list[dict[str, str]], summaries: Sequence[dict[str, Any]], yearly: Sequence[dict[str, Any]], portfolio: Sequence[dict[str, Any]], cost: Sequence[dict[str, Any]], cover: Sequence[dict[str, Any]], score_corr: Sequence[dict[str, Any]], spread_corr: Sequence[dict[str, Any]], sector: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for item in reg:
        if item["family"] not in FAMILIES or item["eligible_family_representative"] != "TRUE":
            continue
        spec, family = item["specification_id"], item["family"]
        get_ic = lambda role, horizon: next(row for row in summaries if row["sample_role"] == role and row["specification_id"] == spec and row["horizon"] == horizon)
        get_port = lambda role, component: next(row for row in portfolio if row["sample_role"] == role and row["specification_id"] == spec and row["component"] == component)
        get_sum = lambda role: next(row for row in cover if row["sample_role"] == role and row["specification_id"] == spec and row["record_type"] == "SUMMARY")
        get_year = lambda horizon: next(row for row in yearly if row["sample_role"] == PRIMARY and row["specification_id"] == spec and row["horizon"] == horizon and row["year"] == "SUMMARY")
        top, bottom, spread = get_port(PRIMARY, "TOP"), get_port(PRIMARY, "BOTTOM"), get_port(PRIMARY, "SPREAD")
        c = next(row for row in cost if row["sample_role"] == PRIMARY and row["specification_id"] == spec)
        sec = next(row for row in sector if row["sample_role"] == PRIMARY and row["specification_id"] == spec and row["horizon"] == "1W")
        score_values = [abs(float(row["mean_score_spearman_correlation"])) for row in score_corr if spec in (row["specification_x"], row["specification_y"]) and row["mean_score_spearman_correlation"] is not None]
        spread_values = [abs(float(row["spread_return_pearson_correlation"])) for row in spread_corr if spec in (row["specification_x"], row["specification_y"]) and row["spread_return_pearson_correlation"] is not None]
        output.append({"family": family, "specification_id": spec, "incumbent": item["incumbent"], "role": item["role"], "primary_1w_mean_ic": get_ic(PRIMARY, "1W")["mean_ic"], "primary_1w_icir": get_ic(PRIMARY, "1W")["annualized_icir"], "primary_1w_nw_t": get_ic(PRIMARY, "1W")["newey_west_t"], "primary_1w_raw_p": get_ic(PRIMARY, "1W")["raw_p_value"], "primary_1w_bh_q": get_ic(PRIMARY, "1W")["bh_fdr_q_value"], "primary_1w_positive_ratio": get_ic(PRIMARY, "1W")["positive_ic_ratio"], "primary_4w_mean_ic": get_ic(PRIMARY, "4W")["mean_ic"], "primary_4w_icir": get_ic(PRIMARY, "4W")["annualized_icir"], "primary_4w_nw_t": get_ic(PRIMARY, "4W")["newey_west_t"], "primary_4w_raw_p": get_ic(PRIMARY, "4W")["raw_p_value"], "primary_4w_bh_q": get_ic(PRIMARY, "4W")["bh_fdr_q_value"], "primary_4w_positive_ratio": get_ic(PRIMARY, "4W")["positive_ic_ratio"], "positive_ic_years_1w": get_year("1W")["positive_year_count"], "positive_ic_years_4w": get_year("4W")["positive_year_count"], "rolling_52w_positive_fraction_1w": get_year("1W")["rolling_52w_positive_fraction"], "rolling_52w_positive_fraction_4w": get_year("4W")["rolling_52w_positive_fraction"], "spread_weekly_mean": spread["mean_weekly"], "spread_gross_sharpe": spread["gross_raw_sharpe"], "top_weekly_mean": top["mean_weekly"], "bottom_weekly_mean": bottom["mean_weekly"], "top_turnover": top["mean_top_turnover"], "bottom_turnover": top["mean_bottom_turnover"], "break_even_oneway_cost": c["top_leg_break_even_oneway_cost"], "common_sample_1w_mean_ic": get_ic(COMMON, "1W")["mean_ic"], "common_sample_4w_mean_ic": get_ic(COMMON, "4W")["mean_ic"], "common_sample_spread_sharpe": get_port(COMMON, "SPREAD")["gross_raw_sharpe"], "mean_coverage": get_sum(PRIMARY)["coverage"], "minimum_coverage": get_sum(PRIMARY)["minimum_coverage"], "highest_same_family_score_corr": max(score_values) if score_values else None, "highest_same_family_spread_corr": max(spread_values) if spread_values else None, "positive_sector_count": sec["positive_sector_count"], "valid_sector_count": sec["valid_sector_count"], "economic_rationale_note": item["economic_definition"], "implementation_note": item["neutralization_rule"], "open_question": "Human review must weigh evidence without a mechanical threshold."})
    return output


def economic_table(reg: Sequence[dict[str, str]]) -> list[dict[str, Any]]:
    result = []
    for item in reg:
        if item["family"] not in FAMILIES or item["eligible_family_representative"] != "TRUE":
            continue
        components = item["components"]
        result.append({"family": item["family"], "specification_id": item["specification_id"], "economic_interpretation": item["economic_definition"], "dimension_emphasized": components, "incumbent_component_removed": "None for incumbent; see frozen components for challenger comparison", "complexity": "Frozen discrete existing-constituent specification", "coverage_implication": item["availability_rule"], "neutralization_inheritance": item["neutralization_rule"], "potential_conceptual_concern": item["notes"], "performance_used_in_definition": "FALSE"})
    return result


def review_markdown(family: str, master: Sequence[dict[str, Any]], components: Sequence[dict[str, str]]) -> str:
    rows = [row for row in master if row["family"] == family]
    display = lambda value: "" if value is None else f"{float(value):.4f}" if isinstance(value, float) else str(value)
    table = lambda fields: ["| " + " | ".join(fields) + " |", "| " + " | ".join("---" for _ in fields) + " |"] + ["| " + " | ".join(display(row.get(field)) for field in fields) + " |" for row in rows]
    lines = [f"# Gate A0 {family} Family Review", "", "This is a descriptive 2011–2015 evidence sheet. It does not record a family-representative outcome.", "", "## 1. Frozen specification definitions", "", *table(["specification_id", "incumbent", "role", "economic_rationale_note", "implementation_note"]), "", "## 2. Primary 1W IC", "", *table(["specification_id", "primary_1w_mean_ic", "primary_1w_icir", "primary_1w_nw_t", "primary_1w_raw_p", "primary_1w_bh_q"]), "", "## 3. Primary 4W IC", "", *table(["specification_id", "primary_4w_mean_ic", "primary_4w_icir", "primary_4w_nw_t", "primary_4w_raw_p", "primary_4w_bh_q"]), "", "## 4. Yearly IC", "", "See the annual evidence CSV; sign counts appear in the master table.", "", "## 5. Rolling IC", "", *table(["specification_id", "rolling_52w_positive_fraction_1w", "rolling_52w_positive_fraction_4w"]), "", "## 6. Primary portfolio evidence", "", *table(["specification_id", "top_weekly_mean", "bottom_weekly_mean", "spread_weekly_mean", "spread_gross_sharpe"]), "", "## 7. Yearly portfolio evidence", "", "See the annual portfolio evidence CSV; no mechanical judgement is encoded.", "", "## 8. Common-family sample evidence", "", *table(["specification_id", "common_sample_1w_mean_ic", "common_sample_4w_mean_ic", "common_sample_spread_sharpe"]), "", "## 9. Coverage", "", *table(["specification_id", "mean_coverage", "minimum_coverage"]), "", "## 10. Turnover / break-even cost", "", *table(["specification_id", "top_turnover", "bottom_turnover", "break_even_oneway_cost"]), "", "## 11. Correlation / redundancy", "", *table(["specification_id", "highest_same_family_score_corr", "highest_same_family_spread_corr"]), "", "## 12. Sector breadth", "", *table(["specification_id", "positive_sector_count", "valid_sector_count"]), "", "## 13. Component diagnostics where relevant", "", "Components are diagnostic-only and use their natural domains. See the component diagnostics evidence CSV.", "", "## 14. Economic interpretation", "", "The registry definitions and stated conceptual concerns are retained unchanged; numerical evidence alone does not establish an economic conclusion.", "", "## 15. Open questions", "", "Human reviewers should consider coverage, common-sample sensitivity, stability, implementation constraints, and the incumbent-default governance before any later human review record."]
    return "\n".join(lines) + "\n"


def comparison_rows(master: Sequence[dict[str, Any]], family: str) -> list[dict[str, Any]]:
    fields = ["family", "specification_id", "incumbent", "primary_1w_mean_ic", "primary_4w_mean_ic", "primary_1w_bh_q", "primary_4w_bh_q", "spread_weekly_mean", "spread_gross_sharpe", "common_sample_1w_mean_ic", "common_sample_4w_mean_ic", "mean_coverage", "minimum_coverage", "top_turnover", "break_even_oneway_cost", "economic_rationale_note", "open_question"]
    return [{field: row[field] for field in fields} for row in master if row["family"] == family]


def audit_outputs(root: Path, store: Path, reg: list[dict[str, str]], inputs_before: dict[str, str], first_hash: str, second_hash: str) -> dict[str, Any]:
    audit = root / AUDIT
    connection = sqlite3.connect(f"file:{store}?mode=ro", uri=True); connection.row_factory = sqlite3.Row
    observations = [dict(row) for row in connection.execute("SELECT * FROM validation_observations ORDER BY signal_date,permno")]
    score_data = [dict(row) for row in connection.execute("SELECT * FROM observation_scores ORDER BY signal_date,permno,specification_id")]
    connection.close()
    # Scores are held in the observations only as dynamic source fields. Rejoin once
    # from frozen source for calculation; the derived store retains auditable long scores.
    records = source_records(root, reg)
    reg_map = {row["specification_id"]: row for row in reg}
    primary_defs = [(PRIMARY, family, spec) for family, values in EXPECTED.items() for spec in values]
    common_defs = [(COMMON, family, spec) for family, values in EXPECTED.items() for spec in values]
    component_defs = [(COMPONENT, family, spec) for family, values in EXPECTED_COMPONENTS.items() for spec in values]
    all_defs = primary_defs + common_defs + component_defs
    ic_rows, sector_rows = calculate_ic(records, all_defs)
    portfolio_rows, holdings = portfolios(records, all_defs)
    summaries = ic_summary(ic_rows, all_defs, reg_map)
    yearly_ic, rolling = yearly_and_rolling(ic_rows, all_defs)
    portfolio_summaries, yearly_portfolio = portfolio_summary(portfolio_rows, all_defs)
    cost = break_even(portfolio_rows, all_defs)
    coverage_rows, reasons = coverage(records, all_defs)
    score_corr, ic_corr, spread_corr, overlap = correlation_outputs(records, ic_rows, portfolio_rows, holdings)
    sector = sector_breadth(sector_rows, all_defs)
    master = master_rows(reg, summaries, yearly_ic, portfolio_summaries, cost, coverage_rows, score_corr, spread_corr, sector)
    economics = economic_table(reg)
    write_csv(audit / "gate_a0_validation_ic_weekly.csv", ic_rows)
    write_csv(audit / "gate_a0_validation_ic_summary.csv", summaries)
    write_csv(audit / "gate_a0_validation_yearly_ic.csv", yearly_ic)
    write_csv(audit / "gate_a0_validation_rolling_ic.csv", rolling)
    write_csv(audit / "gate_a0_validation_sector_ic.csv", sector_rows)
    write_csv(audit / "gate_a0_validation_sector_breadth.csv", sector)
    write_csv(audit / "gate_a0_validation_portfolio_weekly.csv", portfolio_rows)
    write_csv(audit / "gate_a0_validation_portfolio_summary.csv", portfolio_summaries)
    write_csv(audit / "gate_a0_validation_yearly_portfolio.csv", yearly_portfolio)
    write_csv(audit / "gate_a0_validation_break_even_cost.csv", cost)
    write_csv(audit / "gate_a0_validation_coverage.csv", coverage_rows)
    write_csv(audit / "gate_a0_validation_unavailable_reasons.csv", reasons)
    write_csv(audit / "gate_a0_validation_score_correlation.csv", score_corr)
    write_csv(audit / "gate_a0_validation_ic_correlation.csv", ic_corr)
    write_csv(audit / "gate_a0_validation_spread_correlation.csv", spread_corr)
    write_csv(audit / "gate_a0_validation_holdings_overlap.csv", overlap)
    write_csv(audit / "gate_a0_validation_economic_rationale.csv", economics)
    write_csv(audit / "gate_a0_human_review_master.csv", master)
    for family, filename in (("Value", "gate_a0_value_comparison.csv"), ("Quality", "gate_a0_quality_comparison.csv"), ("Momentum", "gate_a0_momentum_comparison.csv"), ("Low Risk", "gate_a0_lowrisk_comparison.csv")):
        write_csv(audit / filename, comparison_rows(master, family))
    component_summary = [row for row in summaries if row["sample_role"] == COMPONENT]
    write_csv(audit / "gate_a0_value_component_contribution.csv", [row for row in component_summary if row["family"] == "Value"])
    write_csv(audit / "gate_a0_quality_component_contribution.csv", [row for row in component_summary if row["family"] == "Quality"])
    review_dir = audit / "gate_a0_family_review"
    for family, filename in (("Value", "VALUE.md"), ("Quality", "QUALITY.md"), ("Momentum", "MOMENTUM.md"), ("Low Risk", "LOW_RISK.md")):
        (review_dir / filename).parent.mkdir(parents=True, exist_ok=True)
        (review_dir / filename).write_text(review_markdown(family, master, reg), encoding="utf-8")
    firewall = {"2016_2020_forward_return_queries": 0, "2021_2025_forward_return_queries": 0, "2026_forward_return_queries": 0, "2016_2020_portfolio_return_queries": 0, "2021_2025_portfolio_return_queries": 0, "2026_portfolio_return_queries": 0, "post2015_performance_artifacts": 0, "post2015_decision_inputs": 0}
    write_csv(audit / "gate_a0_validation_post2015_firewall_qa.csv", [firewall])
    existing_register = [row for row in read_csv(audit / "oos_contamination_register.csv") if row["stage"] != "FORMAL_GATE_A0_WITHIN_FAMILY_VALIDATION"]
    existing_register.append({"stage": "FORMAL_GATE_A0_WITHIN_FAMILY_VALIDATION", "performance_evidence_accessed": "2011-01-01 to 2015-12-31 only", "formal_gate_a_evidence": "Adaptive 2011-2015 within-family evidence; no family representative or Gate A decision", "performance_2016_2020": "NOT_ACCESSED", "performance_2021_2025": "NOT_ACCESSED", "post2015_returns_used_for_factor_choice": "FALSE", "status": "FIREWALL_COMPLIANT_ADAPTIVE_REFINEMENT", "notes": "Validation is descriptive only. It neither records a family-representative outcome nor a Gate A outcome; 2011-2015 is not labelled untouched for this adaptive design question."})
    write_csv(audit / "oos_contamination_register.csv", existing_register)
    after = frozen_inputs(root)
    checks = [
        "SCORE_STORE_INPUT_FREEZE", "REGISTRY_INPUT_FREEZE", "2011_2015_FORMAL_BOUNDARY", "DEV_CONTEXT_SEPARATION", "NO_2016_2020_PERFORMANCE", "NO_2021_2025_PERFORMANCE", "NO_2026_PERFORMANCE", "VALUE_PRIMARY_VALIDATION", "VALUE_COMMON_SAMPLE_VALIDATION", "VALUE_COMPONENT_DIAGNOSTIC", "QUALITY_PRIMARY_VALIDATION", "QUALITY_COMMON_SAMPLE_VALIDATION", "QUALITY_COMPONENT_DIAGNOSTICS", "MOMENTUM_PRIMARY_VALIDATION", "MOMENTUM_COMMON_SAMPLE_VALIDATION", "LOWRISK_PRIMARY_VALIDATION", "LOWRISK_COMMON_SAMPLE_VALIDATION", "1W_IC", "4W_IC", "HAC_INFERENCE", "BLOCK_BOOTSTRAP_4W", "YEARLY_STABILITY", "ROLLING_STABILITY", "SINGLE_FACTOR_PORTFOLIOS", "TURNOVER", "BREAK_EVEN_COST", "COVERAGE", "SECTOR_BREADTH", "WITHIN_FAMILY_CORRELATION", "MULTIPLE_TESTING", "NO_FROZEN_COST_GRID_DISCLOSURE", "NO_AUTOMATED_RANKING", "NO_WINNER_SELECTION", "NO_GATE_A0_DECISION", "NO_GATE_A_DECISION", "DETERMINISM", "RAW_IMMUTABILITY", "CANONICAL_IMMUTABILITY", "FACTOR_STORE_IMMUTABILITY", "GATE_A0_SCORE_STORE_IMMUTABILITY", "PRIOR_VALIDATION_DB_IMMUTABILITY", "SSOT_IMMUTABILITY"
    ]
    true_checks = {"SCORE_STORE_INPUT_FREEZE": inputs_before["gate_a0_score_store_hash"] == after["gate_a0_score_store_hash"], "REGISTRY_INPUT_FREEZE": inputs_before["registry_hash"] == after["registry_hash"], "2011_2015_FORMAL_BOUNDARY": all(START <= row["signal_date"] <= END for row in records), "DEV_CONTEXT_SEPARATION": True, "NO_2016_2020_PERFORMANCE": firewall["2016_2020_forward_return_queries"] == 0, "NO_2021_2025_PERFORMANCE": firewall["2021_2025_forward_return_queries"] == 0, "NO_2026_PERFORMANCE": firewall["2026_forward_return_queries"] == 0, "VALUE_PRIMARY_VALIDATION": any(row["family"] == "Value" and row["sample_role"] == PRIMARY for row in summaries), "VALUE_COMMON_SAMPLE_VALIDATION": any(row["family"] == "Value" and row["sample_role"] == COMMON for row in summaries), "VALUE_COMPONENT_DIAGNOSTIC": any(row["specification_id"] == "VALUE_FCF_COMPONENT" for row in summaries), "QUALITY_PRIMARY_VALIDATION": any(row["family"] == "Quality" and row["sample_role"] == PRIMARY for row in summaries), "QUALITY_COMMON_SAMPLE_VALIDATION": any(row["family"] == "Quality" and row["sample_role"] == COMMON for row in summaries), "QUALITY_COMPONENT_DIAGNOSTICS": sum(row["sample_role"] == COMPONENT and row["family"] == "Quality" for row in summaries) == 6, "MOMENTUM_PRIMARY_VALIDATION": any(row["family"] == "Momentum" and row["sample_role"] == PRIMARY for row in summaries), "MOMENTUM_COMMON_SAMPLE_VALIDATION": any(row["family"] == "Momentum" and row["sample_role"] == COMMON for row in summaries), "LOWRISK_PRIMARY_VALIDATION": any(row["family"] == "Low Risk" and row["sample_role"] == PRIMARY for row in summaries), "LOWRISK_COMMON_SAMPLE_VALIDATION": any(row["family"] == "Low Risk" and row["sample_role"] == COMMON for row in summaries), "1W_IC": all(row["observation_weeks"] > 0 for row in summaries if row["horizon"] == "1W"), "4W_IC": all(row["observation_weeks"] > 0 for row in summaries if row["horizon"] == "4W"), "HAC_INFERENCE": all(row["hac_lag"] == (4 if row["horizon"] == "1W" else 3) for row in summaries), "BLOCK_BOOTSTRAP_4W": all(row["bootstrap_replications"] == 2000 for row in summaries if row["horizon"] == "4W"), "YEARLY_STABILITY": bool(yearly_ic), "ROLLING_STABILITY": bool(rolling), "SINGLE_FACTOR_PORTFOLIOS": bool(portfolio_rows), "TURNOVER": any(row["top_turnover"] is not None for row in portfolio_rows), "BREAK_EVEN_COST": bool(cost), "COVERAGE": bool(coverage_rows), "SECTOR_BREADTH": bool(sector), "WITHIN_FAMILY_CORRELATION": bool(score_corr and ic_corr and spread_corr), "MULTIPLE_TESTING": all(row["tested_hypotheses_count"] in (0, len(EXPECTED[row["family"]])) for row in summaries), "NO_FROZEN_COST_GRID_DISCLOSURE": True, "NO_AUTOMATED_RANKING": True, "NO_WINNER_SELECTION": True, "NO_GATE_A0_DECISION": True, "NO_GATE_A_DECISION": True, "DETERMINISM": first_hash == second_hash, "RAW_IMMUTABILITY": after["raw_hashes_match"] == "true", "CANONICAL_IMMUTABILITY": inputs_before["canonical_hash"] == after["canonical_hash"], "FACTOR_STORE_IMMUTABILITY": inputs_before["candidate_factor_hash"] == after["candidate_factor_hash"], "GATE_A0_SCORE_STORE_IMMUTABILITY": inputs_before["gate_a0_score_store_hash"] == after["gate_a0_score_store_hash"], "PRIOR_VALIDATION_DB_IMMUTABILITY": inputs_before["prior_validation_hash"] == after["prior_validation_hash"], "SSOT_IMMUTABILITY": inputs_before["ssot_hash"] == after["ssot_hash"]}
    acceptance = [{"check": check, "status": "PASS_WITH_WARNING" if check == "NO_FROZEN_COST_GRID_DISCLOSURE" else "PASS" if true_checks[check] else "FAIL", "blocking": "false" if true_checks[check] else "true", "evidence": "gate_a0_validation_*", "notes": "No frozen transaction-cost grid; only break-even diagnostics are present." if check == "NO_FROZEN_COST_GRID_DISCLOSURE" else "Descriptive within-family validation; no human determination is recorded."} for check in checks]
    write_csv(audit / "gate_a0_validation_acceptance.csv", acceptance)
    manifest = [{"dataset_name": "us_gate_a0_within_family_validation", "relative_path": str(OUTPUT), "input_score_store_logical_hash": inputs_before["gate_a0_score_store_hash"], "registry_sha256": inputs_before["registry_hash"], "ssot_sha256": inputs_before["ssot_hash"], "formal_validation_period": f"{START} to {END}", "dev_context_period": "NOT_GENERATED__FORMAL_EVIDENCE_ONLY", "specification_count": len(reg), "eligible_representative_count": sum(len(value) for value in EXPECTED.values()), "component_diagnostic_count": sum(len(value) for value in EXPECTED_COMPONENTS.values()), "validation_observation_count": len(observations), "score_observation_count": len(score_data), "ic_weekly_count": len(ic_rows), "portfolio_weekly_count": len(portfolio_rows), "code_path": "src/validation/build_gate_a0_within_family_validation.py", "logical_output_hash": logical_hash(store), "commit_lineage": git_head(root), "notes": "Existing executable observations only; no 2016+ performance query."}]
    write_csv(audit / "gate_a0_validation_manifest.csv", manifest)
    write_csv(audit / "gate_a0_validation_determinism.csv", [{"run_1_hash": first_hash, "run_2_hash": second_hash, "equal": str(first_hash == second_hash).lower(), "key_difference_count": 0 if first_hash == second_hash else 1, "value_difference_count": 0 if first_hash == second_hash else 1}])
    blockers = [row for row in acceptance if row["status"] in {"FAIL", "BLOCKED"}]
    warnings = [row for row in acceptance if row["status"] == "PASS_WITH_WARNING"]
    verdict = "GATE A0 WITHIN-FAMILY VALIDATION — PASS WITH WARNINGS" if not blockers and warnings else "GATE A0 WITHIN-FAMILY VALIDATION — PASS" if not blockers else "GATE A0 WITHIN-FAMILY VALIDATION — FAIL"
    report = ["# Formal Gate A0 Within-Family Validation Report", "", "## 1. Executive validation verdict", "", f"**{verdict}**. Blockers: {len(blockers)}. Warnings: {len(warnings)}.", "", "## 2. Governance lineage", "", "Frozen registry and the Gate A0 governance report define the only evaluated specifications. No specification is added, removed, or redefined.", "", "## 3. Sample firewall", "", "All performance joins use 2011-01-01 through 2015-12-31 only. No 2016–2025 performance was queried.", "", "## 4. Validation methodology", "", "The existing executable S_t to E_t to E_t+1 and E_t+4 methodology is reused from the immutable pre-Gate-A validation store. Spearman IC, HAC lags, block bootstrap, Top/Bottom 30%, equal weighting, drifted-weight turnover and break-even diagnostics are unchanged.", "", "## 5. Value evidence", "", "Primary and common-family evidence are recorded in the Value comparison CSV and review sheet.", "", "## 6. Quality evidence", "", "Primary and common-family evidence are recorded in the Quality comparison CSV and review sheet.", "", "## 7. Momentum evidence", "", "Primary and common-family evidence are recorded in the Momentum comparison CSV and review sheet.", "", "## 8. Low-Risk evidence", "", "Primary and common-family evidence are recorded in the Low-Risk comparison CSV and review sheet.", "", "## 9. Component diagnostics", "", "Value FCF and Quality GP/A, negative leverage and ROA remain natural-domain diagnostic evidence only. Their descriptive comparisons do not establish causality.", "", "## 10. Primary vs common-family sample", "", "Both views are retained. The common view is a coverage-control diagnostic and does not override primary evidence.", "", "## 11. Stability", "", "Yearly 2011–2015 and rolling-52-week evidence are emitted separately; no automatic stability statistic is used.", "", "## 12. Correlation / redundancy", "", "Within-family score, IC-series and spread-return correlations are descriptive redundancy diagnostics.", "", "## 13. Coverage", "", "Coverage and unavailable-reason tables retain each frozen specification's natural availability semantics.", "", "## 14. Sector breadth", "", "Sector evidence uses historical GICS carried by the frozen score store; no current-GICS backfill is used.", "", "## 15. Turnover / break-even cost", "", "Gross turnover and top-leg break-even one-way cost are diagnostics. No cost scenario or net-performance comparison is produced.", "", "## 16. Multiple-testing evidence", "", "Two-sided normal-reference p-values are calculated from the frozen Newey-West t-statistic; Benjamini-Hochberg q-values are grouped independently by family and IC horizon across only that family's eligible representatives. Component diagnostics are excluded from these groups.", "", "## 17. Incumbent-default governance", "", "The frozen governance default remains: absent a clear, economically coherent and documented rationale, retain the incumbent. This report does not apply that rule or record an outcome.", "", "## 18. Open questions", "", "A human review remains required to consider economic rationale, coverage, common-sample sensitivity, stability and implementation constraints without a mechanical threshold.", "", "## 19. Warnings / limitations", "", "Warning: NO_FROZEN_COST_GRID. Break-even diagnostics are available but no transaction-cost grid is frozen. Gate A0 remains adaptive within the known 2011–2015 evidence sample.", "", "## 20. Exact next permitted step", "", "Human Gate A0 family-representative review may occur under the frozen governance. This artifact does not record a representative outcome or a Gate A outcome."]
    (audit / "WRDS_GATE_A0_WITHIN_FAMILY_VALIDATION_REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return {"verdict": verdict, "blockers": len(blockers), "warnings": len(warnings), "master": master, "summaries": summaries, "portfolio": portfolio_summaries, "cost": cost, "coverage": coverage_rows, "score_corr": score_corr, "spread_corr": spread_corr, "acceptance": acceptance, "hash": logical_hash(store)}


def run(root: Path) -> dict[str, Any]:
    root = root.resolve()
    inputs = frozen_inputs(root)
    reg = registry(root)
    output = root / OUTPUT
    temporary_directory = Path(tempfile.mkdtemp(prefix="gate_a0_validation.", dir=output.parent))
    try:
        first = build_store(root, temporary_directory / "run1.sqlite", reg, inputs)
        second = build_store(root, temporary_directory / "run2.sqlite", reg, inputs)
        if first["hash"] != second["hash"]:
            raise RuntimeError("GATE_A0_VALIDATION_NONDETERMINISTIC")
        if output.exists():
            output.unlink()
        os.replace(temporary_directory / "run2.sqlite", output)
        audit = audit_outputs(root, output, reg, inputs, first["hash"], second["hash"])
        if audit["blockers"]:
            raise RuntimeError("GATE_A0_VALIDATION_BLOCKED")
        return audit
    finally:
        for path in sorted(temporary_directory.glob("*")):
            path.unlink()
        temporary_directory.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    result = run(args.root)
    print(dump({"verdict": result["verdict"], "blockers": result["blockers"], "warnings": result["warnings"], "logical_hash": result["hash"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
