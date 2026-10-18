"""Exploratory portfolio-construction variants on the frozen FMOM scores (NOT the frozen strategy).

    python -m src.strategy.explore_variants              # base cost 10 bp one-way, stress 20 bp
    python -m src.strategy.explore_variants --cost-bps 15

STATUS: ADAPT_VALIDATION (SSOT Step 40).  The 2021-2025 results of the frozen strategy have
already been seen, so the 2021-2025 numbers below are NOT a clean out-of-sample test for any
variant chosen after looking at them.  The frozen strategy in config/strategy/us_base_strategy.json
is not changed by this script and stays the official live strategy.

What stays fixed (from the frozen json): K=4 factors, J=26, risk-adjusted FMOM, lambda=0.5,
FMOM window rule, min 4 factors, buffer on, 30% sector cap, weekly rebalance S_t -> E_t, cost.
What varies (portfolio construction only):
  - number of names: top 20% (frozen, ~100) or a fixed N = 100 / 50 / 30;
  - weighting: equal weight, or score weight w_i = s_i / sum_j s_j over the names held
    (s floored at SCORE_FLOOR so a low-ranked buffer name never gets a zero / negative weight),
    then sector weights capped at 30% (excess moved pro rata to the other sectors).
Buffer for fixed N: a holding is kept while it ranks inside 1.5 x N (same 20% -> 30% ratio).
Weight buffer (no-trade band, score weighting only), band b in {0, 10%, 20%}:
  a name held last week whose new score weight differs from LAST WEEK'S TARGET weight by
  |w_new - w_prev| / w_prev <= b keeps w_prev; the other names share the remaining weight in
  proportion to their new score weights; then the 30% sector cap is applied again.
  Only S_t information is used (this week's scores and last week's target), never the
  Friday-to-Monday drift, so there is no look-ahead.  Drift is still rebalanced back to the
  target at E_t exactly as in the frozen accounting.

Market-cap multiplier (_MV variants): every weight above is multiplied by the stock's market
  cap on the signal date S_t (issuer_market_cap in factor_scores_weekly, split equally across an
  issuer's share classes so e.g. Alphabet is not counted twice), then renormalised and the 30%
  sector cap applied.  EW x MV = plain market-cap weighting of the selected names; SW x MV =
  score x market cap.  A selected name without a market cap gets the smallest cap held that week.

Comparison rows: S&P 500 total return, all eligible stocks equal weight (EW_UNIVERSE), the same
frozen construction with static equal factor weights (STATIC_LAM0, lambda = 0) and the frozen
strategy itself (FROZEN_BASE, must reproduce outputs/strategy/frozen/*/weekly_path.csv).

The strategy is run continuously from 2016-01-01 to 2025-12-31 and then cut into
2016-2020 (in-sample, Gate B period) and 2021-2025 (historical OOS).
Metrics are WEEKLY (metrics.py conventions, 52 / year), not the daily-NAV final numbers.

Outputs (outputs/strategy/adapt_explore/):
  variant_summary.csv   one row per variant x period
  variant_yearly.csv    net return per calendar year
  variant_weekly.csv    weekly gross / net return and NAV
  holdings_<variant>.csv
  megacap_coverage.csv  the 10 largest eligible stocks at each year end and what we held of them
  report.html           one-page report (NAV charts + tables), open in a browser
"""
from __future__ import annotations

import argparse
import html
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src.strategy import composite_score as cs
from src.strategy import factor_returns as fr
from src.strategy import fmom_weights as fw
from src.strategy import governance as gv
from src.strategy import metrics as mt
from src.strategy import portfolio as pf
from src.strategy import run_gate_b as rg

RUN_FROM = "2016-01-01"
RUN_TO = "2025-12-31"
PERIODS = {"IS_2016_2020": ("2016-01-01", "2020-12-31"), "OOS_2021_2025": ("2021-01-01", "2025-12-31")}
SCORE_FLOOR = 0.01          # score weighting: s_i = max(composite score, SCORE_FLOOR)
BUFFER_EXIT_MULT = 1.5      # fixed-N buffer: keep while rank <= 1.5 N
OUT_DIR = "outputs/strategy/adapt_explore"

# name, number of names (None = frozen top 20%), weighting, score set, weight band, report label
# name, number of names (None = frozen top 20%), weighting, score set, weight band, x market cap, label
VARIANTS = [("FROZEN_BASE", None, "equal", "fmom", 0.0, False, "冻结策略：前20%（约100只）等权")]
for _mv in (False, True):
    _sfx, _lab = ("_MV", " ×市值") if _mv else ("", "")
    for _n in (100, 50, 30):
        VARIANTS.append((f"N{_n}_EW{_sfx}", _n, "equal", "fmom", 0.0, _mv,
                         f"{_n}只 " + ("市值加权" if _mv else "等权")))
        for _b in (0.0, 0.10, 0.20):
            VARIANTS.append((f"N{_n}_SW_B{int(round(_b * 100))}{_sfx}", _n, "score", "fmom", _b, _mv,
                             f"{_n}只 分数{_lab}加权，" + ("无权重缓冲" if _b == 0 else f"权重缓冲 {_b:.0%}")))
VARIANTS.append(("STATIC_LAM0", None, "equal", "static", 0.0, False, "对照：同框架但因子等权（λ=0，无择时）"))
BENCH_LABELS = {"EW_UNIVERSE": "对照：全部合格股票等权", "SP500": "标普500 全收益"}


# ------------------------------------------------------------ 1. weights
def cap_sectors(w: np.ndarray, sectors: np.ndarray, cap: float = pf.SECTOR_CAP) -> np.ndarray:
    """Scale every sector above `cap` down to it and give the excess to the uncapped sectors
    in proportion to their weights; repeat until no sector is above the cap.  If every
    sector is capped the rest stays in cash."""
    w = w.astype(float).copy()
    capped = set()
    for _ in range(100):
        sums = pd.Series(w).groupby(sectors).sum()
        over = sums[sums > cap + 1e-12]
        if over.empty:
            break
        excess = 0.0
        for sec, val in over.items():
            m = sectors == sec
            w[m] *= cap / val
            excess += val - cap
            capped.add(sec)
        free = ~np.isin(sectors, list(capped))
        if not free.any() or w[free].sum() <= 0:
            break                                   # nowhere to put it: stays in cash
        w[free] += excess * w[free] / w[free].sum()
    return w


def score_weights(hold: pd.DataFrame, total: float, cap: float = pf.SECTOR_CAP) -> tuple[np.ndarray, int]:
    """w_i proportional to max(score_i, SCORE_FLOOR), summing to `total`, each sector <= cap.
    Returns (weights, number of names whose score was floored)."""
    s = hold["composite_score"].to_numpy(dtype=float)
    floored = int((s < SCORE_FLOOR).sum())
    w = np.maximum(s, SCORE_FLOOR)
    w = total * w / w.sum()
    return cap_sectors(w, hold["sector"].to_numpy(), cap), floored


def make_weights(hold: pd.DataFrame, total: float, weighting: str, use_mv: bool,
                 cap: float = pf.SECTOR_CAP) -> tuple[np.ndarray, int, int]:
    """Base weight (1 or max(score, SCORE_FLOOR)) x market cap (if use_mv), summing to `total`,
    sector-capped.  Returns (weights, names floored, names with a missing market cap)."""
    floored = mv_missing = 0
    if weighting == "equal":
        base = np.ones(len(hold))
    elif weighting == "score":
        sc = hold["composite_score"].to_numpy(dtype=float)
        floored = int((sc < SCORE_FLOOR).sum())
        base = np.maximum(sc, SCORE_FLOOR)
    else:
        raise ValueError(f"unknown weighting {weighting!r}")
    if use_mv:
        mv = hold["mv"].to_numpy(dtype=float) if "mv" in hold else np.full(len(hold), np.nan)
        bad = ~(np.isfinite(mv) & (mv > 0))
        mv_missing = int(bad.sum())
        fill = mv[~bad].min() if (~bad).any() else 1.0
        base = base * np.where(bad, fill, mv)
    w = total * base / base.sum()
    return cap_sectors(w, hold["sector"].to_numpy(), cap), floored, mv_missing


def apply_band(hold: pd.DataFrame, prev_target: dict, band: float,
               cap: float = pf.SECTOR_CAP) -> tuple[np.ndarray, int]:
    """No-trade band on target weights.  Uses only this week's new target (hold['weight'])
    and last week's target `prev_target`, both known at S_t.  Returns (weights, names kept)."""
    w_new = hold["weight"].to_numpy(dtype=float)
    if band <= 0 or not len(hold) or not prev_target:
        return w_new, 0
    prev = hold["PERMNO"].map(prev_target).to_numpy(dtype=float)          # NaN = new name
    with np.errstate(invalid="ignore", divide="ignore"):
        keep = np.isfinite(prev) & (prev > 0) & (np.abs(w_new - prev) / prev <= band + 1e-12)
    if not keep.any():
        return w_new, 0
    total = w_new.sum()
    w = w_new.copy()
    w[keep] = prev[keep]
    rest, adj = total - prev[keep].sum(), w_new[~keep].sum()
    if (~keep).any():
        if rest <= 0 or adj <= 0:
            return w_new, 0                         # band not feasible this week: trade to target
        w[~keep] = w_new[~keep] * rest / adj
    else:
        w = w * total / w.sum()                      # every name kept: keep proportions, same total
    return cap_sectors(w, hold["sector"].to_numpy(), cap), int(keep.sum())


# ---------------------------------------------------------- 2. selection
def select_week_fixed(ranked: pd.DataFrame, previous: set[str], n_target: int, use_buffer: bool,
                      weighting: str, use_mv: bool = False) -> tuple[pd.DataFrame, dict]:
    """Same algorithm as portfolio.select_week (SSOT 4.7 algorithm 2) with a fixed N."""
    n = len(ranked)
    n_target = min(n_target, n)
    n_exit = math.floor(BUFFER_EXIT_MULT * n_target)
    cap_names = math.floor(pf.SECTOR_CAP * n_target)
    if use_buffer and previous:
        keep = ranked["PERMNO"].isin(previous) & (ranked["rank"] <= n_exit)
        order = pd.concat([ranked[keep], ranked[~keep]])
    else:
        order = ranked
    picked, sector_count, skipped = [], {}, 0
    for row in order.itertuples(index=False):
        if len(picked) == n_target or cap_names < 1:
            break
        if sector_count.get(row.sector, 0) >= cap_names:
            skipped += 1
            continue
        sector_count[row.sector] = sector_count.get(row.sector, 0) + 1
        picked.append(row)
    hold = pd.DataFrame(picked, columns=order.columns)
    held_before = hold["PERMNO"].isin(previous)
    hold["entry"] = np.where(~held_before, "NEW",
                             np.where(hold["rank"] > n_target, "KEPT_BY_BUFFER", "KEPT"))
    return _apply_weights(hold, n_target, weighting, n, cap_names, skipped, use_mv)


def _apply_weights(hold: pd.DataFrame, n_target: int, weighting: str, n: int, cap_names: int,
                   skipped: int, use_mv: bool = False) -> tuple[pd.DataFrame, dict]:
    total = len(hold) / n_target                  # unfilled slots stay in cash, as in the frozen rule
    floored = mv_missing = 0
    if weighting not in ("equal", "score"):
        raise ValueError(f"unknown weighting {weighting!r}")
    if weighting == "equal" and not use_mv:
        hold["weight"] = 1.0 / n_target
    elif len(hold):
        hold["weight"], floored, mv_missing = make_weights(hold, total, weighting, use_mv)
    else:
        hold["weight"] = np.array([])
    sector_w = hold.groupby("sector")["weight"].sum()
    stats = {"n_scored": n, "n_target": n_target, "n_held": len(hold), "sector_cap_names": cap_names,
             "n_skipped_by_sector_cap": skipped, "cash_weight": 1.0 - hold["weight"].sum(),
             "constraint_infeasible": int(len(hold) < n_target),
             "max_sector_weight": float(sector_w.max()) if len(sector_w) else 0.0,
             "max_name_weight": float(hold["weight"].max()) if len(hold) else 0.0,
             "n_score_floored": floored, "n_mv_missing": mv_missing}
    return hold, stats


def build_variant(scored: pd.DataFrame, n_names: int | None, weighting: str,
                  use_buffer: bool, band: float = 0.0,
                  use_mv: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Weekly holdings for one variant.  n_names=None and equal weight = portfolio.build_holdings.
    Week t uses only rows with signal_date = t plus last week's holdings (no look-ahead)."""
    if n_names is None and weighting == "equal" and band == 0 and not use_mv:
        hold, cons = pf.build_holdings(scored, use_buffer)
        cons["max_name_weight"] = 1.0 / cons["n_target"]
        cons["n_score_floored"] = 0
        cons["n_band_kept"] = 0
        cons["n_mv_missing"] = 0
        return hold, cons
    scores = scored.copy()
    scores["PERMNO"] = scores["PERMNO"].astype(str)
    sector = scores["historical_gics_sector"].astype(object)
    scores["sector"] = (sector.where(sector.notna(), pf.UNKNOWN_SECTOR).astype(str).str.strip()
                        .replace({"nan": pf.UNKNOWN_SECTOR, "": pf.UNKNOWN_SECTOR, "None": pf.UNKNOWN_SECTOR}))
    holdings, weekly, previous, prev_target = [], [], set(), {}
    for signal_date, week in scores.groupby("signal_date", sort=True):
        ranked = pf.rank_week(week)
        if n_names is None:                      # top 20% with score weights
            base, _ = pf.select_week(ranked, previous, use_buffer)
            base = base.drop(columns=["weight"])
            n_t = max(1, math.floor(pf.SELECT_FRACTION * len(ranked)))
            hold, stats = _apply_weights(base, n_t, weighting, len(ranked),
                                         math.floor(pf.SECTOR_CAP * n_t), 0, use_mv)
        else:
            hold, stats = select_week_fixed(ranked, previous, n_names, use_buffer, weighting, use_mv)
        stats["n_band_kept"] = 0
        if band > 0:
            hold["weight"], stats["n_band_kept"] = apply_band(hold, prev_target, band)
            sector_w = hold.groupby("sector")["weight"].sum()
            stats.update({"cash_weight": 1.0 - hold["weight"].sum(),
                          "max_sector_weight": float(sector_w.max()) if len(sector_w) else 0.0,
                          "max_name_weight": float(hold["weight"].max()) if len(hold) else 0.0})
        stats["signal_date"] = signal_date
        weekly.append(stats)
        holdings.append(hold.assign(signal_date=signal_date))
        previous = set(hold["PERMNO"])
        prev_target = dict(zip(hold["PERMNO"], hold["weight"]))
    cols = ["signal_date", "execution_date", "PERMNO", "sector", "composite_score", "rank", "entry", "weight"]
    return pd.concat(holdings, ignore_index=True)[cols], pd.DataFrame(weekly)


def check_variant(hold: pd.DataFrame, cons: pd.DataFrame) -> list[str]:
    problems = []
    if (hold["weight"] < -1e-15).any():
        problems.append("negative weight")
    if hold.duplicated(["signal_date", "PERMNO"]).any():
        problems.append("duplicate holding")
    total = hold.groupby("signal_date")["weight"].sum().reindex(cons["signal_date"]).fillna(0).to_numpy()
    if not np.allclose(total + cons["cash_weight"].to_numpy(), 1.0, atol=1e-10):
        problems.append("weights + cash != 1")
    if (cons["max_sector_weight"] > pf.SECTOR_CAP + 1e-9).any():
        problems.append("sector cap violated")
    if (cons["n_held"] > cons["n_target"]).any():
        problems.append("more names than target")
    return problems


# ------------------------------------------------------------ 3. metrics
def period_weeks(path: pd.DataFrame, start: str, end: str) -> list[str]:
    """Rebalance weeks whose whole holding period lies in [start, end] (same cut as run_frozen)."""
    m = (path["signal_date"] >= start) & (path["period_end"] <= end)
    return sorted(path.loc[m, "signal_date"])


def period_metrics(path: pd.DataFrame, hold: pd.DataFrame | None, returns: pd.DataFrame | None,
                   rf: pd.Series, bench: pd.Series, weeks: list[str], end: str,
                   cost: float) -> tuple[dict, pd.DataFrame]:
    p = path[path["signal_date"].isin(weeks)].set_index("signal_date").sort_index()
    if len(p) < 2:
        return {"n_weeks": len(p)}, pd.DataFrame(columns=["signal_date", "gross_return", "net_return", "nav"])
    net = mt.net_returns(p, cost)
    stress = mt.net_returns(p, cost * rg.STRESS_MULTIPLIER)
    perf = mt.performance(net, rf, bench)
    row = {k: perf.get(k) for k in ("n_weeks", "ann_return", "ann_vol", "sharpe", "sortino", "max_drawdown",
                                    "calmar", "bench_ann_return", "active_ann_return", "tracking_error",
                                    "information_ratio", "beta", "period_hit_rate", "worst_week")}
    row["gross_ann_return"] = mt.performance(p["gross_return"])["ann_return"]
    row["stress_sharpe"] = mt.performance(stress, rf)["sharpe"]
    row["stress_ann_return"] = mt.performance(stress)["ann_return"]
    row["avg_weekly_turnover"] = float(p["turnover"].mean())
    row["cost_drag_ann"] = row["gross_ann_return"] - row["ann_return"]
    row["break_even_oneway_cost"] = mt.break_even_cost(p.reset_index())
    if hold is not None:
        h = hold[hold["signal_date"].isin(weeks)]
        ts = mt.trade_stats(h, returns, end, cost)
        row.update({k: ts.get(k) for k in ("avg_holding_weeks", "trade_win_rate", "trades_per_year")})
    weekly = pd.DataFrame({"signal_date": p.index, "gross_return": p["gross_return"].to_numpy(),
                           "net_return": net.to_numpy()})
    weekly["nav"] = (1.0 + weekly["net_return"]).cumprod()
    return row, weekly


def bench_path(series: pd.Series) -> pd.DataFrame:
    """A benchmark return series in path form (no turnover, no cost)."""
    s = series.dropna()
    return pd.DataFrame({"signal_date": s.index, "gross_return": s.to_numpy(), "gtr": 0.0, "turnover": 0.0})


# --------------------------------------------------------------- 4. data
def split_issuer_cap(df: pd.DataFrame) -> pd.DataFrame:
    """signal_date, PERMNO, mv: issuer market cap on S_t split equally over the issuer's
    eligible share classes that week (NaN if unavailable)."""
    d = df[df["eligible_universe_flag"] == 1].copy()
    for c in ("signal_date", "PERMNO", "PERMCO"):
        d[c] = d[c].astype(str)
    cap = d["issuer_market_cap"].astype(float)
    ok = (d["issuer_market_cap_available"] == 1) & np.isfinite(cap) & (cap > 0)
    n_cls = d.groupby(["signal_date", "PERMCO"])["PERMNO"].transform("count")
    d["mv"] = np.where(ok, cap / n_cls, np.nan)
    return d[["signal_date", "PERMNO", "PERMCO", "mv"]]


def load_market_caps(root: Path) -> pd.DataFrame:
    df = pd.read_parquet(root / cs.SCORES_FILE,
                         columns=["signal_date", "PERMNO", "PERMCO", "eligible_universe_flag",
                                  "issuer_market_cap", "issuer_market_cap_available"],
                         filters=[("signal_date", ">=", RUN_FROM), ("signal_date", "<=", RUN_TO)])
    return split_issuer_cap(df)


def load_all(root: Path) -> dict:
    """Everything the run needs, 2016-01-01 .. 2025-12-31 (requires the frozen json)."""
    cfg = gv.load_config(root)
    p = cfg["parameters"]
    gv.check_window(RUN_TO, True, root)          # refuses unless the strategy is FROZEN
    fret = fr.compute_factor_returns(*fr.load_inputs(root, RUN_FROM, RUN_TO, True), end=RUN_TO)
    seed = fw._clean(pd.read_parquet(root / fw.SEED_FILE), "seed")
    hist = fw.combine_history([seed, fw.fill_seed_boundary(root, seed), fw._clean(fret, "step16")])
    wide, next_exec = fw.to_wide(hist)
    dates = [d for d in wide.index if RUN_FROM <= d <= RUN_TO]
    rule = p.get("fmom_window_rule", "strict_calendar")
    w_fmom = fw.compute_weights(wide, next_exec, dates, p["fmom_lookback_weeks_J"], p["lambda"],
                                p["risk_adjusted_fmom"], rule)
    w_static = fw.compute_weights(wide, next_exec, dates, p["fmom_lookback_weeks_J"], 0.0,
                                  p["risk_adjusted_fmom"], rule)
    return {
        "cfg": cfg,
        "mcap": load_market_caps(root),
        "scores": cs.load_scores(root, RUN_FROM, RUN_TO, True),
        "w_fmom": cs.weights_for_config(w_fmom, p["fmom_lookback_weeks_J"], p["lambda"], p["risk_adjusted_fmom"]),
        "w_static": cs.weights_for_config(w_static, p["fmom_lookback_weeks_J"], 0.0, p["risk_adjusted_fmom"]),
        "returns": rg.load_returns(root, RUN_FROM, RUN_TO),
        "rf": rg.load_weekly_series(root, rg.RF_FILE, "risk_free_return", RUN_FROM, RUN_TO),
        "bench": rg.load_weekly_series(root, rg.BENCH_FILE, "benchmark_total_return", RUN_FROM, RUN_TO),
    }


# ---------------------------------------------------------------- 5. run
def run_all(data: dict, cost: float) -> dict:
    p = data["cfg"]["parameters"]
    min_f, buf = p["min_available_factors"], p["buffer"]
    mcap = data["mcap"][["signal_date", "PERMNO", "mv"]]
    scored = {k: cs.compute_scores(data["scores"], data[w], min_f).merge(mcap, on=["signal_date", "PERMNO"],
                                                                         how="left")
              for k, w in (("fmom", "w_fmom"), ("static", "w_static"))}
    returns, rf, bench = data["returns"], data["rf"], data["bench"]

    summary, weekly_all, yearly, holdings, checks = [], [], [], {}, {}

    weeks = {}

    def add(name, label, path, hold):
        if not weeks:                                  # fixed by the first (frozen) path
            weeks.update({k: period_weeks(path, s, e) for k, (s, e) in PERIODS.items()})
        for period, (_, end) in PERIODS.items():
            row, wk = period_metrics(path, hold, returns, rf, bench, weeks[period], end, cost)
            summary.append({"variant": name, "label": label, "period": period, **row})
            weekly_all.append(wk.assign(variant=name, period=period))
            yr = mt.yearly_returns(wk.set_index("signal_date")["net_return"], wk["signal_date"])
            yearly.append({"variant": name, "label": label, "period": period, **{str(y): v for y, v in yr.items()}})

    for name, n_names, weighting, score_set, band, use_mv, label in VARIANTS:
        t = time.time()
        hold, cons = build_variant(scored[score_set], n_names, weighting, buf, band, use_mv)
        problems = check_variant(hold, cons)
        path, _ = mt.weekly_path_and_ledger(hold, returns, RUN_TO, cost)
        add(name, label, path, hold)
        holdings[name] = hold
        checks[name] = {"checks": "PASS" if not problems else "; ".join(problems),
                        "avg_names_held": float(cons["n_held"].mean()),
                        "avg_max_name_weight": float(cons["max_name_weight"].mean()),
                        "max_sector_weight_seen": float(cons["max_sector_weight"].max()),
                        "weeks_with_cash": int(cons["constraint_infeasible"].sum()),
                        "names_score_floored": int(cons["n_score_floored"].sum()),
                        "weight_band": band, "times_market_cap": int(use_mv),
                        "names_mv_missing": int(cons["n_mv_missing"].sum()),
                        "avg_share_kept_by_band": float((cons["n_band_kept"] / cons["n_held"].clip(lower=1)).mean())}
        print(f"  {name:<16} {checks[name]['checks']:<5} avg names {checks[name]['avg_names_held']:.1f}  "
              f"avg max name weight {checks[name]['avg_max_name_weight']:.3f}  "
              f"({time.time() - t:.0f}s)")

    ew = data["scores"][data["scores"]["eligible_universe_flag"] == 1][["signal_date", "PERMNO"]].copy()
    ew["PERMNO"] = ew["PERMNO"].astype(str)
    ew["weight"] = 1.0 / ew.groupby("signal_date")["PERMNO"].transform("count")
    ew_path, _ = mt.weekly_path_and_ledger(ew, returns, RUN_TO, cost)
    add("EW_UNIVERSE", BENCH_LABELS["EW_UNIVERSE"], ew_path, None)
    add("SP500", BENCH_LABELS["SP500"], bench_path(bench), None)

    summ = pd.DataFrame(summary)
    extra = pd.DataFrame.from_dict(checks, orient="index")
    summ = summ.merge(extra, left_on="variant", right_index=True, how="left")
    return {"summary": summ, "weekly": pd.concat(weekly_all, ignore_index=True),
            "yearly": pd.DataFrame(yearly), "holdings": holdings,
            "megacap": megacap_coverage(data["mcap"], scored["fmom"], holdings)}


TICKER_HINT = {"14593": "AAPL", "10107": "MSFT", "84788": "AMZN", "86580": "NVDA", "93436": "TSLA",
               "13407": "META", "90319": "GOOGL", "14542": "GOOG", "83443": "BRK.B"}   # common PERMNOs, hint only


def megacap_coverage(mcap: pd.DataFrame, scored: pd.DataFrame, holdings: dict, top: int = 10) -> pd.DataFrame:
    """At the last signal date of each year: the `top` largest eligible stocks (by mv), their share
    of the eligible universe's cap (~ S&P weight), their score rank, and our weights in them."""
    rows = []
    last = mcap.groupby(mcap["signal_date"].str[:4])["signal_date"].max()
    rank = scored[["signal_date", "PERMNO", "score_rank_pct"]]
    for year, d in last.items():
        m = mcap[(mcap["signal_date"] == d) & mcap["mv"].notna()]
        total = m["mv"].sum()
        big = m.nlargest(top, "mv").merge(rank, on=["signal_date", "PERMNO"], how="left")
        for i, r in enumerate(big.itertuples(index=False), 1):
            row = {"year": year, "signal_date": d, "mcap_rank": i, "PERMNO": r.PERMNO,
                   "ticker_hint": TICKER_HINT.get(r.PERMNO, ""), "mv_bn": r.mv / 1e6,
                   "universe_cap_weight": r.mv / total, "score_rank_pct": r.score_rank_pct}
            for name in ("FROZEN_BASE", "N100_EW_MV", "N100_SW_B0_MV"):
                if name in holdings:
                    h = holdings[name]
                    w = h.loc[(h["signal_date"] == d) & (h["PERMNO"] == r.PERMNO), "weight"]
                    row[f"w_{name}"] = float(w.sum())
            rows.append(row)
    return pd.DataFrame(rows)


def reproduce_frozen(root: Path, weekly: pd.DataFrame) -> list[str]:
    """FROZEN_BASE must match the frozen runner's weekly gross returns."""
    lines = []
    for period, folder in (("IS_2016_2020", "gate_b"), ("OOS_2021_2025", "hist_oos")):
        f = root / "outputs/strategy/frozen" / folder / "weekly_path.csv"
        if not f.exists():
            lines.append(f"{period}: {f.relative_to(root)} not found, reproduction not checked")
            continue
        ref = pd.read_csv(f, dtype={"signal_date": str}).set_index("signal_date")["gross_return"]
        mine = weekly[(weekly["variant"] == "FROZEN_BASE") & (weekly["period"] == period)]
        mine = mine.set_index("signal_date")["gross_return"]
        both = ref.index.intersection(mine.index)
        d = float((ref[both] - mine[both]).abs().max()) if len(both) else float("nan")
        ok = len(both) == len(ref) == len(mine) and d < 1e-10
        lines.append(f"{period}: FROZEN_BASE vs frozen/{folder}/weekly_path.csv  weeks {len(mine)}/{len(ref)}, "
                     f"max |gross diff| {d:.1e}  -> {'MATCH' if ok else 'DIFFERENT - check before using'}")
    return lines


# -------------------------------------------------------------- 6. report
_FAMILY = {"100": ("#08306b", "#2171b5", "#6baed6", "#9ecae1"),
           "50": ("#00441b", "#238b45", "#74c476", "#a1d99b"),
           "30": ("#67000d", "#cb181d", "#fb6a4a", "#fcae91")}
COLORS = {"FROZEN_BASE": "#111111"}
for _n, (c_ew, c0, c10, c20) in _FAMILY.items():
    for _s in ("", "_MV"):
        COLORS.update({f"N{_n}_EW{_s}": c_ew, f"N{_n}_SW_B0{_s}": c0, f"N{_n}_SW_B10{_s}": c10,
                       f"N{_n}_SW_B20{_s}": c20})
COLORS.update({"STATIC_LAM0": "#9467bd", "EW_UNIVERSE": "#8c8c8c", "SP500": "#ff7f0e"})
DASHED = {"STATIC_LAM0", "EW_UNIVERSE", "SP500"} | {f"N{n}_EW{x}" for n in _FAMILY for x in ("", "_MV")}
REFERENCE = ["FROZEN_BASE", "STATIC_LAM0", "EW_UNIVERSE", "SP500"]


def _svg_nav(weekly: pd.DataFrame, period: str, names: list[str]) -> str:
    w, h, l, r, t, b = 900, max(360, 14 * len(names) + 60), 50, 170, 15, 30
    d = weekly[(weekly["period"] == period) & weekly["variant"].isin(names)]
    dates = sorted(d["signal_date"].unique())
    if not dates:
        return ""
    x_of = {s: l + (w - l - r) * i / max(1, len(dates) - 1) for i, s in enumerate(dates)}
    lo, hi = min(0.95, d["nav"].min()), d["nav"].max() * 1.02
    y = lambda v: t + (h - t - b) * (1 - (v - lo) / (hi - lo))
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img">']
    for v in np.linspace(lo, hi, 5):
        out.append(f'<line x1="{l}" x2="{w - r}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#e5e5e5"/>'
                   f'<text x="{l - 6}" y="{y(v) + 4:.1f}" font-size="11" text-anchor="end" fill="#666">{v:.2f}</text>')
    for yr in sorted({s[:4] for s in dates}):
        first = next(s for s in dates if s[:4] == yr)
        out.append(f'<text x="{x_of[first]:.1f}" y="{h - 10}" font-size="11" fill="#666">{yr}</text>')
    labels = []
    for name in names:
        g = d[d["variant"] == name]
        if g.empty:
            continue
        pts = " ".join(f"{x_of[s]:.1f},{y(v):.1f}" for s, v in zip(g["signal_date"], g["nav"]))
        dash = ' stroke-dasharray="5 3"' if name in DASHED else ""
        width = 2.4 if name == "FROZEN_BASE" else 1.4
        out.append(f'<polyline points="{pts}" fill="none" stroke="{COLORS[name]}" stroke-width="{width}"{dash}/>')
        labels.append((y(g["nav"].iloc[-1]), name, g["nav"].iloc[-1]))
    labels.sort()
    ys, last_y = [], -99
    for yy, _, _ in labels:                         # end-of-line labels, pushed apart
        last_y = max(yy, last_y + 14)
        ys.append(last_y)
    if ys and ys[-1] > h - 8:                        # ran off the bottom: spread evenly instead
        ys = list(np.linspace(t + 4, h - 8, len(ys))) if len(ys) * 14 > h - t - 12 else \
            [y_ - (ys[-1] - (h - 8)) for y_ in ys]
    for yy, (_, name, v) in zip(ys, labels):
        out.append(f'<text x="{w - r + 6}" y="{yy + 4:.1f}" font-size="11" fill="{COLORS[name]}">{name} {v:.2f}</text>')
    out.append("</svg>")
    return "".join(out)


def _table(df: pd.DataFrame, cols: list[tuple[str, str, str]]) -> str:
    head = "".join(f"<th>{html.escape(t)}</th>" for _, t, _ in cols)
    body = []
    for _, row in df.iterrows():
        cells = []
        for c, _, fmt in cols:
            v = row.get(c)
            if isinstance(v, (float, np.floating)) and not np.isfinite(v):
                s = "—"
            elif v is None:
                s = "—"
            elif fmt == "pct":
                s = f"{v * 100:.1f}%"
            elif fmt == "num":
                s = f"{v:.2f}"
            elif fmt == "int":
                s = f"{v:.0f}"
            else:
                s = html.escape(str(v))
            cells.append(f"<td>{s}</td>")
        cls = ' class="base"' if row.get("variant") == "FROZEN_BASE" else ""
        body.append(f"<tr{cls}>{''.join(cells)}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def write_report(res: dict, out: Path, cost_bps: float, repro: list[str]) -> None:
    cols = [("variant", "代码", "txt"), ("label", "方案", "txt"), ("avg_names_held", "平均持股", "int"),
            ("ann_return", "年化净收益", "pct"), ("ann_vol", "年化波动", "pct"), ("sharpe", "夏普", "num"),
            ("sortino", "Sortino", "num"), ("max_drawdown", "最大回撤", "pct"), ("calmar", "Calmar", "num"),
            ("active_ann_return", "相对标普超额", "pct"), ("information_ratio", "IR", "num"),
            ("beta", "Beta", "num"), ("avg_weekly_turnover", "周换手", "pct"),
            ("cost_drag_ann", "年成本拖累", "pct"), ("stress_sharpe", "成本翻倍夏普", "num"),
            ("avg_max_name_weight", "最大单票权重", "pct"),
            ("avg_share_kept_by_band", "权重缓冲不调占比", "pct")]
    ycols = [c for c in res["yearly"].columns if c.isdigit()]
    parts = [f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>组合构建方案对比</title>
<style>body{{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;margin:24px;color:#222;background:#fff;max-width:1200px}}
table{{border-collapse:collapse;font-size:12.5px;margin:8px 0 24px;width:100%}}th,td{{border-bottom:1px solid #e3e3e3;padding:5px 7px;text-align:right;white-space:nowrap}}
th{{background:#f6f6f6}}td:nth-child(1),td:nth-child(2),th:nth-child(1),th:nth-child(2){{text-align:left}}
tr.base td{{font-weight:600;background:#fffbe6}}.warn{{background:#fff4f4;border-left:4px solid #d62728;padding:10px 14px;margin:12px 0}}
.note{{color:#555;font-size:13px}}h2{{margin-top:32px}}.wrap{{overflow-x:auto}}</style></head><body>
<h1>组合构建方案对比（探索性，ADAPT_VALIDATION）</h1>
<p class="note">生成时间 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · 单边成本 {cost_bps:g}bp（压力 {cost_bps * 2:g}bp）·
周度口径（52/年），不是 M5 的日度 NAV 正式口径 · 策略从 2016-01 连续运行后切成两段。</p>
<div class="warn"><b>注意：</b>冻结策略的 2021–2025 结果已经看过。如果根据下面 2021–2025 的表现挑方案，
那个方案的 2021–2025 数字就不再是样本外结果，只能作为 Step 40 的 ADAPT_VALIDATION 记录。
正式汇报的样本外结果仍然是 FROZEN_BASE（黄色行）。选方案应该只看 2016–2020。</div>
<p class="note">{'<br>'.join(html.escape(x) for x in repro)}</p>"""]
    for period, title in (("IS_2016_2020", "样本内 2016–2020（Gate B 期）"),
                          ("OOS_2021_2025", "样本外 2021–2025（历史 OOS）")):
        s = res["summary"][res["summary"]["period"] == period]
        yr = res["yearly"][res["yearly"]["period"] == period]
        yc = [c for c in ycols if yr[c].notna().any()]
        plain = [v[0] for v in VARIANTS if not v[5] and v[0] not in REFERENCE] + REFERENCE
        mvs = [v[0] for v in VARIANTS if v[5]] + REFERENCE
        parts.append(f"<h2>{title}</h2><h3>净值：不乘市值（扣成本，期初 = 1）</h3>{_svg_nav(res['weekly'], period, plain)}"
                     f"<h3>净值：乘市值（扣成本，期初 = 1）</h3>{_svg_nav(res['weekly'], period, mvs)}")
        parts.append(f'<h3>指标</h3><div class="wrap">{_table(s, cols)}</div>')
        parts.append('<h3>逐年净收益</h3><div class="wrap">'
                     + _table(yr, [("variant", "代码", "txt"), ("label", "方案", "txt")] + [(c, c, "pct") for c in yc])
                     + "</div>")
    mc = res.get("megacap")
    if mc is not None and len(mc):
        wcols = [c for c in mc.columns if c.startswith("w_")]
        g = mc.groupby("year")
        yr = pd.DataFrame({"variant": g.size().index, "label": g.size().index,
                           "n_held_frozen": g["w_FROZEN_BASE"].apply(lambda x: int((x > 0).sum())),
                           "universe_share": g["universe_cap_weight"].sum()})
        for c in wcols:
            yr[c] = g[c].sum()
        parts.append("<h2>前10大市值股票：我们持有多少</h2><p class='note'>每年最后一个信号日，合格股票里市值最大的 10 只。"
                     "“占全样本市值”约等于它们在标普 500 里的合计权重。</p>"
                     + _table(yr, [("variant", "年份", "txt"), ("n_held_frozen", "冻结策略持有几只", "int"),
                                   ("universe_share", "这10只占全样本市值", "pct")]
                              + [(c, f"{c[2:]} 中这10只合计权重", "pct") for c in wcols]))
        last_year = mc["year"].max()
        det = mc[mc["year"] == last_year].assign(variant=lambda x: x["PERMNO"], label=lambda x: x["ticker_hint"])
        parts.append(f"<h3>{last_year} 年末明细</h3>"
                     + _table(det, [("variant", "PERMNO", "txt"), ("label", "代码(参考)", "txt"),
                                    ("mv_bn", "市值(十亿美元)", "num"), ("universe_cap_weight", "占全样本市值", "pct"),
                                    ("score_rank_pct", "综合分排名百分位(越小越好)", "pct")]
                              + [(c, c[2:], "pct") for c in wcols]))
    parts.append("""<h2>口径说明</h2><ul class="note">
<li>所有方案共用冻结的因子分数（K=4、J=26、风险调整 FMOM、λ=0.5、读法 B、4 因子齐全）、Buffer、30% 行业上限、每周调仓和成本口径，只改持股数量和权重。</li>
<li>固定 N 只：每周按综合分排名取前 N 只（行业上限 = floor(0.3N) 只）；Buffer = 已持有的股票排名在 1.5N 以内就保留。</li>
<li>按分数加权（每周滚动）：每周用本周 S_t 的综合分，在本周选中的 N 只里算 w = max(分数, 0.01) / 合计，再把超过 30% 的行业按比例压回，多出的权重按比例分给其他行业。</li>
<li>权重缓冲（B10 / B20）：上周已持有的股票，如果新权重相对上周目标权重的变化 |w新 − w上周| / w上周 不超过 10% / 20%，就沿用上周目标权重；其余股票按新分数比例分剩下的权重，再检查 30% 行业上限。只用 S_t 时点已知的信息（本周分数和上周目标），没有前视偏差。B0 = 不加权重缓冲。</li>
<li>乘市值（_MV）：在上面的权重上再乘以信号日 S_t 的市值（同一公司多个股票类别时平分公司市值），再归一化并检查 30% 行业上限。等权 × 市值 = 选中股票内纯市值加权；分数 × 市值 = 分数和市值相乘。只用 S_t 已知的市值，没有前视偏差。</li>
<li>STATIC_LAM0：与冻结策略完全相同，只是因子权重固定为 1/4（没有 FMOM 择时），用来看择时本身的贡献。</li>
<li>EW_UNIVERSE：所有合格股票等权，同样每周调仓、同样成本。SP500：标普 500 全收益，不扣成本。</li>
<li>夏普 / Sortino 用 FF 无风险利率计算超额收益；最大回撤为正数；相对标普超额 = 周度超额均值 × 52。</li></ul></body></html>""")
    (out / "report.html").write_text("".join(parts), encoding="utf-8")


# ---------------------------------------------------------------- main
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cost-bps", type=float, default=None,
                        help="one-way cost in bp (default: the frozen json value)")
    args = parser.parse_args()
    t0 = time.time()
    root = gv.project_root()
    print("loading inputs 2016-2025 and recomputing factor returns / FMOM weights ...")
    data = load_all(root)
    cost_bps = args.cost_bps if args.cost_bps is not None else data["cfg"]["transaction_cost"]["oneway_bps"]
    print(f"inputs ready ({time.time() - t0:.0f}s); cost {cost_bps:g} bp one-way; building variants:")
    res = run_all(data, cost_bps / 1e4)

    out = root / OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    res["summary"].assign(cost_bps=cost_bps, status="ADAPT_VALIDATION").to_csv(out / "variant_summary.csv", index=False)
    res["yearly"].to_csv(out / "variant_yearly.csv", index=False)
    res["weekly"].to_csv(out / "variant_weekly.csv", index=False)
    res["megacap"].to_csv(out / "megacap_coverage.csv", index=False)
    for name, hold in res["holdings"].items():
        hold.to_csv(out / f"holdings_{name}.csv", index=False)
    repro = reproduce_frozen(root, res["weekly"])
    write_report(res, out, cost_bps, repro)

    pd.set_option("display.width", 220)
    show = ["variant", "avg_names_held", "ann_return", "ann_vol", "sharpe", "max_drawdown",
            "active_ann_return", "avg_weekly_turnover", "stress_sharpe", "checks"]
    for period in PERIODS:
        print(f"\n=== {period} (weekly, net of {cost_bps:g} bp) ===")
        print(res["summary"][res["summary"]["period"] == period][show].round(4).to_string(index=False))
    print("\n" + "\n".join(repro))
    print(f"\ndone in {time.time() - t0:.0f}s -> {out.relative_to(root)}  (open report.html in a browser)")
    print("STATUS: ADAPT_VALIDATION - the frozen strategy is unchanged; 2021-2025 is not a clean OOS "
          "test for any variant picked after seeing it.")


if __name__ == "__main__":
    main()
