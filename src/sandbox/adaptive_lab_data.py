"""SANDBOX adaptive lab, stage 1: data for the adaptive-strategy improvement grid.

Builds, on the adaptive branch's common seven-factor universe (eligible stocks with all of
Quality, Momentum, Low Risk, Investment, Value, Dividend, Size available):
  * weekly stock scores for the 7 original factors + 2 price factors from the earlier factor lab
    (HIGH52 = 52-week-high proximity, REVERSAL_1M = minus last 4 completed weeks), z-scored per week
  * weekly long-short factor returns (top 30% - bottom 30%, equal weight, executed stocks;
    identical rule to src/strategy/factor_returns.py) for all 9 factors, 2011-2025
The 7 original factor returns are checked against data/adaptive_strategy/common_factor_returns.csv.

Writes /root/out/adaptive_lab/{base.pkl, factor_returns.pkl, fret_check.csv}.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

from src.adaptive_strategy.pipeline import PipelineConfig, load_common_scores
from src.adaptive_strategy.composite_score import DEFAULT_FACTORS
from src.sandbox.factor_lab import weekly_tri_factors, zscore_week
from src.strategy import factor_returns as fr
from src.strategy import run_gate_b as rg

ROOT = Path(".")
OUT = Path(os.environ.get("ADAPTIVE_LAB_OUT", "outputs/sandbox/adaptive_lab"))
START, END = "2011-01-01", "2025-12-31"
NEW = {"High52": "HIGH52", "Reversal 1M": "REVERSAL_1M"}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base = load_common_scores(ROOT, PipelineConfig(start=START, end=END, expected_weeks=0))
    base["signal_date"] = base["signal_date"].dt.strftime("%Y-%m-%d")
    print("common universe rows", len(base), "weeks", base["signal_date"].nunique())

    rets = rg.load_returns(ROOT, "2009-01-01", END)          # history for the 52-week window
    tri = weekly_tri_factors(rets, base[["signal_date", "PERMNO"]])
    base = base.merge(tri, on=["signal_date", "PERMNO"], how="left")
    for fam, col in NEW.items():
        base[col] = zscore_week(base, f"{col}_raw")
        base[f"{col}__available"] = np.isfinite(base[col]).astype(int)
    base.to_pickle(OUT / "base.pkl")

    raw = pd.read_parquet(ROOT / rg.RETURNS_FILE,
                          columns=["signal_date", "next_execution_date", "PERMNO",
                                   "execution_to_next_execution_total_return", "execution_feasible",
                                   "unavailable_reason"])
    for c in ("signal_date", "next_execution_date", "PERMNO"):
        raw[c] = raw[c].astype(str).replace({"nan": None, "<NA>": None, "None": None})
    raw = raw[raw["signal_date"].between(START, END)]
    data = base.merge(raw, on=["signal_date", "PERMNO"], how="left")
    data["execution_feasible"] = data["execution_feasible"].fillna(0).astype(int)

    cols = {**DEFAULT_FACTORS, **NEW}
    rows = []
    for s, week in data.groupby("signal_date", sort=True):
        nx = week["next_execution_date"].dropna()
        nx = str(nx.iloc[0]) if len(nx) else ""
        ok_end = bool(nx) and nx <= END
        for fam, col in cols.items():
            el = week[np.isfinite(pd.to_numeric(week[col], errors="coerce"))]
            top, bot = fr.pick_leg(el, col, True), fr.pick_leg(el, col, False)
            t, _ = fr.leg_return(top) if ok_end else (None, 0)
            b, _ = fr.leg_return(bot) if ok_end else (None, 0)
            av = t is not None and b is not None
            rows.append({"signal_date": s, "next_execution_date": nx, "family": fam,
                         "eligible_count": len(el),
                         "factor_return_gross": (t - b) if av else np.nan,
                         "factor_return_available": int(av)})
    fret = pd.DataFrame(rows)
    fret.to_pickle(OUT / "factor_returns.pkl")

    ref = pd.read_csv(ROOT / "data/adaptive_strategy/common_factor_returns.csv")
    m = ref.merge(fret, on=["signal_date", "family"], suffixes=("_ref", "_mine"))
    m["d"] = (m["factor_return_gross_ref"] - m["factor_return_gross_mine"]).abs()
    chk = m.groupby("family").agg(n=("d", "size"), max_abs_diff=("d", "max"),
                                  avail_mismatch=("factor_return_available_ref",
                                                  lambda s: int((s != m.loc[s.index, "factor_return_available_mine"]).sum())))
    chk.to_csv(OUT / "fret_check.csv")
    print(chk)
    print(fret.groupby("family")["factor_return_gross"].agg(["count", "mean"]))


if __name__ == "__main__":
    main()
