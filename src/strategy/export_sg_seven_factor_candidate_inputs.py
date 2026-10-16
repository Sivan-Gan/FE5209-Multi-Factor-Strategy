"""Export private SG candidate inputs for Size, Value and Dividend research.

The existing SG stock-factor panel contains only the four factors that were
implemented in the independent pipeline (Quality, Momentum, LowRisk and
Investment).  This exporter deliberately does *not* turn the LSEG annual
fundamental observations into historical PIT scores: their announcement dates
were not verified.  Instead it provides the raw annual inputs and transparent
candidate ratios for a team member to inspect or enrich with a PIT availability
date before any backtest use.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = ROOT.parent / "SGX_factor_research" / "output_lseg_rebuilt_for_sg_strategy" / "data"
OUT = ROOT / "private_exports" / "sg_seven_factor_candidate_inputs_private"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    fundamentals = pd.read_csv(DATA_ROOT / "fundamentals_lseg_panel_unverified_pit.csv")
    # Workspace exports can label the same field as `2025` or `2025年`.
    fundamentals["fiscal_year"] = pd.to_numeric(
        fundamentals["fiscal_year_label"].astype(str).str.extract(r"(\d{4})", expand=False),
        errors="coerce",
    )
    fundamentals = fundamentals[fundamentals["fiscal_year"].notna()].copy()
    fundamentals["fiscal_year"] = fundamentals["fiscal_year"].astype(int)

    # Candidate ratios are deliberately annual/as-reported candidates, not PIT scores.
    market_cap = pd.to_numeric(fundamentals["market_cap"], errors="coerce")
    equity = pd.to_numeric(fundamentals["common_equity"], errors="coerce")
    earnings = pd.to_numeric(fundamentals["net_income"], errors="coerce")
    ocf = pd.to_numeric(fundamentals["operating_cash_flow"], errors="coerce")
    capex = pd.to_numeric(fundamentals["capex"], errors="coerce")
    fundamentals["size_log_market_cap_candidate"] = market_cap.where(market_cap > 0).map(lambda x: __import__("math").log(x))
    fundamentals["value_book_to_market_candidate"] = equity / market_cap.where(market_cap > 0)
    fundamentals["value_earnings_to_price_candidate"] = earnings / market_cap.where(market_cap > 0)
    fundamentals["value_fcf_to_price_candidate"] = (ocf - capex) / market_cap.where(market_cap > 0)
    fundamentals["candidate_input_status"] = "annual_as_reported_not_PIT"

    keep = [
        "ticker", "six_sector", "source_company_name", "source_instrument",
        "reporting_currency", "source_scale", "fiscal_year", "fiscal_period_end",
        "available_date", "PIT_usable", "PIT_status", "retrieval_date", "source",
        "revenue", "gross_profit", "net_income", "ebit", "ebitda", "total_assets",
        "total_liabilities", "common_equity", "cash", "operating_cash_flow", "capex",
        "shares_outstanding", "market_cap", "roe", "roa",
        "size_log_market_cap_candidate", "value_book_to_market_candidate",
        "value_earnings_to_price_candidate", "value_fcf_to_price_candidate",
        "candidate_input_status",
    ]
    annual = fundamentals[keep].sort_values(["ticker", "fiscal_year"])
    annual.to_csv(OUT / "annual_size_value_candidate_inputs.csv", index=False)

    actions = pd.read_csv(DATA_ROOT / "corporate_actions_2006.csv")
    actions["date"] = pd.to_datetime(actions["date"], errors="coerce")
    dividends = actions[(actions["action_type"] == "cash_dividend") & actions["dividend"].notna()].copy()
    dividends["calendar_year"] = dividends["date"].dt.year
    annual_dividends = (
        dividends.groupby(["ticker", "calendar_year"], as_index=False)
        .agg(
            cash_dividend_per_share_sum=("dividend", "sum"),
            dividend_event_count=("dividend", "size"),
            first_ex_or_event_date=("date", "min"),
            last_ex_or_event_date=("date", "max"),
        )
        .sort_values(["ticker", "calendar_year"])
    )
    annual_dividends["source"] = "Yahoo Finance chart endpoint corporate_actions_2006.csv"
    annual_dividends["candidate_input_status"] = "cash_dividend_events_not_verified_PIT"
    annual_dividends.to_csv(OUT / "annual_dividend_candidate_inputs.csv", index=False)

    metadata = pd.DataFrame([
        {
            "file": "annual_size_value_candidate_inputs.csv",
            "purpose": "Annual historical candidate inputs for Size and Value research.",
            "do_not_assume": "PIT usable; available_date is not verified for these observations.",
            "formulas": "ln(market_cap); common_equity/market_cap; net_income/market_cap; (operating_cash_flow-capex)/market_cap.",
        },
        {
            "file": "annual_dividend_candidate_inputs.csv",
            "purpose": "Calendar-year sum of cash dividend events per share.",
            "do_not_assume": "Special-dividend treatment, tax convention, or audited/PIT dividend availability.",
            "formulas": "cash_dividend_per_share_sum is the sum of recorded cash-dividend event amounts.",
        },
    ])
    metadata.to_csv(OUT / "README_data_dictionary.csv", index=False)

    readme = """# SG seven-factor candidate inputs (private handoff)

This handoff fills the *input-data* gap for exploratory Size, Value and
Dividend research. It does **not** claim that the three factors have been
validated or that the fields are point-in-time (PIT) safe.

## Contents

* `annual_size_value_candidate_inputs.csv`: LSEG Workspace annual/as-reported
  fundamentals plus market-capitalisation, shares outstanding and transparent
  candidate ratios.
* `annual_dividend_candidate_inputs.csv`: annual aggregation of recorded cash
  dividend events from the existing Yahoo corporate-actions layer.
* `README_data_dictionary.csv`: compact field-level cautions.

## Required handling before formal backtest

1. Preserve `ticker`, currency and source scale; never compare raw values
   across currencies without conversion or within-company ratios.
2. Map each annual observation to a verified report announcement/availability
   date, then apply only observations public before each signal date.
3. Screen non-positive market capitalisation and negative equity before ratios.
4. Verify dividend event type (ordinary vs special) and ex-date / availability
   treatment before constructing dividend yield.

The current published SG weekly panel remains a four-factor panel:
Quality, Momentum, LowRisk and Investment.  These files are the appropriate
candidate inputs if the team wants to extend it to a seven-factor research
branch without hiding the PIT limitation.
"""
    (OUT / "README.md").write_text(readme, encoding="utf-8")

    archive = shutil.make_archive(str(OUT), "zip", root_dir=OUT)
    print(archive)
    print("annual rows", len(annual), "tickers", annual["ticker"].nunique())
    print("market cap available", int(annual["market_cap"].notna().sum()))
    print("dividend ticker-years", len(annual_dividends), "tickers", annual_dividends["ticker"].nunique())


if __name__ == "__main__":
    main()
