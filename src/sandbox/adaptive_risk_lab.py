"""SANDBOX adaptive lab, stage 3: risk-control tuning (drawdown / volatility) + full course metrics.

PRE-REGISTRATION (written before running)
-----------------------------------------
Bases (from stage 2):  ORIG = P7 / Top3 / 3y / IV  (the branch's selected model)
                       IMPR = P6 (no Size) / Top3 / 3y / MV
Stock-level construction (names / weights inside the book):
  EQ      equal weight (branch rule)
  IVOL    same names, weight ∝ 1 / sigma_i, sigma_i = std of the stock's last 52 completed weekly
          returns (weeks with holding period ended <= signal date; >= 26 valid, else median of the
          book); single name capped at 2x the equal weight; 30% sector cap re-applied
  LOWVOL  before ranking, drop the 20% most volatile stocks of the week's universe (same sigma),
          then the branch's top-20% / buffer / sector rule on the rest
Portfolio-level control (weekly exposure e, rest in the risk-free asset, 10 bp x |change in e|):
  NONE
  DDCTRL  e = 0.5 once the strategy's drawdown from peak (completed weeks only, lag 2) is worse
          than -10%; back to 1 when it recovers above -5%
  BETA08  e = min(1, 0.8 / beta), beta = 52-week rolling beta to the S&P 500 (lag 2, >= 26 weeks)
18 variants = 2 bases x 3 stock rules x 3 controls.
Selection rule (training 2016-2020 only): among variants with training Sharpe >= 0.88
(original 0.93 minus 0.05), pick the highest training Calmar.  2021-2025 opened once, all reported.
Course metrics per period: annual return, annual volatility, Sharpe, max drawdown, Calmar,
drawdown episodes, weekly hit rate, number of round-trip trades, average holding period,
trade win rate, average profit per trade (trade stats are for the stock book, before exposure).
"""
from __future__ import annotations

import math
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

from src.adaptive_strategy.composite_score import ScoreConfig, calculate_scores
from src.sandbox.adaptive_lab import (COLS, COST, END, POOLS, START, TRAIN, VALID, annual_decisions,
                                      boot_sharpe_diff, nw_t)
from src.strategy import explore_variants as ev
from src.strategy import metrics as mt
from src.strategy import portfolio as pf
from src.strategy import run_gate_b as rg

ROOT = Path(".")
LAB = Path(os.environ.get("ADAPTIVE_LAB_OUT", "outputs/sandbox/adaptive_lab"))
OUT = LAB / "risk"
BASES = {"ORIG": ("P7", "T3L3", "IV"), "IMPR": ("P6", "T3L3", "MV")}
STOCK = ("EQ", "IVOL", "LOWVOL")
CTRL = ("NONE", "DDCTRL", "BETA08")


def stock_vol(rets: pd.DataFrame) -> pd.DataFrame:
    r = rets.assign(ret=np.where(rets["usable"] == 1, rets["execution_to_next_execution_total_return"], np.nan))
    wide = r.pivot(index="signal_date", columns="PERMNO", values="ret").sort_index()
    vol = wide.shift(2).rolling(52, min_periods=26).std()
    out = vol.stack().rename("sigma").reset_index()
    return out


def scored_for(base, fret, dates, pool, select, method):
    uni = base
    for f in POOLS[pool]:
        c = COLS[f]
        uni = uni[(uni[f"{c}__available"] == 1) & np.isfinite(pd.to_numeric(uni[c], errors="coerce"))]
    dec = [d for d in annual_decisions(fret, dates, POOLS[pool], select) if d["method"] == method]
    years = pd.to_datetime(uni["signal_date"]).dt.year
    parts = [calculate_scores(uni[years == d["year"]], d["selected"], d["weights"], ScoreConfig("selected_only"),
                              factor_columns=COLS) for d in dec]
    s = pd.concat(parts, ignore_index=True)
    s["signal_date"] = pd.to_datetime(s["signal_date"]).dt.strftime("%Y-%m-%d")
    s["PERMNO"] = s["PERMNO"].astype(str)
    return s


def ivol_weights(hold: pd.DataFrame, sig: pd.DataFrame) -> pd.DataFrame:
    h = hold.merge(sig, on=["signal_date", "PERMNO"], how="left")
    out = []
    for d, g in h.groupby("signal_date", sort=True):
        total = g["weight"].sum()
        n = len(g)
        s = g["sigma"].fillna(g["sigma"].median()).fillna(1.0).to_numpy()
        w = (1.0 / s) / (1.0 / s).sum() * total
        cap = 2.0 * total / n
        for _ in range(50):
            over = w > cap + 1e-15
            if not over.any():
                break
            ex = (w[over] - cap).sum()
            w[over] = cap
            free = ~over & (w < cap)
            w[free] += ex * w[free] / w[free].sum()
        w = ev.cap_sectors(w, g["sector"].to_numpy())
        out.append(g.assign(weight=w))
    return pd.concat(out, ignore_index=True)[hold.columns]


def control(r, rf, bench, kind):
    rf = rf.reindex(r.index).fillna(0.0)
    if kind == "NONE":
        e = pd.Series(1.0, index=r.index)
    elif kind == "DDCTRL":
        nav = (1 + r).cumprod()
        dd = (nav / nav.cummax() - 1).shift(2).fillna(0.0).to_numpy()
        e_arr, state = np.ones(len(r)), False
        for i, x in enumerate(dd):
            if not state and x < -0.10:
                state = True
            elif state and x > -0.05:
                state = False
            e_arr[i] = 0.5 if state else 1.0
        e = pd.Series(e_arr, index=r.index)
    elif kind == "BETA08":
        b = bench.reindex(r.index)
        beta = (r.rolling(52, min_periods=26).cov(b) / b.rolling(52, min_periods=26).var()).shift(2)
        e = np.minimum(1.0, 0.8 / beta).fillna(1.0).clip(upper=1.0)
    de = (e - e.shift(1).fillna(1.0)).abs()
    return e * r + (1 - e) * rf - COST * de, e


def metrics_block(r, rf, bench, hold, returns, span):
    s = r[(r.index >= span[0]) & (r.index <= span[1])]
    p = mt.performance(s, rf.reindex(s.index), bench.reindex(s.index))
    h = hold[hold["signal_date"].between(span[0], span[1])]
    t = mt.trade_stats(h, returns, span[1], COST)
    keys = ("ann_return", "ann_vol", "sharpe", "max_drawdown", "calmar", "drawdown_episodes",
            "longest_drawdown_weeks", "period_hit_rate", "beta", "information_ratio")
    return {**{k: p[k] for k in keys},
            **{k: t[k] for k in ("n_round_trips", "trades_per_year", "avg_holding_weeks", "trade_win_rate",
                                 "avg_profit_per_trade")}}


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    base = pd.read_pickle(LAB / "base.pkl")
    fret = pd.read_pickle(LAB / "factor_returns.pkl")
    returns = rg.load_returns(ROOT, START, END)
    done = set(returns.loc[returns["next_execution_date"].notna()
                           & (returns["next_execution_date"] <= END), "signal_date"])
    base = base[base["signal_date"].between(START, END) & base["signal_date"].isin(done)].copy()
    dates = sorted(base["signal_date"].unique())
    rf = rg.load_weekly_series(ROOT, rg.RF_FILE, "risk_free_return", START, END)
    bench = rg.load_weekly_series(ROOT, rg.BENCH_FILE, "benchmark_total_return", START, END)
    sig = stock_vol(rg.load_returns(ROOT, "2014-06-01", END))
    ref = pd.read_csv(LAB / "weekly_net_returns.csv", index_col=0)

    rows, paths = [], {}
    for bname, (pool, select, method) in BASES.items():
        scored = scored_for(base, fret, dates, pool, select, method)
        for st in STOCK:
            if st == "LOWVOL":
                s2 = scored.merge(sig, on=["signal_date", "PERMNO"], how="left")
                pct = s2.groupby("signal_date")["sigma"].rank(pct=True)
                s2 = s2[~(pct > 0.8)].drop(columns="sigma")
                hold, _ = pf.build_holdings(s2, use_buffer=True)
            else:
                hold, _ = pf.build_holdings(scored, use_buffer=True)
                if st == "IVOL":
                    hold = ivol_weights(hold, sig)
            path = mt.weekly_path(hold, returns, END)
            r = mt.net_returns(path, COST)
            r.index = path["signal_date"].to_numpy()
            if st == "EQ":
                tag0 = f"{pool}_{select}_{method}_NONE"
                d = (r - ref[tag0].reindex(r.index)).abs().max()
                print(f"{bname} EQ check vs stage 2: {d:.1e}")
                assert d < 1e-10
            for ct in CTRL:
                rr, e = control(r, rf, bench, ct)
                tag = f"{bname}_{st}_{ct}"
                paths[tag] = rr
                row = {"tag": tag, "base": bname, "stock": st, "control": ct, "avg_exposure": float(e.mean())}
                for lab, span in (("tr", TRAIN), ("va", VALID)):
                    row.update({f"{lab}_{k}": v for k, v in metrics_block(rr, rf, bench, hold, returns, span).items()})
                rows.append(row)
        print(bname, f"{time.time() - t0:.0f}s", flush=True)

    res = pd.DataFrame(rows)
    bm = {"tag": "S&P500_TR"}
    for lab, span in (("tr", TRAIN), ("va", VALID)):
        s = bench[(bench.index >= span[0]) & (bench.index <= span[1])]
        p = mt.performance(s, rf.reindex(s.index), s)
        bm.update({f"{lab}_{k}": p[k] for k in ("ann_return", "ann_vol", "sharpe", "max_drawdown", "calmar",
                                                "drawdown_episodes", "longest_drawdown_weeks", "period_hit_rate")})
    res = pd.concat([res, pd.DataFrame([bm])], ignore_index=True)
    res.to_csv(OUT / "risk_results.csv", index=False)
    P = pd.DataFrame(paths)
    P.to_csv(OUT / "risk_weekly_net_returns.csv")

    cand = res[res["base"].notna() & (res["tr_sharpe"] >= 0.88)].sort_values("tr_calmar", ascending=False)
    pick = cand.iloc[0]["tag"]
    va = (P.index >= VALID[0]) & (P.index <= VALID[1])
    sig_rows = []
    for tag in dict.fromkeys([pick, "IMPR_EQ_NONE"]):
        for nm, b in (("ORIG_EQ_NONE", P["ORIG_EQ_NONE"]), ("S&P500", bench.reindex(P.index))):
            obs, p, ci = boot_sharpe_diff(P[tag][va], b[va], rf.reindex(P.index).fillna(0)[va])
            sig_rows.append({"tag": tag, "vs": nm, "nw_t_active": nw_t((P[tag][va] - b[va]).to_numpy()),
                             "sharpe_diff": obs, "boot_p": p, "ci_lo": ci[0], "ci_hi": ci[1]})
    pd.DataFrame(sig_rows).to_csv(OUT / "risk_significance.csv", index=False)
    with open(OUT / "risk_summary.txt", "w") as f:
        f.write(f"eligible (train Sharpe >= 0.88): {cand['tag'].tolist()}\nSELECTED: {pick}\n")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    c = ["tr_ann_return", "tr_sharpe", "tr_max_drawdown", "tr_calmar", "va_ann_return", "va_ann_vol",
         "va_sharpe", "va_max_drawdown", "va_calmar", "va_beta", "avg_exposure"]
    print(res.set_index("tag")[c].round(3).to_string())
    print(open(OUT / "risk_summary.txt").read())
    print(pd.DataFrame(sig_rows).round(3).to_string())
    print(f"total {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
