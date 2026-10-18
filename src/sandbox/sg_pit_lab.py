"""SANDBOX: SG adaptive strategy rebuilt on the 2026-10-18 SGX PIT static delivery.

New inputs (SGX_PIT_STATIC_DELIVERY_2026_10_18, private LSEG-derived, CANDIDATE PIT):
  PIT_Weekly_MCap_Values       direct LSEG weekly market cap (TR.CompanyMarketCap), 75 RICs
  PIT_Annual_Validated         annual statements with IS / BS / CF original announcement dates
  PIT_Security_Metadata_Values RIC -> SGX exchange ticker (panel ticker = exchange_ticker + '.SI')
  PIT_Quarterly_Validated      used only for each RIC's reporting currency (mode)

PRE-REGISTERED DESIGN (written before running)
PIT factor definitions (score = higher is better; sector z-score per week, winsorised 1/99%,
market-wide z when a sector has < 3 stocks):
  Size_PIT        -ln(LSEG market cap), last observation <= signal date (<= 10 days old), SGD caps
                  only; HIGH = SMALL, the teammate's / US direction (panel size_raw = -ln(mcap proxy);
                  corrected 2026-10-18 after a first run used the opposite sign)
  Value_PIT       common equity / market cap (book-to-price); equity > 0; reporting currency SGD
  Quality_PIT     mean of z(ROE = NI / common equity) and z(ROA = NI / total assets)
  Investment_PIT  -(total assets / previous FY total assets - 1)
  NEW Leverage_PIT   -(total liabilities / total assets)
  NEW CashProf_PIT   operating cash flow / total assets
  Annual record usable at signal date S only if (relevant announcement date + 1 day) <= S
  (Quality: max of IS and BS dates; Value / Investment / Leverage: BS; CashProf: max of CF, BS).
  Rows with invalid announcement dates are dropped; duplicated RIC x FY keeps the last row.
  Momentum and Low Risk: unchanged price-based panel scores.
Universe: price_eligible and every factor of the pool available (branch 'common_all').
Strategy: teammate spec (Top-3 by 3y mean/std, IV, top 20%, buffer, 30% sector cap, weekly,
  10 bp) and the Top-4 variant.
Runs:
  A  PROXY6  (old panel factors)       on the old universe   -> reproduces the teammate
  B  PROXY6  restricted to the PIT universe (same stocks as C) -> isolates the universe effect
  C  PIT6    Q, M, LR, Inv, Size, Value all fundamentals PIT
  D  PIT8    PIT6 + Leverage + CashProf (selection decides whether the new factors are used)
  each with Top-3 and Top-4.  Costs 10 / 20 / 30 bp.  Periods: 2016-18 pre, 2019-20
  observation, 2021-01..2026-09 evaluation (+ 2021-25).  No further selection: all reported.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

from src.adaptive_strategy.composite_score import ScoreConfig, calculate_scores
from src.adaptive_strategy.factor_selection import SelectionConfig, select_factors
from src.adaptive_strategy.factor_weighting import WeightingConfig, calculate_selected_weights
from src.sandbox import sg_adaptive_lab as L
from src.sandbox.adaptive_lab import boot_sharpe_diff, nw_t
from src.strategy import factor_returns as fr
from src.strategy import metrics as mt
from src.strategy import portfolio as pf

PIT = Path(os.environ.get("SG_PIT", "SGX_PIT_STATIC_DELIVERY_2026_10_18"))
OUT = Path(os.environ.get("SG_PIT_OUT", "outputs/sandbox/sg_pit_lab"))
PRE, OBS, EVAL, EVAL25 = (("2016-01-01", "2018-12-31"), ("2019-01-01", "2020-12-31"),
                          ("2021-01-01", "2026-12-31"), ("2021-01-01", "2025-12-31"))
PROXY = {"Quality": "Quality_score", "Momentum": "Momentum_score", "Low Risk": "LowRisk_score",
         "Investment": "Investment_score", "Size": "SizePanel_score", "Value": "Value_score"}
PIT6 = {"Quality": "Quality_PIT", "Momentum": "Momentum_score", "Low Risk": "LowRisk_score",
        "Investment": "Investment_PIT", "Size": "Size_PIT", "Value": "Value_PIT"}
PIT8 = {**PIT6, "Leverage": "Leverage_PIT", "Cash Prof": "CashProf_PIT"}


def xl_date(s):
    v = pd.to_numeric(s, errors="coerce")
    return pd.to_datetime(v, unit="D", origin="1899-12-30").astype("datetime64[ns]")


def load_pit_tables():
    rd = lambda k: pd.read_excel(PIT / "SGX_PIT_2006_2026_STATIC_VALUES.xlsx", sheet_name=k)
    cache = OUT / "pit_tables.pkl"
    if cache.exists():
        return pd.read_pickle(cache)
    t = {k: rd(k) for k in ("PIT_Security_Metadata_Values", "PIT_Annual_Validated", "PIT_Weekly_MCap_Values",
                            "PIT_Quarterly_Validated")}
    pd.to_pickle(t, cache)
    return t


def szscore(df, col):
    def f(x):
        x = x.astype(float)
        if x.notna().sum() < 3:
            return x * np.nan
        lo, hi = x.quantile(0.01), x.quantile(0.99)
        x = x.clip(lo, hi)
        sd = x.std(ddof=0)
        return (x - x.mean()) / sd if sd > 0 else x * np.nan
    sec_n = df.groupby(["signal_date", "historical_gics_sector"])[col].transform("count")
    by_sec = df.groupby(["signal_date", "historical_gics_sector"])[col].transform(f)
    by_mkt = df.groupby("signal_date")[col].transform(f)
    return np.where(sec_n >= 3, by_sec, by_mkt)


def build_pit_features(panel):
    t = load_pit_tables()
    meta = t["PIT_Security_Metadata_Values"].copy()
    meta["PERMNO"] = meta["exchange_ticker"].astype(str) + ".SI"
    ric2t = dict(zip(meta["lseg_ric"], meta["PERMNO"]))
    q = t["PIT_Quarterly_Validated"]
    cur = q.dropna(subset=["reporting_currency"]).groupby("lseg_ric")["reporting_currency"].agg(
        lambda s: s.mode().iloc[0])
    keys = panel[["signal_date", "PERMNO", "historical_gics_sector"]].copy()
    keys["sd"] = pd.to_datetime(keys["signal_date"]).astype("datetime64[ns]")

    # weekly market cap (SGD only)
    mc = t["PIT_Weekly_MCap_Values"].copy()
    mc = mc[mc["currency"] == "SGD"]
    mc["PERMNO"] = mc["lseg_ric"].map(ric2t)
    mc["d"] = xl_date(mc["source_date"])
    mc = mc.dropna(subset=["PERMNO", "d", "market_cap"]).query("market_cap > 0")
    mc = mc.sort_values("d").drop_duplicates(["PERMNO", "d"], keep="last")
    k = keys.sort_values("sd")
    k = pd.merge_asof(k, mc[["PERMNO", "d", "market_cap"]].sort_values("d"), left_on="sd", right_on="d",
                      by="PERMNO", direction="backward", tolerance=pd.Timedelta(days=10)).drop(columns="d")

    # annual statements, as-of the relevant announcement date + 1 day
    a = t["PIT_Annual_Validated"].copy()
    a["PERMNO"] = a["lseg_ric"].map(ric2t)
    a["cur"] = a["lseg_ric"].map(cur)
    a = a[a["PERMNO"].notna() & (a["cur"] == "SGD")]
    a["fpe"] = xl_date(a["fiscal_period_end"])
    for c in ("is", "bs", "cf"):
        a[f"{c}_d"] = xl_date(a[f"{c}_announce_date"]).where(a[f"{c}_announce_date_valid"].astype(bool))
    a = a.sort_values(["PERMNO", "fpe"]).drop_duplicates(["PERMNO", "fpe"], keep="last")
    a["ta_prev"] = a.groupby("PERMNO")["total_assets"].shift(1)
    a["fpe_prev"] = a.groupby("PERMNO")["fpe"].shift(1)
    a.loc[(a["fpe"] - a["fpe_prev"]).dt.days.gt(400), "ta_prev"] = np.nan   # gap year -> no growth
    eq = a["common_equity"].where(a["common_equity"] > 0)
    ta = a["total_assets"].where(a["total_assets"] > 0)
    a["roe"], a["roa"] = a["net_income"] / eq, a["net_income"] / ta
    a["agr"] = a["total_assets"] / a["ta_prev"].where(a["ta_prev"] > 0) - 1
    a["lev"] = a["total_liabilities"] / ta
    a["cfp"] = a["operating_cash_flow"] / ta
    a["book"] = eq
    specs = {"Q": (["is_d", "bs_d"], ["roe", "roa"]), "BS": (["bs_d"], ["book", "agr", "lev"]),
             "CF": (["cf_d", "bs_d"], ["cfp"])}
    for name, (dcols, vals) in specs.items():
        x = a[["PERMNO"] + dcols + vals].copy()
        x["avail"] = (x[dcols].max(axis=1, skipna=False) + pd.Timedelta(days=1)).astype("datetime64[ns]")
        x = x.dropna(subset=["avail"]).sort_values("avail")
        x = x.drop_duplicates(["PERMNO", "avail"], keep="last")
        k = pd.merge_asof(k.sort_values("sd"), x[["PERMNO", "avail"] + vals], left_on="sd", right_on="avail",
                          by="PERMNO", direction="backward").drop(columns="avail")
    k["size_raw"] = np.log(k["market_cap"])
    k["bp_raw"] = k["book"] / k["market_cap"]
    for c, raw, sign in (("Size_PIT", "size_raw", -1), ("Value_PIT", "bp_raw", 1), ("roe_z", "roe", 1),
                         ("roa_z", "roa", 1), ("Investment_PIT", "agr", -1), ("Leverage_PIT", "lev", -1),
                         ("CashProf_PIT", "cfp", 1)):
        k[c] = szscore(k.assign(_v=sign * k[raw]), "_v")
    k["Quality_PIT"] = k[["roe_z", "roa_z"]].mean(axis=1, skipna=False)
    cols = ["Size_PIT", "Value_PIT", "Quality_PIT", "Investment_PIT", "Leverage_PIT", "CashProf_PIT"]
    return k[["signal_date", "PERMNO"] + cols + ["market_cap", "size_raw", "bp_raw", "roe", "agr"]]


def run(uni, rets, cols, end, dates, top_k, sti):
    L.COLS = cols
    pool = list(cols)
    fret = L.factor_returns(uni, end)
    cal = pd.to_datetime(pd.Series(dates))
    parts, decs = [], []
    years = pd.to_datetime(uni["signal_date"]).dt.year
    for year, cutoff in cal.groupby(cal.dt.year).min().items():
        sel, _ = select_factors(fret, cutoff, pool, SelectionConfig(lookback_years=3, top_k=top_k, min_coverage=0.65))
        w, _ = calculate_selected_weights(fret, cutoff, sel, WeightingConfig(), covariance_families=pool)
        sub = uni[(years == year) & uni["signal_date"].isin(dates)]
        parts.append(calculate_scores(sub, sel, w["IV"], ScoreConfig("selected_only"), factor_columns=cols))
        decs.append({"year": year, "selected": ", ".join(sel), **{f"w_{f}": round(v, 3) for f, v in w["IV"].items()}})
    s = pd.concat(parts, ignore_index=True)
    s["signal_date"] = pd.to_datetime(s["signal_date"]).dt.strftime("%Y-%m-%d")
    hold, cons = pf.build_holdings(s, use_buffer=True)
    path = mt.weekly_path(hold, rets, end).set_index("signal_date")
    return path, hold, cons, decs, fret


def perf(r, span, bench=None, hold=None, rets=None):
    s = r[(r.index >= span[0]) & (r.index <= span[1])]
    p = mt.performance(s, None, bench.reindex(s.index) if bench is not None else None)
    o = {k: p.get(k) for k in ("ann_return", "ann_vol", "sharpe", "sortino", "max_drawdown", "calmar",
                               "drawdown_episodes", "longest_drawdown_weeks", "period_hit_rate", "beta")}
    if hold is not None:
        h = hold[hold["signal_date"].between(span[0], span[1])]
        tr = mt.trade_stats(h, rets, span[1], 0.001)
        o.update({k: tr[k] for k in ("n_round_trips", "avg_holding_weeks", "trade_win_rate", "avg_profit_per_trade")})
    return o


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    panel, _, rets, last = L.load_panel()           # default 7-factor column set; we use 6 of them
    panel["SizePanel_score"] = panel["Size_score"]           # panel sign: high = SMALL cap
    panel["SizePanel_score__available"] = ((panel["Size_available"] == 1)
                                           & np.isfinite(panel["Size_score"])).astype(int)
    end = str(last)
    feats = build_pit_features(panel)
    feats.to_csv(OUT / "sg_pit_features.csv.gz", index=False, compression="gzip")
    full = panel.merge(feats, on=["signal_date", "PERMNO"], how="left")
    for c in set(PIT8.values()) - set(PROXY.values()):
        full[f"{c}__available"] = np.isfinite(full[c]).astype(int)
    elig = full[full["price_eligible"].astype(bool)].copy()

    def universe(cols):
        ok = np.ones(len(elig), bool)
        for c in cols.values():
            ok &= (elig[f"{c}__available"].eq(1) & np.isfinite(pd.to_numeric(elig[c], errors="coerce"))).to_numpy()
        return elig[ok].copy()

    u_proxy, u_pit6, u_pit8 = universe(PROXY), universe(PIT6), universe(PIT8)
    u_proxy_on_pit = u_proxy[u_proxy.set_index(["signal_date", "PERMNO"]).index.isin(
        u_pit6.set_index(["signal_date", "PERMNO"]).index)].copy()
    done = set(rets.loc[rets["next_execution_date"] <= end, "signal_date"])
    dates = sorted(d for d in u_proxy["signal_date"].unique() if d >= "2016-01-01" and d in done)
    cal = u_proxy[u_proxy.signal_date.isin(dates)].groupby("signal_date")[["execution_date", "next_execution_date"]].first()
    sti = L.sti_weekly(cal.reset_index())

    runs = {"A_PROXY6_fullU": (u_proxy, PROXY), "B_PROXY6_pitU": (u_proxy_on_pit, PROXY),
            "C_PIT6": (u_pit6, PIT6), "D_PIT8": (u_pit8, PIT8)}
    rows, paths, decs, cov = [], {}, [], []
    for name, (u, cols) in runs.items():
        ud = u[u.signal_date.isin(dates)]
        cov.append({"run": name, "avg_stocks_per_week": ud.groupby("signal_date").size().mean(),
                    **{f"n_{y}": ud[ud.signal_date.str[:4] == str(y)].groupby("signal_date").size().mean()
                       for y in (2016, 2019, 2021, 2024, 2026)}})
        for k in (3, 4):
            path, hold, cons, dec, fret = run(u, rets, cols, end, dates, k, sti)
            tag = f"{name}_Top{k}"
            for c in (0.001, 0.002, 0.003):
                paths[f"{tag}@{int(c * 1e4)}bp"] = mt.net_returns(path, c)
            net = paths[f"{tag}@10bp"]
            row = {"tag": tag, "avg_names": float(cons["n_held"].mean()), "ann_turnover": float(path["turnover"].mean() * 52)}
            for lab, span in (("pre", PRE), ("obs", OBS), ("ev", EVAL), ("ev25", EVAL25)):
                row.update({f"{lab}_{kk}": v for kk, v in perf(net, span, sti, hold if lab == "ev" else None, rets).items()})
            for c in (20, 30):
                row[f"ev_ann_return_{c}bp"] = perf(paths[f"{tag}@{c}bp"], EVAL)["ann_return"]
                row[f"ev_sharpe_{c}bp"] = perf(paths[f"{tag}@{c}bp"], EVAL)["sharpe"]
            rows.append(row)
            decs += [{"tag": tag, **d} for d in dec]
            if k == 3:
                fret.assign(run=name).to_csv(OUT / f"factor_returns_{name}.csv", index=False)
    row = {"tag": "STI_TR"}
    for lab, span in (("pre", PRE), ("obs", OBS), ("ev", EVAL), ("ev25", EVAL25)):
        row.update({f"{lab}_{kk}": v for kk, v in perf(sti, span, sti).items()})
    rows.append(row)
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "sg_pit_results.csv", index=False)
    pd.DataFrame(cov).to_csv(OUT / "sg_pit_universe_coverage.csv", index=False)
    pd.DataFrame(decs).to_csv(OUT / "sg_pit_annual_decisions.csv", index=False)
    P = pd.DataFrame(paths)
    P["STI_TR"] = sti
    P.to_csv(OUT / "sg_pit_weekly_returns.csv")

    ev = (P.index >= EVAL[0]) & (P.index <= EVAL[1])
    srows = []
    for tag in ("C_PIT6_Top3", "C_PIT6_Top4", "D_PIT8_Top3", "D_PIT8_Top4", "A_PROXY6_fullU_Top4"):
        for vs in ("STI_TR", "A_PROXY6_fullU_Top3@10bp"):
            a = P[f"{tag}@10bp"][ev]
            b = P[vs][ev]
            o, p, ci = boot_sharpe_diff(a, b, pd.Series(0.0, index=a.index), n=3000)
            srows.append({"tag": tag, "vs": vs.replace("@10bp", ""), "ann_active": float((a - b).mean() * 52),
                          "nw_t": nw_t((a - b).to_numpy()), "sharpe_diff": o, "boot_p": p})
    pd.DataFrame(srows).to_csv(OUT / "sg_pit_significance.csv", index=False)

    pd.set_option("display.width", 260)
    print(pd.DataFrame(cov).round(1).to_string())
    c = ["avg_names", "pre_sharpe", "pre_calmar", "obs_sharpe", "obs_calmar", "ev_ann_return", "ev_ann_vol",
         "ev_sharpe", "ev_max_drawdown", "ev_calmar", "ev_ann_return_30bp", "ann_turnover"]
    print(res.set_index("tag")[c].round(3).to_string())
    print(pd.DataFrame(srows).round(3).to_string())


if __name__ == "__main__":
    main()
