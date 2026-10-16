"""Export a transparent weekly SG market-cap proxy for research.

There is no licensed historical weekly market-cap field in the locally stored
data. This script therefore creates a *proxy*, not a vendor market-cap series:
week-end close × the latest annual shares-outstanding observation that is at
least 180 calendar days old. The lag prevents using fiscal-year-end shares
before a conservative reporting-availability proxy date, but it is not a
substitute for verified filing timestamps.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = ROOT.parent / "SGX_factor_research" / "output_lseg_rebuilt_for_sg_strategy" / "data"
OUT = ROOT / "private_exports" / "sg_weekly_market_cap_proxy_private"
START_DATE = pd.Timestamp("2013-01-01")
ANNOUNCEMENT_LAG_DAYS = 180


def fiscal_year(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.extract(r"(\d{4})", expand=False), errors="coerce")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    prices = pd.read_csv(DATA_ROOT / "prices_daily_2006.csv", usecols=["date", "ticker", "close"])
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
    prices = prices.dropna(subset=["date", "ticker", "close"])
    prices = prices[(prices["date"] >= START_DATE) & prices["close"].gt(0)].copy()

    # One common market signal date per Friday-ended week: latest observed
    # market date in that week. It is intentionally comparable with a weekly
    # strategy calendar rather than each ticker using a different stale date.
    prices["week"] = prices["date"].dt.to_period("W-FRI")
    daily_coverage = prices.groupby("date", as_index=False).agg(n_tickers=("ticker", "nunique"))
    daily_coverage = daily_coverage.sort_values("date").reset_index(drop=True)
    trailing_median = daily_coverage["n_tickers"].rolling(60, min_periods=20).median().shift(1)
    daily_coverage["calendar_eligible"] = daily_coverage["n_tickers"].ge(20) & (
        trailing_median.isna() | daily_coverage["n_tickers"].ge(0.8 * trailing_median)
    )
    daily_coverage = daily_coverage[daily_coverage["calendar_eligible"]].copy()
    daily_coverage["week"] = daily_coverage["date"].dt.to_period("W-FRI")
    signal_dates = daily_coverage.sort_values("date").groupby("week", as_index=False).tail(1)[["week", "date"]]
    signal_dates = signal_dates.rename(columns={"date": "signal_date"})
    weekly_prices = prices.merge(signal_dates, on="week", how="inner")
    weekly_prices = weekly_prices[weekly_prices["date"].eq(weekly_prices["signal_date"])].copy()
    weekly_prices = weekly_prices.rename(columns={"date": "source_date", "close": "close"})

    fundamentals = pd.read_csv(
        DATA_ROOT / "fundamentals_lseg_panel_unverified_pit.csv",
        usecols=["ticker", "fiscal_year_label", "fiscal_period_end", "shares_outstanding", "source_scale", "reporting_currency"],
    )
    fundamentals["fiscal_year"] = fiscal_year(fundamentals["fiscal_year_label"])
    fundamentals["fiscal_period_end"] = pd.to_datetime(fundamentals["fiscal_period_end"], errors="coerce")
    fundamentals["shares_outstanding"] = pd.to_numeric(fundamentals["shares_outstanding"], errors="coerce")
    fundamentals = fundamentals.dropna(subset=["ticker", "fiscal_year", "fiscal_period_end", "shares_outstanding"])
    fundamentals = fundamentals[fundamentals["shares_outstanding"].gt(0)].copy()
    fundamentals = fundamentals.sort_values(["ticker", "fiscal_period_end"]).drop_duplicates(
        ["ticker", "fiscal_period_end"], keep="last"
    )
    fundamentals["shares_available_proxy_date"] = fundamentals["fiscal_period_end"] + pd.Timedelta(days=ANNOUNCEMENT_LAG_DAYS)

    outputs: list[pd.DataFrame] = []
    for ticker, prices_ticker in weekly_prices.groupby("ticker", sort=False):
        shares_ticker = fundamentals[fundamentals["ticker"].eq(ticker)].sort_values("shares_available_proxy_date")
        if shares_ticker.empty:
            continue
        left = prices_ticker.sort_values("signal_date")
        right = shares_ticker.sort_values("shares_available_proxy_date")
        merged = pd.merge_asof(
            left,
            right,
            left_on="signal_date",
            right_on="shares_available_proxy_date",
            direction="backward",
            suffixes=("", "_shares"),
        )
        outputs.append(merged)

    panel = pd.concat(outputs, ignore_index=True) if outputs else pd.DataFrame()
    # All source templates state shares in millions. Convert to full shares and
    # full local listing-currency units for an unambiguous market-cap column.
    panel["shares_outstanding_full"] = panel["shares_outstanding"] * 1_000_000
    panel["market_cap"] = panel["close"] * panel["shares_outstanding_full"]
    panel["currency"] = "SGX listing-price currency; verify per instrument before cross-currency aggregation"
    panel["source_field"] = "derived: weekly close x annual shares_outstanding (annual share observation lagged 180 calendar days)"
    panel["PIT_status"] = "proxy_only: 180-day availability lag, not verified actual announcement timestamp"
    panel["shares_scale"] = "Millions in LSEG annual export; converted to full shares"
    panel["market_cap_status"] = "derived_weekly_market_cap_proxy"

    output_columns = [
        "ticker", "signal_date", "market_cap", "currency", "source_date", "source_field",
        "shares_outstanding", "shares_outstanding_full", "shares_scale", "fiscal_year",
        "fiscal_period_end", "shares_available_proxy_date", "reporting_currency", "close",
        "PIT_status", "market_cap_status",
    ]
    panel = panel[output_columns].sort_values(["signal_date", "ticker"])
    panel.to_csv(OUT / "sg_weekly_market_cap_history.csv", index=False)

    coverage = pd.DataFrame([
        {
            "start_date": panel["signal_date"].min().date().isoformat() if not panel.empty else "",
            "end_date": panel["signal_date"].max().date().isoformat() if not panel.empty else "",
            "weekly_signal_dates": panel["signal_date"].nunique(),
            "price_tickers": prices["ticker"].nunique(),
            "tickers_with_weekly_market_cap_proxy": panel["ticker"].nunique(),
            "rows": len(panel),
            "known_limitation": "Only companies with both price history and annual shares-outstanding source data can appear. Historical delisted securities are not added.",
        }
    ])
    coverage.to_csv(OUT / "coverage_report.csv", index=False)

    (OUT / "README.md").write_text(
        """# SG weekly market-cap proxy (private research handoff)

`sg_weekly_market_cap_history.csv` provides one common weekly signal date and
one value per ticker where data exist.

## Construction

`market_cap = close on signal_date × annual shares_outstanding × 1,000,000`.
The latest annual share count is usable only from fiscal-period-end + 180 days.
This is a conservative PIT **proxy**, not a verified LSEG point-in-time market
capitalisation history. Use `shares_available_proxy_date` and `PIT_status` in
any research audit.

## Currency

The prices are from the SGX listing-price feed. `market_cap` is consequently
in the listing-price currency. It must not be aggregated cross-sectionally with
non-SGD listings before verifying / converting currency. The source reporting
currency is retained separately.

## Scope

The panel covers the current price universe only where annual shares data are
available. It does not resolve survivorship bias or add historical delistings.
""",
        encoding="utf-8",
    )
    archive = shutil.make_archive(str(OUT), "zip", root_dir=OUT)
    print(archive)
    print("rows", len(panel), "weeks", panel["signal_date"].nunique(), "tickers", panel["ticker"].nunique())


if __name__ == "__main__":
    main()
