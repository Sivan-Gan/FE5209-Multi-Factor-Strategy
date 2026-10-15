#!/usr/bin/env python3
"""Normalize annual LSEG company-fundamentals workbooks for the SGX group project.

This script preserves every source value and explicitly keeps the dataset outside
PIT use until a verified announcement/available date is supplied.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import sys
import os
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import openpyxl


PROJECT = Path(__file__).resolve().parents[1]
RAW_ROOT = Path(os.environ.get(
    "LSEG_RAW_ROOT",
    "/Users/kuan/Desktop/fe5209_SG",
))
BASE_DATA = PROJECT / "data_v3"
OUT = Path(os.environ.get(
    "LSEG_OUTPUT_ROOT",
    str(PROJECT / "output_lseg_team_handoff"),
))
DATA_OUT = OUT / "data"
RAW_OUT = OUT / "raw_lseg_annual"
REPORT_OUT = OUT / "reports"

EXPECTED_UNRESOLVED = {
    "Delfi Limited.xlsx": "Source export resolves to a non-SGX company and contains one year only.",
    "Great Eastern Holdings Limited.xlsx": "Source export has a non-SGX/GBP match and omits cash-flow data.",
    "Singapore Airlines Limited.xlsx": "Source export has a non-SGX/THB match and contains one year only.",
    "CapitaLand Investment Limited.xlsx": "Source export resolves to a different issuer and contains one year only.",
}

ADDITIONAL_TICKERS = {
    "singinvestmentsfinancelimited": ("S35.SI", "financials"),
}

METRICS = {
    "revenue": ["Revenue from Business Activities - Total", "Revenue from Goods & Services"],
    "gross_profit": ["Gross Profit - Industrials/Property - Total", "Gross Profit"],
    "net_income": ["Net Income after Tax", "Net Income after Minority Interest", "Net Income"],
    "ebit": ["Earnings before Interest & Taxes (EBIT)"],
    "ebitda": ["Earnings before Interest, Taxes, Depreciation & Amortization (EBITDA)"],
    "total_assets": ["Total Assets"],
    "total_liabilities": ["Total Liabilities"],
    "common_equity": ["Common Equity - Total", "Common Equity Attributable to Parent Shareholders"],
    "cash": ["Cash & Cash Equivalents - Total", "Cash & Cash Equivalents"],
    "operating_cash_flow": ["Net Cash Flow from Operating Activities"],
    "capex": ["Capital Expenditures - Net - Cash Flow"],
    "shares_outstanding": ["Common Shares - Outstanding - Total"],
    "market_cap": ["Market Capitalization"],
    "roe": ["Return on Average Common Equity - % (Income available to Common excluding Extraordinary Items)"],
    "roa": ["Return on Average Total Assets - % (Income before Discontinued Operations & Extraordinary Items)"],
}


def canon(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def file_key(name: str) -> str:
    return canon(Path(name).stem)


def value(ws, row: int, col: int):
    return ws.cell(row, col).value if row and col else None


def iso_date(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else "")


def as_number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def find_label_row(ws, aliases: list[str]) -> int | None:
    aliases_lower = [a.lower() for a in aliases]
    exact = []
    fallback = []
    for row in range(1, ws.max_row + 1):
        label = ws.cell(row, 1).value
        if not isinstance(label, str):
            continue
        lower = label.lower().strip()
        if lower in aliases_lower:
            exact.append(row)
        elif any(alias in lower for alias in aliases_lower):
            fallback.append(row)
    # First exact nonempty row is safest. Some LSEG templates include headings
    # and blank duplicates before the populated standardised item.
    for row in exact + fallback:
        if any(as_number(ws.cell(row, col).value) is not None for col in range(2, ws.max_column + 1)):
            return row
    return (exact + fallback)[0] if (exact + fallback) else None


def source_instrument(company_name: str) -> str:
    match = re.search(r"\(([^()]*)\)\s*$", company_name or "")
    return match.group(1) if match else ""


def load_universe():
    rows = []
    with (BASE_DATA / "stock_universe_six_sectors.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(row)
    return rows


def match_universe(filename: str, universe):
    target = file_key(filename)
    if target in ADDITIONAL_TICKERS:
        ticker, sector = ADDITIONAL_TICKERS[target]
        return ticker, sector, 1.0, "manual_replacement"
    scored = []
    for row in universe:
        score = SequenceMatcher(None, target, canon(row["company_name"])).ratio()
        scored.append((score, row))
    score, row = max(scored, key=lambda item: item[0])
    status = "name_match" if score >= 0.86 else "manual_review_required"
    return row["ticker"], row["six_sector"], round(score, 4), status


def source_sheet_by_keywords(wb, words):
    for ws in wb.worksheets:
        name = ws.title.lower()
        if any(word in name for word in words):
            return ws
    return None


def primary_sheet(wb):
    for ws in wb.worksheets:
        if "summary" in ws.title.lower() or "匯總" in ws.title:
            return ws
    return wb.worksheets[0]


def get_metric_rows(wb):
    preferred = {
        "revenue": source_sheet_by_keywords(wb, ["income", "損益"]),
        "gross_profit": source_sheet_by_keywords(wb, ["income", "損益"]),
        "net_income": source_sheet_by_keywords(wb, ["income", "損益"]),
        "ebit": source_sheet_by_keywords(wb, ["income", "損益"]),
        "ebitda": source_sheet_by_keywords(wb, ["income", "損益"]),
        "total_assets": source_sheet_by_keywords(wb, ["balance", "資產負債"]),
        "total_liabilities": source_sheet_by_keywords(wb, ["balance", "資產負債"]),
        "common_equity": source_sheet_by_keywords(wb, ["balance", "資產負債"]),
        "cash": source_sheet_by_keywords(wb, ["balance", "資產負債"]),
        "operating_cash_flow": source_sheet_by_keywords(wb, ["cash flow", "現金流"]),
        "capex": source_sheet_by_keywords(wb, ["cash flow", "現金流"]),
        "shares_outstanding": primary_sheet(wb),
        "market_cap": primary_sheet(wb),
        "roe": primary_sheet(wb),
        "roa": primary_sheet(wb),
    }
    output = {}
    for metric, aliases in METRICS.items():
        candidates = [preferred.get(metric), primary_sheet(wb)] + list(wb.worksheets)
        seen = set()
        for ws in candidates:
            if ws is None or ws.title in seen:
                continue
            seen.add(ws.title)
            row = find_label_row(ws, aliases)
            if row:
                output[metric] = (ws, row)
                break
    return output


def write_csv(path: Path, rows: list[dict], headers: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    if not RAW_ROOT.exists():
        raise SystemExit(f"Raw LSEG folder not found: {RAW_ROOT}")
    if OUT.exists():
        shutil.rmtree(OUT)
    for folder in (DATA_OUT, RAW_OUT, REPORT_OUT):
        folder.mkdir(parents=True, exist_ok=True)

    universe = load_universe()
    files = sorted(RAW_ROOT.rglob("*.xlsx"))
    raw_rows, coverage, unresolved, processing_errors = [], [], [], []

    for path in files:
        sector_folder = path.parent.name
        filename = path.name
        ticker, sector, confidence, mapping_status = match_universe(filename, universe)
        # Keep the raw workbooks in the user's source folder during parsing.
        # They are copied into the final package in small batches after the
        # normalized outputs have been verified.
        explicit_problem = EXPECTED_UNRESOLVED.get(filename, "")
        try:
            # Normal mode is intentional here. In read-only mode, each random
            # ws.cell() access reparses the XML stream and turns 102 small files
            # into a many-minute operation.
            wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
            ws0 = primary_sheet(wb)
            source_company = str(value(ws0, 2, 2) or "")
            trbc_group = str(value(ws0, 5, 2) or "")
            template = str(value(ws0, 6, 2) or "")
            consolidation = str(value(ws0, 7, 2) or "")
            scale = str(value(ws0, 8, 2) or "")
            frequency = str(value(ws0, 9, 2) or "")
            currency = str(value(ws0, 14, 2) or "")
            sheets = [ws.title for ws in wb.worksheets]
            valid_structure = len(sheets) == 4
            status = "usable_annual_fundamentals" if valid_structure and not explicit_problem else "unresolved_or_incomplete"
            reason = "" if status == "usable_annual_fundamentals" else (explicit_problem or "Expected four source statements were not returned.")
            metric_rows = get_metric_rows(wb)
            years = []
            for col in range(2, ws0.max_column + 1):
                fiscal_year = value(ws0, 11, col)
                period_end = iso_date(value(ws0, 12, col))
                reported_date = iso_date(value(ws0, 13, col))
                if not fiscal_year and not period_end:
                    continue
                years.append((col, str(fiscal_year or ""), period_end, reported_date))
                record = {
                    "ticker": ticker,
                    "six_sector": sector,
                    "source_file": str(path.relative_to(RAW_ROOT)),
                    "source_company_name": source_company,
                    "source_instrument": source_instrument(source_company),
                    "source_trbc_group": trbc_group,
                    "source_template": template,
                    "consolidation_basis": consolidation,
                    "reporting_currency": currency,
                    "source_scale": scale,
                    "period_type": frequency,
                    "source_column": col,
                    "fiscal_year_label": str(fiscal_year or ""),
                    "fiscal_period_end": period_end,
                    "reported_date_source_field": reported_date,
                    "available_date": "",
                    "PIT_usable": "False",
                    "PIT_status": "Not PIT usable: actual announcement/available date has not been verified.",
                    "mapping_status": mapping_status,
                    "mapping_confidence": confidence,
                    "fundamentals_status": status,
                    "status_reason": reason,
                    "retrieval_date": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "source": "LSEG Workspace annual company fundamentals export",
                }
                for metric, location in metric_rows.items():
                    mws, row = location
                    record[metric] = as_number(value(mws, row, col))
                raw_rows.append(record)
            coverage.append({
                "ticker": ticker,
                "six_sector": sector,
                "source_file": str(path.relative_to(RAW_ROOT)),
                "source_company_name": source_company,
                "source_instrument": source_instrument(source_company),
                "reporting_currency": currency,
                "source_template": template,
                "statement_sheets_found": len(sheets),
                "sheet_names": " | ".join(sheets),
                "first_fiscal_year": years[-1][1] if years else "",
                "last_fiscal_year": years[0][1] if years else "",
                "annual_observation_columns": len(years),
                "mapping_status": mapping_status,
                "mapping_confidence": confidence,
                "fundamentals_status": status,
                "status_reason": reason,
                "PIT_usable": "False",
                "PIT_status": "Actual announcement/available date not verified in source export.",
            })
            if status != "usable_annual_fundamentals":
                unresolved.append({"source_file": str(path.relative_to(RAW_ROOT)), "ticker": ticker, "reason": reason, "recommended_treatment": "Retain price data; exclude from Value/Quality panel until a verified export is supplied."})
        except Exception as exc:
            processing_errors.append({"source_file": str(path.relative_to(RAW_ROOT)), "error": repr(exc)})
            unresolved.append({"source_file": str(path.relative_to(RAW_ROOT)), "ticker": ticker, "reason": "Workbook could not be parsed.", "recommended_treatment": "Inspect source workbook manually."})

    metric_headers = list(METRICS)
    raw_headers = [
        "ticker", "six_sector", "source_file", "source_company_name", "source_instrument", "source_trbc_group",
        "source_template", "consolidation_basis", "reporting_currency", "source_scale", "period_type", "source_column",
        "fiscal_year_label", "fiscal_period_end", "reported_date_source_field", "available_date", "PIT_usable", "PIT_status",
        "mapping_status", "mapping_confidence", "fundamentals_status", "status_reason", "retrieval_date", "source",
    ] + metric_headers
    write_csv(DATA_OUT / "fundamentals_lseg_raw.csv", raw_rows, raw_headers)

    # Build the usable normalized panel. Duplicates are retained in raw; only one
    # observation per issuer/fiscal period enters the convenience panel.
    panel_rows, seen = [], set()
    for row in raw_rows:
        key = (row["ticker"], row["fiscal_period_end"])
        if row["fundamentals_status"] != "usable_annual_fundamentals" or not row["fiscal_period_end"] or key in seen:
            continue
        seen.add(key)
        panel_rows.append(row)
    write_csv(DATA_OUT / "fundamentals_lseg_panel_unverified_pit.csv", panel_rows, raw_headers)

    coverage_headers = list(coverage[0]) if coverage else []
    unresolved_headers = ["source_file", "ticker", "reason", "recommended_treatment"]
    write_csv(REPORT_OUT / "fundamental_coverage_lseg.csv", coverage, coverage_headers)
    write_csv(REPORT_OUT / "unresolved_lseg_exports.csv", unresolved, unresolved_headers)
    write_csv(REPORT_OUT / "processing_errors.csv", processing_errors, ["source_file", "error"])

    # Existing price-side inputs remain source data; copy them unchanged.
    for name in ["prices_daily_2006.csv", "stock_universe_six_sectors.csv", "corporate_actions_2006.csv", "price_coverage_by_ticker.csv", "price_validation_2006.json"]:
        shutil.copy2(BASE_DATA / name, DATA_OUT / name)

    usable = sum(1 for row in coverage if row["fundamentals_status"] == "usable_annual_fundamentals")
    manifest = {
        "build_timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "raw_workbooks_found": len(files),
        "usable_fundamental_workbooks": usable,
        "unresolved_or_incomplete_workbooks": len(unresolved),
        "raw_annual_observations": len(raw_rows),
        "usable_panel_observations": len(panel_rows),
        "sector_counts": dict(Counter(row["six_sector"] for row in coverage)),
        "currency_counts": dict(Counter(row["reporting_currency"] for row in coverage)),
        "PIT_status": "Not complete. Actual announcement/available dates are not verified by this annual export.",
    }
    (REPORT_OUT / "build_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    readme = f"""# SGX LSEG fundamentals team handoff

## Scope

This package combines the existing daily SGX price dataset with {len(files)} annual LSEG company-fundamentals source workbooks. The source workbooks are retained unchanged under `raw_lseg_annual/`.

## Key files

- `data/prices_daily_2006.csv`: daily OHLC, adjusted close, volume and derived trading-value fields from the earlier SGX price pipeline.
- `data/stock_universe_six_sectors.csv`: original universe and six-sector mapping.
- `data/corporate_actions_2006.csv`: price-side corporate-action reference data.
- `data/fundamentals_lseg_raw.csv`: one row per source workbook column. Raw duplicates are retained for audit.
- `data/fundamentals_lseg_panel_unverified_pit.csv`: one annual record per usable company/fiscal-period end, suitable for cleaning and factor research only after PIT dates are supplied.
- `reports/fundamental_coverage_lseg.csv`: per-company coverage and source currency.
- `reports/unresolved_lseg_exports.csv`: exports not admitted into the usable fundamental panel.

## Important limitations

1. Annual accounting values are in their original reporting currency and source scale (usually millions). Do not compare currency-denominated values across companies until conversion is explicitly performed.
2. `reported_date_source_field` is retained exactly as exported. It is **not** asserted to be an actual public-announcement timestamp. `available_date` is blank and `PIT_usable=False` for every record.
3. Four exports are marked unresolved/incomplete rather than silently substituted or deleted. Their price data remains available for price-only factors.
4. Companies with shorter history were listed later or have limited provider coverage. Their absent pre-listing years are missing, not zero.
5. Financial institutions, REITs, and non-SGD reporters retain source-specific accounting definitions. Use sector-aware factor definitions.

## Current counts

- Raw LSEG workbooks: {len(files)}
- Usable annual source workbooks: {usable}
- Unresolved/incomplete exports: {len(unresolved)}
- Raw annual source observations: {len(raw_rows)}
- Deduplicated usable annual panel observations: {len(panel_rows)}
"""
    (OUT / "README.md").write_text(readme, encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
