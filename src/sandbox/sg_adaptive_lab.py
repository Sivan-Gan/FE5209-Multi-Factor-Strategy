"""SANDBOX: the adaptive Top-K factor strategy on the Singapore (SGX) universe.

Data: private SGX_comprehensive_2006_2026_handoff (LSEG-derived, NOT strict PIT, 114 current
tickers -> survivorship bias), factor_panels/sg_seven_factor_candidate_panel_2006_latest.csv.gz,
benchmark/STI_Total_Return_LSEG_export.xlsx (.TRISTI, FTSE Straits Times total return, SGD).

Rules copied from the US adaptive branch (same functions where possible):
  universe   price_eligible & all 7 factor scores available (branch 'common_all')
  factors    Quality, Momentum, Low Risk, Investment, Value, Dividend, Size  (SG panel scores;
             Size sign flipped. CORRECTION 2026-10-18: the panel's size_raw is
             -ln(mcap proxy), so Size_score is already high = SMALL cap; after the flip the column
             'SizeSmall_score' is actually high = LARGE cap (name kept so results reproduce))
  factor ret top 30% - bottom 30% equal weight, forward_return (execution -> next execution)
  selection  annual, first signal week of the year, top-K by mean/std of completed weekly
             factor returns over the lookback, min coverage 65%    (branch select_factors)
  weights    EW / IV / MV, 52-week window, 5%-80%, 10% shrinkage    (branch calculate_selected_weights)
  portfolio  top 20% composite score, buffer 20/30, 30% sector cap (six SG sectors), equal weight
             (portfolio.build_holdings), weekly, one-way cost 10 bp (30 bp stress reported)
  Sharpe     excess over 0 (no SGD risk-free series in the package); same for the STI.

PRE-REGISTERED GRID (48): pool {P7, P6 = no Size} x select {T3L3, T3L5, T4L3, ALL}
  x weight {EW, IV, MV} x stock weighting {EQ, IVOL (1/52w vol, lag 2, cap 2x EW, sector cap)}.
Reference config = the US branch's frozen model P7/T3L3/IV/EQ ("SG replica").
Selection on training 2016-2020 only: highest training Sharpe among configs with training
Calmar >= the replica's.  2021-2025 opened once; 2026 YTD (to the last complete week) reported
as an extension.  All configs reported.
"""
from __future__ import annotations

import itertools
import math
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

from src.adaptive_strategy.composite_score import ScoreConfig, calculate_scores
from src.adaptive_strategy.factor_selection import SelectionConfig, select_factors
from src.adaptive_strategy.factor_weighting import WeightingConfig, calculate_selected_weights
from src.sandbox.adaptive_lab import boot_sharpe_diff, nw_t
from src.sandbox.adaptive_risk_lab import ivol_weights
from src.strategy import factor_returns as fr
from src.strategy import metrics as mt
from src.strategy import portfolio as pf

SG = Path(os.environ.get("SG_DATA", "SGX_comprehensive_2006_2026_handoff"))
OUT = Path(os.environ.get("SG_LAB_OUT", "outputs/sandbox/sg_adaptive_lab"))
COLS = {"Quality": "Quality_score", "Momentum": "Momentum_score", "Low Risk": "LowRisk_score",
        "Investment": "Investment_score", "Value": "Value_score", "Dividend": "Dividend_score",
        "Size": "SizeSmall_score"}
P7 = list(COLS)
POOLS = {"P7": P7, "P6": [f for f in P7 if f != "Size"]}
SELECTS = {"T3L3": (3, 3), "T3L5": (3, 5), "T4L3": (4, 3), "ALL": (None, None)}
METHODS = ("EW", "IV", "MV")
STOCKW = ("EQ", "IVOL")
TRAIN, VALID, EXT = ("2016-01-01", "2020-12-31"), ("2021-01-01", "2025-12-31"), ("2026-01-01", "2026-12-31")
START = "2016-01-01"
COST, STRESS = 0.001, 0.003


def load_panel():
    p = pd.read_csv(SG / "factor_panels/sg_seven_factor_candidate_panel_2006_latest.csv.gz")
    p["signal_date"] = pd.to_datetime(p["signal_date"]).dt.strftime("%Y-%m-%d")
    p["SizeSmall_score"] = -p["Size_score"]
    p["SizeSmall_available"] = p["Size_available"]
    avail = {"Quality": "Quality_available", "Momentum": "Momentum_available", "Low Risk": "LowRisk_available",
             "Investment": "Investment_available", "Value": "Value_available", "Dividend": "Dividend_available",
             "Size": "SizeSmall_available"}
    for f, c in COLS.items():
        p[f"{c}__available"] = ((p[avail[f]] == 1) & np.isfinite(p[c])).astype(int)
    p = p.rename(columns={"ticker": "PERMNO", "six_sector": "historical_gics_sector"})
    uni = p[p["price_eligible"].astype(bool)].copy()
    ok = np.ones(len(uni), bool)
    for c in COLS.values():
        ok &= uni[f"{c}__available"].eq(1).to_numpy()
    uni = uni[ok].copy()
    last = p["next_execution_date"].max()
    rets = p[["signal_date", "PERMNO", "next_execution_date", "forward_return"]].rename(
        columns={"forward_return": "execution_to_next_execution_total_return"})
    rets["usable"] = np.isfinite(rets["execution_to_next_execution_total_return"]).astype(int)
    return p, uni, rets, last


def factor_returns(uni, end):
    d = uni.assign(execution_to_next_execution_total_return=uni["forward_return"],
                   execution_feasible=1, unavailable_reason="")
    rows = []
    for s, week in d.groupby("signal_date", sort=True):
        nx = str(week["next_execution_date"].iloc[0])
        ok = nx <= end
        for fam, col in COLS.items():
            t, _ = fr.leg_return(fr.pick_leg(week, col, True)) if ok else (None, 0)
            b, _ = fr.leg_return(fr.pick_leg(week, col, False)) if ok else (None, 0)
            av = t is not None and b is not None
            rows.append({"signal_date": s, "next_execution_date": nx if ok else "", "family": fam,
                         "eligible_count": len(week), "factor_return_gross": (t - b) if av else np.nan,
                         "factor_return_available": int(av)})
    return pd.DataFrame(rows)


def stock_sigma(rets):
    wide = rets.pivot(index="signal_date", columns="PERMNO",
                      values="execution_to_next_execution_total_return").sort_index()
    vol = wide.shift(2).rolling(52, min_periods=26).std()
    return vol.stack().rename("sigma").reset_index()


def sti_weekly(cal):
    x = pd.read_excel(SG / "benchmark/STI_Total_Return_LSEG_export.xlsx", header=None)
    d = x[pd.to_datetime(x[0], errors="coerce").notna() & pd.to_numeric(x[1], errors="coerce").notna()]
    lvl = pd.Series(pd.to_numeric(d[1]).to_numpy(), index=pd.to_datetime(d[0])).sort_index()
    lvl = lvl[~lvl.index.duplicated()]
    def at(day):        # last close on or before the execution date
        return lvl.loc[:pd.Timestamp(day)].iloc[-1]
    return pd.Series({s: at(n) / at(e) - 1.0 for s, e, n in cal.itertuples(index=False)})


def run_config(uni, fret, rets, sig, dates, pool, select, method, stockw, end):
    k, lb = SELECTS[select]
    cal = pd.to_datetime(pd.Series(dates))
    parts = []
    for year, cutoff in cal.groupby(cal.dt.year).min().items():
        if k is None:
            sel = list(POOLS[pool])
        else:
            sel, _ = select_factors(fret, cutoff, POOLS[pool], SelectionConfig(lookback_years=lb, top_k=k,
                                                                              min_coverage=0.65))
        w, _ = calculate_selected_weights(fret, cutoff, sel, WeightingConfig(), covariance_families=POOLS[pool])
        sub = uni[pd.to_datetime(uni["signal_date"]).dt.year == year]
        sub = sub[sub["signal_date"].isin(dates)]
        parts.append((year, sel, w[method], calculate_scores(sub, sel, w[method], ScoreConfig("selected_only"),
                                                             factor_columns=COLS)))
    scored = pd.concat([x[3] for x in parts], ignore_index=True)
    scored["signal_date"] = pd.to_datetime(scored["signal_date"]).dt.strftime("%Y-%m-%d")
    hold, cons = pf.build_holdings(scored, use_buffer=True)
    if stockw == "IVOL":
        hold = ivol_weights(hold, sig)
    path = mt.weekly_path(hold, rets, end).set_index("signal_date")
    decisions = [{"year": y, "selected": ", ".join(s), **{f"w_{f}": v for f, v in ww.items()}}
                 for y, s, ww, _ in parts]
    return path, hold, cons, decisions


def block(r, span, hold=None, rets=None, bench=None):
    s = r[(r.index >= span[0]) & (r.index <= span[1])]
    if len(s) < 5:
        return {}
    p = mt.performance(s, None, bench.reindex(s.index) if bench is not None else None)
    out = {k: p.get(k) for k in ("n_weeks", "ann_return", "ann_vol", "sharpe", "sortino", "max_drawdown", "calmar",
                                 "drawdown_episodes", "longest_drawdown_weeks", "period_hit_rate", "beta",
                                 "information_ratio")}
    if hold is not None:
        h = hold[hold["signal_date"].between(span[0], span[1])]
        t = mt.trade_stats(h, rets, s.index.max() if span[1] > s.index.max() else span[1], COST)
        out.update({k: t[k] for k in ("n_round_trips", "trades_per_year", "avg_holding_weeks", "trade_win_rate",
                                      "avg_profit_per_trade")})
    return out


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    panel, uni, rets, last = load_panel()
    end = str(last)
    fret = factor_returns(uni, end)
    fret.to_csv(OUT / "sg_factor_returns.csv", index=False)
    done = set(rets.loc[rets["next_execution_date"] <= end, "signal_date"])
    dates = sorted(d for d in uni["signal_date"].unique() if d >= START and d in done)
    print("weeks", len(dates), dates[0], dates[-1], "universe/week",
          round(uni[uni.signal_date.isin(dates)].groupby("signal_date").size().mean(), 1))
    cal = uni[uni.signal_date.isin(dates)].groupby("signal_date")[["execution_date", "next_execution_date"]].first()
    sti = sti_weekly(cal.reset_index())
    ewu = uni[uni.signal_date.isin(dates)].groupby("signal_date")["forward_return"].mean()
    sig = stock_sigma(rets)

    rows, paths, decs = [], {}, []
    for pool, select, method, sw in itertools.product(POOLS, SELECTS, METHODS, STOCKW):
        path, hold, cons, dec = run_config(uni, fret, rets, sig, dates, pool, select, method, sw, end)
        tag = f"{pool}_{select}_{method}_{sw}"
        net = mt.net_returns(path, COST)
        paths[tag] = net
        paths[tag + "__stress30"] = mt.net_returns(path, STRESS)
        paths[tag + "__gross"] = path["gross_return"]
        decs += [{"tag": tag, **d} for d in dec if method == "IV" or True]
        row = {"tag": tag, "pool": pool, "select": select, "method": method, "stockw": sw,
               "avg_names": float(cons["n_held"].mean()) if "n_held" in cons else np.nan,
               "ann_turnover": float(path["turnover"].mean() * 52) if "turnover" in path else np.nan}
        for lab, span in (("tr", TRAIN), ("va", VALID), ("x26", EXT)):
            row.update({f"{lab}_{k}": v for k, v in block(net, span, hold, rets, sti).items()})
        row["va_ann_return_stress30"] = block(paths[tag + "__stress30"], VALID).get("ann_return")
        rows.append(row)
    print(f"grid done {time.time() - t0:.0f}s")
    res = pd.DataFrame(rows)
    for name, r in (("STI_TR", sti), ("EW_UNIVERSE_gross", ewu)):
        row = {"tag": name}
        for lab, span in (("tr", TRAIN), ("va", VALID), ("x26", EXT)):
            row.update({f"{lab}_{k}": v for k, v in block(r, span, bench=sti).items()})
        res = pd.concat([res, pd.DataFrame([row])], ignore_index=True)
    res.to_csv(OUT / "sg_grid_results.csv", index=False)
    P = pd.DataFrame(paths)
    P["STI_TR"], P["EW_UNIVERSE_gross"] = sti, ewu
    P.to_csv(OUT / "sg_weekly_returns.csv")
    pd.DataFrame(decs).drop_duplicates().to_csv(OUT / "sg_annual_decisions.csv", index=False)

    ref = "P7_T3L3_IV_EQ"
    g = res[res["pool"].notna()]
    rc = g.loc[g.tag == ref, "tr_calmar"].iloc[0]
    cand = g[g["tr_calmar"] >= rc - 1e-12].sort_values("tr_sharpe", ascending=False)
    pick = cand.iloc[0]["tag"]
    rho = g[["tr_sharpe", "va_sharpe"]].corr(method="spearman").iloc[0, 1]
    va = (P.index >= VALID[0]) & (P.index <= VALID[1])
    sigrows = []
    for tag in dict.fromkeys([ref, pick]):
        for nm in ("STI_TR", ref):
            if nm == tag:
                continue
            a, b = P[tag][va], P[nm][va]
            obs, p, ci = boot_sharpe_diff(a, b, pd.Series(0.0, index=a.index))
            sigrows.append({"tag": tag, "vs": nm, "ann_active": float((a - b).mean() * 52),
                            "nw_t": nw_t((a - b).to_numpy()), "sharpe_diff": obs, "boot_p": p,
                            "ci_lo": ci[0], "ci_hi": ci[1]})
    pd.DataFrame(sigrows).to_csv(OUT / "sg_significance.csv", index=False)
    with open(OUT / "sg_summary.txt", "w") as f:
        f.write(f"weeks {len(dates)} {dates[0]}..{dates[-1]}\nreplica {ref} train calmar {rc:.4f}\n"
                f"eligible {len(cand)}: {cand['tag'].tolist()}\nSELECTED {pick}\n"
                f"spearman(train sharpe, valid sharpe) {rho:.3f}\n")
    print(open(OUT / "sg_summary.txt").read())
    pd.set_option("display.width", 250)
    c = ["tr_ann_return", "tr_sharpe", "tr_max_drawdown", "tr_calmar", "va_ann_return", "va_ann_vol", "va_sharpe",
         "va_max_drawdown", "va_calmar", "va_beta", "x26_ann_return", "x26_sharpe", "x26_max_drawdown"]
    print(res.set_index("tag")[c].round(3).to_string())
    print(pd.DataFrame(sigrows).round(3).to_string())


if __name__ == "__main__":
    main()
