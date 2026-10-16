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

    # Some annual Workspace templates do not carry the Market Capitalization
    # row, notably several large banks and REITs.  For SGD-reporting companies
    # only, a transparent fallback can be built from the last observed SGX
    # close on/before fiscal period end times annual shares outstanding.  We
    # keep the source label rather than silently overwriting missing values.
    prices = pd.read_csv(DATA_ROOT / "prices_daily_2006.csv", usecols=["date", "ticker", "close"])
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
    prices = prices.dropna(subset=["date", "close"]).sort_values(["ticker", "date"])
    fundamentals["fiscal_period_end_dt"] = pd.to_datetime(fundamentals["fiscal_period_end"], errors="coerce")
    close_records: list[dict[str, object]] = []
    for ticker, observations in fundamentals.groupby("ticker", sort=False):
        history = prices[prices["ticker"].eq(ticker)].sort_values("date")
        if history.empty:
            continue
        for idx, observation in observations.iterrows():
            period_end = observation["fiscal_period_end_dt"]
            if pd.isna(period_end):
                continue
            prior = history[history["date"].le(period_end)]
            if not prior.empty:
                last = prior.iloc[-1]
                close_records.append({"_row": idx, "price_date_for_derived_market_cap": last["date"], "close_for_derived_market_cap": last["close"]})
    closes = pd.DataFrame(close_records).set_index("_row") if close_records else pd.DataFrame()
    fundamentals["price_date_for_derived_market_cap"] = closes.get("price_date_for_derived_market_cap")
    fundamentals["close_for_derived_market_cap"] = closes.get("close_for_derived_market_cap")

    # Candidate ratios are deliberately annual/as-reported candidates, not PIT scores.
    market_cap = pd.to_numeric(fundamentals["market_cap"], errors="coerce")
    equity = pd.to_numeric(fundamentals["common_equity"], errors="coerce")
    earnings = pd.to_numeric(fundamentals["net_income"], errors="coerce")
    ocf = pd.to_numeric(fundamentals["operating_cash_flow"], errors="coerce")
    capex = pd.to_numeric(fundamentals["capex"], errors="coerce")
    shares = pd.to_numeric(fundamentals["shares_outstanding"], errors="coerce")
    derived_market_cap = fundamentals["close_for_derived_market_cap"] * shares
    fallback_ok = fundamentals["reporting_currency"].eq("SGD") & derived_market_cap.gt(0)
    fundamentals["market_cap_derived_from_close_x_shares"] = derived_market_cap.where(fallback_ok)
    fundamentals["market_cap_candidate"] = market_cap.where(market_cap > 0).combine_first(
        fundamentals["market_cap_derived_from_close_x_shares"]
    )
    fundamentals["market_cap_candidate_source"] = "missing"
    fundamentals.loc[market_cap.gt(0), "market_cap_candidate_source"] = "LSEG annual market_cap field"
    fundamentals.loc[market_cap.isna() & fallback_ok, "market_cap_candidate_source"] = "derived: last close on/before fiscal period end x annual shares outstanding"
    candidate_market_cap = fundamentals["market_cap_candidate"].where(fundamentals["market_cap_candidate"].gt(0))
    fundamentals["size_log_market_cap_candidate"] = candidate_market_cap.map(lambda x: __import__("math").log(x))
    fundamentals["value_book_to_market_candidate"] = equity / candidate_market_cap
    fundamentals["value_earnings_to_price_candidate"] = earnings / candidate_market_cap
    fundamentals["value_fcf_to_price_candidate"] = (ocf - capex) / candidate_market_cap
    fundamentals["candidate_input_status"] = "annual_as_reported_not_PIT"

    keep = [
        "ticker", "six_sector", "source_company_name", "source_instrument",
        "reporting_currency", "source_scale", "fiscal_year", "fiscal_period_end",
        "available_date", "PIT_usable", "PIT_status", "retrieval_date", "source",
        "revenue", "gross_profit", "net_income", "ebit", "ebitda", "total_assets",
        "total_liabilities", "common_equity", "cash", "operating_cash_flow", "capex",
        "shares_outstanding", "market_cap", "price_date_for_derived_market_cap",
        "close_for_derived_market_cap", "market_cap_derived_from_close_x_shares",
        "market_cap_candidate", "market_cap_candidate_source", "roe", "roa",
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
            "formulas": "ln(market_cap_candidate); common_equity/market_cap_candidate; net_income/market_cap_candidate; (operating_cash_flow-capex)/market_cap_candidate. Missing LSEG market cap is only derived for SGD-reporting companies as close × annual shares.",
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
  candidate ratios.  If the LSEG template lacks market cap, SGD-reporting
  companies receive a clearly labelled fallback: last close on/before fiscal
  period end × annual shares outstanding.
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
    print("LSEG market cap available", int(annual["market_cap"].notna().sum()))
    print("candidate market cap available", int(annual["market_cap_candidate"].notna().sum()))
    print("dividend ticker-years", len(annual_dividends), "tickers", annual_dividends["ticker"].nunique())


if __name__ == "__main__":
    main()
