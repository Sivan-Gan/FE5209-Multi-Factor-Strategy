"""End-to-end pipeline: SG adaptive multi-factor strategy, every step writes its data to disk.

    python -m src.sg_pipeline.run                                  # uses configs/sg_strategy.json
    python -m src.sg_pipeline.run --config configs/sg_strategy.json --output-dir <folder>

Inputs (private, never committed; paths in the config, relative to the repo root):
    SGX_TEAM_HANDOFF.zip                         full SGX universe incl. delisted (WRDS-derived)
    SGX_comprehensive_2006_2026_handoff(1).zip   only for the STI total-return benchmark
  Searched in private_data/ first, then the config's fallback_dirs (e.g. the folder above the repo).
  Extracted once into private_data/SG_data_private/.

Steps and outputs (<output_dir>/):
  01_universe_weekly.csv.gz          eligible stocks per signal week + six family z-scores
  02_security_weekly_returns.csv.gz  execution-to-next-execution total return of eligible stocks
  03_factor_returns_weekly.csv       top-30% minus bottom-30% weekly return of each family
  04_annual_factor_decisions.csv     each year's factor ranking statistics, selected factors, weights
  05_composite_scores_weekly.csv.gz  weekly composite score and rank of every eligible stock
  05_holdings_weekly.csv             target holdings and weights per week
  05_construction_weekly.csv         names held, sector cap, buffer diagnostics per week
  06_portfolio_weekly_returns.csv    gross / net / stress returns, turnover, STI, EW universe, NAV, drawdown
  06_trade_ledger.csv                every trade at each execution date
  07_performance_summary.csv         course metrics per period for strategy, stress, STI, EW universe
  07_yearly_returns.csv              calendar-year returns
  08_attr_decomposition.csv          STI + universe effect + selection/factor tilt + cost = strategy
  08_attr_factor_regression.csv      Newey-West regression on STI and the six factor returns
  08_attr_brinson_sector.csv         allocation / selection / interaction by historical GICS sector
  08_attr_stock_contribution.csv     cumulative weight x return per stock
  run_manifest.json                  config, input files, period boundaries, row counts, run time

Rules (the strategy as frozen for the group, 2026-10-19):
  universe   ORDINARY or REIT, fresh quote in the signal week, price >= 0.20 SGD, 30-day average
             traded value >= 100k SGD, market cap, all six families available
  families   Quality = mean(roe, roa, gross_profitability), Momentum 12-1, Low Risk = 60d volatility,
             Investment = asset growth, Value = book-to-price, Size = -ln(market cap) (small caps);
             each re-standardised (1/99% winsorised z-score) within the weekly universe
  selection  every January: top-4 families by mean/std of the last 3 years' weekly factor returns
  weights    inverse volatility over 52 weeks, bounded 5%-80%
  portfolio  top 20% composite score, keep holdings while inside top 30%, 30% cap per historical
             GICS sector, equal weight, weekly rebalance (signal Friday close, trade next trading day)
  costs      10 bp one-way (30 bp stress); Sharpe uses rf = 0
  sealing    no holding period ending after periods.eval_end (2026-09-30) is used; the live period
             2026-10-01 .. 2026-10-20 stays sealed until the config is changed on purpose
  limits     delisted stocks keep their last price (no delisting haircut); adjusted prices are the
             package's proxy; current-hint security classes; candidate (not certified) PIT data
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src.adaptive_strategy.composite_score import ScoreConfig, calculate_scores
from src.adaptive_strategy.factor_selection import SelectionConfig, select_factors
from src.adaptive_strategy.factor_weighting import WeightingConfig, calculate_selected_weights
from src.strategy import factor_returns as fr
from src.strategy import metrics as mt
from src.strategy import portfolio as pf

REPO = Path(__file__).resolve().parents[2]
FAMILY_COL = {"Quality": "Q", "Momentum": "M", "Low Risk": "LR", "Investment": "INV", "Value": "VAL", "Size": "SIZE"}
GICS = {"10": "Energy", "15": "Materials", "20": "Industrials", "25": "Consumer Discretionary",
        "30": "Consumer Staples", "35": "Health Care", "40": "Financials", "45": "Information Technology",
        "50": "Communication Services", "55": "Utilities", "60": "Real Estate"}
STI_MEMBER = "SGX_comprehensive_2006_2026_handoff/benchmark/STI_Total_Return_LSEG_export.xlsx"


def log(msg: str, t0: float) -> None:
    print(f"[{time.time() - t0:6.0f}s] {msg}", flush=True)


# ------------------------------------------------------------------ 0. inputs
def resolve(p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else REPO / q


def find_input(path: str, fallback_dirs: list[str]) -> Path:
    p = resolve(path)
    if p.exists():
        return p
    stem = p.name
    alts = [stem, stem.replace("(1)", "")]
    for d in fallback_dirs:
        for n in alts:
            q = resolve(d) / n
            if q.exists():
                return q
    sys.exit(f"Input not found: {p}\nPut it in private_data/ or next to the repository (see the config).")


def prepare_inputs(cfg: dict) -> tuple[Path, Path]:
    dc = cfg["data"]
    team_dir = resolve(dc["team_dir"])
    if not (team_dir / "data" / "sgx_weekly_factors.parquet").exists():
        z = find_input(dc["team_zip"], dc.get("fallback_dirs", []))
        print(f"extracting {z.name} -> {team_dir.parent}")
        team_dir.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(z) as zf:
            zf.extractall(team_dir.parent, members=[m for m in zf.namelist() if not m.startswith("__MACOSX")])
    sti = resolve(dc["sti_xlsx"])
    if not sti.exists():
        z = find_input(dc["sti_zip"], dc.get("fallback_dirs", []))
        sti.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(z) as zf:
            sti.write_bytes(zf.read(STI_MEMBER))
    return team_dir, sti


def read_parquet(path: Path, cols: list[str], tmp_dir: Path) -> pd.DataFrame:
    try:
        return pd.read_parquet(path, columns=cols)
    except Exception:                                    # environments without pyarrow: DuckDB CLI
        duck = os.environ.get("DUCKDB", "duckdb")
        tmp = tmp_dir / f"_{path.stem}.csv"
        sel = ", ".join(f'"{c}"' for c in cols)
        subprocess.run([duck, "-c", f"COPY (SELECT {sel} FROM '{path}') TO '{tmp}' (HEADER)"], check=True)
        df = pd.read_csv(tmp, low_memory=False)
        tmp.unlink()
        return df


def load_sti(path: Path) -> pd.Series:
    x = pd.read_excel(path, header=None)
    dt = pd.to_datetime(x[0], errors="coerce", format="mixed")
    v = pd.to_numeric(x[1], errors="coerce")
    ok = dt.notna() & v.notna()
    lvl = pd.Series(v[ok].to_numpy(), index=dt[ok]).sort_index()
    return lvl[~lvl.index.duplicated()]


# ------------------------------------------------- 1-2. universe and returns
def build_universe_and_returns(cfg: dict, team_dir: Path, out: Path, t0: float, open_end: bool = False):
    """open_end=True (live tracking only): the last signal week, whose next execution date does not
    exist yet, is kept and its holding period ends at the last trading day in the data."""
    uc, fc = cfg["universe"], cfg["factors"]
    score_cols = sorted({c for f in fc.values() for c in f.get("scores", [])})
    w = read_parquet(team_dir / "data/sgx_weekly_factors.parquet",
                     ["gvkey", "iid", "signal_date", "quote_observation_state", "marketcap_sgd_candidate",
                      "security_class_candidate", "historical_gics_sector", "average_traded_value__raw_value"]
                     + score_cols, out)
    d = read_parquet(team_dir / "data/sgx_daily_market.parquet",
                     ["gvkey", "iid", "date", "company_name", "price_sgd", "available_adjusted_price"], out)
    for x in (w, d):
        x["PERMNO"] = x["gvkey"].astype(str) + "_" + x["iid"].astype(str)
    w["signal_date"] = pd.to_datetime(w["signal_date"]).dt.normalize()
    d["date"] = pd.to_datetime(d["date"]).dt.normalize()
    names = d.dropna(subset=["company_name"]).drop_duplicates("PERMNO", keep="last").set_index("PERMNO")["company_name"]
    log(f"loaded weekly {len(w):,} rows, daily {len(d):,} rows", t0)

    cnt = d.groupby("date").size()
    tdays = cnt[cnt >= uc["trading_day_min_observations"]].index.sort_values()
    sig = np.sort(w["signal_date"].unique())
    exe = tdays[np.searchsorted(tdays.values, sig, side="right").clip(max=len(tdays) - 1)]
    cal = pd.DataFrame({"signal_date": sig, "execution_date": exe})
    cal = cal[cal["execution_date"] > cal["signal_date"]].drop_duplicates("execution_date").reset_index(drop=True)
    cal["next_execution_date"] = cal["execution_date"].shift(-1)
    if open_end:
        cal["next_execution_date"] = cal["next_execution_date"].fillna(tdays[-1])
        cal = cal[cal["next_execution_date"] > cal["execution_date"]]
    cal = cal.dropna().reset_index(drop=True)
    eval_end = pd.Timestamp(cfg["periods"]["eval_end"])
    cal = cal[cal["next_execution_date"] <= eval_end].reset_index(drop=True)          # live period sealed

    adj = d.dropna(subset=["available_adjusted_price"]).pivot_table(
        index="date", columns="PERMNO", values="available_adjusted_price", aggfunc="last").sort_index().ffill()
    px = d.dropna(subset=["price_sgd"]).pivot_table(
        index="date", columns="PERMNO", values="price_sgd", aggfunc="last").sort_index().ffill()
    ae = adj.reindex(cal["execution_date"], method="ffill").to_numpy()
    an = adj.reindex(cal["next_execution_date"], method="ffill").to_numpy()
    ret = pd.DataFrame(an / ae - 1.0, index=cal["signal_date"], columns=adj.columns)
    rets = ret.stack(future_stack=True).rename("ret").reset_index()
    rets.columns = ["signal_date", "PERMNO", "execution_to_next_execution_total_return"]
    psig = px.reindex(cal["signal_date"], method="ffill").stack(future_stack=True).rename("price_sgd_signal").reset_index()
    psig.columns = ["signal_date", "PERMNO", "price_sgd_signal"]

    u = w.merge(cal, on="signal_date", how="inner").merge(psig, on=["signal_date", "PERMNO"], how="left")
    ok = (u["security_class_candidate"].isin(uc["security_classes"])
          & (u["quote_observation_state"].eq("Fresh") if uc["require_fresh_quote"] else True)
          & (u["price_sgd_signal"] >= uc["min_price_sgd"])
          & (u["average_traded_value__raw_value"] >= uc["min_avg_traded_value_sgd"])
          & (u["marketcap_sgd_candidate"] > 0))
    u = u[ok].copy()
    for fam, spec in fc.items():
        col = FAMILY_COL[fam]
        if "scores" in spec:
            sc = u[spec["scores"]]
            u[col] = sc.mean(axis=1).where(sc.notna().sum(axis=1) >= spec.get("min_available", len(spec["scores"])))
        else:
            u[col] = spec["log_market_cap_sign"] * np.log(u["marketcap_sgd_candidate"])
    u = u.dropna(subset=list(FAMILY_COL.values())).copy()

    def z(s):
        s = s.clip(s.quantile(0.01), s.quantile(0.99))
        return (s - s.mean()) / s.std(ddof=0)
    g = u.groupby("signal_date")
    for col in FAMILY_COL.values():
        u[col] = g[col].transform(z)
        u[f"{col}__available"] = np.isfinite(u[col]).astype(int)
    u["company_name"] = u["PERMNO"].map(names)
    u["sector_code"] = u["historical_gics_sector"].astype(str).str.split(".").str[0]
    u["historical_gics_sector"] = u["sector_code"].map(GICS).fillna("UNKNOWN")
    for c in ("signal_date", "execution_date", "next_execution_date"):
        u[c] = u[c].dt.strftime("%Y-%m-%d")
    rets["signal_date"] = rets["signal_date"].dt.strftime("%Y-%m-%d")
    rets = rets.merge(cal.assign(signal_date=cal["signal_date"].dt.strftime("%Y-%m-%d"),
                                 next_execution_date=cal["next_execution_date"].dt.strftime("%Y-%m-%d"))
                      [["signal_date", "next_execution_date"]], on="signal_date")
    rets["usable"] = np.isfinite(rets["execution_to_next_execution_total_return"]).astype(int)
    rets = rets.merge(u[["signal_date", "PERMNO"]], on=["signal_date", "PERMNO"], how="inner")

    keep = ["signal_date", "execution_date", "next_execution_date", "PERMNO", "gvkey", "iid", "company_name",
            "historical_gics_sector", "security_class_candidate", "price_sgd_signal", "average_traded_value__raw_value",
            "marketcap_sgd_candidate"] + list(FAMILY_COL.values())
    u[keep].to_csv(out / "01_universe_weekly.csv.gz", index=False, compression="gzip")
    rets.to_csv(out / "02_security_weekly_returns.csv.gz", index=False, compression="gzip")
    log(f"01 universe: {len(u):,} stock-weeks, {u.groupby('signal_date').size().mean():.0f} stocks/week", t0)
    return u, rets, cal


# ---------------------------------------------------------- 3. factor returns
def factor_returns(u, rets, end, t0, out):
    dd = u.merge(rets[["signal_date", "PERMNO", "execution_to_next_execution_total_return"]],
                 on=["signal_date", "PERMNO"], how="left")
    dd["execution_feasible"] = np.isfinite(dd["execution_to_next_execution_total_return"]).astype(int)
    dd["unavailable_reason"] = ""
    rows = []
    for s, wk in dd.groupby("signal_date", sort=True):
        nx = str(wk["next_execution_date"].iloc[0])
        ok_end = nx <= end
        for fam, col in FAMILY_COL.items():
            top, bot = fr.pick_leg(wk, col, True), fr.pick_leg(wk, col, False)
            t, nt = fr.leg_return(top) if ok_end else (None, 0)
            b, nb = fr.leg_return(bot) if ok_end else (None, 0)
            av = t is not None and b is not None
            rows.append({"signal_date": s, "next_execution_date": nx if ok_end else "", "family": fam,
                         "top_return": t, "bottom_return": b, "top_count": nt, "bottom_count": nb,
                         "eligible_count": len(wk), "factor_return_gross": (t - b) if av else np.nan,
                         "factor_return_available": int(av)})
    f = pd.DataFrame(rows)
    f.to_csv(out / "03_factor_returns_weekly.csv", index=False)
    log("03 factor returns", t0)
    return f


# ------------------------------------------------ 4-5. selection and holdings
def select_and_hold(cfg, u, rets, fret, dates, end, t0, out):
    sc, wc = cfg["selection"], cfg["weighting"]
    pool = list(FAMILY_COL)
    cols = {f: FAMILY_COL[f] for f in pool}
    cal = pd.to_datetime(pd.Series(dates))
    years = pd.to_datetime(u["signal_date"]).dt.year
    parts, decs = [], []
    for year, cutoff in cal.groupby(cal.dt.year).min().items():
        sel, stats = select_factors(fret, cutoff, pool, SelectionConfig(
            lookback_years=sc["lookback_years"], top_k=sc["top_k"], min_coverage=sc["min_coverage"]))
        wts, _ = calculate_selected_weights(fret, cutoff, sel, WeightingConfig(
            window=wc["window_weeks"], min_weight=wc["min_weight"], max_weight=wc["max_weight"],
            shrinkage=wc["shrinkage"]), covariance_families=pool)
        wsel = wts[wc["method"]]
        for r in stats.itertuples(index=False):
            decs.append({"year": int(year), "cutoff_signal_date": cutoff.strftime("%Y-%m-%d"), "family": r.family,
                         "obs_weeks": r.count, "mean_weekly_factor_return": r.mean, "std": r.std,
                         "selection_score": r.selection_score, "rank": r.rank, "selected": bool(r.selected),
                         "weight": wsel.get(r.family, 0.0)})
        sub = u[(years == year) & u["signal_date"].isin(dates)]
        parts.append(calculate_scores(sub, sel, wsel, ScoreConfig("selected_only"), factor_columns=cols))
    pd.DataFrame(decs).to_csv(out / "04_annual_factor_decisions.csv", index=False)
    log("04 annual factor decisions", t0)
    scored = pd.concat(parts, ignore_index=True)
    scored["signal_date"] = pd.to_datetime(scored["signal_date"]).dt.strftime("%Y-%m-%d")
    scored.to_csv(out / "05_composite_scores_weekly.csv.gz", index=False, compression="gzip")
    pc = cfg["portfolio"]
    pf.SELECT_FRACTION, pf.EXIT_FRACTION, pf.SECTOR_CAP = pc["select_fraction"], pc["exit_fraction"], pc["sector_cap"]
    hold, cons = pf.build_holdings(scored, use_buffer=pc["use_buffer"])
    names = u.drop_duplicates("PERMNO").set_index("PERMNO")["company_name"]
    hold.insert(3, "company_name", hold["PERMNO"].map(names))
    hold.to_csv(out / "05_holdings_weekly.csv", index=False)
    cons.to_csv(out / "05_construction_weekly.csv", index=False)
    log(f"05 holdings: {cons['n_held'].mean():.1f} names on average", t0)
    return hold, cons


# ------------------------------------------------------------ 6. backtest
def backtest(cfg, hold, rets, u, cal, sti_lvl, dates, end, t0, out):
    c = cfg["costs"]
    path, ledger = mt.weekly_path_and_ledger(hold, rets, end, c["one_way"])
    path = path.set_index("signal_date")
    path["net_return"] = mt.net_returns(path, c["one_way"])
    path["net_return_stress"] = mt.net_returns(path, c["stress_one_way"])
    cal_s = cal.assign(signal_date=cal["signal_date"].dt.strftime("%Y-%m-%d")).set_index("signal_date")
    path["execution_date"] = cal_s.loc[path.index, "execution_date"].dt.strftime("%Y-%m-%d").to_numpy()
    path["next_execution_date"] = cal_s.loc[path.index, "next_execution_date"].dt.strftime("%Y-%m-%d").to_numpy()
    path["sti_total_return"] = [sti_lvl.loc[:pd.Timestamp(n)].iloc[-1] / sti_lvl.loc[:pd.Timestamp(e)].iloc[-1] - 1
                                for e, n in zip(path["execution_date"], path["next_execution_date"])]
    ew = u.merge(rets, on=["signal_date", "PERMNO"]).groupby("signal_date")["execution_to_next_execution_total_return"].mean()
    path["ew_universe_return_gross"] = ew.reindex(path.index)
    for c_, n_ in (("net_return", "nav_strategy"), ("sti_total_return", "nav_sti")):
        path[n_] = (1 + path[c_]).cumprod()
    path["drawdown_strategy"] = path["nav_strategy"] / path["nav_strategy"].cummax() - 1
    path.reset_index().to_csv(out / "06_portfolio_weekly_returns.csv", index=False)
    ledger.to_csv(out / "06_trade_ledger.csv", index=False)
    assert (path["next_execution_date"] <= cfg["periods"]["eval_end"]).all(), "live period leaked"
    log(f"06 backtest: {len(path)} weeks to {path['next_execution_date'].iloc[-1]}", t0)
    return path


# ---------------------------------------------------------- 7. performance
def performance(cfg, path, hold, rets, t0, out):
    per = {k: v for k, v in cfg["periods"].items() if isinstance(v, list)}
    series = {"strategy_net": path["net_return"], "strategy_net_stress": path["net_return_stress"],
              "strategy_gross": path["gross_return"], "STI_total_return": path["sti_total_return"],
              "EW_universe_gross": path["ew_universe_return_gross"]}
    rows = []
    for pname, (a, b) in per.items():
        for sname, r in series.items():
            s = r[(r.index >= a) & (r.index <= b)].dropna()
            if len(s) < 5:
                continue
            p = mt.performance(s, None, path["sti_total_return"].reindex(s.index))
            row = {"period": pname, "series": sname, "start": a, "end": b, **p}
            if sname == "strategy_net":
                h = hold[hold["signal_date"].between(a, b)]
                row.update(mt.trade_stats(h, rets, b, cfg["costs"]["one_way"]))
                row["avg_names_held"] = float(h.groupby("signal_date").size().mean())
                row["annual_one_way_turnover"] = float(path.loc[s.index, "turnover"].mean() * 52)
            rows.append(row)
    pd.DataFrame(rows).to_csv(out / "07_performance_summary.csv", index=False)
    yr = pd.DataFrame({k: (1 + v).groupby(v.index.str[:4]).prod() - 1 for k, v in series.items()})
    yr["excess_net_vs_STI"] = yr["strategy_net"] - yr["STI_total_return"]
    yr.index.name = "year"
    yr.to_csv(out / "07_yearly_returns.csv")
    log("07 performance", t0)
    return pd.DataFrame(rows)


# ---------------------------------------------------------- 8. attribution
def ols_nw(y, X, lags=4):
    X = np.column_stack([np.ones(len(y)), X])
    b = np.linalg.lstsq(X, y, rcond=None)[0]
    e = y - X @ b
    inv = np.linalg.inv(X.T @ X)
    S = (X * e[:, None]).T @ (X * e[:, None])
    for l in range(1, lags + 1):
        G = (X[l:] * e[l:, None]).T @ (X[:-l] * e[:-l, None])
        S += (1 - l / (lags + 1)) * (G + G.T)
    se = np.sqrt(np.diag(inv @ S @ inv))
    r2 = 1 - (e @ e) / ((y - y.mean()) @ (y - y.mean()))
    return b, se, r2


def attribution(cfg, path, hold, u, rets, fret, t0, out):
    per = {k: v for k, v in cfg["periods"].items() if isinstance(v, list)}
    net, gross = path["net_return"], path["gross_return"]
    sti, ewu = path["sti_total_return"], path["ew_universe_return_gross"]
    F = fret[fret["factor_return_available"] == 1].pivot(index="signal_date", columns="family",
                                                         values="factor_return_gross").reindex(path.index)[list(FAMILY_COL)]
    uu = u.merge(rets[["signal_date", "PERMNO", "execution_to_next_execution_total_return"]],
                 on=["signal_date", "PERMNO"]).rename(columns={"execution_to_next_execution_total_return": "r"})
    h = hold.merge(uu[["signal_date", "PERMNO", "r"]], on=["signal_date", "PERMNO"], how="left")
    h["r"] = h["r"].fillna(0.0)
    dec, reg, brin = [], [], []
    for pname, (a, b) in per.items():
        m = (path.index >= a) & (path.index <= b)
        n = int(m.sum())
        if n < 10:
            continue
        ann = lambda s: float(s[m].mean() * 52)
        dec.append({"period": pname, "weeks": n, "STI": ann(sti), "universe_effect": ann(ewu - sti),
                    "selection_and_factor_tilt": ann(gross - ewu), "cost_drag": ann(net - gross),
                    "strategy_net": ann(net),
                    "strategy_net_CAGR": float((1 + net[m]).prod() ** (52 / n) - 1),
                    "STI_CAGR": float((1 + sti[m]).prod() ** (52 / n) - 1)})
        X = pd.concat([sti.rename("Market (STI)"), F], axis=1)[m].dropna()
        bcoef, se, r2 = ols_nw(net.reindex(X.index).to_numpy(), X.to_numpy())
        for nm, bb, ss, mu in zip(["alpha"] + list(X.columns), bcoef, se, [np.nan] + list(X.mean())):
            reg.append({"period": pname, "term": nm, "loading": bb, "t_stat_newey_west": bb / ss,
                        "factor_mean_annual": mu * 52, "contribution_annual": bb * (1 if nm == "alpha" else mu) * 52,
                        "r_squared": r2, "weeks": len(X)})
        hp, bp = h[h["signal_date"].isin(path.index[m])], uu[uu["signal_date"].isin(path.index[m])]
        acc = {}
        for s, g in hp.groupby("signal_date"):
            bw = bp[bp["signal_date"] == s]
            rb = bw["r"].mean()
            wb = bw.groupby("historical_gics_sector").size() / len(bw)
            rbs = bw.groupby("historical_gics_sector")["r"].mean()
            gs = g.groupby("sector")
            wp = gs["weight"].sum() / g["weight"].sum()
            rps = gs.apply(lambda x: np.average(x["r"], weights=x["weight"]), include_groups=False)
            secs = wb.index.union(wp.index)
            wb, wp = wb.reindex(secs, fill_value=0.0), wp.reindex(secs, fill_value=0.0)
            rbs = rbs.reindex(secs).fillna(rb)
            rps = rps.reindex(secs).fillna(rbs)
            for sec in secs:
                x = acc.setdefault(sec, np.zeros(5))
                x += [(wp[sec] - wb[sec]) * (rbs[sec] - rb), wb[sec] * (rps[sec] - rbs[sec]),
                      (wp[sec] - wb[sec]) * (rps[sec] - rbs[sec]), wp[sec], wb[sec]]
        k = 52 / n
        rows = [{"period": pname, "sector": sec, "allocation": v[0] * k, "selection": v[1] * k,
                 "interaction": v[2] * k, "avg_portfolio_weight": v[3] / n, "avg_universe_weight": v[4] / n}
                for sec, v in acc.items()]
        tot = pd.DataFrame(rows)[["allocation", "selection", "interaction"]].sum()
        brin += rows + [{"period": pname, "sector": "TOTAL", **tot.to_dict()}]
    pd.DataFrame(dec).to_csv(out / "08_attr_decomposition.csv", index=False)
    pd.DataFrame(reg).to_csv(out / "08_attr_factor_regression.csv", index=False)
    pd.DataFrame(brin).to_csv(out / "08_attr_brinson_sector.csv", index=False)
    a, b = per[cfg["attribution_period"]]
    he = h[h["signal_date"].between(a, b)].assign(contribution=lambda x: x["weight"] * x["r"])
    sc = he.groupby(["PERMNO", "company_name"], dropna=False).agg(
        sector=("sector", "last"), weeks_held=("signal_date", "nunique"),
        contribution=("contribution", "sum")).reset_index().sort_values("contribution", ascending=False)
    sc.insert(0, "period", cfg["attribution_period"])
    sc.to_csv(out / "08_attr_stock_contribution.csv", index=False)
    log("08 attribution", t0)
    return pd.DataFrame(dec), pd.DataFrame(reg)


# ------------------------------------------------------------------ main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(REPO / "configs/sg_strategy.json"))
    ap.add_argument("--output-dir", default=None)
    args = ap.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    out = Path(args.output_dir) if args.output_dir else resolve(cfg["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    fr.LEG_FRACTION = cfg["selection"]["factor_leg_fraction"]
    team_dir, sti_file = prepare_inputs(cfg)
    u, rets, cal = build_universe_and_returns(cfg, team_dir, out, t0)
    end = cfg["periods"]["eval_end"]
    dates = sorted(x for x in u["signal_date"].unique() if x >= cfg["periods"]["start"])
    fret = factor_returns(u, rets, end, t0, out)
    hold, cons = select_and_hold(cfg, u, rets, fret, dates, end, t0, out)
    path = backtest(cfg, hold, rets, u[u["signal_date"].isin(dates)], cal, load_sti(sti_file), dates, end, t0, out)
    perf = performance(cfg, path, hold, rets, t0, out)
    dec, reg = attribution(cfg, path, hold, u[u["signal_date"].isin(dates)], rets, fret, t0, out)
    manifest = {"run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "config": cfg,
                "inputs": {"team_dir": str(team_dir), "sti_xlsx": str(sti_file)},
                "first_signal": dates[0], "last_signal_used": path.index[-1],
                "last_holding_period_end": path["next_execution_date"].iloc[-1],
                "weeks": int(len(path)), "runtime_seconds": round(time.time() - t0, 1),
                "outputs": sorted(p.name for p in out.iterdir() if p.is_file())}
    (out / "run_manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    pd.set_option("display.width", 200)
    show = perf[perf["series"].isin(["strategy_net", "STI_total_return"])][
        ["period", "series", "ann_return", "ann_vol", "sharpe", "max_drawdown", "calmar"]]
    print("\n" + show.round(3).to_string(index=False))
    print("\n" + dec.round(4).to_string(index=False))
    log(f"done -> {out}", t0)


if __name__ == "__main__":
    main()
