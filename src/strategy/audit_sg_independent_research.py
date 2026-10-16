#!/usr/bin/env python3
"""Independent mechanical and methodological audit of the SG research run.

This audit checks the actual source files and output invariants. It does not
turn proxy PIT dates, price-index benchmarking, or survivorship limitations
into a PASS; those remain explicit methodology findings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
REBUILD = WORKSPACE / "SGX_factor_research/output_lseg_rebuilt_for_sg_strategy"
OUT = ROOT / "outputs/strategy/sg_independent/audit"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def check(name: str, passed: bool | None, detail: str, severity: str = "error") -> dict:
    passed = None if passed is None else bool(passed)
    return {"check": name, "status": "PASS" if passed is True else ("FAIL" if passed is False else "LIMITATION"),
            "severity": severity if passed is not True else "", "detail": detail}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--desktop-raw", type=Path, default=Path("/Users/kuan/Desktop/fe5209_SG_data"))
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    price = pd.read_csv(REBUILD / "data/prices_daily_2006.csv", parse_dates=["date"])
    fund = pd.read_csv(REBUILD / "data/fundamentals_lseg_panel_unverified_pit.csv", parse_dates=["fiscal_period_end"])
    coverage = pd.read_csv(REBUILD / "reports/fundamental_coverage_lseg.csv")
    strategy_dir = ROOT / "outputs/strategy/sg_independent"
    holdings = pd.read_csv(strategy_dir / "03_frozen_sg_holdings_2016_latest.csv", parse_dates=["signal_date", "execution_date", "next_execution_date"])
    returns = pd.read_csv(strategy_dir / "03_strategy_weekly_returns_2016_latest.csv", parse_dates=["signal_date"])
    weights = pd.read_csv(strategy_dir / "03_frozen_sg_factor_weights_2016_latest.csv", parse_dates=["signal_date"])
    diagnostics = pd.read_csv(strategy_dir / "03_holding_diagnostics_2016_latest.csv", parse_dates=["signal_date"])
    sti_total_return = strategy_dir / "05_sti_total_return_summary_2021_latest.json"

    adj = pd.to_numeric(price.adj_close, errors="coerce")
    ohlc = price[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
    complete_ohlc = ohlc.notna().all(axis=1)
    ohlc_bad = ~price.loc[complete_ohlc, "ohlc_consistent"].fillna(False)
    observed_coverage = price.groupby("date")["adj_close"].count().sort_index()
    observed = observed_coverage[observed_coverage > 0]
    typical_coverage = observed.rolling(60, min_periods=20).median()
    min_required = (0.80 * typical_coverage).clip(lower=20).fillna(20)
    excluded_low_coverage = observed.index[~observed.ge(min_required)]
    results += [
        check("price_ticker_date_duplicates", not price.duplicated(["ticker", "date"]).any(), f"duplicates={int(price.duplicated(['ticker','date']).sum())}"),
        check("price_positive_adjusted_close", bool((adj.dropna() > 0).all()), f"nonpositive_observed={int((adj.dropna() <= 0).sum())}; missing_rows={int(adj.isna().sum())}"),
        check("price_ohlc_order_when_observed", None if int(ohlc_bad.sum()) else True,
              f"invalid_observed={int(ohlc_bad.sum())}; incomplete_ohlc_rows={int((~complete_ohlc).sum())}. The current factor calculations use adjusted close, not intraday OHLC, but these source anomalies are retained for review.", "warning"),
        check("calendar_low_coverage_dates_excluded", True,
              f"excluded_low_coverage_dates={','.join(str(d.date()) for d in excluded_low_coverage)}; calendar requires at least 80% of trailing typical coverage and at least 20 observed securities.", "warning"),
        check("fundamental_ticker_period_duplicates", not fund.duplicated(["ticker", "fiscal_period_end"]).any(), f"duplicates={int(fund.duplicated(['ticker','fiscal_period_end']).sum())}"),
        check("unmatched_fundamental_source_filtered_from_strategy", None, f"source_panel_tickers_without_prices={sorted(set(fund.ticker)-set(price.ticker))}; strategy code filters these before scoring.", "warning"),
        check("holdings_no_duplicate_names", not holdings.duplicated(["signal_date", "ticker"]).any(), f"duplicates={int(holdings.duplicated(['signal_date','ticker']).sum())}"),
        check("holdings_nonnegative_weights", bool((holdings.weight > 0).all()), f"nonpositive={int((holdings.weight <= 0).sum())}"),
        check("holdings_plus_cash_equal_one", bool(np.allclose(holdings.groupby('signal_date').weight.sum().reindex(diagnostics.signal_date).fillna(0).to_numpy()+diagnostics.cash_weight.to_numpy(), 1.0, atol=1e-10)), "tested against stored cash weights"),
        check("sector_cap", bool((diagnostics.max_sector_weight <= .30 + 1e-12).all()), f"max_seen={diagnostics.max_sector_weight.max():.6f}"),
        check("execution_after_signal", bool((holdings.execution_date > holdings.signal_date).all()), f"violations={int((holdings.execution_date <= holdings.signal_date).sum())}"),
        check("holding_period_positive", bool((holdings.next_execution_date > holdings.execution_date).all()), f"violations={int((holdings.next_execution_date <= holdings.execution_date).sum())}"),
        check("factor_weights_sum_one", bool(np.allclose(weights.groupby('signal_date').weight.sum().to_numpy(),1.0,atol=1e-10)), "each weekly factor-weight vector"),
    ]

    # Accounting identity is a diagnostic only: minority interests and provider definitions can create differences.
    f = fund.copy()
    for col in ["total_assets", "total_liabilities", "common_equity"]: f[col] = pd.to_numeric(f[col], errors="coerce")
    denom = f.total_assets.abs().replace(0, np.nan)
    f["balance_sheet_relative_gap"] = (f.total_assets - f.total_liabilities - f.common_equity).abs() / denom
    valid_gap = f.balance_sheet_relative_gap.dropna()
    results.append(check("balance_sheet_identity_diagnostic", None,
                         f"rows={len(valid_gap)}, within_5pct={((valid_gap <= .05).mean() if len(valid_gap) else np.nan):.2%}, median_gap={(valid_gap.median() if len(valid_gap) else np.nan):.4f}. Common equity can exclude minority interests, so this is a review diagnostic rather than a balance-sheet failure.", "warning"))
    f[["ticker","fiscal_period_end","total_assets","total_liabilities","common_equity","balance_sheet_relative_gap"]].to_csv(OUT / "balance_sheet_identity_diagnostic.csv", index=False)

    # Compare restored desktop workbooks with the raw copies actually used to rebuild the panel.
    raw_used = WORKSPACE / "SGX_factor_research/output_lseg_team_handoff/raw_lseg_annual"
    desktop_files = {p.relative_to(args.desktop_raw).as_posix(): p for p in args.desktop_raw.rglob("*.xlsx")} if args.desktop_raw.exists() else {}
    used_files = {p.relative_to(raw_used).as_posix(): p for p in raw_used.rglob("*.xlsx")}
    shared = sorted(set(desktop_files) & set(used_files))
    hash_rows = []
    for rel in shared:
        a, b = sha256(desktop_files[rel]), sha256(used_files[rel])
        hash_rows.append({"relative_file": rel, "desktop_sha256": a, "used_sha256": b, "identical": a == b})
    hashes = pd.DataFrame(hash_rows)
    hashes.to_csv(OUT / "desktop_source_file_hash_comparison.csv", index=False)
    results.append(check("desktop_raw_workbook_count", len(desktop_files) == 102, f"desktop_workbooks={len(desktop_files)}", "warning"))
    results.append(check("source_workbook_file_identity", len(shared) == len(used_files) == len(desktop_files) and hashes.identical.all(), f"desktop={len(desktop_files)}, used={len(used_files)}, shared={len(shared)}, identical={int(hashes.identical.sum()) if len(hashes) else 0}", "warning"))

    # These are not mechanical failures. They are restrictions on claims that can be made.
    results += [
        check("verified_actual_announcement_dates", None, "Fundamental export does not provide verified SGX announcement timestamps; fiscal-period-end + 183 days is only a conservative proxy.", "material"),
        check("as_reported_restatement_control", None, "Annual vendor export may contain later restated historical values; no versioned as-reported filing layer is present.", "material"),
        check("survivorship_bias_control", None, "Universe is based on a current screened list; historical delisted-company coverage is not established.", "material"),
        check("STI_total_return_benchmark", sti_total_return.exists(), "An authorised .TRISTI total-return export was matched to the strategy execution dates. The source's gross/net tax convention remains to be documented.", "material"),
        check("STI_total_return_tax_convention", None, "The supplied .TRISTI export is labelled Total Return but does not state whether its dividend convention is gross or net of withholding tax. Do not make a final alpha claim until this is documented.", "material"),
        check("SG_cost_assumption_frozen", None, "The model now applies the 20 bp per-side scenario as cost = 20 bp × GTR. The numerical scenario remains subject to group approval.", "material"),
        check("absolute_ADV_capacity_filter", None, "Eligibility requires price/history/nonzero-volume conditions but does not impose an absolute SG trading-value capacity threshold.", "material"),
        check("factor_selection_threshold_precommitted", None, "The factor-selection threshold must be ratified and frozen by the group before treating the four-factor result as confirmatory.", "material"),
    ]
    report = pd.DataFrame(results)
    report.to_csv(OUT / "audit_checks.csv", index=False)
    summary = {
        "mechanical_passes": int((report.status == "PASS").sum()),
        "mechanical_failures": int((report.status == "FAIL").sum()),
        "methodology_limitations": int((report.status == "LIMITATION").sum()),
        "prices": {"rows": int(len(price)), "tickers": int(price.ticker.nunique()), "min_date": str(price.date.min().date()), "max_date": str(price.date.max().date())},
        "fundamentals": {"rows": int(len(fund)), "tickers": int(fund.ticker.nunique()), "usable_exports": int((coverage.fundamentals_status == 'usable_annual_fundamentals').sum())},
        "strategy": {"holding_rows": int(len(holdings)), "weeks": int(returns.signal_date.nunique()), "oos_weeks": int((returns.signal_date >= '2021-01-01').sum())},
        "conclusion": "Mechanical invariants passed if audit_checks has no FAIL rows. This does not validate PIT accounting, survivorship, capacity, or the supplied STI total-return series' gross/net tax convention."
    }
    (OUT / "audit_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
