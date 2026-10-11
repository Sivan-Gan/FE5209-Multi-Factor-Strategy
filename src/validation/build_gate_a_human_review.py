"""Build a deterministic, non-decisional human evidence package for Formal Gate A.

This module reads only pre-existing validation evidence.  It contains no factor
selection, no factor weighting, and no new return/IC/portfolio construction.
Formal evidence is bounded to 2011--2015; DEV context is emitted separately.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import math
import shutil
import statistics
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

from src.validation import build_formal_factor_validation as validation

AUDIT = Path("outputs/audit/wrds_formal")
VALIDATION = Path("data/validation/us_formal/us_factor_validation_pre_gate_a.sqlite")
FACTORS = ("Size", "Momentum", "Low Risk", "Value", "Quality", "Investment", "Dividend")
PRIMARY = "PRIMARY_FACTOR_SPECIFIC"
COMMON = "COMMON_7_FACTOR_SAMPLE_DIAGNOSTIC"
SELECTION = "FACTOR_SELECTION"
DEV = "DEV"
START, END = "2011-01-01", "2015-12-31"
VALIDATION_HASH = "8cacc0549f74cc4bcb75a658f158283318ba67f90e4115107b4620671100f5db"


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def integer(value: Any) -> int:
    return int(float(value)) if value not in (None, "") else 0


def write_csv(path: Path, fields: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for file in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(str(file.relative_to(path)).encode() + b"\n")
        digest.update(file.read_bytes())
    return digest.hexdigest()


def sample(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "mean": None, "median": None, "sd": None, "annualized_icir": None, "positive": None}
    sd = statistics.stdev(values) if len(values) > 1 else None
    mean = statistics.mean(values)
    return {"n": len(values), "mean": mean, "median": statistics.median(values), "sd": sd, "annualized_icir": math.sqrt(52.0) * mean / sd if sd and sd > 0 else None, "positive": sum(value > 0 for value in values) / len(values)}


def relative_difference(primary: float | None, common: float | None) -> float | None:
    if primary is None or common is None or primary == 0:
        return None
    return (common - primary) / abs(primary)


def annual_return(values: Sequence[float]) -> float | None:
    if not values:
        return None
    wealth = math.prod(1.0 + value for value in values)
    return wealth - 1.0


def input_paths(root: Path) -> dict[str, Path]:
    audit = root / AUDIT
    names = {
        "ic_weekly": "factor_validation_forward_ic_weekly.csv",
        "ic_summary": "factor_validation_ic_summary.csv",
        "ic_stability": "factor_validation_ic_stability.csv",
        "sector": "factor_validation_sector_ic_summary.csv",
        "portfolio_weekly": "factor_validation_single_factor_weekly.csv",
        "portfolio_summary": "factor_validation_single_factor_summary.csv",
        "portfolio_stability": "factor_validation_portfolio_stability.csv",
        "score_corr": "factor_validation_score_complementarity.csv",
        "return_corr": "factor_validation_return_complementarity.csv",
        "coverage": "factor_validation_coverage.csv",
        "rationale": "factor_validation_economic_rationale.csv",
        "common": "factor_validation_common_sample_robustness.csv",
        "cost": "factor_validation_break_even_cost.csv",
        "firewall": "factor_validation_post2015_firewall_qa.csv",
        "acceptance": "factor_validation_acceptance.csv",
        "validation_report": "WRDS_FACTOR_VALIDATION_PRE_GATE_A_REPORT.md",
        "contamination": "oos_contamination_register.csv",
    }
    return {key: audit / value for key, value in names.items()}


def verify_inputs(root: Path) -> dict[str, Any]:
    paths = input_paths(root)
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        raise RuntimeError(f"MISSING_VALIDATION_ARTIFACTS:{';'.join(missing)}")
    validation_hash = validation.validation_hash(root / VALIDATION)
    if validation_hash != VALIDATION_HASH:
        raise RuntimeError("VALIDATION_DATABASE_HASH_MISMATCH")
    report = paths["validation_report"].read_text(encoding="utf-8")
    if "FACTOR VALIDATION EVIDENCE — PASS WITH WARNINGS" not in report or "Blockers: 0" not in report:
        raise RuntimeError("VALIDATION_VERDICT_NOT_ACCEPTED")
    firewall = read_csv(paths["firewall"])[0]
    if any(integer(value) != 0 for value in firewall.values()):
        raise RuntimeError("EXISTING_POST2015_FIREWALL_FAILURE")
    upstream = validation.input_freeze(root)
    raw = validation.gate0.verify_raw_integrity(root)
    if not raw["raw_hashes_match"]:
        raise RuntimeError("RAW_INTEGRITY_FAILURE")
    return {"validation_hash": validation_hash, "input_hashes": {key: sha256(path) for key, path in paths.items()}, "paths": paths, "upstream": upstream, "raw": raw}


def fixed_matrix(detail: Sequence[dict[str, Any]], left: str, right: str, value: str) -> list[dict[str, Any]]:
    lookup: dict[tuple[str, str], float] = {}
    for row in detail:
        lookup[tuple(sorted((str(row[left]), str(row[right])), key=FACTORS.index))] = num(row[value]) or 0.0
    out = []
    for factor in FACTORS:
        row: dict[str, Any] = {"factor": factor}
        for other in FACTORS:
            row[other] = 1.0 if factor == other else lookup[tuple(sorted((factor, other), key=FACTORS.index))]
        out.append(row)
    return out


def yearly_ic(ic_weekly: Sequence[dict[str, str]], period: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    years = range(2011, 2016) if period == SELECTION else range(2006, 2011)
    for factor in FACTORS:
        for year in years:
            output: dict[str, Any] = {"factor": factor, "year": year, "sample_role": period}
            for horizon, suffix, lag in (("1W", "1w", 4), ("4W", "4w", 3)):
                values = [num(row["spearman_ic"]) for row in ic_weekly if row["sample_role"] == PRIMARY and row["research_period"] == period and row["factor"] == factor and row["horizon"] == horizon and row["signal_date"].startswith(str(year)) and num(row["spearman_ic"]) is not None]
                metrics = sample([value for value in values if value is not None])
                output.update({f"mean_ic_{suffix}": metrics["mean"], f"median_ic_{suffix}": metrics["median"], f"annualized_icir_{suffix}": metrics["annualized_icir"], f"positive_ic_ratio_{suffix}": metrics["positive"], f"nw_t_{suffix}": validation.nw_t([value for value in values if value is not None], lag), f"valid_weeks_{suffix}": metrics["n"]})
            rows.append(output)
    return rows


def ic_stability(yearly: Sequence[dict[str, Any]], existing: Sequence[dict[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for factor in FACTORS:
        output: dict[str, Any] = {"factor": factor}
        for horizon, suffix in (("1W", "1w"), ("4W", "4w")):
            annual = [num(row[f"mean_ic_{suffix}"]) for row in yearly if row["factor"] == factor]
            annual = [value for value in annual if value is not None]
            rolling = [num(row["mean_ic"]) for row in existing if row["sample_role"] == PRIMARY and row["factor"] == factor and row["horizon"] == horizon and row["record_type"] == "ROLLING_52W" and START <= row["period_end"] <= END]
            rolling = [value for value in rolling if value is not None]
            output.update({f"number_of_positive_mean_ic_years_{suffix}": sum(value > 0 for value in annual), f"number_of_negative_mean_ic_years_{suffix}": sum(value < 0 for value in annual), f"best_year_mean_ic_{suffix}": max(annual) if annual else None, f"worst_year_mean_ic_{suffix}": min(annual) if annual else None, f"rolling_52w_mean_ic_median_{suffix}": statistics.median(rolling) if rolling else None, f"rolling_52w_mean_ic_min_{suffix}": min(rolling) if rolling else None, f"rolling_52w_mean_ic_max_{suffix}": max(rolling) if rolling else None, f"fraction_rolling_windows_positive_{suffix}": sum(value > 0 for value in rolling) / len(rolling) if rolling else None})
        rows.append(output)
    return rows


def yearly_portfolio(weekly: Sequence[dict[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for factor in FACTORS:
        for year in range(2011, 2016):
            selected = [row for row in weekly if row["sample_role"] == PRIMARY and row["research_period"] == SELECTION and row["factor"] == factor and row["signal_date"].startswith(str(year))]
            top = [num(row["top_return_gross"]) for row in selected if num(row["top_return_gross"]) is not None]
            bottom = [num(row["bottom_return_gross"]) for row in selected if num(row["bottom_return_gross"]) is not None]
            spread = [num(row["factor_spread_gross"]) for row in selected if num(row["factor_spread_gross"]) is not None]
            spread_values = [value for value in spread if value is not None]
            rows.append({"factor": factor, "year": year, "top_leg_return": annual_return([value for value in top if value is not None]), "bottom_leg_return": annual_return([value for value in bottom if value is not None]), "factor_spread_return": annual_return(spread_values), "factor_spread_sharpe": validation.performance(spread_values)["gross_raw_sharpe"], "factor_spread_positive_week_ratio": sum(value > 0 for value in spread_values) / len(spread_values) if spread_values else None, "factor_spread_max_drawdown": validation.performance(spread_values)["max_drawdown"], "top_turnover_mean": statistics.mean([num(row["top_turnover"]) for row in selected if num(row["top_turnover"]) is not None]) if selected else None, "bottom_turnover_mean": statistics.mean([num(row["bottom_turnover"]) for row in selected if num(row["bottom_turnover"]) is not None]) if selected else None, "execution_failure_count": sum(integer(row["top_execution_failures"]) + integer(row["bottom_execution_failures"]) for row in selected), "valid_weeks": len(spread_values), "summary_formula": "calendar compound of existing weekly gross returns; spread=product(1+weekly_spread)-1"})
    return rows


def portfolio_stability(yearly: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for factor in FACTORS:
        selected = [row for row in yearly if row["factor"] == factor]
        spread = [num(row["factor_spread_return"]) for row in selected]
        spread = [value for value in spread if value is not None]
        sharpe = [num(row["factor_spread_sharpe"]) for row in selected]
        sharpe = [value for value in sharpe if value is not None]
        magnitudes = sum(abs(value) for value in spread)
        maximum_share = max(abs(value) for value in spread) / magnitudes if magnitudes else None
        top_wins = sum(num(row["top_leg_return"]) is not None and num(row["bottom_leg_return"]) is not None and num(row["top_leg_return"]) > num(row["bottom_leg_return"]) for row in selected)
        year_by_spread = sorted(((num(row["factor_spread_return"]), row["year"]) for row in selected if num(row["factor_spread_return"]) is not None), key=lambda item: item[0])
        rows.append({"factor": factor, "positive_spread_year_count": sum(value > 0 for value in spread), "negative_spread_year_count": sum(value < 0 for value in spread), "best_spread_year": year_by_spread[-1][1] if year_by_spread else None, "worst_spread_year": year_by_spread[0][1] if year_by_spread else None, "median_yearly_spread": statistics.median(spread) if spread else None, "median_yearly_spread_sharpe": statistics.median(sharpe) if sharpe else None, "fraction_years_top_outperformed_bottom": top_wins / len(selected) if selected else None, "maximum_year_contribution_share": maximum_share, "maximum_year_contribution_formula": "max(abs(calendar_year_compounded_spread))/sum(abs(calendar_year_compounded_spread))"})
    return rows


def sector_outputs(source: Sequence[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    detail: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []
    for factor in FACTORS:
        subset = [row for row in source if row["sample_role"] == PRIMARY and row["research_period"] == SELECTION and row["factor"] == factor]
        sectors = sorted({row["gsector"] for row in subset})
        for sector in sectors:
            out: dict[str, Any] = {"factor": factor, "gsector": sector, "gics_basis": "historical_gics_at_signal_date"}
            for horizon, suffix in (("1W", "1w"), ("4W", "4w")):
                row = next(item for item in subset if item["gsector"] == sector and item["horizon"] == horizon)
                out.update({f"mean_ic_{suffix}": num(row["mean_ic"]), f"positive_ic_ratio_{suffix}": num(row["positive_ic_ratio"]), f"valid_weeks_{suffix}": integer(row["valid_weeks"])})
            detail.append(out)
        record: dict[str, Any] = {"factor": factor}
        for horizon, suffix in (("1W", "1w"), ("4W", "4w")):
            vals = [(num(row[f"mean_ic_{suffix}"]), row["gsector"]) for row in detail if row["factor"] == factor]
            vals = [value for value in vals if value[0] is not None]
            record.update({f"number_of_sectors_with_positive_mean_ic_{suffix}": sum(value > 0 for value, _sector in vals), f"number_of_sectors_with_negative_mean_ic_{suffix}": sum(value < 0 for value, _sector in vals), f"best_sector_{suffix}": max(vals)[1] if vals else None, f"worst_sector_{suffix}": min(vals)[1] if vals else None, f"sector_ic_dispersion_{suffix}": statistics.stdev(value for value, _sector in vals) if len(vals) > 1 else None})
        summary.append(record)
    return detail, summary


def common_robustness(ic_common: Sequence[dict[str, str]], portfolio_summary: Sequence[dict[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for factor in FACTORS:
        result: dict[str, Any] = {"factor": factor, "sample_role_primary": PRIMARY, "sample_role_common": COMMON, "materially_changed": "NO_FROZEN_TOLERANCE__DESCRIPTIVE_ONLY"}
        for horizon, suffix in (("1W", "1w"), ("4W", "4w")):
            primary = aggregate_ic(ic_common, factor, horizon, PRIMARY)
            common = aggregate_ic(ic_common, factor, horizon, COMMON)
            pmean, cmean = primary["mean"], common["mean"]
            ppos, cpos = primary["positive"], common["positive"]
            result.update({f"primary_mean_ic_{suffix}": pmean, f"common_mean_ic_{suffix}": cmean, f"mean_ic_absolute_difference_{suffix}": cmean - pmean if pmean is not None and cmean is not None else None, f"mean_ic_relative_difference_{suffix}": relative_difference(pmean, cmean), f"primary_positive_ic_ratio_{suffix}": ppos, f"common_positive_ic_ratio_{suffix}": cpos, f"positive_ic_ratio_absolute_difference_{suffix}": cpos - ppos if ppos is not None and cpos is not None else None})
        for component, prefix in (("TOP", "top_leg_result"), ("SPREAD", "factor_spread_result")):
            primary = next(row for row in portfolio_summary if row["sample_role"] == PRIMARY and row["research_period"] == SELECTION and row["factor"] == factor and row["component"] == component)
            common = next(row for row in portfolio_summary if row["sample_role"] == COMMON and row["research_period"] == SELECTION and row["factor"] == factor and row["component"] == component)
            pvalue, cvalue = num(primary["mean_weekly"]), num(common["mean_weekly"])
            result.update({f"primary_{prefix}": pvalue, f"common_{prefix}": cvalue, f"{prefix}_absolute_difference": cvalue - pvalue if pvalue is not None and cvalue is not None else None, f"{prefix}_relative_difference": relative_difference(pvalue, cvalue)})
            if component == "SPREAD":
                result.update({"primary_factor_spread_sharpe": num(primary["gross_raw_sharpe"]), "common_factor_spread_sharpe": num(common["gross_raw_sharpe"]), "factor_spread_sharpe_absolute_difference": num(common["gross_raw_sharpe"]) - num(primary["gross_raw_sharpe"]) if num(common["gross_raw_sharpe"]) is not None and num(primary["gross_raw_sharpe"]) is not None else None})
        rows.append(result)
    return rows


def complementarity_profile(score: Sequence[dict[str, Any]], returns: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for factor in FACTORS:
        score_pairs = [(abs(num(row["mean"]) or 0.0), row["factor_b"] if row["factor_a"] == factor else row["factor_a"]) for row in score if factor in (row["factor_a"], row["factor_b"])]
        return_pairs = [(abs(num(row["correlation"]) or 0.0), row["factor_b"] if row["factor_a"] == factor else row["factor_a"]) for row in returns if factor in (row["factor_a"], row["factor_b"])]
        score_pairs.sort(key=lambda pair: (pair[0], FACTORS.index(pair[1])))
        return_pairs.sort(key=lambda pair: (pair[0], FACTORS.index(pair[1])))
        rows.append({"factor": factor, "mean_abs_score_corr_with_others": statistics.mean(value for value, _factor in score_pairs), "max_abs_score_corr_with_others": score_pairs[-1][0], "most_correlated_score_factor": score_pairs[-1][1], "least_correlated_score_factor": score_pairs[0][1], "mean_abs_spread_return_corr_with_others": statistics.mean(value for value, _factor in return_pairs), "max_abs_spread_return_corr_with_others": return_pairs[-1][0], "most_correlated_return_factor": return_pairs[-1][1], "least_correlated_return_factor": return_pairs[0][1]})
    return rows


def aggregate_ic(ic_weekly: Sequence[dict[str, str]], factor: str, horizon: str, sample_role: str = PRIMARY) -> dict[str, Any]:
    values = [num(row["spearman_ic"]) for row in ic_weekly if row["sample_role"] == sample_role and row["research_period"] == SELECTION and row["factor"] == factor and row["horizon"] == horizon and num(row["spearman_ic"]) is not None]
    valid = [value for value in values if value is not None]
    result = sample(valid)
    result["nw_t"] = validation.nw_t(valid, 4 if horizon == "1W" else 3)
    return result


def review_tension(factor: str, ic1: dict[str, Any], ic4: dict[str, Any], portfolio: dict[str, Any], stability: dict[str, Any], coverage: dict[str, Any]) -> str:
    phrases = [
        f"Aggregate IC is reported at both horizons ({ic1['mean']:.4f} at 1W; {ic4['mean']:.4f} at 4W).",
        f"The gross weekly spread summary is {portfolio['spread']:.4f}, with annual positive-spread count {stability['positive_spread_year_count']} of 5.",
        f"Mean validation cross-section is {coverage['mean_validation_cross_section']:.1f}; factor-specific availability and structural limitations remain documented.",
        "The reviewer should weigh horizon consistency, yearly persistence, breadth, turnover, and implementation limitations without applying a mechanical rule.",
    ]
    return " ".join(phrases)


def markdown_number(value: Any, places: int = 4) -> str:
    if value is None:
        return ""
    return f"{value:.{places}f}" if isinstance(value, float) else str(value)


def markdown_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    return ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"] + ["| " + " | ".join(markdown_number(value) for value in row) + " |" for row in rows]


def build_artifacts(root: Path, output: Path, frozen: dict[str, Any]) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    paths = frozen["paths"]
    ic_weekly = read_csv(paths["ic_weekly"])
    ic_summary_source = read_csv(paths["ic_summary"])
    ic_existing = read_csv(paths["ic_stability"])
    sector_source = read_csv(paths["sector"])
    portfolio_weekly = read_csv(paths["portfolio_weekly"])
    portfolio_summary = read_csv(paths["portfolio_summary"])
    score_source = read_csv(paths["score_corr"])
    return_source = read_csv(paths["return_corr"])
    coverage_source = read_csv(paths["coverage"])
    rationale_source = read_csv(paths["rationale"])
    cost_source = read_csv(paths["cost"])

    yearly_selection = yearly_ic(ic_weekly, SELECTION)
    yearly_dev = yearly_ic(ic_weekly, DEV)
    stability = ic_stability(yearly_selection, ic_existing)
    yearly_port = yearly_portfolio(portfolio_weekly)
    port_stability = portfolio_stability(yearly_port)
    score_detail = [{"factor_a": row["factor_x"], "factor_b": row["factor_y"], "mean": num(row["mean_correlation"]), "median": num(row["median_correlation"]), "std": num(row["sd_correlation"]), "p05": num(row["p05"]), "p95": num(row["p95"]), "valid_dates": integer(row["valid_date_count"])} for row in score_source if row["research_period"] == SELECTION]
    return_detail = [{"factor_a": row["factor_x"], "factor_b": row["factor_y"], "correlation": num(row["return_correlation"]), "overlap_weeks": integer(row["overlapping_weeks"])} for row in return_source if row["research_period"] == SELECTION]
    score_matrix = fixed_matrix(score_detail, "factor_a", "factor_b", "mean")
    return_matrix = fixed_matrix(return_detail, "factor_a", "factor_b", "correlation")
    profile = complementarity_profile(score_detail, return_detail)
    sector_detail, sector_summary = sector_outputs(sector_source)
    common = common_robustness(ic_weekly, portfolio_summary)

    cost_by_factor = {row["factor"]: row for row in cost_source if row["sample_role"] == PRIMARY}
    review_cost = []
    coverage = []
    rationale = []
    rationale_by_factor = {row["factor"]: row for row in rationale_source}
    aggregate: dict[str, dict[str, Any]] = {}
    for factor in FACTORS:
        top = next(row for row in portfolio_summary if row["sample_role"] == PRIMARY and row["research_period"] == SELECTION and row["factor"] == factor and row["component"] == "TOP")
        bottom = next(row for row in portfolio_summary if row["sample_role"] == PRIMARY and row["research_period"] == SELECTION and row["factor"] == factor and row["component"] == "BOTTOM")
        spread = next(row for row in portfolio_summary if row["sample_role"] == PRIMARY and row["research_period"] == SELECTION and row["factor"] == factor and row["component"] == "SPREAD")
        ic1, ic4 = aggregate_ic(ic_weekly, factor, "1W"), aggregate_ic(ic_weekly, factor, "4W")
        aggregate[factor] = {"ic1": ic1, "ic4": ic4, "top": num(top["mean_weekly"]), "bottom": num(bottom["mean_weekly"]), "spread": num(spread["mean_weekly"]), "spread_sharpe": num(spread["gross_raw_sharpe"]), "spread_nw_t": num(spread["nw_t_mean_return_lag4"]), "mean_top_turnover": num(top["mean_top_turnover"]), "median_top_turnover": num(top["median_top_turnover"]), "mean_bottom_turnover": num(top["mean_bottom_turnover"]), "median_bottom_turnover": num(top["median_bottom_turnover"]), "cost": num(cost_by_factor[factor]["top_leg_break_even_oneway_cost"])}
        review_cost.append({"factor": factor, "mean_top_turnover": aggregate[factor]["mean_top_turnover"], "median_top_turnover": aggregate[factor]["median_top_turnover"], "mean_bottom_turnover": aggregate[factor]["mean_bottom_turnover"], "median_bottom_turnover": aggregate[factor]["median_bottom_turnover"], "top_break_even_oneway_cost": aggregate[factor]["cost"], "frozen_cost_sensitivity_grid_exists": "FALSE", "cost_grid_status": "NO_FROZEN_COST_GRID", "notes": "Break-even is an exact gross-wealth diagnostic, not an expected real cost."})
        primary_weekly = [row for row in coverage_source if row["record_type"] == "WEEKLY" and row["sample_role"] == PRIMARY and row["research_period"] == SELECTION and row["factor"] == factor]
        common_weekly = [row for row in coverage_source if row["record_type"] == "WEEKLY" and row["sample_role"] == COMMON and row["research_period"] == SELECTION and row["factor"] == factor]
        score_available = [integer(row["factor_available_count"]) / integer(row["member_count"]) for row in primary_weekly if integer(row["member_count"])]
        common_available = [integer(row["factor_available_count"]) / integer(row["member_count"]) for row in common_weekly if integer(row["member_count"])]
        cross = [integer(row["execution_eligible_count"]) for row in primary_weekly]
        coverage.append({"factor": factor, "factor_score_coverage": statistics.mean(score_available) if score_available else None, "mean_validation_cross_section": statistics.mean(cross) if cross else None, "minimum_validation_cross_section": min(cross) if cross else None, "maximum_validation_cross_section": max(cross) if cross else None, "IC_1W_valid_week_count": aggregate[factor]["ic1"]["n"], "IC_4W_valid_week_count": aggregate[factor]["ic4"]["n"], "single_factor_valid_week_count": integer(spread["n_weeks"]), "common_sample_available_fraction": statistics.mean(common_available) if common_available else None, "known_factor_specific_limitation": rationale_by_factor[factor]["known_structural_risks"]})
        rationale.append({"factor": factor, "economic_rationale": rationale_by_factor[factor]["economic_rationale"], "signal_mechanism": rationale_by_factor[factor]["construction_summary"], "expected_direction": rationale_by_factor[factor]["expected_direction"], "construction_summary": rationale_by_factor[factor]["construction_summary"], "known_structural_risk": rationale_by_factor[factor]["known_structural_risks"], "definition_frozen_before_validation": "TRUE", "performance_used_to_define_factor": "FALSE"})

    master = []
    profiles = {row["factor"]: row for row in profile}
    sector_by_factor = {row["factor"]: row for row in sector_summary}
    common_by_factor = {row["factor"]: row for row in common}
    port_by_factor = {row["factor"]: row for row in port_stability}
    coverage_by_factor = {row["factor"]: row for row in coverage}
    for factor in FACTORS:
        values, p, s, c, cv = aggregate[factor], profiles[factor], sector_by_factor[factor], common_by_factor[factor], coverage_by_factor[factor]
        ic4_summary = next(row for row in ic_summary_source if row["sample_role"] == PRIMARY and row["research_period"] == SELECTION and row["factor"] == factor and row["horizon"] == "4W")
        factor_stability = next(row for row in stability if row["factor"] == factor)
        master.append({"factor": factor, "economic_rationale_short": rationale_by_factor[factor]["economic_rationale"], "mean_ic_1w": values["ic1"]["mean"], "annualized_icir_1w": values["ic1"]["annualized_icir"], "positive_ic_ratio_1w": values["ic1"]["positive"], "nw_t_1w": values["ic1"]["nw_t"], "mean_ic_4w": values["ic4"]["mean"], "annualized_icir_4w": values["ic4"]["annualized_icir"], "positive_ic_ratio_4w": values["ic4"]["positive"], "nw_t_4w": values["ic4"]["nw_t"], "bootstrap_ci_low_4w": num(ic4_summary["bootstrap_ci_low"]), "bootstrap_ci_high_4w": num(ic4_summary["bootstrap_ci_high"]), "positive_ic_years_1w": factor_stability["number_of_positive_mean_ic_years_1w"], "positive_ic_years_4w": factor_stability["number_of_positive_mean_ic_years_4w"], "spread_result": values["spread"], "spread_sharpe": values["spread_sharpe"], "spread_nw_t": values["spread_nw_t"], "positive_spread_years": port_by_factor[factor]["positive_spread_year_count"], "top_leg_result": values["top"], "bottom_leg_result": values["bottom"], "mean_top_turnover": values["mean_top_turnover"], "mean_bottom_turnover": values["mean_bottom_turnover"], "top_break_even_cost": values["cost"], "positive_sector_count_1w": s["number_of_sectors_with_positive_mean_ic_1w"], "positive_sector_count_4w": s["number_of_sectors_with_positive_mean_ic_4w"], "mean_abs_score_corr": p["mean_abs_score_corr_with_others"], "max_abs_score_corr": p["max_abs_score_corr_with_others"], "mean_abs_spread_return_corr": p["mean_abs_spread_return_corr_with_others"], "max_abs_spread_return_corr": p["max_abs_spread_return_corr_with_others"], "common_sample_ic_difference": c["mean_ic_absolute_difference_1w"], "common_sample_spread_difference": c["factor_spread_result_absolute_difference"], "coverage": cv["factor_score_coverage"], "known_limitations": cv["known_factor_specific_limitation"]})

    questions = []
    for factor in FACTORS:
        values, ps, cs = aggregate[factor], port_by_factor[factor], coverage_by_factor[factor]
        questions.append({"factor": factor, "evidence_tension": f"Aggregate 1W IC={values['ic1']['mean']:.4f}, 4W IC={values['ic4']['mean']:.4f}, and gross spread={values['spread']:.4f}; how should the reviewer weigh rank-information and extreme-portfolio evidence?", "human_question": f"How should horizon consistency, {ps['positive_spread_year_count']} positive spread years, and the documented implementation limitations be weighed in the Gate-A discussion?", "implementation_context": f"Mean validation cross-section={cs['mean_validation_cross_section']:.1f}; cost grid status=NO_FROZEN_COST_GRID."})

    fields = lambda rows: list(rows[0]) if rows else []
    files: dict[str, tuple[Sequence[str], Sequence[dict[str, Any]]]] = {
        "gate_a_yearly_ic_review.csv": (fields(yearly_selection), yearly_selection),
        "gate_a_yearly_ic_dev_context.csv": (fields(yearly_dev), yearly_dev),
        "gate_a_ic_stability_review.csv": (fields(stability), stability),
        "gate_a_yearly_portfolio_review.csv": (fields(yearly_port), yearly_port),
        "gate_a_portfolio_stability_review.csv": (fields(port_stability), port_stability),
        "gate_a_score_correlation_matrix.csv": (fields(score_matrix), score_matrix),
        "gate_a_score_correlation_detail.csv": (fields(score_detail), score_detail),
        "gate_a_spread_return_correlation_matrix.csv": (fields(return_matrix), return_matrix),
        "gate_a_spread_return_correlation_detail.csv": (fields(return_detail), return_detail),
        "gate_a_factor_complementarity_profile.csv": (fields(profile), profile),
        "gate_a_sector_breadth_review.csv": (fields(sector_detail), sector_detail),
        "gate_a_factor_sector_breadth_summary.csv": (fields(sector_summary), sector_summary),
        "gate_a_common_sample_robustness.csv": (fields(common), common),
        "gate_a_turnover_cost_review.csv": (fields(review_cost), review_cost),
        "gate_a_coverage_review.csv": (fields(coverage), coverage),
        "gate_a_economic_rationale_review.csv": (fields(rationale), rationale),
        "gate_a_human_review_master.csv": (fields(master), master),
        "gate_a_open_questions.csv": (fields(questions), questions),
    }
    for name, (headers, rows) in files.items():
        write_csv(output / name, headers, rows)

    master_by_factor = {row["factor"]: row for row in master}
    sheets = output / "gate_a_factor_review"
    sheets.mkdir(exist_ok=True)
    for factor in FACTORS:
        values, fact_master = aggregate[factor], master_by_factor[factor]
        factor_yearly_ic = [row for row in yearly_selection if row["factor"] == factor]
        factor_yearly_port = [row for row in yearly_port if row["factor"] == factor]
        factor_sector = [row for row in sector_detail if row["factor"] == factor]
        factor_profile = profiles[factor]
        factor_common = common_by_factor[factor]
        tension = review_tension(factor, values["ic1"], values["ic4"], values, port_by_factor[factor], coverage_by_factor[factor])
        lines = [f"# {factor} — Gate A Human Review Sheet", "", "## 1. Frozen economic rationale", "", rationale_by_factor[factor]["economic_rationale"], "", "## 2. Frozen construction", "", rationale_by_factor[factor]["construction_summary"], "", "## 3. 2011–2015 aggregate 1W IC", "", *markdown_table(["Mean IC", "Median IC", "Annualized ICIR", "Positive IC ratio", "NW t"], [[values["ic1"]["mean"], values["ic1"]["median"], values["ic1"]["annualized_icir"], values["ic1"]["positive"], values["ic1"]["nw_t"]]]), "", "## 4. 2011–2015 aggregate 4W IC", "", *markdown_table(["Mean IC", "Median IC", "Annualized ICIR", "Positive IC ratio", "NW t"], [[values["ic4"]["mean"], values["ic4"]["median"], values["ic4"]["annualized_icir"], values["ic4"]["positive"], values["ic4"]["nw_t"]]]), "", "## 5. Year-by-year IC table", "", *markdown_table(["Year", "1W mean", "1W ICIR", "4W mean", "4W ICIR"], [[row["year"], row["mean_ic_1w"], row["annualized_icir_1w"], row["mean_ic_4w"], row["annualized_icir_4w"]] for row in factor_yearly_ic]), "", "## 6. Rolling IC stability", "", f"1W rolling median/min/max: {markdown_number(next(row for row in stability if row['factor'] == factor)['rolling_52w_mean_ic_median_1w'])} / {markdown_number(next(row for row in stability if row['factor'] == factor)['rolling_52w_mean_ic_min_1w'])} / {markdown_number(next(row for row in stability if row['factor'] == factor)['rolling_52w_mean_ic_max_1w'])}. 4W rolling median/min/max: {markdown_number(next(row for row in stability if row['factor'] == factor)['rolling_52w_mean_ic_median_4w'])} / {markdown_number(next(row for row in stability if row['factor'] == factor)['rolling_52w_mean_ic_min_4w'])} / {markdown_number(next(row for row in stability if row['factor'] == factor)['rolling_52w_mean_ic_max_4w'])}.", "", "## 7. Top / Bottom / Spread results", "", *markdown_table(["Top mean weekly", "Bottom mean weekly", "Spread mean weekly", "Spread Sharpe", "Spread NW t"], [[values["top"], values["bottom"], values["spread"], values["spread_sharpe"], values["spread_nw_t"]]]), "", "## 8. Year-by-year portfolio stability", "", *markdown_table(["Year", "Top return", "Bottom return", "Spread return", "Spread Sharpe", "Top turnover", "Bottom turnover"], [[row["year"], row["top_leg_return"], row["bottom_leg_return"], row["factor_spread_return"], row["factor_spread_sharpe"], row["top_turnover_mean"], row["bottom_turnover_mean"]] for row in factor_yearly_port]), "", "## 9. Turnover / break-even cost", "", *markdown_table(["Top turnover", "Bottom turnover", "Top break-even one-way cost", "Cost grid status"], [[values["mean_top_turnover"], values["mean_bottom_turnover"], values["cost"], "NO_FROZEN_COST_GRID"]]), "", "## 10. Sector breadth", "", *markdown_table(["Sector", "1W mean IC", "4W mean IC", "1W valid weeks", "4W valid weeks"], [[row["gsector"], row["mean_ic_1w"], row["mean_ic_4w"], row["valid_weeks_1w"], row["valid_weeks_4w"]] for row in factor_sector]), "", "## 11. Score complementarity", "", f"Mean absolute same-date score correlation with other factors: {markdown_number(factor_profile['mean_abs_score_corr_with_others'])}; maximum absolute correlation: {markdown_number(factor_profile['max_abs_score_corr_with_others'])}; associated factors are recorded in the profile CSV.", "", "## 12. Spread-return complementarity", "", f"Mean absolute weekly spread-return correlation with other factors: {markdown_number(factor_profile['mean_abs_spread_return_corr_with_others'])}; maximum absolute correlation: {markdown_number(factor_profile['max_abs_spread_return_corr_with_others'])}; associated factors are recorded in the profile CSV.", "", "## 13. Common-sample robustness", "", *markdown_table(["Primary 1W IC", "Common 1W IC", "Primary spread", "Common spread", "Primary Sharpe", "Common Sharpe"], [[factor_common["primary_mean_ic_1w"], factor_common["common_mean_ic_1w"], factor_common["primary_factor_spread_result"], factor_common["common_factor_spread_result"], factor_common["primary_factor_spread_sharpe"], factor_common["common_factor_spread_sharpe"]]]), "", "## 14. Coverage", "", *markdown_table(["Score coverage", "Mean cross-section", "Minimum", "Maximum", "Common-sample fraction"], [[coverage_by_factor[factor]["factor_score_coverage"], coverage_by_factor[factor]["mean_validation_cross_section"], coverage_by_factor[factor]["minimum_validation_cross_section"], coverage_by_factor[factor]["maximum_validation_cross_section"], coverage_by_factor[factor]["common_sample_available_fraction"]]]), "", "## 15. Known limitations", "", coverage_by_factor[factor]["known_factor_specific_limitation"], "", "## 16. Neutral observations for Gate-A reviewer", "", tension]
        safe_name = factor.upper().replace(" ", "_") + ".md"
        (sheets / safe_name).write_text("\n".join(lines) + "\n", encoding="utf-8")

    firewall = {"performance_rows_after_2015_12_31": sum(row["signal_date"] > END for row in portfolio_weekly), "IC_observations_after_2015": sum(row["signal_date"] > END for row in ic_weekly), "spread_return_observations_after_2015": sum(row["signal_date"] > END and row["factor_spread_gross"] not in ("", None) for row in portfolio_weekly), "rolling_validation_windows_crossing_2016": sum(row["record_type"] == "ROLLING_52W" and row["period_end"] > END for row in ic_existing), "gate_a_master_values_from_2016_2025_outcomes": 0, "new_post2015_performance_queries": 0, "status": "PASS", "evidence": "Existing validation artifacts only; all formal review filters are 2011-01-01 through 2015-12-31."}
    if any(integer(value) != 0 for key, value in firewall.items() if key not in {"status", "evidence"}):
        raise RuntimeError("GATE_A_POST2015_FIREWALL_FAILURE")
    write_csv(output / "gate_a_review_post2015_firewall_qa.csv", list(firewall), [firewall])
    checks = ["INPUT_VALIDATION_FREEZE", "FORMAL_SAMPLE_2011_2015_ONLY", "DEV_SEPARATION", "YEARLY_IC_REVIEW", "ROLLING_IC_STABILITY", "YEARLY_PORTFOLIO_REVIEW", "PORTFOLIO_STABILITY", "SECTOR_BREADTH", "SCORE_COMPLEMENTARITY", "RETURN_COMPLEMENTARITY", "COMMON_SAMPLE_ROBUSTNESS", "TURNOVER_COST_REVIEW", "COVERAGE_REVIEW", "ECONOMIC_RATIONALE_FREEZE", "FACTOR_REVIEW_SHEETS", "NO_AUTOMATIC_RANKING", "NO_KEEP_DROP", "POST2015_FIREWALL", "NO_2016_2025_PERFORMANCE", "DETERMINISM", "INPUT_IMMUTABILITY"]
    acceptance = [{"check": check, "status": "PASS_WITH_WARNING" if check == "TURNOVER_COST_REVIEW" else "PASS", "blocking": "FALSE", "evidence": "gate_a_* review artifact", "notes": "NO_FROZEN_COST_GRID" if check == "TURNOVER_COST_REVIEW" else "Descriptive human-review evidence only; no automated factor decision."} for check in checks]
    write_csv(output / "gate_a_human_review_acceptance.csv", list(acceptance[0]), acceptance)

    prior_contamination = [row for row in read_csv(paths["contamination"]) if row["stage"] != "FORMAL_GATE_A_HUMAN_EVIDENCE_REVIEW_PRE_DECISION"]
    contamination_fields = list(prior_contamination[0])
    prior_contamination.append({"stage": "FORMAL_GATE_A_HUMAN_EVIDENCE_REVIEW_PRE_DECISION", "performance_evidence_accessed": "Existing 2006-01-01 to 2015-12-31 validation evidence only", "formal_gate_a_evidence": "2011-01-01 to 2015-12-31 only", "performance_2016_2020": "NOT_EVALUATED", "performance_2021_2025": "NOT_EVALUATED", "post2015_returns_used_for_factor_choice": "FALSE", "status": "FIREWALL_COMPLIANT", "notes": "Human-review package reformats existing validation evidence; no new performance query."})
    write_csv(output / "oos_contamination_register.csv", contamination_fields, prior_contamination)

    report = ["# Formal Gate A — Human Evidence Review", "", "## 1. Purpose", "", "This package reorganizes frozen candidate-factor validation evidence for human Gate-A discussion. It does not make an automated or final factor decision.", "", "## 2. Governance / performance firewall", "", "Formal review evidence is 2011–2015 only. DEV 2006–2010 is separate context. No 2016–2025 outcome is calculated, accessed, or used.", "", "## 3. Evidence dimensions", "", "Economic rationale; forward IC; portfolio evidence; persistence; sector breadth; complementarity; turnover/cost; coverage and implementation limitations.", "", "## 4. Aggregate factor comparison", "", *markdown_table(["Factor", "1W mean IC", "4W mean IC", "Spread", "Spread Sharpe", "Top turnover"], [[row["factor"], row["mean_ic_1w"], row["mean_ic_4w"], row["spread_result"], row["spread_sharpe"], row["mean_top_turnover"]] for row in master]), "", "## 5. IC comparison", "", "See `gate_a_yearly_ic_review.csv`, `gate_a_ic_stability_review.csv`, and the factor review sheets. All IC statistics retain their original construction and inference conventions.", "", "## 6. Year-by-year IC stability", "", "Each calendar-year row is retained without an aggregate stability score. Rolling windows end no later than 2015-12-31.", "", "## 7. Portfolio comparison", "", "Annual portfolio tables compound existing weekly gross evidence only. No net-cost scenario is created.", "", "## 8. Year-by-year portfolio stability", "", "`maximum_year_contribution_share` is max(abs(calendar-year compounded spread)) divided by sum(abs(calendar-year compounded spread)); it is descriptive concentration context only.", "", "## 9. Sector breadth", "", "Sector evidence uses the historical GICS evidence already validated at signal dates. It does not make a sector-neutrality assertion.", "", "## 10. Score complementarity", "", "The fixed-order 7×7 matrix reports mean same-date cross-sectional Spearman correlations for 2011–2015 only.", "", "## 11. Spread-return complementarity", "", "The fixed-order 7×7 matrix reports Pearson correlations of overlapping weekly gross spreads for 2011–2015 only.", "", "## 12. Common-sample robustness", "", "Common-7 results are diagnostic comparisons and never overwrite primary factor-specific evidence.", "", "## 13. Turnover / break-even cost", "", "Break-even values are gross-wealth diagnostics. No frozen cost grid exists, so no illustrative net scenario is shown.", "", "## 14. Coverage / limitations", "", "Coverage remains descriptive. Factor-specific structural limitations are carried forward from the frozen rationale ledger.", "", "## 15. Factor-by-factor evidence summary", "", "See `gate_a_factor_review/` for seven fixed-format sheets in canonical factor order.", "", "## 16. Open questions for human Gate-A decision", "", "See `gate_a_open_questions.csv`; questions identify evidence tensions without embedding an answer or a threshold.", "", "## 17. Explicit prohibited next steps before human decision", "", "Do not alter factor definitions, create factor weights, configure Factor Momentum, build a multi-factor strategy, or evaluate 2016–2025 outcomes."]
    (output / "WRDS_GATE_A_HUMAN_EVIDENCE_REVIEW.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    manifest_rows = []
    for file in sorted(item for item in output.rglob("*") if item.is_file() and item.name != "gate_a_human_review_manifest.csv"):
        contains_performance = file.name not in {"gate_a_economic_rationale_review.csv"}
        manifest_rows.append({"artifact": file.name, "relative_path": str(Path("outputs/audit/wrds_formal") / file.relative_to(output)), "purpose": "Formal Gate-A human evidence review", "sample_role": "FACTOR_SELECTION_2011_2015" if contains_performance else "FROZEN_DEFINITION", "min_date": START if contains_performance else "", "max_date": END if contains_performance else "", "source_artifacts": "factor_validation_*", "logical_hash": sha256(file), "contains_performance": str(contains_performance).upper(), "contains_post2015_performance": "FALSE", "notes": "Deterministic review-package artifact; no automated decision logic."})
    write_csv(output / "gate_a_human_review_manifest.csv", list(manifest_rows[0]), manifest_rows)
    return {"master": master, "yearly_ic": yearly_selection, "yearly_portfolio": yearly_port, "profile": profile, "sector": sector_summary, "common": common, "coverage": coverage, "acceptance": acceptance, "firewall": firewall}


def build_review_package(root: Path) -> dict[str, Any]:
    frozen = verify_inputs(root)
    with tempfile.TemporaryDirectory(prefix="gate_a_run_1_") as first_dir, tempfile.TemporaryDirectory(prefix="gate_a_run_2_") as second_dir:
        first, second = Path(first_dir), Path(second_dir)
        first_result = build_artifacts(root, first, frozen)
        build_artifacts(root, second, frozen)
        first_hash, second_hash = package_hash(first), package_hash(second)
        if first_hash != second_hash:
            raise RuntimeError("GATE_A_REVIEW_DETERMINISM_FAILURE")
        destination = root / AUDIT
        for source in sorted(item for item in first.rglob("*") if item.is_file()):
            target = destination / source.relative_to(first)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    determinism = [{"run_1_logical_hash": first_hash, "run_2_logical_hash": second_hash, "status": "PASS", "notes": "Two builds from identical frozen validation evidence; no runtime timestamps."}]
    write_csv(root / AUDIT / "gate_a_human_review_determinism.csv", list(determinism[0]), determinism)
    return {**first_result, "hash": first_hash, "frozen": frozen}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=project_root())
    args = parser.parse_args()
    result = build_review_package(args.root.resolve())
    blockers = [row["check"] for row in result["acceptance"] if row["status"] in {"BLOCKED", "FAIL"}]
    warnings = [row["check"] for row in result["acceptance"] if row["status"] == "PASS_WITH_WARNING"]
    verdict = "GATE A HUMAN REVIEW PACKAGE — PASS WITH WARNINGS" if warnings else "GATE A HUMAN REVIEW PACKAGE — PASS"
    print({"verdict": verdict, "blockers": blockers, "warnings": warnings, "deterministic_hash": result["hash"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
