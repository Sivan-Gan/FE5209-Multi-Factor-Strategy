"""Performance attribution for the SG adaptive strategy on SGX_TEAM_HANDOFF data.

Strategy: TEAM_6F_small_Top4 (6 families, Size = small cap, Top-4 by 3y, IV weights, top 20%,
buffer, 30% historical-GICS sector cap, weekly, 10 bp).  Live period 2026-10-01.. stays sealed:
everything here uses sg_team_lab's capped calendar (holding periods ending <= 2026-09-30).

Three views (weekly, rf = 0):
 1. Return decomposition vs STI:  net = STI + (EW universe - STI) [universe effect]
                                   + (gross strategy - EW universe) [selection / factor tilt]
                                   - (gross - net) [cost drag]
 2. Factor regression (Newey-West 4 lags):
      r_strategy = a + b_mkt * STI + sum_k b_k * F_k + e
    F_k = weekly top-30% minus bottom-30% equal-weight returns of the six families in the same
    eligible universe (Quality, Momentum, Low Risk, Investment, Value, Size-small).
    Contribution_k = b_k * mean(F_k) * 52.
 3. Brinson-Fama (holdings based) vs the equal-weight eligible universe, historical GICS sectors:
      allocation  = sum_s (w_p,s - w_b,s) * (r_b,s - r_b)
      selection   = sum_s  w_b,s * (r_p,s - r_b,s)
      interaction = sum_s (w_p,s - w_b,s) * (r_p,s - r_b,s)
    summed weekly, reported as annualised arithmetic means (gross of cost).
Also: top / bottom contributing stocks and calendar-year contributions.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import pandas as pd

from src.sandbox import sg_team_lab as T
from src.strategy import metrics as mt

OUT = Path(os.environ.get("SG_ATTR_OUT", "outputs/sandbox/sg_attribution"))
GICS = {"10": "Energy", "15": "Materials", "20": "Industrials", "25": "Consumer Discretionary",
        "30": "Consumer Staples", "35": "Health Care", "40": "Financials", "45": "Information Technology",
        "50": "Communication Services", "55": "Utilities", "60": "Real Estate"}
SPANS = {"EVAL_2021_26": ("2021-01-01", "2026-09-30"), "FULL_2016_26": ("2016-01-01", "2026-09-30"),
         "OBS_2019_20": ("2019-01-01", "2020-12-31")}


def ols_nw(y, X, lags=4):
    X = np.column_stack([np.ones(len(y)), X])
    b = np.linalg.lstsq(X, y, rcond=None)[0]
    e = y - X @ b
    n = len(y)
    XtX_inv = np.linalg.inv(X.T @ X)
    S = (X * e[:, None]).T @ (X * e[:, None])
    for l in range(1, lags + 1):
        w = 1 - l / (lags + 1)
        G = (X[l:] * e[l:, None]).T @ (X[:-l] * e[:-l, None])
        S += w * (G + G.T)
    V = XtX_inv @ S @ XtX_inv
    r2 = 1 - (e @ e) / ((y - y.mean()) @ (y - y.mean()))
    return b, np.sqrt(np.diag(V)), r2


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    w, d = T.load()
    u, rets, cal = T.build(w, d)
    end = min(rets.loc[rets["usable"] == 1, "next_execution_date"].max(), T.EVAL_END)
    nxt = dict(zip(u["signal_date"], u["next_execution_date"]))
    dates = sorted(x for x in u["signal_date"].unique() if x >= "2016-01-01" and nxt[x] <= end)
    cal = cal[cal["next_execution_date"] <= pd.Timestamp(end)].reset_index(drop=True)
    cols = {"Quality": "Q", "Momentum": "M", "Low Risk": "LR", "Investment": "INV", "Value": "VAL",
            "Size": "SIZE_small"}
    path, hold, cons, decs, fret = T.run(u, rets, cols, end, dates, 4)
    gross = path["gross_return"]
    net = mt.net_returns(path, 0.001)
    sti = T.sti_weekly(cal).reindex(net.index)
    uu = u[u.signal_date.isin(dates)].merge(rets[["signal_date", "PERMNO", "execution_to_next_execution_total_return"]],
                                            on=["signal_date", "PERMNO"])
    uu = uu.rename(columns={"execution_to_next_execution_total_return": "r"})
    ewu = uu.groupby("signal_date")["r"].mean().reindex(net.index)
    F = fret[fret["factor_return_available"] == 1].pivot(index="signal_date", columns="family",
                                                         values="factor_return_gross").reindex(net.index)
    F = F[list(cols)]

    rows_dec, rows_reg, rows_brin = [], [], []
    # holdings-level data for Brinson / stock contributions
    h = hold.merge(uu[["signal_date", "PERMNO", "r"]], on=["signal_date", "PERMNO"], how="left")
    h["r"] = h["r"].fillna(0.0)
    secname = lambda x: GICS.get(str(x).split(".")[0], str(x))
    bench = uu.assign(sector=uu["historical_gics_sector"].map(secname))
    h["sector"] = h["sector"].map(secname)
    for name, (a, b) in SPANS.items():
        m = (net.index >= a) & (net.index <= b)
        n_w = m.sum()
        ann = lambda s: float(s[m].mean() * 52)
        # 1. decomposition (arithmetic annualised means, they add up exactly)
        rows_dec.append({"period": name, "weeks": int(n_w),
                         "STI": ann(sti), "universe_effect (EW universe - STI)": ann(ewu - sti),
                         "selection_and_factor_tilt (gross - EW universe)": ann(gross - ewu),
                         "cost_drag (net - gross)": ann(net - gross), "strategy_net": ann(net),
                         "strategy_net_CAGR": float((1 + net[m]).prod() ** (52 / n_w) - 1),
                         "STI_CAGR": float((1 + sti[m]).prod() ** (52 / n_w) - 1)})
        # 2. factor regression
        X = pd.concat([sti, F], axis=1)[m].dropna()
        y = net.reindex(X.index).to_numpy()
        bcoef, se, r2 = ols_nw(y, X.to_numpy())
        names = ["alpha", "Market (STI)"] + list(cols)
        means = [1.0] + list(X.mean().to_numpy())
        for nm, bb, ss, mu in zip(names, bcoef, se, means):
            rows_reg.append({"period": name, "term": nm, "loading": bb, "t_stat_NW": bb / ss,
                             "factor_mean_ann": mu * 52 if nm != "alpha" else np.nan,
                             "contribution_ann": bb * mu * 52, "R2": r2})
        # 3. Brinson vs EW universe by historical GICS sector
        hp = h[h["signal_date"].isin(net.index[m])]
        bp = bench[bench["signal_date"].isin(net.index[m])]
        tot = {"allocation": 0.0, "selection": 0.0, "interaction": 0.0}
        sec_rows = {}
        for s, g in hp.groupby("signal_date"):
            bw = bp[bp.signal_date == s]
            rb = bw["r"].mean()
            wb = bw.groupby("sector").size() / len(bw)
            rbs = bw.groupby("sector")["r"].mean()
            wp = g.groupby("sector")["weight"].sum() / g["weight"].sum()
            rps = g.groupby("sector").apply(lambda x: np.average(x["r"], weights=x["weight"]))
            secs = wb.index.union(wp.index)
            wb, wp = wb.reindex(secs, fill_value=0), wp.reindex(secs, fill_value=0)
            rbs, rps = rbs.reindex(secs).fillna(rb), rps.reindex(secs)
            rps = rps.fillna(rbs)
            alloc = (wp - wb) * (rbs - rb)
            sel = wb * (rps - rbs)
            inter = (wp - wb) * (rps - rbs)
            tot["allocation"] += alloc.sum()
            tot["selection"] += sel.sum()
            tot["interaction"] += inter.sum()
            for sec in secs:
                d0 = sec_rows.setdefault(sec, {"allocation": 0.0, "selection": 0.0, "interaction": 0.0,
                                               "avg_port_weight": 0.0, "avg_bench_weight": 0.0})
                d0["allocation"] += alloc[sec]
                d0["selection"] += sel[sec]
                d0["interaction"] += inter[sec]
                d0["avg_port_weight"] += wp[sec]
                d0["avg_bench_weight"] += wb[sec]
        k = 52 / n_w
        rows_brin.append({"period": name, "sector": "TOTAL", **{x: v * k for x, v in tot.items()}})
        for sec, v in sec_rows.items():
            rows_brin.append({"period": name, "sector": sec,
                              **{x: v[x] * k for x in ("allocation", "selection", "interaction")},
                              "avg_port_weight": v["avg_port_weight"] / n_w,
                              "avg_bench_weight": v["avg_bench_weight"] / n_w})

    dec = pd.DataFrame(rows_dec)
    reg = pd.DataFrame(rows_reg)
    brin = pd.DataFrame(rows_brin)
    # stock contributions in EVAL (weight * return, summed, gross)
    m = (h["signal_date"] >= SPANS["EVAL_2021_26"][0]) & (h["signal_date"] <= SPANS["EVAL_2021_26"][1])
    he = h[m].assign(contrib=lambda x: x["weight"] * x["r"])
    nm_df = T.read_parquet(T.TEAM / "data/sgx_daily_market.parquet", ["gvkey", "iid", "company_name"])
    nm_df["PERMNO"] = nm_df["gvkey"].astype(str) + "_" + nm_df["iid"].astype(str)
    names = nm_df.dropna(subset=["company_name"]).drop_duplicates("PERMNO", keep="last").set_index("PERMNO")["company_name"]
    sc = he.groupby("PERMNO").agg(weeks_held=("signal_date", "nunique"), contribution=("contrib", "sum"),
                                  sector=("sector", "last")).sort_values("contribution")
    sc.insert(0, "company", sc.index.map(names))
    yr = pd.DataFrame({"strategy_net": (1 + net).groupby(net.index.str[:4]).prod() - 1,
                       "STI": (1 + sti).groupby(sti.index.str[:4]).prod() - 1,
                       "EW_universe": (1 + ewu).groupby(ewu.index.str[:4]).prod() - 1})
    yr["excess_vs_STI"] = yr["strategy_net"] - yr["STI"]
    with pd.ExcelWriter(OUT / "sg_attribution.xlsx") as xw:
        dec.to_excel(xw, sheet_name="1_decomposition", index=False)
        reg.to_excel(xw, sheet_name="2_factor_regression", index=False)
        brin.to_excel(xw, sheet_name="3_brinson_sector", index=False)
        sc.to_excel(xw, sheet_name="4_stock_contrib")
        yr.to_excel(xw, sheet_name="5_by_year")
    for nm, df in (("decomposition", dec), ("factor_regression", reg), ("brinson", brin), ("by_year", yr)):
        df.to_csv(OUT / f"sg_attr_{nm}.csv", index=nm == "by_year")
    sc.to_csv(OUT / "sg_attr_stock_contrib.csv")
    pd.set_option("display.width", 250)
    print(dec.round(4).to_string(index=False))
    print(reg[reg.period == "EVAL_2021_26"].round(3).to_string(index=False))
    print(reg[reg.period == "FULL_2016_26"].round(3).to_string(index=False))
    print(brin[brin.period == "EVAL_2021_26"].round(4).to_string(index=False))
    print(yr.round(3).to_string())
    print("bottom 5 stocks", sc.head(5).round(3).to_string())
    print("top 5 stocks", sc.tail(5).round(3).to_string())
    print("checks: decomposition sums ->",
          (dec["STI"] + dec.iloc[:, 3] + dec.iloc[:, 4] + dec.iloc[:, 5] - dec["strategy_net"]).abs().max())


if __name__ == "__main__":
    main()
