"""Shared strategy metric functions (SSOT 4.4 / Step 46 conventions, weekly frequency).

SSOT requires one shared set of metric functions; M5 should review / reuse these.

Accounting (SSOT 4.4, exact NAV order, not the additive shortcut):
  - pre-trade weights are last week's target weights drifted by realized returns;
    the first week starts from 100% cash;
  - GTR_t = sum over securities |w_target - w_pretrade| (cash excluded), one-way turnover = GTR / 2;
    so the initial build from cash has GTR = 1 (100% of NAV bought once);
  - TC_t = c_oneway * GTR_t;  1 + R_net = (1 - TC_t)(1 + R_hold);
  - a holding without a usable return earns 0 that week (reported as unpriced weight).
Break-even one-way cost solves prod((1 - c*GTR_t)(1 + R_hold_t)) = 1, the same formula
as the Gate A break-even diagnostic.

All metrics here are WEEKLY (52 periods / year).  The authoritative final numbers are
daily-NAV based (SSOT 4.4 Frequency Rule); weekly versions are for Gate B comparison.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

WEEKS_PER_YEAR = 52
CASH = "__CASH__"


# ------------------------------------------------------------ 1. NAV path
def weekly_path(hold: pd.DataFrame, returns: pd.DataFrame, end: str) -> pd.DataFrame:
    """Gross holding return and GTR per rebalance week.

    hold:    signal_date, PERMNO, weight          (target holdings)
    returns: signal_date, PERMNO, next_execution_date,
             execution_to_next_execution_total_return, usable
    Weeks whose holding period ends after `end` are dropped.
    """
    m = hold.merge(returns, on=["signal_date", "PERMNO"], how="left")
    m["usable"] = m["usable"].fillna(0).astype(int)
    m["r"] = np.where(m["usable"] == 1, m["execution_to_next_execution_total_return"], 0.0)

    rows, drifted = [], {CASH: 1.0}
    for signal_date, week in m.groupby("signal_date", sort=True):
        period_end = week["next_execution_date"].dropna()
        period_end = str(period_end.max()) if len(period_end) else ""
        if not period_end or period_end > end:
            break
        target = dict(zip(week["PERMNO"], week["weight"]))
        cash_w = 1.0 - sum(target.values())
        if cash_w > 1e-12:
            target[CASH] = cash_w
        stocks = (set(target) | set(drifted)) - {CASH}      # SSOT: GTR sums over securities only
        gtr = sum(abs(target.get(k, 0.0) - drifted.get(k, 0.0)) for k in stocks)
        r = dict(zip(week["PERMNO"], week["r"]))
        gross = sum(w * r.get(k, 0.0) for k, w in target.items())          # cash earns 0
        value = {k: w * (1.0 + r.get(k, 0.0)) for k, w in target.items()}
        total = sum(value.values())
        drifted = {k: v / total for k, v in value.items()} if total > 0 else {CASH: 1.0}
        rows.append({"signal_date": signal_date, "period_end": period_end, "gross_return": gross,
                     "gtr": gtr, "turnover": 0.5 * gtr,
                     "unpriced_weight": float(week.loc[week["usable"] == 0, "weight"].sum())})
    return pd.DataFrame(rows)


def net_returns(path: pd.DataFrame, cost_oneway: float) -> pd.Series:
    """Exact net return: (1 - c*GTR)(1 + R_hold) - 1."""
    return (1.0 - cost_oneway * path["gtr"]) * (1.0 + path["gross_return"]) - 1.0


def break_even_cost(path: pd.DataFrame) -> float:
    """One-way cost at which ending NAV = 1 (NaN if gross NAV <= 1)."""
    gross, gtr = path["gross_return"].to_numpy(), path["gtr"].to_numpy()
    if np.prod(1.0 + gross) <= 1.0 or gtr.max() == 0:
        return float("nan")
    lo, hi = 0.0, 0.999999 / gtr[gtr > 0].max()
    for _ in range(100):
        mid = (lo + hi) / 2.0
        if np.prod((1.0 - mid * gtr) * (1.0 + gross)) > 1.0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# ------------------------------------------------------ 2. return metrics
def drawdown_series(r: pd.Series) -> pd.Series:
    nav = (1.0 + r).cumprod()
    return nav / nav.cummax() - 1.0                     # <= 0


def drawdown_episodes(dd: pd.Series) -> tuple[int, int]:
    """(number of separate drawdown episodes, longest episode in weeks)."""
    under = (dd < -1e-12).to_numpy()
    starts = np.flatnonzero(under & ~np.r_[False, under[:-1]])
    longest, run = 0, 0
    for u in under:
        run = run + 1 if u else 0
        longest = max(longest, run)
    return int(len(starts)), int(longest)


def performance(r: pd.Series, rf: pd.Series | None = None, bench: pd.Series | None = None) -> dict:
    """Weekly-frequency performance profile. r, rf, bench aligned on the same index."""
    r = r.astype(float)
    n = len(r)
    if n < 2:
        return {"n_weeks": n}
    rf = pd.Series(0.0, index=r.index) if rf is None else rf.reindex(r.index).fillna(0.0)
    ex = r - rf
    nav_end = float((1.0 + r).prod())
    ann_ret = nav_end ** (WEEKS_PER_YEAR / n) - 1.0
    ann_vol = float(r.std(ddof=1) * math.sqrt(WEEKS_PER_YEAR))
    ex_sd = float(ex.std(ddof=1))
    downside = float(np.sqrt(np.mean(np.minimum(ex, 0.0) ** 2)))
    dd = drawdown_series(r)
    mdd = float(-dd.min())
    episodes, longest = drawdown_episodes(dd)
    out = {
        "n_weeks": n,
        "ann_return": ann_ret,
        "ann_vol": ann_vol,
        "sharpe": float(ex.mean() / ex_sd * math.sqrt(WEEKS_PER_YEAR)) if ex_sd > 0 else np.nan,
        "sortino": float(ex.mean() / downside * math.sqrt(WEEKS_PER_YEAR)) if downside > 0 else np.nan,
        "max_drawdown": mdd,                                         # positive, SSOT convention
        "calmar": ann_ret / mdd if mdd > 0 else np.nan,
        "drawdown_episodes": episodes,
        "longest_drawdown_weeks": longest,
        "period_hit_rate": float((r > 0).mean()),                   # share of positive weeks
        "worst_week": float(r.min()),
        "best_week": float(r.max()),
    }
    if bench is not None:
        b = bench.reindex(r.index)
        ok = b.notna()
        active = (r - b)[ok]
        te = float(active.std(ddof=1) * math.sqrt(WEEKS_PER_YEAR)) if ok.sum() > 1 else np.nan
        cov = np.cov(r[ok], b[ok], ddof=1)
        out.update({
            "bench_ann_return": float((1.0 + b[ok]).prod() ** (WEEKS_PER_YEAR / ok.sum()) - 1.0),
            "active_ann_return": float(active.mean() * WEEKS_PER_YEAR),
            "tracking_error": te,
            "information_ratio": float(active.mean() * WEEKS_PER_YEAR / te) if te and te > 0 else np.nan,
            "beta": float(cov[0, 1] / cov[1, 1]) if cov[1, 1] > 0 else np.nan,
        })
    return out


def yearly_returns(r: pd.Series, dates: pd.Series) -> pd.Series:
    """Compounded return per calendar year of the signal date."""
    years = dates.str[:4].to_numpy()
    return (1.0 + r).groupby(years).prod() - 1.0


# ------------------------------------------------------- 3. trade metrics
def trade_stats(hold: pd.DataFrame, returns: pd.DataFrame, end: str, cost_oneway: float) -> dict:
    """Round-trip trades: a stock entering the book and later leaving it is one trade.

    Holding period = consecutive rebalance weeks held.  Trade return = compounded weekly
    returns while held, net of one entry and one exit cost: (1-c)^2 * prod(1+r) - 1.
    Re-weighting a name that stays in the book is not counted as a new trade.
    Trades still open at the window end are closed there.
    """
    m = hold.merge(returns, on=["signal_date", "PERMNO"], how="left")
    m = m[m["next_execution_date"].fillna("9999") <= end]
    m["r"] = np.where(m["usable"].fillna(0) == 1, m["execution_to_next_execution_total_return"], 0.0)
    weeks = sorted(m["signal_date"].unique())
    if not weeks:
        return {"n_round_trips": 0}
    pos = {d: i for i, d in enumerate(weeks)}
    m["wk"] = m["signal_date"].map(pos)
    m = m.sort_values(["PERMNO", "wk"])
    m["new_trade"] = m.groupby("PERMNO")["wk"].diff().ne(1)
    m["trade_id"] = m["new_trade"].cumsum()
    t = m.groupby("trade_id").agg(weeks=("wk", "size"), growth=("r", lambda x: float(np.prod(1.0 + x))),
                                  last_wk=("wk", "max"))
    t["net_return"] = (1.0 - cost_oneway) ** 2 * t["growth"] - 1.0
    closed = t["last_wk"] < len(weeks) - 1
    return {
        "n_round_trips": int(len(t)),
        "n_trades_buy_plus_sell": int(len(t) + closed.sum()),        # every entry + every exit
        "trades_per_year": float(len(t) / (len(weeks) / WEEKS_PER_YEAR)),
        "avg_holding_weeks": float(t["weeks"].mean()),
        "median_holding_weeks": float(t["weeks"].median()),
        "trade_win_rate": float((t["net_return"] > 0).mean()),
        "avg_profit_per_trade": float(t["net_return"].mean()),       # % of the position
        "median_profit_per_trade": float(t["net_return"].median()),
    }
