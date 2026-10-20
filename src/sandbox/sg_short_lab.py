"""SANDBOX: adding a short side to the frozen SG Adaptive Top-4 strategy.

Long book = the pipeline's holdings, unchanged.  Variants fixed before looking at results:
  LONG_ONLY      the strategy as frozen
  HEDGE_50       long book minus 0.5 x STI total return (short STI futures / ETF)
  HEDGE_BETA     long book minus beta_t x STI; beta_t = 52-week beta of the long book's net weekly
                 return on STI, using only weeks completed before the signal (lag 2)
  LS_SHORTABLE   long book minus short book; short book = bottom 20% composite score among the
                 60 largest eligible stocks (proxy for names that can be borrowed on SGX), EW
  LS_ANY         same, short book from the whole universe (upper bound, not implementable)
  130_30         1.3 x long book minus 0.3 x shortable short book
Costs: stocks 10 bp one-way on every trade; stock borrow 3% a year on the short notional;
index hedge 2 bp one-way on hedge-ratio changes (futures), no financing (cash earns 0).
Periods: OBS 2019-20 (where a choice would be made), EVAL 2021-01..2026-09, live window
2026-09-30 close -> last day in data (daily).

    python -m src.sandbox.sg_short_lab --steps <live_output>/_steps --sti <STI xlsx> --team <SGX_TEAM_HANDOFF>
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from src.sg_pipeline import run as R
from src.sg_pipeline.live import daily_nav
from src.strategy import metrics as mt

EVAL_END = "2026-09-30"
N_SHORTABLE, SHORT_FRAC, BORROW, STOCK_C, FUT_C = 60, 0.20, 0.03, 0.001, 0.0002


def short_book(scores, uni, shortable_only):
    s = scores.merge(uni[["signal_date", "PERMNO", "marketcap_sgd_candidate"]], on=["signal_date", "PERMNO"])
    s = s[s["score_available"] == 1]
    if shortable_only:
        s = s[s.groupby("signal_date")["marketcap_sgd_candidate"].rank(ascending=False) <= N_SHORTABLE]
    n = s.groupby("signal_date")["PERMNO"].transform("size")
    r = s.groupby("signal_date")["composite_score"].rank(ascending=True, method="first")
    b = s[r <= np.maximum(1, np.floor(SHORT_FRAC * n))].copy()
    b["weight"] = 1.0 / b.groupby("signal_date")["PERMNO"].transform("size")
    return b[["signal_date", "PERMNO", "weight"]]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", required=True)
    ap.add_argument("--sti", required=True)
    ap.add_argument("--team", required=True)
    ap.add_argument("--out", default="private_data/sg_short_lab")
    a = ap.parse_args()
    st, out = Path(a.steps), R.resolve(a.out)
    out.mkdir(parents=True, exist_ok=True)
    uni = pd.read_csv(st / "01_universe_weekly.csv.gz")
    rets = pd.read_csv(st / "02_security_weekly_returns.csv.gz")
    scores = pd.read_csv(st / "05_composite_scores_weekly.csv.gz")
    hold = pd.read_csv(st / "05_holdings_weekly.csv")
    cal = uni.drop_duplicates("signal_date").set_index("signal_date")[["execution_date", "next_execution_date"]]
    sti_lvl = R.load_sti(Path(a.sti))

    books = {"LONG": hold[["signal_date", "PERMNO", "weight"]],
             "SHORT_SHORTABLE": short_book(scores, uni, True), "SHORT_ANY": short_book(scores, uni, False)}
    p = {}
    for k, h in books.items():
        x, _ = mt.weekly_path_and_ledger(h, rets, "2099-12-31")
        p[k] = x.set_index("signal_date")
    idx = p["LONG"].index
    sti = pd.Series([sti_lvl.loc[:cal.loc[s, "next_execution_date"]].iloc[-1]
                     / sti_lvl.loc[:cal.loc[s, "execution_date"]].iloc[-1] - 1 for s in idx], index=idx)
    L = p["LONG"]["gross_return"] - STOCK_C * p["LONG"]["gtr"]
    def S(k, size):                                   # cost of a short book of given size, as a return
        q = p[k].reindex(idx)
        return size * q["gross_return"], size * (STOCK_C * q["gtr"] + BORROW / 52)
    beta = L.rolling(52).cov(sti) / sti.rolling(52).var()
    beta = beta.shift(2).clip(0, 1.5)
    hb = beta.fillna(beta.dropna().iloc[0] if beta.notna().any() else 0.7)
    sr, sc = S("SHORT_SHORTABLE", 1.0)
    ar, ac = S("SHORT_ANY", 1.0)
    sr3, sc3 = S("SHORT_SHORTABLE", 0.3)
    v = pd.DataFrame({
        "LONG_ONLY": L,
        "HEDGE_50": L - 0.5 * sti - FUT_C * 0.0,
        "HEDGE_BETA": L - hb * sti - FUT_C * hb.diff().abs().fillna(hb),
        "LS_SHORTABLE": L - sr - sc,
        "LS_ANY": L - ar - ac,
        "130_30": 1.3 * L - sr3 - sc3 - 0.3 * STOCK_C * p["LONG"]["gtr"],
        "STI": sti,
        "SHORT_LEG_SHORTABLE_gross": p["SHORT_SHORTABLE"]["gross_return"].reindex(idx),
        "SHORT_LEG_ANY_gross": p["SHORT_ANY"]["gross_return"].reindex(idx),
    })
    v["hedge_beta"] = hb
    end_ok = cal.loc[idx, "next_execution_date"] <= EVAL_END
    v[end_ok.values].to_csv(out / "short_lab_weekly_returns.csv")
    rows = []
    for pname, (a0, b0) in {"OBS_2019_20": ("2019-01-01", "2020-12-31"), "EVAL_2021_26": ("2021-01-01", "2026-09-30"),
                            "FULL_2016_26": ("2016-01-01", "2026-09-30")}.items():
        m = (v.index >= a0) & (v.index <= b0) & end_ok.values
        for c in v.columns.drop("hedge_beta"):
            perf = mt.performance(v.loc[m, c].dropna())
            rows.append({"period": pname, "variant": c, **{k: perf[k] for k in
                         ("ann_return", "ann_vol", "sharpe", "max_drawdown", "calmar")},
                         "corr_with_STI": v.loc[m, c].corr(v.loc[m, "STI"])})
    res = pd.DataFrame(rows)
    res.to_csv(out / "short_lab_summary.csv", index=False)

    # live window, daily: books held from each execution date, short legs gross, costs at rebalances
    d = R.read_parquet(Path(a.team) / "data/sgx_daily_market.parquet", ["gvkey", "iid", "date", "available_adjusted_price"], out)
    d["PERMNO"] = d["gvkey"].astype(str) + "_" + d["iid"].astype(str)
    d["date"] = pd.to_datetime(d["date"]).dt.normalize()
    adj = d.dropna(subset=["available_adjusted_price"]).pivot_table(
        index="date", columns="PERMNO", values="available_adjusted_price", aggfunc="last").sort_index().ffill()
    t0 = pd.Timestamp(EVAL_END)
    live = [s for s in idx if pd.Timestamp(cal.loc[s, "next_execution_date"]) > t0]
    nav = {}
    for k, h in books.items():
        gtr = {pd.Timestamp(cal.loc[s, "execution_date"]): g for s, g in p[k]["gtr"].items()}
        wk = [(pd.Timestamp(cal.loc[s, "execution_date"]), pd.Timestamp(cal.loc[s, "next_execution_date"]),
               dict(zip(h.loc[h["signal_date"] == s, "PERMNO"], h.loc[h["signal_date"] == s, "weight"]))) for s in live]
        nav[k] = daily_nav(wk, adj, EVAL_END, STOCK_C, gtr)
    nav = pd.DataFrame(nav)
    st_d = sti_lvl[(sti_lvl.index >= t0) & (sti_lvl.index <= adj.index.max())]
    nav["STI"] = st_d / st_d.iloc[0]
    nav = nav[nav.index >= t0].dropna()
    r = nav.pct_change().fillna(0)
    days = len(r) - 1
    bl = float(hb.loc[live[-1]])
    lv = pd.DataFrame({
        "LONG_ONLY": r["LONG"], "HEDGE_50": r["LONG"] - 0.5 * r["STI"], "HEDGE_BETA": r["LONG"] - bl * r["STI"],
        "LS_SHORTABLE": r["LONG"] - r["SHORT_SHORTABLE"] - BORROW / 252,
        "LS_ANY": r["LONG"] - r["SHORT_ANY"] - BORROW / 252,
        "130_30": 1.3 * r["LONG"] - 0.3 * r["SHORT_SHORTABLE"] - 0.3 * BORROW / 252, "STI": r["STI"],
        "SHORT_LEG_SHORTABLE": r["SHORT_SHORTABLE"], "SHORT_LEG_ANY": r["SHORT_ANY"]})
    lnav = (1 + lv).cumprod()
    lnav.to_csv(out / "short_lab_live_daily_nav.csv")
    pd.set_option("display.width", 220)
    print(res.round(3).to_string(index=False))
    print(f"\nlive {EVAL_END} -> {nav.index[-1].date()} ({days} days), beta used {bl:.2f}")
    print((lnav.iloc[-1] - 1).round(4).to_string())


if __name__ == "__main__":
    main()
