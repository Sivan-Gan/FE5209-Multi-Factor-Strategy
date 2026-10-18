"""SANDBOX factor lab: replace Low Risk / Investment with other factor dimensions and rerun
the frozen FMOM engine on each factor set.   (NOT the frozen strategy, NOT a clean OOS test)

    python -m src.sandbox.factor_lab

Isolation: reads the frozen pipeline and handoff data, writes only to outputs/sandbox/factor_lab/.

STATUS: POST-HOC EXPLORATION.  The idea (drop Low Risk and Investment) comes from the 2021-2025
diagnosis, so 2021-2025 is NOT out-of-sample for these sets.  To limit the damage the candidate
factors and factor sets below are fixed in advance, each is run once and all are reported; the
in-sample (<= 2020) evidence of every new factor is shown next to the out-of-sample one.

Engine (identical rules to the frozen strategy, generalised to any K):
  factor return  = top 30% - bottom 30% of eligible stocks with the score, equal weight,
                   executed stocks only, E_t -> E_{t+1}                  (Step 16 rule)
  FMOM weights   = J=26, risk-adjusted by 104-week sigma, lambda=0.5, last-valid window rule
  composite      = sum_k w_k F_k, all K factors required                (min factors = K)
  portfolio      = top 20%, equal weight, buffer 20/30, 30% sector cap  (portfolio.py)
  accounting     = metrics.py, frozen one-way cost
The engine is checked by re-running the frozen K=4 set: it must reproduce the frozen weekly path.

Candidate factors (score = higher is better, cross-sectional z-score each week):
  QUALITY, MOMENTUM_12_1, LOWRISK, INVESTMENT   frozen final factors
  VALUE        archive VALUE_CURRENT (Gate A research spec, sector + size neutral)
  DIVIDEND     archive DIVIDEND_CURRENT (trailing ordinary cash yield)
  HIGH52       NEW: total-return index / its max over the last 52 completed weeks (George-Hwang)
  REVERSAL_1M  NEW: minus the return of the last 4 completed weeks (Jegadeesh)
  New factors use only weeks whose holding period ended on or before S_t, need >= 48 of 52
  (HIGH52) / all 4 (REVERSAL) valid weeks, are winsorised at 1/99% and z-scored per week.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np
import pandas as pd

from src.strategy import composite_score as cs
from src.strategy import explore_variants as ev
from src.strategy import factor_returns as fr
from src.strategy import fmom_weights as fw
from src.strategy import governance as gv
from src.strategy import metrics as mt
from src.strategy import portfolio as pf
from src.strategy import run_gate_b as rg

OUT_DIR = "outputs/sandbox/factor_lab"
HIST_FROM = "2006-01-01"
ARCHIVE_FILE = "handoff/factor_research_archive/data/all_factor_scores_weekly.parquet"
FINAL_COLS = {"QUALITY": ("quality_score", "quality_available"),
              "MOMENTUM_12_1": ("momentum_score", "momentum_available"),
              "LOWRISK": ("lowrisk_score", "lowrisk_available"),
              "INVESTMENT": ("investment_score", "investment_available")}
ARCHIVE_COLS = {"VALUE": "VALUE_CURRENT", "DIVIDEND": "DIVIDEND_CURRENT"}
NEW_FACTORS = ("HIGH52", "REVERSAL_1M")

FACTOR_SETS = {   # fixed in advance
    "FROZEN_K4": ["QUALITY", "MOMENTUM_12_1", "LOWRISK", "INVESTMENT"],
    "K2_QM": ["QUALITY", "MOMENTUM_12_1"],
    "K4_QM_VAL_H52": ["QUALITY", "MOMENTUM_12_1", "VALUE", "HIGH52"],
    "K4_QM_H52_REV": ["QUALITY", "MOMENTUM_12_1", "HIGH52", "REVERSAL_1M"],
    "K5_QM_VAL_H52_REV": ["QUALITY", "MOMENTUM_12_1", "VALUE", "HIGH52", "REVERSAL_1M"],
    "K6_QM_VAL_H52_REV_DIV": ["QUALITY", "MOMENTUM_12_1", "VALUE", "HIGH52", "REVERSAL_1M", "DIVIDEND"],
}
LAMBDAS = (0.5, 0.0)          # FMOM (frozen lambda) and static equal factor weights


# ------------------------------------------------------------- 1. scores
def zscore_week(df: pd.DataFrame, col: str) -> pd.Series:
    """Winsorise at 1/99% and z-score within each signal date (eligible stocks only)."""
    def f(x):
        lo, hi = x.quantile(0.01), x.quantile(0.99)
        x = x.clip(lo, hi)
        sd = x.std(ddof=0)
        return (x - x.mean()) / sd if sd > 0 else x * np.nan
    return df.groupby("signal_date")[col].transform(f)


def weekly_tri_factors(returns: pd.DataFrame, base: pd.DataFrame) -> pd.DataFrame:
    """HIGH52 and REVERSAL_1M from completed weekly total returns (point in time).

    returns: signal_date, next_execution_date, PERMNO, execution_to_next_execution_total_return, usable
    base:    signal_date, PERMNO (the stocks / weeks to score)
    For signal date S_t only weeks u with next_execution_date <= S_t are used.
    """
    r = returns[["signal_date", "next_execution_date", "PERMNO", "execution_to_next_execution_total_return",
                 "usable"]].copy()
    r["ret"] = np.where(r["usable"] == 1, r["execution_to_next_execution_total_return"], np.nan)
    dates = sorted(base["signal_date"].unique())
    nxt = r.dropna(subset=["next_execution_date"]).groupby("signal_date")["next_execution_date"].max()
    wide = r.pivot(index="signal_date", columns="PERMNO", values="ret").sort_index()
    ends = nxt.reindex(wide.index).fillna("9999-99-99").to_numpy(dtype=object)
    R = wide.to_numpy(dtype=float)
    valid = np.isfinite(R)
    logr = np.log1p(np.where(valid, R, 0.0))
    cum = np.cumsum(logr, axis=0)                          # log TRI at the END of week u
    cols = wide.columns.to_numpy()
    out = []
    for s_t in dates:
        done = np.flatnonzero(ends <= s_t)
        if len(done) < 52:
            continue
        last = done[-1]
        w52 = done[-52:]
        n_valid52 = valid[w52].sum(axis=0)
        high = np.max(cum[w52], axis=0)
        h52 = np.exp(cum[last] - high)                     # 1 = at the high
        w4 = done[-4:]
        n_valid4 = valid[w4].sum(axis=0)
        rev = -(np.exp(cum[last] - (cum[w4[0] - 1] if w4[0] > 0 else 0.0)) - 1.0)
        out.append(pd.DataFrame({"signal_date": s_t, "PERMNO": cols,
                                 "HIGH52_raw": np.where(n_valid52 >= 48, h52, np.nan),
                                 "REVERSAL_1M_raw": np.where(n_valid4 == 4, rev, np.nan)}))
    f = pd.concat(out, ignore_index=True)
    return base[["signal_date", "PERMNO"]].merge(f, on=["signal_date", "PERMNO"], how="left")


def load_scores_all(root: Path, start: str, end: str, returns: pd.DataFrame) -> pd.DataFrame:
    """One row per (signal_date, PERMNO): keys, sector, eligibility and every candidate score."""
    gv.check_window(end, True, root)
    cols = ["signal_date", "execution_date", "PERMNO", "historical_gics_sector", "eligible_universe_flag",
            "research_period"]
    for s, a in FINAL_COLS.values():
        cols += [s, a]
    f = pd.read_parquet(root / cs.SCORES_FILE, columns=cols,
                        filters=[("signal_date", ">=", start), ("signal_date", "<=", end)])
    for c in ("signal_date", "execution_date", "PERMNO", "historical_gics_sector"):
        f[c] = f[c].astype(str)
    for name, (s, a) in FINAL_COLS.items():
        f[name] = np.where((f[a] == 1) & np.isfinite(f[s]), f[s], np.nan)
    arc = pd.read_parquet(root / ARCHIVE_FILE, columns=["signal_date", "PERMNO", *ARCHIVE_COLS.values()],
                          filters=[("signal_date", ">=", start), ("signal_date", "<=", end)])
    arc["signal_date"], arc["PERMNO"] = arc["signal_date"].astype(str), arc["PERMNO"].astype(str)
    f = f.merge(arc.rename(columns={v: k for k, v in ARCHIVE_COLS.items()}), on=["signal_date", "PERMNO"],
                how="left")
    f = f.merge(weekly_tri_factors(returns, f), on=["signal_date", "PERMNO"], how="left")
    elig = f["eligible_universe_flag"] == 1
    for name in NEW_FACTORS:
        f[name] = np.nan
        f.loc[elig, name] = zscore_week(f[elig], f"{name}_raw")
    return f


# -------------------------------------------------- 2. factor returns (Step 16 rule)
def factor_returns(scores: pd.DataFrame, raw_returns: pd.DataFrame, factors: list[str], end: str) -> pd.DataFrame:
    data = scores.merge(raw_returns, on=["signal_date", "PERMNO"], how="left")
    data["execution_feasible"] = data["execution_feasible"].fillna(0).astype(int)
    rows = []
    for signal_date, week in data.groupby("signal_date", sort=True):
        nx = week["next_execution_date"].dropna()
        nx = str(nx.iloc[0]) if len(nx) else ""
        ok_end = bool(nx) and nx <= end
        for name in factors:
            el = week[(week["eligible_universe_flag"] == 1) & np.isfinite(week[name])]
            top, bot = fr.pick_leg(el, name, True), fr.pick_leg(el, name, False)
            t, _ = fr.leg_return(top) if ok_end else (None, 0)
            b, _ = fr.leg_return(bot) if ok_end else (None, 0)
            avail = t is not None and b is not None
            rows.append({"signal_date": signal_date, "next_execution_date": nx, "family": name,
                         "factor_return_gross": (t - b) if avail else np.nan,
                         "factor_return_available": int(avail)})
    return pd.DataFrame(rows)


# ------------------------------------------------------- 3. FMOM weights (any K)
def fmom_weights(fret: pd.DataFrame, factors: list[str], dates: list[str], J: int, lam: float,
                 risk_adjusted: bool) -> pd.DataFrame:
    """Frozen FMOM rule, 'last_valid' reading, for an arbitrary factor list."""
    K = len(factors)
    valid = fret[fret["factor_return_available"] == 1]
    wide = valid.pivot(index="signal_date", columns="family", values="factor_return_gross")
    wide = wide.reindex(index=sorted(fret["signal_date"].unique()), columns=factors)
    nx = fret[fret["next_execution_date"] != ""].groupby("signal_date")["next_execution_date"].first()
    ends = nx.reindex(wide.index).fillna("").to_numpy(dtype=object)
    R = wide.to_numpy(dtype=float)
    need = max(J, fw.RA_WINDOW) if risk_adjusted else J
    rows = []
    for s_t in dates:
        done = np.flatnonzero((ends != "") & (ends <= s_t))
        signal, ok = np.full(K, np.nan), True
        for k in range(K):
            v = done[~np.isnan(R[done, k])]
            if len(v) < need:
                ok = False
                break
            f = R[v[-J:], k].sum()
            if risk_adjusted:
                sd = np.std(R[v[-fw.RA_WINDOW:], k], ddof=1)
                if sd <= 0:
                    ok = False
                    break
                signal[k] = f / sd
            else:
                signal[k] = f
        if ok:
            pos = np.maximum(signal, 0.0)
            w_f = pos / pos.sum() if pos.sum() > 0 else np.full(K, 1.0 / K)
            status = "OK" if pos.sum() > 0 else "FALLBACK_ALL_NONPOSITIVE"
        else:
            w_f, status = np.full(K, 1.0 / K), "FALLBACK_INSUFFICIENT_HISTORY"
        w = (1 - lam) / K + lam * w_f
        rows.append({"signal_date": s_t, "status": status, **{f"w_{n}": w[k] for k, n in enumerate(factors)}})
    return pd.DataFrame(rows)


def composite(scores: pd.DataFrame, weights: pd.DataFrame, factors: list[str]) -> pd.DataFrame:
    d = scores[scores["eligible_universe_flag"] == 1].merge(weights, on="signal_date", how="inner")
    F = d[factors].to_numpy(dtype=float)
    W = d[[f"w_{n}" for n in factors]].to_numpy(dtype=float)
    ok = np.isfinite(F).all(axis=1)
    d["composite_score"] = np.where(ok, (np.nan_to_num(F) * W).sum(axis=1), np.nan)
    d["score_available"] = ok.astype(int)
    return d[["signal_date", "execution_date", "PERMNO", "historical_gics_sector", "composite_score",
              "score_available"]]


# ---------------------------------------------------------------- 4. run
def load_raw_returns(root: Path, start: str, end: str) -> pd.DataFrame:
    r = pd.read_parquet(root / rg.RETURNS_FILE,
                        columns=["signal_date", "next_execution_date", "PERMNO",
                                 "execution_to_next_execution_total_return", "execution_feasible",
                                 "unavailable_reason"],
                        filters=[("signal_date", ">=", start), ("signal_date", "<=", end)])
    for c in ("signal_date", "next_execution_date", "PERMNO"):
        r[c] = r[c].astype(str).replace({"nan": None, "<NA>": None, "None": None})
    return r


def factor_evidence(fret: pd.DataFrame) -> pd.DataFrame:
    """Annualised mean, t-stat and hit rate of each weekly long-short factor return by period."""
    spans = {"2006_2015": ("2006-01-01", "2015-12-31"), "2016_2020": ("2016-01-01", "2020-12-31"),
             "2021_2025": ("2021-01-01", "2025-12-31")}
    rows = []
    for fam, g in fret[fret["factor_return_available"] == 1].groupby("family"):
        row = {"factor": fam}
        for lab, (s, e) in spans.items():
            x = g.loc[g["signal_date"].between(s, e), "factor_return_gross"]
            if len(x) > 10:
                row[f"ann_mean_{lab}"] = x.mean() * 52
                row[f"t_{lab}"] = x.mean() / x.std(ddof=1) * math.sqrt(len(x))
                row[f"hit_{lab}"] = (x > 0).mean()
        rows.append(row)
    return pd.DataFrame(rows)


def run(root: Path) -> dict:
    t0 = time.time()
    cfg = gv.load_config(root)
    p = cfg["parameters"]
    cost = cfg["transaction_cost"]["oneway_bps"] / 1e4
    J, ra = p["fmom_lookback_weeks_J"], p["risk_adjusted_fmom"]
    raw = load_raw_returns(root, HIST_FROM, ev.RUN_TO)
    returns = rg.load_returns(root, HIST_FROM, ev.RUN_TO)
    scores = load_scores_all(root, HIST_FROM, ev.RUN_TO, returns)
    print(f"scores ready ({time.time() - t0:.0f}s): {len(scores)} rows")
    all_f = sorted({f for fs in FACTOR_SETS.values() for f in fs})
    fret = factor_returns(scores, raw, all_f, ev.RUN_TO)
    print(f"factor returns ready ({time.time() - t0:.0f}s)")
    rf = rg.load_weekly_series(root, rg.RF_FILE, "risk_free_return", ev.RUN_FROM, ev.RUN_TO)
    bench = rg.load_weekly_series(root, rg.BENCH_FILE, "benchmark_total_return", ev.RUN_FROM, ev.RUN_TO)
    dates = sorted(d for d in scores["signal_date"].unique() if ev.RUN_FROM <= d <= ev.RUN_TO)
    run_scores = scores[scores["signal_date"].isin(dates)]
    ret_run = returns[returns["signal_date"] >= ev.RUN_FROM]

    summary, weekly, yearly, weights_all, paths = [], [], [], [], {}
    weeks = None
    for set_name, factors in FACTOR_SETS.items():
        for lam in LAMBDAS:
            name = f"{set_name}_{'FMOM' if lam else 'STATIC'}"
            w = fmom_weights(fret, factors, dates, J, lam, ra)
            sc = composite(run_scores, w, factors)
            hold, cons = pf.build_holdings(sc, p["buffer"])
            problems = ev.check_variant(hold, cons)
            path, _ = mt.weekly_path_and_ledger(hold, ret_run, ev.RUN_TO, cost)
            paths[name] = path
            if weeks is None:
                weeks = {k: ev.period_weeks(path, s, e) for k, (s, e) in ev.PERIODS.items()}
            for period, (_, end) in ev.PERIODS.items():
                row, wk = ev.period_metrics(path, hold, ret_run, rf, bench, weeks[period], end, cost)
                summary.append({"variant": name, "factor_set": set_name, "K": len(factors), "lambda": lam,
                                "factors": "+".join(factors), "period": period,
                                "avg_names_held": float(cons["n_held"].mean()),
                                "avg_scored": float(cons["n_scored"].mean()),
                                "checks": "PASS" if not problems else "; ".join(problems), **row})
                weekly.append(wk.assign(variant=name, period=period))
                yr = mt.yearly_returns(wk.set_index("signal_date")["net_return"], wk["signal_date"])
                yearly.append({"variant": name, "period": period, **{str(y): v for y, v in yr.items()}})
            weights_all.append(w.melt(id_vars=["signal_date", "status"], var_name="factor", value_name="weight")
                               .assign(variant=name))
            print(f"  {name:<30} done ({time.time() - t0:.0f}s)  checks {summary[-1]['checks']}")
    for period, (_, end) in ev.PERIODS.items():
        row, wk = ev.period_metrics(ev.bench_path(bench), None, ret_run, rf, bench, weeks[period], end, cost)
        summary.append({"variant": "SP500", "period": period, **row})
        weekly.append(wk.assign(variant="SP500", period=period))
        yr = mt.yearly_returns(wk.set_index("signal_date")["net_return"], wk["signal_date"])
        yearly.append({"variant": "SP500", "period": period, **{str(y): v for y, v in yr.items()}})
    return {"summary": pd.DataFrame(summary), "weekly": pd.concat(weekly, ignore_index=True),
            "yearly": pd.DataFrame(yearly), "weights": pd.concat(weights_all, ignore_index=True),
            "evidence": factor_evidence(fret), "fret": fret, "paths": paths, "cost": cost}


def reproduce(root: Path, paths: dict) -> list[str]:
    lines = []
    for folder in ("gate_b", "hist_oos"):
        f = root / "outputs/strategy/frozen" / folder / "weekly_path.csv"
        if not f.exists():
            lines.append(f"{folder}: reference not found")
            continue
        ref = pd.read_csv(f, dtype={"signal_date": str}).set_index("signal_date")["gross_return"]
        mine = paths["FROZEN_K4_FMOM"].set_index("signal_date")["gross_return"].reindex(ref.index)
        d = float((ref - mine).abs().max())
        lines.append(f"FROZEN_K4_FMOM vs frozen/{folder}: {len(ref)} weeks, max |gross diff| {d:.1e} -> "
                     f"{'MATCH' if d < 1e-10 else 'DIFFERENT'}")
    return lines


def main() -> None:
    root = gv.project_root()
    res = run(root)
    out = root / OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    res["summary"].assign(status="POST_HOC_SANDBOX").to_csv(out / "summary.csv", index=False)
    res["yearly"].to_csv(out / "yearly.csv", index=False)
    res["weekly"].to_csv(out / "weekly.csv", index=False)
    res["weights"].to_csv(out / "fmom_weights.csv", index=False)
    res["evidence"].to_csv(out / "factor_evidence.csv", index=False)
    res["fret"].to_csv(out / "factor_returns.csv", index=False)
    repro = reproduce(root, res["paths"])
    pd.set_option("display.width", 250)
    print("\nFactor long-short evidence (annualised mean / t-stat):")
    print(res["evidence"].round(3).to_string(index=False))
    show = ["variant", "ann_return", "ann_vol", "sharpe", "max_drawdown", "active_ann_return",
            "information_ratio", "avg_weekly_turnover"]
    for period in ev.PERIODS:
        print(f"\n=== {period} ===")
        print(res["summary"][res["summary"]["period"] == period][show].round(4).to_string(index=False))
    print("\n" + "\n".join(repro))
    print("STATUS: POST-HOC SANDBOX - frozen strategy unchanged; 2021-2025 is not out-of-sample for these sets.")


if __name__ == "__main__":
    main()
