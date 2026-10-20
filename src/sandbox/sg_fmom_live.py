"""SANDBOX: the frozen FMOM strategy (Gate B, US) applied to SG team data, backtest + live window.

FMOM rule exactly as frozen for the US (config J26_lam0.5_ra1_min4_buf1, window rule B):
  factors   Quality, Momentum, Low Risk, Investment (K = 4, fixed)
  weights   FMOM = sum of last 26 completed valid weekly factor returns, divided by the std of the
            last 104 (risk adjusted); w = 0.5 * 1/4 + 0.5 * max(FMOM,0)/sum(max(FMOM,0))
  portfolio all 4 scores required, top 20% EW, buffer 20/30, 30% sector cap, weekly, 10 bp
SG specifics are the same as the SG pipeline (universe, z-scores, execution, STI benchmark), so the
three strategies below differ only in how factors are chosen and weighted:
  FMOM_K4      frozen FMOM (lambda 0.5)
  STATIC_K4    same four factors, equal weight (lambda 0)
  ADAPTIVE_T4  the SG pipeline strategy (annual Top-4 of six, IV weights)

Backtest: holding periods ending <= 2026-09-30.  Live window: close 2026-09-30 -> last day in data,
daily NAV (src.sg_pipeline.live.daily_nav).

    python -m src.sandbox.sg_fmom_live [--config configs/sg_strategy.json] [--output-dir DIR]
"""
from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from src.sandbox.factor_lab import composite, fmom_weights
from src.sg_pipeline import run as R
from src.sg_pipeline.live import daily_nav
from src.strategy import factor_returns as fr
from src.strategy import metrics as mt
from src.strategy import portfolio as pf

K4 = ["Quality", "Momentum", "Low Risk", "Investment"]


def fmom_holdings(u, fret, dates, lam):
    w = fmom_weights(fret, K4, dates, J=26, lam=lam, risk_adjusted=True)
    sc = u[u["signal_date"].isin(dates)].rename(columns={R.FAMILY_COL[f]: f for f in K4}).assign(eligible_universe_flag=1)
    hold, _ = pf.build_holdings(composite(sc, w, K4), use_buffer=True)
    return hold, w


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(R.REPO / "configs/sg_strategy.json"))
    ap.add_argument("--output-dir", default=None)
    args = ap.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    eval_end = cfg["periods"]["eval_end"]
    out = Path(args.output_dir) if args.output_dir else R.resolve("private_data/sg_fmom_live_output")
    work = out / "_steps"
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    fr.LEG_FRACTION = cfg["selection"]["factor_leg_fraction"]
    live_cfg = copy.deepcopy(cfg)
    live_cfg["periods"]["eval_end"] = "2099-12-31"
    team_dir, sti_file = R.prepare_inputs(cfg)
    u, rets, cal = R.build_universe_and_returns(live_cfg, team_dir, work, t0, open_end=True)
    dates = sorted(x for x in u["signal_date"].unique() if x >= cfg["periods"]["start"])
    fret = R.factor_returns(u, rets, "2099-12-31", t0, work)
    pf.SELECT_FRACTION, pf.EXIT_FRACTION, pf.SECTOR_CAP = 0.20, 0.30, 0.30

    holds, weights = {}, {}
    holds["FMOM_K4"], weights["FMOM_K4"] = fmom_holdings(u, fret, dates, 0.5)
    holds["STATIC_K4"], _ = fmom_holdings(u, fret, dates, 0.0)
    holds["ADAPTIVE_T4"], _ = R.select_and_hold(live_cfg, u, rets, fret, dates, "2099-12-31", t0, work)
    R.log("holdings built", t0)

    # ---- backtest to eval_end
    cal_s = cal.assign(signal_date=cal["signal_date"].dt.strftime("%Y-%m-%d")).set_index("signal_date")
    sti_lvl = R.load_sti(sti_file)
    paths = {}
    for k, h in holds.items():
        p, _ = mt.weekly_path_and_ledger(h, rets, eval_end, 0.001)
        p = p.set_index("signal_date")
        paths[k] = p.assign(net=mt.net_returns(p, 0.001))
    idx = paths["FMOM_K4"].index
    sti_w = pd.Series([sti_lvl.loc[:cal_s.loc[s, "next_execution_date"]].iloc[-1]
                       / sti_lvl.loc[:cal_s.loc[s, "execution_date"]].iloc[-1] - 1 for s in idx], index=idx)
    series = {k: p["net"] for k, p in paths.items()} | {"STI": sti_w}
    rows = []
    for pname, (a, b) in {k: v for k, v in cfg["periods"].items() if isinstance(v, list)}.items():
        for k, r in series.items():
            s = r[(r.index >= a) & (r.index <= b)].dropna()
            perf = mt.performance(s)
            rows.append({"period": pname, "strategy": k, **{c: perf[c] for c in
                         ("ann_return", "ann_vol", "sharpe", "max_drawdown", "calmar", "n_weeks")},
                         "annual_turnover": paths[k]["turnover"].reindex(s.index).mean() * 52 if k in paths else np.nan})
    bt = pd.DataFrame(rows)
    bt.to_csv(out / "fmom_backtest_summary.csv", index=False)
    yr = pd.DataFrame({k: (1 + v).groupby(v.index.str[:4]).prod() - 1 for k, v in series.items()})
    yr.to_csv(out / "fmom_yearly_returns.csv")
    weights["FMOM_K4"].to_csv(out / "fmom_factor_weights_weekly.csv", index=False)

    # ---- live window, daily
    d = R.read_parquet(team_dir / "data/sgx_daily_market.parquet", ["gvkey", "iid", "date", "available_adjusted_price"], work)
    d["PERMNO"] = d["gvkey"].astype(str) + "_" + d["iid"].astype(str)
    d["date"] = pd.to_datetime(d["date"]).dt.normalize()
    adj = d.dropna(subset=["available_adjusted_price"]).pivot_table(
        index="date", columns="PERMNO", values="available_adjusted_price", aggfunc="last").sort_index().ffill()
    t_start, last_day = pd.Timestamp(eval_end), adj.index.max()
    live_sig = [s for s in cal_s.index if cal_s.loc[s, "next_execution_date"] > t_start]
    nav = {}
    for k, h in holds.items():
        p, _ = mt.weekly_path_and_ledger(h, rets, "2099-12-31", 0.001)
        gtr = {cal_s.loc[s, "execution_date"]: g for s, g in zip(p["signal_date"], p["gtr"])}
        weeks = [(cal_s.loc[s, "execution_date"], cal_s.loc[s, "next_execution_date"],
                  dict(zip(h.loc[h["signal_date"] == s, "PERMNO"], h.loc[h["signal_date"] == s, "weight"])))
                 for s in live_sig]
        nav[k] = daily_nav(weeks, adj, eval_end, 0.001, gtr)
    sti = sti_lvl[(sti_lvl.index >= t_start) & (sti_lvl.index <= last_day)]
    nav["STI"] = sti / sti.iloc[0]
    nav = pd.DataFrame(nav)
    nav = nav[nav.index >= t_start].dropna()
    nav.index.name = "date"
    nav.to_csv(out / "fmom_live_daily_nav.csv")
    live = pd.DataFrame({"window_return": nav.iloc[-1] - 1, "excess_vs_STI": nav.iloc[-1] - nav["STI"].iloc[-1],
                         "max_drawdown": -(nav / nav.cummax() - 1).min()})
    live.index.name = "strategy"
    live.to_csv(out / "fmom_live_summary.csv")
    wl = weights["FMOM_K4"][weights["FMOM_K4"]["signal_date"].isin(live_sig)]
    hl = {k: set(h.loc[h["signal_date"] == live_sig[-1], "PERMNO"]) for k, h in holds.items()}
    names = u.drop_duplicates("PERMNO", keep="last").set_index("PERMNO")
    for k in ("FMOM_K4", "ADAPTIVE_T4"):
        x = holds[k][holds[k]["signal_date"].isin(live_sig)].copy()
        x["company_name"] = x["PERMNO"].map(names["company_name"])
        x.to_csv(out / f"live_holdings_{k}.csv", index=False)
    sec = {k: holds[k][holds[k]["signal_date"] == live_sig[-1]].groupby("sector")["weight"].sum() for k in holds}

    pd.set_option("display.width", 220)
    print(bt[bt["period"].isin(["OBS_2019_20", "EVAL_2021_26", "EVAL_2021_25"])].round(3).to_string(index=False))
    print(yr.round(3).to_string())
    print("\nlive window", eval_end, "->", nav.index[-1].date())
    print(live.round(4).to_string())
    print(nav.round(4).to_string())
    print("\nFMOM weights in live weeks\n", wl.round(3).to_string(index=False))
    print("\noverlap of last live holdings FMOM vs ADAPTIVE:", len(hl["FMOM_K4"] & hl["ADAPTIVE_T4"]),
          "of", len(hl["FMOM_K4"]), "/", len(hl["ADAPTIVE_T4"]))
    print(pd.DataFrame(sec).round(2).to_string())
    R.log(f"done -> {out}", t0)


if __name__ == "__main__":
    main()
