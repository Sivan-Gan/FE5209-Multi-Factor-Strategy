"""SANDBOX: tune the SG adaptive strategy on the teammate's published specification.

Baseline (teammate, 2026-10-18 group message; reproduced to 0.1 pp):
  6 factors Quality, Momentum, Low Risk, Investment, Size, Value (no Dividend);
  Size = panel Size_score AS GIVEN (high = SMALL cap: panel size_raw = -ln(mcap proxy); an earlier
  version of this note said LARGE, corrected 2026-10-18); Top-3 by 3y mean/std; IV weights;
  top 20% equal weight, buffer 20/30, 30% six-sector cap, weekly, 10 bp one-way.
  Observation period 2019-2020 (compare by Calmar), evaluation 2021-01 .. 2026-09.

PRE-REGISTERED GRID (144 = 4 x 3 x 2 x 3 x 2), written before running:
  select   T3L3 (baseline), T3L5, T4L3, ALL (all six, no selection)
  weight   EW, IV (baseline), MV
  names    N20 = top 20% / exit 30% (baseline, ~13 names);  N30 = top 30% / exit 40% (~19 names)
  stock    EQ (baseline); IVOL = 1/52w vol weights (lag 2, cap 2x EW, sector cap);
           LOWVOL = drop the 20% most volatile stocks before ranking
  control  NONE (baseline); DDCTRL = 50% exposure when lag-2 drawdown < -10%, back at > -5%
Selection rule (observation 2019-2020 only): among variants whose observation Sharpe >= the
baseline's, the highest observation Calmar.  Robustness shown on 2016-2018 (also before the
evaluation period).  Evaluation 2021-2026 opened once; all variants reported.
Sharpe with rf = 0 (as the teammate / the package has no SGD rf).
"""
from __future__ import annotations

import itertools
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

from src.adaptive_strategy.composite_score import ScoreConfig, calculate_scores
from src.adaptive_strategy.factor_selection import SelectionConfig, select_factors
from src.adaptive_strategy.factor_weighting import WeightingConfig, calculate_selected_weights
from src.sandbox import sg_adaptive_lab as L
from src.sandbox.adaptive_lab import boot_sharpe_diff, nw_t
from src.sandbox.adaptive_risk_lab import control, ivol_weights
from src.strategy import metrics as mt
from src.strategy import portfolio as pf

OUT = Path(os.environ.get("SG_TUNE_OUT", "outputs/sandbox/sg_tune_lab"))
L.COLS = {"Quality": "Quality_score", "Momentum": "Momentum_score", "Low Risk": "LowRisk_score",
          "Investment": "Investment_score", "Size": "SizeSmall_score", "Value": "Value_score"}
POOL = list(L.COLS)
SELECTS = {"T3L3": (3, 3), "T3L5": (3, 5), "T4L3": (4, 3), "ALL": (None, None)}
METHODS = ("EW", "IV", "MV")
NAMES = {"N20": (0.20, 0.30), "N30": (0.30, 0.40)}
STOCK = ("EQ", "IVOL", "LOWVOL")
CTRL = ("NONE", "DDCTRL")
PRE, OBS, EVAL = ("2016-01-01", "2018-12-31"), ("2019-01-01", "2020-12-31"), ("2021-01-01", "2026-12-31")
EVAL25 = ("2021-01-01", "2025-12-31")
BASE = "T3L3_IV_N20_EQ_NONE"


def decisions(fret, dates, select):
    k, lb = SELECTS[select]
    cal = pd.to_datetime(pd.Series(dates))
    out = {}
    for year, cutoff in cal.groupby(cal.dt.year).min().items():
        sel = list(POOL) if k is None else select_factors(
            fret, cutoff, POOL, SelectionConfig(lookback_years=lb, top_k=k, min_coverage=0.65))[0]
        w, _ = calculate_selected_weights(fret, cutoff, sel, WeightingConfig(), covariance_families=POOL)
        out[year] = (sel, w)
    return out


def scored_for(uni, dates, dec, method):
    parts = []
    years = pd.to_datetime(uni["signal_date"]).dt.year
    for year, (sel, w) in dec.items():
        sub = uni[(years == year) & uni["signal_date"].isin(dates)]
        parts.append(calculate_scores(sub, sel, w[method], ScoreConfig("selected_only"), factor_columns=L.COLS))
    s = pd.concat(parts, ignore_index=True)
    s["signal_date"] = pd.to_datetime(s["signal_date"]).dt.strftime("%Y-%m-%d")
    return s


def perf(r, span, hold=None, rets=None, bench=None):
    s = r[(r.index >= span[0]) & (r.index <= span[1])]
    p = mt.performance(s, None, bench.reindex(s.index) if bench is not None else None)
    o = {k: p.get(k) for k in ("ann_return", "ann_vol", "sharpe", "sortino", "max_drawdown", "calmar",
                               "drawdown_episodes", "longest_drawdown_weeks", "period_hit_rate", "beta")}
    if hold is not None:
        h = hold[hold["signal_date"].between(span[0], span[1])]
        t = mt.trade_stats(h, rets, span[1], L.COST)
        o.update({k: t[k] for k in ("n_round_trips", "trades_per_year", "avg_holding_weeks", "trade_win_rate",
                                    "avg_profit_per_trade")})
    return o


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    panel, uni, rets, last = L.load_panel()
    uni["SizeSmall_score"] = -uni["SizeSmall_score"]          # back to the panel's sign: high = SMALL cap
    end = str(last)
    fret = L.factor_returns(uni, end)
    done = set(rets.loc[rets["next_execution_date"] <= end, "signal_date"])
    dates = sorted(d for d in uni["signal_date"].unique() if d >= "2016-01-01" and d in done)
    cal = uni[uni.signal_date.isin(dates)].groupby("signal_date")[["execution_date", "next_execution_date"]].first()
    sti = L.sti_weekly(cal.reset_index())
    ewu = uni[uni.signal_date.isin(dates)].groupby("signal_date")["forward_return"].mean()
    sig = L.stock_sigma(rets)
    zero = pd.Series(0.0, index=sti.index)

    rows, paths, holds = [], {}, {}
    dec_cache = {s: decisions(fret, dates, s) for s in SELECTS}
    for select, method, nm, st in itertools.product(SELECTS, METHODS, NAMES, STOCK):
        scored = scored_for(uni, dates, dec_cache[select], method)
        if st == "LOWVOL":
            s2 = scored.merge(sig, on=["signal_date", "PERMNO"], how="left")
            s2 = s2[~(s2.groupby("signal_date")["sigma"].rank(pct=True) > 0.8)].drop(columns="sigma")
            scored = s2
        pf.SELECT_FRACTION, pf.EXIT_FRACTION = NAMES[nm]
        hold, cons = pf.build_holdings(scored, use_buffer=True)
        pf.SELECT_FRACTION, pf.EXIT_FRACTION = 0.20, 0.30
        if st == "IVOL":
            hold = ivol_weights(hold, sig)
        path = L.mt.weekly_path(hold, rets, end).set_index("signal_date")
        net = mt.net_returns(path, L.COST)
        for ct in CTRL:
            r, e = control(net, zero, sti, ct)
            tag = f"{select}_{method}_{nm}_{st}_{ct}"
            paths[tag] = r
            row = {"tag": tag, "select": select, "method": method, "names": nm, "stock": st, "control": ct,
                   "avg_names": float(cons["n_held"].mean()), "avg_exposure": float(e.mean()),
                   "ann_turnover": float(path["turnover"].mean() * 52)}
            for lab, span in (("pre", PRE), ("obs", OBS), ("ev", EVAL), ("ev25", EVAL25)):
                row.update({f"{lab}_{k}": v for k, v in
                            perf(r, span, hold if lab == "ev" else None, rets, sti).items()})
            row["ev_ann_return_cost30"] = perf(mt.net_returns(path, 0.003), EVAL)["ann_return"] if ct == "NONE" else np.nan
            rows.append(row)
            if tag == BASE:
                holds[tag] = hold
    print(f"grid {time.time() - t0:.0f}s")
    res = pd.DataFrame(rows)
    for name, r in (("STI_TR", sti), ("EW_UNIVERSE_gross", ewu)):
        row = {"tag": name}
        for lab, span in (("pre", PRE), ("obs", OBS), ("ev", EVAL), ("ev25", EVAL25)):
            row.update({f"{lab}_{k}": v for k, v in perf(r, span, bench=sti).items()})
        res = pd.concat([res, pd.DataFrame([row])], ignore_index=True)
    res.to_csv(OUT / "sg_tune_results.csv", index=False)
    P = pd.DataFrame(paths)
    P["STI_TR"], P["EW_UNIVERSE_gross"] = sti, ewu
    P.to_csv(OUT / "sg_tune_weekly_returns.csv")

    g = res[res["select"].notna()]
    b = g[g.tag == BASE].iloc[0]
    cand = g[g["obs_sharpe"] >= b["obs_sharpe"] - 1e-12].sort_values("obs_calmar", ascending=False)
    pick = cand.iloc[0]["tag"]
    top5 = cand.head(5)["tag"].tolist()
    rho = g[["obs_calmar", "ev_calmar"]].corr(method="spearman").iloc[0, 1]
    rho2 = g[["obs_sharpe", "ev_sharpe"]].corr(method="spearman").iloc[0, 1]
    ev = (P.index >= EVAL[0]) & (P.index <= EVAL[1])
    srows = []
    for tag in dict.fromkeys([pick, BASE]):
        for vs in ("STI_TR", BASE):
            if vs == tag:
                continue
            a, c = P[tag][ev], P[vs][ev]
            obs, p, ci = boot_sharpe_diff(a, c, pd.Series(0.0, index=a.index))
            srows.append({"tag": tag, "vs": vs, "ann_active": float((a - c).mean() * 52), "nw_t": nw_t((a - c).to_numpy()),
                          "sharpe_diff": obs, "boot_p": p, "ci_lo": ci[0], "ci_hi": ci[1]})
    pd.DataFrame(srows).to_csv(OUT / "sg_tune_significance.csv", index=False)
    with open(OUT / "sg_tune_summary.txt", "w") as f:
        f.write(f"baseline {BASE}: obs sharpe {b['obs_sharpe']:.3f} calmar {b['obs_calmar']:.3f}\n"
                f"eligible {len(cand)} of {len(g)}\nSELECTED {pick}\nTOP5 {top5}\n"
                f"spearman obs->eval calmar {rho:.3f}, sharpe {rho2:.3f}\n")
    print(open(OUT / "sg_tune_summary.txt").read())
    pd.set_option("display.width", 260)
    c = ["avg_names", "pre_sharpe", "pre_calmar", "obs_ann_return", "obs_sharpe", "obs_max_drawdown", "obs_calmar",
         "ev_ann_return", "ev_ann_vol", "ev_sharpe", "ev_max_drawdown", "ev_calmar"]
    show = [BASE] + [t for t in top5 if t != BASE] + ["STI_TR", "EW_UNIVERSE_gross"]
    print(res.set_index("tag").loc[show, c].round(3).to_string())
    for k in ("select", "method", "names", "stock", "control"):
        print(g.groupby(k)[["obs_sharpe", "obs_calmar", "ev_sharpe", "ev_calmar", "ev_max_drawdown"]].median().round(3))
    print(pd.DataFrame(srows).round(3).to_string())


if __name__ == "__main__":
    main()
