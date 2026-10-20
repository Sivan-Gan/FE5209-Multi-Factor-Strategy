"""Live-period tracking: the frozen strategy (configs/sg_strategy.json, unchanged) run forward
from the end of the evaluation period to the last day in the data, with a DAILY NAV.

    python -m src.sg_pipeline.live                      # live window starts after periods.eval_end
    python -m src.sg_pipeline.live --output-dir <folder>

Nothing is re-estimated: the factor choice and weights for 2026 were fixed at the first signal of
2026, and every live rebalance uses the same rules as the backtest.

Daily NAV: holdings are traded at the close of each execution date E_k (cost = one_way x the
two-sided weight traded, from the drifted pre-trade weights) and drift with adjusted prices until
the next execution date.  NAV is rebased to 1 at the close of eval_end (2026-09-30).

Outputs (<output_dir>, default private_data/sg_live_output/):
  live_daily_nav.csv          strategy (10 bp, 30 bp), STI TR, EW universe: daily NAV and return
  live_summary.csv            window return, excess vs STI, vol, max drawdown, best/worst day
  live_holdings.csv           target holdings at each live rebalance (with company names)
  live_trades.csv             trades at each live rebalance
  live_stock_contribution.csv contribution of each stock to the live window return
"""
from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from src.sg_pipeline import run as R
from src.strategy import factor_returns as fr
from src.strategy import metrics as mt


def daily_nav(weeks, adj, start, cost=0.0, gtr=None):
    """weeks: list of (exec_date, next_exec_date, {permno: weight}).  Returns NAV rebased at start."""
    nav, val = {}, 1.0
    for e, n, w in weeks:
        if gtr is not None:
            val *= 1.0 - cost * gtr.get(e, 0.0)
        cols = [p for p in w if p in adj.columns]
        p0 = adj.loc[e, cols]
        days = adj.index[(adj.index > e) & (adj.index <= n)]
        cash = 1.0 - sum(w.values())
        wv = pd.Series({p: w[p] for p in cols})
        for d in days:
            rel = (adj.loc[d, cols] / p0).fillna(1.0)          # no price -> treated as flat
            nav[d] = val * (cash + float((wv * rel).sum()) + sum(v for p, v in w.items() if p not in cols))
        val = nav[days[-1]] if len(days) else val
    s = pd.Series(nav).sort_index()
    return s / s.loc[pd.Timestamp(start)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(R.REPO / "configs/sg_strategy.json"))
    ap.add_argument("--output-dir", default=None)
    args = ap.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    start = cfg["periods"]["eval_end"]
    out = Path(args.output_dir) if args.output_dir else R.resolve("private_data/sg_live_output")
    out.mkdir(parents=True, exist_ok=True)
    work = out / "_steps"
    work.mkdir(exist_ok=True)
    t0 = time.time()
    fr.LEG_FRACTION = cfg["selection"]["factor_leg_fraction"]

    live_cfg = copy.deepcopy(cfg)
    live_cfg["periods"]["eval_end"] = "2099-12-31"            # unseal: same rules, data to the end
    team_dir, sti_file = R.prepare_inputs(cfg)
    u, rets, cal = R.build_universe_and_returns(live_cfg, team_dir, work, t0, open_end=True)
    dates = sorted(x for x in u["signal_date"].unique() if x >= cfg["periods"]["start"])
    fret = R.factor_returns(u, rets, "2099-12-31", t0, work)
    hold, _ = R.select_and_hold(live_cfg, u, rets, fret, dates, "2099-12-31", t0, work)

    d = R.read_parquet(team_dir / "data/sgx_daily_market.parquet",
                       ["gvkey", "iid", "date", "available_adjusted_price"], work)
    d["PERMNO"] = d["gvkey"].astype(str) + "_" + d["iid"].astype(str)
    d["date"] = pd.to_datetime(d["date"]).dt.normalize()
    adj = d.dropna(subset=["available_adjusted_price"]).pivot_table(
        index="date", columns="PERMNO", values="available_adjusted_price", aggfunc="last").sort_index().ffill()
    last_day = adj.index.max()

    cal_s = cal.assign(signal_date=cal["signal_date"].dt.strftime("%Y-%m-%d")).set_index("signal_date")
    t_start = pd.Timestamp(start)
    live_sig = [s for s in cal_s.index if cal_s.loc[s, "next_execution_date"] > t_start]
    path, ledger = mt.weekly_path_and_ledger(hold, rets, "2099-12-31", cfg["costs"]["one_way"])
    gtr = {cal_s.loc[s, "execution_date"]: g for s, g in zip(path["signal_date"], path["gtr"])}

    weeks = [(cal_s.loc[s, "execution_date"], cal_s.loc[s, "next_execution_date"],
              dict(zip(hold.loc[hold["signal_date"] == s, "PERMNO"], hold.loc[hold["signal_date"] == s, "weight"])))
             for s in live_sig]
    uni = [(e, n, {p: 1 / len(g) for p in g}) for (e, n, _), g in
           zip(weeks, [u.loc[u["signal_date"] == s, "PERMNO"].tolist() for s in live_sig])]
    sti = R.load_sti(sti_file)
    sti = sti[(sti.index >= t_start) & (sti.index <= last_day)]
    nav = pd.DataFrame({
        "strategy_net_10bp": daily_nav(weeks, adj, start, cfg["costs"]["one_way"], gtr),
        "strategy_net_30bp": daily_nav(weeks, adj, start, cfg["costs"]["stress_one_way"], gtr),
        "strategy_gross": daily_nav(weeks, adj, start),
        "STI_total_return": sti / sti.iloc[0],
        "EW_universe_gross": daily_nav(uni, adj, start),
    })
    nav = nav[nav.index >= t_start].dropna()
    rows = []
    for c in nav.columns:
        r = nav[c].pct_change().dropna()
        dd = nav[c] / nav[c].cummax() - 1
        rows.append({"series": c, "from_close": start, "to_close": nav.index[-1].strftime("%Y-%m-%d"),
                     "trading_days": len(r), "window_return": nav[c].iloc[-1] - 1,
                     "excess_vs_STI": nav[c].iloc[-1] - nav["STI_total_return"].iloc[-1],
                     "daily_vol_annualised": r.std() * np.sqrt(252), "max_drawdown": -dd.min(),
                     "best_day": r.max(), "worst_day": r.min(), "up_days": int((r > 0).sum())})
    summ = pd.DataFrame(rows)
    nav.index.name = "date"
    nav.join(nav.pct_change().add_suffix("_daily_return")).to_csv(out / "live_daily_nav.csv")
    summ.to_csv(out / "live_summary.csv", index=False)

    names = u.drop_duplicates("PERMNO", keep="last").set_index("PERMNO")["company_name"]
    hl = hold[hold["signal_date"].isin(live_sig)].copy()
    hl.to_csv(out / "live_holdings.csv", index=False)
    tl = ledger[ledger["signal_date"].isin(live_sig)].copy()
    tl.insert(2, "company_name", tl["PERMNO"].map(names))
    tl.to_csv(out / "live_trades.csv", index=False)

    # stock contribution: weight x price change from max(E_k, start) to min(next E_k, last day)
    contrib = []
    for (e, n, w), s in zip(weeks, live_sig):
        a, b = max(e, t_start), min(n, last_day)
        if b <= a:
            continue
        for p, wt in w.items():
            if p in adj.columns:
                drift = wt * adj.loc[a, p] / adj.loc[e, p] if adj.loc[e, p] > 0 else wt
                contrib.append({"signal_date": s, "PERMNO": p, "company_name": names.get(p),
                                "from": a.strftime("%Y-%m-%d"), "to": b.strftime("%Y-%m-%d"),
                                "price_change": adj.loc[b, p] / adj.loc[a, p] - 1,
                                "approx_contribution": drift * (adj.loc[b, p] / adj.loc[a, p] - 1)})
    sc = pd.DataFrame(contrib).groupby(["PERMNO", "company_name"], dropna=False).agg(
        rebalances_held=("signal_date", "nunique"), approx_contribution=("approx_contribution", "sum")
    ).reset_index().sort_values("approx_contribution", ascending=False)
    sc.to_csv(out / "live_stock_contribution.csv", index=False)

    pd.set_option("display.width", 200)
    print("\nlive rebalances:", [(s, cal_s.loc[s, "execution_date"].date(), cal_s.loc[s, "next_execution_date"].date())
                                 for s in live_sig])
    print(summ.round(4).to_string(index=False))
    print(nav.round(4).to_string())
    print(sc.head(5).round(4).to_string(index=False))
    print(sc.tail(5).round(4).to_string(index=False))
    R.log(f"done -> {out}", t0)


if __name__ == "__main__":
    main()
