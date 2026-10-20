"""SANDBOX: adaptive Top-K strategy on the SGX_TEAM_HANDOFF data (full SGX universe incl. delisted).

Data (private, WRDS/Compustat-Global-derived, internal only): SGX_TEAM_HANDOFF/data
  sgx_weekly_factors.parquet   26 frozen factor scores, signal-time market data, dated GICS
  sgx_daily_market.parquet     daily prices; available_adjusted_price includes dividends (checked on DBS)
Benchmark: STI total return (.TRISTI) from the earlier SGX_comprehensive package.

PRE-REGISTERED DESIGN (written before running)
Universe at signal date S (Friday): security class ORDINARY or REIT; quote observed in the signal
  week ('Fresh'); last SGD close <= S >= 0.20; 30-day average traded value >= SGD 100k; market cap
  available; all six family scores available.
Six families (frozen package scores, re-standardised within the eligible universe each week):
  Quality    mean of roe, roa, gross_profitability scores (>= 2 of 3 required)
  Momentum   momentum_12_1        Low Risk   volatility_60d      Investment   asset_growth
  Value      book_to_price        Size       -ln(market cap), winsorised 1/99%  (SMALL = high;
             the 'large' variant uses +ln)
  Factors were NOT chosen from the single-factor results; they mirror the six families used before.
Returns: execution E_t = first SGX trading day after S_t; holding return = adjusted close E_t ->
  E_{t+1} (last available adjusted close on/before each date; a delisted stock keeps its last
  price, i.e. no delisting haircut -- stated limitation).
Strategy (teammate spec): annual Top-K by 3y mean/std of top-30% minus bottom-30% weekly factor
  returns, IV factor weights (52w, 5-80%), top 20% composite, buffer 20/30, 30% cap per historical
  GICS sector, equal weight, weekly, 10 bp one-way (30 bp also reported). Sharpe with rf = 0.
Runs: Top-3 / Top-4  x  Size small / large; benchmarks STI TR and equal-weight eligible universe.
Periods: PRE 2016-18, OBS 2019-20, EVAL 2021-01 .. 2026-09-30, EVAL 2021-25.
LIVE PERIOD 2026-10-01 .. 2026-10-20 IS SEALED: no return ending after 2026-09-30 is used
(SG_EVAL_END).  Note: a first run on 2026-10-19 had used data to mid-October; superseded.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import numpy as np
import pandas as pd

from src.adaptive_strategy.composite_score import ScoreConfig, calculate_scores
from src.adaptive_strategy.factor_selection import SelectionConfig, select_factors
from src.adaptive_strategy.factor_weighting import WeightingConfig, calculate_selected_weights
from src.sandbox.adaptive_lab import boot_sharpe_diff, nw_t
from src.strategy import factor_returns as fr
from src.strategy import metrics as mt
from src.strategy import portfolio as pf

TEAM = Path(os.environ.get("SG_TEAM", "SGX_TEAM_HANDOFF"))
OUT = Path(os.environ.get("SG_TEAM_OUT", "outputs/sandbox/sg_team_lab"))
PERIODS = {"PRE_2016_18": ("2016-01-01", "2018-12-31"), "OBS_2019_20": ("2019-01-01", "2020-12-31"),
           "EVAL_2021_26": ("2021-01-01", "2026-09-30"), "EVAL_2021_25": ("2021-01-01", "2025-12-31")}
FAM = {"Quality": "Q", "Momentum": "M", "Low Risk": "LR", "Investment": "INV", "Value": "VAL", "Size": "SIZE"}
MIN_PRICE, MIN_ADV = 0.20, 100_000.0
# Live test period 2026-10-01 .. 2026-10-20 is SEALED: no holding period may end after EVAL_END.
EVAL_END = os.environ.get("SG_EVAL_END", "2026-09-30")


def read_parquet(path: Path, cols: list[str]) -> pd.DataFrame:
    """pyarrow if available, else the DuckDB CLI (cloud sandbox)."""
    try:
        return pd.read_parquet(path, columns=cols)
    except Exception:
        duck = os.environ.get("DUCKDB", "/opt/duck/duckdb")
        tmp = OUT / f"_{path.stem}.csv"
        sel = ", ".join(f'"{c}"' for c in cols)
        subprocess.run([duck, "-c", f"COPY (SELECT {sel} FROM '{path}') TO '{tmp}' (HEADER)"], check=True)
        df = pd.read_csv(tmp, low_memory=False)
        tmp.unlink()
        return df


def load():
    cache = OUT / "_team_cache.pkl"
    if cache.exists():
        return pd.read_pickle(cache)
    sc = ["roe", "roa", "gross_profitability", "momentum_12_1", "volatility_60d", "asset_growth", "book_to_price"]
    w = read_parquet(TEAM / "data/sgx_weekly_factors.parquet",
                     ["gvkey", "iid", "signal_date", "quote_observation_state", "marketcap_sgd_candidate",
                      "security_class_candidate", "historical_gics_sector", "average_traded_value__raw_value"]
                     + [f"{f}__score" for f in sc])
    d = read_parquet(TEAM / "data/sgx_daily_market.parquet",
                     ["gvkey", "iid", "date", "price_sgd", "available_adjusted_price"])
    for x in (w, d):
        x["PERMNO"] = x["gvkey"].astype(str) + "_" + x["iid"].astype(str)
    w["signal_date"] = pd.to_datetime(w["signal_date"]).dt.normalize()
    d["date"] = pd.to_datetime(d["date"]).dt.normalize()
    pd.to_pickle((w, d), cache)
    return w, d


def build(w, d):
    # trading calendar = days with a broad cross-section of observations
    cnt = d.groupby("date").size()
    tdays = cnt[cnt >= 100].index.sort_values()
    sig = np.sort(w["signal_date"].unique())
    exe = tdays[np.searchsorted(tdays.values, sig, side="right").clip(max=len(tdays) - 1)]
    cal = pd.DataFrame({"signal_date": sig, "execution_date": exe})
    cal = cal[cal["execution_date"] > cal["signal_date"]].drop_duplicates("execution_date").reset_index(drop=True)
    cal["next_execution_date"] = cal["execution_date"].shift(-1)
    cal = cal.dropna().reset_index(drop=True)

    # adjusted price at each execution date (last value on/before), price at signal date
    adj = d.dropna(subset=["available_adjusted_price"]).pivot_table(
        index="date", columns="PERMNO", values="available_adjusted_price", aggfunc="last").sort_index()
    px = d.dropna(subset=["price_sgd"]).pivot_table(
        index="date", columns="PERMNO", values="price_sgd", aggfunc="last").sort_index()
    adj_f, px_f = adj.ffill(), px.ffill()
    ae = adj_f.reindex(cal["execution_date"], method="ffill")
    an = adj_f.reindex(cal["next_execution_date"], method="ffill")
    ret = pd.DataFrame(an.to_numpy() / ae.to_numpy() - 1.0, index=cal["signal_date"], columns=adj.columns)
    rets = ret.stack(future_stack=True).rename("execution_to_next_execution_total_return").reset_index()
    rets.columns = ["signal_date", "PERMNO", "execution_to_next_execution_total_return"]
    rets = rets.merge(cal[["signal_date", "next_execution_date"]], on="signal_date")
    rets["usable"] = np.isfinite(rets["execution_to_next_execution_total_return"]).astype(int)
    psig = px_f.reindex(cal["signal_date"], method="ffill").stack(future_stack=True).rename("price_sgd_signal").reset_index()
    psig.columns = ["signal_date", "PERMNO", "price_sgd_signal"]

    u = w.merge(cal, on="signal_date", how="inner").merge(psig, on=["signal_date", "PERMNO"], how="left")
    ok = (u["security_class_candidate"].isin(["ORDINARY_CURRENT_HINT", "REIT_CURRENT_HINT"])
          & u["quote_observation_state"].eq("Fresh")
          & (u["price_sgd_signal"] >= MIN_PRICE)
          & (u["average_traded_value__raw_value"] >= MIN_ADV)
          & (u["marketcap_sgd_candidate"] > 0))
    u = u[ok].copy()
    qcols = ["roe__score", "roa__score", "gross_profitability__score"]
    u["Q"] = u[qcols].mean(axis=1).where(u[qcols].notna().sum(axis=1) >= 2)
    u["M"], u["LR"] = u["momentum_12_1__score"], u["volatility_60d__score"]
    u["INV"], u["VAL"] = u["asset_growth__score"], u["book_to_price__score"]
    u["lnmc"] = np.log(u["marketcap_sgd_candidate"])
    u = u.dropna(subset=["Q", "M", "LR", "INV", "VAL", "lnmc"]).copy()

    def z(s):
        lo, hi = s.quantile(0.01), s.quantile(0.99)
        s = s.clip(lo, hi)
        return (s - s.mean()) / s.std(ddof=0)
    g = u.groupby("signal_date")
    for c in ("Q", "M", "LR", "INV", "VAL"):
        u[c] = g[c].transform(z)
    u["SIZE_small"] = g["lnmc"].transform(lambda s: z(-s))
    u["SIZE_large"] = -u["SIZE_small"]
    for c in ("Q", "M", "LR", "INV", "VAL", "SIZE_small", "SIZE_large"):
        u[f"{c}__available"] = np.isfinite(u[c]).astype(int)
    for c in ("signal_date", "execution_date", "next_execution_date"):
        u[c] = u[c].dt.strftime("%Y-%m-%d")
    for c in ("signal_date", "next_execution_date"):
        rets[c] = pd.to_datetime(rets[c]).dt.strftime("%Y-%m-%d")
    u["historical_gics_sector"] = u["historical_gics_sector"].fillna("UNKNOWN").astype(str)
    return u, rets, cal


def factor_returns(u, rets, cols, end):
    dd = u.merge(rets[["signal_date", "PERMNO", "execution_to_next_execution_total_return"]],
                 on=["signal_date", "PERMNO"], how="left")
    dd["execution_feasible"] = np.isfinite(dd["execution_to_next_execution_total_return"]).astype(int)
    dd["unavailable_reason"] = ""
    rows = []
    for s, wk in dd.groupby("signal_date", sort=True):
        nx = str(wk["next_execution_date"].iloc[0])
        ok_end = nx <= end
        for fam, col in cols.items():
            t, _ = fr.leg_return(fr.pick_leg(wk, col, True)) if ok_end else (None, 0)
            b, _ = fr.leg_return(fr.pick_leg(wk, col, False)) if ok_end else (None, 0)
            av = t is not None and b is not None
            rows.append({"signal_date": s, "next_execution_date": nx if ok_end else "", "family": fam,
                         "factor_return_gross": (t - b) if av else np.nan, "factor_return_available": int(av)})
    return pd.DataFrame(rows)


def run(u, rets, cols, end, dates, k):
    fret = factor_returns(u, rets, cols, end)
    cal = pd.to_datetime(pd.Series(dates))
    yrs = pd.to_datetime(u["signal_date"]).dt.year
    parts, decs = [], []
    for year, cutoff in cal.groupby(cal.dt.year).min().items():
        sel, _ = select_factors(fret, cutoff, list(cols), SelectionConfig(lookback_years=3, top_k=k, min_coverage=0.65))
        wt, _ = calculate_selected_weights(fret, cutoff, sel, WeightingConfig(), covariance_families=list(cols))
        sub = u[(yrs == year) & u["signal_date"].isin(dates)]
        parts.append(calculate_scores(sub, sel, wt["IV"], ScoreConfig("selected_only"), factor_columns=cols))
        decs.append({"year": year, "selected": ", ".join(sel), **{f"w_{f}": round(v, 3) for f, v in wt["IV"].items()}})
    s = pd.concat(parts, ignore_index=True)
    s["signal_date"] = pd.to_datetime(s["signal_date"]).dt.strftime("%Y-%m-%d")
    hold, cons = pf.build_holdings(s, use_buffer=True)
    path = mt.weekly_path(hold, rets, end).set_index("signal_date")
    return path, hold, cons, decs, fret


def sti_weekly(cal):
    sti_file = Path(os.environ.get("SG_DATA", "SGX_comprehensive_2006_2026_handoff")) / "benchmark/STI_Total_Return_LSEG_export.xlsx"
    x = pd.read_excel(sti_file, header=None)
    dt = pd.to_datetime(x[0], errors="coerce", format="mixed")
    v = pd.to_numeric(x[1], errors="coerce")
    lvl = pd.Series(v[dt.notna() & v.notna()].to_numpy(), index=dt[dt.notna() & v.notna()]).sort_index()
    lvl = lvl[~lvl.index.duplicated()]
    out = {}
    for s, e, n in cal[["signal_date", "execution_date", "next_execution_date"]].itertuples(index=False):
        if n > lvl.index.max():
            continue
        out[s.strftime("%Y-%m-%d")] = lvl.loc[:n].iloc[-1] / lvl.loc[:e].iloc[-1] - 1
    return pd.Series(out)


def perf(r, span, hold=None, rets=None):
    s = r[(r.index >= span[0]) & (r.index <= span[1])].dropna()
    p = mt.performance(s)
    o = {k: p.get(k) for k in ("ann_return", "ann_vol", "sharpe", "sortino", "max_drawdown", "calmar",
                               "drawdown_episodes", "longest_drawdown_weeks", "period_hit_rate")}
    if hold is not None:
        h = hold[hold["signal_date"].between(span[0], span[1])]
        t = mt.trade_stats(h, rets, span[1], 0.001)
        o.update({k: t[k] for k in ("n_round_trips", "avg_holding_weeks", "trade_win_rate", "avg_profit_per_trade")})
    return o


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    w, d = load()
    print(f"loaded {time.time() - t0:.0f}s: weekly {len(w):,}, daily {len(d):,}")
    u, rets, cal = build(w, d)
    end = min(rets.loc[rets["usable"] == 1, "next_execution_date"].max(), EVAL_END)
    # only weeks whose holding period ends on or before EVAL_END (live period stays sealed)
    nxt = dict(zip(u["signal_date"], u["next_execution_date"]))
    dates = sorted(x for x in u["signal_date"].unique() if x >= "2016-01-01" and nxt[x] <= end)
    cal = cal[cal["next_execution_date"] <= pd.Timestamp(end)].reset_index(drop=True)
    print(f"evaluation ends {end}; last signal {dates[-1]} (holding ends {nxt[dates[-1]]})")
    per_week = u[u.signal_date.isin(dates)].groupby("signal_date").size()
    print("eligible stocks / week by year:", per_week.groupby(per_week.index.str[:4]).mean().round(0).to_dict())
    print("delisted-ever names in eligible set:",
          int(u[u.signal_date.isin(dates)]["PERMNO"].nunique()), "unique securities")
    sti = sti_weekly(cal)
    ew = u[u.signal_date.isin(dates)].merge(rets, on=["signal_date", "PERMNO"])
    ewu = ew.groupby("signal_date")["execution_to_next_execution_total_return"].mean()

    base = {"Quality": "Q", "Momentum": "M", "Low Risk": "LR", "Investment": "INV", "Value": "VAL"}
    rows, paths, decs = [], {}, []
    for size in ("small", "large"):
        cols = {**base, "Size": f"SIZE_{size}"}
        for k in (3, 4):
            path, hold, cons, dec, fret = run(u, rets, cols, end, dates, k)
            tag = f"TEAM_6F_{size}_Top{k}"
            for c in (0.001, 0.003):
                paths[f"{tag}@{int(c * 1e4)}bp"] = mt.net_returns(path, c)
            net = paths[f"{tag}@10bp"]
            row = {"strategy": tag, "avg_names": float(cons["n_held"].mean()),
                   "ann_turnover": float(path["turnover"].mean() * 52)}
            for pn, sp in PERIODS.items():
                row.update({f"{pn}_{kk}": v for kk, v in perf(net, sp, hold if pn == "EVAL_2021_26" else None, rets).items()})
            row["EVAL_2021_26_ann_return_30bp"] = perf(paths[f"{tag}@30bp"], PERIODS["EVAL_2021_26"])["ann_return"]
            rows.append(row)
            decs += [{"strategy": tag, **x} for x in dec]
            if k == 3:
                fret.assign(size=size).to_csv(OUT / f"factor_returns_{size}.csv", index=False)
            print(f"{tag} done {time.time() - t0:.0f}s", flush=True)
    for name, r in (("STI_TR", sti), ("EW_UNIVERSE_gross", ewu)):
        row = {"strategy": name}
        for pn, sp in PERIODS.items():
            row.update({f"{pn}_{kk}": v for kk, v in perf(r, sp).items()})
        rows.append(row)
    used = {i for p in paths.values() for i in p.dropna().index}
    assert all(nxt[i] <= end for i in used), "live period leaked"
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "sg_team_results.csv", index=False)
    pd.DataFrame(decs).to_csv(OUT / "sg_team_annual_decisions.csv", index=False)
    P = pd.DataFrame(paths)
    P["STI_TR"], P["EW_UNIVERSE_gross"] = sti, ewu
    P = P[P.index.isin(dates)]
    P.to_csv(OUT / "sg_team_weekly_returns.csv")
    per_week.rename("eligible_stocks").to_csv(OUT / "sg_team_universe_size.csv")

    ev = (P.index >= PERIODS["EVAL_2021_26"][0]) & (P.index <= PERIODS["EVAL_2021_26"][1])
    sg = []
    for tag in ("TEAM_6F_small_Top3", "TEAM_6F_small_Top4"):
        for vs in ("STI_TR", "TEAM_6F_small_Top3@10bp"):
            if vs.startswith(tag):
                continue
            a, b = P[f"{tag}@10bp"][ev], P[vs][ev]
            ok = a.notna() & b.notna()
            o, p, ci = boot_sharpe_diff(a[ok], b[ok], pd.Series(0.0, index=a[ok].index), n=3000)
            sg.append({"strategy": tag, "vs": vs.replace("@10bp", ""), "ann_active": float((a - b)[ok].mean() * 52),
                       "nw_t": nw_t((a - b)[ok].to_numpy()), "sharpe_diff": o, "boot_p": p})
    pd.DataFrame(sg).to_csv(OUT / "sg_team_significance.csv", index=False)
    pd.set_option("display.width", 250)
    c = ["strategy", "avg_names", "PRE_2016_18_sharpe", "OBS_2019_20_sharpe", "OBS_2019_20_calmar",
         "EVAL_2021_26_ann_return", "EVAL_2021_26_ann_vol", "EVAL_2021_26_sharpe", "EVAL_2021_26_max_drawdown",
         "EVAL_2021_26_calmar", "EVAL_2021_26_ann_return_30bp"]
    print(res[c].round(3).to_string(index=False))
    print(pd.DataFrame(decs).pivot(index="year", columns="strategy", values="selected").to_string())
    print(pd.DataFrame(sg).round(3).to_string(index=False))
    print(f"total {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
