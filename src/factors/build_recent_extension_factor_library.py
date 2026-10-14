"""Build the governed 2026 Recent Extension 20-factor research library.

This module is deliberately separate from the frozen Formal 2006--2025
factor store.  It consumes only completed `RECENT_EXTENSION_MIXED_SOURCE`
interfaces and materializes factor inputs/scores, never outcomes, ICs,
portfolios, holdings, or strategy performance.
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
from bisect import bisect_left
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

from src.data import wrds_recent_extension_quality_investment_support as support
from src.factors import build_candidate_factors as formal


MARKET = Path("data/canonical/wrds_recent_extension/us_recent_extension_market_2026.sqlite")
SUPPORT = Path("data/canonical/wrds_recent_extension/us_recent_extension_quality_investment_support_2026.sqlite")
FORMAL = Path("data/canonical/wrds_formal/us_wrds_formal_canonical.sqlite")
REGISTRY = Path("outputs/audit/wrds_2026_bridge/recent_extension_20factor_registry.csv")
READINESS = Path("outputs/audit/wrds_2026_bridge/recent_extension_20factor_readiness_decisions.csv")
IDENTITY = Path("outputs/audit/wrds_2026_bridge/recent_extension_cross_source_identity_coverage.csv")
OUTPUT = Path("data/canonical/wrds_recent_extension/us_recent_extension_factor_library_2026.sqlite")
AUDIT = Path("outputs/audit/wrds_2026_bridge")
LINEAGE = "RECENT_EXTENSION_MIXED_SOURCE"
VERSION = "2026_RECENT_EXTENSION_FROZEN_20_FACTOR_V1"
EXPECTED_START, EXPECTED_END, EXPECTED_DATES = "2026-01-02", "2026-10-02", 40
UNAVAILABLE = {
    "LOWRISK_IVOL": "CRSP_1000080_2026_SOURCE_UNAVAILABLE",
    "DIVIDEND_CURRENT": "CRSP_DISTRIBUTION_TAXONOMY_NOT_REPRODUCIBLE",
}
FINAL_K4 = ("QUALITY_CURRENT", "MOMENTUM_12_1", "LOWRISK_CURRENT", "INVESTMENT_CURRENT")
FORBIDDEN_OUTPUT_COLUMNS = {
    "forward_return", "future_return", "portfolio_return", "long_short_return", "ic", "fmom",
    "holding", "holdings", "nav", "sharpe", "strategy_return",
}
RAW_METRICS = (
    "size", "mom12_1", "mom6_1", "lowrisk_vol", "ep", "bp", "fcf_yield",
    "roe", "roa", "gpa", "negative_leverage", "investment",
)


def root() -> Path:
    return Path(__file__).resolve().parents[2]


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_csv(path: Path, fields: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def registry(project: Path) -> list[dict[str, str]]:
    rows = read_csv(project / REGISTRY)
    names = tuple(row["canonical_factor_name"] for row in rows)
    if len(rows) != 20 or len(set(names)) != 20:
        raise RuntimeError("INVALID_20_FACTOR_REGISTRY")
    if tuple(row["canonical_factor_name"] for row in rows if row["final_k4"] == "TRUE") != FINAL_K4:
        raise RuntimeError("FINAL_K4_REGISTRY_MISMATCH")
    if set(names) & set(UNAVAILABLE) != set(UNAVAILABLE):
        raise RuntimeError("UNAVAILABLE_FACTOR_REGISTRY_MISMATCH")
    return rows


def readiness_decisions(project: Path) -> dict[str, dict[str, str]]:
    rows = read_csv(project / READINESS)
    output = {row["specification_id"]: row for row in rows}
    if len(output) != 20 or set(output) != {row["canonical_factor_name"] for row in registry(project)}:
        raise RuntimeError("READINESS_DECISION_REGISTRY_MISMATCH")
    return output


def identity_rows(project: Path) -> dict[tuple[str, str], dict[str, str]]:
    rows = read_csv(project / IDENTITY)
    output = {(row["signal_date"], row["canonical_security_id"]): row for row in rows}
    if len(output) != len(rows):
        raise RuntimeError("DUPLICATE_CROSS_SOURCE_IDENTITY_RECORD")
    return output


def history_permno(identity: dict[str, str]) -> str | None:
    """Return only an explicitly recorded Formal-PERMNO bridge."""
    if identity["history_source"] != "FORMAL_CRSP_2025_PLUS_RECENT_EXTENSION_2026":
        return None
    member = identity["membership_security_id"]
    return member.split(":", 1)[1] if member.startswith("CRSP_PERMNO:") else None


class ExtensionFundamentals:
    """Formal fundamental precedence over the governed 2026 PIT-event store."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        events: list[support.Event] = []
        for row in connection.execute(
            "SELECT event_id,frequency,gvkey,datadate,fiscal_year,fiscal_quarter,rdq,"
            "availability_date,availability_status,raw_row_count,conflict_flag,"
            "field_values_json,flow_values_json,ttm_eligible,ttm_reason "
            "FROM pit_fundamental_events"
        ):
            events.append(
                support.Event(
                    event_id=int(row[0]), frequency=str(row[1]), gvkey=str(row[2]), datadate=str(row[3]),
                    fiscal_year=int(row[4]) if row[4] is not None else None,
                    fiscal_quarter=int(row[5]) if row[5] is not None else None,
                    rdq=str(row[6] or ""), availability_date=str(row[7] or ""),
                    availability_status=str(row[8]), raw_row_count=int(row[9]), conflict_flag=int(row[10]),
                    values={key: finite(value) for key, value in json.loads(row[11]).items()},
                    flow_values=json.loads(row[12] or "{}"), ttm_eligible=int(row[13]), ttm_reason=str(row[14]),
                )
            )
        self.metrics_engine = support.PITMetrics(events)

    @staticmethod
    def result(value: float | None = None, frequency: str = "", events: list[support.Event] | None = None,
               fallback: bool = False, reason: str = "") -> dict[str, Any]:
        selected = (events or [])[-1] if events else None
        return {
            "value": value, "source_frequency": frequency,
            "source_event_ids": [event.event_id for event in events or []],
            "source_datadate": selected.datadate if selected else "",
            "source_availability_date": selected.availability_date if selected else "",
            "fallback_used": int(fallback), "failure_reason": reason,
        }

    def value_metrics(self, key: str, signal: str) -> dict[str, dict[str, Any]]:
        if not key:
            return {name: self.result(reason="GVKEY_UNAVAILABLE") for name in ("earnings", "book", "fcf")}
        ttm_ni = self.metrics_engine.ttm(key, signal, ("niq",))
        earnings = self.result(ttm_ni[0]["niq"], "QUARTERLY_TTM", ttm_ni[1]) if ttm_ni else None
        if earnings is None:
            annual = self.metrics_engine.latesta(key, signal, ("ni",))
            earnings = self.result(float(annual.values["ni"]), "ANNUAL", [annual], True) if annual else self.result(reason="ANNUAL_METRIC_UNAVAILABLE")
        quarterly_book = self.metrics_engine.latestq(key, signal, ("ceqq",))
        if quarterly_book:
            book = self.result(float(quarterly_book.values["ceqq"]), "QUARTERLY", [quarterly_book])
        else:
            annual = self.metrics_engine.latesta(key, signal, ("ceq",))
            book = self.result(float(annual.values["ceq"]), "ANNUAL", [annual], True) if annual else self.result(reason="ANNUAL_METRIC_UNAVAILABLE")
        fcf = None
        for ordinal in sorted(self.metrics_engine.quarterly.get(key, {}), reverse=True):
            selected = [self.metrics_engine.period(key, ordinal - offset, signal) for offset in (3, 2, 1, 0)]
            if any(event is None for event in selected):
                continue
            usable = [event for event in selected if event is not None]
            operating = [finite(event.flow_values.get("oancfy_single_quarter")) for event in usable]
            capital = [finite(event.flow_values.get("capxy_single_quarter")) for event in usable]
            if all(value is not None for value in operating + capital):
                fcf = self.result(sum(float(value) for value in operating) - sum(float(value) for value in capital), "QUARTERLY_TTM", usable)
                break
        if fcf is None:
            annual = self.metrics_engine.latesta(key, signal, ("oancf", "capx"))
            fcf = self.result(float(annual.values["oancf"]) - float(annual.values["capx"]), "ANNUAL", [annual], True) if annual else self.result(reason="ANNUAL_METRIC_UNAVAILABLE")
        return {"earnings": earnings, "book": book, "fcf": fcf}

    def metrics(self, key: str, signal: str) -> dict[str, dict[str, Any]]:
        result = self.value_metrics(key, signal) | self.metrics_engine.metrics(key, signal)
        negative = result["negative_leverage"]
        negative["leverage_level"] = -float(negative["value"]) if negative["value"] is not None else None
        investment = result["investment"]
        investment["asset_growth"] = -float(investment["value"]) if investment["value"] is not None else None
        return result


def load_histories(project: Path, market: sqlite3.Connection, formal_store: sqlite3.Connection,
                   identities: dict[tuple[str, str], dict[str, str]]) -> dict[str, tuple[list[str], list[float | None], str]]:
    """Append extension returns only to an approved Formal/temporal PERMNO history."""
    formal_returns: dict[str, list[tuple[str, float | None]]] = defaultdict(list)
    for permno, observed, value in formal_store.execute("SELECT permno,date,dlyret FROM security_daily WHERE date<='2025-12-31' ORDER BY permno,date"):
        formal_returns[str(permno)].append((str(observed), finite(value)))
    extension: dict[str, list[tuple[str, float | None]]] = defaultdict(list)
    for canonical_id, observed, value in market.execute("SELECT canonical_security_id,date,daily_return FROM canonical_security_daily ORDER BY canonical_security_id,date"):
        extension[str(canonical_id)].append((str(observed), finite(value)))
    sources: dict[str, str] = {}
    for identity in identities.values():
        sources[identity["canonical_security_id"]] = identity["history_source"]
    output: dict[str, tuple[list[str], list[float | None], str]] = {}
    for canonical_id, extension_rows in extension.items():
        source = sources.get(canonical_id, "IDENTITY_RECORD_UNAVAILABLE")
        selected: list[tuple[str, float | None]] = []
        if source == "FORMAL_CRSP_2025_PLUS_RECENT_EXTENSION_2026":
            exemplar = next(item for item in identities.values() if item["canonical_security_id"] == canonical_id)
            permno = history_permno(exemplar)
            if permno is None:
                raise RuntimeError(f"UNSUPPORTED_APPROVED_HISTORY_IDENTITY:{canonical_id}")
            selected.extend(formal_returns.get(permno, []))
        selected.extend(extension_rows)
        by_date: dict[str, float | None] = {}
        for observed, value in selected:
            if observed in by_date:
                raise RuntimeError(f"DUPLICATE_CROSS_SOURCE_RETURN_DATE:{canonical_id}:{observed}")
            by_date[observed] = value
        dates = sorted(by_date)
        output[canonical_id] = (dates, [by_date[item] for item in dates], source)
    return output


def source_rows(project: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, tuple[list[str], list[float | None], str]], dict[str, str]]:
    identities = identity_rows(project)
    market_path, support_path, formal_path = project / MARKET, project / SUPPORT, project / FORMAL
    market = sqlite3.connect(f"file:{market_path}?mode=ro", uri=True)
    source = sqlite3.connect(f"file:{support_path}?mode=ro", uri=True)
    prior = sqlite3.connect(f"file:{formal_path}?mode=ro", uri=True)
    market.row_factory = source.row_factory = sqlite3.Row
    try:
        histories = load_histories(project, market, prior, identities)
        rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        query = """
        SELECT r.*,m.ticker,m.prccd,m.temporal_identity_rule,m.identity_provenance,m.return_available,m.market_cap_available
        FROM factor_input_readiness r
        JOIN canonical.canonical_security_daily m
          ON m.date=r.signal_date AND m.canonical_security_id=r.canonical_security_id
        ORDER BY r.signal_date,r.canonical_security_id
        """
        source.execute("ATTACH DATABASE ? AS canonical", (str(market_path),))
        for item in source.execute(query):
            row = dict(item)
            identity = identities.get((row["signal_date"], row["canonical_security_id"]))
            if identity is None:
                raise RuntimeError("MISSING_CROSS_SOURCE_IDENTITY_EVIDENCE")
            row["_identity"] = identity
            rows[row["signal_date"]].append(row)
        metadata = {key: value for key, value in source.execute("SELECT key,value FROM build_metadata")}
        return dict(rows), histories, metadata
    finally:
        market.close(); source.close(); prior.close()


def metric_reason(metrics: dict[str, dict[str, Any]], name: str, fallback: str) -> str:
    return str(metrics.get(name, {}).get("failure_reason") or fallback)


def raw_row(item: dict[str, Any], metrics: dict[str, dict[str, Any]], history: tuple[list[str], list[float | None], str]) -> dict[str, Any]:
    signal = str(item["signal_date"]); dates, returns, history_source = history
    position = bisect_left(dates, signal)
    cap = finite(item["issuer_market_cap"]) if item["issuer_market_cap_available"] else None
    cap = cap if cap and cap > 0 else None
    branch = str(item["quality_branch"])
    if position >= len(dates) or dates[position] != signal:
        history_reason = "SIGNAL_DATE_HISTORY_OBSERVATION_MISSING"
        a: list[float | None] = []; b: list[float | None] = []; recent: list[float | None] = []
        mom12 = mom6 = vol = None
    else:
        history_reason = ""
        a, b = formal.momentum_windows(returns, position)
        mom12 = formal.compounded(a) if len(a) == 231 else None
        mom6 = formal.compounded(b) if len(b) == 105 else None
        recent = returns[max(0, position - 251):position + 1]
        vol = statistics.stdev([value for value in recent if value is not None]) if sum(value is not None for value in recent) >= 200 else None
    def ratio(name: str) -> float | None:
        value = finite(metrics[name]["value"])
        return value * 1000 / cap if value is not None and cap is not None else None
    return {
        "signal_date": signal, "execution_date": str(item["execution_date"]),
        "canonical_security_id": str(item["canonical_security_id"]), "membership_security_id": str(item["membership_security_id"]),
        "gvkey": str(item["gvkey"]), "ticker": str(item["ticker"] or ""), "issuer_key": str(item["issuer_key"] or ""),
        "issuer_identity_class": str(item["issuer_identity_class"]), "gics_status": str(item["gics_status"]),
        "gsector": str(item["gsector"] or ""), "branch": branch, "issuer_market_cap": cap,
        "identity_class": str(item["_identity"]["identity_class"]),
        "temporal_identity_rule": str(item["temporal_identity_rule"]), "history_source": history_source,
        "size_raw": -math.log(cap) if cap else None,
        "mom12_1_raw": mom12, "mom12_obs": sum(value is not None for value in a),
        "mom6_1_raw": mom6, "mom6_obs": sum(value is not None for value in b),
        "vol_252_raw": vol, "lowrisk_vol_raw": -vol if vol is not None else None,
        "vol_obs": sum(value is not None for value in recent),
        "ep_raw": ratio("earnings"), "bp_raw": ratio("book"), "fcf_yield_raw": ratio("fcf"),
        "roe_raw": finite(metrics["roe"]["value"]), "roa_raw": finite(metrics["roa"]["value"]),
        "gpa_raw": finite(metrics["gpa"]["value"]), "negative_leverage_raw": finite(metrics["negative_leverage"]["value"]),
        "leverage_raw": finite(metrics["negative_leverage"].get("leverage_level")),
        "investment_raw": finite(metrics["investment"]["value"]), "asset_growth_raw": finite(metrics["investment"].get("asset_growth")),
        "_metrics": metrics,
        "_metric_reasons": {
            "ep": metric_reason(metrics, "earnings", "EARNINGS_OR_ISSUER_MARKET_CAP_UNAVAILABLE"),
            "bp": metric_reason(metrics, "book", "BOOK_OR_ISSUER_MARKET_CAP_UNAVAILABLE"),
            "fcf_yield": metric_reason(metrics, "fcf", "FCF_OR_ISSUER_MARKET_CAP_UNAVAILABLE"),
            "roe": metric_reason(metrics, "roe", "ROE_UNAVAILABLE"),
            "roa": metric_reason(metrics, "roa", "ROA_UNAVAILABLE"),
            "gpa": metric_reason(metrics, "gpa", "GPA_UNAVAILABLE"),
            "negative_leverage": metric_reason(metrics, "negative_leverage", "LEVERAGE_UNAVAILABLE"),
            "investment": metric_reason(metrics, "investment", "EXACT_YEAR_OVER_YEAR_ASSET_PAIR_UNAVAILABLE"),
            "history": history_reason,
        },
    }


def prepare_date(rows: list[dict[str, Any]], winsor: list[dict[str, Any]], neutralization: list[dict[str, Any]]) -> None:
    for metric in RAW_METRICS:
        formal.preprocess(rows, metric, winsor)
    for row in rows:
        if row["branch"] == "FINANCIAL_REAL_ESTATE":
            value_fields, quality_fields, value_min, quality_min = ("ep_z", "bp_z"), ("roe_z", "roa_z"), 1, 1
        elif row["branch"] == "NONFINANCIAL":
            value_fields, quality_fields, value_min, quality_min = ("ep_z", "bp_z", "fcf_yield_z"), ("roe_z", "gpa_z", "negative_leverage_z"), 2, 2
        else:
            value_fields, quality_fields, value_min, quality_min = (), (), 99, 99
        value_values = [row[field] for field in value_fields if row.get(field) is not None]
        quality_values = [row[field] for field in quality_fields if row.get(field) is not None]
        row["value_component_count"] = len(value_values)
        row["quality_component_count"] = len(quality_values)
        row["value_current_pre"] = formal.equal_available(value_values, value_min)
        row["quality_current_pre"] = formal.equal_available(quality_values, quality_min)
        momentum = [row[field] for field in ("mom12_1_z", "mom6_1_z") if row.get(field) is not None]
        lowrisk = [row[field] for field in ("lowrisk_vol_z",) if row.get(field) is not None]
        row["momentum_component_count"] = len(momentum)
        row["lowrisk_component_count"] = len(lowrisk)
        row["momentum_current_final"] = formal.equal_available(momentum, 1)
        row["lowrisk_current_final"] = formal.equal_available(lowrisk, 1)
        row["value_ep_pre"] = row.get("ep_z")
        row["value_bp_pre"] = row.get("bp_z")
        ep_bp = [row[field] for field in ("ep_z", "bp_z") if row.get(field) is not None]
        row["value_ep_bp_pre"] = sum(ep_bp) / len(ep_bp) if len(ep_bp) == 2 else None
        row["value_ep_bp_component_count"] = len(ep_bp)
        row["quality_roe_pre"] = row.get("roe_z")
        profitability_fields = ("roe_z", "gpa_z") if row["branch"] == "NONFINANCIAL" else ("roe_z", "roa_z") if row["branch"] == "FINANCIAL_REAL_ESTATE" else ()
        profitability = [row[field] for field in profitability_fields if row.get(field) is not None]
        row["quality_profitability_pre"] = sum(profitability) / len(profitability) if profitability_fields and len(profitability) == len(profitability_fields) else None
        row["quality_profitability_component_count"] = len(profitability)
        row["investment_current_pre"] = row.get("investment_z")
    # Frozen Formal semantics re-standardize branch/composite candidates before neutralization.
    for field in ("value_current_pre", "quality_current_pre", "value_ep_bp_pre", "quality_profitability_pre"):
        formal.zfield(rows, field, field)
    for name, pre, residual, final, label in (
        ("value_current", "value_current_pre", "value_current_residual", "value_current_final", "Value"),
        ("value_ep", "value_ep_pre", "value_ep_residual", "value_ep_final", "Value EP"),
        ("value_bp", "value_bp_pre", "value_bp_residual", "value_bp_final", "Value BP"),
        ("value_ep_bp", "value_ep_bp_pre", "value_ep_bp_residual", "value_ep_bp_final", "Value EP BP"),
        ("quality_current", "quality_current_pre", "quality_current_residual", "quality_current_final", "Quality"),
        ("quality_roe", "quality_roe_pre", "quality_roe_residual", "quality_roe_final", "Quality ROE"),
        ("quality_profitability", "quality_profitability_pre", "quality_profitability_residual", "quality_profitability_final", "Quality Profitability"),
        ("investment_current", "investment_current_pre", "investment_current_residual", "investment_current_final", "Investment"),
    ):
        qa = formal.neutralize(rows, pre, residual, final, label)
        qa["specification_id"] = name.upper()
        neutralization.append(qa)


def rank_note() -> str:
    return "NOT_MATERIALIZED__FORMAL_FROZEN_SCORE_IS_STANDARDIZED_SCORE_NOT_RANK"


def unavailable_observation(base: dict[str, Any], registry_row: dict[str, str], reason: str) -> dict[str, Any]:
    return factor_observation(base, registry_row, raw=None, winsor=None, processed=None, neutralized=None,
                              final=None, count=0, reason=reason, components={"governed_unavailable": reason})


def source_metric_names(factor_name: str, branch: str) -> tuple[str, ...]:
    """Return the factor-specific PIT metrics whose provenance is material."""
    if factor_name == "VALUE_CURRENT":
        return ("earnings", "book") if branch == "FINANCIAL_REAL_ESTATE" else ("earnings", "book", "fcf")
    mapping = {
        "VALUE_EP": ("earnings",), "VALUE_BP": ("book",), "VALUE_EP_BP": ("earnings", "book"),
        "VALUE_FCF_COMPONENT": ("fcf",), "QUALITY_ROE": ("roe",),
        "QUALITY_GPA_COMPONENT": ("gpa",), "QUALITY_NEG_LEVERAGE_COMPONENT": ("negative_leverage",),
        "QUALITY_ROA_COMPONENT": ("roa",), "INVESTMENT_CURRENT": ("investment",),
    }
    if factor_name == "QUALITY_CURRENT":
        return ("roe", "roa") if branch == "FINANCIAL_REAL_ESTATE" else ("roe", "gpa", "negative_leverage")
    if factor_name == "QUALITY_PROFITABILITY":
        return ("roe", "roa") if branch == "FINANCIAL_REAL_ESTATE" else ("roe", "gpa")
    return mapping.get(factor_name, ())


def fundamental_provenance(base: dict[str, Any], factor_name: str) -> dict[str, dict[str, Any]]:
    fields = ("source_frequency", "source_datadate", "source_availability_date", "source_event_ids", "event_ids", "fallback_used", "failure_reason")
    return {
        name: {field: base["_metrics"][name].get(field) for field in fields if field in base["_metrics"][name]}
        for name in source_metric_names(factor_name, base["branch"])
    }


def factor_observation(base: dict[str, Any], registry_row: dict[str, str], *, raw: float | None,
                       winsor: float | None, processed: float | None, neutralized: float | None,
                       final: float | None, count: int | None, reason: str, components: dict[str, Any]) -> dict[str, Any]:
    available = int(final is not None)
    return {
        "signal_date": base["signal_date"], "execution_date": base["execution_date"],
        "canonical_security_id": base["canonical_security_id"], "membership_security_id": base["membership_security_id"],
        "gvkey": base["gvkey"], "ticker": base["ticker"], "issuer_key": base["issuer_key"],
        "issuer_identity_class": base["issuer_identity_class"], "identity_class": base["identity_class"],
        "temporal_identity_rule": base["temporal_identity_rule"], "history_source": base["history_source"],
        "gics_status": base["gics_status"], "gsector": base["gsector"], "branch": base["branch"],
        "issuer_market_cap": base["issuer_market_cap"], "factor_name": registry_row["canonical_factor_name"],
        "factor_family": registry_row["factor_family"], "final_k4": int(registry_row["final_k4"] == "TRUE"),
        "raw_signal": raw, "winsorized_signal": winsor, "processed_signal": processed,
        "neutralized_signal": neutralized, "final_score": final,
        "rank_or_percentile": None, "rank_status": rank_note(), "component_count": count,
        "availability": available, "missing_reason": "" if available else reason,
        "components_json": dump(components), "provenance_json": dump({
            "data_lineage": LINEAGE, "factor_version": VERSION, "history_source": base["history_source"],
            "temporal_identity_rule": base["temporal_identity_rule"], "metric_reasons": base["_metric_reasons"],
            "fundamental_source_provenance": fundamental_provenance(base, registry_row["canonical_factor_name"]),
        }), "source_lineage": LINEAGE, "factor_version": VERSION,
    }


def missing(base: dict[str, Any], raw_present: bool, metric: str, *, domain: bool = True, neutralized: bool = False) -> str:
    if not domain:
        return "NATURAL_DOMAIN_EXCLUDED"
    if not raw_present:
        return base["_metric_reasons"].get(metric) or "RAW_INPUT_UNAVAILABLE"
    if neutralized:
        if base["issuer_market_cap"] is None:
            return "ISSUER_MARKET_CAP_UNAVAILABLE"
        if not base["gsector"]:
            return "HISTORICAL_GICS_UNAVAILABLE"
        return "NEUTRALIZATION_INPUT_UNAVAILABLE"
    return "STANDARDIZATION_OR_COMPONENT_UNAVAILABLE"


def observations_for_row(base: dict[str, Any], reg: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    get = lambda name: reg[name]
    components = lambda **kwargs: kwargs
    result: list[dict[str, Any]] = []
    def direct(name: str, raw_name: str, *, domain: bool = True) -> None:
        raw, winsor, score = base.get(raw_name + "_raw"), base.get(raw_name + "_winsor"), base.get(raw_name + "_z")
        result.append(factor_observation(base, get(name), raw=raw, winsor=winsor, processed=score, neutralized=None,
            final=score if domain else None, count=int(score is not None and domain),
            reason=missing(base, raw is not None, raw_name, domain=domain), components=components(metric=raw_name)))
    # Value, including frozen neutralized challenger specifications.
    for name, key, raw_name, count in (
        ("VALUE_CURRENT", "value_current", "value_current", base["value_component_count"]),
        ("VALUE_EP", "value_ep", "ep", 1), ("VALUE_BP", "value_bp", "bp", 1),
        ("VALUE_EP_BP", "value_ep_bp", "value_ep_bp", base["value_ep_bp_component_count"]),
    ):
        final, pre, residual = base.get(key + "_final"), base.get(key + "_pre"), base.get(key + "_residual")
        raw = base.get(raw_name + "_raw") if raw_name in {"ep", "bp"} else None
        winsor = base.get(raw_name + "_winsor") if raw_name in {"ep", "bp"} else None
        result.append(factor_observation(base, get(name), raw=raw, winsor=winsor, processed=pre, neutralized=residual,
            final=final, count=count, reason=missing(base, pre is not None, "ep" if name == "VALUE_EP" else "bp" if name == "VALUE_BP" else "fcf_yield", neutralized=True),
            components=components(branch=base["branch"], component_count=count)))
    direct("VALUE_FCF_COMPONENT", "fcf_yield", domain=base["branch"] == "NONFINANCIAL")
    # Quality, its neutralized challengers, and natural-domain diagnostics.
    for name, key, raw_name, count in (
        ("QUALITY_CURRENT", "quality_current", "quality_current", base["quality_component_count"]),
        ("QUALITY_ROE", "quality_roe", "roe", 1),
        ("QUALITY_PROFITABILITY", "quality_profitability", "quality_profitability", base["quality_profitability_component_count"]),
    ):
        final, pre, residual = base.get(key + "_final"), base.get(key + "_pre"), base.get(key + "_residual")
        raw = base.get(raw_name + "_raw") if raw_name == "roe" else None
        winsor = base.get(raw_name + "_winsor") if raw_name == "roe" else None
        result.append(factor_observation(base, get(name), raw=raw, winsor=winsor, processed=pre, neutralized=residual,
            final=final, count=count, reason=missing(base, pre is not None, "roe", neutralized=True),
            components=components(branch=base["branch"], component_count=count)))
    direct("QUALITY_GPA_COMPONENT", "gpa", domain=base["branch"] == "NONFINANCIAL")
    direct("QUALITY_NEG_LEVERAGE_COMPONENT", "negative_leverage", domain=base["branch"] == "NONFINANCIAL")
    direct("QUALITY_ROA_COMPONENT", "roa", domain=base["branch"] == "FINANCIAL_REAL_ESTATE")
    # Momentum and Low Risk use no baseline neutralization.
    result.append(factor_observation(base, get("MOMENTUM_CURRENT"), raw=None, winsor=None,
        processed=base["momentum_current_final"], neutralized=None, final=base["momentum_current_final"],
        count=base["momentum_component_count"], reason=missing(base, base["momentum_component_count"] > 0, "history"),
        components=components(mom12_1=base.get("mom12_1_z"), mom6_1=base.get("mom6_1_z"))))
    direct("MOMENTUM_12_1", "mom12_1")
    direct("MOMENTUM_6_1", "mom6_1")
    result.append(factor_observation(base, get("LOWRISK_CURRENT"), raw=None, winsor=None,
        processed=base["lowrisk_current_final"], neutralized=None, final=base["lowrisk_current_final"],
        count=base["lowrisk_component_count"], reason=missing(base, base["lowrisk_component_count"] > 0, "history"),
        components=components(volatility252=base.get("lowrisk_vol_z"), ivol252=None, component_minimum=1)))
    direct("LOWRISK_VOL", "lowrisk_vol")
    result.append(unavailable_observation(base, get("LOWRISK_IVOL"), UNAVAILABLE["LOWRISK_IVOL"]))
    # Investment, Dividend, and Size.
    result.append(factor_observation(base, get("INVESTMENT_CURRENT"), raw=base.get("investment_raw"), winsor=base.get("investment_winsor"),
        processed=base.get("investment_current_pre"), neutralized=base.get("investment_current_residual"), final=base.get("investment_current_final"),
        count=1, reason=missing(base, base.get("investment_current_pre") is not None, "investment", neutralized=True),
        components=components(asset_growth=base.get("asset_growth_raw"))))
    result.append(unavailable_observation(base, get("DIVIDEND_CURRENT"), UNAVAILABLE["DIVIDEND_CURRENT"]))
    direct("SIZE_CURRENT", "size")
    if {row["factor_name"] for row in result} != set(reg) or len(result) != 20:
        raise RuntimeError("INCOMPLETE_FACTOR_OBSERVATION_SET")
    return result


SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE source_manifest (relative_path TEXT PRIMARY KEY,sha256 TEXT NOT NULL,source_role TEXT NOT NULL);
CREATE TABLE factor_registry (
  factor_order INTEGER PRIMARY KEY, factor_name TEXT UNIQUE NOT NULL, factor_family TEXT NOT NULL,
  role TEXT NOT NULL, economic_definition TEXT NOT NULL, component_formula TEXT NOT NULL,
  branch_rule TEXT NOT NULL, direction TEXT NOT NULL, final_k4 INTEGER NOT NULL,
  governed_unavailable_reason TEXT, registry_json TEXT NOT NULL
);
CREATE TABLE factor_observations (
  signal_date TEXT NOT NULL, execution_date TEXT NOT NULL, canonical_security_id TEXT NOT NULL,
  membership_security_id TEXT NOT NULL, gvkey TEXT NOT NULL, ticker TEXT, issuer_key TEXT,
  issuer_identity_class TEXT NOT NULL, identity_class TEXT NOT NULL, temporal_identity_rule TEXT NOT NULL,
  history_source TEXT NOT NULL, gics_status TEXT NOT NULL, gsector TEXT, branch TEXT NOT NULL,
  issuer_market_cap REAL, factor_name TEXT NOT NULL, factor_family TEXT NOT NULL, final_k4 INTEGER NOT NULL,
  raw_signal REAL, winsorized_signal REAL, processed_signal REAL, neutralized_signal REAL,
  final_score REAL, rank_or_percentile REAL, rank_status TEXT NOT NULL, component_count INTEGER,
  availability INTEGER NOT NULL, missing_reason TEXT NOT NULL, components_json TEXT NOT NULL,
  provenance_json TEXT NOT NULL, source_lineage TEXT NOT NULL, factor_version TEXT NOT NULL,
  PRIMARY KEY(signal_date,canonical_security_id,factor_name)
);
CREATE INDEX observation_factor_date_idx ON factor_observations(factor_name,signal_date);
CREATE INDEX observation_date_idx ON factor_observations(signal_date);
"""


def create_store(project: Path) -> dict[str, Any]:
    reg_rows = registry(project); reg = {row["canonical_factor_name"]: row for row in reg_rows}
    decisions = readiness_decisions(project)
    sources, histories, metadata = source_rows(project)
    if len(sources) != EXPECTED_DATES or min(sources) != EXPECTED_START or max(sources) != EXPECTED_END:
        raise RuntimeError("UNAPPROVED_SIGNAL_CALENDAR")
    support_connection = sqlite3.connect(f"file:{project / SUPPORT}?mode=ro", uri=True)
    fundamentals = ExtensionFundamentals(support_connection)
    destination = project / OUTPUT; destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".recent_extension_factor_library.", suffix=".sqlite", dir=destination.parent)
    os.close(fd); temporary = Path(temp_name)
    winsor: list[dict[str, Any]] = []; neutralization: list[dict[str, Any]] = []
    try:
        output = sqlite3.connect(temporary); output.executescript(SCHEMA)
        output.executemany(
            "INSERT INTO factor_registry VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(
                int(row["factor_order"]), row["canonical_factor_name"], row["factor_family"], row["role"],
                row["economic_definition"], row["component_formula"], row["branch_rule"], row["direction"],
                int(row["final_k4"] == "TRUE"), UNAVAILABLE.get(row["canonical_factor_name"]), dump(row),
            ) for row in reg_rows],
        )
        source_paths = ((MARKET, "COMPLETED_2026_MARKET_CANONICAL"), (SUPPORT, "PIT_GICS_ISSUER_CAP_SUPPORT"),
                        (FORMAL, "FORMAL_CRSP_HISTORY_READ_ONLY"), (REGISTRY, "FROZEN_20_FACTOR_REGISTRY"),
                        (READINESS, "FROZEN_2026_READINESS_DECISIONS"), (IDENTITY, "APPROVED_CROSS_SOURCE_IDENTITY"))
        output.executemany("INSERT INTO source_manifest VALUES (?,?,?)", [(str(path), sha256(project / path), role) for path, role in source_paths])
        meta = {
            "data_lineage": LINEAGE, "factor_version": VERSION, "signal_date_count": str(len(sources)),
            "signal_date_start": min(sources), "signal_date_end": max(sources), "factor_count": "20",
            "available_factor_count": "18", "unavailable_factor_count": "2", "final_k4": ",".join(FINAL_K4),
            "support_build_metadata": dump(metadata), "rank_policy": rank_note(),
        }
        output.executemany("INSERT INTO metadata VALUES (?,?)", sorted(meta.items()))
        fields = [row[1] for row in output.execute("PRAGMA table_info(factor_observations)")]
        insert = "INSERT INTO factor_observations VALUES (" + ",".join("?" for _ in fields) + ")"
        total = 0; pit_violation = 0
        for signal in sorted(sources):
            date_rows: list[dict[str, Any]] = []
            for item in sources[signal]:
                metrics = fundamentals.metrics(str(item["gvkey"]), signal)
                for metric in metrics.values():
                    available = str(metric.get("source_availability_date") or "")
                    if available and available > signal:
                        pit_violation += 1
                history = histories.get(str(item["canonical_security_id"]))
                if history is None:
                    raise RuntimeError("MISSING_HISTORY_INTERFACE")
                date_rows.append(raw_row(item, metrics, history))
            prepare_date(date_rows, winsor, neutralization)
            observations = [observation for row in date_rows for observation in observations_for_row(row, reg)]
            output.executemany(insert, [tuple(row.get(field) for field in fields) for row in observations])
            total += len(observations)
        if pit_violation:
            raise RuntimeError(f"PIT_VIOLATION:{pit_violation}")
        output.commit(); output.close(); os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        support_connection.close()
    return {"rows": total, "winsor": winsor, "neutralization": neutralization, "readiness": decisions,
            "pit_violations": pit_violation}


def logical_hash(path: Path) -> str:
    digest = hashlib.sha256(); connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for table, order in (("metadata", "key"), ("factor_registry", "factor_order"),
                             ("factor_observations", "signal_date,canonical_security_id,factor_name")):
            digest.update((table + "\n").encode())
            for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}"):
                digest.update(dump(list(row)).encode() + b"\n")
    finally:
        connection.close()
    return digest.hexdigest()


def registry_validation_rows(
    connection: sqlite3.Connection,
    signal_dates: Sequence[str],
    factors: Sequence[dict[str, Any]],
    pit_violations: int,
) -> list[dict[str, Any]]:
    """Validate every registry factor against the complete security-date grid."""

    expected_names = tuple(item["factor_name"] for item in factors)
    observed_names = {row[0] for row in connection.execute("SELECT DISTINCT factor_name FROM factor_observations")}
    grid_count = connection.execute(
        "SELECT COUNT(*) FROM (SELECT signal_date,canonical_security_id FROM factor_observations GROUP BY 1,2)"
    ).fetchone()[0]
    total_observations = connection.execute("SELECT COUNT(*) FROM factor_observations").fetchone()[0]
    constructible_count = connection.execute(
        "SELECT COUNT(*) FROM (SELECT factor_name FROM factor_observations GROUP BY factor_name HAVING SUM(availability)>0)"
    ).fetchone()[0]
    observed_k4 = tuple(item["factor_name"] for item in factors if item["final_k4"])
    calendar_ok = (
        len(signal_dates) == EXPECTED_DATES
        and tuple(signal_dates) == tuple(sorted(signal_dates))
        and signal_dates[0] == EXPECTED_START
        and signal_dates[-1] == EXPECTED_END
    )
    global_contract_ok = (
        len(expected_names) == 20
        and len(set(expected_names)) == 20
        and observed_names == set(expected_names)
        and total_observations == grid_count * len(expected_names)
        and constructible_count == 18
        and observed_k4 == FINAL_K4
        and calendar_ok
        and pit_violations == 0
    )
    rows: list[dict[str, Any]] = []
    for item in factors:
        name = str(item["factor_name"])
        observation_count, available_count, raw_count, final_count, reason_count = connection.execute(
            "SELECT COUNT(*),SUM(availability),SUM(raw_signal IS NOT NULL),SUM(final_score IS NOT NULL),"
            "SUM(missing_reason=?) FROM factor_observations WHERE factor_name=?",
            (UNAVAILABLE.get(name, ""), name),
        ).fetchone()
        expected_reason = UNAVAILABLE.get(name, "")
        if expected_reason:
            unavailable_contract_status = "PASS" if (
                available_count == 0
                and raw_count == 0
                and final_count == 0
                and reason_count == observation_count
            ) else "FAIL"
        else:
            unavailable_contract_status = "NOT_APPLICABLE"
        row_ok = (
            name in observed_names
            and observation_count == grid_count
            and (unavailable_contract_status == "PASS" if expected_reason else (available_count or 0) > 0)
        )
        rows.append({
            "factor_name": name,
            "factor_family": item["factor_family"],
            "final_k4": item["final_k4"],
            "registry_present": int(name in observed_names),
            "expected_observation_count": grid_count,
            "observation_count": observation_count,
            "available_observation_count": available_count or 0,
            "registry_factor_count": len(expected_names),
            "observed_factor_count": len(observed_names),
            "constructible_factor_count": constructible_count,
            "signal_date_count": len(signal_dates),
            "signal_date_start": signal_dates[0] if signal_dates else "",
            "signal_date_end": signal_dates[-1] if signal_dates else "",
            "pit_violation_count": pit_violations,
            "governed_unavailable_reason": expected_reason,
            "unavailable_contract_status": unavailable_contract_status,
            "final_k4_contract_status": "PASS" if observed_k4 == FINAL_K4 else "FAIL",
            "status": "PASS" if global_contract_ok and row_ok else "FAIL",
        })
    return rows


def refresh_registry_validation(project: Path | None = None) -> dict[str, Any]:
    """Regenerate only the registry-validation audit from completed stores."""

    project = (project or root()).resolve()
    for path in (OUTPUT, SUPPORT):
        if not (project / path).is_file():
            raise RuntimeError(f"REQUIRED_INPUT_MISSING:{path}")
    with sqlite3.connect(f"file:{project / OUTPUT}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        signal_dates = [row[0] for row in connection.execute("SELECT DISTINCT signal_date FROM factor_observations ORDER BY signal_date")]
        factors = [dict(row) for row in connection.execute("SELECT * FROM factor_registry ORDER BY factor_order")]
        with sqlite3.connect(f"file:{project / SUPPORT}?mode=ro", uri=True) as support_connection:
            pit_violations = support_connection.execute(
                "SELECT COUNT(*) FROM pit_fundamental_signal_asof "
                "WHERE source_availability_date<>'' AND source_availability_date>signal_date"
            ).fetchone()[0]
        rows = registry_validation_rows(connection, signal_dates, factors, pit_violations)
    write_csv(project / AUDIT / "recent_extension_factor_library_registry_validation.csv", tuple(rows[0]), rows)
    return {"rows": len(rows), "pit_violations": pit_violations, "status": {row["status"] for row in rows}}


def audit(project: Path, result: dict[str, Any]) -> dict[str, Any]:
    path = project / OUTPUT; connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True); connection.row_factory = sqlite3.Row
    try:
        signal_dates = [row[0] for row in connection.execute("SELECT DISTINCT signal_date FROM factor_observations ORDER BY signal_date")]
        factors = [dict(row) for row in connection.execute("SELECT * FROM factor_registry ORDER BY factor_order")]
        total_observations = connection.execute("SELECT COUNT(*) FROM factor_observations").fetchone()[0]
        duplicate_keys = connection.execute("SELECT COUNT(*) FROM (SELECT signal_date,canonical_security_id,factor_name,COUNT(*) n FROM factor_observations GROUP BY 1,2,3 HAVING n>1)").fetchone()[0]
        unsupported_identity = connection.execute("SELECT COUNT(*) FROM factor_observations WHERE identity_class='' OR history_source='' ").fetchone()[0]
        rank_nonnull = connection.execute("SELECT COUNT(*) FROM factor_observations WHERE rank_or_percentile IS NOT NULL").fetchone()[0]
        missing_without_reason = connection.execute("SELECT COUNT(*) FROM factor_observations WHERE availability=0 AND missing_reason='' ").fetchone()[0]
        lineage_violations = 0
        for row in connection.execute("SELECT signal_date,provenance_json FROM factor_observations"):
            payload = json.loads(row[1])
            # Fundamental source date is retained in components only when selected; the library itself contains no later-date inputs.
            if payload.get("data_lineage") != LINEAGE:
                lineage_violations += 1
        actual = {row[0] for row in connection.execute("SELECT DISTINCT factor_name FROM factor_observations")}
        registry_rows = registry_validation_rows(connection, signal_dates, factors, result["pit_violations"])
        write_csv(project / AUDIT / "recent_extension_factor_library_registry_validation.csv", tuple(registry_rows[0]), registry_rows)
        coverage = []
        for item in factors:
            name = item["factor_name"]
            n, available = connection.execute("SELECT COUNT(*),SUM(availability) FROM factor_observations WHERE factor_name=?", (name,)).fetchone()
            coverage.append({"factor_name": name, "factor_family": item["factor_family"], "final_k4": item["final_k4"],
                "observation_count": n, "available_count": available or 0, "coverage": (available or 0) / n if n else 0.0,
                "data_lineage": LINEAGE})
        write_csv(project / AUDIT / "recent_extension_factor_library_coverage.csv", tuple(coverage[0]), coverage)
        date_coverage = []
        for signal in signal_dates:
            for item in factors:
                name = item["factor_name"]
                n, available = connection.execute("SELECT COUNT(*),SUM(availability) FROM factor_observations WHERE signal_date=? AND factor_name=?", (signal, name)).fetchone()
                date_coverage.append({"signal_date": signal, "factor_name": name, "factor_family": item["factor_family"],
                    "universe_count": n, "available_count": available or 0, "coverage": (available or 0) / n if n else 0.0})
        write_csv(project / AUDIT / "recent_extension_factor_library_signal_date_coverage.csv", tuple(date_coverage[0]), date_coverage)
        missingness = [{"factor_name": row[0], "missing_reason": row[1], "observation_count": row[2], "data_lineage": LINEAGE}
            for row in connection.execute("SELECT factor_name,missing_reason,COUNT(*) FROM factor_observations WHERE availability=0 GROUP BY factor_name,missing_reason ORDER BY factor_name,missing_reason")]
        write_csv(project / AUDIT / "recent_extension_factor_library_missingness.csv", tuple(missingness[0]), missingness)
        decisions = result["readiness"]
        k4 = []
        for name in FINAL_K4:
            counts = [row[0] for row in connection.execute("SELECT SUM(availability) FROM factor_observations WHERE factor_name=? GROUP BY signal_date ORDER BY signal_date", (name,))]
            decision = decisions[name]
            low, high = int(decision["minimum_expected_ready_count"]), int(decision["maximum_expected_ready_count"])
            k4.append({"factor_name": name, "actual_min_available": min(counts), "actual_max_available": max(counts),
                "expected_min_available": low, "expected_max_available": high,
                "out_of_expected_date_count": sum(not(low <= count <= high) for count in counts),
                "status": "PASS" if all(low <= count <= high for count in counts) else "FAIL"})
        write_csv(project / AUDIT / "recent_extension_factor_library_k4_validation.csv", tuple(k4[0]), k4)
        unavailable = []
        for name, reason in UNAVAILABLE.items():
            n, raw, processed, neutralized, final, rank, reason_count = connection.execute(
                "SELECT COUNT(*),SUM(raw_signal IS NOT NULL),SUM(processed_signal IS NOT NULL),SUM(neutralized_signal IS NOT NULL),"
                "SUM(final_score IS NOT NULL),SUM(rank_or_percentile IS NOT NULL),SUM(missing_reason=?) FROM factor_observations WHERE factor_name=?", (reason, name)
            ).fetchone()
            unavailable.append({"factor_name": name, "expected_reason": reason, "observation_count": n,
                "nonnull_raw_count": raw or 0, "nonnull_processed_count": processed or 0, "nonnull_neutralized_count": neutralized or 0,
                "nonnull_final_count": final or 0, "nonnull_rank_count": rank or 0, "correct_reason_count": reason_count or 0,
                "status": "PASS" if n == len(signal_dates) * sum(row["universe_count"] for row in date_coverage if row["signal_date"] == signal_dates[0] and row["factor_name"] == name) / 1 else "CHECK"})
        # Each unavailable factor has exactly one observation per supported signal-date/security; apply the exact check separately.
        for row in unavailable:
            expected = connection.execute("SELECT COUNT(*) FROM factor_observations WHERE factor_name='SIZE_CURRENT'").fetchone()[0]
            row["status"] = "PASS" if row["observation_count"] == expected and all(row[key] == 0 for key in ("nonnull_raw_count", "nonnull_processed_count", "nonnull_neutralized_count", "nonnull_final_count", "nonnull_rank_count")) and row["correct_reason_count"] == expected else "FAIL"
        write_csv(project / AUDIT / "recent_extension_factor_library_unavailable_factor_validation.csv", tuple(unavailable[0]), unavailable)
        checks = {
            "REGISTRY_EXACT_20": len(factors) == 20 and len(actual) == 20,
            "FINAL_K4_EXACT_4": tuple(item["factor_name"] for item in factors if item["final_k4"]) == FINAL_K4,
            "SIGNAL_CALENDAR_40": len(signal_dates) == EXPECTED_DATES and signal_dates[0] == EXPECTED_START and signal_dates[-1] == EXPECTED_END,
            "NO_DUPLICATE_NATURAL_KEYS": duplicate_keys == 0,
            "NO_UNSUPPORTED_IDENTITY": unsupported_identity == 0,
            "MISSING_REASON_COMPLETE": missing_without_reason == 0,
            "GOVERNED_UNAVAILABLE_NULL": all(row["status"] == "PASS" for row in unavailable),
            "K4_EXPECTED_COVERAGE": all(row["status"] == "PASS" for row in k4),
            "PIT_AVAILABILITY": result["pit_violations"] == 0,
            "PIT_LINEAGE": lineage_violations == 0,
            "NO_IMPLICIT_RANK_SUBSTITUTION": rank_nonnull == 0,
            "NO_OUTCOME_OR_STRATEGY_COLUMNS": not ({row[1] for row in connection.execute("PRAGMA table_info(factor_observations)")} & FORBIDDEN_OUTPUT_COLUMNS),
            "OBSERVATION_COUNT": total_observations == sum(row["observation_count"] for row in coverage),
        }
        blockers = [name for name, passed in checks.items() if not passed]
        return {"checks": checks, "blockers": blockers, "coverage": coverage, "k4": k4, "unavailable": unavailable,
                "total_observations": total_observations, "signal_dates": signal_dates, "logical_hash": logical_hash(path),
                "winsor": result["winsor"], "neutralization": result["neutralization"]}
    finally:
        connection.close()


def documentation(project: Path, evidence: dict[str, Any]) -> None:
    verdict = "2026 FULL 20-FACTOR LIBRARY PASS WITH EXPECTED UNAVAILABLE FACTORS" if not evidence["blockers"] else "2026 FULL 20-FACTOR LIBRARY FAIL"
    lines = [
        "# WRDS 2026 Full 20-Factor Research Library Build", "",
        "## Result", "", f"**{verdict}**.", "",
        "## Scope", "",
        "This is a separate `RECENT_EXTENSION_MIXED_SOURCE` 2026 security-level research library. It does not modify or merge the frozen Formal 2006–2025 factor store.", "",
        "## Registry and timing", "",
        "The complete frozen 20-factor registry is retained at every approved weekly signal date. The completed calendar has 40 dates from 2026-01-02 through 2026-10-02; the incomplete 2026-10-09 week is excluded.", "",
        "Final K=4 is exactly `QUALITY_CURRENT`, `MOMENTUM_12_1`, `LOWRISK_CURRENT`, and `INVESTMENT_CURRENT`.", "",
        "## Preserved semantics", "",
        "The build reuses Formal same-date 1%/99% winsorization, z-scoring, branch rules, component minima, historical-GICS and issuer-cap neutralization, and PIT source precedence. Fundamental source selection retains RDQ-to-next-local-trading-day timing and annual exact-Q4 availability. Market-history inputs use approved Formal-CRSP-to-extension identity evidence only; no ticker/GVKEY inference or return fill is used.", "",
        "## Governed unavailable factors", "",
        "`LOWRISK_IVOL` is retained with null raw/processed/neutralized/final/rank fields at every observation and reason `CRSP_1000080_2026_SOURCE_UNAVAILABLE`. `DIVIDEND_CURRENT` is retained identically with reason `CRSP_DISTRIBUTION_TAXONOMY_NOT_REPRODUCIBLE`. No proxy, replacement source, or heuristic is used.", "",
        "## Scores and rank", "",
        "`final_score` is the frozen standardized score or residual-z. `rank_or_percentile` is deliberately null: Formal semantics contain no rank field, so a rank is not silently substituted for the score.", "",
        "## Validation", "",
    ]
    for name, passed in evidence["checks"].items():
        lines.append(f"- `{name}`: {'PASS' if passed else 'FAIL'}")
    lines += ["", "## Explicitly not performed", "", "No factor-return analysis, IC, portfolio construction, FMOM/RA-FMOM, Gate B/C decision, holdings, transaction cost, NAV, or strategy-performance calculation was run.", ""]
    (project / AUDIT / "WRDS_2026_FULL_20_FACTOR_LIBRARY_BUILD.md").write_text("\n".join(lines), encoding="utf-8")


def build(project: Path | None = None) -> dict[str, Any]:
    project = (project or root()).resolve()
    for path in (MARKET, SUPPORT, FORMAL, REGISTRY, READINESS, IDENTITY):
        if not (project / path).is_file():
            raise RuntimeError(f"REQUIRED_INPUT_MISSING:{path}")
    result = create_store(project)
    evidence = audit(project, result)
    documentation(project, evidence)
    return {"verdict": "2026 FULL 20-FACTOR LIBRARY PASS WITH EXPECTED UNAVAILABLE FACTORS" if not evidence["blockers"] else "2026 FULL 20-FACTOR LIBRARY FAIL", **evidence}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=root())
    args = parser.parse_args()
    result = build(args.root)
    print(dump({"verdict": result["verdict"], "logical_hash": result["logical_hash"], "blockers": result["blockers"], "observations": result["total_observations"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
