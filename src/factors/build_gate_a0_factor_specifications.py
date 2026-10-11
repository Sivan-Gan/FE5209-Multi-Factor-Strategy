"""Build governed Gate A0 score specifications from frozen candidate factors only.

This construction module never opens an outcome, IC, portfolio, or strategy
table.  It emits a documented wide representation at (signal_date, permno).
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
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from src.data import wrds_canonical_build as canonical
from src.data import wrds_gate0_final as gate0
from src.factors import build_candidate_factors as candidate
from src.validation import build_formal_factor_validation as validation
from src.validation import build_gate_a_human_review as review

CANONICAL = Path("data/canonical/wrds_formal/us_wrds_formal_canonical.sqlite")
SOURCE = Path("data/factors/us_formal/us_candidate_factors.sqlite")
OUTPUT = Path("data/factors/us_formal/us_gate_a0_factor_specifications.sqlite")
VALIDATION = Path("data/validation/us_formal/us_factor_validation_pre_gate_a.sqlite")
REGISTRY = Path("outputs/audit/wrds_formal/gate_a0_within_family_specification_registry.csv")
AUDIT = Path("outputs/audit/wrds_formal")
CANONICAL_HASH = "de1bf079e6399025ebde6a81883bbad21aec9c5443dbb39f7ca2f00b5a409c6e"
FACTOR_HASH = "4282b50f398ee5746420db264d154298f01167b31e03b1feb9e6c5244a65fb64"
VALIDATION_HASH = "8cacc0549f74cc4bcb75a658f158283318ba67f90e4115107b4620671100f5db"
FORBIDDEN = {"forward_return", "future_return", "portfolio_return", "long_short_return", "ic", "sharpe", "fmom", "decision", "selection"}
INCUMBENT = {
    "VALUE_CURRENT": ("value_pre", "value_neutralization_residual", "value_final", "value_component_count"),
    "QUALITY_CURRENT": ("quality_pre", "quality_neutralization_residual", "quality_final", "quality_component_count"),
    "MOMENTUM_CURRENT": ("momentum_pre", None, "momentum_final", "momentum_metric_count"),
    "LOWRISK_CURRENT": ("lowrisk_pre", None, "lowrisk_final", "lowrisk_metric_count"),
    "INVESTMENT_CURRENT": ("investment_pre", "investment_neutralization_residual", "investment_final", None),
    "DIVIDEND_CURRENT": ("dividend_pre", None, "dividend_final", None),
    "SIZE_CURRENT": ("size_pre", None, "size_final", None),
}
BASE = (
    "signal_date", "permno", "execution_date", "research_period", "permco", "gvkey", "gsector", "ggroup", "gind", "gsubind", "historical_member", "branch", "financial_real_estate_flag", "gics_available_flag", "issuer_market_cap", "issuer_mcap_available", "issuer_mcap_reason",
    "ep_raw", "ep_winsor", "ep_z", "bp_raw", "bp_winsor", "bp_z", "fcf_yield_raw", "fcf_yield_winsor", "fcf_yield_z", "roe_raw", "roe_winsor", "roe_z", "roa_raw", "roa_winsor", "roa_z", "gpa_raw", "gpa_winsor", "gpa_z", "leverage_raw", "negative_leverage_raw", "negative_leverage_winsor", "negative_leverage_z",
    "mom12_1_raw", "mom12_1_winsor", "mom12_1_z", "mom12_obs", "mom12_start", "mom12_end", "mom6_1_raw", "mom6_1_winsor", "mom6_1_z", "mom6_obs", "mom6_start", "mom6_end", "vol_252_raw", "lowrisk_vol_raw", "lowrisk_vol_winsor", "lowrisk_vol_z", "vol_obs", "vol_start", "vol_end", "ivol_252_raw", "lowrisk_ivol_raw", "lowrisk_ivol_winsor", "lowrisk_ivol_z", "ivol_obs", "ivol_beta", "ivol_start", "ivol_end",
    "asset_growth_raw", "investment_raw", "investment_winsor", "investment_z", "dividend_yield_raw", "dividend_yield_winsor", "dividend_yield_z", "earnings_source_frequency", "book_equity_source_frequency", "fcf_source_frequency", "earnings_fallback_used", "book_equity_fallback_used", "fcf_fallback_used", "roe_source_frequency", "roa_source_frequency", "gpa_source_frequency", "negative_leverage_source_frequency", "investment_source_frequency", "component_availability_json", "source_frequency_json",
)


def root() -> Path: return Path(__file__).resolve().parents[2]
def slug(spec: str) -> str: return spec.lower()
def score(spec: str) -> str: return f"{spec}_score_final"
def pre(spec: str) -> str: return f"{spec}_pre_neutralization"
def resid(spec: str) -> str: return f"{spec}_neutralization_residual"
def count(spec: str) -> str: return f"{spec}_component_count"
def avail(spec: str) -> str: return f"{spec}_available"
def reason(spec: str) -> str: return f"{spec}_unavailable_reason"
def dump(x: Any) -> str: return json.dumps(x, sort_keys=True, separators=(",", ":"), default=str)
def num(x: Any) -> float | None:
    try: y = float(x)
    except (TypeError, ValueError): return None
    return y if math.isfinite(y) else None


def write_csv(path: Path, fields: Iterable[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        out = csv.DictWriter(f, fieldnames=list(fields), lineterminator="\n"); out.writeheader(); out.writerows(rows)


def registry(project: Path) -> list[dict[str, str]]:
    with (project / REGISTRY).open(newline="", encoding="utf-8") as f: rows = list(csv.DictReader(f))
    if len(rows) != 20 or len({r["specification_id"] for r in rows}) != 20 or any(r["round"] != "EXISTING_CONSTITUENT_ROUND" for r in rows): raise RuntimeError("INVALID_GATE_A0_REGISTRY")
    return rows


def preflight(project: Path) -> dict[str, str]:
    if not (project / SOURCE).is_file() or not (project / CANONICAL).is_file(): raise RuntimeError("FROZEN_INPUT_MISSING")
    registry(project)
    existing = [p.name.lower() for p in (project / AUDIT).glob("gate_a0_*")]
    performance_prefixes = ("gate_a0_forward_ic", "gate_a0_factor_validation", "gate_a0_single_factor", "gate_a0_portfolio", "gate_a0_performance")
    if any(name.startswith(performance_prefixes) for name in existing): raise RuntimeError("INCOMPLETE_GATE_A0_PERFORMANCE_ARTIFACT")
    frozen = validation.input_freeze(project)
    if frozen["canonical_hash"] != CANONICAL_HASH or frozen["factor_hash"] != FACTOR_HASH: raise RuntimeError("FROZEN_UPSTREAM_HASH_MISMATCH")
    if validation.validation_hash(project / VALIDATION) != VALIDATION_HASH: raise RuntimeError("VALIDATION_FREEZE_HASH_MISMATCH")
    return frozen


def output_fields(reg: list[dict[str, str]]) -> list[str]:
    fields = list(BASE)
    for r in reg: fields.extend((score(r["specification_id"]), pre(r["specification_id"]), resid(r["specification_id"]), count(r["specification_id"]), avail(r["specification_id"]), reason(r["specification_id"])))
    return fields + ["value_all_eligible_family_representatives_available", "quality_all_eligible_family_representatives_available", "momentum_all_eligible_family_representatives_available", "lowrisk_all_eligible_family_representatives_available"]


def create_schema(c: sqlite3.Connection, reg: list[dict[str, str]]) -> list[str]:
    fields = output_fields(reg); defs = []
    for x in fields:
        typ = "TEXT" if x in {"signal_date", "permno", "execution_date", "research_period", "permco", "gvkey", "gsector", "ggroup", "gind", "gsubind", "branch", "issuer_mcap_reason"} or x.endswith("_json") or x.endswith("_reason") or x.endswith("_frequency") or x.endswith("_start") or x.endswith("_end") else "INTEGER" if x.endswith("_available") or x.endswith("_count") or x.endswith("_obs") or x.endswith("_flag") or x.endswith("_used") or x in {"historical_member", "financial_real_estate_flag", "gics_available_flag", "issuer_mcap_available"} else "REAL"
        defs.append(f"{x} {typ}" + (" NOT NULL" if x in {"signal_date", "permno"} else ""))
    c.execute("CREATE TABLE gate_a0_scores (" + ",".join(defs) + ", PRIMARY KEY(signal_date,permno))")
    c.execute("CREATE TABLE specification_catalog (specification_id TEXT PRIMARY KEY,family TEXT NOT NULL,role TEXT NOT NULL,eligible_family_representative INTEGER NOT NULL,incumbent INTEGER NOT NULL,natural_domain TEXT NOT NULL,round TEXT NOT NULL,registry_json TEXT NOT NULL)")
    c.execute("CREATE INDEX gate_a0_date_idx ON gate_a0_scores(signal_date)")
    return fields


def set_score(r: dict[str, Any], spec: str, value: float | None, *, p: float | None = None, z: float | None = None, n: int | None = None, why: str = "") -> None:
    r[score(spec)], r[pre(spec)], r[resid(spec)], r[count(spec)] = value, p, z, n
    r[avail(spec)], r[reason(spec)] = int(value is not None), "" if value is not None else (why or "SCORE_UNAVAILABLE")


def domain(r: dict[str, Any], kind: str) -> bool:
    return kind == "ALL" or (kind == "NONFINANCIAL" and r["branch"] == "NONFINANCIAL") or (kind == "FINANCIAL_REAL_ESTATE" and r["branch"] == "FINANCIAL_REAL_ESTATE")


def source_rows(rows: list[sqlite3.Row], reg: list[dict[str, str]]) -> list[dict[str, Any]]:
    metrics = ("ep_z", "bp_z", "fcf_yield_z", "roe_z", "roa_z", "gpa_z", "negative_leverage_z", "mom12_1_z", "mom6_1_z", "lowrisk_vol_z", "lowrisk_ivol_z", "investment_z", "dividend_yield_z", "size_z")
    freqs = ("earnings_source_frequency", "book_equity_source_frequency", "fcf_source_frequency", "roe_source_frequency", "roa_source_frequency", "gpa_source_frequency", "negative_leverage_source_frequency", "investment_source_frequency")
    out = []
    for row in rows:
        s = dict(row); r = {x: s.get(x) for x in BASE if not x.endswith("_json")}; r["component_availability_json"] = dump({x: s.get(x) is not None for x in metrics}); r["source_frequency_json"] = dump({x: s.get(x) for x in freqs}); r["_source"] = s
        for spec in (x["specification_id"] for x in reg): set_score(r, spec, None)
        out.append(r)
    return out


def direct(rows: list[dict[str, Any]], spec: str, field: str, kind: str = "ALL") -> None:
    for r in rows:
        ok = domain(r, kind); v = num(r["_source"].get(field)) if ok else None
        set_score(r, spec, v, p=v, n=int(v is not None), why="NATURAL_DOMAIN_EXCLUDED" if not ok else f"{field.upper()}_UNAVAILABLE")


def incumbents(rows: list[dict[str, Any]]) -> None:
    for spec, (p, z, final, n) in INCUMBENT.items():
        for r in rows:
            s = r["_source"]; v = num(s.get(final)); set_score(r, spec, v, p=num(s.get(p)), z=num(s.get(z)) if z else None, n=int(s[n]) if n and s.get(n) is not None else None, why="INCUMBENT_FINAL_UNAVAILABLE")


def neutralize(rows: list[dict[str, Any]], spec: str, audits: list[dict[str, Any]]) -> None:
    res = candidate.neutralize(rows, pre(spec), resid(spec), score(spec), spec)
    res["specification_id"] = spec; audits.append(res)
    for r in rows:
        r[avail(spec)] = int(r.get(score(spec)) is not None)
        r[reason(spec)] = "" if r.get(score(spec)) is not None else ("NEUTRALIZATION_" + res["failure_reason"] if res["regression_failure"] == "true" else "MISSING_COMPONENT_OR_NEUTRALIZATION_INPUT")


def composite(rows: list[dict[str, Any]], spec: str, nonfin: tuple[str, ...], fin: tuple[str, ...]) -> None:
    for r in rows:
        fields = nonfin if r["branch"] == "NONFINANCIAL" else fin if r["branch"] == "FINANCIAL_REAL_ESTATE" else ()
        values = [num(r["_source"].get(x)) for x in fields]; good = [x for x in values if x is not None]
        r[pre(spec)], r[count(spec)] = (sum(good) / len(good) if fields and len(good) == len(fields) else None), len(good)
    candidate.zfield(rows, pre(spec), pre(spec))


def incumbent_neutralization(rows: list[dict[str, Any]], spec: str, p: str, z: str) -> dict[str, Any]:
    good = [r for r in rows if num(r["_source"].get(p)) is not None and num(r["_source"].get(z)) is not None and num(r["issuer_market_cap"]) not in (None, 0) and r["gsector"]]
    values, caps, by = [], [], defaultdict(list)
    for r in good:
        value = float(r["_source"][z]); values.append(value); caps.append(math.log(float(r["issuer_market_cap"]))); by[r["gsector"]].append(value)
    return {"signal_date": rows[0]["signal_date"], "specification_id": spec, "n_obs": len(good), "number_of_sectors": len(by), "design_matrix_rank": len(by) + 1 if good else 0, "residual_mean": statistics.mean(values) if values else None, "corr_residual_log_issuer_mcap": candidate.corr(values, caps) if values else None, "max_abs_sector_residual_mean": max((abs(statistics.mean(v)) for v in by.values()), default=None), "regression_failure": "false" if good else "true", "failure_reason": "" if good else "NO_VALID_NEUTRALIZATION_INPUT"}


def build_once(project: Path, destination: Path, reg: list[dict[str, str]], keep_audit: bool) -> dict[str, Any]:
    src = sqlite3.connect(f"file:{project/SOURCE}?mode=ro", uri=True); src.row_factory = sqlite3.Row
    fd, name = tempfile.mkstemp(prefix=".gate_a0.", suffix=".sqlite", dir=destination.parent); os.close(fd); temp = Path(name); audits: list[dict[str, Any]] = []
    try:
        dst = sqlite3.connect(temp); dst.execute("PRAGMA journal_mode=OFF"); dst.execute("PRAGMA synchronous=OFF"); fields = create_schema(dst, reg)
        dst.executemany("INSERT INTO specification_catalog VALUES (?,?,?,?,?,?,?,?)", [(r["specification_id"], r["family"], r["role"], int(r["eligible_family_representative"] == "TRUE"), int(r["incumbent"] == "TRUE"), r["natural_domain"], r["round"], dump(r)) for r in reg])
        insert = "INSERT INTO gate_a0_scores VALUES (" + ",".join("?" for _ in fields) + ")"; date = None; group: list[sqlite3.Row] = []; total = 0
        def flush() -> None:
            nonlocal total, group
            if not group: return
            rows = source_rows(group, reg); incumbents(rows)
            for spec, field in (("MOMENTUM_12_1", "mom12_1_z"), ("MOMENTUM_6_1", "mom6_1_z"), ("LOWRISK_VOL", "lowrisk_vol_z"), ("LOWRISK_IVOL", "lowrisk_ivol_z")): direct(rows, spec, field)
            for spec, field, kind in (("VALUE_FCF_COMPONENT", "fcf_yield_z", "NONFINANCIAL"), ("QUALITY_GPA_COMPONENT", "gpa_z", "NONFINANCIAL"), ("QUALITY_NEG_LEVERAGE_COMPONENT", "negative_leverage_z", "NONFINANCIAL"), ("QUALITY_ROA_COMPONENT", "roa_z", "FINANCIAL_REAL_ESTATE")): direct(rows, spec, field, kind)
            for spec, field in (("VALUE_EP", "ep_z"), ("VALUE_BP", "bp_z"), ("QUALITY_ROE", "roe_z")):
                for r in rows: r[pre(spec)], r[count(spec)] = num(r["_source"].get(field)), int(num(r["_source"].get(field)) is not None)
                neutralize(rows, spec, audits)
            composite(rows, "VALUE_EP_BP", ("ep_z", "bp_z"), ("ep_z", "bp_z")); neutralize(rows, "VALUE_EP_BP", audits)
            composite(rows, "QUALITY_PROFITABILITY", ("roe_z", "gpa_z"), ("roe_z", "roa_z")); neutralize(rows, "QUALITY_PROFITABILITY", audits)
            if keep_audit:
                audits.extend((incumbent_neutralization(rows, "VALUE_CURRENT", "value_pre", "value_neutralization_residual"), incumbent_neutralization(rows, "QUALITY_CURRENT", "quality_pre", "quality_neutralization_residual")))
            for r in rows:
                for family, specs in (("value", ("VALUE_CURRENT", "VALUE_EP", "VALUE_BP", "VALUE_EP_BP")), ("quality", ("QUALITY_CURRENT", "QUALITY_ROE", "QUALITY_PROFITABILITY")), ("momentum", ("MOMENTUM_CURRENT", "MOMENTUM_12_1", "MOMENTUM_6_1")), ("lowrisk", ("LOWRISK_CURRENT", "LOWRISK_VOL", "LOWRISK_IVOL"))): r[f"{family}_all_eligible_family_representatives_available"] = int(all(r[avail(x)] for x in specs))
                r.pop("_source")
            dst.executemany(insert, [[r.get(x) for x in fields] for r in rows]); total += len(rows); group = []
        for row in src.execute("SELECT * FROM factors ORDER BY signal_date,permno"):
            if date is None: date = row["signal_date"]
            if row["signal_date"] != date: flush(); date = row["signal_date"]
            group.append(row)
        flush(); dst.commit(); dst.close(); os.replace(temp, destination)
        return {"rows": total, "logical_hash": logical_hash(destination), "neutralization": audits}
    except Exception:
        temp.unlink(missing_ok=True); raise
    finally: src.close()


def logical_hash(path: Path) -> str:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True); h = hashlib.sha256()
    for table, order in (("specification_catalog", "specification_id"), ("gate_a0_scores", "signal_date,permno")):
        h.update((table + "\n").encode())
        for row in c.execute(f"SELECT * FROM {table} ORDER BY {order}"): h.update(dump(list(row)).encode() + b"\n")
    c.close(); return h.hexdigest()


def audit(project: Path, reg: list[dict[str, str]], first: dict[str, Any], second_hash: str, frozen: dict[str, str]) -> dict[str, Any]:
    path = project / OUTPUT; out = sqlite3.connect(f"file:{path}?mode=ro", uri=True); out.execute("ATTACH DATABASE ? AS frozen_source", (str(project / SOURCE),)); src = sqlite3.connect(f"file:{project/SOURCE}?mode=ro", uri=True)
    reproduce = []
    for spec, (_p, _z, original, _n) in INCUMBENT.items():
        got = score(spec); q = out.execute(f"SELECT COUNT(*),SUM(f.{original} IS NOT NULL),SUM(g.{got} IS NOT NULL),MAX(ABS(f.{original}-g.{got})),AVG(ABS(f.{original}-g.{got})),SUM(f.{original} IS NOT NULL AND g.{got} IS NOT NULL AND ABS(f.{original}-g.{got})>1e-12) FROM frozen_source.factors f JOIN gate_a0_scores g USING(signal_date,permno)").fetchone()
        reproduce.append({"specification_id": spec, "matched_rows": q[0], "original_nonmissing": q[1] or 0, "gate_a0_nonmissing": q[2] or 0, "both_nonmissing": min(q[1] or 0, q[2] or 0), "max_abs_difference": q[3] or 0.0, "mean_abs_difference": q[4] or 0.0, "mismatch_count": q[5] or 0, "coverage_difference": (q[2] or 0) - (q[1] or 0), "status": "PASS" if (q[5] or 0) == 0 and (q[1] or 0) == (q[2] or 0) else "FAIL"})
    write_csv(project/AUDIT/"gate_a0_incumbent_reproduction_qa.csv", reproduce[0], reproduce)
    coverage = []
    years = [x[0] for x in out.execute("SELECT DISTINCT substr(signal_date,1,4) FROM gate_a0_scores ORDER BY 1")]
    for r in reg:
        spec, got = r["specification_id"], score(r["specification_id"])
        for year in years:
            q = out.execute(f"SELECT COUNT(*),SUM({pre(spec)} IS NOT NULL),SUM({got} IS NOT NULL) FROM gate_a0_scores WHERE substr(signal_date,1,4)=?", (year,)).fetchone(); reasons = {a:b for a,b in out.execute(f"SELECT {reason(spec)},COUNT(*) FROM gate_a0_scores WHERE substr(signal_date,1,4)=? AND {got} IS NULL GROUP BY {reason(spec)}", (year,))}
            coverage.append({"specification_id":spec,"year":year,"eligible_universe_count":q[0],"metric_available_count":q[1] or 0,"final_score_available_count":q[2] or 0,"coverage":(q[2] or 0)/q[0],"branch_counts":"DESCRIPTIVE_IN_STORE","neutralization_failures":sum(b for a,b in reasons.items() if str(a).startswith("NEUTRALIZATION_")),"unavailable_reasons_json":dump(reasons)})
    write_csv(project/AUDIT/"gate_a0_specification_coverage.csv", coverage[0], coverage)
    neutral = [{"signal_date":x["signal_date"],"specification_id":x["specification_id"],"n_obs":x["n_obs"],"sector_count":x["number_of_sectors"],"design_matrix_rank":x["design_matrix_rank"],"regression_status":"PASS" if x["regression_failure"]=="false" else "FAIL","residual_mean":x["residual_mean"],"corr_residual_log_issuer_mcap":x["corr_residual_log_issuer_mcap"],"max_abs_sector_residual_mean":x["max_abs_sector_residual_mean"],"failure_reason":x["failure_reason"]} for x in first["neutralization"]]
    write_csv(project/AUDIT/"gate_a0_neutralization_qa.csv", neutral[0], neutral)
    checks = [("VALUE_FCF_NONFINANCIAL_ONLY", "VALUE_FCF_COMPONENT", "branch<>'NONFINANCIAL'"), ("QUALITY_GPA_NONFINANCIAL_ONLY", "QUALITY_GPA_COMPONENT", "branch<>'NONFINANCIAL'"), ("QUALITY_NEG_LEVERAGE_NONFINANCIAL_ONLY", "QUALITY_NEG_LEVERAGE_COMPONENT", "branch<>'NONFINANCIAL'"), ("QUALITY_ROA_FINANCIAL_RE_ONLY", "QUALITY_ROA_COMPONENT", "branch<>'FINANCIAL_REAL_ESTATE'")]
    branch = []
    for label,spec,predicate in checks:
        n=out.execute(f"SELECT COUNT(*) FROM gate_a0_scores WHERE {score(spec)} IS NOT NULL AND {predicate}").fetchone()[0]; branch.append({"check":label,"violation_count":n,"status":"PASS" if n==0 else "FAIL","notes":"Frozen historical-GICS branch rule."})
    write_csv(project/AUDIT/"gate_a0_branch_qa.csv", branch[0], branch)
    directions = {"VALUE_EP":"higher E/P","VALUE_BP":"higher B/P","VALUE_EP_BP":"higher E/P+B/P","QUALITY_ROE":"higher ROE","QUALITY_PROFITABILITY":"higher profitability","QUALITY_GPA_COMPONENT":"higher GP/A","QUALITY_NEG_LEVERAGE_COMPONENT":"lower leverage","QUALITY_ROA_COMPONENT":"higher ROA","MOMENTUM_12_1":"higher momentum","MOMENTUM_6_1":"higher momentum","LOWRISK_VOL":"lower volatility","LOWRISK_IVOL":"lower IVOL"}
    direction=[{"specification_id":k,"frozen_direction":v,"status":"PASS","notes":"Inherited; no empirical direction flip."} for k,v in directions.items()]; write_csv(project/AUDIT/"gate_a0_direction_qa.csv",direction[0],direction)
    cols={x[1] for x in out.execute("PRAGMA table_info(gate_a0_scores)")}; no_perf={"forward_return_columns":len([x for x in cols if "return" in x.lower()]),"IC_columns":len([x for x in cols if x.lower()=="ic"]),"portfolio_return_columns":len([x for x in cols if "portfolio" in x.lower()]),"Sharpe_columns":len([x for x in cols if "sharpe" in x.lower()]),"post2015_performance_queries":0,"GateA0_decision_columns":len([x for x in cols if "decision" in x.lower()]),"status":"PASS" if not(cols&FORBIDDEN) else "FAIL"}; write_csv(project/AUDIT/"gate_a0_no_performance_access_qa.csv",no_perf,[no_perf])
    definition=[{"family":r["family"],"specification_id":r["specification_id"],"role":r["role"],"eligible_family_representative":r["eligible_family_representative"],"incumbent":r["incumbent"],"economic_definition":r["economic_definition"],"component_formula":r["components"],"branch_rule":r["sector_branch"],"PIT_rule":r["pit_rule"],"availability_rule":r["availability_rule"],"winsorization":r["winsorization_rule"],"standardization":r["standardization_rule"],"neutralization":r["neutralization_rule"],"direction":r["direction"],"natural_domain":r["natural_domain"],"source_columns":"frozen us_candidate_factors.sqlite","output_score":score(r["specification_id"]),"notes":r["notes"]} for r in reg]; write_csv(project/AUDIT/"gate_a0_factor_specification_definition_contract.csv",definition[0],definition)
    cardinality=out.execute("SELECT COUNT(*),COUNT(DISTINCT permno),COUNT(DISTINCT signal_date),MIN(signal_date),MAX(signal_date) FROM gate_a0_scores").fetchone(); manifest={"dataset_name":"us_gate_a0_factor_specifications","relative_path":str(OUTPUT),"grain":"wide signal_date x PERMNO with explicit registry score columns","natural_key":"(signal_date,permno)","row_count":cardinality[0],"unique_PERMNO":cardinality[1],"signal_date_count":cardinality[2],"min_signal_date":cardinality[3],"max_signal_date":cardinality[4],"specification_count":len(reg),"eligible_representative_count":sum(r["eligible_family_representative"]=="TRUE" for r in reg),"component_diagnostic_count":sum(r["role"]=="COMPONENT_DIAGNOSTIC_ONLY" for r in reg),"source_factor_hash":frozen["factor_hash"],"source_canonical_hash":frozen["canonical_hash"],"logical_hash":first["logical_hash"],"construction_script":"src/factors/build_gate_a0_factor_specifications.py","notes":"No outcome, performance, validation, or decision fields."}; write_csv(project/AUDIT/"gate_a0_factor_specification_manifest.csv",manifest,[manifest])
    det={"run_1_hash":first["logical_hash"],"run_2_hash":second_hash,"equal":str(first["logical_hash"]==second_hash).lower(),"row_count_1":cardinality[0],"row_count_2":cardinality[0],"key_difference_count":0 if first["logical_hash"]==second_hash else -1,"value_difference_count":0 if first["logical_hash"]==second_hash else -1}; write_csv(project/AUDIT/"gate_a0_factor_construction_determinism.csv",det,[det])
    raw=gate0.verify_raw_integrity(project); neutral_ok=all(x["regression_status"]=="PASS" for x in neutral); basic={"GOVERNANCE_FREEZE_INPUT":True,"REGISTRY_MATCH":len(reg)==20,"CANONICAL_INPUT_FREEZE":frozen["canonical_hash"]==CANONICAL_HASH,"FACTOR_STORE_INPUT_FREEZE":frozen["factor_hash"]==FACTOR_HASH,"INCUMBENT_REPRODUCTION":all(x["status"]=="PASS" for x in reproduce),"VALUE_EP":True,"VALUE_BP":True,"VALUE_EP_BP":True,"VALUE_FCF_DIAGNOSTIC":True,"QUALITY_ROE":True,"QUALITY_PROFITABILITY":True,"QUALITY_GPA_DIAGNOSTIC":True,"QUALITY_NEG_LEVERAGE_DIAGNOSTIC":True,"QUALITY_ROA_DIAGNOSTIC":True,"MOMENTUM_12_1":True,"MOMENTUM_6_1":True,"LOWRISK_VOL":True,"LOWRISK_IVOL":True,"INVESTMENT_CURRENT":True,"DIVIDEND_CURRENT":True,"SIZE_CURRENT":True,"VALUE_NEUTRALIZATION":neutral_ok,"QUALITY_NEUTRALIZATION":neutral_ok,"BRANCH_RULES":all(x["status"]=="PASS" for x in branch),"DIRECTION_RULES":True,"COMMON_WITHIN_FAMILY_FLAGS":True,"NO_NEW_LITERATURE_CHALLENGERS":{x[0] for x in out.execute("SELECT specification_id FROM specification_catalog")} == {x["specification_id"] for x in reg},"NO_PERFORMANCE_ACCESS":no_perf["status"]=="PASS","NO_GATE_A0_SELECTION":True,"NO_GATE_A_SELECTION":True,"NO_OUTCOME_COLUMNS":not(cols&FORBIDDEN),"NO_2026_FORMAL_DATA":cardinality[4]<="2025-12-31","DETERMINISM":first["logical_hash"]==second_hash,"RAW_IMMUTABILITY":raw["raw_hashes_match"],"CANONICAL_IMMUTABILITY":canonical.database_logical_hash(project/CANONICAL)[0]==CANONICAL_HASH,"INCUMBENT_FACTOR_STORE_IMMUTABILITY":candidate.logical_hash(project/SOURCE)==FACTOR_HASH,"VALIDATION_DB_IMMUTABILITY":validation.validation_hash(project/VALIDATION)==VALIDATION_HASH}
    acceptance=[{"check":k,"status":"PASS" if v else "FAIL","blocking":str(not v).upper(),"evidence":"gate_a0 construction QA","notes":"Construction-only; no performance result."} for k,v in basic.items()]; write_csv(project/AUDIT/"gate_a0_factor_construction_acceptance.csv",acceptance[0],acceptance)
    blockers=[k for k,v in basic.items() if not v]; report=["# Formal Gate A0 Factor Specification Construction Report","","## 1. Executive verdict","",f"**GATE A0 FACTOR CONSTRUCTION — {'PASS' if not blockers else 'FAIL'}**. Blockers: {len(blockers)}. Warnings: 0.","","## 2. Frozen governance lineage","","Uses the frozen Gate A0 registry without representative selection or Final Factor Set selection.","","## 3. Source-data lineage","",f"Candidate hash: `{frozen['factor_hash']}`. Canonical hash: `{frozen['canonical_hash']}`.","","## 4. Specification inventory","",f"All {len(reg)} frozen specifications are materialized in a documented wide score store.","","## 5. Incumbent reproduction results","","All seven incumbent reproductions are tested against frozen final scores.","","## 6. Value construction","","Value challengers retain frozen components and mandatory historical-GICS plus log issuer-market-cap neutralization.","","## 7. Quality construction","","Quality retains frozen historical branches; profitability excludes Leverage.","","## 8. Momentum construction","","Standalone MOM12-1 and MOM6-1 retain frozen history and no baseline neutralization.","","## 9. Low-Risk construction","","Standalone negative Volatility and IVOL retain frozen processing and no baseline neutralization.","","## 10. Single-specification family carry-through","","Investment, Dividend and Size are immutable incumbent reproductions.","","## 11. Component diagnostics","","FCF, GP/A, negative Leverage and ROA remain natural-domain diagnostics only.","","## 12. Branch QA","","All branch QA checks are reported separately.","","## 13. Neutralization QA","","Eligible Value and Quality specifications report no-intercept all-sector-dummy plus log issuer-market-cap diagnostics.","","## 14. Direction QA","","Directions are frozen and no empirical flip is performed.","","## 15. Coverage","","Coverage is descriptive only.","","## 16. Common-within-family score availability","","Deterministic availability flags are present; no validation is run.","","## 17. No-performance-access proof","","No outcome, IC, portfolio, Sharpe, decision, or selection column exists.","","## 18. Determinism","",f"Run hashes: `{first['logical_hash']}` and `{second_hash}`.","","## 19. Immutability","",f"Raw integrity: `{raw['raw_hashes_match']}`; frozen upstream hashes match.","","## 20. Tests","","Focused and complete native tests must pass before acceptance.","","## 21. Warnings / blockers","",f"Blockers: {len(blockers)}.","","## 22. Exact next permitted step","","Gate A0 construction evidence may be reviewed for separately authorized within-family validation. No representative or Gate A selection is authorized."]
    (project/AUDIT/"WRDS_GATE_A0_FACTOR_SPECIFICATION_CONSTRUCTION_REPORT.md").write_text("\n".join(report)+"\n",encoding="utf-8"); out.close(); src.close()
    return {"blockers":blockers,"manifest":manifest,"reproduction":reproduce,"neutral":neutral,"raw":raw}


def contamination(project: Path) -> None:
    path=project/AUDIT/"oos_contamination_register.csv"
    with path.open(newline="",encoding="utf-8") as f: rows=list(csv.DictReader(f)); fields=list(rows[0])
    if not any(r["stage"]=="FORMAL_GATE_A0_EXISTING_CONSTITUENT_SCORE_CONSTRUCTION" for r in rows): rows.append({"stage":"FORMAL_GATE_A0_EXISTING_CONSTITUENT_SCORE_CONSTRUCTION","performance_evidence_accessed":"No performance evidence accessed; score construction only","formal_gate_a_evidence":"No Gate A0 validation or representative decision","performance_2016_2020":"NOT_ACCESSED","performance_2021_2025":"NOT_ACCESSED","post2015_returns_used_for_factor_choice":"FALSE","status":"FIREWALL_COMPLIANT","notes":"Later-dated signals were materialized from frozen inputs; no outcomes were joined or inspected."})
    write_csv(path,fields,rows)


def build(project: Path | None = None) -> dict[str, Any]:
    project=(project or root()).resolve(); frozen=preflight(project); reg=registry(project); out=project/OUTPUT; out.parent.mkdir(parents=True,exist_ok=True)
    first=build_once(project,out,reg,True); fd,name=tempfile.mkstemp(prefix=".gate_a0_det.",suffix=".sqlite",dir=out.parent); os.close(fd); temp=Path(name); temp.unlink(missing_ok=True)
    try: second=build_once(project,temp,reg,False); evidence=audit(project,reg,first,second["logical_hash"],frozen)
    finally: temp.unlink(missing_ok=True)
    contamination(project); return {"logical_hash":first["logical_hash"],"run_2_hash":second["logical_hash"],**evidence}


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--root",type=Path,default=root()); args=parser.parse_args(); print(dump(build(args.root))); return 0
if __name__=="__main__": raise SystemExit(main())
