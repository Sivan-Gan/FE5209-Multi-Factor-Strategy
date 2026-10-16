#!/usr/bin/env python3
"""Compare the frozen SG strategy with an exported STI total-return series.

The user supplies a LSEG/FTSE authorised export.  This script deliberately
does not guess an index RIC or a data item: those depend on the user's LSEG
entitlement and on whether gross or net total return is selected.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/strategy/sg_independent"


def stats(r: pd.Series) -> dict[str, float]:
    r = r.dropna()
    nav = (1 + r).cumprod()
    return {"weeks": int(len(r)), "cumulative_return": float(nav.iloc[-1] - 1),
            "annualized_return": float(nav.iloc[-1] ** (52 / len(r)) - 1),
            "annualized_volatility": float(r.std(ddof=1) * np.sqrt(52)),
            "weekly_sharpe_zero_rf": float(r.mean() / r.std(ddof=1) * np.sqrt(52)),
            "max_drawdown": float((1 - nav / nav.cummax()).max())}


def main() -> None:
    p = argparse.ArgumentParser(description="Use an authorised STI total-return export; no index code is assumed.")
    p.add_argument("sti_total_return_file", type=Path)
    p.add_argument("--sheet", default=0, help="Excel sheet name or zero-based index (default: 0)")
    p.add_argument("--header", type=int, default=0, help="Zero-based header row (default: 0)")
    p.add_argument("--date-col", default="Date")
    p.add_argument("--value-col", required=True, help="Column containing total-return index level")
    p.add_argument("--return-type", choices=["gross", "net", "unspecified"], default="unspecified")
    args = p.parse_args()
    sheet = int(args.sheet) if args.sheet.isdigit() else args.sheet
    raw = pd.read_excel(args.sti_total_return_file, sheet_name=sheet, header=args.header)
    if args.date_col not in raw or args.value_col not in raw:
        raise ValueError(f"Required columns absent. Available columns: {raw.columns.tolist()}")
    sti = raw[[args.date_col, args.value_col]].rename(columns={args.date_col: "date", args.value_col: "sti_total_return_level"})
    sti["date"] = pd.to_datetime(sti.date, errors="coerce")
    sti["sti_total_return_level"] = pd.to_numeric(sti.sti_total_return_level, errors="coerce")
    sti = sti.dropna().drop_duplicates("date").sort_values("date")
    if (sti.sti_total_return_level <= 0).any():
        raise ValueError("Total-return index level must be positive.")

    strategy = pd.read_csv(OUT / "03_strategy_weekly_returns_2016_latest.csv", parse_dates=["signal_date"])
    dates = pd.read_csv(OUT / "03_frozen_sg_holdings_2016_latest.csv",
                        usecols=["signal_date", "execution_date", "next_execution_date"],
                        parse_dates=["signal_date", "execution_date", "next_execution_date"]).drop_duplicates("signal_date")
    execution = sti.rename(columns={"date": "execution_date", "sti_total_return_level": "sti_execution_level"})
    nxt = sti.rename(columns={"date": "next_execution_date", "sti_total_return_level": "sti_next_execution_level"})
    compare = (strategy.merge(dates, on="signal_date", how="inner")
               .merge(execution, on="execution_date", how="left")
               .merge(nxt, on="next_execution_date", how="left"))
    compare["sti_total_return"] = compare.sti_next_execution_level / compare.sti_execution_level - 1
    compare = compare[compare.signal_date.ge("2021-01-01")].dropna(subset=["net_return", "sti_total_return"]).copy()
    compare["active_return_vs_sti_total_return"] = compare.net_return - compare.sti_total_return
    active = compare.active_return_vs_sti_total_return
    beta = compare.net_return.cov(compare.sti_total_return) / compare.sti_total_return.var(ddof=1)
    alpha = compare.net_return.mean() - beta * compare.sti_total_return.mean()
    summary = {"comparison_label": "Strategy net return versus supplied STI total-return index",
               "index_return_type": args.return_type,
               "source_file": args.sti_total_return_file.name,
               "period_start_signal": str(compare.signal_date.min().date()),
               "period_end_signal": str(compare.signal_date.max().date()),
               "strategy_net": stats(compare.net_return), "sti_total_return": stats(compare.sti_total_return),
               "active": {"cumulative_return_difference": float((1 + compare.net_return).prod() / (1 + compare.sti_total_return).prod() - 1),
                          "annualized_tracking_error": float(active.std(ddof=1) * np.sqrt(52)),
                          "information_ratio_zero_rf": float(active.mean() / active.std(ddof=1) * np.sqrt(52)),
                          "beta_to_sti_total_return": float(beta), "annualized_CAPM_alpha_approx": float(alpha * 52)},
               "caveat": "Interpret alpha only after documenting the supplied index's gross/net dividend and currency conventions."}
    compare.to_csv(OUT / "05_sti_total_return_weekly_comparison_2021_latest.csv", index=False)
    (OUT / "05_sti_total_return_summary_2021_latest.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
