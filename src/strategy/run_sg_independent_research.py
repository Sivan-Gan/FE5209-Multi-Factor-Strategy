#!/usr/bin/env python3
"""Independent Singapore factor-selection, configuration-selection, and OOS pipeline.

This is deliberately separate from the frozen US configuration.  It applies the
sequence requested for SG: (1) select factor families using 2011--2015 only;
(2) compare a pre-declared Member-4-style grid in 2016--2020 only; and (3)
freeze the selected family set/configuration and report 2021--latest OOS.

The input annual accounting export does not contain verified filing timestamps.
To prevent use before a report could plausibly be public, accounting values are
made available no earlier than fiscal-period end plus 183 calendar days.  This
is a conservative *proxy*, not a verified PIT announcement date.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import shutil
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
PRICE_FILE = WORKSPACE / "SGX_factor_research/output_lseg_rebuilt_for_sg_strategy/data/prices_daily_2006.csv"
FUND_FILE = WORKSPACE / "SGX_factor_research/output_lseg_rebuilt_for_sg_strategy/data/fundamentals_lseg_panel_unverified_pit.csv"
OUT = ROOT / "outputs/strategy/sg_independent"

SELECTION_START, SELECTION_END = "2011-01-01", "2015-12-31"
CONFIG_START, CONFIG_END = "2016-01-01", "2020-12-31"
OOS_START = "2021-01-01"
ONE_WAY_COST_BPS = 20.0  # pre-declared SG provisional scenario; sensitivity also written
TOP_FRACTION, EXIT_FRACTION, SECTOR_CAP = 0.20, 0.30, 0.30
MIN_CROSS_SECTION = 20
FACTOR_ORDER = ["Quality", "Momentum", "LowRisk", "Investment"]


def clean_number(s):
    return pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan)


def weekly_calendar(prices: pd.DataFrame) -> pd.DataFrame:
    # Remove provider placeholder rows where every security has a missing close.
    # These are not exchange trading days and must not become signal/execution dates.
    valid_dates = prices.groupby("date")["adj_close"].count()
    all_dates = pd.DatetimeIndex(sorted(valid_dates[valid_dates > 0].index))
    cal = pd.DataFrame({"date": all_dates})
    cal["week"] = cal["date"].dt.to_period("W-FRI")
    signals = cal.groupby("week", as_index=False)["date"].max().sort_values("date").reset_index(drop=True)
    # Signal is formed on the last available trading date of the week; execution
    # is the immediately following market trading date, not the next weekly signal.
    date_to_next = {d: all_dates[i + 1] for i, d in enumerate(all_dates[:-1])}
    signals["execution_date"] = signals["date"].map(date_to_next)
    signals["next_execution_date"] = signals["execution_date"].shift(-1)
    return signals.dropna().rename(columns={"date": "signal_date"})


def prepare_prices() -> tuple[pd.DataFrame, pd.DataFrame]:
    p = pd.read_csv(PRICE_FILE, parse_dates=["date"])
    p = p.sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"])
    p["adj_close"] = clean_number(p["adj_close"])
    p["volume"] = clean_number(p["volume"])
    p["trading_value"] = clean_number(p.get("trading_value", p["adj_close"] * p["volume"]))
    p["ret"] = p.groupby("ticker")["adj_close"].pct_change(fill_method=None)
    p["vol_252d"] = p.groupby("ticker")["ret"].transform(lambda x: x.rolling(252, min_periods=180).std())
    p["obs_252d"] = p.groupby("ticker")["adj_close"].transform(lambda x: x.rolling(252, min_periods=180).count())
    p["nonzero_60d"] = p.groupby("ticker")["volume"].transform(lambda x: (x.fillna(0) > 0).rolling(60, min_periods=40).mean())
    p["mom_12_1"] = p.groupby("ticker")["adj_close"].transform(lambda x: x.shift(21) / x.shift(252) - 1)
    return p, weekly_calendar(p)


def prepare_fundamentals(valid_price_tickers: set[str]) -> pd.DataFrame:
    f = pd.read_csv(FUND_FILE, parse_dates=["fiscal_period_end"])
    f = f[f["fundamentals_status"].eq("usable_annual_fundamentals")].copy()
    # A manual source mapping (S35.SI) has no matching price history in the
    # research universe. Keep it in the source handoff for review, but never
    # count it as usable in the factor-research panel.
    f = f[f["ticker"].isin(valid_price_tickers)].copy()
    numeric = ["revenue", "gross_profit", "net_income", "ebit", "ebitda", "total_assets",
               "total_liabilities", "common_equity", "cash", "operating_cash_flow", "capex",
               "shares_outstanding", "market_cap", "roe", "roa"]
    for c in numeric:
        f[c] = clean_number(f[c])
    f = f.sort_values(["ticker", "fiscal_period_end"]).drop_duplicates(["ticker", "fiscal_period_end"], keep="last")
    f["available_date_proxy"] = f["fiscal_period_end"] + pd.Timedelta(days=183)
    f["equity_lag"] = f.groupby("ticker")["common_equity"].shift(1)
    f["assets_lag"] = f.groupby("ticker")["total_assets"].shift(1)
    f["roe_proxy"] = f["net_income"] / f["equity_lag"].where(f["equity_lag"] > 0, f["common_equity"])
    f["roa_proxy"] = f["net_income"] / f["total_assets"]
    f["gp_assets"] = f["gross_profit"] / f["total_assets"]
    f["neg_leverage"] = -f["total_liabilities"] / f["common_equity"]
    f["asset_growth_negative"] = -(f["total_assets"] / f["assets_lag"] - 1)
    return f


def winsor_z(x: pd.Series) -> pd.Series:
    x = clean_number(x)
    valid = x.dropna()
    if len(valid) < 5:
        return pd.Series(np.nan, index=x.index)
    lo, hi = valid.quantile(.01), valid.quantile(.99)
    w = x.clip(lo, hi)
    sd = w.std(ddof=0)
    return (w - w.mean()) / sd if pd.notna(sd) and sd > 0 else pd.Series(np.nan, index=x.index)


def neutralize(frame: pd.DataFrame, raw_col: str) -> pd.Series:
    """Winsorize/z-score then remove six-sector mean when bucket has >=10 names."""
    z = winsor_z(frame[raw_col])
    sec = frame["six_sector"].fillna("other").astype(str)
    counts = sec.groupby(sec).transform("size")
    bucket = sec.where(counts >= 10, "other_neutralization_bucket")
    residual = z - z.groupby(bucket).transform("mean")
    return winsor_z(residual)


def asof_fundamentals(signals: pd.DataFrame, funds: pd.DataFrame) -> pd.DataFrame:
    parts = []
    cols = ["ticker", "six_sector", "available_date_proxy", "roe_proxy", "roa_proxy", "gp_assets",
            "neg_leverage", "asset_growth_negative"]
    for ticker, fs in funds[cols].groupby("ticker", sort=False):
        ss = signals[["signal_date"]].sort_values("signal_date")
        m = pd.merge_asof(ss, fs.sort_values("available_date_proxy"), left_on="signal_date",
                          right_on="available_date_proxy", direction="backward")
        m["ticker"] = ticker
        parts.append(m)
    return pd.concat(parts, ignore_index=True)


def make_scores(prices: pd.DataFrame, calendar: pd.DataFrame, funds: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    required_dates = pd.concat([calendar["signal_date"], calendar["execution_date"], calendar["next_execution_date"]]).unique()
    q = prices[prices["date"].isin(required_dates)].copy()
    signal = calendar[["signal_date", "execution_date", "next_execution_date"]]
    base = q.merge(signal, left_on="date", right_on="signal_date", how="inner")
    base = base.rename(columns={"date": "_signal_price_date"})
    # Exact-price execution convention: unavailable rather than silently carrying stale prices.
    exe = q[["ticker", "date", "adj_close"]].rename(columns={"date": "execution_date", "adj_close": "execution_price"})
    nex = q[["ticker", "date", "adj_close"]].rename(columns={"date": "next_execution_date", "adj_close": "next_execution_price"})
    base = base.merge(exe, on=["ticker", "execution_date"], how="left").merge(nex, on=["ticker", "next_execution_date"], how="left")
    base["forward_return"] = base["next_execution_price"] / base["execution_price"] - 1
    base["return_available"] = np.isfinite(base["forward_return"])
    base["price_eligible"] = ((base["adj_close"] >= .20) & (base["obs_252d"] >= 180) &
                              (base["nonzero_60d"] >= .80) & base["return_available"])
    f = asof_fundamentals(calendar, funds)
    base = base.merge(f, on=["signal_date", "ticker"], how="left", suffixes=("", "_fund"))
    sector_col = "six_sector_fund" if "six_sector_fund" in base else "six_sector"
    base["six_sector"] = base[sector_col].fillna("other")
    if sector_col != "six_sector":
        base = base.drop(columns=[sector_col])
    rows = []
    for date, w in base.groupby("signal_date", sort=True):
        w = w.copy()
        # Quality: financials use only ROE/ROA; others average profitability and leverage signals.
        w["quality_raw"] = np.where(w["six_sector"].eq("financials"),
                                    w[["roe_proxy", "roa_proxy"]].mean(axis=1, skipna=True),
                                    w[["roe_proxy", "roa_proxy", "gp_assets", "neg_leverage"]].mean(axis=1, skipna=True))
        w["momentum_raw"] = w["mom_12_1"]
        w["lowrisk_raw"] = -w["vol_252d"]
        w["investment_raw"] = w["asset_growth_negative"]
        for family, col in [("Quality", "quality_raw"), ("Momentum", "momentum_raw"),
                            ("LowRisk", "lowrisk_raw"), ("Investment", "investment_raw")]:
            w[f"{family}_score"] = neutralize(w, col)
            w[f"{family}_available"] = (w["price_eligible"] & w[f"{family}_score"].notna()).astype(int)
        rows.append(w)
    s = pd.concat(rows, ignore_index=True)
    return s, calendar


def factor_returns(scores: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, ic_rows = [], []
    for date, w in scores.groupby("signal_date", sort=True):
        for fam in FACTOR_ORDER:
            x = w[(w[f"{fam}_available"] == 1) & np.isfinite(w["forward_return"])].copy()
            n = len(x)
            if n < MIN_CROSS_SECTION:
                rows.append({"signal_date": date, "next_execution_date": w["next_execution_date"].iloc[0], "family": fam,
                             "factor_return": np.nan, "available": 0, "n": n})
                ic_rows.append({"signal_date": date, "family": fam, "ic": np.nan, "n": n})
                continue
            x = x.sort_values([f"{fam}_score", "ticker"], ascending=[False, True], kind="mergesort")
            k = max(1, math.floor(.30 * n))
            ret = x.head(k)["forward_return"].mean() - x.tail(k)["forward_return"].mean()
            # Spearman IC without a SciPy runtime dependency.
            ic = x[f"{fam}_score"].rank(method="average").corr(x["forward_return"].rank(method="average"))
            rows.append({"signal_date": date, "next_execution_date": w["next_execution_date"].iloc[0], "family": fam,
                         "factor_return": ret, "available": 1, "n": n})
            ic_rows.append({"signal_date": date, "family": fam, "ic": ic, "n": n})
    return pd.DataFrame(rows), pd.DataFrame(ic_rows)


def selection_summary(fret: pd.DataFrame, ic: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    fr = fret[(fret.signal_date >= pd.Timestamp(SELECTION_START)) & (fret.signal_date <= pd.Timestamp(SELECTION_END))]
    ir = ic[(ic.signal_date >= pd.Timestamp(SELECTION_START)) & (ic.signal_date <= pd.Timestamp(SELECTION_END))]
    rows = []
    for fam in FACTOR_ORDER:
        a = fr[fr.family.eq(fam)].dropna(subset=["factor_return"])
        b = ir[ir.family.eq(fam)].dropna(subset=["ic"])
        years_pos = int((a.assign(year=a.signal_date.dt.year).groupby("year").factor_return.mean() > 0).sum())
        ann = (1 + a.factor_return).prod() ** (52 / len(a)) - 1 if len(a) else np.nan
        icir = b.ic.mean() / b.ic.std(ddof=1) * np.sqrt(52) if len(b) > 1 and b.ic.std(ddof=1) > 0 else np.nan
        selected = len(a) >= 150 and a.ic if False else None
        rows.append({"family": fam, "weeks_available": len(a), "mean_weekly_ls_return": a.factor_return.mean(),
                     "annualized_ls_return": ann, "ls_sharpe": a.factor_return.mean() / a.factor_return.std(ddof=1) * np.sqrt(52) if len(a)>1 and a.factor_return.std(ddof=1)>0 else np.nan,
                     "mean_spearman_ic": b.ic.mean(), "icir": icir, "positive_calendar_years": years_pos,
                     "selection_rule": "available_weeks>=150 AND mean_IC>0 AND annualized_LS_return>0 AND positive_years>=3"})
    out = pd.DataFrame(rows)
    out["selected"] = ((out.weeks_available >= 150) & (out.mean_spearman_ic > 0) &
                       (out.annualized_ls_return > 0) & (out.positive_calendar_years >= 3))
    chosen = out.loc[out.selected, "family"].tolist()
    return out, chosen


def fmom_weights(fret: pd.DataFrame, selected: list[str], config: dict, signal_dates: list[pd.Timestamp]) -> pd.DataFrame:
    wide = fret[fret.family.isin(selected)].pivot(index="signal_date", columns="family", values="factor_return").reindex(columns=selected).sort_index()
    ends = fret.drop_duplicates("signal_date").set_index("signal_date")["next_execution_date"]
    rows = []
    for d in signal_dates:
        done = wide.index[(wide.index < d) & (ends.reindex(wide.index) <= d)]
        vals = []
        ok = True
        for fam in selected:
            valid = wide.loc[done, fam].dropna()
            need = max(config["J"], 104) if config["risk_adjusted"] else config["J"]
            if len(valid) < need:
                ok = False; break
            fm = valid.iloc[-config["J"]:].sum()
            if config["risk_adjusted"]:
                sd = valid.iloc[-104:].std(ddof=1)
                if not np.isfinite(sd) or sd <= 0: ok = False; break
                fm /= sd
            vals.append(fm)
        dyn = np.maximum(vals, 0) / np.maximum(vals, 0).sum() if ok and np.maximum(vals, 0).sum() > 0 else np.repeat(1/len(selected), len(selected))
        weight = (1-config["lambda"])/len(selected) + config["lambda"]*dyn
        for fam, w in zip(selected, weight):
            rows.append({"signal_date": d, "family": fam, "weight": w, "dynamic_status": "OK" if ok else "EQUAL_FALLBACK"})
    return pd.DataFrame(rows)


def score_and_hold(scores: pd.DataFrame, weights: pd.DataFrame, selected: list[str], use_buffer: bool,
                   end: pd.Timestamp | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    sw = scores[scores.signal_date.ge(pd.Timestamp(CONFIG_START))].copy()
    if end is not None:
        sw = sw[sw.signal_date.le(end)]
    widew = weights.pivot(index="signal_date", columns="family", values="weight").reindex(columns=selected)
    holdings, diagnostics, previous = [], [], set()
    for d, w in sw.groupby("signal_date", sort=True):
        z = w[w.price_eligible].copy()
        required = [f"{x}_score" for x in selected]
        z = z.dropna(subset=required)
        if d not in widew.index or len(z) < MIN_CROSS_SECTION: continue
        z["composite_score"] = sum(z[f"{f}_score"] * widew.loc[d, f] for f in selected)
        z = z.sort_values(["composite_score", "ticker"], ascending=[False, True], kind="mergesort").reset_index(drop=True)
        z["rank"] = np.arange(1, len(z)+1)
        n_target, cap = max(1, math.floor(TOP_FRACTION*len(z))), max(1, math.floor(SECTOR_CAP*max(1,math.floor(TOP_FRACTION*len(z)))))
        if use_buffer and previous:
            keep = z.ticker.isin(previous) & z["rank"].le(math.floor(EXIT_FRACTION*len(z)))
            order = pd.concat([z[keep], z[~keep]])
        else: order = z
        pick, count = [], {}
        for r in order.itertuples():
            if len(pick) >= n_target: break
            if count.get(r.six_sector, 0) >= cap: continue
            pick.append(r); count[r.six_sector] = count.get(r.six_sector, 0)+1
        h = pd.DataFrame(pick)
        if h.empty: continue
        h["weight"] = 1/n_target
        h["signal_date"] = d
        h["entry"] = np.where(h.ticker.isin(previous), "KEPT", "NEW")
        holdings.append(h[["signal_date","execution_date","next_execution_date","ticker","six_sector","composite_score","rank","weight","forward_return","entry"]])
        diagnostics.append({"signal_date":d,"n_scored":len(z),"n_target":n_target,"n_held":len(h),"cash_weight":1-len(h)/n_target,"max_sector_weight":h.groupby("six_sector").weight.sum().max()})
        previous=set(h.ticker)
    return pd.concat(holdings, ignore_index=True), pd.DataFrame(diagnostics)


def path(holdings: pd.DataFrame, cost_bps: float) -> pd.DataFrame:
    result=[]; prior=pd.Series(dtype=float)
    for d, h in holdings.groupby("signal_date", sort=True):
        target=h.set_index("ticker").weight
        universe=target.index.union(prior.index)
        turnover=.5*(target.reindex(universe,fill_value=0)-prior.reindex(universe,fill_value=0)).abs().sum() if len(prior) else 1.0
        gross=(h.weight*h.forward_return).sum()
        net=(1-turnover*cost_bps/1e4)*(1+gross)-1
        post=target*(1+h.set_index("ticker").forward_return).reindex(target.index)
        cash = 1.0 - target.sum()
        # Drift the invested sleeve then renormalize by the whole portfolio
        # (including any constraint-created cash) before the next rebalance.
        prior=post/(cash + post.sum()) if (cash + post.sum()) > 0 else pd.Series(dtype=float)
        result.append({"signal_date":d,"gross_return":gross,"net_return":net,"turnover":turnover,"cost_bps":cost_bps})
    return pd.DataFrame(result)


def metrics(p: pd.DataFrame) -> dict:
    r=p.net_return.dropna(); nav=(1+r).cumprod(); ann=(nav.iloc[-1]**(52/len(r))-1) if len(r) else np.nan
    vol=r.std(ddof=1)*np.sqrt(52) if len(r)>1 else np.nan
    dd=1-nav/nav.cummax()
    return {"weeks":len(r),"annualized_net_return":ann,"annualized_volatility":vol,"weekly_sharpe_zero_rf":r.mean()/r.std(ddof=1)*np.sqrt(52) if len(r)>1 and r.std(ddof=1)>0 else np.nan,"max_drawdown":dd.max() if len(dd) else np.nan,"average_one_way_turnover":p.turnover.mean()}


def grid() -> list[dict]:
    out=[]
    for J, lam, ra, buf in itertools.product([13,26,52],[.5,1.0],[False,True],[False,True]): out.append({"J":J,"lambda":lam,"risk_adjusted":ra,"buffer":buf})
    for buf in [False,True]: out.append({"J":26,"lambda":0.0,"risk_adjusted":False,"buffer":buf})
    return out


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--force",action="store_true"); args=parser.parse_args()
    if OUT.exists() and args.force: shutil.rmtree(OUT)
    OUT.mkdir(parents=True,exist_ok=True)
    prices, calendar=prepare_prices(); funds=prepare_fundamentals(set(prices.ticker)); scores,calendar=make_scores(prices,calendar,funds)
    fret,ic=factor_returns(scores); summary,selected=selection_summary(fret,ic)
    if not selected:
        raise RuntimeError("No factor passes the pre-declared 2011-2015 selection rule; do not tune the rule after seeing this result.")
    summary.to_csv(OUT/"01_single_factor_selection_summary_2011_2015.csv",index=False); fret.to_csv(OUT/"01_factor_long_short_returns_weekly.csv",index=False); ic.to_csv(OUT/"01_factor_ic_weekly.csv",index=False)
    # Scores are rebuilt deterministically from the retained code and source data.
    # Do not write a 57 MB intermediate CSV during the research run.
    config_rows=[]; candidates=[]
    signals=sorted(scores[scores.signal_date.ge(pd.Timestamp(CONFIG_START))].signal_date.unique())
    for c in grid():
        w=fmom_weights(fret,selected,c,signals); h,d=score_and_hold(scores,w,selected,c["buffer"], pd.Timestamp(CONFIG_END)); p=path(h,ONE_WAY_COST_BPS)
        sel=p[(p.signal_date>=pd.Timestamp(CONFIG_START))&(p.signal_date<=pd.Timestamp(CONFIG_END))]
        m=metrics(sel); tag=f"J{c['J']}_lam{c['lambda']:g}_ra{int(c['risk_adjusted'])}_buf{int(c['buffer'])}"
        config_rows.append({"tag":tag,**c,"cost_bps":ONE_WAY_COST_BPS,**m}); candidates.append((tag,c,w,h,p,d))
    configs=pd.DataFrame(config_rows).sort_values(["weekly_sharpe_zero_rf","annualized_net_return"],ascending=False)
    configs.to_csv(OUT/"02_configuration_grid_2016_2020.csv",index=False)
    winner=configs.iloc[0].to_dict(); tag=winner["tag"]; chosen=next(x for x in candidates if x[0]==tag)
    # Rebuild the selected configuration continuously from 2016 through the
    # latest date so the OOS buffer/weights inherit only prior information.
    _, c,_,_,_,_=chosen
    w=fmom_weights(fret,selected,c,signals); h,d=score_and_hold(scores,w,selected,c["buffer"]); p=path(h,ONE_WAY_COST_BPS)
    h.to_csv(OUT/"03_frozen_sg_holdings_2016_latest.csv",index=False); w.to_csv(OUT/"03_frozen_sg_factor_weights_2016_latest.csv",index=False); d.to_csv(OUT/"03_holding_diagnostics_2016_latest.csv",index=False); p.to_csv(OUT/"03_strategy_weekly_returns_2016_latest.csv",index=False)
    periods=[]
    for label,start,end in [("configuration_selection",CONFIG_START,CONFIG_END),("out_of_sample",OOS_START,"2099-12-31")]:
        q=p[(p.signal_date>=pd.Timestamp(start))&(p.signal_date<=pd.Timestamp(end))]; periods.append({"period":label,**metrics(q)})
    pd.DataFrame(periods).to_csv(OUT/"04_final_period_summary.csv",index=False)
    manifest={"methodology":"SG independent; factor selection 2011-2015, config selection 2016-2020, OOS 2021-latest","selected_factors":selected,"frozen_config":c,"selection_cost_bps_provisional":ONE_WAY_COST_BPS,"price_end":str(prices.date.max().date()),"fundamental_companies":int(funds.ticker.nunique()),"fundamental_rows":int(len(funds)),"PIT_limit":"Accounting available date = fiscal period end + 183d proxy; actual announcement timestamps not verified."}
    (OUT/"README.json").write_text(json.dumps(manifest,indent=2,default=str),encoding="utf-8")
    print(json.dumps({"selected_factors":selected,"winner":winner,"periods":periods,"out":str(OUT)},indent=2,default=str))


if __name__=="__main__": main()
