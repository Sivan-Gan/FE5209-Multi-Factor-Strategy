"""SANDBOX adaptive lab, stage 2: improvement grid for the Adaptive Top-3 strategy.

Goal (group request 2026-10-18): raise Sharpe and Calmar of the adaptive strategy, re-using ideas
from the earlier sandbox (factor lab: drop Size / add price factors / static all-factor; FMOM lab:
risk management), without touching the original branch code.

PRE-REGISTRATION (written before any variant was run)
-----------------------------------------------------
Engine: the adaptive branch's own functions (select_factors, calculate_selected_weights,
calculate_scores, portfolio.build_holdings, metrics), same universe (common seven-factor),
top 20% equal weight, buffer 20/30, 30% sector cap, 10 bp one-way, weekly, 2016-2025.
Engine check: pool P7 / Top3 / 3y / IV / no overlay must equal the branch's WF_3Y_Top3_IV path.

Grid (240 variants = 4 pools x 5 selection rules x 3 weightings x 4 overlays):
  pool     P7  original 7 (Q, M, LowRisk, Inv, Value, Div, Size)
           P6  P7 without Size                       (Size drag in 2023-24 was flagged earlier)
           P9  P7 + High52 + Reversal 1M            (factor lab price factors)
           P8  P9 without Size
  select   T3L3, T3L5, T4L3, T4L5 (top-K by 3y / 5y mean/std of weekly factor returns, annual)
           ALL (every factor in the pool, no selection; annual weights only)
  weight   EW, IV, MV (branch definitions, 52-week window, 5%-80% bounds)
  overlay  NONE
           VT     exposure = min(1, 15% / realised vol), vol = std of the last 26 completed weekly
                  strategy net returns (weeks with next_execution_date <= signal date, i.e. lag 2)
                  x sqrt(52); exposure 1 until 26 such weeks exist; rest earns the risk-free rate
           TREND  exposure 0.5 when the S&P 500 total-return index (completed weeks only) is below
                  its 40-week moving average, else 1
           VT_TREND  product of the two
           overlay cost: 10 bp x |change in exposure| each week.
Selection rule (training 2016-2020 only):
  SELECTED = highest training net Sharpe among variants whose training Calmar >= the original
  WF_3Y_Top3_IV training Calmar. Also reported: top 3 by that rule, and the highest training Calmar.
  2021-2025 is then opened once for every variant; all variants are reported.
Significance (2021-2025): Newey-West (4 lags) t-statistic of weekly active return vs the original
and vs the S&P 500; stationary block bootstrap (mean block 8 weeks, 5000 draws) of the Sharpe
difference.

STATUS: POST-HOC SANDBOX. 2021-2025 has been looked at before (the original strategy and the
factor-lab sets), so it is not a pristine out-of-sample test; the rule above limits further bias.
"""
from __future__ import annotations

import itertools
import math
import time
import os
from pathlib import Path

import numpy as np
import pandas as pd

from src.adaptive_strategy.composite_score import DEFAULT_FACTORS, ScoreConfig, calculate_scores
from src.adaptive_strategy.factor_selection import SelectionConfig, select_factors
from src.adaptive_strategy.factor_weighting import WeightingConfig, calculate_selected_weights
from src.strategy import metrics as mt
from src.strategy import portfolio as pf
from src.strategy import run_gate_b as rg

ROOT = Path(".")
OUT = Path(os.environ.get("ADAPTIVE_LAB_OUT", "outputs/sandbox/adaptive_lab"))
START, END = "2016-01-01", "2025-12-31"
TRAIN = ("2016-01-01", "2020-12-31")
VALID = ("2021-01-01", "2025-12-31")
COST = 0.001
COLS = {**DEFAULT_FACTORS, "High52": "HIGH52", "Reversal 1M": "REVERSAL_1M"}
P7 = ["Quality", "Momentum", "Low Risk", "Investment", "Value", "Dividend", "Size"]
POOLS = {"P7": P7, "P6": [f for f in P7 if f != "Size"],
         "P9": P7 + ["High52", "Reversal 1M"],
         "P8": [f for f in P7 if f != "Size"] + ["High52", "Reversal 1M"]}
SELECTS = {"T3L3": (3, 3), "T3L5": (3, 5), "T4L3": (4, 3), "T4L5": (4, 5), "ALL": (None, None)}
METHODS = ("EW", "IV", "MV")
OVERLAYS = ("NONE", "VT", "TREND", "VT_TREND")
VT_TARGET, VT_WINDOW, TREND_MA, TREND_EXPO = 0.15, 26, 40, 0.5


# ------------------------------------------------------------ core backtest
def annual_decisions(fret, dates, pool, select):
    k, lb = SELECTS[select]
    cal = pd.to_datetime(pd.Series(dates))
    first = cal.groupby(cal.dt.year).min()
    rows = []
    for year, cutoff in first.items():
        if k is None:
            selected = list(pool)
        else:
            selected, _ = select_factors(fret, cutoff, pool,
                                         SelectionConfig(lookback_years=lb, top_k=k, min_coverage=0.65))
        w, _ = calculate_selected_weights(fret, cutoff, selected, WeightingConfig(), covariance_families=pool)
        for m in METHODS:
            rows.append({"year": int(year), "method": m, "selected": selected, "weights": w[m]})
    return rows


def run_core(base, fret, returns, dates, pool, select):
    """Return {method: weekly path DataFrame} and the annual decisions."""
    need = [COLS[f] for f in pool]
    uni = base
    for c in need:
        uni = uni[(uni[f"{c}__available"] == 1) & np.isfinite(pd.to_numeric(uni[c], errors="coerce"))]
    dec = annual_decisions(fret, dates, pool, select)
    years = pd.to_datetime(uni["signal_date"]).dt.year
    out = {}
    for m in METHODS:
        parts = []
        for d in (x for x in dec if x["method"] == m):
            sub = uni[years == d["year"]]
            parts.append(calculate_scores(sub, d["selected"], d["weights"], ScoreConfig("selected_only"),
                                          factor_columns=COLS))
        scored = pd.concat(parts, ignore_index=True)
        scored["signal_date"] = pd.to_datetime(scored["signal_date"]).dt.strftime("%Y-%m-%d")
        hold, _ = pf.build_holdings(scored, use_buffer=True)
        path = mt.weekly_path(hold, returns, END)
        path["net_return"] = mt.net_returns(path, COST)
        out[m] = path.set_index("signal_date")["net_return"]
    return out, dec


# --------------------------------------------------------------- overlays
def lagged_completed(series: pd.Series) -> pd.Series:
    """Value available at signal date t = information from weeks <= t-2 (holding period ended)."""
    return series.shift(2)


def overlay(r: pd.Series, rf: pd.Series, bench_hist: pd.Series, kind: str) -> pd.Series:
    rf = rf.reindex(r.index).fillna(0.0)
    e = pd.Series(1.0, index=r.index)
    if kind in ("VT", "VT_TREND"):
        vol = r.rolling(VT_WINDOW, min_periods=VT_WINDOW).std(ddof=1) * math.sqrt(52)
        vol = lagged_completed(vol)
        e = e * np.minimum(1.0, VT_TARGET / vol).fillna(1.0)
    if kind in ("TREND", "VT_TREND"):
        idx = (1.0 + bench_hist).cumprod()
        below = (idx < idx.rolling(TREND_MA, min_periods=TREND_MA).mean()).astype(float)
        below = lagged_completed(below).reindex(r.index).fillna(0.0)
        e = e * pd.Series(np.where(below > 0, TREND_EXPO, 1.0), index=r.index)
    de = (e - e.shift(1).fillna(1.0)).abs()
    return e * r + (1.0 - e) * rf - COST * de, e


# ----------------------------------------------------------------- stats
def nw_t(x: np.ndarray, lags: int = 4) -> float:
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    n, mu = len(x), x.mean()
    u = x - mu
    s = u @ u / n
    for l in range(1, lags + 1):
        s += 2 * (1 - l / (lags + 1)) * (u[l:] @ u[:-l]) / n
    return float(mu / math.sqrt(s / n))


def boot_sharpe_diff(a, b, rf, n=5000, block=8, seed=7):
    rng = np.random.default_rng(seed)
    ea, eb = (a - rf).to_numpy(), (b - rf).to_numpy()
    T = len(ea)
    def sh(x):
        return x.mean() / x.std(ddof=1) * math.sqrt(52)
    obs = sh(ea) - sh(eb)
    diffs = np.empty(n)
    p = 1.0 / block
    for i in range(n):
        idx = np.empty(T, dtype=int)
        idx[0] = rng.integers(T)
        for t in range(1, T):
            idx[t] = rng.integers(T) if rng.random() < p else (idx[t - 1] + 1) % T
        diffs[i] = sh(ea[idx]) - sh(eb[idx])
    return obs, float(np.mean(diffs <= 0)), np.percentile(diffs, [2.5, 97.5])


def perf(r, rf, bench, span):
    s = r[(r.index >= span[0]) & (r.index <= span[1])]
    p = mt.performance(s, rf.reindex(s.index), bench.reindex(s.index))
    return {k: p[k] for k in ("ann_return", "ann_vol", "sharpe", "sortino", "max_drawdown", "calmar",
                              "beta", "information_ratio", "active_ann_return")}


def main() -> None:
    t0 = time.time()
    base = pd.read_pickle(OUT / "base.pkl")
    fret = pd.read_pickle(OUT / "factor_returns.pkl")
    returns = rg.load_returns(ROOT, START, END)
    done = set(returns.loc[returns["next_execution_date"].notna()
                           & (returns["next_execution_date"] <= END), "signal_date"])
    base = base[base["signal_date"].between(START, END) & base["signal_date"].isin(done)].copy()
    dates = sorted(base["signal_date"].unique())
    assert len(dates) == 520, len(dates)
    rf = rg.load_weekly_series(ROOT, rg.RF_FILE, "risk_free_return", "2014-01-01", END)
    bench_all = rg.load_weekly_series(ROOT, rg.BENCH_FILE, "benchmark_total_return", "2014-01-01", END)
    bench = bench_all.reindex(dates)

    # engine check against the branch reference path
    ref = pd.read_csv(ROOT / "data/adaptive_strategy/historical_reference_weekly_paths.csv")

    rows, paths, decisions = [], {}, []
    for pname, select in itertools.product(POOLS, SELECTS):
        core, dec = run_core(base, fret, returns, dates, POOLS[pname], select)
        for d in dec:
            decisions.append({"pool": pname, "select": select, "year": d["year"], "method": d["method"],
                              "selected": ", ".join(d["selected"]),
                              **{f"w_{k}": v for k, v in d["weights"].items()}})
        if pname == "P7" and select == "T3L3":
            for m in METHODS:
                rr = ref[ref["strategy"] == f"WF_3Y_Top3_{m}"].set_index("signal_date")["net_return"]
                diff = (core[m].reindex(rr.index) - rr).abs().max()
                print(f"engine check {m}: max |diff| = {diff:.2e}")
                assert diff < 1e-10
        for m, r in core.items():
            for ov in OVERLAYS:
                rr, e = overlay(r, rf, bench_all, ov) if ov != "NONE" else (r, pd.Series(1.0, index=r.index))
                tag = f"{pname}_{select}_{m}_{ov}"
                paths[tag] = rr
                row = {"tag": tag, "pool": pname, "select": select, "method": m, "overlay": ov,
                       "avg_exposure": float(e.mean())}
                for lab, span in (("tr", TRAIN), ("va", VALID)):
                    row.update({f"{lab}_{k}": v for k, v in perf(rr, rf, bench, span).items()})
                rows.append(row)
        print(f"{pname} {select} done  {time.time() - t0:.0f}s", flush=True)

    res = pd.DataFrame(rows)
    pd.DataFrame(paths).to_csv(OUT / "weekly_net_returns.csv")
    pd.DataFrame(decisions).to_csv(OUT / "annual_decisions.csv", index=False)

    # benchmark rows
    for name, r in (("S&P500_TR", bench),):
        row = {"tag": name}
        for lab, span in (("tr", TRAIN), ("va", VALID)):
            row.update({f"{lab}_{k}": v for k, v in perf(r, rf, bench, span).items()})
        res = pd.concat([res, pd.DataFrame([row])], ignore_index=True)

    # selection on training only
    orig = res.loc[res["tag"] == "P7_T3L3_IV_NONE"].iloc[0]
    cand = res[res["pool"].notna() & (res["tr_calmar"] >= orig["tr_calmar"] - 1e-12)]
    cand = cand.sort_values("tr_sharpe", ascending=False)
    top3 = cand.head(3)["tag"].tolist()
    best_calmar = res[res["pool"].notna()].sort_values("tr_calmar", ascending=False).iloc[0]["tag"]
    res["train_rank_sharpe"] = res["tr_sharpe"].rank(ascending=False)
    res["valid_rank_sharpe"] = res["va_sharpe"].rank(ascending=False)
    res.to_csv(OUT / "grid_results.csv", index=False)

    # significance for the picks
    va = lambda s: s[(s.index >= VALID[0]) & (s.index <= VALID[1])]
    rfv = rf.reindex(va(paths["P7_T3L3_IV_NONE"]).index).fillna(0.0)
    sig = []
    for tag in dict.fromkeys(top3 + [best_calmar]):
        a = va(paths[tag])
        for bname, b in (("original", va(paths["P7_T3L3_IV_NONE"])), ("S&P500", va(bench))):
            obs, p, ci = boot_sharpe_diff(a, b, rfv)
            sig.append({"tag": tag, "vs": bname,
                        "ann_active_arith": float((a - b).mean() * 52),
                        "nw_t_active": nw_t((a - b).to_numpy()),
                        "sharpe_diff": obs, "boot_p_diff_le_0": p, "ci_lo": ci[0], "ci_hi": ci[1]})
    sig = pd.DataFrame(sig)
    sig.to_csv(OUT / "significance.csv", index=False)

    rho = res[res["pool"].notna()][["tr_sharpe", "va_sharpe"]].corr(method="spearman").iloc[0, 1]
    with open(OUT / "summary.txt", "w") as f:
        f.write(f"original train calmar {orig['tr_calmar']:.4f} sharpe {orig['tr_sharpe']:.4f}\n")
        f.write(f"eligible by rule: {len(cand)} of {res['pool'].notna().sum()}\n")
        f.write(f"SELECTED: {top3[0]}\nTOP3: {top3}\nBEST TRAIN CALMAR: {best_calmar}\n")
        f.write(f"spearman(train sharpe, valid sharpe) = {rho:.3f}\n")
    print(open(OUT / "summary.txt").read())
    show = ["tag", "tr_ann_return", "tr_sharpe", "tr_calmar", "va_ann_return", "va_ann_vol", "va_sharpe",
            "va_max_drawdown", "va_calmar", "va_beta"]
    pd.set_option("display.width", 250)
    print(res.set_index("tag").loc[["P7_T3L3_IV_NONE", *dict.fromkeys(top3 + [best_calmar]), "S&P500_TR"],
                                   show[1:]].round(4))
    print(sig.round(4))
    print(f"total {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
