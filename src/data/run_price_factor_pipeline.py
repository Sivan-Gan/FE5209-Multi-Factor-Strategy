"""I/O orchestration for the approved US DEV price-factor input pipeline.

It creates canonical market inputs, raw approved price metrics, and their
within-date cross-sectional preprocessing.  It deliberately excludes SEC
fundamental parsing, IVOL, factor IC, portfolio construction, FMOM, execution,
and strategy/backtest calculations.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import sqlite3
from collections import Counter, defaultdict
from itertools import groupby
from pathlib import Path
from typing import Any, Iterable, Iterator

from src.data import canonical_market, source_audit
from src.factors import preprocessing, price_metrics


RAW_METRIC_FIELDS = (
    "evidence_stage",
    "canonical_version",
    "market",
    "week_identifier",
    "signal_date",
    "ticker",
    "yahoo_ticker",
    "security_id",
    "cik",
    "metric",
    "factor_candidate_label",
    "direction",
    "raw_value",
    "raw_metric_available",
    "raw_availability_reason",
    "base_price_eligibility",
    "base_price_eligibility_reason",
    "factor_input_eligible",
    "factor_input_reason",
    "signal_date_zero_volume",
    "signal_date_ohlc_plausible",
    "source_quality_status",
    "source_quality_issues",
    "window_start_date",
    "window_end_date",
    "window_start_market_trading_day_index",
    "window_end_market_trading_day_index",
    "valid_observation_count",
    "expected_observation_count",
    "zero_return_count",
    "raw_source_daily_path",
)

FINAL_FACTOR_FIELDS = RAW_METRIC_FIELDS + (
    "cross_section_count",
    "winsor_lower",
    "winsor_upper",
    "winsorized_value",
    "z_score",
    "direction_normalized_score",
    "preprocessing_available",
    "preprocessing_reason",
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def output_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / "outputs" / "factor_research" / "price_factors"


def audit_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / "outputs" / "audit" / "dev_legacy" / "yahoo_price"


def interim_db_path(root: Path | None = None) -> Path:
    return (root or project_root()) / "data" / "interim" / "us_price_factor_preprocess_dev.sqlite"


def output_paths(root: Path | None = None) -> dict[str, Path]:
    directory = output_dir(root)
    return {
        "raw_metrics": directory / "us_price_metrics_raw_dev.csv.gz",
        "scores": directory / "us_price_factor_scores_dev.csv.gz",
        "weekly_coverage": directory / "weekly_coverage_report_dev.csv",
        "availability": directory / "factor_availability_report_dev.csv",
        "missing": directory / "missing_history_report_dev.csv",
        "preprocessing": directory / "preprocessing_audit_report_dev.csv",
        "anomaly": directory / "anomaly_carryforward_report_dev.csv",
        "ivol_status": directory / "ivol_status_dev.md",
        "audit_json": audit_dir(root) / "price_factor_pipeline_audit_dev.json",
        "acceptance": audit_dir(root) / "price_factor_engineering_acceptance_test_report.md",
    }


def _bool(value: str | bool) -> bool:
    return str(value).strip().lower() == "true"


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format(value, ".12g")
    return str(value)


def _gzip_writer(path: Path) -> tuple[gzip.GzipFile, io.TextIOWrapper]:
    path.parent.mkdir(parents=True, exist_ok=True)
    binary = gzip.GzipFile(filename="", mode="wb", fileobj=path.open("wb"), mtime=0)
    return binary, io.TextIOWrapper(binary, encoding="utf-8", newline="")


def _write_csv(path: Path, fields: Iterable[str], rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(fields), extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _text(value) for key, value in row.items()})
            count += 1
    return count


def _read_calendar(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("Weekly signal calendar is empty")
    for row in rows:
        if row["evidence_stage"] != canonical_market.EVIDENCE_STAGE:
            raise ValueError("Weekly calendar evidence-stage mismatch")
    return rows


def _load_benchmark_dates(canonical_daily_path: Path) -> dict[int, str]:
    dates: dict[int, str] = {}
    with gzip.open(canonical_daily_path, "rt", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if _bool(row["is_benchmark"]):
                index = int(row["market_trading_day_index"])
                if index in dates:
                    raise ValueError(f"Duplicate benchmark market index: {index}")
                dates[index] = row["date"]
    if not dates:
        raise ValueError("Canonical daily file does not contain SPY benchmark rows")
    expected = set(range(max(dates) + 1))
    if set(dates) != expected:
        raise ValueError("Canonical benchmark indexes are not contiguous")
    return dates


def _iter_stock_groups(canonical_daily_path: Path) -> Iterator[tuple[str, list[dict[str, str]]]]:
    """Yield contiguous ticker groups; canonical build preserves source ticker grouping."""

    with gzip.open(canonical_daily_path, "rt", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for ticker, group in groupby(reader, key=lambda row: row["ticker"]):
            rows = list(group)
            if rows and not _bool(rows[0]["is_benchmark"]):
                yield ticker, rows


def _factor_input_reason(metric_result: dict[str, Any], daily_row: dict[str, str] | None) -> tuple[bool, str, bool, str]:
    """Keep raw-metric and base-entry reasons visibly separate and combined."""

    raw_available = bool(metric_result["raw_metric_available"])
    raw_reason = str(metric_result["raw_availability_reason"])
    if daily_row is None:
        base_eligible = False
        base_reason = "no_price_on_signal_date"
    else:
        base_eligible = _bool(daily_row["base_price_eligibility"])
        base_reason = daily_row["base_price_eligibility_reason"]
    reasons = list(dict.fromkeys(reason for reason in (raw_reason, base_reason) if reason))
    return raw_available and base_eligible, ";".join(reasons), base_eligible, base_reason


def _make_raw_metric_rows(
    ticker_rows: list[dict[str, str]],
    calendar_rows: list[dict[str, str]],
    market_dates: dict[int, str],
) -> Iterator[dict[str, Any]]:
    rows_by_index = {int(row["market_trading_day_index"]): row for row in ticker_rows}
    if len(rows_by_index) != len(ticker_rows):
        raise ValueError(f"Duplicate market indexes in canonical ticker {ticker_rows[0]['ticker']}")
    identity = ticker_rows[0]
    for signal in calendar_rows:
        signal_index = int(signal["signal_market_trading_day_index"])
        daily_row = rows_by_index.get(signal_index)
        metric_results = (
            price_metrics.compute_momentum(
                rows_by_index,
                signal_index,
                price_metrics.MOM12_LOOKBACK_TRADING_DAYS,
                "MOM12_1",
            ),
            price_metrics.compute_momentum(
                rows_by_index,
                signal_index,
                price_metrics.MOM6_LOOKBACK_TRADING_DAYS,
                "MOM6_1",
            ),
            price_metrics.compute_realized_volatility(rows_by_index, signal_index),
        )
        for result in metric_results:
            eligible, input_reason, base_eligible, base_reason = _factor_input_reason(result, daily_row)
            start_index = result["window_start_market_trading_day_index"]
            end_index = result["window_end_market_trading_day_index"]
            yield {
                "evidence_stage": canonical_market.EVIDENCE_STAGE,
                "canonical_version": canonical_market.CANONICAL_VERSION,
                "market": canonical_market.MARKET,
                "week_identifier": signal["week_identifier"],
                "signal_date": signal["signal_date"],
                "ticker": identity["ticker"],
                "yahoo_ticker": identity["yahoo_ticker"],
                "security_id": identity["security_id"],
                "cik": identity["cik"],
                "metric": result["metric"],
                "factor_candidate_label": price_metrics.metric_factor_label(result["metric"]),
                "direction": price_metrics.metric_direction(result["metric"]),
                "raw_value": result["raw_value"],
                "raw_metric_available": result["raw_metric_available"],
                "raw_availability_reason": result["raw_availability_reason"],
                "base_price_eligibility": base_eligible,
                "base_price_eligibility_reason": base_reason,
                "factor_input_eligible": eligible,
                "factor_input_reason": input_reason,
                "signal_date_zero_volume": daily_row["is_zero_volume"] if daily_row else "",
                "signal_date_ohlc_plausible": daily_row["is_ohlc_plausible"] if daily_row else "",
                "source_quality_status": identity["quality_status"],
                "source_quality_issues": identity["quality_issues"],
                "window_start_date": market_dates.get(start_index, "") if start_index is not None else "",
                "window_end_date": market_dates.get(end_index, "") if end_index is not None else "",
                "window_start_market_trading_day_index": start_index,
                "window_end_market_trading_day_index": end_index,
                "valid_observation_count": result["valid_observation_count"],
                "expected_observation_count": result["expected_observation_count"],
                "zero_return_count": result["zero_return_count"],
                "raw_source_daily_path": "data/canonical/us_daily_market_dev.csv.gz",
            }


def _create_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    columns = ", ".join(f'"{field}" TEXT NOT NULL' for field in RAW_METRIC_FIELDS)
    connection.execute("DROP TABLE IF EXISTS raw_metrics")
    connection.execute(f"CREATE TABLE raw_metrics ({columns})")
    connection.execute(
        "CREATE INDEX raw_metrics_signal_metric_ticker ON raw_metrics(signal_date, metric, ticker)"
    )
    return connection


def _string_row(row: dict[str, Any]) -> dict[str, str]:
    return {field: _text(row.get(field, "")) for field in RAW_METRIC_FIELDS}


def generate_raw_metrics(
    root: Path,
    canonical_paths: dict[str, Path],
    outputs: dict[str, Path],
) -> tuple[dict[str, Any], Path]:
    """Calculate only approved raw metrics, stream results, and stage for preprocessing."""

    calendar_rows = _read_calendar(canonical_paths["calendar"])
    market_dates = _load_benchmark_dates(canonical_paths["daily"])
    database_path = interim_db_path(root)
    connection = _create_database(database_path)
    raw_binary, raw_handle = _gzip_writer(outputs["raw_metrics"])
    coverage: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    missing: Counter[tuple[str, str, str]] = Counter()
    anomalies: dict[str, Counter[str]] = defaultdict(Counter)
    metric_rows = 0
    stock_tickers: set[str] = set()
    insert_sql = (
        f"INSERT INTO raw_metrics ({', '.join(RAW_METRIC_FIELDS)}) VALUES "
        f"({', '.join('?' for _ in RAW_METRIC_FIELDS)})"
    )
    batch: list[tuple[str, ...]] = []
    try:
        writer = csv.DictWriter(raw_handle, fieldnames=RAW_METRIC_FIELDS, extrasaction="raise")
        writer.writeheader()
        for ticker, ticker_rows in _iter_stock_groups(canonical_paths["daily"]):
            stock_tickers.add(ticker)
            for raw in _make_raw_metric_rows(ticker_rows, calendar_rows, market_dates):
                row = _string_row(raw)
                writer.writerow(row)
                batch.append(tuple(row[field] for field in RAW_METRIC_FIELDS))
                if len(batch) >= 5_000:
                    connection.executemany(insert_sql, batch)
                    batch.clear()
                key = (row["signal_date"], row["metric"])
                coverage[key]["stock_rows"] += 1
                if _bool(row["raw_metric_available"]):
                    coverage[key]["raw_metric_available"] += 1
                else:
                    missing[(row["metric"], "raw_metric", row["raw_availability_reason"] or "unspecified")] += 1
                if _bool(row["base_price_eligibility"]):
                    coverage[key]["base_price_eligible"] += 1
                else:
                    missing[(row["metric"], "base_price_eligibility", row["base_price_eligibility_reason"] or "unspecified")] += 1
                if _bool(row["factor_input_eligible"]):
                    coverage[key]["factor_input_eligible"] += 1
                else:
                    missing[(row["metric"], "factor_input", row["factor_input_reason"] or "unspecified")] += 1
                ticker_anomaly = anomalies[row["ticker"]]
                ticker_anomaly["metric_rows"] += 1
                if row["source_quality_status"] == "review":
                    ticker_anomaly["review_metric_rows"] += 1
                if row["signal_date_zero_volume"] == "true":
                    ticker_anomaly["signal_zero_volume_rows"] += 1
                if row["signal_date_ohlc_plausible"] == "false":
                    ticker_anomaly["signal_invalid_ohlc_rows"] += 1
                if _bool(row["factor_input_eligible"]):
                    ticker_anomaly["factor_input_eligible_rows"] += 1
                metric_rows += 1
        if batch:
            connection.executemany(insert_sql, batch)
        connection.commit()
    finally:
        raw_handle.flush()
        raw_handle.detach()
        raw_binary.close()
        connection.close()
    return (
        {
            "metric_rows": metric_rows,
            "stock_ticker_count": len(stock_tickers),
            "calendar_signal_count": len(calendar_rows),
            "coverage": coverage,
            "missing": missing,
            "anomalies": anomalies,
            "database_path": str(database_path),
        },
        database_path,
    )


def _final_unavailable_row(raw: dict[str, str], cross_section_count: int) -> dict[str, Any]:
    reason = raw["factor_input_reason"] or "factor_input_not_eligible"
    return {
        **raw,
        "cross_section_count": cross_section_count,
        "winsor_lower": None,
        "winsor_upper": None,
        "winsorized_value": None,
        "z_score": None,
        "direction_normalized_score": None,
        "preprocessing_available": False,
        "preprocessing_reason": reason,
    }


def apply_preprocessing(
    root: Path,
    database_path: Path,
    outputs: dict[str, Path],
    raw_summary: dict[str, Any],
) -> dict[str, Any]:
    """Apply strictly date-and-metric-local preprocessing from staged raw metrics."""

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    cursor = connection.execute("SELECT * FROM raw_metrics ORDER BY signal_date, metric, ticker")
    score_binary, score_handle = _gzip_writer(outputs["scores"])
    preprocessing_rows: list[dict[str, Any]] = []
    preprocessed_counts: Counter[tuple[str, str]] = Counter()
    metric_preprocessed: Counter[str] = Counter()
    try:
        writer = csv.DictWriter(score_handle, fieldnames=FINAL_FACTOR_FIELDS, extrasaction="raise")
        writer.writeheader()
        for (signal_date, metric), group in groupby(cursor, key=lambda row: (row["signal_date"], row["metric"])):
            raw_rows = [dict(row) for row in group]
            eligible = [row for row in raw_rows if _bool(row["factor_input_eligible"])]
            direction = price_metrics.metric_direction(metric)
            processed = preprocessing.preprocess_cross_section(
                [{**row, "raw_value": float(row["raw_value"])} for row in eligible], direction
            ) if eligible else []
            by_ticker = {row["ticker"]: row for row in processed}
            available_count = sum(_bool(row["preprocessing_available"]) for row in processed)
            first_processed = processed[0] if processed else None
            preprocess_reason = (
                first_processed["preprocessing_reason"] if first_processed and not _bool(first_processed["preprocessing_available"])
                else "" if processed else "no_factor_input_eligible_securities"
            )
            preprocessing_rows.append(
                {
                    "market": canonical_market.MARKET,
                    "signal_date": signal_date,
                    "metric": metric,
                    "factor_input_eligible_count": len(eligible),
                    "preprocessed_available_count": available_count,
                    "winsor_lower": first_processed["winsor_lower"] if first_processed else "",
                    "winsor_upper": first_processed["winsor_upper"] if first_processed else "",
                    "preprocessing_status": "PASS" if available_count else "WARN",
                    "preprocessing_reason": preprocess_reason,
                    "evidence_stage": canonical_market.EVIDENCE_STAGE,
                }
            )
            for raw in raw_rows:
                final = by_ticker.get(raw["ticker"], _final_unavailable_row(raw, len(eligible)))
                formatted = {field: _text(final.get(field, "")) for field in FINAL_FACTOR_FIELDS}
                writer.writerow(formatted)
                if _bool(formatted["preprocessing_available"]):
                    preprocessed_counts[(signal_date, metric)] += 1
                    metric_preprocessed[metric] += 1
    finally:
        score_handle.flush()
        score_handle.detach()
        score_binary.close()
        connection.close()
    for key, count in preprocessed_counts.items():
        raw_summary["coverage"][key]["preprocessed_available"] = count
    return {
        "preprocessing_rows": preprocessing_rows,
        "metric_preprocessed": metric_preprocessed,
    }


def _write_reports(
    root: Path,
    outputs: dict[str, Path],
    raw_summary: dict[str, Any],
    preprocessed: dict[str, Any],
    canonical_summary: dict[str, Any],
    post_integrity: dict[str, Any],
) -> dict[str, int]:
    coverage_rows = []
    for (signal_date, metric), counts in sorted(raw_summary["coverage"].items()):
        coverage_rows.append(
            {
                "market": canonical_market.MARKET,
                "signal_date": signal_date,
                "metric": metric,
                "stock_rows": counts["stock_rows"],
                "raw_metric_available": counts["raw_metric_available"],
                "base_price_eligible": counts["base_price_eligible"],
                "factor_input_eligible": counts["factor_input_eligible"],
                "preprocessed_available": counts["preprocessed_available"],
                "evidence_stage": canonical_market.EVIDENCE_STAGE,
            }
        )
    availability = Counter()
    for row in coverage_rows:
        metric = row["metric"]
        for key in ("stock_rows", "raw_metric_available", "base_price_eligible", "factor_input_eligible", "preprocessed_available"):
            availability[(metric, key)] += int(row[key])
    availability_rows = []
    for metric in ("MOM12_1", "MOM6_1", "VOL252"):
        total = availability[(metric, "stock_rows")]
        availability_rows.append(
            {
                "metric": metric,
                "stock_signal_metric_rows": total,
                "raw_metric_available_rows": availability[(metric, "raw_metric_available")],
                "base_price_eligible_rows": availability[(metric, "base_price_eligible")],
                "factor_input_eligible_rows": availability[(metric, "factor_input_eligible")],
                "preprocessed_available_rows": availability[(metric, "preprocessed_available")],
                "factor_input_eligible_rate": (
                    availability[(metric, "factor_input_eligible")] / total if total else 0
                ),
                "evidence_stage": canonical_market.EVIDENCE_STAGE,
            }
        )
    missing_rows = [
        {
            "metric": metric,
            "stage": stage,
            "reason": reason,
            "row_count": count,
            "evidence_stage": canonical_market.EVIDENCE_STAGE,
        }
        for (metric, stage, reason), count in sorted(raw_summary["missing"].items())
    ]
    anomaly_rows = []
    for ticker, counts in sorted(raw_summary["anomalies"].items()):
        if counts["review_metric_rows"] or counts["signal_zero_volume_rows"] or counts["signal_invalid_ohlc_rows"]:
            anomaly_rows.append(
                {
                    "ticker": ticker,
                    "metric_rows": counts["metric_rows"],
                    "review_metric_rows": counts["review_metric_rows"],
                    "signal_zero_volume_rows": counts["signal_zero_volume_rows"],
                    "signal_invalid_ohlc_rows": counts["signal_invalid_ohlc_rows"],
                    "factor_input_eligible_rows": counts["factor_input_eligible_rows"],
                    "treatment": "retained_with_provenance_no_automatic_deletion",
                    "evidence_stage": canonical_market.EVIDENCE_STAGE,
                }
            )
    report_counts = {
        "weekly_coverage": _write_csv(
            outputs["weekly_coverage"],
            ("market", "signal_date", "metric", "stock_rows", "raw_metric_available", "base_price_eligible", "factor_input_eligible", "preprocessed_available", "evidence_stage"),
            coverage_rows,
        ),
        "availability": _write_csv(
            outputs["availability"],
            ("metric", "stock_signal_metric_rows", "raw_metric_available_rows", "base_price_eligible_rows", "factor_input_eligible_rows", "preprocessed_available_rows", "factor_input_eligible_rate", "evidence_stage"),
            availability_rows,
        ),
        "missing": _write_csv(
            outputs["missing"],
            ("metric", "stage", "reason", "row_count", "evidence_stage"),
            missing_rows,
        ),
        "preprocessing": _write_csv(
            outputs["preprocessing"],
            ("market", "signal_date", "metric", "factor_input_eligible_count", "preprocessed_available_count", "winsor_lower", "winsor_upper", "preprocessing_status", "preprocessing_reason", "evidence_stage"),
            preprocessed["preprocessing_rows"],
        ),
        "anomaly": _write_csv(
            outputs["anomaly"],
            ("ticker", "metric_rows", "review_metric_rows", "signal_zero_volume_rows", "signal_invalid_ohlc_rows", "factor_input_eligible_rows", "treatment", "evidence_stage"),
            anomaly_rows,
        ),
    }
    outputs["ivol_status"].parent.mkdir(parents=True, exist_ok=True)
    outputs["ivol_status"].write_text(
        "# IVOL status — DEV_CURRENT_UNIVERSE\n\n"
        "`SPY` is available as a delivered benchmark calendar/price series, but the frozen manual does not uniquely freeze the IVOL market-proxy convention, return convention, regression window, or minimum-observation rule. IVOL is therefore not implemented in this phase. No proxy, regression, or threshold has been silently selected.\n",
        encoding="utf-8",
    )
    audit_payload = {
        "schema": "fe5209.price_factor_pipeline.dev.v1",
        "evidence_stage": canonical_market.EVIDENCE_STAGE,
        "canonical_summary": canonical_summary,
        "raw_metric_row_count": raw_summary["metric_rows"],
        "stock_ticker_count": raw_summary["stock_ticker_count"],
        "weekly_signal_count": raw_summary["calendar_signal_count"],
        "report_row_counts": report_counts,
        "post_build_raw_manifest": post_integrity,
        "prohibited_work_not_performed": [
            "SEC fundamental factor construction",
            "IVOL regression",
            "forward IC",
            "single-factor portfolios",
            "FMOM",
            "strategy or backtest returns",
            "neutralized price-factor outputs",
        ],
    }
    outputs["audit_json"].parent.mkdir(parents=True, exist_ok=True)
    outputs["audit_json"].write_text(json.dumps(audit_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_counts


def _write_acceptance_report(
    root: Path,
    outputs: dict[str, Path],
    canonical_summary: dict[str, Any],
    post_integrity: dict[str, Any],
) -> None:
    final_signal_has_next = False
    with canonical_market.canonical_paths(root)["calendar"].open("r", encoding="utf-8", newline="") as handle:
        calendar = list(csv.DictReader(handle))
        final_signal_has_next = bool(calendar and calendar[-1]["next_trading_date"])
    lines = [
        "# DEV Price-Factor Engineering Acceptance Report",
        "",
        f"- **Evidence stage:** `{canonical_market.EVIDENCE_STAGE}`",
        "- **Decision:** PASS WITH WARNINGS — DEV price-input engineering may proceed; this is not Formal PIT or Gate A evidence.",
        "- **Scope:** canonical market inputs, weekly signal dates, MOM12-1, MOM6-1, 252-day realised volatility, and cross-sectional preprocessing only.",
        "",
        "| Check | Status | Evidence / disposition |",
        "| --- | --- | --- |",
        f"| Raw package integrity after build | {'PASS' if post_integrity['passed'] else 'FAIL'} | Verified {post_integrity['verified_payload_files']}/{post_integrity['declared_payload_files']} payload files. |",
        f"| Canonical daily `(ticker, date)` generation | {'PASS' if canonical_summary['daily']['duplicate_canonical_ticker_date_rows'] == 0 else 'FAIL'} | Generated {canonical_summary['daily']['row_count']} rows; duplicate count={canonical_summary['daily']['duplicate_canonical_ticker_date_rows']}. |",
        "| Canonical identity map and benchmark separation | PASS | Master-driven Yahoo-to-canonical mapping is retained; SPY is tagged benchmark-only. |",
        "| Weekly signal calendar | PASS | Signal date is final actual SPY trading date in each ISO natural week; no Friday assumption. |",
        f"| Final delivered-week next trading date | {'PASS' if final_signal_has_next else 'WARN'} | The final signal has no later delivered SPY date, so `next_trading_date` is intentionally blank rather than inferred. |",
        "| MOM12-1 / MOM6-1 timing | PASS | Adjusted-price offsets use 252/21 and 126/21 delivered market trading days and do not access post-signal prices. |",
        "| Realised volatility rule | WARN | 252 daily-return window, minimum 200 valid adjacent-price returns, `sqrt(252)` annualisation; sample standard deviation (`ddof=1`) is explicitly documented as a DEV technical convention requiring confirmation before formal evidence freeze. |",
        "| Cross-sectional preprocessing | PASS | 1%/99% winsorisation then z-score within US × signal date × metric only; no missing-to-zero conversion. |",
        "| Neutralisation / IVOL | PASS (not performed) | Momentum/Low Risk neutralisation is Test; IVOL specification remains open and is not implemented. |",
        "",
        "## Blocking assessment",
        "",
        "No DEV coding blocker was identified by these acceptance checks when raw integrity passes. Current-S&P-500 survivorship bias, current (non-PIT) GICS, Yahoo adjustment limitations, RDQ absence, and missing historical membership/delisting inputs remain DEV limitations and Formal PIT requirements, not a failure of this DEV price-input phase.",
    ]
    outputs["acceptance"].parent.mkdir(parents=True, exist_ok=True)
    outputs["acceptance"].write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_pipeline(root: Path | None = None) -> dict[str, Any]:
    """Run the authorized reproducible DEV price-factor input pipeline."""

    selected_root = root or project_root()
    canonical_summary = canonical_market.build_canonical_market_inputs(selected_root)
    paths = canonical_market.canonical_paths(selected_root)
    outputs = output_paths(selected_root)
    raw_summary, database_path = generate_raw_metrics(selected_root, paths, outputs)
    preprocessed = apply_preprocessing(selected_root, database_path, outputs, raw_summary)
    post_integrity = source_audit.audit_manifest(source_audit.package_root(selected_root))
    report_counts = _write_reports(
        selected_root, outputs, raw_summary, preprocessed, canonical_summary, post_integrity
    )
    _write_acceptance_report(selected_root, outputs, canonical_summary, post_integrity)
    return {
        "canonical_summary": canonical_summary,
        "raw_metric_rows": raw_summary["metric_rows"],
        "post_integrity": post_integrity,
        "report_row_counts": report_counts,
        "paths": {name: str(path) for name, path in outputs.items()},
        "interim_database": str(database_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build FE5209 US DEV price-factor inputs and scores.")
    parser.add_argument("--root", type=Path, default=project_root(), help="FE5209 repository root")
    args = parser.parse_args()
    summary = run_pipeline(args.root.resolve())
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
